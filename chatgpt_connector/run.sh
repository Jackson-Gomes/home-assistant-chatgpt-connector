#!/usr/bin/with-contenv bashio
set -e

export HA_URL="http://supervisor/core"
export HA_TOKEN="$SUPERVISOR_TOKEN"
export SUPERVISOR_URL="http://supervisor"
export PRINTER_URI="${PRINTER_URI:-ipp://192.168.0.104/ipp/print}"

# Local CUPS is used only as an IPP transport to the configured network printer.
mkdir -p /run/cups
cupsd
sleep 1
lpadmin -x ChatGPT_Printer >/dev/null 2>&1 || true
lpadmin -p ChatGPT_Printer -E -v "$PRINTER_URI" -m everywhere
lpoptions -d ChatGPT_Printer
bashio::log.info "IPP printer ready: $PRINTER_URI"

TUNNEL_ID="$(bashio::config 'tunnel_id')"
TUNNEL_API_KEY="$(bashio::config 'tunnel_api_key')"
if [ -z "$TUNNEL_ID" ] || [ -z "$TUNNEL_API_KEY" ]; then
    bashio::log.error "Configure tunnel_id and tunnel_api_key in the add-on Configuration tab."
    exit 1
fi

export CONTROL_PLANE_TUNNEL_ID="$TUNNEL_ID"
export CONTROL_PLANE_API_KEY="$TUNNEL_API_KEY"
export MCP_SERVER_URL="http://127.0.0.1:8000/mcp"

export BRAIN_ENABLED="$(bashio::config 'brain_enabled')"
export BRAIN_AI_TASK_ENTITY="$(bashio::config 'brain_ai_task_entity')"
export BRAIN_BATCH_SECONDS="$(bashio::config 'brain_batch_seconds')"
export BRAIN_MIN_EVENTS_PER_BATCH="$(bashio::config 'brain_min_events_per_batch')"
export BRAIN_MAX_AI_CALLS_PER_HOUR="$(bashio::config 'brain_max_ai_calls_per_hour')"
export BRAIN_MAX_AI_CALLS_PER_DAY="$(bashio::config 'brain_max_ai_calls_per_day')"
export BRAIN_TRACKED_DOMAINS="$(bashio::config 'brain_tracked_domains')"
export BRAIN_ANNOUNCE_ENABLED="$(bashio::config 'brain_announce_enabled')"
export BRAIN_ANNOUNCE_SERVICE="$(bashio::config 'brain_announce_service')"
export BRAIN_NOTIFY_SUGGESTIONS="$(bashio::config 'brain_notify_suggestions')"

python3 /app/admin_server.py &
MCP_PID=$!

BRAIN_PID=""
if [ "$BRAIN_ENABLED" = "true" ]; then
    bashio::log.info "Starting Autonomous Brain foundation..."
    python3 /app/brain_service.py &
    BRAIN_PID=$!
else
    bashio::log.info "Autonomous Brain is disabled."
fi

cleanup() {
    kill "$MCP_PID" 2>/dev/null || true
    if [ -n "$BRAIN_PID" ]; then
        kill "$BRAIN_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

bashio::log.info "Waiting for local MCP server..."
sleep 3
if ! kill -0 "$MCP_PID" 2>/dev/null; then
    bashio::log.error "MCP server stopped during startup."
    wait "$MCP_PID"
    exit 1
fi

if [ -n "$BRAIN_PID" ] && ! kill -0 "$BRAIN_PID" 2>/dev/null; then
    bashio::log.error "Autonomous Brain stopped during startup."
    wait "$BRAIN_PID"
    exit 1
fi

bashio::log.info "Starting OpenAI Secure MCP Tunnel..."
exec tunnel-client run \
    --control-plane.tunnel-id="$CONTROL_PLANE_TUNNEL_ID" \
    --control-plane.api-key=env:CONTROL_PLANE_API_KEY \
    --mcp.server-url="$MCP_SERVER_URL" \
    --health.listen-addr=0.0.0.0:8080 \
    --log.level=info
