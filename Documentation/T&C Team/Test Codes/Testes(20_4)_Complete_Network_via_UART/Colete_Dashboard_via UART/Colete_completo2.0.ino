/*

// teste do canal de dados

#include <Arduino.h>
#include "mbedtls/aes.h"
#include <string.h>

// --- CONFIGURACAO DA UART DO PROTOCOLO ---
#define USE_SERIAL2_PROTOCOL 0

#if USE_SERIAL2_PROTOCOL
  #define PROTOCOL_SERIAL Serial2
  #define DEBUG_ENABLED 1
#else
  #define PROTOCOL_SERIAL Serial
  #define DEBUG_ENABLED 0
#endif

#if DEBUG_ENABLED
  #define DEBUG_SERIAL Serial
#endif

#define VALIDATION_LOGS 1

// --- DEFINICOES DE REDE E ESTADOS ---
#define CH_SYNC 0
#define MAX_VESTS 30

enum ColeteState {
    STATE_SLEEP,
    STATE_TX_DATA,
    STATE_RX_CTRL_WINDOW,
    STATE_HANDLE_ALERT
};

ColeteState currentState = STATE_SLEEP;
uint8_t current_data_channel = 2;
uint8_t current_ctrl_channel = 7;
uint8_t current_channel = CH_SYNC;

// --- TEMPORIZACOES TDMA E JANELAS ---
const uint32_t TDMA_CYCLE_MS = 10000;
const uint32_t SLOT_MS = TDMA_CYCLE_MS / MAX_VESTS;
const uint32_t RX_WINDOW_MS = 100;
uint32_t last_cycle_start = 0;
uint32_t rx_window_start = 0;
uint32_t my_slot_offset = 0;

// --- CHAVES AES-128 CTR ---
const unsigned char AES_KEY[16] = {
    0x2B, 0x7E, 0x15, 0x16, 0x28, 0xAE, 0xD2, 0xA6,
    0xAB, 0xF7, 0x15, 0x88, 0x09, 0xCF, 0x4F, 0x3C
};
const unsigned char FIXED_IV[16] = {
    0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
    0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F
};

// --- OPCODES / FUNCTS ---
const uint8_t OPCODE_SYNC = 0x03;
const uint8_t OPCODE_NETWORK_ALIVE = 0x05;
const uint8_t OPCODE_ACK = 0x09;
const uint8_t OPCODE_VITAL_ALERT = 0x0A;
const uint8_t OPCODE_UNSYNC = 0x0C;

// Alinhado com o Python atual do tablet.
const uint8_t FUNCT_ACK_SYNC = 0x02;
const uint8_t FUNCT_NETWORK_ALIVE_TEAM_HOP = 0x03;

const uint8_t MY_MAC[4] = {0x11, 0x22, 0x33, 0x44};

// --- ESTRUTURAS DE DADOS ---
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

uint8_t buffer_payload_15[15];
uint8_t buffer_payload_7[7];

int32_t last_lat_abs = 0;
int32_t last_lon_abs = 0;
int16_t last_altitude = 0;
bool have_base = false;
uint8_t frame_counter = 0;
bool synced = false;

// --- HELPERS ---
float randomFloat(float minVal, float maxVal) {
    return minVal + ((float)random(0, 10001) / 10000.0) * (maxVal - minVal);
}

uint8_t make_header(uint8_t net_id, uint8_t vest_id) {
    return ((net_id & 0x07) << 5) | (vest_id & 0x1F);
}

uint8_t make_control_byte(uint8_t opcode, uint8_t funct) {
    return ((opcode & 0x0F) << 4) | (funct & 0x0F);
}

void switchToChannel(uint8_t ch) {
    current_channel = ch;
}

const char* state_to_string(ColeteState state) {
    switch (state) {
        case STATE_SLEEP: return "STATE_SLEEP";
        case STATE_TX_DATA: return "STATE_TX_DATA";
        case STATE_RX_CTRL_WINDOW: return "STATE_RX_CTRL_WINDOW";
        case STATE_HANDLE_ALERT: return "STATE_HANDLE_ALERT";
        default: return "UNKNOWN_STATE";
    }
}

void set_state(ColeteState new_state) {
    if (currentState != new_state) {
#if DEBUG_ENABLED && VALIDATION_LOGS
        DEBUG_SERIAL.print("[STATE] ");
        DEBUG_SERIAL.print(state_to_string(currentState));
        DEBUG_SERIAL.print(" -> ");
        DEBUG_SERIAL.println(state_to_string(new_state));
#endif
        currentState = new_state;
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

void send_framed_encrypted_packet(const uint8_t* data, uint8_t len) {
    uint8_t cifrado[16];
    process_aes_ctr(data, cifrado, len);
    PROTOCOL_SERIAL.write(0xAA);
    PROTOCOL_SERIAL.write(len);
    PROTOCOL_SERIAL.write(cifrado, len);

#if DEBUG_ENABLED && VALIDATION_LOGS
    DEBUG_SERIAL.print("[TX] Packet sent | Channel=");
    DEBUG_SERIAL.print(current_channel);
    DEBUG_SERIAL.print(" | Length=");
    DEBUG_SERIAL.println(len);
#endif
}

void send_ack(uint8_t funct) {
    uint8_t ack_payload[2];
    ack_payload[0] = make_header(meus_dados.net_id, meus_dados.vest_id);
    ack_payload[1] = make_control_byte(OPCODE_ACK, funct);
    send_framed_encrypted_packet(ack_payload, 2);

#if DEBUG_ENABLED && VALIDATION_LOGS
    DEBUG_SERIAL.print("[ACK] Sent ACK with funct=0x");
    DEBUG_SERIAL.println(funct, HEX);
#endif
}

bool read_framed_packet(uint8_t* buffer, uint8_t& len, uint32_t timeout_ms) {
    uint32_t start = millis();

    while (millis() - start < timeout_ms) {
        if (PROTOCOL_SERIAL.available() > 0) {
            int start_byte = PROTOCOL_SERIAL.read();
            if (start_byte != 0xAA) {
                continue;
            }

            while (PROTOCOL_SERIAL.available() < 1 && millis() - start < timeout_ms) {
                delay(1);
            }
            if (PROTOCOL_SERIAL.available() < 1) {
                return false;
            }

            len = (uint8_t)PROTOCOL_SERIAL.read();
            if (len != 2 && len != 3 && len != 4 && len != 7 && len != 15) {
                continue;
            }

            uint32_t wait_payload = millis();
            while (PROTOCOL_SERIAL.available() < len && millis() - wait_payload < 50) {
                delay(1);
            }
            if (PROTOCOL_SERIAL.available() < len) {
                return false;
            }

            PROTOCOL_SERIAL.readBytes(buffer, len);
            return true;
        }
        delay(1);
    }

    return false;
}

// --- PRINTS DE DEBUG ---
void print_validation_rx(uint8_t len) {
#if DEBUG_ENABLED && VALIDATION_LOGS
    DEBUG_SERIAL.print("[RX] Packet received on channel ");
    DEBUG_SERIAL.print(current_channel);
    DEBUG_SERIAL.print(" | Length=");
    DEBUG_SERIAL.println(len);
#endif
}

void print_validation_parser(const char* label) {
#if DEBUG_ENABLED && VALIDATION_LOGS
    DEBUG_SERIAL.print("[PARSER] ");
    DEBUG_SERIAL.println(label);
#endif
}

void print_validation_slot() {
#if DEBUG_ENABLED && VALIDATION_LOGS
    DEBUG_SERIAL.print("[TDMA] Vest ID=");
    DEBUG_SERIAL.print(meus_dados.vest_id);
    DEBUG_SERIAL.print(" | Slot offset=");
    DEBUG_SERIAL.print(my_slot_offset);
    DEBUG_SERIAL.println(" ms");
#endif
}

void print_data_frame(uint8_t len, bool is_delta) {
#if DEBUG_ENABLED
    DEBUG_SERIAL.println("=========================================");
    DEBUG_SERIAL.print("[CANAL ");
    DEBUG_SERIAL.print(current_channel);
    DEBUG_SERIAL.println("] TRAMA DE DADOS");
    DEBUG_SERIAL.print("Tamanho: ");
    DEBUG_SERIAL.print(len);
    DEBUG_SERIAL.println(is_delta ? " Bytes (Trama Delta)" : " Bytes (Trama Completa)");
    DEBUG_SERIAL.print("BPM: ");
    DEBUG_SERIAL.println(dados_crus.bpm);
    DEBUG_SERIAL.print("SpO2: ");
    DEBUG_SERIAL.println(dados_crus.spo2);
    DEBUG_SERIAL.print("Temp: ");
    DEBUG_SERIAL.println(dados_crus.temp);
    DEBUG_SERIAL.print("GPS: ");
    DEBUG_SERIAL.print(dados_crus.lat, 6);
    DEBUG_SERIAL.print(", ");
    DEBUG_SERIAL.println(dados_crus.lon, 6);
    DEBUG_SERIAL.println("=========================================\n");
#endif
}

void print_sync_ok() {
#if DEBUG_ENABLED
    DEBUG_SERIAL.println("=========================================");
    DEBUG_SERIAL.println("SYNC RECEBIDO COM SUCESSO");
    DEBUG_SERIAL.print("Vest ID: ");
    DEBUG_SERIAL.println(meus_dados.vest_id);
    DEBUG_SERIAL.print("Net ID: ");
    DEBUG_SERIAL.println(meus_dados.net_id);
    DEBUG_SERIAL.print("Data CH: ");
    DEBUG_SERIAL.println(current_data_channel);
    DEBUG_SERIAL.print("Ctrl CH: ");
    DEBUG_SERIAL.println(current_ctrl_channel);
    DEBUG_SERIAL.println("=========================================\n");
#endif
}

void print_unsync_ok() {
#if DEBUG_ENABLED
    DEBUG_SERIAL.println("=========================================");
    DEBUG_SERIAL.println("UNSYNC RECEBIDO");
    DEBUG_SERIAL.println("Colete removido da rede e regressou ao estado de repouso.");
    DEBUG_SERIAL.println("=========================================\n");
#endif
}

void print_team_hop(uint8_t new_data_ch, uint8_t new_ctrl_ch) {
#if DEBUG_ENABLED
    DEBUG_SERIAL.println("=========================================");
    DEBUG_SERIAL.println("TEAM HOP RECEBIDO");
    DEBUG_SERIAL.print("Novo Data CH: ");
    DEBUG_SERIAL.println(new_data_ch);
    DEBUG_SERIAL.print("Novo Ctrl CH: ");
    DEBUG_SERIAL.println(new_ctrl_ch);
    DEBUG_SERIAL.println("=========================================\n");
#endif
}

void print_network_alive() {
#if DEBUG_ENABLED && VALIDATION_LOGS
    DEBUG_SERIAL.println("[CTRL] NETWORK ALIVE recebido.");
#endif
}

void print_alert_sent(uint16_t valor_exato) {
#if DEBUG_ENABLED
    DEBUG_SERIAL.println("=========================================");
    DEBUG_SERIAL.println("ALERTA BIOMETRICO ENVIADO");
    DEBUG_SERIAL.print("Valor Exato: ");
    DEBUG_SERIAL.println(valor_exato);
    DEBUG_SERIAL.println("=========================================\n");
#endif
}

// --- EMPACOTAMENTO DE TRAMAS ---
void pack_trama_completa(const ResQSenseData* data, uint8_t* buffer) {
    buffer[0] = make_header(data->net_id, data->vest_id);
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

    buffer[0] = make_header(data->net_id, data->vest_id);
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

void send_async_alert(uint8_t opcode, uint8_t funct, uint16_t exact_value) {
    switchToChannel(current_ctrl_channel);

    uint8_t alert_pkt[4];
    alert_pkt[0] = make_header(meus_dados.net_id, meus_dados.vest_id);
    alert_pkt[1] = make_control_byte(opcode, funct);
    alert_pkt[2] = (exact_value >> 8) & 0xFF;
    alert_pkt[3] = exact_value & 0xFF;

    send_framed_encrypted_packet(alert_pkt, 4);
    print_alert_sent(exact_value);
}

// --- DADOS FALSOS MOCK ---
void generateFakeData(SimulatedData* data) {
    bool happens_critical = (random(0, 100) < 10);
    data->is_critical = happens_critical;

    if (happens_critical) {
        data->bpm = random(110, 161);
        data->spo2 = random(80, 95);
        data->temp = randomFloat(41.0, 43.1);
    } else {
        data->bpm = random(80, 101);
        data->spo2 = random(97, 101);
        data->temp = randomFloat(36.2, 37.2);
    }

    if (have_base && frame_counter != 0) {
        data->lat += randomFloat(-0.0002, 0.0002);
        data->lon += randomFloat(-0.0002, 0.0002);
    } else {
        data->lat = 40.6440000 + randomFloat(-0.0001, 0.0001);
        data->lon = -8.6450000 + randomFloat(-0.0001, 0.0001);
    }
}

void reset_network_state() {
    synced = false;
    have_base = false;
    frame_counter = 0;
    meus_dados.net_id = 0;
    meus_dados.vest_id = 0;
    current_data_channel = 2;
    current_ctrl_channel = 7;
    current_channel = CH_SYNC;
    set_state(STATE_SLEEP);
}

bool handle_sync_packet(const uint8_t* plain) {
    print_validation_parser("Decoding SYNC packet...");
    if (((plain[1] >> 4) & 0x0F) != OPCODE_SYNC) {
        return false;
    }

    if (plain[2] != MY_MAC[0] || plain[3] != MY_MAC[1] || plain[4] != MY_MAC[2] || plain[5] != MY_MAC[3]) {
        return false;
    }

    meus_dados.vest_id = plain[0] & 0x1F;
    meus_dados.net_id = plain[1] & 0x07;
    current_data_channel = (plain[6] >> 5) & 0x07;
    current_ctrl_channel = (plain[6] >> 1) & 0x07;
    my_slot_offset = (meus_dados.vest_id - 1) * SLOT_MS;

    synced = true;
    current_channel = current_data_channel;
    set_state(STATE_SLEEP);
    have_base = false;
    frame_counter = 0;
    last_cycle_start = millis();

    print_validation_slot();
    send_ack(FUNCT_ACK_SYNC);
    print_sync_ok();
    return true;
}

bool handle_control_packet(const uint8_t* plain, uint8_t len) {
    if (len == 2) {
        uint8_t opcode = (plain[1] >> 4) & 0x0F;
        uint8_t vest_id = plain[0] & 0x1F;
        print_validation_parser("Decoding 2-byte control packet...");

        if (opcode == OPCODE_UNSYNC && synced && vest_id == meus_dados.vest_id) {
            send_ack(FUNCT_ACK_SYNC);
            print_unsync_ok();
            reset_network_state();
            return true;
        }

        if (opcode == OPCODE_NETWORK_ALIVE) {
            print_network_alive();
            return true;
        }
        return false;
    }

    if (len == 3) {
        uint8_t opcode = (plain[1] >> 4) & 0x0F;
        uint8_t funct = plain[1] & 0x0F;
        print_validation_parser("Decoding 3-byte control packet...");
        if (opcode == OPCODE_NETWORK_ALIVE && funct == FUNCT_NETWORK_ALIVE_TEAM_HOP) {
            current_data_channel = (plain[2] >> 5) & 0x07;
            current_ctrl_channel = (plain[2] >> 2) & 0x07;
            print_team_hop(current_data_channel, current_ctrl_channel);
            return true;
        }
    }

    return false;
}

void poll_incoming_packets(uint32_t timeout_ms) {
    uint8_t cipher[16];
    uint8_t plain[16];
    uint8_t len = 0;

    if (!read_framed_packet(cipher, len, timeout_ms)) {
        return;
    }

    print_validation_rx(len);
    process_aes_ctr(cipher, plain, len);

    if (!synced && current_channel == CH_SYNC && len == 7) {
        handle_sync_packet(plain);
        return;
    }

    if (synced && current_channel == current_ctrl_channel) {
        handle_control_packet(plain, len);
    }
}

// --- SETUP ---
void setup() {
    Serial.begin(115200);

#if USE_SERIAL2_PROTOCOL
    Serial2.begin(115200, SERIAL_8N1, 16, 17);
#endif

    randomSeed(analogRead(34));

    meus_dados.flag_sos = false;
    meus_dados.flag_man_down = false;
    meus_dados.flag_bat = false;
    meus_dados.flag_hw = false;
    meus_dados.flag_pest = false;
    meus_dados.flag_fix = true;
    meus_dados.altitude = 15;

    dados_crus.lat = 40.6440000;
    dados_crus.lon = -8.6450000;

    reset_network_state();

#if DEBUG_ENABLED
    DEBUG_SERIAL.println("Colete UART pronto. A aguardar SYNC...");
#endif
}

// --- LOOP PRINCIPAL ---
void loop() {
    uint32_t current_time = millis();

    if (!synced) {
        switchToChannel(CH_SYNC);
        poll_incoming_packets(20);
        delay(5);
        return;
    }

    generateFakeData(&dados_crus);
    if (dados_crus.is_critical && currentState != STATE_HANDLE_ALERT) {
        set_state(STATE_HANDLE_ALERT);
    }

    switch (currentState) {
        case STATE_SLEEP:
            if (current_time - last_cycle_start >= TDMA_CYCLE_MS) {
                last_cycle_start = current_time;
            }

            if ((current_time - last_cycle_start) >= my_slot_offset &&
                (current_time - last_cycle_start) < (my_slot_offset + SLOT_MS)) {
                set_state(STATE_TX_DATA);
            } else {
                delay(5);
            }
            break;

        case STATE_TX_DATA:
            switchToChannel(current_data_channel);
            print_validation_parser("Preparing telemetry for TDMA transmission...");

            meus_dados.bpm = dados_crus.bpm;
            meus_dados.spo2 = (dados_crus.spo2 < 70) ? 0 : (dados_crus.spo2 - 69);
            meus_dados.temp_raw = (uint16_t)((dados_crus.temp - 25.0) * 100.0);
            meus_dados.lat_abs = (int32_t)(dados_crus.lat * 10000000.0);
            meus_dados.lon_abs = (int32_t)(dados_crus.lon * 10000000.0);
            meus_dados.altitude = 15 + random(-2, 3);
            meus_dados.flag_vital = dados_crus.is_critical;

            if (!have_base || frame_counter == 0) {
                pack_trama_completa(&meus_dados, buffer_payload_15);
                send_framed_encrypted_packet(buffer_payload_15, 15);
                print_data_frame(15, false);

                last_lat_abs = meus_dados.lat_abs;
                last_lon_abs = meus_dados.lon_abs;
                last_altitude = meus_dados.altitude;
                have_base = true;
                frame_counter = 1;
            } else {
                pack_trama_delta(&meus_dados, buffer_payload_7);
                send_framed_encrypted_packet(buffer_payload_7, 7);
                print_data_frame(7, true);

                last_lat_abs = meus_dados.lat_abs;
                last_lon_abs = meus_dados.lon_abs;
                last_altitude = meus_dados.altitude;
                frame_counter++;
                if (frame_counter > 5) {
                    frame_counter = 0;
                }
            }

            switchToChannel(current_ctrl_channel);
            rx_window_start = millis();
            print_validation_parser("Opening control receive window...");
            set_state(STATE_RX_CTRL_WINDOW);
            break;

        case STATE_RX_CTRL_WINDOW:
            if (millis() - rx_window_start < RX_WINDOW_MS) {
                poll_incoming_packets(10);
            } else {
                set_state(STATE_SLEEP);
            }
            break;

        case STATE_HANDLE_ALERT:
            print_validation_parser("Sending asynchronous biometric alert...");
            send_async_alert(OPCODE_VITAL_ALERT, 0x09, dados_crus.bpm);
            rx_window_start = millis();
            set_state(STATE_RX_CTRL_WINDOW);
            break;
    }
}

*/

//  teste de tramas com sync e unsync

#include <Arduino.h>
#include "mbedtls/aes.h"
#include <string.h>

// --- CONFIGURACAO DA UART DO PROTOCOLO ---
// Se estiveres a testar com um adaptador ligado aos pinos RX/TX do ESP32, deixa a 1.
// Se quiseres usar a USB da board para o protocolo, muda para 0.
#define USE_SERIAL2_PROTOCOL 0

#if USE_SERIAL2_PROTOCOL
  #define PROTOCOL_SERIAL Serial2
  #define DEBUG_ENABLED 1
#else
  #define PROTOCOL_SERIAL Serial
  #define DEBUG_ENABLED 0
#endif

#if DEBUG_ENABLED
  #define DEBUG_SERIAL Serial
#endif

#define VALIDATION_LOGS 1

// --- DEFINICOES DE REDE E ESTADOS ---
#define CH_SYNC 0
#define MAX_VESTS 30

enum ColeteState {
    STATE_SLEEP,
    STATE_TX_DATA,
    STATE_RX_CTRL_WINDOW,
    STATE_HANDLE_ALERT
};

ColeteState currentState = STATE_SLEEP;
uint8_t current_data_channel = 2;
uint8_t current_ctrl_channel = 7;
uint8_t current_channel = CH_SYNC;

// --- TEMPORIZACOES TDMA E JANELAS ---
const uint32_t TDMA_CYCLE_MS = 10000;
const uint32_t SLOT_MS = TDMA_CYCLE_MS / MAX_VESTS;
const uint32_t RX_WINDOW_MS = 100;
uint32_t last_cycle_start = 0;
uint32_t rx_window_start = 0;
uint32_t my_slot_offset = 0;

// --- CHAVES AES-128 CTR ---
const unsigned char AES_KEY[16] = {
    0x2B, 0x7E, 0x15, 0x16, 0x28, 0xAE, 0xD2, 0xA6,
    0xAB, 0xF7, 0x15, 0x88, 0x09, 0xCF, 0x4F, 0x3C
};
const unsigned char FIXED_IV[16] = {
    0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
    0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F
};

// --- OPCODES / FUNCTS ---
const uint8_t OPCODE_SYNC = 0x03;
const uint8_t OPCODE_NETWORK_ALIVE = 0x05;
const uint8_t OPCODE_ACK = 0x09;
const uint8_t OPCODE_VITAL_ALERT = 0x0A;
const uint8_t OPCODE_UNSYNC = 0x0C;

// Alinhado com o Python atual do tablet.
const uint8_t FUNCT_ACK_SYNC = 0x02;
const uint8_t FUNCT_NETWORK_ALIVE_TEAM_HOP = 0x03;

const uint8_t MY_MAC[4] = {0x11, 0x22, 0x33, 0x44};

// --- ESTRUTURAS DE DADOS ---
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

uint8_t buffer_payload_15[15];
uint8_t buffer_payload_7[7];

int32_t last_lat_abs = 0;
int32_t last_lon_abs = 0;
int16_t last_altitude = 0;
bool have_base = false;
uint8_t frame_counter = 0;
bool synced = false;

// --- HELPERS ---
float randomFloat(float minVal, float maxVal) {
    return minVal + ((float)random(0, 10001) / 10000.0) * (maxVal - minVal);
}

uint8_t make_header(uint8_t net_id, uint8_t vest_id) {
    return ((net_id & 0x07) << 5) | (vest_id & 0x1F);
}

uint8_t make_control_byte(uint8_t opcode, uint8_t funct) {
    return ((opcode & 0x0F) << 4) | (funct & 0x0F);
}

void switchToChannel(uint8_t ch) {
    current_channel = ch;
}

const char* state_to_string(ColeteState state) {
    switch (state) {
        case STATE_SLEEP: return "STATE_SLEEP";
        case STATE_TX_DATA: return "STATE_TX_DATA";
        case STATE_RX_CTRL_WINDOW: return "STATE_RX_CTRL_WINDOW";
        case STATE_HANDLE_ALERT: return "STATE_HANDLE_ALERT";
        default: return "UNKNOWN_STATE";
    }
}

void set_state(ColeteState new_state) {
    if (currentState != new_state) {
#if DEBUG_ENABLED && VALIDATION_LOGS
        DEBUG_SERIAL.print("[STATE] ");
        DEBUG_SERIAL.print(state_to_string(currentState));
        DEBUG_SERIAL.print(" -> ");
        DEBUG_SERIAL.println(state_to_string(new_state));
#endif
        currentState = new_state;
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

void send_framed_encrypted_packet(const uint8_t* data, uint8_t len) {
    uint8_t cifrado[16];
    process_aes_ctr(data, cifrado, len);
    PROTOCOL_SERIAL.write(0xAA);
    PROTOCOL_SERIAL.write(len);
    PROTOCOL_SERIAL.write(cifrado, len);

#if DEBUG_ENABLED && VALIDATION_LOGS
    DEBUG_SERIAL.print("[TX] Packet sent | Channel=");
    DEBUG_SERIAL.print(current_channel);
    DEBUG_SERIAL.print(" | Length=");
    DEBUG_SERIAL.println(len);
#endif
}

void send_ack(uint8_t funct) {
    uint8_t ack_payload[2];
    ack_payload[0] = make_header(meus_dados.net_id, meus_dados.vest_id);
    ack_payload[1] = make_control_byte(OPCODE_ACK, funct);
    send_framed_encrypted_packet(ack_payload, 2);

#if DEBUG_ENABLED && VALIDATION_LOGS
    DEBUG_SERIAL.print("[ACK] Sent ACK with funct=0x");
    DEBUG_SERIAL.println(funct, HEX);
#endif
}

bool read_framed_packet(uint8_t* buffer, uint8_t& len, uint32_t timeout_ms) {
    uint32_t start = millis();

    while (millis() - start < timeout_ms) {
        if (PROTOCOL_SERIAL.available() > 0) {
            int start_byte = PROTOCOL_SERIAL.read();
            if (start_byte != 0xAA) {
                continue;
            }

            while (PROTOCOL_SERIAL.available() < 1 && millis() - start < timeout_ms) {
                delay(1);
            }
            if (PROTOCOL_SERIAL.available() < 1) {
                return false;
            }

            len = (uint8_t)PROTOCOL_SERIAL.read();
            if (len != 2 && len != 3 && len != 4 && len != 7 && len != 15) {
                continue;
            }

            uint32_t wait_payload = millis();
            while (PROTOCOL_SERIAL.available() < len && millis() - wait_payload < 50) {
                delay(1);
            }
            if (PROTOCOL_SERIAL.available() < len) {
                return false;
            }

            PROTOCOL_SERIAL.readBytes(buffer, len);
            return true;
        }
        delay(1);
    }

    return false;
}

// --- PRINTS DE DEBUG ---
void print_validation_rx(uint8_t len) {
#if DEBUG_ENABLED && VALIDATION_LOGS
    DEBUG_SERIAL.print("[RX] Packet received on channel ");
    DEBUG_SERIAL.print(current_channel);
    DEBUG_SERIAL.print(" | Length=");
    DEBUG_SERIAL.println(len);
#endif
}

void print_validation_parser(const char* label) {
#if DEBUG_ENABLED && VALIDATION_LOGS
    DEBUG_SERIAL.print("[PARSER] ");
    DEBUG_SERIAL.println(label);
#endif
}

void print_validation_slot() {
#if DEBUG_ENABLED && VALIDATION_LOGS
    DEBUG_SERIAL.print("[TDMA] Vest ID=");
    DEBUG_SERIAL.print(meus_dados.vest_id);
    DEBUG_SERIAL.print(" | Slot offset=");
    DEBUG_SERIAL.print(my_slot_offset);
    DEBUG_SERIAL.println(" ms");
#endif
}

void print_data_frame(uint8_t len, bool is_delta) {
#if DEBUG_ENABLED
    DEBUG_SERIAL.println("=========================================");
    DEBUG_SERIAL.print("[CANAL ");
    DEBUG_SERIAL.print(current_channel);
    DEBUG_SERIAL.println("] TRAMA DE DADOS");
    DEBUG_SERIAL.print("Tamanho: ");
    DEBUG_SERIAL.print(len);
    DEBUG_SERIAL.println(is_delta ? " Bytes (Trama Delta)" : " Bytes (Trama Completa)");
    DEBUG_SERIAL.print("BPM: ");
    DEBUG_SERIAL.println(dados_crus.bpm);
    DEBUG_SERIAL.print("SpO2: ");
    DEBUG_SERIAL.println(dados_crus.spo2);
    DEBUG_SERIAL.print("Temp: ");
    DEBUG_SERIAL.println(dados_crus.temp);
    DEBUG_SERIAL.print("GPS: ");
    DEBUG_SERIAL.print(dados_crus.lat, 6);
    DEBUG_SERIAL.print(", ");
    DEBUG_SERIAL.println(dados_crus.lon, 6);
    DEBUG_SERIAL.println("=========================================\n");
#endif
}

void print_sync_ok() {
#if DEBUG_ENABLED
    DEBUG_SERIAL.println("=========================================");
    DEBUG_SERIAL.println("SYNC RECEBIDO COM SUCESSO");
    DEBUG_SERIAL.print("Vest ID: ");
    DEBUG_SERIAL.println(meus_dados.vest_id);
    DEBUG_SERIAL.print("Net ID: ");
    DEBUG_SERIAL.println(meus_dados.net_id);
    DEBUG_SERIAL.print("Data CH: ");
    DEBUG_SERIAL.println(current_data_channel);
    DEBUG_SERIAL.print("Ctrl CH: ");
    DEBUG_SERIAL.println(current_ctrl_channel);
    DEBUG_SERIAL.println("=========================================\n");
#endif
}

void print_unsync_ok() {
#if DEBUG_ENABLED
    DEBUG_SERIAL.println("=========================================");
    DEBUG_SERIAL.println("UNSYNC RECEBIDO");
    DEBUG_SERIAL.println("Colete removido da rede e regressou ao estado de repouso.");
    DEBUG_SERIAL.println("=========================================\n");
#endif
}

void print_team_hop(uint8_t new_data_ch, uint8_t new_ctrl_ch) {
#if DEBUG_ENABLED
    DEBUG_SERIAL.println("=========================================");
    DEBUG_SERIAL.println("TEAM HOP RECEBIDO");
    DEBUG_SERIAL.print("Novo Data CH: ");
    DEBUG_SERIAL.println(new_data_ch);
    DEBUG_SERIAL.print("Novo Ctrl CH: ");
    DEBUG_SERIAL.println(new_ctrl_ch);
    DEBUG_SERIAL.println("=========================================\n");
#endif
}

void print_network_alive() {
#if DEBUG_ENABLED && VALIDATION_LOGS
    DEBUG_SERIAL.println("[CTRL] NETWORK ALIVE recebido.");
#endif
}

void print_alert_sent(uint16_t valor_exato) {
#if DEBUG_ENABLED
    DEBUG_SERIAL.println("=========================================");
    DEBUG_SERIAL.println("ALERTA BIOMETRICO ENVIADO");
    DEBUG_SERIAL.print("Valor Exato: ");
    DEBUG_SERIAL.println(valor_exato);
    DEBUG_SERIAL.println("=========================================\n");
#endif
}

// --- EMPACOTAMENTO DE TRAMAS ---
void pack_trama_completa(const ResQSenseData* data, uint8_t* buffer) {
    buffer[0] = make_header(data->net_id, data->vest_id);
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

    buffer[0] = make_header(data->net_id, data->vest_id);
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

void send_async_alert(uint8_t opcode, uint8_t funct, uint16_t exact_value) {
    switchToChannel(current_ctrl_channel);

    uint8_t alert_pkt[4];
    alert_pkt[0] = make_header(meus_dados.net_id, meus_dados.vest_id);
    alert_pkt[1] = make_control_byte(opcode, funct);
    alert_pkt[2] = (exact_value >> 8) & 0xFF;
    alert_pkt[3] = exact_value & 0xFF;

    send_framed_encrypted_packet(alert_pkt, 4);
    print_alert_sent(exact_value);
}

// --- DADOS FALSOS MOCK ---
void generateFakeData(SimulatedData* data) {
    bool happens_critical = false;
    data->is_critical = happens_critical;

    if (happens_critical) {
        data->bpm = random(110, 161);
        data->spo2 = random(80, 95);
        data->temp = randomFloat(41.0, 43.1);
    } else {
        data->bpm = random(80, 101);
        data->spo2 = random(97, 101);
        data->temp = randomFloat(36.2, 37.2);
    }

    if (have_base && frame_counter != 0) {
        data->lat += randomFloat(-0.0002, 0.0002);
        data->lon += randomFloat(-0.0002, 0.0002);
    } else {
        data->lat = 40.6440000 + randomFloat(-0.0001, 0.0001);
        data->lon = -8.6450000 + randomFloat(-0.0001, 0.0001);
    }
}

void reset_network_state() {
    synced = false;
    have_base = false;
    frame_counter = 0;
    meus_dados.net_id = 0;
    meus_dados.vest_id = 0;
    current_data_channel = 2;
    current_ctrl_channel = 7;
    current_channel = CH_SYNC;
    set_state(STATE_SLEEP);
}

bool handle_sync_packet(const uint8_t* plain) {
    print_validation_parser("Decoding SYNC packet...");
    if (((plain[1] >> 4) & 0x0F) != OPCODE_SYNC) {
        return false;
    }

    if (plain[2] != MY_MAC[0] || plain[3] != MY_MAC[1] || plain[4] != MY_MAC[2] || plain[5] != MY_MAC[3]) {
        return false;
    }

    meus_dados.vest_id = plain[0] & 0x1F;
    meus_dados.net_id = plain[1] & 0x07;
    current_data_channel = (plain[6] >> 5) & 0x07;
    current_ctrl_channel = (plain[6] >> 1) & 0x07;
    my_slot_offset = (meus_dados.vest_id - 1) * SLOT_MS;

    synced = true;
    current_channel = current_data_channel;
    set_state(STATE_SLEEP);
    have_base = false;
    frame_counter = 0;
    last_cycle_start = millis();

    print_validation_slot();
    send_ack(FUNCT_ACK_SYNC);
    print_sync_ok();
    return true;
}

bool handle_control_packet(const uint8_t* plain, uint8_t len) {
    if (len == 2) {
        uint8_t opcode = (plain[1] >> 4) & 0x0F;
        uint8_t vest_id = plain[0] & 0x1F;
        print_validation_parser("Decoding 2-byte control packet...");

        if (opcode == OPCODE_UNSYNC && synced && vest_id == meus_dados.vest_id) {
            send_ack(FUNCT_ACK_SYNC);
            print_unsync_ok();
            reset_network_state();
            return true;
        }

        if (opcode == OPCODE_NETWORK_ALIVE) {
            print_network_alive();
            return true;
        }
        return false;
    }

    if (len == 3) {
        uint8_t opcode = (plain[1] >> 4) & 0x0F;
        uint8_t funct = plain[1] & 0x0F;
        print_validation_parser("Decoding 3-byte control packet...");
        if (opcode == OPCODE_NETWORK_ALIVE && funct == FUNCT_NETWORK_ALIVE_TEAM_HOP) {
            current_data_channel = (plain[2] >> 5) & 0x07;
            current_ctrl_channel = (plain[2] >> 2) & 0x07;
            print_team_hop(current_data_channel, current_ctrl_channel);
            return true;
        }
    }

    return false;
}

void poll_incoming_packets(uint32_t timeout_ms) {
    uint8_t cipher[16];
    uint8_t plain[16];
    uint8_t len = 0;

    if (!read_framed_packet(cipher, len, timeout_ms)) {
        return;
    }

    print_validation_rx(len);
    process_aes_ctr(cipher, plain, len);

    if (!synced && current_channel == CH_SYNC && len == 7) {
        handle_sync_packet(plain);
        return;
    }

    if (synced && current_channel == current_ctrl_channel) {
        handle_control_packet(plain, len);
    }
}

// --- SETUP ---
void setup() {
    Serial.begin(115200);

#if USE_SERIAL2_PROTOCOL
    Serial2.begin(115200, SERIAL_8N1, 16, 17);
#endif

    randomSeed(analogRead(34));

    meus_dados.flag_sos = false;
    meus_dados.flag_man_down = false;
    meus_dados.flag_bat = false;
    meus_dados.flag_hw = false;
    meus_dados.flag_pest = false;
    meus_dados.flag_fix = true;
    meus_dados.altitude = 15;

    dados_crus.lat = 40.6440000;
    dados_crus.lon = -8.6450000;

    reset_network_state();

#if DEBUG_ENABLED
    DEBUG_SERIAL.println("Colete UART pronto. A aguardar SYNC...");
#endif
}

// --- LOOP PRINCIPAL ---
void loop() {
    uint32_t current_time = millis();

    if (!synced) {
        switchToChannel(CH_SYNC);
        poll_incoming_packets(20);
        delay(5);
        return;
    }

    generateFakeData(&dados_crus);
    if (dados_crus.is_critical && currentState != STATE_HANDLE_ALERT) {
        set_state(STATE_HANDLE_ALERT);
    }

    switch (currentState) {
        case STATE_SLEEP:
            if (current_time - last_cycle_start >= TDMA_CYCLE_MS) {
                last_cycle_start = current_time;
            }

            if ((current_time - last_cycle_start) >= my_slot_offset &&
                (current_time - last_cycle_start) < (my_slot_offset + SLOT_MS)) {
                set_state(STATE_TX_DATA);
            } else {
                delay(5);
            }
            break;

        case STATE_TX_DATA:
            switchToChannel(current_data_channel);
            print_validation_parser("Preparing telemetry for TDMA transmission...");

            meus_dados.bpm = dados_crus.bpm;
            meus_dados.spo2 = (dados_crus.spo2 < 70) ? 0 : (dados_crus.spo2 - 69);
            meus_dados.temp_raw = (uint16_t)((dados_crus.temp - 25.0) * 100.0);
            meus_dados.lat_abs = (int32_t)(dados_crus.lat * 10000000.0);
            meus_dados.lon_abs = (int32_t)(dados_crus.lon * 10000000.0);
            meus_dados.altitude = 15 + random(-2, 3);
            meus_dados.flag_vital = dados_crus.is_critical;

            if (!have_base || frame_counter == 0) {
                pack_trama_completa(&meus_dados, buffer_payload_15);
                send_framed_encrypted_packet(buffer_payload_15, 15);
                print_data_frame(15, false);

                last_lat_abs = meus_dados.lat_abs;
                last_lon_abs = meus_dados.lon_abs;
                last_altitude = meus_dados.altitude;
                have_base = true;
                frame_counter = 1;
            } else {
                pack_trama_delta(&meus_dados, buffer_payload_7);
                send_framed_encrypted_packet(buffer_payload_7, 7);
                print_data_frame(7, true);

                last_lat_abs = meus_dados.lat_abs;
                last_lon_abs = meus_dados.lon_abs;
                last_altitude = meus_dados.altitude;
                frame_counter++;
                if (frame_counter > 5) {
                    frame_counter = 0;
                }
            }

            switchToChannel(current_ctrl_channel);
            rx_window_start = millis();
            print_validation_parser("Opening control receive window...");
            set_state(STATE_RX_CTRL_WINDOW);
            break;

        case STATE_RX_CTRL_WINDOW:
            if (millis() - rx_window_start < RX_WINDOW_MS) {
                poll_incoming_packets(10);
            } else {
                set_state(STATE_SLEEP);
            }
            break;

        case STATE_HANDLE_ALERT:
            print_validation_parser("Sending asynchronous biometric alert...");
            send_async_alert(OPCODE_VITAL_ALERT, 0x09, dados_crus.bpm);
            rx_window_start = millis();
            set_state(STATE_RX_CTRL_WINDOW);
            break;
    }
}