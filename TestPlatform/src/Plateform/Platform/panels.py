"""
WipeWash — Panneaux dock
MotorDashPanel, PumpPanel, VehicleRainPanel (+ IgnitionToggle),
CRSLINPanel (CRS Wiper Control + LIN oscilloscope + LIN table).
"""

import json
import datetime
import time
import threading
import os
from collections import deque

# ── Chargement DBC / LDF ──────────────────────────────────────────────────
# Les fichiers .dbc et .ldf sont dans le même dossier que panels.py.
# dbc_loader / ldf_loader sont copiés depuis rpibcm26 (même parseur).
# En cas d'échec silencieux, _DBC_CFG et _LDF_CFG restent None et le
# décodage se replie sur la logique manuelle existante.

_DBC_CFG  = None
_LDF_CFG  = None
_HERE     = os.path.dirname(os.path.abspath(__file__))

try:
    from dbc_loader import load_dbc, unpack_frame as _dbc_unpack
    _DBC_CFG = load_dbc(os.path.join(_HERE, "wiperwash.dbc"))
except Exception as _e:
    print(f"[panels] DBC not loaded: {_e}")

try:
    from ldf_loader import load_ldf
    _LDF_CFG = load_ldf(os.path.join(_HERE, "wiperwash.ldf"))
except Exception as _e:
    print(f"[panels] LDF not loaded: {_e}")

def _dbc_signals_for(msg_id: int) -> list[str]:
    """Retourne la liste des noms de signaux pour un message CAN."""
    if _DBC_CFG is None:
        return []
    msg = _DBC_CFG["messages"].get(msg_id)
    return list(msg.signals.keys()) if msg else []

def _ldf_signals_for(frame_id: str) -> list[str]:
    """Retourne la liste des signaux LIN pour un frame name."""
    if _LDF_CFG is None:
        return []
    frames = _LDF_CFG.get("frames", {})
    fr = frames.get(frame_id, {})
    return list(fr.get("signals", {}).keys())

def _decode_can_physical(ev: dict) -> str:
    """
    Décode les signaux physiques d'une trame CAN via le DBC.
    Retourne une chaîne  Signal=valeur unité  pour chaque signal.
    Si DBC absent ou trame inconnue, retourne chaîne vide (fallback manuel).
    """
    if _DBC_CFG is None:
        return ""
    cid  = ev.get("can_id_int", 0)
    msg  = _DBC_CFG["messages"].get(cid)
    if msg is None:
        return ""
    raw_hex = ev.get("data", "")
    try:
        data_bytes = bytes(int(h, 16) for h in raw_hex.split())
    except Exception:
        return ""
    try:
        vals = _dbc_unpack(msg, data_bytes)
    except Exception:
        return ""
    parts = []
    for sig_name, phys in vals.items():
        sig = msg.signals.get(sig_name)
        unit = sig.unit if sig else ""
        # Afficher valeur entière si pas de décimale utile
        if isinstance(phys, float) and phys == int(phys):
            phys = int(phys)
        # Résoudre les VAL_ (enum)
        val_str = str(phys)
        if sig and sig.values and isinstance(phys, (int, float)):
            val_str = sig.values.get(int(phys), val_str)
        parts.append(f"{sig_name}={val_str}{(' ' + unit) if unit else ''}")
    return "  ".join(parts)

def _decode_lin_physical(ev: dict) -> str:
    """
    Décode les signaux physiques d'une trame LIN via le LDF.
    """
    if _LDF_CFG is None:
        return ""
    raw = ev.get("raw", "")
    pid = ev.get("pid", "")
    frames = _LDF_CFG.get("frames", {})
    # Chercher le frame par PID
    target = None
    for fname, fdef in frames.items():
        if str(fdef.get("id", "")).lower() == str(pid).lower():
            target = fdef; break
    if target is None:
        return ""
    try:
        data_bytes = bytes(int(h, 16) for h in raw.split())
    except Exception:
        return ""
    signals = target.get("signals", {})
    parts = []
    for sig_name, sdef in signals.items():
        try:
            start = sdef.get("start_bit", 0)
            length = sdef.get("length", 8)
            # Extraction simple little-endian
            raw_int = int.from_bytes(data_bytes, "little")
            mask = (1 << length) - 1
            raw_val = (raw_int >> start) & mask
            factor  = sdef.get("factor", 1.0)
            offset  = sdef.get("offset", 0.0)
            phys    = raw_val * factor + offset
            unit    = sdef.get("unit", "")
            if phys == int(phys): phys = int(phys)
            parts.append(f"{sig_name}={phys}{(' ' + unit) if unit else ''}")
        except Exception:
            pass
    return "  ".join(parts)

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QLabel,
    QPushButton, QSlider, QScrollArea, QDoubleSpinBox,
    QComboBox, QCheckBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QDialog, QSizePolicy,
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui  import QColor, QFont, QPainter, QPen, QBrush, QPainterPath, QLinearGradient, QRadialGradient

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
try:
    from constants import CAN_CMD_C, CAN_STA_C, CAN_ACK_C, CAN_VEH_C, CAN_RAIN_C, CAN_GRID
except ImportError:
    CAN_CMD_C  = "#1A4E8E"
    CAN_STA_C  = "#1A6E1A"
    CAN_ACK_C  = "#8B4513"
    CAN_VEH_C  = "#007ACC"
    CAN_RAIN_C = "#D35400"
    CAN_GRID   = "#D8DADC"

# Canaux CAN pour l'oscilloscope : (can_id_int, label, color, amp, direction)
_CAN_CHANNELS = [
    (0x200, "0x200 Wiper_Cmd",    CAN_CMD_C,  0.12, "RX"),
    (0x201, "0x201 Wiper_Status", CAN_STA_C,  0.32, "TX"),
    (0x202, "0x202 Wiper_Ack",    CAN_ACK_C,  0.52, "TX"),
    (0x300, "0x300 Vehicle",      CAN_VEH_C,  0.70, "TX"),
    (0x301, "0x301 RainSensor",   CAN_RAIN_C, 0.88, "TX"),
]
_CAN_FRAME_COLORS = {
    0x200: CAN_CMD_C,
    0x201: CAN_STA_C,
    0x202: CAN_ACK_C,
    0x300: CAN_VEH_C,
    0x301: CAN_RAIN_C,
}
from workers       import send_pump_cmd
from car_comodo_3d import CarComodo3DReadOnly
from widgets_base  import (
    StatusLed, InstrumentPanel, NumericDisplay, LinearBar,
    _lbl, _hsep, _cd_btn,
)
from widgets_instruments import (
    MotorWidget, PumpWidget, WindshieldWidget, CarTopViewWidget,
    ArcGaugeWidget, SparklineWidget,
)
from widgets_motor_pump_enhanced import (
    RestContactWidget, SystemStatusWidget, TimeoutFSRWidget,
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

        # ── Moteurs (en haut) ────────────────────────────────────────
        row_motors = QHBoxLayout(); row_motors.setSpacing(8)

        # Motor Front avec arc gauge courant
        pan_f = InstrumentPanel("Motor Front", A_GREEN)
        self.motor_front = MotorWidget("FRONT")
        pan_f.body().addWidget(self.motor_front, 1)
        self._gauge_mf = ArcGaugeWidget(1.5, "A")
        pan_f.body().addWidget(self._gauge_mf)
        self._spark_mf = SparklineWidget(1.5, "#8DC63F")
        pan_f.body().addWidget(self._spark_mf)
        row_motors.addWidget(pan_f, 1)

        # Motor Rear avec arc gauge courant
        pan_r = InstrumentPanel("Motor Rear", A_TEAL)
        self.motor_rear = MotorWidget("REAR")
        pan_r.body().addWidget(self.motor_rear, 1)
        self._gauge_mr = ArcGaugeWidget(1.5, "A")
        pan_r.body().addWidget(self._gauge_mr)
        self._spark_mr = SparklineWidget(1.5, "#4DB8FF")
        pan_r.body().addWidget(self._spark_mr)
        row_motors.addWidget(pan_r, 1)
        root.addLayout(row_motors, 3)

        # ── Métriques bas ─────────────────────────────────────────────
        row_m = QHBoxLayout(); row_m.setSpacing(8)

        # Rest Contact — nouveau widget visuel clair
        pan_rest = InstrumentPanel("Rest Contact", A_AMBER)
        self._rest_widget = RestContactWidget()
        pan_rest.body().addWidget(self._rest_widget, 1)
        row_m.addWidget(pan_rest, 1)

        # Compatibilité backend : disp_cur / bar_cur / led_rest / lbl_rest masqués
        self.disp_cur = NumericDisplay("CURRENT", "A"); self.disp_cur.hide()
        self.bar_cur  = LinearBar(1.5, "A");            self.bar_cur.hide()
        self.led_rest = StatusLed(18); self.led_rest.hide()
        self.lbl_rest = _lbl("MOVING", 15, True, A_ORANGE); self.lbl_rest.hide()
        self.lbl_rest_sub = _lbl("GPIO26 · Front blade", 9, False, W_TEXT_DIM, True)
        self.lbl_rest_sub.hide()

        # System Status — nouveau widget visuel clair
        pan_st = InstrumentPanel("System Status", W_DOCK_HDR)
        self._sys_status_widget = SystemStatusWidget()
        pan_st.body().addWidget(self._sys_status_widget, 1)
        row_m.addWidget(pan_st, 2)

        # Compat backend labels (masqués)
        self._lbl_st_front = _lbl("—", 10, True, W_TEXT); self._lbl_st_front.hide()
        self._lbl_st_rear  = _lbl("—", 10, True, W_TEXT); self._lbl_st_rear.hide()
        self._lbl_st_speed = _lbl("—", 10, True, W_TEXT); self._lbl_st_speed.hide()
        self._lbl_st_cur   = _lbl("—A", 10, True, W_TEXT); self._lbl_st_cur.hide()
        self.lbl_status = _lbl("Waiting for connection…", 9, False, W_TEXT_DIM, True)
        self.lbl_status.hide()

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
            # rest_contact concerne uniquement la lame AVANT (GPIO26 BCM)
            # BCM : rest_contact_raw=False=GPIO0=lame AU REPOS / True=GPIO1=lame EN MOUVEMENT
            # Dans le dict TCP : rest_contact=1 quand repos (bouton relâché = GPIO=0)
            rest_raw = f.get("rest_contact", 0)
            self._apply({
                "front"  : "ON" if f.get("enable", 0) else "OFF",
                "rear"   : "ON" if r.get("enable", 0) else "OFF",
                "speed"  : "Speed2" if f.get("speed", 0) else "Speed1",
                "current": float(f.get("motor_current", 0)) + float(r.get("motor_current", 0)),
                "fault"  : bool(f.get("fault_status", 0)) or bool(r.get("fault_status", 0)),
                "rest"   : "PARKED" if rest_raw else "MOVING",
            })

    def _apply(self, s: dict) -> None:
        fault = bool(s.get("fault", False))
        self.motor_front.set_state(s.get("front", "OFF"), s.get("speed", "Speed1"))
        self.motor_rear.set_state(s.get("rear",  "OFF"), s.get("speed", "Speed1"))
        cur = float(s.get("current", 0))
        front_on = s.get("front", "OFF") == "ON"
        rear_on  = s.get("rear",  "OFF") == "ON"
        # Arc gauges + sparklines : courant affiché uniquement si le moteur tourne
        cur_f = cur if front_on else 0.0
        cur_r = cur if rear_on  else 0.0
        self._gauge_mf.set_value(cur_f, fault)
        self._gauge_mr.set_value(cur_r, fault)
        self._spark_mf.push(cur_f)
        self._spark_mr.push(cur_r)
        # Compat backend
        self.disp_cur.set_value(f"{cur:.3f}",
                                A_RED if fault else (A_ORANGE if cur > 0.8 else A_TEAL))
        self.bar_cur.set_value(cur, fault)
        parking = s.get("rest", "") == "PARKED"
        # Mise à jour Rest Contact widget amélioré
        self._rest_widget.set_state(parking)
        # Compat backend (masqués)
        self.led_rest.set_state(parking, A_GREEN if parking else A_ORANGE)
        if parking:
            self.lbl_rest.setText("PARKED")
            self.lbl_rest.setStyleSheet(
                f"color:{A_GREEN};font-weight:bold;background:transparent;")
        else:
            self.lbl_rest.setText("MOVING")
            self.lbl_rest.setStyleSheet(
                f"color:{A_ORANGE};font-weight:bold;background:transparent;")
        # System Status — widget amélioré
        front_s = s.get("front", "?"); rear_s = s.get("rear", "?")
        speed_s = s.get("speed", "?")
        cur_str = f"{cur:.3f} A"
        status_txt = ("FAULT FSR_003 — Overcurrent detected"
                      if fault else "System nominal")
        self._sys_status_widget.set_values(
            front_s, rear_s, speed_s, cur_str, fault, status_txt)
        # Compat backend labels (masqués)
        self._lbl_st_front.setText(front_s)
        self._lbl_st_rear.setText(rear_s)
        self._lbl_st_speed.setText(speed_s)
        self._lbl_st_cur.setText(cur_str)
        self.lbl_status.setText(status_txt)

    def set_connected(self, ok: bool, host: str = "") -> None:
        pass


# ═══════════════════════════════════════════════════════════
#  PUMP PANEL
# ═══════════════════════════════════════════════════════════


class PumpPanel(QWidget):
    def __init__(self, pump_getter, rte_getter=None, sim_getter=None, parent=None) -> None:
        super().__init__(parent)
        self._pump_getter  = pump_getter
        self._rte_getter   = rte_getter   # lambda → RTEClient (BCM) ou None
        self._sim_getter   = sim_getter   # lambda → TCPClient (Simulateur) ou None
        self.pump_start   = None
        self._iface_rem   = 0.0
        self._iface_dur   = 0.0
        self._src         = "BCM"
        self._state_str   = "OFF"
        self._rain_pct    = 0
        self.setStyleSheet(f"background:{W_BG};")
        self._build()
        self._t = QTimer(); self._t.timeout.connect(self._tick); self._t.start(100)

    def _build(self) -> None:
        root = QVBoxLayout(self); root.setContentsMargins(8, 6, 8, 6); root.setSpacing(6)

        # ── RANGÉE HAUTE : Pompe (gauche) | Mesures (droite) ──────────────
        row_top = QHBoxLayout(); row_top.setSpacing(8)

        # Pompe hydraulique
        pan_pump = InstrumentPanel("Hydraulic Pump", A_TEAL)
        self.pump_widget = PumpWidget()
        pan_pump.body().addWidget(self.pump_widget, 1)
        row_top.addWidget(pan_pump, 3)

        # Mesures : CURRENT + VOLTAGE empilés verticalement
        pan_m = InstrumentPanel("Measurements", A_GREEN)
        pan_m.body().setSpacing(6)

        pan_m.body().addWidget(_lbl("CURRENT", 8, True, W_TEXT_DIM, True))
        self._gauge_cur = ArcGaugeWidget(1.5, "A")
        pan_m.body().addWidget(self._gauge_cur)
        self._spark_cur = SparklineWidget(1.5, "#4DB8FF")
        pan_m.body().addWidget(self._spark_cur)

        pan_m.body().addSpacing(4)
        pan_m.body().addWidget(_lbl("VOLTAGE", 8, True, W_TEXT_DIM, True))
        self._gauge_vol = ArcGaugeWidget(14.0, "V")
        pan_m.body().addWidget(self._gauge_vol)
        self._spark_vol = SparklineWidget(14.0, "#8DC63F")
        pan_m.body().addWidget(self._spark_vol)

        row_top.addWidget(pan_m, 2)
        root.addLayout(row_top, 3)

        # ── RANGÉE BASSE : Timeout FSR_005 — nouveau widget visuel ─────────
        pan_to = InstrumentPanel("Timeout  FSR_005", A_AMBER)
        self._timeout_widget = TimeoutFSRWidget()
        pan_to.body().addWidget(self._timeout_widget, 1)
        root.addWidget(pan_to, 1)

        # Compat backend masqués
        self.disp_cur = NumericDisplay("CURRENT", "A");  self.disp_cur.hide()
        self.bar_cur  = LinearBar(1.5, "A");             self.bar_cur.hide()
        self.disp_vol = NumericDisplay("VOLTAGE", "V");  self.disp_vol.hide()
        self.bar_vol  = LinearBar(14, "V");              self.bar_vol.hide()
        self.lbl_alert = _lbl("", 0, False, A_RED);     self.lbl_alert.hide()
        self._led_to = StatusLed(14);                    self._led_to.hide()
        self.lbl_to_info = _lbl("Pump inactive", 11, False, W_TEXT_DIM, True)
        self.lbl_to_info.hide()
        self.disp_to = NumericDisplay("REMAINING", "s"); self.disp_to.hide()
        self.bar_to  = LinearBar(5.0, "s", ticks=5);    self.bar_to.hide()


    def update_display(self, s: dict) -> None:
        state       = s.get("state",          "OFF")
        cur         = float(s.get("current",       0.0))
        vol         = float(s.get("voltage",       0.0))
        fault       = s.get("fault",          False)
        reason      = s.get("fault_reason",   "")
        rem         = float(s.get("pump_remaining", 0.0))
        dur         = float(s.get("pump_duration",  0.0))
        src         = s.get("source",         "BCM")

        self._state_str = state; self._src = src
        self._iface_rem = rem;   self._iface_dur = dur

        disp = reason if fault and reason else state
        self.pump_widget.set_state(disp, cur, fault)
        # Sync intensité pluie si disponible (VehicleRainPanel envoie via _on_rain)
        if hasattr(self.pump_widget, "set_rain") and hasattr(self, "_rain_pct"):
            self.pump_widget.set_rain(self._rain_pct)

        # Arc gauges + sparklines
        self._gauge_cur.set_value(cur, fault)
        self._gauge_vol.set_value(vol, False)
        self._spark_cur.push(cur)
        self._spark_vol.push(vol)

        self.disp_cur.set_value(f"{cur:.3f}",
                                A_RED if cur > 1 else (A_ORANGE if cur > 0.7 else A_TEAL))
        self.bar_cur.set_value(cur, False)
        self.disp_vol.set_value(f"{vol:.3f}", A_TEAL)
        self.bar_vol.set_value(vol, False)

        active = state in ("FORWARD", "BACKWARD") and not fault
        if active and src == "BCM":
            if self.pump_start is None:
                self.pump_start = time.time()
            self._timeout_widget.set_state(
                max(0, 5.0 - (time.time() - self.pump_start)),
                5.0, True, "BCM", "BCM Mode — FSR_005 active (5s cutoff)")
        elif active:
            self.pump_start = None
            self._timeout_widget.set_state(
                rem, dur, True, "INTERFACE", f"Interface — {rem:.1f}s / {dur:.1f}s")
        else:
            self.pump_start = None
            self._timeout_widget.set_state(0.0, 5.0, False, "", "Pump inactive")

    def _tick(self) -> None:
        if self.pump_start and self._src == "BCM":
            rem = max(0, 5.0 - (time.time() - self.pump_start))
            self._timeout_widget.set_state(
                rem, 5.0, True, "BCM", "BCM Mode — FSR_005 active (5s cutoff)")
            self.disp_to.set_value(f"{rem:.1f}", A_RED if rem < 1.5 else A_AMBER)
            self.bar_to.set_value(5 - rem)
        elif self._src == "INTERFACE" and self._state_str in ("FORWARD", "BACKWARD"):
            rem = self._iface_rem; dur = self._iface_dur
            if dur > 0:
                self._timeout_widget.set_state(
                    rem, dur, True, "INTERFACE", f"Interface — {rem:.1f}s / {dur:.1f}s")
                self.disp_to.set_value(f"{rem:.1f}", A_RED if rem < 1.5 else A_TEAL)
                self.bar_to.set_value(dur - rem)

    def on_connected(self, h: str)   -> None: pass
    def on_disconnected(self)         -> None: pass


# ═══════════════════════════════════════════════════════════
#  VEHICLE & RAIN PANEL
# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════
#  SPEED KNOB — arc gauge + ACCEL/BRAKE buttons
# ═══════════════════════════════════════════════════════════
class _SpeedKnob(QWidget):
    """Arc speedometer avec boutons ACCEL/BRAKE — remplace QSlider speed.
    Émet value_changed(int) avec valeur 0-2000 (×10 km/h) comme l'ancien slider."""
    value_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._val = 0          # 0-2000 (÷10 = km/h)
        self._step_slow = 10   # +1 km/h
        self._step_fast = 100  # +10 km/h
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._auto_step)
        self._direction = 0
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._build()

    def _build(self):
        vl = QVBoxLayout(self); vl.setContentsMargins(0, 0, 0, 2); vl.setSpacing(3)
        self._arc = _SpeedArc(self)
        self._arc.setMinimumHeight(72)
        vl.addWidget(self._arc, 1)
        row = QHBoxLayout(); row.setSpacing(4)
        self._btn_a = QPushButton("▲ ACCEL"); self._btn_a.setFixedHeight(20); self._btn_a.setFixedWidth(60)
        self._btn_b = QPushButton("▼ BRAKE"); self._btn_b.setFixedHeight(20); self._btn_b.setFixedWidth(60)
        for btn, bg_col, txt_col, brd_col in [
                (self._btn_a, "#18100A", "#E8A020", "#A07010"),
                (self._btn_b, "#18080A", "#FF3020", "#A02010")]:
            btn.setFont(QFont(FONT_UI, 7, QFont.Weight.Bold))
            btn.setStyleSheet(
                f"QPushButton{{background:{bg_col};color:{txt_col};"
                f"border:1px solid {brd_col};border-radius:3px;padding:0px 3px;"
                f"letter-spacing:0.5px;}}"
                f"QPushButton:hover{{background:{brd_col}40;color:{txt_col};}}"
                f"QPushButton:pressed{{background:{brd_col}60;}}")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_a.pressed.connect(lambda: self._start(1))
        self._btn_a.released.connect(self._stop)
        self._btn_b.pressed.connect(lambda: self._start(-1))
        self._btn_b.released.connect(self._stop)
        row.addWidget(self._btn_a); row.addWidget(self._btn_b)
        vl.addLayout(row)

    def _start(self, direction):
        self._direction = direction
        self._do_step()
        self._timer.start()

    def _stop(self):
        self._timer.stop()
        self._direction = 0

    def _do_step(self):
        step = self._step_fast if (self._val > 200 and self._direction > 0) else self._step_slow
        self._val = max(0, min(2000, self._val + self._direction * step))
        self._arc.set_value(self._val)
        self.value_changed.emit(self._val)

    def _auto_step(self):
        if self._direction != 0:
            self._do_step()

    def value(self): return self._val   # compatibilité API slider


class _SpeedArc(QWidget):
    """Arc graphique speedomètre."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._val = 0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_value(self, v):
        self._val = v; self.update()

    def paintEvent(self, _):
        import math
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        # ── Fond Audi : noir profond avec vignette radiale ──
        bg_grad = QRadialGradient(W/2, H/2, max(W, H)*0.7)
        bg_grad.setColorAt(0, QColor("#0D0D0F"))
        bg_grad.setColorAt(1, QColor("#050507"))
        p.fillRect(0, 0, W, H, QBrush(bg_grad))

        cx = W // 2; cy = int(H * 0.82)
        R = min(cx - 6, cy - 4, 54)
        pct = self._val / 2000.0
        spd = self._val / 10.0

        # ── Anneau extérieur chromé ──
        chrome = QLinearGradient(cx-R-6, cy-R-6, cx+R+6, cy+R+6)
        chrome.setColorAt(0, QColor("#3A3A42")); chrome.setColorAt(0.5, QColor("#888890"))
        chrome.setColorAt(1, QColor("#2A2A30"))
        p.setPen(QPen(QBrush(chrome), 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(cx-R-5, cy-R-5, (R+5)*2, (R+5)*2)

        # ── Arc fond épais ──
        pen_bg = QPen(QColor("#1A1A22"), 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen_bg); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(cx-R, cy-R, R*2, R*2, 225*16, -270*16)

        # ── Arc rempli — orange Audi puis rouge danger ──
        if pct > 0:
            nc = "#E8A020" if pct < 0.65 else ("#FF6010" if pct < 0.85 else "#FF2020")
            pen_fill = QPen(QColor(nc), 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(pen_fill)
            p.drawArc(cx-R, cy-R, R*2, R*2, 225*16, int(-270*16*pct))

        # ── Graduations style Audi ──
        for i in range(21):
            a_deg = -225 + 270 * i / 20
            a_rad = math.radians(a_deg)
            is_major = (i % 4 == 0)
            outer_r = R + 2
            inner_r = R - (7 if is_major else 4)
            x1 = cx + outer_r * math.cos(a_rad); y1 = cy - outer_r * math.sin(a_rad)
            x2 = cx + inner_r * math.cos(a_rad); y2 = cy - inner_r * math.sin(a_rad)
            if is_major:
                tick_col = "#E8A020" if (i / 20 <= pct) else "#404048"
                p.setPen(QPen(QColor(tick_col), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            else:
                tick_col = "#C07010" if (i / 20 <= pct) else "#252530"
                p.setPen(QPen(QColor(tick_col), 1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(int(x1), int(y1), int(x2), int(y2))

        # ── Labels vitesse (0 50 100 150 200) ──
        p.setFont(QFont(FONT_MONO, 5, QFont.Weight.Bold))
        for i, label in enumerate([0, 50, 100, 150, 200]):
            a_deg = -225 + 270 * (i * 4) / 20
            a_rad = math.radians(a_deg)
            lx = int(cx + (R - 14) * math.cos(a_rad))
            ly = int(cy - (R - 14) * math.sin(a_rad))
            p.setPen(QPen(QColor("#8888A0")))
            p.drawText(lx - 8, ly - 5, 16, 10, Qt.AlignmentFlag.AlignCenter, str(label))

        # ── Aiguille rouge Audi — fine et lumineuse ──
        angle_deg = -225 + 270 * pct
        angle_r = math.radians(angle_deg)
        needle_len = R - 7
        nx = cx + needle_len * math.cos(angle_r)
        ny = cy - needle_len * math.sin(angle_r)
        back_len = 9
        bx = cx - back_len * math.cos(angle_r)
        by = cy + back_len * math.sin(angle_r)
        # Halo rouge derrière l'aiguille
        glow_pen = QPen(QColor("#FF200040"), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(glow_pen); p.drawLine(int(bx), int(by), int(nx), int(ny))
        # Aiguille principale rouge vif
        p.setPen(QPen(QColor("#FF2020"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(int(bx), int(by), int(nx), int(ny))
        # Pivot central chromé
        grad_c = QRadialGradient(cx, cy, 6)
        grad_c.setColorAt(0, QColor("#C0C0CC")); grad_c.setColorAt(0.5, QColor("#707080"))
        grad_c.setColorAt(1, QColor("#202028"))
        p.setBrush(QBrush(grad_c)); p.setPen(QPen(QColor("#90909860"), 1))
        p.drawEllipse(cx-5, cy-5, 10, 10)
        p.setBrush(QBrush(QColor("#FF202080"))); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(cx-2, cy-2, 4, 4)

        # ── Affichage numérique style Audi MMI ──
        p.setFont(QFont(FONT_MONO, 14, QFont.Weight.Bold))
        p.setPen(QPen(QColor("#F0F0FF")))
        txt = f"{spd:.0f}"
        p.drawText(cx-32, cy-R+2, 64, 22, Qt.AlignmentFlag.AlignCenter, txt)
        p.setFont(QFont(FONT_MONO, 6))
        p.setPen(QPen(QColor("#606070")))
        p.drawText(cx-20, cy-R+23, 40, 11, Qt.AlignmentFlag.AlignCenter, "km/h")


# ═══════════════════════════════════════════════════════════
#  RAIN KNOB — arc gauge goutte + boutons +/-
# ═══════════════════════════════════════════════════════════
class _RainKnob(QWidget):
    """Arc rain gauge avec boutons +/- — remplace QSlider rain.
    Émet value_changed(int) avec valeur 0-100 comme l'ancien slider."""
    value_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._val = 0
        self._timer = QTimer(self); self._timer.setInterval(120)
        self._timer.timeout.connect(self._auto_step)
        self._direction = 0
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._build()

    def _build(self):
        vl = QVBoxLayout(self); vl.setContentsMargins(0, 0, 0, 4); vl.setSpacing(4)
        self._arc = _RainArc(self)
        self._arc.setMinimumHeight(72)
        vl.addWidget(self._arc, 1)
        row = QHBoxLayout(); row.setSpacing(4)
        self._btn_p = QPushButton("＋"); self._btn_p.setFixedHeight(20); self._btn_p.setFixedWidth(38)
        self._btn_m = QPushButton("－"); self._btn_m.setFixedHeight(20); self._btn_m.setFixedWidth(38)
        for btn, bg_col, txt_col, brd_col in [
                (self._btn_p, "#080E1A", "#3090E8", "#1860A8"),
                (self._btn_m, "#080E1A", "#3090E8", "#1860A8")]:
            btn.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
            btn.setStyleSheet(
                f"QPushButton{{background:{bg_col};color:{txt_col};"
                f"border:1px solid {brd_col};border-radius:3px;padding:0px 2px;}}"
                f"QPushButton:hover{{background:{brd_col}40;color:#60B0F0;}}"
                f"QPushButton:pressed{{background:{brd_col}60;}}")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_p.pressed.connect(lambda: self._start(1))
        self._btn_p.released.connect(self._stop)
        self._btn_m.pressed.connect(lambda: self._start(-1))
        self._btn_m.released.connect(self._stop)
        row.addWidget(self._btn_p); row.addWidget(self._btn_m)
        vl.addLayout(row)

    def _start(self, d):
        self._direction = d; self._do_step(); self._timer.start()

    def _stop(self):
        self._timer.stop(); self._direction = 0

    def _do_step(self):
        self._val = max(0, min(100, self._val + self._direction * 5))
        self._arc.set_value(self._val)
        self.value_changed.emit(self._val)

    def _auto_step(self):
        if self._direction != 0: self._do_step()

    def value(self): return self._val


class _RainArc(QWidget):
    """Arc graphique rain intensity avec goutte centrale."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._val = 0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_value(self, v):
        self._val = v; self.update()

    def paintEvent(self, _):
        import math
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        # ── Fond Audi noir ──
        bg_grad = QRadialGradient(W/2, H/2, max(W, H)*0.7)
        bg_grad.setColorAt(0, QColor("#0D0D0F")); bg_grad.setColorAt(1, QColor("#050507"))
        p.fillRect(0, 0, W, H, QBrush(bg_grad))

        cx = W // 2; cy = int(H * 0.80)
        R = min(cx - 6, cy - 4, 50)
        pct = self._val / 100.0

        # ── Anneau chromé ──
        chrome = QLinearGradient(cx-R-5, cy-R-5, cx+R+5, cy+R+5)
        chrome.setColorAt(0, QColor("#3A3A42")); chrome.setColorAt(0.5, QColor("#888890"))
        chrome.setColorAt(1, QColor("#2A2A30"))
        p.setPen(QPen(QBrush(chrome), 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(cx-R-4, cy-R-4, (R+4)*2, (R+4)*2)

        # ── Arc fond ──
        pen_bg = QPen(QColor("#1A1A22"), 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen_bg); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(cx-R, cy-R, R*2, R*2, 225*16, -270*16)

        # ── Arc rempli — bleu MMI progressif ──
        if pct > 0:
            rc = "#3090E8" if pct < 0.5 else ("#1060C0" if pct < 0.80 else "#0040A0")
            pen_fill = QPen(QColor(rc), 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(pen_fill)
            p.drawArc(cx-R, cy-R, R*2, R*2, 225*16, int(-270*16*pct))

        # ── Graduations ──
        for i in range(11):
            a_deg = -225 + 270 * i / 10
            a_rad = math.radians(a_deg)
            is_major = (i % 5 == 0) or i == 10
            outer_r = R + 2
            inner_r = R - (7 if is_major else 4)
            x1 = cx + outer_r * math.cos(a_rad); y1 = cy - outer_r * math.sin(a_rad)
            x2 = cx + inner_r * math.cos(a_rad); y2 = cy - inner_r * math.sin(a_rad)
            if is_major:
                tick_col = "#3090E8" if (i / 10 <= pct) else "#404048"
                p.setPen(QPen(QColor(tick_col), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            else:
                tick_col = "#2060A8" if (i / 10 <= pct) else "#252530"
                p.setPen(QPen(QColor(tick_col), 1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(int(x1), int(y1), int(x2), int(y2))

        # ── Cercle intérieur sombre ──
        ir = R - 13
        glow = QRadialGradient(cx, cy, ir)
        glow.setColorAt(0, QColor("#0C1018")); glow.setColorAt(1, QColor("#060810"))
        p.setBrush(QBrush(glow)); p.setPen(QPen(QColor("#10182A"), 1))
        p.drawEllipse(cx-ir, cy-ir, ir*2, ir*2)

        # ── Goutte d'eau style Audi ──
        ds = int(ir * 0.46)
        dc = QColor("#3090E8") if pct > 0 else QColor("#20304A")
        drop = QPainterPath()
        drop.moveTo(cx, cy - ds)
        drop.cubicTo(cx + ds*0.55, cy - ds*0.2, cx + ds*0.55, cy + ds*0.4, cx, cy + ds*0.52)
        drop.cubicTo(cx - ds*0.55, cy + ds*0.4, cx - ds*0.55, cy - ds*0.2, cx, cy - ds)
        dg = QRadialGradient(cx - ds*0.25, cy - ds*0.25, ds*0.85)
        dg.setColorAt(0, QColor("#90C8F0") if pct > 0 else QColor("#2A3A50"))
        dg.setColorAt(0.5, dc); dg.setColorAt(1, dc.darker(150))
        p.setBrush(QBrush(dg)); p.setPen(QPen(dc.darker(180), 1))
        p.drawPath(drop)

        # ── Aiguille rouge Audi ──
        angle_deg = -225 + 270 * pct
        angle_r = math.radians(angle_deg)
        nx = cx + (R - 8) * math.cos(angle_r)
        ny = cy - (R - 8) * math.sin(angle_r)
        bx = cx - 8 * math.cos(angle_r)
        by = cy + 8 * math.sin(angle_r)
        # Halo
        p.setPen(QPen(QColor("#FF202040"), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(int(bx), int(by), int(nx), int(ny))
        # Aiguille
        p.setPen(QPen(QColor("#FF2020"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(int(bx), int(by), int(nx), int(ny))
        # Pivot chromé
        grad_c = QRadialGradient(cx, cy, 5)
        grad_c.setColorAt(0, QColor("#C0C0CC")); grad_c.setColorAt(0.5, QColor("#707080"))
        grad_c.setColorAt(1, QColor("#202028"))
        p.setBrush(QBrush(grad_c)); p.setPen(QPen(QColor("#90909860"), 1))
        p.drawEllipse(cx-4, cy-4, 8, 8)

        # ── Valeur numérique ──
        p.setFont(QFont(FONT_MONO, 12, QFont.Weight.Bold))
        p.setPen(QPen(QColor("#F0F0FF")))
        p.drawText(cx-22, cy-ir+2, 44, 18, Qt.AlignmentFlag.AlignCenter, f"{self._val}")
        p.setFont(QFont(FONT_MONO, 6))
        p.setPen(QPen(QColor("#606070")))
        p.drawText(cx-14, cy-ir+19, 28, 10, Qt.AlignmentFlag.AlignCenter, "%")


class IgnitionToggle(QWidget):
    changed = Signal(str)
    STATES  = ["OFF", "ACC", "ON"]
    # Couleurs Audi MMI : OFF=gris, ACC=ambre, ON=rouge lumineux
    COLORS  = {
        "OFF": ("#9090A0", "#18181E", "#50505A"),
        "ACC": ("#E8A020", "#2A1A00", "#C08010"),
        "ON":  ("#FF3020", "#2A0800", "#D02010"),
    }
    ICONS = {"OFF": "○", "ACC": "◑", "ON": "●"}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._s = "OFF"
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(4)
        self._btns: dict[str, QPushButton] = {}
        for s in self.STATES:
            b = QPushButton(f"{self.ICONS[s]}  {s}")
            b.setFixedHeight(26)
            b.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, st=s: self._sel(st))
            lay.addWidget(b); self._btns[s] = b
        self._refresh()

    def _sel(self, st: str) -> None:
        self._s = st; self._refresh(); self.changed.emit(st)

    def _refresh(self) -> None:
        for s, b in self._btns.items():
            fg, bg, brd = self.COLORS[s]
            if s == self._s:
                b.setStyleSheet(
                    f"QPushButton{{background:{bg};color:{fg};"
                    f"border:1.5px solid {fg};"
                    f"border-radius:3px;padding:2px 6px;font-weight:bold;"
                    f"letter-spacing:1px;}}"
                    f"QPushButton:hover{{background:{brd}30;color:{fg};}}")
            else:
                b.setStyleSheet(
                    f"QPushButton{{background:#141418;color:#404050;"
                    f"border:1px solid #303038;border-radius:3px;padding:2px 6px;}}"
                    f"QPushButton:hover{{background:#1E1E26;color:#707080;"
                    f"border-color:#505060;}}")

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
        self._pump_ref  = None   # référence PumpPanel pour sync rain gauge
        self.setStyleSheet("background:#08080C;")
        self._build()

    def _build(self) -> None:
        scroll = QScrollArea(self); scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:#08080C;}")
        c = QWidget(); c.setStyleSheet("background:#08080C;"); scroll.setWidget(c)
        vl = QVBoxLayout(self); vl.setContentsMargins(0, 0, 0, 0); vl.addWidget(scroll)
        root = QVBoxLayout(c); root.setContentsMargins(5, 3, 5, 3); root.setSpacing(3)

        # TX labels (conservés pour _sv/_sr mais non affichés)
        self.lbl_tx_v = _lbl("VEH  —", 8, False, A_TEAL, True)
        self.lbl_tx_r = _lbl("RAIN —", 8, False, A_GREEN, True)

        # Stubs NumericDisplay pour compatibilité _on_spd/_on_rain
        self.disp_spd  = type("_Stub", (), {"set_value": lambda self, *a: None})()
        self.disp_rain = type("_Stub", (), {"set_value": lambda self, *a: None})()

        main_row = QHBoxLayout(); main_row.setSpacing(8)

        # ── Vehicle Status ──
        pan_v = InstrumentPanel("Vehicle Status", A_TEAL2)
        pan_v.body().addWidget(_lbl("IGNITION", 9, True, W_TEXT_DIM))
        self.ign = IgnitionToggle(); self.ign.changed.connect(self._sv)
        pan_v.body().addWidget(self.ign); pan_v.body().addWidget(_hsep())

        pan_v.body().addWidget(_lbl("REVERSE GEAR", 9, True, W_TEXT_DIM))
        rr = QHBoxLayout(); rr.setSpacing(6)
        self._led_rev = StatusLed(11); self.lbl_rev = _lbl("NORMAL", 11, True, W_TEXT_DIM)
        self.btn_rev  = _cd_btn("TOGGLE", A_ORANGE, h=22)
        self.btn_rev.clicked.connect(self._toggle_rev)
        rr.addWidget(self._led_rev); rr.addWidget(self.lbl_rev)
        rr.addStretch(); rr.addWidget(self.btn_rev)
        pan_v.body().addLayout(rr); pan_v.body().addWidget(_hsep())

        pan_v.body().addWidget(_lbl("VEHICLE SPEED  (km/h)", 9, True, W_TEXT_DIM))
        # SpeedKnob — arc gauge avec boutons ACCEL/BRAKE (backend _on_spd inchangé)
        self._spd_knob = _SpeedKnob()
        self._spd_knob.value_changed.connect(self._on_spd)
        # Alias pour compatibilité backend (sld_spd.value() non utilisé directement)
        self.sld_spd = self._spd_knob
        pan_v.body().addWidget(self._spd_knob)
        main_row.addWidget(pan_v, 3)

        # ── Vue voiture : CarHTMLWidget injectée depuis main_window ──
        # (le widget HTML est placé au centre de la page combinée LIN+CAN)
        self._car_html = None  # sera défini via set_car_widget()

        # ── Rain Sensor ──
        pan_r = InstrumentPanel("Rain Sensor", A_TEAL)
        pan_r.body().addWidget(_lbl("RAIN INTENSITY  (%)", 9, True, W_TEXT_DIM))
        # RainKnob — arc gauge goutte avec boutons +/- (backend _on_rain inchangé)
        self._rain_knob = _RainKnob()
        self._rain_knob.value_changed.connect(self._on_rain)
        self.sld_rain = self._rain_knob
        pan_r.body().addWidget(self._rain_knob)
        pan_r.body().addWidget(_hsep())
        pan_r.body().addWidget(_lbl("SENSOR STATUS", 9, True, W_TEXT_DIM))

        rs = QHBoxLayout(); rs.setSpacing(6)
        self._led_sens = StatusLed(8); self._led_sens.set_state(True, A_GREEN)  # kept for logic only, not in layout
        # Badge statut style Audi MMI
        self.lbl_sens = QPushButton("● OK")
        self.lbl_sens.setEnabled(False)
        self.lbl_sens.setFont(QFont(FONT_MONO, 9, QFont.Weight.Bold))
        self.lbl_sens.setFixedHeight(24)
        self.lbl_sens.setStyleSheet(
            "QPushButton{background:#0A1E0A;color:#40C040;"
            "border:1.5px solid #306030;border-radius:3px;padding:1px 8px;"
            "letter-spacing:1px;}")
        # Bouton simulation
        self.btn_sens = QPushButton("⚠ SIMULATE ERR")
        self.btn_sens.setFont(QFont(FONT_MONO, 7, QFont.Weight.Bold))
        self.btn_sens.setFixedHeight(24)
        self.btn_sens.setStyleSheet(
            "QPushButton{background:#1E0808;color:#C04040;"
            "border:1px solid #803030;border-radius:3px;padding:1px 6px;}"
            "QPushButton:hover{background:#2A0C0C;color:#E05050;"
            "border-color:#A04040;}"
            "QPushButton:pressed{background:#FF202020;}")
        self.btn_sens.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sens.clicked.connect(self._toggle_sens)
        rs.addWidget(self.lbl_sens, 1)
        rs.addWidget(self.btn_sens, 1)
        pan_r.body().addLayout(rs); pan_r.body().addStretch()
        main_row.addWidget(pan_r, 3)
        root.addLayout(main_row)

        # Barre basse (compteur TX uniquement — SEND NOW supprimé)
        bot = QHBoxLayout(); bot.setSpacing(8)
        self._tx_led = StatusLed(10)
        self.lbl_txc = _lbl("TX: 0", 10, True, A_TEAL, True)
        self._tx_off = QTimer(self); self._tx_off.setSingleShot(True)
        self._tx_off.timeout.connect(lambda: self._tx_led.set_state(False))
        bot.addWidget(self._tx_led); bot.addWidget(self.lbl_txc); bot.addStretch()
        root.addLayout(bot)

    # ── Callbacks UI ─────────────────────────────────────────
    def set_car_widget(self, w) -> None:
        """Reçoit la référence au CarHTMLWidget central (appelé depuis main_window)."""
        self._car_html = w
        if not hasattr(self, "_car_widgets"):
            self._car_widgets = []
        if w not in self._car_widgets:
            self._car_widgets.append(w)

    def add_car_widget(self, w) -> None:
        """Ajoute un CarHTMLWidget supplémentaire (ex: onglet Motor/Pump)."""
        if not hasattr(self, "_car_widgets"):
            self._car_widgets = []
        if w not in self._car_widgets:
            self._car_widgets.append(w)

    def _broadcast_car(self, fn: str, *args) -> None:
        """Appelle fn(*args) sur tous les car widgets enregistrés."""
        targets = getattr(self, "_car_widgets", [self._car_html] if self._car_html else [])
        for w in targets:
            if w:
                getattr(w, fn)(*args)

    def set_pump_panel(self, pump_panel) -> None:
        """Référence au PumpPanel pour synchroniser le rain gauge."""
        self._pump_ref = pump_panel

    def _on_spd(self, v: int) -> None:
        self._spd = v / 10.0
        self.disp_spd.set_value(f"{self._spd:.1f}")
        self._broadcast_car("set_speed", self._spd)
        self._sv()

    def _on_rain(self, v: int) -> None:
        self._rain = v
        self.disp_rain.set_value(f"{v}")
        self._broadcast_car("set_rain", v)
        if self._pump_ref and hasattr(self._pump_ref, "pump_widget"):
            pw = self._pump_ref.pump_widget
            if hasattr(pw, "set_rain"):
                pw.set_rain(v)
            if hasattr(self._pump_ref, "_rain_pct"):
                self._pump_ref._rain_pct = v
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
        self._broadcast_car("set_reverse", bool(self._rev))
        self._sv()

    def _toggle_sens(self) -> None:
        self._sensor_ok = not self._sensor_ok
        if self._sensor_ok:
            self._led_sens.set_state(True, A_GREEN)
            self.lbl_sens.setText("● OK")
            self.lbl_sens.setStyleSheet(
                "QPushButton{background:#0A1E0A;color:#40C040;"
                "border:1.5px solid #306030;border-radius:3px;padding:1px 8px;"
                "letter-spacing:1px;}")
            self.btn_sens.setText("⚠ SIMULATE ERR")
            self.btn_sens.setStyleSheet(
                "QPushButton{background:#1E0808;color:#C04040;"
                "border:1px solid #803030;border-radius:3px;padding:1px 6px;}"
                "QPushButton:hover{background:#2A0C0C;color:#E05050;"
                "border-color:#A04040;}"
                "QPushButton:pressed{background:#FF202020;}")
        else:
            self._led_sens.set_state(True, A_RED)
            self.lbl_sens.setText("⚠ ERROR")
            self.lbl_sens.setStyleSheet(
                "QPushButton{background:#200808;color:#FF4040;"
                "border:1.5px solid #A02020;border-radius:3px;padding:1px 8px;"
                "letter-spacing:1px;}")
            self.btn_sens.setText("✓ RESTORE OK")
            self.btn_sens.setStyleSheet(
                "QPushButton{background:#0A1A0A;color:#40A040;"
                "border:1px solid #306030;border-radius:3px;padding:1px 6px;}"
                "QPushButton:hover{background:#0C220C;color:#50C050;"
                "border-color:#408040;}"
                "QPushButton:pressed{background:#20FF2020;}")
        self._sr()

    # ── Envoi JSON ───────────────────────────────────────────
    def _sv(self, *_) -> None:
        w = self._getter()
        if not w: return
        self._ign = self.ign.get()
        self._broadcast_car("set_ignition", self._ign)
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

# ═══════════════════════════════════════════════════════════
#  CRS / LIN PANEL
# ═══════════════════════════════════════════════════════════
class LINOscilloscope(QWidget):
    WINDOW = 5.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._evts: deque = deque()
        self._paused = False
        self._pause_time = 0.0
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._t = QTimer(); self._t.timeout.connect(self.update); self._t.start(100)  # Optimisation: 100ms (au lieu de 40ms)

    def set_window(self, seconds: float) -> None:
        self.WINDOW = seconds

    def set_paused(self, paused: bool) -> None:
        if paused and not self._paused:
            self._pause_time = time.time()
        self._paused = paused
        if not paused:
            self._pause_time = 0.0

    def add_event(self, typ: str, label: str = "") -> None:
        if self._paused:
            return
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
        # Fond avec reflet vert KPIT (même style que car_simulator.html)
        bg = QLinearGradient(0, 0, W, H)
        bg.setColorAt(0, QColor("#FFFFFF")); bg.setColorAt(0.5, QColor("#F8FAFC")); bg.setColorAt(1, QColor("#F1F5F9"))
        p.fillRect(0, 0, W, H, QBrush(bg))
        # no color overlay — fond blanc pur
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

        vis = [ev for ev in self._evts if (now - ev[0]) <= self.WINDOW]
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
        p.drawText(W - MR - 28, MT, 28, 12, Qt.AlignmentFlag.AlignCenter,
                   "PAUSE" if self._paused else "LIVE")


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
                font-family:{FONT_MONO};font-size:9pt;
                selection-background-color:{A_GREEN_BG};selection-color:{W_TEXT};
            }}
            QHeaderView::section {{
                background:{W_TITLEBAR};color:{W_TEXT_HDR};border:none;
                border-bottom:1px solid {W_BORDER2};border-right:1px solid {W_BORDER};
                padding:2px 6px;font-family:{FONT_UI};font-size:9pt;font-weight:bold;
            }}
            QTableWidget::item {{ padding:1px 4px;border-bottom:1px solid {W_PANEL3}; }}
        """)
        self._rn   = 0
        self._evts: deque = deque()
        self._auto = True
        self.cellDoubleClicked.connect(self._dbl)

    def _dbl(self, row: int, _) -> None:
        if row < len(self._evts):
            self._show_detail(self._evts[row])

    def _show_detail(self, ev: dict) -> None:
        from PySide6.QtWidgets import QDialog, QVBoxLayout
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
            self.removeRow(0); self._evts.popleft(); self._rn -= 1
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
        self.setRowHeight(r, 18)
        if self._auto: self.scrollToBottom()

    def clear_all(self) -> None:
        self.setRowCount(0); self._rn = 0; self._evts.clear()

    def set_auto(self, v: bool) -> None:
        self._auto = v


class CRSLINPanel(QWidget):
    def __init__(self, wiper_setter, lin_sender=None, parent=None) -> None:
        super().__init__(parent)
        self._lin_sender = lin_sender   # callable(dict) → envoie JSON au simulateur crslin
        self._car_html   = None         # CarHTMLWidget injecté depuis main_window
        self.setStyleSheet(f"background:{W_BG};")
        vl = QVBoxLayout(self); vl.setContentsMargins(0, 0, 0, 0); vl.setSpacing(0)

        from PySide6.QtWidgets import QTabWidget
        tabs = QTabWidget()
        tabs.setStyleSheet(f"""
            QTabWidget::pane{{border:none;background:{W_BG};}}
            QTabBar{{background:{W_TOOLBAR};border-bottom:2px solid #8DC63F;}}
            QTabBar::tab{{background:{W_TOOLBAR};color:{W_TEXT_DIM};border:none;
                border-right:1px solid {W_SEP};padding:4px 12px;
                font-family:{FONT_UI};font-size:9pt;min-width:75px;}}
            QTabBar::tab:selected{{background:{W_BG};color:#8DC63F;
                border-top:2px solid #8DC63F;}}
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
        lay = QHBoxLayout(w); lay.setContentsMargins(5, 4, 5, 4); lay.setSpacing(5)

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

        # ── Rest Contact directement sous les boutons WOP ──
        self._ws = None
        pan_rc = InstrumentPanel("Rest Contact", A_AMBER)
        rc_row = QHBoxLayout(); rc_row.setSpacing(6)
        self._led_rc        = StatusLed(11)
        self._lbl_rc        = _lbl("MOVING", 10, True, A_ORANGE)
        self._lbl_rc_cycles = _lbl("Cycles: 0", 9, False, W_TEXT_DIM, True)
        rc_row.addWidget(self._led_rc)
        rc_row.addWidget(self._lbl_rc)
        rc_row.addStretch()
        rc_row.addWidget(self._lbl_rc_cycles)
        pan_rc.body().addLayout(rc_row)
        pan_ops.body().addWidget(pan_rc)
        pan_ops.body().addStretch()
        lay.addWidget(pan_ops, 8)   # élargi (était 6)

        # ── Stubs pour compatibilité avec update_crs_fault / _inject_crs_fault ──
        self._led_crs_fault = StatusLed(11)
        self._led_crs_fault.hide()
        self._lbl_crs_fault_val = _lbl("", 11, True, A_GREEN)
        self._lbl_crs_fault_val.hide()
        self._crs_fault_btns: dict[int, QPushButton] = {}

        # ── Colonne droite : TX Stats + Rear Wiper Status ──────────────────
        right = QWidget(); right.setStyleSheet("background:transparent;")
        rl = QVBoxLayout(right); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(8)

        # TX Statistics
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

        # ── Rear Wiper Status — barres blade + current (toujours présent) ──
        # Le panneau est TOUJOURS dans le layout pour éviter les problèmes de
        # recalcul Qt. On masque/montre uniquement le contenu interne via
        # _rear_content_widget (le header reste visible avec le titre coloré).
        self._pan_rear = InstrumentPanel("Rear Wiper Status", A_ORANGE)

        # Widget contenu interne — caché par défaut, visible op 6/7
        self._rear_content = QWidget()
        self._rear_content.setStyleSheet("background:transparent;")
        rear_cl = QVBoxLayout(self._rear_content)
        rear_cl.setContentsMargins(0, 0, 0, 0); rear_cl.setSpacing(4)

        self._rear_mode_lbl = _lbl("—", 10, True, A_ORANGE)
        rear_cl.addWidget(self._rear_mode_lbl)
        rear_cl.addWidget(_hsep())

        rear_cl.addWidget(_lbl("Blade position", 9, True, W_TEXT_DIM))
        self._rear_blade_disp = NumericDisplay("BLADE", "%")
        self._rear_blade_bar  = LinearBar(100.0, "%")
        rear_cl.addWidget(self._rear_blade_disp)
        rear_cl.addWidget(self._rear_blade_bar)
        rear_cl.addWidget(_hsep())

        rear_cl.addWidget(_lbl("Motor current", 9, True, W_TEXT_DIM))
        self._rear_cur_disp = NumericDisplay("CURRENT", "A")
        self._rear_cur_bar  = LinearBar(1.5, "A")
        rear_cl.addWidget(self._rear_cur_disp)
        rear_cl.addWidget(self._rear_cur_bar)
        rear_cl.addWidget(_hsep())

        rear_fault_row = QHBoxLayout(); rear_fault_row.setSpacing(6)
        self._rear_led_fault = StatusLed(11); self._rear_led_fault.set_state(False, A_GREEN)
        self._rear_lbl_fault = _lbl("NO FAULT", 10, True, A_GREEN)
        rear_fault_row.addWidget(self._rear_led_fault)
        rear_fault_row.addWidget(self._rear_lbl_fault)
        rear_fault_row.addStretch()
        rear_cl.addLayout(rear_fault_row)

        # Message affiché quand op != 6/7
        self._rear_inactive_lbl = _lbl("Select REAR WASH or REAR WIPE", 9, False, W_TEXT_DIM)
        self._rear_inactive_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._pan_rear.body().addWidget(self._rear_inactive_lbl)
        self._pan_rear.body().addWidget(self._rear_content)
        self._pan_rear.body().addStretch()

        # Etat initial : message inactif visible, contenu caché
        self._rear_content.hide()
        rl.addWidget(self._pan_rear, 1)

        lay.addWidget(right, 2)
        return w

    def _inject_crs_fault(self, val: int) -> None:
        """Envoie set_fault au simulateur crslin via LINWorker."""
        if self._lin_sender:
            self._lin_sender({"set_fault": val})
        # Highlight bouton actif
        FAULT_COLORS = {0x00: A_GREEN, 0x01: A_AMBER, 0x02: A_ORANGE, 0x04: A_RED}
        for v, btn in self._crs_fault_btns.items():
            if v == val:
                c = FAULT_COLORS.get(v, A_RED)
                btn.setStyleSheet(
                    f"QPushButton{{background:{c};color:#FFF;"
                    f"border:2px solid #8DC63F;border-radius:3px;"
                    f"padding:2px 4px;font-weight:bold;font-size:9pt;}}")
            else:
                btn.setStyleSheet("")

    def update_crs_fault(self, fault_val: int) -> None:
        """
        Met à jour l'affichage CRS fault reçu du BCM (rte.crs_fault).
        Appelé depuis on_motor_data ou on_lin_event.
        """
        FAULT_NAMES = {
            0x00: "NO FAULT",
            0x01: "STICK SENSOR",
            0x02: "SUPPLY",
            0x04: "INTERNAL COM",
        }
        name = FAULT_NAMES.get(fault_val, f"UNKNOWN")
        has_fault = fault_val != 0x00
        self._led_crs_fault.set_state(has_fault, A_RED if has_fault else A_GREEN)
        color = A_RED if has_fault else A_GREEN
        self._lbl_crs_fault_val.setText(f"0x{fault_val:02X}  {name}")
        self._lbl_crs_fault_val.setStyleSheet(
            f"color:{color};font-weight:bold;background:transparent;")

    def update_rest_contact(self, rest_raw: bool, blade_cycles: int) -> None:
        """
        Met à jour l'affichage rest contact + windshield temps réel.
        rest_raw : True=GPIO1=lame EN MOUVEMENT / False=GPIO0=lame AU REPOS
        """
        # rest_raw=False = lame AU REPOS → PARKED (bouton relâché = GPIO=0)
        parked = not rest_raw
        self._led_rc.set_state(parked, A_GREEN if parked else A_ORANGE)
        if parked:
            self._lbl_rc.setText("PARKED")
            self._lbl_rc.setStyleSheet(f"color:{A_GREEN};font-weight:bold;background:transparent;")
        else:
            self._lbl_rc.setText("MOVING")
            self._lbl_rc.setStyleSheet(f"color:{A_ORANGE};font-weight:bold;background:transparent;")
        self._lbl_rc_cycles.setText(f"Cycles: {blade_cycles}")



    def _make_op_btn(self, op: int) -> QFrame:
        d = WOP[op]; f = QFrame(); f.setMinimumHeight(50); f.setFixedHeight(50)
        f.setStyleSheet(
            f"QFrame{{background:{W_PANEL2};border:1.5px solid #CBD5E1;border-radius:6px;}}"
            f"QFrame:hover{{background:{W_PANEL};border-color:{d['color']};border-width:2px;}}")
        f.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(f); lay.setContentsMargins(8, 4, 10, 4); lay.setSpacing(8)
        # Badge hex — fond coloré + texte blanc lisible
        hex_l = _lbl(f"0x{op:02X}", 10, True, "#FFFFFF", True); hex_l.setFixedWidth(40)
        hex_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hex_l.setStyleSheet(
            f"color:#FFFFFF;background:{d['color']};"
            f"border:1px solid {d['color']};padding:3px 4px;border-radius:3px;font-weight:bold;")
        info = QVBoxLayout(); info.setSpacing(1)
        nm = _lbl(d["label"], 11, True, W_TEXT)
        ds = _lbl(d["desc"],   7, False, W_TEXT_DIM)
        info.addWidget(nm); info.addWidget(ds)
        led = StatusLed(10)
        lay.addWidget(hex_l); lay.addLayout(info, 1); lay.addWidget(led)
        f._op = op; f._hex = hex_l; f._nm = nm; f._led = led   # type: ignore[attr-defined]
        f.mousePressEvent = lambda e, x=op: self._select_op(x)  # type: ignore[method-assign]
        return f

    def set_car_widget(self, w) -> None:
        """Reçoit la référence au CarHTMLWidget central (appelé depuis main_window)."""
        self._car_html = w

    def _select_op(self, op: int) -> None:
        self._cur_op = op; self._wiper_setter(op); d = WOP[op]
        # Mettre à jour la voiture HTML au lieu du WindshieldWidget supprimé
        if self._car_html:
            self._car_html.set_wiper_op(op)
        for o, btn in self._op_btns.items():
            c = WOP[o]['color']
            if o == op:
                btn.setStyleSheet(
                    f"QFrame{{background:{W_PANEL};"
                    f"border:1.5px solid #CBD5E1;border-left:4px solid {d['color']};"
                    f"border-radius:2px;}}")
                btn._nm.setStyleSheet(f"color:{d['color']};font-weight:bold;background:transparent;")   # type: ignore[attr-defined]
                btn._led.set_state(True, d["color"])
                btn._hex.setStyleSheet(   # type: ignore[attr-defined]
                    f"color:#FFFFFF;background:{d['color']};"
                    f"border:1px solid {d['color']};padding:3px 4px;border-radius:3px;font-weight:bold;")
            else:
                btn.setStyleSheet(
                    f"QFrame{{background:{W_PANEL2};border:1.5px solid #CBD5E1;border-radius:4px;}}"
                    f"QFrame:hover{{background:{W_PANEL};border-color:{c};}}")
                btn._nm.setStyleSheet(f"color:{W_TEXT};font-weight:bold;background:transparent;")   # type: ignore[attr-defined]
                btn._led.set_state(False)
                btn._hex.setStyleSheet(   # type: ignore[attr-defined]
                    f"color:#FFFFFF;background:{c};"
                    f"border:1px solid {c};padding:3px 4px;border-radius:3px;font-weight:bold;")

        # ── Afficher/masquer le contenu Rear Wiper Status ───────────────────
        is_rear = op in (6, 7)
        self._rear_content.setVisible(is_rear)
        self._rear_inactive_lbl.setVisible(not is_rear)
        if is_rear:
            op_name = WOP[op]['name']
            self._rear_mode_lbl.setText(f"0x{op:02X}  {op_name}")
            self._rear_mode_lbl.setStyleSheet(
                f"color:{WOP[op]['color']};font-weight:bold;background:transparent;")
            # Initialiser les barres à zéro (mises à jour par update_rear_wiper_status)
            self._rear_blade_disp.set_value("0", "#29B6F6")
            self._rear_blade_bar.set_value(0.0)
            self._rear_cur_disp.set_value("0.000", A_TEAL)
            self._rear_cur_bar.set_value(0.0, False)
            self._rear_led_fault.set_state(False, A_GREEN)
            self._rear_lbl_fault.setText("NO FAULT")
            self._rear_lbl_fault.setStyleSheet(
                f"color:{A_GREEN};font-weight:bold;background:transparent;")

    def update_rear_wiper_status(self, blade_pct: float, current_A: float,
                                  fault: bool) -> None:
        # Met à jour les barres Rear Wiper Status (appelé depuis main_window)
        if not self._rear_content.isVisible():
            return
        cur_col = A_RED if fault else (A_ORANGE if current_A > 0.8 else A_TEAL)
        self._rear_blade_disp.set_value(f"{blade_pct:.0f}", "#29B6F6")
        self._rear_blade_bar.set_value(blade_pct)
        self._rear_cur_disp.set_value(f"{current_A:.3f}", cur_col)
        self._rear_cur_bar.set_value(current_A, fault)
        self._rear_led_fault.set_state(fault, A_RED if fault else A_GREEN)
        self._rear_lbl_fault.setText("FAULT" if fault else "NO FAULT")
        self._rear_lbl_fault.setStyleSheet(
            f"color:{A_RED if fault else A_GREEN};font-weight:bold;background:transparent;")

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
        lay = QVBoxLayout(w); lay.setContentsMargins(5, 4, 5, 4); lay.setSpacing(4)

        hdr_pan = InstrumentPanel("LIN Bus Monitor", LIN_TX_C)
        rh = QHBoxLayout(); rh.setSpacing(10)
        self._led_lin = StatusLed(11)
        self.lbl_lin  = _lbl("DISCONNECTED", 10, True, A_RED)
        rh.addWidget(self._led_lin); rh.addWidget(self.lbl_lin); rh.addStretch()
        for attr, lbl_txt, co in [("_cnt_tx", "TX", LIN_TX_C),
                                   ("_cnt_rx", "RX", LIN_RX_C),
                                   ("_cnt_tot", "TOTAL", A_TEAL2)]:
            v = _lbl("0", 5, True, co, True); setattr(self, attr, v)
            col = QVBoxLayout(); col.setSpacing(1)
            col.addWidget(_lbl(lbl_txt, 9, False, W_TEXT_DIM)); col.addWidget(v)
            rh.addLayout(col)
        hdr_pan.body().addLayout(rh); lay.addWidget(hdr_pan)

        osc_pan = InstrumentPanel("LIN Bus Signal — Rolling Window", LIN_TX_C)
        self._osc = LINOscilloscope()
        osc_pan.body().setContentsMargins(0, 4, 0, 4)

        # ── Barre de contrôle oscilloscope LIN ───────────────
        ctrl = QHBoxLayout(); ctrl.setSpacing(6)
        # Sélecteur de fenêtre temporelle
        ctrl.addWidget(_lbl("Window:", 9, False, W_TEXT_DIM))
        self._lin_win_combo = QComboBox()
        self._lin_win_combo.addItems(["5 s", "30 s", "60 s"])
        self._lin_win_combo.setFixedWidth(70); self._lin_win_combo.setFixedHeight(22)
        self._lin_win_combo.setStyleSheet(
            f"QComboBox{{background:{W_PANEL};color:{W_TEXT};"
            f"border:1px solid {W_BORDER};border-radius:2px;"
            f"padding:1px 4px;font-size:9pt;font-family:{FONT_MONO};}}"
            f"QComboBox::drop-down{{border:none;width:14px;}}"
            f"QComboBox QAbstractItemView{{background:{W_PANEL2};color:{W_TEXT};"
            f"border:1px solid {W_BORDER};selection-background-color:{W_PANEL3};}}")
        self._lin_win_combo.currentTextChanged.connect(
            lambda t: self._osc.set_window(float(t.split()[0])))
        ctrl.addWidget(self._lin_win_combo)
        ctrl.addSpacing(8)
        # Bouton Pause/Resume
        self._lin_pause_btn = _cd_btn("Pause", "#555555", h=22, w=80)
        self._lin_pause_btn.setCheckable(True)
        def _toggle_lin_pause(checked):
            self._osc.set_paused(checked)
            self._lin_pause_btn.setText("Resume" if checked else "Pause")
        self._lin_pause_btn.toggled.connect(_toggle_lin_pause)
        ctrl.addWidget(self._lin_pause_btn)
        ctrl.addStretch()
        # Stats live TX/RX rate
        self._lin_rate_lbl = _lbl("Rate: — fr/s", 9, False, W_TEXT_DIM, True)
        ctrl.addWidget(self._lin_rate_lbl)
        osc_pan.body().addLayout(ctrl)
        osc_pan.body().addWidget(self._osc)
        lay.addWidget(osc_pan, 1)
        self._ltx = 0; self._lrx = 0
        # Timer taux LIN
        self._lin_rate_timer = QTimer(); self._lin_rate_timer.timeout.connect(self._update_lin_rate)
        self._lin_rate_timer.start(2000)
        self._lin_rate_prev = 0; self._lin_rate_t0 = time.time()
        return w

    def _update_lin_rate(self) -> None:
        total = self._ltx + self._lrx
        dt = time.time() - self._lin_rate_t0
        if dt > 0:
            rate = (total - self._lin_rate_prev) / dt
            self._lin_rate_lbl.setText(f"Rate: {rate:.1f} fr/s")
        self._lin_rate_prev = total; self._lin_rate_t0 = time.time()

    # ── LIN Frame Table ───────────────────────────────────────
    def _build_table(self) -> QWidget:
        w = QWidget(); w.setStyleSheet(f"background:{W_BG};")
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)

        tb = QFrame(); tb.setFixedHeight(34)
        tb.setStyleSheet(
            f"QFrame{{background:{W_TOOLBAR};border-bottom:1px solid {W_BORDER};}}")
        tl = QHBoxLayout(tb); tl.setContentsMargins(10, 0, 10, 0); tl.setSpacing(8)
        tl.addWidget(_lbl("Filter:", 10, False, W_TEXT_DIM))

        _lin_combo_style = (
            f"QComboBox{{background:{W_PANEL};color:{W_TEXT};"
            f"border:1px solid {W_BORDER};border-radius:2px;"
            f"padding:1px 6px;font-size:10pt;font-family:{FONT_MONO};}}"
            f"QComboBox::drop-down{{border:none;width:16px;}}"
            f"QComboBox QAbstractItemView{{background:{W_PANEL2};color:{W_TEXT};"
            f"border:1px solid {W_BORDER};selection-background-color:{W_PANEL3};}}"
        )

        self._combo = QComboBox()
        self._combo.addItems(["All Frames", "TX", "RX_HDR"])
        self._combo.setFixedWidth(80); self._combo.setFixedHeight(24)
        self._combo.setStyleSheet(_lin_combo_style)
        self._combo.currentTextChanged.connect(self._filter_tbl)

        # Filtre par signal LDF
        tl.addWidget(_lbl("Signal:", 10, False, W_TEXT_DIM))
        self._lin_sig_combo = QComboBox()
        self._lin_sig_combo.addItem("All Signals")
        # Peupler avec signaux LDF connus du frame 0x16
        for sig in _ldf_signals_for("LeftStickWiperRequester"):
            self._lin_sig_combo.addItem(sig)
        self._lin_sig_combo.setFixedWidth(80); self._lin_sig_combo.setFixedHeight(24)
        self._lin_sig_combo.setStyleSheet(_lin_combo_style)
        self._lin_sig_combo.currentTextChanged.connect(self._filter_tbl)

        cb = QCheckBox("Auto-scroll"); cb.setChecked(True)
        cb.setStyleSheet(f"color:{W_TEXT};background:transparent;font-size:10pt;")
        cb.toggled.connect(lambda v: self._tbl.set_auto(v) if hasattr(self, "_tbl") else None)

        btn_clr = _cd_btn("Clear", "#888888", h=24, w=80)
        btn_clr.clicked.connect(lambda: self._tbl.clear_all())

        btn_exp = _cd_btn("CSV", "#1A6E4A", h=24, w=80)
        btn_exp.clicked.connect(self._export_lin_csv)

        tl.addWidget(self._combo); tl.addWidget(self._lin_sig_combo)
        tl.addWidget(cb); tl.addWidget(btn_clr)
        tl.addWidget(btn_exp); tl.addStretch()
        tl.addWidget(_lbl("", 10, False, W_TEXT_DIM, True))
        lay.addWidget(tb)

        self._tbl = LINTableWidget(); lay.addWidget(self._tbl, 1)

        bot = QFrame(); bot.setFixedHeight(22)
        bot.setStyleSheet(
            f"QFrame{{background:{W_TOOLBAR};border-top:1px solid {W_BORDER};}}")
        bl = QHBoxLayout(bot); bl.setContentsMargins(10, 0, 10, 0); bl.setSpacing(12)
        self.lbl_cnt = _lbl("0 frames", 10, False, W_TEXT_DIM, True)
        bl.addWidget(self.lbl_cnt)
        bl.addWidget(_lbl("|", 9, False, W_BORDER, True))
        self._lin_tbl_tx_lbl = _lbl("TX: 0", 9, False, LIN_TX_C, True)
        self._lin_tbl_rx_lbl = _lbl("RX: 0", 9, False, LIN_RX_C, True)
        bl.addWidget(self._lin_tbl_tx_lbl); bl.addWidget(self._lin_tbl_rx_lbl)
        bl.addStretch()
        bl.addWidget(_lbl("", 9, False, W_TEXT_DIM, True))
        lay.addWidget(bot)

        self._all_evts: deque = deque(maxlen=MAX_ROWS)
        self._tbl_tx_cnt = 0; self._tbl_rx_cnt = 0
        return w

    def _export_lin_csv(self) -> None:
        import csv, os
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.expanduser(f"~/lin_export_{ts}.csv")
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["#", "Time", "Direction", "PID",
                             "Byte0/Op", "Byte1/Alive", "Checksum", "Op Name", "Raw"])
                for ev in self._all_evts:
                    d = ev.get("type", "")
                    ts_s = datetime.datetime.fromtimestamp(
                        ev.get("time", time.time())).strftime("%H:%M:%S.%f")[:-3]
                    op = ev.get("op", 0)
                    row = ["", ts_s,
                           "TX slave->BCM" if d == "TX" else "RX_HDR BCM->slave",
                           "0xD6",
                           f"0x{op:02X} {WOP.get(op,{}).get('name','?')}" if d == "TX" else "—",
                           f"0x{ev.get('alive',0):02X}" if d == "TX" else "—",
                           f"0x{ev.get('cs_int',0):02X}" if d == "TX" else "—",
                           WOP.get(op, {}).get("name", "?") if d == "TX" else "LIN HEADER",
                           ev.get("raw", "")]
                    w.writerow(row)
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Export LIN CSV",
                                    f"✓ File exported:\n{path}")
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Export LIN CSV", f"Erreur export :\n{e}")

    # ── API publique ──────────────────────────────────────────
    def add_lin_event(self, ev: dict) -> None:
        t = ev.get("type", "")
        if t == "TX":
            op = ev.get("op", 0)
            self._osc.add_event("TX", WOP.get(op, {}).get("name", "?"))
            self._ltx += 1; self._tbl_tx_cnt += 1
        elif t == "RX_HDR":
            self._osc.add_event("RX", "HDR")
            self._lrx += 1; self._tbl_rx_cnt += 1
        self._cnt_tx.setText(str(self._ltx))
        self._cnt_rx.setText(str(self._lrx))
        self._cnt_tot.setText(str(self._ltx + self._lrx))
        self._lin_tbl_tx_lbl.setText(f"TX: {self._tbl_tx_cnt}")
        self._lin_tbl_rx_lbl.setText(f"RX: {self._tbl_rx_cnt}")
        self._all_evts.append(ev)
        if len(self._all_evts) > MAX_ROWS:
            pass  # deque(maxlen=MAX_ROWS) auto-trims
        flt     = self._combo.currentText()
        sig_flt = self._lin_sig_combo.currentText() if hasattr(self, "_lin_sig_combo") else "All Signals"
        if (flt in ("All Frames", "") or flt == t) and self._lin_frame_matches_sig(ev, sig_flt):
            self._tbl.add_event(ev)
        self.lbl_cnt.setText(f"{len(self._all_evts)} frames")

    @staticmethod
    def _lin_frame_matches_sig(ev: dict, sig_flt: str) -> bool:
        if sig_flt in ("All Signals", ""):
            return True
        # Chercher le signal dans op_name ou dans les champs connus
        op = ev.get("op", 0)
        op_name = WOP.get(op, {}).get("name", "") if isinstance(op, int) else str(op)
        if sig_flt.lower() in op_name.lower():
            return True
        # Chercher dans le raw LDF décodé si disponible
        phys = _decode_lin_physical(ev)
        return sig_flt in phys

    def _filter_tbl(self) -> None:
        flt     = self._combo.currentText()
        sig_flt = self._lin_sig_combo.currentText() if hasattr(self, "_lin_sig_combo") else "All Signals"
        prev_auto = self._tbl._auto
        self._tbl._auto = False
        self._tbl.setUpdatesEnabled(False)
        self._tbl.clear_all()
        for ev in self._all_evts:
            if (flt in ("All Frames", "") or flt == ev.get("type", "")) and \
               self._lin_frame_matches_sig(ev, sig_flt):
                self._tbl.add_event(ev)
        self._tbl._auto = prev_auto
        self._tbl.setUpdatesEnabled(True)
        if prev_auto:
            self._tbl.scrollToBottom()

    def set_lin_status(self, msg: str, ok: bool) -> None:
        self._led_lin.set_state(ok)
        self.lbl_lin.setText(msg.upper())
        self.lbl_lin.setStyleSheet(
            f"color:{A_GREEN if ok else A_RED};background:transparent;")


# ═══════════════════════════════════════════════════════════════
#  CAN OSCILLOSCOPE  (4 canaux superposés)
# ═══════════════════════════════════════════════════════════════
class CANOscilloscope(QWidget):
    """Oscilloscope multi-canaux pour les 4 trames CAN (30s)."""
    WINDOW = 30.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._evts: deque = deque()    # (time, can_id_int, color, amp)
        self._paused = False
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._t = QTimer(); self._t.timeout.connect(self.update); self._t.start(100)  # Optimisation: 100ms (au lieu de 40ms)

    def set_window(self, seconds: float) -> None:
        self.WINDOW = seconds

    def set_paused(self, paused: bool) -> None:
        self._paused = paused

    def add_event(self, can_id_int: int) -> None:
        if self._paused:
            return
        t = time.time()
        # trouver le canal correspondant
        for cid, _, color, amp, _ in _CAN_CHANNELS:
            if cid == can_id_int:
                self._evts.append((t, can_id_int, color, amp))
                break
        cut = t - self.WINDOW * 2
        while self._evts and self._evts[0][0] < cut:
            self._evts.popleft()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        now = time.time()
        # Fond avec reflet vert KPIT
        bg = QLinearGradient(0, 0, W, H)
        bg.setColorAt(0, QColor("#FFFFFF")); bg.setColorAt(0.5, QColor("#F8FAFC")); bg.setColorAt(1, QColor("#F1F5F9"))
        p.fillRect(0, 0, W, H, QBrush(bg))
        # no color overlay — fond blanc pur

        ML, MR, MT, MB = 6, 10, 6, 20
        cw = W - ML - MR; ch = H - MT - MB
        if cw < 10 or ch < 10: return

        # Grille verticale (temps)
        for i in range(7):
            x   = ML + int(cw * i / 6)
            t_v = self.WINDOW * (1 - i / 6)
            p.setPen(QPen(QColor(CAN_GRID), 1, Qt.PenStyle.DotLine))
            p.drawLine(x, MT, x, MT + ch)
            p.setPen(QPen(QColor(W_TEXT_DIM))); p.setFont(QFont(FONT_MONO, 9))
            p.drawText(x - 14, MT + ch + 2, 28, 14,
                       Qt.AlignmentFlag.AlignCenter, f"-{t_v:.0f}s")

        # Séparateurs horizontaux des 4 canaux
        for _, label, color, amp, direction in _CAN_CHANNELS:
            cy = MT + int(ch * amp)
            p.setPen(QPen(QColor(color), 1, Qt.PenStyle.DotLine))
            p.drawLine(ML, cy, ML + cw, cy)
            # étiquette gauche
            lc = QColor(color); lc.setAlpha(200)
            p.setPen(QPen(lc)); p.setFont(QFont(FONT_MONO, 8))
            tag = f"{'←' if direction == 'RX' else '→'} {label}"
            p.drawText(ML + 2, cy - 11, min(cw // 2, 160), 10,
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, tag)

        # Cadre
        p.setPen(QPen(QColor(W_BORDER), 1))
        p.drawLine(ML, MT, ML, MT + ch); p.drawLine(ML, MT + ch, ML + cw, MT + ch)

        base_y = MT + ch

        def tx_pos(ta): return ML + cw - int(cw * (now - ta) / self.WINDOW)

        vis = [ev for ev in self._evts if (now - ev[0]) <= self.WINDOW]

        for te, ci, co, amp in vis:
            xp = tx_pos(te)
            if xp < ML or xp > ML + cw: continue
            base = MT + int(ch * amp)
            spike_h = max(8, int(ch * 0.09))
            hw = 5
            pts_y = [(xp + dx, base - int(spike_h * max(0, 1.0 - (abs(dx) / hw)**1.5)))
                     for dx in range(-hw, hw + 1)]
            path = QPainterPath(); path.moveTo(xp - hw, base)
            for px, py in pts_y: path.lineTo(px, py)
            path.lineTo(xp + hw, base); path.closeSubpath()
            fc = QColor(co); fc.setAlpha(55)
            p.fillPath(path, QBrush(fc))
            pp2 = QPainterPath(); pp2.moveTo(pts_y[0][0], pts_y[0][1])
            for px, py in pts_y[1:]: pp2.lineTo(px, py)
            p.setPen(QPen(QColor(co), 1.5, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush); p.drawPath(pp2)

        # Légende LIVE
        p.setFont(QFont(FONT_MONO, 9, QFont.Weight.Bold))
        p.setPen(QPen(QColor(A_GREEN)))
        p.drawText(W - MR - 34, MT, 34, 12, Qt.AlignmentFlag.AlignCenter,
                   "⏸ PAUSE" if self._paused else "* LIVE")


# ═══════════════════════════════════════════════════════════════
#  CAN FRAME TABLE
# ═══════════════════════════════════════════════════════════════
class CANTableWidget(QTableWidget):
    COLS = ["#", "Time", "Dir", "CAN ID", "DLC", "Data (hex)", "Description", "Decoded"]

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
                font-family:{FONT_MONO};font-size:9pt;
                selection-background-color:{A_GREEN_BG};selection-color:{W_TEXT};
            }}
            QHeaderView::section {{
                background:{W_TITLEBAR};color:{W_TEXT_HDR};border:none;
                border-bottom:1px solid {W_BORDER2};border-right:1px solid {W_BORDER};
                padding:2px 6px;font-family:{FONT_UI};font-size:9pt;font-weight:bold;
            }}
            QTableWidget::item {{ padding:1px 4px;border-bottom:1px solid {W_PANEL3}; }}
        """)
        self._rn   = 0
        self._evts: deque = deque()
        self._auto = True
        self.cellDoubleClicked.connect(self._dbl)

    def _dbl(self, row: int, _) -> None:
        if row < len(self._evts):
            self._show_detail(self._evts[row])

    @staticmethod
    def _decode_frame(ev: dict) -> str:
        """
        Retourne une chaîne décodée selon le CAN ID.
        Priorité : décodage DBC physique si disponible, sinon décodage manuel.
        """
        # Tentative décodage DBC (valeurs physiques + facteurs + VAL_)
        phys = _decode_can_physical(ev)
        if phys:
            return phys

        # Fallback décodage manuel (fields pré-décodés par bcmcan)
        cid = ev.get("can_id_int", 0)
        f   = ev.get("fields", {})
        if cid == 0x200:
            mode  = f.get("mode", "?")
            op    = WOP.get(mode, {}).get("name", f"0x{mode:02X}") if isinstance(mode, int) else mode
            speed = f.get("speed", "?")
            wash  = f.get("wash", "?")
            alive = f.get("alive", "?")
            crc_s = "✓" if f.get("crc_ok", True) else "✗ CRC ERR"
            return f"Mode={op}  Spd={speed}  Wash={wash}  Alive=0x{alive:02X}  {crc_s}" \
                   if isinstance(alive, int) else \
                   f"Mode={op}  Spd={speed}  Wash={wash}  Alive={alive}  {crc_s}"
        elif cid == 0x201:
            mode   = f.get("mode", "?")
            op     = WOP.get(mode, {}).get("name", f"0x{mode:02X}") if isinstance(mode, int) else mode
            blade  = f.get("blade_pct", "?")
            cur    = f.get("current_A", "?")
            fault  = "FAULT" if f.get("fault") else "OK"
            alive  = f.get("alive", "?")
            crc    = f.get("crc", "?")
            crc_s  = f"0x{crc:02X}" if isinstance(crc, int) else crc
            return f"Mode={op}  Blade={blade}%  I={cur}A  {fault}  Alive={alive}  CRC={crc_s}"
        elif cid == 0x202:
            ack   = f.get("ack_status", "?")
            err   = f.get("error_code", "?")
            alive = f.get("alive", "?")
            crc   = f.get("crc", "?")
            ack_s = "NACK (fault)" if ack else "ACK (ok)"
            err_s = f"ErrCode=0x{err:02X}" if isinstance(err, int) else f"ErrCode={err}"
            alv_s = f"Alive=0x{alive:02X}" if isinstance(alive, int) else f"Alive={alive}"
            crc_s = f"CRC=0x{crc:02X}" if isinstance(crc, int) else f"CRC={crc}"
            return f"{ack_s}  {err_s}  {alv_s}  {crc_s}"
        elif cid == 0x300:
            ign_map = {0: "OFF", 1: "ACC", 2: "ON"}
            ign  = ign_map.get(f.get("ignition", 0), "?")
            rev  = "REV" if f.get("reverse") else "FWD"
            spd  = f.get("speed_kmh", "?")
            return f"IGN={ign}  {rev}  SPD={spd} km/h"
        elif cid == 0x301:
            intensity = f.get("intensity", "?")
            sensor    = "OK" if f.get("sensor_ok", True) else "ERROR"
            return f"Rain={intensity}%  Sensor={sensor}"
        return ""

    def _show_detail(self, ev: dict) -> None:
        from PySide6.QtWidgets import QDialog, QVBoxLayout
        dlg = QDialog(self); dlg.setWindowTitle("CAN Frame Details")
        dlg.setMinimumSize(540, 340)
        dlg.setStyleSheet(f"background:{W_PANEL};color:{W_TEXT};")
        lay = QVBoxLayout(dlg); lay.setContentsMargins(14, 12, 14, 12); lay.setSpacing(8)
        cid   = ev.get("can_id_int", 0)
        color = _CAN_FRAME_COLORS.get(cid, W_TEXT)
        d     = ev.get("type", "?")
        hdr   = QFrame(); hdr.setStyleSheet(f"background:{W_PANEL2};border-left:3px solid {color};")
        hl    = QHBoxLayout(hdr); hl.setContentsMargins(10, 6, 10, 6)
        hl.addWidget(_lbl(f"{ev.get('can_id','?')}  {ev.get('desc','')}", 13, True, color, True))
        hl.addStretch()
        ts_str = datetime.datetime.fromtimestamp(ev.get("time", time.time())).strftime("%H:%M:%S.%f")[:-3]
        hl.addWidget(_lbl(ts_str, 10, False, W_TEXT_DIM, True))
        lay.addWidget(hdr)
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
        add("Direction", f"{'← RX' if d == 'RX' else '→ TX'}", color)
        add("Timestamp", ts_str)
        add("CAN ID", ev.get("can_id", "?"), color)
        add("DLC", ev.get("dlc", 8))
        add("Data", ev.get("data", ""), W_TEXT_DIM)
        add("Description", ev.get("desc", ""), A_TEAL)
        for k, v in ev.get("fields", {}).items():
            add(k, v)
        lay.addWidget(tbl, 1)
        b = _cd_btn("Close", "#707070", h=28); b.clicked.connect(dlg.close); lay.addWidget(b)
        dlg.exec()

    def add_event(self, ev: dict) -> None:
        if self._rn >= MAX_ROWS:
            self.removeRow(0); self._evts.popleft(); self._rn -= 1
        self._evts.append(ev); r = self._rn; self.insertRow(r); self._rn += 1
        ts_str = datetime.datetime.fromtimestamp(
            ev.get("time", time.time())).strftime("%H:%M:%S.%f")[:-3]
        cid   = ev.get("can_id_int", 0)
        color = QColor(_CAN_FRAME_COLORS.get(cid, W_TEXT))
        d     = ev.get("type", "")
        decoded = CANTableWidget._decode_frame(ev)
        cells = [
            str(r + 1),
            ts_str,
            "← RX" if d == "RX" else "→ TX",
            ev.get("can_id", "?"),
            str(ev.get("dlc", 8)),
            ev.get("data", ""),
            ev.get("desc", ""),
            decoded,
        ]
        for ci, val in enumerate(cells):
            it = QTableWidgetItem(val)
            it.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            if ci in (2, 3):
                it.setForeground(color)
            else:
                it.setForeground(QColor(W_TEXT))
            self.setItem(r, ci, it)
        self.setRowHeight(r, 18)
        if self._auto: self.scrollToBottom()

    def clear_all(self) -> None:
        self.setRowCount(0); self._rn = 0; self._evts.clear()

    def set_auto(self, v: bool) -> None:
        self._auto = v


# ═══════════════════════════════════════════════════════════════
#  CAN BUS PANEL  (WC / CAN Signal / CAN Frame Table)
# ═══════════════════════════════════════════════════════════════
class CANBusPanel(QWidget):
    """
    Panneau CAN Bus — 3 onglets :
      • WC  : Wiper Control — décodage 0x200 (RX), 0x202 TX (Wiper_Ack), 0x201 (TX)
      • CAN Bus Signal — oscilloscope 5 canaux, fenêtre 30s
      • CAN Frame Table — historique de toutes les trames
    """

    # Signal émis quand 0x200 reçu → main_window connecte à can_worker.send_0x202
    ack_needed = Signal(int, int, int)   # ack_status, error_code, alive

    def __init__(self, sim_getter=None, parent=None) -> None:
        super().__init__(parent)
        self._sim_getter = sim_getter   # lambda → SimClient ou None
        self._wc_fault = False   # False=ACK mode, True=NACK mode (fault injecté)
        self._car_html  = None   # CarHTMLWidget injecté depuis main_window
        self._motor_driver_fault = False   # état courant du bouton B2102
        self.setStyleSheet(f"background:{W_BG};")
        vl = QVBoxLayout(self); vl.setContentsMargins(0, 0, 0, 0); vl.setSpacing(0)

        from PySide6.QtWidgets import QTabWidget
        tabs = QTabWidget()
        tabs.setStyleSheet(f"""
            QTabWidget::pane{{border:none;background:{W_BG};}}
            QTabBar{{background:{W_TOOLBAR};border-bottom:2px solid #8DC63F;}}
            QTabBar::tab{{background:{W_TOOLBAR};color:{W_TEXT_DIM};border:none;
                border-right:1px solid {W_SEP};padding:4px 12px;
                font-family:{FONT_UI};font-size:9pt;min-width:75px;}}
            QTabBar::tab:selected{{background:{W_BG};color:#8DC63F;
                border-top:2px solid #8DC63F;}}
            QTabBar::tab:hover:!selected{{background:{W_PANEL2};color:{W_TEXT};}}
        """)
        self._wc_tab    = self._build_wc()
        self._sig_tab   = self._build_signal()
        self._table_tab = self._build_table()
        tabs.addTab(self._wc_tab,    "  WC — Wiper Control  ")
        tabs.addTab(self._sig_tab,   "  CAN — Bus Signal  ")
        tabs.addTab(self._table_tab, "  CAN — Frame Table  ")
        vl.addWidget(tabs)

        # Compteurs internes
        self._cnt: dict[int, int] = {0x200: 0, 0x201: 0, 0x202: 0, 0x300: 0, 0x301: 0}
        self._last_200: dict = {}
        self._last_201: dict = {}
        self._led_can_status: "StatusLed | None" = None

    # ── Onglet 1 — WC ────────────────────────────────────────
    def _build_wc(self) -> QWidget:
        w = QWidget(); w.setStyleSheet(f"background:{W_BG};")
        # Layout horizontal : col_gauche (0x200 large) | col_droite (0x201+0x202 empilés)
        lay = QHBoxLayout(w); lay.setContentsMargins(5, 4, 5, 4); lay.setSpacing(6)

        # ══════════════════════════════════════════════════════
        # COL GAUCHE — CAN 0x200 (élargie, prend ~65% de la largeur)
        # ══════════════════════════════════════════════════════
        pan_cmd = InstrumentPanel("CAN 0x200 — Wiper_Cmd", CAN_CMD_C)
        self._cmd_widgets: dict[str, QLabel] = {}
        for key, label, init in [
            ("mode",   "Mode",  WOP.get(0, {}).get("name", "OFF")),
            ("speed",  "Speed", "—"),
            ("wash",   "Wash",  "—"),
            ("alive",  "Alive", "—"),
            ("crc_ok", "CRC",   "—"),
        ]:
            row = QHBoxLayout(); row.setSpacing(6)
            row.addWidget(_lbl(f"{label} :", 10, True, W_TEXT_DIM))
            v = _lbl(init, 12, True, CAN_CMD_C, True)
            self._cmd_widgets[key] = v; row.addWidget(v); row.addStretch()
            pan_cmd.body().addLayout(row)
        pan_cmd.body().addWidget(_hsep())
        pan_cmd.body().addWidget(_lbl("WiperOp", 10, True, W_TEXT_DIM))
        # ── Comodo 3D read-only (affichage CAN, pas d'interaction) ────────
        self._wc_op_btns: dict[int, QFrame] = {}   # conservé vide pour compat backend
        self._comodo_widget = CarComodo3DReadOnly()
        self._comodo_widget.setFixedSize(270, 270)
        pan_cmd.body().addWidget(self._comodo_widget)
        pan_cmd.body().addStretch()
        # 0x200 prend stretch=7 (large)
        lay.addWidget(pan_cmd, 7)

        # ══════════════════════════════════════════════════════
        # COL DROITE — 0x201 seulement (0x202 supprimé)
        # ══════════════════════════════════════════════════════
        right_col = QWidget(); right_col.setStyleSheet("background:transparent;")
        right_vl  = QVBoxLayout(right_col)
        right_vl.setContentsMargins(0, 0, 0, 0); right_vl.setSpacing(6)

        self._wc_ws = None

        # ── Stubs 0x202 pour compatibilité backend ─────────────
        self._ack_mode_lbl = _lbl("", 10, True, A_GREEN); self._ack_mode_lbl.hide()
        self._btn_ack  = QPushButton(); self._btn_ack.hide()
        self._btn_nack = QPushButton(); self._btn_nack.hide()
        self._ack_fields: dict[str, QLabel] = {
            k: _lbl("—", 10, True, W_TEXT_DIM, True)
            for k in ("ack", "err", "alive", "crc")}
        self._ack_cnt_lbl = _lbl("0", 10, True, CAN_ACK_C, True)
        self._ack_tx_count = 0

        # ── 0x201 Wiper_Status TX ────────────────────────────
        pan_sta = InstrumentPanel("CAN 0x201 — Wiper_Status", CAN_STA_C)
        self._sta_widgets: dict[str, QLabel] = {}
        self._sta_bars:   dict[str, "LinearBar"] = {}

        pan_sta.body().addWidget(_lbl("Mode reply", 9, True, W_TEXT_DIM))
        self._sta_mode_lbl = _lbl("—", 11, True, CAN_STA_C)
        pan_sta.body().addWidget(self._sta_mode_lbl)
        pan_sta.body().addWidget(_hsep())

        pan_sta.body().addWidget(_lbl("Blade position", 9, True, W_TEXT_DIM))
        self._sta_blade_disp = NumericDisplay("BLADE", "%")
        self._sta_blade_bar  = LinearBar(100.0, "%")
        pan_sta.body().addWidget(self._sta_blade_disp)
        pan_sta.body().addWidget(self._sta_blade_bar)
        pan_sta.body().addWidget(_hsep())

        pan_sta.body().addWidget(_lbl("Motor current", 9, True, W_TEXT_DIM))
        self._sta_cur_disp = NumericDisplay("CURRENT", "A")
        self._sta_cur_bar  = LinearBar(1.5, "A")
        pan_sta.body().addWidget(self._sta_cur_disp)
        pan_sta.body().addWidget(self._sta_cur_bar)
        pan_sta.body().addWidget(_hsep())

        sta_row = QHBoxLayout(); sta_row.setSpacing(8)
        self._led_fault = StatusLed(11); self._led_fault.set_state(False, A_GREEN)
        self.lbl_fault  = _lbl("NO FAULT", 10, True, A_GREEN)
        sta_row.addWidget(self._led_fault); sta_row.addWidget(self.lbl_fault); sta_row.addStretch()
        pan_sta.body().addLayout(sta_row)

        # ── Bouton injection défaut driver moteur (B2102) ─────────────
        pan_sta.body().addWidget(_hsep())
        pan_sta.body().addWidget(_lbl("Motor Driver Fault", 9, True, W_TEXT_DIM))
        self._btn_motor_fault = QPushButton("INJECT B2102")
        self._btn_motor_fault.setCheckable(True)
        self._btn_motor_fault.setChecked(False)
        self._btn_motor_fault.setStyleSheet(
            f"QPushButton{{background:#444;color:{W_TEXT_DIM};"
            f"border:1px solid #666;border-radius:3px;"
            f"padding:3px 8px;font-size:9pt;font-weight:bold;}}"
            f"QPushButton:checked{{background:{A_RED};color:#FFF;"
            f"border:2px solid #8B0000;border-radius:3px;"
            f"padding:3px 8px;font-size:9pt;font-weight:bold;}}"
        )
        self._btn_motor_fault.toggled.connect(self._on_motor_driver_fault_toggled)
        pan_sta.body().addWidget(self._btn_motor_fault)

        stats_row = QHBoxLayout(); stats_row.setSpacing(12)
        for k, t2, co in [("sta_alive", "Alive", CAN_STA_C),
                           ("sta_crc",   "CRC",   W_TEXT_DIM)]:
            col = QVBoxLayout(); col.setSpacing(1)
            col.addWidget(_lbl(t2, 8, False, W_TEXT_DIM))
            v = _lbl("—", 10, True, co, True)
            self._sta_widgets[k] = v; col.addWidget(v); stats_row.addLayout(col)
        stats_row.addStretch(); pan_sta.body().addLayout(stats_row)
        pan_sta.body().addStretch()
        right_vl.addWidget(pan_sta, 1)

        # La colonne droite prend stretch=3 (étroite)
        lay.addWidget(right_col, 3)
        return w

    def _on_motor_driver_fault_toggled(self, checked: bool) -> None:
        """Injecte ou retire le défaut driver moteur B2102 côté simulateur."""
        self._motor_driver_fault = checked
        sim = self._sim_getter() if self._sim_getter else None
        if sim and sim.is_connected():
            sim.set_motor_driver_fault(checked)
        self._btn_motor_fault.setText(
            "FAULT ACTIVE  (B2102)" if checked else "INJECT B2102")

    def _set_ack_mode(self, fault: bool) -> None:
        """Bascule entre mode ACK (no fault) et NACK (fault)."""
        self._wc_fault = fault
        if fault:
            self._ack_mode_lbl.setText("NACK  (fault active)")
            self._ack_mode_lbl.setStyleSheet(
                f"color:{A_RED};font-weight:bold;background:transparent;")
            self._btn_nack.setStyleSheet(
                f"QPushButton{{background:{A_RED};color:#FFF;"
                f"border:2px solid #8B0000;border-radius:3px;padding:2px 10px;font-weight:bold;}}")
            self._btn_ack.setStyleSheet("")   # reset to default
        else:
            self._ack_mode_lbl.setText("ACK  (no fault)")
            self._ack_mode_lbl.setStyleSheet(
                f"color:{A_GREEN};font-weight:bold;background:transparent;")
            self._btn_ack.setStyleSheet(
                f"QPushButton{{background:{A_GREEN};color:#FFF;"
                f"border:2px solid #1A5C1A;border-radius:3px;padding:2px 10px;font-weight:bold;}}")
            self._btn_nack.setStyleSheet("")

    # ── Onglet 2 — CAN Bus Signal ────────────────────────────
    def _build_signal(self) -> QWidget:
        w = QWidget(); w.setStyleSheet(f"background:{W_BG};")
        lay = QVBoxLayout(w); lay.setContentsMargins(5, 4, 5, 4); lay.setSpacing(4)

        hdr_pan = InstrumentPanel("CAN Bus Monitor", CAN_VEH_C)
        rh = QHBoxLayout(); rh.setSpacing(10)
        self._led_can_status = StatusLed(11)
        self.lbl_can_status  = _lbl("DISCONNECTED", 10, True, A_RED)
        rh.addWidget(self._led_can_status); rh.addWidget(self.lbl_can_status); rh.addStretch()

        for cid, label, color, _, direction in _CAN_CHANNELS:
            col = QVBoxLayout(); col.setSpacing(1)
            col.addWidget(_lbl(f"{label}", 9, False, W_TEXT_DIM))
            v = _lbl("0", 13, True, color, True)
            setattr(self, f"_cnt_lbl_{cid:03X}", v); col.addWidget(v)
            rh.addLayout(col)

        hdr_pan.body().addLayout(rh); lay.addWidget(hdr_pan)

        osc_pan = InstrumentPanel("CAN Bus Signal — Rolling Window", CAN_VEH_C)
        self._osc = CANOscilloscope()
        osc_pan.body().setContentsMargins(0, 4, 0, 4)

        # ── Barre de contrôle oscilloscope CAN ───────────────
        can_ctrl = QHBoxLayout(); can_ctrl.setSpacing(6)
        can_ctrl.addWidget(_lbl("Window:", 9, False, W_TEXT_DIM))
        self._can_win_combo = QComboBox()
        self._can_win_combo.addItems(["5 s", "30 s", "60 s"])
        self._can_win_combo.setCurrentIndex(1)   # défaut 30s
        self._can_win_combo.setFixedWidth(70); self._can_win_combo.setFixedHeight(22)
        self._can_win_combo.setStyleSheet(
            f"QComboBox{{background:{W_PANEL};color:{W_TEXT};"
            f"border:1px solid {W_BORDER};border-radius:2px;"
            f"padding:1px 4px;font-size:9pt;font-family:{FONT_MONO};}}"
            f"QComboBox::drop-down{{border:none;width:14px;}}"
            f"QComboBox QAbstractItemView{{background:{W_PANEL2};color:{W_TEXT};"
            f"border:1px solid {W_BORDER};selection-background-color:{W_PANEL3};}}")
        self._can_win_combo.currentTextChanged.connect(
            lambda t: self._osc.set_window(float(t.split()[0])))
        can_ctrl.addWidget(self._can_win_combo)
        can_ctrl.addSpacing(8)
        self._can_pause_btn = _cd_btn("Pause", "#555555", h=22, w=80)
        self._can_pause_btn.setCheckable(True)
        def _toggle_can_pause(checked):
            self._osc.set_paused(checked)
            self._can_pause_btn.setText("Resume" if checked else "Pause")
        self._can_pause_btn.toggled.connect(_toggle_can_pause)
        can_ctrl.addWidget(self._can_pause_btn)
        can_ctrl.addStretch()
        self._can_rate_lbl = _lbl("Rate: — fr/s", 9, False, W_TEXT_DIM, True)
        can_ctrl.addWidget(self._can_rate_lbl)
        osc_pan.body().addLayout(can_ctrl)
        osc_pan.body().addWidget(self._osc)
        lay.addWidget(osc_pan, 1)
        # Timer taux CAN
        self._can_rate_timer = QTimer(); self._can_rate_timer.timeout.connect(self._update_can_rate)
        self._can_rate_timer.start(2000)
        self._can_rate_prev_total = 0; self._can_rate_t0 = time.time()
        return w

    def _update_can_rate(self) -> None:
        total = sum(self._cnt.values())
        dt = time.time() - self._can_rate_t0
        if dt > 0:
            rate = (total - self._can_rate_prev_total) / dt
            self._can_rate_lbl.setText(f"Rate: {rate:.1f} fr/s")
        self._can_rate_prev_total = total; self._can_rate_t0 = time.time()

    # ── Onglet 3 — CAN Frame Table ───────────────────────────
    def _build_table(self) -> QWidget:
        w = QWidget(); w.setStyleSheet(f"background:{W_BG};")
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)

        tb = QFrame(); tb.setFixedHeight(34)
        tb.setStyleSheet(f"QFrame{{background:{W_TOOLBAR};border-bottom:1px solid {W_BORDER};}}")
        tl = QHBoxLayout(tb); tl.setContentsMargins(10, 0, 10, 0); tl.setSpacing(8)
        tl.addWidget(_lbl("Filter:", 10, False, W_TEXT_DIM))

        _combo_style = (
            f"QComboBox{{background:{W_PANEL};color:{W_TEXT};"
            f"border:1px solid {W_BORDER};border-radius:2px;"
            f"padding:1px 6px;font-size:10pt;font-family:{FONT_MONO};}}"
            f"QComboBox::drop-down{{border:none;width:16px;}}"
            f"QComboBox QAbstractItemView{{background:{W_PANEL2};color:{W_TEXT};"
            f"border:1px solid {W_BORDER};selection-background-color:{W_PANEL3};}}"
        )

        # Filtre par message (ID)
        self._can_combo = QComboBox()
        self._can_combo.addItems(["All Frames", "RX 0x200", "TX 0x201", "TX 0x202", "TX 0x300", "TX 0x301"])
        self._can_combo.setFixedWidth(80); self._can_combo.setFixedHeight(24)
        self._can_combo.setStyleSheet(_combo_style)
        self._can_combo.currentTextChanged.connect(self._filter_can_tbl)

        # Filtre par signal DBC — peuplé dynamiquement selon le message sélectionné
        tl.addWidget(_lbl("Signal:", 10, False, W_TEXT_DIM))
        self._can_sig_combo = QComboBox()
        self._can_sig_combo.addItem("All Signals")
        self._can_sig_combo.setFixedWidth(80); self._can_sig_combo.setFixedHeight(24)
        self._can_sig_combo.setStyleSheet(_combo_style)
        self._can_sig_combo.currentTextChanged.connect(self._filter_can_tbl)

        # Quand le filtre message change → mettre à jour les signaux disponibles
        def _on_msg_filter(txt):
            self._can_sig_combo.blockSignals(True)
            self._can_sig_combo.clear()
            self._can_sig_combo.addItem("All Signals")
            # Extraire le CAN ID du texte du combo (ex: "RX 0x200" → 0x200)
            import re
            m = re.search(r'0x([0-9A-Fa-f]+)', txt)
            if m and _DBC_CFG:
                cid = int(m.group(1), 16)
                for sig in _dbc_signals_for(cid):
                    self._can_sig_combo.addItem(sig)
            self._can_sig_combo.blockSignals(False)
            self._filter_can_tbl()
        self._can_combo.currentTextChanged.connect(_on_msg_filter)

        cb = QCheckBox("Auto-scroll"); cb.setChecked(True)
        cb.setStyleSheet(f"color:{W_TEXT};background:transparent;font-size:10pt;")
        cb.toggled.connect(lambda v: self._can_tbl.set_auto(v) if hasattr(self, "_can_tbl") else None)

        btn_clr = _cd_btn("Clear", "#888888", h=24, w=80)
        btn_clr.clicked.connect(lambda: self._can_tbl.clear_all())

        btn_exp_can = _cd_btn("CSV", "#1A4E8E", h=24, w=80)
        btn_exp_can.clicked.connect(self._export_can_csv)

        tl.addWidget(self._can_combo); tl.addWidget(self._can_sig_combo)
        tl.addWidget(cb); tl.addWidget(btn_clr)
        tl.addWidget(btn_exp_can); tl.addStretch()
        tl.addWidget(_lbl("", 10, False, W_TEXT_DIM, True))
        lay.addWidget(tb)

        self._can_tbl = CANTableWidget(); lay.addWidget(self._can_tbl, 1)

        bot = QFrame(); bot.setFixedHeight(22)
        bot.setStyleSheet(f"QFrame{{background:{W_TOOLBAR};border-top:1px solid {W_BORDER};}}")
        bl = QHBoxLayout(bot); bl.setContentsMargins(10, 0, 10, 0); bl.setSpacing(6)
        self.lbl_can_cnt = _lbl("0 frames", 10, False, W_TEXT_DIM, True)
        bl.addWidget(self.lbl_can_cnt)
        bl.addWidget(_lbl("|", 9, False, W_BORDER, True))
        # Compteurs par canal dans le footer
        self._can_tbl_cnt_lbls = {}
        for cid, label, color, _, _ in _CAN_CHANNELS:
            short = f"0x{cid:03X}"
            dot = QLabel("●"); dot.setFont(QFont(FONT_MONO, 9))
            dot.setStyleSheet(f"color:{color};background:transparent;")
            bl.addWidget(dot)
            lbl_c = _lbl(f"{short}:0", 9, False, W_TEXT_DIM, True)
            self._can_tbl_cnt_lbls[cid] = lbl_c
            bl.addWidget(lbl_c)
            bl.addSpacing(4)
        bl.addStretch()
        lay.addWidget(bot)

        self._all_can_evts: deque = deque(maxlen=MAX_ROWS)
        self._can_tbl_per_id = {cid: 0 for cid, *_ in _CAN_CHANNELS}
        return w

    def _export_can_csv(self) -> None:
        import csv, os
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.expanduser(f"~/can_export_{ts}.csv")
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["#", "Time", "Dir", "CAN ID", "DLC",
                             "Data (hex)", "Description", "Decoded"])
                for i, ev in enumerate(self._all_can_evts):
                    ts_s = datetime.datetime.fromtimestamp(
                        ev.get("time", time.time())).strftime("%H:%M:%S.%f")[:-3]
                    d = ev.get("type", "")
                    w.writerow([
                        i + 1, ts_s,
                        "← RX" if d == "RX" else "→ TX",
                        ev.get("can_id", "?"),
                        ev.get("dlc", 8),
                        ev.get("data", ""),
                        ev.get("desc", ""),
                        CANTableWidget._decode_frame(ev),
                    ])
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Export CAN CSV",
                                    f"✓ File exported:\n{path}")
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Export CAN CSV", f"Erreur export :\n{e}")

    # ── API publique ──────────────────────────────────────────
    def add_can_event(self, ev: dict) -> None:
        """Slot connecté à CANWorker.can_received."""
        t      = ev.get("type", "")
        cid    = ev.get("can_id_int", 0)

        # Oscilloscope
        self._osc.add_event(cid)

        # Compteurs
        if cid in self._cnt:
            self._cnt[cid] += 1
            lbl_attr = f"_cnt_lbl_{cid:03X}"
            if hasattr(self, lbl_attr):
                getattr(self, lbl_attr).setText(str(self._cnt[cid]))

        # Mise à jour WC tab
        # NOTE architectural : 0x202 n'est plus construit ici.
        # Il est émis directement par bcmcan.py (_build_0x202_from_state)
        # depuis l'état interne du WC simulateur, sans dépendance à _last_201.
        if cid == 0x200:
            self._last_200 = ev
            self._update_wc_cmd(ev)
        elif cid == 0x201:
            self._last_201 = ev
            self._update_wc_sta(ev)
        elif cid == 0x202:
            self._update_wc_ack(ev)

        # Table
        self._all_can_evts.append(ev)
        if len(self._all_can_evts) > MAX_ROWS:
            pass  # deque(maxlen=MAX_ROWS) auto-trims

        # Compteur par canal dans le footer de la table
        if cid in self._can_tbl_per_id:
            self._can_tbl_per_id[cid] += 1
            if cid in self._can_tbl_cnt_lbls:
                self._can_tbl_cnt_lbls[cid].setText(
                    f"0x{cid:03X}:{self._can_tbl_per_id[cid]}")

        flt     = self._can_combo.currentText()
        sig_flt = self._can_sig_combo.currentText() if hasattr(self, "_can_sig_combo") else "All Signals"
        if self._frame_matches_filter(ev, flt, sig_flt):
            self._can_tbl.add_event(ev)
        self.lbl_can_cnt.setText(f"{len(self._all_can_evts)} frames")

    @staticmethod
    def _frame_matches_filter(ev: dict, flt: str, sig_flt: str = "All Signals") -> bool:
        # Filtre par message
        if flt != "All Frames":
            cid = ev.get("can_id_int", 0)
            typ = ev.get("type", "")
            mapping = {
                "RX 0x200": (0x200, "RX"),
                "TX 0x201": (0x201, "TX"),
                "TX 0x202": (0x202, "TX"),
                "TX 0x300": (0x300, "TX"),
                "TX 0x301": (0x301, "TX"),
            }
            if flt in mapping:
                fc, ft = mapping[flt]
                if not (cid == fc and typ == ft):
                    return False
        # Filtre par signal — vérifie que le signal est présent dans les fields
        if sig_flt and sig_flt != "All Signals":
            fields = ev.get("fields", {})
            if sig_flt not in fields:
                return False
        return True

    def _filter_can_tbl(self) -> None:
        flt     = self._can_combo.currentText()
        sig_flt = self._can_sig_combo.currentText() if hasattr(self, "_can_sig_combo") else "All Signals"
        prev_auto = self._can_tbl._auto
        self._can_tbl._auto = False
        self._can_tbl.setUpdatesEnabled(False)
        self._can_tbl.clear_all()
        for ev in self._all_can_evts:
            if self._frame_matches_filter(ev, flt, sig_flt):
                self._can_tbl.add_event(ev)
        self._can_tbl._auto = prev_auto
        self._can_tbl.setUpdatesEnabled(True)
        if prev_auto:
            self._can_tbl.scrollToBottom()

    def _update_wc_cmd(self, ev: dict) -> None:
        f    = ev.get("fields", {})
        mode = f.get("mode", 0)
        if isinstance(mode, int):
            op_name = WOP.get(mode, {}).get("name", f"0x{mode:02X}")
            op_col  = WOP.get(mode, {}).get("color", W_TEXT_DIM)
        else:
            op_name = str(mode); op_col = W_TEXT_DIM; mode = 0

        speed = f.get("speed", "?")
        wash  = f.get("wash", "?")
        alive = f.get("alive", "?")
        crc_v = f.get("crc_ok", True)

        self._cmd_widgets["mode"].setText(f"0x{mode:02X}  {op_name}")
        self._cmd_widgets["mode"].setStyleSheet(
            f"color:{op_col};font-weight:bold;background:transparent;font-family:{FONT_MONO};")
        self._cmd_widgets["speed"].setText(str(speed))
        self._cmd_widgets["wash"].setText(str(wash))
        self._cmd_widgets["alive"].setText(f"0x{alive:02X}" if isinstance(alive, int) else str(alive))
        crc_color = A_GREEN if crc_v else A_RED
        self._cmd_widgets["crc_ok"].setText("✓ OK" if crc_v else "✗ FAIL")
        self._cmd_widgets["crc_ok"].setStyleSheet(
            f"color:{crc_color};font-weight:bold;background:transparent;")

        # Piloter le comodo 3D read-only (affichage CAN)
        if hasattr(self, "_comodo_widget"):
            self._comodo_widget.set_op(mode)

        # Sync voiture HTML (si disponible)
        if getattr(self, "_car_html", None):
            self._car_html.set_wiper_op(mode)

    def _update_wc_sta(self, ev: dict) -> None:
        f     = ev.get("fields", {})
        mode  = f.get("mode", 0)
        if isinstance(mode, int):
            op_name = WOP.get(mode, {}).get("name", f"0x{mode:02X}")
        else:
            op_name = str(mode); mode = 0
        blade   = float(f.get("blade_pct", 0))
        cur     = float(f.get("current_A", 0))
        fault   = bool(f.get("fault", False))
        alive   = f.get("alive", 0)
        crc_val = f.get("crc", 0)

        self._sta_mode_lbl.setText(f"0x{mode:02X}  {op_name}")
        self._sta_mode_lbl.setStyleSheet(
            f"color:{WOP.get(mode,{}).get('color', CAN_STA_C)};"
            f"font-weight:bold;background:transparent;")

        cur_col = A_RED if fault else (A_ORANGE if cur > 0.8 else CAN_STA_C)
        self._sta_blade_disp.set_value(f"{blade:.0f}", CAN_VEH_C)
        self._sta_blade_bar.set_value(blade)
        self._sta_cur_disp.set_value(f"{cur:.3f}", cur_col)
        self._sta_cur_bar.set_value(cur, fault)

        self._led_fault.set_state(fault, A_RED if fault else A_GREEN)
        self.lbl_fault.setText("FAULT" if fault else "NO FAULT")
        self.lbl_fault.setStyleSheet(
            f"color:{A_RED if fault else A_GREEN};font-weight:bold;background:transparent;")

        alive_s = f"0x{alive:02X}" if isinstance(alive, int) else str(alive)
        crc_s   = f"0x{crc_val:02X}" if isinstance(crc_val, int) else str(crc_val)
        self._sta_widgets["sta_alive"].setText(alive_s)
        self._sta_widgets["sta_crc"].setText(crc_s)

    def set_can_status(self, msg: str, ok: bool) -> None:
        if self._led_can_status:
            self._led_can_status.set_state(ok)
        self.lbl_can_status.setText(msg.upper())
        self.lbl_can_status.setStyleSheet(
            f"color:{A_GREEN if ok else A_RED};background:transparent;")

    def reset_wc_state(self) -> None:
        """
        Réinitialise l'état interne WC entre deux tests.
        Remet _last_201 à {} pour que panels.py ne calcule pas
        mode_mismatch ni FaultStatus à partir d'une trame résiduelle
        du test précédent lors de la 1ère réception de 0x200.
        Appelé par TestRunner au début du setup de chaque test ERR0x.
        """
        self._last_201 = {}
        self._last_200 = {}

    def set_car_widget(self, w) -> None:
        """Reçoit la référence au CarHTMLWidget central (appelé depuis main_window)."""
        self._car_html = w

    def _update_wc_ack(self, ev: dict) -> None:
        """Met à jour le panneau 0x202 quand une trame Wiper_Ack est reçue/envoyée."""
        f    = ev.get("fields", {})
        ack  = f.get("ack_status", 0)
        err  = f.get("error_code", 0)
        alive = f.get("alive", 0)
        crc   = f.get("crc", 0)

        is_nack = bool(ack)
        ack_col  = A_RED if is_nack else A_GREEN
        err_col  = A_RED if err else W_TEXT_DIM

        self._ack_fields["ack"].setText("1 = NACK" if is_nack else "0 = ACK")
        self._ack_fields["ack"].setStyleSheet(
            f"color:{ack_col};font-weight:bold;background:transparent;font-family:{FONT_MONO};")
        self._ack_fields["err"].setText(f"0x{err:02X}  {'Fault' if err else 'None'}")
        self._ack_fields["err"].setStyleSheet(
            f"color:{err_col};font-weight:bold;background:transparent;font-family:{FONT_MONO};")
        self._ack_fields["alive"].setText(
            f"0x{alive:02X}" if isinstance(alive, int) else str(alive))
        self._ack_fields["alive"].setStyleSheet(
            f"color:{CAN_ACK_C};font-weight:bold;background:transparent;font-family:{FONT_MONO};")
        self._ack_fields["crc"].setText(f"0x{crc:02X}" if isinstance(crc, int) else str(crc))

        self._ack_tx_count += 1
        self._ack_cnt_lbl.setText(str(self._ack_tx_count))