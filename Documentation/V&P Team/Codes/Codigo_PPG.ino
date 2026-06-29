#include "DFRobot_BloodOxygen_S.h"

#define I2C_ADDRESS 0x57
DFRobot_BloodOxygen_S_I2C MAX30102(&Wire ,I2C_ADDRESS);

#define I2C_SDA 21
#define I2C_SCL 22
#define INT_PIN 27 

// --- Variáveis de Controle do Fluxograma ---
int total_counter = 0;
int HR_counter = 0;
int SPO2_counter = 0;
long HR_total = 0;
long SPO2_total = 0;

// Limites de Alerta (Ajuste conforme necessário)
const int SPO2_MIN = 92;
const int HR_MAX = 100;
const int HR_MIN = 60;

volatile bool interruptFired = false;
unsigned long lastReadTime = 0;

void IRAM_ATTR sensorISR() {
  interruptFired = true;
}

void setup() {
  Wire.begin(I2C_SDA, I2C_SCL);
  Serial.begin(115200); 
  
  pinMode(INT_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(INT_PIN), sensorISR, FALLING);

  while (false == MAX30102.begin()) {
    Serial.println("ERR, init fail!");
    delay(1000);
  }

  MAX30102.sensorStartCollect();
  Serial.println("INIT, success!");
}

void loop() {
  if (interruptFired || (millis() - lastReadTime > 200)) {
    if (interruptFired) interruptFired = false; 

    MAX30102.getHeartbeatSPO2();
    lastReadTime = millis();
    
    int spo2 = MAX30102._sHeartbeatSPO2.SPO2;
    int hr = MAX30102._sHeartbeatSPO2.Heartbeat;
    
    // Conforme o fluxograma: ignora se os valores forem <= 0 ou -1
    if (spo2 > 0) { 
      // 1. Envio de dados brutos (MATLAB)
      Serial.print("DATA,");
      Serial.print(millis());
      Serial.print(",");
      Serial.print(spo2);
      Serial.print(",");
      Serial.println(hr);

      // 2. Lógica de Acumulação do Fluxograma
      total_counter++;

      // Decisão HR == -1?
      if (hr != -1) {
        HR_counter++;
        HR_total += hr;
      }

      // Decisão SPO2 == -1?
      if (spo2 != -1) {
        SPO2_counter++;
        SPO2_total += spo2;
      }

      // 3. Verificação: total_counter == 60?
      if (total_counter >= 60) {
        // Cálculo das Médias
        float HR_mean = (HR_counter > 0) ? (float)HR_total / HR_counter : 0;
        float SPO2_mean = (SPO2_counter > 0) ? (float)SPO2_total / SPO2_counter : 0;

        // Envio das Médias
        Serial.print("MEANS,");
        Serial.print(SPO2_mean);
        Serial.print(",");
        Serial.println(HR_mean);

        // 4. Blocos de Decisão de Alerta
        if (SPO2_mean < SPO2_MIN) {
          Serial.println("ALERT,SPO2_min");
        }
        
        if (HR_mean > HR_MAX) {
          Serial.println("ALERT,HR_max");
        } else if (HR_mean < HR_MIN) {
          Serial.println("ALERT,HR_min");
        }

        // 5. Reset de todas as variáveis e contadores
        total_counter = 0;
        HR_counter = 0;
        SPO2_counter = 0;
        HR_total = 0;
        SPO2_total = 0;
      }
    }
  }
}