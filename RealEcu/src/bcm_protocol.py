#!/usr/bin/env python3

import glob
import socket
import struct
import threading
import time

import serial

try:
    import can
    CAN_AVAILABLE = True
except ImportError:
    CAN_AVAILABLE = False

from bcm_rte import (
    RTE,
    LIN_BAUD, LIN_SYNC, LIN_BREAK,
    LIN_PID_0x16, LIN_PID_0x17,
    LIN_CYCLE_0x16, LIN_CYCLE_0x17,
    LIN_TIMEOUT, LIN_INTERFRAME,
    LIN_PORT_CANDIDATES,
    CAN_ID_VEHICLE, CAN_ID_RAIN_SENSOR,
    CAN_RECV_TIMEOUT, CAN_IDLE_SLEEP,
    CAN_ID_WIPER_COMMAND, CAN_ID_WIPER_STATUS,
    CAN_WC_CMD_PERIOD,
    WOP_OFF, WOP_AUTO, WOP_REAR_WASH, WOP_REAR_WIPE, WOP_NAMES,
    ST_OFF,
)

# =====================================================
# CONSTANTES DoIP (ISO 13400)
# =====================================================
DOIP_PORT            = 13400
PROTOCOL_VERSION     = 0x02
DOIP_VEHICLE_ID_REQ  = 0x0001
DOIP_VEHICLE_ID_RES  = 0x0004
DOIP_ROUTING_ACT_REQ = 0x0005
DOIP_ROUTING_ACT_RES = 0x0006
DOIP_ALIVE_CHECK_REQ = 0x0007
DOIP_ALIVE_CHECK_RES = 0x0008
DOIP_DIAGNOSTIC_MSG  = 0x8001

_PTYPE_NAMES = {
    DOIP_VEHICLE_ID_REQ  : "VehicleIdRequest",
    0x0002               : "VehicleIdRequest(EID)",
    0x0003               : "VehicleIdRequest(VIN)",
    DOIP_VEHICLE_ID_RES  : "VehicleIdResponse",
    DOIP_ROUTING_ACT_REQ : "RoutingActivationRequest",
    DOIP_ROUTING_ACT_RES : "RoutingActivationResponse",
    DOIP_ALIVE_CHECK_REQ : "AliveCheckRequest",
    DOIP_ALIVE_CHECK_RES : "AliveCheckResponse",
    DOIP_DIAGNOSTIC_MSG  : "DiagnosticMessage",
}

# Adresses ECU (Diagnostic Specification Section 1)
BCM_ADDR    = 0x0700
TESTER_ADDR = 0x07DF

VIN = b"BCM_WIPEWASH12345"   # 17 octets

# =====================================================
# UDS SERVICE IDs -- exportes pour bcm_application.py
# =====================================================
SID_DSC   = 0x10   # DiagnosticSessionControl
SID_RESET = 0x11   # ECUReset
SID_CLEAR = 0x14   # ClearDiagnosticInformation
SID_RDTC  = 0x19   # ReadDTCInformation
SID_RDID  = 0x22   # ReadDataByIdentifier
SID_WDID  = 0x2E   # WriteDataByIdentifier
SID_SA    = 0x27   # SecurityAccess
SID_CC    = 0x28   # CommunicationControl
SID_RC    = 0x31   # RoutineControl
SID_TP    = 0x3E   # TesterPresent

DSC_DEFAULT  = 0x01
DSC_EXTENDED = 0x03

_SID_NAMES = {
    SID_DSC   : "DiagnosticSessionControl",
    SID_RESET : "ECUReset",
    SID_CLEAR : "ClearDTC",
    SID_RDTC  : "ReadDTCInformation",
    SID_RDID  : "ReadDataByIdentifier",
    SID_WDID  : "WriteDataByIdentifier",
    SID_SA    : "SecurityAccess",
    SID_CC    : "CommunicationControl",
    SID_RC    : "RoutineControl",
    SID_TP    : "TesterPresent",
}

_DSC_NAMES = {DSC_DEFAULT: "Default(0x01)", DSC_EXTENDED: "Extended(0x03)"}

_NRC_NAMES = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLength",
    0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x35: "invalidKey",
    0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
}

# Timeout attente reponse T-DIAG (secondes)
DOIP_RESPONSE_TIMEOUT = 1.000


# =====================================================
# CALCUL PID LIN (ISO 17987)
# =====================================================
def calculate_pid(frame_id: int) -> int:
    """
    Calcule le PID (Protected IDentifier) d'une trame LIN.
    Structure PID : bits 5:0 = frame_id, bit 6 = P0, bit 7 = P1.
    """
    if frame_id > 0x3F:
        raise ValueError(f"Frame ID doit etre 6 bits (0-63), recu: {frame_id:#04x}")
    p0 = (frame_id ^ (frame_id >> 1) ^ (frame_id >> 2) ^ (frame_id >> 4)) & 0x01
    p1 = ~((frame_id >> 1) ^ (frame_id >> 3) ^ (frame_id >> 4) ^ (frame_id >> 5)) & 0x01
    return (frame_id & 0x3F) | (p0 << 6) | (p1 << 7)


def lin_checksum(pid: int, data: bytes) -> int:
    """
    Calcule le checksum LIN Enhanced (ISO 17987).
    Somme PID + donnees avec carry-around, puis complement a 1.
    """
    total = pid
    for b in data:
        total += b
        if total > 0xFF:
            total -= 0xFF
    return (~total) & 0xFF


# =====================================================
# HELPERS TRACE DoIP / UDS
# =====================================================
def _fmt_hex(data: bytes, max_bytes: int = 16) -> str:
    if not data:
        return "(vide)"
    spaced = " ".join(f"{b:02X}" for b in data[:max_bytes])
    if len(data) > max_bytes:
        return f"{spaced} ... ({len(data)} octets)"
    return spaced


def _decode_uds_request(uds: bytes) -> str:
    if not uds:
        return "(vide)"
    sid  = uds[0]
    name = _SID_NAMES.get(sid, f"SID=0x{sid:02X}")

    if sid == SID_DSC and len(uds) >= 2:
        sub = uds[1] & 0x7F
        sup = " [suppressResp]" if uds[1] & 0x80 else ""
        return f"{name}  sub={_DSC_NAMES.get(sub, f'0x{sub:02X}')}{sup}"

    elif sid == SID_RESET and len(uds) >= 2:
        types = {0x01: "hardReset", 0x02: "keyOffOnReset", 0x03: "softReset"}
        return f"{name}  type={types.get(uds[1], f'0x{uds[1]:02X}')}"

    elif sid == SID_SA and len(uds) >= 2:
        subs = {0x01: "RequestSeed", 0x02: "SendKey"}
        sub_name = subs.get(uds[1], f"0x{uds[1]:02X}")
        extra = ""
        if uds[1] == 0x02 and len(uds) >= 4:
            key = (uds[2] << 8) | uds[3]
            extra = f"  key=0x{key:04X}"
        return f"{name}  sub={sub_name}{extra}"

    elif sid == SID_RDID and len(uds) >= 3:
        did = (uds[1] << 8) | uds[2]
        return f"{name}  DID=0x{did:04X}"

    elif sid == SID_WDID and len(uds) >= 4:
        did = (uds[1] << 8) | uds[2]
        val = uds[3]
        return f"{name}  DID=0x{did:04X}  val=0x{val:02X}({val})"

    elif sid == SID_RDTC and len(uds) >= 2:
        subs = {0x02: "reportDTCByStatusMask",
                0x04: "reportDTCSnapshotRecord",
                0x06: "reportDTCExtDataRecord"}
        return f"{name}  sub={subs.get(uds[1], f'0x{uds[1]:02X}')}"

    elif sid == SID_CLEAR and len(uds) >= 4:
        grp = (uds[1] << 16) | (uds[2] << 8) | uds[3]
        label = "allDTCs" if grp == 0xFFFFFF else f"group=0x{grp:06X}"
        return f"{name}  {label}"

    elif sid == SID_RC and len(uds) >= 4:
        sub_names = {0x01: "startRoutine",
                     0x02: "stopRoutine",
                     0x03: "requestResults"}
        rid      = (uds[2] << 8) | uds[3]
        duration = uds[4] if len(uds) >= 5 else "?"
        return (f"{name}  sub={sub_names.get(uds[1], f'0x{uds[1]:02X}')}"
                f"  RID=0x{rid:04X}  duration={duration}s")

    elif sid == SID_CC and len(uds) >= 3:
        subs = {0x00: "enableRxTx", 0x01: "enableRxDisableTx",
                0x02: "disableRxEnableTx", 0x03: "disableRxTx"}
        return (f"{name}  sub={subs.get(uds[1], f'0x{uds[1]:02X}')}"
                f"  commType=0x{uds[2]:02X}")

    elif sid == SID_TP and len(uds) >= 2:
        sup = " [suppressResp]" if uds[1] & 0x80 else ""
        return f"{name}{sup}"

    return f"{name}  data={_fmt_hex(uds[1:], 8)}"


def _decode_uds_response(resp: bytes) -> str:
    if not resp:
        return "(vide)"
    b0 = resp[0]

    if b0 == 0x7F and len(resp) >= 3:
        req_sid  = resp[1]
        nrc_code = resp[2]
        req_name = _SID_NAMES.get(req_sid, f"0x{req_sid:02X}")
        nrc_name = _NRC_NAMES.get(nrc_code, f"0x{nrc_code:02X}")
        return f"NRC  service={req_name}  nrc={nrc_name}"

    if b0 == 0x50 and len(resp) >= 2:
        sub = resp[1]
        return f"DSC+  session={_DSC_NAMES.get(sub, f'0x{sub:02X}')}"

    if b0 == 0x51 and len(resp) >= 2:
        types = {0x01: "hardReset", 0x02: "keyOffOnReset", 0x03: "softReset"}
        return f"ECUReset+  type={types.get(resp[1], f'0x{resp[1]:02X}')}"

    if b0 == 0x67 and len(resp) >= 2:
        sub = resp[1]
        if sub == 0x01 and len(resp) >= 4:
            seed = (resp[2] << 8) | resp[3]
            if seed == 0:
                return "SA+  RequestSeed  [deja deverrouille -> seed=0x0000]"
            return f"SA+  RequestSeed  seed=0x{seed:04X}"
        if sub == 0x02:
            return "SA+  SendKey  [ACCES ACCORDE - secLevel=1]"
        return f"SA+  sub=0x{sub:02X}"

    if b0 == 0x62 and len(resp) >= 3:
        did = (resp[1] << 8) | resp[2]
        val = resp[3:]
        return f"RDID+  DID=0x{did:04X}  data={_fmt_hex(val)}"

    if b0 == 0x6E and len(resp) >= 3:
        did = (resp[1] << 8) | resp[2]
        return f"WDID+  DID=0x{did:04X}  [ecrit OK]"

    if b0 == 0x59:
        return f"RDTC+  {_fmt_hex(resp[1:], 12)}"

    if b0 == 0x54:
        return "ClearDTC+  [DTCs effaces]"

    if b0 == 0x68 and len(resp) >= 2:
        subs = {0x00: "enableRxTx", 0x01: "enableRxDisableTx",
                0x02: "disableRxEnableTx", 0x03: "disableRxTx"}
        return f"CC+  sub={subs.get(resp[1], f'0x{resp[1]:02X}')}"

    if b0 == 0x71 and len(resp) >= 4:
        sub_names = {0x01: "started", 0x02: "stopped", 0x03: "results"}
        rid = (resp[2] << 8) | resp[3]
        dur = f"  duration={resp[4]}s" if len(resp) >= 5 else ""
        return (f"RC+  {sub_names.get(resp[1], f'0x{resp[1]:02X}')}"
                f"  RID=0x{rid:04X}{dur}")

    if b0 == 0x7E:
        return "TesterPresent+  [session maintenue]"

    return f"0x{b0:02X}+  {_fmt_hex(resp[1:], 8)}"


# =====================================================
# COUCHE PROTOCOLE -- LIN + CAN + DoIP
# =====================================================
class ProtocolLayer:
    """
    Gere LIN, CAN et DoIP.

    RESPONSABILITE UNIQUE :
      Recevoir les trames des bus physiques.
      Decoder le contenu.
      Ecrire les donnees dans le RTE.
      Ne prend AUCUNE decision logique.
      Ne lit jamais rte.state pour agir.

    PRINCIPE UNIFORME :
      LIN  → rte.crs_wiper_op        → T-WSM  lit et agit
      CAN  → rte.rain_intensity       → T-WSM  lit et agit
      DoIP → rte.uds_payload         → T-DIAG lit et agit
             attend rte.uds_response  → renvoie au PC

    Threads :
      thread_lin_scheduler() → T-LIN  cycle 20ms (conforme spec)
      thread_can_receiver()  → T-CAN  bloquant
      run()                  → T-DOIP boucle TCP bloquante
      _udp_handler()         → DoIP_UDP thread daemon
    """

    def __init__(self, rte: RTE, dtc_manager):
        self._rte      = rte
        self._dtc      = dtc_manager
        self._running  = False
        self._lin_stop = threading.Event()  # signal arret propre T-LIN
        self.lin_port  = None
        self.can_bus   = None
        self._srv_sock = None
        self._udp_sock = None   # socket UDP DoIP discovery — ferme dans stop()

        # Creer et binder le socket TCP ici dans __init__
        # Evite "Address already in use" au redemarrage :
        # le socket est cree une seule fois, reste ouvert pendant toute la vie du processus.
        # run() fait seulement listen()+accept()  /  stop() fait shutdown() sans close()
        self._srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._srv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass
        self._srv_sock.bind(("0.0.0.0", DOIP_PORT))
        print(f"[DoIP] Socket TCP bind port {DOIP_PORT} OK")

        # Debounce CAN 0x300 / 0x301
        self._CAN_DEBOUNCE   = 0.150

        self._ign_stable     = None
        self._ign_pending    = None
        self._ign_t          = 0.0

        self._rev_stable     = None
        self._rev_pending    = None
        self._rev_t          = 0.0

        self._sensor_stable  = None
        self._sensor_pending = None
        self._sensor_t       = 0.0

        assert calculate_pid(0x16) == LIN_PID_0x16, "PID 0x16 invalide"
        assert calculate_pid(0x17) == LIN_PID_0x17, "PID 0x17 invalide"

    # ==================================================
    # SECTION A -- INIT HARDWARE
    # ==================================================

    def init_can(self):
        """Initialise l'interface CAN physique (SocketCAN)."""
        if not CAN_AVAILABLE:
            print("[CAN] python-can non disponible")
            return
        try:
            self.can_bus = can.interface.Bus(channel="can0", bustype="socketcan")
            print("[CAN] Interface can0 OK")
        except Exception as e:
            print(f"[CAN] Init echec: {e}")

    def init_lin(self):
        """Initialise le port serie UART pour le bus LIN."""
        candidates = list(LIN_PORT_CANDIDATES)
        for p in sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")):
            if p not in candidates:
                candidates.append(p)
        for port in candidates:
            try:
                self.lin_port = serial.Serial(
                    port=port, baudrate=LIN_BAUD,
                    timeout=0.020, write_timeout=0.200,
                    dsrdtr=False, rtscts=False,
                )
                import time as _t; _t.sleep(0.1)
                self.lin_port.reset_input_buffer()
                self.lin_port.reset_output_buffer()
                print(f"[LIN] Port {port} OK (19200 baud)")
                return
            except Exception:
                continue
        print("[LIN] Aucun port serie -- mode simulation")

    # ==================================================
    # SECTION B -- THREAD LIN SCHEDULER (T-LIN)
    # ==================================================

    def thread_lin_scheduler(self):
        """
        T-LIN : ordonnanceur LIN master (BCM).
        Envoie les headers 0xD6 (400ms) et 0x97 (800ms), lit les reponses slave.
        Les deux cycles ne se chevauchent jamais grace a l'interframe.
        """
        t_0x16 = 0.0
        t_0x17 = 0.0
        print(f"[THREAD T-LIN] Demarre | cycle 0x16={LIN_CYCLE_0x16*1000:.0f}ms"
              f" | cycle 0x17={LIN_CYCLE_0x17*1000:.0f}ms")

        while self._running and not self._lin_stop.is_set():
            now = time.time()

            # ── Cycle 0x16 (400ms) ──────────────────────────────────────
            if now - t_0x16 >= LIN_CYCLE_0x16:
                t_0x16 = now
                self._lin_poll_0x16()
                # Interframe systematique apres chaque trame pour laisser
                # le bus se stabiliser avant la prochaine (slave peut etre
                # encore en train de traiter son loopback echo)
                time.sleep(LIN_INTERFRAME)

            # ── Cycle 0x17 (800ms) ──────────────────────────────────────
            now = time.time()
            if now - t_0x17 >= LIN_CYCLE_0x17:
                # Interframe supplementaire si 0x16 vient d'etre envoye
                # (moins de 100ms d'ecart)
                if (now - t_0x16) < 0.100:
                    time.sleep(LIN_INTERFRAME)
                t_0x17 = time.time()
                self._lin_poll_0x17()
                time.sleep(LIN_INTERFRAME)

            self._check_lin_timeout()
            time.sleep(0.005)   # 5ms resolution scheduler

    # ── Primitives LIN ────────────────────────────────

    def _lin_flush_all(self):
        """
        Vide le buffer RX UART completement.
        Appele avant chaque header pour garantir qu'il n'y a pas de
        residus de trames precedentes qui pourraient polluer la lecture.
        """
        if not self.lin_port:
            return
        self.lin_port.reset_input_buffer()
        time.sleep(0.003)
        # Double flush : parfois le driver USB-Serial a encore des octets
        # dans son FIFO interne apres le premier reset
        self.lin_port.reset_input_buffer()

    def _lin_send_break(self):
        """
        Envoyer le BREAK LIN via baudrate/4 (dominant >= 13 bits).
        Strategie identique au code Arduino v7 :
          - Passer a baud/4
          - Envoyer un 0x00
          - Attendre la duree exacte du break + marge USB
          - Repasser a LIN_BAUD
        """
        if not self.lin_port:
            return
        self.lin_port.baudrate = LIN_BAUD // 4
        self.lin_port.write(bytes([LIN_BREAK]))
        self.lin_port.flush()
        # 13 bits @ LIN_BAUD/4 + 3ms marge USB-Serial adapter
        time.sleep(13.0 / (LIN_BAUD // 4) + 0.003)
        self.lin_port.baudrate = LIN_BAUD
        # Stabilisation baudrate (USB-CDC peut avoir un delai interne)
        time.sleep(0.002)

    def _lin_send_header(self, pid: int):
        """
        Envoyer le HEADER LIN complet : BREAK + SYNC(0x55) + PID.
        Le flush initial garantit qu'aucun residus de trame precedente
        ne pollue la fenetre de lecture de la reponse slave.
        """
        if not self.lin_port:
            return
        self._lin_flush_all()
        self._lin_send_break()
        self.lin_port.write(bytes([LIN_SYNC, pid]))
        self.lin_port.flush()
        # Pause minimale : le slave (Arduino/RPi) doit avoir le temps de
        # decoder le PID avant de commencer a repondre.
        # A 19200 baud : 1 octet = ~520us. SYNC+PID = ~1.04ms.
        # On attend 3ms supplementaires = marge USB-Serial.
        time.sleep(0.003)

    def _lin_read_byte(self, timeout_s: float) -> int:
        """
        Lire un octet depuis le bus LIN avec timeout.
        Retourne l'octet (0-255) ou -1 si timeout.
        Polling court (0.3ms) pour ne pas manquer un octet rapide.
        """
        if timeout_s <= 0:
            return -1
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.lin_port and self.lin_port.in_waiting:
                return self.lin_port.read(1)[0]
            time.sleep(0.0003)
        return -1

    def _lin_read_response(self, pid: int) -> bytes:
        """
        Lire la reponse du slave apres envoi du header LIN.

        Architecture hardware : le BCM est MASTER LIN (half-duplex).
        Le transceiver TJA1020 cote BCM fait un loopback UART :
        les octets envoyes (BREAK + SYNC + PID) reapparaissent en RX.
        Ensuite arrivent les 3 octets du slave (data[0], data[1], cs).

        Strategie alignee sur le code Arduino CRS v7 (linReadHeader) :
          1. Drainer les 0x00 du BREAK (un ou plusieurs selon baud/4)
          2. Chercher 0x55 (SYNC) dans le flux (max 12 octets)
          3. Verifier l'echo du PID
          4. Lire les 3 octets de reponse slave
          5. Flush final + pause pour laisser les derniers octets arriver

        Timeout global 130ms :
          BREAK ~2.7ms + SYNC+PID ~1ms + slave reponse ~1.6ms + USB ~5ms
          + marge 120ms pour absorber les pics de latence USB-Serial.

        Retourne b"" si la reponse est invalide ou timeout.
        """
        BYTE_TMO  = 0.015    # 15ms par octet (USB-Serial P99 latency)
        FRAME_TMO = 0.130    # 130ms total frame window

        deadline = time.time() + FRAME_TMO

        # ── Etape 1 : drainer l'echo du BREAK (0x00 consecutifs) ────────
        # Le BREAK envoye a baud/4 est decode comme plusieurs 0x00 a 19200.
        # On consomme tous les 0x00 jusqu'au premier octet non-nul.
        b = -1
        while time.time() < deadline:
            b = self._lin_read_byte(min(BYTE_TMO, deadline - time.time()))
            if b < 0:
                # Aucun octet recu dans le timeout global : pas de loopback
                self._lin_flush_all()
                return b""
            if b != 0x00:
                break   # Premier octet non-nul : peut etre 0x55 ou autre

        if b < 0:
            self._lin_flush_all()
            return b""

        # ── Etape 2 : chercher l'echo du SYNC (0x55) ────────────────────
        # L'octet non-nul peut deja etre 0x55 (cas frequent).
        # Sinon on cherche sur les 12 prochains octets (robustesse).
        sync_found = (b == LIN_SYNC)
        if not sync_found:
            for _ in range(12):
                if time.time() >= deadline:
                    break
                b = self._lin_read_byte(min(BYTE_TMO, deadline - time.time()))
                if b < 0:
                    break
                if b == LIN_SYNC:
                    sync_found = True
                    break
                # Octet inattendu : continuer a chercher (peut etre residus)

        if not sync_found:
            self._lin_flush_all()
            return b""

        # ── Etape 3 : verifier l'echo du PID ────────────────────────────
        b = self._lin_read_byte(min(BYTE_TMO, deadline - time.time()))
        if b < 0:
            self._lin_flush_all()
            return b""
        if b != pid:
            # PID different : residus d'une trame precedente -> flush total
            self._lin_flush_all()
            return b""

        # ── Etape 4 : lire les 3 octets de reponse du slave ─────────────
        # data[0] = byte0 (wiper_op | stick_status), ou fault
        # data[1] = byte1 (alive counter), ou reserved
        # data[2] = checksum LIN enhanced
        resp = bytearray()
        for i in range(3):
            remaining = deadline - time.time()
            if remaining <= 0:
                self._lin_flush_all()
                return b""
            b = self._lin_read_byte(min(BYTE_TMO, remaining))
            if b < 0:
                # Timeout sur un octet de reponse slave
                self._lin_flush_all()
                return b""
            resp.append(b)

        # ── Etape 5 : flush final ────────────────────────────────────────
        # Pause 3ms pour laisser arriver les eventuels octets residuels
        # (echo TX du slave si son transceiver a aussi un loopback),
        # puis flush pour nettoyer avant la prochaine trame.
        time.sleep(0.003)
        if self.lin_port and self.lin_port.in_waiting:
            self.lin_port.reset_input_buffer()

        return bytes(resp)

    def _lin_poll_0x16(self):
        """
        Cycle LIN 0x16 (PID=0xD6) -- LeftStickWiperRequester.
        Le BCM envoie le header et lit la reponse du slave CRS.
        Ecrit rte.crs_wiper_op. Ne filtre pas -- T-WSM decide.

        CORRECTION : t_last_lin0x16 mis a jour UNIQUEMENT sur reponse
        valide (checksum OK). Les trames corrompues ne remettent pas
        le timer a zero → timeout detecte correctement.
        """
        if not self.lin_port:
            return
        rte = self._rte
        try:
            self._lin_send_header(LIN_PID_0x16)
            resp = self._lin_read_response(LIN_PID_0x16)

            if len(resp) < 3:
                # Pas de reponse slave (silence) : ne pas mettre a jour t_last
                # Le timeout sera detecte par _check_lin_timeout()
                return

            data    = resp[:2]
            rx_cs   = resp[2]
            calc_cs = lin_checksum(LIN_PID_0x16, data)
            if rx_cs != calc_cs:
                # Checksum invalide : trame corrompue, ignorer
                print(f"[LIN 0x16] Checksum KO rx=0x{rx_cs:02X} "
                      f"calc=0x{calc_cs:02X} -- trame ignoree")
                return

            new_op       = data[0] & 0x0F
            stick_status = (data[0] >> 4) & 0x0F
            new_alive    = data[1]
            stick_valid  = bool(stick_status & 0x01)
            old_op       = rte.crs_wiper_op

            # Alive counter gele → DTC B2004 (slave bloque)
            if rte.crs_alive_prev != 0xFF and new_alive == rte.crs_alive_prev:
                if not rte.lin_timeout_active:
                    print(f"[TSR_001] Alive counter gele: 0x{new_alive:02X} → B2004")
                    self._dtc.set_active("B2004", rte.make_snapshot())
                    rte.set("lin_timeout_active", True)
                return

            # Mise a jour RTE (sous lock unique pour atomicite)
            with rte._lock:
                rte.crs_alive_prev  = new_alive
                # crs_alive_in peut ne pas exister dans toutes les versions RTE
                if hasattr(rte, "crs_alive_in"):
                    rte.crs_alive_in = new_alive
                rte.crs_stick_valid = stick_valid
                rte.t_last_lin0x16  = time.time()

                # Appliquer la commande wiper selon ignition et stick validity
                if rte.ignition_status == 0:
                    if new_op != WOP_OFF and rte.crs_wiper_op != WOP_OFF:
                        print(f"[LIN 0x16] Ignition OFF → "
                              f"{WOP_NAMES.get(new_op,'?')} ignoree")
                    rte.crs_wiper_op    = WOP_OFF
                    rte._freeze_pending = False
                elif stick_valid:
                    rte.crs_wiper_op = new_op
                    if new_op == WOP_OFF:
                        rte._freeze_pending = False
                else:
                    rte.crs_wiper_op    = WOP_OFF
                    rte._freeze_pending = False

            # Retablissement communication → desactiver B2004
            if rte.lin_timeout_active:
                print("[LIN 0x16] Communication retablie → B2004 inactif")
                self._dtc.set_inactive("B2004")
                rte.set("lin_timeout_active", False)

            if old_op != rte.crs_wiper_op:
                print(f"[LIN 0x16] WiperOp: {WOP_NAMES.get(old_op,'?')} → "
                      f"{WOP_NAMES.get(rte.crs_wiper_op,'?')} "
                      f"alive=0x{new_alive:02X} WSM={rte.state}")

        except serial.SerialTimeoutException:
            # Write timeout USB-Serial : transitoire, pas de DTC
            print("[LIN 0x16] Write timeout UART (overhead USB) -- pas de DTC")
        except serial.SerialException as e:
            print(f"[LIN 0x16] Erreur port serie: {e}")
            self._handle_lin_timeout()
        except Exception as e:
            print(f"[LIN 0x16] Exception inattendue: {e}")
            self._handle_lin_timeout()

    def _lin_poll_0x17(self):
        """
        Cycle LIN 0x17 (PID=0x97) -- CRS_Status.
        Le BCM envoie le header et lit le statut interne du slave CRS.
        Ecrit rte.crs_fault. Non critique pour WSM (pas de DTC ici).

        CORRECTION : reponse toujours attendue et traitee, meme si la
        trame 0x17 n'a pas de role dans la logique WSM. Sans cette lecture,
        les octets de la reponse slave resteraient dans le buffer UART et
        pollueraient la prochaine lecture 0x16.
        """
        if not self.lin_port:
            return
        rte = self._rte
        try:
            self._lin_send_header(LIN_PID_0x17)
            resp = self._lin_read_response(LIN_PID_0x17)
            if len(resp) < 3:
                # Pas de reponse slave pour 0x17 : non critique, on continue
                return
            data  = resp[:2]
            rx_cs = resp[2]
            if lin_checksum(LIN_PID_0x17, data) != rx_cs:
                print(f"[LIN 0x17] Checksum KO -- trame ignoree")
                return
            old_fault = rte.crs_fault
            rte.set_multi(crs_fault=data[0], t_last_lin0x17=time.time())
            if old_fault != data[0]:
                print(f"[LIN 0x17] CRS_InternalFault: "
                      f"0x{old_fault:02X} → 0x{data[0]:02X}")
        except serial.SerialTimeoutException:
            pass   # Non critique
        except Exception as e:
            # Exception sur 0x17 : logguer sans declencher timeout
            # (le timeout est gere par _check_lin_timeout via t_last_lin0x16)
            print(f"[LIN 0x17] Exception (non critique): {e}")

    def _handle_lin_timeout(self):
        """
        Declencher le timeout LIN → B2004 actif + forcer WOP_OFF.
        Idempotent : n'agit que si lin_timeout_active == False.
        """
        rte = self._rte
        if not rte.lin_timeout_active:
            print("[LIN] TIMEOUT detecte → B2004 actif (FSR_001)")
            rte.set_multi(lin_timeout_active=True, crs_wiper_op=WOP_OFF)
            self._dtc.set_active("B2004", rte.make_snapshot())

    def _check_lin_timeout(self):
        """
        Verifier periodiquement si le CRS repond encore.
        Appele par thread_lin_scheduler toutes les 5ms.
        Declenche B2004 si t_last_lin0x16 depasse LIN_TIMEOUT (2s).
        La mise a jour de t_last_lin0x16 n'est faite QUE sur reponse
        valide dans _lin_poll_0x16, donc un silence slave declenche bien
        le timeout apres LIN_TIMEOUT secondes.
        """
        rte = self._rte
        if rte.t_last_lin0x16 == 0.0:
            return   # Pas encore de premiere trame recue
        if (time.time() - rte.t_last_lin0x16) > LIN_TIMEOUT and not rte.lin_timeout_active:
            self._handle_lin_timeout()

    # ==================================================
    # SECTION C -- THREAD CAN RECEIVER (T-CAN)
    # ==================================================

    def thread_can_receiver(self):
        """
        Thread T-CAN -- Recepteur CAN. Bloquant sur bus.
        ID=0x300 → Vehicle_Status  : ignition, marche arriere, vitesse
        ID=0x301 → RainSensorData  : intensite pluie, etat capteur
        ID=0x201 → Wiper_Status    : statut retour WC (Cas B)
        """
        print("[THREAD T-CAN] Demarre")
        rte = self._rte
        _last_log = 0.0

        while self._running:
            if not self.can_bus:
                now = time.time()
                if now - _last_log >= 5.0:
                    print("[THREAD T-CAN] can_bus non disponible, attente...")
                    _last_log = now
                time.sleep(CAN_IDLE_SLEEP)
                continue
            try:
                msg = self.can_bus.recv(timeout=CAN_RECV_TIMEOUT)
                if msg is None:
                    continue
                data = bytes(msg.data)
                if msg.arbitration_id == CAN_ID_VEHICLE:
                    self._can_process_0x300(data)
                elif msg.arbitration_id == CAN_ID_RAIN_SENSOR:
                    self._can_process_0x301(data)
                elif msg.arbitration_id == CAN_ID_WIPER_STATUS:
                    self._can_process_0x201(data)
            except Exception as e:
                print(f"[THREAD T-CAN] Exception: {e}")

    def _can_process_0x300(self, data: bytes):
        """
        Trame CAN 0x300 -- Vehicle_Status.
        byte 0 : Ignition_Status | byte 1 : ReverseGear | byte 2-3 : VehicleSpeed
        VehicleSpeed est encode en 0.1 km/h par bit (simulateur : speed_raw = speed_kmh * 10)
        → diviser par 10 pour obtenir la vitesse en km/h dans le RTE.
        """
        if len(data) < 4:
            return
        rte     = self._rte
        new_ign = data[0]
        new_rev = bool(data[1])
        now     = time.time()
        speed_raw = (data[2] << 8) | data[3]
        speed_kmh = round(speed_raw / 10.0, 1)   # FIX : 0.1 km/h par bit

        rte.set_multi(
            ignition_status = new_ign,
            reverse_gear    = new_rev,
            vehicle_speed   = speed_kmh,          # FIX : km/h réels (ex: 130 → 13.0)
        )

        if new_ign != self._ign_pending:
            self._ign_pending = new_ign
            self._ign_t       = now
        elif (new_ign != self._ign_stable and
              now - self._ign_t >= self._CAN_DEBOUNCE):
            print(f"[CAN 0x300] Ignition: {self._ign_stable} → {new_ign}")
            self._ign_stable = new_ign

        if self._ign_stable is None:
            self._ign_stable = new_ign

        if new_rev != self._rev_pending:
            self._rev_pending = new_rev
            self._rev_t       = now
        elif (new_rev != self._rev_stable and
              now - self._rev_t >= self._CAN_DEBOUNCE):
            print(f"[CAN 0x300] Marche arriere: "
                  f"{'ENGAGEE' if new_rev else 'DESENGAGEE'}")
            self._rev_stable = new_rev

        if self._rev_stable is None:
            self._rev_stable = new_rev

    def _can_process_0x301(self, data: bytes):
        """
        Trame CAN 0x301 -- RainSensorData.
        byte 0 : RainIntensity (0-100) | byte 1 : SensorStatus
        DTC B2007 si sensor_ok = False stable 150ms.
        """
        if len(data) < 2:
            return
        rte       = self._rte
        sensor_ok = (data[1] == 0)
        now       = time.time()

        rte.set_multi(rain_intensity=data[0], rain_sensor_ok=sensor_ok)

        if sensor_ok != self._sensor_pending:
            self._sensor_pending = sensor_ok
            self._sensor_t       = now
        elif (sensor_ok != self._sensor_stable and
              now - self._sensor_t >= self._CAN_DEBOUNCE):
            self._sensor_stable = sensor_ok
            if not sensor_ok:
                print("[CAN 0x301] Panne capteur pluie → B2007")
                self._dtc.set_active("B2007", rte.make_snapshot())
            else:
                print("[CAN 0x301] Capteur pluie retabli → B2007 inactif")
                self._dtc.set_inactive("B2007")

        if self._sensor_stable is None:
            self._sensor_stable = sensor_ok

    # --------------------------------------------------
    # CAN 0x201 -- Wiper_Status (WC → BCM, Cas B)
    # --------------------------------------------------

    def _can_process_0x201(self, data: bytes):
        """
        Trame CAN 0x201 -- Wiper_Status (WC → BCM, Cas B).
        MESSAGE CATALOGUE :
          Byte 0 : CurrentMode   Byte 1 : CurrentSpeed
          Byte 2 : BladePosition Byte 3-4 : MotorCurrent (0.1A/bit)
          Byte 5 : FaultStatus   Byte 6 : AliveCounter  Byte 7 : CRC
        CRC = XOR(byte0..byte6) -- coherent avec _crc_tx() du simulateur (bcmcan.py)
        Met a jour le RTE BCM et reset le timer de supervision B2005.
        """
        if len(data) < 8:                             # FIX : 8 octets requis (7 payload + 1 CRC)
            return
        rte = self._rte
        if not rte.wc_available:
            return   # Cas A : ignorer

        # FIX : verification CRC XOR sur les 7 premiers octets
        crc_calc = 0
        for b in data[:7]:
            crc_calc ^= b
        crc_calc &= 0xFF
        if crc_calc != (data[7] & 0xFF):
            print(f"[CAN 0x201] CRC KO recu=0x{data[7]:02X} calc=0x{crc_calc:02X} -- trame ignoree")
            return

        curr_speed = data[1] & 0xFF
        motor_curr = ((data[3] << 8) | data[4]) * 0.1   # 0.1A/bit
        fault_st   = data[5] & 0xFF
        is_moving  = (curr_speed > 0)
        was_moving = rte.front_blade_moving
        if was_moving and not is_moving:
             rte.set("t_motor_stop", time.time())
             print(f"[CAN 0x201] WC confirme arrêt moteur (speed={curr_speed}) -> t_motor_stop mis à jour")
        
        rte.set_multi(
            front_motor_speed   = curr_speed,
            front_blade_moving  = is_moving,
            motor_current_a     = round(motor_curr, 3),
            t_last_wiper_status = time.time(),
            wc_alive_rx         = data[6] & 0xFF,
        )
        if fault_st != 0:
            print(f"[CAN 0x201] WC FaultStatus=0x{fault_st:02X}")

    def _build_wiper_command(self, wiper_mode: int, speed: int, wash: int) -> bytes:
        """
        Construit trame CAN 0x200 Wiper_Command (MESSAGE CATALOGUE) :
          Byte 0 bits 0-3 : WiperMode   Byte 0 bits 4-7 : WiperSpeedLevel
          Byte 1 bits 0-1 : WashRequest Byte 2 : AliveCounter  Byte 3 : CRC
        CRC = XOR(b0, b1, b2) -- coherent avec _crc_rx() du simulateur (bcmcan.py)
        """
        rte = self._rte
        rte.wc_can_alive_tx = (rte.wc_can_alive_tx + 1) % 256
        b0  = (wiper_mode & 0x0F) | ((speed & 0x0F) << 4)
        b1  = wash & 0x03
        b2  = rte.wc_can_alive_tx & 0xFF
        crc = (b0 ^ b1 ^ b2) & 0xFF          # FIX : XOR (anciennement addition)
        return bytes([b0, b1, b2, crc, 0x00, 0x00, 0x00, 0x00])

    def _can_send_wiper_command(self, data: bytes):
        """Envoie trame CAN 0x200 vers WC."""
        if not self.can_bus:
            return
        try:
            msg = can.Message(
                arbitration_id=CAN_ID_WIPER_COMMAND,
                data=data, is_extended_id=False
            )
            self.can_bus.send(msg)
        except Exception as e:
            print(f"[CAN TX 0x200] Erreur: {e}")

    def thread_can_wc_command(self):
        """
        Thread T-CAN-WC -- BCM → WC Wiper_Command 0x200 (Cas B, 20ms).
        Actif seulement quand rte.wc_available = True.
        Traduit l'etat WSM BCM en commande CAN vers WC.
        """
        from bcm_rte import (
            WOP_OFF, WOP_TOUCH, WOP_SPEED1, WOP_SPEED2, WOP_AUTO, WOP_FRONT_WASH,
            ST_OFF, ST_TOUCH, ST_SPEED1, ST_SPEED2, ST_AUTO,
            ST_WASH_FRONT, ST_WASH_REAR, ST_REAR_WIPE, ST_ERROR, ST_DIAG,
        )
        print(f"[THREAD T-CAN-WC] Demarre | periode={CAN_WC_CMD_PERIOD*1000:.0f}ms")
        rte = self._rte

        while self._running:
            if not rte.wc_available:
                time.sleep(CAN_WC_CMD_PERIOD)
                continue

            state = rte.state

            if state in (ST_OFF, ST_ERROR):
                wiper_mode, speed = WOP_OFF, 0
            elif state == ST_TOUCH:
                wiper_mode, speed = WOP_TOUCH, 1
            elif state == ST_SPEED1:
                wiper_mode, speed = WOP_SPEED1, 1
            elif state == ST_SPEED2:
                wiper_mode, speed = WOP_SPEED2, 2
            elif state == ST_AUTO:
                wiper_mode, speed = WOP_AUTO, rte.front_motor_speed
            elif state == ST_WASH_FRONT:
                wiper_mode, speed = WOP_FRONT_WASH, 1
            elif state == ST_WASH_REAR:
                wiper_mode, speed = WOP_OFF, 0
            elif state == ST_DIAG:
                # En mode DIAG, le BCM continue d'envoyer 0x200 vers WC
                # avec la commande du test actif (front_motor_on/speed du RTE)
                # pour que le WC puisse executer le test moteur avant.
                # Si aucun test actif (ou test pompe/pluie) -> WOP_OFF
                if rte._test_active and rte.front_motor_on:
                    wiper_mode = WOP_SPEED1 if rte.front_motor_speed == 1 else WOP_SPEED2
                    speed      = rte.front_motor_speed
                else:
                    wiper_mode, speed = WOP_OFF, 0
            else:
                wiper_mode, speed = WOP_OFF, 0

            wash = 0
            if state == ST_WASH_FRONT and rte.pump_active and rte.pump_direction == 1:
                wash = 1
            elif state == ST_WASH_REAR and rte.pump_active and rte.pump_direction == 2:
                wash = 2

            frame = self._build_wiper_command(wiper_mode, speed, wash)
            self._can_send_wiper_command(frame)

            # Afficher seulement si changement
            sig = (wiper_mode, speed, wash)
            if sig != getattr(self, '_last_wc_cmd', None):
                self._last_wc_cmd = sig
                from bcm_rte import WOP_NAMES as _WOP_NAMES
                print(f"[CAN TX 0x200] Wiper_Command: "
                      f"mode={_WOP_NAMES.get(wiper_mode,'?')} "
                      f"speed={speed} wash={wash}")

            time.sleep(CAN_WC_CMD_PERIOD)

    def run(self):
        # Socket deja cree et binde dans __init__
        # Ici on fait juste listen() pour accepter les connexions
        self._srv_sock.listen(5)
        print(f"[DoIP] Serveur TCP en ecoute sur port {DOIP_PORT}")

        while self._running:
            try:
                client_sock, client_addr = self._srv_sock.accept()
                print(f"[DoIP TCP] *** Connexion  "
                      f"{client_addr[0]}:{client_addr[1]} ***")
                threading.Thread(
                    target=self._doip_handle_client,
                    args=(client_sock, client_addr),
                    daemon=True
                ).start()
            except OSError:
                break
            except Exception as e:
                if self._running:
                    print(f"[DoIP] Erreur accept TCP: {e}")

    def _start_udp_discovery(self):
        """Lancer le thread UDP DoIP discovery (non bloquant)."""
        threading.Thread(
            target=self._doip_udp_handler,
            daemon=True,
            name="DoIP_UDP"
        ).start()

    def _doip_udp_handler(self):
        """
        Thread UDP -- Vehicle Identification Discovery.
        Repond aux requetes VehicleIdRequest du PC avec VIN + adresse BCM.
        """
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        udp.bind(("0.0.0.0", DOIP_PORT))
        udp.settimeout(1.0)
        self._udp_sock = udp   # reference pour fermeture dans stop()
        print(f"[DoIP] UDP discovery sur port {DOIP_PORT}")

        while self._running:
            try:
                data, addr = udp.recvfrom(1024)
                if len(data) < 8:
                    continue
                _, _, ptype, plen = struct.unpack(">BBHL", data[:8])
                pname = _PTYPE_NAMES.get(ptype, f"0x{ptype:04X}")
                if ptype in (DOIP_VEHICLE_ID_REQ, 0x0002, 0x0003):
                    print(f"[DoIP UDP] RX  {addr[0]}:{addr[1]}  type={pname}")
                    payload  = VIN[:17].ljust(17, b"\x00")
                    payload += struct.pack(">H", BCM_ADDR)
                    payload += bytes([0x00] * 12)
                    frame = self._doip_header(
                        DOIP_VEHICLE_ID_RES, len(payload)) + payload
                    udp.sendto(frame, addr)
                    print(f"[DoIP UDP] TX  {addr[0]}:{addr[1]}  "
                          f"type=VehicleIdResponse  "
                          f"VIN={VIN.decode()}  bcm=0x{BCM_ADDR:04X}")
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    print(f"[DoIP] Erreur UDP: {e}")
        udp.close()

    def _doip_handle_client(self, sock, addr):
        ip_port = f"{addr[0]}:{addr[1]}"
        rte = self._rte

        # Reinitialiser session UDS pour cette connexion
        rte.set_multi(
            _session      = DSC_DEFAULT,
            _sec_level    = 0,
            _pending_seed = {},
        )
        print(f"[DoIP TCP] Session reinit  client={ip_port}  "
              f"session=Default  secLevel=0")

        try:
            while self._running:
                data = sock.recv(4096)
                if not data:
                    print(f"[DoIP TCP] Deconnexion  client={ip_port}")
                    break

                parsed = self._doip_parse(data)
                if not parsed:
                    print(f"[DoIP TCP] Trame invalide (<8 octets)  "
                          f"client={ip_port}  raw={data.hex().upper()}")
                    continue

                ptype, payload = parsed
                pname = _PTYPE_NAMES.get(ptype, f"0x{ptype:04X}")

                # ── Routing Activation ────────────────────────────
                if ptype == DOIP_ROUTING_ACT_REQ:
                    src = (struct.unpack(">H", payload[:2])[0]
                           if len(payload) >= 2 else TESTER_ADDR)
                    act_type = payload[2] if len(payload) >= 3 else 0
                    print(f"[DoIP TCP] RX  type={pname}  "
                          f"tester=0x{src:04X}  "
                          f"activationType=0x{act_type:02X}  "
                          f"client={ip_port}")
                    resp_payload = struct.pack(">HHB4x", src, BCM_ADDR, 0x10)
                    self._doip_send(sock, DOIP_ROUTING_ACT_RES, resp_payload)
                    print(f"[DoIP TCP] TX  type=RoutingActivationResponse  "
                          f"code=0x10(OK)  "
                          f"bcm=0x{BCM_ADDR:04X}  tester=0x{src:04X}")

                # ── Diagnostic Message (UDS) ──────────────────────
                elif ptype == DOIP_DIAGNOSTIC_MSG:
                    resp = self._doip_dispatch_uds(payload, ip_port)
                    if resp:
                        self._doip_send(sock, DOIP_DIAGNOSTIC_MSG, resp)

                # ── Alive Check ───────────────────────────────────
                elif ptype == DOIP_ALIVE_CHECK_REQ:
                    print(f"[DoIP TCP] RX  type={pname}  client={ip_port}")
                    self._doip_send(sock, DOIP_ALIVE_CHECK_RES,
                                    struct.pack(">H", BCM_ADDR))
                    print(f"[DoIP TCP] TX  type=AliveCheckResponse  "
                          f"bcm=0x{BCM_ADDR:04X}")

                else:
                    print(f"[DoIP TCP] RX  type={pname}  [non gere]  "
                          f"client={ip_port}  "
                          f"payload={_fmt_hex(payload, 8)}")

        except Exception as e:
            if self._running:
                print(f"[DoIP TCP] Erreur  client={ip_port}: {e}")
        finally:
            sock.close()
            print(f"[DoIP TCP] Socket ferme  client={ip_port}")

    def _doip_dispatch_uds(self, payload: bytes, ip_port: str = "?") -> bytes:
        """
        Format payload DoIP diagnostic :
          [0-1] SA  : adresse source (tester)
          [2-3] TA  : adresse cible  (BCM = 0x0700)
          [4..] UDS : service + sous-fonction + donnees
        """
        if len(payload) < 5:
            print(f"[DoIP UDS] Payload trop court ({len(payload)} octets)  "
                  f"client={ip_port}")
            return b""

        src, tgt = struct.unpack(">HH", payload[:4])
        uds      = payload[4:]
        sid      = uds[0]
        rte      = self._rte

        is_tp_suppress = (sid == SID_TP and len(uds) >= 2 and bool(uds[1] & 0x80))

        if not is_tp_suppress:
            req_str = _decode_uds_request(uds)
            print(f"[DoIP UDS] REQ  "
                  f"0x{src:04X}->0x{tgt:04X}  "
                  f"{req_str}  "
                  f"raw=[{_fmt_hex(uds, 8)}]")

        # ── SERIALISATION -- un seul client a la fois ──────────────
        # Acquerir le mutex avant d'ecrire dans le RTE.
        # Le release se fait apres lecture de la reponse.
        with rte._uds_mutex:

            # ── ECRITURE RTE ──────────────────────────────────────
            rte.set_multi(
                uds_sid             = sid,
                uds_payload         = uds,
                uds_src_addr        = src,
                uds_dst_addr        = tgt,
                uds_response        = b"",
                uds_response_ready  = False,
                uds_request_pending = True,   # signal a T-DIAG
            )
            # Reveiller T-DIAG immediatement via Event (zero polling CPU)
            rte._uds_event.set()

            # -- ATTENDRE REPONSE DE T-DIAG ---
            deadline = time.time() + DOIP_RESPONSE_TIMEOUT
            while time.time() < deadline:
                if rte.uds_response_ready:
                    break
                time.sleep(0.002)   # 2ms -- T-DIAG repond en < 5ms avec Event -- granularite fine

            if not rte.uds_response_ready:
                print(f"[DoIP UDS] TIMEOUT reponse T-DIAG SID=0x{sid:02X}")
                rte.set("uds_request_pending", False)
                resp = bytes([0x7F, sid, 0x78])
            else:
                resp = rte.uds_response if rte.uds_response else b""
                rte.set_multi(uds_response_ready=False, uds_request_pending=False)

        # ── LOG REPONSE ───────────────────────────────────────────
        if not is_tp_suppress:
            if resp:
                rsp_str = _decode_uds_response(resp)
                tag     = "NRC " if resp[0] == 0x7F else "RSP "
                print(f"[DoIP UDS] {tag} "
                      f"0x{BCM_ADDR:04X}->0x{src:04X}  "
                      f"{rsp_str}  "
                      f"raw=[{_fmt_hex(resp, 8)}]")
            else:
                print("[DoIP UDS] RSP  (suppressResp actif)")

        if resp is None:
            resp = b""
        return struct.pack(">HH", BCM_ADDR, src) + resp

    # ── Primitives DoIP bas niveau ────────────────────

    def _doip_header(self, ptype: int, plen: int) -> bytes:
        """Construire le header DoIP (8 bytes)."""
        inv = (~PROTOCOL_VERSION) & 0xFF
        return struct.pack(">BBHL", PROTOCOL_VERSION, inv, ptype, plen)

    def _doip_parse(self, data: bytes):
        """Parser un header DoIP. Retourne (ptype, payload) ou None."""
        if len(data) < 8:
            return None
        _, _, ptype, plen = struct.unpack(">BBHL", data[:8])
        return ptype, data[8:8 + plen]

    def _doip_send(self, sock, ptype: int, payload: bytes):
        """Envoyer une trame DoIP complete sur le socket TCP."""
        frame = self._doip_header(ptype, len(payload)) + payload
        sock.send(frame)

    # ==================================================
    # SECTION E -- DEMARRAGE / ARRET
    # ==================================================

    def start(self):
        """
        Activer le flag running + lancer le thread UDP DoIP discovery.
        Appele avant le lancement des threads depuis bcm_main.py.
        """
        self._running = True
        self._start_udp_discovery()

    def stop(self):
        """Arreter tous les protocoles proprement."""
        self._running = False

        # Fermer le socket UDP immediatement — libere le port 13400
        # Sans ca : thread UDP daemon reste en vie et tient le port
        # → OSError: [Errno 98] Address already in use au redemarrage
        if self._udp_sock:
            try:
                self._udp_sock.close()
            except Exception:
                pass
            self._udp_sock = None

        # Signaler T-LIN de s'arreter et attendre qu'il finisse
        # son iteration courante avant de fermer le port serie.
        # Sans cette attente : race condition → "port not open"
        self._lin_stop.set()
        time.sleep(LIN_CYCLE_0x16 + 0.100)  # attendre 1 cycle LIN max

        if self._srv_sock:
            try:
                # shutdown() debloque accept() dans run() immediatement
                # Ne pas faire close() ici — le socket reste utilisable
                # pour un eventuel redemarrage dans le meme processus
                self._srv_sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
        if self.can_bus:
            try:
                self.can_bus.shutdown()
            except Exception:
                pass
        if self.lin_port:
            try:
                self.lin_port.close()
            except Exception:
                pass
        print("[ProtocolLayer] Arrete proprement")