# BruceRemote local gateway

This **independent** gateway runs on the Home Assistant host, not on the T-Embed. It offers locally authenticated HTTP-to-MQTT request/response, ready to be wrapped in the existing ChatGPT connector. This is NOT a public ChatGPT MCP endpoint.

Set secure environment variables (never commit real values):

- BRUCE_GATEWAY_TOKEN: unpredictable bearer token, 32+ chars
- BRUCE_DEVICE_ID: must match the ESP32 \`device_id\`
- BRUCE_MQTT_HOST, BRUCE_MQTT_PORT (default 8883)
- BRUCE_MQTT_USER, BRUCE_MQTT_PASSWORD: distinct credentials for gateway; topic ACL restricted to the configured device
- BRUCE_MQTT_CA_PEM: absolute path to trusted broker root CA PEM

Install and launch:

\`\`\`sh
python -m pip install -r requirements.txt
uvicorn server:app --host 127.0.0.1 --port 8765
\`\`\`

Example from **same host**:

\`\`\`sh
curl -s http://127.0.0.1:8765/command -H "Authorization: Bearer $BRUCE_GATEWAY_TOKEN" -H 'Content-Type: application/json' -d '{"op":"info.get","args":{}}'
\`\`\`

- Do NOT expose port 8765 to the internet.
- MQTT uses TLS validation, short-lived commands and correlation IDs.
- Device reports true responses to safe queries and queue admission for actions.
- ChatGPT MCP tunnel integration into the existing HA connector is a separate next step; no production connector files were changed.
