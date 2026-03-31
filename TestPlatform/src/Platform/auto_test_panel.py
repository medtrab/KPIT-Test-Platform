"""
auto_test_panel.py  —  Panneau Qt "Tests Automatiques WipeWash"
===============================================================
Onglet dédié dans MainWindow.
Contraintes issues de : Contraintes_Temps_WipeWash.docx
"""

import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame,
    QTableWidget, QTableWidgetItem, QPushButton,
    QLabel, QProgressBar, QHeaderView,
    QAbstractItemView, QTextEdit, QSplitter,
)
from PyQt6.QtCore  import Qt
from PyQt6.QtGui   import QColor, QFont

from constants import (
    FONT_UI, FONT_MONO,
    W_BG, W_PANEL, W_PANEL2, W_PANEL3,
    W_BORDER, W_TOOLBAR, W_TITLEBAR,
    W_TEXT, W_TEXT_DIM, W_TEXT_HDR,
    A_GREEN, A_RED, A_ORANGE, A_AMBER, A_TEAL, A_TEAL2,
)
from widgets_base  import _lbl, _cd_btn
from test_cases    import ALL_TESTS, TestResult


# ─── Couleurs par statut ──────────────────────────────────────────────────
STATUS_FG = {
    "PASS"   : "#1B5E20",
    "FAIL"   : "#B71C1C",
    "TIMEOUT": "#E65100",
    "RUNNING": "#0D47A1",
    "PENDING": W_TEXT_DIM,
}
STATUS_BG = {
    "PASS"   : "#E8F5E9",
    "FAIL"   : "#FFEBEE",
    "TIMEOUT": "#FFF3E0",
    "RUNNING": "#E3F2FD",
    "PENDING": W_PANEL2,
}
CAT_FG = {
    "CYCLE"         : A_TEAL,
    "TIMEOUT"       : A_AMBER,
    "FONCTIONNEL"   : A_ORANGE,
    "FONCTIONNEL_BCM": "#8E24AA",   # violet — tests WSM BCM
}

# Colonnes
COLS = ["ID", "Nom", "Catégorie", "Référence", "Limite", "Statut", "Mesuré", "Détails"]
C_ID, C_NAME, C_CAT, C_REF, C_LIMIT, C_STATUS, C_MEAS, C_DETAIL = range(8)


class AutoTestPanel(QWidget):
    """
    Panneau principal Tests Automatiques.

    runner_factory : callable() → TestRunner
    Appelé au premier Run pour créer le runner avec les workers disponibles.
    """

    def __init__(self, runner_factory, parent=None):
        super().__init__(parent)
        self._factory = runner_factory
        self._runner  = None
        self.setStyleSheet(f"background:{W_BG};")
        self._build()
        self._populate_pending()

    # ─── Construction ────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Barre d'outils ──
        tb = QFrame(); tb.setFixedHeight(44)
        tb.setStyleSheet(
            f"QFrame{{background:{W_TOOLBAR};border-bottom:1px solid {W_BORDER};}}")
        tl = QHBoxLayout(tb)
        tl.setContentsMargins(12, 0, 12, 0); tl.setSpacing(8)

        self._btn_all  = _cd_btn("▶  RUN ALL",   A_GREEN, h=30, w=110)
        self._btn_sel  = _cd_btn("▶  SELECTED",  A_TEAL,  h=30, w=110)
        self._btn_stop = _cd_btn("⏹  STOP",      A_RED,   h=30, w=80)
        self._btn_stop.setEnabled(False)

        self._btn_all .clicked.connect(self._run_all)
        self._btn_sel .clicked.connect(self._run_selected)
        self._btn_stop.clicked.connect(self._stop)

        self._prog = QProgressBar()
        self._prog.setRange(0, len(ALL_TESTS)); self._prog.setValue(0)
        self._prog.setFixedHeight(14); self._prog.setTextVisible(False)
        self._prog.setStyleSheet(
            f"QProgressBar{{background:{W_PANEL3};border:1px solid {W_BORDER};"
            f"border-radius:2px;}}"
            f"QProgressBar::chunk{{background:{A_GREEN};border-radius:2px;}}")

        self._lbl_sum = _lbl("En attente", 10, False, W_TEXT_DIM, True)

        # Indicateur Redis (●  Redis: connecté / déconnecté)
        self._lbl_redis = _lbl("● Redis: —", 9, False, W_TEXT_DIM, True)

        tl.addWidget(self._btn_all)
        tl.addWidget(self._btn_sel)
        tl.addWidget(self._btn_stop)
        tl.addSpacing(10)
        tl.addWidget(self._prog, 1)
        tl.addSpacing(8)
        tl.addWidget(self._lbl_sum)
        tl.addSpacing(16)
        tl.addWidget(self._lbl_redis)
        root.addWidget(tb)

        # ── Splitter tableau + log ──
        sp = QSplitter(Qt.Orientation.Vertical)
        sp.setStyleSheet(
            f"QSplitter::handle{{background:{W_BORDER};height:3px;}}")

        # Tableau résultats
        self._tbl = QTableWidget(0, len(COLS))
        self._tbl.setHorizontalHeaderLabels(COLS)
        self._tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tbl.setAlternatingRowColors(False)
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.horizontalHeader().setStretchLastSection(True)
        self._tbl.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._tbl.setStyleSheet(f"""
            QTableWidget {{
                background:{W_PANEL};color:{W_TEXT};border:none;
                gridline-color:{W_PANEL3};
                font-family:{FONT_MONO};font-size:11pt;
                selection-background-color:{W_PANEL3};selection-color:{W_TEXT};
            }}
            QHeaderView::section {{
                background:{W_TITLEBAR};color:{W_TEXT_HDR};border:none;
                border-bottom:1px solid {W_BORDER};border-right:1px solid {W_BORDER};
                padding:4px 8px;font-family:{FONT_UI};font-size:10pt;font-weight:bold;
            }}
            QTableWidget::item {{padding:2px 8px;border-bottom:1px solid {W_PANEL3};}}
        """)
        sp.addWidget(self._tbl)

        # Console log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(160)
        self._log.setFont(QFont(FONT_MONO, 10))
        self._log.setStyleSheet(
            f"QTextEdit{{background:{W_PANEL2};color:{W_TEXT};border:none;"
            f"border-top:1px solid {W_BORDER};}}")
        sp.addWidget(self._log)
        sp.setSizes([500, 160])
        root.addWidget(sp, 1)

        # ── Barre de statut bas ──
        bot = QFrame(); bot.setFixedHeight(24)
        bot.setStyleSheet(
            f"QFrame{{background:{W_TOOLBAR};border-top:1px solid {W_BORDER};}}")
        bl = QHBoxLayout(bot); bl.setContentsMargins(12, 0, 12, 0)
        self._lbl_bot = _lbl("Prêt", 10, False, W_TEXT_DIM, True)
        bl.addWidget(self._lbl_bot); bl.addStretch()
        for st, fg in STATUS_FG.items():
            d = QLabel("●"); d.setFont(QFont(FONT_MONO, 10))
            d.setStyleSheet(f"color:{fg};background:transparent;")
            bl.addWidget(d)
            bl.addWidget(_lbl(st, 9, False, W_TEXT_DIM, True))
            bl.addSpacing(6)
        # Légende catégorie BCM
        d2 = QLabel("●"); d2.setFont(QFont(FONT_MONO, 10))
        d2.setStyleSheet("color:#8E24AA;background:transparent;")
        bl.addWidget(d2)
        bl.addWidget(_lbl("BCM", 9, False, W_TEXT_DIM, True))
        root.addWidget(bot)

    def _populate_pending(self):
        self._tbl.setUpdatesEnabled(False)
        self._tbl.setRowCount(len(ALL_TESTS))
        self._row: dict[str, int] = {}
        for i, cls in enumerate(ALL_TESTS):
            self._row[cls.ID] = i
            self._set_row(i, TestResult(
                test_id=cls.ID, name=cls.NAME, category=cls.CATEGORY,
                ref=cls.REF, status="PENDING", limit=cls.LIMIT_STR))
            self._tbl.setRowHeight(i, 24)
        self._tbl.setUpdatesEnabled(True)

    def _set_row(self, row: int, r: TestResult):
        fg = STATUS_FG.get(r.status, W_TEXT)
        bg = STATUS_BG.get(r.status, W_PANEL2)
        vals = [r.test_id, r.name, r.category, r.ref,
                r.limit, r.status, r.measured, r.details]
        for col, val in enumerate(vals):
            it = QTableWidgetItem(val)
            it.setTextAlignment(
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            if col == C_STATUS:
                it.setForeground(QColor(fg))
                it.setBackground(QColor(bg))
                it.setFont(QFont(FONT_MONO, 11, QFont.Weight.Bold))
            elif col == C_CAT:
                it.setForeground(QColor(CAT_FG.get(r.category, W_TEXT_DIM)))
            else:
                it.setForeground(QColor(W_TEXT))
            self._tbl.setItem(row, col, it)

    # ─── Boutons ─────────────────────────────────────────────────────
    def _get_runner(self):
        if self._runner is None:
            self._runner = self._factory()
            self._runner.test_started.connect(self._on_started)
            self._runner.test_result .connect(self._on_result)
            self._runner.progress    .connect(self._on_progress)
            self._runner.all_done    .connect(self._on_done)
            self._runner.log_msg     .connect(self._log_append)
        return self._runner

    def _run_all(self):
        self._reset_all()
        self._btn_all.setEnabled(False)
        self._btn_sel.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._log_append("══ RUN ALL ══════════════════════════════════")
        self._get_runner().run_all()

    def _run_selected(self):
        ids = []
        for item in self._tbl.selectedItems():
            r = item.row()
            id_item = self._tbl.item(r, C_ID)
            if id_item and id_item.text() not in ids:
                ids.append(id_item.text())
        if not ids:
            self._log_append("⚠ Aucun test sélectionné")
            return
        self._reset_ids(ids)
        self._btn_all.setEnabled(False)
        self._btn_sel.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._log_append(f"══ RUN SELECTED : {', '.join(ids)} ══════════")
        self._get_runner().run_selected(ids)

    def _stop(self):
        if self._runner:
            self._runner.stop()
        self._btn_all.setEnabled(True)
        self._btn_sel.setEnabled(True)
        self._btn_stop.setEnabled(False)

    def _reset_all(self):
        self._tbl.setUpdatesEnabled(False)
        for cls in ALL_TESTS:
            self._set_row(self._row[cls.ID], TestResult(
                test_id=cls.ID, name=cls.NAME, category=cls.CATEGORY,
                ref=cls.REF, status="PENDING", limit=cls.LIMIT_STR))
        self._tbl.setUpdatesEnabled(True)
        self._prog.setValue(0)
        self._lbl_sum.setText("En cours…")

    def _reset_ids(self, ids):
        self._tbl.setUpdatesEnabled(False)
        for cls in ALL_TESTS:
            if cls.ID in ids:
                self._set_row(self._row[cls.ID], TestResult(
                    test_id=cls.ID, name=cls.NAME, category=cls.CATEGORY,
                    ref=cls.REF, status="PENDING", limit=cls.LIMIT_STR))
        self._tbl.setUpdatesEnabled(True)
        self._prog.setValue(0)
        self._lbl_sum.setText("En cours…")

    # ─── Slots TestRunner ─────────────────────────────────────────────
    def _on_started(self, tid: str, name: str):
        if tid in self._row:
            row = self._row[tid]
            it = QTableWidgetItem("RUNNING")
            it.setForeground(QColor(STATUS_FG["RUNNING"]))
            it.setBackground(QColor(STATUS_BG["RUNNING"]))
            it.setFont(QFont(FONT_MONO, 11, QFont.Weight.Bold))
            it.setTextAlignment(
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            self._tbl.setItem(row, C_STATUS, it)
            self._tbl.scrollTo(self._tbl.model().index(row, 0))
        self._lbl_bot.setText(f"En cours : [{tid}] {name}")

    def _on_result(self, r: TestResult):
        if r.test_id in self._row:
            self._set_row(self._row[r.test_id], r)

    def _on_progress(self, done: int, total: int):
        self._prog.setMaximum(total)
        self._prog.setValue(done)

    def _on_done(self, results: list):
        n_p = sum(1 for r in results if r.status == "PASS")
        n_f = sum(1 for r in results if r.status == "FAIL")
        n_t = sum(1 for r in results if r.status == "TIMEOUT")
        self._lbl_sum.setText(f"PASS {n_p}  •  FAIL {n_f}  •  TIMEOUT {n_t}")
        self._lbl_bot.setText("Terminé")
        self._btn_all.setEnabled(True)
        self._btn_sel.setEnabled(True)
        self._btn_stop.setEnabled(False)

    def set_redis_status(self, connected: bool, host: str = ""):
        """Appelé par MainWindow pour afficher l'état Redis dans la toolbar."""
        if connected:
            self._lbl_redis.setText(f"● Redis: {host}")
            self._lbl_redis.setStyleSheet(
                f"color:{A_GREEN};background:transparent;font-size:9pt;")
        else:
            self._lbl_redis.setText("● Redis: déconnecté")
            self._lbl_redis.setStyleSheet(
                f"color:#B71C1C;background:transparent;font-size:9pt;")

    def _log_append(self, msg: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._log.append(f"[{ts}]  {msg}")
