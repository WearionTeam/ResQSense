import queue
import threading
import time

from telemetry_protocol import (
    AES,
    AES_KEY,
    Counter,
    FIXED_IV,
    VALID_PACKET_LENGTHS,
    aplicar_delta_gps,
    decrypt_aes_ctr,
    map_warning_code,
    unpack_bio_alert_frame,
    unpack_control_frame,
    unpack_telemetria_completa,
    unpack_telemetria_delta,
)
from tdma_config import (
    TDMA_FRAME_DURATION_MS,
    TDMA_FRAME_SLOTS,
    TDMA_SLOT_DURATION_MS,
)

try:
    import spidev
except ImportError:
    spidev = None

try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None


CHANNEL_FREQUENCIES_HZ = {
    0: 868_100_000,
    1: 867_100_000,
    2: 868_300_000,
    3: 867_300_000,
    4: 867_500_000,
    5: 867_700_000,
    6: 867_900_000,
    7: 868_500_000,
}

OPCODE_SYNC = 0b0011
OPCODE_ACK = 0b1001
OPCODE_UNSYNC = 0b1100
FUNCT_ACK_SYNC = 0b0010
FUNCT_ACK_ALERT = 0b0100   # Confirmar um alerta (QALERT / VTALERT)
FUNCT_UNSYNC_MANUAL = 0b0110
DEFAULT_NET_ID = 0b001
DEFAULT_DATA_CH = 0b010
DEFAULT_CTRL_CH = 0b111


class TDMAScheduler:
    def __init__(self, slot_duration_ms=TDMA_SLOT_DURATION_MS, frame_slots=TDMA_FRAME_SLOTS):
        self.slot_duration_ms = int(slot_duration_ms)
        self.frame_slots = int(frame_slots)
        self.frame_duration_ms = self.slot_duration_ms * self.frame_slots
        self._t0_by_vest = {}
        self._lock = threading.RLock()

    def mark_sync(self, vest_id, t0=None):
        t0_value = time.monotonic() if t0 is None else float(t0)
        with self._lock:
            self._t0_by_vest[int(vest_id)] = t0_value
        return t0_value

    def clear(self, vest_id=None):
        with self._lock:
            if vest_id is None:
                self._t0_by_vest.clear()
            else:
                self._t0_by_vest.pop(int(vest_id), None)

    def get_t0(self, vest_id):
        with self._lock:
            return self._t0_by_vest.get(int(vest_id))

    def current_slot(self, vest_id):
        t0 = self.get_t0(vest_id)
        if t0 is None:
            return None
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return (elapsed_ms // self.slot_duration_ms) % self.frame_slots

    def ms_until_slot(self, vest_id, target_slot):
        t0 = self.get_t0(vest_id)
        if t0 is None:
            return None
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        position = elapsed_ms % self.frame_duration_ms
        target_ms = int(target_slot) * self.slot_duration_ms
        if target_ms <= position:
            target_ms += self.frame_duration_ms
        return target_ms - position

    def wait_until_slot(self, vest_id, target_slot, guard_ms=2):
        wait_ms = self.ms_until_slot(vest_id, target_slot)
        if wait_ms is None:
            return False
        sleep_ms = max(0, wait_ms - int(guard_ms))
        if sleep_ms:
            time.sleep(sleep_ms / 1000.0)
        return True

def _build_ctr():
    return Counter.new(128, initial_value=int.from_bytes(FIXED_IV, byteorder="big"))


def encrypt_aes_ctr(data):
    if AES is None or Counter is None:
        raise RuntimeError("Dependencia em falta: pycryptodome")
    cipher = AES.new(AES_KEY, AES.MODE_CTR, counter=_build_ctr())
    return cipher.encrypt(data)


def create_sync_packet(vest_id, mac_address, net_id=DEFAULT_NET_ID, data_ch=DEFAULT_DATA_CH, ctrl_ch=DEFAULT_CTRL_CH):
    byte0 = vest_id & 0x1F
    byte1 = ((OPCODE_SYNC & 0x0F) << 4) | (net_id & 0x07)
    mac = int(mac_address) & 0xFFFFFFFF
    mac_bytes = mac.to_bytes(4, byteorder="big")
    byte6 = ((data_ch & 0x07) << 5) | ((ctrl_ch & 0x07) << 1)
    return bytes([byte0, byte1]) + mac_bytes + bytes([byte6])


def create_control_packet(vest_id, opcode, funct, net_id=DEFAULT_NET_ID):
    byte0 = ((net_id & 0x07) << 5) | (int(vest_id) & 0x1F)
    byte1 = ((int(opcode) & 0x0F) << 4) | (int(funct) & 0x0F)
    return bytes([byte0, byte1])


class SX1276RFM9x:
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

    def __init__(self, spi_bus=0, spi_device=0, reset_pin=22, dio0_pin=25, spi_speed_hz=5_000_000):
        self.spi_bus = spi_bus
        self.spi_device = spi_device
        self.reset_pin = reset_pin
        self.dio0_pin = dio0_pin
        self.spi_speed_hz = spi_speed_hz
        self.spi = None
        self.current_channel = None

    def open(self):
        if spidev is None:
            raise RuntimeError("Dependencia em falta: spidev")
        if GPIO is None:
            raise RuntimeError("Dependencia em falta: RPi.GPIO")

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
        if version != 0x12:
            raise RuntimeError(f"RFM9x/SX1276 nao respondeu corretamente: 0x{version:02X}")

        self.sleep()
        self.write_reg(self.REG_OP_MODE, self.MODE_LONG_RANGE | self.MODE_SLEEP)
        time.sleep(0.01)
        self.standby()

        self.write_reg(self.REG_FIFO_TX_BASE_ADDR, 0x00)
        self.write_reg(self.REG_FIFO_RX_BASE_ADDR, 0x00)
        self.write_reg(self.REG_LNA, self.read_reg(self.REG_LNA) | 0x03)
        self.write_reg(self.REG_MODEM_CONFIG_3, 0x04)
        self.set_tx_power(14)
        self.set_lora_config(
            bandwidth=125_000,
            coding_rate=5,
            spreading_factor=7,
            preamble_len=8,
            crc_enabled=True,
            sync_word=0x12,
        )

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

    def burst_read(self, addr, length):
        return bytes(self.spi.xfer2([addr & 0x7F] + [0x00] * length)[1:])

    def burst_write(self, addr, data):
        self.spi.xfer2([addr | 0x80] + list(data))

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
        frequency_hz = CHANNEL_FREQUENCIES_HZ.get(channel_id)
        if frequency_hz is None:
            raise ValueError(f"Canal LoRa invalido: {channel_id}")
        self.standby()
        self.set_frequency(frequency_hz)
        self.current_channel = channel_id
        self.rx_continuous()

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
        self.write_reg(self.REG_MODEM_CONFIG_1, (bw_bits << 4) | (cr_bits << 1))
        self.write_reg(self.REG_MODEM_CONFIG_2, (sf_bits << 4) | (0x04 if crc_enabled else 0x00))
        self.write_reg(self.REG_PREAMBLE_MSB, (preamble_len >> 8) & 0xFF)
        self.write_reg(self.REG_PREAMBLE_LSB, preamble_len & 0xFF)
        self.write_reg(self.REG_SYNC_WORD, sync_word)
        if sf_bits == 6:
            self.write_reg(self.REG_DETECTION_OPTIMIZE, 0xC5)
            self.write_reg(self.REG_DETECTION_THRESHOLD, 0x0C)
        else:
            self.write_reg(self.REG_DETECTION_OPTIMIZE, 0xC3)
            self.write_reg(self.REG_DETECTION_THRESHOLD, 0x0A)

    def receive(self, timeout=0.1):
        deadline = time.time() + timeout
        while time.time() < deadline:
            irq = self.read_reg(self.REG_IRQ_FLAGS)
            if irq & self.IRQ_RX_DONE_MASK:
                if irq & self.IRQ_PAYLOAD_CRC_ERROR_MASK:
                    self.write_reg(self.REG_IRQ_FLAGS, 0xFF)
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

    def send(self, payload, timeout=2.0):
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
            if irq & 0x08:
                self.write_reg(self.REG_IRQ_FLAGS, 0x08)
                self.rx_continuous()
                return True
            time.sleep(0.005)

        self.rx_continuous()
        return False


class LoRaTelemetryReceiver:
    def __init__(self, channel=7, spi_bus=0, spi_device=0, reset_pin=22, dio0_pin=25, spi_speed_hz=5_000_000):
        self.channel = int(channel)
        self.radio = SX1276RFM9x(
            spi_bus=spi_bus,
            spi_device=spi_device,
            reset_pin=reset_pin,
            dio0_pin=dio0_pin,
            spi_speed_hz=spi_speed_hz,
        )
        self.running = False
        self.thread = None
        self.radio_lock = threading.RLock()
        self.data_queue = queue.Queue()
        self.memoria_gps = {}
        self.memoria_lock = threading.Lock()
        self.pending_ack = None
        self.pending_ack_lock = threading.Lock()
        self.scheduler = TDMAScheduler()
        self.sync_records = {}
        self.sync_records_lock = threading.RLock()
        self.last_uplink_by_vest = {}
        self.uplink_condition = threading.Condition()
        self.pending_controls_by_vest = {}
        self.pending_controls_lock = threading.RLock()

    def start(self):
        if self.running:
            return
        self.radio.open()
        self.radio.set_channel(self.channel)
        self.running = True
        self.thread = threading.Thread(target=self._listen_thread, daemon=True)
        self.thread.start()
        print(f"[LORA] Receiver ativo no canal {self.channel}")
        self.data_queue.put({"tipo": "PORT_STATUS", "port": f"LoRa CH{self.channel}", "status": "connected"})

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        self.thread = None
        self.scheduler.clear()
        self.radio.close()

    def drain(self, max_items=120):
        items = []
        for _ in range(max_items):
            try:
                items.append(self.data_queue.get_nowait())
            except queue.Empty:
                break
        return items

    def send_sync(self, vest_id, mac_address, net_id=DEFAULT_NET_ID, data_ch=DEFAULT_DATA_CH, ctrl_ch=DEFAULT_CTRL_CH):
        raw = create_sync_packet(vest_id, mac_address, net_id=net_id, data_ch=data_ch, ctrl_ch=ctrl_ch)
        encrypted = encrypt_aes_ctr(raw)
        waiter = self._arm_ack_waiter(vest_id, FUNCT_ACK_SYNC)
        # A RAK abre janelas de RX enquanto não está sincronizada; repetir o SYNC
        # durante mais tempo torna o handshake resistente a desencontros de timing.
        wait_intervals = [1.2] * 15

        with self.radio_lock:
            previous_channel = self.radio.current_channel
            self.radio.set_channel(0)
            print(f"[LORA] SYNC iniciado para Vest {vest_id} MAC=0x{int(mac_address):08X}")

        try:
            for attempt, wait_time in enumerate(wait_intervals, start=1):
                with self.radio_lock:
                    self.radio.set_channel(0)
                    ok = self.radio.send(encrypted)
                    self.radio.set_channel(ctrl_ch)
                print(f"[LORA] SYNC tentativa {attempt}/{len(wait_intervals)} para Vest {vest_id}")
                if ok and self._wait_for_sync_ack(waiter, wait_time):
                    self._remember_sync_params(vest_id, mac_address, net_id, data_ch, ctrl_ch)
                    with self.radio_lock:
                        self.radio.set_channel(data_ch)
                    print(f"[LORA] ACK SYNC recebido. Rececao passou para canal de dados {data_ch}.")
                    return True
            if self._wait_for_sync_ack(waiter, 2.0):
                self._remember_sync_params(vest_id, mac_address, net_id, data_ch, ctrl_ch)
                with self.radio_lock:
                    self.radio.set_channel(data_ch)
                print(f"[LORA] ACK SYNC recebido. Rececao passou para canal de dados {data_ch}.")
                return True
        finally:
            self._clear_ack_waiter(waiter)

        with self.radio_lock:
            if previous_channel is not None:
                self.radio.set_channel(previous_channel)
        print(f"[LORA] SYNC sem ACK para Vest {vest_id}")
        return False

    def _remember_sync_params(self, vest_id, mac_address, net_id, data_ch, ctrl_ch):
        with self.sync_records_lock:
            self.sync_records[int(vest_id)] = {
                "mac_address": int(mac_address),
                "net_id": int(net_id),
                "data_ch": int(data_ch),
                "ctrl_ch": int(ctrl_ch),
            }

    def send_control_frame(self, vest_id, opcode, funct, net_id=DEFAULT_NET_ID, ctrl_ch=None, data_ch=None):
        payload = create_control_packet(vest_id, opcode, funct, net_id=net_id)
        return self.send_control_to_vest(vest_id, payload, ctrl_ch=ctrl_ch, data_ch=data_ch)

    def send_unsync(self, vest_id, funct=FUNCT_UNSYNC_MANUAL, rx_window_timeout=12.0, ack_timeout=3.0):
        with self.sync_records_lock:
            sync_record = self.sync_records.get(int(vest_id), {})

        net_id = sync_record.get("net_id", DEFAULT_NET_ID)
        ctrl_ch = sync_record.get("ctrl_ch", DEFAULT_CTRL_CH)
        data_ch = sync_record.get("data_ch", self.channel)
        payload = create_control_packet(vest_id, OPCODE_UNSYNC, funct, net_id=net_id)
        waiter = self._arm_ack_waiter(vest_id, (funct, FUNCT_ACK_SYNC))

        try:
            ok = self.send_control_to_vest(
                vest_id,
                payload,
                ctrl_ch=ctrl_ch,
                data_ch=data_ch,
                rx_window_timeout=rx_window_timeout,
            )
            ack_ok = ok and self._wait_for_ack(waiter, ack_timeout, label="UNSYNC")
            if ack_ok:
                self.scheduler.clear(vest_id)
                with self.sync_records_lock:
                    self.sync_records.pop(int(vest_id), None)
                print(f"[LORA] UNSYNC confirmado para Vest {vest_id}.")
                return True

            print(f"[LORA] UNSYNC enviado sem ACK para Vest {vest_id}.")
            return False
        finally:
            self._clear_ack_waiter(waiter)
            with self.radio_lock:
                self.radio.set_channel(data_ch)

    def send_control_to_vest(self, vest_id, payload, ctrl_ch=None, data_ch=None, rx_window_timeout=12.0, encrypted=False):
        payload_to_send = bytes(payload) if encrypted else encrypt_aes_ctr(bytes(payload))
        with self.sync_records_lock:
            sync_record = self.sync_records.get(int(vest_id), {})
        target_ctrl_ch = int(ctrl_ch if ctrl_ch is not None else sync_record.get("ctrl_ch", DEFAULT_CTRL_CH))
        target_data_ch = int(data_ch if data_ch is not None else sync_record.get("data_ch", self.channel))

        pending = {
            "vest_id": int(vest_id),
            "payload": payload_to_send,
            "ctrl_ch": target_ctrl_ch,
            "data_ch": target_data_ch,
            "event": threading.Event(),
            "ok": False,
        }
        self._queue_pending_control(pending)
        print(f"[LORA] Controlo em fila para Vest {vest_id}; aguarda proxima janela RX.")

        if pending["event"].wait(timeout=float(rx_window_timeout)):
            return bool(pending["ok"])

        self._remove_pending_control(pending)
        print(f"[LORA] Controlo nao enviado para Vest {vest_id}: janela RX nao abriu.")
        return False

    def _mark_vest_uplink(self, vest_id):
        with self.uplink_condition:
            self.last_uplink_by_vest[int(vest_id)] = time.monotonic()
            self.uplink_condition.notify_all()
        self._flush_pending_controls_for_vest(vest_id)

    def _queue_pending_control(self, pending):
        with self.pending_controls_lock:
            vest_key = int(pending["vest_id"])
            self.pending_controls_by_vest.setdefault(vest_key, []).append(pending)

    def _remove_pending_control(self, pending):
        with self.pending_controls_lock:
            vest_key = int(pending["vest_id"])
            controls = self.pending_controls_by_vest.get(vest_key)
            if not controls:
                return
            try:
                controls.remove(pending)
            except ValueError:
                return
            if not controls:
                self.pending_controls_by_vest.pop(vest_key, None)

    def _flush_pending_controls_for_vest(self, vest_id):
        vest_key = int(vest_id)
        with self.pending_controls_lock:
            controls = self.pending_controls_by_vest.pop(vest_key, [])

        for pending in controls:
            try:
                with self.radio_lock:
                    previous_channel = self.radio.current_channel
                    self.radio.set_channel(pending["ctrl_ch"])
                    ok = self.radio.send(pending["payload"])
                    self.radio.set_channel(pending["data_ch"] if pending["data_ch"] is not None else previous_channel)
            except Exception as exc:
                ok = False
                print(f"[LORA] Erro ao enviar controlo para Vest {vest_id}: {exc}")
            pending["ok"] = ok
            pending["event"].set()
            print(f"[LORA] Controlo para Vest {vest_id} na janela RX: {'ok' if ok else 'falhou'}")

    def _wait_for_vest_rx_window(self, vest_id, timeout=12.0):
        vest_key = int(vest_id)
        requested_at = time.monotonic()
        deadline = requested_at + float(timeout)
        with self.uplink_condition:
            while self.running and time.monotonic() < deadline:
                last_uplink = self.last_uplink_by_vest.get(vest_key, 0.0)
                if last_uplink >= requested_at:
                    return True
                remaining = deadline - time.monotonic()
                self.uplink_condition.wait(timeout=max(0.01, min(0.2, remaining)))
        return False

    def _arm_ack_waiter(self, vest_id, funct):
        if isinstance(funct, (tuple, list, set)):
            funct_value = tuple(int(item) for item in funct)
        else:
            funct_value = int(funct)
        waiter = {
            "vest_id": int(vest_id),
            "funct": funct_value,
            "event": threading.Event(),
            "matched": None,
        }
        with self.pending_ack_lock:
            self.pending_ack = waiter
        return waiter

    def _clear_ack_waiter(self, waiter):
        with self.pending_ack_lock:
            if self.pending_ack is waiter:
                self.pending_ack = None

    def _match_pending_ack(self, control):
        with self.pending_ack_lock:
            waiter = self.pending_ack
            if waiter is None:
                return False
            if control.get("opcode") != OPCODE_ACK:
                return False
            expected_funct = waiter["funct"]
            if isinstance(expected_funct, tuple):
                funct_matches = control.get("funct") in expected_funct
            else:
                funct_matches = control.get("funct") == expected_funct
            if control.get("vest_id") == waiter["vest_id"] and funct_matches:
                waiter["matched"] = control
                if control.get("funct") == FUNCT_ACK_SYNC:
                    waiter["tdma_t0"] = self.scheduler.mark_sync(waiter["vest_id"])
                waiter["event"].set()
                self.pending_ack = None
                return True
        return False

    def _wait_for_sync_ack(self, waiter, timeout):
        return self._wait_for_ack(waiter, timeout, label="SYNC")

    def _wait_for_ack(self, waiter, timeout, label="ACK"):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if waiter["event"].is_set():
                return True

            with self.radio_lock:
                packet = self.radio.receive(timeout=0.08)

            if packet is None:
                continue
            if len(packet) != 2:
                print(f"[LORA] Pacote recebido durante {label} ignorado: {len(packet)} bytes")
                continue

            decrypted = decrypt_aes_ctr(packet)
            control = unpack_control_frame(decrypted)
            if not control:
                print(f"[LORA] ACK invalido durante {label}: {packet.hex(' ')}")
                continue

            print(f"[LORA] RX durante {label}: {control}")
            control["port"] = f"LoRa CH{self.channel}"
            control["warning_code"] = map_warning_code(control["opcode"], control["funct"])
            self.data_queue.put(control)

            if self._match_pending_ack(control):
                return True

        return waiter["event"].is_set()

    def _send_alert_ack(self, vest_id, net_id):
        """Envia ACK ao colete para confirmar receção do alerta (QALERT/VTALERT).
        Chamado imediatamente após _parse_packet receber um pacote de 4 bytes,
        enquanto o colete ainda tem a janela RX aberta (RX_WINDOW_DATA_INTERVAL=200ms).
        """
        try:
            with self.sync_records_lock:
                sync_record = self.sync_records.get(int(vest_id), {})
            ctrl_ch = sync_record.get("ctrl_ch", self.channel)
            data_ch = sync_record.get("data_ch", self.channel)
            net = sync_record.get("net_id", int(net_id))

            payload = create_control_packet(vest_id, OPCODE_ACK, FUNCT_ACK_ALERT, net_id=net)
            encrypted = encrypt_aes_ctr(payload)

            with self.radio_lock:
                self.radio.set_channel(ctrl_ch)
                sent = self.radio.send(encrypted)
                self.radio.set_channel(data_ch)

            if sent:
                print(f"[LORA] ACK enviado ao Vest {vest_id} (alerta confirmado)")
            else:
                print(f"[LORA] ACK para Vest {vest_id} nao confirmado (TX timeout)")
        except Exception as exc:
            print(f"[LORA] Erro ao enviar ACK de alerta para Vest {vest_id}: {exc}")

    def _listen_thread(self):
        while self.running:
            try:
                with self.radio_lock:
                    packet = self.radio.receive(timeout=0.1)
                if packet is None:
                    continue
                print(f"[LORA] RX {len(packet)} bytes: {packet.hex(' ')}")
                if len(packet) not in VALID_PACKET_LENGTHS:
                    print(f"[LORA] Pacote ignorado: tamanho invalido {len(packet)}")
                    continue
                decoded = self._parse_packet(packet)
                if decoded:
                    print(f"[LORA] Decodificado: {decoded}")
                    decoded["port"] = f"LoRa CH{self.channel}"
                    self.data_queue.put(decoded)
                else:
                    print("[LORA] Pacote recebido mas nao reconhecido pelo protocolo")
            except Exception as exc:
                self.data_queue.put({"tipo": "PORT_STATUS", "port": f"LoRa CH{self.channel}", "status": f"error: {exc}"})
                time.sleep(1.0)

    def _parse_packet(self, packet):
        decrypted = decrypt_aes_ctr(packet)
        size = len(packet)

        if size == 15:
            data = unpack_telemetria_completa(decrypted)
            if not data:
                return None
            self._mark_vest_uplink(data["vest_id"])
            with self.memoria_lock:
                self.memoria_gps[data["vest_id"]] = {
                    "net_id": data["net_id"],
                    "lat_raw": data["lat_raw"],
                    "lon_raw": data["lon_raw"],
                    "altitude": data["altitude"],
                }
            return data

        if size == 7:
            delta = unpack_telemetria_delta(decrypted)
            if not delta:
                return None
            self._mark_vest_uplink(delta["vest_id"])
            with self.memoria_lock:
                return aplicar_delta_gps(delta, self.memoria_gps)

        if size == 4:
            alert = unpack_bio_alert_frame(decrypted)
            if not alert:
                return None
            alert["warning_code"] = map_warning_code(alert["opcode"], alert["funct"])
            # O colete aguarda ACK para parar de retransmitir (ACK_TIMEOUT_MS=2500, 3 tentativas).
            # Enviamos imediatamente para evitar repetições.
            self._send_alert_ack(alert["vest_id"], alert.get("net_id", DEFAULT_NET_ID))
            return alert

        if size == 2:
            control = unpack_control_frame(decrypted)
            if not control:
                return None
            self._match_pending_ack(control)
            control["warning_code"] = map_warning_code(control["opcode"], control["funct"])
            return control

        return None
