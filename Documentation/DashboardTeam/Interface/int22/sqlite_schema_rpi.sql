-- ============================================
-- ResQSense Wearion - SQLite Schema (Raspberry Pi)
-- Base de dados operacional / tempo real
-- RGPD: Usar SQLCipher para encriptação total
-- ============================================

-- ============================================
-- RBAC - Controlo de Acesso por Roles
-- ============================================

-- Perfis de acesso
CREATE TABLE IF NOT EXISTS role (
    id                      INTEGER PRIMARY KEY,
    name                    TEXT NOT NULL UNIQUE,
    can_view_biometrics     INTEGER DEFAULT 0,  -- 0=false, 1=true
    can_manage_sessions     INTEGER DEFAULT 0,
    can_manage_operators    INTEGER DEFAULT 0,
    can_manage_roles        INTEGER DEFAULT 0,
    can_access_database     INTEGER DEFAULT 0,
    description             TEXT
);

-- Roles padrão do sistema
INSERT OR IGNORE INTO role (id, name, can_view_biometrics, can_manage_sessions, can_manage_operators, can_manage_roles, can_access_database, description)
VALUES
    (1, 'dev',          1, 1, 1, 1, 1, 'Equipa de desenvolvimento - acesso total'),
    (2, 'team_leader',  1, 1, 1, 0, 0, 'Chefe de equipa - gere sessões e bombeiros'),
    (3, 'operator',     0, 0, 0, 0, 0, 'Bombeiro - apenas dados próprios básicos');

-- ============================================
-- UTILIZADORES E OPERADORES
-- ============================================

-- Team Leaders (cache local para autenticação)
CREATE TABLE IF NOT EXISTS team_leader (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    pin_hash    TEXT NOT NULL,
    role_id     INTEGER NOT NULL DEFAULT 2,
    is_active   INTEGER DEFAULT 1,
    FOREIGN KEY (role_id) REFERENCES role(id)
);

-- Operadores / Bombeiros
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

-- ============================================
-- SESSÕES
-- ============================================

-- Sessão ativa
CREATE TABLE IF NOT EXISTS session (
    session_id      TEXT PRIMARY KEY,
    leader_id       INTEGER NOT NULL,
    started_at      TEXT DEFAULT (datetime('now', 'localtime')),
    ended_at        TEXT,
    FOREIGN KEY (leader_id) REFERENCES team_leader(id)
);

-- Operadores atribuídos a uma sessão
CREATE TABLE IF NOT EXISTS session_operator (
    session_id      TEXT NOT NULL,
    operator_id     INTEGER NOT NULL,
    joined_at       TEXT DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (session_id, operator_id),
    FOREIGN KEY (session_id) REFERENCES session(session_id),
    FOREIGN KEY (operator_id) REFERENCES operator(id)
);

-- ============================================
-- DADOS SENSÍVEIS (RGPD - Interesse Vital)
-- Pseudonimização: operator_id separado dos dados de saúde
-- Encriptação em repouso: SQLCipher encripta toda a BD
-- Minimização: apenas variáveis definidas nas classes
-- ============================================

-- Dados biométricos (chegam em tempo real dos coletes)
CREATE TABLE IF NOT EXISTS biometric_data (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_id             INTEGER NOT NULL,
    session_id              TEXT NOT NULL,
    heart_rate              INTEGER,       -- RGPD: dado de saúde sensível
    oxygenation             INTEGER,       -- RGPD: dado de saúde sensível
    temperature             REAL,          -- RGPD: dado de saúde sensível
    processing_delay_sec    INTEGER,
    recorded_at             TEXT DEFAULT (datetime('now', 'localtime')),
    synced                  INTEGER DEFAULT 0,
    FOREIGN KEY (operator_id) REFERENCES operator(id),
    FOREIGN KEY (session_id) REFERENCES session(session_id)
);

-- Dados de localização (chegam em tempo real dos coletes)
CREATE TABLE IF NOT EXISTS location_data (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_id     INTEGER NOT NULL,
    session_id      TEXT NOT NULL,
    distance        REAL,
    height          REAL,
    lat             REAL,               -- RGPD: dado de geolocalização sensível
    long            REAL,               -- RGPD: dado de geolocalização sensível
    recorded_at     TEXT DEFAULT (datetime('now', 'localtime')),
    synced          INTEGER DEFAULT 0,
    FOREIGN KEY (operator_id) REFERENCES operator(id),
    FOREIGN KEY (session_id) REFERENCES session(session_id)
);

-- Alertas (Man Down, ritmo cardíaco rápido, queda, etc.)
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

-- ============================================
-- AUDIT LOG (RGPD - Accountability)
-- Regista ações sem expor métricas de saúde
-- ============================================

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

-- Tentativas de login falhadas
CREATE TABLE IF NOT EXISTS failed_login_attempt (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    leader_id       INTEGER,
    attempted_at    TEXT DEFAULT (datetime('now', 'localtime')),
    synced          INTEGER DEFAULT 0
);

-- ============================================
-- ÍNDICES
-- ============================================

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
