#!/usr/bin/env python3
"""
ldf_loader.py  —  Chargeur LDF partagé WipeWash
=================================================
Module autonome sans dépendance externe au projet.
Parseur LDF maison (pas besoin de ldfparser installé sur le target RPi).

Expose une seule fonction publique :

    cfg = load_ldf(path)

    cfg["baud"]          → int    ex. 19200
    cfg["frames"]        → dict   nom → {id, pid, dlc, cycle_s, signals}
    cfg["pid_map"]       → dict   pid → frame_name   (dispatch slave)
    cfg["schedule"]      → list   [(frame_name, delay_s), ...]

Structure cfg["frames"][name] :
    {
        "id":       int,          # ID 6 bits (0x16, 0x17, ...)
        "pid":      int,          # PID calculé ISO 17987
        "dlc":      int,          # nombre d'octets de données
        "cycle_s":  float,        # période en secondes (depuis Schedule_tables)
        "signals":  dict          # nom_signal → {start_bit, length}
    }

Fallback intégré :
    Si le fichier LDF est absent ou mal formé, load_ldf() retourne la
    configuration par défaut du projet WipeWash (19200 baud, 0x16/0x17)
    et log un avertissement. Aucune exception n'est propagée.

Usage :
    from ldf_loader import load_ldf
    cfg = load_ldf("/path/to/wiperwash.ldf")
    baud    = cfg["baud"]
    frames  = cfg["frames"]
    pid_map = cfg["pid_map"]
"""

import os
import re
import logging

log = logging.getLogger("ldf_loader")

# ─────────────────────────────────────────────────────────────────────────
#  CALCUL PID LIN (ISO 17987)  — identique dans bcm_rte et crslin
# ─────────────────────────────────────────────────────────────────────────

def _calculate_pid(frame_id: int) -> int:
    """Calcule le PID LIN (6 bits ID + 2 bits de parité P0/P1)."""
    frame_id &= 0x3F
    p0 = (frame_id ^ (frame_id >> 1) ^ (frame_id >> 2) ^ (frame_id >> 4)) & 0x01
    p1 = (~((frame_id >> 1) ^ (frame_id >> 3) ^ (frame_id >> 4) ^ (frame_id >> 5))) & 0x01
    return frame_id | (p0 << 6) | (p1 << 7)


# ─────────────────────────────────────────────────────────────────────────
#  CONFIGURATION PAR DÉFAUT  (utilisée si LDF absent ou invalide)
# ─────────────────────────────────────────────────────────────────────────

def _default_config() -> dict:
    """
    Retourne la configuration LIN codée en dur du projet WipeWash.
    Garantit que le système fonctionne même sans fichier LDF.
    """
    frames = {
        "LeftStickWiperRequester": {
            "id":      0x16,
            "pid":     _calculate_pid(0x16),  # 0xD6
            "dlc":     2,
            "cycle_s": 0.400,
            "signals": {
                "WiperOp":    {"start_bit": 0, "length": 4},
                "StickStatus":{"start_bit": 4, "length": 4},
                "AliveCtr":   {"start_bit": 8, "length": 8},
            },
        },
        "WiperFaultStatus": {
            "id":      0x17,
            "pid":     _calculate_pid(0x17),  # 0x97
            "dlc":     2,
            "cycle_s": 0.800,
            "signals": {
                "FaultCode":  {"start_bit": 0, "length": 8},
                "Reserved17": {"start_bit": 8, "length": 8},
            },
        },
    }
    pid_map   = {f["pid"]: name for name, f in frames.items()}
    schedule  = [(name, f["cycle_s"]) for name, f in frames.items()]

    return {
        "baud":     19200,
        "frames":   frames,
        "pid_map":  pid_map,
        "schedule": schedule,
    }


# ─────────────────────────────────────────────────────────────────────────
#  PARSEUR LDF MAISON  (LIN 2.x subset)
# ─────────────────────────────────────────────────────────────────────────

def _parse_ldf(text: str) -> dict:
    """
    Parse un fichier LDF LIN 2.x et retourne le dict de configuration.
    Gère les sections : LIN_speed, Signals, Frames, Schedule_tables.
    Supporte les blocs avec accolades imbriquées.
    """

    # ── helpers ──────────────────────────────────────────────────────
    def _strip_comments(src):
        src = re.sub(r'/\*.*?\*/', '', src, flags=re.DOTALL)
        src = re.sub(r'//[^\n]*', '', src)
        return src

    def _extract_block(src, keyword):
        """
        Retourne le contenu entre { } de la section `keyword`.
        Gère correctement les accolades imbriquées (frames avec signaux).
        """
        pat = re.compile(r'\b' + re.escape(keyword) + r'\s*\{', re.IGNORECASE)
        m = pat.search(src)
        if not m:
            return ""
        start = m.end()
        depth = 1
        i = start
        while i < len(src) and depth > 0:
            if src[i] == '{':
                depth += 1
            elif src[i] == '}':
                depth -= 1
            i += 1
        return src[start:i - 1]

    def _int(s):
        s = s.strip()
        if s.startswith("0x") or s.startswith("0X"):
            return int(s, 16)
        return int(s)

    clean = _strip_comments(text)

    # ── Baud rate ─────────────────────────────────────────────────────
    baud = 19200
    m = re.search(r'LIN_speed\s*=\s*([\d.]+)\s*kbps', clean, re.IGNORECASE)
    if m:
        baud = int(float(m.group(1)) * 1000)

    # ── Signals  (nom : longueur, init, publisher, subscriber) ────────
    signals_raw: dict[str, int] = {}   # nom → longueur en bits
    sig_block = _extract_block(clean, "Signals")
    for m in re.finditer(r'(\w+)\s*:\s*(\d+)\s*,', sig_block):
        signals_raw[m.group(1)] = int(m.group(2))

    # ── Frames ────────────────────────────────────────────────────────
    frames: dict = {}
    frames_block = _extract_block(clean, "Frames")

    # Chaque frame : nom : id, publisher, dlc { signal, start_bit; ... }
    # On extrait frame par frame en cherchant les sous-blocs imbriqués.
    frame_header_pat = re.compile(
        r'(\w+)\s*:\s*(0x[0-9A-Fa-f]+|\d+)\s*,\s*\w+\s*,\s*(\d+)\s*\{',
    )
    pos = 0
    while pos < len(frames_block):
        fhm = frame_header_pat.search(frames_block, pos)
        if not fhm:
            break
        fname = fhm.group(1)
        fid   = _int(fhm.group(2))
        dlc   = int(fhm.group(3))
        # Extraire le contenu du bloc { ... } de cette frame
        bstart = fhm.end()
        depth  = 1
        i      = bstart
        while i < len(frames_block) and depth > 0:
            if frames_block[i] == '{':
                depth += 1
            elif frames_block[i] == '}':
                depth -= 1
            i += 1
        sig_txt = frames_block[bstart:i - 1]
        pos     = i

        sigs = {}
        for sm in re.finditer(r'(\w+)\s*,\s*(\d+)\s*;', sig_txt):
            sname     = sm.group(1)
            start_bit = int(sm.group(2))
            length    = signals_raw.get(sname, 8)
            sigs[sname] = {"start_bit": start_bit, "length": length}

        frames[fname] = {
            "id":      fid,
            "pid":     _calculate_pid(fid),
            "dlc":     dlc,
            "cycle_s": 0.0,   # sera rempli par Schedule_tables
            "signals": sigs,
        }

    # ── Schedule_tables  (première table trouvée) ─────────────────────
    schedule: list = []
    sched_outer = _extract_block(clean, "Schedule_tables")
    if sched_outer:
        # Chercher la première table interne (elle-même un bloc imbriqué)
        inner_header = re.search(r'\w+\s*\{', sched_outer)
        if inner_header:
            istart = inner_header.end()
            depth  = 1
            i      = istart
            while i < len(sched_outer) and depth > 0:
                if sched_outer[i] == '{':
                    depth += 1
                elif sched_outer[i] == '}':
                    depth -= 1
                i += 1
            inner = sched_outer[istart:i - 1]
            entry_pat = re.compile(
                r'(\w+)\s+delay\s+([\d.]+)\s*ms', re.IGNORECASE
            )
            for em in entry_pat.finditer(inner):
                fname_s = em.group(1)
                delay_s = float(em.group(2)) / 1000.0
                schedule.append((fname_s, delay_s))
                if fname_s in frames:
                    frames[fname_s]["cycle_s"] = delay_s

    # ── pid_map  (PID → nom frame) ────────────────────────────────────
    pid_map = {f["pid"]: name for name, f in frames.items()}

    return {
        "baud":     baud,
        "frames":   frames,
        "pid_map":  pid_map,
        "schedule": schedule,
    }


# ─────────────────────────────────────────────────────────────────────────
#  POINT D'ENTRÉE PUBLIC
# ─────────────────────────────────────────────────────────────────────────

def load_ldf(path: str) -> dict:
    """
    Charge un fichier LDF et retourne le dict de configuration LIN.

    En cas d'erreur (fichier absent, parse raté), retourne la config
    par défaut et log un avertissement — jamais d'exception.

    Paramètres
    ----------
    path : str
        Chemin absolu ou relatif vers le fichier .ldf

    Retour
    ------
    dict avec les clés : baud, frames, pid_map, schedule
    """
    if not path or not os.path.isfile(path):
        log.warning("[LDF] Fichier introuvable: '%s' — config par défaut utilisée", path)
        return _default_config()

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        cfg = _parse_ldf(text)

        if not cfg["frames"]:
            log.warning("[LDF] Aucune frame parsée dans '%s' — config par défaut", path)
            return _default_config()

        log.info("[LDF] Chargé: %s | baud=%d | %d frame(s) | schedule=%s",
                 path, cfg["baud"], len(cfg["frames"]),
                 [(n, f"{d*1000:.0f}ms") for n, d in cfg["schedule"]])
        for name, f in cfg["frames"].items():
            log.info("[LDF]   %-30s  ID=0x%02X  PID=0x%02X  DLC=%d  cycle=%.0fms  signals=%s",
                     name, f["id"], f["pid"], f["dlc"], f["cycle_s"] * 1000,
                     list(f["signals"].keys()))
        return cfg

    except Exception as exc:
        log.warning("[LDF] Erreur parse '%s': %s — config par défaut", path, exc)
        return _default_config()


# Public class alias expected by tests
class LDFLoader:
    """Thin class wrapper around the load_ldf() function."""

    def __init__(self):
        self.data = None
        self.frames = None

    def load(self, path: str):
        self.data = load_ldf(path)
        self.frames = self.data.get("frames") if self.data else None
        return self.data
