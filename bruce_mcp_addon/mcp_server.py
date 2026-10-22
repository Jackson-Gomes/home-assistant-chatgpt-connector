"""Independent MCP server for Bruce on LilyGO T-Embed CC1101.

Inspired by chatgpt_connector/server.py's FastMCP/Tunnel architecture.
This process never contacts Home Assistant and has no HA credentials.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from mqtt_transport import BruceTransport, BruceTransportError

mcp = FastMCP("bruce-chatgpt-connector", host="127.0.0.1", port=8000)
_bridge: BruceTransport | None = None

def bridge() -> BruceTransport:
    global _bridge
    if _bridge is None:
        _bridge = BruceTransport()
    return _bridge

async def command(op: str, args: dict[str, Any] | None = None, *, timeout: float = 12) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(bridge().command, op, args, timeout)
    except (BruceTransportError, OSError, ValueError) as exc:
        return {"ok": False, "op": op, "error": str(exc)}

def alias(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", value):
        raise ValueError("Alias must be 1–32 letters, numbers, hyphens or underscores")
    return value

@mcp.tool()
async def bruce_status() -> dict[str, Any]:
    """Get the MQTT transport state and last reported online/offline presence."""
    try:
        return {"ok": True, **bridge().get_status()}
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}

@mcp.tool()
async def bruce_info() -> dict[str, Any]:
    """Read T-Embed Wi-Fi RSSI, uptime, RAM, PSRAM, brightness and SD status."""
    return await command("info.get")

@mcp.tool()
async def bruce_capabilities() -> dict[str, Any]:
    """List operations actually supported by the installed Bruce firmware."""
    return await command("remote.capabilities")

@mcp.tool()
async def bruce_wifi_scan() -> dict[str, Any]:
    """Passively scan nearby Wi-Fi access points and return firmware-reported network identifiers, channels and signal strengths. Does not connect, deauthenticate or transmit attack traffic."""
    return await command("wifi.scan", timeout=22)

@mcp.tool()
async def bruce_ble_scan() -> dict[str, Any]:
    """Passively discover nearby BLE advertisers with firmware-reported identifiers and RSSI. Does not pair with, connect to or modify devices."""
    return await command("ble.scan", timeout=22)

@mcp.tool()
async def bruce_unlock() -> dict[str, Any]:
    """Grant 5 minutes of sensitive control, only while the owner holds the T-Embed encoder button."""
    return await command("remote.unlock")

@mcp.tool()
async def bruce_lock() -> dict[str, Any]:
    """Immediately revoke temporary sensitive-device controls."""
    return await command("remote.lock")

@mcp.tool()
async def bruce_brightness(percent: int) -> dict[str, Any]:
    """Set the screen brightness from 1 to 100 percent."""
    if percent < 1 or percent > 100:
        return {"ok": False, "error": "Brightness must be 1..100"}
    return await command("screen.brightness", {"percent": percent})

@mcp.tool()
async def bruce_screen_color(hex_rgb: str) -> dict[str, Any]:
    """Set the screen color using six hex digits (e.g. FF9900)."""
    if not re.fullmatch(r"[0-9a-fA-F]{6}", hex_rgb):
        return {"ok": False, "error": "Color must be 6 hex digits"}
    return await command("screen.color", {"hex": hex_rgb.upper()})

@mcp.tool()
async def bruce_tone(hz: int, milliseconds: int) -> dict[str, Any]:
    """Play a short sound (100–4000 Hz, 30–1500 ms)."""
    if not (100 <= hz <= 4000 and 30 <= milliseconds <= 1500):
        return {"ok": False, "error": "Frequency or duration outside device limits"}
    return await command("sound.tone", {"hz": hz, "ms": milliseconds})

@mcp.tool()
async def bruce_say(text: str) -> dict[str, Any]:
    """Speak a short phrase on the T-Embed, max 96 chars."""
    if not text or len(text) > 96 or any(x in text for x in ("\n", "\r", '"', "\\")):
        return {"ok": False, "error": "Invalid phrase; max 96 chars; no newline, quote, backslash"}
    return await command("sound.say", {"text": text})

@mcp.tool()
async def bruce_navigate(button: str) -> dict[str, Any]:
    """Navigate Bruce's menu after physical unlock. Values: next, prev, up, down, select, esc, nextpage, prevpage."""
    allowed = {"next", "prev", "up", "down", "select", "esc", "nextpage", "prevpage"}
    if button not in allowed:
        return {"ok": False, "error": f"Button must be one of {sorted(allowed)}"}
    return await command("menu.nav", {"button": button})

@mcp.tool()
async def bruce_menu_option(index: int) -> dict[str, Any]:
    """Select a Bruce menu option by index after physical unlock."""
    if not 0 <= index <= 100:
        return {"ok": False, "error": "Menu index must be 0..100"}
    return await command("menu.option", {"index": index})

@mcp.tool()
async def bruce_ir_send_saved(name: str) -> dict[str, Any]:
    """Send a user-preapproved IR alias to an owned appliance. Requires physical unlock."""
    try:
        return await command("ir.send_saved", {"name": alias(name)})
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

@mcp.tool()
async def bruce_rf_send_saved(name: str) -> dict[str, Any]:
    """Transmit a locally-preapproved fixed RF code to an owned device. Requires physical unlock."""
    try:
        return await command("rf.send_saved", {"name": alias(name)})
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

@mcp.tool()
async def bruce_ir_receive() -> dict[str, Any]:
    """Passively read a short IR signal, only when physically unlocked and supported by firmware v0.2."""
    return await command("ir.receive", timeout=22)

@mcp.tool()
async def bruce_rf_receive(frequency_hz: int) -> dict[str, Any]:
    """Passively read a Sub-GHz signal, only when unlocked, at one allowed MHz-band center frequency."""
    if frequency_hz not in (315000000, 433920000, 868350000, 915000000):
        return {"ok": False, "error": "Unsupported RF frequency"}
    return await command("rf.receive", {"frequency": frequency_hz}, timeout=22)

@mcp.tool()
async def bruce_rf_scan(frequency_hz: int) -> dict[str, Any]:
    """Perform a short passive Sub-GHz scan from an allowlisted starting frequency; requires unlock."""
    if frequency_hz not in (315000000, 433920000, 868350000, 915000000):
        return {"ok": False, "error": "Unsupported RF frequency"}
    return await command("rf.scan", {"frequency": frequency_hz}, timeout=22)

@mcp.tool()
async def bruce_screen_snapshot() -> dict[str, Any]:
    """Return the bounded TFT drawing-command snapshot from firmware v0.2; NOT pixel-perfect image."""
    return await command("screen.snapshot", timeout=12)

@mcp.tool()
async def bruce_ota_status() -> dict[str, Any]:
    """Inspect whether dual-slot OTA is configured and the inactive partition is available."""
    return await command("ota.status")

@mcp.tool()
async def bruce_ota_apply(sha256: str, confirm: bool = False) -> dict[str, Any]:
    """Update ONLY from locally-pinned HTTPS URL; requires SHA-256 and physical button hold."""
    if not confirm:
        return {"ok": False, "error": "Explicit confirm=true required for OTA"}
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        return {"ok": False, "error": "SHA-256 must be 64 lowercase hexadecimal characters"}
    return await command("ota.apply", {"sha256": sha256}, timeout=12)

if __name__ == "__main__":
    # Must be hosted behind separately authorized OpenAI Secure MCP Tunnel.
    # Never forward this unauthenticated localhost endpoint directly to the Internet.
    mcp.run(transport="streamable-http")
