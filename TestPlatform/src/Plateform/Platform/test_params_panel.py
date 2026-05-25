"""
test_params_panel.py  —  Éditeur de paramètres des tests WipeWash
==================================================================
Design ControlDesk / oscilloscope industriel — dark theme, cartes
de paramètres avec gauge de déviation visuelle, animations confirmées.
"""

from __future__ import annotations

import copy
import json
import time

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit,
    QFrame, QSizePolicy, QScrollArea, QSplitter,
    QSpinBox, QDoubleSpinBox, QFileDialog, QMessageBox,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import (
    QColor, QFont, QPainter, QPen, QBrush,
)

import test_cases as _tc
from constants import (
    FONT_UI, FONT_MONO,
    W_BG, W_PANEL, W_PANEL2, W_PANEL3, W_TOOLBAR, W_TITLEBAR,
    W_BORDER, W_BORDER2,
    W_TEXT, W_TEXT2, W_TEXT_DIM, W_TEXT_HDR,
    A_AMBER, A_TEAL, A_RED, KPIT_GREEN,
)

# ─── Palette — harmonisée avec la plateforme WipeWash (fond blanc/vert KPIT) ──
_C_BG        = W_BG            # "#FFFFFF"
_C_SURFACE   = W_PANEL         # "#F5FFF0"
_C_SURFACE2  = W_PANEL2        # "#EDF9E3"
_C_BORDER    = "#1A1A1A"       # noir uniforme
_C_ACCENT    = "#1A1A1A"       # barre gauche noire — toutes les cartes
_C_GREEN     = "#2E7003"       # bouton APPLIQUER — vert foncé KPIT
_C_AMBER     = A_AMBER         # "#F39C12"
_C_BLUE      = "#007ACC"       # A_TEAL plateforme
_C_RED       = "#C0392B"       # A_RED plateforme
_C_TEXT      = W_TEXT          # "#1A1A1A"
_C_DIM       = W_TEXT_DIM      # "#5A6A4A"
_C_MODIFIED  = "#FFF9C4"       # jaune très pâle
_C_BORDER_MOD = A_AMBER

# Couleur du badge texte uniquement — fond carte toujours W_PANEL
_CAT_COLOR = {
    "CYCLE":           "#7A3100",   # orange foncé
    "TIMEOUT":         "#791F1F",   # rouge foncé
    "FONCTIONNEL":     "#0C447C",   # bleu foncé
    "FONCTIONNEL_BCM": "#3C3489",   # violet foncé
    "FONCTIONNEL_WC":  "#085041",   # teal foncé
    "LIN_SECURITE":    "#633806",   # ambre foncé
    "CAN_SECURITE":    "#444441",   # gris foncé
}

_CAT_BG = {
    "CYCLE":           "#FFF3E0",   # orange pâle
    "TIMEOUT":         "#FCEBEB",   # rouge pâle
    "FONCTIONNEL":     "#E6F1FB",   # bleu pâle
    "FONCTIONNEL_BCM": "#EEEDFE",   # violet pâle
    "FONCTIONNEL_WC":  "#E1F5EE",   # teal pâle
    "LIN_SECURITE":    "#FAEEDA",   # ambre pâle
    "CAN_SECURITE":    "#F1EFE8",   # gris pâle
}


def _collect_defaults() -> dict:
    defaults = {}
    for cls in _tc.ALL_TESTS:
        defaults[cls.__name__] = {
            "ID"            : getattr(cls, "ID", ""),
            "NAME"          : getattr(cls, "NAME", ""),
            "CATEGORY"      : getattr(cls, "CATEGORY", ""),
            "LIMIT_MS"      : getattr(cls, "LIMIT_MS", None),
            "TOL_MS"        : getattr(cls, "TOL_MS", None),
            "MIN_MS"        : getattr(cls, "MIN_MS", None),
            "TEST_TIMEOUT_S": getattr(cls, "TEST_TIMEOUT_S", None),
        }
    return defaults


# ════════════════════════════════════════════════════════════════════════
#  DeviationGauge — mini arc circulaire de déviation
# ════════════════════════════════════════════════════════════════════════
class DeviationGauge(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pct   = 0.0
        self._color = _C_GREEN
        self.setFixedSize(42, 42)
        self.setStyleSheet("background: transparent;")

    def set_deviation(self, original, current):
        if original is None or current is None or original == 0:
            self._pct = 0.0
            self._color = _C_GREEN
        else:
            ratio = (current - original) / abs(original)
            self._pct = max(-1.0, min(1.0, ratio))
            if abs(self._pct) < 0.05:
                self._color = _C_GREEN
            elif abs(self._pct) < 0.25:
                self._color = _C_AMBER
            else:
                self._color = _C_RED
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        cx, cy, r = W // 2, H // 2, min(W, H) // 2 - 4

        p.setPen(QPen(QColor("#1A1A1A"), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        if abs(self._pct) > 0.01:
            pen = QPen(QColor(self._color), 3.2,
                       Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            start = 90 * 16
            span  = int(-self._pct * 180 * 16)
            p.drawArc(cx - r, cy - r, r * 2, r * 2, start, span)

        pct_int = int(abs(self._pct) * 100)
        sign    = "+" if self._pct > 0.01 else ("-" if self._pct < -0.01 else "")
        txt     = f"{sign}{pct_int}%" if pct_int > 0 else "OK"
        p.setPen(QColor(self._color))
        p.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
        p.drawText(0, 0, W, H, Qt.AlignmentFlag.AlignCenter, txt)


# ════════════════════════════════════════════════════════════════════════
#  ParamCard — carte par test
# ════════════════════════════════════════════════════════════════════════
class ParamCard(QFrame):
    value_changed = Signal(str, str, object)

    def __init__(self, cls_name, data, origin, parent=None):
        super().__init__(parent)
        self._cls_name = cls_name
        self._origin   = origin
        self._data     = data
        self._gauges: dict[str, DeviationGauge] = {}
        self._spins:  dict[str, QWidget]        = {}
        self._build()

    def _cat_color(self):
        return _CAT_COLOR.get(self._data.get("CATEGORY", ""), _C_DIM)

    def _build(self):
        cat_c  = self._cat_color()
        cat_bg = _CAT_BG.get(self._data.get("CATEGORY", ""), "rgba(141,198,63,0.2)")
        self.setStyleSheet(f"""
            QFrame {{
                background: {cat_bg};
                border: 2px solid #1A1A1A;
                border-radius: 6px;
            }}
            QFrame:hover {{
                background: {cat_bg};
                border: 2px solid #1A1A1A;
            }}
        """)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        # Header
        hdr = QHBoxLayout(); hdr.setSpacing(8)

        id_lbl = QLabel(self._data.get("ID", "??") or "??")
        id_lbl.setFixedWidth(36)
        id_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        id_lbl.setStyleSheet(f"""
            background: {_C_SURFACE2}; color: {_C_TEXT};
            border: 2px solid {_C_BORDER}; border-radius: 3px;
            font-family: '{FONT_MONO}'; font-size: 10pt; font-weight: bold;
            padding: 2px 2px;
        """)
        hdr.addWidget(id_lbl)

        name_lbl = QLabel(self._data.get("NAME", ""))
        name_lbl.setStyleSheet(
            f"color: {_C_TEXT}; font-family: '{FONT_UI}'; font-size: 11pt;"
            "font-weight: 600; background: transparent;")
        name_lbl.setWordWrap(True)
        hdr.addWidget(name_lbl, 1)

        cat_badge = QLabel(self._data.get("CATEGORY", ""))
        cat_badge.setStyleSheet(f"""
            background: transparent; color: #1A1A1A;
            border: 2px solid #1A1A1A; border-radius: 3px;
            font-family: '{FONT_MONO}'; font-size: 9pt; font-weight: bold;
            padding: 2px 6px;
        """)
        hdr.addWidget(cat_badge)
        root.addLayout(hdr)

        # Paramètres
        params_row = QHBoxLayout(); params_row.setSpacing(10)
        params_row.setContentsMargins(0, 2, 0, 0)

        params = [
            ("LIMIT_MS",       "LIMIT",   "ms", 1, 99999),
            ("TOL_MS",         "TOL ±",   "ms", 0, 9999),
            ("MIN_MS",         "MIN",     "ms", 0, 99999),
            ("TEST_TIMEOUT_S", "TIMEOUT", "s",  1, 300),
        ]

        has_param = False
        for key, label, unit, mn, mx in params:
            val  = self._data.get(key)
            orig = self._origin.get(key)
            if val is None:
                continue
            has_param = True

            cell = QVBoxLayout(); cell.setSpacing(2)

            lbl = QLabel(label)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"color: {_C_TEXT}; font-family: '{FONT_MONO}'; font-size: 9pt;"
                "letter-spacing: 2px; background: transparent;")
            cell.addWidget(lbl)

            if isinstance(val, float) and val != int(val):
                spin = QDoubleSpinBox()
                spin.setDecimals(1)
                spin.setRange(float(mn), float(mx))
                spin.setValue(float(val))
            else:
                spin = QSpinBox()
                spin.setRange(mn, mx)
                spin.setValue(int(val))

            spin.setSuffix(f" {unit}")
            spin.setFixedWidth(92)
            spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            spin.setStyleSheet(self._spin_style(orig, val))

            def _on_change(v, k=key, s=spin, o=orig):
                self._data[k] = v
                modified = (o is not None and v != o)
                s.setStyleSheet(self._spin_style_mod(modified))
                if k in self._gauges:
                    self._gauges[k].set_deviation(o, v)
                any_mod = any(
                    self._data.get(kk) != self._origin.get(kk)
                    for kk in ("LIMIT_MS", "TOL_MS", "MIN_MS", "TEST_TIMEOUT_S")
                    if self._data.get(kk) is not None
                )
                self._mark_modified(any_mod)
                self.value_changed.emit(self._cls_name, k, v)

            spin.valueChanged.connect(_on_change)
            self._spins[key] = spin
            cell.addWidget(spin, 0, Qt.AlignmentFlag.AlignCenter)

            gauge = DeviationGauge()
            gauge.set_deviation(orig, val)
            self._gauges[key] = gauge
            cell.addWidget(gauge, 0, Qt.AlignmentFlag.AlignCenter)

            params_row.addLayout(cell)

        if not has_param:
            none_lbl = QLabel("— aucun paramètre numérique —")
            none_lbl.setStyleSheet(f"color: {_C_DIM}; font-size: 10pt; background: transparent;")
            params_row.addWidget(none_lbl)

        params_row.addStretch()
        root.addLayout(params_row)

    def _spin_style(self, orig, current):
        modified = (orig is not None and current != orig)
        return self._spin_style_mod(modified)

    def _spin_style_mod(self, modified: bool):
        cat_bg = _CAT_BG.get(self._data.get("CATEGORY", ""), "rgba(141,198,63,0.2)")
        border = _C_BORDER_MOD if modified else "#1A1A1A"
        bg     = _C_MODIFIED   if modified else cat_bg
        fg     = _C_AMBER      if modified else _C_TEXT
        return f"""
            QSpinBox, QDoubleSpinBox {{
                background: {bg}; color: {fg};
                border: 2px solid {border}; border-radius: 3px;
                font-family: '{FONT_MONO}'; font-size: 11pt; font-weight: bold;
                padding: 2px 2px;
            }}
            QSpinBox::up-button, QDoubleSpinBox::up-button,
            QSpinBox::down-button, QDoubleSpinBox::down-button {{
                background: {cat_bg}; border: none; width: 12px;
            }}
        """

    def _mark_modified(self, modified: bool):
        cat_bg = _CAT_BG.get(self._data.get("CATEGORY", ""), "rgba(141,198,63,0.2)")
        bg  = "#FFFDE7" if modified else cat_bg
        brd = "#F39C12" if modified else "#1A1A1A"
        self.setStyleSheet(f"""
            QFrame {{
                background: {bg};
                border: 2px solid #1A1A1A;
                border-radius: 6px;
            }}
            QFrame:hover {{
                background: {bg};
                border: 2px solid #1A1A1A;
            }}
        """)

    def reset_to_origin(self):
        for key, spin in self._spins.items():
            orig = self._origin.get(key)
            if orig is not None:
                spin.blockSignals(True)
                if isinstance(spin, QDoubleSpinBox):
                    spin.setValue(float(orig))
                else:
                    spin.setValue(int(orig))
                spin.setStyleSheet(self._spin_style_mod(False))
                spin.blockSignals(False)
                self._data[key] = orig
                if key in self._gauges:
                    self._gauges[key].set_deviation(orig, orig)
        self._mark_modified(False)


# ════════════════════════════════════════════════════════════════════════
#  CategoryHeader
# ════════════════════════════════════════════════════════════════════════
class CategoryHeader(QFrame):
    def __init__(self, category: str, count: int, parent=None):
        super().__init__(parent)
        cat_c = _CAT_COLOR.get(category, _C_DIM)
        self.setFixedHeight(30)
        cat_bg = _CAT_BG.get(category, "rgba(141,198,63,0.2)")
        self.setStyleSheet(f"""
            QFrame {{
                background: {cat_bg};
                border: 2px solid #1A1A1A;
                border-radius: 2px;
            }}
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        lbl = QLabel(f"  {category}")
        lbl.setStyleSheet(
            f"color: {cat_c}; font-family: '{FONT_MONO}'; font-size: 10.5pt;"
            "font-weight: bold; letter-spacing: 2px; background: transparent;")
        cnt = QLabel(f"{count} test{'s' if count > 1 else ''}")
        cnt.setStyleSheet(
            f"color: {_C_DIM}; font-family: '{FONT_MONO}'; font-size: 10pt;"
            "background: transparent;")
        lay.addWidget(lbl); lay.addStretch(); lay.addWidget(cnt)


# ════════════════════════════════════════════════════════════════════════
#  NSamplesWidget
# ════════════════════════════════════════════════════════════════════════
class NSamplesWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._orig = _tc.N_SAMPLES
        self.setFixedHeight(52)
        self.setStyleSheet(f"""
            QFrame {{
                background: {_C_SURFACE};
                border: 2px solid {_C_BORDER};
                border-radius: 6px;
            }}
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(14)

        icon = QLabel("")
        icon.setStyleSheet(f"font-size: 16pt; background: transparent; color: {_C_GREEN};")
        lay.addWidget(icon)

        desc = QVBoxLayout(); desc.setSpacing(0)
        t = QLabel("N_SAMPLES — Intervalles de cycle collectés")
        t.setStyleSheet(
            f"color: {_C_TEXT}; font-family: '{FONT_UI}'; font-size: 11pt;"
            "font-weight: 600; background: transparent;")
        s = QLabel("Nombre d'intervalles avant validation PASS/FAIL sur tests de cycle")
        s.setStyleSheet(
            f"color: {_C_DIM}; font-family: '{FONT_UI}'; font-size: 10pt;"
            "background: transparent;")
        desc.addWidget(t); desc.addWidget(s)
        lay.addLayout(desc, 1)

        self._spin = QSpinBox()
        self._spin.setRange(5, 200)
        self._spin.setValue(_tc.N_SAMPLES)
        self._spin.setFixedSize(100, 32)
        self._spin.setSuffix(" trames")
        self._spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._spin.setStyleSheet(self._spin_style(False))
        self._spin.valueChanged.connect(self._on_change)
        lay.addWidget(self._spin)

        reset = QPushButton("")
        reset.setFixedSize(32, 32)
        reset.setStyleSheet(f"""
            QPushButton {{
                background: {_C_SURFACE2}; color: {_C_DIM};
                border: 2px solid {_C_BORDER}; border-radius: 2px; font-size: 15pt;
            }}
            QPushButton:hover {{ color: {_C_TEXT}; border-color: {_C_GREEN}; }}
        """)
        reset.clicked.connect(lambda: self._spin.setValue(self._orig))
        lay.addWidget(reset)

    def _spin_style(self, modified: bool):
        c  = _C_AMBER if modified else _C_GREEN
        bg = "#FFFDE7" if modified else _C_SURFACE2
        return f"""
            QSpinBox {{
                background: {bg}; color: {c};
                border: 2px solid {c}44; border-radius: 2px;
                font-family: '{FONT_MONO}'; font-size: 13pt; font-weight: bold;
            }}
            QSpinBox::up-button, QSpinBox::down-button {{
                background: {_C_SURFACE}; border: none; width: 16px;
            }}
        """

    def _on_change(self, v: int):
        self._spin.setStyleSheet(self._spin_style(v != self._orig))

    def value(self): return self._spin.value()
    def reset(self): self._spin.setValue(self._orig)


# ════════════════════════════════════════════════════════════════════════
#  StatusBanner
# ════════════════════════════════════════════════════════════════════════
class StatusBanner(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        self._reset_style = f"""
            QFrame {{ background: {_C_SURFACE}; border-radius: 2px;
                      border: 2px solid {_C_BORDER}; }}
        """
        self.setStyleSheet(self._reset_style)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        self._icon = QLabel("")
        self._icon.setStyleSheet(f"color: {_C_DIM}; font-size: 12pt; background: transparent;")
        self._text = QLabel("Prêt")
        self._text.setStyleSheet(
            f"color: {_C_DIM}; font-family: '{FONT_MONO}'; font-size: 11pt; background: transparent;")
        lay.addWidget(self._icon); lay.addWidget(self._text); lay.addStretch()
        self._timer = QTimer(self); self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._reset)

    def show_message(self, msg: str, color: str, icon: str = "", duration=4000):
        self._icon.setText(icon)
        self._icon.setStyleSheet(f"color: {color}; font-size: 12pt; background: transparent;")
        self._text.setText(msg)
        self._text.setStyleSheet(
            f"color: {color}; font-family: '{FONT_MONO}'; font-size: 11pt;"
            "font-weight: bold; background: transparent;")
        self.setStyleSheet(f"""
            QFrame {{ background: {color}11; border-radius: 2px; border: 2px solid {color}44; }}
        """)
        if duration > 0:
            self._timer.start(duration)

    def _reset(self):
        self.setStyleSheet(self._reset_style)
        self._icon.setStyleSheet(f"color: {_C_DIM}; font-size: 12pt; background: transparent;")
        self._icon.setText("")
        self._text.setStyleSheet(
            f"color: {_C_DIM}; font-family: '{FONT_MONO}'; font-size: 11pt; background: transparent;")
        self._text.setText("Prêt")


# ════════════════════════════════════════════════════════════════════════
#  StatsPanel
# ════════════════════════════════════════════════════════════════════════
class StatsPanel(QFrame):
    def __init__(self, total: int, parent=None):
        super().__init__(parent)
        from collections import Counter
        self._total = total
        self._modified_val = 0
        self.setFixedWidth(180)
        self.setStyleSheet(f"""
            QFrame {{
                background: {_C_SURFACE}; border: 2px solid {_C_BORDER};
                border-radius: 6px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(10)

        t = QLabel("STATISTIQUES")
        t.setStyleSheet(
            f"color: {_C_DIM}; font-family: '{FONT_MONO}'; font-size: 9pt;"
            "letter-spacing: 2px; background: transparent;")
        lay.addWidget(t)

        self._stat_lbls: dict[str, QLabel] = {}
        cats = Counter(getattr(cls, "CATEGORY", "") for cls in _tc.ALL_TESTS)
        fonct = cats.get("FONCTIONNEL", 0) + cats.get("FONCTIONNEL_BCM", 0)

        items = [
            ("total",    "TOTAL",      str(total),               _C_TEXT),
            ("modified", "MODIFIÉS",   "0",                      _C_AMBER),
            ("cycle",    "CYCLE",      str(cats.get("CYCLE",0)), _C_GREEN),
            ("timeout",  "TIMEOUT",    str(cats.get("TIMEOUT",0)),_C_RED),
            ("fonct",    "FONCTIONNEL",str(fonct),               _C_BLUE),
        ]
        for key, label, val, color in items:
            row = QVBoxLayout(); row.setSpacing(1)
            v_lbl = QLabel(val)
            v_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v_lbl.setStyleSheet(
                f"color: {color}; font-family: '{FONT_MONO}'; font-size: 19pt;"
                "font-weight: bold; background: transparent;")
            l_lbl = QLabel(label)
            l_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            l_lbl.setStyleSheet(
                f"color: {_C_DIM}; font-family: '{FONT_MONO}'; font-size: 8.5pt;"
                "letter-spacing: 2px; background: transparent;")
            row.addWidget(v_lbl); row.addWidget(l_lbl)
            self._stat_lbls[key] = v_lbl
            lay.addLayout(row)
            if key != "fonct":
                sep = QFrame(); sep.setFixedHeight(1)
                sep.setStyleSheet(f"background: {_C_BORDER};")
                lay.addWidget(sep)

        lay.addStretch()

    def set_modified(self, count: int):
        self._modified_val = count
        self._stat_lbls["modified"].setText(str(count))
        color = _C_AMBER if count > 0 else _C_DIM
        self._stat_lbls["modified"].setStyleSheet(
            f"color: {color}; font-family: '{FONT_MONO}'; font-size: 19pt;"
            "font-weight: bold; background: transparent;")


# ════════════════════════════════════════════════════════════════════════
#  TestParamsPanel — panneau principal
# ════════════════════════════════════════════════════════════════════════
class TestParamsPanel(QWidget):
    params_applied = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._origin:  dict = _collect_defaults()
        self._current: dict = copy.deepcopy(self._origin)
        self._cards:   dict[str, ParamCard] = {}
        self._cat_filter = "Toutes"
        self.setStyleSheet(f"background: {_C_BG};")
        self._build_ui()
        self._populate_cards()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Topbar ────────────────────────────────────────────────────
        topbar = QFrame()
        topbar.setFixedHeight(52)
        topbar.setStyleSheet(f"""
            QFrame {{ background: {_C_SURFACE}; border-bottom: 2px solid {_C_BORDER}; }}
        """)
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(18, 0, 18, 0)
        tb.setSpacing(16)

        icon = QLabel("")
        icon.setStyleSheet(f"color: {_C_GREEN}; font-size: 20pt; background: transparent;")
        tb.addWidget(icon)

        col = QVBoxLayout(); col.setSpacing(0)
        t1 = QLabel("PARAMS TESTS")
        t1.setStyleSheet(
            f"color: {_C_TEXT}; font-family: '{FONT_MONO}'; font-size: 13pt;"
            "font-weight: bold; letter-spacing: 3px; background: transparent;")
        t2 = QLabel("Éditeur de paramètres — WipeWash HIL Test Bench")
        t2.setStyleSheet(
            f"color: {_C_DIM}; font-family: '{FONT_UI}'; font-size: 10pt; background: transparent;")
        col.addWidget(t1); col.addWidget(t2)
        tb.addLayout(col); tb.addStretch()

        for label, color, slot in [
            ("APPLIQUER",     _C_GREEN,  self._apply_params),
            ("RÉINITIALISER", _C_DIM,    self._reset_params),
            ("EXPORTER",     _C_BLUE,   self._export_json),
            ("IMPORTER",     "#6A1B9A", self._import_json),
        ]:
            btn = QPushButton(label)
            btn.setFixedHeight(32)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {color}; color: #FFFFFF;
                    border: 1px solid {color}; border-radius: 5px;
                    font-family: '{FONT_MONO}'; font-size: 10.5pt;
                    font-weight: bold; padding: 0 12px; letter-spacing: 0.5px;
                }}
                QPushButton:hover {{ background: {_C_BG}; color: {color}; border: 1px solid {color}; }}
                QPushButton:pressed {{ background: {_C_SURFACE2}; color: {color}; }}
            """)
            btn.clicked.connect(slot)
            tb.addWidget(btn)

        root.addWidget(topbar)

        # ── Splitter body ─────────────────────────────────────────────
        body = QSplitter(Qt.Orientation.Horizontal)
        body.setStyleSheet(f"QSplitter::handle {{ background: {_C_BORDER}; width: 2px; }}")

        # Panneau gauche
        left = QWidget()
        left.setFixedWidth(215)
        left.setStyleSheet(f"background: {_C_SURFACE}; border-right: 2px solid {_C_BORDER};")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(12, 14, 12, 12)
        ll.setSpacing(8)

        s_lbl = QLabel("RECHERCHE")
        s_lbl.setStyleSheet(
            f"color: {_C_DIM}; font-family: '{FONT_MONO}'; font-size: 9pt;"
            "letter-spacing: 2px; background: transparent;")
        ll.addWidget(s_lbl)

        self._search = QLineEdit()
        self._search.setPlaceholderText("ID ou nom…")
        self._search.setFixedHeight(30)
        self._search.setStyleSheet(f"""
            QLineEdit {{
                background: {_C_BG}; color: {_C_TEXT};
                border: 2px solid {_C_BORDER}; border-radius: 2px;
                font-family: '{FONT_MONO}'; font-size: 11pt; padding: 2px 8px;
            }}
            QLineEdit:focus {{ border-color: {_C_GREEN}; }}
        """)
        self._search.textChanged.connect(self._apply_filter)
        ll.addWidget(self._search)

        f_lbl = QLabel("CATÉGORIE")
        f_lbl.setStyleSheet(
            f"color: {_C_DIM}; font-family: '{FONT_MONO}'; font-size: 9pt;"
            "letter-spacing: 2px; background: transparent;")
        ll.addWidget(f_lbl)

        self._cat_btns: list[QPushButton] = []
        cats = ["Toutes"] + sorted(set(
            d["CATEGORY"] for d in self._origin.values() if d.get("CATEGORY")
        ))
        for cat in cats:
            btn = QPushButton(cat)
            btn.setCheckable(True)
            btn.setChecked(cat == "Toutes")
            btn.setFixedHeight(28)
            btn.setProperty("cat", cat)
            btn.setStyleSheet(self._cat_btn_style(cat, cat == "Toutes"))
            btn.clicked.connect(lambda _, b=btn: self._on_cat_btn(b))
            self._cat_btns.append(btn)
            ll.addWidget(btn)

        ll.addSpacing(8)
        self._stats = StatsPanel(len(_tc.ALL_TESTS))
        ll.addWidget(self._stats)
        ll.addStretch()
        body.addWidget(left)

        # Panneau droit
        right = QWidget()
        right.setStyleSheet(f"background: {_C_BG};")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        # NSamples
        self._nsamples = NSamplesWidget()
        nw = QWidget(); nw.setStyleSheet(f"background: {_C_BG};")
        nwl = QHBoxLayout(nw); nwl.setContentsMargins(14, 10, 14, 0)
        nwl.addWidget(self._nsamples)
        rl.addWidget(nw)

        # Scroll
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(f"""
            QScrollArea {{ background: {_C_BG}; border: none; }}
            QScrollBar:vertical {{
                background: {_C_SURFACE}; width: 6px; border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: {_C_BORDER}; border-radius: 3px; min-height: 22px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {_C_GREEN}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        self._container = QWidget()
        self._container.setStyleSheet(f"background: {_C_BG};")
        self._c_lay = QVBoxLayout(self._container)
        self._c_lay.setContentsMargins(14, 10, 14, 14)
        self._c_lay.setSpacing(0)
        self._scroll.setWidget(self._container)
        rl.addWidget(self._scroll, 1)

        # Banner
        self._banner = StatusBanner()
        bw = QWidget(); bw.setStyleSheet(f"background: {_C_BG};")
        bl = QHBoxLayout(bw); bl.setContentsMargins(14, 6, 14, 8)
        bl.addWidget(self._banner)
        rl.addWidget(bw)

        body.addWidget(right)
        body.setSizes([215, 900])
        root.addWidget(body, 1)

    def _populate_cards(self, filter_cat: str = "Toutes", filter_text: str = ""):
        while self._c_lay.count():
            it = self._c_lay.takeAt(0)
            if it.widget():
                it.widget().setParent(None)

        ft = filter_text.lower()
        cats_seen: set = set()

        for cls in _tc.ALL_TESTS:
            name = cls.__name__
            d    = self._current[name]
            cat  = d.get("CATEGORY", "")

            if filter_cat != "Toutes" and cat != filter_cat:
                continue
            if ft and ft not in d.get("ID","").lower() and ft not in d.get("NAME","").lower():
                continue

            if cat not in cats_seen:
                cats_seen.add(cat)
                count = sum(
                    1 for c2 in _tc.ALL_TESTS
                    if (self._current[c2.__name__].get("CATEGORY","") == cat and
                        (filter_cat == "Toutes" or self._current[c2.__name__].get("CATEGORY","") == filter_cat) and
                        (not ft or ft in self._current[c2.__name__].get("ID","").lower()
                         or ft in self._current[c2.__name__].get("NAME","").lower()))
                )
                hdr = CategoryHeader(cat, count)
                w = QWidget(); w.setStyleSheet(f"background: {_C_BG};")
                wl = QVBoxLayout(w)
                wl.setContentsMargins(0, 14 if cats_seen else 4, 0, 6)
                wl.addWidget(hdr)
                self._c_lay.addWidget(w)

            if name not in self._cards:
                card = ParamCard(name, self._current[name], self._origin[name])
                card.value_changed.connect(self._on_value_changed)
                self._cards[name] = card

            self._c_lay.addWidget(self._cards[name])
            self._c_lay.addSpacing(6)

        self._c_lay.addStretch()

    def _on_cat_btn(self, btn: QPushButton):
        cat = btn.property("cat")
        self._cat_filter = cat
        for b in self._cat_btns:
            c = b.property("cat")
            b.setChecked(c == cat)
            b.setStyleSheet(self._cat_btn_style(c, c == cat))
        self._apply_filter()

    def _cat_btn_style(self, cat: str, active: bool) -> str:
        color = _CAT_COLOR.get(cat, _C_DIM)
        if active:
            return f"""
                QPushButton {{
                    background: {_CAT_BG.get(cat, "rgba(141,198,63,0.2)")}; color: {color};
                    border: 2px solid #1A1A1A;
                    border-radius: 3px;
                    font-family: '{FONT_MONO}'; font-size: 10.5pt; font-weight: bold;
                    padding: 0 10px; text-align: left;
                }}
            """
        return f"""
            QPushButton {{
                background: transparent; color: {_C_DIM};
                border: 2px solid transparent; border-radius: 3px;
                font-family: '{FONT_MONO}'; font-size: 10.5pt;
                padding: 0 10px; text-align: left;
            }}
            QPushButton:hover {{ color: {_C_TEXT}; background: {_C_SURFACE2}; }}
        """

    def _apply_filter(self):
        self._populate_cards(self._cat_filter, self._search.text())

    def _on_value_changed(self, cls_name: str, key: str, value):
        self._current[cls_name][key] = value
        modified_count = sum(
            1 for n, d in self._current.items()
            if any(d.get(k) != self._origin[n].get(k)
                   for k in ("LIMIT_MS", "TOL_MS", "MIN_MS", "TEST_TIMEOUT_S")
                   if d.get(k) is not None)
        )
        self._stats.set_modified(modified_count)

    def _apply_params(self):
        changed = 0
        for cls in _tc.ALL_TESTS:
            name = cls.__name__
            d    = self._current[name]
            for attr, key in [("LIMIT_MS","LIMIT_MS"),("TOL_MS","TOL_MS"),
                               ("MIN_MS","MIN_MS"),("TEST_TIMEOUT_S","TEST_TIMEOUT_S")]:
                val = d.get(key)
                if val is not None and getattr(cls, attr, None) != val:
                    setattr(cls, attr, val)
                    if attr == "LIMIT_MS":
                        tol = getattr(cls, "TOL_MS", None)
                        cls.LIMIT_STR = f"{val} ms ± {tol} ms" if tol else f"≤ {val} ms"
                    changed += 1

        new_n = self._nsamples.value()
        if new_n != _tc.N_SAMPLES:
            _tc.N_SAMPLES = new_n; changed += 1

        ts = time.strftime("%H:%M:%S")
        self._banner.show_message(
            f"{changed} paramètre(s) appliqué(s)  —  {ts}", _C_GREEN, "", 5000)
        self._apply_filter()
        self.params_applied.emit()

    def _reset_params(self):
        reply = QMessageBox.question(
            self, "Réinitialiser",
            "Restaurer toutes les valeurs d'origine ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._current = copy.deepcopy(self._origin)
        for cls in _tc.ALL_TESTS:
            name = cls.__name__; d = self._origin[name]
            for attr, key in [("LIMIT_MS","LIMIT_MS"),("TOL_MS","TOL_MS"),
                               ("MIN_MS","MIN_MS"),("TEST_TIMEOUT_S","TEST_TIMEOUT_S")]:
                if d.get(key) is not None:
                    setattr(cls, attr, d[key])

        _tc.N_SAMPLES = self._nsamples._orig
        self._nsamples.reset()
        for card in self._cards.values():
            card.reset_to_origin()

        self._stats.set_modified(0)
        self._apply_filter()
        self._banner.show_message(
            "Tous les paramètres ont été réinitialisés", _C_AMBER, "", 4000)
        self.params_applied.emit()

    def _export_json(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter les paramètres", "test_params.json", "JSON (*.json)")
        if not path:
            return
        payload = {
            "N_SAMPLES": self._nsamples.value(),
            "tests": {
                name: {k: v for k, v in d.items()
                       if k in ("LIMIT_MS","TOL_MS","MIN_MS","TEST_TIMEOUT_S") and v is not None}
                for name, d in self._current.items()
            }
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            self._banner.show_message(f"Exporté : {path}", _C_BLUE, "", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))

    def _import_json(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Importer des paramètres", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e)); return

        if "N_SAMPLES" in payload:
            self._nsamples._spin.setValue(int(payload["N_SAMPLES"]))

        for name, vals in payload.get("tests", {}).items():
            if name not in self._current:
                continue
            for key in ("LIMIT_MS","TOL_MS","MIN_MS","TEST_TIMEOUT_S"):
                if key in vals:
                    self._current[name][key] = vals[key]
                    if name in self._cards:
                        card = self._cards[name]
                        if key in card._spins:
                            spin = card._spins[key]
                            spin.blockSignals(True)
                            v = vals[key]
                            if isinstance(spin, QDoubleSpinBox): spin.setValue(float(v))
                            else: spin.setValue(int(v))
                            spin.blockSignals(False)
                            spin.setStyleSheet(card._spin_style_mod(v != self._origin[name].get(key)))
                            if key in card._gauges:
                                card._gauges[key].set_deviation(self._origin[name].get(key), v)

        self._apply_filter()
        self._banner.show_message(
            f"Importé : {path}  —  cliquez APPLIQUER pour valider",
            "#6A1B9A", "", 6000)


_COL_TO_KEY = {3: "LIMIT_MS", 4: "TOL_MS", 5: "MIN_MS", 6: "TEST_TIMEOUT_S"}