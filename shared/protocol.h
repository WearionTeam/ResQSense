#ifndef PROTOCOL_H
#define PROTOCOL_H

#include <Arduino.h>

/**
 * @brief Definição das configurações para a modulação LoRa
 * 
 */
const float FREQ_CH[] = {868.0f, 868.25f, 868.5f, 868.75f, 869.0f, 869.25f, 869.5f, 870.0f};
#define BAND        125E3           ///< Largura de Banda do sinal
#define SF          7               ///< Spread Factor
#define CR          5               ///< Coding Rate
#define SYNC_WORD   0xA5            ///< SYNC WORD
#define TX_POWER    14              ///< Power da antena dBm
#define PREAMBLE    8               ///< 
/**
 * @brief Janelas de tempo usadas em ambos os lados da rede
 * 
 */
#define SLOT_TIME               500             ///< Janela de tempo que cada colete fica
#define TDMA_CYCLE_MS           10000           ///< Tempo de ciclo de cada colete
#define SYNC_INTERVAL           4000            ///< Intervalo de tempo para ativar a janela de tempo para sync
#define RX_WINDOW_SYNC_INTERVAL 1000            ///< Janela de receive de pacotes sync
#define RX_WINDOW_DATA_INTERVAL 100             ///< Janela de receive de pacotes de controlo após cada pacote enviado
#define FULL_PACKET_INTERVAL    60000           ///< Intervalo de tempo para mandar um pacote de telemtria completa
/**
 * @brief Configuração dos Op Codes usados na rede
 * 
 */
enum OpCode : uint8_t { 
    OP_INVALID  = 0b0000,
    OP_SYNC     = 0b0011,
    OP_UNSYNC   = 0b1100,
    OP_RETRY    = 0b0110,
    OP_NETALIVE = 0b0101,
    OP_ACK      = 0b1001,
    OP_QALERT   = 0b1111,
    OP_VTALERT  = 0b1010
};
/**
 * @brief Valor da função inválida
 * 
 */
enum FUNCT_INVALID : uint8_t {
    funct_ignore = 0b0000               ///< Função de ignorar
};
/**
 * @brief Valor do campo funct num pacote de controlo unsync
 * 
 */
enum FUNCT_UNSYNC : uint8_t {
    funct_central_off = 0b1000,         ///< Função de aviso que a central está desligar
    funct_man_exp = 0b0100,             ///< Função de expulsão manual
    funct_end_session = 0b0010          ///< Função de fim de operação
};
/**
 * @brief Valor do campo funct num pacote de controlo retry
 * 
 */
enum FUNCT_RETRY : uint8_t {
    funct_retry_cpacket = 0b1000,       ///< Função de pedir a trama completa
    funct_retry_deltapacket = 0b0100    ///< Função de pedir uma trama leve
};
/**
 * @brief Valor do campo funct num pacote de controlo NETALIVE
 * 
 */
enum FUNCT_NETALIVE : uint8_t { 
    funct_beacon = 0b0000,              ///< Função de beacon de vida global -> ???
    funct_concedehop = 0b1110,          ///< Função de dar CONCEDE HOP
    funct_teamhop = 0b1111              ///< Função de dar TEAM HOP
};
/**
 * @brief Valor do campo funct num pacote de controlo ACK
 * 
 */
enum FUNCT_ACK : uint8_t {
    funct_ack_data_alert = 0b1000,      ///< Função para confirmar um data alert
    funct_ack_alert = 0b0100,           ///< Função para confirmar um alerta
    funct_ack_sync = 0b0010             ///< Função que confirma a sync
};
/**
 * @brief Valor do campo funct num pacote de controlo Quick Alert
 * 
 */
enum FUNCT_QALERT : uint8_t {
    funct_qa_sos = 0b1000,              ///< Alerta de SOS
    funct_qa_md = 0b0100,               ///< Alerta de Man Down
    funct_qa_bc = 0b0010,               ///< Alerta de Bateria Crítica 
    funct_qa_hwr = 0b0001               ///< Alerta de Falha de Hardware
};
/**
 * @brief Valor do campo funct num pacote de controlo Alerta dos sensores vitais
 * 
 */
enum FUNCT_VALERT : uint8_t {
    funct_va_hbpm = 0b1001,             ///< Alerta de BPM alto
    funct_va_lbpm = 0b1000,             ///< Alerta de BPM baixo
    funct_va_htemp = 0b0101,            ///< Alerta de Temperatura alta
    funct_va_ltemp = 0b0100,            ///< Alerta de Temperatura baixa     
    funct_va_lspo2 = 0b0010,            ///< Alerta de SpO2 baixo
};
/**
 * @brief Identificador base de cabeçalho do protocolo da ResQSense
 * 
 * @return typedef struct 
 */
typedef struct __attribute__((packed)){
    uint16_t net : 3;                   ///< Net ou canl de onde vem  
    uint16_t vestID : 5;                ///< ID do colete 
} ResQSenseHeader;
/**
 * @brief Flgas de controlo e indicadores de estado de playload
 * 
 * @return typedef struct 
 */
typedef struct __attribute__((packed)) {
    uint8_t T : 1;                      ///< Tipo de playload, 
    uint8_t S : 1;                      ///<
    uint8_t M : 1;                      ///<
    uint8_t V : 1;                      ///<
    uint8_t B : 1;                      ///<
    uint8_t H : 1;                      ///<
    uint8_t P : 1;                      ///<
    uint8_t F : 1;                      ///<
} ControlFlags;
/**
 * @brief Pacote de sincronização tablet -> colete
 * 
 * @return typedef struct 
 */
typedef struct __attribute__((packed)){
    ResQSenseHeader header;             ///< Bits 0-7           NET e VestID
    uint8_t opcode : 4;                 ///< Bits 8-11
    uint8_t R : 1;                      ///< Bits 12
    uint8_t t_netID : 3;                ///< Bits 13-15
    uint32_t mac_addr;                  ///< Bits 16-47
    uint8_t data_ch : 3;                ///< Bits 48-50
    uint8_t p1 : 1;                     ///< Bits 51
    uint8_t ctrl_ch : 3;                ///< Bits 52-54
    uint8_t p2 : 1;                     ///< Bits 55
} SyncPacket;
/**
 * @brief Pacote de telemetria diferencial
 * 
 * @return typedef struct 
 */
typedef struct __attribute__((packed)) {
    ResQSenseHeader header;             ///< Bits 0-7           NET e VestID
    ControlFlags flags;                 ///< Bits 8-15          Frame de Flags
    int16_t latDelta;                   ///< 2 Bytes (16-31)    Variacao de latitude
    int16_t longDelta;                  ///< 2 Bytes (32-47)    Variacao de Longitude
    int8_t altDelta;                    ///< 1 Byte  (48-55)    Variacao de Altitude
} DeltaPositionPacket;
/**
 * @brief Pacote completo de telemetria e sinais vitais
 * 
 * @return typedef struct 
 */
typedef struct __attribute__((packed)){
    ResQSenseHeader header;             ///< Bits 0-7           NET e VestID
    ControlFlags flags;                 ///< Bits 8-15          Frame de Flags
    int32_t lat;                        ///< 4 Bytes (16-47)    Latitude Absoluta
    int32_t lon;                        ///< 4 Bytes (48-79)    Longitude Absoluta
    int16_t alt;                        ///< 2 Byte  (80-95)    Altitude Absoluta 
    uint8_t  BPM;                       ///< 1 Byte  (96-103)   Batimentos Cardíacos
    uint16_t SPO2 : 5;                  ///< Bits 104-108       SpO2 no Sangue
    uint16_t temp : 11;                 ///< Bits 109-119       Temperatura * 10
} FullPacket;
/**
 * @brief Estrutura genérica do pacote de controlo
 * 
 * @return typedef struct 
 */
typedef struct __attribute__((packed)){
    ResQSenseHeader header;             ///< Bits 0-7           NET e VestID
    uint8_t opcode : 4;                 ///< Bits 8-11          OpCode -> Comando
    uint8_t funct : 4;                  ///< Bits 12-15         Funcao do Comando
} ControlPacket;    
/**
 * @brief Pacote de intrução de mudança de canal/frequência para a equipa 
 * 
 * @return typedef struct 
 */
typedef struct __attribute__((packed)){
    ControlPacket header;               ///< Bits 0-15          Packet de Controlo
    uint8_t data_ch : 3;                ///< Bits 16-18         Novo canal de dados
    uint8_t ctrl_ch : 3;                ///< Bits 19-21         Novo canal de controlo
    uint8_t pad : 2;                    ///< Bits 22-23         Bits de padding 
} TeamHop;


#endif
