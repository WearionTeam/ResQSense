/**
 * @file protocol.h
 * @brief Definição do protocolo binário de comunicação ResQSense sobre LoRa.
 * @details Contém as constantes de rádio, temporizações TDMA, códigos de operação (OpCodes)
 * e as estruturas de pacotes compactadas ao nível do bit para máxima eficiência de Airtime.
 * @version 1.0
 * @date 2026
 * * @copyright Copyright (c) 2026
 */
#ifndef PROTOCOL_H
#define PROTOCOL_H

#include <Arduino.h>

/**
 * @brief Definição das configurações para a modulação LoRa
 * 
 */
const float FREQ_CH[] = {868.1f, 867.1f, 868.3f, 867.3f, 867.5f, 867.7f, 867.9f, 868.5f};
#define BAND        125.0           ///< Largura de Banda do sinal
#define SF          7               ///< Spread Factor
#define CR          5               ///< Coding Rate
#define SYNC_WORD   0x12            ///< SYNC WORD
#define TX_POWER    14              ///< Power da antena dBm
#define PREAMBLE    8               ///< Tamanho do preâmbulo em símbolos rádio
/**
 * @brief Janelas de tempo usadas em ambos os lados da rede
 * 
 */
#define SLOT_TIME               333             ///< Janela de tempo que cada colete fica
#define TDMA_CYCLE_MS           10000           ///< Tempo de ciclo de cada colete
#define RX_WINDOW_SYNC_INTERVAL 10000           ///< Janela de receive de pacotes sync
#define RX_WINDOW_DATA_INTERVAL 200             ///< Janela de receive de pacotes de controlo após cada pacote enviado
#define FULL_PACKET_INTERVAL    60000           ///< Intervalo de tempo para mandar um pacote de telemtria completa
#define SYNC_INTERVAL           5000

/** @brief Endereço MAC */
static const uint32_t MAC_ADDRESS = 0x11887766;

/** @brief Estados internos da máquina de operação de rede LoRa do colete. */
enum NetworkState {
    WAIT_SYNC,       /**< Aguarda o beacon de emparelhamento inicial da gateway */
    SEND_DATA,       /**< Aguarda e processa o envio da telemetria no slot TDMA correto */
    SEND_ALERT,      /**< Estado prioritário para disparo de mensagens de emergência */
    RECEIVE_WINDOW,  /**< Mantém o rádio em escuta ativa por um tempo controlado */
    PROCESS_PACKET,  /**< Desencripta e analisa um pacote válido apanhado pela antena */
    END_SESSION      /**< Termina a sessão de monitorização atual do operador */
};

/** @brief Contexto de origem que originou a abertura da janela de receção. */
enum RX_ORIGIN {
    SYNC_STATE,      /**< A janela abriu a aguardar um ciclo de sincronismo global */
    SEND_STATE       /**< A janela abriu logo após o colete submeter dados (à espera de ACK) */
};

/**
 * @brief Configuração dos Op Codes usados na rede
 * @note Ocupam os 4 bits mais significativos (MSB) do segundo byte de controlo.
 */
enum OpCode : uint8_t { 
    OP_INVALID  = 0b0000, ///< Operação inválida / Pacote corrompido
    OP_SYNC     = 0b0011, ///< Solicitação/Resposta de Sincronismo da Gateway
    OP_UNSYNC   = 0b1100, ///< Comando de expulsão ou desconexão da rede
    OP_RETRY    = 0b0110, ///< Pedido de retransmissão devido a perda de tramas
    OP_NETALIVE = 0b0101, ///< Comando Keep-Alive ou instruções de salto de frequência
    OP_ACK      = 0b1001, ///< Confirmação de receção de alertas críticos
    OP_QALERT   = 0b1111, ///< Alerta Rápido / Emergência Instantânea (SOS, Queda)
    OP_VTALERT  = 0b1010  ///< Alerta de Sensores Vitais fora de gama (BPM, SpO2)
};


enum FUNCT_INVALID : uint8_t {
    funct_ignore = 0b0000               ///< Instrução para ignorar payload
};

enum FUNCT_UNSYNC : uint8_t {
    funct_central_off = 0b1000,         ///< A central/gateway vai desligar o sistema
    funct_man_exp = 0b0100,             ///< O operador da central expulsou este colete manualmente
    funct_end_session = 0b0010          ///< Fim programado da operação de resgate
};

enum FUNCT_RETRY : uint8_t {
    funct_retry_cpacket = 0b1000,       ///< Central solicita o reenvio da trama completa (Full Packet)
    funct_retry_deltapacket = 0b0100    ///< Central solicita o reenvio da trama leve (Delta Packet)
};

enum FUNCT_NETALIVE : uint8_t { 
    funct_beacon = 0b0000,              ///< Beacon de presença global de rede
    funct_concedehop = 0b1110,          ///< Gateway concede autorização para troca de canal
    funct_teamhop = 0b1111              ///< Ordem imperativa para toda a equipa saltar de canais (Data e Ctrl)
};

enum FUNCT_ACK : uint8_t {
    funct_ack_data_alert = 0b1000,      ///< Confirmação de receção de um alerta misto com dados
    funct_ack_alert = 0b0100,           ///< Confirmação de receção de um alerta de emergência genérico
    funct_ack_sync = 0b0010             ///< Confirmação enviada pelo colete a validar o emparelhamento
};

enum FUNCT_QALERT : uint8_t {
    funct_qa_sos = 0b1000,              ///< Alerta de SOS acionado manualmente pelo botão de pânico
    funct_qa_md = 0b0100,               ///< Alerta automático de Homem Caído (Man Down) detetado pelo IMU
    funct_qa_bc = 0b0010,               ///< Alerta automático de Bateria Crítica no colete
    funct_qa_hwr = 0b0001               ///< Alerta de Falha Crítica de Hardware (ex: sensor I2C desligado)
};

enum FUNCT_VALERT : uint8_t {
    funct_va_hbpm = 0b1001,             ///< Ritmo cardíaco detetado acima do limite máximo seguro (Taquicardia)
    funct_va_lbpm = 0b1000,             ///< Ritmo cardíaco detetado abaixo do limite mínimo seguro (Bradicardia)
    funct_va_htemp = 0b0101,            ///< Temperatura corporal perigosamente elevada (Hipertermia / Febre)
    funct_va_ltemp = 0b0100,            ///< Temperatura corporal perigosamente baixa (Hipotermia)
    funct_va_lspo2 = 0b0010,            ///< Saturação de Oxigénio no sangue abaixo do aceitável (Hipóxia)
};

/**
 * @brief Identificador de cabeçalho (Byte 0) em qualquer pacote da rede.
 * @note Total: 8 bits (1 Byte). Alinhado perfeitamente para poupança rádio.
 */
typedef struct __attribute__((packed)){
    uint8_t net : 3;                   ///< ID da rede/equipa (0 a 7)
    uint8_t vestID : 5;                ///< ID único do colete na equipa (1 a 31)
} ResQSenseHeader;
/**
 * @brief Flags de Controlo e indicadores rápidos de estado de saúde do operador.
 * @note Total: 8 bits (1 Byte). Mapeado diretamente no `flag_byte` de telemetria.
 */
typedef struct __attribute__((packed)) {
    uint8_t T : 1;                      ///< Tipo de Payload: 1 = Trama Completa (Full), 0 = Diferencial (Delta)
    uint8_t S : 1;                      ///< Estado do Botão SOS (1 = Premido, 0 = Normal)
    uint8_t M : 1;                      ///< Estado do Algoritmo Man-Down (1 = Queda detetada, 0 = OK)
    uint8_t V : 1;                      ///< Estado dos Sinais Vitais (1 = Alerta biométrico ativo, 0 = OK)
    uint8_t B : 1;                      ///< Flag de Bateria (1 = Baixa/Crítica, 0 = OK)
    uint8_t H : 1;                      ///< Estado de Saúde do Hardware (1 = Sensor em falha, 0 = Tudo operacional)
    uint8_t P : 1;                      ///< Flag de Precisão de Posição (1 = GNSS sem sinal/No-Fix, 0 = GNSS OK)
    uint8_t F : 1;                      ///< Flag de Atividade do Sistema (Sempre ativa em 1 para validação de ruído)
} ControlFlags;

/**
 * @brief Estrutura do Pacote de Sincronização (Gateway -> Colete).
 * @note Total: 7 Bytes. Enviado em Broadcast para emparelhar equipamentos.
 */
typedef struct __attribute__((packed)){
    ResQSenseHeader header;             ///< Byte 0: NETID e VestID atribuídos
    uint8_t opcode : 4;                 ///< Byte 1 (Bits 4-7): Deve ser OP_SYNC
    uint8_t R : 1;                      ///< Byte 1 (Bit 3): Bit Reservado
    uint8_t t_netID : 3;                ///< Byte 1 (Bits 0-2): Target Network ID
    uint32_t mac_addr;                  ///< Bytes 2-5: Endereço MAC único do colete visado
    uint8_t data_ch : 3;                ///< Byte 6 (Bits 5-7): Canal atribuído para envio de dados
    uint8_t p1 : 1;                     ///< Byte 6 (Bit 4): Bit de Padding para alinhamento
    uint8_t ctrl_ch : 3;                ///< Byte 6 (Bits 1-3): Canal de controlo para escuta e emergências
    uint8_t p2 : 1;                     ///< Byte 6 (Bit 0): Bit de Padding final
} SyncPacket;

/**
 * @brief Estrutura do Pacote de Posição Diferencial (Delta Packet).
 * @note Total: 7 Bytes. Usado nos ciclos intermédios do TDMA para poupar Airtime.
 * Transmite variações matemáticas de distância em vez de coordenadas absolutas longas.
 */
typedef struct __attribute__((packed)) {
    ResQSenseHeader header;             ///< Byte 0: NET e VestID do colete emissor
    ControlFlags flags;                 ///< Byte 1: Registo de flags de aviso rápido
    int16_t latDelta;                   ///< Bytes 2-3: Variação fracionária comprimida de Latitude
    int16_t longDelta;                  ///< Bytes 4-5: Variação fracionária comprimida de Longitude
    int8_t altDelta;                    ///< Byte 6: Variação direta de Altitude relativa em metros
} DeltaPositionPacket;

/**
 * @brief Estrutura do Pacote Completo de Telemetria e Sinais Vitais (Full Packet).
 * @note Total: 15 Bytes. Submetido a cada 60 segundos ou no arranque da sessão.
 */
typedef struct __attribute__((packed)){
    ResQSenseHeader header;             ///< Byte 0: NET e VestID do colete emissor
    ControlFlags flags;                 ///< Byte 1: Registo de flags de estado
    int32_t lat;                        ///< Bytes 2-5: Coordenada Latitude Absoluta (Multiplicada por 10^7)
    int32_t lon;                        ///< Bytes 6-9: Coordenada Longitude Absoluta (Multiplicada por 10^7)
    int16_t alt;                        ///< Bytes 10-11: Altitude Absoluta em metros em relação ao nível do mar
    uint8_t  BPM;                       ///< Byte 12: Batimentos Cardíacos por minuto diretos (0 a 255)
    uint16_t SPO2 : 5;                  ///< Byte 13 (Bits 11-15): Oxigénio comprimido (Valor real - 69 para caber em 5 bits)
    uint16_t temp : 11;                 ///< Bytes 13/14 (Bits 0-10): Temperatura corporal condensada (Offset * 100)
} FullPacket;

/**
 * @brief Estrutura genérica básica para comandos rápidos de controlo na rede.
 * @note Total: 2 Bytes. Usada para comandos simples bidirecionais (ACK, UNSYNC, etc).
 */
typedef struct __attribute__((packed)){
    ResQSenseHeader header;             ///< Byte 0: Identificação de ID de Nó e Rede
    uint8_t opcode : 4;                 ///< Byte 1 (Bits 4-7): Código de Comando Principal
    uint8_t funct : 4;                  ///< Byte 1 (Bits 0-3): Especificação da Ação da Função
} ControlPacket;    

/**
 * @brief Estrutura dedicada para a emissão de alertas biométricos em tempo real.
 * @note Total: 4 Bytes. Transporta o valor numérico que gerou o gatilho de emergência na central.
 */
typedef struct __attribute__((packed)) {
    ResQSenseHeader header;             ///< Byte 0: Identificação de ID de Nó e Rede
    uint8_t funct  : 4;                 ///< Byte 1 (Bits 4-7): Sub-código do Vital em Alerta (ex: Hipóxia)
    uint8_t opcode : 4;                 ///< Byte 1 (Bits 0-3): Deve ser OP_VTALERT
    uint16_t value;                     ///< Bytes 2-3: Valor numérico em bruto lido pelo sensor (Big-Endian)
} VitalAlertPacket;

/**
 * @brief Estrutura do Pacote de Instrução de Team Hop (Salto de Frequência em Grupo).
 * @note Total: 3 Bytes. Enviado pela Gateway em situações de interferência severa ou jamming de rádio.
 */
typedef struct __attribute__((packed)){
    ControlPacket header;               ///< Bytes 0-1: Cabeçalho base de comando (OP_NETALIVE + funct_teamhop)
    uint8_t data_ch : 3;                ///< Byte 2 (Bits 5-7): Novo índice do canal rádio para telemetria de dados
    uint8_t ctrl_ch : 3;                ///< Byte 2 (Bits 2-4): Novo índice do canal rádio para pacotes de controlo
    uint8_t pad : 2;                    ///< Byte 2 (Bits 0-1): Bits de preenchimento nulos para fechar o byte
} TeamHop;


#endif
