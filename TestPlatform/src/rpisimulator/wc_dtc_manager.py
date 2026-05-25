#!/usr/bin/env python3
"""
wc_dtc_manager.py
=================
DTC Manager du WC ECU -- WipeWash System (Cas B)
Integre dans rpisimulator6 (RPi #2).

DTC WC :
  B2101 : WC Internal Failure
  B2102 : WC Motor Driver Fault
  B2103 : WC Position Sensor Fault
  B2104 : WC CAN NACK — 3 NACKs consécutifs (AckStatus=1, ErrorCode≠0)

Adresse diagnostique WC : 0x701
"""

import json
import os
import time as _time_mod
from datetime import datetime


def _now_ts() -> float:
    """Timestamp Unix courant (pour calcul durees ISO 14229-1)."""
    return _time_mod.time()

# =====================================================
# STATUS BYTE
# =====================================================
STATUS_CLEAN              = 0x00
STATUS_ACTIVE             = 0x2F
STATUS_INACTIVE           = 0x2E
STATUS_AVAILABILITY_MASK  = 0xFF

BIT_TEST_FAILED        = 0x01
BIT_FAILED_THIS_CYCLE  = 0x02
BIT_PENDING            = 0x04
BIT_CONFIRMED          = 0x08
BIT_NOT_COMPLETED      = 0x10
BIT_FAILED_SINCE_CLEAR = 0x20
BIT_WARNING_INDICATOR  = 0x40
BIT_NOT_SINCE_CLEAR    = 0x80

SNAP_DID_IGNITION    = 0xF190
SNAP_DID_WIPER_MODE  = 0xF191
SNAP_DID_MOTOR_CURR  = 0xF192
SNAP_DID_BLADE_POS   = 0xF193

MAX_SNAPSHOT_RECORDS = 5

# Extended Data Record numbers (ISO 14229-1 Section 7.3.4)
EXT_REC_OCCURRENCE_COUNT  = 0x01   # Nombre total d'occurrences (2B)
EXT_REC_FAILED_CYCLES     = 0x03   # Cycles o� le DTC �tait actif (1B)
EXT_REC_TIME_FIRST_OCC    = 0x04   # Secondes depuis premi�re occurrence (4B)
EXT_REC_TIME_LAST_OCC     = 0x05   # Secondes depuis derni�re occurrence (4B)

DTC_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wc_dtc_database.json")


# =====================================================
# DTC MANAGER WC
# =====================================================
class DTCManager_WC:
    def __init__(self, filepath=DTC_FILE):
        self.filepath = filepath
        self._load()
        print(f"[WC-DTC] Manager loaded - {len(self.dtcs)} DTCs in database")

    def _load(self):
        with open(self.filepath, "r") as f:
            self.db = json.load(f)
        self.dtcs = self.db["dtcs"]

    def _save(self):
        with open(self.filepath, "w") as f:
            json.dump(self.db, f, indent=4)

    def _now(self) -> str:
        return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    def set_active(self, code: str, snapshot: dict = None):
        if code not in self.dtcs:
            print(f"[WC-DTC] Unknown DTC: {code}")
            return
        dtc = self.dtcs[code]
        now = self._now()
        is_new = dtc["first_occurrence"] is None

        dtc["status"]           = STATUS_ACTIVE
        dtc["occurrence_count"] += 1
        dtc["last_occurrence"]  = now
        dtc["last_occurrence_ts"] = _now_ts()
        if is_new:
            dtc["first_occurrence"]    = now
            dtc["first_occurrence_ts"] = _now_ts()
        # failed_cycles : incr�ment� UNE SEULE FOIS par cycle d'allumage
        if not dtc.get("_seen_this_cycle", False):
            dtc["failed_cycles"]    = dtc.get("failed_cycles", 0) + 1
            dtc["_seen_this_cycle"] = True

        if snapshot:
            dtc["snapshot"] = snapshot
            existing = dtc.get("snapshot_records", [])
            last_num = existing[-1]["record_number"] if existing else 0
            next_num = (last_num % 255) + 1

            record = {
                "record_number": next_num,
                "timestamp": now,
                "data": {
                    "F190_ignition":    snapshot.get("ignition", 1),
                    "F191_wiper_mode":  snapshot.get("wiper_mode", "UNKNOWN"),
                    "F192_motor_curr":  snapshot.get("motor_curr", 0),
                    "F193_blade_pos":   snapshot.get("blade_pos", 0),
                }
            }
            existing.append(record)
            if len(existing) > MAX_SNAPSHOT_RECORDS:
                existing = existing[-MAX_SNAPSHOT_RECORDS:]
            dtc["snapshot_records"] = existing

        self._save()

        b = dtc["bytes"]
        hex_code = f"{b[0]:02X}{b[1]:02X}{b[2]:02X}"
        print(f"")
        print(f"{'!'*54}")
        print(f"! [WC] DTC ACTIVE  {code} [{hex_code}]")
        print(f"! {dtc['description']}")
        print(f"! Status: 0x{STATUS_ACTIVE:02X}  Occurrence: #{dtc['occurrence_count']}")
        if snapshot:
            print(f"! Snapshot: WiperMode={snapshot.get('wiper_mode','?')}  "
                  f"MotorCurr={snapshot.get('motor_curr',0)}mA")
        print(f"{'!'*54}")
        print(f"")

    def set_inactive(self, code: str):
        if code not in self.dtcs:
            return
        dtc = self.dtcs[code]
        if dtc["status"] == STATUS_ACTIVE:
            dtc["status"] = STATUS_INACTIVE
            self._save()
            b = dtc["bytes"]
            hex_code = f"{b[0]:02X}{b[1]:02X}{b[2]:02X}"
            print(f"  [WC-DTC INACTIVE] {code} [{hex_code}] - {dtc['description']}")

    def clear_all(self):
        for code in self.dtcs:
            self.dtcs[code]["status"]              = STATUS_CLEAN
            self.dtcs[code]["occurrence_count"]    = 0
            self.dtcs[code]["first_occurrence"]    = None
            self.dtcs[code]["first_occurrence_ts"] = None
            self.dtcs[code]["last_occurrence"]     = None
            self.dtcs[code]["last_occurrence_ts"]  = None
            self.dtcs[code]["failed_cycles"]       = 0
            self.dtcs[code]["_seen_this_cycle"]    = False
            self.dtcs[code]["snapshot"]            = {}
            self.dtcs[code]["snapshot_records"]    = []
        self._save()
        now = self._now()
        print(f"")
        print(f"  {'='*52}")
        print(f"  = [WC] DTC CLEARED - {len(self.dtcs)} DTCs reset to CLEAN (0x00)")
        print(f"  = Cleared at: {now}")
        print(f"  {'='*52}")
        print(f"")

    def get_dtcs_by_mask(self, mask: int) -> list:
        result = []
        for code, dtc in self.dtcs.items():
            status = dtc["status"]
            if mask == 0xFF:
                # 0xFF = tous les DTC non CLEAN
                if status != STATUS_CLEAN:
                    result.append((bytes(dtc["bytes"]), status))
            else:
                # Comparaison exacte : mask=0x2F retourne uniquement les ACTIVE
                #                      mask=0x2E retourne uniquement les INACTIVE
                if status == mask:
                    result.append((bytes(dtc["bytes"]), status))
        return result

    def get_all_supported(self) -> list:
        return [(bytes(dtc["bytes"]), dtc["status"]) for dtc in self.dtcs.values()]

    def build_response_02(self, mask: int) -> bytes:
        dtc_list = self.get_dtcs_by_mask(mask)
        resp = bytes([0x59, 0x02, STATUS_AVAILABILITY_MASK])
        for dtc_bytes, status in dtc_list:
            resp += dtc_bytes + bytes([status])
        return resp

    def build_response_04(self, dtc_bytes_target: bytes, record_number: int) -> bytes:
        target = None
        target_code = None

        # Recherche exacte d'abord
        for code, dtc in self.dtcs.items():
            if bytes(dtc["bytes"]) == dtc_bytes_target:
                target = dtc
                target_code = code
                break

        # Si pas trouve : le GUI peut encoder le DTC avec un premier octet
        # tronque (ex: B2101 -> 0x0B2101 au lieu de 0xB22101).
        # On cherche alors par correspondance sur les 2 derniers octets.
        if target is None:
            for code, dtc in self.dtcs.items():
                db = dtc["bytes"]
                if (db[1] == dtc_bytes_target[1] and
                        db[2] == dtc_bytes_target[2]):
                    target = dtc
                    target_code = code
                    # Utiliser les vrais bytes du DTC pour la reponse
                    dtc_bytes_target = bytes(db)
                    print(f"  [WC-DTC] Snapshot: DTC reecrit {dtc_bytes_target.hex().upper()}"
                          f" -> {code}")
                    break

        if target is None:
            return bytes([0x7F, 0x19, 0x31])

        all_records = target.get("snapshot_records", [])

        if record_number == 0xFF:
            # 0xFF = tous les records
            records = all_records
        else:
            # Chercher le record demande
            records = [r for r in all_records if r["record_number"] == record_number]
            # Si pas trouve : retourner le dernier record disponible
            if not records and all_records:
                records = [all_records[-1]]
                print(f"  [WC-DTC] Record {record_number} introuvable"
                      f" -> retour dernier record ({all_records[-1]['record_number']})")

        resp = bytes([0x59, 0x04]) + dtc_bytes_target + bytes([target["status"]])

        if not records:
            resp += bytes([0xFF])
            return resp

        for rec in records:
            resp += bytes([rec["record_number"] & 0xFF])
            d = rec["data"]
            resp += bytes([0xF1, 0x90, 0x01, d["F190_ignition"] & 0xFF])
            mode_bytes = d["F191_wiper_mode"].encode("ascii")[:10].ljust(10, b"\x00")
            resp += bytes([0xF1, 0x91, 0x0A]) + mode_bytes
            curr = d["F192_motor_curr"]
            resp += bytes([0xF1, 0x92, 0x02, (curr >> 8) & 0xFF, curr & 0xFF])
            resp += bytes([0xF1, 0x93, 0x01, d["F193_blade_pos"] & 0xFF])

        return resp

    def build_response_06(self, dtc_bytes_target: bytes) -> bytes:
        """ISO 14229-1 : 5 Extended Data Records."""
        import struct as _struct
        target = None

        # Recherche exacte d'abord
        for code, dtc in self.dtcs.items():
            if bytes(dtc["bytes"]) == dtc_bytes_target:
                target = dtc
                break

        # Fallback : correspondance sur les 2 derniers octets
        if target is None:
            for code, dtc in self.dtcs.items():
                db = dtc["bytes"]
                if (db[1] == dtc_bytes_target[1] and
                        db[2] == dtc_bytes_target[2]):
                    target = dtc
                    dtc_bytes_target = bytes(db)
                    break

        if target is None:
            return bytes([0x7F, 0x19, 0x31])

        resp = bytes([0x59, 0x06]) + dtc_bytes_target + bytes([target["status"]])
        now  = _now_ts()

        # Record 0x01 : occurrence counter (2 octets)
        occ = target.get("occurrence_count", 0)
        resp += bytes([EXT_REC_OCCURRENCE_COUNT, 0x02,
                       (occ >> 8) & 0xFF, occ & 0xFF])

        # Record 0x03 : failed cycles counter (1 octet)
        fc = min(target.get("failed_cycles", 0), 0xFF)
        resp += bytes([EXT_REC_FAILED_CYCLES, 0x01, fc])

        # Record 0x04 : time since first occurrence (4 octets, secondes)
        ts_first = target.get("first_occurrence_ts")
        dt_first = int(now - ts_first) if ts_first else 0xFFFFFFFF
        dt_first = min(dt_first, 0xFFFFFFFF)
        resp += bytes([EXT_REC_TIME_FIRST_OCC, 0x04]) + _struct.pack(">I", dt_first)

        # Record 0x05 : time since last occurrence (4 octets, secondes)
        ts_last = target.get("last_occurrence_ts")
        dt_last = int(now - ts_last) if ts_last else 0xFFFFFFFF
        dt_last = min(dt_last, 0xFFFFFFFF)
        resp += bytes([EXT_REC_TIME_LAST_OCC, 0x04]) + _struct.pack(">I", dt_last)

        return resp

    # -------------------------------------------------
    # Gestion cycle d'allumage (ISO 14229-1 failed_cycles)
    # -------------------------------------------------
    def notify_ignition_on(self):
        """
        Appeler lors de la transition ignition OFF->ON.
        - Si un DTC est encore ACTIVE au debut du nouveau cycle,
          incrementer failed_cycles maintenant.
        - Sinon remettre _seen_this_cycle=False.
        """
        for dtc in self.dtcs.values():
            if dtc.get("status") == STATUS_ACTIVE:
                dtc["failed_cycles"]    = dtc.get("failed_cycles", 0) + 1
                dtc["_seen_this_cycle"] = True
            else:
                dtc["_seen_this_cycle"] = False
        print("[WC-DTC] Nouveau cycle allumage - failed_cycles mis a jour")

    def notify_ignition_off(self):
        """
        Appeler lors de la transition ignition ON->OFF.
        Sauvegarde l'etat final du cycle.
        """
        self._save()
        print("[WC-DTC] Fin cycle allumage - base DTC sauvegardee")

    def print_all(self):
        print(f"\n{'='*56}")
        print(f"  [WC] DTC DATABASE ({len(self.dtcs)} entries)")
        print(f"{'='*56}")
        for code, dtc in self.dtcs.items():
            s = dtc["status"]
            if s == STATUS_ACTIVE:
                label = "ACTIVE   (0x2F)"
            elif s == STATUS_INACTIVE:
                label = "INACTIVE (0x2E)"
            else:
                label = "CLEAN    (0x00)"
            print(f"  {code} | {label} | #{dtc['occurrence_count']}")
            print(f"       {dtc['description']}")
        print(f"{'='*56}\n")


# =====================================================
# UDS 0x19 HANDLER WC
# =====================================================
def handle_read_dtc(dtc_mgr: DTCManager_WC, uds: bytes) -> bytes:
    if len(uds) < 2:
        return bytes([0x7F, 0x19, 0x13])
    subfunc = uds[1]

    if subfunc == 0x02:
        mask = uds[2] if len(uds) >= 3 else 0xFF
        dtc_list = dtc_mgr.get_dtcs_by_mask(mask)
        print(f"  [WC UDS 0x19 0x02] mask=0x{mask:02X} - {len(dtc_list)} DTC(s)")
        return dtc_mgr.build_response_02(mask)

    elif subfunc == 0x04:
        if len(uds) < 5:
            return bytes([0x7F, 0x19, 0x13])
        dtc_b   = bytes(uds[2:5])
        rec_num = uds[5] if len(uds) >= 6 else 0xFF
        return dtc_mgr.build_response_04(dtc_b, rec_num)

    elif subfunc == 0x06:
        if len(uds) < 5:
            return bytes([0x7F, 0x19, 0x13])
        dtc_b = bytes(uds[2:5])
        return dtc_mgr.build_response_06(dtc_b)

    else:
        return bytes([0x7F, 0x19, 0x12])


# =====================================================
# UDS 0x14 HANDLER WC
# =====================================================
def handle_clear_dtc(dtc_mgr: DTCManager_WC, uds: bytes) -> bytes:
    if len(uds) < 4:
        return bytes([0x7F, 0x14, 0x13])
    group = (uds[1] << 16) | (uds[2] << 8) | uds[3]
    print(f"  [WC UDS 0x14] Clear DTC group=0x{group:06X}")
    # 0xFFFFFF = clear all (ISO 14229 standard)
    # 0x000000 = clear all envoye par l'interface GUI
    if group in (0xFFFFFF, 0x000000):
        dtc_mgr.clear_all()
        return bytes([0x54])
    for code, dtc in dtc_mgr.dtcs.items():
        b = dtc["bytes"]
        if (b[0] << 16 | b[1] << 8 | b[2]) == group:
            dtc["status"] = 0x00
            dtc["occurrence_count"] = 0
            dtc["snapshot_records"] = []
            dtc_mgr._save()
            print(f"  [WC-DTC] Cleared {code}")
            return bytes([0x54])
    return bytes([0x7F, 0x14, 0x31])