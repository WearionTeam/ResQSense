#include <Arduino.h>
#include "config.h"
#include "protocol.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include <math.h>
#include <Wire.h>

TaskHandle_t ReadIMUHandle = NULL;

void ReadIMU(void *parameter) {
  for(;;){
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
  Serial.println(mag_a);
  vTaskDelay(pdMS_TO_TICKS(10));
  }
};

void setup() {
  Serial.begin(115200);

  Wire.begin(I2C_SDA,I2C_SCL);

  Wire.beginTransmission(IMU_ADDR);
  Wire.write(0x6B); 
  Wire.write(0);

  xTaskCreatePinnedToCore(
    ReadIMU,
    "ReadIMU",
    3072,     
    NULL,
    1,
    &ReadIMUHandle,
    1
  );
}

void loop() {

}
