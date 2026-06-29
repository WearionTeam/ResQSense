#include "DFRobot_BloodOxygen_S.h"
#include <Wire.h>

// I2C setup (your original)
#define I2C_COMMUNICATION
#ifdef I2C_COMMUNICATION
  #define I2C_ADDRESS 0x57
  DFRobot_BloodOxygen_S_I2C MAX30102(&Wire, I2C_ADDRESS);
#else
#if defined(ARDUINO_AVR_UNO) || defined(ESP8266)
SoftwareSerial mySerial(4, 5);
DFRobot_BloodOxygen_S_SoftWareUart MAX30102(&mySerial, 9600);
#else
DFRobot_BloodOxygen_S_HardWareUart MAX30102(&Serial1, 9600);
#endif
#endif

// Change this to the GPIO you wired the SEN0344 INT pin to:
#define INT_PIN 34      // <--- change to the pin you used
#define I2C_SDA 21
#define I2C_SCL 22

volatile bool sampleReady = false;   // set by ISR when INT goes low
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED; // ESP32 safe (optional)

// ISR: keep it tiny!
void IRAM_ATTR sensorISR() {
  // On ESP32: mark flag in a safe way
  portENTER_CRITICAL_ISR(&mux);
  sampleReady = true;
  portEXIT_CRITICAL_ISR(&mux);
}

void setup() {
  // Init I2C (ESP32 style)
  Wire.begin(I2C_SDA, I2C_SCL);

  Serial.begin(9600);
  while (false == MAX30102.begin()) {
    Serial.println("init fail!");
    delay(1000);
  }
  Serial.println("init success!");
  Serial.println("start measuring...");

  // Start internal data collection on module
  MAX30102.sensorStartCollect();

  // Configure INT pin: module INT is open-drain active-low
  pinMode(INT_PIN, INPUT_PULLUP);

  // Attach interrupt on FALLING edge (active-low pulse)
  attachInterrupt(digitalPinToInterrupt(INT_PIN), sensorISR, FALLING);
}

void loop() {
  // If no interrupt has fired, go idle (or do other tasks)
  if (!sampleReady) {
    // optional: sleep, do other tasks, etc.
    delay(10);
    return;
  }

  // Clear the flag quickly and read the data (do heavy work outside ISR)
  portENTER_CRITICAL(&mux);
  sampleReady = false;
  portEXIT_CRITICAL(&mux);

  // Read current HR/SpO2 from module
  MAX30102.getHeartbeatSPO2();

  int spo2 = MAX30102._sHeartbeatSPO2.SPO2;
  int hr   = MAX30102._sHeartbeatSPO2.Heartbeat;

  // --- Simple validity checks (adjust thresholds to your needs) ---
  bool valid = true;
  if (spo2 < 50 || spo2 > 100) valid = false;      // SpO2 plausible range
  if (hr < 30 || hr > 220) valid = false;          // HR plausible range
  if (spo2 == 0 && hr == 0) valid = false;         // zero means no reading

  // If valid, print / send; otherwise skip or log an error
  if (valid) {
    Serial.print("SPO2 is : ");
    Serial.print(spo2);
    Serial.println("%");

    Serial.print("heart rate is : ");
    Serial.print(hr);
    Serial.println(" Times/min");

    

    // TODO: transmit this data over BLE/WiFi/Serial only when valid.
  } else {
    Serial.println("Invalid sample — skipping transmission.");
    Serial.print("Raw SPO2: "); Serial.print(spo2);
    Serial.print("  HR: "); Serial.print(hr);
    Serial.println(" ");
    
  }

  // small debounce / guard: allow the module to set INT again
  delay(10);
  // loop returns and waits for next interrupt
}