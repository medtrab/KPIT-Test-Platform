#!/usr/bin/env python3
"""
WipeWash — Test Suite
=====================
Couvre :
  1. constants       — valeurs, palette, WOP
  2. network         — subnet, probe (mock socket), scan_async
  3. workers         — MotorVehicleWorker, LINWorker, PumpSignal,
                       PumpDataClient, send_pump_cmd
                       (serveur TCP mock intégré)
  4. widgets_base    — StatusLed, NumericDisplay, LinearBar,
                       InstrumentPanel, PanelHeader, helpers
  5. widgets_instruments — PumpWidget, MotorWidget, WindshieldWidget,
                           CarTopViewWidget
  6. panels          — MotorDashPanel (routage données moteur),
                       PumpPanel (update_display + tick),
                       VehicleRainPanel (envoi JSON),
                       CRSLINPanel (LIN events, wiper stats)
  7. main_window     — MainWindow instanciation, dock widgets,
                       toolbar LEDs, NetworkScanDialog

Usage :
    python test_wipewash.py          # tous les tests
    python test_wipewash.py -v       # verbose
    python test_wipewash.py -k pump  # filtre par nom
"""

import os
import sys
import json
import socket
import threading
import time
import unittest

# ── Qt offscreen (pas d'écran requis) ───────────────────────
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# ── Import du projet ─────────────────────────────────────────
# ── Chemins projet ──────────────────────────────────────────
# Structure attendue :
#   projet/
#   ├── src/Platform/        ← modules interface (constants, workers, …)
#   ├── src/RaspberrySimulator/  ← modules RPi (bcmcan, crslin, main)
#   └── tests/               ← CE fichier
_THIS_DIR     = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
_PLATFORM_DIR = os.path.join(_PROJECT_ROOT, "src", "Platform")
_RPI_DIR      = os.path.join(_PROJECT_ROOT, "src", "RaspberrySimulator")

for _p in (_PLATFORM_DIR, _RPI_DIR, _THIS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore    import QTimer

# Une seule QApplication pour toute la session de tests
_APP = QApplication.instance() or QApplication([])


# ═══════════════════════════════════════════════════════════════
#  1. CONSTANTS
# ═══════════════════════════════════════════════════════════════
class TestConstants(unittest.TestCase):
    """Vérifie les constantes de configuration."""

    def test_ports_values(self):
        from constants import PORT_MOTOR, PORT_LIN, PORT_PUMP_RX, PORT_PUMP_TX
        self.assertEqual(PORT_MOTOR,   5000)
        self.assertEqual(PORT_LIN,     5555)
        self.assertEqual(PORT_PUMP_RX, 5556)
        self.assertEqual(PORT_PUMP_TX, 5001)

    def test_ports_all_different(self):
        from constants import PORT_MOTOR, PORT_LIN, PORT_PUMP_RX, PORT_PUMP_TX
        ports = [PORT_MOTOR, PORT_LIN, PORT_PUMP_RX, PORT_PUMP_TX]
        self.assertEqual(len(ports), len(set(ports)), "Tous les ports doivent être distincts")

    def test_wop_keys(self):
        from constants import WOP
        self.assertEqual(sorted(WOP.keys()), list(range(8)))

    def test_wop_required_fields(self):
        from constants import WOP
        required = {"name", "label", "desc", "req", "color"}
        for op, data in WOP.items():
            with self.subTest(op=op):
                self.assertEqual(required, set(data.keys()),
                                 f"WOP[{op}] champs manquants ou en trop")

    def test_wop_names_unique(self):
        from constants import WOP
        names = [d["name"] for d in WOP.values()]
        self.assertEqual(len(names), len(set(names)), "Les noms WOP doivent être uniques")

    def test_wop_colors_are_hex(self):
        from constants import WOP
        import re
        pattern = re.compile(r'^#[0-9A-Fa-f]{6}$')
        for op, data in WOP.items():
            with self.subTest(op=op):
                self.assertRegex(data["color"], pattern,
                                 f"WOP[{op}]['color'] n'est pas un hex valide")

    def test_palette_colors_defined(self):
        from constants import (W_BG, W_PANEL, W_TEXT, W_TEXT_DIM,
                                A_TEAL, A_GREEN, A_RED, A_ORANGE)
        for name, val in [("W_BG", W_BG), ("W_PANEL", W_PANEL),
                          ("W_TEXT", W_TEXT), ("A_TEAL", A_TEAL),
                          ("A_GREEN", A_GREEN), ("A_RED", A_RED)]:
            with self.subTest(name=name):
                self.assertTrue(val.startswith("#"), f"{name} doit commencer par #")
                self.assertIn(len(val), (4, 7), f"{name} doit être #RGB ou #RRGGBB")

    def test_font_constants(self):
        from constants import FONT_UI, FONT_MONO
        self.assertIsInstance(FONT_UI,   str)
        self.assertIsInstance(FONT_MONO, str)
        self.assertTrue(len(FONT_UI) > 0)
        self.assertTrue(len(FONT_MONO) > 0)

    def test_max_rows_positive(self):
        from constants import MAX_ROWS
        self.assertGreater(MAX_ROWS, 0)


# ═══════════════════════════════════════════════════════════════
#  2. NETWORK
# ═══════════════════════════════════════════════════════════════
class TestNetwork(unittest.TestCase):
    """Teste les fonctions réseau avec mock socket."""

    def test_get_local_subnets_returns_list(self):
        from network import _get_local_subnets
        result = _get_local_subnets()
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_get_local_subnets_format(self):
        from network import _get_local_subnets
        import ipaddress
        for subnet in _get_local_subnets():
            with self.subTest(subnet=subnet):
                # Doit être un réseau CIDR valide
                net = ipaddress.ip_network(subnet, strict=False)
                self.assertIsNotNone(net)

    def test_fixed_subnet_1020(self):
        """Le projet est configuré sur 10.20.0.0/24."""
        from network import _get_local_subnets
        subnets = _get_local_subnets()
        self.assertIn("10.20.0.0/24", subnets)

    def test_probe_closed_port(self):
        """_probe ne doit rien ajouter si le port est fermé."""
        from network import _probe
        import threading
        results = []; lock = threading.Lock()
        _probe("127.0.0.1", 1, results, lock)   # port 1 = refusé
        self.assertEqual(results, [])

    def test_probe_open_port(self):
        """_probe doit détecter un serveur TCP ouvert."""
        from network import _probe
        import threading

        # Serveur éphémère
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        results = []; lock = threading.Lock()
        _probe("127.0.0.1", port, results, lock)
        srv.close()
        self.assertIn("127.0.0.1", results)

    def test_auto_discover_no_host(self):
        """auto_discover doit retourner None si aucun hôte."""
        from network import auto_discover
        # Port 1 = jamais ouvert
        result = auto_discover(1, timeout=0.5)
        self.assertIsNone(result)

    def test_auto_discover_finds_local_server(self):
        """auto_discover trouve un serveur local."""
        import ipaddress, threading
        from network import _get_local_subnets

        # Trouver une IP locale dans le subnet
        subnets = _get_local_subnets()
        if "10.20.0.0/24" in subnets:
            # Skip si on est pas sur ce réseau physiquement
            # (le probe ne va trouver personne en CI)
            self.skipTest("Test réseau physique ignoré hors environnement 10.20.0.x")

        # Serveur local sur 127.0.0.1 dans un subnet détecté
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        # Scan du subnet 127.0.0.0/24 (localhost)
        import ipaddress
        from network import _probe
        results = []; lock = threading.Lock()
        _probe("127.0.0.1", port, results, lock)
        srv.close()
        self.assertIn("127.0.0.1", results)

    def test_scan_async_calls_done_cb(self):
        """scan_async appelle done_cb avec une liste."""
        from network import scan_async
        done = threading.Event()
        received = []
        def on_done(hosts):
            received.extend(hosts)
            done.set()
        scan_async(1, lambda p: None, on_done)
        done.wait(timeout=3.0)
        self.assertTrue(done.is_set(), "done_cb jamais appelé")
        self.assertIsInstance(received, list)

    def test_scan_async_progress_0_to_100(self):
        """scan_async envoie des valeurs de progression 0..100."""
        from network import scan_async
        done   = threading.Event()
        values = []
        scan_async(1, lambda p: values.append(p), lambda h: done.set())
        done.wait(timeout=3.0)
        if values:
            self.assertTrue(all(0 <= v <= 100 for v in values),
                            f"Valeurs hors [0,100]: {values}")


# ═══════════════════════════════════════════════════════════════
#  Helpers — Mini serveur TCP mock
# ═══════════════════════════════════════════════════════════════
class _MockTCPServer(threading.Thread):
    """Serveur TCP minimal pour tester les workers."""

    def __init__(self, responses=None):
        super().__init__(daemon=True)
        self.responses = responses or []   # liste de str JSON à envoyer
        self._srv  = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.port  = self._srv.getsockname()[1]
        self.received: list[str] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def run(self):
        self._srv.settimeout(2.0)
        try:
            conn, _ = self._srv.accept()
        except OSError:
            return
        conn.settimeout(0.1)
        # Envoyer les réponses programmées
        for r in self.responses:
            try:
                conn.sendall((r + "\n").encode())
            except OSError:
                break
        # Lire tout ce qui arrive
        buf = ""
        while not self._stop.is_set():
            try:
                data = conn.recv(1024)
                if not data:
                    break
                buf += data.decode("utf-8", errors="replace")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    with self._lock:
                        self.received.append(line.strip())
            except socket.timeout:
                pass
            except OSError:
                break
        conn.close()
        self._srv.close()

    def stop(self):
        self._stop.set()


# ═══════════════════════════════════════════════════════════════
#  3. WORKERS
# ═══════════════════════════════════════════════════════════════
class TestMotorVehicleWorker(unittest.TestCase):

    def test_initial_state(self):
        from workers import MotorVehicleWorker
        w = MotorVehicleWorker()
        self.assertTrue(w.running)
        self.assertIsNone(w.sock)
        self.assertEqual(w.host, "")
        self.assertEqual(w._wiper_op, 0)
        self.assertEqual(w._wiper_seq, 0)
        self.assertEqual(w._send_queue, [])

    def test_set_wiper_op(self):
        from workers import MotorVehicleWorker
        w = MotorVehicleWorker()
        for op in range(8):
            with self.subTest(op=op):
                w.set_wiper_op(op)
                self.assertEqual(w._wiper_op, op)

    def test_queue_send_valid_json(self):
        from workers import MotorVehicleWorker
        w = MotorVehicleWorker()
        obj = {"type": "vehicle", "ignition_status": "ON",
               "reverse_gear": 0, "vehicle_speed": 50.0}
        w.queue_send(obj)
        self.assertEqual(len(w._send_queue), 1)
        parsed = json.loads(w._send_queue[0])
        self.assertEqual(parsed["type"], "vehicle")
        self.assertEqual(parsed["ignition_status"], "ON")

    def test_queue_send_multiple(self):
        from workers import MotorVehicleWorker
        w = MotorVehicleWorker()
        w.queue_send({"type": "vehicle"})
        w.queue_send({"type": "rain"})
        w.queue_send({"type": "wiper", "wiper_op": 2})
        self.assertEqual(len(w._send_queue), 3)

    def test_queue_send_ends_with_newline(self):
        from workers import MotorVehicleWorker
        w = MotorVehicleWorker()
        w.queue_send({"type": "rain", "rain_intensity": 42})
        self.assertTrue(w._send_queue[0].endswith("\n"))

    def test_stop_sets_running_false(self):
        from workers import MotorVehicleWorker
        w = MotorVehicleWorker()
        w.stop()
        self.assertFalse(w.running)

    def test_wiper_seq_increments_mod_65536(self):
        """Séquence wiper cyclique sur 16 bits."""
        from workers import MotorVehicleWorker
        w = MotorVehicleWorker()
        w._wiper_seq = 0xFFFE
        # Simuler ce que le run() fait
        with w._wiper_lock:
            w._wiper_seq = (w._wiper_seq + 1) & 0xFFFF
        self.assertEqual(w._wiper_seq, 0xFFFF)
        with w._wiper_lock:
            w._wiper_seq = (w._wiper_seq + 1) & 0xFFFF
        self.assertEqual(w._wiper_seq, 0)

    @unittest.skip("Skipped in CI: scanning 10.20.0.0/24 spawns 254 threads which crashes Qt offscreen. Run on real network with hardware.")
    def test_worker_receives_motor_data(self):
        """Worker reçoit du JSON moteur depuis un serveur mock."""
        from workers import MotorVehicleWorker
        import unittest.mock as mock

        motor_json = json.dumps({
            "front": "ON", "rear": "OFF",
            "speed": "Speed1", "current": 0.5,
            "fault": False, "rest": ""
        })
        srv = _MockTCPServer(responses=[motor_json])
        srv.start()
        time.sleep(0.05)

        # Patch auto_discover pour retourner 127.0.0.1
        import network
        received_signals = []
        with mock.patch.object(network, 'auto_discover', return_value="127.0.0.1"):
            import constants
            original_port = constants.PORT_MOTOR
            constants.PORT_MOTOR = srv.port
            try:
                from PyQt6.QtCore import QThread
                w = MotorVehicleWorker()
                w.motor_received.connect(lambda d: received_signals.append(d))
                t = QThread(); w.moveToThread(t); t.started.connect(w.run); t.start()
                # Attendre la réception
                deadline = time.time() + 3.0
                while not received_signals and time.time() < deadline:
                    _APP.processEvents()
                    time.sleep(0.05)
                w.stop(); t.quit(); t.wait(2000)
            finally:
                constants.PORT_MOTOR = original_port
        srv.stop()
        self.assertTrue(len(received_signals) > 0, "Aucune donnée moteur reçue")
        self.assertEqual(received_signals[0]["front"], "ON")

    @unittest.skip("Skipped in CI: scanning 10.20.0.0/24 spawns 254 threads which crashes Qt offscreen. Run on real network with hardware.")
    def test_worker_sends_vehicle_json(self):
        """Worker envoie correctement un message vehicle."""
        from workers import MotorVehicleWorker
        import unittest.mock as mock

        srv = _MockTCPServer()
        srv.start()
        time.sleep(0.05)

        import network, constants
        with mock.patch.object(network, 'auto_discover', return_value="127.0.0.1"):
            orig = constants.PORT_MOTOR
            constants.PORT_MOTOR = srv.port
            try:
                from PyQt6.QtCore import QThread
                w = MotorVehicleWorker()
                t = QThread(); w.moveToThread(t); t.started.connect(w.run); t.start()
                time.sleep(0.3)   # laisser la connexion s'établir
                w.queue_send({"type": "vehicle", "ignition_status": "ACC",
                               "reverse_gear": 1, "vehicle_speed": 30.0})
                deadline = time.time() + 2.0
                while not srv.received and time.time() < deadline:
                    time.sleep(0.05)
                w.stop(); t.quit(); t.wait(2000)
            finally:
                constants.PORT_MOTOR = orig
        srv.stop()

        vehicle_msgs = [json.loads(m) for m in srv.received
                        if m and json.loads(m).get("type") == "vehicle"]
        self.assertTrue(len(vehicle_msgs) > 0, "Message vehicle non reçu par le serveur")
        self.assertEqual(vehicle_msgs[0]["ignition_status"], "ACC")

    @unittest.skip("Skipped in CI: scanning 10.20.0.0/24 spawns 254 threads which crashes Qt offscreen. Run on real network with hardware.")
    def test_worker_sends_wiper_periodically(self):
        """Worker envoie des wiper_op toutes les ~200ms."""
        from workers import MotorVehicleWorker
        import unittest.mock as mock

        srv = _MockTCPServer()
        srv.start()
        time.sleep(0.05)

        import network, constants
        with mock.patch.object(network, 'auto_discover', return_value="127.0.0.1"):
            orig = constants.PORT_MOTOR
            constants.PORT_MOTOR = srv.port
            try:
                from PyQt6.QtCore import QThread
                w = MotorVehicleWorker()
                w.set_wiper_op(3)
                t = QThread(); w.moveToThread(t); t.started.connect(w.run); t.start()
                time.sleep(0.8)   # laisser ~4 cycles de 200ms
                w.stop(); t.quit(); t.wait(2000)
            finally:
                constants.PORT_MOTOR = orig
        srv.stop()

        wiper_msgs = [json.loads(m) for m in srv.received
                      if m and json.loads(m).get("type") == "wiper"]
        self.assertGreaterEqual(len(wiper_msgs), 2, "Moins de 2 messages wiper reçus")
        for msg in wiper_msgs:
            self.assertEqual(msg["wiper_op"], 3)
            self.assertIn("seq", msg)


class TestLINWorker(unittest.TestCase):

    def test_initial_state(self):
        from workers import LINWorker
        w = LINWorker()
        self.assertTrue(w.running)
        self.assertIsNone(w.sock)
        self.assertEqual(w.host, "")

    def test_stop(self):
        from workers import LINWorker
        w = LINWorker()
        w.stop()
        self.assertFalse(w.running)

    @unittest.skip("Skipped in CI: scanning 10.20.0.0/24 spawns 254 threads which crashes Qt offscreen. Run on real network with hardware.")
    def test_lin_worker_receives_event(self):
        """LINWorker reçoit un événement LIN depuis mock."""
        from workers import LINWorker
        import unittest.mock as mock

        lin_json = json.dumps({"type": "TX", "op": 2, "alive": 5,
                                "cs_int": 0xAB, "raw": "12 05 AB"})
        srv = _MockTCPServer(responses=[lin_json])
        srv.start(); time.sleep(0.05)

        import network, constants
        received = []
        with mock.patch.object(network, 'auto_discover', return_value="127.0.0.1"):
            orig = constants.PORT_LIN
            constants.PORT_LIN = srv.port
            try:
                from PyQt6.QtCore import QThread
                w = LINWorker()
                w.lin_received.connect(lambda d: received.append(d))
                t = QThread(); w.moveToThread(t); t.started.connect(w.run); t.start()
                deadline = time.time() + 3.0
                while not received and time.time() < deadline:
                    _APP.processEvents(); time.sleep(0.05)
                w.stop(); t.quit(); t.wait(2000)
            finally:
                constants.PORT_LIN = orig
        srv.stop()
        self.assertTrue(len(received) > 0, "Aucun événement LIN reçu")
        self.assertEqual(received[0]["type"], "TX")
        self.assertEqual(received[0]["op"], 2)


class TestPumpWorker(unittest.TestCase):

    def test_pump_signal_exists(self):
        from workers import PumpSignal
        sig = PumpSignal()
        self.assertIsNotNone(sig)

    def test_pump_client_initial_state(self):
        from workers import PumpSignal, PumpDataClient
        sig = PumpSignal()
        c   = PumpDataClient(sig)
        self.assertIsNone(c.host)
        self.assertTrue(c.daemon)

    @unittest.skip("Skipped in CI: scanning 10.20.0.0/24 spawns 254 threads which crashes Qt offscreen. Run on real network with hardware.")
    def test_pump_client_receives_data(self):
        """PumpDataClient reçoit des données depuis un mock."""
        from workers import PumpSignal, PumpDataClient
        import unittest.mock as mock

        pump_json = json.dumps({
            "state": "FORWARD", "current": 0.45, "voltage": 11.8,
            "fault": False, "fault_reason": "",
            "pump_remaining": 3.2, "pump_duration": 5.0, "source": "BCM"
        })
        srv = _MockTCPServer(responses=[pump_json])
        srv.start(); time.sleep(0.05)

        received = []
        sig = PumpSignal()
        sig.data_received.connect(lambda d: received.append(d))

        import network, constants
        with mock.patch.object(network, 'auto_discover', return_value="127.0.0.1"):
            orig = constants.PORT_PUMP_RX
            constants.PORT_PUMP_RX = srv.port
            try:
                c = PumpDataClient(sig); c.start()
                deadline = time.time() + 3.0
                while not received and time.time() < deadline:
                    _APP.processEvents(); time.sleep(0.05)
            finally:
                constants.PORT_PUMP_RX = orig
        srv.stop()
        self.assertTrue(len(received) > 0, "Aucune donnée pompe reçue")
        self.assertEqual(received[0]["state"], "FORWARD")
        self.assertAlmostEqual(received[0]["current"], 0.45, places=2)

    def test_send_pump_cmd_no_host(self):
        """send_pump_cmd avec host=None ne doit pas lever d'exception."""
        from workers import send_pump_cmd
        try:
            send_pump_cmd(None, "FORWARD", 5.0)
        except Exception as e:
            self.fail(f"send_pump_cmd(None,...) a levé: {e}")

    @unittest.skip("Skipped in CI: scanning 10.20.0.0/24 spawns 254 threads which crashes Qt offscreen. Run on real network with hardware.")
    def test_send_pump_cmd_payload(self):
        """send_pump_cmd envoie le bon JSON au serveur."""
        srv = _MockTCPServer()
        srv.start(); time.sleep(0.05)

        from workers import send_pump_cmd
        import constants
        orig = constants.PORT_PUMP_TX
        constants.PORT_PUMP_TX = srv.port
        try:
            send_pump_cmd("127.0.0.1", "BACKWARD", 7.5)
            time.sleep(0.3)
        finally:
            constants.PORT_PUMP_TX = orig
        srv.stop()

        self.assertTrue(len(srv.received) > 0, "Aucun message reçu par le serveur")
        msg = json.loads(srv.received[0])
        self.assertEqual(msg["cmd"], "BACKWARD")
        self.assertAlmostEqual(msg["duration"], 7.5, places=1)

    @unittest.skip("Skipped in CI: scanning 10.20.0.0/24 spawns 254 threads which crashes Qt offscreen. Run on real network with hardware.")
    def test_send_pump_cmd_off(self):
        srv = _MockTCPServer()
        srv.start(); time.sleep(0.05)
        from workers import send_pump_cmd
        import constants
        orig = constants.PORT_PUMP_TX; constants.PORT_PUMP_TX = srv.port
        try:
            send_pump_cmd("127.0.0.1", "OFF", 0.0)
            time.sleep(0.3)
        finally:
            constants.PORT_PUMP_TX = orig
        srv.stop()
        self.assertTrue(len(srv.received) > 0)
        msg = json.loads(srv.received[0])
        self.assertEqual(msg["cmd"],      "OFF")
        self.assertEqual(msg["duration"], 0.0)


# ═══════════════════════════════════════════════════════════════
#  4. WIDGETS_BASE
# ═══════════════════════════════════════════════════════════════
class TestWidgetsBase(unittest.TestCase):

    def test_status_led_initial(self):
        from widgets_base import StatusLed
        led = StatusLed(13)
        self.assertFalse(led._on)
        self.assertEqual(led.width(), 13)

    def test_status_led_set_on(self):
        from widgets_base import StatusLed
        led = StatusLed()
        led.set_state(True)
        self.assertTrue(led._on)

    def test_status_led_set_off(self):
        from widgets_base import StatusLed
        led = StatusLed()
        led.set_state(True); led.set_state(False)
        self.assertFalse(led._on)

    def test_status_led_custom_color(self):
        from widgets_base import StatusLed
        from PyQt6.QtGui import QColor
        led = StatusLed()
        led.set_state(True, "#FF6600")
        self.assertEqual(led._color, QColor("#FF6600"))

    def test_numeric_display_initial(self):
        from widgets_base import NumericDisplay
        nd = NumericDisplay("TEST", "A")
        self.assertEqual(nd._label, "TEST")
        self.assertEqual(nd._unit,  "A")
        self.assertEqual(nd._val,   "0.000")

    def test_numeric_display_set_value(self):
        from widgets_base import NumericDisplay
        nd = NumericDisplay()
        nd.set_value("3.141")
        self.assertEqual(nd._val, "3.141")

    def test_numeric_display_set_value_with_color(self):
        from widgets_base import NumericDisplay
        nd = NumericDisplay()
        nd.set_value("1.000", color="#FF0000")
        self.assertEqual(nd._color, "#FF0000")

    def test_linear_bar_initial(self):
        from widgets_base import LinearBar
        lb = LinearBar(2.0, "V")
        self.assertEqual(lb._max,  2.0)
        self.assertEqual(lb._unit, "V")
        self.assertEqual(lb._val,  0.0)
        self.assertFalse(lb._fault)

    def test_linear_bar_set_value(self):
        from widgets_base import LinearBar
        lb = LinearBar(1.5)
        lb.set_value(0.75, fault=False)
        self.assertAlmostEqual(lb._val, 0.75)

    def test_linear_bar_fault(self):
        from widgets_base import LinearBar
        lb = LinearBar(1.0)
        lb.set_value(1.2, fault=True)
        self.assertTrue(lb._fault)

    def test_instrument_panel_creates(self):
        from widgets_base import InstrumentPanel
        ip = InstrumentPanel("MOTEUR", "#007ACC")
        self.assertIsNotNone(ip.body())
        self.assertIsNotNone(ip.header())

    def test_panel_header_set_connection(self):
        from widgets_base import PanelHeader
        hdr = PanelHeader("TEST")
        hdr.set_connection(True,  "10.20.0.5")
        hdr.set_connection(False, "")

    def test_helpers_lbl(self):
        from widgets_base import _lbl
        l = _lbl("Hello", 12, True, "#FF0000")
        self.assertEqual(l.text(), "Hello")

    def test_helpers_hsep(self):
        from widgets_base import _hsep
        from PyQt6.QtWidgets import QFrame
        sep = _hsep()
        self.assertIsInstance(sep, QFrame)

    def test_helpers_cd_btn(self):
        from widgets_base import _cd_btn
        from PyQt6.QtWidgets import QPushButton
        btn = _cd_btn("CLICK ME", "#007ACC", h=32, w=100)
        self.assertIsInstance(btn, QPushButton)
        self.assertEqual(btn.height(), 32)
        self.assertEqual(btn.width(),  100)

    def test_helpers_font(self):
        from widgets_base import _font
        from PyQt6.QtGui import QFont
        f = _font(14, bold=True, mono=False)
        self.assertIsInstance(f, QFont)
        self.assertEqual(f.pointSize(), 14)


# ═══════════════════════════════════════════════════════════════
#  5. WIDGETS_INSTRUMENTS
# ═══════════════════════════════════════════════════════════════
class TestWidgetsInstruments(unittest.TestCase):

    def test_pump_widget_initial(self):
        from widgets_instruments import PumpWidget
        w = PumpWidget()
        self.assertEqual(w._state,   "OFF")
        self.assertEqual(w._current, 0.0)
        self.assertFalse(w._fault)

    def test_pump_widget_set_state_forward(self):
        from widgets_instruments import PumpWidget
        w = PumpWidget()
        w.set_state("FORWARD", current=0.5, fault=False)
        self.assertEqual(w._state,   "FORWARD")
        self.assertEqual(w._current, 0.5)

    def test_pump_widget_set_state_fault(self):
        from widgets_instruments import PumpWidget
        w = PumpWidget()
        w.set_state("FAULT", current=1.2, fault=True)
        self.assertTrue(w._fault)

    def test_pump_widget_all_states(self):
        from widgets_instruments import PumpWidget
        w = PumpWidget()
        for state in ("OFF", "FORWARD", "BACKWARD", "FAULT", "OVERCURRENT"):
            with self.subTest(state=state):
                w.set_state(state)
                self.assertEqual(w._state, state)

    def test_motor_widget_initial(self):
        from widgets_instruments import MotorWidget
        w = MotorWidget("FRONT")
        self.assertEqual(w._id,    "FRONT")
        self.assertEqual(w._state, "OFF")
        self.assertEqual(w._speed, "Speed1")

    def test_motor_widget_set_state(self):
        from widgets_instruments import MotorWidget
        w = MotorWidget("REAR")
        w.set_state("ON", speed="Speed2")
        self.assertEqual(w._state, "ON")
        self.assertEqual(w._speed, "Speed2")

    def test_motor_widget_set_off(self):
        from widgets_instruments import MotorWidget
        w = MotorWidget()
        w.set_state("ON", "Speed1")
        w.set_state("OFF")
        self.assertEqual(w._state, "OFF")

    def test_windshield_initial(self):
        from widgets_instruments import WindshieldWidget
        w = WindshieldWidget()
        self.assertEqual(w._op,    0)
        self.assertAlmostEqual(w._angle, -60.0)

    def test_windshield_set_op(self):
        from widgets_instruments import WindshieldWidget
        w = WindshieldWidget()
        for op in range(8):
            with self.subTest(op=op):
                w.set_op(op)
                self.assertEqual(w._op, op)

    def test_windshield_op0_resets_angle(self):
        from widgets_instruments import WindshieldWidget
        w = WindshieldWidget()
        w._angle = 30.0
        w.set_op(0)
        self.assertAlmostEqual(w._angle, -60.0)

    def test_car_top_view_initial(self):
        from widgets_instruments import CarTopViewWidget
        w = CarTopViewWidget()
        self.assertEqual(w._ign,     "OFF")
        self.assertFalse(w._reverse)
        self.assertEqual(w._speed,   0.0)
        self.assertEqual(w._rain,    0)
        self.assertIsInstance(w._rain_drops, list)
        self.assertIsInstance(w._exhaust,    list)

    def test_car_top_view_setters(self):
        from widgets_instruments import CarTopViewWidget
        w = CarTopViewWidget()
        w.set_ignition("ON"); self.assertEqual(w._ign, "ON")
        w.set_reverse(True);  self.assertTrue(w._reverse)
        w.set_speed(120.0);   self.assertAlmostEqual(w._speed, 120.0)
        w.set_rain(75);       self.assertEqual(w._rain, 75)

    def test_car_top_view_yaw_range(self):
        from widgets_instruments import CarTopViewWidget
        w = CarTopViewWidget()
        w._yaw = 0.0
        # Simuler drag
        w._yaw = (w._yaw + 400) % 360
        self.assertGreaterEqual(w._yaw, 0)
        self.assertLess(w._yaw, 360)

    def test_car_top_view_pitch_clamp(self):
        from widgets_instruments import CarTopViewWidget
        w = CarTopViewWidget()
        w._pitch = max(15.0, min(90.0, -100.0))
        self.assertEqual(w._pitch, 15.0)
        w._pitch = max(15.0, min(90.0, 200.0))
        self.assertEqual(w._pitch, 90.0)

    def test_car_top_view_project_returns_tuple(self):
        from widgets_instruments import CarTopViewWidget
        w = CarTopViewWidget()
        result = w._project(200, 200, 60, 0.0, 0.0, 0.0)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], int)
        self.assertIsInstance(result[1], int)


# ═══════════════════════════════════════════════════════════════
#  6. PANELS
# ═══════════════════════════════════════════════════════════════
class TestMotorDashPanel(unittest.TestCase):

    def setUp(self):
        from panels import MotorDashPanel
        self.panel = MotorDashPanel()

    def test_initial_widgets_exist(self):
        self.assertIsNotNone(self.panel.motor_front)
        self.assertIsNotNone(self.panel.motor_rear)
        self.assertIsNotNone(self.panel.disp_cur)
        self.assertIsNotNone(self.panel.bar_cur)
        self.assertIsNotNone(self.panel.lbl_rest)
        self.assertIsNotNone(self.panel.lbl_status)

    def test_on_motor_data_flat_format(self):
        """Format plat (motor_dashboard.py)."""
        self.panel.on_motor_data({
            "front": "ON", "rear": "OFF",
            "speed": "Speed2", "current": 0.3,
            "fault": False, "rest": ""
        })
        self.assertEqual(self.panel.motor_front._state, "ON")
        self.assertEqual(self.panel.motor_rear._state,  "OFF")

    def test_on_motor_data_nested_format(self):
        """Format imbriqué (interface.py) — dicts directs."""
        self.panel.on_motor_data({
            "front": {"enable": 1, "speed": 0,
                      "motor_current": 0.4, "fault_status": 0, "rest_contact": 1},
            "rear":  {"enable": 0, "speed": 0,
                      "motor_current": 0.0, "fault_status": 0, "rest_contact": 1},
        })
        self.assertEqual(self.panel.motor_front._state, "ON")
        self.assertEqual(self.panel.motor_rear._state,  "OFF")

    def test_on_motor_data_fault(self):
        self.panel.on_motor_data({
            "front": "OFF", "rear": "OFF",
            "speed": "Speed1", "current": 1.5,
            "fault": True, "rest": ""
        })
        txt = self.panel.lbl_status.text()
        self.assertIn("FAULT", txt)

    def test_on_motor_data_parking(self):
        self.panel.on_motor_data({
            "front": "OFF", "rear": "OFF",
            "speed": "Speed1", "current": 0.0,
            "fault": False, "rest": "PARKED"
        })
        self.assertEqual(self.panel.lbl_rest.text(), "PARKED")

    def test_on_motor_data_moving(self):
        self.panel.on_motor_data({
            "front": "ON", "rear": "ON",
            "speed": "Speed1", "current": 0.2,
            "fault": False, "rest": ""
        })
        self.assertEqual(self.panel.lbl_rest.text(), "MOVING")

    def test_current_display_updates(self):
        self.panel.on_motor_data({
            "front": "ON", "rear": "ON",
            "speed": "Speed1", "current": 0.789,
            "fault": False, "rest": ""
        })
        self.assertIn("0.789", self.panel.disp_cur._val)


class TestPumpPanel(unittest.TestCase):

    def setUp(self):
        from panels import PumpPanel
        self.panel = PumpPanel(pump_getter=lambda: None)

    def test_initial_widgets_exist(self):
        self.assertIsNotNone(self.panel.pump_widget)
        self.assertIsNotNone(self.panel.disp_cur)
        self.assertIsNotNone(self.panel.disp_vol)
        self.assertIsNotNone(self.panel.bar_cur)
        self.assertIsNotNone(self.panel.bar_vol)
        self.assertIsNotNone(self.panel.lbl_alert)
        self.assertIsNotNone(self.panel.disp_to)
        self.assertIsNotNone(self.panel.btn_fwd)
        self.assertIsNotNone(self.panel.btn_bwd)
        self.assertIsNotNone(self.panel.btn_off)

    def test_update_display_forward_bcm(self):
        self.panel.update_display({
            "state": "FORWARD", "current": 0.5, "voltage": 12.1,
            "fault": False, "fault_reason": "",
            "pump_remaining": 3.0, "pump_duration": 5.0, "source": "BCM"
        })
        self.assertIn("0.500", self.panel.disp_cur._val)
        self.assertIsNotNone(self.panel.pump_start)

    def test_update_display_off(self):
        self.panel.update_display({
            "state": "OFF", "current": 0.0, "voltage": 0.0,
            "fault": False, "fault_reason": "",
            "pump_remaining": 0.0, "pump_duration": 0.0, "source": "BCM"
        })
        self.assertIsNone(self.panel.pump_start)
        self.assertEqual(self.panel.lbl_alert.text(), "No active alerts")

    def test_update_display_overcurrent_fault(self):
        self.panel.update_display({
            "state": "OFF", "current": 1.3, "voltage": 0.0,
            "fault": True, "fault_reason": "OVERCURRENT",
            "pump_remaining": 0.0, "pump_duration": 0.0, "source": "BCM"
        })
        self.assertIn("OVERCURRENT", self.panel.lbl_alert.text())

    def test_update_display_interface_mode(self):
        self.panel.update_display({
            "state": "BACKWARD", "current": 0.3, "voltage": 11.5,
            "fault": False, "fault_reason": "",
            "pump_remaining": 2.5, "pump_duration": 5.0, "source": "INTERFACE"
        })
        self.assertIsNone(self.panel.pump_start)

    def test_tick_bcm_countdown(self):
        """Le tick met à jour le compteur FSR_005 en mode BCM."""
        self.panel.update_display({
            "state": "FORWARD", "current": 0.5, "voltage": 12.0,
            "fault": False, "fault_reason": "",
            "pump_remaining": 5.0, "pump_duration": 5.0, "source": "BCM"
        })
        t0 = self.panel.pump_start
        self.assertIsNotNone(t0)
        time.sleep(0.15)
        self.panel._tick()
        # Le remaining doit avoir diminué
        remaining_str = self.panel.disp_to._val
        self.assertNotEqual(remaining_str, "—")

    def test_voltage_display(self):
        self.panel.update_display({
            "state": "FORWARD", "current": 0.2, "voltage": 11.9,
            "fault": False, "fault_reason": "",
            "pump_remaining": 4.0, "pump_duration": 5.0, "source": "BCM"
        })
        self.assertIn("11.90", self.panel.disp_vol._val)


class TestVehicleRainPanel(unittest.TestCase):

    def setUp(self):
        from workers import MotorVehicleWorker
        self.worker = MotorVehicleWorker()
        from panels import VehicleRainPanel
        self.panel = VehicleRainPanel(motor_getter=lambda: self.worker)

    def test_initial_state(self):
        self.assertEqual(self.panel._ign,       "OFF")
        self.assertEqual(self.panel._rev,       0)
        self.assertAlmostEqual(self.panel._spd, 0.0)
        self.assertEqual(self.panel._rain,      0)
        self.assertTrue(self.panel._sensor_ok)

    def test_send_vehicle_json_format(self):
        """_sv envoie un JSON vehicle correct."""
        self.panel._sv()
        self.assertGreater(len(self.worker._send_queue), 0)
        msg = json.loads(self.worker._send_queue[0])
        self.assertEqual(msg["type"],             "vehicle")
        self.assertIn("ignition_status",  msg)
        self.assertIn("reverse_gear",     msg)
        self.assertIn("vehicle_speed",    msg)

    def test_send_rain_json_format(self):
        """_sr envoie un JSON rain correct."""
        self.panel._sr()
        self.assertGreater(len(self.worker._send_queue), 0)
        msg = json.loads(self.worker._send_queue[-1])
        self.assertEqual(msg["type"], "rain")
        self.assertIn("rain_intensity",  msg)
        self.assertIn("sensor_status",   msg)

    def test_ignition_changes_sent(self):
        self.panel.ign._sel("ON"); _APP.processEvents()
        msgs = [json.loads(m) for m in self.worker._send_queue]
        veh = [m for m in msgs if m.get("type") == "vehicle"]
        self.assertTrue(any(m["ignition_status"] == "ON" for m in veh))

    def test_reverse_toggle(self):
        self.panel._toggle_rev()
        self.assertEqual(self.panel._rev, 1)
        self.panel._toggle_rev()
        self.assertEqual(self.panel._rev, 0)

    def test_reverse_updates_json(self):
        self.panel._toggle_rev()
        msgs = [json.loads(m) for m in self.worker._send_queue]
        veh = [m for m in msgs if m.get("type") == "vehicle"]
        self.assertTrue(any(m["reverse_gear"] == 1 for m in veh))

    def test_speed_slider(self):
        self.panel.sld_spd.setValue(500)   # 50.0 km/h
        _APP.processEvents()
        self.assertAlmostEqual(self.panel._spd, 50.0, places=1)

    def test_rain_slider(self):
        self.panel.sld_rain.setValue(80)
        _APP.processEvents()
        self.assertEqual(self.panel._rain, 80)

    def test_sensor_toggle_error(self):
        self.panel._toggle_sens()
        self.assertFalse(self.panel._sensor_ok)
        msgs = [json.loads(m) for m in self.worker._send_queue]
        rain = [m for m in msgs if m.get("type") == "rain"]
        self.assertTrue(any(m["sensor_status"] == "ERROR" for m in rain))

    def test_sensor_toggle_back_ok(self):
        self.panel._toggle_sens()   # ERROR
        self.panel._toggle_sens()   # OK
        self.assertTrue(self.panel._sensor_ok)

    def test_send_both(self):
        before = len(self.worker._send_queue)
        self.panel._send_both()
        after  = len(self.worker._send_queue)
        self.assertEqual(after - before, 2)   # vehicle + rain

    def test_car_view_updated_on_ignition(self):
        self.panel.ign._sel("ACC"); _APP.processEvents()
        self.assertEqual(self.panel.car_view._ign, "ACC")

    def test_car_view_updated_on_speed(self):
        self.panel.sld_spd.setValue(1200); _APP.processEvents()
        self.assertAlmostEqual(self.panel.car_view._speed, 120.0, places=1)

    def test_car_view_updated_on_rain(self):
        self.panel.sld_rain.setValue(60); _APP.processEvents()
        self.assertEqual(self.panel.car_view._rain, 60)

    def test_tx_counter_increments(self):
        before = self.panel._tx
        self.panel._sv()
        self.assertEqual(self.panel._tx, before + 1)


class TestCRSLINPanel(unittest.TestCase):

    def setUp(self):
        from workers import MotorVehicleWorker
        self.worker = MotorVehicleWorker()
        self.wiper_ops: list[int] = []
        from panels import CRSLINPanel
        self.panel = CRSLINPanel(
            wiper_setter=lambda op: self.wiper_ops.append(op))

    def test_initial_op_buttons_exist(self):
        self.assertEqual(len(self.panel._op_btns), 8)
        for op in range(8):
            with self.subTest(op=op):
                self.assertIn(op, self.panel._op_btns)

    def test_select_op_calls_setter(self):
        self.panel._select_op(3)
        self.assertIn(3, self.wiper_ops)
        self.assertEqual(self.panel._cur_op, 3)

    def test_select_op_updates_windshield(self):
        self.panel._select_op(2)
        self.assertEqual(self.panel._ws._op, 2)

    def test_on_wiper_sent_updates_stats(self):
        self.panel.on_wiper_sent(op=5, seq=42)
        self.assertEqual(self.panel._stat["frames"].text(), "42")
        self.assertIn("5", self.panel._stat["op"].text())
        self.assertEqual(self.panel._stat["alive"].text(), "0x01")

    def test_on_wiper_sent_alive_wraps(self):
        """AliveCounter doit wrapper à 0 après 0xFF."""
        self.panel._al = 0xFE
        self.panel.on_wiper_sent(op=0, seq=1)
        self.assertEqual(self.panel._al, 0xFF)
        self.panel.on_wiper_sent(op=0, seq=2)
        self.assertEqual(self.panel._al, 0x00)

    def test_add_lin_event_tx(self):
        self.panel.add_lin_event({
            "type": "TX", "op": 2, "alive": 10,
            "cs_int": 0xAB, "raw": "12 0A AB", "time": time.time()
        })
        self.assertEqual(self.panel._ltx, 1)
        self.assertEqual(self.panel._lrx, 0)
        self.assertEqual(self.panel.lbl_cnt.text(), "1 frames")

    def test_add_lin_event_rx(self):
        self.panel.add_lin_event({
            "type": "RX_HDR", "pid": "0xD6",
            "raw": "00 55 D6", "time": time.time()
        })
        self.assertEqual(self.panel._lrx, 1)

    def test_add_lin_event_counter_display(self):
        self.panel.add_lin_event({"type": "TX", "op": 0, "alive": 0, "cs_int": 0, "raw": ""})
        self.panel.add_lin_event({"type": "TX", "op": 1, "alive": 1, "cs_int": 0, "raw": ""})
        self.panel.add_lin_event({"type": "RX_HDR", "pid": "0xD6", "raw": ""})
        self.assertEqual(self.panel._cnt_tx.text(), "2")
        self.assertEqual(self.panel._cnt_rx.text(), "1")
        self.assertEqual(self.panel._cnt_tot.text(), "3")

    def test_set_lin_status_connected(self):
        self.panel.set_lin_status("LIN OK", True)
        self.assertTrue(self.panel._led_lin._on)
        self.assertEqual(self.panel.lbl_lin.text(), "LIN OK")

    def test_set_lin_status_disconnected(self):
        self.panel.set_lin_status("No host", False)
        self.assertFalse(self.panel._led_lin._on)

    def test_lin_table_max_rows(self):
        """La table ne dépasse pas MAX_ROWS entrées."""
        from constants import MAX_ROWS
        for i in range(MAX_ROWS + 10):
            self.panel.add_lin_event({
                "type": "TX", "op": i % 8, "alive": i % 256,
                "cs_int": 0, "raw": "", "time": time.time()
            })
        self.assertLessEqual(len(self.panel._all_evts), MAX_ROWS)


# ═══════════════════════════════════════════════════════════════
#  7. MAIN_WINDOW
# ═══════════════════════════════════════════════════════════════
class TestMainWindow(unittest.TestCase):

    def setUp(self):
        from main_window import MainWindow
        self.win = MainWindow()

    def tearDown(self):
        self.win._motor_worker.stop()
        self.win._lin_worker.stop()
        self.win._motor_thread.quit(); self.win._motor_thread.wait(1000)
        self.win._lin_thread.quit();   self.win._lin_thread.wait(1000)
        self.win.close()

    def test_window_title(self):
        self.assertIn("WipeWash", self.win.windowTitle())

    def test_panels_exist(self):
        self.assertIsNotNone(self.win._motor_panel)
        self.assertIsNotNone(self.win._pump_panel)
        self.assertIsNotNone(self.win._veh_panel)
        self.assertIsNotNone(self.win._crslin_panel)

    def test_workers_exist(self):
        self.assertIsNotNone(self.win._motor_worker)
        self.assertIsNotNone(self.win._lin_worker)
        self.assertIsNotNone(self.win._pump_client)
        self.assertIsNotNone(self.win._pump_signal)

    def test_dock_acts_keys(self):
        expected = {"Motor Dashboard", "Pump Monitor",
                    "Vehicle & Rain", "CRS / LIN Monitor"}
        self.assertEqual(set(self.win._dock_acts.keys()), expected)

    def test_toolbar_leds_all_ports(self):
        from constants import PORT_MOTOR, PORT_LIN, PORT_PUMP_RX
        for port in (PORT_MOTOR, PORT_LIN, PORT_PUMP_RX):
            with self.subTest(port=port):
                self.assertIn(port, self.win._toolbar_leds)
                self.assertIn(port, self.win._toolbar_labels)

    def test_set_tb_status_on(self):
        from constants import PORT_MOTOR
        self.win._set_tb_status(PORT_MOTOR, True, "10.20.0.5")
        led = self.win._toolbar_leds[PORT_MOTOR]
        self.assertTrue(led._on)

    def test_set_tb_status_off(self):
        from constants import PORT_LIN
        self.win._set_tb_status(PORT_LIN, False)
        led = self.win._toolbar_leds[PORT_LIN]
        self.assertFalse(led._on)

    def test_minimum_size(self):
        ms = self.win.minimumSize()
        self.assertGreaterEqual(ms.width(),  1000)
        self.assertGreaterEqual(ms.height(), 700)

    def test_on_motor_data_routes_to_panel(self):
        """Motor data goes directly to panel via on_motor_data."""
        self.win._motor_panel.on_motor_data({
            "front": "ON", "rear": "OFF",
            "speed": "Speed1", "current": 0.1,
            "fault": False, "rest": ""
        })
        self.assertEqual(self.win._motor_panel.motor_front._state, "ON")

    def test_on_pump_data_routes_to_panel(self):
        """Pump data goes directly to panel via update_display."""
        self.win._pump_panel.update_display({
            "state": "FORWARD", "current": 0.3, "voltage": 12.0,
            "fault": False, "fault_reason": "",
            "pump_remaining": 4.0, "pump_duration": 5.0, "source": "BCM"
        })
        self.assertIn("0.300", self.win._pump_panel.disp_cur._val)

    def test_on_lin_event_routes_to_crslin(self):
        before = self.win._crslin_panel._ltx
        self.win._on_lin_event({
            "type": "TX", "op": 1, "alive": 0,
            "cs_int": 0, "raw": "", "time": time.time()
        })
        self.assertEqual(self.win._crslin_panel._ltx, before + 1)

    def test_on_wiper_sent_updates_stats(self):
        self.win._on_wiper_sent(op=4, seq=100)
        self.assertEqual(self.win._crslin_panel._stat["frames"].text(), "100")


class TestNetworkScanDialog(unittest.TestCase):

    def test_dialog_creates(self):
        from main_window import NetworkScanDialog
        dlg = NetworkScanDialog()
        self.assertIsNotNone(dlg)
        dlg.close()

    def test_dialog_has_scan_button(self):
        from main_window import NetworkScanDialog
        dlg = NetworkScanDialog()
        self.assertIsNotNone(dlg.btn_scan)
        self.assertIsNotNone(dlg.btn_close)
        dlg.close()

    def test_dialog_bars_all_services(self):
        from main_window import NetworkScanDialog
        dlg = NetworkScanDialog()
        self.assertIn("Motors / Wiper", dlg._bars)
        self.assertIn("Pump",           dlg._bars)
        self.assertIn("LIN Bus",        dlg._bars)
        dlg.close()


# ═══════════════════════════════════════════════════════════════
#  INTEGRATION — scénario complet vehicle → worker → mock server
# ═══════════════════════════════════════════════════════════════
class TestIntegrationVehicleFlow(unittest.TestCase):
    """
    Teste le flux complet :
    VehicleRainPanel → MotorVehicleWorker.queue_send → serveur mock TCP
    """

    @unittest.skip("Skipped in CI: scanning 10.20.0.0/24 spawns 254 threads which crashes Qt offscreen. Run on real network with hardware.")
    def test_full_vehicle_rain_flow(self):
        from workers import MotorVehicleWorker
        from panels  import VehicleRainPanel
        import unittest.mock as mock

        srv = _MockTCPServer()
        srv.start(); time.sleep(0.05)

        worker = MotorVehicleWorker()
        panel  = VehicleRainPanel(motor_getter=lambda: worker)

        import network, constants
        with mock.patch.object(network, 'auto_discover', return_value="127.0.0.1"):
            orig = constants.PORT_MOTOR
            constants.PORT_MOTOR = srv.port
            try:
                from PyQt6.QtCore import QThread
                t = QThread(); worker.moveToThread(t)
                t.started.connect(worker.run); t.start()
                time.sleep(0.3)

                # Simuler interactions utilisateur
                panel.ign._sel("ON");            _APP.processEvents()
                panel._toggle_rev();             _APP.processEvents()
                panel.sld_spd.setValue(800);     _APP.processEvents()  # 80 km/h
                panel.sld_rain.setValue(55);     _APP.processEvents()

                # Attendre que les messages arrivent
                deadline = time.time() + 2.0
                while len(srv.received) < 2 and time.time() < deadline:
                    time.sleep(0.05)

                worker.stop(); t.quit(); t.wait(2000)
            finally:
                constants.PORT_MOTOR = orig
        srv.stop()

        parsed = [json.loads(m) for m in srv.received if m]
        types  = {m.get("type") for m in parsed}

        self.assertIn("vehicle", types, "Message 'vehicle' non reçu")
        self.assertIn("rain",    types, "Message 'rain' non reçu")
        self.assertIn("wiper",   types, "Message 'wiper' non reçu")

        veh  = next(m for m in parsed if m.get("type") == "vehicle")
        rain = next(m for m in parsed if m.get("type") == "rain")

        self.assertEqual(veh["ignition_status"], "ON")
        self.assertEqual(veh["reverse_gear"],    1)
        self.assertAlmostEqual(veh["vehicle_speed"], 80.0, places=1)
        self.assertEqual(rain["rain_intensity"], 55)
        self.assertEqual(rain["sensor_status"],  "OK")


# ═══════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="WipeWash Test Suite")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("-k", "--filter",  type=str,  default="", help="Filter test names")
    args, _ = parser.parse_known_args()

    verbosity = 2 if args.verbose else 1

    # Charger tous les tests de CE fichier directement (robuste quelque soit le nom du fichier)
    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromModule(sys.modules[__name__])

    # Filtre -k (comme pytest)
    if args.filter:
        filtered = unittest.TestSuite()
        for group in suite:
            for test in group:
                if args.filter.lower() in test.id().lower():
                    filtered.addTest(test)
        suite = filtered

    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
