from __future__ import annotations

import asyncio
import sys
from typing import Any

from assist_admin import register_assist_tools
from brain_admin import register_brain_tools
from ha_client import HomeAssistantError
from printer_control import (
    cancel_job as cancel_printer_job_impl,
    get_capabilities as get_printer_capabilities_impl,
    identify as identify_printer_impl,
    queue_control as printer_queue_control_impl,
    queue_status as printer_queue_status_impl,
    wake as wake_printer_impl,
)
from server import _clean_identifier, client, mcp

register_assist_tools(mcp, client)
register_brain_tools(mcp)


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


@mcp.tool()
async def printer_capabilities() -> dict[str, Any]:
    """Probe the configured printer over IPP and return only capabilities it actually advertises."""
    try:
        return {"ok": True, **(await get_printer_capabilities_impl())}
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def printer_identify(
    action: str = "flash",
    message: str = "ChatGPT connected",
) -> dict[str, Any]:
    """Identify the printer without printing.

    action can be flash, display, sound, or speak. The command is sent only when
    the real printer advertises both Identify-Printer and the requested action.
    """
    try:
        return await identify_printer_impl(action=action, message=message)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def printer_wake() -> dict[str, Any]:
    """Wake/activate the printer only if it advertises a standard IPP wake operation."""
    try:
        return await wake_printer_impl()
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def printer_queue_status() -> dict[str, Any]:
    """Read the local CUPS queue state and pending ChatGPT printer jobs."""
    try:
        return await printer_queue_status_impl()
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def printer_queue_control(action: str) -> dict[str, Any]:
    """Control the local print queue. action: pause, resume, or cancel_all."""
    try:
        return await printer_queue_control_impl(action)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def printer_cancel_job(job_id: str) -> dict[str, Any]:
    """Cancel one pending job from the connector's local CUPS queue."""
    try:
        return await cancel_printer_job_impl(job_id)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}


async def startup_check() -> bool:
    print(
        "ChatGPT Connector 0.8.0 (Admin API v2 + Assist + Brain + IPP printer controls) starting...",
        flush=True,
    )
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
            "list_backups, create_full_backup, get_admin_job, get_admin_logs; "
            "Printer tools: printer_capabilities, printer_identify, printer_wake, "
            "printer_queue_status, printer_queue_control, printer_cancel_job; "
            "Assist tools: list_assist_pipelines, update_assist_pipeline, "
            "list_assist_exposed_entities, set_assist_entity_exposure, "
            "set_assist_exposed_entities; "
            "Brain tools: brain_status, brain_recent_events, brain_suggestions",
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
