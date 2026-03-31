"""
WipeWash — Fenêtre principale
MainWindow (3 onglets : Motor/Pompe | LIN/CRS | CAN/Vehicle) + NetworkScanDialog.

MODIFICATION v2 :
  _make_runner() passe pump_signal=self._pump_signal à TestRunner.

OPTIMISATIONS :
  - Architecture 3 pages QTabWidget au lieu de 6 docks flottants → moins de
    widgets rendus simultanément.
  - Oscilloscopes LIN et CAN mis en pause sur les onglets cachés (timer.stop/start).
  - DOCK_STYLE supprimé (inutile).
  - _set_tb_status : dict PORT→name pré-calculé en attribut de classe.
"""

import datetime

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QFrame, QLabel, QToolBar, QStatusBar,
    QTabWidget, QSplitter, QMenu, QDialog, QMessageBox,
    QProgressBar, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui  import QFont, QColor, QAction

from constants import (
    FONT_UI, FONT_MONO,
    W_BG, W_PANEL, W_PANEL2, W_PANEL3,
    W_BORDER, W_TOOLBAR,
    W_TEXT, W_TEXT_DIM, W_DOCK_HDR,
    A_TEAL, A_TEAL2, A_GREEN, A_RED, A_ORANGE,
    PORT_MOTOR, PORT_LIN, PORT_PUMP_RX,
)
try:
    from constants import PORT_CAN, CAN_VEH_C
except ImportError:
    PORT_CAN  = 5557
    CAN_VEH_C = "#007ACC"
from network  import scan_async
from workers  import (
    MotorVehicleWorker, LINWorker, PumpSignal, PumpDataClient, CANWorker,
)
from widgets_base import StatusLed, _lbl, _hsep, _cd_btn
from panels import (
    MotorDashPanel, PumpPanel, VehicleRainPanel, CRSLINPanel, CANBusPanel,
)
from auto_test_panel import AutoTestPanel
from test_runner     import TestRunner
from rte_client      import RTEClient
from PyQt6.QtCore    import QThread


# ═══════════════════════════════════════════════════════════
#  SCAN DIALOG
# ═══════════════════════════════════════════════════════════
class NetworkScanDialog(QDialog):
    connected = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Découverte automatique — WipeWash BCM")
        self.setMinimumSize(520, 380)
        self.setStyleSheet(f"background:{W_PANEL};color:{W_TEXT};")
        lay = QVBoxLayout(self); lay.setContentsMargins(16, 14, 16, 14); lay.setSpacing(10)

        lay.addWidget(_lbl("LOCAL NETWORK SCAN", 18, True, A_TEAL2))
        lay.addWidget(_hsep())

        self._results_lay = QVBoxLayout(); self._results_lay.setSpacing(4)
        lay.addLayout(self._results_lay, 1)

        self._bars: dict[str, QProgressBar] = {}
        self._bar_lbls: dict[str, QLabel]  = {}
        for name in ["Motors / Wiper", "Pump", "LIN Bus"]:
            row = QHBoxLayout(); row.setSpacing(8)
            lbl = _lbl(name, 10, True, W_TEXT_DIM); lbl.setFixedWidth(140)
            bar = QProgressBar(); bar.setRange(0, 100); bar.setValue(0)
            bar.setFixedHeight(16); bar.setTextVisible(False)
            bar.setStyleSheet(
                f"QProgressBar{{background:{W_PANEL3};border:1px solid {W_BORDER};border-radius:2px;}}"
                f"QProgressBar::chunk{{background:{A_TEAL};border-radius:2px;}}")
            status = _lbl("Waiting", 10, False, W_TEXT_DIM, True); status.setFixedWidth(80)
            row.addWidget(lbl); row.addWidget(bar, 1); row.addWidget(status)
            self._results_lay.addLayout(row)
            self._bars[name] = bar; self._bar_lbls[name] = status
        lay.addWidget(_hsep())

        lay.addWidget(_lbl("HÔTES TROUVÉS", 18, True, W_TEXT_DIM))
        self._hosts_lay = QVBoxLayout(); self._hosts_lay.setSpacing(3)
        lay.addLayout(self._hosts_lay, 1)

        btn_row = QHBoxLayout()
        self.btn_scan  = _cd_btn("RUN SCAN",  A_TEAL,    h=32)
        self.btn_close = _cd_btn("Close",     "#707070", h=32)
        self.btn_scan.clicked.connect(self._launch)
        self.btn_close.clicked.connect(self.close)
        btn_row.addWidget(self.btn_scan); btn_row.addStretch(); btn_row.addWidget(self.btn_close)
        lay.addLayout(btn_row)

        self._scanning = False; self._pending = 0

    def _launch(self) -> None:
        if self._scanning: return
        self._scanning = True; self._pending = 3
        self.btn_scan.setEnabled(False); self.btn_scan.setText("Scanning...")
        while self._hosts_lay.count():
            it = self._hosts_lay.takeAt(0)
            if it.widget(): it.widget().deleteLater()
        for bar in self._bars.values(): bar.setValue(0)
        for lbl in self._bar_lbls.values():
            lbl.setText("Scan...")
            lbl.setStyleSheet(f"color:{A_ORANGE};background:transparent;")

        services = [
            (PORT_MOTOR,   "Motors / Wiper", A_TEAL),
            (PORT_PUMP_RX, "Pump",           A_GREEN),
            (PORT_LIN,     "LIN Bus",        "#6A1B9A"),
        ]
        for port, name, color in services:
            def _prog(pct, n=name):
                QTimer.singleShot(0, lambda v=pct, nn=n: self._bars[nn].setValue(v))
            def _done(hosts, n=name, c=color):
                QTimer.singleShot(0, lambda h=hosts, nn=n, cc=c: self._on_done(nn, h, cc))
            scan_async(port, _prog, _done)

    def _on_done(self, name: str, hosts: list[str], color: str) -> None:
        bar_lbl = self._bar_lbls[name]
        if hosts:
            bar_lbl.setText(f"{len(hosts)} found")
            bar_lbl.setStyleSheet(f"color:{A_GREEN};font-weight:bold;background:transparent;")
            for h in hosts:
                card = QFrame()
                card.setStyleSheet(
                    f"QFrame{{background:{W_PANEL2};border:1px solid {W_BORDER};"
                    f"border-left:3px solid {color};border-radius:2px;}}")
                cl = QHBoxLayout(card); cl.setContentsMargins(10, 4, 10, 4); cl.setSpacing(10)
                cl.addWidget(_lbl(h, 11, True, W_TEXT, True))
                cl.addWidget(_lbl(name, 10, False, W_TEXT_DIM))
                cl.addStretch()
                b = _cd_btn("CONNECTER", color, h=24, w=90)
                b.clicked.connect(lambda _, ip=h: self._on_connect(ip))
                cl.addWidget(b); self._hosts_lay.addWidget(card)
        else:
            bar_lbl.setText("Not found")
            bar_lbl.setStyleSheet(f"color:{A_RED};background:transparent;")
        self._pending -= 1
        if self._pending <= 0:
            self._scanning = False
            self.btn_scan.setEnabled(True); self.btn_scan.setText("RESCAN")

    def _on_connect(self, ip: str) -> None:
        QMessageBox.information(
            self, "Connection",
            f"Module {ip} selected.\nAutomatic reconnection started.", "OK")
        self.connected.emit(); self.close()


# ═══════════════════════════════════════════════════════════
#  MAIN WINDOW
# ═══════════════════════════════════════════════════════════
_PORT_NAMES = {
    PORT_MOTOR:   "Motors",
    PORT_LIN:     "LIN",
    PORT_PUMP_RX: "Pump",
    PORT_CAN:     "CAN",
}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._motor_worker = MotorVehicleWorker()
        self._motor_thread = QThread()
        self._motor_worker.moveToThread(self._motor_thread)
        self._motor_thread.started.connect(self._motor_worker.run)

        self._lin_worker  = LINWorker()
        self._lin_thread  = QThread()
        self._lin_worker.moveToThread(self._lin_thread)
        self._lin_thread.started.connect(self._lin_worker.run)

        self._pump_signal = PumpSignal()
        self._pump_client = PumpDataClient(self._pump_signal)

        self._can_worker  = CANWorker()
        self._can_thread  = QThread()
        self._can_worker.moveToThread(self._can_thread)
        self._can_thread.started.connect(self._can_worker.run)

        # Client Redis — connecté au RpiBCM (host résolu après connexion)
        self._rte_client: RTEClient | None = None

        self._build_ui()
        self._connect_signals()

        self._motor_thread.start()
        self._lin_thread.start()
        self._pump_client.start()
        self._can_thread.start()

    # ── Construction UI ──────────────────────────────────────
    def _build_ui(self) -> None:
        self.setWindowTitle("WipeWash  —  HIL Test Bench  |  Wipe & Wash System")
        self.setMinimumSize(1100, 720); self.resize(1440, 900)
        self.setStyleSheet(f"QMainWindow {{ background:{W_BG}; }}")

        self._build_menubar()
        self._build_toolbar()

        # ── Panneaux ─────────────────────────────────────────
        self._motor_panel  = MotorDashPanel()
        self._pump_panel   = PumpPanel(
            lambda: self._pump_client,
            rte_getter=lambda: self._rte_client,
        )
        self._veh_panel    = VehicleRainPanel(lambda: self._motor_worker)
        self._crslin_panel = CRSLINPanel(
            wiper_setter=self._lin_worker.set_wiper_op,
            lin_sender=self._lin_worker.queue_send,
        )
        self._can_panel    = CANBusPanel()

        def _make_runner():
            return TestRunner(
                self._can_worker,
                self._lin_worker,
                self._motor_worker,
                pump_signal=self._pump_signal,
                rte_client=self._rte_client,
            )
        self._auto_test_panel = AutoTestPanel(runner_factory=_make_runner)

        # ── QTabWidget central — 4 pages ─────────────────────
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border:none; background:{W_BG}; }}
            QTabBar {{ background:{W_TOOLBAR}; border-bottom:2px solid {A_TEAL}; }}
            QTabBar::tab {{
                background:{W_TOOLBAR}; color:{W_TEXT_DIM}; border:none;
                border-right:1px solid {W_BORDER};
                padding:8px 26px;
                font-family:{FONT_UI}; font-size:11pt; font-weight:bold;
                min-width:160px;
            }}
            QTabBar::tab:selected {{
                background:{W_BG}; color:{A_TEAL};
                border-top:2px solid {A_TEAL};
            }}
            QTabBar::tab:hover:!selected {{ background:{W_PANEL2}; color:{W_TEXT}; }}
        """)

        # ── Page 1 : Motor / Pompe ────────────────────────────
        pg1 = QWidget(); pg1.setStyleSheet(f"background:{W_BG};")
        l1  = QHBoxLayout(pg1); l1.setContentsMargins(6, 6, 6, 6); l1.setSpacing(6)
        l1.addWidget(self._motor_panel, 1)
        l1.addWidget(self._pump_panel,  1)
        self._tabs.addTab(pg1, "⚙  Motor / Pompe")

        # ── Page 2 : LIN / CRS ───────────────────────────────
        pg2 = QWidget(); pg2.setStyleSheet(f"background:{W_BG};")
        l2  = QVBoxLayout(pg2); l2.setContentsMargins(0, 0, 0, 0); l2.setSpacing(0)
        l2.addWidget(self._crslin_panel)
        self._tabs.addTab(pg2, "📡  LIN / CRS")

        # ── Page 3 : CAN / Vehicle ────────────────────────────
        pg3  = QWidget(); pg3.setStyleSheet(f"background:{W_BG};")
        spl3 = QSplitter(Qt.Orientation.Vertical)
        spl3.setStyleSheet(f"QSplitter::handle{{background:{W_BORDER};height:3px;}}")
        spl3.addWidget(self._can_panel)
        spl3.addWidget(self._veh_panel)
        spl3.setSizes([520, 300])
        l3 = QVBoxLayout(pg3); l3.setContentsMargins(0, 0, 0, 0); l3.addWidget(spl3)
        self._tabs.addTab(pg3, "🚗  CAN / Vehicle")

        # ── Page 4 : Tests Auto ───────────────────────────────
        self._tabs.addTab(self._auto_test_panel, "🧪  Tests Auto")

        # Pause oscilloscopes sur onglets cachés → gain CPU
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._on_tab_changed(0)

        self.setCentralWidget(self._tabs)

        sb = QStatusBar(); sb.setFont(QFont(FONT_MONO, 10))
        sb.setStyleSheet(
            f"background:{W_TOOLBAR};color:{W_TEXT_DIM};border-top:1px solid {W_BORDER};")
        self.setStatusBar(sb); self._qsb = sb

    def _build_menubar(self) -> None:
        mb = self.menuBar()
        mb.setStyleSheet(f"""
            QMenuBar{{background:{W_TOOLBAR};color:{W_TEXT};
                border-bottom:1px solid {W_BORDER};
                font-family:{FONT_UI};font-size:11pt;padding:2px;}}
            QMenuBar::item{{padding:4px 12px;border-radius:2px;}}
            QMenuBar::item:selected{{background:{W_PANEL2};}}
            QMenu{{background:{W_PANEL};color:{W_TEXT};
                border:1px solid {W_BORDER};font-size:11pt;}}
            QMenu::item{{padding:5px 28px;}}
            QMenu::item:selected{{background:{W_PANEL2};color:{A_TEAL2};}}
            QMenu::separator{{height:1px;background:{W_BORDER};margin:3px 8px;}}
        """)
        m_conn = mb.addMenu("Connection")
        act_scan  = QAction("Network Scan...", self); act_scan.setShortcut("Ctrl+Shift+S")
        act_scan.triggered.connect(self._open_scan); m_conn.addAction(act_scan)
        act_recon = QAction("Reconnect all services", self); act_recon.setShortcut("Ctrl+R")
        act_recon.triggered.connect(self._on_rescan); m_conn.addAction(act_recon)
        m_conn.addSeparator()
        act_quit  = QAction("Quit", self); act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close); m_conn.addAction(act_quit)

        m_view = mb.addMenu("View")
        for i, (name, shortcut) in enumerate([
            ("⚙  Motor / Pompe",  "Ctrl+1"),
            ("📡  LIN / CRS",     "Ctrl+2"),
            ("🚗  CAN / Vehicle", "Ctrl+3"),
            ("🧪  Tests Auto",    "Ctrl+4"),
        ]):
            act = QAction(f"  {name}", self); act.setShortcut(shortcut)
            act.triggered.connect(lambda _, idx=i: self._tabs.setCurrentIndex(idx))
            m_view.addAction(act)

        m_help = mb.addMenu("Help")
        act_about = QAction("About...", self)
        act_about.triggered.connect(lambda: QMessageBox.about(
            self, "WipeWash HIL Dashboard",
            "WipeWash Unified Dashboard v5\n\nHIL Test Bench Platform\n"
            "Automotive Wipe & Wash System\n\ndSPACE SCALEXIO compatible"))
        m_help.addAction(act_about)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main Toolbar"); tb.setMovable(False); tb.setFixedHeight(34)
        tb.setStyleSheet(
            f"QToolBar{{background:{W_TOOLBAR};border:none;"
            f"border-bottom:1px solid {W_BORDER};spacing:4px;padding:2px 8px;}}"
            f"QToolButton{{background:transparent;border:none;border-radius:2px;"
            f"color:{W_TEXT};padding:3px 8px;font-family:{FONT_UI};font-size:10pt;}}"
            f"QToolButton:hover{{background:{W_PANEL2};}}")
        self.addToolBar(tb)

        self._toolbar_leds:   dict[int, StatusLed] = {}
        self._toolbar_labels: dict[int, QLabel]    = {}
        for port, name, color in [
            (PORT_MOTOR,   "Motors", A_GREEN),
            (PORT_LIN,     "LIN",    A_TEAL),
            (PORT_PUMP_RX, "Pump",   A_ORANGE),
            (PORT_CAN,     "CAN",    CAN_VEH_C),
        ]:
            led = StatusLed(9); lbl = _lbl(f" {name} ", 10, True, W_TEXT_DIM)
            self._toolbar_leds[port]   = led
            self._toolbar_labels[port] = lbl
            cw = QWidget(); cw.setStyleSheet("background:transparent;")
            cl = QHBoxLayout(cw); cl.setContentsMargins(4, 0, 10, 0); cl.setSpacing(4)
            cl.addWidget(led); cl.addWidget(lbl); tb.addWidget(cw)
            sep = QFrame(); sep.setFrameShape(QFrame.Shape.VLine)
            sep.setStyleSheet(f"background:{W_BORDER};max-width:1px;"); tb.addWidget(sep)

        tb.addSeparator()
        btn_scan = _cd_btn("Scan", A_TEAL, h=26, w=80)
        btn_scan.clicked.connect(self._open_scan); tb.addWidget(btn_scan)

        self._lbl_dt = _lbl("", 10, False, W_TEXT_DIM, True)
        spacer = QWidget(); spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer); tb.addWidget(self._lbl_dt)
        dt_t = QTimer(self); dt_t.timeout.connect(self._upd_dt); dt_t.start(1000)
        self._upd_dt()

    def _on_tab_changed(self, idx: int) -> None:
        """Pause tous les timers d'animation sur les pages cachées → gain CPU majeur.
        Page 0=Motor/Pompe  Page 1=LIN/CRS  Page 2=CAN/Vehicle  Page 3=Tests
        """
        # ── Page 0 : timers MotorWidget + PumpWidget ──────────
        for attr in ("motor_front", "motor_rear"):
            t = getattr(getattr(self._motor_panel, attr, None), "_t", None)
            if t:
                t.start(60) if idx == 0 else t.stop()
        pump_t = getattr(getattr(self._pump_panel, "pump_widget", None), "_t", None)
        if pump_t:
            pump_t.start(60) if idx == 0 else pump_t.stop()

        # ── Page 1 : LIN oscilloscope + WindshieldWidget ──────
        osc_lin = getattr(getattr(self._crslin_panel, "_osc", None), "_t", None)
        if osc_lin:
            osc_lin.start(100) if idx == 1 else osc_lin.stop()
        ws_t = getattr(getattr(self._crslin_panel, "_ws", None), "_t", None)
        if ws_t:
            ws_t.start(20) if idx == 1 else ws_t.stop()

        # ── Page 2 : CAN oscilloscope + CarTopViewWidget ──────
        osc_can = getattr(getattr(self._can_panel, "_osc", None), "_t", None)
        if osc_can:
            osc_can.start(100) if idx == 2 else osc_can.stop()
        car_t = getattr(getattr(self._veh_panel, "car_view", None), "_t", None)
        if car_t:
            car_t.start(60) if idx == 2 else car_t.stop()

    # ── Helpers ──────────────────────────────────────────────
    def _upd_dt(self) -> None:
        self._lbl_dt.setText(datetime.datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))

    def _open_scan(self) -> None:
        dlg = NetworkScanDialog(self)
        dlg.connected.connect(self._on_rescan)
        dlg.exec()

    # ── Connexion des signaux ────────────────────────────────
    def _connect_signals(self) -> None:
        self._motor_worker.motor_received.connect(self._motor_panel.on_motor_data)
        self._motor_worker.motor_received.connect(self._on_motor_data_ws)   # ← BCM→Windshield
        self._motor_worker.status_changed.connect(self._on_motor_status)
        self._motor_worker.wiper_sent.connect(self._on_wiper_sent)
        self._lin_worker.lin_received.connect(self._on_lin_event)
        self._lin_worker.status_changed.connect(self._on_lin_status)
        self._pump_signal.data_received.connect(self._pump_panel.update_display)
        self._pump_signal.connection_ok.connect(self._on_pump_ok)
        self._pump_signal.connection_lost.connect(self._on_pump_lost)
        self._can_worker.can_received.connect(self._can_panel.add_can_event)
        self._can_worker.status_changed.connect(self._on_can_status)
        self._can_panel.ack_needed.connect(
            self._can_worker.send_0x202,
            Qt.ConnectionType.DirectConnection
        )

    def _on_motor_data_ws(self, data: dict) -> None:
        """
        Dispatch données BCM temps réel vers WindshieldWidget + Rest Contact + CRS Fault.
        Appelé à chaque motor_received (~200ms) depuis le TCP broadcast du BCM.
        Complète _on_lin_event : les données moteur arrivent sur port 5000,
        les events LIN sur port 5555 — les deux sources alimentent le windshield.
        """
        motor_on     = data.get("front", "OFF") == "ON"
        rest_raw     = bool(data.get("rest_contact_raw",   False))
        blade_cycles = int(data.get("front_blade_cycles",  0))
        bcm_state    = str(data.get("state",               "OFF"))
        crs_fault    = int(data.get("crs_fault",           0))
        cur_op       = getattr(self._crslin_panel, "_cur_op", 0)

        # WindshieldWidget : état temps réel BCM
        ws = getattr(self._crslin_panel, "_ws", None)
        if ws is not None:
            ws.set_bcm_state(
                front_motor_on   = motor_on,
                rest_contact_raw = rest_raw,
                blade_cycles     = blade_cycles,
                bcm_state        = bcm_state,
                op               = cur_op,
            )

        # Rest Contact panel
        self._crslin_panel.update_rest_contact(rest_raw, blade_cycles)

        # CRS Fault (depuis rte.crs_fault broadcasté par BCM)
        self._crslin_panel.update_crs_fault(crs_fault)

    def _set_tb_status(self, port: int, ok: bool, host: str = "") -> None:
        led = self._toolbar_leds.get(port)
        lbl = self._toolbar_labels.get(port)
        if not led or not lbl: return
        led.set_state(ok, A_GREEN if ok else A_RED)
        n = _PORT_NAMES.get(port, "?")
        if ok:
            lbl.setText(f" {n}  ")
            lbl.setStyleSheet(
                f"color:{A_GREEN};background:transparent;"
                f"font-family:{FONT_UI};font-size:10pt;font-weight:bold;")
        else:
            lbl.setText(f" {n}  ")
            lbl.setStyleSheet(
                f"color:{W_TEXT_DIM};background:transparent;"
                f"font-family:{FONT_UI};font-size:10pt;")

    # ── Slots ─────────────────────────────────────────────────
    def _on_motor_status(self, msg: str, ok: bool) -> None:
        self._set_tb_status(PORT_MOTOR, ok, self._motor_worker.host)
        self._qsb.showMessage(f"[Motors] {msg}")
        # Cree RTEClient Redis des qu'un host BCM est connu
        host = self._motor_worker.host
        if ok and host and self._rte_client is None:
            self._rte_client = RTEClient(host)
            connected = self._rte_client.is_connected()
            if hasattr(self, '_auto_test_panel'):
                self._auto_test_panel.set_redis_status(connected, host)
            if connected:
                self._qsb.showMessage(f"[Redis] Connecte sur {host}:6379")
        elif not ok:
            self._rte_client = None
            if hasattr(self, '_auto_test_panel'):
                self._auto_test_panel.set_redis_status(False)

    def _on_wiper_sent(self, op: int, seq: int) -> None:
        self._crslin_panel.on_wiper_sent(op, seq)

    def _on_lin_event(self, ev: dict) -> None:
        self._crslin_panel.add_lin_event(ev)
        t = ev.get("type", "")

        # ── Trame 0x16 TX (BCM → slave) : wiper_op + état moteur + rest contact ──
        if t == "TX":
            op          = int(ev.get("op",            0))
            bcm_state   = str(ev.get("bcm_state",     "OFF"))
            motor_on    = bool(ev.get("front_motor_on", False))
            rest_raw    = bool(ev.get("rest_contact_raw", False))
            blade_cycles= int(ev.get("front_blade_cycles", 0))

            # Windshield temps réel BCM
            self._crslin_panel._ws.set_bcm_state(
                front_motor_on=motor_on,
                rest_contact_raw=rest_raw,
                blade_cycles=blade_cycles,
                bcm_state=bcm_state,
                op=op,
            )
            # Rest contact panel
            self._crslin_panel.update_rest_contact(rest_raw, blade_cycles)

        # ── Trame 0x17 RX (slave → BCM) : CRS_InternalFault ──────────────────
        elif t == "RX_HDR" and ev.get("pid") == "0x97":
            fault_val = int(ev.get("fault", "0x00"), 16) if isinstance(
                ev.get("fault"), str) else int(ev.get("fault", 0))
            self._crslin_panel.update_crs_fault(fault_val)

        # ── Ack injection fault depuis simulateur ─────────────────────────────
        elif t == "crs_fault_ack":
            try:
                fault_val = int(ev.get("fault", "0x00"), 16)
                self._crslin_panel.update_crs_fault(fault_val)
            except (ValueError, TypeError):
                pass

    def _on_lin_status(self, msg: str, ok: bool) -> None:
        self._set_tb_status(PORT_LIN, ok, self._lin_worker.host)
        self._crslin_panel.set_lin_status(msg, ok)
        self._qsb.showMessage(f"[LIN] {msg}")

    def _on_pump_ok(self, host: str) -> None:
        self._set_tb_status(PORT_PUMP_RX, True, host)
        self._pump_panel.on_connected(host)

    def _on_pump_lost(self) -> None:
        self._set_tb_status(PORT_PUMP_RX, False)
        self._pump_panel.on_disconnected()

    def _on_can_status(self, msg: str, ok: bool) -> None:
        self._set_tb_status(PORT_CAN, ok, self._can_worker.host)
        self._can_panel.set_can_status(msg, ok)
        self._qsb.showMessage(f"[CAN] {msg}")

    def _on_rescan(self) -> None:
        # Moteurs
        self._motor_worker.stop()
        self._motor_thread.quit(); self._motor_thread.wait(2000)
        self._motor_worker = MotorVehicleWorker()
        self._motor_thread = QThread()
        self._motor_worker.moveToThread(self._motor_thread)
        self._motor_thread.started.connect(self._motor_worker.run)
        self._motor_worker.motor_received.connect(self._motor_panel.on_motor_data)
        self._motor_worker.motor_received.connect(self._on_motor_data_ws)   # ← BCM→Windshield
        self._motor_worker.status_changed.connect(self._on_motor_status)
        self._motor_worker.wiper_sent.connect(self._on_wiper_sent)
        self._veh_panel._getter          = lambda: self._motor_worker
        self._crslin_panel._wiper_setter = self._lin_worker.set_wiper_op
        self._motor_thread.start()
        # LIN
        self._lin_worker.stop()
        self._lin_thread.quit(); self._lin_thread.wait(2000)
        self._lin_worker = LINWorker()
        self._lin_thread = QThread()
        self._lin_worker.moveToThread(self._lin_thread)
        self._lin_thread.started.connect(self._lin_worker.run)
        self._lin_worker.lin_received.connect(self._on_lin_event)
        self._lin_worker.status_changed.connect(self._on_lin_status)
        self._crslin_panel._lin_sender = self._lin_worker.queue_send   # ← CRS fault injection
        self._lin_thread.start()
        # CAN
        self._can_worker.stop()
        self._can_thread.quit(); self._can_thread.wait(2000)
        self._can_worker = CANWorker()
        self._can_thread = QThread()
        self._can_worker.moveToThread(self._can_thread)
        self._can_thread.started.connect(self._can_worker.run)
        self._can_worker.can_received.connect(self._can_panel.add_can_event)
        self._can_worker.status_changed.connect(self._on_can_status)
        self._can_panel.ack_needed.connect(
            self._can_worker.send_0x202,
            Qt.ConnectionType.DirectConnection
        )
        self._can_thread.start()
        # Pompe — PumpDataClient se reconnecte automatiquement

    def closeEvent(self, e) -> None:
        self._motor_worker.stop(); self._motor_thread.quit(); self._motor_thread.wait(2000)
        self._lin_worker.stop();   self._lin_thread.quit();   self._lin_thread.wait(2000)
        self._can_worker.stop();   self._can_thread.quit();   self._can_thread.wait(2000)
        e.accept()