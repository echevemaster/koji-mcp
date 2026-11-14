import json

from koji_mcp.formatting import (
    iso_from_ts,
    normalize,
    page,
    render,
    tail_text,
)


def test_iso_from_ts_is_utc_iso8601():
    assert iso_from_ts(0) == "1970-01-01T00:00:00+00:00"
    assert iso_from_ts(1700000000.75) == "2023-11-14T22:13:20+00:00"


def test_normalize_replaces_ts_fields_with_iso_time_fields_recursively():
    data = {
        "creation_time": "2023-11-14 22:13:20.123456+00:00",
        "creation_ts": 1700000000.0,
        "completion_ts": None,
        "children": [{"create_ts": 0}],
    }
    assert normalize(data) == {
        "creation_time": "2023-11-14T22:13:20+00:00",
        "completion_time": None,
        "children": [{"create_time": "1970-01-01T00:00:00+00:00"}],
    }


def test_page_detects_more_results_from_the_extra_row():
    rows, has_more = page([1, 2, 3, 4], limit=3)
    assert rows == [1, 2, 3]
    assert has_more is True
    rows, has_more = page([1, 2], limit=3)
    assert rows == [1, 2]
    assert has_more is False


def test_render_json_is_normalized():
    out = render({"start_ts": 0}, "json")
    assert json.loads(out) == {"start_time": "1970-01-01T00:00:00+00:00"}


def test_render_markdown_dict_lists_fields():
    out = render({"nvr": "bash-5.2-1.fc41", "state": "COMPLETE"}, "markdown", title="Build")
    assert out.splitlines()[0] == "## Build"
    assert "- **nvr**: bash-5.2-1.fc41" in out
    assert "- **state**: COMPLETE" in out


def test_render_markdown_rows_builds_a_table_with_columns():
    out = render(
        [{"nvr": "a-1-1", "state": "COMPLETE", "extra": "x"}],
        "markdown",
        title="Builds",
        columns=["nvr", "state"],
    )
    lines = out.splitlines()
    assert "| nvr | state |" in lines
    assert "| a-1-1 | COMPLETE |" in lines
    assert "extra" not in out


def test_render_markdown_empty_rows_says_so():
    assert "No results." in render([], "markdown", title="Builds", columns=["nvr"])


def test_render_pagination_footer():
    out = render([{"nvr": "a"}], "markdown", columns=["nvr"], offset=20, has_more=True)
    assert "offset=21" in out
    data = json.loads(render([{"nvr": "a"}], "json", offset=20, has_more=True))
    assert data == {"items": [{"nvr": "a"}], "offset": 20, "count": 1, "has_more": True, "next_offset": 21}


def test_tail_text_keeps_last_lines_and_reports_truncation():
    text = "\n".join(f"line {i}" for i in range(1, 11))
    out, truncated = tail_text(text, 3)
    assert out == "line 8\nline 9\nline 10"
    assert truncated is True
    out, truncated = tail_text("a\nb", 3)
    assert (out, truncated) == ("a\nb", False)
