#!/usr/bin/env python3
"""
bcm_rte.py
==========
RTE -- Runtime Environment (Memoire Partagee)
WipeWash System -- Architecture 3 Couches

Regle absolue : AUCUNE logique ici.
Uniquement variables partagees + constantes + acces thread-safe.

MODIFICATION v3 -- Commande par relais  :
  Moteur avant : 2 relais
    RL2 (PIN_RELAY_FRONT_ON)   : ON/OFF moteur avant  actif LOW
    RL1 (PIN_RELAY_FRONT_SPEED): Speed1/Speed2         HIGH=Speed1 / LOW=Speed2

  Moteur arriere : 1 relais
    RL3 (PIN_RELAY_REAR_ON)    : ON/OFF moteur arriere actif LOW

  Rest Contact : hardware present (PIN_REST_CONTACT pull-down)
  Courant moteur : ADS1115 canal A3 (0V=0A / 3.3V=1A)
"""

import threading
import time

# =====================================================
# WIPER OPERATION CODES
# =====================================================
WOP_OFF        = 0x00
WOP_TOUCH      = 0x01
WOP_SPEED1     = 0x02
WOP_SPEED2     = 0x03
WOP_AUTO       = 0x04
WOP_FRONT_WASH = 0x05
WOP_REAR_WASH  = 0x06
WOP_REAR_WIPE  = 0x07

WOP_NAMES = {
    0: "OFF",   1: "TOUCH",      2: "SPEED1",    3: "SPEED2",
    4: "AUTO",  5: "FRONT_WASH", 6: "REAR_WASH", 7: "REAR_WIPE"
}

# =====================================================
# ETATS MACHINE D'ETAT
# =====================================================
ST_OFF        = "OFF"
ST_TOUCH      = "TOUCH"
ST_SPEED1     = "SPEED1"
ST_SPEED2     = "SPEED2"
ST_AUTO       = "AUTO"
ST_WASH_FRONT = "WASH_FRONT"
ST_WASH_REAR  = "WASH_REAR"
ST_REAR_WIPE  = "REAR_WIPE"
ST_ERROR      = "ERROR"
ST_DIAG       = "DIAG"   # DoIP pilote les actionneurs -- WSM ne touche rien

ST_ENC = {
    ST_OFF: 0,  ST_TOUCH: 1,      ST_SPEED1: 2,  ST_SPEED2: 3,
    ST_AUTO: 4, ST_WASH_FRONT: 5, ST_WASH_REAR: 6,
    ST_ERROR: 7, ST_REAR_WIPE: 8, ST_DIAG: 9
}

# =====================================================
# CONSTANTES SECURITY ACCESS
# =====================================================
SA_REQ_SEED = 0x01
SA_SEND_KEY = 0x02
SA_XOR_MASK = 0xA5A5
SA_ADD_MASK = 0x3C

# =====================================================
# LIN
# =====================================================
LIN_BAUD         = 19200
LIN_SYNC         = 0x55
LIN_BREAK        = 0x00
LIN_ID_0x16      = 0x16
LIN_ID_0x17      = 0x17
LIN_PID_0x16     = 0xD6
LIN_PID_0x17     = 0x97
LIN_PID_DIAG_REQ = 0x3C
LIN_PID_DIAG_RSP = 0x3D

LIN_CYCLE_0x16   = 0.400   # 20 ms  (conforme MESSAGE_CATALOGUE)
LIN_CYCLE_0x17   = 0.800   # 100 ms (conforme MESSAGE_CATALOGUE)

LIN_TIMEOUT      = 2.000    # 200 ms (10 cycles manques @ 20ms)
LIN_INTERFRAME   = 0.050   # 50 ms

LIN_PORT_CANDIDATES = [
    "/dev/ttyACM0", "/dev/ttyACM1",
    "/dev/ttyUSB0", "/dev/ttyUSB1",
    "/dev/serial0",
]

# =====================================================
# CAN
# =====================================================
CAN_ID_VEHICLE     = 0x300
CAN_ID_RAIN_SENSOR = 0x301
CAN_RECV_TIMEOUT   = 0.200
CAN_IDLE_SLEEP     = 1.000

# =====================================================
# TIMINGS THREADS
# =====================================================
CONTROL_LOOP_PERIOD   = 0.200   # T-WSM  : 200 ms
PUMP_GUARD_PERIOD     = 0.200   # T-PUMP : 200 ms
DIAG_LOOP_PERIOD      = 0.010   # T-DIAG : 10 ms
ACTUATOR_TEST_PERIOD  = 0.500   # verification duree test actionneur : 500 ms
WATCHDOG_CHECK_PERIOD = 0.500

# =====================================================
# PARAMETRES CALIBRATION
# =====================================================
TOUCH_DURATION          = 1.700
PUMP_MAX_RUNTIME        = 5.000
WASH_FRONT_CYCLES       = 3
WASH_REAR_CYCLES        = 2
RAIN_SPEED2_THRESH      = 20
OVERCURRENT_DELAY       = 0.300
PUMP_OVERCURRENT_DELAY  = 0.300
REST_STUCK_DELAY        = 3.0
REVERSE_REAR_PERIOD     = 1.700
WATCHDOG_MAX_MS         = 50
WIPE_CYCLE_DURATION     = TOUCH_DURATION

# =====================================================
# SEUILS COURANT -- ADS1115 (amperes)
# =====================================================
# ADS1115 gain=1 : plage 0 - 4.096V
# Potentiometre calibre : 0V = 0A / 3.3V = 1A
# Seuils exprimes en amperes (0.0 - 1.0)
OVERCURRENT_THRESH      = 0.8   # 0.8A seuil moteur avant/arriere
PUMP_OVERCURRENT_THRESH = 0.8   # 0.8A seuil pompe

ADS_VOLTAGE_MIN = 0.0           # tension minimale calibree (V)
ADS_VOLTAGE_MAX = 3.3           # tension maximale calibree (V)
ADS_CURRENT_MIN = 0.0           # courant minimal (A)
ADS_CURRENT_MAX = 1.0           # courant maximal (A)
ADS_GAIN        = 1             # gain ADS1115 
ADS_CHANNEL     = 3             # canal A3 potentiometre courant moteur wiper
ADS_READ_PERIOD = 0.100         # lecture courant toutes les 100ms

# ADS1115 canal A0 -- ACS712 courant pompe
ADS_PUMP_CHANNEL    = 0         # canal A0 ACS712 pompe
ADS_PUMP_VREF       = 1.650     # tension repos ACS712 (Vcc/2 = 3.3/2)
ADS_PUMP_SENSITIVITY= 0.100     # V/A  (ACS712 5A : 0.185V/A  /  20A : 0.100V/A)
ADS_PUMP_NOISE      = 0.030     # seuil bruit (A) en dessous → 0A
ADS_PUMP_R_CHARGE   = 20.0      # resistance charge (ohm) pour calcul tension
ADS_PUMP_V_MAX      = 12.0      # tension nominale systeme (V)
ADS_PUMP_NB_SAMPLES = 10        # nb echantillons mediane pour lecture pompe

# =====================================================
# GPIO PINS -- RELAIS (actif LOW)
# =====================================================
# Moteur avant
PIN_RELAY_FRONT_ON    = 20   # RL2 : ON/OFF moteur avant   (LOW=ON  / HIGH=OFF)
PIN_RELAY_FRONT_SPEED = 23   # RL1 : vitesse moteur avant  (HIGH=Speed1 / LOW=Speed2)

# Moteur arriere
PIN_RELAY_REAR_ON     = 21   # RL3 : ON/OFF moteur arriere (LOW=ON  / HIGH=OFF)

# Pompe (inchange)
PIN_PUMP_FWD          = 24   # pompe avant (actif HIGH)
PIN_PUMP_BWD          = 25   # pompe arriere (actif HIGH)

# Rest Contact (pull-down, HIGH = lame en position repos)
PIN_REST_CONTACT      = 26   # bouton pull-down 10kohm

# Logique relais
RELAY_ON    = 0   # LOW  = relais active  (actif LOW)
RELAY_OFF   = 1   # HIGH = relais inactif
RELAY_SPEED1 = 1  # HIGH = Speed1 (vitesse lente)
RELAY_SPEED2 = 0  # LOW  = Speed2 (vitesse rapide)

# Rest contact hardware toujours present sur ce prototype
REST_CONTACT_HARDWARE_PRESENT = False

# =====================================================
# RTE -- RUNTIME ENVIRONMENT
# =====================================================
class RTE:
    """
    Memoire partagee entre toutes les couches.
    AUCUNE logique ici -- uniquement stockage thread-safe.
    """

    def __init__(self):
        self._lock = threading.RLock()

        # ── [CAN] ────────────────────────────────────────
        self.ignition_status = 1
        self.reverse_gear    = False
        self.vehicle_speed   = 0
        self.rain_intensity  = 0
        self.rain_sensor_ok  = True

        # ── [LIN] ────────────────────────────────────────
        self.crs_wiper_op         = WOP_OFF
        self.crs_stick_valid      = True
        self.crs_alive_prev       = 0xFF
        self.crs_fault            = 0x00
        self.t_last_lin0x16       = 0.0
        self.t_last_lin0x17       = 0.0
        self.lin_timeout_active   = False
        self._auto_ignored_logged = False
        self._rear_ignored_logged = False

        # ── [WSM] ────────────────────────────────────────
        self.state            = ST_OFF
        self.prev_state       = ST_OFF
        self.t_motor_stop     = 0.0
        self.t_rear_last      = 0.0
        self._reverse_active  = False

        self.t_touch_start      = 0.0
        self.wash_cycles_done   = 0
        self.t_wash_cycle_start = 0.0
        self._one_shot_armed    = True
        self._freeze_pending    = False
        self._freeze_last_op    = WOP_OFF
        self._auto_speed_prev   = -1

        # ── [PUMP] ───────────────────────────────────────
        self.pump_active             = False
        self.pump_direction          = 0
        self.t_pump_start            = 0.0
        self._pump_overcurrent_start = 0.0

        # ── [ACTIONNEURS -- etat temps reel] ──────────────
        self.front_motor_on     = False   # relais RL2 etat (True=ON)
        self.front_motor_speed  = 0       # 0=off / 1=speed1 / 2=speed2
        self.front_blade_moving = False
        self.rear_motor_on      = False   # relais RL3 etat (True=ON)
        self.rear_motor_running = False
        self.pump_dir_active    = 0

        # ── [COURANT MOTEUR -- ADS1115] ───────────────────
        # Valeur lue toutes les ADS_READ_PERIOD ms par T-PUMP
        # Unité : amperes (0.0 - 1.0A)
        self.motor_current_a         = 0.0
        self.pump_current_a          = 0.0   # courant pompe ACS712 (amperes)
        self.pump_voltage_v          = 0.0   # tension charge pompe (volts)
        self.t_overcurrent_start     = {}
        self._pump_overcurrent_start = 0.0

        # ── [SECURITE] ───────────────────────────────────
        self._rest_contact_stuck_start  = 0.0
        self._rest_contact_last_state   = -1
        self._rest_contact_b2009_active = False

        # ── [DIAG UDS] ───────────────────────────────────
        self._session            = 1
        self._sec_level          = 0
        self._pending_seed       = {}
        self._comm_tx_enabled    = True
        self._comm_rx_enabled    = True
        self._watchdog_kick_time = time.time()

        self._test_active        = False
        self._test_routine       = 0
        self._test_duration      = 0
        self._t_test_start       = 0.0

        # ── [DoIP -- requete entrante decodee] ────────────────
        self._uds_mutex          = threading.Lock()
        self._uds_event          = threading.Event()

        self.uds_sid             = 0
        self.uds_payload         = b""
        self.uds_request_pending = False
        self.uds_response        = b""
        self.uds_response_ready  = False
        self.uds_src_addr        = 0
        self.uds_dst_addr        = 0

        # ── [CODING] ─────────────────────────────────────
        self.rain_sensor_installed = False
        self.rear_wiper_available  = True
        self.channel_front_wash    = 0
        self.channel_rear_camera   = 1

    # ─────────────────────────────────────────────────
    # Acces generique thread-safe
    # ─────────────────────────────────────────────────
    def get(self, key: str):
        with self._lock:
            return getattr(self, key)

    def set(self, key: str, value):
        with self._lock:
            setattr(self, key, value)

    def set_multi(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, k, v)

    def make_snapshot(self) -> dict:
        with self._lock:
            return {
                "ignition":    self.ignition_status,
                "wiper_mode":  self.state,
                "motor_curr":  int(self.motor_current_a * 1000),  # converti en mA pour DTC
                "blade_pos":   1 if self.front_blade_moving else 0,
                "rain":        self.rain_intensity,
                "vehicle_spd": self.vehicle_speed,
            }

    def __repr__(self):
        with self._lock:
            return (f"RTE(state={self.state}, "
                    f"req={WOP_NAMES.get(self.crs_wiper_op,'?')}, "
                    f"ign={self.ignition_status}, "
                    f"current={self.motor_current_a:.2f}A, "
                    f"pump={self.pump_active})")