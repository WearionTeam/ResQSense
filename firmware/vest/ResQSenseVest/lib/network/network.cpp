/**
 * @file network.cpp
 * @brief Implementação da máquina de estados LoRa, encriptação e FreeRTOS.
 */
#include "network.h"
#include <mbedtls/aes.h> 

/**
 * @brief Chave criptográfica AES-128-CTR e Vetor de Inicialização (IV).
 * @note Partilhada assimetricamente com o Tablet. Não alterar sem atualizar o Python.
 */
static const uint8_t AES_KEY[16] = {0x2B, 0x7E, 0x15, 0x16, 0x28, 0xAE, 0xD2, 0xA6, 0xAB, 0xF7, 0x15, 0x88, 0x09, 0xCF, 0x4F, 0x3C};
static const uint8_t FIXED_IV[16]  = {0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F};

TaskHandle_t LoRaNetworkHandle = NULL;          /**< Handler da Task da rede para notificações (TaskNotify) */
const char* LORA_TAG = "LORA";                  /**< Tag para o output do ESP_LOG */

QueueHandle_t telemetry_queue;                  /**< Fila principal para telemetria rotineira */
QueueHandle_t alert_queue;                      /**< Fila prioritária para envio de alertas críticos */
static QueueHandle_t pending_alert_queue;       /**< Fila auxiliar para alertas pendentes sem resposta */

static NetworkState current_state = WAIT_SYNC;  /**< Estado atual da máquina de estados do nó LoRa */
static RX_ORIGIN rx_origin = SYNC_STATE;        /**< Contexto que originou a atual janela de receção */

SPIClass SPI_LORA(FSPI);
Module* mod = nullptr;
SX1276* vest = nullptr;

static uint32_t last_full_packet_time = 0;      /**< Momento em que o último pacote de dados completo foi enviado */
static uint32_t tdmaBaseTime = 0;               /**< Timestamp para cálculo dos slots TDMA recebido no Sync */
static uint32_t rxTime = 0;                     /**< Timestamp de início da janela de escuta (RX Window) */
static uint32_t syncTime = 0;                   /**< Última vez que o nó recebeu um Sync da gateway */
static uint32_t lastCycleSent = 0xFFFFFFFF;     /**< Guarda o último ciclo TDMA em que transmitimos para evitar envios duplicados no mesmo slot */

static int assignedVestID = -1;                 /**< ID do Colete atribuído pela Gateway (1 a 31) */
static int assignedNet = -1;                    /**< ID da Rede à qual o colete pertence */
static int dataCH = -1;                         /**< Canal (frequência) utilizado para telemetria */
static int controlCH = -1;                      /**< Canal (frequência) utilizado para controlo, alertas e sync */

#define ACK_TIMEOUT_MS 2500
static bool waiting_critical_ack = false;       /**< Flag verdadeira se aguardamos um ACK de emergência */
static uint8_t last_critical_alert[4];          /**< Guarda o último pacote de emergência em caso de retentativa */
static uint32_t critical_alert_tx_time = 0;     /**< Momento de envio do alerta para cálculo de timeout */
static uint8_t missed_sync_cycles = 0;          /**< Contador de pacotes de Sync falhados consecutivamente */
static uint8_t critical_alert_retries = 0;      /**< Contador de retentativas do alerta atual */

// Última posição enviada num full packet guardada para depois enviar pacotes delta
static double last_tx_lat = 0.0;
static double last_tx_lon = 0.0;
static float  last_tx_alt = 0.0f;
static bool first_packet_sent = false; 

static int sync_counter = 0;

/** @brief Transita o rádio para a frequência designada para Dados */
static void switchToDataChannel() { vest->setFrequency(FREQ_CH[dataCH != -1 ? dataCH : 0]); }

/** @brief Transita o rádio para a frequência designada para Controlo e Alertas */
static void switchToCtrlChannel() { vest->setFrequency(FREQ_CH[controlCH != -1 ? controlCH : 7]); } // Frequência Default 7

/**
 * @brief Aplica cifra/decifra AES-128-CTR ao buffer de dados.
 * @details Como o modo CTR é um stream cipher (simétrico no processo XOR), a mesma função serve para Encriptar e Desencriptar.
 * @param data Ponteiro para o buffer de bytes a processar. A conversão ocorre in-place.
 * @param length Tamanho do payload em bytes.
 */
void cryptAESCTR(uint8_t* data, size_t length) {
    mbedtls_aes_context aes;
    mbedtls_aes_init(&aes);
    mbedtls_aes_setkey_enc(&aes, AES_KEY, 128);
    size_t nc_off = 0;
    uint8_t nonce_counter[16];
    uint8_t stream_block[16];
    memcpy(nonce_counter, FIXED_IV, 16);
    mbedtls_aes_crypt_ctr(&aes, length, &nc_off, nonce_counter, stream_block, data, data);
    mbedtls_aes_free(&aes);
}

/**
 * @brief Rotina de Interrupção de Hardware (ISR) ativada pelo pino DIO0 do LoRa.
 * @details Avisa o FreeRTOS para acordar a Task do LoRa usando `vTaskNotifyGiveFromISR` sempre que uma transmissão 
 * termina (TX_DONE) ou um pacote é recebido (RX_DONE).
 */
#if defined(ESP8266) || defined(ESP32)
  ICACHE_RAM_ATTR
#endif
void setFlag(void) {
    if (LoRaNetworkHandle != NULL) {
        BaseType_t xHigherPriorityTaskWoken = pdFALSE;
        vTaskNotifyGiveFromISR(LoRaNetworkHandle, &xHigherPriorityTaskWoken);
        if (xHigherPriorityTaskWoken) portYIELD_FROM_ISR(); 
    }
}

bool startNetwork() {
    telemetry_queue = xQueueCreate(10, sizeof(processed_data));
    alert_queue = xQueueCreate(5, sizeof(uint8_t[4])); 
    pending_alert_queue = xQueueCreate(5, sizeof(uint8_t[4]));

    SPI_LORA.begin(SPI_SCLK, SPI_MISO, SPI_MOSI, SPI_NSS);
    mod = new Module(SPI_NSS, DIO0, NRESET, RADIOLIB_NC, SPI_LORA); 
    vest = new SX1276(mod);

    int state = vest->begin(FREQ_CH[0], BAND, SF, CR, SYNC_WORD, TX_POWER, PREAMBLE);
    if (state != RADIOLIB_ERR_NONE) {
        ESP_LOGE(LORA_TAG, "Falha ao iniciar RadioLib: %d", state);
        return false;
    }

    pinMode(DIO0,INPUT);
    vest->setDio0Action(setFlag,RISING);
    ESP_LOGI(LORA_TAG, "Módulo SX1276 Iniciado com Sucesso!");
    return true;
}

void queueTelemetryPacket(const processed_data &data) {
    if (uxQueueSpacesAvailable(telemetry_queue) == 0) {
        processed_data dummy;
        xQueueReceive(telemetry_queue, &dummy, 0); 
    }
    xQueueSend(telemetry_queue, &data, 0);
}

// O array de Alertas é construído manualmente logo aqui
void queueAlert(OpCode opcode, uint8_t functionCode, uint16_t sensor_value) {
    uint8_t buf[4] = {0};
    uint8_t net = assignedNet == -1 ? 0 : assignedNet;
    uint8_t vid = assignedVestID == -1 ? 0 : assignedVestID;
    
    // Constrói o cabeçalho base [NetID(3 bits) | VestID(5 bits)]
    buf[0] = ((net & 0x07) << 5) | (vid & 0x1F);
    buf[1] = ((opcode & 0x0F) << 4) | (functionCode & 0x0F);
    
    // Anexa o payload extra de 16-bits para vitais ou dados quantitativos
    if (opcode == OP_VTALERT || opcode == OP_QALERT) {
        buf[2] = (sensor_value >> 8) & 0xFF; // Valor Big-Endian
        buf[3] = sensor_value & 0xFF;
    }
    xQueueSend(alert_queue, buf, 0);
}

void TaskLoRaNetwork(void *pvParameters) {
    processed_data current_sensor_data;
    memset(&current_sensor_data, 0, sizeof(processed_data));

    vest->startReceive(); 

    for(;;) {
        TickType_t current_tick = xTaskGetTickCount();
        uint32_t current_time = current_tick * portTICK_PERIOD_MS;

        // Tenta obter novos dados de telemetria
        xQueueReceive(telemetry_queue, &current_sensor_data, 0);

        if (assignedVestID != -1) { // Processa e/ou envia alertas se tiver na rede
            
            if (waiting_critical_ack && (current_time - critical_alert_tx_time > ACK_TIMEOUT_MS)) {
                if (critical_alert_retries < 3) {
                    xQueueSendToFront(alert_queue, last_critical_alert, 0);
                    critical_alert_retries++;
                    ESP_LOGW(LORA_TAG, "Timeout do Alerta. A retransmitir...");
                } else {
                    waiting_critical_ack = false; 
                    critical_alert_retries = 0;
                }
            }

            uint8_t buf[4];
            
            if (!waiting_critical_ack && xQueueReceive(alert_queue, buf, 0)) {
                uint8_t op = (buf[1] >> 4) & 0x0F;
                uint8_t funct = buf[1] & 0x0F;
                
                if (funct == funct_qa_md || funct == funct_qa_sos || op == OP_VTALERT) {
                    switchToCtrlChannel();       

                    bool channelFree = false;

                    // Implementação básica de LBT (Listen Before Talk) - Tenta 4 vezes
                    for (int i = 0; i < 4; i++)
                    {
                        int lbt_state = vest->scanChannel();

                        if (lbt_state == RADIOLIB_CHANNEL_FREE)
                        {
                            channelFree = true;
                            break;
                        }
                        vTaskDelay(pdMS_TO_TICKS(random(10,40)));
                    }

                    if (!channelFree)
                    {
                        ESP_LOGW(LORA_TAG,"LBT: CANAL SATURADO. Enviando emergência sem LBT");
                    }                    
                    uint8_t tx_len = (op == OP_VTALERT || op == OP_QALERT) ? 4 : 2;
                    uint8_t tx_buf[4];
                    memcpy(tx_buf, buf, tx_len);
                    cryptAESCTR(tx_buf, tx_len); 
                    vest->transmit(tx_buf, tx_len);
                    
                    memcpy(last_critical_alert, buf, 4); 
                    critical_alert_tx_time = current_time;
                    waiting_critical_ack = true;
                    
                    ulTaskNotifyTake(pdTRUE, 0); 
                    vest->startReceive(); 
                    rxTime = current_time;
                    current_state = RECEIVE_WINDOW;
                    rx_origin = SEND_STATE;
                } else {
                    xQueueSend(pending_alert_queue, buf, 0); 
                }
            }
        } else {
            // Limpa as filas caso o dispositivo perca ligação à rede
            xQueueReset(alert_queue);
            xQueueReset(pending_alert_queue);
            waiting_critical_ack = false;
        }

        switch (current_state) {
            /**
             * @brief WAIT_SYNC
             * Fica no canal de default(0) à espera de um pacote de sincronização 
             * emitido pelo Dashboard (Raspberry Pi) para iniciar o emparelhamento.
             */
            case WAIT_SYNC: {
                if (current_time - syncTime >= SYNC_INTERVAL) {
                    vest->setFrequency(FREQ_CH[0]);
                    vest->startReceive();
                    rxTime = current_time;
                    current_state = RECEIVE_WINDOW;
                    rx_origin = SYNC_STATE;
                    ESP_LOGI(LORA_TAG,"WAIT_SYNC: Abriu a receive window");
                    syncTime = current_time;
                }
                break;
            }
            /**
             * @brief SEND_DATA
             * Gere a transmissão de dados no tempo designado do utilizador (TDMA Slot).
             * Se for a sua vez de falar, comprime a telemetria, encripta e transmite.
             */
            case SEND_DATA: {
                uint32_t currentCycle = (current_time - tdmaBaseTime) / TDMA_CYCLE_MS;
                uint32_t timeInCycle = (current_time - tdmaBaseTime) % TDMA_CYCLE_MS;
                uint32_t slotStart = (assignedVestID) * SLOT_TIME;

                // Verifica se estamos dentro da janela do utilizador (Slot Time) e ainda não transmitiu neste ciclo
                if (timeInCycle >= slotStart && timeInCycle < (slotStart + 100) && lastCycleSent != currentCycle) {
                    lastCycleSent = currentCycle;
                    ESP_LOGI(LORA_TAG,"Estou a mandar dados");
                    
                    bool isTimeFP = (current_time - last_full_packet_time) >= FULL_PACKET_INTERVAL;
                    uint8_t t_flag = (isTimeFP || !first_packet_sent) ? 1 : 0;
                    uint8_t m_flag = current_sensor_data.fall_detected ? 1 : 0;
                    uint8_t v_flag = current_sensor_data.ppg_alert ? 1 : 0;
                    uint8_t f_flag = 1; 

                    uint8_t flag_byte = (t_flag << 7) | (0 << 6) | (m_flag << 5) | (v_flag << 4) | 
                                        (0 << 3) | (0 << 2) | (0 << 1) | f_flag;

                    ulTaskNotifyTake(pdTRUE, 0); 
                    switchToDataChannel();

                    if (t_flag) {
                        // Empacotamento de pacote completo (Full Frame - 15 bytes)
                        uint8_t tx_buf[15];
                        tx_buf[0] = ((assignedNet & 0x07) << 5) | (assignedVestID & 0x1F);
                        tx_buf[1] = flag_byte;
                        
                        // Empacotamento Big-Endian para o Python
                        int32_t lat_raw = (int32_t)(current_sensor_data.lat * 10000000.0); 
                        int32_t lon_raw = (int32_t)(current_sensor_data.lon * 10000000.0);
                        tx_buf[2] = (lat_raw >> 24) & 0xFF; tx_buf[3] = (lat_raw >> 16) & 0xFF; tx_buf[4] = (lat_raw >> 8) & 0xFF; tx_buf[5] = lat_raw & 0xFF;
                        tx_buf[6] = (lon_raw >> 24) & 0xFF; tx_buf[7] = (lon_raw >> 16) & 0xFF; tx_buf[8] = (lon_raw >> 8) & 0xFF; tx_buf[9] = lon_raw & 0xFF;
                        
                        int16_t alt_raw = (int16_t)current_sensor_data.alt;
                        tx_buf[10] = (alt_raw >> 8) & 0xFF; tx_buf[11] = alt_raw & 0xFF;
                        
                        tx_buf[12] = (uint8_t)current_sensor_data.hr;
                        
                        uint16_t spo2_raw = (current_sensor_data.spo2 < 70) ? 0 : (current_sensor_data.spo2 - 69);
                        uint16_t delta_temp;
                        if (isnan(current_sensor_data.temp))
                        {
                            delta_temp = 0;
                        }
                        
                        delta_temp = (uint16_t)((current_sensor_data.temp - 20.0f));
                        if (delta_temp<0.0f)
                        {
                            delta_temp = delta_temp * -1;
                        }

                        delta_temp = delta_temp * 100;

                        uint16_t packed_tail = ((spo2_raw & 0x1F) << 11) | (delta_temp & 0x07FF);
                        tx_buf[13] = (packed_tail >> 8) & 0xFF; tx_buf[14] = packed_tail & 0xFF;

                        ESP_LOGI(LORA_TAG, "BYTES RAW: %02X %02X %02X %02X %02X %02X %02X %02X %02X %02X %02X %02X %02X %02X %02X",
                                 tx_buf[0], tx_buf[1], tx_buf[2], tx_buf[3], tx_buf[4], tx_buf[5], 
                                 tx_buf[6], tx_buf[7], tx_buf[8], tx_buf[9], tx_buf[10], tx_buf[11], 
                                 tx_buf[12], tx_buf[13], tx_buf[14]);

                        cryptAESCTR(tx_buf, 15);
                        vest->transmit(tx_buf, 15);
                        
                        last_full_packet_time = current_time;
                        first_packet_sent = true; 
                        ESP_LOGI(LORA_TAG, "Trama Completa (15B) Enviada!");
                        ESP_LOGI(LORA_TAG, "Trama Completa (15B) Enviada! Net:%d Vest:%d | Lat:%.5f Lon:%.5f Alt:%d HR:%d SpO2:%d Temp:%.1f",
                                 assignedNet, assignedVestID, 
                                 current_sensor_data.lat, current_sensor_data.lon, 
                                 (int)current_sensor_data.alt, current_sensor_data.hr, 
                                 current_sensor_data.spo2, current_sensor_data.temp);
                    } else {
                        // Empacotamento de Delta de Posição (Frame Curta - 7 bytes)
                        uint8_t tx_buf[7];
                        tx_buf[0] = ((assignedNet & 0x07) << 5) | (assignedVestID & 0x1F);
                        tx_buf[1] = flag_byte;

                        int32_t lat_raw = (int32_t)(current_sensor_data.lat * 10000000.0);
                        int32_t lon_raw = (int32_t)(current_sensor_data.lon * 10000000.0);
                        
                        uint32_t lat_frac_nova = labs(lat_raw) % 10000000;
                        uint32_t lon_frac_nova = labs(lon_raw) % 10000000;
                        
                        uint16_t lat_frac16 = (uint16_t)((lat_frac_nova * 65535ULL) / 9999999ULL);
                        uint16_t lon_frac16 = (uint16_t)((lon_frac_nova * 65535ULL) / 9999999ULL);
                        
                        int8_t alt_delta = (int8_t)(current_sensor_data.alt - last_tx_alt);

                        tx_buf[2] = (lat_frac16 >> 8) & 0xFF; tx_buf[3] = lat_frac16 & 0xFF;
                        tx_buf[4] = (lon_frac16 >> 8) & 0xFF; tx_buf[5] = lon_frac16 & 0xFF;
                        tx_buf[6] = alt_delta;

                        ESP_LOGI(LORA_TAG, "BYTES RAW DELTA: %02X %02X %02X %02X %02X %02X %02X",
                                 tx_buf[0], tx_buf[1], tx_buf[2], tx_buf[3], tx_buf[4], tx_buf[5], tx_buf[6]);

                        cryptAESCTR(tx_buf, 7);
                        vest->transmit(tx_buf, 7);
                        
                        // 3. Imprimir os valores reais focados na Navegação
                        ESP_LOGI(LORA_TAG, "Trama Delta (7B) Enviada! Net:%d Vest:%d | Lat:%.5f Lon:%.5f | AltDelta:%d",
                                 assignedNet, assignedVestID, 
                                 current_sensor_data.lat, current_sensor_data.lon, 
                                 alt_delta);
                    }
                    
                    // Atualização da localização para o próximo delta
                    last_tx_lat = current_sensor_data.lat;
                    last_tx_lon = current_sensor_data.lon;
                    last_tx_alt = current_sensor_data.alt;

                    uint8_t pending_buf[4];
                    while (xQueueReceive(pending_alert_queue, pending_buf, 0)) {
                        switchToCtrlChannel();
                        uint8_t op = (pending_buf[1] >> 4) & 0x0F;
                        uint8_t tx_len = (op == OP_VTALERT || op == OP_QALERT) ? 4 : 2;
                        
                        uint8_t tx_buf[4];
                        memcpy(tx_buf, pending_buf, tx_len);
                        cryptAESCTR(tx_buf, tx_len);
                        vest->transmit(tx_buf, tx_len);
                    }

                    // Fim da Transmissão, volta ao canal de Controlo
                    switchToCtrlChannel();       
                    vest->startReceive();
                    rxTime = current_time;
                    current_state = RECEIVE_WINDOW;
                    rx_origin = SEND_STATE;
                }
                break;
            }
            /**
             * @brief RECEIVE_WINDOW
             * Comuta o funcionamento do módulo para receber dados por um período pré-determinado para receber pacotes.
             * O tempo de escuta depende se estamos à espera de um SYNC ou de comandos normais.
             */
            case RECEIVE_WINDOW: {
                // Escolhe o intervalo necessário
                uint32_t interval = (rx_origin == SYNC_STATE) ? RX_WINDOW_SYNC_INTERVAL : RX_WINDOW_DATA_INTERVAL;
                bool timeout = false;

                // Timeout só ocorre se já estivermos na rede, para evitar bloquear 
                if (assignedVestID != -1 || rx_origin == SEND_STATE) {
                    timeout = (current_time - rxTime) >= interval;
                }

                if (timeout) {
                    if (rx_origin == SYNC_STATE) {
                        if (assignedVestID == -1) {
                            syncTime = current_time;
                            current_state = WAIT_SYNC;
                        } 
                        else {
                            // Obteve rede mas perdeu o sync
                            if (missed_sync_cycles < 3) {
                                missed_sync_cycles++;
                                current_state = SEND_DATA; 
                            } else {
                                // Perdeu totalmente a rede
                                syncTime = current_time;
                                current_state = WAIT_SYNC;
                            }
                        }
                    } else {
                        current_state = SEND_DATA;
                    }
                    vest->standby(); 
                } else if (ulTaskNotifyTake(pdTRUE, 0) > 0) {
                    current_state = PROCESS_PACKET;
                }
                break;
            }
            /**
             * @brief PROCESS_PACKET
             * É invocado pela interrupção (operationDone) quando recebe algo
             * Valida tamanhos, desencripta e encaminha a lógica baseada no OPCODE recebido.
             */
            case PROCESS_PACKET: {
                size_t len = vest->getPacketLength();

                if (len > 256) return; // Recusa qualquer pacote maior que o tamanho máximo
                
                if (len > 0 && len <= 256) {
                    ESP_LOGI(LORA_TAG, "A antena recebeu um pacote! Tamanho: %d bytes", len);
                    uint8_t buffer[256];
                    int state = vest->readData(buffer, len);

                    if (state == RADIOLIB_ERR_NONE) {
                        cryptAESCTR(buffer, len); 

                        uint8_t rx_net = (buffer[0] >> 5) & 0x07;
                        uint8_t rx_vestID = buffer[0] & 0x1F;
                        uint8_t rx_opcode = (buffer[1] >> 4) & 0x0F;
                        uint8_t rx_funct = buffer[1] & 0x0F;
                        // Processa o sync
                        if (len == 7 && rx_opcode == OP_SYNC) {
                            uint32_t rx_mac = (buffer[2] << 24) | (buffer[3] << 16) | (buffer[4] << 8) | buffer[5];
                        
                            // Recusa o pacote se ele não tiver o MAC Address definido
                            if (rx_mac != MAC_ADDRESS)
                            {
                                ESP_LOGI(LORA_TAG,"Sync ignorado, MAC não correspondente %08X",rx_mac);
                                vest->startReceive();

                                rxTime = millis();
                                current_state = RECEIVE_WINDOW;
                                break;
                            }

                            ESP_LOGI(LORA_TAG, "SYNC recebido! Alvo MAC: %08X", rx_mac);

                            assignedVestID = rx_vestID;
                            assignedNet = buffer[1] & 0x07; 
                            dataCH = (buffer[6] >> 5) & 0x07;
                            controlCH = (buffer[6] >> 1) & 0x07;
                            tdmaBaseTime = current_time; 
                            missed_sync_cycles = 0;  
                            
                            // Responder ao Dashboard
                            uint8_t ack_buf[2];
                            ack_buf[0] = ((assignedNet & 0x07) << 5) | (assignedVestID & 0x1F);
                            ack_buf[1] = ((OP_ACK & 0x0F) << 4) | (funct_ack_sync & 0x0F);
                            
                            // Muda para o canal de controlo
                            switchToCtrlChannel();

                            // Esperar 150ms antes de transmitir 
                            vTaskDelay(pdMS_TO_TICKS(150));

                            // Encriptar e Transmitir
                            cryptAESCTR(ack_buf, 2);
                            vest->transmit(ack_buf, 2);

                            ESP_LOGI(LORA_TAG, "RX len=%d", len);
                            ESP_LOGI(LORA_TAG, "DEC: %02X %02X %02X %02X %02X %02X %02X",
                                    buffer[0], buffer[1], buffer[2], buffer[3], buffer[4], buffer[5], buffer[6]);

                            ESP_LOGI(LORA_TAG, "SYNC parsed: net=%d vest=%d opcode=0x%X targetNet=%d dataCH=%d ctrlCH=%d",
                                    rx_net,
                                    rx_vestID,
                                    rx_opcode,
                                    buffer[1] & 0x07,
                                    (buffer[6] >> 5) & 0x07,
                                    (buffer[6] >> 1) & 0x07);                            

                            tdmaBaseTime = millis();
                            vTaskDelay(pdMS_TO_TICKS(40));
                            current_state = SEND_DATA;
                            break;
                        }
                        else if (len == 2 && rx_opcode  == OP_ACK) {
                            if (rx_opcode == OP_ACK && rx_net == assignedNet && rx_vestID == assignedVestID) {
                                ESP_LOGI(LORA_TAG, "ACK de Alerta Recebido!");
                                waiting_critical_ack = false; 
                                critical_alert_retries = 0; 
                            }
                        }
                        // Processar o pacote de TeamHope e mudar de canal
                        else if (len == 3 && rx_opcode == OP_NETALIVE && rx_funct == funct_teamhop) {
                            if (rx_net == assignedNet || rx_net == 0) {
                                dataCH = (buffer[2] >> 5) & 0x07;
                                controlCH = (buffer[2] >> 2) & 0x07;
                                ESP_LOGI(LORA_TAG, "TEAM HOP: Mudamos para DataCH %d, CtrlCH %d",dataCH, controlCH);
                                switchToCtrlChannel();
                                vest->startReceive();
                                current_state = RECEIVE_WINDOW;
                            }
                        }
                        // Processar o pacote de UNSYNC e sair da rede
                        else if (len == 2 && rx_opcode == OP_UNSYNC && rx_vestID == assignedVestID) {
                            ESP_LOGI(LORA_TAG, "Fomos removidos da rede (UNSYNC)");
                            // Remove as variáveis conseguidas através do sync
                            assignedVestID = -1;
                            assignedNet = -1;
                            first_packet_sent = false;
                            vest->setFrequency(FREQ_CH[0]);
                            current_state = WAIT_SYNC;
                            break;
                        }
                    }
                }
                // Volta a escutar caso não seja para sair
                vest->startReceive();
                if (assignedVestID == -1) {
                    rxTime = millis();
                    current_state = RECEIVE_WINDOW; // Volta instantaneamente a ouvir
                } else {
                    // Se já estiver emparelhada, segue a vida normal
                    current_state = (rx_origin == SYNC_STATE) ? WAIT_SYNC : SEND_DATA;
                }
                break;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(5));  // Evita Watchdog de dar reset
    }
}