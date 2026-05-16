# 🚗 Advanced Automotive Diagnostic Engineering Platform — HIL

<div align="center">

![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-blue?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.11-green?style=flat-square)
![PySide6](https://img.shields.io/badge/PySide6-6.4%2B-brightgreen?style=flat-square)
![Protocol](https://img.shields.io/badge/Protocol-DoIP%20%7C%20UDS%20%7C%20XCP-orange?style=flat-square)
![Standard](https://img.shields.io/badge/Standard-ISO%2013400%20%7C%20ISO%2014229-red?style=flat-square)
![License](https://img.shields.io/badge/License-Private-lightgrey?style=flat-square)

**Hardware In the Loop Diagnostic Platform for Wiper & Wash System ECUs**

*PFE 2026 — KPIT Engineering Sfax*

</div>

---

## 📋 Table of Contents

- [Overview](#-overview)
- [Architecture](#-architecture)
- [Network Configuration](#-network-configuration)
- [Prerequisites](#-prerequisites)
- [Installation](#-installation)
- [Running the Application](#-running-the-application)
- [Feature Overview](#-feature-overview)
- [UDS Services Reference](#-uds-services-reference)
- [HIL Coding Reference](#-hil-coding-reference)
- [Security Access](#-security-access)
- [Logging](#-logging)
- [Project Structure](#-project-structure)
- [Authors](#-authors)

---

## 🔍 Overview

This platform is a professional **Hardware In the Loop (HIL) diagnostic tool** developed for validating the Wiper & Wash system embedded ECUs. It establishes real-time communication with physical ECUs over the **DoIP transport protocol (ISO 13400)**, implements the full **UDS diagnostic stack (ISO 14229)**, and provides a modern PySide6-based engineering interface comparable to industry tools such as CANoe, ETAS INCA, and BMW ISTA.

### Key Capabilities

| Capability | Description |
|-----------|-------------|
| **DoIP Communication** | Full ISO 13400-2:2012 implementation — UDP discovery, TCP routing activation, diagnostic messaging |
| **UDS Diagnostic Stack** | 10 UDS services fully implemented — session control, security access, DTC management, coding, routines |
| **Dual ECU Support** | Simultaneous connection to BCM (0x0700) and WC (0x0701) with independent socket management |
| **XCP DTC Injection** | UDP-based XCP client for direct memory injection of DTC trigger variables |
| **Live Frame Monitor** | Real-time DoIP/UDS network capture via pyshark with hex dump and protocol decode |
| **Dark / Light Theme** | Full bidirectional theme switching with persistent state across all UI components |
| **Structured Logging** | ASPICE-compliant rotating file logs per module with console mirroring |

---

## 🏗 Architecture

```
doip-uds-simulator-HIL/
│
├── assets/                          # Static resources
│   ├── kpiy_logo.png                # KPIT Engineering logo
│   ├── splash_bg_dark.png           # Splash screen background (dark theme)
│   ├── splash_bg_light.png          # Splash screen background (light theme)
│   └── v_image.png
│
├── core/                            # Backend — communication & data layer
│   ├── constants.py                 # Logical addresses, UDS SIDs, NRC codes
│   ├── controller.py                # BackendController — Qt signal orchestration
│   ├── worker.py                    # DiagnosticWorker — DoIP/UDS socket thread
│   ├── xcp_client.py                # XCP UDP client for DTC injection
│   ├── logging_config.py            # ASPICE-style rotating file logger
│   │
│   ├── transport/                   # DoIP/UDS transport layer
│   │   ├── doip_protocol.py         # DoIP frame builder & parser (ISO 13400)
│   │   ├── diagnostic_client.py     # ECU discovery & routing activation
│   │   └── ecu_server.py            # Minimal UDS server skeleton (HIL mode)
│   │
│   └── data/                        # Static configuration data
│       ├── dtc_bcm.json             # BCM DTC database (codes, descriptions, categories)
│       ├── dtc_wc.json              # WC DTC database
│       └── xcp_variables.json       # XCP calibration variable map (address, size, range)
│
├── ui/                              # Frontend — PySide6 UI layer
│   │
│   ├── common/                      # Shared UI components
│   │   ├── top_bar.py               # ECU selector, LEDs, session/security, theme toggle
│   │   ├── sidebar.py               # Collapsible animated navigation sidebar
│   │   ├── splash_screen.py         # Branded splash screen with hero image & fade
│   │   ├── log_panel.py             # Dockable communication log — filter, export
│   │   └── styling.py               # DARK_STYLESHEET / LIGHT_STYLESHEET
│   │
│   ├── pages/                       # Application pages
│   │   ├── dashboard_page.py        # Status cards, DTC table, quick actions
│   │   ├── security_page.py         # Security Access — Seed/Key flow with animation
│   │   ├── hil_page.py              # HIL coding, routine control, system status read
│   │   ├── bcm_page.py              # BCM DTC management + Communication Control tab
│   │   ├── wc_page.py               # WC DTC management
│   │   ├── uds_console_page.py      # Raw UDS hex console with quick commands
│   │   └── actuator_lab_page.py     # XCP DTC injection lab with slider/checkbox UI
│   │
│   ├── widgets/                     # Specialized reusable widgets
│   │   └── frame_monitor_widget.py  # Live DoIP/UDS capture — pyshark, hex dump, decode
│   │
│   └── main_window.py               # MainWindow — page orchestration & signal wiring
│
├── logs/                            # Runtime logs (auto-generated, git-ignored)
│   └── .gitkeep
│
├── tests/                           # Unit & integration tests
│   └── __init__.py
│
├── docs/                            # Technical documentation
│   ├── architecture.md              # System architecture & communication flow
│   └── uds_sequences.md             # UDS sequence diagrams
│
├── main.py                          # ✅ Application entry point
├── requirements.txt                 # Python dependencies
├── .gitignore
└── README.md
```

### Communication Flow

```
┌─────────────────────────────────────────────────────┐
│                   PySide6 UI Thread                  │
│                                                       │
│  MainWindow ──► BackendController ──► Qt Signals     │
│      │              │                                 │
│   Pages &        controller.py                       │
│   Widgets                                             │
└──────────────────────┬──────────────────────────────┘
                       │  Qt Signals (thread-safe)
┌──────────────────────▼──────────────────────────────┐
│              DiagnosticWorker (QThread)               │
│                                                       │
│   Request Queue ──► Send ──► Receive ──► Dispatch    │
│                                                       │
│   ┌──────────────┐      ┌──────────────┐             │
│   │  TCP Socket  │      │  TCP Socket  │             │
│   │  BCM 13400   │      │   WC 13401   │             │
│   └──────┬───────┘      └──────┬───────┘             │
└──────────┼────────────────────┼────────────────────┘
           │  DoIP (ISO 13400)  │
┌──────────▼────────────────────▼────────────────────┐
│                   Physical ECUs                      │
│                                                       │
│   BCM — 10.20.0.25:13400    WC — 10.20.0.7:13401   │
│   Logical Addr: 0x0700      Logical Addr: 0x0701    │
└─────────────────────────────────────────────────────┘
```

---

## 🌐 Network Configuration

| Device | Role | IP Address | Port | Protocol | Logical Address |
|--------|------|-----------|------|----------|----------------|
| BCM ECU | Target | 10.20.0.25 | 13400 | DoIP/TCP | 0x0700 |
| WC ECU | Target | 10.20.0.7 | 13401 | DoIP/TCP | 0x0701 |
| XCP Server | Target | 10.20.0.25 | 17725 | XCP/UDP | — |
| Tester (PC) | Client | — | — | — | 0x07DF |

> **Note:** WC ECU is only accessible after BCM coding parameter `0xF201` (WCAvailable) is set to `1`.

---

## ⚙️ Prerequisites

- **OS:** Windows 10 / Windows 11 (64-bit)
- **Python:** 3.11 (recommended)
- **Wireshark / tshark:** Required for Frame Monitor live capture
- **Network:** PC must be on the same subnet as the ECUs (`10.20.0.x/24`)

---

## 📦 Installation

```bash
# 1. Clone the repository
git clone https://github.com/<organization>/diagnostic-platform.git
cd diagnostic-platform/doip-uds-simulator-HIL

# 2. Create a virtual environment
python -m venv .venv

# 3. Activate the virtual environment
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# 4. Install dependencies
pip install -r requirements.txt
```

---

## ▶️ Running the Application

```bash
# From the repository root
cd doip-uds-simulator-HIL
python ui/main.py
```

---

## 🖥 Feature Overview

### Dashboard
Real-time ECU status cards (Connection, Routing, Session, Security), ECU discovery, DTC table, and quick action buttons.

### Session & Security
Full UDS Security Access flow — Extended session switch, seed request, key calculation and submission with visual lock/unlock feedback.

### HIL Coding
Write BCM coding parameters (F200–F204) via UDS `0x2E`. UI checkboxes and combos stay synchronized in real time with any UDS command sent from any page.

### DTC Management — BCM / WC
Read, filter, and clear Diagnostic Trouble Codes. Snapshot (0x19/0x04) and Extended Data (0x19/0x06) decoding with per-field display.

### Communication Control
UDS `0x28` subfunction control (Enable/Disable Rx/Tx). DoIP network status display. Live Frame Monitor with full DoIP and UDS protocol decode.

### UDS Console
Send any raw UDS hex command with quick-command shortcuts. Full TX/RX transaction log with service name decode, color-coded by direction and theme.

### Actuator Lab
XCP-based DTC injection — connect to the XCP server, set calibration variable values via sliders/checkboxes, and inject all values in a background thread.

---

## 📡 UDS Services Reference

| Service | SID | Default Session | Extended Session | Security Required |
|---------|-----|:-:|:-:|:-:|
| DiagnosticSessionControl | `0x10` | ✅ | ✅ | ❌ |
| ECUReset (Soft) | `0x11` | ✅ | ✅ | ❌ |
| ClearDiagnosticInformation | `0x14` | ❌ | ✅ | Level 2 |
| ReadDTCInformation | `0x19` | ✅ | ✅ | ❌ |
| ReadDataByIdentifier | `0x22` | ✅ | ✅ | ❌ |
| SecurityAccess | `0x27` | ❌ | ✅ | — |
| CommunicationControl | `0x28` | ❌ | ✅ | ❌ |
| WriteDataByIdentifier | `0x2E` | ❌ | ✅ | Level 2 |
| RoutineControl | `0x31` | ❌ | ✅ | Level 2 |
| TesterPresent | `0x3E` | ✅ | ✅ | ❌ |

---

## 🔧 HIL Coding Reference

### Coding DIDs (BCM — `0x2E`)

| DID | Name | Type | Values |
|-----|------|------|--------|
| `0xF200` | RainSensorInstalled | Boolean | `0` = No · `1` = Yes |
| `0xF201` | WCAvailable | Boolean | `0` = No · `1` = Yes |
| `0xF202` | RearWiperAvailable | Boolean | `0` = No · `1` = Yes |
| `0xF203` | FrontWashDirection | Enum | `0` = Forward · `1` = Backward |
| `0xF204` | RearCameraDirection | Enum | `0` = Backward · `1` = Forward |

### Routine IDs (BCM — `0x31`)

| RID | Name | Precondition |
|-----|------|-------------|
| `0x0201` | Front Wiper Test | None |
| `0x0202` | Rear Wiper Test | F202 = 1 |
| `0x0203` | Pump Forward Test | None |
| `0x0204` | Pump Backward Test | None |
| `0x0205` | Rain Intensity Simulation | F200 = 1 |

### Live Status DIDs (BCM — `0x22`)

| DID | Name | Unit |
|-----|------|------|
| `0xF100` | WiperCurrentMode | Enum |
| `0xF101` | WiperSpeed | Enum |
| `0xF102` | BladePosition | % |
| `0xF103` | MotorCurrent | mA |
| `0xF104` | PumpStatus | Enum |
| `0xF105` | RainIntensity | % |
| `0xF106` | RearWiperStatus | Boolean |
| `0xF107` | ErrorState | Bitmask |

---

## 🔐 Security Access

The platform implements a **two-step Seed/Key** challenge-response:

```
Step 1 — Request Seed:   TX: 27 01  →  RX: 67 01 [S0 S1 S2 S3]
Step 2 — Send Key:       TX: 27 02 [K0 K1 K2 K3]  →  RX: 67 02

Key algorithm:   K = reverse_bytes(Seed)
Example:
  Seed = 11 22 33 44
  Key  = 44 33 22 11
```

---

## 📝 Logging

Logs are automatically written to the `logs/` directory on each application start.

```
logs/
├── doip_protocol.log       # DoIP frame encoding/decoding traces
├── diagnostic_client.log   # ECU discovery & routing activation
└── ecu_server.log          # UDS server response traces
```

**Log format (ASPICE-compliant):**
```
YYYY-MM-DD HH:MM:SS | LEVEL   | module.name | function_name | message
2026-04-22 11:32:10 | INFO    | worker      | connect_to_ecu | BCM socket connected to 10.20.0.25:13400
2026-04-22 11:32:11 | INFO    | worker      | _handle_response | ← 62 F2 00 00
```

**Rotation policy:** 1 MB per file · 3 backup files · UTF-8 encoding

---

## 🧪 Running Tests

```bash
cd doip-uds-simulator-HIL
python -m pytest tests/ -v
```

---

## 👤 Authors

<table>
  <tr>
    <td align="center">
      <strong>Ghassen Hedi Hajji</strong><br>
      Étudiant en Informatique Industrielle<br>
      ENETCOM Sfax · PFE 2026<br>
      KPIT Engineering Sfax
    </td>
  </tr>
</table>

---

<div align="center">

© 2026 Ghassen Hedi Hajji — KPIT Engineering Sfax. All rights reserved.

*Built with PySide6 · ISO 13400 · ISO 14229 · Python 3.11*

</div>
