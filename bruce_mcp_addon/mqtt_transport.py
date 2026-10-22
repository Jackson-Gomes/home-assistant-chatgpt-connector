"""Isolated Bruce MQTT/TLS request-response transport. No Home Assistant dependency."""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import threading
import time
import zlib
from typing import Any

import paho.mqtt.client as mqtt

DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
ALLOWED_OPS = frozenset({
    "info.get", "remote.capabilities", "remote.unlock", "remote.lock",
    "screen.brightness", "screen.color", "sound.tone", "sound.say",
    "menu.nav", "menu.option", "ir.send_saved", "rf.send_saved",
    "ir.receive", "rf.receive", "rf.scan",
    "wifi.scan", "ble.scan",
    "screen.snapshot", "ota.status", "ota.apply",
})

class BruceTransportError(RuntimeError):
    """A message could not be safely transported or acknowledged."""

class BruceTransport:
    def __init__(self) -> None:
        device = os.environ["BRUCE_DEVICE_ID"]
        if not DEVICE_ID_PATTERN.fullmatch(device):
            raise ValueError("Invalid BRUCE_DEVICE_ID")
        self.root = f"bruce/v1/{device}"
        self.status: dict[str, Any] = {"online": False, "observed": False}
        self.ota_status: dict[str, Any] = {}
        self.lock = threading.RLock()
        self.pending: dict[str, tuple[threading.Event, dict[str, Any]]] = {}
        self.screen_pending: dict[str, tuple[threading.Event, dict[int, bytes], dict[str, Any]]] = {}
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="bruce-mcp-" + secrets.token_hex(6),
            clean_session=True,
        )
        self.client.username_pw_set(os.environ["BRUCE_MQTT_USER"], os.environ["BRUCE_MQTT_PASSWORD"])
        # Use system CA certificates by default (Railway includes ca-certificates).
        # A private broker root CA can be supplied optionally via a PEM file.
        # Both modes keep TLS server and hostname verification enabled.
        ca_file = os.environ.get("BRUCE_MQTT_CA_PEM") or None
        if ca_file:
            self.client.tls_set(ca_certs=ca_file)
        else:
            self.client.tls_set()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect
        self.client.connect_async(
            os.environ["BRUCE_MQTT_HOST"],
            int(os.environ.get("BRUCE_MQTT_PORT", "8883")),
            keepalive=30,
        )
        self.client.loop_start()

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            client.subscribe([
                (self.root + "/reply", 1),
                (self.root + "/status", 1),
                (self.root + "/screen", 0),
                (self.root + "/ota", 0),
            ])

    def on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        with self.lock:
            self.status = {"online": False, "observed": False}

    def on_message(self, client, userdata, msg):
        if msg.topic not in {
            self.root + "/reply", self.root + "/status", self.root + "/screen", self.root + "/ota"
        } or len(msg.payload) > 2048:
            return
        try:
            data = json.loads(msg.payload)
        except (UnicodeDecodeError, ValueError):
            return
        if not isinstance(data, dict):
            return
        if msg.topic == self.root + "/status":
            with self.lock:
                self.status = {"online": data.get("online") is True, "observed": True}
            return
        if msg.topic == self.root + "/ota":
            with self.lock:
                self.ota_status = data
            return

        req = data.get("id")
        if not isinstance(req, str):
            return
        with self.lock:
            if msg.topic == self.root + "/reply":
                waiter = self.pending.get(req)
                if waiter:
                    event, reply = waiter
                    reply.update(data)
                    event.set()
                return

            slot = self.screen_pending.get(req)
            if slot is None:
                return
            event, pieces, meta = slot
            part, total = data.get("part"), data.get("total")
            length, crc = data.get("bytes"), data.get("crc32")
            if not isinstance(part, int) or not isinstance(total, int) or not isinstance(length, int) or not isinstance(crc, int):
                return
            if total < 1 or total > 32 or part < 0 or part >= total or length < 1 or length > 14000:
                return
            if meta and (meta["total"], meta["bytes"], meta["crc32"]) != (total, length, crc):
                return
            try:
                payload = base64.b64decode(data["data"], validate=True)
            except (KeyError, ValueError, TypeError):
                return
            if len(payload) > 420:
                return
            meta.update({"total": total, "bytes": length, "crc32": crc})
            pieces[part] = payload
            if len(pieces) == total:
                blob = b"".join(pieces.get(i, b"") for i in range(total))
                if len(blob) == length and (zlib.crc32(blob) & 0xFFFFFFFF) == crc:
                    meta["base64"] = base64.b64encode(blob).decode("ascii")
                    event.set()

    def get_status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "mqtt_connected": self.client.is_connected(),
                "device": dict(self.status),
                "last_ota": dict(self.ota_status),
            }

    def command(self, op: str, args: dict[str, Any] | None = None, timeout: float = 12) -> dict[str, Any]:
        if op not in ALLOWED_OPS:
            raise BruceTransportError("Operation not allowlisted")
        if not self.client.is_connected():
            raise BruceTransportError("MQTT broker is not connected")
        args = args or {}
        req = "req-" + secrets.token_hex(12)
        packet = json.dumps(
            {"v": 1, "id": req, "op": op, "args": args, "expires": int(time.time()) + 40},
            separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
        if len(packet) > 1400:
            raise BruceTransportError("Request exceeds firmware payload limit")
        event = threading.Event()
        reply: dict[str, Any] = {}
        screen_event = threading.Event()
        screen_parts: dict[int, bytes] = {}
        screen_meta: dict[str, Any] = {}
        with self.lock:
            self.pending[req] = (event, reply)
            if op == "screen.snapshot":
                self.screen_pending[req] = (screen_event, screen_parts, screen_meta)
        try:
            result = self.client.publish(self.root + "/cmd", packet, qos=1, retain=False)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                raise BruceTransportError("MQTT publish failed")
            if not event.wait(timeout):
                raise BruceTransportError(f"No device acknowledgment within {timeout}s")
            if op == "screen.snapshot" and reply.get("ok"):
                if not screen_event.wait(24):
                    raise BruceTransportError("Screen snapshot incomplete/CRC mismatch")
                reply["screen"] = {
                    "format": "bruce-tft-drawlog-v1",
                    "base64": screen_meta["base64"],
                    "bytes": screen_meta["bytes"],
                    "crc32": screen_meta["crc32"],
                }
            return reply
        finally:
            with self.lock:
                self.pending.pop(req, None)
                self.screen_pending.pop(req, None)

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()
