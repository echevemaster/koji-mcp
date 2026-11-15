"""Search and history tools."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from koji_mcp.client import KojiClient
from koji_mcp.formatting import normalize, page, query_opts, render
from koji_mcp.tools.common import DEFAULT_LIMIT, READ_ONLY, Format, Limit, Offset, parse_date, slice_page

SearchType = Literal["package", "build", "tag", "target", "user", "host", "rpm"]


def _create_time(row: dict) -> float:
    return row.get("create_ts") or 0


def _subject(row: dict) -> str:
    """What the change is about, besides the tag: a build NVR, a package or a user."""
    parts: list[Any] = [row.get("package.name"), row.get("user.name")]
    if row.get("name") and row.get("version") and row.get("release"):
        parts.append(f"{row['name']}-{row['version']}-{row['release']}")
    return " ".join(str(part) for part in parts if part)


def register(mcp: MCPServer, client: KojiClient) -> None:
    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def koji_search(
        terms: Annotated[str, Field(min_length=1, description="Search terms, e.g. 'bash*'.")],
        search_type: Annotated[SearchType, Field(description="What to search for.")],
        match_type: Annotated[
            Literal["glob", "regexp", "exact"], Field(description="How terms are matched.")
        ] = "glob",
        limit: Limit = DEFAULT_LIMIT,
        offset: Offset = 0,
        response_format: Format = "markdown",
    ) -> str:
        """Find packages, builds, tags, targets, users, hosts or RPMs by name.

        Use it to discover exact names before calling the other tools.
        """
        rows = await client.call(
            "search", terms, search_type, match_type, queryOpts=query_opts(limit, offset, "name")
        )
        rows, has_more = page(rows, limit)
        return render(
            rows,
            response_format,
            title=f"{search_type} matching {terms!r}",
            columns=["id", "name"],
            offset=offset,
            has_more=has_more,
        )

    @mcp.tool(annotations=READ_ONLY, structured_output=False)
    async def koji_history(
        build: Annotated[str | None, Field(description="Build NVR.")] = None,
        tag: Annotated[str | None, Field(description="Tag name.")] = None,
        package: Annotated[str | None, Field(description="Package name.")] = None,
        user: Annotated[str | None, Field(description="User who made the change.")] = None,
        after: Annotated[
            str | None,
            Field(description="ISO 8601 date. Keeps rows where either the change (tag) or its undo (untag) is after it."),
        ] = None,
        before: Annotated[
            str | None,
            Field(description="ISO 8601 date. Keeps rows where either the change (tag) or its undo (untag) is before it."),
        ] = None,
        limit: Limit = DEFAULT_LIMIT,
        offset: Offset = 0,
        response_format: Format = "markdown",
    ) -> str:
        """Audit history: who tagged/untagged builds, changed tags or package lists, and when.

        Needs at least one of build, tag, package or user. Each row is one
        change of kind `table` (tag_listing: a build in a tag; tag_packages: a
        package list entry; tag_inheritance, tag_config, ...). create_time and
        creator_name say when and by whom it was made (e.g. the build was
        tagged); revoke_time and revoker_name say when and by whom it was undone
        (e.g. untagged). A move between tags, such as a Bodhi push, shows as an
        untag of the old tag's row plus a new row for the new tag. Rows are
        sorted by create_time, newest first.
        """
        filters = {"build": build, "tag": tag, "package": package, "user": user}
        if not any(filters.values()):
            raise ToolError("koji_history needs at least one of: build, tag, package, user.")
        filters["after"] = parse_date(after, "after")
        filters["before"] = parse_date(before, "before")
        kwargs = {key: value for key, value in filters.items() if value is not None}
        tables = await client.call("queryHistory", **kwargs)
        rows = [{"table": table, **row} for table, entries in tables.items() for row in entries]
        rows.sort(key=_create_time, reverse=True)
        rows, has_more = slice_page(rows, limit, offset)
        if response_format == "markdown":
            rows = [dict(normalize(row), tag=row.get("tag.name"), subject=_subject(row)) for row in rows]
        return render(
            rows,
            response_format,
            title="History",
            columns=["table", "tag", "subject", "create_time", "creator_name", "revoke_time", "revoker_name"],
            offset=offset,
            has_more=has_more,
        )
