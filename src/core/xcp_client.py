"""
backend/xcp_client.py

Minimal XCP-on-UDP client.
Implements only what the Actuator Lab needs:
  - CONNECT / DISCONNECT
  - SHORT_DOWNLOAD  (write 1-4 bytes to an ECU address)
  - STATUS          (keep-alive / health check)

XCP packet layout (ASAM MCD-1 XCP):
  Byte 0   : Command code
  Byte 1-n : Payload (command-specific)

All multi-byte values are little-endian per XCP standard.
"""

import socket
import struct
import threading
import logging
from typing import Optional

logger = logging.getLogger(__name__)

CMD_CONNECT        = 0xFF
CMD_DISCONNECT     = 0xFE
CMD_STATUS         = 0xFD
CMD_SHORT_DOWNLOAD = 0xED

RES_OK  = 0xFF
RES_ERR = 0xFE


class XCPError(Exception):
    pass


class XCPClient:
    """Thread-safe XCP-on-UDP client."""

    def __init__(self, host: str, port: int, timeout: float = 1.0):
        self._host    = host
        self._port    = port
        self._timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._lock    = threading.Lock()
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        with self._lock:
            try:
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self._sock.settimeout(self._timeout)
                self._sock.connect((self._host, self._port))
                self._sock.send(bytes([CMD_CONNECT, 0x00]))
                resp = self._sock.recv(256)
                if resp and resp[0] == RES_OK:
                    self._connected = True
                    logger.info(f"XCP connected to {self._host}:{self._port}")
                    return True
                logger.warning(f"XCP CONNECT rejected: {resp.hex() if resp else 'empty'}")
                return False
            except Exception as e:
                logger.error(f"XCP connect error: {e}")
                self._cleanup()
                return False

    def disconnect(self):
        with self._lock:
            if self._sock and self._connected:
                try:
                    self._sock.send(bytes([CMD_DISCONNECT, 0x00]))
                except Exception:
                    pass
            self._cleanup()
            logger.info("XCP disconnected")

    def _cleanup(self):
        self._connected = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def short_download(self, address: int, data: bytes) -> bool:
        """Write 1-4 bytes at ECU address via XCP SHORT_DOWNLOAD."""
        if not 1 <= len(data) <= 4:
            raise XCPError(f"SHORT_DOWNLOAD supports 1-4 bytes, got {len(data)}")
        with self._lock:
            if not self._connected or not self._sock:
                raise XCPError("Not connected")
            try:
                addr_bytes = struct.pack("<I", address)
                pkt = bytes([CMD_SHORT_DOWNLOAD, len(data), 0x00, 0x00]) + addr_bytes + data
                self._sock.send(pkt)
                resp = self._sock.recv(256)
                if resp and resp[0] == RES_OK:
                    return True
                if resp and resp[0] == RES_ERR:
                    err = resp[1] if len(resp) > 1 else 0x00
                    logger.warning(f"XCP SHORT_DOWNLOAD error 0x{err:02X} @ 0x{address:08X}")
                return False
            except socket.timeout:
                logger.warning(f"XCP SHORT_DOWNLOAD timeout @ 0x{address:08X}")
                return False
            except Exception as e:
                logger.error(f"XCP SHORT_DOWNLOAD: {e}")
                self._connected = False
                return False

    def write_variable(self, address: int, value: int, size: int) -> bool:
        if size == 1:
            return self.short_download(address, struct.pack("B", max(0, min(255, int(value)))))
        elif size == 2:
            return self.short_download(address, struct.pack("<H", max(0, min(65535, int(value)))))
        elif size == 4:
            return self.short_download(address, struct.pack("<I", max(0, min(0xFFFFFFFF, int(value)))))
        raise XCPError(f"Unsupported size: {size}")

    def ping(self) -> bool:
        with self._lock:
            if not self._connected or not self._sock:
                return False
            try:
                self._sock.send(bytes([CMD_STATUS, 0x00]))
                resp = self._sock.recv(256)
                return bool(resp and resp[0] == RES_OK)
            except Exception:
                return False
