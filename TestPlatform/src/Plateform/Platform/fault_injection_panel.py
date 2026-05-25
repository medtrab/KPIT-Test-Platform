"""
fault_injection_panel.py  --  Panneau Injection de Defauts (HIL Standalone)
============================================================================
REDESIGN : dSPACE ControlDesk / Scalexio FIU style
  - Palette KPIT claire (blanc, vert KPIT #8DC63F, accents)
  - Arc gauges pour motor/pump current (style pump/motor page)
  - Layout 3 colonnes : Mesures | Scene + Modes | Connexions
  - Boutons de mode avec nom affiché en permanence
  - Pas d'emojis — tags textuels [NRM] [OPN] [SHV] etc.
  - Pas de GPIO MAP ni MODE LEGEND
  - Animations circuit pour chaque mode
  - Style professionnel HIL/ControlDesk
  - Boutons avec bordure noire

REVISION :
  [POMPE_ONLY]   Cible MOTOR conservee en affichage uniquement
  [COMBINED_BTN] Boutons combines MODE + DUTY en un seul clic
  [SHORT_GND]    Mode "SHORT TO GND" ajoute
  [SIM_CMD]      send_fault() transmet duty_cycle dans payload JSON
  [FIX_BUTTON_TEXT] Boutons affichent leur texte en permanence (visible sans hover)
  [BLACK_BORDER] Ajout d'une bordure noire pour chaque bouton
  [SMALLER_BUTTONS] Reduction de la taille des boutons
  [DARK_PLOT] Fond noir pour la courbe Real-time current
"""

import math
import time
from collections import deque

import json, os, datetime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QPushButton, QSizePolicy, QGridLayout,
    QDialog, QListWidget, QListWidgetItem, QLineEdit, QComboBox,
    QSpinBox, QDoubleSpinBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QMessageBox, QDialogButtonBox,
    QCheckBox, QScrollArea
)
from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, Signal
from PySide6.QtGui import (
    QColor, QFont, QPainter, QPen, QPainterPath, QBrush,
    QLinearGradient, QRadialGradient, QConicalGradient
)

from constants import (
    FONT_UI, FONT_MONO,
    W_BG, W_PANEL, W_PANEL2, W_PANEL3,
    W_BORDER, W_BORDER2, W_TOOLBAR, W_TITLEBAR,
    W_TEXT, W_TEXT_DIM, W_TEXT_HDR,
    A_TEAL, A_TEAL2, A_GREEN, A_GREEN_BG,
    A_RED, A_RED_BG, A_ORANGE, A_ORANGE_BG, A_AMBER,
    KPIT_GREEN,
)
from widgets_base import (
    StatusLed, InstrumentPanel,
    _lbl, _hsep, _cd_btn,
)


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
    background-color: #E0EED0;
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
"""

def _light_msg(parent, title: str, text: str, kind: str = "info") -> None:
    """Affiche un QMessageBox clair (information ou warning)."""
    mb = QMessageBox(parent)
    mb.setWindowTitle(title)
    mb.setText(text)
    if kind == "warning":
        mb.setIcon(QMessageBox.Icon.Warning)
    else:
        mb.setIcon(QMessageBox.Icon.Information)
    mb.setStandardButtons(QMessageBox.StandardButton.Ok)
    mb.setStyleSheet(_DIALOG_STYLE)
    mb.exec()

def _ask_light(parent, title: str, text: str) -> bool:
    """QMessageBox.question stylé clair — retourne True si Oui/Yes."""
    mb = QMessageBox(parent)
    mb.setWindowTitle(title)
    mb.setText(text)
    mb.setIcon(QMessageBox.Icon.Question)
    mb.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    mb.setDefaultButton(QMessageBox.StandardButton.Yes)
    mb.setStyleSheet(_DIALOG_STYLE)
    return mb.exec() == QMessageBox.StandardButton.Yes


# ══════════════════════════════════════════════════════════════
#  PALETTE KPIT — ControlDesk / Scalexio style
# ══════════════════════════════════════════════════════════════
_BG             = W_BG
_PANEL          = W_PANEL
_PANEL2         = W_PANEL2
_PANEL3         = W_PANEL3
_TOOLBAR        = W_TOOLBAR
_TITLEBAR       = W_TITLEBAR
_BORDER         = "rgba(141,198,63,0.20)"
_BORDER_BRIGHT  = "rgba(141,198,63,0.35)"

_KPIT           = "#5E8A28"   # darker KPIT green (less vivid)
_KPIT_DIM       = "rgba(94,138,40,0.15)"

_TEXT           = W_TEXT
_TEXT_DIM       = W_TEXT_DIM
_TEXT_HDR       = W_TEXT_HDR

# Mode accents
_COL_NORMAL     = "#5AAF20"
_COL_OPENLOAD   = "#D35400"
_COL_SHORTVCC   = "#C0392B"
_COL_VLOAD      = "#007ACC"
_COL_SHORTGND   = "#6A1B9A"
_COL_SEQ        = "#F39C12"   # amber  — séquenceur
_COL_PROF       = "#00BCD4"   # cyan   — profils
_COL_SAVE       = "#5AAF20"   # KPIT   — sauvegarde

_MODE_COLORS = {
    "NORMAL":        (_COL_NORMAL,    "#3A7A10", "#F4FFEF"),
    "OPEN LOAD":     (_COL_OPENLOAD,  "#A84200", "#FEF5E7"),
    "SHORT TO VCC":  (_COL_SHORTVCC,  "#962D22", "#FDEDEC"),
    "VARIABLE LOAD": (_COL_VLOAD,     "#005F9E", "#E0F0FF"),
    "SHORT TO GND":  (_COL_SHORTGND,  "#4A1270", "#F3E5F5"),
}

_MODE_TAGS = {
    "NORMAL":        "NRM",
    "OPEN LOAD":     "OPN",
    "SHORT TO VCC":  "SHV",
    "VARIABLE LOAD": "VLD",
    "SHORT TO GND":  "SHG",
}

# Display labels shown in the UI (internal keys unchanged)
_MODE_DISPLAY_LABELS = {
    "NORMAL":        "NORMAL",
    "OPEN LOAD":     "SENSOR DISCONNECTION",
    "SHORT TO VCC":  "SIGNAL OVER-VOLTAGE",
    "VARIABLE LOAD": "RESISTIVE LOAD VARIATION",
    "SHORT TO GND":  "SIGNAL PULL-DOWN",
}

_MODES_COMBINED = [
    ("RESTORE NORMAL",          "NORMAL",        0,   _COL_NORMAL),
    ("SENSOR DISCONNECTION",    "OPEN LOAD",     0,   _COL_OPENLOAD),
    ("SIGNAL OVER-VOLTAGE",     "SHORT TO VCC",  0,   _COL_SHORTVCC),
    ("SIGNAL PULL-DOWN",        "SHORT TO GND",  0,   _COL_SHORTGND),
    ("25%",  "VARIABLE LOAD", 25,  _COL_VLOAD),
    ("50%",  "VARIABLE LOAD", 50,  _COL_VLOAD),
    ("75%",  "VARIABLE LOAD", 75,  _COL_VLOAD),
    ("100%", "VARIABLE LOAD", 100, _COL_VLOAD),
]

_FONT_HMI   = FONT_MONO
_FONT_LABEL = FONT_UI


# ══════════════════════════════════════════════════════════════
#  ARC GAUGE — ControlDesk instrument style (like pump/motor page)
# ══════════════════════════════════════════════════════════════
class _FaultArcGauge(QWidget):
    """Pro arc gauge for current display — white background, KPIT style."""
    def __init__(self, max_val=1.5, unit="A", label="CURRENT", parent=None):
        super().__init__(parent)
        self._val   = 0.0
        self._max   = max_val
        self._unit  = unit
        self._label = label
        self._fault = False
        self.setFixedHeight(160)
        self.setMinimumWidth(160)

    def set_value(self, val, fault=False):
        self._val   = val
        self._fault = fault
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        # ── fond blanc pur ──────────────────────────────────────
        p.fillRect(0, 0, W, H, QBrush(QColor("#FFFFFF")))

        cx   = W // 2
        cy   = int(H * 0.55)
        R    = int(min(W, H) * 0.38)
        pct  = min(self._val / max(self._max, 1e-9), 1.0)

        # arc: 220° de -220° à 40° (convention Qt: 0=droite, CCW)
        START_DEG = 220   # angle de départ (bas-gauche)
        SPAN_DEG  = 260   # amplitude totale

        # ── cercle ombre extérieur ──────────────────────────────
        shadow = QRadialGradient(cx + 2, cy + 3, R + 12)
        shadow.setColorAt(0, QColor(0, 0, 0, 0))
        shadow.setColorAt(0.85, QColor(0, 0, 0, 0))
        shadow.setColorAt(1.0,  QColor(0, 0, 0, 30))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(shadow))
        p.drawEllipse(cx - R - 12, cy - R - 12, (R + 12) * 2, (R + 12) * 2)

        # ── fond circulaire blanc avec bordure fine ─────────────
        face_grad = QRadialGradient(cx - R * 0.2, cy - R * 0.2, R * 1.3)
        face_grad.setColorAt(0, QColor("#FFFFFF"))
        face_grad.setColorAt(1, QColor("#F0F4F0"))
        p.setBrush(QBrush(face_grad))
        p.setPen(QPen(QColor("#CCCCCC"), 1.5))
        p.drawEllipse(cx - R, cy - R, R * 2, R * 2)

        # ── bezel extérieur ─────────────────────────────────────
        bezel = QConicalGradient(cx, cy, -130)
        bezel.setColorAt(0.0,  QColor("#111111"))
        bezel.setColorAt(0.20, QColor("#444444"))
        bezel.setColorAt(0.40, QColor("#1A1A1A"))
        bezel.setColorAt(0.60, QColor("#555555"))
        bezel.setColorAt(0.80, QColor("#1A1A1A"))
        bezel.setColorAt(1.0,  QColor("#111111"))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QBrush(bezel), 6))
        p.drawEllipse(cx - R - 1, cy - R - 1, (R + 1) * 2, (R + 1) * 2)

        # ── zones de fond de l'arc (grisé) ─────────────────────
        arc_rect = QRectF(cx - R + 10, cy - R + 10, (R - 10) * 2, (R - 10) * 2)
        ARC_W = max(8, R // 5)

        zones = [
            (0,    0.50, "#4CAF50", 40),   # vert   0–50%
            (0.50, 0.75, "#FF9800", 40),   # orange 50–75%
            (0.75, 1.00, "#F44336", 40),   # rouge  75–100%
        ]
        for z0, z1, zc, alpha in zones:
            c = QColor(zc); c.setAlpha(alpha)
            p.setPen(QPen(c, ARC_W, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            a_start = int((START_DEG - SPAN_DEG * z0) * 16)
            a_span  = int(-SPAN_DEG * (z1 - z0) * 16)
            p.drawArc(arc_rect, a_start, a_span)

        # ── arc de fond gris léger ──────────────────────────────
        p.setPen(QPen(QColor("#DDDDDD"), ARC_W, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        p.drawArc(arc_rect, int(START_DEG * 16), int(-SPAN_DEG * 16))

        # ── arc valeur ──────────────────────────────────────────
        if pct > 0.005:
            if self._fault:
                arc_col = QColor("#F44336")
            elif pct >= 0.75:
                arc_col = QColor("#F44336")
            elif pct >= 0.50:
                arc_col = QColor("#FF9800")
            else:
                arc_col = QColor("#4CAF50")
            val_pen = QPen(arc_col, ARC_W, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(val_pen)
            p.drawArc(arc_rect, int(START_DEG * 16), int(-SPAN_DEG * pct * 16))

        # ── graduations majeures + labels ───────────────────────
        NTICKS = 5
        for i in range(NTICKS + 1):
            frac = i / NTICKS
            ang_deg = START_DEG - SPAN_DEG * frac
            ang_rad = math.radians(ang_deg)
            Ro = R - 6
            Ri = R - 6 - (10 if i % 1 == 0 else 5)
            x1 = cx + Ro * math.cos(ang_rad)
            y1 = cy - Ro * math.sin(ang_rad)
            x2 = cx + Ri * math.cos(ang_rad)
            y2 = cy - Ri * math.sin(ang_rad)
            p.setPen(QPen(QColor("#888888"), 1.5))
            p.drawLine(int(x1), int(y1), int(x2), int(y2))
            # label tick
            Rlbl = R - 6 - 18
            xl = cx + Rlbl * math.cos(ang_rad)
            yl = cy - Rlbl * math.sin(ang_rad)
            val_tick = self._max * frac
            lbl_str = f"{val_tick:.1f}" if val_tick != int(val_tick) else f"{int(val_tick)}"
            p.setFont(QFont(_FONT_HMI, 6))
            p.setPen(QPen(QColor("#666666")))
            p.drawText(int(xl) - 12, int(yl) - 6, 24, 12, Qt.AlignmentFlag.AlignCenter, lbl_str)

        # ── aiguille fine ───────────────────────────────────────
        needle_ang = math.radians(START_DEG - SPAN_DEG * pct)
        needle_col = QColor("#1A1A2E")
        Rn = R - 14
        tip_x = cx + Rn * math.cos(needle_ang)
        tip_y = cy - Rn * math.sin(needle_ang)
        # ombre aiguille
        p.setPen(QPen(QColor(0, 0, 0, 40), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(cx + 1, cy + 1, int(tip_x) + 1, int(tip_y) + 1)
        # aiguille
        p.setPen(QPen(needle_col, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(cx, cy, int(tip_x), int(tip_y))

        # ── pivot central ───────────────────────────────────────
        pivot_grad = QRadialGradient(cx - 2, cy - 2, 8)
        pivot_grad.setColorAt(0, QColor("#AAAAAA"))
        pivot_grad.setColorAt(1, QColor("#333333"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(pivot_grad))
        p.drawEllipse(cx - 5, cy - 5, 10, 10)

        # ── valeur numérique ────────────────────────────────────
        if self._fault:
            val_col = QColor("#F44336")
        elif pct >= 0.75:
            val_col = QColor("#F44336")
        elif pct >= 0.50:
            val_col = QColor("#FF9800")
        else:
            val_col = QColor("#2E7D32")

        p.setFont(QFont(_FONT_HMI, 13, QFont.Weight.Bold))
        p.setPen(QPen(val_col))
        p.drawText(0, cy + 8, W, 20, Qt.AlignmentFlag.AlignCenter, f"{self._val:.3f}")

        p.setFont(QFont(_FONT_HMI, 7))
        p.setPen(QPen(QColor("#888888")))
        p.drawText(0, cy + 26, W, 14, Qt.AlignmentFlag.AlignCenter, self._unit)

        # ── label titre ─────────────────────────────────────────
        p.setFont(QFont(_FONT_LABEL, 7, QFont.Weight.Bold))
        p.setPen(QPen(QColor("#444444")))
        p.drawText(0, 4, W, 14, Qt.AlignmentFlag.AlignCenter, self._label)


# ══════════════════════════════════════════════════════════════
#  COURBE TEMPS REEL — ControlDesk oscilloscope style avec fond noir
# ══════════════════════════════════════════════════════════════
class DualCurrentPlot(QWidget):
    HISTORY = 300

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._pump_buf  = deque([0.0]*self.HISTORY, maxlen=self.HISTORY)
        self._motor_buf = deque([0.0]*self.HISTORY, maxlen=self.HISTORY)
        self._pump_max  = 1.5
        self._motor_max = 1.0
        self._ml = 45; self._mr = 45; self._mt = 15; self._mb = 20

    def push(self, p_val, m_val):
        self._pump_buf.append(p_val)
        self._motor_buf.append(m_val)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        ml, mr, mt, mb = self._ml, self._mr, self._mt, self._mb
        pw = w - ml - mr; ph = h - mt - mb

        # Fond noir pour l'oscilloscope
        p.fillRect(0, 0, w, h, QBrush(QColor("#0a0a0a")))
        
        # Bordure externe
        p.setPen(QPen(QColor(141, 198, 63, 80), 1))
        p.drawRect(0, 0, w - 1, h - 1)

        # Grille avec traits fins
        p.setPen(QPen(QColor(141, 198, 63, 40), 1))
        for i in range(5):
            y = mt + int(i * ph / 4)
            p.drawLine(ml, y, ml + pw, y)
        for i in range(7):
            x = ml + int(i * pw / 6)
            p.drawLine(x, mt, x, mt + ph)

        # Cadre de la zone de tracé
        p.setPen(QPen(QColor(141, 198, 63, 120), 1))
        p.drawRect(ml, mt, pw, ph)

        # Échelles
        p.setFont(QFont(_FONT_HMI, 7))
        for i in range(5):
            y = mt + int(i * ph / 4)
            p.setPen(QPen(QColor(_COL_NORMAL)))
            p.drawText(0, y - 6, ml - 4, 12, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{self._pump_max * (1 - i / 4):.1f}")
        for i in range(5):
            y = mt + int(i * ph / 4)
            p.setPen(QPen(QColor(_COL_VLOAD)))
            p.drawText(ml + pw + 4, y - 6, mr - 4, 12, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       f"{self._motor_max * (1 - i / 4):.1f}")

        p.setPen(QPen(QColor(_TEXT_DIM))); p.setFont(QFont(_FONT_HMI, 6))
        p.drawText(ml, h - mb + 2, pw // 2, mb, Qt.AlignmentFlag.AlignLeft, f"-{self.HISTORY // 10}s")
        p.drawText(ml, h - mb + 2, pw, mb, Qt.AlignmentFlag.AlignRight, "now")

        def fill_area(buf, ymax, col):
            data = list(buf); n = len(data)
            if n < 2: return
            c = QColor(col); path = QPainterPath()
            path.moveTo(ml, mt + ph)
            for i, v in enumerate(data):
                x = ml + int(i * pw / (n - 1))
                y = mt + ph - int(min(max(v, 0), ymax) / ymax * ph)
                path.lineTo(x, y)
            path.lineTo(ml + pw, mt + ph); path.closeSubpath()
            grad = QLinearGradient(0, mt, 0, mt + ph)
            grad.setColorAt(0, QColor(c.red(), c.green(), c.blue(), 60))
            grad.setColorAt(1, QColor(c.red(), c.green(), c.blue(), 10))
            p.fillPath(path, QBrush(grad))

        def draw_line(buf, ymax, col):
            data = list(buf); n = len(data)
            if n < 2: return
            p.setPen(QPen(QColor(col), 1.5)); path = QPainterPath()
            for i, v in enumerate(data):
                x = ml + int(i * pw / (n - 1))
                y = mt + ph - int(min(max(v, 0), ymax) / ymax * ph)
                if i == 0: path.moveTo(x, y)
                else:       path.lineTo(x, y)
            p.drawPath(path)

        fill_area(self._pump_buf, self._pump_max, _COL_NORMAL)
        fill_area(self._motor_buf, self._motor_max, _COL_VLOAD)
        draw_line(self._pump_buf, self._pump_max, _COL_NORMAL)
        draw_line(self._motor_buf, self._motor_max, _COL_VLOAD)

        ip = list(self._pump_buf)[-1]; im = list(self._motor_buf)[-1]
        p.setFont(QFont(_FONT_HMI, 8, QFont.Weight.Bold))
        p.setPen(QPen(QColor(_COL_NORMAL)))
        p.drawText(ml + 4, mt + 3, 150, 16, Qt.AlignmentFlag.AlignLeft, f"PUMP  {ip:.3f} A")
        p.setPen(QPen(QColor(_COL_VLOAD)))
        p.drawText(ml + pw - 150, mt + 3, 150, 16, Qt.AlignmentFlag.AlignRight, f"MOTOR  {im:.3f} A")


# ══════════════════════════════════════════════════════════════
#  SCENE VISUELLE — circuit animations per mode  (PRO REWRITE)
# ══════════════════════════════════════════════════════════════
class FaultSceneWidget(QWidget):
    H_PX = 220   # taller for better visibility

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(self.H_PX)
        self.setMaximumHeight(self.H_PX)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._mode  = "NORMAL"
        self._target = "PUMP"
        self._phase = 0.0
        self._duty  = 50.0
        self._timer = QTimer(self)
        self._timer.setInterval(33)   # ~30 fps
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_mode(self, m, duty=50.0):
        self._mode = m; self._duty = duty; self._phase = 0.0
        self.update()

    def set_target(self, t):
        self._target = t; self.update()

    def _tick(self):
        self._phase = (self._phase + 0.07) % (2 * math.pi * 100)
        self.update()

    # ── helpers ────────────────────────────────────────────────
    def _bg(self, p, w, h, accent):
        """Dark oscilloscope-style background with accent glow."""
        p.fillRect(0, 0, w, h, QBrush(QColor("#0D1117")))
        # subtle radial accent glow
        c = QColor(accent)
        grad = QRadialGradient(w // 2, h // 2, w * 0.6)
        grad.setColorAt(0, QColor(c.red(), c.green(), c.blue(), 18))
        grad.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(0, 0, w, h, QBrush(grad))
        # fine grid
        p.setPen(QPen(QColor(255, 255, 255, 12), 1.0))
        for gx in range(0, w, 20): p.drawLine(gx, 0, gx, h)
        for gy in range(0, h, 20): p.drawLine(0, gy, w, gy)
        # border
        p.setPen(QPen(QColor(accent), 1.5))
        p.drawRoundedRect(1, 1, w - 2, h - 2, 6, 6)
        # mode badge top-right
        tag  = _MODE_TAGS.get(self._mode, "---")
        badge = self._mode + (f"  {self._duty:.0f}%" if self._mode == "VARIABLE LOAD" else "")
        p.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
        p.setPen(QPen(QColor(accent)))
        p.drawText(0, 4, w - 6, 14, Qt.AlignmentFlag.AlignRight, badge)

    def _draw_wire(self, p, x1, y1, x2, y2, col, width=2.0, dashed=False, glow=False):
        """Draw a wire segment, optionally with glow halo."""
        if glow:
            gc = QColor(col); gc.setAlpha(40)
            pen = QPen(gc, float(width) * 4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen); p.drawLine(x1, y1, x2, y2)
        pen = QPen(QColor(col), float(width))
        if dashed: pen.setStyle(Qt.PenStyle.DashLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen); p.drawLine(x1, y1, x2, y2)

    def _draw_box(self, p, cx, cy, bw, bh, label, col, active=True, sub=""):
        """Draw a labelled component box."""
        x, y = cx - bw // 2, cy - bh // 2
        fc = QColor(col) if active else QColor(_TEXT_DIM)
        # body gradient
        grad = QLinearGradient(x, y, x, y + bh)
        grad.setColorAt(0, QColor(30, 34, 42))
        grad.setColorAt(1, QColor(18, 20, 26))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(fc, 1.5))
        p.drawRoundedRect(x, y, bw, bh, 5, 5)
        # glow border
        if active:
            gc = QColor(fc.red(), fc.green(), fc.blue(), 50)
            p.setPen(QPen(gc, 4.0))
            p.drawRoundedRect(x - 1, y - 1, bw + 2, bh + 2, 6, 6)
        # label
        p.setPen(QPen(fc))
        p.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
        if sub:
            p.drawText(x, y + 3, bw, bh // 2 + 2, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, label)
            p.setFont(QFont(_FONT_HMI, 5))
            p.setPen(QPen(QColor(fc.red(), fc.green(), fc.blue(), 160)))
            p.drawText(x, y + bh // 2, bw, bh // 2, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, sub)
        else:
            p.drawText(x, y, bw, bh, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, label)

    def _draw_particles(self, p, x1, x2, y, col, phase_offset, n=8, speed=0.5, size=3):
        """Animate glowing electrons along a horizontal wire."""
        p.setPen(Qt.PenStyle.NoPen)
        c = QColor(col)
        for i in range(n):
            t = (self._phase * speed + i / n + phase_offset) % 1.0
            px = int(x1 + t * (x2 - x1))
            alpha = int(230 * math.sin(t * math.pi))
            # outer glow
            p.setBrush(QBrush(QColor(c.red(), c.green(), c.blue(), alpha // 4)))
            p.drawEllipse(px - size * 2, y - size * 2, size * 4, size * 4)
            # core
            p.setBrush(QBrush(QColor(min(255, c.red() + 80), min(255, c.green() + 80),
                                     min(255, c.blue() + 80), alpha)))
            p.drawEllipse(px - size, y - size, size * 2, size * 2)

    def _circuit_layout(self, w, h):
        """Return standard (batt_cx, hbridge_cx, load_cx, wire_y_top, wire_y_bot)."""
        cy = h // 2 - 8
        margin = 50
        span   = min(w - 2 * margin, 350)
        bx = w // 2 - span // 2 + 25
        lx = w // 2 + span // 2 - 25
        hx = w // 2
        return bx, hx, lx, cy - 16, cy + 16

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        clip = QPainterPath(); clip.addRoundedRect(1, 1, w - 2, h - 2, 6, 6)
        p.setClipPath(clip)
        {
            "NORMAL":        self._draw_normal,
            "OPEN LOAD":     self._draw_open_load,
            "SHORT TO VCC":  self._draw_short_vcc,
            "VARIABLE LOAD": self._draw_variable_load,
            "SHORT TO GND":  self._draw_short_gnd,
        }.get(self._mode, self._draw_normal)(p, w, h)
        p.end()

    # ══════════════════════════════════════════════════════════
    #  MODE : NORMAL — steady green current flow
    # ══════════════════════════════════════════════════════════
    def _draw_normal(self, p, w, h):
        self._bg(p, w, h, _COL_NORMAL)
        bx, hx, lx, yt, yb = self._circuit_layout(w, h)
        lbl = "MOTOR" if self._target == "MOTOR" else "PUMP"

        # wires — pulsing brightness
        pulse = 0.6 + 0.4 * abs(math.sin(self._phase * 1.2))
        wc = QColor(int(100 * pulse), int(198 * pulse), int(50 * pulse))
        self._draw_wire(p, bx + 24, yt, hx - 30, yt, wc.name(), 2.0, glow=True)
        self._draw_wire(p, hx + 30, yt, lx - 24, yt, wc.name(), 2.0, glow=True)
        self._draw_wire(p, bx + 24, yb, hx - 30, yb, "#334433", 1.2)
        self._draw_wire(p, hx + 30, yb, lx - 24, yb, "#334433", 1.2)

        # components
        self._draw_box(p, bx, (yt + yb) // 2, 48, 40, "BATT", _COL_NORMAL, True, "12V")
        self._draw_box(p, hx, (yt + yb) // 2, 58, 42, "H-BRIDGE", _COL_NORMAL, True, "L298N")
        self._draw_box(p, lx, (yt + yb) // 2, 48, 40, lbl, _COL_NORMAL, True)

        # flowing electrons (top wire)
        self._draw_particles(p, bx + 24, lx - 24, yt, _COL_NORMAL, 0.0, n=8, speed=0.18, size=3)

        # current ammeter display (bottom centre)
        amp_x, amp_y = w // 2, h - 28
        self._draw_box(p, amp_x, amp_y, 80, 20, "I  OK", _COL_NORMAL, True)

        # status label
        p.setPen(QPen(QColor(_COL_NORMAL)))
        p.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
        p.drawText(0, h - 14, w, 12, Qt.AlignmentFlag.AlignHCenter,
                   "NORMAL OPERATION")

    # ══════════════════════════════════════════════════════════
    #  MODE : OPEN LOAD — wire severed, I = 0
    # ══════════════════════════════════════════════════════════
    def _draw_open_load(self, p, w, h):
        self._bg(p, w, h, _COL_OPENLOAD)
        bx, hx, lx, yt, yb = self._circuit_layout(w, h)
        lbl = "MOTOR" if self._target == "MOTOR" else "PUMP"
        cut_x = hx + 55

        # wire before cut — orange/active
        self._draw_wire(p, bx + 24, yt, hx - 30, yt, _COL_OPENLOAD, 2.0, glow=True)
        self._draw_wire(p, hx + 30, yt, cut_x - 10, yt, _COL_OPENLOAD, 2.0, glow=True)
        # wire after cut — dead / grey dashed
        self._draw_wire(p, cut_x + 10, yt, lx - 24, yt, "#444444", 1.2, dashed=True)
        self._draw_wire(p, bx + 24, yb, hx - 30, yb, "#444444", 1.2)
        self._draw_wire(p, hx + 30, yb, lx - 24, yb, "#444444", 1.2)

        # components
        self._draw_box(p, bx, (yt + yb) // 2, 48, 40, "BATT", _COL_OPENLOAD, True, "12V")
        self._draw_box(p, hx, (yt + yb) // 2, 58, 42, "H-BRIDGE", "#555555", False, "L298N")
        self._draw_box(p, lx, (yt + yb) // 2, 48, 40, lbl, "#555555", False)

        # electrons building up at cut
        self._draw_particles(p, bx + 24, cut_x - 12, yt, _COL_OPENLOAD, 0.0, n=5, speed=0.14, size=3)

        # CUT MARK — animated X
        flash = math.sin(self._phase * 3.5)
        xc = QColor(_COL_SHORTVCC) if flash > 0.3 else QColor(_COL_OPENLOAD)
        p.setPen(QPen(xc, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(cut_x - 6, yt - 12, cut_x + 6, yt + 2)
        p.drawLine(cut_x + 6, yt - 12, cut_x - 6, yt + 2)
        
        # ammeter shows 0
        self._draw_box(p, w // 2, h - 28, 80, 20, "I = 0 A", _COL_OPENLOAD, True)

        p.setPen(QPen(QColor(_COL_OPENLOAD)))
        p.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
        p.drawText(0, h - 14, w, 12, Qt.AlignmentFlag.AlignHCenter,
                   "SENSOR DISCONNECTION — No current")

    # ══════════════════════════════════════════════════════════
    #  MODE : SHORT TO VCC — direct short, max current danger
    # ══════════════════════════════════════════════════════════
    def _draw_short_vcc(self, p, w, h):
        self._bg(p, w, h, _COL_SHORTVCC)
        bx, hx, lx, yt, yb = self._circuit_layout(w, h)
        lbl = "MOTOR" if self._target == "MOTOR" else "PUMP"

        # ALL wires flashing bright red
        fi = abs(math.sin(self._phase * 4))
        wbright = int(160 + 95 * fi)
        wc = QColor(wbright, int(30 * (1 - fi)), int(30 * (1 - fi)))
        for y2 in (yt, yb):
            self._draw_wire(p, bx + 24, y2, hx - 30, y2, wc.name(), 2.5, glow=True)
            self._draw_wire(p, hx + 30, y2, lx - 24, y2, wc.name(), 2.5, glow=True)

        # Short-circuit bridge
        cx = hx + 38
        p.setPen(QPen(QColor(_COL_SHORTVCC), 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(cx, yt, cx, yb)
        p.setFont(QFont(_FONT_HMI, 6, QFont.Weight.Bold))
        p.setPen(QPen(QColor(_COL_SHORTVCC)))
        p.drawText(cx - 14, (yt + yb) // 2 - 6, 28, 12, Qt.AlignmentFlag.AlignHCenter, "SHORT")

        # components
        self._draw_box(p, bx, (yt + yb) // 2, 48, 40, "BATT", _COL_SHORTVCC, True, "12V")
        self._draw_box(p, hx, (yt + yb) // 2, 58, 42, "H-BRIDGE", _COL_SHORTVCC, True, "L298N")
        self._draw_box(p, lx, (yt + yb) // 2, 48, 40, lbl, _COL_SHORTVCC, True)

        # Rapid high-speed electrons
        self._draw_particles(p, bx + 24, lx - 24, yt, "#FF6060", 0.0, n=12, speed=0.55, size=4)

        # danger ammeter
        self._draw_box(p, w // 2, h - 28, 90, 20, "I = MAX!", _COL_SHORTVCC, True)

        p.setPen(QPen(QColor(_COL_SHORTVCC)))
        p.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
        p.drawText(0, h - 14, w, 12, Qt.AlignmentFlag.AlignHCenter,
                   "SIGNAL OVER-VOLTAGE — Overcurrent")

    # ══════════════════════════════════════════════════════════
    #  MODE : VARIABLE LOAD — PWM duty-cycle controlled
    # ══════════════════════════════════════════════════════════
    def _draw_variable_load(self, p, w, h):
        self._bg(p, w, h, _COL_VLOAD)
        bx, hx, lx, yt, yb = self._circuit_layout(w, h)
        lbl  = "MOTOR" if self._target == "MOTOR" else "PUMP"
        amp  = self._duty / 100.0

        # Wire colour pulses with PWM
        pwm_state = math.sin(self._phase * (2 + amp * 8)) > (1 - 2 * amp)
        wc = _COL_VLOAD if pwm_state else "#1A3050"
        self._draw_wire(p, bx + 24, yt, hx - 30, yt, wc, 2.0, glow=pwm_state)
        self._draw_wire(p, hx + 30, yt, lx - 24, yt, wc, 2.0, glow=pwm_state)
        self._draw_wire(p, bx + 24, yb, hx - 30, yb, "#223344", 1.2)
        self._draw_wire(p, hx + 30, yb, lx - 24, yb, "#223344", 1.2)

        # components
        self._draw_box(p, bx, (yt + yb) // 2, 48, 40, "BATT", _COL_VLOAD, True, "12V")
        self._draw_box(p, hx, (yt + yb) // 2, 58, 42, "H-BRIDGE", _COL_VLOAD, True, "L298N")
        self._draw_box(p, lx, (yt + yb) // 2, 48, 40, lbl, _COL_VLOAD, True)

        # electrons
        if amp > 0.05:
            self._draw_particles(p, bx + 24, lx - 24, yt, _COL_VLOAD, 0.0,
                                 n=max(2, int(amp * 10)), speed=amp * 0.4, size=int(2 + amp * 3))

        # PWM oscilloscope (mini)
        osc_w, osc_h = 120, 40
        ox = (w - osc_w) // 2
        oy = h - osc_h - 20
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(QColor(10, 18, 30)))
        p.drawRoundedRect(ox, oy, osc_w, osc_h, 3, 3)
        p.setPen(QPen(QColor(_COL_VLOAD), 1.0)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(ox, oy, osc_w, osc_h, 3, 3)
        
        # duty label
        p.setFont(QFont(_FONT_HMI, 6, QFont.Weight.Bold))
        p.setPen(QPen(QColor(_COL_VLOAD)))
        p.drawText(ox + 3, oy + 2, osc_w - 6, 12, Qt.AlignmentFlag.AlignLeft,
                   f"PWM {self._duty:.0f}%")
        p.drawText(ox + 3, oy + osc_h - 12, osc_w - 6, 10, Qt.AlignmentFlag.AlignRight,
                   f"I ~ {amp * 1.5:.2f}A")

        p.setPen(QPen(QColor(_COL_VLOAD)))
        p.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
        p.drawText(0, h - 14, w, 12, Qt.AlignmentFlag.AlignHCenter,
                   f"VARIABLE LOAD — {self._duty:.0f}% duty")

    # ══════════════════════════════════════════════════════════
    #  MODE : SHORT TO GND — output pulled to 0 V
    # ══════════════════════════════════════════════════════════
    def _draw_short_gnd(self, p, w, h):
        self._bg(p, w, h, _COL_SHORTGND)
        bx, hx, lx, yt, yb = self._circuit_layout(w, h)
        lbl = "MOTOR" if self._target == "MOTOR" else "PUMP"
        sc = QColor(_COL_SHORTGND)

        # top wire
        self._draw_wire(p, bx + 24, yt, hx - 30, yt, _COL_SHORTGND, 2.0, glow=True)
        self._draw_wire(p, hx + 30, yt, lx - 24, yt, _COL_SHORTGND, 2.0, glow=True)
        self._draw_wire(p, bx + 24, yb, hx - 30, yb, "#333333", 1.2)
        self._draw_wire(p, hx + 30, yb, lx - 24, yb, "#333333", 1.2)

        # components
        self._draw_box(p, bx, (yt + yb) // 2, 48, 40, "BATT", _COL_SHORTGND, True, "12V")
        self._draw_box(p, hx, (yt + yb) // 2, 58, 42, "H-BRIDGE", _COL_SHORTGND, True, "L298N")
        self._draw_box(p, lx, (yt + yb) // 2, 48, 40, lbl, "#555555", False)

        # electrons flowing LEFT
        self._draw_particles(p, lx - 24, bx + 24, yt, _COL_SHORTGND, 0.0, n=8, speed=0.22, size=3)

        # Ground drain wire
        gnd_x = lx
        gnd_top = (yt + yb) // 2 + 20
        gnd_bot = h - 38
        p.setPen(QPen(sc, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(gnd_x, gnd_top, gnd_x, gnd_bot)

        # Ground symbol
        for gi, gbar_w in enumerate([18, 12, 6]):
            gy2 = gnd_bot + 3 + gi * 6
            bar_a = int(200 - gi * 50)
            p.setPen(QPen(QColor(sc.red(), sc.green(), sc.blue(), bar_a), 2.0))
            p.drawLine(gnd_x - gbar_w, gy2, gnd_x + gbar_w, gy2)

        p.setFont(QFont(_FONT_HMI, 6, QFont.Weight.Bold))
        p.setPen(QPen(sc))
        p.drawText(gnd_x - 14, gnd_bot + 24, 28, 10, Qt.AlignmentFlag.AlignHCenter, "GND")

        p.setPen(QPen(QColor(_COL_SHORTGND)))
        p.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
        p.drawText(0, h - 14, w, 12, Qt.AlignmentFlag.AlignHCenter,
                   "SIGNAL PULL-DOWN — Output 0V")


# ══════════════════════════════════════════════════════════════
#  HELPER — bouton style KPIT partagé
# ══════════════════════════════════════════════════════════════
def _kpit_btn(text, accent, h=24):
    c = QColor(accent)
    btn = QPushButton(text)
    btn.setFixedHeight(h)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
    tc = W_TEXT
    btn.setStyleSheet(f"""
        QPushButton {{
            background: {QColor(c.red(),c.green(),c.blue(),25).name()};
            color: {tc};
            border: 1px solid {_BORDER};
            border-left: 3px solid {accent};
            border-radius: 3px;
            padding: 0 8px;
        }}
        QPushButton:hover {{
            background: {QColor(c.red(),c.green(),c.blue(),55).name()};
            border: 1px solid {accent};
            border-left: 3px solid {accent};
            color: {tc};
        }}
        QPushButton:pressed {{
            background: {QColor(c.red(),c.green(),c.blue(),80).name()};
            border: 1px solid {accent};
            border-left: 3px solid {accent};
        }}
    """)
    return btn


# ══════════════════════════════════════════════════════════════
#  1) DIALOGUE ÉDITEUR D'ÉTAPE
# ══════════════════════════════════════════════════════════════
class _StepEditorDialog(QDialog):
    def __init__(self, step=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Fault Step")
        self.setMinimumWidth(320)
        self._step = step or {"mode": "NORMAL", "duty": 0, "duration_s": 5.0, "target": "PUMP"}
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        self.setStyleSheet(f"""
            QWidget {{ background: {_PANEL}; color: {_TEXT}; font-family: '{_FONT_HMI}'; }}
            QComboBox, QSpinBox, QDoubleSpinBox {{
                background: {_PANEL2}; color: {_TEXT};
                border: 1px solid {_BORDER}; border-radius: 3px; padding: 1px 4px;
            }}
            QLabel {{ background: transparent; }}
        """)
        t = QLabel("FAULT STEP EDITOR")
        t.setFont(QFont(_FONT_HMI, 9, QFont.Weight.Bold))
        t.setStyleSheet(f"color: {_COL_SEQ}; letter-spacing: 2px;")
        lay.addWidget(t)

        def row(lbl, w):
            r = QHBoxLayout()
            l = QLabel(lbl); l.setFont(QFont(_FONT_HMI, 7))
            r.addWidget(l, 2); r.addWidget(w, 3)
            lay.addLayout(r)

        self._combo_mode = QComboBox()
        for m in ["NORMAL","OPEN LOAD","SHORT TO VCC","SHORT TO GND","VARIABLE LOAD"]:
            self._combo_mode.addItem(_MODE_DISPLAY_LABELS.get(m, m), m)
        self._combo_mode.setCurrentText(_MODE_DISPLAY_LABELS.get(self._step["mode"], self._step["mode"]))
        self._combo_mode.currentIndexChanged.connect(lambda _: self._on_mode(self._combo_mode.currentData()))
        row("Mode:", self._combo_mode)

        self._spin_duty = QSpinBox()
        self._spin_duty.setRange(0, 100)
        self._spin_duty.setValue(int(self._step.get("duty", 0)))
        self._duty_row = QHBoxLayout()
        dl = QLabel("Duty %:"); dl.setFont(QFont(_FONT_HMI, 7))
        self._duty_row.addWidget(dl, 2); self._duty_row.addWidget(self._spin_duty, 3)
        self._duty_container = QWidget(); self._duty_container.setLayout(self._duty_row)
        self._duty_container.setStyleSheet("background: transparent;")
        lay.addWidget(self._duty_container)
        self._on_mode(self._step["mode"])

        self._spin_dur = QDoubleSpinBox()
        self._spin_dur.setRange(0.5, 300.0); self._spin_dur.setSingleStep(0.5)
        self._spin_dur.setValue(float(self._step.get("duration_s", 5.0)))
        row("Duration (s):", self._spin_dur)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _on_mode(self, m):
        self._duty_container.setVisible(m == "VARIABLE LOAD")

    def result_step(self):
        return {
            "mode":       self._combo_mode.currentData(),  # internal key
            "duty":       self._spin_duty.value(),
            "duration_s": self._spin_dur.value(),
            "target":     "PUMP",
        }


# ══════════════════════════════════════════════════════════════
#  2) BARRE DE PROGRESSION SÉQUENCEUR
# ══════════════════════════════════════════════════════════════
class _SeqProgressBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._frac = 0.0; self._cur = -1; self._tot = 0

    def set_progress(self, frac, cur, tot):
        self._frac = frac; self._cur = cur; self._tot = tot
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setBrush(QColor(_PANEL2)); p.setPen(QPen(QColor(_KPIT), 1.0))
        p.drawRoundedRect(0, 0, w-1, h-1, 3, 3)
        if self._frac > 0:
            fw = int((w-2) * self._frac)
            col = _COL_SAVE if self._frac < 1.0 else _COL_VLOAD
            c = QColor(col)
            grad = QLinearGradient(1, 1, fw, h-1)
            grad.setColorAt(0, QColor(c.red(), c.green(), c.blue(), 180))
            grad.setColorAt(1, QColor(c.red(), c.green(), c.blue(), 100))
            p.setBrush(QBrush(grad)); p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(1, 1, fw, h-2, 2, 2)
        p.setPen(QPen(QColor(_TEXT))); p.setFont(QFont(_FONT_HMI, 6, QFont.Weight.Bold))
        step_txt = f"{self._cur+1}/{self._tot}" if self._cur >= 0 else "-/-"
        lbl = f"SEQ  {self._frac*100:.0f}%  ({step_txt})"
        p.drawText(0, 0, w, h, Qt.AlignmentFlag.AlignCenter, lbl)


# ══════════════════════════════════════════════════════════════
#  3) SÉQUENCEUR TEMPOREL (widget standalone)
# ══════════════════════════════════════════════════════════════
class FaultSequencerWidget(QWidget):
    step_activated = Signal(str, float, str)   # mode, duty, target
    sequence_done  = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._steps: list = []
        self._current_idx  = -1
        self._is_running   = False
        self._elapsed_s    = 0.0
        self._step_elapsed = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)

        # Toolbar
        tb = QHBoxLayout(); tb.setSpacing(3)
        self._btn_add  = _kpit_btn("+ Step", _COL_SAVE,     22); self._btn_add.clicked.connect(self._add_step)
        self._btn_del  = _kpit_btn("Del",    _COL_OPENLOAD,  22); self._btn_del.clicked.connect(self._del_step)
        self._btn_up   = _kpit_btn("↑",      _COL_VLOAD,     22); self._btn_up.clicked.connect(self._move_up)
        self._btn_dn   = _kpit_btn("↓",      _COL_VLOAD,     22); self._btn_dn.clicked.connect(self._move_down)
        self._btn_edit = _kpit_btn("Edit",   _COL_SEQ,       22); self._btn_edit.clicked.connect(self._edit_step)
        for b in (self._btn_add, self._btn_del, self._btn_up, self._btn_dn, self._btn_edit):
            tb.addWidget(b)
        tb.addStretch()
        lay.addLayout(tb)

        # Table
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["#", "MODE", "D%", "s"])
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in (0, 2, 3):
            self._table.setColumnWidth(col, 32 if col in (0,2,3) else 38)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setFont(QFont(_FONT_HMI, 6))
        self._table.setMinimumHeight(100)
        self._table.setMaximumHeight(150)
        self._table.setStyleSheet(f"""
            QTableWidget {{
                background: {_PANEL}; border: 1px solid {_BORDER};
                gridline-color: rgba(141,198,63,0.25); color: {_TEXT};
            }}
            QHeaderView::section {{
                background: {_TITLEBAR}; color: {_COL_SAVE};
                font-size: 6pt; font-weight: bold;
                border: 1px solid rgba(141,198,63,0.3); padding: 1px;
            }}
            QTableWidget::item:selected {{ background: rgba(141,198,63,0.25); }}
        """)
        self._table.itemDoubleClicked.connect(self._edit_step)
        lay.addWidget(self._table)

        # Progress
        self._prog = _SeqProgressBar()
        self._prog.setFixedHeight(16)
        lay.addWidget(self._prog)

        # Run controls
        run = QHBoxLayout(); run.setSpacing(3)
        self._btn_run  = _kpit_btn("▶ RUN",  _COL_SAVE,     22); self._btn_run.clicked.connect(self._run)
        self._btn_paus = _kpit_btn("⏸",      _COL_SEQ,      22); self._btn_paus.clicked.connect(self._pause)
        self._btn_stop = _kpit_btn("■ STOP", _COL_SHORTVCC, 22); self._btn_stop.clicked.connect(self._stop_seq)
        self._lbl_seq  = QLabel("IDLE")
        self._lbl_seq.setFont(QFont(_FONT_HMI, 6, QFont.Weight.Bold))
        self._lbl_seq.setStyleSheet(f"color: {_TEXT_DIM}; background: transparent;")
        self._lbl_t = QLabel("0.0 / 0.0 s")
        self._lbl_t.setFont(QFont(_FONT_HMI, 6))
        self._lbl_t.setStyleSheet(f"color: {_TEXT_DIM}; background: transparent;")
        for b in (self._btn_run, self._btn_paus, self._btn_stop):
            run.addWidget(b)
        run.addSpacing(4); run.addWidget(self._lbl_seq); run.addStretch(); run.addWidget(self._lbl_t)
        lay.addLayout(run)

    # ── CRUD ──────────────────────────────────────────────────
    def _add_step(self):
        dlg = _StepEditorDialog(parent=self)
        if dlg.exec(): self._steps.append(dlg.result_step()); self._refresh_table()

    def _del_step(self):
        r = self._table.currentRow()
        if 0 <= r < len(self._steps): self._steps.pop(r); self._refresh_table()

    def _move_up(self):
        r = self._table.currentRow()
        if r > 0:
            self._steps[r], self._steps[r-1] = self._steps[r-1], self._steps[r]
            self._refresh_table(); self._table.selectRow(r-1)

    def _move_down(self):
        r = self._table.currentRow()
        if 0 <= r < len(self._steps)-1:
            self._steps[r], self._steps[r+1] = self._steps[r+1], self._steps[r]
            self._refresh_table(); self._table.selectRow(r+1)

    def _edit_step(self, *_):
        r = self._table.currentRow()
        if 0 <= r < len(self._steps):
            dlg = _StepEditorDialog(step=dict(self._steps[r]), parent=self)
            if dlg.exec(): self._steps[r] = dlg.result_step(); self._refresh_table()

    _COL_MAP = {
        "NORMAL": _COL_NORMAL, "OPEN LOAD": _COL_OPENLOAD,
        "SHORT TO VCC": _COL_SHORTVCC, "SHORT TO GND": _COL_SHORTGND,
        "VARIABLE LOAD": _COL_VLOAD,
    }

    def _refresh_table(self):
        self._table.setRowCount(len(self._steps))
        for i, s in enumerate(self._steps):
            acc = self._COL_MAP.get(s["mode"], _TEXT)
            vals = [str(i+1), _MODE_DISPLAY_LABELS.get(s["mode"], s["mode"]),
                    str(s.get("duty",0)) if s["mode"]=="VARIABLE LOAD" else "--",
                    f"{s.get('duration_s',5.0):.1f}"]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setForeground(QColor(acc) if j == 1 else QColor(_TEXT))
                if i == self._current_idx:
                    it.setBackground(QColor(141, 198, 63, 45))
                self._table.setItem(i, j, it)
        total = sum(s.get("duration_s", 5.0) for s in self._steps)
        self._prog.set_progress(0.0, -1, len(self._steps))
        self._lbl_t.setText(f"0.0 / {total:.1f} s")

    def load_steps(self, steps: list):
        self._steps = [dict(s) for s in steps]; self._refresh_table()

    def get_steps(self) -> list:
        return [dict(s) for s in self._steps]

    # ── Run engine ────────────────────────────────────────────
    def _total(self): return sum(s.get("duration_s", 5.0) for s in self._steps)

    def _run(self):
        if not self._steps: return
        if not self._is_running:
            self._current_idx = 0; self._elapsed_s = 0.0; self._step_elapsed = 0.0
            self._is_running = True; self._timer.start(); self._activate()

    def _pause(self):
        if self._is_running:
            self._is_running = False; self._timer.stop()
            self._lbl_seq.setText("PAUSED"); self._lbl_seq.setStyleSheet(f"color: {_COL_SEQ}; font-weight: bold; background: transparent;")
        elif self._current_idx >= 0:
            self._is_running = True; self._timer.start()
            self._lbl_seq.setText(f"RUNNING {self._current_idx+1}/{len(self._steps)}")
            self._lbl_seq.setStyleSheet(f"color: {_COL_SAVE}; font-weight: bold; background: transparent;")

    def _stop_seq(self):
        self._is_running = False; self._timer.stop()
        self._current_idx = -1; self._elapsed_s = 0.0; self._step_elapsed = 0.0
        self._prog.set_progress(0.0, -1, len(self._steps))
        self._refresh_table()
        self._lbl_seq.setText("STOPPED"); self._lbl_seq.setStyleSheet(f"color: {_COL_SHORTVCC}; font-weight: bold; background: transparent;")
        self._lbl_t.setText(f"0.0 / {self._total():.1f} s")
        self.step_activated.emit("NORMAL", 0.0, "PUMP")

    def _activate(self):
        if not (0 <= self._current_idx < len(self._steps)): return
        s = self._steps[self._current_idx]
        self.step_activated.emit(s["mode"], float(s.get("duty", 0)), s.get("target","PUMP"))
        self._lbl_seq.setText(f"STEP {self._current_idx+1}/{len(self._steps)}")
        self._lbl_seq.setStyleSheet(f"color: {_COL_SAVE}; font-weight: bold; background: transparent;")
        self._refresh_table()

    def _tick(self):
        if not self._is_running or self._current_idx < 0: return
        self._step_elapsed += 0.1; self._elapsed_s += 0.1
        total = self._total()
        self._prog.set_progress(min(self._elapsed_s/total, 1.0) if total else 0, self._current_idx, len(self._steps))
        self._lbl_t.setText(f"{self._elapsed_s:.1f} / {total:.1f} s")
        if self._step_elapsed >= self._steps[self._current_idx].get("duration_s", 5.0):
            self._step_elapsed = 0.0; self._current_idx += 1
            if self._current_idx >= len(self._steps):
                self._is_running = False; self._timer.stop(); self._current_idx = -1
                self._prog.set_progress(1.0, -1, len(self._steps))
                self._lbl_seq.setText("DONE ✓"); self._lbl_seq.setStyleSheet(f"color: {_COL_SAVE}; font-weight: bold; background: transparent;")
                self.sequence_done.emit(); self.step_activated.emit("NORMAL", 0.0, "PUMP")
            else:
                self._activate()


# ══════════════════════════════════════════════════════════════
#  4) BIBLIOTHÈQUE DE SCÉNARIOS
# ══════════════════════════════════════════════════════════════
_DEFAULT_PROFILES = {
    "Nominal":    {"color": _COL_NORMAL,   "steps": [{"mode":"NORMAL","duty":0,"duration_s":10,"target":"PUMP"}]},
    "Soak Test":  {"color": _COL_VLOAD,    "steps": [
        {"mode":"VARIABLE LOAD","duty":25, "duration_s":5, "target":"PUMP"},
        {"mode":"VARIABLE LOAD","duty":50, "duration_s":5, "target":"PUMP"},
        {"mode":"VARIABLE LOAD","duty":75, "duration_s":5, "target":"PUMP"},
        {"mode":"VARIABLE LOAD","duty":100,"duration_s":10,"target":"PUMP"},
        {"mode":"NORMAL",       "duty":0,  "duration_s":5, "target":"PUMP"},
    ]},
    "Fault Burst":{"color": _COL_SHORTVCC, "steps": [
        {"mode":"NORMAL",      "duty":0,"duration_s":3,"target":"PUMP"},
        {"mode":"OPEN LOAD",   "duty":0,"duration_s":2,"target":"PUMP"},
        {"mode":"NORMAL",      "duty":0,"duration_s":2,"target":"PUMP"},
        {"mode":"SHORT TO GND","duty":0,"duration_s":2,"target":"PUMP"},
        {"mode":"NORMAL",      "duty":0,"duration_s":3,"target":"PUMP"},
    ]},
}


class ScenarioLibraryWidget(QWidget):
    scenario_loaded = Signal(str, list)
    SAVE_DIR = os.path.join(os.path.expanduser("~"), ".fault_scenarios")

    def __init__(self, parent=None):
        super().__init__(parent)
        os.makedirs(self.SAVE_DIR, exist_ok=True)
        self._build()
        self._refresh_list()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)

        # List
        self._list = QListWidget()
        self._list.setMaximumHeight(90)
        self._list.setFont(QFont(_FONT_HMI, 6))
        self._list.setStyleSheet(f"""
            QListWidget {{ background: {_PANEL}; border: 1px solid {_BORDER}; color: {_TEXT}; }}
            QListWidget::item:selected {{ background: rgba(141,198,63,0.25); }}
        """)
        self._list.itemSelectionChanged.connect(self._on_select)
        lay.addWidget(self._list)

        # Description
        self._lbl_desc = QLabel("Select a scenario…")
        self._lbl_desc.setWordWrap(True)
        self._lbl_desc.setFont(QFont(_FONT_HMI, 6))
        self._lbl_desc.setStyleSheet(f"color: {_TEXT_DIM}; border: 1px solid rgba(141,198,63,0.3); padding: 2px; background: transparent;")
        self._lbl_desc.setMaximumHeight(32)
        lay.addWidget(self._lbl_desc)

        # Load / Delete
        row1 = QHBoxLayout(); row1.setSpacing(3)
        self._btn_load = _kpit_btn("Load",   _COL_SAVE,     22); self._btn_load.clicked.connect(self._load)
        self._btn_del  = _kpit_btn("Delete", _COL_SHORTVCC, 22); self._btn_del.clicked.connect(self._delete)
        row1.addWidget(self._btn_load); row1.addWidget(self._btn_del); row1.addStretch()
        lay.addLayout(row1)

        # Save
        row2 = QHBoxLayout(); row2.setSpacing(3)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Name…")
        self._name_edit.setFixedHeight(22)
        self._name_edit.setFont(QFont(_FONT_HMI, 7))
        self._name_edit.setStyleSheet(f"""
            QLineEdit {{ background: {_PANEL2}; color: {_TEXT};
                border: 1px solid {_BORDER}; border-radius: 3px; padding: 0 6px; }}
        """)
        self._btn_save = _kpit_btn("Save", _COL_PROF, 22)
        row2.addWidget(self._name_edit, 1); row2.addWidget(self._btn_save)
        lay.addLayout(row2)

        # Quick profiles
        lbl_p = QLabel("QUICK PROFILES")
        lbl_p.setFont(QFont(_FONT_HMI, 6, QFont.Weight.Bold))
        lbl_p.setStyleSheet(f"color: {_COL_PROF}; letter-spacing: 1px; background: transparent;")
        lay.addWidget(lbl_p)
        for name, data in _DEFAULT_PROFILES.items():
            btn = _kpit_btn(f"[{name}]", data["color"], 22)
            btn.clicked.connect(lambda _, n=name, d=data: self.scenario_loaded.emit(n, d["steps"]))
            lay.addWidget(btn)

    def _scenario_path(self, name):
        safe = "".join(c for c in name if c.isalnum() or c in (' ','_','-')).rstrip()
        return os.path.join(self.SAVE_DIR, f"{safe}.json")

    def _refresh_list(self):
        self._list.clear()
        try:
            files = sorted(f for f in os.listdir(self.SAVE_DIR) if f.endswith(".json"))
        except OSError:
            return
        for f in files:
            name = f[:-5]
            try:
                with open(os.path.join(self.SAVE_DIR, f)) as fp:
                    d = json.load(fp)
                n = len(d.get("steps", [])); t = sum(s.get("duration_s",5) for s in d.get("steps",[]))
                lbl = f"{name}  ({n}steps  {t:.0f}s)"
            except Exception:
                lbl = name
            it = QListWidgetItem(lbl); it.setData(Qt.ItemDataRole.UserRole, name)
            self._list.addItem(it)

    def _on_select(self):
        items = self._list.selectedItems()
        if not items: return
        name = items[0].data(Qt.ItemDataRole.UserRole)
        try:
            with open(self._scenario_path(name)) as f:
                d = json.load(f)
            self._lbl_desc.setText(d.get("description","") + "  " + d.get("saved_at",""))
        except Exception:
            self._lbl_desc.setText("Cannot read.")

    def _load(self):
        items = self._list.selectedItems()
        if not items: return
        name = items[0].data(Qt.ItemDataRole.UserRole)
        try:
            with open(self._scenario_path(name)) as f:
                d = json.load(f)
            self.scenario_loaded.emit(name, d.get("steps", []))
        except Exception as e:
            _light_msg(self, "Load Error", str(e), kind="warning")

    def _delete(self):
        items = self._list.selectedItems()
        if not items: return
        name = items[0].data(Qt.ItemDataRole.UserRole)
        if _ask_light(self, "Delete", f"Delete '{name}'?"):
            try: os.remove(self._scenario_path(name))
            except OSError: pass
            self._refresh_list()

    def save_steps(self, steps: list, name: str = ""):
        if not name: name = self._name_edit.text().strip()
        if not name:
            _light_msg(self, "Name required", "Enter a scenario name.", kind="warning"); return
        data = {"name": name, "description": name,
                "saved_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "steps": steps}
        with open(self._scenario_path(name), "w") as f:
            json.dump(data, f, indent=2)
        self._refresh_list(); self._name_edit.clear()


# ══════════════════════════════════════════════════════════════
#  5) PROFILS CONFIGURABLES
# ══════════════════════════════════════════════════════════════
class FaultProfileWidget(QWidget):
    profile_changed = Signal(dict)
    DEFAULT = {"pump_max_a":1.3,"motor_max_a":0.9,"overcurrent_action":"Alert",
               "fault_debounce_ms":200,"auto_restore":True,"auto_restore_delay":5.0,
               "log_to_file":False,"log_path":"~/fault_log.csv"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._profile = dict(self.DEFAULT)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        self.setStyleSheet(f"""
            QDoubleSpinBox, QSpinBox, QComboBox {{
                background: {_PANEL2}; color: {_TEXT};
                border: 1px solid {_BORDER}; border-radius: 3px; padding: 2px 6px;
                font-size: 9pt;
            }}
            QCheckBox {{ color: {_TEXT}; font-size: 9pt; background: transparent; }}
            QLineEdit {{ background: {_PANEL2}; color: {_TEXT}; border: 1px solid {_BORDER};
                border-radius: 3px; padding: 0 6px; font-size: 9pt; }}
        """)

        def row(lbl_txt, widget):
            r = QHBoxLayout(); r.setSpacing(6)
            l = QLabel(lbl_txt); l.setFont(QFont(_FONT_HMI, 9)); l.setStyleSheet(f"color:{_TEXT};background:transparent;")
            r.addWidget(l, 2); r.addWidget(widget, 3); lay.addLayout(r)

        self._spin_pump  = QDoubleSpinBox(); self._spin_pump.setRange(0.1,10); self._spin_pump.setSingleStep(0.1)
        self._spin_pump.setSuffix(" A"); self._spin_pump.setValue(self._profile["pump_max_a"])
        row("Pump max A:", self._spin_pump)

        self._spin_motor = QDoubleSpinBox(); self._spin_motor.setRange(0.1,10); self._spin_motor.setSingleStep(0.1)
        self._spin_motor.setSuffix(" A"); self._spin_motor.setValue(self._profile["motor_max_a"])
        row("Motor max A:", self._spin_motor)

        self._combo_action = QComboBox()
        self._combo_action.addItems(["Alert","Auto-Restore","Stop Pump","Log Only"])
        self._combo_action.setCurrentText(self._profile["overcurrent_action"])
        row("On overcurrent:", self._combo_action)

        self._spin_deb = QSpinBox(); self._spin_deb.setRange(0,5000); self._spin_deb.setSingleStep(50)
        self._spin_deb.setSuffix(" ms"); self._spin_deb.setValue(self._profile["fault_debounce_ms"])
        row("Debounce:", self._spin_deb)

        self._chk_restore = QCheckBox("Auto-restore after fault")
        self._chk_restore.setChecked(self._profile["auto_restore"]); lay.addWidget(self._chk_restore)

        self._spin_delay = QDoubleSpinBox(); self._spin_delay.setRange(0.5,60); self._spin_delay.setSingleStep(0.5)
        self._spin_delay.setSuffix(" s"); self._spin_delay.setValue(self._profile["auto_restore_delay"])
        row("Restore delay:", self._spin_delay)

        self._chk_log = QCheckBox("Log faults to CSV"); self._chk_log.setChecked(self._profile["log_to_file"])
        lay.addWidget(self._chk_log)
        self._edit_log = QLineEdit(self._profile["log_path"]); self._edit_log.setFixedHeight(26)
        lay.addWidget(self._edit_log)

        btn_row = QHBoxLayout(); btn_row.setSpacing(3)
        b_apply  = _kpit_btn("Apply",    _COL_PROF,      28); b_apply.clicked.connect(self._apply)
        b_export = _kpit_btn("Export JSON", _COL_SEQ,    28); b_export.clicked.connect(self._export)
        b_reset  = _kpit_btn("Defaults", _COL_OPENLOAD,  28); b_reset.clicked.connect(self._reset)
        for b in (b_apply, b_export, b_reset): btn_row.addWidget(b)
        lay.addLayout(btn_row)

    def _collect(self):
        return {"pump_max_a": self._spin_pump.value(), "motor_max_a": self._spin_motor.value(),
                "overcurrent_action": self._combo_action.currentText(),
                "fault_debounce_ms": self._spin_deb.value(),
                "auto_restore": self._chk_restore.isChecked(),
                "auto_restore_delay": self._spin_delay.value(),
                "log_to_file": self._chk_log.isChecked(), "log_path": self._edit_log.text().strip()}

    def _apply(self):
        self._profile = self._collect(); self.profile_changed.emit(self._profile)

    def _export(self):
        p = self._collect(); path = os.path.expanduser("~/fault_profile.json")
        with open(path, "w") as f: json.dump(p, f, indent=2)
        _light_msg(self, "Export", f"Saved to:\n{path}")

    def _reset(self):
        self._profile = dict(self.DEFAULT)
        self._spin_pump.setValue(self._profile["pump_max_a"])
        self._spin_motor.setValue(self._profile["motor_max_a"])
        self._combo_action.setCurrentText(self._profile["overcurrent_action"])
        self._spin_deb.setValue(self._profile["fault_debounce_ms"])
        self._chk_restore.setChecked(self._profile["auto_restore"])
        self._spin_delay.setValue(self._profile["auto_restore_delay"])
        self._chk_log.setChecked(self._profile["log_to_file"])
        self._edit_log.setText(self._profile["log_path"])

    def get_profile(self): return dict(self._profile)


# ══════════════════════════════════════════════════════════════
#  FAULT INJECTION PANEL — ControlDesk / Scalexio FIU style
# ══════════════════════════════════════════════════════════════
class FaultInjectionPanel(QWidget):
    def __init__(self, pump_data_signal=None, rte_getter=None, sim_getter=None, parent=None):
        super().__init__(parent)
        self._pump_signal    = pump_data_signal
        self._rte_getter     = rte_getter
        self._sim_getter     = sim_getter
        self._pump_state     = "STOP"
        self._pump_current   = 0.0
        self._motor_current  = 0.0
        self._fi_target      = "PUMP"
        self._fi_mode        = "NORMAL"
        self._vload_duty     = 50.0
        self._active_btn_key = ("RESTORE NORMAL", "NORMAL", 0)
        self._active_profile = FaultProfileWidget.DEFAULT.copy()

        self._apply_style()
        self._build()

        # Connect new widgets (built inside _build_col_right)
        self._sequencer.step_activated.connect(self._on_seq_step)
        self._sequencer.sequence_done.connect(lambda: self._show_alert("Sequence complete ✓", _COL_SAVE))
        self._library.scenario_loaded.connect(self._on_scenario_loaded)
        self._library._btn_save.clicked.connect(self._save_current_scenario)
        self._profile_widget.profile_changed.connect(self._on_profile_changed)

        if self._pump_signal is not None:
            try:
                self._pump_signal.data_received.connect(self._on_pump_data)
            except Exception:
                pass

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(500)
        self._poll_timer.timeout.connect(self._poll_motor)
        self._poll_timer.start()

    def _apply_style(self):
        self.setStyleSheet(f"""
            QWidget {{
                background: {_BG};
                color: {_TEXT};
                font-family: '{_FONT_LABEL}';
                font-size: 9pt;
            }}
            QLabel {{ background: transparent; }}
            QScrollBar:vertical {{
                background: {_PANEL2};
                width: 5px;
                border-radius: 2px;
            }}
            QScrollBar::handle:vertical {{
                background: transparent;
                border-radius: 2px;
                min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QPushButton {{ color: {_TEXT}; }}
        """)

    def _section(self, title, accent, tag=""):
        frame = QWidget(); frame.setObjectName("sec")
        frame.setStyleSheet(f"""
            QWidget#sec {{
                background: {_PANEL};
                border: 2px solid {_BORDER};
                border-top: 2px solid {accent};
                border-radius: 3px;
            }}
        """)
        vl = QVBoxLayout(frame); vl.setContentsMargins(0, 0, 0, 0); vl.setSpacing(0)
        hdr = QWidget(); hdr.setObjectName("hdr"); hdr.setFixedHeight(22)
        hdr.setStyleSheet(f"""
            QWidget#hdr {{
                background: {_TITLEBAR};
                border-radius: 2px 2px 0 0;
            }}
        """)
        hlay = QHBoxLayout(hdr); hlay.setContentsMargins(8, 0, 8, 0)
        if tag:
            tag_lbl = QLabel(tag)
            tag_lbl.setFont(QFont(_FONT_HMI, 6, QFont.Weight.Bold))
            tag_lbl.setStyleSheet(f"color: {accent}; background: transparent;")
            hlay.addWidget(tag_lbl); hlay.addSpacing(3)
        lbl = QLabel(title)
        lbl.setFont(QFont(_FONT_LABEL, 7, QFont.Weight.Bold))
        lbl.setStyleSheet(f"color: {_TEXT_HDR}; letter-spacing: 2px; background: transparent;")
        hlay.addWidget(lbl); hlay.addStretch()
        vl.addWidget(hdr)
        body = QWidget(); body.setStyleSheet("background: transparent;")
        bl = QVBoxLayout(body); bl.setContentsMargins(8, 6, 8, 8); bl.setSpacing(4)
        vl.addWidget(body)
        return frame, bl

    def _mode_card(self, label, mode, duty, accent):
        btn = QPushButton()
        btn.setMinimumHeight(36)
        btn.setMaximumHeight(36)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        c = QColor(accent)
        btn.setText(f"{label}")
        btn.setFont(QFont(_FONT_HMI, 8, QFont.Weight.Bold))
        
        text_color = W_TEXT
        bg_a = QColor(c.red(), c.green(), c.blue(), 30).name()
        
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {bg_a}; 
                color: {text_color};
                border: 1px solid {_BORDER};
                border-left: 3px solid {accent};
                border-radius: 3px; 
                text-align: center; 
                padding: 0 8px; 
            }}
            QPushButton:hover {{
                background: {QColor(c.red(), c.green(), c.blue(), 55).name()};
                border: 1px solid {accent};
                border-left: 3px solid {accent}; 
                color: {text_color};
            }}
            QPushButton:pressed {{ 
                background: {QColor(c.red(), c.green(), c.blue(), 70).name()}; 
                border: 1px solid {accent};
                border-left: 3px solid {accent};
            }}
        """)
        return btn

    def _mode_card_active(self, btn, accent):
        c = QColor(accent)
        text_color = W_TEXT
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {QColor(c.red(), c.green(), c.blue(), 50).name()};
                color: {text_color}; 
                border: 1px solid {accent};
                border-left: 3px solid {accent};
                border-radius: 3px; 
                text-align: center; 
                padding: 0 8px;
                font-weight: 900; 
            }}
            QPushButton:hover {{ 
                background: {QColor(c.red(), c.green(), c.blue(), 65).name()}; 
                border: 1px solid {accent};
                border-left: 3px solid {accent};
            }}
        """)

    def _pill_btn(self, text, accent, h=28):
        c = QColor(accent)
        btn = QPushButton(text)
        btn.setFixedHeight(h)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
        text_color = W_TEXT
        bg_a = QColor(c.red(), c.green(), c.blue(), 15).name()
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {bg_a}; 
                color: {text_color};
                border: 1px solid {_BORDER};
                border-radius: 3px;
                padding: 0 12px; 
            }}
            QPushButton:hover {{
                background: {QColor(c.red(), c.green(), c.blue(), 40).name()};
                border: 1px solid {accent};
                color: {text_color};
            }}
            QPushButton:pressed {{ 
                background: {QColor(c.red(), c.green(), c.blue(), 65).name()}; 
                border: 1px solid {accent};
            }}
        """)
        return btn

    def _pill_btn_active(self, btn, accent):
        c = QColor(accent)
        text_color = W_TEXT
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {QColor(c.red(), c.green(), c.blue(), 55).name()};
                color: {text_color}; 
                border: 1px solid {accent};
                border-radius: 3px;
                padding: 0 12px; 
                font-weight: 900;
            }}
            QPushButton:hover {{ 
                background: {QColor(c.red(), c.green(), c.blue(), 70).name()}; 
                border: 1px solid {accent};
            }}
        """)

    def _status_badge(self, text, color):
        lbl = QLabel(text)
        lbl.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
        c = QColor(color)
        text_color = W_TEXT
        lbl.setStyleSheet(f"""
            color: {text_color};
            background: {QColor(c.red(), c.green(), c.blue(), 25).name()};
            border: 1px solid {_BORDER};
            border-radius: 3px; padding: 2px 6px;
        """)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return lbl

    def _vsep_line(self):
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background: {_BORDER_BRIGHT}; border: none;")
        return sep

    # ── Layout principal ──────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # Title bar
        title_bar = QWidget()
        title_bar.setObjectName("tb")
        title_bar.setFixedHeight(32)
        title_bar.setStyleSheet(f"""
            QWidget#tb {{
                background: {_TITLEBAR}; border: 2px solid {_BORDER};
                border-left: 3px solid {_KPIT}; border-radius: 3px;
            }}
        """)
        tb_lay = QHBoxLayout(title_bar)
        tb_lay.setContentsMargins(10, 0, 10, 0)
        t1 = QLabel("FAULT INJECTION PANEL")
        t1.setFont(QFont(_FONT_HMI, 10, QFont.Weight.Bold))
        t1.setStyleSheet(f"color: {_KPIT}; letter-spacing: 2px; background: transparent;")
        t2 = QLabel("PUMP | HIL")
        t2.setFont(QFont(_FONT_HMI, 7))
        t2.setStyleSheet(f"color: rgba(255,255,255,0.4); letter-spacing: 2px; background: transparent;")
        self._title_mode = QLabel("NORMAL")
        self._title_mode.setFont(QFont(_FONT_HMI, 8, QFont.Weight.Bold))
        self._title_mode.setStyleSheet(f"color: {_KPIT}; letter-spacing: 2px; background: transparent;")
        tb_lay.addWidget(t1)
        tb_lay.addSpacing(12)
        tb_lay.addWidget(t2)
        tb_lay.addStretch()
        tb_lay.addWidget(self._title_mode)
        root.addWidget(title_bar)

        # Body
        body = QHBoxLayout()
        body.setSpacing(4)
        body.addWidget(self._build_col_left(), 3)
        body.addWidget(self._vsep_line())
        body.addWidget(self._build_col_center(), 3)
        body.addWidget(self._vsep_line())
        body.addWidget(self._build_col_right(), 2)
        root.addLayout(body, 1)

        # Status bar
        status_bar = QWidget()
        status_bar.setObjectName("sb")
        status_bar.setFixedHeight(20)
        status_bar.setStyleSheet(f"""
            QWidget#sb {{
                background:#000000; border-top: 2px solid {_BORDER};
                border-radius: 0 0 3px 3px;
            }}
        """)
        sb_lay = QHBoxLayout(status_bar)
        sb_lay.setContentsMargins(10, 0, 10, 0)
        sb_lay.setSpacing(15)
        self._sb_bcm  = QLabel("BCM  --")
        self._sb_sim  = QLabel("SIM  --")
        self._sb_pump = QLabel("PUMP  STOP")
        self._sb_time = QLabel("")
        for lbl in (self._sb_bcm, self._sb_sim, self._sb_pump, self._sb_time):
            lbl.setFont(QFont(_FONT_HMI, 6))
            lbl.setStyleSheet(f"color: {_TEXT_DIM}; background: transparent;")
        sb_lay.addWidget(self._sb_bcm)
        sb_lay.addWidget(self._sb_sim)
        sb_lay.addStretch()
        sb_lay.addWidget(self._sb_pump)
        sb_lay.addWidget(self._sb_time)
        root.addWidget(status_bar)

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start()
        self._tick_clock()

    # ── Left column ───────────────────────────────────────────
    def _build_col_left(self):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        # Pump commands
        s1, b1 = self._section("PUMP", _COL_NORMAL, "CMD")
        row_s = QHBoxLayout()
        self._led_pump = StatusLed(10)
        self._lbl_pump_state = QLabel("STOP")
        self._lbl_pump_state.setFont(QFont(_FONT_HMI, 14, QFont.Weight.Bold))
        self._lbl_pump_state.setStyleSheet(f"color: {_TEXT_DIM}; letter-spacing: 2px;")
        row_s.addWidget(self._led_pump)
        row_s.addSpacing(4)
        row_s.addWidget(self._lbl_pump_state)
        row_s.addStretch()
        b1.addLayout(row_s)
        row_b = QHBoxLayout()
        row_b.setSpacing(4)
        self._btn_fwd  = self._pill_btn("FWD",  _COL_NORMAL, 26)
        self._btn_bwd  = self._pill_btn("BWD",  _COL_VLOAD, 26)
        self._btn_stop = self._pill_btn("STOP", _COL_SHORTVCC, 26)
        self._btn_fwd.clicked.connect(lambda: self._pump_cmd("fwd"))
        self._btn_bwd.clicked.connect(lambda: self._pump_cmd("bwd"))
        self._btn_stop.clicked.connect(lambda: self._pump_cmd("stop"))
        for b in (self._btn_fwd, self._btn_bwd, self._btn_stop):
            row_b.addWidget(b, 1)
        b1.addLayout(row_b)
        lay.addWidget(s1)

        # Arc Gauges
        s2, b2 = self._section("MEASURES", _COL_VLOAD, "ADC")
        gauge_row = QHBoxLayout()
        gauge_row.setSpacing(4)
        self._gauge_pump  = _FaultArcGauge(1.5, "A", "PUMP")
        self._gauge_motor = _FaultArcGauge(1.0, "A", "MOTOR")
        gauge_row.addWidget(self._gauge_pump)
        gauge_row.addWidget(self._gauge_motor)
        b2.addLayout(gauge_row)
        lay.addWidget(s2)

        # Plot
        s3, b3 = self._section("CURRENT", _COL_NORMAL, "OSC")
        self._plot = DualCurrentPlot()
        b3.addWidget(self._plot)
        lay.addWidget(s3, 1)

        # Alert
        s4, b4 = self._section("ALERTS", _COL_SHORTVCC, "ALT")
        self._lbl_alert = QLabel("No alerts")
        self._lbl_alert.setFont(QFont(_FONT_HMI, 8))
        self._lbl_alert.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_alert.setStyleSheet(f"""
            color: {_TEXT_DIM}; background: transparent;
            border: 1px solid {_BORDER}; border-radius: 3px; padding: 4px;
        """)
        b4.addWidget(self._lbl_alert)
        lay.addWidget(s4)
        return w

    # ── Center column ─────────────────────────────────────────
    def _build_col_center(self):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        s0, b0 = self._section("CIRCUIT", _COL_NORMAL, "CIR")
        self._scene = FaultSceneWidget()
        b0.addWidget(self._scene)
        lay.addWidget(s0)

        s2, b2 = self._section("FAULT MODES", _COL_OPENLOAD, "FIU")
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        grid = QGridLayout(container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(3)

        self._combined_btns = {}
        row_idx = 0
        vload_rows = []
        self._danger_col = 0
        
        for label, mode, duty, color in _MODES_COMBINED:
            btn = self._mode_card(label, mode, duty, color)
            key = (label, mode, duty)
            btn.clicked.connect(lambda _, m=mode, d=duty, k=key: self._set_mode_and_duty(m, d, k))
            self._combined_btns[key] = btn
            if mode == "NORMAL":
                grid.addWidget(btn, row_idx, 0, 1, 2)
                row_idx += 1
            elif mode == "VARIABLE LOAD":
                vload_rows.append((btn, duty))
            else:
                grid.addWidget(btn, row_idx, self._danger_col)
                self._danger_col += 1
                if self._danger_col >= 2:
                    self._danger_col = 0
                    row_idx += 1

        if hasattr(self, '_danger_col') and self._danger_col != 0:
            row_idx += 1

        sep_lbl = QLabel("RESISTIVE LOAD VARIATION")
        sep_lbl.setFont(QFont(_FONT_HMI, 6, QFont.Weight.Bold))
        sep_lbl.setStyleSheet(f"color: {_COL_VLOAD}; letter-spacing: 2px; background: transparent;")
        grid.addWidget(sep_lbl, row_idx, 0, 1, 2)
        row_idx += 1
        for i, (btn, duty) in enumerate(vload_rows):
            grid.addWidget(btn, row_idx + i // 2, i % 2)

        b2.addWidget(container)
        self._lbl_mode_active = self._status_badge("MODE: NORMAL", _COL_NORMAL)
        b2.addWidget(self._lbl_mode_active)
        lay.addWidget(s2, 1)
        return w

    # ── Right column ──────────────────────────────────────────
    def _build_col_right(self):
        # Scrollable right column
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{
                background: {_PANEL2}; width: 5px; border-radius: 2px;
            }}
            QScrollBar::handle:vertical {{
                background: transparent; border-radius: 2px; min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)
        inner = QWidget(); inner.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        # ── CONNECTIONS ─────────────────────────────────────
        s1, b1 = self._section("CONNECTIONS", _COL_NORMAL, "NET")
        for conn_attr, led_attr, lbl_attr, lbl_txt in [
            ("_conn_bcm", "_led_bcm_conn", "_lbl_bcm_conn", "BCM Redis"),
            ("_conn_sim", "_led_sim_conn", "_lbl_sim_conn", "SIM TCP"),
        ]:
            row_c = QHBoxLayout()
            row_c.setSpacing(6)
            led = StatusLed(8)
            lbl = QLabel(lbl_txt)
            lbl.setFont(QFont(_FONT_HMI, 7))
            lbl.setStyleSheet(f"color: {_TEXT};")
            lbl2 = QLabel("--")
            lbl2.setFont(QFont(_FONT_HMI, 6))
            lbl2.setStyleSheet(f"color: {_TEXT_DIM};")
            setattr(self, led_attr, led)
            setattr(self, lbl_attr, lbl2)
            row_c.addWidget(led)
            row_c.addWidget(lbl)
            row_c.addStretch()
            row_c.addWidget(lbl2)
            b1.addLayout(row_c)
        lay.addWidget(s1)

        # ── SÉQUENCEUR TEMPOREL ──────────────────────────────
        s_seq, b_seq = self._section("SEQUENCER", _COL_SEQ, "SEQ")
        self._sequencer = FaultSequencerWidget()
        b_seq.addWidget(self._sequencer)
        lay.addWidget(s_seq)

        # ── BIBLIOTHÈQUE DE SCÉNARIOS ────────────────────────
        s_lib, b_lib = self._section("SCENARIOS", _COL_SAVE, "LIB")
        self._library = ScenarioLibraryWidget()
        b_lib.addWidget(self._library)
        lay.addWidget(s_lib)

        # ── PROFILS CONFIGURABLES ────────────────────────────
        s_prof, b_prof = self._section("PROFILE", _COL_PROF, "CFG")
        self._profile_widget = FaultProfileWidget()
        b_prof.addWidget(self._profile_widget)
        lay.addWidget(s_prof)

        lay.addStretch()
        scroll.setWidget(inner)

        self._refresh_mode_ui()
        return scroll

    # ── Callbacks ─────────────────────────────────────────────
    def _rte(self):
        r = self._rte_getter() if self._rte_getter else None
        return r if (r and r.is_connected()) else None

    def _sim(self):
        s = self._sim_getter() if self._sim_getter else None
        return s if (s and s.is_connected()) else None

    def _pump_cmd(self, cmd):
        rte = self._rte()
        if not rte:
            self._show_alert("BCM not connected", _COL_SHORTVCC)
            return
        if rte.set_cmd("pump_cmd", cmd):
            self._pump_state = {"fwd": "FWD", "bwd": "BWD", "stop": "STOP"}.get(cmd, "STOP")
            self._refresh_pump_state_ui()
        else:
            self._show_alert("Error", _COL_SHORTVCC)

    def _set_mode_and_duty(self, mode, duty, key):
        self._fi_mode = mode
        self._vload_duty = float(duty)
        self._active_btn_key = key
        self._scene.set_mode(mode, duty=float(duty))
        sim = self._sim()
        if sim:
            sim.send_fault(pump_fault_mode=mode, pump_fault_target="PUMP", duty_cycle=float(duty))
        self._refresh_mode_ui()

    def _on_pump_data(self, data):
        state  = data.get("state", "OFF")
        cur    = float(data.get("current", 0.0))
        fault  = data.get("fault", False)
        reason = data.get("fault_reason", "")
        self._pump_current = cur
        self._pump_state = state
        self._gauge_pump.set_value(cur, fault)
        self._plot.push(cur, self._motor_current)
        self._refresh_pump_state_ui()
        if fault:
            self._show_alert(f"FAULT: {reason or state}", _COL_SHORTVCC)
        else:
            self._clear_alert()

    def _poll_motor(self):
        rte = self._rte()
        if not rte:
            self._led_bcm_conn.set_state(False)
            self._lbl_bcm_conn.setText("--")
            self._sb_bcm.setText("BCM --")
            return
        self._led_bcm_conn.set_state(True, _COL_NORMAL)
        try:
            host = self._rte_getter().host if hasattr(self._rte_getter(), 'host') else "connected"
        except:
            host = "connected"
        self._lbl_bcm_conn.setText(host[:12])
        self._sb_bcm.setText(f"BCM {host[:10]}")

        im = rte.get_float("motor_current_a", 0.0)
        self._motor_current = im
        self._gauge_motor.set_value(im, im > 0.9)

        ip = rte.get_float("pump_current_a", 0.0)
        self._pump_current = ip
        self._gauge_pump.set_value(ip, ip > 1.3)
        self._plot.push(ip, im)

        sim = self._sim()
        if sim:
            self._led_sim_conn.set_state(True, _COL_NORMAL)
            try:
                sh = self._sim_getter().host
            except:
                sh = "connected"
            self._lbl_sim_conn.setText(sh[:12])
            self._sb_sim.setText(f"SIM {sh[:10]}")
        else:
            self._led_sim_conn.set_state(False)
            self._lbl_sim_conn.setText("--")
            self._sb_sim.setText("SIM --")

    def _tick_clock(self):
        self._sb_time.setText(time.strftime("%H:%M:%S"))

    # ── UI refresh ────────────────────────────────────────────
    def _refresh_pump_state_ui(self):
        s = self._pump_state
        n = {"FORWARD": "FWD", "BACKWARD": "BWD", "OFF": "STOP", "FAULT": "FLT"}.get(s, s)
        col = {"FWD": _COL_NORMAL, "BWD": _COL_VLOAD, "STOP": _TEXT_DIM, "FLT": _COL_SHORTVCC}.get(n, _TEXT_DIM)
        self._led_pump.set_state(n in ("FWD", "BWD"), col)
        self._lbl_pump_state.setText(n)
        self._lbl_pump_state.setStyleSheet(f"color: {col}; font-weight: bold; letter-spacing: 2px;")
        self._sb_pump.setText(f"PUMP {n}")

    def _refresh_mode_ui(self):
        m = self._fi_mode
        col, _, _ = _MODE_COLORS.get(m, (_TEXT_DIM, _TEXT_DIM, _PANEL))
        c = QColor(col)
        tag = _MODE_TAGS.get(m, "---")
        txt = f"MODE: {_MODE_DISPLAY_LABELS.get(m, m)}" + (f" {self._vload_duty:.0f}%" if m == "VARIABLE LOAD" else "")
        text_color = W_TEXT
        self._lbl_mode_active.setText(txt)
        self._lbl_mode_active.setStyleSheet(f"""
            color: {text_color}; background: {QColor(c.red(), c.green(), c.blue(), 25).name()};
            border: 1px solid {_BORDER};
            border-radius: 3px; padding: 2px 6px;
            font-size: 7pt; font-weight: bold;
        """)
        self._title_mode.setText(f"{_MODE_DISPLAY_LABELS.get(m, m)}" + (f" {self._vload_duty:.0f}%" if m == "VARIABLE LOAD" else ""))
        self._title_mode.setStyleSheet(f"color: {col}; letter-spacing: 2px; font-weight: bold;")

        for key, btn in self._combined_btns.items():
            label, mode, duty = key
            accent = next((c2 for l, m2, d, c2 in _MODES_COMBINED if (l, m2, d) == key), _COL_NORMAL)
            btn_text_color = W_TEXT
            if key == self._active_btn_key:
                self._mode_card_active(btn, accent)
            else:
                c2 = QColor(accent)
                bg2 = QColor(c2.red(), c2.green(), c2.blue(), 20).name()
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {bg2}; 
                        color: {btn_text_color};
                        border: 1px solid {_BORDER};
                        border-left: 3px solid {accent};
                        border-radius: 3px; 
                        text-align: center; 
                        padding: 0 8px;
                        font-size: 8pt; 
                        font-weight: bold;
                    }}
                    QPushButton:hover {{
                        background: {QColor(c2.red(), c2.green(), c2.blue(), 55).name()};
                        border: 1px solid {accent};
                        border-left: 3px solid {accent}; 
                        color: {btn_text_color};
                    }}
                    QPushButton:pressed {{ 
                        background: {QColor(c2.red(), c2.green(), c2.blue(), 70).name()}; 
                        border: 1px solid {accent};
                        border-left: 3px solid {accent};
                    }}
                """)

    def _show_alert(self, msg, color=_COL_SHORTVCC):
        c = QColor(color)
        text_color = W_TEXT
        self._lbl_alert.setText(f"! {msg}")
        self._lbl_alert.setStyleSheet(f"""
            color: {text_color}; background: {QColor(c.red(), c.green(), c.blue(), 20).name()};
            border: 1px solid {_BORDER};
            border-left: 3px solid {color}; border-radius: 3px;
            padding: 4px; font-weight: bold; font-size: 7pt;
        """)

    def _clear_alert(self):
        self._lbl_alert.setText("No alerts")
        self._lbl_alert.setStyleSheet(f"""
            color: {_TEXT_DIM}; background: transparent;
            border: 1px solid {_BORDER}; border-radius: 3px;
            padding: 4px; font-size: 7pt;
        """)

    # ── Public API ────────────────────────────────────────────
    def on_pump_data(self, data):
        self._on_pump_data(data)

    def on_connected_bcm(self, host):
        self._led_bcm_conn.set_state(True, _COL_NORMAL)
        self._lbl_bcm_conn.setText(host[:12])

    def on_disconnected_bcm(self):
        self._led_bcm_conn.set_state(False)
        self._lbl_bcm_conn.setText("--")

    def on_connected_sim(self, host):
        self._led_sim_conn.set_state(True, _COL_NORMAL)
        self._lbl_sim_conn.setText(host[:12])

    def on_disconnected_sim(self):
        self._led_sim_conn.set_state(False)
        self._lbl_sim_conn.setText("--")

    # ── Séquenceur : injection d'une étape vers hardware ──────
    def _on_seq_step(self, mode: str, duty: float, target: str):
        self._fi_mode    = mode
        self._vload_duty = duty
        self._scene.set_mode(mode, duty=duty)
        sim = self._sim()
        if sim:
            sim.send_fault(pump_fault_mode=mode, pump_fault_target="PUMP", duty_cycle=duty)
        # Synchronise le bouton actif dans la colonne centre
        key_match = next(
            ((l, m, d) for l, m, d, _ in _MODES_COMBINED
             if m == mode and (m != "VARIABLE LOAD" or d == int(duty))),
            ("RESTORE NORMAL", "NORMAL", 0)
        )
        self._active_btn_key = key_match
        self._refresh_mode_ui()

    # ── Bibliothèque : charge un scénario dans le séquenceur ──
    def _on_scenario_loaded(self, name: str, steps: list):
        self._sequencer.load_steps(steps)

    # ── Sauvegarde les steps courants du séquenceur ───────────
    def _save_current_scenario(self):
        steps = self._sequencer.get_steps()
        name  = self._library._name_edit.text().strip()
        self._library.save_steps(steps, name=name)

    # ── Profil : applique les nouveaux seuils ─────────────────
    def _on_profile_changed(self, profile: dict):
        self._active_profile = profile
        self._gauge_pump._max  = profile.get("pump_max_a",  1.5)
        self._gauge_motor._max = profile.get("motor_max_a", 1.0)

    # ── Override : applique les règles du profil actif ────────
    def _on_pump_data(self, data):
        state  = data.get("state", "OFF")
        cur    = float(data.get("current", 0.0))
        fault  = data.get("fault", False)
        reason = data.get("fault_reason", "")
        self._pump_current = cur
        self._pump_state   = state
        self._gauge_pump.set_value(cur, fault)
        self._plot.push(cur, self._motor_current)
        self._refresh_pump_state_ui()

        prof   = self._active_profile
        max_a  = prof.get("pump_max_a", 1.3)
        action = prof.get("overcurrent_action", "Alert")

        if cur > max_a:
            if action == "Stop Pump":
                self._pump_cmd("stop")
            elif action == "Auto-Restore":
                self._set_mode_and_duty("NORMAL", 0, ("RESTORE NORMAL", "NORMAL", 0))
            if prof.get("log_to_file", False):
                self._log_fault_event(cur, "OVERCURRENT")

        if fault:
            self._show_alert(f"FAULT: {reason or state}", _COL_SHORTVCC)
        elif cur > max_a:
            self._show_alert(f"OVERCURRENT  {cur:.3f} A > {max_a:.1f} A", _COL_SHORTVCC)
        else:
            self._clear_alert()

    # ── Log CSV ───────────────────────────────────────────────
    def _log_fault_event(self, current: float, reason: str):
        try:
            import csv
            path = os.path.expanduser(self._active_profile.get("log_path", "~/fault_log.csv"))
            write_header = not os.path.exists(path)
            with open(path, "a", newline="") as f:
                w = csv.writer(f)
                if write_header:
                    w.writerow(["timestamp", "reason", "current_a", "mode", "target"])
                w.writerow([datetime.datetime.now().isoformat(), reason,
                            f"{current:.4f}", self._fi_mode, self._fi_target])
        except Exception:
            pass