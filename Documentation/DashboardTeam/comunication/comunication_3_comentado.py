
# IMPORTAÇÕES GERAIS E DE SISTEMA
import struct    # Serve para desempacotar bytes brutos recebidos pela rede e convertê-los em números (ex: GPS)
import time      # Permite usar pausas (time.sleep) e medir o tempo
import threading # Permite que o código faça várias tarefas ao mesmo tempo (ex: ouvir o rádio em segundo plano)
import queue     # Cria uma "fila de espera" segura para guardar os dados recebidos antes de os mostrar no ecrã

#===================================================================

# IMPORTAÇÕES PARA O HARDWARE

import board       # Conhece os pinos físicos do Raspberry Pi
import busio       # Gere a comunicação SPI (usada pelo rádio LoRa)
import digitalio   # Permite ligar/desligar os pinos digitais do Raspberry Pi
import serial      # Permite a comunicação via cabo UART/USB (usada nos testes com a ESP32 ou PC)

# Serve como um lugar para guardar a localização (Graus) de cada colete
memoria_gps = {}

#===================================================================

# IMPORTAÇÕES E CONFIGURAÇÕES DE SEGURANÇA

# Bibliotecas necessárias (instalar com: pip install pycryptodome adafruit-circuitpython-rfm9x pyserial)
from Crypto.Cipher import AES
from Crypto.Util import Counter
from adafruit_rfm9x import RFM9x

# A chave secreta partilhada (16 bytes = 128 bits). É a mesma usada no Colete.
AES_KEY = bytes([
    0x2B, 0x7E, 0x15, 0x16, 0x28, 0xAE, 0xD2, 0xA6,
    0xAB, 0xF7, 0x15, 0x88, 0x09, 0xCF, 0x4F, 0x3C
])

def encrypt_aes_ctr(data):
    # O modo CTR precisa de um contador que começa em 0.
    ctr = Counter.new(128, initial_value=0) 
    # Para encriptar, utiliza a chave, o modo e o contador
    cipher = AES.new(AES_KEY, AES.MODE_CTR, counter=ctr)
    # Vai devolver os dados originais transformados em "lixo"(cifrados)
    return cipher.encrypt(data)

def decrypt_aes_ctr(data):
    # Este processo de desencriptar é oposto do encriptar. O contador tem de começar igual.
    ctr = Counter.new(128, initial_value=0)
    cipher = AES.new(AES_KEY, AES.MODE_CTR, counter=ctr)
    # Pega no "lixo" e devolve os bytes limpos originais
    return cipher.decrypt(data)

#===================================================================

# COMUTADOR DE HARDWARE (LORA vs UART)

# Interruptor para alternar entre via cabo (UART) ou rádio (LORA) (aqui escolhemos o modo de teste)
MODO_HARDWARE = "UART"  

if MODO_HARDWARE == "LORA":
    try:
        # Configuração dos pinos do Raspberry Pi ligados ao módulo rádio
        CS = digitalio.DigitalInOut(board.CE1)
        RESET = digitalio.DigitalInOut(board.D25)
        spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
        # Inicializa o rádio na frequência do Canal de Dados (868.2 MHz)
        lora = RFM9x(spi, CS, RESET, 868.2)
        lora.tx_power = 14 # Potência de emissão (14 dBm)
        print("Rádio LoRa inicializado com sucesso no Canal de Dados (868.2 MHz)!")
    except Exception as e:
        print(f"Erro ao inicializar o LoRa: {e}")

elif MODO_HARDWARE == "UART":
    try:
        # Aqui vamos definir a porta do cabo. Exemplo Windows: "COM3" | Linux/Raspberry: "/dev/serial0"
        PORTA_UART = "COM3" 
        # Define a velocidade da porta (115200). Define também um timeout de 0.1s para impedir que o código encrave à espera.
        uart = serial.Serial(PORTA_UART, baudrate=115200, timeout=0.1)
        print(f"Modo UART de testes inicializado com sucesso na porta {PORTA_UART}!")
    except Exception as e:
        print(f"Erro ao inicializar UART: {e}")

#===================================================================

# FUNÇÕES UNIVERSAIS DE ENVIO E RECEÇÃO

def hardware_send(dados_encriptados):
    # Verifica qual é o modo de teste que escolhemos e vai enviar pelo meio correto
    if MODO_HARDWARE == "LORA":
        lora.send(dados_encriptados)
    elif MODO_HARDWARE == "UART":
        uart.write(dados_encriptados)

def hardware_receive(timeout=0.1, max_bytes=15):
    # Fica à escuta de novos dados
    if MODO_HARDWARE == "LORA":
        return lora.receive(timeout=timeout)
    elif MODO_HARDWARE == "UART":
        uart.timeout = timeout
        pacote = uart.read(max_bytes) 
        if len(pacote) > 0:           # Se leu alguma coisa, devolve o pacote
            return pacote
        return None                   # Se passou o tempo e não leu nada, devolve Vazio

def set_hardware_frequency(freq_mhz):
    # O cabo UART não tem frequências, por isso ignoramos se não for LoRa
    if MODO_HARDWARE == "LORA":
        lora.frequency_mhz = freq_mhz

#===================================================================

# PROCESSO DE SYNC (EMPARELHAMENTO DE NOVOS COLETES)

def create_sync_packet(novo_vest_id, target_net_id, mac_address_int, data_ch, ctrl_ch):
    # Constrói o pacote de SYNC
    net_id = 0b000
    byte0 = (net_id << 5) | (novo_vest_id & 0x1F)
    opcode = 0b0011 # Opcode 3 significa: "Configuração de Rede"
    byte1 = (opcode << 4) | (0b0 << 3) | (target_net_id & 0x07)
    mac_bytes = struct.pack('>I', mac_address_int) # Empacota o MAC Address em 4 bytes (Big-Endian)
    byte6 = ((data_ch & 0x07) << 5) | (0b0 << 4) | ((ctrl_ch & 0x07) << 1) | 0b0
    return bytes([byte0, byte1]) + mac_bytes + bytes([byte6])

def send_sync_process(novo_vest_id, target_net_id, mac_address_int, data_ch, ctrl_ch):
    print(f"\nA iniciar SYNC para o MAC {mac_address_int} -> Vest ID {novo_vest_id}...")
    raw_packet = create_sync_packet(novo_vest_id, target_net_id, mac_address_int, data_ch, ctrl_ch)
    encrypted_packet = encrypt_aes_ctr(raw_packet) # Aqui é onde se encripta o "convite"
    
    set_hardware_frequency(868.0) # Muda para o Canal de Controlo (Canal 0)para enviar o convite
    
    # Intervalos de espera cada vez mais curtos 
    wait_intervals = [5.0, 2.5, 1.0, 0.5, 0.25, 0.1, 0.1, 0.1]
    sucesso = False
    
    for attempt, wait_time in enumerate(wait_intervals):
        print(f"Tentativa {attempt + 1}/8. A aguardar {wait_time}s...")
        hardware_send(encrypted_packet) # Dispara o convite
        
        # Fica à espera que o colete devolva o "Recibo" (ACK)
        if wait_for_ack(novo_vest_id, timeout=wait_time):
            print(f"Sucesso: Colete {novo_vest_id} conectado")
            sucesso = True
            break
            
    if not sucesso:
        print("Falha: O colete não respondeu.")
        
    set_hardware_frequency(868.2) # Aconteça o que acontecer, volta sempre ao Canal de Dados!
    return sucesso

def wait_for_ack(expected_vest_id, timeout):
    # Cronómetro para esperar pelo ACK
    start_time = time.time()
    while (time.time() - start_time) < timeout:
        packet = hardware_receive(timeout=0.1, max_bytes=2) # ACKs têm sempre 2 bytes
        if packet is not None and len(packet) == 2:
            decrypted = decrypt_aes_ctr(packet)
            received_vest_id = decrypted[0] & 0x1F
            opcode = (decrypted[1] >> 4) & 0x0F
            funct = decrypted[1] & 0x0F
            # Se for o colete certo e o opcode for 0b1001 (ACK de Sistema), deu certo
            if received_vest_id == expected_vest_id and opcode == 0b1001 and funct == 0b0010:
                return True
    return False

#===================================================================

# TRADUTOR DE TELEMETRIA

def unpack_telemetria_completa(decrypted_data):
    try:
        # Lê a identificação
        net_id = (decrypted_data[0] >> 5) & 0x07
        vest_id = decrypted_data[0] & 0x1F
        flags = decrypted_data[1]
        
        # Desempacota o meio da trama com uma leitura Big-Endian (símbolo '>')
        # Lê 2 Inteiros (Lat/Lon), 1 Short (Alt) e 1 Byte (BPM)
        lat_raw, long_raw, alt_raw, bpm = struct.unpack('>ii h B', decrypted_data[2:13])
        
        # Isola os dois últimos bytes para processar o oxigénio e temperatura
        b13 = decrypted_data[13]
        b14 = decrypted_data[14]

        # Extrai os 5 bits de SpO2 e e soma o valor base 69 para obter a percentagem real de oxigénio
        spo2_raw = (b13 >> 3) & 0x1F  
        spo2 = (spo2_raw + 69) if spo2_raw > 0 else 0
        
        # Junta os bits restantes para a temperatura e divide por 100
        temp_raw = ((b13 & 0x07) << 8) | b14
        temperature = 25.00 + (temp_raw / 100.0)

        # Devolve os dados todos organizados de uma forma fácil de ler
        return {
            "tipo": "COMPLETA",
            "vest_id": vest_id,
            "bpm": bpm,
            "spo2": spo2,
            "temperature": temperature,
            "mandown": bool((flags >> 5) & 0x01),
            "sos": bool((flags >> 6) & 0x01),
            "lat_raw": lat_raw, 
            "lon_raw": long_raw
        }
    except Exception as e:
        return None

#===================================================================

# GESTOR DE TRAMAS 

def listen_for_incoming_data():
    global memoria_gps 
    
    # Escuta o canal à procura de pacotes com um máximo de 15 bytes
    packet = hardware_receive(timeout=0.1, max_bytes=15)
    
    if packet is not None:
        
        # Para testar COM encriptação, usamos a linha de baixo:
        # decrypted_packet = decrypt_aes_ctr(packet)      
        
        # Para testar SEM encriptação (texto limpo da ESP32 ou PC), usamos esta
        decrypted_packet = packet                     
        
        # Mede o tamanho exato do pacote para saber para onde o encaminhar
        tamanho = len(decrypted_packet)
        
        if tamanho == 15:
           
            # 1. TRAMA DE TELEMETRIA COMPLETA
            dados = unpack_telemetria_completa(decrypted_packet)
            if dados:
                vest_id = dados['vest_id']
                
                lat_graus_calc = dados['lat_raw'] // 10000000 
                lat_min_calc = abs(dados['lat_raw']) % 10000000
                lon_graus_calc = dados['lon_raw'] // 10000000
                lon_min_calc = abs(dados['lon_raw']) % 10000000
                
                memoria_gps[vest_id] = {
                    'lat_graus': lat_graus_calc,
                    'lat_min': lat_min_calc,
                    'lon_graus': lon_graus_calc,
                    'lon_min': lon_min_calc
                }
                
                print(f"[Colete {vest_id}] Trama Completa | BPM: {dados['bpm']} | Temp: {dados['temperature']:.2f}ºC")
                return dados
                
        elif tamanho == 7:
            # 2. TRAMA DE POSIÇÃO/DELTA (para fazer uma atualização mais rapida do GPS)

            vest_id = decrypted_packet[0] & 0x1F
            
            lat_min_novo, lon_min_novo = struct.unpack('>HH', decrypted_packet[1:5])
            
            # Se já conhecemos os graus originais deste colete...
            if vest_id in memoria_gps:
                estado = memoria_gps[vest_id]
                
                # Margens para detetar se o bombeiro "deu a volta" na escala dos decimais
                limite_superior = 9000000 # Perto de 10 milhões
                limite_inferior = 1000000 # Perto de zero
                
                # Lógica de Rollover da Latitude:
                # Se estava num valor alto e caiu para um muito baixo, andou 1 Grau para a frente
                if estado['lat_min'] > limite_superior and lat_min_novo < limite_inferior:
                    estado['lat_graus'] += 1
                # Se estava num valor baixo e saltou para um muito alto, andou 1 Grau para trás
                elif estado['lat_min'] < limite_inferior and lat_min_novo > limite_superior:
                    estado['lat_graus'] -= 1
                estado['lat_min'] = lat_min_novo # Atualiza os decimais novos

                # Repetiu-se a Lógica de Rollover para a Longitude
                if estado['lon_min'] > limite_superior and lon_min_novo < limite_inferior:
                    estado['lon_graus'] += 1
                elif estado['lon_min'] < limite_inferior and lon_min_novo > limite_superior:
                    estado['lon_graus'] -= 1
                estado['lon_min'] = lon_min_novo 
                
                print(f"[GPS DELTA] Colete {vest_id} -> {estado['lat_graus']}.{estado['lat_min']}")
                
                return {
                    "tipo": "DELTA_GPS",
                    "vest_id": vest_id,
                    "lat": f"{estado['lat_graus']}.{estado['lat_min']}",
                    "lon": f"{estado['lon_graus']}.{estado['lon_min']}"
                }
            
        elif tamanho == 4:
            # 3. EMERGÊNCIA (SOS / MAN DOWN)

            vest_id = decrypted_packet[0] & 0x1F
            flags = decrypted_packet[1]
            
            # Lê bits individuais para saber o motivo do alerta
            sos = bool((flags >> 6) & 0x01)
            md = bool((flags >> 5) & 0x01)
            
            if sos or md:
                tipo = "SOS" if sos else "MAN DOWN"
                print(f"ALERTA CRÍTICO: Colete {vest_id} em {tipo}")
            
            return {
                "tipo": "ALERTA",
                "vest_id": vest_id,
                "sos": sos,
                "man_down": md
            }
            
        elif tamanho == 2:
            # 4. CONFIRMAÇÃO DE RECEÇÃO (ACK)

            vest_id = decrypted_packet[0] & 0x1F
            opcode = (decrypted_packet[1] >> 4) & 0x0F
            
            # Se o opcode for 9 (0b1001), é o Colete a confirmar que ouviu o Dashboard
            print(f"[ACK] Colete {vest_id} confirmou a receção da mensagem.")
            
            return {
                "tipo": "ACK",
                "vest_id": vest_id,
                "status": "RECEBIDO"
            }
                
    return None # Se o pacote chegou estragado ou com um tamanho inválido, ignora

#===================================================================

# MOTOR DE EXECUÇÃO 

# Cria uma fila segura para guardar os dados, de forma a não perder nada
data_queue = queue.Queue() 

def radio_listening_thread():
    # Loop infinito
    while True:
        try:
            dados = listen_for_incoming_data() # Fica à escuta
            if dados:
                data_queue.put(dados) # Se receber dados válidos, mete-os na fila (queue)
        except Exception as e:
            time.sleep(1) # Se houver um erro, pausa 1 segundo e volta a tentar

#===================================================================

# INÍCIO DO PROGRAMA PRINCIPAL
if __name__ == "__main__":
    
    # 1. Verifica se a inicialização do Hardware a cima correu bem
    if MODO_HARDWARE == "LORA" and 'lora' not in globals():
        print("Erro: O sistema não pode arrancar sem o módulo LoRa configurado.")
        exit(1)
    elif MODO_HARDWARE == "UART" and 'uart' not in globals():
        print("Erro: O sistema não pode arrancar sem a porta UART configurada.")
        exit(1)

    # 2. Cria a Thread e põe-a a correr em segundo plano
    rx_thread = threading.Thread(target=radio_listening_thread, daemon=True)
    rx_thread.start()

    print(f"\nServiço a correr no modo [{MODO_HARDWARE}]...")
    print("A ouvir pacotes. Ctrl+C para encerrar.\n")
    
    # 3. Mantém a janela principal aberta. 
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nEncerramento solicitado pelo utilizador.")
