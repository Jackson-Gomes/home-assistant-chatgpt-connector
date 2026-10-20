# Railway cloud deployment — Bruce MCP worker

This service runs in Railway **24/7 while the subscription/usage budget permits**. The T-Embed, MQTT broker, and connector can be on unrelated Internet networks. No PC or Home Assistant is involved.

## Deployment source
- GitHub repo: Jackson-Gomes/home-assistant-chatgpt-connector
- Branch: feature/bruce-standalone-mcp
- Service Root Directory: /bruce_connector
- Builder: Dockerfile (detected as root-of-source Dockerfile)
- Single replica, always running; no schedule or sleep mode
- No HTTP domain, no TCP proxy, no exposed/public ports
- Start command: use image Dockerfile CMD (/run.sh)
- Environment: production or another named Railway environment
- App name suggestion: bruce-mcp-cloud

Railway's old config-as-code (railway.json/toml) is deprecated for *new* services in late 2026, so this project deliberately uses the Dockerfile plus Railway's service Settings instead of adding new deprecated configuration files. See https://docs.railway.com/config-as-code

## Required Railway Variables (enter them in Railway, NEVER commit values to GitHub)

CONTROL_PLANE_TUNNEL_ID: ID of a **new, dedicated** OpenAI Secure MCP Tunnel for Bruce.
CONTROL_PLANE_API_KEY: API key for that Bruce tunnel.
BRUCE_DEVICE_ID: same identifier as device LittleFS JSON, e.g. t-embed-lab-1.
BRUCE_MQTT_HOST: Internet hostname shown by your TLS MQTT broker.
BRUCE_MQTT_PORT: 8883.
BRUCE_MQTT_USER: separate broker user for the cloud connector.
BRUCE_MQTT_PASSWORD: separate, long random password for the connector.

Optional:
BRUCE_MQTT_CA_PEM: absolute path to a PEM **only for private CAs**. For a broker certified by a trusted public CA, leave unset; Python's system root CA store verifies TLS and hostname. The ESP32 still needs its own CA PEM uploaded to LittleFS as /bruce-ca.pem.

## MQTT broker

Use an independent managed MQTT broker, e.g. EMQX Cloud Serverless. Configure TLS (port 8883), distinct credentials for the T-Embed and Railway service, and topic ACLs following README.md.

Provision broker hostname, root CA and ESP32 credentials in the *local T-Embed WebUI* at http://192.168.0.124 (only while on the same Wi-Fi). Upload /bruce-remote.json and /bruce-ca.pem to LittleFS. No passwords or keys belong in source control, chats or screenshots.

## Staged rollout

1. Create the Railway service from GitHub and configure Source branch and Root Directory before deploying.
2. Configure broker and the dedicated MCP tunnel, then set Railway Variables.
3. Deploy service and inspect logs for tunnel authentication. It does not need a public domain.
4. Connect the dedicated Bruce tunnel to ChatGPT, call bruce_status, then bruce_info.
5. Check the T-Embed status topic for online=true after its local config is uploaded and the device restarted.
6. Test harmless operations first (brightness, tone); only then test physical unlocking and allowlisted IR/RF.

## Troubleshooting

- Railway fails starting: check missing tunnel or MQTT Variables.
- Broker disconnected: verify hostname, port 8883, broker ACL, TLS CA, username/password, and provider quotas.
- bruce_status says observed=false: connector may be on broker but the ESP32 has not connected/published status; verify Wi-Fi Startup and LittleFS.
- MQTT connected but device does not acknowledge: ensure **both** broker identities can access only the expected command/reply topics, same BRUCE_DEVICE_ID, and RTC clock synced on ESP32.
- Tunnel isn't reachable by ChatGPT: verify a separate tunnel was authorized and is attached to the account.
- Do not set Railway HTTP healthcheck: this worker has only an internal listener at 127.0.0.1; no ingress is expected.
- The already installed Build #23 supports the initial MQTT remote operations but NOT screenshots, OTA or decoded IR/RF captures. Don't assume these work until the dual-OTA firmware is installed.

## Cost notes

Railway has usage-based cloud resources. As of October 2026 its trial grant is $5 for up to 30 days; continuous operation after trial can incur charges or require a paid tier. Review usage alerts and spending limits in the Railway dashboard. Managed MQTT providers may have free usage quotas and may stop a deployment when quotas are reached.
