
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QGridLayout,
    QCheckBox, QLabel, QComboBox, QPushButton,
    QHBoxLayout, QSpinBox, QTableWidgetItem
)
from PySide6.QtCore import Signal, QTimer, Qt
_STOP_STYLE = (
    "QPushButton { background-color: #7f1d1d; color: #e0e0e0;"
    " border: 1px solid #dc2626; border-radius: 6px;"
    " padding: 6px 16px; font-weight: bold; }"
    "QPushButton:hover { background-color: #dc2626; }"
    "QPushButton:disabled { background-color: #3a1a1a; color: #666; border-color: #555; }"
)



class RoutineCountdown(QLabel):
    """
    Affiche un compte à rebours en secondes pour une routine.
    Usage : countdown.start(duration_s)
    """

    _S = "QLabel {{ color:{fg}; font-weight:bold; font-size:13px; background:{bg}; border:1px solid {bd}; border-radius:6px; padding:2px 6px; }}"
    _DARK = dict(fg="#69f0ae", bg="#1a2a1a", bd="#2e7d32")
    _LIGHT = dict(fg="#1b5e20", bg="#e8f5e9", bd="#4caf50")
    _STOP_D = dict(fg="#ff7043", bg="#2a1010", bd="#c62828")
    _STOP_L = dict(fg="#b71c1c", bg="#ffebee", bd="#e53935")
    _DONE_D = dict(fg="#aed581", bg="#1a2a10", bd="#558b2f")
    _DONE_L = dict(fg="#33691e", bg="#f1f8e9", bd="#7cb342")

    def __init__(self, parent=None):
        super().__init__("--:--", parent)
        self._dark = True
        self._remaining = 0
        self._timer = QTimer()
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self.setFixedWidth(90)
        self.setFixedHeight(38)
        self.setAlignment(Qt.AlignCenter)
        self._apply(self._DARK)

    def set_dark_mode(self, dark: bool):
        self._dark = dark
        self._apply(self._DARK if dark else self._LIGHT)

    def _apply(self, t):
        self.setStyleSheet(self._S.format(**t))

    def start(self, seconds: int):
        self._timer.stop()
        self._remaining = seconds
        self._apply(self._DARK if self._dark else self._LIGHT)
        self.setText(f"⏱ {self._remaining}s")
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self.setText("⛔ Stop")
        self._apply(self._STOP_D if self._dark else self._STOP_L)

    def _tick(self):
        self._remaining -= 1
        if self._remaining <= 0:
            self._timer.stop()
            self.setText("✅ Done")
            self._apply(self._DONE_D if self._dark else self._DONE_L)
        else:
            self.setText(f"⏱ {self._remaining}s")


class HILPage(QWidget):
    apply_coding_requested = Signal(object)
    routine_requested = Signal(int, int)  # (routine_id, value)  value=-1 → stop
    read_status_requested = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_target = 0x700
        layout = QVBoxLayout(self)

        # ── HIL Coding ────────────────────────────────────────────────
        coding_group = QGroupBox("HIL Coding")
        coding_layout = QGridLayout()

        self.rain_sensor = QCheckBox("Rain Sensor Installed (F200)")
        self.wc_available = QCheckBox("WC Available (F201)")
        self.rear_wiper = QCheckBox("Rear Wiper Available (F202)")

        coding_layout.addWidget(self.rain_sensor, 0, 0)
        coding_layout.addWidget(self.wc_available, 0, 1)
        coding_layout.addWidget(self.rear_wiper, 0, 2)

        coding_layout.addWidget(QLabel("Front Wash Direction (F203)"), 1, 0)
        self.front_wash = QComboBox()
        self.front_wash.addItems(["Forward", "Backward"])
        coding_layout.addWidget(self.front_wash, 1, 1)

        coding_layout.addWidget(QLabel("Rear Camera Direction (F204)"), 2, 0)
        self.rear_camera = QComboBox()
        self.rear_camera.addItems(["Backward", "Forward"])
        coding_layout.addWidget(self.rear_camera, 2, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.apply_btn = QPushButton("Apply Coding")
        btn_row.addWidget(self.apply_btn)
        coding_layout.addLayout(btn_row, 3, 0, 1, 3)

        coding_group.setLayout(coding_layout)
        layout.addWidget(coding_group)
        # ── HIL Routines ──────────────────────────────────────────────
        routine_group = QGroupBox("HIL Routines")
        grid = QGridLayout()
        grid.setSpacing(8)
        # Colonnes : 0=Label  1=SpinBox  2=Start  3=Stop  4=Timer

        # Largeurs fixes des colonnes
        COL_LBL = 200
        COL_SPIN = 160
        COL_START = 200
        COL_STOP = 120
        COL_CD = 90
        ROW_H = 38

        # ── Ligne 0 : Front Wiper ────────────────────────────────────
        lbl0 = QLabel("Front Wiper Duration (s):")
        lbl0.setFixedWidth(COL_LBL)
        grid.addWidget(lbl0, 0, 0)

        self.front_wiper_duration = QSpinBox()
        self.front_wiper_duration.setRange(1, 60)
        self.front_wiper_duration.setValue(5)
        self.front_wiper_duration.setFixedHeight(ROW_H)
        grid.addWidget(self.front_wiper_duration, 0, 1)

        self.front_wiper_btn = QPushButton("Start Front Wiper")
        self.front_wiper_btn.setFixedHeight(ROW_H)
        grid.addWidget(self.front_wiper_btn, 0, 2)

        self.front_wiper_stop_btn = QPushButton("Stop")
        self.front_wiper_stop_btn.setFixedHeight(ROW_H)
        self.front_wiper_stop_btn.setStyleSheet(_STOP_STYLE)
        grid.addWidget(self.front_wiper_stop_btn, 0, 3)

        self.front_countdown = RoutineCountdown()
        grid.addWidget(self.front_countdown, 0, 4)

        # ── Ligne 1 : Rear Wiper ─────────────────────────────────────
        lbl1 = QLabel("Rear Wiper Duration (s):")
        lbl1.setFixedWidth(COL_LBL)
        grid.addWidget(lbl1, 1, 0)

        self.rear_wiper_duration = QSpinBox()
        self.rear_wiper_duration.setRange(1, 60)
        self.rear_wiper_duration.setValue(5)
        self.rear_wiper_duration.setFixedHeight(ROW_H)
        grid.addWidget(self.rear_wiper_duration, 1, 1)

        self.rear_wiper_btn = QPushButton("Start Rear Wiper")
        self.rear_wiper_btn.setFixedHeight(ROW_H)
        grid.addWidget(self.rear_wiper_btn, 1, 2)

        self.rear_wiper_stop_btn = QPushButton("Stop")
        self.rear_wiper_stop_btn.setFixedHeight(ROW_H)
        self.rear_wiper_stop_btn.setStyleSheet(_STOP_STYLE)
        grid.addWidget(self.rear_wiper_stop_btn, 1, 3)

        self.rear_countdown = RoutineCountdown()
        grid.addWidget(self.rear_countdown, 1, 4)

        # ── Ligne 2 : Pump Test ──────────────────────────────────────
        lbl2 = QLabel("Pump Test Duration (s):")
        lbl2.setFixedWidth(COL_LBL)
        grid.addWidget(lbl2, 2, 0)

        self.pump_duration = QSpinBox()
        self.pump_duration.setRange(1, 60)
        self.pump_duration.setValue(5)
        self.pump_duration.setFixedHeight(ROW_H)
        grid.addWidget(self.pump_duration, 2, 1)

        self.pump_test_btn = QPushButton("Start Pump Test")
        self.pump_test_btn.setFixedHeight(ROW_H)
        grid.addWidget(self.pump_test_btn, 2, 2)

        self.pump_stop_btn = QPushButton("Stop")
        self.pump_stop_btn.setFixedHeight(ROW_H)
        self.pump_stop_btn.setStyleSheet(_STOP_STYLE)
        grid.addWidget(self.pump_stop_btn, 2, 3)

        self.pump_countdown = RoutineCountdown()
        grid.addWidget(self.pump_countdown, 2, 4)

        # ── Ligne 3 : Rain Simulation — col 4 volontairement vide ───
        lbl3 = QLabel("Rain Simulation (0x0205):")
        lbl3.setFixedWidth(COL_LBL)
        grid.addWidget(lbl3, 3, 0)

        self.rain_value = QSpinBox()
        self.rain_value.setRange(0, 100)
        self.rain_value.setFixedHeight(ROW_H)
        grid.addWidget(self.rain_value, 3, 1)

        # col 2 = Apply Rain (sous Start *)
        self.rain_apply_btn = QPushButton("Apply Rain")
        self.rain_apply_btn.setFixedHeight(ROW_H)
        grid.addWidget(self.rain_apply_btn, 3, 2)

        # col 3 = Stop (sous Stop *)
        self.rain_stop_btn = QPushButton("Stop")
        self.rain_stop_btn.setFixedHeight(ROW_H)
        self.rain_stop_btn.setStyleSheet(_STOP_STYLE)
        grid.addWidget(self.rain_stop_btn, 3, 3)

        # col 4 vide (sous les timers)
        grid.addWidget(QLabel(""), 3, 4)

        # Stretch colonnes 1,2,3 pour remplir l'espace
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 2)
        grid.setColumnStretch(3, 2)

        routine_group.setLayout(grid)
        layout.addWidget(routine_group)

        # ── System Status ─────────────────────────────────────────────
        status_group = QGroupBox("System Status (Read DID 0x22)")
        status_layout = QVBoxLayout()

        self.read_status_btn = QPushButton("Read System Status")
        status_layout.addWidget(self.read_status_btn)

        from PySide6.QtWidgets import QTableWidget
        self.status_table = QTableWidget(0, 3)
        self.status_table.setHorizontalHeaderLabels(["DID", "Name", "Value"])
        self.status_table.horizontalHeader().setStretchLastSection(True)
        status_layout.addWidget(self.status_table)

        status_group.setLayout(status_layout)
        layout.addWidget(status_group)

        layout.addStretch()

        # ── Signal connections ────────────────────────────────────────
        self.apply_btn.clicked.connect(self._on_apply_clicked)
        self.read_status_btn.clicked.connect(self._on_read_status_clicked)

        self.front_wiper_btn.clicked.connect(self._start_front_wiper)
        self.front_wiper_stop_btn.clicked.connect(self._stop_front_wiper)

        self.rear_wiper_btn.clicked.connect(self._start_rear_wiper)
        self.rear_wiper_stop_btn.clicked.connect(self._stop_rear_wiper)

        self.pump_test_btn.clicked.connect(self._start_pump)
        self.pump_stop_btn.clicked.connect(self._stop_pump)

        self.rain_apply_btn.clicked.connect(self._apply_rain)
        self.rain_stop_btn.clicked.connect(self._stop_rain)

    # ── Target ────────────────────────────────────────────────────────

    def set_target(self, addr: int):
        self._current_target = addr

    # ── Coding ────────────────────────────────────────────────────────

    def _on_apply_clicked(self):
        coding = {
            0xF200: 1 if self.rain_sensor.isChecked() else 0,
            0xF201: 1 if self.wc_available.isChecked() else 0,
            0xF202: 1 if self.rear_wiper.isChecked() else 0,
            0xF203: self.front_wash.currentIndex(),
            0xF204: self.rear_camera.currentIndex(),
        }
        self.apply_coding_requested.emit(coding)

    # ── Routines — Start ──────────────────────────────────────────────
    def _start_front_wiper(self):
        self.routine_requested.emit(0x0201, self.front_wiper_duration.value())
        self.front_countdown.start(self.front_wiper_duration.value())

    def _start_rear_wiper(self):
        self.routine_requested.emit(0x0202, self.rear_wiper_duration.value())
        self.rear_countdown.start(self.rear_wiper_duration.value())

    def _start_pump(self):
        rid = 0x0203 if self.front_wash.currentIndex() == 0 else 0x0204
        self.routine_requested.emit(rid, self.pump_duration.value())
        self.pump_countdown.start(self.pump_duration.value())

    def _apply_rain(self):
        self.routine_requested.emit(0x0205, self.rain_value.value())

    # ── Routines — Stop ───────────────────────────────────────────────

    def _stop_front_wiper(self):
        self.routine_requested.emit(0x0201, -1)
        self.front_countdown.stop()

    def _stop_rear_wiper(self):
        self.routine_requested.emit(0x0202, -1)
        self.rear_countdown.stop()

    def _stop_pump(self):
        rid = 0x0203 if self.front_wash.currentIndex() == 0 else 0x0204
        self.routine_requested.emit(rid, -1)
        self.pump_countdown.stop()

    def _stop_rain(self):
        self.routine_requested.emit(0x0205, -1)

    def apply_theme(self, dark: bool):
        self.front_countdown.set_dark_mode(dark)
        self.rear_countdown.set_dark_mode(dark)
        self.pump_countdown.set_dark_mode(dark)

    # ── System Status ─────────────────────────────────────────────────

    def _on_read_status_clicked(self):
        from core.constants import LOGICAL_ADDR_WC
        from PySide6.QtCore import QTimer

        if self._current_target == LOGICAL_ADDR_WC:
            dids = [0xF000, 0xF001, 0xF002, 0xF003, 0xF004]
            names = ["WiperCurrentMode", "WiperSpeed", "BladePosition",
                     "MotorCurrent", "PumpStatus"]
        else:
            dids = [0xF100, 0xF101, 0xF102, 0xF103,
                    0xF104, 0xF105, 0xF106, 0xF107]
            names = ["WiperCurrentMode", "WiperSpeed", "BladePosition",
                     "MotorCurrent", "PumpStatus", "RainIntensity",
                     "RearWiperStatus", "ErrorState"]

        from PySide6.QtWidgets import QTableWidgetItem
        self.status_table.setRowCount(len(dids))
        for row, did in enumerate(dids):
            self.status_table.setItem(row, 0, QTableWidgetItem(f"0x{did:04X}"))
            self.status_table.setItem(row, 1, QTableWidgetItem(names[row]))
            self.status_table.setItem(row, 2, QTableWidgetItem("..."))

        QTimer.singleShot(300, lambda: self.read_status_requested.emit(dids))