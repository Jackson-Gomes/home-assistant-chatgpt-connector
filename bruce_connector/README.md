# Bruce ChatGPT Connector — standalone prototype

> **BruceRemote v0.3 update:** See [Remote OTA and scans](../prototypes/bruce-remote/REMOTE_OTA_V03.md) for the current GitHub Actions OTA image distribution, locally authorized passive Wi-Fi/BLE scans and remote menu controls. New features require an updated firmware image and local opt-in. The rest of this prototype README contains historical setup notes.

Independent MCP + TLS MQTT connector for the LilyGO T-Embed CC1101 Plus running BruceRemote.

**No Home Assistant runtime, Supervisor API, HA credentials or HA services required.**

This directory is staged in branch feature/bruce-standalone-mcp only because the GitHub connection currently does not offer repository creation. It can be moved wholesale into a new dedicated bruce-chatgpt-connector repository. Do not merge the experimental branch into the Home Assistant connector's main branch.

## What was reused

- The FastMCP / Streamable HTTP server architecture from chatgpt_connector/server.py.
- The outbound Secure MCP Tunnel startup pattern from chatgpt_connector/run.sh and Dockerfile.
- The TLS MQTT request/reply, bounded correlation and screen chunk validation concepts from prototypes/bruce-remote/gateway/server.py.

The connector does not import or contact Home Assistant. It does not require the Home Assistant machine to be running.

## Architecture

ChatGPT -> separate authenticated MCP tunnel -> standalone Bruce MCP server -> TLS MQTT broker -> remote T-Embed.

Both the ESP32 and the connector make outgoing network connections only. The MQTT broker must be reachable from both the ESP32 and the connector; a local-only broker will not work if the T-Embed uses another Wi-Fi unless routing is set up.

## Setup

1. Obtain a private Internet-reachable TLS MQTT broker on port 8883 with broker hostname and root CA PEM. Create two MQTT identities, one for the ESP32 and one for this connector, each scoped to a single device topic.
2. Register a NEW Secure MCP Tunnel for Bruce in ChatGPT. Do not share the existing Home Assistant connector tunnel identity.
3. Copy .env.example to .env, set all values, and place the broker trusted CA certificate at certs/broker-ca.pem. Never commit your real credentials.
4. From this directory, run: docker compose up -d --build
5. Inspect startup logs with: docker compose logs -f
6. From the locally authenticated Bruce WebUI, upload /bruce-remote.json and /bruce-ca.pem to LittleFS. The JSON must have enabled=true and the corresponding broker credentials. Reboot.
7. Use ChatGPT's separately connected Bruce MCP tools to test bruce_status, bruce_info and bruce_capabilities.

The container does not publish inbound ports. Its MCP endpoint is bound to 127.0.0.1:8000 inside the container and exposed externally ONLY through the authenticated Secure MCP Tunnel.

The host running Docker must remain powered and online. This can be a separate mini-PC, VPS, or Windows PC running Docker; it does not have to run Home Assistant.

## Broker ACLs

For device ID t-embed-lab-1:

ESP32 identity:
- subscribe: bruce/v1/t-embed-lab-1/cmd
- publish: bruce/v1/t-embed-lab-1/reply, /status, /screen, /ota

Bruce connector identity:
- publish: bruce/v1/t-embed-lab-1/cmd
- subscribe: bruce/v1/t-embed-lab-1/reply, /status, /screen, /ota

Restrict the ACLs to explicit topics. Disable anonymous access. Use unique credentials. MQTT commands are not retained; status may be retained.

## Supported tools

bruce_status, bruce_info, bruce_capabilities, bruce_unlock, bruce_lock,
bruce_brightness, bruce_screen_color, bruce_tone, bruce_say,
bruce_navigate, bruce_menu_option, bruce_ir_send_saved, bruce_rf_send_saved,
bruce_ir_receive, bruce_rf_receive, bruce_rf_scan, bruce_screen_snapshot,
bruce_ota_status, bruce_ota_apply, bruce_wifi_scan, bruce_ble_scan.

The already-installed Build #23 of BruceRemote has the initial 14 operations. Screen snapshots, IR/RF capture results, and OTA are part of the later v0.2 experimental firmware, which requires separate USB migration to the dual-OTA partition layout. Unsupported operations return a device error rather than being simulated.

The v0.3 overlay requires device-local JSON opt-in for remote menus, passive scans and unattended OTA. Saved IR/RF transmissions retain physical unlock. OTA requires explicit confirmation, known expected SHA-256, locally pinned HTTPS URL/CA, and dual OTA partitions, but no encoder hold **after migration to v0.3**. Results from the Bruce CLI queue are reported as queued, not necessarily executed.

## Deployment status and security

Code is staged; no MQTT broker account or Secure MCP Tunnel has been provisioned automatically. The standalone connector is not yet connected to the user's ChatGPT session.

Do not expose the MCP server directly to the public Internet, or use unsecured anonymous MQTT brokers. Hardware behavior, actual broker connectivity and OTA recovery are not yet validated on the physical board.
