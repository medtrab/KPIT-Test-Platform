"""
WipeWash — Widgets visuels améliorés pour Motor/Pump page
RestContactWidget, SystemStatusWidget, TimeoutFSRWidget

Palette KPIT stricte : uniquement les couleurs de constants.py
  W_BG=#FFFFFF  W_PANEL=#FFFFFF  W_PANEL2=#F8FAFC  W_PANEL3=#F1F5F9
  W_TOOLBAR=#F1F5F9  W_TITLEBAR=#0F1A0A  W_BORDER=rgba(100,116,139,0.25)
  W_TEXT=#1A1A1A  W_TEXT_DIM=#64748B  KPIT_GREEN=#8DC63F
  A_TEAL=#007ACC  A_GREEN=#39FF14  A_RED=#C0392B
  A_ORANGE=#D35400  A_AMBER=#F39C12
"""

import math
from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtCore    import Qt, QTimer, QRectF, QPointF
from PySide6.QtGui     import (
    QPainter, QColor, QPen, QBrush, QFont,
    QLinearGradient, QRadialGradient,
    QPainterPath, QPolygonF,
)

from constants import (
    FONT_UI, FONT_MONO,
    W_BG, W_PANEL, W_PANEL2, W_PANEL3, W_TOOLBAR,
    W_TITLEBAR, W_TEXT, W_TEXT_DIM, W_TEXT_HDR,
    A_TEAL, A_GREEN, A_RED, A_ORANGE, A_AMBER,
    KPIT_GREEN,
)

# ── Couleurs locales — palette KPIT ──────────────────────────────────────────
_KPIT         = QColor(KPIT_GREEN)      # #8DC63F
_KPIT_PALE    = QColor(W_PANEL3)        # #E0F5D0
_KPIT_MID     = QColor(W_PANEL2)        # #EDF9E3
_KPIT_TOOLBAR = QColor(W_TOOLBAR)       # #D6EFC0
_KPIT_DARK    = QColor(W_TITLEBAR)      # #0F1A0A
_TEXT         = QColor(W_TEXT)          # #1A1A1A
_TEXT_DIM     = QColor(W_TEXT_DIM)      # #5A6A4A
_TEAL         = QColor(A_TEAL)          # #007ACC
_RED          = QColor(A_RED)           # #C0392B
_ORANGE       = QColor(A_ORANGE)        # #D35400
_AMBER        = QColor(A_AMBER)         # #F39C12
_BORDER       = QColor(0, 0, 0, 255)          # contour noir unifié


def _alpha(c: QColor, a: int) -> QColor:
    cc = QColor(c); cc.setAlpha(a); return cc


def _draw_panel_bg(p: QPainter, W: int, H: int, r: int = 6) -> None:
    """Fond blanc professionnel — contour slate 1.5px, arrondi 6px."""
    path = QPainterPath()
    path.addRoundedRect(QRectF(1, 1, W - 2, H - 2), r, r)
    bg = QLinearGradient(0, 0, W, H)
    bg.setColorAt(0.00, QColor("#FFFFFF"))
    bg.setColorAt(0.50, QColor("#F8FAFC"))
    bg.setColorAt(1.00, QColor("#F1F5F9"))
    # Grille technique discrète
    p.setBrush(QBrush(bg))
    p.setPen(QPen(QColor("#007ACC"), 1.5))
    p.drawPath(path)
    p.save()
    p.setClipPath(path)
    p.setPen(QPen(QColor(200, 210, 225, 50), 0.5))
    step = 20
    for x in range(0, W, step): p.drawLine(x, 0, x, H)
    for y in range(0, H, step): p.drawLine(0, y, W, y)
    p.restore()


def _draw_stripe(p: QPainter, H: int, color: QColor) -> None:
    sp = QPainterPath()
    sp.addRoundedRect(QRectF(1, 1, 5, H - 2), 2, 2)
    p.setBrush(QBrush(color)); p.setPen(Qt.PenStyle.NoPen)
    p.drawPath(sp)


def _draw_header(p: QPainter, W: int, hdr_h: int, accent: QColor) -> None:
    path = QPainterPath()
    path.addRoundedRect(QRectF(1, 1, W - 2, hdr_h), 8, 8)
    rect = QPainterPath()
    rect.addRect(QRectF(1, hdr_h // 2, W - 2, hdr_h // 2))
    path = path.united(rect)
    bg = QLinearGradient(0, 0, 0, hdr_h)
    bg.setColorAt(0, QColor("#0F1A0A")); bg.setColorAt(1, QColor("#070A04"))
    p.setBrush(QBrush(bg))
    p.setPen(QPen(_alpha(accent, 140), 1))
    p.drawPath(path)


# ═══════════════════════════════════════════════════════════
#  REST CONTACT WIDGET
# ═══════════════════════════════════════════════════════════
class RestContactWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._parked    = False
        self._pulse     = 0.0
        self._pulse_dir = 1
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._t.start(40)
        self.setMinimumSize(150, 100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_state(self, parked: bool) -> None:
        self._parked = parked
        self.update()

    def _tick(self):
        self._pulse += self._pulse_dir * 0.055
        if self._pulse >= 1.0:   self._pulse = 1.0;  self._pulse_dir = -1
        elif self._pulse <= 0.0: self._pulse = 0.0;  self._pulse_dir = 1
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        accent = _TEAL
        _draw_panel_bg(p, W, H)
        _draw_stripe(p, H, accent)

        # Header sombre KPIT
        hdr_h = 26
        _draw_header(p, W, hdr_h, accent)

        # Texte état dans header
        p.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        p.setPen(QPen(accent))
        p.drawText(14, 0, W - 30, hdr_h,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "◼  PARKED" if self._parked else "▶  MOVING")

        # LED pulsante dans header
        led_x = W - 14; led_y = hdr_h // 2
        halo_r = int(7 + 3 * self._pulse)
        p.setBrush(QBrush(_alpha(accent, int(40 * self._pulse))))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(led_x - halo_r // 2, led_y - halo_r // 2, halo_r, halo_r)
        lg = QRadialGradient(led_x - 1, led_y - 1, 5)
        lg.setColorAt(0, accent.lighter(150)); lg.setColorAt(1, accent.darker(110))
        p.setBrush(QBrush(lg)); p.setPen(QPen(accent.darker(130), 1))
        p.drawEllipse(led_x - 5, led_y - 5, 10, 10)

        # GPIO label
        p.setFont(QFont(FONT_MONO, 8))
        p.setPen(QPen(_TEXT_DIM))
        p.drawText(14, hdr_h + 5, W - 20, 14,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "GPIO26 · Front blade")

        # Track blade
        track_y = hdr_h + 26
        track_x = 14; track_w = W - 28; track_h = 10

        tp = QPainterPath()
        tp.addRoundedRect(QRectF(track_x, track_y, track_w, track_h), 5, 5)
        p.setBrush(QBrush(_KPIT_PALE))
        p.setPen(QPen(_alpha(_KPIT, 80), 1))
        p.drawPath(tp)

        if self._parked:
            bp = QPainterPath()
            bp.addRoundedRect(QRectF(track_x, track_y, 18, track_h), 5, 5)
            p.setBrush(QBrush(_KPIT)); p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(bp)
        else:
            pos = 0.1 + 0.75 * self._pulse
            bw = max(14, int(track_w * 0.28))
            bx = track_x + int(pos * (track_w - bw))
            bp = QPainterPath()
            bp.addRoundedRect(QRectF(bx, track_y, bw, track_h), 5, 5)
            p.setBrush(QBrush(_TEAL)); p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(bp)
            if bx > track_x + 4:
                tr = QPainterPath()
                tr.addRoundedRect(QRectF(track_x + 2, track_y + 3,
                                          bx - track_x - 2, track_h - 6), 2, 2)
                p.setBrush(QBrush(_alpha(_TEAL, 30)))
                p.drawPath(tr)

        p.setFont(QFont(FONT_MONO, 7))
        p.setPen(QPen(_TEXT_DIM))
        p.drawText(track_x, track_y + track_h + 3, track_w, 12,
                   Qt.AlignmentFlag.AlignCenter, "BLADE POSITION")


# ═══════════════════════════════════════════════════════════
#  SYSTEM STATUS WIDGET
# ═══════════════════════════════════════════════════════════
class SystemStatusWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._front   = "—"; self._rear = "—"
        self._speed   = "—"; self._current = "—A"
        self._fault   = False
        self._status  = "Waiting for connection…"
        self._anim    = 0.0
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._t.start(50)
        self.setMinimumSize(200, 100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_values(self, front, rear, speed, current, fault, status):
        self._front = front; self._rear = rear
        self._speed = speed; self._current = current
        self._fault = fault; self._status = status
        self.update()

    def _tick(self):
        self._anim = (self._anim + 0.04) % (2 * math.pi)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        bar_accent = _RED if self._fault else _TEAL
        _draw_panel_bg(p, W, H)
        _draw_stripe(p, H, bar_accent)

        # Header
        hdr_h = 26
        _draw_header(p, W, hdr_h, bar_accent)
        p.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        p.setPen(QPen(bar_accent))
        p.drawText(14, 0, W - 20, hdr_h,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "⚠  FAULT DETECTED" if self._fault else "●  SYSTEM NOMINAL")

        # Déterminer la couleur courant
        try:
            cur_val = float(self._current.replace("A", "").strip())
        except Exception:
            cur_val = 0.0
        cur_accent = _RED if self._fault else (_ORANGE if cur_val > 0.8 else _TEAL)

        ITEMS = [
            ("FRONT",   self._front,
             _KPIT if self._front == "ON" else _TEXT_DIM,
             _KPIT_PALE if self._front == "ON" else QColor(W_PANEL)),
            ("REAR",    self._rear,
             _KPIT if self._rear == "ON" else _TEXT_DIM,
             _KPIT_PALE if self._rear == "ON" else QColor(W_PANEL)),
            ("SPEED",   self._speed,  _TEAL,  QColor(W_PANEL2)),
            ("CURRENT", self._current, cur_accent, _KPIT_PALE),
        ]

        n = len(ITEMS)
        pad = 10; status_h = 22
        card_area_y = hdr_h + 5
        card_area_h = H - card_area_y - status_h - 8
        card_w = (W - pad - 6 - (n - 1) * 5) // n
        card_h = max(22, card_area_h)

        for i, (key, val, acc, pale) in enumerate(ITEMS):
            cx = pad + i * (card_w + 5)
            cp = QPainterPath()
            cp.addRoundedRect(QRectF(cx, card_area_y, card_w, card_h), 4, 4)
            p.setBrush(QBrush(pale))
            p.setPen(QPen(_alpha(acc, 100), 1))
            p.drawPath(cp)
            # Bandeau coloré haut
            top = QPainterPath()
            top.addRoundedRect(QRectF(cx, card_area_y, card_w, 3), 1, 1)
            p.setBrush(QBrush(acc)); p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(top)
            # Clé
            p.setFont(QFont(FONT_UI, 7, QFont.Weight.Bold))
            p.setPen(QPen(acc))
            p.drawText(int(cx), int(card_area_y + 4), int(card_w), 13,
                       Qt.AlignmentFlag.AlignCenter, key)
            # Valeur
            p.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
            p.setPen(QPen(acc))
            p.drawText(int(cx), int(card_area_y + 16), int(card_w), int(card_h - 18),
                       Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter, val)

        # Barre statut bas — style toolbar KPIT
        bar_y = H - status_h - 2
        bp = QPainterPath()
        bp.addRoundedRect(QRectF(pad - 2, bar_y, W - pad, status_h), 4, 4)
        if self._fault:
            alpha_v = int(180 + 50 * math.sin(self._anim * 3))
            p.setBrush(QBrush(_alpha(_RED, alpha_v)))
            p.setPen(QPen(_RED.darker(120), 1))
            p.drawPath(bp)
            p.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
            p.setPen(QPen(QColor(W_TEXT_HDR)))
            p.drawText(int(pad), int(bar_y), int(W - pad), status_h,
                       Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter,
                       self._status)
        else:
            p.setBrush(QBrush(_KPIT_TOOLBAR))
            p.setPen(QPen(_alpha(_KPIT, 80), 1))
            p.drawPath(bp)
            dot_x = pad + 7; dot_y = bar_y + status_h // 2
            p.setBrush(QBrush(_KPIT)); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(dot_x - 4, dot_y - 4, 8, 8)
            p.setFont(QFont(FONT_UI, 8))
            p.setPen(QPen(_TEXT))
            p.drawText(pad + 16, bar_y, W - pad - 16, status_h,
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       self._status)


# ═══════════════════════════════════════════════════════════
#  TIMEOUT FSR WIDGET
# ═══════════════════════════════════════════════════════════
class TimeoutFSRWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._remaining = 0.0; self._duration = 5.0
        self._active = False; self._source = ""; self._info_text = "Pump inactive"
        self._anim = 0.0
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._t.start(40)
        self.setMinimumSize(280, 80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_state(self, remaining, duration, active, source, info):
        self._remaining = remaining; self._duration = max(duration, 0.001)
        self._active = active; self._source = source; self._info_text = info
        self.update()

    def _tick(self):
        self._anim = (self._anim + 0.05) % (2 * math.pi)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        pct    = max(0.0, min(1.0, self._remaining / self._duration)) if self._active else 0.0
        urgent = self._active and self._remaining < 1.5

        accent = _RED if urgent else (_AMBER if self._active and pct < 0.4
                 else (_TEAL if self._active else _TEXT_DIM))

        _draw_panel_bg(p, W, H)
        _draw_stripe(p, H, accent)

        # Header
        hdr_h = 26
        _draw_header(p, W, hdr_h, accent)
        p.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        p.setPen(QPen(accent))
        hdr_txt = ("⚠  TIMEOUT URGENT" if urgent
                   else ("⏱  FSR_005 ACTIVE" if self._active else "◉  FSR_005 STANDBY"))
        p.drawText(14, 0, W - 86, hdr_h,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, hdr_txt)

        # Badge source dans le header
        if self._source:
            src_c = _TEAL if self._source == "INTERFACE" else _AMBER
            sbp = QPainterPath()
            sbp.addRoundedRect(QRectF(W - 82, 4, 74, hdr_h - 8), 3, 3)
            p.setBrush(QBrush(_alpha(src_c, 70)))
            p.setPen(QPen(src_c, 1))
            p.drawPath(sbp)
            p.setFont(QFont(FONT_MONO, 7, QFont.Weight.Bold))
            p.setPen(QPen(QColor(W_TEXT_HDR)))
            p.drawText(int(W - 82), 4, 74, hdr_h - 8,
                       Qt.AlignmentFlag.AlignCenter, self._source)

        # LED pulsante
        if self._active:
            led_x = W - 90 if self._source else W - 14
            led_y = hdr_h // 2
            halo = int(7 + 3 * abs(math.sin(self._anim * 2)))
            p.setBrush(QBrush(_alpha(accent, int(45 * abs(math.sin(self._anim))))))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(led_x - halo // 2, led_y - halo // 2, halo, halo)
            lg = QRadialGradient(led_x - 1, led_y - 1, 5)
            lg.setColorAt(0, accent.lighter(150)); lg.setColorAt(1, accent.darker(110))
            p.setBrush(QBrush(lg)); p.setPen(QPen(accent.darker(140), 1))
            p.drawEllipse(led_x - 5, led_y - 5, 10, 10)

        # Contenu : arc countdown + barre
        content_y = hdr_h + 4
        content_h = H - content_y - 4
        arc_cx = 46; arc_cy = content_y + content_h // 2
        arc_R  = min(content_h // 2 - 4, 26)

        # Arc track — vert pâle KPIT
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_KPIT_PALE.darker(115), 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(QRectF(arc_cx - arc_R, arc_cy - arc_R, arc_R * 2, arc_R * 2),
                  90 * 16, -360 * 16)
        if self._active and pct > 0:
            p.setPen(QPen(accent, 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(QRectF(arc_cx - arc_R, arc_cy - arc_R, arc_R * 2, arc_R * 2),
                      90 * 16, int(-360 * 16 * pct))

        # Valeur dans l'arc
        p.setFont(QFont(FONT_MONO, 10, QFont.Weight.Bold))
        p.setPen(QPen(accent))
        p.drawText(arc_cx - arc_R, arc_cy - 10, arc_R * 2, 18,
                   Qt.AlignmentFlag.AlignCenter,
                   f"{self._remaining:.1f}" if self._active else "—")
        p.setFont(QFont(FONT_UI, 7))
        p.setPen(QPen(_TEXT_DIM))
        p.drawText(arc_cx - arc_R, arc_cy + 7, arc_R * 2, 12,
                   Qt.AlignmentFlag.AlignCenter, "sec")

        # Barre linéaire
        bar_x = arc_cx * 2 + 4; bar_y = arc_cy - 8
        bar_h = 16; bar_w = W - bar_x - 10

        bbg = QPainterPath()
        bbg.addRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), bar_h // 2, bar_h // 2)
        p.setBrush(QBrush(_KPIT_PALE))
        p.setPen(QPen(_alpha(_KPIT, 60), 1))
        p.drawPath(bbg)

        if self._active and pct > 0:
            fw = max(bar_h, int(bar_w * pct))
            fp = QPainterPath()
            fp.addRoundedRect(QRectF(bar_x, bar_y, fw, bar_h), bar_h // 2, bar_h // 2)
            fg = QLinearGradient(bar_x, 0, bar_x + fw, 0)
            if urgent:
                fg.setColorAt(0, _alpha(_RED, 140)); fg.setColorAt(1, _RED)
            elif pct < 0.4:
                fg.setColorAt(0, _alpha(_AMBER, 140)); fg.setColorAt(1, _AMBER)
            else:
                fg.setColorAt(0, _alpha(_KPIT, 160)); fg.setColorAt(1, _KPIT)
            p.setBrush(QBrush(fg)); p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(fp)
            for i in range(1, 5):
                tx = int(bar_x + bar_w * i / 5)
                if tx < bar_x + fw:
                    p.setPen(QPen(QColor(255, 255, 255, 100), 1))
                    p.drawLine(tx, bar_y + 3, tx, bar_y + bar_h - 3)

        # Info text
        p.setFont(QFont(FONT_UI, 8))
        p.setPen(QPen(accent if self._active else _TEXT_DIM))
        p.drawText(int(bar_x), int(bar_y + bar_h + 3), int(bar_w), 14,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._info_text)

        # Pulsation alerte
        if urgent:
            full = QPainterPath()
            full.addRoundedRect(QRectF(1, 1, W - 2, H - 2), 8, 8)
            p.setBrush(QBrush(_alpha(_RED, int(12 * abs(math.sin(self._anim * 3))))))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(full)


# ═══════════════════════════════════════════════════════════
#  CURRENT CURVE WIDGET  (Pompe ou Moteur)
#  Oscilloscope-style rolling waveform avec palette KPIT
# ═══════════════════════════════════════════════════════════
class CurrentCurveWidget(QWidget):
    """
    Courbe de courant en temps réel — défilement continu (style oscilloscope).
    channel : "PUMP" | "MOTOR_FRONT" | "MOTOR_REAR"
    max_val : valeur maximale de l'axe Y (A)
    """

    _HISTORY = 120          # nombre de points conservés
    _TICK_MS  = 50          # rafraîchissement 20 Hz

    def __init__(self, channel: str = "PUMP", max_val: float = 2.0, parent=None):
        super().__init__(parent)
        self._channel  = channel
        self._max_val  = max_val
        self._current  = 0.0
        self._fault    = False
        self._history: list[float] = [0.0] * self._HISTORY
        self._phase    = 0.0          # animation demo si pas de données

        # Couleur selon canal
        if channel == "PUMP":
            self._accent = _TEAL
        elif channel == "MOTOR_FRONT":
            self._accent = _TEAL
        else:
            self._accent = _TEAL

        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._t.start(self._TICK_MS)

        self.setMinimumSize(200, 130)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ── API publique ──────────────────────────────────────
    def set_value(self, current: float, fault: bool = False) -> None:
        self._current = max(0.0, current)
        self._fault   = fault
        self._push(self._current)
        self.update()

    # ── Interne ───────────────────────────────────────────
    def _push(self, v: float) -> None:
        self._history.append(v)
        if len(self._history) > self._HISTORY:
            self._history.pop(0)

    def _tick(self):
        # Animation démo quand aucune donnée (courant = 0 depuis longtemps)
        self._phase += 0.08
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        accent = _RED if self._fault else self._accent

        # Fond + bordure
        _draw_panel_bg(p, W, H)
        _draw_stripe(p, H, accent)

        # Header sombre
        hdr_h = 26
        _draw_header(p, W, hdr_h, accent)

        # Titre canal
        label_map = {
            "PUMP":         "PUMP — Current (A)",
            "MOTOR_FRONT":  "MOTOR FRONT — Current (A)",
            "MOTOR_REAR":   "MOTOR REAR — Current (A)",
        }
        p.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        p.setPen(QPen(accent))
        p.drawText(14, 0, W - 80, hdr_h,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   label_map.get(self._channel, self._channel))

        # Valeur courante en haut à droite du header
        p.setFont(QFont(FONT_MONO, 9, QFont.Weight.Bold))
        p.setPen(QPen(accent))
        p.drawText(W - 76, 0, 68, hdr_h,
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   f"{self._current:.3f} A")

        # Zone oscilloscope
        PAD = 10
        ox  = PAD + 8           # origine X (espace pour axe Y)
        oy  = hdr_h + 6         # origine Y
        ow  = W - ox - PAD
        oh  = H - oy - 22       # hauteur graphe (espace bas pour axe X)
        if ow < 20 or oh < 20:
            return

        # Fond graphe — très sombre
        graph_rect = QRectF(ox, oy, ow, oh)
        p.setBrush(QBrush(QColor("#0D1E0A")))
        p.setPen(QPen(QColor(141, 198, 63, 40), 1))
        p.drawRect(graph_rect)

        # Lignes de grille horizontales (4 niveaux)
        GRID_LINES = 4
        for i in range(GRID_LINES + 1):
            gy = oy + oh * i / GRID_LINES
            p.setPen(QPen(QColor(141, 198, 63, 20 if i not in (0, GRID_LINES) else 35), 1))
            p.drawLine(QPointF(ox, gy), QPointF(ox + ow, gy))
            # Label axe Y
            val_y = self._max_val * (1 - i / GRID_LINES)
            p.setFont(QFont(FONT_MONO, 6))
            p.setPen(QPen(_TEXT_DIM))
            p.drawText(int(PAD - 4), int(gy - 5), 14, 11,
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{val_y:.1f}")

        # Aire remplie sous la courbe
        pts = self._history
        n   = len(pts)
        if n >= 2:
            step = ow / (n - 1)

            # Chemin rempli
            fill = QPainterPath()
            x0 = ox + 0 * step
            y0 = oy + oh * (1.0 - min(1.0, pts[0] / self._max_val))
            fill.moveTo(x0, oy + oh)
            fill.lineTo(x0, y0)
            for i in range(1, n):
                xi = ox + i * step
                yi = oy + oh * (1.0 - min(1.0, pts[i] / self._max_val))
                fill.lineTo(xi, yi)
            fill.lineTo(ox + (n - 1) * step, oy + oh)
            fill.closeSubpath()

            fill_grad = QLinearGradient(0, oy, 0, oy + oh)
            fill_grad.setColorAt(0, _alpha(accent, 70))
            fill_grad.setColorAt(1, _alpha(accent, 8))
            p.setBrush(QBrush(fill_grad))
            p.setPen(Qt.PenStyle.NoPen)
            p.setClipRect(graph_rect)
            p.drawPath(fill)

            # Trait de courbe
            line = QPainterPath()
            x0 = ox
            y0 = oy + oh * (1.0 - min(1.0, pts[0] / self._max_val))
            line.moveTo(x0, y0)
            for i in range(1, n):
                xi = ox + i * step
                yi = oy + oh * (1.0 - min(1.0, pts[i] / self._max_val))
                line.lineTo(xi, yi)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(accent, 1.5, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPath(line)
            p.setClipping(False)

        # Axe X — label durée
        p.setFont(QFont(FONT_MONO, 6))
        p.setPen(QPen(_TEXT_DIM))
        dur_s = self._HISTORY * self._TICK_MS / 1000.0
        p.drawText(ox, H - 16, ow, 14,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"-{dur_s:.0f}s")
        p.drawText(ox, H - 16, ow, 14,
                   Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter,
                   "time →")
        p.drawText(ox, H - 16, ow, 14,
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   "now")


# ═══════════════════════════════════════════════════════════
#  REST CONTACT EDGE WIDGET
#  Affichage fronts montant (LOW→HIGH) et descendant (HIGH→LOW)
#  en continu, style oscilloscope numérique
# ═══════════════════════════════════════════════════════════
class RestContactEdgeWidget(QWidget):
    """
    Affiche le signal binaire REST CONTACT en continu :
      • front montant  LOW→HIGH  (rising  edge)  ↑
      • front descendant HIGH→LOW (falling edge)  ↓
    Historique glissant + annotation des fronts détectés.
    """

    _HISTORY = 100
    _TICK_MS  = 60

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state   = False           # état courant du contact
        self._history: list[bool] = [False] * self._HISTORY
        self._edges:   list[tuple]= []  # (index, "rise"|"fall")
        self._pulse   = 0.0
        self._pulse_d = 1

        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._t.start(self._TICK_MS)

        self.setMinimumSize(200, 140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ── API publique ──────────────────────────────────────
    def set_state(self, parked: bool) -> None:
        prev = self._state
        self._state = parked
        self._push(parked)
        # Détection front
        if not prev and parked:
            self._edges.append((len(self._history) - 1, "rise"))
        elif prev and not parked:
            self._edges.append((len(self._history) - 1, "fall"))
        # Garder seulement les fronts dans la fenêtre visible
        self._edges = [(idx, t) for idx, t in self._edges
                       if idx >= len(self._history) - self._HISTORY]
        self.update()

    # ── Interne ───────────────────────────────────────────
    def _push(self, v: bool) -> None:
        self._history.append(v)
        if len(self._history) > self._HISTORY:
            removed = len(self._history) - self._HISTORY
            self._history = self._history[-self._HISTORY:]
            self._edges = [(idx - removed, t) for idx, t in self._edges]
            self._edges = [(idx, t) for idx, t in self._edges if idx >= 0]

    def _tick(self):
        self._pulse += self._pulse_d * 0.06
        if self._pulse >= 1.0:   self._pulse = 1.0;  self._pulse_d = -1
        elif self._pulse <= 0.0: self._pulse = 0.0;  self._pulse_d = 1
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        accent = _TEAL

        # Fond + bordure
        _draw_panel_bg(p, W, H)
        _draw_stripe(p, H, accent)

        # Header
        hdr_h = 26
        _draw_header(p, W, hdr_h, accent)

        p.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        p.setPen(QPen(accent))
        state_lbl = "PARKED (HIGH)" if self._state else "MOVING (LOW)"
        p.drawText(14, 0, W - 80, hdr_h,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"REST CONTACT — {state_lbl}")

        # LED pulsante
        led_x = W - 14; led_y = hdr_h // 2
        halo_r = int(7 + 3 * self._pulse)
        p.setBrush(QBrush(_alpha(accent, int(40 * self._pulse))))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(led_x - halo_r // 2, led_y - halo_r // 2, halo_r, halo_r)
        lg = QRadialGradient(led_x - 1, led_y - 1, 5)
        lg.setColorAt(0, accent.lighter(150)); lg.setColorAt(1, accent.darker(110))
        p.setBrush(QBrush(lg)); p.setPen(QPen(accent.darker(130), 1))
        p.drawEllipse(led_x - 5, led_y - 5, 10, 10)

        # ── Zone oscilloscope numérique ────────────────────
        PAD  = 10
        ox   = PAD + 26     # espace pour labels axe Y
        oy   = hdr_h + 8
        ow   = W - ox - PAD
        oh   = H - oy - 30  # hauteur graphe
        if ow < 20 or oh < 20:
            return

        graph_rect = QRectF(ox, oy, ow, oh)
        p.setBrush(QBrush(QColor("#0D1E0A")))
        p.setPen(QPen(QColor(141, 198, 63, 40), 1))
        p.drawRect(graph_rect)

        # Lignes HIGH / LOW
        y_high = oy + oh * 0.15
        y_low  = oy + oh * 0.85

        p.setPen(QPen(QColor(141, 198, 63, 25), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(ox, y_high), QPointF(ox + ow, y_high))
        p.drawLine(QPointF(ox, y_low),  QPointF(ox + ow, y_low))

        # Labels HIGH / LOW sur axe gauche
        p.setFont(QFont(FONT_MONO, 7, QFont.Weight.Bold))
        p.setPen(QPen(_KPIT))
        p.drawText(PAD - 2, int(y_high) - 8, 22, 14,
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "H")
        p.setPen(QPen(_TEAL))
        p.drawText(PAD - 2, int(y_low) - 8, 22, 14,
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "L")

        # Signal créneau
        hist = self._history
        n    = len(hist)
        if n >= 2:
            step = ow / (n - 1)
            sig  = QPainterPath()
            def _yx(v): return y_high if v else y_low

            x0 = ox
            y0 = _yx(hist[0])
            sig.moveTo(x0, y0)
            for i in range(1, n):
                xi = ox + i * step
                yi = _yx(hist[i])
                if yi != _yx(hist[i - 1]):
                    sig.lineTo(xi, _yx(hist[i - 1]))  # flanc vertical
                sig.lineTo(xi, yi)

            p.setClipRect(graph_rect)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(accent, 2.0, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.SquareCap, Qt.PenJoinStyle.MiterJoin))
            p.drawPath(sig)

            # Annotation des fronts
            for edge_idx, edge_type in self._edges:
                if 0 <= edge_idx < n:
                    ex = ox + edge_idx * step
                    is_rise = edge_type == "rise"
                    ec = _TEAL
                    arrow = "↑" if is_rise else "↓"
                    label = "LOW→HIGH" if is_rise else "HIGH→LOW"

                    # Ligne verticale sur le front
                    p.setPen(QPen(_alpha(ec, 180), 1, Qt.PenStyle.DashLine))
                    p.drawLine(QPointF(ex, oy + 2), QPointF(ex, oy + oh - 2))

                    # Flèche + label
                    ey_arrow = y_high - 14 if is_rise else y_low + 4
                    p.setPen(QPen(ec))
                    p.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
                    p.drawText(int(ex) - 10, int(ey_arrow), 20, 14,
                               Qt.AlignmentFlag.AlignCenter, arrow)
                    # Petit label texte si assez de place
                    lbl_x = min(int(ex) - 24, int(ox + ow) - 56)
                    lbl_x = max(lbl_x, int(ox))
                    p.setFont(QFont(FONT_MONO, 6))
                    p.setPen(QPen(_alpha(ec, 200)))
                    p.drawText(lbl_x, int(ey_arrow) + 12, 56, 10,
                               Qt.AlignmentFlag.AlignLeft, label)

            p.setClipping(False)

        # Barre état bas
        bar_y = H - 22
        # Compteurs fronts
        n_rise = sum(1 for _, t in self._edges if t == "rise")
        n_fall = sum(1 for _, t in self._edges if t == "fall")

        p.setFont(QFont(FONT_MONO, 7))
        p.setPen(QPen(_KPIT))
        p.drawText(ox, bar_y, ow // 2, 18,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"↑ rises: {n_rise}")
        p.setPen(QPen(_TEAL))
        p.drawText(ox + ow // 2, bar_y, ow // 2, 18,
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   f"falls: {n_fall} ↓")


# Re-exports expected by tests (canonical definitions live in widgets_instruments)
from widgets_instruments import MotorWidget, PumpWidget
