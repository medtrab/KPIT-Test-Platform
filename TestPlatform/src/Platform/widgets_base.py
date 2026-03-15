"""
WipeWash — Widgets de base
StatusLed, PanelHeader, InstrumentPanel, NumericDisplay, LinearBar,
et helpers (_font, _lbl, _hsep, _vsep, _cd_btn).
"""

import math

from PyQt6.QtWidgets import QWidget, QFrame, QLabel, QPushButton, QHBoxLayout, QVBoxLayout
from PyQt6.QtCore    import Qt
from PyQt6.QtGui     import (
    QPainter, QColor, QPen, QBrush, QFont,
    QLinearGradient, QRadialGradient,
)

from constants import (
    FONT_UI, FONT_MONO,
    W_PANEL, W_PANEL2, W_PANEL3,
    W_BORDER, W_BORDER2,
    W_TEXT, W_TEXT_DIM, W_TEXT_HDR,
    W_DOCK_HDR,
    A_TEAL, A_GREEN, A_RED, A_ORANGE,
)


# ═══════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════
def _font(size: int = 11, bold: bool = False, mono: bool = False) -> QFont:
    f = QFont(FONT_MONO if mono else FONT_UI, size)
    if bold:
        f.setWeight(QFont.Weight.Bold)
    return f


def _lbl(text: str, size: int = 11, bold: bool = False,
         color: str = W_TEXT, mono: bool = False) -> QLabel:
    l = QLabel(text)
    l.setFont(_font(size, bold, mono))
    l.setStyleSheet(f"color:{color};background:transparent;")
    return l


def _hsep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet(f"background:{W_BORDER};border:none;max-height:1px;")
    return f


def _vsep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.VLine)
    f.setStyleSheet(f"background:{W_BORDER};border:none;max-width:1px;")
    return f


def _cd_btn(text: str, color: str = A_TEAL,
            h: int = 30, w: int | None = None) -> QPushButton:
    b = QPushButton(text)
    b.setFont(QFont(FONT_UI, 11, QFont.Weight.Bold))
    b.setFixedHeight(h)
    if w:
        b.setFixedWidth(w)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    c = QColor(color)
    b.setStyleSheet(f"""
        QPushButton {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 {c.lighter(115).name()}, stop:1 {color});
            color: #FFFFFF;
            border: 1px solid {c.darker(120).name()};
            border-radius: 3px;
            padding: 2px 14px;
        }}
        QPushButton:hover {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 {c.lighter(130).name()}, stop:1 {c.lighter(110).name()});
        }}
        QPushButton:pressed  {{ background: {c.darker(115).name()}; }}
        QPushButton:disabled {{
            background: {W_PANEL3}; color: {W_TEXT_DIM};
            border-color: {W_BORDER};
        }}
    """)
    return b


# ═══════════════════════════════════════════════════════════
#  LED
# ═══════════════════════════════════════════════════════════
class StatusLed(QWidget):
    def __init__(self, size: int = 13, parent=None) -> None:
        super().__init__(parent)
        self._on    = False
        self._color = QColor(A_RED)
        self.setFixedSize(size, size)

    def set_state(self, on: bool, color: str | None = None) -> None:
        self._on    = on
        self._color = QColor(color) if color else QColor(A_GREEN if on else A_RED)
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.width()
        if self._on:
            h = QColor(self._color)
            h.setAlpha(50)
            p.setBrush(QBrush(h))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(0, 0, s, s)
            g = QRadialGradient(s * 0.35, s * 0.35, s * 0.5)
            g.setColorAt(0,   self._color.lighter(170))
            g.setColorAt(0.6, self._color)
            g.setColorAt(1,   self._color.darker(160))
            p.setBrush(QBrush(g))
            p.setPen(QPen(self._color.darker(130), 0.8))
            p.drawEllipse(1, 1, s - 2, s - 2)
        else:
            p.setBrush(QBrush(QColor(W_PANEL3)))
            p.setPen(QPen(QColor(W_BORDER), 0.8))
            p.drawEllipse(1, 1, s - 2, s - 2)


# ═══════════════════════════════════════════════════════════
#  EN-TÊTE PANNEAU
# ═══════════════════════════════════════════════════════════
class PanelHeader(QFrame):
    def __init__(self, title: str, color_bar: str = A_TEAL, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(26)
        self.setStyleSheet(f"background:{W_DOCK_HDR};border:none;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(6)

        cb = QFrame()
        cb.setFixedSize(3, 14)
        cb.setStyleSheet(f"background:{color_bar};border-radius:1px;")

        self._title = QLabel(title.upper())
        self._title.setFont(QFont(FONT_UI, 10, QFont.Weight.Bold))
        self._title.setStyleSheet(
            f"color:{W_TEXT_HDR};background:transparent;letter-spacing:1.5px;")

        self._status_lbl = QLabel("")
        self._status_lbl.setFont(QFont(FONT_MONO, 10))
        self._status_lbl.setStyleSheet(
            "color:#CCCCCC;background:transparent;font-size:9pt;")

        self._led = StatusLed(9)
        self._led.set_state(False)

        lay.addWidget(cb)
        lay.addWidget(self._title)
        lay.addStretch()
        lay.addWidget(self._status_lbl)
        lay.addWidget(self._led)

    def set_connection(self, ok: bool, host: str = "") -> None:
        self._led.set_state(ok)
        self._status_lbl.setText(host if ok else "—")

    def set_title(self, t: str) -> None:
        self._title.setText(t.upper())


# ═══════════════════════════════════════════════════════════
#  PANNEAU INSTRUMENT
# ═══════════════════════════════════════════════════════════
class InstrumentPanel(QFrame):
    def __init__(self, title: str, color_bar: str = A_TEAL, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame#InstrumentPanel{{background:{W_PANEL};"
            f"border:1px solid {W_BORDER};border-radius:3px;}}")
        self.setObjectName("InstrumentPanel")

        vl = QVBoxLayout(self)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        self._hdr = PanelHeader(title, color_bar)
        vl.addWidget(self._hdr)

        self._body = QWidget()
        self._body.setStyleSheet("background:transparent;")
        self._lay = QVBoxLayout(self._body)
        self._lay.setContentsMargins(10, 8, 10, 8)
        self._lay.setSpacing(6)
        vl.addWidget(self._body, 1)

    def body(self) -> QVBoxLayout:
        return self._lay

    def header(self) -> PanelHeader:
        return self._hdr


# ═══════════════════════════════════════════════════════════
#  AFFICHEUR NUMÉRIQUE
# ═══════════════════════════════════════════════════════════
class NumericDisplay(QWidget):
    def __init__(self, label: str = "VALUE", unit: str = "", parent=None) -> None:
        super().__init__(parent)
        self._label = label
        self._unit  = unit
        self._val   = "0.000"
        self._color = A_TEAL
        self.setFixedHeight(56)
        self.setStyleSheet(
            f"background:{W_PANEL3};border:1px solid {W_BORDER};border-radius:2px;")

    def set_value(self, val_str: str, color: str | None = None) -> None:
        self._val = val_str
        if color:
            self._color = color
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        g = QLinearGradient(0, 0, 0, H)
        g.setColorAt(0, QColor(W_PANEL))
        g.setColorAt(1, QColor(W_PANEL3))
        p.fillRect(0, 0, W, H, QBrush(g))
        p.setPen(QPen(QColor(W_BORDER)))
        p.drawRect(0, 0, W - 1, H - 1)

        # Label
        p.setFont(QFont(FONT_UI, 10))
        p.setPen(QPen(QColor(W_TEXT_DIM)))
        p.drawText(6, 3, W - 12, 14,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._label)
        # Valeur
        p.setFont(QFont(FONT_MONO, 18, QFont.Weight.Bold))
        p.setPen(QPen(QColor(self._color)))
        p.drawText(4, 15, W - 50, H - 18,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._val)
        # Unité
        p.setFont(QFont(FONT_UI, 10))
        p.setPen(QPen(QColor(W_TEXT_DIM)))
        p.drawText(W - 46, 15, 42, H - 18,
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   self._unit)


# ═══════════════════════════════════════════════════════════
#  BARRE LINÉAIRE
# ═══════════════════════════════════════════════════════════
class LinearBar(QWidget):
    def __init__(self, max_v: float = 1.0, unit: str = "",
                 ticks: int = 5, parent=None) -> None:
        super().__init__(parent)
        self._max   = max_v
        self._val   = 0.0
        self._unit  = unit
        self._ticks = ticks
        self._fault = False
        self.setFixedHeight(28)
        self.setStyleSheet("background:transparent;")

    def set_value(self, v: float, fault: bool = False) -> None:
        self._val   = v
        self._fault = fault
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        BH = 14; BY = 2; bw = W

        # Track
        p.setBrush(QBrush(QColor(W_PANEL3)))
        p.setPen(QPen(QColor(W_BORDER), 1))
        p.drawRect(0, BY, bw, BH)

        # Fill
        ratio = min(self._val / max(self._max, 1e-9), 1.0)
        fill  = int(bw * ratio)
        if fill > 2:
            col = QColor(
                A_RED if self._fault else (A_ORANGE if ratio > 0.75 else A_GREEN))
            g = QLinearGradient(0, 0, fill, 0)
            g.setColorAt(0, col.lighter(115))
            g.setColorAt(1, col)
            p.setBrush(QBrush(g))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRect(1, BY + 1, fill - 1, BH - 2)

        # Texte centré
        p.setFont(QFont(FONT_MONO, 10, QFont.Weight.Bold))
        p.setPen(QPen(QColor(W_TEXT)))
        p.drawText(0, BY, bw, BH, Qt.AlignmentFlag.AlignCenter,
                   f"{'FAULT  ' if self._fault else ''}{self._val:.3f} {self._unit}")

        # Graduations
        p.setFont(QFont(FONT_MONO, 9))
        p.setPen(QPen(QColor(W_TEXT_DIM)))
        for i in range(self._ticks + 1):
            x = int(bw * i / self._ticks)
            p.drawLine(x, BY + BH, x, BY + BH + 2)
            v = self._max * i / self._ticks
            p.drawText(x - 10, BY + BH + 2, 20, 10,
                       Qt.AlignmentFlag.AlignCenter, f"{v:.1g}")
