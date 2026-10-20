"""Local-only, authenticated HTTP to secure MQTT bridge. Not a public MCP endpoint."""
import hmac
import json
import os
import secrets
import threading
import time

import paho.mqtt.client as mqtt
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

OPS = frozenset((
    "info.get", "remote.capabilities", "remote.unlock", "remote.lock",
    "screen.brightness", "screen.color", "sound.tone", "sound.say",
    "menu.nav", "menu.option", "ir.send_saved", "rf.send_saved",
    "rf.receive", "rf.scan"
))

class Command(BaseModel):
    op: str = Field(min_length=3, max_length=40)
    args: dict = Field(default_factory=dict)

class Bridge:
    def __init__(self):
        self.token = os.environ["BRUCE_GATEWAY_TOKEN"]
        self.device = os.environ["BRUCE_DEVICE_ID"]
        if not self.device or len(self.token) < 32:
            raise RuntimeError("Set a device ID and a strong BRUCE_GATEWAY_TOKEN")
        self.topic = f"bruce/v1/{self.device}"
        self.waiters = {}
        self.lock = threading.Lock()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="gateway-" + secrets.token_hex(6))
        self.client.username_pw_set(os.environ["BRUCE_MQTT_USER"], os.environ["BRUCE_MQTT_PASSWORD"])
        self.client.tls_set(ca_certs=os.environ["BRUCE_MQTT_CA_PEM"])
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.connect(os.environ["BRUCE_MQTT_HOST"], int(os.environ.get("BRUCE_MQTT_PORT","8883")), 30)
        self.client.loop_start()

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            client.subscribe(self.topic + "/reply", qos=1)
            client.subscribe(self.topic + "/status", qos=1)

    def on_message(self, client, userdata, msg):
        if msg.topic != self.topic + "/reply" or len(msg.payload) > 2048:
            return
        try:
            reply = json.loads(msg.payload)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(reply, dict) or not isinstance(reply.get("id"), str):
            return
        with self.lock:
            waiter = self.waiters.get(reply["id"])
            if waiter:
                event, result = waiter
                result.update(reply)
                event.set()

    def call(self, request: Command):
        if request.op not in OPS:
            raise HTTPException(400, "Operation not allowed")
        if not self.client.is_connected():
            raise HTTPException(503, "MQTT disconnected")
        rid = "req-" + secrets.token_hex(12)
        payload = json.dumps({
            "v": 1, "id": rid, "op": request.op, "args": request.args,
            "expires": int(time.time()) + 30,
        }, separators=(",",":")).encode()
        if len(payload) > 1400:
            raise HTTPException(400, "Message exceeds device limit")
        event = threading.Event()
        output = {}
        with self.lock:
            self.waiters[rid] = (event, output)
        try:
            info = self.client.publish(self.topic+"/cmd", payload, qos=1, retain=False)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                raise HTTPException(503, "MQTT publish failed")
            if not event.wait(9):
                raise HTTPException(504, "Device did not acknowledge within 9s")
            return output
        finally:
            with self.lock:
                self.waiters.pop(rid, None)

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()

app = FastAPI(title="BruceRemote local gateway", docs_url=None, redoc_url=None, openapi_url=None)
bridge = None

@app.on_event("startup")
def start():
    global bridge
    bridge = Bridge()

@app.on_event("shutdown")
def stop():
    if bridge:
        bridge.close()

def auth(value):
    if bridge is None or value is None or not hmac.compare_digest(value, "Bearer " + bridge.token):
        raise HTTPException(401, "Unauthorized")

@app.get("/health")
def health(authorization: str | None = Header(default=None)):
    auth(authorization)
    return {"gateway":"running", "mqtt":bridge.client.is_connected()}

@app.post("/command")
def command(request: Command, authorization: str | None = Header(default=None)):
    auth(authorization)
    return bridge.call(request)
