# Gate Caller — Home Assistant Add-on Repository

GSM modem gate opener for Home Assistant.

## Installation

1. In Home Assistant go to **Settings → Add-ons → Add-on Store**
2. Click **⋮** (top right) → **Repositories**
3. Add: `https://github.com/abratanich/gate-caller`
4. Find **Gate Caller** in the store and install
5. Configure allowed numbers and gate number in addon settings
6. Start the addon

## How it works

1. Someone calls your modem number from an allowed phone number
2. Addon answers, waits for DTMF "1" (press 1 to confirm)
3. Hangs up the incoming call
4. Dials the gate number (gate opens when it receives a call)
5. Hangs up after configured duration

## Requirements

- GSM modem (Huawei E169/E173/E220 or compatible) connected via USB
- SIM card with voice calls enabled
