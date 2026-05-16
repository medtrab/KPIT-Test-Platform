"""
FrameMonitorWidget — Capture DoIP/UDS en temps réel via pyshark/tshark.
Style outil industriel (CANoe / Wireshark / ETAS INCA).


"""

import time
import threading
import asyncio

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem,
    QGroupBox, QHeaderView, QComboBox,
    QFileDialog, QMessageBox, QSplitter
)
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QColor, QFont

# ── Réseau ────────────────────────────────────────────────────────────
DOIP_PORT_BCM = 13400
DOIP_PORT_WC  = 13401
# ── DoIP Message Types ────────────────────────────────────────────────
DOIP_TYPES = {
    0x0000: "Generic DoIP Header NACK",
    0x0001: "Vehicle Identification Request",
    0x0004: "Vehicle Identification Response",
    0x0005: "Routing Activation Request",
    0x0006: "Routing Activation Response",
    0x0007: "Alive Check Request",
    0x0008: "Alive Check Response",
    0x4001: "DoIP Entity Status Request",
    0x4002: "DoIP Entity Status Response",
    0x8001: "Diagnostic Message",
    0x8002: "Diagnostic Message Positive ACK",
    0x8003: "Diagnostic Message Negative ACK",
}

# ── UDS Services ──────────────────────────────────────────────────────
UDS_SERVICES = {
    0x10: "DiagnosticSessionControl",
    0x11: "ECUReset",
    0x14: "ClearDiagnosticInformation",
    0x19: "ReadDTCInformation",
    0x22: "ReadDataByIdentifier",
    0x27: "SecurityAccess",
    0x28: "CommunicationControl",
    0x2E: "WriteDataByIdentifier",
    0x31: "RoutineControl",
    0x3E: "TesterPresent",
    0x50: "DiagnosticSessionControl [+]",
    0x54: "ClearDiagnosticInformation [+]",
    0x59: "ReadDTCInformation [+]",
    0x62: "ReadDataByIdentifier [+]",
    0x67: "SecurityAccess [+]",
    0x6E: "WriteDataByIdentifier [+]",
    0x71: "RoutineControl [+]",
    0x7E: "TesterPresent [+]",
    0x7F: "NegativeResponse",
}

UDS_NRC = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLength",
    0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x35: "invalidKey",
    0x36: "exceededNumberOfAttempts",
    0x37: "requiredTimeDelayNotExpired",
    0x7E: "subFunctionNotSupportedInSession",
    0x7F: "serviceNotSupportedInSession",
}


class _Signals(QObject):
    frame_captured = Signal(dict)
    error_occurred = Signal(str)
    status_changed = Signal(str)


class FrameMonitorWidget(QWidget):
    """
    Frame Monitor — capture réseau live DoIP/UDS via pyshark.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frames      = []
        self._counter     = 0
        self._capturing   = False
        self._capture_obj = None
        self._signals     = _Signals()
        self._signals.frame_captured.connect(self._on_frame)
        self._signals.error_occurred.connect(self._on_error)
        self._signals.status_changed.connect(self._on_status)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # ── Toolbar ────────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        toolbar.addWidget(QLabel("Filter:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems([
            "All Frames",
            "TX only  (PC → ECU)",
            "RX only  (ECU → PC)",
            "UDS Diagnostic only",
            "Errors only  (NRC 7F)",
        ])
        self.filter_combo.setFixedHeight(30)
        self.filter_combo.setStyleSheet(self._combo_style())
        self.filter_combo.currentTextChanged.connect(self._apply_filter)
        toolbar.addWidget(self.filter_combo)

        self.status_lbl = QLabel("● Stopped")
        self.status_lbl.setStyleSheet(
            "color:#ef4444; font-weight:bold; font-size:12px; font-family:Consolas;"
        )
        toolbar.addWidget(self.status_lbl)
        toolbar.addStretch()

        self.start_btn = QPushButton("▶  Start Capture")
        self.start_btn.setFixedHeight(30)
        self.start_btn.setStyleSheet(self._btn("#14532d", "#16a34a"))
        self.start_btn.clicked.connect(self._start)
        toolbar.addWidget(self.start_btn)

        self.stop_btn = QPushButton("■  Stop")
        self.stop_btn.setFixedHeight(30)
        self.stop_btn.setStyleSheet(self._btn("#7f1d1d", "#dc2626"))
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        toolbar.addWidget(self.stop_btn)

        self.clear_btn = QPushButton("🗑  Clear")
        self.clear_btn.setFixedHeight(30)
        self.clear_btn.setStyleSheet(self._btn("#37474f", "#546e7a"))
        self.clear_btn.clicked.connect(self._clear)
        toolbar.addWidget(self.clear_btn)

        self.export_btn = QPushButton("💾  Export")
        self.export_btn.setFixedHeight(30)
        self.export_btn.setStyleSheet(self._btn("#1b5e20", "#2e7d32"))
        self.export_btn.clicked.connect(self._export)
        toolbar.addWidget(self.export_btn)

        root.addLayout(toolbar)

        # ── Splitter table / detail ────────────────────────────────────
        splitter = QSplitter(Qt.Vertical)
        splitter.setStyleSheet("QSplitter::handle { background:#21262d; height:3px; }")

        # Table
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "#", "Timestamp", "Dir",
            "DoIP Type", "Src Addr", "Dst Addr",
            "UDS Service", "Payload (hex)"
        ])
        h = self.table.horizontalHeader()
        modes = [
            QHeaderView.ResizeToContents,
            QHeaderView.ResizeToContents,
            QHeaderView.ResizeToContents,
            QHeaderView.ResizeToContents,
            QHeaderView.ResizeToContents,
            QHeaderView.ResizeToContents,
            QHeaderView.ResizeToContents,
            QHeaderView.Stretch,
        ]
        for i, mode in enumerate(modes):
            h.setSectionResizeMode(i, mode)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setFont(QFont("Consolas", 11))
        self.table.setStyleSheet(self._table_style())
        self.table.setMinimumHeight(220)
        self.table.itemSelectionChanged.connect(self._on_select)
        splitter.addWidget(self.table)

        # Detail panel — 3 colonnes
        self._detail_widget = QWidget()
        detail_widget = self._detail_widget
        detail_widget.setStyleSheet("background-color:#0d1117;")
        detail_layout = QHBoxLayout(detail_widget)
        detail_layout.setContentsMargins(6, 6, 6, 6)
        detail_layout.setSpacing(8)

        self.hex_view     = self._make_detail_label("Select a frame", "#79c0ff")
        self.doip_view    = self._make_detail_label("", "#ffa657")
        self.uds_view     = self._make_detail_label("", "#a5d6a7")

        from PySide6.QtWidgets import QScrollArea

        for label, title in [
            (self.hex_view, "Hex Dump"),
            (self.doip_view, "DoIP Layer"),
            (self.uds_view, "UDS Layer"),
        ]:
            grp = QGroupBox(title)
            grp.setStyleSheet(self._detail_group_style())
            gl = QVBoxLayout(grp)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            from PySide6.QtWidgets import QFrame
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setStyleSheet("background:transparent; border:none;")
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

            container = QWidget()
            container.setStyleSheet("background:transparent;")
            cl = QVBoxLayout(container)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.addWidget(label)
            cl.addStretch()

            scroll.setWidget(container)
            gl.addWidget(scroll)
            detail_layout.addWidget(grp, 1)

        splitter.addWidget(detail_widget)
        splitter.setSizes([200, 400])
        detail_widget.setMinimumHeight(200)
        root.addWidget(splitter)

    def _make_detail_label(self, text: str, color: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(QFont("Consolas", 12))
        lbl.setStyleSheet(f"color:{color}; background:transparent; padding:4px; font-weight:bold;")
        lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        lbl.setWordWrap(True)
        return lbl

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def _start(self):
        try:
            import pyshark  # noqa
        except ImportError:
            QMessageBox.critical(self, "pyshark manquant", "pip install pyshark")
            return

        self._capturing = True
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._signals.status_changed.emit("capturing")
        threading.Thread(target=self._capture_loop, daemon=True).start()

    def _capture_loop(self):
        import pyshark

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            capture = pyshark.LiveCapture(
                interface="Ethernet",
                bpf_filter=f"tcp port {DOIP_PORT_BCM} or tcp port {DOIP_PORT_WC}",
                decode_as={f"tcp.port=={DOIP_PORT_WC}": "doip",
                           f"tcp.port=={DOIP_PORT_BCM}": "doip"},
            )
            self._capture_obj = capture

            for pkt in capture.sniff_continuously():
                if not self._capturing:
                    break
                try:
                    frame = self._parse(pkt)
                    if frame:
                        self._signals.frame_captured.emit(frame)
                except Exception:
                    continue

        except Exception as e:
            self._signals.error_occurred.emit(str(e))
        finally:
            loop.close()

    def _parse(self, pkt) -> dict:
        # IP layer
        try:
            src_ip = pkt.ip.src
            dst_ip = pkt.ip.dst
        except AttributeError:
            return None

        # Direction basée sur le port TCP — dynamique depuis pyshark
        try:
            src_port = int(pkt.tcp.srcport)
            dst_port = int(pkt.tcp.dstport)
        except AttributeError:
            return None

        if dst_port in (DOIP_PORT_BCM, DOIP_PORT_WC):
            direction = "TX"
        elif src_port in (DOIP_PORT_BCM, DOIP_PORT_WC):
            direction = "RX"
        else:
            return None

        # DoIP via tshark dissector
        doip_type_int  = None
        doip_type_name = "—"
        src_addr       = "—"
        dst_addr       = "—"
        uds_payload    = b""

        if hasattr(pkt, 'doip'):
            d = pkt.doip
            try:
                doip_type_int  = int(d.type, 16) if hasattr(d, 'type') else None
                doip_type_name = DOIP_TYPES.get(doip_type_int, f"0x{doip_type_int:04X}") \
                                 if doip_type_int is not None else "—"
            except Exception:
                pass
            try:
                src_addr = f"0x{int(d.source_address, 16):04X}" \
                           if hasattr(d, 'source_address') else "—"
            except Exception:
                pass
            try:
                dst_addr = f"0x{int(d.target_address, 16):04X}" \
                           if hasattr(d, 'target_address') else "—"
            except Exception:
                pass
            try:
                if hasattr(d, 'data'):
                    uds_payload = bytes.fromhex(d.data.replace(":", ""))
            except Exception:
                pass

        # Fallback TCP raw — DoIP header=8 bytes + adresses logiques=4 bytes = offset 12
        if not uds_payload and hasattr(pkt, 'tcp') and hasattr(pkt.tcp, 'payload'):
            try:
                raw = bytes.fromhex(pkt.tcp.payload.replace(":", ""))
                # Structure DoIP Diagnostic Message :
                # [0-1] version+inverse  [2-3] type  [4-7] length  [8-9] src addr  [10-11] dst addr  [12+] UDS
                uds_payload = raw[12:] if len(raw) > 12 else b""
            except Exception:
                pass

        if not uds_payload:
            return None

        ts  = time.strftime("%H:%M:%S.") + f"{int(time.time() * 1000) % 1000:03d}"
        hex_str = " ".join(f"{b:02X}" for b in uds_payload)

        self._counter += 1
        return {
            "num":         self._counter,
            "ts":          ts,
            "dir":         direction,
            "doip_type":   doip_type_name,
            "doip_int":    doip_type_int,
            "src_addr":    src_addr,
            "dst_addr":    dst_addr,
            "uds_service": self._uds_service_name(uds_payload),
            "hex":         hex_str,
            "raw":         uds_payload,
            "uds_decoded": self._decode_uds(uds_payload),
            "doip_detail": self._format_doip(
                doip_type_int, doip_type_name,
                src_addr, dst_addr, src_ip, dst_ip, direction,
                port=dst_port if direction == "TX" else src_port
            ),
        }

    def _stop(self):
        self._capturing = False
        if self._capture_obj:
            try:
                self._capture_obj.close()
            except Exception:
                pass
            self._capture_obj = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._signals.status_changed.emit("stopped")

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_frame(self, frame: dict):
        self._frames.append(frame)
        self._add_row(frame)

    def _on_error(self, err: str):
        self._stop()
        QMessageBox.warning(
            self, "Capture Error",
            f"pyshark error:\n{err}\n\nVérifiez que Wireshark/tshark est installé."
        )

    def _on_status(self, s: str):
        if s == "capturing":
            self.status_lbl.setText("● Capturing...")
            self.status_lbl.setStyleSheet(
                "color:#22c55e; font-weight:bold; font-size:12px; font-family:Consolas;"
            )
        else:
            self.status_lbl.setText("● Stopped")
            self.status_lbl.setStyleSheet(
                "color:#ef4444; font-weight:bold; font-size:12px; font-family:Consolas;"
            )

    # ------------------------------------------------------------------
    # Table
    # ------------------------------------------------------------------

    def _add_row(self, f: dict):
        flt = self.filter_combo.currentText()
        if "TX only"        in flt and f["dir"] != "TX":                        return
        if "RX only"        in flt and f["dir"] != "RX":                        return
        if "UDS Diagnostic" in flt and f["doip_int"] != 0x8001:                 return
        if "Errors only"    in flt and not (f["raw"] and f["raw"][0] == 0x7F):  return

        row = self.table.rowCount()
        self.table.insertRow(row)

        dir_color = QColor("#42a5f5") if f["dir"] == "TX" else QColor("#66bb6a")
        is_neg    = bool(f["raw"] and f["raw"][0] == 0x7F)
        svc_color = QColor("#ef9a9a") if is_neg else (
            QColor("#fff176") if f["dir"] == "TX" else QColor("#a5d6a7")
        )

        values = [
            str(f["num"]), f["ts"], f["dir"],
            f["doip_type"], f["src_addr"], f["dst_addr"],
            f["uds_service"], f["hex"],
        ]

        for i, val in enumerate(values):
            item = QTableWidgetItem(val)
            item.setFont(QFont("Consolas", 11))
            if i == 2:
                item.setForeground(dir_color)
                item.setFont(QFont("Consolas", 11, QFont.Bold))
                item.setTextAlignment(Qt.AlignCenter)
            elif i == 6:
                item.setForeground(svc_color)
            self.table.setItem(row, i, item)

        self.table.scrollToBottom()

    def _apply_filter(self):
        self.table.setRowCount(0)
        for f in self._frames:
            self._add_row(f)

    def _on_select(self):
        row      = self.table.currentRow()
        num_item = self.table.item(row, 0)
        if not num_item:
            return
        try:
            num = int(num_item.text())
        except ValueError:
            return
        frame = next((f for f in self._frames if f["num"] == num), None)
        if not frame:
            return

        # Hex dump 16 bytes/ligne
        raw   = frame["raw"]
        lines = []
        for i in range(0, len(raw), 16):
            chunk = raw[i:i+16]
            hex_p = " ".join(f"{b:02X}" for b in chunk)
            asc_p = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"{i:04X}  {hex_p:<47}  {asc_p}")

        self.hex_view.setText("\n".join(lines) or "—")
        self.doip_view.setText(frame["doip_detail"])
        self.uds_view.setText(frame["uds_decoded"])

    # ------------------------------------------------------------------
    # Formatage DoIP
    # ------------------------------------------------------------------

    def _format_doip(self, type_int, type_name, src_addr, dst_addr, src_ip, dst_ip, direction="TX", port=13400) -> str:
        lines = [
            "Protocol : DoIP ISO 13400-2:2012",
            f"Type     : {type_name}",
        ]
        if type_int is not None:
            lines.append(f"           (0x{type_int:04X})")
        port_label = "13401 (WC)" if port == 13401 else "13400 (BCM)"
        lines += [
            "",
            "Network",
            f"  Src IP : {src_ip}",
            f"  Dst IP : {dst_ip}",
            f"  Port   : {port_label} (TCP)",
            f"  Role   : {'Tester → ECU' if direction == 'TX' else 'ECU → Tester'}",
        ]
        if src_addr != "—" or dst_addr != "—":
            lines += [
                "",
                "Logical Addresses",
                f"  Source : {src_addr}",
                f"  Target : {dst_addr}",
            ]
        return "\n".join(lines)
    # ------------------------------------------------------------------
    # UDS decode
    # ------------------------------------------------------------------

    def _uds_service_name(self, data: bytes) -> str:
        if not data:
            return "—"
        return UDS_SERVICES.get(data[0], f"0x{data[0]:02X}")

    def _decode_uds(self, data: bytes) -> str:
        if not data:
            return "Empty"
        sid  = data[0]
        name = UDS_SERVICES.get(sid, f"Unknown (0x{sid:02X})")
        lines = [
            f"Service  : {name}",
            f"SID      : 0x{sid:02X}",
            f"Length   : {len(data)} bytes",
            "",
        ]

        if sid == 0x7F and len(data) >= 3:
            req = UDS_SERVICES.get(data[1], f"0x{data[1]:02X}")
            nrc = UDS_NRC.get(data[2], f"0x{data[2]:02X}")
            lines += [f"Req SID  : {req}  (0x{data[1]:02X})",
                      f"NRC      : {nrc}  (0x{data[2]:02X})"]
        elif sid in (0x10, 0x50) and len(data) >= 2:
            sess = {1:"Default",2:"Programming",3:"Extended"}.get(data[1], f"0x{data[1]:02X}")
            lines.append(f"Session  : {sess}  (0x{data[1]:02X})")
            if len(data) >= 6:
                p2 = (data[2] << 8) | data[3]
                lines.append(f"P2 Server: {p2} ms")
        elif sid in (0x22, 0x62) and len(data) >= 3:
            did = (data[1] << 8) | data[2]
            lines.append(f"DID      : 0x{did:04X}")
            if sid == 0x62 and len(data) > 3:
                lines.append(f"Value    : {data[3:].hex().upper()}")
        elif sid in (0x27, 0x67) and len(data) >= 2:
            sub  = data[1]
            role = "RequestSeed" if sub % 2 == 1 else "SendKey"
            lines.append(f"SubFunc  : 0x{sub:02X}  ({role})")
            if len(data) > 2:
                lines.append(f"Data     : {data[2:].hex().upper()}")
        elif sid == 0x28 and len(data) >= 2:
            ctrl = {0:"enableRxAndTx", 1:"enableRxDisableTx",
                    2:"disableRxEnableTx", 3:"disableRxAndTx"}.get(data[1], f"0x{data[1]:02X}")
            lines.append(f"Control  : {ctrl}")
        elif sid == 0x31 and len(data) >= 4:
            rid = (data[2] << 8) | data[3]
            sf  = {1:"Start",2:"Stop",3:"RequestResults"}.get(data[1], f"0x{data[1]:02X}")
            lines += [f"SubFunc  : {sf}  (0x{data[1]:02X})",
                      f"RID      : 0x{rid:04X}"]
            if len(data) > 4:
                lines.append(f"Data     : {data[4:].hex().upper()}")
        elif sid == 0x19 and len(data) >= 2:
            lines.append(f"SubFunc  : 0x{data[1]:02X}")
            if len(data) >= 3:
                lines.append(f"Mask     : 0x{data[2]:02X}")
        elif sid == 0x59 and len(data) >= 3:
            n = (len(data) - 3) // 4
            lines += [f"SubFunc  : 0x{data[1]:02X}",
                      f"Avail    : 0x{data[2]:02X}",
                      f"DTCs     : {n} found"]
        elif sid == 0x14 and len(data) >= 4:
            grp = (data[1] << 16) | (data[2] << 8) | data[3]
            lines.append(f"Group    : 0x{grp:06X}")
        elif sid == 0x3E and len(data) >= 2:
            lines.append(f"SubFunc  : 0x{data[1]:02X}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Clear / Export
    # ------------------------------------------------------------------

    def _clear(self):
        self._frames.clear()
        self._counter = 0
        self.table.setRowCount(0)
        self.hex_view.setText("Select a frame")
        self.doip_view.setText("")
        self.uds_view.setText("")

    def _export(self):
        if not self._frames:
            QMessageBox.warning(self, "Export", "No frames to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Frame Log",
            f"doip_uds_capture_{time.strftime('%Y%m%d_%H%M%S')}.txt",
            "Text Files (*.txt);;All Files (*)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("DoIP / UDS FRAME CAPTURE\n")
                f.write("=" * 100 + "\n")
                f.write(f"Export     : {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Ports      : {DOIP_PORT_BCM} (BCM) / {DOIP_PORT_WC} (WC)\n")
                f.write(f"Frames     : {len(self._frames)}\n")
                f.write("=" * 100 + "\n\n")
                f.write(
                    f"{'#':<5}{'Timestamp':<16}{'Dir':<4}"
                    f"{'DoIP Type':<36}{'Src':<8}{'Dst':<8}"
                    f"{'UDS Service':<30}{'Payload'}\n"
                )
                f.write("-" * 100 + "\n")
                for fr in self._frames:
                    f.write(
                        f"{fr['num']:<5}{fr['ts']:<16}{fr['dir']:<4}"
                        f"{fr['doip_type']:<36}{fr['src_addr']:<8}{fr['dst_addr']:<8}"
                        f"{fr['uds_service']:<30}{fr['hex']}\n"
                    )
                    decoded = fr['uds_decoded'].replace('\n', ' | ')
                    f.write(f"      UDS: {decoded}\n\n")
            QMessageBox.information(self, "Export OK", f"Saved to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))
    # ------------------------------------------------------------------
    # Styles
    # ------------------------------------------------------------------

    def _btn(self, bg, hover):
        return f"""
            QPushButton {{
                background-color:{bg}; color:#e0e0e0;
                border:1px solid {hover}; border-radius:6px;
                font-size:12px; font-weight:bold; padding:4px 10px;
            }}
            QPushButton:hover {{ background-color:{hover}; }}
            QPushButton:pressed {{ background-color:#0d1f0d; }}
            QPushButton:disabled {{ background-color:#1e1e1e; color:#555; border-color:#333; }}
        """

    def _combo_style(self):
        return """
            QComboBox {
                background-color:#1e1e1e; color:#a5d6a7;
                border:1px solid #2e7d32; border-radius:4px;
                padding:2px 8px; font-size:12px;
            }
            QComboBox QAbstractItemView {
                background-color:#1e1e1e; color:#a5d6a7;
                selection-background-color:#2e7d32;
            }
        """

    def _table_style(self):
        return """
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
        """

    def _detail_group_style(self):
        return """
            QGroupBox {
                font-weight:bold; font-size:11px; color:#a5d6a7;
                border:2px solid #546e7a; border-radius:6px;
                margin-top:0.8em; padding-top:6px;
                background-color:#0d1117;
            }
            QGroupBox::title {
                subcontrol-origin:margin; left:8px; padding:0 4px;
                color:#a5d6a7;
            }
        """