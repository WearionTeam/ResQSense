import queue
import struct
import threading
import time

try:
    import serial
except Exception:  # pragma: no cover - fallback when dependency is missing
    serial = None

try:
    from Crypto.Cipher import AES
    from Crypto.Util import Counter
except Exception:  # pragma: no cover - fallback when dependency is missing
    AES = None
    Counter = None


# AES-128 key shared between tablet and vests.
AES_KEY = bytes([
    0x2B, 0x7E, 0x15, 0x16, 0x28, 0xAE, 0xD2, 0xA6,
    0xAB, 0xF7, 0x15, 0x88, 0x09, 0xCF, 0x4F, 0x3C,
])

# Fixed IV used in UART tests.
FIXED_IV = bytes([
    0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
    0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F,
])

FRAMING_BYTE = 0xAA
VALID_PACKET_LENGTHS = {2, 3, 4, 7, 15}

OPCODE_VITAL_ALERT = 0b1010
OPCODE_QUICK_ALERT = 0b1111

FUNCT_QUICK_SOS = 0b1111
FUNCT_QUICK_MAN_DOWN = 0b1100
FUNCT_QUICK_BAT_CRITICAL = 0b0110
FUNCT_QUICK_HW_FAIL = 0b0011

FUNCT_VITAL_BPM_HIGH = 0b1001
FUNCT_VITAL_BPM_LOW = 0b0110
FUNCT_VITAL_TEMP_HIGH = 0b0101
FUNCT_VITAL_TEMP_LOW = 0b1010
FUNCT_VITAL_SPO2_LOW = 0b0011


def _build_ctr():
    return Counter.new(128, initial_value=int.from_bytes(FIXED_IV, byteorder="big"))


def decrypt_aes_ctr(data):
    cipher = AES.new(AES_KEY, AES.MODE_CTR, counter=_build_ctr())
    return cipher.decrypt(data)


def split_control_byte(byte1):
    return (byte1 >> 4) & 0x0F, byte1 & 0x0F


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
    return raw_value / 10000000.0


def unpack_telemetria_completa(decrypted_data):
    if len(decrypted_data) != 15:
        return None

    net_id = (decrypted_data[0] >> 5) & 0x07
    vest_id = decrypted_data[0] & 0x1F
    flags = decrypted_data[1]
    flags_decoded = decode_flags(flags)
    if not flags_decoded["type_full"]:
        return None

    lat_raw, lon_raw, alt_raw, bpm = struct.unpack(">iihB", decrypted_data[2:13])
    packed_tail = (decrypted_data[13] << 8) | decrypted_data[14]
    spo2_raw = (packed_tail >> 11) & 0x1F
    temp_raw = packed_tail & 0x07FF

    return {
        "tipo": "COMPLETA",
        "net_id": net_id,
        "vest_id": vest_id,
        "flags": flags_decoded,
        "lat_raw": lat_raw,
        "lon_raw": lon_raw,
        "lat": format_coord(lat_raw),
        "lon": format_coord(lon_raw),
        "altitude": alt_raw,
        "bpm": bpm,
        "spo2": 0 if spo2_raw == 0 else spo2_raw + 69,
        "temperature": 25.00 + (temp_raw / 100.0),
    }


def unpack_telemetria_delta(decrypted_data):
    if len(decrypted_data) != 7:
        return None

    net_id = (decrypted_data[0] >> 5) & 0x07
    vest_id = decrypted_data[0] & 0x1F
    flags = decrypted_data[1]
    flags_decoded = decode_flags(flags)
    if flags_decoded["type_full"]:
        return None

    lat_frac16, lon_frac16, alt_delta = struct.unpack(">HHb", decrypted_data[2:7])
    return {
        "tipo": "DELTA_GPS",
        "net_id": net_id,
        "vest_id": vest_id,
        "flags": flags_decoded,
        "lat_frac16": lat_frac16,
        "lon_frac16": lon_frac16,
        "alt_delta": alt_delta,
    }


def unpack_control_frame(decrypted_data):
    if len(decrypted_data) != 2:
        return None

    net_id = (decrypted_data[0] >> 5) & 0x07
    vest_id = decrypted_data[0] & 0x1F
    opcode, funct = split_control_byte(decrypted_data[1])
    return {
        "tipo": "CONTROL",
        "net_id": net_id,
        "vest_id": vest_id,
        "opcode": opcode,
        "funct": funct,
    }


def unpack_bio_alert_frame(decrypted_data):
    if len(decrypted_data) != 4:
        return None

    net_id = (decrypted_data[0] >> 5) & 0x07
    vest_id = decrypted_data[0] & 0x1F
    opcode, funct = split_control_byte(decrypted_data[1])
    value = struct.unpack(">H", decrypted_data[2:4])[0]
    return {
        "tipo": "ALERTA_BIO",
        "net_id": net_id,
        "vest_id": vest_id,
        "opcode": opcode,
        "funct": funct,
        "valor": value,
    }


def map_warning_code(opcode, funct):
    if opcode == OPCODE_QUICK_ALERT:
        if funct == FUNCT_QUICK_MAN_DOWN:
            return "FALL"
        if funct == FUNCT_QUICK_BAT_CRITICAL:
            return "POOR CONNECTION"
        if funct == FUNCT_QUICK_HW_FAIL:
            return "POOR CONNECTION"
        if funct == FUNCT_QUICK_SOS:
            return "FALL"

    if opcode == OPCODE_VITAL_ALERT:
        if funct in (FUNCT_VITAL_BPM_HIGH, FUNCT_VITAL_BPM_LOW):
            return "HR HIGH"
        if funct == FUNCT_VITAL_TEMP_HIGH:
            return "TEMP HIGH"
        if funct == FUNCT_VITAL_SPO2_LOW:
            return "SpO2 LOW"
        if funct == FUNCT_VITAL_TEMP_LOW:
            return "TEMP HIGH"

    return None


def aplicar_delta_gps(dados_delta, memoria_gps):
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
    lat_graus = lat_abs_base // 10000000
    lon_graus = lon_abs_base // 10000000

    lat_frac_antiga = lat_abs_base % 10000000
    lon_frac_antiga = lon_abs_base % 10000000

    lat_frac_nova = int((dados_delta["lat_frac16"] * 9999999) / 65535)
    lon_frac_nova = int((dados_delta["lon_frac16"] * 9999999) / 65535)

    if lat_frac_antiga > 9000000 and lat_frac_nova < 1000000:
        lat_graus += 1
    elif lat_frac_antiga < 1000000 and lat_frac_nova > 9000000:
        lat_graus -= 1

    if lon_frac_antiga > 9000000 and lon_frac_nova < 1000000:
        lon_graus += 1
    elif lon_frac_antiga < 1000000 and lon_frac_nova > 9000000:
        lon_graus -= 1

    novo_lat_raw = lat_sign * (lat_graus * 10000000 + lat_frac_nova)
    novo_lon_raw = lon_sign * (lon_graus * 10000000 + lon_frac_nova)
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


class UARTTelemetryReceiver:
    def __init__(self, port="/dev/cu.usbserial-10", ports=None, baudrate=115200, timeout=0.2, reconnect_delay=1.0):
        if ports is None:
            if isinstance(port, str):
                parsed_ports = [item.strip() for item in port.split(",") if item.strip()]
            elif isinstance(port, (list, tuple, set)):
                parsed_ports = [str(item).strip() for item in port if str(item).strip()]
            else:
                parsed_ports = [str(port).strip()]
        elif isinstance(ports, str):
            parsed_ports = [item.strip() for item in ports.split(",") if item.strip()]
        else:
            parsed_ports = [str(item).strip() for item in ports if str(item).strip()]

        if not parsed_ports:
            raise ValueError("E necessario indicar pelo menos uma porta serie.")

        self.ports = parsed_ports
        self.baudrate = baudrate
        self.timeout = timeout
        self.reconnect_delay = reconnect_delay
        self.running = False
        self.rx_threads = []
        self.uarts = {}
        self.data_queue = queue.Queue()
        self.memoria_gps = {}
        self.memoria_lock = threading.Lock()

    def start(self):
        if serial is None:
            raise RuntimeError("Dependencia em falta: pyserial")
        if AES is None or Counter is None:
            raise RuntimeError("Dependencia em falta: pycryptodome")
        if self.running:
            return

        self.running = True
        self.uarts = {}
        self.rx_threads = []
        for port in self.ports:
            thread = threading.Thread(target=self._listen_port_thread, args=(port,), daemon=True)
            thread.start()
            self.rx_threads.append(thread)

    def stop(self):
        self.running = False

        for uart in list(self.uarts.values()):
            try:
                uart.close()
            except Exception:
                pass
        self.uarts = {}

        for thread in self.rx_threads:
            if thread and thread.is_alive():
                thread.join(timeout=1.0)
        self.rx_threads = []

    def drain(self, max_items=120):
        items = []
        for _ in range(max_items):
            try:
                items.append(self.data_queue.get_nowait())
            except queue.Empty:
                break
        return items

    def _open_uart(self, port):
        uart = serial.Serial(port, baudrate=self.baudrate, timeout=self.timeout)
        time.sleep(1.2)
        uart.reset_input_buffer()
        return uart

    def _listen_port_thread(self, port):
        uart = None
        while self.running:
            if uart is None:
                try:
                    uart = self._open_uart(port)
                    self.uarts[port] = uart
                    self.data_queue.put({"tipo": "PORT_STATUS", "port": port, "status": "connected"})
                except Exception:
                    uart = None
                    time.sleep(self.reconnect_delay)
                    continue

            try:
                packet = self._hardware_receive_framed_uart(uart)
                if packet is None:
                    continue

                decoded = self._parse_packet(packet)
                if decoded:
                    decoded["port"] = port
                    self.data_queue.put(decoded)
            except Exception:
                try:
                    uart.close()
                except Exception:
                    pass
                uart = None
                self.uarts.pop(port, None)
                self.data_queue.put({"tipo": "PORT_STATUS", "port": port, "status": "disconnected"})
                time.sleep(self.reconnect_delay)

        if uart is not None:
            try:
                uart.close()
            except Exception:
                pass
        self.uarts.pop(port, None)

    def _hardware_receive_framed_uart(self, uart):
        if uart is None:
            return None

        start = uart.read(1)
        if len(start) == 0:
            return None
        if start[0] != FRAMING_BYTE:
            return None

        length_raw = uart.read(1)
        if len(length_raw) != 1:
            return None

        packet_len = length_raw[0]
        if packet_len not in VALID_PACKET_LENGTHS:
            return None

        payload = uart.read(packet_len)
        if len(payload) != packet_len:
            return None
        return payload

    def _parse_packet(self, packet):
        decrypted = decrypt_aes_ctr(packet)
        tamanho = len(packet)

        if tamanho == 15:
            dados = unpack_telemetria_completa(decrypted)
            if not dados:
                return None
            with self.memoria_lock:
                self.memoria_gps[dados["vest_id"]] = {
                    "net_id": dados["net_id"],
                    "lat_raw": dados["lat_raw"],
                    "lon_raw": dados["lon_raw"],
                    "altitude": dados["altitude"],
                }
            return dados

        if tamanho == 7:
            delta = unpack_telemetria_delta(decrypted)
            if not delta:
                return None
            with self.memoria_lock:
                return aplicar_delta_gps(delta, self.memoria_gps)

        if tamanho == 4:
            alert = unpack_bio_alert_frame(decrypted)
            if not alert:
                return None
            alert["warning_code"] = map_warning_code(alert["opcode"], alert["funct"])
            return alert

        if tamanho == 2:
            control = unpack_control_frame(decrypted)
            if not control:
                return None
            control["warning_code"] = map_warning_code(control["opcode"], control["funct"])
            return control

        return None
