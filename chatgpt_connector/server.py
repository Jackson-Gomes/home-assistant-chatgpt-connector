from __future__ import annotations

import asyncio
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from ha_client import HomeAssistantClient, HomeAssistantError

mcp = FastMCP("home-assistant-chatgpt-connector", host="0.0.0.0", port=8000)


def client() -> HomeAssistantClient:
    return HomeAssistantClient()


def _clean_identifier(value: str, label: str, *, allow_hyphen: bool = False) -> str:
    cleaned = value.strip().lower()
    if not cleaned:
        raise ValueError(f"{label} is required.")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_")
    if allow_hyphen:
        allowed.add("-")
    if any(char not in allowed for char in cleaned):
        raise ValueError(f"Invalid {label}: {value!r}.")
    return cleaned


def _clean_entity_id(value: str) -> str:
    cleaned = value.strip().lower()
    if "." not in cleaned:
        raise ValueError("entity_id must be in domain.object_id format.")
    domain, object_id = cleaned.split(".", 1)
    _clean_identifier(domain, "entity domain")
    _clean_identifier(object_id, "entity object_id")
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
    try:
        item = await client().get_state(_clean_entity_id(entity_id))
        return {
            "ok": True,
            "entity_id": item.get("entity_id"),
            "state": item.get("state"),
            "attributes": item.get("attributes", {}),
            "last_changed": item.get("last_changed"),
            "last_updated": item.get("last_updated"),
        }
    except (HomeAssistantError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def list_services(domain: str | None = None) -> dict[str, Any]:
    """List Home Assistant services and their schemas, optionally by domain."""
    try:
        services = await client().list_services()
        if domain:
            clean_domain = _clean_identifier(domain, "domain")
            services = [
                item for item in services if item.get("domain") == clean_domain
            ]
        return {"ok": True, "services": services}
    except (HomeAssistantError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def get_history(
    entity_id: str,
    start_time: str | None = None,
    end_time: str | None = None,
    minimal_response: bool = True,
) -> dict[str, Any]:
    """Get state history for one entity.

    start_time/end_time accept ISO-8601 timestamps. If start_time is omitted,
    Home Assistant defaults to roughly the previous day.
    """
    try:
        clean_entity_id = _clean_entity_id(entity_id)
        result = await client().get_history(
            clean_entity_id,
            start_time=start_time,
            end_time=end_time,
            minimal_response=minimal_response,
        )
        return {"ok": True, "entity_id": clean_entity_id, "history": result}
    except (HomeAssistantError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def get_logbook(
    start_time: str | None = None,
    end_time: str | None = None,
    entity_id: str | None = None,
) -> dict[str, Any]:
    """Get Home Assistant logbook entries, optionally filtered by entity and time."""
    try:
        clean_entity_id = _clean_entity_id(entity_id) if entity_id else None
        result = await client().get_logbook(
            start_time=start_time,
            end_time=end_time,
            entity_id=clean_entity_id,
        )
        return {"ok": True, "entries": result}
    except (HomeAssistantError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def get_error_log(max_chars: int = 20000) -> dict[str, Any]:
    """Read the Home Assistant error log from the current session."""
    try:
        max_chars = max(1000, min(max_chars, 100000))
        text = await client().get_error_log()
        return {
            "ok": True,
            "truncated": len(text) > max_chars,
            "log": text[-max_chars:],
        }
    except HomeAssistantError as exc:
        return {"ok": False, "error": str(exc)}


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
        clean_entity_id = _clean_entity_id(entity_id) if entity_id else None

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


@mcp.tool()
async def get_automation_config(automation_id: str) -> dict[str, Any]:
    """Read the persistent Home Assistant automation configuration by automation ID."""
    try:
        clean_id = _clean_identifier(automation_id, "automation_id", allow_hyphen=True)
        config = await client().get_automation_config(clean_id)
        return {"ok": True, "automation_id": clean_id, "config": config}
    except (HomeAssistantError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def save_automation(
    automation_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Create or replace a Home Assistant automation persistently.

    Use get_automation_config first when editing an existing automation.
    The config must use Home Assistant's automation schema.
    """
    try:
        clean_id = _clean_identifier(automation_id, "automation_id", allow_hyphen=True)
        result = await client().save_automation_config(clean_id, config)
        return {
            "ok": True,
            "automation_id": clean_id,
            "result": result,
        }
    except (HomeAssistantError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def get_script_config(script_id: str) -> dict[str, Any]:
    """Read the persistent Home Assistant script configuration by script ID."""
    try:
        clean_id = _clean_identifier(script_id, "script_id", allow_hyphen=True)
        config = await client().get_script_config(clean_id)
        return {"ok": True, "script_id": clean_id, "config": config}
    except (HomeAssistantError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def save_script(
    script_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Create or replace a Home Assistant script persistently.

    Use get_script_config first when editing an existing script.
    The config must use Home Assistant's script schema.
    """
    try:
        clean_id = _clean_identifier(script_id, "script_id", allow_hyphen=True)
        result = await client().save_script_config(clean_id, config)
        return {"ok": True, "script_id": clean_id, "result": result}
    except (HomeAssistantError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def update_entity(
    entity_id: str,
    name: str | None = None,
    new_entity_id: str | None = None,
    icon: str | None = None,
    area_id: str | None = None,
) -> dict[str, Any]:
    """Update an entity registry entry.

    Supports friendly registry name, entity_id rename, icon and area assignment.
    Only supplied values are changed.
    """
    try:
        clean_entity_id = _clean_entity_id(entity_id)
        changes: dict[str, Any] = {}
        if name is not None:
            changes["name"] = name
        if new_entity_id is not None:
            changes["new_entity_id"] = _clean_entity_id(new_entity_id)
        if icon is not None:
            changes["icon"] = icon
        if area_id is not None:
            changes["area_id"] = area_id

        result = await client().update_entity_registry(clean_entity_id, changes)
        return {
            "ok": True,
            "entity_id": clean_entity_id,
            "changes": changes,
            "result": result,
        }
    except (HomeAssistantError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def get_dashboard(url_path: str | None = None) -> dict[str, Any]:
    """Read a Lovelace dashboard config. Omit url_path for the default dashboard."""
    try:
        config = await client().get_dashboard(url_path=url_path)
        return {"ok": True, "url_path": url_path, "config": config}
    except HomeAssistantError as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def save_dashboard(
    config: dict[str, Any],
    url_path: str | None = None,
) -> dict[str, Any]:
    """Replace a storage-mode Lovelace dashboard configuration.

    Always call get_dashboard first and send the complete revised config.
    """
    try:
        result = await client().save_dashboard(config, url_path=url_path)
        return {"ok": True, "url_path": url_path, "result": result}
    except HomeAssistantError as exc:
        return {"ok": False, "error": str(exc)}


async def startup_check() -> bool:
    print("ChatGPT Connector 0.2.0 starting...", flush=True)
    try:
        ha = client()
        info = await ha.check_api()
        states = await ha.list_states()
        print(
            f"Home Assistant API: OK ({info.get('message', 'authenticated')})",
            flush=True,
        )
        print(f"Entities accessible: {len(states)}", flush=True)
        print(
            "MCP tools: ha_health, list_entities, get_entity_state, list_services, "
            "get_history, get_logbook, get_error_log, call_service, "
            "get_automation_config, save_automation, get_script_config, save_script, "
            "update_entity, get_dashboard, save_dashboard",
            flush=True,
        )
        return True
    except Exception as exc:
        print(f"Home Assistant API: ERROR - {exc}", file=sys.stderr, flush=True)
        return False


if __name__ == "__main__":
    if not asyncio.run(startup_check()):
        raise SystemExit(1)
    print(
        "MCP server: listening on 0.0.0.0:8000 (Streamable HTTP)",
        flush=True,
    )
    print("MCP endpoint: /mcp", flush=True)
    mcp.run(transport="streamable-http")
