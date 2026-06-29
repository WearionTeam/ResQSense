#include <SPI.h>
#include <LoRa.h>
#include <mbedtls/aes.h>

static const long LORA_FREQ_HZ = 868500000;  // Igual ao canal de controlo default do tablet

static const int PIN_SCK = 12;
static const int PIN_MISO = 13;
static const int PIN_MOSI = 11;
static const int PIN_CS = 10;
static const int PIN_RST = 9;
static const int PIN_DIO0 = 14;

static const uint8_t AES_KEY[16] = {
  0x2B, 0x7E, 0x15, 0x16, 0x28, 0xAE, 0xD2, 0xA6,
  0xAB, 0xF7, 0x15, 0x88, 0x09, 0xCF, 0x4F, 0x3C,
};

static const uint8_t FIXED_IV[16] = {
  0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
  0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F,
};

static const uint8_t NET_ID = 0b001;
static const uint8_t VEST_ID = 3;

static void writeI32BE(uint8_t* out, int32_t value) {
  out[0] = (uint8_t)((value >> 24) & 0xFF);
  out[1] = (uint8_t)((value >> 16) & 0xFF);
  out[2] = (uint8_t)((value >> 8) & 0xFF);
  out[3] = (uint8_t)(value & 0xFF);
}

static void writeI16BE(uint8_t* out, int16_t value) {
  out[0] = (uint8_t)((value >> 8) & 0xFF);
  out[1] = (uint8_t)(value & 0xFF);
}

static void encryptAesCtr(uint8_t* data, size_t len) {
  mbedtls_aes_context ctx;
  mbedtls_aes_init(&ctx);
  mbedtls_aes_setkey_enc(&ctx, AES_KEY, 128);

  uint8_t nonceCounter[16];
  uint8_t streamBlock[16];
  size_t ncOff = 0;

  memcpy(nonceCounter, FIXED_IV, sizeof(nonceCounter));
  memset(streamBlock, 0, sizeof(streamBlock));

  mbedtls_aes_crypt_ctr(&ctx, len, &ncOff, nonceCounter, streamBlock, data, data);
  mbedtls_aes_free(&ctx);
}

static void buildFullPacket(uint8_t* packet15) {
  // Byte 0: net_id (3 bits) + vest_id (5 bits)
  packet15[0] = (uint8_t)(((NET_ID & 0x07) << 5) | (VEST_ID & 0x1F));

  // Byte 1: flags. Bit 7 tem de ser 1 para o tablet reconhecer como trama completa.
  // type_full=1, fix=1, resto a 0
  packet15[1] = 0b10000001;

  // Coordenadas em graus * 10_000_000
  int32_t latRaw = 387223000;   // 38.7223000
  int32_t lonRaw = -91393000;   // -9.1393000
  int16_t altitude = 42;
  uint8_t bpm = 78;

  writeI32BE(&packet15[2], latRaw);
  writeI32BE(&packet15[6], lonRaw);
  writeI16BE(&packet15[10], altitude);
  packet15[12] = bpm;

  // O tablet calcula:
  // spo2 = 69 + spo2_raw  (se spo2_raw != 0)
  // temperature = 25.00 + temp_raw * 0.01
  // Queremos SpO2 = 98%  => spo2_raw = 29
  // Queremos Temp = 36.50 => temp_raw = 1150
  uint16_t spo2Raw = 29;
  uint16_t tempRaw = 1150;
  uint16_t packedTail = (uint16_t)(((spo2Raw & 0x1F) << 11) | (tempRaw & 0x07FF));

  packet15[13] = (uint8_t)((packedTail >> 8) & 0xFF);
  packet15[14] = (uint8_t)(packedTail & 0xFF);
}

void setup() {
  Serial.begin(115200);
  while (!Serial) {
    delay(10);
  }

  Serial.println();
  Serial.println("ESP teste -> tablet final");

  SPI.begin(PIN_SCK, PIN_MISO, PIN_MOSI, PIN_CS);
  LoRa.setPins(PIN_CS, PIN_RST, PIN_DIO0);

  if (!LoRa.begin(LORA_FREQ_HZ)) {
    Serial.println("Falha ao iniciar o modulo LoRa.");
    while (true) {
      delay(1000);
    }
  }

  LoRa.setSyncWord(0x12);
  LoRa.setSpreadingFactor(7);
  LoRa.setSignalBandwidth(125E3);
  LoRa.setCodingRate4(5);
  LoRa.enableCrc();

  Serial.print("LoRa OK em ");
  Serial.print(LORA_FREQ_HZ / 1000000.0, 1);
  Serial.println(" MHz");
}

void loop() {
  uint8_t packet[15];
  buildFullPacket(packet);
  encryptAesCtr(packet, sizeof(packet));

  LoRa.beginPacket();
  LoRa.write(packet, sizeof(packet));
  LoRa.endPacket();

  Serial.println("Trama completa cifrada enviada.");
  delay(3000);
}
