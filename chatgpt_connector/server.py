from __future__ import annotations
import asyncio
import sys
from typing import Any
from mcp.server.fastmcp import FastMCP
from ha_client import HomeAssistantClient, HomeAssistantError

mcp = FastMCP(
    "home-assistant-chatgpt-connector",
    host="0.0.0.0",
    port=8000,
)

def client() -> HomeAssistantClient:
    return HomeAssistantClient()

@mcp.tool()
async def ha_health() -> dict[str, Any]:
    """Check authentication and connectivity with Home Assistant."""
    try:
        return {"ok": True, "home_assistant": await client().check_api()}
    except HomeAssistantError as exc:
        return {"ok": False, "error": str(exc)}

@mcp.tool()
async def list_entities(domain: str | None = None) -> list[dict[str, Any]]:
    """List entities, optionally filtering by domain such as light or sensor."""
    states = await client().list_states()
    if domain:
        prefix = f"{domain.strip().lower()}."
        states = [s for s in states if s.get("entity_id", "").startswith(prefix)]
    return [
        {
            "entity_id": s.get("entity_id"),
            "state": s.get("state"),
            "friendly_name": s.get("attributes", {}).get("friendly_name"),
        }
        for s in states
    ]

@mcp.tool()
async def get_entity_state(entity_id: str) -> dict[str, Any]:
    """Read state and attributes of one Home Assistant entity."""
    item = await client().get_state(entity_id.strip())
    return {
        "entity_id": item.get("entity_id"),
        "state": item.get("state"),
        "attributes": item.get("attributes", {}),
        "last_changed": item.get("last_changed"),
        "last_updated": item.get("last_updated"),
    }

async def startup_check() -> bool:
    print("ChatGPT Connector 0.1.3 starting...", flush=True)
    try:
        ha = client()
        info = await ha.check_api()
        states = await ha.list_states()
        print(f"Home Assistant API: OK ({info.get('message', 'authenticated')})", flush=True)
        print(f"Entities accessible: {len(states)}", flush=True)
        return True
    except Exception as exc:
        print(f"Home Assistant API: ERROR - {exc}", file=sys.stderr, flush=True)
        return False

if __name__ == "__main__":
    if not asyncio.run(startup_check()):
        raise SystemExit(1)
    print("MCP server: listening on 0.0.0.0:8000 (SSE transport)", flush=True)
    print("MCP endpoint: /sse", flush=True)
    mcp.run(transport="sse")
