# UDS Sequences — HIL Diagnostic Platform

> **Standard:** ISO 14229-1:2020 (UDS) · ISO 13400-2:2012 (DoIP)  
> **Target ECUs:** BCM (0x0700) · WC (0x0701)  
> **Tester Address:** 0x07DF  
> **Version:** 1.0.0 · PFE 2026 — KPIT Engineering Sfax

---

## 1. ECU Discovery & Connection

### 1.1 UDP Discovery (ISO 13400 §7.3)

```
Tester (PC)                              ECU (BCM)
    │                                        │
    │── UDP Broadcast 255.255.255.255:13400 ─►│
    │   [02 FD 00 01 00 00 00 00]             │
    │   Type: Vehicle Identification Request  │
    │                                        │
    │◄─ UDP Response ────────────────────────│
    │   [02 FD 00 04 00 00 00 21             │
    │    VIN(17) LogicAddr(2) EID(6)         │
    │    GID(6) IP(4)]                       │
    │   VIN: VIN12345678901234               │
    │   Addr: 0x0700                         │
```

### 1.2 TCP Routing Activation

```
Tester                                   BCM
    │                                        │
    │── TCP Connect :13400 ─────────────────►│
    │                                        │
    │── Routing Activation Request ─────────►│
    │   [02 FD 00 05 00 00 00 07             │
    │    07 DF 00 00 00 00 00]               │
    │                                        │
    │◄─ Routing Activation Response ─────────│
    │   [02 FD 00 06 00 00 00 03             │
    │    07 DF 00]                           │
    │   Code: 0x00 = Routing activated       │
    │                                        │
    │   [Diagnostic channel open]            │
```

---

## 2. Session Management (SID 0x10)

### 2.1 Switch to Extended Diagnostic Session

```
Tester                                   BCM (0x0700)
    │                                        │
    │── 10 03 ──────────────────────────────►│
    │   DiagnosticSessionControl             │
    │   SubFunction: 0x03 (Extended)         │
    │                                        │
    │◄─ 50 03 00 32 07 D0 ───────────────────│
    │   Positive Response                    │
    │   P2Server: 50ms · P2*Server: 2000ms   │
```

### 2.2 Return to Default Session

```
Tester                                   BCM
    │                                        │
    │── 10 01 ──────────────────────────────►│
    │                                        │
    │◄─ 50 01 00 19 07 D0 ───────────────────│
    │                                        │
    │   [Security level reset to 0]          │
```

### 2.3 ECU Soft Reset (SID 0x11)

```
Tester                                   BCM
    │                                        │
    │── 11 03 ──────────────────────────────►│
    │   ECUReset · SubFunction: 0x03 (Soft)  │
    │                                        │
    │◄─ 51 03 ───────────────────────────────│
    │                                        │
    │   [ECU restores: Session=0x01,         │
    │    SecurityLevel=0]                    │
    │   [UI updates: Session=Default,        │
    │    Security=Locked]                    │
```

---

## 3. Security Access (SID 0x27)

### 3.1 Full Unlock Sequence (Level 2)

```
Tester                                   BCM
    │                                        │
    │   [Precondition: Extended Session]     │
    │                                        │
    │── 27 03 ──────────────────────────────►│
    │   SecurityAccess · RequestSeed         │
    │   SubFunction: 0x03                    │
    │                                        │
    │◄─ 67 03 11 22 33 44 ───────────────────│
    │   Seed: 0x11223344                     │
    │                                        │
    │   [Key calculation: reverse bytes]     │
    │   Key = 0x44332211                     │
    │                                        │
    │── 27 04 44 33 22 11 ──────────────────►│
    │   SecurityAccess · SendKey             │
    │                                        │
    │◄─ 67 04 ───────────────────────────────│
    │   Positive Response                    │
    │   [SecurityLevel = 2, Unlocked]        │
```

### 3.2 Error Cases

| Condition | NRC | Description |
|-----------|-----|-------------|
| Wrong session | 0x7E | subFunctionNotSupportedInActiveSession |
| Invalid key | 0x35 | invalidKey |
| Too many attempts | 0x36 | exceededNumberOfAttempts |
| Delay not expired | 0x37 | requiredTimeDelayNotExpired |

---

## 4. HIL Coding (SID 0x2E)

### 4.1 Enable WC (F201 = 1)

```
Tester                                   BCM
    │                                        │
    │   [Precondition: Extended + Level 2]   │
    │                                        │
    │── 2E F2 01 01 ────────────────────────►│
    │   WriteDataByIdentifier                │
    │   DID: 0xF201 · Value: 0x01            │
    │                                        │
    │◄─ 6E F2 01 ────────────────────────────│
    │   Positive Response                    │
    │   [UI: wc_available checkbox = checked]│
    │   [TopBar: WC (0x701) item added]      │
```

### 4.2 Full Coding Sequence (Apply Coding button)

```
Tester                                   BCM
    │                                        │
    │── 2E F2 04 00 ────────────────────────►│  RearCameraDirection = Forward
    │◄─ 6E F2 04 ────────────────────────────│
    │── 2E F2 03 00 ────────────────────────►│  FrontWashDirection = Forward
    │◄─ 6E F2 03 ────────────────────────────│
    │── 2E F2 02 00 ────────────────────────►│  RearWiperAvailable = No
    │◄─ 6E F2 02 ────────────────────────────│
    │── 2E F2 01 01 ────────────────────────►│  WCAvailable = Yes
    │◄─ 6E F2 01 ────────────────────────────│
    │── 2E F2 00 00 ────────────────────────►│  RainSensorInstalled = No
    │◄─ 6E F2 00 ────────────────────────────│
```

### 4.3 Coding DID Reference

| DID | Name | 0x00 | 0x01 |
|-----|------|------|------|
| `0xF200` | RainSensorInstalled | No | Yes |
| `0xF201` | WCAvailable | No | Yes |
| `0xF202` | RearWiperAvailable | No | Yes |
| `0xF203` | FrontWashDirection | Forward | Backward |
| `0xF204` | RearCameraDirection | Backward | Forward |

---

## 5. DTC Management (SID 0x19 / 0x14)

### 5.1 Read All DTCs (Sub 0x02)

```
Tester                                   BCM
    │                                        │
    │── 19 02 FF ───────────────────────────►│
    │   ReadDTCInformation                   │
    │   SubFunction: 0x02 (byStatusMask)     │
    │   Mask: 0xFF (all)                     │
    │                                        │
    │◄─ 59 02 FF                             │
    │      B2 20 04 2F                       │
    │      B2 20 09 2E ─────────────────────│
    │   DTC B22004 · Status 0x2F (ACTIVE)    │
    │   DTC B22009 · Status 0x2E (NOT ACTIVE)│
```

### 5.2 Status Mask Values

| Mask | Filter |
|------|--------|
| `0xFF` | All DTCs |
| `0x2F` | Active in memory |
| `0x2E` | Not active in memory |

### 5.3 Read DTC Snapshot (Sub 0x04)

```
Tester                                   BCM
    │                                        │
    │── 19 04 B2 20 04 01 ──────────────────►│
    │   DTC: 0xB22004 · Record: 01           │
    │                                        │
    │◄─ 59 04 B2 20 04 2F 01                 │
    │      F1 90 01 01                       │  Ignition: ON
    │      F1 91 0A 53...  01                │  WiperMode: string
    │      F1 92 02 00 C8  02                │  MotorCurrent: 200mA
    │      F1 93 01 32     01 ──────────────│  BladePosition: 50%
```

### 5.4 Clear All DTCs (SID 0x14)

```
Tester                                   BCM
    │                                        │
    │   [Precondition: Extended + Level 2]   │
    │                                        │
    │── 14 FF FF FF ────────────────────────►│
    │   ClearDiagnosticInformation           │
    │   Group: 0xFFFFFF (all)                │
    │                                        │
    │◄─ 54 ──────────────────────────────────│
    │   Positive Response                    │
```

---

## 6. Read System Status (SID 0x22)

### 6.1 BCM Live DIDs

```
Tester                                   BCM
    │                                        │
    │── 22 F1 00 ───────────────────────────►│
    │◄─ 62 F1 00 04 ─────────────────────────│  WiperCurrentMode = AUTO
    │                                        │
    │── 22 F1 03 ───────────────────────────►│
    │◄─ 62 F1 03 00 C8 ──────────────────────│  MotorCurrent = 200 mA
    │                                        │
    │── 22 F1 07 ───────────────────────────►│
    │◄─ 62 F1 07 21 ─────────────────────────│  ErrorState: LIN_FAULT + DIAG_ACTIVE
```

### 6.2 BCM DID Reference

| DID | Name | Size | Unit |
|-----|------|------|------|
| `0xF100` | WiperCurrentMode | 1 byte | Enum (0=OFF…9=DIAG) |
| `0xF101` | WiperSpeed | 1 byte | Enum (0=OFF,1=Speed1,2=Speed2) |
| `0xF102` | BladePosition | 1 byte | % |
| `0xF103` | MotorCurrent | 2 bytes | mA (big-endian) |
| `0xF104` | PumpStatus | 1 byte | Enum (0=OFF,1=FWD,2=BWD) |
| `0xF105` | RainIntensity | 1 byte | % |
| `0xF106` | RearWiperStatus | 1 byte | Boolean |
| `0xF107` | ErrorState | 1 byte | Bitmask |

### 6.3 ErrorState Bitmask (0xF107)

| Bit | Mask | Flag |
|-----|------|------|
| 0 | `0x01` | LIN_FAULT |
| 1 | `0x02` | REST_CONTACT |
| 2 | `0x04` | ERROR_STATE |
| 3 | `0x08` | DIAG_ACTIVE |
| 4 | `0x10` | MOTOR_OC |
| 5 | `0x20` | WIPER_FAULT |
| 6 | `0x40` | PUMP_ERROR |

---

## 7. Routine Control (SID 0x31)

### 7.1 Start Front Wiper Test

```
Tester                                   BCM
    │                                        │
    │   [Precondition: Extended + Level 2]   │
    │                                        │
    │── 31 01 02 01 0A ─────────────────────►│
    │   RoutineControl · Start               │
    │   RID: 0x0201 (FrontWiperTest)         │
    │   Duration: 10 seconds                 │
    │                                        │
    │◄─ 71 01 02 01 0A ──────────────────────│
    │   Positive Response                    │
```

### 7.2 Routine Reference

| RID | Name | Precondition | Parameter |
|-----|------|-------------|-----------|
| `0x0201` | Front Wiper Test | None | Duration (s) |
| `0x0202` | Rear Wiper Test | F202=1 | Duration (s) |
| `0x0203` | Pump Forward Test | None | Duration (s) |
| `0x0204` | Pump Backward Test | None | Duration (s) |
| `0x0205` | Rain Simulation | F200=1 | Intensity (0–100) |

### 7.3 Routine Error Cases

| Condition | NRC | Meaning |
|-----------|-----|---------|
| Wrong session | 0x7E | Extended session required |
| Not unlocked | 0x33 | Security access denied |
| F202=0 for rear | 0x22 | conditionsNotCorrect |
| F200=0 for rain | 0x22 | conditionsNotCorrect |

---

## 8. Communication Control (SID 0x28)

```
Tester                                   BCM
    │                                        │
    │   [Precondition: Extended Session]     │
    │                                        │
    │── 28 03 ──────────────────────────────►│  Disable Rx + Tx
    │◄─ 68 03 ───────────────────────────────│
    │                                        │
    │── 28 00 ──────────────────────────────►│  Enable All
    │◄─ 68 00 ───────────────────────────────│
```

| SubFunction | Action |
|------------|--------|
| `0x00` | Enable Rx and Tx |
| `0x01` | Enable Rx · Disable Tx |
| `0x02` | Disable Rx · Enable Tx |
| `0x03` | Disable Rx and Tx |

---

## 9. TesterPresent Keepalive (SID 0x3E)

```
Tester                                   BCM / WC
    │                                        │
    │   [Every 2 seconds — auto sent]        │
    │                                        │
    │── 3E 00 ──────────────────────────────►│
    │◄─ 7E 00 ───────────────────────────────│
    │                                        │
    │   [Prevents session timeout]           │
```

---

## 10. WC ECU Sequences (0x0701)

> WC is only accessible after BCM coding `F201 = 1` and target switch to `0x0701`.

### 10.1 Read WC DTCs

```
Tester                                   WC (0x0701)
    │                                        │
    │── 19 02 FF ───────────────────────────►│
    │◄─ 59 02 FF                             │
    │      B2 21 01 2F                       │
    │      B2 21 03 2E ─────────────────────│
    │   DTC B22101 · Status 0x2F (ACTIVE)    │
    │   DTC B22103 · Status 0x2E (NOT ACTIVE)│
```

### 10.2 WC DID Reference

| DID | Name | Size | Unit |
|-----|------|------|------|
| `0xF000` | WiperCurrentMode | 1 byte | Enum |
| `0xF001` | WiperSpeed | 1 byte | Enum |
| `0xF002` | BladePosition | 1 byte | % |
| `0xF003` | MotorCurrent | 2 bytes | mA |
| `0xF004` | PumpStatus | 1 byte | Enum |

---

## 11. Negative Response Codes (NRC) Reference

| NRC | Name | Typical Cause |
|-----|------|--------------|
| `0x10` | generalReject | Internal ECU error |
| `0x11` | serviceNotSupported | SID not implemented |
| `0x12` | subFunctionNotSupported | Invalid subfunction byte |
| `0x13` | incorrectMessageLength | Wrong payload length |
| `0x22` | conditionsNotCorrect | Precondition not met (e.g. F202=0) |
| `0x24` | requestSequenceError | Step out of order |
| `0x31` | requestOutOfRange | DID or RID not recognized |
| `0x33` | securityAccessDenied | Not unlocked |
| `0x35` | invalidKey | Wrong key sent |
| `0x36` | exceededNumberOfAttempts | Too many failed unlock attempts |
| `0x37` | requiredTimeDelayNotExpired | Security lockout period active |
| `0x7E` | subFunctionNotSupportedInActiveSession | Wrong session |
| `0x7F` | serviceNotSupportedInActiveSession | Wrong session |

---

*Document maintained by: Ghassen Hedi Hajji — KPIT Engineering Sfax — 2026*
