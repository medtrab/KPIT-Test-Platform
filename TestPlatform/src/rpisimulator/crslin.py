#!/usr/bin/env python3
"""
crslin.py - CRS LIN Slave Node
=================================
Recoit des commandes essuie-glace depuis un client PyQt6 via TCP,
les encode dans la trame LIN 0x16 (LeftStickWiperRequester),
et repond au maitre LIN (BCM / autre RPi) sur le bus UART/TJA1020.

USAGE STANDALONE:
    python3 crslin.py [--host 0.0.0.0] [--port 5555]

USAGE MODULE (depuis main.py):
    import crslin
    crslin.start(tcp_host="0.0.0.0", tcp_port=5555)   # bloquant

HARDWARE:
    UART LIN : /dev/serial0 via transceiver TJA1020
    TCP      : port 5555 par defaut

PROTOCOLE TCP (JSON par ligne):
    Client -> Server : {"cmd": "TOUCH"}
    Client -> Server : {"test_cmd": "stop_lin_tx"}   # pause reponses LIN (T10)
    Client -> Server : {"test_cmd": "start_lin_tx"}  # reprise reponses LIN
    Server -> Client : {"type": "tx16",    "op": "TOUCH", "b0": "0x11", "alive": 5, "cs": "0xAB"}
    Server -> Client : {"type": "rx_hdr",  "pid": "0xD6", "raw": "0x55 0xD6"}
    Server -> Client : {"type": "cmd_ack", "op": "TOUCH", "val": 1}
    Server -> Client : {"type": "error",   "msg": "..."}
    Server -> Client : {"type": "info",    "msg": "..."}
    Server -> Client : {"type": "fault",   "msg": "lin timeout detected"}  # quand pause active

TRAMES LIN GEREES:
    PID 0xD6 (ID 0x16) : LeftStickWiperRequester  -> RPi repond avec op + alive
    PID 0x97 (ID 0x17) : WiperFaultStatus         -> RPi repond avec fault code
"""

import serial
import socket
import select
import threading
import json
import time
import logging
from dataclasses import dataclass, field
from enum import IntEnum

# ============================================================
# CHARGEMENT LDF  —  toutes les constantes LIN viennent du LDF
# ============================================================
import sys as _sys, os as _os

# Chercher ldf_loader.py : même répertoire → répertoire parent → répertoire projet
_here = _os.path.dirname(_os.path.abspath(__file__))
for _d in (_here, _os.path.dirname(_here)):
    if _d not in _sys.path:
        _sys.path.insert(0, _d)

try:
    from ldf_loader import load_ldf as _load_ldf, _calculate_pid
    _LDF_LOADER_OK = True
except ImportError:
    _LDF_LOADER_OK = False
    def _calculate_pid(frame_id):
        frame_id &= 0x3F
        p0 = (frame_id ^ (frame_id >> 1) ^ (frame_id >> 2) ^ (frame_id >> 4)) & 0x01
        p1 = (~((frame_id >> 1) ^ (frame_id >> 3) ^ (frame_id >> 4) ^ (frame_id >> 5))) & 0x01
        return frame_id | (p0 << 6) | (p1 << 7)

# Valeurs par défaut — écrasées par load_ldf_into_crslin() au démarrage
_LIN_BAUD    = 19200

# ── TC_COM_001 : mesure physique baudrate via durée BREAK ────────────────
# Accumulateurs pour moyenner sur N trames (réduit le jitter USB-Serial)
_BAUD_MEAS_EVERY      = 5    # publier 1 mesure tous les 5 BREAK reçus
_baud_meas_zero_total  = 0   # somme des zero_count sur N trames
_baud_meas_time_total  = 0.0 # somme des t_break_s sur N trames (secondes)
_baud_meas_frame_count = 0   # nombre de trames accumulées
_LDF_CONFIG  = None   # dict complet issu du LDF
_PID_MAP     = {}     # pid → frame_name  (dispatch slave)
LIN_PID_16   = _calculate_pid(0x16)   # 0xD6
LIN_PID_17   = _calculate_pid(0x17)   # 0x97


def load_ldf_into_crslin(ldf_path: str) -> dict:
    """
    Charge le LDF et met à jour les constantes globales du module.
    Doit être appelé depuis start() ou main() avant tout thread LIN.
    Retourne le dict de config (baud, frames, pid_map, schedule).
    """
    global _LIN_BAUD, _LDF_CONFIG, _PID_MAP, LIN_PID_16, LIN_PID_17

    if not _LDF_LOADER_OK:
        log.warning("[LDF] ldf_loader introuvable — constantes par défaut conservées")
        return {}

    cfg = _load_ldf(ldf_path)
    _LDF_CONFIG = cfg
    _LIN_BAUD   = cfg["baud"]
    _PID_MAP    = cfg["pid_map"]

    frames = cfg["frames"]
    f16 = frames.get("LeftStickWiperRequester")
    f17 = frames.get("WiperFaultStatus")
    if f16:
        LIN_PID_16 = f16["pid"]
    if f17:
        LIN_PID_17 = f17["pid"]

    log.info("[LDF] crslin chargé depuis '%s' : baud=%d  PID_16=0x%02X  PID_17=0x%02X"
             "  frames=%s",
             ldf_path, _LIN_BAUD, LIN_PID_16, LIN_PID_17,
             list(frames.keys()))
    return cfg

# ============================================================
# CONFIGURATION PAR DEFAUT  (surchargeable via start())
# ============================================================
_LIN_PORT     = "/dev/serial0"
_TCP_HOST     = "0.0.0.0"
_TCP_PORT     = 5555

# Octets speciaux du protocole LIN (fixes, jamais dans le LDF)
LIN_BREAK_BYTE = 0x00
LIN_SYNC_BYTE  = 0x55
LIN_HDR_TMO    = 0.500        # timeout attente header complet (500ms)
                               # Long pour absorber les cycles BCM 400ms/800ms
LIN_BYTE_TMO   = 0.010        # timeout inter-octets (10ms, marge USB-Serial)
LIN_TX_BIT_US  = 52           # duree 1 bit @ 19200 baud (mis a jour apres LDF)
LIN_TX_BYTE_US = LIN_TX_BIT_US * 10 + 500   # ~570 us par octet (10 bits + marge)
LIN_ECHO_TMO   = 0.015        # timeout vidage echo loopback TJA1020 (15ms)
LIN_RETRY_S    = 3            # delai entre tentatives de reouverture du port

# Valeurs des champs LIN
STICK_VALID   = 0x01   # StickStatus bit0 = Valid
STICK_STUCK   = 0x04   # StickStatus bit2 = Stuck (bit6 de byte0)
FAULT_NONE         = 0x00   # pas de défaut
FAULT_STICK_SENSOR = 0x01   # capteur position levier HS → bit0
FAULT_SUPPLY       = 0x02   # alimentation interne hors plage → bit1
FAULT_INTERNAL_COM = 0x04   # défaut communication interne CRS → bit2
CRS_VERSION_NOMINAL  = 0x20   # version FW CRS nominale (acceptée par BCM)
CRS_VERSION_INVALID  = 0xFF   # version invalide → trame ignorée par BCM

FAULT_NAMES = {
    FAULT_NONE:         "NO FAULT",
    FAULT_STICK_SENSOR: "STICK SENSOR",
    FAULT_SUPPLY:       "SUPPLY",
    FAULT_INTERNAL_COM: "INTERNAL COM",
}

# ============================================================
# LOGGING
# ============================================================
log = logging.getLogger("CRS")


# ============================================================
# FLAG PAUSE TX LIN  (test T10 : Platform envoie stop_lin_tx)
# Quand True, le thread LIN ne repond plus aux headers du BCM.
# Le BCM detecte alors l'absence de reponse et leve lin_timeout_active.
# ============================================================
_lin_tx_paused: bool = False
_lin_tx_paused_lock = threading.Lock()


def _set_lin_paused(paused: bool) -> None:
    global _lin_tx_paused
    with _lin_tx_paused_lock:
        _lin_tx_paused = paused


def _is_lin_paused() -> bool:
    with _lin_tx_paused_lock:
        return _lin_tx_paused


# ============================================================
# OPERATIONS ESSUIE-GLACE
# ============================================================

# ══════════════════════════════════════════════════════════════
#  Single Writer Lock — client côté simulateur LIN
# ══════════════════════════════════════════════════════════════
import uuid as _uuid_sim

class _SimLockClient:
    CLIENT_ID = f"Sim-LIN-{_uuid_sim.uuid4().hex[:6].upper()}"

    def __init__(self, redis_host="127.0.0.1", redis_port=6379):
        self._host  = redis_host
        self._port  = redis_port
        self._r     = None
        self._owned = False
        self._connect()

    def _connect(self):
        try:
            import redis as _r
            self._r = _r.Redis(host=self._host, port=self._port,
                               db=0, socket_connect_timeout=2, socket_timeout=2)
            self._r.ping()
        except Exception:
            self._r = None

    def acquire(self) -> bool:
        if self._r is None:
            self._connect()
        if self._r is None:
            return True
        try:
            import json, time as _t
            self._r.publish("rte_cmd", json.dumps({
                "key": "__lock_acquire__", "value": 1, "client": self.CLIENT_ID}))
            ps = self._r.pubsub(ignore_subscribe_messages=True)
            ps.subscribe("rte_lock_status")
            t0 = _t.time()
            while _t.time() - t0 < 0.5:
                msg = ps.get_message(ignore_subscribe_messages=True, timeout=0.1)
                if msg and msg.get("type") == "message":
                    info = json.loads(msg["data"])
                    if info.get("type") == "lock_ack":
                        ps.unsubscribe(); ps.close()
                        self._owned = info.get("ok", False)
                        return self._owned
            ps.unsubscribe(); ps.close()
            self._owned = True
            return True
        except Exception:
            return True

    def release(self):
        if self._r is None or not self._owned:
            return
        try:
            import json
            self._r.publish("rte_cmd", json.dumps({
                "key": "__lock_release__", "value": 1, "client": self.CLIENT_ID}))
            self._owned = False
        except Exception:
            pass

    def renew(self):
        if self._r is None or not self._owned:
            return
        try:
            import json
            self._r.publish("rte_cmd", json.dumps({
                "key": "__lock_renew__", "value": 1, "client": self.CLIENT_ID}))
        except Exception:
            pass


_lock_client = None   # initialisé dans start()

class WOp(IntEnum):
    OFF        = 0
    TOUCH      = 1
    SPEED1     = 2
    SPEED2     = 3
    AUTO       = 4
    FRONT_WASH = 5
    REAR_WASH  = 6
    REAR_WIPE  = 7


# ============================================================
# ETAT DU NOEUD LIN
# ============================================================
@dataclass
class NodeState:
    wiper_op    : WOp = WOp.OFF
    stick_status: int = STICK_VALID
    alive_ctr   : int = 0
    fault       : int = FAULT_NONE
    crs_version : int = CRS_VERSION_NOMINAL  # CRS firmware version (byte1 de 0x17 — 0x20=nominal)
    freeze_alive: bool = False       # TC_LIN_002 : gèle l'alive counter si True
    corrupt_checksum: bool = False   # TC_LIN_CS : force checksum invalide (0x00) sur 0x16
    raw_wiper_op: int = -1           # LIN_INVALID_CMD_001 : valeur brute bits 3:0 de byte0
                                     # -1 = inactif (utilise wiper_op normal)
                                     # ≥0 = valeur injectée une seule fois (one-shot)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self):
        with self._lock:
            return self.wiper_op, self.stick_status, self.alive_ctr, self.fault

    def set_op(self, op: WOp):
        with self._lock:
            self.wiper_op     = op
            self.stick_status = STICK_VALID

    def inc_alive(self):
        with self._lock:
            if not self.freeze_alive:
                self.alive_ctr = (self.alive_ctr + 1) & 0xFF
            # Si freeze_alive=True : alive_ctr inchangé → contre-mesure anti-replay


_state = NodeState()

# ============================================================
# LISTE DES CLIENTS TCP ET BROADCAST
# ============================================================
_clients      : list[socket.socket] = []
_clients_lock = threading.Lock()


def _tcp_broadcast(msg: dict, exclude: socket.socket = None):
    line = (json.dumps(msg) + "\n").encode("utf-8")
    with _clients_lock:
        dead = []
        for c in _clients:
            if c is exclude:
                continue
            try:
                c.sendall(line)
            except OSError as e:
                log.warning("[TCP] Broadcast echoue: %s", e)
                dead.append(c)
        for c in dead:
            _clients.remove(c)
            try:
                c.close()
            except OSError:
                pass


def _register_client(conn: socket.socket):
    with _clients_lock:
        _clients.append(conn)


def _unregister_client(conn: socket.socket):
    with _clients_lock:
        if conn in _clients:
            _clients.remove(conn)


# ============================================================
# CALCULS LIN BAS NIVEAU
# ============================================================
# _calculate_pid est importé depuis ldf_loader (ou défini en fallback ci-dessus).
# Défini ici uniquement si ldf_loader est indisponible (déjà fait dans le bloc import).


def _lin_checksum(pid: int, data: bytes) -> int:
    s = pid + sum(data)
    while s > 0xFF:
        s = (s & 0xFF) + (s >> 8)
    return (~s) & 0xFF


def _lin_send_response(ser: serial.Serial, pid: int, data: bytes) -> int:
    """
    Envoyer la reponse slave sur le bus LIN (slave → master).

    Strategie identique au code Arduino CRS v7 (linSendResponse) :
      1. Calculer le checksum LIN enhanced (PID + data avec carry-around)
      2. Construire le buffer complet [data... checksum] et envoyer en une fois
         (pas d'Inter-Byte Space entre les champs, conforme spec LIN)
      3. Attendre la fin TX (temps proportionnel au nombre d'octets)
      4. Drainer l'echo loopback TJA1020 (les octets TX reapparaissent en RX)

    Le drain de l'echo DOIT etre complet avant le prochain linReadHeader(),
    sinon les octets loopback polluent la detection du BREAK suivant.
    """
    cs = _lin_checksum(pid, data)
    # TC_LIN_CS : si corrupt_checksum actif, forcer un checksum volontairement faux
    # On utilise (cs ^ 0xFF) pour garantir que la valeur est toujours différente du
    # checksum valide (XOR 0xFF sur un octet != 0 change toujours la valeur).
    with _state._lock:
        _corrupt = _state.corrupt_checksum
    if _corrupt:
        cs = cs ^ 0xFF   # valeur garantie différente → BCM doit rejeter la trame
    response = data + bytes([cs])
    n_bytes  = len(response)

    ser.write(response)
    ser.flush()

    # Attendre la fin de la transmission :
    # n_bytes * 10 bits @ 19200 baud = n_bytes * 520us + 1ms marge
    tx_time_s = n_bytes * (10.0 / _LIN_BAUD) + 0.001
    time.sleep(tx_time_s)

    # Drainer l'echo loopback TJA1020 (exactement n_bytes octets attendus)
    # Timeout = LIN_ECHO_TMO (10ms) suffisant car l'echo est immediat
    drained  = 0
    deadline = time.time() + LIN_ECHO_TMO
    while drained < n_bytes and time.time() < deadline:
        avail = ser.in_waiting
        if avail:
            chunk = ser.read(min(avail, n_bytes - drained))
            drained += len(chunk)
        else:
            time.sleep(0.0002)

    # Si l'echo n'est pas complet dans le timeout : flush force TOUJOURS.
    # Meme si in_waiting==0 maintenant, des octets retardes arriveraient au
    # prochain header et pollueraient la detection du BREAK.
    if drained < n_bytes:
        ser.reset_input_buffer()

    return cs


def _lin_read_header(ser: serial.Serial):
    """
    Lire le header LIN envoye par le BCM (master) : BREAK + SYNC(0x55) + PID.

    Strategie identique au code Arduino CRS v7 (linReadHeader) :

    TJA1020 half-duplex loopback : quand le CRS (slave) a envoye sa reponse
    precedente, ces octets TX ont fait un loopback sur RX. La fonction
    _lin_send_response() les a draines, mais il peut rester des residus.
    On ignore donc tout jusqu'a trouver un 0x00 (debut du BREAK).

    Le vrai BREAK LIN est envoye par le BCM a baud/4 → vu comme plusieurs
    0x00 consecutifs a 19200. On consomme tous ces 0x00, puis on cherche
    le SYNC (0x55), puis le PID.

    Retourne (pid, raw_bytes) ou (None, raw_bytes) en cas d'echec.
    """
    deadline = time.time() + LIN_HDR_TMO
    raw = bytearray()

    # ── Etape 1 : attendre le premier 0x00 (debut du BREAK) ─────────────
    # On ignore tous les octets non-nuls (residus loopback de la reponse
    # precedente ou bruit bus). Seul un 0x00 marque le debut du BREAK.
    while time.time() < deadline:
        b = ser.read(1)
        if not b:
            continue
        raw += b
        if b[0] == LIN_BREAK_BYTE:
            break   # Premier 0x00 trouve
    else:
        # Timeout sans BREAK : pas d'activite sur le bus
        return None, bytes(raw)

    # ── Etape 2 : drainer tous les 0x00 restants du BREAK field ─────────
    # Le BREAK LIN dure >= 13 bits a baud/4, vu comme plusieurs 0x00
    # consecutifs a 19200. On les consomme jusqu'au premier octet non-nul.
    # Le timeout est reinitialise a chaque 0x00 recu (le break peut durer
    # plusieurs ms selon le master).
    #
    # TC_COM_001 — MESURE PHYSIQUE DU BAUDRATE :
    # On instrumente ici pour mesurer la duree reelle du BREAK sur le bus.
    # Principe :
    #   - chaque octet 0x00 represente 10 bits (start+8data+stop) a baudrate/4
    #   - t_break = temps entre le premier et le dernier 0x00
    #   - baudrate_effectif = (zero_count x 10 / t_break) x 4
    # Precision : +-3-5ms (latence USB-Serial) — suffit pour detecter
    # une mauvaise config (ex: 9600 vs 19200). Moyenne sur _BAUD_MEAS_EVERY
    # trames consecutives pour reduire le jitter.
    global _baud_meas_zero_total, _baud_meas_time_total, _baud_meas_frame_count
    t_break_start  = time.monotonic()   # timestamp au premier 0x00
    zero_count     = 1                  # le premier 0x00 est deja recu
    t_drain        = time.time()
    first_non_zero = None
    while time.time() - t_drain < LIN_BYTE_TMO:
        b = ser.read(1)
        if not b:
            time.sleep(0.0002)
            continue
        raw += b
        if b[0] != LIN_BREAK_BYTE:
            first_non_zero = b[0]
            break
        # Encore un 0x00 : reinitialiser le timer (break encore en cours)
        zero_count += 1
        t_drain = time.time()
    t_break_end = time.monotonic()      # timestamp apres le dernier 0x00

    # Accumuler et broadcaster la mesure toutes les _BAUD_MEAS_EVERY trames
    t_break_s = t_break_end - t_break_start
    if t_break_s > 0.0005 and zero_count >= 2:   # seuil minimal anti-bruit
        _baud_meas_zero_total  += zero_count
        _baud_meas_time_total  += t_break_s
        _baud_meas_frame_count += 1
        if _baud_meas_frame_count >= _BAUD_MEAS_EVERY:
            baud_meas = int(
                (_baud_meas_zero_total * 10 / _baud_meas_time_total) * 4
            )
            _tcp_broadcast({
                "type"         : "lin_baud_measured",
                "baud_measured": baud_meas,
                "zero_count"   : _baud_meas_zero_total,
                "break_ms"     : round(_baud_meas_time_total * 1000, 2),
                "n_frames"     : _baud_meas_frame_count,
                "time"         : time.time(),
            })
            # Reset accumulateurs pour le prochain groupe
            _baud_meas_zero_total  = 0
            _baud_meas_time_total  = 0.0
            _baud_meas_frame_count = 0

    # Si on n'a pas encore trouve d'octet non-nul, lire avec timeout normal
    # (ser.timeout est deja LIN_BYTE_TMO depuis l'ouverture du port)
    if first_non_zero is None:
        ser.timeout = LIN_BYTE_TMO
        b = ser.read(1)
        if not b:
            return None, bytes(raw)
        first_non_zero = b[0]
        raw += b

    # ── Etape 3 : verifier le SYNC (0x55) ───────────────────────────────
    if first_non_zero != LIN_SYNC_BYTE:
        # Octet inattendu apres le break : flush et abandon
        ser.reset_input_buffer()
        return None, bytes(raw)

    # ── Etape 4 : lire le PID ────────────────────────────────────────────
    ser.timeout = LIN_BYTE_TMO
    b = ser.read(1)
    if not b:
        return None, bytes(raw)

    raw += b
    pid_received = b[0]
    return pid_received, bytes(raw)


# ============================================================
# OUVERTURE DU PORT LIN (avec retry infini)
# ============================================================
def _open_lin_port(lin_port: str) -> serial.Serial:
    while True:
        try:
            ser = serial.Serial(
                lin_port,
                baudrate=_LIN_BAUD,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=LIN_BYTE_TMO,
            )
            log.info("[LIN] Port ouvert: %s @ %d baud", lin_port, _LIN_BAUD)
            _tcp_broadcast({"type": "info", "msg": "LIN ouvert sur " + lin_port})
            return ser
        except serial.SerialException as e:
            log.warning("[LIN] Port indisponible (%s) - retry dans %ds", e, LIN_RETRY_S)
            _tcp_broadcast({"type": "error", "msg": "LIN indisponible: " + str(e)})
            time.sleep(LIN_RETRY_S)


# ============================================================
# THREAD LIN
# MODIFICATION T10 : si _lin_tx_paused == True, on ne repond
# pas aux headers du BCM. Le BCM detecte l'absence de reponse
# et leve lin_timeout_active apres LIN_TIMEOUT (2000 ms).
# ============================================================
def _lin_thread(lin_port: str):
    """
    Thread LIN esclave (CRS) — boucle principale.

    Attend les headers du BCM (master), repond immediatement.
    Le dispatch (quel PID → quelle réponse) est entièrement piloté
    par _PID_MAP, lui-même chargé depuis le fichier LDF au démarrage.

    Strategie :
      1. Surveiller le bus : appeler _lin_read_header()
      2. Lookup PID dans _PID_MAP (issu du LDF) → frame_name
      3. Dispatcher vers _build_lin_response(frame_name) → bytes
      4. Envoyer la réponse puis broadcaster sur TCP
      5. PAUSE TEST T10 : si _is_lin_paused(), ignorer APRES broadcast RX_HDR

    IMPORTANT anti-chevauchement :
    - _lin_read_header() draine les 0x00 du BREAK ET l'echo de la reponse
      precedente. Ne jamais appeler _lin_read_header() pendant qu'un
      _lin_send_response() est en cours (meme thread → OK, sequentiel).
    - En cas d'erreur serie fatale : fermer + reouvrir le port.
    - En cas d'erreur transitoire : flush + continuer.
    """
    ser = _open_lin_port(lin_port)

    while True:
        try:
            # ── Attendre le prochain header BCM ─────────────────────────
            pid, raw = _lin_read_header(ser)

            if pid is None:
                continue

            raw_hex = " ".join(f"0x{b:02X}" for b in raw)

            # ── PAUSE TEST T10 ───────────────────────────────────────────
            if _is_lin_paused():
                _tcp_broadcast({
                    "type": "RX_HDR",
                    "pid" : f"0x{pid:02X}",
                    "raw" : raw_hex,
                    "time": time.time(),
                })
                try:
                    ser.reset_input_buffer()
                except OSError:
                    pass
                continue

            # ── Lire l'etat courant (atomique) ──────────────────────────
            op, status, alive, fault = _state.snapshot()

            # ── Dispatcher via le PID_MAP issu du LDF ────────────────────
            # REGLE CRITIQUE : la reponse LIN doit etre emise AVANT tout
            # appel TCP (_tcp_broadcast peut bloquer sur sendall si un client
            # est lent). Le master LIN a une fenetre de reponse limitee.
            # Toujours : reponse LIN → puis broadcast TCP.

            frame_name = _PID_MAP.get(pid)

            if frame_name == "LeftStickWiperRequester":
                # ── Trame 0x16 — WiperOp + StickStatus + AliveCtr ───────
                # Encodage défini dans le LDF :
                #   WiperOp    : bits 3:0 de byte0 (start_bit=0, length=4)
                #   StickStatus: bits 7:4 de byte0 (start_bit=4, length=4)
                #   AliveCtr   : byte1              (start_bit=8, length=8)
                # LIN_INVALID_CMD_001 : raw_wiper_op >= 0 → injection one-shot
                with _state._lock:
                    raw_op = _state.raw_wiper_op
                    if raw_op >= 0:
                        _state.raw_wiper_op = -1   # consommer immédiatement
                if raw_op >= 0:
                    b0 = (raw_op & 0x0F) | ((status & 0x0F) << 4)
                    log.info("[TEST] LIN_INVALID_CMD_001 : WiperOp brut=0x%02X injecté dans trame 0x16", raw_op)
                else:
                    b0 = (int(op) & 0x0F) | ((status & 0x0F) << 4)
                d  = bytes([b0, alive])
                cs = _lin_send_response(ser, pid, d)           # 1. REPONDRE
                _state.inc_alive()
                _tcp_broadcast({                                # 2. BROADCASTER
                    "type": "RX_HDR",
                    "pid" : f"0x{pid:02X}",
                    "raw" : raw_hex,
                    "time": time.time(),
                })
                _tcp_broadcast({
                    "type"   : "TX",
                    "pid"    : f"0x{pid:02X}",
                    "op"     : int(op),
                    "alive"  : alive,
                    "cs_int" : cs,
                    "raw"    : f"{b0:02X} {alive:02X} {cs:02X}",
                    "time"   : time.time(),
                })
                log.debug("[LIN] TX 0x%02X (LeftStickWiperRequester) "
                          "op=%s alive=0x%02X cs=0x%02X",
                          pid, op.name, alive, cs)

            elif frame_name == "CRS_Status":
                # ── Trame 0x17 — CRS_Status (WW-MCAT-005 Rev5) ─────────────
                # Encodage défini dans le LDF :
                #   byte0 bit0 : CRS_InternalFault_Stick  (FAULT_STICK_SENSOR)
                #   byte0 bit1 : CRS_InternalFault_Supply (FAULT_SUPPLY)
                #   byte0 bit2 : CRS_InternalFault_Comms  (FAULT_INTERNAL_COM)
                #   byte0 bit3-7 : CRS_Reserved           (0)
                #   byte1       : CRS_Version             (firmware version 0-255)
                crs_version = getattr(_state, 'crs_version', 0x01)
                d  = bytes([fault & 0x07, crs_version & 0xFF])
                cs = _lin_send_response(ser, pid, d)           # 1. REPONDRE
                _tcp_broadcast({                                # 2. BROADCASTER
                    "type": "RX_HDR",
                    "pid" : f"0x{pid:02X}",
                    "raw" : raw_hex,
                    "time": time.time(),
                })
                _tcp_broadcast({
                    "type"       : "tx17",
                    "fault_stick": bool(fault & 0x01),
                    "fault_supply": bool(fault & 0x02),
                    "fault_comms": bool(fault & 0x04),
                    "crs_version": crs_version,
                    "fault_raw"  : f"0x{fault:02X}",
                    "cs_int"     : cs,
                    "cs"         : f"0x{cs:02X}",
                    "time"       : time.time(),
                })
                log.debug("[LIN] TX 0x%02X (CRS_Status) "
                          "fault=0x%02X version=%d cs=0x%02X",
                          pid, fault, crs_version, cs)

            elif frame_name is not None:
                # ── Frame LDF future / inconnue de la logique applicative ─
                # On répond avec des zéros (DLC lu depuis le LDF)
                frames = _LDF_CONFIG["frames"] if _LDF_CONFIG else {}
                meta   = frames.get(frame_name, {})
                dlc    = meta.get("dlc", 2)
                d  = bytes(dlc)
                cs = _lin_send_response(ser, pid, d)
                _tcp_broadcast({
                    "type": "RX_HDR",
                    "pid" : f"0x{pid:02X}",
                    "raw" : raw_hex,
                    "time": time.time(),
                })
                log.debug("[LIN] TX 0x%02X (%s) [no logic] cs=0x%02X",
                          pid, frame_name, cs)

            else:
                # ── PID absent du LDF — inconnu ───────────────────────────
                log.debug("[LIN] PID inconnu 0x%02X (absent du LDF) -- ignore", pid)
                _tcp_broadcast({
                    "type": "RX_HDR",
                    "pid" : f"0x{pid:02X}",
                    "raw" : raw_hex,
                    "time": time.time(),
                })
                try:
                    ser.reset_input_buffer()
                except OSError:
                    pass

        except serial.SerialException as e:
            err_str = str(e).lower()
            # Erreur "readiness to read but returned no data" :
            # erreur FTDI/CP210x transitoire, flush et continuer
            if "readiness to read but returned no data" in err_str:
                try:
                    ser.reset_input_buffer()
                except OSError:
                    pass
                time.sleep(0.005)
                continue

            # Autre erreur serie fatale (port ferme, cable debranche...)
            log.error("[LIN] Erreur serie fatale: %s - reouverture dans 1s", e)
            _tcp_broadcast({"type": "error", "msg": "Erreur LIN: " + str(e)})
            time.sleep(1)
            try:
                ser.close()
            except OSError:
                pass
            ser = _open_lin_port(lin_port)

        except Exception as e:
            log.error("[LIN] Erreur inattendue: %s", e, exc_info=True)
            time.sleep(0.100)


# ============================================================
# TRAITEMENT DES COMMANDES RECUES SUR TCP
# ============================================================
_VALID_OPS = frozenset(op.name for op in WOp)


def _handle_cmd(cmd: str, addr):
    cmd = cmd.upper().strip()
    if not cmd:
        return

    if cmd == "STATUS":
        op, _, alive, fault = _state.snapshot()
        _tcp_broadcast({
            "type":  "status",
            "op":    op.name,
            "alive": alive,
            "fault": f"0x{fault:02X}",
            "pid16": f"0x{LIN_PID_16:02X}",
            "pid17": f"0x{LIN_PID_17:02X}",
        })
        return

    if cmd not in _VALID_OPS:
        log.warning("[CMD] Commande inconnue de %s: '%s'", addr, cmd)
        _tcp_broadcast({"type": "error", "msg": "Commande inconnue: " + cmd})
        return

    op = WOp[cmd]
    _state.set_op(op)
    log.info("[CMD] %s -> %s (op=%d)", addr, cmd, int(op))
    _tcp_broadcast({"type": "cmd_ack", "op": cmd, "val": int(op)})


def _process_line(line: str, addr):
    """
    Parse et dispatche une ligne JSON recue sur TCP.

    Formats acceptes :
      {"cmd": "TOUCH"}              -> commande wiper normale
      {"wiper_op": 1}               -> format numerique legacy
      {"test_cmd": "stop_lin_tx"}   -> NOUVEAU : pause reponses LIN (test T10)
      {"test_cmd": "start_lin_tx"}  -> NOUVEAU : reprise reponses LIN
      "TOUCH"                       -> texte brut debug
    """
    line = line.strip()
    if not line:
        return

    if not line.isascii() or not all(0x20 <= ord(c) < 0x7F for c in line):
        return

    log.info("[TCP RX LIN] de %s recu %s", addr, line)

    if not line.startswith("{"):
        _handle_cmd(line, addr)
        return

    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        return

    # ── NOUVEAU : commandes de test T10 ────────────────────────────────
    # Commandes nécessitant le verrou RTE Single Writer
    _LOCK_CMDS = {
        "stop_lin_tx", "freeze_alive_counter", "corrupt_lin_checksum",
        "set_raw_wiper_op", "send_invalid_stick_status", "send_bit4_zero", "restore_bit4",
    }
    _RESTORE_CMDS = {
        "start_lin_tx", "restore_alive_counter",
        "restore_lin_checksum", "restore_lin_normal",
    }
    if "test_cmd" in msg:
        tc = msg["test_cmd"]
        if tc in _LOCK_CMDS and _lock_client is not None:
            if not _lock_client.acquire():
                log.warning("[SIM-LOCK] '%s' REFUSÉ — verrou RTE indisponible", tc)
                _tcp_broadcast({"type": "error", "msg": f"lock_refused:{tc}"})
                return
        if tc in _RESTORE_CMDS and _lock_client is not None:
            _lock_client.release()
        if tc == "stop_lin_tx":
            _set_lin_paused(True)
            log.info("[TEST] LIN TX mis en pause (stop_lin_tx) — T10 actif")
            # Diffuser immediatement un evenement fault vers Platform
            # pour que on_lin_frame() de T10 puisse mesurer le delta.
            # Note : le BCM mettra ~2s a detecter. Ce broadcast est envoye
            # par precaution pour les scenarios ou Platform ecoute aussi :5555.
            _tcp_broadcast({
                "type": "fault",
                "msg":  "lin timeout detected — tx paused by test_cmd",
                "time": time.time(),
            })
        elif tc == "start_lin_tx":
            _set_lin_paused(False)
            log.info("[TEST] LIN TX repris (start_lin_tx)")
            _tcp_broadcast({"type": "info", "msg": "lin tx resumed"})

        # ── TC_LIN_004 : injection stickStatus invalide (0xFF) ─────────
        elif tc == "send_invalid_stick_status":
            with _state._lock:
                _state.stick_status = 0xFF   # valeur invalide hors plage
            log.info("[TEST] TC_LIN_004 : stick_status forcé à 0xFF (invalide)")
            _tcp_broadcast({"type": "info", "msg": "invalid stick_status 0xFF injected"})
            # Auto-restore après 3 s si restore_lin_normal n'est pas appelé avant
            def _auto_restore():
                time.sleep(3.0)
                with _state._lock:
                    if _state.stick_status == 0xFF:   # pas déjà restauré
                        _state.stick_status = STICK_VALID
                log.info("[TEST] TC_LIN_004 : stick_status auto-restauré (STICK_VALID)")
            threading.Thread(target=_auto_restore, daemon=True).start()

        # ── TC_LIN_016_BIT4 : bit4=0 (Valid=0) maintenu → B2004 après 2.5s ──
        elif tc == "send_bit4_zero":
            with _state._lock:
                # Forcer bit0 du nibble StickStatus à 0 (Valid=0)
                # stick_status = (stick_status & ~0x01) : efface bit0 uniquement
                # WiperOp reste intact → seul bit4 de byte0 passe à 0
                _state.stick_status = _state.stick_status & ~0x01
            log.info("[TEST] TC_LIN_016_BIT4 : stick_status bit0=0 (Valid=0) maintenu")
            _tcp_broadcast({"type": "info", "msg": "stick_status bit4=0 (invalid) injected"})

        # ── TC_LIN_016_BIT4 restore : remettre bit4=1 (Valid=1) ────────
        elif tc == "restore_bit4":
            with _state._lock:
                _state.stick_status = _state.stick_status | 0x01   # remet bit0=1
            log.info("[TEST] TC_LIN_016_BIT4 : stick_status bit0=1 (Valid=1) restauré")
            _tcp_broadcast({"type": "info", "msg": "stick_status bit4=1 (valid) restored"})

        # ── Restauration LIN normal (TC_LIN_004, TC_LIN_005 cleanup) ───
        elif tc == "restore_lin_normal":
            with _state._lock:
                _state.stick_status = STICK_VALID
                _state.fault        = 0x00
            _set_lin_paused(False)
            log.info("[TEST] LIN restauré (restore_lin_normal) : "
                     "stick_status=VALID, fault=0, tx resumed")
            _tcp_broadcast({"type": "info", "msg": "lin restored to normal"})

        # ── TC_LIN_002 : geler l'AliveCounter (anti-replay) ────────────
        elif tc == "freeze_alive_counter":
            with _state._lock:
                _state.freeze_alive = True
            log.info("[TEST] TC_LIN_002 : AliveCounter gelé")
            _tcp_broadcast({"type": "info", "msg": "alive_counter frozen"})

        # ── TC_LIN_002 cleanup : reprendre l'incrémentation normale ────
        elif tc == "restore_alive_counter":
            with _state._lock:
                _state.freeze_alive = False
            log.info("[TEST] TC_LIN_002 : AliveCounter repris (normal)")
            _tcp_broadcast({"type": "info", "msg": "alive_counter restored"})

        # ── TC_LIN_CS : corrompre le checksum de la trame 0x16 ─────────
        elif tc == "corrupt_lin_checksum":
            with _state._lock:
                _state.corrupt_checksum = True
            log.info("[TEST] TC_LIN_CS : checksum LIN 0x16 corrompu actif")
            _tcp_broadcast({"type": "info", "msg": "lin_checksum_corrupted"})

        # ── TC_LIN_CS cleanup : restaurer le checksum normal ───────────
        elif tc == "restore_lin_checksum":
            with _state._lock:
                _state.corrupt_checksum = False
            log.info("[TEST] TC_LIN_CS : checksum LIN 0x16 restauré (normal)")
            _tcp_broadcast({"type": "info", "msg": "lin_checksum_restored"})

        # ── LIN_INVALID_CMD_001 : injecter une valeur WiperOp hors plage ─
        elif tc == "set_raw_wiper_op":
            raw_op = int(msg.get("op", 10))
            with _state._lock:
                _state.raw_wiper_op = raw_op
            log.info("[TEST] LIN_INVALID_CMD_001 : raw_wiper_op=0x%02X armé (one-shot)", raw_op)
            _tcp_broadcast({"type": "info", "msg": f"raw_wiper_op={raw_op} armé"})

        else:
            log.warning("[TEST] test_cmd inconnu: %s", tc)
        return
    # ───────────────────────────────────────────────────────────────────

    # ── Injection CRS_Status (trame 0x17) — bits individuels + version ──
    if "set_fault" in msg:
        try:
            val = int(msg["set_fault"]) & 0x07   # bits 0-2 seulement (catalogue)
            with _state._lock:
                _state.fault = val
            fname = FAULT_NAMES.get(val, f"0x{val:02X}")
            log.info("[FAULT] CRS_Status fault force a 0x%02X (%s)", val, fname)
            _tcp_broadcast({
                "type":        "crs_fault_ack",
                "fault":       f"0x{val:02X}",
                "fault_name":  fname,
                "fault_stick": bool(val & 0x01),
                "fault_supply":bool(val & 0x02),
                "fault_comms": bool(val & 0x04),
            })
        except (ValueError, TypeError) as e:
            log.warning("[FAULT] Valeur invalide: %s", e)
            _tcp_broadcast({"type": "error", "msg": f"set_fault valeur invalide: {e}"})
        return

    # ── Injection CRS_Version (byte1 de trame 0x17) ──────────────────
    if "set_crs_version" in msg:
        try:
            ver = int(msg["set_crs_version"]) & 0xFF
            with _state._lock:
                _state.crs_version = ver
            log.info("[CRS] CRS_Version force a 0x%02X (%d)", ver, ver)
            _tcp_broadcast({"type": "crs_version_ack", "version": ver})
        except (ValueError, TypeError) as e:
            log.warning("[CRS] crs_version valeur invalide: %s", e)
            _tcp_broadcast({"type": "error", "msg": f"set_crs_version invalide: {e}"})
        return

    # ── Injection StickStatus Stuck (bit2 = bit6 de byte0 trame 0x16) ──
    # Simule le levier coincé (Stuck) pour tester la condition B2011.
    # "set_stuck": true  → bit6=1 (Stuck actif)
    # "set_stuck": false → bit6=0 (normal)
    if "set_stuck" in msg:
        stuck_val = bool(msg["set_stuck"])
        with _state._lock:
            if stuck_val:
                _state.stick_status = (_state.stick_status | STICK_STUCK)
            else:
                _state.stick_status = (_state.stick_status & ~STICK_STUCK) & 0x0F
        log.info("[STUCK] StickStatus Stuck (bit2) forcé à %s", stuck_val)
        _tcp_broadcast({"type": "stuck_ack", "stuck": stuck_val,
                         "stick_status": _state.stick_status})
        return
    # ──────────────────────────────────────────────────────────────────

    if "cmd" in msg:
        _handle_cmd(msg["cmd"], addr)
    elif "wiper_op" in msg:
        try:
            op = WOp(int(msg["wiper_op"]))
            _handle_cmd(op.name, addr)
        except ValueError:
            log.warning("[TCP] wiper_op inconnu: %s de %s", msg["wiper_op"], addr)


# ============================================================
# HANDLER D'UN CLIENT TCP
# ============================================================
def _client_handler(conn: socket.socket, addr):
    log.info("[TCP] Nouveau client: %s", addr)
    _tcp_broadcast({"type": "info", "msg": "Client " + str(addr) + " connecte"})
    _register_client(conn)

    conn.setblocking(False)
    buf = ""

    try:
        while True:
            ready, _, errored = select.select([conn], [], [conn], 1.0)

            if errored:
                log.warning("[TCP] Erreur select sur %s", addr)
                break

            if not ready:
                continue

            try:
                data = conn.recv(4096)
            except OSError as e:
                log.warning("[TCP] recv exception %s: %s", addr, e)
                break

            if not data:
                break

            buf += data.decode("utf-8", errors="replace")

            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                _process_line(line, addr)

    finally:
        _unregister_client(conn)
        try:
            conn.close()
        except OSError:
            pass
        log.info("[TCP] Client %s deconnecte", addr)
        _tcp_broadcast({"type": "info", "msg": "Client " + str(addr) + " deconnecte"})


# ============================================================
# SERVEUR TCP
# ============================================================
def _tcp_server(tcp_host: str, tcp_port: int):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except AttributeError:
        pass   # SO_REUSEPORT non disponible sur certains OS
    # Retry bind : apres un arret brutal le port peut rester en TIME_WAIT
    for attempt in range(10):
        try:
            srv.bind((tcp_host, tcp_port))
            break
        except OSError:
            log.warning("[TCP] Port %d occupe, attente 1s (tentative %d/10)...",
                        tcp_port, attempt + 1)
            time.sleep(1.0)
    else:
        log.error("[TCP] Port %d toujours occupe apres 10s -- abandon", tcp_port)
        return
    srv.listen(5)
    log.info("[TCP] Serveur en ecoute sur %s:%d", tcp_host, tcp_port)

    while True:
        try:
            conn, addr = srv.accept()
            threading.Thread(
                target=_client_handler,
                args=(conn, addr),
                daemon=True,
                name=f"crslin_client_{addr[1]}",
            ).start()
        except OSError as e:
            log.error("[TCP] Erreur accept: %s", e)


# ============================================================
# POINT D'ENTREE DU MODULE
# ============================================================
def start(tcp_host: str = _TCP_HOST, tcp_port: int = _TCP_PORT,
          lin_port: str = _LIN_PORT, ldf_path: str = "wiperwash.ldf"):
    """
    Démarre le nœud CRS LIN esclave.

    Paramètres
    ----------
    tcp_host  : adresse d'écoute TCP
    tcp_port  : port TCP (défaut 5555)
    lin_port  : port série LIN (défaut /dev/serial0)
    ldf_path  : chemin vers le fichier LDF (défaut wiperwash.ldf)
                Tous les PIDs, DLCs et noms de frames sont lus depuis ce fichier.
    """
    log.info("=== CRS LIN Node - demarrage ===")

    # ── Charger le LDF EN PREMIER ──────────────────────────────────────
    import os
    _candidates = [
        ldf_path,
        os.path.join(os.path.dirname(__file__), ldf_path),
        os.path.join(os.path.dirname(__file__), "..", ldf_path),
        os.path.join(os.path.dirname(__file__), "..", "wiperwash.ldf"),
    ]
    _ldf_found = next((p for p in _candidates if os.path.isfile(p)), None)
    if _ldf_found:
        load_ldf_into_crslin(_ldf_found)
        log.info("[LDF] Chargé depuis '%s'", _ldf_found)
    else:
        log.warning("[LDF] Fichier '%s' introuvable — PIDs par défaut", ldf_path)

    # Vérification cohérence PID après chargement LDF
    assert _calculate_pid(0x16) == LIN_PID_16, \
        f"PID 0x16 incorrect: attendu 0x{_calculate_pid(0x16):02X} recu 0x{LIN_PID_16:02X}"
    assert _calculate_pid(0x17) == LIN_PID_17, \
        f"PID 0x17 incorrect: attendu 0x{_calculate_pid(0x17):02X} recu 0x{LIN_PID_17:02X}"
    log.info("[LIN] PIDs OK  PID_16=0x%02X  PID_17=0x%02X  baud=%d  ops: %s",
             LIN_PID_16, LIN_PID_17, _LIN_BAUD,
             ", ".join(op.name for op in WOp))

    threading.Thread(
        target=_lin_thread,
        args=(lin_port,),
        daemon=True,
        name="crslin_lin",
    ).start()

    _tcp_server(tcp_host, tcp_port)


# ============================================================
# USAGE STANDALONE
# ============================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    p = argparse.ArgumentParser(description="CRS LIN Slave Node — piloté par LDF")
    p.add_argument("--host",     default=_TCP_HOST,      help="Adresse ecoute TCP")
    p.add_argument("--port",     type=int, default=_TCP_PORT,  help="Port TCP")
    p.add_argument("--lin-port", default=_LIN_PORT,      help="Port serie LIN")
    p.add_argument("--ldf",      default="wiperwash.ldf",
                   help="Chemin vers le fichier LDF (défaut: wiperwash.ldf)")
    args = p.parse_args()

    start(tcp_host=args.host, tcp_port=args.port,
          lin_port=args.lin_port, ldf_path=args.ldf)