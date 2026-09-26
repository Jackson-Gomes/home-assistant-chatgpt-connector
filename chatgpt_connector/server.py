from __future__ import annotations

import asyncio
import base64
import binascii
import os
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

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


_PRINT_ROOT = Path("/config/www").resolve()
_PRINT_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
_PRINT_MAX_BYTES = 25 * 1024 * 1024
_PRINT_MIME_EXTENSIONS = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}


class OpenAIFileInput(BaseModel):
    download_url: str
    file_id: str
    mime_type: str | None = None
    file_name: str | None = None


def _resolve_print_path(path: str) -> Path:
    """Resolve a printable file while keeping access inside /config/www."""
    target = _resolve_config_path(path)
    try:
        target.relative_to(_PRINT_ROOT)
    except ValueError as exc:
        raise ValueError("print file must stay inside /config/www/") from exc
    if target.suffix.lower() not in _PRINT_EXTENSIONS:
        raise ValueError("print file must be PDF, PNG, JPG, or JPEG.")
    if not target.is_file():
        raise ValueError("print file does not exist or is not a regular file.")
    size = target.stat().st_size
    if size <= 0 or size > _PRINT_MAX_BYTES:
        raise ValueError("print file must be between 1 byte and 25 MB.")
    return target


def _printer_uri() -> str:
    uri = os.environ.get("PRINTER_URI", "ipp://192.168.0.104/ipp/print").strip()
    if not (uri.startswith("ipp://") or uri.startswith("ipps://")):
        raise ValueError("PRINTER_URI must use ipp:// or ipps://.")
    return uri


@mcp.tool()
async def read_config_file(path: str, max_chars: int = 500000) -> dict[str, Any]:
    """Read a UTF-8 text file under /config. Binary files are rejected."""
    try:
        target = _resolve_config_path(path)
        max_chars = max(1, min(max_chars, 2_000_000))
        if not target.is_file():
            raise ValueError("file does not exist or is not a regular file.")
        data = target.read_bytes()
        if b"\x00" in data:
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


def _store_print_bytes(filename: str, data: bytes) -> Path:
    safe_name = Path(filename.strip()).name
    if not safe_name or safe_name in {".", ".."}:
        raise ValueError("filename is required.")
    suffix = Path(safe_name).suffix.lower()
    if suffix not in _PRINT_EXTENSIONS:
        raise ValueError("print file must be PDF, PNG, JPG, or JPEG.")
    if not data or len(data) > _PRINT_MAX_BYTES:
        raise ValueError("print file must be between 1 byte and 25 MB.")

    signatures = {
        ".pdf": lambda b: b.startswith(b"%PDF-"),
        ".png": lambda b: b.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": lambda b: b.startswith(b"\xff\xd8\xff"),
        ".jpeg": lambda b: b.startswith(b"\xff\xd8\xff"),
    }
    if not signatures[suffix](data):
        raise ValueError("file content does not match its declared PDF/PNG/JPEG type.")

    upload_dir = (_PRINT_ROOT / "chatgpt_print").resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)
    token = os.urandom(8).hex()
    target = upload_dir / f"{token}{suffix}"
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_bytes(data)
    os.replace(temp, target)
    return target


def _decode_print_upload(filename: str, content_base64: str) -> Path:
    """Decode one printable attachment into the connector-controlled print folder."""
    if len(content_base64) > ((_PRINT_MAX_BYTES * 4 // 3) + 4096):
        raise ValueError("encoded print file is too large.")
    try:
        data = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("content_base64 is not valid base64.") from exc
    return _store_print_bytes(filename, data)


def _attachment_filename(file: OpenAIFileInput) -> str:
    if file.file_name:
        safe_name = Path(file.file_name.strip()).name
        if Path(safe_name).suffix.lower() in _PRINT_EXTENSIONS:
            return safe_name

    mime = (file.mime_type or "").split(";", 1)[0].strip().lower()
    suffix = _PRINT_MIME_EXTENSIONS.get(mime)
    if suffix:
        return f"attachment{suffix}"
    raise ValueError("attached file must be PDF, PNG, JPG, or JPEG.")


async def _download_print_attachment(file: OpenAIFileInput) -> Path:
    """Download a ChatGPT-authorized file parameter and store it temporarily for printing."""
    url = file.download_url.strip()
    if not url.lower().startswith("https://"):
        raise ValueError("file download URL must use HTTPS.")

    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as http:
        async with http.stream("GET", url) as response:
            response.raise_for_status()
            if response.url.scheme.lower() != "https":
                raise ValueError("file download redirect must remain on HTTPS.")

            declared = response.headers.get("content-length")
            if declared:
                try:
                    if int(declared) > _PRINT_MAX_BYTES:
                        raise ValueError("print file must be 25 MB or smaller.")
                except ValueError as exc:
                    if str(exc) == "print file must be 25 MB or smaller.":
                        raise

            data = bytearray()
            async for chunk in response.aiter_bytes():
                if len(data) + len(chunk) > _PRINT_MAX_BYTES:
                    raise ValueError("print file must be 25 MB or smaller.")
                data.extend(chunk)

    return _store_print_bytes(_attachment_filename(file), bytes(data))


async def _submit_print_job(
    target: Path,
    copies: int,
    media: str,
    color: bool,
    duplex: bool,
) -> dict[str, Any]:
    copies = max(1, min(int(copies), 10))
    clean_media = media.strip()
    if not clean_media or len(clean_media) > 40 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-." for ch in clean_media):
        raise ValueError("Invalid media name.")
    args = [
        "lp", "-d", "ChatGPT_Printer",
        "-n", str(copies),
        "-o", f"media={clean_media}",
        "-o", "print-color-mode=color" if color else "print-color-mode=monochrome",
        "-o", "sides=two-sided-long-edge" if duplex else "sides=one-sided",
        str(target),
    ]
    env = os.environ.copy()
    env["DEVICE_URI"] = _printer_uri()
    proc = await asyncio.to_thread(
        subprocess.run, args, capture_output=True, text=True, timeout=60, env=env
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "lp failed").strip())
    return {
        "ok": True,
        "printer_uri": _printer_uri(),
        "path": str(target),
        "copies": copies,
        "media": clean_media,
        "color": color,
        "duplex": duplex,
        "job": proc.stdout.strip(),
    }


@mcp.tool(meta={"openai/fileParams": ["file"]})
async def print_attachment(
    file: OpenAIFileInput,
    copies: int = 1,
    media: str = "A4",
    color: bool = True,
    duplex: bool = False,
) -> dict[str, Any]:
    """Print a PDF/JPEG/PNG file attached by the user directly from ChatGPT.

    ChatGPT supplies a temporary authorized download URL through the file parameter.
    The connector downloads, validates, prints, and deletes the temporary local copy.
    """
    target: Path | None = None
    try:
        target = await _download_print_attachment(file)
        result = await _submit_print_job(target, copies, media, color, duplex)
        result["uploaded_filename"] = _attachment_filename(file)
        result["file_id"] = file.file_id
        return result
    except (httpx.HTTPError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if target is not None:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass


@mcp.tool()
async def upload_and_print_document(
    filename: str,
    content_base64: str,
    copies: int = 1,
    media: str = "A4",
    color: bool = True,
    duplex: bool = False,
) -> dict[str, Any]:
    """Upload a PDF/JPEG/PNG attachment as base64 and print it immediately.

    Kept as a compatibility fallback. Prefer print_attachment for normal ChatGPT files.
    """
    target: Path | None = None
    try:
        target = _decode_print_upload(filename, content_base64)
        result = await _submit_print_job(target, copies, media, color, duplex)
        result["uploaded_filename"] = Path(filename).name
        return result
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if target is not None:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass


@mcp.tool()
async def print_document(
    path: str,
    copies: int = 1,
    media: str = "A4",
    color: bool = True,
    duplex: bool = False,
) -> dict[str, Any]:
    """Print a PDF/JPEG/PNG stored under /config/www using the configured IPP printer.

    The printer destination is fixed by PRINTER_URI (default: the discovered HP
    DeskJet 2600 at ipp://192.168.0.104/ipp/print). No arbitrary command or
    destination is accepted from the MCP caller.
    """
    try:
        target = _resolve_print_path(path)
        return await _submit_print_job(target, copies, media, color, duplex)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
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
    print("ChatGPT Connector 0.8.2 starting...", flush=True)
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
            "get_history, get_logbook, get_error_log, call_service, print_attachment, upload_and_print_document, print_document, "
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
