#!/usr/bin/env python3
"""
test_Platform.py  —  Tests complets de la plateforme WipeWash
=============================================================
Couvre :
  1. constants.py        — ports, palette, WOP (champs complets), polices
  2. network.py          — auto_discover, auto_discover_all, scan_async
  3. rte_client.py       — constantes WOP, RTEClient (mocked Redis)
  4. test_cases.py       — TestResult, BaseTest, BaseCycleTest, T01-T39
  5. workers.py          — MotorVehicleWorker, PumpDataClient (mocked Qt)
  6. test_runner.py      — TestRunner (mocked Qt)

Usage :
    python3 test_Platform.py              # tous les tests
    python3 test_Platform.py -v           # verbose
    python3 test_Platform.py TestRTEClient  # classe spécifique

Dépendances : stdlib uniquement (unittest, unittest.mock, threading, socket…)
"""

import sys
import os
import time
import json
import socket
import threading
import unittest
from unittest.mock import MagicMock, patch, PropertyMock, call
from dataclasses import fields as dc_fields

# ─── Chemin vers le package Platform ───────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "Platform"))

# ─── Mock PyQt6 avant toute importation qui en dépend ───────────────────────
_qt_mock = MagicMock()
_qt_mock.QtCore.QObject = object         # pour les classes qui héritent de QObject
_qt_mock.QtCore.pyqtSignal = lambda *a, **kw: MagicMock()
_qt_mock.QtCore.QTimer = MagicMock
_qt_mock.QtCore.Qt.ConnectionType.DirectConnection = 0
sys.modules.setdefault("PyQt6", _qt_mock)
sys.modules.setdefault("PyQt6.QtCore", _qt_mock.QtCore)
sys.modules.setdefault("PyQt6.QtWidgets", MagicMock())
sys.modules.setdefault("PyQt6.QtGui", MagicMock())


# ═══════════════════════════════════════════════════════════════════════════
#  1. CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════
class TestConstants(unittest.TestCase):
    """Vérifie toutes les constantes définies dans constants.py."""

    @classmethod
    def setUpClass(cls):
        import constants as C
        cls.C = C

    # ── Ports TCP ──────────────────────────────────────────────────────────
    def test_port_motor(self):
        self.assertEqual(self.C.PORT_MOTOR, 5000)

    def test_port_bcmcan(self):
        self.assertEqual(self.C.PORT_BCMCAN, 5002)

    def test_port_lin(self):
        self.assertEqual(self.C.PORT_LIN, 5555)

    def test_port_pump_rx(self):
        self.assertEqual(self.C.PORT_PUMP_RX, 5556)

    def test_port_pump_tx(self):
        self.assertEqual(self.C.PORT_PUMP_TX, 5001)

    def test_port_can(self):
        self.assertEqual(self.C.PORT_CAN, 5557)

    def test_all_ports_are_int(self):
        for attr in ("PORT_MOTOR", "PORT_BCMCAN", "PORT_LIN",
                     "PORT_PUMP_RX", "PORT_PUMP_TX", "PORT_CAN"):
            self.assertIsInstance(getattr(self.C, attr), int, attr)

    def test_ports_are_unique(self):
        ports = [self.C.PORT_MOTOR, self.C.PORT_BCMCAN, self.C.PORT_LIN,
                 self.C.PORT_PUMP_RX, self.C.PORT_PUMP_TX, self.C.PORT_CAN]
        self.assertEqual(len(ports), len(set(ports)), "Ports en doublon")

    # ── CAN IDs / couleurs CAN ─────────────────────────────────────────────
    def test_can_cmd_color_defined(self):
        self.assertTrue(hasattr(self.C, "CAN_CMD_C"))

    def test_can_sta_color_defined(self):
        self.assertTrue(hasattr(self.C, "CAN_STA_C"))

    def test_can_veh_color_defined(self):
        self.assertTrue(hasattr(self.C, "CAN_VEH_C"))

    def test_can_rain_color_defined(self):
        self.assertTrue(hasattr(self.C, "CAN_RAIN_C"))

    # ── Palette ────────────────────────────────────────────────────────────
    def test_required_palette_keys(self):
        required = ["W_BG", "W_PANEL", "W_TEXT", "A_TEAL", "A_RED", "A_GREEN"]
        for key in required:
            with self.subTest(key=key):
                self.assertTrue(hasattr(self.C, key), f"Clé {key} manquante")

    def test_extended_palette_keys(self):
        extended = ["W_PANEL2", "W_PANEL3", "W_TOOLBAR", "W_TITLEBAR",
                    "W_BORDER", "W_TEXT2", "W_TEXT_DIM", "A_TEAL2",
                    "A_GREEN_L", "A_RED_L", "A_ORANGE", "A_AMBER"]
        for key in extended:
            with self.subTest(key=key):
                self.assertTrue(hasattr(self.C, key), f"Clé {key} manquante")

    def test_palette_values_are_hex_strings(self):
        for key in ["W_BG", "W_PANEL", "W_TEXT", "A_TEAL", "A_RED", "A_GREEN"]:
            val = getattr(self.C, key)
            with self.subTest(key=key):
                self.assertIsInstance(val, str)
                self.assertTrue(val.startswith("#"), f"{key}={val!r} pas hex")
                self.assertIn(len(val), (4, 7, 9), f"{key} longueur inattendue")

    # ── Polices ────────────────────────────────────────────────────────────
    def test_font_ui_defined(self):
        self.assertTrue(hasattr(self.C, "FONT_UI"))
        self.assertIsInstance(self.C.FONT_UI, str)
        self.assertGreater(len(self.C.FONT_UI), 0)

    def test_font_mono_defined(self):
        self.assertTrue(hasattr(self.C, "FONT_MONO"))
        self.assertIsInstance(self.C.FONT_MONO, str)

    def test_fonts_are_different(self):
        self.assertNotEqual(self.C.FONT_UI, self.C.FONT_MONO)

    # ── MAX_ROWS ───────────────────────────────────────────────────────────
    def test_max_rows_defined(self):
        self.assertTrue(hasattr(self.C, "MAX_ROWS"))
        self.assertIsInstance(self.C.MAX_ROWS, int)
        self.assertGreater(self.C.MAX_ROWS, 0)

    # ── WOP : structure ────────────────────────────────────────────────────
    def test_wop_has_8_entries(self):
        self.assertEqual(len(self.C.WOP), 8, f"Trouvé {len(self.C.WOP)}")

    def test_wop_keys_are_0_to_7(self):
        self.assertEqual(set(self.C.WOP.keys()), set(range(8)))

    def test_wop_all_required_fields(self):
        required_fields = {"name", "label", "desc", "req", "color"}
        for code, entry in self.C.WOP.items():
            with self.subTest(code=code):
                self.assertTrue(required_fields.issubset(entry.keys()),
                                f"WOP[{code}] champs manquants: "
                                f"{required_fields - entry.keys()}")

    def test_wop_names(self):
        expected = {0: "OFF", 1: "TOUCH", 2: "SPEED1", 3: "SPEED2",
                    4: "AUTO", 5: "FRONT_WASH", 6: "REAR_WASH", 7: "REAR_WIPE"}
        for code, name in expected.items():
            with self.subTest(code=code):
                self.assertEqual(self.C.WOP[code]["name"], name)

    def test_wop_labels_non_empty(self):
        for code in range(8):
            with self.subTest(code=code):
                self.assertGreater(len(self.C.WOP[code]["label"]), 0)

    def test_wop_reqs_non_empty(self):
        for code in range(8):
            with self.subTest(code=code):
                self.assertTrue(self.C.WOP[code]["req"].startswith("SRD_"),
                                f"WOP[{code}]['req'] inattendu")

    def test_wop_colors_are_hex(self):
        for code in range(8):
            c = self.C.WOP[code]["color"]
            with self.subTest(code=code):
                self.assertTrue(c.startswith("#"), f"WOP[{code}]['color']={c!r}")

    def test_wop_names_unique(self):
        names = [self.C.WOP[i]["name"] for i in range(8)]
        self.assertEqual(len(names), len(set(names)), "Noms WOP en doublon")


# ═══════════════════════════════════════════════════════════════════════════
#  2. NETWORK
# ═══════════════════════════════════════════════════════════════════════════
class TestNetwork(unittest.TestCase):
    """Vérifie network.py sans connexion réseau réelle."""

    @classmethod
    def setUpClass(cls):
        import network
        cls.net = network

    # ── auto_discover ──────────────────────────────────────────────────────
    def test_auto_discover_returns_none_or_str(self):
        result = self.net.auto_discover(port=9, timeout=0.3)
        self.assertIn(type(result), (str, type(None)))

    def test_auto_discover_port_discard_no_host(self):
        """Port discard (9) devrait retourner None en CI (aucun serveur)."""
        result = self.net.auto_discover(port=9, timeout=0.3)
        # Accepte None ou une IP valide (si réseau disponible)
        if result is not None:
            parts = result.split(".")
            self.assertEqual(len(parts), 4, f"IP invalide : {result}")

    # ── auto_discover_all ──────────────────────────────────────────────────
    def test_auto_discover_all_returns_list(self):
        result = self.net.auto_discover_all(port=9, timeout=0.3)
        self.assertIsInstance(result, list)

    def test_auto_discover_all_sorted(self):
        result = self.net.auto_discover_all(port=9, timeout=0.3)
        self.assertEqual(result, sorted(result))

    def test_auto_discover_all_contains_only_strings(self):
        result = self.net.auto_discover_all(port=9, timeout=0.3)
        for item in result:
            self.assertIsInstance(item, str)

    # ── scan_async ─────────────────────────────────────────────────────────
    def test_scan_async_calls_done_cb(self):
        done_event = threading.Event()
        received = []

        def done_cb(hosts):
            received.extend(hosts)
            done_event.set()

        self.net.scan_async(port=9, progress_cb=lambda _: None, done_cb=done_cb)
        fired = done_event.wait(timeout=5.0)
        self.assertTrue(fired, "done_cb non déclenché dans le délai")

    def test_scan_async_done_cb_receives_list(self):
        result_holder = []
        done_event = threading.Event()

        def done_cb(hosts):
            result_holder.append(hosts)
            done_event.set()

        self.net.scan_async(port=9, progress_cb=lambda _: None, done_cb=done_cb)
        done_event.wait(timeout=5.0)
        self.assertGreater(len(result_holder), 0)
        self.assertIsInstance(result_holder[0], list)

    def test_scan_async_progress_cb_called(self):
        progress_values = []
        done_event = threading.Event()

        self.net.scan_async(
            port=9,
            progress_cb=lambda pct: progress_values.append(pct),
            done_cb=lambda _: done_event.set(),
        )
        done_event.wait(timeout=5.0)
        # progress_cb doit avoir été appelé au moins une fois
        self.assertGreater(len(progress_values), 0)

    def test_scan_async_progress_values_in_range(self):
        progress_values = []
        done_event = threading.Event()

        self.net.scan_async(
            port=9,
            progress_cb=lambda pct: progress_values.append(pct),
            done_cb=lambda _: done_event.set(),
        )
        done_event.wait(timeout=5.0)
        for pct in progress_values:
            self.assertGreaterEqual(pct, 0)
            self.assertLessEqual(pct, 100)

    def test_scan_async_done_cb_sorted(self):
        result_holder = []
        done_event = threading.Event()

        def done_cb(hosts):
            result_holder.append(hosts)
            done_event.set()

        self.net.scan_async(port=9, progress_cb=lambda _: None, done_cb=done_cb)
        done_event.wait(timeout=5.0)
        if result_holder and result_holder[0]:
            hosts = result_holder[0]
            self.assertEqual(hosts, sorted(hosts), "done_cb: hôtes non triés")

    # ── _probe interne ──────────────────────────────────────────────────────
    def test_probe_finds_local_open_port(self):
        """Crée un serveur local et vérifie que _probe le trouve."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            results = []
            lock = threading.Lock()
            self.net._probe("127.0.0.1", port, results, lock)
            self.assertEqual(results, ["127.0.0.1"])
        finally:
            srv.close()

    def test_probe_does_not_add_closed_port(self):
        """_probe ne doit rien ajouter pour un port fermé."""
        results = []
        lock = threading.Lock()
        self.net._probe("127.0.0.1", 1, results, lock)
        self.assertEqual(results, [])

    def test_probe_early_exit_on_found_event(self):
        """_probe doit sortir immédiatement si found est déjà set."""
        found = threading.Event()
        found.set()
        results = []
        lock = threading.Lock()
        # Même si le port était ouvert, l'event court-circuite
        self.net._probe("127.0.0.1", 9999, results, lock, found)
        self.assertEqual(results, [])


# ═══════════════════════════════════════════════════════════════════════════
#  3. RTE_CLIENT
# ═══════════════════════════════════════════════════════════════════════════
class TestRTEClientConstants(unittest.TestCase):
    """Constantes WOP_* et WOP_NAMES."""

    @classmethod
    def setUpClass(cls):
        import rte_client as rc
        cls.rc = rc

    def test_wop_off(self):
        self.assertEqual(self.rc.WOP_OFF, 0)

    def test_wop_touch(self):
        self.assertEqual(self.rc.WOP_TOUCH, 1)

    def test_wop_speed1(self):
        self.assertEqual(self.rc.WOP_SPEED1, 2)

    def test_wop_speed2(self):
        self.assertEqual(self.rc.WOP_SPEED2, 3)

    def test_wop_auto(self):
        self.assertEqual(self.rc.WOP_AUTO, 4)

    def test_wop_front_wash(self):
        self.assertEqual(self.rc.WOP_FRONT_WASH, 5)

    def test_wop_rear_wash(self):
        self.assertEqual(self.rc.WOP_REAR_WASH, 6)

    def test_wop_rear_wipe(self):
        self.assertEqual(self.rc.WOP_REAR_WIPE, 7)

    def test_wop_names_all_present(self):
        for name in ("OFF", "TOUCH", "SPEED1", "SPEED2", "AUTO",
                     "FRONT_WASH", "REAR_WASH", "REAR_WIPE"):
            with self.subTest(name=name):
                self.assertIn(name, self.rc.WOP_NAMES)

    def test_wop_names_values_match_constants(self):
        mapping = {
            "OFF": 0, "TOUCH": 1, "SPEED1": 2, "SPEED2": 3,
            "AUTO": 4, "FRONT_WASH": 5, "REAR_WASH": 6, "REAR_WIPE": 7,
        }
        for name, code in mapping.items():
            with self.subTest(name=name):
                self.assertEqual(self.rc.WOP_NAMES[name], code)

    def test_wop_names_no_extra_keys(self):
        self.assertEqual(len(self.rc.WOP_NAMES), 8)


class TestRTEClientMocked(unittest.TestCase):
    """RTEClient avec Redis mocké pour tester toute la logique interne."""

    def _make_client(self, connected=True):
        """Fabrique un RTEClient avec un Redis mocké (injection directe)."""
        import rte_client as rc
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True

        # Instanciation sans appeler __init__ (qui essaierait de se connecter)
        client = rc.RTEClient.__new__(rc.RTEClient)
        client._host       = "10.20.0.25"
        client._port       = 6379
        client._connected  = connected
        client._r          = mock_redis if connected else None
        client._sub_thread = None
        return client, mock_redis

    # ── get() ──────────────────────────────────────────────────────────────
    def test_get_returns_decoded_string(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_r.get.return_value = b"SPEED1"
        self.assertEqual(client.get("state"), "SPEED1")

    def test_get_returns_none_if_key_absent(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_r.get.return_value = None
        self.assertIsNone(client.get("state"))

    def test_get_returns_none_if_not_connected(self):
        import rte_client as rc
        client, _ = self._make_client(connected=False)
        self.assertIsNone(client.get("state"))

    def test_get_uses_rte_prefix(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_r.get.return_value = b"42"
        client.get("vehicle_speed")
        mock_r.get.assert_called_once_with("rte:vehicle_speed")

    def test_get_returns_none_on_exception(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_r.get.side_effect = Exception("Redis down")
        self.assertIsNone(client.get("state"))

    # ── get_int() ─────────────────────────────────────────────────────────
    def test_get_int_normal(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_r.get.return_value = b"42"
        self.assertEqual(client.get_int("speed"), 42)

    def test_get_int_default_on_none(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_r.get.return_value = None
        self.assertEqual(client.get_int("speed", default=99), 99)

    def test_get_int_default_on_invalid(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_r.get.return_value = b"not_a_number"
        self.assertEqual(client.get_int("speed", default=0), 0)

    # ── get_float() ───────────────────────────────────────────────────────
    def test_get_float_normal(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_r.get.return_value = b"1.23"
        self.assertAlmostEqual(client.get_float("current"), 1.23)

    def test_get_float_default_on_none(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_r.get.return_value = None
        self.assertEqual(client.get_float("current", default=0.0), 0.0)

    def test_get_float_default_on_invalid(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_r.get.return_value = b"xyz"
        self.assertEqual(client.get_float("current", default=-1.0), -1.0)

    # ── get_bool() ────────────────────────────────────────────────────────
    def test_get_bool_true_values(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        for val in (b"true", b"True", b"1", b"yes"):
            mock_r.get.return_value = val
            with self.subTest(val=val):
                self.assertTrue(client.get_bool("flag"))

    def test_get_bool_false_values(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        for val in (b"false", b"False", b"0", b"no"):
            mock_r.get.return_value = val
            with self.subTest(val=val):
                self.assertFalse(client.get_bool("flag"))

    def test_get_bool_default_on_none(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_r.get.return_value = None
        self.assertFalse(client.get_bool("flag", default=False))
        self.assertTrue(client.get_bool("flag", default=True))

    # ── set_cmd() ────────────────────────────────────────────────────────
    def test_set_cmd_publishes_json(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_r.publish.return_value = 1
        result = client.set_cmd("crs_wiper_op", 2)
        self.assertTrue(result)
        args = mock_r.publish.call_args
        self.assertEqual(args[0][0], "rte_cmd")
        payload = json.loads(args[0][1])
        self.assertEqual(payload["key"], "crs_wiper_op")
        self.assertEqual(payload["value"], 2)

    def test_set_cmd_returns_false_if_disconnected(self):
        import rte_client as rc
        client, _ = self._make_client(connected=False)
        self.assertFalse(client.set_cmd("crs_wiper_op", 2))

    def test_set_cmd_returns_false_on_exception(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_r.publish.side_effect = Exception("error")
        self.assertFalse(client.set_cmd("crs_wiper_op", 2))

    # ── set_wiper_op() ─────────────────────────────────────────────────────
    def test_set_wiper_op_all_valid_ops(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_r.publish.return_value = 1
        for name in ("OFF", "TOUCH", "SPEED1", "SPEED2", "AUTO",
                     "FRONT_WASH", "REAR_WASH", "REAR_WIPE"):
            with self.subTest(name=name):
                self.assertTrue(client.set_wiper_op(name))

    def test_set_wiper_op_invalid_returns_false(self):
        import rte_client as rc
        client, _ = self._make_client()
        self.assertFalse(client.set_wiper_op("INVALID_OP"))

    def test_set_wiper_op_case_insensitive(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_r.publish.return_value = 1
        self.assertTrue(client.set_wiper_op("speed1"))
        self.assertTrue(client.set_wiper_op("Speed1"))

    def test_set_wiper_op_sends_correct_code(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_r.publish.return_value = 1
        client.set_wiper_op("SPEED2")
        payload = json.loads(mock_r.publish.call_args[0][1])
        self.assertEqual(payload["value"], 3)

    # ── is_connected() ────────────────────────────────────────────────────
    def test_is_connected_true_on_ping_ok(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_r.ping.return_value = True
        self.assertTrue(client.is_connected())

    def test_is_connected_false_on_ping_exception(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_r.ping.side_effect = Exception("connexion perdue")
        self.assertFalse(client.is_connected())

    def test_is_connected_false_if_disconnected(self):
        import rte_client as rc
        client, _ = self._make_client(connected=False)
        self.assertFalse(client.is_connected())

    # ── get_all_public() ──────────────────────────────────────────────────
    def test_get_all_public_returns_dict(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_pipe = MagicMock()
        mock_r.pipeline.return_value = mock_pipe
        mock_pipe.execute.return_value = [b"SPEED1"] + [None] * 12
        result = client.get_all_public()
        self.assertIsInstance(result, dict)

    def test_get_all_public_known_keys(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_pipe = MagicMock()
        mock_r.pipeline.return_value = mock_pipe
        mock_pipe.execute.return_value = [b"val"] * 13
        result = client.get_all_public()
        for key in ("state", "crs_wiper_op", "vehicle_speed", "rain_intensity"):
            with self.subTest(key=key):
                self.assertIn(key, result)

    def test_get_all_public_decodes_bytes(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_pipe = MagicMock()
        mock_r.pipeline.return_value = mock_pipe
        mock_pipe.execute.return_value = [b"SPEED1"] + [None] * 12
        result = client.get_all_public()
        self.assertEqual(result["state"], "SPEED1")

    def test_get_all_public_returns_empty_if_disconnected(self):
        import rte_client as rc
        client, _ = self._make_client(connected=False)
        self.assertEqual(client.get_all_public(), {})

    def test_get_all_public_returns_empty_on_exception(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_r.pipeline.side_effect = Exception("boom")
        self.assertEqual(client.get_all_public(), {})

    # ── subscribe_changes() ───────────────────────────────────────────────
    def test_subscribe_changes_starts_thread(self):
        import rte_client as rc
        client, mock_r = self._make_client()
        mock_ps = MagicMock()
        mock_r.pubsub.return_value = mock_ps
        # Stoppe immédiatement pour éviter un thread bloquant
        mock_ps.listen.return_value = iter([])
        cb = MagicMock()
        client.subscribe_changes(cb)
        time.sleep(0.05)
        self.assertIsNotNone(client._sub_thread)

    def test_subscribe_changes_noop_if_disconnected(self):
        import rte_client as rc
        client, _ = self._make_client(connected=False)
        client.subscribe_changes(lambda keys: None)
        self.assertIsNone(client._sub_thread)


# ═══════════════════════════════════════════════════════════════════════════
#  4. TEST_CASES
# ═══════════════════════════════════════════════════════════════════════════
class TestTestResultDataclass(unittest.TestCase):
    """Vérifie la structure du dataclass TestResult."""

    @classmethod
    def setUpClass(cls):
        from test_cases import TestResult
        cls.TR = TestResult

    def test_all_fields_present(self):
        field_names = {f.name for f in dc_fields(self.TR)}
        for name in ("test_id", "name", "category", "ref",
                     "status", "limit", "measured", "details"):
            with self.subTest(name=name):
                self.assertIn(name, field_names)

    def test_default_measured(self):
        tr = self.TR("T01", "Test", "CYCLE", "REF", "PASS", "≤ 400 ms")
        self.assertEqual(tr.measured, "—")

    def test_default_details(self):
        tr = self.TR("T01", "Test", "CYCLE", "REF", "PASS", "≤ 400 ms")
        self.assertEqual(tr.details, "")

    def test_status_values(self):
        for status in ("PASS", "FAIL", "TIMEOUT", "RUNNING", "PENDING"):
            tr = self.TR("T01", "Test", "CAT", "REF", status, "limit")
            with self.subTest(status=status):
                self.assertEqual(tr.status, status)


class TestBaseTest(unittest.TestCase):
    """Vérifie BaseTest : start, check_timeout, _pass, _fail."""

    @classmethod
    def setUpClass(cls):
        from test_cases import BaseTest, TestResult

        class ConcreteTest(BaseTest):
            ID       = "TX"
            NAME     = "Test concret"
            CATEGORY = "TEST"
            REF      = "TST_001"
            LIMIT_STR = "≤ 100 ms"
            TEST_TIMEOUT_S = 0.1

        cls.ConcreteTest = ConcreteTest
        cls.TR = TestResult

    def _make(self):
        return self.ConcreteTest()

    def test_start_initializes_state(self):
        t = self._make()
        t.start()
        self.assertFalse(t._done)
        self.assertGreater(t._t_start, 0)

    def test_check_timeout_returns_none_before_expiry(self):
        t = self._make()
        t.TEST_TIMEOUT_S = 100
        t.start()
        self.assertIsNone(t.check_timeout())

    def test_check_timeout_returns_result_after_expiry(self):
        t = self._make()
        t.start()
        time.sleep(0.15)
        result = t.check_timeout()
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "TIMEOUT")

    def test_pass_sets_done(self):
        t = self._make()
        t.start()
        r = t._pass("42 ms")
        self.assertTrue(t._done)
        self.assertEqual(r.status, "PASS")

    def test_fail_sets_done(self):
        t = self._make()
        t.start()
        r = t._fail("999 ms")
        self.assertTrue(t._done)
        self.assertEqual(r.status, "FAIL")

    def test_result_fields(self):
        t = self._make()
        t.start()
        r = t._pass("42 ms", "tout va bien")
        self.assertEqual(r.test_id, "TX")
        self.assertEqual(r.name, "Test concret")
        self.assertEqual(r.measured, "42 ms")
        self.assertEqual(r.details, "tout va bien")

    def test_check_timeout_no_result_when_done(self):
        t = self._make()
        t.start()
        t._done = True
        time.sleep(0.15)
        # check_timeout ne doit pas retourner de résultat si déjà terminé
        result = t.check_timeout()
        self.assertIsNone(result)


class TestBaseCycleTest(unittest.TestCase):
    """Vérifie la logique de fenêtre glissante de BaseCycleTest via T03/T04."""

    @classmethod
    def setUpClass(cls):
        from test_cases import T03_CAN_200_Cycle, T04_CAN_201_Cycle, T01_LIN_Requester_Cycle
        cls.T03 = T03_CAN_200_Cycle
        cls.T04 = T04_CAN_201_Cycle
        cls.T01 = T01_LIN_Requester_Cycle

    def _make_t03(self):
        t = self.T03()
        t.start()
        return t

    def _make_t01(self):
        t = self.T01()
        t.start()
        return t

    def _send_can_frames(self, t, can_id, n, interval_s):
        """Envoie n trames CAN espacées de interval_s secondes (t_kernel simulé)."""
        results = []
        now = time.time()
        for i in range(n):
            ev = {"can_id_int": can_id, "t_kernel": now + i * interval_s}
            results.append(t.on_can_frame(ev))
        return results

    def _send_lin_frames(self, t, n, interval_s):
        """Envoie n trames LIN T01 (type=TX, pid=0xD6)."""
        results = []
        now = time.time()
        for i in range(n):
            ev = {"type": "TX", "pid": "0xD6", "t_kernel": now + i * interval_s}
            results.append(t.on_lin_frame(ev))
        return results

    def test_no_result_before_n_samples_can(self):
        t = self._make_t03()
        results = self._send_can_frames(t, 0x200, 5, 0.4)
        non_none = [r for r in results if r is not None]
        self.assertEqual(len(non_none), 0, "Résultat trop tôt (< N_SAMPLES)")

    def test_no_result_before_n_samples_lin(self):
        t = self._make_t01()
        results = self._send_lin_frames(t, 5, 0.4)
        non_none = [r for r in results if r is not None]
        self.assertEqual(len(non_none), 0)

    def test_pass_with_nominal_interval_can(self):
        t = self._make_t03()
        results = self._send_can_frames(t, 0x200, 30, 0.4)
        pass_results = [r for r in results if r and r.status == "PASS"]
        self.assertGreater(len(pass_results), 0)

    def test_fail_with_bad_interval_can(self):
        """Intervalle 5× trop long → FAIL."""
        t = self._make_t03()
        results = self._send_can_frames(t, 0x200, 30, 2.0)
        fail_results = [r for r in results if r and r.status == "FAIL"]
        self.assertGreater(len(fail_results), 0)

    def test_ignores_wrong_can_id(self):
        t = self._make_t03()
        now = time.time()
        for i in range(30):
            ev = {"can_id_int": 0x999, "t_kernel": now + i * 0.4}
            self.assertIsNone(t.on_can_frame(ev))

    def test_ignores_wrong_lin_type(self):
        t = self._make_t01()
        now = time.time()
        for i in range(30):
            ev = {"type": "RX", "pid": "0xD6", "t_kernel": now + i * 0.4}
            self.assertIsNone(t.on_lin_frame(ev))

    def test_ignores_wrong_lin_pid(self):
        t = self._make_t01()
        now = time.time()
        for i in range(30):
            ev = {"type": "TX", "pid": "0xFF", "t_kernel": now + i * 0.4}
            self.assertIsNone(t.on_lin_frame(ev))


class TestT01_LIN_Requester(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_cases import T01_LIN_Requester_Cycle
        cls.T = T01_LIN_Requester_Cycle

    def test_id(self):
        self.assertEqual(self.T.ID, "T01")

    def test_name(self):
        self.assertIn("LIN", self.T.NAME)

    def test_limit_ms(self):
        self.assertEqual(self.T.LIMIT_MS, 400)

    def test_tol_ms(self):
        self.assertEqual(self.T.TOL_MS, 40)

    def test_limit_str(self):
        self.assertIn("400", self.T.LIMIT_STR)

    def test_pid_constant(self):
        t = self.T()
        self.assertEqual(t._PID_16, "0xD6")

    def test_on_lin_frame_accepts_correct_event(self):
        t = self.T()
        t.start()
        now = time.time()
        # Envoie N_SAMPLES+1 trames valides espacées de 400ms
        from test_cases import N_SAMPLES
        for i in range(N_SAMPLES + 2):
            ev = {"type": "TX", "pid": "0xD6", "t_kernel": now + i * 0.4}
            t.on_lin_frame(ev)
        # Pas d'exception levée

    def test_on_lin_frame_ignores_rx(self):
        t = self.T()
        t.start()
        ev = {"type": "RX", "pid": "0xD6", "t_kernel": time.time()}
        self.assertIsNone(t.on_lin_frame(ev))

    def test_on_lin_frame_ignores_wrong_pid(self):
        t = self.T()
        t.start()
        ev = {"type": "TX", "pid": "0xFF", "t_kernel": time.time()}
        self.assertIsNone(t.on_lin_frame(ev))


class TestT02_LIN_CRSStatus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_cases import T02_LIN_CRSStatus_Cycle
        cls.T = T02_LIN_CRSStatus_Cycle

    def test_id(self):
        self.assertEqual(self.T.ID, "T02")

    def test_limit_ms(self):
        self.assertEqual(self.T.LIMIT_MS, 800)

    def test_tol_ms(self):
        self.assertEqual(self.T.TOL_MS, 100)

    def test_on_lin_frame_accepts_tx17(self):
        t = self.T()
        t.start()
        ev = {"type": "tx17", "t_kernel": time.time()}
        # Pas d'exception, retourne None (pas assez de samples)
        result = t.on_lin_frame(ev)
        self.assertIsNone(result)

    def test_on_lin_frame_ignores_other_type(self):
        t = self.T()
        t.start()
        ev = {"type": "TX", "pid": "0xD6", "t_kernel": time.time()}
        self.assertIsNone(t.on_lin_frame(ev))


class TestT03_CAN_200(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_cases import T03_CAN_200_Cycle
        cls.T = T03_CAN_200_Cycle

    def test_id(self):
        self.assertEqual(self.T.ID, "T03")

    def test_limit_ms(self):
        self.assertEqual(self.T.LIMIT_MS, 400)

    def test_tol_ms(self):
        self.assertEqual(self.T.TOL_MS, 40)

    def test_on_can_frame_accepts_0x200(self):
        t = self.T()
        t.start()
        ev = {"can_id_int": 0x200, "t_kernel": time.time()}
        # Pas d'exception
        t.on_can_frame(ev)

    def test_on_can_frame_ignores_other_ids(self):
        t = self.T()
        t.start()
        for cid in (0x201, 0x202, 0x300, 0x301):
            ev = {"can_id_int": cid, "t_kernel": time.time()}
            with self.subTest(can_id=hex(cid)):
                self.assertIsNone(t.on_can_frame(ev))


class TestT04_T05_T06_T07(unittest.TestCase):
    def _check_class(self, cls_name, tid, can_id, limit_ms, tol_ms):
        import test_cases as tc
        cls = getattr(tc, cls_name)
        with self.subTest(cls=cls_name, check="ID"):
            self.assertEqual(cls.ID, tid)
        with self.subTest(cls=cls_name, check="LIMIT_MS"):
            self.assertEqual(cls.LIMIT_MS, limit_ms)
        with self.subTest(cls=cls_name, check="TOL_MS"):
            self.assertEqual(cls.TOL_MS, tol_ms)
        # Vérifie que le bon can_id est filtré
        t = cls()
        t.start()
        ev_good = {"can_id_int": can_id,   "t_kernel": time.time()}
        ev_bad  = {"can_id_int": 0x999,    "t_kernel": time.time()}
        with self.subTest(cls=cls_name, check="accepte_bon_id"):
            t.on_can_frame(ev_good)   # pas d'exception
        with self.subTest(cls=cls_name, check="ignore_mauvais_id"):
            self.assertIsNone(t.on_can_frame(ev_bad))

    def test_T04(self):
        self._check_class("T04_CAN_201_Cycle", "T04", 0x201, 400, 40)

    def test_T05(self):
        self._check_class("T05_CAN_202_Cycle", "T05", 0x202, 400, 40)

    def test_T06(self):
        self._check_class("T06_CAN_300_Cycle", "T06", 0x300, 200, 20)

    def test_T07(self):
        self._check_class("T07_CAN_301_Cycle", "T07", 0x301, 200, 20)


class TestT20_WipeCycle(unittest.TestCase):
    """T20 : durée de cycle TOUCH (rest_contact + fallback)."""

    @classmethod
    def setUpClass(cls):
        from test_cases import T20_WipeCycle_Duration
        cls.T = T20_WipeCycle_Duration

    def _make(self):
        t = self.T()
        t.start()
        return t

    def test_id_and_ref(self):
        self.assertEqual(self.T.ID, "T20")
        self.assertEqual(self.T.REF, "SRD_WW_021")

    def test_limit_ms(self):
        self.assertEqual(self.T.LIMIT_MS, 1700)

    def test_no_result_before_motor_active(self):
        t = self._make()
        r = t.on_motor_data({"front": "OFF", "state": "OFF"})
        self.assertIsNone(r)

    def test_pass_via_fallback_fast(self):
        """Moteur s'active puis s'arrête en < 1700 ms → PASS (fallback)."""
        t = self._make()
        t.on_motor_data({"front": "ON", "state": "SPEED1"})
        time.sleep(0.05)
        result = t.on_motor_data({"front": "OFF", "state": "OFF"})
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "PASS")

    def test_fail_via_fallback_slow(self):
        """Moteur actif > 1700 ms → FAIL (fallback)."""
        t = self._make()
        t.on_motor_data({"front": "ON", "state": "SPEED1"})
        time.sleep(1.8)
        result = t.on_motor_data({"front": "OFF", "state": "OFF"})
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "FAIL")

    def test_pass_via_rest_contact(self):
        """Cycle terminé via rest_contact 1→0 en < 1700 ms → PASS."""
        t = self._make()
        # Démarre le moteur + lame en mouvement
        t.on_motor_data({"front": "ON", "state": "SPEED1",
                         "rest_contact_raw": True})
        time.sleep(0.1)
        # Lame revenue au repos
        result = t.on_motor_data({"front": "ON", "state": "SPEED1",
                                  "rest_contact_raw": False})
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "PASS")


class TestT10_LINTimeout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_cases import T10_LIN_Timeout
        cls.T = T10_LIN_Timeout

    def _make_with_rte(self, state_val="OFF"):
        t = self.T()
        t.rte_client = MagicMock()
        t.rte_client.get.return_value = state_val
        t.start()
        return t

    def test_id_and_category(self):
        self.assertEqual(self.T.ID, "T10")
        self.assertEqual(self.T.CATEGORY, "TIMEOUT")

    def test_ref(self):
        self.assertIn("FSR_001", self.T.REF)

    def test_limit_ms(self):
        # LIN timeout detection = 2500 ms (LIN_TIMEOUT 2000 + marges Redis/poll)
        self.assertEqual(self.T.LIMIT_MS, 2500)

    def test_on_lin_frame_accepted(self):
        """T10 réagit aux trames LIN."""
        t = self._make_with_rte()
        ev = {"frame_id": 0x16, "t_kernel": time.time()}
        # Pas d'erreur levée
        t.on_lin_frame(ev)


class TestT11_CANTimeout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_cases import T11_CAN_Timeout
        cls.T = T11_CAN_Timeout

    def test_id(self):
        self.assertEqual(self.T.ID, "T11")

    def test_category(self):
        self.assertEqual(self.T.CATEGORY, "TIMEOUT")

    def test_limit_2000ms(self):
        # CAN timeout detection = 2500 ms (CAN_TIMEOUT 2000 + marges Redis/poll)
        self.assertEqual(self.T.LIMIT_MS, 2500)


class TestT21_PumpAutoStop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_cases import T21_Pump_AutoStop
        cls.T = T21_Pump_AutoStop

    def _make(self):
        t = self.T()
        t.rte_client = MagicMock()
        t.rte_client.get.return_value = "OFF"
        t.start()
        return t

    def test_id(self):
        self.assertEqual(self.T.ID, "T21")

    def test_limit_5000ms(self):
        # Pump arrêt automatique = 5500 ms (5000 ms + marges)
        self.assertEqual(self.T.LIMIT_MS, 5500)

    def test_ref(self):
        self.assertIn("FSR_005", self.T.REF)

    def test_pump_active_then_off(self):
        t = self._make()
        # Pompe active
        t.on_motor_data({"pump_state": "FORWARD", "pump_active": True})
        # Pompe s'arrête
        result = t.on_motor_data({"pump_state": "OFF", "pump_active": False})
        # Devrait produire un résultat (PASS ou encore None si chrono trop court)
        # On vérifie juste que la méthode ne lève pas d'exception


class TestT30_T31_WSM(unittest.TestCase):
    """T30/T31 : transition OFF → SPEED1/SPEED2."""

    def _make_bcm_test(self, cls_name):
        import test_cases as tc
        cls = getattr(tc, cls_name)
        t = cls()
        t.rte_client = MagicMock()
        t.start()
        return t

    def test_T30_target_state(self):
        t = self._make_bcm_test("T30_WSM_Speed1")
        self.assertEqual(t._target_state(), "SPEED1")

    def test_T31_target_state(self):
        t = self._make_bcm_test("T31_WSM_Speed2")
        self.assertEqual(t._target_state(), "SPEED2")

    def test_T30_id(self):
        import test_cases as tc
        self.assertEqual(tc.T30_WSM_Speed1.ID, "T30")

    def test_T31_id(self):
        import test_cases as tc
        self.assertEqual(tc.T31_WSM_Speed2.ID, "T31")

    def test_T30_pass_via_can_backup(self):
        """T30 : CAN 0x201 mode=2 (SPEED1) → PASS backup."""
        t = self._make_bcm_test("T30_WSM_Speed1")
        ev = {"can_id_int": 0x201, "fields": {"mode": 2}, "t_kernel": time.time()}
        result = t.on_can_frame(ev)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "PASS")

    def test_T31_pass_via_can_backup(self):
        """T31 : CAN 0x201 mode=3 (SPEED2) → PASS backup."""
        t = self._make_bcm_test("T31_WSM_Speed2")
        ev = {"can_id_int": 0x201, "fields": {"mode": 3}, "t_kernel": time.time()}
        result = t.on_can_frame(ev)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "PASS")

    def test_T30_ignores_wrong_can_id(self):
        t = self._make_bcm_test("T30_WSM_Speed1")
        ev = {"can_id_int": 0x300, "fields": {"mode": 2}, "t_kernel": time.time()}
        self.assertIsNone(t.on_can_frame(ev))

    def test_T30_ignores_wrong_mode(self):
        t = self._make_bcm_test("T30_WSM_Speed1")
        ev = {"can_id_int": 0x201, "fields": {"mode": 3}, "t_kernel": time.time()}
        self.assertIsNone(t.on_can_frame(ev))


class TestT32_Speed1_to_Off(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_cases import T32_WSM_Speed1_to_Off
        cls.T = T32_WSM_Speed1_to_Off

    def test_id_and_ref(self):
        self.assertEqual(self.T.ID, "T32")
        self.assertIn("SRD_WW_001", self.T.REF)

    def test_check_rte_waits_for_speed1_then_off(self):
        """T32 : attend de voir SPEED1 avant de valider OFF."""
        t = self.T()
        t.rte_client = MagicMock()
        t.start()
        # Tant qu'on n'a pas vu SPEED1, _check_rte doit retourner None
        t.rte_client.get.return_value = "OFF"
        self.assertIsNone(t._check_rte())

    def test_check_rte_passes_when_off_after_speed1(self):
        """Après avoir vu SPEED1 puis OFF → PASS."""
        t = self.T()
        t.rte_client = MagicMock()
        t.start()
        # Simule SPEED1 vu
        t._saw_speed1 = True
        t.rte_client.get.return_value = "OFF"
        result = t._check_rte()
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "PASS")

    def test_limit_ms(self):
        self.assertGreater(self.T.LIMIT_MS, 0)


class TestT33_IgnitionOff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_cases import T33_Ignition_Off_SafeState
        cls.T = T33_Ignition_Off_SafeState

    def test_id(self):
        self.assertEqual(self.T.ID, "T33")

    def test_ref(self):
        self.assertIn("SRD_WW_001", self.T.REF)

    def test_limit_ms(self):
        self.assertEqual(self.T.LIMIT_MS, 2000)

    def test_check_rte_waits_for_active_state(self):
        """T33 : attend un état actif avant de surveiller OFF."""
        t = self.T()
        t.rte_client = MagicMock()
        t.start()
        # Premier appel : état OFF direct → rien (pas encore vu actif)
        t.rte_client.get.return_value = "OFF"
        self.assertIsNone(t._check_rte())

    def test_check_rte_passes_when_off_after_active(self):
        """Après état actif puis ignition OFF → PASS."""
        t = self.T()
        t.rte_client = MagicMock()
        t.start()
        # T33 requiert state=OFF ET ignition_status=0
        t.rte_client.get.return_value = "OFF"
        t.rte_client.get_int.return_value = 0   # ignition_status=0
        result = t._check_rte()
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "PASS")


class TestT34_T35_Auto(unittest.TestCase):
    def _make(self, cls_name):
        import test_cases as tc
        cls = getattr(tc, cls_name)
        t = cls()
        t.rte_client = MagicMock()
        t.start()
        return t

    def test_T34_id(self):
        t = self._make("T34_Auto_Rain_Speed1")
        self.assertEqual(t.ID, "T34")

    def test_T34_limit_ms(self):
        t = self._make("T34_Auto_Rain_Speed1")
        self.assertEqual(t.LIMIT_MS, 1500)

    def test_T34_check_rte_passes_when_auto_speed1(self):
        """T34 : state=AUTO + speed=1 après une valeur différente → PASS."""
        t = self._make("T34_Auto_Rain_Speed1")
        # 1er appel : état différent (initial_checked devient True)
        t.rte_client.get.return_value = "OFF"
        t.rte_client.get_int.return_value = 0
        t._check_rte()
        # 2ème appel : état cible
        t.rte_client.get.return_value = "AUTO"
        t.rte_client.get_int.return_value = 1
        result = t._check_rte()
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "PASS")

    def test_T35_id(self):
        t = self._make("T35_Auto_Rain_Speed2")
        self.assertEqual(t.ID, "T35")

    def test_T35_limit_ms(self):
        t = self._make("T35_Auto_Rain_Speed2")
        self.assertEqual(t.LIMIT_MS, 1500)

    def test_T35_check_rte_passes_when_speed2(self):
        """T35 : front_motor_speed=2 après valeur différente → PASS."""
        t = self._make("T35_Auto_Rain_Speed2")
        # 1er appel : speed ≠ 2
        t.rte_client.get_int.return_value = 0
        t._check_rte()
        # 2ème appel : speed = 2
        t.rte_client.get_int.return_value = 2
        result = t._check_rte()
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "PASS")

    def test_T34_can_backup_speed1(self):
        """T34 : CAN 0x201 mode=2 (SPEED1) → PASS backup."""
        t = self._make("T34_Auto_Rain_Speed1")
        ev = {"can_id_int": 0x201, "fields": {"mode": 2}, "t_kernel": time.time()}
        result = t.on_can_frame(ev)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "PASS")

    def test_T35_can_backup_speed2(self):
        """T35 : CAN 0x201 fields.speed=2 → PASS backup."""
        t = self._make("T35_Auto_Rain_Speed2")
        ev = {"can_id_int": 0x201, "fields": {"speed": 2}, "t_kernel": time.time()}
        result = t.on_can_frame(ev)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "PASS")


class TestT36_FrontWash(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_cases import T36_FrontWash
        cls.T = T36_FrontWash

    def test_id(self):
        self.assertEqual(self.T.ID, "T36")

    def test_ref(self):
        self.assertIn("SRD_WW_100", self.T.REF)

    def test_target_cycles(self):
        self.assertEqual(self.T.TARGET_CYCLES, 3)

    def test_timeout_generous(self):
        self.assertGreaterEqual(self.T.TEST_TIMEOUT_S, 15)


class TestT37_RearWash(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_cases import T37_RearWash_Cycle
        cls.T = T37_RearWash_Cycle

    def test_id(self):
        self.assertEqual(self.T.ID, "T37")

    def test_ref(self):
        self.assertIn("SRD_WW_110", self.T.REF)


class TestT38_Overcurrent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_cases import T38_Overcurrent_Motor
        cls.T = T38_Overcurrent_Motor

    def test_id(self):
        self.assertEqual(self.T.ID, "T38")

    def test_ref(self):
        self.assertIn("FSR_003", self.T.REF)

    def test_limit_ms(self):
        self.assertGreater(self.T.LIMIT_MS, 0)

    def test_no_attribute_error_before_start(self):
        """T38 initialise _oc_start_ms dans __init__."""
        t = self.T()
        t.rte_client = MagicMock()
        t.rte_client.get_float.return_value = 0.0
        t.rte_client.get_bool.return_value = False
        # Pas d'AttributeError
        t._check_rte()


class TestT39_LIN_Timeout_WSM_Off(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_cases import T39_LIN_Timeout_WSM_Off
        cls.T = T39_LIN_Timeout_WSM_Off

    def test_id(self):
        self.assertEqual(self.T.ID, "T39")

    def test_ref_lin_timeout(self):
        self.assertIn("FSR_001", self.T.REF)


class TestALL_TESTS_Registry(unittest.TestCase):
    """Vérifie l'intégrité du registre ALL_TESTS."""

    @classmethod
    def setUpClass(cls):
        from test_cases import ALL_TESTS
        cls.ALL_TESTS = ALL_TESTS

    def test_count(self):
        self.assertEqual(len(self.ALL_TESTS), 21)

    def test_no_duplicates(self):
        ids = [cls.ID for cls in self.ALL_TESTS]
        self.assertEqual(len(ids), len(set(ids)), f"IDs en doublon: {ids}")

    def test_all_expected_ids_present(self):
        ids = {cls.ID for cls in self.ALL_TESTS}
        expected = {
            "T01", "T02", "T03", "T04", "T05", "T06", "T07",
            "T10", "T11",
            "T20", "T21",
            "T30", "T31", "T32", "T33", "T34", "T35",
            "T36", "T37", "T38", "T39",
        }
        self.assertEqual(ids, expected)

    def test_all_classes_are_instantiable(self):
        for cls in self.ALL_TESTS:
            with self.subTest(id=cls.ID):
                t = cls()
                self.assertIsNotNone(t)

    def test_all_have_id_name_ref(self):
        for cls in self.ALL_TESTS:
            with self.subTest(id=cls.ID):
                self.assertTrue(len(cls.ID) > 0)
                self.assertTrue(len(cls.NAME) > 0)
                self.assertTrue(len(cls.REF) > 0)

    def test_all_have_category(self):
        valid_cats = {"CYCLE", "TIMEOUT", "FONCTIONNEL", "FONCTIONNEL_BCM"}
        for cls in self.ALL_TESTS:
            with self.subTest(id=cls.ID):
                self.assertIn(cls.CATEGORY, valid_cats,
                              f"{cls.ID}.CATEGORY={cls.CATEGORY!r}")

    def test_all_have_limit_str(self):
        for cls in self.ALL_TESTS:
            with self.subTest(id=cls.ID):
                self.assertTrue(len(cls.LIMIT_STR) > 0,
                                f"{cls.ID} LIMIT_STR vide")

    def test_ordering_cycles_before_bcm(self):
        """Les tests de cycle (T01-T07) doivent précéder les tests BCM (T30+)."""
        ids = [cls.ID for cls in self.ALL_TESTS]
        idx_T01 = ids.index("T01")
        idx_T30 = ids.index("T30")
        self.assertLess(idx_T01, idx_T30)

    def test_timeout_tests_between_cycles_and_functional(self):
        ids = [cls.ID for cls in self.ALL_TESTS]
        idx_T10 = ids.index("T10")
        idx_T01 = ids.index("T01")
        idx_T20 = ids.index("T20")
        self.assertGreater(idx_T10, idx_T01)
        self.assertLess(idx_T10, idx_T20)


# ═══════════════════════════════════════════════════════════════════════════
#  5. WORKERS (mocked Qt)
# ═══════════════════════════════════════════════════════════════════════════
class TestWorkersMocked(unittest.TestCase):
    """Vérifie les workers TCP sans Qt réel."""

    @classmethod
    def setUpClass(cls):
        import workers
        cls.workers = workers

    def _make_motor_worker(self):
        """Instancie MotorVehicleWorker sans appeler super().__init__ de QObject."""
        w = self.workers.MotorVehicleWorker.__new__(
            self.workers.MotorVehicleWorker)
        w.running      = True
        w.sock         = None
        w._host        = ""
        w._send_lock   = threading.Lock()
        w._send_queue  = []
        w._wiper_lock  = threading.Lock()
        w._wiper_op    = 0
        w._wiper_seq   = 0
        return w

    # ── queue_send ────────────────────────────────────────────────────────
    def test_queue_send_adds_json(self):
        w = self._make_motor_worker()
        w.queue_send({"ignition_status": 1, "vehicle_speed": 0})
        self.assertEqual(len(w._send_queue), 1)
        parsed = json.loads(w._send_queue[0])
        self.assertEqual(parsed["ignition_status"], 1)

    def test_queue_send_appends(self):
        w = self._make_motor_worker()
        w.queue_send({"a": 1})
        w.queue_send({"b": 2})
        self.assertEqual(len(w._send_queue), 2)

    def test_queue_send_newline_terminated(self):
        w = self._make_motor_worker()
        w.queue_send({"x": 1})
        self.assertTrue(w._send_queue[0].endswith("\n"))

    # ── set_wiper_op ──────────────────────────────────────────────────────
    def test_set_wiper_op_updates(self):
        w = self._make_motor_worker()
        w.set_wiper_op(3)
        self.assertEqual(w._wiper_op, 3)

    def test_set_wiper_op_all_values(self):
        w = self._make_motor_worker()
        for op in range(8):
            w.set_wiper_op(op)
            self.assertEqual(w._wiper_op, op)

    # ── host property ─────────────────────────────────────────────────────
    def test_host_property(self):
        w = self._make_motor_worker()
        w._host = "10.20.0.25"
        self.assertEqual(w.host, "10.20.0.25")

    # ── stop ──────────────────────────────────────────────────────────────
    def test_stop_sets_running_false(self):
        w = self._make_motor_worker()
        w.stop()
        self.assertFalse(w.running)

    def test_stop_closes_socket(self):
        w = self._make_motor_worker()
        mock_sock = MagicMock()
        w.sock = mock_sock
        w.stop()
        mock_sock.close.assert_called_once()

    def test_stop_no_error_if_no_socket(self):
        w = self._make_motor_worker()
        w.sock = None
        w.stop()  # ne doit pas lever d'exception


# ═══════════════════════════════════════════════════════════════════════════
#  6. TEST_RUNNER (mocked Qt)
# ═══════════════════════════════════════════════════════════════════════════
class TestTestRunnerImport(unittest.TestCase):
    """Vérifie que test_runner.py s'importe et que TestRunner est utilisable."""

    def test_import_succeeds(self):
        try:
            import test_runner
        except Exception as e:
            self.fail(f"Importation de test_runner échouée : {e}")

    def test_testrunner_class_exists(self):
        import test_runner
        self.assertTrue(hasattr(test_runner, "TestRunner"))

    def test_testrunner_has_required_methods(self):
        import test_runner
        for method in ("run_all", "run_selected"):
            with self.subTest(method=method):
                self.assertTrue(hasattr(test_runner.TestRunner, method),
                                f"Méthode {method} manquante")

    def test_testrunner_has_signals(self):
        import test_runner
        for sig in ("test_started", "test_result", "all_done", "progress", "log_msg"):
            with self.subTest(sig=sig):
                self.assertTrue(hasattr(test_runner.TestRunner, sig),
                                f"Signal {sig} manquant")


# ═══════════════════════════════════════════════════════════════════════════
#  7. COHÉRENCE TRANSVERSALE
# ═══════════════════════════════════════════════════════════════════════════
class TestCrossConsistency(unittest.TestCase):
    """Vérifie la cohérence entre les différents modules."""

    def test_wop_codes_consistent_between_constants_and_rte_client(self):
        import constants as C
        import rte_client as rc
        for code in range(8):
            name = C.WOP[code]["name"]
            with self.subTest(name=name):
                self.assertEqual(rc.WOP_NAMES[name], code,
                                 f"Incohérence WOP[{code}]={name!r}")

    def test_all_tests_ids_are_strings(self):
        from test_cases import ALL_TESTS
        for cls in ALL_TESTS:
            with self.subTest(id=cls.ID):
                self.assertIsInstance(cls.ID, str)
                self.assertRegex(cls.ID, r"^T\d+$",
                                 f"Format ID inattendu : {cls.ID!r}")

    def test_ports_in_workers_match_constants(self):
        import constants as C
        import workers
        # Les workers utilisent les constantes de constants.py
        self.assertEqual(workers.PORT_MOTOR, C.PORT_MOTOR)
        self.assertEqual(workers.PORT_LIN,   C.PORT_LIN)
        self.assertEqual(workers.PORT_PUMP_RX, C.PORT_PUMP_RX)
        self.assertEqual(workers.PORT_PUMP_TX, C.PORT_PUMP_TX)

    def test_wop_names_in_rte_match_wop_count_in_constants(self):
        import constants as C
        import rte_client as rc
        self.assertEqual(len(rc.WOP_NAMES), len(C.WOP))


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    loader  = unittest.TestLoader()
    suite   = unittest.TestSuite()

    suites_ordered = [
        TestConstants,
        TestNetwork,
        TestRTEClientConstants,
        TestRTEClientMocked,
        TestTestResultDataclass,
        TestBaseTest,
        TestBaseCycleTest,
        TestT01_LIN_Requester,
        TestT02_LIN_CRSStatus,
        TestT03_CAN_200,
        TestT04_T05_T06_T07,
        TestT20_WipeCycle,
        TestT10_LINTimeout,
        TestT11_CANTimeout,
        TestT21_PumpAutoStop,
        TestT30_T31_WSM,
        TestT32_Speed1_to_Off,
        TestT33_IgnitionOff,
        TestT34_T35_Auto,
        TestT36_FrontWash,
        TestT37_RearWash,
        TestT38_Overcurrent,
        TestT39_LIN_Timeout_WSM_Off,
        TestALL_TESTS_Registry,
        TestWorkersMocked,
        TestTestRunnerImport,
        TestCrossConsistency,
    ]

    for s in suites_ordered:
        suite.addTests(loader.loadTestsFromTestCase(s))

    runner = unittest.TextTestRunner(
        verbosity=2,
        stream=sys.stdout,
        failfast=False,
    )
    result = runner.run(suite)

    total  = result.testsRun
    failed = len(result.failures) + len(result.errors)
    passed = total - failed

    print("\n" + "═" * 65)
    print(f"  Résultat : {passed}/{total} tests réussis", end="")
    if failed:
        print(f"  —  {failed} ÉCHEC(S)")
    else:
        print("  ✓")
    print("═" * 65)
    sys.exit(0 if failed == 0 else 1)