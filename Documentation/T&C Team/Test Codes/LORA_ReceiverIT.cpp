#include <Arduino.h>
#include <SPI.h>
#include <LoRa.h>

// --- PINOS SPARKFUN 1-CHANNEL LORA GATEWAY ---
#define SCK  14
#define MISO 12
#define MOSI 13
#define NSS_PIN 16 
#define RST_PIN 5     
#define DIO 26   
#define LED_PIN 17   

// --- CONFIGURAÇÃO LORA (IGUAL AO EMISSOR) ---
#define BAND              868E6
#define SPREADING_FACTOR  9        // Usando SF10 para o teste de penetração de paredes
#define SIGNAL_BANDWIDTH  250E3    // Usando 250kHz para evitar desvio de frequência
#define CODING_RATE_4     5      

// --- ESTRUTURA DE DADOS ---
struct __attribute__((packed)) ResQSensePacket {
  int8_t vestID;
  uint8_t spo2_header;
  uint16_t heartRate;
  int16_t temperature; 
  int32_t latitude;
  int32_t longitude;
  int16_t altitude;
  int16_t counter;
};

ResQSensePacket receivedData;
unsigned long lastPacketTime = 0;

// --- VARIÁVEIS DE INTERRUPÇÃO (VOLATILE) ---
volatile bool packetReceived = false;
volatile int rxPacketSize = 0;

String avaliarSinal(int rssi);
void printRadioInfo();
void printDataInfo(unsigned long timeDiff);

// --- FUNÇÃO DE INTERRUPÇÃO (ISR) ---
void IRAM_ATTR onReceiveCallback(int packetSize) {
  if (packetSize == 0) return;
  rxPacketSize = packetSize;
  packetReceived = true; 
}

void setup() {
  Serial.begin(115200);
  Serial.println("\n--- INICIANDO RECETOR (MODO INTERRUPÇÃO) ---");

  // --- CONFIGURAÇÃO DO LED ---
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW); // Começa desligado

  SPI.begin(SCK, MISO, MOSI, -1);
  LoRa.setSPI(SPI); 
  LoRa.setPins(NSS_PIN, RST_PIN, DIO);

  if (!LoRa.begin(BAND)) {
    Serial.println("ERRO: Falha no módulo LoRa!");
    while (1);
  }

  // Configurações exatas (Match with Emitter)
  LoRa.setSpreadingFactor(SPREADING_FACTOR);
  LoRa.setSignalBandwidth(SIGNAL_BANDWIDTH);
  LoRa.setCodingRate4(CODING_RATE_4);
  LoRa.enableCrc();
  LoRa.setSyncWord(0xA5); 

  // Configuração da Interrupção
  LoRa.onReceive(onReceiveCallback);
  LoRa.receive(); 

  Serial.println("Rádio Pronto. À escuta via DIO0...");
  Serial.println("----------------------------------------");
  
  lastPacketTime = millis();
}

void loop() {
  if (packetReceived) {
    // --- ALTERAR O ESTADO DO LED ---
    // Lê o estado atual do pino e inverte-o (!). 
    // Se estava ligado, desliga. Se estava desligado, liga.
    digitalWrite(LED_PIN, !digitalRead(LED_PIN)); 
    
    // 1. Calcular tempo
    unsigned long currentTime = millis();
    unsigned long timeDiff = currentTime - lastPacketTime;
    lastPacketTime = currentTime;

    // 2. Ler e validar os dados
    if (rxPacketSize == sizeof(ResQSensePacket)) {
      LoRa.readBytes((uint8_t *)&receivedData, rxPacketSize);
      printDataInfo(timeDiff);      
      printRadioInfo();             
    } else {
      Serial.print("ERRO: Tamanho de pacote inválido: ");
      Serial.println(rxPacketSize);
      while (LoRa.available()) LoRa.read(); 
    }

    packetReceived = false; 
  }
}

// --- FUNÇÕES AUXILIARES MANTÊM-SE IGUAIS ---
void printDataInfo(unsigned long timeDiff) {
  Serial.print("\nRX [");
  Serial.print(millis() / 1000); 
  Serial.println("s] -----------------------------");
  Serial.print("-> Intervalo:    "); Serial.print(timeDiff); Serial.print(" ms");
  if (timeDiff > 33000) Serial.print(" [ALERTA: Atraso!]");
  Serial.println();
  Serial.print("-> Temperatura:  "); Serial.print(receivedData.temperature); Serial.println(" °C");
  Serial.print("-> VestID:      "); Serial.println(receivedData.vestID);
  Serial.print("-> SPO2:       ");  Serial.print(receivedData.spo2_header); Serial.println(" %");
  Serial.print("-> HR:        "); Serial.println(receivedData.heartRate);
  Serial.print("-> LAT:        "); Serial.println(receivedData.latitude);
  Serial.print("-> LON:        "); Serial.println(receivedData.longitude);
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
  if (rssi > -90)  return "EXCELENTE";
  if (rssi > -105) return "BOM";
  if (rssi > -120) return "FRACO";
  return "CRÍTICO";
}