"""Tag, inheritance and build target tools."""

from __future__ import annotations

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from koji_mcp.client import KojiClient
from koji_mcp.formatting import render
from koji_mcp.tools.common import (
    DEFAULT_LIMIT,
    READ_ONLY,
    Format,
    Limit,
    Offset,
    TagRef,
    id_or_name,
    slice_page,
)

TAGGED_COLUMNS = ["nvr", "tag_name", "owner_name", "creation_time"]
PackageFilter = Annotated[str | None, Field(description="Only this package, e.g. 'bash'.")]


def register(mcp: MCPServer, client: KojiClient) -> None:
    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def koji_latest_builds(
        tag: TagRef,
        package: PackageFilter = None,
        limit: Limit = DEFAULT_LIMIT,
        offset: Offset = 0,
        response_format: Format = "markdown",
    ) -> str:
        """Latest build of each package in a tag, inheritance included.

        Answers "which version of X is in f41-updates?" when package is set.
        """
        rows = await client.call("getLatestBuilds", id_or_name(tag), package=package)
        rows, has_more = slice_page(rows, limit, offset)
        return render(
            rows,
            response_format,
            title=f"Latest builds in {tag}",
            columns=TAGGED_COLUMNS,
            offset=offset,
            has_more=has_more,
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def koji_list_tagged(
        tag: TagRef,
        package: PackageFilter = None,
        owner: Annotated[str | None, Field(description="Only builds owned by this user.")] = None,
        inherit: Annotated[bool, Field(description="Include builds from parent tags.")] = False,
        latest: Annotated[bool, Field(description="Only the latest build per package.")] = False,
        limit: Limit = DEFAULT_LIMIT,
        offset: Offset = 0,
        response_format: Format = "markdown",
    ) -> str:
        """List every build tagged into a tag (all versions unless latest=true)."""
        rows = await client.call(
            "listTagged", id_or_name(tag), package=package, owner=owner, inherit=inherit, latest=latest
        )
        rows, has_more = slice_page(rows, limit, offset)
        return render(
            rows,
            response_format,
            title=f"Builds tagged in {tag}",
            columns=TAGGED_COLUMNS,
            offset=offset,
            has_more=has_more,
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def koji_get_tag(tag: TagRef, response_format: Format = "markdown") -> str:
        """Get a tag's configuration: arches, locked state, permissions, extra settings."""
        info = await client.call("getTag", id_or_name(tag), strict=False)
        if not info:
            raise ToolError(
                f"Tag {tag!r} not found. Use koji_search with search_type='tag' and a glob such as 'f41*'."
            )
        return render(info, response_format, title=f"Tag {info.get('name')}")

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def koji_tag_inheritance(
        tag: TagRef,
        reverse: Annotated[bool, Field(description="List tags inheriting from this one instead.")] = False,
        response_format: Format = "markdown",
    ) -> str:
        """Full inheritance chain of a tag, with depth, priority and package filters."""
        rows = await client.call("getFullInheritance", id_or_name(tag), reverse=reverse)
        return render(
            rows,
            response_format,
            title=f"{'Children' if reverse else 'Parents'} of {tag}",
            columns=["name", "currdepth", "priority", "maxdepth", "intransitive", "noconfig", "pkg_filter"],
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def koji_get_target(
        target: Annotated[str, Field(min_length=1, description="Build target name, e.g. 'f42'.")],
        response_format: Format = "markdown",
    ) -> str:
        """Get a build target: the tag builds run in and the tag results land in."""
        info = await client.call("getBuildTarget", id_or_name(target), strict=False)
        if not info:
            raise ToolError(
                f"Build target {target!r} not found. Use koji_search with search_type='target'."
            )
        return render(info, response_format, title=f"Target {info.get('name')}")
