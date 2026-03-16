#!/usr/bin/env python3
"""
test_rte.py
===========
Tests unitaires -- bcm_rte.py (RTE Runtime Environment)

Fonctionnalites testees :
  - Valeurs initiales de tous les champs RTE
  - get() / set() thread-safe
  - set_multi() modification multiple atomique
  - make_snapshot() contenu et format
  - Acces concurrent thread-safe (lecture/ecriture simultanee)
  - Constantes WOP_* et ST_*
  - Encodage ST_ENC
"""

import sys
import os
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

from bcm_rte import (
    RTE,
    WOP_OFF, WOP_TOUCH, WOP_SPEED1, WOP_SPEED2,
    WOP_AUTO, WOP_FRONT_WASH, WOP_REAR_WASH, WOP_REAR_WIPE,
    WOP_NAMES,
    ST_OFF, ST_TOUCH, ST_SPEED1, ST_SPEED2, ST_AUTO,
    ST_WASH_FRONT, ST_WASH_REAR, ST_REAR_WIPE,
    ST_ERROR, ST_DIAG, ST_ENC,
    OVERCURRENT_THRESH, PUMP_OVERCURRENT_THRESH,
    PUMP_MAX_RUNTIME, TOUCH_DURATION,
)

PASS = 0
FAIL = 0

def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  [PASS] {name}")
        PASS += 1
    else:
        print(f"  [FAIL] {name}" + (f" -- {detail}" if detail else ""))
        FAIL += 1


# ─────────────────────────────────────────────────────────
# TEST 1 : Valeurs initiales
# ─────────────────────────────────────────────────────────
def test_initial_values():
    print("\n[TEST] Valeurs initiales RTE")
    rte = RTE()

    # CAN
    check("ignition_status=1",      rte.ignition_status == 1)
    check("reverse_gear=False",     rte.reverse_gear == False)
    check("vehicle_speed=0",        rte.vehicle_speed == 0)
    check("rain_intensity=0",       rte.rain_intensity == 0)
    check("rain_sensor_ok=True",    rte.rain_sensor_ok == True)

    # LIN
    check("crs_wiper_op=WOP_OFF",   rte.crs_wiper_op == WOP_OFF)
    check("crs_stick_valid=True",   rte.crs_stick_valid == True)
    check("lin_timeout_active=False", rte.lin_timeout_active == False)

    # WSM
    check("state=ST_OFF",           rte.state == ST_OFF)
    check("prev_state=ST_OFF",      rte.prev_state == ST_OFF)

    # Pompe
    check("pump_active=False",      rte.pump_active == False)
    check("pump_direction=0",       rte.pump_direction == 0)

    # Actionneurs
    check("front_motor_on=False",   rte.front_motor_on == False)
    check("front_motor_speed=0",    rte.front_motor_speed == 0)
    check("front_blade_moving=False", rte.front_blade_moving == False)
    check("rear_motor_on=False",    rte.rear_motor_on == False)
    check("rear_motor_running=False", rte.rear_motor_running == False)

    # Courant
    check("motor_current_a=0.0",    rte.motor_current_a == 0.0)
    check("pump_current_a=0.0",     rte.pump_current_a == 0.0)
    check("pump_voltage_v=0.0",     rte.pump_voltage_v == 0.0)

    # UDS
    check("uds_request_pending=False", rte.uds_request_pending == False)
    check("uds_response_ready=False",  rte.uds_response_ready == False)
    check("uds_payload=b''",           rte.uds_payload == b"")
    check("_session=1",                rte._session == 1)
    check("_sec_level=0",              rte._sec_level == 0)

    # Coding
    check("rain_sensor_installed=False", rte.rain_sensor_installed == False)
    check("rear_wiper_available=True",   rte.rear_wiper_available == True)


# ─────────────────────────────────────────────────────────
# TEST 2 : get() / set()
# ─────────────────────────────────────────────────────────
def test_get_set():
    print("\n[TEST] get() et set() thread-safe")
    rte = RTE()

    rte.set("state", ST_SPEED1)
    check("set state=ST_SPEED1",    rte.get("state") == ST_SPEED1)

    rte.set("rain_intensity", 75)
    check("set rain_intensity=75",  rte.get("rain_intensity") == 75)

    rte.set("motor_current_a", 0.55)
    check("set motor_current_a=0.55", abs(rte.get("motor_current_a") - 0.55) < 1e-6)

    rte.set("pump_active", True)
    check("set pump_active=True",   rte.get("pump_active") == True)

    rte.set("crs_wiper_op", WOP_SPEED2)
    check("set crs_wiper_op=WOP_SPEED2", rte.get("crs_wiper_op") == WOP_SPEED2)


# ─────────────────────────────────────────────────────────
# TEST 3 : set_multi()
# ─────────────────────────────────────────────────────────
def test_set_multi():
    print("\n[TEST] set_multi() modification atomique multiple")
    rte = RTE()

    rte.set_multi(
        state             = ST_SPEED2,
        rain_intensity    = 50,
        vehicle_speed     = 90,
        front_motor_on    = True,
        front_motor_speed = 2,
        front_blade_moving= True,
    )
    check("set_multi state=ST_SPEED2",      rte.state == ST_SPEED2)
    check("set_multi rain_intensity=50",    rte.rain_intensity == 50)
    check("set_multi vehicle_speed=90",     rte.vehicle_speed == 90)
    check("set_multi front_motor_on=True",  rte.front_motor_on == True)
    check("set_multi front_motor_speed=2",  rte.front_motor_speed == 2)
    check("set_multi front_blade_moving",   rte.front_blade_moving == True)


# ─────────────────────────────────────────────────────────
# TEST 4 : make_snapshot()
# ─────────────────────────────────────────────────────────
def test_make_snapshot():
    print("\n[TEST] make_snapshot() contenu et types")
    rte = RTE()
    rte.set_multi(
        ignition_status  = 1,
        state            = ST_SPEED1,
        motor_current_a  = 0.350,
        front_blade_moving = True,
        rain_intensity   = 30,
        vehicle_speed    = 80,
    )
    snap = rte.make_snapshot()

    check("snapshot cle ignition",    "ignition"    in snap)
    check("snapshot cle wiper_mode",  "wiper_mode"  in snap)
    check("snapshot cle motor_curr",  "motor_curr"  in snap)
    check("snapshot cle blade_pos",   "blade_pos"   in snap)
    check("snapshot cle rain",        "rain"        in snap)
    check("snapshot cle vehicle_spd", "vehicle_spd" in snap)

    check("snapshot ignition=1",      snap["ignition"] == 1)
    check("snapshot wiper_mode=ST_SPEED1", snap["wiper_mode"] == ST_SPEED1)
    check("snapshot motor_curr=350mA",snap["motor_curr"] == 350)
    check("snapshot blade_pos=1",     snap["blade_pos"] == 1)
    check("snapshot rain=30",         snap["rain"] == 30)
    check("snapshot vehicle_spd=80",  snap["vehicle_spd"] == 80)

    # blade_pos = 0 quand moteur arrete
    rte.set("front_blade_moving", False)
    snap2 = rte.make_snapshot()
    check("snapshot blade_pos=0 quand arrete", snap2["blade_pos"] == 0)


# ─────────────────────────────────────────────────────────
# TEST 5 : Acces concurrent thread-safe
# ─────────────────────────────────────────────────────────
def test_thread_safety():
    print("\n[TEST] Acces concurrent thread-safe")
    rte    = RTE()
    errors = []
    N      = 500

    def writer_state():
        states = [ST_OFF, ST_SPEED1, ST_SPEED2, ST_AUTO, ST_TOUCH]
        for i in range(N):
            try:
                rte.set("state", states[i % len(states)])
            except Exception as e:
                errors.append(f"writer_state: {e}")

    def writer_current():
        for i in range(N):
            try:
                rte.set("motor_current_a", i * 0.001)
            except Exception as e:
                errors.append(f"writer_current: {e}")

    def reader():
        for _ in range(N):
            try:
                _ = rte.get("state")
                _ = rte.get("motor_current_a")
                _ = rte.make_snapshot()
            except Exception as e:
                errors.append(f"reader: {e}")

    threads = [
        threading.Thread(target=writer_state),
        threading.Thread(target=writer_current),
        threading.Thread(target=reader),
        threading.Thread(target=reader),
    ]
    for t in threads: t.start()
    for t in threads: t.join()

    check("zero exceptions concurrentes", len(errors) == 0,
          f"{len(errors)} erreurs: {errors[:3]}")


# ─────────────────────────────────────────────────────────
# TEST 6 : Constantes WOP_*
# ─────────────────────────────────────────────────────────
def test_wop_constants():
    print("\n[TEST] Constantes WOP_* et WOP_NAMES")
    check("WOP_OFF=0x00",        WOP_OFF        == 0x00)
    check("WOP_TOUCH=0x01",      WOP_TOUCH      == 0x01)
    check("WOP_SPEED1=0x02",     WOP_SPEED1     == 0x02)
    check("WOP_SPEED2=0x03",     WOP_SPEED2     == 0x03)
    check("WOP_AUTO=0x04",       WOP_AUTO       == 0x04)
    check("WOP_FRONT_WASH=0x05", WOP_FRONT_WASH == 0x05)
    check("WOP_REAR_WASH=0x06",  WOP_REAR_WASH  == 0x06)
    check("WOP_REAR_WIPE=0x07",  WOP_REAR_WIPE  == 0x07)
    check("WOP_NAMES[0]='OFF'",  WOP_NAMES[0]   == "OFF")
    check("WOP_NAMES[2]='SPEED1'",WOP_NAMES[2]  == "SPEED1")


# ─────────────────────────────────────────────────────────
# TEST 7 : Constantes ST_* et ST_ENC
# ─────────────────────────────────────────────────────────
def test_st_constants():
    print("\n[TEST] Constantes ST_* et ST_ENC")
    check("ST_OFF='OFF'",         ST_OFF    == "OFF")
    check("ST_SPEED1='SPEED1'",   ST_SPEED1 == "SPEED1")
    check("ST_SPEED2='SPEED2'",   ST_SPEED2 == "SPEED2")
    check("ST_ERROR='ERROR'",     ST_ERROR  == "ERROR")
    check("ST_DIAG='DIAG'",       ST_DIAG   == "DIAG")
    check("ST_ENC ST_OFF=0",      ST_ENC[ST_OFF]    == 0)
    check("ST_ENC ST_SPEED1=2",   ST_ENC[ST_SPEED1] == 2)
    check("ST_ENC ST_SPEED2=3",   ST_ENC[ST_SPEED2] == 3)
    check("ST_ENC ST_ERROR=7",    ST_ENC[ST_ERROR]  == 7)
    check("ST_ENC ST_DIAG=9",     ST_ENC[ST_DIAG]   == 9)


# ─────────────────────────────────────────────────────────
# TEST 8 : Seuils calibration
# ─────────────────────────────────────────────────────────
def test_calibration_constants():
    print("\n[TEST] Seuils calibration")
    check("OVERCURRENT_THRESH=0.8",      OVERCURRENT_THRESH      == 0.8)
    check("PUMP_OVERCURRENT_THRESH=0.8", PUMP_OVERCURRENT_THRESH == 0.8)
    check("PUMP_MAX_RUNTIME=5.0",        PUMP_MAX_RUNTIME        == 5.0)
    check("TOUCH_DURATION=1.7",          TOUCH_DURATION          == 1.7)


# ─────────────────────────────────────────────────────────
# TEST 9 : __repr__
# ─────────────────────────────────────────────────────────
def test_repr():
    print("\n[TEST] __repr__ lisible")
    rte = RTE()
    r   = repr(rte)
    check("repr contient state=",    "state=" in r)
    check("repr contient req=",      "req=" in r)
    check("repr contient ign=",      "ign=" in r)
    check("repr contient current=",  "current=" in r)
    check("repr contient pump=",     "pump=" in r)


# ─────────────────────────────────────────────────────────
# BILAN
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  TEST SUITE : bcm_rte.py (RTE)")
    print("=" * 55)
    test_initial_values()
    test_get_set()
    test_set_multi()
    test_make_snapshot()
    test_thread_safety()
    test_wop_constants()
    test_st_constants()
    test_calibration_constants()
    test_repr()
    print()
    print("=" * 55)
    print(f"  RESULTAT : {PASS} PASS  |  {FAIL} FAIL")
    print("=" * 55)
    sys.exit(0 if FAIL == 0 else 1)
