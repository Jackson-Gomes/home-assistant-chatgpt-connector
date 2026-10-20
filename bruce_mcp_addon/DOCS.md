# Bruce MCP Connector for Home Assistant OS

The Bruce add-on is independent of the original ChatGPT/Home Assistant connector.
It has **no** Home Assistant or Supervisor API access, no mounts, and **no inbound ports**.

## First: local LAN MQTT connectivity test (v0.1.1)

The new setting `mqtt_probe_only: true` (default) runs **only** an outbound authenticated MQTT TLS connection to your broker and watches Bruce status. It does not start the MCP tunnel and does not require a tunnel ID or API key. Start with this before registering a separate MCP tunnel.

Keep the existing Mosquitto broker configuration and Home Assistant connector unchanged. The following are configured **only in this Bruce add-on**:

- `device_id`: `t-embed-lab-1` (match the T-Embed configuration)
- `mqtt_host`: DNS hostname of the Mosquitto TLS server, resolvable by the HAOS container, **matching the TLS certificate's SAN**.
- `mqtt_port`: TLS listener port (often 8883, not plaintext 1883)
- `mqtt_user` and `mqtt_password`: dedicated MQTT credentials recognized by Mosquitto (set in the HAOS add-on Configuration tab, not in Git)
- `mqtt_ca_pem`: optional trusted CA PEM certificate content for a self-signed or privately issued broker certificate; use system-trusted CA if blank
- `mqtt_probe_only`: `true` for test mode

The T-Embed's BruceRemote firmware requires its own `/bruce-remote.json` with `enabled: true`, matching broker host/port, device ID and the **device's separate MQTT identity**, as well as the broker CA certificate in `/bruce-ca.pem`. Its MQTT endpoint is **TLS-only**. Standard unmodified Bruce firmware cannot connect to this protocol without BruceRemote compiled in.

### Expected logs

When the connector is connected to the broker:
```
Bruce MQTT probe: broker_connected=true, device_observed=false, device_online=false
```
After a configured T-Embed joins the same broker and publishes online status:
```
Bruce MQTT probe: broker_connected=true, device_observed=true, device_online=true
```

When missing values are detected the add-on refuses to connect. Certificates are verified; do not disable certificate/hostname verification to hide mismatches.

This test does not require internet broker services, public router ports or modifications to Mosquitto's configuration.

## Then: turn on MCP mode

Set `mqtt_probe_only: false` and provide a **new, separate** `tunnel_id` and `tunnel_api_key` for Bruce in ChatGPT. The existing Home Assistant tunnel must not be reused. With this option, the existing `run.sh` starts MCP and the outbound secure tunnel as before.

Do not expose ports 8000 or 1883/8883 publicly. A LAN-only broker will not be reachable to an ESP32 away from the LAN without a secure VPN routing solution.
