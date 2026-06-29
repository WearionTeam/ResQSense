import queue
import struct
import threading
import time

import serial
from Crypto.Cipher import AES
from Crypto.Util import Counter


# ===========================================================
# CONFIGURACOES GLOBAIS

# Chave AES-128 que é partilhada entre o tablet e os coletes.
# Como para já ainda estamos a testar apenas via UART, a chave fica fixa nos dois lados.
AES_KEY = bytes([
    0x2B, 0x7E, 0x15, 0x16, 0x28, 0xAE, 0xD2, 0xA6,
    0xAB, 0xF7, 0x15, 0x88, 0x09, 0xCF, 0x4F, 0x3C
])

# IV/contador inicial fixo para testes UART.
# Com o radio isto deve deixar de ser constante em todos os pacotes.
FIXED_IV = bytes([
    0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
    0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F
])

# Aqui definimos o Modo de teste (por enquanto apenas UART), a porta para o teste (PC ou RASP) e o baudrate 
MODO_HARDWARE = "UART"
PORTA_UART = "/dev/cu.usbserial-10"
BAUDRATE = 115200

# Este byte serve para mostrar onde o pacote comeca.
# Aqui definimos tambem todos os tamanhos possiveis de pacote que a rede tem:
# 15 bytes - Trama Completa
# 7 bytes - Trama Delta ou Trama de SYNC
# 4 bytes - Trama de Alerta Biometrico
# 3 bytes - Trama TEAM HOP
# 2 bytes - Trama de Controlo ou Trama CONCEDE HOP
FRAMING_BYTE = 0xAA
VALID_PACKET_LENGTHS = {2, 3, 4, 7, 15}

# Valores usados apenas nos testes UART.
# Na versao final estes parametros devem ser atribuidos de forma dinamica pelo tablet.
DEFAULT_NET_ID = 0b001
DEFAULT_DATA_CH = 0b010
DEFAULT_CTRL_CH = 0b111

# O ciclo TDMA da rede dura 10 segundos.
# O sistema foi pensado para suportar ate 30 coletes.
# Com isto, o tempo de cada slot e calculado pelo tempo total do ciclo a dividir pelo numero maximo de coletes.
TDMA_CYCLE_SECONDS = 10.0
TDMA_MAX_VESTS = 30
TDMA_SLOT_SECONDS = TDMA_CYCLE_SECONDS / TDMA_MAX_VESTS

# ===========================================================
# OPCODES / FUNCTS

# Tabela de opcodes da Network Specification V1.1.
OPCODE_SYNC = 0b0011
OPCODE_NETWORK_ALIVE = 0b0101
OPCODE_RETRY_REQUEST = 0b0110
OPCODE_ACK = 0b1001
OPCODE_VITAL_ALERT = 0b1010
OPCODE_UNSYNC = 0b1100
OPCODE_QUICK_ALERT = 0b1111

# Aqui definimos os functs principais usados pelo tablet.
# Alguns ainda nao estao a ser usados nos testes, mas ja ficam preparados no protocolo.
FUNCT_ACK_ALERT = 0b0110
FUNCT_ACK_SYNC = 0b0010

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

FUNCT_NETWORK_ALIVE_BEACON = 0b0000
FUNCT_NETWORK_ALIVE_WAKE_UWB = 0b1001
FUNCT_NETWORK_ALIVE_CONCEDE_HOP = 0b1100
FUNCT_NETWORK_ALIVE_TEAM_HOP = 0b0011

# ===========================================================
# ESTADO

# Esta variavel vai guardar a ligacao à porta serie UART depois de ela ser aberta.
uart = None
# `data_queue` permite guardar o que foi recebido e processado sem bloquear a thread.
data_queue = queue.Queue()
# `memoria_gps` guarda a ultima posicao completa de cada colete para reconstruir as tramas deltas.
memoria_gps = {}

# Aqui guardamos a informacao de todos os coletes que o tablet conhece na rede.
registered_vests = {}
# Este lock evita conflitos quando duas partes do programa tentam mexer no processo de sync ao mesmo tempo.
sync_lock = threading.Lock()
# Esta variavel diz se o programa deve continuar a correr.
running = True
# Guarda o instante em que o ciclo TDMA comecou, para depois calcular os slots de cada colete.
tdma_epoch = time.time()

# ===========================================================
# CRIPTOGRAFIA

def _build_ctr():
    # No modo AES-CTR o cifrador depende da chave e do contador inicial.
    return Counter.new(128, initial_value=int.from_bytes(FIXED_IV, byteorder="big"))


def encrypt_aes_ctr(data):
    # CTR cifra e decifra da mesma forma
    cipher = AES.new(AES_KEY, AES.MODE_CTR, counter=_build_ctr())
    return cipher.encrypt(data)


def decrypt_aes_ctr(data):
    # O recetor precisa do mesmo contador que o emissor usou.
    cipher = AES.new(AES_KEY, AES.MODE_CTR, counter=_build_ctr())
    return cipher.decrypt(data)

# ===========================================================
# CAMADA DE HARDWARE

def open_hardware():
    # Esta funcao inicia o meio de comunicacao que estiver a ser usado.
    # Para ja usa UART, mas no futuro e aqui que se faz a adaptacao para o LoRa.
    if MODO_HARDWARE == "UART":
        open_uart()
    else:
        raise NotImplementedError(f"Modo de hardware ainda nao esta implementado: {MODO_HARDWARE}")


def open_uart():
    global uart
    # Abre a porta UART com timeout de 1 segundo.
    uart = serial.Serial(PORTA_UART, baudrate=BAUDRATE, timeout=1.0)
    # Muitas ESPs reiniciam quando a porta abre, esta pausa garante que a comunicacao fica estavel.
    time.sleep(2)
    # Limpa o buffer para nao ficarem bytes antigos na leitura.
    uart.reset_input_buffer()
    print(f"Modo UART inicializado com sucesso na porta {PORTA_UART}")


def hardware_send_packet(payload):
    # Esta funcao faz a ligacao entre a logica do protocolo e o meio de comunicacao.
    if MODO_HARDWARE == "UART":
        hardware_send_framed_uart(payload)
        return
    raise NotImplementedError(f"Modo de hardware nao suportado: {MODO_HARDWARE}")


def hardware_receive_packet():
    # Para o protocolo, a forma de receber o pacote deve ser sempre a mesma, independentemente do hardware.
    if MODO_HARDWARE == "UART":
        return hardware_receive_framed_uart()
    raise NotImplementedError(f"Modo de hardware nao suportado: {MODO_HARDWARE}")


def hardware_set_role_data():
    # Em UART nao existe troca fisica de canal.
    # Esta funcao existe para a futuramente usarmos o LoRa.
    pass


def hardware_set_role_control():
    # Em UART nao existe troca fisica de canal.
    # Esta funcao existe para a futuramente usarmos o LoRa.
    pass


def hardware_send_framed_uart(payload):
    if uart is None:
        return
    # Envia primeiro o byte de inicio, depois o tamanho e so no fim os dados do pacote.
    uart.write(bytes([FRAMING_BYTE, len(payload)]) + payload)


def hardware_receive_framed_uart():
    if uart is None:
        return None

    while True:
        # Procura o byte de inicio do framing para sincronizar a stream.
        start = uart.read(1)
        if len(start) == 0:
            return None
        if start[0] != FRAMING_BYTE:
            continue

        # O byte seguinte indica quantos bytes de payload devem ser lidos.
        length_raw = uart.read(1)
        if len(length_raw) != 1:
            return None

        packet_len = length_raw[0]
        # So aceitamos tamanhos validos do protocolo.
        if packet_len not in VALID_PACKET_LENGTHS:
            continue

        payload = uart.read(packet_len)
        if len(payload) != packet_len:
            return None
        return payload


# ===========================================================
# FUNCOES AUXILIARES DO PROTOCOLO

def make_header(net_id, vest_id):
    # Byte 0 das tramas: 3 bits de Net ID + 5 bits de Vest ID.
    return ((net_id & 0x07) << 5) | (vest_id & 0x1F)


def split_control_byte(byte1):
    # Byte 1 das tramas de controlo: 4 bits de opcode + 4 bits de funct.
    return (byte1 >> 4) & 0x0F, byte1 & 0x0F


def make_control_byte(opcode, funct):
    return ((opcode & 0x0F) << 4) | (funct & 0x0F)


def decode_flags(flags):
    # Byte 1 das tramas de dados: T S M V B H P F
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
    # A especificacao usa graus decimais multiplicados por 10^7.
    return raw_value / 10000000.0


def slot_start_for_vest(vest_id):
    # Esta funcao futuramente vai servir para calcular com mais detalhe o inicio do slot de cada colete.
    pass


def seconds_until_next_slot(vest_id):
    # Esta funcao calcula quanto tempo falta ate ao proximo slot desse colete.
    now = time.time()
    base = tdma_epoch
    cycle_pos = (now - base) % TDMA_CYCLE_SECONDS
    slot_pos = (vest_id - 1) * TDMA_SLOT_SECONDS
    wait = slot_pos - cycle_pos
    if wait < 0:
        wait += TDMA_CYCLE_SECONDS
    return wait

# ===========================================================
# CONSTRUCAO DE TRAMAS

def create_sync_packet(new_vest_id, target_net_id, mac_address_int, data_ch, ctrl_ch):
    # SYNC é uma trama de 7 bytes enviada com Net ID 000 no "canal 0".
    # O tablet diz ao colete: "o teu novo Vest ID e X e a tua rede/canais passam a ser Y".
    byte0 = make_header(0b000, new_vest_id)
    byte1 = (OPCODE_SYNC << 4) | (target_net_id & 0x07)
    mac_bytes = struct.pack(">I", mac_address_int)
    byte6 = ((data_ch & 0x07) << 5) | ((ctrl_ch & 0x07) << 1)
    return bytes([byte0, byte1]) + mac_bytes + bytes([byte6])


def create_unsync_packet(net_id, vest_id, funct=FUNCT_UNSYNC_MANUAL):
    # UNSYNC é a trama de 2 bytes que serve para retirar um colete da rede.
    return bytes([
        make_header(net_id, vest_id),
        make_control_byte(OPCODE_UNSYNC, funct),
    ])


def create_ack_packet(net_id, vest_id, funct):
    # Esta funcao fica preparada para o tablet poder criar os ACKs.
    pass


def create_retry_request_packet(net_id, vest_id, ask_full=True):
    # Esta funcao fica preparada, para mais tarde criar tramas de retry.
    pass


def create_network_alive_packet(net_id):
    # Esta funcao fica preparada para mais tarde criar a mensagem de Network Alive.
    pass


def create_team_hop_packet(net_id, data_ch, ctrl_ch):
    # Esta funcao fica preparada para a futura trama Team Hop.
    pass


# ===========================================================
# LEITURA E INTERPRETACAO DAS TRAMAS RECEBIDAS

def unpack_telemetria_completa(decrypted_data):
    # Trama de telemetria completa.
    if len(decrypted_data) != 15:
        return None

    net_id = (decrypted_data[0] >> 5) & 0x07
    vest_id = decrypted_data[0] & 0x1F
    flags = decrypted_data[1]
    flags_decoded = decode_flags(flags)
    if not flags_decoded["type_full"]:
        return None

    # Campo central da trama: latitude, longitude, altitude e BPM.
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
    # Trama delta de 7 bytes.
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
    # Trama base de controlo/alerta: 2 bytes.
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


def unpack_team_hop_frame(decrypted_data):
    # Esta funcao fica preparada para interpretar a trama Team Hop mais tarde.
    pass


def unpack_bio_alert_frame(decrypted_data):
    # Alerta biometrico inclui o valor exato do sensor em 16 bits.
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


def unpack_sync_frame(decrypted_data):
    # Esta funcao fica preparada para ler uma trama SYNC recebida.
    pass


# ===========================================================
# ESTADO GPS

def guardar_posicao_base(dados):
    # A ultima trama completa é guardada para depois "construir" as futuras tramas delta.
    memoria_gps[dados["vest_id"]] = {
        "net_id": dados["net_id"],
        "lat_raw": dados["lat_raw"],
        "lon_raw": dados["lon_raw"],
        "altitude": dados["altitude"],
    }


def aplicar_delta_gps(dados_delta):
    # Reconstrucao da coordenada final com os graus da ultima trama completa
    # e com a parte fracionaria atual recebida na trama delta.
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

    # Quando a parte fracionaria "da a volta", ajustamos o grau.
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


# ===========================================================
# REGISTO DE COLETES / TDMA

def register_vest(vest_id, mac_address=None, net_id=DEFAULT_NET_ID):
    # O slot TDMA de cada colete é definido a partir do seu Vest ID.
    registered_vests[vest_id] = {
        "vest_id": vest_id,
        "mac_address": mac_address,
        "net_id": net_id,
        "slot_index": vest_id - 1,
        "slot_start_s": (vest_id - 1) * TDMA_SLOT_SECONDS,
        "online": False,
        "last_seen": None,
        "last_frame_type": None,
    }


def mark_vest_online(vest_id):
    # Marca o colete como ativo quando ele entra na rede ou quando volta a comunicar.
    vest = registered_vests.setdefault(vest_id, {
        "vest_id": vest_id,
        "mac_address": None,
        "net_id": DEFAULT_NET_ID,
        "slot_index": vest_id - 1,
        "slot_start_s": (vest_id - 1) * TDMA_SLOT_SECONDS,
    })
    vest["online"] = True
    vest["last_seen"] = time.time()


def mark_vest_seen(vest_id, frame_type):
    # Atualiza a ultima vez em que o tablet recebeu alguma trama desse colete.
    vest = registered_vests.setdefault(vest_id, {
        "vest_id": vest_id,
        "mac_address": None,
        "net_id": DEFAULT_NET_ID,
        "slot_index": vest_id - 1,
        "slot_start_s": (vest_id - 1) * TDMA_SLOT_SECONDS,
    })
    vest["online"] = True
    vest["last_seen"] = time.time()
    vest["last_frame_type"] = frame_type


def remove_vest(vest_id):
    # Ao remover um colete tambem limpamos o seu GPS.
    registered_vests.pop(vest_id, None)
    memoria_gps.pop(vest_id, None)


def print_tdma_table():
    # Mostra no terminal a tabela dos coletes e dos respetivos slots TDMA.
    print("===== TDMA TABLE =====")
    for vest_id in sorted(registered_vests):
        vest = registered_vests[vest_id]
        print(
            f"Colete {vest_id} | Slot={vest['slot_index']} | "
            f"Inicio={vest['slot_start_s']:.3f}s | Online={vest.get('online', False)}"
        )
    print("======================\n")


# ===========================================================
# SYNC / UNSYNC

def wait_for_ack(expected_vest_id, expected_funct, timeout=8.0):
    # Espera por um ACK especifico. Durante esta espera, outros pacotes validos
    # continuam a ser processados para nao "congelar" o resto da rede.
    end_time = time.time() + timeout
    while time.time() < end_time:
        packet = hardware_receive_packet()
        if packet is None:
            continue
        decrypted = decrypt_aes_ctr(packet)
        # Se entrou outra trama que nao é ACK que queriamos, ela nao se perde, vai para o pipeline normal.
        if len(decrypted) != 2:
            decoded = parse_and_handle_packet(packet, emit_logs=False)
            if decoded:
                data_queue.put(decoded)
            continue

        control = unpack_control_frame(decrypted)
        if control and control["opcode"] == OPCODE_ACK:
            if control["vest_id"] == expected_vest_id and control["funct"] == expected_funct:
                return True
    return False


def send_sync_process(new_vest_id, mac_address_int, target_net_id=DEFAULT_NET_ID,
                      data_ch=DEFAULT_DATA_CH, ctrl_ch=DEFAULT_CTRL_CH):
    # Fluxo de emparelhamento do documento:
    # 1. construir o convite SYNC
    # 2. enviar varias tentativas com esperas decrescentes
    # 3. aguardar ACK de sincronizacao
    raw_packet = create_sync_packet(new_vest_id, target_net_id, mac_address_int, data_ch, ctrl_ch)
    encrypted_packet = encrypt_aes_ctr(raw_packet)
    register_vest(new_vest_id, mac_address=mac_address_int, net_id=target_net_id)

    # Sequencia inspirada no documento para intercetar a janela assincrona do colete.
    wait_intervals = [5.0, 2.5, 1.0, 0.5, 0.25, 0.1, 0.1, 0.1]
    print(f"\nA iniciar SYNC para MAC {mac_address_int:#010x} -> Vest ID {new_vest_id}")

    with sync_lock:
        for attempt, wait_time in enumerate(wait_intervals, start=1):
            print(f"Tentativa {attempt}/8")
            hardware_set_role_control()
            hardware_send_packet(encrypted_packet)
            if wait_for_ack(new_vest_id, FUNCT_ACK_SYNC, timeout=wait_time):
                mark_vest_online(new_vest_id)
                print(f"SYNC bem sucedido para colete {new_vest_id}\n")
                return True

    print(f"Falha no SYNC do colete {new_vest_id}\n")
    return False


def send_unsync_process(vest_id, funct=FUNCT_UNSYNC_MANUAL, timeout=3.0):
    # Como ainda estamos apenas nos testes UART, simulamos este comportamento calculando 
    # a janela logo a seguir ao slot TDMA esperado do colete.

    vest = registered_vests.get(vest_id)
    if not vest:
        print(f"Colete {vest_id} nao registado.")
        return False

    packet = create_unsync_packet(vest["net_id"], vest_id, funct=funct)
    encrypted = encrypt_aes_ctr(packet)

    wait_slot = seconds_until_next_slot(vest_id) + 0.06
    print(f"A aguardar {wait_slot:.3f}s pela janela apos o slot do colete {vest_id}...")
    time.sleep(wait_slot)

    hardware_set_role_control()
    hardware_send_packet(encrypted)
    if wait_for_ack(vest_id, FUNCT_ACK_SYNC, timeout=timeout):
        remove_vest(vest_id)
        print(f"UNSYNC bem sucedido para colete {vest_id}\n")
        return True

    print(f"Falha no UNSYNC do colete {vest_id}\n")
    return False


# ===========================================================
# PROCESSAMENTO DE PACOTES

def format_hex_bytes(data):
    return " ".join(f"0x{byte:02X}" for byte in data)

def parse_and_handle_packet(packet, emit_logs=True):
    # Esta funcao é o ponto central de tratamento dos pacotes recebidos:
    # desencripta, identifica o tipo de trama pela dimensao e encaminha para o sítio certo.
    tamanho = len(packet)
    decrypted_packet = decrypt_aes_ctr(packet)

    # 15 bytes -> trama completa do canal de dados.
    if tamanho == 15:
        if emit_logs:
            print(f"[RX] Raw bytes received: {format_hex_bytes(packet)}")
            print(f"[PARSER] Packet length detected: {tamanho} bytes")
            print(f"[PARSER] Decrypted payload: {format_hex_bytes(decrypted_packet)}")
            print("[PARSER] Decoding Full Telemetry Packet...")
        dados = unpack_telemetria_completa(decrypted_packet)
        if not dados:
            if emit_logs:
                print("[PARSER] Full Telemetry Packet invalid.\n")
            return None
        guardar_posicao_base(dados)
        mark_vest_seen(dados["vest_id"], "COMPLETA")
        if emit_logs:
            print(f"[DATA] Type: Full Telemetry | Net={dados['net_id']} | Colete={dados['vest_id']}")
            print(f"[DATA] BPM: {dados['bpm']}")
            print(f"[DATA] SpO2: {dados['spo2']}%" if dados["spo2"] != 0 else "[DATA] SpO2: ERRO/HIPOXIA (<70%)")
            print(f"[DATA] Temp: {dados['temperature']:.2f} C")
            print(f"[DATA] Alt: {dados['altitude']} m")
            print(f"[DATA] GPS: {dados['lat']:.7f}, {dados['lon']:.7f}\n")
        return dados

    # 7 bytes -> trama delta do canal de dados.
    if tamanho == 7:
        if emit_logs:
            print(f"[RX] Raw bytes received: {format_hex_bytes(packet)}")
            print(f"[PARSER] Packet length detected: {tamanho} bytes")
            print(f"[PARSER] Decrypted payload: {format_hex_bytes(decrypted_packet)}")
            print("[PARSER] Decoding Delta Packet...")
        dados_delta = unpack_telemetria_delta(decrypted_packet)
        if not dados_delta:
            if emit_logs:
                print("[PARSER] Delta Packet invalid.\n")
            return None
        dados = aplicar_delta_gps(dados_delta)
        if not dados:
            if emit_logs:
                print(f"[PARSER] Delta recebido para colete {dados_delta['vest_id']}, mas ainda sem trama completa base.\n")
            return None
        mark_vest_seen(dados["vest_id"], "DELTA_GPS")
        if emit_logs:
            print(f"[DATA] Type: Delta Packet | Net={dados['net_id']} | Colete={dados['vest_id']}")
            print(f"[DATA] GPS: {dados['lat']:.7f}, {dados['lon']:.7f}")
            print(f"[DATA] Alt: {dados['altitude']} m (delta {dados['alt_delta']:+d})\n")
        return dados

    # 4 bytes -> alerta biometrico com o valor exato do sensor.
    if tamanho == 4:
        alert = unpack_bio_alert_frame(decrypted_packet)
        if alert and emit_logs:
            print("=========================================")
            print(
                f"ALERTA BIOMETRICO | Net={alert['net_id']} | Colete={alert['vest_id']} | "
                f"Opcode={alert['opcode']:04b} | Funct={alert['funct']:04b} | Valor={alert['valor']}"
            )
            print("=========================================\n")
        return alert

    # 3 bytes -> este tamanho fica reservado para Team Hop, mas ainda nao esta a ser usado.
    if tamanho == 3:
        return None

    # 2 bytes -> trama base de controlo: ACK, UNSYNC, NETWORK ALIVE, QUICK ALERT, etc.
    if tamanho == 2:
        control = unpack_control_frame(decrypted_packet)
        if control and emit_logs:
            print("=========================================")
            print(
                f"CONTROL | Net={control['net_id']} | Colete={control['vest_id']} | "
                f"Opcode={control['opcode']:04b} | Funct={control['funct']:04b}"
            )
            print("=========================================\n")
        return control

    return None


def listen_for_incoming_data():
    # Esta funcao faz a rececao principal dos pacotes vindos da UART.
    packet = hardware_receive_packet()
    if packet is None:
        return None
    return parse_and_handle_packet(packet, emit_logs=True)


# ===========================================================
# THREADS

def radio_listening_thread():
    # Esta Thread serve para receber continuamente sem bloquear o resto do programa.
    while running:
        try:
            dados = listen_for_incoming_data()
            if dados:
                data_queue.put(dados)
        except Exception as e:
            print(f"ERRO NO PYTHON: {e}")
            time.sleep(1)


def network_alive_thread():
    # Esta funcao fica preparada para o envio periodico de Network Alive.
    pass


# ===========================================================
# MAIN 

if __name__ == "__main__":
    try:
        open_hardware()
    except Exception as e:
        print(f"Erro: O sistema nao pode arrancar sem a porta UART configurada. {e}")
        raise SystemExit(1)

    # Thread de rececao continua.
    rx_thread = threading.Thread(target=radio_listening_thread, daemon=True)
    rx_thread.start()

    print("\nServico tablet UART a correr...")
    print("Suporta tramas 2/4/7/15 bytes, TDMA simples, SYNC e UNSYNC.")
    print("Exemplo de uso interativo no REPL:")
    print("send_sync_process(3, 0xA1B2C3D4)")
    print("send_unsync_process(3)")
    print("print_tdma_table()\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        running = False
        print("\nEncerramento solicitado pelo utilizador.")
