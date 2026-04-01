#!/bin/sh
# Gate Caller Add-on — entry point
# Reads config from /data/options.json (mounted by Supervisor)

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

# Build comma-separated allowed numbers
ALLOWED_NUMBERS=$(jq -r '.allowed_numbers | join(",")' "$CONFIG")

if [ -z "$ALLOWED_NUMBERS" ] || [ "$ALLOWED_NUMBERS" = "null" ]; then
    echo "[ERROR] No allowed numbers configured! Add at least one number in addon settings."
    exit 1
fi

if [ -z "$GATE_NUMBER" ] || [ "$GATE_NUMBER" = "null" ] || [ "$GATE_NUMBER" = "" ]; then
    echo "[ERROR] Gate number not configured! Set gate_number in addon settings."
    exit 1
fi

# HA Supervisor API for firing events (if token available)
HA_WEBHOOK_URL="http://supervisor/core/api/events/gate_caller"
HA_TOKEN="${SUPERVISOR_TOKEN:-}"

export MODEM_PORT MODEM_BAUD ALLOWED_NUMBERS GATE_NUMBER
export DTMF_TIMEOUT GATE_RING_DURATION POST_HANGUP_DELAY
export HA_WEBHOOK_URL HA_TOKEN LOG_LEVEL

echo "[INFO] Gate Caller starting..."
echo "[INFO] Modem: ${MODEM_PORT} @ ${MODEM_BAUD}"
echo "[INFO] Allowed numbers: ${ALLOWED_NUMBERS}"
echo "[INFO] Gate number: ${GATE_NUMBER}"
echo "[INFO] DTMF timeout: ${DTMF_TIMEOUT}s"

exec python3 /usr/local/bin/gate_daemon.py
