#ifndef CONFIG_H
#define CONFIG_H

#include <Arduino.h>

/**
 * @brief Pinos fisicos ESP32
 * 
 */

 #if defined(U_WROOM) 
    #define SPI_MISO    23
    #define SPI_MOSI    19
    #define SPI_SCLK    18
    #define SPI_NSS     15
    #define DIO0        26
    #define NRESET      27
    #define I2C_SDA     21
    #define I2C_SCL     22
#elif defined(U_32S3)
    #define SPI_MISO    23
    #define SPI_MOSI    19
    #define SPI_SCLK    18
    #define SPI_NSS     15
    #define DIO0        26
    #define NRESET      27
    #define I2C_SDA     4
    #define I2C_SCL     5
    #define ISR_PIN     10
#endif


/**
 * @brief  Endereços hexadecimais dos sensores das ResQSense
 * 
 */
#define IMU_ADDR     0x68                   ///< Endereço do IMU(MPU-6050)
#define TMP_ADDR     0x48                   ///< Endereço do Sensor de Temperatura 1
#define TMPA_ADDR    0x4A                   ///< Endereço do Sensor de Temperatura 2
#define TMPB_ADDR    0x4B                   ///< Endereço do Sensor de Temperatura 2
#define MAX_ADDR     0x57                   ///< Endereço do SEN
#define GNSS_ADDR    0x66                   ///< Endereço do GNSS

/**
 * @brief Constantes para conversão de valores RAW para unidades SI
 * 
 */
#define IMU_ACCEL_SENSIVITY 2048.0f         ///< Sensibilidade do Acelerómetro
#define IMU_GYRO_SENSIVITY  16.4f           ///< Sensibilidade do Giroscópio
#define IMU_OFFSET_ACCEL_X  -372            ///< Offset de calibração para o eixo X do Acelerómetro
#define IMU_OFFSET_ACCEL_Y  14              ///< Offset de calibração para o eixo Y do Acelerómetro
#define IMU_OFFSET_ACCEL_Z  12              ///< Offset de calibração para o eixo Z do Acelerómetro
#define IMU_OFFSET_GYRO_X   -42             ///< Offset de calibração para o eixo X do Giroscópio
#define IMU_OFFSET_GYRO_Y   -8              ///< Offset de calibração para o eixo Y do Giroscópio
#define IMU_OFFSET_GYRO_Z   -1              ///< Offset de calibração para o eixo Z do Giroscópio
#define GRAVITY             9.81f           ///> Aceleração gravítica

/**
 * @brief Limiares de aceleração e janelas para identificação de queda
 * 
 */
#define TRESHOLD_FALL       0.52f           ///< Limiar de Free-Fall 
#define TRESHOLD_IMPACT     3.66f           ///< Limiar de impacto
#define TRESHOLD_GRAVITY    0.10f           ///< Limiar para verificação de x ou y approx de 0
#define FALL_TIMEOUT        3000            ///< Intervalo de tempo para queda
#define INACTIVITY_TIME     1500            ///< Intervalo de tempo inactivo
/**
 * @brief 
 * 
 */
#define TEMP_SAMPLE_TIME_MS 10000UL
#define TEMP_MIN 2.0f
#define TEMP_MAX 39.0f
#define IGNORE_SENSOR_INTERVAL 180000UL
#define CLOSE_PAIR_THRESHOLD 1.0f
#define FAR_SENSOR_THRESHOLD 2.5f
/**
 * @brief 
 * 
 */
#define SPO2_MIN 95
#define HR_MAX  159
#define HR_MIN  44
#define SEN_END_CYCLE 60
#define SEN_SAMPLE_TIME 1000

 #endif
