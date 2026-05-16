"""
ui/splash_screen.py — HIL Diagnostic Platform Splash Screen
Professional automotive diagnostic tool style (CANoe / INCA / BMW ISTA)
"""

import os
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QFont, QColor, QPainter, QLinearGradient, QBrush


class SplashScreen(QWidget):
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dark_mode = True
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._here = os.path.dirname(os.path.abspath(__file__))

        # ── Hero image container ──────────────────────────────────────
        self._hero_container = QFrame()
        self._hero_container.setFixedHeight(320)
        self._hero_container.setStyleSheet("background-color: #0d1214; border: none;")
        hero_layout = QVBoxLayout(self._hero_container)
        hero_layout.setContentsMargins(0, 0, 0, 0)

        self.hero = QLabel()
        self.hero.setAlignment(Qt.AlignCenter)
        self.hero.setStyleSheet("background-color: transparent; border: none;")
        hero_layout.addWidget(self.hero)

        self._load_hero_image(dark_mode=True)
        root.addWidget(self._hero_container)

        # ── Content area ──────────────────────────────────────────────
        self._content = QFrame()
        content = self._content
        content.setStyleSheet("""
            QFrame {
                background-color: #0d1214;
                border-top: 2px solid #2e7d32;
            }
        """)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(48, 24, 48, 24)
        content_layout.setSpacing(8)

        # Suite label
        self._suite_lbl = QLabel("ENGINEERING DIAGNOSTIC SUITE")
        self._suite_lbl.setAlignment(Qt.AlignCenter)
        self._suite_lbl.setStyleSheet("""
            color: #2e7d32; font-size: 11px; font-weight: 700;
            letter-spacing: 4px; font-family: 'Segoe UI', sans-serif;
        """)
        content_layout.addWidget(self._suite_lbl)

        # Main title
        self._title = QLabel("Advanced Automotive Diagnostic Engineering Platform")
        self._title.setAlignment(Qt.AlignCenter)
        self._title.setStyleSheet("""
            color: #e8f5e9; font-size: 24px; font-weight: 800;
            font-family: 'Segoe UI', sans-serif;
        """)
        content_layout.addWidget(self._title)

        # Subtitle
        self._subtitle = QLabel("BCM 0x700  •  Wiper Controller 0x701  —  DoIP / UDS  •  Wiper & Wash System HIL")
        self._subtitle.setAlignment(Qt.AlignCenter)
        self._subtitle.setStyleSheet("""
            color: #90a4ae; font-size: 13px; font-weight: 500;
            font-family: 'Consolas', monospace; letter-spacing: 1px;
        """)
        content_layout.addWidget(self._subtitle)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("background-color: #1e3a22; border: none; max-height: 1px; margin: 4px 0;")
        content_layout.addWidget(sep)

        # Tags row
        tags_row = QHBoxLayout()
        tags_row.setSpacing(12)
        tags_row.addStretch()
        self._tag_labels = []
        for tag in ["SMART VALIDATION", "LIVE DIAGNOSTICS", "HIL TESTING"]:
            t = QLabel(f"• {tag}")
            t.setStyleSheet("""
                color: #a5d6a7; font-size: 11px; font-weight: 600;
                letter-spacing: 1.5px; font-family: 'Segoe UI', sans-serif;
            """)
            tags_row.addWidget(t)
            self._tag_labels.append(t)
        tags_row.addStretch()
        content_layout.addLayout(tags_row)

        # Credits card
        self._credits = QFrame()
        self._credits.setStyleSheet("""
            QFrame {
                background-color: #111a13;
                border: 1px solid #2a4a30;
                border-radius: 8px;
            }
        """)
        credits_layout = QVBoxLayout(self._credits)
        credits_layout.setContentsMargins(20, 12, 20, 12)
        credits_layout.setSpacing(4)

        self._credit_labels = []
        dark_colors  = ["#a5d6a7", "#90a4ae", "#e0e0e0", "#78909c"]
        for i, (line, size) in enumerate([
            ("Project Credits", 12),
            ("Wiper & Wash System Diagnostic Platform — PFE 2026 at KPIT Engineering Sfax", 11),
            ("Ghassen Hedi Hajji", 13),
            ("Étudiant en Informatique Industrielle  •  ENETCOM Sfax", 11),
        ]):
            lbl = QLabel(line)
            lbl.setStyleSheet(f"""
                color: {dark_colors[i]}; font-size: {size}px;
                font-family: 'Segoe UI', sans-serif; background: transparent;
            """)
            credits_layout.addWidget(lbl)
            self._credit_labels.append((lbl, size))

        content_layout.addWidget(self._credits)

        # Bottom row
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(0)

        ver_col = QVBoxLayout()
        ver_col.setSpacing(2)
        self._ver_lbl = QLabel("Version 1.0.0  (Engineering Build)")
        self._ver_lbl.setStyleSheet("color: #546e7a; font-size: 11px; font-family: Consolas; background: transparent;")
        self._copy_lbl = QLabel("© 2026 Ghassen Hedi Hajji — All rights reserved.")
        self._copy_lbl.setStyleSheet("color: #37474f; font-size: 10px; font-family: Consolas; background: transparent;")
        ver_col.addWidget(self._ver_lbl)
        ver_col.addWidget(self._copy_lbl)
        bottom_row.addLayout(ver_col)
        bottom_row.addStretch()

        enter_btn = QPushButton("Enter Diagnostic Platform")
        enter_btn.setFixedHeight(48)
        enter_btn.setMinimumWidth(280)
        enter_btn.setCursor(Qt.PointingHandCursor)
        enter_btn.setStyleSheet("""
            QPushButton {
                background-color: #1b5e20;
                color: #e8f5e9;
                border: 1px solid #2e7d32;
                border-radius: 10px;
                font-size: 15px;
                font-weight: 700;
                font-family: 'Segoe UI', sans-serif;
                padding: 0 32px;
                letter-spacing: 0.5px;
            }
            QPushButton:hover { background-color: #2e7d32; }
            QPushButton:pressed { background-color: #0d4d0d; }
        """)
        enter_btn.clicked.connect(self.finished)
        bottom_row.addWidget(enter_btn)

        content_layout.addLayout(bottom_row)
        root.addWidget(content)

    # ------------------------------------------------------------------
    # Image loader with gradient fade
    # ------------------------------------------------------------------
    def _load_hero_image(self, dark_mode: bool):
        name = "splash_bg_dark.png" if dark_mode else "splash_bg_light.png"
        img_path = os.path.join(self._here, "..", "..", "assets", name)
        bg = "#0d1214" if dark_mode else "#D6DBE0"

        if os.path.exists(img_path):
            pix = QPixmap(img_path)

            # Récupérer la largeur du widget (ou valeur par défaut)
            container_w = self._hero_container.width()
            if container_w < 100:
                container_w = 1400
            container_h = 320

            # Scaler pour couvrir toute la largeur
            scaled = pix.scaledToWidth(container_w, Qt.SmoothTransformation)

            # Si trop petit en hauteur, scaler par la hauteur
            if scaled.height() < container_h:
                scaled = pix.scaledToHeight(container_h, Qt.SmoothTransformation)

            # Centrer le crop horizontalement et verticalement
            x_offset = max(0, (scaled.width() - container_w) // 2)
            y_offset = max(0, (scaled.height() - container_h) // 2)
            cropped = scaled.copy(x_offset, y_offset, container_w, container_h)

            # Appliquer le fondu
            faded = self._apply_fade(cropped, bg)
            self.hero.setPixmap(faded)
        else:
            self.hero.clear()

    def _apply_fade(self, pix: QPixmap, bg_color: str) -> QPixmap:
        result = QPixmap(pix.size())
        result.fill(Qt.transparent)

        painter = QPainter(result)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.drawPixmap(0, 0, pix)

        w = pix.width()
        h = pix.height()
        fade_w = int(w * 0.20)

        color = QColor(bg_color)
        c_transparent = QColor(color)
        c_transparent.setAlpha(0)

        # Left fade
        grad_left = QLinearGradient(0, 0, fade_w, 0)
        grad_left.setColorAt(0.0, color)
        grad_left.setColorAt(1.0, c_transparent)
        painter.fillRect(0, 0, fade_w, h, QBrush(grad_left))

        # Right fade
        grad_right = QLinearGradient(w - fade_w, 0, w, 0)
        grad_right.setColorAt(0.0, c_transparent)
        grad_right.setColorAt(1.0, color)
        painter.fillRect(w - fade_w, 0, fade_w, h, QBrush(grad_right))

        # Bottom fade
        fade_h = int(h * 0.35)
        grad_bottom = QLinearGradient(0, h - fade_h, 0, h)
        grad_bottom.setColorAt(0.0, c_transparent)
        grad_bottom.setColorAt(1.0, color)
        painter.fillRect(0, h - fade_h, w, fade_h, QBrush(grad_bottom))

        painter.end()
        return result

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def apply_theme(self, dark_mode: bool):
        self._dark_mode = dark_mode
        self._load_hero_image(dark_mode)

        if dark_mode:
            self._hero_container.setStyleSheet("background-color: #0d1214; border: none;")
            self._content.setStyleSheet("QFrame { background-color: #0d1214; border-top: 2px solid #2e7d32; }")
            self._suite_lbl.setStyleSheet("color: #2e7d32; font-size: 11px; font-weight: 700; letter-spacing: 4px; font-family: 'Segoe UI';")
            self._title.setStyleSheet("color: #e8f5e9; font-size: 24px; font-weight: 800; font-family: 'Segoe UI';")
            self._subtitle.setStyleSheet("color: #90a4ae; font-size: 13px; font-weight: 500; font-family: 'Consolas'; letter-spacing: 1px;")
            for t in self._tag_labels:
                t.setStyleSheet("color: #a5d6a7; font-size: 11px; font-weight: 600; letter-spacing: 1.5px; font-family: 'Segoe UI';")
            self._credits.setStyleSheet("QFrame { background-color: #111a13; border: 1px solid #2a4a30; border-radius: 8px; }")
            dark_colors = ["#a5d6a7", "#90a4ae", "#e0e0e0", "#78909c"]
            for i, (lbl, size) in enumerate(self._credit_labels):
                lbl.setStyleSheet(f"color: {dark_colors[i]}; font-size: {size}px; font-family: 'Segoe UI'; background: transparent;")
            self._ver_lbl.setStyleSheet("color: #546e7a; font-size: 11px; font-family: Consolas; background: transparent;")
            self._copy_lbl.setStyleSheet("color: #37474f; font-size: 10px; font-family: Consolas; background: transparent;")
        else:
            self._hero_container.setStyleSheet("background-color: #D6DBE0; border: none;")
            self._content.setStyleSheet("QFrame { background-color: #D6DBE0; border-top: 2px solid #2e7d32; }")
            self._suite_lbl.setStyleSheet("color: #1b5e20; font-size: 11px; font-weight: 700; letter-spacing: 4px; font-family: 'Segoe UI';")
            self._title.setStyleSheet("color: #0d1f0d; font-size: 24px; font-weight: 800; font-family: 'Segoe UI';")
            self._subtitle.setStyleSheet("color: #1b5e20; font-size: 13px; font-weight: 600; font-family: 'Consolas'; letter-spacing: 1px;")
            for t in self._tag_labels:
                t.setStyleSheet("color: #1b5e20; font-size: 11px; font-weight: 700; letter-spacing: 1.5px; font-family: 'Segoe UI';")
            self._credits.setStyleSheet("QFrame { background-color: #E2E7EB; border: 1px solid #78909c; border-radius: 8px; }")
            light_colors = ["#1b5e20", "#37474f", "#0d1f0d", "#546e7a"]
            for i, (lbl, size) in enumerate(self._credit_labels):
                lbl.setStyleSheet(f"color: {light_colors[i]}; font-size: {size}px; font-family: 'Segoe UI'; background: transparent;")
            self._ver_lbl.setStyleSheet("color: #37474f; font-size: 11px; font-family: Consolas; background: transparent;")
            self._copy_lbl.setStyleSheet("color: #546e7a; font-size: 10px; font-family: Consolas; background: transparent;")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._load_hero_image(self._dark_mode)