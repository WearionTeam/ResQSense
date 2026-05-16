#ifndef SENSORS_H
#define SENSORS_H

#include "config.h"
#include "DFRobot_GNSSAndRTC.h"
#include "DFRobot_GNSS.h"
#include <Adafruit_TMP117.h>
#include <Adafruit_Sensor.h>
#include <DFRobot_BloodOxygen_S.h>
#include <math.h>
#include <Wire.h>
#include <Arduino.h>

/**
 * @brief Structure com todos as medidas necessárias
 * 
 */
struct sensors_data
{
    float ax, ay, az;
    float gx, gy, gz;
    float mag_a;
    sLonLat_t coordinates;
    float temp;
    int hr;
    int spo2;
    bool fall_detected;
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

void readIMU(sensors_data &component);
bool mandown(sensors_data &componenet);


#endif
