"""
ui/actuator_lab_page.py

Actuator Lab — DTC injection via XCP.
Layout adapts to active target (BCM or WC).
Sends XCP SHORT_DOWNLOAD to inject signal values that trigger DTCs.
Read DTC is handled by the existing DTC Management page.
"""

import json
import os
from typing import Dict, Any, Optional

from PySide6.QtCore import Qt, Signal, QThread, QObject
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QPushButton, QSlider, QSpinBox,
    QDoubleSpinBox, QCheckBox, QScrollArea, QFrame,
    QSizePolicy, QProgressBar,
)
from PySide6.QtGui import QColor

from core.xcp_client import XCPClient, XCPError


# ── XCP worker (runs downloads in a thread) ───────────────────────────────────
class _XCPWorker(QObject):
    done     = Signal(str, bool)
    all_done = Signal(bool)

    def __init__(self, client: XCPClient, jobs: list):
        super().__init__()
        self._client = client
        self._jobs   = jobs

    def run(self):
        ok = True
        for name, addr, value, size in self._jobs:
            try:
                r = self._client.write_variable(addr, value, size)
                self.done.emit(name, r)
                ok = ok and r
            except XCPError as e:
                self.done.emit(name, False)
                ok = False
        self.all_done.emit(ok)


# ── helpers ───────────────────────────────────────────────────────────────────
def _load_xcp_config() -> dict:
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(os.path.dirname(os.path.dirname(here)), "core", "data", "xcp_variables.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _make_label(text: str, obj_name: str = "") -> QLabel:
    lbl = QLabel(text)
    if obj_name:
        lbl.setObjectName(obj_name)
    return lbl


# ── variable row widget (redesigned for professional card-like layout) ────────
class _VarRow(QWidget):
    """One variable : label | slider/checkbox | spinbox | DTC badge | condition | status."""

    def __init__(self, name: str, cfg: dict, parent=None):
        super().__init__(parent)
        self.name = name
        self.cfg  = cfg

        self._is_bool = cfg.get("unit") == "bool"
        lo = int(cfg.get("range_min", 0))
        hi = int(cfg.get("range_max", 255))

        # Main layout: horizontal for clean alignment
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(20)

        # Variable name (col 0)
        lbl = QLabel(name.replace("_", " ").title())
        lbl.setFixedWidth(200)
        lbl.setStyleSheet("color: #c8d8ca; font-size: 13px; font-weight: 500;")
        main_layout.addWidget(lbl)

        # Input area (col 1) - slider + spinbox or checkbox
        if self._is_bool:
            self._cb = QCheckBox()
            self._cb.setChecked(bool(cfg.get("default", 0)))
            self._cb.setStyleSheet("""
                QCheckBox {
                    spacing: 8px;
                    color: #c8d8ca;
                }
                QCheckBox::indicator {
                    width: 20px;
                    height: 20px;
                    border-radius: 4px;
                    border: 1px solid #2e7d32;
                    background: #111a13;
                }
                QCheckBox::indicator:checked {
                    background: #2e7d32;
                }
            """)
            main_layout.addWidget(self._cb)
            self._spin = None
            self._slider = None
        else:
            # Combined control widget with subtle border
            control_widget = QWidget()
            control_widget.setObjectName("ControlWidget")
            control_widget.setStyleSheet("""
                            #ControlWidget {
                                background: #111a13;
                                border: 1px solid #2a4a30;
                                border-radius: 8px;
                                padding: 4px 8px;
                            }
            """)
            control_layout = QHBoxLayout(control_widget)
            control_layout.setContentsMargins(8, 4, 8, 4)
            control_layout.setSpacing(16)

            self._slider = QSlider(Qt.Horizontal)
            self._slider.setRange(lo, hi)
            self._slider.setValue(int(cfg.get("default", lo)))
            self._slider.setMinimumHeight(24)
            self._slider.setStyleSheet("""
                QSlider::groove:horizontal {
                    height: 6px;
                    background: #1a2e1e;
                    border-radius: 3px;
                }
                QSlider::handle:horizontal {
                    background: #2e7d32;
                    width: 18px;
                    height: 18px;
                    margin: -6px 0;
                    border-radius: 9px;
                }
                QSlider::sub-page:horizontal {
                    background: #2e7d32;
                    border-radius: 3px;
                }
            """)
            control_layout.addWidget(self._slider, stretch=1)

            self._spin = QSpinBox()
            self._spin.setRange(lo, hi)
            self._spin.setValue(int(cfg.get("default", lo)))
            self._spin.setFixedWidth(130)
            self._spin.setSuffix(f" {cfg.get('unit', '')}")
            self._spin.setAlignment(Qt.AlignRight)
            self._spin.setStyleSheet("""
                            QSpinBox {
                                background: transparent;
                                border: none;
                                color: #c8d8ca;
                                font-size: 12px;
                                font-weight: 500;
                                padding: 6px 20px 6px 4px;
                            }
                        """)
            control_layout.addWidget(self._spin)

            main_layout.addWidget(control_widget, stretch=1)
            self._cb = None

            self._slider.valueChanged.connect(self._spin.setValue)
            self._spin.valueChanged.connect(self._slider.setValue)

        # DTC badge (col 2)
        dtc = cfg.get("dtc", "")
        badge = QLabel(dtc)
        badge.setFixedWidth(90)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet("""
            background: #132218;
            color: #5db96a;
            border: 1px solid #2a5c30;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 700;
            padding: 4px 8px;
        """)
        main_layout.addWidget(badge)

        # Pas de colonne condition ni status
        self._status = None

        # Row styling: card-like with hover effect
        self.setStyleSheet("""
            _VarRow {
                background-color: #0f1611;
                border-bottom: 1px solid #1e3322;
            }
            _VarRow:hover {
                background-color: #131e17;
            }
        """)

    def get_value(self) -> int:
        if self._is_bool:
            return 1 if self._cb.isChecked() else 0
        return self._spin.value()

    def set_status(self, ok: Optional[bool]):
        pass  # colonne status supprimée

    def reset(self):
        default = int(self.cfg.get("default", 0))
        if self._is_bool:
            self._cb.setChecked(bool(default))
        else:
            self._slider.setValue(default)
            self._spin.setValue(default)
        self.set_status(None)


# ── main page ─────────────────────────────────────────────────────────────────
class ActuatorLabPage(QWidget):

    xcp_inject_requested = Signal(dict)   # {var_name: value}

    _SS = """
    QWidget { background-color: #0d1214; color: #c8d8ca; }
    QGroupBox {
        border: 2px solid #2e7d32; border-radius: 12px;
        margin-top: 1.2em; font-size: 14px; font-weight: 700;
        color: #a5d6a7; background-color: #0a0f11;
        padding: 12px 8px 8px 8px;
    }
    QGroupBox::title {
        subcontrol-origin: margin; left: 16px;
        padding: 0 8px; background-color: #0a0f11;
    }
    QScrollArea { border: none; background: transparent; }
    QScrollBar:vertical { background: #1a2a1c; width: 10px; border-radius: 5px; margin: 2px; }
    QScrollBar::handle:vertical { background: #2e7d32; border-radius: 5px; min-height: 20px; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
    QPushButton#InjectBtn {
        background: #1f6b28; color: #e8f5e9; border: none;
        border-radius: 10px; font-size: 14px; font-weight: 700;
        padding: 12px 32px; min-height: 48px;
    }
    QPushButton#InjectBtn:hover   { background: #27832f; }
    QPushButton#InjectBtn:pressed { background: #155520; }
    QPushButton#InjectBtn:disabled { background: #1a2a1c; color: #3a5a3e; }
    QPushButton#ResetBtn {
        background: #1a2e22; color: #7aab80;
        border: 1px solid #2a4a30; border-radius: 10px;
        font-size: 13px; font-weight: 600;
        padding: 11px 28px; min-height: 48px;
    }
    QPushButton#ResetBtn:hover { background: #223a28; }
    QPushButton#ConnectBtn {
        background: #0d2e15; color: #5db96a;
        border: 1px solid #2a5c30; border-radius: 8px;
        font-size: 12px; font-weight: 600;
        padding: 8px 20px; min-height: 38px;
    }
    QPushButton#ConnectBtn:hover { background: #142a1a; }
    QProgressBar {
        border: 1px solid #2a5c30; border-radius: 6px;
        background: #0d1a10; text-align: center;
        color: #a5d6a7; font-size: 11px; font-weight: bold; max-height: 20px;
    }
    QProgressBar::chunk { background: #2e7d32; border-radius: 5px; }
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(self._SS)
        self._cfg = _load_xcp_config()
        self._target = "bcm"
        self._xcp = None
        self._var_rows: Dict[str, _VarRow] = {}
        self._thread: Optional[QThread] = None
        self._dark_mode = True

        self._build_xcp_client()
        self._build_ui()

    def apply_theme(self, dark_mode: bool):
            self._dark_mode = dark_mode
            if dark_mode:
                self.setStyleSheet(self._SS)
                self._header_frame.setStyleSheet("""
                    #HeaderFrame {
                        background-color: #0a0f11;
                        border: 1px solid #2a4a30;
                        border-radius: 12px;
                        padding: 8px 16px;
                    }
                """)
                self._title_lbl.setStyleSheet(
                    "font-size: 18px; font-weight: 800; color: #a5d6a7;"
                )
            else:
                self.setStyleSheet("""
    QWidget { background-color: #D6DBE0; color: #0d1f0d; }
    QGroupBox {
        border: 1.5px solid #90A4AE; border-radius: 12px;
        margin-top: 1.2em; font-size: 14px; font-weight: 700;
        color: #1b5e20; background-color: #E2E7EB;
        padding: 12px 8px 8px 8px;
    }
    QGroupBox::title {
        subcontrol-origin: margin; left: 16px;
        padding: 0 8px; background-color: #E2E7EB;
    }
    QScrollArea { border: none; background: transparent; }
    QScrollBar:vertical { background: #C5CBD0; width: 10px; border-radius: 5px; margin: 2px; }
    QScrollBar::handle:vertical { background: #78909C; border-radius: 5px; min-height: 20px; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
    QPushButton#InjectBtn {
        background: #2e7d32; color: #ffffff; border: none;
        border-radius: 10px; font-size: 14px; font-weight: 700;
        padding: 12px 32px; min-height: 48px;
    }
    QPushButton#InjectBtn:hover   { background: #1b5e20; }
    QPushButton#InjectBtn:pressed { background: #0d4d0d; }
    QPushButton#InjectBtn:disabled { background: #90A4AE; color: #CFD8DC; }
    QPushButton#ResetBtn {
        background: #E2E7EB; color: #1b5e20;
        border: 1px solid #2e7d32; border-radius: 10px;
        font-size: 13px; font-weight: 600;
        padding: 11px 28px; min-height: 48px;
    }
    QPushButton#ResetBtn:hover { background: #B0BEC5; }
    QPushButton#ConnectBtn {
        background: #E2E7EB; color: #1b5e20;
        border: 1px solid #2e7d32; border-radius: 8px;
        font-size: 12px; font-weight: 600;
        padding: 8px 20px; min-height: 38px;
    }
    QPushButton#ConnectBtn:hover { background: #B0BEC5; }
    QProgressBar {
        border: 1px solid #78909C; border-radius: 6px;
        background: #B0BEC5; text-align: center;
        color: #0d1f0d; font-size: 11px; font-weight: bold; max-height: 20px;
    }
    QProgressBar::chunk { background: #2e7d32; border-radius: 5px; }
    """)
                self._header_frame.setStyleSheet("""
                    #HeaderFrame {
                        background-color: #E2E7EB;
                        border: 1px solid #90A4AE;
                        border-radius: 12px;
                        padding: 8px 16px;
                    }
                """)
                self._title_lbl.setStyleSheet(
                    "font-size: 18px; font-weight: 800; color: #1b5e20;"
                )
            self._apply_rows_theme(dark_mode)

    def _apply_rows_theme(self, dark_mode: bool):
            """Met à jour les styles hardcodés dans chaque _VarRow."""
            # Header widget
            if hasattr(self, '_hdr_widget') and self._hdr_widget:
                bg = "#0e1a12" if dark_mode else "#B0BEC5"
                self._hdr_widget.setStyleSheet(f"""
                    background-color: {bg};
                    border-radius: 8px;
                    margin: 4px 0px;
                """)

            for row in self._var_rows.values():
                # Label nom
                lbl_color = "#c8d8ca" if dark_mode else "#0d1f0d"
                for child in row.findChildren(QLabel):
                    if child.minimumWidth() == 200 or child.maximumWidth() == 200:
                        child.setStyleSheet(
                            f"color: {lbl_color}; font-size: 13px; font-weight: 500;"
                        )

                if hasattr(row, '_spin') and row._spin:
                    if dark_mode:
                        row._spin.setStyleSheet("""
                            QSpinBox {
                                background: transparent; border: none;
                                color: #c8d8ca; font-size: 12px;
                                font-weight: 500; padding: 6px 20px 6px 4px;
                            }
                        """)
                        for cw in row.findChildren(QWidget):
                            if cw.objectName() == "ControlWidget":
                                cw.setStyleSheet("""
                                    #ControlWidget {
                                        background: #111a13;
                                        border: 1px solid #2a4a30;
                                        border-radius: 8px;
                                        padding: 4px 8px;
                                    }
                                """)
                    else:
                        row._spin.setStyleSheet("""
                                                QSpinBox {
                                                    background: #ffffff;
                                                    border: 1px solid #90A4AE;
                                                    border-radius: 4px;
                                                    color: #0d1f0d; font-size: 12px;
                                                    font-weight: 500; padding: 6px 20px 6px 4px;
                                                }
                         """)
                        for cw in row.findChildren(QWidget):
                            if cw.objectName() == "ControlWidget":
                                cw.setStyleSheet("""
                                    #ControlWidget {
                                        background: #ffffff;
                                        border: 1px solid #90A4AE;
                                        border-radius: 8px;
                                        padding: 4px 8px;
                                    }
                                """)

                if hasattr(row, '_cb') and row._cb:
                    if dark_mode:
                        row._cb.setStyleSheet("""
                            QCheckBox { spacing: 8px; color: #c8d8ca; }
                            QCheckBox::indicator {
                                width: 20px; height: 20px;
                                border-radius: 4px;
                                border: 1px solid #2e7d32;
                                background: #111a13;
                            }
                            QCheckBox::indicator:checked { background: #2e7d32; }
                        """)
                    else:
                        row._cb.setStyleSheet("""
                            QCheckBox { spacing: 8px; color: #0d1f0d; }
                            QCheckBox::indicator {
                                width: 20px; height: 20px;
                                border-radius: 4px;
                                border: 1.5px solid #546E7A;
                                background: #EEF1F4;
                            }
                            QCheckBox::indicator:checked { background: #2e7d32; }
                        """)

    # ── XCP client setup ──────────────────────────────────────────────────────

    def _build_xcp_client(self, target: str = "bcm"):
        if target == "wc":
            transport = self._cfg.get("xcp_transport_wc",
                                      self._cfg.get("xcp_transport", {}))
        else:
            transport = self._cfg.get("xcp_transport", {})
        host    = transport.get("host", "127.0.0.1")
        port    = int(transport.get("port", 17725 if target == "bcm" else 17726))
        timeout = float(transport.get("timeout_s", 1.0))
        self._xcp = XCPClient(host, port, timeout)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(20)

        # ── top bar (card-like header) ────────────────────────────────────────
        self._header_frame = QFrame()
        header_frame = self._header_frame
        header_frame.setObjectName("HeaderFrame")
        header_frame.setStyleSheet("""
                    #HeaderFrame {
                        background-color: #0a0f11;
                        border: 1px solid #2a4a30;
                        border-radius: 12px;
                        padding: 8px 16px;
                    }
        """)
        top = QHBoxLayout(header_frame)
        top.setContentsMargins(12, 8, 12, 8)
        top.setSpacing(20)

        self._title_lbl = QLabel("Actuator Lab — DTC Injection")
        title = self._title_lbl
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: #a5d6a7;")
        top.addWidget(title)

        top.addStretch()

        self._target_badge = QLabel("TARGET: BCM")
        self._target_badge.setStyleSheet("""
            background: #132218;
            color: #5db96a;
            border: 1px solid #2a5c30;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 700;
            padding: 6px 16px;
        """)
        top.addWidget(self._target_badge)

        self._connect_btn = QPushButton("XCP Connect")
        self._connect_btn.setObjectName("ConnectBtn")
        self._connect_btn.clicked.connect(self._on_xcp_connect)
        top.addWidget(self._connect_btn)

        self._xcp_status = QLabel("● Disconnected")
        self._xcp_status.setStyleSheet("color: #4a5c4e; font-size: 13px; font-weight: 600;")
        top.addWidget(self._xcp_status)

        root.addWidget(header_frame)

        # ── progress bar ──────────────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFormat("Ready")
        root.addWidget(self._progress)

        # ── variables group box with scroll (card style) ──────────────────────
        vars_group = QGroupBox("Calibration Variables")
        vars_layout = QVBoxLayout(vars_group)
        vars_layout.setContentsMargins(8, 16, 8, 12)
        vars_layout.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll_container = QWidget()
        self._scroll_layout = QVBoxLayout(self._scroll_container)
        self._scroll_layout.setContentsMargins(0, 0, 0, 0)
        self._scroll_layout.setSpacing(0)
        self._scroll.setWidget(self._scroll_container)
        vars_layout.addWidget(self._scroll)

        root.addWidget(vars_group, 1)

        # ── bottom buttons ────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(20)

        self._inject_btn = QPushButton("Inject Values")
        self._inject_btn.setObjectName("InjectBtn")
        self._inject_btn.setEnabled(False)
        self._inject_btn.clicked.connect(self._on_inject)
        btn_row.addWidget(self._inject_btn)

        self._reset_btn = QPushButton("Reset Defaults")
        self._reset_btn.setObjectName("ResetBtn")
        self._reset_btn.clicked.connect(self._on_reset)
        btn_row.addWidget(self._reset_btn)

        btn_row.addStretch()

        self._result_label = QLabel("")
        self._result_label.setStyleSheet("font-size: 14px; font-weight: 600;")
        btn_row.addWidget(self._result_label)

        root.addLayout(btn_row)

        self._rebuild_rows()

    # ── variable rows ─────────────────────────────────────────────────────────

    def _rebuild_rows(self):
        """Clear and rebuild variable rows for current target."""
        while self._scroll_layout.count():
            item = self._scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._var_rows.clear()

        vars_cfg: dict = self._cfg.get(self._target, {})

        if not vars_cfg:
            lbl = QLabel(f"No XCP variables configured for target '{self._target}'.")
            lbl.setStyleSheet("color: #4a5c4e; font-size: 14px; padding: 30px;")
            lbl.setAlignment(Qt.AlignCenter)
            self._scroll_layout.addWidget(lbl)
            self._scroll_layout.addStretch()
            return

        # Header row (sticky, visually separated)
        self._hdr_widget = QFrame()
        hdr_widget = self._hdr_widget
        bg = "#0e1a12" if self._dark_mode else "#B0BEC5"
        hdr_widget.setStyleSheet(f"""
                    background-color: {bg};
                    border-radius: 8px;
                    margin: 4px 0px;
                """)
        hdr_layout = QHBoxLayout(hdr_widget)
        hdr_layout.setContentsMargins(16, 10, 16, 10)
        hdr_layout.setSpacing(20)

        hdr_layout.addWidget(self._header_label("Variable", 200))
        hdr_layout.addWidget(self._header_label("Value / Control", 0, stretch=1))
        hdr_layout.addWidget(self._header_label("DTC", 90))

        self._scroll_layout.addWidget(hdr_widget)

        # Separator line
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("background: #1e3a22; border: none;")
        sep.setFixedHeight(2)
        self._scroll_layout.addWidget(sep)

        for name, cfg in vars_cfg.items():
            row = _VarRow(name, cfg)
            self._var_rows[name] = row
            self._scroll_layout.addWidget(row)

        self._scroll_layout.addStretch()

    def _header_label(self, text: str, width: int = 0, stretch: int = 0) -> QLabel:
        lbl = QLabel(text)
        if width > 0:
            lbl.setFixedWidth(width)
        lbl.setStyleSheet("color: #1b5e20; font-size: 12px; font-weight: 700; letter-spacing: 0.5px;")

        if stretch:
            # For the control column, allow stretching
            pass
        return lbl

    # ── slots ─────────────────────────────────────────────────────────────────

    def set_target(self, addr: int):
        """Called by main_window when target changes."""
        new_target = "wc" if addr == 0x0701 else "bcm"
        if new_target != self._target:
            if self._xcp and self._xcp.connected:
                self._xcp.disconnect()
                self._xcp_status.setText("\u25cf Disconnected")
                self._xcp_status.setStyleSheet("color: #4a5c4e; font-size: 13px; font-weight: 600;")
                self._connect_btn.setText("XCP Connect")
                self._inject_btn.setEnabled(False)
            self._target = new_target
            self._build_xcp_client(self._target)
        self._target_badge.setText(f"TARGET: {'WC' if self._target == 'wc' else 'BCM'}")
        self._rebuild_rows()
        # Réappliquer le thème courant après rebuild
        self._apply_rows_theme(self._dark_mode)
        self._result_label.setText("")

    def _on_xcp_connect(self):
        if self._xcp.connected:
            self._xcp.disconnect()
            self._xcp_status.setText("● Disconnected")
            self._xcp_status.setStyleSheet("color: #4a5c4e; font-size: 13px; font-weight: 600;")
            self._connect_btn.setText("XCP Connect")
            self._inject_btn.setEnabled(False)
        else:
            ok = self._xcp.connect()
            if ok:
                self._xcp_status.setText("● Connected")
                self._xcp_status.setStyleSheet("color: #4caf50; font-size: 13px; font-weight: 600;")
                self._connect_btn.setText("XCP Disconnect")
                self._inject_btn.setEnabled(True)
                self._result_label.setText("")
            else:
                self._xcp_status.setText("● Failed")
                self._xcp_status.setStyleSheet("color: #ef5350; font-size: 13px; font-weight: 600;")
                self._result_label.setText("XCP connection failed — check server")
                self._result_label.setStyleSheet("color: #ef5350; font-size: 14px;")

    def _on_inject(self):
        if not self._xcp.connected:
            self._result_label.setText("Not connected to XCP server")
            self._result_label.setStyleSheet("color: #ef5350;")
            return

        if not self._var_rows:
            return

        vars_cfg = self._cfg.get(self._target, {})
        jobs = []
        for name, row in self._var_rows.items():
            cfg  = vars_cfg.get(name, {})
            addr = int(cfg.get("address", "0x0"), 16)
            size = int(cfg.get("size", 1))
            val  = row.get_value()
            jobs.append((name, addr, val, size))
            row.set_status(None)

        total = len(jobs)
        self._progress.setRange(0, total)
        self._progress.setValue(0)
        self._progress.setFormat("Injecting…")
        self._inject_btn.setEnabled(False)
        self._result_label.setText("")

        self._worker = _XCPWorker(self._xcp, jobs)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._on_job_done)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.all_done.connect(self._thread.quit)
        self._thread.start()

        self._inject_count = 0

    def _on_job_done(self, name: str, ok: bool):
        if name in self._var_rows:
            self._var_rows[name].set_status(ok)
        self._inject_count = getattr(self, "_inject_count", 0) + 1
        self._progress.setValue(self._inject_count)

    def _on_all_done(self, success: bool):
        self._inject_btn.setEnabled(True)
        total = len(self._var_rows)
        if success:
            self._progress.setFormat(f"Done — {total}/{total} injected")
            self._result_label.setText(f"✓ {total} variables injected successfully")
            self._result_label.setStyleSheet("color: #4caf50; font-size: 14px; font-weight: 600;")
        else:
            self._progress.setFormat(f"Some failed — check XCP server")
            self._result_label.setText(f"⚠ Some variables failed — check XCP server")
            self._result_label.setStyleSheet("color: #ff9800; font-size: 14px; font-weight: 600;")

    def _on_reset(self):
        for row in self._var_rows.values():
            row.reset()
        self._progress.setValue(0)
        self._progress.setFormat("Ready")
        self._result_label.setText("")