#!/usr/bin/env python3
"""
Unit tests for UDS Security Access (0x27).
Verifies seed/key logic, access levels, and NRCs.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.ecu_server import UDSECU
from src.doip_protocol import hex_str_to_bytes


class TestSecurityAccess(unittest.TestCase):
    """Test service 0x27 – SecurityAccess."""

    def setUp(self):
        self.ecu = UDSECU()
        self.assertEqual(self.ecu.state.security_level, 0)

    def test_request_seed_level1(self):
        """27 01 → request seed for security level 1."""
        request = hex_str_to_bytes("27 01")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response[0], 0x67)
        self.assertEqual(response[1], 0x01)
        self.assertEqual(len(response), 6)
        self.assertEqual(response[2:], b"\xAA\x55\xCC\x33")

    def test_send_key_level1_correct(self):
        """
        27 02 with correct key (inverse of seed) → positive response,
        security level becomes 1.
        """
        self.ecu.handle_uds(hex_str_to_bytes("27 01"))
        request = hex_str_to_bytes("27 02 33 CC 55 AA")  # correct key
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x67\x02")
        self.assertEqual(self.ecu.state.security_level, 1)

    def test_send_key_level1_incorrect(self):
        """27 02 with wrong key → NRC 0x35."""
        self.ecu.handle_uds(hex_str_to_bytes("27 01"))
        request = hex_str_to_bytes("27 02 11 22 33 44")  # wrong
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x27\x35")
        self.assertEqual(self.ecu.state.security_level, 0)

    def test_request_seed_level2(self):
        """27 03 → request seed for level 2 (11 22 33 44)."""
        request = hex_str_to_bytes("27 03")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response[0], 0x67)
        self.assertEqual(response[1], 0x03)
        self.assertEqual(response[2:], b"\x11\x22\x33\x44")

    def test_send_key_level2_correct(self):
        """27 04 44 33 22 11 → correct key for level 2."""
        self.ecu.handle_uds(hex_str_to_bytes("27 03"))
        request = hex_str_to_bytes("27 04 44 33 22 11")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x67\x04")
        self.assertEqual(self.ecu.state.security_level, 2)

    def test_invalid_subfunction_odd_greater_than_0x7F(self):
        """27 81 → subfunction > 0x7F → NRC 0x12."""
        request = hex_str_to_bytes("27 81")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x27\x12")

    def test_send_key_without_prior_seed(self):
        """27 02 AA 55 CC 33 without requesting seed → NRC 0x35."""
        request = hex_str_to_bytes("27 02 AA 55 CC 33")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x27\x35")


if __name__ == '__main__':
    unittest.main()