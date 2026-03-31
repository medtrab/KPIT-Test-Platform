"""
WipeWash — Widgets instruments graphiques
PumpWidget, MotorWidget, WindshieldWidget, CarTopViewWidget (BMW M4 3D).
"""

import math
import random

from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore    import Qt, QTimer, QRectF, QPointF
from PyQt6.QtGui     import (
    QPainter, QColor, QPen, QBrush, QFont,
    QLinearGradient, QRadialGradient,
    QPainterPath, QPolygonF,
)

from constants import (
    FONT_UI, FONT_MONO,
    W_PANEL, W_PANEL2, W_PANEL3,
    W_BORDER, W_BORDER2, W_TEXT, W_TEXT_DIM,
    A_TEAL, A_TEAL2, A_GREEN, A_RED, A_ORANGE, A_AMBER,
    WOP,
)


# ═══════════════════════════════════════════════════════════
#  POMPE HYDRAULIQUE
# ═══════════════════════════════════════════════════════════
class PumpWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._state       = "OFF"
        self._current     = 0.0
        self._fault       = False
        self._angle       = 0.0
        self._flow_offset = 0.0
        self._t = QTimer()
        self._t.timeout.connect(self._tick)
        self._t.start(60)  # Optimisation: 60ms = ~16 FPS (au lieu de 30ms)
        self.setMinimumSize(220, 180)

    def set_state(self, state: str, current: float = 0.0, fault: bool = False) -> None:
        self._state   = state
        self._current = current
        self._fault   = fault

    def _tick(self) -> None:
        if self._state == "FORWARD":
            self._angle       = (self._angle + 4) % 360
            self._flow_offset = (self._flow_offset + 2) % 30
        elif self._state == "BACKWARD":
            self._angle       = (self._angle - 4) % 360
            self._flow_offset = (self._flow_offset - 2) % 30
        else:
            if abs(self._angle % 360) > 2:
                self._angle = (self._angle + 1) % 360
            else:
                return   # rien n'a changé, pas de repaint inutile
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(0, 0, W, H, QBrush(QColor(W_PANEL)))

        running = self._state in ("FORWARD", "BACKWARD")
        fw      = self._state == "FORWARD"

        TOP_H  = 28; BOT_H = 30
        draw_h = H - TOP_H - BOT_H
        cx = W // 2
        cy = TOP_H + draw_h // 2
        R  = min(draw_h // 2 - 10, W // 2 - 24, 50)

        # Courant
        p.setFont(QFont(FONT_MONO, 10, QFont.Weight.Bold))
        p.setPen(QPen(QColor(W_TEXT_DIM)))
        p.drawText(0, 4, W, TOP_H - 4,
                   Qt.AlignmentFlag.AlignCenter, f"I = {self._current:.3f} A")
        p.setPen(QPen(QColor(W_BORDER), 1))
        p.drawLine(8, TOP_H - 1, W - 8, TOP_H - 1)

        # Corps
        body_c = QColor("#5A8FA0") if running else QColor("#8A9BA8")
        g = QLinearGradient(cx - R, cy - R, cx + R, cy + R)
        g.setColorAt(0, body_c.lighter(130))
        g.setColorAt(0.5, body_c)
        g.setColorAt(1, body_c.darker(130))
        p.setBrush(QBrush(g)); p.setPen(QPen(QColor("#3A6070"), 2))
        p.drawEllipse(cx - R, cy - R, R * 2, R * 2)

        # Rotor
        nr = int(R * 0.68)
        rc = QColor(A_GREEN if fw else A_ORANGE) if running else QColor(W_BORDER2)
        ps = max(5, int(R * 0.15))
        for i in range(6):
            ang = math.radians(self._angle + i * 60)
            bx = cx + nr * math.cos(ang); by = cy + nr * math.sin(ang)
            p.setBrush(QBrush(rc.lighter(110)))
            p.setPen(QPen(rc.darker(120), 1))
            p.drawEllipse(int(bx - ps), int(by - ps), ps * 2, ps * 2)
        p.setBrush(QBrush(QColor("#3A3A3A")))
        p.setPen(QPen(QColor("#1A1A1A"), 1.5))
        p.drawEllipse(cx - 8, cy - 8, 16, 16)

        # Tuyaux
        pipe_c = QColor("#4A6070"); ph = 18
        p.setBrush(QBrush(pipe_c)); p.setPen(QPen(pipe_c.darker(120), 1.5))
        plw = max(cx - R - 2, 2)
        if plw > 2:
            p.drawRect(2, cy - ph // 2, plw, ph)
        prx = cx + R; prw = W - prx - 2
        if prw > 2:
            p.drawRect(prx, cy - ph // 2, prw, ph)
        pbt = cy + R; pbh = H - BOT_H - pbt - 4
        if pbh > 2:
            p.drawRect(cx - ph // 2, pbt, ph, pbh)

        # Flèches débit
        if running:
            fc = QColor(A_GREEN if fw else A_ORANGE)
            p.setPen(QPen(fc, 2))
            for i in range(3):
                ax = int(8 + i * 12 + self._flow_offset * 0.5) % max(plw - 4, 1) + 4
                if ax > plw - 4:
                    continue
                p.drawLine(ax, cy - 5, ax + 8, cy)
                p.drawLine(ax, cy + 5, ax + 8, cy)
            for i in range(3):
                ax = int(prx + 6 + i * 12 + self._flow_offset * 0.5) % max(prw - 4, 1) + prx + 4
                if ax > W - 8:
                    continue
                p.drawLine(ax, cy - 5, ax + 8, cy)
                p.drawLine(ax, cy + 5, ax + 8, cy)

        # Fault overlay
        if self._fault:
            p.setBrush(QBrush(QColor(255, 0, 0, 30)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(cx - R, cy - R, R * 2, R * 2)
            p.setFont(QFont(FONT_UI, 12, QFont.Weight.Bold))
            p.setPen(QPen(QColor(A_RED)))
            p.drawText(cx - R, cy - 10, R * 2, 20, Qt.AlignmentFlag.AlignCenter, "FAULT")

        # État bas
        state_map = {
            "FORWARD": ">  FORWARD", "BACKWARD": "<  BACKWARD",
            "OFF": "[]  STOPPED", "FAULT": "!  FAULT", "OVERCURRENT": "!  OVERCUR.",
        }
        state_c = {
            "FORWARD": A_GREEN, "BACKWARD": A_TEAL,
            "OFF": W_TEXT_DIM,  "FAULT": A_RED, "OVERCURRENT": A_ORANGE,
        }
        disp = state_map.get(self._state, "[]  STOPPED")
        sc   = state_c.get(self._state, W_TEXT_DIM)
        sep_y = H - BOT_H
        p.setPen(QPen(QColor(W_BORDER), 1))
        p.drawLine(8, sep_y, W - 8, sep_y)
        p.setFont(QFont(FONT_UI, 10, QFont.Weight.Bold))
        p.setPen(QPen(QColor(sc)))
        p.drawText(0, sep_y + 4, W, BOT_H - 4, Qt.AlignmentFlag.AlignCenter, disp)


# ═══════════════════════════════════════════════════════════
#  MOTEUR ÉLECTRIQUE
# ═══════════════════════════════════════════════════════════
class MotorWidget(QWidget):
    def __init__(self, motor_id: str = "FRONT", parent=None) -> None:
        super().__init__(parent)
        self._id    = motor_id
        self._state = "OFF"
        self._speed = "Speed1"
        self._angle = 0.0
        self._t = QTimer()
        self._t.timeout.connect(self._tick)
        self._t.start(60)  # Optimisation: 60ms = ~16 FPS (au lieu de 25ms)
        self.setMinimumSize(190, 180)

    def set_state(self, state: str, speed: str = "Speed1") -> None:
        self._state = state
        self._speed = speed

    def _tick(self) -> None:
        if self._state == "ON":
            spd = 6.0 if self._speed == "Speed2" else 3.5
            self._angle = (self._angle + spd) % 360
            self.update()
        elif self._angle % 360 > 2:
            self._angle = (self._angle + 0.8) % 360
            self.update()
        # else: rien n'a bougé, pas de repaint inutile

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(0, 0, W, H, QBrush(QColor(W_PANEL)))
        on = self._state == "ON"

        TOP_H = 26; BOT_H = 46
        draw_h = H - TOP_H - BOT_H
        cx = W // 2
        cy = TOP_H + draw_h // 2
        R  = min(draw_h // 2 - 8, W // 2 - 10, 52)

        # ID
        p.setFont(QFont(FONT_UI, 10, QFont.Weight.Bold))
        p.setPen(QPen(QColor(W_TEXT_DIM)))
        p.drawText(0, 4, W, TOP_H - 4, Qt.AlignmentFlag.AlignCenter, f"MOTOR {self._id}")
        p.setPen(QPen(QColor(W_BORDER), 1))
        p.drawLine(8, TOP_H - 1, W - 8, TOP_H - 1)

        # Stator
        stator_c = QColor("#5A6A7A") if on else QColor("#8A9AA8")
        g = QLinearGradient(cx - R, cy - R, cx + R, cy + R)
        g.setColorAt(0, stator_c.lighter(125))
        g.setColorAt(0.4, stator_c)
        g.setColorAt(1, stator_c.darker(140))
        p.setBrush(QBrush(g)); p.setPen(QPen(QColor("#3A4A5A"), 2.5))
        p.drawEllipse(cx - R, cy - R, R * 2, R * 2)
        p.setPen(QPen(QColor("#3A4A5A"), 1.5))
        for i in range(12):
            ang = math.radians(i * 30)
            x1 = cx + (R - 4) * math.cos(ang); y1 = cy + (R - 4) * math.sin(ang)
            x2 = cx + (R + 1) * math.cos(ang); y2 = cy + (R + 1) * math.sin(ang)
            p.drawLine(int(x1), int(y1), int(x2), int(y2))

        # Pôles rotor
        rr = int(R * 0.68)
        for i, pc in enumerate([A_RED, A_TEAL2, A_RED, A_TEAL2]):
            ang = math.radians(self._angle + i * 90)
            px = cx + rr * 0.55 * math.cos(ang)
            py = cy + rr * 0.55 * math.sin(ang)
            pole = QColor(pc) if on else QColor(W_BORDER2)
            ps = max(7, int(R * 0.16))
            p.setBrush(QBrush(pole)); p.setPen(QPen(pole.darker(140), 1))
            p.drawEllipse(int(px - ps), int(py - ps), ps * 2, ps * 2)

        # Disque rotor
        g2 = QRadialGradient(cx, cy, rr)
        g2.setColorAt(0, QColor("#C8D0D8"))
        g2.setColorAt(0.7, QColor("#9AAABB"))
        g2.setColorAt(1, QColor("#708090"))
        p.setBrush(QBrush(g2)); p.setPen(QPen(QColor("#4A5A6A"), 2))
        p.drawEllipse(cx - rr, cy - rr, rr * 2, rr * 2)
        p.setPen(QPen(QColor("#5A6A7A"), 2))
        for i in range(8):
            ang = math.radians(self._angle + i * 45)
            x1 = cx + 8 * math.cos(ang); y1 = cy + 8 * math.sin(ang)
            x2 = cx + (rr - 4) * math.cos(ang); y2 = cy + (rr - 4) * math.sin(ang)
            p.drawLine(int(x1), int(y1), int(x2), int(y2))
        p.setBrush(QBrush(QColor("#2A2A2A"))); p.setPen(QPen(QColor("#1A1A1A"), 1.5))
        p.drawEllipse(cx - 7, cy - 7, 14, 14)

        # Arbre
        sb = H - BOT_H - 2; st = cy + R - 2
        if sb > st:
            sc = QColor("#707070") if not on else QColor("#4A5A6A")
            p.setBrush(QBrush(sc)); p.setPen(QPen(sc.darker(130), 1.5))
            p.drawRect(cx - 5, st, 10, sb - st)

        # Flèche rotation
        if on:
            fc = QColor(A_GREEN if self._speed == "Speed1" else "#1B5E20")
            p.setPen(QPen(fc, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(cx - R + 8, cy - R + 8, (R - 8) * 2, (R - 8) * 2, 45 * 16, -90 * 16)
            ar = math.radians(45 - 90)
            tx = cx + (R - 8) * math.cos(ar); ty = cy + (R - 8) * math.sin(ar)
            pts = QPolygonF([QPointF(tx, ty), QPointF(tx - 6, ty - 4), QPointF(tx - 4, ty + 6)])
            p.setBrush(QBrush(fc)); p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(pts)

        # Séparateur bas
        sep_y = H - BOT_H
        p.setPen(QPen(QColor(W_BORDER), 1))
        p.drawLine(8, sep_y, W - 8, sep_y)

        # État
        state_str = "*  ON" if on else "o  OFF"
        state_c   = A_GREEN if on else W_TEXT_DIM
        p.setFont(QFont(FONT_UI, 11, QFont.Weight.Bold))
        p.setPen(QPen(QColor(state_c)))
        p.drawText(0, sep_y + 4, W, 22, Qt.AlignmentFlag.AlignCenter, state_str)
        if on:
            spd_c = A_TEAL if self._speed == "Speed2" else A_GREEN
            p.setFont(QFont(FONT_UI, 10))
            p.setPen(QPen(QColor(spd_c)))
            p.drawText(0, sep_y + 24, W, 20, Qt.AlignmentFlag.AlignCenter, self._speed)


# ═══════════════════════════════════════════════════════════
#  ESSUIE-GLACE (vue parebrise)
# ═══════════════════════════════════════════════════════════
class WindshieldWidget(QWidget):
    """
    Vue pare-brise temps réel synchronisée avec le BCM.

    Sources de vérité (set_bcm_state) :
      - front_motor_on    : True  = lame en mouvement (BCM actionne RL2)
      - rest_contact_raw  : True  = GPIO=1 = lame EN MOUVEMENT
                            False = GPIO=0 = lame AU REPOS (position repos)
      - front_blade_cycles: compteur cycles lame (incrémenté par _track_blade_cycle)
      - bcm_state         : état WSM (OFF/SPEED1/SPEED2/TOUCH/AUTO/WASH_FRONT/...)
      - op                : WiperOp courant (pour couleur + label)

    Logique d'animation :
      - Si front_motor_on=True  → lame animée (mouvement continu)
      - Si front_motor_on=False → lame retourne en position repos (angle=-60)
      - rest_contact_raw=False (repos) → lame dessinée en vert (position repos confirmée)
      - rest_contact_raw=True  (mouvement) → lame dessinée en couleur WOP active
    """
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._op              = 0
        self._angle           = -60.0
        self._dir             = 1
        self._front_motor_on  = False
        self._rest_contact    = False   # True=GPIO1=lame bouge / False=GPIO0=repos
        self._blade_cycles    = 0
        self._bcm_state       = "OFF"
        self._returning       = False   # True = lame en retour vers repos (motor_on→False)

        self._t = QTimer()
        self._t.timeout.connect(self._tick)
        self._t.start(20)               # 50 Hz — animation fluide
        self.setMinimumSize(280, 160)

    # ── API publique ──────────────────────────────────────────────────

    def set_op(self, op: int) -> None:
        """Appelé par le sélecteur manuel — mise à jour op uniquement."""
        self._op = op

    def set_bcm_state(self, front_motor_on: bool, rest_contact_raw: bool,
                      blade_cycles: int, bcm_state: str, op: int) -> None:
        """
        Mise à jour depuis les données temps réel du BCM.
        Appelé à chaque réception TCP/Redis (200ms).
        """
        prev_motor = self._front_motor_on
        self._front_motor_on = front_motor_on
        self._rest_contact   = rest_contact_raw
        self._blade_cycles   = blade_cycles
        self._bcm_state      = bcm_state
        self._op             = op

        # Moteur vient de s'arrêter → déclencher retour repos
        if prev_motor and not front_motor_on:
            self._returning = True

    # ── Tick animation ────────────────────────────────────────────────

    def _tick(self) -> None:
        if self._front_motor_on:
            # Lame en mouvement : animation continue selon vitesse état BCM
            self._returning = False
            spd = {
                "SPEED1":      1.8,
                "SPEED2":      3.5,
                "TOUCH":       2.0,
                "AUTO":        2.2,
                "WASH_FRONT":  2.0,
            }.get(self._bcm_state, 2.0)

            self._angle += spd * self._dir
            if self._angle >= 60:
                self._angle = 60.0; self._dir = -1
            elif self._angle <= -60:
                self._angle = -60.0; self._dir = 1

        elif self._returning:
            # Moteur arrêté → retour progressif en position repos (-60°)
            if self._angle > -60.0:
                self._angle = max(-60.0, self._angle - 3.0)
            else:
                self._angle   = -60.0
                self._returning = False

        self.update()

    # ── Dessin ────────────────────────────────────────────────────────

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        # Carrosserie / contour pare-brise
        path = QPainterPath()
        path.moveTo(W * 0.08, H * 0.95)
        path.quadTo(W * 0.03, H * 0.02, W * 0.18, H * 0.05)
        path.lineTo(W * 0.82, H * 0.05)
        path.quadTo(W * 0.97, H * 0.02, W * 0.92, H * 0.95)
        path.closeSubpath()

        p.fillRect(0, 0, W, H, QBrush(QColor("#D0D8E0")))
        glass_g = QLinearGradient(0, 0, 0, H)
        glass_g.setColorAt(0, QColor(200, 220, 255, 180))
        glass_g.setColorAt(1, QColor(180, 200, 240, 120))
        p.setBrush(QBrush(glass_g)); p.setPen(QPen(QColor("#4A5A6A"), 2))
        p.drawPath(path)

        cx = W // 2; cy = int(H * 0.94); R = int(H * 0.82)

        # Zone de balayage
        sw_c = QColor(A_GREEN if self._front_motor_on else "#B0B3B5")
        sw_c.setAlpha(25)
        p.setBrush(QBrush(sw_c)); p.setPen(Qt.PenStyle.NoPen)
        p.drawPie(QRectF(cx - R, cy - R, R * 2, R * 2),
                  int((-60 + 90) * 16), int(-120 * 16))

        # Couleur lame selon rest contact
        # rest_contact=False (GPIO=0) = lame AU REPOS → vert
        # rest_contact=True  (GPIO=1) = lame EN MOUVEMENT → couleur WOP
        if not self._front_motor_on and not self._rest_contact:
            wiper_c = QColor(A_GREEN)    # repos confirmé par hardware
        elif self._front_motor_on:
            wiper_c = QColor(WOP[self._op]["color"]) if self._op > 0 else QColor("#E0A000")
        else:
            wiper_c = QColor("#888888")  # arrêté mais rest contact pas encore confirmé

        ang_r = math.radians(self._angle - 90)
        ex = cx + R * math.cos(ang_r); ey = cy + R * math.sin(ang_r)

        # Ombre bras
        p.setPen(QPen(QColor(0, 0, 0, 30), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(cx + 2, cy + 2, int(ex + 2), int(ey + 2))
        # Bras
        p.setPen(QPen(QColor("#505050"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(cx, cy, int(ex), int(ey))
        # Lame
        perp = math.radians(self._angle - 90 + 90); bl = 32
        bx1 = ex + bl * math.cos(perp); by1 = ey + bl * math.sin(perp)
        bx2 = ex - bl * math.cos(perp); by2 = ey - bl * math.sin(perp)
        p.setPen(QPen(wiper_c, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(int(bx1), int(by1), int(bx2), int(by2))

        # Point pivot
        p.setBrush(QBrush(QColor("#303030"))); p.setPen(QPen(QColor("#1A1A1A"), 1.5))
        p.drawEllipse(cx - 5, cy - 5, 10, 10)

        # Indicateur rest contact (petit point coin bas-gauche)
        rc_color = QColor(A_GREEN) if not self._rest_contact else QColor(A_AMBER)
        p.setBrush(QBrush(rc_color)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(6, H - 16, 10, 10)
        p.setFont(QFont(FONT_MONO, 8))
        p.setPen(QPen(QColor(W_TEXT_DIM)))
        p.drawText(20, H - 16, 60, 12, Qt.AlignmentFlag.AlignLeft, "REST")

        # Compteur cycles (coin bas-droit)
        p.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
        p.setPen(QPen(QColor(A_TEAL2)))
        p.drawText(W - 70, H - 16, 66, 12,
                   Qt.AlignmentFlag.AlignRight, f"#{self._blade_cycles}")

        # Label état BCM (centré en bas)
        if self._bcm_state not in ("OFF", ""):
            lbl_c = QColor(WOP[self._op]["color"]) if self._op > 0 else QColor(W_TEXT_DIM)
            p.setFont(QFont(FONT_UI, 10, QFont.Weight.Bold))
            p.setPen(QPen(lbl_c))
            p.drawText(4, H - 18, W - 8, 16, Qt.AlignmentFlag.AlignCenter,
                       self._bcm_state)


# ═══════════════════════════════════════════════════════════
#  BMW M4 WIDEBODY — Vue 3D rotative (drag souris)
# ═══════════════════════════════════════════════════════════
class CarTopViewWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._ign     = "OFF"
        self._reverse = False
        self._speed   = 0.0
        self._rain    = 0

        self._yaw          = 0.0
        self._pitch        = 70.0
        self._drag         = False
        self._last_mouse   = QPointF(0, 0)
        self._rot_inertia  = 0.0

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
        self._t = QTimer(); self._t.timeout.connect(self._tick); self._t.start(60)  # Optimisation: 60ms = ~16 FPS (au lieu de 28ms)

    # ── Setters ──────────────────────────────────────────────
    def set_ignition(self, s: str)  -> None: self._ign     = s
    def set_reverse(self, r: bool)  -> None: self._reverse = bool(r)
    def set_speed(self, spd: float) -> None: self._speed   = spd
    def set_rain(self, rain: int)   -> None: self._rain    = rain

    # ── Souris ───────────────────────────────────────────────
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = True
            self._last_mouse = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseMoveEvent(self, e) -> None:
        if self._drag:
            dx = e.position().x() - self._last_mouse.x()
            dy = e.position().y() - self._last_mouse.y()
            self._yaw   = (self._yaw + dx * 0.55) % 360
            self._pitch = max(15.0, min(90.0, self._pitch - dy * 0.35))
            self._rot_inertia = dx * 0.3
            self._last_mouse  = e.position()

    def wheelEvent(self, e) -> None:
        delta = e.angleDelta().y() / 120.0
        self._pitch = max(15.0, min(90.0, self._pitch + delta * 3.0))
        self.update()

    # ── Animation ────────────────────────────────────────────
    def _tick(self) -> None:
        on = self._ign == "ON"
        if not self._drag and abs(self._rot_inertia) > 0.05:
            self._yaw = (self._yaw + self._rot_inertia) % 360
            self._rot_inertia *= 0.92
        if on:
            spd = max(self._speed / 22.0, 0.18 if self._speed > 0 else 0)
            self._wheel_angle = (self._wheel_angle + (-6 if self._reverse else 6) * spd) % 360
            tgt = 12 if self._reverse else -12
            self._car_offset += (tgt - self._car_offset) * 0.08
        else:
            self._car_offset *= 0.88
        if on:
            self._vibe += 0.45 * self._vibe_dir
            if abs(self._vibe) > 1.0: self._vibe_dir *= -1
        else:
            self._vibe *= 0.75
        self._arrow_phase = (self._arrow_phase + 0.07) % 1.0
        if on and random.random() < 0.4:
            for sx in (-1, 1):
                self._exhaust.append([0.0, sx, random.uniform(0.5, 0.95)])
        self._exhaust = [[a + 0.05, s, op - 0.032]
                         for a, s, op in self._exhaust if op > 0]
        if self._rain > 0:
            for _ in range(max(1, int(self._rain / 16))):
                if len(self._rain_drops) < 80:
                    self._rain_drops.append([
                        random.uniform(0, 1), random.uniform(0, 0.2),
                        random.uniform(0.02, 0.055), random.uniform(0.3, 0.75)])
            self._rain_drops = [[x, y + sp, sp, op]
                                for x, y, sp, op in self._rain_drops if y < 1.1]
        else:
            self._rain_drops.clear()
        if self._rain > 10 and on:
            ws = 2.0 + self._rain / 28.0
            self._wiper_angle += ws * self._wiper_dir
            if self._wiper_angle >  28: self._wiper_angle =  28; self._wiper_dir = -1
            if self._wiper_angle < -28: self._wiper_angle = -28; self._wiper_dir =  1
        self.update()

    # ── Projection 3D → 2D ───────────────────────────────────
    def _project(self, cx, cy, scale, lx, ly, lz=0.0):
        yr = math.radians(self._yaw); pr = math.radians(self._pitch)
        rx = lx * math.cos(yr) - ly * math.sin(yr)
        ry = lx * math.sin(yr) + ly * math.cos(yr)
        pc = math.cos(pr); ps = math.sin(pr)
        fy = ry * ps - lz * pc
        off_y = self._car_offset * (ps / 90.0) * 0.5
        sx = int(cx + fy * scale)
        sy = int(cy + rx * scale + off_y + self._vibe * ps * 0.3)
        return sx, sy

    def _p3(self, cx, cy, scale, pts3):
        return QPolygonF([QPointF(*self._project(cx, cy, scale, x, y, z))
                          for x, y, z in pts3])

    def _path3(self, cx, cy, scale, cmds):
        path = QPainterPath()
        for item in cmds:
            cmd = item[0]
            if cmd == "M":
                path.moveTo(*self._project(cx, cy, scale, *item[1:]))
            elif cmd == "L":
                path.lineTo(*self._project(cx, cy, scale, *item[1:]))
            elif cmd == "Q":
                if len(item) < 6:
                    path.lineTo(*self._project(cx, cy, scale, item[1], item[2],
                                               item[3] if len(item) > 3 else 0))
                else:
                    cx2, cy2 = self._project(cx, cy, scale, item[1], item[2],
                                             item[3] if len(item) > 3 else 0)
                    ex, ey   = self._project(cx, cy, scale, item[4], item[5],
                                             item[6] if len(item) > 6 else 0)
                    path.quadTo(cx2, cy2, ex, ey)
            elif cmd == "C":
                c1x, c1y = self._project(cx, cy, scale, item[1], item[2],
                                         item[3] if len(item) > 3 else 0)
                c2x, c2y = self._project(cx, cy, scale, item[4], item[5],
                                         item[6] if len(item) > 6 else 0)
                ex, ey   = self._project(cx, cy, scale, item[7], item[8],
                                         item[9] if len(item) > 9 else 0)
                path.cubicTo(c1x, c1y, c2x, c2y, ex, ey)
            elif cmd == "Z":
                path.closeSubpath()
        return path

    def _draw_wheel(self, p, cx2, cy2, scale, lx, ly, flip=1):
        ww = scale * 0.24; wh = scale * 0.44
        corners = [(lx - 0.22, ly, 0.18), (lx + 0.22, ly, 0.18),
                   (lx + 0.22, ly, -0.18), (lx - 0.22, ly, -0.18)]
        poly = self._p3(cx2, cy2, scale, corners)
        tg = QLinearGradient(poly.at(0).x(), poly.at(0).y(),
                              poly.at(1).x(), poly.at(1).y())
        tg.setColorAt(0, QColor("#0A0A0A")); tg.setColorAt(0.45, QColor("#242424"))
        tg.setColorAt(0.55, QColor("#181818")); tg.setColorAt(1, QColor("#0A0A0A"))
        p.setBrush(QBrush(tg)); p.setPen(QPen(QColor("#050505"), 1.5))
        p.drawPolygon(poly)
        rcx, rcy = self._project(cx2, cy2, scale, lx, ly, 0)
        rrx = int(ww * 0.45 * abs(math.sin(math.radians(self._yaw)) * 0.5 + 0.5) + ww * 0.18)
        rry = int(wh * 0.41)
        gr = QRadialGradient(rcx, rcy, max(rrx, rry))
        gr.setColorAt(0, QColor("#C8D0D8")); gr.setColorAt(0.55, QColor("#707880"))
        gr.setColorAt(1, QColor("#383E44"))
        p.setBrush(QBrush(gr)); p.setPen(QPen(QColor("#282E34"), 0.8))
        p.drawEllipse(rcx - rrx, rcy - rry, rrx * 2, rry * 2)
        for i in range(5):
            a = math.radians(self._wheel_angle * flip + i * 72)
            ex2 = rcx + int((rrx - 1) * math.cos(a))
            ey2 = rcy + int((rry - 1) * math.sin(a))
            p.setPen(QPen(QColor("#A0A8B0"), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(rcx, rcy, ex2, ey2)
            a2 = math.radians(self._wheel_angle * flip + i * 72 + 9)
            ex3 = rcx + int((rrx - 1) * math.cos(a2))
            ey3 = rcy + int((rry - 1) * math.sin(a2))
            p.setPen(QPen(QColor("#808890"), 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(rcx, rcy, ex3, ey3)
        p.setBrush(QBrush(QColor("#1A2030"))); p.setPen(QPen(QColor("#101820"), 0.8))
        p.drawEllipse(rcx - 4, rcy - 4, 8, 8)

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.fillRect(0, 0, W, H, QBrush(QColor("#1E2228")))
        p.setPen(QPen(QColor("#262C34"), 1, Qt.PenStyle.DashLine))
        for x in range(0, W, 30): p.drawLine(x, 0, x, H)
        for y in range(0, H, 30): p.drawLine(0, y, W, y)

        for rx, ry, _, op in self._rain_drops:
            rc = QColor(A_TEAL); rc.setAlphaF(op * 0.4)
            p.setPen(QPen(rc, 1))
            px2 = int(rx * W); py2 = int(ry * H)
            p.drawLine(px2, py2, px2 - 1, py2 + 7)

        margin = 48
        scale  = min((H - margin * 2) / 3.2, (W - margin * 2) / 2.0, 78)
        cx, cy = W // 2, H // 2
        on  = self._ign == "ON"
        acc = self._ign == "ACC"
        off = self._ign == "OFF"

        def pr(lx, ly, lz=0.0): return self._project(cx, cy, scale, lx, ly, lz)
        def pg(pts3):            return self._p3(cx, cy, scale, pts3)
        def ph(*cmds):           return self._path3(cx, cy, scale, cmds)

        body_col = QColor("#4A5058") if off else (
                   QColor("#2A3A50") if acc else QColor("#1C2A3C"))

        yaw_r   = math.radians(self._yaw)
        light_x = math.cos(yaw_r + math.pi / 4)
        spec    = 0.5 + 0.5 * light_x

        # Ombre
        p.setBrush(QBrush(QColor(0, 0, 0, 50))); p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(pg([(-1.55, -0.85, -0.05), (1.60, -0.85, -0.05),
                          (1.60, 0.85, -0.05), (-1.55, 0.85, -0.05)]))

        # Carrosserie
        body = ph(
            ("M",-1.50,0.00,0.04),("Q",-1.52,0.30,0.04,-1.45,0.55,0.06),
            ("Q",-1.38,0.68,0.06,-1.20,0.72,0.08),("Q",-0.80,0.82,0.10,-0.50,0.84,0.12),
            ("Q",0.00,0.80,0.10,0.40,0.82,0.10),("Q",0.70,0.86,0.12,0.95,0.88,0.12),
            ("Q",1.20,0.84,0.10,1.40,0.72,0.08),("Q",1.52,0.55,0.06,1.55,0.28,0.04),
            ("L",1.55,0.00,0.04),("L",1.55,-0.28,0.04),
            ("Q",1.52,-0.55,0.06,1.40,-0.72,0.08),("Q",1.20,-0.84,0.10,0.95,-0.88,0.12),
            ("Q",0.70,-0.86,0.12,0.40,-0.82,0.10),("Q",0.00,-0.80,0.10,-0.50,-0.84,0.12),
            ("Q",-0.80,-0.82,0.10,-1.20,-0.72,0.08),("Q",-1.38,-0.68,0.06,-1.45,-0.55,0.06),
            ("Q",-1.52,-0.30,0.04,-1.50,0.00,0.04),("Z",),
        )
        bx0, by0 = pr(-0.82, 0, 0.08); bx1, by1 = pr(0.82, 0, 0.08)
        gbd = QLinearGradient(bx0, by0, bx1, by1)
        gbd.setColorAt(0,    body_col.darker(int(150 - spec * 20)))
        gbd.setColorAt(0.18, body_col.lighter(int(105 + spec * 15)))
        gbd.setColorAt(0.45, body_col.lighter(int(140 + spec * 25)))
        gbd.setColorAt(0.55, body_col.lighter(int(155 + spec * 20)))
        gbd.setColorAt(0.82, body_col.lighter(int(108 + spec * 10)))
        gbd.setColorAt(1,    body_col.darker(int(148 - spec * 15)))
        p.setBrush(QBrush(gbd)); p.setPen(QPen(body_col.darker(200), 1.2))
        p.drawPath(body)

        # Capot
        hood = ph(
            ("M",-1.50,0.00,0.04),("Q",-1.48,0.50,0.08,-1.30,0.62,0.10),
            ("Q",-1.00,0.62,0.12,-0.68,0.56,0.14),("Q",-0.50,0.48,0.15,-0.42,0.00,0.16),
            ("Q",-0.50,-0.48,0.15,-0.68,-0.56,0.14),("Q",-1.00,-0.62,0.12,-1.30,-0.62,0.10),
            ("Q",-1.48,-0.50,0.08,-1.50,0.00,0.04),("Z",),
        )
        hx0, hy0 = pr(-1.50, 0, 0.08); hx1, hy1 = pr(-0.42, 0, 0.16)
        gh = QLinearGradient(hx0, hy0, hx1, hy1)
        gh.setColorAt(0, body_col.darker(160))
        gh.setColorAt(0.25, body_col.lighter(int(112 + spec * 20)))
        gh.setColorAt(0.55, body_col.lighter(int(188 + spec * 15)))
        gh.setColorAt(0.85, body_col.lighter(int(110 + spec * 10)))
        gh.setColorAt(1, body_col.darker(140))
        p.setBrush(QBrush(gh)); p.setPen(QPen(body_col.darker(170), 0.8))
        p.drawPath(hood)

        # Dômes puissance
        for sy in (-0.14, 0.14):
            dome = ph(
                ("M",-1.46,sy-0.06,0.04),("Q",-1.00,sy-0.07,0.14,-0.46,sy-0.05,0.16),
                ("Q",-0.46,sy+0.05,0.16,-1.00,sy+0.07,0.14),
                ("Q",-1.46,sy+0.06,0.04,-1.46,sy-0.06,0.04),("Z",),
            )
            p.setBrush(QBrush(body_col.lighter(200))); p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(dome)

        # Toit
        roof = ph(
            ("M",-0.42,0.00,0.16),("Q",-0.40,0.44,0.20,-0.20,0.52,0.52),
            ("Q",0.00,0.50,0.60,0.30,0.48,0.62),("Q",0.65,0.44,0.58,0.80,0.38,0.50),
            ("Q",0.88,0.30,0.38,0.90,0.00,0.28),("Q",0.88,-0.30,0.38,0.80,-0.38,0.50),
            ("Q",0.65,-0.44,0.58,0.30,-0.48,0.62),("Q",0.00,-0.50,0.60,-0.20,-0.52,0.52),
            ("Q",-0.40,-0.44,0.20,-0.42,0.00,0.16),("Z",),
        )
        rx0, ry0 = pr(0, 0, 0.55); rx1, ry1 = pr(0.45, 0, 0.55)
        rc_dark = QColor("#0A1422") if on else (QColor("#141E2C") if acc else QColor("#1E2630"))
        groof = QLinearGradient(rx0, ry0, rx1, ry1)
        for stop, fac in [(0, 1), (0.25, 200), (0.50, 280), (0.75, 190), (1, 1)]:
            groof.setColorAt(stop, rc_dark.lighter(fac) if fac > 1 else rc_dark)
        p.setBrush(QBrush(groof)); p.setPen(QPen(QColor("#050C14"), 1))
        p.drawPath(roof)

        # Pare-brise
        wsf = ph(
            ("M",-0.42,0.44,0.20),("Q",-0.38,0.50,0.22,-0.20,0.52,0.52),
            ("Q",0.00,0.50,0.60,0.00,-0.50,0.60),("Q",-0.20,-0.52,0.52,-0.38,-0.50,0.22),
            ("Q",-0.42,-0.44,0.20,-0.42,0.44,0.20),("Z",),
        )
        gwsf = QLinearGradient(*pr(-0.42, 0.44, 0.20), *pr(-0.20, 0.52, 0.52))
        gwsf.setColorAt(0, QColor(140, 195, 230, 170)); gwsf.setColorAt(1, QColor(80, 145, 190, 95))
        p.setBrush(QBrush(gwsf)); p.setPen(QPen(QColor("#1A4060"), 0.8))
        p.drawPath(wsf)

        # Essuie-glace 3D
        if self._rain > 10 and on:
            wpx, wpy = pr(-0.10, 0, 0.38)
            wr2  = int(scale * 0.40)
            wa_r = math.radians(self._wiper_angle * 1.6)
            wex  = wpx + int(wr2 * math.sin(wa_r))
            wey  = wpy + int(wr2 * math.cos(wa_r))
            p.setPen(QPen(QColor("#101010"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(wpx, wpy, wex, wey)
            p.setPen(QPen(QColor(A_TEAL), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(wpx, wpy, wex, wey)

        # Lunette arrière
        wsr = ph(
            ("M",0.80,0.38,0.50),("Q",0.65,0.44,0.58,0.30,0.48,0.62),
            ("Q",0.30,-0.48,0.62,0.65,-0.44,0.58),("Q",0.80,-0.38,0.50,0.80,0.38,0.50),("Z",),
        )
        gwsr = QLinearGradient(*pr(0.65, 0, 0.58), *pr(0.80, 0, 0.50))
        gwsr.setColorAt(0, QColor(80, 145, 190, 95)); gwsr.setColorAt(1, QColor(140, 195, 230, 160))
        p.setBrush(QBrush(gwsr)); p.setPen(QPen(QColor("#1A4060"), 0.8)); p.drawPath(wsr)

        # Coffre
        trunk = ph(
            ("M",0.90,0.00,0.14),("Q",0.88,0.35,0.16,0.80,0.38,0.50),
            ("Q",0.80,-0.38,0.50,0.88,-0.35,0.16),("L",0.90,0.00,0.14),("Z",),
        )
        tx0, ty0 = pr(0.88, 0, 0.16); tx1, ty1 = pr(0.80, 0, 0.50)
        gtrunk = QLinearGradient(tx0, ty0, tx1, ty1)
        gtrunk.setColorAt(0, body_col.darker(140)); gtrunk.setColorAt(0.5, body_col.lighter(140))
        gtrunk.setColorAt(1, body_col.darker(130))
        p.setBrush(QBrush(gtrunk)); p.setPen(QPen(body_col.darker(175), 0.8)); p.drawPath(trunk)

        # Aileron GT
        wing_blade = ph(
            ("M",1.35,-0.85,0.58),("Q",1.40,-0.85,0.60,1.45,0.00,0.62),
            ("Q",1.40,0.85,0.60,1.35,0.85,0.58),("Q",1.30,0.82,0.55,1.28,0.00,0.54),
            ("Q",1.30,-0.82,0.55,1.35,-0.85,0.58),("Z",),
        )
        wg = QLinearGradient(*pr(1.35, -0.85, 0.60), *pr(1.35, 0.85, 0.60))
        wg.setColorAt(0, QColor("#181C22")); wg.setColorAt(0.45, QColor("#3A4048"))
        wg.setColorAt(0.55, QColor("#2A3038")); wg.setColorAt(1, QColor("#181C22"))
        p.setBrush(QBrush(wg)); p.setPen(QPen(QColor("#0A0E14"), 1)); p.drawPath(wing_blade)

        # Rétroviseurs
        for sy in (-0.78, 0.78):
            mirror = ph(
                ("M",-0.60,sy,0.38),("Q",-0.62,sy*1.08,0.36,-0.58,sy*1.12,0.34),
                ("Q",-0.50,sy*1.10,0.34,-0.48,sy,0.36),
                ("Q",-0.52,sy*0.96,0.38,-0.60,sy,0.38),("Z",),
            )
            mg = QLinearGradient(*pr(-0.60, sy, 0.38), *pr(-0.55, sy * 1.1, 0.34))
            mg.setColorAt(0, body_col.darker(150)); mg.setColorAt(1, body_col.lighter(120))
            p.setBrush(QBrush(mg)); p.setPen(QPen(body_col.darker(180), 0.8)); p.drawPath(mirror)

        # Grilles M4
        for sy in (-0.18, 0.18):
            grille = ph(
                ("M",-1.52,sy-0.14,0.04),("Q",-1.55,sy-0.14,0.06,-1.56,sy,0.08),
                ("Q",-1.55,sy+0.14,0.06,-1.52,sy+0.14,0.04),
                ("Q",-1.46,sy+0.12,0.04,-1.45,sy,0.04),
                ("Q",-1.46,sy-0.12,0.04,-1.52,sy-0.14,0.04),("Z",),
            )
            p.setBrush(QBrush(QColor("#08090C"))); p.setPen(QPen(QColor("#1A2030"), 0.8))
            p.drawPath(grille)

        # Roues
        for lx, ly, flip in [(-0.88, -0.82, 1), (-0.88, 0.82, -1),
                               (0.95, -0.82, 1),  (0.95, 0.82, -1)]:
            self._draw_wheel(p, cx, cy, scale, lx, ly, flip)

        # Phares avant
        for sy in (-0.42, 0.42):
            hx2, hy2 = pr(-1.52, sy, 0.06)
            if on:
                halo = QRadialGradient(hx2, hy2, int(scale * 0.24))
                halo.setColorAt(0, QColor(255, 255, 230, 200))
                halo.setColorAt(1, QColor(255, 255, 200, 0))
                p.setBrush(QBrush(halo)); p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(hx2 - int(scale * 0.24), hy2 - int(scale * 0.12),
                              int(scale * 0.48), int(scale * 0.24))
                p.setBrush(QBrush(QColor("#F2F6FF")))
                p.setPen(QPen(QColor("#B0B8C8"), 0.8))
            elif acc:
                p.setBrush(QBrush(QColor("#FF8C00"))); p.setPen(QPen(QColor("#CC6600"), 0.8))
            else:
                p.setBrush(QBrush(QColor("#141820"))); p.setPen(QPen(QColor("#0C1018"), 0.8))
            hw = int(scale * 0.28); hh = int(scale * 0.12)
            p.drawEllipse(hx2 - hw // 2, hy2 - hh // 2, hw, hh)
            drl_c = QColor("#FFFFC0") if on else (QColor("#FFA020") if acc else QColor("#111820"))
            p.setPen(QPen(drl_c, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            dl = int(scale * 0.15)
            p.drawLine(hx2 - dl, hy2 - int(scale * 0.10), hx2 + dl, hy2 - int(scale * 0.10))
            p.drawLine(hx2 + dl, hy2 - int(scale * 0.10), hx2 + dl, hy2 + int(scale * 0.02))

        # Feux arrière
        for sy in (-0.42, 0.42):
            tx2, ty2 = pr(1.52, sy, 0.06)
            if on or acc:
                halo = QRadialGradient(tx2, ty2, int(scale * 0.20))
                halo.setColorAt(0, QColor(220, 0, 0, 200)); halo.setColorAt(1, QColor(180, 0, 0, 0))
                p.setBrush(QBrush(halo)); p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(tx2 - int(scale * 0.20), ty2 - int(scale * 0.10),
                              int(scale * 0.40), int(scale * 0.20))
                p.setBrush(QBrush(QColor("#E01818"))); p.setPen(QPen(QColor("#800000"), 0.8))
            else:
                p.setBrush(QBrush(QColor("#3C0808"))); p.setPen(QPen(QColor("#200404"), 0.8))
            tw = int(scale * 0.24); th = int(scale * 0.10)
            p.drawEllipse(tx2 - tw // 2, ty2 - th // 2, tw, th)

        # Feux de recul
        if self._reverse and on:
            for sy in (-0.18, 0.18):
                rx3, ry3 = pr(1.52, sy, 0.06)
                halo = QRadialGradient(rx3, ry3, int(scale * 0.14))
                halo.setColorAt(0, QColor(255, 255, 255, 220))
                halo.setColorAt(1, QColor(255, 255, 255, 0))
                p.setBrush(QBrush(halo)); p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(rx3 - int(scale * 0.14), ry3 - int(scale * 0.08),
                              int(scale * 0.28), int(scale * 0.16))

        # Echappements + fumée
        for sy in (-0.32, 0.32):
            ex2, ey2 = pr(1.55, sy, 0.00)
            p.setBrush(QBrush(QColor("#303840"))); p.setPen(QPen(QColor("#1A2028"), 0.8))
            p.drawEllipse(ex2 - int(scale * 0.05), ey2 - int(scale * 0.05),
                          int(scale * 0.10), int(scale * 0.10))
        for adv, sx2, op in self._exhaust:
            ebx, eby = pr(1.55, sx2 * 0.32, 0.00)
            r_e = int(3 + adv * 25)
            smoke = QColor(180, 185, 192); smoke.setAlphaF(max(0, op * 0.25))
            p.setBrush(QBrush(smoke)); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(ebx - r_e, int(eby + adv * 28) - r_e, r_e * 2, r_e * 2)

        # Flèches direction
        if self._reverse and on:
            for ai in range(3):
                ph2 = (self._arrow_phase * 3 + ai) % 3
                alpha = int(255 * max(0, 1.0 - abs(ph2 - 1.0)))
                ac = QColor(A_ORANGE); ac.setAlpha(alpha)
                ax2, ay2 = pr(1.75 + ai * 0.20, 0, 0.04)
                pts3 = QPolygonF([QPointF(ax2 + int(scale * 0.13), ay2),
                                   QPointF(ax2, ay2 - int(scale * 0.15)),
                                   QPointF(ax2, ay2 + int(scale * 0.15))])
                p.setBrush(QBrush(ac)); p.setPen(Qt.PenStyle.NoPen); p.drawPolygon(pts3)
        if not self._reverse and on and self._speed > 3:
            for ai in range(3):
                ph2 = (self._arrow_phase * 3 + ai) % 3
                alpha = int(255 * max(0, 1.0 - abs(ph2 - 1.0)))
                ac = QColor(A_GREEN); ac.setAlpha(alpha)
                ax2, ay2 = pr(-1.75 - ai * 0.20, 0, 0.04)
                pts3 = QPolygonF([QPointF(ax2 - int(scale * 0.13), ay2),
                                   QPointF(ax2, ay2 - int(scale * 0.15)),
                                   QPointF(ax2, ay2 + int(scale * 0.15))])
                p.setBrush(QBrush(ac)); p.setPen(Qt.PenStyle.NoPen); p.drawPolygon(pts3)

        # HUD bas
        bw2 = 158; bh2 = 24; bx3 = (W - bw2) // 2; by3 = H - 28
        p.setBrush(QBrush(QColor(0, 0, 0, 155))); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(bx3, by3, bw2, bh2, 6, 6)
        lmap2 = {"OFF": ("ENGINE  OFF", "#888888"),
                 "ACC": ("ACCESSORY", A_ORANGE),
                 "ON":  ("ENGINE  ON", A_GREEN)}
        lt2, lc2 = ("REVERSE  <", A_ORANGE) if (self._reverse and on) \
                   else lmap2.get(self._ign, ("OFF", "#888888"))
        p.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
        p.setPen(QPen(QColor(lc2)))
        p.drawText(bx3, by3, bw2, bh2, Qt.AlignmentFlag.AlignCenter, lt2)
        if on and self._speed > 0:
            sw3 = 62; sx4 = bx3 + bw2 + 5
            p.setBrush(QBrush(QColor(0, 0, 0, 130))); p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(sx4, by3, sw3, bh2, 6, 6)
            p.setFont(QFont(FONT_MONO, 9, QFont.Weight.Bold))
            p.setPen(QPen(QColor(A_TEAL)))
            p.drawText(sx4, by3, sw3, bh2, Qt.AlignmentFlag.AlignCenter,
                       f"{self._speed:.0f} km/h")

        p.setFont(QFont(FONT_UI, 7))
        p.setPen(QPen(QColor(80, 85, 95)))
        p.drawText(6, H - 12, W - 12, 12, Qt.AlignmentFlag.AlignCenter,
                   "drag to rotate  .  scroll to tilt")
