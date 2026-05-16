"""
Constantes globales pour le backend DoIP/UDS.
"""

# Adresses logiques
LOGICAL_ADDR_BCM = 0x0700
LOGICAL_ADDR_WC = 0x0701
LOGICAL_ADDR_TESTER = 0x07DF

# Services UDS (pour référence)
UDS_SERVICE = {
    'DIAGNOSTIC_SESSION_CONTROL': 0x10,
    'ECU_RESET': 0x11,
    'CLEAR_DTC': 0x14,
    'READ_DTC': 0x19,
    'READ_DATA_BY_ID': 0x22,
    'SECURITY_ACCESS': 0x27,
    'COMMUNICATION_CONTROL': 0x28,
    'WRITE_DATA_BY_ID': 0x2E,
    'ROUTINE_CONTROL': 0x31,
    'TESTER_PRESENT': 0x3E,
}

# Codes NRC (Negative Response Code)
UDS_NRC = {
    0x11: "Service Not Supported",
    0x12: "Sub‑Function Not Supported",
    0x13: "Incorrect Message Length / Invalid Format",
    0x22: "Conditions Not Correct",
    0x24: "Request Sequence Error",
    0x31: "Request Out of Range",
    0x33: "Security Access Denied",
    0x35: "Invalid Key",
    0x36: "Exceeded Number of Attempts",
    0x37: "Required Time Delay Not Expired",
    0x7E: "Sub‑Function Not Supported in Active Session",
    0x7F: "Service Not Supported in Active Session",
}