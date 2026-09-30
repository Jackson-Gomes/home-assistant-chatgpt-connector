"""Compatibility shim for the connector while migrating from MCP Python SDK 1.x to 2.x.

The existing connector imports mcp.server.fastmcp.FastMCP. MCP SDK 2.x renamed
that class to MCPServer and moved host/port from the constructor to run(). This
shim preserves the existing server module unchanged while attaching the MCP
Events extension required by ChatGPT.
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from typing import Any

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver import MCPServer

from mcp_events import (
    EventsListParams,
    EventsSubscribeParams,
    EventsUnsubscribeParams,
    MCPEventsRuntime,
)

# MCP 2026-07-28 requests carry protocol-level _meta fields. The beta event
# parameter models validate the application fields strictly, but must tolerate
# those SDK-managed request metadata keys.
for _params_model in (EventsListParams, EventsSubscribeParams, EventsUnsubscribeParams):
    _params_model.model_config["extra"] = "ignore"
    _params_model.model_rebuild(force=True)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class _InstrumentedMCPEventsRuntime(MCPEventsRuntime):
    """MCP Events runtime with wire-level diagnostics for ChatGPT discovery/subscription."""

    _TRACKED_METHODS = (
        "server/discover",
        "events/list",
        "events/subscribe",
        "events/unsubscribe",
    )

    def __init__(self) -> None:
        super().__init__()
        self._rpc: dict[str, dict[str, Any]] = {
            method: {
                "calls": 0,
                "successes": 0,
                "failures": 0,
                "last_called_at": None,
                "last_success_at": None,
                "last_error_at": None,
                "last_error": None,
            }
            for method in self._TRACKED_METHODS
        }

        # Keep the existing public diagnostic tool, but enrich it without adding
        # another MCP tool or exposing callback URLs/secrets.
        base_status = self.store.status

        def status_with_rpc() -> dict[str, Any]:
            payload = base_status()
            payload["rpc"] = {method: dict(data) for method, data in self._rpc.items()}
            return payload

        self.store.status = status_with_rpc  # type: ignore[method-assign]

    async def capability_middleware(
        self,
        ctx: ServerRequestContext[Any, Any],
        call_next: CallNext,
    ) -> HandlerResult:
        method = str(ctx.method)
        diagnostic = self._rpc.get(method)
        if diagnostic is not None:
            diagnostic["calls"] += 1
            diagnostic["last_called_at"] = _utc_iso()

        try:
            result = await super().capability_middleware(ctx, call_next)
        except Exception as exc:
            if diagnostic is not None:
                diagnostic["failures"] += 1
                diagnostic["last_error_at"] = _utc_iso()
                diagnostic["last_error"] = f"{type(exc).__name__}: {exc}"
            raise

        if diagnostic is not None:
            diagnostic["successes"] += 1
            diagnostic["last_success_at"] = _utc_iso()
            diagnostic["last_error"] = None
        return result


class FastMCP(MCPServer):
    def __init__(
        self,
        name: str | None = None,
        *args,
        host: str | None = None,
        port: int | None = None,
        **kwargs,
    ):
        self._compat_host = host or "127.0.0.1"
        self._compat_port = int(port or 8000)
        runtime = _InstrumentedMCPEventsRuntime()
        self._mcp_events_runtime = runtime

        extensions = list(kwargs.pop("extensions", ()) or ())
        extensions.append(runtime.extension)
        middleware = list(kwargs.pop("middleware", ()) or ())
        middleware.append(runtime.capability_middleware)

        kwargs.setdefault("version", "0.9.1-beta")
        if kwargs.get("lifespan") is None:
            kwargs["lifespan"] = runtime.lifespan
        super().__init__(
            name,
            *args,
            extensions=extensions,
            middleware=middleware,
            **kwargs,
        )

    def run(self, transport="stdio", **kwargs):
        if transport == "streamable-http":
            kwargs.setdefault("host", self._compat_host)
            kwargs.setdefault("port", self._compat_port)
        return super().run(transport=transport, **kwargs)


_module = types.ModuleType("mcp.server.fastmcp")
_module.FastMCP = FastMCP
sys.modules["mcp.server.fastmcp"] = _module

try:
    import mcp.server as _server_package

    _server_package.fastmcp = _module
except Exception:
    pass
