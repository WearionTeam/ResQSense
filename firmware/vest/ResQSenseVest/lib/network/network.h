/**
 * @file network.h
 * @brief Biblioteca com as funções que gerem a rede
 * @details Este módulo gere a stack de comunicação rádio, emparelhamento (Sync),
 * envio de telemetria baseada em slots de tempo (TDMA) e encriptação AES-128-CTR.
 */
#ifndef NETWORK_H
#define NETWORK_H

#include <RadioLib.h>
#include <Arduino.h>

#include "protocol.h"
#include "config.h"
#include "sensors.h"

/** 
 * @brief Fila (Queue) do FreeRTOS para armazenar pacotes de telemetria pendentes. 
 * @note Exposta externamente para que outras Tasks possam injetar dados dos sensores.
 */
extern QueueHandle_t telemetry_queue;
/** 
 * @brief Fila (Queue) do FreeRTOS para armazenar alertas e emergências. 
 */
extern QueueHandle_t alert_queue;

/**
 * @brief Inicializa o módulo lora com o chip SX1276
 * @details Configura o barramento SPI, pinos de interrupção e parâmetros de rádio iniciais.
 * @return true se o módulo de lora começar sem erro 
 * @return false se o módulo de lora começar e obter um erro
 */
bool startNetwork(); 
/**
 * @brief Tarefa (Task) principal do FreeRTOS para a máquina de estados da Rede LoRa.
 * @details Gere a receção, envio, sincronização TDMA e transição de canais.
 * Corre num loop infinito bloqueado/desbloqueado por interrupções e filas.
 * @param pvParameters Parâmetros passados à tarefa na sua criação (habitualmente NULL).
 */
void TaskLoRaNetwork(void *pvParameters); 
/**
 * @brief Coloca um pacote de telemetria (dados dos sensores) na fila de envio.
 * @details Se a fila estiver cheia, o pacote mais antigo é descartado para dar lugar ao novo (overwrite).
 * @param data Estrutura processada com os dados do IMU, GNSS e Vitais a enviar.
 */
void queueTelemetryPacket(const processed_data &data); 
/**
 * @brief Formata e coloca um alerta de emergência na fila de prioridade (alert_queue).
 * @param opcode Código de operação principal do alerta (ex: OP_VTALERT, OP_QALERT).
 * @param functionCode Sub-código ou especificação do alerta (ex: queda, botão SOS).
 * @param sensor_value Valor adicional de 16 bits caso o alerta o exija.
 */
void queueAlert(OpCode opcode, uint8_t functionCode, uint16_t sensor_value);

#endif
