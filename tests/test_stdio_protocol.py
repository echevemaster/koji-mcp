"""Talk to the real server process over stdio, as an MCP client would."""

import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

EXPECTED_TOOLS = {
    "koji_get_build",
    "koji_list_builds",
    "koji_list_rpms",
    "koji_latest_builds",
    "koji_list_tagged",
    "koji_get_tag",
    "koji_tag_inheritance",
    "koji_get_target",
    "koji_get_task",
    "koji_task_logs",
    "koji_diagnose_failed_build",
    "koji_search",
    "koji_history",
}

SERVER = StdioServerParameters(command=sys.executable, args=["-m", "koji_mcp.server", "--profile", "koji"])


async def test_server_lists_every_tool_as_read_only_over_stdio():
    async with stdio_client(SERVER) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = (await session.list_tools()).tools
        assert {tool.name for tool in tools} == EXPECTED_TOOLS
        for tool in tools:
            assert tool.annotations.read_only_hint is True, tool.name
            assert tool.annotations.destructive_hint is False, tool.name
            assert tool.description, tool.name


async def test_tool_errors_come_back_as_error_results_over_stdio():
    async with stdio_client(SERVER) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("koji_diagnose_failed_build", {})
        assert result.is_error is True
        assert "exactly one of build or task_id" in result.content[0].text
