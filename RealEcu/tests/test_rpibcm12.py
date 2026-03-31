#!/usr/bin/env python3
"""
test_bcm2.py
============
Tests unitaires pour l'ECU BCM2 -- WipeWash System
Couvre : Machine d'état (WSM), UDS, DTC Manager, Securité, Diagnostics

Exécution :
    pip install pytest
    pytest test_bcm2.py -v

Structure :
    TestRTE              -- Variables partagées RTE
    TestDTCManager       -- Gestionnaire DTC
    TestWSMStateMachine  -- Machine d'état (transitions, gardes)
    TestOvercurrentProt  -- Protection surcourant moteur + pompe
    TestUDSHandlers      -- Services UDS (DSC, SA, RDID, WDID, RC, Clear)
    TestPumpLogic        -- Logique pompe (FWD/BWD, max runtime, overcurrent)
    TestReverseGear      -- SRD_WW_060 marche arrière
    TestRestContact      -- B2006 / B2009 contact repos
"""

import sys
import time
import types
import threading
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

# ─────────────────────────────────────────────────────────────
# Stub des modules hardware non disponibles hors RPi
# ─────────────────────────────────────────────────────────────
def _stub_module(name):
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod

for _m in ["RPi", "RPi.GPIO", "board", "busio",
           "adafruit_ads1x15", "adafruit_ads1x15.ads1115",
           "adafruit_ads1x15.analog_in", "redis"]:
    if _m not in sys.modules:
        _stub_module(_m)

# GPIO stub
GPIO = sys.modules["RPi.GPIO"]
GPIO.BCM = GPIO.OUT = GPIO.IN = GPIO.HIGH = GPIO.LOW = 0
GPIO.PUD_DOWN = 0
GPIO.setmode = lambda *a, **k: None
GPIO.setwarnings = lambda *a, **k: None
GPIO.setup = lambda *a, **k: None
GPIO.output = lambda *a, **k: None
GPIO.input = lambda *a, **k: 0
GPIO.cleanup = lambda *a, **k: None

# busio stub
busio = sys.modules["busio"]
busio.I2C = MagicMock()

# board stub
board = sys.modules["board"]
board.SCL = board.SDA = MagicMock()

# ADS stub
ads_mod = sys.modules["adafruit_ads1x15.ads1115"]
ads_mod.ADS1115 = MagicMock()
analog_in_mod = sys.modules["adafruit_ads1x15.analog_in"]
analog_in_mod.AnalogIn = MagicMock()

# redis stub
redis_mod = sys.modules["redis"]
redis_mod.Redis = MagicMock()
redis_mod.ConnectionPool = MagicMock()

# ─────────────────────────────────────────────────────────────
# Ajout du dossier bcm2 au path Python
# ─────────────────────────────────────────────────────────────
import os
BCM2_DIR = os.path.join(os.path.dirname(__file__), "bcm2")
if BCM2_DIR not in sys.path:
    sys.path.insert(0, BCM2_DIR)

# ─────────────────────────────────────────────────────────────
# Import des modules BCM2
# ─────────────────────────────────────────────────────────────
from bcm_rte import (
    RTE,
    ST_OFF, ST_TOUCH, ST_SPEED1, ST_SPEED2, ST_AUTO,
    ST_WASH_FRONT, ST_WASH_REAR, ST_REAR_WIPE, ST_ERROR, ST_DIAG,
    WOP_OFF, WOP_TOUCH, WOP_SPEED1, WOP_SPEED2, WOP_AUTO,
    WOP_FRONT_WASH, WOP_REAR_WASH, WOP_REAR_WIPE,
    OVERCURRENT_THRESH, PUMP_OVERCURRENT_THRESH,
    OVERCURRENT_DELAY, PUMP_OVERCURRENT_DELAY,
    TOUCH_DURATION, PUMP_MAX_RUNTIME,
    WASH_FRONT_CYCLES, WASH_REAR_CYCLES,
    RAIN_SPEED2_THRESH, WIPE_CYCLE_DURATION,
    REST_STUCK_DELAY,
    SA_XOR_MASK, SA_ADD_MASK,
)
from dtc_manager import DTCManager
from bcm_application import ApplicationLayer


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def _make_rte() -> RTE:
    """Crée un RTE propre sans Redis."""
    rte = RTE()
    rte._redis_ok = False
    rte._redis    = None
    rte.ignition_status = 1
    return rte


def _make_app(rte=None) -> ApplicationLayer:
    """Crée une ApplicationLayer mockée (sans GPIO, sans TCP)."""
    if rte is None:
        rte = _make_rte()
    dtc = DTCManager()

    with patch("bcm_application._gpio_setup"), \
         patch("bcm_application.TCPBroadcast") as MockTCP, \
         patch("bcm_application.TCPPumpBroadcast") as MockPumpTCP:
        MockTCP.return_value.send   = MagicMock()
        MockPumpTCP.return_value.send = MagicMock()
        app = ApplicationLayer(rte, dtc)

    # Éviter les appels réseau dans les tests
    app._tcp.send      = MagicMock()
    app._tcp_pump.send = MagicMock()
    return app


# ═══════════════════════════════════════════════════════════════
# TEST RTE
# ═══════════════════════════════════════════════════════════════
class TestRTE(unittest.TestCase):
    """Tests de la mémoire partagée RTE."""

    def setUp(self):
        self.rte = _make_rte()

    def test_initial_state_is_off(self):
        self.assertEqual(self.rte.state, ST_OFF)

    def test_ignition_default(self):
        rte = RTE()
        self.assertEqual(rte.ignition_status, 0)

    def test_set_and_get_threadsafe(self):
        self.rte.set("vehicle_speed", 42)
        self.assertEqual(self.rte.vehicle_speed, 42)

    def test_set_multi(self):
        self.rte.set_multi(rain_intensity=50, vehicle_speed=90)
        self.assertEqual(self.rte.rain_intensity, 50)
        self.assertEqual(self.rte.vehicle_speed, 90)

    def test_make_snapshot_keys(self):
        snap = self.rte.make_snapshot()
        for k in ("ignition", "wiper_mode", "motor_curr", "blade_pos", "rain", "vehicle_spd"):
            self.assertIn(k, snap)

    def test_concurrent_set_get(self):
        """Vérifie thread-safety sur 100 écritures parallèles."""
        errors = []
        def writer():
            for _ in range(50):
                try:
                    self.rte.set("rain_intensity", 10)
                except Exception as e:
                    errors.append(e)
        threads = [threading.Thread(target=writer) for _ in range(4)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(errors, [])


# ═══════════════════════════════════════════════════════════════
# TEST DTC MANAGER
# ═══════════════════════════════════════════════════════════════
class TestDTCManager(unittest.TestCase):
    """Tests du gestionnaire de codes défaut."""

    def setUp(self):
        self.dtc = DTCManager()
        self.snap = {"ignition": 1, "wiper_mode": "SPEED1",
                     "motor_curr": 500, "blade_pos": 1,
                     "rain": 0, "vehicle_spd": 50}

    def test_all_dtc_codes_present(self):
        expected = {"B2001","B2002","B2003","B2004","B2005",
                    "B2006","B2007","B2008","B2009"}
        self.assertEqual(set(self.dtc.dtcs.keys()), expected)

    def test_set_active_changes_status(self):
        self.dtc.set_active("B2001", self.snap)
        dtc = self.dtc.dtcs["B2001"]
        self.assertEqual(dtc["status"], 0x2F)  # STATUS_ACTIVE
        self.assertGreater(dtc["occurrence_count"], 0)

    def test_set_inactive_after_active(self):
        self.dtc.set_active("B2001", self.snap)
        self.dtc.set_inactive("B2001")
        dtc = self.dtc.dtcs["B2001"]
        self.assertEqual(dtc["status"], 0x2E)  # STATUS_INACTIVE

    def test_clear_dtc_resets_status(self):
        self.dtc.set_active("B2002", self.snap)
        # Commande UDS 0x14 : clear tous les DTC (group 0xFFFFFF)
        from dtc_manager import handle_clear_dtc
        response = handle_clear_dtc(self.dtc, bytes([0x14, 0xFF, 0xFF, 0xFF]))
        self.assertEqual(response[0], 0x54)
        self.assertEqual(self.dtc.dtcs["B2002"]["status"], 0x00)

    def test_multiple_occurrences(self):
        self.dtc.set_active("B2003", self.snap)
        self.dtc.set_active("B2003", self.snap)
        self.assertEqual(self.dtc.dtcs["B2003"]["occurrence_count"], 2)

    def test_unknown_dtc_code_ignored(self):
        """set_active sur code inconnu ne lève pas d'exception."""
        self.dtc.set_active("BXXXX", self.snap)  # doit juste logger


# ═══════════════════════════════════════════════════════════════
# TEST MACHINE D'ÉTAT WSM
# ═══════════════════════════════════════════════════════════════
class TestWSMStateMachine(unittest.TestCase):
    """Tests des transitions d'état du Wiper State Machine."""

    def setUp(self):
        self.rte = _make_rte()
        self.app = _make_app(self.rte)

    # ── Transitions de base ─────────────────────────────────
    def test_off_to_speed1(self):
        self.rte.crs_wiper_op = WOP_SPEED1
        self.app._update_state_machine()
        self.assertEqual(self.rte.state, ST_SPEED1)

    def test_off_to_speed2(self):
        self.rte.crs_wiper_op = WOP_SPEED2
        self.app._update_state_machine()
        self.assertEqual(self.rte.state, ST_SPEED2)

    def test_speed1_to_off(self):
        self.app._enter_state(ST_SPEED1)
        self.rte.crs_wiper_op = WOP_OFF
        self.app._process_speed1(WOP_OFF)
        self.assertEqual(self.rte.state, ST_OFF)

    def test_speed1_to_speed2(self):
        self.app._enter_state(ST_SPEED1)
        self.rte.crs_wiper_op = WOP_SPEED2
        self.app._process_speed1(WOP_SPEED2)
        self.assertEqual(self.rte.state, ST_SPEED2)

    def test_speed2_to_speed1(self):
        self.app._enter_state(ST_SPEED2)
        self.rte.crs_wiper_op = WOP_SPEED1
        self.app._process_speed2(WOP_SPEED1)
        self.assertEqual(self.rte.state, ST_SPEED1)

    def test_ignition_off_forces_off_from_speed1(self):
        """SRD_WW_001 : ignition=0 force ST_OFF depuis n'importe quel état."""
        self.app._enter_state(ST_SPEED1)
        self.rte.ignition_status = 0
        self.app._update_state_machine()
        self.assertEqual(self.rte.state, ST_OFF)

    def test_ignition_off_forces_off_from_error(self):
        """SRD_WW_001 étendu : ST_ERROR aussi forcé vers ST_OFF."""
        self.app._enter_state(ST_ERROR)
        self.rte.ignition_status = 0
        self.app._update_state_machine()
        self.assertEqual(self.rte.state, ST_OFF)

    # ── Mode AUTO ───────────────────────────────────────────
    def test_auto_requires_rain_sensor(self):
        self.rte.rain_sensor_installed = False
        self.rte.crs_wiper_op = WOP_AUTO
        self.app._process_off_state(WOP_AUTO)
        self.assertEqual(self.rte.state, ST_OFF)

    def test_auto_with_rain_sensor(self):
        self.rte.rain_sensor_installed = True
        self.rte.crs_wiper_op = WOP_AUTO
        self.app._process_off_state(WOP_AUTO)
        self.assertEqual(self.rte.state, ST_AUTO)

    def test_auto_speed2_high_rain(self):
        self.rte.rain_sensor_installed = True
        self.app._enter_state(ST_AUTO)
        self.rte.rain_intensity = RAIN_SPEED2_THRESH + 1
        self.rte._auto_speed_prev = -1
        self.app._process_auto(WOP_AUTO)
        self.assertEqual(self.rte.front_motor_speed, 2)

    def test_auto_speed1_low_rain(self):
        self.rte.rain_sensor_installed = True
        self.app._enter_state(ST_AUTO)
        self.rte.rain_intensity = 5
        self.rte._auto_speed_prev = -1
        self.app._process_auto(WOP_AUTO)
        self.assertEqual(self.rte.front_motor_speed, 1)

    def test_auto_stop_no_rain(self):
        self.rte.rain_sensor_installed = True
        self.app._enter_state(ST_AUTO)
        self.rte.rain_intensity = 0
        self.rte._auto_speed_prev = 2
        self.app._process_auto(WOP_AUTO)
        self.assertFalse(self.rte.front_motor_on)

    # ── Mode TOUCH ──────────────────────────────────────────
    def test_touch_is_oneshot(self):
        """TOUCH n'est déclenché qu'une seule fois (one-shot)."""
        self.rte._one_shot_armed = True
        self.rte.crs_wiper_op   = WOP_TOUCH
        self.app._process_off_state(WOP_TOUCH)
        self.assertEqual(self.rte.state, ST_TOUCH)
        self.assertFalse(self.rte._one_shot_armed)

    def test_touch_goes_off_after_timeout(self):
        self.app._enter_state(ST_TOUCH)
        # Simuler un dépassement du délai TOUCH
        self.rte.t_touch_start = time.time() - (TOUCH_DURATION + 0.1)
        # Simuler lame au repos (contact relâché = False = repos)
        self.app._read_rest_contact = MagicMock(return_value=False)
        self.app._process_touch()
        self.assertEqual(self.rte.state, ST_OFF)

    # ── Mode WASH FRONT ─────────────────────────────────────
    def test_front_wash_starts_pump_fwd(self):
        self.rte._one_shot_armed = True
        self.rte.crs_wiper_op   = WOP_FRONT_WASH
        self.app._process_off_state(WOP_FRONT_WASH)
        self.assertEqual(self.rte.state, ST_WASH_FRONT)
        self.assertTrue(self.rte.pump_active)
        self.assertEqual(self.rte.pump_direction, 1)  # FWD

    def test_front_wash_ends_after_cycles(self):
        self.app._enter_state(ST_WASH_FRONT)
        self.rte.wash_cycles_done = WASH_FRONT_CYCLES
        self.app._process_front_wash()
        self.assertEqual(self.rte.state, ST_OFF)
        self.assertFalse(self.rte.pump_active)

    # ── Mode WASH REAR ──────────────────────────────────────
    def test_rear_wash_requires_rear_available(self):
        self.rte.rear_wiper_available = False
        self.rte._one_shot_armed = True
        self.rte.crs_wiper_op   = WOP_REAR_WASH
        self.app._process_off_state(WOP_REAR_WASH)
        self.assertEqual(self.rte.state, ST_OFF)

    def test_rear_wash_starts_pump_bwd(self):
        self.rte.rear_wiper_available = True
        self.rte._one_shot_armed = True
        self.rte.crs_wiper_op   = WOP_REAR_WASH
        self.app._process_off_state(WOP_REAR_WASH)
        self.assertEqual(self.rte.state, ST_WASH_REAR)
        self.assertEqual(self.rte.pump_direction, 2)  # BWD

    def test_rear_wash_ends_after_cycles(self):
        self.rte.rear_wiper_available = True
        self.app._enter_state(ST_WASH_REAR)
        self.rte.wash_cycles_done = WASH_REAR_CYCLES
        self.app._process_rear_wash()
        self.assertEqual(self.rte.state, ST_OFF)

    # ── Mode REAR WIPE ──────────────────────────────────────
    def test_rear_wipe_requires_rear_available(self):
        self.rte.rear_wiper_available = False
        self.rte.crs_wiper_op = WOP_REAR_WIPE
        self.app._process_off_state(WOP_REAR_WIPE)
        self.assertEqual(self.rte.state, ST_OFF)

    def test_rear_wipe_starts_rear_motor(self):
        self.rte.rear_wiper_available = True
        self.rte.crs_wiper_op = WOP_REAR_WIPE
        self.app._process_off_state(WOP_REAR_WIPE)
        self.assertEqual(self.rte.state, ST_REAR_WIPE)
        self.assertTrue(self.rte.rear_motor_on)

    def test_rear_wipe_stops_on_lever_release(self):
        self.rte.rear_wiper_available = True
        self.app._enter_state(ST_REAR_WIPE)
        self.rte.crs_wiper_op = WOP_OFF
        self.app._process_rear_wipe()
        self.assertEqual(self.rte.state, ST_OFF)

    # ── LIN Timeout ─────────────────────────────────────────
    def test_lin_timeout_forces_off(self):
        self.app._enter_state(ST_SPEED1)
        self.rte.lin_timeout_active = True
        self.app._update_state_machine()
        self.assertEqual(self.rte.state, ST_OFF)

    # ── Nettoyage actionneurs lors des transitions ───────────
    def test_front_to_rear_cleans_front_motor(self):
        self.app._enter_state(ST_SPEED1)
        self.rte.front_motor_on = True
        self.app._exit_current_state(ST_WASH_REAR)
        self.assertFalse(self.rte.front_motor_on)

    def test_rear_to_front_cleans_rear_motor(self):
        self.app._enter_state(ST_WASH_REAR)
        self.rte.rear_motor_on = True
        self.app._exit_current_state(ST_SPEED1)
        self.assertFalse(self.rte.rear_motor_on)

    # ── État ERROR ──────────────────────────────────────────
    def test_error_clears_on_op_off(self):
        self.app._enter_state(ST_ERROR)
        self.rte.crs_wiper_op = WOP_OFF
        self.rte.lin_timeout_active = False
        self.app._update_state_machine()
        self.assertEqual(self.rte.state, ST_OFF)

    def test_enter_off_resets_error_flags(self):
        self.rte.front_motor_error = True
        self.rte.rear_motor_error  = True
        self.rte.pump_error        = True
        self.app._enter_off()
        self.assertFalse(self.rte.front_motor_error)
        self.assertFalse(self.rte.rear_motor_error)
        self.assertFalse(self.rte.pump_error)


# ═══════════════════════════════════════════════════════════════
# TEST PROTECTION SURCOURANT
# ═══════════════════════════════════════════════════════════════
class TestOvercurrentProt(unittest.TestCase):
    """Tests de la protection surcourant moteur avant/arrière."""

    def setUp(self):
        self.rte = _make_rte()
        self.app = _make_app(self.rte)

    def _set_front_overcurrent(self):
        self.rte.front_motor_on  = True
        self.rte.front_motor_speed = 1
        self.rte.state           = ST_SPEED1
        self.rte.motor_current_a = OVERCURRENT_THRESH + 0.1
        # Simuler délai dépassé
        self.rte.t_overcurrent_start["front"] = time.time() - (OVERCURRENT_DELAY + 0.1)

    def test_front_overcurrent_triggers_b2001(self):
        self._set_front_overcurrent()
        self.app._check_overcurrent()
        self.assertFalse(self.rte.front_motor_on)
        self.assertTrue(self.rte.front_motor_error)
        self.assertEqual(self.rte.state, ST_ERROR)

    def test_front_overcurrent_does_not_stop_pump(self):
        self._set_front_overcurrent()
        self.rte.pump_active = True
        self.app._check_overcurrent()
        self.assertTrue(self.rte.pump_active)

    def test_rear_overcurrent_triggers_b2002(self):
        self.rte.rear_motor_on   = True
        self.rte.state           = ST_WASH_REAR
        self.rte.motor_current_a = OVERCURRENT_THRESH + 0.1
        self.rte.t_overcurrent_start["rear"] = time.time() - (OVERCURRENT_DELAY + 0.1)
        self.app._check_overcurrent()
        self.assertFalse(self.rte.rear_motor_on)
        self.assertTrue(self.rte.rear_motor_error)

    def test_no_overcurrent_below_thresh(self):
        self.rte.front_motor_on  = True
        self.rte.state           = ST_SPEED1
        self.rte.motor_current_a = OVERCURRENT_THRESH - 0.1
        self.app._check_overcurrent()
        self.assertEqual(self.rte.state, ST_SPEED1)

    def test_pump_overcurrent_triggers_b2003(self):
        self.rte.pump_active    = True
        self.rte.pump_current_a = PUMP_OVERCURRENT_THRESH + 0.1
        self.rte._pump_overcurrent_start = time.time() - (PUMP_OVERCURRENT_DELAY + 0.1)
        self.app._check_pump_overcurrent()
        self.assertFalse(self.rte.pump_active)
        self.assertTrue(self.rte.pump_error)
        # Moteur non affecté
        self.assertNotEqual(self.rte.state, ST_ERROR)


# ═══════════════════════════════════════════════════════════════
# TEST LOGIQUE POMPE
# ═══════════════════════════════════════════════════════════════
class TestPumpLogic(unittest.TestCase):
    """Tests de la logique de contrôle pompe."""

    def setUp(self):
        self.rte = _make_rte()
        self.app = _make_app(self.rte)

    def test_pump_start_fwd(self):
        self.app._pump_start(1)
        self.assertTrue(self.rte.pump_active)
        self.assertEqual(self.rte.pump_direction, 1)

    def test_pump_start_bwd(self):
        self.app._pump_start(2)
        self.assertTrue(self.rte.pump_active)
        self.assertEqual(self.rte.pump_direction, 2)

    def test_pump_start_idempotent(self):
        """Appeler pump_start deux fois ne change pas la direction."""
        self.app._pump_start(1)
        self.app._pump_start(2)  # ignoré car déjà active
        self.assertEqual(self.rte.pump_direction, 1)

    def test_pump_stop(self):
        self.app._pump_start(1)
        self.app._pump_stop("test")
        self.assertFalse(self.rte.pump_active)
        self.assertEqual(self.rte.pump_direction, 0)

    def test_pump_max_runtime_protection(self):
        self.rte.pump_active  = True
        self.rte.t_pump_start = time.time() - (PUMP_MAX_RUNTIME + 0.1)
        self.rte.state        = ST_SPEED1  # pas dans wash → B2008
        self.app._check_pump_protection()
        self.assertFalse(self.rte.pump_active)
        # DTC B2008 doit être actif
        self.assertEqual(self.app._dtc.dtcs["B2008"]["status"], 0x2F)


# ═══════════════════════════════════════════════════════════════
# TEST MARCHE ARRIÈRE (SRD_WW_060)
# ═══════════════════════════════════════════════════════════════
class TestReverseGear(unittest.TestCase):
    """Tests SRD_WW_060 : Wiper arrière en marche arrière."""

    def setUp(self):
        self.rte = _make_rte()
        self.app = _make_app(self.rte)

    def test_reverse_starts_rear_motor_when_front_active(self):
        self.rte.rear_wiper_available = True
        self.app._enter_state(ST_SPEED1)
        self.rte.reverse_gear = True
        self.rte._reverse_active = False
        self.app._handle_reverse_intermittent()
        self.assertTrue(self.rte.rear_motor_on)
        self.assertTrue(self.rte._reverse_active)

    def test_reverse_stop_when_gear_off(self):
        self.rte.rear_wiper_available = True
        self.app._enter_state(ST_SPEED1)
        self.rte.reverse_gear    = True
        self.rte._reverse_active = True
        self.rte.rear_motor_on   = True
        self.rte.reverse_gear    = False
        self.app._handle_reverse_intermittent()
        self.assertFalse(self.rte.rear_motor_on)
        self.assertFalse(self.rte._reverse_active)

    def test_reverse_requires_rear_available(self):
        self.rte.rear_wiper_available = False
        self.app._enter_state(ST_SPEED1)
        self.rte.reverse_gear = True
        self.app._handle_reverse_intermittent()
        self.assertFalse(self.rte.rear_motor_on)

    def test_reverse_not_active_in_off_state(self):
        """Marche arrière ignorée si le balayage avant n'est pas actif."""
        self.rte.rear_wiper_available = True
        self.rte.state        = ST_OFF
        self.rte.reverse_gear = True
        self.rte._reverse_active = False
        self.app._handle_reverse_intermittent()
        self.assertFalse(self.rte.rear_motor_on)


# ═══════════════════════════════════════════════════════════════
# TEST CONTACT REPOS (B2006 / B2009)
# ═══════════════════════════════════════════════════════════════
class TestRestContact(unittest.TestCase):
    """Tests des DTCs contact repos (B2006 lame bloquée, B2009 contact coincé)."""

    def setUp(self):
        self.rte = _make_rte()
        self.app = _make_app(self.rte)
        # Activer la simulation du contact repos
        self.rte.rest_contact_sim_active = True

    def test_b2006_blade_still_moving_after_stop(self):
        """B2006 : lame encore en mouvement 2s après arrêt moteur."""
        from bcm_rte import REST_CONTACT_HARDWARE_PRESENT
        import bcm_rte
        original = bcm_rte.REST_CONTACT_HARDWARE_PRESENT
        bcm_rte.REST_CONTACT_HARDWARE_PRESENT = True

        self.rte.t_motor_stop  = time.time() - 2.5
        self.rte.state         = ST_OFF
        self.rte._rest_contact_b2006_active = False
        # Simuler lame encore en mouvement (bouton appuyé = True)
        self.rte.rest_contact_sim = True
        self.app._check_blade_position()
        self.assertTrue(self.rte._rest_contact_b2006_active)
        self.assertEqual(self.rte.state, ST_ERROR)

        bcm_rte.REST_CONTACT_HARDWARE_PRESENT = original

    def test_b2006_not_triggered_blade_at_rest(self):
        """B2006 non déclenché si lame au repos (bouton relâché = False)."""
        import bcm_rte
        original = bcm_rte.REST_CONTACT_HARDWARE_PRESENT
        bcm_rte.REST_CONTACT_HARDWARE_PRESENT = True

        self.rte.t_motor_stop  = time.time() - 2.5
        self.rte.state         = ST_OFF
        self.rte._rest_contact_b2006_active = False
        # Lame au repos
        self.rte.rest_contact_sim = False
        self.app._check_blade_position()
        self.assertFalse(self.rte._rest_contact_b2006_active)
        self.assertEqual(self.rte.state, ST_OFF)

        bcm_rte.REST_CONTACT_HARDWARE_PRESENT = original

    def test_b2006_guard_prevents_loop(self):
        """Garde anti-boucle : B2006 ne se déclenche pas deux fois."""
        import bcm_rte
        original = bcm_rte.REST_CONTACT_HARDWARE_PRESENT
        bcm_rte.REST_CONTACT_HARDWARE_PRESENT = True

        self.rte._rest_contact_b2006_active = True
        self.rte.rest_contact_sim = True
        self.rte.t_motor_stop     = time.time() - 3.0
        self.app._check_blade_position()
        # Ne doit pas changer d'état
        self.assertEqual(self.rte.state, ST_OFF)

        bcm_rte.REST_CONTACT_HARDWARE_PRESENT = original

    def test_clear_dtc_resets_b2006_guard(self):
        """0x14 ClearDTC remet _rest_contact_b2006_active à False."""
        self.rte._rest_contact_b2006_active = True
        uds = bytes([0x14, 0xFF, 0xFF, 0xFF])
        self.app._handle_clear(uds)
        self.assertFalse(self.rte._rest_contact_b2006_active)


# ═══════════════════════════════════════════════════════════════
# TEST HANDLERS UDS
# ═══════════════════════════════════════════════════════════════
class TestUDSHandlers(unittest.TestCase):
    """Tests des services UDS supportés par le BCM."""

    def setUp(self):
        self.rte = _make_rte()
        self.app = _make_app(self.rte)

    # ── DSC : Diagnostic Session Control ────────────────────
    def test_dsc_default_session(self):
        resp = self.app._handle_dsc(bytes([0x10, 0x01]))
        self.assertEqual(resp[0], 0x50)
        self.assertEqual(resp[1], 0x01)

    def test_dsc_extended_session(self):
        resp = self.app._handle_dsc(bytes([0x10, 0x03]))
        self.assertEqual(resp[0], 0x50)
        self.assertEqual(self.rte._session, 0x03)

    def test_dsc_invalid_subfunction(self):
        resp = self.app._handle_dsc(bytes([0x10, 0x99]))
        self.assertEqual(resp[0], 0x7F)   # NRC
        self.assertEqual(resp[2], 0x12)   # subFunctionNotSupported

    def test_dsc_suppress_positive_response(self):
        """Bit 7 de sub = suppress response."""
        resp = self.app._handle_dsc(bytes([0x10, 0x81]))  # 0x01 | 0x80
        self.assertEqual(resp, b"")

    # ── SA : Security Access ────────────────────────────────
    def test_sa_requires_extended_session(self):
        self.rte._session = 0x01  # Default
        resp = self.app._handle_sa(bytes([0x27, 0x01]))
        self.assertEqual(resp[0], 0x7F)
        self.assertEqual(resp[2], 0x7E)  # requestOutOfRange / conditions not correct

    def test_sa_request_seed_returns_67(self):
        self.rte._session   = 0x03
        self.rte._sec_level = 0
        resp = self.app._handle_sa(bytes([0x27, 0x01]))
        self.assertEqual(resp[0], 0x67)
        self.assertEqual(resp[1], 0x01)
        self.assertEqual(len(resp), 4)  # 0x67 + sub + seed(2)

    def test_sa_already_unlocked_returns_zero_seed(self):
        self.rte._session   = 0x03
        self.rte._sec_level = 1
        resp = self.app._handle_sa(bytes([0x27, 0x01]))
        self.assertEqual(resp[2], 0x00)
        self.assertEqual(resp[3], 0x00)

    def test_sa_correct_key_unlocks(self):
        self.rte._session   = 0x03
        self.rte._sec_level = 0
        seed_resp = self.app._handle_sa(bytes([0x27, 0x01]))
        seed = (seed_resp[2] << 8) | seed_resp[3]
        key  = ((seed ^ SA_XOR_MASK) + SA_ADD_MASK) & 0xFFFF
        resp = self.app._handle_sa(bytes([0x27, 0x02, key >> 8, key & 0xFF]))
        self.assertEqual(resp[0], 0x67)
        self.assertEqual(self.rte._sec_level, 1)

    def test_sa_wrong_key_rejected(self):
        self.rte._session   = 0x03
        self.rte._sec_level = 0
        self.app._handle_sa(bytes([0x27, 0x01]))  # get seed
        self.rte._pending_seed = {1: 0x1234}
        resp = self.app._handle_sa(bytes([0x27, 0x02, 0xDE, 0xAD]))  # mauvaise clé
        self.assertEqual(resp[2], 0x35)  # invalidKey

    # ── RDID : ReadDataByIdentifier ─────────────────────────
    def test_rdid_f100_returns_state(self):
        self.rte.state = ST_SPEED1
        resp = self.app._handle_rdid(bytes([0x22, 0xF1, 0x00]))
        self.assertEqual(resp[0], 0x62)
        self.assertEqual(resp[3], 2)   # ST_ENC[SPEED1] = 2

    def test_rdid_f101_returns_motor_speed(self):
        self.rte.front_motor_speed = 2
        resp = self.app._handle_rdid(bytes([0x22, 0xF1, 0x01]))
        self.assertEqual(resp[3], 2)

    def test_rdid_f103_returns_motor_current_ma(self):
        self.rte.motor_current_a = 0.5
        resp = self.app._handle_rdid(bytes([0x22, 0xF1, 0x03]))
        curr_ma = (resp[3] << 8) | resp[4]
        self.assertEqual(curr_ma, 500)

    def test_rdid_unknown_did_returns_nrc(self):
        resp = self.app._handle_rdid(bytes([0x22, 0xAB, 0xCD]))
        self.assertEqual(resp[0], 0x7F)
        self.assertEqual(resp[2], 0x31)  # requestOutOfRange

    # ── WDID : WriteDataByIdentifier ────────────────────────
    def test_wdid_requires_extended_session(self):
        self.rte._session = 0x01
        resp = self.app._handle_wdid(bytes([0x2E, 0xF2, 0x00, 0x01]))
        self.assertEqual(resp[2], 0x22)

    def test_wdid_requires_security_access(self):
        self.rte._session   = 0x03
        self.rte._sec_level = 0
        resp = self.app._handle_wdid(bytes([0x2E, 0xF2, 0x00, 0x01]))
        self.assertEqual(resp[2], 0x33)

    def test_wdid_f200_sets_rain_sensor(self):
        self.rte._session   = 0x03
        self.rte._sec_level = 1
        resp = self.app._handle_wdid(bytes([0x2E, 0xF2, 0x00, 0x01]))
        self.assertEqual(resp[0], 0x6E)
        self.assertTrue(self.rte.rain_sensor_installed)

    def test_wdid_f201_enables_wc_available(self):
        self.rte._session   = 0x03
        self.rte._sec_level = 1
        self.app._handle_wdid(bytes([0x2E, 0xF2, 0x01, 0x01]))
        self.assertTrue(self.rte.wc_available)

    def test_wdid_f202_disables_rear_wiper(self):
        self.rte._session   = 0x03
        self.rte._sec_level = 1
        self.rte.rear_wiper_available = True
        self.app._handle_wdid(bytes([0x2E, 0xF2, 0x02, 0x00]))
        self.assertFalse(self.rte.rear_wiper_available)

    # ── RC : RoutineControl ─────────────────────────────────
    def test_rc_0201_starts_front_motor_diag(self):
        resp = self.app._handle_rc(bytes([0x31, 0x01, 0x02, 0x01, 0x05]))
        self.assertEqual(resp[0], 0x71)
        self.assertEqual(self.rte.state, ST_DIAG)
        self.assertTrue(self.rte.front_motor_on)

    def test_rc_0202_starts_rear_motor_diag(self):
        self.rte.rear_wiper_available = True
        resp = self.app._handle_rc(bytes([0x31, 0x01, 0x02, 0x02, 0x05]))
        self.assertEqual(resp[0], 0x71)
        self.assertEqual(self.rte.state, ST_DIAG)
        self.assertTrue(self.rte.rear_motor_on)

    def test_rc_0203_starts_pump_fwd_diag(self):
        resp = self.app._handle_rc(bytes([0x31, 0x01, 0x02, 0x03, 0x05]))
        self.assertEqual(resp[0], 0x71)
        self.assertTrue(self.rte.pump_active)
        self.assertEqual(self.rte.pump_dir_active, 1)

    def test_rc_0204_starts_pump_bwd_diag(self):
        resp = self.app._handle_rc(bytes([0x31, 0x01, 0x02, 0x04, 0x05]))
        self.assertEqual(resp[0], 0x71)
        self.assertTrue(self.rte.pump_active)
        self.assertEqual(self.rte.pump_dir_active, 2)

    def test_rc_stop_0x02_stops_test(self):
        # Démarrer un test
        self.app._handle_rc(bytes([0x31, 0x01, 0x02, 0x01, 0x05]))
        # Arrêter
        resp = self.app._handle_rc(bytes([0x31, 0x02, 0x02, 0x01]))
        self.assertEqual(resp[0], 0x71)
        self.assertFalse(self.rte._test_active)

    def test_rc_invalid_rid_returns_nrc(self):
        resp = self.app._handle_rc(bytes([0x31, 0x01, 0xFF, 0xFF, 0x05]))
        self.assertEqual(resp[0], 0x7F)

    # ── TesterPresent ───────────────────────────────────────
    def test_tp_response(self):
        resp = self.app._handle_tp(bytes([0x3E, 0x00]))
        self.assertEqual(resp[0], 0x7E)

    def test_tp_suppress(self):
        resp = self.app._handle_tp(bytes([0x3E, 0x80]))
        self.assertEqual(resp, b"")

    # ── CommunicationControl ────────────────────────────────
    def test_cc_disable_tx(self):
        resp = self.app._handle_cc(bytes([0x28, 0x01, 0x01]))
        self.assertEqual(resp[0], 0x68)
        self.assertFalse(self.rte._comm_tx_enabled)

    def test_cc_enable_all(self):
        self.rte._comm_tx_enabled = False
        resp = self.app._handle_cc(bytes([0x28, 0x00, 0x01]))
        self.assertEqual(resp[0], 0x68)
        self.assertTrue(self.rte._comm_tx_enabled)
        self.assertTrue(self.rte._comm_rx_enabled)

    # ── ECU Reset ───────────────────────────────────────────
    def test_ecu_reset_returns_to_off(self):
        self.app._enter_state(ST_SPEED2)
        resp = self.app._handle_reset(bytes([0x11, 0x01]))
        self.assertEqual(resp[0], 0x51)
        self.assertEqual(self.rte.state, ST_OFF)
        self.assertEqual(self.rte._session,   1)
        self.assertEqual(self.rte._sec_level, 0)

    def test_ecu_reset_clears_b2006_guard(self):
        self.rte._rest_contact_b2006_active = True
        self.app._handle_reset(bytes([0x11, 0x01]))
        self.assertFalse(self.rte._rest_contact_b2006_active)

    # ── ReadDTCInformation ──────────────────────────────────
    def test_read_dtc_by_status_mask(self):
        from dtc_manager import handle_read_dtc
        snap = {"ignition":1,"wiper_mode":"OFF","motor_curr":0,
                "blade_pos":0,"rain":0,"vehicle_spd":0}
        self.app._dtc.set_active("B2001", snap)
        resp = handle_read_dtc(self.app._dtc, bytes([0x19, 0x02, 0xFF]))
        self.assertEqual(resp[0], 0x59)


# ═══════════════════════════════════════════════════════════════
# TEST CAN TIMEOUT (B2005)
# ═══════════════════════════════════════════════════════════════
class TestCANTimeout(unittest.TestCase):
    """Tests supervision CAN WC (B2005)."""

    def setUp(self):
        self.rte = _make_rte()
        self.app = _make_app(self.rte)

    def test_b2005_triggers_on_wc_timeout(self):
        from bcm_rte import CAN_WC_TIMEOUT
        self.rte.wc_available         = True
        self.rte.t_last_wiper_status  = time.time() - (CAN_WC_TIMEOUT + 0.5)
        self.rte.wc_timeout_active    = False
        self.rte.state                = ST_SPEED1
        self.app._check_wc_timeout()
        self.assertTrue(self.rte.wc_timeout_active)
        self.assertEqual(self.app._dtc.dtcs["B2005"]["status"], 0x2F)

    def test_b2005_not_triggered_without_wc(self):
        self.rte.wc_available = False
        self.app._check_wc_timeout()
        self.assertFalse(self.rte.wc_timeout_active)

    def test_b2005_suspended_in_rear_states(self):
        from bcm_rte import CAN_WC_TIMEOUT
        self.rte.wc_available        = True
        self.rte.t_last_wiper_status = time.time() - (CAN_WC_TIMEOUT + 1.0)
        self.rte.state               = ST_WASH_REAR
        self.app._check_wc_timeout()
        self.assertFalse(self.rte.wc_timeout_active)


# ═══════════════════════════════════════════════════════════════
# POINT D'ENTRÉE
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    unittest.main(verbosity=2)