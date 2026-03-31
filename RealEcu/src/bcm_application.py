#!/usr/bin/env python3
"""
bcm_application.py
==================
Couche Application -- Machine d'etat + Fonctions Wiper + Diagnostic UDS
WipeWash System -- Architecture 3 Couches

ADS1115 chip (I2C)
  ├── Canal A3 ── Potentiomètre ── _read_ads_current() ── rte.motor_current_a
  └── Canal A0 ── H-Bridge diviseur ─ _read_ads_pump()  ── rte.pump_current_a
                  noeud B (R_HAUTE/R_BASSE)                 rte.pump_voltage_v
                                                            rte.pump_v_b / pump_v_a
                                                            rte.pump_voltage_v
"""

import os
import struct
import threading
import time

from bcm_rte import (
    RTE,
    ST_OFF, ST_TOUCH, ST_SPEED1, ST_SPEED2, ST_AUTO,
    ST_WASH_FRONT, ST_WASH_REAR, ST_REAR_WIPE, ST_ERROR, ST_DIAG, ST_ENC,
    WOP_OFF, WOP_TOUCH, WOP_SPEED1, WOP_SPEED2, WOP_AUTO,
    WOP_FRONT_WASH, WOP_REAR_WASH, WOP_REAR_WIPE, WOP_NAMES,
    TOUCH_DURATION, PUMP_MAX_RUNTIME,
    WASH_FRONT_CYCLES, WASH_REAR_CYCLES,
    RAIN_SPEED2_THRESH,
    OVERCURRENT_THRESH, OVERCURRENT_DELAY,
    PUMP_OVERCURRENT_THRESH, PUMP_OVERCURRENT_DELAY,
    REST_STUCK_DELAY, REVERSE_REAR_PERIOD,
    WATCHDOG_MAX_MS, WIPE_CYCLE_DURATION,
    PIN_RELAY_FRONT_ON, PIN_RELAY_FRONT_SPEED,
    PIN_RELAY_REAR_ON,
    PIN_PUMP_FWD, PIN_PUMP_BWD,
    PIN_ISO, PIN_ISO_MOT, PIN_ISO_DEF, PIN_SPDT,
    PIN_MUX_A, PIN_MUX_B, PIN_Y0_GATE, PIN_Y2_BASE,
    PIN_REST_CONTACT,
    RELAY_ON, RELAY_OFF, RELAY_SPEED1, RELAY_SPEED2,
    REST_CONTACT_HARDWARE_PRESENT,
    CONTROL_LOOP_PERIOD, PUMP_GUARD_PERIOD,
    ACTUATOR_TEST_PERIOD,
    SA_REQ_SEED, SA_SEND_KEY, SA_XOR_MASK, SA_ADD_MASK,
    ADS_VOLTAGE_MIN, ADS_VOLTAGE_MAX,
    ADS_CURRENT_MIN, ADS_CURRENT_MAX,
    ADS_GAIN, ADS_CHANNEL, ADS_READ_PERIOD,
    ADS_PUMP_CHANNEL,
    ADS_PUMP_NOISE, ADS_PUMP_R_CHARGE, ADS_PUMP_R_HAUTE, ADS_PUMP_R_BASSE,
    ADS_PUMP_RATIO_DIV, ADS_PUMP_NB_SAMPLES,
    PUMP_FAULT_NORMAL, PUMP_FAULT_OPEN_LOAD, PUMP_FAULT_SIG_VAR,
    PUMP_FAULT_SHORT_VCC, PUMP_FAULT_MODES, PUMP_FAULT_ISO_OUVERT,
)

from bcm_tcp_broadcast import TCPBroadcast
from bcm_tcp_pump     import TCPPumpBroadcast
from bcm_protocol import (
    SID_DSC, SID_RESET, SID_CLEAR, SID_RDTC,
    SID_RDID, SID_WDID, SID_SA, SID_CC, SID_RC, SID_TP,
    DSC_DEFAULT, DSC_EXTENDED,
)

# =====================================================
# GPIO via RPi.GPIO
# =====================================================
GPIO_AVAILABLE = False

try:
    import RPi.GPIO as GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO_AVAILABLE = True
    print("[GPIO] RPi.GPIO disponible")
except ImportError:
    print("[GPIO] RPi.GPIO non disponible -- mode simulation")


# =====================================================
# ADS1115 -- Lecture courant moteur + pompe
# =====================================================
ADS_AVAILABLE     = False
_ads_channel      = None
_ads_pump_channel = None

# Offset calibre mode OPEN LOAD (moyenne 5 lectures, delai 300ms)
_pump_offset_open_load = 0.0

try:
    import board
    import busio
    from adafruit_ads1x15 import ads1115 as ADS
    from adafruit_ads1x15.analog_in import AnalogIn

    _i2c         = busio.I2C(board.SCL, board.SDA)
    _ads         = ADS.ADS1115(_i2c)
    _ads.gain    = ADS_GAIN
    _ads_channel      = AnalogIn(_ads, ADS_CHANNEL)
    _ads_pump_channel = AnalogIn(_ads, ADS_PUMP_CHANNEL)
    ADS_AVAILABLE = True
    print(f"[ADS1115] Initialise | gain={ADS_GAIN} | canal=A{ADS_CHANNEL}")
    print(f"[ADS1115] Pompe H-bridge diviseur | canal=A{ADS_PUMP_CHANNEL}")
    print(f"[ADS1115] RATIO_DIV={ADS_PUMP_RATIO_DIV:.4f}  R_CHARGE={ADS_PUMP_R_CHARGE}ohm")
except Exception as e:
    print(f"[ADS1115] ERREUR INIT : {type(e).__name__}: {e}")
    print("[ADS1115] Courant/tension simules a 0")
    _ads_pump_channel = None





# =====================================================
# CONTROLE GPIO MODES DEFAUT H-BRIDGE
# =====================================================

def _fault_gpio_activer_isolation(cible: str):
    """
    [C1] Active l'isolation selon la cible.
    Cible POMPE  : ISO OFF (noeud B isole de A0).
    Cible MOTEUR : ISO_MOT OFF (POT isole de A3) + ISO_DEF ON (signal defaut -> A3).
    """
    if not GPIO_AVAILABLE:
        return
    if cible == "MOTEUR":
        GPIO.output(PIN_ISO_MOT, GPIO.HIGH)   # POT isole de A3
        GPIO.output(PIN_ISO_DEF, GPIO.LOW)    # signal defaut connecte a A3
        print("[ISO] ISO_MOT OFF  POT isole | ISO_DEF ON  defaut->A3")
    else:
        GPIO.output(PIN_ISO,     GPIO.HIGH)   # noeud B isole de A0
        GPIO.output(PIN_ISO_MOT, GPIO.HIGH)   # POT isole de A3
        GPIO.output(PIN_ISO_DEF, GPIO.HIGH)   # A3 isole  cible POMPE
        print("[ISO] ISO OFF  noeud B isole | ISO_MOT OFF | ISO_DEF OFF")


def _fault_gpio_desactiver_isolation():
    """Remet toutes les sources en mode mesure normale."""
    if not GPIO_AVAILABLE:
        return
    GPIO.output(PIN_ISO,     GPIO.LOW)    # noeud B connecte a A0
    GPIO.output(PIN_ISO_MOT, GPIO.LOW)    # POT connecte a A3
    GPIO.output(PIN_ISO_DEF, GPIO.HIGH)   # signal defaut deconnecte
    print("[ISO] ISO ON | ISO_MOT ON | ISO_DEF OFF  -- mesure normale")


def _fault_gpio_desactiver_sources():
    """Eteint tous les generateurs de defaut avant de changer de mode."""
    if not GPIO_AVAILABLE:
        return
    GPIO.output(PIN_Y0_GATE, GPIO.LOW)
    GPIO.output(PIN_Y2_BASE, GPIO.LOW)
    GPIO.output(PIN_MUX_A,   GPIO.LOW)
    GPIO.output(PIN_MUX_B,   GPIO.LOW)


def _fault_gpio_set_cible(cible: str, rte):
    """Configure le relais SPDT pour cibler POMPE (A0) ou MOTEUR (A3)."""
    if not GPIO_AVAILABLE:
        return
    GPIO.output(PIN_ISO,     GPIO.HIGH)
    GPIO.output(PIN_ISO_MOT, GPIO.HIGH)
    GPIO.output(PIN_ISO_DEF, GPIO.HIGH)
    time.sleep(0.02)
    if cible == "MOTEUR":
        GPIO.output(PIN_SPDT, GPIO.LOW)
        print("[DEFAUT] Cible MOTEUR  NO1->COM4->NC4->ADS1115 A3")
    else:
        GPIO.output(PIN_SPDT, GPIO.HIGH)
        print("[DEFAUT] Cible POMPE  NC1->ADS1115 A0")

    if rte.pump_fault_mode == PUMP_FAULT_NORMAL:
        time.sleep(0.02)
        GPIO.output(PIN_ISO,     GPIO.LOW)
        GPIO.output(PIN_ISO_MOT, GPIO.LOW)
        GPIO.output(PIN_ISO_DEF, GPIO.HIGH)
        print("[ISO] Sources reconnectees (mode NORMAL)")


def _fault_gpio_open_load(cible: str):
    """Active mode OPEN LOAD sur la cible."""
    _fault_gpio_desactiver_sources()
    _fault_gpio_activer_isolation(cible)
    if GPIO_AVAILABLE:
        GPIO.output(PIN_MUX_A,   GPIO.LOW)
        GPIO.output(PIN_MUX_B,   GPIO.LOW)
        GPIO.output(PIN_Y0_GATE, GPIO.LOW)
    print("[DEFAUT] OPEN LOAD actif -- calibration offset...")


def _fault_gpio_signal_variable(cible: str):
    """Active mode SIGNAL VARIABLE (POT externe Y1 via MUX)."""
    _fault_gpio_desactiver_sources()
    _fault_gpio_activer_isolation(cible)
    if GPIO_AVAILABLE:
        GPIO.output(PIN_MUX_A, GPIO.HIGH)
        GPIO.output(PIN_MUX_B, GPIO.LOW)
    print("[DEFAUT] SIGNAL VARIABLE actif  regler POT CTRL")


def _fault_gpio_short_vcc(cible: str):
    """Active mode SHORT TO VCC via transistor Y2."""
    _fault_gpio_desactiver_sources()
    _fault_gpio_activer_isolation(cible)
    if GPIO_AVAILABLE:
        GPIO.output(PIN_MUX_A,   GPIO.LOW)
        GPIO.output(PIN_MUX_B,   GPIO.HIGH)
        GPIO.output(PIN_Y2_BASE, GPIO.HIGH)
    print("[DEFAUT] SHORT TO VCC actif  Y2 base HIGH")


def _fault_gpio_retour_normal():
    """Desactive tous les modes defaut et remet en mesure normale."""
    _fault_gpio_desactiver_sources()
    _fault_gpio_desactiver_isolation()
    print("[DEFAUT] Retour NORMAL -- toutes sources OFF")


def _calibrer_offset_open_load(canal) -> float:
    """
    [C4] Moyenne 5 lectures + delai 300ms de stabilisation.
    Retourne l'offset mesure.
    """
    global _pump_offset_open_load
    time.sleep(0.3)
    lectures = []
    for _ in range(5):
        try:
            lectures.append(abs(canal.voltage))
            time.sleep(0.05)
        except OSError as e:
            print(f"[CALIB] Erreur lecture : {e}")
    if lectures:
        offset = sum(lectures) / len(lectures)
        _pump_offset_open_load = offset
        print(f"[CALIB] Offset OPEN LOAD ({len(lectures)} lectures) : {offset:.4f} V")
    else:
        _pump_offset_open_load = 0.0
        print("[CALIB] Erreur lecture offset -- compensation desactivee")
    return _pump_offset_open_load

# =====================================================
# LECTURE COURANT MOTEUR WIPER (ADS1115 A3)
# =====================================================
def _read_ads_current() -> float:
    """
    Lit le courant moteur wiper via ADS1115 canal A3.
    Remapping lineaire : ADS_VOLTAGE_MIN->0A / ADS_VOLTAGE_MAX->1A
    Retourne 0.0 si ADS non disponible.
    """
    if not ADS_AVAILABLE or _ads_channel is None:
        return 0.0
    try:
        voltage = _ads_channel.voltage
        ratio   = (voltage - ADS_VOLTAGE_MIN) / (ADS_VOLTAGE_MAX - ADS_VOLTAGE_MIN)
        current = ratio * ADS_CURRENT_MAX
        return round(max(ADS_CURRENT_MIN, min(ADS_CURRENT_MAX, current)), 3)
    except Exception as e:
        print(f"[ADS1115] Erreur lecture moteur : {e}")
        return 0.0


# =====================================================
# LECTURE COURANT + TENSION POMPE (ADS1115 A0 / H-Bridge)
# =====================================================

def _read_ads_pump(rte) -> tuple:
    """
    Lit courant et tension pompe via diviseur H-bridge (canal A0).

    Schema :
      OUT1 L298N --- R_CHARGE(10ohm) --- noeud A --- R_CHARGE(10ohm) --- OUT2
      noeud A --- R_HAUTE(10k) --- noeud B --- R_BASSE(1.9k) --- GND
      ADS1115 A0 mesure noeud B (via relais ISO)

    Formules :
      V_b = lecture ADS1115 A0
      V_a = V_b / RATIO_DIV      (RATIO = R_BASSE / (R_HAUTE + R_BASSE))
      I   = V_a / R_CHARGE

    [C2] Lecture bloquee si mode defaut ISO ouvert + pompe active + cible POMPE.
    [C6] En BACKWARD : estimation depuis derniere V_b FORWARD memorisee.
    Retourne (current_A, voltage_v, v_b, v_a).
    """
    fault_mode   = rte.pump_fault_mode
    fault_target = rte.pump_fault_target
    pump_active  = rte.pump_active
    direction    = rte.pump_direction   # 1=FWD / 2=BWD

    # [C6] BACKWARD : estimation depuis derniere mesure FORWARD
    if pump_active and direction == 2:
        v_b     = rte._pump_vb_last_fwd
        v_a     = v_b / ADS_PUMP_RATIO_DIV if ADS_PUMP_RATIO_DIV > 0 else 0.0
        current = v_a / ADS_PUMP_R_CHARGE
        voltage = round(v_a, 3)
        return round(current, 3), round(voltage, 3), round(v_b, 4), round(v_a, 4)

    # [C2] Mode defaut ISO ouvert + pompe active + cible POMPE : lecture bloquee
    if fault_mode in PUMP_FAULT_ISO_OUVERT and fault_target == "POMPE" and pump_active:
        return 0.0, 0.0, 0.0, 0.0

    if not ADS_AVAILABLE or _ads_pump_channel is None:
        return 0.0, 0.0, 0.0, 0.0

    # Collecte echantillons
    samples = []
    for _ in range(ADS_PUMP_NB_SAMPLES):
        try:
            samples.append(abs(_ads_pump_channel.voltage))
            time.sleep(0.005)
        except OSError as e:
            print(f"[ADS-PUMP] Erreur I2C : {e}")
            time.sleep(0.02)

    if not samples:
        return 0.0, 0.0, 0.0, 0.0

    # Mediane robuste
    s   = sorted(samples)
    n   = len(s)
    v_b = (s[n//2 - 1] + s[n//2]) / 2 if n % 2 == 0 else s[n//2]

    # Compensation offset OPEN LOAD
    if fault_mode == PUMP_FAULT_OPEN_LOAD and fault_target == "POMPE":
        v_b = max(0.0, v_b - _pump_offset_open_load)

    # Seuil bruit
    if v_b < ADS_PUMP_NOISE:
        v_b = 0.0

    v_a     = v_b / ADS_PUMP_RATIO_DIV if ADS_PUMP_RATIO_DIV > 0 else 0.0
    current = v_a / ADS_PUMP_R_CHARGE
    voltage = v_a   # tension noeud A = tension aux bornes de la charge

    # [C6] Memorise V_b si FORWARD
    if pump_active and direction == 1:
        rte._pump_vb_last_fwd = v_b

    return round(current, 3), round(voltage, 3), round(v_b, 4), round(v_a, 4)


# =====================================================
# PRIMITIVES GPIO -- RELAIS
# =====================================================

def _gpio_setup():
    """Initialise toutes les broches GPIO."""
    if not GPIO_AVAILABLE:
        return
    GPIO.setup(PIN_RELAY_FRONT_ON,    GPIO.OUT, initial=RELAY_OFF)
    GPIO.setup(PIN_RELAY_FRONT_SPEED, GPIO.OUT, initial=RELAY_SPEED1)
    GPIO.setup(PIN_RELAY_REAR_ON,     GPIO.OUT, initial=RELAY_OFF)
    GPIO.setup(PIN_PUMP_FWD,          GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(PIN_PUMP_BWD,          GPIO.OUT, initial=GPIO.LOW)
    # Relais modes defaut
    GPIO.setup(PIN_ISO,               GPIO.OUT, initial=GPIO.LOW)    # ISO ON (noeud B -> A0)
    GPIO.setup(PIN_ISO_MOT,           GPIO.OUT, initial=GPIO.LOW)    # ISO_MOT ON (POT -> A3)
    GPIO.setup(PIN_ISO_DEF,           GPIO.OUT, initial=GPIO.HIGH)   # ISO_DEF OFF
    GPIO.setup(PIN_SPDT,              GPIO.OUT, initial=GPIO.HIGH)   # cible POMPE par defaut
    GPIO.setup(PIN_MUX_A,             GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(PIN_MUX_B,             GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(PIN_Y0_GATE,           GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(PIN_Y2_BASE,           GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(PIN_REST_CONTACT, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    print("[GPIO] Broches initialisees")
    print(f"  PIN_RELAY_FRONT_ON    = GPIO{PIN_RELAY_FRONT_ON}  (RL2 ON/OFF avant)")
    print(f"  PIN_RELAY_FRONT_SPEED = GPIO{PIN_RELAY_FRONT_SPEED}  (RL1 Speed1/Speed2)")
    print(f"  PIN_RELAY_REAR_ON     = GPIO{PIN_RELAY_REAR_ON}  (RL3 ON/OFF arriere)")
    print(f"  PIN_ISO               = GPIO{PIN_ISO}  (relais ISO noeud B)")
    print(f"  PIN_SPDT              = GPIO{PIN_SPDT}  (cible POMPE/MOTEUR)")
    print(f"  PIN_REST_CONTACT      = GPIO{PIN_REST_CONTACT}  (contact repos pull-down)")


def _gpio_cleanup():
    if not GPIO_AVAILABLE:
        return
    try:
        GPIO.cleanup()
        print("[GPIO] Libere")
    except Exception:
        pass


# =====================================================
# COUCHE APPLICATION
# =====================================================
class ApplicationLayer:

    def __init__(self, rte: RTE, dtc_manager):
        self._rte     = rte
        self._dtc     = dtc_manager
        self._running = False
        self._tcp      = TCPBroadcast()
        self._tcp_pump = TCPPumpBroadcast()
        _gpio_setup()



    # ==================================================
    # SECTION B -- PRIMITIVES MOTEUR / POMPE (RELAIS)
    # ==================================================

    def _front_motor_run(self, speed_level: int):
        # Cas B : si WcAvailable, le BCM ne touche pas les relais moteur avant.
        # La commande est transmise via CAN 0x200 par ProtocolLayer (thread T-CAN-WC).
        if getattr(self._rte, 'wc_available', False):
            print(f"[CAS B] Moteur avant -> commande CAN 0x200 speed={speed_level} "
                  f"(WC commande le relais)")
            return
        if not GPIO_AVAILABLE:
            print(f"[SIM] Moteur avant ON speed={speed_level}")
            return
        speed_val = RELAY_SPEED1 if speed_level == 1 else RELAY_SPEED2
        GPIO.output(PIN_RELAY_FRONT_SPEED, speed_val)
        GPIO.output(PIN_RELAY_FRONT_ON,    RELAY_ON)
        speed_name = "Speed1 (lente)" if speed_level == 1 else "Speed2 (rapide)"
        print(f"[MOTEUR] Avant ON | {speed_name} | "
              f"RL2=LOW(ON) RL1={'HIGH' if speed_level==1 else 'LOW'}")

    def _front_motor_stop(self):
        # Cas B : si WcAvailable, arret transmis via CAN 0x200 (WOP_OFF).
        if getattr(self._rte, 'wc_available', False):
            print("[CAS B] Moteur avant -> arret CAN 0x200 WOP_OFF")
            self._rte.set("t_motor_stop", time.time())
            return
        if not GPIO_AVAILABLE:
            print("[SIM] Moteur avant OFF")
            return
        GPIO.output(PIN_RELAY_FRONT_ON,    RELAY_OFF)
        GPIO.output(PIN_RELAY_FRONT_SPEED, RELAY_SPEED1)
        self._rte.set("t_motor_stop", time.time())
        print("[MOTEUR] Avant OFF | RL2=HIGH(OFF)")

    def _rear_motor_run(self):
        if not GPIO_AVAILABLE:
            print("[SIM] Moteur arriere ON")
            return
        GPIO.output(PIN_RELAY_REAR_ON, RELAY_ON)
        print("[MOTEUR] Arriere ON | RL3=LOW(ON)")

    def _rear_motor_stop(self):
        if not GPIO_AVAILABLE:
            print("[SIM] Moteur arriere OFF")
            return
        GPIO.output(PIN_RELAY_REAR_ON, RELAY_OFF)
        print("[MOTEUR] Arriere OFF | RL3=HIGH(OFF)")

    def _read_rest_contact(self) -> bool:
        """
        Lit l'état brut du GPIO contact repos (pull-down).
          GPIO = 0 (bouton relâché) -> False  -> lame AU REPOS
          GPIO = 1 (bouton appuyé)  -> True   -> lame EN MOUVEMENT

        MODE SIMULATION TEST (rest_contact_sim_active=True) :
          Retourne rte.rest_contact_sim injecté par la Platform via Redis.
          Permet T20/T36 sans intervention humaine sur le bouton GPIO26.
        """
        rte = self._rte
        # Injection Platform prioritaire (T20/T36)
        if rte.rest_contact_sim_active:
            result = bool(rte.rest_contact_sim)
        elif not GPIO_AVAILABLE:
            result = False   # simulation : lame consideree au repos
        else:
            result = bool(GPIO.input(PIN_REST_CONTACT))
        # Publier l'état brut dans le RTE → Redis → Platform
        rte.set("rest_contact_raw", result)
        return result

    def _track_blade_cycle(self, count_on_rest=True):
        """
        Détecte les cycles de la lame avant via le contact repos.
        
        Args:
            count_on_rest: True = compte un cycle quand la lame revient au repos (front descendant)
                           False = compte un cycle quand la lame commence un mouvement (front montant)
        """
        if not REST_CONTACT_HARDWARE_PRESENT and not self._rte.rest_contact_sim_active:
            return
        rte = self._rte
        # GPIO=1 (bouton appuye)  -> True  -> lame EN MOUVEMENT
        # GPIO=0 (bouton relache) -> False -> lame AU REPOS
        blade_moving = self._read_rest_contact()

        if rte._rest_contact_prev is None:
            rte._rest_contact_prev = blade_moving
            return

        # Détection selon le mode demandé
        if count_on_rest:
            # Compter quand la lame revient au repos (front descendant)
            if rte._rest_contact_prev is True and not blade_moving:
                rte._front_blade_cycles += 1
                rte.front_blade_cycles   = rte._front_blade_cycles  # sync public Redis
                print(f"[REST CONTACT] Cycle lame #{rte._front_blade_cycles} "
                      f"(contact repos atteint, etat={rte.state})")
                self._tcp.send(rte)
        else:
            # Compter quand la lame commence un mouvement (front montant)
            if not rte._rest_contact_prev and blade_moving:
                rte._front_blade_cycles += 1
                rte.front_blade_cycles   = rte._front_blade_cycles  # sync public Redis
                print(f"[REST CONTACT] Cycle lame #{rte._front_blade_cycles} "
                      f"(debut mouvement, etat={rte.state})")
                self._tcp.send(rte)

        rte._rest_contact_prev = blade_moving

    def _read_motor_current(self) -> float:
        real = _read_ads_current()
        rte  = self._rte
        # Si valeur injectée par test (> seuil) et ADS lit < 0.1A → ne pas écraser
        if rte.motor_current_a > 0.8 and real <  rte.motor_current_a:
            return rte.motor_current_a   # conserver l'injection test
        rte.set("motor_current_a", real)
        return real

    def _read_pump_current(self) -> tuple:
        current, voltage, v_b, v_a = _read_ads_pump(self._rte)
        self._rte.set_multi(
            pump_current_a = current,
            pump_voltage_v = voltage,
            pump_v_b       = v_b,
            pump_v_a       = v_a,
        )
        return current, voltage

    def _pump_start(self, direction: int):
        rte = self._rte
        if rte.pump_active:
            return
        rte.set_multi(
            pump_active            = True,
            pump_direction         = direction,
            t_pump_start           = time.time(),
            _pump_overcurrent_start= 0.0,
        )
        if GPIO_AVAILABLE:
            if direction == 1:
                GPIO.output(PIN_PUMP_FWD, GPIO.HIGH)
                GPIO.output(PIN_PUMP_BWD, GPIO.LOW)
            else:
                GPIO.output(PIN_PUMP_FWD, GPIO.LOW)
                GPIO.output(PIN_PUMP_BWD, GPIO.HIGH)
        dirs = {1: "FWD (FrontWash)", 2: "BWD (RearWash)"}
        print(f"[POMPE] Demarrage {dirs.get(direction,'?')}")
        self._log_sensors("POMPE START")
        self._tcp_pump.send(rte)

    def _pump_stop(self, reason: str = "normal"):
        rte = self._rte
        if not rte.pump_active:
            return
        elapsed = time.time() - rte.t_pump_start
        rte.set_multi(
            pump_active            = False,
            pump_direction         = 0,
            _pump_overcurrent_start= 0.0,
        )
        if GPIO_AVAILABLE:
            GPIO.output(PIN_PUMP_FWD, GPIO.LOW)
            GPIO.output(PIN_PUMP_BWD, GPIO.LOW)
        print(f"[POMPE] Arret ({reason}, runtime={elapsed:.1f}s)")
        self._log_sensors("POMPE STOP")
        self._tcp_pump.send(rte)

    def _stop_all(self):
        self._front_motor_stop()
        self._rear_motor_stop()
        self._pump_stop("stop_all")

    def _log_sensors(self, tag: str = ""):
        """
        Affiche courant moteur + courant/tension pompe en temps reel.
        Lit uniquement les valeurs deja presentes dans le RTE
        (mises a jour par T-PUMP) pour ne pas bloquer T-WSM avec
        des lectures I2C/ADS1115 synchrones.
        """
        rte    = self._rte
        prefix = f"[{tag}] " if tag else ""

        m_curr = self._read_motor_current()
        if rte.pump_active:
               p_curr, p_volt = self._read_pump_current()
        else:
              p_curr = 0.0
              p_volt = 0.0
              self._rte.set_multi(pump_current_a=0.0, pump_voltage_v=0.0)

        print(f"{prefix}"
              f"[MOTEUR] I={m_curr:.3f}A  "
              f"[POMPE]  I={p_curr:.3f}A  U={p_volt:.2f}V")

    # ==================================================
    # SECTION C -- MACHINE D'ETAT (WSM)
    # ==================================================

    # --------------------------------------------------
    # Nettoyage actionneurs avant changement d'etat
    # --------------------------------------------------
    # Groupes d'actionneurs mutuellement exclusifs :
    #   GROUPE_FRONT : etats utilisant le moteur avant
    #   GROUPE_REAR  : etats utilisant le moteur arriere
    #
    # Regle : si on quitte un etat FRONT pour aller vers
    # un etat REAR (ou inversement), on arrete les
    # actionneurs du groupe qu'on quitte AVANT d'entrer
    # dans le nouvel etat. Cela garantit qu'un moteur ne
    # reste jamais actif en arriere-plan.

    _STATES_USING_FRONT = {ST_TOUCH, ST_SPEED1, ST_SPEED2, ST_AUTO, ST_WASH_FRONT}
    _STATES_USING_REAR  = {ST_WASH_REAR, ST_REAR_WIPE}

    def _exit_current_state(self, new_state: str):
        """
        Arrete les actionneurs du groupe abandonne si le nouvel etat
        appartient a un groupe incompatible.

        Cas traites :
          FRONT  -> REAR  : arret moteur avant
          REAR   -> FRONT : arret moteur arriere (+ pompe BWD si active)
          ANY    -> OFF   : rien (les _enter_off/_enter_error appellent _stop_all)
          ANY    -> ERROR : rien (idem)
        """
        rte       = self._rte
        old_state = rte.state

        # Pas de nettoyage necessaire si le nouvel etat gere lui-meme tout
        if new_state in (ST_OFF, ST_ERROR, ST_DIAG):
            return

        # Transition d'un etat FRONT vers un etat REAR
        if old_state in self._STATES_USING_FRONT and new_state in self._STATES_USING_REAR:
            print(f"[WSM] EXIT-CLEAN: arret moteur AVANT ({old_state} -> {new_state})")
            self._front_motor_stop()
            if rte.pump_active and rte.pump_direction == 1:   # pompe FWD (avant)
                self._pump_stop("transition_front_to_rear")
            rte.set_multi(
                front_motor_on     = False,
                front_motor_speed  = 0,
                front_blade_moving = False,
            )

        # Transition d'un etat REAR vers un etat FRONT
        elif old_state in self._STATES_USING_REAR and new_state in self._STATES_USING_FRONT:
            print(f"[WSM] EXIT-CLEAN: arret moteur ARRIERE ({old_state} -> {new_state})")
            self._rear_motor_stop()
            if rte.pump_active and rte.pump_direction == 2:   # pompe BWD (arriere)
                self._pump_stop("transition_rear_to_front")
            rte.set_multi(
                rear_motor_on      = False,
                rear_motor_running = False,
            )

    def _enter_state(self, new_state: str):
        rte = self._rte
        if new_state == rte.state:
            return
        print(f"\n{'='*50}")
        print(f"[WSM] TRANSITION: {rte.state} --> {new_state}")
        print(f"{'='*50}")

        # --- Nettoyage des actionneurs du groupe quitte ---
        self._exit_current_state(new_state)

        rte.set_multi(prev_state=rte.state, state=new_state)
        self._log_sensors("TRANSITION")
        ENTRY = {
            ST_OFF:        self._enter_off,
            ST_TOUCH:      self._enter_touch,
            ST_SPEED1:     self._enter_speed1,
            ST_SPEED2:     self._enter_speed2,
            ST_AUTO:       self._enter_auto,
            ST_WASH_FRONT: self._enter_front_wash,
            ST_WASH_REAR:  self._enter_rear_wash,
            ST_REAR_WIPE:  self._enter_rear_wipe,
            ST_ERROR:      self._enter_error,
            ST_DIAG:       self._enter_diag,
        }
        fn = ENTRY.get(new_state)
        if fn:
            fn()
        self._tcp.send(rte)

    def _update_state_machine(self):
        self._watchdog_kick()
        rte = self._rte

        if rte.ignition_status == 0 and rte.state not in (ST_OFF,):
            # SRD_WW_001 : ignition=0 force ST_OFF depuis N'IMPORTE quel état
            # y compris ST_ERROR (ajout : auparavant ST_ERROR était exclu)
            print("[WSM] Ignition OFF -> arret force (SRD_WW_001)")
            if rte.state == ST_DIAG:
                rte.set("_test_active", False)
            self._enter_state(ST_OFF)
            return

        if rte.lin_timeout_active and rte.state not in (ST_OFF, ST_ERROR, ST_DIAG):
            self._enter_state(ST_OFF)
            return

        state = rte.state

        if state == ST_OFF:
            self._process_off_state(rte.crs_wiper_op)
        elif state == ST_TOUCH:
            self._process_touch()
        elif state == ST_SPEED1:
            self._process_speed1(rte.crs_wiper_op)
        elif state == ST_SPEED2:
            self._process_speed2(rte.crs_wiper_op)
        elif state == ST_AUTO:
            self._process_auto(rte.crs_wiper_op)
        elif state == ST_WASH_FRONT:
            self._process_front_wash()
        elif state == ST_WASH_REAR:
            self._process_rear_wash()
        elif state == ST_REAR_WIPE:
            self._process_rear_wipe()
        elif state == ST_DIAG:
            pass
        elif state == ST_ERROR:
            if rte.crs_wiper_op == WOP_OFF and not rte.lin_timeout_active:
                print("[MODE ERROR] Conditions effacees -> OFF")
                self._enter_state(ST_OFF)

        if state not in (ST_DIAG, ST_ERROR, ST_OFF):
            self._handle_reverse_intermittent()

    # ── Etat OFF ──────────────────────────────────────

    def _enter_off(self):
        print("[MODE OFF] Arret de tous les actionneurs")
        self._stop_all()
        self._rte.set_multi(
            front_motor_on            = False,
            front_motor_speed         = 0,
            front_blade_moving        = False,
            rear_motor_on             = False,
            rear_motor_running        = False,
            pump_dir_active           = 0,
            _rest_contact_stuck_start = 0.0,   # reset timer B2009
            _rest_contact_last_state  = -1,    # reset etat precedent B2009
            _rest_contact_prev        = None,  # Reset pour la prochaine detection de front
            _front_blade_cycles       = 0,     # reset compteur interne
            front_blade_cycles        = 0,     # reset compteur public Redis
            # ── Reset erreurs individuelles au retour OFF ──
            front_motor_error         = False,
            rear_motor_error          = False,
            pump_error                = False,
            t_motor_stop              = 0.0,
        )

    def _process_off_state(self, op: int):
        rte = self._rte
        if op == WOP_OFF:
            if not rte._freeze_pending:
                rte._one_shot_armed = True
            return

        if rte._freeze_pending and op != rte._freeze_last_op:
            rte._freeze_pending = False
            rte._one_shot_armed = True

        if rte._freeze_pending:
            return

        if op == WOP_TOUCH:
            if rte._one_shot_armed:
                rte._one_shot_armed = False
                self._enter_state(ST_TOUCH)
        elif op == WOP_SPEED1:
            self._enter_state(ST_SPEED1)
        elif op == WOP_SPEED2:
            self._enter_state(ST_SPEED2)
        elif op == WOP_AUTO:
            if rte.rain_sensor_installed:
                rte._auto_ignored_logged = False
                self._enter_state(ST_AUTO)
            else:
                if not rte._auto_ignored_logged:
                    print("[WSM] AUTO ignore: RainSensorInstalled=False")
                    rte._auto_ignored_logged = True
        elif op == WOP_FRONT_WASH:
            if rte._one_shot_armed:
                rte._one_shot_armed = False
                self._enter_state(ST_WASH_FRONT)
        elif op == WOP_REAR_WASH:
            if rte._one_shot_armed and rte.rear_wiper_available:
                rte._one_shot_armed = False
                self._enter_state(ST_WASH_REAR)
            elif rte._one_shot_armed and not rte.rear_wiper_available:
                if not rte._rear_ignored_logged:
                    print("[WSM] REAR_WASH ignore: RearWiperAvailable=False")
                    rte._rear_ignored_logged = True
        elif op == WOP_REAR_WIPE:
            # SRD_WW_092 : REAR_WIPE est une commande directe (pas one-shot)
            # Le declenchement est autorise a chaque fois que le levier est en position REAR_WIPE
            if rte.rear_wiper_available:
                self._enter_state(ST_REAR_WIPE)
            elif not rte._rear_ignored_logged:
                print("[WSM] REAR_WIPE ignore: RearWiperAvailable=False")
                rte._rear_ignored_logged = True

    # ── Etat DIAG ─────────────────────────────────────

    def _enter_diag(self):
        print("[MODE DIAG] DoIP prend le controle -- WSM suspendu")

    # ── Etat TOUCH ────────────────────────────────────

    def _enter_touch(self):
        print(f"[MODE TOUCH] 1 cycle <= {TOUCH_DURATION*1000:.0f}ms (SRD_WW_020)")
        rte = self._rte
        rte._front_blade_cycles = 0
        rte._rest_contact_prev  = self._read_rest_contact()
        self._rte.set_multi(
            t_touch_start             = time.time(),
            front_motor_on            = True,
            front_motor_speed         = 1,
            front_blade_moving        = True,
            t_motor_stop              = 0.0,
            # FIX BUG 3 : reset timers B2009 au demarrage de chaque mode moteur
            _rest_contact_stuck_start = 0.0,
            _rest_contact_last_state  = -1,
        )
        self._front_motor_run(1)

    def _process_touch(self):
        rte = self._rte
        elapsed = time.time() - rte.t_touch_start
        # GPIO=0 (bouton relache) -> False -> lame AU REPOS -> fin de cycle normale
        # GPIO=1 (bouton appuye)  -> True  -> lame EN MOUVEMENT -> cycle en cours
        blade_moving  = self._read_rest_contact()
        rest_detected = not blade_moving   # True = lame revenue au repos (bouton relache)
        if elapsed >= TOUCH_DURATION or rest_detected:
            reason = "contact_repos" if rest_detected else f"{elapsed*1000:.0f}ms"
            print(f"[MODE TOUCH] Cycle termine ({reason}) -> OFF")

            # ── B2006 : fin par timeout = repos jamais detecte ───────────────
            # Si le cycle se termine par epuisement du temps (pas par contact repos),
            # cela signifie que la lame n'est pas revenue en position repos dans
            # les 1700ms impartis. On declenche B2006 immediatement sans attendre
            # le delai de 2s de _check_blade_position.
            if not rest_detected and not rte._rest_contact_b2006_active:
                print(f"[FSR_006] TOUCH: contact repos jamais detecte apres "
                      f"{elapsed*1000:.0f}ms -> B2006")
                self._dtc.set_active("B2006", rte.make_snapshot())
                rte.set("_rest_contact_b2006_active", True)
                self._rte.set_multi(
                    front_motor_on     = False,
                    front_motor_speed  = 0,
                    front_blade_moving = False,
                )
                self._front_motor_stop()
                self._enter_state(ST_ERROR)
                return
            # ── Fin normale : contact repos detecte ─────────────────────────

            rte.crs_wiper_op    = WOP_OFF
            rte._one_shot_armed = False
            rte._freeze_pending = True
            rte._freeze_last_op = WOP_TOUCH
            self._rte.set_multi(
                front_motor_on     = False,
                front_motor_speed  = 0,
                front_blade_moving = False,
            )
            self._front_motor_stop()
            self._enter_state(ST_OFF)

    # ── Etat SPEED1 ───────────────────────────────────

    def _enter_speed1(self):
        print("[MODE SPEED1] Relais Speed1 (SRD_WW_030)")
        rte = self._rte
        rte._front_blade_cycles = 0
        rte._rest_contact_prev  = self._read_rest_contact()
        self._rte.set_multi(
            front_motor_on            = True,
            front_motor_speed         = 1,
            front_blade_moving        = True,
            t_motor_stop              = 0.0,
            # FIX BUG 3 : reset timers B2009 au demarrage de chaque mode moteur
            _rest_contact_stuck_start = 0.0,
            _rest_contact_last_state  = -1,
        )
        self._front_motor_run(1)

    def _process_speed1(self, op: int):
        self._track_blade_cycle(count_on_rest=True)   # suivi cycles lame via contact repos
        rte = self._rte
        if op == WOP_OFF:
            self._enter_state(ST_OFF)
        elif op == WOP_TOUCH:
            self._enter_state(ST_TOUCH)
        elif op == WOP_SPEED2:
            self._enter_state(ST_SPEED2)
        elif op == WOP_AUTO and rte.rain_sensor_installed:
            self._enter_state(ST_AUTO)
        elif op == WOP_FRONT_WASH:
            self._enter_state(ST_WASH_FRONT)
        elif op == WOP_REAR_WASH and rte.rear_wiper_available:
            self._enter_state(ST_WASH_REAR)
        elif op == WOP_REAR_WIPE and rte.rear_wiper_available:
            self._enter_state(ST_REAR_WIPE)

    # ── Etat SPEED2 ───────────────────────────────────

    def _enter_speed2(self):
        print("[MODE SPEED2] Relais Speed2 (SRD_WW_040)")
        rte = self._rte
        rte._front_blade_cycles = 0
        rte._rest_contact_prev  = self._read_rest_contact()
        self._rte.set_multi(
            front_motor_on            = True,
            front_motor_speed         = 2,
            front_blade_moving        = True,
            t_motor_stop              = 0.0,
            # FIX BUG 3 : reset timers B2009 au demarrage de chaque mode moteur
            _rest_contact_stuck_start = 0.0,
            _rest_contact_last_state  = -1,
        )
        self._front_motor_run(2)

    def _process_speed2(self, op: int):
        self._track_blade_cycle(count_on_rest=True)   # suivi cycles lame via contact repos
        rte = self._rte
        if op == WOP_OFF:
            self._enter_state(ST_OFF)
        elif op == WOP_TOUCH:
            self._enter_state(ST_TOUCH)
        elif op == WOP_SPEED1:
            self._enter_state(ST_SPEED1)
        elif op == WOP_AUTO and rte.rain_sensor_installed:
            self._enter_state(ST_AUTO)
        elif op == WOP_FRONT_WASH:
            self._enter_state(ST_WASH_FRONT)
        elif op == WOP_REAR_WASH and rte.rear_wiper_available:
            self._enter_state(ST_WASH_REAR)
        elif op == WOP_REAR_WIPE and rte.rear_wiper_available:
            self._enter_state(ST_REAR_WIPE)

    # ── Etat AUTO ─────────────────────────────────────

    def _enter_auto(self):
        print("[MODE AUTO] Pluie automatique (SRD_WW_050)")
        rte = self._rte
        rte._front_blade_cycles = 0
        rte._rest_contact_prev  = self._read_rest_contact()
        self._rte.set_multi(
            _auto_speed_prev   = -1,
            front_motor_on     = False,
            front_motor_speed  = 0,
            front_blade_moving = False,
            _rest_contact_stuck_start = 0.0,
            _rest_contact_last_state  = -1,
        )

    def _process_auto(self, op: int):
        rte = self._rte
        if not rte.rain_sensor_installed:
            self._enter_state(ST_OFF)
            return
        if op == WOP_OFF:
            self._enter_state(ST_OFF);        return
        elif op == WOP_TOUCH:
            self._enter_state(ST_TOUCH);      return
        elif op == WOP_SPEED1:
            self._enter_state(ST_SPEED1);     return
        elif op == WOP_SPEED2:
            self._enter_state(ST_SPEED2);     return
        elif op == WOP_FRONT_WASH:
            self._enter_state(ST_WASH_FRONT); return
        elif op == WOP_REAR_WASH and rte.rear_wiper_available:
            self._enter_state(ST_WASH_REAR);  return

        rain    = rte.rain_intensity
        new_spd = 2 if rain >= RAIN_SPEED2_THRESH else (1 if rain > 0 else 0)
        if new_spd != rte._auto_speed_prev:
            rte.set("_auto_speed_prev", new_spd)
            if new_spd == 2:
                self._rte.set_multi(front_motor_on=True, front_motor_speed=2, front_blade_moving=True)
                self._front_motor_run(2)
                print(f"[MODE AUTO] Speed2 (pluie={rain}>={RAIN_SPEED2_THRESH})")
            elif new_spd == 1:
                self._rte.set_multi(front_motor_on=True, front_motor_speed=1, front_blade_moving=True)
                self._front_motor_run(1)
                print(f"[MODE AUTO] Speed1 (pluie={rain}>0)")
            else:
                self._rte.set_multi(front_motor_on=False, front_motor_speed=0, front_blade_moving=False)
                self._front_motor_stop()
                print("[MODE AUTO] Moteur STOP (pluie=0)")
            self._tcp.send(rte)   # notifier la Platform du changement vitesse/etat moteur

        # Suivi cycles lame via contact repos (uniquement si moteur avant actif)
        if rte.front_motor_on:
            self._track_blade_cycle(count_on_rest=True)

    # ── Etat WASH_FRONT ───────────────────────────────

    def _enter_front_wash(self):
        print(f"[MODE FRONT WASH] Pompe FWD + {WASH_FRONT_CYCLES} cycles")
        rte = self._rte
        rte._front_blade_cycles = 0
        rte._rest_contact_prev  = self._read_rest_contact()
        self._pump_start(1)
        rte.set_multi(
            wash_cycles_done          = 0,
            t_wash_cycle_start        = time.time(),
            front_motor_on            = True,
            front_motor_speed         = 1,
            front_blade_moving        = True,
            pump_dir_active           = 1,
            t_motor_stop              = 0.0,
            _rest_contact_stuck_start = 0.0,
            _rest_contact_last_state  = -1,
        )
        self._front_motor_run(1)

    def _process_front_wash(self):
        rte     = self._rte
        elapsed = time.time() - rte.t_wash_cycle_start

        if rte.pump_active and elapsed >= PUMP_MAX_RUNTIME:
            self._pump_stop("front_wash_fsr005")
            rte.set("pump_dir_active", 0)

        # Comptage de cycles : contact repos si hardware present OU simulation active,
        # sinon fallback par temps (banc sans GPIO ni simulation)
        if REST_CONTACT_HARDWARE_PRESENT or rte.rest_contact_sim_active:
            self._track_blade_cycle(count_on_rest=True)
            cycles = rte._front_blade_cycles
        else:
            cycles = int(elapsed / WIPE_CYCLE_DURATION)

        if cycles > rte.wash_cycles_done:
            rte.set("wash_cycles_done", cycles)
            print(f"[MODE FRONT WASH] Cycle {cycles}/{WASH_FRONT_CYCLES}")
            self._log_sensors(f"Cycle {cycles}/{WASH_FRONT_CYCLES}")

        if rte.wash_cycles_done >= WASH_FRONT_CYCLES:
            print(f"[MODE FRONT WASH] {WASH_FRONT_CYCLES} cycles -> OFF")
            if rte.pump_active:
                self._pump_stop("front_wash_complet")
            self._rte.set_multi(
                front_motor_on     = False,
                front_motor_speed  = 0,
                front_blade_moving = False,
                pump_dir_active    = 0,
            )
            self._front_motor_stop()
            rte.crs_wiper_op    = WOP_OFF
            rte._one_shot_armed = False
            rte._freeze_pending = True
            rte._freeze_last_op = WOP_FRONT_WASH
            self._enter_state(ST_OFF)

    # ── Etat WASH_REAR ────────────────────────────────

    def _enter_rear_wash(self):
        print(f"[MODE REAR WASH] Pompe BWD + {WASH_REAR_CYCLES} cycles")
        rte = self._rte
        self._pump_start(2)
        rte.set_multi(
            wash_cycles_done   = 0,
            t_wash_cycle_start = time.time(),
            pump_dir_active    = 2,
        )
        if rte.rear_wiper_available:
            rte.set("rear_motor_running", True)
            rte.set("rear_motor_on",      True)
            self._rear_motor_run()

    def _process_rear_wash(self):
        rte = self._rte
        if not rte.rear_wiper_available:
            self._pump_stop("coding_f202")
            self._rear_motor_stop()
            rte.set_multi(rear_motor_on=False, rear_motor_running=False)
            rte.set("_one_shot_armed", True)
            self._enter_state(ST_OFF)
            return
        elapsed = time.time() - rte.t_wash_cycle_start
        cycles  = int(elapsed / WIPE_CYCLE_DURATION)

        if rte.pump_active and elapsed >= PUMP_MAX_RUNTIME:
            self._pump_stop("rear_wash_fsr005")
            rte.set("pump_dir_active", 0)

        if cycles > rte.wash_cycles_done:
            rte.set("wash_cycles_done", cycles)
            print(f"[MODE REAR WASH] Cycle {cycles}/{WASH_REAR_CYCLES}")
            self._log_sensors(f"Cycle {cycles}/{WASH_REAR_CYCLES}")

        if rte.wash_cycles_done >= WASH_REAR_CYCLES:
            print(f"[MODE REAR WASH] {WASH_REAR_CYCLES} cycles -> OFF")
            if rte.pump_active:
                self._pump_stop("rear_wash_complet")
            self._rear_motor_stop()
            rte.set_multi(
                rear_motor_on      = False,
                rear_motor_running = False,
                pump_dir_active    = 0,
            )
            rte.crs_wiper_op    = WOP_OFF
            rte._one_shot_armed = False
            rte._freeze_pending = True
            rte._freeze_last_op = WOP_REAR_WASH
            self._enter_state(ST_OFF)

    # ── Etat REAR_WIPE ────────────────────────────────

    def _enter_rear_wipe(self):
        print(f"[MODE REAR WIPE] Moteur arriere ON -- cycles de {TOUCH_DURATION*1000:.0f}ms (SRD_WW_092)")
        rte = self._rte
        rte.set("t_touch_start", time.time())
        if rte.rear_wiper_available:
            rte.set_multi(rear_motor_running=True, rear_motor_on=True)
            self._rear_motor_run()   # demarrage unique -- moteur reste ON

    def _process_rear_wipe(self):
        rte = self._rte
        if not rte.rear_wiper_available:
            self._rear_motor_stop()
            rte.set_multi(rear_motor_on=False, rear_motor_running=False)
            # SRD_WW_091 : demande ignoree si RearWiperAvailable=False
            self._enter_state(ST_OFF)
            return

        # Surveiller le levier : si relache → arreter et passer OFF
        if rte.crs_wiper_op != WOP_REAR_WIPE:
            self._rear_motor_stop()
            rte.set_multi(rear_motor_on=False, rear_motor_running=False)
            self._enter_state(ST_OFF)
            return

        # Compter les cycles (pour log) sans arreter le moteur
        elapsed = time.time() - rte.t_touch_start
        if elapsed >= TOUCH_DURATION:
            # Nouveau cycle : juste reset du timer, moteur reste ON
            rte.set("t_touch_start", time.time())
            print(f"[MODE REAR WIPE] Cycle suivant (levier maintenu)")

    # ── Etat ERROR ────────────────────────────────────

    def _enter_error(self):
        print("[MODE ERROR] Erreur -> arret tous actionneurs")
        self._stop_all()
        self._rte.set_multi(
            front_motor_on            = False,
            front_motor_speed         = 0,
            front_blade_moving        = False,
            rear_motor_on             = False,
            rear_motor_running        = False,
            pump_dir_active           = 0,
            _rest_contact_stuck_start = 0.0,   # reset timer B2009
            _rest_contact_last_state  = -1,    # reset etat precedent B2009
            _front_blade_cycles       = 0,     # reset compteur interne
            front_blade_cycles        = 0,     # reset compteur public Redis
        )

    # ── Marche arriere ────────────────────────────────

    # Etats consideres comme "Front wiping active" (SRD_WW_060)
    _STATES_FRONT_ACTIVE = {ST_SPEED1, ST_SPEED2, ST_AUTO, ST_TOUCH}

    def _handle_reverse_intermittent(self):
        """
        SRD_WW_060 : If ReverseGear=TRUE and Front wiping active
                     → Rear wiper shall perform one cycle every 1700ms.

        Implementation :
          - "Front wiping active" = state in {ST_SPEED1, ST_SPEED2, ST_AUTO, ST_TOUCH}
          - Moteur arriere demarre immediatement (ON continu, comme ST_REAR_WIPE)
          - Timer 1700ms : reset a chaque cycle, moteur ne s'arrete PAS entre cycles
          - reverse_gear → False : arret moteur immediat
          - Front wiper quitte un etat actif : arret moteur immediat
        """
        rte = self._rte

        # ── Cas 1 : marche arriere desactivee ──────────────────────────
        if not rte.reverse_gear:
            if rte._reverse_active:
                self._rear_motor_stop()
                rte.set_multi(
                    rear_motor_on       = False,
                    rear_motor_running  = False,
                    _reverse_active     = False,
                    _reverse_cycle_num  = 0,
                    t_rear_last         = 0.0,
                )
                print("[SRD_WW_060] Moteur arriere OFF (ReverseGear=False)")
                self._tcp.send(rte)
            return

        # ── Cas 2 : front wiper n'est plus actif (levier relache) ──────
        if rte.state not in self._STATES_FRONT_ACTIVE:
            if rte._reverse_active:
                self._rear_motor_stop()
                rte.set_multi(
                    rear_motor_on       = False,
                    rear_motor_running  = False,
                    _reverse_active     = False,
                    _reverse_cycle_num  = 0,
                    t_rear_last         = 0.0,
                )
                print("[SRD_WW_060] Moteur arriere OFF (front wiper inactif)")
                self._tcp.send(rte)
            return

        # ── Cas 3 : RearWiperAvailable requis ──────────────────────────
        if not rte.rear_wiper_available:
            return

        # ── Cas 4 : premiere detection → demarrage moteur arriere ──────
        if not rte._reverse_active:
            rte._reverse_active    = True
            rte._reverse_cycle_num = 1
            rte.set("t_rear_last", time.time())
            self._rear_motor_run()
            rte.set_multi(
                rear_motor_on      = True,
                rear_motor_running = True,
            )
            print(f"[SRD_WW_060] Moteur arriere ON (front={rte.state} | reverse=True)")
            self._tcp.send(rte)
            return

        # ── Cas 5 : moteur deja ON → cycle 1700ms, moteur reste ON ─────
        now = time.time()
        if now - rte.t_rear_last >= REVERSE_REAR_PERIOD:
            rte.set("t_rear_last", now)
            rte._reverse_cycle_num += 1
            rte.set_multi(
                rear_motor_on      = True,
                rear_motor_running = True,
            )


    # ==================================================
    # SECTION E -- SURVEILLANCE (PUMP GUARD + ADS1115)
    # ==================================================

    def _check_blade_position(self):
        # ---------------------------------------------------------------
        # Logique bouton (pull-down GPIO26) :
        #   Bouton RELACHE -> GPIO=0 -> _read_rest_contact()=False -> lame AU REPOS
        #   Bouton APPUYE  -> GPIO=1 -> _read_rest_contact()=True  -> lame EN MOUVEMENT
        #
        # B2006 : moteur arrete depuis > 2s ET lame toujours EN MOUVEMENT
        #         (bouton encore appuye = GPIO=1 = True)
        #
        # GARDE ANTI-BOUCLE : _rest_contact_b2006_active empeche le re-declenchement cyclique.
        # Sequence sans garde : B2006 -> ST_ERROR -> _stop_all() -> t_motor_stop=now ->
        #   ST_OFF -> 2s -> B2006 -> boucle infinie.
        # Le flag est remis a False uniquement par Clear DTC (0x14) ou ECU Reset (0x11).
        # ---------------------------------------------------------------
        rte = self._rte
        if not REST_CONTACT_HARDWARE_PRESENT:
            return
        if rte.state in (ST_ERROR, ST_DIAG):
            return
        if rte._rest_contact_b2006_active:            # deja actif -> attendre Clear/Reset UDS
            return
        if rte.t_motor_stop == 0.0:
            return
        elapsed = time.time() - rte.t_motor_stop
        if elapsed < 2.0:
            return

        # GPIO=True  (bouton appuye)  -> lame EN MOUVEMENT -> anormal apres arret moteur
        # GPIO=False (bouton relache) -> lame AU REPOS     -> normal
        blade_moving = self._read_rest_contact()   # True = bouton appuye = lame bouge
        if blade_moving:
            src = "ST_OFF" if rte.state == ST_OFF else rte.state
            print(f"[FSR_006] Lame en mouvement depuis {elapsed:.1f}s apres arret moteur ({src}) -> B2006")
            self._dtc.set_active("B2006", rte.make_snapshot())
            rte.set("_rest_contact_b2006_active", True)
            self._enter_state(ST_ERROR)

    def _check_rest_contact_stuck(self):
        # ---------------------------------------------------------------
        # Logique bouton (pull-down GPIO26) :
        #   Bouton RELACHE -> GPIO=0 -> _read_rest_contact()=False -> lame AU REPOS
        #   Bouton APPUYE  -> GPIO=1 -> _read_rest_contact()=True  -> lame EN MOUVEMENT
        #
        # B2009 STUCK CLOSED : moteur AVANT EN MARCHE depuis >REST_STUCK_DELAY ET
        #                       aucun mouvement jamais detecte (aucun front False->True).
        #
        #   IMPORTANT : le bouton relache (False) est NORMAL entre deux balayages
        #   car la lame revient en position repos a chaque cycle (~1.7s). Declarer
        #   B2009 des que bouton=relache+moteur=ON serait une fausse alarme a chaque
        #   cycle normal. On detecte uniquement l'absence TOTALE de mouvement depuis
        #   le demarrage du mode moteur.
        #
        #   Les etats REAR (WASH_REAR, REAR_WIPE) sont exclus : le contact repos
        #   est un capteur du moteur AVANT uniquement.
        #
        # B2009 STUCK OPEN   : moteur A L'ARRET + bouton APPUYE (GPIO=1=True)
        #                       depuis >REST_STUCK_DELAY apres arret moteur.
        #                       Contact physiquement bloque ferme.
        # ---------------------------------------------------------------
        rte = self._rte
        if not REST_CONTACT_HARDWARE_PRESENT:
            return
        if rte._rest_contact_b2009_active:
            return
        if rte.state in (ST_DIAG, ST_ERROR):
            return

        blade_moving = self._read_rest_contact()   # True=bouton appuye=lame bouge / False=relache=repos
        now          = time.time()

        # Contact repos surveille UNIQUEMENT pour le moteur AVANT
        # WASH_REAR et REAR_WIPE utilisent le moteur arriere -> pas de contact repos
        front_motor_running = rte.state in (ST_SPEED1, ST_SPEED2, ST_AUTO, ST_TOUCH,    ST_WASH_FRONT) \
                      and rte.front_motor_on
        motor_running_any = front_motor_running or rte.state in (ST_WASH_REAR, ST_REAR_WIPE)

        # ── CAS 1 : STUCK CLOSED ─────────────────────────────────────────────
        # Moteur AVANT EN MARCHE ET aucun mouvement detecte depuis demarrage.
        # Un front montant (False->True) prouve que le contact fonctionne -> reset timer.
        if front_motor_running:
            # Front montant detecte : lame vient de commencer un mouvement
            if blade_moving and rte._rest_contact_prev is False:
                # Contact OK -> reset timer stuck, plus de risque B2009
                rte._rest_contact_stuck_start = 0.0
                rte._rest_contact_last_state  = -1

            # Mise a jour etat precedent pour detection du prochain front
            rte._rest_contact_prev = blade_moving

            # Demarrage timer si pas encore fait
            if rte._rest_contact_stuck_start == 0.0:
                rte._rest_contact_stuck_start = now
                rte._rest_contact_last_state  = 0
            elif rte._rest_contact_last_state == 0:
                if (now - rte._rest_contact_stuck_start) >= REST_STUCK_DELAY:
                    print("[B2009] Contact repos STUCK CLOSED "
                          "(moteur avant marche + aucun mouvement depuis >3s) -> ERROR")
                    self._dtc.set_active("B2009", rte.make_snapshot())
                    rte.set_multi(
                        _rest_contact_b2009_active=True,
                        _rest_contact_stuck_start=0.0
                    )
                    self._enter_state(ST_ERROR)
                    return

        # ── CAS 2 : STUCK OPEN ───────────────────────────────────────────────
        # Moteur A L'ARRET + bouton APPUYE depuis >REST_STUCK_DELAY.
        elif not motor_running_any and blade_moving and rte.t_motor_stop > 0.0:
            if rte._rest_contact_stuck_start == 0.0:
                rte._rest_contact_stuck_start = now
                rte._rest_contact_last_state  = 1
            elif rte._rest_contact_last_state == 1:
                if (now - rte._rest_contact_stuck_start) >= REST_STUCK_DELAY:
                    print("[B2009] Contact repos STUCK OPEN "
                          "(moteur arrete + bouton appuye depuis >3s) -> ERROR")
                    self._dtc.set_active("B2009", rte.make_snapshot())
                    rte.set_multi(
                        _rest_contact_b2009_active=True,
                        _rest_contact_stuck_start=0.0
                    )
                    self._enter_state(ST_ERROR)
                    return

        # ── CAS 3 : etat coherent ou moteur arriere -> reset timers ──────────
        else:
            if not front_motor_running:
                rte._rest_contact_stuck_start = 0.0
                rte._rest_contact_last_state  = -1

    def _check_pump_overcurrent(self):
        rte = self._rte
        if not rte.pump_active:
            rte.set("_pump_overcurrent_start", 0.0)
            return
        current = rte.pump_current_a   # courant pompe ACS712 reel
        now     = time.time()
        if current > PUMP_OVERCURRENT_THRESH:
            if rte._pump_overcurrent_start == 0.0:
                rte.set("_pump_overcurrent_start", now)
            elif (now - rte._pump_overcurrent_start) > PUMP_OVERCURRENT_DELAY:
                rte.set("_pump_overcurrent_start", 0.0)
                print(f"[B2003] Surintensite pompe {current:.2f}A > {PUMP_OVERCURRENT_THRESH}A -> arret pompe (moteur NON affecte)")
                snap = rte.make_snapshot()
                self._dtc.set_active("B2003", snap)
                self._pump_stop("overcurrent_b2003")
                # ── Erreur isolee pompe uniquement ──
                rte.set("pump_error", True)
                self._tcp_pump.send(rte)
        else:
            rte.set("_pump_overcurrent_start", 0.0)

    def _check_pump_protection(self):
        rte = self._rte
        if not rte.pump_active:
            return
        elapsed = time.time() - rte.t_pump_start
        if rte.state in (ST_WASH_FRONT, ST_WASH_REAR):
            if elapsed >= PUMP_MAX_RUNTIME - 0.1:
                self._pump_stop("wash_normal_4.9s")
        else:
            if elapsed > PUMP_MAX_RUNTIME:
                print(f"[POMPE GUARD] {PUMP_MAX_RUNTIME}s depasse -> arret + B2008")
                self._pump_stop("max_runtime_5s")
                self._dtc.set_active("B2008", rte.make_snapshot())

    def _check_overcurrent(self):
        rte     = self._rte
        current = rte.motor_current_a
        now     = time.time()

        if rte.front_motor_on:
            motor_id  = "front"
            dtc_code  = "B2001"
            error_key = "front_motor_error"
        elif rte.rear_motor_on:
            motor_id  = "rear"
            dtc_code  = "B2002"
            error_key = "rear_motor_error"
        else:
            rte.t_overcurrent_start.clear()
            return

        if current > OVERCURRENT_THRESH:
            if motor_id not in rte.t_overcurrent_start:
                rte.t_overcurrent_start[motor_id] = now
                print(f"[SECURITE] Surintensite {motor_id} {current:.2f}A detectee...")
            elif now - rte.t_overcurrent_start[motor_id] > OVERCURRENT_DELAY:
                del rte.t_overcurrent_start[motor_id]
                print(f"[SECURITE] {dtc_code} Surintensite {motor_id} "
                      f"{current:.2f}A > {OVERCURRENT_THRESH}A pendant "
                      f"{OVERCURRENT_DELAY*1000:.0f}ms -> arret moteur {motor_id} (pompe NON affectee)")
                snap = rte.make_snapshot()
                self._dtc.set_active(dtc_code, snap)
                # ── Arreter UNIQUEMENT le moteur concerne, pas la pompe ──
                if motor_id == "front":
                    self._front_motor_stop()
                    rte.set_multi(
                        front_motor_on    = False,
                        front_motor_speed = 0,
                        front_blade_moving= False,
                        front_motor_error = True,
                    )
                else:
                    self._rear_motor_stop()
                    rte.set_multi(
                        rear_motor_on     = False,
                        rear_motor_running= False,
                        rear_motor_error  = True,
                    )
                self._enter_state(ST_ERROR)
                # La pompe continue de fonctionner independamment
        else:
            rte.t_overcurrent_start.pop(motor_id, None)

    def _watchdog_kick(self):
        self._rte._watchdog_kick_time = time.time()

    def _watchdog_check(self):
        elapsed_ms = (time.time() - self._rte._watchdog_kick_time) * 1000
        if elapsed_ms > WATCHDOG_MAX_MS * 10:
            print(f"[WATCHDOG] Timeout {elapsed_ms:.0f}ms -> reset (TSR_005)")
            self._rte.set("_watchdog_kick_time", time.time())

    def _check_wc_timeout(self):
        """
        FSR_002 / TSR_002 : supervision CAN WC.
        Si wc_available et pas de trame 0x201 depuis CAN_WC_TIMEOUT -> B2005.
        Timeout modifie 100ms -> 2000ms (demande utilisateur).
        Supervision suspendue pendant ST_WASH_REAR et ST_REAR_WIPE :
        ces etats n'impliquent pas le moteur avant, le WC ne repond pas.
        """
        from bcm_rte import CAN_WC_TIMEOUT, ST_WASH_REAR, ST_REAR_WIPE
        rte = self._rte
        if not rte.wc_available:
            # Cas A : pas de WC, pas de supervision CAN
            if rte.wc_timeout_active:
                rte.set("wc_timeout_active", False)
            return
        # Suspension pendant etats arriere (WC non sollicite)
        if rte.state in (ST_WASH_REAR, ST_REAR_WIPE):
            if rte.wc_timeout_active:
                rte.set("wc_timeout_active", False)
            rte.set("t_last_wiper_status", time.time())  # reset le timer
            return
        if rte.t_last_wiper_status == 0.0:
            return  # WC pas encore vu

        elapsed = time.time() - rte.t_last_wiper_status
        if elapsed > CAN_WC_TIMEOUT and not rte.wc_timeout_active:
            print(f"[B2005] CAN Timeout WC: pas de 0x201 depuis "
                  f"{elapsed:.1f}s > {CAN_WC_TIMEOUT}s -> B2005 + OFF")
            rte.set("wc_timeout_active", True)
            self._dtc.set_active("B2005", rte.make_snapshot())
            # Etat securise : OFF (FSR_002)
            if rte.state not in (ST_OFF, ST_ERROR, ST_DIAG):
                self._enter_state(ST_OFF)
        elif elapsed <= CAN_WC_TIMEOUT and rte.wc_timeout_active:
            rte.set("wc_timeout_active", False)
            self._dtc.set_inactive("B2005")

    # ==================================================
    # SECTION F -- TRAITEMENT UDS
    # ==================================================

    def _process_uds_request(self):
        from dtc_manager import handle_read_dtc, handle_clear_dtc
        rte = self._rte
        uds = rte.uds_payload
        sid = rte.uds_sid

        handlers = {
            SID_DSC:   self._handle_dsc,
            SID_RESET: self._handle_reset,
            SID_CLEAR: self._handle_clear,
            SID_RDTC:  lambda u: handle_read_dtc(self._dtc, u),
            SID_RDID:  self._handle_rdid,
            SID_WDID:  self._handle_wdid,
            SID_SA:    self._handle_sa,
            SID_CC:    self._handle_cc,
            SID_RC:    self._handle_rc,
            SID_TP:    self._handle_tp,
        }
        handler  = handlers.get(sid)
        response = handler(uds) if handler else self._nrc(sid, 0x11)

        rte.set_multi(
            uds_response       = response if response is not None else b"",
            uds_response_ready = True,
            uds_request_pending= False,
        )

    # ==================================================
    # SECTION G -- HANDLERS UDS
    # ==================================================

    def _nrc(self, sid: int, code: int) -> bytes:
        return bytes([0x7F, sid, code])

    def _handle_dsc(self, uds: bytes) -> bytes:
        if len(uds) < 2:
            return self._nrc(SID_DSC, 0x13)
        sub      = uds[1] & 0x7F
        suppress = bool(uds[1] & 0x80)
        if sub not in (DSC_DEFAULT, DSC_EXTENDED):
            return self._nrc(SID_DSC, 0x12)
        rte = self._rte
        rte.set("_session", sub)
        if sub == DSC_DEFAULT:
            rte.set("_sec_level", 0)
        rte.set("_pending_seed", {})
        if suppress:
            return b""
        return bytes([0x50, sub, 0x00, 0x32, 0x07, 0xD0])

    def _handle_clear(self, uds: bytes) -> bytes:
        """
        UDS 0x14 -- ClearDiagnosticInformation
        """
        from dtc_manager import handle_clear_dtc
        rte = self._rte

        # Clear DTC standard
        response = handle_clear_dtc(self._dtc, uds)
        print("[CLEAR DTC] DTC effaces")

        # Reset garde anti-boucle B2006 : autoriser re-detection apres reparation mecanique
        rte.set_multi(
           _rest_contact_b2006_active = False,
           _rest_contact_b2009_active = False,  # ← FIX AJOUTÉ
           _rest_contact_stuck_start  = 0.0,    # ← optionnel mais recommandé
           _rest_contact_last_state   = -1,     # ← idem
           # ── Reset erreurs individuelles apres clear DTC ──
           front_motor_error          = False,
           rear_motor_error           = False,
           pump_error                 = False,
       )

        # Verification courant apres clear
        # Note : depuis la correction isolation erreurs, l'overcurrent moteur
        # n'entraine plus ST_ERROR global. Le moteur concerne est simplement
        # arrete et son flag (front_motor_error / rear_motor_error) est remis
        # a False ci-dessus. Aucune reprise d'etat necessaire ici.
        rte.t_overcurrent_start.clear()

        return response

    def _handle_reset(self, uds: bytes) -> bytes:
        sub = uds[1] if len(uds) > 1 else 0x01
        rte = self._rte
        rte.set_multi(
            _session=1, _sec_level=0, _pending_seed={},
            _rest_contact_b2009_active = False,
            _rest_contact_b2006_active = False,   # reset garde anti-boucle B2006
            _rest_contact_stuck_start  = 0.0,
            _rest_contact_last_state   = -1,
            _rest_contact_prev         = None,    # reset detection front montant
            t_motor_stop               = 0.0,     # FIX: evite B2006 residuel apres ECU Reset
            _test_active=False,
        )
        self._enter_state(ST_OFF)
        return bytes([0x51, sub])

    def _compute_key(self, seed: int) -> int:
        return ((seed ^ SA_XOR_MASK) + SA_ADD_MASK) & 0xFFFF

    def _handle_sa(self, uds: bytes) -> bytes:
        if len(uds) < 2:
            return self._nrc(SID_SA, 0x13)
        sub = uds[1]
        rte = self._rte
        if rte._session != DSC_EXTENDED:
            return self._nrc(SID_SA, 0x7E)
        if sub == SA_REQ_SEED:
            if rte._sec_level >= 1:
                return bytes([0x67, sub, 0x00, 0x00])
            seed = int.from_bytes(os.urandom(2), "big") or 0x1234
            rte._pending_seed[1] = seed
            return bytes([0x67, sub, seed >> 8, seed & 0xFF])
        elif sub == SA_SEND_KEY:
            if 1 not in rte._pending_seed:
                return self._nrc(SID_SA, 0x24)
            if len(uds) < 4:
                return self._nrc(SID_SA, 0x13)
            received = (uds[2] << 8) | uds[3]
            expected = self._compute_key(rte._pending_seed.pop(1))
            if received != expected:
                return self._nrc(SID_SA, 0x35)
            rte.set("_sec_level", 1)
            return bytes([0x67, sub])
        return self._nrc(SID_SA, 0x12)

    def _handle_cc(self, uds: bytes) -> bytes:
        if len(uds) < 3:
            return self._nrc(SID_CC, 0x13)
        sub       = uds[1]
        comm_type = uds[2]
        rte       = self._rte
        if sub == 0x00:
            rte.set_multi(_comm_tx_enabled=True,  _comm_rx_enabled=True)
        elif sub == 0x01:
            rte.set("_comm_tx_enabled", False)
        elif sub == 0x02:
            rte.set("_comm_rx_enabled", False)
        elif sub == 0x03:
            rte.set_multi(_comm_tx_enabled=False, _comm_rx_enabled=False)
        else:
            return self._nrc(SID_CC, 0x12)
        return bytes([0x68, sub, comm_type])

    def _handle_rdid(self, uds: bytes) -> bytes:
        if len(uds) < 3:
            return self._nrc(SID_RDID, 0x13)
        did = (uds[1] << 8) | uds[2]
        rte = self._rte

        if did == 0xF100:
            return bytes([0x62, 0xF1, 0x00, ST_ENC.get(rte.state, 0)])
        elif did == 0xF101:
            return bytes([0x62, 0xF1, 0x01, rte.front_motor_speed & 0xFF])
        elif did == 0xF102:
            return bytes([0x62, 0xF1, 0x02, 1 if rte.front_blade_moving else 0])
        elif did == 0xF103:
            curr_ma = int(rte.motor_current_a * 1000)
            return bytes([0x62, 0xF1, 0x03, (curr_ma >> 8) & 0xFF, curr_ma & 0xFF])
        elif did == 0xF104:
            return bytes([0x62, 0xF1, 0x04, rte.pump_dir_active & 0xFF])
        elif did == 0xF105:
            return bytes([0x62, 0xF1, 0x05, rte.rain_intensity & 0xFF])
        elif did == 0xF106:
            return bytes([0x62, 0xF1, 0x06, 1 if rte.rear_motor_running else 0])
        elif did == 0xF107:
            err = 0
            if rte.lin_timeout_active:            err |= 0x01
            if rte._rest_contact_b2009_active:    err |= 0x02
            if rte.state == ST_ERROR:             err |= 0x04
            if rte.state == ST_DIAG:              err |= 0x08
            return bytes([0x62, 0xF1, 0x07, err])

        return self._nrc(SID_RDID, 0x31)

    def _handle_wdid(self, uds: bytes) -> bytes:
        rte = self._rte
        if rte._session != DSC_EXTENDED:
            return self._nrc(SID_WDID, 0x22)
        if rte._sec_level < 1:
            return self._nrc(SID_WDID, 0x33)
        if len(uds) < 4:
            return self._nrc(SID_WDID, 0x13)
        did = (uds[1] << 8) | uds[2]
        val = uds[3]

        if did == 0xF200:
            rte.set("rain_sensor_installed", bool(val))
            if not bool(val) and rte.state == ST_AUTO:
                self._enter_state(ST_OFF)
        elif did == 0xF201:
            # Cas A -> Cas B (ou inverse)
            old_val = rte.wc_available
            new_val = bool(val)
            rte.set("wc_available", new_val)
            if new_val and not old_val:
                print("[CODING] F201 WcAvailable=Installed -> Cas B actif "
                      "(BCM envoie CAN 0x200, WC commande moteur avant)")
                # BCM arrête ses propres relais moteur avant -- WC prend le relais
                self._front_motor_stop()
                self._rte.set_multi(front_motor_on=False, front_motor_speed=0,
                                    front_blade_moving=False)
            elif not new_val and old_val:
                print("[CODING] F201 WcAvailable=NotInstalled -> Cas A actif "
                      "(BCM commande directement moteur avant)")
                rte.set("wc_timeout_active", False)
        elif did == 0xF202:
            rte.set("rear_wiper_available", bool(val))
            if not bool(val) and rte.state in (ST_WASH_REAR, ST_REAR_WIPE):
                self._pump_stop("coding_f202")
                self._rear_motor_stop()
                rte.set_multi(rear_motor_on=False, _one_shot_armed=True)
                self._enter_state(ST_OFF)
        elif did == 0xF203:
            if val != 0:
                return self._nrc(SID_WDID, 0x31)
            rte.set("channel_front_wash", val)
        elif did == 0xF204:
            if val != 1:
                return self._nrc(SID_WDID, 0x31)
            rte.set("channel_rear_camera", val)
        else:
            return self._nrc(SID_WDID, 0x31)

        return bytes([0x6E, uds[1], uds[2]])

    def _handle_rc(self, uds: bytes) -> bytes:
        """RoutineControl BCM -- tests actionneurs.
        Accessible depuis toute session, sans SecurityAccess requis.
        """
        rte = self._rte
        if len(uds) < 4:
            return self._nrc(SID_RC, 0x13)

        sub      = uds[1]
        rid      = struct.unpack(">H", uds[2:4])[0]
        duration = min(uds[4] if len(uds) >= 5 else 10, 60)

        if sub == 0x01:
            if rid == 0x0201:
                if rte.state in (ST_SPEED1, ST_SPEED2, ST_AUTO, ST_TOUCH, ST_WASH_FRONT):
                    return self._nrc(SID_RC, 0x22)
                if rte.state == ST_DIAG:
                    return self._nrc(SID_RC, 0x22)
                rte.set_multi(
                    _test_active=True, _test_routine=rid,
                    _test_duration=duration, _t_test_start=time.time(),
                    front_motor_on=True, front_motor_speed=1,
                    front_blade_moving=True, rear_motor_on=False,
                    rear_motor_running=False, pump_dir_active=0,
                )
                self._front_motor_run(1)
                self._enter_state(ST_DIAG)
                return bytes([0x71, sub, 0x02, 0x01, duration & 0xFF])

            elif rid == 0x0202:
                if not rte.rear_wiper_available:
                    return self._nrc(SID_RC, 0x22)
                if rte.state in (ST_REAR_WIPE, ST_WASH_REAR, ST_DIAG):
                    return self._nrc(SID_RC, 0x22)
                rte.set_multi(
                    _test_active=True, _test_routine=rid,
                    _test_duration=duration, _t_test_start=time.time(),
                    rear_motor_on=True, rear_motor_running=True,
                    front_motor_on=False, front_motor_speed=0,
                    front_blade_moving=False, pump_dir_active=0,
                )
                self._rear_motor_run()
                self._enter_state(ST_DIAG)
                return bytes([0x71, sub, 0x02, 0x02, duration & 0xFF])

            elif rid == 0x0203:
                if rte.pump_active or rte.state == ST_DIAG:
                    return self._nrc(SID_RC, 0x22)
                rte.set_multi(
                    _test_active=True, _test_routine=rid,
                    _test_duration=min(duration, PUMP_MAX_RUNTIME),
                    _t_test_start=time.time(), pump_dir_active=1,
                    front_motor_on=False, front_motor_speed=0,
                    front_blade_moving=False, rear_motor_on=False,
                    rear_motor_running=False,
                )
                self._pump_start(1)
                self._enter_state(ST_DIAG)
                return bytes([0x71, sub, 0x02, 0x03, duration & 0xFF])

            elif rid == 0x0204:
                if rte.pump_active or rte.state == ST_DIAG:
                    return self._nrc(SID_RC, 0x22)
                rte.set_multi(
                    _test_active=True, _test_routine=rid,
                    _test_duration=min(duration, PUMP_MAX_RUNTIME),
                    _t_test_start=time.time(), pump_dir_active=2,
                    front_motor_on=False, front_motor_speed=0,
                    front_blade_moving=False, rear_motor_on=False,
                    rear_motor_running=False,
                )
                self._pump_start(2)
                self._enter_state(ST_DIAG)
                return bytes([0x71, sub, 0x02, 0x04, duration & 0xFF])

            elif rid == 0x0205:
                rain_val = uds[4] if len(uds) >= 5 else 0
                if rain_val > 100:
                    return self._nrc(SID_RC, 0x31)
                rte.set("rain_intensity", rain_val)
                return bytes([0x71, sub, 0x02, 0x05, rain_val & 0xFF])

        elif sub == 0x02:
            if rte._test_active:
                self._stop_test()
            return bytes([0x71, sub]) + uds[2:4]

        elif sub == 0x03:
            if rte._test_active:
                elapsed = int(time.time() - rte._t_test_start)
                return bytes([0x71, sub]) + uds[2:4] + bytes([elapsed & 0xFF])
            return bytes([0x71, sub]) + uds[2:4] + bytes([0x00])

        return self._nrc(SID_RC, 0x31)

    def _handle_tp(self, uds: bytes) -> bytes:
        sub = uds[1] if len(uds) > 1 else 0x00
        if sub & 0x80:
            return b""
        return bytes([0x7E, 0x00])

    def _stop_test(self):
        rte = self._rte
        print(f"[TEST] Routine 0x{rte._test_routine:04X} terminee")
        rte.set_multi(
            _test_active       = False,
            front_motor_on     = False,
            front_motor_speed  = 0,
            front_blade_moving = False,
            rear_motor_on      = False,
            rear_motor_running = False,
            pump_dir_active    = 0,
        )
        self._front_motor_stop()
        self._rear_motor_stop()
        self._pump_stop("test_complet")
        self._enter_state(ST_OFF)

    # ==================================================
    # SECTION H -- THREADS
    # ==================================================

    def thread_wsm_control(self):
        """Thread T-WSM -- Machine d'etat wiper (200ms)."""
        print(f"[THREAD T-WSM] Demarre | periode={CONTROL_LOOP_PERIOD*1000:.0f}ms")
        while self._running:
            self._watchdog_kick()
            self._update_state_machine()
            time.sleep(CONTROL_LOOP_PERIOD)

    def thread_pump_guard(self):
        """
        Thread T-PUMP -- Surveillance pompe + courant ADS1115 + securites (200ms).
        Lit le courant ADS1115 toutes les ADS_READ_PERIOD (100ms).
        Detecte aussi les changements de pump_fault_mode depuis Redis et
        applique les GPIO correspondants (ISO, MUX, Y0_GATE, Y2_BASE).
        """
        print(f"[THREAD T-PUMP] Demarre | periode={PUMP_GUARD_PERIOD*1000:.0f}ms")
        t_last_ads    = 0.0
        _current_prev = -1.0
        _CURRENT_STEP = 0.050
        _last_fault_mode   = PUMP_FAULT_NORMAL
        _last_fault_target = "POMPE"

        while self._running:
            # Lecture périodique rest_contact → met à jour rest_contact_raw dans Redis
            # même quand le moteur est à l'arrêt (nécessaire pour WindshieldWidget Platform)
            self._read_rest_contact()

            now = time.time()
            rte = self._rte

            # ── Detection changement mode defaut (depuis Redis) ──────────────
            cur_mode   = rte.pump_fault_mode
            cur_target = rte.pump_fault_target

            if cur_target != _last_fault_target:
                _last_fault_target = cur_target
                _fault_gpio_set_cible(cur_target, rte)
                print(f"[DEFAUT] Cible changee -> {cur_target}")

            if cur_mode != _last_fault_mode:
                _last_fault_mode = cur_mode
                print(f"[DEFAUT] Mode change -> {cur_mode}  cible={cur_target}")
                if cur_mode == PUMP_FAULT_NORMAL:
                    _fault_gpio_retour_normal()
                elif cur_mode == PUMP_FAULT_OPEN_LOAD:
                    _fault_gpio_open_load(cur_target)
                    canal = _ads_pump_channel if cur_target == "POMPE" else _ads_channel
                    if canal is not None:
                        _calibrer_offset_open_load(canal)
                elif cur_mode == PUMP_FAULT_SIG_VAR:
                    _fault_gpio_signal_variable(cur_target)
                elif cur_mode == PUMP_FAULT_SHORT_VCC:
                    _fault_gpio_short_vcc(cur_target)
                self._tcp_pump.send(rte)

            # Lecture ADS1115 moteur wiper toutes les 100ms
            if now - t_last_ads >= ADS_READ_PERIOD:
                current    = self._read_motor_current()
                t_last_ads = now

                stepped = round(round(current / _CURRENT_STEP) * _CURRENT_STEP, 2)

                if stepped != _current_prev:
                    _current_prev = stepped
                    self._rte.set("motor_current_a", stepped)
                    print(f"[ADS1115] Courant moteur : {stepped:.2f}A ({stepped*1000:.0f}mA)")
                    self._tcp.send(self._rte)
                    self._tcp_pump.send(self._rte)
                    self._rte.set("motor_current_a", current)

            # Lecture courant pompe ACS712 si pompe active
            if self._rte.pump_active:
                p_curr, p_volt = self._read_pump_current()
                stepped_p = round(round(p_curr / 0.050) * 0.050, 2)
                if not hasattr(self, '_pump_curr_prev') or stepped_p != self._pump_curr_prev:
                    self._pump_curr_prev = stepped_p
                    self._tcp_pump.send(self._rte)
            else:
                if self._rte.pump_current_a != 0.0 or self._rte.pump_voltage_v != 0.0:
                    self._rte.set_multi(
                        pump_current_a = 0.0,
                        pump_voltage_v = 0.0,
                        pump_v_b       = 0.0,
                        pump_v_a       = 0.0,
                    )

            self._check_pump_protection()
            self._check_overcurrent()
            self._check_blade_position()
            self._check_rest_contact_stuck()
            self._check_pump_overcurrent()
            self._check_wc_timeout()
            time.sleep(PUMP_GUARD_PERIOD)

    def thread_diagnostic(self):
        """Thread T-DIAG -- Traitement UDS + test actionneur + watchdog."""
        print("[THREAD T-DIAG] Demarre | mode Event (zero polling)")
        rte = self._rte

        while self._running:
            signaled = rte._uds_event.wait(timeout=ACTUATOR_TEST_PERIOD)

            if not self._running:
                break

            if signaled and rte.uds_request_pending:
                rte._uds_event.clear()
                self._process_uds_request()

            if rte._test_active:
                elapsed = time.time() - rte._t_test_start
                if elapsed >= rte._test_duration:
                    self._stop_test()

            self._watchdog_check()

    # ==================================================
    # DEMARRAGE / ARRET
    # ==================================================

    def start(self):
        self._running = True
        self._tcp.start()
        self._tcp_pump.start()

    def stop(self):
        self._running = False
        self._tcp.stop()
        self._tcp_pump.stop()
        self._stop_all()
        _gpio_cleanup()
        print("[ApplicationLayer] Arretee proprement")