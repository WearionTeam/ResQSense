#include "sensors.h"

static Adafruit_TMP117 tmp1;
static Adafruit_TMP117 tmp2;
static Adafruit_TMP117 tmp3;

SensorNode sensors[3] = {
  { &tmp1, TMP_ADDR, false, NAN },
  { &tmp2, TMPA_ADDR, false, NAN },
  { &tmp3, TMPB_ADDR, false, NAN }
};


static bool start_fall = false;
static bool impact_check = false;
static unsigned long fall_time = 0;
static unsigned long inactive_time = 0;

static bool gnss_first_fix = false;
static double lat_ancora = 0.0;
static double lon_ancora = 0.0;
static int steps_t_gnss = 0;
static volatile double imu_radius = 0.0;
static volatile int current_pdr_state = PDR_STATIC;

static const float b0 = 0.007820, b1 = 0.015640, b2 = 0.007820;
static const float a1 = -1.734726, a2 = 0.766007;
static volatile float x1 = 0, x2 = 0, y_1 = 0, y_2 = 0;

static int steps_temp = 0, count_wd = 0;
static float max_temp = -100.0, min_temp = 100.0, dyn_treshold = 1.0, diff_mag = 0.0;
static unsigned long last_step_time = 0;

static volatile bool isrFlag = false;
static unsigned long lastKick = 0;
static unsigned long systemStartMs = 0;
static unsigned long lastMaxRead = 0;
static bool waitingForData = false;
static unsigned long lastRead_SEN = 0;

static bool Flag_lowTemp = false, Flag_highTemp = false, Flag_faultySensor = false;
static int faultySensorIndex = -1;
static bool sensorIgnored[3] = { false, false, false };
static unsigned long ignoreUntil[3] = { 0, 0, 0 };
static uint32_t counter = 0;
static float sumCalculatedMeans = 0.0f;
static uint32_t numCalculatedMeans = 0;
static float userTemperatureToSend = NAN;
static bool userTemperatureReadyToSend = false;
static uint32_t temp_counter = 0;

static int total_counter_SEN = 0;
static int HR_counter = 0, HR_total = 0, HR_mean_val = 0;
static int SPO2_counter = 0, SPO2_total = 0, SPO2_mean_val = 0;

void IRAM_ATTR onAlert() {
  isrFlag = true;
}

void startIMU()
{
    Wire.beginTransmission(IMU_ADDR);
    Wire.write(0x6B);
    Wire.write(0);
    Wire.endTransmission(true);
    Wire.beginTransmission(IMU_ADDR);
    Wire.write(0x1B);
    Wire.write(0x18);
    Wire.endTransmission(true);
    Wire.beginTransmission(IMU_ADDR);
    Wire.write(0x1C);
    Wire.write(0x18);
    Wire.endTransmission(true);
}

void startGNSS(DFRobot_GNSSAndRTC_I2C &component)
{
    while(!component.begin())
    {
        delay(500);
    }
    component.setGnss(DFRobot_GNSS::eGPS_BeiDou_GLONASS);
    component.enablePower();
}

void readIMU(sensors_data &component)
{
    Wire.beginTransmission(IMU_ADDR);
    Wire.write(0x3B);
    Wire.endTransmission(false);
    Wire.requestFrom(IMU_ADDR, 14);

    uint16_t AcX_raw = Wire.read() << 8 | Wire.read(); 
    uint16_t AcY_raw = Wire.read() << 8 | Wire.read(); 
    uint16_t AcZ_raw = Wire.read() << 8 | Wire.read(); 
    uint16_t Tmp_raw = Wire.read() << 8 | Wire.read(); 
    uint16_t GyX_raw = Wire.read() << 8 | Wire.read(); 
    uint16_t GyY_raw = Wire.read() << 8 | Wire.read(); 
    uint16_t GyZ_raw = Wire.read() << 8 | Wire.read();

    //Wire.endTransmission();

    int16_t AcX_cal = AcX_raw - IMU_OFFSET_ACCEL_Z;
    int16_t AcY_cal = AcY_raw - IMU_OFFSET_ACCEL_Y;
    int16_t AcZ_cal = AcZ_raw - IMU_OFFSET_ACCEL_Z;
    int16_t GyX_cal = GyX_raw - IMU_OFFSET_GYRO_X;
    int16_t GyY_cal = GyY_raw - IMU_OFFSET_GYRO_Y;
    int16_t GyZ_cal = GyZ_raw - IMU_OFFSET_GYRO_Z;

    component.ax = ((float)AcX_cal / IMU_ACCEL_SENSIVITY);
    component.ay = ((float)AcY_cal / IMU_ACCEL_SENSIVITY);
    component.az = ((float)AcZ_cal / IMU_ACCEL_SENSIVITY);     

    component.gx = (float)GyX_cal / IMU_GYRO_SENSIVITY;
    component.gy = (float)GyY_cal / IMU_GYRO_SENSIVITY;
    component.gz = (float)GyZ_cal / IMU_GYRO_SENSIVITY;
}

void mandown(processed_data &component,float az,float ax)
{
  if ( component.mag_a <= TRESHOLD_FALL ){
    if(!start_fall){
      start_fall = true;
      fall_time = millis();
    }
  }

  if ( start_fall ){
    if( millis() - fall_time >= FALL_TIMEOUT){
      start_fall = false;
    } else if( component.mag_a >= TRESHOLD_IMPACT){
      
      impact_check = true;
      inactive_time = millis();
      start_fall = false;

    } 
  }

  if ( impact_check ){
    if ( millis() - inactive_time >= INACTIVITY_TIME && (1 + TRESHOLD_GRAVITY >= abs(az) && abs(az) >= 1 - TRESHOLD_GRAVITY || 1 + TRESHOLD_GRAVITY >= abs(ax) && abs(ax) >= 1 - TRESHOLD_GRAVITY )) {
      impact_check = false;
      component.fall_detected = true;
    }
  }        
}

void readGNSS(sensors_data &component, DFRobot_GNSSAndRTC_I2C &gps)
{
  DFRobot_GNSS::sLonLat_t latInfo = gps.getLat();
  DFRobot_GNSS::sLonLat_t lonInfo = gps.getLon();
  uint8_t numSats = gps.getNumSatUsed();
  Serial.printf("Num of sattelites: %i\n",numSats);
  component.lat = latInfo.latitude;
  component.lon = lonInfo.lonitude;
  component.alt = gps.getAlt();    
}

void pdr(float magnitude)
{
  float t = millis()/1000.0;
  float dyn_mag = magnitude - 1;
  float filtered_mag = b0 * dyn_mag + b1 * x1 + b2 * x2 - a1 * y_1 - a2 * y_2;

  if (filtered_mag > max_temp) max_temp = filtered_mag;
  if (filtered_mag < min_temp) min_temp = filtered_mag;

  if(++count_wd >= 100)
  {
    dyn_treshold = (max_temp + min_temp) / 2.0;
    diff_mag = max_temp - min_temp;
    max_temp = -100.0; min_temp = 100.0; count_wd = 0;
  }

  if ((y_1>filtered_mag) && (y_1>y_2) && (y_1>dyn_treshold) && (diff_mag>1.0))
  {
    float delta_t = t - last_step_time;

    if((delta_t > 0.30) && (delta_t < 1.50))
    {
      steps_temp++;

      int local_state = (delta_t < 0.45 || y_1 > 8.0) ? PDR_RUN : PDR_WALK;

      if (steps_temp >= 3)
      {
        int i = (steps_temp == 3) ? 3 : 1;
        float stride_length = 0.0;

        if (local_state == PDR_WALK)
        {
          stride_length = 0.72;
        } else if (local_state == PDR_RUN)
        {
          stride_length = 0.99;
        }        
        steps_t_gnss += i;
        imu_radius += (stride_length * i);
        current_pdr_state = local_state;
      }
    } else
    {
      steps_temp = 1;
      current_pdr_state = PDR_STATIC;
    }
  }
  last_step_time = t;
  x2 = x1; x1 = dyn_mag; y_2 = y_1; y_1 = filtered_mag;
  
}

void processSensorFusion(sensors_data &measurements, processed_data &info) {
  info.timestamp_ms = measurements.timestamp_ms;
  
  // Calcula a magnitude e processa queda/PDR internamente
  info.mag_a = sqrt(measurements.ax*measurements.ax + measurements.ay*measurements.ay + measurements.az*measurements.az);
  mandown(info, measurements.az, measurements.ax);
  pdr(info.mag_a);

  // Lógica do GNSS que estava no main.cpp veio para aqui:
  if (measurements.gnss_updated) {
    if (!gnss_first_fix) { 
      lat_ancora = measurements.lat;
      lon_ancora = measurements.lon;
      gnss_first_fix = true;
    } else {
      double lat_rad = lat_ancora * (PI / 180.0);
      double dx = (measurements.lon - lon_ancora) * cos(lat_rad) * 111320.0;
      double dy = (measurements.lat - lat_ancora) * 111000.0;
      double dist_gnss = sqrt(dx*dx + dy*dy);

      if (dist_gnss > 0.1) {
          double cx = (dx / dist_gnss) * imu_radius;
          double cy = (dy / dist_gnss) * imu_radius;
          double x_est = (cx + dx) / 2.0;
          double y_est = (cy + dy) / 2.0;

          lat_ancora = lat_ancora + (y_est / 111110.0);
          lon_ancora = lon_ancora + (x_est / (cos(lat_rad) * 111320.0));
      } else {
          lat_ancora = measurements.lat;
          lon_ancora = measurements.lon;
      }
    } 
    info.lat = lat_ancora;
    info.lon = lon_ancora;
  }
}


void resetNavigationState() {
  steps_t_gnss = 0;
  imu_radius = 0.0;
}

static bool readReg16(uint8_t a, uint8_t reg, uint16_t &v) 
{
  Wire.beginTransmission(a);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)a, 2) != 2) return false;
  v = ((uint16_t)Wire.read() << 8) | Wire.read();
  return true;
}

static bool writeReg16(uint8_t a, uint8_t reg, uint16_t v) 
{
  Wire.beginTransmission(a);
  Wire.write(reg);
  Wire.write((uint8_t)(v >> 8));
  Wire.write((uint8_t)(v & 0xFF));
  return Wire.endTransmission() == 0;
}

void initVitalsHardware(DFRobot_BloodOxygen_S_I2C &sen) {
  while (false == sen.begin()) {
    Serial.println("ERR, MAX30102 init fail!");
    delay(1000);
  }
  sen.sensorStartCollect();

  initTMP117(sensors[0]);
  initTMP117(sensors[1]);
  initTMP117(sensors[2]);

  pinMode(ISR_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(ISR_PIN), onAlert, FALLING);

  
  kickAllSensors();
}

bool initTMP117(SensorNode &s) 
{
  s.ok = s.dev->begin(s.addr, &Wire);
  Serial.printf("INIT | sensor_addr=0x%02X | status=%s\r\n", s.addr, s.ok ? "OK" : "FAIL");
  if (!s.ok) return false;
  s.dev->interruptsActiveLow(true);
  uint16_t cfg = 0;
  if (readReg16(s.addr, 0x01, cfg)) 
  {
    cfg |= 0x0040;
    writeReg16(s.addr, 0x01, cfg);
    Serial.printf("CFG | sensor_addr=0x%02X | config=0x%04X\r\n", s.addr, cfg);
  }
  s.dev->setMeasurementMode(TMP117_MODE_SHUTDOWN);
  return true;
}

void kickAllSensors() 
{
  isrFlag = false;
  waitingForData = true;
  lastKick = millis();
  for (int i = 0; i < 3; i++) 
  {
    if (sensors[i].ok) sensors[i].dev->setMeasurementMode(TMP117_MODE_ONE_SHOT);
  }
  Serial.printf("KICK | ms=%lu\r\n", lastKick);
}

void ignoreSensor(int idx, unsigned long now) 
{
  sensorIgnored[idx] = true;
  ignoreUntil[idx] = now + IGNORE_SENSOR_INTERVAL;
  Flag_faultySensor = true;
  faultySensorIndex = idx;
  Serial.printf("SENSOR_IGNORED | sensor_addr=0x%02X | ms=%lu | ignoreUntil=%lu\r\n", sensors[idx].addr, now, ignoreUntil[idx]);
}

bool faultySensorDetection(bool validMask[3], float temps[3], unsigned long now) 
{
  Flag_faultySensor = false;
  faultySensorIndex = -1;
  if (!validMask[0] || !validMask[1] || !validMask[2]) 
  {
    Serial.println("FAULT_CHECK | status=SKIPPED | reason=not_all_sensors_available");
    return false;
  }
  float d_48_4A = fabs(temps[0] - temps[1]);
  float d_48_4B = fabs(temps[0] - temps[2]);
  float d_4A_4B = fabs(temps[1] - temps[2]);
  Serial.printf("FAULT_CHECK | d_48_4A=%.3f | d_48_4B=%.3f | d_4A_4B=%.3f\r\n", d_48_4A, d_48_4B, d_4A_4B);

  if (d_48_4A > FAR_SENSOR_THRESHOLD && d_4A_4B > FAR_SENSOR_THRESHOLD && d_48_4A > d_48_4B && d_4A_4B > d_48_4B) 
  {
    validMask[1] = false; ignoreSensor(1, now);
    Serial.println("FAULT_DETECTED | faultySensor=0x4A");
    return true;
  }
  if (d_48_4B > CLOSE_PAIR_THRESHOLD) 
  {
    if (d_48_4A > d_4A_4B) { validMask[0] = false; ignoreSensor(0, now); Serial.println("FAULT_DETECTED | faultySensor=0x48"); return true; }
    else if (d_4A_4B > d_48_4A) { validMask[2] = false; ignoreSensor(2, now); Serial.println("FAULT_DETECTED | faultySensor=0x4B"); return true; }
  }
  Serial.println("FAULT_DETECTED | faultySensor=NONE");
  return false;
}

void readPPG(DFRobot_BloodOxygen_S_I2C &sen, sensors_data &component) 
{
  sen.getHeartbeatSPO2();
  component.spo2 = sen._sHeartbeatSPO2.SPO2;
  component.hr = sen._sHeartbeatSPO2.Heartbeat;
}

void processPPG(sensors_data &component, processed_data &info)
{
  total_counter_SEN++;
  if (component.spo2 > 50 && component.spo2 < 101) {
    SPO2_counter++;
    SPO2_total += component.spo2;
  }
  if (component.hr > 5 && component.hr < 220) {
    HR_counter++;
    HR_total += component.hr;
  } 

  if (total_counter_SEN >= SEN_END_CYCLE) {
  SPO2_mean_val = (SPO2_counter > 0) ? (SPO2_total / SPO2_counter) : 0;
  HR_mean_val = (HR_counter > 0) ? (HR_total / HR_counter) : 0;
  
  info.ppg_alert = (SPO2_mean_val < SPO2_MIN) || (HR_mean_val < HR_MIN) || (HR_mean_val > HR_MAX);

  total_counter_SEN = 0; HR_counter = 0; HR_total = 0; SPO2_counter = 0; SPO2_total = 0;
  }

  info.spo2 = SPO2_mean_val;
  info.hr = HR_mean_val;
}

void readTemperatureSensors(float temps[3], bool validMask[3], unsigned long now) {
  for (int i = 0; i < 3; i++) {
    // Inicialização segura
    temps[i] = NAN;
    validMask[i] = false;

    // Verificar se o tempo de ignorar já passou
    if (sensorIgnored[i] && now >= ignoreUntil[i]) {
      sensorIgnored[i] = false;
    }
    
    if (!sensors[i].ok) continue;

    // Leitura do sensor
    sensors_event_t ev;
    sensors[i].dev->getEvent(&ev);
    
    if (!isnan(ev.temperature) && !sensorIgnored[i]) {
      temps[i] = ev.temperature;
      validMask[i] = true;
    }
  }
}

void processTemperatureData(float temps[3], bool validMask[3], unsigned long now, processed_data &info) {
  // 1. Deteção de falhas (pode alterar a validMask)
  faultySensorDetection(validMask, temps, now);

  // 2. Cálculo da soma e contagem de sensores válidos
  float sum = 0.0f;
  uint8_t validCount = 0;
  for (int i = 0; i < 3; i++) {
    if (validMask[i]) { 
      sum += temps[i]; 
      validCount++; 
    }
  }

  // 3. Atualizar struct com base nas médias
  if (validCount > 0) {
    float mean = sum / validCount;
    
    temp_counter++;
    sumCalculatedMeans += mean;
    numCalculatedMeans++;

    info.temp = sumCalculatedMeans / numCalculatedMeans;
    info.temp_alert = (info.temp < TEMP_MIN) || (info.temp > TEMP_MAX);
  } else {
    info.temp = NAN; 
  }

  // 4. Acordar sensores para o próximo ciclo de leitura
  //kickAllSensors();
}

