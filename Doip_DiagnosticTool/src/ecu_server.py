#!/usr/bin/env python3
"""
DoIP/UDS ECU Simulator for Raspberry Pi 3 (ISO 13400 / ISO 14229)

This program simulates a vehicle ECU that communicates via DoIP and implements
a subset of UDS services. It runs two servers:
  - UDP discovery server (broadcast) to respond to vehicle identification requests.
  - TCP diagnostic server for routing activation and UDS diagnostic payloads.

Behaviour is 100% identical to the original simulation. Only structure,
documentation and logging have been improved.
"""

import socket
import struct
import threading
import logging
from typing import Optional, Tuple
import logging_config

# Import shared DoIP protocol definitions
from doip_protocol import (
    DOIP_PROTOCOL_VERSION, DOIP_INVERSE_VERSION,
    DOIP_VEHICLE_IDENTIFICATION_REQUEST, DOIP_VEHICLE_IDENTIFICATION_RESPONSE,
    DOIP_ROUTING_ACTIVATION_REQUEST, DOIP_ROUTING_ACTIVATION_RESPONSE,
    DOIP_PAYLOAD_DIAG,
    LOGICAL_ADDR_TESTER, LOGICAL_ADDR_ECU,
    hex_str_to_bytes, bytes_to_hex_str,
    send_doip_message, receive_doip_message, build_doip_header
)

# -----------------------------------------------------------------------------
# Logging configuration (preserves original console output)
# -----------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# =============================================================================
# ECU Internal State (ISO 14229 simulation)
# =============================================================================
class ECUState:
    """
    Holds all dynamic state of the simulated ECU.
    This includes session, security level, DTCs, flash memory, DID storage,
    routine control, and routing status.
    """

    def __init__(self):
        # UDS session (default = 0x01)
        self.session = 0x01

        # Security access level (0 = locked, 1 = diagnostic, 2 = programming)
        self.security_level = 0

        # Simulated DTCs (DTC code -> status byte)
        self.dtc_list = {
            0x123456: 0x2F,
            0x654321: 0x2E,
            0x789ABC: 0x04,   # confirmed
            0xDEF012: 0x08    # pending
        }

        # Flash memory (used for download/upload simulation)
        self.flash_memory = bytearray()

        # Transfer state
        self.download_active = False
        self.upload_active = False
        self.expected_block = 1

        # Routine control
        self.routine_active = False
        self.routine_id = 0x0000
        self.routine_results = bytearray()

        # Data identifiers (DID -> static data)
        self.data_identifiers = {
            0xF190: b"\x56\x34\x12",           # VIN (3 bytes)
            0xF191: b"\x01\x02",               # Serial number (2 bytes)
            0xF192: b"\x00\x10\x20\x30",       # Software version (4 bytes)
            0xF193: b"\xAA\xBB\xCC\xDD\xEE\xFF", # Configuration (6 bytes)
            0xF194: b"\x11\x22\x33\x44\x55\x66\x77\x88"  # Extended data (8 bytes)
        }

        # Session timing parameters (P2, P2* in ms) – not used in logic except for responses
        self.session_timing = {
            0x01: (0x0032, 0x07D0),   # Default: 50ms, 2000ms
            0x02: (0x00C8, 0x0FA0),   # Programming: 200ms, 4000ms
            0x03: (0x0190, 0x1F40)    # Extended: 400ms, 8000ms
        }

        # Conditions for service 0x22 (simulated)
        self.ecu_busy = False
        self.sequence_required = False
        self.pending_responses = {}
        self.calibration_mode = False

        # DIDs that require specific session/security levels
        self.protected_dids = {
            0xF200: {"security": 2, "session": 2},   # programming DID
            0xF210: {"security": 1, "session": 1}    # diagnostic DID
        }

        # Routing state
        self.client_connected = False
        self.client_address = None
        self.client_logical_address = None
        self.routing_active = False

        # Thread safety lock (added for future-proofing, no behavioural change)
        self._lock = threading.RLock()


# =============================================================================
# UDS ECU Logic (ISO 14229-1)
# =============================================================================
class UDSECU:
    """Implements the UDS diagnostic services supported by the ECU simulator."""

    def __init__(self):
        self.state = ECUState()
        self._lock = self.state._lock  # alias for convenience

    # -------------------------------------------------------------------------
    # Helper methods for service 0x22 (read by identifier) – unchanged logic
    # -------------------------------------------------------------------------
    def _check_calibration_conditions(self) -> bool:
        """Simulate vehicle/ECU condition checks for calibration DIDs."""
        # Original behaviour: always returns True when calibration_mode is True or simply True.
        return self.state.calibration_mode or True

    def _is_high_priority_request(self, uds: bytes) -> bool:
        """Determine if a UDS request contains high‑priority DIDs (VIN, serial)."""
        high_priority_dids = [0xF190, 0xF191]
        if len(uds) < 3:
            return False
        i = 1
        while i < len(uds):
            if i + 1 >= len(uds):
                break
            did = (uds[i] << 8) | uds[i + 1]
            if did in high_priority_dids:
                return True
            i += 2
        return False

    def _check_request_sequence(self, uds: bytes) -> bool:
        """Simulate request sequence verification (always accepts)."""
        return True

    def _is_data_available(self, did: int) -> bool:
        """Simulate data availability (always true)."""
        return True

    # -------------------------------------------------------------------------
    # Main UDS dispatcher
    # -------------------------------------------------------------------------
    def handle_uds(self, uds: bytes) -> bytes:
        """
        Process a UDS request and return the UDS response.

        Args:
            uds: UDS request payload (without DoIP header/addresses).

        Returns:
            UDS response bytes (positive or negative).
        """
        with self._lock:   # ensure thread safety (currently single-threaded, but harmless)
            return self._handle_uds_locked(uds)

    def _handle_uds_locked(self, uds: bytes) -> bytes:
        """Internal method, called with state lock held."""
        if len(uds) == 0:
            return bytes([0x7F, 0x00, 0x13])   # NRC: incorrect message length

        sid = uds[0]

        # ---------------------------------------------------------------------
        # 0x10: Diagnostic Session Control
        # ---------------------------------------------------------------------
        if sid == 0x10:
            if len(uds) < 2:
                return bytes([0x7F, 0x10, 0x13])   # NRC: incorrect length

            subfunction = uds[1]
            if subfunction not in [0x01, 0x02, 0x03]:
                return bytes([0x7F, 0x10, 0x12])   # NRC: subfunction not supported

            if subfunction == 0x02 and self.state.security_level < 2:
                return bytes([0x7F, 0x10, 0x33])   # NRC: security access denied
            if subfunction == 0x03 and self.state.security_level < 1:
                return bytes([0x7F, 0x10, 0x33])

            self.state.session = subfunction
            p2, p2_star = self.state.session_timing.get(subfunction, (0x0032, 0x07D0))
            response = bytearray([0x50, subfunction])
            response.extend(p2.to_bytes(2, 'big'))
            response.extend(p2_star.to_bytes(2, 'big'))
            return bytes(response)

        # ---------------------------------------------------------------------
        # 0x11: ECU Reset
        # ---------------------------------------------------------------------
        if sid == 0x11:
            if len(uds) < 2:
                return bytes([0x7F, 0x11, 0x13])

            reset_type = uds[1]
            if reset_type not in [0x01, 0x02, 0x03, 0x04]:
                return bytes([0x7F, 0x11, 0x12])

            if reset_type == 0x01:   # Hard Reset
                old_dtc = self.state.dtc_list.copy()
                old_data = self.state.data_identifiers.copy()
                self.state = ECUState()
                self.state.dtc_list = old_dtc
                self.state.data_identifiers = old_data
                return bytes([0x51, 0x01])

            elif reset_type == 0x02: # Soft Reset
                self.state.session = 0x01
                self.state.security_level = 0
                self.state.download_active = False
                self.state.upload_active = False
                self.state.routine_active = False
                return bytes([0x51, 0x02])

            elif reset_type == 0x03: # Electrical restart
                self.state.session = 0x01
                self.state.security_level = 0
                return bytes([0x51, 0x03])

            else:                   # 0x04 or other supported value
                return bytes([0x51, reset_type])

        # ---------------------------------------------------------------------
        # 0x14: Clear Diagnostic Information
        # ---------------------------------------------------------------------
        if sid == 0x14:
            if len(uds) < 4:
                return bytes([0x7F, 0x14, 0x13])

            group_of_dtc = int.from_bytes(uds[1:4], 'big')
            if group_of_dtc == 0x000000 or group_of_dtc == 0xFFFFFF:
                self.state.dtc_list.clear()
            else:
                group_mask = (group_of_dtc >> 16) & 0xFF
                dtcs_to_remove = [dtc for dtc in self.state.dtc_list if ((dtc >> 16) & 0xFF) == group_mask]
                for dtc in dtcs_to_remove:
                    del self.state.dtc_list[dtc]

            return bytes([0x54])

        # ---------------------------------------------------------------------
        # 0x19: Read DTC Information
        # ---------------------------------------------------------------------
        if sid == 0x19:
            if len(uds) < 2:
                return bytes([0x7F, 0x19, 0x13])

            subfunction = uds[1]

            # 0x01: Report DTC by Status Mask
            if subfunction == 0x01:
                if len(uds) < 3:
                    return bytes([0x7F, 0x19, 0x13])
                status_mask = uds[2]
                response = bytearray([0x59, 0x01, status_mask])
                dtc_count = 0
                for dtc_code, dtc_status in self.state.dtc_list.items():
                    if dtc_status & status_mask:
                        response.extend(dtc_code.to_bytes(3, 'big'))
                        response.append(dtc_status)
                        dtc_count += 1
                if dtc_count == 0:
                    response.extend(b'\x00\x00\x00\x00')
                return bytes(response)

            # 0x02: Report DTC Snapshot Identification
            elif subfunction == 0x02:
                if len(uds) < 6:
                    return bytes([0x7F, 0x19, 0x13])
                dtc_code = int.from_bytes(uds[2:5], 'big')
                snapshot_record_number = uds[5]
                if dtc_code not in self.state.dtc_list:
                    return bytes([0x7F, 0x19, 0x31])
                response = bytearray([0x59, 0x02])
                response.extend(dtc_code.to_bytes(3, 'big'))
                response.append(snapshot_record_number)
                snapshot_data = bytes([snapshot_record_number, 0x11, 0x22, 0x33, 0x44])
                response.extend(snapshot_data)
                return bytes(response)

            # 0x03: Report DTC Snapshot Record
            elif subfunction == 0x03:
                if len(uds) < 6:
                    return bytes([0x7F, 0x19, 0x13])
                dtc_code = int.from_bytes(uds[2:5], 'big')
                snapshot_record_number = uds[5]
                if dtc_code not in self.state.dtc_list:
                    return bytes([0x7F, 0x19, 0x31])
                response = bytearray([0x59, 0x03])
                response.extend(dtc_code.to_bytes(3, 'big'))
                response.append(snapshot_record_number)
                snapshot_record_data = bytes([0x00, 0x00, 0x10, 0x00, 0x13, 0x88, 0x50, 0x4B, 0x0B, 0xB8])
                response.extend(snapshot_record_data)
                return bytes(response)

            # 0x04: Report DTC Extended Data Record
            elif subfunction == 0x04:
                if len(uds) < 5:
                    return bytes([0x7F, 0x19, 0x13])
                dtc_code = int.from_bytes(uds[2:5], 'big')
                if dtc_code not in self.state.dtc_list:
                    return bytes([0x7F, 0x19, 0x31])
                response = bytearray([0x59, 0x04])
                response.extend(dtc_code.to_bytes(3, 'big'))
                extended_data = bytes([0x01, 0x03, 0x00, 0x00, 0x20, 0x00, 0x00, 0x00, 0x40, 0x00, 0x2A, 0x0C])
                response.extend(extended_data)
                return bytes(response)

            # 0x06: Report Extended Data by DTC
            elif subfunction == 0x06:
                if len(uds) < 6:
                    return bytes([0x7F, 0x19, 0x13])
                dtc_code = int.from_bytes(uds[2:5], 'big')
                extended_data_record_number = uds[5]
                if dtc_code not in self.state.dtc_list:
                    return bytes([0x7F, 0x19, 0x31])
                response = bytearray([0x59, 0x06])
                response.extend(dtc_code.to_bytes(3, 'big'))
                response.append(extended_data_record_number)
                if extended_data_record_number == 0x01:
                    extended_data = b'\x01\x02\x03\x04\x05'
                elif extended_data_record_number == 0x02:
                    extended_data = b'\x0A\x0B\x0C\x0D\x0E'
                else:
                    extended_data = b'\x00\x00\x00\x00\x00'
                response.extend(extended_data)
                return bytes(response)

            # 0x07: Report Number of DTC by Severity Mask
            elif subfunction == 0x07:
                if len(uds) < 3:
                    return bytes([0x7F, 0x19, 0x13])
                severity_mask = uds[2]
                severity_count = sum(1 for status in self.state.dtc_list.values() if status & severity_mask)
                response = bytearray([0x59, 0x07, severity_mask])
                response.extend(severity_count.to_bytes(2, 'big'))
                return bytes(response)

            # 0x08: Report DTC Fault Detection Counter
            elif subfunction == 0x08:
                if len(uds) < 5:
                    return bytes([0x7F, 0x19, 0x13])
                dtc_code = int.from_bytes(uds[2:5], 'big')
                if dtc_code not in self.state.dtc_list:
                    return bytes([0x7F, 0x19, 0x31])
                fault_detection_counter = 0x05
                response = bytearray([0x59, 0x08])
                response.extend(dtc_code.to_bytes(3, 'big'))
                response.append(fault_detection_counter)
                return bytes(response)

            # 0x09: Report DTC With Permanent Status
            elif subfunction == 0x09:
                response = bytearray([0x59, 0x09])
                permanent_status_mask = 0x08
                permanent_count = 0
                for dtc_code, dtc_status in self.state.dtc_list.items():
                    if dtc_status & permanent_status_mask:
                        response.extend(dtc_code.to_bytes(3, 'big'))
                        response.append(dtc_status)
                        permanent_count += 1
                if permanent_count == 0:
                    response.extend(b'\x00\x00\x00\x00')
                return bytes(response)

            # 0x0A: Report Number of DTC by Status Mask
            elif subfunction == 0x0A:
                if len(uds) < 3:
                    return bytes([0x7F, 0x19, 0x13])
                status_mask = uds[2]
                dtc_count = sum(1 for status in self.state.dtc_list.values() if status & status_mask)
                response = bytearray([0x59, 0x0A, status_mask])
                response.extend(dtc_count.to_bytes(2, 'big'))
                return bytes(response)

            # 0x0B: Report DTC by Severity Mask
            elif subfunction == 0x0B:
                if len(uds) < 3:
                    return bytes([0x7F, 0x19, 0x13])
                severity_mask = uds[2]
                response = bytearray([0x59, 0x0B, severity_mask])
                severity_count = 0
                for dtc_code, dtc_status in self.state.dtc_list.items():
                    severity_level = dtc_status & 0x07
                    if severity_level & severity_mask:
                        response.extend(dtc_code.to_bytes(3, 'big'))
                        response.append(severity_level)
                        severity_count += 1
                if severity_count == 0:
                    response.extend(b'\x00\x00\x00\x00')
                return bytes(response)

            # 0x0C: Report DTC Severity Information
            elif subfunction == 0x0C:
                if len(uds) < 5:
                    return bytes([0x7F, 0x19, 0x13])
                dtc_code = int.from_bytes(uds[2:5], 'big')
                if dtc_code not in self.state.dtc_list:
                    return bytes([0x7F, 0x19, 0x31])
                response = bytearray([0x59, 0x0C])
                response.extend(dtc_code.to_bytes(3, 'big'))
                severity_info = bytes([0x02, 0x01, 0x03, 0x80, 0x00, 0x0A])
                response.extend(severity_info)
                return bytes(response)

            # 0x0D: Report Emission-Related DTC
            elif subfunction == 0x0D:
                response = bytearray([0x59, 0x0D])
                emission_count = 0
                for dtc_code, dtc_status in self.state.dtc_list.items():
                    if (dtc_code & 0xFF0000) == 0x010000:
                        response.extend(dtc_code.to_bytes(3, 'big'))
                        response.append(dtc_status)
                        emission_count += 1
                if emission_count == 0:
                    response.extend(b'\x00\x00\x00\x00')
                return bytes(response)

            # 0x0E: Report Emission-Related DTC by Status Mask
            elif subfunction == 0x0E:
                if len(uds) < 3:
                    return bytes([0x7F, 0x19, 0x13])
                status_mask = uds[2]
                response = bytearray([0x59, 0x0E, status_mask])
                emission_count = 0
                for dtc_code, dtc_status in self.state.dtc_list.items():
                    if (dtc_code & 0xFF0000) == 0x010000 and (dtc_status & status_mask):
                        response.extend(dtc_code.to_bytes(3, 'big'))
                        response.append(dtc_status)
                        emission_count += 1
                if emission_count == 0:
                    response.extend(b'\x00\x00\x00\x00')
                return bytes(response)

            else:
                return bytes([0x7F, 0x19, 0x12])   # subfunction not supported

        # ---------------------------------------------------------------------
        # 0x22: Read Data by Identifier
        # ---------------------------------------------------------------------
        if sid == 0x22:
            if len(uds) < 3:
                return bytes([0x7F, 0x22, 0x13])
            if (len(uds) - 1) % 2 != 0:
                return bytes([0x7F, 0x22, 0x13])

            # Session check
            i = 1
            while i < len(uds):
                if i + 1 >= len(uds):
                    break
                did = (uds[i] << 8) | uds[i + 1]
                if did in self.state.protected_dids:
                    required_session = self.state.protected_dids[did].get("session", 1)
                    if self.state.session < required_session:
                        return bytes([0x7F, 0x22, 0x7E])
                i += 2

            # Security check
            i = 1
            while i < len(uds):
                if i + 1 >= len(uds):
                    break
                did = (uds[i] << 8) | uds[i + 1]
                if did in self.state.protected_dids:
                    required_security = self.state.protected_dids[did].get("security", 0)
                    if self.state.security_level < required_security:
                        return bytes([0x7F, 0x22, 0x33])
                i += 2

            # Condition checks
            i = 1
            while i < len(uds):
                if i + 1 >= len(uds):
                    break
                did = (uds[i] << 8) | uds[i + 1]
                if did in [0xF300, 0xF301] and not self._check_calibration_conditions():
                    return bytes([0x7F, 0x22, 0x22])
                i += 2

            # ECU busy check
            if self.state.ecu_busy and not self._is_high_priority_request(uds):
                return bytes([0x7F, 0x22, 0x21])

            # Sequence check
            if self.state.sequence_required and not self._check_request_sequence(uds):
                return bytes([0x7F, 0x22, 0x24])

            # Data availability check
            i = 1
            while i < len(uds):
                if i + 1 >= len(uds):
                    break
                did = (uds[i] << 8) | uds[i + 1]
                if did in [0xF400, 0xF401] and not self._is_data_available(did):
                    return bytes([0x7F, 0x22, 0x78])
                i += 2

            # Process reads
            response = bytearray([0x62])
            i = 1
            while i < len(uds):
                if i + 1 >= len(uds):
                    break
                did_high = uds[i]
                did_low = uds[i + 1]
                did = (did_high << 8) | did_low

                if did in self.state.data_identifiers:
                    response.append(did_high)
                    response.append(did_low)
                    data_entry = self.state.data_identifiers[did]
                    if callable(data_entry):
                        data = data_entry()
                    elif isinstance(data_entry, bytes):
                        data = data_entry
                    elif isinstance(data_entry, dict):
                        data = data_entry.get('data', b'')
                    else:
                        data = b''
                    response.extend(data)
                else:
                    return bytes([0x7F, 0x22, 0x31])   # request out of range
                i += 2
            return bytes(response)

        # ---------------------------------------------------------------------
        # 0x27: Security Access
        # ---------------------------------------------------------------------
        if sid == 0x27:
            if len(uds) < 2:
                return bytes([0x7F, 0x27, 0x13])
            subfunction = uds[1]
            if subfunction == 0x00 or subfunction > 0x7F:
                return bytes([0x7F, 0x27, 0x12])

            if subfunction % 2 == 1:   # Request Seed
                security_level = (subfunction + 1) // 2
                if security_level == 1:
                    seed = b'\xAA\x55\xCC\x33'
                elif security_level == 2:
                    seed = b'\x11\x22\x33\x44'
                else:
                    seed = b'\xDE\xAD\xBE\xEF'
                response = bytearray([0x67, subfunction])
                response.extend(seed)
                return bytes(response)
            else:                     # Send Key
                if len(uds) < 6:
                    return bytes([0x7F, 0x27, 0x13])
                security_level = subfunction // 2
                received_key = uds[2:]
                if security_level == 1:
                    expected_key = b'\x33\xCC\x55\xAA'
                    if received_key == expected_key:
                        self.state.security_level = max(self.state.security_level, 1)
                        return bytes([0x67, subfunction])
                elif security_level == 2:
                    expected_key = b'\x44\x33\x22\x11'
                    if received_key == expected_key:
                        self.state.security_level = max(self.state.security_level, 2)
                        return bytes([0x67, subfunction])
                return bytes([0x7F, 0x27, 0x35])   # invalid key

        # ---------------------------------------------------------------------
        # 0x31: Routine Control
        # ---------------------------------------------------------------------
        if sid == 0x31:
            if len(uds) < 4:
                return bytes([0x7F, 0x31, 0x13])
            subfunction = uds[1]
            routine_id = int.from_bytes(uds[2:4], 'big')
            if subfunction not in [0x01, 0x02, 0x03]:
                return bytes([0x7F, 0x31, 0x12])
            supported_routines = [0x0200, 0x0201, 0x0202, 0x0203, 0xFF00, 0xFF01]
            if routine_id not in supported_routines:
                return bytes([0x7F, 0x31, 0x31])

            if subfunction == 0x01:   # Start Routine
                if self.state.routine_active:
                    return bytes([0x7F, 0x31, 0x22])
                self.state.routine_active = True
                self.state.routine_id = routine_id
                self.state.routine_results = bytearray()
                if routine_id == 0x0202:
                    self.state.routine_results.extend(b'\x00\x64')
                elif routine_id == 0xFF00:
                    self.state.routine_results.extend(b'\xCA\xFE\xBA\xBE')
                else:
                    self.state.routine_results.extend(b'\x00\x00')
                response = bytearray([0x71, 0x01])
                response.extend(routine_id.to_bytes(2, 'big'))
                if len(uds) > 4:
                    response.extend(uds[4:])
                return bytes(response)

            elif subfunction == 0x02: # Stop Routine
                if not self.state.routine_active or self.state.routine_id != routine_id:
                    return bytes([0x7F, 0x31, 0x24])
                self.state.routine_active = False
                return bytes([0x71, 0x02]) + routine_id.to_bytes(2, 'big')

            else:                    # Request Routine Results (0x03)
                if not self.state.routine_active or self.state.routine_id != routine_id:
                    return bytes([0x7F, 0x31, 0x31])
                response = bytearray([0x71, 0x03])
                response.extend(routine_id.to_bytes(2, 'big'))
                response.extend(self.state.routine_results)
                return bytes(response)

        # ---------------------------------------------------------------------
        # 0x34: Request Download
        # ---------------------------------------------------------------------
        if sid == 0x34:
            if self.state.session != 0x02:
                return bytes([0x7F, 0x34, 0x7E])
            if self.state.security_level < 2:
                return bytes([0x7F, 0x34, 0x33])
            if len(uds) < 5:
                return bytes([0x7F, 0x34, 0x13])

            data_format = uds[1]
            address_length_format = uds[2]
            address_size = (address_length_format >> 4) & 0x0F
            memory_size = address_length_format & 0x0F
            if address_size == 0 or memory_size == 0:
                return bytes([0x7F, 0x34, 0x13])

            expected_length = 3 + address_size + memory_size
            if len(uds) < expected_length:
                return bytes([0x7F, 0x34, 0x13])

            address_start = 3
            address_end = address_start + address_size
            memory_start = address_end
            memory_end = memory_start + memory_size

            address = int.from_bytes(uds[address_start:address_end], 'big')
            memory_size_bytes = int.from_bytes(uds[memory_start:memory_end], 'big')

            if address > 0xFFFFFFFF:
                return bytes([0x7F, 0x34, 0x31])

            self.state.flash_memory = bytearray()
            self.state.download_active = True
            self.state.upload_active = False
            self.state.expected_block = 1

            max_block_length = 0x08
            length_format = 0x20
            response = bytearray([0x74, length_format])
            response.extend(max_block_length.to_bytes(2, 'big'))
            return bytes(response)

        # ---------------------------------------------------------------------
        # 0x35: Request Upload
        # ---------------------------------------------------------------------
        if sid == 0x35:
            if self.state.security_level < 1:
                return bytes([0x7F, 0x35, 0x33])
            if len(uds) < 5:
                return bytes([0x7F, 0x35, 0x13])

            data_format = uds[1]
            address_length_format = uds[2]
            address_size = (address_length_format >> 4) & 0x0F
            memory_size = address_length_format & 0x0F
            if address_size == 0 or memory_size == 0:
                return bytes([0x7F, 0x35, 0x13])

            expected_length = 3 + address_size + memory_size
            if len(uds) < expected_length:
                return bytes([0x7F, 0x35, 0x13])

            address_start = 3
            address_end = address_start + address_size
            memory_start = address_end
            memory_end = memory_start + memory_size

            address = int.from_bytes(uds[address_start:address_end], 'big')
            requested_size = int.from_bytes(uds[memory_start:memory_end], 'big')

            if address >= len(self.state.flash_memory):
                return bytes([0x7F, 0x35, 0x31])

            available_size = min(requested_size, len(self.state.flash_memory) - address)

            self.state.upload_active = True
            self.state.download_active = False
            self.state.expected_block = 1

            length_format = 0x20
            response = bytearray([0x75, length_format])
            response.extend(available_size.to_bytes(2, 'big'))
            return bytes(response)

        # ---------------------------------------------------------------------
        # 0x36: Transfer Data
        # ---------------------------------------------------------------------
        if sid == 0x36:
            if len(uds) < 2:
                return bytes([0x7F, 0x36, 0x13])
            block_counter = uds[1]

            if not self.state.download_active and not self.state.upload_active:
                return bytes([0x7F, 0x36, 0x24])

            if self.state.download_active:
                if block_counter != self.state.expected_block:
                    return bytes([0x7F, 0x36, 0x73])
                data = uds[2:] if len(uds) > 2 else b''
                if len(data) > 8:
                    return bytes([0x7F, 0x36, 0x13])
                self.state.flash_memory.extend(data)
                self.state.expected_block += 1
                return bytes([0x76, block_counter])

            else:   # upload active
                if block_counter != self.state.expected_block:
                    return bytes([0x7F, 0x36, 0x73])
                block_size = 8
                start_idx = (block_counter - 1) * block_size
                end_idx = start_idx + block_size
                if start_idx >= len(self.state.flash_memory):
                    return bytes([0x7F, 0x36, 0x31])
                data = self.state.flash_memory[start_idx:end_idx]
                self.state.expected_block += 1
                response = bytearray([0x76, block_counter])
                response.extend(data)
                return bytes(response)

        # ---------------------------------------------------------------------
        # 0x37: Transfer Exit
        # ---------------------------------------------------------------------
        if sid == 0x37:
            if not self.state.download_active and not self.state.upload_active:
                return bytes([0x7F, 0x37, 0x24])
            self.state.download_active = False
            self.state.upload_active = False
            self.state.expected_block = 1
            return bytes([0x77])

        # ---------------------------------------------------------------------
        # Service not supported
        # ---------------------------------------------------------------------
        return bytes([0x7F, sid, 0x11])


# =============================================================================
# Network Helpers
# =============================================================================
def get_local_ip() -> str:
    """
    Obtain the local IP address of the machine (Raspberry Pi).

    Returns:
        IP address string, or "127.0.0.1" on failure.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# =============================================================================
# UDP Discovery Server
# =============================================================================
def udp_discovery_server() -> None:
    """
    UDP server that responds to DoIP vehicle identification requests.
    Runs in a separate thread.
    """
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
                if (proto_ver == DOIP_PROTOCOL_VERSION and
                    inv_ver == DOIP_INVERSE_VERSION and
                    payload_type == DOIP_VEHICLE_IDENTIFICATION_REQUEST):
                    logger.info(f"Discovery request received from {client_addr}")

                    vin = b"VIN12345678901234"               # 17 bytes VIN
                    logical_address = LOGICAL_ADDR_ECU.to_bytes(2, 'big')
                    eid = b"\x00\x00\x00\x00\x00\x01"       # entity ID
                    gid = b"\x00\x00\x00\x00\x00\x02"       # group ID
                    ip_bytes = socket.inet_aton(get_local_ip())
                    payload = vin + logical_address + eid + gid + ip_bytes

                    header = build_doip_header(DOIP_VEHICLE_IDENTIFICATION_RESPONSE, len(payload))
                    response = header + payload
                    udp_sock.sendto(response, client_addr)
                    logger.info(f"Discovery response sent to {client_addr}")
    except Exception as e:
        logger.error(f"UDP discovery server error: {e}")
    finally:
        udp_sock.close()


# =============================================================================
# DoIP Message Handler (TCP)
# =============================================================================
def handle_doip_message(data: bytes, ecu: UDSECU, conn: socket.socket, addr: tuple) -> Optional[bytes]:
    """
    Process a DoIP message received over TCP.

    Args:
        data: Raw message bytes.
        ecu: UDSECU instance holding the ECU state.
        conn: TCP connection socket.
        addr: Client address.

    Returns:
        Response bytes to send, or None if no response should be sent.
    """
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

    payload = data[8:8 + payload_len]

    # -------------------------------------------------------------------------
    # Routing Activation Request
    # -------------------------------------------------------------------------
    if payload_type == DOIP_ROUTING_ACTIVATION_REQUEST:
        logger.info(f"Routing activation request received from {addr}")

        with ecu.state._lock:
            if ecu.state.client_connected and ecu.state.client_address != addr:
                response_code = 0x02   # already connected to another tester
            else:
                ecu.state.client_connected = True
                ecu.state.client_address = addr
                ecu.state.routing_active = True
                response_code = 0x00   # success

        response_payload = struct.pack(">H B", LOGICAL_ADDR_TESTER, response_code)
        header = build_doip_header(DOIP_ROUTING_ACTIVATION_RESPONSE, len(response_payload))
        return header + response_payload

    # -------------------------------------------------------------------------
    # Diagnostic Payload
    # -------------------------------------------------------------------------
    elif payload_type == DOIP_PAYLOAD_DIAG:
        with ecu.state._lock:
            if not ecu.state.routing_active:
                return None

        if len(payload) < 4:
            return None

        source_addr = struct.unpack(">H", payload[:2])[0]
        target_addr = struct.unpack(">H", payload[2:4])[0]

        if target_addr != LOGICAL_ADDR_ECU:
            return None

        uds_payload = payload[4:]

        logger.info(f"[←] Received UDS from {addr}: {bytes_to_hex_str(uds_payload)}")

        uds_response = ecu.handle_uds(uds_payload)

        logger.info(f"[→] Response UDS: {bytes_to_hex_str(uds_response)}")

        response_payload = struct.pack(">HH", LOGICAL_ADDR_ECU, source_addr) + uds_response
        header = build_doip_header(DOIP_PAYLOAD_DIAG, len(response_payload))
        return header + response_payload

    else:
        return None


# =============================================================================
# TCP Server (Main Diagnostic Interface)
# =============================================================================
def tcp_server() -> None:
    """
    Main TCP server for DoIP diagnostic communication.
    """
    ecu = UDSECU()

    # Start UDP discovery thread
    udp_thread = threading.Thread(target=udp_discovery_server, daemon=True)
    udp_thread.start()

    local_ip = get_local_ip()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server.bind((local_ip, 13400))
        server.listen(5)

        logger.info("=" * 60)
        logger.info("ECU DoIP Simulator with Routing Functionality")
        logger.info(f"TCP Server listening on {local_ip}:13400")
        logger.info("UDP Discovery server running on port 13400")
        logger.info("=" * 60)
        logger.info("Supported UDS Services: 0x10, 0x11, 0x14, 0x19, 0x22, 0x27, 0x31, 0x34, 0x35, 0x36, 0x37")
        logger.info("=" * 60)

        while True:
            conn, addr = server.accept()
            logger.info(f"\nNew TCP connection from {addr}")

            with ecu.state._lock:
                if ecu.state.client_connected and ecu.state.client_address != addr:
                    logger.info(f"Connection refused: another client already connected from {ecu.state.client_address}")
                    conn.close()
                    continue

                ecu.state.client_connected = True
                ecu.state.client_address = addr
                ecu.state.routing_active = False

            try:
                while True:
                    data = conn.recv(4096)
                    if not data:
                        logger.info(f"Connection closed by client {addr}")
                        break

                    response = handle_doip_message(data, ecu, conn, addr)
                    if response:
                        conn.send(response)

            except ConnectionResetError:
                logger.info(f"Connection lost with {addr}")
            except Exception as e:
                logger.error(f"Communication error with {addr}: {e}")
            finally:
                with ecu.state._lock:
                    ecu.state.client_connected = False
                    ecu.state.client_address = None
                    ecu.state.routing_active = False
                conn.close()
                logger.info(f"TCP connection closed with {addr}")

    except Exception as e:
        logger.error(f"TCP server error: {e}")
    finally:
        server.close()


# =============================================================================
# Entry Point
# =============================================================================
if __name__ == "__main__":
    logging_config.setup_logging()
    try:
        tcp_server()
    except KeyboardInterrupt:
        logger.info("\n\nECU DoIP Simulator stopped by user")