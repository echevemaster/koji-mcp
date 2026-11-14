"""Response shaping: ISO dates, pagination, markdown/JSON rendering, truncation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal, Sequence

ResponseFormat = Literal["markdown", "json"]

DEFAULT_LIMIT = 25
MAX_LIMIT = 200


def iso_from_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat(timespec="seconds")


def normalize(value: Any) -> Any:
    """Replace koji's ``X_ts`` epoch fields with ISO 8601 ``X_time`` fields."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        stamps: dict[str, Any] = {}
        for key, item in value.items():
            if key.endswith("_ts"):
                stamps[key[: -len("_ts")] + "_time"] = item
            else:
                out[key] = normalize(item)
        for key, ts in stamps.items():
            out[key] = iso_from_ts(ts) if isinstance(ts, (int, float)) else ts
        return out
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    return value


def query_opts(limit: int, offset: int, order: str | None = None) -> dict:
    """Koji queryOpts asking for one extra row to detect a next page."""
    opts: dict[str, Any] = {"limit": limit + 1, "offset": offset}
    if order:
        opts["order"] = order
    return opts


def page(rows: Sequence[Any], limit: int) -> tuple[list[Any], bool]:
    rows = list(rows)
    return rows[:limit], len(rows) > limit


def tail_text(text: str, lines: int) -> tuple[str, bool]:
    parts = text.splitlines()
    if len(parts) <= lines:
        return text, False
    return "\n".join(parts[-lines:]), True


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, default=str)
    return str(value).replace("|", "\\|").replace("\n", " ")


def render(
    data: Any,
    fmt: ResponseFormat,
    *,
    title: str | None = None,
    columns: Sequence[str] | None = None,
    offset: int | None = None,
    has_more: bool | None = None,
) -> str:
    """Render a record (dict) or a list of records.

    Passing ``has_more`` marks the result as a page and adds pagination
    metadata (JSON) or a footer (markdown).
    """
    data = normalize(data)
    paged = has_more is not None
    if fmt == "json":
        if paged:
            count = len(data)
            payload = {
                "items": data,
                "offset": offset or 0,
                "count": count,
                "has_more": has_more,
                "next_offset": (offset or 0) + count if has_more else None,
            }
            return json.dumps(payload, indent=2, default=str)
        return json.dumps(data, indent=2, default=str)

    lines: list[str] = [f"## {title}", ""] if title else []
    if isinstance(data, dict):
        lines += [f"- **{key}**: {_cell(value)}" for key, value in data.items()]
    elif not data:
        lines.append("No results.")
    else:
        cols = list(columns or data[0].keys())
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("|" + "---|" * len(cols))
        lines += ["| " + " | ".join(_cell(row.get(c)) for c in cols) + " |" for row in data]
    if paged and has_more:
        lines += ["", f"More results available: call again with offset={(offset or 0) + len(data)}."]
    return "\n".join(lines)
