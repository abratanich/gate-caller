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
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
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

# HTTP API port (для вызовов из HA автоматизаций)
API_PORT = int(os.environ.get("API_PORT", "8099"))

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


def call_number(ser: serial.Serial, number: str, duration: int = None) -> bool:
    """Позвонить на произвольный номер. Используется для ворот и как notify.call замена."""
    if duration is None:
        duration = GATE_RING_DURATION
    log.info(f"Calling: {number} (duration {duration}s)")
    notify_ha("calling", {"number": number})

    resp = send_at(ser, f"ATD{number};", timeout=10)

    if "ERROR" in resp:
        log.error(f"Call failed: {resp}")
        notify_ha("call_failed", {"number": number, "error": resp})
        return False

    log.info(f"Ringing for {duration}s...")
    end_time = time.time() + duration
    while time.time() < end_time:
        if ser.in_waiting:
            data = ser.read(ser.in_waiting).decode("ascii", errors="replace")
            if "NO CARRIER" in data or "BUSY" in data:
                log.info("Remote side answered/hung up")
                break
        time.sleep(1)

    hangup(ser)
    log.info(f"Call to {number} completed")
    notify_ha("call_completed", {"number": number})
    return True


def call_gate(ser: serial.Serial) -> bool:
    """Позвонить на номер ворот."""
    return call_number(ser, GATE_NUMBER)


# === HTTP API — замена notify.call ===
# Глобальная ссылка на serial, устанавливается в main()
_serial_lock = threading.Lock()
_serial_ref = None
_call_queue = []  # очередь звонков [(target, duration), ...]
_queue_lock = threading.Lock()


def _call_worker():
    """Воркер — берёт звонки из очереди и выполняет последовательно."""
    while True:
        task = None
        with _queue_lock:
            if _call_queue:
                task = _call_queue.pop(0)

        if task:
            target, duration = task
            log.info(f"Queue: calling {target} ({duration}s), {len(_call_queue)} remaining")
            with _serial_lock:
                call_number(_serial_ref, target, duration)
            # Пауза между звонками
            time.sleep(1)
        else:
            time.sleep(0.2)


class APIHandler(BaseHTTPRequestHandler):
    """HTTP API для вызовов из HA автоматизаций.

    POST /call  {"target": "+380986015838", "duration": 20}
    GET  /health
    GET  /queue
    """

    def log_message(self, format, *args):
        log.debug(f"HTTP: {args[0]}")

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"status": "ok", "modem": MODEM_PORT, "queue": len(_call_queue)})
        elif self.path == "/queue":
            self._respond(200, {"queue": list(_call_queue), "length": len(_call_queue)})
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/call":
            self._respond(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
        except (json.JSONDecodeError, ValueError):
            self._respond(400, {"error": "invalid json"})
            return

        target = body.get("target", "")
        duration = int(body.get("duration", GATE_RING_DURATION))

        if not target:
            self._respond(400, {"error": "target number required"})
            return

        with _queue_lock:
            position = len(_call_queue) + 1
            _call_queue.append((target, duration))

        log.info(f"API: queued call to {target} ({duration}s), position #{position}")
        self._respond(200, {"status": "queued", "target": target, "duration": duration, "position": position})

    def _respond(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


HEALTH_CHECK_INTERVAL = 30  # секунд между проверками модема
MAX_HEALTH_FAILURES = 3     # после N неудач — перезапуск


def check_modem_health(ser: serial.Serial) -> bool:
    """Проверить что модем отвечает на AT."""
    try:
        resp = send_at(ser, "AT", timeout=3)
        return "OK" in resp
    except Exception:
        return False


def reconnect_modem(ser: serial.Serial) -> bool:
    """Переоткрыть порт и переинициализировать модем."""
    log.warning("Reconnecting modem...")
    notify_ha("modem_reconnecting")
    try:
        ser.close()
    except Exception:
        pass
    time.sleep(3)
    try:
        ser.open()
        if init_modem(ser):
            log.info("Modem reconnected successfully")
            notify_ha("modem_reconnected")
            return True
    except Exception as e:
        log.error(f"Reconnect failed: {e}")
    return False


def main_loop(ser: serial.Serial):
    """Основной цикл — слушаем входящие звонки + health check."""
    log.info(f"Listening for calls on {MODEM_PORT}...")
    log.info(f"Allowed numbers: {ALLOWED_NUMBERS}")
    log.info(f"Gate number: {GATE_NUMBER}")

    buffer = ""
    ringing = False
    caller = ""
    last_health_check = time.time()
    health_failures = 0

    while True:
        try:
            # Периодическая проверка модема
            now = time.time()
            if now - last_health_check > HEALTH_CHECK_INTERVAL:
                last_health_check = now
                if not _serial_lock.locked():  # не проверять во время звонка
                    with _serial_lock:
                        if check_modem_health(ser):
                            health_failures = 0
                        else:
                            health_failures += 1
                            log.warning(f"Modem health check failed ({health_failures}/{MAX_HEALTH_FAILURES})")
                            if health_failures >= MAX_HEALTH_FAILURES:
                                log.error("Modem unresponsive — restarting...")
                                notify_ha("modem_error", {"error": "health check failed, restarting"})
                                if reconnect_modem(ser):
                                    health_failures = 0
                                else:
                                    log.error("Reconnect failed — exiting for supervisor restart")
                                    sys.exit(1)

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

                            with _serial_lock:
                                hangup(ser)
                                log.info("Incoming call rejected (ATH)")
                                time.sleep(POST_HANGUP_DELAY)
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
            if not reconnect_modem(ser):
                log.error("Cannot recover — exiting for supervisor restart")
                sys.exit(1)

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

    # HTTP API сервер (замена notify.call)
    global _serial_ref
    _serial_ref = ser

    # Call queue worker
    worker = threading.Thread(target=_call_worker, daemon=True)
    worker.start()
    log.info("Call queue worker started")

    # HTTP API server
    api_server = HTTPServer(("0.0.0.0", API_PORT), APIHandler)
    api_thread = threading.Thread(target=api_server.serve_forever, daemon=True)
    api_thread.start()
    log.info(f"HTTP API listening on port {API_PORT}")
    log.info(f"  POST /call {{\"target\": \"+380...\", \"duration\": 20}}")

    notify_ha("daemon_started")
    main_loop(ser)
    api_server.shutdown()
    ser.close()


if __name__ == "__main__":
    main()
