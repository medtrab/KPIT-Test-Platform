#!/usr/bin/env python3
"""
Unit tests for doip_protocol.py – DoIP header creation and hex utilities.
No network sockets are used; only pure logic is tested.
"""

import sys
import os
import unittest

# -----------------------------------------------------------------------------
# Add src/ to sys.path so that we can import the modules without modification
# -----------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.doip_protocol import (
    DOIP_PROTOCOL_VERSION,
    DOIP_INVERSE_VERSION,
    DOIP_VEHICLE_IDENTIFICATION_REQUEST,
    DOIP_PAYLOAD_DIAG,
    LOGICAL_ADDR_TESTER,
    LOGICAL_ADDR_ECU,
    hex_str_to_bytes,
    bytes_to_hex_str,
    build_doip_header,
)


class TestDoipProtocol(unittest.TestCase):
    """Test DoIP protocol constants and pure functions."""

    def test_protocol_constants(self):
        """Verify version and inverse version are correct."""
        self.assertEqual(DOIP_PROTOCOL_VERSION, 0x02)
        self.assertEqual(DOIP_INVERSE_VERSION, 0xFD)

    def test_logical_addresses(self):
        """Tester and ECU logical addresses are fixed."""
        self.assertEqual(LOGICAL_ADDR_TESTER, 0x0E00)
        self.assertEqual(LOGICAL_ADDR_ECU, 0x0E01)

    def test_hex_str_to_bytes(self):
        """Convert various hex string formats to bytes."""
        self.assertEqual(hex_str_to_bytes("10 02"), b"\x10\x02")
        self.assertEqual(hex_str_to_bytes("0x10 0x02"), b"\x10\x02")
        self.assertEqual(hex_str_to_bytes("10,02"), b"\x10\x02")
        self.assertEqual(hex_str_to_bytes("1002"), b"\x10\x02")
        self.assertEqual(hex_str_to_bytes("0x1002"), b"\x10\x02")
        # odd length -> pad with leading zero
        self.assertEqual(hex_str_to_bytes("102"), b"\x01\x02")
        with self.assertRaises(ValueError):
            hex_str_to_bytes("XX")

    def test_bytes_to_hex_str(self):
        """Convert bytes to space‑separated uppercase hex."""
        self.assertEqual(bytes_to_hex_str(b"\x10\x02"), "10 02")
        self.assertEqual(bytes_to_hex_str(b"\xAA\xBB"), "AA BB")
        self.assertEqual(bytes_to_hex_str(b""), "")

    def test_build_doip_header(self):
        """8‑byte header: version, inverse, payload type, length."""
        header = build_doip_header(DOIP_PAYLOAD_DIAG, 4)
        # Expected: 02 FD 80 01 00 00 00 04
        expected = bytes([
            DOIP_PROTOCOL_VERSION,
            DOIP_INVERSE_VERSION,
            0x80, 0x01,          # 0x8001 (big endian)
            0x00, 0x00, 0x00, 0x04
        ])
        self.assertEqual(header, expected)

    def test_build_doip_header_zero_length(self):
        """Header with zero payload length."""
        header = build_doip_header(DOIP_VEHICLE_IDENTIFICATION_REQUEST, 0)
        expected = bytes([
            DOIP_PROTOCOL_VERSION,
            DOIP_INVERSE_VERSION,
            0x00, 0x01,          # 0x0001
            0x00, 0x00, 0x00, 0x00
        ])
        self.assertEqual(header, expected)


if __name__ == '__main__':
    unittest.main()