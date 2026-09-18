from __future__ import annotations

import asyncio
import sys
from typing import Any

from ha_client import HomeAssistantError
from server import _clean_identifier, client, mcp


@mcp.tool()
async def admin_health() -> dict[str, Any]:
    """Check both Home Assistant Core and Supervisor API access."""
    try:
        ha = client()
        core = await ha.check_api()
        supervisor = await ha.supervisor_info()
        return {"ok": True, "core": core, "supervisor": supervisor}
    except HomeAssistantError as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def list_devices() -> dict[str, Any]:
    """List Home Assistant device registry entries."""
    try:
        devices = await client().list_devices()
        return {"ok": True, "count": len(devices or []), "devices": devices or []}
    except HomeAssistantError as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def list_integrations(domain: str | None = None) -> dict[str, Any]:
    """List Home Assistant config entries/integrations, optionally filtered by domain."""
    try:
        entries = await client().list_integrations()
        entries = entries or []
        if domain:
            clean_domain = _clean_identifier(domain, "domain")
            entries = [item for item in entries if item.get("domain") == clean_domain]
        return {"ok": True, "count": len(entries), "integrations": entries}
    except (HomeAssistantError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def list_addons(installed_only: bool = True) -> dict[str, Any]:
    """List Supervisor apps/add-ons and their installed/update state."""
    try:
        addons = await client().list_addons()
        if installed_only:
            addons = [item for item in addons if item.get("installed")]
        return {"ok": True, "count": len(addons), "addons": addons}
    except HomeAssistantError as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def get_addon_info(addon_slug: str) -> dict[str, Any]:
    """Get detailed Supervisor information for one installed app/add-on."""
    try:
        clean_slug = _clean_identifier(addon_slug, "addon_slug", allow_hyphen=True)
        info = await client().get_addon_info(clean_slug)
        return {"ok": True, "addon_slug": clean_slug, "info": info}
    except (HomeAssistantError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def get_hardware_info() -> dict[str, Any]:
    """List hardware visible to Home Assistant OS/Supervisor, including USB/serial devices."""
    try:
        return {"ok": True, "hardware": await client().get_hardware_info()}
    except HomeAssistantError as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def get_host_info() -> dict[str, Any]:
    """Get Home Assistant host details such as disk, kernel and platform information."""
    try:
        return {"ok": True, "host": await client().get_host_info()}
    except HomeAssistantError as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def list_backups() -> dict[str, Any]:
    """List Home Assistant full and partial backups."""
    try:
        backups = await client().list_backups()
        return {"ok": True, "count": len(backups), "backups": backups}
    except HomeAssistantError as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def create_full_backup(
    confirm: bool,
    name: str | None = None,
    compressed: bool = True,
    exclude_database: bool = False,
) -> dict[str, Any]:
    """Create a full Home Assistant backup.

    Safety: this tool refuses to run unless confirm=true is explicitly supplied.
    Backups run in the background; use get_admin_job with the returned job_id.
    """
    if confirm is not True:
        return {
            "ok": False,
            "confirmation_required": True,
            "error": "Set confirm=true to create the full backup.",
        }

    try:
        result = await client().create_full_backup(
            name=name,
            compressed=compressed,
            exclude_database=exclude_database,
        )
        return {"ok": True, "result": result}
    except HomeAssistantError as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def get_admin_job(job_id: str) -> dict[str, Any]:
    """Read Supervisor job status, for example a background backup job."""
    try:
        clean_job_id = _clean_identifier(job_id, "job_id", allow_hyphen=True)
        return {"ok": True, "job": await client().get_job(clean_job_id)}
    except (HomeAssistantError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def get_admin_logs(
    source: str = "core",
    addon_slug: str | None = None,
    max_lines: int = 200,
) -> dict[str, Any]:
    """Read recent Core, Supervisor, host or add-on logs.

    source: core | supervisor | host | addon
    addon_slug is required when source=addon.
    """
    try:
        clean_source = source.strip().lower()
        clean_slug = None
        if addon_slug is not None:
            clean_slug = _clean_identifier(
                addon_slug, "addon_slug", allow_hyphen=True
            )
        max_lines = max(20, min(int(max_lines), 2000))
        text = await client().get_admin_logs(
            clean_source,
            addon_slug=clean_slug,
            max_lines=max_lines,
        )
        max_chars = 120000
        return {
            "ok": True,
            "source": clean_source,
            "addon_slug": clean_slug,
            "truncated": len(text) > max_chars,
            "log": text[-max_chars:],
        }
    except (HomeAssistantError, ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}


async def startup_check() -> bool:
    print("ChatGPT Connector 0.4.0 (Admin API v2) starting...", flush=True)
    try:
        ha = client()
        core = await ha.check_api()
        supervisor = await ha.supervisor_info()
        states = await ha.list_states()
        print(
            f"Home Assistant Core API: OK ({core.get('message', 'authenticated')})",
            flush=True,
        )
        print(
            f"Supervisor API: OK ({supervisor.get('operating_system', 'connected')})",
            flush=True,
        )
        print(f"Entities accessible: {len(states)}", flush=True)
        print(
            "Admin v2 tools enabled: admin_health, list_devices, list_integrations, "
            "list_addons, get_addon_info, get_hardware_info, get_host_info, "
            "list_backups, create_full_backup, get_admin_job, get_admin_logs",
            flush=True,
        )
        return True
    except Exception as exc:
        print(f"Admin API startup check: ERROR - {exc}", file=sys.stderr, flush=True)
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
