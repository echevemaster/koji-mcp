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
    assert "| f41-updates-testing | bash-5.2.32-1.fc41 |" in lines[0]
    assert "1970-01-01T00:05:00+00:00" in lines[0]
    assert "| f41-updates | bash-5.2.32-1.fc41 |" in lines[1]


async def test_history_sorts_by_create_time_and_separates_tag_and_subject(make_server):
    def row(tag, create, revoke=None):
        return {
            "tag.name": tag, "name": "bash", "version": "5.3", "release": "1.fc46",
            "create_ts": create, "creator_name": "bodhi", "revoke_ts": revoke,
            "revoker_name": "bodhi" if revoke else None,
        }

    # A Bodhi push: each tag is revoked when the next one is created.
    history = {
        "tag_listing": [
            row("f46-updates-candidate", 100.0, revoke=500.0),
            row("f46-updates-testing", 300.0, revoke=500.0),
            row("f46", 600.0),
            row("f46-signing-pending", 200.0, revoke=300.0),
        ],
        "tag_packages": [{"tag.name": "f46", "package.name": "bash", "user.name": "alice", "create_ts": 50.0}],
    }
    server, _ = make_server(queryHistory=lambda **kw: history)
    text = await call_text(server, "koji_history", package="bash")
    assert "| table | tag | subject | create_time | creator_name | revoke_time | revoker_name |" in text
    tags = [line.split(" | ")[1] for line in text.splitlines() if line.startswith("| tag_")]
    assert tags == ["f46", "f46-updates-testing", "f46-signing-pending", "f46-updates-candidate", "f46"]
    assert "| tag_packages | f46 | bash alice |" in text


async def test_history_description_explains_create_and_revoke(make_server):
    server, _ = make_server()
    tool = {t.name: t for t in await server.list_tools()}["koji_history"]
    assert "revoke" in tool.description and "untag" in tool.description
    assert "create_time, newest first" in tool.description
    props = tool.input_schema["properties"]
    for name in ("after", "before"):
        assert "either" in props[name]["description"] and "untag" in props[name]["description"]


async def test_history_json_keeps_raw_fields(make_server):
    history = {"tag_listing": [{"tag.name": "f41", "create_ts": 1.0, "revoke_ts": None}]}
    server, _ = make_server(queryHistory=lambda **kw: history)
    data = json.loads(await call_text(server, "koji_history", tag="f41", response_format="json"))
    assert data["items"][0]["table"] == "tag_listing"
    assert data["items"][0]["tag.name"] == "f41"
    assert data["items"][0]["create_time"] == "1970-01-01T00:00:01+00:00"
