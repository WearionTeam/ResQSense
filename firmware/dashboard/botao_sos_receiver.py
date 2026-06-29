#!/usr/bin/env python3
"""
RECEPTOR DE ALERTA SOS (lado do colete) - teste standalone.

Escuta por LoRa os alertas de botao enviados pelo botao_sos_sender.py.
Quando chega um alerta de botao (OPCODE_QUICK_ALERT), reage:
   - mostra no ecra/terminal o aviso;
   - toca o som de emergencia (altifalante, ou buzzer se nao houver altifalante).

Isto e util para TESTAR a rececao sem abrir o dashboard inteiro. O dashboard
(testedashboard.py) ja recebe e reage a estes mesmos alertas automaticamente,
porque usam o protocolo de controlo que ele ja conhece.

COMO USAR:
    python3 botao_sos_receiver.py
    RESQSENSE_LORA_CHANNEL=7 python3 botao_sos_receiver.py

Ctrl+C para sair.
"""

import os
import time

from lora_telemetry_receiver import SX1276RFM9x
from telemetry_protocol import (
    decrypt_aes_ctr,
    unpack_control_frame,
    map_warning_code,
    OPCODE_QUICK_ALERT,
    FUNCT_QUICK_SOS,
    FUNCT_QUICK_MAN_DOWN,
    FUNCT_QUICK_BAT_CRITICAL,
    FUNCT_QUICK_HW_FAIL,
)

try:
    from speaker_controller import SpeakerController
except Exception:
    SpeakerController = None

try:
    from buzzer_controller import BuzzerController
except Exception:
    BuzzerController = None


def _int_env(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


LORA_CHANNEL = _int_env("RESQSENSE_LORA_CHANNEL", 7)
SPEAKER_PIN = _int_env("RESQSENSE_SPEAKER_PIN", 17)

FUNCT_LABELS = {
    FUNCT_QUICK_SOS: "SOS (botao)",
    FUNCT_QUICK_MAN_DOWN: "HOMEM ABATIDO",
    FUNCT_QUICK_BAT_CRITICAL: "BATERIA CRITICA",
    FUNCT_QUICK_HW_FAIL: "FALHA HARDWARE",
}


def make_sound_device():
    """Devolve um objeto com .emergency_burst(), priorizando o altifalante."""
    if SpeakerController is not None:
        spk = SpeakerController(pin=SPEAKER_PIN)
        if spk.available():
            print("[SOM] Altifalante ativo.")
            return spk
    if BuzzerController is not None:
        print("[SOM] A usar buzzer.")
        return BuzzerController(pin=SPEAKER_PIN)
    print("[SOM] Nenhum dispositivo de som disponivel (so texto).")
    return None


def handle_alert(control, sound):
    funct = control.get("funct")
    label = FUNCT_LABELS.get(funct, f"funct={funct}")
    vest_id = control.get("vest_id")
    stamp = time.strftime("%H:%M:%S")

    print("\n" + "=" * 44)
    print(f"  !!! ALERTA RECEBIDO [{stamp}] !!!")
    print(f"  Colete: {vest_id}   Tipo: {label}")
    print(f"  Codigo de aviso: {control.get('warning_code')}")
    print("=" * 44)

    if sound is not None:
        try:
            sound.emergency_burst()
        except Exception as exc:
            print(f"[SOM] Erro ao tocar: {exc}")


def main():
    print(f"Receptor de alertas SOS no canal LoRa {LORA_CHANNEL}. Ctrl+C para sair.\n")
    sound = make_sound_device()

    radio = SX1276RFM9x()
    radio.open()
    radio.set_channel(LORA_CHANNEL)

    try:
        while True:
            packet = radio.receive(timeout=0.2)
            if packet is None:
                continue
            if len(packet) != 2:
                # so nos interessam frames de controlo (2 bytes)
                continue

            control = unpack_control_frame(decrypt_aes_ctr(packet))
            if not control:
                continue
            control["warning_code"] = map_warning_code(control["opcode"], control["funct"])

            if control.get("opcode") == OPCODE_QUICK_ALERT:
                handle_alert(control, sound)
            else:
                print(f"[RX] Controlo recebido (nao-alerta): {control}")
    except KeyboardInterrupt:
        print("\nA encerrar...")
    finally:
        radio.close()


if __name__ == "__main__":
    main()
