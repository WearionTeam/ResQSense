#!/usr/bin/env python3
"""
BOTAO SOS -> envia um alerta por LoRa.

Este script corre no dispositivo do BOTAO (ex.: um Raspberry Pi com modulo
LoRa RFM95/SX1276 e um push-button). Quando o botao e premido, envia um
"alerta rapido" (OPCODE_QUICK_ALERT + FUNCT_QUICK_SOS) por LoRa, no mesmo
formato (AES + frame de controlo de 2 bytes) que o dashboard ja sabe receber.

O colete/estacao a correr o dashboard recebe esse alerta automaticamente e
dispara o popup no ecra + o som de emergencia.

LIGACAO DO BOTAO (no dispositivo emissor):
    Um lado do botao  -> GPIO 26 (BCM)   [configuravel]
    Outro lado        -> GND
    (usa pull-up interno; premir liga o pino a GND)

COMO USAR:
    # Modo botao fisico (fica a espera de premir):
    python3 botao_sos_sender.py

    # Enviar UM alerta de teste e sair (sem botao):
    python3 botao_sos_sender.py --once

    # Definir id do colete e canal:
    RESQSENSE_VEST_ID=3 RESQSENSE_LORA_CHANNEL=7 python3 botao_sos_sender.py
"""

import os
import sys
import time

from lora_telemetry_receiver import (
    SX1276RFM9x,
    create_control_packet,
    encrypt_aes_ctr,
    DEFAULT_NET_ID,
)
from telemetry_protocol import OPCODE_QUICK_ALERT, FUNCT_QUICK_SOS

try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None


def _int_env(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


VEST_ID = _int_env("RESQSENSE_VEST_ID", 1)
NET_ID = _int_env("RESQSENSE_NET_ID", DEFAULT_NET_ID)
LORA_CHANNEL = _int_env("RESQSENSE_LORA_CHANNEL", 7)
BUTTON_PIN = _int_env("RESQSENSE_BUTTON_PIN", 26)


def build_alert_packet():
    """Cria o pacote de alerta SOS (2 bytes, encriptado AES)."""
    raw = create_control_packet(VEST_ID, OPCODE_QUICK_ALERT, FUNCT_QUICK_SOS, net_id=NET_ID)
    return encrypt_aes_ctr(raw)


def send_alert(radio):
    payload = build_alert_packet()
    radio.set_channel(LORA_CHANNEL)
    ok = radio.send(payload)
    stamp = time.strftime("%H:%M:%S")
    if ok:
        print(f"[{stamp}] ALERTA SOS enviado (Vest {VEST_ID}, canal {LORA_CHANNEL}) "
              f"-> {payload.hex(' ')}")
    else:
        print(f"[{stamp}] FALHA ao enviar o alerta (TX timeout).")
    return ok


def run_once():
    radio = SX1276RFM9x()
    radio.open()
    try:
        send_alert(radio)
    finally:
        radio.close()


def run_button_loop():
    if GPIO is None:
        print("RPi.GPIO nao disponivel. Use '--once' para enviar um teste, "
              "ou corra no Raspberry Pi.")
        return

    radio = SX1276RFM9x()
    radio.open()

    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    print(f"Botao SOS pronto no GPIO {BUTTON_PIN} (BCM). Prima para enviar alerta. "
          f"Ctrl+C para sair.")
    last_press = 0.0
    try:
        while True:
            # Botao a GND com pull-up: premido = nivel LOW.
            if GPIO.input(BUTTON_PIN) == GPIO.LOW:
                now = time.monotonic()
                if now - last_press > 1.0:  # debounce / anti-repeticao (1s)
                    last_press = now
                    send_alert(radio)
                    # espera soltar para nao repetir
                    while GPIO.input(BUTTON_PIN) == GPIO.LOW:
                        time.sleep(0.02)
            time.sleep(0.02)
    except KeyboardInterrupt:
        print("\nA encerrar...")
    finally:
        radio.close()
        GPIO.cleanup(BUTTON_PIN)


def main():
    if "--once" in sys.argv or "-1" in sys.argv:
        run_once()
    else:
        run_button_loop()


if __name__ == "__main__":
    main()
