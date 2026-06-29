import sqlite3
import psycopg2

# ============================================
# CONFIGURAÇÃO - AJUSTAR ANTES DE USAR
# ============================================

SQLITE_PATH = '/home/pee/Desktop/int21/resqsense.db'

ENCRYPTION_KEY = 'CHAVE_SECRETA'

PG_CONFIG = {
    "host": "10.42.0.1",
    "port": 5432,
    "user": "resqsense_dev",
    "password": "MUDAR_PASSWORD_DEV",
    "database": "resqsense"
}

def enc(conn_pg, value, key):
    """Encripta um valor — se for None devolve NULL."""
    if value is None:
        return None
    cur = conn_pg.cursor()
    cur.execute("SELECT pgp_sym_encrypt(%s::text, %s)", (str(value), key))
    result = cur.fetchone()[0]
    cur.close()
    return result

# ============================================
# FUNÇÃO PRINCIPAL DE SINCRONIZAÇÃO
# ============================================

def sincronizar_para_postgresql():
    conn_sqlite = None
    conn_pg = None

    try:
        conn_sqlite = sqlite3.connect(SQLITE_PATH)
        conn_sqlite.row_factory = sqlite3.Row
        cur_sqlite = conn_sqlite.cursor()

        conn_pg = psycopg2.connect(**PG_CONFIG)
        cur_pg = conn_pg.cursor()

        print("[SYNC] Ligação estabelecida. A sincronizar...")

        # ----------------------------------------
        # 1. team_leader
        # ----------------------------------------
        try:
            cur_sqlite.execute("SELECT * FROM team_leader")
            rows = cur_sqlite.fetchall()
            for row in rows:
                cur_pg.execute("""
                    INSERT INTO team_leader (id, name, pin_hash, role_id, is_active, created_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (id) DO UPDATE
                        SET name      = EXCLUDED.name,
                            pin_hash  = EXCLUDED.pin_hash,
                            is_active = EXCLUDED.is_active
                """, (row['id'], row['name'], row['pin_hash'],
                      row['role_id'], bool(row['is_active'])))
            conn_pg.commit()
            print(f"[SYNC] team_leader: {len(rows)} registo(s)")
        except Exception as e:
            conn_pg.rollback()
            print(f"[SYNC] ERRO team_leader: {e}")

        # ----------------------------------------
        # 2. operator
        # ----------------------------------------
        try:
            cur_sqlite.execute("SELECT * FROM operator")
            rows = cur_sqlite.fetchall()
            for row in rows:
                cur_pg.execute("""
                    INSERT INTO operator
                        (id, name, vest_id, is_alerting, baseline_heart_rate,
                         baseline_temperature, baseline_oxygenation, role_id, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (id) DO UPDATE
                        SET name                 = EXCLUDED.name,
                            vest_id              = EXCLUDED.vest_id,
                            is_alerting          = EXCLUDED.is_alerting,
                            baseline_heart_rate  = EXCLUDED.baseline_heart_rate,
                            baseline_temperature = EXCLUDED.baseline_temperature,
                            baseline_oxygenation = EXCLUDED.baseline_oxygenation
                """, (row['id'], row['name'], row['vest_id'],
                      bool(row['is_alerting']),
                      row['baseline_heart_rate'], row['baseline_temperature'],
                      row['baseline_oxygenation'], row['role_id']))
            conn_pg.commit()
            print(f"[SYNC] operator: {len(rows)} registo(s)")
        except Exception as e:
            conn_pg.rollback()
            print(f"[SYNC] ERRO operator: {e}")

        # ----------------------------------------
        # 3. session
        # ----------------------------------------
        try:
            cur_sqlite.execute("SELECT * FROM session")
            rows = cur_sqlite.fetchall()
            for row in rows:
                cur_pg.execute("""
                    INSERT INTO session (session_id, leader_id, started_at, ended_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (session_id) DO UPDATE
                        SET ended_at = EXCLUDED.ended_at
                """, (row['session_id'], row['leader_id'],
                      row['started_at'], row['ended_at']))
            conn_pg.commit()
            print(f"[SYNC] session: {len(rows)} registo(s)")
        except Exception as e:
            conn_pg.rollback()
            print(f"[SYNC] ERRO session: {e}")

        # ----------------------------------------
        # 4. session_operator
        # ----------------------------------------
        try:
            cur_sqlite.execute("SELECT * FROM session_operator")
            rows = cur_sqlite.fetchall()
            for row in rows:
                cur_pg.execute("""
                    INSERT INTO session_operator (session_id, operator_id, joined_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (session_id, operator_id) DO NOTHING
                """, (row['session_id'], row['operator_id'], row['joined_at']))
            conn_pg.commit()
            print(f"[SYNC] session_operator: {len(rows)} registo(s)")
        except Exception as e:
            conn_pg.rollback()
            print(f"[SYNC] ERRO session_operator: {e}")

        # ----------------------------------------
        # 5. biometric_data
        # ----------------------------------------
        try:
            cur_sqlite.execute("SELECT * FROM biometric_data WHERE synced = 0")
            rows = cur_sqlite.fetchall()
            for row in rows:
                hr_enc  = enc(conn_pg, row['heart_rate'],  ENCRYPTION_KEY)
                oxy_enc = enc(conn_pg, row['oxygenation'], ENCRYPTION_KEY)
                tmp_enc = enc(conn_pg, row['temperature'], ENCRYPTION_KEY)
                cur_pg.execute("""
                    INSERT INTO biometric_data
                        (operator_id, session_id, heart_rate_enc, oxygenation_enc,
                         temperature_enc, processing_delay_sec, recorded_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (row['operator_id'], row['session_id'],
                      hr_enc, oxy_enc, tmp_enc,
                      row['processing_delay_sec'], row['recorded_at']))
                cur_sqlite.execute(
                    "UPDATE biometric_data SET synced = 1 WHERE id = ?", (row['id'],))
            conn_pg.commit()
            conn_sqlite.commit()
            print(f"[SYNC] biometric_data: {len(rows)} registo(s)")
        except Exception as e:
            conn_pg.rollback()
            print(f"[SYNC] ERRO biometric_data: {e}")

        # ----------------------------------------
        # 6. location_data
        # ----------------------------------------
        try:
            cur_sqlite.execute("SELECT * FROM location_data WHERE synced = 0")
            rows = cur_sqlite.fetchall()
            for row in rows:
                lat_enc  = enc(conn_pg, row['lat'],  ENCRYPTION_KEY)
                long_enc = enc(conn_pg, row['long'], ENCRYPTION_KEY)
                cur_pg.execute("""
                    INSERT INTO location_data
                        (operator_id, session_id, distance, height,
                         lat_enc, long_enc, recorded_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (row['operator_id'], row['session_id'],
                      row['distance'], row['height'],
                      lat_enc, long_enc, row['recorded_at']))
                cur_sqlite.execute(
                    "UPDATE location_data SET synced = 1 WHERE id = ?", (row['id'],))
            conn_pg.commit()
            conn_sqlite.commit()
            print(f"[SYNC] location_data: {len(rows)} registo(s)")
        except Exception as e:
            conn_pg.rollback()
            print(f"[SYNC] ERRO location_data: {e}")

        # ----------------------------------------
        # 7. alert
        # ----------------------------------------
        try:
            cur_sqlite.execute("SELECT * FROM alert WHERE synced = 0")
            rows = cur_sqlite.fetchall()
            for row in rows:
                cur_pg.execute("""
                    INSERT INTO alert (operator_id, session_id, timestamp, description)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (row['operator_id'], row['session_id'],
                      row['timestamp'], row['description']))
                cur_sqlite.execute(
                    "UPDATE alert SET synced = 1 WHERE id = ?", (row['id'],))
            conn_pg.commit()
            conn_sqlite.commit()
            print(f"[SYNC] alert: {len(rows)} registo(s)")
        except Exception as e:
            conn_pg.rollback()
            print(f"[SYNC] ERRO alert: {e}")

        # ----------------------------------------
        # 8. audit_log
        # ----------------------------------------
        try:
            cur_sqlite.execute("SELECT * FROM audit_log WHERE synced = 0")
            rows = cur_sqlite.fetchall()
            for row in rows:
                cur_pg.execute("""
                    INSERT INTO audit_log
                        (user_id, role_id, action, target_table, target_id, timestamp)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (row['user_id'], row['role_id'], row['action'],
                      row['target_table'], row['target_id'], row['timestamp']))
                cur_sqlite.execute(
                    "UPDATE audit_log SET synced = 1 WHERE id = ?", (row['id'],))
            conn_pg.commit()
            conn_sqlite.commit()
            print(f"[SYNC] audit_log: {len(rows)} registo(s)")
        except Exception as e:
            conn_pg.rollback()
            print(f"[SYNC] ERRO audit_log: {e}")

        # ----------------------------------------
        # 9. failed_login_attempt
        # ----------------------------------------
        try:
            cur_sqlite.execute("SELECT * FROM failed_login_attempt WHERE synced = 0")
            rows = cur_sqlite.fetchall()
            for row in rows:
                cur_pg.execute("""
                    INSERT INTO failed_login_attempt (leader_id, attempted_at)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                """, (row['leader_id'], row['attempted_at']))
                cur_sqlite.execute(
                    "UPDATE failed_login_attempt SET synced = 1 WHERE id = ?", (row['id'],))
            conn_pg.commit()
            conn_sqlite.commit()
            print(f"[SYNC] failed_login_attempt: {len(rows)} registo(s)")
        except Exception as e:
            conn_pg.rollback()
            print(f"[SYNC] ERRO failed_login_attempt: {e}")

        print("[SYNC] Sincronização concluída.")

    except psycopg2.OperationalError as e:
        print(f"[SYNC] Erro de ligação ao PostgreSQL: {e}")

    except Exception as e:
        print(f"[SYNC] Erro inesperado: {e}")

    finally:
        if conn_sqlite:
            conn_sqlite.close()
        if conn_pg:
            conn_pg.close()
