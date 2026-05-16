#!/usr/bin/env python3
"""
DoIP/UDS Diagnostic Client (ISO 13400 / ISO 14229)
Extended to support target ECU selection (BCM 0x0700 / WC 0x0701)

This program acts as a diagnostic tester that communicates with a DoIP ECU
simulator. It supports ECU discovery, routing activation, and sending UDS
requests interactively, allowing the user to choose the target ECU (BCM or WC)
at runtime.

Behaviour is 100% identical to the original simulation, except for the added
"""

import socket
import struct
import time
import logging
from typing import Optional, List, Dict, Any

from core import logging_config
from core.logging_config import setup_logging
# Import shared DoIP protocol definitions
from core.transport.doip_protocol import (
    DOIP_PROTOCOL_VERSION, DOIP_INVERSE_VERSION,
    DOIP_VEHICLE_IDENTIFICATION_REQUEST, DOIP_VEHICLE_IDENTIFICATION_RESPONSE,
    DOIP_ROUTING_ACTIVATION_REQUEST, DOIP_ROUTING_ACTIVATION_RESPONSE,
    DOIP_PAYLOAD_DIAG,
    LOGICAL_ADDR_TESTER, LOGICAL_ADDR_ECU,
    hex_str_to_bytes, bytes_to_hex_str,
    send_doip_message, receive_doip_message, build_doip_header
)

# -----------------------------------------------------------------------------
# Logging configuration (preserves original console output)
# -----------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# =============================================================================
# Target ECU logical addresses
# =============================================================================
LOGICAL_ADDR_BCM = 0x0700
LOGICAL_ADDR_WC  = 0x0701

# Port TCP selon l'ECU cible
DOIP_PORT_BCM = 13400   # RPi BCM
DOIP_PORT_WC  = 13400   # RPi Simulateur (WC) — port standard DoIP, IP 10.20.0.7

# IP fixe du RPi Simulateur -- WC ECU
WC_ECU_IP = "10.20.0.7"

# =============================================================================
# ECU Discovery (UDP broadcast)
# =============================================================================
def discover_ecus() -> List[Dict[str, Any]]:
    """
    Broadcast a DoIP vehicle identification request and collect responses.

    Returns:
        List of dictionaries, each containing discovered ECU info:
        'ip', 'vin', 'logical_address', 'address' (tuple).
    """
    logger.info("\n" + "=" * 60)
    logger.info("DoIP ECU DISCOVERY")
    logger.info("=" * 60)

    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    udp_sock.settimeout(2.0)

    # Build discovery request (empty payload)
    header = build_doip_header(DOIP_VEHICLE_IDENTIFICATION_REQUEST, 0)
    message = header + b""

    broadcast_addr = "255.255.255.255"
    logger.info(f"Sending discovery broadcast to {broadcast_addr}:13400")
    udp_sock.sendto(message, (broadcast_addr, 13400))

    ecus = []
    start_time = time.time()
    while time.time() - start_time < 2.0:
        try:
            data, addr = udp_sock.recvfrom(4096)
            if len(data) >= 8:
                proto_ver, inv_ver, payload_type = struct.unpack(">BBH", data[:4])
                payload_len = struct.unpack(">I", data[4:8])[0]

                if (proto_ver == DOIP_PROTOCOL_VERSION and
                    inv_ver == DOIP_INVERSE_VERSION and
                    payload_type == DOIP_VEHICLE_IDENTIFICATION_RESPONSE and
                    len(data) >= 8 + payload_len):

                    payload = data[8:8 + payload_len]

                    if len(payload) >= 17:
                        vin = payload[:17].decode('ascii', errors='ignore')
                        logical_address = struct.unpack(">H", payload[17:19])[0] if len(payload) >= 19 else 0x0000
                        ip_address = addr[0]

                        ecu_info = {
                            'ip': ip_address,
                            'vin': vin,
                            'logical_address': logical_address,
                            'address': addr
                        }
                        ecus.append(ecu_info)
                        logger.info(f"ECU discovered: {vin} at {ip_address} (Logical address: 0x{logical_address:04X})")

        except socket.timeout:
            break
        except Exception as e:
            logger.error(f"Error during discovery: {e}")
            continue

    udp_sock.close()
    return ecus


# =============================================================================
# Routing Activation
# =============================================================================
def connect_to_ecu(ecu_info: Dict[str, Any]) -> Optional[socket.socket]:
    """
    Establish a TCP connection to an ECU and perform DoIP routing activation.

    Args:
        ecu_info: Dictionary with at least 'ip' key.

    Returns:
        Connected TCP socket with routing activated, or None on failure.
    """
    # Choisir le port TCP selon l'adresse logique de l'ECU
    logical_addr = ecu_info.get('logical_address', LOGICAL_ADDR_BCM)
    port = DOIP_PORT_WC if logical_addr == LOGICAL_ADDR_WC else DOIP_PORT_BCM

    logger.info(f"\nConnecting to ECU at {ecu_info['ip']}:{port}")

    tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_sock.settimeout(10.0)

    try:
        tcp_sock.connect((ecu_info['ip'], port))
        logger.info(f"Connected to ECU at {ecu_info['ip']}:{port}")

        logger.info("DoIP routing activation...")
        routing_payload = struct.pack(">H B 4s", LOGICAL_ADDR_TESTER, 0x00, b'\x00\x00\x00\x00')
        send_doip_message(tcp_sock, DOIP_ROUTING_ACTIVATION_REQUEST, routing_payload)

        payload_type, payload = receive_doip_message(tcp_sock, 5.0)

        if payload_type == DOIP_ROUTING_ACTIVATION_RESPONSE and payload:
            # ISO 13400-2 format : [tester(2)][ecu(2)][response_code(1)][reserved(4)]
            if len(payload) >= 5:
                client_addr = struct.unpack(">H", payload[0:2])[0]
                response_code = payload[4]  # byte 4 = response code ISO 13400-2
                # 0x10 = routing activated
                if response_code == 0x10:
                    logger.info("Routing activated successfully!")
                    return tcp_sock
                else:
                    logger.error(f"Routing activation failed, code: 0x{response_code:02X}")
                    tcp_sock.close()
                    return None
            else:
                logger.error("Invalid activation response payload")
                tcp_sock.close()
                return None
        else:
            logger.error("No response to routing activation request")
            tcp_sock.close()
            return None

    except Exception as e:
        logger.error(f"Connection error: {e}")
        if tcp_sock:
            tcp_sock.close()
        return None


# =============================================================================
# UDS Request over DoIP (with selectable target logical address)
# =============================================================================
def send_uds_request(sock: socket.socket, uds_payload: bytes, target_addr: int) -> bytes:
    """
    Send a UDS request via DoIP to the specified target logical address
    and return the UDS response.

    Args:
        sock: Connected TCP socket with routing activated.
        uds_payload: Raw UDS request bytes.
        target_addr: Target logical address (e.g., 0x0700 or 0x0701).

    Returns:
        UDS response payload (without DoIP headers), or empty bytes on error.
    """
    doip_payload = struct.pack(">HH", LOGICAL_ADDR_TESTER, target_addr) + uds_payload
    send_doip_message(sock, DOIP_PAYLOAD_DIAG, doip_payload)

    payload_type, payload = receive_doip_message(sock, 10.0)

    if payload_type == DOIP_PAYLOAD_DIAG and payload:
        if len(payload) >= 4:
            return payload[4:]   # strip source/destination addresses
    return b""


# =============================================================================
# Interactive Helpers
# =============================================================================
def print_help() -> None:
    """Display interactive command help with Wipe & Wash system context."""
    logger.info("\n" + "=" * 60)
    logger.info("UDS DIAGNOSTIC CLIENT – WIPE & WASH SYSTEM")
    logger.info("=" * 60)
    logger.info("\nVEHICLE ARCHITECTURE:")
    logger.info("  BCM (Body Control Module)      : 0x0700")
    logger.info("  WC  (Wiper Controller, optional): 0x0701")
    logger.info("  → WC responds only if coding F201 (WcAvailable) = 1")
    logger.info("")
    logger.info("SESSION & SECURITY PRECONDITIONS:")
    logger.info("  • 0x2E (Write Data / Coding)   : Extended session + Security level 2")
    logger.info("  • 0x31 (Routine Control)       : Extended session + Security level 1")
    logger.info("  • 0x28 (Communication Control) : Extended session (no security)")
    logger.info("  • 0x14 (Clear DTC)             : Extended session + Security level 2")
    logger.info("  • 0x19 (Read DTC)               : Available in any session")
    logger.info("  • 0x22 (Read Data)              : Available in any session")
    logger.info("")
    logger.info("COMMAND FORMAT:")
    logger.info("  Enter hex bytes, separated by spaces or without spaces.")
    logger.info("  Examples:")
    logger.info("    10 03        → Switch to Extended Diagnostic Session (BCM)")
    logger.info("    27 01        → Request Seed for Security Level 1 (BCM)")
    logger.info("    27 02 AA 55 CC 33 → Send Key for Level 1 (BCM)")
    logger.info("    2E F2 01 01  → Enable WC (write coding 0xF201 = 1) – BCM")
    logger.info("    22 F1 00     → Read WiperCurrentMode (DID 0xF100) – BCM")
    logger.info("    19 02 FF     → Read all confirmed DTCs – any target")
    logger.info("\nSPECIAL COMMANDS:")
    logger.info("  discover      : Discover available ECUs")
    logger.info("  connect [ip]  : Connect to a specific ECU")
    logger.info("  target        : Show current target ECU")
    logger.info("  target bcm    : Set target to BCM (0x0700)")
    logger.info("  target wc     : Set target to WC (0x0701)")
    logger.info("  target 0x700  : Set target by hex address (0x700 or 0x701)")
    logger.info("  status        : Show connection status and target")
    logger.info("  examples      : Show realistic UDS request examples")
    logger.info("  help          : Show this help")
    logger.info("  quit / exit   : Quit the program")
    logger.info("\nSUPPORTED UDS SERVICES (per ECU):")
    logger.info("  BCM (0x0700) : 0x10, 0x11, 0x14, 0x19, 0x22, 0x27, 0x28, 0x2E, 0x31, 0x3E")
    logger.info("  WC  (0x0701) : 0x10, 0x19, 0x22, 0x31, 0x3E")
    logger.info("=" * 60)


def print_examples() -> None:
    """Display realistic UDS request examples for Wipe & Wash system."""
    logger.info("\n" + "=" * 60)
    logger.info("REALISTIC UDS EXAMPLES – WIPE & WASH")
    logger.info("=" * 60)

    logger.info("\n▶ SESSION & SECURITY (target = BCM)")
    logger.info("  10 03          → Extended Diagnostic Session (required for coding/routines)")
    logger.info("  27 01          → Request Seed (Security Level 1 – diagnostic)")
    logger.info("  27 02 AA 55 CC 33 → Send Key (Level 1) – unlocks Level 1")
    logger.info("  27 03          → Request Seed (Security Level 2 – coding)")
    logger.info("  27 04 44 33 22 11 → Send Key (Level 2) – unlocks Level 2")

    logger.info("\n▶ CODING (target = BCM, requires Extended session + Level 2)")
    logger.info("  2E F2 00 01    → Enable RainSensorInstalled (F200 = 1)")
    logger.info("  2E F2 01 01    → Enable WC (F201 = 1) – makes WC accessible")
    logger.info("  2E F2 02 00    → Disable RearWiper (F202 = 0)")
    logger.info("  2E F2 03 01    → Set ChannelFrontWash to Backward (F203 = 1)")

    logger.info("\n▶ ROUTINE CONTROL – ACTUATOR TESTS (target = BCM, Extended + Level 1)")
    logger.info("  31 01 02 01 0A → Start Front Wiper Test for 10 seconds (0x0201, duration 10)")
    logger.info("  31 03 02 01    → Request results of Front Wiper Test (returns echo)")
    logger.info("  31 02 02 01    → Stop Front Wiper Test")
    logger.info("  31 01 02 05 64 → Rain Sensor Simulation – set intensity 100 (0x0205, value 0x64)")

    logger.info("\n▶ DATA IDENTIFIERS – SYSTEM STATUS (target = BCM)")
    logger.info("  22 F1 00       → Read WiperCurrentMode (DID 0xF100)")
    logger.info("  22 F1 03       → Read MotorCurrent (2 bytes)")
    logger.info("  22 F1 04       → Read PumpStatus")

    logger.info("\n▶ DTC OPERATIONS (target = BCM or WC)")
    logger.info("  19 02 FF       → Report all DTCs by status mask (FF = all status bits)")
    logger.info("  19 04 12 20 01 01 → Read snapshot record 1 for DTC B2001 (0x122001)")
    logger.info("  19 04 12 21 01 00 → Get number of snapshot records for B2101 (WC DTC)")
    logger.info("  19 06 12 20 01 01 → Read extended data record 1 for B2001")
    logger.info("  14 00 00 00    → Clear all DTCs (BCM only, requires Extended + Level 2)")

    logger.info("\n▶ WC ECU OPERATIONS (target must be switched to WC)")
    logger.info("  target wc      → Switch target to WC")
    logger.info("  19 02 FF       → Read WC DTCs (B2101, B2102, B2103)")
    logger.info("  19 04 12 21 01 01 → Read snapshot for B2101")
    logger.info("  31 01 02 01 05 → Start routine (if WC supports any – check spec)")
    logger.info("  22 F1 00       → Read data (WC may support some DIDs, otherwise NRC 0x31)")

    logger.info("=" * 60)


# =============================================================================
# Main Interactive Loop
# =============================================================================
def main() -> None:
    """Main diagnostic client entry point."""
    logging_config.setup_logging()
    current_socket: Optional[socket.socket] = None
    current_ecu: Optional[Dict[str, Any]] = None
    current_target_addr: int = LOGICAL_ADDR_BCM   # default to BCM

    print_help()

    while True:
        try:
            command = input("\nUDS> ").strip()

            # -----------------------------------------------------------------
            # Special commands
            # -----------------------------------------------------------------
            if command.lower() in ["quit", "exit", "q"]:
                if current_socket:
                    current_socket.close()
                logger.info("Disconnecting...")
                break

            elif command.lower() == "help":
                print_help()
                continue

            elif command.lower() == "status":
                if current_socket and current_ecu:
                    logger.info(f"Connected to ECU: {current_ecu['ip']}")
                    logger.info(f"VIN: {current_ecu['vin']}")
                    logger.info(f"Logical address: 0x{current_ecu['logical_address']:04X}")
                else:
                    logger.info("Not connected to any ECU")
                target_name = "BCM" if current_target_addr == LOGICAL_ADDR_BCM else "WC" if current_target_addr == LOGICAL_ADDR_WC else f"0x{current_target_addr:04X}"
                logger.info(f"Current target: {target_name} (0x{current_target_addr:04X})")
                continue

            elif command.lower() == "examples":
                print_examples()
                continue

            elif command.lower() == "discover":
                ecus = discover_ecus()
                if ecus:
                    logger.info(f"\n{len(ecus)} ECU(s) discovered")
                    for i, ecu in enumerate(ecus):
                        logger.info(f"{i}: {ecu['vin']} at {ecu['ip']}")
                else:
                    logger.info("No ECU discovered")
                continue

            elif command.lower().startswith("connect "):
                parts = command.split()
                if len(parts) >= 2:
                    ip = parts[1]
                    ecu_info = {'ip': ip, 'vin': 'Unknown', 'logical_address': LOGICAL_ADDR_BCM}  # placeholder
                    sock = connect_to_ecu(ecu_info)
                    if sock:
                        if current_socket:
                            current_socket.close()
                        current_socket = sock
                        current_ecu = ecu_info
                        logger.info(f"Successfully connected to {ip}")
                else:
                    logger.info("Usage: connect [ip_address]")
                continue

            elif command.lower().startswith("target"):
                parts = command.split()
                if len(parts) == 1:
                    # show current target
                    target_name = "BCM" if current_target_addr == LOGICAL_ADDR_BCM else "WC" if current_target_addr == LOGICAL_ADDR_WC else f"0x{current_target_addr:04X}"
                    logger.info(f"Current target: {target_name} (0x{current_target_addr:04X})")
                    continue
                target_str = parts[1].lower()
                if target_str == "bcm":
                    current_target_addr = LOGICAL_ADDR_BCM
                    logger.info(f"Target ECU set to BCM (0x{current_target_addr:04X})")
                elif target_str == "wc":
                    current_target_addr = LOGICAL_ADDR_WC
                    logger.info(f"Target ECU set to WC (0x{current_target_addr:04X})")
                else:
                    # try to parse as hex
                    try:
                        if target_str.startswith("0x"):
                            val = int(target_str, 16)
                        else:
                            val = int(target_str, 0)  # auto-detect base
                        if val in (LOGICAL_ADDR_BCM, LOGICAL_ADDR_WC):
                            current_target_addr = val
                            logger.info(f"Target ECU set to 0x{current_target_addr:04X}")
                        else:
                            logger.error("Invalid target logical address. Use 0x700 or 0x701.")
                    except ValueError:
                        logger.error("Invalid target. Use 'bcm', 'wc', or hex address.")
                continue

            elif command == "":
                continue

            # -----------------------------------------------------------------
            # UDS request
            # -----------------------------------------------------------------
            if not current_socket:
                logger.info("Not connected to an ECU. Use 'discover' then 'connect [ip]'")
                continue

            try:
                uds_request = hex_str_to_bytes(command)
                if len(uds_request) == 0:
                    logger.error("Error: Empty command")
                    continue

                logger.info(f"[→] Send to target 0x{current_target_addr:04X}: {bytes_to_hex_str(uds_request)}")
                uds_response = send_uds_request(current_socket, uds_request, current_target_addr)

                if len(uds_response) == 0:
                    logger.info("[←] Response: (empty or communication error)")
                else:
                    logger.info(f"[←] Response: {bytes_to_hex_str(uds_response)}")

                    # Analyse negative response
                    if uds_response[0] == 0x7F:
                        sid = uds_response[1]
                        nrc = uds_response[2]
                        logger.info(f"    NEGATIVE RESPONSE: SID=0x{sid:02X}, NRC=0x{nrc:02X}")

                        nrc_messages = {
                            0x11: "Service not supported",
                            0x12: "Subfunction not supported",
                            0x13: "Incorrect message length or invalid format",
                            0x22: "Conditions not correct",
                            0x24: "Request sequence error",
                            0x31: "Request out of range",
                            0x33: "Security access denied",
                            0x35: "Invalid key",
                            0x36: "Exceeded number of attempts",
                            0x37: "Required time delay not expired",
                            0x70: "Upload/download not accepted",
                            0x71: "Transfer data suspended",
                            0x72: "General programming failure",
                            0x73: "Wrong block sequence counter",
                            0x78: "Request correctly received - response pending",
                            0x7E: "Subfunction not supported in active session",
                            0x7F: "Service not supported in active session"
                        }

                        if nrc in nrc_messages:
                            logger.info(f"    Meaning: {nrc_messages[nrc]}")
                    else:
                        positive_sid = uds_response[0]
                        original_sid = positive_sid - 0x40 if positive_sid >= 0x40 else positive_sid
                        logger.info(f"    POSITIVE RESPONSE: SID=0x{original_sid:02X}+0x40")

            except ValueError as e:
                logger.error(f"Error: Invalid hexadecimal format - {e}")
                logger.info("Valid example: '10 02' or '1002' or '0x10 0x02'")
            except Exception as e:
                logger.error(f"Error: {e}")
                # Check if connection is still alive
                try:
                    current_socket.settimeout(1)
                    current_socket.recv(1)
                except:
                    logger.info("Connection lost")
                    current_socket.close()
                    current_socket = None
                    current_ecu = None

        except KeyboardInterrupt:
            logger.info("\n\nInterrupted by user")
            break

    if current_socket:
        current_socket.close()


if __name__ == "__main__":
    main()