from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTabWidget, QGroupBox, QGridLayout,
    QPushButton, QTableWidget, QTableWidgetItem, QLabel, QHBoxLayout,
    QLineEdit, QSpinBox, QCheckBox, QScrollArea, QFrame
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QHeaderView
from PySide6.QtGui import QColor
from ui.widgets.frame_monitor_widget import FrameMonitorWidget

class BCMPage(QWidget):
    read_dtc_requested = Signal(int)
    clear_dtc_requested = Signal()
    snapshot_requested = Signal(int, int)
    extended_requested = Signal(int, int)
    communication_control_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.dtc_database = {}
        self.current_extended_dtc = None
        self.current_extended_record_num = None

        self.tabs.addTab(self.create_dtc_tab(), "DTC")
        self.tabs.addTab(self.create_communication_tab(), "Communication")
        layout.addWidget(self.tabs)

    def set_tab(self, index):
        self.tabs.setCurrentIndex(index)

    # ------------------------------------------------------------------
    # DTC Tab
    # ------------------------------------------------------------------
    def create_dtc_tab(self):
        tab = QWidget()
        outer_layout = QVBoxLayout(tab)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background-color: transparent;")
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.verticalScrollBar().setSingleStep(40)
        scroll.verticalScrollBar().setPageStep(300)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        # ── Tableau DTC ──────────────────────────────────────────────
        self.dtc_table = QTableWidget(0, 4)
        self.dtc_table.setHorizontalHeaderLabels(["DTC", "Status", "Category", "Description"])
        header = self.dtc_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.dtc_table.setWordWrap(True)
        self.dtc_table.verticalHeader().setVisible(False)
        self.dtc_table.verticalHeader().setDefaultSectionSize(54)
        self.dtc_table.setMinimumHeight(260)
        layout.addWidget(self.dtc_table)

        # ── Filtre status ─────────────────────────────────────────────
        filter_group = QGroupBox("Status Filter")
        filter_layout = QHBoxLayout()
        self.filter_all = QCheckBox("All (0xFF)")
        self.filter_active = QCheckBox("Not Active in Memory (0x2E)")
        self.filter_not_active = QCheckBox("Active in Memory (0x2F)")
        self.filter_all.setChecked(True)
        filter_layout.addWidget(self.filter_all)
        filter_layout.addWidget(self.filter_active)
        filter_layout.addWidget(self.filter_not_active)
        filter_group.setLayout(filter_layout)
        layout.addWidget(filter_group)

        ctrl_layout = QHBoxLayout()
        self.read_dtc_btn = QPushButton("Read DTCs")
        self.read_dtc_btn.clicked.connect(self._emit_read_dtc)
        self._apply_green_button_style(self.read_dtc_btn)
        self.clear_dtc_btn = QPushButton("Clear DTCs")
        self.clear_dtc_btn.clicked.connect(self.clear_dtc_requested)
        self._apply_green_button_style(self.clear_dtc_btn)
        ctrl_layout.addWidget(self.read_dtc_btn)
        ctrl_layout.addWidget(self.clear_dtc_btn)
        layout.addLayout(ctrl_layout)

        # ── Snapshot ──────────────────────────────────────────────────
        snap_group = QGroupBox("Snapshot Record")
        snap_layout = QGridLayout()
        snap_layout.addWidget(QLabel("DTC:"), 0, 0)
        self.snapshot_dtc = QLineEdit()
        self.snapshot_dtc.setPlaceholderText("e.g., B22004")
        snap_layout.addWidget(self.snapshot_dtc, 0, 1)
        snap_layout.addWidget(QLabel("Record:"), 0, 2)
        self.snapshot_record = QSpinBox()
        self.snapshot_record.setRange(1, 10)
        snap_layout.addWidget(self.snapshot_record, 0, 3)
        self.snapshot_btn = QPushButton("Get Snapshot")
        self.snapshot_btn.clicked.connect(self._emit_snapshot)
        self._apply_green_button_style(self.snapshot_btn)
        snap_layout.addWidget(self.snapshot_btn, 1, 0, 1, 4)
        snap_group.setLayout(snap_layout)
        layout.addWidget(snap_group)

        # ── Snapshot décodé ───────────────────────────────────────────
        decoded_group = QGroupBox("Decoded Snapshot")
        decoded_layout = QVBoxLayout(decoded_group)
        self.snapshot_meta_label = QLabel("No snapshot loaded")
        self.snapshot_meta_label.setStyleSheet("color: #1b5e20; font-weight: bold;")
        decoded_layout.addWidget(self.snapshot_meta_label)

        self.snapshot_grid = QGridLayout()
        self.snapshot_grid.setHorizontalSpacing(24)
        self.snapshot_grid.setVerticalSpacing(10)
        self.snapshot_labels = {}
        snapshot_items = [
            ("Ignition",           "ignition"),
            ("Wiper Mode",         "front_mode"),
            ("Motor Current (mA)", "front_current"),
            ("Blade Position (%)", "front_position"),
            ("Rain Intensity (%)", "rain"),
            ("Vehicle Speed (km/h)","vehicle_speed"),
        ]
        for idx, (title, key) in enumerate(snapshot_items):
            row, col = idx // 2, (idx % 2) * 2
            title_label = QLabel(f"{title}:")
            value_label = QLabel("--")
            value_label.setStyleSheet("font-weight: bold; color: #2e7d32;")
            self.snapshot_grid.addWidget(title_label, row, col)
            self.snapshot_grid.addWidget(value_label, row, col + 1)
            self.snapshot_labels[key] = value_label
        decoded_layout.addLayout(self.snapshot_grid)
        layout.addWidget(decoded_group)

        # ── Extended ──────────────────────────────────────────────────
        ext_group = QGroupBox("Extended Data")
        ext_layout = QGridLayout()
        ext_layout.addWidget(QLabel("DTC:"), 0, 0)
        self.extended_dtc = QLineEdit()
        self.extended_dtc.setPlaceholderText("e.g., B22004")
        ext_layout.addWidget(self.extended_dtc, 0, 1)
        ext_layout.addWidget(QLabel("Record:"), 0, 2)
        self.extended_record = QSpinBox()
        self.extended_record.setRange(1, 10)
        ext_layout.addWidget(self.extended_record, 0, 3)
        self.extended_btn = QPushButton("Get Extended")
        self.extended_btn.clicked.connect(self._emit_extended)
        self._apply_green_button_style(self.extended_btn)
        ext_layout.addWidget(self.extended_btn, 1, 0, 1, 4)
        ext_group.setLayout(ext_layout)
        layout.addWidget(ext_group)

        # ── Extended décodé ───────────────────────────────────────────
        decoded_ext_group = QGroupBox("Decoded Extended Data")
        decoded_ext_layout = QVBoxLayout(decoded_ext_group)
        self.extended_meta_label = QLabel("No extended data loaded")
        self.extended_meta_label.setStyleSheet("color: #1b5e20; font-weight: bold;")
        decoded_ext_layout.addWidget(self.extended_meta_label)

        self.extended_grid = QGridLayout()
        self.extended_grid.setHorizontalSpacing(24)
        self.extended_grid.setVerticalSpacing(10)
        self.extended_labels = {}
        # ISO 14229-1 Extended Data Records
        extended_items = [
            ("Occurrence Counter",           "occurrence_count"),
            ("Failed Cycles",                "failed_cycles"),
            ("Time Since First Occ. (s)",   "time_first_occ"),
            ("Time Since Last Occ. (s)",    "time_last_occ"),
        ]
        for idx, (title, key) in enumerate(extended_items):
            row, col = idx // 2, (idx % 2) * 2
            title_label = QLabel(f"{title}:")
            value_label = QLabel("--")
            value_label.setStyleSheet("font-weight: bold; color: #2e7d32;")
            self.extended_grid.addWidget(title_label, row, col)
            self.extended_grid.addWidget(value_label, row, col + 1)
            self.extended_labels[key] = value_label
        decoded_ext_layout.addLayout(self.extended_grid)
        layout.addWidget(decoded_ext_group)

        layout.addStretch()
        scroll.setWidget(container)
        outer_layout.addWidget(scroll)
        return tab
    # ------------------------------------------------------------------
    # Communication Tab
    # ------------------------------------------------------------------
    def create_communication_tab(self):
        import time as _time
        tab = QWidget()
        outer_layout = QVBoxLayout(tab)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background-color: transparent;")
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        scroll_container = QWidget()
        layout = QVBoxLayout(scroll_container)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # ── Communication Control (UDS 0x28) ─────────────────────────────
        comm_group = QGroupBox("Communication Control  [UDS 0x28]")
        comm_group.setStyleSheet("""
                    QGroupBox {
                        font-weight: bold; font-size: 13px;
                        border: 2px solid #2e7d32; border-radius: 8px;
                        margin-top: 1.2em; padding-top: 10px;
                    }
                    QGroupBox::title {
                        subcontrol-origin: margin; left: 10px;
                        padding: 0 6px; color: #0d1f0d; font-weight: bold;
                    }
        """)
        comm_inner = QVBoxLayout(comm_group)
        comm_inner.setSpacing(10)

        # Status LED + label
        status_row = QHBoxLayout()
        self._comm_led = QLabel()
        self._comm_led.setFixedSize(14, 14)
        self._comm_led.setStyleSheet(
            "background-color: #2e7d32; border-radius: 7px; border: 1px solid #444;"
        )
        self.comm_state = QLabel("Rx: Enabled  |  Tx: Enabled")
        self.comm_state.setStyleSheet(
            "color: #1b5e20; font-weight: bold; font-size: 13px; font-family: Consolas;"
        )
        status_row.addWidget(self._comm_led)
        status_row.addWidget(self.comm_state)
        status_row.addStretch()
        comm_inner.addLayout(status_row)

        # Boutons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.disable_rx_btn = QPushButton("⊘  Disable Rx")
        self.disable_rx_btn.setFixedHeight(38)
        self.disable_rx_btn.clicked.connect(lambda: self._on_comm_control(1))
        self._apply_comm_btn_style(self.disable_rx_btn, "#7f1d1d", "#dc2626")

        self.disable_tx_btn = QPushButton("⊘  Disable Tx")
        self.disable_tx_btn.setFixedHeight(38)
        self.disable_tx_btn.clicked.connect(lambda: self._on_comm_control(2))
        self._apply_comm_btn_style(self.disable_tx_btn, "#7f1d1d", "#dc2626")

        self.disable_all_btn = QPushButton("⊘  Disable Rx + Tx")
        self.disable_all_btn.setFixedHeight(38)
        self.disable_all_btn.clicked.connect(lambda: self._on_comm_control(3))
        self._apply_comm_btn_style(self.disable_all_btn, "#4a1942", "#9333ea")

        self.enable_all_btn = QPushButton("✔  Enable All")
        self.enable_all_btn.setFixedHeight(38)
        self.enable_all_btn.clicked.connect(lambda: self._on_comm_control(0))
        self._apply_comm_btn_style(self.enable_all_btn, "#14532d", "#16a34a")

        btn_layout.addWidget(self.disable_rx_btn)
        btn_layout.addWidget(self.disable_tx_btn)
        btn_layout.addWidget(self.disable_all_btn)
        btn_layout.addWidget(self.enable_all_btn)
        comm_inner.addLayout(btn_layout)

        layout.addWidget(comm_group)

        # ── DoIP Network Status ───────────────────────────────────────────
        doip_group = QGroupBox("DoIP Network Status  [ISO 13400]")
        doip_group.setStyleSheet(comm_group.styleSheet())
        doip_layout = QGridLayout(doip_group)
        doip_layout.setSpacing(8)

        doip_fields = [
            ("Protocol Version", "2012 (ISO 13400-2)"),
            ("Activation Type", "Default (0x00)"),
            ("Routing Status", "Active"),
            ("Diagnostic Power Mode", "Ready"),
            ("Tester Address", "0x07DF"),
            ("Target Address", "0x0700 / 0x0701"),
        ]
        for i, (lbl, val) in enumerate(doip_fields):
            row, col = i // 2, (i % 2) * 2
            name_lbl = QLabel(f"{lbl}:")
            name_lbl.setStyleSheet("color: #37474f; font-size: 12px; font-weight: 600;")
            val_lbl = QLabel(val)
            val_lbl.setStyleSheet(
                "color: #1b5e20; font-weight: bold; font-size: 12px; font-family: Consolas;"
            )
            doip_layout.addWidget(name_lbl, row, col)
            doip_layout.addWidget(val_lbl, row, col + 1)

        layout.addWidget(doip_group)

        # ── Frame Monitor ─────────────────────────────────────────────────
        from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView
        from PySide6.QtGui import QFont

        # ── Frame Monitor ─────────────────────────────────────────────────
        monitor_group = QGroupBox("Frame Monitor  [DoIP / UDS — Live Capture]")
        monitor_group.setStyleSheet(comm_group.styleSheet())
        monitor_layout = QVBoxLayout(monitor_group)
        monitor_layout.setContentsMargins(6, 6, 6, 6)

        self.frame_monitor = FrameMonitorWidget()
        monitor_layout.addWidget(self.frame_monitor)

        layout.addWidget(monitor_group, stretch=1)

        # Storage interne
        self._monitor_frames = []
        self._monitor_counter = 0

        scroll.setWidget(scroll_container)
        outer_layout.addWidget(scroll)
        return tab

    # ------------------------------------------------------------------
    # Communication Control handlers
    # ------------------------------------------------------------------
    def _on_comm_control(self, subf: int):
        labels = {
            0: ("Rx: Enabled  |  Tx: Enabled", "#2e7d32"),
            1: ("Rx: DISABLED  |  Tx: Enabled", "#dc2626"),
            2: ("Rx: Enabled  |  Tx: DISABLED", "#dc2626"),
            3: ("Rx: DISABLED  |  Tx: DISABLED", "#9333ea"),
        }
        text, color = labels.get(subf, ("Unknown", "#aaaaaa"))
        self.comm_state.setText(text)
        self.comm_state.setStyleSheet(
            "color: #1b5e20; font-weight: bold; font-size: 13px; font-family: Consolas;"
        )
        led_color = "#2e7d32" if subf == 0 else ("#9333ea" if subf == 3 else "#dc2626")
        self._comm_led.setStyleSheet(
            f"background-color: {led_color}; border-radius: 7px; border: 1px solid #444;"
        )
        self.communication_control_requested.emit(subf)

    def _apply_comm_btn_style(self, btn, bg: str, hover: str):
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg}; color: #e0e0e0;
                border: 1px solid {hover}; border-radius: 6px;
                font-size: 12px; font-weight: bold; padding: 4px 12px;
            }}
            QPushButton:hover {{ background-color: {hover}; }}
            QPushButton:pressed {{ background-color: #0d1f0d; }}
            QPushButton:checked {{ background-color: {hover}; }}
        """)

    # ------------------------------------------------------------------
    # Slots émetteurs
    # ------------------------------------------------------------------
    def _emit_read_dtc(self):
        if self.filter_active.isChecked():
            mask = 0x2E
        elif self.filter_not_active.isChecked():
            mask = 0x2F
        else:
            mask = 0xFF
        self.read_dtc_requested.emit(mask)

    def _emit_snapshot(self):
        try:
            dtc = int(self.snapshot_dtc.text(), 16)
        except ValueError:
            return
        self.snapshot_requested.emit(dtc, self.snapshot_record.value())

    def _emit_extended(self):
        try:
            dtc = int(self.extended_dtc.text(), 16)
        except ValueError:
            return
        self.extended_requested.emit(dtc, self.extended_record.value())

    # ------------------------------------------------------------------
    # Base DTC
    # ------------------------------------------------------------------
    def set_dtc_database(self, database):
        self.dtc_database = database

    # ------------------------------------------------------------------
    # Mise à jour tableau DTC
    # ------------------------------------------------------------------
    def update_dtc_table(self, dtc_list):
        color_map = {
            "electrical":    "#FF6347",
            "communication": "#FFA500",
            "functional":    "#87CEFA",
            "protection":    "#2e7d32",
            "signal_fault":  "#DA70D6",
        }

        self.dtc_table.setRowCount(len(dtc_list))
        for row, (dtc, status) in enumerate(dtc_list):
            dtc_item = QTableWidgetItem(f"0x{dtc:06X}")
            status_label = f"0x{status:02X}"
            if status == 0x2F:
                status_label += "  ACTIVE"
            elif status == 0x2E:
                status_label += "  NOT ACTIVE"
            status_item = QTableWidgetItem(status_label)

            if status == 0x2F:
                status_item.setForeground(QColor("#c62828"))
            elif status == 0x2E:
                status_item.setForeground(QColor("#f57f17"))
            else:
                status_item.setForeground(QColor("#888888"))

            self.dtc_table.setItem(row, 0, dtc_item)
            self.dtc_table.setItem(row, 1, status_item)

            info = self.dtc_database.get(dtc, {})
            category = info.get("category", "—")
            description = info.get("description", "—")

            cat_item = QTableWidgetItem(category)
            cat_item.setForeground(QColor(color_map.get(category, "#888888")))
            self.dtc_table.setItem(row, 2, cat_item)

            self.dtc_table.setItem(row, 3, QTableWidgetItem(description))

        self.dtc_table.resizeRowsToContents()

    # ------------------------------------------------------------------
    # Décodage Snapshot
    # ------------------------------------------------------------------
    def display_snapshot(self, dtc_code: int, record_num: int, data: bytes):
        if not hasattr(self, "snapshot_meta_label"):
            return

        self.snapshot_meta_label.setText(
            f"DTC 0x{dtc_code:06X} | Record {record_num} | {len(data)} bytes"
        )
        for label in self.snapshot_labels.values():
            label.setText("--")

        if not data or len(data) < 7:
            return

        # Header : [59][04][DTC H][DTC M][DTC L][Status][RecNum]
        # DIDs commencent à l'octet 7
        payload = data[7:]
        did_values = {}
        i = 0
        while i + 2 < len(payload):
            did    = (payload[i] << 8) | payload[i + 1]
            length = payload[i + 2]
            val    = payload[i + 3: i + 3 + length]
            if len(val) < length:
                break
            did_values[did] = val
            i += 3 + length

        if did_values:
            ign_b = did_values.get(0xF190, b"\x00")
            self.snapshot_labels["ignition"].setText("ON" if ign_b[0] else "OFF")

            mode_b = did_values.get(0xF191, b"")
            mode_str = mode_b.rstrip(b"\x00").decode("ascii", errors="replace").strip()
            self.snapshot_labels["front_mode"].setText(mode_str if mode_str else "--")

            curr_b = did_values.get(0xF192, b"\x00\x00")
            curr = ((curr_b[0] << 8) | curr_b[1]) if len(curr_b) >= 2 else 0
            self.snapshot_labels["front_current"].setText(str(curr))

            pos_b = did_values.get(0xF193, b"\x00")
            self.snapshot_labels["front_position"].setText(str(pos_b[0]))

            rain_b = did_values.get(0xF194, b"\x00")
            self.snapshot_labels["rain"].setText(str(rain_b[0]))

            spd_b = did_values.get(0xF195, b"\x00\x00")
            spd = ((spd_b[0] << 8) | spd_b[1]) if len(spd_b) >= 2 else 0
            self.snapshot_labels["vehicle_speed"].setText(str(spd))

    # ------------------------------------------------------------------
    # Décodage Extended
    # ------------------------------------------------------------------
    def display_extended(self, dtc_code: int, record_num: int, data: bytes):
        """
        Parse ISO 14229-1 Extended Data Records (0x19 0x06).
        Format : [59 06 DTC(3) STATUS] [recNum lenN data...] ...
        Records : 0x01 occurrence_count | 0x03 failed_cycles
                  0x03 failed_cycles    | 0x04 time_first_occ
                  0x05 time_last_occ
        """
        import struct as _struct
        if not hasattr(self, "extended_meta_label"):
            return

        self.extended_meta_label.setText(
            f"DTC 0x{dtc_code:06X} | {len(data)} bytes"
        )
        for label in self.extended_labels.values():
            label.setText("--")

        if not data or len(data) < 6:
            return

        # Header : [59 06 DTC(3) STATUS] = 6 octets
        payload = data[6:]

        # Parser les records TLV : [recNum][len][data...]
        i = 0
        while i + 1 < len(payload):
            rec_num = payload[i]
            rec_len = payload[i + 1]
            rec_val = payload[i + 2 : i + 2 + rec_len]
            if len(rec_val) < rec_len:
                break

            if rec_num == 0x01 and rec_len == 2:   # occurrence counter
                occ = (rec_val[0] << 8) | rec_val[1]
                self.extended_labels["occurrence_count"].setText(str(occ))

            elif rec_num == 0x03 and rec_len == 1: # failed cycles
                self.extended_labels["failed_cycles"].setText(str(rec_val[0]))

            elif rec_num == 0x04 and rec_len == 4: # time since first occ
                secs = _struct.unpack(">I", rec_val)[0]
                txt = self._format_duration(secs)
                self.extended_labels["time_first_occ"].setText(txt)

            elif rec_num == 0x05 and rec_len == 4: # time since last occ
                secs = _struct.unpack(">I", rec_val)[0]
                txt = self._format_duration(secs)
                self.extended_labels["time_last_occ"].setText(txt)

            i += 2 + rec_len

        self.current_extended_dtc = dtc_code
        self.current_extended_record_num = record_num

    # ------------------------------------------------------------------
    # Helpers texte
    # ------------------------------------------------------------------
    def _severity_text(self, value: int) -> str:
        return {0: "None", 1: "Low", 2: "Medium", 3: "High"}.get(value, str(value))

    def _session_text(self, value: int) -> str:
        return {0x01: "Default", 0x03: "Extended"}.get(value, f"0x{value:02X}")

    def _format_duration(self, secs: int) -> str:
        """Convertit des secondes en format lisible h/min/s."""
        if secs == 0xFFFFFFFF:
            return "N/A"
        if secs < 60:
            return f"{secs}s"
        elif secs < 3600:
            return f"{secs // 60}min {secs % 60}s"
        else:
            h = secs // 3600
            m = (secs % 3600) // 60
            return f"{h}h {m}min"

    def _front_mode_text(self, code: int) -> str:
        return {0: "Off", 1: "Single", 3: "Low", 4: "High", 5: "Auto"}.get(
            code, f"0x{code:02X}"
        )

    # ------------------------------------------------------------------
    # Style boutons
    # ------------------------------------------------------------------
    def _apply_green_button_style(self, button):
        button.setStyleSheet("""
            QPushButton {
                background-color: #2e7d32;
                color: white;
                border: none;
                padding: 10px 16px;
                border-radius: 6px;
                font-weight: bold;
                min-height: 36px;
            }
            QPushButton:hover { background-color: #1b5e20; }
            QPushButton:pressed { background-color: #0d4d0d; }
            QPushButton:disabled { background-color: #4a4a4a; color: #aaaaaa; }
        """)