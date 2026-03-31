#!/usr/bin/env python3
"""
wc_doip.py
==========
Serveur DoIP/UDS du WC ECU -- integre dans rpisimulator6 (RPi #2)

Responsabilites :
  - Serveur TCP DoIP port 13401 (adresse WC 0x0701)
  - Serveur UDP DoIP discovery port 13401
  - Traitement UDS : DSC(0x10), ReadDTC(0x19), ReadDID(0x22),
    RoutineControl(0x31), TesterPresent(0x3E)
  - Detection DTC et acces a la machine d'etat WC via _WCState partagee

PRINCIPE D'INTEGRATION :
  Ce module ne modifie AUCUN mecanisme de bcmcan.py ni crslin.py.
  Il s'appuie sur _WCState (singleton) qui est mis a jour par bcmcan.py
  via update_from_bcmcan() a chaque reception/emission CAN.

USAGE :
  Appele depuis main.py si --mode inclut 'wc' ou 'both' (ou 'all').
  Lance en thread daemon independant.
"""

import os
import socket
import struct
import threading
import time

from wc_dtc_manager import DTCManager_WC

# =====================================================
# CONSTANTES DoIP
# =====================================================
PROTOCOL_VERSION     = 0x02
DOIP_VEHICLE_ID_REQ  = 0x0001
DOIP_VEHICLE_ID_RES  = 0x0004
DOIP_ROUTING_ACT_REQ = 0x0005
DOIP_ROUTING_ACT_RES = 0x0006
DOIP_ALIVE_CHECK_REQ = 0x0007
DOIP_ALIVE_CHECK_RES = 0x0008
DOIP_DIAGNOSTIC_MSG  = 0x8001
DOIP_DIAG_MSG_ACK    = 0x8002

WC_LOGICAL_ADDR      = 0x0701
DOIP_PORT            = 13401
TESTER_ADDR          = 0x07DF
TESTER_ADDR_PHYSICAL = 0x07DE

# =====================================================
# UDS SIDs
# =====================================================
SID_DSC  = 0x10
SID_RDTC = 0x19
SID_RDID = 0x22
SID_RC   = 0x31
SID_TP   = 0x3E

DSC_DEFAULT  = 0x01
DSC_EXTENDED = 0x03

# =====================================================
# ETAT WC PARTAGE (mis a jour par bcmcan.py)
# =====================================================
class _WCState:
    """
    Memoire partagee legere entre bcmcan.py et wc_doip.py.
    bcmcan.py appelle update_from_bcmcan() a chaque RX/TX CAN.
    wc_doip.py lit ces valeurs pour construire les reponses UDS DIDs.
    """
    def __init__(self):
        self._lock = threading.RLock()

        # Etat moteur / lame (mis a jour par bcmcan)
        self.current_mode   = 0     # WiperMode actuel (encode)
        self.current_speed  = 0     # Vitesse actuelle
        self.blade_moving   = False
        self.motor_current  = 0.0   # Amperes
        self.fault_status   = 0     # bitfield
        self.can_timeout    = False  # flag timeout CAN

        # Session UDS interne
        self._session       = DSC_DEFAULT
        self._sec_level     = 0
        self._pending_seed  = {}

        # Test actuateur
        self._test_active   = False
        self._test_routine  = 0
        self._test_duration = 0
        self._t_test_start  = 0.0

        # UDS event
        self._uds_event     = threading.Event()
        self.uds_sid        = 0
        self.uds_payload    = b""
        self.uds_request_pending = False
        self.uds_response   = b""
        self.uds_response_ready  = False

        # Watchdog interne DoIP
        self._watchdog_kick_time = time.time()
        self._watchdog_max_ms    = 50

    def update_from_bcmcan(self,
                           mode: int, speed: int,
                           blade_moving: bool,
                           motor_current_a: float,
                           fault_status: int,
                           can_timeout: bool):
        with self._lock:
            self.current_mode  = mode
            self.current_speed = speed
            self.blade_moving  = blade_moving
            self.motor_current = motor_current_a
            self.fault_status  = fault_status
            self.can_timeout   = can_timeout

    def make_snapshot(self) -> dict:
        with self._lock:
            wiper_names = {0:"OFF",1:"TOUCH",2:"SPEED1",3:"SPEED2",
                           4:"AUTO",5:"WASH_FRONT",7:"ERROR",9:"DIAG"}
            return {
                "ignition":    1,
                "wiper_mode":  wiper_names.get(self.current_mode, "UNKNOWN"),
                "motor_curr":  int(self.motor_current * 1000),
                "blade_pos":   1 if self.blade_moving else 0,
                "rain":        0,
                "vehicle_spd": 0,
            }

    def watchdog_kick(self):
        with self._lock:
            self._watchdog_kick_time = time.time()

    def watchdog_check(self, dtc_mgr: DTCManager_WC):
        with self._lock:
            elapsed_ms = (time.time() - self._watchdog_kick_time) * 1000
        if elapsed_ms > self._watchdog_max_ms * 10:
            print(f"[WC-WATCHDOG] Timeout {elapsed_ms:.0f}ms -> B2101")
            dtc_mgr.set_active("B2101", self.make_snapshot())
            with self._lock:
                self._watchdog_kick_time = time.time()


# Singleton global accessible depuis bcmcan.py
wc_state = _WCState()


# =====================================================
# SUPERVISEUR DTC (tourne en thread daemon)
# =====================================================
class _DTCSupervisor:
    """
    Thread de surveillance DTC independant.
    Surveille : overcurrent (B2102), position sensor (B2103), watchdog (B2101).
    Lit _WCState.can_timeout et motor_current pour detecter les fautes.
    """

    OVERCURRENT_THRESH = 0.8   # A
    OVERCURRENT_DELAY  = 0.300 # s
    CAN_WC_TIMEOUT     = 2.0   # s (coherent avec wc_rte.py)
    GUARD_PERIOD       = 0.200 # s

    def __init__(self, state: _WCState, dtc_mgr: DTCManager_WC):
        self._state   = state
        self._dtc     = dtc_mgr
        self._running = False
        self._t_overcurrent_start = {}

    def start(self):
        self._running = True
        t = threading.Thread(target=self._run, daemon=True, name="WC-DTC-Guard")
        t.start()
        print("[WC-DTC-Guard] Thread surveillance DTC demarre")

    def stop(self):
        self._running = False

    def _run(self):
        while self._running:
            self._state.watchdog_kick()
            self._check_overcurrent()
            self._state.watchdog_check(self._dtc)
            time.sleep(self.GUARD_PERIOD)

    def _check_overcurrent(self):
        current = self._state.motor_current
        now     = time.time()

        if not self._state.blade_moving:
            self._t_overcurrent_start.clear()
            return

        if current > self.OVERCURRENT_THRESH:
            if "front" not in self._t_overcurrent_start:
                self._t_overcurrent_start["front"] = now
                print(f"[WC-SECURITE] Surintensite {current:.2f}A detectee...")
            elif now - self._t_overcurrent_start["front"] > self.OVERCURRENT_DELAY:
                del self._t_overcurrent_start["front"]
                print(f"[WC-B2102] Surintensite moteur {current:.2f}A > "
                      f"{self.OVERCURRENT_THRESH}A pendant "
                      f"{self.OVERCURRENT_DELAY*1000:.0f}ms -> B2102")
                self._dtc.set_active("B2102", self._state.make_snapshot())
        else:
            self._t_overcurrent_start.pop("front", None)


# =====================================================
# SERVEUR DoIP / UDS
# =====================================================
class WCDoIPServer:
    """
    Serveur DoIP TCP+UDP pour l'adresse WC 0x0701 sur port 13401.
    Traite les requetes UDS et interagit avec _WCState et DTCManager_WC.
    """

    def __init__(self, state: _WCState, dtc_mgr: DTCManager_WC):
        self._state    = state
        self._dtc      = dtc_mgr
        self._running  = False
        self._tcp_sock = None
        self._udp_sock = None

    # ── Helpers DoIP ──────────────────────────────────

    def _hdr(self, ptype: int, plen: int) -> bytes:
        inv = (~PROTOCOL_VERSION) & 0xFF
        return struct.pack(">BBHL", PROTOCOL_VERSION, inv, ptype, plen)

    def _parse_hdr(self, data: bytes):
        if len(data) < 8:
            return None, None
        _, _, ptype, plen = struct.unpack(">BBHL", data[:8])
        return ptype, data[8:8+plen]

    def _vehicle_id_response(self) -> bytes:
        vin = b"WC_WIPEWASH_ECU_"[:17].ljust(17, b'\x00')
        payload = vin + struct.pack(">H", WC_LOGICAL_ADDR) + b'\x00' * 8
        return self._hdr(DOIP_VEHICLE_ID_RES, len(payload)) + payload

    # ── UDS NRC ───────────────────────────────────────

    def _nrc(self, sid: int, code: int) -> bytes:
        return bytes([0x7F, sid, code])

    # ── UDS Handlers ──────────────────────────────────

    def _process_uds(self, uds: bytes) -> bytes:
        if not uds:
            return self._nrc(0x00, 0x11)
        sid = uds[0]
        handlers = {
            SID_DSC:   self._handle_dsc,
            SID_RDTC:  self._handle_rdtc,
            SID_RDID:  self._handle_rdid,
            SID_RC:    self._handle_rc,
            SID_TP:    self._handle_tp,
        }
        handler = handlers.get(sid)
        return handler(uds) if handler else self._nrc(sid, 0x11)

    def _handle_dsc(self, uds: bytes) -> bytes:
        if len(uds) < 2:
            return self._nrc(SID_DSC, 0x13)
        sub      = uds[1] & 0x7F
        suppress = bool(uds[1] & 0x80)
        if sub not in (DSC_DEFAULT, DSC_EXTENDED):
            return self._nrc(SID_DSC, 0x12)
        st = self._state
        with st._lock:
            st._session = sub
            if sub == DSC_DEFAULT:
                st._sec_level = 0
            st._pending_seed = {}
        if suppress:
            return b""
        return bytes([0x50, sub, 0x00, 0x32, 0x07, 0xD0])

    def _handle_rdtc(self, uds: bytes) -> bytes:
        from wc_dtc_manager import handle_read_dtc
        return handle_read_dtc(self._dtc, uds)

    def _handle_rdid(self, uds: bytes) -> bytes:
        """DIDs WC : F110-F115"""
        if len(uds) < 3:
            return self._nrc(SID_RDID, 0x13)
        did = (uds[1] << 8) | uds[2]
        st  = self._state

        with st._lock:
            if did == 0xF110:   # WC WiperCurrentMode
                return bytes([0x62, 0xF1, 0x10, st.current_mode & 0xFF])
            elif did == 0xF111: # WC WiperSpeed
                return bytes([0x62, 0xF1, 0x11, st.current_speed & 0xFF])
            elif did == 0xF112: # WC BladeMoving
                return bytes([0x62, 0xF1, 0x12, 1 if st.blade_moving else 0])
            elif did == 0xF113: # WC MotorCurrent (mA)
                curr_ma = int(st.motor_current * 1000)
                return bytes([0x62, 0xF1, 0x13, (curr_ma >> 8) & 0xFF, curr_ma & 0xFF])
            elif did == 0xF114: # WC CANTimeoutActive
                return bytes([0x62, 0xF1, 0x14, 1 if st.can_timeout else 0])
            elif did == 0xF115: # WC ErrorState bitfield
                err = 0
                if st.can_timeout:          err |= 0x01
                if st.fault_status & 0x01:  err |= 0x04
                return bytes([0x62, 0xF1, 0x15, err])

        return self._nrc(SID_RDID, 0x31)

    def _handle_rc(self, uds: bytes) -> bytes:
        """RoutineControl WC -- test moteur avant (RID 0x0201)"""
        st = self._state
        with st._lock:
            session   = st._session

        if session != DSC_EXTENDED:
            return self._nrc(SID_RC, 0x22)
        if len(uds) < 4:
            return self._nrc(SID_RC, 0x13)

        sub      = uds[1]
        rid      = struct.unpack(">H", uds[2:4])[0]
        duration = min(uds[4] if len(uds) >= 5 else 10, 60)

        if sub == 0x01:
            if rid == 0x0201:  # Front Wiper Actuator Test
                with st._lock:
                    if st._test_active:
                        return self._nrc(SID_RC, 0x22)
                    st._test_active   = True
                    st._test_routine  = rid
                    st._test_duration = duration
                    st._t_test_start  = time.time()
                print(f"[WC-RC] Actuator Test 0x{rid:04X} demarre -- {duration}s")
                return bytes([0x71, sub, 0x02, 0x01, duration & 0xFF])

        elif sub == 0x02:
            with st._lock:
                if st._test_active:
                    st._test_active = False
                    print(f"[WC-RC] Actuator Test stoppe")
            return bytes([0x71, sub]) + uds[2:4]

        elif sub == 0x03:
            with st._lock:
                active  = st._test_active
                elapsed = int(time.time() - st._t_test_start) if active else 0
            return bytes([0x71, sub]) + uds[2:4] + bytes([elapsed & 0xFF])

        return self._nrc(SID_RC, 0x31)

    def _handle_tp(self, uds: bytes) -> bytes:
        sub = uds[1] if len(uds) > 1 else 0x00
        if sub & 0x80:
            return b""
        return bytes([0x7E, 0x00])

    # ── Client handler ────────────────────────────────

    def _handle_client(self, conn: socket.socket, addr):
        print(f"[WC-DoIP] Client connecte: {addr}")
        try:
            while self._running:
                try:
                    conn.settimeout(60.0)
                    data = conn.recv(4096)
                    if not data:
                        break
                    ptype, payload = self._parse_hdr(data)
                    if ptype is None:
                        continue

                    if ptype == DOIP_ROUTING_ACT_REQ:
                        resp_payload = struct.pack(">HHBBII",
                            TESTER_ADDR, WC_LOGICAL_ADDR, 0x10, 0x00, 0, 0)
                        conn.send(self._hdr(DOIP_ROUTING_ACT_RES,
                                            len(resp_payload)) + resp_payload)
                        print(f"[WC-DoIP] Routing Activation OK")

                    elif ptype == DOIP_DIAGNOSTIC_MSG and len(payload) > 4:
                        src = struct.unpack(">H", payload[0:2])[0]
                        dst = struct.unpack(">H", payload[2:4])[0]
                        uds = payload[4:]

                        if dst == WC_LOGICAL_ADDR and uds:
                            sid = uds[0]
                            print(f"[WC DoIP UDS] REQ  0x{src:04X}->0x{dst:04X}"
                                  f"  SID=0x{sid:02X}  raw=[{uds.hex(' ').upper()}]")

                            resp_uds = self._process_uds(uds)

                            if resp_uds:
                                print(f"[WC DoIP UDS] RSP  0x{dst:04X}->0x{src:04X}"
                                      f"  SID=0x{resp_uds[0]:02X}"
                                      f"  raw=[{resp_uds.hex(' ').upper()}]")
                                resp_payload = struct.pack(">HH", dst, src) + resp_uds
                                conn.send(self._hdr(DOIP_DIAGNOSTIC_MSG,
                                                    len(resp_payload)) + resp_payload)

                    elif ptype == DOIP_ALIVE_CHECK_REQ:
                        pl = struct.pack(">H", WC_LOGICAL_ADDR)
                        conn.send(self._hdr(DOIP_ALIVE_CHECK_RES, len(pl)) + pl)

                except socket.timeout:
                    break
                except Exception as e:
                    print(f"[WC-DoIP] Erreur client: {e}")
                    break
        finally:
            conn.close()
            print(f"[WC-DoIP] Client deconnecte: {addr}")

    # ── UDP Discovery ─────────────────────────────────

    def _udp_discovery(self):
        try:
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            udp.bind(("0.0.0.0", DOIP_PORT))
            udp.settimeout(1.0)
            self._udp_sock = udp
            print(f"[WC-DoIP] UDP discovery port {DOIP_PORT}")
            while self._running:
                try:
                    data, addr = udp.recvfrom(1024)
                    ptype, _ = self._parse_hdr(data)
                    if ptype == DOIP_VEHICLE_ID_REQ:
                        resp = self._vehicle_id_response()
                        udp.sendto(resp, addr)
                        print(f"[WC-DoIP] Vehicle ID response -> {addr}")
                except socket.timeout:
                    pass
        except Exception as e:
            print(f"[WC-DoIP UDP] Erreur: {e}")

    # ── Demarrage / arret ─────────────────────────────

    def start(self):
        self._running = True

        threading.Thread(target=self._udp_discovery, daemon=True,
                         name="WC-DoIP-UDP").start()

        self._tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._tcp_sock.bind(("0.0.0.0", DOIP_PORT))
        self._tcp_sock.listen(5)
        self._tcp_sock.settimeout(1.0)
        print(f"[WC-DoIP] TCP en ecoute port {DOIP_PORT} (addr WC=0x{WC_LOGICAL_ADDR:04X})")

        while self._running:
            try:
                conn, addr = self._tcp_sock.accept()
                threading.Thread(target=self._handle_client,
                                 args=(conn, addr), daemon=True).start()
            except socket.timeout:
                pass
            except Exception as e:
                if self._running:
                    print(f"[WC-DoIP TCP] Erreur: {e}")

    def stop(self):
        self._running = False
        for s in (self._tcp_sock, self._udp_sock):
            if s:
                try:
                    s.close()
                except Exception:
                    pass
        print("[WC-DoIP] Serveur arrete")


# =====================================================
# POINT D'ENTREE (appele depuis main.py)
# =====================================================
_dtc_mgr    = None
_supervisor = None
_server     = None


def start():
    """
    Demarre le DTC manager, le superviseur DTC et le serveur DoIP WC.
    Bloquant -- appeler dans un thread daemon depuis main.py.
    """
    global _dtc_mgr, _supervisor, _server

    print("=" * 60)
    print("  WC ECU -- DoIP/DTC module (rpisimulator6)")
    print(f"  Adresse diagnostique : 0x{WC_LOGICAL_ADDR:04X}")
    print(f"  Port DoIP            : {DOIP_PORT}")
    print("=" * 60)

    _dtc_mgr = DTCManager_WC()
    _dtc_mgr.print_all()

    _supervisor = _DTCSupervisor(wc_state, _dtc_mgr)
    _supervisor.start()

    _server = WCDoIPServer(wc_state, _dtc_mgr)
    _server.start()   # bloquant


def stop():
    global _supervisor, _server
    if _supervisor:
        _supervisor.stop()
    if _server:
        _server.stop()
    print("[WC-DoIP] Module arrete")
