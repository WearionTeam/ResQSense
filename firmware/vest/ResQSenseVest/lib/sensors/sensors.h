#ifndef SENSORS_H
#define SENSORS_H

#include <Arduino.h>
#include <Wire.h>
#include <math.h>

#include "config.h"
#include "DFRobot_GNSSAndRTC.h"
#include "DFRobot_GNSS.h"
#include <Adafruit_TMP117.h>
#include <Adafruit_Sensor.h>
#include <DFRobot_BloodOxygen_S.h>



typedef enum {
    PDR_STATIC = 0,
    PDR_RUN,
    PDR_WALK
} pdr_state_t;
/**
 * @brief Structure com todos as medidas dos sensores
 * 
 */
typedef struct 
{
    uint32_t timestamp_ms;
    bool gnss_updated;
    bool ppg_updated;
    bool temp_updated;
    float ax, ay, az;
    float gx, gy, gz;
    double lat, lon, alt;
    int hr, spo2;
    float raw_temps[3];
    bool validMask[3];
}sensors_data;
/**
 * @brief Structure com os dados processados e necessários para os pacotes
 * 
 */
typedef struct
{
    uint32_t timestamp_ms;
    float mag_a;
    double lat, lon, alt;
    float temp;
    int hr, spo2;
    bool fall_detected;
    bool temp_alert;
    bool ppg_alert;
} processed_data;
/**
 * @brief 
 * 
 */
struct SensorNode {
  Adafruit_TMP117* dev;
  uint8_t addr;
  bool ok;
  float lastTemp;
};

/**
 * @brief Configura os registos do MPU-6050
 * 
 */
void startIMU();
/**
 * @brief Inicializar o módulo GNSS
 * 
 * @param component Objeto da classe DFRobot GNSS
 */
void startGNSS(DFRobot_GNSSAndRTC_I2C &component);
/**
 * @brief Ler os dados do IMU por polling
 * 
 * @param component Objeto da classe sensors
 */
void readIMU(sensors_data &component);
/**
 * @brief Função para detetar a queda do operador
 * 
 * @param componenet Objeto da classe sensors
 */
void mandown(processed_data &component,float az,float ax);
/**
 * @brief 
 * 
 * @param component 
 * @param gps 
 */
void readGNSS(sensors_data &component, DFRobot_GNSSAndRTC_I2C &gps);
/**
 * @brief 
 * 
 */
void pdr(float magnitude);

void processSensorFusion(sensors_data &measurements, processed_data &info);

static bool writeReg16(uint8_t a, uint8_t reg, uint16_t v);
static bool readReg16(uint8_t a, uint8_t reg, uint16_t &v);

bool initTMP117(SensorNode &s);
void kickAllSensors();

void initVitalsHardware(DFRobot_BloodOxygen_S_I2C &sen);
void readTemperatureSensors(float temps[3], bool validMask[3], unsigned long now);
void processTemperatureData(float temps[3], bool validMask[3], unsigned long now, processed_data &info);
void readPPG(DFRobot_BloodOxygen_S_I2C &sen, sensors_data &component);
void processPPG(sensors_data &component, processed_data &info);
void ignoreSensor(int idx, unsigned long now); 
void resetNavigationState();



#endif
