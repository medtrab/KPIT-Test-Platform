"""
tests/test_doip_protocol.py

Unit tests for core/transport/doip_protocol.py
Validates DoIP frame building, parsing, and protocol constants.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import struct
import pytest
from core.transport.doip_protocol import (
    build_doip_header,
    send_doip_message,
    receive_doip_message,
    bytes_to_hex_str,
    hex_str_to_bytes,
    DOIP_PROTOCOL_VERSION,
    DOIP_INVERSE_VERSION,
    DOIP_PAYLOAD_DIAG,
    DOIP_ROUTING_ACTIVATION_REQUEST,
    DOIP_ROUTING_ACTIVATION_RESPONSE,
    DOIP_VEHICLE_IDENTIFICATION_REQUEST,
    DOIP_VEHICLE_IDENTIFICATION_RESPONSE,
    LOGICAL_ADDR_TESTER,
    LOGICAL_ADDR_ECU,
)


class TestDoIPConstants:

    def test_protocol_version(self):
        assert DOIP_PROTOCOL_VERSION == 0x02

    def test_inverse_version(self):
        assert DOIP_INVERSE_VERSION == 0xFD

    def test_version_xor(self):
        """Protocol version XOR inverse must equal 0xFF"""
        assert DOIP_PROTOCOL_VERSION ^ DOIP_INVERSE_VERSION == 0xFF

    def test_diagnostic_payload_type(self):
        assert DOIP_PAYLOAD_DIAG == 0x8001

    def test_routing_activation_request_type(self):
        assert DOIP_ROUTING_ACTIVATION_REQUEST == 0x0005

    def test_routing_activation_response_type(self):
        assert DOIP_ROUTING_ACTIVATION_RESPONSE == 0x0006

    def test_vehicle_id_request_type(self):
        assert DOIP_VEHICLE_IDENTIFICATION_REQUEST == 0x0001

    def test_vehicle_id_response_type(self):
        assert DOIP_VEHICLE_IDENTIFICATION_RESPONSE == 0x0004

    def test_tester_logical_address(self):
        assert LOGICAL_ADDR_TESTER == 0x07DF

    def test_ecu_logical_address(self):
        assert LOGICAL_ADDR_ECU == 0x0700


class TestBuildDoIPHeader:

    def test_header_length(self):
        header = build_doip_header(DOIP_PAYLOAD_DIAG, 10)
        assert len(header) == 8

    def test_header_protocol_version(self):
        header = build_doip_header(DOIP_PAYLOAD_DIAG, 0)
        assert header[0] == DOIP_PROTOCOL_VERSION

    def test_header_inverse_version(self):
        header = build_doip_header(DOIP_PAYLOAD_DIAG, 0)
        assert header[1] == DOIP_INVERSE_VERSION

    def test_header_payload_type(self):
        header = build_doip_header(DOIP_PAYLOAD_DIAG, 0)
        payload_type = struct.unpack(">H", header[2:4])[0]
        assert payload_type == DOIP_PAYLOAD_DIAG

    def test_header_payload_length_zero(self):
        header = build_doip_header(DOIP_PAYLOAD_DIAG, 0)
        length = struct.unpack(">I", header[4:8])[0]
        assert length == 0

    def test_header_payload_length_nonzero(self):
        header = build_doip_header(DOIP_PAYLOAD_DIAG, 255)
        length = struct.unpack(">I", header[4:8])[0]
        assert length == 255

    def test_header_payload_length_large(self):
        header = build_doip_header(DOIP_PAYLOAD_DIAG, 65535)
        length = struct.unpack(">I", header[4:8])[0]
        assert length == 65535

    def test_header_different_payload_types(self):
        for ptype in (
            DOIP_PAYLOAD_DIAG,
            DOIP_ROUTING_ACTIVATION_REQUEST,
            DOIP_VEHICLE_IDENTIFICATION_REQUEST,
        ):
            header = build_doip_header(ptype, 0)
            extracted = struct.unpack(">H", header[2:4])[0]
            assert extracted == ptype

    def test_diagnostic_message_full_frame(self):
        """Build a complete DoIP diagnostic message and verify structure."""
        uds = bytes([0x10, 0x03])   # DiagnosticSessionControl Extended
        src  = LOGICAL_ADDR_TESTER
        dst  = LOGICAL_ADDR_ECU
        doip_payload = struct.pack(">HH", src, dst) + uds
        header = build_doip_header(DOIP_PAYLOAD_DIAG, len(doip_payload))
        frame = header + doip_payload

        # Parse back
        assert len(frame) == 8 + 4 + 2      # header + addrs + UDS
        assert frame[0] == DOIP_PROTOCOL_VERSION
        assert frame[1] == DOIP_INVERSE_VERSION
        extracted_type = struct.unpack(">H", frame[2:4])[0]
        assert extracted_type == DOIP_PAYLOAD_DIAG
        extracted_src = struct.unpack(">H", frame[8:10])[0]
        assert extracted_src == src
        extracted_dst = struct.unpack(">H", frame[10:12])[0]
        assert extracted_dst == dst
        assert frame[12:14] == uds


class TestHexConversion:

    def test_bytes_to_hex_str_basic(self):
        assert bytes_to_hex_str(bytes([0x10, 0x03])) == "10 03"

    def test_bytes_to_hex_str_single(self):
        assert bytes_to_hex_str(bytes([0xFF])) == "FF"

    def test_bytes_to_hex_str_empty(self):
        assert bytes_to_hex_str(b"") == ""

    def test_bytes_to_hex_str_padding(self):
        assert bytes_to_hex_str(bytes([0x00, 0x01])) == "00 01"

    def test_hex_str_to_bytes_basic(self):
        assert hex_str_to_bytes("10 03") == bytes([0x10, 0x03])

    def test_hex_str_to_bytes_no_spaces(self):
        assert hex_str_to_bytes("1003") == bytes([0x10, 0x03])

    def test_hex_str_to_bytes_0x_prefix(self):
        assert hex_str_to_bytes("0x10 0x03") == bytes([0x10, 0x03])

    def test_hex_str_to_bytes_mixed(self):
        assert hex_str_to_bytes("19 02 FF") == bytes([0x19, 0x02, 0xFF])

    def test_hex_roundtrip(self):
        original = bytes([0x27, 0x01, 0xAA, 0xBB])
        assert hex_str_to_bytes(bytes_to_hex_str(original)) == original

    def test_hex_str_to_bytes_invalid_raises(self):
        with pytest.raises((ValueError, Exception)):
            hex_str_to_bytes("ZZ XX")
