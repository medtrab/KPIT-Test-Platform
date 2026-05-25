#!/usr/bin/env python3
"""
wc_doip.py
==========
Serveur DoIP/UDS du WC ECU -- integre dans rpisimulator (RPi #2)

Aligne sur le protocole de l'interface graphique (diagnostic_pc) :
  - Header DoIP : version=0x02, inverse=0xFD
  - Port UDP discovery : 13400  (standard ISO 13400)
  - Port TCP           : 13400  (port standard DoIP, WC sur IP 10.20.0.7)
  - Adresse logique WC : 0x0701
  - Tester             : 0x07DF
  - Routing Activation Response : struct.pack(">H B", TESTER, 0x00)
  - DIDs live alignes sur LIVE_SNAPSHOT_DIDS_WC du worker :
      0xF000 WiperCurrentMode  (1 octet)
      0xF001 WiperSpeed        (1 octet)
      0xF002 BladeMoving       (1 octet)
      0xF003 MotorCurrent mA   (2 octets)
      0xF004 PumpStatus        (1 octet)

La gestion DTC (DTCManager_WC), les conditions de declenchement,
les snapshots et les donnees etendues ne sont PAS modifies.
"""

import os
import socket
import struct
import threading
import time

from wc_dtc_manager import DTCManager_WC, handle_read_dtc, handle_clear_dtc

# =====================================================
# CONSTANTES DoIP (aligne sur doip_protocol.py GUI)
# =====================================================
PROTOCOL_VERSION     = 0x02
INVERSE_VERSION      = 0xFD          # (~0x02) & 0xFF

DOIP_VEHICLE_ID_REQ  = 0x0001
DOIP_VEHICLE_ID_RES  = 0x0004
DOIP_ROUTING_ACT_REQ = 0x0005
DOIP_ROUTING_ACT_RES = 0x0006
DOIP_ALIVE_CHECK_REQ = 0x0007
DOIP_ALIVE_CHECK_RES = 0x0008
DOIP_DIAGNOSTIC_MSG  = 0x8001
DOIP_DIAG_MSG_ACK    = 0x8002

WC_LOGICAL_ADDR      = 0x0701
TESTER_ADDR          = 0x07DF

DOIP_UDP_PORT        = 13400   # discovery UDP : port standard ISO 13400
DOIP_TCP_PORT        = 13400   # port standard DoIP ISO 13400 (WC sur IP 10.20.0.7)

# =====================================================
# UDS SIDs
# =====================================================
SID_CDTC = 0x14
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
    Memoire partagee entre bcmcan.py et wc_doip.py.
    bcmcan.py appelle update_from_bcmcan() a chaque RX/TX CAN.
    wc_doip.py lit ces valeurs pour construire les reponses UDS DIDs.
    """
    def __init__(self):
        self._lock = threading.RLock()

        # Etat moteur / lame (mis a jour par bcmcan)
        self.current_mode   = 0
        self.current_speed  = 0
        self.blade_pos      = 0.0    # position lame 0.0-100.0 %
        self.motor_current  = 0.0
        self.fault_status   = 0
        self.can_timeout    = False
        self.pump_status    = 0       # 0=OFF 1=FORWARD 2=BACKWARD

        # Session UDS interne
        self._session       = DSC_DEFAULT
        self._sec_level     = 0
        self._pending_seed  = {}

        # Test actuateur
        self._test_active   = False
        self._test_routine  = 0
        self._test_duration = 0
        self._t_test_start  = 0.0

        # Thread simulation moteur pendant routine
        self._motor_sim_thread = None

    def update_from_bcmcan(self,
                           mode: int, speed: int,
                           blade_pos: float,
                           motor_current_a: float,
                           fault_status: int,
                           can_timeout: bool,
                           pump_status: int = 0):
        with self._lock:
            self.current_mode  = mode
            self.current_speed = speed
            self.blade_pos     = float(blade_pos)
            self.motor_current = motor_current_a
            self.fault_status  = fault_status
            self.can_timeout   = can_timeout
            self.pump_status   = pump_status

    def _run_motor_simulation(self, duration: float):
        """
        Simule le moteur essuie-glace pendant la routine.
        - Phase aller  (0 -> 50% de la duree) : blade_pos 0->100%, current monte
        - Phase retour (50% -> fin)            : blade_pos 100->0%, current descend
        - Apres : blade_pos=0, current=0, mode=DIAG(9) -> OFF(0)
        """
        import math
        t_start = time.time()

        # Courant moteur nominal simule (~300mA en pointe)
        CURRENT_PEAK_A = 0.30

        while True:
            elapsed = time.time() - t_start
            if elapsed >= duration:
                break

            ratio = elapsed / duration          # 0.0 -> 1.0
            # Mouvement lame : aller-retour
            if ratio < 0.5:
                blade = ratio * 2.0 * 100.0     # 0 -> 100%
            else:
                blade = (1.0 - ratio) * 2.0 * 100.0  # 100 -> 0%

            # Courant : sinusoide (max au milieu du mouvement)
            current_a = CURRENT_PEAK_A * math.sin(ratio * math.pi)

            with self._lock:
                self.blade_pos     = round(blade, 1)
                self.motor_current = round(current_a, 3)
                self.current_mode  = 9   # DIAG
                self.current_speed = 1

            time.sleep(0.1)

        # Fin de la routine : retour a l etat repos
        with self._lock:
            self.blade_pos     = 0.0
            self.motor_current = 0.0
            self.current_mode  = 0   # OFF
            self.current_speed = 0
            self._test_active  = False
        print("[WC-RC] Simulation moteur terminee")

    def start_motor_simulation(self, duration: float):
        """Lance le thread de simulation moteur."""
        if self._motor_sim_thread and self._motor_sim_thread.is_alive():
            return
        self._motor_sim_thread = threading.Thread(
            target=self._run_motor_simulation,
            args=(duration,),
            daemon=True,
            name="WC-MotorSim"
        )
        self._motor_sim_thread.start()
        print(f"[WC-RC] Simulation moteur demarree {duration}s")

    def make_snapshot(self) -> dict:
        with self._lock:
            wiper_names = {0:"OFF",1:"TOUCH",2:"SPEED1",3:"SPEED2",
                           4:"AUTO",5:"WASH_FRONT",7:"ERROR",9:"DIAG"}
            return {
                "ignition":    1,
                "wiper_mode":  wiper_names.get(self.current_mode, "UNKNOWN"),
                "motor_curr":  int(self.motor_current * 1000),
                "blade_pos":   int(round(self.blade_pos)),
            }


# Singleton global accessible depuis bcmcan.py
wc_state = _WCState()


# =====================================================
# SERVEUR DoIP / UDS
# =====================================================
class WCDoIPServer:
    """
    Serveur DoIP TCP+UDP pour l'adresse WC 0x0701.
    - UDP port 13400 : discovery (ISO 13400 standard)
    - TCP port 13400 : connexion diagnostique WC (IP 10.20.0.7)

    Header DoIP aligne sur l'interface graphique :
        [0x02, 0xFD, ptype_hi, ptype_lo, len_b3, len_b2, len_b1, len_b0]

    Routing Activation Response conforme a ce qu'attend le GUI :
        payload = struct.pack(">H B", TESTER_ADDR, 0x00)
        => 3 octets : [07 DF 00]
        => GUI lit payload[2] = 0x00 => succes
    """

    def __init__(self, state: _WCState, dtc_mgr: DTCManager_WC):
        self._state    = state
        self._dtc      = dtc_mgr
        self._running  = False
        self._tcp_sock = None
        self._udp_sock = None

    # ── Helpers DoIP ──────────────────────────────────

    def _hdr(self, ptype: int, plen: int) -> bytes:
        """Construit le header DoIP 8 octets conforme ISO 13400 et GUI."""
        return struct.pack(">BBHI",
                           PROTOCOL_VERSION,
                           INVERSE_VERSION,
                           ptype,
                           plen)

    def _parse_hdr(self, data: bytes):
        if len(data) < 8:
            return None, None
        proto, inv, ptype, plen = struct.unpack(">BBHI", data[:8])
        # Valider le header (tolerant : on accepte les deux ordres I/L)
        if proto != PROTOCOL_VERSION:
            return None, None
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
            SID_CDTC: self._handle_cdtc,
            SID_DSC:  self._handle_dsc,
            SID_RDTC: self._handle_rdtc,
            SID_RDID: self._handle_rdid,
            SID_RC:   self._handle_rc,
            SID_TP:   self._handle_tp,
        }
        handler = handlers.get(sid)
        return handler(uds) if handler else self._nrc(sid, 0x11)

    def _handle_cdtc(self, uds: bytes) -> bytes:
        """UDS 0x14 - ClearDiagnosticInformation."""
        return handle_clear_dtc(self._dtc, uds)

    def _handle_dsc(self, uds: bytes) -> bytes:
        """UDS 0x10 - DiagnosticSessionControl."""
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
        """UDS 0x19 - ReadDTCInformation — delegue au dtc_manager (inchange)."""
        return handle_read_dtc(self._dtc, uds)

    def _handle_rdid(self, uds: bytes) -> bytes:
        """
        UDS 0x22 - ReadDataByIdentifier - supporte requetes mono et multi-DID.
        DIDs supportes :
          0xF000  WiperCurrentMode  (1 octet)
          0xF001  WiperSpeed        (1 octet)
          0xF002  BladePosition %   (1 octet)
          0xF003  MotorCurrent mA   (2 octets big-endian)
          0xF004  PumpStatus        (1 octet)
        """
        if len(uds) < 3:
            return self._nrc(SID_RDID, 0x13)

        # Extraire tous les DIDs (chaque DID = 2 octets apres le SID)
        dids = []
        i = 1
        while i + 1 < len(uds):
            dids.append((uds[i] << 8) | uds[i + 1])
            i += 2

        st   = self._state
        resp = bytes([0x62])

        with st._lock:
            for did in dids:
                if did == 0xF000:
                    resp += bytes([0xF0, 0x00, st.current_mode & 0xFF])
                elif did == 0xF001:
                    resp += bytes([0xF0, 0x01, st.current_speed & 0xFF])
                elif did == 0xF002:
                    resp += bytes([0xF0, 0x02,
                                   max(0, min(100, int(round(st.blade_pos))))])
                elif did == 0xF003:
                    curr_ma = int(st.motor_current * 1000)
                    resp += bytes([0xF0, 0x03,
                                   (curr_ma >> 8) & 0xFF,
                                   curr_ma & 0xFF])
                elif did == 0xF004:
                    resp += bytes([0xF0, 0x04, st.pump_status & 0xFF])
                else:
                    return self._nrc(SID_RDID, 0x31)

        if len(resp) == 1:
            return self._nrc(SID_RDID, 0x31)
        return resp

    def _handle_rc(self, uds: bytes) -> bytes:
        """UDS 0x31 - RoutineControl WC (test moteur avant RID 0x0201)."""
        st = self._state
        with st._lock:
            session = st._session

        # Accepter DEFAULT et EXTENDED — l'interface WC n'impose pas de session etendue
        if session not in (DSC_DEFAULT, DSC_EXTENDED):
            return self._nrc(SID_RC, 0x22)
        if len(uds) < 4:
            return self._nrc(SID_RC, 0x13)

        sub      = uds[1]
        rid      = struct.unpack(">H", uds[2:4])[0]
        duration = min(uds[4] if len(uds) >= 5 else 10, 60)

        if sub == 0x01:
            if rid == 0x0201:
                with st._lock:
                    # Auto-expire : liberer si la duree est ecoulee
                    if st._test_active:
                        elapsed = time.time() - st._t_test_start
                        if elapsed >= st._test_duration:
                            st._test_active = False
                            print(f"[WC-RC] Actuator Test 0x{st._test_routine:04X}"
                                  f" auto-expire apres {elapsed:.1f}s")
                        else:
                            return self._nrc(SID_RC, 0x22)
                    st._test_active   = True
                    st._test_routine  = rid
                    st._test_duration = duration
                    st._t_test_start  = time.time()
                print(f"[WC-RC] Actuator Test 0x{rid:04X} started -- {duration}s")
                # Lancer la simulation moteur en arriere-plan
                st.start_motor_simulation(float(duration))
                return bytes([0x71, sub, 0x02, 0x01, duration & 0xFF])

        elif sub == 0x02:
            with st._lock:
                # NRC 0x22 si aucune routine active
                if not st._test_active:
                    return self._nrc(SID_RC, 0x22)   # conditionsNotCorrect — aucune routine en cours
                # NRC 0x31 si le RID ne correspond pas a la routine en cours
                if rid != st._test_routine:
                    return self._nrc(SID_RC, 0x31)   # requestOutOfRange — RID ne correspond pas
                st._test_active = False
                print(f"[WC-RC] Actuator Test 0x{rid:04X} stoppe")
            return bytes([0x71, sub]) + uds[2:4]

        elif sub == 0x03:
            with st._lock:
                active  = st._test_active
                elapsed = int(time.time() - st._t_test_start) if active else 0
                # Auto-expire pour request results aussi
                if active and elapsed >= st._test_duration:
                    st._test_active = False
                    elapsed = st._test_duration
            return bytes([0x71, sub]) + uds[2:4] + bytes([min(elapsed, 0xFF)])

        return self._nrc(SID_RC, 0x31)

    def _handle_tp(self, uds: bytes) -> bytes:
        """UDS 0x3E - TesterPresent."""
        sub = uds[1] if len(uds) > 1 else 0x00
        if sub & 0x80:
            return b""
        return bytes([0x7E, 0x00])

    # ── Client handler TCP ────────────────────────────

    def _handle_client(self, conn: socket.socket, addr):
        print(f"[WC-DoIP] Client connected: {addr}")
        try:
            while self._running:
                try:
                    # Timeout court pour rester reactif.
                    # Le GUI envoie TesterPresent toutes les 2s — on poll a 5s.
                    conn.settimeout(5.0)
                    data = conn.recv(4096)
                    if not data:
                        # data vide = fermeture propre cote GUI (FIN TCP)
                        break
                    ptype, payload = self._parse_hdr(data)
                    if ptype is None:
                        continue

                    # ── Routing Activation ────────────────────────────
                    if ptype == DOIP_ROUTING_ACT_REQ:
                        # ISO 13400-2 : [tester(2)][ecu(2)][response_code(1)][reserved(4)]
                        resp_payload = struct.pack(">H H B 4s",
                                                   TESTER_ADDR,
                                                   WC_LOGICAL_ADDR,
                                                   0x10,
                                                   b'\x00\x00\x00\x00')
                        conn.send(self._hdr(DOIP_ROUTING_ACT_RES, len(resp_payload))
                                  + resp_payload)
                        print(f"[WC-DoIP] Routing Activation OK -> {addr}")

                    # ── Diagnostic Message ────────────────────────────
                    elif ptype == DOIP_DIAGNOSTIC_MSG and len(payload) > 4:
                        src = struct.unpack(">H", payload[0:2])[0]
                        dst = struct.unpack(">H", payload[2:4])[0]
                        uds = payload[4:]

                        if dst == WC_LOGICAL_ADDR and uds:
                            sid = uds[0]
                            print(f"[WC DoIP UDS] REQ  0x{src:04X}->0x{dst:04X}"
                                  f"  SID=0x{sid:02X}  [{uds.hex(' ').upper()}]")

                            resp_uds = self._process_uds(uds)

                            if resp_uds:
                                print(f"[WC DoIP UDS] RSP  0x{dst:04X}->0x{src:04X}"
                                      f"  [{resp_uds.hex(' ').upper()}]")
                                # Reponse DoIP : src=WC dst=Tester
                                resp_payload = struct.pack(">HH", WC_LOGICAL_ADDR, src) + resp_uds
                                conn.send(self._hdr(DOIP_DIAGNOSTIC_MSG,
                                                    len(resp_payload)) + resp_payload)

                    # ── Alive Check ───────────────────────────────────
                    elif ptype == DOIP_ALIVE_CHECK_REQ:
                        pl = struct.pack(">H", WC_LOGICAL_ADDR)
                        conn.send(self._hdr(DOIP_ALIVE_CHECK_RES, len(pl)) + pl)

                except socket.timeout:
                    # Timeout sur recv : pas de données pendant 60s
                    # mais la connexion TCP est toujours active → continuer
                    continue
                except Exception as e:
                    print(f"[WC-DoIP] Client error: {e}")
                    break
        finally:
            conn.close()
            print(f"[WC-DoIP] Client disconnected: {addr}")

    # ── UDP Discovery ─────────────────────────────────

    def _udp_discovery(self):
        """
        Ecoute les VehicleIdentificationRequest sur le port UDP standard 13400.
        Repond avec le VIN et l'adresse logique WC 0x0701.
        """
        try:
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            udp.bind(("0.0.0.0", DOIP_UDP_PORT))
            udp.settimeout(1.0)
            self._udp_sock = udp
            print(f"[WC-DoIP] UDP discovery listening on port {DOIP_UDP_PORT}")
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
            print(f"[WC-DoIP UDP] Error: {e}")

    # ── Start / Stop ──────────────────────────────────

    def start(self):
        self._running = True

        # Pas de thread UDP discovery pour le WC :
        # la connexion se fait directement sur IP fixe 10.20.0.7:13400
        # quand l'interface bascule target -> WC.

        self._tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._tcp_sock.bind(("0.0.0.0", DOIP_TCP_PORT))
        self._tcp_sock.listen(5)
        self._tcp_sock.settimeout(1.0)
        print(f"[WC-DoIP] TCP listening on port {DOIP_TCP_PORT}"
              f" (WC logical addr=0x{WC_LOGICAL_ADDR:04X})")

        while self._running:
            try:
                conn, addr = self._tcp_sock.accept()
                threading.Thread(target=self._handle_client,
                                 args=(conn, addr), daemon=True).start()
            except socket.timeout:
                pass
            except Exception as e:
                if self._running:
                    print(f"[WC-DoIP TCP] Error: {e}")

    def stop(self):
        self._running = False
        if self._tcp_sock:
            try:
                self._tcp_sock.close()
            except Exception:
                pass
        print("[WC-DoIP] Server stopped")


# =====================================================
# POINT D'ENTREE (appele depuis main.py)
# =====================================================
_dtc_mgr = None
_server  = None


def start():
    """
    Demarre le DTC manager et le serveur DoIP WC.
    Bloquant -- appeler dans un thread daemon depuis main.py.
    """
    global _dtc_mgr, _server

    print("=" * 60)
    print("  WC ECU -- DoIP/UDS module")
    print(f"  Adresse diagnostique : 0x{WC_LOGICAL_ADDR:04X}")
    print(f"  TCP connexion port   : {DOIP_TCP_PORT}")
    print(f"  (pas de UDP discovery : connexion directe IP fixe)")
    print("=" * 60)

    _dtc_mgr = DTCManager_WC()
    _dtc_mgr.print_all()

    _server = WCDoIPServer(wc_state, _dtc_mgr)
    _server.start()   # bloquant


def stop():
    global _server
    if _server:
        _server.stop()
    print("[WC-DoIP] Module stopped")