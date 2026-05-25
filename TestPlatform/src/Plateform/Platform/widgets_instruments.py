"""
WipeWash — Widgets instruments graphiques (redesign v4 — fond blanc professionnel — BACKEND INCHANGÉ)
MotorWidget et PumpWidget : designs photoréalistes style SolidWorks/KeyShot
issus de car_comodo.py.
WindshieldWidget, CarTopViewWidget, ArcGaugeWidget, SparklineWidget : inchangés.

BACKEND / SIGNAUX inchangés : set_state(), set_rain(), set_value(), push()
et toutes les connexions SignalHub restent identiques.
"""

import math
import random
from collections import deque

from PySide6.QtWidgets import QWidget, QSizePolicy, QVBoxLayout, QHBoxLayout, QLabel
from PySide6.QtCore    import Qt, QTimer, QRectF, QPointF
from PySide6.QtGui     import (
    QPainter, QColor, QPen, QBrush, QFont,
    QLinearGradient, QRadialGradient, QConicalGradient,
    QPainterPath, QPolygonF,
)

from constants import (
    FONT_UI, FONT_MONO,
    W_PANEL, W_PANEL2, W_PANEL3,
    W_BORDER, W_BORDER2, W_TEXT, W_TEXT_DIM,
    A_TEAL, A_TEAL2, A_GREEN, A_RED, A_ORANGE, A_AMBER,
    WOP,
)

# ── Palette interne (conservée pour ArcGauge/Sparkline/Windshield) ──────────
_C_BG        = QColor("#FFFFFF")
_C_GRID      = QColor(90, 150, 60, 30)
_C_GREEN     = QColor("#8DC63F")
_C_TEAL      = QColor("#007ACC")
_C_ORANGE    = QColor("#D35400")
_C_RED       = QColor("#C0392B")
_C_DIM       = QColor("#5A6A4A")
_C_METAL0    = QColor("#7A9A5A")
_C_METAL1    = QColor("#5A7A3A")
_C_METAL2    = QColor("#3A5A20")
_C_COIL_R    = QColor("#C04018")
_C_COIL_B    = QColor("#1060C0")
_C_COIL_RD   = QColor("#A83010")
_C_COIL_BD   = QColor("#0840A0")

def _hex_alpha(qc: QColor, alpha: int) -> QColor:
    c = QColor(qc); c.setAlpha(alpha); return c

# ── Helpers photoréalistes (portés de car_comodo.py) ──────────────────────
_LIGHT_RC = QPointF(-0.6, -0.8)   # source lumineuse principale

def _rc_phong_color(base: QColor, nx: float, ny: float,
                    ambient=0.12, diffuse=0.68, specular=0.72, shininess=64) -> QColor:
    nlen = math.hypot(nx, ny)
    if nlen < 1e-6: nx, ny = 0.0, -1.0
    else:           nx /= nlen; ny /= nlen
    ndotl = _clamp(-(nx * _LIGHT_RC.x() + ny * _LIGHT_RC.y()))
    hx = -_LIGHT_RC.x(); hy = -_LIGHT_RC.y(); hz = 1.0
    hlen = math.sqrt(hx*hx + hy*hy + hz*hz)
    hx /= hlen; hy /= hlen
    ndoth = _clamp(nx*hx + ny*hy)
    spec = (ndoth ** shininess) * specular
    r = _clamp(base.redF()   * (ambient + diffuse*ndotl) + spec)
    g = _clamp(base.greenF() * (ambient + diffuse*ndotl) + spec)
    b = _clamp(base.blueF()  * (ambient + diffuse*ndotl) + spec)
    return QColor.fromRgbF(r, g, b)

def _rc_metal_brush_v(rect: QRectF, base: QColor, roughness=0.25) -> QBrush:
    g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    n = 12
    for i in range(n):
        t = i / (n-1)
        ny = math.cos(math.pi * t) * (1-roughness)
        c = _rc_phong_color(base, -0.2, ny)
        g.setColorAt(t, c)
    g.setColorAt(0.18, QColor(255, 255, 255, int(200*(1-roughness))))
    g.setColorAt(0.26, QColor(255, 255, 255, int(30*(1-roughness))))
    g.setColorAt(0.34, QColor(255, 255, 255, 0))
    g.setColorAt(0.90, QColor(0, 0, 0, int(40*(1-roughness*0.5))))
    g.setColorAt(1.0,  QColor(0, 0, 0, int(80*(1-roughness*0.5))))
    return QBrush(g)

def _rc_sphere_brush(cx: float, cy: float, r: float, base: QColor, roughness=0.2) -> QBrush:
    lx = cx - r * 0.38; ly = cy - r * 0.44
    g = QRadialGradient(lx, ly, r * 1.15, cx, cy, r)
    hi  = _rc_phong_color(base, -0.7, -0.7, ambient=0.04, diffuse=0.48,
                          specular=1.0*(1-roughness), shininess=80)
    mid = _rc_phong_color(base, 0.0, 0.0, ambient=0.22, diffuse=0.52, specular=0.0)
    lo  = _rc_phong_color(base, 0.65, 0.75, ambient=0.06, diffuse=0.28, specular=0.0)
    rim_col = QColor(
        min(255, int(base.red()   * 0.15 + 40)),
        min(255, int(base.green() * 0.15 + 60)),
        min(255, int(base.blue()  * 0.3  + 120)),
        int(90 * (1 - roughness))
    )
    g.setColorAt(0.0,  QColor(255, 255, 255, int(240*(1-roughness))))
    g.setColorAt(0.08, hi)
    g.setColorAt(0.48, mid)
    g.setColorAt(0.82, lo)
    g.setColorAt(1.0,  rim_col)
    return QBrush(g)

def _rc_mk_pen(color, w=1.5, style=Qt.SolidLine):
    p = QPen(color, w, style)
    p.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return p

def _rc_drop_shadow(painter: QPainter, path_or_rect, blur=8, offset=(4, 5)):
    ox, oy = offset
    painter.save()
    for i in range(blur, 0, -1):
        t = i / blur
        alpha = int(60 * (1 - t) * (1 - t))
        painter.translate(ox * t, oy * t)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0, alpha)))
        if isinstance(path_or_rect, QRectF):
            painter.drawRoundedRect(path_or_rect, 10, 10)
        else:
            painter.drawPath(path_or_rect)
        painter.translate(-ox * t, -oy * t)
    painter.restore()

class _RcMat:
    STEEL   = QColor("#8a9aaa")
    STEEL_D = QColor("#38444e")
    PCB     = QColor("#183818")
    GOLD_W  = QColor("#d4a010")
    GREEN   = QColor("#28cc58")
    AMBER   = QColor("#d88010")
    RED     = QColor("#cc2828")


# ══════════════════════════════════════════════════════════════════════
#  UTILITAIRES PHOTORÉALISTES (depuis car_comodo.py)
# ══════════════════════════════════════════════════════════════════════
_LIGHT = QPointF(-0.6, -0.8)

def _clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))

def _phong_color(base: QColor, nx: float, ny: float,
                 ambient=0.12, diffuse=0.68, specular=0.72, shininess=64) -> QColor:
    nlen = math.hypot(nx, ny)
    if nlen < 1e-6:
        nx, ny = 0.0, -1.0
    else:
        nx /= nlen; ny /= nlen
    ndotl = _clamp(-(nx * _LIGHT.x() + ny * _LIGHT.y()))
    hx = -_LIGHT.x(); hy = -_LIGHT.y(); hz = 1.0
    hlen = math.sqrt(hx*hx + hy*hy + hz*hz)
    hx /= hlen; hy /= hlen; hz /= hlen
    ndoth = _clamp(nx*hx + ny*hy)
    spec = (ndoth ** shininess) * specular
    r = _clamp(base.redF()   * (ambient + diffuse*ndotl) + spec)
    g = _clamp(base.greenF() * (ambient + diffuse*ndotl) + spec)
    b = _clamp(base.blueF()  * (ambient + diffuse*ndotl) + spec)
    return QColor.fromRgbF(r, g, b)

def _metal_brush_h(rect: QRectF, base: QColor, roughness=0.25, n_stops=12) -> QBrush:
    g = QLinearGradient(rect.topLeft(), rect.topRight())
    for i in range(n_stops):
        t = i / (n_stops - 1)
        nx = math.cos(math.pi * t) * (1 - roughness)
        c = _phong_color(base, nx, -0.3)
        g.setColorAt(t, c)
    g.setColorAt(0.24, QColor(255, 255, 255, int(210 * (1-roughness))))
    g.setColorAt(0.30, QColor(255, 255, 255, int(40  * (1-roughness))))
    g.setColorAt(0.36, QColor(255, 255, 255, 0))
    g.setColorAt(0.88, QColor(60, 100, 180, int(30*(1-roughness))))
    g.setColorAt(1.0,  QColor(40, 70, 140, int(50*(1-roughness))))
    return QBrush(g)

def _metal_brush_v(rect: QRectF, base: QColor, roughness=0.25) -> QBrush:
    g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    n = 12
    for i in range(n):
        t = i / (n-1)
        ny = math.cos(math.pi * t) * (1-roughness)
        c = _phong_color(base, -0.2, ny)
        g.setColorAt(t, c)
    g.setColorAt(0.18, QColor(255, 255, 255, int(200*(1-roughness))))
    g.setColorAt(0.26, QColor(255, 255, 255, int(30*(1-roughness))))
    g.setColorAt(0.34, QColor(255, 255, 255, 0))
    g.setColorAt(0.90, QColor(0, 0, 0, int(40*(1-roughness*0.5))))
    g.setColorAt(1.0,  QColor(0, 0, 0, int(80*(1-roughness*0.5))))
    return QBrush(g)

def _sphere_brush(cx: float, cy: float, r: float, base: QColor, roughness=0.2) -> QBrush:
    lx = cx - r * 0.38
    ly = cy - r * 0.44
    g = QRadialGradient(lx, ly, r * 1.15, cx, cy, r)
    hi  = _phong_color(base, -0.7, -0.7, ambient=0.04, diffuse=0.48,
                       specular=1.0*(1-roughness), shininess=80)
    mid = _phong_color(base, 0.0, 0.0, ambient=0.22, diffuse=0.52, specular=0.0)
    lo  = _phong_color(base, 0.65, 0.75, ambient=0.06, diffuse=0.28, specular=0.0)
    rim_col = QColor(
        min(255, int(base.red()   * 0.15 + 40)),
        min(255, int(base.green() * 0.15 + 60)),
        min(255, int(base.blue()  * 0.3  + 120)),
        int(90 * (1 - roughness))
    )
    g.setColorAt(0.0,  QColor(255, 255, 255, int(240*(1-roughness))))
    g.setColorAt(0.08, hi)
    g.setColorAt(0.48, mid)
    g.setColorAt(0.82, lo)
    g.setColorAt(1.0,  rim_col)
    return QBrush(g)

def _mk_pen(color, w=1.5, style=Qt.SolidLine):
    pen = QPen(color, w, style)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen

def _drop_shadow(painter: QPainter, rect: QRectF, blur=8, offset=(5, 6)):
    ox, oy = offset
    painter.save()
    for i in range(blur, 0, -1):
        t = i / blur
        alpha = int(70 * (1 - t) * (1 - t))
        painter.translate(ox * t, oy * t)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0, alpha)))
        painter.drawRoundedRect(rect, 12, 12)
        painter.translate(-ox * t, -oy * t)
    painter.restore()

def _draw_grid(painter: QPainter, width: int, height: int, spacing: int = 30):
    painter.save()
    painter.setPen(QPen(QColor(0, 0, 0, 55), 0.7, Qt.SolidLine))
    for x in range(0, width, spacing):
        painter.drawLine(x, 0, x, height)
    for y in range(0, height, spacing):
        painter.drawLine(0, y, width, y)
    painter.setPen(QPen(QColor(0, 0, 0, 100), 1.0, Qt.SolidLine))
    for x in range(0, width, spacing*5):
        painter.drawLine(x, 0, x, height)
    for y in range(0, height, spacing*5):
        painter.drawLine(0, y, width, y)
    painter.restore()

class _Mat:
    ALU      = QColor("#9aabb8")
    ALU_DARK = QColor("#5a6a78")
    STEEL    = QColor("#8a9aaa")
    STEEL_D  = QColor("#38444e")
    COPPER   = QColor("#b86830")
    GOLD_W   = QColor("#d4a010")
    BRONZE   = QColor("#9a6818")
    RUBBER   = QColor("#181818")
    PLASTIC  = QColor("#252535")
    GREEN    = QColor("#28cc58")
    AMBER    = QColor("#d88010")
    RED      = QColor("#cc2828")
    WATER    = QColor("#1a6aaa")


# ══════════════════════════════════════════════════════════════════════
#  FOND COMMUN photoréaliste (vert studio KPIT)
# ══════════════════════════════════════════════════════════════════════
def _draw_photo_bg(p: QPainter, W: int, H: int):
    """Fond blanc professionnel — subtil dégradé blanc cassé, grille technique légère."""
    # Fond blanc avec très légère texture
    bg_g = QLinearGradient(0, 0, 0, H)
    bg_g.setColorAt(0.0, QColor(252, 253, 255))
    bg_g.setColorAt(0.5, QColor(248, 250, 253))
    bg_g.setColorAt(1.0, QColor(244, 247, 252))
    p.fillRect(0, 0, W, H, QBrush(bg_g))
    # Grille technique ultra-légère (pointillé discret)
    p.save()
    p.setPen(QPen(QColor(200, 210, 225, 60), 0.5, Qt.PenStyle.SolidLine))
    step = 20
    for x in range(0, W, step):
        p.drawLine(x, 0, x, H)
    for y in range(0, H, step):
        p.drawLine(0, y, W, y)
    # Lignes majeures toutes les 100px
    p.setPen(QPen(QColor(180, 195, 215, 80), 0.7, Qt.PenStyle.SolidLine))
    for x in range(0, W, 100):
        p.drawLine(x, 0, x, H)
    for y in range(0, H, 100):
        p.drawLine(0, y, W, y)
    p.restore()


# ══════════════════════════════════════════════════════════════════════
#  MOTOR WIDGET — Design photoréaliste (WiperMotorWidget de car_comodo.py)
#  Backend inchangé : set_state(state, speed)
# ══════════════════════════════════════════════════════════════════════
class MotorWidget(QWidget):
    def __init__(self, motor_id: str = "FRONT", parent=None) -> None:
        super().__init__(parent)
        self._id          = motor_id
        self._state       = "OFF"
        self._speed       = "Speed1"
        self._angle       = 0.0
        self._crank_angle = 0.0
        self._heat        = 0.0
        self._t = QTimer()
        self._t.timeout.connect(self._tick)
        self._t.start(33)
        self.setMinimumSize(300, 200)

    # ── API backend inchangée ─────────────────────────────────────────
    def set_state(self, state: str, speed: str = "Speed1") -> None:
        self._state = state
        self._speed = speed
        self.update()

    def _tick(self) -> None:
        on = self._state == "ON"
        if on:
            rpm = 65 if self._speed == "Speed2" else 40
            self._angle       = (self._angle       + rpm * 6 / 30) % 360
            self._crank_angle = (self._crank_angle + rpm     / 30) % 360
            self._heat  = min(1.0, self._heat + 0.02)
            self.update()
        else:
            if self._angle % 360 > 2:
                self._angle       = (self._angle       + 0.7)  % 360
                self._crank_angle = (self._crank_angle + 0.18) % 360
                self._heat  = max(0.0, self._heat - 0.01)
                self.update()
            elif self._heat > 0:
                self._heat = max(0.0, self._heat - 0.005)
                self.update()

    # ── Éléments de rendu ─────────────────────────────────────────────
    def _draw_carter(self, p, cx, cy, sc):
        Wc, Hc = int(310*sc), int(180*sc)
        rect = QRectF(cx - Wc//2, cy - Hc//2, Wc, Hc)
        _drop_shadow(p, rect, blur=10, offset=(6, 8))
        p.setBrush(_metal_brush_h(rect, _Mat.ALU, roughness=0.14))
        p.setPen(_mk_pen(QColor("#4a5a72"), 1.5))
        p.drawRoundedRect(rect, 14*sc, 14*sc)
        for i in range(9):
            x = rect.left() + (22 + i*30)*sc
            if x < rect.right() - 20*sc:
                g = QLinearGradient(x, rect.top(), x+5*sc, rect.top())
                g.setColorAt(0, QColor(255,255,255,0))
                g.setColorAt(0.3, QColor(255,255,255,110))
                g.setColorAt(0.6, QColor(255,255,255,50))
                g.setColorAt(1, QColor(0,0,0,40))
                p.setBrush(QBrush(g)); p.setPen(Qt.NoPen)
                p.drawRoundedRect(QRectF(x, rect.top()+6*sc, 4*sc, rect.height()-12*sc), 1, 1)
        p.setPen(_mk_pen(QColor(255,255,255,90), 1.2))
        p.drawLine(QPointF(rect.left()+14*sc, rect.top()+1),
                   QPointF(rect.right()-14*sc, rect.top()+1))
        p.setPen(_mk_pen(QColor(0,0,0,150), 1.2))
        p.drawLine(QPointF(rect.left()+14*sc, rect.bottom()-1),
                   QPointF(rect.right()-14*sc, rect.bottom()-1))

    def _draw_stator(self, p, cx, cy, sc):
        sw, sh = int(165*sc), int(138*sc)
        rect = QRectF(cx - sw//2 - 50*sc, cy - sh//2, sw, sh)
        p.setBrush(_metal_brush_v(rect, _Mat.STEEL_D, roughness=0.35))
        p.setPen(_mk_pen(QColor("#28303a"), 1))
        p.drawRoundedRect(rect, 7*sc, 7*sc)
        bw, bh = int(26*sc), int(100*sc)
        for bx in [rect.left()+6*sc, rect.left()+34*sc,
                   rect.left()+62*sc, rect.left()+90*sc]:
            br = QRectF(bx, cy - bh//2, bw, bh)
            g = QLinearGradient(br.topLeft(), br.topRight())
            g.setColorAt(0, QColor("#6a3810")); g.setColorAt(0.3, QColor("#c87832"))
            g.setColorAt(0.5, QColor("#e8a050")); g.setColorAt(0.7, QColor("#c87832"))
            g.setColorAt(1, QColor("#6a3810"))
            p.setBrush(QBrush(g)); p.setPen(_mk_pen(QColor("#8a5020"), 0.6))
            p.drawRoundedRect(br, 3*sc, 3*sc)
            p.setPen(_mk_pen(QColor("#d4a017"), 0.8))
            for row in range(8):
                fy = br.top() + (8 + row*11)*sc
                p.drawLine(QPointF(br.left()+2*sc, fy), QPointF(br.right()-2*sc, fy))

    def _draw_shaft(self, p, cx, cy, sc):
        shaft = QRectF(cx-155*sc, cy-5*sc, 210*sc, 10*sc)
        g = QLinearGradient(shaft.topLeft(), shaft.bottomLeft())
        g.setColorAt(0, QColor("#e0e8f0")); g.setColorAt(0.3, QColor("#b0bcc8"))
        g.setColorAt(0.7, QColor("#8090a0")); g.setColorAt(1, QColor("#506070"))
        p.setBrush(QBrush(g)); p.setPen(_mk_pen(QColor("#405060"), 0.8))
        p.drawRoundedRect(shaft, 5*sc, 5*sc)
        p.setPen(_mk_pen(QColor(255,255,255,160), 1))
        p.drawLine(QPointF(shaft.left()+8*sc, shaft.top()+2*sc),
                   QPointF(shaft.right()-8*sc, shaft.top()+2*sc))

    def _draw_worm(self, p, cx, cy, sc):
        cr = QRectF(cx+165*sc, cy-82*sc, 118*sc, 164*sc)
        _drop_shadow(p, cr, blur=6, offset=(5, 6))
        p.setBrush(_metal_brush_v(cr, _Mat.ALU, roughness=0.22))
        p.setPen(_mk_pen(QColor("#4a5a6a"), 1.2))
        p.drawRoundedRect(cr, 11*sc, 11*sc)
        wr = QRectF(cx+48*sc, cy-18*sc, 120*sc, 36*sc)
        g = QLinearGradient(wr.topLeft(), wr.bottomLeft())
        g.setColorAt(0, QColor("#d0d8e0")); g.setColorAt(0.2, QColor("#a0aab4"))
        g.setColorAt(0.5, QColor("#707880")); g.setColorAt(0.8, QColor("#909aa4"))
        g.setColorAt(1, QColor("#404850"))
        p.setBrush(QBrush(g)); p.setPen(_mk_pen(QColor("#506070"), 0.8))
        p.drawRoundedRect(wr, 8*sc, 8*sc)
        off = (self._angle * 0.3) % 10
        for i in range(14):
            x = wr.left() + (7 + i*8)*sc + off*sc
            if wr.left() < x < wr.right()-4*sc:
                p.setPen(_mk_pen(QColor(0,0,0,60), 1.5))
                p.drawLine(QPointF(x, wr.top()+3*sc), QPointF(x+3*sc, wr.bottom()-3*sc))
                p.setPen(_mk_pen(QColor(255,255,255,80), 0.8))
                p.drawLine(QPointF(x+sc, wr.top()+3*sc), QPointF(x+4*sc, wr.bottom()-3*sc))

    def _draw_crown(self, p, gcx, cy, sc):
        r_out = int(60*sc); r_in = int(45*sc); r_base = int(50*sc)
        n_teeth = 24
        p.save()
        p.translate(gcx, cy)
        p.rotate(self._crank_angle * 0.8)
        for blur_r, alpha in [(r_out+10, 35), (r_out+6, 45), (r_out+3, 55)]:
            sg = QRadialGradient(5*sc, 7*sc, blur_r)
            sg.setColorAt(0.6, QColor(0,0,0,alpha)); sg.setColorAt(1.0, QColor(0,0,0,0))
            p.setPen(Qt.NoPen); p.setBrush(QBrush(sg))
            p.drawEllipse(QPointF(5*sc, 7*sc), blur_r, blur_r)
        path = QPainterPath()
        tooth_half = math.radians(360 / (n_teeth * 2) * 0.38)
        gap_half   = math.radians(360 / (n_teeth * 2) * 0.62)
        for i in range(n_teeth):
            ac = math.radians(i * 360 / n_teeth)
            a_rl = ac - gap_half; a_sl = ac - tooth_half
            a_sr = ac + tooth_half; a_rr = ac + gap_half
            pts = [(math.cos(a_rl)*r_base, math.sin(a_rl)*r_base),
                   (math.cos(a_sl)*r_out,  math.sin(a_sl)*r_out),
                   (math.cos(a_sr)*r_out,  math.sin(a_sr)*r_out),
                   (math.cos(a_rr)*r_base, math.sin(a_rr)*r_base)]
            if i == 0: path.moveTo(*pts[0])
            for pt in pts: path.lineTo(*pt)
        for i in range(n_teeth):
            ac = math.radians(i * 360 / n_teeth)
            a_rr = ac + gap_half
            a_rl_next = math.radians((i+1) * 360 / n_teeth) - gap_half
            path.arcTo(-r_in, -r_in, r_in*2, r_in*2,
                       -math.degrees(a_rr), -math.degrees(a_rl_next - a_rr))
        path.closeSubpath()
        bg = QRadialGradient(-r_out*0.45, -r_out*0.5, r_out*1.8)
        bg.setColorAt(0.00, QColor("#fce08a")); bg.setColorAt(0.08, QColor("#e8b840"))
        bg.setColorAt(0.30, QColor("#c88c28")); bg.setColorAt(0.58, QColor("#9a6418"))
        bg.setColorAt(0.82, QColor("#6a3e0a")); bg.setColorAt(1.00, QColor("#3c1e04"))
        p.setBrush(QBrush(bg)); p.setPen(_mk_pen(QColor("#5a3010"), 0.8))
        p.drawPath(path)
        bg2 = QRadialGradient(-6*sc, -7*sc, 20*sc)
        bg2.setColorAt(0.0, QColor("#e8f0f8")); bg2.setColorAt(0.3, QColor("#b0c0d2"))
        bg2.setColorAt(0.6, QColor("#708090")); bg2.setColorAt(1.0, QColor("#304050"))
        p.setBrush(QBrush(bg2)); p.setPen(_mk_pen(QColor("#c0d4ea"), 1.5))
        p.drawEllipse(QPointF(0, 0), 18*sc, 18*sc)
        p.restore()

    def _draw_rotor(self, p, rx, ry, sc):
        p.save()
        p.translate(rx, ry)
        p.rotate(self._angle)
        r = int(42*sc)
        p.setPen(Qt.NoPen); p.setBrush(QBrush(QColor(0,0,0,60)))
        p.drawEllipse(QPointF(4*sc, 5*sc), r+2, r+2)
        rg = QRadialGradient(-r*0.38, -r*0.42, r*1.4)
        rg.setColorAt(0.0, QColor("#d8e4f0")); rg.setColorAt(0.15, QColor("#a0b0c4"))
        rg.setColorAt(0.45, QColor("#687888")); rg.setColorAt(0.75, QColor("#384858"))
        rg.setColorAt(1.0, QColor("#1a2430"))
        p.setBrush(QBrush(rg)); p.setPen(_mk_pen(QColor("#2a3848"), 1.2))
        p.drawEllipse(QPointF(0, 0), r, r)
        for i in range(12):
            a = math.radians(i * 30)
            x1 = math.cos(a)*(r-14*sc); y1 = math.sin(a)*(r-14*sc)
            x2 = math.cos(a)*r;         y2 = math.sin(a)*r
            p.setPen(_mk_pen(QColor("#0e1620"), 1.8))
            p.drawLine(QPointF(x1,y1), QPointF(x2,y2))
        for i in range(6):
            a = math.radians(i * 60)
            path = QPainterPath()
            path.moveTo(0, 0)
            path.lineTo(math.cos(a)*32*sc, math.sin(a)*32*sc)
            p.setPen(_mk_pen(QColor("#b07030"), 2.5)); p.drawPath(path)
            p.setPen(_mk_pen(QColor("#e8b060"), 1.0)); p.drawPath(path)
        p.setBrush(Qt.NoBrush)
        p.setPen(_mk_pen(QColor(220,240,255,120), 2.5))
        rr = r-5*sc
        p.drawArc(QRectF(-rr,-rr,rr*2,rr*2), 105*16, 65*16)
        ag = QRadialGradient(-3*sc, -3.5*sc, 8.5*sc)
        ag.setColorAt(0.0, QColor("#f0f8ff")); ag.setColorAt(0.3, QColor("#c0d4e8"))
        ag.setColorAt(0.7, QColor("#708090")); ag.setColorAt(1.0, QColor("#304050"))
        p.setBrush(QBrush(ag)); p.setPen(_mk_pen(QColor("#c0d8f0"), 1.2))
        p.drawEllipse(QPointF(0,0), 8.5*sc, 8.5*sc)
        p.restore()

    def _draw_crank(self, p, gcx, cy, sc):
        on = self._state == "ON"
        pin_dist  = 32*sc
        pin_angle = math.radians(self._crank_angle * 0.8)
        pin_x = gcx + math.cos(pin_angle)*pin_dist
        pin_y = cy  + math.sin(pin_angle)*pin_dist
        out_x = gcx + 110*sc; out_y = cy
        cp = QPainterPath(); cp.moveTo(pin_x,pin_y); cp.lineTo(out_x,out_y)
        p.setPen(_mk_pen(QColor("#b07828"), 5*sc)); p.drawPath(cp)
        p.setPen(_mk_pen(QColor("#e0b040"), 2.5*sc)); p.drawPath(cp)
        p.setPen(_mk_pen(QColor("#808070"), 6*sc))
        p.drawLine(QPointF(gcx,cy), QPointF(pin_x,pin_y))
        p.setPen(_mk_pen(QColor("#d0e0f0"), 2.5*sc))
        p.drawLine(QPointF(gcx,cy), QPointF(pin_x,pin_y))
        p.setBrush(_sphere_brush(pin_x, pin_y, 9*sc, _Mat.STEEL, roughness=0.08))
        p.setPen(_mk_pen(QColor("#c0d0e0"), 1.2))
        p.drawEllipse(QPointF(pin_x,pin_y), 9*sc, 9*sc)
        p.setBrush(_sphere_brush(out_x, out_y, 13*sc, _Mat.GOLD_W, roughness=0.06))
        p.setPen(_mk_pen(QColor("#d4b030"), 1.5))
        p.drawEllipse(QPointF(out_x,out_y), 13*sc, 13*sc)
        if on:
            col = _Mat.GREEN if self._speed == "Speed2" else _Mat.AMBER
            p.setBrush(Qt.NoBrush); p.setPen(_mk_pen(col, 2))
            p.drawArc(QRectF(gcx-68*sc, cy-68*sc, 136*sc, 136*sc), 30*16, -200*16)

    def _draw_header(self, p, W):
        on   = self._state == "ON"
        spd2 = on and self._speed == "Speed2"
        if spd2:   accent = QColor(A_TEAL);     txt = f"▲▲  {self._id}  SPEED 2"
        elif on:   accent = QColor("#007ACC");   txt = f"▲   {self._id}  SPEED 1"
        else:      accent = QColor("#007ACC");   txt = f"◼   {self._id}  STANDBY"
        TOP_H = 26
        # Fond sombre KPIT unifié
        hdr_g = QLinearGradient(0, 0, 0, TOP_H)
        hdr_g.setColorAt(0, QColor("#0F1A0A"))
        hdr_g.setColorAt(1, QColor("#070A04"))
        p.fillRect(0, 0, W, TOP_H, QBrush(hdr_g))
        # Barre colorée gauche 3px
        bar_col = QColor("#007ACC") if spd2 else (QColor("#007ACC") if on else QColor("#007ACC"))
        p.fillRect(0, 0, 3, TOP_H, QBrush(bar_col))
        # Séparateur bas noir
        p.setPen(QPen(QColor("#000000"), 2))
        p.drawLine(0, TOP_H - 1, W, TOP_H - 1)
        # Texte
        p.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
        p.setPen(QPen(accent))
        p.drawText(10, 0, W - 14, TOP_H, Qt.AlignmentFlag.AlignCenter, txt)

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        TOP_H = 26
        sc = min(W / 620.0, (H - TOP_H) / 290.0)
        sc = max(0.25, min(sc, 1.0))
        cx  = W // 2 - int(60 * sc)
        cy  = TOP_H + (H - TOP_H) // 2
        gcx = cx + int(228 * sc)
        _draw_photo_bg(p, W, H)
        self._draw_header(p, W)
        self._draw_carter(p, cx, cy, sc)
        self._draw_stator(p, cx, cy, sc)
        self._draw_shaft(p, cx, cy, sc)
        self._draw_worm(p, cx, cy, sc)
        self._draw_crown(p, gcx, cy, sc)
        self._draw_crank(p, gcx, cy, sc)
        self._draw_rotor(p, cx + int(58*sc), cy, sc)
        # Contour unique professionnel
        p.setPen(QPen(QColor("#007ACC"), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(1, 1, W-2, H-2, 6, 6)


# ══════════════════════════════════════════════════════════════════════
#  PUMP WIDGET — Design photoréaliste (PumpWidget de car_comodo.py)
#  Backend inchangé : set_state(state, current, fault), set_rain()
# ══════════════════════════════════════════════════════════════════════
class PumpWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._state          = "OFF"
        self._current        = 0.0
        self._fault          = False
        self._rain           = 0
        self._impeller_angle = 0.0
        self._flow_offset    = 0.0
        self._t = QTimer()
        self._t.timeout.connect(self._tick)
        self._t.start(33)
        self.setMinimumSize(280, 180)

    # ── API backend inchangée ─────────────────────────────────────────
    def set_state(self, state: str, current: float = 0.0, fault: bool = False) -> None:
        self._state   = state
        self._current = current
        self._fault   = fault
        self.update()

    def set_rain(self, pct: int) -> None:
        self._rain = pct

    def _tick(self) -> None:
        running = self._state in ("FORWARD", "BACKWARD")
        if running:
            d = 1 if self._state == "FORWARD" else -1
            self._impeller_angle = (self._impeller_angle + 5*d) % 360
            self._flow_offset    = (self._flow_offset + 3) % 40
            self.update()
        elif abs(self._impeller_angle % 360) > 1.5:
            self._impeller_angle = (self._impeller_angle + 1.2) % 360
            self.update()

    def _draw_reservoir(self, p, W, H, sc, TOP_H):
        rx = int(20*sc); ry = TOP_H + int(30*sc)
        rw = int(80*sc); rh = int(min(H - TOP_H - 40*sc, 180*sc))
        rect = QRectF(rx, ry, rw, rh)
        _drop_shadow(p, rect, blur=6, offset=(4, 5))
        g = QLinearGradient(rx, ry, rx+rw, ry)
        g.setColorAt(0, QColor(20, 60, 100, 230)); g.setColorAt(0.4, QColor(30, 80, 130, 200))
        g.setColorAt(0.7, QColor(40, 90, 140, 180)); g.setColorAt(1, QColor(20, 50, 90, 230))
        p.setBrush(QBrush(g)); p.setPen(_mk_pen(QColor("#3a7aaa"), 2))
        p.drawRoundedRect(rect, 10*sc, 10*sc)
        fill_h = int(rh * 0.70)
        lg = QLinearGradient(rx, ry+rh-fill_h, rx+rw, ry+rh)
        lg.setColorAt(0, QColor("#0a3a6a")); lg.setColorAt(0.5, QColor("#1a6aaa"))
        lg.setColorAt(1, QColor("#2a8ad4"))
        p.setBrush(QBrush(lg)); p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(rx+3*sc, ry+rh-fill_h+1, rw-6*sc, fill_h-4), 7*sc, 7*sc)
        p.setPen(_mk_pen(QColor("#60b4f0"), 1.5))
        wave = QPainterPath()
        wy = ry + rh - fill_h + 5*sc
        wave.moveTo(rx+3*sc, wy)
        step = max(int(12*sc), 6)
        for wx in range(0, int(rw), step):
            wave.lineTo(rx+wx+step//2, wy-4*sc)
            wave.lineTo(rx+wx+step, wy)
        p.drawPath(wave)

    def _draw_motor_dc(self, p, px, py, sc):
        mx = px - int(105*sc); my = py; mr = int(45*sc)
        p.setPen(Qt.NoPen); p.setBrush(QBrush(QColor(0,0,0,80)))
        p.drawEllipse(QPointF(mx+5*sc, my+6*sc), mr, mr*0.9)
        p.setBrush(_sphere_brush(mx, my, mr, _Mat.STEEL_D, roughness=0.28))
        p.setPen(_mk_pen(QColor("#4a5a6a"), 1.5))
        p.drawEllipse(QPointF(mx, my), mr, mr)
        # 4 bobines
        for i in range(4):
            a = math.radians(i*90+45)
            sx = mx + math.cos(a)*32*sc; sy = my + math.sin(a)*32*sc
            p.setBrush(_sphere_brush(sx, sy, 11*sc, _Mat.COPPER, roughness=0.35))
            p.setPen(_mk_pen(QColor("#8a5020"), 0.8))
            p.drawEllipse(QPointF(sx,sy), 11*sc, 11*sc)
        running = self._state in ("FORWARD", "BACKWARD")
        if running:
            col = _Mat.GREEN if self._state == "FORWARD" else _Mat.AMBER
            p.setBrush(Qt.NoBrush); p.setPen(_mk_pen(col, 2.5))
            span = -280 if self._state == "FORWARD" else 280
            p.drawArc(QRectF(mx-mr*1.2, my-mr*1.2, mr*2.4, mr*2.4), 0, span*16)

    def _draw_pump_body(self, p, px, py, sc):
        vol_path = QPainterPath()
        for i in range(361):
            a = math.radians(i)
            r = (44 + i/360*34)*sc
            x = px + math.cos(a)*r; y = py + math.sin(a)*r
            if i == 0: vol_path.moveTo(x, y)
            else:       vol_path.lineTo(x, y)
        p.setPen(_mk_pen(QColor("#3a4a5a"), 20*sc)); p.drawPath(vol_path)
        p.setPen(_mk_pen(QColor("#283040"), 15*sc)); p.drawPath(vol_path)
        p.setPen(_mk_pen(QColor("#8090a0"), 2));     p.drawPath(vol_path)
        asp = QRadialGradient(px, py, 38*sc)
        asp.setColorAt(0, QColor("#060e1a")); asp.setColorAt(0.5, QColor("#0a1828"))
        asp.setColorAt(1, QColor("#142030"))
        p.setBrush(QBrush(asp)); p.setPen(_mk_pen(QColor("#2a3848"), 1))
        p.drawEllipse(QPointF(px,py), 38*sc, 38*sc)
        out_rect = QRectF(px+72*sc, py-16*sc, 34*sc, 32*sc)
        p.setBrush(_metal_brush_h(out_rect, _Mat.ALU, roughness=0.25))
        p.setPen(_mk_pen(QColor("#4a5a6a"), 1.5))
        p.drawRoundedRect(out_rect, 3*sc, 3*sc)

    def _draw_impeller(self, p, px, py, sc):
        running = self._state in ("FORWARD", "BACKWARD")
        d = 1 if self._state == "FORWARD" else (-1 if self._state == "BACKWARD" else 1)
        p.save(); p.translate(px, py); p.rotate(self._impeller_angle)
        r_hub = 12*sc; r_blade = 30*sc
        for i in range(6):
            a = math.radians(i*60)
            blade = QPainterPath()
            blade.moveTo(math.cos(a)*r_hub, math.sin(a)*r_hub)
            ca = a + math.radians(35*d)
            ex = math.cos(a+math.radians(18))*r_blade
            ey = math.sin(a+math.radians(18))*r_blade
            blade.cubicTo(math.cos(ca)*20*sc, math.sin(ca)*20*sc,
                          math.cos(ca)*20*sc, math.sin(ca)*20*sc, ex, ey)
            g = QLinearGradient(0, 0, ex, ey)
            g.setColorAt(0, QColor("#4a5a6a")); g.setColorAt(0.5, QColor("#8090a8"))
            g.setColorAt(1, QColor("#c0d0e0"))
            p.setBrush(QBrush(g)); p.setPen(_mk_pen(QColor("#8090a0"), 1.5))
            p.drawPath(blade)
        p.setBrush(_sphere_brush(0, 0, r_hub, _Mat.STEEL, roughness=0.12))
        p.setPen(_mk_pen(QColor("#c0d0e0"), 1.5))
        p.drawEllipse(QPointF(0,0), r_hub, r_hub)
        p.restore()

    def _draw_pipes(self, p, px, py, sc, W, H, TOP_H):
        running = self._state in ("FORWARD", "BACKWARD")
        asp_from = QPointF(px - int(170*sc), py + int(20*sc))
        asp_to   = QPointF(px - int(38*sc), py)
        for w, col in [(10*sc, QColor("#3a3a48")), (6*sc, QColor("#5a5a68")),
                        (2, QColor("#9090a0"))]:
            p.setPen(_mk_pen(col, w)); p.drawLine(asp_from, asp_to)
        out_mid = QPointF(px+int(106*sc), py)
        out_up  = QPointF(px+int(106*sc), py-int(70*sc))
        out_l   = QPointF(px+int(32*sc),  py-int(70*sc))
        out_r   = QPointF(px+int(210*sc), py-int(70*sc))
        for w, col in [(10*sc, QColor("#3a3a48")), (6*sc, QColor("#5a5a68")),
                        (2, QColor("#9090a0"))]:
            p.setPen(_mk_pen(col, w))
            p.drawLine(out_mid, out_up); p.drawLine(out_up, out_l); p.drawLine(out_up, out_r)
        if running:
            p.setPen(_mk_pen(QColor(30,120,200,160), 4))
            p.drawLine(asp_from, asp_to)
            dest = out_r if self._state == "FORWARD" else out_l
            p.setPen(_mk_pen(QColor(30,120,200,120), 4))
            p.drawLine(out_mid, out_up); p.drawLine(out_up, dest)
            p.setPen(_mk_pen(QColor(100,180,240,200), 2))
            for i in range(5):
                frac = (self._flow_offset + i*8) % 40 / 40
                dx = asp_to.x() - asp_from.x(); dy = asp_to.y() - asp_from.y()
                fx = asp_from.x() + dx*frac; fy = asp_from.y() + dy*frac
                p.drawEllipse(QPointF(fx, fy), 2, 2)

    def _draw_header(self, p, W):
        running = self._state in ("FORWARD", "BACKWARD")
        fw      = self._state == "FORWARD"
        if running and fw:   accent = QColor("#007ACC");   txt = "PUMP  ▶  FORWARD"
        elif running:        accent = QColor(A_ORANGE);    txt = "PUMP  ◀  BACKWARD"
        else:                accent = QColor("#007ACC");   txt = "PUMP  ◼  OFF"
        TOP_H = 26
        hdr_g = QLinearGradient(0, 0, 0, TOP_H)
        hdr_g.setColorAt(0, QColor("#0F1A0A"))
        hdr_g.setColorAt(1, QColor("#070A04"))
        p.fillRect(0, 0, W, TOP_H, QBrush(hdr_g))
        # Barre colorée gauche 3px
        bar_col = QColor("#007ACC") if running and fw else (
                  QColor(A_ORANGE) if running else QColor("#3A5A20"))
        p.fillRect(0, 0, 3, TOP_H, QBrush(bar_col))
        # Séparateur bas noir
        p.setPen(QPen(QColor("#000000"), 2))
        p.drawLine(0, TOP_H - 1, W, TOP_H - 1)
        # Texte état
        p.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
        p.setPen(QPen(accent))
        p.drawText(10, 0, W - 20, TOP_H, Qt.AlignmentFlag.AlignCenter, txt)

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        TOP_H = 26
        sc = min(W / 540.0, (H - TOP_H) / 280.0)
        sc = max(0.22, min(sc, 1.0))
        px = int(W * 0.53); py = TOP_H + (H - TOP_H) // 2
        _draw_photo_bg(p, W, H)
        self._draw_header(p, W)
        self._draw_reservoir(p, W, H, sc, TOP_H)
        self._draw_pipes(p, px, py, sc, W, H, TOP_H)
        self._draw_motor_dc(p, px, py, sc)
        self._draw_pump_body(p, px, py, sc)
        self._draw_impeller(p, px, py, sc)
        if self._fault:
            p.setBrush(QBrush(QColor(192, 57, 43, 80))); p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(px,py), 45*sc, 45*sc)
            p.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
            p.setPen(QPen(QColor(A_RED)))
            p.drawText(int(px-45*sc), int(py-10), int(90*sc), 20,
                       Qt.AlignmentFlag.AlignCenter, "⚠ FAULT")
        # Contour unique professionnel
        p.setPen(QPen(QColor("#007ACC"), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(1, 1, W-2, H-2, 6, 6)


# ═══════════════════════════════════════════════════════════
#  ARC GAUGE WIDGET — inchangé
# ═══════════════════════════════════════════════════════════
class ArcGaugeWidget(QWidget):
    """Gauge arc style KPIT — fond vert photoréaliste avec grille, thème unifié."""
    def __init__(self, max_val: float = 1.5, unit: str = "A", parent=None):
        super().__init__(parent)
        self._val   = 0.0
        self._max   = max_val
        self._unit  = unit
        self._fault = False
        self.setFixedHeight(120)
        self.setMinimumWidth(120)

    def set_value(self, val: float, fault: bool = False) -> None:
        self._val = val; self._fault = fault; self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        # ── Fond vert KPIT + grille (thème unifié) ───────────────────────
        _draw_photo_bg(p, W, H)

        cx = W // 2
        cy = int(H * 0.68)
        R  = min(cx - 10, cy - 8, 40)

        pct   = min(self._val / max(self._max, 1e-9), 1.0)
        START = math.radians(218)
        SPAN  = math.radians(244)

        # ── Anneau de fond gravé (vert foncé KPIT) ───────────────────────
        p.setPen(QPen(QColor(200, 210, 225, 60), R*0.55,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        p.drawArc(QRectF(cx-R, cy-R, R*2, R*2),
                  int(-math.degrees(START)*16), int(-math.degrees(SPAN)*16))

        # Piste track vert olive profond
        p.setPen(QPen(QColor("#E2E8F0"), int(R*0.32),
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(QRectF(cx-R, cy-R, R*2, R*2),
                  int(-math.degrees(START)*16), int(-math.degrees(SPAN)*16))

        # ── Zones colorées (harmonisées palette KPIT) ────────────────────
        for z0, z1, c_hex, alpha in [
            (0.00, 0.50, "#8DC63F", 80),    # vert KPIT
            (0.50, 0.75, "#D87B00", 80),    # orange chaud
            (0.75, 1.00, "#C82828", 80),    # rouge
        ]:
            zc = QColor(c_hex); zc.setAlpha(alpha)
            p.setPen(QPen(zc, int(R*0.30),
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(QRectF(cx-R, cy-R, R*2, R*2),
                      int(-math.degrees(START + SPAN*z0)*16),
                      int(-math.degrees(SPAN*(z1-z0))*16))

        # ── Arc actif lumineux ───────────────────────────────────────────
        if pct > 0.005:
            if self._fault:
                arc_col = QColor("#FF3A3A")
            elif pct >= 0.75:
                arc_col = QColor("#E03030")
            elif pct >= 0.50:
                arc_col = QColor("#E07800")
            else:
                arc_col = QColor("#8DC63F")    # vert KPIT vif

            # Halo glow
            glow = QColor(arc_col); glow.setAlpha(55)
            p.setPen(QPen(glow, int(R*0.46),
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(QRectF(cx-R, cy-R, R*2, R*2),
                      int(-math.degrees(START)*16),
                      int(-math.degrees(SPAN*pct)*16))
            # Arc principal
            p.setPen(QPen(arc_col, int(R*0.26),
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(QRectF(cx-R, cy-R, R*2, R*2),
                      int(-math.degrees(START)*16),
                      int(-math.degrees(SPAN*pct)*16))
            # Reflet blanc sur bord avant
            tip_a = START + SPAN * pct
            tip_x = cx + R * math.cos(tip_a)
            tip_y = cy + R * math.sin(tip_a)
            tipg = QRadialGradient(tip_x, tip_y, int(R*0.22))
            tipg.setColorAt(0, QColor(255, 255, 255, 160))
            tipg.setColorAt(1, QColor(255, 255, 255, 0))
            p.setBrush(QBrush(tipg)); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(int(tip_x - R*0.22), int(tip_y - R*0.22),
                          int(R*0.44), int(R*0.44))

        # ── Graduations vert olive ────────────────────────────────────────
        for i in range(9):
            ta = START + SPAN * i / 8
            is_major = (i % 2 == 0)
            outer = R + 4; inner = R - (5 if is_major else 3)
            lw = 1.5 if is_major else 0.8
            lc = QColor(100, 120, 150, 200) if is_major else QColor(150, 165, 185, 120)
            p.setPen(QPen(lc, lw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(
                int(cx + inner*math.cos(ta)), int(cy + inner*math.sin(ta)),
                int(cx + outer*math.cos(ta)), int(cy + outer*math.sin(ta))
            )

        # ── Aiguille métallique chromée ──────────────────────────────────
        ang = START + SPAN * pct
        needle_len = R - 7
        nx_tip = cx + needle_len * math.cos(ang)
        ny_tip = cy + needle_len * math.sin(ang)
        p.setPen(QPen(QColor(0, 0, 0, 80), 3,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(cx+1, cy+1, int(nx_tip+1), int(ny_tip+1))
        ndl_g = QLinearGradient(cx, cy, int(nx_tip), int(ny_tip))
        ndl_g.setColorAt(0,   QColor("#94A3B8"))
        ndl_g.setColorAt(0.4, QColor("#CBD5E1"))
        ndl_g.setColorAt(1,   QColor("#475569"))
        p.setPen(QPen(QBrush(ndl_g), 2.5,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(cx, cy, int(nx_tip), int(ny_tip))

        # Pivot central sphère acier
        p.setBrush(_rc_sphere_brush(cx, cy, 7, _RcMat.STEEL, roughness=0.06))
        p.setPen(_rc_mk_pen(QColor("#94A3B8"), 1.2))
        p.drawEllipse(cx-7, cy-7, 14, 14)
        p.setBrush(_rc_sphere_brush(cx, cy, 3, _RcMat.STEEL, roughness=0.04))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(cx-3, cy-3, 6, 6)

        # ── Affichage valeur — LCD vert KPIT ─────────────────────────────
        if self._fault:
            val_col = QColor("#E03030")
        elif pct >= 0.75:
            val_col = QColor("#E04040")
        elif pct >= 0.50:
            val_col = QColor("#D07800")
        else:
            val_col = QColor("#3A6A1A")   # vert foncé lisible sur fond clair

        # Fond LCD vert très foncé, cohérent avec le fond général
        lcd_r = QRectF(cx - R*0.72, cy - R - 2, R*1.44, R*0.58)
        lcd_bg = QLinearGradient(lcd_r.topLeft(), lcd_r.bottomLeft())
        lcd_bg.setColorAt(0, QColor("#F1F5F9")); lcd_bg.setColorAt(1, QColor("#E2E8F0"))
        p.setBrush(QBrush(lcd_bg))
        p.setPen(QPen(QColor("#007ACC"), 1))
        p.drawRoundedRect(lcd_r, 3, 3)
        # Reflet vitre
        p.setBrush(QBrush(QColor(255, 255, 255, 12)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(lcd_r.x()+2, lcd_r.y()+2,
                                 lcd_r.width()-4, lcd_r.height()*0.45), 2, 2)
        # Valeur
        p.setFont(QFont(FONT_MONO, 10, QFont.Weight.Bold))
        display_col = QColor("#1E40AF") if pct < 0.50 else val_col
        p.setPen(QPen(display_col))
        p.drawText(lcd_r.toRect(), Qt.AlignmentFlag.AlignCenter,
                   f"{self._val:.3f}")

        # Unité — vert olive cohérent
        p.setFont(QFont(FONT_MONO, 7))
        p.setPen(QPen(QColor("#64748B")))
        p.drawText(0, int(cy + R*0.22), W, 14,
                   Qt.AlignmentFlag.AlignCenter, self._unit)

        # Cadre extérieur biseauté vert KPIT
        # Contour unique professionnel
        p.setPen(QPen(QColor("#007ACC"), 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(1, 1, W-2, H-2, 6, 6)


# ═══════════════════════════════════════════════════════════
#  SPARKLINE WIDGET — thème vert KPIT unifié
# ═══════════════════════════════════════════════════════════
class SparklineWidget(QWidget):
    def __init__(self, max_val: float = 1.5, color: str = "#4DB8FF",
                 max_pts: int = 80, parent=None):
        super().__init__(parent)
        self._max  = max_val
        self._col  = QColor(color)
        self._data : deque = deque(maxlen=max_pts)
        self.setFixedHeight(42)

    def push(self, val: float) -> None:
        self._data.append(val); self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        # ── Fond vert KPIT + grille (thème unifié) ───────────────────────
        _draw_photo_bg(p, W, H)

        # Ligne zéro vert foncé
        zy = H - 4
        p.setPen(QPen(QColor(30, 80, 10, 80), 0.8, Qt.PenStyle.DashLine))
        p.drawLine(0, zy, W, zy)

        # Couleur courbe : si bleu par défaut → on la réharmonise en vert KPIT
        # Si l'appelant a passé une couleur spécifique non-bleue, on la conserve
        is_default_blue = (self._col.blue() > 150 and self._col.red() < 100)
        draw_col = QColor("#3B82F6") if is_default_blue else self._col

        if len(self._data) < 2:
            p.setPen(QPen(QColor(draw_col.red(), draw_col.green(), draw_col.blue(), 50), 1))
            p.drawLine(0, zy, W, zy)
        else:
            data = list(self._data); n = len(data); step = W / (n-1)

            # ── Tracé de la courbe ───────────────────────────────────────
            path = QPainterPath()
            for i, v in enumerate(data):
                x = i * step
                y = H - 4 - max(0, min(v/self._max, 1.0)) * (H - 8)
                if i == 0: path.moveTo(x, y)
                else:      path.lineTo(x, y)

            # Fill area dégradé vert
            fp = QPainterPath(path)
            fp.lineTo((n-1)*step, H); fp.lineTo(0, H); fp.closeSubpath()
            fill_g = QLinearGradient(0, 0, 0, H)
            fc_top = QColor(draw_col); fc_top.setAlpha(80)
            fc_bot = QColor(draw_col); fc_bot.setAlpha(8)
            fill_g.setColorAt(0, fc_top); fill_g.setColorAt(1, fc_bot)
            p.fillPath(fp, QBrush(fill_g))

            # Halo glow
            glow_c = QColor(draw_col); glow_c.setAlpha(50)
            p.setPen(QPen(glow_c, 4.5, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush); p.drawPath(path)
            # Ligne principale
            p.setPen(QPen(draw_col, 2.0, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPath(path)
            # Reflet brillant dessus
            bright = QColor(draw_col).lighter(150); bright.setAlpha(100)
            p.setPen(QPen(bright, 0.7, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPath(path)

            # Point actif
            lx = (n-1)*step
            ly = H - 4 - max(0, min(data[-1]/self._max, 1.0)) * (H - 8)
            halo = QRadialGradient(lx, ly, 7)
            halo.setColorAt(0, QColor(draw_col.red(), draw_col.green(), draw_col.blue(), 140))
            halo.setColorAt(1, QColor(draw_col.red(), draw_col.green(), draw_col.blue(), 0))
            p.setBrush(QBrush(halo)); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(int(lx)-7, int(ly)-7, 14, 14)
            p.setBrush(QBrush(draw_col)); p.setPen(QPen(QColor(255, 255, 255, 160), 1))
            p.drawEllipse(int(lx)-3, int(ly)-3, 6, 6)

        # Cadre biseauté vert KPIT
        # Contour unique professionnel
        p.setPen(QPen(QColor("#007ACC"), 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(0, 0, W-1, H-1, 4, 4)


# ═══════════════════════════════════════════════════════════
#  WINDSHIELD WIDGET — inchangé
# ═══════════════════════════════════════════════════════════
class WindshieldWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._op             = 0
        self._angle          = -60.0
        self._dir            = 1
        self._front_motor_on = False
        self._rest_contact   = False
        self._blade_cycles   = 0
        self._bcm_state      = "OFF"
        self._returning      = False
        self._cam_angle      = 0.0          # animation came rest contact
        self._t = QTimer()
        self._t.timeout.connect(self._tick)
        self._t.start(20)
        self.setMinimumSize(280, 200)       # un peu plus haut pour le panneau RC

    def set_op(self, op: int) -> None:
        self._op = op

    def set_bcm_state(self, front_motor_on: bool, rest_contact_raw: bool,
                      blade_cycles: int, bcm_state: str, op: int) -> None:
        prev_motor = self._front_motor_on
        self._front_motor_on = front_motor_on
        self._rest_contact   = rest_contact_raw
        self._blade_cycles   = blade_cycles
        self._bcm_state      = bcm_state
        self._op             = op
        if prev_motor and not front_motor_on:
            self._returning = True

    def _tick(self) -> None:
        if self._front_motor_on:
            self._returning = False
            spd = {"SPEED1": 1.8, "SPEED2": 3.5, "TOUCH": 2.0,
                   "AUTO": 2.2, "WASH_FRONT": 2.0}.get(self._bcm_state, 2.0)
            self._angle += spd * self._dir
            if self._angle >= 60:  self._angle = 60.0;  self._dir = -1
            elif self._angle <= -60: self._angle = -60.0; self._dir = 1
        elif self._returning:
            if self._angle > -60.0: self._angle = max(-60.0, self._angle - 3.0)
            else:                    self._angle = -60.0; self._returning = False
        # Animation came : tourne doucement quand moteur actif, stable sinon
        if self._front_motor_on:
            self._cam_angle = (self._cam_angle + 1.4) % 360
        self.update()

    def _draw_rest_contact_panel(self, p: QPainter, px: int, py: int, scale: float) -> None:
        """Rendu photoréaliste came + lame-ressort style car_comodo.py."""
        r_cam    = int(scale * 22)
        r_relief = int(scale * 27)

        # ── Fond panneau vert KPIT + grille (thème unifié) ──────────────
        panel_w = int(scale * 130)
        panel_h = int(scale * 60)
        panel_r = QRectF(px - 4, py - panel_h // 2 - 4, panel_w, panel_h)
        # Fond vert KPIT localisé
        bg_g = QLinearGradient(panel_r.left(), panel_r.top(),
                               panel_r.left(), panel_r.bottom())
        bg_g.setColorAt(0.0,  QColor(252, 253, 255))
        bg_g.setColorAt(0.45, QColor(248, 250, 253))
        bg_g.setColorAt(1.0,  QColor(244, 247, 252))
        p.setBrush(QBrush(bg_g))
        p.setPen(_rc_mk_pen(QColor("#CBD5E1"), 1.2))
        p.drawRoundedRect(panel_r, 4, 4)
        # Grille technique (même que _draw_grid mais clippée au panneau)
        p.save()
        p.setClipRect(panel_r)
        p.setPen(QPen(QColor(180, 195, 215, 50), 0.4, Qt.PenStyle.SolidLine))
        for xi in range(int(panel_r.x()), int(panel_r.right()), 10):
            p.drawLine(xi, int(panel_r.top()), xi, int(panel_r.bottom()))
        for yi in range(int(panel_r.top()), int(panel_r.bottom()), 10):
            p.drawLine(int(panel_r.left()), yi, int(panel_r.right()), yi)
        p.restore()
        # Vignette bords
        vig = QRadialGradient(panel_r.center().x(), panel_r.center().y(),
                              max(panel_w, panel_h) * 0.65)
        vig.setColorAt(0.55, QColor(0, 0, 0, 0))
        vig.setColorAt(1.0,  QColor(0, 0, 0, 60))
        p.setBrush(QBrush(vig)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(panel_r, 4, 4)

        cx = px + r_cam + 6
        cy = py

        # ── Came rotative ────────────────────────────────────────────────
        p.save()
        p.translate(cx, cy)
        p.rotate(self._cam_angle)

        # Ombre
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(0, 0, 0, 60)))
        p.drawEllipse(QPointF(3, 4), r_cam+1, r_cam+1)

        # Corps came — alu usiné
        cam_g = QRadialGradient(-r_cam*0.4, -r_cam*0.4, r_cam*1.5)
        cam_g.setColorAt(0.0, QColor("#c8d0d8"))
        cam_g.setColorAt(0.3, QColor("#7080a0"))
        cam_g.setColorAt(0.7, QColor("#404a5a"))
        cam_g.setColorAt(1.0, QColor("#252a34"))
        p.setBrush(QBrush(cam_g))
        p.setPen(_rc_mk_pen(QColor("#404a5a"), 1.2))
        p.drawEllipse(QPointF(0, 0), r_cam, r_cam)

        # Relief came
        cam_path = QPainterPath()
        cam_path.moveTo(0, 0)
        for i in range(61):
            a = math.radians(-30 + i)
            cam_path.lineTo(math.cos(a)*r_relief, math.sin(a)*r_relief)
        cam_path.closeSubpath()
        rg2 = QRadialGradient(-8, -r_relief*0.55, r_relief*0.75)
        rg2.setColorAt(0,   QColor("#e0e8f0"))
        rg2.setColorAt(0.4, QColor("#a0b0c0"))
        rg2.setColorAt(1,   QColor("#506070"))
        p.setBrush(QBrush(rg2))
        p.setPen(_rc_mk_pen(QColor("#7090a0"), 0.8))
        p.drawPath(cam_path)

        # Reflet spéculaire
        p.setPen(_rc_mk_pen(QColor(255, 255, 255, 120), 2.0))
        p.drawArc(QRectF(-r_cam+4, -r_cam+4, (r_cam-4)*2, (r_cam-4)*2), 100*16, 70*16)

        # Cannelures axe
        p.setPen(_rc_mk_pen(QColor("#151c28"), 1.5))
        for i in range(6):
            a = math.radians(i * 60)
            p.drawLine(QPointF(math.cos(a)*5, math.sin(a)*5),
                       QPointF(math.cos(a)*10, math.sin(a)*10))

        # Axe central
        p.setBrush(_rc_sphere_brush(0, 0, 6, _RcMat.STEEL, roughness=0.06))
        p.setPen(_rc_mk_pen(QColor("#b0c0d0"), 1.2))
        p.drawEllipse(QPointF(0, 0), 6, 6)
        p.setBrush(_rc_sphere_brush(0, 0, 2, _RcMat.STEEL, roughness=0.04))
        p.drawEllipse(QPointF(0, 0), 2, 2)
        p.restore()

        # ── Lame-ressort + contact ───────────────────────────────────────
        a_mod = self._cam_angle % 360
        on_relief = (a_mod <= 30 or a_mod >= 330)
        contact_color = _RcMat.GREEN if on_relief else _RcMat.STEEL_D

        r_contact = r_relief + 6 if on_relief else r_cam + 6
        touch_a   = math.radians(90)
        touch_x   = cx + math.cos(touch_a) * r_contact
        touch_y   = cy + math.sin(touch_a) * r_contact
        deflect   = -7 if on_relief else 0

        spring_bx = cx + int(scale * 48)
        spring_by = cy - int(scale * 10)

        # PCB
        pcb = QRectF(spring_bx - 4, spring_by - int(scale*20),
                     int(scale*10), int(scale*40))
        _rc_drop_shadow(p, pcb, blur=3, offset=(2, 2))
        p.setBrush(_rc_metal_brush_v(pcb, _RcMat.PCB, roughness=0.9))
        p.setPen(_rc_mk_pen(QColor("#1e4a1e"), 1.0))
        p.drawRoundedRect(pcb, 2, 2)
        p.setPen(_rc_mk_pen(QColor("#c07808"), 1.5))
        p.drawLine(QPointF(spring_bx, spring_by - int(scale*18)),
                   QPointF(spring_bx, spring_by + int(scale*18)))
        p.setBrush(_rc_sphere_brush(spring_bx, spring_by, 3, _RcMat.GOLD_W, roughness=0.2))
        p.setPen(_rc_mk_pen(QColor("#b07008"), 0.6))
        for sy2 in [-int(scale*10), 0, int(scale*10)]:
            p.drawEllipse(QPointF(spring_bx, spring_by + sy2), 3, 3)

        # Lame-ressort
        spring = QPainterPath()
        spring.moveTo(spring_bx, spring_by)
        spring.cubicTo(spring_bx - int(scale*12), spring_by + int(scale*10) + deflect,
                       touch_x + int(scale*12), touch_y - int(scale*6) + deflect,
                       touch_x, touch_y + deflect)
        p.setPen(_rc_mk_pen(QColor(0, 0, 0, 50), 4))
        p.drawPath(spring)
        lame_col = QColor("#c09028") if on_relief else QColor("#808898")
        p.setPen(_rc_mk_pen(lame_col, 2.8))
        p.drawPath(spring)
        p.setPen(_rc_mk_pen(QColor(255, 255, 255, 80), 0.8))
        p.drawPath(spring)

        # Pastille de contact
        p.setBrush(_rc_sphere_brush(touch_x, touch_y + deflect, 5,
                                    contact_color, roughness=0.15))
        p.setPen(_rc_mk_pen(contact_color.darker(120) if hasattr(contact_color, 'darker')
                            else QColor("#283038"), 1.0))
        p.drawEllipse(QPointF(touch_x, touch_y + deflect), 5, 5)

        # Halo si contact actif
        if on_relief:
            p.setPen(_rc_mk_pen(QColor(40, 220, 100, 90), 2.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(touch_x, touch_y + deflect), 9, 9)
            # Glow radial
            halo_g = QRadialGradient(touch_x, touch_y + deflect, 14)
            halo_g.setColorAt(0, QColor(40, 220, 100, 60))
            halo_g.setColorAt(1, QColor(40, 220, 100, 0))
            p.setBrush(QBrush(halo_g)); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(touch_x, touch_y + deflect), 14, 14)

        # Câbles (3 fils)
        wire_cols = [QColor("#cc2222"), QColor("#3333cc"), QColor("#111111")]
        for i, wc in enumerate(wire_cols):
            wy = spring_by - int(scale*9) + i*int(scale*9)
            p.setPen(_rc_mk_pen(wc, 1.8))
            p.drawLine(QPointF(spring_bx + pcb.width(), wy),
                       QPointF(spring_bx + pcb.width() + int(scale*18), wy))
            hl = wc.lighter(140); hl.setAlpha(160)
            p.setPen(_rc_mk_pen(hl, 0.6))
            p.drawLine(QPointF(spring_bx + pcb.width(), wy - 1),
                       QPointF(spring_bx + pcb.width() + int(scale*17), wy - 1))

        # ── Label état — vert KPIT thème ─────────────────────────────────
        state_col = QColor("#2A6010") if on_relief else QColor("#4A6030")
        state_txt = "PARK" if on_relief else "RUN"
        p.setFont(QFont(FONT_MONO, 7, QFont.Weight.Bold))
        p.setPen(QPen(state_col))
        lbl_x = int(panel_r.right()) - int(scale*30)
        p.drawText(lbl_x, int(panel_r.top() + 4), int(scale*28), 14,
                   Qt.AlignmentFlag.AlignCenter, state_txt)
        # Pastille LED
        led_c = QColor("#28B848") if on_relief else QColor("#5A7A3A")
        if on_relief:
            led_h = QRadialGradient(lbl_x + int(scale*14), int(panel_r.top()) + 22, 5)
            led_h.setColorAt(0, QColor(40, 180, 80, 200))
            led_h.setColorAt(1, QColor(40, 180, 80, 0))
            p.setBrush(QBrush(led_h)); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(lbl_x + int(scale*9), int(panel_r.top()) + 17, 10, 10)
        p.setBrush(_rc_sphere_brush(lbl_x + int(scale*14), int(panel_r.top()) + 22, 4,
                                    led_c, roughness=0.1))
        p.setPen(_rc_mk_pen(QColor("#1A3010"), 0.8))
        p.drawEllipse(QPointF(lbl_x + int(scale*14), int(panel_r.top()) + 22), 4, 4)

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        # Zone parebrise (haut) — fond vert KPIT harmonisé
        ws_h = int(H * 0.60)
        path = QPainterPath()
        path.moveTo(W*0.08, ws_h*0.98)
        path.quadTo(W*0.03, ws_h*0.02, W*0.18, ws_h*0.05)
        path.lineTo(W*0.82, ws_h*0.05)
        path.quadTo(W*0.97, ws_h*0.02, W*0.92, ws_h*0.98)
        path.closeSubpath()
        # Fond vert KPIT + grille (haut de zone)
        bg_ws = QLinearGradient(0, 0, 0, ws_h)
        bg_ws.setColorAt(0.0,  QColor(252, 253, 255))
        bg_ws.setColorAt(0.6,  QColor(248, 250, 253))
        bg_ws.setColorAt(1.0,  QColor(244, 247, 252))
        p.fillRect(0, 0, W, ws_h, QBrush(bg_ws))
        glass_g = QLinearGradient(0, 0, 0, ws_h)
        glass_g.setColorAt(0, QColor(200,220,255,140)); glass_g.setColorAt(1, QColor(180,200,240,80))
        p.setBrush(QBrush(glass_g)); p.setPen(QPen(QColor("#94A3B8"), 1.5)); p.drawPath(path)

        cx = W//2; cy = int(ws_h*0.94); R = int(ws_h*0.82)
        sw_c = QColor("#8DC63F") if self._front_motor_on else QColor("#B0B3B5")
        sw_c.setAlpha(25)
        p.setBrush(QBrush(sw_c)); p.setPen(Qt.PenStyle.NoPen)
        p.drawPie(QRectF(cx-R, cy-R, R*2, R*2), int((-60+90)*16), int(-120*16))

        if not self._front_motor_on and not self._rest_contact:
            wiper_c = QColor("#8DC63F")
        elif self._front_motor_on:
            wiper_c = QColor(WOP[self._op]["color"]) if self._op > 0 else QColor("#E0A000")
        else:
            wiper_c = QColor("#888888")

        ang_r = math.radians(self._angle - 90)
        ex = cx + R*math.cos(ang_r); ey = cy + R*math.sin(ang_r)
        p.setPen(QPen(QColor(0,0,0,30), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(cx+2, cy+2, int(ex+2), int(ey+2))
        p.setPen(QPen(QColor("#505050"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(cx, cy, int(ex), int(ey))
        perp = math.radians(self._angle-90+90); bl = 32
        bx1 = ex+bl*math.cos(perp); by1 = ey+bl*math.sin(perp)
        bx2 = ex-bl*math.cos(perp); by2 = ey-bl*math.sin(perp)
        p.setPen(QPen(wiper_c, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(int(bx1),int(by1), int(bx2),int(by2))
        p.setBrush(QBrush(QColor("#303030"))); p.setPen(QPen(QColor("#1A1A1A"), 1.5))
        p.drawEllipse(cx-5, cy-5, 10, 10)

        # BCM state label
        if self._bcm_state not in ("OFF", ""):
            lbl_c = QColor(WOP[self._op]["color"]) if self._op > 0 else QColor(W_TEXT_DIM)
            p.setFont(QFont(FONT_UI, 10, QFont.Weight.Bold)); p.setPen(QPen(lbl_c))
            p.drawText(4, ws_h - 20, W-8, 16, Qt.AlignmentFlag.AlignCenter, self._bcm_state)

        # Compteur cycles
        p.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold)); p.setPen(QPen(QColor(A_TEAL2)))
        p.drawText(W-70, ws_h - 16, 66, 12, Qt.AlignmentFlag.AlignRight, f"#{self._blade_cycles}")

        # ── Zone bas (REST CONTACT) — fond vert KPIT continu ────────────
        rc_zone_y = ws_h + 4
        # Continuer le fond vert KPIT sous le parebrise
        bg_rc = QLinearGradient(0, ws_h, 0, H)
        bg_rc.setColorAt(0.0, QColor(248, 250, 253))
        bg_rc.setColorAt(1.0, QColor(244, 247, 252))
        p.fillRect(0, ws_h, W, H - ws_h, QBrush(bg_rc))
        # Grille légère zone RC
        p.save()
        p.translate(0, ws_h)
        p.setPen(QPen(QColor(200, 210, 225, 50), 0.5))
        for x in range(0, W, 20): p.drawLine(x, 0, x, H - ws_h)
        for y in range(0, H - ws_h, 20): p.drawLine(0, y, W, y)
        p.restore()

        scale_rc  = max(0.65, min(1.0, W / 320))
        rc_cx     = int(W * 0.12 + scale_rc * 28)
        rc_cy     = rc_zone_y + int((H - ws_h - 8) * 0.52)
        self._draw_rest_contact_panel(p, rc_cx, rc_cy, scale_rc)

        # Label "REST CONTACT" en haut du panneau
        p.setFont(QFont(FONT_MONO, 7))
        p.setPen(QPen(QColor("#64748B")))
        p.drawText(4, rc_zone_y + 2, W - 8, 11,
                   Qt.AlignmentFlag.AlignLeft, "REST CONTACT")

        # Cadre global
        p.setPen(QPen(QColor("#007ACC"), 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(1, 1, W-2, H-2, 6, 6)


# ═══════════════════════════════════════════════════════════
#  BMW M4 — Vue 3D rotative — inchangée
# ═══════════════════════════════════════════════════════════
class CarTopViewWidget(QWidget):
    """Vue 3D BMW M4 — fond sombre, drag souris. Conservée intacte."""
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._ign     = "OFF"
        self._reverse = False
        self._speed   = 0.0
        self._rain    = 0
        self._yaw     = 0.0
        self._pitch   = 70.0
        self._drag    = False
        self._last_mouse = QPointF(0, 0)
        self._rot_inertia = 0.0
        self._wheel_angle = 0.0
        self._car_offset  = 0.0
        self._vibe        = 0.0
        self._vibe_dir    = 1
        self._exhaust     = []
        self._rain_drops  = []
        self._wiper_angle = -28.0
        self._wiper_dir   = 1
        self._arrow_phase = 0.0
        self.setMinimumSize(260, 360)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setMouseTracking(True)
        self._t = QTimer()
        self._t.timeout.connect(self._tick)
        self._t.start(60)

    def set_ignition(self, s: str)  -> None: self._ign     = s
    def set_reverse(self, r: bool)  -> None: self._reverse = bool(r)
    def set_speed(self, spd: float) -> None: self._speed   = spd
    def set_rain(self, rain: int)   -> None: self._rain    = rain

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = True; self._last_mouse = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = False; self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseMoveEvent(self, e) -> None:
        if self._drag:
            dx = e.position().x() - self._last_mouse.x()
            dy = e.position().y() - self._last_mouse.y()
            self._yaw   = (self._yaw + dx*0.55) % 360
            self._pitch = max(15.0, min(90.0, self._pitch - dy*0.35))
            self._rot_inertia = dx*0.3
            self._last_mouse  = e.position()

    def wheelEvent(self, e) -> None:
        self._pitch = max(15.0, min(90.0, self._pitch + e.angleDelta().y()/40.0))
        self.update()

    def _tick(self) -> None:
        on = self._ign == "ON"
        if not self._drag and abs(self._rot_inertia) > 0.05:
            self._yaw = (self._yaw + self._rot_inertia) % 360
            self._rot_inertia *= 0.92
        if on:
            spd = max(self._speed/22.0, 0.18 if self._speed > 0 else 0)
            self._wheel_angle = (self._wheel_angle + (-6 if self._reverse else 6)*spd) % 360
            tgt = 12 if self._reverse else -12
            self._car_offset += (tgt - self._car_offset)*0.08
        else:
            self._car_offset *= 0.88
        if on:
            self._vibe += 0.45*self._vibe_dir
            if abs(self._vibe) > 1.0: self._vibe_dir *= -1
        else:
            self._vibe *= 0.75
        self._arrow_phase = (self._arrow_phase + 0.07) % 1.0
        if on and random.random() < 0.4:
            for sx in (-1, 1):
                self._exhaust.append([0.0, sx, random.uniform(0.5, 0.95)])
        self._exhaust = [[a+0.05,s,op-0.032] for a,s,op in self._exhaust if op > 0]
        if self._rain > 0:
            for _ in range(max(1, int(self._rain/16))):
                if len(self._rain_drops) < 80:
                    self._rain_drops.append([random.uniform(0,1), random.uniform(0,0.2),
                                             random.uniform(0.02,0.055), random.uniform(0.3,0.75)])
            self._rain_drops = [[x,y+sp,sp,op] for x,y,sp,op in self._rain_drops if y < 1.1]
        else:
            self._rain_drops.clear()
        if self._rain > 10 and on:
            ws = 2.0 + self._rain/28.0
            self._wiper_angle += ws*self._wiper_dir
            if self._wiper_angle > 28:  self._wiper_angle = 28;  self._wiper_dir = -1
            if self._wiper_angle < -28: self._wiper_angle = -28; self._wiper_dir = 1
        self.update()

    def _project(self, cx, cy, scale, lx, ly, lz=0.0):
        yr = math.radians(self._yaw); pr = math.radians(self._pitch)
        rx = lx*math.cos(yr) - ly*math.sin(yr)
        ry = lx*math.sin(yr) + ly*math.cos(yr)
        pc = math.cos(pr); ps = math.sin(pr)
        fy = ry*ps - lz*pc
        off_y = self._car_offset*(ps/90.0)*0.5
        sx = int(cx + fy*scale)
        sy = int(cy + rx*scale + off_y + self._vibe*ps*0.3)
        return sx, sy

    def _p3(self, cx, cy, scale, pts3):
        return QPolygonF([QPointF(*self._project(cx,cy,scale,x,y,z)) for x,y,z in pts3])

    def _path3(self, cx, cy, scale, cmds):
        path = QPainterPath()
        for item in cmds:
            cmd = item[0]
            if cmd == "M": path.moveTo(*self._project(cx,cy,scale,*item[1:]))
            elif cmd == "L": path.lineTo(*self._project(cx,cy,scale,*item[1:]))
            elif cmd == "Q":
                if len(item) < 6:
                    path.lineTo(*self._project(cx,cy,scale,item[1],item[2],item[3] if len(item)>3 else 0))
                else:
                    cx2,cy2 = self._project(cx,cy,scale,item[1],item[2],item[3] if len(item)>3 else 0)
                    ex,ey   = self._project(cx,cy,scale,item[4],item[5],item[6] if len(item)>6 else 0)
                    path.quadTo(cx2,cy2,ex,ey)
            elif cmd == "C":
                c1x,c1y = self._project(cx,cy,scale,item[1],item[2],item[3] if len(item)>3 else 0)
                c2x,c2y = self._project(cx,cy,scale,item[4],item[5],item[6] if len(item)>6 else 0)
                ex,ey   = self._project(cx,cy,scale,item[7],item[8],item[9] if len(item)>9 else 0)
                path.cubicTo(c1x,c1y,c2x,c2y,ex,ey)
            elif cmd == "Z": path.closeSubpath()
        return path

    def _draw_wheel(self, p, cx2, cy2, scale, lx, ly, flip=1):
        corners = [(lx-0.22,ly,0.18),(lx+0.22,ly,0.18),(lx+0.22,ly,-0.18),(lx-0.22,ly,-0.18)]
        poly = self._p3(cx2,cy2,scale,corners)
        tg = QLinearGradient(poly.at(0).x(),poly.at(0).y(),poly.at(1).x(),poly.at(1).y())
        tg.setColorAt(0,QColor("#0A0A0A")); tg.setColorAt(0.45,QColor("#242424"))
        tg.setColorAt(0.55,QColor("#181818")); tg.setColorAt(1,QColor("#0A0A0A"))
        p.setBrush(QBrush(tg)); p.setPen(QPen(QColor("#050505"),1.5)); p.drawPolygon(poly)
        rcx,rcy = self._project(cx2,cy2,scale,lx,ly,0)
        rrx = int(scale*0.24*0.5+scale*0.08); rry = int(scale*0.44*0.41)
        gr = QRadialGradient(rcx,rcy,max(rrx,rry))
        gr.setColorAt(0,QColor("#C8D0D8")); gr.setColorAt(0.55,QColor("#707880")); gr.setColorAt(1,QColor("#383E44"))
        p.setBrush(QBrush(gr)); p.setPen(QPen(QColor("#282E34"),0.8))
        p.drawEllipse(rcx-rrx,rcy-rry,rrx*2,rry*2)
        for i in range(5):
            a = math.radians(self._wheel_angle*flip + i*72)
            p.setPen(QPen(QColor("#A0A8B0"),2.0,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
            p.drawLine(rcx,rcy,int(rcx+(rrx-1)*math.cos(a)),int(rcy+(rry-1)*math.sin(a)))
        p.setBrush(QBrush(QColor("#1A2030"))); p.setPen(QPen(QColor("#101820"),0.8))
        p.drawEllipse(rcx-4,rcy-4,8,8)

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(0,0,W,H,QBrush(QColor("#1E2228")))
        p.setPen(QPen(QColor("#262C34"),1,Qt.PenStyle.DashLine))
        for x in range(0,W,30): p.drawLine(x,0,x,H)
        for y in range(0,H,30): p.drawLine(0,y,W,y)
        for rx,ry,_,op in self._rain_drops:
            rc = QColor(A_TEAL); rc.setAlphaF(op*0.4)
            p.setPen(QPen(rc,1))
            px2=int(rx*W); py2=int(ry*H)
            p.drawLine(px2,py2,px2-1,py2+7)
        margin = 48
        scale = min((H-margin*2)/3.2,(W-margin*2)/2.0,78)
        cx, cy = W//2, H//2
        on=self._ign=="ON"; acc=self._ign=="ACC"; off=self._ign=="OFF"
        body_col = QColor("#4A5058") if off else (QColor("#2A3A50") if acc else QColor("#1C2A3C"))
        yaw_r = math.radians(self._yaw)
        spec  = 0.5+0.5*math.cos(yaw_r+math.pi/4)
        def pr(lx,ly,lz=0.0): return self._project(cx,cy,scale,lx,ly,lz)
        def pg(pts3):          return self._p3(cx,cy,scale,pts3)
        def ph(*cmds):         return self._path3(cx,cy,scale,cmds)
        p.setBrush(QBrush(QColor(0,0,0,50))); p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(pg([(-1.55,-0.85,-0.05),(1.60,-0.85,-0.05),(1.60,0.85,-0.05),(-1.55,0.85,-0.05)]))
        body = ph(
            ("M",-1.50,0.00,0.04),("Q",-1.52,0.30,0.04,-1.45,0.55,0.06),
            ("Q",-1.38,0.68,0.06,-1.20,0.72,0.08),("Q",-0.80,0.82,0.10,-0.50,0.84,0.12),
            ("Q",0.00,0.80,0.10,0.40,0.82,0.10),("Q",0.70,0.86,0.12,0.95,0.88,0.12),
            ("Q",1.20,0.84,0.10,1.40,0.72,0.08),("Q",1.52,0.55,0.06,1.55,0.28,0.04),
            ("L",1.55,0.00,0.04),("L",1.55,-0.28,0.04),
            ("Q",1.52,-0.55,0.06,1.40,-0.72,0.08),("Q",1.20,-0.84,0.10,0.95,-0.88,0.12),
            ("Q",0.70,-0.86,0.12,0.40,-0.82,0.10),("Q",0.00,-0.80,0.10,-0.50,-0.84,0.12),
            ("Q",-0.80,-0.82,0.10,-1.20,-0.72,0.08),("Q",-1.38,-0.68,0.06,-1.45,-0.55,0.06),
            ("Q",-1.52,-0.30,0.04,-1.50,0.00,0.04),("Z",),)
        bx0,by0=pr(-0.82,0,0.08); bx1,by1=pr(0.82,0,0.08)
        gbd=QLinearGradient(bx0,by0,bx1,by1)
        gbd.setColorAt(0,body_col.darker(int(150-spec*20)))
        gbd.setColorAt(0.18,body_col.lighter(int(105+spec*15)))
        gbd.setColorAt(0.45,body_col.lighter(int(140+spec*25)))
        gbd.setColorAt(0.55,body_col.lighter(int(155+spec*20)))
        gbd.setColorAt(0.82,body_col.lighter(int(108+spec*10)))
        gbd.setColorAt(1,body_col.darker(int(148-spec*15)))
        p.setBrush(QBrush(gbd)); p.setPen(QPen(body_col.darker(200),1.2)); p.drawPath(body)
        hood = ph(
            ("M",-1.50,0.00,0.04),("Q",-1.48,0.50,0.08,-1.30,0.62,0.10),
            ("Q",-1.00,0.62,0.12,-0.68,0.56,0.14),("Q",-0.50,0.48,0.15,-0.42,0.00,0.16),
            ("Q",-0.50,-0.48,0.15,-0.68,-0.56,0.14),("Q",-1.00,-0.62,0.12,-1.30,-0.62,0.10),
            ("Q",-1.48,-0.50,0.08,-1.50,0.00,0.04),("Z",),)
        hx0,hy0=pr(-1.50,0,0.08); hx1,hy1=pr(-0.42,0,0.16)
        gh=QLinearGradient(hx0,hy0,hx1,hy1)
        gh.setColorAt(0,body_col.darker(160))
        gh.setColorAt(0.25,body_col.lighter(int(112+spec*20)))
        gh.setColorAt(0.55,body_col.lighter(int(188+spec*15)))
        gh.setColorAt(0.85,body_col.lighter(int(110+spec*10)))
        gh.setColorAt(1,body_col.darker(140))
        p.setBrush(QBrush(gh)); p.setPen(QPen(body_col.darker(170),0.8)); p.drawPath(hood)
        for sy in (-0.14,0.14):
            dome=ph(("M",-1.46,sy-0.06,0.04),("Q",-1.00,sy-0.07,0.14,-0.46,sy-0.05,0.16),
                    ("Q",-0.46,sy+0.05,0.16,-1.00,sy+0.07,0.14),
                    ("Q",-1.46,sy+0.06,0.04,-1.46,sy-0.06,0.04),("Z",),)
            p.setBrush(QBrush(body_col.lighter(200))); p.setPen(Qt.PenStyle.NoPen); p.drawPath(dome)
        roof=ph(("M",-0.42,0.00,0.16),("Q",-0.40,0.44,0.20,-0.20,0.52,0.52),
                ("Q",0.00,0.50,0.60,0.30,0.48,0.62),("Q",0.65,0.44,0.58,0.80,0.38,0.50),
                ("Q",0.88,0.30,0.38,0.90,0.00,0.28),("Q",0.88,-0.30,0.38,0.80,-0.38,0.50),
                ("Q",0.65,-0.44,0.58,0.30,-0.48,0.62),("Q",0.00,-0.50,0.60,-0.20,-0.52,0.52),
                ("Q",-0.40,-0.44,0.20,-0.42,0.00,0.16),("Z",),)
        rx0,ry0=pr(0,0,0.55); rx1,ry1=pr(0.45,0,0.55)
        rc_dark=QColor("#0A1422") if on else (QColor("#141E2C") if acc else QColor("#1E2630"))
        groof=QLinearGradient(rx0,ry0,rx1,ry1)
        for stop,fac in [(0,1),(0.25,200),(0.50,280),(0.75,190),(1,1)]:
            groof.setColorAt(stop,rc_dark.lighter(fac) if fac>1 else rc_dark)
        p.setBrush(QBrush(groof)); p.setPen(QPen(QColor("#050C14"),1)); p.drawPath(roof)
        wsf=ph(("M",-0.42,0.44,0.20),("Q",-0.38,0.50,0.22,-0.20,0.52,0.52),
               ("Q",0.00,0.50,0.60,0.00,-0.50,0.60),("Q",-0.20,-0.52,0.52,-0.38,-0.50,0.22),
               ("Q",-0.42,-0.44,0.20,-0.42,0.44,0.20),("Z",),)
        gwsf=QLinearGradient(*pr(-0.42,0.44,0.20),*pr(-0.20,0.52,0.52))
        gwsf.setColorAt(0,QColor(140,195,230,170)); gwsf.setColorAt(1,QColor(80,145,190,95))
        p.setBrush(QBrush(gwsf)); p.setPen(QPen(QColor("#1A4060"),0.8)); p.drawPath(wsf)
        if self._rain>10 and on:
            wpx,wpy=pr(-0.10,0,0.38)
            wr2=int(scale*0.40); wa_r=math.radians(self._wiper_angle*1.6)
            wex=wpx+int(wr2*math.sin(wa_r)); wey=wpy+int(wr2*math.cos(wa_r))
            p.setPen(QPen(QColor("#101010"),3,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
            p.drawLine(wpx,wpy,wex,wey)
            p.setPen(QPen(QColor(A_TEAL),2,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
            p.drawLine(wpx,wpy,wex,wey)
        wsr=ph(("M",0.80,0.38,0.50),("Q",0.65,0.44,0.58,0.30,0.48,0.62),
               ("Q",0.30,-0.48,0.62,0.65,-0.44,0.58),("Q",0.80,-0.38,0.50,0.80,0.38,0.50),("Z",),)
        gwsr=QLinearGradient(*pr(0.65,0,0.58),*pr(0.80,0,0.50))
        gwsr.setColorAt(0,QColor(80,145,190,95)); gwsr.setColorAt(1,QColor(140,195,230,160))
        p.setBrush(QBrush(gwsr)); p.setPen(QPen(QColor("#1A4060"),0.8)); p.drawPath(wsr)
        trunk=ph(("M",0.90,0.00,0.14),("Q",0.88,0.35,0.16,0.80,0.38,0.50),
                 ("Q",0.80,-0.38,0.50,0.88,-0.35,0.16),("L",0.90,0.00,0.14),("Z",),)
        tx0,ty0=pr(0.88,0,0.16); tx1,ty1=pr(0.80,0,0.50)
        gtrunk=QLinearGradient(tx0,ty0,tx1,ty1)
        gtrunk.setColorAt(0,body_col.darker(140)); gtrunk.setColorAt(0.5,body_col.lighter(140))
        gtrunk.setColorAt(1,body_col.darker(130))
        p.setBrush(QBrush(gtrunk)); p.setPen(QPen(body_col.darker(175),0.8)); p.drawPath(trunk)
        wing=ph(("M",1.35,-0.85,0.58),("Q",1.40,-0.85,0.60,1.45,0.00,0.62),
                ("Q",1.40,0.85,0.60,1.35,0.85,0.58),("Q",1.30,0.82,0.55,1.28,0.00,0.54),
                ("Q",1.30,-0.82,0.55,1.35,-0.85,0.58),("Z",),)
        wg=QLinearGradient(*pr(1.35,-0.85,0.60),*pr(1.35,0.85,0.60))
        wg.setColorAt(0,QColor("#181C22")); wg.setColorAt(0.45,QColor("#3A4048"))
        wg.setColorAt(0.55,QColor("#2A3038")); wg.setColorAt(1,QColor("#181C22"))
        p.setBrush(QBrush(wg)); p.setPen(QPen(QColor("#0A0E14"),1)); p.drawPath(wing)
        for sy in (-0.78,0.78):
            mirror=ph(("M",-0.60,sy,0.38),("Q",-0.62,sy*1.08,0.36,-0.58,sy*1.12,0.34),
                      ("Q",-0.50,sy*1.10,0.34,-0.48,sy,0.36),
                      ("Q",-0.52,sy*0.96,0.38,-0.60,sy,0.38),("Z",),)
            mg=QLinearGradient(*pr(-0.60,sy,0.38),*pr(-0.55,sy*1.1,0.34))
            mg.setColorAt(0,body_col.darker(150)); mg.setColorAt(1,body_col.lighter(120))
            p.setBrush(QBrush(mg)); p.setPen(QPen(body_col.darker(180),0.8)); p.drawPath(mirror)
        for sy in (-0.18,0.18):
            grille=ph(("M",-1.52,sy-0.14,0.04),("Q",-1.55,sy-0.14,0.06,-1.56,sy,0.08),
                      ("Q",-1.55,sy+0.14,0.06,-1.52,sy+0.14,0.04),
                      ("Q",-1.46,sy+0.12,0.04,-1.45,sy,0.04),
                      ("Q",-1.46,sy-0.12,0.04,-1.52,sy-0.14,0.04),("Z",),)
            p.setBrush(QBrush(QColor("#08090C"))); p.setPen(QPen(QColor("#1A2030"),0.8)); p.drawPath(grille)
        for lx,ly,flip in [(-0.88,-0.82,1),(-0.88,0.82,-1),(0.95,-0.82,1),(0.95,0.82,-1)]:
            self._draw_wheel(p,cx,cy,scale,lx,ly,flip)
        for sy in (-0.42,0.42):
            hx2,hy2=pr(-1.52,sy,0.06)
            if on:
                halo=QRadialGradient(hx2,hy2,int(scale*0.24))
                halo.setColorAt(0,QColor(255,255,230,200)); halo.setColorAt(1,QColor(255,255,200,0))
                p.setBrush(QBrush(halo)); p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(hx2-int(scale*0.24),hy2-int(scale*0.12),int(scale*0.48),int(scale*0.24))
                p.setBrush(QBrush(QColor("#F2F6FF"))); p.setPen(QPen(QColor("#B0B8C8"),0.8))
            elif acc:
                p.setBrush(QBrush(QColor("#FF8C00"))); p.setPen(QPen(QColor("#CC6600"),0.8))
            else:
                p.setBrush(QBrush(QColor("#141820"))); p.setPen(QPen(QColor("#0C1018"),0.8))
            hw=int(scale*0.28); hh=int(scale*0.12)
            p.drawEllipse(hx2-hw//2,hy2-hh//2,hw,hh)
            drl_c=QColor("#FFFFC0") if on else (QColor("#FFA020") if acc else QColor("#111820"))
            p.setPen(QPen(drl_c,2.2,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
            dl=int(scale*0.15)
            p.drawLine(hx2-dl,hy2-int(scale*0.10),hx2+dl,hy2-int(scale*0.10))
            p.drawLine(hx2+dl,hy2-int(scale*0.10),hx2+dl,hy2+int(scale*0.02))
        for sy in (-0.42,0.42):
            tx2,ty2=pr(1.52,sy,0.06)
            if on or acc:
                halo=QRadialGradient(tx2,ty2,int(scale*0.20))
                halo.setColorAt(0,QColor(220,0,0,200)); halo.setColorAt(1,QColor(180,0,0,0))
                p.setBrush(QBrush(halo)); p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(tx2-int(scale*0.20),ty2-int(scale*0.10),int(scale*0.40),int(scale*0.20))
                p.setBrush(QBrush(QColor("#E01818"))); p.setPen(QPen(QColor("#800000"),0.8))
            else:
                p.setBrush(QBrush(QColor("#3C0808"))); p.setPen(QPen(QColor("#200404"),0.8))
            tw=int(scale*0.24); th=int(scale*0.10)
            p.drawEllipse(tx2-tw//2,ty2-th//2,tw,th)
        if self._reverse and on:
            for sy in (-0.18,0.18):
                rx3,ry3=pr(1.52,sy,0.06)
                halo=QRadialGradient(rx3,ry3,int(scale*0.14))
                halo.setColorAt(0,QColor(255,255,255,220)); halo.setColorAt(1,QColor(255,255,255,0))
                p.setBrush(QBrush(halo)); p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(rx3-int(scale*0.14),ry3-int(scale*0.08),int(scale*0.28),int(scale*0.16))
        for sy in (-0.32,0.32):
            ex2,ey2=pr(1.55,sy,0.00)
            p.setBrush(QBrush(QColor("#303840"))); p.setPen(QPen(QColor("#1A2028"),0.8))
            p.drawEllipse(ex2-int(scale*0.05),ey2-int(scale*0.05),int(scale*0.10),int(scale*0.10))
        for adv,sx2,op in self._exhaust:
            ebx,eby=pr(1.55,sx2*0.32,0.00)
            r_e=int(3+adv*25)
            smoke=QColor(180,185,192); smoke.setAlphaF(max(0,op*0.25))
            p.setBrush(QBrush(smoke)); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(ebx-r_e,int(eby+adv*28)-r_e,r_e*2,r_e*2)
        if self._reverse and on:
            for ai in range(3):
                ph2=(self._arrow_phase*3+ai)%3
                alpha=int(255*max(0,1.0-abs(ph2-1.0)))
                ac=QColor(A_ORANGE); ac.setAlpha(alpha)
                ax2,ay2=pr(1.75+ai*0.20,0,0.04)
                pts3=QPolygonF([QPointF(ax2+int(scale*0.13),ay2),
                                QPointF(ax2,ay2-int(scale*0.15)),
                                QPointF(ax2,ay2+int(scale*0.15))])
                p.setBrush(QBrush(ac)); p.setPen(Qt.PenStyle.NoPen); p.drawPolygon(pts3)
        if not self._reverse and on and self._speed > 3:
            for ai in range(3):
                ph2=(self._arrow_phase*3+ai)%3
                alpha=int(255*max(0,1.0-abs(ph2-1.0)))
                ac=QColor(A_GREEN); ac.setAlpha(alpha)
                ax2,ay2=pr(-1.75-ai*0.20,0,0.04)
                pts3=QPolygonF([QPointF(ax2-int(scale*0.13),ay2),
                                QPointF(ax2,ay2-int(scale*0.15)),
                                QPointF(ax2,ay2+int(scale*0.15))])
                p.setBrush(QBrush(ac)); p.setPen(Qt.PenStyle.NoPen); p.drawPolygon(pts3)
        bw2=158; bh2=24; bx3=(W-bw2)//2; by3=H-28
        p.setBrush(QBrush(QColor(0,0,0,155))); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(bx3,by3,bw2,bh2,6,6)
        lmap2={"OFF":("ENGINE  OFF","#888888"),"ACC":("ACCESSORY",A_ORANGE),"ON":("ENGINE  ON","#8DC63F")}
        lt2,lc2=("REVERSE  <",A_ORANGE) if (self._reverse and on) else lmap2.get(self._ign,("OFF","#888888"))
        p.setFont(QFont(FONT_UI,9,QFont.Weight.Bold)); p.setPen(QPen(QColor(lc2)))
        p.drawText(bx3,by3,bw2,bh2,Qt.AlignmentFlag.AlignCenter,lt2)
        if on and self._speed > 0:
            sw3=62; sx4=bx3+bw2+5
            p.setBrush(QBrush(QColor(0,0,0,130))); p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(sx4,by3,sw3,bh2,6,6)
            p.setFont(QFont(FONT_MONO,9,QFont.Weight.Bold)); p.setPen(QPen(QColor(A_TEAL)))
            p.drawText(sx4,by3,sw3,bh2,Qt.AlignmentFlag.AlignCenter,f"{self._speed:.0f} km/h")
        p.setFont(QFont(FONT_UI,7)); p.setPen(QPen(QColor(80,85,95)))
        p.drawText(6,H-12,W-12,12,Qt.AlignmentFlag.AlignCenter,"drag to rotate  .  scroll to tilt")-e

# Public aliases expected by tests
ArcGauge        = ArcGaugeWidget
DigitalDisplay  = SparklineWidget   # closest numeric display widget
OscilloscopeWidget = SparklineWidget  # oscilloscope-style sparkline
