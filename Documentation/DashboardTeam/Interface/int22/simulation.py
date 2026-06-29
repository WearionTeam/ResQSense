import os
import random


def _safe_env_float(name, default_value):
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default_value
    try:
        return float(raw_value)
    except ValueError:
        return default_value


class HardwareSimulator:
    """
    Generates fake telemetry data for local testing without hardware.

    Output schema matches Operator.update_detail():
    {
        "hr": int,
        "spo2": int,
        "temp": float,
        "lat": float,
        "long": float,
        "height": float,
    }
    """

    _seed = None
    _seed_text = os.getenv("RESQSENSE_SIM_SEED", "").strip()
    if _seed_text:
        try:
            _seed = int(_seed_text)
        except ValueError:
            _seed = None

    _rng = random.Random(_seed)

    _critical_rate = max(0.0, min(1.0, _safe_env_float("RESQSENSE_SIM_CRITICAL_RATE", 0.12)))

    _state = {
        "hr": 86.0,
        "spo2": 98.0,
        "temp": 36.6,
        "lat": _safe_env_float("RESQSENSE_SIM_BASE_LAT", 40.6405),
        "long": _safe_env_float("RESQSENSE_SIM_BASE_LONG", -8.6538),
        "height": 4.0,
    }

    _critical_kind = None
    _critical_cycles_left = 0

    @staticmethod
    def _clamp(value, low, high):
        return max(low, min(high, value))

    @classmethod
    def _start_critical_event_if_needed(cls):
        if cls._critical_cycles_left > 0:
            return

        if cls._rng.random() >= cls._critical_rate:
            cls._critical_kind = None
            return

        cls._critical_kind = cls._rng.choice(("HR HIGH", "SpO2 LOW", "TEMP HIGH"))
        cls._critical_cycles_left = cls._rng.randint(2, 4)

    @classmethod
    def _update_normal_vitals(cls):
        cls._state["hr"] = cls._clamp(cls._state["hr"] + cls._rng.uniform(-3.2, 3.2), 72.0, 102.0)
        cls._state["spo2"] = cls._clamp(cls._state["spo2"] + cls._rng.uniform(-0.8, 0.6), 95.0, 100.0)
        cls._state["temp"] = cls._clamp(cls._state["temp"] + cls._rng.uniform(-0.08, 0.08), 36.1, 37.7)

    @classmethod
    def _apply_critical_vitals(cls):
        if cls._critical_kind == "HR HIGH":
            cls._state["hr"] = cls._rng.uniform(112.0, 138.0)
            cls._state["spo2"] = cls._clamp(cls._state["spo2"] + cls._rng.uniform(-0.5, 0.2), 95.0, 100.0)
            cls._state["temp"] = cls._clamp(cls._state["temp"] + cls._rng.uniform(-0.05, 0.05), 36.2, 37.6)
        elif cls._critical_kind == "SpO2 LOW":
            cls._state["spo2"] = cls._rng.uniform(86.0, 93.0)
            cls._state["hr"] = cls._clamp(cls._state["hr"] + cls._rng.uniform(-1.0, 4.0), 80.0, 110.0)
            cls._state["temp"] = cls._clamp(cls._state["temp"] + cls._rng.uniform(-0.05, 0.05), 36.2, 37.6)
        elif cls._critical_kind == "TEMP HIGH":
            cls._state["temp"] = cls._rng.uniform(38.1, 39.3)
            cls._state["hr"] = cls._clamp(cls._state["hr"] + cls._rng.uniform(1.0, 6.0), 85.0, 118.0)
            cls._state["spo2"] = cls._clamp(cls._state["spo2"] + cls._rng.uniform(-0.5, 0.3), 94.0, 100.0)

        cls._critical_cycles_left = max(0, cls._critical_cycles_left - 1)
        if cls._critical_cycles_left == 0:
            cls._critical_kind = None

    @classmethod
    def _update_position(cls):
        cls._state["lat"] += cls._rng.uniform(-0.00002, 0.00002)
        cls._state["long"] += cls._rng.uniform(-0.00002, 0.00002)
        cls._state["height"] = cls._clamp(cls._state["height"] + cls._rng.uniform(-0.8, 0.8), 0.0, 80.0)

    @classmethod
    def get_fake_data(cls):
        cls._start_critical_event_if_needed()
        cls._update_normal_vitals()

        if cls._critical_kind:
            cls._apply_critical_vitals()

        cls._update_position()

        return {
            "hr": int(round(cls._state["hr"])),
            "spo2": int(round(cls._state["spo2"])),
            "temp": round(cls._state["temp"], 2),
            "lat": round(cls._state["lat"], 7),
            "long": round(cls._state["long"], 7),
            "height": round(cls._state["height"], 2),
        }
