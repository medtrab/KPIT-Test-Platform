"""
WipeWash — Free Layout Engine v3  (ControlDesk-style)
VERSION DÉFINITIVE - AUCUN JAUNE - DESIGN ÉLÉGANT
"""

from __future__ import annotations
import json, os, math
from datetime import datetime
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QSplitter,
    QPushButton, QFileDialog, QScrollArea, QSizePolicy,
    QInputDialog, QMessageBox, QTreeWidget, QTreeWidgetItem,
    QAbstractItemView, QApplication,
)
from PySide6.QtCore import (
    Qt, QPoint, QSize, QMimeData, QByteArray, Signal, QObject,
    QRectF, QPointF, QTimer, QPropertyAnimation, QEasingCurve,
    QParallelAnimationGroup,
)
from PySide6.QtGui import (
    QFont, QColor, QPainter, QPen, QBrush, QPainterPath,
    QMouseEvent, QDrag, QPixmap, QLinearGradient, QRadialGradient,
    QAction, QPalette, QRegion,
)

from constants import (
    FONT_UI, FONT_MONO,
    W_BG, W_PANEL, W_PANEL2, W_PANEL3,
    W_BORDER, W_BORDER2,
    W_TEXT, W_TEXT_DIM, W_DOCK_HDR, W_TEXT_HDR,
    A_TEAL, A_TEAL2, A_GREEN, A_RED, A_ORANGE, A_AMBER,
    KPIT_GREEN,
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
    background-color: #EFF3F8;
    border-color: #94A3B8;
    color: #1A1A1A;
}
QMessageBox QPushButton:default, QInputDialog QPushButton:default {
    background-color: #8DC63F;
    color: #FFFFFF;
    border-color: #6AAF2A;
}
QMessageBox QPushButton:default:hover {
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

def _light_warn(parent, title: str, text: str) -> None:
    mb = QMessageBox(parent)
    mb.setWindowTitle(title)
    mb.setText(text)
    mb.setIcon(QMessageBox.Icon.Warning)
    mb.setStyleSheet(_DIALOG_STYLE)
    mb.exec()

def _light_get_text(parent, title: str, label: str, text: str = "") -> tuple:
    """QInputDialog.getText stylé clair — retourne (str, bool)."""
    dlg = QInputDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setLabelText(label)
    dlg.setTextValue(text)
    dlg.setStyleSheet(_DIALOG_STYLE)
    ok = dlg.exec() == QInputDialog.DialogCode.Accepted
    return dlg.textValue(), ok


_SNAP  = 8
_MIN_W = 150
_MIN_H = 120
_GRIP  = 10

DEFAULT_LAYOUT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "motor_pump_layout.sdf"
)

MIME_INSTRUMENT = "application/x-wipewash-instrument"

def _snap(v: int) -> int:
    return round(v / _SNAP) * _SNAP


# ═══════════════════════════════════════════════════════════
#  SDF LAYOUT — Lecture / Écriture
# ═══════════════════════════════════════════════════════════

_SDF_LAYOUT_VERSION = "3.0"
_SDF_COMPONENT      = "MotorPump_Page"
_SDF_PROJECT        = "WipeWash_BCM_HIL"


def _sdf_write(path: str, profile: str, locked: bool, panels: list[dict]) -> None:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = []

    lines += [
        "[FILEINFO]",
        f"VERSION={_SDF_LAYOUT_VERSION}",
        f"PROJECT={_SDF_PROJECT}",
        f"COMPONENT={_SDF_COMPONENT}",
        f"PROFILE={profile}",
        f"TIMESTAMP={now_str}",
        f"LOCKED={'true' if locked else 'false'}",
        f"PANEL_COUNT={len(panels)}",
        "",
    ]

    for idx, p in enumerate(panels):
        lines += [
            f"[PANEL:{idx:04d}]",
            f"INSTRUMENT_ID={p.get('instrument_id', '')}",
            f"X={p.get('x', 0)}",
            f"Y={p.get('y', 0)}",
            f"W={p.get('w', 200)}",
            f"H={p.get('h', 160)}",
            "",
        ]

    lines.append("[END]")

    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(lines))


def _sdf_read(path: str) -> dict:
    if path.lower().endswith(".json"):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    result = {"_profile": "default", "_locked": False, "panels": []}
    current_panel: dict | None = None

    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue

            if line.startswith("[PANEL:"):
                if current_panel is not None:
                    result["panels"].append(current_panel)
                current_panel = {}
                continue

            if line == "[FILEINFO]" or line == "[END]":
                if current_panel is not None:
                    result["panels"].append(current_panel)
                    current_panel = None
                continue

            if "=" not in line:
                continue

            key, _, val = line.partition("=")
            key = key.strip().upper()
            val = val.strip()

            if current_panel is None:
                if key == "PROFILE":
                    result["_profile"] = val
                elif key == "LOCKED":
                    result["_locked"] = val.lower() == "true"
            else:
                if key == "INSTRUMENT_ID":
                    current_panel["instrument_id"] = val
                elif key in ("X", "Y", "W", "H"):
                    try:
                        current_panel[key.lower()] = int(val)
                    except ValueError:
                        current_panel[key.lower()] = 0

    return result


# ─── Catalogue ────────────────────────────────────────────────────
INSTRUMENT_CATALOG = [
    ("pump_widget",   "Pump",            "PUMP",   "#007ACC", "Animation pompe"),
    ("pump_current",  "Pump Current",    "PUMP",   "#007ACC", "Courant (A)"),
    ("pump_voltage",  "Pump Voltage",    "PUMP",   "#007ACC", "Tension (V)"),
    ("pump_timeout",  "Timeout FSR_005", "PUMP",   "#007ACC", "Countdown FSR"),
    ("front_motor",   "Front Motor",     "MOTOR",  "#007ACC", "Moteur avant"),
    ("front_current", "Front Current",   "MOTOR",  "#007ACC", "Courant avant (A)"),
    ("rear_motor",    "Rear Motor",      "MOTOR",  "#007ACC", "Moteur arriere"),
    ("rear_current",  "Rear Current",    "MOTOR",  "#007ACC", "Courant arriere (A)"),
    ("rest_contact",  "Rest Contact",    "MOTOR",  "#007ACC", "Position lame"),
    ("sys_status",       "System Status",      "SYSTEM", "#007ACC", "Synthese systeme"),
    ("pump_current_curve",  "Pump Current Curve",  "PUMP",   "#007ACC", "Courbe courant pompe"),
    ("motor_front_curve",   "Motor Front Curve",   "MOTOR",  "#007ACC", "Courbe courant moteur avant"),
    ("rest_contact_edge",   "Rest Contact Edges",  "MOTOR",  "#007ACC", "Fronts montant/descendant contact"),
]
_BY_ID = {r[0]: r for r in INSTRUMENT_CATALOG}

_GROUP_COLOR = {"PUMP": "#4FC3F7", "MOTOR": "#8DC63F", "SYSTEM": "#FFB74D"}
_GROUP_BG    = {"PUMP": "#0D1E2E", "MOTOR": "#0D1E0D", "SYSTEM": "#1E1A0A"}


# ═══════════════════════════════════════════════════════════
#  SIGNAL HUB
# ═══════════════════════════════════════════════════════════
class SignalHub(QObject):
    motor_data = Signal(dict)
    pump_data  = Signal(dict)
    lin_data   = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)

    def on_motor_data(self, data: dict) -> None:
        self.motor_data.emit(data)

    def on_pump_data(self, data: dict) -> None:
        self.pump_data.emit(data)

    def on_lin_event(self, data: dict) -> None:
        self.lin_data.emit(data)


# ═══════════════════════════════════════════════════════════
#  INSTRUMENT FACTORY + CONNECT
# ═══════════════════════════════════════════════════════════
def _build_widget(iid: str) -> Optional[QWidget]:
    from widgets_instruments import MotorWidget, PumpWidget, ArcGaugeWidget
    from widgets_motor_pump_enhanced import (
        RestContactWidget, SystemStatusWidget, TimeoutFSRWidget,
        CurrentCurveWidget, RestContactEdgeWidget,
    )
    if iid == "pump_widget":    return PumpWidget()
    if iid == "pump_current":   return ArcGaugeWidget(max_val=2.0,  unit="A")
    if iid == "pump_voltage":   return ArcGaugeWidget(max_val=15.0, unit="V")
    if iid == "pump_timeout":   return TimeoutFSRWidget()
    if iid == "front_motor":    return MotorWidget("FRONT")
    if iid == "front_current":  return ArcGaugeWidget(max_val=1.5,  unit="A")
    if iid == "rear_motor":     return MotorWidget("REAR")
    if iid == "rear_current":   return ArcGaugeWidget(max_val=1.5,  unit="A")
    if iid == "rest_contact":   return RestContactWidget()
    if iid == "sys_status":          return SystemStatusWidget()
    if iid == "pump_current_curve":  return CurrentCurveWidget("PUMP",         max_val=2.0)
    if iid == "motor_front_curve":   return CurrentCurveWidget("MOTOR_FRONT",  max_val=1.5)
    if iid == "rest_contact_edge":   return RestContactEdgeWidget()
    return None


def _connect_widget(iid: str, widget: QWidget, hub: SignalHub) -> None:
    from widgets_instruments import MotorWidget, PumpWidget, ArcGaugeWidget
    from widgets_motor_pump_enhanced import (
        RestContactWidget, SystemStatusWidget, TimeoutFSRWidget,
        CurrentCurveWidget, RestContactEdgeWidget,
    )
    import json as _j

    def _d(v):
        if isinstance(v, dict): return v
        try:    return _j.loads(v) if isinstance(v, str) else {}
        except: return {}

    if iid == "pump_widget" and isinstance(widget, PumpWidget):
        def _f(data): widget.set_state(data.get("state","OFF"), bool(data.get("fault",False)))
        hub.pump_data.connect(_f)
    elif iid == "pump_current" and isinstance(widget, ArcGaugeWidget):
        def _f(data): widget.set_value(float(data.get("current", data.get("pump_current",0))), bool(data.get("fault",False)))
        hub.pump_data.connect(_f)
    elif iid == "pump_voltage" and isinstance(widget, ArcGaugeWidget):
        def _f(data): widget.set_value(float(data.get("voltage", data.get("pump_voltage",0))), bool(data.get("fault",False)))
        hub.pump_data.connect(_f)
    elif iid == "pump_timeout" and isinstance(widget, TimeoutFSRWidget):
        def _f(data):
            widget.set_state(float(data.get("timeout_remaining",0)), float(data.get("timeout_duration",5)),
                             bool(data.get("timeout_active",False)), str(data.get("timeout_source","")),
                             str(data.get("timeout_info","Pump inactive")))
        hub.pump_data.connect(_f)
    elif iid == "front_motor" and isinstance(widget, MotorWidget):
        def _f(data):
            if isinstance(data.get("front"), str):
                widget.set_state(data.get("front","OFF"), data.get("speed","Speed1"))
            else:
                f = _d(data.get("front",{}))
                widget.set_state("ON" if f.get("enable",0) else "OFF", "Speed2" if f.get("speed",0) else "Speed1")
        hub.motor_data.connect(_f)
    elif iid == "front_current" and isinstance(widget, ArcGaugeWidget):
        def _f(data):
            if isinstance(data.get("front"), str):
                front_on = data.get("front", "OFF") == "ON"
                cur = float(data.get("current", 0)) if front_on else 0.0
                widget.set_value(cur, bool(data.get("fault", False)))
            else:
                f = _d(data.get("front", {}))
                widget.set_value(float(f.get("motor_current", 0)), bool(f.get("fault_status", False)))
        hub.motor_data.connect(_f)
    elif iid == "rear_motor" and isinstance(widget, MotorWidget):
        def _f(data):
            if isinstance(data.get("rear"), str):
                widget.set_state(data.get("rear","OFF"), data.get("speed","Speed1"))
            else:
                r = _d(data.get("rear",{}))
                widget.set_state("ON" if r.get("enable",0) else "OFF", "Speed2" if r.get("speed",0) else "Speed1")
        hub.motor_data.connect(_f)
    elif iid == "rear_current" and isinstance(widget, ArcGaugeWidget):
        def _f(data):
            if isinstance(data.get("rear"), str):
                rear_on = data.get("rear", "OFF") == "ON"
                cur = float(data.get("current", 0)) if rear_on else 0.0
                widget.set_value(cur, bool(data.get("fault", False)))
            else:
                r = _d(data.get("rear", {}))
                widget.set_value(float(r.get("motor_current", 0)), bool(r.get("fault_status", False)))
        hub.motor_data.connect(_f)
    elif iid == "rest_contact" and isinstance(widget, RestContactWidget):
        def _f(data):
            if isinstance(data.get("front"), str):
                parked = data.get("rest","") == "PARKED"
            else:
                parked = bool(_d(data.get("front",{})).get("rest_contact",0))
            widget.set_state(parked)
        hub.motor_data.connect(_f)
    elif iid == "sys_status" and isinstance(widget, SystemStatusWidget):
        def _f(data):
            if isinstance(data.get("front"), str):
                fs = data.get("front","?"); rs = data.get("rear","?")
                sp = data.get("speed","?"); cur = float(data.get("current",0))
                fault = bool(data.get("fault",False))
            else:
                f = _d(data.get("front",{})); r = _d(data.get("rear",{}))
                fs = "ON" if f.get("enable",0) else "OFF"
                rs = "ON" if r.get("enable",0) else "OFF"
                sp = "Speed2" if f.get("speed",0) else "Speed1"
                cur = float(f.get("motor_current",0)) + float(r.get("motor_current",0))
                fault = bool(f.get("fault_status",0)) or bool(r.get("fault_status",0))
            widget.set_values(fs, rs, sp, f"{cur:.3f} A", fault,
                              "FAULT FSR_003" if fault else "System nominal")
        hub.motor_data.connect(_f)
    elif iid == "pump_current_curve" and isinstance(widget, CurrentCurveWidget):
        def _f(data):
            widget.set_value(float(data.get("current", data.get("pump_current", 0))),
                             bool(data.get("fault", False)))
        hub.pump_data.connect(_f)
    elif iid == "motor_front_curve" and isinstance(widget, CurrentCurveWidget):
        def _f(data):
            if isinstance(data.get("front"), str):
                front_on = data.get("front", "OFF") == "ON"
                cur = float(data.get("current", 0)) if front_on else 0.0
                widget.set_value(cur, bool(data.get("fault", False)))
            else:
                import json as _j
                def _d(v):
                    if isinstance(v, dict): return v
                    try: return _j.loads(v) if isinstance(v, str) else {}
                    except: return {}
                f = _d(data.get("front", {}))
                widget.set_value(float(f.get("motor_current", 0)),
                                 bool(f.get("fault_status", False)))
        hub.motor_data.connect(_f)
    elif iid == "rest_contact_edge" and isinstance(widget, RestContactEdgeWidget):
        def _f_motor(data):
            if "rest_contact_raw" in data:
                widget.set_state(not bool(data["rest_contact_raw"]))
                return
            if isinstance(data.get("front"), str):
                widget.set_state(data.get("rest", "") == "PARKED")
            else:
                import json as _j
                def _d(v):
                    if isinstance(v, dict): return v
                    try: return _j.loads(v) if isinstance(v, str) else {}
                    except: return {}
                widget.set_state(not bool(_d(data.get("front", {})).get("rest_contact", 0)))
        hub.motor_data.connect(_f_motor)

        def _f_lin(data):
            if "rest_contact_raw" in data:
                widget.set_state(not bool(data["rest_contact_raw"]))
        hub.lin_data.connect(_f_lin)


# ═══════════════════════════════════════════════════════════
#  INSTRUMENT TREE
# ═══════════════════════════════════════════════════════════
class InstrumentTree(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setAnimated(False)
        self.setIndentation(0)
        self.setRootIsDecorated(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("""
            QTreeWidget {
                background: #1C2B1C;
                color: #E8F5E0;
                border: none;
                font-family: 'Segoe UI';
                font-size: 10pt;
                outline: none;
            }
            QTreeWidget::item {
                padding: 0px;
                border: none;
            }
            QTreeWidget::item:selected { background: transparent; }
            QTreeWidget::item:hover    { background: transparent; }
            QScrollBar:vertical {
                background: #1C2B1C;
                width: 4px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #6A9A4A;
                border-radius: 2px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        self._build()

    # ── Vector icon painter ───────────────────────────────────────────────────
    @staticmethod
    def _draw_icon(iid: str, color: QColor, size: int = 16) -> QPixmap:
        """Draw a crisp vector icon for each instrument id."""
        pm = QPixmap(size, size)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(color, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        s = size
        m = 1.5  # margin

        if iid == "pump_widget":
            # Water drop / pump: circle with small inlet arrow
            p.drawEllipse(QRectF(m, m+2, s-m*2, s-m*2-2))
            p.drawLine(QPointF(s/2, m), QPointF(s/2, m+3))
            p.drawLine(QPointF(s/2-2, m+2), QPointF(s/2, m))
            p.drawLine(QPointF(s/2+2, m+2), QPointF(s/2, m))

        elif iid in ("pump_current", "front_current", "rear_current"):
            # Lightning bolt
            pts = [QPointF(s*0.62, m), QPointF(s*0.3, s*0.52),
                   QPointF(s*0.54, s*0.52), QPointF(s*0.38, s-m),
                   QPointF(s*0.70, s*0.48), QPointF(s*0.46, s*0.48)]
            path = QPainterPath()
            path.moveTo(pts[0])
            for pt in pts[1:]: path.lineTo(pt)
            path.closeSubpath()
            p.setBrush(QBrush(color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(path)

        elif iid == "pump_voltage":
            # Sine wave
            pen2 = QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(pen2)
            pts2 = []
            import math as _m
            for i in range(30):
                x = m + (s - m*2) * i / 29
                y = s/2 - (s/2 - m - 1) * _m.sin(i / 29 * 2 * _m.pi * 1.5)
                pts2.append(QPointF(x, y))
            for i in range(len(pts2)-1):
                p.drawLine(pts2[i], pts2[i+1])

        elif iid == "pump_timeout":
            # Stopwatch / clock: circle + hand
            p.drawEllipse(QRectF(m, m+1, s-m*2, s-m*2))
            p.drawLine(QPointF(s/2, s/2), QPointF(s/2, m+3))
            p.drawLine(QPointF(s/2, s/2), QPointF(s*0.7, s*0.62))
            # crown
            p.drawLine(QPointF(s/2-2, m+1), QPointF(s/2-2, m-0.5))
            p.drawLine(QPointF(s/2+2, m+1), QPointF(s/2+2, m-0.5))

        elif iid in ("pump_current_curve", "motor_front_curve"):
            # Trend line with upward arrow
            pen2 = QPen(color, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(pen2)
            p.drawLine(QPointF(m, s-m), QPointF(s*0.35, s*0.6))
            p.drawLine(QPointF(s*0.35, s*0.6), QPointF(s*0.65, s*0.4))
            p.drawLine(QPointF(s*0.65, s*0.4), QPointF(s-m, m+1))
            # arrowhead
            p.drawLine(QPointF(s-m, m+1), QPointF(s-m-3, m+2))
            p.drawLine(QPointF(s-m, m+1), QPointF(s-m-1.5, m+4))

        elif iid == "front_motor":
            # Forward triangle (play)
            path = QPainterPath()
            path.moveTo(m+1, m)
            path.lineTo(m+1, s-m)
            path.lineTo(s-m, s/2)
            path.closeSubpath()
            p.setBrush(QBrush(color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(path)

        elif iid == "rear_motor":
            # Reverse triangle
            path = QPainterPath()
            path.moveTo(s-m-1, m)
            path.lineTo(s-m-1, s-m)
            path.lineTo(m, s/2)
            path.closeSubpath()
            p.setBrush(QBrush(color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(path)

        elif iid == "rest_contact":
            # Switch / contact: horizontal line with gap and dot
            p.drawLine(QPointF(m, s/2), QPointF(s*0.35, s/2))
            p.drawLine(QPointF(s*0.65, s/2), QPointF(s-m, s/2))
            p.setBrush(QBrush(color))
            p.drawEllipse(QRectF(s/2-2, s/2-2, 4, 4))
            # vertical ticks = contact points
            p.drawLine(QPointF(s*0.35, s/2-3), QPointF(s*0.35, s/2+3))
            p.drawLine(QPointF(s*0.65, s/2-3), QPointF(s*0.65, s/2+3))

        elif iid == "rest_contact_edge":
            # Signal edge / pulse shape
            pen2 = QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(pen2)
            p.drawLine(QPointF(m, s-m-2), QPointF(s*0.3, s-m-2))
            p.drawLine(QPointF(s*0.3, s-m-2), QPointF(s*0.3, m+2))
            p.drawLine(QPointF(s*0.3, m+2), QPointF(s*0.7, m+2))
            p.drawLine(QPointF(s*0.7, m+2), QPointF(s*0.7, s-m-2))
            p.drawLine(QPointF(s*0.7, s-m-2), QPointF(s-m, s-m-2))

        elif iid == "sys_status":
            # Monitor / screen
            p.drawRoundedRect(QRectF(m, m, s-m*2, s*0.7), 1.5, 1.5)
            p.drawLine(QPointF(s/2, m+s*0.7), QPointF(s/2, s-m-1))
            p.drawLine(QPointF(s*0.3, s-m-1), QPointF(s*0.7, s-m-1))
            # signal bars inside screen
            p.drawLine(QPointF(s*0.35, m+s*0.5), QPointF(s*0.35, m+s*0.15))
            p.drawLine(QPointF(s*0.5,  m+s*0.5), QPointF(s*0.5,  m+s*0.25))
            p.drawLine(QPointF(s*0.65, m+s*0.5), QPointF(s*0.65, m+s*0.05))

        else:
            # Fallback: small filled diamond
            path = QPainterPath()
            cx, cy, r = s/2, s/2, s/2 - m
            path.moveTo(cx, cy - r)
            path.lineTo(cx + r, cy)
            path.lineTo(cx, cy + r)
            path.lineTo(cx - r, cy)
            path.closeSubpath()
            p.setBrush(QBrush(color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(path)

        p.end()
        return pm

    @staticmethod
    def _draw_group_icon(group: str, color: QColor, size: int = 14) -> QPixmap:
        """Draw group header icon."""
        pm = QPixmap(size, size)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(color, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        s = size
        m = 1.0

        if group == "PUMP":
            # Pipe cross-section + flow arrow
            p.drawEllipse(QRectF(m, m, s-m*2, s-m*2))
            p.drawLine(QPointF(s/2, m+2), QPointF(s/2, s-m-2))
            p.drawLine(QPointF(m+2, s/2), QPointF(s-m-2, s/2))

        elif group == "MOTOR":
            # Gear: circle + 6 teeth
            import math as _m
            p.drawEllipse(QRectF(s*0.3, s*0.3, s*0.4, s*0.4))
            for i in range(6):
                angle = i * _m.pi / 3
                x1 = s/2 + (s*0.3) * _m.cos(angle)
                y1 = s/2 + (s*0.3) * _m.sin(angle)
                x2 = s/2 + (s*0.46) * _m.cos(angle)
                y2 = s/2 + (s*0.46) * _m.sin(angle)
                p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        elif group == "SYSTEM":
            # Desktop monitor outline
            p.drawRoundedRect(QRectF(m, m, s-m*2, s*0.68), 1, 1)
            p.drawLine(QPointF(s/2, m+s*0.68), QPointF(s/2, s-m))
            p.drawLine(QPointF(s*0.28, s-m), QPointF(s*0.72, s-m))

        else:
            p.drawEllipse(QRectF(m, m, s-m*2, s-m*2))

        p.end()
        return pm

    def _make_group_widget(self, group: str, color: str) -> QWidget:
        w = QWidget()
        w.setFixedHeight(30)
        w.setStyleSheet(f"""
            QWidget {{
                background: #253525;
                border-left: 4px solid {color};
                border-top: 1px solid #304530;
                border-bottom: 1px solid #304530;
            }}
        """)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(7)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(self._draw_group_icon(group, QColor(color), 14))
        icon_lbl.setFixedSize(14, 14)
        icon_lbl.setStyleSheet("background:transparent; border:none;")

        txt = QLabel(group)
        txt.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        txt.setStyleSheet(f"color:{color}; background:transparent; border:none; letter-spacing:3px;")
        lay.addWidget(icon_lbl)
        lay.addWidget(txt)
        lay.addStretch()
        return w

    def _make_item_widget(self, iid: str, label: str, desc: str) -> QWidget:
        w = QWidget()
        w.setFixedHeight(34)
        w.setObjectName("instrItem")
        w.setStyleSheet("""
            QWidget#instrItem { background: #1C2B1C; }
            QWidget#instrItem:hover { background: #263826; }
        """)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(18, 0, 12, 0)
        lay.setSpacing(8)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(self._draw_icon(iid, QColor("#7AB860"), 16))
        icon_lbl.setFixedSize(16, 16)
        icon_lbl.setStyleSheet("background:transparent; border:none;")

        name_lbl = QLabel(label)
        name_lbl.setFont(QFont("Segoe UI", 10))
        name_lbl.setStyleSheet("color: #E0F0D0; background:transparent; border:none;")
        name_lbl.setToolTip(f"{label}  —  {desc}")
        lay.addWidget(icon_lbl)
        lay.addWidget(name_lbl, 1)
        return w

    def _make_divider(self) -> QTreeWidgetItem:
        """Visible gap between groups."""
        div = QTreeWidgetItem(self, [""])
        div.setFlags(Qt.ItemFlag.NoItemFlags)
        div.setSizeHint(0, QSize(0, 10))
        div.setBackground(0, QColor("#141F14"))
        return div

    def _make_item_separator(self) -> QTreeWidgetItem:
        """1px line between instrument rows."""
        sep = QTreeWidgetItem(self, [""])
        sep.setFlags(Qt.ItemFlag.NoItemFlags)
        sep.setSizeHint(0, QSize(0, 1))
        sep.setBackground(0, QColor("#2E472E"))
        return sep

    def _build(self):
        groups: dict[str, list] = {}
        for row in INSTRUMENT_CATALOG:
            groups.setdefault(row[2], []).append(row)

        first = True
        for group, items in groups.items():
            color = _GROUP_COLOR.get(group, "#AAAAAA")

            if not first:
                self._make_divider()
            first = False

            grp_item = QTreeWidgetItem(self, [""])
            grp_item.setFlags(Qt.ItemFlag.NoItemFlags)
            grp_item.setSizeHint(0, QSize(0, 30))
            self.setItemWidget(grp_item, 0, self._make_group_widget(group, color))

            for i, (iid, label, _, accent, desc) in enumerate(items):
                if i > 0:
                    self._make_item_separator()
                child = QTreeWidgetItem(self, [""])
                child.setSizeHint(0, QSize(0, 34))
                child.setData(0, Qt.ItemDataRole.UserRole, iid)
                child.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled)
                self.setItemWidget(child, 0, self._make_item_widget(iid, label, desc))

    def startDrag(self, supported_actions):
        item = self.currentItem()
        if item is None:
            return
        iid = item.data(0, Qt.ItemDataRole.UserRole)
        if not iid:
            return
        mime = QMimeData()
        mime.setData(MIME_INSTRUMENT, QByteArray(iid.encode()))
        drag = QDrag(self)
        drag.setMimeData(mime)

        row    = _BY_ID.get(iid)
        accent = row[3] if row else KPIT_GREEN
        label  = row[1] if row else iid
        pm = QPixmap(220, 32)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, 220, 32), 5, 5)
        c = QColor(accent); c.setAlpha(230)
        p.fillPath(path, QBrush(c))
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        p.setPen(QPen(QColor("white")))
        p.drawText(12, 0, 200, 32, Qt.AlignmentFlag.AlignVCenter, label)
        p.end()
        drag.setPixmap(pm)
        drag.setHotSpot(QPoint(110, 16))
        drag.exec(Qt.DropAction.CopyAction)


# ═══════════════════════════════════════════════════════════
#  DROPPED PANEL
# ═══════════════════════════════════════════════════════════
class DroppedPanel(QWidget):
    close_requested = Signal(object)
    focused         = Signal(object)

    def __init__(self, iid: str, widget: QWidget,
                 accent: str, label: str, parent=None):
        super().__init__(parent)
        self.instrument_id = iid
        self._accent   = accent
        self._widget   = widget
        self._locked   = False
        self._dragging = False
        self._drag_off = QPoint()
        self._resizing = False
        self._rs_pos   = QPoint()
        self._rs_size  = QSize()

        self.setMinimumSize(_MIN_W, _MIN_H)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._build(label)

    def _build(self, label: str):
        self.setStyleSheet(f"""
            DroppedPanel {{
                background: {W_PANEL};
                border: 1px solid {W_BORDER2};
                border-top: 3px solid {self._accent};
                border-radius: 4px;
            }}
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._hdr = QFrame()
        self._hdr.setObjectName("dp_hdr")
        self._hdr.setFixedHeight(28)
        self._hdr.setStyleSheet(f"""
            QFrame#dp_hdr {{
                background: {W_PANEL2};
                border-bottom: 1px solid {W_BORDER};
            }}
        """)
        self._hdr.setMouseTracking(True)
        hl = QHBoxLayout(self._hdr)
        hl.setContentsMargins(8, 0, 4, 0)
        hl.setSpacing(6)

        badge = QLabel(f" {label} ")
        badge.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
        badge.setStyleSheet(
            f"color:{self._accent};"
            f"background:rgba(0,0,0,0.05);"
            f"border:1px solid {self._accent};"
            f"border-radius:9px;padding:0 5px;"
        )
        hl.addWidget(badge)
        hl.addStretch()

        btn = QPushButton("x")
        btn.setFixedSize(18, 18)
        btn.setFont(QFont(FONT_MONO, 7))
        btn.setStyleSheet(
            "QPushButton{background:transparent;color:#AAAAAA;border:none;border-radius:3px;}"
            "QPushButton:hover{background:#FFEBEE;color:#C62828;}"
        )
        btn.clicked.connect(lambda: self.close_requested.emit(self))
        hl.addWidget(btn)
        root.addWidget(self._hdr)

        self._widget.setParent(self)
        root.addWidget(self._widget, 1)

        gr = QHBoxLayout()
        gr.setContentsMargins(0, 0, 2, 1)
        gr.addStretch()
        self._grip = QLabel("◢")
        self._grip.setFixedSize(11, 11)
        self._grip.setFont(QFont(FONT_MONO, 7))
        self._grip.setStyleSheet(f"color:{W_TEXT_DIM};background:transparent;")
        self._grip.setCursor(Qt.CursorShape.SizeFDiagCursor)
        gr.addWidget(self._grip)
        root.addLayout(gr)
        self._hdr.installEventFilter(self)

    def set_locked(self, v: bool):
        self._locked = v
        self._hdr.setCursor(Qt.CursorShape.ArrowCursor if v else Qt.CursorShape.SizeAllCursor)
        self._grip.setVisible(not v)

    def to_dict(self) -> dict:
        return {"instrument_id": self.instrument_id,
                "x": self.x(), "y": self.y(),
                "w": self.width(), "h": self.height()}

    def eventFilter(self, obj, event):
        if obj is self._hdr and not self._locked:
            t = event.type()
            if t == event.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._dragging = True
                self._drag_off = event.globalPosition().toPoint() - self.pos()
                self.focused.emit(self); return True
            if t == event.Type.MouseButtonDblClick:
                self.close_requested.emit(self); return True
            if t == event.Type.MouseMove and self._dragging and \
               event.buttons() & Qt.MouseButton.LeftButton:
                np = event.globalPosition().toPoint() - self._drag_off
                self.move(_snap(max(0, np.x())), _snap(max(0, np.y()))); return True
            if t == event.Type.MouseButtonRelease:
                self._dragging = False; return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton and self._in_grip(e.pos()) and not self._locked:
            self._resizing = True
            self._rs_pos  = e.globalPosition().toPoint()
            self._rs_size = self.size()
            self.focused.emit(self)
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._resizing and not self._locked:
            d = e.globalPosition().toPoint() - self._rs_pos
            self.resize(_snap(max(_MIN_W, self._rs_size.width()  + d.x())),
                        _snap(max(_MIN_H, self._rs_size.height() + d.y())))
        elif not self._resizing:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor
                           if self._in_grip(e.pos()) else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent):
        self._resizing = False
        super().mouseReleaseEvent(e)

    def _in_grip(self, pos: QPoint) -> bool:
        return pos.x() >= self.width()-_GRIP and pos.y() >= self.height()-_GRIP

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i in range(3):
            p.setPen(QPen(QColor(0, 0, 0, 12), 1))
            p.drawRoundedRect(i, i, self.width()-1-i, self.height()-1-i, 4, 4)
        p.setPen(QPen(QColor(self._accent), 1))
        p.drawRoundedRect(0, 0, self.width()-1, self.height()-1, 4, 4)


# ═══════════════════════════════════════════════════════════
#  CIRCULAR DROP CANVAS
# ═══════════════════════════════════════════════════════════
class CircularDropCanvas(QWidget):
    CAR_W = 560
    CAR_H = 480

    def __init__(self, car_widget: QWidget, hub: SignalHub, parent=None):
        super().__init__(parent)
        self._car    = car_widget
        self._hub    = hub
        self._panels: list[DroppedPanel] = []
        self._locked = False
        self._grid   = True
        self._drag_over = False

        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(500, 400)
        self.setStyleSheet(f"background:{W_BG};")
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(W_BG))
        self.setPalette(pal)

        self._car.setParent(self)
        self._car.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._car.show()
        self._reposition_car()

    def _car_radius(self) -> int:
        return min(self.CAR_W, self.CAR_H) // 2

    def _apply_circle_mask(self):
        from PySide6.QtGui import QRegion
        r = self._car_radius()
        cx = self.CAR_W // 2
        cy = self.CAR_H // 2
        region = QRegion(cx - r, cy - r, r * 2, r * 2, QRegion.RegionType.Ellipse)
        self._car.setMask(region)

    def _reposition_car(self):
        cx = (self.width()  - self.CAR_W) // 2
        cy = (self.height() - self.CAR_H) // 2
        ox = max(0, cx)
        oy = max(0, cy)
        self._car.setGeometry(ox, oy, self.CAR_W, self.CAR_H)
        self._apply_circle_mask()
        self._car.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._reposition_car()
        self.update()

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME_INSTRUMENT):
            self._drag_over = True
            self.update()
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME_INSTRUMENT):
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._drag_over = False
        self.update()

    def dropEvent(self, e):
        self._drag_over = False
        self.update()
        if e.mimeData().hasFormat(MIME_INSTRUMENT):
            raw = bytes(e.mimeData().data(MIME_INSTRUMENT)).decode()
            pos = e.position().toPoint()
            self._spawn(raw, _snap(max(0, pos.x() - 90)), _snap(max(0, pos.y() - 20)))
            e.acceptProposedAction()

    def _spawn(self, iid: str, x: int, y: int,
               w: int = 200, h: int = 160) -> Optional[DroppedPanel]:
        row = _BY_ID.get(iid)
        if row is None:
            return None
        widget = _build_widget(iid)
        if widget is None:
            return None
        _connect_widget(iid, widget, self._hub)
        dp = DroppedPanel(iid, widget, row[3], row[1], self)
        dp.close_requested.connect(self._remove)
        dp.focused.connect(self._raise_panel)
        dp.setGeometry(x, y, w, h)
        if self._locked:
            dp.set_locked(True)
        dp.show()
        self._car.raise_()
        self._panels.append(dp)
        return dp

    def _remove(self, dp: DroppedPanel):
        if dp in self._panels:
            self._panels.remove(dp)
        dp.hide()
        dp.deleteLater()

    def _raise_panel(self, dp: DroppedPanel):
        dp.raise_()
        self._car.raise_()

    def set_locked(self, v: bool):
        self._locked = v
        for dp in self._panels:
            dp.set_locked(v)

    def set_grid(self, v: bool):
        self._grid = v
        self.update()

    def get_layout(self) -> list:
        return [dp.to_dict() for dp in self._panels]

    def apply_layout(self, items: list):
        for dp in list(self._panels):
            dp.hide()
            dp.deleteLater()
        self._panels.clear()
        for d in items:
            self._spawn(d.get("instrument_id", ""), d.get("x", 10), d.get("y", 10),
                        d.get("w", 200), d.get("h", 160))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.fillRect(0, 0, W, H, QColor(W_BG))

        if self._grid:
            for x in range(0, W, _SNAP):
                alpha = 45 if x % (8 * _SNAP) == 0 else 18
                p.setPen(QPen(QColor(160, 160, 160, alpha), 1))
                p.drawLine(x, 0, x, H)
            for y in range(0, H, _SNAP):
                alpha = 45 if y % (8 * _SNAP) == 0 else 18
                p.setPen(QPen(QColor(160, 160, 160, alpha), 1))
                p.drawLine(0, y, W, y)


# ═══════════════════════════════════════════════════════════
#  LAYOUT TOOLBAR
# ═══════════════════════════════════════════════════════════
class LayoutToolbar(QWidget):
    reset_requested = Signal()

    def __init__(self, canvas: CircularDropCanvas, parent=None):
        super().__init__(parent)
        self._canvas = canvas
        self._locked = False
        self._dir    = os.path.dirname(DEFAULT_LAYOUT_PATH) or "."
        self._build()

    def _build(self):
        self.setFixedHeight(42)
        self.setStyleSheet(f"background:#08080C;border-top:1px solid {KPIT_GREEN}55;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 4)
        lay.setSpacing(8)

        def _btn(text, bg, fg, tip, cb, check=False):
            b = QPushButton(text)
            b.setFixedHeight(30); b.setMinimumWidth(90)
            b.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
            b.setCheckable(check); b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton{{background:{bg};color:{fg};border:1px solid {bg};border-radius:5px;padding:0 14px;}}"
                f"QPushButton:hover{{background:{bg}CC;color:{fg};border:1px solid {bg};}}"
                f"QPushButton:checked{{background:{bg}33;color:{bg};border:2px solid {bg};}}"
            )
            b.clicked.connect(cb); return b

        def _sep():
            s = QFrame(); s.setFrameShape(QFrame.Shape.VLine)
            s.setFixedWidth(1); s.setStyleSheet(f"background:{W_BORDER2};"); return s

        lay.addWidget(_btn("Save",   KPIT_GREEN, "#FFF", "Sauvegarder", self._save))
        lay.addWidget(_btn("Load",   A_TEAL,     "#FFF", "Load",        self._load))
        lay.addWidget(_sep())
        lay.addWidget(_btn("Reset",  A_ORANGE,   "#FFF", "Vider",       self._reset))
        lay.addWidget(_sep())
        self._btn_lock = _btn("Unlock", A_AMBER,    "#FFF", "Verrouiller", self._toggle_lock, True)
        lay.addWidget(self._btn_lock)
        self._btn_grid = _btn("Grid",   W_TEXT_DIM, "#FFF", "Grille",      self._toggle_grid, True)
        self._btn_grid.setChecked(True)
        lay.addWidget(self._btn_grid)
        lay.addStretch()
        self._lbl = QLabel("profile: —")
        self._lbl.setFont(QFont(FONT_MONO, 9))
        self._lbl.setStyleSheet(f"color:{W_TEXT_DIM};background:transparent;")
        lay.addWidget(self._lbl)

    def _save(self):
        name, ok = _light_get_text(self, "Sauvegarder", "Nom du profil :", text="default")
        if not ok or not name.strip(): return
        name = name.strip().replace(" ", "_")
        path = os.path.join(self._dir, f"{name}.sdf")
        try:
            os.makedirs(self._dir, exist_ok=True)
            _sdf_write(path, profile=name, locked=self._locked,
                       panels=self._canvas.get_layout())
            self._lbl.setText(f"profile: {name}  OK")
        except Exception as ex:
            _light_warn(self, "Erreur", f"Impossible de sauvegarder:\n{ex}")

    def _load(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load File", self._dir,
            "Layout SDF (*.sdf);;Layout JSON (*.json);;Tous (*.*)"
        )
        if not path: return
        try:
            data = _sdf_read(path)
        except Exception as ex:
            _light_warn(self, "Erreur", f"Impossible de lire:\n{ex}"); return
        self._canvas.apply_layout(data.get("panels", []))
        profile = data.get("_profile", "?")
        if data.get("_locked", False):
            self._btn_lock.setChecked(True); self._apply_lock(True)
        self._lbl.setText(f"profile: {profile}  OK")

    def _reset(self):
        self._canvas.apply_layout([]); self._lbl.setText("profile: —")
        self.reset_requested.emit()

    def _toggle_lock(self): self._apply_lock(self._btn_lock.isChecked())

    def _apply_lock(self, v: bool):
        self._locked = v; self._canvas.set_locked(v)
        self._btn_lock.setText("Locked" if v else "Unlock")

    def _toggle_grid(self): self._canvas.set_grid(self._btn_grid.isChecked())

    def auto_load(self, path: str):
        candidates = [path]
        if path.endswith(".sdf"):
            candidates.append(path[:-4] + ".json")
        elif path.endswith(".json"):
            candidates.insert(0, path[:-5] + ".sdf")

        for candidate in candidates:
            if not os.path.exists(candidate):
                continue
            try:
                data = _sdf_read(candidate)
                self._canvas.apply_layout(data.get("panels", []))
                self._lbl.setText(f"profile: {data.get('_profile', 'default')}  OK")
                return
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════
#  AUDI GAUGE WIDGET
# ═══════════════════════════════════════════════════════════
class _AudiGaugeWidget(QWidget):
    value_changed = Signal(int)

    def __init__(self, mode: str, parent=None):
        super().__init__(parent)
        self._mode = mode
        self._val = 0
        self._max = 2000 if mode == "speed" else 100
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(200, 200)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_value(self, v: int):
        self._val = max(0, min(self._max, v))
        self.update()

    def value(self): return self._val

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        cx, cy = W // 2, H // 2
        R = min(cx, cy) - 10

        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(0, 0, W, H, Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        bg = QRadialGradient(cx, cy, R * 1.2)
        bg.setColorAt(0, QColor("#111115")); bg.setColorAt(1, QColor("#050507"))
        p.fillRect(0, 0, W, H, QBrush(bg))

        chrome = QLinearGradient(cx - R - 8, cy - R - 8, cx + R + 8, cy + R + 8)
        chrome.setColorAt(0.0, QColor("#2A2A32")); chrome.setColorAt(0.3, QColor("#707078"))
        chrome.setColorAt(0.7, QColor("#909098")); chrome.setColorAt(1.0, QColor("#1E1E26"))
        p.setPen(QPen(QBrush(chrome), 3)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(cx - R - 4, cy - R - 4, (R + 4) * 2, (R + 4) * 2)

        p.setPen(QPen(QColor("#1A1A22"), 1))
        p.drawEllipse(cx - R, cy - R, R * 2, R * 2)

        arc_w = max(10, R // 7)
        pen_bg = QPen(QColor("#1C1C26"), arc_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen_bg); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(cx - R + arc_w, cy - R + arc_w,
                  (R - arc_w) * 2, (R - arc_w) * 2, 225 * 16, -270 * 16)

        pct = self._val / self._max

        if pct > 0:
            if self._mode == "speed":
                nc = "#E8A020" if pct < 0.65 else ("#FF6010" if pct < 0.85 else "#FF2020")
            else:
                nc = "#2878D8" if pct < 0.5 else ("#1050B0" if pct < 0.8 else "#0838A0")
            pen_fill = QPen(QColor(nc), arc_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(pen_fill)
            p.drawArc(cx - R + arc_w, cy - R + arc_w,
                      (R - arc_w) * 2, (R - arc_w) * 2, 225 * 16, int(-270 * 16 * pct))

        n_ticks = 20 if self._mode == "speed" else 10
        labels_vals = [0, 50, 100, 150, 200] if self._mode == "speed" else [0, 25, 50, 75, 100]
        labels_idx  = [0, n_ticks // 4, n_ticks // 2, 3 * n_ticks // 4, n_ticks]
        for i in range(n_ticks + 1):
            a_deg = -225 + 270 * i / n_ticks
            a_rad = math.radians(a_deg)
            is_major = (i % (n_ticks // 4) == 0) or i == n_ticks
            outer_r = R - arc_w * 2 - 1
            inner_r = outer_r - (int(R * 0.12) if is_major else int(R * 0.06))
            x1 = cx + outer_r * math.cos(a_rad); y1 = cy - outer_r * math.sin(a_rad)
            x2 = cx + inner_r * math.cos(a_rad); y2 = cy - inner_r * math.sin(a_rad)
            if is_major:
                col = ("#E8A020" if self._mode == "speed" else "#2878D8") if (i / n_ticks <= pct) else "#404050"
                p.setPen(QPen(QColor(col), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            else:
                col = ("#B07010" if self._mode == "speed" else "#18508A") if (i / n_ticks <= pct) else "#252535"
                p.setPen(QPen(QColor(col), 1))
            p.drawLine(int(x1), int(y1), int(x2), int(y2))

        p.setFont(QFont(FONT_MONO, max(5, R // 14), QFont.Weight.Bold))
        for k, label in enumerate(labels_vals):
            i = labels_idx[k]
            a_deg = -225 + 270 * i / n_ticks
            a_rad = math.radians(a_deg)
            lr = R - arc_w * 2 - int(R * 0.22)
            lx = int(cx + lr * math.cos(a_rad))
            ly = int(cy - lr * math.sin(a_rad))
            p.setPen(QPen(QColor("#9898A8")))
            p.drawText(lx - 14, ly - 7, 28, 14, Qt.AlignmentFlag.AlignCenter, str(label))

        ir = R - arc_w * 2 - int(R * 0.28)
        dial_bg = QRadialGradient(cx, cy, ir)
        dial_bg.setColorAt(0, QColor("#0D0D12")); dial_bg.setColorAt(1, QColor("#080810"))
        p.setBrush(QBrush(dial_bg)); p.setPen(QPen(QColor("#18182A"), 1))
        p.drawEllipse(cx - ir, cy - ir, ir * 2, ir * 2)

        if self._mode == "rain":
            ds = int(ir * 0.38)
            dc = QColor("#2878D8") if pct > 0 else QColor("#1E2840")
            drop = QPainterPath()
            drop.moveTo(cx, cy - ds)
            drop.cubicTo(cx + ds*0.6, cy - ds*0.15, cx + ds*0.6, cy + ds*0.45, cx, cy + ds*0.55)
            drop.cubicTo(cx - ds*0.6, cy + ds*0.45, cx - ds*0.6, cy - ds*0.15, cx, cy - ds)
            dg = QRadialGradient(cx - ds*0.25, cy - ds*0.3, ds*0.9)
            dg.setColorAt(0, QColor("#80B8F0") if pct > 0 else QColor("#2A3848"))
            dg.setColorAt(0.6, dc); dg.setColorAt(1, dc.darker(160))
            p.setBrush(QBrush(dg)); p.setPen(QPen(dc.darker(200), 1))
            p.drawPath(drop)

        angle_r = math.radians(-225 + 270 * pct)
        needle_r = R - arc_w * 2 - int(R * 0.05)
        nx = cx + needle_r * math.cos(angle_r)
        ny = cy - needle_r * math.sin(angle_r)
        back_r = int(R * 0.14)
        bx = cx - back_r * math.cos(angle_r)
        by = cy + back_r * math.sin(angle_r)
        p.setPen(QPen(QColor("#FF202030"), max(5, arc_w - 2), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(int(bx), int(by), int(nx), int(ny))
        p.setPen(QPen(QColor("#FF2828"), max(2, arc_w // 4), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(int(bx), int(by), int(nx), int(ny))
        piv = max(5, arc_w // 2)
        gc = QRadialGradient(cx, cy, piv * 2)
        gc.setColorAt(0, QColor("#D0D0DC")); gc.setColorAt(0.5, QColor("#808090"))
        gc.setColorAt(1, QColor("#181820"))
        p.setBrush(QBrush(gc)); p.setPen(QPen(QColor("#A0A0B050"), 1))
        p.drawEllipse(cx - piv, cy - piv, piv * 2, piv * 2)
        p.setBrush(QBrush(QColor("#FF282880"))); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(cx - piv // 2, cy - piv // 2, piv, piv)

        font_sz = max(14, R // 5)
        p.setFont(QFont(FONT_MONO, font_sz, QFont.Weight.Bold))
        p.setPen(QPen(QColor("#F4F4FF")))
        if self._mode == "speed":
            txt = f"{self._val / 10:.0f}"
        else:
            txt = f"{self._val}"
        tw = p.fontMetrics().horizontalAdvance(txt)
        p.drawText(cx - tw // 2, cy + int(R * 0.25), txt)

        unit = "km/h" if self._mode == "speed" else "%"
        p.setFont(QFont(FONT_MONO, max(7, R // 10)))
        p.setPen(QPen(QColor("#60607A")))
        uw = p.fontMetrics().horizontalAdvance(unit)
        p.drawText(cx - uw // 2, cy + int(R * 0.40), unit)

        lbl = "VEHICLE SPEED" if self._mode == "speed" else "RAIN INTENSITY"
        p.setFont(QFont(FONT_MONO, max(6, R // 12), QFont.Weight.Bold))
        col_lbl = "#D08020" if self._mode == "speed" else "#2878D8"
        p.setPen(QPen(QColor(col_lbl)))
        lw = p.fontMetrics().horizontalAdvance(lbl)
        p.drawText(cx - lw // 2, cy - int(R * 0.55), lbl)


# ═══════════════════════════════════════════════════════════
#  AUDI COCKPIT WIDGET — VERSION SANS AUCUN ELEMENT JAUNE
# ═══════════════════════════════════════════════════════════
class _AudiCockpitWidget(QWidget):
    """Dashboard style Audi - design propre sans artefacts jaunes"""

    def __init__(self, veh_panel, parent=None):
        super().__init__(parent)
        self._vp = veh_panel
        self._rev_active = False
        self._sens_ok = True
        
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor("#08080C"))
        self.setPalette(pal)
        
        self._build()

    def _build(self):
        vp = self._vp
        main = QHBoxLayout(self)
        main.setContentsMargins(20, 14, 20, 14)
        main.setSpacing(10)

        # Jauge gauche
        self._gauge_spd = _AudiGaugeWidget("speed")
        self._gauge_spd.value_changed.connect(vp._on_spd)
        spd_col = QVBoxLayout()
        spd_col.setSpacing(4)
        spd_col.setContentsMargins(0, 0, 0, 0)
        spd_col.addWidget(self._gauge_spd, 1)
        spd_btns = QHBoxLayout()
        spd_btns.setSpacing(6)
        btn_a = QPushButton("▲  ACCEL")
        btn_a.setFixedHeight(24)
        btn_b = QPushButton("▼  BRAKE")
        btn_b.setFixedHeight(24)
        for btn, col_fg, col_bg in [(btn_a, "#E8A020", "#1E1200"), (btn_b, "#FF3020", "#1E0808")]:
            btn.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton{{background:{col_bg};color:{col_fg};"
                f"border:1px solid {col_fg}80;border-radius:4px;padding:0 8px;letter-spacing:1px;}}"
                f"QPushButton:hover{{background:{col_fg}25;border-color:{col_fg};}}"
                f"QPushButton:pressed{{background:{col_fg}45;}}")
        self._btn_accel = btn_a
        self._btn_brake = btn_b
        self._spd_dir = 0

        def _spd_start(d):
            self._spd_dir = d
            if not hasattr(self, '_spd_qtimer') or self._spd_qtimer is None:
                self._spd_qtimer = QTimer(self)
                self._spd_qtimer.setInterval(80)
                self._spd_qtimer.timeout.connect(_spd_tick)
            _spd_tick()
            self._spd_qtimer.start()

        def _spd_stop():
            if hasattr(self, '_spd_qtimer') and self._spd_qtimer:
                self._spd_qtimer.stop()
            self._spd_dir = 0

        def _spd_tick():
            v = self._gauge_spd.value()
            step = 100 if (v > 200 and self._spd_dir > 0) else 10
            nv = max(0, min(2000, v + self._spd_dir * step))
            self._gauge_spd.set_value(nv)
            vp._on_spd(nv)

        btn_a.pressed.connect(lambda: _spd_start(1))
        btn_a.released.connect(_spd_stop)
        btn_b.pressed.connect(lambda: _spd_start(-1))
        btn_b.released.connect(_spd_stop)
        spd_btns.addWidget(btn_a)
        spd_btns.addWidget(btn_b)
        spd_col.addLayout(spd_btns)
        spd_wrap = QWidget()
        spd_wrap.setStyleSheet("background:transparent;")
        spd_wrap.setLayout(spd_col)
        main.addWidget(spd_wrap, 3)

        # Panneau central
        center = QWidget()
        center.setStyleSheet(f"background:#0A0E0A;border:1.5px solid {KPIT_GREEN};border-radius:8px;")
        center.setFixedWidth(200)
        center_lay = QVBoxLayout(center)
        center_lay.setContentsMargins(12, 12, 12, 12)
        center_lay.setSpacing(8)

        ign_title = QLabel("IGNITION")
        ign_title.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
        ign_title.setStyleSheet(f"color:{KPIT_GREEN};letter-spacing:3px;background:transparent;")
        ign_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_lay.addWidget(ign_title)

        self._ign_disp = QLabel("○  OFF")
        self._ign_disp.setFont(QFont(FONT_MONO, 14, QFont.Weight.Bold))
        self._ign_disp.setStyleSheet("color:#404050;background:transparent;")
        self._ign_disp.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_lay.addWidget(self._ign_disp)

        ign_btns = QHBoxLayout()
        ign_btns.setSpacing(6)
        self._ign_btns = {}
        _ign_defs = [("OFF","#707078","#10101A"),("ACC","#E8A020","#1E1200"),("ON","#FF3020","#1E0808")]
        for state, fg, bg in _ign_defs:
            b = QPushButton(state)
            b.setFixedHeight(28)
            b.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, s=state: self._ign_select(s))
            ign_btns.addWidget(b)
            self._ign_btns[state] = b
        center_lay.addLayout(ign_btns)
        self._ign_select("OFF")

        self._rev_btn = QPushButton("DRIVE")
        self._rev_btn.setFixedHeight(32)
        self._rev_btn.setFont(QFont(FONT_MONO, 10, QFont.Weight.Bold))
        self._rev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rev_btn.setStyleSheet(
            f"QPushButton{{background:#0A120A;color:{KPIT_GREEN};"
            f"border:1px solid {KPIT_GREEN};border-radius:6px;letter-spacing:2px;}}"
            f"QPushButton:hover{{background:{KPIT_GREEN}18;border-color:{KPIT_GREEN};color:#FFFFFF;}}")

        def _toggle_rev_cb():
            self._rev_active = not self._rev_active
            if self._rev_active:
                self._rev_btn.setText("REVERSE")
                self._rev_btn.setStyleSheet(
                    "QPushButton{background:#200800;color:#FF8020;"
                    "border:2px solid #FF8020;border-radius:6px;letter-spacing:2px;}"
                    "QPushButton:hover{background:#FF802018;}")
            else:
                self._rev_btn.setText("DRIVE")
                self._rev_btn.setStyleSheet(
                    f"QPushButton{{background:#0A120A;color:{KPIT_GREEN};"
                    f"border:1px solid {KPIT_GREEN};border-radius:6px;letter-spacing:2px;}}"
                    f"QPushButton:hover{{background:{KPIT_GREEN}18;border-color:{KPIT_GREEN};color:#FFFFFF;}}")
            if vp and hasattr(vp, '_toggle_rev'):
                vp._toggle_rev()

        self._rev_btn.clicked.connect(_toggle_rev_cb)
        center_lay.addWidget(self._rev_btn)

        sens_title = QLabel("SENSOR STATUS")
        sens_title.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
        sens_title.setStyleSheet(f"color:{KPIT_GREEN};letter-spacing:2px;background:transparent;")
        sens_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_lay.addWidget(sens_title)

        self._sens_badge = QLabel("●  SENSOR OK")
        self._sens_badge.setFont(QFont(FONT_MONO, 10, QFont.Weight.Bold))
        self._sens_badge.setStyleSheet("color:#30B030;background:transparent;")
        self._sens_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_lay.addWidget(self._sens_badge)

        self._sens_btn = QPushButton("SIMULATE ERROR")
        self._sens_btn.setFixedHeight(28)
        self._sens_btn.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
        self._sens_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sens_btn.setStyleSheet(
            f"QPushButton{{background:#0A0E0A;color:#C03030;"
            f"border:1px solid {KPIT_GREEN}80;border-radius:5px;}}"
            f"QPushButton:hover{{background:#C0303018;border-color:{KPIT_GREEN};color:#E04040;}}")

        def _toggle_sens_cb():
            if vp:
                vp._toggle_sens()
            self._sens_ok = vp._sensor_ok if vp else not self._sens_ok
            if self._sens_ok:
                self._sens_badge.setText("●  SENSOR OK")
                self._sens_badge.setStyleSheet("color:#30B030;background:transparent;")
                self._sens_btn.setText("SIMULATE ERROR")
                self._sens_btn.setStyleSheet(
                    f"QPushButton{{background:#0A0E0A;color:#C03030;"
                    f"border:1px solid {KPIT_GREEN}80;border-radius:5px;}}"
                    f"QPushButton:hover{{background:#C0303018;border-color:{KPIT_GREEN};color:#E04040;}}")
            else:
                self._sens_badge.setText("ERROR")
                self._sens_badge.setStyleSheet("color:#FF3030;background:transparent;")
                self._sens_btn.setText("✓  RESTORE OK")
                self._sens_btn.setStyleSheet(
                    f"QPushButton{{background:#081808;color:#40A040;"
                    f"border:1px solid {KPIT_GREEN};border-radius:5px;}}"
                    f"QPushButton:hover{{background:#40A04018;border-color:{KPIT_GREEN};color:#50C050;}}")

        self._sens_btn.clicked.connect(_toggle_sens_cb)
        center_lay.addWidget(self._sens_btn)
        center_lay.addStretch()
        main.addWidget(center, 1)

        # Jauge droite
        self._gauge_rain = _AudiGaugeWidget("rain")
        self._gauge_rain.value_changed.connect(vp._on_rain)
        rain_col = QVBoxLayout()
        rain_col.setSpacing(4)
        rain_col.setContentsMargins(0, 0, 0, 0)
        rain_col.addWidget(self._gauge_rain, 1)
        rain_btns = QHBoxLayout()
        rain_btns.setSpacing(6)
        btn_p = QPushButton("＋  RAIN")
        btn_p.setFixedHeight(24)
        btn_m = QPushButton("－  RAIN")
        btn_m.setFixedHeight(24)
        for btn in [btn_p, btn_m]:
            btn.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton{background:#080E18;color:#2878D8;"
                "border:1px solid #1848A0;border-radius:4px;padding:0 8px;letter-spacing:1px;}"
                "QPushButton:hover{background:#2878D820;border-color:#2878D8;}"
                "QPushButton:pressed{background:#2878D840;}")

        def _rain_start(d):
            self._rain_dir = d
            if not hasattr(self, '_rain_qtimer') or self._rain_qtimer is None:
                self._rain_qtimer = QTimer(self)
                self._rain_qtimer.setInterval(120)
                self._rain_qtimer.timeout.connect(_rain_tick)
            _rain_tick()
            self._rain_qtimer.start()

        def _rain_stop():
            if hasattr(self, '_rain_qtimer') and self._rain_qtimer:
                self._rain_qtimer.stop()
            self._rain_dir = 0

        def _rain_tick():
            v = self._gauge_rain.value()
            nv = max(0, min(100, v + self._rain_dir * 5))
            self._gauge_rain.set_value(nv)
            vp._on_rain(nv)

        self._rain_dir = 0
        btn_p.pressed.connect(lambda: _rain_start(1))
        btn_p.released.connect(_rain_stop)
        btn_m.pressed.connect(lambda: _rain_start(-1))
        btn_m.released.connect(_rain_stop)
        rain_btns.addWidget(btn_p)
        rain_btns.addWidget(btn_m)
        rain_col.addLayout(rain_btns)
        rain_wrap = QWidget()
        rain_wrap.setStyleSheet("background:transparent;")
        rain_wrap.setLayout(rain_col)
        main.addWidget(rain_wrap, 3)

    def _ign_select(self, state: str):
        _defs = [("OFF","#707078","#10101A"),("ACC","#E8A020","#1E1200"),("ON","#FF3020","#1E0808")]
        _cols = {s:(fg,bg) for s,fg,bg in _defs}
        _disp = {"OFF": ("○  OFF","#404050"), "ACC": ("◑  ACC","#E8A020"), "ON": ("●  ON","#FF3020")}
        txt, col = _disp.get(state, ("○  OFF","#404050"))
        self._ign_disp.setText(txt)
        self._ign_disp.setStyleSheet(f"color:{col};font-weight:bold;")
        for s, btn in self._ign_btns.items():
            fg, bg = _cols[s]
            if s == state:
                btn.setStyleSheet(
                    f"QPushButton{{background:{bg};color:{fg};"
                    f"border:1.5px solid {fg};border-radius:4px;padding:4px 8px;font-weight:bold;}}")
            else:
                btn.setStyleSheet(
                    f"QPushButton{{background:#0A0E0A;color:#303040;"
                    f"border:1px solid {KPIT_GREEN}40;border-radius:4px;padding:4px 8px;}}"
                    f"QPushButton:hover{{background:{KPIT_GREEN}10;color:#505060;border-color:{KPIT_GREEN}80;}}")
        vp = self._vp
        if vp:
            vp.ign._sel(state)


# ═══════════════════════════════════════════════════════════
#  SLIDING DASHBOARD CONTAINER
#  — glisser vers le bas = cacher, vers le haut = afficher —
# ═══════════════════════════════════════════════════════════
class _SlidingDashboard(QWidget):
    """
    Conteneur animé pour _AudiCockpitWidget.
    Un handle 'drag' en haut du dashboard permet de le faire glisser :
      • glisser vers le bas (ou cliquer)  -> cache le dashboard, revele la grille
      • glisser vers le haut (ou cliquer) -> affiche le dashboard
    La hauteur est animee via QPropertyAnimation.
    """

    DASH_H   = 260   # hauteur dashboard visible
    HANDLE_H = 22    # hauteur de la bande-poignee

    visibility_changed = Signal(bool)

    def __init__(self, cockpit_widget: QWidget, parent=None):
        super().__init__(parent)
        self._expanded   = True
        self._drag_start = None
        self._drag_orig  = None
        self._animating  = False
        self.setFixedHeight(self.DASH_H + self.HANDLE_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Handle / barre de glissement — discret
        self._handle = QWidget()
        self._handle.setFixedHeight(self.HANDLE_H)
        self._handle.setCursor(Qt.CursorShape.SizeVerCursor)
        self._handle.setStyleSheet("background: transparent; border-top: 1px solid #1A2A1A;")

        handle_lay = QHBoxLayout(self._handle)
        handle_lay.setContentsMargins(0, 0, 0, 0)
        handle_lay.setSpacing(0)

        self._icon_lbl = QLabel("▼")
        self._icon_lbl.setFont(QFont(FONT_MONO, 6))
        self._icon_lbl.setStyleSheet(f"color: {KPIT_GREEN}55; background: transparent;")
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        handle_lay.addWidget(self._icon_lbl, 1, Qt.AlignmentFlag.AlignCenter)

        outer.addWidget(self._handle)

        # Cadre vert KPIT autour du cockpit
        self._frame = QFrame()
        self._frame.setStyleSheet(
            f"QFrame {{ border: 2px solid {KPIT_GREEN}; background: transparent; }}"
        )
        frame_lay = QVBoxLayout(self._frame)
        frame_lay.setContentsMargins(0, 0, 0, 0)
        frame_lay.setSpacing(0)

        self._cockpit = cockpit_widget
        self._cockpit.setParent(self._frame)
        frame_lay.addWidget(self._cockpit)

        outer.addWidget(self._frame)

        self._anim = QPropertyAnimation(self, b"maximumHeight")
        self._anim.setDuration(320)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._on_anim_done)

        self._handle.mousePressEvent   = self._hdl_press
        self._handle.mouseMoveEvent    = self._hdl_move
        self._handle.mouseReleaseEvent = self._hdl_release

    def _target_h(self, expanded: bool) -> int:
        return (self.DASH_H + self.HANDLE_H) if expanded else self.HANDLE_H

    def _animate_to(self, expanded: bool):
        if self._animating:
            return
        self._animating = True
        self._expanded  = expanded
        self._anim.stop()
        self._anim.setStartValue(self.maximumHeight())
        self._anim.setEndValue(self._target_h(expanded))
        self._update_icon(expanded)
        self._anim.start()
        self.visibility_changed.emit(expanded)

    def _on_anim_done(self):
        self._animating = False
        self.setFixedHeight(self._target_h(self._expanded))

    def _update_icon(self, expanded: bool):
        if expanded:
            self._icon_lbl.setText("▼")
        else:
            self._icon_lbl.setText("▲")

    def _hdl_press(self, ev: QMouseEvent):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag_start = ev.globalPosition().y()
            self._drag_orig  = self.height()

    def _hdl_move(self, ev: QMouseEvent):
        if self._drag_start is None or self._animating:
            return
        dy = ev.globalPosition().y() - self._drag_start
        new_h = max(self.HANDLE_H,
                    min(self.DASH_H + self.HANDLE_H,
                        self._drag_orig - int(dy)))
        self.setFixedHeight(new_h)

    def _hdl_release(self, ev: QMouseEvent):
        if self._drag_start is None:
            return
        dy = ev.globalPosition().y() - self._drag_start
        if abs(dy) < 8:
            self._animate_to(not self._expanded)
        elif dy > 40:
            self._animate_to(False)
        elif dy < -40:
            self._animate_to(True)
        else:
            self._animate_to(self._expanded)
        self._drag_start = None
        self._drag_orig  = None

    def is_expanded(self) -> bool:
        return self._expanded

    def toggle(self):
        self._animate_to(not self._expanded)


# ═══════════════════════════════════════════════════════════
#  MOTOR PUMP FREE PAGE
# ═══════════════════════════════════════════════════════════
class MotorPumpFreePage(QWidget):
    def __init__(self, motor_panel: QWidget, car_xray: QWidget,
                 pump_panel: QWidget, signal_hub: SignalHub,
                 veh_panel=None, parent=None):
        super().__init__(parent)
        self._hub      = signal_hub
        self._car      = car_xray
        self._veh_panel = veh_panel
        self.setStyleSheet("background:#08080C;")
        self._build()
        self._toolbar.auto_load(DEFAULT_LAYOUT_PATH)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        left_panel = QWidget()
        left_panel.setFixedWidth(240)
        left_panel.setStyleSheet("background:#111D11;")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        hdr = QWidget()
        hdr.setFixedHeight(42)
        hdr.setStyleSheet(f"background:#0A1208;border-bottom:2px solid {KPIT_GREEN};")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(14, 0, 10, 0)
        lbl_hdr = QLabel("INSTRUMENTS")
        lbl_hdr.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
        lbl_hdr.setStyleSheet(f"color:{KPIT_GREEN};background:transparent;letter-spacing:3px;")
        hdr_lay.addWidget(lbl_hdr)
        left_layout.addWidget(hdr)

        self._tree = InstrumentTree()
        left_layout.addWidget(self._tree, 1)
        body.addWidget(left_panel)

        self._canvas = CircularDropCanvas(self._car, self._hub)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._canvas)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background:{W_BG}; border:none; }}
            QScrollArea > QWidget > QWidget {{ background:{W_BG}; }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(141,198,63,0.35);
                border-radius: 4px;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: rgba(141,198,63,0.65);
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            QScrollBar:horizontal {{
                background: transparent;
                height: 8px;
                margin: 0px;
            }}
            QScrollBar::handle:horizontal {{
                background: rgba(141,198,63,0.35);
                border-radius: 4px;
                min-width: 30px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: rgba(141,198,63,0.65);
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                width: 0px;
            }}
        """)
        body.addWidget(scroll, 1)

        root.addLayout(body, 1)

        if self._veh_panel is not None:
            bottom = _AudiCockpitWidget(self._veh_panel)
            self._sliding_dash = _SlidingDashboard(bottom)
            root.addWidget(self._sliding_dash)

        self._toolbar = LayoutToolbar(self._canvas, self)
        root.addWidget(self._toolbar)