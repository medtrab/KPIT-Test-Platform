#!/usr/bin/env python3
"""
DoIP/UDS ECU Simulator – PURE PROTOCOL SKELETON (ISO 13400 / ISO 14229)

This module provides a minimal UDS server for protocol-level testing only.
ALL ECU simulation logic (plant model, DTC engine, coding side-effects,
routine scheduler, actuator control) has been removed.

Behavior:
  - 0x10  Session Control         → positive response
  - 0x11  ECU Reset               → positive response
  - 0x14  Clear DTC               → NO RESPONSE (send-only client mode)
  - 0x19  Read DTC Information    → NO RESPONSE
  - 0x22  Read Data by Identifier → NO RESPONSE
  - 0x27  Security Access         → positive response (seed/key)
  - 0x28  Communication Control   → positive response
  - 0x2E  Write Data by Identifier→ positive response (no side-effects)
  - 0x31  Routine Control         → minimal positive response (no logic)
  - 0x3E  Tester Present          → positive response
"""

import socket
import struct
import threading
import logging
import time
from typing import Optional, Tuple

from core import logging_config

# Import shared DoIP protocol definitions
from doip_protocol import (
    DOIP_PROTOCOL_VERSION,
    DOIP_INVERSE_VERSION,
    DOIP_VEHICLE_IDENTIFICATION_REQUEST,
    DOIP_VEHICLE_IDENTIFICATION_RESPONSE,
    DOIP_ROUTING_ACTIVATION_REQUEST,
    DOIP_ROUTING_ACTIVATION_RESPONSE,
    DOIP_PAYLOAD_DIAG,
    LOGICAL_ADDR_TESTER,
    LOGICAL_ADDR_ECU,
    hex_str_to_bytes,
    bytes_to_hex_str,
    send_doip_message,
    receive_doip_message,
    build_doip_header,
)

logger = logging.getLogger(__name__)

# WC ECU logical address (kept for routing compatibility)
LOGICAL_ADDR_WC = 0x0701


# =============================================================================
# Minimal routing / connection state  (no ECU simulation)
# =============================================================================
class ECUState:
    """
    Holds only the connection and session state required for DoIP routing.
    All simulation state (plant, DTCs, coding params, actuators) has been removed.
    """
    def __init__(self):
        self.session: int = 0x01
        self.security_level: int = 0
        self.rx_enabled: bool = True
        self.tx_enabled: bool = True
        self.client_connected: bool = False
        self.client_address = None
        self.client_logical_address = None
        self.routing_active: bool = False
        self._lock = threading.RLock()

    def reset(self):
        """Reset session/security to defaults (used by ECU Reset)."""
        with self._lock:
            self.session = 0x01
            self.security_level = 0


# =============================================================================
# Minimal UDS handler for BCM (pure protocol, no simulation)
# =============================================================================
class UDSECU:
    """
    Minimal UDS service handler.

    Only services that require a protocol-level acknowledgement are handled.
    Services whose responses are ignored by the client (0x19, 0x22) return
    no response so the transport layer stays clean.
    """

    def __init__(self):
        self.state = ECUState()
        self._lock = self.state._lock
        self.coding = {
            0xF200: 0,  # Rain
            0xF201: 0,  # WC
            0xF202: 0,  # Rear
        }

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def handle_uds(self, uds: bytes) -> Optional[bytes]:
        """
        Process a UDS request.  Returns response bytes or None (no response).
        """
        with self._lock:
            return self._dispatch(uds)

    def _dispatch(self, uds: bytes) -> Optional[bytes]:
        if len(uds) == 0:
            return bytes([0x7F, 0x00, 0x13])

        sid = uds[0]

        # 0x10 – Diagnostic Session Control
        if sid == 0x10:
            return self._session_control(uds)

        # 0x11 – ECU Reset
        if sid == 0x11:
            return self._ecu_reset(uds)

        # 0x14 – Clear DTC
        if sid == 0x14:
            return self._clear_dtc(uds)

        # 0x19 – Read DTC Information  → NO RESPONSE
        if sid == 0x19:
            return None

        # 0x22 – Read Data by Identifier  → NO RESPONSE
        if sid == 0x22:
            return self._read_data(uds)
        # 0x27 – Security Access
        if sid == 0x27:
            return self._security_access(uds)

        # 0x28 – Communication Control
        if sid == 0x28:
            return self._communication_control(uds)

        # 0x2E – Write Data by Identifier  → positive ack, no side-effects
        if sid == 0x2E:
            return self._write_data(uds)

        # 0x31 – Routine Control  → minimal positive response
        if sid == 0x31:
            return self._routine_control(uds)

        # 0x3E – Tester Present
        if sid == 0x3E:
            return self._tester_present(uds)

        # Service not supported
        return bytes([0x7F, sid, 0x11])

    # ------------------------------------------------------------------
    # Service implementations (protocol only, zero business logic)
    # ------------------------------------------------------------------

    def _session_control(self, uds: bytes) -> bytes:
        if len(uds) < 2:
            return bytes([0x7F, 0x10, 0x13])
        subfunction = uds[1]
        if subfunction not in (0x01, 0x03):
            return bytes([0x7F, 0x10, 0x12])
        self.state.session = subfunction
        p2, p2_star = (0x0032, 0x07D0) if subfunction == 0x01 else (0x0190, 0x1F40)
        resp = bytearray([0x50, subfunction])
        resp.extend(p2.to_bytes(2, "big"))
        resp.extend(p2_star.to_bytes(2, "big"))
        return bytes(resp)

    def _ecu_reset(self, uds: bytes) -> bytes:
        if len(uds) < 2:
            return bytes([0x7F, 0x11, 0x13])
        reset_type = uds[1]
        if reset_type != 0x03:
            return bytes([0x7F, 0x11, 0x12])
        self.state.reset()
        return bytes([0x51, 0x03])

    def _security_access(self, uds: bytes) -> bytes:
        if len(uds) < 2:
            return bytes([0x7F, 0x27, 0x13])
        subfunction = uds[1]
        if subfunction == 0x03:
            # Seed request – return fixed seed
            return bytes([0x67, 0x03, 0x11, 0x22, 0x33, 0x44])
        elif subfunction == 0x04:
            if len(uds) < 6:
                return bytes([0x7F, 0x27, 0x13])
            received_key = uds[2:6]
            expected_key = b"\x44\x33\x22\x11"
            if received_key == expected_key:
                self.state.security_level = 2
                return bytes([0x67, 0x04])
            return bytes([0x7F, 0x27, 0x35])
        return bytes([0x7F, 0x27, 0x12])

    def _communication_control(self, uds: bytes) -> bytes:
        if len(uds) < 2:
            return bytes([0x7F, 0x28, 0x13])
        subfunction = uds[1]
        if subfunction not in (0x00, 0x01, 0x02, 0x03):
            return bytes([0x7F, 0x28, 0x12])
        # Update flags for completeness (no downstream effect)
        if subfunction == 0x00:
            self.state.rx_enabled = True
            self.state.tx_enabled = True
        elif subfunction == 0x01:
            self.state.rx_enabled = False
        elif subfunction == 0x02:
            self.state.tx_enabled = False
        elif subfunction == 0x03:
            self.state.rx_enabled = False
            self.state.tx_enabled = False
        return bytes([0x68, subfunction])


    def _write_data(self, uds: bytes) -> bytes:
        if len(uds) < 4:
            return bytes([0x7F, 0x2E, 0x13])

        if self.state.session != 0x03:
            return bytes([0x7F, 0x2E, 0x7E])

        if self.state.security_level < 2:
            return bytes([0x7F, 0x2E, 0x33])

        did = (uds[1] << 8) | uds[2]
        value = uds[3]

        # 🔥 STOCKER CODING
        self.coding[did] = value

        return bytes([0x6E, uds[1], uds[2]])

    _BCM_LIVE_DIDS = {
        0xF100: bytes([0x00]),  # WiperCurrentMode  → OFF
        0xF101: bytes([0x00]),  # WiperSpeed        → 0
        0xF102: bytes([0x00]),  # BladePosition     → 0%
        0xF103: bytes([0x00, 0x00]),  # MotorCurrent      → 0 mA
        0xF104: bytes([0x00]),  # PumpStatus        → OFF
        0xF105: bytes([0x00]),  # RainIntensity     → 0
        0xF106: bytes([0x00]),  # RearWiperStatus   → OFF
        0xF107: bytes([0x00]),  # ErrorState        → 0
    }

    def _read_data(self, uds: bytes) -> bytes:
        if len(uds) < 3:
            return bytes([0x7F, 0x22, 0x13])
        did = (uds[1] << 8) | uds[2]
        if did not in self._BCM_LIVE_DIDS:
            return bytes([0x7F, 0x22, 0x31])
        value = self._BCM_LIVE_DIDS[did]
        return bytes([0x62, uds[1], uds[2]]) + value

    def _routine_control(self, uds: bytes) -> bytes:
        if len(uds) < 4:
            return bytes([0x7F, 0x31, 0x13])

        # 🔒 session obligatoire
        if self.state.session != 0x03:
            return bytes([0x7F, 0x31, 0x7E])

        # 🔒 security obligatoire
        if self.state.security_level < 2:
            return bytes([0x7F, 0x31, 0x33])

        subfunction = uds[1]
        if subfunction not in (0x01, 0x02, 0x03):
            return bytes([0x7F, 0x31, 0x12])

        rid = (uds[2] << 8) | uds[3]
        value = uds[4] if len(uds) > 4 else 0x00

        # 🔥 DID (OFFICIEL)
        DID_RAIN = 0xF200
        DID_REAR = 0xF202

        # 🔥 ROUTINES (OFFICIEL)
        ROUTINE_FRONT = 0x0201
        ROUTINE_REAR = 0x0202
        ROUTINE_PUMP_FWD = 0x0203
        ROUTINE_PUMP_BWD = 0x0204
        ROUTINE_RAIN = 0x0205

        # -------------------------------------------------
        # ❌ REAR dépend de F202
        # -------------------------------------------------
        if rid == ROUTINE_REAR:
            if self.coding.get(DID_REAR, 0) == 0:
                return bytes([0x7F, 0x31, 0x22])

        # -------------------------------------------------
        # ❌ RAIN dépend de F200
        # -------------------------------------------------
        if rid == ROUTINE_RAIN:
            if self.coding.get(DID_RAIN, 0) == 0:
                return bytes([0x7F, 0x31, 0x22])

        # -------------------------------------------------
        # ✅ FRONT + PUMP toujours OK
        # -------------------------------------------------

        return bytes([0x71, subfunction, uds[2], uds[3], value])

    def _clear_dtc(self, uds: bytes) -> bytes:
        # 🔒 session obligatoire
        if self.state.session != 0x03:
            return bytes([0x7F, 0x14, 0x7E])

        # 🔒 security obligatoire
        if self.state.security_level < 2:
            return bytes([0x7F, 0x14, 0x33])

        return bytes([0x54])

    def _tester_present(self, uds: bytes) -> bytes:
        if len(uds) < 2:
            return bytes([0x7F, 0x3E, 0x13])
        return bytes([0x7E, uds[1]])


# =============================================================================
# Minimal WC ECU (same no-simulation policy)
# =============================================================================
class WCUDSECU:
    """
    WC ECU — supporte uniquement : 0x10, 0x19, 0x22, 0x31, 0x3E
    0x19 et 0x22 → NO RESPONSE (client lit depuis JSON local)
    """
    def __init__(self):
        self.session: int = 0x01
        self.security_level: int = 0
        self._lock = threading.RLock()

    def start_monitoring(self, interval: float = 0.1):
        pass

    def stop_monitoring(self):
        pass

    def handle_uds(self, uds: bytes) -> Optional[bytes]:
        with self._lock:
            return self._dispatch(uds)
    def _dispatch(self, uds: bytes) -> Optional[bytes]:
        if not uds:
            return bytes([0x7F, 0x00, 0x13])

        sid = uds[0]

        if sid == 0x10:
            return self._session_control(uds)

        if sid == 0x19:
            return None  # NO RESPONSE

        if sid == 0x22:
            return self._read_data(uds)

        if sid == 0x31:
            return self._routine_control(uds)

        if sid == 0x3E:
            if len(uds) < 2:
                return bytes([0x7F, 0x3E, 0x13])
            return bytes([0x7E, uds[1]])

        return bytes([0x7F, sid, 0x11])

    def _session_control(self, uds: bytes) -> bytes:
        if len(uds) < 2:
            return bytes([0x7F, 0x10, 0x13])
        subf = uds[1]
        if subf not in (0x01, 0x03):
            return bytes([0x7F, 0x10, 0x12])
        self.session = subf
        p2, p2_star = (0x0032, 0x07D0) if subf == 0x01 else (0x0190, 0x1F40)
        resp = bytearray([0x50, subf])
        resp.extend(p2.to_bytes(2, "big"))
        resp.extend(p2_star.to_bytes(2, "big"))
        return bytes(resp)

    _WC_LIVE_DIDS = {
        0xF000: bytes([0x00]),  # WiperCurrentMode  → OFF
        0xF001: bytes([0x00]),  # WiperSpeed        → 0
        0xF002: bytes([0x00]),  # BladePosition     → 0%
        0xF003: bytes([0x00, 0x00]),  # MotorCurrent      → 0 mA
        0xF004: bytes([0x00]),  # PumpStatus        → OFF
    }

    def _read_data(self, uds: bytes) -> bytes:
        if len(uds) < 3:
            return bytes([0x7F, 0x22, 0x13])
        did = (uds[1] << 8) | uds[2]
        if did not in self._WC_LIVE_DIDS:
            return bytes([0x7F, 0x22, 0x31])
        value = self._WC_LIVE_DIDS[did]
        return bytes([0x62, uds[1], uds[2]]) + value

    def _routine_control(self, uds: bytes) -> bytes:
        if len(uds) < 4:
            return bytes([0x7F, 0x31, 0x13])
        subf = uds[1]
        if subf not in (0x01, 0x02, 0x03):
            return bytes([0x7F, 0x31, 0x12])
        # WC accepte uniquement la routine front wiper (0x0201)
        rid = (uds[2] << 8) | uds[3]
        if rid != 0x0201:
            return bytes([0x7F, 0x31, 0x31])  # requestOutOfRange
        return bytes([0x71, subf, uds[2], uds[3]])


# =============================================================================
# Network helpers (unchanged)
# =============================================================================
def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"
# =============================================================================
# UDP Discovery Server (unchanged)
# =============================================================================
def udp_discovery_server() -> None:
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    try:
        udp_sock.bind(("0.0.0.0", 13400))
        logger.info("UDP DoIP discovery server listening on port 13400")

        while True:
            data, client_addr = udp_sock.recvfrom(4096)
            if len(data) >= 8:
                proto_ver, inv_ver, payload_type = struct.unpack(">BBH", data[:4])
                payload_len = struct.unpack(">I", data[4:8])[0]

                if (
                    proto_ver == DOIP_PROTOCOL_VERSION
                    and inv_ver == DOIP_INVERSE_VERSION
                    and payload_type == DOIP_VEHICLE_IDENTIFICATION_REQUEST
                ):
                    logger.info(f"Discovery request received from {client_addr}")

                    vin = b"VIN12345678901234"
                    logical_address = LOGICAL_ADDR_ECU.to_bytes(2, "big")
                    eid = b"\x00\x00\x00\x00\x00\x01"
                    gid = b"\x00\x00\x00\x00\x00\x02"
                    ip_bytes = socket.inet_aton(get_local_ip())

                    payload = vin + logical_address + eid + gid + ip_bytes
                    header = build_doip_header(
                        DOIP_VEHICLE_IDENTIFICATION_RESPONSE, len(payload)
                    )
                    udp_sock.sendto(header + payload, client_addr)
                    logger.info(f"Discovery response sent to {client_addr}")

    except Exception as e:
        logger.error(f"UDP discovery server error: {e}")
    finally:
        udp_sock.close()


# =============================================================================
# DoIP Message Handler (TCP) – routes to BCM or WC, no simulation
# =============================================================================
def handle_doip_message(
    data: bytes,
    ecu_bcm: UDSECU,
    ecu_wc: Optional[WCUDSECU],
    conn: socket.socket,
    addr: tuple,
) -> Optional[bytes]:
    if len(data) < 8:
        return None

    try:
        proto_ver, inv_ver, payload_type = struct.unpack(">BBH", data[:4])
        payload_len = struct.unpack(">I", data[4:8])[0]
    except struct.error:
        return None

    if proto_ver != DOIP_PROTOCOL_VERSION or inv_ver != DOIP_INVERSE_VERSION:
        return None

    if len(data) < 8 + payload_len:
        return None

    payload = data[8 : 8 + payload_len]

    # ------------------------------------------------------------------
    # Routing Activation (unchanged)
    # ------------------------------------------------------------------
    if payload_type == DOIP_ROUTING_ACTIVATION_REQUEST:
        logger.info(f"Routing activation request from {addr}")

        with ecu_bcm.state._lock:
            if ecu_bcm.state.client_connected and ecu_bcm.state.client_address != addr:
                response_code = 0x02
            else:
                ecu_bcm.state.client_connected = True
                ecu_bcm.state.client_address = addr
                ecu_bcm.state.routing_active = True
                response_code = 0x00

        response_payload = struct.pack(">H B", LOGICAL_ADDR_TESTER, response_code)
        header = build_doip_header(
            DOIP_ROUTING_ACTIVATION_RESPONSE, len(response_payload)
        )
        return header + response_payload

    # ------------------------------------------------------------------
    # Diagnostic Payload
    # ------------------------------------------------------------------
    elif payload_type == DOIP_PAYLOAD_DIAG:
        with ecu_bcm.state._lock:
            if not ecu_bcm.state.routing_active:
                return None

        if len(payload) < 4:
            return None

        source_addr = struct.unpack(">H", payload[:2])[0]
        target_addr = struct.unpack(">H", payload[2:4])[0]
        uds_payload = payload[4:]

        # ---- BCM ----
        if target_addr == LOGICAL_ADDR_ECU:
            with ecu_bcm.state._lock:
                if not ecu_bcm.state.rx_enabled:
                    return None

            logger.info(f"[←] BCM UDS from {addr}: {bytes_to_hex_str(uds_payload)}")
            uds_response = ecu_bcm.handle_uds(uds_payload)
            if uds_response is None:
                return None

            with ecu_bcm.state._lock:
                if not ecu_bcm.state.tx_enabled:
                    return None

            logger.info(f"[→] BCM response: {bytes_to_hex_str(uds_response)}")
            resp_payload = (
                struct.pack(">HH", LOGICAL_ADDR_ECU, source_addr) + uds_response
            )
            return build_doip_header(DOIP_PAYLOAD_DIAG, len(resp_payload)) + resp_payload

        # ---- WC ----
        elif target_addr == LOGICAL_ADDR_WC:
            # WC availability gated by BCM coding param F201
            with ecu_bcm.state._lock:
                # In pure client mode we simply pass through if WC instance exists
                pass

            if ecu_wc is None:
                return None

            logger.info(f"[←] WC UDS from {addr}: {bytes_to_hex_str(uds_payload)}")
            uds_response = ecu_wc.handle_uds(uds_payload)

            if uds_response is None:
                return None

            logger.info(f"[→] WC response: {bytes_to_hex_str(uds_response)}")
            resp_payload = (
                struct.pack(">HH", LOGICAL_ADDR_WC, source_addr) + uds_response
            )
            return build_doip_header(DOIP_PAYLOAD_DIAG, len(resp_payload)) + resp_payload

        else:
            return None

    return None


# =============================================================================
# TCP Server
# =============================================================================
def tcp_server() -> None:
    ecu_bcm = UDSECU()
    ecu_wc = WCUDSECU()

    # Monitoring stubs – no-ops in this version
    ecu_wc.start_monitoring()

    udp_thread = threading.Thread(target=udp_discovery_server, daemon=True)
    udp_thread.start()

    local_ip = get_local_ip()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server.bind(("0.0.0.0", 13400))
        server.listen(5)

        logger.info("=" * 60)
        logger.info("ECU DoIP Simulator – PURE PROTOCOL SKELETON")
        logger.info(f"TCP Server listening on {local_ip}:13400")
        logger.info("UDP Discovery server running on port 13400")
        logger.info("=" * 60)
        logger.info(
            "Supported UDS (BCM): 0x10, 0x11, 0x27, 0x28, 0x2E, 0x31, 0x3E  |  "
            "No-response: 0x14, 0x19, 0x22"
        )
        logger.info("=" * 60)

        while True:
            conn, addr = server.accept()
            logger.info(f"\nNew TCP connection from {addr}")

            with ecu_bcm.state._lock:
                if (
                    ecu_bcm.state.client_connected
                    and ecu_bcm.state.client_address != addr
                ):
                    logger.info(
                        f"Connection refused: another client already connected "
                        f"from {ecu_bcm.state.client_address}"
                    )
                    conn.close()
                    continue
                ecu_bcm.state.client_connected = True
                ecu_bcm.state.client_address = addr
                ecu_bcm.state.routing_active = False

            try:
                rx_buffer = b""

                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        logger.info(f"Connection closed by client {addr}")
                        break

                    rx_buffer += chunk

                    while True:
                        if len(rx_buffer) < 8:
                            break

                        try:
                            proto_ver, inv_ver, payload_type = struct.unpack(
                                ">BBH", rx_buffer[:4]
                            )
                            payload_len = struct.unpack(">I", rx_buffer[4:8])[0]
                        except struct.error:
                            logger.error(f"Malformed DoIP header from {addr}")
                            rx_buffer = b""
                            break

                        total_len = 8 + payload_len
                        if len(rx_buffer) < total_len:
                            break

                        frame = rx_buffer[:total_len]
                        rx_buffer = rx_buffer[total_len:]

                        response = handle_doip_message(
                            frame, ecu_bcm, ecu_wc, conn, addr
                        )
                        if response:
                            conn.send(response)

            except ConnectionResetError:
                logger.info(f"Connection lost with {addr}")
            except Exception as e:
                logger.error(f"Communication error with {addr}: {e}")
            finally:
                with ecu_bcm.state._lock:
                    ecu_bcm.state.client_connected = False
                    ecu_bcm.state.client_address = None
                    ecu_bcm.state.routing_active = False
                conn.close()
                logger.info(f"TCP connection closed with {addr}")

    except Exception as e:
        logger.error(f"TCP server error: {e}")
    finally:
        server.close()
        ecu_wc.stop_monitoring()


# =============================================================================
# Entry Point
# =============================================================================
if __name__ == "__main__":
    logging_config.setup_logging()
    try:
        tcp_server()
    except KeyboardInterrupt:
        logger.info("\n\nECU DoIP Simulator stopped by user")
