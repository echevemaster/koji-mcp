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
    "owner_name",
    "parent",
    "host_id",
    "host_name",
    "create_time",
    "start_time",
    "completion_time",
    "request",
    "url",
]
CHILD_COLUMNS = ["id", "method", "arch", "state", "label", "host_id", "host_name"]

TaskId = Annotated[int, Field(ge=1, description="Numeric Koji task ID.")]
TailLines = Annotated[int, Field(ge=1, le=2000, description="How many lines from the end of the log to return.")]


def task_state_name(state: Any) -> Any:
    try:
        return koji.TASK_STATES[state]
    except (KeyError, IndexError, TypeError):
        return state


async def resolve_names(client: KojiClient, info: dict, children: list[dict]) -> None:
    """Add owner_name and host_name next to the numeric IDs, in one round-trip.

    Best effort: if the lookup fails, the task is still shown with its IDs.
    """
    owner = info.get("owner")
    host_ids = sorted({t["host_id"] for t in [info, *children] if t.get("host_id")})
    calls = [("getHost", (host_id,), {}) for host_id in host_ids]
    if owner:
        calls.append(("getUser", (owner,), {}))
    if not calls:
        return
    try:
        results = await client.multicall(calls)
    except ToolError:
        return
    hosts = {host_id: (host or {}).get("name") for host_id, host in zip(host_ids, results)}
    if owner:
        info["owner_name"] = (results[-1] or {}).get("name")
    for task in [info, *children]:
        if task.get("host_id"):
            task["host_name"] = hosts.get(task["host_id"])


def log_url(client: KojiClient, task_id: int, file_name: str) -> str | None:
    if not client.topurl:
        return None
    return f"{koji.PathInfo(topdir=client.topurl).task(task_id)}/{file_name}"


async def list_outputs(client: KojiClient, task_id: int) -> dict[str, int]:
    """Output file name -> size in bytes."""
    stats = await client.call("listTaskOutput", task_id, stat=True)
    return {name: int(info.get("st_size") or 0) for name, info in stats.items()}


async def download_end(
    client: KojiClient, task_id: int, file_name: str, outputs: dict[str, int]
) -> tuple[str, bool]:
    """The last MAX_LOG_BYTES of an output file, starting at a whole line.

    Returns the text and whether the start of the file was cut off.
    """
    if file_name not in outputs:
        available = ", ".join(sorted(outputs)) or "none"
        raise ToolError(f"Task {task_id} has no output file {file_name!r}. Available files: {available}.")
    size = outputs[file_name]
    offset = max(0, size - MAX_LOG_BYTES)
    data = await client.call("downloadTaskOutput", task_id, file_name, offset=offset, size=size - offset)
    text = data.decode("utf-8", errors="replace")
    if offset > 0:
        text = text.partition("\n")[2]  # drop the partial first line
    return text.rstrip("\n"), offset > 0


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
    text, cut = await download_end(client, task_id, file_name, outputs)
    return log_record(client, task_id, file_name, outputs[file_name], text, cut, tail_lines)


def log_record(
    client: KojiClient, task_id: int, file_name: str, size: int, text: str, cut: bool, tail_lines: int
) -> dict:
    content, truncated = tail_text(text, tail_lines)
    return {
        "task_id": task_id,
        "file": file_name,
        "size": size,
        "truncated": truncated or cut,
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
        failed child to read its build.log or root.log. Owner and host IDs come
        with their names. For a child, label is what it builds and arch is the
        Koji arch it was scheduled for: a "noarch" label runs on an x86_64
        builder, and the i686 label has arch i386.
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
        await resolve_names(client, info, children)
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
