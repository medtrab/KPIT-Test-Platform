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
    Server -> Client : {"type": "tx16",    "op": "TOUCH", "b0": "0x11", "alive": 5, "cs": "0xAB"}
    Server -> Client : {"type": "rx_hdr",  "pid": "0xD6", "raw": "0x55 0xD6"}
    Server -> Client : {"type": "cmd_ack", "op": "TOUCH", "val": 1}
    Server -> Client : {"type": "error",   "msg": "..."}
    Server -> Client : {"type": "info",    "msg": "..."}

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

# Timings LIN (secondes / microsecondes)
LIN_BYTE_TMO   = 0.006        # timeout inter-octets
LIN_HDR_TMO    = 0.050        # timeout reception header complet
LIN_TX_BIT_US  = 52           # duree 1 bit @ 19200 baud (us)
LIN_TX_BYTE_US = LIN_TX_BIT_US * 10 + 500   # ~570 us par octet (10 bits + marge)
LIN_ECHO_TMO   = 0.010        # timeout vidage echo loopback TJA1020
LIN_RETRY_S    = 3            # delai entre tentatives de reouverture du port

# Valeurs des champs LIN
STICK_VALID = 0x01
FAULT_NONE  = 0x00

# ============================================================
# LOGGING  (le logger est configure globalement dans main.py
#            ou localement si ce module tourne en standalone)
# ============================================================
log = logging.getLogger("CRS")


# ============================================================
# OPERATIONS ESSUIE-GLACE
# Utilise IntEnum pour permettre la comparaison directe avec
# des entiers et supprimer le dictionnaire inverse manuel.
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
# Dataclass avec lock interne pour garantir des acces atomiques
# depuis les threads LIN et TCP sans variables globales.
# ============================================================
@dataclass
class NodeState:
    wiper_op    : WOp = WOp.OFF
    stick_status: int = STICK_VALID
    alive_ctr   : int = 0
    fault       : int = FAULT_NONE
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self):
        """Retourne une copie atomique (wiper_op, stick_status, alive_ctr, fault)."""
        with self._lock:
            return self.wiper_op, self.stick_status, self.alive_ctr, self.fault

    def set_op(self, op: WOp):
        """Applique une nouvelle operation et remet stick_status a VALID."""
        with self._lock:
            self.wiper_op     = op
            self.stick_status = STICK_VALID

    def inc_alive(self):
        """Incremente le compteur alive (modulo 256)."""
        with self._lock:
            self.alive_ctr = (self.alive_ctr + 1) & 0xFF


# Instance unique du noeud
_state = NodeState()

# ============================================================
# LISTE DES CLIENTS TCP ET BROADCAST
# ============================================================
_clients      : list[socket.socket] = []
_clients_lock = threading.Lock()


def _tcp_broadcast(msg: dict, exclude: socket.socket = None):
    """
    Serialise msg en JSON et l'envoie a tous les clients connectes.
    Les sockets mortes sont silencieusement retirees de la liste.
    Le parametre exclude permet d'eviter l'echo vers l'expediteur.
    """
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
    """
    Calcule le PID LIN 8 bits depuis un identifiant 6 bits.
    Ajoute les bits de parite P0 (bit 6) et P1 (bit 7)
    conformement a la specification LIN 2.x.
    """
    id6 &= 0x3F
    p0 = (id6 ^ (id6 >> 1) ^ (id6 >> 2) ^ (id6 >> 4)) & 0x01
    p1 = (~((id6 >> 1) ^ (id6 >> 3) ^ (id6 >> 4) ^ (id6 >> 5))) & 0x01
    return id6 | (p0 << 6) | (p1 << 7)


def _lin_checksum(pid: int, data: bytes) -> int:
    """
    Checksum LIN classique (enhanced checksum avec PID).
    Somme PID + data avec propagation des retenues (carry-fold),
    puis inversion du resultat sur 8 bits.
    """
    s = pid + sum(data)
    while s > 0xFF:
        s = (s & 0xFF) + (s >> 8)
    return (~s) & 0xFF


def _lin_send_response(ser: serial.Serial, pid: int, data: bytes) -> int:
    """
    Envoie les octets de reponse esclave sur le bus LIN.

    Le RPi est en mode ESCLAVE : le maitre a deja emis BREAK+SYNC+PID.
    On ecrit uniquement data + checksum sur l'UART.

    Le transceiver TJA1020 (half-duplex) renvoie ses propres octets TX
    sur la broche RX (loopback). Cette fonction vide ce loopback apres
    la transmission pour que le prochain header ne soit pas corrompu.

    Retourne le checksum calcule.
    """
    cs       = _lin_checksum(pid, data)
    response = data + bytes([cs])

    ser.write(response)
    ser.flush()

    # Attendre la fin physique de la transmission avant de lire l'echo
    tx_time_s = len(response) * LIN_TX_BYTE_US / 1_000_000
    time.sleep(tx_time_s)

    # Vider l'echo loopback byte par byte jusqu'a timeout
    expected = len(response)
    deadline = time.time() + LIN_ECHO_TMO
    received = 0
    while received < expected and time.time() < deadline:
        chunk = ser.read(expected - received)
        received += len(chunk)

    return cs


def _lin_read_header(ser: serial.Serial):
    """
    Attend le prochain header LIN emis par le maitre : BREAK + SYNC + PID.

    Algorithme :
      1. Attend un octet 0x00 (debut du BREAK field)
      2. Attend la fin du BREAK (premier octet != 0x00 avec timeout)
      3. Verifie que cet octet est 0x55 (SYNC field)
      4. Lit le PID (1 octet)

    Retourne (pid, raw_bytes) ou (None, raw_bytes) en cas d'echec/timeout.
    """
    deadline = time.time() + LIN_HDR_TMO
    raw = bytearray()

    # Attente du premier octet BREAK (0x00)
    while time.time() < deadline:
        b = ser.read(1)
        if b and b[0] == LIN_BREAK_BYTE:
            raw += b
            break
    else:
        return None, bytes(raw)   # timeout global depasse

    # Attente de la fin du BREAK (octet non nul)
    t_break = time.time()
    first_non_zero = None
    while time.time() - t_break < LIN_BYTE_TMO:
        b = ser.read(1)
        if not b:
            continue
        if b[0] != LIN_BREAK_BYTE:
            first_non_zero = b[0]
            raw += b
            break
        raw += b
        t_break = time.time()   # prolonger si des 0x00 continuent d'arriver

    if first_non_zero is None:
        # Aucun octet non nul dans le temps imparti, on tente une derniere lecture
        ser.timeout = LIN_BYTE_TMO
        b = ser.read(1)
        if not b:
            return None, bytes(raw)
        first_non_zero = b[0]
        raw += b

    # Verification SYNC field
    if first_non_zero != LIN_SYNC_BYTE:
        return None, bytes(raw)

    # Lecture du PID
    ser.timeout = LIN_BYTE_TMO
    b = ser.read(1)
    if not b:
        return None, bytes(raw)

    raw += b
    return b[0], bytes(raw)


# ============================================================
# OUVERTURE DU PORT LIN (avec retry infini)
# ============================================================
def _open_lin_port(lin_port: str) -> serial.Serial:
    """
    Ouvre le port serie LIN et retourne l'objet Serial.
    En cas d'echec, attend LIN_RETRY_S secondes et reessaie
    indefiniment (utile au demarrage si le port n'est pas encore pret).
    """
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
# THREAD LIN  (boucle de traitement des headers recus)
# ============================================================
def _lin_thread(lin_port: str):
    """
    Thread principal LIN.
    - Lit les headers emis par le maitre (BREAK + SYNC + PID)
    - Pour PID 0xD6 : repond avec l'operation essuie-glace courante + alive
    - Pour PID 0x97 : repond avec le code de faute courant
    - Broadcast chaque trame TX/RX vers tous les clients TCP connectes
    - Se recupere automatiquement en cas d'erreur serie (reouverture du port)
    """
    ser = _open_lin_port(lin_port)

    while True:
        try:
            pid, raw = _lin_read_header(ser)
            if pid is None:
                continue   # timeout normal, on reessaie

            # Notifier tous les clients TCP de la reception du header
            raw_hex = " ".join(f"0x{b:02X}" for b in raw)
            _tcp_broadcast({"type": "rx_hdr", "pid": f"0x{pid:02X}", "raw": raw_hex})

            op, status, alive, fault = _state.snapshot()

            if pid == LIN_PID_16:
                # Trame 0x16 : LeftStickWiperRequester
                # B0 = nibble bas (op) | nibble haut (stick_status)
                b0 = (op & 0x0F) | ((status & 0x0F) << 4)
                d  = bytes([b0, alive])
                cs = _lin_send_response(ser, LIN_PID_16, d)
                _state.inc_alive()
                _tcp_broadcast({
                    "type":  "tx16",
                    "op":    op.name,
                    "b0":    f"0x{b0:02X}",
                    "alive": alive,
                    "cs":    f"0x{cs:02X}",
                })

            elif pid == LIN_PID_17:
                # Trame 0x17 : WiperFaultStatus
                d  = bytes([fault, 0x00])
                cs = _lin_send_response(ser, LIN_PID_17, d)
                _tcp_broadcast({"type": "tx17", "fault": f"0x{fault:02X}", "cs": f"0x{cs:02X}"})

        except serial.SerialException as e:
            # Perte du port serie : fermer, attendre, et reuvrir
            log.error("[LIN] Erreur serie: %s - reouverture dans 1s", e)
            _tcp_broadcast({"type": "error", "msg": "Erreur LIN: " + str(e)})
            time.sleep(1)
            try:
                ser.close()
            except OSError:
                pass
            ser = _open_lin_port(lin_port)

        except Exception as e:
            log.error("[LIN] Erreur inattendue: %s", e, exc_info=True)
            time.sleep(0.1)


# ============================================================
# TRAITEMENT DES COMMANDES RECUES SUR TCP
# ============================================================
_VALID_OPS = frozenset(op.name for op in WOp)   # lookup O(1)


def _handle_cmd(cmd: str, addr):
    """
    Traite une commande texte normalisee (ex: "TOUCH", "STATUS").
    Met a jour l'etat global et broadcast un ack a tous les clients.
    """
    cmd = cmd.upper().strip()
    if not cmd:
        return

    if cmd == "STATUS":
        # Reponse speciale : renvoie l'etat complet sans modifier quoi que ce soit
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

    # Appliquer la nouvelle operation et confirmer a tous les clients
    op = WOp[cmd]
    _state.set_op(op)
    log.info("[CMD] %s -> %s (op=%d)", addr, cmd, int(op))
    _tcp_broadcast({"type": "cmd_ack", "op": cmd, "val": int(op)})


def _process_line(line: str, addr):
    """
    Parse et dispatche une ligne JSON (ou texte brut) recue sur TCP.

    Formats acceptes :
      {"cmd": "TOUCH"}          -> interface PyQt6 (format principal)
      {"wiper_op": 1}           -> ancienne application (code numerique)
      "TOUCH"                   -> texte brut (debug manuel)
      {"type": "..."}           -> broadcast en retour, ignore silencieusement
    """
    line = line.strip()
    if not line:
        return

    # Ignorer les lignes contenant des caracteres non imprimables (< 0x20 ou > 0x7E)
    if not line.isascii() or not all(0x20 <= ord(c) < 0x7F for c in line):
        return

    # Texte brut (pas un objet JSON)
    if not line.startswith("{"):
        _handle_cmd(line, addr)
        return

    # Parsing JSON
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        return

    if "cmd" in msg:
        _handle_cmd(msg["cmd"], addr)
    elif "wiper_op" in msg:
        try:
            op = WOp(int(msg["wiper_op"]))
            _handle_cmd(op.name, addr)
        except ValueError:
            log.warning("[TCP] wiper_op inconnu: %s de %s", msg["wiper_op"], addr)
    # Les messages avec "type" sont des broadcasts en retour : on les ignore


# ============================================================
# HANDLER D'UN CLIENT TCP
# ============================================================
def _client_handler(conn: socket.socket, addr):
    """
    Thread dedie a un client TCP.
    Utilise select() avec timeout 1s pour rester reactif sans bloquer
    indefiniment sur recv(). Retire proprement le client a la deconnexion.
    """
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
                continue   # timeout 1s, le client est toujours vivant

            try:
                data = conn.recv(4096)
            except OSError as e:
                log.warning("[TCP] recv exception %s: %s", addr, e)
                break

            if not data:
                break   # connexion fermee proprement par le client

            buf += data.decode("utf-8", errors="replace")

            # Traiter toutes les lignes completes du buffer
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
# SERVEUR TCP  (accepte les connexions entrantes)
# ============================================================
def _tcp_server(tcp_host: str, tcp_port: int):
    """
    Boucle d'acceptation des connexions TCP.
    Chaque client recoit son propre thread daemon _client_handler.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((tcp_host, tcp_port))
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
    """
    Initialise et demarre le noeud LIN.

    1. Verifie les PIDs LIN (assertion au demarrage)
    2. Lance le thread LIN en daemon
    3. Lance le serveur TCP (BLOQUANT - doit etre appele dans un thread
       dedie si combine avec d'autres modules dans main.py)

    Parametres:
        tcp_host : adresse d'ecoute TCP (defaut "0.0.0.0")
        tcp_port : port TCP (defaut 5555)
        lin_port : chemin du port serie LIN (defaut "/dev/serial0")
    """
    log.info("=== CRS LIN Node - demarrage ===")

    # Verification des PIDs au demarrage (erreur fatale si incorrects)
    assert _calculate_pid(0x16) == LIN_PID_16, "PID 0x16 incorrect!"
    assert _calculate_pid(0x17) == LIN_PID_17, "PID 0x17 incorrect!"
    log.info("[LIN] PIDs OK - ops: %s", ", ".join(op.name for op in WOp))

    # Thread LIN (daemon : s'arrete avec le processus principal)
    threading.Thread(
        target=_lin_thread,
        args=(lin_port,),
        daemon=True,
        name="crslin_lin",
    ).start()

    # Serveur TCP (bloquant)
    _tcp_server(tcp_host, tcp_port)


# ============================================================
# USAGE STANDALONE  (python3 crslin.py)
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
