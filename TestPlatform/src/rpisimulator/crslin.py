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
# CONFIGURATION PAR DEFAUT  (surchargeable via start())
# ============================================================
_LIN_PORT     = "/dev/serial0"
_LIN_BAUD     = 19200
_TCP_HOST     = "0.0.0.0"
_TCP_PORT     = 5555

# Octets speciaux du protocole LIN
LIN_BREAK_BYTE = 0x00
LIN_SYNC_BYTE  = 0x55
LIN_PID_16     = 0xD6   # PID calcule pour ID 0x16
LIN_PID_17     = 0x97   # PID calcule pour ID 0x17

# Timings LIN (secondes)
# A 19200 baud : 1 bit = 52us, 1 octet (10 bits) = 520us
LIN_BYTE_TMO   = 0.010        # timeout inter-octets (10ms, marge USB-Serial)
LIN_HDR_TMO    = 0.500        # timeout attente header complet (500ms)
                               # Long pour absorber les cycles BCM 400ms/800ms
LIN_TX_BIT_US  = 52           # duree 1 bit @ 19200 baud (us)
LIN_TX_BYTE_US = LIN_TX_BIT_US * 10 + 500   # ~570 us par octet (10 bits + marge)
LIN_ECHO_TMO   = 0.015        # timeout vidage echo loopback TJA1020 (15ms)
LIN_RETRY_S    = 3            # delai entre tentatives de reouverture du port

# Valeurs des champs LIN
STICK_VALID = 0x01
FAULT_NONE         = 0x00   # pas de défaut
FAULT_STICK_SENSOR = 0x01   # capteur position levier HS
FAULT_SUPPLY       = 0x02   # alimentation interne hors plage
FAULT_INTERNAL_COM = 0x04   # défaut communication interne CRS

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
            self.alive_ctr = (self.alive_ctr + 1) & 0xFF


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
def _calculate_pid(id6: int) -> int:
    id6 &= 0x3F
    p0 = (id6 ^ (id6 >> 1) ^ (id6 >> 2) ^ (id6 >> 4)) & 0x01
    p1 = (~((id6 >> 1) ^ (id6 >> 3) ^ (id6 >> 4) ^ (id6 >> 5))) & 0x01
    return id6 | (p0 << 6) | (p1 << 7)


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
    cs       = _lin_checksum(pid, data)
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
    t_drain = time.time()
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
        t_drain = time.time()

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
    Strategie identique au loop() Arduino CRS v7 :
      1. Surveiller le bus : appeler _lin_read_header()
      2. Si pid == LIN_PID_16 (0xD6) → repondre avec sendFrame16()
      3. Si pid == LIN_PID_17 (0x97) → repondre avec sendFrame17()
      4. PID inconnu : ignorer (flush buffer)
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
                # Pas de header valide dans LIN_HDR_TMO : normal si le BCM
                # est en pause (test T10) ou si le bus est inactif.
                # Pas de flush ici : _lin_read_header gere deja son buffer.
                continue

            # ── PAUSE TEST T10 ───────────────────────────────────────────
            # stop_lin_tx recu : on ne repond PAS.
            # On broadcast RX_HDR ici (avant continue) car il n'y a pas
            # de risque de delai TCP avant reponse (pas de reponse LIN).
            if _is_lin_paused():
                raw_hex = " ".join(f"0x{b:02X}" for b in raw)
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

            # ── Repondre IMMEDIATEMENT selon le PID recu ─────────────────
            # REGLE CRITIQUE : la reponse LIN doit etre emise AVANT tout
            # appel TCP (_tcp_broadcast peut bloquer sur sendall si un client
            # est lent). Le master LIN a une fenetre de reponse limitee.
            # Toujours : reponse LIN → puis broadcast TCP.
            if pid == LIN_PID_16:
                # Header 0xD6 → repondre LeftStickWiperRequester
                b0 = (int(op) & 0x0F) | ((status & 0x0F) << 4)
                d  = bytes([b0, alive])
                cs = _lin_send_response(ser, LIN_PID_16, d)  # 1. REPONDRE
                _state.inc_alive()   # Incrementer apres envoi (comme Arduino)
                raw_hex = " ".join(f"0x{b:02X}" for b in raw)
                _tcp_broadcast({     # 2. PUIS broadcaster (TCP peut attendre)
                    "type": "RX_HDR",
                    "pid" : f"0x{pid:02X}",
                    "raw" : raw_hex,
                    "time": time.time(),
                })
                _tcp_broadcast({
                    "type"   : "TX",
                    "pid"    : f"0x{LIN_PID_16:02X}",
                    "op"     : int(op),
                    "alive"  : alive,
                    "cs_int" : cs,
                    "raw"    : f"{b0:02X} {alive:02X} {cs:02X}",
                    "time"   : time.time(),
                })
                log.debug("[LIN] TX 0xD6 op=%s alive=0x%02X cs=0x%02X",
                          op.name, alive, cs)

            elif pid == LIN_PID_17:
                # Header 0x97 → repondre CRS_Status
                d  = bytes([fault, 0x00])
                cs = _lin_send_response(ser, LIN_PID_17, d)  # 1. REPONDRE
                raw_hex = " ".join(f"0x{b:02X}" for b in raw)
                _tcp_broadcast({     # 2. PUIS broadcaster
                    "type": "RX_HDR",
                    "pid" : f"0x{pid:02X}",
                    "raw" : raw_hex,
                    "time": time.time(),
                })
                _tcp_broadcast({
                    "type"  : "tx17",
                    "fault" : f"0x{fault:02X}",
                    "cs_int": cs,
                    "cs"    : f"0x{cs:02X}",
                    "time"  : time.time(),
                })
                log.debug("[LIN] TX 0x97 fault=0x%02X cs=0x%02X", fault, cs)

            else:
                # PID inconnu : pas de reponse LIN, flush propre
                log.debug("[LIN] PID inconnu 0x%02X -- ignore", pid)
                raw_hex = " ".join(f"0x{b:02X}" for b in raw)
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
    if "test_cmd" in msg:
        tc = msg["test_cmd"]
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
        else:
            log.warning("[TEST] test_cmd inconnu: %s", tc)
        return
    # ───────────────────────────────────────────────────────────────────

    # ── Injection CRS_InternalFault (trame 0x17) ──────────────────────
    if "set_fault" in msg:
        try:
            val = int(msg["set_fault"]) & 0xFF
            with _state._lock:
                _state.fault = val
            fname = FAULT_NAMES.get(val, f"0x{val:02X}")
            log.info("[FAULT] CRS_InternalFault force a 0x%02X (%s)", val, fname)
            _tcp_broadcast({
                "type":       "crs_fault_ack",
                "fault":      f"0x{val:02X}",
                "fault_name": fname,
            })
        except (ValueError, TypeError) as e:
            log.warning("[FAULT] Valeur invalide: %s", e)
            _tcp_broadcast({"type": "error", "msg": f"set_fault valeur invalide: {e}"})
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
          lin_port: str = _LIN_PORT):
    log.info("=== CRS LIN Node - demarrage ===")

    assert _calculate_pid(0x16) == LIN_PID_16, "PID 0x16 incorrect!"
    assert _calculate_pid(0x17) == LIN_PID_17, "PID 0x17 incorrect!"
    log.info("[LIN] PIDs OK - ops: %s", ", ".join(op.name for op in WOp))

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

    p = argparse.ArgumentParser(description="CRS LIN Slave Node")
    p.add_argument("--host",     default=_TCP_HOST,  help="Adresse ecoute TCP")
    p.add_argument("--port",     type=int, default=_TCP_PORT,  help="Port TCP")
    p.add_argument("--lin-port", default=_LIN_PORT,  help="Port serie LIN")
    args = p.parse_args()

    start(tcp_host=args.host, tcp_port=args.port, lin_port=args.lin_port)