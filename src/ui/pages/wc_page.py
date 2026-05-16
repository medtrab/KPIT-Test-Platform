from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QPushButton, QTableWidget, QTableWidgetItem,
    QLabel, QLineEdit, QSpinBox, QCheckBox, QHeaderView
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QScrollArea, QFrame
from PySide6.QtGui import QColor


class WCPage(QWidget):
    read_dtc_requested = Signal(int)   # status_mask
    snapshot_requested = Signal(int, int)
    extended_requested = Signal(int, int)
    clear_dtc_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.dtc_database = {}
        from PySide6.QtWidgets import QScrollArea, QFrame
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

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
        layout.setSpacing(10)

        # --------------------------------------------------
        # Tableau DTC (EN PREMIER)
        # --------------------------------------------------
        dtc_group = QGroupBox("WC Diagnostic Trouble Codes")
        dtc_layout = QVBoxLayout()

        self.dtc_table = QTableWidget(0, 4)
        self.dtc_table.setHorizontalHeaderLabels(
            ["DTC", "Description", "Category", "Status"]
        )

        header = self.dtc_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)

        self.dtc_table.setWordWrap(True)
        self.dtc_table.verticalHeader().setVisible(False)
        self.dtc_table.setMinimumHeight(200)
        self.dtc_table.setMaximumHeight(400)

        dtc_layout.addWidget(self.dtc_table)
        dtc_group.setLayout(dtc_layout)
        layout.addWidget(dtc_group)

        # --------------------------------------------------
        # ✅ FILTER (SOUS LE TABLEAU)
        # --------------------------------------------------
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

        # --------------------------------------------------
        # Bouton Read
        # --------------------------------------------------
        btn_layout = QHBoxLayout()

        self.read_btn = QPushButton("Read WC DTCs")
        self.read_btn.clicked.connect(self._emit_read_dtc)
        self._apply_green_button_style(self.read_btn)

        self.clear_btn = QPushButton("Clear DTCs")
        self.clear_btn.clicked.connect(self.clear_dtc_requested)
        self._apply_green_button_style(self.clear_btn)

        btn_layout.addWidget(self.read_btn)
        btn_layout.addWidget(self.clear_btn)
        layout.addLayout(btn_layout)

        # --------------------------------------------------
        # Snapshot
        # --------------------------------------------------
        snap_group = QGroupBox("Snapshot Record")
        snap_layout = QHBoxLayout()

        snap_layout.addWidget(QLabel("DTC (hex):"))
        self.snapshot_dtc = QLineEdit()
        self.snapshot_dtc.setPlaceholderText("e.g., B22101")
        snap_layout.addWidget(self.snapshot_dtc)

        snap_layout.addWidget(QLabel("Record:"))
        self.snapshot_record = QSpinBox()
        self.snapshot_record.setRange(1, 10)
        snap_layout.addWidget(self.snapshot_record)

        self.snapshot_btn = QPushButton("Get Snapshot")
        self.snapshot_btn.clicked.connect(self._emit_snapshot)
        self._apply_green_button_style(self.snapshot_btn)
        snap_layout.addWidget(self.snapshot_btn)

        snap_group.setLayout(snap_layout)
        layout.addWidget(snap_group)

        # --------------------------------------------------
        # Snapshot décodé
        # --------------------------------------------------
        decoded_group = QGroupBox("Decoded Snapshot")
        decoded_layout = QVBoxLayout(decoded_group)

        self.snapshot_meta = QLabel("No snapshot loaded")
        self.snapshot_meta.setStyleSheet("color: #1b5e20; font-weight: bold;")
        decoded_layout.addWidget(self.snapshot_meta)

        from PySide6.QtWidgets import QGridLayout
        snap_grid = QGridLayout()

        self._snap_labels = {}

        snap_fields = [
            ("Ignition", "ignition"),
            ("Wiper Mode", "wiper_mode"),
            ("Motor Current (mA)", "motor_current"),
            ("Blade Position (%)", "blade_position"),
        ]

        for idx, (title, key) in enumerate(snap_fields):
            row, col = idx // 2, (idx % 2) * 2

            snap_grid.addWidget(QLabel(f"{title}:"), row, col)

            val = QLabel("--")
            val.setStyleSheet("font-weight: bold; color: #2e7d32;")
            snap_grid.addWidget(val, row, col + 1)

            self._snap_labels[key] = val

        decoded_layout.addLayout(snap_grid)
        layout.addWidget(decoded_group)

        # --------------------------------------------------
        # Extended
        # --------------------------------------------------
        ext_group = QGroupBox("Extended Data")
        ext_layout = QHBoxLayout()

        ext_layout.addWidget(QLabel("DTC (hex):"))

        self.extended_dtc = QLineEdit()
        self.extended_dtc.setPlaceholderText("e.g., B22101")
        ext_layout.addWidget(self.extended_dtc)

        ext_layout.addWidget(QLabel("Record:"))

        self.extended_record = QSpinBox()
        self.extended_record.setRange(1, 10)
        ext_layout.addWidget(self.extended_record)

        self.extended_btn = QPushButton("Get Extended")
        self.extended_btn.clicked.connect(self._emit_extended)
        self._apply_green_button_style(self.extended_btn)
        ext_layout.addWidget(self.extended_btn)

        ext_group.setLayout(ext_layout)
        layout.addWidget(ext_group)

        # ── Extended Data décodé (ISO 14229-1) ────────────────────
        decoded_ext_group = QGroupBox("Decoded Extended Data")
        decoded_ext_layout = QVBoxLayout(decoded_ext_group)

        self.ext_meta_label = QLabel("No extended data loaded")
        self.ext_meta_label.setStyleSheet("color: #1b5e20; font-weight: bold;")
        decoded_ext_layout.addWidget(self.ext_meta_label)

        ext_dec_grid = QGridLayout()
        ext_dec_grid.setHorizontalSpacing(24)
        ext_dec_grid.setVerticalSpacing(10)
        self._ext_labels = {}
        ext_dec_items = [
            ("Occurrence Counter",          "occurrence_count"),
            ("Failed Cycles",               "failed_cycles"),
            ("Time Since First Occ. (s)",  "time_first_occ"),
            ("Time Since Last Occ. (s)",   "time_last_occ"),
        ]
        for idx, (title, key) in enumerate(ext_dec_items):
            row, col = idx // 2, (idx % 2) * 2
            lbl_t = QLabel(f"{title}:")
            lbl_v = QLabel("--")
            lbl_v.setStyleSheet("font-weight: bold; color: #2e7d32;")
            ext_dec_grid.addWidget(lbl_t, row, col)
            ext_dec_grid.addWidget(lbl_v, row, col + 1)
            self._ext_labels[key] = lbl_v
        decoded_ext_layout.addLayout(ext_dec_grid)
        layout.addWidget(decoded_ext_group)

        layout.addStretch()
        scroll.setWidget(container)
        outer.addWidget(scroll)
    # ------------------------------------------------------------------
    def _emit_read_dtc(self):
        if self.filter_not_active.isChecked():
            mask = 0x2F
        elif self.filter_active.isChecked():
            mask = 0x2E
        else:
            mask = 0xFF
        self.read_dtc_requested.emit(mask)

    def _emit_snapshot(self):
        try:
            dtc = int(self.snapshot_dtc.text().replace("0x",""), 16)
        except ValueError:
            return
        self.snapshot_requested.emit(dtc, self.snapshot_record.value())

    def _emit_extended(self):
        try:
            dtc = int(self.extended_dtc.text().replace("0x",""), 16)
        except ValueError:
            return
        self.extended_requested.emit(dtc, self.extended_record.value())

    def set_dtc_database(self, database: dict):
        self.dtc_database = database

    def update_dtc_table(self, dtc_list):
        color_map = {
            "electrical": "#c62828",
            "communication": "#e65100",
            "functional": "#1565c0",
            "signal_fault": "#f57f17",
            "protection": "#1b5e20",
        }

        self.dtc_table.setRowCount(len(dtc_list))
        for row, (dtc_id, status) in enumerate(dtc_list):
            info = self.dtc_database.get(dtc_id, {})
            name = info.get("name", f"0x{dtc_id:06X}")
            desc = info.get("description", "—")
            cat  = info.get("category", "—")

            self.dtc_table.setItem(row, 0, QTableWidgetItem(name))
            self.dtc_table.setItem(row, 1, QTableWidgetItem(desc))

            cat_item = QTableWidgetItem(cat)
            cat_item.setForeground(QColor(color_map.get(cat, "#A9A9A9")))
            self.dtc_table.setItem(row, 2, cat_item)

            self.dtc_table.setItem(row, 3, QTableWidgetItem(f"0x{status:02X}"))

        self.dtc_table.resizeRowsToContents()

    def display_snapshot(self, dtc_code: int, record_num: int, data: bytes):
        """
        Parse la reponse 0x59/0x04 complete.
        Format : [59 04 DTC(3) STATUS REC_NUM DID_DATA...]
        DID_DATA : suite de [DID_HI DID_LO LEN VAL...]
          F190 : ignition (1 octet)
          F191 : wiper_mode (10 octets ASCII)
          F192 : motor_current mA (2 octets)
          F193 : blade_pos (1 octet)
        """
        self.snapshot_meta.setText(
            f"DTC 0x{dtc_code:06X} | Record {record_num} | {len(data)} bytes")
        for lbl in self._snap_labels.values():
            lbl.setText("--")
        if not data or len(data) < 7:
            return

        # Les DIDs commencent a l'octet 7 (apres 59 04 DTC DTC DTC STATUS REC)
        i = 7
        parsed = {}
        while i + 2 < len(data):
            did    = (data[i] << 8) | data[i + 1]
            length = data[i + 2]
            val    = data[i + 3: i + 3 + length]
            if len(val) < length:
                break
            parsed[did] = val
            i += 3 + length

        # F190 : ignition
        if 0xF190 in parsed:
            self._snap_labels["ignition"].setText(
                "ON" if parsed[0xF190][0] else "OFF")

        # F191 : wiper mode (string ASCII null-padded)
        if 0xF191 in parsed:
            mode_str = parsed[0xF191].rstrip(b"\x00").decode("ascii", errors="ignore")
            self._snap_labels["wiper_mode"].setText(mode_str if mode_str else "--")

        # F192 : motor current mA (2 octets big-endian)
        if 0xF192 in parsed and len(parsed[0xF192]) >= 2:
            curr = (parsed[0xF192][0] << 8) | parsed[0xF192][1]
            self._snap_labels["motor_current"].setText(str(curr))

        # F193 : blade position
        if 0xF193 in parsed:
            self._snap_labels["blade_position"].setText(str(parsed[0xF193][0]))


    def display_extended(self, dtc_code: int, record_num: int, data: bytes):
        """
        Parse ISO 14229-1 Extended Data Records (0x19 0x06) pour le WC.
        Format : [59 06 DTC(3) STATUS] [recNum lenN data...] ...
        Records : 0x01 occurrence_count | 0x03 failed_cycles
                  0x03 failed_cycles    | 0x04 time_first_occ
                  0x05 time_last_occ
        """
        import struct as _struct
        if not hasattr(self, "ext_meta_label"):
            return

        self.ext_meta_label.setText(
            f"DTC 0x{dtc_code:06X} | {len(data)} bytes")
        for lbl in self._ext_labels.values():
            lbl.setText("--")

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

            if rec_num == 0x01 and rec_len == 2:
                occ = (rec_val[0] << 8) | rec_val[1]
                self._ext_labels["occurrence_count"].setText(str(occ))
            elif rec_num == 0x03 and rec_len == 1:
                self._ext_labels["failed_cycles"].setText(str(rec_val[0]))
            elif rec_num == 0x04 and rec_len == 4:
                secs = _struct.unpack(">I", rec_val)[0]
                self._ext_labels["time_first_occ"].setText(self._fmt_dur(secs))
            elif rec_num == 0x05 and rec_len == 4:
                secs = _struct.unpack(">I", rec_val)[0]
                self._ext_labels["time_last_occ"].setText(self._fmt_dur(secs))

            i += 2 + rec_len

    def _fmt_dur(self, secs: int) -> str:
        """Convertit des secondes en format lisible."""
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