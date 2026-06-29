# Integracao LoRa com a Dashboard

Esta pasta contem uma copia da interface ligada ao receptor LoRa.

## Como correr na Raspberry

```bash
cd ~/Desktop/int22_lora_integrated
python3 testedashboard.py
```

Por defeito, a dashboard tenta usar LoRa no canal `7` (`868.5 MHz`), que e o canal usado pelo sketch `esp_tablet_fullpacket_test.ino`.

## Variaveis uteis

Forcar simulacao:

```bash
RESQSENSE_FORCE_SIMULATION=1 python3 testedashboard.py
```

Usar LoRa noutro canal:

```bash
RESQSENSE_LORA_CHANNEL=2 python3 testedashboard.py
```

Usar UART em vez de LoRa:

```bash
RESQSENSE_TELEMETRY_TRANSPORT=uart RESQSENSE_FORCE_SIMULATION=0 python3 testedashboard.py
```

## Dependencias na Raspberry

```bash
sudo apt install python3-pycryptodome python3-rpi.gpio python3-spidev
```

O receptor LoRa espera o mesmo wiring usado nos testes:

- `CS` -> `CE0 / GPIO8`
- `RST` -> `GPIO22`
- `DIO0/G0` -> `GPIO25`
- SPI0 normal da Raspberry (`MOSI`, `MISO`, `SCK`)
