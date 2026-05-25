#!/usr/bin/env python3
"""
xcp_server_wc.py  —  XCP-on-UDP Server côté WC (rpisimulator27)
================================================================

3 variables XCP boolean — une par DTC WC :
  0x20010000  wc_internal_fault        uint8 bool → B2101
  0x20010001  wc_motor_driver_fault    uint8 bool → B2102
  0x20010002  wc_position_sensor_fault uint8 bool → B2103

Intégration sans modifier le fonctionnement de bcmcan :
  XCP écrit dans _sensor_state.xcp_* (nouvelles variables ajoutées dans
  SensorState). Les fonctions de détection bcmcan font OR :
    B2101 : elapsed > _CAN_TIMEOUT_WC  OR  xcp_internal_fault
    B2102 : motor_driver_fault         OR  xcp_motor_driver_fault
    B2103 : blade mismatch             OR  xcp_position_sensor_fault

Port : UDP 17726  (BCM = 17725)
"""

from __future__ import annotations

import json
import os
import socket
import struct
import threading
import time
import logging
from typing import Optional

HOST        = "0.0.0.0"
PORT        = 17726
MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory_wc.json")

CMD_CONNECT        = 0xFF
CMD_DISCONNECT     = 0xFE
CMD_STATUS         = 0xFD
CMD_SHORT_DOWNLOAD = 0xED

RES_OK  = bytes([0xFF])
RES_ERR = bytes([0xFE])

ADDR_WC_INTERNAL_FAULT       = 0x20010000
ADDR_WC_MOTOR_DRIVER_FAULT   = 0x20010001
ADDR_WC_POSITION_SENSOR_FAULT = 0x20010002

POLL_PERIOD_S = 0.050

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [XCP-WC] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("xcp_server_wc")


def _load_memory() -> dict:
    if not os.path.exists(MEMORY_FILE):
        return {}
    try:
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_memory(mem: dict) -> None:
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(mem, f, indent=2)
    except Exception as e:
        logger.warning(f"memory_wc.json write error: {e}")

def _mem_get(mem: dict, addr: int, default: int = 0) -> int:
    return mem.get(f"0x{addr:08X}", default)

def _mem_set(mem: dict, addr: int, value: int) -> None:
    mem[f"0x{addr:08X}"] = value


def _get_sensor_state():
    """Retourne bcmcan._sensor_state si bcmcan est chargé."""
    import sys
    bcmcan = sys.modules.get("bcmcan", None)
    if bcmcan is None:
        return None
    return getattr(bcmcan, "_sensor_state", None)


class _DTCEngineXCP_WC:
    """
    Lit memory_wc.json toutes les 50ms.
    Sur changement de valeur, écrit dans _sensor_state.xcp_*
    pour que bcmcan déclenche/guérisse les DTCs via son OR naturel.
    """

    def __init__(self, mem_lock: threading.Lock):
        self._lock    = mem_lock
        self._running = False
        # Dernières valeurs traitées (latch — réagit seulement au changement)
        self._last_internal_fault    : int = 0
        self._last_motor_driver_fault: int = 0
        self._last_position_fault    : int = 0

    def run(self):
        logger.info("T-XCP-WC-DTC demarre (periode=50ms)")
        self._running = True
        while self._running:
            try:
                with self._lock:
                    mem = _load_memory()
                self._evaluate(mem)
            except Exception as e:
                logger.error(f"T-XCP-WC-DTC erreur: {e}")
            time.sleep(POLL_PERIOD_S)

    def stop(self):
        self._running = False

    def _evaluate(self, mem: dict):
        self._handle_b2101(mem)
        self._handle_b2102(mem)
        self._handle_b2103(mem)

    def _handle_b2101(self, mem: dict):
        val = _mem_get(mem, ADDR_WC_INTERNAL_FAULT, default=0)
        if val == self._last_internal_fault:
            return
        sensor = _get_sensor_state()
        if sensor is not None:
            sensor.set_xcp_internal_fault(bool(val))
            logger.info(f"[B2101] xcp_internal_fault={bool(val)} -> OR bcmcan B2101")
        self._last_internal_fault = val

    def _handle_b2102(self, mem: dict):
        val = _mem_get(mem, ADDR_WC_MOTOR_DRIVER_FAULT, default=0)
        if val == self._last_motor_driver_fault:
            return
        sensor = _get_sensor_state()
        if sensor is not None:
            sensor.set_xcp_motor_driver_fault(bool(val))
            logger.info(f"[B2102] xcp_motor_driver_fault={bool(val)} -> OR bcmcan B2102")
        self._last_motor_driver_fault = val

    def _handle_b2103(self, mem: dict):
        val = _mem_get(mem, ADDR_WC_POSITION_SENSOR_FAULT, default=0)
        if val == self._last_position_fault:
            return
        sensor = _get_sensor_state()
        if sensor is not None:
            sensor.set_xcp_position_sensor_fault(bool(val))
            logger.info(f"[B2103] xcp_position_sensor_fault={bool(val)} -> OR bcmcan B2103")
        self._last_position_fault = val


class XCPServerWC:

    def __init__(self, host: str = HOST, port: int = PORT):
        self._host   = host
        self._port   = port
        self._lock   = threading.Lock()
        self._engine: Optional[_DTCEngineXCP_WC] = None
        self._init_memory()

    def _init_memory(self):
        if not os.path.exists(MEMORY_FILE):
            defaults = {
                f"0x{ADDR_WC_INTERNAL_FAULT:08X}":        0,
                f"0x{ADDR_WC_MOTOR_DRIVER_FAULT:08X}":    0,
                f"0x{ADDR_WC_POSITION_SENSOR_FAULT:08X}": 0,
            }
            try:
                with open(MEMORY_FILE, "w") as f:
                    json.dump(defaults, f, indent=2)
                logger.info(f"memory_wc.json cree : {MEMORY_FILE}")
            except Exception as e:
                logger.warning(f"Impossible de creer memory_wc.json : {e}")

    def run(self):
        self._engine = _DTCEngineXCP_WC(self._lock)
        threading.Thread(
            target=self._engine.run, daemon=True, name="T-XCP-WC-DTC"
        ).start()

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1.0)  # permet au Ctrl+C de sortir proprement
        sock.bind((self._host, self._port))
        logger.info(f"XCP Server WC en ecoute sur {self._host}:{self._port}")

        with self._lock:
            memory = _load_memory()

        try:
            while True:
                try:
                    data, addr = sock.recvfrom(1024)
                except socket.timeout:
                    continue  # timeout -> verifier si on doit arreter
                if not data:
                    continue
                cmd = data[0]
                if cmd == CMD_CONNECT:
                    logger.info(f"[XCP-WC] CONNECT depuis {addr}")
                    sock.sendto(RES_OK, addr)
                elif cmd == CMD_DISCONNECT:
                    logger.info(f"[XCP-WC] DISCONNECT depuis {addr}")
                    sock.sendto(RES_OK, addr)
                elif cmd == CMD_STATUS:
                    sock.sendto(RES_OK, addr)
                elif cmd == CMD_SHORT_DOWNLOAD:
                    sock.sendto(self._handle_short_download(data, addr, memory), addr)
                else:
                    logger.warning(f"[XCP-WC] Commande inconnue: 0x{cmd:02X}")
                    sock.sendto(RES_ERR, addr)
        except KeyboardInterrupt:
            logger.info("XCP Server WC arrete")
        finally:
            if self._engine:
                self._engine.stop()
            sock.close()

    def _handle_short_download(self, data: bytes, addr, memory: dict) -> bytes:
        try:
            if len(data) < 8:
                raise ValueError(f"Paquet trop court: {len(data)}")
            size     = data[1]
            addr_val = struct.unpack("<I", data[4:8])[0]
            val_bytes = data[8:8 + size]
            if len(val_bytes) != size:
                raise ValueError("Donnees incompletes")
            if size == 1:
                value = struct.unpack("B",  val_bytes)[0]
            elif size == 2:
                value = struct.unpack("<H", val_bytes)[0]
            elif size == 4:
                value = struct.unpack("<I", val_bytes)[0]
            else:
                raise ValueError(f"Taille non supportee: {size}")
            with self._lock:
                _mem_set(memory, addr_val, value)
                _save_memory(memory)
            logger.info(
                f"[XCP-WC WRITE] {self._addr_name(addr_val)} "
                f"@ 0x{addr_val:08X} = {value}"
            )
            return RES_OK
        except Exception as e:
            logger.error(f"[XCP-WC SHORT_DOWNLOAD] Erreur: {e}")
            return RES_ERR

    @staticmethod
    def _addr_name(addr: int) -> str:
        return {
            ADDR_WC_INTERNAL_FAULT       : "wc_internal_fault",
            ADDR_WC_MOTOR_DRIVER_FAULT   : "wc_motor_driver_fault",
            ADDR_WC_POSITION_SENSOR_FAULT: "wc_position_sensor_fault",
        }.get(addr, f"unknown@0x{addr:08X}")


if __name__ == "__main__":
    print("=" * 60)
    print("  XCP Server WC — mode standalone")
    print(f"  Port UDP       : {PORT}")
    print(f"  memory_wc.json : {MEMORY_FILE}")
    print()
    print(f"  0x{ADDR_WC_INTERNAL_FAULT:08X}  wc_internal_fault        (bool) -> OR B2101")
    print(f"  0x{ADDR_WC_MOTOR_DRIVER_FAULT:08X}  wc_motor_driver_fault    (bool) -> OR B2102")
    print(f"  0x{ADDR_WC_POSITION_SENSOR_FAULT:08X}  wc_position_sensor_fault (bool) -> OR B2103")
    print("=" * 60)
    XCPServerWC().run()