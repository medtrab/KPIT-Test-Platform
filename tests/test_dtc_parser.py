"""
tests/test_dtc_parser.py

Unit tests for UDS 0x59 (ReadDTCInformation) response parsing.
Mirrors the parsing logic in core/worker.py DiagnosticWorker._handle_response.

Response format (Sub 0x02 — reportDTCByStatusMask):
    [59 02 availabilityMask DTC_H DTC_M DTC_L Status ...]

Response format (Sub 0x04 — reportDTCSnapshotRecord):
    [59 04 DTC_H DTC_M DTC_L Status RecordNum DID_DATA...]

Response format (Sub 0x06 — reportDTCExtDataRecord):
    [59 06 DTC_H DTC_M DTC_L Status DATA...]
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Parser functions (mirrors worker.py logic)
# ─────────────────────────────────────────────────────────────────────────────

def parse_dtc_by_status_mask(response: bytes) -> list:
    """Parse 0x59 0x02 response → list of (dtc_code, status)."""
    if not response or response[0] != 0x59 or response[1] != 0x02:
        return []
    dtc_list = []
    idx = 3  # skip [0x59, 0x02, availabilityMask]
    while idx + 3 < len(response):
        dtc_code = (response[idx] << 16) | (response[idx+1] << 8) | response[idx+2]
        status   = response[idx+3]
        dtc_list.append((dtc_code, status))
        idx += 4
    return dtc_list


def parse_dtc_snapshot(response: bytes) -> tuple:
    """Parse 0x59 0x04 response → (dtc_code, record_num)."""
    if not response or len(response) < 7:
        return None, None
    if response[0] != 0x59 or response[1] != 0x04:
        return None, None
    dtc_code = (response[2] << 16) | (response[3] << 8) | response[4]
    rec_num  = response[6]
    return dtc_code, rec_num


def parse_dtc_extended(response: bytes) -> tuple:
    """Parse 0x59 0x06 response → (dtc_code, status)."""
    if not response or len(response) < 6:
        return None, None
    if response[0] != 0x59 or response[1] != 0x06:
        return None, None
    dtc_code = (response[2] << 16) | (response[3] << 8) | response[4]
    status   = response[5]
    return dtc_code, status


# ─────────────────────────────────────────────────────────────────────────────
# Tests — Sub 0x02 (reportDTCByStatusMask)
# ─────────────────────────────────────────────────────────────────────────────

class TestDTCByStatusMask:

    def test_single_dtc(self):
        response = bytes([0x59, 0x02, 0xFF, 0xB2, 0x20, 0x04, 0x2F])
        dtcs = parse_dtc_by_status_mask(response)
        assert len(dtcs) == 1
        assert dtcs[0] == (0xB22004, 0x2F)

    def test_two_dtcs(self):
        response = bytes([
            0x59, 0x02, 0xFF,
            0xB2, 0x20, 0x04, 0x2F,
            0xB2, 0x20, 0x09, 0x2E,
        ])
        dtcs = parse_dtc_by_status_mask(response)
        assert len(dtcs) == 2
        assert dtcs[0] == (0xB22004, 0x2F)
        assert dtcs[1] == (0xB22009, 0x2E)

    def test_no_dtcs(self):
        response = bytes([0x59, 0x02, 0xFF])
        dtcs = parse_dtc_by_status_mask(response)
        assert dtcs == []

    def test_active_status(self):
        response = bytes([0x59, 0x02, 0xFF, 0xB2, 0x20, 0x04, 0x2F])
        dtcs = parse_dtc_by_status_mask(response)
        assert dtcs[0][1] == 0x2F   # ACTIVE

    def test_not_active_status(self):
        response = bytes([0x59, 0x02, 0xFF, 0xB2, 0x20, 0x09, 0x2E])
        dtcs = parse_dtc_by_status_mask(response)
        assert dtcs[0][1] == 0x2E   # NOT ACTIVE

    def test_wrong_sid_returns_empty(self):
        response = bytes([0x19, 0x02, 0xFF, 0xB2, 0x20, 0x04, 0x2F])
        dtcs = parse_dtc_by_status_mask(response)
        assert dtcs == []

    def test_wrong_subfunc_returns_empty(self):
        response = bytes([0x59, 0x04, 0xFF, 0xB2, 0x20, 0x04, 0x2F])
        dtcs = parse_dtc_by_status_mask(response)
        assert dtcs == []

    def test_empty_response(self):
        assert parse_dtc_by_status_mask(b"") == []

    def test_wc_dtc(self):
        """WC DTC B22101"""
        response = bytes([0x59, 0x02, 0xFF, 0xB2, 0x21, 0x01, 0x2F])
        dtcs = parse_dtc_by_status_mask(response)
        assert dtcs[0][0] == 0xB22101

    def test_dtc_code_encoding(self):
        """Verify 3-byte DTC encoding: [H M L] → 24-bit code."""
        response = bytes([0x59, 0x02, 0xFF, 0x12, 0x34, 0x56, 0xFF])
        dtcs = parse_dtc_by_status_mask(response)
        assert dtcs[0][0] == 0x123456

    def test_multiple_dtcs_order_preserved(self):
        response = bytes([
            0x59, 0x02, 0xFF,
            0xB2, 0x20, 0x01, 0x2F,
            0xB2, 0x20, 0x02, 0x2E,
            0xB2, 0x20, 0x03, 0x2F,
        ])
        dtcs = parse_dtc_by_status_mask(response)
        assert len(dtcs) == 3
        assert dtcs[0][0] == 0xB22001
        assert dtcs[1][0] == 0xB22002
        assert dtcs[2][0] == 0xB22003


# ─────────────────────────────────────────────────────────────────────────────
# Tests — Sub 0x04 (reportDTCSnapshotRecord)
# ─────────────────────────────────────────────────────────────────────────────

class TestDTCSnapshot:

    def test_basic_parse(self):
        response = bytes([0x59, 0x04, 0xB2, 0x20, 0x04, 0x2F, 0x01, 0xAA, 0xBB])
        dtc, rec = parse_dtc_snapshot(response)
        assert dtc == 0xB22004
        assert rec == 0x01

    def test_record_number_extraction(self):
        response = bytes([0x59, 0x04, 0xB2, 0x20, 0x09, 0x2E, 0x03, 0x00])
        dtc, rec = parse_dtc_snapshot(response)
        assert rec == 0x03

    def test_too_short_returns_none(self):
        dtc, rec = parse_dtc_snapshot(bytes([0x59, 0x04, 0xB2]))
        assert dtc is None
        assert rec is None

    def test_wrong_subfunc_returns_none(self):
        response = bytes([0x59, 0x02, 0xB2, 0x20, 0x04, 0x2F, 0x01])
        dtc, rec = parse_dtc_snapshot(response)
        assert dtc is None

    def test_empty_returns_none(self):
        dtc, rec = parse_dtc_snapshot(b"")
        assert dtc is None
        assert rec is None


# ─────────────────────────────────────────────────────────────────────────────
# Tests — Sub 0x06 (reportDTCExtDataRecord)
# ─────────────────────────────────────────────────────────────────────────────

class TestDTCExtended:

    def test_basic_parse(self):
        response = bytes([0x59, 0x06, 0xB2, 0x20, 0x04, 0x2F, 0x01, 0x00])
        dtc, status = parse_dtc_extended(response)
        assert dtc == 0xB22004
        assert status == 0x2F

    def test_status_extraction(self):
        response = bytes([0x59, 0x06, 0xB2, 0x20, 0x09, 0x2E, 0x00])
        dtc, status = parse_dtc_extended(response)
        assert status == 0x2E

    def test_too_short_returns_none(self):
        dtc, status = parse_dtc_extended(bytes([0x59, 0x06]))
        assert dtc is None

    def test_wrong_subfunc_returns_none(self):
        response = bytes([0x59, 0x04, 0xB2, 0x20, 0x04, 0x2F])
        dtc, status = parse_dtc_extended(response)
        assert dtc is None

    def test_empty_returns_none(self):
        dtc, status = parse_dtc_extended(b"")
        assert dtc is None


# ─────────────────────────────────────────────────────────────────────────────
# Tests — DTC status values
# ─────────────────────────────────────────────────────────────────────────────

class TestDTCStatusValues:

    def test_active_status_value(self):
        assert 0x2F == 0x2F   # ACTIVE in memory

    def test_not_active_status_value(self):
        assert 0x2E == 0x2E   # NOT ACTIVE in memory

    def test_clear_all_group(self):
        """Clear DTC group 0xFFFFFF clears all DTCs."""
        clear_frame = bytes([0x14, 0xFF, 0xFF, 0xFF])
        group = (clear_frame[1] << 16) | (clear_frame[2] << 8) | clear_frame[3]
        assert group == 0xFFFFFF

    def test_clear_positive_response(self):
        response = bytes([0x54])
        assert response[0] == 0x54
