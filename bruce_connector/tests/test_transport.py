"""No hardware/network required: exercise the standalone MQTT request protocol."""
import ast
import base64
import json
import os
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mqtt_transport import ALLOWED_OPS, BruceTransport, BruceTransportError


class FakeMqttClient:
    def __init__(self, *args, **kwargs):
        self.on_message = None
        self.started = False
        self.published = []
        self.on_publish_hook = None

    def username_pw_set(self, *args):
        pass

    def tls_set(self, *args, **kwargs):
        # System trust is secure by default; explicit CA works for private CAs.
        if "ca_certs" in kwargs and kwargs["ca_certs"] != "/tmp/test-ca.pem":
            raise AssertionError("Unexpected certificate authority")

    def connect_async(self, *args, **kwargs):
        self.started = True

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def is_connected(self):
        return self.started

    def publish(self, topic, payload, qos, retain):
        self.published.append((topic, payload, qos, retain))
        if self.on_publish_hook:
            self.on_publish_hook(topic, payload)
        return types.SimpleNamespace(rc=0)


ENV = {
    "BRUCE_DEVICE_ID": "t-embed-lab-1",
    "BRUCE_MQTT_USER": "bruce-test",
    "BRUCE_MQTT_PASSWORD": "not-secret",
    "BRUCE_MQTT_HOST": "mqtt-test.example.com",
    "BRUCE_MQTT_PORT": "8883",
}


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.client_patch = patch("mqtt_transport.mqtt.Client", FakeMqttClient)
        self.client_patch.start()
        self.transport = BruceTransport()

    def tearDown(self):
        self.transport.close()
        self.client_patch.stop()
        self.env.stop()

    def emit(self, suffix, data):
        self.transport.on_message(
            self.transport.client, None,
            types.SimpleNamespace(topic=self.transport.root + "/" + suffix,
                                  payload=json.dumps(data).encode()),
        )

    def test_private_ca_can_be_configured(self):
        self.transport.close()
        with patch.dict(os.environ, {"BRUCE_MQTT_CA_PEM": "/tmp/test-ca.pem"}):
            second = BruceTransport()
            self.assertTrue(second.client.is_connected())
            second.close()

    def test_allowed_operations_not_unrestricted(self):
        self.assertIn("info.get", ALLOWED_OPS)
        self.assertIn("screen.snapshot", ALLOWED_OPS)
        self.assertNotIn("raw.cli", ALLOWED_OPS)
        self.assertNotIn("badusb.run", ALLOWED_OPS)
        with self.assertRaises(BruceTransportError):
            self.transport.command("arbitrary.shell", {})

    def test_response_matching_and_mqtt_retain_false(self):
        def reply(topic, payload):
            doc = json.loads(payload)
            self.assertEqual(doc["op"], "info.get")
            self.assertGreater(doc["expires"], 0)
            self.emit("reply", {"v": 1, "id": doc["id"], "ok": True, "result": {"heap": 100}})
        self.transport.client.on_publish_hook = reply
        result = self.transport.command("info.get", {}, timeout=0.2)
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["heap"], 100)
        self.assertFalse(self.transport.client.published[0][3])
        self.assertEqual(self.transport.client.published[0][2], 1)

    def test_screen_chunk_crc_round_trip(self):
        blob = b"\xaa\x08\x63\x01\x40\x00\xaa\x00"
        crc = zlib.crc32(blob) & 0xFFFFFFFF
        def reply(topic, payload):
            doc = json.loads(payload)
            self.emit("reply", {"id": doc["id"], "ok": True})
            self.emit("screen", {
                "id": doc["id"], "part": 0, "total": 1, "bytes": len(blob),
                "crc32": crc, "data": base64.b64encode(blob).decode(),
            })
        self.transport.client.on_publish_hook = reply
        r = self.transport.command("screen.snapshot", {}, timeout=0.1)
        self.assertEqual(base64.b64decode(r["screen"]["base64"]), blob)

    def test_screen_rejects_corrupted_crc(self):
        req = "req-abc123456"
        event, pieces, meta = threading.Event(), {}, {}
        self.transport.screen_pending[req] = (event, pieces, meta)
        self.emit("screen", {
            "id": req, "part": 0, "total": 1, "bytes": 3,
            "crc32": 1, "data": base64.b64encode(b"bad").decode(),
        })
        self.assertFalse(event.is_set())

    def test_availability_status(self):
        self.emit("status", {"online": True})
        self.assertTrue(self.transport.get_status()["device"]["online"])
        self.assertTrue(self.transport.get_status()["device"]["observed"])

    def test_mcp_server_does_not_import_ha(self):
        source = (ROOT / "mcp_server.py").read_text()
        tree = ast.parse(source)
        imported = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertNotIn("ha_client", imported)
        self.assertNotIn("homeassistant", imported)
        self.assertIn('FastMCP("bruce-chatgpt-connector"', source)


if __name__ == "__main__":
    unittest.main()
