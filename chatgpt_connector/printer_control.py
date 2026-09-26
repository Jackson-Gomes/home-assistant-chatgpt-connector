from __future__ import annotations

import asyncio
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any


_QUEUE_NAME = "ChatGPT_Printer"
_SAFE_IDENTIFY_ACTIONS = {"flash", "display", "sound", "speak"}
_SAFE_WAKE_OPERATIONS = ("Startup-Printer", "Activate-Printer", "Resume-Printer")
_CAPABILITY_ATTRIBUTES = (
    "printer-make-and-model",
    "printer-state",
    "printer-state-reasons",
    "printer-is-accepting-jobs",
    "queued-job-count",
    "operations-supported",
    "identify-actions-default",
    "identify-actions-supported",
    "color-supported",
    "sides-supported",
    "media-supported",
    "document-format-supported",
)


def printer_uri() -> str:
    uri = os.environ.get("PRINTER_URI", "ipp://192.168.0.104/ipp/print").strip()
    if not (uri.startswith("ipp://") or uri.startswith("ipps://")):
        raise ValueError("PRINTER_URI must use ipp:// or ipps://.")
    return uri


async def _run_command(args: list[str], timeout: int = 15) -> subprocess.CompletedProcess[str]:
    return await asyncio.to_thread(
        subprocess.run,
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


async def _run_ipptool(test_body: str, timeout: int = 15) -> str:
    if not Path("/usr/bin/ipptool").exists():
        raise RuntimeError("ipptool is not installed in the add-on image.")

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".test", delete=False) as handle:
        handle.write(test_body)
        test_path = handle.name

    try:
        proc = await _run_command(
            ["ipptool", "-tv", "-T", "5", printer_uri(), test_path],
            timeout=timeout,
        )
    finally:
        try:
            os.unlink(test_path)
        except OSError:
            pass

    output = "\n".join(part for part in (proc.stdout, proc.stderr) if part).strip()
    if proc.returncode != 0:
        raise RuntimeError(output or f"ipptool failed with exit code {proc.returncode}")
    return output


def _attribute_line(output: str, name: str) -> str | None:
    pattern = re.compile(rf"^\s*{re.escape(name)}\s+\([^)]*\)\s*=\s*(.*?)\s*$", re.MULTILINE)
    match = pattern.search(output)
    return match.group(1).strip() if match else None


def _split_values(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip().strip("'\"") for item in value.split(",") if item.strip()]


def _as_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return None


def _as_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _capability_test() -> str:
    requested = ",".join(_CAPABILITY_ATTRIBUTES)
    return f'''{{
    NAME "ChatGPT printer capability probe"
    OPERATION Get-Printer-Attributes
    GROUP operation-attributes-tag
    ATTR charset attributes-charset utf-8
    ATTR language attributes-natural-language en
    ATTR uri printer-uri $uri
    ATTR keyword requested-attributes {requested}
    STATUS successful-ok
    STATUS successful-ok-ignored-or-substituted-attributes
}}
'''


async def get_capabilities() -> dict[str, Any]:
    output = await _run_ipptool(_capability_test())
    operations = _split_values(_attribute_line(output, "operations-supported"))
    identify_actions = _split_values(_attribute_line(output, "identify-actions-supported"))
    sides = _split_values(_attribute_line(output, "sides-supported"))
    media = _split_values(_attribute_line(output, "media-supported"))
    formats = _split_values(_attribute_line(output, "document-format-supported"))

    return {
        "printer_uri": printer_uri(),
        "make_and_model": _attribute_line(output, "printer-make-and-model"),
        "state": _attribute_line(output, "printer-state"),
        "state_reasons": _split_values(_attribute_line(output, "printer-state-reasons")),
        "accepting_jobs": _as_bool(_attribute_line(output, "printer-is-accepting-jobs")),
        "queued_job_count": _as_int(_attribute_line(output, "queued-job-count")),
        "operations_supported": operations,
        "identify_actions_supported": identify_actions,
        "identify_actions_default": _split_values(_attribute_line(output, "identify-actions-default")),
        "color_supported": _as_bool(_attribute_line(output, "color-supported")),
        "sides_supported": sides,
        "media_supported": media,
        "document_formats_supported": formats,
        "safe_controls": {
            "identify": "Identify-Printer" in operations,
            "identify_flash": "Identify-Printer" in operations and "flash" in identify_actions,
            "identify_display": "Identify-Printer" in operations and "display" in identify_actions,
            "identify_sound": "Identify-Printer" in operations and "sound" in identify_actions,
            "identify_speak": "Identify-Printer" in operations and "speak" in identify_actions,
            "wake": next((op for op in _SAFE_WAKE_OPERATIONS if op in operations), None),
            "cancel_job": "Cancel-Job" in operations,
        },
    }


def _safe_message(message: str) -> str:
    text = " ".join(message.strip().split())[:80]
    if not text:
        return "ChatGPT connected"
    if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ._:-" for ch in text):
        raise ValueError("message contains unsupported characters")
    return text


def _simple_operation_test(operation: str) -> str:
    return f'''{{
    NAME "ChatGPT {operation}"
    OPERATION {operation}
    GROUP operation-attributes-tag
    ATTR charset attributes-charset utf-8
    ATTR language attributes-natural-language en
    ATTR uri printer-uri $uri
    ATTR name requesting-user-name $user
    STATUS successful-ok
    STATUS successful-ok-ignored-or-substituted-attributes
}}
'''


def _identify_test(action: str, message: str) -> str:
    message_attr = ""
    if action in {"display", "speak"}:
        message_attr = f'    ATTR text message "{_safe_message(message)}"\n'
    return f'''{{
    NAME "ChatGPT Identify-Printer {action}"
    OPERATION Identify-Printer
    GROUP operation-attributes-tag
    ATTR charset attributes-charset utf-8
    ATTR language attributes-natural-language en
    ATTR uri printer-uri $uri
    ATTR name requesting-user-name $user
    ATTR keyword identify-actions {action}
{message_attr}    STATUS successful-ok
    STATUS successful-ok-ignored-or-substituted-attributes
}}
'''


async def identify(action: str = "flash", message: str = "ChatGPT connected") -> dict[str, Any]:
    clean_action = action.strip().lower()
    if clean_action not in _SAFE_IDENTIFY_ACTIONS:
        raise ValueError(f"action must be one of: {', '.join(sorted(_SAFE_IDENTIFY_ACTIONS))}")

    caps = await get_capabilities()
    operations = set(caps["operations_supported"])
    actions = set(caps["identify_actions_supported"])
    if "Identify-Printer" not in operations:
        return {
            "ok": False,
            "supported": False,
            "error": "This printer does not advertise the IPP Identify-Printer operation.",
            "capabilities": caps,
        }
    if clean_action not in actions:
        return {
            "ok": False,
            "supported": False,
            "error": f"This printer does not advertise identify action {clean_action!r}.",
            "capabilities": caps,
        }

    output = await _run_ipptool(_identify_test(clean_action, message))
    return {
        "ok": True,
        "supported": True,
        "action": clean_action,
        "printer_uri": printer_uri(),
        "response": output[-2000:],
    }


async def wake() -> dict[str, Any]:
    caps = await get_capabilities()
    operations = set(caps["operations_supported"])
    selected = next((op for op in _SAFE_WAKE_OPERATIONS if op in operations), None)
    if selected is None:
        return {
            "ok": False,
            "supported": False,
            "error": "This printer does not advertise a standard IPP startup/activate/resume operation.",
            "capabilities": caps,
        }

    output = await _run_ipptool(_simple_operation_test(selected))
    return {
        "ok": True,
        "supported": True,
        "operation": selected,
        "printer_uri": printer_uri(),
        "response": output[-2000:],
    }


async def queue_status() -> dict[str, Any]:
    status = await _run_command(["lpstat", "-p", _QUEUE_NAME, "-l"])
    jobs = await _run_command(["lpstat", "-W", "not-completed", "-o", _QUEUE_NAME])
    if status.returncode != 0:
        raise RuntimeError((status.stderr or status.stdout or "lpstat failed").strip())
    job_lines = [line.strip() for line in jobs.stdout.splitlines() if line.strip()]
    return {
        "ok": True,
        "queue": _QUEUE_NAME,
        "status": status.stdout.strip(),
        "pending_jobs": job_lines,
        "pending_job_count": len(job_lines),
    }


async def queue_control(action: str) -> dict[str, Any]:
    clean_action = action.strip().lower()
    commands = {
        "pause": ["cupsdisable", _QUEUE_NAME],
        "resume": ["cupsenable", _QUEUE_NAME],
        "cancel_all": ["cancel", "-a", _QUEUE_NAME],
    }
    if clean_action not in commands:
        raise ValueError("action must be pause, resume, or cancel_all")

    proc = await _run_command(commands[clean_action])
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"{clean_action} failed").strip())
    return {"ok": True, "action": clean_action, **(await queue_status())}


async def cancel_job(job_id: str) -> dict[str, Any]:
    clean_job = job_id.strip()
    if not re.fullmatch(rf"{re.escape(_QUEUE_NAME)}-\d+", clean_job):
        raise ValueError(f"job_id must look like {_QUEUE_NAME}-123")

    status = await queue_status()
    known = {line.split()[0] for line in status["pending_jobs"] if line.split()}
    if clean_job not in known:
        return {"ok": False, "error": "Job is not pending in the local ChatGPT printer queue.", "job_id": clean_job}

    proc = await _run_command(["cancel", clean_job])
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "cancel failed").strip())
    return {"ok": True, "job_id": clean_job, **(await queue_status())}


async def validate_print_options(*, color: bool, duplex: bool) -> dict[str, Any]:
    caps = await get_capabilities()
    if color and caps.get("color_supported") is False:
        raise ValueError("The printer reports that color printing is unsupported.")
    if duplex and "two-sided-long-edge" not in set(caps.get("sides_supported") or []):
        raise ValueError(
            "Automatic duplex is not advertised by this printer. "
            "HP DeskJet 2600 uses manual duplex, so the connector will not send an unsupported automatic-duplex option."
        )
    return caps
