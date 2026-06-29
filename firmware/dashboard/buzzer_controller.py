import threading
import time


class BuzzerController:
    """
    Hardware buzzer wrapper with fallback support:
    1) gpiozero.Buzzer
    2) RPi.GPIO
    """

    def __init__(self, pin=17):
        self.pin = pin
        self.backend = None
        self.device = None
        self.gpio = None
        self._last_burst_ts = 0.0
        self._min_burst_interval_sec = 1.5
        self._burst_running = False
        self._lock = threading.Lock()

        # Preferred backend: gpiozero
        try:
            from gpiozero import Buzzer  # type: ignore

            self.device = Buzzer(self.pin)
            self.backend = "gpiozero"
            return
        except Exception:
            pass

        # Fallback backend: RPi.GPIO
        try:
            import RPi.GPIO as GPIO  # type: ignore

            self.gpio = GPIO
            self.gpio.setwarnings(False)
            self.gpio.setmode(self.gpio.BCM)
            self.gpio.setup(self.pin, self.gpio.OUT)
            self.gpio.output(self.pin, self.gpio.LOW)
            self.backend = "rpi_gpio"
        except Exception:
            self.backend = None

    def available(self):
        return self.backend is not None

    def on(self):
        if self.backend == "gpiozero" and self.device:
            self.device.on()
        elif self.backend == "rpi_gpio" and self.gpio:
            self.gpio.output(self.pin, self.gpio.HIGH)

    def off(self):
        if self.backend == "gpiozero" and self.device:
            self.device.off()
        elif self.backend == "rpi_gpio" and self.gpio:
            self.gpio.output(self.pin, self.gpio.LOW)

    def set_alarm(self, is_active):
        if is_active:
            self.on()
        else:
            self.off()

    def emergency_burst(self):
        if not self.available():
            return

        now = time.monotonic()
        if now - self._last_burst_ts < self._min_burst_interval_sec:
            return
        self._last_burst_ts = now

        if self.backend == "gpiozero" and self.device:
            # Non-blocking short pattern.
            self.device.beep(on_time=0.08, off_time=0.08, n=4, background=True)
            return

        if self.backend == "rpi_gpio" and self.gpio:
            with self._lock:
                if self._burst_running:
                    return
                self._burst_running = True
            threading.Thread(target=self._burst_worker_gpio, daemon=True).start()

    def _burst_worker_gpio(self):
        try:
            for _ in range(4):
                self.on()
                time.sleep(0.08)
                self.off()
                time.sleep(0.08)
        finally:
            with self._lock:
                self._burst_running = False

    def cleanup(self):
        try:
            self.off()
        except Exception:
            pass

        if self.backend == "gpiozero" and self.device:
            try:
                self.device.close()
            except Exception:
                pass

        if self.backend == "rpi_gpio" and self.gpio:
            try:
                self.gpio.cleanup(self.pin)
            except Exception:
                pass
