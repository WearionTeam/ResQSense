/**
 * @file sensors.h
 * @brief Biblioteca das funçoes usadas com os sensores
 * @version 1.0
 * @date 2026
 */

#ifndef SENSORS_H
#define SENSORS_H

#include <Arduino.h>
#include <Wire.h>
#include <math.h>

#include "config.h"
#include "DFRobot_GNSSAndRTC.h"
#include "DFRobot_GNSS.h"
#include <Adafruit_TMP117.h>
#include <Adafruit_Sensor.h>
#include <DFRobot_BloodOxygen_S.h>

/**
 * @brief Estados do algoritmo Pedestrian Dead Reckoning (PDR).
 * @details Classifica o tipo de atividade/deslocamento atual do utilizador.
 */
typedef enum {
    PDR_STATIC = 0, /**< Utilizador parado ou imóvel */
    PDR_RUN,        /**< Utilizador a correr */
    PDR_WALK        /**< Utilizador a caminhar */
} pdr_state_t;

/**
 * @brief Estrutura que armazena raw data de todos os sensores.
 */
typedef struct 
{
    uint32_t timestamp_ms;    /**< Hora da leitura em milissegundos */
    bool gnss_updated;        /**< Flag indicativa do uptade nas coordenadas */
    bool ppg_updated;         /**< Flag indicativa do update dos dados do ppg */
    bool temp_updated;        /**< Flag indicativa do update dos dados da temperatura */
    float ax, ay, az;         /**< Aceleração nos eixos X, Y, Z (g ou m/s²) */
    float gx, gy, gz;         /**< Velocidade nos eixos X, Y, Z (dps ou rad/s) */
    double lat, lon, alt;     /**< Latitude, Longitude e Altitude */
    int hr, spo2;             /**< Ritmo Cardíaco (hr) e Saturação de Oxigénio (%) */
    float raw_temps[3];       /**< Array com as temperaturas lidas dos sensores TMP117 */
    bool validMask[3];        /**< Máscara de validação individual para cada sensor de temperatura */
} sensors_data;

/**
 * @brief Estrutura com os dados processados, filtrados e para empacotamento/transmissão.
 */
typedef struct
{
    uint32_t timestamp_ms;    /**< Hora do processamento em milissegundos */
    float mag_a;              /**< Magnitude do vetor de aceleração calculada */
    double lat, lon, alt;     /**< Coordenadas geográficas validadas e filtradas */
    float temp;               /**< Média da Temperatura corporal */
    int hr, spo2;             /**< Sinais vitais  */
    bool fall_detected;       /**< Alerta ativo se for detetada uma queda do utilizador (Man-down) */
    bool sos_alert;           /**< Alerta ativo se o botão de pânico/SOS manual for premido */
    bool temp_alert;          /**< Alerta ativo se a temperatura ultrapassar os limites de segurança */
    bool ppg_alert;           /**< Alerta ativo se os vitais (HR/SpO2) estiverem fora da gama normal */
} processed_data;

/**
 * @brief Nó de controlo individual para um sensor de temperatura TMP117.
 */
struct SensorNode {
    Adafruit_TMP117* dev;     /**< Ponteiro para a instância do sensor Adafruit */
    uint8_t addr;             /**< Endereço I2C do sensor */
    bool ok;                  /**< Estado de saúde do sensor (true se estiver a comunicar bem) */
    float lastTemp;           /**< Último valor de temperatura registado com sucesso */
};

/**
 * @brief Configura os registos internos e escalas do IMU (ex: MPU-6050).
 */
void startIMU();

/**
 * @brief Inicializa e configura o módulo GNSS no barramento I2C.
 * @param gps Referência para o objeto do GNSS.
 */
void startGNSS(DFRobot_GNSSAndRTC_I2C &gps);

/**
 * @brief Inicializa o hardware do sensor biométrico (Oximetria e HR).
 * @param sen Referência para o objeto do sensor de oxigénio no sangue.
 */
void initVitalsHardware(DFRobot_BloodOxygen_S_I2C &sen);

/**
 * @brief Inicializa a comunicação com um nó específico de sensor TMP117.
 * @param s Referência para a estrutura de configuração do nó de temperatura.
 * @return true se o sensor foi detetado com sucesso, false caso contrário.
 */
bool initTMP117(SensorNode &s);

/**
 * @brief Executa uma rotina de Watchdog em todos os sensores para detetar bloqueios.
 * @note Utilizada para recuperar sensores que possam ter falhado no barramento I2C.
 */
void kickAllSensors();

/**
 * @brief Efetua a leitura por polling dos dados do acelerómetro e giroscópio do IMU.
 * @param component Referência para a estrutura onde serão guardados os dados brutos.
 */
void readIMU(sensors_data &component);

/**
 * @brief Lê  NMEA do módulo GNSS e extrai a localização.
 * @param component Referência para a estrutura onde será guardada a localização
 * @param gps Referência para o objeto do GNSS.
 */
void readGNSS(sensors_data &component, DFRobot_GNSSAndRTC_I2C &gps);

/**
 * @brief Lê os dados de biometria (PPG) a partir do sensor de oximetria.
 * @param sen Referência para o objeto do sensor de oxigénio no sangue.
 * @param component Referência para a estrutura onde serão guardados os dados brutos.
 */
void readPPG(DFRobot_BloodOxygen_S_I2C &sen, sensors_data &component);

/**
 * @brief Lê sequencialmente o array de sensores de temperatura TMP117 disponíveis.
 * @param temps Array de saída onde serão guardadas as leituras de temperatura.
 * @param validMask Máscara de saída indicando quais os índices do array que contêm leituras válidas.
 * @param now Timestamp atual do sistema em milissegundos.
 */
void readTemperatureSensors(float temps[3], bool validMask[3], unsigned long now);

/**
 * @brief Função principal de Fusão de Sensores.
 * @details Consolida os dados em bruto de `measurements`, aplica filtros e atualiza a estrutura `info`.
 * @param measurements Estrutura com as últimas leituras brutas dos sensores.
 * @param info Estrutura de saída com os dados tratados e prontos para envio.
 */
void processSensorFusion(sensors_data &measurements, processed_data &info);

/**
 * @brief Algoritmo Man-Down para deteção de quedas do operador.
 * @details Avalia as componentes de aceleração para identificar picos de impacto seguidos de imobilidade.
 * @param component Estrutura onde a flag `fall_detected` será atualizada em caso de evento crítico.
 * @param az Aceleração medida no eixo Z.
 * @param ax Aceleração medida no eixo X.
 */
void mandown(processed_data &component, float az, float ax);

/**
 * @brief Algoritmo de Pedestrian Dead Reckoning (PDR).
 * @details Estima o deslocamento ou tipo de passada com base na magnitude da aceleração.
 * @param magnitude Magnitude do vetor de aceleração
 */
void pdr(float magnitude);

/**
 * @brief Processa e valida as leituras biométricas acumuladas.
 * @details Aplica filtros.
 * @param component Estrutura com os dados brutos de PPG.
 * @param info Estrutura de saída com os dados tratados e prontos para envio.
 */
void processPPG(sensors_data &component, processed_data &info);

/**
 * @brief Processa e consolida as múltiplas leituras de temperatura.
 * @details Trata erros de leitura através da máscara e decide o valor final de temperatura a reportar.
 * @param temps Array com as temperaturas lidas.
 * @param validMask Máscara que indica quais as temperaturas que são de confiança.
 * @param now Timestamp atual do sistema em milissegundos.
 * @param info Estrutura de saída com os dados tratados e prontos para envio.
 */
void processTemperatureData(float temps[3], bool validMask[3], unsigned long now, processed_data &info);

/**
 * @brief Verifica se o utilizador acionou o botão físico ou comando de SOS manual.
 * @param info Estrutura onde a flag `sos_alert` será atualizada.
 */
void checkSOS(processed_data &info);

/**
 * @brief Coloca temporariamente um sensor de temperatura em "Blacklist" após consecutivas falhas.
 * @param idx Índice do sensor a ignorar (0 a 2).
 * @param now Timestamp atual do sistema em milissegundos.
 */
void ignoreSensor(int idx, unsigned long now); 

/**
 * @brief Reinicia as variáveis de estado do algoritmo de navegação.
 * @details Útil para limpezas de buffers de posição após perdas de fix de satélites prolongadas.
 */
void resetNavigationState();

/**
 * @brief Escreve um valor de 16 bits num registo I2C de um dispositivo.
 * @param a Endereço I2C do dispositivo.
 * @param reg Registo interno para escrita.
 * @param v Valor de 16 bits a enviar.
 * @return true se a escrita teve sucesso, false em caso de erro no barramento.
 */
static bool writeReg16(uint8_t a, uint8_t reg, uint16_t v);

/**
 * @brief Lê um valor de 16 bits de um registo I2C de um dispositivo.
 * @param a Endereço I2C do dispositivo.
 * @param reg Registo interno para leitura.
 * @param[out] v Referência onde será guardado o valor lido de 16 bits.
 * @return true se a leitura teve sucesso, false em caso de erro no barramento.
 */
static bool readReg16(uint8_t a, uint8_t reg, uint16_t &v);

#endif // SENSORS_H