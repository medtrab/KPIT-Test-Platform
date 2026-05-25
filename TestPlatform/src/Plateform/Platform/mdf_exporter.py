"""
mdf_exporter.py  —  Export MDF4 (ASAM MDF 4.10) pour WipeWash HIL Platform
============================================================================
Produit un fichier .mf4 standard lisible par MATLAB, CANalyzer, INCA,
dSPACE ControlDesk, Vector CANdb++, PEAK PCAN-Explorer, etc.

Architecture MDF4 produite
--------------------------
  ┌─ Groupe "MOTOR"         source_type=ECU  bus_type=NONE
  │   ├── motor_state_int   int32   [–]    0=OFF…7=REAR_WIPE   conversion ValueToText
  │   ├── motor_state_txt   string  [–]    label textuel brut
  │   ├── front_motor_on    uint8   [bool]
  │   ├── rear_motor_on     uint8   [bool]
  │   ├── motor_current     float32 [A]
  │   ├── rest_contact      uint8   [bool] 1=PARKED
  │   ├── fault             uint8   [bool]
  │   ├── crs_wiper_op      int32   [–]    même enum que motor_state_int
  │   ├── ignition          int32   [–]    0=OFF 1=ACC 2=ON
  │   ├── vehicle_speed     float32 [km/h]
  │   ├── rain_intensity    float32 [%]
  │   └── front_blade_cycles int32  [count]
  ├─ Groupe "LIN"           source_type=BUS  bus_type=LIN
  │   ├── lin_pid           uint8   [–]    ID trame LIN (7 bits)
  │   ├── lin_type          string  [–]    "TX"/"RX"
  │   ├── lin_wiper_op      int32   [–]    enum wiper op
  │   ├── lin_front_on      uint8   [bool]
  │   └── lin_rest_raw      uint8   [bool]
  ├─ Groupe "CAN"           source_type=BUS  bus_type=CAN
  │   ├── can_id            uint32  [–]    ID décimal (ex. 512 = 0x200)
  │   ├── can_id_hex        string  [–]    "0x200" etc.
  │   ├── can_direction     uint8   [–]    0=RX 1=TX
  │   ├── can_dlc           uint8   [–]    Data Length Code
  │   ├── can_payload_b0…b7 uint8×8 [–]    octets du payload
  │   ├── can_wiper_cmd     int32   [–]    champ décodé 0x200
  │   ├── can_wiper_status  int32   [–]    champ décodé 0x201
  │   └── can_wiper_ack     int32   [–]    champ décodé 0x202
  └─ Groupe "PUMP"          source_type=IO   bus_type=NONE
      ├── pump_flow         float32 [L/min]
      ├── pump_pressure     float32 [bar]
      ├── pump_current      float32 [A]
      ├── pump_active       uint8   [bool]
      ├── pump_state        string  [–]    "FORWARD"/"OFF" etc.
      ├── pump_direction    string  [–]    "FORWARD"/"REVERSE"
      └── pump_timeout_elapsed float32 [s]

Métadonnées ASAM MDF4 complètes
---------------------------------
  header.author      = bench_id
  header.project     = project
  header.subject     = session_id  (UUID auto-généré)
  header.description = "WipeWash HIL — Sources: MOTOR LIN CAN PUMP"
  header.start_time  = datetime enregistrement

Conversions ValueToText (enum lisibles dans CANalyzer / INCA)
--------------------------------------------------------------
  motor_state_int, crs_wiper_op  →  OFF / TOUCH / SPEED1 / SPEED2 /
                                     AUTO / FRONT_WASH / REAR_WASH / REAR_WIPE
  ignition               →  OFF / ACC / ON
  can_direction          →  RX / TX

Usage
-----
    from mdf_exporter import MDFExporter
    exp = MDFExporter(bench_id="Banc-A", project="WW_2026")
    path = exp.export(recorder, output_dir="/tmp")

    # Ou depuis un CSV exporté par DataRecorder :
    path = exp.export_from_csv("/tmp/wipewash_20260416.csv", output_dir="/tmp")
"""

from __future__ import annotations

import csv
import datetime
import os
import uuid
from typing import Optional, List, Dict, Any

import numpy as np

# ── Constantes : encodages énumérés ───────────────────────────────────────

_WIPER_OP_MAP: dict[str, int] = {
    "OFF": 0, "TOUCH": 1, "SPEED1": 2, "SPEED2": 3,
    "AUTO": 4, "FRONT_WASH": 5, "REAR_WASH": 6, "REAR_WIPE": 7,
}
_WIPER_OP_CONV: dict[int, str] = {v: k for k, v in _WIPER_OP_MAP.items()}

_IGNITION_MAP: dict[str, int] = {"OFF": 0, "ACC": 1, "ON": 2}
_IGNITION_CONV: dict[int, str] = {v: k for k, v in _IGNITION_MAP.items()}

# Conversion asammdf : format {val_N: int, label_N: str, ...}
def _make_vtxt(d: dict[int, str]) -> dict:
    """Crée une conversion ValueToText pour asammdf."""
    conv: dict[str, Any] = {"conversion_type": "ValueToTextConversion"}
    for i, (val, label) in enumerate(sorted(d.items())):
        conv[f"val_{i}"]   = val
        conv[f"label_{i}"] = label
    return conv


_CONV_WIPER  = _make_vtxt(_WIPER_OP_CONV)
_CONV_IGN    = _make_vtxt(_IGNITION_CONV)
_CONV_DIRCAN = _make_vtxt({0: "RX", 1: "TX"})

# ── Helpers de parsing ────────────────────────────────────────────────────

def _parse_ts(ts_str: str) -> float:
    """Convertit un timestamp string en float epoch (secondes)."""
    if not ts_str:
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(ts_str, fmt).timestamp()
        except ValueError:
            pass
    return 0.0


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v) if v not in (None, "", "nan") else default
    except (TypeError, ValueError):
        return default


def _i(v, default: int = 0) -> int:
    try:
        return int(float(v)) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _b(v) -> int:
    """Bool-like → 0/1."""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return 1 if v else 0
    s = str(v).strip().upper()
    return 1 if s in ("TRUE", "1", "ON", "YES", "PARKED") else 0


def _wiper_int(s: str) -> int:
    return _WIPER_OP_MAP.get(str(s).upper().strip(), 0)


def _ign_int(s: str) -> int:
    return _IGNITION_MAP.get(str(s).upper().strip(), 0)


def _parse_can_id(v) -> int:
    """Parse un CAN ID qui peut être hex string ou int."""
    if v in (None, ""):
        return 0
    s = str(v).strip()
    try:
        return int(s, 16) if s.startswith("0x") or s.startswith("0X") else int(s)
    except (ValueError, TypeError):
        return 0


def _parse_payload_bytes(p, dlc: int = 8) -> np.ndarray:
    """
    Convertit un payload hex string (ex. "01 02 AB CD" ou "0x010xAB")
    en tableau uint8 de longueur dlc (0-padded).
    """
    result = np.zeros(8, dtype=np.uint8)
    if not p:
        return result
    s = str(p).strip().replace("0x", "").replace("0X", "").replace(" ", "").replace(":", "")
    try:
        raw = bytes.fromhex(s)
        n = min(len(raw), 8)
        result[:n] = np.frombuffer(raw[:n], dtype=np.uint8)
    except (ValueError, TypeError):
        pass
    return result


def _to_bytes_array(strings: list) -> np.ndarray:
    """Convertit une liste de str en tableau numpy bytes (requis pour Signal string MDF4)."""
    return np.array([s.encode("utf-8") for s in strings])


def _decode_can_field(v) -> int:
    """Décode un champ CAN décodé (peut être dict JSON-like, int ou str)."""
    if v in (None, ""):
        return 0
    if isinstance(v, dict):
        # Prend la première valeur numérique du dict
        for val in v.values():
            try:
                return int(float(val))
            except (TypeError, ValueError):
                pass
        return 0
    try:
        import json
        obj = json.loads(str(v))
        if isinstance(obj, dict):
            for val in obj.values():
                try:
                    return int(float(val))
                except Exception:
                    pass
        return int(float(obj))
    except Exception:
        pass
    try:
        return int(float(str(v)))
    except (TypeError, ValueError):
        return 0


# ── Classe principale ──────────────────────────────────────────────────────

class MDFExporter:
    """
    Exporte les données du DataRecorder en fichier MDF4 (.mf4)
    avec métadonnées complètes et conversions enum lisibles dans
    MATLAB / CANalyzer / INCA / dSPACE ControlDesk.

    Args:
        bench_id  : identifiant du banc HIL (ex. "Banc-A")
        project   : nom du projet (ex. "WipeWash_2026")
        engineer  : nom de l'ingénieur responsable
    """

    VERSION = "4.10"   # ASAM MDF version

    def __init__(self,
                 bench_id:  str = "WipeWash-Bench",
                 project:   str = "WipeWash Automotive HIL",
                 engineer:  str = "WipeWash Platform"):
        self._bench_id = bench_id
        self._project  = project
        self._engineer = engineer

    # ─────────────────────────────────────────────────────────────────────
    #  API publique
    # ─────────────────────────────────────────────────────────────────────

    def export(self,
               recorder,
               output_dir: str = ".",
               base_name:  str = "",
               session_id: str = "") -> Optional[str]:
        """
        Exporte toutes les données du DataRecorder en MDF4.

        Args:
            recorder   : instance DataRecorder (datasave_panel.py)
            output_dir : dossier de destination
            base_name  : préfixe du nom de fichier (auto si vide)
            session_id : identifiant de session (UUID auto si vide)

        Returns:
            Chemin absolu du .mf4 créé, ou None en cas d'erreur.
        """
        rows = recorder.get_rows() if hasattr(recorder, "get_rows") else []
        if not rows:
            print("[MDFExporter] Aucune donnée à exporter.")
            return None
        return self._build_and_save(rows, output_dir, base_name, session_id,
                                    t_start=getattr(recorder, "_t0", None))

    def export_from_csv(self,
                        csv_path:   str,
                        output_dir: str = ".",
                        base_name:  str = "",
                        session_id: str = "") -> Optional[str]:
        """
        Exporte depuis un CSV produit par DataRecorder.export_csv().
        Utile pour convertir des enregistrements existants.

        Args:
            csv_path   : chemin vers le fichier CSV source
            output_dir : dossier de destination
            base_name  : préfixe (auto si vide)
            session_id : identifiant de session (UUID auto si vide)

        Returns:
            Chemin absolu du .mf4 créé, ou None en cas d'erreur.
        """
        rows = self._read_csv(csv_path)
        if not rows:
            print(f"[MDFExporter] CSV vide ou illisible : {csv_path}")
            return None
        if not base_name:
            base_name = os.path.splitext(os.path.basename(csv_path))[0]
        return self._build_and_save(rows, output_dir, base_name, session_id)

    # ─────────────────────────────────────────────────────────────────────
    #  Lecture CSV
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _read_csv(path: str) -> List[Dict]:
        rows: List[Dict] = []
        encodings = ("utf-8-sig", "utf-8", "latin-1")
        for enc in encodings:
            try:
                with open(path, newline="", encoding=enc) as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)
                break
            except (UnicodeDecodeError, FileNotFoundError):
                continue
        return rows

    # ─────────────────────────────────────────────────────────────────────
    #  Cœur : construction du fichier MDF4
    # ─────────────────────────────────────────────────────────────────────

    def _build_and_save(self,
                        rows:       List[Dict],
                        output_dir: str,
                        base_name:  str,
                        session_id: str,
                        t_start=None) -> Optional[str]:
        try:
            from asammdf import MDF, Signal
            from asammdf.blocks.source_utils import Source
        except ImportError:
            print("[MDFExporter] asammdf non installé — pip install asammdf")
            return None

        os.makedirs(output_dir, exist_ok=True)

        if not base_name:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            base_name = f"wipewash_{ts}"
        if not session_id:
            session_id = str(uuid.uuid4())

        # ── Métadonnées header ────────────────────────────────────────
        mdf = MDF(version=self.VERSION)
        now = datetime.datetime.now()
        mdf.header.start_time = now
        mdf.header.author     = self._engineer
        mdf.header.project    = self._project
        mdf.header.subject    = f"{self._bench_id} — session {session_id[:8]}"
        mdf.header.description = (
            f"WipeWash HIL Bench  |  Banc: {self._bench_id}  |"
            f"  Session: {session_id}  |  Date: {now:%Y-%m-%d %H:%M:%S}  |"
            f"  Sources: MOTOR LIN CAN PUMP  |"
            f"  Tool: WipeWash Platform / MDFExporter v2 / asammdf"
        )

        # ── Dispatcher par source ─────────────────────────────────────
        src_rows: dict[str, list] = {}
        for r in rows:
            src = str(r.get("source", "motor")).lower().strip()
            src_rows.setdefault(src, []).append(r)

        total_samples = 0

        _SRC_SOURCE = {
            "motor": lambda S: S(name="BCM_ECU", path="RTE/BCM",
                                 comment="Body Control Module — RTE variables",
                                 source_type=S.SOURCE_ECU,
                                 bus_type=S.BUS_TYPE_NONE),
            "lin":   lambda S: S(name="LIN1", path="LIN1/BCM",
                                 comment="LIN bus 1 — CRS/BCM",
                                 source_type=S.SOURCE_BUS,
                                 bus_type=S.BUS_TYPE_LIN),
            "can":   lambda S: S(name="CAN1", path="CAN1/BCM",
                                 comment="CAN bus 1 — BCM/WC/GW",
                                 source_type=S.SOURCE_BUS,
                                 bus_type=S.BUS_TYPE_CAN),
            "pump":  lambda S: S(name="PUMP_IO", path="GPIO/RPi",
                                 comment="Pump I/O — RPi Simulator GPIO",
                                 source_type=S.SOURCE_IO,
                                 bus_type=S.BUS_TYPE_NONE),
        }

        _BUILDERS = {
            "motor": self._build_motor,
            "lin":   self._build_lin,
            "can":   self._build_can,
            "pump":  self._build_pump,
        }

        for src, builder in _BUILDERS.items():
            data = src_rows.get(src, [])
            if not data:
                continue
            acq_source = _SRC_SOURCE[src](Source)
            signals = builder(data, Signal, acq_source)
            if signals:
                mdf.append(signals, acq_name=src.upper(),
                            acq_source=acq_source)
                total_samples += len(data)

        # ── Sauvegarde ────────────────────────────────────────────────
        path = os.path.join(output_dir, base_name + ".mf4")
        mdf.save(path, overwrite=True, compression=2)   # compression Deflate
        size_kb = os.path.getsize(path) / 1024
        print(f"[MDFExporter] ✓  {path}  "
              f"({total_samples} échantillons, {size_kb:.1f} KB, "
              f"session {session_id[:8]})")
        return path

    # ─────────────────────────────────────────────────────────────────────
    #  Groupe MOTOR
    # ─────────────────────────────────────────────────────────────────────

    def _build_motor(self, rows: list, Signal, source) -> list:
        """
        Canaux moteur : état wiper, courant, vitesse véhicule, pluie,
        ignition, rest contact, fault, cycles lame.
        """
        if not rows:
            return []

        t0 = _parse_ts(rows[0].get("timestamp", "")) or 0.0

        ts           : list[float] = []
        state_i      : list[int]   = []
        state_txt    : list[str]   = []
        front        : list[int]   = []
        rear         : list[int]   = []
        current      : list[float] = []
        rest         : list[int]   = []
        fault        : list[int]   = []
        crs_op       : list[int]   = []
        ignition     : list[int]   = []
        vspeed       : list[float] = []
        rain         : list[float] = []
        blade_cycles : list[int]   = []

        for r in rows:
            t = _parse_ts(r.get("timestamp", ""))
            ts.append(t - t0)

            st = str(r.get("state", "OFF")).upper().strip()
            state_i.append(_wiper_int(st))
            state_txt.append(st if st else "OFF")

            front.append(_b(r.get("front", 0)))
            rear.append(_b(r.get("rear",  0)))
            current.append(_f(r.get("current", 0)))
            rest.append(1 if str(r.get("rest_contact", "")).upper() == "PARKED" else 0)
            fault.append(_b(r.get("fault", 0)))
            # crs_wiper_op : live dict ne contient pas toujours cette clé → fallback sur "state"
            crs_raw = str(r.get("crs_wiper_op", "") or r.get("state", "OFF")).strip()
            crs_op.append(_wiper_int(crs_raw if crs_raw else "OFF"))
            # ignition : live dict contient "ON"/"OFF"/"ACC" (string), pas un int
            ign_raw = str(r.get("ignition", "OFF") or "OFF").strip().upper()
            ignition.append(_ign_int(ign_raw))
            vspeed.append(_f(r.get("vehicle_speed", 0)))
            rain.append(_f(r.get("rain_intensity", 0)))
            blade_cycles.append(_i(r.get("front_blade_cycles", 0)))

        ta = np.array(ts, dtype=np.float64)
        kw = {"source": source}

        return [
            Signal(samples=np.array(state_i, dtype=np.int32), timestamps=ta,
                   name="motor_state_int", unit="",
                   comment="Wiper state index (0=OFF … 7=REAR_WIPE)",
                   conversion=_CONV_WIPER, **kw),

            Signal(samples=_to_bytes_array(state_txt), timestamps=ta,
                   name="motor_state_txt", unit="",
                   comment="Wiper state string label",
                   encoding="utf-8", **kw),

            Signal(samples=np.array(front, dtype=np.uint8), timestamps=ta,
                   name="front_motor_on", unit="bool",
                   comment="Front wiper motor active", **kw),

            Signal(samples=np.array(rear, dtype=np.uint8), timestamps=ta,
                   name="rear_motor_on", unit="bool",
                   comment="Rear wiper motor active", **kw),

            Signal(samples=np.array(current, dtype=np.float32), timestamps=ta,
                   name="motor_current", unit="A",
                   comment="Total motor current (front + rear)", **kw),

            Signal(samples=np.array(rest, dtype=np.uint8), timestamps=ta,
                   name="rest_contact", unit="bool",
                   comment="1=PARKED (rest contact active) 0=MOVING", **kw),

            Signal(samples=np.array(fault, dtype=np.uint8), timestamps=ta,
                   name="motor_fault", unit="bool",
                   comment="Motor fault flag", **kw),

            Signal(samples=np.array(crs_op, dtype=np.int32), timestamps=ta,
                   name="crs_wiper_op", unit="",
                   comment="CRS wiper operation request",
                   conversion=_CONV_WIPER, **kw),

            Signal(samples=np.array(ignition, dtype=np.int32), timestamps=ta,
                   name="ignition", unit="",
                   comment="Ignition status",
                   conversion=_CONV_IGN, **kw),

            Signal(samples=np.array(vspeed, dtype=np.float32), timestamps=ta,
                   name="vehicle_speed", unit="km/h",
                   comment="Vehicle speed (from GW 0x300)", **kw),

            Signal(samples=np.array(rain, dtype=np.float32), timestamps=ta,
                   name="rain_intensity", unit="%",
                   comment="Rain sensor intensity (from GW 0x301)", **kw),

            Signal(samples=np.array(blade_cycles, dtype=np.int32), timestamps=ta,
                   name="front_blade_cycles", unit="count",
                   comment="Front blade wipe cycle counter", **kw),
        ]

    # ─────────────────────────────────────────────────────────────────────
    #  Groupe LIN
    # ─────────────────────────────────────────────────────────────────────

    def _build_lin(self, rows: list, Signal, source) -> list:
        """
        Canaux LIN : PID, type trame, wiper_op, front_motor_on, rest_contact_raw,
        alive counter, checksum (cs_int), bcm_state et raw frame hex.
        """
        if not rows:
            return []

        t0 = _parse_ts(rows[0].get("timestamp", "")) or 0.0

        ts        : list[float] = []
        pid       : list[int]   = []
        lin_type  : list[str]   = []
        wiper_op  : list[int]   = []
        front_on  : list[int]   = []
        rest_raw  : list[int]   = []
        alive     : list[int]   = []
        cs_int    : list[int]   = []
        bcm_state : list[str]   = []
        raw_hex   : list[str]   = []

        for r in rows:
            t = _parse_ts(r.get("timestamp", ""))
            ts.append(t - t0)

            pid_s = r.get("pid", "0x00") or "0x00"
            try:
                pid.append(int(pid_s, 16) if str(pid_s).startswith("0x") else int(pid_s))
            except (ValueError, TypeError):
                pid.append(0)

            lin_type.append(str(r.get("lin_type", "")).strip() or "?")
            # live dict LIN utilise "op" (int enum), "wiper_op" souvent vide
            op_raw = r.get("op", "") or r.get("wiper_op", "") or "OFF"
            # "op" peut être un int directement (enum wiper_op)
            try:
                op_int = int(op_raw)
                wiper_op.append(op_int)
            except (ValueError, TypeError):
                wiper_op.append(_wiper_int(str(op_raw)))
            front_on.append(_b(r.get("front_motor_on", 0)))
            rest_raw.append(_b(r.get("rest_contact_raw", 0)))

            # Alive counter (uint8 0-15 tournant)
            try:
                alive.append(int(r.get("alive", 0) or 0) & 0xFF)
            except (ValueError, TypeError):
                alive.append(0)

            # Checksum / cs_int
            try:
                cs_int.append(int(r.get("cs_int", 0) or 0) & 0xFF)
            except (ValueError, TypeError):
                cs_int.append(0)

            bcm_state.append(str(r.get("bcm_state", "OFF") or "OFF").strip())
            raw_hex.append(str(r.get("raw", "") or "").strip())

        ta = np.array(ts, dtype=np.float64)
        kw = {"source": source}

        return [
            Signal(samples=np.array(pid, dtype=np.uint8), timestamps=ta,
                   name="lin_pid", unit="",
                   comment="LIN protected ID (7-bit frame identifier)", **kw),

            Signal(samples=_to_bytes_array(lin_type), timestamps=ta,
                   name="lin_type", unit="",
                   comment="Frame type: TX=BCM→CRS / RX=CRS→BCM",
                   encoding="utf-8", **kw),

            Signal(samples=np.array(wiper_op, dtype=np.int32), timestamps=ta,
                   name="lin_wiper_op", unit="",
                   comment="Wiper operation from LIN LeftStickWiperRequester",
                   conversion=_CONV_WIPER, **kw),

            Signal(samples=np.array(front_on, dtype=np.uint8), timestamps=ta,
                   name="lin_front_motor_on", unit="bool",
                   comment="Front motor on flag from LIN CRS_Status", **kw),

            Signal(samples=np.array(rest_raw, dtype=np.uint8), timestamps=ta,
                   name="lin_rest_contact_raw", unit="bool",
                   comment="Rest contact raw bit from LIN CRS_Status", **kw),

            Signal(samples=np.array(alive, dtype=np.uint8), timestamps=ta,
                   name="lin_alive", unit="",
                   comment="LIN alive counter (0-15 rolling)", **kw),

            Signal(samples=np.array(cs_int, dtype=np.uint8), timestamps=ta,
                   name="lin_cs_int", unit="",
                   comment="LIN frame checksum integer", **kw),

            Signal(samples=_to_bytes_array(bcm_state), timestamps=ta,
                   name="lin_bcm_state", unit="",
                   comment="BCM wiper state text", encoding="utf-8", **kw),

            Signal(samples=_to_bytes_array(raw_hex), timestamps=ta,
                   name="lin_raw", unit="",
                   comment="Raw LIN frame bytes (hex string)", encoding="utf-8", **kw),
        ]

    # ─────────────────────────────────────────────────────────────────────
    #  Groupe CAN
    # ─────────────────────────────────────────────────────────────────────

    def _build_can(self, rows: list, Signal, source) -> list:
        """
        Canaux CAN : ID, direction, DLC, payload (8 octets séparés),
        champs décodés des trames Wiper_Command/Status/Ack.
        """
        if not rows:
            return []

        t0 = _parse_ts(rows[0].get("timestamp", "")) or 0.0

        ts           : list[float]         = []
        can_id       : list[int]           = []
        can_id_hex   : list[str]           = []
        direction    : list[int]           = []
        dlc          : list[int]           = []
        payload_rows : list[np.ndarray]    = []
        wiper_cmd    : list[int]           = []
        wiper_status : list[int]           = []
        wiper_ack    : list[int]           = []

        for r in rows:
            t = _parse_ts(r.get("timestamp", ""))
            ts.append(t - t0)

            cid = _parse_can_id(r.get("can_id", r.get("id", 0)))
            can_id.append(cid)
            can_id_hex.append(f"0x{cid:03X}")

            d = str(r.get("direction", r.get("dir", "RX"))).upper().strip()
            direction.append(1 if d == "TX" else 0)

            dlc.append(_i(r.get("dlc", 8)))

            payload_rows.append(_parse_payload_bytes(
                r.get("payload", r.get("data", "")),
                dlc=_i(r.get("dlc", 8))
            ))

            wiper_cmd.append(_decode_can_field(r.get("wiper_cmd", 0)))
            wiper_status.append(_decode_can_field(r.get("wiper_status", 0)))
            wiper_ack.append(_decode_can_field(r.get("wiper_ack", 0)))

        ta = np.array(ts, dtype=np.float64)
        kw = {"source": source}

        # Payload : tableau 2D (N, 8) → 8 signaux uint8 séparés (B0…B7)
        # car MDF4 ne supporte pas nativement les tableaux 2D en Signal simple
        payload_arr = np.stack(payload_rows, axis=0)   # shape (N, 8)

        signals = [
            Signal(samples=np.array(can_id, dtype=np.uint32), timestamps=ta,
                   name="can_id", unit="",
                   comment="CAN frame ID (decimal, ex. 512 = 0x200)", **kw),

            Signal(samples=_to_bytes_array(can_id_hex), timestamps=ta,
                   name="can_id_hex", unit="",
                   comment="CAN frame ID (hex string, ex. '0x200')",
                   encoding="utf-8", **kw),

            Signal(samples=np.array(direction, dtype=np.uint8), timestamps=ta,
                   name="can_direction", unit="",
                   comment="Frame direction",
                   conversion=_CONV_DIRCAN, **kw),

            Signal(samples=np.array(dlc, dtype=np.uint8), timestamps=ta,
                   name="can_dlc", unit="bytes",
                   comment="CAN Data Length Code", **kw),

            Signal(samples=np.array(wiper_cmd, dtype=np.int32), timestamps=ta,
                   name="can_wiper_cmd", unit="",
                   comment="Decoded field from 0x200 Wiper_Command", **kw),

            Signal(samples=np.array(wiper_status, dtype=np.int32), timestamps=ta,
                   name="can_wiper_status", unit="",
                   comment="Decoded field from 0x201 Wiper_Status", **kw),

            Signal(samples=np.array(wiper_ack, dtype=np.int32), timestamps=ta,
                   name="can_wiper_ack", unit="",
                   comment="Decoded field from 0x202 Wiper_Ack", **kw),
        ]

        # Octets payload B0…B7
        for byte_idx in range(8):
            signals.append(
                Signal(samples=payload_arr[:, byte_idx].copy(), timestamps=ta,
                       name=f"can_payload_B{byte_idx}", unit="",
                       comment=f"CAN payload byte {byte_idx} (raw hex octet)", **kw)
            )

        return signals

    # ─────────────────────────────────────────────────────────────────────
    #  Groupe PUMP
    # ─────────────────────────────────────────────────────────────────────

    def _build_pump(self, rows: list, Signal, source) -> list:
        """
        Canaux pompe : débit, pression, courant, état, direction,
        actif, timeout_elapsed.
        """
        if not rows:
            return []

        t0 = _parse_ts(rows[0].get("timestamp", "")) or 0.0

        ts       : list[float] = []
        flow     : list[float] = []
        pressure : list[float] = []
        current  : list[float] = []
        active   : list[int]   = []
        state    : list[str]   = []
        direction: list[str]   = []
        timeout  : list[float] = []

        for r in rows:
            t = _parse_ts(r.get("timestamp", ""))
            ts.append(t - t0)
            flow.append(_f(r.get("flow", 0)))
            pressure.append(_f(r.get("pressure", 0)))
            current.append(_f(r.get("current", 0)))
            active.append(_b(r.get("active", 0)))
            state.append(str(r.get("state", "OFF")).strip() or "OFF")
            direction.append(str(r.get("direction", "")).strip())
            timeout.append(_f(r.get("timeout_elapsed", 0)))

        ta = np.array(ts, dtype=np.float64)
        kw = {"source": source}

        return [
            Signal(samples=np.array(flow, dtype=np.float32), timestamps=ta,
                   name="pump_flow", unit="L/min",
                   comment="Pump flow rate", **kw),

            Signal(samples=np.array(pressure, dtype=np.float32), timestamps=ta,
                   name="pump_pressure", unit="bar",
                   comment="Pump output pressure", **kw),

            Signal(samples=np.array(current, dtype=np.float32), timestamps=ta,
                   name="pump_current", unit="A",
                   comment="Pump motor current (ADS1115)", **kw),

            Signal(samples=np.array(active, dtype=np.uint8), timestamps=ta,
                   name="pump_active", unit="bool",
                   comment="1=pump running 0=stopped", **kw),

            Signal(samples=_to_bytes_array(state), timestamps=ta,
                   name="pump_state", unit="",
                   comment="Pump state string: OFF/FORWARD/REVERSE",
                   encoding="utf-8", **kw),

            Signal(samples=_to_bytes_array(direction), timestamps=ta,
                   name="pump_direction", unit="",
                   comment="Pump direction: FORWARD/REVERSE/OFF",
                   encoding="utf-8", **kw),

            Signal(samples=np.array(timeout, dtype=np.float32), timestamps=ta,
                   name="pump_timeout_elapsed", unit="s",
                   comment="Elapsed time since pump activation (FSR_005 watchdog)", **kw),
        ]