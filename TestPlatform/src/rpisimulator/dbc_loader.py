#!/usr/bin/env python3
"""
dbc_loader.py  —  Chargeur DBC partagé WipeWash
=================================================
Module autonome sans dépendance externe (pas de python-can requis).
Parseur DBC maison compatible avec le subset utilisé par WipeWash.

Expose une seule fonction publique :

    cfg = load_dbc(path)

    cfg["messages"]       → dict   msg_id(int) → MessageDef
    cfg["id_map"]         → dict   msg_id → msg_name
    cfg["periods_ms"]     → dict   msg_id → cycle_time_ms (0 = event)
    cfg["nodes"]          → list   noms des noeuds déclarés

Structure cfg["messages"][msg_id] :
    MessageDef(namedtuple) :
      .msg_id   : int          # ex. 0x200 = 512
      .name     : str          # "Wiper_Command"
      .dlc      : int          # nombre d'octets
      .sender   : str          # noeud émetteur
      .signals  : dict         # nom_signal → SignalDef

Structure SignalDef :
    SignalDef(namedtuple) :
      .start_bit : int
      .length    : int
      .is_little_endian : bool
      .is_signed : bool
      .factor    : float
      .offset    : float
      .min_val   : float
      .max_val   : float
      .unit      : str
      .receivers : list[str]
      .values    : dict        # int → str  (VAL_ table, peut être vide)

Helpers d'encodage/décodage :
    raw  = encode_signal(signal_def, physical_value) → int
    phys = decode_signal(signal_def, raw_value)      → float
    data = pack_frame(message_def, signal_values)    → bytes  (DLC octets)
    signal_values = unpack_frame(message_def, data)  → dict   nom → float

Fallback intégré :
    Si le fichier DBC est absent ou mal formé, load_dbc() retourne la
    configuration codée en dur du projet WipeWash et logue un avertissement.
    Aucune exception n'est propagée.

Usage :
    from dbc_loader import load_dbc, pack_frame, unpack_frame
    cfg  = load_dbc("/path/to/wiperwash.dbc")
    msgs = cfg["messages"]
    m200 = msgs[0x200]
    data = pack_frame(m200, {"WiperMode": 2, "WiperSpeedLevel": 1,
                             "WashRequest": 0, "AliveCounter_TX": 5, "CRC_CMD": 0})
"""

import os
import re
import logging
from collections import namedtuple

log = logging.getLogger("dbc_loader")

# ─────────────────────────────────────────────────────────────────────────
#  TYPES DE DONNÉES
# ─────────────────────────────────────────────────────────────────────────

SignalDef = namedtuple("SignalDef", [
    "start_bit", "length", "is_little_endian", "is_signed",
    "factor", "offset", "min_val", "max_val",
    "unit", "receivers", "values",
])

MessageDef = namedtuple("MessageDef", [
    "msg_id", "name", "dlc", "sender", "signals",
])


# ─────────────────────────────────────────────────────────────────────────
#  HELPERS ENCODE / DECODE
# ─────────────────────────────────────────────────────────────────────────

def encode_signal(sig: SignalDef, physical: float) -> int:
    """Convertit une valeur physique en valeur brute (raw) entière."""
    raw = round((physical - sig.offset) / sig.factor)
    max_raw = (1 << sig.length) - 1
    if sig.is_signed:
        half = 1 << (sig.length - 1)
        raw = max(-half, min(half - 1, raw))
    else:
        raw = max(0, min(max_raw, raw))
    return int(raw)


def decode_signal(sig: SignalDef, raw: int) -> float:
    """Convertit une valeur brute en valeur physique."""
    if sig.is_signed:
        half = 1 << (sig.length - 1)
        if raw >= half:
            raw -= (1 << sig.length)
    return raw * sig.factor + sig.offset


def pack_frame(msg: MessageDef, signal_values: dict) -> bytes:
    """
    Construit un payload bytes[DLC] à partir d'un dict nom_signal → valeur_physique.
    Signaux absents du dict → valeur 0.
    Supporte uniquement little-endian (Motorola big-endian non utilisé par WipeWash).
    """
    data = bytearray(msg.dlc)
    for name, sig in msg.signals.items():
        phys = signal_values.get(name, 0.0)
        raw  = encode_signal(sig, phys)
        # Placement little-endian : start_bit = LSB
        bit_pos = sig.start_bit
        for i in range(sig.length):
            byte_idx = bit_pos // 8
            bit_idx  = bit_pos % 8
            if byte_idx < msg.dlc:
                if raw & (1 << i):
                    data[byte_idx] |= (1 << bit_idx)
            bit_pos += 1
    return bytes(data)


def unpack_frame(msg: MessageDef, data: bytes) -> dict:
    """
    Décode un payload bytes en dict nom_signal → valeur_physique.
    Supporte uniquement little-endian.
    """
    result = {}
    for name, sig in msg.signals.items():
        raw = 0
        bit_pos = sig.start_bit
        for i in range(sig.length):
            byte_idx = bit_pos // 8
            bit_idx  = bit_pos % 8
            if byte_idx < len(data):
                if data[byte_idx] & (1 << bit_idx):
                    raw |= (1 << i)
            bit_pos += 1
        result[name] = decode_signal(sig, raw)
    return result


# ─────────────────────────────────────────────────────────────────────────
#  CONFIGURATION PAR DÉFAUT  (config WipeWash codée en dur)
# ─────────────────────────────────────────────────────────────────────────

def _make_sig(start_bit, length, factor=1.0, offset=0.0,
              min_val=0.0, max_val=0.0, unit="",
              is_signed=False, receivers=None, values=None):
    return SignalDef(
        start_bit=start_bit, length=length,
        is_little_endian=True, is_signed=is_signed,
        factor=factor, offset=offset,
        min_val=min_val, max_val=max_val,
        unit=unit,
        receivers=receivers or [],
        values=values or {},
    )


def _default_config() -> dict:
    """
    Configuration DBC codée en dur pour WipeWash.
    Utilisée si le fichier .dbc est absent ou invalide.
    """
    # ── 0x200 : Wiper_Command (BCM → WC) — WW-MCAT-005 Rev5 ──────────
    msg_200 = MessageDef(
        msg_id=0x200, name="Wiper_Command", dlc=8, sender="BCM",
        signals={
            "WiperMode":       _make_sig(0,  4, values={0:"OFF",1:"TOUCH",2:"SPEED1",
                                                         3:"SPEED2",4:"AUTO",5:"WASH"},
                                          receivers=["WC","SIM"]),
            "WiperSpeedLevel": _make_sig(4,  4, values={0:"OFF",1:"Low",2:"High"},
                                          receivers=["WC","SIM"]),
            "WashRequest":     _make_sig(8,  2, values={0:"None",1:"Front",2:"Rear"},
                                          receivers=["WC","SIM"]),
            "Reserved_1":      _make_sig(10, 6, receivers=["WC","SIM"]),
            "AliveCounter_TX": _make_sig(16, 8, receivers=["WC","SIM"]),
            "Checksum":        _make_sig(24, 8, receivers=["WC","SIM"]),
            "Reserved_2":      _make_sig(32, 8, receivers=["WC","SIM"]),
            "Reserved_3":      _make_sig(40, 8, receivers=["WC","SIM"]),
            "Reserved_4":      _make_sig(48, 8, receivers=["WC","SIM"]),
            "Reserved_5":      _make_sig(56, 8, receivers=["WC","SIM"]),
        }
    )

    # ── 0x201 : Wiper_Status (WC → BCM) — WW-MCAT-005 Rev5 ──────────
    msg_201 = MessageDef(
        msg_id=0x201, name="Wiper_Status", dlc=8, sender="WC",
        signals={
            "CurrentMode":             _make_sig(0,  8, receivers=["BCM","SIM"]),
            "CurrentSpeed":            _make_sig(8,  8, receivers=["BCM","SIM"]),
            "BladePosition":           _make_sig(16, 8, max_val=100.0, unit="%",
                                                  receivers=["BCM","SIM"]),
            "MotorCurrent":            _make_sig(24, 8, factor=0.1, max_val=25.5,
                                                  unit="A", receivers=["BCM","SIM"]),
            "FaultStatus_WC_Internal": _make_sig(32, 1, receivers=["BCM","SIM"]),
            "FaultStatus_MotorDriver": _make_sig(33, 1, receivers=["BCM","SIM"]),
            "FaultStatus_PosSensor":   _make_sig(34, 1, receivers=["BCM","SIM"]),
            "FaultStatus_Supply":      _make_sig(35, 1, receivers=["BCM","SIM"]),
            "FaultStatus_CAN_Timeout": _make_sig(36, 1, receivers=["BCM","SIM"]),
            "FaultStatus_MotorBlocked":_make_sig(37, 1, receivers=["BCM","SIM"]),
            "FaultStatus_Reserved":    _make_sig(38, 2, receivers=["BCM","SIM"]),
            "AliveCounter_RX":         _make_sig(40, 8, receivers=["BCM","SIM"]),
            "CRC_Low":                 _make_sig(48, 8, receivers=["BCM","SIM"]),
            "Reserved_201":            _make_sig(56, 8, receivers=["BCM","SIM"]),
        }
    )

    # ── 0x202 : Wiper_Ack (WC → BCM, event) — WW-MCAT-005 Rev5, DLC=4
    msg_202 = MessageDef(
        msg_id=0x202, name="Wiper_Ack", dlc=4, sender="WC",
        signals={
            "AckStatus":       _make_sig(0,  1, values={0:"ACK",1:"NACK"},
                                          receivers=["BCM","SIM"]),
            "Ack_Reserved":    _make_sig(1,  7, receivers=["BCM","SIM"]),
            "ErrorCode":       _make_sig(8,  8,
                                          values={0:"NoError",1:"InvalidCmd",2:"MotorBlocked",
                                                  3:"Overcurrent",4:"PosSensorFault",
                                                  5:"InternalFault",6:"SupplyFault",7:"Busy"},
                                          receivers=["BCM","SIM"]),
            "AliveCounter_AK": _make_sig(16, 8, receivers=["BCM","SIM"]),
            "CRC_Ack":         _make_sig(24, 8, receivers=["BCM","SIM"]),
        }
    )

    # ── 0x300 : Vehicle_Status (SIM → BCM) ───────────────────────────
    msg_300 = MessageDef(
        msg_id=0x300, name="Vehicle_Status", dlc=8, sender="SIM",
        signals={
            "IgnitionStatus": _make_sig(0,  8, max_val=2.0,
                                         values={0:"OFF",1:"ON_ACC",2:"START"},
                                         receivers=["BCM"]),
            "ReverseGear":    _make_sig(8,  8, max_val=1.0,
                                         values={0:"DISENGAGED",1:"ENGAGED"},
                                         receivers=["BCM"]),
            "VehicleSpeed":   _make_sig(16, 16, factor=0.1, max_val=6553.5,
                                         unit="km/h", receivers=["BCM"]),
            "Reserved_300":   _make_sig(32, 32, receivers=["BCM"]),
        }
    )

    # ── 0x301 : RainSensorData (SIM → BCM) ───────────────────────────
    msg_301 = MessageDef(
        msg_id=0x301, name="RainSensorData", dlc=8, sender="SIM",
        signals={
            "RainIntensity": _make_sig(0,  8, max_val=255.0, receivers=["BCM"]),
            "SensorOK":      _make_sig(8,  8, max_val=1.0,
                                        values={0:"FAULT",1:"OK"},
                                        receivers=["BCM"]),
            "Reserved_301":  _make_sig(16, 48, receivers=["BCM"]),
        }
    )

    messages = {
        0x200: msg_200,
        0x201: msg_201,
        0x202: msg_202,
        0x300: msg_300,
        0x301: msg_301,
    }
    id_map = {mid: m.name for mid, m in messages.items()}
    periods_ms = {
        0x200: 20,    # Wiper_Command  BCM→WC  20ms
        0x201: 20,    # Wiper_Status   WC→BCM  20ms
        0x202: 0,     # event-based
        0x300: 200,
        0x301: 200,
    }
    return {
        "messages":   messages,
        "id_map":     id_map,
        "periods_ms": periods_ms,
        "nodes":      ["BCM", "WC", "SIM"],
    }


# ─────────────────────────────────────────────────────────────────────────
#  PARSEUR DBC MAISON
# ─────────────────────────────────────────────────────────────────────────

def _parse_dbc(text: str) -> dict:
    """
    Parse un fichier DBC (subset WipeWash) et retourne le dict de config.
    Sections lues : BU_, BO_, SG_, BA_ (GenMsgCycleTime), VAL_, CM_.
    """

    def _strip_comments(src):
        # Supprimer commentaires /* */ et // (hors strings)
        src = re.sub(r'/\*.*?\*/', '', src, flags=re.DOTALL)
        src = re.sub(r'//[^\n]*', '', src)
        return src

    clean = _strip_comments(text)

    # ── Noeuds BU_ ────────────────────────────────────────────────────
    nodes = []
    m = re.search(r'\bBU_\s*:(.*?)(?=\n\s*\n|\bBO_|\bBS_|\Z)', clean, re.DOTALL)
    if m:
        nodes = re.findall(r'\w+', m.group(1))

    # ── Messages BO_ + Signaux SG_ ───────────────────────────────────
    messages: dict = {}

    # Pattern: BO_ <id> <name>: <dlc> <sender>
    bo_pat = re.compile(
        r'\bBO_\s+(\d+)\s+(\w+)\s*:\s*(\d+)\s+(\w+)(.*?)(?=\bBO_|\Z)',
        re.DOTALL
    )
    # Pattern SG_: nom : start_bit|length@byte_order value_type (factor,offset) [min|max] "unit" receivers
    sg_pat = re.compile(
        r'\bSG_\s+(\w+)\s*:\s*'
        r'(\d+)\|(\d+)@([01])([+-])\s*'
        r'\(([^,]+),([^)]+)\)\s*'
        r'\[([^|]*)\|([^\]]*)\]\s*'
        r'"([^"]*)"\s*'
        r'([^\n]*)'   # FIX: s'arrêter à la fin de ligne (évite d'avaler les SG_ suivants)
    )

    for bom in bo_pat.finditer(clean):
        msg_id   = int(bom.group(1))
        msg_name = bom.group(2)
        dlc      = int(bom.group(3))
        sender   = bom.group(4)
        body     = bom.group(5)

        signals = {}
        for sgm in sg_pat.finditer(body):
            sname     = sgm.group(1)
            start_bit = int(sgm.group(2))
            length    = int(sgm.group(3))
            is_le     = sgm.group(4) == "1"
            is_signed = sgm.group(5) == "-"
            factor    = float(sgm.group(6))
            offset    = float(sgm.group(7))
            min_val   = float(sgm.group(8)) if sgm.group(8).strip() else 0.0
            max_val   = float(sgm.group(9)) if sgm.group(9).strip() else 0.0
            unit      = sgm.group(10)
            receivers = [r.strip() for r in sgm.group(11).split(',') if r.strip()]

            signals[sname] = SignalDef(
                start_bit=start_bit, length=length,
                is_little_endian=is_le, is_signed=is_signed,
                factor=factor, offset=offset,
                min_val=min_val, max_val=max_val,
                unit=unit, receivers=receivers,
                values={},   # rempli plus bas par VAL_
            )

        messages[msg_id] = MessageDef(
            msg_id=msg_id, name=msg_name, dlc=dlc,
            sender=sender, signals=signals,
        )

    # ── VAL_ (tables de valeurs) ──────────────────────────────────────
    val_pat = re.compile(
        r'\bVAL_\s+(\d+)\s+(\w+)((?:\s+\d+\s+"[^"]*")+)\s*;',
        re.DOTALL
    )
    entry_pat = re.compile(r'(\d+)\s+"([^"]*)"')

    for vm in val_pat.finditer(clean):
        mid   = int(vm.group(1))
        sname = vm.group(2)
        table_str = vm.group(3)
        table = {int(em.group(1)): em.group(2)
                 for em in entry_pat.finditer(table_str)}
        if mid in messages and sname in messages[mid].signals:
            old = messages[mid].signals[sname]
            messages[mid].signals[sname] = old._replace(values=table)
            # Reconstruire le MessageDef (signals dict est mutable via _replace sur namedtuple signal)

    # ── BA_ GenMsgCycleTime ───────────────────────────────────────────
    periods_ms: dict = {}
    ba_pat = re.compile(
        r'\bBA_\s+"GenMsgCycleTime"\s+BO_\s+(\d+)\s+(\d+)\s*;'
    )
    for bam in ba_pat.finditer(clean):
        periods_ms[int(bam.group(1))] = int(bam.group(2))

    # Compléter les messages sans période avec 0
    for mid in messages:
        if mid not in periods_ms:
            periods_ms[mid] = 0

    id_map = {mid: m.name for mid, m in messages.items()}

    return {
        "messages":   messages,
        "id_map":     id_map,
        "periods_ms": periods_ms,
        "nodes":      nodes,
    }


# ─────────────────────────────────────────────────────────────────────────
#  POINT D'ENTRÉE PUBLIC
# ─────────────────────────────────────────────────────────────────────────

def load_dbc(path: str) -> dict:
    """
    Charge un fichier DBC et retourne le dict de configuration CAN.

    En cas d'erreur (fichier absent, parse raté), retourne la config
    par défaut et logue un avertissement — jamais d'exception.

    Paramètres
    ----------
    path : str
        Chemin absolu ou relatif vers le fichier .dbc

    Retour
    ------
    dict avec les clés : messages, id_map, periods_ms, nodes
    """
    if not path or not os.path.isfile(path):
        log.warning("[DBC] Fichier introuvable: '%s' — config par défaut utilisée", path)
        return None

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        cfg = _parse_dbc(text)

        if not cfg["messages"]:
            log.warning("[DBC] Aucun message parsé dans '%s' — config par défaut", path)
            return _default_config()

        log.info("[DBC] Chargé: %s | %d message(s) | %d noeud(s)",
                 path, len(cfg["messages"]), len(cfg["nodes"]))
        for mid, m in cfg["messages"].items():
            period = cfg["periods_ms"].get(mid, 0)
            log.info("[DBC]   0x%03X %-20s DLC=%d sender=%-5s period=%dms signals=%s",
                     mid, m.name, m.dlc, m.sender, period,
                     list(m.signals.keys()))
        return cfg

    except Exception as exc:
        log.warning("[DBC] Erreur parse '%s': %s — config par défaut", path, exc)
        return _default_config()