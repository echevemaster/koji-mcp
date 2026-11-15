"""Async wrapper around koji.ClientSession with actionable error messages."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Iterable

import koji
from mcp.server.mcpserver.exceptions import ToolError

DEFAULT_PROFILE = "koji"
FEDORA_OPTS = {
    "server": "https://koji.fedoraproject.org/kojihub",
    "weburl": "https://koji.fedoraproject.org/koji",
    "topurl": "https://kojipkgs.fedoraproject.org",
}

Call = tuple[str, tuple, dict]
SessionFactory = Callable[[dict], Any]


def _default_session_factory(opts: dict) -> Any:
    return koji.ClientSession(opts["server"], opts=koji.grab_session_options(opts))


class KojiClient:
    """Lazily connects to the hub of one koji profile.

    The session is created on first use and never logs in: every tool in this
    server uses anonymous, read-only hub calls.
    """

    def __init__(
        self,
        profile: str = DEFAULT_PROFILE,
        opts: dict | None = None,
        session_factory: SessionFactory = _default_session_factory,
    ) -> None:
        self.profile = profile
        self._opts = opts
        self._session_factory = session_factory
        self._session: Any = None

    @property
    def opts(self) -> dict:
        if self._opts is None:
            self._opts = self._load_profile()
        return self._opts

    def _load_profile(self) -> dict:
        try:
            return dict(koji.read_config(self.profile))
        except koji.ConfigurationError:
            # Systems without koji config files (macOS, non-Fedora distros)
            # still get Fedora's hub for the default profile.
            if self.profile == DEFAULT_PROFILE:
                return dict(FEDORA_OPTS)
            raise ToolError(
                f"No koji configuration for profile {self.profile!r}. Create "
                f"~/.koji/config.d/{self.profile}.conf with a [{self.profile}] section "
                "setting server, weburl and topurl."
            ) from None

    @property
    def server(self) -> str:
        return self.opts["server"]

    @property
    def weburl(self) -> str | None:
        return self.opts.get("weburl")

    @property
    def topurl(self) -> str | None:
        return self.opts.get("topurl")

    @property
    def session(self) -> Any:
        if self._session is None:
            self._session = self._session_factory(self.opts)
        return self._session

    async def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Run one hub call in a worker thread."""
        return await asyncio.to_thread(self._call, method, args, kwargs)

    async def multicall(self, calls: Iterable[Call]) -> list[Any]:
        """Run several hub calls in one round-trip; results keep call order."""
        return await asyncio.to_thread(self._multicall, list(calls))

    def _call(self, method: str, args: tuple, kwargs: dict) -> Any:
        try:
            return getattr(self.session, method)(*args, **kwargs)
        except Exception as exc:  # translated below, never swallowed
            raise self._translate(exc, method) from exc

    def _multicall(self, calls: list[Call]) -> list[Any]:
        names = ", ".join(name for name, _, _ in calls)
        try:
            with self.session.multicall(strict=True) as mc:
                pending = [getattr(mc, name)(*args, **kwargs) for name, args, kwargs in calls]
            return [p.result for p in pending]
        except Exception as exc:
            raise self._translate(exc, names) from exc

    def _translate(self, exc: Exception, method: str) -> ToolError:
        if isinstance(exc, ToolError):
            return exc
        if isinstance(exc, koji.ParameterError):
            return ToolError(
                f"Koji rejected invalid parameters for {method}: {exc}. "
                "Check the argument names and types in the tool description."
            )
        if isinstance(exc, koji.GenericError):
            return ToolError(
                f"Koji hub error in {method}: {exc}. If a name or ID is wrong, "
                "use koji_search to find the exact build, package or tag name."
            )
        if isinstance(exc, OSError):  # includes requests.RequestException
            return ToolError(
                f"Could not reach the Koji hub at {self.server} ({type(exc).__name__}: {exc}). "
                "Check the network and the server URL of the koji profile."
            )
        return ToolError(f"Unexpected error in {method}: {type(exc).__name__}: {exc}")
