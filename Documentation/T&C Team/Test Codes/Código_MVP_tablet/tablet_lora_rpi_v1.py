import os
import queue
import struct
import threading
import time

from Crypto.Cipher import AES
from Crypto.Util import Counter

try:
    import spidev
except ImportError:
    spidev = None

try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None


# CONFIGURACOES GLOBAIS

_AES_KEY_HEX = os.environ.get("WEARION_AES_KEY", "")
if len(_AES_KEY_HEX) == 32:
    AES_KEY = bytes.fromhex(_AES_KEY_HEX)
else:
    AES_KEY = bytes([
        0x2B, 0x7E, 0x15, 0x16, 0x28, 0xAE, 0xD2, 0xA6,
        0xAB, 0xF7, 0x15, 0x88, 0x09, 0xCF, 0x4F, 0x3C,
    ])

# IV fixo para compatibilidade com o formato atual das tramas.
FIXED_IV = bytes([
    0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
    0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F,
])

MODO_HARDWARE = "LORA_SPI"
MODO_VALIDACAO = True

VALID_PACKET_LENGTHS = {2, 3, 4, 7, 15}

DEFAULT_NET_ID = 0b001
DEFAULT_DATA_CH = 0b010
DEFAULT_CTRL_CH = 0b111

TDMA_CYCLE_SECONDS = 10.0
TDMA_MAX_VESTS = 30
TDMA_SLOT_SECONDS = TDMA_CYCLE_SECONDS / TDMA_MAX_VESTS
TDMA_SLOT_GUARD_SECONDS = 0.12

# -----------------------------------------------------------
# CONFIGURACAO LORA — Raspberry Pi + RFM95W / SX1276

SPI_BUS = 0
SPI_DEVICE = 0
SPI_SPEED_HZ = 5_000_000

PIN_LORA_RESET = 22
PIN_LORA_DIO0 = 25

LORA_FREQUENCY_HZ = 868_000_000
LORA_SYNC_WORD = 0x12
LORA_TX_POWER_DBM = 14
LORA_BANDWIDTH = 125_000
LORA_CODING_RATE = 5       
LORA_SPREADING_FACTOR = 7
LORA_PREAMBLE_LEN = 8
LORA_ENABLE_CRC = True

ACK_DEFAULT_TIMEOUT = 8.0
RETRY_RESPONSE_TIMEOUT = 2.0
RETRY_COOLDOWN_SECONDS = 1.5
NETWORK_ALIVE_INTERVAL_S = 20.0


CHANNEL_FREQUENCIES_HZ = {
   
    0: 868_100_000,  # Canal global / SYNC
    1: 867_100_000,
    2: 868_300_000,  # Canal de dados default
    3: 867_300_000,
    4: 867_500_000,
    5: 867_700_000,
    6: 867_900_000,
    7: 868_500_000,  # Canal de controlo default
}


# OPCODES / FUNCTS

OPCODE_NULL = 0b0000
OPCODE_SYNC = 0b0011
OPCODE_RETRY_REQUEST = 0b0110
OPCODE_NETWORK_ALIVE = 0b0101
OPCODE_ACK = 0b1001
OPCODE_VITAL_ALERT = 0b1010
OPCODE_UNSYNC = 0b1100
OPCODE_QUICK_ALERT = 0b1111

FUNCT_ACK_SYNC = 0b0011
FUNCT_ACK_UNSYNC = 0b0010
FUNCT_ACK_ALERT = 0b0110
FUNCT_ACK_DATA_ALERT = 0b1100

FUNCT_UNSYNC_CENTRAL_OFF = 0b1100
FUNCT_UNSYNC_MANUAL = 0b0110
FUNCT_UNSYNC_END_OP = 0b0011

FUNCT_RETRY_FULL = 0b1100
FUNCT_RETRY_LIGHT = 0b0011

FUNCT_QUICK_SOS = 0b1111
FUNCT_QUICK_MAN_DOWN = 0b1100
FUNCT_QUICK_BAT_CRITICAL = 0b0110
FUNCT_QUICK_HW_FAIL = 0b0011

FUNCT_VITAL_BPM_HIGH = 0b1001
FUNCT_VITAL_BPM_LOW = 0b0110
FUNCT_VITAL_TEMP_HIGH = 0b0101
FUNCT_VITAL_TEMP_LOW = 0b1010
FUNCT_VITAL_SPO2_LOW = 0b0011

FUNCT_NA_BEACON = 0b0000
FUNCT_NA_WAKE_UWB = 0b1001
FUNCT_NA_CONCEDE_HOP = 0b1100
FUNCT_NA_TEAM_HOP = 0b0011


# ESTADO GLOBAL

lora = None
data_queue = queue.Queue()
memoria_gps = {}
registered_vests = {}
sync_lock = threading.Lock()
running = True
tdma_epoch = time.time()
current_team_data_ch = DEFAULT_DATA_CH
current_team_ctrl_ch = DEFAULT_CTRL_CH

pending_ack = None
pending_ack_lock = threading.Lock()
pending_retry = None
pending_retry_lock = threading.Lock()


# CRIPTOGRAFIA

def _build_ctr():
    return Counter.new(128, initial_value=int.from_bytes(FIXED_IV, byteorder="big"))


def encrypt_aes_ctr(data):
    cipher = AES.new(AES_KEY, AES.MODE_CTR, counter=_build_ctr())
    return cipher.encrypt(data)


def decrypt_aes_ctr(data):
    cipher = AES.new(AES_KEY, AES.MODE_CTR, counter=_build_ctr())
    return cipher.decrypt(data)


# DRIVER SX1276 / RFM95W

class SX1276RFM95W:
    REG_FIFO = 0x00
    REG_OP_MODE = 0x01
    REG_FRF_MSB = 0x06
    REG_FRF_MID = 0x07
    REG_FRF_LSB = 0x08
    REG_PA_CONFIG = 0x09
    REG_LNA = 0x0C
    REG_FIFO_ADDR_PTR = 0x0D
    REG_FIFO_TX_BASE_ADDR = 0x0E
    REG_FIFO_RX_BASE_ADDR = 0x0F
    REG_FIFO_RX_CURRENT_ADDR = 0x10
    REG_IRQ_FLAGS = 0x12
    REG_RX_NB_BYTES = 0x13
    REG_PKT_SNR_VALUE = 0x19
    REG_PKT_RSSI_VALUE = 0x1A
    REG_MODEM_CONFIG_1 = 0x1D
    REG_MODEM_CONFIG_2 = 0x1E
    REG_PREAMBLE_MSB = 0x20
    REG_PREAMBLE_LSB = 0x21
    REG_PAYLOAD_LENGTH = 0x22
    REG_MODEM_CONFIG_3 = 0x26
    REG_DETECTION_OPTIMIZE = 0x31
    REG_DETECTION_THRESHOLD = 0x37
    REG_SYNC_WORD = 0x39
    REG_DIO_MAPPING_1 = 0x40
    REG_VERSION = 0x42
    REG_PA_DAC = 0x4D

    MODE_LONG_RANGE = 0x80
    MODE_SLEEP = 0x00
    MODE_STDBY = 0x01
    MODE_TX = 0x03
    MODE_RX_CONTINUOUS = 0x05

    IRQ_RX_TIMEOUT_MASK = 0x80
    IRQ_RX_DONE_MASK = 0x40
    IRQ_PAYLOAD_CRC_ERROR_MASK = 0x20
    IRQ_TX_DONE_MASK = 0x08

    MAX_PKT_LENGTH = 255

    def __init__(self, spi_bus, spi_device, reset_pin, dio0_pin, spi_speed_hz=5_000_000):
        self.spi_bus = spi_bus
        self.spi_device = spi_device
        self.reset_pin = reset_pin
        self.dio0_pin = dio0_pin
        self.spi_speed_hz = spi_speed_hz
        self.spi = None
        self.current_channel = None

    def open(self):
        if spidev is None:
            raise RuntimeError("Modulo 'spidev' nao encontrado. Instala com: pip install spidev")
        if GPIO is None:
            raise RuntimeError("Modulo 'RPi.GPIO' nao encontrado. Instala com: pip install RPi.GPIO")

        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.reset_pin, GPIO.OUT, initial=GPIO.HIGH)
        GPIO.setup(self.dio0_pin, GPIO.IN)

        self.spi = spidev.SpiDev()
        self.spi.open(self.spi_bus, self.spi_device)
        self.spi.max_speed_hz = self.spi_speed_hz
        self.spi.mode = 0

        self.reset()
        version = self.read_reg(self.REG_VERSION)
        validation_log("LORA", f"Chip version: 0x{version:02X}")
        if version != 0x12:
            validation_log("LORA", "AVISO: versao de chip inesperada — confirma ligacoes SPI.")

        self.sleep()
        self.write_reg(self.REG_OP_MODE, self.MODE_LONG_RANGE | self.MODE_SLEEP)
        time.sleep(0.01)
        self.standby()

        self.write_reg(self.REG_FIFO_TX_BASE_ADDR, 0x00)
        self.write_reg(self.REG_FIFO_RX_BASE_ADDR, 0x00)
        self.write_reg(self.REG_LNA, self.read_reg(self.REG_LNA) | 0x03)
        self.write_reg(self.REG_MODEM_CONFIG_3, 0x04)

        self.set_tx_power(LORA_TX_POWER_DBM)
        self.set_lora_config(
            bandwidth=LORA_BANDWIDTH,
            coding_rate=LORA_CODING_RATE,
            spreading_factor=LORA_SPREADING_FACTOR,
            preamble_len=LORA_PREAMBLE_LEN,
            crc_enabled=LORA_ENABLE_CRC,
            sync_word=LORA_SYNC_WORD,
        )
        self.set_channel(0)

    def close(self):
        try:
            if self.spi is not None:
                self.sleep()
                self.spi.close()
        finally:
            self.spi = None
            if GPIO is not None:
                GPIO.cleanup((self.reset_pin, self.dio0_pin))

    def reset(self):
        GPIO.output(self.reset_pin, GPIO.LOW)
        time.sleep(0.01)
        GPIO.output(self.reset_pin, GPIO.HIGH)
        time.sleep(0.01)

    def read_reg(self, addr):
        return self.spi.xfer2([addr & 0x7F, 0x00])[1]

    def write_reg(self, addr, value):
        self.spi.xfer2([addr | 0x80, value & 0xFF])

    def burst_write(self, addr, data):
        self.spi.xfer2([addr | 0x80] + list(data))

    def burst_read(self, addr, length):
        return bytes(self.spi.xfer2([addr & 0x7F] + [0x00] * length)[1:])

    def sleep(self):
        self.write_reg(self.REG_OP_MODE, self.MODE_LONG_RANGE | self.MODE_SLEEP)

    def standby(self):
        self.write_reg(self.REG_OP_MODE, self.MODE_LONG_RANGE | self.MODE_STDBY)

    def rx_continuous(self):
        self.write_reg(self.REG_DIO_MAPPING_1, 0x00)
        self.write_reg(self.REG_OP_MODE, self.MODE_LONG_RANGE | self.MODE_RX_CONTINUOUS)

    def set_frequency(self, frequency_hz):
        frf = int((frequency_hz << 19) / 32_000_000)
        self.write_reg(self.REG_FRF_MSB, (frf >> 16) & 0xFF)
        self.write_reg(self.REG_FRF_MID, (frf >> 8) & 0xFF)
        self.write_reg(self.REG_FRF_LSB, frf & 0xFF)

    def set_channel(self, channel_id):
        freq = CHANNEL_FREQUENCIES_HZ.get(channel_id, LORA_FREQUENCY_HZ)
        self.standby()
        self.set_frequency(freq)
        self.current_channel = channel_id
        self.rx_continuous()
        validation_log("LORA", f"Canal logico {channel_id} -> {freq / 1e6:.3f} MHz")

    def set_tx_power(self, tx_power_dbm):
        tx_power_dbm = max(2, min(tx_power_dbm, 17))
        self.write_reg(self.REG_PA_CONFIG, 0x80 | (tx_power_dbm - 2))
        self.write_reg(self.REG_PA_DAC, 0x04)

    def set_lora_config(self, bandwidth, coding_rate, spreading_factor, preamble_len, crc_enabled, sync_word):
        bw_bits = {
            7_800: 0,
            10_400: 1,
            15_600: 2,
            20_800: 3,
            31_250: 4,
            41_700: 5,
            62_500: 6,
            125_000: 7,
            250_000: 8,
            500_000: 9,
        }[bandwidth]
        cr_bits = max(1, min(coding_rate - 4, 4))
        sf_bits = max(6, min(spreading_factor, 12))

        modem_config_1 = (bw_bits << 4) | (cr_bits << 1)
        modem_config_2 = (sf_bits << 4) | (0x04 if crc_enabled else 0x00)
        modem_config_3 = self.read_reg(self.REG_MODEM_CONFIG_3)
        if bandwidth == 125_000 and sf_bits >= 11:
            modem_config_3 |= 0x08
        else:
            modem_config_3 &= ~0x08

        self.write_reg(self.REG_MODEM_CONFIG_1, modem_config_1)
        self.write_reg(self.REG_MODEM_CONFIG_2, modem_config_2)
        self.write_reg(self.REG_MODEM_CONFIG_3, modem_config_3)
        self.write_reg(self.REG_PREAMBLE_MSB, (preamble_len >> 8) & 0xFF)
        self.write_reg(self.REG_PREAMBLE_LSB, preamble_len & 0xFF)
        self.write_reg(self.REG_SYNC_WORD, sync_word)

        if sf_bits == 6:
            self.write_reg(self.REG_DETECTION_OPTIMIZE, 0xC5)
            self.write_reg(self.REG_DETECTION_THRESHOLD, 0x0C)
        else:
            self.write_reg(self.REG_DETECTION_OPTIMIZE, 0xC3)
            self.write_reg(self.REG_DETECTION_THRESHOLD, 0x0A)

    def send(self, payload, timeout=2.0):
        if not 0 < len(payload) <= self.MAX_PKT_LENGTH:
            raise ValueError(f"Payload LoRa invalido: {len(payload)} bytes")

        self.standby()
        self.write_reg(self.REG_IRQ_FLAGS, 0xFF)
        self.write_reg(self.REG_FIFO_ADDR_PTR, 0x00)
        self.burst_write(self.REG_FIFO, payload)
        self.write_reg(self.REG_PAYLOAD_LENGTH, len(payload))
        self.write_reg(self.REG_DIO_MAPPING_1, 0x40)
        self.write_reg(self.REG_OP_MODE, self.MODE_LONG_RANGE | self.MODE_TX)

        deadline = time.time() + timeout
        while time.time() < deadline:
            irq = self.read_reg(self.REG_IRQ_FLAGS)
            if irq & self.IRQ_TX_DONE_MASK:
                self.write_reg(self.REG_IRQ_FLAGS, self.IRQ_TX_DONE_MASK)
                self.rx_continuous()
                return True
            time.sleep(0.005)

        self.rx_continuous()
        return False

    def receive(self, timeout=0.1):
        deadline = time.time() + timeout
        while time.time() < deadline:
            irq = self.read_reg(self.REG_IRQ_FLAGS)
            if irq & self.IRQ_RX_DONE_MASK:
                if irq & self.IRQ_PAYLOAD_CRC_ERROR_MASK:
                    self.write_reg(self.REG_IRQ_FLAGS, 0xFF)
                    validation_log("LORA", "CRC error — pacote descartado.")
                    return None

                current_addr = self.read_reg(self.REG_FIFO_RX_CURRENT_ADDR)
                packet_len = self.read_reg(self.REG_RX_NB_BYTES)
                self.write_reg(self.REG_FIFO_ADDR_PTR, current_addr)
                payload = self.burst_read(self.REG_FIFO, packet_len)
                self.write_reg(self.REG_IRQ_FLAGS, 0xFF)
                return payload

            if irq & self.IRQ_RX_TIMEOUT_MASK:
                self.write_reg(self.REG_IRQ_FLAGS, self.IRQ_RX_TIMEOUT_MASK)
                return None

            time.sleep(0.003)
        return None


# CAMADA DE HARDWARE

def open_hardware():
    if MODO_HARDWARE == "LORA_SPI":
        _open_lora_spi()
    else:
        raise NotImplementedError(f"Modo nao implementado: {MODO_HARDWARE}")


def _open_lora_spi():
    global lora
    lora = SX1276RFM95W(
        spi_bus=SPI_BUS,
        spi_device=SPI_DEVICE,
        reset_pin=PIN_LORA_RESET,
        dio0_pin=PIN_LORA_DIO0,
        spi_speed_hz=SPI_SPEED_HZ,
    )
    lora.open()
    print(
        f"[LORA] SPI inicializado — {LORA_FREQUENCY_HZ / 1e6:.1f} MHz | "
        f"SF{LORA_SPREADING_FACTOR} BW{LORA_BANDWIDTH // 1000}k CR4/{LORA_CODING_RATE}"
    )


def hardware_send_packet(payload):
    if MODO_HARDWARE == "LORA_SPI":
        ok = lora.send(payload)
        if not ok:
            raise TimeoutError("Timeout no envio LoRa (TX_DONE nao recebido)")
        return
    raise NotImplementedError(f"Modo nao suportado: {MODO_HARDWARE}")


def hardware_receive_packet():
    if MODO_HARDWARE == "LORA_SPI":
        packet = lora.receive(timeout=0.1)
        if packet is None:
            return None
        if len(packet) not in VALID_PACKET_LENGTHS:
            validation_log("LORA", f"Pacote ignorado — tamanho invalido: {len(packet)} B")
            return None
        return packet
    raise NotImplementedError(f"Modo nao suportado: {MODO_HARDWARE}")


def hardware_set_channel(channel_id):
    if lora is not None and lora.current_channel != channel_id:
        lora.set_channel(channel_id)


def hardware_set_role_data(vest=None):
    channel_id = current_team_data_ch if vest is None else vest.get("data_ch", current_team_data_ch)
    hardware_set_channel(channel_id)


def hardware_set_role_control(vest=None):
    channel_id = current_team_ctrl_ch if vest is None else vest.get("ctrl_ch", current_team_ctrl_ch)
    hardware_set_channel(channel_id)


def hardware_set_role_sync():
    hardware_set_channel(0)


# FUNCOES AUXILIARES DO PROTOCOLO

def make_header(net_id, vest_id):
    return ((net_id & 0x07) << 5) | (vest_id & 0x1F)


def split_control_byte(byte1):
    return (byte1 >> 4) & 0x0F, byte1 & 0x0F


def make_control_byte(opcode, funct):
    return ((opcode & 0x0F) << 4) | (funct & 0x0F)


def decode_flags(flags):
    return {
        "type_full": bool((flags >> 7) & 0x01),
        "sos": bool((flags >> 6) & 0x01),
        "mandown": bool((flags >> 5) & 0x01),
        "vital": bool((flags >> 4) & 0x01),
        "bat": bool((flags >> 3) & 0x01),
        "hw": bool((flags >> 2) & 0x01),
        "pest": bool((flags >> 1) & 0x01),
        "fix": bool(flags & 0x01),
    }


def format_coord(raw_value):
    return raw_value / 10_000_000.0


def current_cycle_index():
    return int((time.time() - tdma_epoch) // TDMA_CYCLE_SECONDS)


def seconds_until_next_slot(vest_id):
    now = time.time()
    cycle_pos = (now - tdma_epoch) % TDMA_CYCLE_SECONDS
    slot_pos = (vest_id - 1) * TDMA_SLOT_SECONDS
    wait = slot_pos - cycle_pos
    if wait < 0:
        wait += TDMA_CYCLE_SECONDS
    return wait


def format_hex_bytes(data):
    return " ".join(f"0x{byte:02X}" for byte in data)


def validation_log(tag, message):
    if MODO_VALIDACAO:
        print(f"[{tag}] {message}")


# CONSTRUCAO DE TRAMAS

def create_sync_packet(new_vest_id, target_net_id, mac_address_int, data_ch, ctrl_ch):
    byte0 = make_header(0b000, new_vest_id)
    byte1 = ((OPCODE_SYNC & 0x0F) << 4) | (target_net_id & 0x07)
    mac_bytes = struct.pack(">I", mac_address_int)
    byte6 = ((data_ch & 0x07) << 5) | ((ctrl_ch & 0x07) << 1)
    return bytes([byte0, byte1]) + mac_bytes + bytes([byte6])


def create_unsync_packet(net_id, vest_id, funct=FUNCT_UNSYNC_MANUAL):
    return bytes([
        make_header(net_id, vest_id),
        make_control_byte(OPCODE_UNSYNC, funct),
    ])


def create_ack_packet(net_id, vest_id, funct):
    return bytes([
        make_header(net_id, vest_id),
        make_control_byte(OPCODE_ACK, funct),
    ])


def create_retry_request_packet(net_id, vest_id, ask_full=True):
    funct = FUNCT_RETRY_FULL if ask_full else FUNCT_RETRY_LIGHT
    return bytes([
        make_header(net_id, vest_id),
        make_control_byte(OPCODE_RETRY_REQUEST, funct),
    ])


def create_network_alive_packet(net_id):
    return bytes([
        make_header(net_id, 0),
        make_control_byte(OPCODE_NETWORK_ALIVE, FUNCT_NA_BEACON),
    ])


def create_team_hop_packet(net_id, data_ch, ctrl_ch):
    return bytes([
        make_header(net_id, 0),
        make_control_byte(OPCODE_NETWORK_ALIVE, FUNCT_NA_TEAM_HOP),
        ((data_ch & 0x07) << 5) | ((ctrl_ch & 0x07) << 2),
    ])


# DESEMPACOTAMENTO

def unpack_telemetria_completa(data):
    if len(data) != 15:
        return None

    net_id = (data[0] >> 5) & 0x07
    vest_id = data[0] & 0x1F
    flags = decode_flags(data[1])
    if not flags["type_full"]:
        return None

    lat_raw, lon_raw, alt_raw, bpm = struct.unpack(">iihB", data[2:13])
    packed_tail = (data[13] << 8) | data[14]
    spo2_raw = (packed_tail >> 11) & 0x1F
    temp_raw = packed_tail & 0x07FF

    return {
        "tipo": "COMPLETA",
        "net_id": net_id,
        "vest_id": vest_id,
        "flags": flags,
        "lat_raw": lat_raw,
        "lon_raw": lon_raw,
        "lat": format_coord(lat_raw),
        "lon": format_coord(lon_raw),
        "altitude": alt_raw,
        "bpm": bpm,
        "spo2": 0 if spo2_raw == 0 else (spo2_raw + 69),
        "temperature": 25.00 + (temp_raw * 0.01),
    }


def unpack_telemetria_delta(data):
    if len(data) != 7:
        return None

    net_id = (data[0] >> 5) & 0x07
    vest_id = data[0] & 0x1F
    flags = decode_flags(data[1])
    if flags["type_full"]:
        return None

    lat_frac16, lon_frac16, alt_delta = struct.unpack(">HHb", data[2:7])
    return {
        "tipo": "DELTA_GPS",
        "net_id": net_id,
        "vest_id": vest_id,
        "flags": flags,
        "lat_frac16": lat_frac16,
        "lon_frac16": lon_frac16,
        "alt_delta": alt_delta,
    }


def unpack_control_frame(data):
    if len(data) != 2:
        return None

    net_id = (data[0] >> 5) & 0x07
    vest_id = data[0] & 0x1F
    opcode, funct = split_control_byte(data[1])
    return {
        "tipo": "CONTROL",
        "net_id": net_id,
        "vest_id": vest_id,
        "opcode": opcode,
        "funct": funct,
    }


def unpack_team_hop_frame(data):
    if len(data) != 3:
        return None

    net_id = (data[0] >> 5) & 0x07
    vest_id = data[0] & 0x1F
    opcode, funct = split_control_byte(data[1])
    if opcode != OPCODE_NETWORK_ALIVE or funct != FUNCT_NA_TEAM_HOP:
        return None

    return {
        "tipo": "TEAM_HOP",
        "net_id": net_id,
        "vest_id": vest_id,
        "opcode": opcode,
        "funct": funct,
        "data_ch": (data[2] >> 5) & 0x07,
        "ctrl_ch": (data[2] >> 2) & 0x07,
    }


def unpack_bio_alert_frame(data):
    if len(data) != 4:
        return None

    net_id = (data[0] >> 5) & 0x07
    vest_id = data[0] & 0x1F
    opcode, funct = split_control_byte(data[1])
    value = struct.unpack(">H", data[2:4])[0]
    return {
        "tipo": "ALERTA_BIO",
        "net_id": net_id,
        "vest_id": vest_id,
        "opcode": opcode,
        "funct": funct,
        "valor": value,
    }


def unpack_sync_frame(data):
    if len(data) != 7:
        return None

    opcode = (data[1] >> 4) & 0x0F
    if opcode != OPCODE_SYNC:
        return None

    return {
        "tipo": "SYNC",
        "net_id": (data[0] >> 5) & 0x07,
        "vest_id": data[0] & 0x1F,
        "opcode": opcode,
        "target_net_id": data[1] & 0x07,
        "mac_address": struct.unpack(">I", data[2:6])[0],
        "data_ch": (data[6] >> 5) & 0x07,
        "ctrl_ch": (data[6] >> 1) & 0x07,
    }


# ESTADO GPS

def guardar_posicao_base(dados):
    memoria_gps[dados["vest_id"]] = {
        "net_id": dados["net_id"],
        "lat_raw": dados["lat_raw"],
        "lon_raw": dados["lon_raw"],
        "altitude": dados["altitude"],
    }


def aplicar_delta_gps(dados_delta):
    vest_id = dados_delta["vest_id"]
    if vest_id not in memoria_gps:
        return None

    estado = memoria_gps[vest_id]
    lat_base = estado["lat_raw"]
    lon_base = estado["lon_raw"]

    lat_sign = -1 if lat_base < 0 else 1
    lon_sign = -1 if lon_base < 0 else 1

    lat_abs_base = abs(lat_base)
    lon_abs_base = abs(lon_base)
    lat_graus = lat_abs_base // 10_000_000
    lon_graus = lon_abs_base // 10_000_000

    lat_frac_antiga = lat_abs_base % 10_000_000
    lon_frac_antiga = lon_abs_base % 10_000_000

    lat_frac_nova = int((dados_delta["lat_frac16"] * 9_999_999) / 65535)
    lon_frac_nova = int((dados_delta["lon_frac16"] * 9_999_999) / 65535)

    if lat_frac_antiga > 9_000_000 and lat_frac_nova < 1_000_000:
        lat_graus += 1
    elif lat_frac_antiga < 1_000_000 and lat_frac_nova > 9_000_000:
        lat_graus -= 1

    if lon_frac_antiga > 9_000_000 and lon_frac_nova < 1_000_000:
        lon_graus += 1
    elif lon_frac_antiga < 1_000_000 and lon_frac_nova > 9_000_000:
        lon_graus -= 1

    novo_lat_raw = lat_sign * (lat_graus * 10_000_000 + lat_frac_nova)
    novo_lon_raw = lon_sign * (lon_graus * 10_000_000 + lon_frac_nova)
    nova_alt = estado["altitude"] + dados_delta["alt_delta"]

    estado["lat_raw"] = novo_lat_raw
    estado["lon_raw"] = novo_lon_raw
    estado["altitude"] = nova_alt

    return {
        "tipo": "DELTA_GPS",
        "net_id": dados_delta["net_id"],
        "vest_id": vest_id,
        "flags": dados_delta["flags"],
        "lat_raw": novo_lat_raw,
        "lon_raw": novo_lon_raw,
        "lat": format_coord(novo_lat_raw),
        "lon": format_coord(novo_lon_raw),
        "altitude": nova_alt,
        "alt_delta": dados_delta["alt_delta"],
    }


# REGISTO DE COLETES / TDMA

def _make_vest_entry(vest_id, mac_address=None, net_id=DEFAULT_NET_ID):
    return {
        "vest_id": vest_id,
        "mac_address": mac_address,
        "net_id": net_id,
        "data_ch": DEFAULT_DATA_CH,
        "ctrl_ch": DEFAULT_CTRL_CH,
        "slot_index": vest_id - 1,
        "slot_start_s": (vest_id - 1) * TDMA_SLOT_SECONDS,
        "online": False,
        "last_seen": None,
        "last_frame": None,
        "last_cycle_seen": None,
        "last_retry_cycle": None,
        "last_retry_at": 0.0,
        "missed_alive": 0,
    }


def register_vest(vest_id, mac_address=None, net_id=DEFAULT_NET_ID):
    if vest_id not in registered_vests:
        registered_vests[vest_id] = _make_vest_entry(vest_id, mac_address, net_id)
    else:
        if mac_address is not None:
            registered_vests[vest_id]["mac_address"] = mac_address
        registered_vests[vest_id]["net_id"] = net_id


def mark_vest_online(vest_id):
    entry = registered_vests.setdefault(vest_id, _make_vest_entry(vest_id))
    entry["online"] = True
    entry["last_seen"] = time.time()
    entry["last_cycle_seen"] = current_cycle_index()
    entry["missed_alive"] = 0


def mark_vest_seen(vest_id, frame_type):
    entry = registered_vests.setdefault(vest_id, _make_vest_entry(vest_id))
    entry["online"] = True
    entry["last_seen"] = time.time()
    entry["last_frame"] = frame_type
    entry["last_cycle_seen"] = current_cycle_index()


def remove_vest(vest_id):
    registered_vests.pop(vest_id, None)
    memoria_gps.pop(vest_id, None)


def print_tdma_table():
    print(" TDMA TABLE ")
    if not registered_vests:
        print("  (sem coletes registados)")
    for vid in sorted(registered_vests):
        v = registered_vests[vid]
        status = "ONLINE" if v.get("online") else "OFFLINE"
        last = f"{time.time() - v['last_seen']:.1f}s atras" if v.get("last_seen") else "—"
        print(
            f"  Vest {vid:2d} | Slot {v['slot_index']:2d} | {v['slot_start_s']:.3f}s | "
            f"{status} | visto {last} | D={v['data_ch']} C={v['ctrl_ch']}"
        )


# ACK / RETRY WAITERS

def arm_ack_waiter(expected_vest_id, expected_funct):
    global pending_ack
    event = threading.Event()
    waiter = {
        "vest_id": expected_vest_id,
        "funct": expected_funct,
        "event": event,
        "matched": None,
    }
    with pending_ack_lock:
        pending_ack = waiter
    return waiter


def clear_ack_waiter(waiter):
    global pending_ack
    with pending_ack_lock:
        if pending_ack is waiter:
            pending_ack = None


def match_pending_ack(control):
    global pending_ack
    with pending_ack_lock:
        waiter = pending_ack
        if waiter is None:
            return False
        if control["opcode"] != OPCODE_ACK:
            return False
        if control["vest_id"] == waiter["vest_id"] and control["funct"] == waiter["funct"]:
            waiter["matched"] = control
            waiter["event"].set()
            pending_ack = None
            return True
    return False


def wait_for_ack(expected_vest_id, expected_funct, timeout=ACK_DEFAULT_TIMEOUT):
    validation_log("WAIT", f"Aguardando ACK | Vest={expected_vest_id} | Funct=0b{expected_funct:04b} | Timeout={timeout:.2f}s")
    waiter = arm_ack_waiter(expected_vest_id, expected_funct)
    matched = waiter["event"].wait(timeout)
    if matched:
        validation_log("ACK", f"ACK recebido | Vest={expected_vest_id} | Funct=0b{expected_funct:04b}")
        return True
    clear_ack_waiter(waiter)
    validation_log("ACK", f"Timeout ACK | Vest={expected_vest_id}")
    return False


def arm_retry_waiter(expected_vest_id, expected_kind):
    global pending_retry
    event = threading.Event()
    waiter = {
        "vest_id": expected_vest_id,
        "tipo": expected_kind,
        "event": event,
        "matched": None,
    }
    with pending_retry_lock:
        pending_retry = waiter
    return waiter


def clear_retry_waiter(waiter):
    global pending_retry
    with pending_retry_lock:
        if pending_retry is waiter:
            pending_retry = None


def match_pending_retry(decoded):
    global pending_retry
    with pending_retry_lock:
        waiter = pending_retry
        if waiter is None or decoded is None:
            return False
        if decoded.get("vest_id") != waiter["vest_id"]:
            return False
        if decoded.get("tipo") != waiter["tipo"]:
            return False
        waiter["matched"] = decoded
        waiter["event"].set()
        pending_retry = None
        return True


def wait_for_retry_response(expected_vest_id, expected_kind, timeout=RETRY_RESPONSE_TIMEOUT):
    validation_log("WAIT", f"Aguardando RETRY {expected_kind} | Vest={expected_vest_id} | Timeout={timeout:.2f}s")
    waiter = arm_retry_waiter(expected_vest_id, expected_kind)
    matched = waiter["event"].wait(timeout)
    if matched:
        validation_log("RETRY", f"Retransmissao {expected_kind} recebida do colete {expected_vest_id}.")
        return waiter["matched"]
    clear_retry_waiter(waiter)
    validation_log("RETRY", f"Timeout RETRY | Vest={expected_vest_id}")
    return None


# SYNC / UNSYNC / RETRY

def send_sync_process(new_vest_id, mac_address_int, target_net_id=DEFAULT_NET_ID, data_ch=DEFAULT_DATA_CH, ctrl_ch=DEFAULT_CTRL_CH):
    global current_team_data_ch, current_team_ctrl_ch
    raw_packet = create_sync_packet(new_vest_id, target_net_id, mac_address_int, data_ch, ctrl_ch)
    encrypted = encrypt_aes_ctr(raw_packet)

    register_vest(new_vest_id, mac_address=mac_address_int, net_id=target_net_id)
    registered_vests[new_vest_id]["data_ch"] = data_ch
    registered_vests[new_vest_id]["ctrl_ch"] = ctrl_ch
    current_team_data_ch = data_ch
    current_team_ctrl_ch = ctrl_ch

    wait_intervals = [5.0, 2.5, 1.0, 0.5, 0.25, 0.1, 0.1, 0.1]

    print(f"\n[SYNC] A iniciar SYNC -> Vest {new_vest_id} | MAC 0x{mac_address_int:08X} | Net {target_net_id}")
    validation_log("SYNC", f"Raw: {format_hex_bytes(raw_packet)}")
    validation_log("SYNC", f"Enc: {format_hex_bytes(encrypted)}")

    with sync_lock:
        hardware_set_role_sync()
        for attempt, wait_time in enumerate(wait_intervals, start=1):
            print(f"[SYNC] Tentativa {attempt}/8")
            validation_log("TX", f"Sending SYNC attempt {attempt}/8")
            hardware_send_packet(encrypted)
            if wait_for_ack(new_vest_id, FUNCT_ACK_SYNC, timeout=wait_time):
                mark_vest_online(new_vest_id)
                print(f"[SYNC] ✓ Colete {new_vest_id} entrou na rede.\n")
                return True

    print(f"[SYNC] ✗ Falha no SYNC do colete {new_vest_id}.\n")
    return False


def send_sync_async(new_vest_id, mac_address_int, target_net_id=DEFAULT_NET_ID, data_ch=DEFAULT_DATA_CH, ctrl_ch=DEFAULT_CTRL_CH):
    threading.Thread(
        target=send_sync_process,
        args=(new_vest_id, mac_address_int, target_net_id, data_ch, ctrl_ch),
        daemon=True,
        name=f"sync-vest{new_vest_id}",
    ).start()


def send_unsync_process(vest_id, funct=FUNCT_UNSYNC_MANUAL, timeout=3.0):
    vest = registered_vests.get(vest_id)
    if not vest:
        print(f"[UNSYNC] Colete {vest_id} nao esta registado.")
        return False

    packet = create_unsync_packet(vest["net_id"], vest_id, funct=funct)
    encrypted = encrypt_aes_ctr(packet)

    validation_log("UNSYNC", f"Raw: {format_hex_bytes(packet)}")
    validation_log("UNSYNC", f"Enc: {format_hex_bytes(encrypted)}")

    wait_s = seconds_until_next_slot(vest_id) + TDMA_SLOT_SECONDS * 0.05
    print(f"[UNSYNC] Aguardando {wait_s:.3f}s pela janela do colete {vest_id}...")
    time.sleep(wait_s)

    hardware_set_role_control(vest)
    validation_log("TX", f"Sending UNSYNC -> Vest {vest_id}")
    hardware_send_packet(encrypted)

    if wait_for_ack(vest_id, FUNCT_ACK_UNSYNC, timeout=timeout):
        remove_vest(vest_id)
        print(f"[UNSYNC] ✓ Colete {vest_id} removido da rede.\n")
        return True

    print(f"[UNSYNC] ✗ Sem confirmacao do colete {vest_id}.\n")
    return False


def send_unsync_async(vest_id, funct=FUNCT_UNSYNC_MANUAL):
    threading.Thread(
        target=send_unsync_process,
        args=(vest_id, funct),
        daemon=True,
        name=f"unsync-vest{vest_id}",
    ).start()


def send_retry_request(vest_id, ask_full=True):
    vest = registered_vests.get(vest_id)
    if not vest:
        print(f"[RETRY] Colete {vest_id} nao esta registado.")
        return None

    packet = create_retry_request_packet(vest["net_id"], vest_id, ask_full=ask_full)
    encrypted = encrypt_aes_ctr(packet)
    expected_kind = "COMPLETA" if ask_full else "DELTA_GPS"

    validation_log("RETRY", f"Raw: {format_hex_bytes(packet)}")
    validation_log("RETRY", f"Enc: {format_hex_bytes(encrypted)}")

    hardware_set_role_control(vest)
    validation_log("TX", f"Sending RETRY REQUEST -> Vest {vest_id} | kind={expected_kind}")
    hardware_send_packet(encrypted)

    retry_data = wait_for_retry_response(vest_id, expected_kind, timeout=RETRY_RESPONSE_TIMEOUT)
    if retry_data:
        vest["last_retry_cycle"] = current_cycle_index()
        vest["last_retry_at"] = time.time()
    return retry_data


def choose_retry_mode(vest):
    """Decide se o retry deve pedir FULL ou LIGHT com base no estado conhecido."""
    if vest.get("last_frame") == "DELTA_GPS" and vest["vest_id"] in memoria_gps:
        return False
    return True


# ALERTAS

def _handle_alert_flags(vest_id, flags):
    vest = registered_vests.get(vest_id, {})
    net_id = vest.get("net_id", DEFAULT_NET_ID)

    if flags.get("sos") or flags.get("mandown") or flags.get("vital"):
        labels = []
        if flags.get("sos"):
            labels.append("SOS")
        if flags.get("mandown"):
            labels.append("MAN DOWN")
        if flags.get("vital"):
            labels.append("VITAL")
        print(f"[ALERTA] Colete {vest_id}: {' | '.join(labels)}")
        ack = create_ack_packet(net_id, vest_id, FUNCT_ACK_ALERT)
        hardware_set_role_control(vest)
        hardware_send_packet(encrypt_aes_ctr(ack))
        validation_log("TX", f"ACK ALERT enviado -> Vest {vest_id}")

    if flags.get("bat"):
        print(f"[AVISO] Colete {vest_id}: BATERIA FRACA")
    if flags.get("hw"):
        print(f"[AVISO] Colete {vest_id}: FALHA DE HARDWARE")


def _dispatch_quick_alert(control):
    vest_id = control["vest_id"]
    funct = control["funct"]
    labels = {
        FUNCT_QUICK_SOS: "SOS",
        FUNCT_QUICK_MAN_DOWN: "MAN DOWN",
        FUNCT_QUICK_BAT_CRITICAL: "BATERIA CRITICA",
        FUNCT_QUICK_HW_FAIL: "FALHA DE HW",
    }
    label = labels.get(funct, f"ALERTA RAPIDO (funct=0b{funct:04b})")
    print(f"[ALERTA CRITICO] Colete {vest_id}: {label}")

    vest = registered_vests.get(vest_id, {})
    net_id = vest.get("net_id", DEFAULT_NET_ID)
    ack = create_ack_packet(net_id, vest_id, FUNCT_ACK_ALERT)
    hardware_set_role_control(vest)
    hardware_send_packet(encrypt_aes_ctr(ack))
    validation_log("TX", f"ACK QUICK ALERT enviado -> Vest {vest_id}")


# PROCESSAMENTO DE PACOTES RECEBIDOS

def parse_and_handle_packet(packet, emit_logs=True):
    tamanho = len(packet)
    decrypted = decrypt_aes_ctr(packet)

    if emit_logs:
        print(f"[RX] Raw ({tamanho} B): {format_hex_bytes(packet)}")
        print(f"[RX] Dec ({tamanho} B): {format_hex_bytes(decrypted)}")

    if tamanho == 15:
        dados = unpack_telemetria_completa(decrypted)
        if not dados:
            if emit_logs:
                print("[PARSER] Trama Completa invalida.\n")
            return None
        guardar_posicao_base(dados)
        mark_vest_seen(dados["vest_id"], "COMPLETA")
        if emit_logs:
            print(f"[DATA] COMPLETA | Net={dados['net_id']} | Vest={dados['vest_id']}")
            print(
                f"[DATA] BPM={dados['bpm']} | SpO2="
                f"{'ERR/<70%' if dados['spo2'] == 0 else str(dados['spo2']) + '%'} | "
                f"Temp={dados['temperature']:.2f}C | Alt={dados['altitude']}m"
            )
            print(f"[DATA] GPS={dados['lat']:.7f}, {dados['lon']:.7f}\n")
        _handle_alert_flags(dados["vest_id"], dados["flags"])
        match_pending_retry(dados)
        return dados

    if tamanho == 7:
        sync = unpack_sync_frame(decrypted)
        if sync:
            register_vest(sync["vest_id"], mac_address=sync["mac_address"], net_id=sync["target_net_id"])
            registered_vests[sync["vest_id"]]["data_ch"] = sync["data_ch"]
            registered_vests[sync["vest_id"]]["ctrl_ch"] = sync["ctrl_ch"]
            if emit_logs:
                print(
                    f"[DATA] SYNC | Vest={sync['vest_id']} | Net={sync['target_net_id']} | "
                    f"MAC=0x{sync['mac_address']:08X} | DataCH={sync['data_ch']} CtrlCH={sync['ctrl_ch']}\n"
                )
            return sync

        dados_delta = unpack_telemetria_delta(decrypted)
        if not dados_delta:
            if emit_logs:
                print("[PARSER] Trama de 7 bytes nao reconhecida.\n")
            return None
        dados = aplicar_delta_gps(dados_delta)
        if not dados:
            if emit_logs:
                print(f"[PARSER] Delta recebido para Vest {dados_delta['vest_id']} sem ancora base — ignorado.\n")
            return None
        mark_vest_seen(dados["vest_id"], "DELTA_GPS")
        if emit_logs:
            print(f"[DATA] DELTA | Net={dados['net_id']} | Vest={dados['vest_id']}")
            print(f"[DATA] GPS={dados['lat']:.7f}, {dados['lon']:.7f} | Alt={dados['altitude']}m ({dados['alt_delta']:+d}m)\n")
        _handle_alert_flags(dados["vest_id"], dados["flags"])
        match_pending_retry(dados)
        return dados

    if tamanho == 4:
        alert = unpack_bio_alert_frame(decrypted)
        if not alert:
            if emit_logs:
                print("[PARSER] Trama de alerta de 4 bytes invalida.\n")
            return None
        mark_vest_seen(alert["vest_id"], "ALERTA_BIO")
        if emit_logs:
            print(
                f"[DATA] ALERTA BIO | Net={alert['net_id']} | Vest={alert['vest_id']} | "
                f"Opcode=0b{alert['opcode']:04b} | Funct=0b{alert['funct']:04b} | Valor={alert['valor']}\n"
            )
        vest = registered_vests.get(alert["vest_id"], {})
        net_id = vest.get("net_id", DEFAULT_NET_ID)
        ack = create_ack_packet(net_id, alert["vest_id"], FUNCT_ACK_DATA_ALERT)
        hardware_set_role_control(vest)
        hardware_send_packet(encrypt_aes_ctr(ack))
        validation_log("TX", f"ACK DATA ALERT enviado -> Vest {alert['vest_id']}")
        return alert

    if tamanho == 3:
        hop = unpack_team_hop_frame(decrypted)
        if not hop:
            if emit_logs:
                print("[PARSER] Trama de 3 bytes nao reconhecida.\n")
            return None
        global current_team_data_ch, current_team_ctrl_ch
        current_team_data_ch = hop["data_ch"]
        current_team_ctrl_ch = hop["ctrl_ch"]
        for vest in registered_vests.values():
            if vest.get("net_id") == hop["net_id"]:
                vest["data_ch"] = hop["data_ch"]
                vest["ctrl_ch"] = hop["ctrl_ch"]
        if emit_logs:
            print(f"[DATA] TEAM HOP | Net={hop['net_id']} | DataCH={hop['data_ch']} CtrlCH={hop['ctrl_ch']}\n")
        return hop

    if tamanho == 2:
        control = unpack_control_frame(decrypted)
        if not control:
            if emit_logs:
                print("[PARSER] Trama de controlo de 2 bytes invalida.\n")
            return None
        if emit_logs:
            print(
                f"[DATA] CONTROL | Net={control['net_id']} | Vest={control['vest_id']} | "
                f"Opcode=0b{control['opcode']:04b} | Funct=0b{control['funct']:04b}"
            )

        if control["opcode"] == OPCODE_ACK:
            match_pending_ack(control)
        elif control["opcode"] == OPCODE_QUICK_ALERT:
            _dispatch_quick_alert(control)

        if emit_logs:
            print()
        return control

    return None


def listen_for_incoming_data():
    packet = hardware_receive_packet()
    if packet is None:
        return None
    return parse_and_handle_packet(packet, emit_logs=True)


# THREADS

def radio_listening_thread():
    while running:
        try:
            dados = listen_for_incoming_data()
            if dados:
                data_queue.put(dados)
        except Exception as exc:
            print(f"[RX THREAD] Erro: {exc}")
            time.sleep(1)


def network_alive_thread():
    while running:
        try:
            packet = create_network_alive_packet(DEFAULT_NET_ID)
            enc = encrypt_aes_ctr(packet)
            hardware_set_role_control()
            validation_log("TX", "Network Alive beacon enviado.")
            validation_log("NA", f"Enc: {format_hex_bytes(enc)}")
            hardware_send_packet(enc)
        except Exception as exc:
            print(f"[NA THREAD] Erro: {exc}")
        time.sleep(NETWORK_ALIVE_INTERVAL_S)


def retry_supervision_thread():
    while running:
        try:
            now = time.time()
            cycle_idx = current_cycle_index()
            cycle_pos = (now - tdma_epoch) % TDMA_CYCLE_SECONDS

            for vest_id, vest in list(registered_vests.items()):
                if not vest.get("online"):
                    continue

                slot_end = vest["slot_start_s"] + TDMA_SLOT_SECONDS + TDMA_SLOT_GUARD_SECONDS
                if cycle_pos < slot_end:
                    continue

                if vest.get("last_cycle_seen") == cycle_idx:
                    continue

                if vest.get("last_retry_cycle") == cycle_idx:
                    continue

                if now - vest.get("last_retry_at", 0.0) < RETRY_COOLDOWN_SECONDS:
                    continue

                ask_full = choose_retry_mode(vest)
                kind = "FULL" if ask_full else "LIGHT"
                validation_log("RETRY", f"Sem pacote no slot TDMA esperado para colete {vest_id} -> retry {kind}.")
                vest["last_retry_at"] = now
                vest["last_retry_cycle"] = cycle_idx
                send_retry_request(vest_id, ask_full=ask_full)

            time.sleep(0.05)
        except Exception as exc:
            print(f"[RETRY THREAD] Erro: {exc}")
            time.sleep(0.5)


# ===========================================================
# LOOP DE COMANDOS
# ===========================================================

HELP_TEXT = """
Comandos disponiveis:
  sync  <vest_id> <mac_hex8>          — Adicionar colete (ex: sync 3 99A498C4)
  unsync <vest_id>                    — Remover colete
  retry <vest_id> [full|light]        — Pedir retransmissao (default: full)
  alive                               — Enviar beacon Network Alive manual
  hop <data_ch> <ctrl_ch>             — Enviar Team Hop (ex: hop 3 5)
  table                               — Mostrar tabela TDMA
  quit                                — Encerrar
"""


def command_loop():
    print(HELP_TEXT)
    while running:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return

        if not line:
            continue

        parts = line.split()
        cmd = parts[0].lower()

        try:
            if cmd == "sync" and len(parts) == 3:
                vest_id = int(parts[1])
                mac = int(parts[2], 16)
                send_sync_async(vest_id, mac)

            elif cmd == "unsync" and len(parts) == 2:
                vest_id = int(parts[1])
                send_unsync_async(vest_id)

            elif cmd == "retry" and len(parts) >= 2:
                vest_id = int(parts[1])
                ask_full = len(parts) < 3 or parts[2].lower() != "light"
                send_retry_request(vest_id, ask_full)

            elif cmd == "alive":
                packet = create_network_alive_packet(DEFAULT_NET_ID)
                enc = encrypt_aes_ctr(packet)
                hardware_set_role_control()
                validation_log("TX", "Network Alive manual enviado.")
                hardware_send_packet(enc)

            elif cmd == "hop" and len(parts) == 3:
                global current_team_data_ch, current_team_ctrl_ch
                data_ch = int(parts[1])
                ctrl_ch = int(parts[2])
                packet = create_team_hop_packet(DEFAULT_NET_ID, data_ch, ctrl_ch)
                enc = encrypt_aes_ctr(packet)
                hardware_set_role_control()
                validation_log("TX", f"Team Hop -> DataCH={data_ch} CtrlCH={ctrl_ch}")
                hardware_send_packet(enc)
                current_team_data_ch = data_ch
                current_team_ctrl_ch = ctrl_ch
                for vest in registered_vests.values():
                    if vest.get("net_id") == DEFAULT_NET_ID:
                        vest["data_ch"] = data_ch
                        vest["ctrl_ch"] = ctrl_ch

            elif cmd == "table":
                print_tdma_table()

            elif cmd == "quit":
                return

            else:
                print("Comando invalido. Digite 'quit' para sair.")

        except (ValueError, IndexError) as exc:
            print(f"Erro de argumento: {exc}")
        except Exception as exc:
            print(f"Erro no comando: {exc}")


# MAIN

if __name__ == "__main__":
    try:
        open_hardware()
    except Exception as exc:
        print(f"\n[ERRO] Nao foi possivel inicializar o hardware LoRa: {exc}")
        raise SystemExit(1)

    rx_thread = threading.Thread(target=radio_listening_thread, daemon=True, name="rx-lora")
    na_thread = threading.Thread(target=network_alive_thread, daemon=True, name="network-alive")
    retry_thread = threading.Thread(target=retry_supervision_thread, daemon=True, name="retry-supervision")
    rx_thread.start()
    na_thread.start()
    retry_thread.start()

    print("\n╔══════════════════════════════════════════╗")
    print("║  Wearion ResQSense — Tablet LoRa Driver  ║")
    print("║  Transporte : SX1276 / Adafruit RFM95W   ║")
    print("║  Tramas     : 2 / 3 / 4 / 7 / 15 bytes   ║")
    print("║  Encriptacao: AES-128-CTR (IV fixo MVP)  ║")
    print("╚══════════════════════════════════════════╝\n")

    try:
        command_loop()
    finally:
        running = False
        time.sleep(0.3)
        if lora is not None:
            lora.close()
        print("\n[SYS] Encerrado.")
