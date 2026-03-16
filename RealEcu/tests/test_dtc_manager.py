#!/usr/bin/env python3
"""
test_dtc_manager.py
===================
Tests unitaires -- dtc_manager.py (Gestionnaire DTC)

Fonctionnalites testees :
  - Chargement base JSON (9 DTCs)
  - set_active()  : statut ACTIVE (0x2F) + compteur + timestamp
  - set_active()  : snapshot et snapshot_records
  - set_active()  : rotation MAX_SNAPSHOT_RECORDS=5
  - set_inactive(): statut INACTIVE (0x2E)
  - clear_all()   : remise a zero complete
  - get_dtcs_by_mask() : filtrage par masque
  - build_response_02(): format reponse UDS 0x19/0x02
  - build_response_04(): format reponse UDS 0x19/0x04 avec snapshot
  - build_response_06(): format reponse UDS 0x19/0x06 occurrence count
  - handle_read_dtc() / handle_clear_dtc() : handlers UDS complets
  - DTC inconnu : pas de crash
"""

import sys
import os
import json
import shutil
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

from dtc_manager import (
    DTCManager,
    STATUS_CLEAN, STATUS_ACTIVE, STATUS_INACTIVE,
    STATUS_AVAILABILITY_MASK, MAX_SNAPSHOT_RECORDS,
    handle_read_dtc, handle_clear_dtc,
)

PASS = 0
FAIL = 0

# ── Fixture : copie temporaire de la base JSON ──────────
_ORIG_DB = os.path.join(os.path.dirname(__file__), '../src/dtc_database.json')

def _make_dtc() -> DTCManager:
    """Retourne un DTCManager avec une copie temporaire propre de la DB."""
    tmp = tempfile.mktemp(suffix=".json")
    shutil.copy(_ORIG_DB, tmp)
    dtc = DTCManager(filepath=tmp)
    dtc.clear_all()   # s'assurer que tout est CLEAN
    return dtc

def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  [PASS] {name}")
        PASS += 1
    else:
        print(f"  [FAIL] {name}" + (f" -- {detail}" if detail else ""))
        FAIL += 1


# ─────────────────────────────────────────────────────────
# TEST 1 : Chargement base
# ─────────────────────────────────────────────────────────
def test_load():
    print("\n[TEST] Chargement base DTC")
    dtc = _make_dtc()
    check("9 DTCs charges",         len(dtc.dtcs) == 9)
    check("B2001 present",          "B2001" in dtc.dtcs)
    check("B2009 present",          "B2009" in dtc.dtcs)
    check("B2004 communication",    dtc.dtcs["B2004"]["category"] == "communication")
    check("B2001 3 bytes",          len(dtc.dtcs["B2001"]["bytes"]) == 3)
    check("B2001 bytes=[178,32,1]", dtc.dtcs["B2001"]["bytes"] == [178, 32, 1])
    check("tous CLEAN au depart",
          all(dtc.dtcs[c]["status"] == STATUS_CLEAN for c in dtc.dtcs))


# ─────────────────────────────────────────────────────────
# TEST 2 : set_active() sans snapshot
# ─────────────────────────────────────────────────────────
def test_set_active_basic():
    print("\n[TEST] set_active() sans snapshot")
    dtc = _make_dtc()
    dtc.set_active("B2001")
    d = dtc.dtcs["B2001"]
    check("status=STATUS_ACTIVE(0x2F)",   d["status"] == STATUS_ACTIVE)
    check("occurrence_count >= 1",        d["occurrence_count"] >= 1)
    check("first_occurrence renseigne",   d["first_occurrence"] is not None)
    check("last_occurrence renseigne",    d["last_occurrence"] is not None)

    # Deuxieme appel : compteur incremente
    dtc.set_active("B2001")
    check("2eme appel occurrence_count=2", dtc.dtcs["B2001"]["occurrence_count"] == 2)


# ─────────────────────────────────────────────────────────
# TEST 3 : set_active() avec snapshot
# ─────────────────────────────────────────────────────────
def test_set_active_with_snapshot():
    print("\n[TEST] set_active() avec snapshot")
    dtc  = _make_dtc()
    snap = {
        "ignition":    1,
        "wiper_mode":  "SPEED1",
        "motor_curr":  850,
        "blade_pos":   50,
        "rain":        30,
        "vehicle_spd": 80,
    }
    dtc.set_active("B2002", snapshot=snap)
    d = dtc.dtcs["B2002"]
    check("status=ACTIVE",                  d["status"] == STATUS_ACTIVE)
    check("snapshot stocke",                d["snapshot"] == snap)
    check("1 snapshot_record cree",         len(d["snapshot_records"]) == 1)

    rec = d["snapshot_records"][0]
    check("record_number=1",                rec["record_number"] == 1)
    check("F190_ignition=1",                rec["data"]["F190_ignition"] == 1)
    check("F191_wiper_mode=SPEED1",         rec["data"]["F191_wiper_mode"] == "SPEED1")
    check("F192_motor_curr=850",            rec["data"]["F192_motor_curr"] == 850)
    check("F193_blade_pos=50",              rec["data"]["F193_blade_pos"] == 50)
    check("F194_rain=30",                   rec["data"]["F194_rain"] == 30)
    check("F195_vehicle_spd=80",            rec["data"]["F195_vehicle_spd"] == 80)


# ─────────────────────────────────────────────────────────
# TEST 4 : Rotation MAX_SNAPSHOT_RECORDS
# ─────────────────────────────────────────────────────────
def test_snapshot_rotation():
    print("\n[TEST] Rotation MAX_SNAPSHOT_RECORDS=5")
    dtc  = _make_dtc()
    snap = {"ignition": 1, "wiper_mode": "OFF",
            "motor_curr": 0, "blade_pos": 0,
            "rain": 0, "vehicle_spd": 0}
    for i in range(8):
        dtc.set_active("B2003", snapshot=snap)
    records = dtc.dtcs["B2003"]["snapshot_records"]
    check(f"max {MAX_SNAPSHOT_RECORDS} records gardes",
          len(records) <= MAX_SNAPSHOT_RECORDS)


# ─────────────────────────────────────────────────────────
# TEST 5 : set_inactive()
# ─────────────────────────────────────────────────────────
def test_set_inactive():
    print("\n[TEST] set_inactive()")
    dtc = _make_dtc()
    dtc.set_active("B2004")
    dtc.set_inactive("B2004")
    check("status=INACTIVE(0x2E)",
          dtc.dtcs["B2004"]["status"] == STATUS_INACTIVE)

    # set_inactive sur DTC CLEAN ne doit pas changer le statut
    dtc2 = _make_dtc()
    dtc2.set_inactive("B2005")
    check("CLEAN reste CLEAN apres set_inactive",
          dtc2.dtcs["B2005"]["status"] == STATUS_CLEAN)


# ─────────────────────────────────────────────────────────
# TEST 6 : clear_all()
# ─────────────────────────────────────────────────────────
def test_clear_all():
    print("\n[TEST] clear_all()")
    dtc = _make_dtc()
    dtc.set_active("B2001")
    dtc.set_active("B2002")
    dtc.set_active("B2003")
    dtc.clear_all()
    for code in dtc.dtcs:
        d = dtc.dtcs[code]
        check(f"{code} status=CLEAN",         d["status"] == STATUS_CLEAN)
        check(f"{code} occurrence_count=0",   d["occurrence_count"] == 0)
        check(f"{code} first_occurrence=None",d["first_occurrence"] is None)
        check(f"{code} snapshot_records=[]",  d["snapshot_records"] == [])


# ─────────────────────────────────────────────────────────
# TEST 7 : get_dtcs_by_mask()
# ─────────────────────────────────────────────────────────
def test_get_dtcs_by_mask():
    print("\n[TEST] get_dtcs_by_mask()")
    dtc = _make_dtc()

    # Pas de DTC actif : mask 0xFF retourne liste vide
    result = dtc.get_dtcs_by_mask(0xFF)
    check("0 DTC apres clear",              len(result) == 0)

    dtc.set_active("B2001")
    dtc.set_active("B2004")
    result = dtc.get_dtcs_by_mask(0xFF)
    check("2 DTCs actifs avec mask=0xFF",   len(result) == 2)

    # Masque bit 0 (Test Failed) --> STATUS_ACTIVE (0x2F) & 0x01 = 1
    result_mask = dtc.get_dtcs_by_mask(0x01)
    check("mask=0x01 trouve DTCs actifs",   len(result_mask) >= 2)

    # Masque 0x00 --> aucun match
    result_zero = dtc.get_dtcs_by_mask(0x00)
    check("mask=0x00 retourne vide",        len(result_zero) == 0)


# ─────────────────────────────────────────────────────────
# TEST 8 : build_response_02()
# ─────────────────────────────────────────────────────────
def test_build_response_02():
    print("\n[TEST] build_response_02() -- UDS 0x19/0x02")
    dtc = _make_dtc()
    dtc.set_active("B2001")
    dtc.set_active("B2002")

    resp = dtc.build_response_02(0xFF)
    check("resp[0]=0x59",                   resp[0] == 0x59)
    check("resp[1]=0x02",                   resp[1] == 0x02)
    check("resp[2]=STATUS_AVAIL_MASK",      resp[2] == STATUS_AVAILABILITY_MASK)
    # 2 DTCs x (3 bytes DTC + 1 byte status) = 8 bytes apres le header de 3
    check("longueur 3 + 2x4 = 11 bytes",    len(resp) == 3 + 2 * 4)

    # Aucun DTC actif
    dtc2    = _make_dtc()
    resp_empty = dtc2.build_response_02(0xFF)
    check("resp vide = 3 bytes header",     len(resp_empty) == 3)


# ─────────────────────────────────────────────────────────
# TEST 9 : build_response_04() -- snapshot
# ─────────────────────────────────────────────────────────
def test_build_response_04():
    print("\n[TEST] build_response_04() -- UDS 0x19/0x04")
    dtc  = _make_dtc()
    snap = {"ignition": 1, "wiper_mode": "SPEED2",
            "motor_curr": 400, "blade_pos": 1,
            "rain": 10, "vehicle_spd": 50}
    dtc.set_active("B2001", snapshot=snap)

    dtc_bytes = bytes(dtc.dtcs["B2001"]["bytes"])
    resp = dtc.build_response_04(dtc_bytes, 0xFF)
    check("resp[0]=0x59",    resp[0] == 0x59)
    check("resp[1]=0x04",    resp[1] == 0x04)
    check("DTC bytes inclus",resp[2:5] == dtc_bytes)

    # DTC inexistant → NRC 0x31
    resp_nrc = dtc.build_response_04(bytes([0x00, 0x00, 0x00]), 0xFF)
    check("DTC inconnu → NRC 0x31", resp_nrc == bytes([0x7F, 0x19, 0x31]))

    # Pas de snapshot → resp contient 0xFF sentinel
    dtc2 = _make_dtc()
    dtc2.set_active("B2002")   # sans snapshot
    dtc_b2 = bytes(dtc2.dtcs["B2002"]["bytes"])
    resp2 = dtc2.build_response_04(dtc_b2, 0xFF)
    check("sans snapshot: sentinel 0xFF", 0xFF in resp2)


# ─────────────────────────────────────────────────────────
# TEST 10 : build_response_06() -- extended data
# ─────────────────────────────────────────────────────────
def test_build_response_06():
    print("\n[TEST] build_response_06() -- UDS 0x19/0x06")
    dtc = _make_dtc()
    dtc.set_active("B2001")
    dtc.set_active("B2001")   # 2 occurrences

    dtc_bytes = bytes(dtc.dtcs["B2001"]["bytes"])
    resp = dtc.build_response_06(dtc_bytes)
    check("resp[0]=0x59",            resp[0] == 0x59)
    check("resp[1]=0x06",            resp[1] == 0x06)
    check("DTC bytes inclus",        resp[2:5] == dtc_bytes)
    # occurrence=2 encodes sur 2 bytes
    occ = (resp[-2] << 8) | resp[-1]
    check("occurrence_count=2",      occ == 2)

    # DTC inexistant
    resp_nrc = dtc.build_response_06(bytes([0x00, 0x00, 0x00]))
    check("DTC inconnu → NRC 0x31", resp_nrc == bytes([0x7F, 0x19, 0x31]))


# ─────────────────────────────────────────────────────────
# TEST 11 : handle_read_dtc() handler UDS 0x19
# ─────────────────────────────────────────────────────────
def test_handle_read_dtc():
    print("\n[TEST] handle_read_dtc() -- handler UDS 0x19 complet")
    dtc = _make_dtc()
    dtc.set_active("B2001")

    # Sous-fonction 0x02
    resp = handle_read_dtc(dtc, bytes([0x19, 0x02, 0xFF]))
    check("0x19/0x02 resp[0]=0x59",  resp[0] == 0x59)
    check("0x19/0x02 resp[1]=0x02",  resp[1] == 0x02)

    # Sous-fonction 0x04 avec DTC B2001
    dtc_b = bytes(dtc.dtcs["B2001"]["bytes"])
    resp4 = handle_read_dtc(dtc, bytes([0x19, 0x04]) + dtc_b + bytes([0xFF]))
    check("0x19/0x04 resp[0]=0x59",  resp4[0] == 0x59)
    check("0x19/0x04 resp[1]=0x04",  resp4[1] == 0x04)

    # Sous-fonction 0x06
    resp6 = handle_read_dtc(dtc, bytes([0x19, 0x06]) + dtc_b)
    check("0x19/0x06 resp[0]=0x59",  resp6[0] == 0x59)
    check("0x19/0x06 resp[1]=0x06",  resp6[1] == 0x06)

    # Sous-fonction non supportee → NRC 0x12
    resp_nrc = handle_read_dtc(dtc, bytes([0x19, 0x99]))
    check("sub non supporte → NRC 0x12", resp_nrc[2] == 0x12)

    # Payload trop court → NRC 0x13
    resp_short = handle_read_dtc(dtc, bytes([0x19]))
    check("payload court → NRC 0x13", resp_short[2] == 0x13)


# ─────────────────────────────────────────────────────────
# TEST 12 : handle_clear_dtc() handler UDS 0x14
# ─────────────────────────────────────────────────────────
def test_handle_clear_dtc():
    print("\n[TEST] handle_clear_dtc() -- handler UDS 0x14")
    dtc = _make_dtc()
    dtc.set_active("B2001")
    dtc.set_active("B2004")

    # Clear all DTCs (groupe 0xFFFFFF)
    resp = handle_clear_dtc(dtc, bytes([0x14, 0xFF, 0xFF, 0xFF]))
    check("clear all → resp=0x54",   resp == bytes([0x54]))
    check("B2001 CLEAN apres clear", dtc.dtcs["B2001"]["status"] == STATUS_CLEAN)
    check("B2004 CLEAN apres clear", dtc.dtcs["B2004"]["status"] == STATUS_CLEAN)

    # Clear DTC specifique B2002 : bytes [178,32,2] = [0xB2,0x20,0x02]
    dtc.set_active("B2002")
    resp2 = handle_clear_dtc(dtc, bytes([0x14, 0xB2, 0x20, 0x02]))
    check("clear B2002 → resp=0x54",     resp2 == bytes([0x54]))
    check("B2002 CLEAN apres clear",     dtc.dtcs["B2002"]["status"] == STATUS_CLEAN)

    # Payload trop court → NRC 0x13
    resp_short = handle_clear_dtc(dtc, bytes([0x14]))
    check("payload court → NRC 0x13",    resp_short[2] == 0x13)

    # DTC inconnu → NRC 0x31
    resp_nrc = handle_clear_dtc(dtc, bytes([0x14, 0x00, 0x00, 0x01]))
    check("DTC inconnu → NRC 0x31",      resp_nrc[2] == 0x31)


# ─────────────────────────────────────────────────────────
# TEST 13 : DTC inconnu ne crash pas
# ─────────────────────────────────────────────────────────
def test_unknown_dtc_no_crash():
    print("\n[TEST] DTC inconnu : pas de crash")
    dtc = _make_dtc()
    try:
        dtc.set_active("B9999")
        dtc.set_inactive("B9999")
        check("set_active DTC inconnu : pas de crash", True)
    except Exception as e:
        check("set_active DTC inconnu : pas de crash", False, str(e))


# ─────────────────────────────────────────────────────────
# TEST 14 : Tous les 9 DTCs peuvent etre actives
# ─────────────────────────────────────────────────────────
def test_all_dtcs_activable():
    print("\n[TEST] Tous les 9 DTCs activables")
    dtc   = _make_dtc()
    codes = list(dtc.dtcs.keys())
    snap  = {"ignition": 1, "wiper_mode": "SPEED1",
             "motor_curr": 100, "blade_pos": 0,
             "rain": 0, "vehicle_spd": 0}
    for code in codes:
        dtc.set_active(code, snapshot=snap)
    for code in codes:
        check(f"{code} ACTIVE", dtc.dtcs[code]["status"] == STATUS_ACTIVE)


# ─────────────────────────────────────────────────────────
# BILAN
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  TEST SUITE : dtc_manager.py (DTC Manager)")
    print("=" * 55)
    test_load()
    test_set_active_basic()
    test_set_active_with_snapshot()
    test_snapshot_rotation()
    test_set_inactive()
    test_clear_all()
    test_get_dtcs_by_mask()
    test_build_response_02()
    test_build_response_04()
    test_build_response_06()
    test_handle_read_dtc()
    test_handle_clear_dtc()
    test_unknown_dtc_no_crash()
    test_all_dtcs_activable()
    print()
    print("=" * 55)
    print(f"  RESULTAT : {PASS} PASS  |  {FAIL} FAIL")
    print("=" * 55)
    sys.exit(0 if FAIL == 0 else 1)
