#!/usr/bin/env python3
"""
Unit tests for UDS Diagnostic Session Control (0x10).
Tests session changes, security level restrictions, and NRCs.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.ecu_server import UDSECU
from src.doip_protocol import hex_str_to_bytes


class TestUdsSessionControl(unittest.TestCase):
    """Test service 0x10 – DiagnosticSessionControl."""

    def setUp(self):
        self.ecu = UDSECU()
        self.assertEqual(self.ecu.state.session, 0x01)
        self.assertEqual(self.ecu.state.security_level, 0)

    def test_change_to_default_session(self):
        """
        10 01 → already default, should succeed and return
        0x50 0x01 + P2 (2 bytes) + P2* (2 bytes) = total 6 bytes.
        """
        request = hex_str_to_bytes("10 01")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response[0], 0x50)
        self.assertEqual(response[1], 0x01)
        self.assertEqual(len(response), 6)  # 0x50, 0x01, 2B P2, 2B P2*
        self.assertEqual(self.ecu.state.session, 0x01)

    def test_change_to_programming_session_without_security(self):
        """10 02 → requires security level ≥2 → should be denied."""
        request = hex_str_to_bytes("10 02")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x10\x33")
        self.assertEqual(self.ecu.state.session, 0x01)

    def test_change_to_extended_session_without_security(self):
        """10 03 → requires security level ≥1 → should be denied."""
        request = hex_str_to_bytes("10 03")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x10\x33")
        self.assertEqual(self.ecu.state.session, 0x01)

    def test_change_to_programming_session_with_security(self):
        """10 02 after security level 2 → should succeed."""
        # Unlock to level 2 (seed/key for level 2)
        self.ecu.handle_uds(hex_str_to_bytes("27 03"))
        self.ecu.handle_uds(hex_str_to_bytes("27 04 44 33 22 11"))
        self.assertEqual(self.ecu.state.security_level, 2)

        request = hex_str_to_bytes("10 02")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response[0], 0x50)
        self.assertEqual(response[1], 0x02)
        self.assertEqual(len(response), 6)
        self.assertEqual(self.ecu.state.session, 0x02)

    def test_unsupported_subfunction(self):
        """10 04 → subfunction not supported."""
        request = hex_str_to_bytes("10 04")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x10\x12")

    def test_incorrect_message_length(self):
        """10 alone → missing subfunction byte."""
        request = hex_str_to_bytes("10")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x10\x13")


if __name__ == '__main__':
    unittest.main()#!/usr/bin/env python3
"""
Unit tests for UDS Diagnostic Session Control (0x10).
Tests session changes, security level restrictions, and NRCs.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from ecu_server import UDSECU
from doip_protocol import hex_str_to_bytes


class TestUdsSessionControl(unittest.TestCase):
    """Test service 0x10 – DiagnosticSessionControl."""

    def setUp(self):
        self.ecu = UDSECU()
        self.assertEqual(self.ecu.state.session, 0x01)
        self.assertEqual(self.ecu.state.security_level, 0)

    def test_change_to_default_session(self):
        """
        10 01 → already default, should succeed and return
        0x50 0x01 + P2 (2 bytes) + P2* (2 bytes) = total 6 bytes.
        """
        request = hex_str_to_bytes("10 01")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response[0], 0x50)
        self.assertEqual(response[1], 0x01)
        self.assertEqual(len(response), 6)  # 0x50, 0x01, 2B P2, 2B P2*
        self.assertEqual(self.ecu.state.session, 0x01)

    def test_change_to_programming_session_without_security(self):
        """10 02 → requires security level ≥2 → should be denied."""
        request = hex_str_to_bytes("10 02")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x10\x33")
        self.assertEqual(self.ecu.state.session, 0x01)

    def test_change_to_extended_session_without_security(self):
        """10 03 → requires security level ≥1 → should be denied."""
        request = hex_str_to_bytes("10 03")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x10\x33")
        self.assertEqual(self.ecu.state.session, 0x01)

    def test_change_to_programming_session_with_security(self):
        """10 02 after security level 2 → should succeed."""
        # Unlock to level 2 (seed/key for level 2)
        self.ecu.handle_uds(hex_str_to_bytes("27 03"))
        self.ecu.handle_uds(hex_str_to_bytes("27 04 44 33 22 11"))
        self.assertEqual(self.ecu.state.security_level, 2)

        request = hex_str_to_bytes("10 02")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response[0], 0x50)
        self.assertEqual(response[1], 0x02)
        self.assertEqual(len(response), 6)
        self.assertEqual(self.ecu.state.session, 0x02)

    def test_unsupported_subfunction(self):
        """10 04 → subfunction not supported."""
        request = hex_str_to_bytes("10 04")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x10\x12")

    def test_incorrect_message_length(self):
        """10 alone → missing subfunction byte."""
        request = hex_str_to_bytes("10")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x10\x13")


if __name__ == '__main__':
    unittest.main()