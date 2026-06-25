#include <Arduino.h>
#include "protocol.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "sensors.h"
#include "config.h"
#include "network.h"

const char* TASK_TAG = "TASK";
const char* QUEUE_TAG = "QUEUE";
const char* INFO_TAG = "INFO";

DFRobot_GNSSAndRTC_I2C gnss(&Wire,GNSS_ADDR);
DFRobot_BloodOxygen_S_I2C MAX30102(&Wire, MAX_ADDR);

TaskHandle_t ReadSensorsHandle = NULL;
TaskHandle_t ProcessDataHandle = NULL;

QueueHandle_t raw_data;

void TaskReadSensors(void *pvParameters);
void TaskProcessData(void *pvParameters);

void setup() {
  Serial.begin(115200);
  delay(5000);
  ESP_LOGI(INFO_TAG,"Serial monitor initialized");
  
  Wire.begin(I2C_SDA,I2C_SCL);
  ESP_LOGI(INFO_TAG,"I2C Busc initialized");

  raw_data = xQueueCreate(50,sizeof(sensors_data));
  ESP_LOGI(INFO_TAG,"Necessary queues initialized");

  startIMU();
  ESP_LOGI(INFO_TAG,"Starting IMU");
  startGNSS(gnss);
  ESP_LOGI(INFO_TAG,"Starting GNSS");

  initVitalsHardware(MAX30102);
  ESP_LOGI(INFO_TAG,"Starting Vital sensors");

  if (startNetwork()) {
    ESP_LOGI(INFO_TAG, "Rede inicializada!");
    extern TaskHandle_t LoRaNetworkHandle; 
    xTaskCreatePinnedToCore(
      TaskLoRaNetwork,
      "TaskLoRaNetwork",
      8192,
      NULL,
      3,
      &LoRaNetworkHandle, 
      1
    );
  } else {
      ESP_LOGE(INFO_TAG, "Falha fatal a iniciar LoRa!");
  }

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
  xTaskNotifyGive(ReadSensorsHandle);
  xTaskNotifyGive(ProcessDataHandle);

  ESP_LOGI(INFO_TAG,"Tasks created and starting the code");
}

void loop() {
  vTaskDelete(NULL);
}

void TaskReadSensors(void *pvParameters) {
  sensors_data measurements;
  ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

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
      
      readPPG(MAX30102, measurements);
      measurements.ppg_updated = true;
    }

    if (loop_counter % 500 == 0) {
      readTemperatureSensors(measurements.raw_temps, measurements.validMask, current_time);
      measurements.temp_updated = true;
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

  ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

  uint32_t last_telemetry_time = 0;
  
  // Variáveis para evitar Spam (Deteção de Flanco)
  bool last_ppg_alert_state = false;
  bool last_temp_alert_state = false;
  bool last_fall_alert_state = false;
  bool last_sos_alert_state = false;

  for (;;) {
    if(xQueueReceive(raw_data, &measurements, portMAX_DELAY)) {
      
      info.timestamp_ms = measurements.timestamp_ms;

      processSensorFusion(measurements, info);
      checkSOS(info);

      if (info.sos_alert && !last_sos_alert_state) 
      {
        queueAlert(OP_QALERT,funct_qa_sos,0);
        ESP_LOGW(TASK_TAG,"Alerta SOS");
        
      }
      last_sos_alert_state = info.sos_alert;

      if (info.fall_detected && !last_fall_alert_state)
      {
        queueAlert(OP_QALERT,funct_qa_md,0);
        ESP_LOGW(TASK_TAG,"Alerta Man Down");
      }
      last_fall_alert_state = info.fall_detected;
      
      
      if (measurements.ppg_updated) {
        processPPG(measurements, info);
        
        // Só dispara o alerta se houver MUDANÇA de estado (Normal -> Alerta)
        if (info.ppg_alert && !last_ppg_alert_state) {
            queueAlert(OP_VTALERT, funct_va_hbpm, info.hr);
            ESP_LOGW(TASK_TAG, "Alerta de BPM emitido!");
        }
        last_ppg_alert_state = info.ppg_alert; // Atualiza a memória
      }

      if (measurements.temp_updated) {
        processTemperatureData(measurements.raw_temps, measurements.validMask, measurements.timestamp_ms, info);
        
        // Só dispara o alerta se houver MUDANÇA de estado
        if (info.temp_alert && !last_temp_alert_state) {
            queueAlert(OP_VTALERT, funct_va_htemp, (uint16_t)(info.temp * 100.0f));
            ESP_LOGW(TASK_TAG, "Alerta de Temperatura emitido!");
        }
        last_temp_alert_state = info.temp_alert; // Atualiza a memória
      }

      bool is_time_to_send = (measurements.timestamp_ms - last_telemetry_time) >= TDMA_CYCLE_MS;
      
      if (is_time_to_send) {
        queueTelemetryPacket(info); 
        ESP_LOGI(TASK_TAG,"Telemetria enviada para a queue do módulo de rede.");
        
        last_telemetry_time = measurements.timestamp_ms;
        resetNavigationState();
        info.fall_detected = false; 
      }
    }
  }
}