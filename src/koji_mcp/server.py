"""Koji MCP server: read-only access to a Koji build system hub."""

from __future__ import annotations

import argparse
import os

from mcp.server.mcpserver import MCPServer

from koji_mcp.client import KojiClient
from koji_mcp.tools import builds, catalog, tags

INSTRUCTIONS = """\
Read-only access to a Koji build system hub (Fedora's by default).
Use koji_search to find exact names, then the get/list tools.
Builds are identified by NVR (name-version-release) or numeric ID.
List tools paginate with limit/offset; dates are ISO 8601 in UTC.
"""


def create_server(client: KojiClient) -> MCPServer:
    mcp = MCPServer("koji_mcp", instructions=INSTRUCTIONS)
    for module in (builds, tags, catalog):
        module.register(mcp, client)
    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP server for the Koji build system (stdio).")
    parser.add_argument(
        "--profile",
        default=os.environ.get("KOJI_MCP_PROFILE", "koji"),
        help="koji client profile from /etc/koji.conf(.d) or ~/.koji/config (default: koji, i.e. Fedora; "
        "env KOJI_MCP_PROFILE).",
    )
    args = parser.parse_args()
    create_server(KojiClient(profile=args.profile)).run("stdio")


if __name__ == "__main__":
    main()
