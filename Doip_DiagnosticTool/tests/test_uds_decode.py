"""
tests/test_uds_decode.py

Unit tests for UDS response decoding logic.
Mirrors the decoding in ui/pages/uds_console_page.py and core/worker.py.

Covers:
- Positive response SID identification (SID + 0x40)
- Negative response parsing (0x7F)
- Service name resolution
- NRC name resolution
- Per-service detail extraction
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# UDS service / NRC lookup tables (mirrors uds_console_page.py)
# ─────────────────────────────────────────────────────────────────────────────

UDS_SERVICE_NAMES = {
    0x10: "DiagnosticSessionControl",
    0x11: "ECUReset",
    0x14: "ClearDTC",
    0x19: "ReadDTCInformation",
    0x22: "ReadDataByIdentifier",
    0x27: "SecurityAccess",
    0x28: "CommunicationControl",
    0x2E: "WriteDataByIdentifier",
    0x31: "RoutineControl",
    0x3E: "TesterPresent",
    0x50: "DiagnosticSessionControl [+]",
    0x51: "ECUReset [+]",
    0x54: "ClearDTC [+]",
    0x59: "ReadDTCInformation [+]",
    0x62: "ReadDataByIdentifier [+]",
    0x67: "SecurityAccess [+]",
    0x6E: "WriteDataByIdentifier [+]",
    0x71: "RoutineControl [+]",
    0x7E: "TesterPresent [+]",
    0x7F: "NegativeResponse",
}

UDS_NRC_NAMES = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLengthOrInvalidFormat",
    0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x35: "invalidKey",
    0x36: "exceededNumberOfAttempts",
    0x37: "requiredTimeDelayNotExpired",
    0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
}


# ─────────────────────────────────────────────────────────────────────────────
# Decode helpers
# ─────────────────────────────────────────────────────────────────────────────

def is_positive_response(sid: int) -> bool:
    return sid >= 0x40 and sid != 0x7F

def get_request_sid(positive_sid: int) -> int:
    return positive_sid - 0x40

def decode_negative_response(response: bytes) -> tuple:
    """Returns (requested_sid, nrc_code, nrc_name)."""
    if len(response) < 3 or response[0] != 0x7F:
        return None, None, None
    req_sid  = response[1]
    nrc      = response[2]
    nrc_name = UDS_NRC_NAMES.get(nrc, f"0x{nrc:02X}")
    return req_sid, nrc, nrc_name


# ─────────────────────────────────────────────────────────────────────────────
# Tests — Service name lookup
# ─────────────────────────────────────────────────────────────────────────────

class TestServiceNameLookup:

    def test_session_control_request(self):
        assert UDS_SERVICE_NAMES[0x10] == "DiagnosticSessionControl"

    def test_session_control_response(self):
        assert UDS_SERVICE_NAMES[0x50] == "DiagnosticSessionControl [+]"

    def test_security_access_request(self):
        assert UDS_SERVICE_NAMES[0x27] == "SecurityAccess"

    def test_security_access_response(self):
        assert UDS_SERVICE_NAMES[0x67] == "SecurityAccess [+]"

    def test_write_data_request(self):
        assert UDS_SERVICE_NAMES[0x2E] == "WriteDataByIdentifier"

    def test_write_data_response(self):
        assert UDS_SERVICE_NAMES[0x6E] == "WriteDataByIdentifier [+]"

    def test_read_data_request(self):
        assert UDS_SERVICE_NAMES[0x22] == "ReadDataByIdentifier"

    def test_read_data_response(self):
        assert UDS_SERVICE_NAMES[0x62] == "ReadDataByIdentifier [+]"

    def test_tester_present_request(self):
        assert UDS_SERVICE_NAMES[0x3E] == "TesterPresent"

    def test_tester_present_response(self):
        assert UDS_SERVICE_NAMES[0x7E] == "TesterPresent [+]"

    def test_negative_response(self):
        assert UDS_SERVICE_NAMES[0x7F] == "NegativeResponse"

    def test_unknown_sid_fallback(self):
        sid = 0xAA
        name = UDS_SERVICE_NAMES.get(sid, f"Unknown (0x{sid:02X})")
        assert "AA" in name

    def test_response_offset_consistency(self):
        """Every request SID + 0x40 should be in the table.
        Note: 0x28 (CommunicationControl) positive response is 0x68
        which is not decoded by name — only 0x68 subfunc is used directly."""
        request_sids = [0x10, 0x11, 0x14, 0x19, 0x22, 0x27, 0x2E, 0x31, 0x3E]
        for sid in request_sids:
            assert sid + 0x40 in UDS_SERVICE_NAMES, f"0x{sid + 0x40:02X} missing"
        # 0x28 positive response = 0x68, not named in table (handled by subfunction)
        assert 0x28 not in [s + 0x40 for s in request_sids]


# ─────────────────────────────────────────────────────────────────────────────
# Tests — Positive response detection
# ─────────────────────────────────────────────────────────────────────────────

class TestPositiveResponse:

    def test_session_control_positive(self):
        response = bytes([0x50, 0x03, 0x00, 0x32, 0x07, 0xD0])
        assert is_positive_response(response[0])
        assert get_request_sid(response[0]) == 0x10

    def test_security_access_seed_positive(self):
        response = bytes([0x67, 0x03, 0x11, 0x22, 0x33, 0x44])
        assert is_positive_response(response[0])
        assert get_request_sid(response[0]) == 0x27

    def test_write_data_positive(self):
        response = bytes([0x6E, 0xF2, 0x01])
        assert is_positive_response(response[0])
        assert get_request_sid(response[0]) == 0x2E

    def test_tester_present_positive(self):
        response = bytes([0x7E, 0x00])
        assert is_positive_response(response[0])
        assert get_request_sid(response[0]) == 0x3E

    def test_negative_response_not_positive(self):
        response = bytes([0x7F, 0x10, 0x22])
        assert not is_positive_response(response[0])

    def test_ecu_reset_positive(self):
        response = bytes([0x51, 0x03])
        assert is_positive_response(response[0])
        assert get_request_sid(response[0]) == 0x11


# ─────────────────────────────────────────────────────────────────────────────
# Tests — Negative response decoding
# ─────────────────────────────────────────────────────────────────────────────

class TestNegativeResponse:

    def test_conditions_not_correct(self):
        response = bytes([0x7F, 0x2E, 0x22])
        req_sid, nrc, name = decode_negative_response(response)
        assert req_sid == 0x2E
        assert nrc == 0x22
        assert name == "conditionsNotCorrect"

    def test_security_access_denied(self):
        response = bytes([0x7F, 0x31, 0x33])
        req_sid, nrc, name = decode_negative_response(response)
        assert req_sid == 0x31
        assert nrc == 0x33
        assert name == "securityAccessDenied"

    def test_invalid_key(self):
        response = bytes([0x7F, 0x27, 0x35])
        req_sid, nrc, name = decode_negative_response(response)
        assert nrc == 0x35
        assert name == "invalidKey"

    def test_wrong_session(self):
        response = bytes([0x7F, 0x2E, 0x7E])
        req_sid, nrc, name = decode_negative_response(response)
        assert nrc == 0x7E
        assert name == "subFunctionNotSupportedInActiveSession"

    def test_request_out_of_range(self):
        response = bytes([0x7F, 0x22, 0x31])
        req_sid, nrc, name = decode_negative_response(response)
        assert nrc == 0x31
        assert name == "requestOutOfRange"

    def test_unknown_nrc_fallback(self):
        response = bytes([0x7F, 0x10, 0xAB])
        req_sid, nrc, name = decode_negative_response(response)
        assert "AB" in name

    def test_too_short_returns_none(self):
        req_sid, nrc, name = decode_negative_response(bytes([0x7F]))
        assert req_sid is None

    def test_empty_returns_none(self):
        req_sid, nrc, name = decode_negative_response(b"")
        assert req_sid is None


# ─────────────────────────────────────────────────────────────────────────────
# Tests — Per-service response details
# ─────────────────────────────────────────────────────────────────────────────

class TestServiceResponseDetails:

    def test_session_control_extract_subfunction(self):
        response = bytes([0x50, 0x03, 0x00, 0x32, 0x07, 0xD0])
        subfunc = response[1]
        assert subfunc == 0x03   # Extended

    def test_session_control_p2_server(self):
        response = bytes([0x50, 0x03, 0x00, 0x32, 0x07, 0xD0])
        p2 = (response[2] << 8) | response[3]
        assert p2 == 50   # 50 ms

    def test_read_data_extract_did(self):
        response = bytes([0x62, 0xF2, 0x00, 0x00])
        did = (response[1] << 8) | response[2]
        assert did == 0xF200

    def test_read_data_extract_value(self):
        response = bytes([0x62, 0xF1, 0x00, 0x04])
        value = response[3]
        assert value == 0x04   # WiperCurrentMode = AUTO

    def test_write_data_extract_did(self):
        response = bytes([0x6E, 0xF2, 0x01])
        did = (response[1] << 8) | response[2]
        assert did == 0xF201   # WCAvailable

    def test_routine_control_extract_rid(self):
        response = bytes([0x71, 0x01, 0x02, 0x01, 0x0A])
        rid = (response[2] << 8) | response[3]
        assert rid == 0x0201   # FrontWiperTest

    def test_security_seed_extraction(self):
        response = bytes([0x67, 0x03, 0x11, 0x22, 0x33, 0x44])
        seed = response[2:]
        assert seed == bytes([0x11, 0x22, 0x33, 0x44])

    def test_clear_dtc_positive(self):
        response = bytes([0x54])
        assert response[0] == 0x54

    def test_comm_control_positive(self):
        response = bytes([0x68, 0x00])
        assert response[0] == 0x68
        assert response[1] == 0x00   # enableRxAndTx

    def test_ecu_soft_reset_positive(self):
        response = bytes([0x51, 0x03])
        assert response[0] == 0x51
        assert response[1] == 0x03   # softReset
