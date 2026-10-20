"""Start isolated Bruce add-on in MQTT diagnostics or MCP-tunnel mode."""
from __future__ import annotations

import json
import os
import re
import sys
import time

OPTIONS = "/data/options.json"
MAPPING = {
    "tunnel_id": "CONTROL_PLANE_TUNNEL_ID",
    "tunnel_api_key": "CONTROL_PLANE_API_KEY",
    "device_id": "BRUCE_DEVICE_ID",
    "mqtt_host": "BRUCE_MQTT_HOST",
    "mqtt_user": "BRUCE_MQTT_USER",
    "mqtt_password": "BRUCE_MQTT_PASSWORD",
}


def load_options() -> tuple[dict, bool, dict]:
    try:
        with open(OPTIONS, encoding="utf-8") as f:
            options = json.load(f)
        probe_only = options.get("mqtt_probe_only", True)
        if not isinstance(probe_only, bool):
            raise ValueError("mqtt_probe_only must be true or false")
        required = ["device_id", "mqtt_host", "mqtt_user", "mqtt_password"]
        if not probe_only:
            required += ["tunnel_id", "tunnel_api_key"]
        missing = [
            key for key in required
            if not isinstance(options.get(key), str) or not options[key].strip()
        ]
        if missing:
            sys.exit("Bruce configuration incomplete: " + ", ".join(missing))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", options["device_id"]):
            raise ValueError("Invalid device_id")
        port = int(options.get("mqtt_port", 8883))
        if not 1 <= port <= 65535:
            raise ValueError("Invalid mqtt_port")
        if port == 1883:
            raise ValueError("Port 1883 is plaintext. Bruce requires TLS; use the broker's TLS listener.")
        env = os.environ.copy()
        for key, varname in MAPPING.items():
            if key in options and isinstance(options[key], str):
                env[varname] = options[key]
        env["BRUCE_MQTT_PORT"] = str(port)
        ca_pem = options.get("mqtt_ca_pem", "")
        if not isinstance(ca_pem, str) or len(ca_pem) > 20000:
            raise ValueError("Invalid mqtt_ca_pem")
        if ca_pem.strip():
            if "-----BEGIN CERTIFICATE-----" not in ca_pem:
                raise ValueError("mqtt_ca_pem must be a PEM certificate")
            ca_path = "/tmp/bruce-mqtt-ca.pem"
            fd = os.open(ca_path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(ca_pem)
            env["BRUCE_MQTT_CA_PEM"] = ca_path
        return options, probe_only, env
    except (OSError, ValueError, TypeError, KeyError) as exc:
        sys.exit("Unable to load Bruce add-on options: " + str(exc))


def main() -> None:
    _options, probe_only, env = load_options()
    if not probe_only:
        os.execve("/run.sh", ["/run.sh"], env)
    os.environ.update(env)
    from mqtt_transport import BruceTransport

    print("Bruce MQTT probe enabled: only outbound TLS MQTT; MCP tunnel not started", flush=True)
    transport = BruceTransport()
    last_status = None
    while True:
        status = transport.get_status()
        device = status["device"]
        current = (bool(status["mqtt_connected"]), bool(device["observed"]), bool(device["online"]))
        if current != last_status:
            print(
                "Bruce MQTT probe: broker_connected=%s, device_observed=%s, device_online=%s"
                % current,
                flush=True,
            )
            last_status = current
        time.sleep(5)


if __name__ == "__main__":
    main()
