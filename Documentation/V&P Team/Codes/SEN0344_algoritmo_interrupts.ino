#include "DFRobot_BloodOxygen_S.h"

#define I2C_COMMUNICATION  //use I2C for communication, but use the serial port for communication if the line of codes were masked

#ifdef  I2C_COMMUNICATION
#define I2C_ADDRESS    0x57
  DFRobot_BloodOxygen_S_I2C MAX30102(&Wire ,I2C_ADDRESS);
#else
#if defined(ARDUINO_AVR_UNO) || defined(ESP8266)
SoftwareSerial mySerial(4, 5);
DFRobot_BloodOxygen_S_SoftWareUart MAX30102(&mySerial, 9600);
#else
DFRobot_BloodOxygen_S_HardWareUart MAX30102(&Serial1, 9600); 
#endif
#endif

#define INT_PIN 18     
#define I2C_SDA 21
#define I2C_SCL 22
#define SPO2_min 95
#define HR_min 44
#define HR_max 187
#define fim 10

// incializar variaveis e flags
int total_counter = 0;
int HR_counter = 0;
int HR_total = 0;
int HR_mean = 0;
int SPO2_counter = 0;
int SPO2_total = 0;
int SPO2_mean = 0;
bool HR_flag_min = false;
bool HR_flag_max = false;
bool SPO2_flag_min = false;

volatile bool sampleReady = false;   // set by ISR when INT goes low
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED; // ESP32 safe (optional)

// ISR: keep it tiny!
void IRAM_ATTR sensorISR() {
  // On ESP32: mark flag in a safe way
  portENTER_CRITICAL_ISR(&mux);
  sampleReady = true;
  portEXIT_CRITICAL_ISR(&mux);
}

void setup() {
  Wire.begin(I2C_SDA, I2C_SCL);
  Serial.begin(9600);
  delay(1000);

  while (false == MAX30102.begin())
  {
    // Adicionado prefixo ERR, para o MATLAB ignorar esta linha nos dados
    Serial.println("ERR, init fail!");
    delay(1000);
  }
  // Adicionados prefixos INIT, e START, para aparecerem na consola do MATLAB
  Serial.println("INIT, success!");
  Serial.println("START, measuring...");  

   // Start internal data collection on module
  MAX30102.sensorStartCollect();

  // Configure INT pin: module INT is open-drain active-low
  pinMode(INT_PIN, INPUT_PULLUP);

  // Attach interrupt on FALLING edge (active-low pulse)
  attachInterrupt(digitalPinToInterrupt(INT_PIN), sensorISR, FALLING);
}

void loop()
{ 
 
  // If no interrupt has fired, go idle (or do other tasks)
  if (!sampleReady) {
  delay(10);
  return;
}
  // Clear the flag quickly and read the data (do heavy work outside ISR)
  portENTER_CRITICAL(&mux);
  sampleReady = false;
  portEXIT_CRITICAL(&mux);

  MAX30102.getHeartbeatSPO2(); // retira valores
  int spo2 = MAX30102._sHeartbeatSPO2.SPO2; // spo2
  int hr = MAX30102._sHeartbeatSPO2.Heartbeat; // hr
  total_counter++;

  if (spo2 > 50 && spo2 < 101){
    SPO2_counter++;
    SPO2_total =  SPO2_total + spo2;
  }

  if (hr > 5 && hr < 220){
    HR_counter++;
    HR_total =  HR_total + hr;
  }

  if(total_counter == fim){
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
    // envio desses valores
    Serial.print("SPO2_mean:");
    Serial.println(SPO2_mean);
    Serial.print("HR_mean:");
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
      HR_flag_max = false;
    }
    // envio das flags
    Serial.print("SPO2_flag_min: ");
    Serial.println(SPO2_flag_min);

    Serial.print("HR_flag_min:");
    Serial.println(HR_flag_min);
    
    Serial.print("HR_flag_max:");
    Serial.println(HR_flag_max);

    Serial.println(" ");
    
    // meter tds as variaveis a 0 
    total_counter = 0;
    HR_counter = 0;
    HR_total = 0;
    HR_mean = 0;
    SPO2_counter = 0;
    SPO2_total = 0;
    SPO2_mean = 0;
    
    //reiniciar o ciclo
    delay(1000);
    return;

  }else{
    delay(1000);
    return;
  }
}