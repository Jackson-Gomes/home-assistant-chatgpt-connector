from __future__ import annotations

import asyncio
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from ha_client import HomeAssistantClient, HomeAssistantError

mcp = FastMCP("home-assistant-chatgpt-connector", host="0.0.0.0", port=8000)


def client() -> HomeAssistantClient:
    return HomeAssistantClient()


def _clean_identifier(value: str, label: str) -> str:
    cleaned = value.strip().lower()
    if not cleaned:
        raise ValueError(f"{label} is required.")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_")
    if any(char not in allowed for char in cleaned):
        raise ValueError(f"Invalid {label}: {value!r}.")
    return cleaned


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


@mcp.tool()
async def call_service(
    domain: str,
    service: str,
    entity_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call any Home Assistant service.

    Examples:
      light.turn_on + light.luz_da_sala + {"brightness_pct": 50}
      switch.turn_off + switch.impressora_3d_socket_1
      script.turn_on + script.aspirar_sala
    """
    try:
        clean_domain = _clean_identifier(domain, "domain")
        clean_service = _clean_identifier(service, "service")
        clean_entity_id = entity_id.strip() if entity_id else None

        if clean_entity_id and "." not in clean_entity_id:
            raise ValueError("entity_id must be in domain.object_id format.")

        result = await client().call_service(
            clean_domain,
            clean_service,
            entity_id=clean_entity_id,
            data=data,
        )
        return {
            "ok": True,
            "domain": clean_domain,
            "service": clean_service,
            "entity_id": clean_entity_id,
            "result": result,
        }
    except (HomeAssistantError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


async def startup_check() -> bool:
    print("ChatGPT Connector 0.1.7 starting...", flush=True)
    try:
        ha = client()
        info = await ha.check_api()
        states = await ha.list_states()
        print(f"Home Assistant API: OK ({info.get('message', 'authenticated')})", flush=True)
        print(f"Entities accessible: {len(states)}", flush=True)
        print(
            "MCP tools: ha_health, list_entities, get_entity_state, call_service",
            flush=True,
        )
        return True
    except Exception as exc:
        print(f"Home Assistant API: ERROR - {exc}", file=sys.stderr, flush=True)
        return False


if __name__ == "__main__":
    if not asyncio.run(startup_check()):
        raise SystemExit(1)
    print("MCP server: listening on 0.0.0.0:8000 (Streamable HTTP)", flush=True)
    print("MCP endpoint: /mcp", flush=True)
    mcp.run(transport="streamable-http")
