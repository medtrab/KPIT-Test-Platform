#!/usr/bin/env python3
"""
Unit tests for UDS Read Data by Identifier (0x22).
Tests static DIDs, protected DIDs (session/security), NRCs.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.ecu_server import UDSECU
from src.doip_protocol import hex_str_to_bytes


class TestReadDataByIdentifier(unittest.TestCase):
    """Test service 0x22 – ReadDataByIdentifier."""

    def setUp(self):
        self.ecu = UDSECU()
        # default session 0x01, security level 0

    def test_read_vin(self):
        """22 F1 90 → VIN = 56 34 12."""
        request = hex_str_to_bytes("22 F1 90")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response[0], 0x62)
        self.assertEqual(response[1:3], b"\xF1\x90")
        self.assertEqual(response[3:], b"\x56\x34\x12")

    def test_read_software_version(self):
        """22 F1 92 → 00 10 20 30."""
        request = hex_str_to_bytes("22 F1 92")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response[0], 0x62)
        self.assertEqual(response[1:3], b"\xF1\x92")
        self.assertEqual(response[3:], b"\x00\x10\x20\x30")

    def test_read_multiple_identifiers(self):
        """22 F1 90 F1 91 → VIN + serial number."""
        request = hex_str_to_bytes("22 F1 90 F1 91")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response[0], 0x62)
        self.assertEqual(response[1:6], b"\xF1\x90\x56\x34\x12")
        self.assertEqual(response[6:10], b"\xF1\x91\x01\x02")
        self.assertEqual(len(response), 10)

    def test_read_protected_did_without_session(self):
        """
        22 F2 00 → protected DID requiring session 2, security 2.
        Default session → NRC 0x7E (subfunction not supported in active session).
        """
        request = hex_str_to_bytes("22 F2 00")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x22\x7E")

    def test_read_protected_did_correct_session_but_low_security(self):
        """
        22 F2 10 → protected DID requiring session 1, security 1.
        Session is 0x01 (ok), but security level 0 → NRC 0x33.
        """
        request = hex_str_to_bytes("22 F2 10")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x22\x33")

    def test_read_protected_did_with_security(self):
        """
        22 F2 10 after unlocking to level 1 → security check passes,
        but DID is not present → NRC 0x31.
        """
        # Unlock to level 1 using correct key (inverse of seed)
        self.ecu.handle_uds(hex_str_to_bytes("27 01"))               # request seed
        self.ecu.handle_uds(hex_str_to_bytes("27 02 33 CC 55 AA"))  # send correct key
        self.assertEqual(self.ecu.state.security_level, 1)

        request = hex_str_to_bytes("22 F2 10")
        response = self.ecu.handle_uds(request)
        # DID 0xF210 is not in data_identifiers → NRC 0x31
        self.assertEqual(response[0], 0x7F)
        self.assertEqual(response[2], 0x31)

    def test_nonexistent_did(self):
        """22 F1 FF → DID not present → NRC 0x31."""
        request = hex_str_to_bytes("22 F1 FF")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x22\x31")

    def test_incorrect_length(self):
        """22 F1 → missing second DID byte → NRC 0x13."""
        request = hex_str_to_bytes("22 F1")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x22\x13")

    def test_odd_number_of_bytes(self):
        """22 F1 90 F1 → odd length → NRC 0x13."""
        request = hex_str_to_bytes("22 F1 90 F1")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x22\x13")


if __name__ == '__main__':
    unittest.main()