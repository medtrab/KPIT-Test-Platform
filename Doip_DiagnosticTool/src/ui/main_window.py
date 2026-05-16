"""
MainWindow – orchestrates the UI and backend.
"""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QStackedWidget,
)
from PySide6.QtCore import Qt, QTimer
from core.controller import BackendController
from core.constants import LOGICAL_ADDR_BCM, LOGICAL_ADDR_WC
from ui.common.top_bar import TopBar
from ui.common.sidebar import Sidebar
from ui.common.splash_screen import SplashScreen
from ui.pages.dashboard_page import DashboardPage
from ui.pages.security_page import SecurityPage
from ui.pages.bcm_page import BCMPage
from ui.pages.wc_page import WCPage
from ui.common.log_panel import LogPanel
from ui.common.styling import DARK_STYLESHEET
from ui.pages.hil_page import HILPage
from ui.pages.uds_console_page import UDSConsolePage
from ui.pages.actuator_lab_page import ActuatorLabPage
import time


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self._bcm_restore_ts = 0.0
        self.current_session = 0x01
        self.current_security = 0
        self._bcm_ui_frozen = False

        self.setWindowTitle("Advanced Automotive Diagnostic Engineering Platform")
        self.setMinimumSize(1400, 900)
        self.setStyleSheet(DARK_STYLESHEET)

        self.controller = BackendController()
        self.controller.uds_positive.connect(self._handle_routine_control)

        self._setup_central_widget()
        self._create_pages()
        self._connect_ui_signals()
        self.top_bar.ecu_selector.setCurrentIndex(0)
        self._connect_controller_signals()

        self.controller.snapshot_received.connect(self._on_snapshot_received)
        self.controller.extended_received.connect(self._on_extended_received)

        self.status_bar = self.statusBar()
        self.status_bar.showMessage("Ready")

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.top_bar.update_time)
        self.timer.start(1000)

        self.stacked.setCurrentWidget(self.splash)
        self.splash.finished.connect(self.show_dashboard)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _setup_central_widget(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.top_bar = TopBar()
        main_layout.addWidget(self.top_bar)

        middle = QWidget()
        middle_layout = QHBoxLayout(middle)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        middle_layout.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.page_selected.connect(self.switch_page)
        middle_layout.addWidget(self.sidebar)

        self.stacked = QStackedWidget()
        middle_layout.addWidget(self.stacked, 1)

        main_layout.addWidget(middle, 1)

        self.log_panel = LogPanel(self)
        self.addDockWidget(Qt.RightDockWidgetArea, self.log_panel)

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    def _create_pages(self):
        self.splash      = SplashScreen()
        self.dashboard   = DashboardPage()
        self.security    = SecurityPage()
        self.bcm         = BCMPage()
        self.wc          = WCPage()
        self.hil         = HILPage()
        self.uds_console = UDSConsolePage()
        self.actuator_lab = ActuatorLabPage()
        self.stacked.addWidget(self.actuator_lab)

        self.hil.read_status_requested.connect(self._handle_read_status)

        self.stacked.addWidget(self.splash)
        self.stacked.addWidget(self.dashboard)
        self.stacked.addWidget(self.security)
        self.stacked.addWidget(self.hil)
        self.stacked.addWidget(self.bcm)
        self.stacked.addWidget(self.wc)
        self.stacked.addWidget(self.uds_console)

    # ------------------------------------------------------------------
    # UI signal connections
    # ------------------------------------------------------------------

    def _connect_ui_signals(self):
        self.dashboard.discover_requested.connect(self.controller.discover_ecus)
        self.dashboard.connect_requested.connect(self._connect_to_selected_ecu)
        self.dashboard.read_dtc_requested.connect(self.controller.read_dtc)
        self.dashboard.clear_dtc_requested.connect(self.controller.clear_dtc)
        self.dashboard.default_session_requested.connect(self._on_default_session_requested)
        self.dashboard.extended_session_requested.connect(self._on_extended_session_requested)
        self.dashboard.ecu_reset_requested.connect(self._on_ecu_reset)

        self.security.seed_requested.connect(self.controller.request_seed)
        self.security.key_send_requested.connect(self.controller.send_key)

        self.bcm.read_dtc_requested.connect(self.controller.read_dtc)
        self.bcm.clear_dtc_requested.connect(self.controller.clear_dtc)
        self.bcm.snapshot_requested.connect(self.controller.read_dtc_snapshot)
        self.bcm.extended_requested.connect(self.controller.read_dtc_extended)
        self.bcm.communication_control_requested.connect(self.controller.communication_control)

        self.wc.read_dtc_requested.connect(self.controller.read_dtc)
        self.wc.clear_dtc_requested.connect(self.controller.clear_dtc)
        self.wc.snapshot_requested.connect(self.controller.read_dtc_snapshot)
        self.wc.extended_requested.connect(self.controller.read_dtc_extended)

        self.top_bar.target_changed.connect(self._on_target_changed)

        self.hil.apply_coding_requested.connect(self._apply_hil_coding)
        self.hil.routine_requested.connect(self._handle_hil_routine)

        self.uds_console.raw_uds_requested.connect(self.controller.send_uds_request)

        

    # ------------------------------------------------------------------
    # Controller signal connections
    # ------------------------------------------------------------------

    def _connect_controller_signals(self):
        self.controller.session_changed.connect(self._on_session_changed)
        self.controller.security_level_changed.connect(self._on_security_changed)
        self.controller.log_message.connect(self._handle_filtered_log_message)

        self.controller.connection_state_changed.connect(self.top_bar.set_connection_state)
        self.controller.connection_state_changed.connect(
            lambda c, msg: self.status_bar.showMessage(msg)
        )
        self.controller.routing_state_changed.connect(self.top_bar.set_routing_state)

        self.controller.dtc_list_updated.connect(self._update_dtc_tables)
        self.controller.seed_received.connect(self.security.display_seed)
        self.controller.error_occurred.connect(
            lambda msg: self.status_bar.showMessage(f"Error: {msg}", 5000)
        )

        self.controller.ecu_discovered.connect(self.dashboard.update_ecu_list)
        self.controller.connection_state_changed.connect(self.dashboard.set_connection_state)
        self.controller.routing_state_changed.connect(self.dashboard.set_routing_state)

        self.controller.dtc_database_ready.connect(self._on_dtc_database_ready)
        self.controller.uds_positive.connect(self._update_hil_status_table)
        self.controller.uds_positive.connect(self.uds_console.handle_response)
        self.controller.uds_sent.connect(self.uds_console.handle_sent)
        self.top_bar.theme_changed.connect(self._on_theme_changed)
        self.controller.uds_positive.connect(self._on_uds_positive_hil)
        self.controller.uds_sent.connect(self._on_uds_sent_hil)
    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _on_uds_positive_hil(self, response: bytes):
        if not response or len(response) < 3:
            return
        if response[0] == 0x62 and len(response) >= 4:
            did = (response[1] << 8) | response[2]
            value = response[3]
            if did in (0xF200, 0xF201, 0xF202, 0xF203, 0xF204):
                self._update_hil_coding_widget(did, value)

    def _on_uds_sent_hil(self, request: bytes):
        if not request or len(request) < 4:
            return
        # 0x2E WriteDataByIdentifier
        if request[0] == 0x2E:
            did = (request[1] << 8) | request[2]
            value = request[3]
            if did in (0xF200, 0xF201, 0xF202, 0xF203, 0xF204):
                self._update_hil_coding_widget(did, value)

    def _update_hil_coding_widget(self, did: int, value: int):
        if did == 0xF200:
            self.hil.rain_sensor.blockSignals(True)
            self.hil.rain_sensor.setChecked(bool(value))
            self.hil.rain_sensor.blockSignals(False)
        elif did == 0xF201:
            self.hil.wc_available.blockSignals(True)
            self.hil.wc_available.setChecked(bool(value))
            self.hil.wc_available.blockSignals(False)
            self.top_bar.enable_wc() if value else self.top_bar.disable_wc()
        elif did == 0xF202:
            self.hil.rear_wiper.blockSignals(True)
            self.hil.rear_wiper.setChecked(bool(value))
            self.hil.rear_wiper.blockSignals(False)
        elif did == 0xF203:
            self.hil.front_wash.blockSignals(True)
            self.hil.front_wash.setCurrentIndex(value)
            self.hil.front_wash.blockSignals(False)
        elif did == 0xF204:
            self.hil.rear_camera.blockSignals(True)
            self.hil.rear_camera.setCurrentIndex(value)
            self.hil.rear_camera.blockSignals(False)


    def _handle_routine_control(self, response: bytes):
        if len(response) < 4 or response[0] != 0x71:
            return

    def _handle_read_status(self, dids: list):
        if not dids:
            return
        is_wc = (self.controller._worker._target_addr == LOGICAL_ADDR_WC)
        allowed = (
            {0xF000, 0xF001, 0xF002, 0xF003, 0xF004} if is_wc
            else {0xF100, 0xF101, 0xF102, 0xF103, 0xF104, 0xF105, 0xF106, 0xF107}
        )
        for did in dids:
            if did in allowed:
                self.controller.read_data_by_identifier(did)

    def _handle_hil_routine(self, rid: int, value: int):
        if value == -1:
            # Stop routine — 31 02
            self.controller.stop_routine(rid)
        else:
            # Start routine — 31 01
            self.controller.start_routine(rid, value)

    def _apply_hil_coding(self, coding: dict):
        for did, value in coding.items():
            self.controller.write_coding(did, value)
        wc_available = coding.get(0xF201, 0)
        if wc_available == 1:
            self.top_bar.enable_wc()
        else:
            self.top_bar.disable_wc()

    def _connect_to_selected_ecu(self):
        ecu = self.dashboard.get_selected_ecu()
        if ecu:
            self.controller.connect_to_ecu(ecu)

    def _on_default_session_requested(self):
        self._bcm_restore_ts = 0.0
        self.controller.switch_session(0x01)

    def _on_extended_session_requested(self):
        self._bcm_restore_ts = 0.0
        self.controller.switch_session(0x03)

    def _on_ecu_reset(self):
        self.controller.send_uds_request(bytes([0x11, 0x03]))

    def _update_hil_status_table(self, response: bytes):
        if not response or response[0] != 0x62 or len(response) < 4:
            return

        did = (response[1] << 8) | response[2]
        value_bytes = response[3:]
        is_wc = (self.controller._worker._target_addr == LOGICAL_ADDR_WC)

        mode_names  = {0: "OFF", 1: "TOUCH", 2: "SPEED1", 3: "SPEED2",
                       4: "AUTO", 5: "WASH_FRONT", 6: "WASH_REAR",
                       7: "ERROR", 8: "REAR_WIPE", 9: "DIAG"}
        speed_names = {0: "OFF", 1: "Speed1", 2: "Speed2"}
        pump_names  = {0: "OFF", 1: "FORWARD (FrontWash)", 2: "BACKWARD (RearWash)"}

        from PySide6.QtWidgets import QTableWidgetItem

        if is_wc:
            did_order = [0xF000, 0xF001, 0xF002, 0xF003, 0xF004]
            if did not in did_order:
                return
            row = did_order.index(did)
            value = value_bytes[0] if value_bytes else 0
            if did == 0xF000:   readable = mode_names.get(value, f"Unknown(0x{value:02X})")
            elif did == 0xF001: readable = speed_names.get(value, str(value))
            elif did == 0xF002: readable = f"{value}%"
            elif did == 0xF003:
                if len(value_bytes) >= 2:
                    value = (value_bytes[0] << 8) | value_bytes[1]
                readable = f"{value} mA"
            elif did == 0xF004: readable = pump_names.get(value, f"Unknown(0x{value:02X})")
            else:               readable = str(value)
        else:
            did_order = [0xF100, 0xF101, 0xF102, 0xF103,
                         0xF104, 0xF105, 0xF106, 0xF107]
            if did not in did_order:
                return
            row = did_order.index(did)
            value = value_bytes[0] if value_bytes else 0
            if did == 0xF100:   readable = mode_names.get(value, f"Unknown(0x{value:02X})")
            elif did == 0xF101: readable = speed_names.get(value, str(value))
            elif did == 0xF102: readable = f"{value}%"
            elif did == 0xF103:
                if len(value_bytes) >= 2:
                    value = (value_bytes[0] << 8) | value_bytes[1]
                readable = f"{value} mA"
            elif did == 0xF104: readable = pump_names.get(value, f"Unknown(0x{value:02X})")
            elif did == 0xF105: readable = f"{value}%"
            elif did == 0xF106: readable = "ON" if value else "OFF"
            elif did == 0xF107:
                flags = []
                if value & 0x04: flags.append("ERROR_STATE")
                if value & 0x10: flags.append("MOTOR_OC")       # B2001/B2002
                if value & 0x20: flags.append("WIPER_FAULT")    # B2006/B2009
                if value & 0x40: flags.append("PUMP_OC")        # B2003
                if value & 0x80: flags.append("PUMP_RUNTIME")   # B2008
                readable = ", ".join(flags) if flags else "NONE"
            else: readable = str(value)

        self.hil.status_table.setItem(row, 2, QTableWidgetItem(readable))

    # ------------------------------------------------------------------
    # Target switch
    # ------------------------------------------------------------------

    def _on_target_changed(self, addr: int):
        is_wc = (addr == LOGICAL_ADDR_WC)

        self.controller.set_target(addr)
        self.dashboard.set_target(addr)

        bcm_db = getattr(self, '_dtc_db_bcm', {})
        wc_db  = getattr(self, '_dtc_db_wc',  {})
        self.dashboard.set_dtc_database(wc_db if is_wc else bcm_db)

        if is_wc:
            self._bcm_ui_frozen      = True
            self._bcm_restore_ts     = time.time() + 10
            self._bcm_saved_session  = self.current_session
            self._bcm_saved_security = self.current_security

            self.current_session  = 0x01
            self.current_security = 0
            self.top_bar.set_session(0x01)
            self.top_bar.set_security(0)
            self.dashboard.set_session(0x01)
            self.dashboard.set_security_none()

            self.dashboard.set_extended_session_enabled(False)
            self.dashboard.ecu_reset_btn.setEnabled(False)
            self.dashboard.ecu_reset_btn.setStyleSheet(
                "background-color: #4a4a4a; color: #888; border-radius: 6px; padding: 10px 16px; font-weight: bold;"
            )
            self.bcm.disable_rx_btn.setEnabled(False)
            self.bcm.disable_tx_btn.setEnabled(False)
            self.bcm.disable_all_btn.setEnabled(False)
            self.bcm.enable_all_btn.setEnabled(False)

        else:
            self._bcm_ui_frozen  = False
            self._bcm_restore_ts = time.time()

            saved_session  = getattr(self, '_bcm_saved_session',  0x01)
            saved_security = getattr(self, '_bcm_saved_security', 0)
            self.current_session  = saved_session
            self.current_security = saved_security

            self.top_bar.set_session(saved_session)
            self.top_bar.set_security(saved_security)
            self.dashboard.set_session(saved_session)
            self.dashboard.set_security(saved_security)
            self.security.update_security_level(saved_security)

            QTimer.singleShot(200, lambda: self._restore_bcm_ui(saved_session, saved_security))

            self.dashboard.set_extended_session_enabled(True)
            self.dashboard.ecu_reset_btn.setEnabled(True)
            self.dashboard.ecu_reset_btn.setStyleSheet("")
            self.bcm.disable_rx_btn.setEnabled(True)
            self.bcm.disable_tx_btn.setEnabled(True)
            self.bcm.disable_all_btn.setEnabled(True)
            self.bcm.enable_all_btn.setEnabled(True)

        target_label = self.dashboard.cards.get("Target ECU")
        if target_label:
            if is_wc:
                target_label.setText("WC (0x701)")
                target_label.setStyleSheet("color: #2e7d32; font-size: 20px; font-weight: bold;")
            else:
                target_label.setText("BCM (0x700)")
                target_label.setStyleSheet("color: green; font-size: 20px; font-weight: bold;")

        self.stacked.setCurrentWidget(self.dashboard)
        self.sidebar.set_selected("Dashboard")

        self._dtc_page_override = self.wc if is_wc else self.bcm

        self._set_hil_wc_mode(is_wc)
        self.hil.set_target(addr)
        self.actuator_lab.set_target(addr)

        self.security.setEnabled(not is_wc)

    def _restore_bcm_ui(self, session: int, security: int):
        if self.controller._worker._target_addr == LOGICAL_ADDR_WC:
            return
        self.top_bar.set_session(session)
        self.top_bar.set_security(security)
        self.dashboard.set_session(session)
        self.dashboard.set_security(security)
        self.security.update_security_level(security)

    def _set_hil_wc_mode(self, wc_mode: bool):
        # Coding — disabled in WC mode
        self.hil.rain_sensor.setEnabled(not wc_mode)
        self.hil.wc_available.setEnabled(not wc_mode)
        self.hil.rear_wiper.setEnabled(not wc_mode)
        self.hil.front_wash.setEnabled(not wc_mode)
        self.hil.rear_camera.setEnabled(not wc_mode)
        self.hil.apply_btn.setEnabled(not wc_mode)
        # Front wiper — always active (WC supports 0x0201)
        self.hil.front_wiper_btn.setEnabled(True)
        self.hil.front_wiper_stop_btn.setEnabled(True)
        self.hil.front_wiper_duration.setEnabled(True)
        # Rear wiper — disabled in WC mode
        self.hil.rear_wiper_btn.setEnabled(not wc_mode)
        self.hil.rear_wiper_stop_btn.setEnabled(not wc_mode)
        self.hil.rear_wiper_duration.setEnabled(not wc_mode)
        # Pump — disabled in WC mode
        self.hil.pump_test_btn.setEnabled(not wc_mode)
        self.hil.pump_stop_btn.setEnabled(not wc_mode)
        self.hil.pump_duration.setEnabled(not wc_mode)
        # Rain — disabled in WC mode
        self.hil.rain_apply_btn.setEnabled(not wc_mode)
        self.hil.rain_stop_btn.setEnabled(not wc_mode)
        self.hil.rain_value.setEnabled(not wc_mode)

    # ------------------------------------------------------------------
    # State handlers
    # ------------------------------------------------------------------

    def _on_session_changed(self, session):
        if self._bcm_ui_frozen:
            return
        if self.controller._worker._target_addr == LOGICAL_ADDR_WC:
            return
        self.current_session = session
        if (time.time() - self._bcm_restore_ts) < 3.0:
            return
        self.top_bar.set_session(session)
        self.dashboard.set_session(session)
        if session == 0x01:
            self.current_security = 0
            self.top_bar.set_security(0)
            self.dashboard.set_security(0)
            self.security.update_security_level(0)

    def _on_security_changed(self, level):
        if self._bcm_ui_frozen:
            return
        if self.controller._worker._target_addr == LOGICAL_ADDR_WC:
            return
        self.current_security = level
        if (time.time() - self._bcm_restore_ts) < 3.0:
            return
        self.top_bar.set_security(level)
        self.dashboard.set_security(level)
        self.security.update_security_level(level)

    def _on_dtc_database_ready(self, payload):
        bcm_db = payload.get("bcm", {})
        wc_db  = payload.get("wc",  {})
        self.bcm.set_dtc_database(bcm_db)
        self.wc.set_dtc_database(wc_db)
        is_wc = (self.controller._worker._target_addr == LOGICAL_ADDR_WC)
        self.dashboard.set_dtc_database(wc_db if is_wc else bcm_db)
        self._dtc_db_bcm = bcm_db
        self._dtc_db_wc  = wc_db
        self.status_bar.showMessage(
            f"DTC database: BCM={len(bcm_db)} WC={len(wc_db)} entries", 5000)

    def _update_dtc_tables(self, dtc_list):
        is_wc = (self.controller._worker._target_addr == LOGICAL_ADDR_WC)
        self.dashboard.update_dtc_table(dtc_list)
        if is_wc:
            self.wc.update_dtc_table(dtc_list)
        else:
            self.bcm.update_dtc_table(dtc_list)

    def _on_snapshot_received(self, dtc_code: int, record_num: int, data: bytes):
        is_wc = (self.controller._worker._target_addr == LOGICAL_ADDR_WC)
        if is_wc:
            self.wc.display_snapshot(dtc_code, record_num, data)
        else:
            self.bcm.display_snapshot(dtc_code, record_num, data)

    def _on_extended_received(self, dtc_code: int, record_num: int, data: bytes):
        is_wc = (self.controller._worker._target_addr == LOGICAL_ADDR_WC)
        if is_wc:
            self.wc.display_extended(dtc_code, record_num, data)
        else:
            self.bcm.display_extended(dtc_code, record_num, data)

    # ------------------------------------------------------------------
    # Log
    # ------------------------------------------------------------------

    def _handle_filtered_log_message(self, level: str, message: str):
        normalized = " ".join(message.strip().upper().split())
        if "NRC 0X24" in normalized and "REQUEST SEQUENCE ERROR" in normalized:
            return
        self.log_panel.add_message(level, message)

    def _on_theme_changed(self, dark_mode: bool):
        from ui.common.styling import DARK_STYLESHEET, LIGHT_STYLESHEET
        self.setStyleSheet(DARK_STYLESHEET if dark_mode else LIGHT_STYLESHEET)
        self._apply_theme_sidebar(dark_mode)
        self._apply_theme_dashboard(dark_mode)
        self._apply_theme_security(dark_mode)
        self._apply_theme_bcm(dark_mode)
        self._apply_theme_uds_console(dark_mode)
        self._apply_theme_frame_monitor(dark_mode)
        self.actuator_lab.apply_theme(dark_mode)
        self.splash.apply_theme(dark_mode)
        self.top_bar._apply_topbar_style()

        if dark_mode:
            cb_style = ""
        else:
            cb_style = """
                        QCheckBox { color: #0d1f0d; }
                        QCheckBox::indicator {
                            width: 16px; height: 16px;
                            border: 1.5px solid #546E7A;
                            border-radius: 3px;
                            background: #ffffff;
                        }
                        QCheckBox::indicator:checked {
                            background: #2e7d32;
                            border: 1.5px solid #1b5e20;
                        }
                        QCheckBox::indicator:disabled {
                            background: #B0BEC5;
                            border: 1.5px solid #90A4AE;
                        }
                        QCheckBox::indicator:checked:disabled {
                            background: #2e7d32;
                            border: 1.5px solid #1b5e20;
                        }
                        QCheckBox:disabled { color: #546E7A; }
                    """
        for cb in (self.hil.rain_sensor, self.hil.wc_available, self.hil.rear_wiper):
            cb.setStyleSheet(cb_style)

    def _apply_theme_uds_console(self, dark_mode: bool):
        if dark_mode:
            self.uds_console.cmd_input.setStyleSheet("""
                QLineEdit {
                    background-color: #0d1f0d;
                    color: #a5d6a7;
                    border: 2px solid #2e7d32;
                    border-radius: 6px;
                    padding: 4px 10px;
                    selection-background-color: #2e7d32;
                }
                QLineEdit:focus { border: 2px solid #66bb6a; }
            """)
            if hasattr(self.uds_console, 'title'):
                self.uds_console.title.setStyleSheet(
                    "font-size: 20px; font-weight: 900; color: #ffffff; letter-spacing: 1px;"
                )
            if hasattr(self.uds_console, 'table'):
                self.uds_console.table.setStyleSheet("""
                    QTableWidget { background-color: #0d1117; color: #c9d1d9; }
                    QHeaderView::section {
                        background-color: #161b22; color: #8b949e;
                        font-weight: bold; padding: 6px;
                    }
                    QScrollBar:horizontal {
                        background-color: #1e1e1e; height: 8px; border-radius: 4px;
                    }
                    QScrollBar::handle:horizontal {
                        background-color: #546e7a; border-radius: 4px; min-width: 24px;
                    }
                    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
                """)
            self.uds_console.set_theme(True)
        else:
            self.uds_console.cmd_input.setStyleSheet("""
                QLineEdit {
                    background-color: #ffffff;
                    color: #0d1f0d;
                    border: 2px solid #2e7d32;
                    border-radius: 6px;
                    padding: 4px 10px;
                    selection-background-color: #a5d6a7;
                }
                QLineEdit:focus { border: 2px solid #1b5e20; }
            """)
            if hasattr(self.uds_console, 'title'):
                self.uds_console.title.setStyleSheet(
                    "font-size: 20px; font-weight: 900; color: #1b5e20; letter-spacing: 1px;"
                )
            if hasattr(self.uds_console, 'table'):
                self.uds_console.table.setStyleSheet("""
                    QTableWidget { background-color: #ffffff; color: #0d1f0d; gridline-color: #90A4AE; }
                    QHeaderView::section {
                        background-color: #2e7d32; color: #ffffff;
                        font-weight: bold; padding: 6px;
                    }
                    QTableWidget::item { color: #0d1f0d; }
                    QTableWidget::item:selected { background-color: #A5D6A7; color: #0d1f0d; }
                    QScrollBar:horizontal {
                        background-color: #C5CBD0; height: 8px; border-radius: 4px;
                    }
                    QScrollBar::handle:horizontal {
                        background-color: #78909C; border-radius: 4px; min-width: 24px;
                    }
                    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
                """)
            self.uds_console.set_theme(False)

    def _apply_theme_frame_monitor(self, dark_mode: bool):
        fm = self.bcm.frame_monitor
        if dark_mode:
            fm.filter_combo.setStyleSheet("""
                QComboBox {
                    background-color:#1e1e1e; color:#a5d6a7;
                    border:1px solid #2e7d32; border-radius:4px;
                    padding:2px 8px; font-size:12px;
                }
                QComboBox QAbstractItemView {
                    background-color:#1e1e1e; color:#a5d6a7;
                    selection-background-color:#2e7d32;
                }
            """)
            fm.table.setStyleSheet("""
                QTableWidget {
                    background-color:#0d1117; color:#c9d1d9;
                    gridline-color:#21262d; border:none;
                }
                QHeaderView::section {
                    background-color:#161b22; color:#8b949e;
                    border:none; padding:6px;
                    font-weight:bold; font-size:11px;
                }
                QTableWidget::item:selected { background-color:#1f3a5f; }
            """)
            detail_style = """
                QGroupBox {
                    font-weight:bold; font-size:11px; color:#c9d1d9;
                    border:2px solid #30363d; border-radius:6px;
                    margin-top:0.8em; padding-top:6px;
                    background-color:#0d1117;
                }
                QGroupBox::title {
                    subcontrol-origin:margin; left:8px; padding:0 4px;
                }
            """
            if hasattr(fm, '_detail_widget'):
                fm._detail_widget.setStyleSheet("background-color:#0d1117;")
                fm.hex_view.setStyleSheet(
                    "color:#79c0ff; background:transparent; padding:4px; font-weight:bold;"
                )
                fm.doip_view.setStyleSheet(
                    "color:#ffa657; background:transparent; padding:4px; font-weight:bold;"
                )
                fm.uds_view.setStyleSheet(
                    "color:#a5d6a7; background:transparent; padding:4px; font-weight:bold;"
                )
        else:
            fm.filter_combo.setStyleSheet("""
                QComboBox {
                    background-color:#ffffff; color:#0d1f0d;
                    border:1px solid #2e7d32; border-radius:4px;
                    padding:2px 8px; font-size:12px;
                }
                QComboBox QAbstractItemView {
                    background-color:#ffffff; color:#0d1f0d;
                    selection-background-color:#a5d6a7;
                    selection-color:#0d1f0d;
                }
            """)
            fm.table.setStyleSheet("""
                QTableWidget {
                    background-color:#EEF1F4; color:#0d1f0d;
                    gridline-color:#90A4AE; border:none;
                }
                QHeaderView::section {
                    background-color:#2e7d32; color:#ffffff;
                    border:none; padding:6px;
                    font-weight:bold; font-size:11px;
                }
                QTableWidget::item { color:#0d1f0d; }
                QTableWidget::item:selected {
                    background-color:#A5D6A7; color:#0d1f0d;
                }
            """)
            detail_style = """
                QGroupBox {
                    font-weight:bold; font-size:11px; color:#1b5e20;
                    border:2px solid #2e7d32; border-radius:6px;
                    margin-top:0.8em; padding-top:6px;
                    background-color:#E2E7EB;
                }
                QGroupBox::title {
                    subcontrol-origin:margin; left:8px; padding:0 4px;
                    color:#1b5e20; font-weight:bold;
                }
                QLabel { color:#0d1f0d; font-weight:600; }
            """
            if hasattr(fm, '_detail_widget'):
                fm._detail_widget.setStyleSheet("background-color:#E2E7EB;")
                fm.hex_view.setStyleSheet(
                    "color:#0d47a1; background:transparent; padding:4px; font-weight:bold;"
                )
                fm.doip_view.setStyleSheet(
                    "color:#e65100; background:transparent; padding:4px; font-weight:bold;"
                )
                fm.uds_view.setStyleSheet(
                    "color:#1b5e20; background:transparent; padding:4px; font-weight:bold;"
                )

        from PySide6.QtWidgets import QGroupBox as _QGB
        for grp in fm.findChildren(_QGB):
            grp.setStyleSheet(detail_style)

    def _apply_theme_bcm(self, dark_mode: bool):
        if dark_mode:
            combo_style = """
                QComboBox {
                    background-color: #2D2D2D; color: #E0E0E0;
                    border: 2px solid #546E7A; border-radius: 5px;
                    padding: 5px 8px; font-weight: bold;
                }
                QComboBox:focus { border: 2px solid #2E7D32; }
                QComboBox QAbstractItemView {
                    background-color: #2D2D2D; color: #E0E0E0;
                    selection-background-color: #2E7D32;
                    selection-color: #FFFFFF; border: 1px solid #546E7A;
                }
            """
        else:
            combo_style = """
                QComboBox {
                    background-color: #FFFFFF; color: #0D1F1F;
                    border: 2px solid #546E7A; border-radius: 5px;
                    padding: 5px 8px; font-weight: bold;
                }
                QComboBox:focus { border: 2px solid #2E7D32; }
                QComboBox QAbstractItemView {
                    background-color: #FFFFFF; color: #0D1F1F;
                    selection-background-color: #A5D6A7;
                    selection-color: #0D1F1F; border: 1px solid #546E7A;
                }
            """
        if hasattr(self.bcm, 'status_mask_combo'):
            self.bcm.status_mask_combo.setStyleSheet(combo_style)

    def _apply_theme_sidebar(self, dark_mode: bool):
        if dark_mode:
            sidebar_style = "QFrame { background-color: #1A1A1A; border-right: 2px solid #2E7D32; }"
            btn_style = """
                QPushButton {
                    background-color: transparent; color: #E0E0E0;
                    text-align: left; padding-left: 20px;
                    border: none; font-size: 14px;
                }
                QPushButton:hover { background-color: #2E7D32; color: white; }
                QPushButton:checked {
                    background-color: #1B5E20; color: white;
                    border-left: 4px solid #A5D6A7;
                }
            """
            collapse_style = """
                QPushButton {
                    background-color: #2E7D32; color: white;
                    border: none; padding: 8px; font-weight: bold;
                }
                QPushButton:hover { background-color: #1B5E20; }
            """
        else:
            sidebar_style = "QFrame { background-color: #B0BEC5; border-right: 2px solid #2E7D32; }"
            btn_style = """
                QPushButton {
                    background-color: transparent; color: #0D1F1F;
                    text-align: left; padding-left: 20px;
                    border: none; font-size: 14px; font-weight: 500;
                }
                QPushButton:hover { background-color: #2E7D32; color: white; }
                QPushButton:checked {
                    background-color: #1B5E20; color: white;
                    border-left: 4px solid #A5D6A7;
                }
            """
            collapse_style = """
                QPushButton {
                    background-color: #2E7D32; color: white;
                    border: none; padding: 8px; font-weight: bold;
                }
                QPushButton:hover { background-color: #1B5E20; }
            """
        self.sidebar.setStyleSheet(sidebar_style)
        for btn in self.sidebar.buttons:
            btn.setStyleSheet(btn_style)
        self.sidebar.collapse_btn.setStyleSheet(collapse_style)

    def _apply_theme_dashboard(self, dark_mode: bool):
        if dark_mode:
            card_style = """
                QFrame {
                    background-color: #2D2D2D;
                    border: 1px solid #2E7D32;
                    border-radius: 8px; padding: 10px;
                }
            """
        else:
            card_style = """
                QFrame {
                    background-color: #E2E7EB;
                    border: 1.5px solid #90A4AE;
                    border-radius: 8px; padding: 10px;
                }
            """
        for card in (
                self.dashboard.conn_card, self.dashboard.routing_card,
                self.dashboard.target_card, self.dashboard.session_card,
                self.dashboard.security_card,
        ):
            card.setStyleSheet(card_style)

    def _apply_theme_security(self, dark_mode: bool):
        if not hasattr(self.security, 'card'):
            return
        if dark_mode:
            self.security.card.setStyleSheet("""
                QFrame {
                    background-color: #2D2D2D;
                    border: 2px solid #2E7D32;
                    border-radius: 12px; padding: 20px;
                }
            """)
            self.security.title_label.setStyleSheet(
                "font-size: 22px; font-weight: bold; color: #A5D6A7; background: transparent;"
            )
        else:
            self.security.card.setStyleSheet("""
                QFrame {
                    background-color: #E2E7EB;
                    border: 2px solid #2E7D32;
                    border-radius: 12px; padding: 20px;
                }
            """)
            self.security.title_label.setStyleSheet(
                "font-size: 22px; font-weight: bold; color: #1B5E20; background: transparent;"
            )
    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def show_dashboard(self):
        self.stacked.setCurrentWidget(self.dashboard)
        self.sidebar.set_selected("Dashboard")

    def switch_page(self, page_name: str):
        mapping = {
            "Dashboard":             self.dashboard,
            "Session & Security":    self.security,
            "HIL":                   self.hil,
            "DTC Management":        getattr(self, '_dtc_page_override', self.bcm),
            "UDS Console":           self.uds_console,
            "Communication Control": self.bcm,
            "Actuator Lab": self.actuator_lab,
        }
        page = mapping.get(page_name)
        if not page:
            return
        self.stacked.setCurrentWidget(page)
        if page_name == "DTC Management" and hasattr(page, "set_tab"):
            page.set_tab(0)
        elif page_name == "Communication Control" and hasattr(page, "set_tab"):
            page.set_tab(1)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        self.controller.shutdown()
        event.accept()