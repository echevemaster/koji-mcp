"""Shared parameter types, annotations and lookups for the tools."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import koji
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from koji_mcp.client import KojiClient
from koji_mcp.formatting import DEFAULT_LIMIT, MAX_LIMIT

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)

Format = Annotated[
    Literal["markdown", "json"],
    Field(description="'markdown' for reading, 'json' for the complete normalized record."),
]
Limit = Annotated[int, Field(ge=1, le=MAX_LIMIT, description="Maximum number of results.")]
Offset = Annotated[int, Field(ge=0, description="Number of results to skip, for pagination.")]
BuildRef = Annotated[
    str,
    Field(min_length=1, description="Build NVR (e.g. 'bash-5.2.32-1.fc41') or numeric build ID."),
]
TagRef = Annotated[str, Field(min_length=1, description="Tag name (e.g. 'f41-updates') or numeric tag ID.")]

BuildState = Literal["BUILDING", "COMPLETE", "DELETED", "FAILED", "CANCELED"]

__all__ = [
    "DEFAULT_LIMIT",
    "READ_ONLY",
    "BuildRef",
    "BuildState",
    "Format",
    "Limit",
    "Offset",
    "TagRef",
    "build_state_name",
    "id_or_name",
    "parse_date",
    "resolve_build",
    "slice_page",
]


def id_or_name(value: str) -> int | str:
    """Koji accepts IDs as ints and names as strings."""
    return int(value) if value.isdigit() else value


def build_state_name(state: Any) -> Any:
    try:
        return koji.BUILD_STATES[state]
    except (KeyError, IndexError, TypeError):
        return state


def parse_date(value: str | None, field: str) -> float | None:
    """Turn an ISO 8601 date or datetime into an epoch timestamp (UTC if naive)."""
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ToolError(
            f"Invalid {field}: {value!r}. Use an ISO 8601 date such as '2024-05-01' "
            "or '2024-05-01T13:00:00+00:00'."
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def slice_page(rows: list[Any], limit: int, offset: int) -> tuple[list[Any], bool]:
    """Paginate a result the hub returns whole (no queryOpts support)."""
    return rows[offset : offset + limit], len(rows) > offset + limit


async def resolve_build(client: KojiClient, build: str) -> dict:
    info = await client.call("getBuild", id_or_name(build), strict=False)
    if not info:
        raise ToolError(
            f"Build {build!r} not found. Check the NVR (name-version-release) or use "
            "koji_search with search_type='build' and a glob such as 'bash-5.2*'."
        )
    return info
