import math
import os
import threading
import time


class MagnetometerController:
    """
    BNO08x heading reader for Raspberry Pi over I2C.

    The controller keeps a background heading estimate in degrees:
    0 = North, 90 = East, 180 = South, 270 = West
    """

    def __init__(self, update_interval_sec=0.15):
        self.backend = None
        self.sensor = None
        self.running = False
        self.thread = None
        self.update_interval_sec = update_interval_sec
        self.heading_deg = 0.0
        self.last_error = None
        self._lock = threading.RLock()

        try:
            self.declination_deg = float(os.getenv("RESQSENSE_MAG_DECLINATION_DEG", "0"))
        except ValueError:
            self.declination_deg = 0.0

        try:
            self.offset_deg = float(os.getenv("RESQSENSE_MAG_OFFSET_DEG", "0"))
        except ValueError:
            self.offset_deg = 0.0

        self.swap_xy = os.getenv("RESQSENSE_MAG_SWAP_XY", "0").strip() == "1"
        self.x_sign = -1.0 if os.getenv("RESQSENSE_MAG_X_SIGN", "1").strip() == "-1" else 1.0
        self.y_sign = -1.0 if os.getenv("RESQSENSE_MAG_Y_SIGN", "1").strip() == "-1" else 1.0

        try:
            import board  # type: ignore
            import busio  # type: ignore
            from adafruit_bno08x import BNO_REPORT_MAGNETOMETER  # type: ignore
            from adafruit_bno08x.i2c import BNO08X_I2C  # type: ignore

            i2c = busio.I2C(board.SCL, board.SDA, frequency=400000)
            sensor = BNO08X_I2C(i2c)
            sensor.enable_feature(BNO_REPORT_MAGNETOMETER)

            self.sensor = sensor
            self.backend = "adafruit_bno08x"
        except Exception as exc:
            self.last_error = str(exc)
            self.backend = None

    def available(self):
        return self.backend is not None and self.sensor is not None

    def start(self):
        if not self.available() or self.running:
            return
        self.running = True
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

    def _normalize_heading(self, heading_deg):
        return (heading_deg + 360.0) % 360.0

    def _read_heading_once(self):
        if not self.sensor:
            return None

        mag_x, mag_y, _mag_z = self.sensor.magnetic

        if self.swap_xy:
            mag_x, mag_y = mag_y, mag_x

        mag_x *= self.x_sign
        mag_y *= self.y_sign

        heading_deg = math.degrees(math.atan2(mag_y, mag_x))
        heading_deg += self.declination_deg
        heading_deg += self.offset_deg
        return self._normalize_heading(heading_deg)

    def _worker(self):
        while self.running:
            try:
                heading = self._read_heading_once()
                if heading is not None:
                    with self._lock:
                        self.heading_deg = heading
            except Exception as exc:
                self.last_error = str(exc)
            time.sleep(self.update_interval_sec)
