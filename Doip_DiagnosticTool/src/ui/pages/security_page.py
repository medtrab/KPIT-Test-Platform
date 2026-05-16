from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox, QFrame
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, QTimer
from PySide6.QtGui import QFont

class SecurityPage(QWidget):
    seed_requested = Signal(int)  # niveau (1 ou 2)
    key_send_requested = Signal(int, bytes)  # (niveau, clé en bytes)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        self.card = QFrame()
        card = self.card
        card.setFrameShape(QFrame.StyledPanel)
        card.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d;
                border: 2px solid #2e7d32;
                border-radius: 12px;
                padding: 20px;
            }
        """)
        card_layout = QVBoxLayout(card)

        self.title_label = QLabel("Security Access")
        title = self.title_label
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #a5d6a7;")
        title.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(title)

        # Single level (Programming only)
        level_layout = QHBoxLayout()
        level_layout.addWidget(QLabel("Security Level: Programming"))
        card_layout.addLayout(level_layout)

        # Bouton requête seed
        self.request_btn = QPushButton("Request Seed")
        self.request_btn.clicked.connect(self._on_request_seed)
        card_layout.addWidget(self.request_btn)

        # Affichage seed
        seed_layout = QHBoxLayout()
        seed_layout.addWidget(QLabel("Seed:"))
        self.seed_display = QLineEdit()
        self.seed_display.setReadOnly(True)
        self.seed_display.setPlaceholderText("Seed will appear here")
        self.seed_display.setFont(QFont("Consolas", 12))
        seed_layout.addWidget(self.seed_display)
        card_layout.addLayout(seed_layout)

        # Saisie clé
        key_layout = QHBoxLayout()
        key_layout.addWidget(QLabel("Key (hex):"))
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("Enter calculated key (e.g., 33CC55AA)")
        self.key_input.setFont(QFont("Consolas", 12))
        key_layout.addWidget(self.key_input)
        card_layout.addLayout(key_layout)

        # Bouton unlock
        self.unlock_btn = QPushButton("Unlock")
        self.unlock_btn.clicked.connect(self._on_unlock)
        card_layout.addWidget(self.unlock_btn)

        # Icône cadenas et bannière
        self.lock_icon = QLabel("🔒")
        self.lock_icon.setAlignment(Qt.AlignCenter)
        self.lock_icon.setStyleSheet("font-size: 48px;")
        card_layout.addWidget(self.lock_icon)

        self.status_banner = QLabel("")
        self.status_banner.setAlignment(Qt.AlignCenter)
        self.status_banner.setVisible(False)
        card_layout.addWidget(self.status_banner)

        # Indicateur progression
        self.progress = QLabel("Security: LOCKED")
        self.progress.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(self.progress)

        layout.addWidget(card)
        layout.addStretch()

    def _on_request_seed(self):
        level = 2  # 🔥 toujours programming
        self.seed_requested.emit(level)

    def _on_unlock(self):
        key_hex = self.key_input.text().strip().replace(" ", "")
        if not key_hex:
            self.show_banner("Please enter the key", "orange")
            return
        try:
            key_bytes = bytes.fromhex(key_hex)
        except ValueError:
            self.show_banner("Invalid hex format", "red")
            return
        level = 2  # 🔥 toujours programming
        self.key_send_requested.emit(level, key_bytes)

    def display_seed(self, seed_bytes):
        self.seed_display.setText(seed_bytes.hex().upper())

    def show_unlock_success(self):
        self.show_banner("UNLOCKED – Security access granted", "green")
        self.lock_icon.setText("🔓")
        self.progress.setText("Security: UNLOCKED")

    def show_unlock_failure(self, reason=""):
        msg = f"UNLOCK FAILED – {reason}" if reason else "UNLOCK FAILED – Invalid key"
        self.show_banner(msg, "red")
        self.lock_icon.setText("🔒")

    def reset_security_state(self):
        self.lock_icon.setText("🔒")
        self.progress.setText("Security: LOCKED")
        self.seed_display.clear()
        self.key_input.clear()

    def show_banner(self, message, color):
        self.status_banner.setText(message)
        self.status_banner.setVisible(True)
        self.status_banner.setStyleSheet(f"""
            background-color: {color};
            color: white;
            border-radius: 8px;
            padding: 10px;
            font-weight: bold;
        """)
        self.anim = QPropertyAnimation(self.status_banner, b"windowOpacity")
        self.anim.setDuration(2000)
        self.anim.setStartValue(1.0)
        self.anim.setEndValue(0.0)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.start()
        QTimer.singleShot(2000, lambda: self.status_banner.setVisible(False))

    def update_security_level(self, level: int):
        self.current_level = level

        if level > 0:
            self.lock_icon.setText("🔓")
            self.progress.setText("Security: UNLOCKED")
            self.request_btn.setEnabled(False)
            self.key_input.setEnabled(False)
            self.unlock_btn.setEnabled(False)
            self.seed_display.clear()
            self.key_input.clear()
        else:
            self.lock_icon.setText("🔒")
            self.progress.setText("Security: LOCKED")
            self.request_btn.setEnabled(True)
            self.key_input.setEnabled(True)
            self.unlock_btn.setEnabled(True)
            self.seed_display.clear()
            self.key_input.clear()



