#!/usr/bin/env python3
"""
Gate Opener Daemon — Huawei GSM modem AT command controller
Слушает входящие звонки, проверяет номер, ждёт DTMF "1", открывает ворота.

Последовательность:
1. Входящий звонок → проверка CLIP (caller ID)
2. Ответить (ATA)
3. Ждать DTMF "1" (timeout 10s)
4. Повесить трубку (ATH)
5. Пауза 3s (модем освобождается)
6. Позвонить на номер ворот (ATD)
7. Ждать 20s (ворота открываются по звонку)
8. Повесить трубку (ATH)
"""

import serial
import time
import logging
import os
import sys
import json
import signal
import requests
from datetime import datetime

# === Конфигурация ===
MODEM_PORT = os.environ.get("MODEM_PORT", "/dev/ttyUSB0")
MODEM_BAUD = int(os.environ.get("MODEM_BAUD", "115200"))

# Разрешённые номера — задаются через UI аддона, не хардкодить!
ALLOWED_NUMBERS = os.environ.get("ALLOWED_NUMBERS", "").split(",")
ALLOWED_NUMBERS = [n.strip() for n in ALLOWED_NUMBERS if n.strip()]

# Номер ворот — задаётся через UI аддона
GATE_NUMBER = os.environ.get("GATE_NUMBER", "")

# Таймауты
DTMF_TIMEOUT = int(os.environ.get("DTMF_TIMEOUT", "10"))
GATE_RING_DURATION = int(os.environ.get("GATE_RING_DURATION", "20"))
POST_HANGUP_DELAY = int(os.environ.get("POST_HANGUP_DELAY", "3"))

# Home Assistant webhook (опционально)
HA_WEBHOOK_URL = os.environ.get("HA_WEBHOOK_URL", "")
HA_TOKEN = os.environ.get("HA_TOKEN", "")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("gate-daemon")


def notify_ha(event: str, data: dict = None):
    """Отправить событие в Home Assistant."""
    if not HA_WEBHOOK_URL:
        return
    try:
        payload = {"event": event, "timestamp": datetime.now().isoformat()}
        if data:
            payload.update(data)
        headers = {}
        if HA_TOKEN:
            headers["Authorization"] = f"Bearer {HA_TOKEN}"
        requests.post(HA_WEBHOOK_URL, json=payload, headers=headers, timeout=5)
        log.info(f"HA notified: {event}")
    except Exception as e:
        log.warning(f"HA notify failed: {e}")


def send_at(ser: serial.Serial, command: str, timeout: float = 2.0) -> str:
    """Отправить AT команду и получить ответ."""
    ser.reset_input_buffer()
    cmd = f"{command}\r\n"
    ser.write(cmd.encode())
    log.debug(f"TX: {command}")

    response = ""
    end_time = time.time() + timeout
    while time.time() < end_time:
        if ser.in_waiting:
            chunk = ser.read(ser.in_waiting).decode("ascii", errors="replace")
            response += chunk
            if "OK" in response or "ERROR" in response or "NO CARRIER" in response:
                break
        time.sleep(0.1)

    log.debug(f"RX: {response.strip()}")
    return response.strip()


def init_modem(ser: serial.Serial) -> bool:
    """Инициализация модема для приёма звонков."""
    commands = [
        ("ATZ", "Reset"),
        ("ATE0", "Echo off"),
        ("AT+CMEE=1", "Extended errors"),
        ("AT+CLIP=1", "Caller ID on"),
        ("AT+CRC=1", "Extended ring format"),
        ("AT+CVHU=0", "Voice hangup control"),
        ("AT+DDET=1", "DTMF detection on"),  # Huawei-specific
    ]

    for cmd, desc in commands:
        resp = send_at(ser, cmd, timeout=3)
        if "ERROR" in resp:
            log.warning(f"Init {desc} ({cmd}): {resp}")
            # Не фатально — некоторые команды модем может не поддерживать
        else:
            log.info(f"Init {desc}: OK")

    log.info("Modem initialized")
    return True


def normalize_number(number: str) -> str:
    """Нормализовать номер для сравнения: убрать +38, пробелы, дефисы."""
    n = number.replace("+38", "").replace("+", "").replace(" ", "").replace("-", "")
    # Оставить последние 10 цифр
    if len(n) > 10:
        n = n[-10:]
    return n


def is_allowed(caller: str) -> bool:
    """Проверить что номер в списке разрешённых."""
    caller_norm = normalize_number(caller)
    for allowed in ALLOWED_NUMBERS:
        if normalize_number(allowed) == caller_norm:
            return True
    return False


def extract_caller(line: str) -> str:
    """Извлечь номер из CLIP: +CLIP: "+380670000000",145,,,,0"""
    if "+CLIP:" not in line:
        return ""
    try:
        # +CLIP: "+380670000000",145,,,,0
        start = line.index('"') + 1
        end = line.index('"', start)
        return line[start:end]
    except (ValueError, IndexError):
        return ""


def wait_for_dtmf(ser: serial.Serial, timeout: int = 10) -> str:
    """Ждать DTMF тон от модема. Huawei: +DTMF: <digit>"""
    log.info(f"Waiting for DTMF (timeout {timeout}s)...")
    end_time = time.time() + timeout
    buffer = ""

    while time.time() < end_time:
        if ser.in_waiting:
            chunk = ser.read(ser.in_waiting).decode("ascii", errors="replace")
            buffer += chunk

            # Huawei DTMF format: +DTMF: 1
            for line in buffer.split("\n"):
                line = line.strip()
                if "+DTMF:" in line:
                    digit = line.split(":")[-1].strip()
                    log.info(f"DTMF received: {digit}")
                    return digit

            # Проверяем что звонок не сброшен
            if "NO CARRIER" in buffer or "BUSY" in buffer:
                log.info("Caller hung up before DTMF")
                return ""

        time.sleep(0.1)

    log.info("DTMF timeout")
    return ""


def answer_call(ser: serial.Serial) -> bool:
    """Ответить на входящий звонок."""
    resp = send_at(ser, "ATA", timeout=5)
    if "OK" in resp or "CONNECT" in resp:
        log.info("Call answered")
        return True
    log.warning(f"Answer failed: {resp}")
    return False


def hangup(ser: serial.Serial):
    """Повесить трубку."""
    send_at(ser, "ATH", timeout=3)
    log.info("Hung up")


def call_gate(ser: serial.Serial) -> bool:
    """Позвонить на номер ворот для открытия."""
    log.info(f"Calling gate: {GATE_NUMBER}")
    notify_ha("gate_calling", {"number": GATE_NUMBER})

    # Инициируем голосовой вызов
    resp = send_at(ser, f"ATD{GATE_NUMBER};", timeout=10)

    if "ERROR" in resp:
        log.error(f"Gate call failed: {resp}")
        notify_ha("gate_call_failed", {"error": resp})
        return False

    # Ждём пока ворота "услышат" звонок
    log.info(f"Ringing gate for {GATE_RING_DURATION}s...")
    end_time = time.time() + GATE_RING_DURATION
    while time.time() < end_time:
        if ser.in_waiting:
            data = ser.read(ser.in_waiting).decode("ascii", errors="replace")
            if "NO CARRIER" in data or "BUSY" in data:
                log.info("Gate answered/hung up — likely opened")
                break
        time.sleep(1)

    hangup(ser)
    log.info("Gate call completed")
    notify_ha("gate_opened")
    return True


def main_loop(ser: serial.Serial):
    """Основной цикл — слушаем входящие звонки."""
    log.info(f"Listening for calls on {MODEM_PORT}...")
    log.info(f"Allowed numbers: {ALLOWED_NUMBERS}")
    log.info(f"Gate number: {GATE_NUMBER}")

    buffer = ""
    ringing = False
    caller = ""

    while True:
        try:
            if ser.in_waiting:
                chunk = ser.read(ser.in_waiting).decode("ascii", errors="replace")
                buffer += chunk

                for line in buffer.split("\n"):
                    line = line.strip()
                    if not line:
                        continue

                    # Входящий звонок
                    if "RING" in line:
                        ringing = True
                        log.info("RING detected")

                    # Caller ID
                    if "+CLIP:" in line and ringing:
                        caller = extract_caller(line)
                        log.info(f"Caller: {caller}")

                        if is_allowed(caller):
                            log.info(f"Allowed caller: {caller} — opening gate!")
                            notify_ha("call_received", {"caller": caller, "allowed": True})

                            # Сбросить входящий звонок
                            hangup(ser)
                            log.info("Incoming call rejected (ATH)")

                            # Пауза чтобы модем освободился
                            time.sleep(POST_HANGUP_DELAY)

                            # Звоним на ворота
                            call_gate(ser)
                        else:
                            log.warning(f"Rejected caller: {caller}")
                            notify_ha("call_rejected", {"caller": caller})

                        ringing = False
                        caller = ""

                    # Звонок завершён
                    if "NO CARRIER" in line:
                        ringing = False
                        caller = ""

                # Очищаем обработанный буфер
                if "\n" in buffer:
                    buffer = buffer.split("\n")[-1]

            time.sleep(0.1)

        except serial.SerialException as e:
            log.error(f"Serial error: {e}")
            notify_ha("modem_error", {"error": str(e)})
            time.sleep(5)
            # Пробуем переоткрыть порт
            try:
                ser.close()
                time.sleep(2)
                ser.open()
                init_modem(ser)
            except Exception:
                log.error("Failed to reopen serial port")
                time.sleep(10)

        except KeyboardInterrupt:
            log.info("Shutting down...")
            hangup(ser)
            break


def main():
    log.info(f"Gate Opener Daemon starting")
    log.info(f"Modem: {MODEM_PORT} @ {MODEM_BAUD}")

    if not ALLOWED_NUMBERS:
        log.error("No allowed numbers configured! Set them in addon settings.")
        sys.exit(1)
    if not GATE_NUMBER:
        log.error("Gate number not configured! Set it in addon settings.")
        sys.exit(1)

    # Graceful shutdown
    def signal_handler(sig, frame):
        log.info("Signal received, shutting down...")
        sys.exit(0)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        # Huawei USB modems throw BrokenPipeError on DTR ioctl.
        # Workaround: patch Serial to skip DTR/RTS state updates.
        _orig_update_dtr = serial.Serial._update_dtr_state
        _orig_update_rts = serial.Serial._update_rts_state

        def _noop_dtr(self):
            try:
                _orig_update_dtr(self)
            except (BrokenPipeError, OSError):
                pass

        def _noop_rts(self):
            try:
                _orig_update_rts(self)
            except (BrokenPipeError, OSError):
                pass

        serial.Serial._update_dtr_state = _noop_dtr
        serial.Serial._update_rts_state = _noop_rts

        ser = serial.Serial(
            port=MODEM_PORT,
            baudrate=MODEM_BAUD,
            timeout=1,
            write_timeout=5,
            dsrdtr=False,
            rtscts=False,
        )
        log.info(f"Serial port opened: {MODEM_PORT}")
    except serial.SerialException as e:
        log.error(f"Cannot open {MODEM_PORT}: {e}")
        sys.exit(1)

    if not init_modem(ser):
        log.error("Modem init failed")
        sys.exit(1)

    notify_ha("daemon_started")
    main_loop(ser)
    ser.close()


if __name__ == "__main__":
    main()
