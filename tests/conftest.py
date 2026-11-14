"""Test doubles for koji.ClientSession."""

from __future__ import annotations

import contextlib
from typing import Any, Callable

import pytest

from koji_mcp.client import KojiClient


class _VirtualCall:
    def __init__(self, value: Any) -> None:
        self.result = value


class _MultiCall:
    def __init__(self, session: "FakeSession") -> None:
        self._session = session
        self._pending: list[tuple[_VirtualCall, Callable[[], Any]]] = []

    def __getattr__(self, name: str) -> Callable[..., _VirtualCall]:
        def queue(*args: Any, **kwargs: Any) -> _VirtualCall:
            call = _VirtualCall(None)
            self._pending.append((call, lambda: getattr(self._session, name)(*args, **kwargs)))
            return call

        return queue

    def run(self) -> None:
        for call, thunk in self._pending:
            call.result = thunk()


class FakeSession:
    """Minimal stand-in for koji.ClientSession.

    Each hub method is a callable in ``handlers``; every call is recorded in
    ``calls`` as ``(method, args, kwargs)``.
    """

    def __init__(self, **handlers: Callable[..., Any]) -> None:
        self.handlers = handlers
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name not in self.handlers:
            raise AttributeError(name)

        def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            return self.handlers[name](*args, **kwargs)

        return call

    @contextlib.contextmanager
    def multicall(self, strict: bool = False):
        mc = _MultiCall(self)
        yield mc
        mc.run()


OPTS = {
    "server": "https://koji.example.org/kojihub",
    "weburl": "https://koji.example.org/koji",
    "topurl": "https://kojipkgs.example.org",
}


@pytest.fixture
def make_client() -> Callable[..., tuple[KojiClient, FakeSession]]:
    def factory(**handlers: Callable[..., Any]) -> tuple[KojiClient, FakeSession]:
        session = FakeSession(**handlers)
        client = KojiClient(profile="test", opts=OPTS, session_factory=lambda opts: session)
        return client, session

    return factory
