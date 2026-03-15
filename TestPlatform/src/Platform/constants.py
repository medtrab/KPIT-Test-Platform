"""
WipeWash — Constantes globales
Ports, palette, opérations wiper, polices.
"""

# ═══════════════════════════════════════════════════════════
#  PORTS
# ═══════════════════════════════════════════════════════════
PORT_MOTOR   = 5000   # Moteurs rx + Vehicle/Rain/Wiper tx
PORT_LIN     = 5555   # LIN events rx
PORT_PUMP_RX = 5556   # Pompe données rx
PORT_PUMP_TX = 5001   # Pompe commandes tx

# ═══════════════════════════════════════════════════════════
#  PALETTE  ControlDesk gris clair
# ═══════════════════════════════════════════════════════════
W_BG        = "#ECEDEE"
W_PANEL     = "#F5F5F5"
W_PANEL2    = "#EBEBEB"
W_PANEL3    = "#E0E0E0"
W_TOOLBAR   = "#D6D8DA"
W_TITLEBAR  = "#C8CACB"
W_DOCK_HDR  = "#3C3F41"

W_BORDER    = "#B0B3B5"
W_BORDER2   = "#9A9D9F"
W_SEP       = "#C5C7C9"

W_TEXT      = "#1A1A1A"
W_TEXT2     = "#3A3A3A"
W_TEXT_DIM  = "#707070"
W_TEXT_HDR  = "#FAFAFA"

A_TEAL      = "#007ACC"
A_TEAL2     = "#005F9E"
A_GREEN     = "#2E8B2E"
A_GREEN_L   = "#4CAF50"
A_GREEN_BG  = "#E8F5E9"
A_RED       = "#C0392B"
A_RED_L     = "#E74C3C"
A_RED_BG    = "#FDEDEC"
A_ORANGE    = "#D35400"
A_ORANGE_BG = "#FEF5E7"
A_AMBER     = "#F39C12"

LIN_TX_C    = "#1A6E1A"
LIN_RX_C    = "#1A4E8E"
LIN_GRID    = "#D8DADC"

# ═══════════════════════════════════════════════════════════
#  POLICES
# ═══════════════════════════════════════════════════════════
FONT_UI   = "Segoe UI"
FONT_MONO = "Consolas"

# ═══════════════════════════════════════════════════════════
#  LIN TABLE
# ═══════════════════════════════════════════════════════════
MAX_ROWS = 500

# ═══════════════════════════════════════════════════════════
#  WIPER OPERATIONS
# ═══════════════════════════════════════════════════════════
WOP = {
    0: {"name":"OFF",        "label":"Stop",       "desc":"Blade at rest position",        "req":"SRD_WW_001", "color":"#707070"},
    1: {"name":"TOUCH",      "label":"Touch",      "desc":"1 cycle <= 1700 ms",            "req":"SRD_WW_020", "color":A_TEAL},
    2: {"name":"SPEED1",     "label":"Speed 1",    "desc":"Continuous slow — PWM 50 %",    "req":"SRD_WW_030", "color":A_GREEN},
    3: {"name":"SPEED2",     "label":"Speed 2",    "desc":"Continuous fast — PWM 100 %",   "req":"SRD_WW_040", "color":"#1B5E20"},
    4: {"name":"AUTO",       "label":"Auto",       "desc":"Automatic rain sensor mode",    "req":"SRD_WW_050", "color":"#6A1B9A"},
    5: {"name":"FRONT_WASH", "label":"Front Wash", "desc":"Pump FWD + Speed1 >= 3 cycles", "req":"SRD_WW_100", "color":"#00695C"},
    6: {"name":"REAR_WASH",  "label":"Rear Wash",  "desc":"Pump BWD + rear 2 cycles",      "req":"SRD_WW_110", "color":A_ORANGE},
    7: {"name":"REAR_WIPE",  "label":"Rear Wipe",  "desc":"1 rear cycle <= 1700 ms",       "req":"SRD_WW_090", "color":"#37474F"},
}
