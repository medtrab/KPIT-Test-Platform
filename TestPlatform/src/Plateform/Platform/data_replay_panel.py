"""
data_replay_panel.py  —  Data Record & Scenario Replay  (merged panel)
=======================================================================
Fusion de datasave_panel.py et scenario_replay_panel.py en un seul onglet
professionnel style ControlDesk / Scalexio.

FIXES v2 (split horizontal) :
  - Splitter DATA SAVE | REPLAY maintenant HORIZONTAL (côte à côte)
  - Toolbar Replay découpée en 2 lignes pour éviter le chevauchement :
      Ligne 1 : Load CSV | ▶ ⏸ ■ | × speed | file badge
      Ligne 2 : FILTER  MTR LIN CAN PMP | ⚡ Virtual ECU
  - Colonnes des deux tables réduites (adaptées à la demi-largeur)
  - Polices et hauteurs de lignes allégées pour gagner de la place
  - Splitter interne du panneau Replay repassé VERTICAL (Timeline / Log)
"""

from __future__ import annotations

import csv
import datetime
import os
import time
from collections import deque
from dataclasses import dataclass
from typing import List, Optional

from PySide6.QtCore    import Qt, QTimer, Signal, QObject
from PySide6.QtGui     import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDoubleSpinBox, QFileDialog,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QProgressBar,
    QPushButton, QSizePolicy, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget, QComboBox,
)

from constants import (
    FONT_UI, FONT_MONO,
    W_BG, W_PANEL, W_PANEL2, W_PANEL3,
    W_BORDER, W_BORDER2, W_TOOLBAR, W_TITLEBAR,
    W_TEXT, W_TEXT_DIM, W_TEXT_HDR,
    A_GREEN, A_TEAL, A_ORANGE, A_RED, A_AMBER,
    KPIT_GREEN, KPIT_GREEN_GLOW, KPIT_GREEN_DIM,
)
from widgets_base import StatusLed, _lbl, _hsep

try:
    from mdf_exporter import MDFExporter
    _MDF_AVAILABLE = True
except ImportError:
    _MDF_AVAILABLE = False

try:
    from asammdf import MDF as _AsamMDF
    _ASAMMDF_AVAILABLE = True
except ImportError:
    _ASAMMDF_AVAILABLE = False


# ══════════════════════════════════════════════════════════════
#  PALETTE  — DataDesk · thème KPIT harmonisé
# ══════════════════════════════════════════════════════════════
# Backgrounds — vert KPIT clair, cohérent avec W_PANEL / W_PANEL2
_BG_BASE    = "#FFFFFF"       # fond global  = W_PANEL2
_BG_PANEL   = "#FFFFFF"       # panneaux     = W_PANEL
_BG_CARD    = "#F8FAFC"       # cartes       = W_PANEL3
_BG_ROW_ALT = "#D8F0C8"       # alternance table
_BG_INPUT   = "#C8EAB8"       # champs de saisie
_BG_TOOLBAR = "#F1F5F9"       # barre statut = W_PANEL2

# Accent vert KPIT — remplace le cyan
_CY         = "#3A7A10"       # vert KPIT foncé (lisible sur fond clair)
_CY_DIM     = "rgba(141,198,63,0.18)"
_CY_GLOW    = "rgba(141,198,63,0.35)"
_CY_BORDER  = "rgba(141,198,63,0.30)"

# Accent vert action OK — KPIT signature
_GN         = "#8DC63F"
_GN_DIM     = "rgba(141,198,63,0.20)"
_GN_GLOW    = "rgba(141,198,63,0.35)"

# Borders / separators — vert KPIT semi-transparent
_BR_MAIN    = "rgba(141,198,63,0.35)"
_BR_DIM     = "rgba(58,122,16,0.18)"

# Text — vert olive foncé lisible sur fond clair
_TX_PRI     = "#1A2A0A"       # texte principal
_TX_SEC     = "#4A6A2A"       # texte secondaire  = W_TEXT_DIM
_TX_DIM     = "#7A9A5A"       # texte discret
_TX_HDR     = "#2A4A10"       # headers colonnes

# States — inchangés (rouge enregistrement, amber alerte)
_REC_RED    = "#C0392B"
_REC_RED_DIM = "rgba(192,57,43,0.15)"
_WARN_AMB   = "#D35400"
_WARN_DIM   = "rgba(211,84,0,0.15)"

# KPIT compat
_KG         = _GN
_KG_DIM     = _GN_DIM
_KG_GLOW    = _CY_GLOW

_BORDER_HI  = _BR_MAIN

_FONT_HMI   = FONT_MONO   # "Consolas"  — aligns with platform-wide monospace font
_FONT_UI    = FONT_UI     # "Segoe UI"  — aligns with platform-wide UI font
_FONT_MONO  = FONT_MONO   # "Consolas"  — aligns with platform-wide monospace font

_SRC_TAG   = {"motor": "MTR", "lin": "LIN", "can": "CAN", "pump": "PMP"}
_SRC_LABEL = {"motor": "MOTOR", "lin": "LIN", "can": "CAN", "pump": "PUMP"}

MAX_BUFFER   = 100_000
_PREVIEW_MAX = 500


# ══════════════════════════════════════════════════════════════
#  HELPERS UI
# ══════════════════════════════════════════════════════════════
def _pill(text: str, h: int = 32, w: int = 0, accent: bool = False) -> "QPushButton":
    from PySide6.QtWidgets import QPushButton
    b = QPushButton(text)
    if w:
        b.setFixedWidth(w)
    b.setFixedHeight(h)
    b.setFont(QFont(_FONT_HMI, 8, QFont.Weight.Bold))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if accent:
        b.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #9ED648, stop:1 {_GN});
                color: #FFFFFF;
                border: 1.5px solid #1A1A1A; border-radius: 5px;
                padding: 0 16px; letter-spacing: 1px;
                font-weight: 900;
            }}
            QPushButton:hover  {{
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #A8E050, stop:1 #7AB830);
            }}
            QPushButton:pressed{{ background: #6A9A20; }}
            QPushButton:disabled{{
                background: {_BG_INPUT}; color: {_TX_DIM};
                border: 1.5px solid #1A1A1A;
            }}
        """)
    else:
        b.setStyleSheet(f"""
            QPushButton {{
                background: {_BG_CARD}; color: {_CY};
                border: 1.5px solid #1A1A1A; border-radius: 5px;
                padding: 0 14px; letter-spacing: 0.8px;
            }}
            QPushButton:hover  {{
                background: {_CY_DIM}; border-color: #1A1A1A;
                color: #1A3A08;
            }}
            QPushButton:pressed{{ background: {_GN_DIM}; }}
            QPushButton:checked{{
                background: {_GN_DIM}; border: 1.5px solid #1A1A1A;
                color: #1A3A08; font-weight: 900;
            }}
            QPushButton:disabled{{
                color: {_TX_DIM}; border: 1.5px solid #1A1A1A;
                background: {_BG_INPUT};
            }}
        """)
    return b


def _rec_pill(text: str, h: int = 32, w: int = 0) -> "QPushButton":
    from PySide6.QtWidgets import QPushButton
    b = QPushButton(text)
    if w:
        b.setFixedWidth(w)
    b.setFixedHeight(h)
    b.setFont(QFont(_FONT_HMI, 8, QFont.Weight.Bold))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet(f"""
        QPushButton {{
            background: {_REC_RED_DIM}; color: {_REC_RED};
            border: 1.5px solid #1A1A1A; border-radius: 5px;
            padding: 0 14px; letter-spacing: 1px;
        }}
        QPushButton:hover  {{
            background: rgba(255,59,59,0.22); border-color: #1A1A1A;
            color: #FFAAAA;
        }}
        QPushButton:pressed{{ background: rgba(255,59,59,0.35); }}
        QPushButton:disabled{{
            color: {_TX_DIM}; border: 1.5px solid #1A1A1A;
            background: {_BG_INPUT};
        }}
    """)
    return b


def _vsep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.VLine)
    f.setFixedWidth(1)
    f.setStyleSheet(f"background: {_BR_DIM}; border: none;")
    return f


def _hsep_kpit() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFixedHeight(1)
    f.setStyleSheet(f"background: {_BR_DIM}; border: none;")
    return f


def _tag_badge(tag: str) -> QLabel:
    lbl = QLabel(f"{tag}")
    lbl.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
    lbl.setStyleSheet(
        f"color: {_CY}; background: {_CY_DIM};"
        f"border: 1px solid {_CY_BORDER}; border-radius: 3px;"
        f"padding: 1px 5px; letter-spacing: 1px;"
    )
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return lbl


def _status_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(QFont(_FONT_HMI, 8, QFont.Weight.Bold))
    lbl.setStyleSheet(
        f"color: {_CY}; background: {_CY_DIM};"
        f"border: 1px solid {_CY_BORDER}; border-radius: 3px;"
        f"padding: 2px 8px; letter-spacing: 0.5px;"
    )
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return lbl


class _Section:
    def __init__(self, title: str, tag: str = ""):
        self.frame = QWidget()
        self.frame.setObjectName("sec")
        self.frame.setStyleSheet(f"""
            QWidget#sec {{
                background: {_BG_PANEL};
                border: 1px solid {_BR_MAIN};
                border-left: 3px solid {_CY};
                border-radius: 6px;
            }}
        """)
        vl = QVBoxLayout(self.frame)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        hdr = QWidget()
        hdr.setObjectName("hdr")
        hdr.setFixedHeight(32)
        hdr.setStyleSheet(f"""
            QWidget#hdr {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #FFFFFF, stop:1 {_BG_PANEL});
                border-radius: 5px 5px 0 0;
                border-bottom: 1px solid {_BR_DIM};
            }}
        """)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(14, 0, 14, 0)
        if tag:
            t = QLabel(tag)
            t.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
            t.setStyleSheet(
                f"color: {_CY}; background: {_CY_DIM};"
                f"border: 1px solid {_CY_BORDER}; border-radius: 3px;"
                f"padding: 0px 5px; letter-spacing: 1px;")
            hl.addWidget(t)
            hl.addSpacing(8)
        lb = QLabel(title)
        lb.setFont(QFont(_FONT_HMI, 9, QFont.Weight.Bold))
        lb.setStyleSheet(
            f"color: {_TX_PRI}; letter-spacing: 2px; background: transparent;")
        hl.addWidget(lb)
        hl.addStretch()
        self._right_lbl = QLabel("")
        self._right_lbl.setFont(QFont(_FONT_HMI, 7))
        self._right_lbl.setStyleSheet(
            f"color: {_TX_SEC}; background: transparent;")
        hl.addWidget(self._right_lbl)
        vl.addWidget(hdr)

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        self.layout = QVBoxLayout(body)
        self.layout.setContentsMargins(12, 10, 12, 12)
        self.layout.setSpacing(8)
        vl.addWidget(body)

    def set_header_right(self, text: str, color: str = _TX_SEC):
        self._right_lbl.setText(text)
        self._right_lbl.setStyleSheet(
            f"color: {color}; background: transparent;")


# ══════════════════════════════════════════════════════════════
#  DATA RECORDER
# ══════════════════════════════════════════════════════════════
_MOTOR_COLS = [
    "timestamp", "source",
    "state", "front", "rear", "speed",
    "current", "rest_contact", "fault",
    "crs_wiper_op", "ignition", "vehicle_speed", "rain_intensity",
    "front_blade_cycles",
]
_LIN_COLS = [
    "timestamp", "source",
    "lin_type", "pid", "op", "wiper_op",
    "front_motor_on", "rest_contact_raw", "fault", "raw",
]
_CAN_COLS = [
    "timestamp", "source",
    "can_id", "direction", "dlc", "payload",
    "wiper_cmd", "wiper_status", "wiper_ack",
    "vehicle_status", "rain_sensor",
]
_PUMP_COLS = [
    "timestamp", "source",
    "flow", "pressure", "current",
    "state", "direction", "active", "timeout_elapsed",
]


class DataRecorder(QObject):
    row_added = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buf:     deque = deque(maxlen=MAX_BUFFER)
        self._active   = False
        self._t0: float | None = None
        self._filters: set[str] = {"motor", "lin", "can", "pump"}

    def start(self):
        self._active = True
        self._t0 = time.monotonic()

    def stop(self):
        self._active = False

    def clear(self):
        self._buf.clear()

    def is_active(self) -> bool:
        return self._active

    def set_filter(self, src: str, enabled: bool):
        if enabled:
            self._filters.add(src)
        else:
            self._filters.discard(src)

    def row_count(self) -> int:
        return len(self._buf)

    def elapsed(self) -> float:
        if self._t0 is None:
            return 0.0
        return time.monotonic() - self._t0

    def get_rows(self) -> list[dict]:
        return list(self._buf)

    def _push(self, row: dict):
        if not self._active:
            return
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        row["timestamp"] = ts
        self._buf.append(row)
        self.row_added.emit(row)

    def push_motor(self, data: dict):
        if "motor" not in self._filters:
            return
        def _d(v):
            if isinstance(v, dict):
                return v
            try:
                import json
                return json.loads(v) if isinstance(v, str) else {}
            except Exception:
                return {}

        f = _d(data.get("front", {}))
        r = _d(data.get("rear",  {}))

        if isinstance(data.get("front"), str):
            row = {
                "source":             "motor",
                "state":              data.get("state", ""),
                "front":              data.get("front", ""),
                "rear":               data.get("rear", ""),
                "speed":              data.get("speed", ""),
                "current":            data.get("current", ""),
                "rest_contact":       data.get("rest", ""),
                "fault":              data.get("fault", ""),
                "crs_wiper_op":       data.get("crs_wiper_op", ""),
                "ignition":           data.get("ignition_status", ""),
                "vehicle_speed":      data.get("vehicle_speed", ""),
                "rain_intensity":     data.get("rain_intensity", ""),
                "front_blade_cycles": data.get("front_blade_cycles", ""),
            }
        else:
            row = {
                "source":             "motor",
                "state":              data.get("state", ""),
                "front":              "ON" if f.get("enable", 0) else "OFF",
                "rear":               "ON" if r.get("enable", 0) else "OFF",
                "speed":              "Speed2" if f.get("speed", 0) else "Speed1",
                "current":            round(
                    float(f.get("motor_current", 0)) + float(r.get("motor_current", 0)), 4),
                "rest_contact":       "PARKED" if f.get("rest_contact", 0) else "MOVING",
                "fault":              bool(f.get("fault_status", 0)) or bool(r.get("fault_status", 0)),
                "crs_wiper_op":       data.get("crs_wiper_op", ""),
                "ignition":           data.get("ignition_status", ""),
                "vehicle_speed":      data.get("vehicle_speed", ""),
                "rain_intensity":     data.get("rain_intensity", ""),
                "front_blade_cycles": data.get("front_blade_cycles", ""),
            }
        self._push(row)

    def push_lin(self, ev: dict):
        if "lin" not in self._filters:
            return
        row = {
            "source":           "lin",
            "lin_type":         ev.get("type", ""),
            "pid":              ev.get("pid", ""),
            "op":               ev.get("op", ""),
            "wiper_op":         ev.get("wiper_op", ""),
            "front_motor_on":   ev.get("front_motor_on", ""),
            "rest_contact_raw": ev.get("rest_contact_raw", ""),
            "fault":            ev.get("fault", ""),
            "raw":              ev.get("raw", ""),
        }
        self._push(row)

    def push_can(self, ev: dict):
        if "can" not in self._filters:
            return
        row = {
            "source":         "can",
            "can_id":         ev.get("can_id", ev.get("id", "")),
            "direction":      ev.get("direction", ev.get("dir", "")),
            "dlc":            ev.get("dlc", ""),
            "payload":        ev.get("payload", ev.get("data", "")),
            "wiper_cmd":      ev.get("wiper_cmd", ""),
            "wiper_status":   ev.get("wiper_status", ""),
            "wiper_ack":      ev.get("wiper_ack", ""),
            "vehicle_status": ev.get("vehicle_status", ""),
            "rain_sensor":    ev.get("rain_sensor", ""),
        }
        self._push(row)

    def push_pump(self, data: dict):
        if "pump" not in self._filters:
            return
        row = {
            "source":          "pump",
            "flow":            data.get("flow", ""),
            "pressure":        data.get("pressure", ""),
            "current":         data.get("pump_current", data.get("current", "")),
            "state":           data.get("state", ""),
            "direction":       data.get("direction", ""),
            "active":          data.get("active", ""),
            "timeout_elapsed": data.get("timeout_elapsed", ""),
        }
        self._push(row)

    def export_csv(self, path: str, source_filter: str = "all") -> int:
        rows = list(self._buf)
        if source_filter != "all":
            rows = [r for r in rows if r.get("source") == source_filter]
        if not rows:
            return 0
        fieldnames = ["timestamp", "source"]
        seen = set(fieldnames)
        for r in rows:
            for k in r:
                if k not in seen:
                    fieldnames.append(k)
                    seen.add(k)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        return len(rows)

    def export_per_source(self, folder: str, base_name: str) -> dict[str, int]:
        results = {}
        col_map = {"motor": _MOTOR_COLS, "lin": _LIN_COLS, "can": _CAN_COLS, "pump": _PUMP_COLS}
        for src in ("motor", "lin", "can", "pump"):
            rows = [r for r in self._buf if r.get("source") == src]
            if not rows:
                continue
            path = os.path.join(folder, f"{base_name}_{src}.csv")
            cols = list(col_map[src])
            seen_keys = set(cols)
            for r in rows:
                for k in r:
                    if k not in seen_keys:
                        cols.append(k)
                        seen_keys.add(k)
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            results[src] = len(rows)
        return results


# ══════════════════════════════════════════════════════════════
#  SCENARIO ENGINE
# ══════════════════════════════════════════════════════════════
@dataclass
class ScenarioRow:
    t_abs:   float
    t_rel:   float
    source:  str
    raw:     dict
    summary: str = ""


class CsvScenarioLoader:
    @staticmethod
    def load(path: str, sources: set | None = None) -> tuple[list[ScenarioRow], str]:
        if not os.path.isfile(path):
            return [], f"Fichier introuvable : {path}"
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                raw_rows = list(csv.DictReader(f))
        except Exception as exc:
            return [], f"Erreur lecture CSV : {exc}"
        if not raw_rows:
            return [], "CSV vide."

        t0: Optional[float] = None
        for r in raw_rows:
            t = _parse_ts(r.get("timestamp", ""))
            if t:
                t0 = t
                break
        if t0 is None:
            return [], "Aucun timestamp valide."

        rows: list[ScenarioRow] = []
        for r in raw_rows:
            src = r.get("source", "").strip().lower()
            if sources and src not in sources:
                continue
            t_abs = _parse_ts(r.get("timestamp", ""))
            if not t_abs:
                continue
            rows.append(ScenarioRow(
                t_abs=t_abs, t_rel=t_abs - t0,
                source=src, raw=dict(r),
                summary=CsvScenarioLoader._summarise(src, r),
            ))
        rows.sort(key=lambda x: x.t_rel)
        return rows, ""

    @staticmethod
    def _summarise(src: str, r: dict) -> str:
        if src == "motor":
            parts = []
            for k in ("crs_wiper_op", "ignition", "vehicle_speed", "rain_intensity", "state"):
                if r.get(k):
                    parts.append(f"{k}={r[k]}")
            return "  ".join(parts) or r.get("state", "—")
        if src == "lin":
            return f"{r.get('lin_type','')}  op={r.get('op','')} wiper={r.get('wiper_op','')}"
        if src == "can":
            return f"{r.get('can_id','')} {r.get('direction','')} {r.get('payload','')}"
        if src == "pump":
            return f"pump={r.get('pump_state','')} cur={r.get('pump_current','')} A"
        return str(r)


# ═══════════════════════════════════════════════════════════
#  MDF4 SCENARIO LOADER
# ═══════════════════════════════════════════════════════════
class Mdf4ScenarioLoader:
    """
    Charge un fichier MDF4 (.mf4) produit par MDFExporter et le convertit
    en ScenarioRow[] compatible avec le moteur de replay CSV.
    Strategie : iterer groupe par groupe (MOTOR / LIN / CAN / PUMP),
    chaque groupe produisant des rows avec la bonne source.
    Necessite : pip install asammdf
    """

    # Nom du groupe MDF → cle source ScenarioRow
    _GROUP_TO_SRC = {
        "MOTOR": "motor",
        "LIN":   "lin",
        "CAN":   "can",
        "PUMP":  "pump",
    }

    # Canaux qui signalent l'appartenance d'un groupe anonyme
    # IMPORTANT : les noms doivent correspondre exactement à ceux produits
    # par mdf_exporter.py (ex. lin_front_motor_on, lin_rest_contact_raw,
    # can_payload_B0 avec majuscule, motor_fault).
    _SRC_HINTS = {
        "motor": {"motor_state_txt", "motor_state_int", "front_motor_on",
                  "rear_motor_on", "motor_current", "crs_wiper_op", "motor_fault"},
        "lin":   {"lin_pid", "lin_type", "lin_wiper_op",
                  "lin_front_motor_on", "lin_rest_contact_raw", "lin_front_on",
                  "lin_alive", "lin_bcm_state"},
        "can":   {"can_id", "can_direction", "can_dlc",
                  "can_payload_B0", "can_payload_b0"},
        "pump":  {"pump_flow", "pump_pressure", "pump_current",
                  "pump_active", "pump_state"},
    }

    @staticmethod
    def load(path: str, sources: set | None = None) -> tuple[list, str]:
        if not _ASAMMDF_AVAILABLE:
            return [], "asammdf non installe — pip install asammdf"
        if not os.path.isfile(path):
            return [], f"Fichier introuvable : {path}"
        try:
            mdf = _AsamMDF(path)
        except Exception as exc:
            return [], f"Erreur ouverture MDF4 : {exc}"

        rows: list = []
        t0: float | None = None

        # ── Itérer groupe par groupe via iter_groups() ────────────────
        # On utilise iter_groups() (toujours disponible dans asammdf) plutôt que
        # to_dataframe(group=N) qui n'existe pas dans toutes les versions.
        try:
            group_iter = list(mdf.iter_groups())
        except Exception:
            # Fallback ultime si iter_groups non disponible non plus
            return Mdf4ScenarioLoader._load_flat(mdf, path, sources)

        for grp_idx, df in enumerate(group_iter):
            if df is None or len(df) == 0:
                continue

            # Nom du groupe → source
            grp_name = ""
            try:
                grp_name = (mdf.groups[grp_idx].channel_group.acq_name or "").upper()
            except Exception:
                pass
            src = Mdf4ScenarioLoader._GROUP_TO_SRC.get(grp_name)

            # Normaliser les noms de colonnes
            df.columns = [c.split(".")[-1] if "." in c else c for c in df.columns]
            df.columns = [c.split(":")[0]  if ":" in c else c for c in df.columns]
            cols = list(df.columns)

            # Deviner la source via les canaux si nom de groupe inconnu
            if src is None:
                src = Mdf4ScenarioLoader._detect_source(cols)
            if src is None:
                continue
            if sources and src not in sources:
                continue

            timestamps = df.index.to_numpy(dtype=float)
            if t0 is None and len(timestamps):
                t0 = float(timestamps[0])

            for i, ts in enumerate(timestamps):
                row_dict: dict = {"source": src, "timestamp": str(ts)}
                for col in cols:
                    try:
                        v = df.iloc[i][col]
                        if isinstance(v, (bytes, bytearray)):
                            row_dict[col] = v.decode("utf-8", errors="replace").strip("\x00")
                        elif v is not None:
                            row_dict[col] = str(v)
                        else:
                            row_dict[col] = ""
                    except Exception:
                        pass
                Mdf4ScenarioLoader._remap(src, row_dict)
                t_abs = float(ts)
                t_rel = t_abs - (t0 or t_abs)
                rows.append(ScenarioRow(
                    t_abs=t_abs, t_rel=t_rel,
                    source=src, raw=row_dict,
                    summary=Mdf4ScenarioLoader._summarise(src, row_dict),
                ))

        if not rows:
            return Mdf4ScenarioLoader._load_flat(mdf, path, sources)

        rows.sort(key=lambda x: x.t_rel)
        return rows, ""

    @staticmethod
    def _load_flat(mdf, path: str, sources: set | None) -> tuple[list, str]:
        """Fallback : exporter tout en un seul DataFrame et deviner la source."""
        try:
            df = mdf.to_dataframe()
        except Exception as exc:
            return [], f"Erreur lecture MDF4 : {exc}"
        if df is None or len(df) == 0:
            return [], "MDF4 vide."

        df.columns = [c.split(".")[-1] if "." in c else c for c in df.columns]
        df.columns = [c.split(":")[0] if ":" in c else c for c in df.columns]
        cols = list(df.columns)
        src = Mdf4ScenarioLoader._detect_source(cols) or "motor"
        if sources and src not in sources:
            return [], "Source non selectionnee dans les filtres."

        timestamps = df.index.to_numpy(dtype=float)
        t0 = float(timestamps[0]) if len(timestamps) else 0.0
        rows = []
        for i, ts in enumerate(timestamps):
            row_dict: dict = {"source": src, "timestamp": str(ts)}
            for col in cols:
                try:
                    v = df.iloc[i][col]
                    if isinstance(v, (bytes, bytearray)):
                        row_dict[col] = v.decode("utf-8", errors="replace").strip("\x00")
                    elif v is not None:
                        row_dict[col] = str(v)
                    else:
                        row_dict[col] = ""
                except Exception:
                    pass
            Mdf4ScenarioLoader._remap(src, row_dict)
            t_abs = float(ts)
            rows.append(ScenarioRow(
                t_abs=t_abs, t_rel=t_abs - t0,
                source=src, raw=row_dict,
                summary=Mdf4ScenarioLoader._summarise(src, row_dict),
            ))
        if not rows:
            return [], "Aucune donnee valide dans ce fichier MDF4."
        rows.sort(key=lambda x: x.t_rel)
        return rows, ""

    @staticmethod
    def _detect_source(cols: list) -> str | None:
        col_set = set(cols)
        for src, hints in Mdf4ScenarioLoader._SRC_HINTS.items():
            if col_set & hints:
                return src
        return None

    @staticmethod
    def _remap(src: str, r: dict) -> None:
        """Renomme les canaux MDF vers les noms attendus par ScenarioEngine/VirtualECU.
        Les clés ici doivent correspondre aux noms réels produits par mdf_exporter.py."""
        aliases = {
            # MOTOR
            "motor_state_txt":   "state",
            "motor_state_int":   "state_int",
            "front_motor_on":    "front_on",
            "rear_motor_on":     "rear_on",
            "motor_current":     "current",
            "crs_wiper_op":      "wiper_op",
            "rest_contact":      "rest_contact_raw",
            "front_blade_cycles":"blade_cycles",
            "vehicle_speed":     "vehicle_speed",
            "rain_intensity":    "rain_intensity",
            "motor_fault":       "fault",
            # LIN — noms produits par mdf_exporter.py
            "lin_pid":               "lin_id",
            "lin_wiper_op":          "wiper_op",
            "lin_front_motor_on":    "front_on",    # nom réel dans le MDF4
            "lin_front_on":          "front_on",    # alias de compatibilité
            "lin_rest_contact_raw":  "rest_contact_raw",  # nom réel dans le MDF4
            "lin_rest_raw":          "rest_contact_raw",  # alias de compatibilité
            "lin_alive":             "alive",        # alive counter rolling
            "lin_cs_int":            "cs_int",       # checksum entier
            "lin_bcm_state":         "bcm_state",    # état BCM textuel
            "lin_raw":               "raw",          # trame hex brute
            # CAN
            "can_direction":     "direction",
            "can_wiper_cmd":     "wiper_cmd",
            "can_payload_B0":    "can_payload_b0",  # normalisation casse
            # PUMP
            "pump_active":       "pump_on",
            "pump_state":        "state",
            "pump_current":      "current",
            "pump_flow":         "pump_flow",
            "pump_pressure":     "pump_pressure",
            "pump_timeout_elapsed": "pump_remaining",
            "pump_direction":    "direction",
        }
        for mdf_name, csv_name in aliases.items():
            if mdf_name in r and csv_name not in r:
                r[csv_name] = r[mdf_name]

        # Reconstruire "state" textuel si absent mais state_int present
        if src == "motor" and "state" not in r and "state_int" in r:
            _WOP = {0:"OFF",1:"TOUCH",2:"SPEED1",3:"SPEED2",
                    4:"AUTO",5:"FRONT_WASH",6:"REAR_WASH",7:"REAR_WIPE"}
            try:
                r["state"] = _WOP.get(int(float(r["state_int"])), "OFF")
            except Exception:
                r["state"] = "OFF"

    @staticmethod
    def _summarise(src: str, r: dict) -> str:
        if src == "motor":
            parts = [f"{k}={r[k]}" for k in
                     ("state", "wiper_op", "ignition", "vehicle_speed", "rain_intensity")
                     if r.get(k)]
            return "  ".join(parts) or r.get("state", "—")
        if src == "lin":
            return f"{r.get('lin_type','')}  op={r.get('wiper_op','')} front={r.get('front_on','')}"
        if src == "can":
            return f"{r.get('can_id_hex', r.get('can_id',''))} {r.get('direction','')} dlc={r.get('can_dlc','')}"
        if src == "pump":
            return f"pump={r.get('state', r.get('pump_state',''))} cur={r.get('current', r.get('pump_current',''))} A"
        return str(r)
        if src == "pump":
            return f"state={r.get('state','')} dir={r.get('direction','')} I={r.get('current','')}A"
        return str(r)


def _bool_val(raw: str) -> bool:
    """Interprète un booléen depuis une chaîne qui peut venir d'un CSV ('1','ON','true')
    ou d'un MDF4 ('1.0', '0.0' — asammdf convertit les uint8 en float64 dans iter_groups).
    Retourne True si la valeur représente 1/vrai/actif."""
    s = raw.strip().lower()
    if s in ("1", "true", "on", "yes", "parked"):
        return True
    if s in ("0", "false", "off", "no", "moving"):
        return False
    try:
        return int(float(s)) != 0
    except ValueError:
        return False


def _parse_ts(ts: str) -> float:
    if not ts:
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(ts.strip(), fmt).timestamp()
        except ValueError:
            pass
    return 0.0


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _safe_int(v, default=0) -> int:
    try:
        return int(float(v))
    except Exception:
        return default


_WOP_CODE = {
    "OFF": 0, "TOUCH": 1, "SPEED1": 2, "SPEED2": 3,
    "AUTO": 4, "FRONT_WASH": 5, "REAR_WASH": 6, "REAR_WIPE": 7,
}
_WOP_NAME = {v: k for k, v in _WOP_CODE.items()}
_WOP_MOTOR = {
    0: (False, False, "OFF"),    1: (True, False, "OFF"),
    2: (True, False, "OFF"),     3: (True, False, "OFF"),
    4: (True, False, "OFF"),     5: (True, False, "FORWARD"),
    6: (False, True, "BACKWARD"), 7: (False, True, "OFF"),
}


class VirtualECU:
    def __init__(self):
        self.wiper_op    : int   = 0
        self.ignition    : str   = "OFF"
        self.vehicle_spd : float = 0.0
        self.rain        : int   = 0
        self.reverse     : bool  = False
        self.front_on    : bool  = False
        self.rear_on     : bool  = False
        self.pump_state  : str   = "OFF"
        self.pump_cur    : float = 0.0
        self.pump_vol    : float = 0.0
        self.pump_flow   : float = 0.0
        self.pump_pressure: float = 0.0
        self.motor_cur   : float = 0.0
        self.motor_speed : str   = "Speed1"   # "Speed1" ou "Speed2" — mis à jour par apply_motor_row
        self.rest_contact: bool  = False
        self.blade_cycles: int   = 0
        self.bcm_state   : str   = "OFF"
        self._motor_panel  = None
        self._pump_panel   = None
        self._veh_panel    = None
        self._crslin_panel = None
        self._can_panel    = None
        self._car_html     = None
        self._car_html_mp  = None
        self._main_window  = None

    def connect_panels(self, motor_panel=None, pump_panel=None, veh_panel=None,
                       crslin_panel=None, can_panel=None, car_html=None,
                       car_html_mp=None, main_window=None):
        self._motor_panel  = motor_panel
        self._pump_panel   = pump_panel
        self._veh_panel    = veh_panel
        self._crslin_panel = crslin_panel
        self._can_panel    = can_panel
        self._car_html     = car_html
        self._car_html_mp  = car_html_mp
        self._main_window  = main_window

    def apply_motor_row(self, r: dict):
        # Chercher wiper_op en priorité : crs_wiper_op (entier MDF4) > wiper_op (alias remap) > state (textuel)
        # Ignorer les valeurs 'nan' produites par to_dataframe global quand les groupes sont fusionnés
        def _clean(v):
            s = str(v or "").strip()
            return "" if s.lower() in ("nan", "none") else s

        wop_raw = (
            _clean(r.get("crs_wiper_op", "")) or
            _clean(r.get("wiper_op",     "")) or
            _clean(r.get("state",        ""))
        )
        # Si crs_wiper_op = "0" ou "0.0" (MDF4 float) et que state/front_motor_on
        # indique que le moteur tourne quand même, préférer state ou ignorer l'op=0
        wop_is_zero = wop_raw in ("0", "0.0")
        if wop_is_zero and _clean(r.get("state", "")):
            wop_raw = _clean(r.get("state", ""))
            wop_is_zero = False
        # Si op=0 mais front_motor_on=1 dans le fichier → le BCM tourne réellement,
        # crs_wiper_op n'était pas enregistré correctement → garder wiper_op courant
        if wop_is_zero:
            front_hint = str(r.get("front_motor_on", r.get("front_on", ""))).strip()
            if front_hint and _bool_val(front_hint):
                wop_raw = ""   # ignorer l'op=0 erroné, ne pas écraser l'état courant
        op = self._parse_wiper_op(wop_raw)
        # Mettre à jour sans la garde "op != self.wiper_op" pour garantir
        # que _update_wiper_state() est appelée à chaque step de replay
        # (sinon si le 1er step est déjà SPEED1, les suivants seraient ignorés)
        if op is not None:
            self.wiper_op = op
            # Dériver motor_speed depuis wiper_op (source de vérité principale)
            self.motor_speed = "Speed2" if op == 3 else "Speed1"
            self._update_wiper_state()
        ign_raw = str(r.get("ignition", "") or r.get("ignition_status", "")).strip().upper()
        ign = self._parse_ignition(ign_raw)
        if ign:
            # Pas de garde != : chaque row de replay met à jour l'ignition
            self.ignition = ign
            self._apply_ignition()
        spd = r.get("vehicle_speed", "")
        if spd not in ("", None, "nan"):
            v = _safe_float(spd)
            self.vehicle_spd = v
            self._apply_speed()
        rain = r.get("rain_intensity", "")
        if rain not in ("", None, "nan"):
            v = _safe_int(rain)
            self.rain = v
            self._apply_rain()
        rev_raw = str(r.get("reverse_gear", "")).strip().lower()
        if rev_raw in ("1", "true", "r", "reverse", "1.0"):
            self.reverse = True
            self._apply_reverse()
        elif rev_raw in ("0", "false", "d", "n", "p", "0.0"):
            self.reverse = False
            self._apply_reverse()
        cur = r.get("current", "")
        if cur not in ("", None, "nan"):
            self.motor_cur = _safe_float(cur)
        # Lire la vitesse moteur depuis le champ enregistré (CSV "Speed1"/"Speed2"
        # ou MDF4 après remap). Si absent, la valeur dérivée de wiper_op (ci-dessus) est conservée.
        spd_raw = str(r.get("speed", "") or "").strip()
        if spd_raw.lower() in ("speed2", "2"):
            self.motor_speed = "Speed2"
        elif spd_raw.lower() in ("speed1", "1") or spd_raw.lower() == "speed1":
            self.motor_speed = "Speed1"
        rest_raw = str(r.get("rest_contact", "") or r.get("rest_contact_raw", "")).strip()
        if rest_raw and rest_raw.lower() not in ("nan", "none"):
            # 1/true/parked → PARKED = rest_contact=False dans VirtualECU
            if _bool_val(rest_raw) or rest_raw.lower() == "parked":
                rest = False
            else:
                rest = True
            self.rest_contact = rest
            self._apply_rest_contact()
        bc = r.get("front_blade_cycles", "") or r.get("blade_cycles", "")
        if bc not in ("", None, "nan"):
            self.blade_cycles = _safe_int(bc)

        # front_on / rear_on — clés remappées par _remap (front_motor_on → front_on)
        # Le CSV live utilise "front"="ON"/"OFF", le MDF utilise "front_on"=0/1
        front_raw = str(r.get("front_on", "") or r.get("front", "")).strip()
        if front_raw and front_raw.lower() not in ("nan", "none", ""):
            self.front_on = _bool_val(front_raw)
        rear_raw = str(r.get("rear_on", "") or r.get("rear", "")).strip()
        if rear_raw and rear_raw.lower() not in ("nan", "none", ""):
            self.rear_on = _bool_val(rear_raw)

        self._push_motor_panel()

    def apply_lin_row(self, r: dict):
        op_raw = str(r.get("op", "") or r.get("wiper_op", "")).strip()

        # Fallback : bcm_state textuel si wiper_op LIN est absent ou 0
        if not op_raw or op_raw == "0":
            bcm_raw = str(r.get("bcm_state", "")).strip()
            if bcm_raw and bcm_raw.upper() not in ("", "NAN", "NONE"):
                op_raw = bcm_raw  # ex. "SPEED1" → op=2, "OFF" → op=0

        op = self._parse_wiper_op(op_raw)

        # op=0 depuis le LIN = bruit de bus (trame RX_HDR sans réponse BCM),
        # PAS une commande OFF réelle — le OFF réel arrive via apply_motor_row
        # (motor_state_txt='OFF'). On n'écrase l'état courant que si op > 0.
        if op is not None and op != 0:
            self.wiper_op = op
            self._update_wiper_state()

        # Mettre à jour rest_contact depuis le fichier si présent
        rest_raw = str(r.get("rest_contact_raw", "") or r.get("rest_contact", "")).strip()
        if rest_raw and rest_raw.lower() not in ("nan", "none", ""):
            if _bool_val(rest_raw) or rest_raw.lower() == "parked":
                self.rest_contact = False   # PARKED = au repos
            else:
                self.rest_contact = True    # MOVING
            self._apply_rest_contact()

        front_raw = str(r.get("front_on", "") or r.get("front_motor_on", "")).strip()
        if front_raw and front_raw.lower() not in ("nan", "none", ""):
            val = _bool_val(front_raw)
            # front_on=False du LIN = bruit de bus, pas un arrêt réel.
            # On n'applique que True (moteur actif confirmé par le LIN).
            # Le False réel arrive via apply_motor_row (motor_state_txt='OFF').
            if val:
                self.front_on = True

        # Mettre à jour bcm_state si présent (champ texte)
        bcm_raw = str(r.get("bcm_state", "")).strip()
        if bcm_raw and bcm_raw not in ("nan", "none", ""):
            self.bcm_state = bcm_raw

        # Blade cycles
        bc = r.get("blade_cycles", "") or r.get("front_blade_cycles", "")
        if bc not in ("", None, "nan"):
            self.blade_cycles = _safe_int(bc)

        if self._crslin_panel:
            alive_val = _safe_int(r.get("alive", 0))
            cs_val    = _safe_int(r.get("cs_int", 0))
            pid_val   = str(r.get("pid", "") or r.get("lin_id", "0xD6") or "0xD6")
            ev = {
                "type": "TX", "op": self.wiper_op, "pid": pid_val,
                "alive": alive_val, "cs_int": cs_val,
                "raw": str(r.get("raw", "") or ""),
                "time": time.time(), "bcm_state": self.bcm_state,
                "front_motor_on": self.front_on, "rear_motor_on": self.rear_on,
                "rest_contact_raw": self.rest_contact,
                "front_blade_cycles": self.blade_cycles,
            }
            try:
                self._crslin_panel.add_lin_event(ev)
                self._crslin_panel.on_wiper_sent(self.wiper_op, alive_val)
            except Exception:
                pass

    def apply_can_row(self, r: dict):
        can_id_str = str(r.get("can_id", "")).strip()
        direction  = str(r.get("direction", "TX")).strip()
        payload    = str(r.get("payload", "")).strip()
        try:
            can_id_int = int(can_id_str, 16) if can_id_str.startswith(("0x", "0X")) else int(can_id_str)
        except Exception:
            can_id_int = 0
        fields = self._decode_can_fields(can_id_int, r)
        if can_id_int == 0x300:
            ign_code = fields.get("ignition", -1)
            if isinstance(ign_code, int):
                ign = {0: "OFF", 1: "ACC", 2: "ON"}.get(ign_code)
                if ign and ign != self.ignition:
                    self.ignition = ign
                    self._apply_ignition()
            spd = fields.get("speed_kmh")
            if spd is not None:
                self.vehicle_spd = float(spd)
                self._apply_speed()
            rev = fields.get("reverse")
            if rev is not None and bool(rev) != self.reverse:
                self.reverse = bool(rev)
                self._apply_reverse()
        elif can_id_int == 0x301:
            rain = fields.get("intensity")
            if rain is not None:
                v = int(rain)
                if v != self.rain:
                    self.rain = v
                    self._apply_rain()
        elif can_id_int == 0x201:
            mode = fields.get("mode")
            if isinstance(mode, int) and mode != self.wiper_op:
                self.wiper_op = mode
                self._update_wiper_state()
            cur = fields.get("current_A")
            if cur is not None:
                self.motor_cur = float(cur)
        if self._can_panel:
            ev = {
                "type": direction, "can_id": can_id_str, "can_id_int": can_id_int,
                "dlc": _safe_int(r.get("dlc", 8)), "data": payload,
                "desc": self._can_desc(can_id_int), "time": time.time(), "fields": fields,
            }
            try:
                self._can_panel.add_can_event(ev)
            except Exception:
                pass

    def apply_pump_row(self, r: dict):
        # "state" peut venir de pump_state (MDF remappé) ou directement
        state = str(r.get("state") or r.get("pump_state") or "").strip().upper()
        direc = str(r.get("direction", "") or r.get("pump_direction", "")).strip().upper()
        if direc in ("FWD", "FORWARD"):
            state = "FORWARD"
        elif direc in ("BWD", "BACKWARD", "REVERSE"):
            state = "BACKWARD"
        # Si pump_active=0 et state vide → OFF
        active_raw = str(r.get("pump_on", r.get("pump_active", ""))).strip()
        if active_raw.lower() not in ("", "nan", "none") and not _bool_val(active_raw) and state not in ("FORWARD", "BACKWARD"):
            state = "OFF"
        # Ignorer les valeurs NaN produites par asammdf sur colonnes mixtes
        if state and state not in ("NAN", "NONE", ""):
            self.pump_state = state
        cur_raw = r.get("current") or r.get("pump_current") or "0"
        if str(cur_raw).lower() not in ("nan", "none", ""):
            self.pump_cur = _safe_float(cur_raw)
        vol_raw = r.get("voltage") or "12.0"
        if str(vol_raw).lower() not in ("nan", "none", ""):
            self.pump_vol = _safe_float(vol_raw) or 12.0
        flow_raw = r.get("pump_flow", r.get("flow", ""))
        if str(flow_raw).lower() not in ("nan", "none", ""):
            self.pump_flow = _safe_float(flow_raw)
        pres_raw = r.get("pump_pressure", r.get("pressure", ""))
        if str(pres_raw).lower() not in ("nan", "none", ""):
            self.pump_pressure = _safe_float(pres_raw)
        self._apply_pump()

    def _update_wiper_state(self):
        front, rear, pump = _WOP_MOTOR.get(self.wiper_op, (False, False, "OFF"))
        self.front_on = front
        self.rear_on  = rear
        if pump != "OFF":
            self.pump_state = pump
        self.bcm_state = _WOP_NAME.get(self.wiper_op, "OFF")
        # Synchroniser motor_speed avec wiper_op (sauf si apply_motor_row l'a déjà
        # surchargé depuis le champ "speed" enregistré — dans ce cas il sera
        # écrasé juste après par la lecture du champ raw, ce qui est correct)
        self.motor_speed = "Speed2" if self.wiper_op == 3 else "Speed1"
        if self._crslin_panel:
            try:
                self._crslin_panel._select_op(self.wiper_op)
            except Exception:
                pass
        for car in self._cars():
            try:
                car.set_wiper_op(self.wiper_op)
                car.set_wiper_from_bcm(motor_on=self.front_on or self.rear_on,
                                       rest_raw=self.rest_contact,
                                       bcm_state=self.bcm_state, op=self.wiper_op)
            except Exception:
                pass
        if self._car_html_mp:
            try:
                self._car_html_mp.set_pump_state(self.pump_state, False)
            except Exception:
                pass

    def _apply_ignition(self):
        if self._main_window:
            try:
                self._main_window._car_ign_changed(self.ignition)
            except Exception:
                pass
        if self._veh_panel:
            try:
                self._veh_panel.ign._sel(self.ignition)
            except Exception:
                pass
        for car in self._cars():
            try:
                car.set_ignition(self.ignition)
            except Exception:
                pass

    def _apply_speed(self):
        if self._veh_panel:
            try:
                knob = self._veh_panel._spd_knob
                knob._val = int(self.vehicle_spd * 10)
                knob._arc.set_value(knob._val)
            except Exception:
                pass
        for car in self._cars():
            try:
                car.set_speed(self.vehicle_spd)
            except Exception:
                pass

    def _apply_rain(self):
        if self._veh_panel:
            try:
                knob = self._veh_panel._rain_knob
                knob._val = self.rain
                knob._arc.set_value(self.rain)
            except Exception:
                pass
        for car in self._cars():
            try:
                car.set_rain(self.rain)
            except Exception:
                pass

    def _apply_reverse(self):
        if self._veh_panel:
            try:
                self._veh_panel._rev = 1 if self.reverse else 0
                self._veh_panel._led_rev.set_state(self.reverse, A_ORANGE if self.reverse else "#707070")
                self._veh_panel.lbl_rev.setText("REVERSE" if self.reverse else "NORMAL")
            except Exception:
                pass
        if self._main_window:
            try:
                self._main_window._rev_btn.setChecked(self.reverse)
            except Exception:
                pass
        for car in self._cars():
            try:
                car.set_reverse(self.reverse)
            except Exception:
                pass

    def _apply_rest_contact(self):
        if self._crslin_panel:
            try:
                self._crslin_panel.update_rest_contact(self.rest_contact, self.blade_cycles)
            except Exception:
                pass
        if self._motor_panel:
            try:
                parked = not self.rest_contact
                self._motor_panel.led_rest.set_state(parked, A_GREEN if parked else A_ORANGE)
                self._motor_panel.lbl_rest.setText("PARKED" if parked else "MOVING")
            except Exception:
                pass

    def _push_motor_panel(self):
        if not self._motor_panel:
            return
        try:
            # Utiliser motor_speed (mis à jour par apply_motor_row depuis wiper_op ou champ "speed")
            speed_str = self.motor_speed
            self._motor_panel.on_motor_data({
                "front": "ON" if self.front_on else "OFF",
                "rear":  "ON" if self.rear_on  else "OFF",
                "speed": speed_str, "current": self.motor_cur,
                "fault": self.motor_cur > 1.2,
                "rest":  "PARKED" if not self.rest_contact else "MOVING",
                "state": self.bcm_state, "vehicle_speed": self.vehicle_spd,
                "rain_intensity": self.rain,
            })
        except Exception:
            pass

    def _apply_pump(self):
        if self._pump_panel:
            try:
                active = self.pump_state in ("FORWARD", "BACKWARD")
                self._pump_panel.update_display({
                    "state": self.pump_state, "current": self.pump_cur,
                    "voltage": self.pump_vol or 12.0, "fault": False,
                    "fault_reason": "",
                    "flow": self.pump_flow, "pressure": self.pump_pressure,
                    "pump_remaining": 5.0 if active else 0.0,
                    "pump_duration": 5.0, "source": "REPLAY",
                })
            except Exception:
                pass
        if self._car_html_mp:
            try:
                self._car_html_mp.set_pump_state(self.pump_state, False)
            except Exception:
                pass

    def _cars(self):
        return [c for c in (self._car_html, self._car_html_mp) if c]

    @staticmethod
    def _parse_wiper_op(raw: str) -> Optional[int]:
        if not raw:
            return None
        if raw.upper() in _WOP_CODE:
            return _WOP_CODE[raw.upper()]
        try:
            v = int(float(raw))
            if 0 <= v <= 7:
                return v
        except Exception:
            pass
        return None

    @staticmethod
    def _parse_ignition(raw: str) -> Optional[str]:
        if raw in ("1", "ON", "TRUE"):
            return "ON"
        if raw in ("2",):          # MDF stocke 2=ON (int32 0=OFF 1=ACC 2=ON)
            return "ON"
        if raw == "ACC":
            return "ACC"
        if raw in ("0", "OFF", "FALSE"):
            return "OFF"
        return None

    @staticmethod
    def _decode_can_fields(can_id: int, r: dict) -> dict:
        col_map = {
            0x200: "wiper_cmd", 0x201: "wiper_status", 0x202: "wiper_ack",
            0x300: "vehicle_status", 0x301: "rain_sensor",
        }
        col = col_map.get(can_id)
        if col and r.get(col):
            try:
                import json as _j
                return _j.loads(r[col])
            except Exception:
                pass
        return {}

    @staticmethod
    def _can_desc(can_id: int) -> str:
        return {
            0x200: "Wiper_Cmd", 0x201: "Wiper_Status", 0x202: "Wiper_Ack",
            0x300: "Vehicle_Status", 0x301: "RainSensorData",
        }.get(can_id, f"0x{can_id:03X}")


class ScenarioEngine(QObject):
    step_fired       = Signal(int)
    replay_finished  = Signal()
    log_msg          = Signal(str)
    progress_changed = Signal(int, int)
    virtual_motor_data = Signal(dict)
    virtual_lin_event  = Signal(dict)
    virtual_pump_data  = Signal(dict)

    def __init__(self, can_worker=None, lin_worker=None,
                 motor_worker=None, rte_client=None, parent=None):
        super().__init__(parent)
        self._can_w   = can_worker
        self._lin_w   = lin_worker
        self._motor_w = motor_worker
        self._rte     = rte_client
        self._rows:    list[ScenarioRow] = []
        self._has_motor_rows = True
        self._has_pump_rows  = True
        self._idx:     int  = 0
        self._t_start: float = 0.0
        self._speed:   float = 1.0
        self._running  = False
        self._paused   = False
        self._virtual_widgets: bool = True
        self.ecu = VirtualECU()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire_next)

    def load(self, rows: list[ScenarioRow]):
        self._rows = rows
        self._idx  = 0
        # Détecter quelles sources sont présentes dans le fichier
        sources = {r.source for r in rows}
        self._has_motor_rows = "motor" in sources or "lin" in sources
        self._has_pump_rows  = "pump"  in sources

    def set_speed(self, factor: float):
        self._speed = max(0.1, factor)

    def set_virtual_widgets(self, enabled: bool):
        self._virtual_widgets = enabled

    def connect_panels(self, **kwargs):
        self.ecu.connect_panels(**kwargs)

    def start(self):
        if not self._rows:
            return
        self._idx = 0
        self._t_start = time.monotonic()
        self._running = True
        self._paused  = False
        self.log_msg.emit(f"PLAY  {len(self._rows)} steps  x{self._speed:.1f}")
        self._schedule_next()

    def pause(self):
        if not self._running:
            return
        self._paused = True
        self._timer.stop()
        self.log_msg.emit("PAUSE")

    def resume(self):
        if not self._paused:
            return
        self._paused = False
        self._t_start = time.monotonic() - self._rows[self._idx].t_rel / self._speed
        self.log_msg.emit("RESUME")
        self._schedule_next()

    def stop(self):
        self._running = False
        self._paused  = False
        self._timer.stop()
        self.log_msg.emit("STOP")

    def seek(self, index: int):
        self._idx = max(0, min(index, len(self._rows) - 1))
        self._t_start = time.monotonic() - self._rows[self._idx].t_rel / self._speed
        if self._running and not self._paused:
            self._timer.stop()
            self._schedule_next()

    @property
    def current_index(self) -> int:
        return self._idx

    @property
    def is_running(self) -> bool:
        return self._running and not self._paused

    def _schedule_next(self):
        if self._idx >= len(self._rows):
            self._running = False
            self.replay_finished.emit()
            self.log_msg.emit("DONE — all steps injected")
            return
        row      = self._rows[self._idx]
        elapsed  = time.monotonic() - self._t_start
        delay_ms = max(0, int((row.t_rel / self._speed - elapsed) * 1000))
        self._timer.start(delay_ms)

    def _fire_next(self):
        if not self._running or self._paused:
            return
        if self._idx >= len(self._rows):
            self._running = False
            self.replay_finished.emit()
            return
        row = self._rows[self._idx]
        self._inject(row)
        self.step_fired.emit(self._idx)
        self.progress_changed.emit(self._idx + 1, len(self._rows))
        self._idx += 1
        self._schedule_next()

    def _inject(self, row: ScenarioRow):
        src = row.source
        r   = row.raw
        if src == "motor":
            if self._virtual_widgets:
                self.ecu.apply_motor_row(r)
            self._inject_motor_physical(r)
            self._emit_legacy_motor()
            # Si pump absent du fichier, on réémet son dernier état connu
            # pour que les widgets drag&drop Motor/Pump restent à jour
            if not self._has_pump_rows:
                self._emit_legacy_pump()
        elif src == "lin":
            if self._virtual_widgets:
                self.ecu.apply_lin_row(r)
            self._inject_lin_physical(r)
            self._emit_legacy_lin(r)
            # ── FIX : alimenter aussi le SignalHub (instruments drag&drop)
            # apply_lin_row() met à jour ecu.wiper_op / front_on / rear_on,
            # mais virtual_lin_event n'est pas connecté au signal_hub.on_motor_data.
            # On réutilise _emit_legacy_motor() qui émet virtual_motor_data → SignalHub.
            self._emit_legacy_motor()
        elif src == "can":
            if self._virtual_widgets:
                self.ecu.apply_can_row(r)
            self._inject_can_physical(r)
            # ── FIX : même logique que LIN — apply_can_row() peut changer wiper_op,
            # ignition, vehicle_speed : on propague au SignalHub.
            if self._virtual_widgets:
                self._emit_legacy_motor()
        elif src == "pump":
            if self._virtual_widgets:
                self.ecu.apply_pump_row(r)
            self._emit_legacy_pump()
            # Si motor absent du fichier, on réémet son dernier état connu
            if not self._has_motor_rows:
                self._emit_legacy_motor()

    def _inject_motor_physical(self, r: dict):
        stimuli = {}
        op = self.ecu._parse_wiper_op(
            str(r.get("crs_wiper_op", "") or r.get("state", "")).strip())
        if op is not None:
            stimuli["crs_wiper_op"] = op
        ign = self.ecu._parse_ignition(str(r.get("ignition", "")).strip().upper())
        if ign:
            stimuli["ignition_status"] = 1 if ign == "ON" else (2 if ign == "ACC" else 0)
        spd = r.get("vehicle_speed", "")
        if spd not in ("", None, "nan"):
            stimuli["vehicle_speed"] = _safe_float(spd)
        rain = r.get("rain_intensity", "")
        if rain not in ("", None, "nan"):
            stimuli["rain_intensity"] = _safe_int(rain)
        if not stimuli:
            return
        if self._rte:
            for k, v in stimuli.items():
                self._rte.set_cmd(k, v)
            self.log_msg.emit(
                f"  RTE  {', '.join(f'{k}={v}' for k,v in stimuli.items())}")
        elif self._motor_w:
            payload = {}
            if "crs_wiper_op" in stimuli:
                payload["wiper_op"] = _WOP_NAME.get(stimuli["crs_wiper_op"], "OFF")
            if "ignition_status" in stimuli:
                payload["ignition_status"] = "ON" if stimuli["ignition_status"] == 1 else "OFF"
            if "vehicle_speed" in stimuli:
                payload["vehicle_speed"] = stimuli["vehicle_speed"]
            if "rain_intensity" in stimuli:
                payload["rain_intensity"] = stimuli["rain_intensity"]
            if payload:
                self._motor_w.queue_send(payload)

    def _inject_lin_physical(self, r: dict):
        op_raw = str(r.get("op", "") or r.get("wiper_op", "")).strip().upper()
        if op_raw and self._lin_w:
            self._lin_w.queue_send({"cmd": op_raw})

    def _inject_can_physical(self, r: dict):
        can_id = str(r.get("can_id", "")).strip()
        if can_id not in {"0x300", "0x301", "300", "301"}:
            return
        payload = str(r.get("payload", "")).strip()
        if payload and self._motor_w:
            self._motor_w.queue_send({"test_cmd": "inject_can",
                                       "can_id": can_id, "payload": payload})

    def _emit_legacy_motor(self):
        virt = {
            "state":              self.ecu.bcm_state,
            "front":              "ON" if self.ecu.front_on else "OFF",
            "rear":               "ON" if self.ecu.rear_on  else "OFF",
            "speed":              self.ecu.motor_speed,   # "Speed1" ou "Speed2" (mis à jour par apply_motor_row / wiper_op)
            "current":            self.ecu.motor_cur,
            "fault":              False,
            "rest":               "PARKED" if not self.ecu.rest_contact else "MOVING",
            "rest_contact_raw":   self.ecu.rest_contact,
            "front_blade_cycles": self.ecu.blade_cycles,
            "crs_fault":          0,
            "vehicle_speed":      self.ecu.vehicle_spd,
            "rain_intensity":     self.ecu.rain,
        }
        self.virtual_motor_data.emit(virt)
        self.log_msg.emit(
            f"  MTR  op={_WOP_NAME.get(self.ecu.wiper_op,'?')} "
            f"ign={self.ecu.ignition} "
            f"spd={self.ecu.vehicle_spd:.0f}km/h rain={self.ecu.rain}%")

    def _emit_legacy_lin(self, r: dict | None = None):
        r = r or {}
        # Lire alive/cs_int depuis le fichier source (CSV ou MDF4 remappé).
        # Fallback à 0 si absent (compatibilité anciens enregistrements).
        alive_val = _safe_int(r.get("alive",  0))
        cs_val    = _safe_int(r.get("cs_int", 0))
        raw_val   = str(r.get("raw", "") or "")
        pid_val   = str(r.get("pid", "") or r.get("lin_id", "0xD6") or "0xD6")
        # bcm_state : priorité au champ remappé du fichier, sinon VirtualECU
        bcm_val   = str(r.get("bcm_state", "") or self.ecu.bcm_state or "OFF")
        ev = {
            "type": "TX",
            "op":                 self.ecu.wiper_op,
            "bcm_state":          bcm_val,
            "front_motor_on":     self.ecu.front_on,
            "rear_motor_on":      self.ecu.rear_on,
            "rest_contact_raw":   self.ecu.rest_contact,
            "front_blade_cycles": self.ecu.blade_cycles,
            "pid":    pid_val,
            "alive":  alive_val,
            "cs_int": cs_val,
            "raw":    raw_val,
            "time":   time.time(),
        }
        self.virtual_lin_event.emit(ev)
        self.log_msg.emit(
            f"  LIN  op={_WOP_NAME.get(self.ecu.wiper_op,'?')}"
            f"  alive=0x{alive_val:02X}  bcm={bcm_val}")

    def _emit_legacy_pump(self):
        active = self.ecu.pump_state in ("FORWARD", "BACKWARD")
        virt = {
            "state": self.ecu.pump_state, "current": self.ecu.pump_cur,
            "voltage": self.ecu.pump_vol or 12.0, "fault": False,
            "flow": self.ecu.pump_flow, "pressure": self.ecu.pump_pressure,
            "fault_reason": "",
            "pump_remaining": 5.0 if active else 0.0,
            "pump_duration": 5.0, "source": "REPLAY",
        }
        self.virtual_pump_data.emit(virt)
        self.log_msg.emit(
            f"  PMP  state={self.ecu.pump_state} I={self.ecu.pump_cur:.3f}A"
            f"  flow={self.ecu.pump_flow:.2f} pres={self.ecu.pump_pressure:.2f}")


# ══════════════════════════════════════════════════════════════
#  TABLE STYLE COMMUN — police 8pt pour gagner de la place
# ══════════════════════════════════════════════════════════════
_TABLE_SS = (
    f"QTableWidget{{"
    f"background:{_BG_CARD};border:1px solid {_BR_MAIN};"
    f"gridline-color:{_BR_DIM};color:{_TX_PRI};"
    f"font-family:{_FONT_HMI};font-size:8pt;}}"
    f"QHeaderView::section{{background:{_BG_CARD};color:{_TX_HDR};"
    f"border:none;border-right:1px solid {_BR_DIM};"
    f"padding:4px 6px;font-family:{_FONT_HMI};font-size:7pt;font-weight:bold;"
    f"letter-spacing:1px;}}"
    f"QTableWidget::item{{padding:2px 4px;}}"
    f"QTableWidget::item:alternate{{background:{_BG_ROW_ALT};}}"
    f"QTableWidget::item:selected{{background:{_CY_DIM};color:{_TX_PRI};"
    f"border-left:2px solid {_CY};}}"
)


# ══════════════════════════════════════════════════════════════
#  DATA REPLAY PANEL
# ══════════════════════════════════════════════════════════════
class DataReplayPanel(QWidget):
    """
    Panneau unifié Data Record + Scenario Replay (split horizontal).

    API publique :
      on_motor_data(data)  on_lin_event(ev)  on_can_event(ev)  on_pump_data(data)
      connect_panels(...)  _engine

    Signaux StatusBar (connectés depuis MainWindow) :
      trigger_fired(msg)   — émis quand un overcurrent déclenche l'enregistrement
      trigger_cleared()    — émis quand l'alarme est acquittée / trigger désactivé
    """
    trigger_fired   = Signal(str)   # message décrivant l'événement
    trigger_cleared = Signal()

    def __init__(self, recorder: DataRecorder,
                 can_worker=None, lin_worker=None,
                 motor_worker=None, rte_client=None, parent=None):
        super().__init__(parent)
        self.setObjectName("DataReplayPanel")
        self._rec = recorder

        self._engine = ScenarioEngine(
            can_worker=can_worker, lin_worker=lin_worker,
            motor_worker=motor_worker, rte_client=rte_client, parent=self,
        )
        self._rows: list[ScenarioRow] = []
        self._csv_path: str = ""
        self._preview_paused = False

        # ── Auto-Trigger state ────────────────────────────────
        self._trig_enabled       = False
        self._trig_motor_thr     = 2.0    # A
        self._trig_pump_thr      = 2.0    # A
        self._trig_mode          = 0      # 0=Either 1=Motor 2=Pump 3=Both
        self._trig_autostop_s    = 0      # 0 = never auto-stop
        self._trig_motor_oc      = False  # overcurrent motor actif ?
        self._trig_pump_oc       = False  # overcurrent pump actif  ?
        self._alarm_active       = False
        self._alarm_blink_state  = False
        self._alarm_events: list[str] = []   # log des événements

        # Timer clignotant alarme (200 ms)
        self._t_alarm_blink = QTimer(self)
        self._t_alarm_blink.setInterval(200)
        self._t_alarm_blink.timeout.connect(self._on_alarm_blink)

        # Timer auto-stop
        self._t_autostop = QTimer(self)
        self._t_autostop.setSingleShot(True)
        self._t_autostop.timeout.connect(self._on_autostop)

        self._engine.step_fired.connect(self._on_step_fired)
        self._engine.replay_finished.connect(self._on_replay_finished)
        self._engine.log_msg.connect(self._replay_log)
        self._engine.progress_changed.connect(self._on_progress)
        self._rec.row_added.connect(self._on_row_added)

        self._apply_style()
        self._build()

        self._t_stats = QTimer(self)
        self._t_stats.timeout.connect(self._refresh_stats)
        self._t_stats.start(500)

    def _apply_style(self):
        self.setStyleSheet(f"""
            QWidget {{
                background: {_BG_BASE};
                color: {_TX_PRI};
                font-family: '{_FONT_UI}';
                font-size: 10pt;
            }}
            QLabel {{ background: transparent; }}
            QPushButton {{
                border: 1.5px solid #1A1A1A;
            }}
            QCheckBox::indicator {{
                border: 1.5px solid #1A1A1A;
                border-radius: 3px;
            }}
            QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit {{
                border: 1.5px solid #1A1A1A;
                border-radius: 4px;
            }}
            QSplitter::handle {{
                background: {_BR_DIM};
                height: 3px; width: 3px;
            }}
            QScrollBar:vertical {{
                background: {_BG_CARD}; width: 5px; border-radius: 3px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {_CY_BORDER}; border-radius: 3px; min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {_CY};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QScrollBar:horizontal {{
                background: {_BG_CARD}; height: 5px; border-radius: 3px;
            }}
            QScrollBar::handle:horizontal {{
                background: {_CY_BORDER}; border-radius: 3px;
            }}
        """)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ── Title bar ─────────────────────────────────────────
        title_bar = QWidget()
        title_bar.setObjectName("tb")
        title_bar.setFixedHeight(46)
        title_bar.setStyleSheet(f"""
            QWidget#tb {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #0E1520, stop:0.5 #0B0D12, stop:1 #0E1520);
                border: 1px solid {_BR_MAIN};
                border-left: 4px solid {_CY};
                border-radius: 6px;
            }}
        """)
        tbl = QHBoxLayout(title_bar)
        tbl.setContentsMargins(16, 0, 16, 0)
        tbl.setSpacing(0)

        # dot indicator
        dot = QLabel("◉")
        dot.setFont(QFont(_FONT_HMI, 10))
        dot.setStyleSheet(f"color: {_CY}; background: transparent; margin-right: 8px;")
        tbl.addWidget(dot)
        tbl.addSpacing(8)

        t1 = QLabel("DATADESK")
        t1.setFont(QFont(_FONT_HMI, 13, QFont.Weight.Bold))
        t1.setStyleSheet(f"color: {_TX_PRI}; letter-spacing: 4px; background: transparent;")
        t2 = QLabel("  //  Record · Export · Replay · Virtual ECU")
        t2.setFont(QFont(_FONT_HMI, 8))
        t2.setStyleSheet(f"color: {_TX_SEC}; letter-spacing: 0.5px; background: transparent;")
        self._title_status = QLabel("IDLE")
        self._title_status.setFont(QFont(_FONT_HMI, 8, QFont.Weight.Bold))
        self._title_status.setStyleSheet(
            f"color: {_TX_DIM}; letter-spacing: 2px; background: transparent;")
        tbl.addWidget(t1)
        tbl.addWidget(t2)
        tbl.addStretch()
        tbl.addWidget(self._title_status)
        root.addWidget(title_bar)

        # ── Splitter HORIZONTAL : DATA SAVE (gauche) | REPLAY (droite) ──
        hsp = QSplitter(Qt.Orientation.Horizontal)
        hsp.addWidget(self._build_data_save())
        hsp.addWidget(self._build_replay())
        hsp.setSizes([560, 560])
        root.addWidget(hsp, 1)

        # ── Status bar ────────────────────────────────────────
        sb = QWidget()
        sb.setObjectName("sb")
        sb.setFixedHeight(26)
        sb.setStyleSheet(f"""
            QWidget#sb {{
                background: {_BG_TOOLBAR};
                border: 1px solid {_BR_DIM};
                border-top: 1px solid {_BR_MAIN};
                border-radius: 0 0 6px 6px;
            }}
        """)
        sl = QHBoxLayout(sb)
        sl.setContentsMargins(14, 0, 14, 0)
        sl.setSpacing(24)
        self._sb_rec    = QLabel("● REC  —  idle")
        self._sb_rows   = QLabel("0 rows")
        self._sb_replay = QLabel("▶ REPLAY  —  no file")
        self._sb_time   = QLabel("")
        for lb in (self._sb_rec, self._sb_rows, self._sb_replay, self._sb_time):
            lb.setFont(QFont(_FONT_HMI, 7))
            lb.setStyleSheet(f"color: {_TX_DIM}; background: transparent;")
        sl.addWidget(self._sb_rec)
        sl.addWidget(self._sb_rows)
        sl.addStretch()
        sl.addWidget(self._sb_replay)
        sl.addWidget(self._sb_time)
        root.addWidget(sb)

        self._t_clock = QTimer(self)
        self._t_clock.setInterval(1000)
        self._t_clock.timeout.connect(
            lambda: self._sb_time.setText(time.strftime("%H:%M:%S")))
        self._t_clock.start()

    # ══════════════════════════════════════════════════════════
    #  SECTION DATA SAVE (panneau gauche)
    # ══════════════════════════════════════════════════════════
    def _build_data_save(self) -> QWidget:
        sec = _Section("DATA RECORD & EXPORT", "REC")
        lay = sec.layout

        # Ligne 1 : REC / STOP / CLR | Preview
        r1 = QHBoxLayout()
        r1.setSpacing(5)
        self._btn_rec   = _rec_pill("REC",  h=36, w=92)
        self._btn_stop  = _pill("STOP",      h=36, w=82)
        self._btn_clear = _pill("CLEAR",     h=36, w=82)
        self._btn_stop.setEnabled(False)
        self._btn_rec.clicked.connect(self._on_rec)
        self._btn_stop.clicked.connect(self._on_stop_rec)
        self._btn_clear.clicked.connect(self._on_clear_rec)
        r1.addWidget(self._btn_rec)
        r1.addWidget(self._btn_stop)
        r1.addWidget(self._btn_clear)
        r1.addWidget(_vsep())
        self._btn_pause_preview = _pill("Preview", h=36, w=96)
        self._btn_pause_preview.setCheckable(True)
        self._btn_pause_preview.toggled.connect(self._on_pause_preview)
        r1.addWidget(self._btn_pause_preview)
        r1.addStretch()
        self._lbl_elapsed = QLabel("00:00:00")
        self._lbl_elapsed.setFont(QFont(_FONT_HMI, 14, QFont.Weight.Bold))
        self._lbl_elapsed.setStyleSheet(
            f"color: {_TX_DIM}; letter-spacing: 3px; background: transparent;")
        r1.addWidget(self._lbl_elapsed)
        lay.addLayout(r1)

        # Ligne 2 : filtres sources
        r2 = QHBoxLayout()
        r2.setSpacing(6)
        lbl_s = QLabel("SOURCES")
        lbl_s.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
        lbl_s.setStyleSheet(f"color: {_TX_DIM}; letter-spacing: 1px; background: transparent;")
        r2.addWidget(lbl_s)

        self._src_checks: dict[str, QCheckBox] = {}
        self._src_counts: dict[str, QLabel]    = {}
        self._src_leds:   dict[str, StatusLed] = {}

        for src in ("motor", "lin", "can", "pump"):
            tag = _SRC_TAG[src]
            cb = QCheckBox(tag)
            cb.setChecked(True)
            cb.setFont(QFont(_FONT_HMI, 8, QFont.Weight.Bold))
            cb.setStyleSheet(f"""
                QCheckBox {{ color: {_TX_PRI}; background: transparent; spacing: 5px; }}
                QCheckBox::indicator {{
                    width: 13px; height: 13px;
                    border: 1px solid {_CY_BORDER};
                    border-radius: 3px; background: {_BG_INPUT};
                }}
                QCheckBox::indicator:checked {{
                    background: {_CY_DIM}; border-color: {_CY};
                    image: none;
                }}
                QCheckBox::indicator:checked::after {{
                    content: '✓';
                }}
            """)
            cb.toggled.connect(lambda checked, s=src: self._rec.set_filter(s, checked))
            self._src_checks[src] = cb
            led = StatusLed(6)
            led.set_state(False, _CY)
            self._src_leds[src] = led
            cnt = QLabel("0")
            cnt.setFont(QFont(_FONT_HMI, 8, QFont.Weight.Bold))
            cnt.setStyleSheet(f"color: {_CY}; background: transparent;")
            cnt.setFixedWidth(36)
            self._src_counts[src] = cnt
            grp = QHBoxLayout()
            grp.setSpacing(2)
            grp.addWidget(cb)
            grp.addWidget(led)
            grp.addWidget(cnt)
            r2.addLayout(grp)

        r2.addStretch()
        self._lbl_total = QLabel("0 rows")
        self._lbl_total.setFont(QFont(_FONT_HMI, 9, QFont.Weight.Bold))
        self._lbl_total.setStyleSheet(f"color: {_TX_PRI}; background: transparent;")
        r2.addWidget(self._lbl_total)
        lay.addLayout(r2)

        # Barre buffer
        br = QHBoxLayout()
        br.setSpacing(8)
        bl = QLabel("BUFFER")
        bl.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
        bl.setStyleSheet(f"color: {_TX_DIM}; letter-spacing: 1px; background: transparent;")
        br.addWidget(bl)
        self._prog_buf = QProgressBar()
        self._prog_buf.setRange(0, MAX_BUFFER)
        self._prog_buf.setValue(0)
        self._prog_buf.setFixedHeight(4)
        self._prog_buf.setTextVisible(False)
        self._prog_buf.setStyleSheet(
            f"QProgressBar{{background:{_BG_CARD};border:none;border-radius:2px;}}"
            f"QProgressBar::chunk{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 {_CY},stop:1 {_GN});border-radius:2px;}}"
        )
        br.addWidget(self._prog_buf, 1)
        self._lbl_buf = QLabel(f"0 / {MAX_BUFFER:,}")
        self._lbl_buf.setFont(QFont(_FONT_HMI, 7))
        self._lbl_buf.setStyleSheet(f"color: {_TX_DIM}; background: transparent;")
        br.addWidget(self._lbl_buf)
        lay.addLayout(br)

        # ── AUTO-TRIGGER ──────────────────────────────────────
        lay.addWidget(self._build_auto_trigger())

        # Export
        er = QHBoxLayout()
        er.setSpacing(6)
        el = QLabel("EXPORT")
        el.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
        el.setStyleSheet(f"color: {_TX_DIM}; letter-spacing: 1px; background: transparent;")
        er.addWidget(el)
        self._exp_combo = QComboBox()
        self._exp_combo.addItems(["All (merged)", "MOTOR", "LIN", "CAN", "PUMP", "Split (4 files)"])
        self._exp_combo.setStyleSheet(f"""
            QComboBox {{
                background: {_BG_INPUT}; border: 1px solid {_BR_MAIN};
                color: {_TX_PRI}; border-radius: 5px; padding: 3px 8px;
                font-family: {_FONT_HMI}; font-size: 8pt;
            }}
            QComboBox::drop-down {{ border: none; width: 16px; }}
            QComboBox QAbstractItemView {{
                background: {_BG_CARD}; color: {_TX_PRI};
                border: 1px solid {_BR_MAIN}; outline: none;
            }}
            QComboBox QAbstractItemView::item:hover {{
                background: {_CY_DIM};
            }}
        """)
        self._exp_combo.setFixedWidth(130)
        er.addWidget(self._exp_combo)
        self._btn_csv = _pill("CSV", h=28, w=72, accent=True)
        self._btn_csv.clicked.connect(self._on_export_csv)
        er.addWidget(self._btn_csv)
        self._btn_mdf = _pill("MDF4", h=28, w=80)
        self._btn_mdf.clicked.connect(self._on_export_mdf)
        if not _MDF_AVAILABLE:
            self._btn_mdf.setEnabled(False)
            self._btn_mdf.setToolTip("pip install asammdf")
        er.addWidget(self._btn_mdf)
        er.addStretch()
        self._lbl_export_status = QLabel("")
        self._lbl_export_status.setFont(QFont(_FONT_HMI, 7))
        self._lbl_export_status.setStyleSheet(f"color: {_TX_DIM}; background: transparent;")
        self._lbl_export_status.setWordWrap(True)
        er.addWidget(self._lbl_export_status, 1)
        lay.addLayout(er)

        # Preview header
        ph = QHBoxLayout()
        ph.addWidget(_lbl("LIVE PREVIEW", 7, bold=True, color=_TX_DIM))
        ph.addWidget(_lbl(f"last {_PREVIEW_MAX} rows", 7, color=_TX_DIM))
        ph.addStretch()
        self._lbl_preview_cnt = QLabel("0 visible")
        self._lbl_preview_cnt.setFont(QFont(_FONT_HMI, 7))
        self._lbl_preview_cnt.setStyleSheet(f"color: {_TX_DIM}; background: transparent;")
        ph.addWidget(self._lbl_preview_cnt)
        lay.addLayout(ph)

        # Preview table — colonnes compactes
        self._preview_table = QTableWidget(0, 5)
        self._preview_table.setHorizontalHeaderLabels(["Time", "S", "Field 1", "Field 2", "Field 3"])
        self._preview_table.setStyleSheet(_TABLE_SS)
        self._preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._preview_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._preview_table.setAlternatingRowColors(True)
        self._preview_table.horizontalHeader().setStretchLastSection(True)
        self._preview_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive)
        self._preview_table.setColumnWidth(0, 118)   # Time
        self._preview_table.setColumnWidth(1, 30)    # Src
        self._preview_table.setColumnWidth(2, 130)   # Field 1
        self._preview_table.setColumnWidth(3, 120)   # Field 2
        # col 4 stretches
        self._preview_table.verticalHeader().setVisible(False)
        self._preview_table.verticalHeader().setDefaultSectionSize(16)
        lay.addWidget(self._preview_table, 1)

        return sec.frame

    # ══════════════════════════════════════════════════════════
    #  SECTION AUTO-TRIGGER (injectée dans DATA SAVE)
    # ══════════════════════════════════════════════════════════
    def _build_auto_trigger(self) -> QWidget:
        """
        Carte AUTO-TRIGGER : démarre l'enregistrement automatiquement
        sur dépassement de seuil de courant (moteur / pompe / les deux).
        """
        card = QFrame()
        card.setObjectName("atcard")
        card.setStyleSheet(f"""
            QFrame#atcard {{
                background: #FFF3E0;
                border: 2px solid #1A1A1A;
                border-left: 4px solid {_WARN_AMB};
                border-radius: 6px;
            }}
        """)
        vlay = QVBoxLayout(card)
        vlay.setContentsMargins(12, 8, 12, 8)
        vlay.setSpacing(6)

        # ── Titre + Enable ────────────────────────────────────
        hdr = QHBoxLayout()
        ico = QLabel("")
        ico.setFont(QFont(_FONT_HMI, 9, QFont.Weight.Bold))
        ico.setStyleSheet(f"color:{_WARN_AMB};background:transparent;")
        ico.setFixedWidth(20)
        title_lbl = QLabel("AUTO-TRIGGER")
        title_lbl.setFont(QFont(_FONT_HMI, 8, QFont.Weight.Bold))
        title_lbl.setStyleSheet(f"color:{_WARN_AMB};letter-spacing:2px;background:transparent;")
        hdr.addWidget(ico)
        hdr.addWidget(title_lbl)
        hdr.addStretch()

        self._trig_cb = QCheckBox("Enable")
        self._trig_cb.setFont(QFont(_FONT_HMI, 8, QFont.Weight.Bold))
        self._trig_cb.setStyleSheet(f"""
            QCheckBox {{ color: {_WARN_AMB}; background: transparent; spacing: 5px; }}
            QCheckBox::indicator {{
                width: 13px; height: 13px;
                border: 1px solid rgba(255,184,48,0.40); border-radius: 3px;
                background: #FFF3E0;
            }}
            QCheckBox::indicator:checked {{
                background: {_WARN_DIM}; border-color: {_WARN_AMB};
            }}
        """)
        self._trig_cb.toggled.connect(self._on_trig_enable)
        hdr.addWidget(self._trig_cb)
        vlay.addLayout(hdr)

        # ── Seuils + Mode ─────────────────────────────────────
        r2 = QHBoxLayout(); r2.setSpacing(8)

        def _spin_thr(val: float) -> QDoubleSpinBox:
            sb = QDoubleSpinBox()
            sb.setRange(0.1, 20.0)
            sb.setSingleStep(0.1)
            sb.setValue(val)
            sb.setDecimals(1)
            sb.setSuffix(" A")
            sb.setFixedWidth(70)
            sb.setFixedHeight(24)
            sb.setFont(QFont(_FONT_HMI, 8))
            sb.setStyleSheet(f"""
                QDoubleSpinBox {{
                    background: #FFF8EC; color: {_WARN_AMB};
                    border: 1.5px solid #1A1A1A; border-radius: 4px;
                    padding: 2px 4px;
                    background: #FFF3E0;
                }}
                QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
                    width: 14px; border: none; background: #FFE8B8;
                }}
            """)
            return sb

        def _tag(t: str) -> QLabel:
            lb = QLabel(t)
            lb.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
            lb.setStyleSheet(f"color:{_TX_SEC};background:transparent;letter-spacing:1px;")
            return lb

        r2.addWidget(_tag("MTR >"))
        self._trig_motor_spin = _spin_thr(2.0)
        self._trig_motor_spin.valueChanged.connect(
            lambda v: setattr(self, '_trig_motor_thr', v))
        r2.addWidget(self._trig_motor_spin)

        r2.addSpacing(6)
        r2.addWidget(_tag("PMP >"))
        self._trig_pump_spin = _spin_thr(2.0)
        self._trig_pump_spin.valueChanged.connect(
            lambda v: setattr(self, '_trig_pump_thr', v))
        r2.addWidget(self._trig_pump_spin)

        r2.addSpacing(6)
        r2.addWidget(_tag("MODE"))
        self._trig_mode_combo = QComboBox()
        self._trig_mode_combo.addItems(
            ["Motor OU Pompe", "Motor seul", "Pompe seule", "Motor ET Pompe"])
        self._trig_mode_combo.setFont(QFont(_FONT_HMI, 7))
        self._trig_mode_combo.setFixedHeight(24)
        self._trig_mode_combo.setFixedWidth(130)
        self._trig_mode_combo.setStyleSheet(f"""
            QComboBox {{
                background: #FFF3E0; color: {_WARN_AMB};
                border: 1.5px solid #1A1A1A; border-radius: 4px;
                padding: 1px 5px; font-size: 7pt;
            }}
            QComboBox::drop-down {{ border: none; width: 14px; }}
            QComboBox QAbstractItemView {{
                background: #FFF3E0; color: {_WARN_AMB};
                border: 1px solid #1A1A1A;
            }}
        """)
        self._trig_mode_combo.currentIndexChanged.connect(
            lambda i: setattr(self, '_trig_mode', i))
        r2.addWidget(self._trig_mode_combo)

        r2.addSpacing(6)
        r2.addWidget(_tag("STOP"))
        self._trig_autostop_spin = QSpinBox()
        self._trig_autostop_spin.setRange(0, 300)
        self._trig_autostop_spin.setValue(0)
        self._trig_autostop_spin.setSuffix(" s")
        self._trig_autostop_spin.setSpecialValueText("∞")
        self._trig_autostop_spin.setFixedWidth(62)
        self._trig_autostop_spin.setFixedHeight(24)
        self._trig_autostop_spin.setFont(QFont(_FONT_HMI, 8))
        self._trig_autostop_spin.setStyleSheet(f"""
            QSpinBox {{
                background: #FFF3E0; color: {_WARN_AMB};
                border: 1.5px solid #1A1A1A; border-radius: 4px;
                padding: 2px 4px;
            }}
            QSpinBox::up-button, QSpinBox::down-button {{
                width: 14px; border: none; background: #FFE8B8;
            }}
        """)
        self._trig_autostop_spin.valueChanged.connect(
            lambda v: setattr(self, '_trig_autostop_s', v))
        r2.addWidget(self._trig_autostop_spin)

        r2.addStretch()
        vlay.addLayout(r2)

        # ── Bandeau ALARME ────────────────────────────────────
        self._alarm_banner = QLabel("  ○  MONITORING INACTIVE  ")
        self._alarm_banner.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
        self._alarm_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._alarm_banner.setFixedHeight(20)
        self._alarm_banner.setStyleSheet(
            f"background:#FFF3E0;color:{_TX_DIM};border-radius:3px;letter-spacing:1px;")
        vlay.addWidget(self._alarm_banner)

        # ── Ligne status + ACK ────────────────────────────────
        r3 = QHBoxLayout(); r3.setSpacing(6)
        self._alarm_led = StatusLed(8)
        self._alarm_led.set_state(False, _WARN_AMB)
        r3.addWidget(self._alarm_led)

        self._alarm_info = QLabel("Auto-trigger disabled")
        self._alarm_info.setFont(QFont(_FONT_HMI, 7))
        self._alarm_info.setStyleSheet(f"color:{_TX_DIM};background:transparent;")
        r3.addWidget(self._alarm_info, 1)

        self._btn_ack = QPushButton("ACK")
        self._btn_ack.setFixedSize(64, 22)
        self._btn_ack.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
        self._btn_ack.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_ack.setEnabled(False)
        self._btn_ack.setStyleSheet(f"""
            QPushButton {{ background:{_WARN_AMB}; color:#000000;
                          border:none; border-radius:4px; font-weight:900; }}
            QPushButton:hover {{ background:#FFC840; }}
            QPushButton:disabled {{ background:{_BG_INPUT}; color:{_TX_DIM};
                                    border:1px solid {_BR_DIM}; }}
        """)
        self._btn_ack.clicked.connect(self._on_ack_alarm)
        r3.addWidget(self._btn_ack)
        vlay.addLayout(r3)

        return card

    # ══════════════════════════════════════════════════════════
    #  SECTION SCENARIO REPLAY (panneau droit)
    #  Toolbar découpée en 2 lignes pour éviter le chevauchement
    # ══════════════════════════════════════════════════════════
    def _build_replay(self) -> QWidget:
        sec = _Section("SCENARIO REPLAY  —  Virtual ECU", "RPL")
        lay = sec.layout

        # ── Toolbar ligne 1 : Load | ▶ ⏸ ■ | × speed | badge ─
        tb1 = QHBoxLayout()
        tb1.setSpacing(5)

        self._btn_load  = _pill("Load CSV",  h=34, w=100, accent=True)
        self._btn_load_mdf = _pill("Load MDF4", h=34, w=110)
        if not _ASAMMDF_AVAILABLE:
            self._btn_load_mdf.setToolTip("pip install asammdf")
            self._btn_load_mdf.setEnabled(True)   # on laisse cliquable pour afficher le msg
        self._btn_play  = _pill("PLAY",      h=34, w=80)
        self._btn_pause = _pill("PAUSE",     h=34, w=80)
        self._btn_rstop = _pill("STOP",      h=34, w=72)
        self._btn_load.clicked.connect(self._on_load_csv)
        self._btn_load_mdf.clicked.connect(self._on_load_mdf4)
        self._btn_play.clicked.connect(self._on_play)
        self._btn_pause.clicked.connect(self._on_pause_replay)
        self._btn_rstop.clicked.connect(self._on_stop_replay)

        tb1.addWidget(self._btn_load)
        tb1.addWidget(self._btn_load_mdf)
        tb1.addWidget(_vsep())
        tb1.addWidget(self._btn_play)
        tb1.addWidget(self._btn_pause)
        tb1.addWidget(self._btn_rstop)
        tb1.addWidget(_vsep())

        spd_lbl = QLabel("SPEED ×")
        spd_lbl.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
        spd_lbl.setStyleSheet(f"color: {_TX_DIM}; letter-spacing: 1px; background: transparent;")
        self._spd_spin = QDoubleSpinBox()
        self._spd_spin.setRange(0.1, 10.0)
        self._spd_spin.setSingleStep(0.5)
        self._spd_spin.setValue(1.0)
        self._spd_spin.setFixedWidth(60)
        self._spd_spin.setFixedHeight(30)
        self._spd_spin.setFont(QFont(_FONT_HMI, 8))
        self._spd_spin.valueChanged.connect(lambda v: self._engine.set_speed(v))
        self._spd_spin.setStyleSheet(
            f"background:{_BG_INPUT};color:{_TX_PRI};"
            f"border:1px solid {_BR_MAIN};border-radius:5px;padding:2px 4px;")
        tb1.addWidget(spd_lbl)
        tb1.addWidget(self._spd_spin)
        tb1.addStretch()

        self._file_badge = QLabel("NO FILE")
        self._file_badge.setFont(QFont(_FONT_HMI, 7))
        self._file_badge.setStyleSheet(
            f"color: {_TX_DIM}; background: transparent;")
        tb1.addWidget(self._file_badge)
        lay.addLayout(tb1)

        # ── Toolbar ligne 2 : FILTER checkboxes | Virtual ECU badge ──
        tb2 = QHBoxLayout()
        tb2.setSpacing(6)

        flt_lbl = QLabel("FILTER")
        flt_lbl.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
        flt_lbl.setStyleSheet(f"color: {_TX_DIM}; letter-spacing: 1px; background: transparent;")
        tb2.addWidget(flt_lbl)

        self._rpl_chk: dict[str, QCheckBox] = {}
        for src in ("motor", "lin", "can", "pump"):
            c = QCheckBox(_SRC_TAG[src])
            c.setChecked(True)
            c.setFont(QFont(_FONT_HMI, 8))
            c.setStyleSheet(f"""
                QCheckBox {{ color: {_TX_PRI}; background: transparent; spacing: 5px; }}
                QCheckBox::indicator {{
                    width: 13px; height: 13px;
                    border: 1px solid {_CY_BORDER}; border-radius: 3px;
                    background: {_BG_INPUT};
                }}
                QCheckBox::indicator:checked {{
                    background: {_CY_DIM}; border-color: {_CY};
                }}
            """)
            self._rpl_chk[src] = c
            tb2.addWidget(c)

        tb2.addStretch()

        virt_badge = QLabel("◈ Virtual ECU")
        virt_badge.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
        virt_badge.setStyleSheet(
            f"color: {_GN}; background: {_GN_DIM};"
            f"border: 1px solid {_GN_GLOW}; border-radius: 4px;"
            f"padding: 2px 8px; letter-spacing: 1px;")
        tb2.addWidget(virt_badge)
        lay.addLayout(tb2)

        # ── Progress bar ───────────────────────────────────────
        pr = QHBoxLayout()
        pr.setSpacing(8)
        self._prog_replay = QProgressBar()
        self._prog_replay.setRange(0, 100)
        self._prog_replay.setValue(0)
        self._prog_replay.setFixedHeight(4)
        self._prog_replay.setTextVisible(False)
        self._prog_replay.setStyleSheet(
            f"QProgressBar{{background:{_BG_CARD};border:none;border-radius:2px;}}"
            f"QProgressBar::chunk{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 {_GN},stop:1 {_CY});border-radius:2px;}}")
        self._lbl_rprog = QLabel("0 / 0")
        self._lbl_rprog.setFont(QFont(_FONT_HMI, 7))
        self._lbl_rprog.setStyleSheet(f"color: {_TX_DIM}; background: transparent;")
        self._lbl_rtime = QLabel("0.0 s")
        self._lbl_rtime.setFont(QFont(_FONT_HMI, 7))
        self._lbl_rtime.setStyleSheet(f"color: {_CY}; background: transparent; font-weight: bold;")
        pr.addWidget(self._prog_replay, 1)
        pr.addWidget(self._lbl_rprog)
        pr.addWidget(self._lbl_rtime)
        lay.addLayout(pr)

        # ── Splitter VERTICAL : Timeline (haut) | Stats+Log (bas) ──
        vsp = QSplitter(Qt.Orientation.Vertical)

        # Timeline table — colonnes compactes
        tl_frame = QWidget()
        tl_frame.setStyleSheet("background: transparent;")
        tll = QVBoxLayout(tl_frame)
        tll.setContentsMargins(0, 0, 0, 0)
        tll.setSpacing(4)
        tl_hdr = QLabel("TIMELINE")
        tl_hdr.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
        tl_hdr.setStyleSheet(
            f"color: {_TX_DIM}; background: transparent; letter-spacing: 2px;")
        tll.addWidget(tl_hdr)

        self._tl_table = QTableWidget(0, 5)
        self._tl_table.setHorizontalHeaderLabels(["#", "T(s)", "Src", "Stimulus", "OK"])
        self._tl_table.setStyleSheet(_TABLE_SS)
        self._tl_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tl_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tl_table.setAlternatingRowColors(True)
        self._tl_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch)
        self._tl_table.setColumnWidth(0, 32)   # #
        self._tl_table.setColumnWidth(1, 56)   # T(s)
        self._tl_table.setColumnWidth(2, 32)   # Src
        self._tl_table.setColumnWidth(4, 20)   # ✓
        self._tl_table.verticalHeader().setVisible(False)
        self._tl_table.verticalHeader().setDefaultSectionSize(18)
        self._tl_table.cellDoubleClicked.connect(self._on_seek)
        tll.addWidget(self._tl_table, 1)
        vsp.addWidget(tl_frame)

        # Stats + Log
        rw = QWidget()
        rw.setStyleSheet("background: transparent;")
        rl = QVBoxLayout(rw)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)

        stat_hdr = QLabel("STATISTICS")
        stat_hdr.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
        stat_hdr.setStyleSheet(
            f"color: {_TX_DIM}; background: transparent; letter-spacing: 2px;")
        rl.addWidget(stat_hdr)
        self._stat_lbl = QLabel("—")
        self._stat_lbl.setFont(QFont(_FONT_HMI, 7))
        self._stat_lbl.setStyleSheet(f"color: {_TX_PRI}; background: transparent;")
        self._stat_lbl.setWordWrap(True)
        rl.addWidget(self._stat_lbl)
        rl.addWidget(_hsep_kpit())

        log_hdr = QLabel("EXECUTION LOG")
        log_hdr.setFont(QFont(_FONT_HMI, 7, QFont.Weight.Bold))
        log_hdr.setStyleSheet(
            f"color: {_TX_DIM}; background: transparent; letter-spacing: 2px;")
        rl.addWidget(log_hdr)
        self._log_edit = QTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setFont(QFont(_FONT_HMI, 7))
        self._log_edit.setStyleSheet(
            f"background: {_BG_CARD}; color: {_TX_PRI}; border: 1px solid {_BR_MAIN};"
            f"border-radius: 4px;")
        rl.addWidget(self._log_edit, 1)
        btn_clr = _pill("Clear log", h=24, w=100)
        btn_clr.clicked.connect(self._log_edit.clear)
        rl.addWidget(btn_clr, alignment=Qt.AlignmentFlag.AlignRight)

        vsp.addWidget(rw)
        vsp.setSizes([320, 180])
        lay.addWidget(vsp, 1)

        self._replay_set_controls(False)
        return sec.frame


    # ══════════════════════════════════════════════════════════
    #  SLOTS — AUTO-TRIGGER
    # ══════════════════════════════════════════════════════════
    def _on_trig_enable(self, enabled: bool):
        self._trig_enabled = enabled
        if enabled:
            self._trig_motor_oc = False
            self._trig_pump_oc  = False
            self._alarm_info.setText("Auto-trigger ACTIF — surveillance en cours…")
            self._alarm_info.setStyleSheet("color:#FF8888;background:transparent;")
            self._alarm_banner.setText("  [ON]  AUTO-TRIGGER ACTIF — SURVEILLANCE EN COURS  ")
            self._alarm_banner.setStyleSheet(
                "background:#2A0A0A;color:#FF8888;border-radius:3px;letter-spacing:1px;")
        else:
            self._t_alarm_blink.stop()
            self._t_autostop.stop()
            self._alarm_active = False
            self._alarm_led.set_state(False, "#CC3333")
            self._btn_ack.setEnabled(False)
            self._alarm_info.setText("Auto-trigger disabled")
            self._alarm_info.setStyleSheet("color:#555555;background:transparent;")
            self._alarm_banner.setText("  [!]  OVERCURRENT — WAITING FOR EVENT  ")
            self._alarm_banner.setStyleSheet(
                "background:#FFF0F0;color:#AA5050;border-radius:3px;letter-spacing:1px;")

    def _check_trigger(self, source: str, current_A: float):
        """Vérifie si le seuil est dépassé et déclenche l'alarme si besoin."""
        if not self._trig_enabled:
            return

        # Mise à jour état overcurrent par source
        if source == "motor":
            self._trig_motor_oc = current_A > self._trig_motor_thr
        elif source == "pump":
            self._trig_pump_oc  = current_A > self._trig_pump_thr
        else:
            return

        # Évaluation condition selon mode
        mode = self._trig_mode
        if mode == 0:   # Either : motor OU pompe
            fired = self._trig_motor_oc or self._trig_pump_oc
        elif mode == 1: # Motor seul
            fired = self._trig_motor_oc
        elif mode == 2: # Pompe seule
            fired = self._trig_pump_oc
        else:           # Both : motor ET pompe
            fired = self._trig_motor_oc and self._trig_pump_oc

        if fired and not self._alarm_active:
            self._fire_alarm(source, current_A)

    def _fire_alarm(self, source: str, current_A: float):
        """Déclenche l'enregistrement + alarme visuelle/sonore."""
        self._alarm_active = True

        # Démarrer l'enregistrement si pas déjà actif
        if not self._rec.is_active():
            self._on_rec()

        # Message d'événement
        ts  = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        src = "MOTOR" if source == "motor" else "POMPE"
        thr = self._trig_motor_thr if source == "motor" else self._trig_pump_thr
        msg = f"[{ts}]  OVERCURRENT {src}  {current_A:.2f} A  > {thr:.1f} A"
        self._alarm_events.append(msg)

        # Bandeau rouge clignotant
        self._alarm_banner.setText(
            f"  🔴  OVERCURRENT {src}  {current_A:.2f} A > threshold {thr:.1f} A  —  {ts}  ")
        self._alarm_led.set_state(True, "#CC3333")
        self._btn_ack.setEnabled(True)
        self._alarm_info.setText(msg)
        self._alarm_info.setStyleSheet("color:#FF4444;font-weight:bold;background:transparent;")

        # Clignotement
        self._t_alarm_blink.start()

        # Auto-stop
        if self._trig_autostop_s > 0:
            self._t_autostop.start(self._trig_autostop_s * 1000)

        # Forcer la mise à jour titre
        self._title_status.setText("🔴 OC!")
        self._title_status.setStyleSheet(
            f"color:#FF4444;letter-spacing:1px;background:transparent;font-weight:bold;")

        # Notifier la StatusBar (visible depuis tous les onglets)
        self.trigger_fired.emit(msg)

    def _on_alarm_blink(self):
        """Alterne couleur du bandeau alarme."""
        self._alarm_blink_state = not self._alarm_blink_state
        if self._alarm_blink_state:
            self._alarm_banner.setStyleSheet(
                "background:#CC0000;color:#FFFFFF;border-radius:3px;"
                "letter-spacing:1px;font-weight:bold;")
        else:
            self._alarm_banner.setStyleSheet(
                "background:#3A0000;color:#FF4444;border-radius:3px;"
                "letter-spacing:1px;font-weight:bold;")

    def _on_autostop(self):
        """Arrête automatiquement l'enregistrement après le délai configuré."""
        if self._rec.is_active():
            self._on_stop_rec()
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._alarm_info.setText(
            f"[{ts}]  Auto-stop triggered after {self._trig_autostop_s} s")

    def _on_ack_alarm(self):
        """Acquittement alarme : arrête le clignotement, remet en veille."""
        self._alarm_active   = False
        self._trig_motor_oc  = False
        self._trig_pump_oc   = False
        self._t_alarm_blink.stop()
        self._t_autostop.stop()
        self._alarm_led.set_state(False, "#CC3333")
        self._btn_ack.setEnabled(False)
        self._alarm_banner.setText("  [ON]  AUTO-TRIGGER ACTIF — SURVEILLANCE EN COURS  ")
        self._alarm_banner.setStyleSheet(
            "background:#2A0A0A;color:#FF8888;border-radius:3px;letter-spacing:1px;")
        self._alarm_info.setText(
            f"Alarm acknowledged — {len(self._alarm_events)} event(s) recorded")
        self._alarm_info.setStyleSheet("color:#888888;background:transparent;")
        if self._rec.is_active():
            self._title_status.setText("REC")
            self._title_status.setStyleSheet(
                f"color:{_REC_RED};letter-spacing:1px;background:transparent;")
        else:
            self._title_status.setText("IDLE")
            self._title_status.setStyleSheet(
                f"color:{_TX_DIM};letter-spacing:1px;background:transparent;")

        # Notifier la StatusBar
        self.trigger_cleared.emit()

    # ══════════════════════════════════════════════════════════
    #  SLOTS — DATA SAVE
    # ══════════════════════════════════════════════════════════
    def _on_rec(self):
        self._rec.start()
        self._btn_rec.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._btn_rec.setStyleSheet(
            self._btn_rec.styleSheet() +
            f"QPushButton {{ color: {_REC_RED}; border-color: {_REC_RED}; }}"
        )
        for led in self._src_leds.values():
            led.set_state(True, _KG)
        self._lbl_elapsed.setStyleSheet(f"color: {_REC_RED}; background: transparent;")
        self._sb_rec.setText("REC  |  RECORDING")
        self._sb_rec.setStyleSheet(
            f"color: {_REC_RED}; font-family: '{_FONT_HMI}'; font-size: 7pt; background: transparent;")
        self._title_status.setText("REC")
        self._title_status.setStyleSheet(
            f"color: {_REC_RED}; letter-spacing: 1px; background: transparent;")

    def _on_stop_rec(self):
        self._rec.stop()
        self._btn_rec.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._btn_rec.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {_KG};
                border: 1px solid {_KG_GLOW}; border-radius: 4px;
                padding: 0 14px; letter-spacing: 0.5px;
            }}
            QPushButton:hover  {{ background: {_KG_DIM}; border-color: {_KG}; color: {_TX_PRI}; }}
            QPushButton:pressed{{ background: {_KG_GLOW}; }}
            QPushButton:disabled{{ color: #AAAAAA; border-color: #CCCCCC; }}
        """)
        for led in self._src_leds.values():
            led.set_state(False, _KG)
        self._lbl_elapsed.setStyleSheet(f"color: {_TX_DIM}; background: transparent;")
        self._sb_rec.setText("REC  --  stopped")
        self._sb_rec.setStyleSheet(
            f"color: {_TX_DIM}; font-family: '{_FONT_HMI}'; font-size: 7pt; background: transparent;")
        self._title_status.setText("IDLE")
        self._title_status.setStyleSheet(
            f"color: {_TX_DIM}; letter-spacing: 1px; background: transparent;")

    def _on_clear_rec(self):
        self._rec.clear()
        self._preview_table.setRowCount(0)
        self._lbl_total.setText("0 rows")
        self._lbl_preview_cnt.setText("0 visible")
        self._prog_buf.setValue(0)
        self._lbl_buf.setText(f"0 / {MAX_BUFFER:,}")
        for cnt in self._src_counts.values():
            cnt.setText("0")
        self._lbl_export_status.setText("")
        self._lbl_elapsed.setText("00:00:00")

    def _on_pause_preview(self, paused: bool):
        self._preview_paused = paused
        self._btn_pause_preview.setText("Resume" if paused else "Preview")

    def _on_row_added(self, row: dict):
        if not self._preview_paused:
            self._add_preview_row(row)

    def _add_preview_row(self, row: dict):
        src = row.get("source", "?")
        ts  = row.get("timestamp", "")
        tag = _SRC_TAG.get(src, src.upper()[:3])
        if src == "motor":
            k1 = f"state={row.get('state','?')}"
            k2 = f"F={row.get('front','?')} R={row.get('rear','?')}"
            k3 = f"I={row.get('current','?')}A"
        elif src == "lin":
            k1 = f"{row.get('lin_type','?')} pid={row.get('pid','?')}"
            k2 = f"op={row.get('op','?')} w={row.get('wiper_op','?')}"
            k3 = f"flt={row.get('fault','?')}"
        elif src == "can":
            k1 = f"id={row.get('can_id','?')} {row.get('direction','?')}"
            k2 = f"dlc={row.get('dlc','?')} {str(row.get('payload',''))[:18]}"
            k3 = ""
        elif src == "pump":
            k1 = f"st={row.get('state','?')}"
            k2 = f"I={row.get('current','?')}A"
            k3 = f"fl={row.get('flow','?')}"
        else:
            k1 = str(row)[:40]; k2 = k3 = ""

        r = self._preview_table.rowCount()
        self._preview_table.insertRow(r)
        for ci, val in enumerate((ts, tag, k1, k2, k3)):
            item = QTableWidgetItem(str(val))
            item.setForeground(QColor("_TX_PRI" if ci > 1 else _KG))
            self._preview_table.setItem(r, ci, item)
        while self._preview_table.rowCount() > _PREVIEW_MAX:
            self._preview_table.removeRow(0)
        self._preview_table.scrollToBottom()
        self._lbl_preview_cnt.setText(f"{self._preview_table.rowCount()} visible")

    def _on_export_csv(self):
        if self._rec.row_count() == 0:
            self._show_export_status("Buffer empty.", warn=True)
            return
        choice = self._exp_combo.currentIndex()
        ts_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        if choice == 5:
            folder = QFileDialog.getExistingDirectory(
                self, "Export folder", os.path.expanduser("~"))
            if not folder:
                return
            results = self._rec.export_per_source(folder, f"wipewash_{ts_str}")
            total = sum(results.values())
            self._show_export_status(
                f"OK  {total} rows  |  {len(results)} files: "
                + "  ".join(f"[{_SRC_TAG.get(s,s)}]={n}" for s, n in results.items()))
        else:
            src_map = {0: "all", 1: "motor", 2: "lin", 3: "can", 4: "pump"}
            src = src_map.get(choice, "all")
            label = src if src != "all" else "merged"
            path, _ = QFileDialog.getSaveFileName(
                self, "Save CSV",
                os.path.join(os.path.expanduser("~"), f"wipewash_{ts_str}_{label}.csv"),
                "CSV (*.csv)")
            if not path:
                return
            n = self._rec.export_csv(path, src)
            if n:
                self._show_export_status(f"OK  {n:,} rows → {os.path.basename(path)}")
            else:
                self._show_export_status("No data for this filter.", warn=True)

    def _on_export_mdf(self):
        if self._rec.row_count() == 0:
            self._show_export_status("Buffer empty.", warn=True)
            return
        folder = QFileDialog.getExistingDirectory(
            self, "MDF4 folder", os.path.expanduser("~"))
        if not folder:
            return
        try:
            exp = MDFExporter(bench_id="WipeWash-Bench", project="WipeWash Automotive HIL")
            ts_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = exp.export(self._rec, output_dir=folder, base_name=f"wipewash_{ts_str}")
            if path:
                self._show_export_status(f"MDF4 OK → {os.path.basename(path)}")
            else:
                self._show_export_status("MDF4 export failed.", warn=True)
        except Exception as e:
            self._show_export_status(f"MDF4 error : {e}", warn=True)

    def _show_export_status(self, msg: str, warn: bool = False):
        c = _TX_DIM if not warn else "#AA4400"
        self._lbl_export_status.setText(msg)
        self._lbl_export_status.setStyleSheet(
            f"color: {c}; background: transparent; font-family: '{_FONT_HMI}'; font-size: 7pt;")

    def _refresh_stats(self):
        rows = self._rec.get_rows()
        total = len(rows)
        counts = {"motor": 0, "lin": 0, "can": 0, "pump": 0}
        for r in rows:
            s = r.get("source", "")
            if s in counts:
                counts[s] += 1
        for src, cnt in self._src_counts.items():
            cnt.setText(f"{counts[src]:,}")
        self._lbl_total.setText(f"{total:,} rows")
        self._prog_buf.setValue(min(total, MAX_BUFFER))
        self._lbl_buf.setText(f"{total:,} / {MAX_BUFFER:,}")
        self._sb_rows.setText(f"{total:,} rows buffered")
        if self._rec.is_active():
            e = self._rec.elapsed()
            h, m, s = int(e // 3600), int((e % 3600) // 60), int(e % 60)
            self._lbl_elapsed.setText(f"{h:02d}:{m:02d}:{s:02d}")

    # ══════════════════════════════════════════════════════════
    #  SLOTS — REPLAY
    # ══════════════════════════════════════════════════════════
    def _replay_set_controls(self, loaded: bool):
        self._btn_play.setEnabled(loaded)
        self._btn_pause.setEnabled(False)
        self._btn_rstop.setEnabled(False)

    def _replay_set_playing(self, playing: bool):
        self._btn_play.setEnabled(not playing)
        self._btn_pause.setEnabled(playing)
        self._btn_rstop.setEnabled(True)

    def _on_load_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load CSV scenario", "", "CSV (*.csv);;All (*)")
        if path:
            self._csv_path = path
            self._load_csv(path)

    def _on_load_mdf4(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load MDF4 scenario", "",
            "MDF4 (*.mf4 *.MF4);;All (*)")
        if not path:
            return
        sources = {s for s, c in self._rpl_chk.items() if c.isChecked()}
        rows, err = Mdf4ScenarioLoader.load(path, sources or None)
        if err:
            self._replay_log(f"ERROR  {err}")
            return
        self._rows = rows
        self._csv_path = path
        self._engine.load(rows)
        self._populate_timeline(rows)
        self._update_replay_stats(rows)
        self._replay_set_controls(True)
        self._prog_replay.setValue(0)
        self._lbl_rprog.setText(f"0 / {len(rows)}")
        fname = os.path.basename(path)
        self._file_badge.setText(fname)
        self._file_badge.setStyleSheet(
            f"color: {_KG}; background: {_KG_DIM}; border: 1px solid {_KG_GLOW};"            f"border-radius: 3px; padding: 1px 5px; font-family: '{_FONT_HMI}'; font-size: 7pt;")
        self._replay_log(f"LOADED MDF4  {fname}  ({len(rows)} steps)")
        self._sb_replay.setText(f"REPLAY MDF4  |  {fname}")
        self._sb_replay.setStyleSheet(
            f"color: {_KG}; font-family: '{_FONT_HMI}'; font-size: 7pt; background: transparent;")

    def _load_csv(self, path: str):
        sources = {s for s, c in self._rpl_chk.items() if c.isChecked()}
        rows, err = CsvScenarioLoader.load(path, sources or None)
        if err:
            self._replay_log(f"ERROR  {err}")
            return
        self._rows = rows
        self._engine.load(rows)
        self._populate_timeline(rows)
        self._update_replay_stats(rows)
        self._replay_set_controls(True)
        self._prog_replay.setValue(0)
        self._lbl_rprog.setText(f"0 / {len(rows)}")
        fname = os.path.basename(path)
        self._file_badge.setText(fname)
        self._file_badge.setStyleSheet(
            f"color: {_KG}; background: {_KG_DIM}; border: 1px solid {_KG_GLOW};"
            f"border-radius: 3px; padding: 1px 5px; font-family: '{_FONT_HMI}'; font-size: 7pt;")
        self._replay_log(f"LOADED  {fname}  ({len(rows)} steps)")
        self._sb_replay.setText(f"REPLAY  |  {fname}")
        self._sb_replay.setStyleSheet(
            f"color: {_KG}; font-family: '{_FONT_HMI}'; font-size: 7pt; background: transparent;")

    def _on_play(self):
        if self._engine._paused:
            self._engine.resume()
        else:
            self._engine.start()
        self._replay_set_playing(True)

    def _on_pause_replay(self):
        self._engine.pause()
        self._btn_play.setEnabled(True)
        self._btn_pause.setEnabled(False)

    def _on_stop_replay(self):
        self._engine.stop()
        self._replay_set_controls(bool(self._rows))
        self._replay_set_playing(False)
        self._btn_play.setEnabled(bool(self._rows))
        self._prog_replay.setValue(0)

    def _on_seek(self, row_idx: int, _: int):
        if self._engine._running:
            self._engine.seek(row_idx)
            self._replay_log(f"SEEK  step {row_idx}")

    def _on_step_fired(self, idx: int):
        self._tl_table.selectRow(idx)
        self._tl_table.scrollTo(self._tl_table.model().index(idx, 0))
        item = QTableWidgetItem("OK")
        item.setForeground(QColor(_KG))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tl_table.setItem(idx, 4, item)
        if self._rows:
            self._lbl_rtime.setText(f"{self._rows[idx].t_rel:.1f} s")

    def _on_replay_finished(self):
        self._replay_set_playing(False)
        self._btn_play.setEnabled(bool(self._rows))
        self._prog_replay.setValue(100)
        self._replay_log("DONE  — all steps injected")

    def _on_progress(self, done: int, total: int):
        pct = int(done / total * 100) if total else 0
        self._prog_replay.setValue(pct)
        self._lbl_rprog.setText(f"{done} / {total}")

    def _populate_timeline(self, rows: list[ScenarioRow]):
        self._tl_table.setRowCount(0)
        self._tl_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            tag = _SRC_TAG.get(row.source, row.source.upper()[:3])

            def _item(text, align=Qt.AlignmentFlag.AlignLeft,
                      c="_TX_PRI") -> QTableWidgetItem:
                it = QTableWidgetItem(str(text))
                it.setForeground(QColor(c))
                it.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
                return it

            self._tl_table.setItem(i, 0, _item(str(i), Qt.AlignmentFlag.AlignCenter, _TX_DIM))
            self._tl_table.setItem(i, 1, _item(f"{row.t_rel:.3f}", Qt.AlignmentFlag.AlignRight))
            self._tl_table.setItem(i, 2, _item(tag, Qt.AlignmentFlag.AlignCenter, _KG))
            self._tl_table.setItem(i, 3, _item(row.summary))
            self._tl_table.setItem(i, 4, _item("·", Qt.AlignmentFlag.AlignCenter, _TX_DIM))
        self._tl_table.resizeRowsToContents()

    def _update_replay_stats(self, rows: list[ScenarioRow]):
        if not rows:
            self._stat_lbl.setText("—")
            return
        dur = rows[-1].t_rel - rows[0].t_rel if len(rows) > 1 else 0.0
        cnt = {s: sum(1 for r in rows if r.source == s)
               for s in ("motor", "lin", "can", "pump")}
        fname = os.path.basename(self._csv_path)
        lines = [
            f"File    : {fname}",
            f"Steps   : {len(rows)}",
            f"Duration: {dur:.2f} s",
            f"Mode    : Virtual ECU", "",
        ]
        for src, n in cnt.items():
            if n:
                lines.append(f"  [{_SRC_TAG[src]}]  {n} events")
        self._stat_lbl.setText("\n".join(lines))

    def _replay_log(self, msg: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._log_edit.append(f"[{ts}]  {msg}")
        sb = self._log_edit.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ══════════════════════════════════════════════════════════
    #  API PUBLIQUE
    # ══════════════════════════════════════════════════════════
    def on_motor_data(self, data: dict):
        self._rec.push_motor(data)
        # Calcul courant moteur total pour le trigger
        try:
            f = data.get("front", {})
            r = data.get("rear",  {})
            if isinstance(f, dict):
                cur = float(f.get("motor_current", 0)) + float(r.get("motor_current", 0))
            else:
                cur = float(data.get("current", 0))
            self._check_trigger("motor", cur)
        except Exception:
            pass

    def on_lin_event(self, ev: dict):
        self._rec.push_lin(ev)

    def on_can_event(self, ev: dict):
        self._rec.push_can(ev)

    def on_pump_data(self, data: dict):
        self._rec.push_pump(data)
        # Courant pompe pour le trigger
        try:
            cur = float(data.get("pump_current", data.get("current", 0)))
            self._check_trigger("pump", cur)
        except Exception:
            pass

    def connect_panels(self, motor_panel=None, pump_panel=None, veh_panel=None,
                       crslin_panel=None, can_panel=None, car_html=None,
                       car_html_mp=None, main_window=None):
        self._engine.ecu.connect_panels(
            motor_panel=motor_panel, pump_panel=pump_panel,
            veh_panel=veh_panel, crslin_panel=crslin_panel,
            can_panel=can_panel, car_html=car_html,
            car_html_mp=car_html_mp, main_window=main_window,
        )
        self._replay_log("VirtualECU connected to all platform components")

    def set_rte_client(self, rte_client):
        self._engine._rte = rte_client