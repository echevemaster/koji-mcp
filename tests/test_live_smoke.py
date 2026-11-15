"""Read-only smoke checks against the real Fedora Koji hub.

Skipped unless KOJI_MCP_LIVE=1. The assertions use stable facts only.
"""

import json
import os

import pytest
from conftest import call_text

from koji_mcp.client import KojiClient
from koji_mcp.server import create_server

pytestmark = pytest.mark.skipif(os.environ.get("KOJI_MCP_LIVE") != "1", reason="set KOJI_MCP_LIVE=1")

FEDORA = {
    "server": "https://koji.fedoraproject.org/kojihub",
    "weburl": "https://koji.fedoraproject.org/koji",
    "topurl": "https://kojipkgs.fedoraproject.org",
}


@pytest.fixture
def server():
    return create_server(KojiClient(profile="fedora", opts=FEDORA))


async def test_live_get_build(server):
    data = json.loads(await call_text(server, "koji_get_build", build="bash-5.2.32-1.fc41", response_format="json"))
    assert data["id"] == 2525453
    assert data["state"] == "COMPLETE"
    assert data["completion_time"] == "2024-08-12T12:45:22+00:00"


async def test_live_search_and_inheritance(server):
    assert "| 1088 | bash |" in await call_text(
        server, "koji_search", terms="bash", search_type="package", match_type="exact"
    )
    assert "| f42-override | 1 |" in await call_text(server, "koji_tag_inheritance", tag="f42-build")


async def test_live_diagnose_old_failed_build_without_logs(server):
    text = await call_text(server, "koji_diagnose_failed_build", build="bash-5.2.37-2.fc42")
    assert "mock exited with status 1" in text
