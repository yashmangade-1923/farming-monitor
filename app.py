#!/usr/bin/env python3
"""
===============================================================================
SMART IRRIGATION SYSTEM - LOCAL SERIAL-TO-WEB GATEWAY
===============================================================================
Flask + PySerial bridge connecting Arduino Uno firmware to the Web Dashboard.

Features:
- Configurable serial port with intelligent auto-discovery of Arduino devices.
- Resilient background worker thread with automatic reconnection on disconnect.
- Thread-safe ring buffer storing up to 200 historical telemetry samples.
- Synchronous command dispatch with ACK/ERR response handling.
- RESTful JSON API adhering to strict input validation.
- Embedded static/template server for frictionless local deployment.
===============================================================================
"""

import sys
import time
import json
import logging
import threading
from collections import deque
from datetime import datetime
from typing import Optional, Dict, Any, List

from flask import Flask, jsonify, request, render_template, send_from_directory
import serial
import serial.tools.list_ports

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------
# Set explicitly (e.g. 'COM3', 'COM4', '/dev/ttyUSB0', '/dev/ttyACM0')
# or leave as None to enable automatic Arduino port detection.
SERIAL_PORT: Optional[str] = None

# Serial baud rate - must match Arduino sketch Serial.begin(9600)
BAUD_RATE: int = 9600

# Web server configuration
HTTP_HOST: str = "0.0.0.0"
HTTP_PORT: int = 5000

# Telemetry history buffer depth (number of readings kept for live charts)
MAX_HISTORY: int = 200

# Fallback simulation mode if hardware is absent (set True or pass --simulate)
SIMULATION_MODE: bool = "--simulate" in sys.argv

# -----------------------------------------------------------------------------
# LOGGING SETUP
# -----------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("SmartIrrigation")

# -----------------------------------------------------------------------------
# THREAD-SAFE STATE STORE
# -----------------------------------------------------------------------------
class SystemState:
    def __init__(self):
        self.lock = threading.Lock()
        self.connected: bool = False
        self.active_port: Optional[str] = None
        self.last_update_time: Optional[float] = None
        self.latest_data: Optional[Dict[str, Any]] = None
        self.history: deque = deque(maxlen=MAX_HISTORY)
        
        # Command synchronization
        self.cmd_lock = threading.Lock()
        self.cmd_event = threading.Event()
        self.last_ack: Optional[str] = None

    def update_telemetry(self, data: Dict[str, Any]):
        now = time.time()
        iso_ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"
        data["timestamp"] = iso_ts
        data["epoch_ms"] = int(now * 1000)

        with self.lock:
            self.latest_data = data
            self.last_update_time = now
            self.history.append(dict(data))

    def get_latest(self) -> Dict[str, Any]:
        with self.lock:
            seconds_ago = None
            if self.last_update_time is not None:
                seconds_ago = round(time.time() - self.last_update_time, 2)

            return {
                "connected": self.connected,
                "port": self.active_port,
                "last_update_seconds_ago": seconds_ago,
                "data": dict(self.latest_data) if self.latest_data else None
            }

    def get_history(self, limit: int = MAX_HISTORY) -> List[Dict[str, Any]]:
        with self.lock:
            items = list(self.history)
            if limit > 0:
                return items[-limit:]
            return items

    def set_connection_status(self, connected: bool, port: Optional[str] = None):
        with self.lock:
            self.connected = connected
            self.active_port = port
            if not connected:
                # Do not clear latest_data: preserve last known state for graceful degradation
                pass

state = SystemState()

# -----------------------------------------------------------------------------
# SERIAL PORT DETECTION & COMMUNICATIONS MANAGER
# -----------------------------------------------------------------------------
def detect_arduino_port() -> Optional[str]:
    """Scans system serial ports for known Arduino USB VID/PIDs and descriptions."""
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        return None

    # Priority 1: Match known Arduino signatures in description / hardware ID
    arduino_keywords = ["arduino", "ch340", "ch341", "cp210", "ftdi", "usb serial", "usb-serial", "acm"]
    for p in ports:
        desc = (p.description or "").lower()
        hwid = (p.hwid or "").lower()
        mfg = (p.manufacturer or "").lower()
        for kw in arduino_keywords:
            if kw in desc or kw in hwid or kw in mfg:
                logger.info(f"Auto-detected Arduino on {p.device} ({p.description})")
                return p.device

    # Priority 2: If only one USB serial port exists, use it
    usb_ports = [p.device for p in ports if "usb" in (p.description or "").lower() or "acm" in p.device.lower()]
    if len(usb_ports) == 1:
        logger.info(f"Selecting single available USB serial port: {usb_ports[0]}")
        return usb_ports[0]

    # Priority 3: Fall back to first available port
    logger.info(f"Defaulting to first detected port: {ports[0].device} ({ports[0].description})")
    return ports[0].device


class SerialBridge(threading.Thread):
    """Background worker that maintains the serial connection and handles I/O."""

    def __init__(self, target_port: Optional[str], baud_rate: int):
        super().__init__(daemon=True, name="SerialBridgeThread")
        self.target_port = target_port
        self.baud_rate = baud_rate
        self.ser: Optional[serial.Serial] = None
        self.running = True

    def send_command(self, cmd_line: str, timeout: float = 1.5) -> str:
        """Sends command line string to Arduino and waits for synchronous ACK/ERR reply."""
        if not self.ser or not self.ser.is_open:
            raise ConnectionError("Arduino serial port is not open.")

        clean_cmd = cmd_line.strip()
        with state.cmd_lock:
            state.cmd_event.clear()
            state.last_ack = None

            full_line = f"{clean_cmd}\n".encode("utf-8")
            self.ser.write(full_line)
            self.ser.flush()
            logger.info(f"-> TX: {clean_cmd}")

            # Wait for response from reader thread
            if state.cmd_event.wait(timeout=timeout):
                return state.last_ack or "ACK:OK"
            else:
                logger.warning(f"Timeout waiting for ACK after sending: {clean_cmd}")
                return "ACK:TIMEOUT_ASSUMED_SENT"

    def run(self):
        while self.running:
            port = self.target_port or detect_arduino_port()
            if not port:
                state.set_connection_status(False, None)
                logger.info("No serial port detected. Retrying in 2 seconds...")
                time.sleep(2.0)
                continue

            try:
                logger.info(f"Opening serial connection to {port} at {self.baud_rate} baud...")
                self.ser = serial.Serial(port=port, baudrate=self.baud_rate, timeout=1.0)
                # Allow Arduino bootloader auto-reset to settle
                time.sleep(2.0)
                self.ser.reset_input_buffer()
                state.set_connection_status(True, port)
                logger.info(f"Successfully connected to Arduino on {port}")

                while self.running and self.ser.is_open:
                    try:
                        raw_line = self.ser.readline()
                        if not raw_line:
                            continue

                        line = raw_line.decode("utf-8", errors="ignore").strip()
                        if not line:
                            continue

                        # Check if line is JSON telemetry
                        if line.startswith("{") and line.endswith("}"):
                            try:
                                payload = json.loads(line)
                                state.update_telemetry(payload)
                            except json.JSONDecodeError:
                                logger.warning(f"Malformed JSON telemetry received: {line}")
                        # Check if line is Command ACK / ERR reply
                        elif line.startswith("ACK:") or line.startswith("ERR:"):
                            logger.info(f"<- RX: {line}")
                            state.last_ack = line
                            state.cmd_event.set()
                        else:
                            logger.debug(f"Serial output: {line}")

                    except (serial.SerialException, OSError) as e:
                        logger.error(f"Serial read error on {port}: {e}")
                        break

            except (serial.SerialException, PermissionError, OSError) as e:
                logger.warning(f"Could not connect to {port}: {e}. Retrying in 2 seconds...")
                state.set_connection_status(False, port)
            finally:
                if self.ser and self.ser.is_open:
                    try:
                        self.ser.close()
                    except Exception:
                        pass
                state.set_connection_status(False, None)

            time.sleep(2.0)


# -----------------------------------------------------------------------------
# SIMULATOR WORKER (Fallback for offline testing)
# -----------------------------------------------------------------------------
class SimulatorThread(threading.Thread):
    """Simulates an Arduino running the smart irrigation firmware."""

    def __init__(self):
        super().__init__(daemon=True, name="SimulatorThread")
        self.moisture = 42
        self.pump = "OFF"
        self.mode = "AUTO"
        self.threshold_on = 30
        self.threshold_off = 60
        self.dry = 620
        self.wet = 310
        self.sensor_ok = True
        self.safety_cutoff = False
        self.pump_on_time = 0

    def send_command(self, cmd_line: str, timeout: float = 1.0) -> str:
        cmd = cmd_line.strip()
        if cmd == "PUMP_ON":
            self.pump = "ON"
            self.mode = "MANUAL"
            self.safety_cutoff = False
            self.pump_on_time = time.time()
            return "ACK:PUMP_ON"
        elif cmd == "PUMP_OFF":
            self.pump = "OFF"
            self.mode = "MANUAL"
            self.safety_cutoff = False
            return "ACK:PUMP_OFF"
        elif cmd == "AUTO":
            self.mode = "AUTO"
            self.safety_cutoff = False
            return "ACK:AUTO"
        elif cmd.startswith("SET_THRESHOLDS:"):
            try:
                parts = cmd.split(":")[1].split(",")
                on_v, off_v = int(parts[0]), int(parts[1])
                self.threshold_on = on_v
                self.threshold_off = off_v
                return f"ACK:SET_THRESHOLDS:{on_v},{off_v}"
            except Exception:
                return "ERR:INVALID_THRESHOLDS"
        elif cmd.startswith("SET_CALIB:"):
            try:
                parts = cmd.split(":")[1].split(",")
                d_v, w_v = int(parts[0]), int(parts[1])
                self.dry = d_v
                self.wet = w_v
                return f"ACK:SET_CALIB:{d_v},{w_v}"
            except Exception:
                return "ERR:INVALID_CALIB"
        return "ERR:UNKNOWN_COMMAND"

    def run(self):
        logger.info(">>> SIMULATION MODE ACTIVE: Emulating Arduino Uno Soil Controller <<<")
        state.set_connection_status(True, "SIMULATOR (VIRTUAL-COM)")

        while True:
            # Physical simulation physics
            if self.pump == "ON":
                self.moisture = min(100, self.moisture + 2)
                # Check 60-second safety cutoff in simulation
                if time.time() - self.pump_on_time > 60:
                    self.pump = "OFF"
                    self.safety_cutoff = True
                    self.mode = "AUTO"
            else:
                self.moisture = max(10, self.moisture - 1)

            # Auto hysteresis logic
            if self.mode == "AUTO" and not self.safety_cutoff:
                if self.moisture < self.threshold_on:
                    if self.pump == "OFF":
                        self.pump = "ON"
                        self.pump_on_time = time.time()
                elif self.moisture > self.threshold_off:
                    if self.pump == "ON":
                        self.pump = "OFF"

            # Calculate simulated raw value
            raw_val = int(self.dry - ((self.dry - self.wet) * (self.moisture / 100.0)))

            telemetry = {
                "raw": raw_val,
                "moisture": self.moisture,
                "pump": self.pump,
                "mode": self.mode,
                "threshold_on": self.threshold_on,
                "threshold_off": self.threshold_off,
                "sensor_ok": self.sensor_ok,
                "safety_cutoff": self.safety_cutoff,
                "dry": self.dry,
                "wet": self.wet
            }
            state.update_telemetry(telemetry)
            time.sleep(1.0)


# Instantiate communication worker
bridge: Any = None
if SIMULATION_MODE:
    bridge = SimulatorThread()
else:
    bridge = SerialBridge(target_port=SERIAL_PORT, baud_rate=BAUD_RATE)
bridge.start()

# -----------------------------------------------------------------------------
# FLASK WEB APPLICATION
# -----------------------------------------------------------------------------
app = Flask(__name__, template_folder="templates", static_folder="static")

@app.route("/")
def index():
    """Serves the single-page dashboard."""
    return render_template("index.html")


@app.route("/api/data", methods=["GET"])
def get_data():
    """Returns the latest telemetry sample, connection status, and recency."""
    return jsonify(state.get_latest())


@app.route("/api/history", methods=["GET"])
def get_history():
    """Returns the buffered telemetry history for rendering time-series charts."""
    try:
        limit = int(request.args.get("limit", MAX_HISTORY))
    except (ValueError, TypeError):
        limit = MAX_HISTORY
    
    records = state.get_history(limit)
    return jsonify({
        "count": len(records),
        "history": records
    })


@app.route("/api/control", methods=["POST"])
def post_control():
    """Executes a manual pump or mode override."""
    if not request.is_json:
        return jsonify({"status": "error", "message": "Expected JSON payload"}), 400

    payload = request.get_json()
    command = payload.get("command", "").strip().upper()

    whitelist = ["PUMP_ON", "PUMP_OFF", "AUTO"]
    if command not in whitelist:
        return jsonify({
            "status": "error",
            "message": f"Invalid command '{command}'. Allowed: {', '.join(whitelist)}"
        }), 400

    current_status = state.get_latest()
    if not current_status["connected"]:
        return jsonify({
            "status": "error",
            "message": "Arduino is currently disconnected. Cannot send command."
        }), 503

    try:
        reply = bridge.send_command(command)
        if reply.startswith("ERR:"):
            return jsonify({"status": "error", "message": reply}), 400
        return jsonify({"status": "success", "command": command, "reply": reply})
    except Exception as e:
        logger.error(f"Error dispatching command '{command}': {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/settings", methods=["POST"])
def post_settings():
    """Updates operational thresholds and/or sensor calibration."""
    if not request.is_json:
        return jsonify({"status": "error", "message": "Expected JSON payload"}), 400

    payload = request.get_json()
    current_status = state.get_latest()
    if not current_status["connected"]:
        return jsonify({
            "status": "error",
            "message": "Arduino is currently disconnected. Cannot update settings."
        }), 503

    responses = []

    # 1. Update Thresholds (threshold_on, threshold_off)
    if "threshold_on" in payload or "threshold_off" in payload:
        try:
            on_v = int(payload.get("threshold_on"))
            off_v = int(payload.get("threshold_off"))
        except (ValueError, TypeError):
            return jsonify({"status": "error", "message": "threshold_on and threshold_off must be integers"}), 400

        if not (0 <= on_v < off_v <= 100):
            return jsonify({
                "status": "error",
                "message": "Validation failed: 0 <= threshold_on < threshold_off <= 100 required"
            }), 400

        cmd_thresh = f"SET_THRESHOLDS:{on_v},{off_v}"
        reply_thresh = bridge.send_command(cmd_thresh)
        responses.append(reply_thresh)

    # 2. Update Calibration (dry, wet)
    if "dry" in payload or "wet" in payload:
        try:
            dry_v = int(payload.get("dry"))
            wet_v = int(payload.get("wet"))
        except (ValueError, TypeError):
            return jsonify({"status": "error", "message": "dry and wet calibration values must be integers"}), 400

        if not (dry_v > wet_v and 0 <= wet_v <= 1023 and dry_v <= 1023):
            return jsonify({
                "status": "error",
                "message": "Validation failed: 0 <= wet < dry <= 1023 required"
            }), 400

        cmd_calib = f"SET_CALIB:{dry_v},{wet_v}"
        reply_calib = bridge.send_command(cmd_calib)
        responses.append(reply_calib)

    if not responses:
        return jsonify({"status": "error", "message": "No valid settings fields provided"}), 400

    has_err = any(r.startswith("ERR:") for r in responses)
    if has_err:
        return jsonify({"status": "error", "responses": responses}), 400

    return jsonify({"status": "success", "responses": responses})


# -----------------------------------------------------------------------------
# STARTUP BANNER & CONSOLE OUTPUT
# -----------------------------------------------------------------------------
def print_banner():
    banner = f"""
===============================================================================
       🌱 SMART IRRIGATION SYSTEM - SERIAL-TO-WEB GATEWAY 🌱
===============================================================================
  Web Dashboard URL : http://localhost:{HTTP_PORT}
                      http://127.0.0.1:{HTTP_PORT}
  Configured Baud   : {BAUD_RATE} baud
  Configured Port   : {SERIAL_PORT or 'AUTO-DETECT'}
  Simulation Mode   : {'ENABLED (--simulate flag detected)' if SIMULATION_MODE else 'OFF (Hardware Serial)'}
===============================================================================
  AVAILABLE API ENDPOINTS:
  - GET  /api/data      : Latest sensor sample & connection status
  - GET  /api/history   : Buffered telemetry time-series (limit=N)
  - POST /api/control   : Pump and mode overrides (PUMP_ON, PUMP_OFF, AUTO)
  - POST /api/settings  : Update thresholds & dry/wet calibration
===============================================================================
  STARTUP CHECKLIST:
  1. Ensure Arduino sketch 'smart_irrigation.ino' is compiled and uploaded.
  2. CLOSE the Arduino IDE Serial Monitor (two apps cannot share one COM port).
  3. Open your browser and navigate to: http://localhost:{HTTP_PORT}
===============================================================================
"""
    print(banner)


# -----------------------------------------------------------------------------
# ENTRY POINT
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    print_banner()
    app.run(host=HTTP_HOST, port=HTTP_PORT, debug=False, use_reloader=False)
