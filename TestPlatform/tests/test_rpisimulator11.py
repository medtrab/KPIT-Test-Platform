#!/usr/bin/env python3
"""
test_wc2.py  --  Tests unitaires ECU WC2  (WipeWash System)
=============================================================
VERSION CORRIGÉE -- 100 % synchronisée avec le code source réel.

Corrections majeures v2 :
  - crslin : utilise WOp (pas WiperOp), _lin_checksum (pas classic/enhanced),
             _set_lin_paused/_is_lin_paused (pas _handle_test_cmd/_State),
             NodeState.inc_alive() (pas _increment_alive),
             _tcp_broadcast (pas _build_tcp_tx16 / _build_tcp_rx_hdr)
  - bcmcan : _build_0x201/_build_0x300/_build_0x301, _crc_tx, _can_tx_paused
             directement (pas de _set_can_tx_paused, _decode_wiper_mode etc.)
  - wc_doip: WCDoIPServer (pas _UDSHandler), _hdr/_vehicle_id_response,
             _process_uds, DIDs F110-F115, RC nécessite DSC_EXTENDED
  - wc_dtc_manager : snapshot_records (pas snapshots)
  - TestWCStateShared : conservé tel quel (100 % OK)

Exécution :
    pytest test_wc2.py -v
    python3 test_wc2.py
"""

import sys, os, json, types, struct, threading, unittest, time
from unittest.mock import MagicMock, patch

# ─────────────────────────────────────────────────────────────────────────────
# STUBS HARDWARE
# ─────────────────────────────────────────────────────────────────────────────
def _stub(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items(): setattr(mod, k, v)
    sys.modules.setdefault(name, mod)
    return sys.modules[name]

_stub("gpiod", Chip=MagicMock(), LineRequest=MagicMock())
_stub("board", SCL=MagicMock(), SDA=MagicMock())
_stub("busio",  I2C=MagicMock())

_mock_ch = MagicMock(); _mock_ch.voltage = 0.0
_stub("adafruit_ads1x15")
_stub("adafruit_ads1x15.ads1115",   ADS1115=MagicMock(return_value=MagicMock()))
_stub("adafruit_ads1x15.analog_in", AnalogIn=MagicMock(return_value=_mock_ch))

_serial_mod = _stub("serial", SerialException=Exception)
_serial_mod.Serial = MagicMock()

_can_mod = _stub("can", Bus=MagicMock(), Message=MagicMock())
_can_mod.interface = MagicMock()

_stub("redis", Redis=MagicMock(), ConnectionPool=MagicMock())

# ─────────────────────────────────────────────────────────────────────────────
# PATH
# ─────────────────────────────────────────────────────────────────────────────
WC2_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wc2")
if WC2_DIR not in sys.path:
    sys.path.insert(0, WC2_DIR)

from wc_dtc_manager import DTCManager_WC
import crslin
import wc_doip

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _snap():
    return {"ignition":1,"wiper_mode":"SPEED1","motor_curr":300,
            "blade_pos":1,"rain":10,"vehicle_spd":60}

def _make_wc_dtc(): return DTCManager_WC()
def _make_wc_state(): return wc_doip._WCState()

def _make_doip_server():
    """Crée un WCDoIPServer sans ouvrir de socket."""
    st  = _make_wc_state()
    dtc = _make_wc_dtc()
    srv = wc_doip.WCDoIPServer(st, dtc)
    return srv, st, dtc


# ═════════════════════════════════════════════════════════════════════════════
# TEST DTC MANAGER WC
# ═════════════════════════════════════════════════════════════════════════════
class TestWCDTCManager(unittest.TestCase):

    def setUp(self): self.dtc = _make_wc_dtc()

    def test_codes_present(self):
        self.assertEqual(set(self.dtc.dtcs), {"B2101","B2102","B2103"})

    def test_set_active_b2101(self):
        self.dtc.set_active("B2101", _snap())
        self.assertEqual(self.dtc.dtcs["B2101"]["status"], 0x2F)

    def test_set_active_b2102(self):
        self.dtc.set_active("B2102", _snap())
        self.assertEqual(self.dtc.dtcs["B2102"]["status"], 0x2F)

    def test_set_active_b2103(self):
        self.dtc.set_active("B2103", _snap())
        self.assertEqual(self.dtc.dtcs["B2103"]["status"], 0x2F)

    def test_set_inactive(self):
        self.dtc.set_active("B2101", _snap())
        self.dtc.set_inactive("B2101")
        self.assertEqual(self.dtc.dtcs["B2101"]["status"], 0x2E)

    def test_clear_all(self):
        from wc_dtc_manager import handle_clear_dtc
        self.dtc.set_active("B2101", _snap())
        self.dtc.set_active("B2102", _snap())
        r = handle_clear_dtc(self.dtc, bytes([0x14,0xFF,0xFF,0xFF]))
        self.assertEqual(r[0], 0x54)
        self.assertEqual(self.dtc.dtcs["B2101"]["status"], 0x00)
        self.assertEqual(self.dtc.dtcs["B2102"]["status"], 0x00)

    def test_occurrence_count(self):
        self.dtc.set_active("B2103", _snap())
        self.dtc.set_active("B2103", _snap())
        self.assertEqual(self.dtc.dtcs["B2103"]["occurrence_count"], 2)

    def test_first_occurrence_set(self):
        self.dtc.set_active("B2101", _snap())
        self.assertIsNotNone(self.dtc.dtcs["B2101"]["first_occurrence"])

    def test_unknown_ignored(self):
        self.dtc.set_active("BXXXX", _snap())

    def test_status_bits_active(self):
        self.dtc.set_active("B2101", _snap())
        s = self.dtc.dtcs["B2101"]["status"]
        self.assertTrue(s & 0x01)  # test_failed
        self.assertTrue(s & 0x04)  # pending
        self.assertTrue(s & 0x08)  # confirmed

    def test_read_dtc_response(self):
        from wc_dtc_manager import handle_read_dtc
        self.dtc.set_active("B2101", _snap())
        r = handle_read_dtc(self.dtc, bytes([0x19,0x02,0xFF]))
        self.assertEqual(r[0], 0x59)
        self.assertGreater(len(r), 4)

    def test_snapshot_records_stored(self):
        """Les snapshots sont stockés dans snapshot_records (pas snapshots)."""
        self.dtc.set_active("B2102", _snap())
        records = self.dtc.dtcs["B2102"].get("snapshot_records", [])
        self.assertGreater(len(records), 0)
        # Vérifier la structure du record
        rec = records[0]
        self.assertIn("record_number", rec)
        self.assertIn("data", rec)
        self.assertIn("F190_ignition", rec["data"])


# ═════════════════════════════════════════════════════════════════════════════
# TEST CRS LIN -- Protocole (fonctions réelles)
# ═════════════════════════════════════════════════════════════════════════════
class TestCRSLinProtocol(unittest.TestCase):

    def test_pid_0x16_correct(self):
        """PID calculé pour ID 0x16 = 0xD6."""
        self.assertEqual(crslin.LIN_PID_16, 0xD6)

    def test_pid_0x17_correct(self):
        """PID calculé pour ID 0x17 = 0x97."""
        self.assertEqual(crslin.LIN_PID_17, 0x97)

    def test_calculate_pid_0x16(self):
        """_calculate_pid(0x16) doit retourner 0xD6."""
        self.assertEqual(crslin._calculate_pid(0x16), 0xD6)

    def test_calculate_pid_0x17(self):
        self.assertEqual(crslin._calculate_pid(0x17), 0x97)

    def test_wop_enum_values(self):
        """Classe WOp avec les bons codes opération."""
        self.assertEqual(crslin.WOp.OFF,        0)
        self.assertEqual(crslin.WOp.TOUCH,      1)
        self.assertEqual(crslin.WOp.SPEED1,     2)
        self.assertEqual(crslin.WOp.SPEED2,     3)
        self.assertEqual(crslin.WOp.AUTO,       4)
        self.assertEqual(crslin.WOp.FRONT_WASH, 5)
        self.assertEqual(crslin.WOp.REAR_WASH,  6)
        self.assertEqual(crslin.WOp.REAR_WIPE,  7)

    def test_stick_valid_flag(self):
        self.assertEqual(crslin.STICK_VALID, 0x01)

    def test_fault_none_code(self):
        self.assertEqual(crslin.FAULT_NONE, 0x00)

    def test_fault_names_mapping(self):
        self.assertIn(crslin.FAULT_STICK_SENSOR, crslin.FAULT_NAMES)
        self.assertIn(crslin.FAULT_SUPPLY,       crslin.FAULT_NAMES)
        self.assertIn(crslin.FAULT_INTERNAL_COM, crslin.FAULT_NAMES)

    def test_lin_checksum_enhanced(self):
        """_lin_checksum(pid, data) : somme avec carry-around, complément."""
        pid  = crslin.LIN_PID_16
        data = bytes([0x02, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        cs   = crslin._lin_checksum(pid, data)
        self.assertIsInstance(cs, int)
        self.assertIn(cs, range(256))
        # Vérification : sum(pid, data...) + cs = 0xFF (modulo carry)
        s = pid + sum(data) + cs
        while s > 0xFF: s = (s & 0xFF) + (s >> 8)
        self.assertEqual(s, 0xFF)

    def test_lin_checksum_varies_with_data(self):
        """Données différentes → checksum différent."""
        pid  = crslin.LIN_PID_16
        cs1  = crslin._lin_checksum(pid, bytes([0x01]*8))
        cs2  = crslin._lin_checksum(pid, bytes([0x02]*8))
        self.assertNotEqual(cs1, cs2)

    def test_node_state_initial_op(self):
        """NodeState initial = WOp.OFF."""
        st = crslin.NodeState()
        self.assertEqual(st.wiper_op, crslin.WOp.OFF)

    def test_node_state_set_op(self):
        st = crslin.NodeState()
        st.set_op(crslin.WOp.SPEED1)
        self.assertEqual(st.wiper_op, crslin.WOp.SPEED1)

    def test_node_state_inc_alive(self):
        st = crslin.NodeState()
        st.inc_alive()
        self.assertEqual(st.alive_ctr, 1)

    def test_node_state_alive_wraps_at_255(self):
        """inc_alive() wrap de 255 → 0."""
        st = crslin.NodeState()
        st.alive_ctr = 255
        st.inc_alive()
        self.assertEqual(st.alive_ctr, 0)

    def test_node_state_snapshot(self):
        st = crslin.NodeState()
        st.set_op(crslin.WOp.SPEED2)
        op, ss, alive, fault = st.snapshot()
        self.assertEqual(op,    crslin.WOp.SPEED2)
        self.assertEqual(ss,    crslin.STICK_VALID)
        self.assertEqual(fault, crslin.FAULT_NONE)

    def test_set_lin_paused(self):
        """_set_lin_paused(True) → _is_lin_paused() retourne True."""
        crslin._set_lin_paused(False)
        self.assertFalse(crslin._is_lin_paused())
        crslin._set_lin_paused(True)
        self.assertTrue(crslin._is_lin_paused())
        crslin._set_lin_paused(False)  # restore

    def test_all_wop_encodable_in_snapshot(self):
        """Tous les WOp peuvent être stockés dans NodeState."""
        for op in crslin.WOp:
            st = crslin.NodeState()
            st.set_op(op)
            snap = st.snapshot()
            self.assertEqual(snap[0], op)


# ═════════════════════════════════════════════════════════════════════════════
# TEST BCM CAN NODE
# ═════════════════════════════════════════════════════════════════════════════
class TestBCMCanLogic(unittest.TestCase):

    def test_can_ids(self):
        import bcmcan
        self.assertEqual(bcmcan._CAN_ID_CMD,    0x200)
        self.assertEqual(bcmcan._CAN_ID_STATUS, 0x201)
        self.assertEqual(bcmcan._CAN_ID_ACK,    0x202)

    def test_tcp_port_default(self):
        import bcmcan
        self.assertEqual(bcmcan._TCP_PORT, 5000)

    def test_can_tx_paused_flag_initial(self):
        """_can_tx_paused est un bool (False au démarrage)."""
        import bcmcan
        self.assertIsInstance(bcmcan._can_tx_paused, bool)

    def test_can_tx_paused_writeable(self):
        """On peut écrire _can_tx_paused directement (variable module)."""
        import bcmcan
        old = bcmcan._can_tx_paused
        bcmcan._can_tx_paused = True
        self.assertTrue(bcmcan._can_tx_paused)
        bcmcan._can_tx_paused = False
        self.assertFalse(bcmcan._can_tx_paused)
        bcmcan._can_tx_paused = old

    def test_build_0x201_length(self):
        """_build_0x201(alive) retourne 8 octets."""
        import bcmcan
        frame = bcmcan._build_0x201(alive=3)
        self.assertEqual(len(frame), 8)

    def test_build_0x201_alive_byte(self):
        """Le 7e octet (index 6) = alive counter."""
        import bcmcan
        frame = bcmcan._build_0x201(alive=7)
        self.assertEqual(frame[6], 7)

    def test_build_0x300_length(self):
        """Trame Vehicle_Status 0x300 = 8 octets."""
        import bcmcan
        self.assertEqual(len(bcmcan._build_0x300()), 8)

    def test_build_0x301_length(self):
        """Trame RainSensorData 0x301 = 8 octets."""
        import bcmcan
        self.assertEqual(len(bcmcan._build_0x301()), 8)

    def test_crc_tx(self):
        """_crc_tx = XOR de tous les octets du payload 7."""
        import bcmcan
        payload = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07])
        expected = 0x01^0x02^0x03^0x04^0x05^0x06^0x07
        self.assertEqual(bcmcan._crc_tx(payload), expected)

    def test_crc_rx(self):
        """_crc_rx = XOR des 3 premiers octets."""
        import bcmcan
        data = bytes([0xAA, 0xBB, 0xCC, 0x00, 0x00, 0x00, 0x00, 0x00])
        self.assertEqual(bcmcan._crc_rx(data), 0xAA ^ 0xBB ^ 0xCC)

    def test_vehicle_state_update(self):
        """VehicleState.update() met à jour les champs."""
        import bcmcan
        vs = bcmcan.VehicleState()
        vs.update(ign=1, rev=0, spd_raw=80)
        ign, rev, spd = vs.snapshot()
        self.assertEqual(ign, 1)
        self.assertEqual(spd, 80)

    def test_rain_state_update(self):
        """RainState.update() met à jour l'intensité."""
        import bcmcan
        rs = bcmcan.RainState()
        rs.update(intensity=45, ok=True)
        intensity, ok = rs.snapshot()
        self.assertEqual(intensity, 45)
        self.assertTrue(ok)

    def test_sensor_state_update(self):
        """SensorState.update() met à jour blade, current, fault."""
        import bcmcan
        ss = bcmcan.SensorState()
        ss.update(blade=75.0, current=0.4, fault=0)
        bp, mc, fs = ss.snapshot()
        self.assertAlmostEqual(bp, 75.0)
        self.assertAlmostEqual(mc, 0.4)
        self.assertEqual(fs, 0)

    def test_wiper_cmd_update(self):
        """WiperCmd.update() met à jour mode, speed, wash, alive."""
        import bcmcan
        wc = bcmcan.WiperCmd()
        wc.update(mode=2, speed=1, wash=0, alive=5)
        mode, speed, wash, alive = wc.snapshot()
        self.assertEqual(mode,  2)
        self.assertEqual(speed, 1)
        self.assertEqual(alive, 5)

    def test_handle_rain_sensor_json(self):
        """_handle_rain_sensor met à jour _rain_state."""
        import bcmcan
        bcmcan._handle_rain_sensor({"rain_intensity": 35})
        intensity, _ = bcmcan._rain_state.snapshot()
        self.assertEqual(intensity, 35)

    def test_handle_vehicle_status_json(self):
        """_handle_vehicle_status met à jour _vehicle_state."""
        import bcmcan
        bcmcan._handle_vehicle_status({"ignition": 1, "vehicle_speed": 90, "reverse_gear": 0})
        ign, rev, spd = bcmcan._vehicle_state.snapshot()
        self.assertEqual(ign, 1)
        self.assertEqual(spd, 90)


# ═════════════════════════════════════════════════════════════════════════════
# TEST WC STATE PARTAGÉ
# ═════════════════════════════════════════════════════════════════════════════
class TestWCStateShared(unittest.TestCase):

    def setUp(self): self.st = _make_wc_state()

    def test_initial_mode_zero(self):       self.assertEqual(self.st.current_mode, 0)
    def test_initial_blade_not_moving(self): self.assertFalse(self.st.blade_moving)

    def test_update_from_bcmcan(self):
        self.st.update_from_bcmcan(2, 1, True, 0.4, 0, False)
        self.assertEqual(self.st.current_mode,  2)
        self.assertEqual(self.st.current_speed, 1)
        self.assertTrue(self.st.blade_moving)
        self.assertAlmostEqual(self.st.motor_current, 0.4)

    def test_snapshot_keys(self):
        for k in ("ignition","wiper_mode","motor_curr","blade_pos","rain","vehicle_spd"):
            self.assertIn(k, self.st.make_snapshot())

    def test_can_timeout_flag(self):
        self.st.update_from_bcmcan(0, 0, False, 0.0, 0, True)
        self.assertTrue(self.st.can_timeout)

    def test_thread_safety(self):
        errors = []
        def w():
            for _ in range(30):
                try: self.st.update_from_bcmcan(2, 1, True, 0.3, 0, False)
                except Exception as e: errors.append(e)
        ts = [threading.Thread(target=w) for _ in range(4)]
        for t in ts: t.start()
        for t in ts: t.join()
        self.assertFalse(errors)


# ═════════════════════════════════════════════════════════════════════════════
# TEST WC DOIP UDS  (WCDoIPServer._process_uds)
# ═════════════════════════════════════════════════════════════════════════════
class TestWCDoIPUDS(unittest.TestCase):
    """Tests des services UDS via WCDoIPServer._process_uds()."""

    def setUp(self):
        self.srv, self.st, self.dtc = _make_doip_server()

    def _uds(self, payload: bytes) -> bytes:
        return self.srv._process_uds(payload)

    # DSC
    def test_dsc_default(self):
        r = self._uds(bytes([0x10,0x01]))
        self.assertEqual(r[0], 0x50); self.assertEqual(r[1], 0x01)

    def test_dsc_extended(self):
        r = self._uds(bytes([0x10,0x03]))
        self.assertEqual(r[0], 0x50)
        with self.st._lock:
            self.assertEqual(self.st._session, 0x03)

    def test_dsc_invalid_nrc(self):
        r = self._uds(bytes([0x10,0x99]))
        self.assertEqual(r[0], 0x7F); self.assertEqual(r[2], 0x12)

    def test_dsc_suppress(self):
        self.assertEqual(self._uds(bytes([0x10,0x81])), b"")

    # RDID  (DIDs réels : F110-F115)
    def test_rdid_f110_current_mode(self):
        self.st.update_from_bcmcan(2, 1, False, 0.0, 0, False)
        r = self._uds(bytes([0x22,0xF1,0x10]))
        self.assertEqual(r[0], 0x62); self.assertEqual(r[3], 2)

    def test_rdid_f111_wiper_speed(self):
        self.st.update_from_bcmcan(2, 1, False, 0.0, 0, False)
        r = self._uds(bytes([0x22,0xF1,0x11]))
        self.assertEqual(r[0], 0x62); self.assertEqual(r[3], 1)

    def test_rdid_f112_blade_moving(self):
        self.st.update_from_bcmcan(2, 1, True, 0.0, 0, False)
        r = self._uds(bytes([0x22,0xF1,0x12]))
        self.assertEqual(r[3], 1)

    def test_rdid_f113_motor_current_ma(self):
        self.st.update_from_bcmcan(2, 1, False, 0.6, 0, False)
        r = self._uds(bytes([0x22,0xF1,0x13]))
        self.assertEqual(r[0], 0x62)
        curr_ma = (r[3] << 8) | r[4]
        self.assertEqual(curr_ma, 600)

    def test_rdid_f114_can_timeout(self):
        self.st.update_from_bcmcan(0, 0, False, 0.0, 0, True)
        r = self._uds(bytes([0x22,0xF1,0x14]))
        self.assertEqual(r[3], 1)

    def test_rdid_unknown_nrc(self):
        r = self._uds(bytes([0x22,0xAB,0xCD]))
        self.assertEqual(r[0], 0x7F); self.assertEqual(r[2], 0x31)

    # RoutineControl  (nécessite DSC Extended)
    def test_rc_requires_extended_session(self):
        """RC 0x31 refusé en session Default."""
        with self.st._lock: self.st._session = wc_doip.DSC_DEFAULT
        r = self._uds(bytes([0x31,0x01,0x02,0x01,0x05]))
        self.assertEqual(r[0], 0x7F); self.assertEqual(r[2], 0x22)

    def test_rc_0201_motor_test_extended(self):
        """RC 0201 accepté en session Extended."""
        with self.st._lock: self.st._session = wc_doip.DSC_EXTENDED
        r = self._uds(bytes([0x31,0x01,0x02,0x01,0x05]))
        self.assertEqual(r[0], 0x71)
        with self.st._lock: self.assertTrue(self.st._test_active)

    def test_rc_stop_clears_test(self):
        with self.st._lock: self.st._session = wc_doip.DSC_EXTENDED
        self._uds(bytes([0x31,0x01,0x02,0x01,0x05]))
        r = self._uds(bytes([0x31,0x02,0x02,0x01]))
        self.assertEqual(r[0], 0x71)
        with self.st._lock: self.assertFalse(self.st._test_active)

    def test_rc_invalid_rid_nrc(self):
        with self.st._lock: self.st._session = wc_doip.DSC_EXTENDED
        r = self._uds(bytes([0x31,0x01,0xFF,0xFF]))
        self.assertEqual(r[0], 0x7F)

    # TesterPresent
    def test_tp_response(self):
        r = self._uds(bytes([0x3E,0x00]))
        self.assertEqual(r[0], 0x7E)

    def test_tp_suppress(self):
        self.assertEqual(self._uds(bytes([0x3E,0x80])), b"")

    # ReadDTC
    def test_rdtc_b2101_found(self):
        self.dtc.set_active("B2101", _snap())
        r = self._uds(bytes([0x19,0x02,0xFF]))
        self.assertEqual(r[0], 0x59)
        self.assertGreater(len(r), 4)

    def test_rdtc_no_active(self):
        r = self._uds(bytes([0x19,0x02,0x00]))
        self.assertEqual(r[0], 0x59)

    # Service inconnu
    def test_unknown_service_nrc(self):
        r = self._uds(bytes([0xAB,0x00]))
        self.assertEqual(r[0], 0x7F); self.assertEqual(r[2], 0x11)


# ═════════════════════════════════════════════════════════════════════════════
# TEST DOIP FRAMING
# ═════════════════════════════════════════════════════════════════════════════
class TestDoIPFraming(unittest.TestCase):

    def setUp(self):
        self.srv, self.st, self.dtc = _make_doip_server()

    def test_hdr_length(self):
        """_hdr() retourne 8 octets."""
        hdr = self.srv._hdr(ptype=0x8001, plen=4)
        self.assertEqual(len(hdr), 8)

    def test_hdr_version(self):
        """Byte[0] = PROTOCOL_VERSION (0x02)."""
        hdr = self.srv._hdr(0x8001, 0)
        self.assertEqual(hdr[0], wc_doip.PROTOCOL_VERSION)

    def test_hdr_inverse_version(self):
        """Byte[1] = ~version & 0xFF."""
        hdr = self.srv._hdr(0x8001, 0)
        self.assertEqual(hdr[1], (~wc_doip.PROTOCOL_VERSION) & 0xFF)

    def test_hdr_payload_type(self):
        """Bytes[2:4] = payload type big-endian."""
        hdr = self.srv._hdr(0x8001, 0)
        self.assertEqual(struct.unpack(">H", hdr[2:4])[0], 0x8001)

    def test_hdr_payload_length(self):
        """Bytes[4:8] = longueur payload big-endian."""
        hdr = self.srv._hdr(0x8001, 42)
        self.assertEqual(struct.unpack(">I", hdr[4:8])[0], 42)

    def test_vehicle_id_response_structure(self):
        """_vehicle_id_response() contient header + VIN + LogicalAddr."""
        resp = self.srv._vehicle_id_response()
        # header(8) + VIN(17) + addr(2) + padding(8) = 35
        self.assertGreaterEqual(len(resp), 8 + 17 + 2)

    def test_vehicle_id_response_ptype(self):
        """Type dans vehicle_id_response = DOIP_VEHICLE_ID_RES."""
        resp = self.srv._vehicle_id_response()
        ptype = struct.unpack(">H", resp[2:4])[0]
        self.assertEqual(ptype, wc_doip.DOIP_VEHICLE_ID_RES)

    def test_nrc_format(self):
        """_nrc(sid, code) = [0x7F, sid, code]."""
        r = self.srv._nrc(0x22, 0x31)
        self.assertEqual(list(r), [0x7F, 0x22, 0x31])

    def test_parse_hdr_valid(self):
        """_parse_hdr() décode correctement un header valide."""
        hdr     = self.srv._hdr(0x0001, 0)
        ptype, payload = self.srv._parse_hdr(hdr)
        self.assertEqual(ptype, 0x0001)

    def test_parse_hdr_too_short(self):
        """_parse_hdr() retourne (None,None) si données trop courtes."""
        ptype, payload = self.srv._parse_hdr(bytes([0x02, 0xFD]))
        self.assertIsNone(ptype)


if __name__ == "__main__":
    unittest.main(verbosity=2)