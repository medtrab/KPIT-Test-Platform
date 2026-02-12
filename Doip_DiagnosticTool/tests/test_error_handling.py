#!/usr/bin/env python3
"""
Unit tests for generic error conditions:
- Unsupported SID
- Empty request
- NRCs for various out‑of‑range / condition failures
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.ecu_server import UDSECU
from src.doip_protocol import hex_str_to_bytes


class TestErrorHandling(unittest.TestCase):
    """Test negative responses and edge cases."""

    def setUp(self):
        self.ecu = UDSECU()

    def test_empty_request(self):
        """Zero‑length UDS request → NRC 0x13."""
        request = b""
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x00\x13")

    def test_unsupported_sid(self):
        """SID 0x23 (ReadMemoryByAddress) not implemented → NRC 0x11."""
        request = hex_str_to_bytes("23 00 00 00 00")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x23\x11")

    def test_sid_0x10_too_short(self):
        """10 alone → NRC 0x13."""
        request = hex_str_to_bytes("10")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x10\x13")

    def test_sid_0x27_too_short(self):
        """27 alone → NRC 0x13."""
        request = hex_str_to_bytes("27")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x27\x13")

    def test_sid_0x34_missing_bytes(self):
        """
        34 00 44 00 ... incomplete → NRC 0x13.
        To reach the length check we must be in programming session.
        """
        # Unlock to level 2 and switch to programming session
        self.ecu.handle_uds(hex_str_to_bytes("27 03"))
        self.ecu.handle_uds(hex_str_to_bytes("27 04 44 33 22 11"))
        self.ecu.handle_uds(hex_str_to_bytes("10 02"))
        # Now send incomplete RequestDownload
        request = hex_str_to_bytes("34 00 44 00 00")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x34\x13")

    def test_download_security_restriction(self):
        """
        34 without security level 2 → NRC 0x33.
        Must be in programming session first.
        """
        # Switch to programming session (requires level 2)
        self.ecu.handle_uds(hex_str_to_bytes("27 03"))
        self.ecu.handle_uds(hex_str_to_bytes("27 04 44 33 22 11"))
        self.ecu.handle_uds(hex_str_to_bytes("10 02"))
        # Now artificially lower security (for test only)
        self.ecu.state.security_level = 1
        request = hex_str_to_bytes("34 00 44 00 00 00 00 00 00 00 18")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x34\x33")

    def test_transfer_data_without_active_download_or_upload(self):
        """36 when no transfer active → NRC 0x24."""
        request = hex_str_to_bytes("36 01 AA")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x36\x24")

    def test_transfer_exit_without_active_transfer(self):
        """37 without active transfer → NRC 0x24."""
        request = hex_str_to_bytes("37")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x37\x24")

    def test_wrong_block_counter_in_transfer(self):
        """Already tested in test_transfer_data, but repeat here for completeness."""
        # Start download
        self.ecu.handle_uds(hex_str_to_bytes("27 03"))
        self.ecu.handle_uds(hex_str_to_bytes("27 04 44 33 22 11"))
        self.ecu.handle_uds(hex_str_to_bytes("10 02"))
        self.ecu.handle_uds(hex_str_to_bytes("34 00 44 00 00 00 00 00 00 00 18"))
        self.ecu.handle_uds(hex_str_to_bytes("36 01 AA BB"))  # block 1
        # Wrong block counter
        resp = self.ecu.handle_uds(hex_str_to_bytes("36 01 CC DD"))
        self.assertEqual(resp, b"\x7F\x36\x73")


if __name__ == '__main__':
    unittest.main()