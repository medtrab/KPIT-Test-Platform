#!/usr/bin/env python3
"""
bcm_application.py
==================
Couche Application -- Machine d'etat + Fonctions Wiper + Diagnostic UDS
WipeWash System -- Architecture 3 Couches

ADS1115 chip (I2C)
  ├── Canal A3 ── Potentiomètre ── _read_ads_current() ── rte.motor_current_a
  └── Canal A0 ── ACS712 ───────── _read_ads_pump()    ── rte.pump_current_a
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
    PIN_REST_CONTACT,
    RELAY_ON, RELAY_OFF, RELAY_SPEED1, RELAY_SPEED2,
    REST_CONTACT_HARDWARE_PRESENT,
    CONTROL_LOOP_PERIOD, PUMP_GUARD_PERIOD,
    ACTUATOR_TEST_PERIOD,
    SA_REQ_SEED, SA_SEND_KEY, SA_XOR_MASK, SA_ADD_MASK,
    ADS_VOLTAGE_MIN, ADS_VOLTAGE_MAX,
    ADS_CURRENT_MIN, ADS_CURRENT_MAX,
    ADS_GAIN, ADS_CHANNEL, ADS_READ_PERIOD,
    ADS_PUMP_CHANNEL, ADS_PUMP_VREF, ADS_PUMP_SENSITIVITY,
    ADS_PUMP_NOISE, ADS_PUMP_R_CHARGE, ADS_PUMP_V_MAX, ADS_PUMP_NB_SAMPLES,
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

# VREF calibree dynamiquement au demarrage
_pump_vref_calibrated = ADS_PUMP_VREF   # valeur par defaut avant calibration

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
    print(f"[ADS1115] Pompe ACS712 | canal=A{ADS_PUMP_CHANNEL}")
except Exception as e:
    print(f"[ADS1115] ERREUR INIT : {type(e).__name__}: {e}")
    print("[ADS1115] Courant simule a 0A")
    _ads_pump_channel = None


# =====================================================
# CALIBRATION AUTOMATIQUE ACS712 (pompe OFF obligatoire)
# =====================================================
def _calibrate_pump_sensor():
    """
    Mesure la vraie tension de repos de l'ACS712 quand la pompe est OFF.
    Met a jour _pump_vref_calibrated utilise par _read_ads_pump().
    Appele une seule fois au demarrage dans ApplicationLayer.__init__().
    """
    global _pump_vref_calibrated

    if not ADS_AVAILABLE or _ads_pump_channel is None:
        print("[CALIB] ADS1115 non disponible -> calibration ignoree")
        return

    print("[CALIB] Calibration ACS712 pompe (pompe doit etre OFF)...")

    samples = []
    for _ in range(100):
        try:
            samples.append(_ads_pump_channel.voltage)
        except Exception as e:
            print(f"[CALIB] Erreur lecture : {e}")
        time.sleep(0.02)

    if not samples:
        print("[CALIB] Echec : aucun echantillon recu")
        return

    # Mediane robuste au bruit
    s   = sorted(samples)
    n   = len(s)
    mid = (s[n//2 - 1] + s[n//2]) / 2 if n % 2 == 0 else s[n//2]

    # Bruit standard deviation
    mean = sum(samples) / len(samples)
    std  = (sum((v - mean) ** 2 for v in samples) / len(samples)) ** 0.5

    vref_raw = mid   # tension brute mediane

    # Correction offset identique a RPi1 : Vref = Vbrut + 0.59 * Sensitivity
    old_vref              = _pump_vref_calibrated
    _pump_vref_calibrated = round(vref_raw + 0.59 * ADS_PUMP_SENSITIVITY, 4)

    print(f"[CALIB] Vref ancienne   = {old_vref:.4f} V")
    print(f"[CALIB] Vref brute      = {round(vref_raw, 4):.4f} V")
    print(f"[CALIB] Vref corrigee   = {_pump_vref_calibrated:.4f} V  (brut + 0.59 x Sensitivity)")
    print(f"[CALIB] Bruit std_dev   = {round(std * 1000, 1)} mV")
    print(f"[CALIB] Sensitivity     = {ADS_PUMP_SENSITIVITY} V/A")
    print(f"[CALIB] R_charge        = {ADS_PUMP_R_CHARGE} ohm")
    print(f"[CALIB] I_max theorique = {round(ADS_PUMP_V_MAX / ADS_PUMP_R_CHARGE, 3)} A")
    print(f"[CALIB] NOISE_THRESHOLD = {ADS_PUMP_NOISE} A")

    if std > 0.050:
        print(f"[CALIB] ATTENTION bruit {round(std*1000,1)}mV -> verifier GND commun")
    else:
        print("[CALIB] Bruit OK -> calibration valide")


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
# LECTURE COURANT + TENSION POMPE (ADS1115 A0 / ACS712)
# =====================================================

def _safe_read_pump_voltage() -> float | None:
    """
    Lecture robuste de la tension ACS712 avec 3 tentatives.
    En cas d'echec total, tente de reinitialiser le canal ADS1115.
    Retourne None si toutes les tentatives echouent.
    """
    global _ads_pump_channel
    for attempt in range(3):
        try:
            return _ads_pump_channel.voltage
        except Exception as e:
            if attempt == 2:
                print(f"[ADS-PUMP] Erreur lecture (tentative {attempt+1}) : {e} -> reinit canal")
                try:
                    _ads_pump_channel = AnalogIn(_ads, ADS_PUMP_CHANNEL)
                except Exception as reinit_e:
                    print(f"[ADS-PUMP] Reinit echouee : {reinit_e}")
            else:
                time.sleep(0.02)
    return None


def _read_ads_pump() -> tuple:
    """
    Lit le courant et la tension de la pompe via ACS712 canal A0.
    Methode identique a RPi1 :
      - ADS_PUMP_NB_SAMPLES echantillons avec 5ms entre chaque
      - Mediane pour filtrer le bruit
      - Formule : I = abs(Vout - Vref) / Sensitivity
      - Clamp a I_max = V_max / R_charge
      - Seuil bruit : si I < NOISE -> 0A
      - Tension : U = I x R_charge (loi d'Ohm, limitee a V_max)
    Utilise _pump_vref_calibrated (mis a jour par _calibrate_pump_sensor).
    Retourne (current_A, voltage_V).
    """
    if not ADS_AVAILABLE or _ads_pump_channel is None:
        return 0.0, 0.0

    # --- Collecte des echantillons avec delai 5ms  ---
    samples = []
    for _ in range(ADS_PUMP_NB_SAMPLES):           # 10 échantillons
        v = _safe_read_pump_voltage()
        if v is not None:
            samples.append(v)
        time.sleep(0.005)   # 5ms entre chaque echantillon

    if not samples:
        return 0.0, 0.0

    # --- Mediane robuste au bruit impulsionnel ---
    s    = sorted(samples)
    n    = len(s)
    vout = (s[n//2 - 1] + s[n//2]) / 2 if n % 2 == 0 else s[n//2]

    # --- Courant ACS712 : I = (Vout - Vref) / Sensitivity ---
    current = (vout - _pump_vref_calibrated) / ADS_PUMP_SENSITIVITY
    current = round(abs(current), 3)

    # --- Clamp courant max : I_max = V_max / R_charge ---
    i_max   = round(ADS_PUMP_V_MAX / ADS_PUMP_R_CHARGE, 3)
    current = min(current, i_max)

    # --- Seuil bruit : en dessous -> 0A ---
    if current < ADS_PUMP_NOISE:
        current = 0.0

    # --- Tension via loi d'Ohm, limitee a V_max ---
    voltage = min(round(current * ADS_PUMP_R_CHARGE, 2), ADS_PUMP_V_MAX)

    return current, voltage


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
    GPIO.setup(PIN_REST_CONTACT, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    print("[GPIO] Broches initialisees")
    print(f"  PIN_RELAY_FRONT_ON    = GPIO{PIN_RELAY_FRONT_ON}  (RL2 ON/OFF avant)")
    print(f"  PIN_RELAY_FRONT_SPEED = GPIO{PIN_RELAY_FRONT_SPEED}  (RL1 Speed1/Speed2)")
    print(f"  PIN_RELAY_REAR_ON     = GPIO{PIN_RELAY_REAR_ON}  (RL3 ON/OFF arriere)")
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

        # Calibration automatique ACS712 au demarrage (pompe OFF)
        _calibrate_pump_sensor()

    # ==================================================
    # SECTION B -- PRIMITIVES MOTEUR / POMPE (RELAIS)
    # ==================================================

    def _front_motor_run(self, speed_level: int):
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
        return False

    def _read_motor_current(self) -> float:
        current = _read_ads_current()
        self._rte.set("motor_current_a", current)
        return current

    def _read_pump_current(self) -> tuple:
        current, voltage = _read_ads_pump()
        self._rte.set_multi(
            pump_current_a = current,
            pump_voltage_v = voltage,
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
        Lit les valeurs ADS1115 directement pour avoir les vraies mesures.
        """
        rte    = self._rte
        prefix = f"[{tag}] " if tag else ""

        # Courant moteur wiper (A3) -- toujours lire en direct
        m_curr = _read_ads_current()
        self._rte.set("motor_current_a", m_curr)

        # Courant/tension pompe (A0 ACS712) -- lire en direct si active
        if rte.pump_active:
            p_curr, p_volt = self._read_pump_current()
        else:
            # Pompe arretee : remettre a zero proprement
            p_curr = 0.0
            p_volt = 0.0
            self._rte.set_multi(pump_current_a=0.0, pump_voltage_v=0.0)

        print(f"{prefix}"
              f"[MOTEUR] I={m_curr:.3f}A  "
              f"[POMPE]  I={p_curr:.3f}A  U={p_volt:.2f}V")

    # ==================================================
    # SECTION C -- MACHINE D'ETAT (WSM)
    # ==================================================

    def _enter_state(self, new_state: str):
        rte = self._rte
        if new_state == rte.state:
            return
        print(f"\n{'='*50}")
        print(f"[WSM] TRANSITION: {rte.state} --> {new_state}")
        print(f"{'='*50}")
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

        if rte.ignition_status == 0 and rte.state not in (ST_OFF, ST_ERROR):
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
            front_motor_on     = False,
            front_motor_speed  = 0,
            front_blade_moving = False,
            rear_motor_on      = False,
            rear_motor_running = False,
            pump_dir_active    = 0,
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
            if rte._one_shot_armed and rte.rear_wiper_available:
                rte._one_shot_armed = False
                self._enter_state(ST_REAR_WIPE)

    # ── Etat DIAG ─────────────────────────────────────

    def _enter_diag(self):
        print("[MODE DIAG] DoIP prend le controle -- WSM suspendu")

    # ── Etat TOUCH ────────────────────────────────────

    def _enter_touch(self):
        print(f"[MODE TOUCH] 1 cycle <= {TOUCH_DURATION*1000:.0f}ms (SRD_WW_020)")
        self._rte.set_multi(
            t_touch_start      = time.time(),
            front_motor_on     = True,
            front_motor_speed  = 1,
            front_blade_moving = True,
        )
        self._front_motor_run(1)

    def _process_touch(self):
        rte = self._rte
        elapsed       = time.time() - rte.t_touch_start
        rest_detected = self._read_rest_contact()
        if elapsed >= TOUCH_DURATION or rest_detected:
            reason = "contact_repos" if rest_detected else f"{elapsed*1000:.0f}ms"
            print(f"[MODE TOUCH] Cycle termine ({reason}) -> OFF")
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
        self._rte.set_multi(
            front_motor_on     = True,
            front_motor_speed  = 1,
            front_blade_moving = True,
        )
        self._front_motor_run(1)

    def _process_speed1(self, op: int):
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
        self._rte.set_multi(
            front_motor_on     = True,
            front_motor_speed  = 2,
            front_blade_moving = True,
        )
        self._front_motor_run(2)

    def _process_speed2(self, op: int):
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
        self._rte.set_multi(
            _auto_speed_prev   = -1,
            front_motor_on     = False,
            front_motor_speed  = 0,
            front_blade_moving = False,
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

    # ── Etat WASH_FRONT ───────────────────────────────

    def _enter_front_wash(self):
        print(f"[MODE FRONT WASH] Pompe FWD + {WASH_FRONT_CYCLES} cycles")
        rte = self._rte
        self._pump_start(1)
        rte.set_multi(
            wash_cycles_done   = 0,
            t_wash_cycle_start = time.time(),
            front_motor_on     = True,
            front_motor_speed  = 1,
            front_blade_moving = True,
            pump_dir_active    = 1,
        )
        self._front_motor_run(1)

    def _process_front_wash(self):
        rte     = self._rte
        elapsed = time.time() - rte.t_wash_cycle_start
        cycles  = int(elapsed / WIPE_CYCLE_DURATION)

        if rte.pump_active and elapsed >= PUMP_MAX_RUNTIME:
            self._pump_stop("front_wash_fsr005")
            rte.set("pump_dir_active", 0)

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
        print(f"[MODE REAR WIPE] 1 cycle <= {TOUCH_DURATION*1000:.0f}ms")
        rte = self._rte
        rte.set("t_touch_start", time.time())
        if rte.rear_wiper_available:
            rte.set_multi(rear_motor_running=True, rear_motor_on=True)
            self._rear_motor_run()

    def _process_rear_wipe(self):
        rte = self._rte
        if not rte.rear_wiper_available:
            self._rear_motor_stop()
            rte.set_multi(rear_motor_on=False, rear_motor_running=False)
            rte.set("_one_shot_armed", True)
            self._enter_state(ST_OFF)
            return
        elapsed = time.time() - rte.t_touch_start
        if elapsed >= TOUCH_DURATION:
            self._rear_motor_stop()
            rte.set_multi(rear_motor_on=False, rear_motor_running=False)
            rte.crs_wiper_op    = WOP_OFF
            rte._one_shot_armed = False
            rte._freeze_pending = True
            rte._freeze_last_op = WOP_REAR_WIPE
            self._enter_state(ST_OFF)

    # ── Etat ERROR ────────────────────────────────────

    def _enter_error(self):
        print("[MODE ERROR] Erreur -> arret tous actionneurs")
        self._stop_all()
        self._rte.set_multi(
            front_motor_on     = False,
            front_motor_speed  = 0,
            front_blade_moving = False,
            rear_motor_on      = False,
            rear_motor_running = False,
            pump_dir_active    = 0,
        )

    # ── Marche arriere ────────────────────────────────

    def _handle_reverse_intermittent(self):
        rte = self._rte
        if not rte.reverse_gear:
            rte._reverse_active = False
            return
        if rte.state not in (ST_SPEED1, ST_SPEED2, ST_AUTO):
            return
        if not rte.rear_wiper_available:
            return
        if not rte._reverse_active:
            print("[SRD_WW_060] Marche arriere -> cycle arriere intermittent")
            rte._reverse_active = True
        now = time.time()
        if now - rte.t_rear_last >= REVERSE_REAR_PERIOD:
            rte.set("t_rear_last", now)
            if not hasattr(self, '_reverse_thread') or not self._reverse_thread.is_alive():
                self._reverse_thread = threading.Thread(
                    target=self._rear_wipe_one_cycle,
                    daemon=True, name="REVERSE_REAR"
                )
                self._reverse_thread.start()

    def _rear_wipe_one_cycle(self):
        rte = self._rte
        rte.set_multi(rear_motor_running=True, rear_motor_on=True)
        self._rear_motor_run()
        time.sleep(TOUCH_DURATION)
        self._rear_motor_stop()
        rte.set_multi(rear_motor_running=False, rear_motor_on=False)

    # ==================================================
    # SECTION E -- SURVEILLANCE (PUMP GUARD + ADS1115)
    # ==================================================

    def _check_blade_position(self):
        rte = self._rte
        if not REST_CONTACT_HARDWARE_PRESENT:
            return
        if rte.state in (ST_OFF, ST_ERROR, ST_DIAG):
            return
        if rte.t_motor_stop == 0.0:
            return
        elapsed = time.time() - rte.t_motor_stop
        if elapsed < 2.0:
            return
        at_rest = self._read_rest_contact()
        if not at_rest:
            print(f"[FSR_006] Lame implausible depuis {elapsed:.1f}s -> B2006")
            self._dtc.set_active("B2006", rte.make_snapshot())
            self._enter_state(ST_ERROR)

    def _check_rest_contact_stuck(self):
        rte = self._rte
        if not REST_CONTACT_HARDWARE_PRESENT:
            return
        if rte._rest_contact_b2009_active:
            return
        if rte.state in (ST_DIAG, ST_ERROR):
            return

        contact_closed = not self._read_rest_contact()
        now            = time.time()
        motor_running  = rte.state in (
            ST_SPEED1, ST_SPEED2, ST_AUTO,
            ST_TOUCH, ST_WASH_FRONT, ST_WASH_REAR, ST_REAR_WIPE
        )

        if motor_running and contact_closed:
            if rte._rest_contact_stuck_start == 0.0:
                rte._rest_contact_stuck_start = now
                rte._rest_contact_last_state  = 0
            elif rte._rest_contact_last_state == 0:
                if (now - rte._rest_contact_stuck_start) >= REST_STUCK_DELAY:
                    print("[B2009] Contact repos STUCK CLOSED -> ERROR")
                    self._dtc.set_active("B2009", rte.make_snapshot())
                    rte.set_multi(
                        _rest_contact_b2009_active=True,
                        _rest_contact_stuck_start=0.0
                    )
                    self._enter_state(ST_ERROR)
                    return
        elif not motor_running and contact_closed and rte.t_motor_stop > 0.0:
            motor_stopped_since = now - rte.t_motor_stop
            if motor_stopped_since >= REST_STUCK_DELAY:
                if rte._rest_contact_stuck_start == 0.0:
                    rte._rest_contact_stuck_start = now
                    rte._rest_contact_last_state  = 1
                elif rte._rest_contact_last_state == 1:
                    if (now - rte._rest_contact_stuck_start) >= REST_STUCK_DELAY:
                        print("[B2009] Contact repos STUCK OPEN -> ERROR")
                        self._dtc.set_active("B2009", rte.make_snapshot())
                        rte.set_multi(
                            _rest_contact_b2009_active=True,
                            _rest_contact_stuck_start=0.0
                        )
                        self._enter_state(ST_ERROR)
                        return
        else:
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
                print(f"[B2003] Surintensite pompe {current:.2f}A > {PUMP_OVERCURRENT_THRESH}A -> arret")
                snap = rte.make_snapshot()
                self._dtc.set_active("B2003", snap)
                self._pump_stop("overcurrent_b2003")
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
            motor_id = "front"
            dtc_code = "B2001"
        elif rte.rear_motor_on:
            motor_id = "rear"
            dtc_code = "B2002"
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
                      f"{OVERCURRENT_DELAY*1000:.0f}ms -> ERROR")
                snap = rte.make_snapshot()
                self._dtc.set_active(dtc_code, snap)
                self._enter_state(ST_ERROR)
        else:
            rte.t_overcurrent_start.pop(motor_id, None)

    def _watchdog_kick(self):
        self._rte._watchdog_kick_time = time.time()

    def _watchdog_check(self):
        elapsed_ms = (time.time() - self._rte._watchdog_kick_time) * 1000
        if elapsed_ms > WATCHDOG_MAX_MS * 10:
            print(f"[WATCHDOG] Timeout {elapsed_ms:.0f}ms -> reset (TSR_005)")
            self._rte.set("_watchdog_kick_time", time.time())

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

        # Verification courant apres clear
        current = rte.motor_current_a
        if rte.state == ST_ERROR:
            if current < OVERCURRENT_THRESH:
                print(f"[CLEAR DTC] Courant OK ({current:.2f}A < {OVERCURRENT_THRESH}A) "
                      f"-> reprise etat precedent ({rte.prev_state})")
                # Reprendre l'etat avant l'erreur (prev_state stocke par _enter_state)
                prev = rte.prev_state if rte.prev_state not in (ST_ERROR, ST_OFF) else ST_OFF
                self._enter_state(prev)
            else:
                print(f"[CLEAR DTC] Courant toujours eleve ({current:.2f}A >= {OVERCURRENT_THRESH}A) "
                      f"-> B2001 se redeclenche")
                # Le moteur reste en ST_ERROR, _check_overcurrent redetectera
                # On force juste la remise a zero du timer pour repartir proprement
                rte.t_overcurrent_start.clear()

        return response

    def _handle_reset(self, uds: bytes) -> bytes:
        sub = uds[1] if len(uds) > 1 else 0x01
        rte = self._rte
        rte.set_multi(
            _session=1, _sec_level=0, _pending_seed={},
            _rest_contact_b2009_active=False,
            _rest_contact_stuck_start=0.0,
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
            print("[CODING] F201 WcAvailable: ignore (Cas A)")
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
        rte = self._rte
        if rte._session != DSC_EXTENDED:
            return self._nrc(SID_RC, 0x22)
        if rte._sec_level < 1:
            return self._nrc(SID_RC, 0x33)
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
            self._update_state_machine()
            time.sleep(CONTROL_LOOP_PERIOD)

    def thread_pump_guard(self):
        """
        Thread T-PUMP -- Surveillance pompe + courant ADS1115 + securites (200ms).
        Lit le courant ADS1115 toutes les ADS_READ_PERIOD (100ms).
        """
        print(f"[THREAD T-PUMP] Demarre | periode={PUMP_GUARD_PERIOD*1000:.0f}ms")
        t_last_ads    = 0.0
        _current_prev = -1.0
        _CURRENT_STEP = 0.050

        while self._running:
            now = time.time()

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
                    self._rte.set_multi(pump_current_a=0.0, pump_voltage_v=0.0)

            self._check_pump_protection()
            self._check_overcurrent()
            self._check_blade_position()
            self._check_rest_contact_stuck()
            self._check_pump_overcurrent()
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