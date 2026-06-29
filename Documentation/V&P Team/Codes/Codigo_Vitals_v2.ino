#include <Wire.h>
#include <Adafruit_TMP117.h>
#include <Adafruit_Sensor.h>
#include <DFRobot_BloodOxygen_S.h>
#include <math.h>

// ========= CONFIGURAÇÕES DE PINOS =========
#define I2C_SDA 21
#define I2C_SCL 22
#define ALERT_PIN 27

// ========= CONSTANTES TMP117 =========
#define PERIOD_MS 10000UL
#define IGNORE_TIME_MS 180000UL 
#define MIN_THRESHOLD 2.0f
#define MAX_THRESHOLD 39.0f
#define CLOSE_PAIR_THRESHOLD 1.0f 
#define FAR_SENSOR_THRESHOLD 2.5f 
#define TMP117_ADDR_1 0x48
#define TMP117_ADDR_2 0x4A
#define TMP117_ADDR_3 0x4B

// ========= CONSTANTES PPG (MAX30102) =========
#define MAX_I2C_ADDRESS 0x57
const int SPO2_min = 95;
const int HR_max = 159;
const int HR_min = 44;
const int fim_ciclo_SEN = 60;

// ========= OBJETOS =========
Adafruit_TMP117 tmp1, tmp2, tmp3;
DFRobot_BloodOxygen_S_I2C MAX30102(&Wire, MAX_I2C_ADDRESS);

struct SensorNode {
  Adafruit_TMP117* dev;
  uint8_t addr;
  bool ok;
  float lastTemp;
};

SensorNode sensors[3] = {
  { &tmp1, TMP117_ADDR_1, false, NAN },
  { &tmp2, TMP117_ADDR_2, false, NAN },
  { &tmp3, TMP117_ADDR_3, false, NAN }
};

// ========= VARIÁVEIS GLOBAIS =========
volatile bool isrFlag = false;
unsigned long lastKick = 0;
unsigned long systemStartMs = 0;
unsigned long lastMaxRead = 0;
bool waitingForData = false;

// Variáveis TMP117
bool Flag_lowTemp = false, Flag_highTemp = false, Flag_faultySensor = false;
int faultySensorIndex = -1;
bool sensorIgnored[3] = { false, false, false };
unsigned long ignoreUntil[3] = { 0, 0, 0 };
uint32_t counter = 0;
float sumCalculatedMeans = 0.0f;
uint32_t numCalculatedMeans = 0;
float userTemperatureToSend = NAN;
bool userTemperatureReadyToSend = false;

// Variáveis PPG (Fluxograma)
int total_counter_SEN = 0;
int HR_counter = 0;
int HR_total = 0;
int HR_mean = 0;
int SPO2_counter = 0;
int SPO2_total = 0;
int SPO2_mean = 0;
bool HR_flag_min = false;
bool HR_flag_max = false;
bool SPO2_flag_min = false;
unsigned long lastRead_SEN = 0; 
const unsigned long intervalo_SEN = 1000; // 1000 ms = 1 segundo

// interrupt SEN0344
void IRAM_ATTR onAlert() {
  isrFlag = true;
}

// ========= FUNÇÕES AUXILIARES I2C =========
static bool readReg16(uint8_t a, uint8_t reg, uint16_t &v) {
  Wire.beginTransmission(a);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)a, 2) != 2) return false;
  v = ((uint16_t)Wire.read() << 8) | Wire.read();
  return true;
}

static bool writeReg16(uint8_t a, uint8_t reg, uint16_t v) {
  Wire.beginTransmission(a);
  Wire.write(reg);
  Wire.write((uint8_t)(v >> 8));
  Wire.write((uint8_t)(v & 0xFF));
  return Wire.endTransmission() == 0;
}

// ========= LÓGICA TMP117 (SEM ALTERAÇÃO) =========
bool initTMP117(SensorNode &s) {
  s.ok = s.dev->begin(s.addr, &Wire);
  Serial.printf("INIT | sensor_addr=0x%02X | status=%s\r\n", s.addr, s.ok ? "OK" : "FAIL");
  if (!s.ok) return false;
  s.dev->interruptsActiveLow(true);
  uint16_t cfg = 0;
  if (readReg16(s.addr, 0x01, cfg)) {
    cfg |= 0x0040;
    writeReg16(s.addr, 0x01, cfg);
    Serial.printf("CFG | sensor_addr=0x%02X | config=0x%04X\r\n", s.addr, cfg);
  }
  s.dev->setMeasurementMode(TMP117_MODE_SHUTDOWN);
  return true;
}

void kickAllSensors() {
  isrFlag = false;
  waitingForData = true;
  lastKick = millis();
  for (int i = 0; i < 3; i++) {
    if (sensors[i].ok) sensors[i].dev->setMeasurementMode(TMP117_MODE_ONE_SHOT);
  }
  Serial.printf("KICK | ms=%lu\r\n", lastKick);
}

void ignoreSensor(int idx, unsigned long now) {
  sensorIgnored[idx] = true;
  ignoreUntil[idx] = now + IGNORE_TIME_MS;
  Flag_faultySensor = true;
  faultySensorIndex = idx;
  Serial.printf("SENSOR_IGNORED | sensor_addr=0x%02X | ms=%lu | ignoreUntil=%lu\r\n", sensors[idx].addr, now, ignoreUntil[idx]);
}

bool faultySensorDetection(bool validMask[3], float temps[3], unsigned long now) {
  Flag_faultySensor = false;
  faultySensorIndex = -1;
  if (!validMask[0] || !validMask[1] || !validMask[2]) {
    Serial.println("FAULT_CHECK | status=SKIPPED | reason=not_all_sensors_available");
    return false;
  }
  float d_48_4A = fabs(temps[0] - temps[1]);
  float d_48_4B = fabs(temps[0] - temps[2]);
  float d_4A_4B = fabs(temps[1] - temps[2]);
  Serial.printf("FAULT_CHECK | d_48_4A=%.3f | d_48_4B=%.3f | d_4A_4B=%.3f\r\n", d_48_4A, d_48_4B, d_4A_4B);

  if (d_48_4A > FAR_SENSOR_THRESHOLD && d_4A_4B > FAR_SENSOR_THRESHOLD && d_48_4A > d_48_4B && d_4A_4B > d_48_4B) {
    validMask[1] = false; ignoreSensor(1, now);
    Serial.println("FAULT_DETECTED | faultySensor=0x4A");
    return true;
  }
  if (d_48_4B > CLOSE_PAIR_THRESHOLD) {
    if (d_48_4A > d_4A_4B) { validMask[0] = false; ignoreSensor(0, now); Serial.println("FAULT_DETECTED | faultySensor=0x48"); return true; }
    else if (d_4A_4B > d_48_4A) { validMask[2] = false; ignoreSensor(2, now); Serial.println("FAULT_DETECTED | faultySensor=0x4B"); return true; }
  }
  Serial.println("FAULT_DETECTED | faultySensor=NONE");
  return false;
}

// ========= SETUP =========
void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println("START");
  Wire.begin(I2C_SDA, I2C_SCL);
  systemStartMs = millis();

  while (false == MAX30102.begin()) { Serial.println("ERR, SEN0344 init fail!"); delay(1000); }
  MAX30102.sensorStartCollect();

  initTMP117(sensors[0]);
  initTMP117(sensors[1]);
  initTMP117(sensors[2]);

  pinMode(ALERT_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(ALERT_PIN), onAlert, FALLING);
  kickAllSensors();
}

// ========= LOOP =========
void loop() {
  unsigned long now = millis();

  // receber dados SEN0344
  if (isrFlag || (now - lastRead_SEN > intervalo_SEN)) {
    if (isrFlag && !waitingForData) { isrFlag = false; }

    MAX30102.getHeartbeatSPO2();
    lastRead_SEN = millis();
    int spo2 = MAX30102._sHeartbeatSPO2.SPO2;
    int hr = MAX30102._sHeartbeatSPO2.Heartbeat;

    total_counter_SEN++;
      Serial.print("spo2,");
      Serial.println(spo2);
      Serial.print("hr,");
      Serial.println(hr);
    if (spo2 > 50 && spo2 < 101){
      SPO2_counter++;
      SPO2_total =  SPO2_total + spo2;
    }
    if (hr > 5 && hr < 220){
      HR_counter++;
      HR_total =  HR_total + hr;
    } 
  }

  // algoritmo_SEN0344
  if (total_counter_SEN == fim_ciclo_SEN){
    // caculo das médias 
    if (SPO2_counter > 0) {
      SPO2_mean = (SPO2_total / SPO2_counter);
    } else {
      SPO2_mean = 0; // Valor de segurança se falhou tudo
    }
    if (HR_counter > 0) {
      HR_mean = (HR_total / HR_counter);
    } else {
      HR_mean = 0;
    }
    // envio das médias SEN
    Serial.print("SPO2_mean,");
    Serial.println(SPO2_mean);
    Serial.print("HR_mean,");
    Serial.println(HR_mean);
    //FLAGss
    if (SPO2_mean < SPO2_min){
      SPO2_flag_min = true;
    }else{
      SPO2_flag_min = false;
    }
    if (HR_mean < HR_min){
      HR_flag_min = true;
    }else{
      HR_flag_min = false;
    }
    if (HR_mean > HR_max){
      HR_flag_max = true;
    }else{
      HR_flag_min = false;
    }
    // envio das flags
    Serial.print("SPO2_flag_min,");
    Serial.println(SPO2_flag_min);
    Serial.print("HR_flag_min,");
    Serial.println(HR_flag_min);
    Serial.print("HR_flag_max,");
    Serial.println(HR_flag_max);
    // meter tds as variaveis a 0 
    total_counter_SEN = 0;
    HR_counter = 0;
    HR_total = 0;
    HR_mean = 0;
    SPO2_counter = 0;
    SPO2_total = 0;
    SPO2_mean = 0;
    lastRead_SEN = millis();
  }
  

  // --- LÓGICA TEMPERATURA (EXATAMENTE COMO SOLICITADO) ---
  if (!waitingForData && (now - lastKick >= PERIOD_MS)) {
    kickAllSensors();
  }

  if (waitingForData && (isrFlag || digitalRead(ALERT_PIN) == LOW)) {
    isrFlag = false;
    waitingForData = false;
    float mean = NAN; uint8_t validCount = 0;
    float temps[3] = { NAN, NAN, NAN };
    bool validMask[3] = { false, false, false };
    unsigned long tRead = millis();

    // Reativa sensores ignorados e lê dados
    for (int i = 0; i < 3; i++) {
      if (sensorIgnored[i] && tRead >= ignoreUntil[i]) {
        sensorIgnored[i] = false;
        Serial.printf("SENSOR_REACTIVATED | sensor_addr=0x%02X | ms=%lu\r\n", sensors[i].addr, tRead);
      }
      if (!sensors[i].ok) { sensors[i].lastTemp = NAN; continue; }
      sensors_event_t ev;
      sensors[i].dev->getEvent(&ev);
      sensors[i].lastTemp = ev.temperature;
      if (!isnan(ev.temperature) && !sensorIgnored[i]) { temps[i] = ev.temperature; validMask[i] = true; }
    }

    faultySensorDetection(validMask, temps, tRead);

    float sum = 0.0f;
    for (int i = 0; i < 3; i++) { if (validMask[i]) { sum += temps[i]; validCount++; } }

    if (validCount > 0) {
      mean = sum / validCount;
      // Impressão RAW (Como o código original apresentava)
      bool anyIgnored = sensorIgnored[0] || sensorIgnored[1] || sensorIgnored[2];
      Flag_faultySensor = anyIgnored;
      if (anyIgnored) { for(int i=0; i<3; i++) if(sensorIgnored[i]) faultySensorIndex = i; } else { faultySensorIndex = -1; }

      Serial.printf("RAW | ms=%lu", tRead);
      for (int i = 0; i < 3; i++) {
        Serial.printf(" | sensor_%d_addr=0x%02X", i + 1, sensors[i].addr);
        if (sensors[i].ok && !isnan(sensors[i].lastTemp)) Serial.printf(" | sensor_%d_temp=%.3f", i + 1, sensors[i].lastTemp);
        else Serial.print(" | sensor_%d_temp=NA");
        Serial.printf(" | sensor_%d_ignored=%d", i + 1, sensorIgnored[i]);
        if (sensorIgnored[i]) Serial.printf(" | sensor_%d_ignoreUntil=%lu", i + 1, ignoreUntil[i]);
      }
      Serial.printf(" | Flag_faultySensor=%d", Flag_faultySensor);
      if (faultySensorIndex >= 0) Serial.printf(" | faultySensor_addr=0x%02X", sensors[faultySensorIndex].addr);
      else Serial.print(" | faultySensor_addr=NONE");
      Serial.println();

      if ((tRead - systemStartMs) < IGNORE_TIME_MS) {
        Serial.printf("STARTUP_IGNORE | ms=%lu | mean=%.3f | validCount=%u | remainingMs=%lu\r\n", 
                      tRead, mean, validCount, IGNORE_TIME_MS - (tRead - systemStartMs));
      } else {
        // Algoritmo TMP117
        Flag_lowTemp = (mean < MIN_THRESHOLD);
        Flag_highTemp = (mean > MAX_THRESHOLD);
        counter++; sumCalculatedMeans += mean; numCalculatedMeans++;
        
        float sampleMean = sumCalculatedMeans / numCalculatedMeans;
        userTemperatureReadyToSend = false;
        if (counter % (60000UL / PERIOD_MS) == 0) {
          userTemperatureToSend = sampleMean;
          userTemperatureReadyToSend = true;
          Serial.printf("USER_TEMP_READY | ms=%lu | userTemperatureToSend=%.3f\r\n", tRead, userTemperatureToSend);
          sumCalculatedMeans = 0; numCalculatedMeans = 0;
        }
        Serial.printf("DATA | ms=%lu | mean=%.3f | validCount=%u | Flag_lowTemp=%d | Flag_highTemp=%d | Flag_faultySensor=%d | counter=%lu | sampleMean=%.3f | readyToSend=%d\r\n",
                      tRead, mean, validCount, Flag_lowTemp, Flag_highTemp, Flag_faultySensor, counter, sampleMean, userTemperatureReadyToSend);
      }
    }
  }
}