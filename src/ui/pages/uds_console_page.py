"""
UDS Console — Envoi manuel de trames UDS brutes.
Style outil industriel (CANoe / INCA / CANalyzer).
"""

import time
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QTableWidget, QTableWidgetItem,
    QGroupBox, QGridLayout, QHeaderView, QFrame,
    QFileDialog, QMessageBox, QComboBox
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut


# Décodage service ID UDS
UDS_SERVICE_NAMES = {
    0x10: "DiagnosticSessionControl",
    0x11: "ECUReset",
    0x14: "ClearDTC",
    0x19: "ReadDTCInformation",
    0x22: "ReadDataByIdentifier",
    0x27: "SecurityAccess",
    0x28: "CommunicationControl",
    0x2E: "WriteDataByIdentifier",
    0x31: "RoutineControl",
    0x3E: "TesterPresent",
    0x50: "DiagnosticSessionControl [+]",
    0x51: "ECUReset [+]",
    0x54: "ClearDTC [+]",
    0x59: "ReadDTCInformation [+]",
    0x62: "ReadDataByIdentifier [+]",
    0x67: "SecurityAccess [+]",
    0x6E: "WriteDataByIdentifier [+]",
    0x71: "RoutineControl [+]",
    0x7E: "TesterPresent [+]",
    0x7F: "NegativeResponse",
}

UDS_NRC_NAMES = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLengthOrInvalidFormat",
    0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError",
    0x25: "noResponseFromSubnetComponent",
    0x26: "failurePreventsExecutionOfRequestedAction",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x35: "invalidKey",
    0x36: "exceededNumberOfAttempts",
    0x37: "requiredTimeDelayNotExpired",
    0x70: "uploadDownloadNotAccepted",
    0x71: "transferDataSuspended",
    0x72: "generalProgrammingFailure",
    0x73: "wrongBlockSequenceCounter",
    0x78: "requestCorrectlyReceivedResponsePending",
    0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
}

# Raccourcis UDS communs
QUICK_COMMANDS = [
    ("Tester Present",    "3E 00"),
    ("Default Session",   "10 01"),
    ("Extended Session",  "10 03"),
    ("Request Seed Lvl1", "27 01"),
    ("Read All DTC",      "19 02 FF"),
    ("Clear All DTC",     "14 FF FF FF"),
]


class UDSConsolePage(QWidget):
    """Page console UDS — envoi de trames brutes hex."""

    raw_uds_requested = Signal(bytes)   # signal vers le controller

    def __init__(self, parent=None):
        super().__init__(parent)
        self._history = []
        self._dark_mode = True
        self._build_ui()

    # ------------------------------------------------------------------
    # Construction UI
    # ------------------------------------------------------------------
    def set_theme(self, dark_mode: bool):
        self._dark_mode = dark_mode
        hex_color = QColor("#e0e0e0") if dark_mode else QColor("#0d1f0d")
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 2)
            if item:
                item.setForeground(hex_color)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ── Titre ──────────────────────────────────────────────────────

        title_row = QHBoxLayout()
        title = QLabel("UDS Console")
        title.setStyleSheet("font-size: 20px; font-weight: 900; color: #1b5e20; letter-spacing: 1px;")
        title_row.addWidget(title)
        title_row.addStretch()

        self.clear_btn = QPushButton("🗑  Clear")
        self.clear_btn.setFixedHeight(32)
        self.clear_btn.setStyleSheet(self._btn_style("#37474f", "#546e7a"))
        self.clear_btn.clicked.connect(self._clear_history)
        title_row.addWidget(self.clear_btn)

        self.export_btn = QPushButton("💾  Export")
        self.export_btn.setFixedHeight(32)
        self.export_btn.setStyleSheet(self._btn_style("#1b5e20", "#2e7d32"))
        self.export_btn.clicked.connect(self._export)
        title_row.addWidget(self.export_btn)

        root.addLayout(title_row)

        # ── Historique ─────────────────────────────────────────────────
        hist_group = QGroupBox("Transaction Log")
        hist_layout = QVBoxLayout(hist_group)
        hist_layout.setContentsMargins(6, 6, 6, 6)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Timestamp", "Dir", "Raw (hex)", "Service", "Details"]
        )
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setFont(QFont("Consolas", 11))
        self.table.setMinimumHeight(320)

        hist_layout.addWidget(self.table)
        root.addWidget(hist_group, stretch=1)

        # ── Séparateur ─────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #2e7d32;")
        root.addWidget(sep)

        # ── Raccourcis ─────────────────────────────────────────────────
        quick_group = QGroupBox("Quick Commands")
        quick_layout = QGridLayout(quick_group)
        quick_layout.setSpacing(6)

        for i, (label, cmd) in enumerate(QUICK_COMMANDS):
            btn = QPushButton(label)
            btn.setFixedHeight(30)
            btn.setStyleSheet(self._btn_style("#2e7d32", "#1b5e20"))
            btn.setToolTip(f"Send: {cmd}")
            btn.clicked.connect(lambda _, c=cmd: self._send_quick(c))
            quick_layout.addWidget(btn, i // 5, i % 5)

        root.addWidget(quick_group)

        # ── Zone de saisie ─────────────────────────────────────────────
        input_group = QGroupBox("Command Input")
        input_layout = QVBoxLayout(input_group)
        input_layout.setContentsMargins(8, 8, 8, 8)
        input_layout.setSpacing(8)

        # Ligne de saisie
        cmd_row = QHBoxLayout()

        prompt = QLabel("TX >")
        prompt.setStyleSheet(
            "color: #1b5e20; font-weight: bold; font-size: 14px; font-family: Consolas;"
        )
        prompt.setFixedWidth(48)
        cmd_row.addWidget(prompt)

        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText(
            "Enter UDS hex bytes  (e.g.  22 F2 00  or  10 03  or  19 02 FF)"
        )
        self.cmd_input.setFont(QFont("Consolas", 13))
        self.cmd_input.setFixedHeight(40)
        self.cmd_input.setStyleSheet("")
        self.cmd_input.returnPressed.connect(self._on_send)
        cmd_row.addWidget(self.cmd_input, stretch=1)

        self.send_btn = QPushButton("  Send  ↵")
        self.send_btn.setFixedSize(100, 40)
        self.send_btn.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.send_btn.setStyleSheet(self._btn_style("#1b5e20", "#2e7d32", size=13))
        self.send_btn.clicked.connect(self._on_send)
        cmd_row.addWidget(self.send_btn)

        input_layout.addLayout(cmd_row)

        # Status bar
        self.status_label = QLabel("Ready — type a UDS command and press Enter or Send")
        self.status_label.setStyleSheet("color: #78909c; font-size: 12px; font-family: Consolas;")
        input_layout.addWidget(self.status_label)

        root.addWidget(input_group)

    # ------------------------------------------------------------------
    # Logique envoi
    # ------------------------------------------------------------------

    def _on_send(self):
        raw = self.cmd_input.text().strip()
        if not raw:
            return

        # Parser les bytes hex
        try:
            parts = raw.replace(",", " ").split()
            data = bytes(int(p, 16) for p in parts)
        except ValueError:
            self._set_status(f"❌  Invalid hex: '{raw}'", error=True)
            return

        if len(data) == 0:
            return

        self._set_status(f"✔  Sent: {' '.join(f'{b:02X}' for b in data)}")
        self.raw_uds_requested.emit(data)
        self.cmd_input.clear()


    def _send_quick(self, cmd: str):
        self.cmd_input.setText(cmd)
        self._on_send()

    # ------------------------------------------------------------------
    # Réception réponse (appelé depuis main_window)
    # ------------------------------------------------------------------

    def handle_response(self, response: bytes):
        """Appelé par main_window quand une réponse UDS arrive."""
        if not response:
            return
        hex_str = " ".join(f"{b:02X}" for b in response)
        service, details = self._decode_response(response)
        self._add_row("RX", hex_str, service, details, sent=False)

    # ------------------------------------------------------------------
    # Table
    # ------------------------------------------------------------------

    def _add_row(self, direction: str, hex_str: str, service: str, details: str, sent: bool):
        ts = time.strftime("%H:%M:%S.") + f"{int(time.time() * 1000) % 1000:03d}"
        self._history.append((ts, direction, hex_str, service, details))

        row = self.table.rowCount()
        self.table.insertRow(row)

        items = [
            QTableWidgetItem(ts),
            QTableWidgetItem(direction),
            QTableWidgetItem(hex_str),
            QTableWidgetItem(service),
            QTableWidgetItem(details),
        ]

        # Couleurs
        dir_color = QColor("#42a5f5") if sent else QColor("#2e7d32")
        hex_color = QColor("#e0e0e0") if self._dark_mode else QColor("#0d1f0d")
        svc_color = QColor("#e65100") if sent else QColor("#1b5e20")
        if "Negative" in service or "NRC" in details:
            svc_color = QColor("#c62828")

        for i, item in enumerate(items):
            item.setFont(QFont("Consolas", 11))
            if i == 1:
                item.setForeground(dir_color)
                item.setFont(QFont("Consolas", 11, QFont.Bold))
                item.setTextAlignment(Qt.AlignCenter)
            elif i == 2:
                item.setForeground(hex_color)
            elif i == 3:
                item.setForeground(svc_color)
            self.table.setItem(row, i, item)

        self.table.scrollToBottom()

    # ------------------------------------------------------------------
    # Décodage UDS
    # ------------------------------------------------------------------

    def _decode_request(self, data: bytes):
        sid = data[0]
        name = UDS_SERVICE_NAMES.get(sid, f"Unknown (0x{sid:02X})")
        details = ""

        if sid == 0x10 and len(data) >= 2:
            sess = {0x01: "Default", 0x02: "Programming", 0x03: "Extended"}.get(data[1], f"0x{data[1]:02X}")
            details = f"Session={sess}"
        elif sid == 0x22 and len(data) >= 3:
            did = (data[1] << 8) | data[2]
            details = f"DID=0x{did:04X}"
        elif sid == 0x2E and len(data) >= 3:
            did = (data[1] << 8) | data[2]
            val = data[3:].hex().upper() if len(data) > 3 else ""
            details = f"DID=0x{did:04X}  Value={val}"
        elif sid == 0x27 and len(data) >= 2:
            details = f"SubFunc=0x{data[1]:02X} ({'RequestSeed' if data[1] % 2 == 1 else 'SendKey'})"
        elif sid == 0x19 and len(data) >= 2:
            details = f"SubFunc=0x{data[1]:02X}"
            if len(data) >= 3:
                details += f"  Mask=0x{data[2]:02X}"
        elif sid == 0x31 and len(data) >= 4:
            rid = (data[2] << 8) | data[3]
            sf = {0x01: "Start", 0x02: "Stop", 0x03: "RequestResults"}.get(data[1], f"0x{data[1]:02X}")
            details = f"{sf}  RID=0x{rid:04X}"
        elif sid == 0x14 and len(data) >= 4:
            grp = (data[1] << 16) | (data[2] << 8) | data[3]
            details = f"Group=0x{grp:06X}"
        elif sid == 0x3E and len(data) >= 2:
            details = f"SubFunc=0x{data[1]:02X}"
        else:
            details = data[1:].hex().upper() if len(data) > 1 else ""

        return name, details

    def _decode_response(self, data: bytes):
        if not data:
            return "Empty", ""

        sid = data[0]
        name = UDS_SERVICE_NAMES.get(sid, f"Unknown (0x{sid:02X})")
        details = ""

        if sid == 0x7F:
            # Negative response
            req_sid = data[1] if len(data) > 1 else 0
            nrc = data[2] if len(data) > 2 else 0
            nrc_name = UDS_NRC_NAMES.get(nrc, f"0x{nrc:02X}")
            req_name = UDS_SERVICE_NAMES.get(req_sid, f"0x{req_sid:02X}")
            details = f"ReqService={req_name}  NRC=0x{nrc:02X} ({nrc_name})"
        elif sid == 0x62 and len(data) >= 3:
            did = (data[1] << 8) | data[2]
            val = data[3:].hex().upper() if len(data) > 3 else ""
            details = f"DID=0x{did:04X}  Value={val}"
        elif sid == 0x67 and len(data) >= 2:
            details = f"SubFunc=0x{data[1]:02X}  Seed={data[2:].hex().upper()}"
        elif sid == 0x71 and len(data) >= 4:
            rid = (data[2] << 8) | data[3]
            sf = {0x01: "Started", 0x02: "Stopped", 0x03: "Results"}.get(data[1], f"0x{data[1]:02X}")
            details = f"{sf}  RID=0x{rid:04X}"
        elif sid == 0x50 and len(data) >= 2:
            sess = {0x01: "Default", 0x02: "Programming", 0x03: "Extended"}.get(data[1], f"0x{data[1]:02X}")
            details = f"Session={sess}"
        else:
            details = data[1:].hex().upper() if len(data) > 1 else ""

        return name, details

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _clear_history(self):
        self._history.clear()
        self.table.setRowCount(0)
        self._set_status("Log cleared")

    def _export(self):
        if not self._history:
            QMessageBox.warning(self, "Export", "No data to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export UDS Log",
            f"uds_log_{time.strftime('%Y%m%d_%H%M%S')}.txt",
            "Text Files (*.txt);;All Files (*)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("UDS CONSOLE LOG\n")
                f.write("=" * 80 + "\n")
                f.write(f"Export: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("=" * 80 + "\n\n")
                f.write(f"{'Timestamp':<16} {'Dir':<4} {'Raw (hex)':<30} {'Service':<35} {'Details'}\n")
                f.write("-" * 120 + "\n")
                for ts, direction, hex_str, service, details in self._history:
                    f.write(f"{ts:<16} {direction:<4} {hex_str:<30} {service:<35} {details}\n")
            QMessageBox.information(self, "Export", f"Log exported to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))

    def _set_status(self, msg: str, error: bool = False):
        color = "#ef9a9a" if error else "#78909c"
        self.status_label.setStyleSheet(f"color: {color}; font-size: 12px; font-family: Consolas;")
        self.status_label.setText(msg)

    def _btn_style(self, bg: str, hover: str, size: int = 12) -> str:
        return f"""
            QPushButton {{
                background-color: {bg};
                color: #ffffff;
                border: 1px solid {hover};
                border-radius: 6px;
                font-size: {size}px;
                font-weight: bold;
                padding: 4px 10px;
            }}
            QPushButton:hover {{ background-color: {hover}; }}
            QPushButton:pressed {{ background-color: #0d4d0d; }}
        """

    def handle_sent(self, data: bytes):
        """Appelé automatiquement pour chaque trame TX envoyée depuis n'importe quelle page."""
        hex_str = " ".join(f"{b:02X}" for b in data)
        service, details = self._decode_request(data)
        self._add_row("TX", hex_str, service, details, sent=True)