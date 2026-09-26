from __future__ import annotations

import asyncio
import os
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from ha_client import HomeAssistantClient, HomeAssistantError
from apto3d_files import list_component_files, read_component_file, write_component_file

mcp = FastMCP("home-assistant-chatgpt-connector", host="0.0.0.0", port=8000)


_HA_CLIENT: HomeAssistantClient | None = None
_ENTITY_CACHE: dict[str, list[str]] = {}
_ENTITY_CACHE_BY_ID: set[str] = set()
_ENTITY_CACHE_UPDATED = 0.0
_ENTITY_CACHE_TTL = 300.0


def client() -> HomeAssistantClient:
    global _HA_CLIENT
    if _HA_CLIENT is None:
        _HA_CLIENT = HomeAssistantClient()
    return _HA_CLIENT


def _normalize_entity_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.strip().lower())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(value.replace("_", " ").replace("-", " ").split())


async def _refresh_entity_cache(force: bool = False) -> None:
    global _ENTITY_CACHE, _ENTITY_CACHE_BY_ID, _ENTITY_CACHE_UPDATED
    if not force and _ENTITY_CACHE and time.monotonic() - _ENTITY_CACHE_UPDATED < _ENTITY_CACHE_TTL:
        return

    states = await client().list_states()
    cache: dict[str, list[str]] = {}
    ids: set[str] = set()
    for state in states:
        entity_id = str(state.get("entity_id") or "").strip().lower()
        if not entity_id:
            continue
        ids.add(entity_id)
        attributes = state.get("attributes") or {}
        friendly_name = str(attributes.get("friendly_name") or "").strip()
        object_id = entity_id.split(".", 1)[1] if "." in entity_id else entity_id
        keys = {entity_id, object_id}
        if friendly_name:
            keys.add(friendly_name)
        for key in keys:
            normalized = _normalize_entity_name(key)
            if normalized:
                cache.setdefault(normalized, []).append(entity_id)

    _ENTITY_CACHE = cache
    _ENTITY_CACHE_BY_ID = ids
    _ENTITY_CACHE_UPDATED = time.monotonic()


async def _resolve_entity(value: str, domain: str | None = None) -> str:
    raw = value.strip().lower()
    await _refresh_entity_cache()

    if raw in _ENTITY_CACHE_BY_ID:
        if domain and not raw.startswith(f"{domain}."):
            raise ValueError(f"Entity {raw!r} is not in domain {domain!r}.")
        return raw

    normalized = _normalize_entity_name(value)
    matches = list(dict.fromkeys(_ENTITY_CACHE.get(normalized, [])))
    if domain:
        matches = [item for item in matches if item.startswith(f"{domain}.")]

    if not matches:
        # One forced refresh handles newly created or renamed entities immediately.
        await _refresh_entity_cache(force=True)
        matches = list(dict.fromkeys(_ENTITY_CACHE.get(normalized, [])))
        if domain:
            matches = [item for item in matches if item.startswith(f"{domain}.")]

    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"No entity found for name {value!r}.")
    raise ValueError(
        f"Ambiguous entity name {value!r}; matches: {', '.join(matches)}"
    )


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


_CONFIG_ROOT = Path("/config").resolve()
_WRITABLE_ROOT = (_CONFIG_ROOT / "www").resolve()


def _resolve_config_path(path: str, *, write: bool = False) -> Path:
    raw = path.strip()
    if not raw:
        raise ValueError("path is required.")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = _CONFIG_ROOT / candidate
    resolved = candidate.resolve(strict=False)
    root = _WRITABLE_ROOT if write else _CONFIG_ROOT
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        scope = "/config/www/" if write else "/config/"
        raise ValueError(f"path must stay inside {scope}") from exc
    return resolved


@mcp.tool()
async def read_config_file(path: str, max_chars: int = 500000) -> dict[str, Any]:
    """Read a UTF-8 text file under /config. Binary files are rejected."""
    try:
        target = _resolve_config_path(path)
        max_chars = max(1, min(max_chars, 2_000_000))
        if not target.is_file():
            raise ValueError("file does not exist or is not a regular file.")
        data = target.read_bytes()
        if b"\\x00" in data:
            raise ValueError("binary files are not supported.")
        content = data.decode("utf-8")
        return {"ok": True, "path": str(target), "truncated": len(content) > max_chars, "content": content[:max_chars]}
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def write_config_file(path: str, content: str) -> dict[str, Any]:
    """Atomically write a UTF-8 text file under /config/www only.

    Parent traversal and writes outside /config/www are rejected.
    """
    try:
        target = _resolve_config_path(path, write=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".chatgpt.tmp")
        temp.write_text(content, encoding="utf-8")
        os.replace(temp, target)
        return {"ok": True, "path": str(target), "bytes": len(content.encode("utf-8"))}
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def read_apto3d_component_file(path: str, max_chars: int = 500000) -> dict[str, Any]:
    """Read an allowed UTF-8 file only from /config/custom_components/apto3d/."""
    try:
        return {"ok": True, **read_component_file(path, max_chars=max_chars)}
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def write_apto3d_component_file(path: str, content: str) -> dict[str, Any]:
    """Atomically write an allowed file only inside /config/custom_components/apto3d/."""
    try:
        return {"ok": True, **write_component_file(path, content)}
    except (OSError, UnicodeEncodeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def list_apto3d_component_files() -> dict[str, Any]:
    """List allowed regular files inside the APTO3D custom component directory."""
    try:
        files = list_component_files()
        return {"ok": True, "count": len(files), "files": files}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


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
    entity_name: str | None = None,
) -> dict[str, Any]:
    """Call any Home Assistant service.

    Prefer entity_id when known. Otherwise pass entity_name using the Home Assistant
    friendly name; it is resolved from the in-memory entity cache without requiring
    a list_entities call first.

    Examples:
      light.turn_on + entity_name="abajur escritorio"
      light.turn_on + light.luz_da_sala + {"brightness_pct": 50}
      switch.turn_off + switch.impressora_3d_socket_1
      script.turn_on + script.aspirar_sala
    """
    try:
        clean_domain = _clean_identifier(domain, "domain")
        clean_service = _clean_identifier(service, "service")
        if entity_id and entity_name:
            raise ValueError("Use entity_id or entity_name, not both.")
        if entity_name:
            clean_entity_id = await _resolve_entity(entity_name, clean_domain)
        else:
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
    print("ChatGPT Connector 0.4.0 starting...", flush=True)
    try:
        ha = client()
        info = await ha.check_api()
        states = await ha.list_states()
        await _refresh_entity_cache(force=True)
        print(
            f"Home Assistant API: OK ({info.get('message', 'authenticated')})",
            flush=True,
        )
        print(f"Entities accessible: {len(states)}", flush=True)
        print(f"Entity name cache: {len(_ENTITY_CACHE)} names/IDs cached", flush=True)
        print(
            "MCP tools: read_config_file, write_config_file, read_apto3d_component_file, "
            "write_apto3d_component_file, list_apto3d_component_files, ha_health, list_entities, get_entity_state, list_services, "
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
