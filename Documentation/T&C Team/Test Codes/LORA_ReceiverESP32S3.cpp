#include <Arduino.h>
#include <SPI.h>
#include <LoRa.h>

// PINS ESP32-S3
#define MOSI 11
#define MISO 12
#define SCK 13 
#define NSS_PIN   10 
#define RST_PIN   14    
#define DIO 21
#define LED_PIN 2

// LoRa Configuration
#define BAND              868E6
#define SPREADING_FACTOR  9     
#define SIGNAL_BANDWIDTH  250E3  
#define CODING_RATE_4     5      

// ResQSense data packet
struct __attribute__((packed)) ResQSensePacket {
  int8_t vestID;
  uint8_t spo2_header;
  uint16_t heartRate;
  int16_t temperature; 
  int32_t latitude;
  int32_t longitude;
  int16_t altitude;
  int16_t counter; // MAKE SURE THE EMITTER HAS THIS TOO!
};

ResQSensePacket receivedData;
unsigned long lastPacketTime = 0; 

// --- FIXED: Added missing volatile variables ---
volatile bool packetReceived = false;
volatile int rxPacketSize = 0;

// --- FIXED: Added missing function prototype ---
String avaliarSinal(int rssi);
void printRadioInfo();
void printDataInfo(unsigned long timeDiff);

void IRAM_ATTR onReceiveCallback(int packetSize) {
  if (packetSize == 0) return;
  rxPacketSize = packetSize;
  packetReceived = true; 
}

void setup() {
  Serial.begin(115200);
  Serial.println("\n--- INICIANDO RECETOR (MODO INTERRUPÇÃO) ---");

  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW); 

  SPI.begin(SCK, MISO, MOSI, -1);
  LoRa.setSPI(SPI); 
  LoRa.setPins(NSS_PIN, RST_PIN, DIO);

  if (!LoRa.begin(BAND)) {
    Serial.println("ERRO: Falha no módulo LoRa!");
    while (1);
  }

  LoRa.setSpreadingFactor(SPREADING_FACTOR);
  LoRa.setSignalBandwidth(SIGNAL_BANDWIDTH);
  LoRa.setCodingRate4(CODING_RATE_4);
  LoRa.enableCrc();
  LoRa.setSyncWord(0xA5); 

  LoRa.onReceive(onReceiveCallback);
  LoRa.receive(); 

  Serial.println("Rádio Pronto. À escuta via DIO0...");
  Serial.println("----------------------------------------");
  
  lastPacketTime = millis();
}

void loop() {
  // --- FIXED: Removed LoRa.parsePacket(). Now we only check the interrupt flag ---
  if (packetReceived) {
    
    unsigned long currentTime = millis();
    unsigned long timeDiff = currentTime - lastPacketTime;
    lastPacketTime = currentTime; 
    
    digitalWrite(LED_PIN, !digitalRead(LED_PIN)); 
    
    // Ler os dados usando o tamanho capturado pela interrupção (rxPacketSize)
    if (rxPacketSize == sizeof(ResQSensePacket)) {
      LoRa.readBytes((uint8_t *)&receivedData, rxPacketSize);
      printDataInfo(timeDiff);      
      printRadioInfo();             
    } else {
      Serial.print("ERRO: Tamanho de pacote inválido: ");
      Serial.print(rxPacketSize);
      Serial.print(" bytes (Esperado: ");
      Serial.print(sizeof(ResQSensePacket));
      Serial.println(" bytes)");
      while (LoRa.available()) LoRa.read(); // Limpar buffer
    }

    // --- FIXED: Reset the flag so we can wait for the next packet ---
    packetReceived = false;
  }
}

void printDataInfo(unsigned long timeDiff) {
  Serial.print("RX [");
  Serial.print(millis() / 1000); 
  Serial.println("s] -----------------------------");
  
  Serial.print("-> Intervalo:    "); 
  Serial.print(timeDiff); 
  Serial.print(" ms");
  
  if (timeDiff > 11000) Serial.print(" [ALERTA: Atraso/Perda detetada!]");
  else if (timeDiff < 9000 && timeDiff > 0) Serial.print(" [Rápido]");
  else Serial.print(" [Normal]");
  Serial.println();
}

void printRadioInfo() {
  int sf = SPREADING_FACTOR; 
  int rssi = LoRa.packetRssi();
  float snr = LoRa.packetSnr();

  Serial.println("--- DIAGNÓSTICO DE SINAL ---");
  Serial.print("SF: "); Serial.print(sf);
  Serial.print(" | RSSI: "); Serial.print(rssi); Serial.print(" dBm ("); Serial.print(avaliarSinal(rssi)); Serial.print(")");
  Serial.print(" | SNR: "); Serial.print(snr); Serial.println(" dB");
  Serial.print("Pacote nº "); Serial.println(receivedData.counter);
  Serial.println("========================================");
}

String avaliarSinal(int rssi) {
  if (rssi > -90)  return "EXCELENTE (Perto)";
  if (rssi > -105) return "BOM (Estável)";
  if (rssi > -120) return "FRACO (Limite da ligação)";
  return "CRÍTICO (Perda iminente)";
}