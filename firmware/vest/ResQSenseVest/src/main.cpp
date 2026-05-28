#include <Arduino.h>
#include "protocol.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "sensors.h"
#include "config.h"

const char* TASK_TAG = "TASK";
const char* QUEUE_TAG = "QUEUE";
const char* INFO_TAG = "INFO";


uint32_t last_gnss_read = 0;
uint32_t last_temp_read = 0;

DFRobot_GNSSAndRTC_I2C gnss(&Wire,GNSS_ADDR);
DFRobot_BloodOxygen_S_I2C MAX30102(&Wire, MAX_ADDR);

TaskHandle_t ReadSensorsHandle = NULL;
TaskHandle_t ProcessDataHandle = NULL;
TaskHandle_t SpendQueue = NULL;

volatile unsigned long last_telemetry_time = 0;

/**
 * @brief Queue para guardar todos os valores "raw" dos sensores
 * 
 */
QueueHandle_t raw_data;
/**
 * @brief Queue que guarda os valores necessários para criar um pacote
 * 
 */
QueueHandle_t packet_data;

void TaskReadSensors(void *pvParameters);
void TaskProcessData(void *pvParameters);
void TaskSpendQueue(void *pvParameters);

void setup() {

  Serial.begin(115200);
  
  delay(5000);
  ESP_LOGI(INFO_TAG,"Serial monitor initialized");
  
  
  Wire.begin(I2C_SDA,I2C_SCL);
  ESP_LOGI(INFO_TAG,"I2C Busc initialized");

  raw_data=xQueueCreate(50,sizeof(sensors_data));
  packet_data=xQueueCreate(10,sizeof(processed_data));
  ESP_LOGI(INFO_TAG,"Necessary queues initialized");

  startIMU();
  ESP_LOGI(INFO_TAG,"Starting IMU");
  startGNSS(gnss);
  ESP_LOGI(INFO_TAG,"Starting GNSS");

  initVitalsHardware(MAX30102);
  ESP_LOGI(INFO_TAG,"Starting Vital sensors");
    
  xTaskCreatePinnedToCore(
    TaskReadSensors,
    "TaskReadSensors",
    10000,
    NULL,
    5,
    &ReadSensorsHandle,
    0
  );

  xTaskCreatePinnedToCore(
    TaskProcessData,
    "TaskProcessData",
    10000,
    NULL,
    4,
    &ProcessDataHandle,
    0
  );

  xTaskCreatePinnedToCore(
    TaskSpendQueue,
    "TaskSpendQueue",
    4096, 
    NULL,
    3,    
    &SpendQueue,
    1     
  );

  ESP_LOGI(INFO_TAG,"Tasks created and starting the code");
}

void loop() {

}


void TaskReadSensors(void *pvParameters) {
  sensors_data measurements;
  TickType_t current_tick = xTaskGetTickCount();
  const TickType_t sample_time = pdMS_TO_TICKS(20);
  uint32_t loop_counter = 0;

  for(;;) {
    uint32_t current_time = current_tick * portTICK_PERIOD_MS;
    
    measurements.timestamp_ms = current_time;
    measurements.gnss_updated = false;
    measurements.ppg_updated = false;
    measurements.temp_updated = false;
    
    readIMU(measurements);
    
    if (loop_counter % 50 == 0) {
      readGNSS(measurements, gnss);
      measurements.gnss_updated = true;
      ESP_LOGI(TASK_TAG,"Acabou de ler GNSS");
      
      readPPG(MAX30102, measurements);
      measurements.ppg_updated = true;
      ESP_LOGI(TASK_TAG,"Acabou de ler o MAX3012");
    }

    if (loop_counter % 500 == 0) {
      readTemperatureSensors(measurements.raw_temps, measurements.validMask, current_time);
      measurements.temp_updated = true;
      ESP_LOGI(TASK_TAG,"Acabou de ler os sensores de Tempertura");

      kickAllSensors();
    }

    if (xQueueSend(raw_data, &measurements, 0) != pdTRUE) {
      ESP_LOGE(QUEUE_TAG, "Queue RAW cheia! Perda de dados.");
    }
    
    loop_counter++;
    vTaskDelayUntil(&current_tick, sample_time);
  }
}

void TaskProcessData(void *pvParameters) {
  sensors_data measurements;
  
  processed_data info; 
  memset(&info, 0, sizeof(processed_data)); 

  uint32_t last_telemetry_time = 0;

  for (;;) {
    if(xQueueReceive(raw_data, &measurements, portMAX_DELAY)) {
      
      info.timestamp_ms = measurements.timestamp_ms;

      processSensorFusion(measurements, info);
      
      if (measurements.ppg_updated) {
        processPPG(measurements, info);
        ESP_LOGI(TASK_TAG,"Processou os dados de ppg");
      }

      if (measurements.temp_updated) {
        processTemperatureData(measurements.raw_temps, measurements.validMask, measurements.timestamp_ms, info);
        ESP_LOGI(TASK_TAG,"Processou os dados de Temperatura");
      }

      bool is_time_to_send = (measurements.timestamp_ms - last_telemetry_time) >= TDMA_CYCLE_MS;
      
      if (is_time_to_send) {
        xQueueSend(packet_data, &info, 0);
        ESP_LOGI(TASK_TAG,"Mandou os dados necessários para a queue de dados");
        
        last_telemetry_time = measurements.timestamp_ms;
        
        resetNavigationState();
        info.fall_detected = false; 
      }
    }
  }
}

void TaskSpendQueue(void *pvParameters)
{
  processed_data final_packet;

  ESP_LOGI("NETWORK", "Task de Rede simulada iniciada. A aguardar pacotes...");

  for (;;)
  {
    // Bloqueia até que a TaskProcessData envie um pacote para a packet_data
    if (xQueueReceive(packet_data, &final_packet, portMAX_DELAY))
    {
      //ESP_LOGI("====================================");
      ESP_LOGI("NETWORK","Timestamp: %lu ms\n", final_packet.timestamp_ms);
      ESP_LOGI("NETWORK","Estimativa Lat: %.6f\n", final_packet.lat);
      ESP_LOGI("NETWORK","Estimativa Lon: %.6f\n", final_packet.lon);
      ESP_LOGI("NETWORK","Mag Aceleração: %.2f\n", final_packet.mag_a);
      ESP_LOGI("NETWORK","Temp: %.2f ºC | Temp Alert: %d\n", final_packet.temp, final_packet.temp_alert);
      ESP_LOGI("NETWORK","HR: %d bpm | SpO2: %d %% | PPG Alert: %d\n", final_packet.hr, final_packet.spo2, final_packet.ppg_alert);
      //ESP_LOGI("NETWORK",("Queda Detetada: %s\n", final_packet.fall_detected);
      //ESP_LOGI("====================================");
    }
    //vTaskDelay(pdMS_TO_TICKS(TDMA_CYCLE_MS));
  }
}
