# 🌱 Smart Irrigation System — Living Garden Control Room

A production-grade, local IoT Smart Irrigation System built on a **Serial-to-Web** architecture. It pairs an **Arduino Uno** running non-blocking firmware with a **Python (Flask + PySerial)** gateway and a responsive, futuristic **"Living Garden" glassmorphism web dashboard**.

---

## 📁 Project Structure

```text
farming dashboard project/
│
├── smart_irrigation/
│   └── smart_irrigation.ino    # Arduino Uno C++ firmware (non-blocking, failsafes)
│
├── templates/
│   └── index.html              # Living Garden animated dashboard (Tailwind + Chart.js)
│
├── app.py                      # Flask backend + PySerial background worker
├── requirements.txt            # Python dependencies (flask, pyserial)
└── README.md                   # Complete wiring, calibration, and operational guide
```

---

## ⚡ Quick Start (5-Minute Setup)

### 1. Upload Arduino Firmware
1. Open the [Arduino IDE](https://www.arduino.cc/en/software).
2. Connect your Arduino Uno to your PC via USB cable.
3. Open `smart_irrigation/smart_irrigation.ino`.
4. Select your board (**Tools > Board > Arduino Uno**) and Port (**Tools > Port > COMx** on Windows or `/dev/ttyUSBx` on Linux/macOS).
5. Click **Upload** (Arrow icon).
6. **IMPORTANT:** Once uploaded, **CLOSE THE ARDUINO SERIAL MONITOR**. Only one program can access the COM port at a time; keeping the Serial Monitor open will block Python from connecting.

### 2. Install Python Dependencies
Open your terminal (Command Prompt, PowerShell, or Bash) in this project folder and run:
```bash
pip install -r requirements.txt
```

### 3. Run the Python Gateway
Start the local server:
```bash
python app.py
```
*Note:* The gateway will automatically scan your system and connect to the Arduino. If you want to test the web dashboard UI without physical hardware connected, you can launch in simulation mode:
```bash
python app.py --simulate
```

### 4. Open the Web Dashboard
Open your browser and navigate to:
```
http://localhost:5000
```

---

## 🔌 Hardware Wiring Guide

> [!CAUTION]
> **NEVER POWER THE DC WATER PUMP FROM THE ARDUINO 5V PIN.**
> Motors create high current draw and inductive kickback (back-EMF) that can reset or permanently destroy the Arduino microcontroller. Always power the pump from a dedicated battery pack or external DC power supply.

### Connection Table

| Component | Component Pin | Arduino / Power Source Pin | Description / Notes |
| :--- | :--- | :--- | :--- |
| **Capacitive Sensor v1.2** | `VCC` | `5V` (or `3.3V`)* on Arduino | Power supply for sensor board |
| | `GND` | `GND` on Arduino | Common signal ground |
| | `AOUT` | `A0` on Arduino | Analog output voltage proportional to moisture |
| **5V Relay Module** | `VCC` | `5V` on Arduino | Logic supply for optocoupler & relay coil |
| | `GND` | `GND` on Arduino | Common ground |
| | `IN` | `Pin 8` on Arduino | Digital control signal (Active-LOW by default) |
| **Relay Output Contacts** | `COM` | External Battery `(+)` | Relay common switch terminal |
| | `NO` (Normally Open) | DC Pump `(+)` Wire | Closed only when relay is energized |
| **DC Motor Pump** | `(+)` Red Wire | Relay `NO` Terminal | Switched through relay |
| | `(-)` Black Wire | External Battery `(-)` Ground | Direct return to external battery ground |
| **1N4007 Diode** | Cathode (Silver Ring) | Motor `(+)` terminal | **Flyback diode:** Absorbs reverse inductive spikes |
| | Anode (Black End) | Motor `(-)` terminal | Connect in reverse-bias across motor leads |

*\*Pro Tip:* Some v1.2 capacitive sensors produce cleaner readings on 3.3V due to on-board voltage regulators. If your raw reading in air is close to 1023 on 5V, power it from 3.3V.

```
       [External Battery (+)] ───── [Relay COM]
                                         │  (Switched contact)
                                    [Relay NO]
                                         │
                                         ├─── [Cathode: Silver Band] ──┐
                                         │                             │ 1N4007 Diode
                                     [Pump (+)]                        │ (Flyback)
                                         │                             │
                                     [Pump (-)] ───────────────────────┴── [External Battery (-)]
```

---

## 🧪 Sensor Calibration Guide

Capacitive soil moisture sensors do not corrode like resistive sensors, but they must be calibrated for your specific soil mix and ambient voltage.

1. **Dry Reading (Air Calibration):**
   - Hold the clean, dry sensor in ambient air by the top edges (do not touch the metal sensor plates).
   - Look at the dashboard or the raw value in the serial stream.
   - Note down the `raw` value (typically **580 - 680**).
2. **Wet Reading (Water Calibration):**
   - Fill a cup with water.
   - Submerge the sensor blade into water up to (but not beyond) the white horizontal line.
   - **Do NOT submerge the top electronics or components on the sensor board!**
   - Note down the `raw` value (typically **280 - 350**).
3. **Apply Calibration:**
   - In the **Settings & Calibration** panel on the web dashboard, enter your measured `Dry Value` and `Wet Value`.
   - Click **Flash Settings to Arduino**.
   - The Arduino firmware immediately updates its internal mapping:
     $$\text{Moisture } \% = \text{constrain}\left(\text{map}(\text{raw}, \text{dryValue}, \text{wetValue}, 0, 100), 0, 100\right)$$

---

## 📡 Serial Protocol Reference

**Baud Rate:** `9600` &bull; **Data Bits:** `8` &bull; **Parity:** `None` &bull; **Stop Bits:** `1` &bull; **Line Ending:** `\n` (newline)

### Telemetry (Arduino $\rightarrow$ PC, every 1000 ms)
Emits a single-line JSON string:
```json
{"raw":512,"moisture":45,"pump":"OFF","mode":"AUTO","threshold_on":30,"threshold_off":60,"sensor_ok":true,"safety_cutoff":false,"dry":620,"wet":310}
```

### Commands (PC $\rightarrow$ Arduino)
| Command String | Response | Effect |
| :--- | :--- | :--- |
| `PUMP_ON` | `ACK:PUMP_ON` | Forces pump ON immediately; sets mode to `MANUAL`. |
| `PUMP_OFF` | `ACK:PUMP_OFF` | Forces pump OFF immediately; sets mode to `MANUAL`. |
| `AUTO` | `ACK:AUTO` | Sets mode to `AUTO`; clears safety lock and resumes hysteresis control. |
| `SET_THRESHOLDS:<on>,<off>` | `ACK:SET_THRESHOLDS:<on>,<off>` | Updates hysteresis limits (e.g. `SET_THRESHOLDS:25,55`). |
| `SET_CALIB:<dry>,<wet>` | `ACK:SET_CALIB:<dry>,<wet>` | Updates calibration baselines (e.g. `SET_CALIB:630,305`). |

---

## 🛡️ Built-in Failsafes & Safety Features

1. **Hysteresis Anti-Chatter:** The pump turns ON when moisture $< 30\%$ and turns OFF only after moisture $> 60\%$. Between 30% and 60%, the pump maintains its previous state to prevent rapid relay wear.
2. **60-Second Continuous Run Safety Cutoff:** If the pump runs continuously for 60 seconds (due to a dry water reservoir, siphon failure, or leak), the firmware forces the pump OFF, restores `AUTO` mode, and sets `"safety_cutoff": true`.
3. **Sensor Fault Detection:** If the analog reading drops below 150 (short circuit) or climbs above 950 (sensor disconnected or wire broken), the firmware flags `"sensor_ok": false` and locks the pump OFF.
4. **Boot State Guarantee:** The relay is initialized and forced OFF at startup before serial communications begin.
5. **Memory-Safe C-String Parser:** Avoids dynamic heap fragmentation from the Arduino `String` class on 2KB SRAM.

---

## 🔧 Troubleshooting Guide

| Symptom | Root Cause | Solution |
| :--- | :--- | :--- |
| **"Could not connect to COMx: Access is denied"** | Arduino IDE Serial Monitor or Cura/Pronterface is still open. | Close all Serial Monitors, 3D printer software, or previous Python instances. Only one application can open a serial port at a time. |
| **"No serial port detected"** | Arduino USB cable is loose, or driver is missing. | Verify cable supports data (not power-only). For clone Uno boards with CH340 chip, install the [WCH CH340 USB driver](http://www.wch-ic.com/downloads/CH341SER_ZIP.html). |
| **Pump is ON when dashboard says OFF (Inverted Relay)** | Relay is Active-HIGH, but firmware is set to Active-LOW. | In `smart_irrigation.ino`, change `const bool RELAY_ACTIVE_LOW = true;` to `false;` and re-upload. |
| **Sensor readings jump erratically** | Electrical motor noise or poor power stability. | 1. Ensure the 1N4007 diode is installed across motor terminals.<br>2. Ensure Arduino is grounded properly.<br>3. Power the pump from a separate battery pack, never from the Arduino. |
| **Moisture % is stuck at 0% or 100%** | Inverted calibration or sensor submerged too deep. | Capacitive sensors read **lower** ADC counts when wet and **higher** when dry. Ensure `Dry Value > Wet Value` in settings. |
| **Arduino restarts whenever the pump starts** | Motor current spike causes voltage dip on Arduino. | Disconnect the pump power from the Arduino. Use an external battery for the motor and only connect relay `IN`, `VCC`, and `GND` to the Arduino. |
