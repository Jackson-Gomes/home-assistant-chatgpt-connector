from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import websockets

from ha_client import HomeAssistantClient, HomeAssistantError

VERSION = "0.6.0"
STATE_PATH = Path("/data/brain_state.json")
EVENT_LOG_PATH = Path("/data/brain_events.jsonl")
EVENT_LOG_BACKUP_PATH = Path("/data/brain_events.jsonl.1")
MAX_EVENT_LOG_BYTES = 5 * 1024 * 1024

DEFAULT_DOMAINS = {
    "binary_sensor",
    "person",
    "light",
    "switch",
    "media_player",
    "vacuum",
    "climate",
    "cover",
    "lock",
}


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class BrainConfig:
    enabled: bool
    ai_task_entity: str
    batch_seconds: int
    min_events_per_batch: int
    max_ai_calls_per_hour: int
    max_ai_calls_per_day: int
    announce_enabled: bool
    announce_service: str
    notify_suggestions: bool
    tracked_domains: frozenset[str]

    @classmethod
    def from_env(cls) -> "BrainConfig":
        domains_raw = os.environ.get("BRAIN_TRACKED_DOMAINS", "")
        domains = {
            item.strip().lower()
            for item in domains_raw.split(",")
            if item.strip()
        } or DEFAULT_DOMAINS
        return cls(
            enabled=_env_bool("BRAIN_ENABLED", False),
            ai_task_entity=os.environ.get(
                "BRAIN_AI_TASK_ENTITY", "ai_task.codex_assist_ai_task"
            ).strip(),
            batch_seconds=_env_int("BRAIN_BATCH_SECONDS", 45, 10, 600),
            min_events_per_batch=_env_int("BRAIN_MIN_EVENTS_PER_BATCH", 2, 1, 20),
            max_ai_calls_per_hour=_env_int("BRAIN_MAX_AI_CALLS_PER_HOUR", 4, 1, 60),
            max_ai_calls_per_day=_env_int("BRAIN_MAX_AI_CALLS_PER_DAY", 30, 1, 500),
            announce_enabled=_env_bool("BRAIN_ANNOUNCE_ENABLED", False),
            announce_service=os.environ.get(
                "BRAIN_ANNOUNCE_SERVICE", "notify.alexa_media_alexa"
            ).strip(),
            notify_suggestions=_env_bool("BRAIN_NOTIFY_SUGGESTIONS", False),
            tracked_domains=frozenset(domains),
        )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_value(state: Any) -> str | None:
    if isinstance(state, dict):
        value = state.get("state")
        return str(value) if value is not None else None
    return None


def compact_state_changed(
    event: dict[str, Any], tracked_domains: frozenset[str]
) -> dict[str, Any] | None:
    """Return a compact meaningful state change or None for local noise."""
    if event.get("event_type") != "state_changed":
        return None

    data = event.get("data")
    if not isinstance(data, dict):
        return None

    entity_id = str(data.get("entity_id") or "").strip().lower()
    if "." not in entity_id:
        return None
    domain = entity_id.split(".", 1)[0]
    if domain not in tracked_domains:
        return None

    old_state_obj = data.get("old_state")
    new_state_obj = data.get("new_state")
    if not isinstance(old_state_obj, dict) or not isinstance(new_state_obj, dict):
        return None

    old_state = _state_value(old_state_obj)
    new_state = _state_value(new_state_obj)
    if old_state is None or new_state is None or old_state == new_state:
        return None

    if old_state in {"unknown", "unavailable"} and new_state in {
        "unknown",
        "unavailable",
    }:
        return None

    if domain in {"light", "switch"} and {old_state, new_state} - {"on", "off"}:
        return None

    attrs = new_state_obj.get("attributes")
    if not isinstance(attrs, dict):
        attrs = {}

    friendly_name = str(attrs.get("friendly_name") or entity_id)
    device_class = attrs.get("device_class")
    if device_class is not None:
        device_class = str(device_class)

    return {
        "time": str(event.get("time_fired") or _utc_now_iso()),
        "entity_id": entity_id,
        "domain": domain,
        "name": friendly_name[:120],
        "device_class": device_class,
        "from": old_state[:80],
        "to": new_state[:80],
    }


def is_priority_event(event: dict[str, Any]) -> bool:
    domain = event.get("domain")
    target = event.get("to")
    device_class = event.get("device_class")

    if domain == "vacuum" and target in {"error", "stuck"}:
        return True
    if domain in {"person", "lock", "cover"}:
        return True
    if domain == "binary_sensor" and device_class in {
        "door",
        "garage_door",
        "opening",
        "window",
    }:
        return True
    return False


class BrainState:
    def __init__(self, config: BrainConfig) -> None:
        self.config = config
        self.data: dict[str, Any] = {
            "version": VERSION,
            "enabled": config.enabled,
            "connected": False,
            "started_at": _utc_now_iso(),
            "last_event_at": None,
            "last_ai_call_at": None,
            "last_decision": None,
            "metrics": {
                "events_seen": 0,
                "events_kept": 0,
                "batches_considered": 0,
                "batches_skipped_small": 0,
                "batches_skipped_budget": 0,
                "ai_calls": 0,
                "ai_errors": 0,
                "decisions_ignore": 0,
                "decisions_suggest": 0,
                "decisions_announce": 0,
                "announcements_sent": 0,
            },
            "suggestions": [],
            "recent_ai_calls": [],
        }
        self._load_existing()

    def _load_existing(self) -> None:
        try:
            existing = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(existing, dict):
            return

        old_metrics = existing.get("metrics")
        if isinstance(old_metrics, dict):
            for key in self.data["metrics"]:
                try:
                    self.data["metrics"][key] = int(old_metrics.get(key, 0))
                except (TypeError, ValueError):
                    pass

        suggestions = existing.get("suggestions")
        if isinstance(suggestions, list):
            self.data["suggestions"] = suggestions[-50:]

        calls = existing.get("recent_ai_calls")
        if isinstance(calls, list):
            self.data["recent_ai_calls"] = [
                float(item) for item in calls if isinstance(item, (int, float))
            ][-500:]

    def metric(self, name: str, amount: int = 1) -> None:
        metrics = self.data["metrics"]
        metrics[name] = int(metrics.get(name, 0)) + amount

    def prune_calls(self, now: float | None = None) -> list[float]:
        current = now if now is not None else time.time()
        cutoff = current - 86400
        calls = [
            float(item)
            for item in self.data.get("recent_ai_calls", [])
            if isinstance(item, (int, float)) and float(item) >= cutoff
        ]
        self.data["recent_ai_calls"] = calls[-500:]
        return calls

    def budget_available(self, now: float | None = None) -> bool:
        current = now if now is not None else time.time()
        calls = self.prune_calls(current)
        hour_calls = sum(1 for item in calls if item >= current - 3600)
        day_calls = len(calls)
        return (
            hour_calls < self.config.max_ai_calls_per_hour
            and day_calls < self.config.max_ai_calls_per_day
        )

    def record_ai_call(self, now: float | None = None) -> None:
        current = now if now is not None else time.time()
        calls = self.prune_calls(current)
        calls.append(current)
        self.data["recent_ai_calls"] = calls[-500:]
        self.data["last_ai_call_at"] = _utc_now_iso()
        self.metric("ai_calls")

    def add_suggestion(self, message: str, reason: str, source: str) -> None:
        suggestion = {
            "id": f"s-{int(time.time() * 1000)}",
            "created_at": _utc_now_iso(),
            "message": message[:500],
            "reason": reason[:500],
            "source": source[:120],
        }
        items = self.data.get("suggestions")
        if not isinstance(items, list):
            items = []
        items.append(suggestion)
        self.data["suggestions"] = items[-50:]

    def save(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp = STATE_PATH.with_suffix(".tmp")
        temp.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temp, STATE_PATH)


class BrainService:
    def __init__(self, config: BrainConfig) -> None:
        self.config = config
        self.ha = HomeAssistantClient()
        self.state = BrainState(config)
        self.pending: dict[str, dict[str, Any]] = {}
        self.flush_task: asyncio.Task[None] | None = None

    def _append_event_log(self, event: dict[str, Any]) -> None:
        EVENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            if (
                EVENT_LOG_PATH.exists()
                and EVENT_LOG_PATH.stat().st_size > MAX_EVENT_LOG_BYTES
            ):
                try:
                    EVENT_LOG_BACKUP_PATH.unlink(missing_ok=True)
                except OSError:
                    pass
                os.replace(EVENT_LOG_PATH, EVENT_LOG_BACKUP_PATH)
            with EVENT_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                )
        except OSError as exc:
            print(f"Brain event log warning: {exc}", flush=True)

    async def _schedule_flush(self) -> None:
        if self.flush_task is not None and not self.flush_task.done():
            return

        async def wait_and_flush() -> None:
            await asyncio.sleep(self.config.batch_seconds)
            await self.flush()

        self.flush_task = asyncio.create_task(wait_and_flush())

    async def handle_event(self, raw_event: dict[str, Any]) -> None:
        self.state.metric("events_seen")
        compact = compact_state_changed(raw_event, self.config.tracked_domains)
        if compact is None:
            return

        self.state.metric("events_kept")
        self.state.data["last_event_at"] = compact["time"]
        self.pending[compact["entity_id"]] = compact
        self._append_event_log(compact)
        self.state.save()
        await self._schedule_flush()

    def _should_analyze(self, events: list[dict[str, Any]]) -> bool:
        if any(is_priority_event(item) for item in events):
            return True
        return len(events) >= self.config.min_events_per_batch

    def _format_prompt(self, events: list[dict[str, Any]]) -> str:
        lines = []
        for item in events:
            lines.append(
                f"- {item.get('time', '')} | {item['entity_id']} ({item['name']}): "
                f"{item['from']} -> {item['to']}"
            )
        joined = "\n".join(lines)
        return (
            "Você é o classificador de atenção de uma casa inteligente. "
            "Analise somente os eventos abaixo. Não execute nenhuma ação física. "
            "Escolha 'ignore' para mudanças normais sem valor, 'suggest' quando houver "
            "um hábito ou automação que valha sugerir ao morador, e 'announce' somente "
            "quando uma informação imediata e útil merecer ser falada em voz alta. "
            "Não use announce para eventos rotineiros. Mensagem curta, em português do Brasil, "
            "máximo 160 caracteres. Se não houver algo realmente útil, escolha ignore.\n\n"
            f"Eventos recentes:\n{joined}"
        )

    async def _ask_ai(self, events: list[dict[str, Any]]) -> dict[str, str]:
        structure = {
            "decision": {
                "description": "One of: ignore, suggest, announce",
                "required": True,
                "selector": {
                    "select": {"options": ["ignore", "suggest", "announce"]}
                },
            },
            "message": {
                "description": "Short pt-BR message, empty only when decision is ignore",
                "required": True,
                "selector": {"text": {}},
            },
            "reason": {
                "description": "Very short reason for the classification",
                "required": True,
                "selector": {"text": {}},
            },
        }
        result = await self.ha.websocket_command(
            "call_service",
            {
                "domain": "ai_task",
                "service": "generate_data",
                "service_data": {
                    "task_name": "home brain event triage",
                    "instructions": self._format_prompt(events),
                    "entity_id": self.config.ai_task_entity,
                    "structure": structure,
                },
                "return_response": True,
            },
        )
        if not isinstance(result, dict):
            raise HomeAssistantError("AI Task returned an invalid WebSocket result.")
        response = result.get("response")
        if not isinstance(response, dict):
            raise HomeAssistantError("AI Task returned no response payload.")
        data = response.get("data")
        if not isinstance(data, dict):
            raise HomeAssistantError("AI Task structured response is missing data.")

        decision = str(data.get("decision") or "ignore").strip().lower()
        if decision not in {"ignore", "suggest", "announce"}:
            decision = "ignore"
        return {
            "decision": decision,
            "message": str(data.get("message") or "").strip()[:500],
            "reason": str(data.get("reason") or "").strip()[:500],
        }

    async def _send_announcement(self, message: str) -> None:
        service_ref = self.config.announce_service.strip().lower()
        if "." not in service_ref:
            raise HomeAssistantError("brain_announce_service must be domain.service.")
        domain, service = service_ref.split(".", 1)
        if domain != "notify":
            raise HomeAssistantError(
                "Autonomous Brain announcements are restricted to notify.* services."
            )
        await self.ha.call_service(
            domain,
            service,
            data={"message": message, "data": {"type": "tts"}},
        )
        self.state.metric("announcements_sent")

    async def _notify_suggestion(self, message: str, reason: str) -> None:
        if not self.config.notify_suggestions:
            return
        await self.ha.call_service(
            "persistent_notification",
            "create",
            data={
                "title": "Brain suggestion",
                "message": f"{message}\n\n{reason}".strip(),
            },
        )

    async def _apply_decision(
        self, decision: dict[str, str], events: list[dict[str, Any]]
    ) -> None:
        action = decision["decision"]
        message = decision["message"]
        reason = decision["reason"]
        source = ",".join(item["entity_id"] for item in events[:5])
        self.state.data["last_decision"] = {
            "at": _utc_now_iso(),
            **decision,
            "entities": [item["entity_id"] for item in events],
        }

        if action == "ignore":
            self.state.metric("decisions_ignore")
            return

        if action == "suggest":
            self.state.metric("decisions_suggest")
            if message:
                self.state.add_suggestion(message, reason, source)
                await self._notify_suggestion(message, reason)
            return

        self.state.metric("decisions_announce")
        if not message:
            return
        if self.config.announce_enabled:
            await self._send_announcement(message)
        else:
            self.state.add_suggestion(
                message,
                (
                    "Announcement suppressed because autonomous speech is disabled. "
                    f"{reason}"
                ).strip(),
                source,
            )

    async def flush(self) -> None:
        if not self.pending:
            return

        events = list(self.pending.values())
        self.pending.clear()
        self.state.metric("batches_considered")

        if not self._should_analyze(events):
            self.state.metric("batches_skipped_small")
            self.state.save()
            return

        if not self.state.budget_available():
            self.state.metric("batches_skipped_budget")
            self.state.save()
            return

        self.state.record_ai_call()
        self.state.save()

        try:
            decision = await self._ask_ai(events)
            await self._apply_decision(decision, events)
        except Exception as exc:
            self.state.metric("ai_errors")
            self.state.data["last_error"] = {
                "at": _utc_now_iso(),
                "message": str(exc)[:1000],
            }
            print(f"Brain AI evaluation failed: {exc}", flush=True)
        finally:
            self.state.save()

    async def _authenticate(self, ws: Any) -> None:
        hello = json.loads(await ws.recv())
        if hello.get("type") != "auth_required":
            raise HomeAssistantError(
                f"Unexpected WebSocket handshake: {hello.get('type')!r}."
            )
        await ws.send(json.dumps({"type": "auth", "access_token": self.ha.token}))
        auth = json.loads(await ws.recv())
        if auth.get("type") != "auth_ok":
            raise HomeAssistantError(
                f"Home Assistant WebSocket authentication failed: {auth}."
            )

    async def listen_once(self) -> None:
        async with websockets.connect(
            self.ha.websocket_url,
            open_timeout=10,
            close_timeout=5,
            max_size=16 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            await self._authenticate(ws)
            await ws.send(
                json.dumps(
                    {
                        "id": 1,
                        "type": "subscribe_events",
                        "event_type": "state_changed",
                    }
                )
            )

            while True:
                payload = json.loads(await ws.recv())
                if payload.get("id") == 1 and payload.get("type") == "result":
                    if not payload.get("success"):
                        raise HomeAssistantError(
                            f"state_changed subscription failed: {payload.get('error')}"
                        )
                    self.state.data["connected"] = True
                    self.state.save()
                    print("Brain: state_changed subscription active.", flush=True)
                    continue

                if payload.get("id") != 1 or payload.get("type") != "event":
                    continue
                event = payload.get("event")
                if isinstance(event, dict):
                    await self.handle_event(event)

    async def run(self) -> None:
        if not self.config.enabled:
            print("Brain: disabled by add-on configuration.", flush=True)
            self.state.data["enabled"] = False
            self.state.save()
            return

        print(
            f"Brain {VERSION}: enabled; AI Task={self.config.ai_task_entity}; "
            f"budget={self.config.max_ai_calls_per_hour}/hour, "
            f"{self.config.max_ai_calls_per_day}/day; "
            f"autonomous speech={'on' if self.config.announce_enabled else 'off'}.",
            flush=True,
        )
        backoff = 2
        while True:
            try:
                await self.listen_once()
                backoff = 2
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.state.data["connected"] = False
                self.state.data["last_error"] = {
                    "at": _utc_now_iso(),
                    "message": str(exc)[:1000],
                }
                self.state.save()
                print(f"Brain WebSocket disconnected: {exc}", flush=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)


async def main() -> None:
    config = BrainConfig.from_env()
    service = BrainService(config)
    await service.run()


if __name__ == "__main__":
    asyncio.run(main())
