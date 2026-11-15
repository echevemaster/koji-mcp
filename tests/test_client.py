import koji
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from koji_mcp.client import KojiClient


async def test_session_is_created_lazily_and_reused(make_client):
    created = []

    def factory(opts):
        created.append(opts)
        return object()

    client = KojiClient(profile="test", opts={"server": "https://x/kojihub"}, session_factory=factory)
    assert created == []
    first = client.session
    assert client.session is first
    assert len(created) == 1


async def test_call_forwards_args_and_returns_result(make_client):
    client, session = make_client(getBuild=lambda info, strict=False: {"id": 1, "nvr": info})
    result = await client.call("getBuild", "bash-5.2-1.fc41", strict=False)
    assert result == {"id": 1, "nvr": "bash-5.2-1.fc41"}
    assert session.calls == [("getBuild", ("bash-5.2-1.fc41",), {"strict": False})]


async def test_parameter_error_becomes_actionable_tool_error(make_client):
    def boom(*a, **k):
        raise koji.ParameterError("unexpected keyword argument 'bogus'")

    client, _ = make_client(listBuilds=boom)
    with pytest.raises(ToolError) as exc:
        await client.call("listBuilds", bogus=1)
    message = str(exc.value)
    assert "listBuilds" in message
    assert "invalid parameters" in message.lower()
    assert "bogus" in message


async def test_generic_error_includes_hub_message(make_client):
    def boom(*a, **k):
        raise koji.GenericError("No such tagInfo: 'f99'")

    client, _ = make_client(getTag=boom)
    with pytest.raises(ToolError) as exc:
        await client.call("getTag", "f99", strict=True)
    assert "No such tagInfo: 'f99'" in str(exc.value)
    assert "koji_search" in str(exc.value)


async def test_connection_error_names_the_hub(make_client):
    def boom(*a, **k):
        raise ConnectionError("refused")

    client, _ = make_client(getBuild=boom)
    with pytest.raises(ToolError) as exc:
        await client.call("getBuild", 1)
    assert "https://koji.example.org/kojihub" in str(exc.value)


async def test_multicall_returns_results_in_order(make_client):
    client, _ = make_client(
        getTaskInfo=lambda tid, request=False: {"id": tid},
        getTaskChildren=lambda tid, request=False: [{"id": tid + 1}],
    )
    info, children = await client.multicall(
        [("getTaskInfo", (10,), {"request": True}), ("getTaskChildren", (10,), {})]
    )
    assert info == {"id": 10}
    assert children == [{"id": 11}]


def test_web_and_top_urls_come_from_profile(make_client):
    client, _ = make_client()
    assert client.weburl == "https://koji.example.org/koji"
    assert client.topurl == "https://kojipkgs.example.org"


def _no_config(profile, *args, **kwargs):
    raise koji.ConfigurationError(f"no configuration for profile name: {profile}")


def test_default_profile_falls_back_to_fedora_when_unconfigured(monkeypatch):
    monkeypatch.setattr(koji, "read_config", _no_config)
    client = KojiClient()
    assert client.server == "https://koji.fedoraproject.org/kojihub"
    assert client.weburl == "https://koji.fedoraproject.org/koji"
    assert client.topurl == "https://kojipkgs.fedoraproject.org"


async def test_missing_named_profile_is_an_actionable_error(monkeypatch):
    monkeypatch.setattr(koji, "read_config", _no_config)
    client = KojiClient(profile="centos")
    with pytest.raises(ToolError) as exc:
        await client.call("getBuild", 1)
    assert "'centos'" in str(exc.value)
    assert "~/.koji/config.d/" in str(exc.value)


def test_configured_profile_is_used(monkeypatch):
    monkeypatch.setattr(koji, "read_config", lambda profile, *a, **k: {"server": f"https://{profile}/kojihub"})
    assert KojiClient(profile="stream").server == "https://stream/kojihub"
