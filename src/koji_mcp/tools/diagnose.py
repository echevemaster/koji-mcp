"""Workflow tool: explain why a build failed."""

from __future__ import annotations

import json
import re
from typing import Annotated, Any

import koji
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from koji_mcp.client import KojiClient
from koji_mcp.tools.common import READ_ONLY, Format, build_state_name, resolve_build
from koji_mcp.tools.tasks import download_end, list_outputs, log_record, task_state_name

LOGS = ("build.log", "root.log")
MAX_KEY_LINES = 20
# Whole words only: "redhat-hardened-ld-errors" or "libgpg-error-1.58" are not errors.
KEY_LINE = re.compile(
    r"(?<![\w-])(?:errors?|failed|fail:|fatal|undefined reference|no match for argument"
    r"|nothing provides|no matching package|problem:|not found|unpackaged)(?![\w-])",
    re.IGNORECASE,
)
DEPENDENCY_FAILURE = re.compile(
    r"No match for argument|nothing provides|No matching package to install|Failed to resolve the transaction"
)
BAD_EXIT = re.compile(r"Bad exit status from \S+ \(%(\w+)\)")


def key_lines(text: str) -> list[str]:
    """The last lines that look like errors, without duplicates."""
    seen: dict[str, None] = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("+ ") and KEY_LINE.search(line):  # "+ " is shell trace
            seen.pop(line, None)
            seen[line] = None
    return list(seen)[-MAX_KEY_LINES:]


def likely_cause(texts: dict[str, str]) -> str:
    build_log, root_log = texts.get("build.log", ""), texts.get("root.log", "")
    if DEPENDENCY_FAILURE.search(root_log):
        return "unresolvable BuildRequires: the buildroot dependencies could not be installed (see root.log)"
    if "Installed (but unpackaged) file(s) found" in build_log:
        return "files were installed but not listed in %files"
    if "File not found:" in build_log:
        return "a file listed in %files was not installed"
    match = BAD_EXIT.search(build_log)
    if match:
        section = match.group(1)
        if section == "check":
            return "the %check section failed (tests)"
        return f"the %{section} section failed"
    return "unknown: read the key lines and the log tails"


async def failure_message(client: KojiClient, task_id: int) -> str | None:
    """getTaskResult raises the task's own error for a failed task."""
    try:
        await client.call("getTaskResult", task_id)
    except ToolError as exc:
        return str(exc.__cause__ or exc)
    return None


def render_markdown(report: dict) -> str:
    lines = [f"## Diagnosis: {report['title']} (task {report['task_id']})", ""]
    failed = report["failed_task"]
    lines.append(f"- **Failed task**: {failed['id']} {failed['method']} {failed['arch']}")
    if report["other_failed_tasks"]:
        others = ", ".join(f"{t['id']} {t['method']} {t['arch']}" for t in report["other_failed_tasks"])
        lines.append(f"- **Other failed tasks**: {others}")
    if report["error"]:
        lines.append(f"- **Koji error**: {report['error']}")
    lines.append(f"- **Likely cause**: {report['likely_cause']}")
    if not report["logs"]:
        lines += ["", "No logs: old task outputs are garbage-collected by the hub."]
        return "\n".join(lines)
    for name, log in report["logs"].items():
        if log["key_lines"]:
            lines += ["", f"### Key lines ({name})", "```", *log["key_lines"], "```"]
    build_log = report["logs"].get("build.log")
    if build_log:
        lines += ["", f"### Tail of build.log (last {build_log['lines_shown']} lines)", "```", build_log["content"], "```"]
    urls = [log["url"] for log in report["logs"].values() if log["url"]]
    if urls:
        lines += ["", "Full logs: " + " ".join(urls)]
    return "\n".join(lines)


def register(mcp: MCPServer, client: KojiClient) -> None:
    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def koji_diagnose_failed_build(
        build: Annotated[str | None, Field(description="Failed build NVR or numeric build ID.")] = None,
        task_id: Annotated[int | None, Field(ge=1, description="Failed task ID (a build or buildArch task).")] = None,
        tail_lines: Annotated[int, Field(ge=5, le=500, description="Lines of build.log tail to include.")] = 40,
        response_format: Format = "markdown",
    ) -> str:
        """Explain why a build failed, in one call.

        Finds the first failed subtask, reads Koji's error, scans build.log and
        root.log for error lines, guesses the likely cause (missing
        BuildRequires, failing %build/%check, unpackaged files) and returns the
        build.log tail. Give exactly one of build or task_id.
        """
        if (build is None) == (task_id is None):
            raise ToolError("Give exactly one of build or task_id.")
        title = f"task {task_id}"
        if build is not None:
            info = await resolve_build(client, build)
            title = info.get("nvr") or build
            state = build_state_name(info.get("state"))
            if state not in ("FAILED", "CANCELED"):
                return f"Build {title} is {state}: nothing to diagnose."
            task_id = info.get("task_id")
            if not task_id:
                raise ToolError(f"Build {title} has no task (it was imported), so there are no build logs.")

        parent, children = await client.multicall(
            [
                ("getTaskInfo", (task_id,), {"request": True, "strict": False}),
                ("getTaskChildren", (task_id,), {}),
            ]
        )
        if not parent:
            raise ToolError(f"Task {task_id} not found.")
        failed = [c for c in children if c.get("state") == koji.TASK_STATES["FAILED"]] or [parent]
        target, others = failed[0], failed[1:]

        error = await failure_message(client, target["id"])
        outputs = await list_outputs(client, target["id"])
        texts: dict[str, str] = {}
        logs: dict[str, Any] = {}
        for name in LOGS:
            if name not in outputs:
                continue
            text, cut = await download_end(client, target["id"], name, outputs)
            texts[name] = text
            record = log_record(client, target["id"], name, outputs[name], text, cut, tail_lines)
            record["key_lines"] = key_lines(text)
            logs[name] = record

        def brief(task: dict) -> dict:
            return {
                "id": task["id"],
                "method": task.get("method"),
                "arch": task.get("arch"),
                "state": task_state_name(task.get("state")),
            }

        report = {
            "title": title,
            "task_id": task_id,
            "failed_task": brief(target),
            "other_failed_tasks": [brief(t) for t in others],
            "error": error,
            "likely_cause": likely_cause(texts) if texts else "unknown: logs are not available",
            "logs": logs,
        }
        if response_format == "json":
            return json.dumps(report, indent=2)
        return render_markdown(report)
