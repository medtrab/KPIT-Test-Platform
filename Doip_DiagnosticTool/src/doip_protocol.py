#!/usr/bin/env python3
"""
DoIP Protocol Constants and Helper Functions (ISO 13400-2)

This module centralises all DoIP protocol definitions and low‑level socket
utilities used by both the ECU simulator and the diagnostic client.

No functional logic is modified – only extracted to eliminate duplication.
All functions preserve original behaviour.
"""

import socket
import struct
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# =============================================================================
# DoIP Protocol Constants (ISO 13400-2)
# =============================================================================
DOIP_PROTOCOL_VERSION = 0x02
DOIP_INVERSE_VERSION = 0xFD

# Payload Types
DOIP_VEHICLE_IDENTIFICATION_REQUEST = 0x0001
DOIP_VEHICLE_IDENTIFICATION_RESPONSE = 0x0004
DOIP_ROUTING_ACTIVATION_REQUEST = 0x0005
DOIP_ROUTING_ACTIVATION_RESPONSE = 0x0006
DOIP_PAYLOAD_DIAG = 0x8001

# Logical Addresses (simulation fixed values)
LOGICAL_ADDR_TESTER = 0x0E00
LOGICAL_ADDR_ECU = 0x0E01

# =============================================================================
# Hexadecimal Conversion Utilities
# =============================================================================
def hex_str_to_bytes(hex_str: str) -> bytes:
    """
    Convert a hexadecimal string to bytes.

    Args:
        hex_str: Hex string with optional spaces, '0x', or commas.

    Returns:
        Bytes object.

    Raises:
        ValueError: If the input cannot be parsed as hexadecimal.
    """
    hex_str = hex_str.replace(" ", "").replace("0x", "").replace(",", "")
    if len(hex_str) % 2 != 0:
        hex_str = "0" + hex_str
    return bytes.fromhex(hex_str)


def bytes_to_hex_str(data: bytes) -> str:
    """
    Convert bytes to a formatted hexadecimal string.

    Args:
        data: Bytes to convert.

    Returns:
        Space-separated hex representation, e.g. "10 02".
    """
    return " ".join(f"{b:02X}" for b in data)


# =============================================================================
# DoIP Message Encoding / Decoding
# =============================================================================
def build_doip_header(payload_type: int, payload_length: int) -> bytes:
    """
    Build a DoIP header (8 bytes) according to ISO 13400-2.

    Args:
        payload_type: DoIP payload type (e.g. 0x8001).
        payload_length: Length of the payload in bytes.

    Returns:
        8‑byte header as bytes.
    """
    return struct.pack(
        ">BBH I",
        DOIP_PROTOCOL_VERSION,
        DOIP_INVERSE_VERSION,
        payload_type,
        payload_length
    )


def send_doip_message(sock: socket.socket, payload_type: int, payload: bytes) -> None:
    """
    Send a complete DoIP message over an open socket.

    Args:
        sock: Connected TCP socket.
        payload_type: DoIP payload type.
        payload: Payload bytes.
    """
    header = build_doip_header(payload_type, len(payload))
    sock.send(header + payload)


def receive_doip_message(sock: socket.socket, timeout: float = 5.0) -> Tuple[Optional[int], Optional[bytes]]:
    """
    Receive a single DoIP message from a socket.

    Args:
        sock: TCP socket.
        timeout: Socket timeout in seconds.

    Returns:
        Tuple (payload_type, payload) or (None, None) on timeout/error.
    """
    sock.settimeout(timeout)
    try:
        data = sock.recv(4096)
        if len(data) < 8:
            return None, None

        protocol_version, inverse_version, payload_type = struct.unpack(">BBH", data[:4])
        payload_length = struct.unpack(">I", data[4:8])[0]

        # Validate protocol version (non‑critical, but keep original behaviour)
        if protocol_version != DOIP_PROTOCOL_VERSION or inverse_version != DOIP_INVERSE_VERSION:
            logger.warning("Received DoIP message with incorrect protocol version")
            return None, None

        if len(data) < 8 + payload_length:
            return None, None

        payload = data[8:8 + payload_length]
        return payload_type, payload

    except socket.timeout:
        return None, None
    except Exception as e:
        logger.debug(f"Error receiving DoIP message: {e}")
        return None, None