from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STATE_PATH = Path("/data/brain_state.json")
EVENT_LOG_PATH = Path("/data/brain_events.jsonl")


def _read_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "version": "0.6.0",
            "enabled": False,
            "connected": False,
            "status": "no_state_yet",
        }
    return data if isinstance(data, dict) else {}


def _read_recent_events(limit: int) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 100))
    try:
        lines = EVENT_LOG_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    result: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            result.append(item)
    return result


def register_brain_tools(mcp: Any) -> None:
    @mcp.tool()
    async def brain_status() -> dict[str, Any]:
        """Read Autonomous Brain status, AI-call budget metrics and last decision."""
        state = _read_state()
        return {
            "ok": True,
            "version": state.get("version"),
            "enabled": state.get("enabled"),
            "connected": state.get("connected"),
            "started_at": state.get("started_at"),
            "last_event_at": state.get("last_event_at"),
            "last_ai_call_at": state.get("last_ai_call_at"),
            "last_decision": state.get("last_decision"),
            "last_error": state.get("last_error"),
            "metrics": state.get("metrics", {}),
        }

    @mcp.tool()
    async def brain_recent_events(limit: int = 20) -> dict[str, Any]:
        """Read compact locally-filtered Home Assistant events seen by the Brain."""
        try:
            clean_limit = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            clean_limit = 20
        events = _read_recent_events(clean_limit)
        return {"ok": True, "count": len(events), "events": events}

    @mcp.tool()
    async def brain_suggestions(limit: int = 10) -> dict[str, Any]:
        """Read recent automation or behavior suggestions produced by the Brain."""
        try:
            clean_limit = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            clean_limit = 10
        state = _read_state()
        suggestions = state.get("suggestions")
        if not isinstance(suggestions, list):
            suggestions = []
        items = [item for item in suggestions if isinstance(item, dict)][-clean_limit:]
        return {"ok": True, "count": len(items), "suggestions": items}
