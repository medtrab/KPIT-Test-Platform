import sys
import math
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt, QPoint, QRect, QTimer
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QRadialGradient,
    QLinearGradient, QPainterPath, QFontMetrics, QConicalGradient
)

# OPTIONS aligné sur WOP de constants.py :
# 0=OFF  1=TOUCH  2=SPEED1  3=SPEED2  4=AUTO  5=FRONT_WASH  6=REAR_WASH  7=REAR_WIPE
OPTIONS = ["OFF", "TOUCH", "SPEED 1", "SPEED 2", "AUTO", "FRONTWASH", "REARWASH", "REARWIPE"]

OPTION_COLORS = {
    "OFF":       (80,  80,  90),
    "TOUCH":     (0,   200, 220),
    "SPEED 1":   (0,   180, 255),
    "SPEED 2":   (0,   100, 255),
    "AUTO":      (0,   220, 160),
    "FRONTWASH": (60,  140, 255),
    "REARWASH":  (140, 60,  255),
    "REARWIPE":  (220, 80,  255),
}

ICONS = {
    "OFF":       "⏻",
    "TOUCH":     "①",
    "SPEED 1":   "〜",
    "SPEED 2":   "≋",
    "AUTO":      "⟳",
    "FRONTWASH": "↑≋",
    "REARWASH":  "≋↓",
    "REARWIPE":  "↩",
}

# Couleur verte signature - version plus pâle pour l'extérieur
GREEN_ACCENT = QColor(141, 198, 63)  # #8DC63F
GREEN_PALE = QColor(181, 218, 123)   # Version plus pâle pour l'extérieur
GREEN_VERY_PALE = QColor(201, 228, 158)  # Version très pâle

def qc(rgb, alpha=255):
    return QColor(rgb[0], rgb[1], rgb[2], alpha)


class CarComodo3D(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Comodo 3D — Voiture")
        self.setFixedSize(600, 660)
        
        # Fond vert très pâle
        self.setStyleSheet("background-color: #e8f4d4;")
        
        self.setMouseTracking(True)

        self.selected_index = 0
        self.hover_index    = -1
        self.anim_t         = [0.0] * len(OPTIONS)
        self.anim_t[0]      = 1.0
        self.spin_angle     = 0.0
        self.target_angle   = 0.0
        self.drag_start_mouse_angle = None
        self.drag_start_spin        = None

        self.idle_pulse = 0.0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(14)

    def _cx(self): 
        return self.width() // 2
    
    def _cy(self): 
        return self.height() // 2 - 10

    def _step(self): 
        return 360.0 / len(OPTIONS)

    def _node_angle(self, idx):
        return -90.0 + idx * self._step() + self.spin_angle

    def _mouse_angle(self, mx, my):
        return math.degrees(math.atan2(my - self._cy(), mx - self._cx()))

    def _nearest_index(self):
        best, best_d = 0, 999
        for i in range(len(OPTIONS)):
            a = self._node_angle(i) % 360
            d = abs((a + 90) % 360)
            if d > 180: 
                d = 360 - d
            if d < best_d:
                best_d = d
                best = i
        return best

    def _snap_target(self, idx):
        return -(idx * self._step())

    def _tick(self):
        diff = self.target_angle - self.spin_angle
        while diff > 180: 
            diff -= 360
        while diff < -180: 
            diff += 360
        self.spin_angle += diff * 0.18

        for i in range(len(OPTIONS)):
            t = 1.0 if i == self.selected_index else 0.0
            self.anim_t[i] += (t - self.anim_t[i]) * 0.14

        self.idle_pulse = (self.idle_pulse + 0.03) % (2 * math.pi)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        cx, cy = self._cx(), self._cy()

        self._draw_bg(p, cx, cy)
        self._draw_shadow_plate(p, cx, cy)
        self._draw_outer_bezel(p, cx, cy)
        self._draw_ring_3d(p, cx, cy)
        self._draw_tick_marks(p, cx, cy)
        self._draw_nodes(p, cx, cy)
        self._draw_knob_3d(p, cx, cy)
        self._draw_status_bar(p, cx, cy)
        p.end()

    def _draw_bg(self, p, cx, cy):
        # Fond transparent : laissé au parent widget (CarComodo3DReadOnly gère ça)
        # On ne peint rien ici pour permettre l'intégration sur n'importe quel fond
        pass

    def _draw_shadow_plate(self, p, cx, cy):
        for off, alpha in [(30, 12), (20, 20), (12, 28), (6, 40)]:
            g = QRadialGradient(cx + 4, cy + off, 220)
            g.setColorAt(0,   QColor(0, 0, 0, alpha))
            g.setColorAt(0.7, QColor(0, 0, 0, alpha // 2))
            g.setColorAt(1,   QColor(0, 0, 0, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(QPoint(cx + 4, cy + off), 220, 220)

    def _draw_outer_bezel(self, p, cx, cy):
        R = 215

        g = QRadialGradient(cx - 40, cy - 60, R * 1.4)
        g.setColorAt(0,   QColor(45, 55, 75))
        g.setColorAt(0.5, QColor(30, 38, 55))
        g.setColorAt(1,   QColor(18, 22, 35))
        p.setBrush(QBrush(g))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPoint(cx, cy), R, R)
        
        # Anneau extérieur avec couleur verte très pâle (presque invisible)
        for width, alpha in [(3, 30), (2, 50)]:
            pen = QPen(QColor(140, 160, 180, alpha * 2), width)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            rect = QRect(cx - R + 5, cy - R + 5, (R - 5) * 2, (R - 5) * 2)
            p.drawArc(rect, 0 * 16, 360 * 16)

        for width, alpha in [(12, 25), (8, 40), (4, 70), (2, 120)]:
            pen = QPen(QColor(200, 215, 240, alpha), width)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            rect = QRect(cx - R, cy - R, R * 2, R * 2)
            p.drawArc(rect, 30 * 16, 160 * 16)

        for width, alpha in [(8, 60), (4, 100)]:
            pen = QPen(QColor(0, 0, 0, alpha), width)
            p.setPen(pen)
            p.drawArc(QRect(cx - R, cy - R, R * 2, R * 2), 200 * 16, 140 * 16)

        p.setPen(QPen(QColor(80, 95, 125), 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPoint(cx, cy), R, R)

    def _draw_ring_3d(self, p, cx, cy):
        R = 185
        ring_w = 22

        g = QConicalGradient(cx, cy, 225)
        g.setColorAt(0.00, QColor(65, 78, 100))
        g.setColorAt(0.25, QColor(35, 45, 65))
        g.setColorAt(0.50, QColor(25, 33, 50))
        g.setColorAt(0.75, QColor(55, 68, 92))
        g.setColorAt(1.00, QColor(65, 78, 100))
        pen = QPen(QBrush(g), ring_w)
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        p.drawEllipse(QPoint(cx, cy), R, R)
        
        # Anneau intérieur acier chromé
        inner_pen = QPen(QColor(90, 105, 130, 120), 1)
        inner_pen.setCapStyle(Qt.RoundCap)
        p.setPen(inner_pen)
        p.drawEllipse(QPoint(cx, cy), R - 15, R - 15)

        for w, alpha in [(10, 30), (6, 55), (3, 90)]:
            hp = QPen(QColor(180, 205, 240, alpha), w)
            hp.setCapStyle(Qt.RoundCap)
            p.setPen(hp)
            p.drawArc(QRect(cx - R, cy - R, R * 2, R * 2), 45 * 16, 110 * 16)

        for w, alpha in [(10, 60), (5, 90)]:
            sp = QPen(QColor(0, 0, 0, alpha), w)
            sp.setCapStyle(Qt.RoundCap)
            p.setPen(sp)
            p.drawArc(QRect(cx - R, cy - R, R * 2, R * 2), 215 * 16, 100 * 16)

        sel_c = qc(OPTION_COLORS[OPTIONS[self.selected_index]])
        sel_angle = self._node_angle(self.selected_index)
        pulse = 0.85 + 0.15 * math.sin(self.idle_pulse)
        for w, alpha in [(18, int(30 * pulse)), (12, int(55 * pulse)), (6, int(90 * pulse))]:
            gp = QPen(QColor(sel_c.red(), sel_c.green(), sel_c.blue(), alpha), w)
            gp.setCapStyle(Qt.RoundCap)
            p.setPen(gp)
            start = int((-sel_angle + 90 - 22) * 16)
            span  = int(44 * 16)
            p.drawArc(QRect(cx - R, cy - R, R * 2, R * 2), start, span)

    def _draw_tick_marks(self, p, cx, cy):
        R_out = 193
        for i in range(70):
            a = math.radians(i * (360 / 70))
            is_major = (i % 10 == 0)
            r_in = R_out - (11 if is_major else 5)
            x1 = cx + R_out * math.cos(a);  y1 = cy + R_out * math.sin(a)
            x2 = cx + r_in  * math.cos(a);  y2 = cy + r_in  * math.sin(a)
            # Touche de couleur très pâle sur les graduations majeures
            if is_major:
                col = QColor(130, 150, 170, 200)
            else:
                col = QColor(65, 78, 102, 100)
            p.setPen(QPen(col, 1.5 if is_major else 1))
            p.drawLine(QPoint(int(x1), int(y1)), QPoint(int(x2), int(y2)))

    def _draw_nodes(self, p, cx, cy):
        node_r = 158

        for i, opt in enumerate(OPTIONS):
            ang   = math.radians(self._node_angle(i))
            nx    = int(cx + node_r * math.cos(ang))
            ny    = int(cy + node_r * math.sin(ang))
            t     = self.anim_t[i]
            col   = qc(OPTION_COLORS[opt])
            is_sel = (i == self.selected_index)
            is_hov = (i == self.hover_index)

            size = 20 + int(5 * t)

            if t > 0.02 or is_hov:
                halo_r = int(size * 2.2)
                hg = QRadialGradient(nx, ny, halo_r)
                alpha = int(100 * t + 40 * (1 if is_hov else 0))
                # Halo dans la couleur propre du node
                mixed_color = QColor(col.red(), col.green(), col.blue(), alpha)
                hg.setColorAt(0, mixed_color)
                hg.setColorAt(0.5, QColor(col.red(), col.green(), col.blue(), alpha // 3))
                hg.setColorAt(1, QColor(0, 0, 0, 0))
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(hg))
                p.drawEllipse(QPoint(nx, ny), halo_r, halo_r)

            sg = QRadialGradient(nx - size * 0.3, ny - size * 0.35, size * 1.1)
            if is_sel:
                sg.setColorAt(0,   col.lighter(200))
                sg.setColorAt(0.4, col.lighter(130))
                sg.setColorAt(1,   col.darker(160))
            elif is_hov:
                sg.setColorAt(0,   col.lighter(160))
                sg.setColorAt(0.5, col.lighter(110))
                sg.setColorAt(1,   col.darker(180))
            else:
                dark_col = QColor(40, 48, 68)
                mid_col  = QColor(58, 70, 95)
                sg.setColorAt(0,   QColor(80, 92, 118))
                sg.setColorAt(0.5, mid_col)
                sg.setColorAt(1,   dark_col)

            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(sg))
            p.drawEllipse(QPoint(nx, ny), size, size)

            spec_x = nx - int(size * 0.32)
            spec_y = ny - int(size * 0.30)
            spec_r = max(3, size // 3)
            spec_g = QRadialGradient(spec_x, spec_y, spec_r)
            spec_alpha = int(210 * t) if is_sel else (120 if is_hov else 60)
            spec_g.setColorAt(0,   QColor(255, 255, 255, spec_alpha))
            spec_g.setColorAt(1,   QColor(255, 255, 255, 0))
            p.setBrush(QBrush(spec_g))
            p.drawEllipse(QPoint(spec_x, spec_y), spec_r, spec_r)

            rim_col = col.lighter(130) if is_sel else QColor(70, 85, 115)
            p.setPen(QPen(rim_col, 1.5 if is_sel else 1))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPoint(nx, ny), size, size)

            if is_sel:
                icon_font = QFont("Segoe UI Symbol", 14)
                icon_font.setBold(True)
            else:
                icon_font = QFont("Segoe UI Symbol", 11)
            p.setFont(icon_font)
            icon_alpha = int(240 * t + 140 * (1 - t))
            p.setPen(QColor(255, 255, 255, icon_alpha))
            p.drawText(QRect(nx - size, ny - size, size * 2, size * 2),
                       Qt.AlignCenter, ICONS.get(opt, ""))

            lbl_r = node_r + 55  # centre du label juste à l'extérieur du node
            lx = int(cx + lbl_r * math.cos(ang))
            ly = int(cy + lbl_r * math.sin(ang))

            lf = QFont("Segoe UI", 10 if not is_sel else 12)
            lf.setBold(True)
            lf.setWeight(QFont.Black)
            lf.setLetterSpacing(QFont.AbsoluteSpacing, 1.8)
            p.setFont(lf)
            lc = col if is_sel else (col.lighter(115) if is_hov else QColor(95, 110, 140))
            p.setPen(lc)
            fm = QFontMetrics(lf)
            th = fm.height()
            # Large rect centré exactement sur (lx, ly)
            lbl_rect = QRect(lx - 80, ly - th, 160, th * 2)
            p.drawText(lbl_rect, Qt.AlignCenter, opt)

    def _draw_knob_3d(self, p, cx, cy):
        R = 58

        for off, alph in [(6, 40), (3, 70)]:
            p.setPen(Qt.NoPen)
            sg = QRadialGradient(cx + 2, cy + off, R + 10)
            sg.setColorAt(0,   QColor(0, 0, 0, 0))
            sg.setColorAt(0.7, QColor(0, 0, 0, alph))
            sg.setColorAt(1,   QColor(0, 0, 0, 0))
            p.setBrush(QBrush(sg))
            p.drawEllipse(QPoint(cx + 2, cy + off), R + 10, R + 10)

        bg = QRadialGradient(cx - R * 0.28, cy - R * 0.32, R * 1.5)
        bg.setColorAt(0,   QColor(78, 92, 122))
        bg.setColorAt(0.4, QColor(45, 56, 78))
        bg.setColorAt(0.75,QColor(28, 36, 54))
        bg.setColorAt(1,   QColor(18, 22, 35))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(bg))
        p.drawEllipse(QPoint(cx, cy), R, R)

        for ri in range(5):
            rr = R - 8 - ri * 9
            if rr < 4: break
            ring_g = QConicalGradient(cx, cy, 60 + ri * 18)
            ring_g.setColorAt(0.0,  QColor(90, 108, 142, 80))
            ring_g.setColorAt(0.25, QColor(50, 62, 85, 80))
            ring_g.setColorAt(0.5,  QColor(80, 96, 128, 80))
            ring_g.setColorAt(0.75, QColor(45, 56, 76, 80))
            ring_g.setColorAt(1.0,  QColor(90, 108, 142, 80))
            p.setPen(QPen(QBrush(ring_g), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPoint(cx, cy), rr, rr)

        # Cercle central avec touche de verte pâle
        center_circle = QRadialGradient(cx, cy, 15)
        center_circle.setColorAt(0, QColor(130, 150, 170).lighter(140))
        center_circle.setColorAt(1, QColor(80, 100, 120))
        p.setBrush(QBrush(center_circle))
        p.setPen(QPen(QColor(100, 120, 140), 1))
        p.drawEllipse(QPoint(cx, cy), 15, 15)

        for d in range(12):
            da  = math.radians(d * 30)
            dr  = R - 7
            dx  = int(cx + dr * math.cos(da))
            dy  = int(cy + dr * math.sin(da))
            dg = QRadialGradient(dx - 1, dy - 1, 3)
            dg.setColorAt(0, QColor(120, 140, 178))
            dg.setColorAt(1, QColor(40, 50, 70))
            p.setBrush(QBrush(dg))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPoint(dx, dy), 3, 3)

        sel_c  = qc(OPTION_COLORS[OPTIONS[self.selected_index]])
        sel_a  = math.radians(self._node_angle(self.selected_index))
        pulse  = 0.85 + 0.15 * math.sin(self.idle_pulse)
        tip_r  = R - 8
        tail_r = 10

        tip_x  = cx + tip_r  * math.cos(sel_a)
        tip_y  = cy + tip_r  * math.sin(sel_a)
        tail_x = cx + tail_r * math.cos(sel_a + math.pi)
        tail_y = cy + tail_r * math.sin(sel_a + math.pi)

        for w, a in [(10, int(40 * pulse)), (6, int(70 * pulse))]:
            gp = QPen(QColor(sel_c.red(), sel_c.green(), sel_c.blue(), a), w, Qt.SolidLine, Qt.RoundCap)
            p.setPen(gp)
            p.drawLine(QPoint(int(tail_x), int(tail_y)), QPoint(int(tip_x), int(tip_y)))

        p.setPen(QPen(sel_c.lighter(140), 3, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPoint(int(tail_x), int(tail_y)), QPoint(int(tip_x), int(tip_y)))

        perp_a = sel_a + math.pi / 2
        arr = 7
        arr1 = QPoint(int(tip_x - arr * math.cos(sel_a) + arr * 0.5 * math.cos(perp_a)),
                      int(tip_y - arr * math.sin(sel_a) + arr * 0.5 * math.sin(perp_a)))
        arr2 = QPoint(int(tip_x - arr * math.cos(sel_a) - arr * 0.5 * math.cos(perp_a)),
                      int(tip_y - arr * math.sin(sel_a) - arr * 0.5 * math.sin(perp_a)))
        arr_path = QPainterPath()
        arr_path.moveTo(tip_x, tip_y)
        arr_path.lineTo(arr1)
        arr_path.lineTo(arr2)
        arr_path.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(sel_c.lighter(140))
        p.drawPath(arr_path)

        spec_g = QRadialGradient(cx - R * 0.30, cy - R * 0.33, R * 0.45)
        spec_g.setColorAt(0,   QColor(255, 255, 255, 70))
        spec_g.setColorAt(0.6, QColor(255, 255, 255, 20))
        spec_g.setColorAt(1,   QColor(255, 255, 255, 0))
        p.setBrush(QBrush(spec_g))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPoint(cx, cy), R, R)

        jewel_r = 9
        jg = QRadialGradient(cx - 3, cy - 3, jewel_r * 1.4)
        jg.setColorAt(0,   sel_c.lighter(200))
        jg.setColorAt(0.5, sel_c)
        jg.setColorAt(1,   sel_c.darker(160))
        p.setBrush(QBrush(jg))
        p.setPen(QPen(sel_c.lighter(130), 1))
        p.drawEllipse(QPoint(cx, cy), jewel_r, jewel_r)
        
        jspec = QRadialGradient(cx - 3, cy - 3, 4)
        jspec.setColorAt(0, QColor(255, 255, 255, 200))
        jspec.setColorAt(1, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(jspec))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPoint(cx - 2, cy - 2), 4, 4)

    def _draw_status_bar(self, p, cx, cy):
        opt = OPTIONS[self.selected_index]
        col = qc(OPTION_COLORS[opt])

        bx, by = cx - 125, cy + 242
        bw, bh = 250, 54

        panel = QPainterPath()
        panel.addRoundedRect(bx, by, bw, bh, 12, 12)

        bg_g = QLinearGradient(bx, by, bx, by + bh)
        bg_g.setColorAt(0, QColor(35, 42, 60))
        bg_g.setColorAt(1, QColor(20, 26, 40))
        p.fillPath(panel, QBrush(bg_g))

        p.setPen(QPen(QColor(100, 120, 160, 120), 1))
        p.drawLine(int(bx + 14), int(by + 1), int(bx + bw - 14), int(by + 1))

        for w, alpha in [(6, 40), (3, 80), (1, 160)]:
            gp = QPen(QColor(col.red(), col.green(), col.blue(), alpha), w, Qt.SolidLine, Qt.RoundCap)
            p.setPen(gp)
            p.drawLine(int(bx + 20), int(by + bh - 2), int(bx + bw - 20), int(by + bh - 2))

        # Bordure acier chromé
        p.setPen(QPen(QColor(90, 110, 140, 160), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(panel)

        # Calcul de la largeur totale de l'icône + texte
        icon_font = QFont("Segoe UI Symbol", 18)
        icon_font.setBold(True)
        p.setFont(icon_font)
        icon_text = ICONS.get(opt, "")
        icon_width = p.fontMetrics().horizontalAdvance(icon_text)
        
        text_font = QFont("Courier New", 13)
        text_font.setBold(True)
        text_font.setLetterSpacing(QFont.AbsoluteSpacing, 3)
        p.setFont(text_font)
        text_width = p.fontMetrics().horizontalAdvance(opt)
        
        spacing = 10
        total_width = icon_width + spacing + text_width
        start_x = bx + (bw - total_width) // 2
        
        p.setFont(icon_font)
        p.setPen(QColor(col.red(), col.green(), col.blue(), 200))
        icon_rect = QRect(int(start_x), int(by), icon_width, int(bh))
        p.drawText(icon_rect, Qt.AlignVCenter | Qt.AlignLeft, icon_text)
        
        p.setFont(text_font)
        p.setPen(col.lighter(150))
        text_rect = QRect(int(start_x + icon_width + spacing), int(by), text_width, int(bh))
        p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, opt)

        for di in range(len(OPTIONS)):
            dot_x = int(bx + bw // 2 - (len(OPTIONS) - 1) * 7 + di * 14)
            dot_y = int(by + bh + 10)
            if di == self.selected_index:
                dg = QRadialGradient(dot_x, dot_y, 5)
                dg.setColorAt(0, col.lighter(160))
                dg.setColorAt(1, col)
                p.setBrush(QBrush(dg))
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPoint(dot_x, dot_y), 5, 5)
            else:
                p.setBrush(QColor(45, 56, 76))
                p.setPen(QPen(QColor(70, 85, 110), 1))
                p.drawEllipse(QPoint(dot_x, dot_y), 3, 3)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton: 
            return
        mx, my = event.position().x(), event.position().y()
        cx, cy = self._cx(), self._cy()
        dist = math.hypot(mx - cx, my - cy)

        node_r = 158
        for i in range(len(OPTIONS)):
            ang = math.radians(self._node_angle(i))
            nx  = cx + node_r * math.cos(ang)
            ny  = cy + node_r * math.sin(ang)
            if math.hypot(mx - nx, my - ny) < 28:
                self.selected_index = i
                self.target_angle   = self._snap_target(i)
                return

        if 50 < dist < 225:
            self.drag_start_mouse_angle = self._mouse_angle(mx, my)
            self.drag_start_spin        = self.spin_angle

    def mouseMoveEvent(self, event):
        mx, my = event.position().x(), event.position().y()
        cx, cy = self._cx(), self._cy()
        node_r = 158

        old_h = self.hover_index
        self.hover_index = -1
        for i in range(len(OPTIONS)):
            ang = math.radians(self._node_angle(i))
            nx  = cx + node_r * math.cos(ang)
            ny  = cy + node_r * math.sin(ang)
            if math.hypot(mx - nx, my - ny) < 28:
                self.hover_index = i
                break
        if self.hover_index != old_h:
            self.update()

        if self.drag_start_mouse_angle is not None:
            cur   = self._mouse_angle(mx, my)
            delta = cur - self.drag_start_mouse_angle
            self.spin_angle    = self.drag_start_spin + delta
            self.selected_index = self._nearest_index()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.drag_start_mouse_angle is not None:
            self.drag_start_mouse_angle = None
            idx = self._nearest_index()
            self.selected_index = idx
            self.target_angle   = self._snap_target(idx)

    def wheelEvent(self, event):
        if event.angleDelta().y() > 0:
            self.selected_index = (self.selected_index - 1) % len(OPTIONS)
        else:
            self.selected_index = (self.selected_index + 1) % len(OPTIONS)
        self.target_angle = self._snap_target(self.selected_index)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    w = CarComodo3D()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()

# ══════════════════════════════════════════════════════════════════
#  CarComodo3DReadOnly — affichage seul, zéro interaction souris
#  Utilisé par le panneau WC (Wiper Control) : lecture CAN unique.
#  API publique :
#      set_op(index: int)  — met à jour le mode affiché
# ══════════════════════════════════════════════════════════════════
class CarComodo3DReadOnly(CarComodo3D):
    """CarComodo3D en lecture seule : toutes les interactions
    souris et molette sont désactivées. Seul set_op() peut
    changer la sélection, piloté par la réception CAN."""

    # Taille de référence du design original
    _REF_W = 560
    _REF_H = 640

    def __init__(self, parent=None):
        super().__init__()
        if parent is not None:
            self.setParent(parent)
        # Libérer la taille fixe héritée (560×640)
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        # Fond transparent : s'intègre sur le fond KPIT du panel parent
        self.setStyleSheet("background: transparent;")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(False)
        self.setCursor(Qt.ArrowCursor)

    def _draw_bg(self, p, cx, cy):
        """Fond complètement transparent — le panel parent fournit le fond KPIT."""
        pass

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        W, H = self.width(), self.height()
        sx = W / self._REF_W
        sy = H / self._REF_H
        scale = min(sx, sy)

        # Pas de fillRect fond : on laisse transparaître le fond KPIT du parent

        off_x = (W - self._REF_W * scale) / 2
        off_y = (H - self._REF_H * scale) / 2

        p.translate(off_x, off_y)
        p.scale(scale, scale)

        cx = self._REF_W // 2
        cy = self._REF_H // 2 - 10

        self._draw_bg(p, cx, cy)
        self._draw_shadow_plate(p, cx, cy)
        self._draw_outer_bezel(p, cx, cy)
        self._draw_ring_3d(p, cx, cy)
        self._draw_tick_marks(p, cx, cy)
        self._draw_nodes(p, cx, cy)
        self._draw_knob_3d(p, cx, cy)
        self._draw_status_bar(p, cx, cy)
        p.end()

    # ── API backend ─────────────────────────────────────────
    def set_op(self, index: int) -> None:
        """Met à jour le mode affiché depuis la réception CAN.
        index : entier 0-7 correspondant à WiperOp."""
        idx = max(0, min(index, len(OPTIONS) - 1))
        self.selected_index = idx
        self.target_angle   = self._snap_target(idx)

    # ── Bloquer toutes les interactions ─────────────────────
    def mousePressEvent(self, event):
        event.ignore()

    def mouseMoveEvent(self, event):
        # Pas de hover, pas de drag
        event.ignore()

    def mouseReleaseEvent(self, event):
        event.ignore()

    def wheelEvent(self, event):
        event.ignore()