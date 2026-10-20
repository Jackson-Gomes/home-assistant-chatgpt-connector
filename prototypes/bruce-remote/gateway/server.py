"""Local-only, authenticated HTTP to secure MQTT bridge. Not a public MCP endpoint."""
import base64
import hmac
import json
import os
import secrets
import threading
import time
import zlib

import paho.mqtt.client as mqtt
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

OPS = frozenset((
    "info.get", "remote.capabilities", "remote.unlock", "remote.lock",
    "screen.brightness", "screen.color", "sound.tone", "sound.say",
    "menu.nav", "menu.option", "ir.send_saved", "rf.send_saved",
    "ir.receive", "rf.receive", "rf.scan",
    "screen.snapshot", "ota.status", "ota.apply"
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
        self.screen_waiters = {}
        self.last_ota = {}
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
            client.subscribe(self.topic + "/screen", qos=0)
            client.subscribe(self.topic + "/ota", qos=0)

    def on_message(self, client, userdata, msg):
        if msg.topic not in {self.topic + "/reply", self.topic + "/screen", self.topic + "/ota"} or len(msg.payload)>2048:
            return
        try:
            data=json.loads(msg.payload)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(data, dict) or not isinstance(data.get("id"), str):
            return
        with self.lock:
            if msg.topic == self.topic + "/reply":
                waiter=self.waiters.get(data["id"])
                if waiter:
                    event,result=waiter
                    result.update(data)
                    event.set()
            elif msg.topic == self.topic + "/screen":
                pending=self.screen_waiters.get(data["id"])
                if not pending:
                    return
                event,parts,meta=pending
                index,total=data.get("part"),data.get("total")
                if not isinstance(index,int) or not isinstance(total,int) or total<1 or total>32 or index<0 or index>=total:
                    return
                try:
                    chunk=base64.b64decode(data["data"],validate=True)
                except (KeyError,TypeError,ValueError):
                    return
                if len(chunk)>420:
                    return
                if meta and (meta["total"]!=total or meta["crc32"]!=data.get("crc32")):
                    return
                if not meta:
                    meta.update({"total":total,"crc32":data.get("crc32"),"bytes":data.get("bytes")})
                parts[index]=chunk
                if len(parts)==total:
                    blob=b"".join(parts[i] for i in range(total))
                    if len(blob)==meta.get("bytes") and (zlib.crc32(blob)&0xFFFFFFFF)==meta["crc32"]:
                        meta["base64"]=base64.b64encode(blob).decode("ascii")
                        event.set()
            else:
                self.last_ota=data

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
        screen_event = threading.Event()
        screen_parts = {}
        screen_meta = {}
        with self.lock:
            self.waiters[rid] = (event, output)
            if request.op == "screen.snapshot":
                self.screen_waiters[rid] = (screen_event, screen_parts, screen_meta)
        try:
            info = self.client.publish(self.topic+"/cmd", payload, qos=1, retain=False)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                raise HTTPException(503, "MQTT publish failed")
            timeout = 24 if request.op in {"ir.receive","rf.receive","rf.scan"} else 9
            if not event.wait(timeout):
                raise HTTPException(504, "Device did not acknowledge before timeout")
            if request.op == "screen.snapshot" and output.get("ok"):
                if not screen_event.wait(20):
                    raise HTTPException(504, "Screen snapshot was incomplete or corrupted")
                output["screen"] = {
                    "format": "bruce-tft-drawlog-v1",
                    "base64": screen_meta["base64"],
                    "bytes": screen_meta["bytes"],
                    "crc32": screen_meta["crc32"],
                }
            return output
        finally:
            with self.lock:
                self.waiters.pop(rid, None)
                self.screen_waiters.pop(rid, None)

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

@app.get("/ota/status")
def ota_status(authorization: str | None = Header(default=None)):
    auth(authorization)
    with bridge.lock:
        return dict(bridge.last_ota)
