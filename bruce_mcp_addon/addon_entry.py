"""Launch Bruce independently using Supervisor add-on options, without HA API access."""
import json
import os
import re
import sys

OPTIONS = "/data/options.json"
MAPPING = {
    "tunnel_id": "CONTROL_PLANE_TUNNEL_ID",
    "tunnel_api_key": "CONTROL_PLANE_API_KEY",
    "device_id": "BRUCE_DEVICE_ID",
    "mqtt_host": "BRUCE_MQTT_HOST",
    "mqtt_user": "BRUCE_MQTT_USER",
    "mqtt_password": "BRUCE_MQTT_PASSWORD",
}
try:
    with open(OPTIONS, encoding="utf-8") as f:
        options = json.load(f)
    missing = [key for key in MAPPING if not isinstance(options.get(key), str) or not options[key].strip()]
    if missing:
        sys.exit("Bruce configuration incomplete: " + ", ".join(missing))
    device_id = options["device_id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", device_id):
        sys.exit("Invalid device_id")
    port = int(options.get("mqtt_port", 8883))
    if port < 1 or port > 65535:
        sys.exit("Invalid mqtt_port")
except (OSError, ValueError, TypeError) as exc:
    sys.exit("Unable to load valid Bruce add-on options: " + str(exc))

env = os.environ.copy()
for key, name in MAPPING.items():
    env[name] = options[key]
env["BRUCE_MQTT_PORT"] = str(port)
# TLS validation and hostname verification are enforced in mqtt_transport.py.
# Secrets live in /data/options.json and process environment, never in repository.
os.execve("/run.sh", ["/run.sh"], env)
