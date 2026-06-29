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

// --- CONFIGURAÇÃO BASE LORA ---
#define BAND              868E6
#define SIGNAL_BANDWIDTH  125E3   
#define CODING_RATE_4     5      

// --- ARRAYS DE CONFIGURAÇÃO ---
// Índices:             0, 1, 2,  3,  4,  5
const int sfArray[] = {7, 8, 9, 10, 11, 12};

// Tempos de espera obrigatórios (1% Duty Cycle) aproximados para um payload de 2 bytes:
const unsigned long delayArray[] = {3069, 5148, 10197, 20592, 40986, 81972};

// ---> MUDAR ESTE PARAMETRO PARA MUDAR O SF (IGUAL AO RECETOR) <---
int sfIndex = 5; 

// --- ESTRUTURA DE DADOS MINIMALISTA ---
struct __attribute__((packed)) ResQSensePacket {
  int16_t counter;
};

ResQSensePacket txData;

void setup() {
  Serial.begin(115200);
  delay(3000);
  Serial.println("\n--- INICIANDO EMISSOR (TESTE DE ALCANCE) ---");

  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  SPI.begin(SCK, MISO, MOSI, -1);
  LoRa.setSPI(SPI); 
  LoRa.setPins(NSS_PIN, RST_PIN, DIO);

  if (!LoRa.begin(BAND)) {
    Serial.println("ERRO: Falha no módulo LoRa!");
    while (1);
  }

  // Configurações iguais às do recetor
  LoRa.setSpreadingFactor(sfArray[sfIndex]);
  LoRa.setSignalBandwidth(SIGNAL_BANDWIDTH);
  LoRa.setCodingRate4(CODING_RATE_4);
  LoRa.enableCrc();
  LoRa.setSyncWord(0xA5); 

  txData.counter = 0; // Inicializa o contador

  Serial.print("Emissor Pronto. A transmitir no SF: ");
  Serial.println(sfArray[sfIndex]);
  Serial.print("Delay estático entre pacotes: ");
  Serial.print(delayArray[sfIndex] / 1000);
  Serial.println(" segundos.");
  Serial.println("----------------------------------------");
}

void loop() {
  // Verifica se ainda não enviámos os 10 pacotes
  if (txData.counter < 10) {
    txData.counter++; // Incrementa para 1, 2, 3...
    
    // Ligar LED para indicar início do processo de envio
    digitalWrite(LED_PIN, HIGH);

    // --- INÍCIO DA MEDIÇÃO DE TEMPO (ToA) ---
    unsigned long startTime = millis();

    // Enviar dados
    LoRa.beginPacket();
    LoRa.write((uint8_t *)&txData, sizeof(ResQSensePacket));
    LoRa.endPacket(); // O código "bloqueia" aqui até o rádio acabar de emitir

    // --- FIM DA MEDIÇÃO DE TEMPO ---
    unsigned long toa = millis() - startTime;

    // Desligar LED após envio concluído
    digitalWrite(LED_PIN, LOW);
    
    // Print do resultado
    Serial.print("Pacote nº ");
    Serial.print(txData.counter);
    Serial.print(" enviado! ToA aprox: ");
    Serial.print(toa);
    Serial.println(" ms");

    // Se for o último pacote, não precisa do delay total de espera
    if (txData.counter < 10) {
      delay(delayArray[sfIndex]);
    }
    
  } else {
    // --- FIM DO TESTE DESTE SF ---
    Serial.println("\n=======================================================");
    Serial.println(" TESTE CONCLUÍDO: 10 PACOTES ENVIADOS!");
    Serial.println(" Por favor, liga o módulo ao PC, altera a variável");
    Serial.println(" 'sfIndex' para o próximo valor, e faz o upload.");
    Serial.println("=======================================================\n");
    
    // Piscar o LED repetidamente para avisar quem está no terreno que acabou
    while(1) {
      digitalWrite(LED_PIN, HIGH);
      delay(500);
      digitalWrite(LED_PIN, LOW);
      delay(500);
    }
  }
}