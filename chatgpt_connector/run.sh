#!/usr/bin/with-contenv bashio
set -e
export HA_URL="http://supervisor/core"
export HA_TOKEN="$SUPERVISOR_TOKEN"
export SUPERVISOR_URL="http://supervisor"
TUNNEL_ID="$(bashio::config 'tunnel_id')"
TUNNEL_API_KEY="$(bashio::config 'tunnel_api_key')"
if [ -z "$TUNNEL_ID" ] || [ -z "$TUNNEL_API_KEY" ]; then
    bashio::log.error "Configure tunnel_id and tunnel_api_key in the add-on Configuration tab."
    exit 1
fi
export CONTROL_PLANE_TUNNEL_ID="$TUNNEL_ID"
export CONTROL_PLANE_API_KEY="$TUNNEL_API_KEY"
export MCP_SERVER_URL="http://127.0.0.1:8000/mcp"
python3 /app/admin_server.py &
MCP_PID=$!
cleanup() { kill "$MCP_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
bashio::log.info "Waiting for local MCP server..."
sleep 3
if ! kill -0 "$MCP_PID" 2>/dev/null; then
    bashio::log.error "MCP server stopped during startup."
    wait "$MCP_PID"
    exit 1
fi
bashio::log.info "Starting OpenAI Secure MCP Tunnel..."
exec tunnel-client run \
    --control-plane.tunnel-id="$CONTROL_PLANE_TUNNEL_ID" \
    --control-plane.api-key=env:CONTROL_PLANE_API_KEY \
    --mcp.server-url="$MCP_SERVER_URL" \
    --health.listen-addr=0.0.0.0:8080 \
    --log.level=info
