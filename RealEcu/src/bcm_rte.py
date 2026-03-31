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
import json

# =====================================================
# REDIS -- Publication RTE + Ecoute commandes
# =====================================================
_REDIS_AVAILABLE = False
try:
    import redis as _redis_mod
    _REDIS_AVAILABLE = True
except ImportError:
    print("[RTE] package 'redis' absent -- pip install redis")

# Cles publiees dans Redis (rte:<key>)
REDIS_PUBLIC_KEYS = [
    "state", "crs_wiper_op", "ignition_status", "reverse_gear",
    "vehicle_speed", "rain_intensity", "front_motor_on",
    "front_motor_speed", "rear_motor_on", "rear_motor_running",
    "pump_active", "pump_direction", "motor_current_a",
    "pump_current_a", "pump_voltage_v", "pump_v_b", "pump_v_a",
    "pump_fault_mode", "pump_fault_target",
    "lin_timeout_active",
    "wc_available", "wc_timeout_active",
    "front_motor_error", "rear_motor_error", "pump_error",  # erreurs individuelles
    "rest_contact_raw",    # etat GPIO26 temps reel (True=lame EN MOUVEMENT)
    "front_blade_cycles",  # compteur cycles lame avant (incremente par rest_contact)
    "crs_fault",           # CRS_InternalFault recu via trame LIN 0x17
]

# Cles modifiables depuis la Platform via Redis pub/sub (stimuli tests)
REDIS_WRITABLE_KEYS = frozenset([
    "crs_wiper_op", "ignition_status", "rain_intensity",
    "vehicle_speed", "reverse_gear",
    "motor_current_a",          # T38 : injection surcourant depuis Platform
    "wc_timeout_active",        # T11 post_test : reset après CAN timeout
    "lin_timeout_active",       # T10/T39 post_test : reset après LIN timeout
    "rain_sensor_installed",    # T34/T35 : activer capteur pluie pour AUTO
    "rest_contact_sim",         # T20/T36 : injection rest contact depuis Platform
                                # True=bouton appuyé=lame EN MOUVEMENT
                                # False=bouton relâché=lame AU REPOS
    "rest_contact_sim_active",  # True = Platform pilote rest_contact (tests auto)
                                # False = lecture GPIO hardware normale (défaut)
    "pump_fault_mode",          # Mode defaut pompe/moteur depuis Platform
    "pump_fault_target",        # Cible defaut : "POMPE" ou "MOTEUR"
])


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

LIN_CYCLE_0x16   = 0.400   # 400 ms  (conforme rasp -- modifie de 20ms -> 400ms)
LIN_CYCLE_0x17   = 0.800   # 800 ms  (conforme rasp -- valeur originale conservee)

LIN_TIMEOUT      = 2.000   # 2000 ms (conforme rasp -- valeur originale conservee)
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
# CAN -- Trames BCM <-> WC (Cas B : WcAvailable = Installed)
# MESSAGE CATALOGUE Section 2
# =====================================================
CAN_ID_WIPER_COMMAND = 0x200   # BCM → WC  Wiper_Command  (20ms)
CAN_ID_WIPER_STATUS  = 0x201   # WC  → BCM Wiper_Status   (20ms)
CAN_ID_WIPER_ACK     = 0x202   # WC  → BCM Wiper_Ack      (event)

# Periode emission Wiper_Command par BCM (Cas B)
CAN_WC_CMD_PERIOD    = 0.400   # 20ms

# Timeout supervision WC : si BCM ne recoit plus 0x201 -> B2005
# Modifie de 100ms → 2000ms (demande utilisateur)
CAN_WC_TIMEOUT       = 2.000   # 2000ms (ancien: 100ms)

# =====================================================
# TIMINGS THREADS
# =====================================================
CONTROL_LOOP_PERIOD   = 0.200   # T-WSM  : 200 ms
PUMP_GUARD_PERIOD     = 0.010   # T-PUMP : 200 ms
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
WATCHDOG_MAX_MS         = 50   # seuil watchdog : 250ms (x10 = 2500ms avant reset)
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
ADS_PUMP_CHANNEL    = 0         # canal A0 noeud B diviseur pompe
ADS_PUMP_R_CHARGE   = 10.0      # resistance de charge serie (ohm) -- loi d'Ohm courant
ADS_PUMP_R_HAUTE    = 10000.0   # diviseur haute (ohm) noeud A -> noeud B
ADS_PUMP_R_BASSE    =  1900.0   # diviseur basse (ohm) noeud B -> GND
ADS_PUMP_RATIO_DIV  = ADS_PUMP_R_BASSE / (ADS_PUMP_R_HAUTE + ADS_PUMP_R_BASSE)  # ~0.160
ADS_PUMP_NOISE      = 0.005     # seuil bruit V_b (V) en dessous -> 0
ADS_PUMP_NB_SAMPLES = 5         # nb echantillons mediane pour lecture pompe

# Modes defaut pompe / moteur
PUMP_FAULT_NORMAL   = "NORMAL"
PUMP_FAULT_OPEN_LOAD= "OPEN LOAD"
PUMP_FAULT_SIG_VAR  = "SIGNAL VARIABLE"
PUMP_FAULT_SHORT_VCC= "SHORT TO VCC"
PUMP_FAULT_MODES    = {PUMP_FAULT_NORMAL, PUMP_FAULT_OPEN_LOAD,
                       PUMP_FAULT_SIG_VAR, PUMP_FAULT_SHORT_VCC}
PUMP_FAULT_ISO_OUVERT = {PUMP_FAULT_OPEN_LOAD, PUMP_FAULT_SIG_VAR, PUMP_FAULT_SHORT_VCC}

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

# Relais / MUX modes defaut (H-bridge v5.5)
PIN_ISO               = 19   # canal 2 relais -- noeud B -> A0 (LOW=ISO ON)
PIN_ISO_MOT           = 17   # canal 3 relais -- POT moteur -> A3 (LOW=ON)
PIN_ISO_DEF           = 27   # canal 4 relais -- signal defaut -> A3 (LOW=ON)
PIN_SPDT              = 12   # canal 1 relais -- cible POMPE(HIGH) / MOTEUR(LOW)
PIN_MUX_A             =  6   # CD4051 select A (LSB)
PIN_MUX_B             = 13   # CD4051 select B (MSB)
PIN_Y0_GATE           =  5   # gate IRLZ44N OPEN LOAD
PIN_Y2_BASE           = 16   # base 2N1919 SHORT TO VCC

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
        self.ignition_status =1
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

        # ── [ERREURS INDIVIDUELLES -- composant isole] ────
        # Chaque composant a son propre flag d'erreur independant.
        # Une erreur moteur n'affecte pas la pompe, et vice versa.
        self.front_motor_error  = False   # B2001 : surcourant moteur avant
        self.rear_motor_error   = False   # B2002 : surcourant moteur arriere
        self.pump_error         = False   # B2003 : surcourant pompe

        # ── [COURANT MOTEUR -- ADS1115] ───────────────────
        # Valeur lue toutes les ADS_READ_PERIOD ms par T-PUMP
        # Unité : amperes (0.0 - 1.0A)
        self.motor_current_a         = 0.0
        self.pump_current_a          = 0.0   # courant pompe ACS712 (amperes)
        self.pump_voltage_v          = 0.0   # tension charge pompe (volts)
        self.pump_v_b                = 0.0   # tension brute ADS1115 (volts)
        self.pump_v_a                = 0.0   # tension calculee noeud A (volts)
        self.t_overcurrent_start     = {}
        self._pump_overcurrent_start = 0.0

        # ── [MODES DEFAUT H-BRIDGE] ───────────────────────
        self.pump_fault_mode         = "NORMAL"   # NORMAL / OPEN LOAD / SIGNAL VARIABLE / SHORT TO VCC
        self.pump_fault_target       = "POMPE"    # POMPE / MOTEUR
        self._pump_vb_last_fwd       = 0.0        # [C6] derniere V_b FORWARD memorisee

        # ── [SECURITE] ───────────────────────────────────
        self._rest_contact_stuck_start  = 0.0
        self._rest_contact_last_state   = -1
        self._rest_contact_b2009_active = False
        self._rest_contact_b2006_active = False  # garde anti-boucle B2006 (reset par Clear/Reset UDS)
        self._rest_contact_prev         = None   # etat precedent (edge detection cycles)
        self._front_blade_cycles        = 0      # compteur cycles lame avant
        # Variables publiques Redis (lues par Platform)
        self.rest_contact_raw           = False  # etat GPIO26 brut (True=lame EN MOUVEMENT)
        self.front_blade_cycles         = 0      # alias public de _front_blade_cycles
        self.crs_fault                  = 0x00   # CRS_InternalFault recu LIN 0x17
        # Injection test Platform (T20/T36)
        self.rest_contact_sim_active    = False  # False = GPIO hardware (défaut)
        self.rest_contact_sim           = False  # valeur injectée par Platform

        # ── [DIAG UDS] ───────────────────────────────────
        self._session            = 1
        self._sec_level          = 0
        self._pending_seed       = {}
        self._comm_tx_enabled    = True
        self._comm_rx_enabled    = True
        self._watchdog_kick_time = time.time()

        self._test_active        = False

        # ── [REDIS] ──────────────────────────────────────
        self._redis    = None
        self._redis_ok = False
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

        # ── [CAS B -- WC disponible] ──────────────────────
        # Quand wc_available=True, le BCM envoie CAN 0x200 vers WC
        # et WC commande le moteur avant (Cas B).
        # Quand wc_available=False, le BCM commande directement
        # les relais moteur avant (Cas A).
        self.wc_available           = False  # code via WDID 0xF201
        self.t_last_wiper_status    = 0.0    # timestamp derniere trame 0x201 recue
        self.wc_timeout_active      = False  # B2005 CAN Timeout WC
        self.wc_alive_rx            = 0      # AliveCounter recu de WC
        self.wc_can_alive_tx        = 0      # AliveCounter emis par BCM

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


    # ─────────────────────────────────────────────────
    # REDIS -- Publication + Ecoute commandes
    # ─────────────────────────────────────────────────
    def redis_connect(self, host: str = "127.0.0.1", port: int = 6379) -> bool:
        """
        Connecte le RTE a Redis.
        Appele une seule fois depuis bcm_main.py apres creation du RTE.
        Memorise host/port pour la reconnexion automatique.
        """
        if not _REDIS_AVAILABLE:
            print("[RTE-REDIS] redis non disponible -- tests BCM via backup CAN")
            return False
        # Memoriser pour reconnexion automatique
        self._redis_host = host
        self._redis_port = port
        return self._redis_reconnect()

    def _redis_reconnect(self) -> bool:
        """
        (Re)connecte Redis. Appele depuis redis_connect() et en cas d'erreur.
        Thread-safe via _lock deja acquis ou non (utilise son propre mutex).
        """
        if not _REDIS_AVAILABLE:
            return False
        host = getattr(self, "_redis_host", "127.0.0.1")
        port = getattr(self, "_redis_port", 6379)
        try:
            # Fermer l'ancienne connexion proprement
            if self._redis is not None:
                try:
                    self._redis.close()
                except Exception:
                    pass
            self._redis = _redis_mod.Redis(
                host=host, port=port, db=0,
                socket_connect_timeout=2,
                socket_timeout=1,
                # Desactive le pool de connexions interne pour eviter les
                # chevauchements entre T-REDIS (publish) et T-REDIS-CMD (pubsub).
                # Chaque thread utilise sa propre instance Redis.
                connection_pool=_redis_mod.ConnectionPool(
                    host=host, port=port, db=0,
                    socket_connect_timeout=2,
                    socket_timeout=1,
                    max_connections=4,
                ),
            )
            self._redis.ping()
            self._redis_ok = True
            print(f"[RTE-REDIS] Connecte sur {host}:{port}")
            return True
        except Exception as e:
            self._redis_ok = False
            self._redis = None
            print(f"[RTE-REDIS] Connexion impossible ({e}) -- mode degrade")
            return False

    def redis_publish(self) -> None:
        """
        Publie toutes les cles REDIS_PUBLIC_KEYS dans Redis (rte:<key>).
        Appele par T-REDIS toutes les 100ms.
        Publie aussi sur le canal rte_changed la liste des cles mises a jour.

        Gestion d'erreur robuste :
        - En cas d'erreur Redis transitoire, on tente UNE reconnexion.
        - Si la reconnexion echoue, on attend le prochain cycle (100ms).
        - N'influence JAMAIS les communications TCP/LIN/CAN reelles.
        """
        if not self._redis_ok or self._redis is None:
            # Tentative de reconnexion periodique (toutes les ~5s via 50 cycles)
            if not hasattr(self, "_redis_retry_ctr"):
                self._redis_retry_ctr = 0
            self._redis_retry_ctr += 1
            if self._redis_retry_ctr >= 50:
                self._redis_retry_ctr = 0
                self._redis_reconnect()
            return

        try:
            # Snapshot atomique sous lock (ne jamais tenir le lock pendant IO Redis)
            with self._lock:
                snapshot = {}
                for k in REDIS_PUBLIC_KEYS:
                    val = getattr(self, k, None)
                    snapshot[k] = str(val).lower() if isinstance(val, bool) else str(val)

            # Pipeline non-transactionnel pour performance maximale
            pipe = self._redis.pipeline(transaction=False)
            for key, encoded in snapshot.items():
                pipe.set(f"rte:{key}", encoded, ex=10)   # TTL 10s securite
            pipe.publish("rte_changed", json.dumps(list(snapshot.keys())))
            pipe.execute()

        except _redis_mod.ConnectionError as e:
            print(f"[RTE-REDIS] Connexion perdue ({e}) -- reconnexion...")
            self._redis_ok = False
            # Reconnexion immediate sans bloquer T-REDIS longtemps
            self._redis_reconnect()
        except _redis_mod.TimeoutError as e:
            # Timeout court (1s) : on logue et on continue sans bloquer
            print(f"[RTE-REDIS] Timeout publish ({e}) -- cycle suivant")
        except Exception as e:
            # Toute autre erreur Redis : logue, desactive, reconnexion au prochain cycle
            print(f"[RTE-REDIS] Erreur publish inattendue: {e}")
            self._redis_ok = False

    def redis_apply_cmd(self, key: str, value) -> bool:
        """
        Applique une commande SET venue de la Platform via Redis pub/sub.
        Seules les cles REDIS_WRITABLE_KEYS sont acceptees (securite).
        Retourne True si appliquee.
        """
        if key not in REDIS_WRITABLE_KEYS:
            print(f"[RTE-REDIS] Cle refusee (non inscriptible): {key}")
            return False
        try:
            with self._lock:
                attr_type = type(getattr(self, key, 0))
            # Convertir selon le type reel de l'attribut
            if attr_type == bool:
                typed = str(value).lower() in ("true", "1", "yes")
            elif attr_type == float:
                typed = float(value)
            else:
                typed = int(value)
            self.set(key, typed)
            print(f"[RTE-REDIS] SET {key}={typed} (type={attr_type.__name__})")
            return True
        except Exception as e:
            print(f"[RTE-REDIS] Erreur apply_cmd({key}={value}): {e}")
            return False

    def __repr__(self):
        with self._lock:
            return (f"RTE(state={self.state}, "
                    f"req={WOP_NAMES.get(self.crs_wiper_op,'?')}, "
                    f"ign={self.ignition_status}, "
                    f"current={self.motor_current_a:.2f}A, "
                    f"pump={self.pump_active})")