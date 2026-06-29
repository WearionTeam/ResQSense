import threading
import time


class SpeakerController:
    """
    Passive speaker controller using software-generated square waves.

    Preferred backend:
    1) gpiozero.LED
    2) RPi.GPIO digital output
    """

    def __init__(self, pin=17, frequency_hz=440.0):
        self.pin = pin
        self.frequency_hz = max(1.0, float(frequency_hz))
        self.half_period_sec = 1.0 / (self.frequency_hz * 2.0)
        self.backend = None
        self.device = None
        self.gpio = None
        self._alarm_active = False
        self._alarm_thread = None
        self._burst_running = False
        self._last_burst_ts = 0.0
        self._min_burst_interval_sec = 1.5
        self._lock = threading.RLock()

        try:
            from gpiozero import LED  # type: ignore

            self.device = LED(self.pin)
            self.backend = "gpiozero_led"
            return
        except Exception:
            pass

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

    def _pin_on(self):
        if self.backend == "gpiozero_led" and self.device:
            self.device.on()
        elif self.backend == "rpi_gpio" and self.gpio:
            self.gpio.output(self.pin, self.gpio.HIGH)

    def _pin_off(self):
        if self.backend == "gpiozero_led" and self.device:
            self.device.off()
        elif self.backend == "rpi_gpio" and self.gpio:
            self.gpio.output(self.pin, self.gpio.LOW)

    def _emit_square_wave(self, duration_sec, should_continue=None):
        deadline = time.monotonic() + max(0.0, duration_sec)
        while time.monotonic() < deadline:
            if should_continue is not None and not should_continue():
                break
            self._pin_on()
            time.sleep(self.half_period_sec)
            if should_continue is not None and not should_continue():
                break
            self._pin_off()
            time.sleep(self.half_period_sec)
        self._pin_off()

    def stop(self):
        self._pin_off()

    def set_alarm(self, is_active):
        if not self.available():
            return

        if is_active:
            if self._alarm_active:
                return
            self._alarm_active = True
            self._alarm_thread = threading.Thread(target=self._alarm_worker, daemon=True)
            self._alarm_thread.start()
            return

        self._alarm_active = False
        self.stop()

    def emergency_burst(self):
        if not self.available():
            return

        if self._alarm_active:
            return

        now = time.monotonic()
        if now - self._last_burst_ts < self._min_burst_interval_sec:
            return
        self._last_burst_ts = now

        with self._lock:
            if self._burst_running:
                return
            self._burst_running = True

        threading.Thread(target=self._burst_worker, daemon=True).start()

    def _alarm_worker(self):
        try:
            while self._alarm_active:
                with self._lock:
                    self._emit_square_wave(0.25, should_continue=lambda: self._alarm_active)
        finally:
            self.stop()

    def _burst_worker(self):
        try:
            with self._lock:
                self._emit_square_wave(0.28)
        finally:
            with self._lock:
                self._burst_running = False
            self.stop()

    def cleanup(self):
        self._alarm_active = False
        if self._alarm_thread and self._alarm_thread.is_alive():
            self._alarm_thread.join(timeout=0.3)
        try:
            self.stop()
        except Exception:
            pass

        if self.backend == "gpiozero_led" and self.device:
            try:
                self.device.close()
            except Exception:
                pass

        if self.backend == "rpi_gpio" and self.gpio:
            try:
                self.gpio.cleanup(self.pin)
            except Exception:
                pass
