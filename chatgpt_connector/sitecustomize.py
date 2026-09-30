"""Compatibility shim for the connector while migrating from MCP Python SDK 1.x to 2.x.

The existing connector imports mcp.server.fastmcp.FastMCP. MCP SDK 2.x renamed
that class to MCPServer and moved host/port from the constructor to run(). This
shim preserves the existing server module unchanged while attaching the MCP
Events extension required by ChatGPT.
"""
from __future__ import annotations

import sys
import types

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
        runtime = MCPEventsRuntime()
        self._mcp_events_runtime = runtime

        extensions = list(kwargs.pop("extensions", ()) or ())
        extensions.append(runtime.extension)
        middleware = list(kwargs.pop("middleware", ()) or ())
        middleware.append(runtime.capability_middleware)

        kwargs.setdefault("version", "0.9.0-beta")
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
