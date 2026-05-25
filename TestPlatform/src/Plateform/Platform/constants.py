"""
WipeWash — Constantes globales
Ports, palette, opérations wiper, polices.
"""

# ═══════════════════════════════════════════════════════════
#  PORTS
# ═══════════════════════════════════════════════════════════
PORT_MOTOR   = 5000   # RPiBCM : état moteur rx (bcm_tcp_broadcast)
PORT_BCMCAN  = 5002   # RPiSIM  : vehicle/rain/wiper tx vers bcmcan
PORT_LIN     = 5555   # LIN events rx
PORT_PUMP_RX = 5556   # Pompe données rx
PORT_PUMP_TX = 5001   # Pompe commandes tx
PORT_CAN     = 5557   # CAN frame events rx (bcmcan TCP broadcast)

# ═══════════════════════════════════════════════════════════════
#  CAN IDs / COULEURS
# ═══════════════════════════════════════════════════════════════
CAN_CMD_C    = "#1A4E8E"   # 0x200 Wiper_Cmd     (RX → bus)  bleu foncé
CAN_STA_C    = "#1A6E1A"   # 0x201 Wiper_Status  (TX ← bus) vert foncé
CAN_ACK_C    = "#8B4513"   # 0x202 Wiper_Ack     (TX WC→BCM) brun
CAN_VEH_C    = "#007ACC"   # 0x300 Vehicle_Status           bleu vif
CAN_RAIN_C   = "#D35400"   # 0x301 RainSensorData           orange
CAN_GRID     = "#D8DADC"

# ═══════════════════════════════════════════════════════════
#  PALETTE  — HTML vert KPIT / blanc (car_simulator.html unifié)
# ═══════════════════════════════════════════════════════════
W_BG        = "#FFFFFF"    # fond global : blanc pur (espace entre rectangles)
W_PANEL     = "#FFFFFF"    # panneaux : blanc pur
W_PANEL2    = "#F8FAFC"    # panneaux secondaires
W_PANEL3    = "#F1F5F9"    # panneaux tertiaires
W_TOOLBAR   = "#F1F5F9"    # barre d'outils
W_TITLEBAR  = "#0F1A0A"    # titres dark (palette HTML header)
W_DOCK_HDR  = "#0F1A0A"    # headers panneaux dark vert

W_BORDER    = "rgba(100,116,139,0.25)"  # slate semi-transparent
W_BORDER2   = "rgba(100,116,139,0.40)"
W_SEP       = "rgba(100,116,139,0.15)"

W_TEXT      = "#1A1A1A"
W_TEXT2     = "#2A2A2A"
W_TEXT_DIM  = "#64748B"    # slate gris
W_TEXT_HDR  = "#FFFFFF"    # blanc sur header dark

A_TEAL      = "#007ACC"
A_TEAL2     = "#005F9E"
A_GREEN     = "#39FF14"   # vert fluo — speed
A_GREEN_L   = "#7FFF00"
A_GREEN_BG  = "#E0F8D0"   # fond vert très pâle (nouvelle palette)
A_RED       = "#C0392B"
A_RED_L     = "#E74C3C"
A_RED_BG    = "#FDEDEC"
A_ORANGE    = "#D35400"
A_ORANGE_BG = "#FEF5E7"
A_AMBER     = "#F39C12"

# KPIT green (palette HTML partagée)
KPIT_GREEN       = "#8DC63F"
KPIT_GREEN_GLOW  = "rgba(141,198,63,0.35)"
KPIT_GREEN_DIM   = "rgba(141,198,63,0.15)"

# Rain : bleu/noir
RAIN_ARC_C   = "#1E90FF"
RAIN_ARC_BG  = "#0D1520"
# Speed : vert fluo/noir
SPEED_ARC_C  = "#39FF14"
SPEED_ARC_BG = "#0A1200"

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
