from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from firestation_database import ResQSenseDatabase, get_database


@dataclass
class BiometricData:
    heart_rate: int = 0
    oxygenation: int = 0
    temperature: float = 0.0
    processing_delay_sec: int = 0


@dataclass
class Location:
    latitude: float = 0.0
    longitude: float = 0.0
    distance_relative: float = 0.0
    height: float = 0.0


@dataclass
class Alert:
    operator_id: int
    description: str
    timestamp: datetime = field(default_factory=datetime.now)


class Operator:
    def __init__(self, id: int, name: str, vest_id: str):
        self.id = id
        self.name = name
        self.vest_id = vest_id
        self.db_id: Optional[int] = None
        self.biometrics = BiometricData()
        self.location = Location()

    def update_detail(self, new_data: dict):
        if "hr" in new_data:
            self.biometrics.heart_rate = new_data["hr"]
        if "spo2" in new_data:
            self.biometrics.oxygenation = new_data["spo2"]
        if "temp" in new_data:
            self.biometrics.temperature = new_data["temp"]
        if "lat" in new_data:
            self.location.latitude = new_data["lat"]
        if "long" in new_data:
            self.location.longitude = new_data["long"]
        if "height" in new_data:
            self.location.height = new_data["height"]

    def check_health_status(self):
        warnings = []
        is_critical = False

        if self.biometrics.heart_rate > 150:
            warnings.append("HR HIGH")
            is_critical = True

        if self.biometrics.oxygenation < 95 and self.biometrics.oxygenation > 0:
            warnings.append("SpO2 LOW")
            is_critical = True

        if self.biometrics.temperature > 37.8:
            warnings.append("TEMP HIGH")
            is_critical = True

        return is_critical, warnings


class Session:
    def __init__(self, session_id: str, leader_id: int, database: Optional[ResQSenseDatabase] = None):
        self.session_id = session_id
        self.leader_id = leader_id
        self.database = database or get_database()
        self.active_operators: List[Operator] = []
        self.alerts_log: List[Alert] = []
        self._operator_db_ids_by_vest: Dict[str, int] = {}
        self._operator_db_ids_by_logic_id: Dict[int, int] = {}

    def _ensure_operator_persisted(self, operator: Operator) -> int:
        if operator.db_id is not None:
            self._operator_db_ids_by_vest[operator.vest_id] = operator.db_id
            self._operator_db_ids_by_logic_id[operator.id] = operator.db_id
            return operator.db_id

        cached_id = self._operator_db_ids_by_vest.get(operator.vest_id)
        if cached_id is not None:
            operator.db_id = cached_id
            self._operator_db_ids_by_logic_id[operator.id] = cached_id
            return cached_id

        db_id = self.database.upsert_operator(
            name=operator.name,
            vest_id=operator.vest_id,
            baseline_heart_rate=operator.biometrics.heart_rate if operator.biometrics.heart_rate > 0 else None,
            baseline_temperature=operator.biometrics.temperature if operator.biometrics.temperature > 0 else None,
            baseline_oxygenation=operator.biometrics.oxygenation if operator.biometrics.oxygenation > 0 else None,
        )
        operator.db_id = db_id
        self._operator_db_ids_by_vest[operator.vest_id] = db_id
        self._operator_db_ids_by_logic_id[operator.id] = db_id
        self.database.attach_operator_to_session(self.session_id, db_id)
        return db_id

    def _resolve_operator_db_id_from_logic_id(self, logic_operator_id: int) -> Optional[int]:
        cached = self._operator_db_ids_by_logic_id.get(logic_operator_id)
        if cached is not None:
            return cached

        for operator in self.active_operators:
            if operator.id == logic_operator_id:
                return self._ensure_operator_persisted(operator)
        return None

    def add_firefighter_to_team(self, operator: Operator):
        if not any(existing.vest_id == operator.vest_id for existing in self.active_operators):
            self.active_operators.append(operator)
        db_operator_id = self._ensure_operator_persisted(operator)
        self.database.attach_operator_to_session(self.session_id, db_operator_id)
        print(f"Operador {operator.name} ({operator.vest_id}) adicionado a missao.")

    def persist_operator_snapshot(self, operator: Operator, distance_m: Optional[float] = None):
        db_operator_id = self._ensure_operator_persisted(operator)
        self.database.insert_biometric(
            operator_id=db_operator_id,
            session_id=self.session_id,
            heart_rate=operator.biometrics.heart_rate,
            oxygenation=operator.biometrics.oxygenation,
            temperature=operator.biometrics.temperature,
            processing_delay_sec=operator.biometrics.processing_delay_sec,
        )
        self.database.insert_location(
            operator_id=db_operator_id,
            session_id=self.session_id,
            distance=distance_m,
            height=operator.location.height,
            lat=operator.location.latitude,
            long_value=operator.location.longitude,
        )

    def create_alert(self, alert: Alert):
        self.alerts_log.append(alert)
        db_operator_id = self._resolve_operator_db_id_from_logic_id(alert.operator_id)
        if db_operator_id is None:
            return
        self.database.insert_alert(
            operator_id=db_operator_id,
            session_id=self.session_id,
            description=alert.description,
        )

    def create_alert_for_operator(self, operator: Operator, description: str):
        self.create_alert(Alert(operator_id=operator.id, description=description))


class DashboardController:
    def __init__(self, database: Optional[ResQSenseDatabase] = None):
        self.database = database or get_database()
        self.current_session: Optional[Session] = None
        self.logged_in_leader: Optional[str] = None
        self.logged_in_leader_id: Optional[int] = None
        self.logged_in_role_id: Optional[int] = None

    def set_logged_in_leader(self, leader_name: str):
        normalized_name = leader_name.strip()
        if not normalized_name:
            return
        self.logged_in_leader = normalized_name
        leader_id, role_id = self.database.get_or_create_team_leader(normalized_name)
        self.logged_in_leader_id = leader_id
        self.logged_in_role_id = role_id
        self.database.insert_audit(
            action="leader_login",
            user_id=leader_id,
            role_id=role_id,
            target_table="team_leader",
            target_id=str(leader_id),
        )

    def start_new_session(self, leader_name: Optional[str] = None):
        if leader_name:
            self.set_logged_in_leader(leader_name)
        if not self.logged_in_leader:
            self.set_logged_in_leader("Unknown Leader")

        if self.logged_in_leader_id is None:
            self.set_logged_in_leader(self.logged_in_leader or "Unknown Leader")

        session_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.database.create_session(session_id=session_id, leader_id=int(self.logged_in_leader_id))
        self.current_session = Session(
            session_id=session_id,
            leader_id=int(self.logged_in_leader_id),
            database=self.database,
        )
        self.database.insert_audit(
            action="start_session",
            user_id=self.logged_in_leader_id,
            role_id=self.logged_in_role_id,
            target_table="session",
            target_id=session_id,
        )
        print(f"Sessao {session_id} iniciada.")

    def end_session(self):
        if not self.current_session:
            print("Nenhuma sessao ativa.")
            return

        session_id = self.current_session.session_id
        print(f"A terminar sessao {session_id}...")
        self.database.end_session(session_id)
        self.database.insert_audit(
            action="end_session",
            user_id=self.logged_in_leader_id,
            role_id=self.logged_in_role_id,
            target_table="session",
            target_id=session_id,
        )
        self.current_session = None

    def persist_operator_snapshot(self, operator: Operator, distance_m: Optional[float] = None):
        if not self.current_session:
            return
        self.current_session.persist_operator_snapshot(operator, distance_m=distance_m)

    def record_operator_alert(self, operator: Operator, description: str):
        if not self.current_session:
            return
        self.current_session.create_alert_for_operator(operator, description)
