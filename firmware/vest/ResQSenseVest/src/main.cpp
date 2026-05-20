#include <Arduino.h>
#include "config.h"
#include "protocol.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include <math.h>
#include <Wire.h>

// Bibliotecas dos Sensores Vitais
#include <Adafruit_TMP117.h>
#include <Adafruit_Sensor.h>
#include <DFRobot_BloodOxygen_S.h>

const char* TAG = "TASK";

// ========= MUTEX I2C (Crucial no FreeRTOS) =========
SemaphoreHandle_t i2cMutex;

// ========= OBJETOS E VARIÁVEIS DOS SENSORES VITAIS =========
Adafruit_TMP117 tmp1, tmp2, tmp3;
DFRobot_BloodOxygen_S_I2C MAX30102(&Wire, MAX_ADDR); // Usa MAX_ADDR do config.h

struct SensorNode {
  Adafruit_TMP117* dev;
  uint8_t addr;
  bool ok;
  float lastTemp;
};

SensorNode sensors[3] = {
  { &tmp1, TMP_ADDR, false, NAN },
  { &tmp2, TMPA_ADDR, false, NAN },
  { &tmp3, TMPB_ADDR, false, NAN }
};

volatile bool isrFlag = false;
unsigned long lastKick = 0;
unsigned long systemStartMs = 0;
unsigned long lastMaxRead = 0;
bool waitingForData = false;

// Variáveis de Lógica TMP117
bool Flag_lowTemp = false, Flag_highTemp = false, Flag_faultySensor = false;
int faultySensorIndex = -1;
bool sensorIgnored[3] = { false, false, false };
unsigned long ignoreUntil[3] = { 0, 0, 0 };
uint32_t counter = 0;
float sumCalculatedMeans = 0.0f;
uint32_t numCalculatedMeans = 0;
float userTemperatureToSend = NAN;
bool userTemperatureReadyToSend = false;

// Variáveis de Lógica PPG
int total_counter = 0, HR_counter = 0, SPO2_counter = 0;
long HR_total = 0, SPO2_total = 0;
unsigned long lastPPGSecond = 0;
long ppgHRSum = 0, ppgSPO2Sum = 0;
int ppgSampleCount = 0;

// Valores Finais Prontos para Telemetria
uint8_t current_BPM = 0;
uint8_t current_SPO2 = 0;

// ========= HANDLERS DAS TASKS =========
TaskHandle_t ReadIMUHandle = NULL;
TaskHandle_t ReadVitalsHandle = NULL;

// ========= ISR ALERTA =========
void IRAM_ATTR onAlert() {
  isrFlag = true;
}

// ========= FUNÇÕES AUXILIARES I2C (Protegidas) =========
static bool readReg16(uint8_t a, uint8_t reg, uint16_t &v) {
  bool ret = false;
  if (xSemaphoreTake(i2cMutex, portMAX_DELAY)) {
    Wire.beginTransmission(a);
    Wire.write(reg);
    if (Wire.endTransmission(false) == 0 && Wire.requestFrom((int)a, 2) == 2) {
      v = ((uint16_t)Wire.read() << 8) | Wire.read();
      ret = true;
    }
    xSemaphoreGive(i2cMutex);
  }
  return ret;
}

static bool writeReg16(uint8_t a, uint8_t reg, uint16_t v) {
  bool ret = false;
  if (xSemaphoreTake(i2cMutex, portMAX_DELAY)) {
    Wire.beginTransmission(a);
    Wire.write(reg);
    Wire.write((uint8_t)(v >> 8));
    Wire.write((uint8_t)(v & 0xFF));
    ret = (Wire.endTransmission() == 0);
    xSemaphoreGive(i2cMutex);
  }
  return ret;
}

// ========= FUNÇÕES TMP117 =========
bool initTMP117(SensorNode &s) {
  if (xSemaphoreTake(i2cMutex, portMAX_DELAY)) {
    s.ok = s.dev->begin(s.addr, &Wire);
    xSemaphoreGive(i2cMutex);
  }
  if (!s.ok) return false;
  
  s.dev->interruptsActiveLow(true);
  uint16_t cfg = 0;
  if (readReg16(s.addr, 0x01, cfg)) {
    cfg |= 0x0040;
    writeReg16(s.addr, 0x01, cfg);
  }
  
  if (xSemaphoreTake(i2cMutex, portMAX_DELAY)) {
    s.dev->setMeasurementMode(TMP117_MODE_SHUTDOWN);
    xSemaphoreGive(i2cMutex);
  }
  return true;
}

void kickAllSensors() {
  isrFlag = false;
  waitingForData = true;
  lastKick = millis();
  if (xSemaphoreTake(i2cMutex, portMAX_DELAY)) {
    for (int i = 0; i < 3; i++) {
      if (sensors[i].ok) sensors[i].dev->setMeasurementMode(TMP117_MODE_ONE_SHOT);
    }
    xSemaphoreGive(i2cMutex);
  }
}

void ignoreSensor(int idx, unsigned long now) {
  sensorIgnored[idx] = true;
  ignoreUntil[idx] = now + IGNORE_TIME_MS;
  Flag_faultySensor = true;
  faultySensorIndex = idx;
}

bool faultySensorDetection(bool validMask[3], float temps[3], unsigned long now) {
  Flag_faultySensor = false;
  faultySensorIndex = -1;
  if (!validMask[0] || !validMask[1] || !validMask[2]) return false;
  
  float d_48_4A = fabs(temps[0] - temps[1]);
  float d_48_4B = fabs(temps[0] - temps[2]);
  float d_4A_4B = fabs(temps[1] - temps[2]);

  if (d_48_4A > FAR_SENSOR_THRESHOLD && d_4A_4B > FAR_SENSOR_THRESHOLD && d_48_4A > d_48_4B && d_4A_4B > d_48_4B) {
    validMask[1] = false; ignoreSensor(1, now);
    return true;
  }
  if (d_48_4B > CLOSE_PAIR_THRESHOLD) {
    if (d_48_4A > d_4A_4B) { validMask[0] = false; ignoreSensor(0, now); return true; }
    else if (d_4A_4B > d_48_4A) { validMask[2] = false; ignoreSensor(2, now); return true; }
  }
  return false;
}

// ========= TAREFA IMU =========
void startIMU(){
  if (xSemaphoreTake(i2cMutex, portMAX_DELAY)) {
    Wire.beginTransmission(IMU_ADDR); Wire.write(0x6B); Wire.write(0); Wire.endTransmission(true);
    Wire.beginTransmission(IMU_ADDR); Wire.write(0x1B); Wire.write(0x18); Wire.endTransmission(true);
    Wire.beginTransmission(IMU_ADDR); Wire.write(0x1C); Wire.write(0x18); Wire.endTransmission(true);
    xSemaphoreGive(i2cMutex);
  }
}

void ReadIMU(void *parameter) {
  for(;;){
    if (xSemaphoreTake(i2cMutex, portMAX_DELAY)) {
      Wire.beginTransmission(IMU_ADDR);
      Wire.write(0x3B);
      Wire.endTransmission(false);
      Wire.requestFrom((int)IMU_ADDR, 14);
      
      uint16_t AcX_raw = Wire.read() << 8 | Wire.read(); 
      uint16_t AcY_raw = Wire.read() << 8 | Wire.read(); 
      uint16_t AcZ_raw = Wire.read() << 8 | Wire.read(); 
      uint16_t Tmp_raw = Wire.read() << 8 | Wire.read(); 
      uint16_t GyX_raw = Wire.read() << 8 | Wire.read(); 
      uint16_t GyY_raw = Wire.read() << 8 | Wire.read(); 
      uint16_t GyZ_raw = Wire.read() << 8 | Wire.read();
      xSemaphoreGive(i2cMutex); // Liberta I2C após leitura
      
      int16_t AcX_cal = AcX_raw - IMU_OFFSET_ACCEL_Z;
      int16_t AcY_cal = AcY_raw - IMU_OFFSET_ACCEL_Y;
      int16_t AcZ_cal = AcZ_raw - IMU_OFFSET_ACCEL_Z;
      int16_t GyX_cal = GyX_raw - IMU_OFFSET_GYRO_X;
      int16_t GyY_cal = GyY_raw - IMU_OFFSET_GYRO_Y;
      int16_t GyZ_cal = GyZ_raw - IMU_OFFSET_GYRO_Z;

      float ax = ((float)AcX_cal / IMU_ACCEL_SENSIVITY);
      float ay = ((float)AcY_cal / IMU_ACCEL_SENSIVITY);
      float az = ((float)AcZ_cal / IMU_ACCEL_SENSIVITY);     
      float gx = (float)GyX_cal / IMU_GYRO_SENSIVITY;
      float gy = (float)GyY_cal / IMU_GYRO_SENSIVITY;
      float gz = (float)GyZ_cal / IMU_GYRO_SENSIVITY;

      float mag_a = sqrt(pow(ax,2) + pow(ay,2) + pow(az,2));
      Serial.printf("[%s] Magnitude de: %.2f\n", TAG, mag_a);
    }
    vTaskDelay(pdMS_TO_TICKS(100)); // O Gemini disse para colocar isto para não estar sempre a repetir esta tas
  }
}

// ========= TAREFA DOS SINAIS VITAIS =========
void ReadVitals(void *parameter) {
  for(;;) {
    unsigned long now = millis();

    // --- LÓGICA PPG ---
    if (isrFlag || (now - lastMaxRead > 200)) {
      if (isrFlag && !waitingForData) isrFlag = false;

      // Protege I2C
      if (xSemaphoreTake(i2cMutex, portMAX_DELAY)) {
        MAX30102.getHeartbeatSPO2();
        xSemaphoreGive(i2cMutex);
      }
      
      lastMaxRead = now;
      int spo2 = MAX30102._sHeartbeatSPO2.SPO2;
      int hr = MAX30102._sHeartbeatSPO2.Heartbeat;

      if (spo2 > 0) {
        ppgHRSum += hr; ppgSPO2Sum += spo2; ppgSampleCount++;
        total_counter++;
        if (hr != -1) { HR_counter++; HR_total += hr; }
        if (spo2 != -1) { SPO2_counter++; SPO2_total += spo2; }

        if (total_counter >= 60) {
          float HR_mean = (HR_counter > 0) ? (float)HR_total / HR_counter : 0;
          float SPO2_mean = (SPO2_counter > 0) ? (float)SPO2_total / SPO2_counter : 0;
          total_counter = 0; HR_counter = 0; SPO2_counter = 0; HR_total = 0; SPO2_total = 0;
        }
      }
    }

    if (now - lastPPGSecond >= 1000) {
      if (ppgSampleCount > 0) {
        current_SPO2 = ppgSPO2Sum / ppgSampleCount;
        current_BPM = ppgHRSum / ppgSampleCount;
      }
      ppgHRSum = 0; ppgSPO2Sum = 0; ppgSampleCount = 0;
      lastPPGSecond = now;
    }

    // --- LÓGICA TEMPERATURA ---
    if (!waitingForData && (now - lastKick >= PERIOD_MS)) kickAllSensors();

    if (waitingForData && (isrFlag || digitalRead(ALERT_PIN) == LOW)) {
      isrFlag = false;
      waitingForData = false;
      float mean = NAN; uint8_t validCount = 0;
      float temps[3] = { NAN, NAN, NAN };
      bool validMask[3] = { false, false, false };
      unsigned long tRead = millis();

      if (xSemaphoreTake(i2cMutex, portMAX_DELAY)) {
        for (int i = 0; i < 3; i++) {
          if (sensorIgnored[i] && tRead >= ignoreUntil[i]) sensorIgnored[i] = false;
          if (!sensors[i].ok) { sensors[i].lastTemp = NAN; continue; }
          
          sensors_event_t ev;
          sensors[i].dev->getEvent(&ev);
          sensors[i].lastTemp = ev.temperature;
          if (!isnan(ev.temperature) && !sensorIgnored[i]) { temps[i] = ev.temperature; validMask[i] = true; }
        }
        xSemaphoreGive(i2cMutex);
      }

      faultySensorDetection(validMask, temps, tRead);

      float sum = 0.0f;
      for (int i = 0; i < 3; i++) { if (validMask[i]) { sum += temps[i]; validCount++; } }

      if (validCount > 0) {
        mean = sum / validCount;
        if ((tRead - systemStartMs) >= IGNORE_TIME_MS) {
          Flag_lowTemp = (mean < MIN_THRESHOLD);
          Flag_highTemp = (mean > MAX_THRESHOLD);
          counter++; sumCalculatedMeans += mean; numCalculatedMeans++;
          
          if (counter % (60000UL / PERIOD_MS) == 0) {
            userTemperatureToSend = sumCalculatedMeans / numCalculatedMeans;
            userTemperatureReadyToSend = true;
            sumCalculatedMeans = 0; numCalculatedMeans = 0;
            Serial.printf("TEMP PRONTA: %.2fC | SPO2: %d%% | BPM: %d\n", userTemperatureToSend, current_SPO2, current_BPM);
          }
        }
      }
    }
    vTaskDelay(pdMS_TO_TICKS(10)); // O Gemini disse para colocar isto para não estar sempre a repetir esta task
  }
}

// ========= SETUP =========
void setup() {
  Serial.begin(115200);
  delay(1000);
  
  // 1. Criar o Mutex do I2C
  i2cMutex = xSemaphoreCreateMutex();
  if (i2cMutex == NULL) {
    Serial.println("Erro ao criar Mutex do I2C");
    while(1);
  }

  // 2. Iniciar I2C
  Wire.begin(I2C_SDA, I2C_SCL);

  // 3. Setup dos Sensores
  startIMU();
  
  bool ppgStatus = false;
  if (xSemaphoreTake(i2cMutex, portMAX_DELAY)) {
    ppgStatus = MAX30102.begin();
    xSemaphoreGive(i2cMutex);
  }
  if(ppgStatus) MAX30102.sensorStartCollect();
  else Serial.println("Erro ao iniciar MAX30102!");

  initTMP117(sensors[0]);
  initTMP117(sensors[1]);
  initTMP117(sensors[2]);

  pinMode(ALERT_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(ALERT_PIN), onAlert, FALLING);
  
  systemStartMs = millis();
  kickAllSensors();

  // 4. Iniciar Tasks em Cores (O ESP32 e o S3 são Dual-Core)
  xTaskCreatePinnedToCore(
    ReadIMU, 
    "ReadIMU", 
    4096, 
    NULL, 
    1, 
    &ReadIMUHandle, 
    1);
  xTaskCreatePinnedToCore(
    ReadVitals, 
    "ReadVitals", 
    4096, 
    NULL, 
    1, 
    &ReadVitalsHandle, 
    1);
}

void loop() {
  // vTaskDelete(NULL); // Recomendação do Geimini para não gastar memoria
}