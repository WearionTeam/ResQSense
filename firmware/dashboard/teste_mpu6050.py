#!/usr/bin/env python3
"""
Teste rapido do MPU-6050 para o radar.

O que faz:
  - Liga ao sensor pelo MagnetometerController (o mesmo que o dashboard usa).
  - Mostra no terminal o rumo (heading) em graus, em tempo real.
  - Gira o valor conforme voce roda fisicamente o sensor (eixo Z do giroscopio).

Como usar:
    cd ~/Desktop/int_testelora_novo_radar
    python3 teste_mpu6050.py

  Para inverter o sentido de rotacao:
    RESQSENSE_MAG_Z_SIGN=-1 python3 teste_mpu6050.py

  Se o sensor estiver no endereco 0x69 (AD0 em HIGH):
    RESQSENSE_MPU_I2C_ADDR=0x69 python3 teste_mpu6050.py

Pressione Ctrl+C para sair.
"""

import time

from magnetometer_controller import MagnetometerController


def barra(heading_deg, largura=36):
    """Desenha uma setinha simples para visualizar o rumo."""
    pos = int((heading_deg % 360.0) / 360.0 * largura)
    return "[" + "-" * pos + "|" + "-" * (largura - pos - 1) + "]"


def main():
    print("== Teste MPU-6050 / Radar ==")
    print("A ligar ao sensor (deixe-o PARADO ~1s para calibrar)...\n")

    mag = MagnetometerController(update_interval_sec=0.02)

    if not mag.available():
        print("ERRO: nao consegui falar com o MPU-6050.")
        print("Detalhe:", mag.last_error)
        print("\nVerifique:")
        print("  1) I2C ligado:        sudo raspi-config -> Interface Options -> I2C")
        print("  2) Sensor detetado:   i2cdetect -y 1   (deve mostrar 68)")
        print("  3) Ligacoes: VCC->3.3V(pino1) GND->pino9 SDA->pino3 SCL->pino5")
        print("  4) Biblioteca:        pip install smbus2")
        return

    print(f"OK! Sensor ligado. Bias do giroscopio Z = {mag.gyro_bias_z:.3f} graus/s")
    if mag.last_error:
        print("Aviso:", mag.last_error)
    print("Rode o sensor para ver o rumo mudar. Ctrl+C para sair.\n")

    mag.start()
    try:
        while True:
            h = mag.get_heading_degrees()
            print(f"\rRumo: {h:6.1f} deg  {barra(h)}", end="", flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n\nA encerrar...")
    finally:
        mag.stop()
        print("Terminado.")


if __name__ == "__main__":
    main()
