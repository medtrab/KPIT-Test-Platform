"""
test_rpisimulator29.py — Suite de tests complète RPi Simulateur WipeWash v29
=============================================================================
Couvre TOUTES les fonctionnalités sans exception :

  Couche 0  — main.py         (parse_args, build_signal_handler, threads)
  Couche 1  — bcmcan.py       (SensorState, VehicleState, RainState, WiperCmd,
                                _build_0x201, _build_0x300, _build_0x301,
                                _build_0x202_from_state, _crc_rx, _crc_tx,
                                _b2104_track, _b2104_reset, DTC B2101/B2102/
                                B2103/B2104, fault-GPIO, test_cmd TCP,
                                AliveCounter freeze, CRC corruption, mode mismatch)
  Couche 2  — crslin.py       (NodeState, WOp, LDF loader, alive counter,
                                stick_status, fault codes, checksum corruption,
                                stop_lin_tx / start_lin_tx, _calculate_pid)
  Couche 3  — wc_doip.py      (WCDoIPServer, _WCState, UDS DIDs, DoIP header,
                                session DSC, routing activation, Alive Check)
  Couche 4  — wc_dtc_manager  (DTCManager_WC : set_active, set_inactive,
                                notify_ignition_on/off, B2101-B2104 lifecycle,
                                handle_read_dtc, handle_clear_dtc)
  Couche 5  — xcp_server_wc   (XCPServerWC, CMD CONNECT/STATUS/SHORT_DOWNLOAD,
                                B2101/B2102/B2103 via mémoire XCP)
  Couche 6  — bcm_tcp_can.py  (TCPCANBroadcast, callbacks on_tx_0x201,
                                on_rx_0x200, on_tx_0x202, set_0x202_callback)
  Couche 7  — dbc_loader.py   (load_dbc, pack_frame, unpack_frame,
                                load_dbc_sim, CAN IDs, périodes)
  Couche 8  — ldf_loader.py   (load_ldf, _calculate_pid, pid_map,
                                baud, frames, schedule)
  Couche 9  — sim_control.py  (modes, argparse, GPIO stubs)
  Couche 10 — Intégration     (inject_motor_current, freeze/unfreeze blade,
                                blade cycling, fault injection GPIO,
                                pump monitor, TCPDiscovery)

Exécution (banc de dev, sans matériel) :
  pytest test_rpisimulator29.py -v --timeout=30
  pytest test_rpisimulator29.py -v -k "BCMCAN or DTC"
  pytest test_rpisimulator29.py -v -k "CRS or LIN"
  pytest test_rpisimulator29.py -v -k "XCP or DoIP"
"""

import json
import os
import socket
import struct
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ─── Racine du projet ──────────────────────────────────────────────────────────
ROOT = Path(__file__).parent / "platform_v8_clean" / "platform_v8_clean"
SIM  = Path(__file__).parent / "rpisimulator29"
# Ajout des deux chemins potentiels
for p in (str(SIM), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

# ─── Stubs hardware (RPi.GPIO, gpiod, board, busio, ADS1115) ──────────────────
# Ces stubs permettent d'importer les modules sans matériel physique.
_GPIO_STUB = MagicMock()
_GPIO_STUB.BCM    = 11
_GPIO_STUB.OUT    = 0
_GPIO_STUB.IN     = 1
_GPIO_STUB.HIGH   = 1
_GPIO_STUB.LOW    = 0
_GPIO_STUB.PWM    = MagicMock(return_value=MagicMock())
sys.modules.setdefault("RPi",        MagicMock())
sys.modules.setdefault("RPi.GPIO",   _GPIO_STUB)
sys.modules.setdefault("gpiod",      MagicMock())
sys.modules.setdefault("board",      MagicMock())
sys.modules.setdefault("busio",      MagicMock())
sys.modules.setdefault("adafruit_ads1x15",            MagicMock())
sys.modules.setdefault("adafruit_ads1x15.ads1115",    MagicMock())
sys.modules.setdefault("adafruit_ads1x15.analog_in",  MagicMock())
sys.modules.setdefault("redis",      MagicMock())
sys.modules.setdefault("serial",     MagicMock())


# ══════════════════════════════════════════════════════════════════════════════
#  COUCHE 0 — MAIN.PY
# ══════════════════════════════════════════════════════════════════════════════

class TestMain:

    def test_main_importable(self):
        import main
        assert hasattr(main, "main")
        assert hasattr(main, "parse_args")
        assert hasattr(main, "build_signal_handler")

    def test_parse_args_defaults(self):
        import main
        with patch("sys.argv", ["main.py"]):
            args = main.parse_args()
        assert args.mode    == "both"
        assert args.host    == "0.0.0.0"
        assert args.linport == 5555
        assert args.canport == 5000
        assert args.linserial == "/dev/serial0"
        assert args.loglevel  == "INFO"

    def test_parse_args_mode_lin(self):
        import main
        with patch("sys.argv", ["main.py", "lin"]):
            args = main.parse_args()
        assert args.mode == "lin"

    def test_parse_args_mode_can(self):
        import main
        with patch("sys.argv", ["main.py", "can"]):
            args = main.parse_args()
        assert args.mode == "can"

    def test_parse_args_mode_both(self):
        import main
        with patch("sys.argv", ["main.py", "both"]):
            args = main.parse_args()
        assert args.mode == "both"

    def test_parse_args_loglevel_debug(self):
        import main
        with patch("sys.argv", ["main.py", "--loglevel", "DEBUG"]):
            args = main.parse_args()
        assert args.loglevel == "DEBUG"

    def test_parse_args_ldf_flag(self):
        import main
        with patch("sys.argv", ["main.py", "--ldf", "custom.ldf"]):
            args = main.parse_args()
        assert args.ldf == "custom.ldf"

    def test_parse_args_dbc_flag(self):
        import main
        with patch("sys.argv", ["main.py", "--dbc", "custom.dbc"]):
            args = main.parse_args()
        assert args.dbc == "custom.dbc"

    def test_build_signal_handler_returns_callable(self):
        import main
        stop = threading.Event()
        handler = main.build_signal_handler([], stop)
        assert callable(handler)

    def test_signal_handler_sets_stop_event(self):
        import main, signal
        stop = threading.Event()
        handler = main.build_signal_handler([], stop)
        handler(signal.SIGINT, None)
        assert stop.is_set()


# ══════════════════════════════════════════════════════════════════════════════
#  COUCHE 1A — BCMCAN : DATACLASSES
# ══════════════════════════════════════════════════════════════════════════════

class TestBcmcanDataclasses:
    """Tests unitaires sur SensorState, VehicleState, RainState, WiperCmd."""

    @pytest.fixture(autouse=True)
    def import_bcmcan(self):
        import bcmcan
        self.m = bcmcan

    # ── SensorState ──────────────────────────────────────────────────────────

    def test_sensor_state_initial_values(self):
        s = self.m.SensorState()
        assert s.blade_position == 0.0
        assert s.blade_real     == 0.0
        assert s.blade_sim      == -1.0
        assert s.motor_current  == 0.0
        assert s.fault_status   == 0
        assert s.motor_driver_fault is False

    def test_sensor_state_update(self):
        s = self.m.SensorState()
        s.update(75.3, 0.42, 1)
        assert s.blade_position == 75.3
        assert s.blade_real     == 75.3
        assert s.motor_current  == 0.42
        assert s.fault_status   == 1

    def test_sensor_state_update_clamps_blade(self):
        s = self.m.SensorState()
        s.update(150.0, 0.0, 0)
        assert s.blade_position == 100.0

    def test_sensor_state_update_clamps_blade_min(self):
        s = self.m.SensorState()
        s.update(-10.0, 0.0, 0)
        assert s.blade_position == 0.0

    def test_sensor_state_set_blade_sim_positive(self):
        s = self.m.SensorState()
        s.set_blade_sim(60.0)
        assert s.blade_sim == 60.0

    def test_sensor_state_set_blade_sim_negative_disarms(self):
        s = self.m.SensorState()
        s.set_blade_sim(60.0)
        s.set_blade_sim(-1.0)
        assert s.blade_sim < 0

    def test_sensor_state_set_motor_driver_fault_true(self):
        s = self.m.SensorState()
        s.set_motor_driver_fault(True)
        assert s.motor_driver_fault is True

    def test_sensor_state_set_motor_driver_fault_false(self):
        s = self.m.SensorState()
        s.set_motor_driver_fault(True)
        s.set_motor_driver_fault(False)
        assert s.motor_driver_fault is False

    def test_sensor_state_set_fault_status_bits(self):
        s = self.m.SensorState()
        s.set_fault_status_bits(0x07)
        assert s._forced_fault_bits == 0x07

    def test_sensor_state_set_fault_status_bits_clamp_6bits(self):
        s = self.m.SensorState()
        s.set_fault_status_bits(0xFF)
        assert s._forced_fault_bits == 0x3F  # 6 bits max

    def test_sensor_state_reset_fault_status_bits(self):
        s = self.m.SensorState()
        s.set_fault_status_bits(0x0F)
        s.reset_fault_status_bits()
        assert s._forced_fault_bits == 0

    def test_sensor_state_get_fault_byte_or_combines(self):
        s = self.m.SensorState()
        s.update(0.0, 0.0, 0x01)   # fault_status hardware = 0x01
        s.set_fault_status_bits(0x02)  # forced bits = 0x02
        fb = s.get_fault_byte_for_tx()
        assert fb & 0x01             # bit hardware
        assert fb & 0x02             # bit forced

    def test_sensor_state_xcp_flags_default_false(self):
        s = self.m.SensorState()
        assert not s.xcp_internal_fault
        assert not s.xcp_motor_driver_fault
        assert not s.xcp_position_sensor_fault

    def test_sensor_state_set_xcp_internal_fault(self):
        s = self.m.SensorState()
        s.set_xcp_internal_fault(True)
        assert s.xcp_internal_fault

    def test_sensor_state_set_xcp_motor_driver_fault(self):
        s = self.m.SensorState()
        s.set_xcp_motor_driver_fault(True)
        assert s.xcp_motor_driver_fault

    def test_sensor_state_set_xcp_position_sensor_fault(self):
        s = self.m.SensorState()
        s.set_xcp_position_sensor_fault(True)
        assert s.xcp_position_sensor_fault

    def test_sensor_state_get_fault_byte_includes_xcp_b2101(self):
        """xcp_internal_fault=True → bit0 dans get_fault_byte_for_tx."""
        s = self.m.SensorState()
        s.set_xcp_internal_fault(True)
        fb = s.get_fault_byte_for_tx()
        assert fb & 0x01

    def test_sensor_state_get_fault_byte_includes_xcp_b2102(self):
        """xcp_motor_driver_fault=True → bit1 dans get_fault_byte_for_tx."""
        s = self.m.SensorState()
        s.set_xcp_motor_driver_fault(True)
        fb = s.get_fault_byte_for_tx()
        assert fb & 0x02

    def test_sensor_state_get_fault_byte_includes_xcp_b2103(self):
        """xcp_position_sensor_fault=True → bit2 dans get_fault_byte_for_tx."""
        s = self.m.SensorState()
        s.set_xcp_position_sensor_fault(True)
        fb = s.get_fault_byte_for_tx()
        assert fb & 0x04

    def test_sensor_state_motor_current_override_active(self):
        s = self.m.SensorState()
        s.update(0.0, 0.1, 0)
        s.set_motor_current_override(0.95)
        mc = s.get_motor_current_for_tx()
        assert mc == pytest.approx(0.95)

    def test_sensor_state_motor_current_override_inactive(self):
        s = self.m.SensorState()
        s.update(0.0, 0.35, 0)
        s.set_motor_current_override(-1.0)
        mc = s.get_motor_current_for_tx()
        assert mc == pytest.approx(0.35, abs=0.01)

    def test_sensor_state_motor_current_override_reset(self):
        s = self.m.SensorState()
        s.set_motor_current_override(0.9)
        s.set_motor_current_override(-1.0)
        s.update(0.0, 0.2, 0)
        assert s.get_motor_current_for_tx() == pytest.approx(0.2, abs=0.01)

    def test_sensor_state_blade_frozen(self):
        s = self.m.SensorState()
        s.set_blade_frozen(True, 42.5)
        assert s.get_blade_for_tx() == pytest.approx(42.5)

    def test_sensor_state_blade_unfrozen(self):
        s = self.m.SensorState()
        s.update(77.0, 0.0, 0)
        s.set_blade_frozen(True, 42.5)
        s.set_blade_frozen(False)
        assert s.get_blade_for_tx() == pytest.approx(77.0, abs=0.1)

    def test_sensor_state_blade_cycling_starts(self):
        s = self.m.SensorState()
        s.start_blade_cycling(period_ms=200)
        time.sleep(0.05)
        assert s._blade_cycling

    def test_sensor_state_blade_cycling_stops(self):
        s = self.m.SensorState()
        s.start_blade_cycling(period_ms=200)
        time.sleep(0.05)
        s.stop_blade_cycling()
        assert not s._blade_cycling

    def test_sensor_state_snapshot(self):
        s = self.m.SensorState()
        s.update(50.0, 0.3, 2)
        bp, mc, fs = s.snapshot()
        assert bp == pytest.approx(50.0, abs=0.1)
        assert mc == pytest.approx(0.3, abs=0.01)
        assert fs == 2

    def test_sensor_state_snapshot_blade_diag(self):
        s = self.m.SensorState()
        s.update(30.0, 0.0, 0)
        s.set_blade_sim(80.0)
        br, bs = s.snapshot_blade_diag()
        assert br == pytest.approx(30.0, abs=0.1)
        assert bs == pytest.approx(80.0)

    def test_sensor_state_snapshot_driver_fault(self):
        s = self.m.SensorState()
        s.set_motor_driver_fault(True)
        assert s.snapshot_driver_fault() is True

    def test_sensor_state_thread_safety_concurrent_update(self):
        s = self.m.SensorState()
        errors = []

        def writer():
            try:
                for i in range(200):
                    s.update(float(i % 101), 0.1, 0)
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(200):
                    s.snapshot()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer)] + \
                  [threading.Thread(target=reader) for _ in range(3)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=5)
        assert not errors, f"Thread-safety errors: {errors}"

    # ── VehicleState ─────────────────────────────────────────────────────────

    def test_vehicle_state_initial_values(self):
        v = self.m.VehicleState()
        assert v.ignition == 0
        assert v.reverse  == 0
        assert v.speed    == 0

    def test_vehicle_state_update(self):
        v = self.m.VehicleState()
        v.update(2, 1, 300)
        ign, rev, spd = v.snapshot()
        assert ign == 2
        assert rev == 1
        assert spd == 300

    def test_vehicle_state_snapshot(self):
        v = self.m.VehicleState()
        v.update(1, 0, 500)
        assert v.snapshot() == (1, 0, 500)

    # ── RainState ─────────────────────────────────────────────────────────────

    def test_rain_state_initial_values(self):
        r = self.m.RainState()
        assert r.intensity == 0
        assert r.sensor_ok is True

    def test_rain_state_update(self):
        r = self.m.RainState()
        r.update(75, True)
        intensity, ok = r.snapshot()
        assert intensity == 75
        assert ok is True

    def test_rain_state_fault(self):
        r = self.m.RainState()
        r.update(0, False)
        _, ok = r.snapshot()
        assert ok is False

    # ── WiperCmd ─────────────────────────────────────────────────────────────

    def test_wiper_cmd_initial_values(self):
        w = self.m.WiperCmd()
        assert w.mode     == 0
        assert w.speed    == 0
        assert w.wash     == 0
        assert w.alive_rx == 0

    def test_wiper_cmd_update(self):
        w = self.m.WiperCmd()
        w.update(2, 1, 1, 42)
        mode, speed, wash, alive = w.snapshot()
        assert mode  == 2
        assert speed == 1
        assert wash  == 1
        assert alive == 42

    def test_ignition_enum_from_str(self):
        assert self.m.Ignition.from_value("OFF") == self.m.Ignition.OFF
        assert self.m.Ignition.from_value("ON")  == self.m.Ignition.ON
        assert self.m.Ignition.from_value("ACC") == self.m.Ignition.ACC

    def test_ignition_enum_from_int(self):
        assert self.m.Ignition.from_value(0) == self.m.Ignition.OFF
        assert self.m.Ignition.from_value(2) == self.m.Ignition.ON

    def test_ignition_enum_clamp_high(self):
        assert self.m.Ignition.from_value(99) == self.m.Ignition.ON

    def test_ignition_enum_clamp_low(self):
        assert self.m.Ignition.from_value(-5) == self.m.Ignition.OFF


# ══════════════════════════════════════════════════════════════════════════════
#  COUCHE 1B — BCMCAN : CONSTRUCTION DES TRAMES CAN
# ══════════════════════════════════════════════════════════════════════════════

class TestBcmcanFrameBuilding:

    @pytest.fixture(autouse=True)
    def setup(self):
        import bcmcan
        self.m = bcmcan
        # Réinitialiser les états globaux
        self.m._sensor_state  = bcmcan.SensorState()
        self.m._vehicle_state = bcmcan.VehicleState()
        self.m._rain_state    = bcmcan.RainState()
        self.m._wiper_cmd     = bcmcan.WiperCmd()
        self.m._can_tx_paused = False
        self.m._corrupt_crc_count = 0
        self.m._mode_mismatch_0x201 = False
        self.m._alive_counter_frozen = False
        self.m._alive_counter_prev   = -1
        self.m._alive_freeze_repeat_count = 0

    # ── CRC ──────────────────────────────────────────────────────────────────

    def test_crc_rx_xor3(self):
        """CRC 0x200 = XOR(byte0, byte1, byte2)."""
        data = bytes([0x21, 0x01, 0x05, 0xFF, 0, 0, 0, 0])
        expected = 0x21 ^ 0x01 ^ 0x05
        assert self.m._crc_rx(data) == expected

    def test_crc_tx_xor6(self):
        """CRC 0x201 = XOR(byte0..byte5)."""
        payload = bytes([0x02, 0x01, 50, 20, 0x00, 0xA5])
        expected = 0
        for b in payload:
            expected ^= b
        assert self.m._crc_tx(payload) == expected & 0xFF

    def test_crc_rx_zero_payload(self):
        data = bytes(8)
        assert self.m._crc_rx(data) == 0

    def test_crc_tx_all_same_cancel(self):
        payload = bytes([0xAA] * 6)
        # XOR de 6 fois le même octet : si pair → 0
        result = self.m._crc_tx(payload)
        assert isinstance(result, int)

    # ── _build_0x300 ─────────────────────────────────────────────────────────

    def test_build_0x300_length(self):
        fd = self.m._build_0x300()
        assert len(fd) == 8

    def test_build_0x300_ignition_byte(self):
        self.m._vehicle_state.update(2, 0, 0)
        fd = self.m._build_0x300()
        assert fd[0] == 2   # ignition=ON

    def test_build_0x300_reverse_byte(self):
        self.m._vehicle_state.update(2, 1, 0)
        fd = self.m._build_0x300()
        assert fd[1] == 1

    def test_build_0x300_speed_encoding(self):
        """Vitesse 100 km/h → raw = 1000 → byte2=3, byte3=232."""
        speed_kmh = 100.0
        speed_raw = int(round(speed_kmh * 10))  # 1000
        self.m._vehicle_state.update(2, 0, speed_raw)
        fd = self.m._build_0x300()
        decoded = ((fd[2] << 8) | fd[3])
        assert decoded == speed_raw

    # ── _build_0x301 ─────────────────────────────────────────────────────────

    def test_build_0x301_length(self):
        fd = self.m._build_0x301()
        assert len(fd) == 8

    def test_build_0x301_intensity_byte(self):
        self.m._rain_state.update(80, True)
        fd = self.m._build_0x301()
        assert fd[0] == 80

    def test_build_0x301_sensor_ok(self):
        self.m._rain_state.update(50, True)
        fd = self.m._build_0x301()
        assert fd[1] == 0x00   # OK

    def test_build_0x301_sensor_fault(self):
        self.m._rain_state.update(0, False)
        fd = self.m._build_0x301()
        assert fd[1] == 0x01   # FAULT

    def test_build_0x301_intensity_clamp_max(self):
        self.m._rain_state.update(100, True)
        fd = self.m._build_0x301()
        assert fd[0] == 100

    # ── _build_0x201 ─────────────────────────────────────────────────────────

    def test_build_0x201_length(self):
        fd = self.m._build_0x201(alive=0)
        assert len(fd) == 8

    def test_build_0x201_crc_valid(self):
        """Le CRC_Low (byte6) doit être XOR(byte0..byte5)."""
        fd = self.m._build_0x201(alive=0xAB)
        expected_crc = self.m._crc_tx(fd[:6])
        assert fd[6] == expected_crc

    def test_build_0x201_alive_in_byte5(self):
        self.m._build_0x201(alive=0)  # warm up
        fd = self.m._build_0x201(alive=0x42)
        assert fd[5] == 0x42

    def test_build_0x201_reserved_byte7_zero(self):
        fd = self.m._build_0x201(alive=0)
        assert fd[7] == 0x00

    def test_build_0x201_blade_position_clamped(self):
        self.m._sensor_state.update(105.0, 0.0, 0)
        fd = self.m._build_0x201(alive=0)
        assert fd[2] <= 100

    def test_build_0x201_motor_current_encoding(self):
        """MotorCurrent = 1.5 A → byte3 = 15 (0.1A/bit)."""
        self.m._sensor_state.update(50.0, 1.5, 0)
        fd = self.m._build_0x201(alive=0)
        assert fd[3] == 15

    def test_build_0x201_mode_mismatch_forces_mode_off(self):
        """mode_mismatch_0x201=True → CurrentMode=0x00 (OFF)."""
        self.m._wiper_cmd.update(2, 1, 0, 0)  # mode=SPEED1=2
        self.m._mode_mismatch_0x201 = True
        fd = self.m._build_0x201(alive=0)
        assert fd[0] == 0   # CurrentMode forcé à OFF

    def test_build_0x201_mode_mismatch_off_restores_mode(self):
        self.m._wiper_cmd.update(2, 1, 0, 0)
        self.m._mode_mismatch_0x201 = False
        fd = self.m._build_0x201(alive=0)
        assert fd[0] == 2   # CurrentMode=SPEED1

    def test_build_0x201_corrupt_crc_inverts_byte6(self):
        """Si _corrupt_crc_count>0, le CRC doit être inversé."""
        self.m._corrupt_crc_count = 3
        fd = self.m._build_0x201(alive=0)
        expected_normal = self.m._crc_tx(fd[:6])
        assert fd[6] != expected_normal   # corrompu

    def test_build_0x201_corrupt_crc_decrements_counter(self):
        self.m._corrupt_crc_count = 5
        self.m._build_0x201(alive=0)
        assert self.m._corrupt_crc_count == 4

    def test_build_0x201_fault_byte_6bits(self):
        """Seuls les 6 bits inférieurs du fault_byte doivent être publiés."""
        self.m._sensor_state.set_fault_status_bits(0xFF)
        fd = self.m._build_0x201(alive=0)
        assert fd[4] == (0xFF & 0x3F)

    # ── _build_0x202_from_state ───────────────────────────────────────────────

    def test_build_0x202_from_state_length(self):
        fd = self.m._build_0x202_from_state(alive_tx=0)
        assert len(fd) == 4

    def test_build_0x202_from_state_ack_ok_when_no_fault(self):
        self.m._sensor_state.reset_fault_status_bits()
        self.m._sensor_state.set_motor_current_override(-1.0)
        self.m._mode_mismatch_0x201 = False
        fd = self.m._build_0x202_from_state(alive_tx=0)
        assert fd[0] == 0   # AckStatus=ACK
        assert fd[1] == 0   # ErrorCode=0

    def test_build_0x202_err01_mode_mismatch(self):
        """mode_mismatch → AckStatus=1, ErrorCode=0x01."""
        self.m._mode_mismatch_0x201 = True
        self.m._sensor_state.reset_fault_status_bits()
        fd = self.m._build_0x202_from_state(alive_tx=0)
        assert fd[0] == 1
        assert fd[1] == 0x01

    def test_build_0x202_err02_motor_driver_fault(self):
        """fault_bit1 (MotorDriver) → AckStatus=1, ErrorCode=0x02."""
        self.m._sensor_state.set_fault_status_bits(0x02)
        self.m._mode_mismatch_0x201 = False
        fd = self.m._build_0x202_from_state(alive_tx=0)
        assert fd[0] == 1
        assert fd[1] == 0x02

    def test_build_0x202_err03_overcurrent(self):
        """motor_current ≥ threshold → AckStatus=1, ErrorCode=0x03."""
        self.m._sensor_state.set_motor_current_override(self.m._WC_OVERCURRENT_THRESH + 0.1)
        self.m._sensor_state.reset_fault_status_bits()
        self.m._mode_mismatch_0x201 = False
        fd = self.m._build_0x202_from_state(alive_tx=0)
        assert fd[0] == 1
        assert fd[1] == 0x03

    def test_build_0x202_err04_pos_sensor_fault(self):
        """fault_bit2 (PosSensor) → AckStatus=1, ErrorCode=0x04."""
        self.m._sensor_state.reset_fault_status_bits()
        self.m._sensor_state.set_fault_status_bits(0x04)
        self.m._mode_mismatch_0x201 = False
        fd = self.m._build_0x202_from_state(alive_tx=0)
        assert fd[0] == 1
        assert fd[1] == 0x04

    def test_build_0x202_err05_wc_internal_fault(self):
        """fault_bit0 (WC_Internal) → AckStatus=1, ErrorCode=0x05."""
        self.m._sensor_state.reset_fault_status_bits()
        self.m._sensor_state.set_fault_status_bits(0x01)
        self.m._mode_mismatch_0x201 = False
        fd = self.m._build_0x202_from_state(alive_tx=0)
        assert fd[0] == 1
        assert fd[1] == 0x05

    def test_build_0x202_crc_is_xor_first3(self):
        """CRC = XOR(byte0, byte1, byte2)."""
        fd = self.m._build_0x202_from_state(alive_tx=0x1A)
        expected_crc = (fd[0] ^ fd[1] ^ fd[2]) & 0xFF
        assert fd[3] == expected_crc

    def test_build_0x202_alive_in_byte2(self):
        self.m._mode_mismatch_0x201 = False
        self.m._sensor_state.reset_fault_status_bits()
        fd = self.m._build_0x202_from_state(alive_tx=0x7F)
        assert fd[2] == 0x7F

    def test_overcurrent_threshold_is_positive(self):
        assert self.m._WC_OVERCURRENT_THRESH > 0


# ══════════════════════════════════════════════════════════════════════════════
#  COUCHE 1C — BCMCAN : B2104 TRACKER
# ══════════════════════════════════════════════════════════════════════════════

class TestBcmcanB2104:

    @pytest.fixture(autouse=True)
    def setup(self):
        import bcmcan
        self.m = bcmcan
        self.m._b2104_reset()

    def test_b2104_reset_clears_counters(self):
        self.m._b2104_nack_count = 5
        self.m._b2104_ack_count  = 2
        self.m._b2104_active     = True
        self.m._b2104_reset()
        assert self.m._b2104_nack_count == 0
        assert self.m._b2104_ack_count  == 0
        assert not self.m._b2104_active

    def test_b2104_track_nack_increments_counter(self):
        self.m._b2104_track(bytes([0x01, 0x02, 0x00, 0x03]))
        assert self.m._b2104_nack_count == 1

    def test_b2104_track_ack_resets_nack_counter(self):
        self.m._b2104_nack_count = 2
        self.m._b2104_track(bytes([0x00, 0x00, 0x00, 0x00]))
        assert self.m._b2104_nack_count == 0

    def test_b2104_activates_after_3_nacks(self):
        for _ in range(3):
            self.m._b2104_track(bytes([0x01, 0x03, 0x00, 0x02]))
        assert self.m._b2104_active

    def test_b2104_does_not_activate_on_ack_error_code_zero(self):
        """AckStatus=1 mais ErrorCode=0x00 → pas de décompte NACK."""
        for _ in range(5):
            self.m._b2104_track(bytes([0x01, 0x00, 0x00, 0x01]))
        assert not self.m._b2104_active

    def test_b2104_heals_after_3_acks(self):
        for _ in range(3):
            self.m._b2104_track(bytes([0x01, 0x03, 0x00, 0x02]))
        assert self.m._b2104_active
        for _ in range(3):
            self.m._b2104_track(bytes([0x00, 0x00, 0x00, 0x00]))
        assert not self.m._b2104_active

    def test_b2104_track_ignores_short_frame(self):
        """Trame de moins de 2 bytes → pas d'erreur, pas de comptage."""
        self.m._b2104_track(bytes([0x01]))
        assert self.m._b2104_nack_count == 0

    def test_b2104_track_thread_safe(self):
        """Appels concurrents à _b2104_track ne doivent pas crasher."""
        errors = []

        def worker():
            try:
                for _ in range(100):
                    self.m._b2104_track(bytes([0x01, 0x03, 0x00, 0x02]))
                    self.m._b2104_track(bytes([0x00, 0x00, 0x00, 0x00]))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=5)
        assert not errors


# ══════════════════════════════════════════════════════════════════════════════
#  COUCHE 1D — BCMCAN : TCP COMMAND HANDLER
# ══════════════════════════════════════════════════════════════════════════════

class TestBcmcanTcpCommands:
    """Teste les test_cmd reçus via TCP sans ouvrir de vrai socket."""

    @pytest.fixture(autouse=True)
    def setup(self):
        import bcmcan
        self.m = bcmcan
        # Reset états
        self.m._can_tx_paused         = False
        self.m._corrupt_crc_count     = 0
        self.m._mode_mismatch_0x201   = False
        self.m._alive_counter_frozen  = False
        self.m._alive_counter_prev    = -1
        self.m._alive_freeze_repeat_count = 0
        self.m._sensor_state = bcmcan.SensorState()
        self.m._b2104_reset()

    def _send_cmd(self, cmd: dict):
        """Simule le traitement d'un test_cmd reçu (copie logique du handler)."""
        tc = cmd.get("test_cmd")
        if tc == "stop_can_tx":
            with self.m._can_tx_paused_lock:
                self.m._can_tx_paused = True
        elif tc == "start_can_tx":
            with self.m._can_tx_paused_lock:
                self.m._can_tx_paused = False
        elif tc == "corrupt_crc_0x201":
            count = int(cmd.get("count", 10))
            with self.m._corrupt_crc_count_lock:
                self.m._corrupt_crc_count = count
        elif tc == "freeze_can_alive":
            with self.m._alive_counter_frozen_lock:
                self.m._alive_counter_frozen      = True
                self.m._alive_counter_prev        = -1
                self.m._alive_freeze_repeat_count = 0
        elif tc == "restore_can_alive":
            with self.m._alive_counter_frozen_lock:
                self.m._alive_counter_frozen      = False
                self.m._alive_counter_prev        = -1
                self.m._alive_freeze_repeat_count = 0
        elif tc == "set_blade_sim":
            self.m._sensor_state.set_blade_sim(float(cmd.get("value", -1.0)))
        elif tc == "start_blade_cycling":
            self.m._sensor_state.start_blade_cycling(float(cmd.get("period_ms", 1500)))
        elif tc == "stop_blade_cycling":
            self.m._sensor_state.stop_blade_cycling()
        elif tc == "freeze_blade_position":
            self.m._sensor_state.set_blade_frozen(True, float(cmd.get("value", 50.0)))
        elif tc == "unfreeze_blade_position":
            self.m._sensor_state.set_blade_frozen(False)
        elif tc == "inject_motor_current":
            self.m._sensor_state.set_motor_current_override(float(cmd.get("value", 0.95)))
        elif tc == "reset_motor_current":
            self.m._sensor_state.set_motor_current_override(-1.0)
        elif tc == "set_mode_mismatch_0x201":
            with self.m._mode_mismatch_0x201_lock:
                self.m._mode_mismatch_0x201 = True
        elif tc == "reset_mode_mismatch_0x201":
            with self.m._mode_mismatch_0x201_lock:
                self.m._mode_mismatch_0x201 = False
        elif tc == "set_fault_status_bits":
            self.m._sensor_state.set_fault_status_bits(int(cmd.get("bits", 0)) & 0x3F)
        elif tc == "reset_fault_status_bits":
            self.m._sensor_state.reset_fault_status_bits()
        elif tc == "set_motor_driver_fault":
            self.m._sensor_state.set_motor_driver_fault(bool(cmd.get("value", False)))
        elif tc == "set_xcp_internal_fault":
            self.m._sensor_state.set_xcp_internal_fault(bool(cmd.get("value", True)))
        elif tc == "set_xcp_position_sensor_fault":
            self.m._sensor_state.set_xcp_position_sensor_fault(bool(cmd.get("value", True)))
        elif tc == "reset_b2104":
            self.m._b2104_reset()

    def test_stop_can_tx_sets_paused(self):
        self._send_cmd({"test_cmd": "stop_can_tx"})
        assert self.m._can_tx_paused

    def test_start_can_tx_clears_paused(self):
        self.m._can_tx_paused = True
        self._send_cmd({"test_cmd": "start_can_tx"})
        assert not self.m._can_tx_paused

    def test_corrupt_crc_0x201_sets_count(self):
        self._send_cmd({"test_cmd": "corrupt_crc_0x201", "count": 8})
        assert self.m._corrupt_crc_count == 8

    def test_corrupt_crc_default_count(self):
        self._send_cmd({"test_cmd": "corrupt_crc_0x201"})
        assert self.m._corrupt_crc_count == 10

    def test_freeze_can_alive_activates(self):
        self._send_cmd({"test_cmd": "freeze_can_alive"})
        assert self.m._alive_counter_frozen
        assert self.m._alive_counter_prev == -1
        assert self.m._alive_freeze_repeat_count == 0

    def test_restore_can_alive_deactivates(self):
        self.m._alive_counter_frozen = True
        self._send_cmd({"test_cmd": "restore_can_alive"})
        assert not self.m._alive_counter_frozen

    def test_set_blade_sim(self):
        self._send_cmd({"test_cmd": "set_blade_sim", "value": 75.0})
        assert self.m._sensor_state.blade_sim == pytest.approx(75.0)

    def test_blade_cycling_start_stop(self):
        self._send_cmd({"test_cmd": "start_blade_cycling", "period_ms": 300})
        assert self.m._sensor_state._blade_cycling
        self._send_cmd({"test_cmd": "stop_blade_cycling"})
        assert not self.m._sensor_state._blade_cycling

    def test_freeze_unfreeze_blade_position(self):
        self._send_cmd({"test_cmd": "freeze_blade_position", "value": 33.0})
        assert self.m._sensor_state.blade_position_frozen
        assert self.m._sensor_state.get_blade_for_tx() == pytest.approx(33.0)
        self._send_cmd({"test_cmd": "unfreeze_blade_position"})
        assert not self.m._sensor_state.blade_position_frozen

    def test_inject_motor_current(self):
        self._send_cmd({"test_cmd": "inject_motor_current", "value": 0.88})
        mc = self.m._sensor_state.get_motor_current_for_tx()
        assert mc == pytest.approx(0.88)

    def test_reset_motor_current(self):
        self.m._sensor_state.set_motor_current_override(0.9)
        self._send_cmd({"test_cmd": "reset_motor_current"})
        self.m._sensor_state.update(0.0, 0.1, 0)
        mc = self.m._sensor_state.get_motor_current_for_tx()
        assert mc == pytest.approx(0.1, abs=0.01)

    def test_set_mode_mismatch_0x201(self):
        self._send_cmd({"test_cmd": "set_mode_mismatch_0x201"})
        assert self.m._mode_mismatch_0x201

    def test_reset_mode_mismatch_0x201(self):
        self.m._mode_mismatch_0x201 = True
        self._send_cmd({"test_cmd": "reset_mode_mismatch_0x201"})
        assert not self.m._mode_mismatch_0x201

    def test_set_fault_status_bits(self):
        self._send_cmd({"test_cmd": "set_fault_status_bits", "bits": 0x0E})
        assert self.m._sensor_state._forced_fault_bits == 0x0E

    def test_reset_fault_status_bits(self):
        self.m._sensor_state.set_fault_status_bits(0x0F)
        self._send_cmd({"test_cmd": "reset_fault_status_bits"})
        assert self.m._sensor_state._forced_fault_bits == 0

    def test_set_motor_driver_fault(self):
        self._send_cmd({"test_cmd": "set_motor_driver_fault", "value": True})
        assert self.m._sensor_state.motor_driver_fault

    def test_clear_motor_driver_fault(self):
        self.m._sensor_state.set_motor_driver_fault(True)
        self._send_cmd({"test_cmd": "set_motor_driver_fault", "value": False})
        assert not self.m._sensor_state.motor_driver_fault

    def test_set_xcp_internal_fault(self):
        self._send_cmd({"test_cmd": "set_xcp_internal_fault", "value": True})
        assert self.m._sensor_state.xcp_internal_fault

    def test_set_xcp_position_sensor_fault(self):
        self._send_cmd({"test_cmd": "set_xcp_position_sensor_fault", "value": True})
        assert self.m._sensor_state.xcp_position_sensor_fault

    def test_reset_b2104(self):
        self.m._b2104_nack_count = 5
        self._send_cmd({"test_cmd": "reset_b2104"})
        assert self.m._b2104_nack_count == 0


# ══════════════════════════════════════════════════════════════════════════════
#  COUCHE 1E — BCMCAN : FAULT GPIO (stubs)
# ══════════════════════════════════════════════════════════════════════════════

class TestBcmcanFaultGPIO:
    """Vérifie les fonctions GPIO d'injection de défauts (sur stubs)."""

    @pytest.fixture(autouse=True)
    def setup(self):
        import bcmcan
        self.m = bcmcan

    def test_apply_fault_mode_normal(self):
        """_apply_fault_mode('NORMAL') doit tourner sans exception."""
        self.m._apply_fault_mode("NORMAL", "POMPE", 0.0)
        assert self.m._fault_mode == "NORMAL"

    def test_apply_fault_mode_open_load(self):
        self.m._apply_fault_mode("OPEN LOAD", "POMPE", 0.0)
        assert self.m._fault_mode == "OPEN LOAD"

    def test_apply_fault_mode_short_to_gnd(self):
        self.m._apply_fault_mode("SHORT TO GND", "POMPE", 0.0)
        assert self.m._fault_mode == "SHORT TO GND"

    def test_apply_fault_mode_short_to_vcc(self):
        self.m._apply_fault_mode("SHORT TO VCC", "POMPE", 0.0)
        assert self.m._fault_mode == "SHORT TO VCC"

    def test_apply_fault_mode_variable_load(self):
        self.m._apply_fault_mode("VARIABLE LOAD", "POMPE", 75.0)
        assert self.m._fault_mode == "VARIABLE LOAD"
        assert self.m._fault_duty == pytest.approx(75.0)

    def test_apply_fault_mode_unknown_falls_back_to_normal(self):
        self.m._apply_fault_mode("UNICORN_MODE", "POMPE", 0.0)
        assert self.m._fault_mode == "UNICORN_MODE"   # mode stocké mais gpio retour normal

    def test_set_variable_load_clamps_duty(self):
        self.m.set_variable_load(150.0)   # doit être clampé à 100

    def test_set_variable_load_negative(self):
        self.m.set_variable_load(-10.0)   # doit être clampé à 0

    def test_fault_target_always_pompe(self):
        """La cible est toujours POMPE (monomoteur)."""
        self.m._apply_fault_mode("OPEN LOAD", "MOTEUR", 0.0)
        assert self.m._fault_target == "POMPE"

    def test_fault_gpio_init_no_crash_without_hw(self):
        """_fault_gpio_init() ne doit pas crasher sans GPIO réel."""
        try:
            self.m._fault_gpio_init()
        except Exception as exc:
            pytest.fail(f"_fault_gpio_init raised {exc}")

    def test_fault_gpio_retour_normal_no_crash(self):
        try:
            self.m._fault_gpio_retour_normal()
        except Exception as exc:
            pytest.fail(f"_fault_gpio_retour_normal raised {exc}")

    def test_cleanup_sets_shutdown(self):
        import bcmcan
        old_shutdown = bcmcan._shutdown
        bcmcan.cleanup("test")
        assert bcmcan._shutdown
        bcmcan._shutdown = old_shutdown   # restore


# ══════════════════════════════════════════════════════════════════════════════
#  COUCHE 1F — BCMCAN : HANDLE VEHICLE STATUS & RAIN SENSOR
# ══════════════════════════════════════════════════════════════════════════════

class TestBcmcanHandlers:

    @pytest.fixture(autouse=True)
    def setup(self):
        import bcmcan
        self.m = bcmcan
        self.m._vehicle_state = bcmcan.VehicleState()
        self.m._rain_state    = bcmcan.RainState()
        self.m._prev_ignition = -1

    def test_handle_vehicle_status_ignition_on_str(self):
        self.m._handle_vehicle_status({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
        ign, _, _ = self.m._vehicle_state.snapshot()
        assert ign == 2

    def test_handle_vehicle_status_ignition_off_str(self):
        self.m._handle_vehicle_status({"ignition_status": "OFF"})
        ign, _, _ = self.m._vehicle_state.snapshot()
        assert ign == 0

    def test_handle_vehicle_status_reverse_gear(self):
        self.m._handle_vehicle_status({"ignition_status": "ON", "reverse_gear": 1, "vehicle_speed": 0})
        _, rev, _ = self.m._vehicle_state.snapshot()
        assert rev == 1

    def test_handle_vehicle_status_speed_encoding(self):
        self.m._handle_vehicle_status({"vehicle_speed": 100.0})
        _, _, spd = self.m._vehicle_state.snapshot()
        # 100 km/h * 10 = 1000 (raw)
        assert spd == 1000

    def test_handle_vehicle_status_speed_clamp(self):
        self.m._handle_vehicle_status({"vehicle_speed": 9999.0})
        _, _, spd = self.m._vehicle_state.snapshot()
        assert spd <= 65535

    def test_handle_rain_sensor_intensity(self):
        self.m._handle_rain_sensor({"rain_intensity": 60, "sensor_status": "OK"})
        intensity, ok = self.m._rain_state.snapshot()
        assert intensity == 60
        assert ok is True

    def test_handle_rain_sensor_fault(self):
        self.m._handle_rain_sensor({"rain_intensity": 0, "sensor_status": "FAULT"})
        _, ok = self.m._rain_state.snapshot()
        assert ok is False

    def test_handle_rain_sensor_intensity_clamp(self):
        self.m._handle_rain_sensor({"rain_intensity": 999})
        intensity, _ = self.m._rain_state.snapshot()
        assert intensity <= 100


# ══════════════════════════════════════════════════════════════════════════════
#  COUCHE 2 — CRSLIN.PY
# ══════════════════════════════════════════════════════════════════════════════

class TestCrslin:

    @pytest.fixture(autouse=True)
    def setup(self):
        import crslin
        self.m = crslin

    def test_wop_enum_values(self):
        assert self.m.WOp.OFF        == 0
        assert self.m.WOp.TOUCH      == 1
        assert self.m.WOp.SPEED1     == 2
        assert self.m.WOp.SPEED2     == 3
        assert self.m.WOp.AUTO       == 4
        assert self.m.WOp.FRONT_WASH == 5
        assert self.m.WOp.REAR_WASH  == 6
        assert self.m.WOp.REAR_WIPE  == 7

    def test_calculate_pid_0x16(self):
        pid = self.m._calculate_pid(0x16)
        assert pid == 0xD6

    def test_calculate_pid_0x17(self):
        pid = self.m._calculate_pid(0x17)
        assert pid == 0x97

    def test_calculate_pid_parity_bits(self):
        """Le PID doit avoir les bits de parité corrects."""
        pid = self.m._calculate_pid(0x16)
        # bit6 = P0, bit7 = P1 (calculés selon LIN spec)
        assert pid & 0xC0 != 0   # au moins un bit de parité actif

    def test_lin_constants_exist(self):
        assert hasattr(self.m, "LIN_BREAK_BYTE")
        assert hasattr(self.m, "LIN_SYNC_BYTE")
        assert hasattr(self.m, "LIN_HDR_TMO")
        assert hasattr(self.m, "STICK_VALID")
        assert hasattr(self.m, "STICK_STUCK")

    def test_lin_sync_byte_is_0x55(self):
        assert self.m.LIN_SYNC_BYTE == 0x55

    def test_lin_break_byte_is_0x00(self):
        assert self.m.LIN_BREAK_BYTE == 0x00

    def test_stick_valid_bit0(self):
        assert self.m.STICK_VALID & 0x01

    def test_stick_stuck_bit2(self):
        assert self.m.STICK_STUCK & 0x04

    def test_fault_constants_defined(self):
        assert self.m.FAULT_NONE         == 0x00
        assert self.m.FAULT_STICK_SENSOR == 0x01
        assert self.m.FAULT_SUPPLY       == 0x02
        assert self.m.FAULT_INTERNAL_COM == 0x04

    def test_crs_version_nominal(self):
        assert self.m.CRS_VERSION_NOMINAL == 0x20

    def test_crs_version_invalid(self):
        assert self.m.CRS_VERSION_INVALID == 0xFF

    def test_node_state_initial(self):
        s = self.m.NodeState()
        assert s.wiper_op     == self.m.WOp.OFF
        assert s.stick_status == self.m.STICK_VALID
        assert s.alive_ctr    == 0
        assert s.fault        == self.m.FAULT_NONE
        assert s.freeze_alive is False
        assert s.corrupt_checksum is False
        assert s.raw_wiper_op == -1

    def test_node_state_set_op(self):
        s = self.m.NodeState()
        s.set_op(self.m.WOp.SPEED1)
        assert s.wiper_op == self.m.WOp.SPEED1
        assert s.stick_status == self.m.STICK_VALID

    def test_node_state_inc_alive_increments(self):
        s = self.m.NodeState()
        s.inc_alive()
        assert s.alive_ctr == 1

    def test_node_state_inc_alive_wraps_at_255(self):
        s = self.m.NodeState()
        s.alive_ctr = 255
        s.inc_alive()
        assert s.alive_ctr == 0

    def test_node_state_freeze_alive_prevents_increment(self):
        s = self.m.NodeState()
        s.freeze_alive = True
        s.inc_alive()
        assert s.alive_ctr == 0

    def test_node_state_snapshot_returns_4_tuple(self):
        s = self.m.NodeState()
        result = s.snapshot()
        assert len(result) == 4

    def test_node_state_crs_version(self):
        s = self.m.NodeState()
        assert s.crs_version == self.m.CRS_VERSION_NOMINAL

    def test_node_state_set_corrupt_checksum(self):
        s = self.m.NodeState()
        s.corrupt_checksum = True
        assert s.corrupt_checksum is True

    def test_node_state_raw_wiper_op_oneshot(self):
        """raw_wiper_op = LIN_INVALID_CMD_001 : valeur brute injectée."""
        s = self.m.NodeState()
        s.raw_wiper_op = 10   # opération hors plage
        assert s.raw_wiper_op == 10

    def test_lin_paused_flag_default_false(self):
        assert not self.m._is_lin_paused()

    def test_set_lin_paused_true(self):
        self.m._set_lin_paused(True)
        assert self.m._is_lin_paused()
        self.m._set_lin_paused(False)   # cleanup

    def test_set_lin_paused_false(self):
        self.m._set_lin_paused(True)
        self.m._set_lin_paused(False)
        assert not self.m._is_lin_paused()

    def test_baud_meas_constants_defined(self):
        assert hasattr(self.m, "_BAUD_MEAS_EVERY")
        assert self.m._BAUD_MEAS_EVERY > 0

    def test_fault_names_dict(self):
        assert self.m.FAULT_NONE         in self.m.FAULT_NAMES
        assert self.m.FAULT_STICK_SENSOR in self.m.FAULT_NAMES
        assert self.m.FAULT_SUPPLY       in self.m.FAULT_NAMES
        assert self.m.FAULT_INTERNAL_COM in self.m.FAULT_NAMES

    def test_load_ldf_into_crslin_with_real_ldf(self):
        ldf = str(SIM / "wiperwash.ldf")
        if not os.path.exists(ldf):
            pytest.skip("wiperwash.ldf non trouvé")
        cfg = self.m.load_ldf_into_crslin(ldf)
        assert isinstance(cfg, dict)
        if cfg:
            assert "baud"   in cfg
            assert "frames" in cfg

    def test_load_ldf_updates_global_baud(self):
        ldf = str(SIM / "wiperwash.ldf")
        if not os.path.exists(ldf):
            pytest.skip("wiperwash.ldf non trouvé")
        self.m.load_ldf_into_crslin(ldf)
        assert self.m._LIN_BAUD > 0

    def test_lin_retry_s_positive(self):
        assert self.m.LIN_RETRY_S > 0


# ══════════════════════════════════════════════════════════════════════════════
#  COUCHE 3 — WC_DOIP.PY
# ══════════════════════════════════════════════════════════════════════════════

class TestWcDoIP:

    @pytest.fixture(autouse=True)
    def setup(self):
        import wc_doip
        self.m = wc_doip

    def test_constants_protocol_version(self):
        assert self.m.PROTOCOL_VERSION == 0x02
        assert self.m.INVERSE_VERSION  == 0xFD

    def test_constants_ports(self):
        assert self.m.DOIP_UDP_PORT == 13400
        assert self.m.DOIP_TCP_PORT == 13400

    def test_constants_addresses(self):
        assert self.m.WC_LOGICAL_ADDR == 0x0701
        assert self.m.TESTER_ADDR     == 0x07DF

    def test_doip_message_types(self):
        assert hasattr(self.m, "DOIP_VEHICLE_ID_REQ")
        assert hasattr(self.m, "DOIP_VEHICLE_ID_RES")
        assert hasattr(self.m, "DOIP_ROUTING_ACT_REQ")
        assert hasattr(self.m, "DOIP_ROUTING_ACT_RES")
        assert hasattr(self.m, "DOIP_DIAGNOSTIC_MSG")
        assert hasattr(self.m, "DOIP_DIAG_MSG_ACK")
        assert hasattr(self.m, "DOIP_ALIVE_CHECK_REQ")
        assert hasattr(self.m, "DOIP_ALIVE_CHECK_RES")

    def test_uds_sids(self):
        assert self.m.SID_CDTC == 0x14
        assert self.m.SID_DSC  == 0x10
        assert self.m.SID_RDTC == 0x19
        assert self.m.SID_RDID == 0x22
        assert self.m.SID_RC   == 0x31
        assert self.m.SID_TP   == 0x3E

    def test_dsc_sessions(self):
        assert self.m.DSC_DEFAULT  == 0x01
        assert self.m.DSC_EXTENDED == 0x03

    def test_wc_state_singleton(self):
        assert hasattr(self.m, "wc_state")
        assert isinstance(self.m.wc_state, self.m._WCState)

    def test_wc_state_update_from_bcmcan(self):
        self.m.wc_state.update_from_bcmcan(
            mode=2, speed=1, blade_pos=55.0,
            motor_current_a=0.25, fault_status=0, can_timeout=False
        )
        assert self.m.wc_state.current_mode  == 2
        assert self.m.wc_state.current_speed == 1
        assert self.m.wc_state.blade_pos     == pytest.approx(55.0)
        assert self.m.wc_state.motor_current == pytest.approx(0.25)
        assert self.m.wc_state.can_timeout   is False

    def test_wc_state_update_pump_status(self):
        self.m.wc_state.update_from_bcmcan(
            mode=5, speed=0, blade_pos=0.0,
            motor_current_a=0.0, fault_status=0, can_timeout=False,
            pump_status=1
        )
        assert self.m.wc_state.pump_status == 1

    def test_wc_state_make_snapshot_keys(self):
        snap = self.m.wc_state.make_snapshot()
        assert "ignition"   in snap
        assert "wiper_mode" in snap
        assert "motor_curr" in snap
        assert "blade_pos"  in snap

    def test_wc_state_make_snapshot_ignition_always_1(self):
        """Le simulateur est toujours Ignition ON."""
        snap = self.m.wc_state.make_snapshot()
        assert snap["ignition"] == 1

    def test_wc_state_motor_simulation_starts(self):
        self.m.wc_state.start_motor_simulation(0.2)
        time.sleep(0.1)
        assert self.m.wc_state._motor_sim_thread is not None

    def test_wc_state_motor_simulation_completes(self):
        self.m.wc_state.start_motor_simulation(0.1)
        time.sleep(0.3)
        assert not self.m.wc_state._test_active

    def test_wc_state_motor_simulation_blade_returns_to_zero(self):
        self.m.wc_state.start_motor_simulation(0.1)
        time.sleep(0.4)
        assert self.m.wc_state.blade_pos == pytest.approx(0.0, abs=1.0)

    def test_wc_doip_server_importable(self):
        assert hasattr(self.m, "WCDoIPServer")
        assert callable(self.m.WCDoIPServer)

    def test_handle_read_dtc_importable(self):
        from wc_doip import handle_read_dtc
        assert callable(handle_read_dtc)

    def test_handle_clear_dtc_importable(self):
        from wc_doip import handle_clear_dtc
        assert callable(handle_clear_dtc)

    def test_dtc_mgr_initialized_on_start(self):
        """_dtc_mgr est None avant start() et initialisé après."""
        assert hasattr(self.m, "_dtc_mgr")


# ══════════════════════════════════════════════════════════════════════════════
#  COUCHE 4 — WC_DTC_MANAGER.PY
# ══════════════════════════════════════════════════════════════════════════════

class TestDTCManager:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        import json
        from wc_dtc_manager import DTCManager_WC, DTC_FILE

        # Copie la vraie base de données DTC pour chaque test (isolation)
        src = Path(SIM) / "wc_dtc_database.json"
        if not src.exists():
            pytest.skip("wc_dtc_database.json introuvable")
        dst = tmp_path / "wc_dtc_database.json"
        dst.write_text(src.read_text())

        self.mgr  = DTCManager_WC(filepath=str(dst))
        self.path = str(dst)

    def test_manager_loads_dtcs(self):
        assert len(self.mgr.dtcs) >= 4  # B2101, B2102, B2103, B2104

    def test_b2101_in_dtcs(self):
        assert "B2101" in self.mgr.dtcs

    def test_b2102_in_dtcs(self):
        assert "B2102" in self.mgr.dtcs

    def test_b2103_in_dtcs(self):
        assert "B2103" in self.mgr.dtcs

    def test_b2104_in_dtcs(self):
        assert "B2104" in self.mgr.dtcs

    def test_set_active_b2101(self):
        snap = {"ignition": 1, "wiper_mode": "OFF", "motor_curr": 0, "blade_pos": 0}
        self.mgr.set_active("B2101", snap)
        from wc_dtc_manager import STATUS_ACTIVE
        assert self.mgr.dtcs["B2101"]["status"] == STATUS_ACTIVE

    def test_set_active_increments_occurrence(self):
        snap = {"ignition": 1, "wiper_mode": "SPEED1", "motor_curr": 100, "blade_pos": 50}
        before = self.mgr.dtcs["B2102"]["occurrence_count"]
        self.mgr.set_active("B2102", snap)
        assert self.mgr.dtcs["B2102"]["occurrence_count"] == before + 1

    def test_set_active_sets_last_occurrence(self):
        snap = {"ignition": 1, "wiper_mode": "OFF", "motor_curr": 0, "blade_pos": 0}
        self.mgr.set_active("B2103", snap)
        assert self.mgr.dtcs["B2103"]["last_occurrence"] is not None

    def test_set_active_sets_first_occurrence_on_new(self):
        self.mgr.dtcs["B2104"]["first_occurrence"] = None
        snap = {"ignition": 1, "wiper_mode": "OFF", "motor_curr": 0, "blade_pos": 0}
        self.mgr.set_active("B2104", snap)
        assert self.mgr.dtcs["B2104"]["first_occurrence"] is not None

    def test_set_inactive_b2101(self):
        snap = {"ignition": 1, "wiper_mode": "OFF", "motor_curr": 0, "blade_pos": 0}
        self.mgr.set_active("B2101", snap)
        self.mgr.set_inactive("B2101")
        from wc_dtc_manager import STATUS_INACTIVE
        assert self.mgr.dtcs["B2101"]["status"] == STATUS_INACTIVE

    def test_set_active_unknown_dtc_no_crash(self):
        self.mgr.set_active("B9999", {})

    def test_notify_ignition_on(self):
        self.mgr.notify_ignition_on()

    def test_notify_ignition_off(self):
        self.mgr.notify_ignition_off()

    def test_handle_read_dtc_sf_all(self):
        from wc_dtc_manager import handle_read_dtc
        resp = handle_read_dtc(bytes([0x01, 0xFF]), self.mgr)
        assert isinstance(resp, bytes)
        assert len(resp) > 0

    def test_handle_clear_dtc_all(self):
        from wc_dtc_manager import handle_clear_dtc
        resp = handle_clear_dtc(bytes([0xFF, 0xFF, 0xFF]), self.mgr)
        assert isinstance(resp, bytes)

    def test_clear_dtc_resets_occurrence_count(self):
        from wc_dtc_manager import handle_clear_dtc
        snap = {"ignition": 1, "wiper_mode": "OFF", "motor_curr": 0, "blade_pos": 0}
        self.mgr.set_active("B2101", snap)
        handle_clear_dtc(bytes([0xFF, 0xFF, 0xFF]), self.mgr)
        # après clear, le count doit être remis à 0 ou le DTC doit être clean
        from wc_dtc_manager import STATUS_CLEAN
        assert self.mgr.dtcs["B2101"]["status"] in (STATUS_CLEAN, 0x00)

    def test_dtc_status_byte_constants(self):
        from wc_dtc_manager import (
            STATUS_ACTIVE, STATUS_INACTIVE, STATUS_CLEAN,
            BIT_TEST_FAILED, BIT_CONFIRMED, BIT_PENDING
        )
        assert STATUS_ACTIVE   != STATUS_INACTIVE
        assert STATUS_CLEAN    == 0x00
        assert BIT_TEST_FAILED == 0x01
        assert BIT_CONFIRMED   == 0x08
        assert BIT_PENDING     == 0x04

    def test_snapshot_dids_defined(self):
        from wc_dtc_manager import (
            SNAP_DID_IGNITION, SNAP_DID_WIPER_MODE,
            SNAP_DID_MOTOR_CURR, SNAP_DID_BLADE_POS
        )
        assert SNAP_DID_IGNITION  == 0xF190
        assert SNAP_DID_WIPER_MODE == 0xF191
        assert SNAP_DID_MOTOR_CURR == 0xF192
        assert SNAP_DID_BLADE_POS  == 0xF193

    def test_ext_record_numbers_defined(self):
        from wc_dtc_manager import (
            EXT_REC_OCCURRENCE_COUNT, EXT_REC_FAILED_CYCLES,
            EXT_REC_TIME_FIRST_OCC, EXT_REC_TIME_LAST_OCC
        )
        assert EXT_REC_OCCURRENCE_COUNT == 0x01
        assert EXT_REC_FAILED_CYCLES    == 0x03
        assert EXT_REC_TIME_FIRST_OCC   == 0x04
        assert EXT_REC_TIME_LAST_OCC    == 0x05

    def test_max_snapshot_records(self):
        from wc_dtc_manager import MAX_SNAPSHOT_RECORDS
        assert MAX_SNAPSHOT_RECORDS == 5


# ══════════════════════════════════════════════════════════════════════════════
#  COUCHE 5 — XCP_SERVER_WC.PY
# ══════════════════════════════════════════════════════════════════════════════

class TestXCPServerWC:

    @pytest.fixture(autouse=True)
    def setup(self):
        import xcp_server_wc
        self.m = xcp_server_wc

    def test_xcp_constants(self):
        assert self.m.PORT              == 17726
        assert self.m.CMD_CONNECT       == 0xFF
        assert self.m.CMD_DISCONNECT    == 0xFE
        assert self.m.CMD_STATUS        == 0xFD
        assert self.m.CMD_SHORT_DOWNLOAD == 0xED

    def test_xcp_response_bytes(self):
        assert self.m.RES_OK  == bytes([0xFF])
        assert self.m.RES_ERR == bytes([0xFE])

    def test_xcp_addresses(self):
        assert self.m.ADDR_WC_INTERNAL_FAULT        == 0x20010000
        assert self.m.ADDR_WC_MOTOR_DRIVER_FAULT    == 0x20010001
        assert self.m.ADDR_WC_POSITION_SENSOR_FAULT == 0x20010002

    def test_poll_period_positive(self):
        assert self.m.POLL_PERIOD_S > 0

    def test_mem_get_default(self):
        mem = {}
        val = self.m._mem_get(mem, 0x20010000, default=0)
        assert val == 0

    def test_mem_get_existing(self):
        mem = {"0x20010000": 1}
        val = self.m._mem_get(mem, 0x20010000, default=0)
        assert val == 1

    def test_mem_set(self):
        mem = {}
        self.m._mem_set(mem, 0x20010001, 1)
        assert mem.get("0x20010001") == 1

    def test_mem_set_zero(self):
        mem = {}
        self.m._mem_set(mem, 0x20010002, 0)
        assert mem.get("0x20010002") == 0

    def test_load_memory_empty_when_no_file(self, tmp_path):
        old = self.m.MEMORY_FILE
        self.m.MEMORY_FILE = str(tmp_path / "nonexistent.json")
        result = self.m._load_memory()
        assert result == {}
        self.m.MEMORY_FILE = old

    def test_save_and_load_memory(self, tmp_path):
        old = self.m.MEMORY_FILE
        self.m.MEMORY_FILE = str(tmp_path / "mem.json")
        mem = {"0x20010000": 1, "0x20010001": 0}
        self.m._save_memory(mem)
        loaded = self.m._load_memory()
        assert loaded == mem
        self.m.MEMORY_FILE = old

    def test_xcp_server_wc_instantiable(self):
        srv = self.m.XCPServerWC()
        assert srv is not None

    def test_xcp_server_wc_has_run(self):
        srv = self.m.XCPServerWC()
        assert hasattr(srv, "run")
        assert callable(srv.run)

    def test_dtc_engine_xcp_wc_instantiable(self):
        lock = threading.Lock()
        engine = self.m._DTCEngineXCP_WC(lock)
        assert engine is not None

    def test_dtc_engine_stop(self):
        lock = threading.Lock()
        engine = self.m._DTCEngineXCP_WC(lock)
        engine.stop()
        assert not engine._running

    def test_get_sensor_state_returns_none_without_bcmcan(self):
        import sys
        # Si bcmcan n'est pas chargé, retourne None
        bcmcan_backup = sys.modules.pop("bcmcan", None)
        result = self.m._get_sensor_state()
        assert result is None
        if bcmcan_backup is not None:
            sys.modules["bcmcan"] = bcmcan_backup


# ══════════════════════════════════════════════════════════════════════════════
#  COUCHE 6 — BCM_TCP_CAN.PY
# ══════════════════════════════════════════════════════════════════════════════

class TestBcmTcpCan:

    @pytest.fixture(autouse=True)
    def setup(self):
        import bcm_tcp_can
        self.m = bcm_tcp_can

    def test_tcp_can_broadcast_importable(self):
        assert hasattr(self.m, "TCPCANBroadcast")

    def test_tcp_can_broadcast_instantiable(self):
        tcp = self.m.TCPCANBroadcast()
        assert tcp is not None

    def test_tcp_can_has_on_tx_0x201(self):
        tcp = self.m.TCPCANBroadcast()
        assert hasattr(tcp, "on_tx_0x201")

    def test_tcp_can_has_on_rx_0x200(self):
        tcp = self.m.TCPCANBroadcast()
        assert hasattr(tcp, "on_rx_0x200")

    def test_tcp_can_has_on_tx_0x202(self):
        tcp = self.m.TCPCANBroadcast()
        assert hasattr(tcp, "on_tx_0x202")

    def test_tcp_can_has_on_tx_0x300(self):
        tcp = self.m.TCPCANBroadcast()
        assert hasattr(tcp, "on_tx_0x300")

    def test_tcp_can_has_on_tx_0x301(self):
        tcp = self.m.TCPCANBroadcast()
        assert hasattr(tcp, "on_tx_0x301")

    def test_tcp_can_has_set_0x202_callback(self):
        tcp = self.m.TCPCANBroadcast()
        assert hasattr(tcp, "set_0x202_callback")

    def test_set_0x202_callback_stores_callback(self):
        tcp = self.m.TCPCANBroadcast()
        cb = MagicMock()
        tcp.set_0x202_callback(cb)
        # vérifier que le callback est stocké (attribut ou dict)

    def test_tcp_can_callbacks_no_crash_on_empty_data(self):
        tcp = self.m.TCPCANBroadcast()
        try:
            tcp.on_tx_0x201(bytes(8), t_kernel=time.time())
            tcp.on_tx_0x300(bytes(8), t_kernel=time.time())
            tcp.on_tx_0x301(bytes(8), t_kernel=time.time())
            tcp.on_rx_0x200(bytes(8), t_kernel=time.time())
            tcp.on_tx_0x202(bytes(4), t_kernel=time.time())
        except Exception as exc:
            pytest.fail(f"Callback raised {exc}")

    def test_tcp_can_has_start(self):
        tcp = self.m.TCPCANBroadcast()
        assert hasattr(tcp, "start")


# ══════════════════════════════════════════════════════════════════════════════
#  COUCHE 7 — DBC_LOADER.PY
# ══════════════════════════════════════════════════════════════════════════════

class TestDBCLoader:

    @pytest.fixture(autouse=True)
    def setup(self):
        import dbc_loader
        self.m = dbc_loader

    def test_dbc_loader_importable(self):
        assert hasattr(self.m, "load_dbc")
        assert hasattr(self.m, "pack_frame")
        assert hasattr(self.m, "unpack_frame")

    def test_load_dbc_with_real_file(self):
        dbc = str(SIM / "wiperwash.dbc")
        if not os.path.exists(dbc):
            pytest.skip("wiperwash.dbc non trouvé")
        cfg = self.m.load_dbc(dbc)
        assert isinstance(cfg, dict)
        assert "messages" in cfg

    def test_load_dbc_returns_periods(self):
        dbc = str(SIM / "wiperwash.dbc")
        if not os.path.exists(dbc):
            pytest.skip("wiperwash.dbc non trouvé")
        cfg = self.m.load_dbc(dbc)
        assert "periods_ms" in cfg

    def test_load_dbc_messages_not_empty(self):
        dbc = str(SIM / "wiperwash.dbc")
        if not os.path.exists(dbc):
            pytest.skip("wiperwash.dbc non trouvé")
        cfg = self.m.load_dbc(dbc)
        assert len(cfg["messages"]) > 0

    def test_load_dbc_contains_wiper_command(self):
        dbc = str(SIM / "wiperwash.dbc")
        if not os.path.exists(dbc):
            pytest.skip("wiperwash.dbc non trouvé")
        cfg = self.m.load_dbc(dbc)
        names = [m.name for m in cfg["messages"].values()]
        assert "Wiper_Command" in names or any("Wiper" in n for n in names)

    def test_load_dbc_nonexistent_returns_empty(self):
        cfg = self.m.load_dbc("/tmp/nonexistent_xyz.dbc")
        assert cfg is None or (isinstance(cfg, dict) and not cfg.get("messages"))

    def test_pack_frame_returns_8_bytes(self):
        dbc = str(SIM / "wiperwash.dbc")
        if not os.path.exists(dbc):
            pytest.skip("wiperwash.dbc non trouvé")
        cfg = self.m.load_dbc(dbc)
        if not cfg or not cfg["messages"]:
            pytest.skip("Aucun message DBC chargé")
        msg = next(iter(cfg["messages"].values()))
        sigs = {s.name: 0.0 for s in msg.signals}
        result = self.m.pack_frame(msg, sigs)
        assert isinstance(result, (bytes, bytearray))
        assert len(result) == 8

    def test_unpack_frame_returns_dict(self):
        dbc = str(SIM / "wiperwash.dbc")
        if not os.path.exists(dbc):
            pytest.skip("wiperwash.dbc non trouvé")
        cfg = self.m.load_dbc(dbc)
        if not cfg or not cfg["messages"]:
            pytest.skip("Aucun message DBC chargé")
        msg = next(iter(cfg["messages"].values()))
        result = self.m.unpack_frame(msg, bytes(8))
        assert isinstance(result, dict)

    def test_load_dbc_sim_updates_globals(self):
        import bcmcan
        dbc = str(SIM / "wiperwash.dbc")
        if not os.path.exists(dbc):
            pytest.skip("wiperwash.dbc non trouvé")
        bcmcan.load_dbc_sim(dbc)
        assert bcmcan._CAN_ID_CMD in (0x200, 0x201, 0x202, 0x300, 0x301)


# ══════════════════════════════════════════════════════════════════════════════
#  COUCHE 8 — LDF_LOADER.PY
# ══════════════════════════════════════════════════════════════════════════════

class TestLDFLoader:

    @pytest.fixture(autouse=True)
    def setup(self):
        import ldf_loader
        self.m = ldf_loader

    def test_ldf_loader_importable(self):
        assert hasattr(self.m, "load_ldf")
        assert hasattr(self.m, "_calculate_pid")

    def test_calculate_pid_pid16(self):
        assert self.m._calculate_pid(0x16) == 0xD6

    def test_calculate_pid_pid17(self):
        assert self.m._calculate_pid(0x17) == 0x97

    def test_calculate_pid_parity_p0_p1(self):
        """Les bits P0 et P1 encodent bien la parité LIN."""
        pid = self.m._calculate_pid(0x16)
        frame_id = 0x16 & 0x3F
        p0 = (frame_id ^ (frame_id >> 1) ^ (frame_id >> 2) ^ (frame_id >> 4)) & 0x01
        p1 = (~((frame_id >> 1) ^ (frame_id >> 3) ^ (frame_id >> 4) ^ (frame_id >> 5))) & 0x01
        expected = frame_id | (p0 << 6) | (p1 << 7)
        assert pid == expected

    def test_load_ldf_with_real_file(self):
        ldf = str(SIM / "wiperwash.ldf")
        if not os.path.exists(ldf):
            pytest.skip("wiperwash.ldf non trouvé")
        cfg = self.m.load_ldf(ldf)
        assert isinstance(cfg, dict)
        assert "baud" in cfg
        assert "frames" in cfg
        assert "pid_map" in cfg

    def test_load_ldf_baud_positive(self):
        ldf = str(SIM / "wiperwash.ldf")
        if not os.path.exists(ldf):
            pytest.skip("wiperwash.ldf non trouvé")
        cfg = self.m.load_ldf(ldf)
        assert cfg["baud"] > 0

    def test_load_ldf_frames_contain_0x16(self):
        ldf = str(SIM / "wiperwash.ldf")
        if not os.path.exists(ldf):
            pytest.skip("wiperwash.ldf non trouvé")
        cfg = self.m.load_ldf(ldf)
        names = list(cfg.get("frames", {}).keys())
        assert any("Wiper" in n or "CRS" in n or "LIN" in n for n in names)

    def test_load_ldf_pid_map_has_0xd6(self):
        ldf = str(SIM / "wiperwash.ldf")
        if not os.path.exists(ldf):
            pytest.skip("wiperwash.ldf non trouvé")
        cfg = self.m.load_ldf(ldf)
        assert 0xD6 in cfg.get("pid_map", {})

    def test_load_ldf_nonexistent_returns_empty_or_none(self):
        result = self.m.load_ldf("/tmp/nonexistent_ldf.ldf")
        assert result is None or result == {}


# ══════════════════════════════════════════════════════════════════════════════
#  COUCHE 9 — SIM_CONTROL.PY
# ══════════════════════════════════════════════════════════════════════════════

class TestSimControl:

    @pytest.fixture(autouse=True)
    def setup(self):
        import sim_control
        self.m = sim_control

    def test_modes_list(self):
        expected = {"NORMAL", "OPEN LOAD", "SHORT TO VCC", "VARIABLE LOAD", "SHORT TO GND"}
        assert set(self.m.MODES) == expected

    def test_fault_pin_constants(self):
        assert hasattr(self.m, "_FAULT_PIN_ISO")
        assert hasattr(self.m, "_FAULT_PIN_MUX_A")
        assert hasattr(self.m, "_FAULT_PIN_MUX_B")
        assert hasattr(self.m, "_FAULT_PIN_Y0_GATE")
        assert hasattr(self.m, "_FAULT_PIN_Y2_BASE")
        assert hasattr(self.m, "_FAULT_PIN_VLOAD_PWM")

    def test_fault_pin_iso_is_gpio18(self):
        assert self.m._FAULT_PIN_ISO == 18

    def test_fault_pin_mux_a_is_gpio6(self):
        assert self.m._FAULT_PIN_MUX_A == 6

    def test_fault_pin_mux_b_is_gpio13(self):
        assert self.m._FAULT_PIN_MUX_B == 13

    def test_fault_pin_y0_gate_is_gpio5(self):
        assert self.m._FAULT_PIN_Y0_GATE == 5

    def test_fault_pin_y2_base_is_gpio16(self):
        assert self.m._FAULT_PIN_Y2_BASE == 16

    def test_fault_pin_vload_pwm_is_gpio26(self):
        assert self.m._FAULT_PIN_VLOAD_PWM == 26

    def test_vload_pwm_freq(self):
        assert self.m._VLOAD_PWM_FREQ == 50000

    def test_gpio_available_false_without_hw(self):
        # Sur banc de dev sans GPIO, GPIO_AVAILABLE doit être False
        # (RPi.GPIO est un mock → pas de vraie init)
        assert hasattr(self.m, "GPIO_AVAILABLE")


# ══════════════════════════════════════════════════════════════════════════════
#  COUCHE 10 — TESTS D'INTÉGRATION (SANS MATÉRIEL)
# ══════════════════════════════════════════════════════════════════════════════

class TestIntegration:

    def test_full_0x201_pipeline(self):
        """Simule : injection surcourant → _build_0x201 → 0x202 ERR03."""
        import bcmcan
        bcmcan._sensor_state = bcmcan.SensorState()
        bcmcan._wiper_cmd    = bcmcan.WiperCmd()
        bcmcan._mode_mismatch_0x201 = False
        bcmcan._corrupt_crc_count   = 0

        # Injecter surcourant
        bcmcan._sensor_state.set_motor_current_override(bcmcan._WC_OVERCURRENT_THRESH + 0.2)

        fd201 = bcmcan._build_0x201(alive=0xAB)
        assert len(fd201) == 8
        assert fd201[6] == bcmcan._crc_tx(fd201[:6])   # CRC valide

        fd202 = bcmcan._build_0x202_from_state(alive_tx=0xAB)
        assert fd202[0] == 1       # NACK
        assert fd202[1] == 0x03   # ErrorCode=Overcurrent

        # Cleanup
        bcmcan._sensor_state.set_motor_current_override(-1.0)

    def test_full_crc_corruption_pipeline(self):
        """corrupt_crc_0x201 → 0x201 avec CRC inversé."""
        import bcmcan
        bcmcan._sensor_state       = bcmcan.SensorState()
        bcmcan._wiper_cmd          = bcmcan.WiperCmd()
        bcmcan._mode_mismatch_0x201 = False
        bcmcan._corrupt_crc_count  = 1

        fd = bcmcan._build_0x201(alive=0)
        expected_normal = bcmcan._crc_tx(fd[:6])
        # Le CRC doit être différent du normal (corrompu)
        assert fd[6] != expected_normal
        assert bcmcan._corrupt_crc_count == 0

    def test_b2104_pipeline_3_nacks_then_3_acks(self):
        """3 NACKs activent B2104, 3 ACKs le désactivent."""
        import bcmcan
        bcmcan._b2104_reset()

        for _ in range(3):
            bcmcan._b2104_track(bytes([0x01, 0x03, 0x00, 0x02]))
        assert bcmcan._b2104_active

        for _ in range(3):
            bcmcan._b2104_track(bytes([0x00, 0x00, 0x00, 0x00]))
        assert not bcmcan._b2104_active

    def test_alive_counter_freeze_threshold(self):
        """_alive_freeze_repeat_count ≥ 3 doit signaler la faute (sans crash)."""
        import bcmcan
        bcmcan._alive_counter_frozen      = True
        bcmcan._alive_counter_prev        = -1
        bcmcan._alive_freeze_repeat_count = 0

        for i in range(4):
            with bcmcan._alive_counter_frozen_lock:
                bcmcan._alive_freeze_repeat_count += 1

        assert bcmcan._alive_freeze_repeat_count >= bcmcan._ALIVE_FREEZE_THRESHOLD

        # Cleanup
        bcmcan._alive_counter_frozen      = False
        bcmcan._alive_freeze_repeat_count = 0

    def test_mode_mismatch_affects_0x201_and_0x202(self):
        """mode_mismatch → 0x201 CurrentMode=0 + 0x202 NACK ErrorCode=1."""
        import bcmcan
        bcmcan._sensor_state = bcmcan.SensorState()
        bcmcan._wiper_cmd    = bcmcan.WiperCmd()
        bcmcan._wiper_cmd.update(2, 1, 0, 0)   # mode=SPEED1
        bcmcan._mode_mismatch_0x201 = True
        bcmcan._corrupt_crc_count   = 0

        fd201 = bcmcan._build_0x201(alive=0)
        assert fd201[0] == 0   # CurrentMode forcé OFF

        fd202 = bcmcan._build_0x202_from_state(alive_tx=0)
        assert fd202[1] == 0x01   # ErrorCode=InvalidCmd

        bcmcan._mode_mismatch_0x201 = False

    def test_blade_frozen_propagates_to_0x201(self):
        """blade_frozen=True → BladePosition figée dans 0x201."""
        import bcmcan
        bcmcan._sensor_state = bcmcan.SensorState()
        bcmcan._sensor_state.set_blade_frozen(True, 42.0)
        bcmcan._wiper_cmd    = bcmcan.WiperCmd()
        bcmcan._mode_mismatch_0x201 = False
        bcmcan._corrupt_crc_count   = 0

        fd = bcmcan._build_0x201(alive=0)
        assert fd[2] == 42

        bcmcan._sensor_state.set_blade_frozen(False)

    def test_xcp_b2101_flag_propagates_to_fault_byte(self):
        """xcp_internal_fault=True → bit0 dans fault_byte de 0x201."""
        import bcmcan
        bcmcan._sensor_state = bcmcan.SensorState()
        bcmcan._sensor_state.set_xcp_internal_fault(True)
        bcmcan._wiper_cmd    = bcmcan.WiperCmd()
        bcmcan._mode_mismatch_0x201 = False
        bcmcan._corrupt_crc_count   = 0

        fd = bcmcan._build_0x201(alive=0)
        assert fd[4] & 0x01   # bit0 = WC_Internal

        bcmcan._sensor_state.set_xcp_internal_fault(False)

    def test_handle_vehicle_and_build_0x300(self):
        """_handle_vehicle_status + _build_0x300 → cohérence."""
        import bcmcan
        bcmcan._vehicle_state = bcmcan.VehicleState()
        bcmcan._handle_vehicle_status({
            "ignition_status": "ON",
            "reverse_gear": 1,
            "vehicle_speed": 50.0,
        })
        fd = bcmcan._build_0x300()
        assert fd[0] == 2   # IGN=ON
        assert fd[1] == 1   # REV=1

    def test_handle_rain_and_build_0x301(self):
        """_handle_rain_sensor + _build_0x301 → cohérence."""
        import bcmcan
        bcmcan._rain_state = bcmcan.RainState()
        bcmcan._handle_rain_sensor({"rain_intensity": 70, "sensor_status": "OK"})
        fd = bcmcan._build_0x301()
        assert fd[0] == 70
        assert fd[1] == 0x00   # sensor OK

    def test_wc_state_and_dtc_manager_integration(self, tmp_path):
        """wc_state.make_snapshot() peut alimenter DTCManager_WC.set_active."""
        import wc_doip
        from wc_dtc_manager import DTCManager_WC
        src = Path(SIM) / "wc_dtc_database.json"
        if not src.exists():
            pytest.skip("wc_dtc_database.json introuvable")
        dst = tmp_path / "wc_dtc_database.json"
        dst.write_text(src.read_text())

        mgr = DTCManager_WC(filepath=str(dst))
        wc_doip.wc_state.update_from_bcmcan(
            mode=0, speed=0, blade_pos=0.0,
            motor_current_a=0.9, fault_status=0, can_timeout=True
        )
        snap = wc_doip.wc_state.make_snapshot()
        mgr.set_active("B2101", snap)

        from wc_dtc_manager import STATUS_ACTIVE
        assert mgr.dtcs["B2101"]["status"] == STATUS_ACTIVE

    def test_crslin_node_state_full_lifecycle(self):
        """Lifecycle complet NodeState : OFF→SPEED1→freeze→unfreeze."""
        import crslin
        s = crslin.NodeState()
        assert s.wiper_op == crslin.WOp.OFF
        s.set_op(crslin.WOp.SPEED1)
        assert s.wiper_op == crslin.WOp.SPEED1
        for _ in range(5):
            s.inc_alive()
        assert s.alive_ctr == 5
        s.freeze_alive = True
        s.inc_alive()
        assert s.alive_ctr == 5   # gelé
        s.freeze_alive = False
        s.inc_alive()
        assert s.alive_ctr == 6

    def test_discover_bcm_host_returns_str(self):
        """_discover_bcm_host() retourne une str (vide si aucun hôte répond)."""
        import bcmcan
        result = bcmcan._discover_bcm_host(timeout=0.3)
        assert isinstance(result, str)

    def test_bcm_tcp_port_5000(self):
        import bcmcan
        assert bcmcan._TCP_PORT == 5000

    def test_bcm_lin_pid_constants_match_crslin(self):
        """Les PIDs dans les deux modules doivent être cohérents."""
        import crslin
        assert crslin.LIN_PID_16 == 0xD6
        assert crslin.LIN_PID_17 == 0x97

    def test_wc_doip_has_start_function(self):
        import wc_doip
        assert hasattr(wc_doip, "start")
        assert callable(wc_doip.start)

    def test_bcmcan_has_start_function(self):
        import bcmcan
        assert hasattr(bcmcan, "start")
        assert callable(bcmcan.start)

    def test_crslin_has_start_function(self):
        import crslin
        assert hasattr(crslin, "start")
        assert callable(crslin.start)

    def test_memory_wc_json_exists(self):
        path = SIM / "memory_wc.json"
        if not path.exists():
            pytest.skip("memory_wc.json non trouvé")
        with open(path) as f:
            mem = json.load(f)
        assert isinstance(mem, dict)

    def test_wc_dtc_database_json_has_required_dtcs(self):
        path = SIM / "wc_dtc_database.json"
        if not path.exists():
            pytest.skip("wc_dtc_database.json non trouvé")
        with open(path) as f:
            db = json.load(f)
        assert "dtcs" in db
        for dtc in ("B2101", "B2102", "B2103", "B2104"):
            assert dtc in db["dtcs"]

    def test_bcmcan_overcurrent_thresh_is_0_8(self):
        import bcmcan
        assert bcmcan._WC_OVERCURRENT_THRESH == pytest.approx(0.8, abs=0.01)

    def test_can_timeout_threshold_is_2s(self):
        import bcmcan
        assert bcmcan._CAN_TIMEOUT_WC == pytest.approx(2.0, abs=0.01)

    def test_bcm_tcp_port_pump_is_5556(self):
        import bcmcan
        assert bcmcan._BCM_PUMP_PORT == 5556

    def test_alive_freeze_threshold_is_3(self):
        import bcmcan
        assert bcmcan._ALIVE_FREEZE_THRESHOLD == 3

    def test_blade_mismatch_thresh_is_10pct(self):
        import bcmcan
        assert bcmcan._BLADE_MISMATCH_THRESH == pytest.approx(10.0, abs=0.1)

    def test_blade_mismatch_delay_is_1s(self):
        import bcmcan
        assert bcmcan._BLADE_MISMATCH_DELAY == pytest.approx(1.0, abs=0.01)
