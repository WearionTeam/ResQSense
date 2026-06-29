import tkinter as tk
from tkinter import ttk, messagebox
import datetime
import math
import os
import random
import time

# IMPORTAÃ‡ÃƒO DOS OUTROS SCRIPTS
from models import DashboardController, Operator
from simulation import HardwareSimulator
from autocomplete_entry import AutocompleteEntry
from firestation_database import get_fire_station_names
from buzzer_controller import BuzzerController
from magnetometer_controller import MagnetometerController
from speaker_controller import SpeakerController
from telemetry_protocol import UARTTelemetryReceiver
from lora_telemetry_receiver import LoRaTelemetryReceiver

from language_config import LANGUAGE_OPTIONS, get_language_label, translate
import threading
from sync_postgresql import sincronizar_para_postgresql

class ResQSenseApp:
    def __init__(self, root):
        self.root = root
        self.root.title("ResQSense.exe")

        # Ajusta automaticamente ao ecra (importante para Raspberry Pi 800x480)
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        window_w = min(1000, max(760, screen_w - 40))
        window_h = min(600, max(460, screen_h - 80))
        window_w = min(window_w, screen_w)
        window_h = min(window_h, screen_h)
        pos_x = max(0, (screen_w - window_w) // 2)
        pos_y = max(0, (screen_h - window_h) // 2)
        self.root.geometry(f"{window_w}x{window_h}+{pos_x}+{pos_y}")
        self.root.minsize(min(760, window_w), min(460, window_h))
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Inicializa o speaker passivo por software no GPIO 17.
        try:
            speaker_pin = int(os.getenv("RESQSENSE_SPEAKER_PIN", "17"))
        except ValueError:
            speaker_pin = 17
        self.speaker = SpeakerController(pin=speaker_pin)
        self.buzzer = None
        if not self.speaker.available():
            self.buzzer = BuzzerController(pin=17)
        self.magnetometer = MagnetometerController(update_interval_sec=0.15)
        self.magnetometer.start()
        
        # INICIALIZA O CONTROLADOR (BACKEND)
        self.controller = DashboardController()
        
        # Cores baseadas no wireframe original
        self.bg_color = "#d9d9d9"
        self.header_color = "#d9d9d9"
        self.sidebar_color = "#d9d9d9"
        self.alert_color = "#e74c3c" # Vermelho para alertas
        self.safe_color = "#2ecc71"  # Verde para seguro
        self.avatar_fill = "white"   # Cor de preenchimento do boneco
        self.avatar_outline = "black"
        
        self.root.configure(bg=self.bg_color)
        
        self.team_leader_name = ""
        self.fire_station_name = ""
        self.max_operationals = 5
        self.radar_ring_count = 5
        self.radar_ring_step_m = 40
        self.radar_max_distance_m = self.radar_ring_count * self.radar_ring_step_m
        
        # Lista HÃ­brida: { "id": "1", "name": "...", "angle": 0.0, "dist": 0.5, "logic_ref": OperatorObject, "is_leader": False }
        self.firefighters = []

        # Estado da aplicaÃ§Ã£o
        self.edit_mode = False 
        self.menu_open = False 
        self.blink_state = False 
        self.active_critical_signatures = set()
        self.pending_man_down_alerts = []
        self.warning_history = []
        self.current_alert_popup = None
        self.current_popup_data = None
        self.selected_firefighter_id = None
        self.session_panel_visible = False
        self.session_info_panel = None
        self.session_name_label = None
        self.dashboard_active = False
        self.simulation_job = None
        self.current_language = "en"
        self.language_window = None
        self.active_input_widget = None
        self.firefighter_touch_last_y = None
        self.firefighter_touch_dragging = False
        self.firefighter_touch_clear_job = None
        self.sync_requests_in_flight = set()
        self.telemetry_receiver = None
        self.live_data_enabled = False
        ports_env = os.getenv("RESQSENSE_UART_PORTS", "").strip()
        if ports_env:
            self.live_data_ports = [part.strip() for part in ports_env.split(",") if part.strip()]
        else:
            single_port = os.getenv("RESQSENSE_UART_PORT", "/dev/cu.usbserial-10").strip()
            self.live_data_ports = [single_port] if single_port else ["/dev/cu.usbserial-10"]
        try:
            self.live_data_baudrate = int(os.getenv("RESQSENSE_UART_BAUD", "115200"))
        except ValueError:
            self.live_data_baudrate = 115200
        try:
            self.signal_loss_timeout_sec = float(os.getenv("RESQSENSE_SIGNAL_LOSS_TIMEOUT", "20"))
        except ValueError:
            self.signal_loss_timeout_sec = 20.0
        self.telemetry_transport = os.getenv("RESQSENSE_TELEMETRY_TRANSPORT", "lora").strip().lower()
        try:
            self.lora_channel = int(os.getenv("RESQSENSE_LORA_CHANNEL", "7"))
        except ValueError:
            self.lora_channel = 7
        self.force_simulation_mode = os.getenv("RESQSENSE_FORCE_SIMULATION", "0").strip() == "1"
        leader_vest_env = os.getenv("RESQSENSE_LEADER_VEST_ID", "").strip()
        self.leader_vest_id = leader_vest_env if leader_vest_env else None
        self.radar_origin = None
        self.last_heading_deg = 0.0
        self.radar_fullscreen = False
        self.previous_window_geometry = None
        self.previous_window_fullscreen = False

        self.login_station_label = None
        self.login_name_label = None
        self.login_ops_label = None
        self.login_button = None
        self.sidebar_title_label = None
        self.menu_edit_button = None
        self.menu_resources_button = None
        self.menu_language_button = None
        self.end_session_button = None
        self.add_operational_button = None
        self.header_frame = None
        self.header_separator = None
        self.main_container = None
        self.map_area = None
        self.footer = None
        self.radar_fullscreen_button = None
        self.radar_restore_button = None

        # Inicia com a tela de Login
        self.show_login_screen()

    def clear_window(self):
        """Limpa todos os widgets da janela atual"""
        if getattr(self, "radar_fullscreen", False):
            self.exit_radar_fullscreen()
        self.close_language_window()
        self.active_input_widget = None
        for widget in self.root.winfo_children():
            widget.destroy()

    def t(self, key, **kwargs):
        return translate(self.current_language, key, **kwargs)

    def get_language_name(self, code=None):
        selected = code if code else self.current_language
        return get_language_label(selected)

    def set_language(self, language_code):
        valid_codes = {code for _, code in LANGUAGE_OPTIONS}
        if language_code not in valid_codes:
            return

        self.current_language = language_code

        if self.menu_open:
            self.menu_frame.place_forget()
            self.menu_open = False

        self.apply_translations_to_visible_widgets()
        self.refresh_sidebar_list_safe()
        self.draw_radar_safe()

    def apply_translations_to_visible_widgets(self):
        if self.login_station_label and self.login_station_label.winfo_exists():
            self.login_station_label.config(text=self.t("fire_station"))
        if self.login_name_label and self.login_name_label.winfo_exists():
            self.login_name_label.config(text=self.t("name_id"))
        if self.login_ops_label and self.login_ops_label.winfo_exists():
            self.login_ops_label.config(text=self.t("nr_operationals"))
        if self.login_button and self.login_button.winfo_exists():
            self.login_button.config(text=self.t("login"))

        if self.sidebar_title_label and self.sidebar_title_label.winfo_exists():
            self.sidebar_title_label.config(text=self.t("operational"))

        if self.menu_edit_button and self.menu_edit_button.winfo_exists():
            self.menu_edit_button.config(text=self.t("menu_edit"))
        if self.menu_resources_button and self.menu_resources_button.winfo_exists():
            self.menu_resources_button.config(text=self.t("menu_resources"))
        if self.menu_language_button and self.menu_language_button.winfo_exists():
            self.menu_language_button.config(text=self.t("menu_language"))

        if self.session_name_label and self.session_name_label.winfo_exists():
            self.session_name_label.config(text=self.t("session_name", name=self.team_leader_name))
        if self.end_session_button and self.end_session_button.winfo_exists():
            self.end_session_button.config(text=self.t("end_session"))
        if self.add_operational_button and self.add_operational_button.winfo_exists():
            self.add_operational_button.config(text=f"+ {self.t('add_operational_title')}")

    def refresh_sidebar_list_safe(self):
        if not self.dashboard_active:
            return
        if not hasattr(self, "firefighter_list_frame"):
            return
        if not self.firefighter_list_frame or not self.firefighter_list_frame.winfo_exists():
            return
        self.refresh_sidebar_list()

    def draw_radar_safe(self):
        if not self.dashboard_active:
            return
        if not hasattr(self, "canvas"):
            return
        if not self.canvas or not self.canvas.winfo_exists():
            return
        self.draw_radar()

    def stop_alert_audio(self):
        self.speaker.set_alarm(False)
        self.speaker.stop()
        if self.buzzer:
            self.buzzer.off()

    def trigger_emergency_sound(self):
        if self.speaker.available():
            self.speaker.emergency_burst()
            return
        if self.buzzer:
            self.buzzer.emergency_burst()

    def set_continuous_alarm_sound(self, is_active):
        if self.speaker.available():
            self.speaker.set_alarm(is_active)
        elif self.buzzer:
            self.buzzer.set_alarm(is_active)

    def get_device_heading_radians(self):
        if self.magnetometer and self.magnetometer.available():
            heading_deg = self.magnetometer.get_heading_degrees()
            self.last_heading_deg = heading_deg
            return math.radians(heading_deg)
        return math.radians(self.last_heading_deg)

    def start_live_data_receiver(self):
        if self.force_simulation_mode:
            self.telemetry_receiver = None
            self.live_data_enabled = False
            print("[UART] Modo simulacao ativo. Rececao de dados reais desativada.")
            return

        if self.live_data_enabled:
            return

        try:
            if self.telemetry_transport == "lora":
                self.telemetry_receiver = LoRaTelemetryReceiver(channel=self.lora_channel)
            else:
                self.telemetry_receiver = UARTTelemetryReceiver(
                    ports=self.live_data_ports,
                    baudrate=self.live_data_baudrate,
                )
            self.telemetry_receiver.start()
            self.live_data_enabled = True
            if self.telemetry_transport == "lora":
                print(f"[LORA] Rececao ativa no canal {self.lora_channel}")
            else:
                ports_text = ", ".join(self.live_data_ports)
                print(f"[UART] Rececao ativa em {ports_text} @ {self.live_data_baudrate}")
        except Exception as exc:
            self.telemetry_receiver = None
            self.live_data_enabled = False
            print(f"[{self.telemetry_transport.upper()}] Falha ao iniciar rececao ({exc}). Mantida simulacao.")

    def stop_live_data_receiver(self):
        if self.telemetry_receiver is not None:
            self.telemetry_receiver.stop()
        self.telemetry_receiver = None
        self.live_data_enabled = False

    def ensure_firefighter_for_vest(self, vest_id):
        vest_key = str(vest_id)
        for firefighter in self.firefighters:
            firefighter_vest = str(firefighter.get("vest_id", firefighter.get("id", ""))).strip()
            if firefighter_vest == vest_key:
                return firefighter
        return None

    def firefighter_has_numeric_vest(self, firefighter):
        return str(firefighter.get("vest_id", "")).strip().isdigit()

    def get_leader_firefighter(self):
        for firefighter in self.firefighters:
            if firefighter.get("is_leader", False):
                return firefighter
        return None

    def get_firefighter_gps(self, firefighter):
        if not firefighter:
            return None
        latitude = firefighter.get("gps_lat")
        longitude = firefighter.get("gps_lon")
        if latitude is None or longitude is None:
            return None
        return latitude, longitude

    def calculate_gps_vector(self, origin_lat, origin_lon, latitude, longitude):
        mean_lat = math.radians((origin_lat + latitude) / 2.0)
        meters_per_deg_lat = 111320.0
        meters_per_deg_lon = max(1.0, 111320.0 * math.cos(mean_lat))

        delta_x = (longitude - origin_lon) * meters_per_deg_lon
        delta_y = (latitude - origin_lat) * meters_per_deg_lat
        distance_m = math.hypot(delta_x, delta_y)
        angle_rad = math.atan2(delta_x, delta_y)
        return angle_rad, distance_m

    def set_firefighter_radar_vector(self, firefighter, origin_lat, origin_lon):
        gps = self.get_firefighter_gps(firefighter)
        if gps is None:
            return False

        latitude, longitude = gps
        angle_rad, distance_m = self.calculate_gps_vector(origin_lat, origin_lon, latitude, longitude)

        firefighter["angle"] = angle_rad
        if firefighter.get("is_leader", False):
            firefighter["dist"] = 0.0
        else:
            firefighter["dist"] = max(0.0, min(1.0, distance_m / self.radar_max_distance_m))

        firefighter["distance_m"] = distance_m
        firefighter["logic_ref"].location.distance_relative = distance_m
        return True

    def recalculate_radar_positions_from_leader(self):
        leader = self.get_leader_firefighter()
        leader_gps = self.get_firefighter_gps(leader)
        if leader_gps is None:
            return

        origin_lat, origin_lon = leader_gps
        self.radar_origin = leader_gps

        for firefighter in self.firefighters:
            self.set_firefighter_radar_vector(firefighter, origin_lat, origin_lon)

    def get_vest_mac_for_sync(self, vest_id):
     vest_key = str(vest_id).strip()

     # Mapeamento dos MACs! 
     # Coloca aqui os valores que o teu colega definiu no config.h
     MAC_MAPPING = {
         "0": "99A498C4", 
         "1": "11223344",
         "2": "11887766",
           
     }

     raw_mac = MAC_MAPPING.get(vest_key)
     if not raw_mac:
         print(f"[LORA] Sem MAC configurado para Vest {vest_key}; SYNC nao enviado.")
         return None

     raw_mac = raw_mac.replace(":", "").replace("-", "").replace("0x", "").replace("0X", "").strip()
     try:
         return int(raw_mac[-8:], 16)
     except ValueError:
         return None

    def request_sync_for_firefighter(self, firefighter):
        if not self.live_data_enabled or self.telemetry_receiver is None:
            return
        if not hasattr(self.telemetry_receiver, "send_sync"):
            return

        vest_id = firefighter.get("vest_id", firefighter.get("id"))
        if vest_id is None:
            return

        mac = self.get_vest_mac_for_sync(vest_id)
        if mac is None:
            print(f"[LORA] SYNC nao enviado: MAC invalido para Vest {vest_id}")
            return

        receiver = self.telemetry_receiver
        vest_key = str(vest_id)
        if vest_key in self.sync_requests_in_flight:
            return
        self.sync_requests_in_flight.add(vest_key)

        def sync_worker():
            try:
                # Usa o MESMO canal para dados e controlo. Com um unico radio, o
                # dashboard so consegue escutar um canal de cada vez; se os
                # alertas (SOS/botao, man-down, vitais) fossem no canal de
                # controlo e a telemetria no de dados, os alertas nunca seriam
                # ouvidos. Unificando, o colete passa a enviar tudo no canal
                # onde o dashboard fica a escutar.
                sent = receiver.send_sync(
                    int(vest_id),
                    mac,
                    data_ch=self.lora_channel,
                    ctrl_ch=self.lora_channel,
                )
                if sent:
                    print(f"[LORA] SYNC concluido para operacional {firefighter.get('name')} / Vest {vest_id}")
                else:
                    print(f"[LORA] SYNC enviado mas sem ACK para Vest {vest_id}")
            except Exception as exc:
                print(f"[LORA] Erro ao enviar SYNC para Vest {vest_id}: {exc}")
            finally:
                self.sync_requests_in_flight.discard(vest_key)

        try:
            threading.Thread(
                target=sync_worker,
                daemon=True,
                name=f"sync-vest-{vest_id}",
            ).start()
            print(f"[LORA] SYNC pedido para operacional {firefighter.get('name')} / Vest {vest_id}")
        except Exception as exc:
            self.sync_requests_in_flight.discard(vest_key)
            print(f"[LORA] Erro ao iniciar thread de SYNC para Vest {vest_id}: {exc}")

    def request_unsync_for_firefighter(self, firefighter):
        if not self.live_data_enabled or self.telemetry_receiver is None:
            return
        if not hasattr(self.telemetry_receiver, "send_unsync"):
            return
        vest_id = firefighter.get("vest_id", firefighter.get("id"))
        if vest_id is None:
            return

        receiver = self.telemetry_receiver

        def unsync_worker():
            try:
                sent = receiver.send_unsync(int(vest_id))
                if sent:
                    print(f"[LORA] UNSYNC concluido para operacional {firefighter.get('name')} / Vest {vest_id}")
                else:
                    print(f"[LORA] UNSYNC sem confirmacao para operacional {firefighter.get('name')} / Vest {vest_id}")
            except Exception as exc:
                print(f"[LORA] Erro ao enviar UNSYNC para Vest {vest_id}: {exc}")

        try:
            threading.Thread(
                target=unsync_worker,
                daemon=True,
                name=f"unsync-vest-{vest_id}",
            ).start()
            print(f"[LORA] UNSYNC pedido para operacional {firefighter.get('name')} / Vest {vest_id}")
        except Exception as exc:
            print(f"[LORA] Erro ao iniciar thread de UNSYNC para Vest {vest_id}: {exc}")

    def update_radar_position_from_gps(self, firefighter, latitude, longitude):
        firefighter["gps_lat"] = latitude
        firefighter["gps_lon"] = longitude

        if firefighter.get("is_leader", False):
            self.recalculate_radar_positions_from_leader()
            return

        leader = self.get_leader_firefighter()
        leader_gps = self.get_firefighter_gps(leader)
        if leader_gps is None:
            return

        origin_lat, origin_lon = leader_gps
        self.radar_origin = leader_gps
        self.set_firefighter_radar_vector(firefighter, origin_lat, origin_lon)

    def apply_incoming_packet(self, packet):
        print(f"[DASHBOARD] Pacote recebido: {packet}")
        if packet.get("tipo") == "PORT_STATUS":
            status = packet.get("status", "unknown")
            port = packet.get("port", "unknown")
            print(f"[TELEMETRY] Porta {port}: {status}")
            return

        vest_id = packet.get("vest_id")
        if vest_id is None:
            return

        firefighter = self.ensure_firefighter_for_vest(vest_id)
        if firefighter is None:
            print(f"[DASHBOARD] Colete {vest_id} ignorado: operacional ainda nao criado/associado.")
            return

        now_ts = time.time()
        firefighter["last_seen"] = now_ts
        firefighter["signal_lost"] = False
        print(f"[DASHBOARD] Colete {vest_id} associado a {firefighter.get('name')}")

        packet_type = packet.get("tipo")
        if packet_type in ("COMPLETA", "DELTA_GPS"):
            op_logic = firefighter["logic_ref"]
            mapped_data = {}
            if "bpm" in packet:
                mapped_data["hr"] = packet["bpm"]
            if "spo2" in packet and packet["spo2"] > 0:
                mapped_data["spo2"] = packet["spo2"]
            if "temperature" in packet:
                mapped_data["temp"] = packet["temperature"]
            if "lat" in packet:
                mapped_data["lat"] = packet["lat"]
            if "lon" in packet:
                mapped_data["long"] = packet["lon"]
            if "altitude" in packet:
                mapped_data["height"] = packet["altitude"]

            if mapped_data:
                print(f"[DASHBOARD] Dados aplicados ao Vest {vest_id}: {mapped_data}")
                op_logic.update_detail(mapped_data)

            latitude = packet.get("lat")
            longitude = packet.get("lon")
            if latitude is not None and longitude is not None:
                self.update_radar_position_from_gps(firefighter, latitude, longitude)

            flags = packet.get("flags", {})
            warnings = []
            if flags.get("mandown") or flags.get("sos"):
                warnings.append("FALL")
            if flags.get("hw") or flags.get("pest"):
                warnings.append("POOR CONNECTION")

            if flags.get("vital"):
                _is_critical, health_warnings = op_logic.check_health_status()
                for warning in health_warnings:
                    if warning not in warnings:
                        warnings.append(warning)

            # Preserve SOS BUTTON from a control packet that arrived in the same
            # drain batch – the COMPLETA would otherwise wipe it before the
            # simulation cycle gets a chance to queue the popup.
            if firefighter.get("sos_button_active") and "SOS BUTTON" not in warnings:
                warnings.append("SOS BUTTON")

            if warnings:
                firefighter["status"] = "no_signal" if warnings == ["POOR CONNECTION"] else "critical"
                firefighter["active_warnings"] = warnings
            else:
                firefighter["status"] = "normal"
                firefighter["active_warnings"] = []
            return

        warning_code = packet.get("warning_code")
        if warning_code:
            existing = list(firefighter.get("active_warnings", []))
            if warning_code not in existing:
                existing.append(warning_code)
            firefighter["active_warnings"] = existing
            firefighter["status"] = "no_signal" if warning_code == "POOR CONNECTION" else "critical"
            if warning_code == "SOS BUTTON":
                firefighter["sos_button_active"] = True

    def drain_incoming_packets(self):
        if not self.live_data_enabled or self.telemetry_receiver is None:
            return 0

        packets = self.telemetry_receiver.drain(max_items=120)
        for packet in packets:
            self.apply_incoming_packet(packet)
        return len(packets)

    def open_language_window(self):
        if self.menu_open:
            self.menu_frame.place_forget()
            self.menu_open = False

        if self.language_window and self.language_window.winfo_exists():
            self.language_window.lift()
            return

        popup = tk.Toplevel(self.root)
        popup.title(self.t("language_title"))
        popup.geometry("330x430")
        popup.configure(bg=self.bg_color)
        popup.resizable(False, False)
        popup.transient(self.root)
        popup.grab_set()
        self.language_window = popup

        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 165
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 215
        popup.geometry(f"+{x}+{y}")

        popup.protocol("WM_DELETE_WINDOW", self.close_language_window)

        container = tk.Frame(popup, bg=self.bg_color, relief="solid", bd=1, padx=14, pady=14)
        container.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        tk.Label(container, text=self.t("language_select"), bg=self.bg_color, font=("Arial", 11, "bold"), anchor="w").pack(fill=tk.X, pady=(0, 8))
        tk.Label(
            container,
            text=self.t("language_current", language=self.get_language_name()),
            bg=self.bg_color,
            font=("Arial", 10),
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 8))

        list_frame = tk.Frame(container, bg=self.bg_color)
        list_frame.pack(fill=tk.BOTH, expand=True)

        scroll = tk.Scrollbar(list_frame, orient=tk.VERTICAL)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        language_list = tk.Listbox(list_frame, yscrollcommand=scroll.set, exportselection=False, font=("Arial", 10))
        language_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.configure(command=language_list.yview)

        current_index = 0
        for idx, (label, code) in enumerate(LANGUAGE_OPTIONS):
            language_list.insert(tk.END, label)
            if code == self.current_language:
                current_index = idx
        language_list.selection_set(current_index)
        language_list.activate(current_index)
        language_list.see(current_index)

        def apply_selected_language(_event=None):
            selected = language_list.curselection()
            if not selected:
                return
            _, code = LANGUAGE_OPTIONS[selected[0]]
            self.set_language(code)
            self.close_language_window()

        language_list.bind("<Double-Button-1>", apply_selected_language)
        language_list.bind("<Return>", apply_selected_language)

        buttons = tk.Frame(container, bg=self.bg_color)
        buttons.pack(fill=tk.X, pady=(10, 0))

        tk.Button(
            buttons,
            text=self.t("language_apply"),
            relief="solid",
            bd=1,
            command=apply_selected_language,
        ).pack(side=tk.LEFT, padx=(0, 8), ipadx=10)

        tk.Button(
            buttons,
            text=self.t("language_cancel"),
            relief="solid",
            bd=1,
            command=self.close_language_window,
        ).pack(side=tk.LEFT, ipadx=10)

    def close_language_window(self):
        if self.language_window and self.language_window.winfo_exists():
            try:
                self.language_window.grab_release()
            except tk.TclError:
                pass
            self.language_window.destroy()
        self.language_window = None

    def draw_standard_avatar(self, canvas, x, y, width, height):
        """Desenha um boneco padronizado (estilo perfil) do 1Âº cÃ³digo"""
        pad = 2
        w = width - 2*pad
        h = height - 2*pad
        x0 = x + pad
        y0 = y + pad
        
        cx = x0 + w // 2
        
        head_r = h * 0.20
        head_cy = y0 + (h * 0.3) 
        
        canvas.create_oval(cx - head_r, head_cy - head_r, 
                           cx + head_r, head_cy + head_r, 
                           fill=self.avatar_fill, outline=self.avatar_outline, width=2)
        
        body_w = w * 0.7  
        body_h = h * 0.45 
        
        arc_x0 = cx - body_w / 2
        arc_x1 = cx + body_w / 2
        arc_y0 = head_cy + head_r + (h * 0.05) 
        arc_y1 = arc_y0 + (body_h * 2) 
        
        canvas.create_arc(arc_x0, arc_y0, arc_x1, arc_y1, 
                          start=0, extent=180, style=tk.CHORD, 
                          fill=self.avatar_fill, outline=self.avatar_outline, width=2)

    # ==========================================
    # TELA 1: LOGIN
    # ==========================================
    def show_login_screen(self):
        self.dashboard_active = False
        self.session_panel_visible = False
        self.stop_live_data_receiver()
        self.radar_origin = None
        self.stop_alert_audio()
        if self.simulation_job:
            try:
                self.root.after_cancel(self.simulation_job)
            except tk.TclError:
                pass
            self.simulation_job = None

        self.clear_window()
        self.root.configure(bg=self.bg_color)

        self.root.update_idletasks()
        win_w = max(360, self.root.winfo_width())
        win_h = max(420, self.root.winfo_height())
        compact_mode = win_h < 560
        frame_w = min(420, max(330, int(win_w * 0.44)))
        frame_h = min(560, max(430, win_h - 30))
        keyboard_w = max(280, win_w - frame_w - 54)

        layout_frame = tk.Frame(self.root, bg=self.bg_color)
        layout_frame.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        login_frame = tk.Frame(layout_frame, bg=self.bg_color, relief="solid", bd=2, width=frame_w, height=frame_h)
        login_frame.pack(side=tk.LEFT, padx=(0, 12))
        login_frame.pack_propagate(False)

        keyboard_frame = tk.Frame(layout_frame, bg=self.bg_color, relief="solid", bd=2, width=keyboard_w, height=frame_h)
        keyboard_frame.pack(side=tk.LEFT)
        keyboard_frame.pack_propagate(False)

        avatar_size = 110 if compact_mode else 140
        avatar_top = 10 if compact_mode else 26
        avatar_bottom = 12 if compact_mode else 25
        avatar_canvas = tk.Canvas(login_frame, width=avatar_size, height=avatar_size, bg=self.bg_color, highlightthickness=0)
        avatar_canvas.pack(pady=(avatar_top, avatar_bottom))
        self.draw_standard_avatar(avatar_canvas, 5, 5, avatar_size - 10, avatar_size - 10)

        fields_container = tk.Frame(login_frame, bg=self.bg_color)
        fields_padx = 22 if compact_mode else 42
        fields_container.pack(fill=tk.X, padx=fields_padx)

        field_height = 42

        self.login_station_label = tk.Label(fields_container, text=self.t("fire_station"), bg=self.bg_color, anchor="w", font=("Arial", 10))
        self.login_station_label.pack(fill=tk.X, pady=(0, 4))
        station_wrapper = tk.Frame(fields_container, bg=self.bg_color, relief="solid", bd=1, height=field_height)
        station_wrapper.pack(fill=tk.X, pady=(0, 18))
        station_wrapper.pack_propagate(False)
        self.station_selector = AutocompleteEntry(
            station_wrapper,
            options=get_fire_station_names(),
            bg=self.bg_color,
        )
        self.station_selector.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        station_wrapper.bind("<Button-1>", lambda _e: self._focus_station_input())

        self.login_name_label = tk.Label(fields_container, text=self.t("name_id"), bg=self.bg_color, anchor="w", font=("Arial", 10))
        self.login_name_label.pack(fill=tk.X, pady=(0, 4))
        name_wrapper = tk.Frame(fields_container, bg=self.bg_color, relief="solid", bd=1, height=field_height)
        name_wrapper.pack(fill=tk.X, pady=(0, 18))
        name_wrapper.pack_propagate(False)
        self.leader_name_entry = tk.Entry(name_wrapper, relief="flat", bd=0, font=("Arial", 12), fg="black")
        self.leader_name_entry.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        name_wrapper.bind("<Button-1>", lambda _e: self._focus_leader_input())

        self.login_ops_label = tk.Label(fields_container, text=self.t("nr_operationals"), bg=self.bg_color, anchor="w", font=("Arial", 10))
        self.login_ops_label.pack(fill=tk.X, pady=(0, 4))
        ops_wrapper = tk.Frame(fields_container, bg=self.bg_color, relief="solid", bd=1, height=field_height)
        ops_bottom_gap = 10 if compact_mode else 22
        ops_wrapper.pack(fill=tk.X, pady=(0, ops_bottom_gap))
        ops_wrapper.pack_propagate(False)
        self.operationals_entry = tk.Entry(ops_wrapper, relief="flat", bd=0, font=("Arial", 12), fg="black")
        self.operationals_entry.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        ops_wrapper.bind("<Button-1>", lambda _e: self._focus_ops_input())
        self.operationals_entry.insert(0, "5")

        self.station_selector.bind("<<AutocompleteSelected>>", lambda _e: self._focus_leader_input())
        station_input = self.station_selector.get_input_widget()
        station_input.bind("<FocusIn>", lambda _e: self.set_active_input_widget(station_input), add="+")

        def on_leader_focus(_event=None):
            self.station_selector.hide_suggestions()
            self.set_active_input_widget(self.leader_name_entry)

        def on_ops_focus(_event=None):
            self.station_selector.hide_suggestions()
            self.set_active_input_widget(self.operationals_entry)

        self.leader_name_entry.bind("<FocusIn>", on_leader_focus)
        self.operationals_entry.bind("<FocusIn>", on_ops_focus)
        self.root.after(10, self._focus_station_input)

        self.login_button = tk.Button(
            login_frame,
            text=self.t("login"),
            bg=self.bg_color,
            relief="solid",
            bd=1,
            font=("Arial", 12),
            width=12,
            command=self.verify_login
        )
        self.login_button.pack(ipady=6, pady=(0, 12), side=tk.BOTTOM)

        self.build_login_keyboard(keyboard_frame, compact_mode)

    def _apply_placeholder(self, entry_widget, placeholder_text):
        entry_widget.delete(0, tk.END)
        entry_widget.insert(0, placeholder_text)
        entry_widget.config(fg="#555")

        def on_focus_in(_event):
            if entry_widget.get() == placeholder_text:
                entry_widget.delete(0, tk.END)
                entry_widget.config(fg="black")

        def on_focus_out(_event):
            if not entry_widget.get().strip():
                entry_widget.insert(0, placeholder_text)
                entry_widget.config(fg="#555")

        entry_widget.bind("<FocusIn>", on_focus_in)
        entry_widget.bind("<FocusOut>", on_focus_out)

    def set_active_input_widget(self, widget):
        self.active_input_widget = widget

    def build_login_keyboard(self, parent, compact_mode, key_handler=None, title_text="Keyboard"):
        if key_handler is None:
            key_handler = self.handle_virtual_key_press

        title = tk.Label(parent, text=title_text, bg=self.bg_color, font=("Arial", 11, "bold"), anchor="w")
        title.pack(fill=tk.X, padx=10, pady=(10, 4))

        keyboard = tk.Frame(parent, bg=self.bg_color)
        keyboard.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        rows = [
            ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "Back"],
            ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P"],
            ["A", "S", "D", "F", "G", "H", "J", "K", "L"],
            ["Z", "X", "C", "V", "B", "N", "M", "-", "_"],
            ["Space", "Clear", "Enter"],
        ]

        btn_font = ("Arial", 11 if compact_mode else 12, "bold")
        btn_height = 1 if compact_mode else 2

        for row in rows:
            row_frame = tk.Frame(keyboard, bg=self.bg_color)
            row_frame.pack(fill=tk.X, pady=3)

            col_index = 0
            for key in row:
                col_span = 1
                if key == "Space":
                    col_span = 4
                elif key in ("Back", "Clear", "Enter"):
                    col_span = 2

                btn = tk.Button(
                    row_frame,
                    text=key,
                    font=btn_font,
                    height=btn_height,
                    relief="solid",
                    bd=1,
                    command=lambda k=key: key_handler(k),
                )
                btn.grid(row=0, column=col_index, columnspan=col_span, sticky="nsew", padx=2)
                for expand_col in range(col_index, col_index + col_span):
                    row_frame.grid_columnconfigure(expand_col, weight=1)
                col_index += col_span

    def handle_virtual_key_press(self, key):
        target = self.active_input_widget

        if not target or not target.winfo_exists():
            self._focus_station_input()
            target = self.active_input_widget

        if not target:
            return

        target.focus_set()

        station_input = self.station_selector.get_input_widget() if hasattr(self, "station_selector") else None

        if key == "Enter":
            if target == station_input:
                self._focus_leader_input()
            elif target == self.leader_name_entry:
                self._focus_ops_input()
            else:
                self.verify_login()
            return

        if key == "Back":
            text_now = target.get()
            if not text_now:
                return
            try:
                cursor = target.index(tk.INSERT)
            except tk.TclError:
                cursor = len(text_now)
            if cursor <= 0:
                return
            target.delete(cursor - 1, cursor)
            if target == station_input:
                self.station_selector.on_virtual_input()
            return

        if key == "Clear":
            target.delete(0, tk.END)
            if target == station_input:
                self.station_selector.on_virtual_input()
            return

        char = " " if key == "Space" else key

        if target == self.operationals_entry and not char.isdigit():
            return

        try:
            target.insert(tk.INSERT, char)
        except tk.TclError:
            target.insert(tk.END, char)
        if target == station_input:
            self.station_selector.on_virtual_input()

    def _focus_station_input(self):
        if hasattr(self, "station_selector"):
            self.station_selector.focus_input()
            self.set_active_input_widget(self.station_selector.get_input_widget())

    def _focus_leader_input(self):
        if hasattr(self, "station_selector"):
            self.station_selector.hide_suggestions()
        if hasattr(self, "leader_name_entry"):
            self.leader_name_entry.focus_set()
            self.leader_name_entry.icursor(tk.END)
            self.set_active_input_widget(self.leader_name_entry)

    def _focus_ops_input(self):
        if hasattr(self, "station_selector"):
            self.station_selector.hide_suggestions()
        if hasattr(self, "operationals_entry"):
            self.operationals_entry.focus_set()
            self.operationals_entry.icursor(tk.END)
            self.set_active_input_widget(self.operationals_entry)

    def _focus_popup_entry(self, entry_widget):
        try:
            entry_widget.focus_set()
            entry_widget.icursor(tk.END)
        except tk.TclError:
            return

    def verify_login(self):
        fire_station = self.station_selector.get()
        leader_name = self.leader_name_entry.get().strip()
        operations_text = self.operationals_entry.get().strip()

        if not fire_station:
            messagebox.showwarning(self.t("warning_title"), self.t("warn_select_station"))
            return

        if not leader_name:
            messagebox.showwarning(self.t("warning_title"), self.t("warn_enter_leader_name"))
            return

        if not operations_text:
            max_operationals = 5
        else:
            if not operations_text.isdigit():
                messagebox.showwarning(self.t("warning_title"), self.t("warn_ops_integer"))
                return
            max_operationals = int(operations_text)
            if max_operationals < 1 or max_operationals > 12:
                messagebox.showwarning(self.t("warning_title"), self.t("warn_ops_between"))
                return

        if max_operationals <= 0:
            messagebox.showwarning(self.t("warning_title"), self.t("warn_ops_at_least_one"))
            return
            
        self.fire_station_name = fire_station
        self.team_leader_name = leader_name
        self.max_operationals = max_operationals
        self.controller.set_logged_in_leader(self.team_leader_name)
        self.controller.start_new_session(leader_name=self.team_leader_name)
        self.show_dashboard_wireframe()
        
        self.add_leader_to_team(self.team_leader_name)

    # ==========================================
    # TELA 2: DASHBOARD
    # ==========================================
    def show_dashboard_wireframe(self):
        self.clear_window()
        self.root.configure(bg=self.bg_color)
        self.dashboard_active = True
        self.session_panel_visible = False
        self.radar_fullscreen = False

        header_frame = tk.Frame(self.root, bg=self.header_color, height=60)
        self.header_frame = header_frame
        header_frame.pack(fill=tk.X, side=tk.TOP)
        
        separator = tk.Frame(self.root, bg="black", height=2)
        self.header_separator = separator
        separator.pack(fill=tk.X, side=tk.TOP)

        self.lbl_menu = tk.Label(header_frame, text="\u2630", font=("Arial", 24), bg=self.header_color, cursor="hand2")
        self.lbl_menu.pack(side=tk.LEFT, padx=15)
        self.lbl_menu.bind("<Button-1>", self.toggle_menu)
        
        tk.Frame(header_frame, bg="black", width=2, height=40).pack(side=tk.LEFT, padx=5, pady=10)

        tk.Label(header_frame, text="ResQSense", font=("Arial", 16), bg=self.header_color).pack(side=tk.LEFT, padx=10)
        tk.Label(header_frame, text="Wearion", font=("Arial", 10), bg=self.header_color, fg="#555").pack(side=tk.LEFT, pady=(5,0))

        profile_canvas = tk.Canvas(header_frame, width=50, height=50, bg=self.header_color, highlightthickness=0)
        profile_canvas.pack(side=tk.RIGHT, padx=15)
        self.draw_standard_avatar(profile_canvas, 0, 0, 50, 50)
        profile_canvas.bind("<Button-1>", lambda _e: self.toggle_session_panel())

        self.time_label = tk.Label(header_frame, text="00:00", font=("Arial", 14), bg=self.header_color)
        self.time_label.pack(side=tk.RIGHT, padx=20)
        self.update_clock()

        self.radar_fullscreen_button = tk.Button(
            header_frame,
            text="Radar full",
            font=("Arial", 11, "bold"),
            bg="#f4f4f4",
            activebackground="#e5e5e5",
            relief="solid",
            bd=1,
            cursor="hand2",
            command=self.enter_radar_fullscreen,
        )
        self.radar_fullscreen_button.pack(side=tk.RIGHT, padx=(0, 8), ipadx=8, ipady=4)

        main_container = tk.Frame(self.root, bg=self.bg_color)
        self.main_container = main_container
        main_container.pack(fill=tk.BOTH, expand=True)

        self.sidebar = tk.Frame(main_container, bg=self.sidebar_color, width=245)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar.pack_propagate(False)

        tk.Frame(self.sidebar, bg="black", width=4).pack(side=tk.RIGHT, fill=tk.Y)

        self.content_sidebar = tk.Frame(self.sidebar, bg=self.sidebar_color)
        self.content_sidebar.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.content_sidebar.grid_columnconfigure(0, weight=1)
        self.content_sidebar.grid_rowconfigure(1, weight=1)

        self.sidebar_title_label = tk.Label(self.content_sidebar, text=self.t("operational"), font=("Arial", 14), bg=self.sidebar_color, anchor="w")
        self.sidebar_title_label.grid(row=0, column=0, sticky="ew", pady=(10, 5))

        self.firefighter_scroll_container = tk.Frame(self.content_sidebar, bg=self.sidebar_color)
        self.firefighter_scroll_container.grid(row=1, column=0, sticky="nsew")

        self.firefighter_list_canvas = tk.Canvas(
            self.firefighter_scroll_container,
            bg=self.sidebar_color,
            highlightthickness=0,
            bd=0,
        )
        self.firefighter_list_scrollbar = tk.Scrollbar(
            self.firefighter_scroll_container,
            orient=tk.VERTICAL,
            command=self.firefighter_list_canvas.yview,
            width=18,
        )
        self.firefighter_list_canvas.configure(yscrollcommand=self.firefighter_list_scrollbar.set)

        self.firefighter_list_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.firefighter_list_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.firefighter_list_frame = tk.Frame(self.firefighter_list_canvas, bg=self.sidebar_color)
        self.firefighter_list_window = self.firefighter_list_canvas.create_window((0, 0), window=self.firefighter_list_frame, anchor="nw")

        self.firefighter_list_frame.bind("<Configure>", self._update_firefighter_scrollregion)
        self.firefighter_list_canvas.bind("<Configure>", self._resize_firefighter_list_frame)
        for widget in (self.firefighter_list_canvas, self.firefighter_list_frame, self.firefighter_scroll_container):
            widget.bind("<MouseWheel>", self._on_firefighter_mousewheel, add="+")
            widget.bind("<Button-4>", self._on_firefighter_mousewheel, add="+")
            widget.bind("<Button-5>", self._on_firefighter_mousewheel, add="+")
            self._bind_firefighter_touch_events(widget)

        action_frame = tk.Frame(self.content_sidebar, bg=self.sidebar_color)
        action_frame.grid(row=2, column=0, sticky="ew", pady=(12, 6))

        self.add_operational_button = tk.Button(
            action_frame,
            text=f"+ {self.t('add_operational_title')}",
            font=("Arial", 15, "bold"),
            bg="#f4f4f4",
            activebackground="#e5e5e5",
            relief="solid",
            borderwidth=3,
            wraplength=220,
            justify="center",
            cursor="hand2",
            command=self.open_add_firefighter_window,
        )
        self.add_operational_button.pack(fill=tk.X, ipady=14)

        map_area = tk.Frame(main_container, bg=self.bg_color)
        self.map_area = map_area
        map_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(map_area, bg=self.bg_color, highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", self.draw_radar)

        footer = tk.Frame(self.root, bg="black", height=5)
        self.footer = footer
        footer.pack(fill=tk.X, side=tk.BOTTOM)

        self.menu_frame = tk.Frame(self.root, bg="#f0f0f0", relief="solid", bd=1)
        self.menu_edit_button = tk.Button(self.menu_frame, text=self.t("menu_edit"), font=("Arial", 12), anchor="w", bg="#f0f0f0", bd=0, padx=10, command=self.toggle_edit_mode)
        self.menu_edit_button.pack(fill=tk.X, pady=2)
        self.menu_resources_button = tk.Button(
            self.menu_frame,
            text=self.t("menu_resources"),
            font=("Arial", 12),
            anchor="w",
            bg="#f0f0f0",
            bd=0,
            padx=10,
            command=self.toggle_session_panel,
        )
        self.menu_resources_button.pack(fill=tk.X, pady=2)
        self.menu_language_button = tk.Button(
            self.menu_frame,
            text=self.t("menu_language"),
            font=("Arial", 12),
            anchor="w",
            bg="#f0f0f0",
            bd=0,
            padx=10,
            command=self.open_language_window,
        )
        self.menu_language_button.pack(fill=tk.X, pady=2)

        self.session_info_panel = tk.Frame(self.root, bg=self.bg_color, relief="solid", bd=1)
        self.session_name_label = tk.Label(
            self.session_info_panel,
            text=self.t("session_name", name=self.team_leader_name),
            bg=self.bg_color,
            font=("Arial", 15),
            anchor="w",
        )
        self.session_name_label.pack(fill=tk.X, padx=10, pady=(10, 6))
        self.end_session_button = tk.Button(
            self.session_info_panel,
            text=self.t("end_session"),
            font=("Arial", 17),
            relief="solid",
            bd=1,
            command=self.end_current_session,
        )
        self.end_session_button.pack(padx=10, pady=(0, 10), anchor="w")
        self.session_info_panel.place_forget()

        self.root.bind("<Escape>", self.exit_radar_fullscreen, add="+")
        self.start_live_data_receiver()
        self.update_simulation_cycle()

    # ==========================================
    # LÃ“GICA DO MENU E EDIÃ‡ÃƒO
    # ==========================================
    def enter_radar_fullscreen(self):
        if self.radar_fullscreen:
            return
        if not self.dashboard_active or not self.canvas or not self.canvas.winfo_exists():
            return

        self.hide_session_panel()
        if self.menu_open:
            self.menu_frame.place_forget()
            self.menu_open = False

        self.previous_window_geometry = self.root.geometry()
        try:
            self.previous_window_fullscreen = bool(self.root.attributes("-fullscreen"))
            self.root.attributes("-fullscreen", True)
        except tk.TclError:
            self.previous_window_fullscreen = False

        for widget in (self.header_frame, self.header_separator, self.footer, self.sidebar):
            if widget and widget.winfo_exists():
                widget.pack_forget()

        if self.map_area and self.map_area.winfo_exists():
            self.map_area.pack_forget()
            self.map_area.pack(fill=tk.BOTH, expand=True)

        self.radar_restore_button = tk.Button(
            self.map_area,
            text="Voltar",
            font=("Arial", 12, "bold"),
            bg="#f4f4f4",
            activebackground="#e5e5e5",
            relief="solid",
            bd=2,
            cursor="hand2",
            command=self.exit_radar_fullscreen,
        )

        self.radar_fullscreen = True
        self.root.after_idle(self.position_radar_restore_button)
        self.root.after_idle(self.draw_radar)

    def position_radar_restore_button(self):
        if not self.radar_fullscreen:
            return
        if self.radar_restore_button and self.radar_restore_button.winfo_exists():
            self.radar_restore_button.place(relx=1.0, x=-12, y=12, anchor="ne")
            self.radar_restore_button.lift()

    def exit_radar_fullscreen(self, event=None):
        if not self.radar_fullscreen:
            return

        if self.radar_restore_button and self.radar_restore_button.winfo_exists():
            self.radar_restore_button.destroy()
        self.radar_restore_button = None

        try:
            self.root.attributes("-fullscreen", self.previous_window_fullscreen)
        except tk.TclError:
            pass
        if self.previous_window_geometry and not self.previous_window_fullscreen:
            self.root.geometry(self.previous_window_geometry)

        if self.main_container and self.main_container.winfo_exists():
            self.main_container.pack_forget()
        if self.header_frame and self.header_frame.winfo_exists():
            self.header_frame.pack(fill=tk.X, side=tk.TOP)
        if self.header_separator and self.header_separator.winfo_exists():
            self.header_separator.pack(fill=tk.X, side=tk.TOP)
        if self.main_container and self.main_container.winfo_exists():
            self.main_container.pack(fill=tk.BOTH, expand=True)
        if self.footer and self.footer.winfo_exists():
            self.footer.pack(fill=tk.X, side=tk.BOTTOM)
        if self.sidebar and self.sidebar.winfo_exists():
            self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        if self.map_area and self.map_area.winfo_exists():
            self.map_area.pack_forget()
            self.map_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.radar_fullscreen = False
        self.root.after_idle(self.draw_radar)
        return "break"

    def toggle_menu(self, event):
        if self.menu_open:
            self.menu_frame.place_forget()
            self.menu_open = False
        else:
            self.menu_frame.place(x=10, y=60, width=220, height=118)
            self.menu_frame.lift()
            self.menu_open = True

    def toggle_edit_mode(self):
        self.edit_mode = not self.edit_mode
        self.refresh_sidebar_list()
        self.menu_frame.place_forget()
        self.menu_open = False

    def toggle_session_panel(self):
        if not self.session_info_panel:
            return
        if self.session_panel_visible:
            self.hide_session_panel()
        else:
            self.show_session_panel()
        self.menu_frame.place_forget()
        self.menu_open = False

    def show_session_panel(self):
        if not self.session_info_panel:
            return
        if self.session_name_label:
            self.session_name_label.config(text=self.t("session_name", name=self.team_leader_name))

        self.root.update_idletasks()
        panel_width = 200
        panel_height = 95
        x = max(5, self.root.winfo_width() - panel_width - 5)
        y = 62
        self.session_info_panel.place(x=x, y=y, width=panel_width, height=panel_height)
        self.session_info_panel.lift()
        self.session_panel_visible = True

    def hide_session_panel(self):
        if self.session_info_panel:
            self.session_info_panel.place_forget()
        self.session_panel_visible = False

    def end_current_session(self):
        confirm = messagebox.askyesno(self.t("end_session_title"), self.t("end_session_confirm"))
        if not confirm:
            return

        self.hide_session_panel()
        self.dismiss_current_popup(queue_next=False)
        self.controller.end_session()

        # Sincronizar dados com o PostgreSQL do PC em background
        threading.Thread(target=sincronizar_para_postgresql, daemon=True).start()

        self.firefighters = []
        self.warning_history = []
        self.pending_man_down_alerts = []
        self.active_critical_signatures = set()
        self.selected_firefighter_id = None

        self.stop_alert_audio()
        self.show_login_screen()

    def _update_firefighter_scrollregion(self, _event=None):
        if hasattr(self, "firefighter_list_canvas"):
            self.firefighter_list_canvas.configure(scrollregion=self.firefighter_list_canvas.bbox("all"))

    def _resize_firefighter_list_frame(self, event):
        if hasattr(self, "firefighter_list_window"):
            self.firefighter_list_canvas.itemconfigure(self.firefighter_list_window, width=event.width)

    def _bind_firefighter_touch_events(self, widget, include_children=False):
        if not widget or not widget.winfo_exists():
            return
        widget.bind("<ButtonPress-1>", self._on_firefighter_touch_start, add="+")
        widget.bind("<B1-Motion>", self._on_firefighter_touch_drag, add="+")
        widget.bind("<ButtonRelease-1>", self._on_firefighter_touch_end, add="+")
        if include_children:
            for child in widget.winfo_children():
                self._bind_firefighter_touch_events(child, include_children=True)

    def _scroll_firefighter_list(self, units):
        if not hasattr(self, "firefighter_list_canvas"):
            return
        self.firefighter_list_canvas.yview_scroll(units, "units")

    def _on_firefighter_touch_start(self, event):
        if self.firefighter_touch_clear_job:
            try:
                self.root.after_cancel(self.firefighter_touch_clear_job)
            except tk.TclError:
                pass
            self.firefighter_touch_clear_job = None
        self.firefighter_touch_last_y = event.y_root
        self.firefighter_touch_dragging = False

    def _on_firefighter_touch_drag(self, event):
        if not hasattr(self, "firefighter_list_canvas"):
            return None
        if self.firefighter_touch_last_y is None:
            self.firefighter_touch_last_y = event.y_root
            return None

        delta_y = event.y_root - self.firefighter_touch_last_y
        if abs(delta_y) < 2:
            return None

        self.firefighter_touch_dragging = True
        units = int(-delta_y / 7)
        if units == 0:
            units = -1 if delta_y > 0 else 1

        self._scroll_firefighter_list(units)
        self.firefighter_touch_last_y = event.y_root
        return "break"

    def _on_firefighter_touch_end(self, _event=None):
        self.firefighter_touch_last_y = None
        self.firefighter_touch_clear_job = self.root.after(60, self._clear_firefighter_touch_drag_state)

    def _clear_firefighter_touch_drag_state(self):
        self.firefighter_touch_clear_job = None
        self.firefighter_touch_dragging = False

    def _on_firefighter_mousewheel(self, event):
        if not hasattr(self, "firefighter_list_canvas"):
            return
        # Linux/Raspberry uses Button-4/5 while Windows uses MouseWheel delta.
        if hasattr(event, "num") and event.num in (4, 5):
            step = -1 if event.num == 4 else 1
            self._scroll_firefighter_list(step)
            return "break"

        delta = getattr(event, "delta", 0)
        if delta == 0:
            return None

        step = int(-1 * (delta / 120))
        if step == 0:
            step = -1 if delta > 0 else 1
        self._scroll_firefighter_list(step)
        return "break"

    def _on_firefighter_card_release(self, firefighter_id):
        if self.firefighter_touch_dragging:
            return
        self.set_selected_firefighter(firefighter_id)

    def set_selected_firefighter(self, firefighter_id):
        self.selected_firefighter_id = firefighter_id
        self.refresh_sidebar_list()
        self.draw_radar()

    def remove_firefighter(self, ff_to_remove):
        if ff_to_remove.get('is_leader', False): return 

        if ff_to_remove in self.firefighters:
            self.request_unsync_for_firefighter(ff_to_remove)
            if self.selected_firefighter_id == ff_to_remove["id"]:
                self.selected_firefighter_id = "C"
            self.firefighters.remove(ff_to_remove)
            self.refresh_sidebar_list()
            self.draw_radar()

    # ==========================================
    # LÃ“GICA DE SIMULAÃ‡ÃƒO
    # ==========================================
    def update_simulation_cycle(self):
        if not self.dashboard_active:
            self.stop_alert_audio()
            self.simulation_job = None
            return

        self.blink_state = not self.blink_state
        if self.live_data_enabled:
            self.drain_incoming_packets()

        alerta_ativo = False
        cycle_critical_signatures = set()
        now_ts = time.time()
        if self.firefighters:
            for ff in self.firefighters:
                is_leader = ff.get("is_leader", False)
                op_logic = ff['logic_ref']
                if self.live_data_enabled:
                    if is_leader and not self.firefighter_has_numeric_vest(ff):
                        ff["status"] = "leader"
                        ff["active_warnings"] = []
                        ff["signal_lost"] = False
                    else:
                        last_seen = ff.get("last_seen")
                        signal_lost = last_seen is None or (now_ts - last_seen) > self.signal_loss_timeout_sec
                        ff["signal_lost"] = signal_lost
                        if signal_lost:
                            ff["status"] = "no_signal"
                            ff["active_warnings"] = ["POOR CONNECTION"]
                        else:
                            warnings = list(ff.get("active_warnings", []))
                            is_critical, health_warnings = op_logic.check_health_status()
                            if is_critical:
                                for warning in health_warnings:
                                    if warning not in warnings:
                                        warnings.append(warning)
                            if warnings:
                                ff["status"] = "no_signal" if warnings == ["POOR CONNECTION"] else "critical"
                                ff["active_warnings"] = warnings
                            else:
                                ff["status"] = "leader" if is_leader else "normal"
                                ff["active_warnings"] = []
                else:
                    if is_leader:
                        ff["signal_lost"] = False
                        data = HardwareSimulator.get_fake_data()
                        op_logic.update_detail(data)

                        is_critical, warnings = op_logic.check_health_status()
                        if is_critical:
                            ff["status"] = "critical"
                            ff["active_warnings"] = warnings
                        else:
                            ff["status"] = "leader"
                            ff["active_warnings"] = []
                        ff["angle"] = 0.0
                        ff["dist"] = 0.0
                        ff["distance_m"] = 0.0
                    else:
                        was_signal_lost = ff.get("signal_lost", False)
                        if was_signal_lost:
                            signal_lost = random.random() < 0.65
                        else:
                            signal_lost = random.random() < 0.08
                        ff["signal_lost"] = signal_lost

                        if signal_lost:
                            ff["status"] = "no_signal"
                            ff["active_warnings"] = ["POOR CONNECTION"]
                        else:
                            data = HardwareSimulator.get_fake_data()
                            op_logic.update_detail(data)

                            is_critical, warnings = op_logic.check_health_status()
                            if is_critical:
                                ff["status"] = "critical"
                                ff["active_warnings"] = warnings
                            else:
                                ff["status"] = "normal"
                                ff["active_warnings"] = []

                        ff['angle'] += random.uniform(-0.1, 0.1)
                        ff['dist'] = max(0.1, min(0.9, ff['dist'] + random.uniform(-0.02, 0.02)))
                        ff["distance_m"] = self.normalized_distance_to_meters(ff.get("dist", 0.0))

                should_persist_snapshot = not (
                    self.live_data_enabled and is_leader and not self.leader_vest_id
                )
                if should_persist_snapshot and not ff.get("signal_lost", False):
                    distance_m = self.get_firefighter_persist_distance_m(ff)
                    self.controller.persist_operator_snapshot(op_logic, distance_m=distance_m)

                warnings = ff.get("active_warnings", [])
                if ff["status"] not in ("critical", "no_signal") or not warnings:
                    continue

                alerta_ativo = True
                critical_signature = (ff['id'], tuple(sorted(warnings)))
                cycle_critical_signatures.add(critical_signature)
                if critical_signature not in self.active_critical_signatures:
                    self.register_warning_event(ff, warnings)
                    if ff["status"] == "critical":
                        self.queue_man_down_alert(ff, warnings)
                        # Clear the sticky SOS flag once the alert is queued
                        if "SOS BUTTON" in warnings:
                            ff["sos_button_active"] = False
                    self.trigger_emergency_sound()

            self.refresh_sidebar_list()
            self.draw_radar()

        self.set_continuous_alarm_sound(alerta_ativo)
        self.active_critical_signatures = cycle_critical_signatures
        self.show_next_man_down_popup()
        refresh_ms = 1000 if self.live_data_enabled else 2000
        self.simulation_job = self.root.after(refresh_ms, self.update_simulation_cycle)

    # ==========================================
    # GESTÃƒO DA EQUIPA E POPUP
    # ==========================================
    def add_leader_to_team(self, name):
        leader_vest_id = self.leader_vest_id if self.leader_vest_id else "0"
        leader_logic = Operator(
            id=int(leader_vest_id) if str(leader_vest_id).isdigit() else 0,
            name=name,
            vest_id=leader_vest_id,
        )
        leader_dict = {
            "id": "C",
            "name": name,
            "vest_id": leader_vest_id,
            "angle": 0.0,
            "dist": 0.0,
            "logic_ref": leader_logic,
            "is_leader": True,
            "status": "leader",
            "active_warnings": [],
            "signal_lost": False,
            "last_seen": time.time(),
            "gps_lat": None,
            "gps_lon": None,
            "distance_m": 0.0,
        }
        self.firefighters.append(leader_dict)
        self.selected_firefighter_id = "C"
        self.refresh_sidebar_list()
        self.draw_radar()
        self.request_sync_for_firefighter(leader_dict)

    def open_add_firefighter_window(self):
        # Passo 1: Conta os operacionais (excluindo o lÃ­der) para nÃ£o passar do limite definido no login
        current_ops = [f for f in self.firefighters if not f.get('is_leader', False)]
        if len(current_ops) >= self.max_operationals:
            messagebox.showwarning(
                self.t("warning_title"),
                self.t("warn_max_ops", max_ops=self.max_operationals),
            )
            return

        popup = tk.Toplevel(self.root)
        popup.title(self.t("add_operational_title"))
        popup.configure(bg=self.bg_color)
        popup.transient(self.root)
        popup.grab_set()
        popup.resizable(False, False)

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        popup_w = min(760, max(620, screen_w - 20))
        popup_h = min(460, max(360, screen_h - 80))
        x = max(0, (screen_w - popup_w) // 2)
        y = max(0, (screen_h - popup_h) // 2)
        popup.geometry(f"{popup_w}x{popup_h}+{x}+{y}")

        content = tk.Frame(popup, bg=self.bg_color)
        content.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        form_w = max(280, int(popup_w * 0.42))
        form_panel = tk.Frame(content, bg=self.bg_color, relief="solid", bd=1, width=form_w)
        form_panel.pack(side=tk.LEFT, fill=tk.BOTH, padx=(0, 10))
        form_panel.pack_propagate(False)

        keyboard_panel = tk.Frame(content, bg=self.bg_color, relief="solid", bd=1)
        keyboard_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        keyboard_panel.pack_propagate(False)

        form_body = tk.Frame(form_panel, bg=self.bg_color)
        form_body.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

        icon_canvas = tk.Canvas(form_body, width=85, height=85, bg=self.bg_color, highlightthickness=0)
        icon_canvas.pack(pady=(2, 10))
        self.draw_standard_avatar(icon_canvas, 0, 0, 85, 85)

        tk.Label(form_body, text=self.t("fire_station"), bg=self.bg_color, anchor="w", font=("Arial", 10)).pack(fill=tk.X)
        entry_station = AutocompleteEntry(
            form_body,
            options=get_fire_station_names(),
            bg=self.bg_color,
        )
        entry_station.pack(fill=tk.X, pady=(0, 12))
        if self.fire_station_name:
            entry_station.set(self.fire_station_name)

        tk.Label(form_body, text=self.t("name_id"), bg=self.bg_color, anchor="w", font=("Arial", 10)).pack(fill=tk.X)
        entry_name = tk.Entry(form_body, relief="solid", bd=1, font=("Arial", 12))
        entry_name.pack(fill=tk.X, pady=(0, 12), ipady=4)

        def confirm_add():
            f_station = entry_station.get().strip()
            f_name = entry_name.get().strip()
            
            if f_station and f_name:
                # Passo 2: GeraÃ§Ã£o AutomÃ¡tica do ID. Se jÃ¡ temos 2 operacionais, este serÃ¡ o 3.
                ops_count = len([f for f in self.firefighters if not f.get('is_leader', False)])
                auto_id = str(ops_count + 1)
                
                self.add_firefighter(auto_id, f_name)
                popup.destroy()
            else:
                messagebox.showwarning(self.t("warning_title"), self.t("warn_fill_all"))

        btn_add = tk.Button(
            form_body,
            text=self.t("add_firefighter"),
            bg="#d0d0d0",
            relief="solid",
            bd=1,
            command=confirm_add,
        )
        btn_add.pack(fill=tk.X, ipady=6, pady=(4, 0))

        popup_input_ref = {"widget": None}
        station_input = entry_station.get_input_widget()

        def set_popup_active_input(widget):
            popup_input_ref["widget"] = widget

        def focus_add_station_input(_event=None):
            entry_station.focus_input()
            set_popup_active_input(station_input)

        def focus_add_name_input(_event=None):
            entry_station.hide_suggestions()
            popup.after_idle(lambda: self._focus_popup_entry(entry_name))
            set_popup_active_input(entry_name)

        def on_popup_name_focus(_event=None):
            entry_station.hide_suggestions()
            set_popup_active_input(entry_name)

        entry_station.bind("<<AutocompleteSelected>>", focus_add_name_input)
        station_input.bind("<FocusIn>", lambda _e: set_popup_active_input(station_input), add="+")
        entry_name.bind("<FocusIn>", on_popup_name_focus)
        entry_name.bind("<Button-1>", focus_add_name_input)
        popup.bind("<Escape>", lambda _e: entry_station.hide_suggestions())

        def handle_popup_virtual_key(key):
            target = popup_input_ref["widget"]
            if not target or not target.winfo_exists():
                focus_add_station_input()
                target = popup_input_ref["widget"]

            if not target:
                return

            target.focus_set()

            if key == "Enter":
                if target == station_input:
                    focus_add_name_input()
                else:
                    confirm_add()
                return

            if key == "Back":
                text_now = target.get()
                if not text_now:
                    return
                try:
                    cursor = target.index(tk.INSERT)
                except tk.TclError:
                    cursor = len(text_now)
                if cursor <= 0:
                    return
                target.delete(cursor - 1, cursor)
                if target == station_input:
                    entry_station.on_virtual_input()
                return

            if key == "Clear":
                target.delete(0, tk.END)
                if target == station_input:
                    entry_station.on_virtual_input()
                return

            char = " " if key == "Space" else key
            try:
                target.insert(tk.INSERT, char)
            except tk.TclError:
                target.insert(tk.END, char)
            if target == station_input:
                entry_station.on_virtual_input()

        compact_keyboard = popup_h < 420
        self.build_login_keyboard(
            keyboard_panel,
            compact_mode=compact_keyboard,
            key_handler=handle_popup_virtual_key,
            title_text="Keyboard",
        )
        popup.after(40, focus_add_station_input)

    def add_firefighter(self, f_number, f_name):
        new_op_logic = Operator(id=int(f_number) if f_number.isdigit() else 0, name=f_name, vest_id=f_number)
        
        if self.controller.current_session:
            self.controller.current_session.add_firefighter_to_team(new_op_logic)

        angle = random.uniform(0, 2 * math.pi)
        distance = random.uniform(0.2, 0.8) 
        
        new_ff = {
            "id": f_number,
            "name": f_name,
            "vest_id": f_number,
            "angle": angle,
            "dist": distance,
            "logic_ref": new_op_logic,
            "is_leader": False,
            "status": "normal",
            "active_warnings": [],
            "signal_lost": False,
            "last_seen": None,
            "gps_lat": None,
            "gps_lon": None,
            "distance_m": None,
        }
        self.firefighters.append(new_ff)
        if self.selected_firefighter_id in (None, "C"):
            self.selected_firefighter_id = f_number
        self.refresh_sidebar_list()
        self.draw_radar()
        self.request_sync_for_firefighter(new_ff)

    def humanize_warning_reason(self, warning_code):
        warning_map = {
            "HR HIGH": self.t("reason_hr_high"),
            "SpO2 LOW": self.t("reason_spo2_low"),
            "TEMP HIGH": self.t("reason_temp_high"),
            "POOR CONNECTION": self.t("reason_poor_connection"),
            "FALL": self.t("reason_fall"),
            "SOS BUTTON": "SOS botão premido",
        }
        return warning_map.get(warning_code, warning_code)

    def format_warning_reason(self, warnings):
        if not warnings:
            return self.t("reason_critical_generic")
        return " | ".join(self.humanize_warning_reason(warning) for warning in warnings)

    def register_warning_event(self, firefighter, warnings):
        timestamp = datetime.datetime.now().strftime("%H:%M")
        if not warnings:
            warnings = [self.t("reason_critical_generic")]

        for warning in warnings:
            reason = self.humanize_warning_reason(warning)
            entry = {
                "time": timestamp,
                "name": firefighter["name"],
                "id": firefighter["id"],
                "reason": reason,
            }

            if self.warning_history:
                latest = self.warning_history[-1]
                if latest["id"] == entry["id"] and latest["reason"] == entry["reason"] and latest["time"] == entry["time"]:
                    continue

            self.warning_history.append(entry)
            op_logic = firefighter.get("logic_ref")
            if op_logic is not None:
                self.controller.record_operator_alert(op_logic, reason)

        if len(self.warning_history) > 50:
            self.warning_history = self.warning_history[-50:]

    def queue_man_down_alert(self, firefighter, warnings):
        alert_data = {
            "name": firefighter["name"],
            "id": firefighter["id"],
            "reason": self.format_warning_reason(warnings),
        }
        new_signature = (alert_data["id"], alert_data["reason"])

        if self.current_popup_data:
            current_signature = (self.current_popup_data["id"], self.current_popup_data["reason"])
            if new_signature == current_signature:
                return

        queued_signatures = {(item["id"], item["reason"]) for item in self.pending_man_down_alerts}
        if new_signature in queued_signatures:
            return

        self.pending_man_down_alerts.append(alert_data)

    def show_next_man_down_popup(self):
        if self.current_alert_popup and self.current_alert_popup.winfo_exists():
            return
        if not self.pending_man_down_alerts:
            return

        alert_data = self.pending_man_down_alerts.pop(0)
        self.current_popup_data = alert_data

        popup = tk.Toplevel(self.root)
        popup.title("Critical Alert")
        popup.geometry("380x420")
        popup.configure(bg=self.alert_color, bd=2, relief="solid")
        popup.resizable(False, False)
        popup.transient(self.root)
        popup.grab_set()
        popup.attributes("-topmost", True)
        popup.protocol("WM_DELETE_WINDOW", self.dismiss_current_popup)

        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 190
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 210
        popup.geometry(f"+{x}+{y}")

        icon_canvas = tk.Canvas(popup, width=180, height=130, bg=self.alert_color, highlightthickness=0)
        icon_canvas.pack(pady=(25, 10))
        icon_canvas.create_polygon(90, 10, 25, 120, 155, 120, fill=self.alert_color, outline="black", width=2)
        icon_canvas.create_line(90, 42, 90, 86, fill="black", width=3)
        icon_canvas.create_oval(83, 95, 97, 109, fill="black", outline="black")

        tk.Label(popup, text="Alert", font=("Arial", 22, "bold"), bg=self.alert_color, fg="white").pack(pady=(0, 15))
        tk.Label(popup, text=f"{alert_data['name']} is in danger", font=("Arial", 20, "bold"), bg=self.alert_color, fg="white").pack()
        tk.Label(
            popup,
            text=f"Reason: {alert_data['reason']}",
            font=("Arial", 13, "bold"),
            bg=self.alert_color,
            fg="white",
            wraplength=320,
            justify="center",
        ).pack(pady=(12, 26))

        tk.Button(
            popup,
            text="View More",
            font=("Arial", 11),
            relief="solid",
            bd=2,
            width=20,
            command=self.show_current_alert_details,
        ).pack()

        self.current_alert_popup = popup

    def show_current_alert_details(self):
        if not self.current_popup_data:
            return

        alert_data = self.current_popup_data
        self.set_selected_firefighter(str(alert_data["id"]))
        self.dismiss_current_popup(queue_next=False)

    def dismiss_current_popup(self, queue_next=True):
        if self.current_alert_popup and self.current_alert_popup.winfo_exists():
            try:
                self.current_alert_popup.grab_release()
            except tk.TclError:
                pass
            self.current_alert_popup.destroy()
        self.current_alert_popup = None
        self.current_popup_data = None
        if queue_next:
            self.root.after(100, self.show_next_man_down_popup)

    def get_health_badge(self, status):
        if status == "critical":
            return "bad", "white"
        if status == "no_signal":
            return "ok", "#ffd166"
        return "good", "#1b7f3a"

    def normalized_distance_to_meters(self, normalized_distance):
        return max(0.0, normalized_distance) * self.radar_max_distance_m

    def get_firefighter_distance_m(self, firefighter):
        distance_m = firefighter.get("distance_m")
        if distance_m is not None:
            return max(0.0, distance_m)
        return self.normalized_distance_to_meters(firefighter.get("dist", 0.0))

    def get_firefighter_persist_distance_m(self, firefighter):
        distance_m = firefighter.get("distance_m")
        if distance_m is not None:
            return max(0.0, distance_m)
        if self.live_data_enabled:
            return None
        return self.normalized_distance_to_meters(firefighter.get("dist", 0.0))

    def build_firefighter_details_text(self, firefighter):
        op_logic = firefighter["logic_ref"]
        bio = op_logic.biometrics
        location = op_logic.location
        return (
            f"{bio.heart_rate} bpm\n"
            f"{bio.oxygenation} %\n"
            f"{bio.temperature:.1f} \N{DEGREE SIGN}C\n"
            f"{self.t('altitude')}: {location.height:.1f} m\n"
            f"{self.t('distance')}: {self.get_firefighter_distance_m(firefighter):.0f} m\n"
            f"{self.t('coordinates')}: {location.latitude:.4f}, {location.longitude:.4f}"
        )

    # ==========================================
    # ATUALIZAR SIDEBAR
    # ==========================================
    def restore_firefighter_scroll_position(self, position):
        if not hasattr(self, "firefighter_list_canvas"):
            return
        try:
            if self.firefighter_list_canvas.winfo_exists():
                self.firefighter_list_canvas.yview_moveto(position)
        except tk.TclError:
            pass

    def refresh_sidebar_list(self):
        scroll_position = 0.0
        if hasattr(self, "firefighter_list_canvas") and self.firefighter_list_canvas.winfo_exists():
            try:
                scroll_position = self.firefighter_list_canvas.yview()[0]
            except tk.TclError:
                scroll_position = 0.0

        for widget in self.firefighter_list_frame.winfo_children():
            widget.destroy()
            
        for ff in self.firefighters:
            op_logic = ff['logic_ref']
            is_leader = ff.get('is_leader', False)
            status = ff.get("status", "leader" if is_leader else "normal")
            warnings = ff.get("active_warnings", [])
            is_selected = self.selected_firefighter_id == ff["id"]

            card_bg = self.bg_color
            text_fg = "black"
            if status == "critical":
                card_bg = "#f44336"
            elif status == "no_signal":
                card_bg = "#111111"
                text_fg = "white"

            border_thick = 2 if is_selected else 1
            card = tk.Frame(self.firefighter_list_frame, bg=card_bg, relief="solid", bd=border_thick, pady=5, padx=5)
            card.pack(fill=tk.X, pady=5)

            header = tk.Frame(card, bg=card_bg)
            header.pack(fill=tk.X)

            icon_canvas = tk.Canvas(header, width=30, height=30, bg=card_bg, highlightthickness=0)
            icon_canvas.pack(side=tk.LEFT, padx=(0, 5))
            self.draw_standard_avatar(icon_canvas, 0, 0, 30, 30)

            # Badge com o numero do operacional (igual ao radar), sobre o avatar
            badge_bg = "black" if status == "no_signal" else "white"
            badge_fg = "white" if status == "no_signal" else "black"
            icon_canvas.create_oval(15, 15, 29, 29, fill=badge_bg, outline="black", width=1)
            icon_canvas.create_text(22, 22, text=str(ff["id"]), font=("Arial", 8, "bold"), fill=badge_fg)

            health_text, health_fg = self.get_health_badge(status)
            status_label = tk.Label(header, text=health_text, font=("Arial", 9, "bold"), bg=card_bg, fg=health_fg)
            status_label.pack(side=tk.LEFT, padx=(2, 5))

            name_label = tk.Label(header, text=ff["name"], font=("Arial", 11, "bold"), bg=card_bg, fg=text_fg)
            name_label.pack(side=tk.LEFT, padx=5)

            for clickable in (card, header, icon_canvas, status_label, name_label):
                clickable.bind("<ButtonRelease-1>", lambda _e, ff_id=ff["id"]: self._on_firefighter_card_release(ff_id))

            if self.edit_mode and not is_leader:
                btn_del = tk.Button(
                    header,
                    text="-",
                    font=("Arial", 10, "bold"),
                    bg="white",
                    fg="red",
                    relief="solid",
                    bd=1,
                    width=2,
                    cursor="hand2",
                    command=lambda f=ff: self.remove_firefighter(f),
                )
                btn_del.pack(side=tk.RIGHT, padx=5)

            if is_leader:
                tk.Label(card, text=self.t("captain"), font=("Arial", 8, "bold"), bg=card_bg, fg=text_fg).pack(anchor="w", pady=(4, 0))

            if not is_selected:
                self._bind_firefighter_touch_events(card, include_children=True)
                continue

            details_text = self.build_firefighter_details_text(ff)

            details_fg = text_fg if status != "critical" else "black"
            tk.Label(
                card,
                text=details_text,
                justify="left",
                anchor="w",
                font=("Arial", 9),
                bg=card_bg,
                fg=details_fg,
            ).pack(fill=tk.X, padx=2, pady=(3, 0))

            if warnings:
                warn_text = " | ".join(self.humanize_warning_reason(warning) for warning in warnings)
                warn_fg = "white" if status == "no_signal" else "black"
                tk.Label(
                    card,
                    text=f"\N{WARNING SIGN} {warn_text}",
                    font=("Arial", 8, "bold"),
                    bg=card_bg,
                    fg=warn_fg,
                ).pack(anchor="w", pady=(2, 0))

            self._bind_firefighter_touch_events(card, include_children=True)

        self.firefighter_list_frame.update_idletasks()
        self._update_firefighter_scrollregion()
        self.restore_firefighter_scroll_position(scroll_position)
        self.root.after_idle(lambda pos=scroll_position: self.restore_firefighter_scroll_position(pos))

    # ==========================================
    # ATUALIZAR RADAR
    # ==========================================
    def draw_radar(self, event=None):
        self.canvas.delete("all")
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        heading_rad = self.get_device_heading_radians()
        heading_deg = (math.degrees(heading_rad) + 360.0) % 360.0
        
        if w <= 10 or h <= 10: return

        if self.radar_fullscreen:
            warnings_panel_height = max(72, min(92, int(h * 0.11)))
        else:
            warnings_panel_height = max(110, min(155, int(h * 0.28)))
        warnings_top_y = h - warnings_panel_height

        cx, cy = w // 2, warnings_top_y // 2

        self.canvas.create_line(0, cy, w, cy, fill="black", width=1)
        self.canvas.create_line(cx, 0, cx, warnings_top_y, fill="black", width=1)

        margin = 22 if self.radar_fullscreen else 38
        max_radius = min(w, warnings_top_y) // 2 - margin
        if max_radius < 10:
            max_radius = 10

        for i in range(1, self.radar_ring_count + 1):
            r = (max_radius / self.radar_ring_count) * i
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline="black", width=1)
            ring_distance = i * self.radar_ring_step_m
            self.canvas.create_text(
                cx + 6,
                cy - r + 10,
                text=f"{ring_distance} m",
                font=("Arial", 7),
                fill="#555",
                anchor="w",
            )

        compass_x, compass_y = 54, 48
        compass_r = 28
        north_relative = -heading_rad
        north_tip_x = compass_x + compass_r * math.sin(north_relative)
        north_tip_y = compass_y - compass_r * math.cos(north_relative)
        south_tip_x = compass_x - compass_r * math.sin(north_relative)
        south_tip_y = compass_y + compass_r * math.cos(north_relative)

        self.canvas.create_oval(
            compass_x - compass_r - 4,
            compass_y - compass_r - 4,
            compass_x + compass_r + 4,
            compass_y + compass_r + 4,
            fill="#f4f4f4",
            outline="#2a2a2a",
            width=1,
        )
        self.canvas.create_oval(
            compass_x - compass_r + 5,
            compass_y - compass_r + 5,
            compass_x + compass_r - 5,
            compass_y + compass_r - 5,
            outline="#b8b8b8",
            width=1,
        )
        self.canvas.create_line(compass_x, compass_y - compass_r + 2, compass_x, compass_y + compass_r - 2, fill="#c8c8c8", width=1)
        self.canvas.create_line(compass_x - compass_r + 2, compass_y, compass_x + compass_r - 2, compass_y, fill="#c8c8c8", width=1)
        self.canvas.create_text(compass_x, compass_y - compass_r - 10, text="N", font=("Arial", 10, "bold"), fill="#b22222")
        self.canvas.create_text(compass_x + compass_r + 10, compass_y, text="E", font=("Arial", 8), fill="#555")
        self.canvas.create_text(compass_x, compass_y + compass_r + 10, text="S", font=("Arial", 8), fill="#555")
        self.canvas.create_text(compass_x - compass_r - 10, compass_y, text="W", font=("Arial", 8), fill="#555")
        self.canvas.create_line(compass_x, compass_y, south_tip_x, south_tip_y, fill="#595959", width=3)
        self.canvas.create_line(compass_x, compass_y, north_tip_x, north_tip_y, fill="#d7263d", width=4)
        self.canvas.create_oval(compass_x - 3, compass_y - 3, compass_x + 3, compass_y + 3, fill="#1f1f1f", outline="")
        self.canvas.create_text(compass_x, compass_y + compass_r + 24, text=f"HDG {heading_deg:.0f}\N{DEGREE SIGN}", font=("Arial", 8, "bold"), fill="#333")

        self.canvas.create_line(0, warnings_top_y, w, warnings_top_y, fill="black", width=4)

        rect_w, rect_h = 120, 30
        self.canvas.create_rectangle(0, warnings_top_y-rect_h, rect_w, warnings_top_y, fill="#ffcc00", outline="black", width=2)
        self.canvas.create_text(rect_w/2, warnings_top_y-(rect_h/2), text=f"\N{WARNING SIGN} {self.t('warnings')}", font=("Arial", 10, "bold"))

        scale_x_start = w - 100
        scale_x_end = w - 20
        scale_y = max(20, warnings_top_y - 22)
        self.canvas.create_text(scale_x_start, scale_y - 10, text="0 m", font=("Arial", 8))
        self.canvas.create_text(scale_x_end, scale_y - 10, text=f"{self.radar_max_distance_m} m", font=("Arial", 8))
        self.canvas.create_line(scale_x_start, scale_y, scale_x_start, scale_y+5, fill="black", width=1)
        self.canvas.create_line(scale_x_end, scale_y, scale_x_end, scale_y+5, fill="black", width=1)
        self.canvas.create_line(scale_x_start, scale_y, scale_x_end, scale_y, fill="black", width=1)
        self.canvas.create_text((scale_x_start + scale_x_end) / 2, scale_y + 11, text=f"{self.radar_ring_step_m} m / ring", font=("Arial", 7))

        node_r = 12
        radar_nodes = []
        for ff in self.firefighters:
            is_leader = ff.get('is_leader', False)
            if is_leader:
                leader_status = ff.get("status", "leader")
                leader_fill = self.safe_color
                leader_text = "black"
                leader_outline = "black"
                if leader_status == "critical":
                    leader_fill = self.alert_color
                    if self.blink_state:
                        self.canvas.create_oval(cx - 17, cy - 17, cx + 17, cy + 17, outline="#d7263d", width=2)
                elif leader_status == "no_signal":
                    leader_fill = "#111111"
                    leader_text = "white"
                    if self.blink_state:
                        self.canvas.create_oval(cx - 17, cy - 17, cx + 17, cy + 17, outline="#111111", width=2)

                leader_tag = f"ff_{ff['id']}"
                self.canvas.create_oval(
                    cx - node_r, cy - node_r, cx + node_r, cy + node_r,
                    fill=leader_fill, outline=leader_outline, tags=(leader_tag,)
                )
                if self.selected_firefighter_id == ff["id"]:
                    self.canvas.create_oval(cx - 16, cy - 16, cx + 16, cy + 16, outline="#1f6feb", width=2, tags=(leader_tag,))
                self.canvas.create_text(cx, cy, text="C", font=("Arial", 10, "bold"), fill=leader_text, tags=(leader_tag,))
                self.canvas.tag_bind(leader_tag, "<Button-1>", lambda _e, ff_id=ff["id"]: self.set_selected_firefighter(ff_id))
                continue

            r_pixels = ff['dist'] * max_radius
            relative_angle = ff['angle'] - heading_rad
            radar_nodes.append(
                {
                    "id": str(ff["id"]),
                    "x": cx + r_pixels * math.sin(relative_angle),
                    "y": cy - r_pixels * math.cos(relative_angle),
                    "status": ff.get("status", "normal"),
                }
            )

        # Agrupa operacionais sobrepostos para mostrar "1/2" no mesmo ponto
        overlap_threshold = 18
        clusters = []
        for node in radar_nodes:
            assigned = False
            for cluster in clusters:
                if math.hypot(node["x"] - cluster["x"], node["y"] - cluster["y"]) <= overlap_threshold:
                    cluster["members"].append(node)
                    total = len(cluster["members"])
                    cluster["x"] = sum(member["x"] for member in cluster["members"]) / total
                    cluster["y"] = sum(member["y"] for member in cluster["members"]) / total
                    assigned = True
                    break
            if not assigned:
                clusters.append({"x": node["x"], "y": node["y"], "members": [node]})

        for cluster in clusters:
            members = cluster["members"]
            ids = [member["id"] for member in members]
            ids.sort(key=lambda value: (0, int(value)) if value.isdigit() else (1, value))
            label_text = "/".join(ids)

            statuses = {member["status"] for member in members}
            cluster_status = "normal"
            if "no_signal" in statuses:
                cluster_status = "no_signal"
            elif "critical" in statuses:
                cluster_status = "critical"

            color = self.safe_color
            text_color = "black"
            icon_color = None
            icon_char = "!"
            if cluster_status == "critical":
                color = self.alert_color
                icon_color = "#e74c3c"
                if self.blink_state:
                    self.canvas.create_oval(cluster["x"] - 16, cluster["y"] - 16, cluster["x"] + 16, cluster["y"] + 16, outline="red", width=2)
            elif cluster_status == "no_signal":
                color = "black"
                text_color = "white"
                icon_color = "black"
                icon_char = "x"
                if self.blink_state:
                    self.canvas.create_oval(cluster["x"] - 16, cluster["y"] - 16, cluster["x"] + 16, cluster["y"] + 16, outline="black", width=2)

            click_target = members[0]["id"]
            if self.selected_firefighter_id in ids:
                click_target = self.selected_firefighter_id
            tag = f"ff_cluster_{'_'.join(ids)}"

            self.canvas.create_oval(
                cluster["x"] - node_r, cluster["y"] - node_r, cluster["x"] + node_r, cluster["y"] + node_r,
                fill=color, outline="black", tags=(tag,)
            )
            if self.selected_firefighter_id in ids:
                self.canvas.create_oval(
                    cluster["x"] - 16, cluster["y"] - 16, cluster["x"] + 16, cluster["y"] + 16,
                    outline="#1f6feb", width=2, tags=(tag,)
                )

            label_font = ("Arial", 10, "bold") if len(label_text) <= 3 else ("Arial", 8, "bold")
            self.canvas.create_text(cluster["x"], cluster["y"], text=label_text, font=label_font, fill=text_color, tags=(tag,))

            if icon_color:
                icon_x = cluster["x"] + 12
                icon_y = cluster["y"] - 22
                self.canvas.create_polygon(
                    icon_x, icon_y - 8,
                    icon_x - 7, icon_y + 5,
                    icon_x + 7, icon_y + 5,
                    outline=icon_color,
                    fill=self.bg_color,
                    width=1,
                    tags=(tag,)
                )
                self.canvas.create_text(icon_x, icon_y + 1, text=icon_char, fill=icon_color, font=("Arial", 8, "bold"), tags=(tag,))

            self.canvas.tag_bind(tag, "<Button-1>", lambda _e, ff_id=click_target: self.set_selected_firefighter(ff_id))

        # Historico de warnings
        max_warning_entries = 2 if self.radar_fullscreen else 4
        entries = self.warning_history[-max_warning_entries:]
        list_start_y = warnings_top_y + (8 if self.radar_fullscreen else 12)
        row_height = 29 if self.radar_fullscreen else 34
        left_col_x = 20
        left_col_w = 160 if self.radar_fullscreen else 180
        right_col_x = left_col_x + left_col_w + 18
        right_col_w = max(120, w - right_col_x - 20)
        warning_font = ("Arial", 10) if self.radar_fullscreen else ("Arial", 12)
        warning_row_h = 22 if self.radar_fullscreen else 24

        if not entries:
            self.canvas.create_text(
                left_col_x,
                list_start_y + 14,
                text=self.t("no_warnings"),
                font=("Arial", 10),
                fill="#555",
                anchor="w",
            )
            return

        for idx, entry in enumerate(entries):
            y0 = list_start_y + (idx * row_height)
            y1 = y0 + warning_row_h
            if y1 > h - 8:
                break

            self.canvas.create_rectangle(left_col_x, y0, left_col_x + left_col_w, y1, outline="black", width=1)
            self.canvas.create_text(
                left_col_x + 8,
                (y0 + y1) / 2,
                text=f"{entry['time']} {entry['name']}",
                font=warning_font,
                anchor="w",
            )

            self.canvas.create_rectangle(right_col_x, y0, right_col_x + right_col_w, y1, outline="black", width=1)
            self.canvas.create_text(
                right_col_x + 8,
                (y0 + y1) / 2,
                text=f"\N{WARNING SIGN}  {entry['reason']}",
                font=warning_font,
                anchor="w",
            )

    def on_close(self):
        if self.simulation_job:
            try:
                self.root.after_cancel(self.simulation_job)
            except tk.TclError:
                pass
            self.simulation_job = None

        self.dismiss_current_popup(queue_next=False)
        self.close_language_window()
        self.stop_live_data_receiver()
        if self.controller.current_session:
            self.controller.end_session()
        self.stop_alert_audio()
        if self.magnetometer:
            self.magnetometer.stop()
        if self.buzzer:
            self.buzzer.cleanup()
        self.speaker.cleanup()
        self.root.destroy()

    def update_clock(self):
        now = datetime.datetime.now().strftime("%H:%M")
        try:
            self.time_label.config(text=now)
            self.root.after(1000, self.update_clock)
        except:
            pass

if __name__ == "__main__":
    root = tk.Tk()
    app = ResQSenseApp(root)
    root.mainloop()
