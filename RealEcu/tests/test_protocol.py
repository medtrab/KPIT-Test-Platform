#!/usr/bin/env python3
"""
test_protocol.py
================
Tests unitaires -- bcm_protocol.py (Couche Protocole)

Fonctionnalites testees :
  - calculate_pid() : calcul PID LIN ISO 17987 (valeurs connues)
  - lin_checksum()  : checksum LIN Enhanced
  - Constantes DoIP (ports, adresses, types)
  - Constantes UDS SIDs exportes
  - _doip_header() : structure header 8 bytes
  - _doip_parse()  : parsing header DoIP
  - _decode_uds_request() : dechiffrage UDS lisible
  - _decode_uds_response(): dechiffrage reponse UDS
  - _can_process_0x300()  : trame CAN Vehicle_Status → RTE
  - _can_process_0x301()  : trame CAN RainSensor → RTE
  - _doip_dispatch_uds()  : dispatch UDS complet via RTE
  - ProtocolLayer init    : socket TCP bind OK
"""

import sys
import os
import socket
import struct
import threading
import time
import shutil
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

from bcm_protocol import (
    ProtocolLayer,
    calculate_pid, lin_checksum,
    DOIP_PORT, PROTOCOL_VERSION,
    DOIP_VEHICLE_ID_REQ, DOIP_VEHICLE_ID_RES,
    DOIP_ROUTING_ACT_REQ, DOIP_ROUTING_ACT_RES,
    DOIP_DIAGNOSTIC_MSG, DOIP_ALIVE_CHECK_REQ, DOIP_ALIVE_CHECK_RES,
    BCM_ADDR, TESTER_ADDR, VIN,
    SID_DSC, SID_RESET, SID_CLEAR, SID_RDTC,
    SID_RDID, SID_WDID, SID_SA, SID_CC, SID_RC, SID_TP,
    DSC_DEFAULT, DSC_EXTENDED,
    _decode_uds_request, _decode_uds_response, _fmt_hex,
)
from bcm_rte import (
    RTE, WOP_OFF, WOP_SPEED1, WOP_SPEED2,
    LIN_PID_0x16, LIN_PID_0x17,
    ST_OFF,
)
from dtc_manager import DTCManager

PASS = 0
FAIL = 0

_ORIG_DB = os.path.join(os.path.dirname(__file__), '../src/dtc_database.json')

def _make_dtc():
    tmp = tempfile.mktemp(suffix=".json")
    shutil.copy(_ORIG_DB, tmp)
    return DTCManager(filepath=tmp)

def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  [PASS] {name}")
        PASS += 1
    else:
        print(f"  [FAIL] {name}" + (f" -- {detail}" if detail else ""))
        FAIL += 1


# ─────────────────────────────────────────────────────────
# TEST 1 : calculate_pid() -- ISO 17987
# ─────────────────────────────────────────────────────────
def test_calculate_pid():
    print("\n[TEST] calculate_pid() -- ISO 17987")
    # Valeurs connues de la spec
    check("PID(0x16)=LIN_PID_0x16(0xD6)", calculate_pid(0x16) == LIN_PID_0x16)
    check("PID(0x17)=LIN_PID_0x17(0x97)", calculate_pid(0x17) == LIN_PID_0x17)
    check("PID(0x00)=0x00",               calculate_pid(0x00) == 0x00)
    check("PID(0x01)=0x41",               calculate_pid(0x01) == 0x41)
    check("PID(0x3F)=0xFF",               calculate_pid(0x3F) == 0xFF)
    # Resultat toujours 1 byte
    for fid in range(64):
        pid = calculate_pid(fid)
        check(f"PID(0x{fid:02X}) 0-255", 0 <= pid <= 0xFF)

    # Frame ID > 6 bits → ValueError
    raised = False
    try:
        calculate_pid(0x40)
    except ValueError:
        raised = True
    check("PID(0x40) → ValueError", raised)


# ─────────────────────────────────────────────────────────
# TEST 2 : lin_checksum()
# ─────────────────────────────────────────────────────────
def test_lin_checksum():
    print("\n[TEST] lin_checksum() -- Enhanced (ISO 17987)")
    # Propriete : checksum + verification = 0xFF apres carry-around
    def verify(pid, data):
        cs  = lin_checksum(pid, data)
        tot = pid + cs
        for b in data:
            tot += b
        while tot > 0xFF:
            tot -= 0xFF
        return tot == 0xFF

    check("verify PID=0xD6 data=[0x02,0x03]",
          verify(LIN_PID_0x16, bytes([0x02, 0x03])))
    check("verify PID=0x97 data=[0x00,0x00]",
          verify(LIN_PID_0x17, bytes([0x00, 0x00])))
    check("verify PID=0x00 data=[]",
          verify(0x00, b""))
    check("checksum est 1 byte (0-255)",
          0 <= lin_checksum(0xD6, bytes([0x05, 0x07])) <= 0xFF)


# ─────────────────────────────────────────────────────────
# TEST 3 : Constantes DoIP
# ─────────────────────────────────────────────────────────
def test_doip_constants():
    print("\n[TEST] Constantes DoIP")
    check("DOIP_PORT=13400",            DOIP_PORT == 13400)
    check("PROTOCOL_VERSION=0x02",      PROTOCOL_VERSION == 0x02)
    check("BCM_ADDR=0x0700",            BCM_ADDR == 0x0700)
    check("TESTER_ADDR=0x07DF",         TESTER_ADDR == 0x07DF)
    check("VIN=17 bytes",               len(VIN) == 17)
    check("DOIP_DIAGNOSTIC_MSG=0x8001", DOIP_DIAGNOSTIC_MSG == 0x8001)
    check("DOIP_ROUTING_ACT_REQ=0x0005",DOIP_ROUTING_ACT_REQ == 0x0005)
    check("DOIP_ROUTING_ACT_RES=0x0006",DOIP_ROUTING_ACT_RES == 0x0006)


# ─────────────────────────────────────────────────────────
# TEST 4 : Constantes UDS SIDs
# ─────────────────────────────────────────────────────────
def test_uds_sid_constants():
    print("\n[TEST] Constantes UDS SIDs")
    check("SID_DSC=0x10",   SID_DSC   == 0x10)
    check("SID_RESET=0x11", SID_RESET == 0x11)
    check("SID_CLEAR=0x14", SID_CLEAR == 0x14)
    check("SID_RDTC=0x19",  SID_RDTC  == 0x19)
    check("SID_RDID=0x22",  SID_RDID  == 0x22)
    check("SID_WDID=0x2E",  SID_WDID  == 0x2E)
    check("SID_SA=0x27",    SID_SA    == 0x27)
    check("SID_CC=0x28",    SID_CC    == 0x28)
    check("SID_RC=0x31",    SID_RC    == 0x31)
    check("SID_TP=0x3E",    SID_TP    == 0x3E)
    check("DSC_DEFAULT=0x01",  DSC_DEFAULT  == 0x01)
    check("DSC_EXTENDED=0x03", DSC_EXTENDED == 0x03)


# ─────────────────────────────────────────────────────────
# TEST 5 : _doip_header() et _doip_parse()
# ─────────────────────────────────────────────────────────
def test_doip_header_parse():
    print("\n[TEST] _doip_header() et _doip_parse()")
    rte = RTE()
    dtc = _make_dtc()
    pl  = ProtocolLayer(rte, dtc)

    # header : 8 bytes
    hdr = pl._doip_header(DOIP_DIAGNOSTIC_MSG, 10)
    check("header = 8 bytes",        len(hdr) == 8)
    check("header version=0x02",     hdr[0] == PROTOCOL_VERSION)
    check("header inv version OK",   hdr[1] == ((~PROTOCOL_VERSION) & 0xFF))
    ptype, plen = struct.unpack(">HL", hdr[2:8])
    check("header ptype=DIAG_MSG",   ptype == DOIP_DIAGNOSTIC_MSG)
    check("header plen=10",          plen == 10)

    # parse trame valide
    payload = b"\x01\x02\x03\x04\x05"
    frame   = pl._doip_header(DOIP_ROUTING_ACT_REQ, len(payload)) + payload
    parsed  = pl._doip_parse(frame)
    check("parse retourne tuple",    parsed is not None)
    ptype2, payload2 = parsed
    check("parse ptype correct",     ptype2 == DOIP_ROUTING_ACT_REQ)
    check("parse payload correct",   payload2 == payload)

    # parse trame trop courte → None
    check("parse <8 bytes → None",   pl._doip_parse(b"\x02\xFD") is None)

    pl._srv_sock.close()


# ─────────────────────────────────────────────────────────
# TEST 6 : _fmt_hex()
# ─────────────────────────────────────────────────────────
def test_fmt_hex():
    print("\n[TEST] _fmt_hex()")
    check("vide → '(vide)'",         _fmt_hex(b"") == "(vide)")
    check("1 byte → '01'",           _fmt_hex(b"\x01") == "01")
    check("3 bytes espacees",        _fmt_hex(bytes([0xAB, 0xCD, 0xEF])) == "AB CD EF")
    # Troncature si > max_bytes
    long_data = bytes(range(20))
    r = _fmt_hex(long_data, max_bytes=4)
    check("troncature avec ...",     "..." in r and "20 octets" in r)


# ─────────────────────────────────────────────────────────
# TEST 7 : _decode_uds_request()
# ─────────────────────────────────────────────────────────
def test_decode_uds_request():
    print("\n[TEST] _decode_uds_request()")
    # DSC
    r = _decode_uds_request(bytes([SID_DSC, DSC_DEFAULT]))
    check("DSC decode contient 'DiagnosticSessionControl'", "DiagnosticSessionControl" in r)

    # ECUReset
    r = _decode_uds_request(bytes([SID_RESET, 0x01]))
    check("ECUReset decode contient 'ECUReset'", "ECUReset" in r)

    # RDID
    r = _decode_uds_request(bytes([SID_RDID, 0xF1, 0x00]))
    check("RDID decode contient 'DID=0xF100'", "F100" in r)

    # TesterPresent sans suppress
    r = _decode_uds_request(bytes([SID_TP, 0x00]))
    check("TesterPresent decode OK", "TesterPresent" in r)

    # TesterPresent avec suppress
    r = _decode_uds_request(bytes([SID_TP, 0x80]))
    check("TesterPresent suppressResp", "suppressResp" in r)

    # Payload vide
    r = _decode_uds_request(b"")
    check("vide → '(vide)'", r == "(vide)")


# ─────────────────────────────────────────────────────────
# TEST 8 : _decode_uds_response()
# ─────────────────────────────────────────────────────────
def test_decode_uds_response():
    print("\n[TEST] _decode_uds_response()")
    # NRC
    r = _decode_uds_response(bytes([0x7F, SID_DSC, 0x22]))
    check("NRC decode contient 'NRC'",            "NRC" in r)
    check("NRC decode contient 'conditionsNotCorrect'", "conditionsNotCorrect" in r)

    # DSC+
    r = _decode_uds_response(bytes([0x50, DSC_EXTENDED]))
    check("DSC+ decode contient 'DSC+'", "DSC+" in r)
    check("DSC+ contient 'Extended'",    "Extended" in r)

    # RDID+
    r = _decode_uds_response(bytes([0x62, 0xF1, 0x00, 0x02]))
    check("RDID+ decode contient '0xF100'", "F100" in r)

    # ClearDTC+
    r = _decode_uds_response(bytes([0x54]))
    check("ClearDTC+ decode OK", "54" in r or "Clear" in r)

    # TesterPresent+
    r = _decode_uds_response(bytes([0x7E, 0x00]))
    check("TesterPresent+ decode OK", "TesterPresent" in r)

    # Vide
    r = _decode_uds_response(b"")
    check("vide → '(vide)'", r == "(vide)")


# ─────────────────────────────────────────────────────────
# TEST 9 : _can_process_0x300() -- trame Vehicle_Status
# ─────────────────────────────────────────────────────────
def test_can_process_0x300():
    print("\n[TEST] _can_process_0x300() -- CAN Vehicle_Status")
    rte = RTE()
    dtc = _make_dtc()
    pl  = ProtocolLayer(rte, dtc)

    # Ignition ON, reverse OFF, speed 100 km/h
    data = bytes([0x01, 0x00, 0x00, 0x64])
    pl._can_process_0x300(data)
    check("ignition_status=1",      rte.ignition_status == 1)
    check("reverse_gear=False",     rte.reverse_gear == False)
    check("vehicle_speed=100",      rte.vehicle_speed == 100)

    # Ignition OFF, reverse ON, speed 0
    data2 = bytes([0x00, 0x01, 0x00, 0x00])
    pl._can_process_0x300(data2)
    check("ignition_status=0",      rte.ignition_status == 0)
    check("reverse_gear=True",      rte.reverse_gear == True)
    check("vehicle_speed=0",        rte.vehicle_speed == 0)

    # Trame trop courte → pas de crash
    try:
        pl._can_process_0x300(bytes([0x01]))
        check("trame courte 0x300 : pas de crash", True)
    except Exception as e:
        check("trame courte 0x300 : pas de crash", False, str(e))

    pl._srv_sock.close()


# ─────────────────────────────────────────────────────────
# TEST 10 : _can_process_0x301() -- trame RainSensor
# ─────────────────────────────────────────────────────────
def test_can_process_0x301():
    print("\n[TEST] _can_process_0x301() -- CAN RainSensor")
    rte = RTE()
    dtc = _make_dtc()
    pl  = ProtocolLayer(rte, dtc)

    # Pluie=45, capteur OK (status=0)
    pl._can_process_0x301(bytes([0x2D, 0x00]))
    check("rain_intensity=45",      rte.rain_intensity == 45)
    check("rain_sensor_ok=True",    rte.rain_sensor_ok == True)

    # Pluie=0, capteur KO (status!=0)
    pl._can_process_0x301(bytes([0x00, 0x01]))
    check("rain_intensity=0",       rte.rain_intensity == 0)
    check("rain_sensor_ok=False",   rte.rain_sensor_ok == False)

    # Pluie max=100
    pl._can_process_0x301(bytes([0x64, 0x00]))
    check("rain_intensity=100",     rte.rain_intensity == 100)

    # Trame trop courte → pas de crash
    try:
        pl._can_process_0x301(bytes([0x10]))
        check("trame courte 0x301 : pas de crash", True)
    except Exception as e:
        check("trame courte 0x301 : pas de crash", False, str(e))

    pl._srv_sock.close()


# ─────────────────────────────────────────────────────────
# TEST 11 : _doip_dispatch_uds() -- dispatch complet
# ─────────────────────────────────────────────────────────
def test_doip_dispatch_uds():
    print("\n[TEST] _doip_dispatch_uds() -- dispatch UDS complet")
    rte = RTE()
    dtc = _make_dtc()
    pl  = ProtocolLayer(rte, dtc)

    # Simuler T-DIAG qui traite la requete UDS en background
    def fake_diag():
        """Simule thread T-DIAG : attend event, ecrit reponse."""
        for _ in range(50):          # 50 x 20ms = 1s max
            if rte.uds_request_pending:
                sid  = rte.uds_payload[0] if rte.uds_payload else 0
                rte.set_multi(
                    uds_response        = bytes([sid + 0x40, 0x01]),
                    uds_response_ready  = True,
                    uds_request_pending = False,
                )
                return
            time.sleep(0.020)

    # Payload DoIP = [SA 2B][TA 2B][UDS...]
    uds     = bytes([SID_TP, 0x00])   # TesterPresent sans suppress
    payload = struct.pack(">HH", TESTER_ADDR, BCM_ADDR) + uds

    t = threading.Thread(target=fake_diag, daemon=True)
    t.start()
    resp = pl._doip_dispatch_uds(payload)
    t.join(timeout=2)

    check("dispatch retourne des bytes", isinstance(resp, bytes))
    check("resp non vide",               len(resp) >= 4)
    # Les 4 premiers bytes = [BCM_ADDR(2B)][TESTER_ADDR(2B)]
    src_r, dst_r = struct.unpack(">HH", resp[:4])
    check("resp src=BCM_ADDR",  src_r == BCM_ADDR)
    check("resp dst=TESTER",    dst_r == TESTER_ADDR)

    # Payload trop court → retourne b""
    resp_short = pl._doip_dispatch_uds(bytes([0x07, 0xDF]))
    check("payload <5 → b''",  resp_short == b"")

    pl._srv_sock.close()


# ─────────────────────────────────────────────────────────
# TEST 12 : ProtocolLayer init -- socket TCP bind
# ─────────────────────────────────────────────────────────
def test_protocol_init():
    print("\n[TEST] ProtocolLayer init -- socket TCP bind")
    rte = RTE()
    dtc = _make_dtc()
    try:
        pl = ProtocolLayer(rte, dtc)
        check("socket TCP cree",    pl._srv_sock is not None)
        check("_running=False",     pl._running == False)
        check("lin_port=None init", pl.lin_port is None)
        check("can_bus=None init",  pl.can_bus is None)
        pl._srv_sock.close()
        check("socket ferme OK",    True)
    except Exception as e:
        check("ProtocolLayer init sans crash", False, str(e))


# ─────────────────────────────────────────────────────────
# BILAN
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  TEST SUITE : bcm_protocol.py (Couche Protocole)")
    print("=" * 55)
    test_calculate_pid()
    test_lin_checksum()
    test_doip_constants()
    test_uds_sid_constants()
    test_doip_header_parse()
    test_fmt_hex()
    test_decode_uds_request()
    test_decode_uds_response()
    test_can_process_0x300()
    test_can_process_0x301()
    test_doip_dispatch_uds()
    test_protocol_init()
    print()
    print("=" * 55)
    print(f"  RESULTAT : {PASS} PASS  |  {FAIL} FAIL")
    print("=" * 55)
    sys.exit(0 if FAIL == 0 else 1)
