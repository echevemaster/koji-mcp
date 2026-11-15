"""Build and RPM tools."""

from __future__ import annotations

from typing import Annotated

import koji
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from koji_mcp.client import KojiClient
from koji_mcp.formatting import normalize, page, query_opts, render
from koji_mcp.tools.common import (
    DEFAULT_LIMIT,
    READ_ONLY,
    BuildRef,
    BuildState,
    Format,
    Limit,
    Offset,
    build_state_name,
    parse_date,
    resolve_build,
)

BUILD_FIELDS = [
    "id",
    "nvr",
    "state",
    "owner_name",
    "task_id",
    "source",
    "volume_name",
    "creation_time",
    "start_time",
    "completion_time",
    "url",
]
BUILD_COLUMNS = ["nvr", "state", "owner_name", "creation_time", "task_id"]


def register(mcp: MCPServer, client: KojiClient) -> None:
    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def koji_get_build(build: BuildRef, response_format: Format = "markdown") -> str:
        """Get one Koji build: state, owner, task, timestamps and web link.

        Use koji_list_rpms for its RPMs and koji_get_task with task_id for the
        task tree. If the build failed, koji_diagnose_failed_build explains why.
        """
        info = normalize(await resolve_build(client, build))
        info["state"] = build_state_name(info.get("state"))
        if client.weburl:
            info["url"] = f"{client.weburl}/buildinfo?buildID={info['id']}"
        if response_format == "json":
            return render(info, "json")
        summary = {key: info.get(key) for key in BUILD_FIELDS if key in info}
        return render(summary, "markdown", title=f"Build {info.get('nvr')}")

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def koji_list_builds(
        package: Annotated[str | None, Field(description="Package name, e.g. 'bash'.")] = None,
        owner: Annotated[str | None, Field(description="Koji user name of the build owner.")] = None,
        state: Annotated[BuildState | None, Field(description="Only builds in this state.")] = None,
        prefix: Annotated[str | None, Field(description="Only packages whose name starts with this, e.g. 'python-' (not an NVR prefix).")] = None,
        created_after: Annotated[str | None, Field(description="ISO 8601 date, e.g. '2024-05-01'.")] = None,
        created_before: Annotated[str | None, Field(description="ISO 8601 date, e.g. '2024-06-01'.")] = None,
        limit: Limit = DEFAULT_LIMIT,
        offset: Offset = 0,
        response_format: Format = "markdown",
    ) -> str:
        """List builds, newest first, filtered by package, package-name prefix, owner, state or dates."""
        filters = {
            "packageID": package,
            "userID": owner,
            "state": koji.BUILD_STATES[state] if state else None,
            "prefix": prefix,
            "createdAfter": parse_date(created_after, "created_after"),
            "createdBefore": parse_date(created_before, "created_before"),
        }
        kwargs = {key: value for key, value in filters.items() if value is not None}
        rows = await client.call("listBuilds", **kwargs, queryOpts=query_opts(limit, offset, "-build_id"))
        # The hub returns no builds, not an error, for an unknown user or package name.
        if not rows and owner and await client.call("getUser", owner) is None:
            raise ToolError(
                f"No Koji user named {owner!r}. Find the exact user name with "
                "koji_search (search_type='user'), or start from a package the person maintains."
            )
        if not rows and package and await client.call("getPackageID", package) is None:
            raise ToolError(
                f"No Koji package named {package!r}. Find the exact package name with "
                "koji_search (search_type='package')."
            )
        rows, has_more = page(rows, limit)
        for row in rows:
            row["state"] = build_state_name(row.get("state"))
        return render(
            rows,
            response_format,
            title="Builds",
            columns=BUILD_COLUMNS,
            offset=offset,
            has_more=has_more,
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def koji_list_rpms(
        build: BuildRef,
        arches: Annotated[
            list[str] | None, Field(description="Only these arches, e.g. ['x86_64', 'src'].")
        ] = None,
        limit: Limit = 100,
        offset: Offset = 0,
        response_format: Format = "markdown",
    ) -> str:
        """List the RPMs produced by a build: package name, NVR, arch and size.

        size_bytes is the size of the RPM file in bytes (not the installed size).
        """
        info = await resolve_build(client, build)
        kwargs = {"buildID": info["id"]}
        if arches:
            kwargs["arches"] = arches
        rows = await client.call("listRPMs", **kwargs, queryOpts=query_opts(limit, offset, "name"))
        rows, has_more = page(rows, limit)
        rows = [dict(row, size_bytes=row.get("size")) for row in rows]
        return render(
            rows,
            response_format,
            title=f"RPMs of {info.get('nvr')}",
            columns=["name", "nvr", "arch", "size_bytes"],
            offset=offset,
            has_more=has_more,
        )
