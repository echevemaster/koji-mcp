import json

import pytest
from conftest import call_text
from mcp.server.mcpserver.exceptions import ToolError


def _builds(n):
    return [{"nvr": f"pkg{i}-1-1", "package_name": f"pkg{i}", "tag_name": "f41", "owner_name": "o"} for i in range(n)]


async def test_latest_builds_paginates_client_side(make_server):
    server, session = make_server(getLatestBuilds=lambda tag, package=None: _builds(5))
    data = json.loads(
        await call_text(server, "koji_latest_builds", tag="f41", limit=2, offset=2, response_format="json")
    )
    assert session.calls == [("getLatestBuilds", ("f41",), {"package": None})]
    assert [b["nvr"] for b in data["items"]] == ["pkg2-1-1", "pkg3-1-1"]
    assert data["has_more"] is True
    assert data["next_offset"] == 4


async def test_latest_builds_for_one_package(make_server):
    server, session = make_server(getLatestBuilds=lambda tag, package=None: _builds(1))
    text = await call_text(server, "koji_latest_builds", tag="f41-updates", package="pkg0")
    assert session.calls[0] == ("getLatestBuilds", ("f41-updates",), {"package": "pkg0"})
    assert "| pkg0-1-1 |" in text


async def test_list_tagged_passes_flags(make_server):
    server, session = make_server(listTagged=lambda tag, **kw: _builds(1))
    await call_text(server, "koji_list_tagged", tag="f41", package="bash", inherit=True, latest=True)
    assert session.calls[0] == (
        "listTagged",
        ("f41",),
        {"package": "bash", "owner": None, "inherit": True, "latest": True},
    )


async def test_get_tag_not_found(make_server):
    server, _ = make_server(getTag=lambda tag, strict=False: None)
    with pytest.raises(ToolError, match="Tag 'f99' not found"):
        await server.call_tool("koji_get_tag", {"tag": "f99"})


async def test_get_tag_shows_fields(make_server):
    server, session = make_server(getTag=lambda tag, strict=False: {"id": 7, "name": "f41", "arches": "x86_64"})
    text = await call_text(server, "koji_get_tag", tag="7")
    assert session.calls[0] == ("getTag", (7,), {"strict": False})
    assert "- **name**: f41" in text


async def test_tag_inheritance_table(make_server):
    parents = [{"name": "f41-override", "currdepth": 1, "priority": 0, "maxdepth": None, "intransitive": False}]
    server, session = make_server(getFullInheritance=lambda tag, reverse=False: parents)
    text = await call_text(server, "koji_tag_inheritance", tag="f41-build")
    assert session.calls[0] == ("getFullInheritance", ("f41-build",), {"reverse": False})
    assert "| f41-override | 1 | 0 |" in text


async def test_get_target_not_found(make_server):
    server, _ = make_server(getBuildTarget=lambda info, strict=False: None)
    with pytest.raises(ToolError, match="Build target 'f99' not found"):
        await server.call_tool("koji_get_target", {"target": "f99"})


async def test_get_target(make_server):
    target = {"name": "f41", "build_tag_name": "f41-build", "dest_tag_name": "f41-updates-candidate"}
    server, _ = make_server(getBuildTarget=lambda info, strict=False: target)
    text = await call_text(server, "koji_get_target", target="f41")
    assert "- **build_tag_name**: f41-build" in text
