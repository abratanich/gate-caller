#!/usr/bin/env bashio
# Gate Caller Add-on — entry point
# Reads config from HA addon UI and launches the Python daemon

MODEM_PORT=$(bashio::config 'modem_port')
MODEM_BAUD=$(bashio::config 'modem_baud')
GATE_NUMBER=$(bashio::config 'gate_number')
DTMF_TIMEOUT=$(bashio::config 'dtmf_timeout')
GATE_RING_DURATION=$(bashio::config 'gate_ring_duration')
POST_HANGUP_DELAY=$(bashio::config 'post_hangup_delay')
LOG_LEVEL=$(bashio::config 'log_level')

# Build comma-separated allowed numbers list
ALLOWED_NUMBERS=""
for num in $(bashio::config 'allowed_numbers'); do
    if [ -n "$ALLOWED_NUMBERS" ]; then
        ALLOWED_NUMBERS="${ALLOWED_NUMBERS},${num}"
    else
        ALLOWED_NUMBERS="${num}"
    fi
done

if [ -z "$ALLOWED_NUMBERS" ]; then
    bashio::log.error "No allowed numbers configured! Add at least one number in addon settings."
    exit 1
fi

if [ -z "$GATE_NUMBER" ]; then
    bashio::log.error "Gate number not configured! Set gate_number in addon settings."
    exit 1
fi

# HA Supervisor API for firing events
HA_WEBHOOK_URL="http://supervisor/core/api/events/gate_caller"
HA_TOKEN="${SUPERVISOR_TOKEN}"

export MODEM_PORT MODEM_BAUD ALLOWED_NUMBERS GATE_NUMBER
export DTMF_TIMEOUT GATE_RING_DURATION POST_HANGUP_DELAY
export HA_WEBHOOK_URL HA_TOKEN LOG_LEVEL

bashio::log.info "Gate Caller starting..."
bashio::log.info "Modem: ${MODEM_PORT} @ ${MODEM_BAUD}"
bashio::log.info "Allowed numbers: ${ALLOWED_NUMBERS}"
bashio::log.info "Gate number: ${GATE_NUMBER}"
bashio::log.info "DTMF timeout: ${DTMF_TIMEOUT}s"

exec python3 /usr/local/bin/gate_daemon.py
