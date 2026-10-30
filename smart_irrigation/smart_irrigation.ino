/* ============================================================================
 * SMART IRRIGATION SYSTEM - ARDUINO UNO FIRMWARE
 * ============================================================================
 * Architecture: Serial-to-Web IoT Controller
 * Baud Rate: 9600 baud, newline-terminated ('\n')
 * Hardware:
 *   - Arduino Uno (ATmega328P, 16MHz, 2KB SRAM)
 *   - Capacitive Soil Moisture Sensor v1.2 / v2.0 -> Analog Pin A0
 *   - 5V Relay Module -> Digital Pin 8 (Configurable Active-LOW / Active-HIGH)
 *   - DC Submersible Pump -> Switched via Relay (COM & NO contacts)
 *                            powered by external DC battery (NEVER from 5V pin!)
 *                            with 1N4007 flyback diode across motor terminals.
 *
 * Safety Features:
 *   - Non-blocking timing with millis() - zero delay() calls in loop.
 *   - Low-pass 10-sample moving average on A0 to filter electrical noise.
 *   - Sensor fault detection (disconnected wire / short circuit detection).
 *   - 60-second continuous pump safety cutoff (prevents flooding & pump burnout).
 *   - Hysteresis band (threshold_on vs threshold_off) to prevent relay chatter.
 *   - Pure C-string parser (no dynamic String class allocation to preserve SRAM).
 * ============================================================================ */

#include <Arduino.h>

// ----------------------------------------------------------------------------
// PIN DEFINITIONS & HARDWARE CONFIGURATION
// ----------------------------------------------------------------------------
const uint8_t SOIL_PIN  = A0;  // Analog pin connected to soil moisture sensor AOUT
const uint8_t RELAY_PIN = 8;   // Digital output driving relay module input (IN)

// Polarity configuration:
// Most standard Arduino 5V relay modules are ACTIVE-LOW (LOW = ON, HIGH = OFF).
// If using an active-HIGH transistor or solid-state relay, change this to false.
const bool RELAY_ACTIVE_LOW = true;

// ----------------------------------------------------------------------------
// CALIBRATION CONSTANTS (Defaults for Capacitive Sensor v1.2)
// ----------------------------------------------------------------------------
// CALIBRATION GUIDE:
// 1. Dry Value: Read raw value while sensor is dry in ambient air (~580 - 680).
// 2. Wet Value: Read raw value submerged in water up to the max line (~280 - 350).
// Because sensor capacitance increases in water, RAW ADC counts DECREASE as moisture increases.
int dryValue = 620;  // Default dry baseline (0% moisture in air)
int wetValue = 310;  // Default wet baseline (100% moisture submerged in water)

// Out-of-bounds bounds to detect disconnected or shorted sensor wires
const int SENSOR_MIN_RAW = 150;  // Anything below this indicates direct short / fault
const int SENSOR_MAX_RAW = 950;  // Anything above this indicates open circuit / disconnected

// ----------------------------------------------------------------------------
// CONTROL PARAMETERS & HYSTERESIS
// ----------------------------------------------------------------------------
int thresholdOn  = 30;  // Turn pump ON when moisture drops below this % (default: 30%)
int thresholdOff = 60;  // Turn pump OFF when moisture rises above this % (default: 60%)

// Operating Mode: "AUTO" (hysteresis loop) or "MANUAL" (user override)
enum SystemMode { MODE_AUTO, MODE_MANUAL };
SystemMode currentMode = MODE_AUTO;

// Safety cutoff timer: Force pump off if running continuously longer than 60 seconds
const unsigned long MAX_PUMP_RUN_TIME = 60000UL; // 60,000 milliseconds = 60 seconds

// ----------------------------------------------------------------------------
// SYSTEM STATE VARIABLES
// ----------------------------------------------------------------------------
bool pumpActive = false;             // Logical state: true = ON, false = OFF
bool sensorOk = true;                // True if sensor readings are within physical limits
bool safetyCutoffTriggered = false;  // True if pump was auto-stopped by run-time limit
unsigned long pumpStartTime = 0;     // Timestamp (millis) when pump was energized
unsigned long lastTelemetryTime = 0; // Timestamp of last JSON telemetry broadcast
const unsigned long TELEMETRY_INTERVAL = 1000UL; // Broadcast every 1000ms

// Moving average buffer for A0
const uint8_t SAMPLE_COUNT = 10;
int rawSamples[SAMPLE_COUNT];
uint8_t sampleIndex = 0;
bool bufferFilled = false;
unsigned long lastSampleTime = 0;
const unsigned long SAMPLE_INTERVAL = 20UL; // Sample every 20ms (10 samples in 200ms)

// Serial input buffer (avoids Arduino String class fragmentation)
const uint8_t RX_BUFFER_SIZE = 64;
char rxBuffer[RX_BUFFER_SIZE];
uint8_t rxIndex = 0;

// ----------------------------------------------------------------------------
// HELPER FUNCTIONS
// ----------------------------------------------------------------------------

/**
 * Applies physical electrical state to the relay pin based on configured polarity.
 */
void setRelayHardware(bool turnOn) {
  if (RELAY_ACTIVE_LOW) {
    digitalWrite(RELAY_PIN, turnOn ? LOW : HIGH);
  } else {
    digitalWrite(RELAY_PIN, turnOn ? HIGH : LOW);
  }
}

/**
 * High-level pump state manager with safety enforcement.
 */
void setPump(bool enable) {
  if (enable) {
    // Safety check: Never turn pump on if sensor is faulty or safety cutoff is locked
    if (!sensorOk || safetyCutoffTriggered) {
      setRelayHardware(false);
      pumpActive = false;
      return;
    }
    if (!pumpActive) {
      pumpStartTime = millis();
    }
    pumpActive = true;
    setRelayHardware(true);
  } else {
    pumpActive = false;
    setRelayHardware(false);
  }
}

/**
 * Computes moving average of analog sensor to reject electrical noise.
 */
int getAveragedRaw() {
  long sum = 0;
  uint8_t count = bufferFilled ? SAMPLE_COUNT : (sampleIndex == 0 ? 1 : sampleIndex);
  for (uint8_t i = 0; i < count; i++) {
    sum += rawSamples[i];
  }
  return (int)(sum / count);
}

/**
 * Converts raw ADC value into a 0-100% moisture scale with calibration.
 */
int calculateMoisturePercent(int rawVal) {
  if (dryValue == wetValue) return 0;
  // Note: Higher raw = drier, lower raw = wetter
  long percent = map(rawVal, dryValue, wetValue, 0, 100);
  return (int)constrain(percent, 0, 100);
}

/**
 * Emits JSON telemetry packet once per second.
 * Format: {"raw":512,"moisture":45,"pump":"OFF","mode":"AUTO","threshold_on":30,"threshold_off":60,"sensor_ok":true,"safety_cutoff":false}
 */
void transmitTelemetry(int rawVal, int moistureVal) {
  Serial.print(F("{\"raw\":"));
  Serial.print(rawVal);
  Serial.print(F(",\"moisture\":"));
  Serial.print(moistureVal);
  Serial.print(F(",\"pump\":\""));
  Serial.print(pumpActive ? F("ON") : F("OFF"));
  Serial.print(F("\",\"mode\":\""));
  Serial.print((currentMode == MODE_AUTO) ? F("AUTO") : F("MANUAL"));
  Serial.print(F("\",\"threshold_on\":"));
  Serial.print(thresholdOn);
  Serial.print(F(",\"threshold_off\":"));
  Serial.print(thresholdOff);
  Serial.print(F(",\"sensor_ok\":"));
  Serial.print(sensorOk ? F("true") : F("false"));
  Serial.print(F(",\"safety_cutoff\":"));
  Serial.print(safetyCutoffTriggered ? F("true") : F("false"));
  Serial.print(F(",\"dry\":"));
  Serial.print(dryValue);
  Serial.print(F(",\"wet\":"));
  Serial.print(wetValue);
  Serial.println(F("}"));
}

/**
 * Parses and executes incoming serial commands using C-string routines.
 */
void processCommand(char* cmd) {
  // Strip trailing whitespace or newline characters
  int len = strlen(cmd);
  while (len > 0 && (cmd[len - 1] == '\r' || cmd[len - 1] == '\n' || cmd[len - 1] == ' ')) {
    cmd[--len] = '\0';
  }

  if (len == 0) return;

  // Command: PUMP_ON
  if (strcmp(cmd, "PUMP_ON") == 0) {
    if (!sensorOk) {
      Serial.println(F("ERR:SENSOR_FAULT"));
      return;
    }
    safetyCutoffTriggered = false; // Manual intervention clears cutoff
    currentMode = MODE_MANUAL;
    setPump(true);
    Serial.println(F("ACK:PUMP_ON"));
  }
  // Command: PUMP_OFF
  else if (strcmp(cmd, "PUMP_OFF") == 0) {
    safetyCutoffTriggered = false;
    currentMode = MODE_MANUAL;
    setPump(false);
    Serial.println(F("ACK:PUMP_OFF"));
  }
  // Command: AUTO
  else if (strcmp(cmd, "AUTO") == 0) {
    safetyCutoffTriggered = false;
    currentMode = MODE_AUTO;
    Serial.println(F("ACK:AUTO"));
  }
  // Command: SET_THRESHOLDS:<on>,<off>
  else if (strncmp(cmd, "SET_THRESHOLDS:", 15) == 0) {
    char* args = cmd + 15;
    char* comma = strchr(args, ',');
    if (comma != NULL) {
      *comma = '\0';
      int onVal = atoi(args);
      int offVal = atoi(comma + 1);

      // Validate logic: 0 <= on < off <= 100
      if (onVal >= 0 && offVal <= 100 && onVal < offVal) {
        thresholdOn = onVal;
        thresholdOff = offVal;
        Serial.print(F("ACK:SET_THRESHOLDS:"));
        Serial.print(thresholdOn);
        Serial.print(F(","));
        Serial.println(thresholdOff);
      } else {
        Serial.println(F("ERR:INVALID_THRESHOLDS_RANGE"));
      }
    } else {
      Serial.println(F("ERR:MALFORMED_THRESHOLDS_SYNTAX"));
    }
  }
  // Command: SET_CALIB:<dry>,<wet>
  else if (strncmp(cmd, "SET_CALIB:", 10) == 0) {
    char* args = cmd + 10;
    char* comma = strchr(args, ',');
    if (comma != NULL) {
      *comma = '\0';
      int dVal = atoi(args);
      int wVal = atoi(comma + 1);

      // Validate: dry must be greater than wet for capacitive sensor, within 0-1023
      if (dVal > wVal && dVal <= 1023 && wVal >= 0) {
        dryValue = dVal;
        wetValue = wVal;
        Serial.print(F("ACK:SET_CALIB:"));
        Serial.print(dryValue);
        Serial.print(F(","));
        Serial.println(wetValue);
      } else {
        Serial.println(F("ERR:INVALID_CALIBRATION_VALUES"));
      }
    } else {
      Serial.println(F("ERR:MALFORMED_CALIB_SYNTAX"));
    }
  }
  else {
    Serial.print(F("ERR:UNKNOWN_COMMAND:"));
    Serial.println(cmd);
  }
}

// ----------------------------------------------------------------------------
// ARDUINO SETUP
// ----------------------------------------------------------------------------
void setup() {
  // Configure relay pin as output and guarantee pump is strictly OFF at boot
  pinMode(RELAY_PIN, OUTPUT);
  setRelayHardware(false);
  pumpActive = false;

  // Configure analog input
  pinMode(SOIL_PIN, INPUT);

  // Initialize hardware serial
  Serial.begin(9600);
  while (!Serial && millis() < 3000) {
    ; // Wait up to 3 seconds for serial port enumeration (useful on 32u4 / Leonardo)
  }

  // Pre-seed the moving average filter
  int initialRead = analogRead(SOIL_PIN);
  for (uint8_t i = 0; i < SAMPLE_COUNT; i++) {
    rawSamples[i] = initialRead;
  }
  bufferFilled = true;

  lastTelemetryTime = millis();
  lastSampleTime = millis();
}

// ----------------------------------------------------------------------------
// ARDUINO MAIN LOOP (100% NON-BLOCKING)
// ----------------------------------------------------------------------------
void loop() {
  unsigned long currentMillis = millis();

  // 1. High-frequency analog sampling (every 20ms)
  if (currentMillis - lastSampleTime >= SAMPLE_INTERVAL) {
    lastSampleTime = currentMillis;
    rawSamples[sampleIndex] = analogRead(SOIL_PIN);
    sampleIndex = (sampleIndex + 1) % SAMPLE_COUNT;
    if (sampleIndex == 0) bufferFilled = true;
  }

  int rawVal = getAveragedRaw();
  int moisturePercent = calculateMoisturePercent(rawVal);

  // 2. Hardware Fault Detection
  if (rawVal < SENSOR_MIN_RAW || rawVal > SENSOR_MAX_RAW) {
    if (sensorOk) {
      sensorOk = false;
      // Immediate failsafe: shut off pump if sensor hardware fails!
      setPump(false);
    }
  } else {
    sensorOk = true;
  }

  // 3. Safety Run-Time Cutoff Check
  if (pumpActive) {
    if (currentMillis - pumpStartTime >= MAX_PUMP_RUN_TIME) {
      setPump(false);
      safetyCutoffTriggered = true;
      currentMode = MODE_AUTO; // Force AUTO mode per protocol specification
    }
  }

  // 4. Automatic Moisture Control with Hysteresis (Only active in AUTO mode and when sensor is healthy)
  if (currentMode == MODE_AUTO && sensorOk && !safetyCutoffTriggered) {
    if (moisturePercent < thresholdOn) {
      if (!pumpActive) {
        setPump(true);
      }
    } else if (moisturePercent > thresholdOff) {
      if (pumpActive) {
        setPump(false);
      }
    }
    // If between thresholdOn and thresholdOff: hysteresis holds previous state
  }

  // 5. Read incoming serial commands from PC
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (rxIndex > 0) {
        rxBuffer[rxIndex] = '\0';
        processCommand(rxBuffer);
        rxIndex = 0;
      }
    } else {
      if (rxIndex < RX_BUFFER_SIZE - 1) {
        rxBuffer[rxIndex++] = c;
      } else {
        // Buffer overflow protection: flush
        rxIndex = 0;
      }
    }
  }

  // 6. Periodic Telemetry Broadcast (Every 1000ms)
  if (currentMillis - lastTelemetryTime >= TELEMETRY_INTERVAL) {
    lastTelemetryTime = currentMillis;
    transmitTelemetry(rawVal, moisturePercent);
  }
}
