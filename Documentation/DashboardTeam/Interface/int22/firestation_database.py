"""
SQLite persistence layer for ResQSense.
"""

import os
import sqlite3
import threading
from typing import Optional, Tuple


FIRE_STATIONS = [
    "Bombeiros Voluntarios de Aveiro",
    "Bombeiros Voluntarios de Agueda",
    "Bombeiros Voluntarios de Albergaria-a-Velha",
    "Bombeiros Voluntarios de Anadia",
    "Bombeiros Voluntarios de Coimbra",
    "Bombeiros Sapadores de Coimbra",
    "Bombeiros Voluntarios de Espinho",
    "Bombeiros Sapadores do Porto",
    "Bombeiros Voluntarios de Gaia",
    "Bombeiros Voluntarios de Matosinhos-Leixoes",
    "Bombeiros Sapadores de Braga",
    "Bombeiros Voluntarios de Guimaraes",
    "Bombeiros Voluntarios de Viseu",
    "Bombeiros Voluntarios de Leiria",
    "Bombeiros Voluntarios de Santarem",
    "Bombeiros Sapadores de Lisboa",
    "Bombeiros Voluntarios de Oeiras",
    "Bombeiros Voluntarios de Cascais",
    "Bombeiros Sapadores de Setubal",
    "Bombeiros Voluntarios de Faro",
]


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS role (
    id                      INTEGER PRIMARY KEY,
    name                    TEXT NOT NULL UNIQUE,
    can_view_biometrics     INTEGER DEFAULT 0,
    can_manage_sessions     INTEGER DEFAULT 0,
    can_manage_operators    INTEGER DEFAULT 0,
    can_manage_roles        INTEGER DEFAULT 0,
    can_access_database     INTEGER DEFAULT 0,
    description             TEXT
);

INSERT OR IGNORE INTO role (id, name, can_view_biometrics, can_manage_sessions, can_manage_operators, can_manage_roles, can_access_database, description)
VALUES
    (1, 'dev',          1, 1, 1, 1, 1, 'Equipa de desenvolvimento - acesso total'),
    (2, 'team_leader',  1, 1, 1, 0, 0, 'Chefe de equipa - gere sessoes e bombeiros'),
    (3, 'operator',     0, 0, 0, 0, 0, 'Bombeiro - apenas dados proprios basicos');

CREATE TABLE IF NOT EXISTS team_leader (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    pin_hash    TEXT NOT NULL,
    role_id     INTEGER NOT NULL DEFAULT 2,
    is_active   INTEGER DEFAULT 1,
    FOREIGN KEY (role_id) REFERENCES role(id)
);

CREATE TABLE IF NOT EXISTS operator (
    id                      INTEGER PRIMARY KEY,
    name                    TEXT NOT NULL,
    vest_id                 TEXT UNIQUE NOT NULL,
    is_alerting             INTEGER DEFAULT 0,
    baseline_heart_rate     INTEGER,
    baseline_temperature    REAL,
    baseline_oxygenation    INTEGER,
    role_id                 INTEGER NOT NULL DEFAULT 3,
    FOREIGN KEY (role_id) REFERENCES role(id)
);

CREATE TABLE IF NOT EXISTS session (
    session_id      TEXT PRIMARY KEY,
    leader_id       INTEGER NOT NULL,
    started_at      TEXT DEFAULT (datetime('now', 'localtime')),
    ended_at        TEXT,
    FOREIGN KEY (leader_id) REFERENCES team_leader(id)
);

CREATE TABLE IF NOT EXISTS session_operator (
    session_id      TEXT NOT NULL,
    operator_id     INTEGER NOT NULL,
    joined_at       TEXT DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (session_id, operator_id),
    FOREIGN KEY (session_id) REFERENCES session(session_id),
    FOREIGN KEY (operator_id) REFERENCES operator(id)
);

CREATE TABLE IF NOT EXISTS biometric_data (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_id             INTEGER NOT NULL,
    session_id              TEXT NOT NULL,
    heart_rate              INTEGER,
    oxygenation             INTEGER,
    temperature             REAL,
    processing_delay_sec    INTEGER,
    recorded_at             TEXT DEFAULT (datetime('now', 'localtime')),
    synced                  INTEGER DEFAULT 0,
    FOREIGN KEY (operator_id) REFERENCES operator(id),
    FOREIGN KEY (session_id) REFERENCES session(session_id)
);

CREATE TABLE IF NOT EXISTS location_data (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_id     INTEGER NOT NULL,
    session_id      TEXT NOT NULL,
    distance        REAL,
    height          REAL,
    lat             REAL,
    long            REAL,
    recorded_at     TEXT DEFAULT (datetime('now', 'localtime')),
    synced          INTEGER DEFAULT 0,
    FOREIGN KEY (operator_id) REFERENCES operator(id),
    FOREIGN KEY (session_id) REFERENCES session(session_id)
);

CREATE TABLE IF NOT EXISTS alert (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_id     INTEGER NOT NULL,
    session_id      TEXT NOT NULL,
    timestamp       TEXT DEFAULT (datetime('now', 'localtime')),
    description     TEXT NOT NULL,
    synced          INTEGER DEFAULT 0,
    FOREIGN KEY (operator_id) REFERENCES operator(id),
    FOREIGN KEY (session_id) REFERENCES session(session_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER,
    role_id         INTEGER,
    action          TEXT NOT NULL,
    target_table    TEXT,
    target_id       TEXT,
    timestamp       TEXT DEFAULT (datetime('now', 'localtime')),
    synced          INTEGER DEFAULT 0,
    FOREIGN KEY (role_id) REFERENCES role(id)
);

CREATE TABLE IF NOT EXISTS failed_login_attempt (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    leader_id       INTEGER,
    attempted_at    TEXT DEFAULT (datetime('now', 'localtime')),
    synced          INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biometric_operator ON biometric_data(operator_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_location_operator ON location_data(operator_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_alert_session ON alert(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_biometric_synced ON biometric_data(synced);
CREATE INDEX IF NOT EXISTS idx_location_synced ON location_data(synced);
CREATE INDEX IF NOT EXISTS idx_alert_synced ON alert(synced);
CREATE INDEX IF NOT EXISTS idx_audit_synced ON audit_log(synced);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action, timestamp);
CREATE INDEX IF NOT EXISTS idx_team_leader_role ON team_leader(role_id);
CREATE INDEX IF NOT EXISTS idx_operator_role ON operator(role_id);
"""


def get_fire_station_names():
    return sorted(FIRE_STATIONS)


class ResQSenseDatabase:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = os.getenv(
                "RESQSENSE_DB_PATH",
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "resqsense.db"),
            )
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
            self._conn.executescript(SCHEMA_SQL)
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    def get_or_create_team_leader(self, name: str, pin_hash: str = "NO_PIN") -> Tuple[int, int]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, role_id FROM team_leader WHERE name = ? LIMIT 1",
                (name,),
            ).fetchone()
            if row:
                return int(row["id"]), int(row["role_id"])

            cursor = self._conn.execute(
                "INSERT INTO team_leader (name, pin_hash, role_id, is_active) VALUES (?, ?, 2, 1)",
                (name, pin_hash),
            )
            self._conn.commit()
            return int(cursor.lastrowid), 2

    def create_session(self, session_id: str, leader_id: int):
        with self._lock:
            self._conn.execute(
                "INSERT INTO session (session_id, leader_id) VALUES (?, ?)",
                (session_id, leader_id),
            )
            self._conn.commit()

    def end_session(self, session_id: str):
        with self._lock:
            self._conn.execute(
                "UPDATE session SET ended_at = datetime('now', 'localtime') WHERE session_id = ? AND ended_at IS NULL",
                (session_id,),
            )
            self._conn.commit()

    def upsert_operator(
        self,
        name: str,
        vest_id: str,
        baseline_heart_rate: Optional[int] = None,
        baseline_temperature: Optional[float] = None,
        baseline_oxygenation: Optional[int] = None,
    ) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM operator WHERE vest_id = ? LIMIT 1",
                (vest_id,),
            ).fetchone()
            if row:
                operator_id = int(row["id"])
                self._conn.execute(
                    """
                    UPDATE operator
                    SET name = ?,
                        baseline_heart_rate = COALESCE(?, baseline_heart_rate),
                        baseline_temperature = COALESCE(?, baseline_temperature),
                        baseline_oxygenation = COALESCE(?, baseline_oxygenation)
                    WHERE id = ?
                    """,
                    (name, baseline_heart_rate, baseline_temperature, baseline_oxygenation, operator_id),
                )
                self._conn.commit()
                return operator_id

            cursor = self._conn.execute(
                """
                INSERT INTO operator
                    (name, vest_id, baseline_heart_rate, baseline_temperature, baseline_oxygenation, role_id)
                VALUES (?, ?, ?, ?, ?, 3)
                """,
                (name, vest_id, baseline_heart_rate, baseline_temperature, baseline_oxygenation),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def attach_operator_to_session(self, session_id: str, operator_id: int):
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO session_operator (session_id, operator_id) VALUES (?, ?)",
                (session_id, operator_id),
            )
            self._conn.commit()

    def set_operator_alerting(self, operator_id: int, is_alerting: bool):
        with self._lock:
            self._conn.execute(
                "UPDATE operator SET is_alerting = ? WHERE id = ?",
                (1 if is_alerting else 0, operator_id),
            )
            self._conn.commit()

    def insert_biometric(
        self,
        operator_id: int,
        session_id: str,
        heart_rate: Optional[int],
        oxygenation: Optional[int],
        temperature: Optional[float],
        processing_delay_sec: int = 0,
    ):
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO biometric_data
                    (operator_id, session_id, heart_rate, oxygenation, temperature, processing_delay_sec, synced)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (operator_id, session_id, heart_rate, oxygenation, temperature, processing_delay_sec),
            )
            self._conn.commit()

    def insert_location(
        self,
        operator_id: int,
        session_id: str,
        distance: Optional[float],
        height: Optional[float],
        lat: Optional[float],
        long_value: Optional[float],
    ):
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO location_data
                    (operator_id, session_id, distance, height, lat, long, synced)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (operator_id, session_id, distance, height, lat, long_value),
            )
            self._conn.commit()

    def insert_alert(self, operator_id: int, session_id: str, description: str):
        with self._lock:
            self._conn.execute(
                "INSERT INTO alert (operator_id, session_id, description, synced) VALUES (?, ?, ?, 0)",
                (operator_id, session_id, description),
            )
            self._conn.commit()

    def insert_audit(
        self,
        action: str,
        user_id: Optional[int] = None,
        role_id: Optional[int] = None,
        target_table: Optional[str] = None,
        target_id: Optional[str] = None,
    ):
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO audit_log (user_id, role_id, action, target_table, target_id, synced)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (user_id, role_id, action, target_table, target_id),
            )
            self._conn.commit()


_db_singleton: Optional[ResQSenseDatabase] = None
_db_singleton_lock = threading.Lock()


def get_database() -> ResQSenseDatabase:
    global _db_singleton
    with _db_singleton_lock:
        if _db_singleton is None:
            _db_singleton = ResQSenseDatabase()
        return _db_singleton
