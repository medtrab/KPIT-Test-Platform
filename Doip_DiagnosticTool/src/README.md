# DoIP/UDS ECU Simulator & Diagnostic Client – Source Code Documentation

This directory (`src/`) contains the complete production implementation of the DoIP (ISO 13400-2) and UDS (ISO 14229-1) ECU simulator and diagnostic client. It represents the core logic layer of the project, including protocol encoding/decoding, ECU state management, UDS service handling, routing activation, diagnostic client interaction, and centralised logging configuration. The entire implementation relies exclusively on the Python 3 standard library. No external dependencies are required.

## 📁 Source Code Structure

```
src/
├── doip_protocol.py        # DoIP protocol definitions and helpers
├── ecu_server.py           # ECU simulator (Raspberry Pi side)
├── diagnostic_client.py    # Diagnostic tester (PC side)
└── logging_config.py       # Centralised logging configuration (ASPICE-compliant)
```


Each file has a clearly defined responsibility and must remain logically separated.

📦 Module Responsibilities

1️⃣ doip_protocol.py – DoIP Protocol Layer

This module centralizes all ISO 13400-2 protocol definitions and low-level utilities. It contains DoIP protocol constants (protocol version 0x02 and inverse 0xFD, payload types for Vehicle Identification, Routing Activation, and Diagnostic Payload, logical addresses Tester 0x0E00 and ECU 0x0E01), hexadecimal conversion utilities (hex_str_to_bytes() and bytes_to_hex_str()), and DoIP message construction helpers (build_doip_header(), send_doip_message(), receive_doip_message()). This file contains no ECU logic. It strictly handles message formatting and decoding. Both ecu_server.py and diagnostic_client.py import from this module.

2️⃣ ecu_server.py – ECU Simulator (ISO 14229 Logic)

This file implements the full ECU simulation logic.

🔹 ECUState Class  
The ECUState class holds all dynamic ECU state information, including current diagnostic session, security level, stored DTCs, flash memory buffer, transfer state (download/upload), routine control state, protected Data Identifiers (DIDs), routing activation status, and session timing parameters. This structure simulates how a real automotive ECU maintains internal diagnostic state.

🔹 UDSECU Class  
The UDSECU class implements the UDS service dispatcher. Its main entry point is:

handle_uds(uds: bytes) -> bytes

This method processes UDS requests and returns either a positive response (SID + 0x40) or a negative response (0x7F + SID + NRC). The supported services are:

0x10 Diagnostic Session Control  
0x11 ECU Reset  
0x14 Clear Diagnostic Information  
0x19 Read DTC Information  
0x22 Read Data by Identifier  
0x27 Security Access  
0x31 Routine Control  
0x34 Request Download  
0x35 Request Upload  
0x36 Transfer Data  
0x37 Transfer Exit  

All Negative Response Codes (NRCs) follow ISO 14229-1.

🔹 Networking Layer  
The ECU runs two servers. The UDP Discovery Server listens on port 13400, responds to Vehicle Identification Requests (0x0001), and sends VIN, logical address, and IP information. The TCP Diagnostic Server accepts one client at a time, handles routing activation (0x0005 / 0x0006), processes DoIP diagnostic payloads (0x8001), and forwards UDS payloads to UDSECU.handle_uds(). Routing activation must be successful before diagnostics are allowed.

3️⃣ diagnostic_client.py – Diagnostic Tester

This file implements a DoIP tester running on a PC.

It performs ECU discovery by broadcasting a Vehicle Identification Request over UDP and collecting discovered ECUs. It performs routing activation by opening a TCP connection, sending a routing activation request, and validating the activation response. It handles UDS communication by sending UDS requests wrapped in a DoIP diagnostic payload, receiving and parsing responses, displaying positive and negative responses, and interpreting NRC meanings. It provides an interactive console with the following commands: discover, connect <IP>, status, help, examples, any UDS hexadecimal command (e.g., 10 03, 22 F1 90), and quit. The client contains no ECU logic and acts strictly as a tester.

4️⃣ logging_config.py – Centralised Logging Infrastructure

This module provides a production-grade, ASPICE-ready logging infrastructure used by both the ECU simulator and the diagnostic client. It implements a structured log format: YYYY-MM-DD HH:MM:SS,mmm | LEVEL | module.name | function_name | message. It automatically creates the ./logs/ directory, configures per-module file handlers with rotation (1 MB size, 3 backups) for doip_protocol.log, ecu_server.log, and diagnostic_client.log, and provides console output (INFO level and above) with identical formatting. The setup_logging() function is idempotent and safe to call multiple times. If the log directory is not writable, the system falls back to console-only logging. The configuration works with direct execution, python -m execution, IDE launches, and absolute paths. This module contains no functional logic and serves purely as infrastructure.

🔁 Execution Roles

ecu_server.py runs on Raspberry Pi 3 and acts as the ECU.  
diagnostic_client.py runs on a PC and acts as the tester.  
doip_protocol.py is shared protocol infrastructure.  
logging_config.py is shared logging infrastructure.  

The roles must never be swapped.

🔍 Internal Behavior Summary

DTCs are stored internally in a dictionary. Static DIDs (0xF190–0xF194) return predefined data. Protected DIDs require the correct session and security level. Security Access uses fixed seed values and deterministic inverse key validation logic. Download and upload operations use an internal flash bytearray. Transfer Data enforces block counter sequencing. Routine Control simulates routine start, stop, and result behavior. Only one active TCP client is allowed at a time. P2 and P2* timing values are returned in session control responses.

🛡 Architectural Principles

Strict separation between protocol and logic. Deterministic simulation behavior. ISO-aligned message formatting. Thread-safe ECU state handling. Centralised, industrial-grade logging infrastructure. No external dependencies. Fully testable logic layer used by the tests/ directory.

📄 Requirements

Python 3.7 or newer.  
Standard library only.  

requirements.txt is intentionally empty.
