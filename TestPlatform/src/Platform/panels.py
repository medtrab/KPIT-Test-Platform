"""
WipeWash — Panneaux dock
MotorDashPanel, PumpPanel, VehicleRainPanel (+ IgnitionToggle),
CRSLINPanel (CRS Wiper Control + LIN oscilloscope + LIN table).
"""

import json
import datetime
import time
import threading
from collections import deque

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QPushButton, QSlider, QScrollArea, QDoubleSpinBox,
    QComboBox, QCheckBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QDialog, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui  import QColor, QFont, QPainter, QPen, QBrush, QPainterPath

from constants import (
    FONT_UI, FONT_MONO, MAX_ROWS,
    W_BG, W_PANEL, W_PANEL2, W_PANEL3,
    W_BORDER, W_BORDER2, W_SEP, W_TITLEBAR,
    W_TOOLBAR, W_DOCK_HDR,
    W_TEXT, W_TEXT_DIM, W_TEXT_HDR,
    A_TEAL, A_TEAL2, A_GREEN, A_GREEN_BG,
    A_RED, A_RED_BG, A_ORANGE, A_ORANGE_BG, A_AMBER,
    LIN_TX_C, LIN_RX_C, LIN_GRID,
    WOP,
)
from workers       import send_pump_cmd
from widgets_base  import (
    StatusLed, InstrumentPanel, NumericDisplay, LinearBar,
    _lbl, _hsep, _cd_btn,
)
from widgets_instruments import (
    MotorWidget, PumpWidget, WindshieldWidget, CarTopViewWidget,
)


# ═══════════════════════════════════════════════════════════
#  MOTOR DASHBOARD PANEL
# ═══════════════════════════════════════════════════════════
class MotorDashPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background:{W_BG};")
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        # Moteurs
        row_motors = QHBoxLayout(); row_motors.setSpacing(8)
        pan_f = InstrumentPanel("Motor Front", A_GREEN)
        self.motor_front = MotorWidget("FRONT")
        pan_f.body().addWidget(self.motor_front)
        row_motors.addWidget(pan_f, 1)

        pan_r = InstrumentPanel("Motor Rear", A_TEAL)
        self.motor_rear = MotorWidget("REAR")
        pan_r.body().addWidget(self.motor_rear)
        row_motors.addWidget(pan_r, 1)
        root.addLayout(row_motors, 3)

        # Métriques bas
        row_m = QHBoxLayout(); row_m.setSpacing(8)

        pan_rest = InstrumentPanel("Rest Contact", A_AMBER)
        self.led_rest  = StatusLed(14); self.led_rest.set_state(False, A_ORANGE)
        self.lbl_rest  = _lbl("MOVING", 13, True, A_ORANGE)
        rr = QHBoxLayout(); rr.setSpacing(8)
        rr.addWidget(self.led_rest); rr.addWidget(self.lbl_rest); rr.addStretch()
        pan_rest.body().addLayout(rr)
        row_m.addWidget(pan_rest, 1)

        pan_cur = InstrumentPanel("Motor Current", A_TEAL)
        self.disp_cur = NumericDisplay("CURRENT", "A")
        self.bar_cur  = LinearBar(1.5, "A")
        pan_cur.body().addWidget(self.disp_cur)
        pan_cur.body().addWidget(self.bar_cur)
        row_m.addWidget(pan_cur, 2)

        pan_st = InstrumentPanel("System Status", W_DOCK_HDR)
        self.lbl_status = _lbl("Waiting for connection…", 10, False, W_TEXT_DIM, True)
        self.lbl_status.setWordWrap(True)
        pan_st.body().addWidget(self.lbl_status)
        row_m.addWidget(pan_st, 2)

        root.addLayout(row_m, 1)

    def on_motor_data(self, data: dict) -> None:
        if isinstance(data.get("front"), str):
            self._apply(data)
        else:
            def _d(v):
                if isinstance(v, dict): return v
                try:   return json.loads(v) if isinstance(v, str) else {}
                except: return {}
            f = _d(data.get("front", {}))
            r = _d(data.get("rear",  {}))
            self._apply({
                "front"  : "ON" if f.get("enable", 0) else "OFF",
                "rear"   : "ON" if r.get("enable", 0) else "OFF",
                "speed"  : "Speed2" if f.get("speed", 0) else "Speed1",
                "current": float(f.get("motor_current", 0)) + float(r.get("motor_current", 0)),
                "fault"  : bool(f.get("fault_status", 0)) or bool(r.get("fault_status", 0)),
                "rest"   : "PARKED" if (f.get("rest_contact", 0) and r.get("rest_contact", 0)) else "",
            })

    def _apply(self, s: dict) -> None:
        fault = bool(s.get("fault", False))
        self.motor_front.set_state(s.get("front", "OFF"), s.get("speed", "Speed1"))
        self.motor_rear.set_state(s.get("rear",  "OFF"), s.get("speed", "Speed1"))
        cur = float(s.get("current", 0))
        self.disp_cur.set_value(f"{cur:.3f}",
                                A_RED if fault else (A_ORANGE if cur > 0.8 else A_TEAL))
        self.bar_cur.set_value(cur, fault)
        parking = s.get("rest", "") == "PARKED"
        self.led_rest.set_state(parking, A_GREEN if parking else A_ORANGE)
        if parking:
            self.lbl_rest.setText("PARKED")
            self.lbl_rest.setStyleSheet(
                f"color:{A_GREEN};font-weight:bold;background:transparent;")
        else:
            self.lbl_rest.setText("MOVING")
            self.lbl_rest.setStyleSheet(
                f"color:{A_ORANGE};font-weight:bold;background:transparent;")
        if fault:
            self.lbl_status.setText("FAULT FSR_003 - Overcurrent detected - Motors stopped")
            self.lbl_status.setStyleSheet(f"color:{A_RED};background:transparent;")
        else:
            self.lbl_status.setText(
                f"Front:{s.get('front','?')}  Rear:{s.get('rear','?')}"
                f"  {s.get('speed','?')}  I={cur:.3f} A")
            self.lbl_status.setStyleSheet(f"color:{W_TEXT_DIM};background:transparent;")

    def set_connected(self, ok: bool, host: str = "") -> None:
        pass


# ═══════════════════════════════════════════════════════════
#  PUMP PANEL
# ═══════════════════════════════════════════════════════════
class PumpPanel(QWidget):
    def __init__(self, pump_getter, parent=None) -> None:
        super().__init__(parent)
        self._pump_getter = pump_getter
        self.pump_start   = None
        self._iface_rem   = 0.0
        self._iface_dur   = 0.0
        self._src         = "BCM"
        self._state_str   = "OFF"
        self.setStyleSheet(f"background:{W_BG};")
        self._build()
        self._t = QTimer(); self._t.timeout.connect(self._tick); self._t.start(100)

    def _build(self) -> None:
        root = QVBoxLayout(self); root.setContentsMargins(8, 6, 8, 6); root.setSpacing(6)

        row1 = QHBoxLayout(); row1.setSpacing(8)

        pan_pump = InstrumentPanel("Hydraulic Pump", A_TEAL)
        self.pump_widget = PumpWidget()
        pan_pump.body().addWidget(self.pump_widget)
        row1.addWidget(pan_pump, 2)

        pan_m = InstrumentPanel("Measurements", A_GREEN)
        self.disp_cur = NumericDisplay("CURRENT", "A")
        self.bar_cur  = LinearBar(1.5, "A")
        self.disp_vol = NumericDisplay("VOLTAGE", "V")
        self.bar_vol  = LinearBar(14, "V")
        for w in (self.disp_cur, self.bar_cur, self.disp_vol, self.bar_vol):
            pan_m.body().addWidget(w)
        row1.addWidget(pan_m, 2)

        pan_to = InstrumentPanel("Timeout  FSR_005", A_AMBER)
        self.disp_to    = NumericDisplay("REMAINING", "s")
        self.bar_to     = LinearBar(5.0, "s", ticks=5)
        self.lbl_to_info = _lbl("Pump inactive", 10, False, W_TEXT_DIM, True)
        pan_to.body().addWidget(self.disp_to)
        pan_to.body().addWidget(self.bar_to)
        pan_to.body().addWidget(self.lbl_to_info)
        row1.addWidget(pan_to, 2)
        root.addLayout(row1, 3)

        pan_cmd = InstrumentPanel("Interface Commands — Override BCM", A_GREEN)
        row_d = QHBoxLayout(); row_d.setSpacing(8)
        row_d.addWidget(_lbl("Duration:", 10, False, W_TEXT_DIM))
        self.spin = QDoubleSpinBox()
        self.spin.setRange(0.5, 120); self.spin.setValue(5.0); self.spin.setSuffix(" s")
        self.spin.setFont(QFont(FONT_MONO, 12, QFont.Weight.Bold)); self.spin.setFixedHeight(30)
        self.spin.setStyleSheet(
            f"QDoubleSpinBox{{background:{W_PANEL};color:{W_TEXT};"
            f"border:1px solid {W_BORDER};border-radius:2px;padding:1px 8px;}}"
            f"QDoubleSpinBox::up-button,QDoubleSpinBox::down-button"
            f"{{width:20px;background:{W_PANEL3};}}")
        row_d.addWidget(self.spin); row_d.addStretch()
        pan_cmd.body().addLayout(row_d)

        row_b = QHBoxLayout(); row_b.setSpacing(6)
        self.btn_fwd = _cd_btn("FORWARD",  A_GREEN, h=32)
        self.btn_bwd = _cd_btn("BACKWARD", A_TEAL,  h=32)
        self.btn_off = _cd_btn("STOP",     A_RED,   h=32)
        self.btn_fwd.clicked.connect(lambda: self._send("FORWARD"))
        self.btn_bwd.clicked.connect(lambda: self._send("BACKWARD"))
        self.btn_off.clicked.connect(lambda: self._send("OFF", 0))
        for b in (self.btn_fwd, self.btn_bwd, self.btn_off):
            row_b.addWidget(b)
        pan_cmd.body().addLayout(row_b)
        root.addWidget(pan_cmd, 1)

        pan_al = InstrumentPanel("Alerts", A_RED)
        self.lbl_alert = _lbl("No active alerts", 11, True, A_GREEN)
        self.lbl_alert.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_alert.setStyleSheet(
            f"color:{A_GREEN};background:{A_GREEN_BG};"
            f"border:1px solid {A_GREEN};padding:5px;border-radius:2px;")
        pan_al.body().addWidget(self.lbl_alert)
        root.addWidget(pan_al)

    def _send(self, cmd: str, dur=None) -> None:
        dur  = dur if dur is not None else self.spin.value()
        c    = self._pump_getter()
        host = c.host if c else None
        threading.Thread(target=send_pump_cmd, args=(host, cmd, dur), daemon=True).start()

    def update_display(self, s: dict) -> None:
        state  = s.get("state",          "OFF")
        cur    = float(s.get("current",        0.0))
        vol    = float(s.get("voltage",        0.0))
        fault  = s.get("fault",          False)
        reason = s.get("fault_reason",   "")
        rem    = float(s.get("pump_remaining", 0.0))
        dur    = float(s.get("pump_duration",  0.0))
        src    = s.get("source",         "BCM")

        self._state_str = state; self._src = src
        self._iface_rem = rem;   self._iface_dur = dur

        disp = reason if fault and reason else state
        self.pump_widget.set_state(disp, cur, fault)

        self.disp_cur.set_value(f"{cur:.3f}",
                                A_RED if cur > 1 else (A_ORANGE if cur > 0.7 else A_TEAL))
        self.bar_cur.set_value(cur, False)
        self.disp_vol.set_value(f"{vol:.2f}", A_TEAL)
        self.bar_vol.set_value(vol, False)

        active = state in ("FORWARD", "BACKWARD") and not fault
        if active and src == "BCM":
            if self.pump_start is None:
                self.pump_start = time.time()
            self.lbl_to_info.setText("BCM Mode — FSR_005 active (5s cutoff)")
            self.lbl_to_info.setStyleSheet(f"color:{A_ORANGE};background:transparent;")
        elif active:
            self.pump_start = None
            self.lbl_to_info.setText(f"Interface — {rem:.1f}s / {dur:.1f}s")
            self.lbl_to_info.setStyleSheet(f"color:{A_TEAL};background:transparent;")
        else:
            self.pump_start = None
            self.disp_to.set_value("—"); self.bar_to.set_value(0)
            self.lbl_to_info.setText("Pump inactive")
            self.lbl_to_info.setStyleSheet(f"color:{W_TEXT_DIM};background:transparent;")

        if fault:
            msg = (f"OVERCURRENT : {cur:.3f}A > 1.0A (FSR_003)"
                   if reason == "OVERCURRENT" else f"FAULT: {reason}")
            self.lbl_alert.setText(f"FAULT: {msg}")
            self.lbl_alert.setStyleSheet(
                f"color:{A_RED};background:{A_RED_BG};"
                f"border:1px solid {A_RED};padding:5px;border-radius:2px;")
        else:
            self.lbl_alert.setText("No active alerts")
            self.lbl_alert.setStyleSheet(
                f"color:{A_GREEN};background:{A_GREEN_BG};"
                f"border:1px solid {A_GREEN};padding:5px;border-radius:2px;")

    def _tick(self) -> None:
        if self.pump_start and self._src == "BCM":
            rem = max(0, 5.0 - (time.time() - self.pump_start))
            c   = A_RED if rem < 1.5 else A_AMBER
            self.disp_to.set_value(f"{rem:.1f}", c)
            self.bar_to.set_value(5 - rem)
        elif self._src == "INTERFACE" and self._state_str in ("FORWARD", "BACKWARD"):
            rem = self._iface_rem; dur = self._iface_dur
            if dur > 0:
                self.disp_to.set_value(f"{rem:.1f}", A_RED if rem < 1.5 else A_TEAL)
                self.bar_to.set_value(dur - rem)

    def on_connected(self, h: str)   -> None: pass
    def on_disconnected(self)         -> None: pass


# ═══════════════════════════════════════════════════════════
#  VEHICLE & RAIN PANEL
# ═══════════════════════════════════════════════════════════
class IgnitionToggle(QWidget):
    changed = pyqtSignal(str)
    STATES  = ["OFF", "ACC", "ON"]
    COLORS  = {
        "OFF": ("#707070", W_PANEL3),
        "ACC": (A_ORANGE,  A_ORANGE_BG),
        "ON":  (A_GREEN,   A_GREEN_BG),
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._s = "OFF"
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(3)
        self._btns: dict[str, QPushButton] = {}
        for s in self.STATES:
            b = QPushButton(s); b.setFixedHeight(28)
            b.setFont(QFont(FONT_MONO, 10, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, st=s: self._sel(st))
            lay.addWidget(b); self._btns[s] = b
        self._refresh()

    def _sel(self, st: str) -> None:
        self._s = st; self._refresh(); self.changed.emit(st)

    def _refresh(self) -> None:
        for s, b in self._btns.items():
            fg, bg = self.COLORS[s]
            if s == self._s:
                b.setStyleSheet(
                    f"QPushButton{{background:{fg};color:#FFF;"
                    f"border:1px solid {QColor(fg).darker(120).name()};"
                    f"border-radius:2px;padding:2px 10px;}}")
            else:
                b.setStyleSheet(
                    f"QPushButton{{background:{W_PANEL3};color:{W_TEXT_DIM};"
                    f"border:1px solid {W_BORDER};border-radius:2px;padding:2px 10px;}}"
                    f"QPushButton:hover{{background:{W_PANEL2};}}")

    def get(self) -> str: return self._s


class VehicleRainPanel(QWidget):
    def __init__(self, motor_getter, parent=None) -> None:
        super().__init__(parent)
        self._getter    = motor_getter
        self._ign       = "OFF"
        self._rev       = 0
        self._spd       = 0.0
        self._rain      = 0
        self._sensor_ok = True
        self._tx        = 0
        self.setStyleSheet(f"background:{W_BG};")
        self._build()

    def _build(self) -> None:
        scroll = QScrollArea(self); scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea{{border:none;background:{W_BG};}}")
        c = QWidget(); c.setStyleSheet(f"background:{W_BG};"); scroll.setWidget(c)
        vl = QVBoxLayout(self); vl.setContentsMargins(0, 0, 0, 0); vl.addWidget(scroll)
        root = QVBoxLayout(c); root.setContentsMargins(8, 6, 8, 6); root.setSpacing(6)

        # TX display
        pan_tx = InstrumentPanel("Last TX Frames", A_TEAL2)
        self.lbl_tx_v = _lbl("VEH  —", 10, False, A_TEAL, True)
        self.lbl_tx_r = _lbl("RAIN —", 10, False, A_GREEN, True)
        self.lbl_tx_v.setStyleSheet(
            f"color:{A_TEAL};background:{W_PANEL3};padding:2px 6px;"
            f"border-radius:2px;font-family:{FONT_MONO};font-size:10pt;")
        self.lbl_tx_r.setStyleSheet(
            f"color:{A_GREEN};background:{W_PANEL3};padding:2px 6px;"
            f"border-radius:2px;font-family:{FONT_MONO};font-size:10pt;")
        pan_tx.body().addWidget(self.lbl_tx_v)
        pan_tx.body().addWidget(self.lbl_tx_r)
        root.addWidget(pan_tx)

        main_row = QHBoxLayout(); main_row.setSpacing(8)

        # ── Vehicle Status ──
        pan_v = InstrumentPanel("Vehicle Status", A_TEAL2)
        pan_v.body().addWidget(_lbl("IGNITION", 10, True, W_TEXT_DIM))
        self.ign = IgnitionToggle(); self.ign.changed.connect(self._sv)
        pan_v.body().addWidget(self.ign); pan_v.body().addWidget(_hsep())

        pan_v.body().addWidget(_lbl("REVERSE GEAR", 10, True, W_TEXT_DIM))
        rr = QHBoxLayout(); rr.setSpacing(6)
        self._led_rev = StatusLed(11); self.lbl_rev = _lbl("NORMAL", 11, True, W_TEXT_DIM)
        self.btn_rev  = _cd_btn("TOGGLE", A_ORANGE, h=26)
        self.btn_rev.clicked.connect(self._toggle_rev)
        rr.addWidget(self._led_rev); rr.addWidget(self.lbl_rev)
        rr.addStretch(); rr.addWidget(self.btn_rev)
        pan_v.body().addLayout(rr); pan_v.body().addWidget(_hsep())

        pan_v.body().addWidget(_lbl("VEHICLE SPEED  (km/h)", 10, True, W_TEXT_DIM))
        self.disp_spd = NumericDisplay("SPEED", "km/h")
        self.sld_spd  = QSlider(Qt.Orientation.Horizontal); self.sld_spd.setRange(0, 2000)
        self.sld_spd.setStyleSheet(
            f"QSlider::groove:horizontal{{background:{W_PANEL3};height:4px;border:1px solid {W_BORDER};}}"
            f"QSlider::handle:horizontal{{background:{A_TEAL};width:12px;height:12px;"
            f"margin:-5px 0;border-radius:6px;}}"
            f"QSlider::sub-page:horizontal{{background:{A_TEAL};}}")
        self.sld_spd.valueChanged.connect(self._on_spd)
        pan_v.body().addWidget(self.disp_spd); pan_v.body().addWidget(self.sld_spd)
        main_row.addWidget(pan_v, 3)

        # ── Vue voiture ──
        pan_car = InstrumentPanel("Vehicle Simulation — Top View", A_TEAL)
        self.car_view = CarTopViewWidget()
        pan_car.body().setContentsMargins(4, 4, 4, 4)
        pan_car.body().addWidget(self.car_view)
        main_row.addWidget(pan_car, 4)

        # ── Rain Sensor ──
        pan_r = InstrumentPanel("Rain Sensor", A_TEAL)
        pan_r.body().addWidget(_lbl("RAIN INTENSITY  (%)", 10, True, W_TEXT_DIM))
        self.disp_rain = NumericDisplay("RAIN", "%")
        self.sld_rain  = QSlider(Qt.Orientation.Horizontal); self.sld_rain.setRange(0, 100)
        self.sld_rain.setStyleSheet(
            f"QSlider::groove:horizontal{{background:{W_PANEL3};height:4px;border:1px solid {W_BORDER};}}"
            f"QSlider::handle:horizontal{{background:{A_TEAL2};width:12px;height:12px;"
            f"margin:-5px 0;border-radius:6px;}}"
            f"QSlider::sub-page:horizontal{{background:{A_TEAL2};}}")
        self.sld_rain.valueChanged.connect(self._on_rain)
        pan_r.body().addWidget(self.disp_rain); pan_r.body().addWidget(self.sld_rain)
        pan_r.body().addWidget(_hsep())
        pan_r.body().addWidget(_lbl("SENSOR STATUS", 10, True, W_TEXT_DIM))

        rs = QHBoxLayout(); rs.setSpacing(6)
        self._led_sens = StatusLed(11); self._led_sens.set_state(True, A_GREEN)
        self.lbl_sens  = _lbl("OK", 11, True, A_GREEN)
        self.btn_sens  = _cd_btn("SIMULATE ERROR", A_RED, h=26)
        self.btn_sens.clicked.connect(self._toggle_sens)
        rs.addWidget(self._led_sens); rs.addWidget(self.lbl_sens)
        rs.addStretch(); rs.addWidget(self.btn_sens)
        pan_r.body().addLayout(rs); pan_r.body().addStretch()
        main_row.addWidget(pan_r, 3)
        root.addLayout(main_row)

        # Barre basse
        bot = QHBoxLayout(); bot.setSpacing(8)
        self.btn_now = _cd_btn("SEND NOW", A_TEAL2, h=28)
        self.btn_now.clicked.connect(self._send_both)
        self._tx_led = StatusLed(10)
        self.lbl_txc = _lbl("TX: 0", 10, True, A_TEAL, True)
        self._tx_off = QTimer(self); self._tx_off.setSingleShot(True)
        self._tx_off.timeout.connect(lambda: self._tx_led.set_state(False))
        bot.addWidget(self.btn_now); bot.addSpacing(4)
        bot.addWidget(self._tx_led); bot.addWidget(self.lbl_txc); bot.addStretch()
        root.addLayout(bot)

    # ── Callbacks UI ─────────────────────────────────────────
    def _on_spd(self, v: int) -> None:
        self._spd = v / 10.0
        self.disp_spd.set_value(f"{self._spd:.1f}")
        self.car_view.set_speed(self._spd)
        self._sv()

    def _on_rain(self, v: int) -> None:
        self._rain = v
        self.disp_rain.set_value(f"{v}")
        self.car_view.set_rain(v)
        self._sr()

    def _toggle_rev(self) -> None:
        self._rev = 1 - self._rev
        self._led_rev.set_state(bool(self._rev), A_ORANGE if self._rev else "#707070")
        if self._rev:
            self.lbl_rev.setText("REVERSE")
            self.lbl_rev.setStyleSheet(f"color:{A_ORANGE};font-weight:bold;background:transparent;")
        else:
            self.lbl_rev.setText("NORMAL")
            self.lbl_rev.setStyleSheet(f"color:{W_TEXT_DIM};font-weight:bold;background:transparent;")
        self.car_view.set_reverse(bool(self._rev))
        self._sv()

    def _toggle_sens(self) -> None:
        self._sensor_ok = not self._sensor_ok
        if self._sensor_ok:
            self._led_sens.set_state(True, A_GREEN)
            self.lbl_sens.setText("OK")
            self.lbl_sens.setStyleSheet(f"color:{A_GREEN};font-weight:bold;background:transparent;")
            self.btn_sens.setText("SIMULATE ERROR")
        else:
            self._led_sens.set_state(True, A_RED)
            self.lbl_sens.setText("ERROR")
            self.lbl_sens.setStyleSheet(f"color:{A_RED};font-weight:bold;background:transparent;")
            self.btn_sens.setText("RESTORE OK")
        self._sr()

    # ── Envoi JSON ───────────────────────────────────────────
    def _sv(self, *_) -> None:
        w = self._getter()
        if not w: return
        self._ign = self.ign.get()
        self.car_view.set_ignition(self._ign)
        obj = {"type": "vehicle", "ignition_status": self._ign,
               "reverse_gear": self._rev, "vehicle_speed": round(self._spd, 1)}
        w.queue_send(obj); self._tx += 1; self.lbl_txc.setText(f"TX: {self._tx}")
        self._tx_led.set_state(True, A_GREEN); self._tx_off.start(80)
        self.lbl_tx_v.setText(f"VEH  {json.dumps(obj)}")

    def _sr(self) -> None:
        w = self._getter()
        if not w: return
        obj = {"type": "rain", "rain_intensity": self._rain,
               "sensor_status": "OK" if self._sensor_ok else "ERROR"}
        w.queue_send(obj); self._tx += 1; self.lbl_txc.setText(f"TX: {self._tx}")
        self._tx_led.set_state(True, A_GREEN); self._tx_off.start(80)
        self.lbl_tx_r.setText(f"RAIN {json.dumps(obj)}")

    def _send_both(self) -> None:
        self._sv(); self._sr()


# ═══════════════════════════════════════════════════════════
#  CRS / LIN PANEL
# ═══════════════════════════════════════════════════════════
class LINOscilloscope(QWidget):
    WINDOW = 30.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._evts: deque = deque()
        self.setMinimumHeight(170)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._t = QTimer(); self._t.timeout.connect(self.update); self._t.start(40)

    def add_event(self, typ: str, label: str = "") -> None:
        t   = time.time()
        amp = 1.0 if typ == "TX" else 0.65
        col = LIN_TX_C if typ == "TX" else LIN_RX_C
        self._evts.append((t, amp, col, label))
        cut = t - self.WINDOW * 2
        while self._evts and self._evts[0][0] < cut:
            self._evts.popleft()

    def paintEvent(self, _) -> None:
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height(); now = time.time()
        p.fillRect(0, 0, W, H, QBrush(QColor(W_PANEL)))
        ML, MR, MT, MB = 44, 10, 8, 26
        cw = W - ML - MR; ch = H - MT - MB
        if cw < 10 or ch < 10: return

        for i in range(7):
            x   = ML + int(cw * i / 6)
            t_v = self.WINDOW * (1 - i / 6)
            p.setPen(QPen(QColor(LIN_GRID), 1, Qt.PenStyle.DotLine))
            p.drawLine(x, MT, x, MT + ch)
            p.setPen(QPen(QColor(W_TEXT_DIM))); p.setFont(QFont(FONT_MONO, 9))
            p.drawText(x - 14, MT + ch + 2, 28, 12,
                       Qt.AlignmentFlag.AlignCenter, f"-{t_v:.0f}s")
        for i in range(5):
            y = MT + int(ch * i / 4)
            p.setPen(QPen(QColor(LIN_GRID), 1, Qt.PenStyle.DotLine))
            p.drawLine(ML, y, ML + cw, y)
            a = 1.0 - i / 4
            p.setPen(QPen(QColor(W_TEXT_DIM))); p.setFont(QFont(FONT_MONO, 9))
            p.drawText(0, y - 6, ML - 3, 12,
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{a:.1f}")
        p.setPen(QPen(QColor(W_BORDER), 1))
        p.drawLine(ML, MT, ML, MT + ch); p.drawLine(ML, MT + ch, ML + cw, MT + ch)

        base_y = MT + ch

        def tx(ta): return ML + cw - int(cw * (now - ta) / self.WINDOW)
        def ay(a):  return MT + int(ch * (1.0 - a))

        vis = [(te, am, co, lb) for te, am, co, lb in self._evts
               if (now - te) <= self.WINDOW]
        for te, am, co, lb in vis:
            xp = tx(te)
            if xp < ML or xp > ML + cw: continue
            hw  = 8
            pts = [(xp + dx, ay(am * max(0, 1.0 - (abs(dx) / hw) ** 1.4)))
                   for dx in range(-hw, hw + 1)]
            fp = QPainterPath(); fp.moveTo(xp - hw, base_y)
            for px, py in pts: fp.lineTo(px, py)
            fp.lineTo(xp + hw, base_y); fp.closeSubpath()
            fc = QColor(co); fc.setAlpha(50); p.fillPath(fp, QBrush(fc))
            pp2 = QPainterPath(); pp2.moveTo(pts[0][0], pts[0][1])
            for px, py in pts[1:]: pp2.lineTo(px, py)
            p.setPen(QPen(QColor(co), 1.5, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush); p.drawPath(pp2)
            p.setPen(QPen(QColor(co), 1, Qt.PenStyle.DotLine))
            p.drawLine(xp, ay(am), xp, base_y)
            if lb and xp > ML + 16:
                p.setFont(QFont(FONT_MONO, 9))
                lc = QColor(co); lc.setAlpha(200); p.setPen(QPen(lc))
                p.drawText(xp - 14, ay(am) - 12, 28, 10,
                           Qt.AlignmentFlag.AlignCenter, lb[:6])
        p.setPen(QPen(QColor(W_BORDER), 1, Qt.PenStyle.DashLine))
        p.drawLine(ML, base_y, ML + cw, base_y)
        lx = ML + 6; ly = MT + 4
        for co, lb in [(LIN_TX_C, "TX  slave->BCM"), (LIN_RX_C, "RX  BCM->slave")]:
            p.setPen(QPen(QColor(co), 2)); p.drawLine(lx, ly + 5, lx + 16, ly + 5)
            p.setPen(QPen(QColor(W_TEXT))); p.setFont(QFont(FONT_MONO, 9))
            p.drawText(lx + 20, ly, 110, 12,
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, lb)
            ly += 13
        p.setFont(QFont(FONT_MONO, 9, QFont.Weight.Bold))
        p.setPen(QPen(QColor(A_GREEN)))
        p.drawText(W - MR - 28, MT, 28, 12, Qt.AlignmentFlag.AlignCenter, "* LIVE")


class LINTableWidget(QTableWidget):
    COLS = ["#", "Time", "Direction", "PID",
            "Byte0 / Op", "Byte1 / Alive", "Checksum", "Op Name", "Raw"]

    def __init__(self, parent=None) -> None:
        super().__init__(0, len(self.COLS), parent)
        self.setHorizontalHeaderLabels(self.COLS)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setAlternatingRowColors(True); self.verticalHeader().setVisible(False)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.setStyleSheet(f"""
            QTableWidget {{
                background:{W_PANEL};color:{W_TEXT};border:none;
                gridline-color:{W_PANEL3};alternate-background-color:{W_PANEL2};
                font-family:{FONT_MONO};font-size:11pt;
                selection-background-color:{A_GREEN_BG};selection-color:{W_TEXT};
            }}
            QHeaderView::section {{
                background:{W_TITLEBAR};color:{W_TEXT_HDR};border:none;
                border-bottom:1px solid {W_BORDER2};border-right:1px solid {W_BORDER};
                padding:4px 8px;font-family:{FONT_UI};font-size:10pt;font-weight:bold;
            }}
            QTableWidget::item {{ padding:1px 6px;border-bottom:1px solid {W_PANEL3}; }}
        """)
        self._rn   = 0
        self._evts: list[dict] = []
        self._auto = True
        self.cellDoubleClicked.connect(self._dbl)

    def _dbl(self, row: int, _) -> None:
        if row < len(self._evts):
            self._show_detail(self._evts[row])

    def _show_detail(self, ev: dict) -> None:
        from PyQt6.QtWidgets import QDialog, QVBoxLayout
        dlg = QDialog(self); dlg.setWindowTitle("LIN Frame Details")
        dlg.setMinimumSize(500, 360)
        dlg.setStyleSheet(f"background:{W_PANEL};color:{W_TEXT};")
        lay = QVBoxLayout(dlg); lay.setContentsMargins(14, 12, 14, 12); lay.setSpacing(8)
        d   = ev.get("type", "?"); col = LIN_TX_C if d == "TX" else LIN_RX_C
        hdr = QFrame(); hdr.setStyleSheet(f"background:{W_PANEL2};border-left:3px solid {col};")
        hl  = QHBoxLayout(hdr); hl.setContentsMargins(10, 6, 10, 6)
        hl.addWidget(_lbl(f"{'TX  slave->BCM' if d == 'TX' else 'RX_HDR  BCM->slave'}",
                          13, True, col, True))
        hl.addStretch()
        ts = datetime.datetime.fromtimestamp(
            ev.get("time", time.time())).strftime("%H:%M:%S.%f")[:-3]
        hl.addWidget(_lbl(ts, 10, False, W_TEXT_DIM, True)); lay.addWidget(hdr)
        tbl = QTableWidget(0, 2); tbl.setHorizontalHeaderLabels(["Field", "Value"])
        tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tbl.verticalHeader().setVisible(False); tbl.horizontalHeader().setStretchLastSection(True)
        tbl.setStyleSheet(
            f"QTableWidget{{background:{W_PANEL};color:{W_TEXT};border:1px solid {W_BORDER};"
            f"font-family:{FONT_MONO};font-size:11pt;}}"
            f"QHeaderView::section{{background:{W_TITLEBAR};color:{W_TEXT_HDR};"
            f"border:none;border-bottom:1px solid {W_BORDER};padding:4px;font-weight:bold;}}")
        def add(f2, v2, vc=W_TEXT):
            r = tbl.rowCount(); tbl.insertRow(r)
            fi = QTableWidgetItem(f2); fi.setForeground(QColor(W_TEXT_DIM)); tbl.setItem(r, 0, fi)
            vi = QTableWidgetItem(str(v2)); vi.setForeground(QColor(vc)); tbl.setItem(r, 1, vi)
            tbl.setRowHeight(r, 22)
        add("Direction", "TX slave->BCM" if d == "TX" else "RX_HDR BCM->slave", col)
        add("Timestamp", ts)
        if d == "TX":
            op = ev.get("op", 0)
            add("PID", "0xD6")
            add("Byte0 — WiperOp",
                f"0x{op:02X}  ->  {WOP.get(op, {}).get('name', '?')}",
                WOP.get(op, {}).get("color", W_TEXT))
            add("Byte1 — AliveCounter", f"0x{ev.get('alive', 0):02X}", A_TEAL)
            add("Checksum", f"0x{ev.get('cs_int', 0):02X}", W_TEXT_DIM)
            add("Description", WOP.get(op, {}).get("desc", "?"), W_TEXT_DIM)
            add("Requirement", WOP.get(op, {}).get("req", "?"), "#6A1B9A")
        else:
            add("Break", "0x00  (13 dominant bits)", W_TEXT_DIM)
            add("Sync",  "0x55", W_TEXT_DIM)
            add("PID",   ev.get("pid", "0xD6"), A_TEAL)
        add("Raw bytes", ev.get("raw", "—"), W_TEXT_DIM)
        lay.addWidget(tbl, 1)
        b = _cd_btn("Close", "#707070", h=28); b.clicked.connect(dlg.close); lay.addWidget(b)
        dlg.exec()

    def add_event(self, ev: dict) -> None:
        if self._rn >= MAX_ROWS:
            self.removeRow(0); self._evts.pop(0); self._rn -= 1
        self._evts.append(ev); r = self._rn; self.insertRow(r); self._rn += 1
        ts = datetime.datetime.fromtimestamp(
            ev.get("time", time.time())).strftime("%H:%M:%S.%f")[:-3]
        d  = ev.get("type", ""); c = QColor(LIN_TX_C if d == "TX" else LIN_RX_C)
        cells = [str(r + 1), ts,
                 "TX  slave->BCM" if d == "TX" else "RX_HDR  BCM->slave",
                 "0xD6", "", "", "", "", ev.get("raw", "")]
        if d == "TX":
            op = ev.get("op", 0)
            cells[4] = f"0x{op:02X}  {WOP.get(op, {}).get('name', '?')}"
            cells[5] = f"0x{ev.get('alive', 0):02X}"
            cells[6] = f"0x{ev.get('cs_int', 0):02X}"
            cells[7] = WOP.get(op, {}).get("name", "?")
        else:
            cells[3] = ev.get("pid", "0xD6")
            cells[4] = cells[5] = cells[6] = "—"; cells[7] = "LIN HEADER"
        for ci, val in enumerate(cells):
            it = QTableWidgetItem(val)
            it.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            if ci == 2:
                it.setForeground(c)
            elif ci == 7 and d == "TX":
                it.setForeground(QColor(WOP.get(ev.get("op", 0), {}).get("color", W_TEXT)))
            else:
                it.setForeground(QColor(W_TEXT))
            self.setItem(r, ci, it)
        self.setRowHeight(r, 20)
        if self._auto: self.scrollToBottom()

    def clear_all(self) -> None:
        self.setRowCount(0); self._rn = 0; self._evts.clear()

    def set_auto(self, v: bool) -> None:
        self._auto = v


class CRSLINPanel(QWidget):
    def __init__(self, wiper_setter, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background:{W_BG};")
        vl = QVBoxLayout(self); vl.setContentsMargins(0, 0, 0, 0); vl.setSpacing(0)

        from PyQt6.QtWidgets import QTabWidget
        tabs = QTabWidget()
        tabs.setStyleSheet(f"""
            QTabWidget::pane{{border:none;background:{W_BG};}}
            QTabBar{{background:{W_TOOLBAR};border-bottom:1px solid {W_BORDER};}}
            QTabBar::tab{{background:{W_TOOLBAR};color:{W_TEXT_DIM};border:none;
                border-right:1px solid {W_SEP};padding:7px 20px;
                font-family:{FONT_UI};font-size:11pt;min-width:100px;}}
            QTabBar::tab:selected{{background:{W_BG};color:{A_TEAL2};
                border-top:2px solid {A_TEAL};}}
            QTabBar::tab:hover:!selected{{background:{W_PANEL2};color:{W_TEXT};}}
        """)
        self._crs     = self._build_crs(wiper_setter)
        self._signal  = self._build_signal()
        self._table_w = self._build_table()
        tabs.addTab(self._crs,     "  CRS — Wiper Control  ")
        tabs.addTab(self._signal,  "  LIN — Bus Signal  ")
        tabs.addTab(self._table_w, "  LIN — Frame Table  ")
        vl.addWidget(tabs)

    # ── CRS Wiper Control ─────────────────────────────────────
    def _build_crs(self, wiper_setter) -> QWidget:
        w = QWidget(); w.setStyleSheet(f"background:{W_BG};")
        lay = QHBoxLayout(w); lay.setContentsMargins(8, 6, 8, 6); lay.setSpacing(8)

        pan_ops = InstrumentPanel("Wiper Operation Selector", A_TEAL)
        self._op_btns: dict[int, QFrame] = {}
        self._cur_op      = 0
        self._wiper_setter = wiper_setter
        self._al = 0; self._seq = 0
        self._rt = time.time(); self._rn_rate = 0; self._rv = 0.0

        for op in range(8):
            btn = self._make_op_btn(op)
            pan_ops.body().addWidget(btn)
            self._op_btns[op] = btn
        pan_ops.body().addStretch(); lay.addWidget(pan_ops, 5)

        right = QWidget(); right.setStyleSheet("background:transparent;")
        rl = QVBoxLayout(right); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(8)

        pan_ws = InstrumentPanel("Windshield View", A_TEAL2)
        self._ws = WindshieldWidget(); pan_ws.body().addWidget(self._ws)
        rl.addWidget(pan_ws, 3)

        pan_st = InstrumentPanel("TX Statistics", A_GREEN)
        self._stat: dict[str, QLabel] = {}
        sg = QHBoxLayout(); sg.setSpacing(16)
        for k, t in [("frames", "Frames TX"), ("rate", "Rate"),
                     ("op", "WiperOp"), ("alive", "Alive")]:
            col = QVBoxLayout(); col.setSpacing(2)
            col.addWidget(_lbl(t, 10, False, W_TEXT_DIM))
            v = _lbl("--", 14, True, A_TEAL, True)
            self._stat[k] = v; col.addWidget(v); sg.addLayout(col)
        sg.addStretch(); pan_st.body().addLayout(sg); rl.addWidget(pan_st, 1)
        lay.addWidget(right, 4)
        return w

    def _make_op_btn(self, op: int) -> QFrame:
        d = WOP[op]; f = QFrame(); f.setFixedHeight(48)
        f.setStyleSheet(
            f"QFrame{{background:{W_PANEL2};border:1px solid {W_BORDER};border-radius:2px;}}"
            f"QFrame:hover{{background:{W_PANEL};border-color:{d['color']};}}")
        f.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(f); lay.setContentsMargins(8, 0, 10, 0); lay.setSpacing(8)
        hex_l = _lbl(f"0x{op:02X}", 11, True, W_TEXT_DIM, True); hex_l.setFixedWidth(34)
        hex_l.setStyleSheet(
            f"color:{W_TEXT_DIM};background:{W_PANEL3};"
            f"border:1px solid {W_BORDER};padding:1px 4px;border-radius:2px;")
        info = QVBoxLayout(); info.setSpacing(0)
        nm = _lbl(d["label"], 11, True, W_TEXT)
        ds = _lbl(d["desc"],  10, False, W_TEXT_DIM)
        rq = _lbl(d["req"],    9, False, W_BORDER2, True)
        info.addWidget(nm); info.addWidget(ds); info.addWidget(rq)
        led = StatusLed(8)
        lay.addWidget(hex_l); lay.addLayout(info, 1); lay.addWidget(led)
        f._op = op; f._hex = hex_l; f._nm = nm; f._led = led   # type: ignore[attr-defined]
        f.mousePressEvent = lambda e, x=op: self._select_op(x)  # type: ignore[method-assign]
        return f

    def _select_op(self, op: int) -> None:
        self._cur_op = op; self._wiper_setter(op); d = WOP[op]
        self._ws.set_op(op)
        for o, btn in self._op_btns.items():
            if o == op:
                btn.setStyleSheet(
                    f"QFrame{{background:{W_PANEL};"
                    f"border:1.5px solid {d['color']};border-left:3px solid {d['color']};"
                    f"border-radius:2px;}}")
                btn._nm.setStyleSheet(f"color:{d['color']};font-weight:bold;background:transparent;")   # type: ignore[attr-defined]
                btn._led.set_state(True, d["color"]); btn._hex.setStyleSheet(   # type: ignore[attr-defined]
                    f"color:{d['color']};background:{W_PANEL3};"
                    f"border:1px solid {d['color']};padding:1px 4px;border-radius:2px;")
            else:
                btn.setStyleSheet(
                    f"QFrame{{background:{W_PANEL2};border:1px solid {W_BORDER};border-radius:2px;}}"
                    f"QFrame:hover{{background:{W_PANEL};border-color:{WOP[o]['color']};}}")
                btn._nm.setStyleSheet(f"color:{W_TEXT};font-weight:bold;background:transparent;")   # type: ignore[attr-defined]
                btn._led.set_state(False); btn._hex.setStyleSheet(   # type: ignore[attr-defined]
                    f"color:{W_TEXT_DIM};background:{W_PANEL3};"
                    f"border:1px solid {W_BORDER};padding:1px 4px;border-radius:2px;")

    def on_wiper_sent(self, op: int, seq: int) -> None:
        self._seq = seq; self._rn_rate += 1; now = time.time()
        if now - self._rt >= 1.0:
            self._rv = self._rn_rate / (now - self._rt); self._rt = now; self._rn_rate = 0
        self._al = (self._al + 1) & 0xFF
        self._stat["frames"].setText(str(seq))
        self._stat["rate"].setText(f"{self._rv:.1f} Hz")
        self._stat["op"].setText(f"0x{op:02X} {WOP[op]['name']}")
        self._stat["op"].setStyleSheet(
            f"color:{WOP[op]['color']};font-weight:bold;background:transparent;")
        self._stat["alive"].setText(f"0x{self._al:02X}")

    # ── LIN Bus Signal ────────────────────────────────────────
    def _build_signal(self) -> QWidget:
        w = QWidget(); w.setStyleSheet(f"background:{W_BG};")
        lay = QVBoxLayout(w); lay.setContentsMargins(8, 6, 8, 6); lay.setSpacing(6)

        hdr_pan = InstrumentPanel("LIN Bus Monitor", LIN_TX_C)
        rh = QHBoxLayout(); rh.setSpacing(10)
        self._led_lin = StatusLed(11)
        self.lbl_lin  = _lbl("DISCONNECTED", 10, True, A_RED)
        rh.addWidget(self._led_lin); rh.addWidget(self.lbl_lin); rh.addStretch()
        for attr, lbl_txt, co in [("_cnt_tx", "TX", LIN_TX_C),
                                   ("_cnt_rx", "RX", LIN_RX_C),
                                   ("_cnt_tot", "TOTAL", A_TEAL2)]:
            v = _lbl("0", 15, True, co, True); setattr(self, attr, v)
            col = QVBoxLayout(); col.setSpacing(1)
            col.addWidget(_lbl(lbl_txt, 9, False, W_TEXT_DIM)); col.addWidget(v)
            rh.addLayout(col)
        hdr_pan.body().addLayout(rh); lay.addWidget(hdr_pan)

        osc_pan = InstrumentPanel("LIN Bus Signal — Rolling 30s Window", LIN_TX_C)
        self._osc = LINOscilloscope()
        osc_pan.body().setContentsMargins(0, 4, 0, 4)
        osc_pan.body().addWidget(self._osc)
        lay.addWidget(osc_pan, 1)
        self._ltx = 0; self._lrx = 0
        return w

    # ── LIN Frame Table ───────────────────────────────────────
    def _build_table(self) -> QWidget:
        w = QWidget(); w.setStyleSheet(f"background:{W_BG};")
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)

        tb = QFrame(); tb.setFixedHeight(34)
        tb.setStyleSheet(
            f"QFrame{{background:{W_TOOLBAR};border-bottom:1px solid {W_BORDER};}}")
        tl = QHBoxLayout(tb); tl.setContentsMargins(10, 0, 10, 0); tl.setSpacing(8)
        tl.addWidget(_lbl("Filter:", 10, False, W_TEXT_DIM))

        self._combo = QComboBox()
        self._combo.addItems(["All Frames", "TX", "RX_HDR"])
        self._combo.setFixedWidth(110); self._combo.setFixedHeight(24)
        self._combo.setStyleSheet(
            f"QComboBox{{background:{W_PANEL};color:{W_TEXT};"
            f"border:1px solid {W_BORDER};border-radius:2px;"
            f"padding:1px 6px;font-size:10pt;font-family:{FONT_MONO};}}"
            f"QComboBox::drop-down{{border:none;width:16px;}}"
            f"QComboBox QAbstractItemView{{background:{W_PANEL2};color:{W_TEXT};"
            f"border:1px solid {W_BORDER};selection-background-color:{W_PANEL3};}}")
        self._combo.currentTextChanged.connect(self._filter_tbl)

        cb = QCheckBox("Auto-scroll"); cb.setChecked(True)
        cb.setStyleSheet(f"color:{W_TEXT};background:transparent;font-size:10pt;")
        cb.toggled.connect(lambda v: self._tbl.set_auto(v) if hasattr(self, "_tbl") else None)

        btn_clr = _cd_btn("Clear", "#888888", h=24, w=60)
        btn_clr.clicked.connect(lambda: self._tbl.clear_all())

        tl.addWidget(self._combo); tl.addWidget(cb); tl.addWidget(btn_clr); tl.addStretch()
        tl.addWidget(_lbl("Double-click -> full frame decode", 10, False, W_TEXT_DIM, True))
        lay.addWidget(tb)

        self._tbl = LINTableWidget(); lay.addWidget(self._tbl, 1)

        bot = QFrame(); bot.setFixedHeight(22)
        bot.setStyleSheet(
            f"QFrame{{background:{W_TOOLBAR};border-top:1px solid {W_BORDER};}}")
        bl = QHBoxLayout(bot); bl.setContentsMargins(10, 0, 10, 0)
        self.lbl_cnt = _lbl("0 frames", 10, False, W_TEXT_DIM, True)
        bl.addWidget(self.lbl_cnt); bl.addStretch()
        bl.addWidget(_lbl("Double-click = full decode", 9, False, W_TEXT_DIM, True))
        lay.addWidget(bot)

        self._all_evts: list[dict] = []
        return w

    # ── API publique ──────────────────────────────────────────
    def add_lin_event(self, ev: dict) -> None:
        t = ev.get("type", "")
        if t == "TX":
            op = ev.get("op", 0)
            self._osc.add_event("TX", WOP.get(op, {}).get("name", "?"))
            self._ltx += 1
        elif t == "RX_HDR":
            self._osc.add_event("RX", "HDR")
            self._lrx += 1
        self._cnt_tx.setText(str(self._ltx))
        self._cnt_rx.setText(str(self._lrx))
        self._cnt_tot.setText(str(self._ltx + self._lrx))
        self._all_evts.append(ev)
        if len(self._all_evts) > MAX_ROWS:
            self._all_evts = self._all_evts[-MAX_ROWS:]
        flt = self._combo.currentText()
        if flt in ("All Frames", "") or flt == t:
            self._tbl.add_event(ev)
        self.lbl_cnt.setText(f"{len(self._all_evts)} frames")

    def _filter_tbl(self) -> None:
        flt = self._combo.currentText(); self._tbl.clear_all()
        for ev in self._all_evts:
            if flt in ("All Frames", "") or flt == ev.get("type", ""):
                self._tbl.add_event(ev)

    def set_lin_status(self, msg: str, ok: bool) -> None:
        self._led_lin.set_state(ok)
        self.lbl_lin.setText(msg.upper())
        self.lbl_lin.setStyleSheet(
            f"color:{A_GREEN if ok else A_RED};background:transparent;")
