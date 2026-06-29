#include <Arduino.h>
#include "mbedtls/aes.h"
#include <string.h>

const unsigned char AES_KEY[16] = {
    0x2B, 0x7E, 0x15, 0x16, 0x28, 0xAE, 0xD2, 0xA6,
    0xAB, 0xF7, 0x15, 0x88, 0x09, 0xCF, 0x4F, 0x3C
};

const unsigned char FIXED_IV[16] = {
    0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
    0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F
};

// MAC Address fixo simulado para emparelhamento
const uint8_t MY_MAC[4] = {0x11, 0x22, 0x33, 0x44};

enum EstadoColete { REPOUSO, ATIVO };
EstadoColete estado_atual = REPOUSO;

struct SimulatedData {
    uint8_t bpm;
    uint8_t spo2;
    float temp;
    float lat;
    float lon;
    bool is_critical;
};

struct ResQSenseData {
    uint8_t net_id;
    uint8_t vest_id;
    bool flag_sos, flag_man_down, flag_vital, flag_bat, flag_hw, flag_pest, flag_fix;
    int32_t lat_abs, lon_abs;
    int16_t altitude;
    uint8_t bpm, spo2;
    uint16_t temp_raw;
};

SimulatedData dados_crus;
ResQSenseData meus_dados;

int32_t last_lat_abs = 0;
int32_t last_lon_abs = 0;
int16_t last_altitude = 0;
bool have_base = false;
uint8_t frame_counter = 0;

// Gestao TDMA
int myID = 0;
unsigned long cycleStartTime = 0;
unsigned long nextTxTime = 0;

#define CYCLE_MS 10000
#define SLOT_MS 500

// Funcao de debug para imprimir bits
void printBinaryBuffer(const char* titulo, const uint8_t* buffer, size_t len) {
    Serial.print(titulo);
    for (size_t i = 0; i < len; i++) {
        for (int b = 7; b >= 0; b--) {
            Serial.print(bitRead(buffer[i], b));
        }
        Serial.print(" ");
    }
    Serial.println();
}

float randomFloat(float minVal, float maxVal) {
    return minVal + ((float)random(0, 10001) / 10000.0) * (maxVal - minVal);
}

void generateFakeData(SimulatedData* data) {
    bool happens_critical = (random(0, 100) < 10);
    data->is_critical = happens_critical;

    if (happens_critical) {
        data->bpm = random(110, 161);
        data->spo2 = random(95, 101);
        data->temp = randomFloat(36.5, 37.5);
    } else {
        data->bpm = random(80, 101);
        data->spo2 = random(97, 101);
        data->temp = randomFloat(36.2, 37.2);
    }
}

void process_aes_ctr(const uint8_t* input, uint8_t* output, size_t length) {
    mbedtls_aes_context aes;
    mbedtls_aes_init(&aes);
    mbedtls_aes_setkey_enc(&aes, AES_KEY, 128);

    size_t nc_off = 0;
    unsigned char stream_block[16] = {0};
    unsigned char nonce_counter[16];
    memcpy(nonce_counter, FIXED_IV, 16);

    mbedtls_aes_crypt_ctr(&aes, length, &nc_off, nonce_counter, stream_block, input, output);
    mbedtls_aes_free(&aes);
}

void send_framed_packet(const uint8_t* data, uint8_t len) {
    uint8_t cifrado[16];
    process_aes_ctr(data, cifrado, len);

    Serial.write(0xAA);
    Serial.write(len);
    Serial.write(cifrado, len);
}

void enviar_ack(uint8_t funct_mask) {
    uint8_t ack_payload[2];
    ack_payload[0] = ((meus_dados.net_id & 0x07) << 5) | (meus_dados.vest_id & 0x1F);
    ack_payload[1] = (0x09 << 4) | (funct_mask & 0x0F);
    send_framed_packet(ack_payload, 2);

    // Serial.println("[TX] ACK Enviado.");
}

void pack_trama_completa(const ResQSenseData* data, uint8_t* buffer) {
    buffer[0] = ((data->net_id & 0x07) << 5) | (data->vest_id & 0x1F);
    buffer[1] = (1 << 7)
              | ((data->flag_sos & 0x01) << 6)
              | ((data->flag_man_down & 0x01) << 5)
              | ((data->flag_vital & 0x01) << 4)
              | ((data->flag_bat & 0x01) << 3)
              | ((data->flag_hw & 0x01) << 2)
              | ((data->flag_pest & 0x01) << 1)
              | (data->flag_fix & 0x01);

    buffer[2] = (data->lat_abs >> 24) & 0xFF;
    buffer[3] = (data->lat_abs >> 16) & 0xFF;
    buffer[4] = (data->lat_abs >> 8) & 0xFF;
    buffer[5] = data->lat_abs & 0xFF;

    buffer[6] = (data->lon_abs >> 24) & 0xFF;
    buffer[7] = (data->lon_abs >> 16) & 0xFF;
    buffer[8] = (data->lon_abs >> 8) & 0xFF;
    buffer[9] = data->lon_abs & 0xFF;

    buffer[10] = (data->altitude >> 8) & 0xFF;
    buffer[11] = data->altitude & 0xFF;

    buffer[12] = data->bpm;

    uint16_t spo2_temp = ((data->spo2 & 0x1F) << 11) | (data->temp_raw & 0x07FF);
    buffer[13] = (spo2_temp >> 8) & 0xFF;
    buffer[14] = spo2_temp & 0xFF;
}

void pack_trama_delta(const ResQSenseData* data, uint8_t* buffer) {
    int32_t lat_abs_val = abs(data->lat_abs);
    int32_t lon_abs_val = abs(data->lon_abs);
    int32_t lat_frac = lat_abs_val % 10000000;
    int32_t lon_frac = lon_abs_val % 10000000;

    uint16_t lat_frac16 = (uint16_t)((lat_frac * 65535LL) / 9999999LL);
    uint16_t lon_frac16 = (uint16_t)((lon_frac * 65535LL) / 9999999LL);

    int16_t alt_diff_16 = data->altitude - last_altitude;
    int8_t alt_diff = (int8_t)constrain(alt_diff_16, -128, 127);

    buffer[0] = ((data->net_id & 0x07) << 5) | (data->vest_id & 0x1F);
    buffer[1] = ((data->flag_sos & 0x01) << 6)
              | ((data->flag_man_down & 0x01) << 5)
              | ((data->flag_vital & 0x01) << 4)
              | ((data->flag_bat & 0x01) << 3)
              | ((data->flag_hw & 0x01) << 2)
              | ((data->flag_pest & 0x01) << 1)
              | (data->flag_fix & 0x01);

    buffer[2] = (lat_frac16 >> 8) & 0xFF;
    buffer[3] = lat_frac16 & 0xFF;
    buffer[4] = (lon_frac16 >> 8) & 0xFF;
    buffer[5] = lon_frac16 & 0xFF;
    buffer[6] = (uint8_t)alt_diff;
}

void ler_porta_serie(unsigned long timeout_ms) {
    unsigned long start = millis();

    while (millis() - start < timeout_ms) {
        if (Serial.available() >= 2) {
            if (Serial.read() == 0xAA) {
                uint8_t len = Serial.read();
                unsigned long waitPayload = millis();

                while (Serial.available() < len && millis() - waitPayload < 50) {
                    delay(1);
                }

                if (Serial.available() >= len) {
                    uint8_t cifrado[16], limpo[16];
                    Serial.readBytes(cifrado, len);
                    process_aes_ctr(cifrado, limpo, len);

                    uint8_t opCode = (limpo[1] >> 4) & 0x0F;

                    // 1. TRAMA SYNC (7 Bytes) no repouso
                    if (estado_atual == REPOUSO && len == 7 && opCode == 0x03) {
                        if (limpo[2] == MY_MAC[0] && limpo[3] == MY_MAC[1] &&
                            limpo[4] == MY_MAC[2] && limpo[5] == MY_MAC[3]) {

                            myID = limpo[0] & 0x1F;
                            meus_dados.vest_id = myID;
                            meus_dados.net_id = limpo[1] & 0x07;

                            // Serial.printf("\n[SYNC] Emparelhado! ID: %d | Net: %d\n", myID, meus_dados.net_id);
                            // printBinaryBuffer("Bits RX (SYNC): ", limpo, len);

                            estado_atual = ATIVO;

                            // Pela tabela da spec: ACK SYNC = 0011
                            enviar_ack(0x03);

                            cycleStartTime = millis();
                            nextTxTime = cycleStartTime + ((myID - 1) * SLOT_MS);
                            have_base = false;
                            frame_counter = 0;
                            return;
                        }
                    }

                    // 2. TRAMA UNSYNC (2 Bytes) no ativo
                    if (estado_atual == ATIVO && len == 2 && opCode == 0x0C) {
                        // Serial.println("\n[UNSYNC] Comando de Expulsao Recebido! A resetar...");
                        // printBinaryBuffer("Bits RX (UNSYNC): ", limpo, len);

                        enviar_ack(0x03);

                        myID = 0;
                        meus_dados.vest_id = 0;
                        meus_dados.net_id = 0;
                        estado_atual = REPOUSO;
                        return;
                    }
                }
            }
        }
    }
}

void setup() {
    Serial.begin(115200);
    randomSeed(analogRead(34));

    meus_dados.flag_sos = false;
    meus_dados.flag_man_down = false;
    meus_dados.flag_bat = false;
    meus_dados.flag_hw = false;
    meus_dados.flag_pest = false;
    meus_dados.flag_fix = true;
    meus_dados.altitude = 15;

    // Serial.println("\n--- Colete Iniciado ---");
}

void loop() {
    if (estado_atual == REPOUSO) {
        // Serial.println("[ ENERGIA ] Radio OFF -> Entrando em Deep Sleep por 4s...");
        delay(4000);

        // Serial.println("[ ENERGIA ] Radio ON -> Rx Window aberta por 1s (Canal 0) a procura de SYNC...");
        ler_porta_serie(1000);
    } else if (estado_atual == ATIVO) {
        if (millis() >= nextTxTime) {
            // Serial.println("[ ENERGIA ] Radio ON -> A preparar transmissao TDMA...");

            generateFakeData(&dados_crus);

            if (have_base && frame_counter != 0) {
                dados_crus.lat += randomFloat(-0.0002, 0.0002);
                dados_crus.lon += randomFloat(-0.0002, 0.0002);
            } else {
                dados_crus.lat = 40.6440000 + randomFloat(-0.0001, 0.0001);
                dados_crus.lon = -8.6450000 + randomFloat(-0.0001, 0.0001);
            }

            meus_dados.bpm = dados_crus.bpm;
            meus_dados.spo2 = (dados_crus.spo2 < 70) ? 0 : (dados_crus.spo2 - 69);
            meus_dados.temp_raw = (uint16_t)((dados_crus.temp - 25.0) * 100.0);
            meus_dados.lat_abs = (int32_t)(dados_crus.lat * 10000000.0);
            meus_dados.lon_abs = (int32_t)(dados_crus.lon * 10000000.0);
            meus_dados.altitude = 15 + random(-2, 3);
            meus_dados.flag_vital = dados_crus.is_critical;

            uint8_t payload[15];
            bool send_full = (!have_base || frame_counter == 0);

            if (send_full) {
                pack_trama_completa(&meus_dados, payload);
                send_framed_packet(payload, 15);

                // Serial.printf("[TX Slot %d] TRAMA COMPLETA (15B)\n", myID);
                // printBinaryBuffer("Bits TX: ", payload, 15);

                last_lat_abs = meus_dados.lat_abs;
                last_lon_abs = meus_dados.lon_abs;
                last_altitude = meus_dados.altitude;
                have_base = true;
                frame_counter = 1;
            } else {
                pack_trama_delta(&meus_dados, payload);
                send_framed_packet(payload, 7);

                // Serial.printf("[TX Slot %d] TRAMA DELTA (7B) - Seq: %d/5\n", myID, frame_counter);
                // printBinaryBuffer("Bits TX: ", payload, 7);

                last_lat_abs = meus_dados.lat_abs;
                last_lon_abs = meus_dados.lon_abs;
                last_altitude = meus_dados.altitude;
                frame_counter++;
                if (frame_counter > 5) frame_counter = 0;
            }

            cycleStartTime += CYCLE_MS;
            nextTxTime = cycleStartTime + ((myID - 1) * SLOT_MS);

            // Serial.println("Radio Rx Window aberta por ~100ms no Canal de Controlo...");
            ler_porta_serie(100);

            // Serial.println("Radio OFF -> A dormir ate ao proximo Slot TDMA...");
            // Serial.println("--------------------------------------------------");
        }
    }
}