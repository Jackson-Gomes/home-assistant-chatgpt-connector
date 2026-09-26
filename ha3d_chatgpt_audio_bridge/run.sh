#!/usr/bin/with-contenv bashio
set -e

SOURCE_NAME="$(bashio::config 'source_name')"
SAMPLE_RATE="$(bashio::config 'sample_rate')"
SET_DEFAULT_SOURCE="$(bashio::config 'set_default_source')"
FIFO="/tmp/ha3d_chatgpt_mic.raw"

export HA3D_AUDIO_FIFO="$FIFO"
export HA3D_AUDIO_RATE="$SAMPLE_RATE"

bashio::log.info "Waiting for Home Assistant PulseAudio server..."
READY=false
for _ in $(seq 1 30); do
    if pactl info >/dev/null 2>&1; then
        READY=true
        break
    fi
    sleep 1
done

if [ "$READY" != "true" ]; then
    bashio::log.error "PulseAudio server is unavailable. The add-on needs audio: true support from Home Assistant OS."
    exit 1
fi

PREVIOUS_SOURCE="$(pactl get-default-source 2>/dev/null || true)"
rm -f "$FIFO"
mkfifo "$FIFO"

MODULE_ID="$(pactl load-module module-pipe-source \
    source_name="$SOURCE_NAME" \
    file="$FIFO" \
    format=s16le \
    rate="$SAMPLE_RATE" \
    channels=1 \
    source_properties=device.description=HA3D_ChatGPT_Mic)"

if [ -z "$MODULE_ID" ]; then
    bashio::log.error "Could not create the PulseAudio virtual microphone."
    exit 1
fi

if [ "$SET_DEFAULT_SOURCE" = "true" ]; then
    pactl set-default-source "$SOURCE_NAME" || true
    bashio::log.info "Virtual microphone '$SOURCE_NAME' is now the default PulseAudio source."
fi

cleanup() {
    if [ -n "$PREVIOUS_SOURCE" ] && [ "$SET_DEFAULT_SOURCE" = "true" ]; then
        pactl set-default-source "$PREVIOUS_SOURCE" >/dev/null 2>&1 || true
    fi
    pactl unload-module "$MODULE_ID" >/dev/null 2>&1 || true
    rm -f "$FIFO"
}
trap cleanup EXIT INT TERM

bashio::log.info "HA3D ChatGPT Audio Bridge ready at 48 kHz mono PCM over Supervisor ingress."
python3 /app/bridge.py
