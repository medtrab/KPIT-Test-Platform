# System Architecture — HIL Diagnostic Platform

> **Project:** Wiper & Wash System — Hardware In the Loop Diagnostic Platform  
> **Standard:** DoIP ISO 13400-2:2012 · UDS ISO 14229-1:2020  
> **Version:** 1.0.0 · PFE 2026 — KPIT Engineering Sfax

---

## 1. System Overview

The HIL Diagnostic Platform is a PC-based engineering tool that communicates with two physical ECUs over a real Ethernet network using the DoIP transport protocol. It replaces the traditional bench tester (CANoe / ETAS INCA) for validation and calibration of the Wiper & Wash system.

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Engineering PC                                 │
│                                                                        │
│   ┌─────────────────────────────────────────────────────────────┐    │
│   │              HIL Diagnostic Platform (PySide6)               │    │
│   │                                                               │    │
│   │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐  │    │
│   │  │Dashboard │  │  HIL     │  │   DTC    │  │    UDS     │  │    │
│   │  │          │  │ Coding   │  │ Manager  │  │  Console   │  │    │
│   │  └──────────┘  └──────────┘  └──────────┘  └────────────┘  │    │
│   │                                                               │    │
│   │  ┌──────────────────────────────────────────────────────┐   │    │
│   │  │              BackendController (Qt Main Thread)       │   │    │
│   │  └──────────────────────┬───────────────────────────────┘   │    │
│   │                         │ Qt Signals (thread-safe)           │    │
│   │  ┌──────────────────────▼───────────────────────────────┐   │    │
│   │  │            DiagnosticWorker (QThread)                 │   │    │
│   │  │                                                        │   │    │
│   │  │   Request Queue → Send → Receive → Dispatch           │   │    │
│   │  │                                                        │   │    │
│   │  │   TCP Socket BCM          TCP Socket WC               │   │    │
│   │  │   10.20.0.25:13400        10.20.0.7:13401             │   │    │
│   │  └──────────┬────────────────────────┬───────────────────┘   │    │
│   └─────────────┼────────────────────────┼───────────────────────┘    │
└─────────────────┼────────────────────────┼────────────────────────────┘
                  │  DoIP / TCP             │  DoIP / TCP
         ┌────────▼────────┐      ┌────────▼────────┐
         │   BCM ECU        │      │    WC ECU        │
         │  10.20.0.25      │      │   10.20.0.7      │
         │  Port 13400      │      │   Port 13401     │
         │  Addr: 0x0700    │      │   Addr: 0x0701   │
         └─────────────────┘      └─────────────────┘
```

---

## 2. Software Layer Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Presentation Layer                  │
│                                                       │
│  MainWindow                                           │
│  ├── DashboardPage     (status, DTC table)            │
│  ├── SecurityPage      (seed/key flow)                │
│  ├── HILPage           (coding, routines, status)     │
│  ├── BCMPage           (DTC mgmt + CommControl tab)   │
│  ├── WCPage            (DTC mgmt)                     │
│  ├── UDSConsolePage    (raw hex console)              │
│  ├── ActuatorLabPage   (XCP injection)                │
│  └── FrameMonitorWidget (live DoIP/UDS capture)       │
├─────────────────────────────────────────────────────┤
│                   Application Layer                   │
│                                                       │
│  BackendController     (signal routing, state mgmt)   │
│  MainWindow            (UI ↔ backend wiring)          │
├─────────────────────────────────────────────────────┤
│                   Service Layer                       │
│                                                       │
│  DiagnosticWorker      (request queue, socket mgmt)   │
│  XCPClient             (UDP injection)                │
├─────────────────────────────────────────────────────┤
│                   Transport Layer                     │
│                                                       │
│  doip_protocol.py      (ISO 13400 frame codec)        │
│  diagnostic_client.py  (discovery, routing act.)      │
│  ecu_server.py         (minimal UDS server skeleton)  │
├─────────────────────────────────────────────────────┤
│                   Infrastructure Layer                │
│                                                       │
│  constants.py          (addresses, SIDs, NRCs)        │
│  logging_config.py     (ASPICE rotating file log)     │
│  data/dtc_bcm.json     (BCM DTC database)             │
│  data/dtc_wc.json      (WC DTC database)              │
│  data/xcp_variables.json (XCP variable map)           │
└─────────────────────────────────────────────────────┘
```

---

## 3. Threading Model

```
Main Thread (Qt Event Loop)
│
├── TopBar          — theme toggle, ECU selector, clock
├── Sidebar         — page navigation
├── MainWindow      — signal dispatch, UI state
├── BackendController — pass-through signal relay
│
└── QThread: DiagnosticWorker
        │
        ├── Socket BCM  (TCP 13400) — blocking I/O
        ├── Socket WC   (TCP 13401) — blocking I/O
        ├── Request Queue (deque, RLock-protected)
        ├── QTimer: TesterPresent (2 s interval)
        └── Loop: dequeue → send → receive → emit signal

QThread: _XCPWorker (ActuatorLab, per-inject session)
        │
        └── UDP Socket — XCP SHORT_DOWNLOAD jobs
```

**Thread safety:** All UI updates from `DiagnosticWorker` are delivered via Qt signals, which are automatically queued across thread boundaries. No direct UI access from the worker thread.

---

## 4. DoIP Protocol Stack (ISO 13400)

```
Application (UDS payload)
        │
        ▼
┌───────────────────────────────────────┐
│           DoIP Message                │
│                                       │
│  Header (8 bytes)                     │
│  ┌──────┬──────┬──────────┬─────────┐ │
│  │ Ver  │~Ver  │  Type    │ Length  │ │
│  │ 0x02 │ 0xFD │ 0x8001   │ N bytes │ │
│  └──────┴──────┴──────────┴─────────┘ │
│                                       │
│  Payload                              │
│  ┌────────────┬────────────┬────────┐ │
│  │  Src Addr  │  Dst Addr  │  UDS   │ │
│  │  0x07DF    │  0x0700    │ bytes  │ │
│  └────────────┴────────────┴────────┘ │
└───────────────────────────────────────┘
        │
        ▼
TCP Socket (full-duplex, persistent connection)
```

### DoIP Session Establishment

```
PC                              ECU
│                                │
│── UDP: Vehicle ID Request ────►│  (broadcast 255.255.255.255:13400)
│◄─ UDP: Vehicle ID Response ───│  (VIN + logical address + IP)
│                                │
│── TCP Connect :13400 ─────────►│
│── Routing Activation Request ─►│
│◄─ Routing Activation Response ─│  (0x00 = activated)
│                                │
│    [Diagnostic session open]   │
│── Diagnostic Message (UDS) ───►│
│◄─ Diagnostic Message (UDS) ───│
```

---

## 5. UDS State Machine

```
                    ┌─────────────────┐
          ┌────────►│  Default Session │◄────────────┐
          │         │   (0x01)         │             │
          │         └────────┬────────┘             │
          │                  │ 10 03                │
    11 03 │                  ▼                 10 01 │
    (soft │         ┌─────────────────┐             │
    reset)│         │Extended Session │             │
          │         │   (0x03)         │─────────────┘
          │         └────────┬────────┘
          │                  │ 27 01/02
          │                  ▼
          │         ┌─────────────────┐
          └─────────│Security Unlocked│
                    │  (Level 2)       │
                    └─────────────────┘

Allowed in Default Session:   0x10, 0x11, 0x19, 0x22, 0x3E
Allowed in Extended Session:  all of the above + 0x27, 0x28, 0x2E, 0x31, 0x14
Requires Security Level 2:    0x2E, 0x31, 0x14
```

---

## 6. Dual ECU Socket Management

```
DiagnosticWorker
│
├── _sock_bcm  ──► TCP 10.20.0.25:13400  (BCM, addr 0x0700)
│                  Created on: connect_to_ecu(BCM)
│                  Persistent: yes (TesterPresent keepalive)
│
└── _sock_wc   ──► TCP 10.20.0.7:13401   (WC,  addr 0x0701)
                   Created on: set_target(0x0701) if not connected
                   Auto-connect: connect_to_wc() called automatically
                   Precondition: BCM F201 (WCAvailable) = 1

Active socket = _sock_bcm if target == 0x0700
              = _sock_wc  if target == 0x0701

Target switch:
  1. Clear request queue
  2. Flush socket buffer (0.01s timeout drain)
  3. If WC and socket is None → auto-connect
```

---

## 7. XCP DTC Injection (ActuatorLab)

```
ActuatorLabPage
│
├── _load_xcp_config()  ──► core/data/xcp_variables.json
│                           {target: {var_name: {address, size, range, dtc}}}
│
└── _on_inject()
        │
        ├── Build jobs: [(name, addr, value, size), ...]
        │
        └── _XCPWorker (QThread)
                │
                └── XCPClient.write_variable(addr, value, size)
                        │
                        └── UDP SHORT_DOWNLOAD to 10.20.0.25:17725
```

---

## 8. Theme System

The platform supports full **Dark / Light** bidirectional theme switching.

```
TopBar._on_theme_toggle()
        │
        └── theme_changed.emit(dark_mode: bool)
                │
                └── MainWindow._on_theme_changed(dark_mode)
                        │
                        ├── setStyleSheet(DARK_STYLESHEET | LIGHT_STYLESHEET)
                        ├── _apply_theme_sidebar()
                        ├── _apply_theme_dashboard()
                        ├── _apply_theme_security()
                        ├── _apply_theme_bcm()
                        ├── _apply_theme_uds_console()
                        ├── _apply_theme_frame_monitor()
                        ├── actuator_lab.apply_theme()
                        ├── splash.apply_theme()
                        └── top_bar._apply_topbar_style()
```

All theme methods apply hardcoded color overrides on top of the global stylesheet for components that have inline styles.

---

## 9. Signal Map (MainWindow)

| Signal Source | Signal | Slot |
|--------------|--------|------|
| `controller.session_changed` | `int` | `_on_session_changed` |
| `controller.security_level_changed` | `int` | `_on_security_changed` |
| `controller.dtc_list_updated` | `list` | `_update_dtc_tables` |
| `controller.uds_positive` | `bytes` | `_update_hil_status_table` |
| `controller.uds_positive` | `bytes` | `uds_console.handle_response` |
| `controller.uds_positive` | `bytes` | `_on_uds_positive_hil` |
| `controller.uds_sent` | `bytes` | `uds_console.handle_sent` |
| `controller.uds_sent` | `bytes` | `_on_uds_sent_hil` |
| `top_bar.target_changed` | `int` | `_on_target_changed` |
| `top_bar.theme_changed` | `bool` | `_on_theme_changed` |
| `hil.apply_coding_requested` | `dict` | `_apply_hil_coding` |
| `wc.clear_dtc_requested` | — | `controller.clear_dtc` |

---

*Document maintained by: Ghassen Hedi Hajji — KPIT Engineering Sfax — 2026*
