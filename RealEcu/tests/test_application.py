#!/usr/bin/env python3
"""
test_application.py
===================
Tests unitaires -- bcm_application.py (Couche Application)

Fonctionnalites testees :
  - Machine d'etat WSM : transitions ST_OFF → ST_SPEED1/SPEED2/TOUCH/AUTO...
  - Transition vers ST_ERROR sur ST_OFF (ignition=0)
  - LIN timeout → retour ST_OFF
  - Pompe : _pump_start() / _pump_stop()
  - Securite : _check_pump_protection() (max runtime)
  - Securite : _check_overcurrent() (moteur avant/arriere)
  - Securite : _check_pump_overcurrent()
  - Handlers UDS : DSC, RESET, RDID, SA, CC, TP, WDID
  - Security Access : seed/key + cle incorrecte
  - CommunicationControl : enable/disable
  - start() / stop() cycle de vie
"""

import sys
import os
import time
import threading
import shutil
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

from bcm_rte import (
    RTE,
    ST_OFF, ST_TOUCH, ST_SPEED1, ST_SPEED2, ST_AUTO,
    ST_WASH_FRONT, ST_WASH_REAR, ST_REAR_WIPE, ST_ERROR, ST_DIAG,
    WOP_OFF, WOP_TOUCH, WOP_SPEED1, WOP_SPEED2,
    WOP_AUTO, WOP_FRONT_WASH, WOP_REAR_WASH, WOP_REAR_WIPE,
    OVERCURRENT_THRESH, PUMP_OVERCURRENT_THRESH,
    PUMP_MAX_RUNTIME, TOUCH_DURATION, OVERCURRENT_DELAY,
)
from bcm_application import ApplicationLayer
from dtc_manager import DTCManager, STATUS_ACTIVE
from bcm_protocol import (
    SID_DSC, SID_RESET, SID_RDID, SID_SA, SID_CC, SID_TP,
    SID_WDID, SID_RC,
    DSC_DEFAULT, DSC_EXTENDED,
)

PASS = 0
FAIL = 0

_ORIG_DB = os.path.join(os.path.dirname(__file__), '../src/dtc_database.json')

def _make_dtc():
    tmp = tempfile.mktemp(suffix=".json")
    shutil.copy(_ORIG_DB, tmp)
    d = DTCManager(filepath=tmp)
    d.clear_all()
    return d

def _make_app():
    """Cree un RTE + DTCManager + ApplicationLayer frais."""
    rte = RTE()
    dtc = _make_dtc()
    app = ApplicationLayer(rte, dtc)
    return rte, dtc, app

def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  [PASS] {name}")
        PASS += 1
    else:
        print(f"  [FAIL] {name}" + (f" -- {detail}" if detail else ""))
        FAIL += 1


# ─────────────────────────────────────────────────────────
# TEST 1 : Machine d'etat -- ST_OFF → ST_SPEED1
# ─────────────────────────────────────────────────────────
def test_wsm_off_to_speed1():
    print("\n[TEST] WSM : ST_OFF → ST_SPEED1")
    rte, dtc, app = _make_app()
    rte.set("crs_wiper_op", WOP_SPEED1)
    app._update_state_machine()
    check("state=ST_SPEED1",         rte.state == ST_SPEED1)
    check("front_motor_on=True",     rte.front_motor_on == True)
    check("front_motor_speed=1",     rte.front_motor_speed == 1)
    check("front_blade_moving=True", rte.front_blade_moving == True)


# ─────────────────────────────────────────────────────────
# TEST 2 : Machine d'etat -- ST_OFF → ST_SPEED2
# ─────────────────────────────────────────────────────────
def test_wsm_off_to_speed2():
    print("\n[TEST] WSM : ST_OFF → ST_SPEED2")
    rte, dtc, app = _make_app()
    rte.set("crs_wiper_op", WOP_SPEED2)
    app._update_state_machine()
    check("state=ST_SPEED2",         rte.state == ST_SPEED2)
    check("front_motor_speed=2",     rte.front_motor_speed == 2)


# ─────────────────────────────────────────────────────────
# TEST 3 : Machine d'etat -- ST_SPEED1 → ST_OFF (WOP_OFF)
# ─────────────────────────────────────────────────────────
def test_wsm_speed1_to_off():
    print("\n[TEST] WSM : ST_SPEED1 → ST_OFF")
    rte, dtc, app = _make_app()
    rte.set("crs_wiper_op", WOP_SPEED1)
    app._update_state_machine()
    rte.set("crs_wiper_op", WOP_OFF)
    app._update_state_machine()
    check("state=ST_OFF",            rte.state == ST_OFF)
    check("front_motor_on=False",    rte.front_motor_on == False)


# ─────────────────────────────────────────────────────────
# TEST 4 : Machine d'etat -- ST_SPEED1 → ST_SPEED2
# ─────────────────────────────────────────────────────────
def test_wsm_speed1_to_speed2():
    print("\n[TEST] WSM : ST_SPEED1 → ST_SPEED2")
    rte, dtc, app = _make_app()
    rte.set("crs_wiper_op", WOP_SPEED1)
    app._update_state_machine()
    rte.set("crs_wiper_op", WOP_SPEED2)
    app._update_state_machine()
    check("state=ST_SPEED2", rte.state == ST_SPEED2)
    check("front_motor_speed=2", rte.front_motor_speed == 2)


# ─────────────────────────────────────────────────────────
# TEST 5 : Machine d'etat -- TOUCH (one-shot)
# ─────────────────────────────────────────────────────────
def test_wsm_touch():
    print("\n[TEST] WSM : TOUCH one-shot")
    rte, dtc, app = _make_app()
    rte.set_multi(crs_wiper_op=WOP_TOUCH, _one_shot_armed=True)
    app._update_state_machine()
    check("state=ST_TOUCH",          rte.state == ST_TOUCH)
    check("front_motor_on=True",     rte.front_motor_on == True)
    check("front_motor_speed=1",     rte.front_motor_speed == 1)
    check("t_touch_start set",       rte.t_touch_start > 0)


# ─────────────────────────────────────────────────────────
# TEST 6 : Machine d'etat -- TOUCH expire → ST_OFF
# ─────────────────────────────────────────────────────────
def test_wsm_touch_expire():
    print("\n[TEST] WSM : TOUCH expire → ST_OFF")
    rte, dtc, app = _make_app()
    rte.set_multi(
        state          = ST_TOUCH,
        t_touch_start  = time.time() - (TOUCH_DURATION + 0.1),
        front_motor_on = True,
        front_motor_speed = 1,
        front_blade_moving = True,
    )
    app._process_touch()
    check("state=ST_OFF apres expiration", rte.state == ST_OFF)


# ─────────────────────────────────────────────────────────
# TEST 7 : Machine d'etat -- AUTO ignore si pas de capteur
# ─────────────────────────────────────────────────────────
def test_wsm_auto_no_sensor():
    print("\n[TEST] WSM : AUTO ignore si rain_sensor_installed=False")
    rte, dtc, app = _make_app()
    rte.set_multi(rain_sensor_installed=False, crs_wiper_op=WOP_AUTO)
    app._update_state_machine()
    check("state reste ST_OFF", rte.state == ST_OFF)


# ─────────────────────────────────────────────────────────
# TEST 8 : Machine d'etat -- AUTO avec capteur
# ─────────────────────────────────────────────────────────
def test_wsm_auto_with_sensor():
    print("\n[TEST] WSM : AUTO avec rain_sensor_installed=True")
    rte, dtc, app = _make_app()
    rte.set_multi(rain_sensor_installed=True, crs_wiper_op=WOP_AUTO)
    app._update_state_machine()
    check("state=ST_AUTO", rte.state == ST_AUTO)


# ─────────────────────────────────────────────────────────
# TEST 9 : Ignition OFF → ST_OFF force
# ─────────────────────────────────────────────────────────
def test_ignition_off_forces_off():
    print("\n[TEST] Ignition OFF → force ST_OFF")
    rte, dtc, app = _make_app()
    rte.set("crs_wiper_op", WOP_SPEED1)
    app._update_state_machine()
    check("SPEED1 avant ignition OFF", rte.state == ST_SPEED1)

    rte.set("ignition_status", 0)
    app._update_state_machine()
    check("ST_OFF apres ignition=0",   rte.state == ST_OFF)


# ─────────────────────────────────────────────────────────
# TEST 10 : LIN timeout → ST_OFF force
# ─────────────────────────────────────────────────────────
def test_lin_timeout_forces_off():
    print("\n[TEST] LIN timeout → force ST_OFF")
    rte, dtc, app = _make_app()
    rte.set("crs_wiper_op", WOP_SPEED1)
    app._update_state_machine()
    check("SPEED1 avant timeout", rte.state == ST_SPEED1)

    rte.set("lin_timeout_active", True)
    app._update_state_machine()
    check("ST_OFF apres LIN timeout", rte.state == ST_OFF)


# ─────────────────────────────────────────────────────────
# TEST 11 : Pompe start / stop
# ─────────────────────────────────────────────────────────
def test_pump_start_stop():
    print("\n[TEST] Pompe : _pump_start() et _pump_stop()")
    rte, dtc, app = _make_app()

    # Start FWD
    app._pump_start(1)
    check("pump_active=True",         rte.pump_active == True)
    check("pump_direction=1 (FWD)",   rte.pump_direction == 1)
    check("t_pump_start set",         rte.t_pump_start > 0)

    # Stop
    app._pump_stop("test")
    check("pump_active=False apres stop", rte.pump_active == False)
    check("pump_direction=0 apres stop",  rte.pump_direction == 0)

    # Start BWD
    app._pump_start(2)
    check("pump_direction=2 (BWD)", rte.pump_direction == 2)

    # Double stop : pas de crash
    app._pump_stop("double")
    try:
        app._pump_stop("double2")
        check("double stop : pas de crash", True)
    except Exception as e:
        check("double stop : pas de crash", False, str(e))


# ─────────────────────────────────────────────────────────
# TEST 12 : Protection pompe max runtime → DTC B2008
# ─────────────────────────────────────────────────────────
def test_pump_max_runtime():
    print("\n[TEST] Securite : pompe max runtime → B2008")
    rte, dtc, app = _make_app()

    # Pompe active depuis plus de PUMP_MAX_RUNTIME
    rte.set_multi(
        pump_active   = True,
        pump_direction= 1,
        t_pump_start  = time.time() - (PUMP_MAX_RUNTIME + 0.5),
        state         = ST_OFF,   # pas en WASH → protection active
    )
    app._check_pump_protection()
    check("pompe arretee apres max runtime",  rte.pump_active == False)
    check("DTC B2008 actif",
          dtc.dtcs["B2008"]["status"] == STATUS_ACTIVE)


# ─────────────────────────────────────────────────────────
# TEST 13 : Surintensité moteur avant → DTC B2001 + ST_ERROR
# ─────────────────────────────────────────────────────────
def test_overcurrent_front_motor():
    print("\n[TEST] Securite : surintensité moteur avant → B2001 + ST_ERROR")
    rte, dtc, app = _make_app()

    rte.set_multi(
        front_motor_on   = True,
        motor_current_a  = OVERCURRENT_THRESH + 0.1,
        state            = ST_SPEED1,
    )
    # Premiere detection : timer demarre
    app._check_overcurrent()
    check("timer overcurrent demarre",
          "front" in rte.t_overcurrent_start)

    # Simuler delai ecoule
    rte.t_overcurrent_start["front"] = time.time() - (OVERCURRENT_DELAY + 0.1)
    app._check_overcurrent()
    check("state=ST_ERROR apres overcurrent",  rte.state == ST_ERROR)
    check("DTC B2001 actif",
          dtc.dtcs["B2001"]["status"] == STATUS_ACTIVE)


# ─────────────────────────────────────────────────────────
# TEST 14 : Surintensité moteur arriere → DTC B2002 + ST_ERROR
# ─────────────────────────────────────────────────────────
def test_overcurrent_rear_motor():
    print("\n[TEST] Securite : surintensité moteur arriere → B2002 + ST_ERROR")
    rte, dtc, app = _make_app()

    rte.set_multi(
        front_motor_on  = False,
        rear_motor_on   = True,
        motor_current_a = OVERCURRENT_THRESH + 0.2,
        state           = ST_REAR_WIPE,
    )
    app._check_overcurrent()
    rte.t_overcurrent_start["rear"] = time.time() - (OVERCURRENT_DELAY + 0.1)
    app._check_overcurrent()
    check("state=ST_ERROR",          rte.state == ST_ERROR)
    check("DTC B2002 actif",
          dtc.dtcs["B2002"]["status"] == STATUS_ACTIVE)


# ─────────────────────────────────────────────────────────
# TEST 15 : Surintensité pompe → DTC B2003
# ─────────────────────────────────────────────────────────
def test_pump_overcurrent():
    print("\n[TEST] Securite : surintensité pompe → B2003")
    rte, dtc, app = _make_app()
    from bcm_rte import PUMP_OVERCURRENT_DELAY

    rte.set_multi(
        pump_active     = True,
        pump_direction  = 1,
        pump_current_a  = PUMP_OVERCURRENT_THRESH + 0.1,
        t_pump_start    = time.time(),
        state           = ST_OFF,
    )
    app._check_pump_overcurrent()
    check("timer overcurrent pompe demarre",
          rte._pump_overcurrent_start > 0)

    rte.set("_pump_overcurrent_start",
            time.time() - (PUMP_OVERCURRENT_DELAY + 0.1))
    app._check_pump_overcurrent()
    check("pompe arretee apres overcurrent",  rte.pump_active == False)
    check("DTC B2003 actif",
          dtc.dtcs["B2003"]["status"] == STATUS_ACTIVE)


# ─────────────────────────────────────────────────────────
# TEST 16 : Handler UDS DSC (DiagnosticSessionControl)
# ─────────────────────────────────────────────────────────
def test_uds_dsc():
    print("\n[TEST] Handler UDS : DSC (0x10)")
    rte, dtc, app = _make_app()

    # DSC Default
    resp = app._handle_dsc(bytes([SID_DSC, DSC_DEFAULT]))
    check("DSC Default resp[0]=0x50",   resp[0] == 0x50)
    check("DSC Default resp[1]=0x01",   resp[1] == DSC_DEFAULT)
    check("session=1",                  rte._session == DSC_DEFAULT)

    # DSC Extended
    resp2 = app._handle_dsc(bytes([SID_DSC, DSC_EXTENDED]))
    check("DSC Extended resp[0]=0x50",  resp2[0] == 0x50)
    check("DSC Extended resp[1]=0x03",  resp2[1] == DSC_EXTENDED)
    check("session=3",                  rte._session == DSC_EXTENDED)

    # DSC suppress
    resp3 = app._handle_dsc(bytes([SID_DSC, 0x80 | DSC_DEFAULT]))
    check("DSC suppress → b''",         resp3 == b"")

    # Payload court → NRC 0x13
    resp4 = app._handle_dsc(bytes([SID_DSC]))
    check("DSC court → NRC 0x13",       resp4[2] == 0x13)

    # Sous-fonction inconnue → NRC 0x12
    resp5 = app._handle_dsc(bytes([SID_DSC, 0x99]))
    check("DSC sub inconnu → NRC 0x12", resp5[2] == 0x12)


# ─────────────────────────────────────────────────────────
# TEST 17 : Handler UDS ECUReset (0x11)
# ─────────────────────────────────────────────────────────
def test_uds_reset():
    print("\n[TEST] Handler UDS : ECUReset (0x11)")
    rte, dtc, app = _make_app()
    rte.set_multi(_session=DSC_EXTENDED, _sec_level=1)

    resp = app._handle_reset(bytes([SID_RESET, 0x01]))
    check("ECUReset resp[0]=0x51",      resp[0] == 0x51)
    check("ECUReset resp[1]=0x01",      resp[1] == 0x01)
    check("state=ST_OFF apres reset",   rte.state == ST_OFF)
    check("session=1 apres reset",      rte._session == 1)
    check("sec_level=0 apres reset",    rte._sec_level == 0)


# ─────────────────────────────────────────────────────────
# TEST 18 : Handler UDS RDID (0x22)
# ─────────────────────────────────────────────────────────
def test_uds_rdid():
    print("\n[TEST] Handler UDS : RDID (0x22)")
    rte, dtc, app = _make_app()
    rte.set_multi(state=ST_SPEED1, front_motor_speed=1,
                  front_blade_moving=True, motor_current_a=0.35,
                  pump_dir_active=0, rain_intensity=20,
                  rear_motor_running=False)

    # F100 : wiper state
    r = app._handle_rdid(bytes([SID_RDID, 0xF1, 0x00]))
    check("RDID F100 resp[0]=0x62",     r[0] == 0x62)
    check("RDID F100 DID=0xF100",       r[1] == 0xF1 and r[2] == 0x00)

    # F101 : motor speed
    r2 = app._handle_rdid(bytes([SID_RDID, 0xF1, 0x01]))
    check("RDID F101 speed=1",          r2[3] == 1)

    # F102 : blade moving
    r3 = app._handle_rdid(bytes([SID_RDID, 0xF1, 0x02]))
    check("RDID F102 blade_moving=1",   r3[3] == 1)

    # F103 : motor current (mA)
    r4 = app._handle_rdid(bytes([SID_RDID, 0xF1, 0x03]))
    curr_ma = (r4[3] << 8) | r4[4]
    check("RDID F103 current=350mA",    curr_ma == 350)

    # F105 : rain intensity
    r5 = app._handle_rdid(bytes([SID_RDID, 0xF1, 0x05]))
    check("RDID F105 rain=20",          r5[3] == 20)

    # DID inconnu → NRC 0x31
    rn = app._handle_rdid(bytes([SID_RDID, 0xF9, 0x99]))
    check("RDID DID inconnu → NRC 0x31", rn[2] == 0x31)

    # Payload court → NRC 0x13
    rc = app._handle_rdid(bytes([SID_RDID, 0xF1]))
    check("RDID court → NRC 0x13",       rc[2] == 0x13)


# ─────────────────────────────────────────────────────────
# TEST 19 : Handler UDS Security Access (0x27)
# ─────────────────────────────────────────────────────────
def test_uds_security_access():
    print("\n[TEST] Handler UDS : SecurityAccess (0x27)")
    rte, dtc, app = _make_app()

    # Pas en session Extended → NRC 0x7E
    resp_bad = app._handle_sa(bytes([SID_SA, 0x01]))
    check("SA hors session Extended → NRC 0x7E", resp_bad[2] == 0x7E)

    # Passer en session Extended
    rte.set("_session", DSC_EXTENDED)

    # Request Seed
    resp_seed = app._handle_sa(bytes([SID_SA, 0x01]))
    check("SA RequestSeed resp[0]=0x67",  resp_seed[0] == 0x67)
    check("SA RequestSeed resp[1]=0x01",  resp_seed[1] == 0x01)
    seed = (resp_seed[2] << 8) | resp_seed[3]
    check("seed != 0",                    seed != 0)

    # Calculer la cle correcte
    from bcm_rte import SA_XOR_MASK, SA_ADD_MASK
    expected_key = ((seed ^ SA_XOR_MASK) + SA_ADD_MASK) & 0xFFFF
    key_bytes    = bytes([SID_SA, 0x02,
                          (expected_key >> 8) & 0xFF,
                           expected_key & 0xFF])
    resp_key = app._handle_sa(key_bytes)
    check("SA SendKey correct resp[0]=0x67", resp_key[0] == 0x67)
    check("SA SendKey correct resp[1]=0x02", resp_key[1] == 0x02)
    check("sec_level=1 apres cle OK",        rte._sec_level == 1)

    # Cle incorrecte
    rte.set_multi(_session=DSC_EXTENDED, _sec_level=0)
    app._handle_sa(bytes([SID_SA, 0x01]))  # nouveau seed
    resp_bad_key = app._handle_sa(bytes([SID_SA, 0x02, 0xDE, 0xAD]))
    check("SA cle incorrecte → NRC 0x35",    resp_bad_key[2] == 0x35)


# ─────────────────────────────────────────────────────────
# TEST 20 : Handler UDS CommunicationControl (0x28)
# ─────────────────────────────────────────────────────────
def test_uds_cc():
    print("\n[TEST] Handler UDS : CommunicationControl (0x28)")
    rte, dtc, app = _make_app()

    # EnableRxTx (0x00)
    resp = app._handle_cc(bytes([SID_CC, 0x00, 0x01]))
    check("CC EnableRxTx resp[0]=0x68",  resp[0] == 0x68)
    check("_comm_tx_enabled=True",       rte._comm_tx_enabled == True)
    check("_comm_rx_enabled=True",       rte._comm_rx_enabled == True)

    # DisableTx (0x01)
    app._handle_cc(bytes([SID_CC, 0x01, 0x01]))
    check("_comm_tx_enabled=False",      rte._comm_tx_enabled == False)

    # DisableRx (0x02)
    app._handle_cc(bytes([SID_CC, 0x02, 0x01]))
    check("_comm_rx_enabled=False",      rte._comm_rx_enabled == False)

    # DisableRxTx (0x03)
    app._handle_cc(bytes([SID_CC, 0x03, 0x01]))
    check("TX et RX desactives",
          rte._comm_tx_enabled == False and rte._comm_rx_enabled == False)

    # Sous-fonction inconnue → NRC 0x12
    rn = app._handle_cc(bytes([SID_CC, 0x99, 0x01]))
    check("CC sub inconnu → NRC 0x12", rn[2] == 0x12)

    # Payload court → NRC 0x13
    rc = app._handle_cc(bytes([SID_CC, 0x00]))
    check("CC court → NRC 0x13", rc[2] == 0x13)


# ─────────────────────────────────────────────────────────
# TEST 21 : Handler UDS TesterPresent (0x3E)
# ─────────────────────────────────────────────────────────
def test_uds_tp():
    print("\n[TEST] Handler UDS : TesterPresent (0x3E)")
    rte, dtc, app = _make_app()

    # Sans suppress
    resp = app._handle_tp(bytes([SID_TP, 0x00]))
    check("TP resp=[0x7E, 0x00]", resp == bytes([0x7E, 0x00]))

    # Avec suppress (bit 7 = 1)
    resp2 = app._handle_tp(bytes([SID_TP, 0x80]))
    check("TP suppress → b''", resp2 == b"")


# ─────────────────────────────────────────────────────────
# TEST 22 : Handler UDS WDID (0x2E) -- coding
# ─────────────────────────────────────────────────────────
def test_uds_wdid():
    print("\n[TEST] Handler UDS : WDID (0x2E) -- coding")
    rte, dtc, app = _make_app()

    # Pas en session Extended → NRC 0x22
    resp_bad = app._handle_wdid(bytes([SID_WDID, 0xF2, 0x00, 0x01]))
    check("WDID hors session → NRC 0x22", resp_bad[2] == 0x22)

    # Passer en session Extended avec sec_level=1
    rte.set_multi(_session=DSC_EXTENDED, _sec_level=1)

    # F200 : RainSensorInstalled = True
    resp = app._handle_wdid(bytes([SID_WDID, 0xF2, 0x00, 0x01]))
    check("WDID F200 resp[0]=0x6E",        resp[0] == 0x6E)
    check("rain_sensor_installed=True",    rte.rain_sensor_installed == True)

    # F202 : RearWiperAvailable = False
    resp2 = app._handle_wdid(bytes([SID_WDID, 0xF2, 0x02, 0x00]))
    check("WDID F202 rear_wiper=False",    rte.rear_wiper_available == False)

    # DID inconnu → NRC 0x31
    rn = app._handle_wdid(bytes([SID_WDID, 0xFF, 0xFF, 0x01]))
    check("WDID DID inconnu → NRC 0x31",  rn[2] == 0x31)

    # Sec level insuffisant → NRC 0x33
    rte.set("_sec_level", 0)
    rs = app._handle_wdid(bytes([SID_WDID, 0xF2, 0x00, 0x01]))
    check("WDID sans sec_level → NRC 0x33", rs[2] == 0x33)


# ─────────────────────────────────────────────────────────
# TEST 23 : start() / stop() cycle de vie
# ─────────────────────────────────────────────────────────
def test_start_stop():
    print("\n[TEST] ApplicationLayer : start() / stop()")
    rte, dtc, app = _make_app()
    try:
        app.start()
        check("_running=True apres start", app._running == True)
        app.stop()
        check("_running=False apres stop", app._running == False)
    except Exception as e:
        check("start/stop sans crash", False, str(e))


# ─────────────────────────────────────────────────────────
# TEST 24 : _nrc() helper
# ─────────────────────────────────────────────────────────
def test_nrc_helper():
    print("\n[TEST] _nrc() helper")
    rte, dtc, app = _make_app()
    nrc = app._nrc(SID_DSC, 0x22)
    check("NRC[0]=0x7F",      nrc[0] == 0x7F)
    check("NRC[1]=SID_DSC",   nrc[1] == SID_DSC)
    check("NRC[2]=0x22",      nrc[2] == 0x22)
    check("NRC longueur=3",   len(nrc) == 3)


# ─────────────────────────────────────────────────────────
# BILAN
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  TEST SUITE : bcm_application.py (Couche Application)")
    print("=" * 55)
    test_wsm_off_to_speed1()
    test_wsm_off_to_speed2()
    test_wsm_speed1_to_off()
    test_wsm_speed1_to_speed2()
    test_wsm_touch()
    test_wsm_touch_expire()
    test_wsm_auto_no_sensor()
    test_wsm_auto_with_sensor()
    test_ignition_off_forces_off()
    test_lin_timeout_forces_off()
    test_pump_start_stop()
    test_pump_max_runtime()
    test_overcurrent_front_motor()
    test_overcurrent_rear_motor()
    test_pump_overcurrent()
    test_uds_dsc()
    test_uds_reset()
    test_uds_rdid()
    test_uds_security_access()
    test_uds_cc()
    test_uds_tp()
    test_uds_wdid()
    test_start_stop()
    test_nrc_helper()
    print()
    print("=" * 55)
    print(f"  RESULTAT : {PASS} PASS  |  {FAIL} FAIL")
    print("=" * 55)
    sys.exit(0 if FAIL == 0 else 1)
