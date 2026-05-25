"""
WipeWash — Widgets de base
StatusLed, PanelHeader, InstrumentPanel, NumericDisplay, LinearBar,
et helpers (_font, _lbl, _hsep, _vsep, _cd_btn).
"""

import math

from PySide6.QtWidgets import QWidget, QFrame, QLabel, QPushButton, QHBoxLayout, QVBoxLayout
from PySide6.QtCore    import Qt
from PySide6.QtGui     import (
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
    f.setStyleSheet("background:rgba(100,116,139,0.25);border:none;max-height:1px;")
    return f


def _vsep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.VLine)
    f.setStyleSheet("background:rgba(100,116,139,0.25);border:none;max-width:1px;")
    return f


def _cd_btn(text: str, color: str = "#007ACC",
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
        new_color = QColor(color) if color else QColor(A_GREEN if on else A_RED)
        if self._on == on and self._color == new_color:
            return   # rien n'a changé → pas de repaint inutile
        self._on    = on
        self._color = new_color
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
        self._color_bar = color_bar
        # Header sombre KPIT unifié — même recette que les draw_header des widgets
        self.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #0F1A0A, stop:1 #070A04);"
            "border:none;"
            "border-bottom:2px solid #000000;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(6)

        # Barre colorée gauche (3px) — identifiant visuel du canal
        cb = QFrame()
        cb.setFixedSize(3, 16)
        cb.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            f"stop:0 {QColor(color_bar).lighter(130).name()}, stop:1 {color_bar});"
            f"border-radius:1px;")

        self._title = QLabel(title.upper())
        self._title.setFont(QFont(FONT_MONO, 9, QFont.Weight.Bold))
        self._title.setMinimumWidth(0)
        self._title.setMaximumWidth(9999)
        self._title.setStyleSheet("color:#E8F8E0;background:transparent;")

        self._status_lbl = QLabel("")
        self._status_lbl.setFont(QFont(FONT_MONO, 9))
        self._status_lbl.setStyleSheet("color:#8DC63F;background:transparent;")

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
#  PANNEAU INSTRUMENT — design unifié KPIT
# ═══════════════════════════════════════════════════════════
class InstrumentPanel(QFrame):
    def __init__(self, title: str, color_bar: str = A_TEAL, parent=None) -> None:
        super().__init__(parent)
        # Fond blanc professionnel + contour slate unique
        self.setStyleSheet(
            "QFrame#InstrumentPanel{"
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            "stop:0 #FFFFFF, stop:0.5 #F8FAFC, stop:1 #F1F5F9);"
            "border:1.5px solid #CBD5E1;"
            "border-radius:6px;}")
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
#  AFFICHEUR NUMÉRIQUE — LCD vert foncé KPIT
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
            "background:transparent;"
            "border:1.5px solid #CBD5E1;"
            "border-radius:4px;")

    def set_value(self, val_str: str, color: str | None = None) -> None:
        new_color = color or self._color
        if self._val == val_str and new_color == self._color:
            return
        self._val = val_str
        if color:
            self._color = color
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        # Fond blanc cassé professionnel
        g = QLinearGradient(0, 0, 0, H)
        g.setColorAt(0, QColor("#F8FAFC"))
        g.setColorAt(1, QColor("#F1F5F9"))
        p.fillRect(0, 0, W, H, QBrush(g))

        # Reflet vitre LCD subtil
        p.setBrush(QBrush(QColor(255, 255, 255, 10)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(2, 2, W - 4, int(H * 0.4), 2, 2)

        p.setPen(QPen(QColor("#CBD5E1"), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(1, 1, W - 2, H - 2, 4, 4)

        p.setFont(QFont(FONT_MONO, 8))
        p.setPen(QPen(QColor("#64748B")))
        p.drawText(6, 3, W - 12, 16,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._label)
        # Valeur — couleur dynamique
        p.setFont(QFont(FONT_MONO, 18, QFont.Weight.Bold))
        p.setPen(QPen(QColor(self._color)))
        p.drawText(4, 17, W - 52, H - 20,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._val)
        p.setFont(QFont(FONT_MONO, 9))
        p.setPen(QPen(QColor("#64748B")))
        p.drawText(W - 48, 17, 44, H - 20,
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   self._unit)


# ═══════════════════════════════════════════════════════════
#  BARRE LINÉAIRE — thème KPIT unifié, contour noir
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
        self.setFixedHeight(32)
        self.setStyleSheet("background:transparent;")

    def set_value(self, v: float, fault: bool = False) -> None:
        if self._val == v and self._fault == fault:
            return
        self._val   = v
        self._fault = fault
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        BH = 15; BY = 2; bw = W

        # Track fond gris clair professionnel
        track_g = QLinearGradient(0, BY, 0, BY + BH)
        track_g.setColorAt(0, QColor("#E2E8F0"))
        track_g.setColorAt(1, QColor("#CBD5E1"))
        p.setBrush(QBrush(track_g))
        p.setPen(QPen(QColor("#94A3B8"), 1.0))
        p.drawRoundedRect(0, BY, bw, BH, 2, 2)

        # Fill coloré avec glow
        ratio = min(self._val / max(self._max, 1e-9), 1.0)
        fill  = int(bw * ratio)
        if fill > 3:
            if self._fault:
                fill_col = QColor(A_RED)
            elif ratio > 0.75:
                fill_col = QColor(A_ORANGE)
            else:
                fill_col = QColor("#8DC63F")   # vert KPIT signature
            fg = QLinearGradient(0, 0, fill, 0)
            fg.setColorAt(0, fill_col.lighter(130))
            fg.setColorAt(0.5, fill_col)
            fg.setColorAt(1, fill_col.darker(110))
            p.setBrush(QBrush(fg))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(2, BY + 2, fill - 2, BH - 4, 1, 1)
            # Reflet brillant dessus
            p.setBrush(QBrush(QColor(255, 255, 255, 30)))
            p.drawRoundedRect(2, BY + 2, fill - 2, (BH - 4) // 2, 1, 1)

        p.setFont(QFont(FONT_MONO, 9, QFont.Weight.Bold))
        txt_col = QColor(A_RED) if self._fault else QColor("#334155")
        p.setPen(QPen(txt_col))
        p.drawText(0, BY, bw, BH, Qt.AlignmentFlag.AlignCenter,
                   f"{'FAULT  ' if self._fault else ''}{self._val:.3f} {self._unit}")

        p.setFont(QFont(FONT_MONO, 7))
        p.setPen(QPen(QColor("#64748B")))
        for i in range(self._ticks + 1):
            x = int(bw * i / self._ticks)
            p.drawLine(x, BY + BH + 1, x, BY + BH + 3)
            v = self._max * i / self._ticks
            p.drawText(x - 12, BY + BH + 3, 24, 10,
                       Qt.AlignmentFlag.AlignCenter, f"{v:.1g}")
