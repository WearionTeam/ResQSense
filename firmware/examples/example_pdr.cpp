#include "DFRobot_GNSS.h"
#include <Wire.h>
#include <math.h>

#define MPU6050_ADDR 0x68

// GNSS na linha I2C secundária
DFRobot_GNSS_I2C gnss(&Wire1, 0x66); 

// ==========================================
// VARIÁVEIS PARTILHADAS & MUTEX (Proteção Dual-Core)
// ==========================================
SemaphoreHandle_t mutex_dados;

volatile int passos_desde_gnss = 0;
volatile double raio_imu_metros = 0.0;
String estado_atual = "PARADO";

// Flag de Sincronização de Arranque
volatile bool imu_calibrado = false; 

// ==========================================
// VARIÁVEIS GLOBAIS DO GNSS (PoC)
// ==========================================
double lat_ancora = 0.0;
double lon_ancora = 0.0;
bool primeiro_fix_obtido = false;

// ==========================================
// TASK 1: IMU & PEDÓMETRO (Corre no Core 1)
// ==========================================
void TaskIMU(void *pvParameters) {
  Serial.println("Core 1: A calibrar IMU (Aguarde 10 segundos)...");
  
  Wire.beginTransmission(MPU6050_ADDR);
  Wire.write(0x6B); Wire.write(0);
  Wire.endTransmission(true);

  double soma_mag = 0;
  for (int i = 0; i < 1000; i++) {
    Wire.beginTransmission(MPU6050_ADDR);
    Wire.write(0x3B); Wire.endTransmission(false);
    Wire.requestFrom(MPU6050_ADDR, 6, true);
    
    int16_t ax_raw = Wire.read() << 8 | Wire.read();
    int16_t ay_raw = Wire.read() << 8 | Wire.read();
    int16_t az_raw = Wire.read() << 8 | Wire.read();

    soma_mag += sqrt(pow((ax_raw / 8192.0) * 9.81, 2) + pow((ay_raw / 8192.0) * 9.81, 2) + pow((az_raw / 8192.0) * 9.81, 2));
    vTaskDelay(pdMS_TO_TICKS(10));
  }
  double magnitude_repouso = soma_mag / 1000.0;
  Serial.println("Core 1: Calibracao IMU concluida.");
  
  // Levanta a flag para libertar o arranque do GNSS no Core 0
  imu_calibrado = true;

  float b0 = 0.007820, b1 = 0.015640, b2 = 0.007820;
  float a1 = -1.734726, a2 = 0.766007;
  float x1 = 0, x2 = 0, y_1 = 0, y_2 = 0;
  int passos_temp = 0;
  float t_ultimo_passo = 0.0;
  int contador_janela = 0;
  float max_temp = -100.0, min_temp = 100.0, limiar_dinamico = 1.0, pico_a_pico = 0.0;

  TickType_t xLastWakeTime = xTaskGetTickCount();

  while(1) {
    float t = millis() / 1000.0;

    Wire.beginTransmission(MPU6050_ADDR);
    Wire.write(0x3B); Wire.endTransmission(false);
    Wire.requestFrom(MPU6050_ADDR, 6, true);
    
    int16_t ax_raw = Wire.read() << 8 | Wire.read();
    int16_t ay_raw = Wire.read() << 8 | Wire.read();
    int16_t az_raw = Wire.read() << 8 | Wire.read();

    double M = sqrt(pow((ax_raw / 2048.0) * 9.81, 2) + pow((ay_raw / 2048.0) * 9.81, 2) + pow((az_raw / 2048.0) * 9.81, 2));
    double M_dinamica = M - magnitude_repouso;
    float M_filtrada = b0 * M_dinamica + b1 * x1 + b2 * x2 - a1 * y_1 - a2 * y_2;

    if (M_filtrada > max_temp) max_temp = M_filtrada;
    if (M_filtrada < min_temp) min_temp = M_filtrada;

    if (++contador_janela >= 100) {
      limiar_dinamico = (max_temp + min_temp) / 2.0;
      pico_a_pico = max_temp - min_temp;
      max_temp = -100.0; min_temp = 100.0; contador_janela = 0;
    }

    if ((y_1 > M_filtrada) && (y_1 > y_2) && (y_1 > limiar_dinamico) && (pico_a_pico > 1.0)) {
      float delta_t = t - t_ultimo_passo;

      if ((delta_t > 0.30) && (delta_t < 1.50)) {
        passos_temp++;
        
        String estado_local = (delta_t < 0.45 || y_1 > 8.0) ? "CORRIDA" : "CAMINHADA";

        if (passos_temp >= 3) {
          int incremento = (passos_temp == 3) ? 3 : 1;
          
          float tamanho_passo = 0.0;
          if (estado_local == "CAMINHADA") {
            tamanho_passo = 0.72;
          } else if (estado_local == "CORRIDA") {
            tamanho_passo = 0.99;
          }

          if(xSemaphoreTake(mutex_dados, portMAX_DELAY)) {
            passos_desde_gnss += incremento;
            raio_imu_metros += (tamanho_passo * incremento);
            estado_atual = estado_local;
            xSemaphoreGive(mutex_dados);
          }
        }
      } else {
        passos_temp = 1; 
        
        if(xSemaphoreTake(mutex_dados, portMAX_DELAY)) {
          estado_atual = "PARADO";
          xSemaphoreGive(mutex_dados);
        }
      }
      t_ultimo_passo = t;
    }

    x2 = x1; x1 = M_dinamica; y_2 = y_1; y_1 = M_filtrada;

    vTaskDelayUntil(&xLastWakeTime, pdMS_TO_TICKS(10)); 
  }
}

// ==========================================
// TASK 2: GNSS E FILTRO (Corre no Core 0)
// ==========================================
void TaskGNSS(void *pvParameters) {
  // 1. O GNSS aguarda ativamente até o IMU terminar a calibração
  Serial.println("Core 0: A aguardar calibracao do IMU...");
  while (!imu_calibrado) {
    vTaskDelay(pdMS_TO_TICKS(100)); 
  }
  
  Serial.println("Core 0: A iniciar modulo GNSS...");
  while (!gnss.begin()) {
    vTaskDelay(pdMS_TO_TICKS(1000));
  }
  
  gnss.setGnss(eGPS_BeiDou_GLONASS);
  gnss.enablePower(); 

  uint8_t ultimo_segundo_processado = 99; 

  while(1) {
    uint8_t numSats = gnss.getNumSatUsed();
    sLonLat_t latInfo = gnss.getLat();
    sTim_t tempoGNSS = gnss.getUTC();

    if (numSats >= 4 && latInfo.latitude != 0.0 && tempoGNSS.second != ultimo_segundo_processado) {
      ultimo_segundo_processado = tempoGNSS.second;

      sLonLat_t lonInfo = gnss.getLon();
      
      double lat_decimal = latInfo.latitude;
      double lon_decimal = lonInfo.lonitude;
      
      char direcao_latitude = lonInfo.lonDirection; 
      char direcao_longitude = latInfo.latDirection; 
      
      if (direcao_latitude == 'S') lat_decimal *= -1.0;
      if (direcao_longitude == 'W') lon_decimal *= -1.0;

      // ==========================================
      int passos_locais = 0;
      double raio_local = 0.0;
      String estado_local = "";
      
      if(xSemaphoreTake(mutex_dados, portMAX_DELAY)) {
        passos_locais = passos_desde_gnss;
        raio_local = raio_imu_metros;
        estado_local = estado_atual;
        
        passos_desde_gnss = 0;
        raio_imu_metros = 0.0;
        xSemaphoreGive(mutex_dados);
      }
      // ==========================================

      Serial.println("----------------------------------------");
      
      // Nova linha formatada com a Hora UTC (HH:MM:SS)
      Serial.print("Hora da aquisicao (UTC): ");
      if (tempoGNSS.hour < 10) Serial.print("0");
      Serial.print(tempoGNSS.hour); Serial.print(":");
      if (tempoGNSS.minute < 10) Serial.print("0");
      Serial.print(tempoGNSS.minute); Serial.print(":");
      if (tempoGNSS.second < 10) Serial.print("0");
      Serial.println(tempoGNSS.second);

      // Nova linha com número de satélites
      Serial.print("Satelites visiveis/utilizados: ");
      Serial.println(numSats);

      Serial.print("Posicao GNSS atual: ");
      Serial.print(lat_decimal, 6); Serial.print(", "); Serial.println(lon_decimal, 6);
      Serial.print("Numero de passos dado desde ultima aquisicao: ");
      Serial.println(passos_locais);
      Serial.print("Estado cinematico: ");
      Serial.println(estado_local);

      if (!primeiro_fix_obtido) {
        lat_ancora = lat_decimal;
        lon_ancora = lon_decimal;
        primeiro_fix_obtido = true;
      } else {
        double lat_rad = lat_ancora * (PI / 180.0);
        double dx = (lon_decimal - lon_ancora) * cos(lat_rad) * 111320.0;
        double dy = (lat_decimal - lat_ancora) * 111000.0;
        double dist_gnss = sqrt(dx*dx + dy*dy);

        if (dist_gnss > 0.1) {
          double cx = (dx / dist_gnss) * raio_local;
          double cy = (dy / dist_gnss) * raio_local;
          double x_est = (cx + dx) / 2.0;
          double y_est = (cy + dy) / 2.0;

          lat_ancora = lat_ancora + (y_est / 111110.0);
          lon_ancora = lon_ancora + (x_est / (cos(lat_rad) * 111320.0));
        } else {
          lat_ancora = lat_decimal;
          lon_ancora = lon_decimal;
        }
      }
      
      Serial.print("Nova estimativa de posicao: ");
      Serial.print(lat_ancora, 6); Serial.print(", "); Serial.println(lon_ancora, 6);
      Serial.println("----------------------------------------\n");
    }

    vTaskDelay(pdMS_TO_TICKS(100)); 
  }
}

// ==========================================
// SETUP PRINCIPAL
// ==========================================
void setup() {
  Serial.begin(115200);
  
  mutex_dados = xSemaphoreCreateMutex();
  
  Wire.begin(18, 17); 
  Wire1.begin(8, 3);  

  xTaskCreatePinnedToCore(TaskIMU, "Task_IMU", 4096, NULL, 2, NULL, 1);
  xTaskCreatePinnedToCore(TaskGNSS, "Task_GNSS", 4096, NULL, 1, NULL, 0);

  vTaskDelete(NULL); 
}

void loop() {
}
