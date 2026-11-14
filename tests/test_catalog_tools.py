import json

import pytest
from conftest import call_text
from mcp.server.mcpserver.exceptions import ToolError


async def test_search_passes_type_match_and_paging(make_server):
    server, session = make_server(search=lambda terms, type, matchType, queryOpts=None: [{"id": 1088, "name": "bash"}])
    text = await call_text(server, "koji_search", terms="bash*", search_type="package")
    assert session.calls[0] == (
        "search",
        ("bash*", "package", "glob"),
        {"queryOpts": {"limit": 26, "offset": 0, "order": "name"}},
    )
    assert "| 1088 | bash |" in text


async def test_history_requires_a_filter(make_server):
    server, session = make_server(queryHistory=lambda **kw: {})
    with pytest.raises(ToolError, match="at least one"):
        await server.call_tool("koji_history", {})
    assert session.calls == []


async def test_history_flattens_tables_newest_first(make_server):
    history = {
        "tag_listing": [
            {
                "tag.name": "f41-updates",
                "name": "bash",
                "version": "5.2.32",
                "release": "1.fc41",
                "create_ts": 100.0,
                "creator_name": "bodhi",
                "revoke_ts": None,
                "revoker_name": None,
                "active": True,
            },
            {
                "tag.name": "f41-updates-testing",
                "name": "bash",
                "version": "5.2.32",
                "release": "1.fc41",
                "create_ts": 200.0,
                "creator_name": "bodhi",
                "revoke_ts": 300.0,
                "revoker_name": "bodhi",
                "active": None,
            },
        ]
    }
    server, session = make_server(queryHistory=lambda **kw: history)
    text = await call_text(server, "koji_history", build="bash-5.2.32-1.fc41", after="2024-01-01")
    assert session.calls[0] == ("queryHistory", (), {"build": "bash-5.2.32-1.fc41", "after": 1704067200.0})
    lines = [line for line in text.splitlines() if line.startswith("| tag_listing")]
    assert "f41-updates-testing bash-5.2.32-1.fc41" in lines[0]
    assert "1970-01-01T00:05:00+00:00" in lines[0]
    assert "f41-updates bash-5.2.32-1.fc41" in lines[1]


async def test_history_json_keeps_raw_fields(make_server):
    history = {"tag_listing": [{"tag.name": "f41", "create_ts": 1.0, "revoke_ts": None}]}
    server, _ = make_server(queryHistory=lambda **kw: history)
    data = json.loads(await call_text(server, "koji_history", tag="f41", response_format="json"))
    assert data["items"][0]["table"] == "tag_listing"
    assert data["items"][0]["tag.name"] == "f41"
    assert data["items"][0]["create_time"] == "1970-01-01T00:00:01+00:00"
