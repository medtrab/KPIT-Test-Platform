import os

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QComboBox, QFrame, QPushButton
from PySide6.QtCore import Qt, Signal
import time
from PySide6.QtGui import QPixmap

class TopBar(QWidget):
    target_changed = Signal(int)
    theme_changed = Signal(bool)  # True = dark, False = light

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(70)
        self._dark_mode = True
        self._apply_topbar_style()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 5, 20, 5)

        # Logo société
        self.logo_label = QLabel()
        _here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        pixmap = QPixmap(os.path.join(_here, "assets", "kpiy_logo.png"))
        pixmap = pixmap.scaledToHeight(52, Qt.SmoothTransformation)
        self.logo_label.setPixmap(pixmap)
        self.logo_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.logo_label.setToolTip("KPIT Engineering")
        layout.addWidget(self.logo_label)

        # Séparateur vertical après logo
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color: #2e7d32;")
        sep.setFixedWidth(2)
        layout.addWidget(sep)
        layout.addSpacing(8)

        # Sélecteur ECU
        layout.addWidget(QLabel("ECU:"))
        self.ecu_selector = QComboBox()
        self.ecu_selector.addItems(["BCM (0x700)"])
        self.ecu_selector.currentIndexChanged.connect(self._on_target_changed)
        layout.addWidget(self.ecu_selector)

        layout.addSpacing(12)

        # LEDs
        layout.addWidget(QLabel("Connection:"))
        self.conn_led = self.create_led("red")
        layout.addWidget(self.conn_led)

        layout.addSpacing(8)

        layout.addWidget(QLabel("Routing:"))
        self.routing_led = self.create_led("red")
        layout.addWidget(self.routing_led)

        layout.addSpacing(12)

        # Session / sécurité
        layout.addWidget(QLabel("Session:"))
        self.session_label = QLabel("Default")
        self.session_label.setStyleSheet("color: #a5d6a7; font-weight: bold;")
        layout.addWidget(self.session_label)

        layout.addSpacing(8)

        layout.addWidget(QLabel("Security:"))
        self.security_label = QLabel("Locked")
        self.security_label.setStyleSheet("color: #FF6347; font-weight: bold;")
        layout.addWidget(self.security_label)

        layout.addSpacing(12)

        # VIN
        layout.addWidget(QLabel("VIN:"))
        self.vin_label = QLabel("VIN12345678901234")
        self.vin_label.setStyleSheet("color: #e0e0e0;")
        layout.addWidget(self.vin_label)

        layout.addStretch()

        # Horloge
        self.time_label = QLabel()
        self.time_label.setStyleSheet("color: #a5d6a7; font-weight: bold;")
        layout.addWidget(self.time_label)
        self.update_time()

        layout.addSpacing(12)

        # Toggle theme
        self.theme_btn = QPushButton("☀ Light")
        self.theme_btn.setFixedSize(90, 36)
        self.theme_btn.setStyleSheet("""
            QPushButton {
                background-color: #37474f;
                color: #e0e0e0;
                border: 1px solid #546e7a;
                border-radius: 18px;
                font-size: 12px;
                font-weight: bold;
                padding: 0px;
            }
            QPushButton:hover { background-color: #455a64; }
            QPushButton:pressed { background-color: #263238; }
        """)
        self.theme_btn.clicked.connect(self._on_theme_toggle)
        layout.addWidget(self.theme_btn)

    def _apply_topbar_style(self):
        if self._dark_mode:
            self.setStyleSheet("""
                QWidget {
                    background-color: #1A1A1A;
                    border-bottom: 2px solid #2E7D32;
                    color: #E0E0E0;
                }
                QLabel {
                    color: #E0E0E0;
                    background-color: transparent;
                }
                QComboBox {
                    background-color: #2D2D2D;
                    color: #E0E0E0;
                    border: 1px solid #2E7D32;
                    border-radius: 4px;
                    padding: 4px 8px;
                    min-width: 100px;
                }
                QComboBox::drop-down { border: none; }
                QComboBox QAbstractItemView {
                    background-color: #2D2D2D;
                    color: #E0E0E0;
                    selection-background-color: #2E7D32;
                    border: 1px solid #37474F;
                }
            """)
        else:
            self.setStyleSheet("""
                QWidget {
                    background-color: #C5CBD0;
                    border-bottom: 2px solid #2E7D32;
                    color: #0D1F1F;
                }
                QLabel {
                    color: #0D1F1F;
                    background-color: transparent;
                }
                QComboBox {
                    background-color: #EEF1F4;
                    color: #0D1F1F;
                    border: 1.5px solid #78909C;
                    border-radius: 4px;
                    padding: 4px 8px;
                    min-width: 100px;
                }
                QComboBox:focus { border: 1.5px solid #2E7D32; }
                QComboBox::drop-down { border: none; }
                QComboBox QAbstractItemView {
                    background-color: #EEF1F4;
                    color: #0D1F1F;
                    selection-background-color: #A5D6A7;
                    border: 1px solid #90A4AE;
                }
            """)

        if not hasattr(self, 'vin_label') or not hasattr(self, 'time_label'):
            return

        if self._dark_mode:
            self.vin_label.setStyleSheet(
                "color: #B0BEC5; background: transparent; font-size: 12px;"
            )
            self.time_label.setStyleSheet(
                "color: #A5D6A7; font-weight: bold; background: transparent;"
            )
        else:
            self.vin_label.setStyleSheet(
                "color: #37474F; background: transparent; font-size: 12px;"
            )
            self.time_label.setStyleSheet(
                "color: #1B5E20; font-weight: bold; background: transparent;"
            )
    def _on_theme_toggle(self):
        self._dark_mode = not self._dark_mode
        if self._dark_mode:
            self.theme_btn.setText("☀ Light")
            self.theme_btn.setStyleSheet("""
                QPushButton {
                    background-color: #37474f;
                    color: #e0e0e0;
                    border: 1px solid #546e7a;
                    border-radius: 18px;
                    font-size: 12px;
                    font-weight: bold;
                    padding: 0px;
                }
                QPushButton:hover { background-color: #455a64; }
            """)
        else:
            self.theme_btn.setText("🌙 Dark")
            self.theme_btn.setStyleSheet("""
                QPushButton {
                    background-color: #a5d6a7;
                    color: #1a1a1a;
                    border: 1px solid #2e7d32;
                    border-radius: 18px;
                    font-size: 12px;
                    font-weight: bold;
                    padding: 0px;
                }
                QPushButton:hover { background-color: #81c784; }
            """)
        self._apply_topbar_style()
        self.theme_changed.emit(self._dark_mode)
    def create_led(self, color):
        led = QLabel()
        led.setFixedSize(16, 16)
        led.setStyleSheet(f"background-color: {color}; border-radius: 8px; border: 1px solid #444;")
        return led

    def update_time(self):
        self.time_label.setText(time.strftime("%H:%M:%S"))

    def _on_target_changed(self, index):
        from core.constants import LOGICAL_ADDR_BCM, LOGICAL_ADDR_WC
        addr = LOGICAL_ADDR_BCM if index == 0 else LOGICAL_ADDR_WC
        self.target_changed.emit(addr)

    # Méthodes de mise à jour depuis les signaux
    def set_connection_state(self, connected: bool):
        color = "green" if connected else "red"
        self.conn_led.setStyleSheet(f"background-color: {color}; border-radius: 8px; border: 1px solid #444;")

    def set_routing_state(self, active: bool):
        color = "green" if active else "red"
        self.routing_led.setStyleSheet(f"background-color: {color}; border-radius: 8px; border: 1px solid #444;")

    def set_session(self, session: int):
        names = {0x01: "Default", 0x03: "Extended"}
        self.session_label.setText(names.get(session, f"0x{session:02X}"))

    def set_security(self, level: int):
        if level >= 1:
            self.security_label.setText("Unlocked")
            self.security_label.setStyleSheet("color: #2e7d32; font-weight: bold;")
        else:
            self.security_label.setText("Locked")
            self.security_label.setStyleSheet("color: #FF6347; font-weight: bold;")
    def set_vin(self, vin: str):
        self.vin_label.setText(vin)

    def enable_wc(self):
        if self.ecu_selector.count() < 2:
            self.ecu_selector.addItem("WC (0x701)")

    def disable_wc(self):
        if self.ecu_selector.count() > 1:
            self.ecu_selector.removeItem(1)
            self.ecu_selector.setCurrentIndex(0)