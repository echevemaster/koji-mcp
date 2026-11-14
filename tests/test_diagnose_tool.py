import json

import koji
import pytest
from conftest import call_text
from mcp.server.mcpserver.exceptions import ToolError

FAILED_BUILD = {"id": 9, "nvr": "bazelisk-1.30.0-4.fc44", "state": 3, "task_id": 100}
PARENT = {"id": 100, "method": "build", "state": 5, "arch": "noarch", "request": []}
CHILDREN = [
    {"id": 101, "method": "buildArch", "state": 5, "arch": "aarch64", "label": "aarch64"},
    {"id": 102, "method": "buildSRPMFromSCM", "state": 2, "arch": "noarch", "label": "srpm"},
    {"id": 103, "method": "buildArch", "state": 5, "arch": "x86_64", "label": "x86_64"},
]
BUILD_LOG = "\n".join(
    [f"+ step {i}" for i in range(200)]
    + [
        "go: go.mod requires go >= 1.27.3 (running go 1.26.10; GOTOOLCHAIN=local)",
        "RPM build errors:",
        "error: Bad exit status from /var/tmp/rpm-tmp.0yB55B (%build)",
        "Child return code was: 1",
    ]
)
ROOT_LOG = "\n".join(f"DEBUG util.py: line {i}" for i in range(50))


def _logs(build_log=BUILD_LOG, root_log=ROOT_LOG):
    files = {"build.log": build_log.encode(), "root.log": root_log.encode()}

    def list_output(task_id, stat=False):
        return {name: {"st_size": str(len(data))} for name, data in files.items()}

    def download(task_id, file_name, offset=0, size=-1):
        return files[file_name][offset : offset + size]

    return {"listTaskOutput": list_output, "downloadTaskOutput": download}


def _task_result(task_id):
    raise koji.BuildError(
        f"error building package (arch {task_id}), mock exited with status 1; see build.log or root.log"
    )


def _handlers(**overrides):
    handlers = {
        "getBuild": lambda info, strict=False: dict(FAILED_BUILD),
        "getTaskInfo": lambda tid, request=False, strict=False: dict(PARENT),
        "getTaskChildren": lambda tid, request=False, strict=False: [dict(c) for c in CHILDREN],
        "getTaskResult": _task_result,
        **_logs(),
    }
    handlers.update(overrides)
    return handlers


async def test_diagnose_by_nvr_reads_the_first_failed_arch_task(make_server):
    server, session = make_server(**_handlers())
    text = await call_text(server, "koji_diagnose_failed_build", build="bazelisk-1.30.0-4.fc44")
    assert "## Diagnosis: bazelisk-1.30.0-4.fc44 (task 100)" in text
    assert "- **Failed task**: 101 buildArch aarch64" in text
    assert "- **Other failed tasks**: 103 buildArch x86_64" in text
    assert "mock exited with status 1" in text
    assert (
        "- **Likely cause**: the %build section failed: the buildroot's Go is older than go.mod requires"
        " (`go: go.mod requires go >= 1.27.3 (running go 1.26.10; GOTOOLCHAIN=local)`)"
    ) in text
    assert "go: go.mod requires go >= 1.27.3" in text
    assert "https://kojipkgs.example.org/work/tasks/101/101/build.log" in text
    downloaded = {args[1] for name, args, _ in session.calls if name == "downloadTaskOutput"}
    assert downloaded == {"build.log", "root.log"}
    assert all(args[0] == 101 for name, args, _ in session.calls if name == "downloadTaskOutput")


async def test_diagnose_lists_every_arch_subtask_state(make_server):
    children = CHILDREN + [{"id": 104, "method": "buildArch", "state": 2, "arch": "s390x", "label": "s390x"}]
    server, _ = make_server(
        **_handlers(getTaskChildren=lambda tid, request=False, strict=False: [dict(c) for c in children])
    )
    text = await call_text(server, "koji_diagnose_failed_build", build="x")
    assert "- **Arch subtasks**: aarch64 FAILED, x86_64 FAILED, s390x CLOSED" in text
    data = json.loads(await call_text(server, "koji_diagnose_failed_build", build="x", response_format="json"))
    assert [(t["arch"], t["state"]) for t in data["arch_subtasks"]] == [
        ("aarch64", "FAILED"),
        ("x86_64", "FAILED"),
        ("s390x", "CLOSED"),
    ]


async def test_diagnose_key_lines_come_from_before_the_tail(make_server):
    log = "\n".join(["make: *** [Makefile:12: all] Error 2"] + [f"+ noise {i}" for i in range(100)])
    server, _ = make_server(**_handlers(**_logs(build_log=log)))
    text = await call_text(server, "koji_diagnose_failed_build", build="x", tail_lines=5)
    assert "make: *** [Makefile:12: all] Error 2" in text
    assert "+ noise 94" not in text


async def test_diagnose_detects_missing_build_requires_from_root_log(make_server):
    root = ROOT_LOG + "\nDEBUG: No match for argument: golang >= 1.27\nDEBUG: Failed to resolve the transaction"
    server, _ = make_server(**_handlers(**_logs(build_log="Mock Version: 6.8", root_log=root)))
    text = await call_text(server, "koji_diagnose_failed_build", task_id=101)
    assert "- **Likely cause**: unresolvable BuildRequires" in text
    assert "No match for argument: golang >= 1.27" in text


async def test_diagnose_by_task_id_skips_build_lookup(make_server):
    server, session = make_server(**_handlers())
    data = json.loads(
        await call_text(server, "koji_diagnose_failed_build", task_id=100, response_format="json")
    )
    assert not any(name == "getBuild" for name, _, _ in session.calls)
    assert data["task_id"] == 100
    assert data["failed_task"]["id"] == 101
    assert data["likely_cause"].startswith("the %build section failed")
    assert data["logs"]["build.log"]["content"].endswith("Child return code was: 1")


async def test_diagnose_completed_build_has_nothing_to_diagnose(make_server):
    server, session = make_server(**_handlers(getBuild=lambda info, strict=False: dict(FAILED_BUILD, state=1)))
    text = await call_text(server, "koji_diagnose_failed_build", build="x")
    assert "COMPLETE" in text and "nothing to diagnose" in text
    assert [name for name, _, _ in session.calls] == ["getBuild"]


async def test_diagnose_without_logs_reports_garbage_collection(make_server):
    server, _ = make_server(**_handlers(listTaskOutput=lambda tid, stat=False: {}))
    text = await call_text(server, "koji_diagnose_failed_build", build="x")
    assert "mock exited with status 1" in text
    assert "garbage-collected" in text


@pytest.mark.parametrize("args", [{}, {"build": "x", "task_id": 1}])
async def test_diagnose_needs_exactly_one_reference(make_server, args):
    server, _ = make_server(**_handlers())
    with pytest.raises(ToolError, match="exactly one of build or task_id"):
        await server.call_tool("koji_diagnose_failed_build", args)


BAD_BUILD = "error: Bad exit status from /var/tmp/rpm-tmp.0y (%build)"
BAD_CHECK = "error: Bad exit status from /var/tmp/rpm-tmp.0y (%check)"


@pytest.mark.parametrize(
    ("line", "bad_exit", "expected"),
    [
        (
            "go: go.mod requires go >= 1.27.3 (running go 1.26.10; GOTOOLCHAIN=local)",
            BAD_BUILD,
            "the %build section failed: the buildroot's Go is older than go.mod requires",
        ),
        (
            "internal/cli/run.go:42:7: undefined: bazel.Run",
            BAD_BUILD,
            "the %build section failed: Go compilation failed",
        ),
        (
            "ModuleNotFoundError: No module named 'pytest_asyncio'",
            BAD_CHECK,
            "the %check section failed (tests): a Python module is missing,"
            " likely a missing BuildRequires (python3dist(...))",
        ),
        (
            "ValueError: Extra test does not exist in upstream metadata. Available extras: [].",
            "error: Bad exit status from /var/tmp/rpm-tmp.0y (%generate_buildrequires)",
            "the %generate_buildrequires section failed: the spec asks for a Python extra"
            " upstream does not provide (check %pyproject_buildrequires -x)",
        ),
        (
            "error[E0425]: cannot find value `x` in this scope",
            BAD_BUILD,
            "the %build section failed: Rust compilation failed",
        ),
        (
            "error: no matching package named `serde` found",
            BAD_BUILD,
            "the %build section failed: a Rust crate is missing, likely a missing rust-*-devel BuildRequires",
        ),
        (
            "CMake Error at CMakeLists.txt:12 (project):",
            BAD_BUILD,
            "the %build section failed: CMake configuration failed",
        ),
        (
            "-- Could NOT find ZLIB (missing: ZLIB_LIBRARY ZLIB_INCLUDE_DIR)",
            BAD_BUILD,
            "the %build section failed: a library was not found, likely a missing -devel BuildRequires",
        ),
        (
            "src/main.c:3:10: fatal error: zlib.h: No such file or directory",
            BAD_BUILD,
            "the %build section failed: a C/C++ header is missing, likely a missing -devel BuildRequires",
        ),
    ],
)
def test_likely_cause_names_the_toolchain_error(line, bad_exit, expected):
    from koji_mcp.tools.diagnose import key_lines, likely_cause

    log = "\n".join(["+ make", line, "RPM build errors:", bad_exit])
    assert likely_cause({"build.log": log}) == f"{expected} (`{line.strip()}`)"
    assert line.strip() in key_lines(log)


def test_likely_cause_without_bad_exit_still_names_the_toolchain_error():
    from koji_mcp.tools.diagnose import likely_cause

    line = "CMake Error at CMakeLists.txt:12 (project):"
    assert likely_cause({"build.log": line}) == f"CMake configuration failed (`{line}`)"


def test_likely_cause_keeps_generic_section_failure():
    from koji_mcp.tools.diagnose import likely_cause

    assert likely_cause({"build.log": "make: *** [all] Error 2\n" + BAD_BUILD}) == "the %build section failed"


def test_key_lines_ignore_package_names_and_shell_trace():
    from koji_mcp.tools.diagnose import key_lines

    text = "\n".join(
        [
            "+ gcc -specs=/usr/lib/rpm/redhat/redhat-hardened-ld-errors -o x",
            "DEBUG util.py:537:  libgpg-error-1.58-2.fc44.aarch64",
            "make: *** [Makefile:12: all] Error 2",
            "error: Bad exit status from /var/tmp/rpm-tmp.0y (%build)",
            "FAIL: test_parse (tests.test_x)",
        ]
    )
    assert key_lines(text) == [
        "make: *** [Makefile:12: all] Error 2",
        "error: Bad exit status from /var/tmp/rpm-tmp.0y (%build)",
        "FAIL: test_parse (tests.test_x)",
    ]
