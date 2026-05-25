"""
auto_test_panel.py  —  Panneau Qt "Tests Automatiques WipeWash"  (v5 – redesign)
=================================================================================
Design épuré : une seule vue d'exécution, pas d'onglets, pas de log.
Sous la zone des étapes : donut + courbes de résultats.
Panneau d'explication du cas de test enrichi.
"""

from __future__ import annotations

import datetime
import os
import re
import tempfile
import numpy as np

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame,
    QTreeWidget, QTreeWidgetItem,
    QPushButton, QLabel, QProgressBar,
    QAbstractItemView, QTextEdit, QSplitter,
    QFileDialog, QMessageBox, QSizePolicy,
    QScrollArea, QGraphicsDropShadowEffect,
    QDialog, QSpinBox, QDoubleSpinBox, QMenu,
)
from PySide6.QtCore import Qt, QMimeData, QByteArray, QPropertyAnimation, QEasingCurve, QPoint
from PySide6.QtGui import QColor, QFont, QDrag, QPixmap, QPainter, QLinearGradient, QPen, QBrush, QAction

import matplotlib
matplotlib.use("Qt5Agg")
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.patches as mpatches
import matplotlib.gridspec as gridspec

try:
    from report_generator import ReportGenerator
    _REPORT_AVAILABLE = True
except ImportError:
    _REPORT_AVAILABLE = False

from constants import (
    FONT_UI, FONT_MONO,
    W_BG, W_PANEL, W_PANEL2, W_PANEL3,
    W_BORDER, W_TOOLBAR, W_TITLEBAR,
    W_TEXT, W_TEXT_DIM, W_TEXT_HDR,
    A_GREEN, A_RED, A_ORANGE, A_AMBER, A_TEAL, A_TEAL2,
    KPIT_GREEN,
)
from widgets_base import _lbl, _cd_btn
from test_cases import ALL_TESTS, TestResult

# ─── Palette & statuts ────────────────────────────────────────────
STATUS_FG = {
    "PASS"   : "#2E7D32",
    "FAIL"   : "#C62828",
    "TIMEOUT": "#E65100",
    "RUNNING": "#1565C0",
    "PENDING": "#9E9E9E",
}
STATUS_BG = {
    "PASS"   : "#F1F8E9",
    "FAIL"   : "#FFEBEE",
    "TIMEOUT": "#FFF3E0",
    "RUNNING": "#E8F0FE",
    "PENDING": "#FAFAFA",
}
STATUS_ACCENT = {
    "PASS"   : "#66BB6A",
    "FAIL"   : "#EF5350",
    "TIMEOUT": "#FFA726",
    "RUNNING": "#42A5F5",
    "PENDING": "#BDBDBD",
}
CAT_FG = {
    "CYCLE"          : A_TEAL,
    "TIMEOUT"        : A_AMBER,
    "FUNCTIONAL"     : A_ORANGE,
    "FUNCTIONAL_BCM" : "#8E24AA",
}
CAT_ICON = {
    "CYCLE"          : "[C]",
    "TIMEOUT"        : "[T]",
    "FUNCTIONAL"     : "[F]",
    "FUNCTIONAL_BCM" : "[B]",
}
CAT_DESC = {
    "CYCLE"          : "Validates the number of wipe/wash cycles within timing constraints.",
    "TIMEOUT"        : "Ensures actuator response does not exceed maximum allowed timeout.",
    "FUNCTIONAL"     : "Checks functional behaviour of wiper/washer under standard conditions.",
    "FUNCTIONAL_BCM" : "BCM-level functional check including body control module signal handling.",
}
MIME_TEST_ID = "application/x-wipewash-test-id"

# ─── Couleurs matplotlib ──────────────────────────────────────────
MPL_PASS    = "#66BB6A"
MPL_FAIL    = "#EF5350"
MPL_TIMEOUT = "#FFA726"
MPL_LIMIT   = "#8DC63F"
MPL_GRID    = "#E0E0E0"
MPL_TEXT    = "#424242"


# ═══════════════════════════════════════════════════════════════════
#  TestParamDialog — fenêtre de paramètres (clic droit sur un test)
# ═══════════════════════════════════════════════════════════════════

_DLG_BG      = "#FFFFFF"
_DLG_SURFACE = "#F5FFF0"
_DLG_SURFACE2= "#EDF9E3"
_DLG_BORDER  = "#1A1A1A"
_DLG_GREEN   = "#2E7003"
_DLG_AMBER   = "#F39C12"
_DLG_RED     = "#C0392B"
_DLG_BLUE    = "#007ACC"
_DLG_DIM     = "#5A6A4A"
_DLG_TEXT    = "#1A1A1A"

_CAT_COLOR_DLG = {
    "CYCLE":           "#7A3100",
    "TIMEOUT":         "#791F1F",
    "FUNCTIONAL":      "#0C447C",
    "FUNCTIONAL_BCM":  "#3C3489",
    "FUNCTIONAL_WC":   "#085041",
    "LIN_SECURITE":    "#633806",
    "CAN_SECURITE":    "#444441",
}
_CAT_BG_DLG = {
    "CYCLE":           "#FFF3E0",
    "TIMEOUT":         "#FCEBEB",
    "FUNCTIONAL":      "#E6F1FB",
    "FUNCTIONAL_BCM":  "#EEEDFE",
    "FUNCTIONAL_WC":   "#E1F5EE",
    "LIN_SECURITE":    "#FAEEDA",
    "CAN_SECURITE":    "#F1EFE8",
}


class _DeviationGaugeDlg(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pct   = 0.0
        self._color = _DLG_GREEN
        self.setFixedSize(48, 48)
        self.setStyleSheet("background: transparent;")

    def set_deviation(self, original, current):
        if original is None or current is None or original == 0:
            self._pct = 0.0; self._color = _DLG_GREEN
        else:
            ratio = (current - original) / abs(original)
            self._pct = max(-1.0, min(1.0, ratio))
            self._color = (_DLG_GREEN if abs(self._pct) < 0.05
                           else _DLG_AMBER if abs(self._pct) < 0.25
                           else _DLG_RED)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        cx, cy, r = W // 2, H // 2, min(W, H) // 2 - 4
        p.setPen(QPen(QColor(_DLG_BORDER), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(cx - r, cy - r, r * 2, r * 2)
        if abs(self._pct) > 0.01:
            pen = QPen(QColor(self._color), 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(cx - r, cy - r, r * 2, r * 2, 90 * 16, int(-self._pct * 180 * 16))
        pct_int = int(abs(self._pct) * 100)
        sign = "+" if self._pct > 0.01 else ("-" if self._pct < -0.01 else "")
        txt  = f"{sign}{pct_int}%" if pct_int > 0 else "OK"
        p.setPen(QColor(self._color))
        p.setFont(QFont(FONT_MONO, 8, QFont.Weight.Bold))
        p.drawText(0, 0, W, H, Qt.AlignmentFlag.AlignCenter, txt)


class TestParamDialog(QDialog):
    """
    Fenêtre de paramétrage d'un test individuel.
    Ouverte via clic droit sur un item de TestTreeWidget.
    """

    def __init__(self, cls, parent=None):
        super().__init__(parent)
        self._cls     = cls
        self._spins:  dict[str, QWidget] = {}
        self._gauges: dict[str, _DeviationGaugeDlg] = {}
        self._origin  = {
            "LIMIT_MS":       getattr(cls, "LIMIT_MS",       None),
            "TOL_MS":         getattr(cls, "TOL_MS",         None),
            "MIN_MS":         getattr(cls, "MIN_MS",         None),
            "TEST_TIMEOUT_S": getattr(cls, "TEST_TIMEOUT_S", None),
        }
        self._current = dict(self._origin)

        self.setWindowTitle(f"Parameters — {cls.ID}  {cls.NAME}")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setStyleSheet(f"background: {_DLG_BG};")
        self._build()

    # ── Construction ────────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header coloré ────────────────────────────────────────────────
        cat  = getattr(self._cls, "CATEGORY", "")
        cat_c  = _CAT_COLOR_DLG.get(cat, _DLG_DIM)
        cat_bg = _CAT_BG_DLG.get(cat, "#F5FFF0")

        hdr = QFrame()
        hdr.setFixedHeight(72)
        hdr.setStyleSheet(f"background: {cat_bg}; border-bottom: 1px solid #D0D0D0;")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(20, 0, 20, 0); hl.setSpacing(14)

        # Badge ID
        id_badge = QLabel(getattr(self._cls, "ID", "??"))
        id_badge.setFixedSize(52, 36)
        id_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        id_badge.setStyleSheet(f"""
            background: {_DLG_SURFACE2}; color: {_DLG_TEXT};
            border: none; border-radius: 4px;
            font-family: '{FONT_MONO}'; font-size: 11pt; font-weight: bold;
        """)
        hl.addWidget(id_badge)

        # Titre + ref
        col = QVBoxLayout(); col.setSpacing(2)
        name_lbl = QLabel(getattr(self._cls, "NAME", ""))
        name_lbl.setStyleSheet(
            f"color: {_DLG_TEXT}; font-family: '{FONT_UI}'; font-size: 13pt;"
            "font-weight: 700; background: transparent;")
        ref_lbl = QLabel(getattr(self._cls, "REF", ""))
        ref_lbl.setStyleSheet(
            f"color: {_DLG_DIM}; font-family: '{FONT_MONO}'; font-size: 9pt;"
            "background: transparent; letter-spacing: 1px;")
        col.addWidget(name_lbl); col.addWidget(ref_lbl)
        hl.addLayout(col, 1)

        # Badge catégorie
        cat_badge = QLabel(cat)
        cat_badge.setStyleSheet(f"""
            background: {cat_bg}; color: {cat_c};
            border: none; border-radius: 3px;
            font-family: '{FONT_MONO}'; font-size: 8pt; font-weight: bold;
            padding: 3px 8px;
        """)
        hl.addWidget(cat_badge)

        # Limit string
        ls = getattr(self._cls, "LIMIT_STR", "")
        if ls:
            ls_lbl = QLabel(ls)
            ls_lbl.setStyleSheet(
                f"color: {_DLG_DIM}; font-family: '{FONT_MONO}'; font-size: 9.5pt;"
                "background: transparent;")
            hl.addWidget(ls_lbl)

        root.addWidget(hdr)

        # ── Corps : paramètres ───────────────────────────────────────────
        body = QWidget(); body.setStyleSheet(f"background: {_DLG_BG};")
        bl = QVBoxLayout(body); bl.setContentsMargins(24, 20, 24, 12); bl.setSpacing(14)

        params = [
            ("LIMIT_MS",       "LIMIT",   "ms",  1, 99999, "Success time limit"),
            ("TOL_MS",         "TOL ±",   "ms",  0,  9999, "Tolerance around the limit"),
            ("MIN_MS",         "MIN",     "ms",  0, 99999, "Minimum expected duration"),
            ("TEST_TIMEOUT_S", "TIMEOUT", "s",   1,   300, "Timeout global du test"),
        ]

        has_any = False
        for key, label, unit, mn, mx, desc in params:
            val  = self._origin.get(key)
            if val is None:
                continue
            has_any = True

            row = QFrame()
            row.setStyleSheet(f"""
                QFrame {{
                    background: {_DLG_SURFACE};
                    border: 1px solid #CCCCCC;
                    border-radius: 6px;
                }}
            """)
            rl = QHBoxLayout(row); rl.setContentsMargins(16, 10, 16, 10); rl.setSpacing(16)

            # Label + description
            lcol = QVBoxLayout(); lcol.setSpacing(2)
            lbl = QLabel(label)
            lbl.setFixedWidth(68)
            lbl.setStyleSheet(
                f"color: {_DLG_TEXT}; font-family: '{FONT_MONO}'; font-size: 12pt;"
                "font-weight: bold; background: transparent; letter-spacing: 2px;")
            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet(
                f"color: {_DLG_DIM}; font-family: '{FONT_UI}'; font-size: 9.5pt;"
                "background: transparent;")
            lcol.addWidget(lbl); lcol.addWidget(desc_lbl)
            rl.addLayout(lcol, 1)

            # Spinbox
            if isinstance(val, float) and val != int(val):
                spin = QDoubleSpinBox()
                spin.setDecimals(1)
                spin.setRange(float(mn), float(mx))
                spin.setValue(float(val))
            else:
                spin = QSpinBox()
                spin.setRange(mn, mx)
                spin.setValue(int(val))

            spin.setSuffix(f"  {unit}")
            spin.setFixedSize(120, 36)
            spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            spin.setStyleSheet(self._spin_style(False))

            gauge = _DeviationGaugeDlg()
            gauge.set_deviation(val, val)
            self._gauges[key] = gauge
            self._spins[key]  = spin

            def _on_change(v, k=key, s=spin, orig=val):
                self._current[k] = v
                modified = (orig is not None and v != orig)
                s.setStyleSheet(self._spin_style(modified))
                if k in self._gauges:
                    self._gauges[k].set_deviation(orig, v)
                self._update_apply_btn()

            spin.valueChanged.connect(_on_change)

            rl.addWidget(gauge)
            rl.addWidget(spin)
            bl.addWidget(row)

        if not has_any:
            nl = QLabel("— This test has no editable numeric parameters —")
            nl.setStyleSheet(f"color: {_DLG_DIM}; font-size: 11pt; background: transparent;")
            nl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            bl.addWidget(nl)

        root.addWidget(body, 1)

        # ── Barre de boutons ─────────────────────────────────────────────
        btn_bar = QFrame()
        btn_bar.setFixedHeight(56)
        btn_bar.setStyleSheet(
            f"background: {_DLG_SURFACE}; border-top: 1px solid #D0D0D0;")
        bb = QHBoxLayout(btn_bar)
        bb.setContentsMargins(20, 0, 20, 0); bb.setSpacing(10)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(
            f"color: {_DLG_DIM}; font-family: '{FONT_MONO}'; font-size: 9.5pt;"
            "background: transparent;")
        bb.addWidget(self._status_lbl, 1)

        self._reset_btn = self._mk_btn("RESET", _DLG_DIM)
        self._reset_btn.clicked.connect(self._do_reset)
        bb.addWidget(self._reset_btn)

        self._apply_btn = self._mk_btn("APPLIQUER", _DLG_GREEN)
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._do_apply)
        bb.addWidget(self._apply_btn)

        close_btn = self._mk_btn("FERMER", _DLG_RED)
        close_btn.clicked.connect(self.accept)
        bb.addWidget(close_btn)

        root.addWidget(btn_bar)

    # ── Helpers ──────────────────────────────────────────────────────────
    def _mk_btn(self, text: str, color: str) -> QPushButton:
        b = QPushButton(text)
        b.setFixedHeight(34)
        b.setStyleSheet(f"""
            QPushButton {{
                background: {color}; color: #FFFFFF;
                border: 1px solid {color}; border-radius: 5px;
                font-family: '{FONT_MONO}'; font-size: 9pt; font-weight: bold;
                padding: 0 14px; letter-spacing: 0.5px;
            }}
            QPushButton:hover {{ background: {_DLG_BG}; color: {color}; border: 1px solid {color}; }}
            QPushButton:pressed {{ background: {_DLG_SURFACE}; color: {color}; }}
            QPushButton:disabled {{ background: #EEEEEE; color: #BBBBBB; border-color: #DDDDDD; }}
        """)
        return b

    def _spin_style(self, modified: bool) -> str:
        border = _DLG_AMBER if modified else "#CCCCCC"
        bg     = "#FFFDE7"  if modified else _DLG_SURFACE2
        fg     = _DLG_AMBER if modified else _DLG_TEXT
        return f"""
            QSpinBox, QDoubleSpinBox {{
                background: {bg}; color: {fg};
                border: 1px solid {border}; border-radius: 3px;
                font-family: '{FONT_MONO}'; font-size: 11pt; font-weight: bold;
            }}
            QSpinBox::up-button, QDoubleSpinBox::up-button,
            QSpinBox::down-button, QDoubleSpinBox::down-button {{
                background: {_DLG_SURFACE}; border: none; width: 14px;
            }}
        """

    def _update_apply_btn(self):
        modified = any(
            self._current.get(k) != self._origin.get(k)
            for k in ("LIMIT_MS", "TOL_MS", "MIN_MS", "TEST_TIMEOUT_S")
            if self._origin.get(k) is not None
        )
        self._apply_btn.setEnabled(modified)
        if modified:
            self._status_lbl.setText("Modifications en attente")
            self._status_lbl.setStyleSheet(
                f"color: {_DLG_AMBER}; font-family: '{FONT_MONO}'; font-size: 9.5pt;"
                "background: transparent; font-weight: bold;")
        else:
            self._status_lbl.setText("")

    def _do_apply(self):
        import time as _time
        changed = 0
        for attr, key in [("LIMIT_MS","LIMIT_MS"), ("TOL_MS","TOL_MS"),
                           ("MIN_MS","MIN_MS"), ("TEST_TIMEOUT_S","TEST_TIMEOUT_S")]:
            val = self._current.get(key)
            if val is not None and getattr(self._cls, attr, None) != val:
                setattr(self._cls, attr, val)
                if attr == "LIMIT_MS":
                    tol = getattr(self._cls, "TOL_MS", None)
                    self._cls.LIMIT_STR = (f"{val} ms ± {tol} ms" if tol else f"≤ {val} ms")
                changed += 1
        ts = _time.strftime("%H:%M:%S")
        self._status_lbl.setText(f"{changed} param(s) applied at {ts}")
        self._status_lbl.setStyleSheet(
            f"color: {_DLG_GREEN}; font-family: '{FONT_MONO}'; font-size: 9.5pt;"
            "background: transparent; font-weight: bold;")
        self._apply_btn.setEnabled(False)

    def _do_reset(self):
        for key, spin in self._spins.items():
            orig = self._origin.get(key)
            if orig is not None:
                spin.blockSignals(True)
                if isinstance(spin, QDoubleSpinBox): spin.setValue(float(orig))
                else:                                spin.setValue(int(orig))
                spin.setStyleSheet(self._spin_style(False))
                spin.blockSignals(False)
                self._current[key] = orig
                if key in self._gauges:
                    self._gauges[key].set_deviation(orig, orig)
        self._apply_btn.setEnabled(False)
        self._status_lbl.setText("Values reset")
        self._status_lbl.setStyleSheet(
            f"color: {_DLG_DIM}; font-family: '{FONT_MONO}'; font-size: 9.5pt;"
            "background: transparent;")


# ═══════════════════════════════════════════════════════════════════
#  Arborescence des tests (drag source)
# ═══════════════════════════════════════════════════════════════════

class TestTreeWidget(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAnimated(True)
        self.setIndentation(18)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self._cls_map = {cls.ID: cls for cls in ALL_TESTS}
        self.setStyleSheet(f"""
            QTreeWidget {{
                background: #1A1A1A;
                color: #E0E0E0;
                border: none;
                font-family: "Segoe UI", {FONT_UI};
                font-size: 10pt;
                outline: none;
            }}
            QTreeWidget::item {{
                padding: 5px 8px;
                border-radius: 4px;
            }}
            QTreeWidget::item:selected {{
                background: rgba(141,198,63,0.30);
                color: #FFFFFF;
            }}
            QTreeWidget::item:hover:!selected {{
                background: rgba(255,255,255,0.07);
            }}
        """)
        self._build_tree()

    def _build_tree(self):
        cats: dict[str, list] = {}
        for cls in ALL_TESTS:
            cats.setdefault(cls.CATEGORY, []).append(cls)

        for cat, tests in cats.items():
            icon = CAT_ICON.get(cat, "■")
            root_item = QTreeWidgetItem(self, [f" {icon}  {cat}  ({len(tests)})"])
            root_item.setFont(0, QFont(FONT_UI, 10, QFont.Weight.DemiBold))
            root_item.setForeground(0, QColor(CAT_FG.get(cat, W_TEXT)))
            root_item.setData(0, Qt.ItemDataRole.UserRole, None)
            for cls in tests:
                child = QTreeWidgetItem(root_item, [f"  {cls.ID}  —  {cls.NAME}"])
                child.setFont(0, QFont(FONT_MONO, 9))
                child.setForeground(0, QColor("#D0D0D0"))
                child.setData(0, Qt.ItemDataRole.UserRole, cls.ID)
                child.setToolTip(0, f"[{cls.CATEGORY}] {cls.NAME}\n{cls.REF}\nLimit: {cls.LIMIT_STR}\n\nClic droit -> Parametres")
            root_item.setExpanded(True)

    def _show_context_menu(self, pos: QPoint):
        item = self.itemAt(pos)
        if item is None:
            return
        test_id = item.data(0, Qt.ItemDataRole.UserRole)
        if not test_id:
            return  # clic sur une catégorie
        cls = self._cls_map.get(test_id)
        if cls is None:
            return

        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: #FFFFFF; color: #1A1A1A;
                border: 2px solid #1A1A1A; border-radius: 4px;
                font-family: '{FONT_UI}'; font-size: 10pt;
                padding: 4px 0;
            }}
            QMenu::item {{ padding: 7px 24px; }}
            QMenu::item:selected {{ background: #EDF9E3; color: #2E7003; }}
            QMenu::separator {{ height: 1px; background: #1A1A1A; margin: 4px 8px; }}
        """)

        act_params = QAction(f"Parameters — {test_id}", self)
        act_params.setFont(QFont(FONT_MONO, 10, QFont.Weight.Bold))
        menu.addAction(act_params)
        menu.addSeparator()
        act_info = QAction(f"ℹ  {cls.NAME}", self)
        act_info.setEnabled(False)
        menu.addAction(act_info)

        chosen = menu.exec(self.viewport().mapToGlobal(pos))
        if chosen == act_params:
            dlg = TestParamDialog(cls, self)
            dlg.exec()

    def startDrag(self, supported_actions):
        items = self.selectedItems()
        ids = [it.data(0, Qt.ItemDataRole.UserRole) for it in items
               if it.data(0, Qt.ItemDataRole.UserRole)]
        if not ids:
            return
        mime = QMimeData()
        mime.setData(MIME_TEST_ID, QByteArray(",".join(ids).encode()))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pm = QPixmap(180, 28)
        pm.fill(QColor(KPIT_GREEN))
        p = QPainter(pm)
        p.setFont(QFont(FONT_MONO, 9))
        p.setPen(QColor("#FFFFFF"))
        label = ids[0] if len(ids) == 1 else f"{ids[0]}  +{len(ids)-1} more"
        p.drawText(10, 19, label)
        p.end()
        drag.setPixmap(pm)
        drag.exec(Qt.DropAction.CopyAction)


# ═══════════════════════════════════════════════════════════════════
#  Carte d'explication du cas de test (en haut de la drop zone)
# ═══════════════════════════════════════════════════════════════════

class TestInfoCard(QFrame):
    """Panneau riche expliquant le cas de test sélectionné."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("testInfoCard")
        self.setMinimumHeight(120)
        self.setMaximumHeight(180)
        self.setStyleSheet(f"""
            QFrame#testInfoCard {{
                background: #1A1A1A;
                border-bottom: 1px solid #333333;
            }}
        """)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 10, 16, 10)
        self._layout.setSpacing(6)
        self._show_placeholder()

    def _show_placeholder(self):
        self._clear()
        ph = QLabel("Drop a test to view its specification")
        ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ph.setStyleSheet(
            f"color:#555555;font-family:'Segoe UI',{FONT_UI};"
            f"font-size:10pt;background:transparent;font-style:italic;")
        self._layout.addWidget(ph)

    def _clear(self):
        self._clear_layout(self._layout)

    def _clear_layout(self, layout):
        """Supprime récursivement tous les items (widgets, sous-layouts, spacers)."""
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            else:
                sub = item.layout()
                if sub:
                    self._clear_layout(sub)
                # les spacers sont automatiquement supprimés par takeAt

    def update_tests(self, classes: list):
        """Affiche les infos du/des cas de test."""
        self._clear()
        if not classes:
            self._show_placeholder()
            return

        if len(classes) == 1:
            cls = classes[0]
            self._render_single(cls)
        else:
            self._render_multi(classes)

    def _render_single(self, cls):
        cat_color = CAT_FG.get(cls.CATEGORY, W_TEXT_DIM)
        cat_icon  = CAT_ICON.get(cls.CATEGORY, "■")
        cat_desc  = CAT_DESC.get(cls.CATEGORY, "")

        # Ligne 1 : ID + nom + badge catégorie
        row1 = QHBoxLayout()
        id_lbl = QLabel(cls.ID)
        id_lbl.setStyleSheet(
            f"color:{KPIT_GREEN};font-family:{FONT_MONO};font-size:13pt;"
            f"font-weight:bold;background:transparent;letter-spacing:1px;")

        name_lbl = QLabel(cls.NAME)
        name_lbl.setStyleSheet(
            f"color:#E0E0E0;font-family:'Segoe UI',{FONT_UI};font-size:11pt;"
            f"font-weight:600;background:transparent;")
        name_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        badge = QLabel(f" {cat_icon} {cls.CATEGORY} ")
        badge.setStyleSheet(
            f"color:{cat_color};background:rgba(255,255,255,0.07);"
            f"font-family:{FONT_MONO};font-size:8pt;font-weight:bold;"
            f"border:1px solid {cat_color};border-radius:10px;padding:0 6px;")
        row1.addWidget(id_lbl)
        row1.addSpacing(12)
        row1.addWidget(name_lbl, 1)
        row1.addWidget(badge)
        self._layout.addLayout(row1)

        # Ligne 2 : description de la catégorie
        desc_lbl = QLabel(cat_desc)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(
            f"color:#777777;font-family:'Segoe UI',{FONT_UI};"
            f"font-size:9pt;background:transparent;font-style:italic;")
        self._layout.addWidget(desc_lbl)

        # Ligne 3 : méta-données (Ref, Limit)
        row3 = QHBoxLayout()
        row3.setSpacing(32)
        for key, val in [("Reference", cls.REF), ("Limit", cls.LIMIT_STR)]:
            col = QVBoxLayout()
            col.setSpacing(1)
            k_lbl = QLabel(key.upper())
            k_lbl.setStyleSheet(
                f"color:#555555;font-family:'Segoe UI',{FONT_UI};"
                f"font-size:7pt;font-weight:bold;letter-spacing:1px;background:transparent;")
            v_lbl = QLabel(val or "—")
            v_lbl.setStyleSheet(
                f"color:#D0D0D0;font-family:{FONT_MONO};font-size:10pt;"
                f"font-weight:bold;background:transparent;")
            col.addWidget(k_lbl)
            col.addWidget(v_lbl)
            row3.addLayout(col)
        row3.addStretch()
        self._layout.addLayout(row3)

    def _render_multi(self, classes):
        hdr = QLabel(f"{len(classes)} tests selected")
        hdr.setStyleSheet(
            f"color:{KPIT_GREEN};font-family:{FONT_MONO};font-size:11pt;"
            f"font-weight:bold;background:transparent;")
        self._layout.addWidget(hdr)

        # Résumé par catégorie
        cats: dict[str, int] = {}
        for cls in classes:
            cats[cls.CATEGORY] = cats.get(cls.CATEGORY, 0) + 1

        row = QHBoxLayout()
        row.setSpacing(20)
        for cat, count in cats.items():
            badge = QLabel(f"{CAT_ICON.get(cat,'')} {cat}  ×{count}")
            badge.setStyleSheet(
                f"color:{CAT_FG.get(cat, W_TEXT_DIM)};background:rgba(255,255,255,0.06);"
                f"font-family:{FONT_MONO};font-size:9pt;"
                f"border:1px solid {CAT_FG.get(cat, W_BORDER)};border-radius:10px;padding:2px 10px;")
            row.addWidget(badge)
        row.addStretch()
        self._layout.addLayout(row)

        ids_str = "  ·  ".join(cls.ID for cls in classes[:8])
        if len(classes) > 8:
            ids_str += f"  … +{len(classes)-8}"
        ids_lbl = QLabel(ids_str)
        ids_lbl.setStyleSheet(
            f"color:#555555;font-family:{FONT_MONO};font-size:8pt;background:transparent;")
        self._layout.addWidget(ids_lbl)
        self._layout.addStretch()


# ═══════════════════════════════════════════════════════════════════
#  Carte de résultat détaillé (sous les étapes)
# ═══════════════════════════════════════════════════════════════════

class ResultDetailCard(QFrame):
    """Explication riche du résultat d'un test."""

    def __init__(self, cls, result: TestResult, parent=None):
        super().__init__(parent)
        self.setObjectName("resultDetailCard")
        status = result.status if result else "PENDING"
        fg     = STATUS_FG.get(status, W_TEXT)
        bg     = STATUS_BG.get(status, "#FAFAFA")
        accent = STATUS_ACCENT.get(status, "#BDBDBD")

        self.setStyleSheet(f"""
            QFrame#resultDetailCard {{
                background: {bg};
                border: 1.5px solid {accent};
                border-left: 5px solid {accent};
                border-radius: 8px;
            }}
        """)

        vl = QVBoxLayout(self)
        vl.setContentsMargins(16, 12, 16, 14)
        vl.setSpacing(6)

        # ── Titre ──
        row1 = QHBoxLayout()
        id_lbl = QLabel(f"<b>{cls.ID}</b>")
        id_lbl.setStyleSheet(
            f"color:{A_TEAL};font-family:{FONT_MONO};font-size:12pt;background:transparent;")
        name_lbl = QLabel(cls.NAME)
        name_lbl.setStyleSheet(
            f"color:{W_TEXT};font-family:'Segoe UI',{FONT_UI};font-size:10pt;background:transparent;")
        name_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        pill = QLabel(f"  {status}  ")
        pill.setStyleSheet(
            f"color:#FFFFFF;background:{fg};font-family:{FONT_MONO};font-size:10pt;"
            f"font-weight:bold;border-radius:10px;padding:2px 8px;")
        row1.addWidget(id_lbl)
        row1.addSpacing(10)
        row1.addWidget(name_lbl, 1)
        row1.addWidget(pill)
        vl.addLayout(row1)

        # ── Séparateur ──
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{accent};border:none;")
        vl.addWidget(sep)

        if result:
            # ── Mesure vs Limite ──
            row2 = QHBoxLayout()
            row2.setSpacing(40)
            for key, val, highlight in [
                ("LIMIT",    cls.LIMIT_STR,   False),
                ("MEASURED", result.measured,  True),
            ]:
                col = QVBoxLayout()
                col.setSpacing(2)
                k = QLabel(key)
                k.setStyleSheet(
                    f"color:{W_TEXT_DIM};font-family:'Segoe UI',{FONT_UI};"
                    f"font-size:7.5pt;font-weight:bold;letter-spacing:1px;background:transparent;")
                color = fg if highlight else W_TEXT
                v = QLabel(val or "—")
                v.setStyleSheet(
                    f"color:{color};font-family:{FONT_MONO};font-size:13pt;"
                    f"font-weight:bold;background:transparent;")
                col.addWidget(k)
                col.addWidget(v)
                row2.addLayout(col)

            # ── Barre de ratio mesuré/limite ──
            ratio_col = QVBoxLayout()
            ratio_col.setSpacing(3)
            ratio_lbl = QLabel("RATIO")
            ratio_lbl.setStyleSheet(
                f"color:{W_TEXT_DIM};font-family:'Segoe UI',{FONT_UI};"
                f"font-size:7.5pt;font-weight:bold;letter-spacing:1px;background:transparent;")
            ratio_col.addWidget(ratio_lbl)
            meas_ms = _extract_ms(result.measured)
            lim_ms  = _extract_ms(cls.LIMIT_STR)
            if meas_ms and lim_ms and lim_ms > 0:
                ratio = min(meas_ms / lim_ms, 1.5)
                pct   = int(min(ratio, 1.0) * 100)
                bar   = QProgressBar()
                bar.setRange(0, 100)
                bar.setValue(pct)
                bar.setFixedHeight(12)
                bar.setFixedWidth(160)
                bar.setTextVisible(False)
                bar_color = MPL_PASS if ratio <= 1.0 else MPL_FAIL
                bar.setStyleSheet(
                    f"QProgressBar{{background:#E0E0E0;border:none;border-radius:5px;}}"
                    f"QProgressBar::chunk{{background:{bar_color};border-radius:5px;}}")
                ratio_col.addWidget(bar)
                pct_lbl = QLabel(f"{ratio*100:.0f}% of limit")
                pct_lbl.setStyleSheet(
                    f"color:{bar_color};font-family:{FONT_MONO};font-size:8pt;"
                    f"font-weight:bold;background:transparent;")
                ratio_col.addWidget(pct_lbl)
            else:
                ratio_col.addWidget(QLabel("—"))
            row2.addLayout(ratio_col)
            row2.addStretch()
            vl.addLayout(row2)

            # ── Verdict explicatif ──
            verdict_text = _build_verdict(cls, result)
            verdict_lbl = QLabel(verdict_text)
            verdict_lbl.setWordWrap(True)
            verdict_lbl.setStyleSheet(
                f"color:{fg};font-family:'Segoe UI',{FONT_UI};font-size:9pt;"
                f"font-style:italic;background:transparent;")
            vl.addWidget(verdict_lbl)

            # ── Détails / cause ──
            if result.details and result.details not in ("", "—"):
                dtl_hdr = QLabel("CAUSE / DETAILS")
                dtl_hdr.setStyleSheet(
                    f"color:{W_TEXT_DIM};font-family:'Segoe UI',{FONT_UI};"
                    f"font-size:7.5pt;font-weight:bold;letter-spacing:1px;background:transparent;")
                vl.addWidget(dtl_hdr)
                dtl_box = QTextEdit()
                dtl_box.setReadOnly(True)
                dtl_box.setPlainText(result.details)
                dtl_box.setFixedHeight(52)
                dtl_box.setStyleSheet(
                    f"QTextEdit{{background:rgba(0,0,0,0.04);color:{W_TEXT};"
                    f"border:1px solid {W_BORDER};border-radius:4px;"
                    f"font-family:{FONT_MONO};font-size:8.5pt;padding:3px;}}")
                vl.addWidget(dtl_box)
        else:
            pending = QLabel(f"Limit: {cls.LIMIT_STR}  ·  Waiting for execution…")
            pending.setStyleSheet(
                f"color:{W_TEXT_DIM};font-family:{FONT_MONO};font-size:9pt;background:transparent;")
            vl.addWidget(pending)


def _extract_ms(s: str) -> float | None:
    if not s:
        return None
    m = re.search(r"([\d.]+)\s*ms", s, re.I)
    if m:
        return float(m.group(1))
    m2 = re.search(r"([\d.]+)\s*s\b", s, re.I)
    if m2:
        return float(m2.group(1)) * 1000
    return None


def _build_verdict(cls, result: TestResult) -> str:
    """Génère une phrase explicative du résultat."""
    status = result.status
    meas   = result.measured or "N/A"
    lim    = cls.LIMIT_STR or "N/A"

    if status == "PASS":
        return (f"Test passed: measured value ({meas}) is within the specified limit ({lim}). "
                f"The {cls.CATEGORY} behaviour is nominal.")
    elif status == "FAIL":
        return (f"Test failed: measured value ({meas}) does not comply with the limit ({lim}). "
                f"Investigate the {cls.CATEGORY} logic or the associated ECU/BCM signals.")
    elif status == "TIMEOUT":
        return (f"Timeout: the system did not respond within the allowed window ({lim}). "
                f"Check the CAN bus communication and actuator availability.")
    else:
        return f"Status: {status}  ·  Limit: {lim}"


# ═══════════════════════════════════════════════════════════════════
#  Graphiques de résultats (donut + scatter + timeline)
# ═══════════════════════════════════════════════════════════════════

class ResultChartsWidget(QWidget):
    """Donut + scatter + timeline, affiché sous les étapes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(320)
        self.setStyleSheet(f"background:{W_PANEL2};border-top:1.5px solid {W_BORDER};")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Donut (carré)
        self._donut_canvas = FigureCanvas(Figure(figsize=(2.8, 2.8), facecolor="#FAFAFA"))
        self._donut_canvas.setFixedWidth(240)
        layout.addWidget(self._donut_canvas)

        # Zone droite : scatter + timeline empilés
        right = QVBoxLayout()
        right.setSpacing(6)

        self._scatter_canvas = FigureCanvas(Figure(figsize=(5.5, 2.5), facecolor="#FAFAFA"))
        self._scatter_canvas.setMinimumHeight(130)
        right.addWidget(self._scatter_canvas, 1)

        self._timeline_canvas = FigureCanvas(Figure(figsize=(5.5, 2.5), facecolor="#FAFAFA"))
        self._timeline_canvas.setMinimumHeight(130)
        right.addWidget(self._timeline_canvas, 1)

        layout.addLayout(right, 1)

        self._draw_empty()

    # ── API publique ───────────────────────────────────────────────

    def update_charts(self, results: list[TestResult], cls_map: dict):
        if not results:
            self._draw_empty()
            return
        n_pass    = sum(1 for r in results if r.status == "PASS")
        n_fail    = sum(1 for r in results if r.status == "FAIL")
        n_timeout = sum(1 for r in results if r.status == "TIMEOUT")
        self._draw_donut(n_pass, n_fail, n_timeout)
        self._draw_scatter(results, cls_map)
        self._draw_timeline(results)

    # ── Donut ──────────────────────────────────────────────────────

    def _draw_empty(self):
        # Donut placeholder
        fig = self._donut_canvas.figure
        fig.clear()
        fig.patch.set_facecolor("#FAFAFA")
        ax = fig.add_subplot(111)
        ax.set_facecolor("#FAFAFA")
        wedge_data = [1, 1, 1]
        ax.pie(wedge_data, colors=["#E8F5E9", "#FFEBEE", "#FFF3E0"],
               startangle=90,
               wedgeprops=dict(width=0.46, edgecolor="#FAFAFA", linewidth=2))
        ax.text(0, 0.10, "—", ha="center", fontsize=20, fontweight="bold",
                color="#BDBDBD", fontfamily="Segoe UI")
        ax.text(0, -0.18, "PASS RATE", ha="center", fontsize=7.5, color="#BDBDBD",
                fontfamily="Segoe UI", fontweight="bold")
        patches = [
            mpatches.Patch(color="#66BB6A", label="PASS"),
            mpatches.Patch(color="#EF5350", label="FAIL"),
            mpatches.Patch(color="#FFA726", label="TIMEOUT"),
        ]
        ax.legend(handles=patches, loc="lower center",
                  bbox_to_anchor=(0.5, -0.18), ncol=1,
                  fontsize=8, frameon=False, labelcolor="#BDBDBD")
        ax.set_title("Results", fontsize=9, fontweight="bold",
                     pad=6, color="#BDBDBD", fontfamily="Segoe UI")
        fig.tight_layout(pad=0.5)
        self._donut_canvas.draw()

        # Scatter placeholder
        fig2 = self._scatter_canvas.figure
        fig2.clear()
        fig2.patch.set_facecolor("#FAFAFA")
        ax2 = fig2.add_subplot(111)
        ax2.set_facecolor("#FAFAFA")
        ax2.set_title("Measured vs Limit", fontsize=8.5, fontweight="bold",
                      color="#BDBDBD", pad=4)
        ax2.text(0.5, 0.5, "Run tests to see timing comparison",
                 ha="center", va="center", transform=ax2.transAxes,
                 fontsize=8.5, color="#BDBDBD", style="italic")
        ax2.spines[["top", "right", "left", "bottom"]].set_color("#E0E0E0")
        ax2.set_xticks([])
        ax2.set_yticks([])
        fig2.tight_layout(pad=0.6)
        self._scatter_canvas.draw()

        # Timeline placeholder
        fig3 = self._timeline_canvas.figure
        fig3.clear()
        fig3.patch.set_facecolor("#FAFAFA")
        ax3 = fig3.add_subplot(111)
        ax3.set_facecolor("#FAFAFA")
        ax3.set_title("Execution Timeline", fontsize=8.5, fontweight="bold",
                      color="#BDBDBD", pad=4)
        ax3.text(0.5, 0.5, "Run tests to see execution durations",
                 ha="center", va="center", transform=ax3.transAxes,
                 fontsize=8.5, color="#BDBDBD", style="italic")
        ax3.spines[["top", "right", "left", "bottom"]].set_color("#E0E0E0")
        ax3.set_xticks([])
        ax3.set_yticks([])
        fig3.tight_layout(pad=0.6)
        self._timeline_canvas.draw()

    def _draw_donut(self, n_pass, n_fail, n_timeout):
        total = n_pass + n_fail + n_timeout
        fig = self._donut_canvas.figure
        fig.clear()
        fig.patch.set_facecolor("#FAFAFA")
        ax = fig.add_subplot(111)
        ax.set_facecolor("#FAFAFA")

        if total == 0:
            ax.text(0.5, 0.5, "—", ha="center", va="center",
                    transform=ax.transAxes, fontsize=18, color="#9E9E9E")
            ax.axis("off")
            self._donut_canvas.draw()
            return

        data = [(l, n, c) for l, n, c in [
            ("PASS",    n_pass,    MPL_PASS),
            ("FAIL",    n_fail,    MPL_FAIL),
            ("TIMEOUT", n_timeout, MPL_TIMEOUT),
        ] if n > 0]

        wedges, _ = ax.pie(
            [d[1] for d in data],
            colors=[d[2] for d in data],
            startangle=90,
            wedgeprops=dict(width=0.46, edgecolor="#FAFAFA", linewidth=2),
            counterclock=False,
        )
        pct = int(n_pass / total * 100)
        ax.text(0, 0.12, f"{pct}%",  ha="center", fontsize=20, fontweight="bold",
                color="#212121", fontfamily="Segoe UI")
        ax.text(0, -0.14, "PASS RATE", ha="center", fontsize=7.5, color="#9E9E9E",
                fontfamily="Segoe UI", fontweight="bold")
        ax.text(0, -0.34, f"{total} tests", ha="center", fontsize=7, color="#BDBDBD",
                fontfamily="Segoe UI")

        patches = [mpatches.Patch(color=d[2], label=f"{d[0]}  {d[1]}/{total}")
                   for d in data]
        ax.legend(handles=patches, loc="lower center",
                  bbox_to_anchor=(0.5, -0.22), ncol=1,
                  fontsize=8, frameon=False,
                  labelcolor=MPL_TEXT)
        verdict = "All tests passed" if n_fail == 0 and n_timeout == 0 else \
                  f"{n_fail} failure(s), {n_timeout} timeout(s)"
        ax.set_title(f"Results — {verdict}", fontsize=8, fontweight="bold",
                     pad=6, color=MPL_TEXT if n_fail == 0 and n_timeout == 0 else MPL_FAIL,
                     fontfamily="Segoe UI")
        fig.tight_layout(pad=0.5)
        self._donut_canvas.draw()

    # ── Scatter mesuré vs limite ───────────────────────────────────

    def _draw_scatter(self, results, cls_map: dict):
        fig = self._scatter_canvas.figure
        fig.clear()
        fig.patch.set_facecolor("#FAFAFA")
        ax = fig.add_subplot(111)
        ax.set_facecolor("#FAFAFA")

        ids, meas_vals, lim_vals, colors = [], [], [], []
        for r in results:
            cls = cls_map.get(r.test_id)
            if not cls:
                continue
            mv = _extract_ms(r.measured)
            lv = _extract_ms(cls.LIMIT_STR)
            if mv is None or lv is None:
                continue
            ids.append(r.test_id)
            meas_vals.append(mv)
            lim_vals.append(lv)
            colors.append(MPL_PASS if r.status == "PASS" else
                          MPL_TIMEOUT if r.status == "TIMEOUT" else MPL_FAIL)

        if len(ids) < 1:
            ax.text(0.5, 0.5, "No timing data available", ha="center", va="center",
                    transform=ax.transAxes, fontsize=8.5, color="#9E9E9E")
            ax.axis("off")
            fig.tight_layout(pad=0.4)
            self._scatter_canvas.draw()
            return

        x = np.arange(len(ids))
        ax.bar(x, lim_vals,   width=0.55, color="#E8F5E9", edgecolor=MPL_LIMIT,
               linewidth=1.2, label="Limit", zorder=2)
        ax.scatter(x, meas_vals, c=colors, s=70, zorder=5,
                   edgecolors="white", linewidths=1.0, label="Measured")
        for xi, mv, lv, col in zip(x, meas_vals, lim_vals, colors):
            ax.plot([xi, xi], [0, mv], color="#BDBDBD", lw=0.8, zorder=3)
            # Annotate with margin percentage
            margin_pct = (1.0 - mv / lv) * 100 if lv > 0 else 0
            sign = "+" if margin_pct >= 0 else ""
            ax.annotate(f"{sign}{margin_pct:.0f}%", (xi, mv),
                        textcoords="offset points", xytext=(0, 7),
                        ha="center", fontsize=6, fontweight="bold",
                        color=col)

        ax.set_xticks(x)
        ax.set_xticklabels(ids, fontsize=7, rotation=25, ha="right", color=MPL_TEXT)
        ax.set_ylabel("Time (ms)", fontsize=8, color=MPL_TEXT)
        ax.set_title("Measured vs Limit", fontsize=8.5, fontweight="bold",
                     color=MPL_TEXT, pad=4)
        ax.tick_params(colors=MPL_TEXT, labelsize=7)
        ax.spines[["top", "right"]].set_visible(False)
        ax.spines[["left", "bottom"]].set_color(MPL_GRID)
        ax.yaxis.grid(True, color=MPL_GRID, linestyle="--", linewidth=0.6, zorder=1)
        ax.set_axisbelow(True)
        legend = ax.legend(fontsize=7, frameon=False, labelcolor=MPL_TEXT)
        fig.tight_layout(pad=0.6)
        self._scatter_canvas.draw()

    # ── Timeline horizontale ───────────────────────────────────────

    def _draw_timeline(self, results):
        fig = self._timeline_canvas.figure
        fig.clear()
        fig.patch.set_facecolor("#FAFAFA")
        ax = fig.add_subplot(111)
        ax.set_facecolor("#FAFAFA")

        if not results:
            ax.text(0.5, 0.5, "No results", ha="center", va="center",
                    transform=ax.transAxes, fontsize=9, color="#9E9E9E")
            ax.axis("off")
            self._timeline_canvas.draw()
            return

        labels, durations, lim_durations, colors = [], [], [], []
        for r in results:
            ms = _extract_ms(r.measured)
            dur = (ms / 1000.0) if ms else 0.05
            durations.append(dur)
            labels.append(r.test_id)
            colors.append(
                MPL_PASS if r.status == "PASS" else
                MPL_TIMEOUT if r.status == "TIMEOUT" else MPL_FAIL
            )
            cls = {}  # placeholder, limit not used in timeline
            lim_durations.append(dur)  # not shown, kept for reference

        y = np.arange(len(results))
        ax.barh(y, durations, height=0.5, color=colors,
                alpha=0.85, edgecolor="white", linewidth=0.8)
        # Annotate each bar with duration
        for yi, dur, r in zip(y, durations, results):
            ax.text(dur + max(durations) * 0.01, yi,
                    f"{r.measured or 'N/A'}  [{r.status}]",
                    va="center", fontsize=6.5, color=MPL_TEXT)
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=7, color=MPL_TEXT)
        ax.set_xlabel("Duration (s)", fontsize=8, color=MPL_TEXT)
        ax.set_title("Execution Timeline  —  measured duration per test", fontsize=8.5,
                     fontweight="bold", color=MPL_TEXT, pad=4)
        ax.invert_yaxis()
        ax.spines[["top", "right"]].set_visible(False)
        ax.spines[["left", "bottom"]].set_color(MPL_GRID)
        ax.xaxis.grid(True, color=MPL_GRID, linestyle="--", linewidth=0.6)
        ax.set_axisbelow(True)
        ax.tick_params(colors=MPL_TEXT, labelsize=7)
        fig.tight_layout(pad=0.6)
        self._timeline_canvas.draw()


# ═══════════════════════════════════════════════════════════════════
#  Zone principale d'exécution (drop + cartes + graphiques)
# ═══════════════════════════════════════════════════════════════════

class ExecutionZone(QWidget):
    """
    Layout vertical :
      ┌──────────────────────────────┐
      │  TestInfoCard  (spéc test)   │
      ├──────────────────────────────┤
      │  Scroll → ResultDetailCards  │
      ├──────────────────────────────┤
      │  ResultChartsWidget          │
      └──────────────────────────────┘
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._current_ids: list[str] = []
        self._results_map: dict[str, TestResult] = {}
        self._cls_map: dict[str, type] = {cls.ID: cls for cls in ALL_TESTS}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Barre de titre de zone ──
        hdr = QFrame()
        hdr.setFixedHeight(30)
        hdr.setStyleSheet(
            f"QFrame{{background:{W_TITLEBAR};border-bottom:1px solid {W_BORDER};}}")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(12, 0, 12, 0)
        self._hdr_lbl = _lbl("  Drag & drop a test here to run it", 9, False, W_TEXT_DIM, True)
        hl.addWidget(self._hdr_lbl)
        hl.addStretch()
        root.addWidget(hdr)

        # ── Panneau d'explication du cas de test ──
        self._info_card = TestInfoCard()
        root.addWidget(self._info_card)

        # ── Splitter vertical : cartes | graphiques ──
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setStyleSheet(f"QSplitter::handle{{background:{W_BORDER};height:3px;}}")

        # Scroll des cartes de résultats
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(f"""
            QScrollArea {{background:{W_BG};border:none;}}
            QScrollBar:vertical {{
                background:{W_PANEL};width:7px;border-radius:3px;
            }}
            QScrollBar::handle:vertical {{
                background:{KPIT_GREEN};border-radius:3px;min-height:20px;
            }}
        """)
        self._cards_widget = QWidget()
        self._cards_widget.setStyleSheet(f"background:{W_BG};")
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setContentsMargins(12, 10, 12, 10)
        self._cards_layout.setSpacing(10)
        self._cards_layout.addStretch()
        self._scroll.setWidget(self._cards_widget)
        splitter.addWidget(self._scroll)

        # Graphiques
        self._charts = ResultChartsWidget()
        splitter.addWidget(self._charts)

        splitter.setSizes([500, 320])
        root.addWidget(splitter, 1)

        self._show_placeholder()

    # ── Drop ───────────────────────────────────────────────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(MIME_TEST_ID):
            event.acceptProposedAction()
            self.setStyleSheet("QWidget{background:rgba(141,198,63,0.06);}")
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.setStyleSheet("")

    def dropEvent(self, event):
        self.setStyleSheet("")
        raw = bytes(event.mimeData().data(MIME_TEST_ID)).decode()
        ids = [i.strip() for i in raw.split(",") if i.strip()]
        if ids:
            # Si les tests droppés sont différents des précédents,
            # vider les résultats des anciens tests pour repartir proprement
            if set(ids) != set(self._current_ids):
                self._results_map.clear()
            self._current_ids = ids
            self._refresh()
        event.acceptProposedAction()

    # ── Render ─────────────────────────────────────────────────────

    def _show_placeholder(self):
        self._clear_cards()
        ph = QLabel(
            "No test selected\n\n"
            "Drag a test from the list on the left\nto view its specification and live results.")
        ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ph.setStyleSheet(
            f"color:{W_TEXT_DIM};font-family:'Segoe UI',{FONT_UI};"
            f"font-size:11pt;background:transparent;line-height:1.8;")
        self._cards_layout.insertWidget(0, ph)

    def _clear_cards(self):
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.setParent(None)   # détache immédiatement du conteneur
                w.deleteLater()     # libère la mémoire au prochain cycle Qt
            # Supprimer aussi les spacers (stretch) pour éviter l'accumulation

    def _refresh(self):
        self._clear_cards()
        classes = [self._cls_map[tid] for tid in self._current_ids if tid in self._cls_map]

        # Info card en haut
        self._info_card.update_tests(classes)

        # Cartes de résultats
        for cls in classes:
            result = self._results_map.get(cls.ID)
            card = ResultDetailCard(cls, result)
            self._cards_layout.addWidget(card)
        self._cards_layout.addStretch()

        # Mise à jour du titre
        n = len(self._current_ids)
        n_done = sum(1 for tid in self._current_ids if tid in self._results_map)
        self._hdr_lbl.setText(
            f"  {n} test(s) selected  ·  {n_done} result(s) available")

        # Graphiques
        results_shown = [self._results_map[tid]
                         for tid in self._current_ids if tid in self._results_map]
        self._charts.update_charts(results_shown, self._cls_map)

    # ── API publique ───────────────────────────────────────────────

    def update_result(self, r: TestResult):
        self._results_map[r.test_id] = r
        if r.test_id in self._current_ids:
            self._refresh()

    def reset_results(self, ids: list[str]):
        for tid in ids:
            self._results_map.pop(tid, None)
        if any(tid in self._current_ids for tid in ids):
            self._refresh()

    def update_all_charts(self, results: list[TestResult]):
        """Mise à jour des graphiques avec tous les résultats de la campagne."""
        self._charts.update_charts(results, self._cls_map)


# ═══════════════════════════════════════════════════════════════════
#  Panel principal AutoTestPanel
# ═══════════════════════════════════════════════════════════════════

class AutoTestPanel(QWidget):
    """Panneau principal design Control Desk — vue unique, sans onglets."""

    def __init__(self, runner_factory, parent=None,
                 bench_id: str = "WipeWash-Bench",
                 project: str = "WipeWash Automotive HIL"):
        super().__init__(parent)
        self._factory   = runner_factory
        self._runner    = None
        self._results   = []        # résultats du run courant
        self._all_results = []      # TOUS les résultats accumulés (tous runs)
        self._runs      = []        # liste de runs: [{ids, t_start, t_end, results}]
        self._t_start   = None
        self._t_end     = None
        self._bench_id  = bench_id
        self._project   = project

        self.setStyleSheet(f"background:{W_BG};")
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Toolbar ────────────────────────────────────────────────
        tb = QFrame()
        tb.setFixedHeight(50)
        tb.setStyleSheet(
            f"QFrame{{background:{W_TOOLBAR};"
            f"border-bottom:1.5px solid {W_BORDER};}}")
        tl = QHBoxLayout(tb)
        tl.setContentsMargins(14, 0, 14, 0)
        tl.setSpacing(8)

        self._btn_sel    = _cd_btn("RUN SELECTED",    A_TEAL,    h=34, w=150)
        self._btn_stop   = _cd_btn("STOP",            A_RED,     h=34, w=100)
        self._btn_report = _cd_btn("DOWNLOAD REPORT", A_ORANGE,  h=34, w=200)
        self._btn_clear  = _cd_btn("CLEAR RESULTS",   "#546E7A",  h=34, w=150)
        self._btn_stop.setEnabled(False)
        self._btn_report.setEnabled(False)
        self._btn_clear.setEnabled(False)

        self._btn_sel.clicked.connect(self._run_selected)
        self._btn_stop.clicked.connect(self._stop)
        self._btn_report.clicked.connect(self._download_report)
        self._btn_clear.clicked.connect(self._clear_results)

        self._prog = QProgressBar()
        self._prog.setRange(0, len(ALL_TESTS))
        self._prog.setValue(0)
        self._prog.setFixedHeight(8)
        self._prog.setTextVisible(False)
        self._prog.setStyleSheet(
            f"QProgressBar{{background:{W_PANEL3};border:1px solid {W_BORDER};border-radius:3px;}}"
            f"QProgressBar::chunk{{background:{KPIT_GREEN};border-radius:3px;}}")

        self._lbl_sum   = _lbl("Waiting…", 10, False, W_TEXT_DIM, True)
        self._lbl_redis = _lbl("Redis: —", 9, False, W_TEXT_DIM, True)

        tl.addWidget(self._btn_sel)
        tl.addWidget(self._btn_stop)
        tl.addWidget(self._btn_report)
        tl.addWidget(self._btn_clear)
        tl.addSpacing(16)
        tl.addWidget(self._prog, 1)
        tl.addSpacing(10)
        tl.addWidget(self._lbl_sum)
        tl.addSpacing(20)
        tl.addWidget(self._lbl_redis)
        root.addWidget(tb)

        # ── Corps : arborescence (gauche) + zone exécution (droite) ──
        body = QSplitter(Qt.Orientation.Horizontal)
        body.setStyleSheet(
            f"QSplitter::handle{{background:{W_BORDER};width:3px;}}")

        # Panneau gauche : arbre
        left = QWidget()
        left.setMinimumWidth(240)
        left.setMaximumWidth(360)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)

        tree_hdr = QFrame()
        tree_hdr.setFixedHeight(32)
        tree_hdr.setStyleSheet(f"QFrame{{background:{W_TITLEBAR};}}")
        thl = QHBoxLayout(tree_hdr)
        thl.setContentsMargins(12, 0, 12, 0)
        thl.addWidget(_lbl(f"  TESTS  ({len(ALL_TESTS)})", 9, True, KPIT_GREEN, True))
        thl.addStretch()
        ll.addWidget(tree_hdr)

        self._tree = TestTreeWidget()
        ll.addWidget(self._tree, 1)
        body.addWidget(left)

        # Zone d'exécution
        self._exec_zone = ExecutionZone()
        body.addWidget(self._exec_zone)
        body.setSizes([280, 920])
        root.addWidget(body, 1)

        # ── Status bar ─────────────────────────────────────────────
        bot = QFrame()
        bot.setFixedHeight(24)
        bot.setStyleSheet(
            f"QFrame{{background:{W_TOOLBAR};border-top:1px solid {W_BORDER};}}")
        bl = QHBoxLayout(bot)
        bl.setContentsMargins(12, 0, 12, 0)
        self._lbl_bot = _lbl("Ready", 9, False, W_TEXT_DIM, True)
        bl.addWidget(self._lbl_bot)
        bl.addStretch()
        for st, fg in STATUS_FG.items():
            dot = QLabel("  |  ")
            dot.setFont(QFont(FONT_MONO, 8))
            dot.setStyleSheet(f"color:{fg};background:transparent;")
            bl.addWidget(dot)
            bl.addWidget(_lbl(st, 8, False, W_TEXT_DIM, True))
            bl.addSpacing(6)
        root.addWidget(bot)

    # ── Gestion des tests ──────────────────────────────────────────

    def _get_runner(self):
        if self._runner is None:
            self._runner = self._factory()
            self._runner.test_started.connect(self._on_started)
            self._runner.test_result.connect(self._on_result)
            self._runner.progress.connect(self._on_progress)
            self._runner.all_done.connect(self._on_done)
        return self._runner

    def _run_selected(self):
        runner = self._get_runner()
        ids = []
        for item in self._tree.selectedItems():
            tid = item.data(0, Qt.ItemDataRole.UserRole)
            if tid and tid not in ids:
                ids.append(tid)
        if not ids:
            return
        self._t_start = datetime.datetime.now()
        self._results = []          # résultats du run courant seulement
        self._current_run_ids = ids
        self._exec_zone.reset_results(ids)
        self._btn_sel.setEnabled(False)
        self._btn_stop.setEnabled(True)
        runner.run_selected(ids)

    def _stop(self):
        if self._runner:
            self._runner.stop()
        self._btn_sel.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._lbl_bot.setText("Stopped by user.")

    def _on_started(self, tid: str, name: str):
        self._lbl_bot.setText(f"Running:  [{tid}]  {name}")

    def _on_result(self, r: TestResult):
        self._results.append(r)
        self._all_results.append(r)
        self._exec_zone.update_result(r)
        self._exec_zone.update_all_charts(self._results)

    def _on_progress(self, done: int, total: int):
        self._prog.setMaximum(total)
        self._prog.setValue(done)

    def _on_done(self, results: list):
        self._t_end = datetime.datetime.now()
        n_p = sum(1 for r in results if r.status == "PASS")
        n_f = sum(1 for r in results if r.status == "FAIL")
        n_t = sum(1 for r in results if r.status == "TIMEOUT")
        self._lbl_sum.setText(f"PASS {n_p}   FAIL {n_f}   TIMEOUT {n_t}")
        self._lbl_bot.setText("Campaign finished.")
        self._btn_sel.setEnabled(True)
        self._btn_stop.setEnabled(False)
        # Enregistrer ce run dans la liste des runs accumulés
        self._runs.append({
            "run_index": len(self._runs) + 1,
            "t_start":   self._t_start,
            "t_end":     self._t_end,
            "ids":       getattr(self, "_current_run_ids", []),
            "results":   list(self._results),
        })
        if self._all_results:
            self._btn_report.setEnabled(True)
            self._btn_clear.setEnabled(True)
        n_total = len(self._all_results)
        n_runs  = len(self._runs)
        self._lbl_sum.setText(
            f"Run #{n_runs} — PASS {n_p}  FAIL {n_f}  TIMEOUT {n_t}"
            f"  |  Total accumulé: {n_total} test(s) sur {n_runs} run(s)"
        )
        self._exec_zone.update_all_charts(self._results)

    def _download_report(self):
        if not self._all_results:
            QMessageBox.warning(self, "No Results", "No test results available to export.")
            return
        if not _REPORT_AVAILABLE:
            QMessageBox.warning(self, "Report Unavailable",
                                "report_generator module not found.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Report", f"report_{datetime.datetime.now():%Y%m%d_%H%M%S}.html",
            "HTML Report (*.html);;All Files (*)")
        if not path:
            return
        try:
            gen = ReportGenerator(
                bench_id=self._bench_id,
                project=self._project,
            )
            # t_start = début du 1er run, t_end = fin du dernier run
            t_start = self._runs[0]["t_start"] if self._runs else self._t_start
            t_end   = self._runs[-1]["t_end"]  if self._runs else self._t_end
            gen.generate(
                self._all_results,
                output_path=path,
                t_start=t_start,
                t_end=t_end,
                runs=self._runs,
            )
            QMessageBox.information(self, "Report Saved", f"Report saved to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", str(exc))

    def _clear_results(self):
        reply = QMessageBox.question(
            self, "Clear Results",
            f"Supprimer tous les résultats accumulés ({len(self._all_results)} test(s) sur {len(self._runs)} run(s)) ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._results     = []
        self._all_results = []
        self._runs        = []
        self._t_start     = None
        self._t_end       = None
        self._btn_report.setEnabled(False)
        self._btn_clear.setEnabled(False)
        self._lbl_sum.setText("Waiting…")
        self._lbl_bot.setText("Results cleared.")

    # ── Redis ──────────────────────────────────────────────────────

    def set_redis_status(self, connected: bool, host: str = ""):
        if connected:
            self._lbl_redis.setText(f"Redis: {host}")
            self._lbl_redis.setStyleSheet(
                f"color:{A_GREEN};background:transparent;font-size:9pt;")
        else:
            self._lbl_redis.setText("Redis: disconnected")
            self._lbl_redis.setStyleSheet(
                f"color:#C62828;background:transparent;font-size:9pt;")