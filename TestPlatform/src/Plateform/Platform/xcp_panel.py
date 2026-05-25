"""
xcp_panel.py  —  XCP Panel UI (Platform WipeWash)
==================================================
Panneau de calibration XCP-like intégré dans le QTabWidget principal.

NOUVEAUTÉS v2 — 3 fonctionnalités ControlDesk-inspired

  1. DIRTY INDICATOR
     Chaque carte détecte quand spinbox != valeur BCM live -> badge
     amber animé "PENDING" + bouton DOWNLOAD passe amber.
     Compteur global dans la barre d'actions.

  2. SAVE / LOAD PROFIL JSON
     Snapshot nommé de toutes les valeurs courantes -> fichier JSON.
     Load : recharge + DOWNLOAD batch vers le BCM.
     Historique des 5 derniers profils dans un QComboBox.

  3. MINI-OSCILLOSCOPE intégré
     Splitter droite du scroll : oscilloscope multi-courbes alimenté
     par les polls SHORT_UPLOAD (500ms).
     Sélection des paramètres à tracer via checkboxes dans les cartes.
     Palette de couleurs dédiée, curseur de temps, légende live.

ARCHITECTURE — toujours 1 seule page XCP

  ┌───────────────────────────────────────────────────────────────┐
  │  HEADER DARK  —  XCP CALIBRATION · BCM LIVE                  │
  ├───────────────────────────────────────────────────────────────┤
  │  BARRE ACTIONS                                                │
  │  [filtre] [cat] [Apply N pending] [Save] [Load]              │
  ├───────────────────┬───────────────────────────────────────────┤
  │                   │  OSCILLOSCOPE                             │
  │  CARTES           │  (multi-courbes, légende, time window)    │
  │  PARAMETRES       │                                           │
  │  (scroll)         ├───────────────────────────────────────────┤
  │                   │  SELECTED PARAMS — checkboxes             │
  ├───────────────────┴───────────────────────────────────────────┤
  │  XCP LOG  (bas)                                               │
  └───────────────────────────────────────────────────────────────┘

INTÉGRATION dans main_window.py  — IDENTIQUE À AVANT (0 modif)

    from xcp_panel import XCPPanel
    self._xcp_panel = XCPPanel()
    self._tabs.addTab(self._xcp_panel, "XCP Calibration")
    ...
    if hasattr(self, '_xcp_panel'):
        self._xcp_panel.set_host(host)

"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections import deque
from typing import Any

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QFrame, QScrollArea,
    QDoubleSpinBox, QSpinBox, QComboBox, QLineEdit,
    QSizePolicy, QMessageBox, QFileDialog, QSlider,
)
from PySide6.QtCore import Qt, Signal, QTimer, QObject, QPointF, QRectF
from PySide6.QtGui  import (
    QColor, QFont, QPainter, QPen, QBrush,
    QPainterPath, QLinearGradient, QRadialGradient, QConicalGradient,
)

from constants import (
    FONT_UI, FONT_MONO,
    W_BG, W_PANEL, W_PANEL2, W_TOOLBAR, W_TITLEBAR,
    W_BORDER, W_TEXT, W_TEXT_DIM, W_TEXT_HDR,
    KPIT_GREEN, KPIT_GREEN, KPIT_GREEN, KPIT_GREEN,
    A_TEAL, A_TEAL2, A_ORANGE,
)
from xcp_master import XCPMaster, XCPError

# Palette locale
_C_BG      = "#0A100A"          # fond très sombre vert-noir
_C_SURF    = "#0F180F"          # surface carte
_C_SURF2   = "#141E14"          # surface alternate
_C_TEXT    = "#C8E8C0"          # texte principal
_C_DIM     = "#5A7A5A"          # texte dim
_C_GREEN   = KPIT_GREEN         # #8DC63F
_C_KPIT    = KPIT_GREEN

# Palette par catégorie — chaque catégorie a sa propre couleur accent
_CAT = {
    "TIMING":     {"hdr": "#8DC63F", "bg": "#0F180F", "plot": "#8DC63F",  "accent": "#8DC63F"},
    "PUMP":       {"hdr": "#00C8FF", "bg": "#0A1520", "plot": "#00C8FF",  "accent": "#00C8FF"},
    "WASH":       {"hdr": "#00E5CC", "bg": "#0A1A18", "plot": "#00E5CC",  "accent": "#00E5CC"},
    "RAIN":       {"hdr": "#4FC3F7", "bg": "#0A1520", "plot": "#4FC3F7",  "accent": "#4FC3F7"},
    "PROTECTION": {"hdr": "#FF6B35", "bg": "#1A0F0A", "plot": "#FF6B35",  "accent": "#FF6B35"},
    "WATCHDOG":   {"hdr": "#FFB830", "bg": "#1A1400", "plot": "#FFB830",  "accent": "#FFB830"},
}
_CAT_DEFAULT = _CAT["TIMING"]

# Palette oscilloscope — couleurs vives distinctes
_PLOT_PALETTE = [
    "#8DC63F", "#00C8FF", "#FF6B35", "#FFB830", "#00E5CC",
    "#E040FB", "#4FC3F7", "#69F0AE", "#FF4081", "#FFEB3B",
    "#F48FB1", "#80DEEA", "#CCFF90", "#FF8A65",
]

# Mapping catégories (fallback statique)
_PARAM_CAT_STATIC = {
    "TOUCH_DURATION":          "TIMING",
    "PARK_TIMEOUT":            "TIMING",
    "REVERSE_REAR_PERIOD":     "TIMING",
    "WIPE_CYCLE_DURATION":     "TIMING",
    "PUMP_MAX_RUNTIME":        "PUMP",
    "WASH_FRONT_CYCLES":       "WASH",
    "WASH_REAR_CYCLES":        "WASH",
    "RAIN_SPEED2_THRESH":      "RAIN",
    "OVERCURRENT_THRESH":      "PROTECTION",
    "OVERCURRENT_DELAY":       "PROTECTION",
    "PUMP_OVERCURRENT_THRESH": "PROTECTION",
    "PUMP_OVERCURRENT_DELAY":  "PROTECTION",
    "REST_STUCK_DELAY":        "PROTECTION",
    "WATCHDOG_MAX_MS":         "WATCHDOG",
}

try:
    from xcp_master import _LOCAL_A2L as _A2L_FOR_CAT
except Exception:
    _A2L_FOR_CAT = None

if _A2L_FOR_CAT:
    _PARAM_CAT: dict[str, str] = {
        k: v.get("category", "TIMING") for k, v in _A2L_FOR_CAT.items()
    }
else:
    _PARAM_CAT = _PARAM_CAT_STATIC


# XCPBridge — pont thread-safe -> signaux Qt
# ══════════════════════════════════════════════════════════════
#  HELPER — boîtes de dialogue claires et professionnelles
# ══════════════════════════════════════════════════════════════
_DIALOG_STYLE = """
QMessageBox, QInputDialog {
    background-color: #FFFFFF;
    color: #1A1A1A;
}
QMessageBox QLabel, QInputDialog QLabel {
    color: #1A1A1A;
    background-color: transparent;
    font-size: 13px;
}
QMessageBox QPushButton, QInputDialog QPushButton {
    background-color: #F0F0F0;
    color: #1A1A1A;
    border: 1px solid #BDBDBD;
    border-radius: 4px;
    padding: 5px 18px;
    min-width: 72px;
    font-size: 12px;
}
QMessageBox QPushButton:hover, QInputDialog QPushButton:hover {
    background-color: #F8FAFC;
    border-color: #8DC63F;
    color: #1A1A1A;
}
QMessageBox QPushButton:default, QInputDialog QPushButton:default {
    background-color: #8DC63F;
    color: #FFFFFF;
    border-color: #6AAF2A;
}
QMessageBox QPushButton:default:hover, QInputDialog QPushButton:default:hover {
    background-color: #7ABB30;
}
QInputDialog QLineEdit {
    background-color: #F5F5F5;
    color: #1A1A1A;
    border: 1px solid #BDBDBD;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 13px;
}
QInputDialog QLineEdit:focus {
    border-color: #8DC63F;
}
"""

def _apply_light_style(dlg) -> None:
    """Applique un style clair et professionnel à un QMessageBox ou QInputDialog."""
    dlg.setStyleSheet(_DIALOG_STYLE)


def _ask(parent, title: str, text: str) -> bool:
    """QMessageBox.question stylé clair — retourne True si l'utilisateur clique Oui."""
    mb = QMessageBox(parent)
    mb.setWindowTitle(title)
    mb.setText(text)
    mb.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    mb.setDefaultButton(QMessageBox.StandardButton.Yes)
    _apply_light_style(mb)
    return mb.exec() == QMessageBox.StandardButton.Yes


class XCPBridge(QObject):
    response_received = Signal(str, str, object, object)
    get_a2l_err       = Signal(str)
    download_ok       = Signal(str, object)
    download_err      = Signal(str, str)
    log_msg           = Signal(str, bool)
    poll_values       = Signal(object)



# ═══════════════════════════════════════════════════════════════════════
#  XCPParamTile — tuile instrument carrée (nouveau design)
#  Remplace DeltaArc + PendingBadge + XCPParamCard + XCPOscilloscope
# ═══════════════════════════════════════════════════════════════════════

class XCPParamTile(QWidget):
    """
    Tuile carrée style ControlDesk — chaque paramètre = une tuile.
    Centre : jauge arc radiale + valeur live.
    Bas    : slider + boutons DL / Reset.
    Design : fond vert KPIT, contour noir 2px, accent par catégorie.
    """
    download_requested = Signal(str, object)
    dirty_changed      = Signal(str, bool)

    _TILE_W = 200   # largeur minimale
    _TILE_H = 230   # hauteur fixe

    def __init__(self, key: str, meta: dict, parent=None):
        super().__init__(parent)
        self._key       = key
        self._meta      = meta
        self._live_val  = meta["default"]
        self._is_dirty  = False
        self._pulse     = 0.0
        self._pulse_dir = 1
        self._anim_val  = float(meta["default"])  # valeur animée pour la jauge

        cat   = meta.get("category") or _PARAM_CAT.get(key, "TIMING")
        self._theme = _CAT.get(cat, _CAT_DEFAULT)
        self._accent = QColor(self._theme["accent"])
        self._cat    = cat

        self.setMinimumSize(self._TILE_W, self._TILE_H)
        self.setFixedHeight(self._TILE_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("background:transparent;")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Timer animation pulse dirty + jauge
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._t.start(40)

        # Spin caché (logique inchangée)
        if meta["type"] == "float":
            self._spin = QDoubleSpinBox(self)
            self._spin.setDecimals(3)
            self._spin.setSingleStep(meta.get("step", 0.1))
        else:
            self._spin = QSpinBox(self)
            self._spin.setSingleStep(meta.get("step", 1))
        self._spin.setRange(meta["min"], meta["max"])
        self._spin.setValue(meta["default"])
        self._spin.setEnabled(False)
        self._spin.hide()
        self._spin.valueChanged.connect(self._check_dirty)

        # Slider visible
        self._slider = QSlider(Qt.Orientation.Horizontal, self)
        rng = meta["max"] - meta["min"]
        if meta["type"] == "float":
            self._slider_scale = max(1, int(1000 / max(rng, 1e-9)))
        else:
            self._slider_scale = 1
        self._slider.setRange(
            int(meta["min"] * self._slider_scale),
            int(meta["max"] * self._slider_scale)
        )
        self._slider.setValue(int(meta["default"] * self._slider_scale))
        self._slider.setEnabled(False)
        self._slider.setStyleSheet(self._slider_style())
        self._slider.valueChanged.connect(self._on_slider_changed)

        # Bouton DL
        self._dl_btn = QPushButton("DL", self)
        self._dl_btn.setFixedSize(36, 26)
        self._dl_btn.setFont(QFont(FONT_MONO, 9, QFont.Weight.Bold))
        self._dl_btn.setEnabled(False)
        self._dl_btn.clicked.connect(self._on_download)
        self._update_dl_style(False)

        # Bouton Reset
        self._rst_btn = QPushButton("RST", self)
        self._rst_btn.setFixedSize(36, 26)
        self._rst_btn.setFont(QFont(FONT_MONO, 9))
        self._rst_btn.setEnabled(False)
        self._rst_btn.setStyleSheet(
            f"QPushButton{{background:#0A1200;color:#2A4A2A;"
            f"border:1px solid #1E3A1E;border-radius:3px;}}"
            f"QPushButton:hover{{color:{self._theme['accent']};border-color:{self._theme['accent']};}}"
            f"QPushButton:disabled{{color:#0F1A0F;border-color:#0A1000;}}"
        )
        self._rst_btn.clicked.connect(lambda: self._set_value(self._meta["default"]))
        self._reposition_children()

    def _reposition_children(self):
        """Repositionne slider et boutons selon la largeur réelle de la tuile."""
        W = self.width() or self._TILE_W
        H = self._TILE_H
        self._slider.setGeometry(10, H - 50, W - 20, 16)
        self._dl_btn.setGeometry(W - 80, H - 28, 36, 26)
        self._rst_btn.setGeometry(W - 40, H - 28, 36, 26)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_children()

    def _slider_style(self):
        acc = self._theme["accent"]
        return (
            f"QSlider::groove:horizontal{{height:4px;background:#1E3A1E;border-radius:2px;}}"
            f"QSlider::sub-page:horizontal{{background:{acc};border-radius:2px;}}"
            f"QSlider::handle:horizontal{{width:10px;height:10px;margin:-3px 0;"
            f"background:{acc};border-radius:5px;border:1px solid #000000;}}"
            f"QSlider:disabled::groove:horizontal{{background:#0F1A0F;}}"
            f"QSlider:disabled::sub-page:horizontal{{background:#1E2E1E;}}"
            f"QSlider:disabled::handle:horizontal{{background:#1E2E1E;}}"
        )

    def _tick(self):
        # Animation jauge vers valeur live
        target = float(self._live_val)
        self._anim_val += (target - self._anim_val) * 0.12
        # Pulse dirty
        if self._is_dirty:
            self._pulse += self._pulse_dir * 0.06
            if self._pulse >= 1.0: self._pulse = 1.0; self._pulse_dir = -1
            elif self._pulse <= 0.0: self._pulse = 0.0; self._pulse_dir = 1
        else:
            self._pulse = 0.0
        self.update()

    def _on_slider_changed(self, v):
        if not self._slider.isEnabled():
            return
        val = v / self._slider_scale
        self._spin.blockSignals(True)
        self._spin.setValue(val)
        self._spin.blockSignals(False)
        self._check_dirty()
        self.update()

    def _set_value(self, val):
        self._slider.setValue(int(val * self._slider_scale))
        self._spin.setValue(val)

    def _check_dirty(self, _=None):
        if not self._spin.isEnabled():
            return
        spin_val = self._spin.value()
        try:
            live = float(self._live_val) if self._meta["type"] == "float" \
                   else int(float(self._live_val))
        except (TypeError, ValueError):
            return
        dirty = abs(spin_val - live) > 1e-9
        if dirty != self._is_dirty:
            self._is_dirty = dirty
            self._update_dl_style(dirty)
            self.dirty_changed.emit(self._key, dirty)
        self.update()

    def _update_dl_style(self, dirty: bool):
        if dirty:
            self._dl_btn.setStyleSheet(
                "QPushButton{background:#1A0E00;color:#FFB830;"
                "border:1px solid #FFB830;border-radius:3px;}"
                "QPushButton:hover{background:#2A1800;}")
        else:
            acc = self._theme["accent"]
            self._dl_btn.setStyleSheet(
                f"QPushButton{{background:#0A1200;color:#2A4A2A;"
                f"border:1px solid #1E3A1E;border-radius:3px;}}"
                f"QPushButton:hover{{color:{acc};border-color:{acc};}}"
                f"QPushButton:disabled{{color:#0F1A0F;border-color:#0A1000;}}")

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        # ── Fond tuile ─────────────────────────────────────────────────
        path = QPainterPath()
        path.addRoundedRect(QRectF(1, 1, W-2, H-2), 6, 6)
        bg = QLinearGradient(0, 0, 0, H)
        bg.setColorAt(0, QColor("#FFFFFF"))
        bg.setColorAt(1, QColor("#FFFFFF"))
        p.setBrush(QBrush(bg))
        # Contour noir 2px — dirty = amber pulsant
        if self._is_dirty:
            amber = QColor(255, 184, 48, int(100 + 155 * self._pulse))
            p.setPen(QPen(amber, 2))
        else:
            p.setPen(QPen(QColor("#000000"), 2))
        p.drawPath(path)

        # Barre catégorie haut (6px)
        bar = QPainterPath()
        bar.addRoundedRect(QRectF(1, 1, W-2, 5), 5, 5)
        bar.addRect(QRectF(1, 4, W-2, 3))
        p.fillPath(bar, QBrush(self._accent))

        # ── Nom paramètre ──────────────────────────────────────────────
        short = self._key.replace("_", " ")
        p.setFont(QFont(FONT_MONO, 9, QFont.Weight.Bold))
        p.setPen(QPen(self._accent))
        p.drawText(6, 8, W-12, 16, Qt.AlignmentFlag.AlignLeft
                   | Qt.AlignmentFlag.AlignVCenter, short)

        # Badge catégorie
        p.setFont(QFont(FONT_MONO, 8))
        p.setPen(QPen(QColor(self._theme["accent"])))
        badge_r = QRectF(W - 52, 10, 46, 14)
        bp = QPainterPath(); bp.addRoundedRect(badge_r, 2, 2)
        cat_bg = QColor(self._accent); cat_bg.setAlpha(25)
        p.fillPath(bp, QBrush(cat_bg))
        p.setPen(QPen(self._accent))
        p.drawText(badge_r.toRect(), Qt.AlignmentFlag.AlignCenter, self._cat[:5])

        # ── Jauge radiale centrale ─────────────────────────────────────
        cx, cy = W // 2, 108
        R = 48
        vmin = float(self._meta["min"])
        vmax = float(self._meta["max"])
        vrange = vmax - vmin if vmax != vmin else 1.0
        pct = max(0.0, min(1.0, (self._anim_val - vmin) / vrange))
        spin_pct = max(0.0, min(1.0, (self._spin.value() - vmin) / vrange))

        START  = 220   # degrés (depuis 3h)
        SPAN   = 280   # amplitude totale

        # Track fond sombre
        p.setPen(QPen(QColor("#1E3A1E"), 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(int(cx-R), int(cy-R), R*2, R*2,
                  int(-(START - SPAN//2 + 90) * 16),
                  int(-(SPAN) * 16))

        # Arc valeur spin (cible)
        if self._is_dirty and spin_pct > 0:
            p.setPen(QPen(QColor(255, 184, 48, 80), 7,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(int(cx-R), int(cy-R), R*2, R*2,
                      int(-(START - SPAN//2 + 90) * 16),
                      int(-(SPAN * spin_pct) * 16))

        # Arc valeur live animée
        if pct > 0.005:
            arc_col = self._accent if not self._is_dirty else QColor("#8DC63F")
            # Glow
            glow_c = QColor(arc_col); glow_c.setAlpha(45)
            p.setPen(QPen(glow_c, 13, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(int(cx-R), int(cy-R), R*2, R*2,
                      int(-(START - SPAN//2 + 90) * 16),
                      int(-(SPAN * pct) * 16))
            # Principal
            p.setPen(QPen(arc_col, 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(int(cx-R), int(cy-R), R*2, R*2,
                      int(-(START - SPAN//2 + 90) * 16),
                      int(-(SPAN * pct) * 16))

        # Fond intérieur jauge
        inner_g = QRadialGradient(cx, cy, R - 10)
        inner_g.setColorAt(0, QColor("#1A2E0A"))
        inner_g.setColorAt(1, QColor("#0F1A08"))
        p.setBrush(QBrush(inner_g))
        p.setPen(QPen(QColor("#1E3A1E"), 1))
        p.drawEllipse(int(cx - R + 9), int(cy - R + 9), (R-9)*2, (R-9)*2)

        # Valeur live au centre
        v_str = f"{self._live_val:.1f}" if self._meta["type"] == "float" else str(int(self._live_val))
        p.setFont(QFont(FONT_MONO, 16, QFont.Weight.Bold))
        txt_col = QColor(self._accent) if not self._is_dirty else QColor("#FFB830")
        p.setPen(QPen(txt_col))
        p.drawText(cx-R+10, cy-14, (R-10)*2, 20,
                   Qt.AlignmentFlag.AlignCenter, v_str)

        # Unité
        p.setFont(QFont(FONT_MONO, 9))
        p.setPen(QPen(QColor("#3A6A3A")))
        p.drawText(cx-R+10, cy+4, (R-10)*2, 14,
                   Qt.AlignmentFlag.AlignCenter, self._meta["unit"])

        # Valeur spin (si dirty)
        if self._is_dirty:
            sp_str = f">{self._spin.value():.1f}" if self._meta["type"]=="float" \
                     else f">{int(self._spin.value())}"
            p.setFont(QFont(FONT_MONO, 9, QFont.Weight.Bold))
            p.setPen(QPen(QColor("#FFB830")))
            p.drawText(cx-R+10, cy+18, (R-10)*2, 14,
                       Qt.AlignmentFlag.AlignCenter, sp_str)

        # Description (bas jauge)
        desc = self._meta.get("desc", "")[:32]
        p.setFont(QFont(FONT_UI, 8))
        p.setPen(QPen(QColor("#5A7A5A")))
        p.drawText(4, H - 66, W-8, 14,
                   Qt.AlignmentFlag.AlignCenter, desc)

    # ── API publique (inchangée) ──────────────────────────────────────
    def set_session_active(self, active: bool = True):
        self._spin.setEnabled(True)
        self._slider.setEnabled(True)
        self._dl_btn.setEnabled(True)
        self._rst_btn.setEnabled(True)

    def update_live(self, val):
        try:
            v = float(val) if self._meta["type"] == "float" else int(float(val))
        except (TypeError, ValueError):
            return
        self._live_val = v
        if not self._is_dirty:
            self._slider.blockSignals(True)
            self._slider.setValue(int(v * self._slider_scale))
            self._slider.blockSignals(False)
            self._spin.blockSignals(True)
            self._spin.setValue(v)
            self._spin.blockSignals(False)
        self._check_dirty()

    def flash_ack(self):
        self._is_dirty = False
        self._pulse = 0.0
        self._update_dl_style(False)
        self.dirty_changed.emit(self._key, False)
        self.update()

    def get_spin_value(self):
        v = self._spin.value()
        return int(v) if self._meta["type"] == "int" else v

    def set_spin_value(self, val):
        self._set_value(val)
        self._check_dirty()

    def is_dirty(self) -> bool:
        return self._is_dirty

    def _on_download(self):
        val = self.get_spin_value()
        self.download_requested.emit(self._key, val)


# Alias pour compatibilité avec _build_cards
XCPParamCard = XCPParamTile


# Stub XCPOscilloscope conservé pour compatibilité _apply_poll
class XCPOscilloscope(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.hide()
    def add_trace(self, key, color, meta): pass
    def remove_trace(self, key): pass
    def push_value(self, key, val): pass
    def clear_all(self): pass


# XCPPanel — panneau principal
class XCPPanel(QWidget):
    """
    Panneau XCP complet v2 — intègre dirty indicator, save/load profil, oscillo.
    Interface main_window.py inchangée.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._host   = None
        self._master: XCPMaster | None = None
        self._bridge = XCPBridge()
        self._cards: dict[str, XCPParamCard] = {}
        self._a2l:   dict = {}
        self._dirty_keys: set[str] = set()
        self._last_cols: int = 0   # pour détecter les changements de colonnes

        # Historique profils
        self._profiles: list[dict] = []   # [{name, values, ts}]

        # Timer poll 500ms
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_all)
        self._poll_timer.setInterval(500)

        # Connexions bridge
        self._bridge.response_received.connect(self._on_response)
        self._bridge.get_a2l_err.connect(
            lambda e: self._log(f"GET_A2L failed: {e}", error=True))
        self._bridge.download_ok.connect(self._on_download_ok)
        self._bridge.download_err.connect(
            lambda key, e: self._log(f"DOWNLOAD {key} failed: {e}", error=True))
        self._bridge.log_msg.connect(self._log)
        self._bridge.poll_values.connect(self._apply_poll)

        self._build_ui()
        self._poll_timer.start()

    # Construction UI
    def _build_ui(self):
        self.setStyleSheet(f"background:{_C_BG};")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── HEADER ─────────────────────────────────────────────────────────
        hdr = QFrame()
        hdr.setFixedHeight(46)
        hdr.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #0F1A0A, stop:1 #070A04);"
            "border-bottom:2px solid #000000;"
        )
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(14, 0, 12, 0)
        hdr_lay.setSpacing(10)

        bar = QFrame(); bar.setFixedSize(3, 28)
        bar.setStyleSheet(f"background:{_C_GREEN}; border-radius:1px;")
        hdr_lay.addWidget(bar)

        ttl = QLabel("XCP CALIBRATION")
        ttl.setFont(QFont(FONT_MONO, 11, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color:{_C_GREEN}; background:transparent;")
        hdr_lay.addWidget(ttl)

        sub = QLabel("BCM LIVE PARAMETERS")
        sub.setFont(QFont(FONT_MONO, 8))
        sub.setStyleSheet("color:#3A5A3A; background:transparent;")
        hdr_lay.addWidget(sub)
        hdr_lay.addStretch()

        ip_lbl = QLabel("BCM ▸")
        ip_lbl.setFont(QFont(FONT_MONO, 8))
        ip_lbl.setStyleSheet("color:#3A6A3A; background:transparent;")
        hdr_lay.addWidget(ip_lbl)

        self._ip_edit = QLineEdit()
        self._ip_edit.setPlaceholderText("10.20.0.x")
        self._ip_edit.setFixedWidth(110); self._ip_edit.setFixedHeight(28)
        self._ip_edit.setFont(QFont(FONT_MONO, 9))
        self._ip_edit.setStyleSheet(
            f"QLineEdit{{background:#0A1A08;color:{_C_GREEN};"
            f"border:1px solid #1E3A1E;border-radius:3px;padding:0 8px;}}"
            f"QLineEdit:focus{{border:1px solid {_C_GREEN};}}")
        self._ip_edit.returnPressed.connect(self._on_connect_clicked)
        hdr_lay.addWidget(self._ip_edit)

        from widgets_base import _cd_btn as _mk_btn_cd
        self._connect_btn = _mk_btn_cd("CONNECT", _C_GREEN, h=34, w=120)
        self._connect_btn.setFont(QFont(FONT_MONO, 10, QFont.Weight.Bold))
        self._connect_btn.clicked.connect(self._on_connect_clicked)
        hdr_lay.addWidget(self._connect_btn)

        self._conn_badge = QLabel("◌ OFFLINE")
        self._conn_badge.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
        self._conn_badge.setStyleSheet("color:#2A4A2A;background:transparent;padding:0 4px;")
        hdr_lay.addWidget(self._conn_badge)
        root.addWidget(hdr)

        # ── BARRE ACTIONS ──────────────────────────────────────────────────
        act_bar = QFrame()
        act_bar.setFixedHeight(52)
        act_bar.setStyleSheet("background:#080E08;border-bottom:1px solid #1E2E1E;")
        act_lay = QHBoxLayout(act_bar)
        act_lay.setContentsMargins(12, 0, 12, 0); act_lay.setSpacing(8)

        _inp = (f"background:#0A1400;color:#8DC63F;"
                f"border:1px solid #1E3A1E;border-radius:3px;padding:0 8px;")

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search…")
        self._search.setFixedWidth(180); self._search.setFixedHeight(34)
        self._search.setFont(QFont(FONT_MONO, 9))
        self._search.setStyleSheet(f"QLineEdit{{{_inp}}}QLineEdit:focus{{border-color:{_C_GREEN};}}")
        self._search.textChanged.connect(self._apply_filter)
        act_lay.addWidget(self._search)

        self._cat_filter = QComboBox()
        self._cat_filter.addItem("ALL CATEGORIES")
        for cat in _CAT: self._cat_filter.addItem(cat)
        self._cat_filter.setFixedWidth(160); self._cat_filter.setFixedHeight(34)
        self._cat_filter.setFont(QFont(FONT_MONO, 9))
        self._cat_filter.setStyleSheet(
            f"QComboBox{{{_inp}}}QComboBox::drop-down{{border:none;width:18px;}}"
            f"QComboBox QAbstractItemView{{background:#0A1400;color:{_C_GREEN};"
            f"border:1px solid #1E3A1E;selection-background-color:#1E3A1E;}}")
        self._cat_filter.currentIndexChanged.connect(self._apply_filter)
        act_lay.addWidget(self._cat_filter)
        act_lay.addStretch()

        from widgets_base import _cd_btn as _mk_cd
        self._apply_btn = _mk_cd("0 PENDING", A_TEAL, h=34)
        self._apply_btn.setFixedHeight(34)
        self._apply_btn.setFont(QFont(FONT_MONO, 9, QFont.Weight.Bold))
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._on_apply_all)
        act_lay.addWidget(self._apply_btn)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color:#1E2E1E;"); sep.setFixedWidth(1)
        act_lay.addWidget(sep)

        from widgets_base import _cd_btn as _mk_cd2
        self._save_btn = _mk_cd2("SAVE", _C_GREEN, h=34)
        self._save_btn.setFont(QFont(FONT_MONO, 9))
        self._save_btn.clicked.connect(self._on_save_profile)
        act_lay.addWidget(self._save_btn)

        self._load_combo = QComboBox()
        self._load_combo.addItem("LOAD PROFILE…")
        self._load_combo.setFixedWidth(180); self._load_combo.setFixedHeight(34)
        self._load_combo.setFont(QFont(FONT_MONO, 9))
        self._load_combo.setStyleSheet(
            f"QComboBox{{{_inp}}}QComboBox::drop-down{{border:none;width:18px;}}"
            f"QComboBox QAbstractItemView{{background:#0A1400;color:{_C_GREEN};"
            f"border:1px solid #1E3A1E;selection-background-color:#1E3A1E;}}")
        self._load_combo.activated.connect(self._on_load_profile)
        act_lay.addWidget(self._load_combo)

        self._reset_all_btn = _mk_cd2("DEFAULTS", "#546E7A", h=34)
        self._reset_all_btn.setFont(QFont(FONT_MONO, 9))
        self._reset_all_btn.clicked.connect(self._on_reset_all)
        act_lay.addWidget(self._reset_all_btn)
        root.addWidget(act_bar)

        # ── ZONE TUILES — scroll + grille ──────────────────────────────────
        self._scroll_w = QWidget()
        self._scroll_w.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            f"stop:0 #FFFFFF, stop:1 #FFFFFF);")
        self._grid_lay = QGridLayout(self._scroll_w)
        self._grid_lay.setContentsMargins(12, 12, 12, 12)
        self._grid_lay.setSpacing(10)
        self._grid_lay.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Message "en attente"
        self._no_session_lbl = QLabel("")
        self._no_session_lbl.setFont(QFont(FONT_MONO, 11))
        self._no_session_lbl.setStyleSheet("color:#3A5A3A;background:transparent;")
        self._no_session_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._grid_lay.addWidget(self._no_session_lbl, 0, 0, 1, 6)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._scroll_w)
        self._scroll.setStyleSheet(
            f"QScrollArea{{border:none;background:#FFFFFF;}}"
            f"QScrollBar:vertical{{background:#E2E8F0;width:6px;border:none;}}"
            f"QScrollBar::handle:vertical{{background:#8DC63F;border-radius:3px;}}"
            f"QScrollBar:horizontal{{background:#E2E8F0;height:6px;border:none;}}"
            f"QScrollBar::handle:horizontal{{background:#8DC63F;border-radius:3px;}}")
        root.addWidget(self._scroll, 1)

        # Stub oscillo pour compatibilité
        self._osc = XCPOscilloscope()

        # ── LOG BAS ────────────────────────────────────────────────────────
        log_frame = QFrame()
        log_frame.setFixedHeight(30)
        log_frame.setStyleSheet("background:#0F1A0A;border-top:2px solid #000000;")
        log_lay = QHBoxLayout(log_frame)
        log_lay.setContentsMargins(10, 0, 10, 0)

        log_tag = QLabel("LOG ▸")
        log_tag.setFont(QFont(FONT_MONO, 7, QFont.Weight.Bold))
        log_tag.setStyleSheet(f"color:{_C_GREEN};background:transparent;")
        log_lay.addWidget(log_tag)

        self._log_lbl = QLabel("Waiting for connection…")
        self._log_lbl.setFont(QFont(FONT_MONO, 8))
        self._log_lbl.setStyleSheet("color:#5A8A3A;background:transparent;")
        log_lay.addWidget(self._log_lbl, 1)
        root.addWidget(log_frame)

    # Construction tuiles en grille
    def _build_cards(self, a2l: dict):
        self._a2l = a2l

        # Vider la grille
        while self._grid_lay.count():
            item = self._grid_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._cards.clear()
        self._dirty_keys.clear()

        # Grouper par catégorie
        groups: dict[str, list] = {}
        for key in a2l:
            cat = a2l[key].get("category") or _PARAM_CAT.get(key, "TIMING")
            groups.setdefault(cat, []).append(key)

        # Calcul dynamique du nombre de colonnes selon la largeur disponible
        tile_w    = XCPParamTile._TILE_W
        spacing   = 10
        margins   = 24   # 12px de chaque côté
        avail_w   = self._scroll.width() - 12  # largeur visible
        if avail_w < tile_w + margins:
            avail_w = self.width() - 12
        COLS = max(1, (avail_w - margins + spacing) // (tile_w + spacing))
        self._last_cols = COLS
        row = 0

        for cat, keys in groups.items():
            theme = _CAT.get(cat, _CAT_DEFAULT)
            acc   = theme["accent"]

            # En-tête de catégorie — barre pleine largeur
            sep = QLabel(f"  ▸  {cat}  —  {len(keys)} parameter(s)")
            sep.setFixedHeight(24)
            sep.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
            sep.setStyleSheet(
                f"background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                f"stop:0 #0F1A0A, stop:1 #070A04);"
                f"color:{acc};"
                f"border-left:3px solid {acc};"
                f"border-top:1px solid #000000;"
                f"border-bottom:1px solid #000000;"
                f"padding-left:8px;"
            )
            self._grid_lay.addWidget(sep, row, 0, 1, COLS)
            row += 1

            # Tuiles de la catégorie
            col = 0
            for key in keys:
                tile = XCPParamTile(key, a2l[key])
                tile.download_requested.connect(self._on_download)
                tile.dirty_changed.connect(self._on_dirty_changed)
                self._cards[key] = tile
                self._grid_lay.addWidget(tile, row, col)
                col += 1
                if col >= COLS:
                    col = 0; row += 1
            if col > 0:
                row += 1

        # Colonnes égales — chaque colonne s'étire uniformément
        for c in range(COLS):
            self._grid_lay.setColumnStretch(c, 1)

        # Mettre à jour le conn badge
        self._conn_badge.setText(f"● LIVE  {len(a2l)} params")
        self._conn_badge.setStyleSheet(
            f"color:{_C_GREEN}; background:transparent; padding:0 4px;"
        )

    # Feature 1 : Dirty indicator
    def _on_dirty_changed(self, key: str, dirty: bool):
        if dirty:
            self._dirty_keys.add(key)
        else:
            self._dirty_keys.discard(key)
        n = len(self._dirty_keys)
        if n > 0:
            self._apply_btn.setText(f"{n} PENDING")
            self._apply_btn.setEnabled(True)
            self._apply_btn.setStyleSheet(
                f"QPushButton {{ background:#1A0E00; color:#FFB830;"
                f"border:1px solid #FFB830; border-radius:3px; padding:0 12px; }}"
                f"QPushButton:hover {{ background:#2A1800; }}"
            )
        else:
            self._apply_btn.setText("0 PENDING")
            self._apply_btn.setEnabled(False)
            self._apply_btn.setStyleSheet(
                "QPushButton { background:#0A0E08; color:#2A3A2A;"
                "border:1px solid #1A2A1A; border-radius:3px; padding:0 12px; }"
            )

    def _on_apply_all(self):
        """Envoie en batch tous les DOWNLOAD pending."""
        dirty = list(self._dirty_keys)
        if not dirty:
            return
        reply = _ask(
            self, "Apply All Pending",
            f"Apply {len(dirty)} modified parameter(s) to BCM?",
        )
        if not reply:
            return

        for key in dirty:
            card = self._cards.get(key)
            if card and self._master:
                val = card.get_spin_value()
                self._on_download(key, val)
        self._log(f"Apply All — {len(dirty)} DOWNLOAD sent")

    # Feature 2 : Save / Load profil
    def _on_save_profile(self):
        """Snapshot des valeurs spinbox courantes -> fichier JSON."""
        if not self._cards:
            self._log("No parameter loaded", error=True)
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Sauvegarder profil XCP", "",
            "JSON Calibration (*.json)"
        )
        if not path:
            return

        snapshot = {}
        for key, card in self._cards.items():
            snapshot[key] = card.get_spin_value()

        profile = {
            "version":   "wipewash_xcp_v1",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "host":      self._host or "unknown",
            "values":    snapshot,
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(profile, f, indent=2)
            name = os.path.basename(path)
            self._log(f"Profile saved: {name}  ({len(snapshot)} params)")
            self._add_profile_to_history(name, path, snapshot)
        except Exception as e:
            self._log(f"Save failed: {e}", error=True)

    def _add_profile_to_history(self, name: str, path: str, values: dict):
        """Ajoute au QComboBox de chargement (5 max)."""
        # Eviter les doublons
        for i in range(1, self._load_combo.count()):
            if self._load_combo.itemData(i) == path:
                return
        self._load_combo.addItem(f"  {name}", userData=path)
        # Limiter à 6 entrées (1 placeholder + 5 profils)
        while self._load_combo.count() > 6:
            self._load_combo.removeItem(1)

    def _on_load_profile(self, idx: int):
        """Charge un profil JSON et applique les valeurs (+ DOWNLOAD batch optionnel)."""
        if idx == 0:
            return  # placeholder sélectionné

        path = self._load_combo.itemData(idx)
        if not path:
            # Demander un fichier
            path, _ = QFileDialog.getOpenFileName(
                self, "Load XCP Profile", "",
                "JSON Calibration (*.json)"
            )
            if not path:
                self._load_combo.setCurrentIndex(0)
                return

        try:
            with open(path, "r", encoding="utf-8") as f:
                profile = json.load(f)
        except Exception as e:
            self._log(f"Profile load failed: {e}", error=True)
            self._load_combo.setCurrentIndex(0)
            return

        values = profile.get("values", {})
        n_applied = 0
        for key, val in values.items():
            card = self._cards.get(key)
            if card:
                card.set_spin_value(val)
                n_applied += 1

        self._log(
            f"Profile loaded: {os.path.basename(path)}  "
            f"({n_applied}/{len(values)} params applied)"
        )

        # Proposer d'envoyer vers le BCM
        if _ask(
            self, "Envoyer vers BCM ?",
            f"Profile loaded ({n_applied} parameters).\n"
            f"Envoyer maintenant vers le BCM (DOWNLOAD batch) ?",
        ):
            for key, val in values.items():
                if key in self._cards and self._master:
                    self._on_download(key, val)
            self._log(f"DOWNLOAD batch — {n_applied} parameters sent to BCM")

        self._load_combo.setCurrentIndex(0)

    def _on_plot_toggle(self, key: str, checked: bool, color: str):
        pass   # oscilloscope supprimé

    def _on_osc_clear(self):
        pass   # oscilloscope supprimé

    # Filtre recherche / catégorie
    def _apply_filter(self):
        text = self._search.text().lower().strip()
        cat_filter = self._cat_filter.currentText()
        if cat_filter == "ALL CATEGORIES":
            cat_filter = ""

        for i in range(self._grid_lay.count()):
            item = self._grid_lay.itemAt(i)
            if not item or not item.widget():
                continue
            w = item.widget()

            if isinstance(w, QLabel):   # séparateur catégorie
                if cat_filter:
                    hdr_cat = w.text().split("▸")[-1].split("—")[0].strip()
                    w.setVisible(hdr_cat == cat_filter)
                else:
                    w.setVisible(True)
                continue

            if isinstance(w, XCPParamTile):
                key = w._key
                meta = self._a2l.get(key, {})
                card_cat = meta.get("category") or _PARAM_CAT.get(key, "")
                match_text = text in key.lower() or text in meta.get("desc", "").lower()
                match_cat  = (not cat_filter) or (card_cat == cat_filter)
                w.setVisible(match_text and match_cat)

    # Download / Poll
    def _on_download(self, key: str, value: Any):
        if self._master is None:
            return

        def _do():
            try:
                self._master.download(key, value)
                self._bridge.download_ok.emit(key, value)
            except XCPError as e:
                self._bridge.download_err.emit(key, str(e))

        threading.Thread(target=_do, daemon=True).start()
        self._log(f"DOWNLOAD {key} = {value}")

    def _on_download_ok(self, key: str, value: Any):
        card = self._cards.get(key)
        if card:
            card.flash_ack()
        self._log(f"DOWNLOAD {key} = {value}  ok BCM")

    def _poll_all(self):
        if self._master is None:
            return

        def _do():
            try:
                status = self._master.get_status()
                vals   = status.get("current_values", {})
                self._bridge.poll_values.emit(vals)
            except XCPError:
                pass

        threading.Thread(target=_do, daemon=True).start()

    def _apply_poll(self, vals: dict):
        if vals.get("__a2l__"):
            a2l = {k: v for k, v in vals.items() if k != "__a2l__"}
            self._a2l = a2l
            self._build_cards(a2l)
            for card in self._cards.values():
                card.set_session_active(True)
            return
        for key, val in vals.items():
            card = self._cards.get(key)
            if card and val is not None:
                card.update_live(val)
            # Push vers oscilloscope
            self._osc.push_value(key, float(val) if val is not None else 0)

    # Reset all
    def _on_reset_all(self):
        if not _ask(
            self, "Reset All",
            "Reset ALL BCM parameters to default A2L values?",
        ):
            return

        def _do():
            try:
                results = self._master.restore_all_defaults()
                n_ok    = sum(1 for ok in results.values() if ok)
                self._bridge.log_msg.emit(
                    f"RESET ALL — {n_ok}/{len(results)} paramètres remis à défaut", False)
            except XCPError as e:
                self._bridge.log_msg.emit(f"RESET ALL échoué: {e}", True)

        threading.Thread(target=_do, daemon=True).start()

    # Response handler / Log
    def resizeEvent(self, event):
        """Recrée la grille si le nombre de colonnes change."""
        super().resizeEvent(event)
        if not self._a2l:
            return
        tile_w  = XCPParamTile._TILE_W
        spacing = 10
        margins = 24
        avail_w = self._scroll.width() - 12
        if avail_w < tile_w + margins:
            avail_w = self.width() - 12
        new_cols = max(1, (avail_w - margins + spacing) // (tile_w + spacing))
        if new_cols != self._last_cols:
            self._last_cols = new_cols
            self._build_cards(self._a2l)

    def _on_response(self, cmd: str, status: str,
                     data: object, error: object):
        pass

    def _log(self, msg: str, error: bool = False):
        # Sentinel interne pour réactiver le bouton CONNECT depuis un thread bg
        if msg == "__restore_btn__":
            self._restore_connect_btn()
            return
        ts  = time.strftime("%H:%M:%S")
        col = "#FF6B35" if error else "#3A6A3A"
        self._log_lbl.setStyleSheet(f"color:{col}; background:transparent;")
        self._log_lbl.setText(f"[{ts}]  {msg}")

    def set_host(self, host: str) -> None:
        """Set the target host IP address (called from main_window or tests)."""
        self._host = host

    def _on_connect_clicked(self) -> None:
        """Bouton CONNECT dans le header — lance la connexion XCP vers le BCM."""
        host = self._ip_edit.text().strip()
        if not host:
            self._log("Entrez une adresse IP BCM avant de connecter.", error=True)
            return

        # Déjà connecté au même host
        if self._host == host and self._master is not None:
            self._log(f"Already connected to {host}.")
            return

        self._host = host
        self._connect_btn.setEnabled(False)
        self._connect_btn.setText("...")
        self._conn_badge.setText("◌ CONNECTING…")
        self._conn_badge.setStyleSheet(
            f"color:#FFB830; background:transparent; padding:0 4px;"
        )
        self._log(f"Connecting XCP → {host}…")

        def _on_resp(cmd, status, data, error):
            self._bridge.response_received.emit(cmd, status or "", data, error)

        self._master = XCPMaster(host, on_response=_on_resp)

        def _load():
            try:
                a2l = self._master.get_a2l()
                self._bridge.log_msg.emit(
                    f"BCM {host} — {len(a2l)} paramètres XCP prêts", False)
                self._bridge.poll_values.emit({"__a2l__": True, **a2l})
                try:
                    status = self._master.get_status()
                    live   = status.get("current_values", {})
                    if live:
                        self._bridge.poll_values.emit(live)
                except Exception:
                    pass
            except Exception as e:
                self._bridge.get_a2l_err.emit(str(e))
            finally:
                # Réactiver le bouton dans le thread Qt
                self._bridge.log_msg.emit("__restore_btn__", False)

        threading.Thread(target=_load, daemon=True).start()

    def _restore_connect_btn(self) -> None:
        """Réactive le bouton CONNECT après tentative (succès ou échec)."""
        self._connect_btn.setEnabled(True)
        self._connect_btn.setText("CONNECT")

