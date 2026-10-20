#!/bin/sh
set -eu

: "${CONTROL_PLANE_TUNNEL_ID:?Set separate Bruce tunnel ID}"
: "${CONTROL_PLANE_API_KEY:?Set separate Bruce tunnel API key}"
: "${BRUCE_DEVICE_ID:?Set device ID}"
: "${BRUCE_MQTT_HOST:?Set MQTT broker hostname}"
: "${BRUCE_MQTT_USER:?Set MQTT username}"
: "${BRUCE_MQTT_PASSWORD:?Set MQTT password}"
# A custom CA PEM is optional: system root CA store is used by default.
# If a custom PEM is explicitly configured, fail closed when it is absent.
if [ -n "${BRUCE_MQTT_CA_PEM:-}" ] && [ ! -s "$BRUCE_MQTT_CA_PEM" ]; then
  echo "MQTT TLS CA certificate file missing or empty" >&2
  exit 1
fi

python -u /app/mcp_server.py &
MCP_PID=$!
trap 'kill "$MCP_PID" 2>/dev/null || true' EXIT INT TERM
sleep 3
if ! kill -0 "$MCP_PID" 2>/dev/null; then
  echo "Bruce MCP server failed to start" >&2
  wait "$MCP_PID"
  exit 1
fi

exec tunnel-client run \
  --control-plane.tunnel-id="$CONTROL_PLANE_TUNNEL_ID" \
  --control-plane.api-key=env:CONTROL_PLANE_API_KEY \
  --mcp.server-url="http://127.0.0.1:8000/mcp" \
  --health.listen-addr=127.0.0.1:8080 \
  --log.level=info
