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

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QFrame, QLabel, QToolBar, QStatusBar,
    QTabWidget, QSplitter, QMenu, QDialog, QMessageBox,
    QProgressBar, QSizePolicy, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QPushButton,
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui  import QFont, QColor, QAction, QPixmap, QIcon
from data_replay_panel import DataRecorder, DataReplayPanel

from constants import (
    FONT_UI, FONT_MONO,
    W_BG, W_PANEL, W_PANEL2, W_PANEL3,
    W_BORDER, W_TOOLBAR,
    W_TEXT, W_TEXT_DIM, W_DOCK_HDR,
    A_TEAL, A_TEAL2, A_GREEN, A_GREEN_BG, A_RED, A_ORANGE,
    KPIT_GREEN,
    PORT_MOTOR, PORT_LIN, PORT_PUMP_RX,
)
try:
    from constants import PORT_CAN, CAN_VEH_C
except ImportError:
    PORT_CAN  = 5557
    CAN_VEH_C = "#007ACC"
from network  import scan_async, scan_multi_ports_async
from workers  import (
    MotorVehicleWorker, LINWorker, PumpSignal, PumpDataClient, CANWorker,
)
from widgets_base import StatusLed, _lbl, _hsep, _cd_btn
from panels import (
    MotorDashPanel, PumpPanel, VehicleRainPanel, CRSLINPanel, CANBusPanel,
)
from fault_injection_panel import FaultInjectionPanel
from auto_test_panel  import AutoTestPanel
from test_runner      import TestRunner
from test_params_panel import TestParamsPanel  # kept for compatibility but tab removed
from xcp_panel         import XCPPanel
from car_html_widget  import CarHTMLWidget, CarXRayWidget
from rte_client       import RTEClient
from sim_client       import SimClient
# datasave_panel / scenario_replay_panel merged → data_replay_panel
from PySide6.QtCore    import QThread
from free_layout      import MotorPumpFreePage, SignalHub


# ═══════════════════════════════════════════════════════════
#  SCAN DIALOG  — Design professionnel monochrome
# ═══════════════════════════════════════════════════════════

# Palette scan — utilise les constantes existantes de constants.py
_SC_BG        = "#0D1117"   # fond dialog
_SC_PANEL     = "#161B22"   # panneau / carte
_SC_BORDER    = "#30363D"   # bordures
_SC_TEXT      = "#E6EDF3"   # texte principal
_SC_TEXT_DIM  = "#8B949E"   # texte secondaire
_SC_ACCENT    = KPIT_GREEN  # vert KPIT (#8DC63F) — boutons connect
_SC_ACCENT_DK = A_TEAL2     # bleu-vert foncé hover (#005F9E)
_SC_NEUTRAL   = "#21262D"   # bouton neutre
_SC_HEADER    = "#010409"   # entête tableau
_SC_ROW_ALT   = "#0D1117"   # ligne alternée tableau
_SC_SEL       = A_TEAL      # bleu KPIT sélection (#007ACC)
_SC_CONN      = KPIT_GREEN  # connecté — vert KPIT (#8DC63F)
_SC_DISCONN   = "#8B949E"   # non connecté


class DiscoveryDialog(QDialog):
    """
    Discovery Dialog — scan réseau multi-port.
    Affiche les hôtes et leurs ports dans un tableau professionnel.
    Design monochrome sans multicouleurs.

    Signaux émis après connexion :
      host_bcm_selected(ip)   → RPiBCM  (Motors/Wiper port 5000)
      host_sim_selected(ip)   → RPiSIM  (Vehicle/CAN port 5000 TX)
      host_lin_selected(ip)   → LIN bus (port 5555)
      host_pump_selected(ip)  → Pompe   (port 5556)
      host_can_selected(ip)   → CAN bus (port 5557)
    """
    host_bcm_selected  = Signal(str)
    host_sim_selected  = Signal(str)
    host_lin_selected  = Signal(str)
    host_pump_selected = Signal(str)
    host_can_selected  = Signal(str)
    connected          = Signal()
    _scan_done         = Signal(object)

    # Libellé/tag par port (monochrome : plus de couleur par port)
    _PORT_META = {
        PORT_MOTOR:   ("Motors / Wiper", "BCM"),
        5000:         ("Sim Vehicle",    "SIM"),
        PORT_LIN:     ("LIN Bus",        "LIN"),
        PORT_PUMP_RX: ("Pump",           "PUMP"),
        PORT_CAN:     ("CAN Bus",        "CAN"),
    }

    _TABLE_HEADERS = ["IP Address", "Port", "Service", "Role", "Status", "Action"]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Network Discovery — WipeWash BCM")
        self.setMinimumSize(780, 520)
        self.setStyleSheet(f"""
            QDialog {{
                background: {_SC_BG};
                color: {_SC_TEXT};
                font-family: {FONT_UI};
            }}
            QLabel {{ background: transparent; }}
            QScrollBar:vertical {{
                background: {_SC_PANEL};
                width: 8px; border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: {_SC_BORDER};
                border-radius: 4px; min-height: 30px;
            }}
        """)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)

        # ── Titre ──────────────────────────────────────────────
        title_row = QHBoxLayout()
        title_lbl = QLabel("NETWORK DISCOVERY")
        title_lbl.setStyleSheet(
            f"font-size:16pt;font-weight:700;color:{_SC_TEXT};"
            f"letter-spacing:2px;")
        subtitle = QLabel("Multi-port scan  ·  10.20.0.0/28 + 10.20.0.16/28")
        subtitle.setStyleSheet(f"font-size:9pt;color:{_SC_TEXT_DIM};")
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        title_row.addWidget(subtitle)
        lay.addLayout(title_row)

        # Séparateur
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{_SC_BORDER};background:{_SC_BORDER};max-height:1px;")
        lay.addWidget(sep)

        # ── Barre de progression ───────────────────────────────
        prog_row = QHBoxLayout(); prog_row.setSpacing(10)
        self._prog_bar = QProgressBar()
        self._prog_bar.setRange(0, 100); self._prog_bar.setValue(0)
        self._prog_bar.setFixedHeight(6); self._prog_bar.setTextVisible(False)
        self._prog_bar.setStyleSheet(f"""
            QProgressBar {{
                background: {_SC_PANEL};
                border: 1px solid {_SC_BORDER};
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background: {_SC_ACCENT};
                border-radius: 3px;
            }}
        """)
        self._prog_lbl = QLabel("Ready")
        self._prog_lbl.setFixedWidth(120)
        self._prog_lbl.setStyleSheet(f"font-size:9pt;color:{_SC_TEXT_DIM};font-family:{FONT_MONO};")
        prog_row.addWidget(self._prog_bar, 1)
        prog_row.addWidget(self._prog_lbl)
        lay.addLayout(prog_row)

        # ── Tableau des résultats ──────────────────────────────
        tbl_hdr_lbl = QLabel("DISCOVERED HOSTS")
        tbl_hdr_lbl.setStyleSheet(
            f"font-size:9pt;font-weight:600;color:{_SC_TEXT_DIM};"
            f"letter-spacing:1px;padding-bottom:2px;")
        lay.addWidget(tbl_hdr_lbl)

        self._table = QTableWidget(0, len(self._TABLE_HEADERS))
        self._table.setHorizontalHeaderLabels(self._TABLE_HEADERS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # IP
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # Port
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)            # Service
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # Role
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)  # Status
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)              # Action
        hh.resizeSection(5, 140)  # largeur fixe pour afficher le bouton complet

        self._table.setStyleSheet(f"""
            QTableWidget {{
                background: {_SC_PANEL};
                alternate-background-color: {_SC_ROW_ALT};
                color: {_SC_TEXT};
                border: 1px solid {_SC_BORDER};
                border-radius: 6px;
                gridline-color: transparent;
                font-size: 10pt;
                outline: 0;
            }}
            QTableWidget::item {{
                padding: 6px 10px;
                border: none;
            }}
            QTableWidget::item:selected {{
                background: rgba(31,111,235,0.18);
                color: {_SC_TEXT};
            }}
            QHeaderView::section {{
                background: {_SC_HEADER};
                color: {_SC_TEXT_DIM};
                font-size: 8pt;
                font-weight: 600;
                letter-spacing: 1px;
                padding: 6px 10px;
                border: none;
                border-bottom: 1px solid {_SC_BORDER};
                text-transform: uppercase;
            }}
        """)
        lay.addWidget(self._table, 1)

        # ── Boutons ───────────────────────────────────────────
        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color:{_SC_BORDER};background:{_SC_BORDER};max-height:1px;")
        lay.addWidget(sep2)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)

        _btn_style = lambda bg, hover: (
            f"QPushButton {{background:{bg};color:{_SC_TEXT};"
            f"border:1px solid {_SC_BORDER};border-radius:5px;"
            f"padding:6px 20px;font-size:10pt;font-weight:600;"
            f"font-family:{FONT_UI};}}"
            f"QPushButton:hover {{background:{hover};}}"
            f"QPushButton:disabled {{background:{_SC_NEUTRAL};color:{_SC_TEXT_DIM};}}"
        )

        self.btn_scan  = _cd_btn("▶  RUN SCAN",  _SC_ACCENT, h=34)
        self.btn_scan.setStyleSheet(_btn_style(_SC_ACCENT, _SC_ACCENT_DK))

        self.btn_connect_all = _cd_btn("Connect All", _SC_NEUTRAL, h=34)
        self.btn_connect_all.setStyleSheet(
            f"QPushButton{{background:transparent;color:{_SC_ACCENT};"
            f"border:1px solid {_SC_ACCENT};border-radius:5px;"
            f"padding:6px 20px;font-size:10pt;font-weight:600;font-family:{FONT_UI};}}"
            f"QPushButton:hover{{background:{_SC_ACCENT};color:#0D1117;}}"
            f"QPushButton:pressed{{background:{_SC_ACCENT_DK};color:#FFFFFF;}}"
            f"QPushButton:disabled{{background:{_SC_NEUTRAL};color:{_SC_TEXT_DIM};border-color:{_SC_BORDER};}}"
        )
        self.btn_connect_all.setEnabled(False)

        self.btn_close = _cd_btn("Close", _SC_NEUTRAL, h=34)
        self.btn_close.setStyleSheet(_btn_style(_SC_NEUTRAL, _SC_BORDER))

        self.btn_scan.clicked.connect(self._launch)
        self.btn_connect_all.clicked.connect(self._connect_all)
        self.btn_close.clicked.connect(self.close)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"font-size:9pt;color:{_SC_TEXT_DIM};font-family:{FONT_MONO};")

        btn_row.addWidget(self.btn_scan)
        btn_row.addWidget(self.btn_connect_all)
        btn_row.addWidget(self._status_lbl)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_close)
        lay.addLayout(btn_row)

        self._scanning = False
        self._connected_ports: dict[tuple, str] = {}
        self._scan_done.connect(self._on_scan_done)

    # ── Lancement du scan ──────────────────────────────────────
    def _launch(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        self.btn_scan.setEnabled(False)
        self.btn_scan.setText("Scanning…")
        self.btn_connect_all.setEnabled(False)
        self._prog_bar.setValue(0)
        self._prog_lbl.setText("Scanning…")
        self._status_lbl.setText("")
        self._table.setRowCount(0)

        ports = [PORT_MOTOR, PORT_LIN, PORT_PUMP_RX, PORT_CAN]

        def _on_done(results: dict):
            self._scan_done.emit(results)

        scan_multi_ports_async(ports, _on_done)

        self._anim_val = 0
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._anim_step)
        self._anim_timer.start(40)

    def _anim_step(self) -> None:
        self._anim_val = (self._anim_val + 3) % 101
        self._prog_bar.setValue(self._anim_val)

    # ── Résultats du scan ──────────────────────────────────────
    def _on_scan_done(self, results: dict) -> None:
        """results = {ip: {"ports": [...], "role_5000": "BCM"|"SIM"|"UNKNOWN"|None}}"""
        self._anim_timer.stop()
        self._prog_bar.setValue(100)
        self._scanning = False
        self.btn_scan.setEnabled(True)
        self.btn_scan.setText("↺  RESCAN")

        self._table.setRowCount(0)

        if not results:
            self._prog_lbl.setText("0 hosts")
            self._status_lbl.setText("No hosts found on the network.")
            self.btn_connect_all.setEnabled(False)
            return

        n = len(results)
        self._prog_lbl.setText(f"{n} host{'s' if n > 1 else ''} found")

        row_idx = 0
        for ip in sorted(results.keys()):
            info = results[ip]
            if isinstance(info, dict):
                open_ports = info.get("ports", [])
                role_5000  = info.get("role_5000")
            else:
                open_ports = info
                role_5000  = None

            for port in sorted(open_ports):
                self._insert_table_row(row_idx, ip, port, role_5000)
                row_idx += 1

        for r in range(self._table.rowCount()):
            self._table.setRowHeight(r, 46)
        self.btn_connect_all.setEnabled(True)

    def _insert_table_row(self, row_idx: int, ip: str, port: int, role_5000) -> None:
        """Insère une ligne dans le tableau pour un couple IP/port."""
        self._table.insertRow(row_idx)

        # Détermination service / tag
        if port == 5000:
            if role_5000 == "BCM":
                service, tag = "Motors / Wiper", "BCM"
            elif role_5000 == "SIM":
                service, tag = "Sim Vehicle", "SIM"
            else:
                service, tag = "Unknown (5000)", "BCM?"
        else:
            meta = self._PORT_META.get(port)
            if meta:
                service, tag = meta
            else:
                service, tag = f"Port {port}", str(port)

        role_str = role_5000 if role_5000 and port == 5000 else "—"

        # Statut connexion
        conn_key = (port, tag.rstrip("?"))
        already = self._connected_ports.get(conn_key) == ip

        # Helpers pour items centrés
        def _item(text, mono=False, align=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft):
            it = QTableWidgetItem(text)
            it.setTextAlignment(align)
            if mono:
                f = QFont(FONT_MONO, 10)
                it.setFont(f)
            return it

        center = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter

        # Col 0 — IP
        ip_item = _item(ip, mono=True)
        ip_item.setForeground(QColor(_SC_TEXT))
        self._table.setItem(row_idx, 0, ip_item)

        # Col 1 — Port
        port_item = _item(str(port), mono=True, align=center)
        port_item.setForeground(QColor(_SC_TEXT_DIM))
        self._table.setItem(row_idx, 1, port_item)

        # Col 2 — Service
        svc_item = _item(service)
        svc_item.setForeground(QColor(_SC_TEXT))
        self._table.setItem(row_idx, 2, svc_item)

        # Col 3 — Role
        role_item = _item(role_str, align=center)
        role_item.setForeground(QColor(_SC_TEXT_DIM))
        self._table.setItem(row_idx, 3, role_item)

        # Col 4 — Status
        status_text = "● Connected" if already else "○ Available"
        status_item = _item(status_text, align=center)
        status_item.setForeground(QColor(_SC_CONN if already else _SC_DISCONN))
        self._table.setItem(row_idx, 4, status_item)

        # Col 5 — Bouton CONNECT via setCellWidget
        btn_text = f"✓ {tag}" if already else "Connect"
        btn = QPushButton(btn_text)
        btn.setEnabled(not already)
        btn.setFixedHeight(26)
        btn.setFixedWidth(90)
        if already:
            btn.setStyleSheet(
                f"QPushButton{{background:{A_GREEN_BG};color:{_SC_CONN};"
                f"border:none;border-radius:3px;"
                f"font-size:9pt;font-weight:600;padding:2px 12px;}}"
            )
            container_style = (
                f"background:{A_GREEN_BG};border:1px solid {_SC_CONN};"
                f"border-radius:4px;"
            )
        else:
            btn.setStyleSheet(
                f"QPushButton{{background:transparent;color:{_SC_ACCENT};"
                f"border:none;"
                f"font-size:9pt;font-weight:600;padding:2px 12px;}}"
                f"QPushButton:hover{{background:{_SC_ACCENT};color:#0D1117;}}"
                f"QPushButton:pressed{{background:{_SC_ACCENT_DK};color:#FFFFFF;}}"
            )
            container_style = (
                f"background:transparent;border:1px solid {_SC_ACCENT};"
                f"border-radius:4px;"
            )
        btn.clicked.connect(
            lambda _, i=ip, p=port, t=tag, r=row_idx: self._on_port_connect(i, p, t, r)
        )

        container = QWidget()
        container.setStyleSheet(container_style)
        cl = QHBoxLayout(container)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(btn)

        wrapper = QWidget()
        wrapper.setStyleSheet("background:transparent;border:none;")
        wl = QHBoxLayout(wrapper)
        wl.setContentsMargins(6, 6, 6, 6)
        wl.addStretch()
        wl.addWidget(container)
        wl.addStretch()
        self._table.setCellWidget(row_idx, 5, wrapper)
        self._table.setRowHeight(row_idx, 46)

    # ── Connexion d'un port ────────────────────────────────────
    def _on_port_connect(self, ip: str, port: int, tag: str, row: int) -> None:
        conn_key = (port, tag.rstrip("?"))
        self._connected_ports[conn_key] = ip

        # Mettre à jour le statut dans le tableau
        status_item = QTableWidgetItem("● Connected")
        status_item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter)
        status_item.setForeground(QColor(_SC_CONN))
        self._table.setItem(row, 4, status_item)

        # Mettre à jour le bouton
        container = QWidget()
        container.setStyleSheet(
            f"background:{A_GREEN_BG};border:1px solid {_SC_CONN};border-radius:4px;"
        )
        cl = QHBoxLayout(container)
        cl.setContentsMargins(0, 0, 0, 0)
        btn = QPushButton(f"✓ {tag}")
        btn.setEnabled(False)
        btn.setFixedHeight(26)
        btn.setFixedWidth(90)
        btn.setStyleSheet(
            f"QPushButton{{background:{A_GREEN_BG};color:{_SC_CONN};"
            f"border:none;border-radius:3px;"
            f"font-size:9pt;font-weight:600;padding:2px 12px;}}"
        )
        cl.addWidget(btn)

        wrapper = QWidget()
        wrapper.setStyleSheet("background:transparent;border:none;")
        wl = QHBoxLayout(wrapper)
        wl.setContentsMargins(6, 6, 6, 6)
        wl.addStretch()
        wl.addWidget(container)
        wl.addStretch()
        self._table.setCellWidget(row, 5, wrapper)
        self._table.setRowHeight(row, 46)

        # Émettre le signal correspondant
        clean = tag.rstrip("?")
        if clean == "BCM":
            self.host_bcm_selected.emit(ip)
        elif clean == "SIM":
            self.host_sim_selected.emit(ip)
        elif clean == "LIN":
            self.host_lin_selected.emit(ip)
        elif clean == "PUMP":
            self.host_pump_selected.emit(ip)
        elif clean == "CAN":
            self.host_can_selected.emit(ip)

        self.connected.emit()

    # ── Connexion de tous les hôtes disponibles ────────────────
    def _connect_all(self) -> None:
        """Connecte tous les hôtes disponibles (non encore connectés)."""
        rows = self._table.rowCount()
        connected_count = 0
        for row in range(rows):
            container = self._table.cellWidget(row, 5)
            if container is None:
                continue
            btn = container.findChild(QPushButton)
            if btn is not None and btn.isEnabled():
                btn.click()
                connected_count += 1
        if connected_count == 0:
            self._status_lbl.setText("All hosts already connected.")
        else:
            self._status_lbl.setText(f"{connected_count} host(s) connected.")
        self.btn_connect_all.setEnabled(False)


# Alias pour rétro-compatibilité
NetworkScanDialog = DiscoveryDialog


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
        # Client TCP — connecté au RPi Simulateur (injection de défauts GPIO)
        self._sim_client: SimClient = SimClient()

        # DataSave — enregistrement & export CSV
        self._data_recorder = DataRecorder()

        # SignalHub — pont vers les instruments drag&drop de la page Motor/Pump
        self._signal_hub = SignalHub(self)

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
        self.setStyleSheet(f"QMainWindow {{ background:#08080C; }}")

        # ── Icône fenêtre / barre des tâches ─────────────────────────────────
        import os as _os_icon
        _icon_path = _os_icon.path.join(_os_icon.path.dirname(_os_icon.path.abspath(__file__)), "app_icon.png")
        if _os_icon.path.exists(_icon_path):
            self.setWindowIcon(QIcon(_icon_path))


        self._build_menubar()
        self._build_toolbar()

        # ── Panneaux ─────────────────────────────────────────
        self._motor_panel  = MotorDashPanel()
        self._pump_panel   = PumpPanel(
            lambda: self._pump_client,
            rte_getter=lambda: self._rte_client,
            sim_getter=lambda: self._sim_client,
        )
        self._veh_panel    = VehicleRainPanel(lambda: self._motor_worker)
        self._veh_panel.hide()
        self._veh_panel.setStyleSheet("background:#08080C;")
        self._crslin_panel = CRSLINPanel(
            wiper_setter=self._lin_worker.set_wiper_op,
            lin_sender=self._lin_worker.queue_send,
        )
        self._can_panel    = CANBusPanel(sim_getter=lambda: self._sim_client)

        # ── Fault Injection Panel (standalone — pompe directe + défauts H-Bridge) ──
        self._fi_panel = FaultInjectionPanel(
            pump_data_signal=self._pump_signal,
            rte_getter=lambda: self._rte_client,
            sim_getter=lambda: self._sim_client,
        )

        def _make_runner():
            return TestRunner(
                self._can_worker,
                self._lin_worker,
                self._motor_worker,
                pump_signal=self._pump_signal,
                rte_client=self._rte_client,
                sim_client=self._sim_client,
                can_panel=self._can_panel,
            )
        self._auto_test_panel = AutoTestPanel(runner_factory=_make_runner)

        # ── QTabWidget central — 4 pages ─────────────────────
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.tabBar().setExpanding(True)
        self._tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border:none; background:#08080C; }}
            QTabBar {{ background:#1A1A2E; border-bottom:2px solid {A_TEAL}; }}
            QTabBar::tab {{
                background:#1A1A2E; color:#FFFFFF; border:none;
                border-right:1px solid #2A2A3E;
                padding:8px 0px;
                font-family:{FONT_UI}; font-size:10pt; font-weight:bold;
            }}
            QTabBar::tab:selected {{
                background:#0D1117; color:{A_TEAL};
                border-top:2px solid {A_TEAL};
            }}
            QTabBar::tab:hover:!selected {{ background:#252535; color:#FFFFFF; }}
        """)

        # ── CarHTMLWidget — voiture BMW 3D centrale (LIN/CRS/Vehicle tab) ──────────
        self._car_html = CarHTMLWidget()
        self._car_html.setMinimumWidth(380)

        # ── CarXRayWidget — controldesk-xray.html pour l'onglet Motor / Pump ──────
        self._car_html_mp = CarXRayWidget()
        self._car_html_mp.setMinimumWidth(360)

        # Brancher la voiture HTML sur les 3 panneaux qui la contrôlent
        self._crslin_panel.set_car_widget(self._car_html)
        self._veh_panel.set_car_widget(self._car_html)
        self._can_panel.set_car_widget(self._car_html)
        # Enregistrer la voiture de l'onglet Motor/Pump pour qu'elle reçoive
        # les mêmes mises à jour (ignition, speed, rain, reverse) que la voiture principale
        self._veh_panel.add_car_widget(self._car_html_mp)

        if hasattr(self._veh_panel, "set_pump_panel"):
            self._veh_panel.set_pump_panel(self._pump_panel)

        # ── Page 1 : Motor / Pompe — disposition libre ControlDesk-style ──
        self._motor_pump_page = MotorPumpFreePage(
            motor_panel = self._motor_panel,
            car_xray    = self._car_html_mp,
            pump_panel  = self._pump_panel,
            signal_hub  = self._signal_hub,
            veh_panel   = self._veh_panel,
        )
        self._tabs.addTab(self._motor_pump_page, "HardwareDesk")



        # ── Page 2 : LIN / CRS / Vehicle — voiture HTML au centre ──────────
        pg2 = QWidget()
        pg2.setStyleSheet(f"background:{W_TOOLBAR};")
        l2  = QHBoxLayout(pg2); l2.setContentsMargins(0, 0, 0, 0); l2.setSpacing(0)

        # Splitter horizontal 3 colonnes
        spl2 = QSplitter(Qt.Orientation.Horizontal)
        spl2.setStyleSheet(f"QSplitter::handle{{background:{W_BORDER};width:3px;}}")

        # Colonne gauche : CRS / LIN panel (tabs: CRS Control, LIN Signal, LIN Table)
        self._crslin_panel.setStyleSheet(f"background:{W_BG};")
        spl2.addWidget(self._crslin_panel)

        # Colonne centrale : voiture HTML BMW + contrôles sous la voiture
        car_wrapper = QWidget(); car_wrapper.setStyleSheet(f"background:{W_BG};")
        car_lay = QVBoxLayout(car_wrapper)
        car_lay.setContentsMargins(4, 4, 4, 4); car_lay.setSpacing(6)
        car_lay.addWidget(self._car_html, 1)

        spl2.addWidget(car_wrapper)

        # Colonne droite : CAN Bus seulement (VehicleRain déplacé sous la voiture)
        spl2.addWidget(self._can_panel)

        # Proportions : LIN/CRS 28% | Car 50% | CAN 22%
        spl2.setSizes([300, 600, 330])

        l2.addWidget(spl2)
        self._tabs.addTab(pg2, "BusDesk")

        # ── Page 4 : Tests Auto ───────────────────────────────
        self._tabs.addTab(self._auto_test_panel, "AutomationDesk")



        # ── Page 4c : XCP Calibration ────────────────────────
        self._xcp_panel = XCPPanel()
        self._tabs.addTab(self._xcp_panel, "XCPDesk")

        # ── Page 5 : Fault Injection ──────────────────────────
        self._tabs.addTab(self._fi_panel, "FaultDesk")

        # ── Page 6 : Data / Replay (fusionné) ────────────────
        self._data_replay_panel = DataReplayPanel(
            recorder     = self._data_recorder,
            can_worker   = self._can_worker,
            lin_worker   = self._lin_worker,
            motor_worker = self._motor_worker,
            rte_client   = getattr(self, '_rte_client', None),
        )
        self._data_replay_panel.connect_panels(
            motor_panel  = self._motor_panel,
            pump_panel   = self._pump_panel,
            veh_panel    = self._veh_panel,
            crslin_panel = self._crslin_panel,
            can_panel    = self._can_panel,
            car_html     = self._car_html,
            car_html_mp  = self._car_html_mp,
            main_window  = self,
        )
        # Alias de compatibilité (accès depuis d'autres modules)
        self._datasave_panel  = self._data_replay_panel
        self._scenario_panel  = self._data_replay_panel
        self._tabs.addTab(self._data_replay_panel, "DataDesk")

        # Pause oscilloscopes sur onglets cachés → gain CPU
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._on_tab_changed(0)

        self.setCentralWidget(self._tabs)

        sb = QStatusBar(); sb.setFont(QFont(FONT_MONO, 10))
        sb.setStyleSheet(
            f"background:#000000;color:{W_TEXT_DIM};border-top:1px solid {W_BORDER};")
        self.setStatusBar(sb); self._qsb = sb

        # ── Widget trigger permanent (visible depuis tous les onglets) ─────────
        self._sb_trigger = QLabel("  ◉ TRIGGER: inactive  ")
        self._sb_trigger.setFont(QFont(FONT_MONO, 9, QFont.Weight.Bold))
        self._sb_trigger.setStyleSheet(
            f"color:{W_TEXT_DIM};background:transparent;padding:0 8px;"
            f"font-family:{FONT_MONO};font-size:9pt;")
        self._sb_trig_blink_state = False
        self._sb_trig_blink = QTimer(self)
        self._sb_trig_blink.setInterval(400)
        self._sb_trig_blink.timeout.connect(self._on_sb_trig_blink)
        self._qsb.addPermanentWidget(self._sb_trigger)


    def _build_menubar(self) -> None:
        mb = self.menuBar()
        mb.setStyleSheet(f"""
            QMenuBar{{
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #161616, stop:1 #0D0D0D);
                color:#CCCCCC;
                border-bottom:2px solid {KPIT_GREEN};
                font-family:{FONT_UI};font-size:11pt;padding:3px 4px;
            }}
            QMenuBar::item{{
                padding:5px 14px;border-radius:3px;color:#CCCCCC;
                font-weight:500;
            }}
            QMenuBar::item:selected{{
                background:rgba(141,198,63,0.18);color:{KPIT_GREEN};
            }}
            QMenuBar::item:pressed{{
                background:rgba(141,198,63,0.28);color:{KPIT_GREEN};
            }}
            QMenu{{
                background:#161B22;color:#E6EDF3;
                border:1px solid rgba(141,198,63,0.5);
                border-radius:6px;
                padding:4px 0;
                font-size:10pt;
                font-family:{FONT_UI};
            }}
            QMenu::item{{
                padding:7px 32px 7px 20px;color:#E6EDF3;
                border-radius:3px;margin:1px 4px;
            }}
            QMenu::item:selected{{
                background:rgba(141,198,63,0.18);color:{KPIT_GREEN};
            }}
            QMenu::item:disabled{{color:#555;}}
            QMenu::separator{{
                height:1px;background:rgba(141,198,63,0.25);
                margin:4px 8px;
            }}
        """)

        # CONNECTION menu
        m_conn = mb.addMenu("  Connection  ")
        act_scan = QAction("Network Discovery Scan…", self)
        act_scan.setShortcut("Ctrl+Shift+S")
        act_scan.setStatusTip("Scan the local network to discover BCM / Simulator nodes")
        act_scan.triggered.connect(self._open_scan)
        m_conn.addAction(act_scan)
        act_recon = QAction("Reconnect All Services", self)
        act_recon.setShortcut("Ctrl+R")
        act_recon.setStatusTip("Reset and reconnect all TCP/UDP workers")
        act_recon.triggered.connect(self._on_rescan)
        m_conn.addAction(act_recon)
        m_conn.addSeparator()
        act_quit = QAction("Exit", self)
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)
        m_conn.addAction(act_quit)

        # VIEW menu
        m_view = mb.addMenu("  View  ")
        for i, (name, shortcut) in enumerate([
            ("HardwareDesk   — Motor & Pump",     "Ctrl+1"),
            ("BusDesk        — LIN / CAN / CRS",  "Ctrl+2"),
            ("AutomationDesk — Test Suite",        "Ctrl+3"),
            ("XCPDesk        — Calibration",       "Ctrl+4"),
            ("FaultDesk      — Fault Injection",   "Ctrl+5"),
            ("DataDesk       — Record & Replay",   "Ctrl+6"),
        ]):
            act = QAction(f"  {name}", self)
            act.setShortcut(shortcut)
            act.triggered.connect(lambda _, idx=i: self._tabs.setCurrentIndex(idx))
            m_view.addAction(act)
        m_view.addSeparator()
        act_fs = QAction("  Toggle Fullscreen", self)
        act_fs.setShortcut("F11")
        act_fs.triggered.connect(
            lambda: self.showFullScreen() if not self.isFullScreen() else self.showNormal())
        m_view.addAction(act_fs)

        # TOOLS menu
        m_tools = mb.addMenu("  Tools  ")
        act_bus_cfg = QAction("Bus Config — LDF / DBC Editor…", self)
        act_bus_cfg.setShortcut("Ctrl+B")
        act_bus_cfg.setStatusTip("Open the LDF / DBC bus configuration editor")
        act_bus_cfg.triggered.connect(self._open_bus_config)
        m_tools.addAction(act_bus_cfg)
        m_tools.addSeparator()
        act_bus_info = QAction("Show Active Bus Configuration", self)
        act_bus_info.setStatusTip("Display a summary of the currently loaded LDF and DBC files")
        act_bus_info.triggered.connect(self._show_bus_info)
        m_tools.addAction(act_bus_info)

        # HELP menu
        m_help = mb.addMenu("  Help  ")
        act_about = QAction("About WipeWash…", self)
        act_about.setShortcut("F1")
        act_about.triggered.connect(self._open_about_dialog)
        m_help.addAction(act_about)
        act_shortcuts = QAction("Keyboard Shortcuts", self)
        act_shortcuts.triggered.connect(self._open_shortcuts_dialog)
        m_help.addAction(act_shortcuts)

    def _open_about_dialog(self) -> None:
        """Professional About dialog with KPIT logo."""
        import os as _os
        dlg = QDialog(self)
        dlg.setWindowTitle("About WipeWash HIL Platform")
        dlg.setFixedSize(520, 400)
        dlg.setStyleSheet(f"""
            QDialog {{
                background: #0D1117;
                color: #E6EDF3;
                font-family: {FONT_UI};
            }}
            QLabel {{ background: transparent; color: #E6EDF3; }}
        """)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Header with logo
        hdr = QFrame()
        hdr.setFixedHeight(110)
        hdr.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 #0F1A0A, stop:1 #1A2E10);"
            f"border-bottom: 2px solid {KPIT_GREEN};")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(28, 10, 28, 10)
        _logo_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "kpit_logo.png")
        logo_lbl = QLabel()
        logo_lbl.setFixedSize(140, 70)
        logo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _pm = QPixmap(_logo_path) if _os.path.exists(_logo_path) else QPixmap()
        if not _pm.isNull():
            logo_lbl.setPixmap(_pm.scaledToHeight(64, Qt.TransformationMode.SmoothTransformation))
        hdr_lay.addWidget(logo_lbl)
        vline = QFrame(); vline.setFrameShape(QFrame.Shape.VLine)
        vline.setStyleSheet(f"background:{KPIT_GREEN};max-width:1px;margin:12px 20px;")
        hdr_lay.addWidget(vline)
        title_col = QVBoxLayout(); title_col.setSpacing(4)
        t1 = QLabel("WipeWash HIL Platform")
        t1.setStyleSheet(f"font-size:16pt;font-weight:700;color:{KPIT_GREEN};letter-spacing:1px;")
        t2 = QLabel("Hardware-in-the-Loop Test Bench  ·  v5.0")
        t2.setStyleSheet("font-size:10pt;color:#8B949E;")
        title_col.addWidget(t1); title_col.addWidget(t2)
        hdr_lay.addLayout(title_col); hdr_lay.addStretch()
        lay.addWidget(hdr)

        body = QWidget(); body.setStyleSheet("background:#0D1117;")
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(32, 20, 32, 0); body_lay.setSpacing(10)

        def _row(label, value):
            r = QHBoxLayout(); r.setSpacing(0)
            lbl = QLabel(label); lbl.setFixedWidth(170)
            lbl.setStyleSheet("font-size:10pt;color:#8B949E;font-weight:600;")
            val = QLabel(value)
            val.setStyleSheet("font-size:10pt;color:#E6EDF3;")
            r.addWidget(lbl); r.addWidget(val); r.addStretch()
            return r

        body_lay.addLayout(_row("Platform:", "WipeWash BCM HIL v5"))
        body_lay.addLayout(_row("Target ECU:", "Wipe & Wash Body Control Module"))
        body_lay.addLayout(_row("Hardware:", "Raspberry Pi 4  ·  RPiBCM + RPiSIM"))
        body_lay.addLayout(_row("Bus Protocols:", "LIN 2.1  ·  CAN 2.0B  ·  XCP/A2L"))
        body_lay.addLayout(_row("Framework:", "PySide6  ·  Redis RTE  ·  Jenkins CI"))
        body_lay.addLayout(_row("Compatibility:", "dSPACE SCALEXIO-compatible"))
        body_lay.addStretch()

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("background:rgba(141,198,63,0.2);max-height:1px;")
        body_lay.addWidget(sep2)
        foot = QLabel("© KPIT Technologies  ·  Automotive Embedded Engineering")
        foot.setStyleSheet("font-size:9pt;color:#555;padding:8px 0 12px 0;")
        foot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body_lay.addWidget(foot)
        lay.addWidget(body, 1)

        btn_row = QHBoxLayout(); btn_row.setContentsMargins(0, 0, 20, 16)
        from widgets_base import _cd_btn as _b
        btn_close = _b("Close", KPIT_GREEN, h=32, w=100)
        btn_close.clicked.connect(dlg.accept)
        btn_row.addStretch(); btn_row.addWidget(btn_close)
        lay.addLayout(btn_row)
        dlg.exec()

    def _open_shortcuts_dialog(self) -> None:
        """Display keyboard shortcuts reference dialog."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Keyboard Shortcuts")
        dlg.setFixedSize(460, 420)
        dlg.setStyleSheet(f"QDialog{{background:#0D1117;color:#E6EDF3;font-family:{FONT_UI};}}"
                          "QLabel{background:transparent;}")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 20, 24, 16); lay.setSpacing(8)
        title = QLabel("Keyboard Shortcuts")
        title.setStyleSheet(f"font-size:14pt;font-weight:700;color:{KPIT_GREEN};margin-bottom:4px;")
        lay.addWidget(title)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background:rgba(141,198,63,0.3);max-height:1px;")
        lay.addWidget(sep)
        shortcuts = [
            ("Ctrl+Shift+S", "Open Network Discovery Scan"),
            ("Ctrl+R",        "Reconnect All Services"),
            ("Ctrl+B",        "Bus Config LDF / DBC Editor"),
            ("Ctrl+Q",        "Exit Application"),
            ("F1",            "About WipeWash"),
            ("F11",           "Toggle Fullscreen"),
            ("Ctrl+1",        "Switch to HardwareDesk"),
            ("Ctrl+2",        "Switch to BusDesk"),
            ("Ctrl+3",        "Switch to AutomationDesk"),
            ("Ctrl+4",        "Switch to XCPDesk"),
            ("Ctrl+5",        "Switch to FaultDesk"),
            ("Ctrl+6",        "Switch to DataDesk"),
        ]
        for key, desc in shortcuts:
            row = QHBoxLayout(); row.setSpacing(0)
            k = QLabel(key); k.setFixedWidth(160)
            k.setStyleSheet(f"font-family:{FONT_UI};font-size:10pt;color:{KPIT_GREEN};font-weight:600;")
            d = QLabel(desc); d.setStyleSheet("font-size:10pt;color:#8B949E;")
            row.addWidget(k); row.addWidget(d); row.addStretch()
            lay.addLayout(row)
        lay.addStretch()
        from widgets_base import _cd_btn as _b
        btn_close = _b("Close", KPIT_GREEN, h=32, w=100)
        btn_close.clicked.connect(dlg.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch(); btn_row.addWidget(btn_close)
        lay.addLayout(btn_row)
        dlg.exec()

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main Toolbar"); tb.setMovable(False); tb.setFixedHeight(42)
        tb.setStyleSheet(
            f"QToolBar{{background:#0D0D0D;border:none;"
            f"border-bottom:1px solid {KPIT_GREEN};spacing:4px;padding:2px 8px;}}"
            f"QToolButton{{background:transparent;border:none;border-radius:2px;"
            f"color:#CCCCCC;padding:3px 8px;font-family:{FONT_UI};font-size:10pt;}}"
            f"QToolButton:hover{{background:rgba(141,198,63,0.18);color:{KPIT_GREEN};}}")
        self.addToolBar(tb)

        # ── Logo KPIT dans la toolbar ──────────────────────────────────────
        import os as _os
        _logo_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "kpit_logo.png")
        _lbl_logo = QLabel()
        _lbl_logo.setFixedHeight(36)
        _lbl_logo.setContentsMargins(4, 2, 16, 2)
        _lbl_logo.setStyleSheet("background:transparent;")
        _pm_logo = QPixmap(_logo_path) if _os.path.exists(_logo_path) else QPixmap()
        if not _pm_logo.isNull():
            _lbl_logo.setPixmap(_pm_logo.scaledToHeight(34, Qt.TransformationMode.SmoothTransformation))
        tb.addWidget(_lbl_logo)

        # ── Séparateur après logo ──────────────────────────────────────────
        _sep_logo = QFrame(); _sep_logo.setFrameShape(QFrame.Shape.VLine)
        _sep_logo.setStyleSheet(f"background:{KPIT_GREEN};max-width:1px;margin:4px 8px;")
        tb.addWidget(_sep_logo)

        self._toolbar_leds:   dict[int, StatusLed] = {}
        self._toolbar_labels: dict[int, QLabel]    = {}
        for port, name, color in [
            (PORT_MOTOR,   "Motors", A_GREEN),
            (PORT_LIN,     "LIN",    A_TEAL),
            (PORT_PUMP_RX, "Pump",   A_ORANGE),
            (PORT_CAN,     "CAN",    CAN_VEH_C),
        ]:
            led = StatusLed(9); lbl = _lbl(f" {name} ", 10, True, "#AAAAAA")
            self._toolbar_leds[port]   = led
            self._toolbar_labels[port] = lbl
            cw = QWidget(); cw.setStyleSheet("background:transparent;")
            cl = QHBoxLayout(cw); cl.setContentsMargins(4, 0, 10, 0); cl.setSpacing(4)
            cl.addWidget(led); cl.addWidget(lbl); tb.addWidget(cw)
            sep = QFrame(); sep.setFrameShape(QFrame.Shape.VLine)
            sep.setStyleSheet(f"background:rgba(141,198,63,0.4);max-width:1px;"); tb.addWidget(sep)

        tb.addSeparator()
        btn_scan = _cd_btn("Scan", A_TEAL, h=27, w=110)
        btn_scan.clicked.connect(self._open_scan); tb.addWidget(btn_scan)

        btn_bus_cfg = _cd_btn("Bus Config", A_TEAL, h=27, w=110)
        btn_bus_cfg.setToolTip("Load a different LDF or DBC file")
        btn_bus_cfg.clicked.connect(self._open_bus_config)
        tb.addWidget(btn_bus_cfg)

        self._lbl_dt = _lbl("", 10, False, KPIT_GREEN, True)
        spacer = QWidget(); spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        spacer.setStyleSheet("background:transparent;")
        tb.addWidget(spacer); tb.addWidget(self._lbl_dt)
        dt_t = QTimer(self); dt_t.timeout.connect(self._upd_dt); dt_t.start(1000)
        self._upd_dt()

    def _on_tab_changed(self, idx: int) -> None:
        """Pause les timers d'animation sur les pages cachées → gain CPU.
        Page 0 = Motor/Pompe   Page 1 = LIN/CRS/Vehicle   Page 2 = Tests   Page 3 = Fault
        """
        # ── Page 0 : timers MotorWidget + PumpWidget ──────────
        for attr in ("motor_front", "motor_rear"):
            t = getattr(getattr(self._motor_panel, attr, None), "_t", None)
            if t:
                t.start(60) if idx == 0 else t.stop()
        pump_t = getattr(getattr(self._pump_panel, "pump_widget", None), "_t", None)
        if pump_t:
            pump_t.start(60) if idx == 0 else pump_t.stop()

        # ── Page 1 : oscilloscope LIN (CAN toujours actif sur la même page) ──
        osc_lin = getattr(getattr(self._crslin_panel, "_osc", None), "_t", None)
        if osc_lin:
            osc_lin.start(100) if idx == 1 else osc_lin.stop()
        osc_can = getattr(getattr(self._can_panel, "_osc", None), "_t", None)
        if osc_can:
            osc_can.start(100) if idx == 1 else osc_can.stop()

    # ── Helpers ──────────────────────────────────────────────
    def _upd_dt(self) -> None:
        self._lbl_dt.setText(datetime.datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))

    def _open_bus_config(self) -> None:
        """Ouvre l'éditeur LDF/DBC embarqué (BusConfigWidget) en fenêtre modale."""
        from PySide6.QtWidgets import QDialog, QVBoxLayout
        try:
            from bus_config_widget import BusConfigWidget
            _use_widget = True
        except ImportError:
            _use_widget = False

        dlg = QDialog(self)
        dlg.setWindowTitle("Bus Configuration — LDF / DBC Editor")
        dlg.setMinimumSize(1100, 700)
        dlg.resize(1280, 800)

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        if _use_widget:
            widget = BusConfigWidget(dlg)
            widget.file_saved.connect(self._on_bus_file_saved)
            lay.addWidget(widget)
        else:
            # Fallback : ancien BusConfigPanel si bus_config_widget indisponible
            from bus_config_panel import BusConfigPanel
            from PySide6.QtWidgets import QHBoxLayout
            from widgets_base import _cd_btn as _b
            dlg.resize(600, 500)
            panel = BusConfigPanel(dlg)
            lay.addWidget(panel, 1)
            btn_row = QHBoxLayout()
            btn_row.setContentsMargins(12, 8, 12, 10)
            btn_close = _b("Close", "#707070", h=28)
            btn_close.clicked.connect(dlg.accept)
            btn_row.addStretch(); btn_row.addWidget(btn_close)
            lay.addLayout(btn_row)
            panel.config_changed.connect(self._on_bus_config_changed)

        dlg.exec()

    def _on_bus_file_saved(self, dest: str, content: str) -> None:
        """Appelé après écriture sur disque par BusConfigWidget._do_download.

        dest    : chemin complet du fichier déjà écrit par le widget
        content : contenu (non utilisé ici, le fichier est déjà sur disque)

        On se contente de recharger la config à chaud et de rafraîchir la
        plateforme — l'écriture et le reload principal sont déjà faits dans
        _do_download pour éviter toute dépendance à network_config ici.
        """
        self._on_bus_config_changed()

    def _on_bus_config_changed(self) -> None:
        """Appelé après un rechargement LDF/DBC à chaud.

        Rafraîchit les structures qui dépendent des listes de messages CAN :
        - _CAN_CHANNELS de panels (oscilloscope)
        - _CAN_FRAME_COLORS de panels (table CAN)
        """
        try:
            from bcm_tcp_can_platform import can_channels_for_osc, can_frame_colors
            import panels
            panels._CAN_CHANNELS    = can_channels_for_osc()
            panels._CAN_FRAME_COLORS = can_frame_colors()
        except Exception:
            pass

    def _show_bus_info(self) -> None:
        """Affiche un résumé de la configuration bus active dans une boîte de dialogue."""
        try:
            from network_config import cfg_lin, cfg_can, active_paths
            paths = active_paths()
            lin_lines = "\n".join(
                f"  {n}  PID=0x{f['pid']:02X}  DLC={f['dlc']}"
                f"  cycle={f['cycle_s']*1000:.0f}ms"
                f"  signals={list(f['signals'].keys())}"
                for n, f in cfg_lin["frames"].items()
            )
            can_lines = "\n".join(
                f"  0x{mid:03X}  {msg.name}  sender={msg.sender}"
                f"  period={cfg_can['periods_ms'].get(mid,0)}ms"
                f"  signals={list(msg.signals.keys())}"
                for mid, msg in sorted(cfg_can["messages"].items())
            )
            info = (
                f"Active LDF: {paths['ldf']}\n"
                f"  baud = {cfg_lin['baud']}\n"
                f"{lin_lines}\n\n"
                f"Active DBC: {paths['dbc']}\n"
                f"{can_lines}"
            )
        except Exception as e:
            info = f"Error reading bus configuration:\n{e}"

        QMessageBox.information(self, "Active Bus Configuration", info)

    def _open_scan(self) -> None:
        dlg = DiscoveryDialog(self)
        # DirectConnection : set_host() s'exécute dans le thread appelant (main thread)
        # et non dans le thread worker bloqué sur wait() — sinon le signal serait mis
        # en queue et jamais traité tant que le worker attend.
        DC = Qt.ConnectionType.DirectConnection
        dlg.host_bcm_selected.connect(self._motor_worker.set_host,  DC)
        dlg.host_bcm_selected.connect(lambda ip: self._on_bcm_selected(ip))
        dlg.host_sim_selected.connect(self._motor_worker.set_sim_host, DC)
        dlg.host_lin_selected.connect(self._lin_worker.set_host,    DC)
        dlg.host_pump_selected.connect(self._pump_client.set_host)   # threading.Thread, pas de QThread
        dlg.host_can_selected.connect(self._can_worker.set_host,    DC)
        dlg.exec()

    def _on_bcm_selected(self, ip: str) -> None:
        """Appelé dès qu'un BCM est sélectionné dans le DiscoveryDialog.
        Lance la connexion Redis/XCP en arrière-plan pour ne pas bloquer le thread Qt."""
        # Annuler l'ancienne connexion si présente
        self._rte_client = None

        def _connect_bg():
            # Connexion Redis (peut bloquer jusqu'à socket_connect_timeout=2s)
            rte = RTEClient(ip)
            connected = rte.is_connected()
            # Repasser dans le thread Qt pour mettre à jour l'UI
            QTimer.singleShot(0, lambda: self._on_bcm_ready(ip, rte, connected))

        import threading as _th
        _th.Thread(target=_connect_bg, daemon=True, name="BCM-Connect").start()

    def _on_bcm_ready(self, ip: str, rte: RTEClient, connected: bool) -> None:
        """Appelé dans le thread Qt quand la connexion Redis BCM est établie."""
        self._rte_client = rte

        if hasattr(self, '_auto_test_panel'):
            self._auto_test_panel.set_redis_status(connected, ip)
        if hasattr(self, '_fi_panel'):
            if connected:
                self._fi_panel.on_connected_bcm(ip)
            else:
                self._fi_panel.on_disconnected_bcm()
        if hasattr(self, '_xcp_panel'):
            self._xcp_panel.set_host(ip)

        if connected:
            self._qsb.showMessage(f"[Redis] Connected to {ip}:6379")
        else:
            self._qsb.showMessage(f"[Redis] Connection failed {ip}:6379")

    # ── Connexion des signaux ────────────────────────────────
    def _connect_signals(self) -> None:
        self._motor_worker.motor_received.connect(self._motor_panel.on_motor_data)
        self._motor_worker.motor_received.connect(self._on_motor_data_ws)   # ← BCM→CarHTMLWidget (LIN tab)
        self._motor_worker.motor_received.connect(self._on_motor_data_mp)   # ← BCM→CarHTMLWidget (Motor/Pump tab)
        self._motor_worker.motor_received.connect(self._datasave_panel.on_motor_data)  # ← DataSave
        self._motor_worker.motor_received.connect(self._signal_hub.on_motor_data)      # ← SignalHub (drag&drop panels)
        self._motor_worker.status_changed.connect(self._on_motor_status)
        self._motor_worker.wiper_sent.connect(self._on_wiper_sent)
        self._motor_worker.sim_host_found.connect(self._on_sim_host_found)
        self._lin_worker.lin_received.connect(self._on_lin_event)
        self._lin_worker.lin_received.connect(self._datasave_panel.on_lin_event)       # ← DataSave
        self._lin_worker.lin_received.connect(self._signal_hub.on_lin_event)           # ← SignalHub (rest_contact_raw GPIO26)
        self._lin_worker.status_changed.connect(self._on_lin_status)
        self._pump_signal.data_received.connect(self._pump_panel.update_display)
        self._pump_signal.data_received.connect(self._fi_panel.on_pump_data)
        self._pump_signal.data_received.connect(self._datasave_panel.on_pump_data)     # ← DataSave
        self._pump_signal.data_received.connect(self._on_pump_data_mp)                 # ← Pump rain→CarHTMLWidget (Motor/Pump tab)
        self._pump_signal.data_received.connect(self._signal_hub.on_pump_data)         # ← SignalHub (drag&drop panels)
        self._pump_signal.connection_ok.connect(self._on_pump_ok)
        self._pump_signal.connection_lost.connect(self._on_pump_lost)
        self._can_worker.can_received.connect(self._can_panel.add_can_event)
        self._can_worker.can_received.connect(self._datasave_panel.on_can_event)       # ← DataSave
        self._can_worker.status_changed.connect(self._on_can_status)
        # NOTE : ack_needed → send_0x202 supprimé.
        # 0x202 est maintenant émis directement par bcmcan.py
        # (_build_0x202_from_state) — panels.py ne construit plus 0x202.

        # ── Signaux trigger → StatusBar (notification depuis tous les onglets) ─
        self._data_replay_panel.trigger_fired.connect(self._on_trigger_fired)
        self._data_replay_panel.trigger_cleared.connect(self._on_trigger_cleared)

        # ── Signaux virtuels ScenarioReplay → widgets (sans ECU physique) ──
        eng = self._scenario_panel._engine
        eng.virtual_motor_data.connect(self._motor_panel.on_motor_data)
        eng.virtual_motor_data.connect(self._on_motor_data_ws)
        eng.virtual_motor_data.connect(self._on_motor_data_mp)
        eng.virtual_motor_data.connect(self._signal_hub.on_motor_data)   # ← drag&drop Motor/Pump
        eng.virtual_lin_event.connect(self._on_lin_event)
        eng.virtual_lin_event.connect(self._signal_hub.on_lin_event)     # ← drag&drop rest_contact
        eng.virtual_pump_data.connect(self._pump_panel.update_display)
        eng.virtual_pump_data.connect(self._on_pump_data_mp)
        eng.virtual_pump_data.connect(self._signal_hub.on_pump_data)     # ← drag&drop Pump

    # ── Mapping état BCM (WSM state string) → wiper_op entier (0-7) ──────────
    # Miroir de bcm_rte.py WOP_* / ST_* — utilisé pour synchroniser la voiture HTML
    # avec l'état RTE réel sans dépendre de _cur_op (sélection UI manuelle).
    _STATE_TO_OP: dict = {
        "OFF":        0,  # WOP_OFF
        "TOUCH":      1,  # WOP_TOUCH      (single wipe)
        "SPEED1":     2,  # WOP_SPEED1     (intermittent / slow)
        "SPEED2":     3,  # WOP_SPEED2     (fast)
        "AUTO":       4,  # WOP_AUTO       (rain sensor)
        "FRONT_WASH": 5,  # WOP_FRONT_WASH
        "REAR_WASH":  6,  # WOP_REAR_WASH
        "REAR_WIPE":  7,  # WOP_REAR_WIPE
        # États internes WSM qui n'ont pas de wiper_op dédié → garder op courant UI
        "ERROR":      None,
        "DIAG":       None,
    }

    def _op_from_state(self, bcm_state: str) -> int:
        """
        Dérive le wiper_op (0-7) depuis l'état WSM broadcasté par le BCM.
        Fallback sur _cur_op (sélection UI) pour les états sans mapping direct
        (ERROR, DIAG, ou états inconnus).
        """
        op = self._STATE_TO_OP.get(bcm_state.upper())
        if op is None:
            op = getattr(self._crslin_panel, "_cur_op", 0)
        return op

    def _on_motor_data_ws(self, data: dict) -> None:
        """
        Dispatch données BCM temps réel vers CarHTMLWidget + Rest Contact + CRS Fault.
        Appelé à chaque motor_received (~200 ms) depuis le TCP broadcast du BCM.
        Complète _on_lin_event : données moteur sur port 5000, events LIN sur 5555.
        """
        front_on     = data.get("front", "OFF") == "ON"
        rear_on      = data.get("rear",  "OFF") == "ON"
        motor_on     = front_on or rear_on          # REAR_WASH/REAR_WIPE use rear motor
        rest_raw     = bool(data.get("rest_contact_raw",   False))
        blade_cycles = int(data.get("front_blade_cycles",  0))
        bcm_state    = str(data.get("state",               "OFF"))
        crs_fault    = int(data.get("crs_fault",           0))
        # Dériver l'op depuis l'état RTE réel (bcm_state) plutôt que la sélection UI
        cur_op       = self._op_from_state(bcm_state)

        # Voiture HTML : état wiper temps réel BCM
        self._car_html.set_wiper_from_bcm(
            motor_on  = motor_on,
            rest_raw  = rest_raw,
            bcm_state = bcm_state,
            op        = cur_op,
        )

        # Rest Contact panel
        self._crslin_panel.update_rest_contact(rest_raw, blade_cycles)

        # CRS Fault (depuis rte.crs_fault broadcasté par BCM)
        self._crslin_panel.update_crs_fault(crs_fault)

        # ── Barres Rear Wiper Status (CRS panel) pour REAR_WASH/REAR_WIPE ──
        # Le moteur arrière transmet ses données via le flux TCP moteur (port 5000).
        # On extrait current et blade et on les pousse dans le panneau CRS.
        if cur_op in (6, 7):
            def _d(v):
                if isinstance(v, dict): return v
                try:
                    import json as _j
                    return _j.loads(v) if isinstance(v, str) else {}
                except Exception:
                    return {}
            # Format BCM réel : dict plat avec "current" à la racine
            # Format simulateur : "rear" est un dict avec "motor_current"
            if isinstance(data.get("rear"), str):
                rear_cur   = float(data.get("current", 0.0))
                rear_blade = float(data.get("blade_position", 0.0))
                rear_fault = bool(data.get("fault", False))
            else:
                r          = _d(data.get("rear", {}))
                rear_cur   = float(r.get("motor_current", 0.0))
                rear_blade = float(r.get("blade_position", 0.0))
                rear_fault = bool(r.get("fault_status", False))
            self._crslin_panel.update_rear_wiper_status(
                blade_pct = rear_blade,
                current_A = rear_cur,
                fault     = rear_fault,
            )

    def _on_motor_data_mp(self, data: dict) -> None:
        """
        Dispatch données BCM temps réel vers CarHTMLWidget de l'onglet Motor/Pump.
        Même logique que _on_motor_data_ws mais pilote self._car_html_mp.
        """
        front_on  = data.get("front", "OFF") == "ON"
        rear_on   = data.get("rear",  "OFF") == "ON"
        motor_on  = front_on or rear_on
        rest_raw  = bool(data.get("rest_contact_raw", False))
        bcm_state = str(data.get("state", "OFF"))
        # Dériver l'op depuis l'état RTE réel (bcm_state) plutôt que la sélection UI
        cur_op    = self._op_from_state(bcm_state)

        self._car_html_mp.set_wiper_from_bcm(
            motor_on  = motor_on,
            rest_raw  = rest_raw,
            bcm_state = bcm_state,
            op        = cur_op,
        )

    def _on_pump_data_mp(self, data: dict) -> None:
        """
        Sync pump state + rain → CarHTMLWidget onglet Motor/Pump.
        Pilote l'animation pompe (impeller, fluid lines) et la pluie.
        """
        # Animation pompe (impeller + lignes fluide)
        state = data.get("state", "OFF")
        fault = bool(data.get("fault", False))
        self._car_html_mp.set_pump_state(state, fault)
        # Sync pluie depuis slider VehicleRainPanel
        rain_pct = getattr(self._pump_panel, "_rain_pct", 0)
        self._car_html_mp.set_rain(rain_pct)

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
        host = self._motor_worker.host
        if ok and host:
            if self._rte_client is None:
                # Chemin de secours (pas passé par le DiscoveryDialog)
                self._rte_client = RTEClient(host)
                connected = self._rte_client.is_connected()
                if hasattr(self, '_auto_test_panel'):
                    self._auto_test_panel.set_redis_status(connected, host)
                if hasattr(self, '_fi_panel'):
                    if connected:
                        self._fi_panel.on_connected_bcm(host)
                    else:
                        self._fi_panel.on_disconnected_bcm()
                if hasattr(self, '_xcp_panel'):
                    self._xcp_panel.set_host(host)
                if connected:
                    self._qsb.showMessage(f"[Redis] Connected to {host}:6379")
        elif not ok:
            self._rte_client = None
            if hasattr(self, '_auto_test_panel'):
                self._auto_test_panel.set_redis_status(False)
            if hasattr(self, '_fi_panel'):
                self._fi_panel.on_disconnected_bcm()

    def _on_wiper_sent(self, op: int, seq: int) -> None:
        self._crslin_panel.on_wiper_sent(op, seq)

    def _on_lin_event(self, ev: dict) -> None:
        self._crslin_panel.add_lin_event(ev)
        t = ev.get("type", "")

        # ── Trame 0x16 TX (BCM → slave) : wiper_op + état moteur + rest contact ──
        if t == "TX":
            op          = int(ev.get("op",            0))
            bcm_state   = str(ev.get("bcm_state",     "OFF"))
            motor_on    = bool(ev.get("front_motor_on", False)) or bool(ev.get("rear_motor_on", False))  # REAR_WASH/REAR_WIPE use rear motor
            rest_raw    = bool(ev.get("rest_contact_raw", False))
            blade_cycles= int(ev.get("front_blade_cycles", 0))

            # Voiture HTML : état wiper temps réel BCM
            self._car_html.set_wiper_from_bcm(
                motor_on  = motor_on,
                rest_raw  = rest_raw,
                bcm_state = bcm_state,
                op        = op,
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

    def _on_sim_host_found(self, host: str) -> None:
        """Connecte le SimClient au RPi Simulateur dès que son IP est connue."""
        if not self._sim_client.is_connected():
            ok = self._sim_client.connect(host)
            if ok:
                self._qsb.showMessage(f"[SimClient] Simulator connected: {host}:5000")
                print(f"[SimClient] Fault injection -> {host}:5000")
                if hasattr(self, '_fi_panel'):
                    self._fi_panel.on_connected_sim(host)

    def _on_rescan(self) -> None:
        # Moteurs
        self._motor_worker.stop()
        self._motor_thread.quit(); self._motor_thread.wait(2000)
        self._motor_worker = MotorVehicleWorker()
        self._motor_thread = QThread()
        self._motor_worker.moveToThread(self._motor_thread)
        self._motor_thread.started.connect(self._motor_worker.run)
        self._motor_worker.motor_received.connect(self._motor_panel.on_motor_data)
        self._motor_worker.motor_received.connect(self._on_motor_data_ws)   # ← BCM→CarHTMLWidget (LIN tab)
        self._motor_worker.motor_received.connect(self._on_motor_data_mp)   # ← BCM→CarHTMLWidget (Motor/Pump tab)
        self._motor_worker.motor_received.connect(self._datasave_panel.on_motor_data)  # ← DataSave
        self._motor_worker.motor_received.connect(self._signal_hub.on_motor_data)      # ← SignalHub
        self._motor_worker.wiper_sent.connect(self._on_wiper_sent)
        self._motor_worker.sim_host_found.connect(self._on_sim_host_found)
        self._sim_client.disconnect()   # reset SimClient — sera reconnecté via sim_host_found
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
        self._lin_worker.lin_received.connect(self._datasave_panel.on_lin_event)       # ← DataSave
        self._lin_worker.lin_received.connect(self._signal_hub.on_lin_event)           # ← SignalHub (rest_contact_raw GPIO26)
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
        self._can_worker.can_received.connect(self._datasave_panel.on_can_event)       # ← DataSave
        self._can_worker.status_changed.connect(self._on_can_status)
        # NOTE : ack_needed → send_0x202 supprimé.
        # 0x202 est maintenant émis directement par bcmcan.py
        # (_build_0x202_from_state) — panels.py ne construit plus 0x202.
        self._can_thread.start()
        # Pompe — PumpDataClient se reconnecte automatiquement

    def closeEvent(self, e) -> None:
        self._motor_worker.stop(); self._motor_thread.quit(); self._motor_thread.wait(2000)
        self._lin_worker.stop();   self._lin_thread.quit();   self._lin_thread.wait(2000)
        self._can_worker.stop();   self._can_thread.quit();   self._can_thread.wait(2000)
        e.accept()

    # ══════════════════════════════════════════════════════════
    #  SLOTS — TRIGGER STATUSBAR
    # ══════════════════════════════════════════════════════════
    def _on_trigger_fired(self, msg: str) -> None:
        """Déclenché par DataReplayPanel quand un overcurrent démarre l'enregistrement."""
        self._sb_trigger.setText(f"  🔴 REC TRIGGER — {msg}  ")
        self._sb_trigger.setStyleSheet(
            f"color:#FF4444;background:#3A0000;padding:0 8px;"
            f"border-left:2px solid #CC0000;"
            f"font-family:{FONT_MONO};font-size:9pt;font-weight:bold;")
        self._sb_trig_blink_state = False
        self._sb_trig_blink.start()

    def _on_trigger_cleared(self) -> None:
        """Déclenché quand l'alarme est acquittée dans Data/Replay."""
        self._sb_trig_blink.stop()
        self._sb_trig_blink_state = False
        self._sb_trigger.setText("  ◉ TRIGGER: acknowledged  ")
        self._sb_trigger.setStyleSheet(
            f"color:{W_TEXT_DIM};background:transparent;padding:0 8px;"
            f"font-family:{FONT_MONO};font-size:9pt;")

    def _on_sb_trig_blink(self) -> None:
        """Clignotement du widget trigger dans la StatusBar (400 ms)."""
        self._sb_trig_blink_state = not self._sb_trig_blink_state
        if self._sb_trig_blink_state:
            self._sb_trigger.setStyleSheet(
                f"color:#FFFFFF;background:#CC0000;padding:0 8px;"
                f"font-family:{FONT_MONO};font-size:9pt;font-weight:bold;")
        else:
            self._sb_trigger.setStyleSheet(
                f"color:#FF4444;background:#3A0000;padding:0 8px;"
                f"border-left:2px solid #CC0000;"
                f"font-family:{FONT_MONO};font-size:9pt;font-weight:bold;")