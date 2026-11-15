import json

import pytest
from conftest import call_text
from mcp.server.mcpserver.exceptions import ToolError

BUILD = {
    "id": 2525453,
    "build_id": 2525453,
    "nvr": "bash-5.2.32-1.fc41",
    "name": "bash",
    "state": 1,
    "owner_name": "bob",
    "task_id": 121669842,
    "creation_ts": 1723466000.0,
    "creation_time": "2024-08-12 12:33:20+00:00",
    "completion_ts": None,
    "completion_time": None,
}


async def test_get_build_by_nvr_shows_state_name_and_web_link(make_server):
    server, session = make_server(getBuild=lambda info, strict=False: dict(BUILD))
    text = await call_text(server, "koji_get_build", build="bash-5.2.32-1.fc41")
    assert session.calls == [("getBuild", ("bash-5.2.32-1.fc41",), {"strict": False})]
    assert "- **state**: COMPLETE" in text
    assert "- **creation_time**: 2024-08-12T12:33:20+00:00" in text
    assert "https://koji.example.org/koji/buildinfo?buildID=2525453" in text


async def test_get_build_numeric_id_is_sent_as_int(make_server):
    server, session = make_server(getBuild=lambda info, strict=False: dict(BUILD))
    await call_text(server, "koji_get_build", build="2525453")
    assert session.calls[0][1] == (2525453,)


async def test_get_build_json_returns_full_normalized_record(make_server):
    server, _ = make_server(getBuild=lambda info, strict=False: dict(BUILD))
    data = json.loads(await call_text(server, "koji_get_build", build="x", response_format="json"))
    assert data["state"] == "COMPLETE"
    assert data["completion_time"] is None
    assert "creation_ts" not in data


async def test_get_build_not_found_suggests_search(make_server):
    server, _ = make_server(getBuild=lambda info, strict=False: None)
    with pytest.raises(ToolError, match="koji_search"):
        await server.call_tool("koji_get_build", {"build": "nope-1-1"})


async def test_list_builds_unknown_owner_is_an_error(make_server):
    server, _ = make_server(listBuilds=lambda **kw: [], getUser=lambda user, strict=False: None)
    with pytest.raises(ToolError, match=r"No Koji user named 'nobody'.*koji_search"):
        await server.call_tool("koji_list_builds", {"owner": "nobody"})


async def test_list_builds_known_owner_without_builds_is_empty(make_server):
    server, _ = make_server(listBuilds=lambda **kw: [], getUser=lambda user, strict=False: {"id": 1, "name": user})
    text = await call_text(server, "koji_list_builds", owner="bob", state="FAILED")
    assert "No results." in text


async def test_list_builds_unknown_package_is_an_error(make_server):
    server, _ = make_server(listBuilds=lambda **kw: [], getPackageID=lambda name, strict=False: None)
    with pytest.raises(ToolError, match=r"No Koji package named 'no-such-pkg'.*koji_search"):
        await server.call_tool("koji_list_builds", {"package": "no-such-pkg"})


async def test_list_builds_known_package_without_matches_is_empty(make_server):
    server, _ = make_server(listBuilds=lambda **kw: [], getPackageID=lambda name, strict=False: 1088)
    text = await call_text(server, "koji_list_builds", package="bash", created_after="2030-01-01")
    assert "No results." in text


async def test_list_builds_with_results_does_not_look_up_the_owner(make_server):
    server, session = make_server(listBuilds=lambda **kw: [dict(BUILD)])
    await call_text(server, "koji_list_builds", owner="bob", package="bash")
    assert [name for name, _, _ in session.calls] == ["listBuilds"]


async def test_list_builds_maps_filters_to_hub_arguments(make_server):
    rows = [dict(BUILD, nvr=f"bash-{i}") for i in range(3)]
    server, session = make_server(listBuilds=lambda **kw: rows)
    text = await call_text(
        server,
        "koji_list_builds",
        package="bash",
        owner="bob",
        state="FAILED",
        created_after="2024-01-01",
        limit=2,
        offset=4,
    )
    name, args, kwargs = session.calls[0]
    assert name == "listBuilds"
    assert kwargs["packageID"] == "bash"
    assert kwargs["userID"] == "bob"
    assert kwargs["state"] == 3
    assert kwargs["createdAfter"] == 1704067200.0
    assert kwargs["queryOpts"] == {"limit": 3, "offset": 4, "order": "-build_id"}
    assert "| bash-0 |" in text and "| bash-1 |" in text and "bash-2" not in text
    assert "offset=6" in text


async def test_list_builds_rejects_bad_date(make_server):
    server, session = make_server(listBuilds=lambda **kw: [])
    with pytest.raises(ToolError, match="ISO 8601"):
        await server.call_tool("koji_list_builds", {"package": "bash", "created_before": "last week"})
    assert session.calls == []


async def test_list_rpms_resolves_nvr_and_filters_arches(make_server):
    server, session = make_server(
        getBuild=lambda info, strict=False: dict(BUILD),
        listRPMs=lambda **kw: [{"nvr": "bash-5.2.32-1.fc41", "arch": "x86_64", "size": 10}],
    )
    text = await call_text(server, "koji_list_rpms", build="bash-5.2.32-1.fc41", arches=["x86_64"])
    assert session.calls[1] == (
        "listRPMs",
        (),
        {"buildID": 2525453, "arches": ["x86_64"], "queryOpts": {"limit": 101, "offset": 0, "order": "name"}},
    )
    assert "| bash-5.2.32-1.fc41 | x86_64 |" in text


async def test_list_rpms_shows_package_name_and_size_unit(make_server):
    rpms = [
        {"name": "bash", "nvr": "bash-5.2.32-1.fc41", "arch": "x86_64", "size": 1874934},
        {"name": "bash-doc", "nvr": "bash-doc-5.2.32-1.fc41", "arch": "x86_64", "size": 1518456},
    ]
    server, _ = make_server(getBuild=lambda info, strict=False: dict(BUILD), listRPMs=lambda **kw: rpms)
    text = await call_text(server, "koji_list_rpms", build="bash-5.2.32-1.fc41")
    assert "| name | nvr | arch | size_bytes |" in text
    assert "| bash | bash-5.2.32-1.fc41 | x86_64 | 1874934 |" in text
    assert "| bash-doc | bash-doc-5.2.32-1.fc41 | x86_64 | 1518456 |" in text
