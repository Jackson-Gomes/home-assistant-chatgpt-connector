"""Compatibility shim for the connector while migrating from MCP Python SDK 1.x to 2.x.

The existing connector imports mcp.server.fastmcp.FastMCP. MCP SDK 2.x renamed
that class to MCPServer and moved host/port from the constructor to run(). This
shim preserves the existing server module unchanged while attaching the MCP
Events extension required by ChatGPT.
"""
from __future__ import annotations

import importlib
import json
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


def _capture_result(result: Any) -> dict[str, Any]:
    """Capture the logical MCP result immediately before transport serialization."""
    if isinstance(result, dict):
        payload: Any = dict(result)
    elif hasattr(result, "model_dump"):
        payload = result.model_dump(by_alias=True, exclude_none=True)
    else:
        payload = {"repr": repr(result)[:8000]}

    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=False,
        )
    except Exception as exc:
        serialized = f"<json serialization failed: {type(exc).__name__}: {exc}>"

    return {
        "captured_at": _utc_iso(),
        "python_type": type(result).__name__,
        "payload": payload,
        "json": serialized,
    }


def _empty_capture() -> dict[str, Any]:
    return {
        "captured_at": None,
        "transport": None,
        "python_type": None,
        "payload": None,
        "json": None,
    }


def _capture_wire_message(runtime: Any, msg: Any, transport: str) -> None:
    """Capture the exact JSON-RPC body produced by the SDK transport serializer."""
    try:
        body = msg.model_dump(mode="json", by_alias=True, exclude_none=True)
        # Match mcp.server._streamable_http_modern._write for JSON-RPC errors
        # whose request id could not be parsed. Success responses are unchanged.
        if getattr(msg, "id", object()) is None and hasattr(msg, "error"):
            body["id"] = None
        serialized = json.dumps(body, separators=(",", ":"))
    except Exception as exc:
        failed = {
            "captured_at": _utc_iso(),
            "transport": transport,
            "python_type": type(msg).__name__,
            "payload": {"repr": repr(msg)[:8000]},
            "json": f"<wire serialization capture failed: {type(exc).__name__}: {exc}>",
        }
        runtime._wire_last = failed
        return

    capture = {
        "captured_at": _utc_iso(),
        "transport": transport,
        "python_type": type(msg).__name__,
        "payload": body,
        "json": serialized,
    }
    runtime._wire_last = capture

    result = body.get("result") if isinstance(body, dict) else None
    if not isinstance(result, dict):
        return

    # Keep dedicated snapshots so later diagnostic tool calls cannot overwrite
    # the evidence from discovery/list responses.
    if "capabilities" in result and "supportedVersions" in result:
        runtime._wire_discovery = capture
    elif "events" in result:
        runtime._wire_events_list = capture


def _install_wire_capture(runtime: Any) -> None:
    """Instrument the SDK's final modern HTTP/SSE serialization boundary."""
    try:
        modern = importlib.import_module("mcp.server._streamable_http_modern")
    except Exception as exc:
        runtime._wire_patch_error = f"import failed: {type(exc).__name__}: {exc}"
        return

    modern._ha_events_wire_runtime = runtime

    if not getattr(modern, "_ha_events_wire_capture_installed", False):
        original_write = modern._write
        original_sse_event = modern._sse_event

        async def _write_with_capture(msg, scope, receive, send):
            target = getattr(modern, "_ha_events_wire_runtime", None)
            if target is not None:
                _capture_wire_message(target, msg, "json")
            return await original_write(msg, scope, receive, send)

        def _sse_event_with_capture(msg):
            target = getattr(modern, "_ha_events_wire_runtime", None)
            if target is not None:
                _capture_wire_message(target, msg, "sse")
            return original_sse_event(msg)

        modern._write = _write_with_capture
        modern._sse_event = _sse_event_with_capture
        modern._ha_events_wire_capture_installed = True

    runtime._wire_patch_error = None


class _InstrumentedMCPEventsRuntime(MCPEventsRuntime):
    """MCP Events runtime with logical and wire-level discovery diagnostics."""

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
        self._discovery: dict[str, Any] = {
            "captured_at": None,
            "python_type": None,
            "payload": None,
            "json": None,
        }
        self._events_list: dict[str, Any] = {
            "captured_at": None,
            "python_type": None,
            "payload": None,
            "json": None,
        }
        self._wire_discovery: dict[str, Any] = _empty_capture()
        self._wire_events_list: dict[str, Any] = _empty_capture()
        self._wire_last: dict[str, Any] = _empty_capture()
        self._wire_patch_error: str | None = None

        _install_wire_capture(self)

        # Keep the existing public diagnostic tool, but enrich it without adding
        # another MCP tool or exposing callback URLs/secrets.
        base_status = self.store.status

        def status_with_rpc() -> dict[str, Any]:
            payload = base_status()
            payload["rpc"] = {method: dict(data) for method, data in self._rpc.items()}
            payload["discovery"] = dict(self._discovery)
            payload["events_list"] = dict(self._events_list)
            payload["wire"] = {
                "patch_error": self._wire_patch_error,
                "discovery": dict(self._wire_discovery),
                "events_list": dict(self._wire_events_list),
                "last_response": dict(self._wire_last),
            }
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

        if method == "server/discover":
            self._discovery = _capture_result(result)
        elif method == "events/list":
            self._events_list = _capture_result(result)

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

        kwargs.setdefault("version", "0.9.4-beta")
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
