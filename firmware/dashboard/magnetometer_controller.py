import math
import os
import threading
import time


class MagnetometerController:
    """
    Leitor de "heading" (rumo) para Raspberry Pi via I2C usando um MPU-6050.

    ATENCAO: o MPU-6050 e um sensor de 6 eixos (acelerometro + giroscopio) e
    NAO tem magnetometro. Por isso nao existe um Norte absoluto como no BNO08x
    antigo. Aqui o heading e RELATIVO: comeca em 0 graus quando o programa
    arranca e gira conforme voce roda fisicamente o sensor, integrando a
    velocidade angular do eixo Z do giroscopio.

    Resultado pratico: o radar gira quando voce roda o sensor, que e
    exactamente o comportamento pedido.

    Interface mantida identica ao controller anterior para nao mexer no
    testedashboard.py:
        - available()
        - start() / stop()
        - get_heading_degrees() / get_heading_radians()

    Ligacoes (as MESMAS do sensor antigo, I2C padrao do Raspberry Pi):
        Pino 1  (3.3V) -> VCC
        Pino 9  (GND)  -> GND
        Pino 3  (SDA)  -> SDA
        Pino 5  (SCL)  -> SCL
    """

    # Registos do MPU-6050
    _PWR_MGMT_1 = 0x6B
    _WHO_AM_I = 0x75
    _GYRO_CONFIG = 0x1B
    _GYRO_ZOUT_H = 0x47
    # Sensibilidade do giroscopio em ±250 graus/s (FS_SEL = 0): 131 LSB por (graus/s)
    _GYRO_SENS_250DPS = 131.0

    def __init__(self, update_interval_sec=0.02):
        self.backend = None
        self.bus = None
        self.address = None
        self.running = False
        self.thread = None
        # Atualizacao rapida (50 Hz) para integrar o giroscopio com pouco erro.
        self.update_interval_sec = update_interval_sec
        self.heading_deg = 0.0
        self.gyro_bias_z = 0.0
        self.last_error = None
        self._lock = threading.RLock()
        self._last_time = None

        # Offset inicial do rumo (graus) e sentido de rotacao, configuraveis.
        try:
            self.offset_deg = float(os.getenv("RESQSENSE_MAG_OFFSET_DEG", "0"))
        except ValueError:
            self.offset_deg = 0.0

        self.z_sign = -1.0 if os.getenv("RESQSENSE_MAG_Z_SIGN", "1").strip() == "-1" else 1.0

        try:
            self.i2c_bus_num = int(os.getenv("RESQSENSE_MPU_I2C_BUS", "1"))
        except ValueError:
            self.i2c_bus_num = 1

        try:
            self.address = int(os.getenv("RESQSENSE_MPU_I2C_ADDR", "0x68"), 0)
        except ValueError:
            self.address = 0x68

        self.heading_deg = self.offset_deg % 360.0

        try:
            try:
                from smbus2 import SMBus  # type: ignore
            except ImportError:
                from smbus import SMBus  # type: ignore

            bus = SMBus(self.i2c_bus_num)

            # Confirma que ha um MPU-6050/6500 no endereco esperado.
            who = bus.read_byte_data(self.address, self._WHO_AM_I)
            if who not in (0x68, 0x70, 0x72, 0x73, 0x71):
                # Continua mesmo assim, mas regista o aviso.
                self.last_error = f"WHO_AM_I inesperado: 0x{who:02X}"

            # Acorda o sensor (sai do modo sleep).
            bus.write_byte_data(self.address, self._PWR_MGMT_1, 0x00)
            time.sleep(0.05)
            # Garante faixa do giroscopio em ±250 dps.
            bus.write_byte_data(self.address, self._GYRO_CONFIG, 0x00)
            time.sleep(0.05)

            self.bus = bus
            self.backend = "mpu6050"

            # Calibra o bias do giroscopio com o sensor parado.
            self._calibrate_gyro_bias()
        except Exception as exc:
            self.last_error = str(exc)
            self.backend = None
            self.bus = None

    # ------------------------------------------------------------------ #
    # API publica (igual ao controller antigo)
    # ------------------------------------------------------------------ #
    def available(self):
        return self.backend is not None and self.bus is not None

    def start(self):
        if not self.available() or self.running:
            return
        self.running = True
        self._last_time = time.monotonic()
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        self.thread = None

    def get_heading_degrees(self):
        with self._lock:
            return self.heading_deg

    def get_heading_radians(self):
        return math.radians(self.get_heading_degrees())

    def reset_heading(self, value_deg=0.0):
        """Zera (ou define) o rumo atual. Util para 'apontar o radar' a frente."""
        with self._lock:
            self.heading_deg = value_deg % 360.0

    # ------------------------------------------------------------------ #
    # Internos
    # ------------------------------------------------------------------ #
    def _read_word_signed(self, reg_high):
        high = self.bus.read_byte_data(self.address, reg_high)
        low = self.bus.read_byte_data(self.address, reg_high + 1)
        value = (high << 8) | low
        if value >= 0x8000:
            value -= 0x10000
        return value

    def _read_gyro_z_dps(self):
        raw = self._read_word_signed(self._GYRO_ZOUT_H)
        return raw / self._GYRO_SENS_250DPS

    def _calibrate_gyro_bias(self, samples=200):
        """Mede o desvio do giroscopio parado para reduzir a deriva (drift)."""
        try:
            total = 0.0
            for _ in range(samples):
                total += self._read_gyro_z_dps()
                time.sleep(0.002)
            self.gyro_bias_z = total / samples
        except Exception as exc:
            self.last_error = str(exc)
            self.gyro_bias_z = 0.0

    def _normalize_heading(self, heading_deg):
        return (heading_deg + 360.0) % 360.0

    def _worker(self):
        while self.running:
            try:
                now = time.monotonic()
                dt = now - (self._last_time or now)
                self._last_time = now
                if dt <= 0 or dt > 1.0:
                    # Salto de tempo invalido: ignora esta iteracao.
                    time.sleep(self.update_interval_sec)
                    continue

                rate_dps = (self._read_gyro_z_dps() - self.gyro_bias_z) * self.z_sign
                with self._lock:
                    self.heading_deg = self._normalize_heading(
                        self.heading_deg + rate_dps * dt
                    )
            except Exception as exc:
                self.last_error = str(exc)
            time.sleep(self.update_interval_sec)
