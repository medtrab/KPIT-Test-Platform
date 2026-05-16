"""
tests/test_constants.py

Unit tests for core/constants.py
Validates logical addresses, UDS service IDs, and NRC codes.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from core.constants import (
    LOGICAL_ADDR_BCM,
    LOGICAL_ADDR_WC,
    LOGICAL_ADDR_TESTER,
    UDS_SERVICE,
    UDS_NRC,
)


class TestLogicalAddresses:

    def test_bcm_address(self):
        assert LOGICAL_ADDR_BCM == 0x0700

    def test_wc_address(self):
        assert LOGICAL_ADDR_WC == 0x0701

    def test_tester_address(self):
        assert LOGICAL_ADDR_TESTER == 0x07DF

    def test_bcm_wc_different(self):
        assert LOGICAL_ADDR_BCM != LOGICAL_ADDR_WC

    def test_addresses_are_16bit(self):
        for addr in (LOGICAL_ADDR_BCM, LOGICAL_ADDR_WC, LOGICAL_ADDR_TESTER):
            assert 0x0000 <= addr <= 0xFFFF


class TestUDSServiceIDs:

    def test_diagnostic_session_control(self):
        assert UDS_SERVICE["DIAGNOSTIC_SESSION_CONTROL"] == 0x10

    def test_ecu_reset(self):
        assert UDS_SERVICE["ECU_RESET"] == 0x11

    def test_clear_dtc(self):
        assert UDS_SERVICE["CLEAR_DTC"] == 0x14

    def test_read_dtc(self):
        assert UDS_SERVICE["READ_DTC"] == 0x19

    def test_read_data_by_id(self):
        assert UDS_SERVICE["READ_DATA_BY_ID"] == 0x22

    def test_security_access(self):
        assert UDS_SERVICE["SECURITY_ACCESS"] == 0x27

    def test_communication_control(self):
        assert UDS_SERVICE["COMMUNICATION_CONTROL"] == 0x28

    def test_write_data_by_id(self):
        assert UDS_SERVICE["WRITE_DATA_BY_ID"] == 0x2E

    def test_routine_control(self):
        assert UDS_SERVICE["ROUTINE_CONTROL"] == 0x31

    def test_tester_present(self):
        assert UDS_SERVICE["TESTER_PRESENT"] == 0x3E

    def test_positive_response_offset(self):
        """Positive response SID = request SID + 0x40"""
        assert UDS_SERVICE["DIAGNOSTIC_SESSION_CONTROL"] + 0x40 == 0x50
        assert UDS_SERVICE["SECURITY_ACCESS"] + 0x40 == 0x67
        assert UDS_SERVICE["WRITE_DATA_BY_ID"] + 0x40 == 0x6E

    def test_all_sids_are_bytes(self):
        for name, sid in UDS_SERVICE.items():
            assert 0x00 <= sid <= 0xFF, f"{name} SID out of byte range"


class TestNRCCodes:

    def test_service_not_supported(self):
        assert 0x11 in UDS_NRC

    def test_conditions_not_correct(self):
        assert 0x22 in UDS_NRC

    def test_security_access_denied(self):
        assert 0x33 in UDS_NRC

    def test_invalid_key(self):
        assert 0x35 in UDS_NRC

    def test_request_out_of_range(self):
        assert 0x31 in UDS_NRC

    def test_nrc_values_are_strings(self):
        for code, message in UDS_NRC.items():
            assert isinstance(message, str)
            assert len(message) > 0

    def test_nrc_codes_are_bytes(self):
        for code in UDS_NRC:
            assert 0x00 <= code <= 0xFF
