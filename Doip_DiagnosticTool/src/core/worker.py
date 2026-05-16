"""
DiagnosticWorker – executes all DoIP/UDS network operations in a dedicated thread.

PURE CLIENT VERSION:
- Sends UDS requests correctly.
- 0x19 (Read DTC) and 0x14 (Clear DTC) are SEND-ONLY: responses are discarded.
- 0x22 (Read DID) responses are parsed only for live snapshot cache; no other
  business logic is applied.
- No DTC generation, no actuator simulation, no internal state computation.
"""

import socket
import struct
import time
import threading
from collections import deque
from typing import Optional, Dict
from PySide6.QtCore import QObject, Signal, QTimer

from core.constants import (
    LOGICAL_ADDR_TESTER,
    LOGICAL_ADDR_BCM,
    UDS_SERVICE,
    UDS_NRC,
)
from core.transport.diagnostic_client import (
    discover_ecus,
    connect_to_ecu,
    receive_doip_message,
    bytes_to_hex_str,
)
from core.transport.doip_protocol import send_doip_message


class DiagnosticWorker(QObject):
    """
    Worker running in a dedicated QThread.
    Manages the request queue, socket and responses.
    """

    # Signals emitted toward the controller (and thus toward the UI)
    log_signal = Signal(str, str)             # level, message
    ecu_discovered = Signal(list)             # list of discovered ECUs
    connection_state = Signal(bool, str)      # (connected, message)
    routing_state = Signal(bool)              # routing active/inactive
    session_changed = Signal(int)             # current session (0x01 or 0x03)
    security_level_changed = Signal(int)      # level 0, 1, 2
    dtc_list_updated = Signal(list)           # list of (dtc_code, status)  – kept for API compat
    seed_received = Signal(bytes)             # received seed
    uds_positive = Signal(bytes)              # raw positive UDS response
    uds_sent = Signal(bytes)                   # trame UDS envoyée (TX)
    uds_negative = Signal(int, int)           # (service, nrc)
    error_occurred = Signal(str)              # error message
    snapshot_received = Signal(int, int, bytes)
    extended_received = Signal(int, int, bytes)
    dtc_database_ready = Signal(dict)



    # SIDs for which we fire-and-forget (no response wait)
    # NOTE: 0x19 and 0x14 removed — the real BCM responds to these now
    _NO_RESPONSE_SIDS = frozenset([])

    # IP fixe du RPi Simulateur (WC ECU) — modifiable ici ou via set_wc_ip()
    WC_ECU_IP = "10.20.0.7"

    def __init__(self):
        super().__init__()
        self._running = True
        # --- deux sockets séparés : un par ECU physique ---
        self._sock_bcm: Optional[socket.socket] = None   # RPi BCM  port 13400
        self._sock_wc:  Optional[socket.socket] = None   # RPi Sim  port 13400
        self._ecu_info: Optional[Dict] = None
        self._target_addr = LOGICAL_ADDR_BCM
        self._request_queue: deque = deque()
        self._lock = threading.RLock()
        self._keep_alive_timer: Optional[QTimer] = None
        self._pending = False
        self._last_dtc_database: dict = {"bcm": {}, "wc": {}}

    # ------------------------------------------------------------------
    # Propriete _sock : retourne le socket actif selon le target courant
    # ------------------------------------------------------------------
    @property
    def _sock(self) -> Optional[socket.socket]:
        from core.constants import LOGICAL_ADDR_WC
        return self._sock_wc if self._target_addr == LOGICAL_ADDR_WC else self._sock_bcm

    @_sock.setter
    def _sock(self, value: Optional[socket.socket]):
        from core.constants import LOGICAL_ADDR_WC
        if self._target_addr == LOGICAL_ADDR_WC:
            self._sock_wc = value
        else:
            self._sock_bcm = value

    def set_wc_ip(self, ip: str):
        self.WC_ECU_IP = ip

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_no_response_request(self, request: bytes) -> bool:
        """True if this request should be sent without waiting for a reply."""
        return bool(request) and (request[0] in self._NO_RESPONSE_SIDS)
    def _reset_runtime_state(self):
        self._pending = False
        self._request_queue.clear()
    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def stop(self):
        self._running = False
        if self._keep_alive_timer:
            self._keep_alive_timer.stop()
            self._keep_alive_timer = None
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None

    # ------------------------------------------------------------------
    # Public API (called from controller via direct method calls or queued signals)
    # ------------------------------------------------------------------

    def discover_ecus(self):
        try:
            ecus = discover_ecus()
            self.ecu_discovered.emit(ecus)
            self.log_signal.emit("INFO", f"Discovery: {len(ecus)} ECU(s) found")
        except Exception as e:
            self.log_signal.emit("ERROR", f"Discovery error: {e}")
            self.error_occurred.emit(f"Discovery failed: {e}")

    def connect_to_ecu(self, ecu_info: Dict):
        """
        Connexion a un ECU.
        Stocke dans _sock_bcm ou _sock_wc selon logical_address.
        """
        from core.constants import LOGICAL_ADDR_WC
        logical_addr = ecu_info.get('logical_address', LOGICAL_ADDR_BCM)
        self._ecu_info = ecu_info
        try:
            sock = connect_to_ecu(ecu_info)
            if sock:
                with self._lock:
                    if logical_addr == LOGICAL_ADDR_WC:
                        if self._sock_wc:
                            self._sock_wc.close()
                        self._sock_wc = sock
                        self.log_signal.emit("INFO", f"WC socket connected to {ecu_info['ip']}:13400")
                    else:
                        if self._sock_bcm:
                            self._sock_bcm.close()
                        self._sock_bcm = sock
                        self._reset_runtime_state()
                        self.log_signal.emit("INFO", f"BCM socket connected to {ecu_info['ip']}:13400")

                self._send_dtc_database()
                self.connection_state.emit(True, f"Connected to {ecu_info['ip']}")
                self.routing_state.emit(True)

                if self._keep_alive_timer is None:
                    self._keep_alive_timer = QTimer()
                    self._keep_alive_timer.timeout.connect(self.send_tester_present)
                    self._keep_alive_timer.start(2000)

                if logical_addr != LOGICAL_ADDR_WC:
                    # Pour BCM : ne pas réémettre session/security
                    # l'UI BCM gère la restauration via _bcm_ui_frozen
                    pass
                else:
                    from core.constants import LOGICAL_ADDR_WC
                    if logical_addr == LOGICAL_ADDR_WC:
                        self.session_changed.emit(0x01)
                        self.security_level_changed.emit(0)
                    # BCM : ne pas émettre — l'UI restaure elle-même via _bcm_ui_frozen
            else:
                self.connection_state.emit(False, "Connection failed")
                self.error_occurred.emit("Connection refused")
        except Exception as e:
            self.log_signal.emit("ERROR", f"Connection error: {e}")
            self.error_occurred.emit(f"Connection failed: {e}")

    def connect_to_wc(self):
        """
        Ouvre la connexion TCP vers RPi Simulateur (WC) sur IP fixe WC_ECU_IP:13400.
        Appele automatiquement par set_target(0x0701) si pas encore connecte.
        """
        from core.constants import LOGICAL_ADDR_WC
        wc_ecu_info = {
            'ip': self.WC_ECU_IP,
            'vin': 'WC_WIPEWASH_ECU_',
            'logical_address': LOGICAL_ADDR_WC,
        }
        self.log_signal.emit("INFO", f"Connexion WC vers {self.WC_ECU_IP}:13400 ...")
        self.connect_to_ecu(wc_ecu_info)

    def _send_dtc_database(self):
        try:
            import json, os
            current_dir = os.path.dirname(__file__)

            payload = {"bcm": {}, "wc": {}}

            for ecu_key, filename in [("bcm", "dtc_bcm.json"), ("wc", "dtc_wc.json")]:
                json_path = os.path.join(current_dir, "data", filename)
                if os.path.exists(json_path):
                    with open(json_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for dtc_name, dtc_info in data.get("dtcs", {}).items():
                        b = dtc_info.get("bytes", [])
                        if len(b) == 3:
                            dtc_id = (b[0] << 16) | (b[1] << 8) | b[2]
                            payload[ecu_key][dtc_id] = {
                                "id": dtc_id,
                                "name": dtc_name,
                                "description": dtc_info.get("description", ""),
                                "category": dtc_info.get("category", ""),
                                "status": dtc_info.get("status", 0),  # ✅ AJOUT CRITIQUE
                            }

            self._last_dtc_database = payload
            self.dtc_database_ready.emit(payload)
            self.log_signal.emit("INFO",
                                 f"DTC database: BCM={len(payload['bcm'])} WC={len(payload['wc'])}")
        except Exception as e:
            self.log_signal.emit("ERROR", f"DTC database load error: {e}")
            self.dtc_database_ready.emit({"bcm": {}, "wc": {}})
    def disconnect(self):
        with self._lock:
            for sock_attr in ('_sock_bcm', '_sock_wc'):
                s = getattr(self, sock_attr, None)
                if s:
                    try:
                        s.close()
                    except Exception:
                        pass
                    setattr(self, sock_attr, None)
            self._reset_runtime_state()

        if self._keep_alive_timer:
            self._keep_alive_timer.stop()
            self._keep_alive_timer = None

        self._ecu_info = None
        self.connection_state.emit(False, "Disconnected")
        self.routing_state.emit(False)
        self.session_changed.emit(0x01)
        self.security_level_changed.emit(0)
        self.log_signal.emit("INFO", "Disconnected")

    def request_routine_results(self, routine_id: int):
        req = bytes([UDS_SERVICE["ROUTINE_CONTROL"], 0x03]) + routine_id.to_bytes(2, "big")
        self.send_uds_request(req)

    def set_target(self, addr: int):
        from core.constants import LOGICAL_ADDR_WC
        with self._lock:
            self._target_addr = addr

            # RESET COMPLET de la file et du cache
            self._request_queue.clear()
            self._pending = False

            # Vider le buffer du socket actif apres changement de target
            active = self._sock_wc if addr == LOGICAL_ADDR_WC else self._sock_bcm
            if active:
                try:
                    active.settimeout(0.01)
                    while True:
                        active.recv(4096)
                except (socket.timeout, OSError):
                    pass

        self.log_signal.emit("INFO", f"Target changed to 0x{addr:04X}")

        # Si on bascule sur WC et que le socket WC n'est pas encore ouvert,
        # on ouvre la connexion TCP vers le RPi Simulateur automatiquement.
        if addr == LOGICAL_ADDR_WC and self._sock_wc is None:
            self.connect_to_wc()

    def send_uds_request(self, request: bytes, priority: bool = False):
        with self._lock:
            if priority:
                self._request_queue.appendleft(request)
            else:
                self._request_queue.append(request)

    def read_data_by_identifier(self, did: int):
        request = bytes([0x22, did >> 8, did & 0xFF])
        self.send_uds_request(request)

    def send_tester_present(self):
        if not self._sock or self._pending:
            return
        req = bytes([UDS_SERVICE["TESTER_PRESENT"], 0x00])
        self.send_uds_request(req, priority=False)

    def switch_session(self, session_type: int):
        if session_type not in (0x01, 0x03):
            return
        req = bytes([UDS_SERVICE["DIAGNOSTIC_SESSION_CONTROL"], session_type])
        self.send_uds_request(req, priority=True)

    def request_seed(self, level: int):
        req = bytes([UDS_SERVICE["SECURITY_ACCESS"], 0x01])   # sub 0x01 = requestSeed level 1
        self.send_uds_request(req, priority=True)

    def send_key(self, level: int, key_bytes: bytes):
        req = bytes([UDS_SERVICE["SECURITY_ACCESS"], 0x02]) + key_bytes  # sub 0x02 = sendKey level 1
        self.send_uds_request(req, priority=True)

    def read_dtc(self, status_mask: int = 0xFF):
        """
        Envoie 0x19 0x02 vers l'ECU cible.
        Le BCM repond avec les vrais DTCs — la reponse 0x59 est parsee
        dans _handle_response et emise via dtc_list_updated.
        """
        req = bytes([UDS_SERVICE["READ_DTC"], 0x02, status_mask])
        self.send_uds_request(req)
    def clear_dtc(self):
        """Send-only – 0x14 response will be discarded."""
        req = bytes([UDS_SERVICE["CLEAR_DTC"], 0x00, 0x00, 0x00])
        self.send_uds_request(req, priority=True)

    def read_dtc_snapshot(self, dtc_code: int, record_num: int):
        """Send-only – response discarded."""
        dtc_code = dtc_code & 0xFFFFFF
        req = (
            bytes([UDS_SERVICE["READ_DTC"], 0x04])
            + dtc_code.to_bytes(3, "big")
            + bytes([record_num])
        )
        self.send_uds_request(req)

    def read_dtc_extended(self, dtc_code: int, record_num: int):
        """Send-only – response discarded."""
        dtc_code = dtc_code & 0xFFFFFF
        req = (
            bytes([UDS_SERVICE["READ_DTC"], 0x06])
            + dtc_code.to_bytes(3, "big")
            + bytes([record_num])
        )
        self.send_uds_request(req)

    def start_routine(self, rid: int, value: int):
        req = bytes([0x31, 0x01, (rid >> 8) & 0xFF, rid & 0xFF, value & 0xFF])
        self.send_uds_request(req, priority=True)

    def stop_routine(self, routine_id: int):
        req = bytes([UDS_SERVICE["ROUTINE_CONTROL"], 0x02]) + routine_id.to_bytes(2, "big")
        self.send_uds_request(req, priority=True)

    def write_coding(self, did: int, value: int):
        req = bytes([
            UDS_SERVICE["WRITE_DATA_BY_ID"],
            (did >> 8) & 0xFF,
            did & 0xFF,
            value & 0xFF,
        ])
        self.send_uds_request(req, priority=True)

    def communication_control(self, subf: int):
        req = bytes([UDS_SERVICE["COMMUNICATION_CONTROL"], subf, 0x01])
        self.send_uds_request(req, priority=True)

    def clear_pending_requests(self):
        with self._lock:
            self._request_queue.clear()

    # ------------------------------------------------------------------
    # Main thread loop
    # ------------------------------------------------------------------

    def run(self):
        self.log_signal.emit("INFO", "Worker started")

        while self._running:
            request = None

            # Step 1 – dequeue next request when nothing is pending
            if not self._pending:
                with self._lock:
                    if self._request_queue:
                        request = self._request_queue.popleft()

                if request and self._sock:
                    try:
                        self._send_uds_request(request)

                        if self._is_no_response_request(request):
                            pass  # Fire-and-forget: do not set _pending
                        else:
                            self._pending = True

                    except Exception as e:
                        self.log_signal.emit("ERROR", f"Send error: {e}")
                        with self._lock:
                            if self._sock:
                                self._request_queue.appendleft(request)
                        time.sleep(0.05)

            # Step 2 – read response when one is expected
            if self._sock and self._pending:
                try:
                    self._sock.settimeout(0.35)
                    payload_type, payload = receive_doip_message(self._sock, timeout=0.35)

                    if payload_type == 0x8001 and payload and len(payload) >= 4:
                        uds_resp = payload[4:]
                        self._handle_response(uds_resp)

                    self._pending = False

                except socket.timeout:
                    self._pending = False

                except Exception as e:
                    self.log_signal.emit("ERROR", f"Receive error: {e}")
                    self.disconnect()
                    self._pending = False

            time.sleep(0.005)

        self.log_signal.emit("INFO", "Worker stopped")

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _send_uds_request(self, request: bytes):
        if not self._sock:
            raise RuntimeError("Socket not connected")
        doip_payload = struct.pack(">HH", LOGICAL_ADDR_TESTER, self._target_addr) + request
        send_doip_message(self._sock, 0x8001, doip_payload)

        hex_str = bytes_to_hex_str(request)
        self.log_signal.emit("SEND", f"→ {hex_str}")
        self.uds_sent.emit(request)
    # ------------------------------------------------------------------
    # Response handling (minimal – no business logic)
    # ------------------------------------------------------------------

    def _handle_response(self, response: bytes):
        if not response:
            return

        first_byte = response[0]

        # ----------------------------------------------------------------
        # Negative response
        # ----------------------------------------------------------------
        if first_byte == 0x7F:
            sid = response[1] if len(response) > 1 else 0
            nrc = response[2] if len(response) > 2 else 0
            self.uds_negative.emit(sid, nrc)
            msg = UDS_NRC.get(nrc, f"NRC 0x{nrc:02X}")
            self.log_signal.emit("NEGATIVE", f"NRC 0x{nrc:02X}: {msg}")
            return

        # ----------------------------------------------------------------
        # DTC positive responses — parse real data from BCM
        # ----------------------------------------------------------------
        if first_byte == 0x59:
            hex_str = bytes_to_hex_str(response)
            self.log_signal.emit("RECV", f"← {hex_str}")

            # Sub 0x02 : reportDTCByStatusMask
            if len(response) >= 3 and response[1] == 0x02:
                dtc_list = []
                idx = 3  # skip [0x59, 0x02, availabilityMask]
                while idx + 3 < len(response):
                    dtc_code = (response[idx] << 16) | (response[idx + 1] << 8) | response[idx + 2]
                    status = response[idx + 3]
                    dtc_list.append((dtc_code, status))
                    idx += 4
                self.dtc_list_updated.emit(dtc_list)
                self.log_signal.emit("INFO", f"DTC report: {len(dtc_list)} DTC(s)")

            # Sub 0x04 : reportDTCSnapshotRecord
            elif len(response) >= 7 and response[1] == 0x04:
                dtc_code = (response[2] << 16) | (response[3] << 8) | response[4]
                rec_num = response[6]
                self.snapshot_received.emit(dtc_code, rec_num, response)

            # Sub 0x06 : reportDTCExtDataRecord
            elif len(response) >= 6 and response[1] == 0x06:
                dtc_code = (response[2] << 16) | (response[3] << 8) | response[4]
                status = response[5]
                self.extended_received.emit(dtc_code, status, response)

            self.uds_positive.emit(response)
            return

        # ----------------------------------------------------------------
        # DTC clear positive response
        # ----------------------------------------------------------------
        if first_byte == 0x54:
            self.log_signal.emit("RECV", "← 54  [DTC cleared OK]")
            self.uds_positive.emit(response)
            return

        # ----------------------------------------------------------------
        # Positive response
        # ----------------------------------------------------------------
        sid = first_byte - 0x40


        hex_str = bytes_to_hex_str(response)
        self.log_signal.emit("RECV", f"← {hex_str}")

        self.uds_positive.emit(response)

        # ----------------------------------------------------------------
        # Per-service dispatch
        # ----------------------------------------------------------------
        if sid == 0x10:  # Session Control
            if len(response) >= 2:
                self.session_changed.emit(response[1])
        elif sid == 0x11:  # ECUReset positive response
            # Reset session et security côté UI
            self.session_changed.emit(0x01)
            self.security_level_changed.emit(0)
        elif sid == 0x27:  # Security Access
            if len(response) >= 2:
                subf = response[1]
                if subf % 2 == 1:  # requestSeed response
                    if len(response) >= 4:
                        self.seed_received.emit(response[2:])
                else:  # sendKey response
                    level = subf // 2
                    self.security_level_changed.emit(0 if level == 0 else 1)