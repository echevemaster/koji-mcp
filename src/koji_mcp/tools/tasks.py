"""Task and task log tools."""

from __future__ import annotations

import json
from typing import Annotated, Any

import koji
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from koji_mcp.client import KojiClient
from koji_mcp.formatting import normalize, render, tail_text
from koji_mcp.tools.common import READ_ONLY, Format

MAX_LOG_BYTES = 256 * 1024
TASK_FIELDS = [
    "id",
    "method",
    "state",
    "arch",
    "label",
    "owner",
    "parent",
    "host_id",
    "create_time",
    "start_time",
    "completion_time",
    "request",
    "url",
]
CHILD_COLUMNS = ["id", "method", "arch", "state", "label"]

TaskId = Annotated[int, Field(ge=1, description="Numeric Koji task ID.")]
TailLines = Annotated[int, Field(ge=1, le=2000, description="How many lines from the end of the log to return.")]


def task_state_name(state: Any) -> Any:
    try:
        return koji.TASK_STATES[state]
    except (KeyError, IndexError, TypeError):
        return state


def log_url(client: KojiClient, task_id: int, file_name: str) -> str | None:
    if not client.topurl:
        return None
    return f"{koji.PathInfo(topdir=client.topurl).task(task_id)}/{file_name}"


async def list_outputs(client: KojiClient, task_id: int) -> dict[str, int]:
    """Output file name -> size in bytes."""
    stats = await client.call("listTaskOutput", task_id, stat=True)
    return {name: int(info.get("st_size") or 0) for name, info in stats.items()}


async def fetch_log_tail(
    client: KojiClient,
    task_id: int,
    file_name: str,
    tail_lines: int,
    outputs: dict[str, int] | None = None,
) -> dict:
    """Download the end of a task output file, at most MAX_LOG_BYTES."""
    if outputs is None:
        outputs = await list_outputs(client, task_id)
    if file_name not in outputs:
        available = ", ".join(sorted(outputs)) or "none"
        raise ToolError(f"Task {task_id} has no output file {file_name!r}. Available files: {available}.")
    size = outputs[file_name]
    offset = max(0, size - MAX_LOG_BYTES)
    data = await client.call("downloadTaskOutput", task_id, file_name, offset=offset, size=size - offset)
    text = data.decode("utf-8", errors="replace")
    if offset > 0:
        text = text.partition("\n")[2]  # drop the partial first line
    content, truncated = tail_text(text.rstrip("\n"), tail_lines)
    return {
        "task_id": task_id,
        "file": file_name,
        "size": size,
        "truncated": truncated or offset > 0,
        "lines_shown": len(content.splitlines()),
        "content": content,
        "url": log_url(client, task_id, file_name),
    }


def render_log(log: dict, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(log, indent=2)
    scope = f"last {log['lines_shown']} lines" if log["truncated"] else "complete"
    lines = [f"## {log['file']} of task {log['task_id']} ({scope}, {log['size']} bytes)"]
    if log["url"]:
        lines.append(f"Full log: {log['url']}")
    lines += ["", "```", log["content"], "```"]
    return "\n".join(lines)


def register(mcp: MCPServer, client: KojiClient) -> None:
    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def koji_get_task(task_id: TaskId, response_format: Format = "markdown") -> str:
        """Get a task (state, method, arch, request, timestamps) and its child tasks.

        A build task has one buildArch child per arch; use koji_task_logs on a
        failed child to read its build.log or root.log.
        """
        info, children = await client.multicall(
            [
                ("getTaskInfo", (task_id,), {"request": True, "strict": False}),
                ("getTaskChildren", (task_id,), {}),
            ]
        )
        if not info:
            raise ToolError(f"Task {task_id} not found. Task IDs come from koji_get_build (task_id).")
        info = normalize(info)
        info["state"] = task_state_name(info.get("state"))
        children = normalize(children)
        for child in children:
            child["state"] = task_state_name(child.get("state"))
        if client.weburl:
            info["url"] = f"{client.weburl}/taskinfo?taskID={task_id}"
        if response_format == "json":
            return render(dict(info, children=children), "json")
        summary = {key: info.get(key) for key in TASK_FIELDS if key in info}
        if "request" in summary:
            summary["request"] = json.dumps(summary["request"], default=str)[:500]
        text = render(summary, "markdown", title=f"Task {task_id} ({info.get('method')})")
        return text + "\n\n" + render(children, "markdown", title="Child tasks", columns=CHILD_COLUMNS)

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def koji_task_logs(
        task_id: TaskId,
        file_name: Annotated[
            str | None,
            Field(description="Output file to read, e.g. 'build.log' or 'root.log'. Omit to list files."),
        ] = None,
        tail_lines: TailLines = 100,
        response_format: Format = "markdown",
    ) -> str:
        """List a task's output files, or read the tail of one log.

        Build failures usually show in build.log (compile/test errors) or
        root.log (missing BuildRequires). Logs are always truncated to the last
        tail_lines lines; the full log URL is included.
        """
        outputs = await list_outputs(client, task_id)
        if file_name is None:
            if not outputs:
                return (
                    f"Task {task_id} has no output files. Old task outputs are garbage-collected "
                    "by the hub, or the task produced none (e.g. a parent build task)."
                )
            rows = [{"file": name, "size": size, "url": log_url(client, task_id, name)} for name, size in outputs.items()]
            return render(rows, response_format, title=f"Output of task {task_id}", columns=["file", "size"])
        log = await fetch_log_tail(client, task_id, file_name, tail_lines, outputs)
        return render_log(log, response_format)
