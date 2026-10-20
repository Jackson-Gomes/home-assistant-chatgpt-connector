# Bruce MCP Connector for Home Assistant OS

This is a **separate add-on**, not an extension of the existing ChatGPT/Home Assistant connector.
Its only purpose is to run the stand-alone Bruce connector on the same always-on machine.

- Runs `mcp_server.py` bound to `127.0.0.1:8000` within its own container.
- Uses an **independent OpenAI Secure MCP Tunnel** with outbound connections.
- Uses a TLS-verified MQTT broker with an outbound connection.
- Has no published ports, Home Assistant API token, Supervisor API permissions or `/config` mount.
- Does not modify, restart or depend upon the existing ChatGPT Connector add-on.

## Install

The existing GitHub repository is already an HA add-on repository.
Open Settings > Apps (Add-ons) > App Store, refresh/reload that repository, and install **Bruce MCP Connector**.

Before starting, set all fields in the add-on Configuration tab:

| Option | Value |
|---|---|
| tunnel_id | NEW Bruce-specific Secure MCP Tunnel ID (do not reuse Home Assistant's) |
| tunnel_api_key | Matching NEW Bruce tunnel API key |
| device_id | The configured BruceRemote ID, e.g. t-embed-lab-1 |
| mqtt_host | TLS MQTT broker DNS hostname (reachable from the add-on) |
| mqtt_port | 8883, unless your TLS broker uses a different port |
| mqtt_user | Bruce connector's own MQTT user (not device user) |
| mqtt_password | Dedicated strong MQTT password |

Do not commit secrets. Do not make MCP port 8000 or any other port public.

The current MQTT transport **requires TLS with a trusted certificate and hostname validation**. The Home Assistant Mosquitto add-on only works as the broker here when its TLS listener on 8883 is properly configured, has a certificate valid for the DNS hostname used, and the user/account has appropriate access. Do not simply point this connector at plaintext port 1883 without changing the transport design.

If your T-Embed needs to work on other Wi-Fi networks, a LAN-only MQTT broker isn't reachable directly. Use a separately secured externally reachable TLS MQTT broker or appropriate private network routing. Do not open unprotected router ports.

## Operational status

Until the new tunnel and broker credentials are supplied, the add-on will stop with a configuration-incomplete error; this has no impact on other add-ons. Verify its own logs after configuration. This package has no HA Supervisor management operations and does not install itself.
