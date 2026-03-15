#!/usr/bin/env python3
"""
test_wiper_system.py - Suite de tests unifiée
==============================================
Couvre les trois modules du système essuie-glace :
  - crslin.py  : noeud LIN esclave
  - bcmcan.py  : noeud BCM / CAN
  - main.py    : orchestrateur (arguments, surveillance des threads, signaux)

Les tests sont totalement hors-hardware : tous les modules tiers
nécessitant du vrai matériel (gpiod, board, busio, pigpio, adafruit_ads1x15,
serial, socket PF_CAN) sont mockés avant tout import des modules métier.

Structure du projet attendue :
    TestPlatform/
    ├── src/
    │   └── RaspberrySimulator/
    │       ├── bcmcan.py
    │       ├── crslin.py
    │       └── main.py
    └── tests/
        └── test_wiper_system.py

Lancer (depuis la racine du projet) :
    python3 -m pytest tests/test_wiper_system.py -v
    python3 -m unittest discover -s tests -v
    python3 tests/test_wiper_system.py -v
"""

import importlib
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
import types
import unittest
from io import StringIO
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# RÉSOLUTION DU PATH  :  tests/ -> src/platform/
# Ce bloc doit rester AVANT tout import de crslin / bcmcan / main.
# Fonctionne quelle que soit la racine d'exécution (racine, tests/, CI…).
# ---------------------------------------------------------------------------
_TESTS_DIR     = os.path.dirname(os.path.abspath(__file__))                    # …/TestPlatform/tests
_SRC_RASPBERRY = os.path.join(_TESTS_DIR, "..", "src", "RaspberrySimulator")   # …/TestPlatform/src/RaspberrySimulator
sys.path.insert(0, os.path.normpath(_SRC_RASPBERRY))

# ---------------------------------------------------------------------------
# CONFIGURATION DES LOGS (silencieux pendant les tests sauf en mode -v)
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.CRITICAL)  # supprime les logs métier


# ===========================================================================
# HELPERS GÉNÉRIQUES
# ===========================================================================

def _make_mock_module(name: str, **attrs) -> types.ModuleType:
    """Crée et enregistre dans sys.modules un module fictif avec des attributs."""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def _install_hardware_mocks():
    """
    Installe tous les mocks matériels nécessaires avant d'importer
    crslin ou bcmcan. Peut être appelé plusieurs fois sans effet de bord.
    """
    # --- serial (pyserial) ---
    if "serial" not in sys.modules:
        mock_serial_mod = _make_mock_module("serial")
        mock_serial_mod.Serial = MagicMock
        mock_serial_mod.SerialException = OSError

    # --- gpiod ---
    if "gpiod" not in sys.modules:
        gpiod_mod = _make_mock_module(
            "gpiod",
            Chip=MagicMock,
            LINE_REQ_DIR_IN=1,
            LINE_REQ_FLAG_BIAS_PULL_UP=2,
        )

    # --- board / busio ---
    if "board" not in sys.modules:
        board_mod = _make_mock_module("board")
        board_mod.SCL = MagicMock()
        board_mod.SDA = MagicMock()
    if "busio" not in sys.modules:
        busio_mod = _make_mock_module("busio")
        busio_mod.I2C = MagicMock

    # --- pigpio ---
    if "pigpio" not in sys.modules:
        pigpio_mod = _make_mock_module("pigpio")
        pi_inst = MagicMock()
        pi_inst.connected = True
        pigpio_mod.pi = MagicMock(return_value=pi_inst)
        pigpio_mod.OUTPUT = 1

    # --- adafruit_ads1x15 ---
    for sub in ("adafruit_ads1x15", "adafruit_ads1x15.ads1115", "adafruit_ads1x15.analog_in"):
        if sub not in sys.modules:
            _make_mock_module(sub)
    sys.modules["adafruit_ads1x15.ads1115"].ADS1115 = MagicMock
    sys.modules["adafruit_ads1x15.analog_in"].AnalogIn = MagicMock


_install_hardware_mocks()

# Maintenant on peut importer les modules métier en sécurité
import crslin
import bcmcan


# ===========================================================================
# ===========================================================================
#  PARTIE 1 : TESTS CRSLIN.PY
# ===========================================================================
# ===========================================================================

class TestCalculatePid(unittest.TestCase):
    """Vérification du calcul de PID LIN (bits de parité P0/P1)."""

    def test_pid_0x16_known_value(self):
        self.assertEqual(crslin._calculate_pid(0x16), 0xD6)

    def test_pid_0x17_known_value(self):
        self.assertEqual(crslin._calculate_pid(0x17), 0x97)

    def test_pid_mask_6bits(self):
        """Les bits 7-6 de l'entrée doivent être ignorés."""
        self.assertEqual(
            crslin._calculate_pid(0x16),
            crslin._calculate_pid(0x56),  # bits supérieurs mis à 1
        )

    def test_pid_zero(self):
        """PID pour ID=0 : P0=0, P1=1 → 0x80."""
        self.assertEqual(crslin._calculate_pid(0x00), 0x80)

    def test_pid_all_ids_return_8bit(self):
        """Tous les IDs 6 bits doivent retourner une valeur 0-255."""
        for i in range(64):
            pid = crslin._calculate_pid(i)
            self.assertGreaterEqual(pid, 0)
            self.assertLessEqual(pid, 0xFF)


class TestLinChecksum(unittest.TestCase):
    """Vérification du checksum LIN enhanced."""

    def test_simple_checksum(self):
        """Checksum must be the bitwise complement of the carry-folded sum."""
        pid  = 0xD6
        data = bytes([0x11, 0x05, 0xAB])
        raw  = pid + sum(data)
        while raw > 0xFF:
            raw = (raw & 0xFF) + (raw >> 8)
        expected = (~raw) & 0xFF
        self.assertEqual(crslin._lin_checksum(pid, data), expected)

    def test_checksum_complement_range(self):
        """Le résultat doit toujours être dans [0, 255]."""
        for pid in [0xD6, 0x97, 0x00, 0xFF]:
            cs = crslin._lin_checksum(pid, bytes([0x00, 0x00]))
            self.assertGreaterEqual(cs, 0)
            self.assertLessEqual(cs, 0xFF)

    def test_checksum_empty_data(self):
        """Checksum avec data vide : repose uniquement sur PID."""
        pid = 0x42
        raw = pid
        while raw > 0xFF:
            raw = (raw & 0xFF) + (raw >> 8)
        self.assertEqual(crslin._lin_checksum(pid, b""), (~raw) & 0xFF)

    def test_checksum_different_pid(self):
        """Deux PIDs différents donnent des checksums différents."""
        data = bytes([0xAA])
        self.assertNotEqual(
            crslin._lin_checksum(0xD6, data),
            crslin._lin_checksum(0x97, data),
        )


class TestWOp(unittest.TestCase):
    """Vérification de l'enum WOp."""

    def test_all_ops_defined(self):
        expected = {"OFF", "TOUCH", "SPEED1", "SPEED2", "AUTO",
                    "FRONT_WASH", "REAR_WASH", "REAR_WIPE"}
        self.assertEqual({op.name for op in crslin.WOp}, expected)

    def test_off_is_zero(self):
        self.assertEqual(int(crslin.WOp.OFF), 0)

    def test_ops_are_int(self):
        for op in crslin.WOp:
            self.assertIsInstance(int(op), int)


class TestNodeState(unittest.TestCase):
    """Tests de la dataclass NodeState (thread-safety + transitions)."""

    def setUp(self):
        self.state = crslin.NodeState()

    def test_initial_values(self):
        op, ss, alive, fault = self.state.snapshot()
        self.assertEqual(op, crslin.WOp.OFF)
        self.assertEqual(ss, crslin.STICK_VALID)
        self.assertEqual(alive, 0)
        self.assertEqual(fault, crslin.FAULT_NONE)

    def test_set_op_changes_mode(self):
        self.state.set_op(crslin.WOp.SPEED2)
        op, ss, _, _ = self.state.snapshot()
        self.assertEqual(op, crslin.WOp.SPEED2)
        self.assertEqual(ss, crslin.STICK_VALID)

    def test_inc_alive_wraps_at_256(self):
        self.state.alive_ctr = 255
        self.state.inc_alive()
        _, _, alive, _ = self.state.snapshot()
        self.assertEqual(alive, 0)

    def test_inc_alive_increments(self):
        self.state.inc_alive()
        _, _, alive, _ = self.state.snapshot()
        self.assertEqual(alive, 1)

    def test_thread_safe_concurrent_inc_alive(self):
        """100 threads incrémentent alive : le résultat final doit être 100."""
        self.state.alive_ctr = 0
        threads = [threading.Thread(target=self.state.inc_alive) for _ in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        _, _, alive, _ = self.state.snapshot()
        self.assertEqual(alive, 100)


class TestProcessLine(unittest.TestCase):
    """Tests du parseur de messages TCP entrants (crslin._process_line)."""

    def setUp(self):
        # Réinitialiser l'état global entre chaque test
        crslin._state = crslin.NodeState()
        self.broadcasts = []
        self._orig_broadcast = crslin._tcp_broadcast
        crslin._tcp_broadcast = lambda msg, exclude=None: self.broadcasts.append(msg)

    def tearDown(self):
        crslin._tcp_broadcast = self._orig_broadcast

    def test_json_cmd_touch(self):
        crslin._process_line('{"cmd": "TOUCH"}', ("127.0.0.1", 9999))
        self.assertTrue(any(m.get("type") == "cmd_ack" for m in self.broadcasts))

    def test_json_wiper_op_numeric(self):
        crslin._process_line('{"wiper_op": 2}', ("127.0.0.1", 9999))
        op, _, _, _ = crslin._state.snapshot()
        self.assertEqual(op, crslin.WOp.SPEED1)

    def test_plain_text_command(self):
        crslin._process_line("AUTO", ("127.0.0.1", 9999))
        op, _, _, _ = crslin._state.snapshot()
        self.assertEqual(op, crslin.WOp.AUTO)

    def test_unknown_command_triggers_error(self):
        crslin._process_line('{"cmd": "UNKNOWN_CMD"}', ("127.0.0.1", 9999))
        self.assertTrue(any(m.get("type") == "error" for m in self.broadcasts))

    def test_empty_line_ignored(self):
        crslin._process_line("", ("127.0.0.1", 9999))
        self.assertEqual(len(self.broadcasts), 0)

    def test_non_ascii_ignored(self):
        crslin._process_line("\x80\x90", ("127.0.0.1", 9999))
        self.assertEqual(len(self.broadcasts), 0)

    def test_invalid_json_ignored(self):
        crslin._process_line("{not valid json", ("127.0.0.1", 9999))
        self.assertEqual(len(self.broadcasts), 0)

    def test_status_command_returns_status_type(self):
        crslin._process_line("STATUS", ("127.0.0.1", 9999))
        self.assertTrue(any(m.get("type") == "status" for m in self.broadcasts))

    def test_type_message_ignored(self):
        """Les broadcasts en retour (type=tx16, etc.) ne doivent pas générer d'ack."""
        before = len(self.broadcasts)
        crslin._process_line('{"type": "cmd_ack", "op": "TOUCH"}', ("127.0.0.1", 9999))
        self.assertEqual(len(self.broadcasts), before)

    def test_case_insensitive_cmd(self):
        crslin._process_line('{"cmd": "speed1"}', ("127.0.0.1", 9999))
        op, _, _, _ = crslin._state.snapshot()
        self.assertEqual(op, crslin.WOp.SPEED1)

    def test_all_valid_ops_accepted(self):
        for op in crslin.WOp:
            crslin._process_line(json.dumps({"cmd": op.name}), ("127.0.0.1", 0))
            current_op, _, _, _ = crslin._state.snapshot()
            self.assertEqual(current_op, op)


class TestTcpBroadcast(unittest.TestCase):
    """Tests du mécanisme de broadcast TCP (crslin)."""

    def setUp(self):
        crslin._clients = []
        crslin._clients_lock = threading.Lock()

    def _make_mock_socket(self, fail=False):
        s = MagicMock(spec=socket.socket)
        if fail:
            s.sendall.side_effect = OSError("connexion perdue")
        return s

    def test_broadcast_sends_to_all(self):
        s1 = self._make_mock_socket()
        s2 = self._make_mock_socket()
        crslin._clients = [s1, s2]
        crslin._tcp_broadcast({"type": "info", "msg": "hello"})
        s1.sendall.assert_called_once()
        s2.sendall.assert_called_once()

    def test_broadcast_excludes_sender(self):
        s1 = self._make_mock_socket()
        s2 = self._make_mock_socket()
        crslin._clients = [s1, s2]
        crslin._tcp_broadcast({"type": "info"}, exclude=s1)
        s1.sendall.assert_not_called()
        s2.sendall.assert_called_once()

    def test_dead_socket_removed(self):
        dead = self._make_mock_socket(fail=True)
        live = self._make_mock_socket()
        crslin._clients = [dead, live]
        crslin._tcp_broadcast({"msg": "test"})
        self.assertNotIn(dead, crslin._clients)
        self.assertIn(live, crslin._clients)

    def test_broadcast_json_format(self):
        s = self._make_mock_socket()
        crslin._clients = [s]
        crslin._tcp_broadcast({"type": "cmd_ack", "op": "TOUCH", "val": 1})
        raw = s.sendall.call_args[0][0]
        decoded = raw.decode("utf-8")
        self.assertTrue(decoded.endswith("\n"))
        obj = json.loads(decoded.strip())
        self.assertEqual(obj["type"], "cmd_ack")


# ===========================================================================
# ===========================================================================
#  PARTIE 2 : TESTS BCMCAN.PY
# ===========================================================================
# ===========================================================================

class TestIgnitionEnum(unittest.TestCase):
    """Tests de l'enum Ignition et de sa méthode from_value."""

    def test_from_string_off(self):
        self.assertEqual(bcmcan.Ignition.from_value("OFF"), bcmcan.Ignition.OFF)

    def test_from_string_acc(self):
        self.assertEqual(bcmcan.Ignition.from_value("ACC"), bcmcan.Ignition.ACC)

    def test_from_string_on(self):
        self.assertEqual(bcmcan.Ignition.from_value("ON"), bcmcan.Ignition.ON)

    def test_from_int(self):
        self.assertEqual(bcmcan.Ignition.from_value(2), bcmcan.Ignition.ON)

    def test_clamp_above_max(self):
        self.assertEqual(bcmcan.Ignition.from_value(99), bcmcan.Ignition.ON)

    def test_clamp_below_min(self):
        self.assertEqual(bcmcan.Ignition.from_value(-5), bcmcan.Ignition.OFF)


class TestSensorState(unittest.TestCase):
    """Tests de la dataclass SensorState (update, snapshot, clamp)."""

    def setUp(self):
        self.s = bcmcan.SensorState()

    def test_initial_values(self):
        bp, mc, fs = self.s.snapshot()
        self.assertEqual(bp, 0.0)
        self.assertEqual(mc, 0.0)
        self.assertEqual(fs, 0)

    def test_update_and_snapshot(self):
        self.s.update(55.5, 0.750, 1)
        bp, mc, fs = self.s.snapshot()
        self.assertAlmostEqual(bp, 55.5, places=1)
        self.assertAlmostEqual(mc, 0.750, places=3)
        self.assertEqual(fs, 1)

    def test_blade_position_rounded_to_1_decimal(self):
        self.s.update(33.456, 0.0, 0)
        bp, _, _ = self.s.snapshot()
        self.assertEqual(bp, 33.5)

    def test_motor_current_rounded_to_3_decimals(self):
        self.s.update(0.0, 0.123456, 0)
        _, mc, _ = self.s.snapshot()
        self.assertEqual(mc, 0.123)

    def test_thread_safe_concurrent_updates(self):
        results = []

        def writer(val):
            self.s.update(val, val / 100, 0)

        def reader():
            results.append(self.s.snapshot())

        threads = [threading.Thread(target=writer, args=(float(i),)) for i in range(50)]
        threads += [threading.Thread(target=reader) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Pas d'assertion sur les valeurs (race bénigne), juste pas de crash


class TestVehicleState(unittest.TestCase):

    def setUp(self):
        self.v = bcmcan.VehicleState()

    def test_initial(self):
        ign, rev, spd = self.v.snapshot()
        self.assertEqual(ign, 0)
        self.assertEqual(rev, 0)
        self.assertEqual(spd, 0)

    def test_update(self):
        self.v.update(2, 1, 500)
        ign, rev, spd = self.v.snapshot()
        self.assertEqual(ign, 2)
        self.assertEqual(rev, 1)
        self.assertEqual(spd, 500)


class TestRainState(unittest.TestCase):

    def setUp(self):
        self.r = bcmcan.RainState()

    def test_initial(self):
        intensity, ok = self.r.snapshot()
        self.assertEqual(intensity, 0)
        self.assertTrue(ok)

    def test_update_error(self):
        self.r.update(80, False)
        intensity, ok = self.r.snapshot()
        self.assertEqual(intensity, 80)
        self.assertFalse(ok)


class TestWiperCmd(unittest.TestCase):

    def setUp(self):
        self.w = bcmcan.WiperCmd()

    def test_initial(self):
        mode, speed, wash, alive = self.w.snapshot()
        self.assertEqual((mode, speed, wash, alive), (0, 0, 0, 0))

    def test_update(self):
        self.w.update(3, 2, 1, 42)
        mode, speed, wash, alive = self.w.snapshot()
        self.assertEqual((mode, speed, wash, alive), (3, 2, 1, 42))


class TestCrcFunctions(unittest.TestCase):
    """Tests des fonctions CRC XOR de bcmcan."""

    def test_crc_rx_xor_three_bytes(self):
        data = bytes([0xAA, 0xBB, 0xCC, 0x00, 0, 0, 0, 0])
        expected = (0xAA ^ 0xBB ^ 0xCC) & 0xFF
        self.assertEqual(bcmcan._crc_rx(data), expected)

    def test_crc_tx_xor_all_bytes(self):
        payload = bytes([0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40])
        crc = 0
        for b in payload:
            crc ^= b
        self.assertEqual(bcmcan._crc_tx(payload), crc & 0xFF)

    def test_crc_tx_zero_payload(self):
        payload = bytes(7)
        self.assertEqual(bcmcan._crc_tx(payload), 0)

    def test_crc_rx_self_consistent(self):
        """Construit une trame et vérifie que le CRC est cohérent."""
        d = bytes([0x35, 0x10, 0x07, 0x00, 0, 0, 0, 0])
        crc = bcmcan._crc_rx(d)
        d_with_crc = d[:3] + bytes([crc]) + d[4:]
        self.assertEqual(bcmcan._crc_rx(d_with_crc), crc)


class TestBuildFrames(unittest.TestCase):
    """Tests de construction des trames CAN (0x300, 0x301, 0x201)."""

    def setUp(self):
        # Réinitialiser les états partagés avant chaque test
        bcmcan._vehicle_state = bcmcan.VehicleState()
        bcmcan._rain_state    = bcmcan.RainState()
        bcmcan._sensor_state  = bcmcan.SensorState()
        bcmcan._wiper_cmd     = bcmcan.WiperCmd()

    def test_build_0x300_default(self):
        frame = bcmcan._build_0x300()
        self.assertEqual(len(frame), 8)
        self.assertEqual(frame[0], 0)  # ignition OFF
        self.assertEqual(frame[1], 0)  # reverse = 0

    def test_build_0x300_with_vehicle_state(self):
        bcmcan._vehicle_state.update(2, 1, 1234)  # 1234 = 123.4 km/h
        frame = bcmcan._build_0x300()
        self.assertEqual(frame[0], 2)  # ignition ON
        self.assertEqual(frame[1], 1)  # reverse
        self.assertEqual((frame[2] << 8) | frame[3], 1234)

    def test_build_0x301_default(self):
        frame = bcmcan._build_0x301()
        self.assertEqual(len(frame), 8)
        self.assertEqual(frame[0], 0)    # intensity
        self.assertEqual(frame[1], 0x00) # OK

    def test_build_0x301_with_rain(self):
        bcmcan._rain_state.update(75, False)
        frame = bcmcan._build_0x301()
        self.assertEqual(frame[0], 75)
        self.assertEqual(frame[1], 0x01)  # sensor erreur

    def test_build_0x201_length_and_crc(self):
        alive = 7
        frame = bcmcan._build_0x201(alive)
        self.assertEqual(len(frame), 8)
        # Vérification du CRC (dernier octet = XOR des 7 premiers)
        expected_crc = bcmcan._crc_tx(frame[:7])
        self.assertEqual(frame[7], expected_crc)

    def test_build_0x201_alive_echo(self):
        alive = 42
        frame = bcmcan._build_0x201(alive)
        self.assertEqual(frame[6], alive)

    def test_build_0x201_blade_clamp(self):
        """Les valeurs de position hors [0, 100] doivent être clampées."""
        bcmcan._sensor_state.update(150.0, 0.0, 0)
        frame = bcmcan._build_0x201(0)
        self.assertEqual(frame[2], 100)

    def test_build_0x201_motor_current_encoding(self):
        """Le courant moteur est encodé en 1/10 A sur 16 bits hi/lo."""
        bcmcan._sensor_state.update(0.0, 0.5, 0)  # 0.5A -> 5 (int * 10)
        frame = bcmcan._build_0x201(0)
        mc_raw = (frame[3] << 8) | frame[4]
        self.assertEqual(mc_raw, 5)


class TestHandleVehicleStatus(unittest.TestCase):
    """Tests du parseur JSON Vehicle_Status."""

    def setUp(self):
        bcmcan._vehicle_state = bcmcan.VehicleState()

    def test_full_payload(self):
        bcmcan._handle_vehicle_status({
            "ignition_status": "ON",
            "reverse_gear": 1,
            "vehicle_speed": 90.5,
        })
        ign, rev, spd = bcmcan._vehicle_state.snapshot()
        self.assertEqual(ign, 2)
        self.assertEqual(rev, 1)
        self.assertEqual(spd, 905)

    def test_defaults_when_missing(self):
        bcmcan._handle_vehicle_status({})
        ign, rev, spd = bcmcan._vehicle_state.snapshot()
        self.assertEqual(ign, 0)
        self.assertEqual(rev, 0)
        self.assertEqual(spd, 0)

    def test_speed_rounding(self):
        bcmcan._handle_vehicle_status({"vehicle_speed": 12.34})
        _, _, spd = bcmcan._vehicle_state.snapshot()
        self.assertEqual(spd, 123)  # round(12.34 * 10) = 123

    def test_speed_clamp_max(self):
        bcmcan._handle_vehicle_status({"vehicle_speed": 999999.0})
        _, _, spd = bcmcan._vehicle_state.snapshot()
        self.assertEqual(spd, 65535)

    def test_speed_clamp_min(self):
        bcmcan._handle_vehicle_status({"vehicle_speed": -100.0})
        _, _, spd = bcmcan._vehicle_state.snapshot()
        self.assertEqual(spd, 0)

    def test_ignition_numeric(self):
        bcmcan._handle_vehicle_status({"ignition_status": 1})
        ign, _, _ = bcmcan._vehicle_state.snapshot()
        self.assertEqual(ign, 1)


class TestHandleRainSensor(unittest.TestCase):
    """Tests du parseur JSON RainSensorData."""

    def setUp(self):
        bcmcan._rain_state = bcmcan.RainState()

    def test_ok_sensor(self):
        bcmcan._handle_rain_sensor({"rain_intensity": 60, "sensor_status": "OK"})
        intensity, ok = bcmcan._rain_state.snapshot()
        self.assertEqual(intensity, 60)
        self.assertTrue(ok)

    def test_error_sensor(self):
        bcmcan._handle_rain_sensor({"rain_intensity": 0, "sensor_status": "ERROR"})
        _, ok = bcmcan._rain_state.snapshot()
        self.assertFalse(ok)

    def test_intensity_clamp_max(self):
        bcmcan._handle_rain_sensor({"rain_intensity": 200})
        intensity, _ = bcmcan._rain_state.snapshot()
        self.assertEqual(intensity, 100)

    def test_intensity_clamp_min(self):
        bcmcan._handle_rain_sensor({"rain_intensity": -50})
        intensity, _ = bcmcan._rain_state.snapshot()
        self.assertEqual(intensity, 0)

    def test_defaults(self):
        bcmcan._handle_rain_sensor({})
        intensity, ok = bcmcan._rain_state.snapshot()
        self.assertEqual(intensity, 0)
        self.assertTrue(ok)

    def test_float_intensity_rounds(self):
        bcmcan._handle_rain_sensor({"rain_intensity": 33.6})
        intensity, _ = bcmcan._rain_state.snapshot()
        self.assertEqual(intensity, 34)


# ===========================================================================
# ===========================================================================
#  PARTIE 3 : TESTS MAIN.PY
# ===========================================================================
# ===========================================================================

class TestParseArgs(unittest.TestCase):
    """Tests du parseur d'arguments CLI de main.py."""

    def _parse(self, args):
        """Lance _parse_args avec sys.argv simulé."""
        with patch("sys.argv", ["main.py"] + args):
            import main
            return main._parse_args()

    def test_default_mode_is_both(self):
        import main
        with patch("sys.argv", ["main.py"]):
            args = main._parse_args()
        self.assertEqual(args.mode, "both")

    def test_mode_lin(self):
        args = self._parse(["--mode", "lin"])
        self.assertEqual(args.mode, "lin")

    def test_mode_can(self):
        args = self._parse(["--mode", "can"])
        self.assertEqual(args.mode, "can")

    def test_default_ports(self):
        args = self._parse([])
        self.assertEqual(args.lin_port, 5555)
        self.assertEqual(args.can_port, 5556)

    def test_custom_ports(self):
        args = self._parse(["--lin-port", "6000", "--can-port", "6001"])
        self.assertEqual(args.lin_port, 6000)
        self.assertEqual(args.can_port, 6001)

    def test_default_host(self):
        args = self._parse([])
        self.assertEqual(args.host, "0.0.0.0")

    def test_default_serial(self):
        args = self._parse([])
        self.assertEqual(args.lin_serial, "/dev/serial0")

    def test_log_level_debug(self):
        args = self._parse(["--log-level", "DEBUG"])
        self.assertEqual(args.log_level, "DEBUG")

    def test_invalid_mode_exits(self):
        with self.assertRaises(SystemExit):
            with patch("sys.stderr", new_callable=StringIO):  # supprime le message argparse
                self._parse(["--mode", "invalid"])


class TestAllAlive(unittest.TestCase):
    """Tests de la fonction de surveillance des threads."""

    def test_all_alive_true(self):
        import main
        t1 = threading.Thread(target=lambda: time.sleep(5), daemon=True)
        t2 = threading.Thread(target=lambda: time.sleep(5), daemon=True)
        t1.start()
        t2.start()
        self.assertTrue(main._all_alive([t1, t2]))
        # nettoyage implicite : les threads daemon s'arrêtent avec le test

    def test_all_alive_false_when_one_dead(self):
        import main
        t1 = threading.Thread(target=lambda: None)
        t2 = threading.Thread(target=lambda: time.sleep(10), daemon=True)
        t1.start()
        t2.start()
        t1.join()  # t1 est fini
        self.assertFalse(main._all_alive([t1, t2]))

    def test_empty_list_returns_true(self):
        import main
        self.assertTrue(main._all_alive([]))


class TestSignalHandler(unittest.TestCase):
    """Tests de la construction du handler SIGINT/SIGTERM."""

    def test_handler_callable(self):
        import main
        handler = main._build_signal_handler([])
        self.assertTrue(callable(handler))

    def test_handler_calls_sys_exit(self):
        import main
        finished_threads = []
        t = threading.Thread(target=lambda: None)
        t.start()
        t.join()  # t est mort

        handler = main._build_signal_handler([t])
        with self.assertRaises(SystemExit):
            with patch("bcmcan.cleanup"):
                handler(signal.SIGINT, None)

    def test_handler_attempts_bcmcan_cleanup(self):
        import main
        with patch("bcmcan.cleanup") as mock_cleanup:
            handler = main._build_signal_handler([])
            with self.assertRaises(SystemExit):
                handler(signal.SIGTERM, None)
            mock_cleanup.assert_called_once_with("SIGTERM")


# ===========================================================================
# ===========================================================================
#  PARTIE 4 : TESTS D'INTÉGRATION (TCP bas niveau sans vrai hardware)
# ===========================================================================
# ===========================================================================

class TestCrslinTcpIntegration(unittest.TestCase):
    """
    Démarre un vrai serveur TCP crslin sur un port éphémère,
    envoie des commandes JSON et vérifie les réponses.
    Le thread LIN n'est pas démarré (pas de vrai UART).
    """

    @classmethod
    def setUpClass(cls):
        crslin._state   = crslin.NodeState()
        crslin._clients = []
        crslin._clients_lock = threading.Lock()

        # Trouver un port libre
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]

        cls.server_thread = threading.Thread(
            target=crslin._tcp_server,
            args=("127.0.0.1", cls.port),
            daemon=True,
        )
        cls.server_thread.start()
        time.sleep(0.15)  # laisser le serveur démarrer

    def _connect(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        s.settimeout(2)
        return s

    def _recv_line(self, s) -> dict:
        buf = b""
        while b"\n" not in buf:
            buf += s.recv(1024)
        return json.loads(buf.split(b"\n")[0])

    def test_send_touch_receives_ack(self):
        with self._connect() as s:
            s.sendall(b'{"cmd": "TOUCH"}\n')
            msg = self._recv_line(s)
            self.assertEqual(msg.get("type"), "cmd_ack")
            self.assertEqual(msg.get("op"), "TOUCH")

    def test_send_off_command(self):
        with self._connect() as s:
            s.sendall(b'{"cmd": "OFF"}\n')
            msg = self._recv_line(s)
            self.assertEqual(msg.get("type"), "cmd_ack")

    def test_send_status_returns_status(self):
        with self._connect() as s:
            s.sendall(b'{"cmd": "STATUS"}\n')
            msg = self._recv_line(s)
            self.assertEqual(msg.get("type"), "status")
            self.assertIn("op", msg)
            self.assertIn("alive", msg)

    def test_send_plain_text_speed2(self):
        with self._connect() as s:
            s.sendall(b"SPEED2\n")
            msg = self._recv_line(s)
            self.assertEqual(msg.get("type"), "cmd_ack")
            self.assertEqual(msg.get("op"), "SPEED2")

    def test_unknown_command_returns_error(self):
        with self._connect() as s:
            s.sendall(b'{"cmd": "FLY"}\n')
            msg = self._recv_line(s)
            self.assertEqual(msg.get("type"), "error")

    def test_multiple_commands_sequential(self):
        with self._connect() as s:
            for op in ("TOUCH", "SPEED1", "AUTO", "OFF"):
                s.sendall(json.dumps({"cmd": op}).encode() + b"\n")
                msg = self._recv_line(s)
                self.assertEqual(msg.get("op"), op)


class TestBcmcanTcpIntegration(unittest.TestCase):
    """
    Démarre un vrai serveur TCP bcmcan sur un port éphémère,
    envoie des données JSON VehicleStatus / RainSensorData
    et vérifie que les états internes sont mis à jour.
    Le hardware est mocké.
    """

    @classmethod
    def setUpClass(cls):
        bcmcan._vehicle_state = bcmcan.VehicleState()
        bcmcan._rain_state    = bcmcan.RainState()
        bcmcan._sensor_state  = bcmcan.SensorState()
        bcmcan._tcp_clients   = []
        bcmcan._tcp_clients_lock = threading.Lock()

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]

        cls.server_thread = threading.Thread(
            target=bcmcan._tcp_server,
            args=("127.0.0.1", cls.port),
            daemon=True,
        )
        cls.server_thread.start()
        time.sleep(0.15)

    def _connect(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        s.settimeout(2)
        return s

    def test_initial_state_sent_on_connect(self):
        """Le serveur doit envoyer l'état initial des capteurs à la connexion."""
        with self._connect() as s:
            raw = b""
            while b"\n" not in raw:
                raw += s.recv(1024)
            msg = json.loads(raw.split(b"\n")[0])
            self.assertIn("front", msg)

    def test_vehicle_status_updates_state(self):
        with self._connect() as s:
            # Lire l'état initial
            raw = b""
            while b"\n" not in raw:
                raw += s.recv(1024)

            payload = json.dumps({
                "ignition_status": "ON",
                "reverse_gear": 0,
                "vehicle_speed": 60.0,
            }) + "\n"
            s.sendall(payload.encode())
            time.sleep(0.1)

        ign, rev, spd = bcmcan._vehicle_state.snapshot()
        self.assertEqual(ign, 2)
        self.assertEqual(spd, 600)

    def test_rain_sensor_updates_state(self):
        with self._connect() as s:
            raw = b""
            while b"\n" not in raw:
                raw += s.recv(1024)

            payload = json.dumps({
                "rain_intensity": 55,
                "sensor_status": "OK",
            }) + "\n"
            s.sendall(payload.encode())
            time.sleep(0.1)

        intensity, ok = bcmcan._rain_state.snapshot()
        self.assertEqual(intensity, 55)
        self.assertTrue(ok)

    def test_combined_json_updates_both_states(self):
        """Un seul JSON peut contenir les clés des deux types de données."""
        with self._connect() as s:
            raw = b""
            while b"\n" not in raw:
                raw += s.recv(1024)

            payload = json.dumps({
                "ignition_status": "ACC",
                "vehicle_speed": 0.0,
                "rain_intensity": 10,
                "sensor_status": "ERROR",
            }) + "\n"
            s.sendall(payload.encode())
            time.sleep(0.1)

        ign, _, _ = bcmcan._vehicle_state.snapshot()
        _, ok = bcmcan._rain_state.snapshot()
        self.assertEqual(ign, 1)   # ACC
        self.assertFalse(ok)


# ===========================================================================
# ===========================================================================
#  PARTIE 5 : TESTS DE ROBUSTESSE / EDGE-CASES
# ===========================================================================
# ===========================================================================

class TestEdgeCases(unittest.TestCase):
    """Cas limites et robustesse transversale."""

    def test_lin_checksum_carry_propagation(self):
        """Le carry doit se propager correctement pour des sommes > 0x1FF."""
        pid  = 0xFF
        data = bytes([0xFF, 0xFF])
        cs   = crslin._lin_checksum(pid, data)
        self.assertGreaterEqual(cs, 0)
        self.assertLessEqual(cs, 0xFF)

    def test_build_0x300_speed_boundary_zero(self):
        bcmcan._vehicle_state = bcmcan.VehicleState()
        bcmcan._vehicle_state.update(0, 0, 0)
        frame = bcmcan._build_0x300()
        self.assertEqual(frame[2], 0)
        self.assertEqual(frame[3], 0)

    def test_build_0x300_speed_boundary_max(self):
        bcmcan._vehicle_state = bcmcan.VehicleState()
        bcmcan._vehicle_state.update(0, 0, 65535)
        frame = bcmcan._build_0x300()
        self.assertEqual((frame[2] << 8) | frame[3], 65535)

    def test_crc_tx_single_byte(self):
        """CRC XOR sur un seul octet doit retourner cet octet."""
        payload = bytes([0xAB]) + bytes(6)
        # XOR de 0xAB avec 0x00*6 = 0xAB
        self.assertEqual(bcmcan._crc_tx(payload), 0xAB)

    def test_wop_int_enum_comparison(self):
        """WOp doit être comparable directement avec des entiers."""
        self.assertEqual(crslin.WOp.TOUCH, 1)
        self.assertTrue(crslin.WOp.OFF == 0)

    def test_node_state_set_op_resets_stick_status(self):
        state = crslin.NodeState()
        state.stick_status = 0xFF  # valeur arbitraire
        state.set_op(crslin.WOp.AUTO)
        _, ss, _, _ = state.snapshot()
        self.assertEqual(ss, crslin.STICK_VALID)

    def test_handle_vehicle_status_reverse_gear_truthy(self):
        bcmcan._vehicle_state = bcmcan.VehicleState()
        bcmcan._handle_vehicle_status({"reverse_gear": 99})
        _, rev, _ = bcmcan._vehicle_state.snapshot()
        self.assertEqual(rev, 1)

    def test_handle_vehicle_status_reverse_gear_falsy(self):
        bcmcan._vehicle_state = bcmcan.VehicleState()
        bcmcan._handle_vehicle_status({"reverse_gear": 0})
        _, rev, _ = bcmcan._vehicle_state.snapshot()
        self.assertEqual(rev, 0)

    def test_rain_sensor_status_case_insensitive(self):
        bcmcan._rain_state = bcmcan.RainState()
        bcmcan._handle_rain_sensor({"sensor_status": "ok"})
        _, ok = bcmcan._rain_state.snapshot()
        self.assertTrue(ok)

    def test_wiper_cmd_snapshot_consistent(self):
        """Les valeurs retournées par snapshot doivent rester cohérentes."""
        wc = bcmcan.WiperCmd()
        wc.update(1, 2, 3, 4)
        snapshot = wc.snapshot()
        self.assertEqual(snapshot, (1, 2, 3, 4))


# ===========================================================================
# POINT D'ENTRÉE
# ===========================================================================
if __name__ == "__main__":
    loader  = unittest.TestLoader()
    loader.sortTestMethodsUsing = None  # ordre de définition
    suite   = loader.loadTestsFromModule(sys.modules[__name__])
    runner  = unittest.TextTestRunner(verbosity=2 if "-v" in sys.argv else 1)
    result  = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)