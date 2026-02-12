#!/usr/bin/env python3
"""
DoIP/UDS Diagnostic Client (ISO 13400 / ISO 14229)

This program acts as a diagnostic tester that communicates with a DoIP ECU
simulator. It supports ECU discovery, routing activation, and sending UDS
requests interactively.

Behaviour is 100% identical to the original simulation. Only structure,
documentation and logging have been improved.
"""

import socket
import struct
import time
import logging
from typing import Optional, List, Dict, Any
import logging_config


# Import shared DoIP protocol definitions
from doip_protocol import (
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

    broadcast_addr = "192.168.8.255"
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
    logger.info(f"\nConnecting to ECU at {ecu_info['ip']}:13400")

    tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_sock.settimeout(10.0)

    try:
        tcp_sock.connect((ecu_info['ip'], 13400))
        logger.info(f"Connected to ECU at {ecu_info['ip']}:13400")

        logger.info("DoIP routing activation...")
        routing_payload = struct.pack(">H B 4s", LOGICAL_ADDR_TESTER, 0x00, b'\x00\x00\x00\x00')
        send_doip_message(tcp_sock, DOIP_ROUTING_ACTIVATION_REQUEST, routing_payload)

        payload_type, payload = receive_doip_message(tcp_sock, 5.0)

        if payload_type == DOIP_ROUTING_ACTIVATION_RESPONSE and payload:
            if len(payload) >= 3:
                client_addr, response_code = struct.unpack(">H B", payload[:3])
                if response_code == 0x00:
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
# UDS Request over DoIP
# =============================================================================
def send_uds_request(sock: socket.socket, uds_payload: bytes) -> bytes:
    """
    Send a UDS request via DoIP and return the UDS response.

    Args:
        sock: Connected TCP socket with routing activated.
        uds_payload: Raw UDS request bytes.

    Returns:
        UDS response payload (without DoIP headers), or empty bytes on error.
    """
    doip_payload = struct.pack(">HH", LOGICAL_ADDR_TESTER, LOGICAL_ADDR_ECU) + uds_payload
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
    """Display interactive command help."""
    logger.info("\n" + "=" * 60)
    logger.info("UDS CLIENT INTERACTIVE WITH DoIP ROUTING")
    logger.info("=" * 60)
    logger.info("\nCOMMAND FORMAT:")
    logger.info("  Enter hex bytes, separated by spaces or without spaces.")
    logger.info("  Examples:")
    logger.info("    10 02        → Diagnostic Session Control (Programming)")
    logger.info("    27 01        → Security Access Request Seed")
    logger.info("    22 F1 90     → Read Data by Identifier 0xF190")
    logger.info("    34 00 44 00 00 00 00 00 00 00 18 → Request Download")
    logger.info("\nSPECIAL COMMANDS:")
    logger.info("  discover      : Discover available ECUs")
    logger.info("  connect [ip]  : Connect to a specific ECU")
    logger.info("  status        : Show connection status")
    logger.info("  examples      : Show UDS request examples")
    logger.info("  help          : Show this help")
    logger.info("  quit / exit   : Quit the program")
    logger.info("\nSUPPORTED UDS SERVICES:")
    logger.info("  0x10 : Diagnostic Session Control")
    logger.info("  0x11 : ECU Reset")
    logger.info("  0x14 : Clear Diagnostic Information")
    logger.info("  0x19 : Read DTC Information")
    logger.info("  0x22 : Read Data by Identifier")
    logger.info("  0x27 : Security Access")
    logger.info("  0x31 : Routine Control")
    logger.info("  0x34 : Request Download")
    logger.info("  0x35 : Request Upload")
    logger.info("  0x36 : Transfer Data")
    logger.info("  0x37 : Transfer Exit")
    logger.info("=" * 60)


def print_examples() -> None:
    """Display example UDS requests."""
    logger.info("\n" + "=" * 60)
    logger.info("UDS REQUEST EXAMPLES")
    logger.info("=" * 60)
    logger.info("\n1. SESSION CONTROL:")
    logger.info("  10 01          → Default Session")
    logger.info("  10 02          → Programming Session")
    logger.info("  10 03          → Extended Diagnostic Session")
    logger.info("\n2. SECURITY ACCESS:")
    logger.info("  27 01          → Request Seed (level 1)")
    logger.info("  27 02 AA 55 CC 33 → Send Key (4 bytes)")
    logger.info("  27 03          → Request Seed (level 2)")
    logger.info("  27 04 11 22 33 44 → Send Key (level 2)")
    logger.info("\n3. ECU RESET:")
    logger.info("  11 01          → Hard Reset")
    logger.info("  11 02          → Soft Reset")
    logger.info("  11 03          → Electrical Restart")
    logger.info("\n4. DTC MANAGEMENT:")
    logger.info("  14 00 00 00    → Clear all DTCs")
    logger.info("  14 12 00 00    → Clear DTCs of group 0x12")
    logger.info("  19 01 FF       → Report DTC by status mask (all)")
    logger.info("  19 0A 01       → Report number of DTC with TestFailed bit")
    logger.info("\n5. DATA IDENTIFIERS:")
    logger.info("  22 F1 90       → Read VIN (0xF190)")
    logger.info("  22 F1 90 F1 91 → Read multiple identifiers")
    logger.info("  22 F1 92       → Read software version")
    logger.info("\n6. ROUTINE CONTROL:")
    logger.info("  31 01 02 02    → Start Routine 0x0202")
    logger.info("  31 03 02 02    → Request Results of routine 0x0202")
    logger.info("  31 02 02 02    → Stop Routine 0x0202")
    logger.info("\n7. DOWNLOAD/UPLOAD:")
    logger.info("  34 00 44 00 00 00 00 00 00 00 18 → Request Download")
    logger.info("  35 00 44 00 00 00 00 00 00 00 18 → Request Upload")
    logger.info("  36 01 AA BB CC DD EE FF 11 22 → Transfer Data block 1")
    logger.info("  36 02                         → Transfer Data (upload)")
    logger.info("  37                            → Transfer Exit")
    logger.info("=" * 60)


# =============================================================================
# Main Interactive Loop
# =============================================================================
def main() -> None:
    """Main diagnostic client entry point."""
    logging_config.setup_logging()
    current_socket: Optional[socket.socket] = None
    current_ecu: Optional[Dict[str, Any]] = None

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
                    ecu_info = {'ip': ip, 'vin': 'Unknown', 'logical_address': LOGICAL_ADDR_ECU}
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

                logger.info(f"[→] Send: {bytes_to_hex_str(uds_request)}")
                uds_response = send_uds_request(current_socket, uds_request)

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