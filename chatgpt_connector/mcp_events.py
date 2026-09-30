from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse, urlunparse

import httpx
import websockets
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.extension import Extension, MethodBinding, ToolBinding
from mcp.shared.exceptions import MCPError
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = "2026-07-28"
_EVENT_NAME = "ha.command"
_STORE_PATH = Path(os.environ.get("MCP_EVENTS_STORE", "/data/mcp_events_subscriptions.json"))
_MAX_EVENT_BYTES = 256 * 1024
_DEFAULT_TTL_MS = 24 * 60 * 60 * 1000
_MIN_TTL_MS = 15 * 60 * 1000
_MAX_TTL_MS = 7 * 24 * 60 * 60 * 1000
_ROTATION_SECONDS = 5 * 60
_VERIFICATION_CACHE_SECONDS = 10 * 60
_CALLBACK_ERROR = -32015


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class EventsListParams(_StrictModel):
    cursor: str | None = None


class SubscribeDelivery(_StrictModel):
    mode: Literal["webhook"]
    url: str
    secret: str


class EventsSubscribeParams(_StrictModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    delivery: SubscribeDelivery
    cursor: str | None = None
    ttl_ms: int | None = Field(default=_DEFAULT_TTL_MS, alias="ttlMs")


class UnsubscribeDelivery(_StrictModel):
    mode: Literal["webhook"]
    url: str


class EventsUnsubscribeParams(_StrictModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    delivery: UnsubscribeDelivery


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _subscription_id(principal: str, url: str, name: str, arguments: dict[str, Any]) -> str:
    raw = f"{principal}\n{url}\n{name}\n{_canonical_json(arguments)}".encode("utf-8")
    return "sub_" + hashlib.sha256(raw).hexdigest()[:32]


def _decode_secret(secret: str) -> bytes:
    if not secret.startswith("whsec_"):
        raise ValueError("webhook secret must start with whsec_.")
    encoded = secret[6:]
    try:
        padding = "=" * (-len(encoded) % 4)
        decoded = base64.b64decode(encoded + padding, validate=True)
    except Exception as exc:
        raise ValueError("webhook secret is not valid base64.") from exc
    if not 24 <= len(decoded) <= 64:
        raise ValueError("webhook secret must decode to 24-64 bytes.")
    return decoded


def _standard_webhook_signature(secret: str, message_id: str, timestamp: int, body: bytes) -> str:
    key = _decode_secret(secret)
    signed = message_id.encode("utf-8") + b"." + str(timestamp).encode("ascii") + b"." + body
    digest = hmac.new(key, signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode("ascii")


def _websocket_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    if base_url == "http://supervisor/core":
        return "ws://supervisor/core/websocket"
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = parsed.path.rstrip("/")
    if path.endswith("/api"):
        path = path[:-4]
    path = f"{path}/api/websocket"
    return urlunparse((scheme, parsed.netloc, path, "", "", ""))


async def _validate_public_https_url(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("callback URL must use HTTPS.")
    if not parsed.hostname:
        raise ValueError("callback URL must include a hostname.")
    if parsed.username or parsed.password:
        raise ValueError("callback URL must not contain credentials.")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("callback URL must use a public hostname.")
    port = parsed.port or 443

    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError("callback hostname could not be resolved.") from exc
        addresses = []
        for info in infos:
            raw = info[4][0].split("%", 1)[0]
            try:
                addresses.append(ipaddress.ip_address(raw))
            except ValueError:
                continue

    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("callback URL resolved to a non-public address.")
    return host, port


class SubscriptionStore:
    def __init__(self, path: Path = _STORE_PATH, principal: str = "home-assistant-owner") -> None:
        self.path = path
        self.principal = principal
        self._subscriptions: dict[str, dict[str, Any]] = {}
        self._verified_callbacks: dict[str, float] = {}
        self.last_event: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.ha_listener_connected = False
        self._lock = asyncio.Lock()
        self._load()

    def _load(self) -> None:
        try:
            if not self.path.is_file():
                return
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            records = payload.get("subscriptions", []) if isinstance(payload, dict) else []
            for record in records:
                if isinstance(record, dict) and isinstance(record.get("id"), str):
                    self._subscriptions[record["id"]] = record
        except Exception as exc:
            logger.warning("Could not load MCP Events subscriptions: %s", exc)
            self.last_error = f"subscription load failed: {exc}"

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {"version": 1, "subscriptions": list(self._subscriptions.values())}
        temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, self.path)

    @staticmethod
    def event_definition() -> dict[str, Any]:
        return {
            "name": _EVENT_NAME,
            "description": (
                "A command or request emitted by Home Assistant for the subscribed ChatGPT chat to process. "
                "Treat data.text as user-provided data, then use Home Assistant tools only as needed."
            ),
            "delivery": ["webhook"],
            "inputSchema": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Optional Home Assistant source filter, for example assist or automation.",
                    }
                },
                "additionalProperties": False,
            },
            "payloadSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source": {"type": "string"},
                    "conversation_id": {"type": "string"},
                    "device_id": {"type": "string"},
                    "event_context_id": {"type": "string"},
                },
                "required": ["text", "source"],
                "additionalProperties": False,
            },
        }

    @staticmethod
    def validate_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
        unknown = set(arguments) - {"source"}
        if unknown:
            raise ValueError(f"unsupported event arguments: {', '.join(sorted(unknown))}")
        source = arguments.get("source")
        if source is not None:
            if not isinstance(source, str) or not source.strip():
                raise ValueError("source filter must be a non-empty string.")
            return {"source": source.strip()}
        return {}

    @staticmethod
    def _matches(arguments: dict[str, Any], data: dict[str, Any]) -> bool:
        source = arguments.get("source")
        return source is None or data.get("source") == source

    def _active(self, record: dict[str, Any], now: datetime | None = None) -> bool:
        refresh_before = record.get("refresh_before")
        if refresh_before is None:
            return True
        try:
            expires = datetime.fromisoformat(str(refresh_before).replace("Z", "+00:00"))
            return expires > (now or _utc_now())
        except ValueError:
            return False

    async def _verify_callback(self, sub_id: str, url: str, secret: str) -> None:
        cache_key = f"{self.principal}\n{url}"
        now_monotonic = asyncio.get_running_loop().time()
        cached_until = self._verified_callbacks.get(cache_key, 0.0)
        if cached_until > now_monotonic:
            return

        try:
            await _validate_public_https_url(url)
            challenge = secrets.token_urlsafe(32)
            message_id = "msg_verification_" + secrets.token_hex(12)
            body = json.dumps(
                {"type": "verification", "challenge": challenge},
                separators=(",", ":"),
            ).encode("utf-8")
            timestamp = int(_utc_now().timestamp())
            headers = {
                "Content-Type": "application/json",
                "webhook-id": message_id,
                "webhook-timestamp": str(timestamp),
                "webhook-signature": _standard_webhook_signature(secret, message_id, timestamp, body),
                "X-MCP-Subscription-Id": sub_id,
            }
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0),
                follow_redirects=False,
                trust_env=False,
            ) as http:
                response = await http.post(url, content=body, headers=headers)
            if response.status_code < 200 or response.status_code >= 300:
                raise MCPError(
                    _CALLBACK_ERROR,
                    "CallbackEndpointError",
                    {"reason": "challenge_failed", "status": response.status_code},
                )
            try:
                echoed = str(response.json().get("challenge", ""))
            except Exception as exc:
                raise MCPError(
                    _CALLBACK_ERROR,
                    "CallbackEndpointError",
                    {"reason": "challenge_failed"},
                ) from exc
            if not hmac.compare_digest(echoed, challenge):
                raise MCPError(
                    _CALLBACK_ERROR,
                    "CallbackEndpointError",
                    {"reason": "challenge_failed"},
                )
            self._verified_callbacks[cache_key] = now_monotonic + _VERIFICATION_CACHE_SECONDS
        except MCPError:
            raise
        except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
            raise MCPError(
                _CALLBACK_ERROR,
                "CallbackEndpointError",
                {"reason": "timeout"},
            ) from exc
        except (httpx.HTTPError, ValueError, OSError) as exc:
            raise MCPError(
                _CALLBACK_ERROR,
                "CallbackEndpointError",
                {"reason": "challenge_failed", "detail": str(exc)},
            ) from exc

    async def subscribe(self, params: EventsSubscribeParams) -> dict[str, Any]:
        if params.name != _EVENT_NAME:
            raise ValueError(f"unsupported event name: {params.name}")
        arguments = self.validate_arguments(params.arguments)
        if params.cursor is not None:
            raise ValueError("ha.command does not support replay cursors.")
        _decode_secret(params.delivery.secret)
        await _validate_public_https_url(params.delivery.url)
        sub_id = _subscription_id(self.principal, params.delivery.url, params.name, arguments)
        await self._verify_callback(sub_id, params.delivery.url, params.delivery.secret)

        explicit_ttl = "ttl_ms" in params.model_fields_set
        if explicit_ttl and params.ttl_ms is None:
            refresh_before: str | None = None
        else:
            requested = _DEFAULT_TTL_MS if params.ttl_ms is None else int(params.ttl_ms)
            requested = max(_MIN_TTL_MS, min(requested, _MAX_TTL_MS))
            refresh_before = _iso(_utc_now() + timedelta(milliseconds=requested))

        async with self._lock:
            existing = self._subscriptions.get(sub_id)
            previous_secret = None
            previous_valid_until = None
            if existing and existing.get("secret") != params.delivery.secret:
                previous_secret = existing.get("secret")
                previous_valid_until = _iso(_utc_now() + timedelta(seconds=_ROTATION_SECONDS))
            record = {
                "id": sub_id,
                "principal": self.principal,
                "name": params.name,
                "arguments": arguments,
                "url": params.delivery.url,
                "secret": params.delivery.secret,
                "previous_secret": previous_secret,
                "previous_valid_until": previous_valid_until,
                "refresh_before": refresh_before,
                "updated_at": _iso(_utc_now()),
                "created_at": existing.get("created_at") if existing else _iso(_utc_now()),
            }
            self._subscriptions[sub_id] = record
            self._save_locked()

        return {"id": sub_id, "refreshBefore": refresh_before, "cursor": None, "truncated": False}

    async def unsubscribe(self, params: EventsUnsubscribeParams) -> dict[str, Any]:
        if params.name != _EVENT_NAME:
            return {}
        arguments = self.validate_arguments(params.arguments)
        sub_id = _subscription_id(self.principal, params.delivery.url, params.name, arguments)
        async with self._lock:
            if self._subscriptions.pop(sub_id, None) is not None:
                self._save_locked()
        return {}

    def _signatures(self, record: dict[str, Any], event_id: str, timestamp: int, body: bytes) -> str:
        signatures = [_standard_webhook_signature(record["secret"], event_id, timestamp, body)]
        previous_secret = record.get("previous_secret")
        previous_valid_until = record.get("previous_valid_until")
        if previous_secret and previous_valid_until:
            try:
                valid_until = datetime.fromisoformat(str(previous_valid_until).replace("Z", "+00:00"))
                if valid_until > _utc_now():
                    signatures.append(_standard_webhook_signature(previous_secret, event_id, timestamp, body))
            except ValueError:
                pass
        return " ".join(signatures)

    async def _deliver(self, record: dict[str, Any], event: dict[str, Any], body: bytes) -> dict[str, Any]:
        url = str(record["url"])
        for attempt in range(3):
            try:
                await _validate_public_https_url(url)
                timestamp = int(_utc_now().timestamp())
                headers = {
                    "Content-Type": "application/json",
                    "webhook-id": event["eventId"],
                    "webhook-timestamp": str(timestamp),
                    "webhook-signature": self._signatures(record, event["eventId"], timestamp, body),
                    "X-MCP-Subscription-Id": record["id"],
                }
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(10.0),
                    follow_redirects=False,
                    trust_env=False,
                ) as http:
                    response = await http.post(url, content=body, headers=headers)
                if 200 <= response.status_code < 300:
                    return {"subscription_id": record["id"], "accepted": True, "status": response.status_code}
                if response.status_code in {410, 413}:
                    if response.status_code == 410:
                        async with self._lock:
                            self._subscriptions.pop(record["id"], None)
                            self._save_locked()
                    return {"subscription_id": record["id"], "accepted": False, "status": response.status_code}
                if response.status_code < 500 and response.status_code != 429:
                    return {"subscription_id": record["id"], "accepted": False, "status": response.status_code}
            except Exception as exc:
                self.last_error = f"event delivery failed: {exc}"
                logger.warning("MCP event delivery attempt %s failed: %s", attempt + 1, exc)
            if attempt < 2:
                await asyncio.sleep(2**attempt)
        return {"subscription_id": record["id"], "accepted": False, "status": None}

    async def emit(self, name: str, data: dict[str, Any]) -> dict[str, Any]:
        if name != _EVENT_NAME:
            raise ValueError(f"unsupported event name: {name}")
        text = data.get("text")
        source = data.get("source")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("event data.text must be a non-empty string.")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("event data.source must be a non-empty string.")
        allowed = {"text", "source", "conversation_id", "device_id", "event_context_id"}
        payload = {key: value for key, value in data.items() if key in allowed and isinstance(value, str)}
        payload["text"] = text.strip()
        payload["source"] = source.strip()

        event = {
            "eventId": "evt_" + secrets.token_hex(16),
            "name": name,
            "timestamp": _iso(_utc_now()),
            "data": payload,
            "cursor": None,
        }
        body = json.dumps(event, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(body) > _MAX_EVENT_BYTES:
            raise ValueError("event payload exceeds 256 KiB.")

        now = _utc_now()
        async with self._lock:
            records = [
                dict(record)
                for record in self._subscriptions.values()
                if record.get("name") == name
                and self._active(record, now)
                and self._matches(record.get("arguments") or {}, payload)
            ]

        deliveries = (
            await asyncio.gather(
                *(self._deliver(record, event, body) for record in records),
                return_exceptions=False,
            )
            if records
            else []
        )
        self.last_event = {
            "event_id": event["eventId"],
            "name": name,
            "timestamp": event["timestamp"],
            "source": payload["source"],
            "deliveries": deliveries,
        }
        return {
            "ok": True,
            "event_id": event["eventId"],
            "matching_subscriptions": len(records),
            "deliveries": deliveries,
        }

    def status(self) -> dict[str, Any]:
        now = _utc_now()
        active = sum(1 for record in self._subscriptions.values() if self._active(record, now))
        return {
            "ok": True,
            "protocol_version": _PROTOCOL_VERSION,
            "event": _EVENT_NAME,
            "subscriptions_total": len(self._subscriptions),
            "subscriptions_active": active,
            "ha_listener_connected": self.ha_listener_connected,
            "last_event": self.last_event,
            "last_error": self.last_error,
        }


class HomeAssistantEventBridge:
    def __init__(self, store: SubscriptionStore) -> None:
        self.store = store
        self.base_url = os.environ.get("HA_URL", "http://supervisor/core")
        self.token = os.environ.get("HA_TOKEN") or os.environ.get("SUPERVISOR_TOKEN", "")
        self.websocket_url = _websocket_url(self.base_url)

    async def run(self) -> None:
        delay = 1
        while True:
            try:
                if not self.token:
                    raise RuntimeError("Home Assistant token is unavailable.")
                async with websockets.connect(
                    self.websocket_url,
                    open_timeout=10,
                    close_timeout=5,
                    max_size=2 * 1024 * 1024,
                ) as ws:
                    hello = json.loads(await ws.recv())
                    if hello.get("type") != "auth_required":
                        raise RuntimeError(f"unexpected Home Assistant WebSocket handshake: {hello}")
                    await ws.send(json.dumps({"type": "auth", "access_token": self.token}))
                    auth = json.loads(await ws.recv())
                    if auth.get("type") != "auth_ok":
                        raise RuntimeError("Home Assistant WebSocket authentication failed.")
                    await ws.send(
                        json.dumps(
                            {"id": 1, "type": "subscribe_events", "event_type": "chatgpt_command"}
                        )
                    )
                    while True:
                        response = json.loads(await ws.recv())
                        if response.get("id") == 1 and response.get("type") == "result":
                            if not response.get("success"):
                                raise RuntimeError(
                                    f"Home Assistant event subscription failed: {response.get('error')}"
                                )
                            break
                    self.store.ha_listener_connected = True
                    self.store.last_error = None
                    delay = 1
                    logger.info(
                        "MCP Events bridge subscribed to Home Assistant event chatgpt_command"
                    )

                    async for raw in ws:
                        message = json.loads(raw)
                        if message.get("type") != "event" or message.get("id") != 1:
                            continue
                        event = message.get("event") or {}
                        event_data = event.get("data") or {}
                        text = event_data.get("text")
                        if not isinstance(text, str) or not text.strip():
                            logger.warning("Ignored chatgpt_command without non-empty text")
                            continue
                        payload: dict[str, Any] = {
                            "text": text,
                            "source": str(event_data.get("source") or "home_assistant"),
                        }
                        for key in ("conversation_id", "device_id"):
                            value = event_data.get(key)
                            if isinstance(value, str) and value:
                                payload[key] = value
                        context = event.get("context") or {}
                        if isinstance(context.get("id"), str) and context["id"]:
                            payload["event_context_id"] = context["id"]
                        await self.store.emit(_EVENT_NAME, payload)
            except asyncio.CancelledError:
                self.store.ha_listener_connected = False
                raise
            except Exception as exc:
                self.store.ha_listener_connected = False
                self.store.last_error = f"Home Assistant event bridge: {exc}"
                logger.warning("Home Assistant MCP Events bridge disconnected: %s", exc)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)


class MCPEventsExtension(Extension):
    identifier = "com.jacksongomes/ha-events"

    def __init__(self, store: SubscriptionStore) -> None:
        self.store = store

    def settings(self) -> dict[str, Any]:
        return {"protocolVersion": _PROTOCOL_VERSION, "event": _EVENT_NAME}

    def tools(self):
        return (
            ToolBinding(
                self.mcp_events_status,
                kwargs={
                    "description": "Show MCP Events subscription and Home Assistant event-bridge status without exposing webhook secrets."
                },
            ),
            ToolBinding(
                self.emit_test_chatgpt_event,
                kwargs={
                    "description": "Emit one test ha.command MCP event to the subscribed ChatGPT chat. Use only for diagnostics."
                },
            ),
        )

    def methods(self):
        versions = frozenset({_PROTOCOL_VERSION})
        return (
            MethodBinding(
                "events/list",
                EventsListParams,
                self.events_list,
                protocol_versions=versions,
            ),
            MethodBinding(
                "events/subscribe",
                EventsSubscribeParams,
                self.events_subscribe,
                protocol_versions=versions,
            ),
            MethodBinding(
                "events/unsubscribe",
                EventsUnsubscribeParams,
                self.events_unsubscribe,
                protocol_versions=versions,
            ),
        )

    async def events_list(
        self, _ctx: ServerRequestContext[Any, Any], params: EventsListParams
    ) -> HandlerResult:
        if params.cursor is not None:
            return {"events": [], "nextCursor": None}
        return {"events": [self.store.event_definition()], "nextCursor": None}

    async def events_subscribe(
        self, _ctx: ServerRequestContext[Any, Any], params: EventsSubscribeParams
    ) -> HandlerResult:
        try:
            return await self.store.subscribe(params)
        except MCPError:
            raise
        except ValueError as exc:
            raise MCPError(-32602, "Invalid params", {"detail": str(exc)}) from exc

    async def events_unsubscribe(
        self, _ctx: ServerRequestContext[Any, Any], params: EventsUnsubscribeParams
    ) -> HandlerResult:
        try:
            return await self.store.unsubscribe(params)
        except ValueError as exc:
            raise MCPError(-32602, "Invalid params", {"detail": str(exc)}) from exc

    async def mcp_events_status(self) -> dict[str, Any]:
        """Return MCP Events and Home Assistant bridge status without secrets."""
        return self.store.status()

    async def emit_test_chatgpt_event(
        self, text: str, source: str = "manual_test"
    ) -> dict[str, Any]:
        """Emit a test ha.command event to matching ChatGPT subscriptions."""
        return await self.store.emit(_EVENT_NAME, {"text": text, "source": source})


class MCPEventsRuntime:
    def __init__(self) -> None:
        self.store = SubscriptionStore()
        self.extension = MCPEventsExtension(self.store)
        self.bridge = HomeAssistantEventBridge(self.store)

    async def capability_middleware(
        self,
        ctx: ServerRequestContext[Any, Any],
        call_next: CallNext,
    ) -> HandlerResult:
        result = await call_next(ctx)
        if ctx.method != "server/discover" or result is None:
            return result
        if isinstance(result, BaseModel):
            payload = result.model_dump(by_alias=True, exclude_none=True)
        elif isinstance(result, dict):
            payload = dict(result)
        else:
            return result
        capabilities = dict(payload.get("capabilities") or {})
        capabilities["events"] = {}
        payload["capabilities"] = capabilities
        return payload

    @asynccontextmanager
    async def lifespan(self, _server):
        task = asyncio.create_task(self.bridge.run(), name="ha-mcp-events-bridge")
        try:
            yield {"mcp_events": self}
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
