/**
 * @file config.h
 * @brief Ficheiro central de configurações de hardware e limites de segurança.
 * @details Contém o mapeamento de pinos (Pinout) para diferentes versões do ESP32,
 * endereços I2C dos periféricos, offsets de calibração do IMU e os limiares 
 * críticos (Thresholds) para acionamento de alarmes vitais e de queda.
 * @version 1.0
 * @date 2026
 * * @copyright Copyright (c) 2026
 */

#ifndef CONFIG_H
#define CONFIG_H

#include <Arduino.h>

/**
 * @brief Configuração de pinos
 *
 */

#define SPI_MISO    13      ///< Pino MISO para o barramento SPI (LoRa)
#define SPI_MOSI    11      ///< Pino MOSI para o barramento SPI (LoRa)
#define SPI_SCLK    12      ///< Pino Clock para o barramento SPI (LoRa)
#define SPI_NSS     10      ///< Pino Chip Select (CS/NSS) do módulo LoRa
#define DIO0        2       ///< Pino de interrupção (RX/TX Done) do módulo LoRa
#define NRESET      14      ///< Pino de hardware reset do módulo LoRa
#define I2C_SDA     4       ///< Pino de dados do barramento I2C (Sensores)
#define I2C_SCL     5       ///< Pino de clock do barramento I2C (Sensores)
#define ISR_PIN     9       ///< Pino genérico para interrupções externas de hardware
#define FSR_PIN     6       ///< Pino ADC para o sensor de força (Botão SOS)

/**
 * @brief Endereços hexadecimais nativos (ou configurados por hardware) dos sensores ResQSense.
 */
#define IMU_ADDR     0x68                   ///< Endereço do IMU (MPU-6050)
#define TMP_ADDR     0x48                   ///< Endereço do Sensor de Temperatura 1 (TMP117 Base)
#define TMPA_ADDR    0x4A                   ///< Endereço do Sensor de Temperatura 2 (TMP117 A)
#define TMPB_ADDR    0x4B                   ///< Endereço do Sensor de Temperatura 3 (TMP117 B)
#define MAX_ADDR     0x57                   ///< Endereço do Sensor Biométrico (MAX30102 Oximetria)
#define GNSS_ADDR    0x66                   ///< Endereço do Módulo de Localização GNSS

/**
 * @brief Constantes para conversão de valores RAW de 16-bits para unidades SI (g e º/s).
 */
#define IMU_ACCEL_SENSIVITY 2048.0f         ///< Fator de escala do Acelerómetro (16g = 2048 LSB/g)
#define IMU_GYRO_SENSIVITY  16.4f           ///< Fator de escala do Giroscópio (2000º/s = 16.4 LSB/º/s)
#define IMU_OFFSET_ACCEL_X  -372            ///< Offset estático de calibração para o eixo X (Aceleração)
#define IMU_OFFSET_ACCEL_Y  14              ///< Offset estático de calibração para o eixo Y (Aceleração)
#define IMU_OFFSET_ACCEL_Z  12              ///< Offset estático de calibração para o eixo Z (Aceleração)
#define IMU_OFFSET_GYRO_X   -42             ///< Offset estático de calibração para o eixo X (Giroscópio)
#define IMU_OFFSET_GYRO_Y   -8              ///< Offset estático de calibração para o eixo Y (Giroscópio)
#define IMU_OFFSET_GYRO_Z   -1              ///< Offset estático de calibração para o eixo Z (Giroscópio)
#define GRAVITY             9.81f           ///< Constante de aceleração gravítica na Terra (m/s²)

/**
 * @brief Limiares de Força e janelas de tempo para identificação de padrões de queda.
 * @note Os nomes das macros mantêm a nomenclatura original de implementação.
 */
#define TRESHOLD_FALL       0.52f           ///< Limiar máximo para deteção de Free-Fall (Aceleração < 0.52G)
#define TRESHOLD_IMPACT     3.66f           ///< Limiar mínimo para deteção de choque brusco no solo (> 3.66G)
#define TRESHOLD_GRAVITY    0.10f           ///< Margem de erro estática para avaliar imobilidade (orientação final)
#define FALL_TIMEOUT        3000            ///< Tempo limite para o impacto ocorrer após o free-fall (em ms)
#define INACTIVITY_TIME     1500            ///< Tempo em que a vítima deve permanecer imóvel para confirmar Man-Down (em ms)

/**
 * @brief Parâmetros de funcionamento e gatilhos de segurança para monitorização corporal.
 */
#define TEMP_SAMPLE_TIME_MS    10000UL      ///< Intervalo padrão de leitura dos sensores de temperatura (10s)
#define TEMP_MIN               2.0f         ///< Limite Crítico Inferior para despoletar Alerta de Hipotermia/Ambiente (ºC)
#define TEMP_MAX               39.0f        ///< Limite Crítico Superior para despoletar Alerta de Febre/Exaustão (ºC)
#define IGNORE_SENSOR_INTERVAL 180000UL     ///< Tempo de Quarentena em que um sensor avariado fica na Blacklist (3 min)
#define CLOSE_PAIR_THRESHOLD   1.0f         ///< Desvio máximo tolerado entre dois sensores considerados próximos (ºC)
#define FAR_SENSOR_THRESHOLD   2.5f         ///< Desvio máximo tolerado para considerar uma leitura como discrepante (ºC)

/**
 * @brief Limites biométricos críticos baseados em padrões de saúde, e tempos de amostragem.
 */
#define SPO2_MIN               95           ///< Limite mínimo de saturação de Oxigénio no sangue (despoleta Hipóxia < 95%)
#define HR_MAX                 159          ///< Limite máximo seguro de Batimentos por Minuto (despoleta Taquicardia > 159)
#define HR_MIN                 44           ///< Limite mínimo seguro de Batimentos por Minuto (despoleta Bradicardia < 44)
#define SEN_END_CYCLE          60           ///< Número de amostras biológicas recolhidas para calcular uma média sólida
#define SEN_SAMPLE_TIME        1000         ///< Intervalo base de amostragem em milissegundos para captação de vitais

#endif // CONFIG_H