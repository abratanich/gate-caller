# Changelog

## 2.2.0

- Fix: `gate_opened` event was never emitted — `Gate Opens Today` counter stayed at 0.
  It is now fired after a successful call to `GATE_NUMBER`.
- New MQTT sensors:
  - `Gate Last Opener` — state = name, attributes = caller number + time.
  - `Gate Opens Today` now exposes `json_attributes_topic` with
    `last_caller`, `last_name`, `last_time`, and `today` (list of today's opens,
    each with `{time, caller, name}`) — ready for a "Today's activity" card.
- Incoming CLIP flow passes the caller's name through to the gate open event.
- HTTP `POST /call` accepts an optional `caller_name` field; when the target
  matches `GATE_NUMBER` the open is attributed to that name (e.g. "Въезд button").

## 1.0.0

- Initial release
- Incoming call detection with CLIP (caller ID)
- Allowed numbers whitelist (configured via UI)
- DTMF "1" verification before opening
- Outgoing call to gate number
- HA events: gate_caller (call_received, gate_opened, gate_denied, call_rejected)
- Huawei E169/E173/E220 support
