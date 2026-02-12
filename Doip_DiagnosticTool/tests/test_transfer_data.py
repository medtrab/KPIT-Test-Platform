#!/usr/bin/env python3
"""
Unit tests for UDS Download/Upload (0x34, 0x35, 0x36, 0x37).
Tests security restrictions, block counter, wrong block counter NRC 0x73.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.ecu_server import UDSECU
from src.doip_protocol import hex_str_to_bytes


class TestTransferData(unittest.TestCase):
    """Test services 0x34, 0x35, 0x36, 0x37."""

    def setUp(self):
        self.ecu = UDSECU()
        # Unlock to security level 2 and switch to programming session
        self.ecu.handle_uds(hex_str_to_bytes("27 03"))
        self.ecu.handle_uds(hex_str_to_bytes("27 04 44 33 22 11"))
        self.ecu.handle_uds(hex_str_to_bytes("10 02"))
        self.assertEqual(self.ecu.state.session, 0x02)
        self.assertEqual(self.ecu.state.security_level, 2)

    def test_download_without_programming_session(self):
        """34 ... in default session → NRC 0x7E."""
        # Reset to default session
        self.ecu.handle_uds(hex_str_to_bytes("10 01"))
        request = hex_str_to_bytes("34 00 44 00 00 00 00 00 00 00 18")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x34\x7E")

    def test_download_without_security_level_2(self):
        """
        34 ... in programming session but security level <2 → NRC 0x33.
        """
        # Keep session = 0x02, but lower security level (test only)
        self.ecu.state.security_level = 1
        request = hex_str_to_bytes("34 00 44 00 00 00 00 00 00 00 18")
        response = self.ecu.handle_uds(request)
        self.assertEqual(response, b"\x7F\x34\x33")

    def test_successful_download_sequence(self):
        """Full download: RequestDownload → TransferData → TransferExit."""
        # RequestDownload
        req = hex_str_to_bytes("34 00 44 00 00 00 00 00 00 00 18")
        resp = self.ecu.handle_uds(req)
        self.assertEqual(resp[0], 0x74)
        self.assertEqual(resp[1], 0x20)
        self.assertEqual(int.from_bytes(resp[2:4], 'big'), 0x08)
        self.assertTrue(self.ecu.state.download_active)

        # TransferData block 1
        req = hex_str_to_bytes("36 01 AA BB CC DD")
        resp = self.ecu.handle_uds(req)
        self.assertEqual(resp, b"\x76\x01")
        self.assertEqual(self.ecu.state.expected_block, 2)
        self.assertEqual(self.ecu.state.flash_memory, b"\xAA\xBB\xCC\xDD")

        # TransferData block 2
        req = hex_str_to_bytes("36 02 EE FF 00 11")
        resp = self.ecu.handle_uds(req)
        self.assertEqual(resp, b"\x76\x02")
        self.assertEqual(self.ecu.state.flash_memory, b"\xAA\xBB\xCC\xDD\xEE\xFF\x00\x11")

        # TransferExit
        req = hex_str_to_bytes("37")
        resp = self.ecu.handle_uds(req)
        self.assertEqual(resp, b"\x77")
        self.assertFalse(self.ecu.state.download_active)

    def test_wrong_block_counter(self):
        """TransferData with wrong block counter → NRC 0x73."""
        # Start download
        self.ecu.handle_uds(hex_str_to_bytes("34 00 44 00 00 00 00 00 00 00 18"))
        self.ecu.handle_uds(hex_str_to_bytes("36 01 AA BB"))  # block 1 OK
        # Send block 1 again (expected block is 2)
        req = hex_str_to_bytes("36 01 CC DD")
        resp = self.ecu.handle_uds(req)
        self.assertEqual(resp, b"\x7F\x36\x73")
        self.assertEqual(self.ecu.state.expected_block, 2)

    def test_upload_without_security_level1(self):
        """35 ... without security level1 → NRC 0x33."""
        self.ecu = UDSECU()  # fresh, security=0
        req = hex_str_to_bytes("35 00 44 00 00 00 00 00 00 00 18")
        resp = self.ecu.handle_uds(req)
        self.assertEqual(resp, b"\x7F\x35\x33")

    def test_upload_no_flash_memory(self):
        """35 ... when flash_memory is empty → NRC 0x31."""
        self.ecu.handle_uds(hex_str_to_bytes("27 01"))
        self.ecu.handle_uds(hex_str_to_bytes("27 02 33 CC 55 AA"))  # correct key
        req = hex_str_to_bytes("35 00 44 00 00 00 00 00 00 00 18")
        resp = self.ecu.handle_uds(req)
        self.assertEqual(resp, b"\x7F\x35\x31")

    def test_upload_after_download(self):
        """Write some data, then read it back via upload."""
        # Perform download first
        self.ecu.handle_uds(hex_str_to_bytes("34 00 44 00 00 00 00 00 00 00 18"))
        self.ecu.handle_uds(hex_str_to_bytes("36 01 AA BB CC DD"))
        self.ecu.handle_uds(hex_str_to_bytes("36 02 EE FF 00 11"))
        self.ecu.handle_uds(hex_str_to_bytes("37"))

        # RequestUpload
        req = hex_str_to_bytes("35 00 44 00 00 00 00 00 00 00 18")
        resp = self.ecu.handle_uds(req)
        self.assertEqual(resp[0], 0x75)
        self.assertEqual(resp[1], 0x20)
        available = int.from_bytes(resp[2:4], 'big')
        self.assertEqual(available, 8)

        # TransferData (upload) block 1
        req = hex_str_to_bytes("36 01")
        resp = self.ecu.handle_uds(req)
        self.assertEqual(resp[0], 0x76)
        self.assertEqual(resp[1], 0x01)
        self.assertEqual(resp[2:], b"\xAA\xBB\xCC\xDD\xEE\xFF\x00\x11")


if __name__ == '__main__':
    unittest.main()