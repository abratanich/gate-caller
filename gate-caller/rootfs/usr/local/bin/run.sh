#!/bin/sh
# Gate Caller Add-on v2.0 — entry point

CONFIG="/data/options.json"

if [ ! -f "$CONFIG" ]; then
    echo "[ERROR] Config file not found: $CONFIG"
    exit 1
fi

MODEM_PORT=$(jq -r '.modem_port' "$CONFIG")
MODEM_BAUD=$(jq -r '.modem_baud' "$CONFIG")
GATE_NUMBER=$(jq -r '.gate_number' "$CONFIG")
DTMF_TIMEOUT=$(jq -r '.dtmf_timeout' "$CONFIG")
GATE_RING_DURATION=$(jq -r '.gate_ring_duration' "$CONFIG")
POST_HANGUP_DELAY=$(jq -r '.post_hangup_delay' "$CONFIG")
LOG_LEVEL=$(jq -r '.log_level' "$CONFIG")
API_PORT=$(jq -r '.api_port // 8099' "$CONFIG")
MQTT_HOST=$(jq -r '.mqtt_host // "core-mosquitto"' "$CONFIG")
MQTT_PORT=$(jq -r '.mqtt_port // 1883' "$CONFIG")
MQTT_USER=$(jq -r '.mqtt_user // ""' "$CONFIG")
MQTT_PASS=$(jq -r '.mqtt_pass // ""' "$CONFIG")

# allowed_numbers — JSON массив объектов {number, name}, передаём как JSON в env
ALLOWED_JSON=$(jq -c '.allowed_numbers' "$CONFIG")

if [ "$ALLOWED_JSON" = "null" ] || [ "$ALLOWED_JSON" = "[]" ]; then
    echo "[ERROR] No allowed numbers configured!"
    exit 1
fi

if [ -z "$GATE_NUMBER" ] || [ "$GATE_NUMBER" = "null" ] || [ "$GATE_NUMBER" = "" ]; then
    echo "[ERROR] Gate number not configured!"
    exit 1
fi

HA_WEBHOOK_URL="http://supervisor/core/api/events/gate_caller"
HA_TOKEN="${SUPERVISOR_TOKEN:-}"

ALLOWED_NUMBERS="$ALLOWED_JSON"
export MODEM_PORT MODEM_BAUD ALLOWED_NUMBERS GATE_NUMBER
export DTMF_TIMEOUT GATE_RING_DURATION POST_HANGUP_DELAY
export HA_WEBHOOK_URL HA_TOKEN LOG_LEVEL API_PORT
export MQTT_HOST MQTT_PORT MQTT_USER MQTT_PASS

echo "[INFO] Gate Caller v2.1.0 starting..."
echo "[INFO] Modem: ${MODEM_PORT} @ ${MODEM_BAUD}"
echo "[INFO] Allowed: ${ALLOWED_NUMBERS}"
echo "[INFO] Gate: ${GATE_NUMBER}"
echo "[INFO] MQTT: ${MQTT_HOST}:${MQTT_PORT}"
echo "[INFO] API: http://localhost:${API_PORT}"

exec python3 /usr/local/bin/gate_daemon.py
