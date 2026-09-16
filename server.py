from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ha_client import HomeAssistantClient, HomeAssistantError

mcp = FastMCP("home-assistant-chatgpt-connector")


def client() -> HomeAssistantClient:
    return HomeAssistantClient()


@mcp.tool()
async def ha_health() -> dict[str, Any]:
    """Check whether the connector can authenticate with Home Assistant."""
    try:
        return {"ok": True, "home_assistant": await client().check_api()}
    except HomeAssistantError as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def list_entities(domain: str | None = None) -> list[dict[str, Any]]:
    """List Home Assistant entities, optionally filtering by domain such as light or sensor."""
    states = await client().list_states()
    if domain:
        prefix = f"{domain.strip().lower()}."
        states = [item for item in states if item.get("entity_id", "").startswith(prefix)]

    return [
        {
            "entity_id": item.get("entity_id"),
            "state": item.get("state"),
            "friendly_name": item.get("attributes", {}).get("friendly_name"),
        }
        for item in states
    ]


@mcp.tool()
async def get_entity_state(entity_id: str) -> dict[str, Any]:
    """Read the current state and attributes of one Home Assistant entity."""
    item = await client().get_state(entity_id.strip())
    return {
        "entity_id": item.get("entity_id"),
        "state": item.get("state"),
        "attributes": item.get("attributes", {}),
        "last_changed": item.get("last_changed"),
        "last_updated": item.get("last_updated"),
    }


if __name__ == "__main__":
    mcp.run()
