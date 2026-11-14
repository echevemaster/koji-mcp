import json

import pytest
from conftest import call_text
from mcp.server.mcpserver.exceptions import ToolError

TASK = {
    "id": 100,
    "method": "build",
    "state": 5,
    "arch": "noarch",
    "label": None,
    "owner": 7,
    "parent": None,
    "create_ts": 0.0,
    "create_time": "1970-01-01 00:00:00+00:00",
    "request": ["git+https://src.example.org/rpms/bash.git#abc", "f42-candidate", {}],
}
CHILDREN = [
    {"id": 101, "method": "buildArch", "state": 5, "arch": "x86_64", "label": "x86_64"},
    {"id": 102, "method": "buildSRPMFromSCM", "state": 2, "arch": "noarch", "label": "srpm"},
]
LOG = "\n".join(f"line {i}" for i in range(1, 501))


def _output(task_id, stat=False):
    return {"build.log": {"st_size": str(len(LOG.encode()))}, "root.log": {"st_size": "10"}}


def _download(task_id, file_name, offset=0, size=-1):
    data = LOG.encode()
    return data[offset : offset + size] if size >= 0 else data[offset:]


async def test_get_task_shows_state_children_and_link(make_server):
    server, session = make_server(
        getTaskInfo=lambda tid, request=False, strict=False: dict(TASK),
        getTaskChildren=lambda tid, request=False, strict=False: [dict(c) for c in CHILDREN],
    )
    text = await call_text(server, "koji_get_task", task_id=100)
    assert ("getTaskInfo", (100,), {"request": True, "strict": False}) in session.calls
    assert ("getTaskChildren", (100,), {}) in session.calls
    assert "- **state**: FAILED" in text
    assert "https://koji.example.org/koji/taskinfo?taskID=100" in text
    assert "| 101 | buildArch | x86_64 | FAILED | x86_64 |" in text
    assert "| 102 | buildSRPMFromSCM | noarch | CLOSED | srpm |" in text


async def test_get_task_json_nests_children(make_server):
    server, _ = make_server(
        getTaskInfo=lambda tid, request=False, strict=False: dict(TASK),
        getTaskChildren=lambda tid, request=False, strict=False: [dict(c) for c in CHILDREN],
    )
    data = json.loads(await call_text(server, "koji_get_task", task_id=100, response_format="json"))
    assert data["state"] == "FAILED"
    assert data["create_time"] == "1970-01-01T00:00:00+00:00"
    assert [c["state"] for c in data["children"]] == ["FAILED", "CLOSED"]


async def test_get_task_not_found(make_server):
    server, _ = make_server(
        getTaskInfo=lambda tid, request=False, strict=False: None,
        getTaskChildren=lambda tid, request=False, strict=False: [],
    )
    with pytest.raises(ToolError, match="Task 999 not found"):
        await server.call_tool("koji_get_task", {"task_id": 999})


async def test_task_logs_without_file_lists_outputs(make_server):
    server, session = make_server(listTaskOutput=_output)
    text = await call_text(server, "koji_task_logs", task_id=101)
    assert session.calls == [("listTaskOutput", (101,), {"stat": True})]
    assert f"| build.log | {len(LOG.encode())} |" in text
    assert "| root.log | 10 |" in text


async def test_task_logs_returns_only_the_tail(make_server):
    server, session = make_server(listTaskOutput=_output, downloadTaskOutput=_download)
    text = await call_text(server, "koji_task_logs", task_id=101, file_name="build.log", tail_lines=3)
    assert "```\nline 498\nline 499\nline 500\n```" in text
    assert "line 497" not in text
    assert "last 3 lines" in text
    assert "https://kojipkgs.example.org/work/tasks/101/101/build.log" in text


async def test_task_logs_downloads_at_most_the_byte_cap_from_the_end(make_server, monkeypatch):
    from koji_mcp.tools import tasks

    monkeypatch.setattr(tasks, "MAX_LOG_BYTES", 100)
    server, session = make_server(listTaskOutput=_output, downloadTaskOutput=_download)
    data = json.loads(
        await call_text(
            server, "koji_task_logs", task_id=101, file_name="build.log", tail_lines=1000, response_format="json"
        )
    )
    name, args, kwargs = session.calls[-1]
    assert name == "downloadTaskOutput"
    assert kwargs == {"offset": len(LOG.encode()) - 100, "size": 100}
    assert data["truncated"] is True
    assert data["content"].endswith("line 500")
    # the partial first line of the chunk is dropped
    assert all(line.startswith("line ") for line in data["content"].splitlines())
    assert data["size"] == len(LOG.encode())


async def test_task_logs_unknown_file_lists_available(make_server):
    server, _ = make_server(listTaskOutput=_output, downloadTaskOutput=_download)
    with pytest.raises(ToolError, match="build.log, root.log"):
        await server.call_tool("koji_task_logs", {"task_id": 101, "file_name": "nope.log"})


async def test_task_logs_no_output_explains_garbage_collection(make_server):
    server, _ = make_server(listTaskOutput=lambda tid, stat=False: {})
    text = await call_text(server, "koji_task_logs", task_id=101)
    assert "no output files" in text.lower()
