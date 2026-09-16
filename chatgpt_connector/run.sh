#!/usr/bin/with-contenv bashio
set -e
export HA_URL="http://supervisor/core"
export HA_TOKEN="${SUPERVISOR_TOKEN}"
exec python3 /app/server.py
