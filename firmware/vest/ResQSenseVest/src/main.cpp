#include <Arduino.h>
#include "protocol.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "sensors.h"

const char* TAG = "TASK";

sensors_data measurements;
DFRobot_GNSSAndRTC_I2C gnss(&Wire,GNSS_ADDR);

TaskHandle_t ReadIMUHandle = NULL;
TaskHandle_t ReadGNSSHandle = NULL;
SemaphoreHandle_t data_mutex;
SemaphoreHandle_t i2c_mutex;

void TaskReadIMU(void *pvParameters);
void TaskReadGNSS(void *pvParameters);


void setup() {

  Serial.begin(115200);
  delay(1000);
  
  Wire.begin(I2C_SDA,I2C_SCL);

  startIMU();
  startGNSS(gnss);

}

void loop() {

}
