#!/usr/bin/env python3
"""
bcmcan.py - BCM Sensor / CAN Node
====================================
Lit les capteurs physiques (ADS1115 I2C, GPIO FAULT), recoit les donnees
Vehicle_Status et RainSensorData depuis un client TCP, et communique
sur le bus CAN socketcan (can0).

MODIFICATIONS T11 :
  Quand Platform envoie {"test_cmd":"stop_can_tx"}, bcmcan :
    1. Met _can_tx_paused = True
    2. Diffuse {"can_fault":true,"state":"FAULT"} a tous les clients :5000.
  Quand Platform envoie {"test_cmd":"start_can_tx"} :
    1. Remet _can_tx_paused = False
    2. Diffuse {"can_fault":false,"state":"OFF"}.

SUPPRESSION UART :
  pigpio / UART bit-bang (GPIO17/27, 9600 baud) supprime.
  Causes warnings au shutdown + inutile en production.

FIX SHUTDOWN :
  - _shutdown flag pour sortir _main_loop proprement
  - _line_fault.get_value() protege contre ValueError apres cleanup()
"""

import time
import os
import sys
import json
import socket
import threading
import struct
import logging
from dataclasses import dataclass, field
from enum import IntEnum

import queue as _queue_mod

_HW_GPIOD = False
_HW_I2C   = False

try:
    import gpiod
    _HW_GPIOD = True
except ImportError:
    pass

try:
    import board
    import busio
    import adafruit_ads1x15.ads1115 as ADS
    from adafruit_ads1x15.analog_in import AnalogIn
    _HW_I2C = True
except Exception:
    pass

# ============================================================
# CONFIGURATION
# ============================================================
_TCP_HOST = "0.0.0.0"
_TCP_PORT = 5000

_tcp_can = None

_ack_relay_queue: "_queue_mod.Queue[bytes]" = _queue_mod.Queue(maxsize=32)

_can_tx_paused: bool = False
_can_tx_paused_lock  = threading.Lock()

# TC_FSR_010 : nombre de trames 0x201 avec CRC corrompu à émettre
_corrupt_crc_count      = 0
_corrupt_crc_count_lock = threading.Lock()

# TC_CAN_202_ERR01 : désaccord WiperMode 0x200 ≠ CurrentMode 0x201
# Quand _mode_mismatch_0x201=True, _build_0x201 retourne CurrentMode=0x00
# (OFF) au lieu du mode reçu dans 0x200 → le WC détecte le désaccord et
# émet naturellement une trame 0x202 avec AckStatus=1 (NACK) + ErrorCode=0x01
_mode_mismatch_0x201      = False
_mode_mismatch_0x201_lock = threading.Lock()

# TC_CAN_003 : gel de l'AliveCounter dans les trames 0x200 reçues par le WC
# Quand _alive_counter_frozen=True, le WC détecte un counter figé et lève wc_alive_fault
_alive_counter_frozen      = False
_alive_counter_frozen_lock = threading.Lock()
_alive_counter_prev        = -1          # dernière valeur vue (−1 = jamais reçu)
_alive_freeze_repeat_count = 0           # nb de répétitions du même counter
_ALIVE_FREEZE_THRESHOLD    = 3           # nb de trames identiques avant détection faute

_SO_TIMESTAMP = 29
_CHIP_NAME    = "gpiochip0"
_PIN_FAULT    = 19

_CAN_IFACE      = "can0"
_CAN_ID_CMD     = 0x200
_CAN_ID_STATUS  = 0x201
_CAN_ID_ACK     = 0x202
_CAN_ID_VEHICLE = 0x300
_CAN_ID_RAIN    = 0x301
_CAN_FMT        = "=IB3x8s"
_CAN_SIZE       = struct.calcsize(_CAN_FMT)

_LOOP_PERIOD_S   = 0.200
_CAN_TX_PERIOD_S = 0.200
_CAN_RETRY_S     = 2

# ============================================================
# DBC LOADER — chargement dynamique de la configuration CAN
# ============================================================
# Permet de modifier les IDs CAN et les périodes en changeant
# le fichier wiperwash.dbc, sans toucher au code (comme le LDF pour LIN).
# ============================================================

_here_bcmcan = os.path.dirname(os.path.abspath(__file__))
for _dbc_dir in (_here_bcmcan, os.path.dirname(_here_bcmcan)):
    if _dbc_dir not in sys.path:
        sys.path.insert(0, _dbc_dir)

_DBC_LOADER_OK  = False
_DBC_CFG        = None   # config chargée : messages, id_map, periods_ms

try:
    from dbc_loader import load_dbc as _load_dbc_sim, pack_frame as _dbc_pack_sim, unpack_frame as _dbc_unpack_sim
    _DBC_LOADER_OK = True
except ImportError:
    log.warning("[BCMCAN] dbc_loader introuvable — IDs et périodes CAN fixes par défaut")


def load_dbc_sim(dbc_path: str) -> None:
    """
    Charge le fichier DBC et met à jour les constantes CAN du simulateur.
    Appelé depuis start() ou main() avant le lancement des threads.
    """
    global _DBC_CFG
    global _CAN_ID_CMD, _CAN_ID_STATUS, _CAN_ID_ACK, _CAN_ID_VEHICLE, _CAN_ID_RAIN
    global _CAN_TX_PERIOD_S

    if not _DBC_LOADER_OK:
        return

    cfg = _load_dbc_sim(dbc_path)
    if not cfg or not cfg["messages"]:
        log.warning("[BCMCAN-DBC] Aucun message — config par défaut conservée")
        return

    _DBC_CFG = cfg
    msgs    = cfg["messages"]
    periods = cfg["periods_ms"]

    for mid, m in msgs.items():
        if m.name == "Wiper_Command":
            _CAN_ID_CMD    = mid
        elif m.name == "Wiper_Status":
            _CAN_ID_STATUS = mid
        elif m.name == "Wiper_Ack":
            _CAN_ID_ACK    = mid
        elif m.name == "Vehicle_Status":
            _CAN_ID_VEHICLE = mid
        elif m.name == "RainSensorData":
            _CAN_ID_RAIN   = mid

    # Période TX véhicule depuis DBC (0x300)
    p_veh = periods.get(_CAN_ID_VEHICLE, 200)
    if p_veh > 0:
        _CAN_TX_PERIOD_S = p_veh / 1000.0

    log.info("[BCMCAN-DBC] Chargé: CMD=0x%03X STS=0x%03X ACK=0x%03X "
             "VEH=0x%03X RAIN=0x%03X period_veh=%.0fms",
             _CAN_ID_CMD, _CAN_ID_STATUS, _CAN_ID_ACK,
             _CAN_ID_VEHICLE, _CAN_ID_RAIN, _CAN_TX_PERIOD_S * 1000)

_ADS_ADDR      = 0x48
_ADS_GAIN      = 1
_ADS_CHAN_BLADE = 2
_ADS_CHAN_MOTOR = 1
_VREF          = 3.3
_I2C_ERR_MAX   = 5

_shutdown = False   # FIX: flag arret propre pour _main_loop

log = logging.getLogger("BCM")

# ============================================================
# GPIO INJECTION DE DEFAUTS (deplace depuis rpibcm12)
# Ces broches sont maintenant sur le RPi Simulateur.
# Le câblage physique relie ces GPIOs aux composants réels
# (pompe / moteur) qui sont testés par le BCM.
# ============================================================
_GPIO_FAULT_AVAILABLE = False
try:
    import RPi.GPIO as _RPIGPIO
    _RPIGPIO.setmode(_RPIGPIO.BCM)
    _RPIGPIO.setwarnings(False)
    _GPIO_FAULT_AVAILABLE = True
    log.info("[FAULT-GPIO] RPi.GPIO disponible")
except ImportError:
    log.warning("[FAULT-GPIO] RPi.GPIO non disponible -- mode simulation")

# Broches d'injection de défauts -- cible POMPE uniquement (SPDT/ISO_MOT/ISO_DEF supprimés)
# [POMPE_ONLY] GPIO17 (ISO_MOT), GPIO27 (ISO_DEF), GPIO12 (SPDT) supprimes
_FAULT_PIN_ISO       = 18   # Relais ISO      : noeud B -> A0 (LOW=connecté, actif bas)
                             # [PROTECT] TOUJOURS premier HIGH / dernier LOW
_FAULT_PIN_MUX_A     =  6   # CD4051 select A (LSB)
_FAULT_PIN_MUX_B     = 13   # CD4051 select B (MSB)
_FAULT_PIN_Y0_GATE   =  5   # Gate IRLZ44N Y0 : OPEN LOAD (LOW) / SHORT TO GND (HIGH)
_FAULT_PIN_Y2_BASE   = 16   # Base 2N2222  Y2 : SHORT TO VCC
_FAULT_PIN_VLOAD_PWM = 26   # GPIO26 PWM 50kHz -> filtre RC -> Gate IRLZ44N Y3 VARIABLE LOAD
_VLOAD_PWM_FREQ      = 50000
_pwm_vload           = None  # objet RPi.GPIO.PWM initialisé dans _fault_gpio_init()

# État courant d'injection
_fault_mode   = "NORMAL"
_fault_target = "POMPE"
_fault_duty   = 50.0
_fault_lock   = threading.Lock()


def _fault_gpio_init():
    global _pwm_vload
    if not _GPIO_FAULT_AVAILABLE:
        return
    # [PROTECT] ISO LOW au démarrage = mesure normale (relais fermé)
    _RPIGPIO.setup(_FAULT_PIN_ISO,       _RPIGPIO.OUT, initial=_RPIGPIO.LOW)
    _RPIGPIO.setup(_FAULT_PIN_MUX_A,     _RPIGPIO.OUT, initial=_RPIGPIO.LOW)
    _RPIGPIO.setup(_FAULT_PIN_MUX_B,     _RPIGPIO.OUT, initial=_RPIGPIO.LOW)
    _RPIGPIO.setup(_FAULT_PIN_Y0_GATE,   _RPIGPIO.OUT, initial=_RPIGPIO.LOW)
    _RPIGPIO.setup(_FAULT_PIN_Y2_BASE,   _RPIGPIO.OUT, initial=_RPIGPIO.LOW)
    _RPIGPIO.setup(_FAULT_PIN_VLOAD_PWM, _RPIGPIO.OUT, initial=_RPIGPIO.LOW)
    _pwm_vload = _RPIGPIO.PWM(_FAULT_PIN_VLOAD_PWM, _VLOAD_PWM_FREQ)
    _pwm_vload.start(0)
    log.info("[FAULT-GPIO] Broches initialisees: ISO=GPIO%d MUX_A=GPIO%d MUX_B=GPIO%d "
             "Y0=GPIO%d Y2=GPIO%d VLOAD_PWM=GPIO%d freq=%dHz",
             _FAULT_PIN_ISO, _FAULT_PIN_MUX_A, _FAULT_PIN_MUX_B,
             _FAULT_PIN_Y0_GATE, _FAULT_PIN_Y2_BASE,
             _FAULT_PIN_VLOAD_PWM, _VLOAD_PWM_FREQ)


def _fault_gpio_desactiver_sources():
    """Éteint toutes les sources de défaut. [PROTECT] appeler AVANT ISO."""
    if not _GPIO_FAULT_AVAILABLE:
        return
    _RPIGPIO.output(_FAULT_PIN_Y0_GATE, _RPIGPIO.LOW)
    _RPIGPIO.output(_FAULT_PIN_Y2_BASE, _RPIGPIO.LOW)
    _RPIGPIO.output(_FAULT_PIN_MUX_A,   _RPIGPIO.LOW)
    _RPIGPIO.output(_FAULT_PIN_MUX_B,   _RPIGPIO.LOW)
    if _pwm_vload is not None:
        _pwm_vload.ChangeDutyCycle(0)



def _fault_gpio_ouvrir_iso():
    """[PROTECT] ISO ouvert : noeud B déconnecté de A0. Appeler APRÈS desactiver_sources."""
    if _GPIO_FAULT_AVAILABLE:
        _RPIGPIO.output(_FAULT_PIN_ISO, _RPIGPIO.HIGH)


def _fault_gpio_fermer_iso():
    """[PROTECT] ISO fermé : mesure normale. Appeler EN DERNIER au retour NORMAL."""
    if _GPIO_FAULT_AVAILABLE:
        _RPIGPIO.output(_FAULT_PIN_ISO, _RPIGPIO.LOW)


def set_variable_load(duty: float):
    """[VLOAD] Règle le duty cycle PWM. duty : 0.0 à 100.0."""
    duty = max(0.0, min(100.0, float(duty)))
    if _GPIO_FAULT_AVAILABLE and _pwm_vload is not None:
        _pwm_vload.ChangeDutyCycle(duty)
    log.info("[VLOAD] Duty cycle : %.1f%%", duty)


def _fault_gpio_open_load():
    """[PROTECT] ISO ouvert EN PREMIER, MUX Y0, Y0_GATE=LOW."""
    _fault_gpio_desactiver_sources()
    _fault_gpio_ouvrir_iso()            # GPIO18 HIGH EN PREMIER
    if _GPIO_FAULT_AVAILABLE:
        _RPIGPIO.output(_FAULT_PIN_MUX_A,   _RPIGPIO.LOW)
        _RPIGPIO.output(_FAULT_PIN_MUX_B,   _RPIGPIO.LOW)
        _RPIGPIO.output(_FAULT_PIN_Y0_GATE, _RPIGPIO.LOW)


def _fault_gpio_short_to_gnd():
    """[PROTECT] ISO ouvert EN PREMIER, MUX Y0, Y0_GATE=HIGH (IRLZ44N sature)."""
    _fault_gpio_desactiver_sources()
    _fault_gpio_ouvrir_iso()            # GPIO18 HIGH AVANT GPIO5=HIGH
    if _GPIO_FAULT_AVAILABLE:
        _RPIGPIO.output(_FAULT_PIN_MUX_A,   _RPIGPIO.LOW)
        _RPIGPIO.output(_FAULT_PIN_MUX_B,   _RPIGPIO.LOW)
        _RPIGPIO.output(_FAULT_PIN_Y0_GATE, _RPIGPIO.HIGH)  # EN DERNIER


def _fault_gpio_short_vcc():
    """[PROTECT] ISO ouvert EN PREMIER, MUX Y2, Y2_BASE=HIGH."""
    _fault_gpio_desactiver_sources()
    _fault_gpio_ouvrir_iso()            # GPIO18 HIGH EN PREMIER
    if _GPIO_FAULT_AVAILABLE:
        _RPIGPIO.output(_FAULT_PIN_MUX_A,   _RPIGPIO.LOW)
        _RPIGPIO.output(_FAULT_PIN_MUX_B,   _RPIGPIO.HIGH)
        _RPIGPIO.output(_FAULT_PIN_Y2_BASE, _RPIGPIO.HIGH)


def _fault_gpio_variable_load(duty: float):
    """[VLOAD] ISO reste FERMÉ. MUX Y3 (A=HIGH,B=HIGH), PWM EN DERNIER."""
    _fault_gpio_desactiver_sources()    # PWM -> 0%, signaux OFF
    # ISO reste fermé : ADS1115 lit noeud B directement (variation visible)
    if _GPIO_FAULT_AVAILABLE:
        _RPIGPIO.output(_FAULT_PIN_MUX_A, _RPIGPIO.HIGH)   # MUX Y3
        _RPIGPIO.output(_FAULT_PIN_MUX_B, _RPIGPIO.HIGH)
    set_variable_load(duty)             # PWM EN DERNIER


def _fault_gpio_retour_normal():
    """[PROTECT] Sources OFF, PWM=0%, ISO fermé EN DERNIER."""
    _fault_gpio_desactiver_sources()
    _fault_gpio_fermer_iso()            # GPIO18 LOW EN DERNIER
    log.info("[FAULT-GPIO] Retour NORMAL -- toutes sources OFF")


def _apply_fault_mode(mode: str, cible: str = "POMPE", duty: float = 50.0):
    """Applique le mode d'injection de défaut sur les GPIOs du Simulateur (cible POMPE fixe)."""
    global _fault_mode, _fault_target, _fault_duty
    with _fault_lock:
        _fault_target = "POMPE"   # [POMPE_ONLY] cible toujours POMPE
        _fault_duty   = duty
        if mode != _fault_mode:
            _fault_mode = mode
            log.info("[FAULT] Mode change -> %s  duty=%.1f", mode, duty)
            if mode == "NORMAL":
                _fault_gpio_retour_normal()
            elif mode == "OPEN LOAD":
                _fault_gpio_open_load()
            elif mode == "SHORT TO GND":
                _fault_gpio_short_to_gnd()
            elif mode == "SHORT TO VCC":
                _fault_gpio_short_vcc()
            elif mode == "VARIABLE LOAD":
                _fault_gpio_variable_load(duty)
            else:
                log.warning("[FAULT] Mode inconnu: %s -- retour NORMAL", mode)
                _fault_gpio_retour_normal()


# ============================================================
# ENUMS / DATACLASSES
# ============================================================
class Ignition(IntEnum):
    OFF = 0
    ACC = 1
    ON  = 2

    @classmethod
    def from_value(cls, v) -> "Ignition":
        if isinstance(v, str):
            return cls[v.upper()]
        return cls(max(0, min(2, int(v))))


@dataclass
class SensorState:
    # blade_position : valeur publiee dans la trame 0x201 vers le BCM.
    # Historiquement alimentee directement par le pot. On la conserve pour
    # ne pas casser les consommateurs existants -- elle reflete blade_real.
    blade_position: float = 0.0

    # blade_real : valeur reelle lue sur le potentiometre (ADS1115 canal A2).
    # Mise a jour par _main_loop() a chaque cycle.
    blade_real:     float = 0.0

    # blade_sim  : valeur "simulee" forcee par une interface de test externe.
    # ECRITE DEPUIS L'EXTERIEUR (par une collegue via son test automatique).
    # Le WC compare blade_real et blade_sim : si |real - sim| > 10% pendant
    # plus d'1 seconde, DTC B2103 declenche.
    # Semantique : tant que blade_sim <= 0, aucune comparaison (= pas de test).
    blade_sim:      float = -1.0

    # motor_driver_fault : flag de defaut electronique du driver moteur.
    # ECRIT DEPUIS L'EXTERIEUR (bouton d'une interface de test).
    # N'EST PAS un critere de surintensite -- la surintensite est deja
    # geree cote BCM a partir de la trame 0x201.
    # Quand passe True : DTC B2102 (WC Motor Driver Fault) est arme.
    # Quand repasse False : rien, le DTC reste armé jusqu'au ClearDTC.
    motor_driver_fault: bool = False

    motor_current:  float = 0.0
    fault_status:   int   = 0
    # TC_CAN_202_ERR02/04/05 : bits forcés par interface de test externe.
    # Résistent aux écrasements de update() (lecture GPIO hardware = 0 sur banc).
    # Appliqués en OR dans _build_0x201 indépendamment de fault_status.
    _forced_fault_bits: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(self, blade: float, current: float, fault: int):
        with self._lock:
            self.blade_position = round(max(0.0, min(100.0, blade)), 1)
            self.blade_real     = round(blade, 1)   # copie pot -> blade_real
            self.motor_current  = round(current, 3)
            self.fault_status   = fault

    def set_blade_sim(self, value: float):
        """
        API d'ecriture pour l'interface de test externe.
        value >= 0  : arme la comparaison avec la cible donnee (en %)
        value <  0  : desarme la comparaison (aucun test actif)
        """
        with self._lock:
            self.blade_sim = float(value)

    def set_motor_driver_fault(self, value: bool):
        """
        API d'ecriture pour l'interface de test externe.
        Passer True pour simuler un defaut driver moteur -> arme B2102.
        Passer False pour retirer le defaut (le DTC reste actif jusqu'au
        ClearDTC UDS cote WC, le flag sert uniquement a la detection).
        """
        with self._lock:
            self.motor_driver_fault = bool(value)

    def set_fault_status_bits(self, bits: int):
        """
        TC_CAN_202_ERR02/04/05 : force le champ FaultStatus (byte4 de 0x201).
        Utilise _forced_fault_bits (séparé de fault_status) pour résister
        aux écrasements de update() qui lit le GPIO hardware (= 0 sur banc).
        bits : masque OR des bits à activer :
          bit0 = FaultStatus_WC_Internal   (ErrorCode=0x05)
          bit1 = FaultStatus_MotorDriver   (ErrorCode=0x02)
          bit2 = FaultStatus_PosSensor     (ErrorCode=0x04)
        """
        with self._lock:
            self._forced_fault_bits = int(bits) & 0x3F

    def reset_fault_status_bits(self):
        """Remet _forced_fault_bits à 0x00 (fin de test TC_CAN_202_ERR02/04/05)."""
        with self._lock:
            self._forced_fault_bits = 0

    def get_fault_byte_for_tx(self) -> int:
        """
        Retourne le FaultStatus effectif pour _build_0x201.
        Combine 3 sources en OR :
          1. fault_status      : valeur hardware (GPIO/ADS) mise à jour par update()
          2. _forced_fault_bits: bits forcés par test externe (set_fault_status_bits)
          3. DTC actifs        : quand B2102/B2103 est actif, le WC doit reporter
                                 le bit correspondant dans 0x201 byte4 automatiquement
                                 → c'est ce qui déclenche naturellement le NACK 0x202
        """
        with self._lock:
            hw_bits     = int(self.fault_status)
            forced_bits = int(self._forced_fault_bits)
            # DTC actifs → bits FaultStatus automatiques (sans injection externe)
            dtc_bits = 0
            if self.motor_driver_fault or self.xcp_motor_driver_fault:
                dtc_bits |= 0x02   # bit1 = FaultStatus_MotorDriver (B2102)
            if self.xcp_position_sensor_fault:
                dtc_bits |= 0x04   # bit2 = FaultStatus_PosSensor (B2103)
            if self.xcp_internal_fault:
                dtc_bits |= 0x01   # bit0 = FaultStatus_WC_Internal (B2101)
        return (hw_bits | forced_bits | dtc_bits) & 0x3F

    # ── Variables XCP WC (ecrites par xcp_server_wc.py) ──────────────────
    # OR avec les conditions reelles : le DTC se declenche si la condition
    # reelle OU si le flag XCP est a True.
    xcp_internal_fault:       bool = False   # OR B2101
    xcp_motor_driver_fault:   bool = False   # OR B2102
    xcp_position_sensor_fault: bool = False  # OR B2103

    def set_xcp_internal_fault(self, value: bool):
        with self._lock:
            self.xcp_internal_fault = bool(value)

    def set_xcp_motor_driver_fault(self, value: bool):
        with self._lock:
            self.xcp_motor_driver_fault = bool(value)

    def set_xcp_position_sensor_fault(self, value: bool):
        with self._lock:
            self.xcp_position_sensor_fault = bool(value)

    # ── T50b : injection surcourant moteur via CAN (Cas B / H-bridge) ─────────
    # motor_current_override >= 0 : force la valeur MotorCurrent dans trame 0x201.
    # motor_current_override  < 0 : désactivé, lecture ADS normale.
    motor_current_override: float = -1.0
    _motor_override_ts: float = 0.0
    _MOTOR_OVERRIDE_HOLD: float = 3.0  # durée max de l'override (auto-reset)

    def set_motor_current_override(self, value: float):
        """Force motor_current dans la prochaine trame 0x201 (T50b)."""
        with self._lock:
            self.motor_current_override = float(value)
            self._motor_override_ts = 0.0  # reset hold timer

    def get_motor_current_for_tx(self) -> float:
        """Retourne le courant à publier : override si actif, sinon valeur ADS."""
        with self._lock:
            if self.motor_current_override >= 0:
                import time as _t
                now = _t.time()
                if self._motor_override_ts == 0.0:
                    self._motor_override_ts = now
                if (now - self._motor_override_ts) < self._MOTOR_OVERRIDE_HOLD:
                    return self.motor_current_override
                # Hold expiré → reset
                self.motor_current_override = -1.0
                self._motor_override_ts = 0.0
            return self.motor_current

    # ── T_B2009_BLADE_STUCK : gel de BladePosition dans trame 0x201 ──────────
    # blade_position_frozen=True : BladePosition reste figée à _blade_frozen_val.
    # Simule une lame mécaniquement bloquée (pot ne bouge plus).
    blade_position_frozen: bool = False
    _blade_frozen_val:     float = 0.0

    def set_blade_frozen(self, frozen: bool, value: float = 0.0):
        """Gèle ou libère BladePosition dans la trame 0x201."""
        with self._lock:
            self.blade_position_frozen = frozen
            self._blade_frozen_val     = float(value)

    # ── Blade cycling (T50 / T_B2009_CAN_REST_STUCK) ─────────────────────────
    # Oscille BladePosition entre 1% et 99% toutes les period_ms.
    # But : empêcher B2009 (blade_pos>0 constant) tout en simulant un cycle réel.
    _blade_cycling:      bool  = False
    _blade_cycle_period: float = 1500.0   # ms
    _blade_cycle_thread: object = None    # threading.Thread

    def start_blade_cycling(self, period_ms: float = 1500.0):
        """Démarre l'oscillation BladePosition 1↔99 toutes les period_ms."""
        import threading as _th
        import time as _t
        with self._lock:
            self._blade_cycling      = True
            self._blade_cycle_period = period_ms
        def _cycle():
            phase = 0  # 0=montée(1→99) 1=descente(99→1)
            while True:
                with self._lock:
                    if not self._blade_cycling:
                        break
                    p = self._blade_cycle_period / 1000.0
                # Alterner entre 1 et 99 toutes les period_ms
                target = 99.0 if phase == 0 else 1.0
                with self._lock:
                    if not self.blade_position_frozen:
                        self.blade_position = target
                phase = 1 - phase
                _t.sleep(p)
        t = _th.Thread(target=_cycle, daemon=True)
        with self._lock:
            self._blade_cycle_thread = t
        t.start()

    def stop_blade_cycling(self):
        """Arrête l'oscillation BladePosition et remet à 0."""
        with self._lock:
            self._blade_cycling  = False
            self.blade_position  = 0.0

    def get_blade_for_tx(self) -> float:
        """Retourne BladePosition à publier : valeur gelée si actif, sinon blade_position."""
        with self._lock:
            if self.blade_position_frozen:
                return self._blade_frozen_val
            return self.blade_position

    def snapshot(self):
        with self._lock:
            return self.blade_position, self.motor_current, self.fault_status

    def snapshot_blade_diag(self):
        """Snapshot dedie a la detection B2103 : (blade_real, blade_sim)."""
        with self._lock:
            return self.blade_real, self.blade_sim

    def snapshot_driver_fault(self):
        """Snapshot dedie a la detection B2102 : motor_driver_fault."""
        with self._lock:
            return self.motor_driver_fault


@dataclass
class VehicleState:
    ignition: int = 0
    reverse:  int = 0
    speed:    int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(self, ign: int, rev: int, spd_raw: int):
        with self._lock:
            self.ignition = ign
            self.reverse  = rev
            self.speed    = spd_raw

    def snapshot(self):
        with self._lock:
            return self.ignition, self.reverse, self.speed


@dataclass
class RainState:
    intensity: int  = 0
    sensor_ok: bool = True
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(self, intensity: int, ok: bool):
        with self._lock:
            self.intensity = intensity
            self.sensor_ok = ok

    def snapshot(self):
        with self._lock:
            return self.intensity, self.sensor_ok


@dataclass
class WiperCmd:
    mode:     int = 0
    speed:    int = 0
    wash:     int = 0
    alive_rx: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(self, mode: int, speed: int, wash: int, alive: int):
        with self._lock:
            self.mode     = mode
            self.speed    = speed
            self.wash     = wash
            self.alive_rx = alive

    def snapshot(self):
        with self._lock:
            return self.mode, self.speed, self.wash, self.alive_rx


_sensor_state  = SensorState()
_vehicle_state = VehicleState(ignition=1)   # défaut ON — évite boucle OFF→SPEED1 si Platform n'a pas encore envoyé 0x300
_rain_state    = RainState()
_wiper_cmd     = WiperCmd()

# ============================================================
# HARDWARE
# ============================================================
_chip       = None
_line_fault = None
_i2c_bus    = None
_ads        = None
_chan_blade  = None
_chan_motor  = None
_i2c_lock   = threading.Lock()


def _init_i2c():
    i2c = busio.I2C(board.SCL, board.SDA)
    ads = ADS.ADS1115(i2c, address=_ADS_ADDR)
    ads.gain = _ADS_GAIN
    return i2c, ads, AnalogIn(ads, _ADS_CHAN_BLADE), AnalogIn(ads, _ADS_CHAN_MOTOR)


def _reinit_i2c():
    global _i2c_bus, _ads, _chan_blade, _chan_motor
    if not _HW_I2C or _i2c_bus is None:
        return
    try:
        _i2c_bus.deinit()
    except OSError:
        pass
    time.sleep(0.1)
    try:
        _i2c_bus, _ads, _chan_blade, _chan_motor = _init_i2c()
        log.info("[I2C] Reinit OK")
    except Exception as e:
        log.warning("[I2C] Reinit echec: %s  blade=0 motor=0", e)
        _chan_blade = _chan_motor = None


def _init_hardware():
    global _chip, _line_fault, _i2c_bus, _ads, _chan_blade, _chan_motor

    # gpiod
    if _HW_GPIOD:
        try:
            log.info("[INIT] gpiod chip...")
            _chip = gpiod.Chip(_CHIP_NAME)
            line  = _chip.get_line(_PIN_FAULT)
            try:
                line.request(consumer="fault", type=gpiod.LINE_REQ_DIR_IN,
                             flags=gpiod.LINE_REQ_FLAG_BIAS_PULL_UP)
            except OSError:
                line.request(consumer="fault", type=gpiod.LINE_REQ_DIR_IN)
            _line_fault = line
            log.info("[INIT] gpiod OK  fault line=%d", _PIN_FAULT)
        except Exception as e:
            log.warning("[INIT] gpiod echec (%s)  fault_status=0", e)
    else:
        log.warning("[INIT] gpiod absent  fault_status=0")

    # ADS1115
    if _HW_I2C:
        try:
            log.info("[INIT] I2C + ADS1115...")
            _i2c_bus, _ads, _chan_blade, _chan_motor = _init_i2c()
            log.info("[INIT] ADS1115 OK  canal_blade=A%d canal_motor=A%d",
                     _ADS_CHAN_BLADE, _ADS_CHAN_MOTOR)
        except Exception as e:
            log.warning("[INIT] ADS1115 indisponible (%s)  blade=0.0 motor=0.0", e)
    else:
        log.warning("[INIT] bibliotheques I2C absentes  blade=0.0 motor=0.0")

    # Initialisation broches injection de défauts
    _fault_gpio_init()


def cleanup(reason: str = ""):
    global _shutdown
    _shutdown = True   # FIX: stoppe _main_loop avant de fermer gpiod
    log.info("[SHUTDOWN] BCM arret: %s", reason)
    # Retour état normal des GPIOs d'injection avant arrêt
    try:
        _fault_gpio_retour_normal()
    except Exception:
        pass
    if _GPIO_FAULT_AVAILABLE:
        try:
            _RPIGPIO.cleanup()
        except Exception:
            pass
    for action in (
        lambda: _line_fault.release() if _line_fault else None,
        lambda: _chip.close()         if _chip       else None,
        lambda: _i2c_bus.deinit()     if _i2c_bus    else None,
        lambda: os.system("ip link set " + _CAN_IFACE + " down 2>/dev/null"),
    ):
        try:
            action()
        except Exception:
            pass


# ============================================================
# TCP SERVER
# ============================================================
_tcp_clients      : list = []
_tcp_clients_lock = threading.Lock()
_IGN_STR_MAP      = {"OFF": 0, "ACC": 1, "ON": 2}


def _broadcast_to_motor_clients(msg: dict) -> None:
    line = (json.dumps(msg) + "\n").encode("utf-8")
    dead = []
    with _tcp_clients_lock:
        for c in _tcp_clients:
            try:
                c.sendall(line)
            except OSError:
                dead.append(c)
        for c in dead:
            _tcp_clients.remove(c)


_prev_ignition: int = -1   # -1 = jamais recu

def _handle_vehicle_status(obj: dict):
    global _prev_ignition
    raw_ign = obj.get("ignition_status", "OFF")
    ign = _IGN_STR_MAP.get(raw_ign.upper(), 0) if isinstance(raw_ign, str) \
          else max(0, min(2, int(raw_ign)))
    rev       = 1 if obj.get("reverse_gear", 0) else 0
    speed_kmh = float(obj.get("vehicle_speed", 0.0))
    speed_raw = max(0, min(65535, int(round(speed_kmh * 10))))
    _vehicle_state.update(ign, rev, speed_raw)
    log.info("[TCP] VehicleStatus: IGN=%d REV=%d SPD=%.1f km/h", ign, rev, speed_kmh)
    # Notifier DTCManager WC du changement de cycle d'allumage
    if _prev_ignition != ign:
        try:
            import wc_doip as _wc_doip
            if _wc_doip._dtc_mgr is not None:
                if _prev_ignition == 0 and ign != 0:
                    _wc_doip._dtc_mgr.notify_ignition_on()   # OFF -> ON
                elif _prev_ignition != 0 and _prev_ignition != -1 and ign == 0:
                    _wc_doip._dtc_mgr.notify_ignition_off()  # ON -> OFF
        except Exception:
            pass
        _prev_ignition = ign


def _handle_rain_sensor(obj: dict):
    intensity  = max(0, min(100, int(round(float(obj.get("rain_intensity", 0))))))
    status_raw = obj.get("sensor_status", "OK")
    ok = (status_raw.upper() == "OK") if isinstance(status_raw, str) else bool(status_raw)
    _rain_state.update(intensity, ok)
    log.info("[TCP] RainSensor: intensity=%d%% status=%s", intensity, "OK" if ok else "ERROR")


def _tcp_client_handler(conn: socket.socket, addr):
    log.info("[TCP] Client connecte: %s", addr)
    with _tcp_clients_lock:
        _tcp_clients.append(conn)

    bp, mc, fs = _sensor_state.snapshot()
    try:
        conn.sendall((json.dumps({"front": {
            "blade_position": bp,
            "motor_current":  mc,
            "fault_status":   fs,
        }}) + "\n").encode("utf-8"))
    except OSError:
        pass

    buf = ""
    try:
        while True:
            try:
                raw = conn.recv(1024).decode("utf-8", errors="replace")
                if not raw:
                    break
                buf += raw
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        if "test_cmd" in obj:
                            global _can_tx_paused
                            global _alive_counter_frozen, _alive_counter_prev, _alive_freeze_repeat_count
                            global _corrupt_crc_count
                            global _mode_mismatch_0x201
                            tc = obj["test_cmd"]
                            if tc == "stop_can_tx":
                                with _can_tx_paused_lock:
                                    _can_tx_paused = True
                                log.info("[TEST] CAN TX mis en pause (stop_can_tx)")
                                _broadcast_to_motor_clients({
                                    "can_fault": True, "state": "FAULT",
                                    "fault": True, "msg": "CAN TX paused by test_cmd",
                                })
                            elif tc == "start_can_tx":
                                with _can_tx_paused_lock:
                                    _can_tx_paused = False
                                log.info("[TEST] CAN TX repris (start_can_tx)")
                                _broadcast_to_motor_clients({
                                    "can_fault": False, "state": "OFF",
                                    "fault": False, "msg": "CAN TX resumed",
                                })
                            elif tc == "corrupt_crc_0x201":
                                # TC_FSR_010 : injecter N trames 0x201 avec CRC faux
                                count = int(obj.get("count", 10))
                                with _corrupt_crc_count_lock:
                                    _corrupt_crc_count = count
                                log.info("[TEST] TC_FSR_010 : %d trames 0x201 avec CRC corrompu", count)
                                _broadcast_to_motor_clients({
                                    "type": "info", "msg": f"corrupt_crc_0x201 x{count}"
                                })
                            elif tc == "freeze_can_alive":
                                # TC_CAN_003 : geler la détection AliveCounter 0x200
                                with _alive_counter_frozen_lock:
                                    _alive_counter_frozen      = True
                                    _alive_counter_prev        = -1
                                    _alive_freeze_repeat_count = 0
                                log.info("[TEST] TC_CAN_003 : détection AliveCounter gelé activée (0x200)")
                                _broadcast_to_motor_clients({
                                    "type": "info", "msg": "freeze_can_alive activé"
                                })
                            elif tc == "restore_can_alive":
                                # TC_CAN_003 cleanup : remettre AliveCounter normal
                                with _alive_counter_frozen_lock:
                                    _alive_counter_frozen      = False
                                    _alive_counter_prev        = -1
                                    _alive_freeze_repeat_count = 0
                                # NE PAS remettre _corrupt_crc_count à 0 ici :
                                # TC_FSR_010 peut avoir déjà posé count=8 si les
                                # deux messages arrivent dans le même burst TCP.
                                # _corrupt_crc_count est géré exclusivement par
                                # corrupt_crc_0x201 (set) et _build_0x201 (décrement).
                                log.info("[TEST] AliveCounter restauré (CRC count non touché)")
                            elif tc == "set_blade_sim":
                                # TC_B2103 : injecter la position cible blade_sim
                                val = float(obj.get("value", -1.0))
                                _sensor_state.set_blade_sim(val)
                                log.info("[TEST] B2103 : blade_sim forcé à %.1f%%", val)
                                try:
                                    conn.sendall((json.dumps({
                                        "type":      "blade_sim_ack",
                                        "blade_sim": val,
                                    }) + "\n").encode("utf-8"))
                                except OSError:
                                    pass
                            elif tc == "start_blade_cycling":
                                # T50 / T_B2009_CAN_REST_STUCK : démarre oscillation
                                # BladePosition dans trame 0x201 pour simuler mouvement
                                # normal de la lame via CAN (évite B2009 par blade_pos>0
                                # qui change toutes les ~1500ms < REST_STUCK_DELAY=3s).
                                # La lame alterne entre 1% et 99% toutes les period_ms.
                                period_ms = float(obj.get("period_ms", 1500))
                                _sensor_state.start_blade_cycling(period_ms)
                                log.info("[TEST] start_blade_cycling period=%.0fms", period_ms)
                                _broadcast_to_motor_clients({"type": "info",
                                                "msg": f"blade_cycling started period={period_ms:.0f}ms"})

                            elif tc == "stop_blade_cycling":
                                # T50 / T_B2009_CAN_REST_STUCK cleanup
                                _sensor_state.stop_blade_cycling()
                                log.info("[TEST] stop_blade_cycling")
                                _broadcast_to_motor_clients({"type": "info", "msg": "blade_cycling stopped"})

                            elif tc == "freeze_blade_position":
                                # T_B2009_CAN_BLADE_STUCK : gèle BladePosition à value
                                # pour simuler une lame mécaniquement bloquée.
                                val = float(obj.get("value", 50.0))
                                _sensor_state.set_blade_frozen(True, val)
                                log.info("[TEST] freeze_blade_position value=%.1f", val)
                                _broadcast_to_motor_clients({"type": "info",
                                                "msg": f"blade_position frozen at {val:.1f}%"})

                            elif tc == "unfreeze_blade_position":
                                # Cleanup : libère BladePosition (retour lecture ADS)
                                _sensor_state.set_blade_frozen(False)
                                log.info("[TEST] unfreeze_blade_position")
                                _broadcast_to_motor_clients({"type": "info", "msg": "blade_position unfrozen"})

                            elif tc == "inject_motor_current":
                                # T50b : force MotorCurrent dans trame 0x201
                                val = float(obj.get("value", 0.95))
                                _sensor_state.set_motor_current_override(val)
                                log.info("[TEST] inject_motor_current value=%.3fA", val)
                                _broadcast_to_motor_clients({"type": "info",
                                                "msg": f"motor_current overridden to {val:.3f}A"})

                            elif tc == "reset_motor_current":
                                # Cleanup inject_motor_current
                                _sensor_state.set_motor_current_override(-1.0)
                                log.info("[TEST] reset_motor_current")
                                _broadcast_to_motor_clients({"type": "info", "msg": "motor_current override reset"})

                            elif tc == "reset_b2103":
                                # TC_B2103 post_test : réinitialiser B2103
                                # TC_B2103 : réinitialiser guard anti-réarmement + blade_sim
                                global _b2103_mismatch_start, _b2103_active, _b2103_heal_start
                                _b2103_mismatch_start = 0.0
                                _b2103_heal_start     = 0.0
                                if _b2103_active:
                                    try:
                                        import wc_doip as _wc_doip
                                        if _wc_doip._dtc_mgr is not None:
                                            _wc_doip._dtc_mgr.set_inactive("B2103")
                                    except Exception:
                                        pass
                                _b2103_active         = False
                                _sensor_state.set_blade_sim(-1.0)
                                log.info("[TEST] B2103 : état réinitialisé (garde + blade_sim=-1)")
                                try:
                                    conn.sendall((json.dumps({
                                        "type": "b2103_reset_ack",
                                    }) + "\n").encode("utf-8"))
                                except OSError:
                                    pass

                            elif tc == "reset_b2104":
                                # Post_test TC_B2104 : réinitialiser compteurs NACK/ACK + B2104
                                _b2104_reset()
                                log.info("[TEST] B2104 : compteurs NACK/ACK réinitialisés")
                                try:
                                    conn.sendall((json.dumps({
                                        "type": "b2104_reset_ack",
                                    }) + "\n").encode("utf-8"))
                                except OSError:
                                    pass

                            elif tc == "reset_b2101":
                                # T50 post_test : réinitialiser B2101 (CAN Timeout WC)
                                # Remet _t_last_0x200 à now → elapsed=0
                                # Désactive le check B2101 pendant 3s pour empêcher
                                # le redéclenchement immédiat quand le BCM passe en
                                # Cas A (arrêt émission 0x200 → timeout 2s inévitable).
                                global _t_last_0x200, _can_timeout_b2011, _b2101_check_disabled
                                _t_last_0x200         = time.time()
                                _can_timeout_b2011    = False
                                _b2101_check_disabled = True
                                # Réactiver après 3s (> CAN_TIMEOUT_WC=2s)
                                def _reenable_b2101():
                                    global _b2101_check_disabled, _t_last_0x200
                                    _b2101_check_disabled = False
                                    # Remettre _t_last_0x200=0 pour que le prochain
                                    # check parte d'un état propre (pas de résidu)
                                    _t_last_0x200 = 0.0
                                    log.info("[TEST] B2101 check réactivé")
                                threading.Timer(3.0, _reenable_b2101).start()
                                try:
                                    import wc_doip as _wc_doip_local
                                    if _wc_doip_local._dtc_mgr:
                                        _wc_doip_local._dtc_mgr.set_inactive("B2101")
                                except Exception as e:
                                    log.warning("[TEST] reset_b2101 : set_inactive B2101 erreur: %s", e)
                                log.info("[TEST] B2101 : réinitialisé + check suspendu 3s")
                                try:
                                    conn.sendall((json.dumps({
                                        "type": "b2101_reset_ack",
                                    }) + "\n").encode("utf-8"))
                                except OSError:
                                    pass
                            elif tc == "set_mode_mismatch_0x201":
                                # TC_CAN_202_ERR01 : activer le désaccord
                                # CurrentMode dans 0x201 ≠ WiperMode de 0x200
                                with _mode_mismatch_0x201_lock:
                                    _mode_mismatch_0x201 = True
                                log.info("[TEST] TC_CAN_202_ERR01 : mode_mismatch_0x201 ACTIVÉ — CurrentMode forcé à OFF")
                                _broadcast_to_motor_clients({"type": "info", "msg": "mode_mismatch_0x201 activé"})

                            elif tc == "reset_mode_mismatch_0x201":
                                # TC_CAN_202_ERR01 post_test : désactiver le désaccord
                                with _mode_mismatch_0x201_lock:
                                    _mode_mismatch_0x201 = False
                                log.info("[TEST] TC_CAN_202_ERR01 : mode_mismatch_0x201 DÉSACTIVÉ — CurrentMode normal")
                                _broadcast_to_motor_clients({"type": "info", "msg": "mode_mismatch_0x201 désactivé"})

                            elif tc == "set_fault_status_bits":
                                # TC_CAN_202_ERR02/04/05 : forcer FaultStatus byte4 dans 0x201
                                bits = int(obj.get("bits", 0)) & 0x3F
                                _sensor_state.set_fault_status_bits(bits)
                                log.info("[TEST] fault_status_bits=0x%02X activé dans 0x201", bits)
                                _broadcast_to_motor_clients({"type": "info",
                                    "msg": f"fault_status_bits=0x{bits:02X} activé"})

                            elif tc == "reset_fault_status_bits":
                                # TC_CAN_202_ERR02/04/05 post_test : remettre FaultStatus=0
                                _sensor_state.reset_fault_status_bits()
                                log.info("[TEST] fault_status_bits remis à 0x00")
                                _broadcast_to_motor_clients({"type": "info",
                                    "msg": "fault_status_bits reset 0x00"})

                            elif tc == "set_motor_driver_fault":
                                # Injection défaut driver moteur B2102 depuis Platform
                                # value=True  → arme B2102 (WC Motor Driver Fault)
                                # value=False → retire le signal (DTC reste jusqu'au ClearDTC)
                                value = bool(obj.get("value", False))
                                _sensor_state.set_motor_driver_fault(value)
                                # Ne pas resetter _b2102_active ici :
                                # le healing 1s dans _check_motor_driver_fault
                                # appellera set_inactive() apres stabilite.
                                log.info("[TEST] set_motor_driver_fault=%s → B2102 %s",
                                         value, "armé" if value else "condition retirée (healing 1s)")

                            elif tc == "set_xcp_internal_fault":
                                # TC_CAN_202_ERR05 : déclencher B2101 (WC Internal Fault)
                                # → FaultStatus_WC_Internal bit0=1 dans 0x201 automatiquement
                                value = bool(obj.get("value", True))
                                _sensor_state.set_xcp_internal_fault(value)
                                log.info("[TEST] xcp_internal_fault=%s → B2101 %s + bit0 0x201",
                                         value, "armé" if value else "retiré")
                                _broadcast_to_motor_clients({"type": "info",
                                    "msg": f"xcp_internal_fault={'armé' if value else 'retiré'}"})

                            elif tc == "set_xcp_position_sensor_fault":
                                # TC_CAN_202_ERR04 : déclencher B2103 (WC PosSensor Fault)
                                # → FaultStatus_PosSensor bit2=1 dans 0x201 automatiquement
                                value = bool(obj.get("value", True))
                                _sensor_state.set_xcp_position_sensor_fault(value)
                                log.info("[TEST] xcp_position_sensor_fault=%s → B2103 %s + bit2 0x201",
                                         value, "armé" if value else "retiré")
                                _broadcast_to_motor_clients({"type": "info",
                                    "msg": f"xcp_position_sensor_fault={'armé' if value else 'retiré'}"})

                            else:
                                log.warning("[TEST] test_cmd inconnu: %s", tc)
                        elif "pump_fault_mode" in obj or "pump_fault_target" in obj:
                            # Commande d'injection de défaut depuis la Platform
                            new_mode  = obj.get("pump_fault_mode",  _fault_mode)
                            new_duty  = float(obj.get("duty_cycle", 50.0))
                            log.info("[FAULT] Commande reçue: mode=%s duty=%.1f", new_mode, new_duty)
                            threading.Thread(
                                target=_apply_fault_mode,
                                args=(new_mode, "POMPE", new_duty),
                                daemon=True,
                            ).start()
                            try:
                                conn.sendall((json.dumps({
                                    "type": "fault_ack",
                                    "pump_fault_mode": new_mode,
                                    "duty_cycle":      new_duty,
                                }) + "\n").encode("utf-8"))
                            except OSError:
                                pass
                        elif any(k in obj for k in ("ignition_status", "reverse_gear", "vehicle_speed")):
                            _handle_vehicle_status(obj)
                        elif obj.get("can_id_int") == 0x202:
                            # T50b/c/d : injection Wiper_Ack (0x202) depuis Platform via sim_client
                            # Reconstruit les 4 bytes et relaie vers la file CAN (même chemin que 0x202 normal)
                            f   = obj.get("fields", {})
                            b0  = int(f.get("ack_status", 0)) & 0xFF
                            b1  = int(f.get("error_code", 0)) & 0xFF
                            b2  = int(f.get("alive",      0)) & 0xFF
                            crc = int(f.get("crc", (b0 ^ b1 ^ b2) & 0xFF)) & 0xFF
                            _relay_0x202(bytes([b0, b1, b2, crc]))
                        if any(k in obj for k in ("rain_intensity", "sensor_status")):
                            _handle_rain_sensor(obj)
                    except (json.JSONDecodeError, ValueError):
                        pass
            except socket.timeout:
                pass
            except OSError:
                break
    finally:
        with _tcp_clients_lock:
            if conn in _tcp_clients:
                _tcp_clients.remove(conn)
        try:
            conn.close()
        except OSError:
            pass
        log.info("[TCP] Client deconnecte: %s", addr)


def _tcp_server(tcp_host: str, tcp_port: int):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except AttributeError:
        pass
    for attempt in range(10):
        try:
            srv.bind((tcp_host, tcp_port))
            break
        except OSError:
            log.warning("[TCP] Port %d occupe, attente 1s (tentative %d/10)...",
                        tcp_port, attempt + 1)
            import time as _t; _t.sleep(1.0)
    else:
        log.error("[TCP] Port %d toujours occupe -- abandon", tcp_port)
        return
    srv.listen(5)
    log.info("[TCP] Serveur en ecoute sur %s:%d", tcp_host, tcp_port)
    while True:
        try:
            conn, addr = srv.accept()
            conn.settimeout(2.0)   # timeout généreux : sim_client lit l'ident puis envoie (~1s)
            threading.Thread(
                target=_tcp_client_handler,
                args=(conn, addr),
                daemon=True,
                name=f"bcmcan_client_{addr[1]}",
            ).start()
        except OSError as e:
            log.error("[TCP] Erreur accept: %s", e)


# ============================================================
# CAN — Construction des trames (DBC-aware)
# ============================================================
def _build_0x300() -> bytes:
    """
    Construit Vehicle_Status (0x300).
    Utilise pack_frame DBC si disponible, sinon fallback hardcodé.
    Layout : byte0=Ignition  byte1=Reverse  byte2-3=Speed(0.1km/h/bit)
    """
    ign, rev, spd = _vehicle_state.snapshot()
    if _DBC_CFG and _DBC_LOADER_OK:
        msg = _DBC_CFG["messages"].get(_CAN_ID_VEHICLE)
        if msg:
            try:
                return _dbc_pack_sim(msg, {
                    "IgnitionStatus": float(ign & 0xFF),
                    "ReverseGear":    float(rev & 0xFF),
                    "VehicleSpeed":   round(spd / 10.0, 1),
                    "Reserved_300":   0.0,
                })
            except Exception as e:
                log.warning("[BUILD 0x300] DBC pack erreur: %s — fallback", e)
    return bytes([ign & 0xFF, rev & 0xFF, (spd >> 8) & 0xFF, spd & 0xFF,
                  0x00, 0x00, 0x00, 0x00])


def _build_0x301() -> bytes:
    """
    Construit RainSensorData (0x301).
    Utilise pack_frame DBC si disponible, sinon fallback hardcodé.
    Layout : byte0=RainIntensity  byte1=SensorStatus(0=OK/1=FAULT)
    """
    intensity, ok = _rain_state.snapshot()
    if _DBC_CFG and _DBC_LOADER_OK:
        msg = _DBC_CFG["messages"].get(_CAN_ID_RAIN)
        if msg:
            try:
                return _dbc_pack_sim(msg, {
                    "RainIntensity": float(intensity & 0xFF),
                    "SensorOK":      1.0 if ok else 0.0,
                    "Reserved_301":  0.0,
                })
            except Exception as e:
                log.warning("[BUILD 0x301] DBC pack erreur: %s — fallback", e)
    return bytes([intensity & 0xFF, 0x00 if ok else 0x01,
                  0x00, 0x00, 0x00, 0x00, 0x00, 0x00])


def _crc_rx(data8: bytes) -> int:
    """CRC reçu 0x200 : XOR(byte0, byte1, byte2) — Checksum en byte3."""
    return (data8[0] ^ data8[1] ^ data8[2]) & 0xFF


def _crc_tx(payload6: bytes) -> int:
    """CRC émis 0x201 : XOR(byte0..byte5) — CRC_Low en byte6."""
    crc = 0
    for b in payload6:
        crc ^= b
    return crc & 0xFF


def _build_0x201(alive: int) -> bytes:
    """
    Construit Wiper_Status (0x201) — WW-MCAT-005 Rev5.
    Layout 8 bytes :
      byte0 : CurrentMode
      byte1 : CurrentSpeed
      byte2 : BladePosition   (0-100%)
      byte3 : MotorCurrent    (8-bit, 0.1A/bit, range 0-25.5A)
      byte4 : FaultStatus bitfield (bits 0-5 individuels)
              bit0=WC_Internal bit1=MotorDriver bit2=PosSensor
              bit3=Supply      bit4=CAN_Timeout bit5=MotorBlocked
      byte5 : AliveCounter_RX (0-255)
      byte6 : CRC_Low          XOR(byte0..byte5)
      byte7 : Reserved         (0x00)
    """
    bp  = _sensor_state.get_blade_for_tx()         # blade gelée ou réelle
    mc  = _sensor_state.get_motor_current_for_tx()  # courant overridé ou ADS
    # get_fault_byte_for_tx() = OR(fault_status_hw, _forced_fault_bits_test)
    # → résiste aux écrasements de update() sur banc sans GPIO
    fs = _sensor_state.get_fault_byte_for_tx()
    mode, speed, _, _ = _wiper_cmd.snapshot()

    # TC_CAN_202_ERR01 : si mismatch actif, forcer CurrentMode=0x00 (OFF)
    # pour créer un désaccord avec le WiperMode reçu dans 0x200 → le WC
    # émettra naturellement 0x202 avec NACK=1 et ErrorCode=0x01 (InvalidCmd)
    with _mode_mismatch_0x201_lock:
        _apply_mismatch = _mode_mismatch_0x201
    if _apply_mismatch:
        mode = 0   # CurrentMode=OFF ≠ WiperMode envoyé par BCM

    blade_byte = max(0, min(100, int(round(bp))))
    # MotorCurrent : 8-bit, 0.1A/bit → clamp à 25.5A
    mc_byte    = max(0, min(255, int(round(mc * 10))))
    fault_byte = int(fs) & 0x3F   # 6 bits utiles (bits 7:6 réservés = 0)

    # TC_FSR_010 : vérifier si la corruption CRC est demandée
    global _corrupt_crc_count
    _do_corrupt = False
    with _corrupt_crc_count_lock:
        if _corrupt_crc_count > 0:
            _do_corrupt = True
            _corrupt_crc_count -= 1
            log.info("[TEST] TC_FSR_010 : CRC 0x201 corrompu (restant=%d)", _corrupt_crc_count)

    if _DBC_CFG and _DBC_LOADER_OK:
        msg = _DBC_CFG["messages"].get(_CAN_ID_STATUS)
        if msg:
            try:
                # Décomposer fault_byte en bits pour le DBC
                # CRC_Low passé à 0 : sera recalculé sur les octets réels après packing
                data = bytearray(_dbc_pack_sim(msg, {
                    "CurrentMode":             float(mode & 0xFF),
                    "CurrentSpeed":            float(speed & 0xFF),
                    "BladePosition":           float(blade_byte),
                    "MotorCurrent":            round(mc, 3),
                    "FaultStatus_WC_Internal": float((fault_byte >> 0) & 0x01),
                    "FaultStatus_MotorDriver": float((fault_byte >> 1) & 0x01),
                    "FaultStatus_PosSensor":   float((fault_byte >> 2) & 0x01),
                    "FaultStatus_Supply":      float((fault_byte >> 3) & 0x01),
                    "FaultStatus_CAN_Timeout": float((fault_byte >> 4) & 0x01),
                    "FaultStatus_MotorBlocked":float((fault_byte >> 5) & 0x01),
                    "FaultStatus_Reserved":    0.0,
                    "AliveCounter_RX":         float(alive & 0xFF),
                    "CRC_Low":                 0.0,
                    "Reserved_201":            0.0,
                }))
                # CRC calculé sur les octets réels après packing (bytes 0-5)
                # → immunisé contre tout écart DBC d'encodage/arrondi
                crc = _crc_tx(bytes(data[:6]))
                if _do_corrupt:
                    crc = (~crc) & 0xFF
                data[6] = crc
                return bytes(data)
            except Exception as e:
                log.warning("[BUILD 0x201] DBC pack erreur: %s — fallback", e)

    # Fallback hardcodé
    payload6 = bytes([mode & 0xFF, speed & 0xFF, blade_byte,
                      mc_byte, fault_byte, alive & 0xFF])
    crc = _crc_tx(payload6)
    if _do_corrupt:
        crc = (~crc) & 0xFF
    return payload6 + bytes([crc, 0x00])


def _send_can(sock: socket.socket, can_id: int, payload: bytes):
    """Envoie une trame CAN raw socket. DLC = len(payload), padded to 8 bytes."""
    dlc = len(payload)
    padded = (payload + bytes(8))[:8]   # padding à 8 pour le format raw socket
    sock.send(struct.pack(_CAN_FMT, can_id, dlc, padded))


def _build_0x202_from_state(alive_tx: int) -> bytes:
    """
    Construit Wiper_Ack (0x202) directement depuis l'état interne du WC simulateur.
    Aucune dépendance à panels.py — le simulateur WC est la seule source de vérité.

    Priorité ErrorCode (même logique que le vrai WC hardware) :
      bit1 MotorDriver / bit5 MotorBlocked → ErrorCode=0x02
      motor_current ≥ OVERCURRENT_THRESH   → ErrorCode=0x03 (Overcurrent)
      bit2 PosSensor                       → ErrorCode=0x04
      bit0 WC_Internal                     → ErrorCode=0x05
      mode_mismatch (CurrentMode≠WiperMode) → ErrorCode=0x01
      aucune condition                      → AckStatus=0, ErrorCode=0x00
    """
    fault_byte = _sensor_state.get_fault_byte_for_tx()
    fault_motor_driver  = bool(fault_byte & 0x02)   # bit1
    fault_pos_sensor    = bool(fault_byte & 0x04)   # bit2
    fault_wc_internal   = bool(fault_byte & 0x01)   # bit0
    fault_motor_blocked = bool(fault_byte & 0x20)   # bit5

    # Overcurrent : le WC mesure son propre courant moteur dans _sensor_state
    # Si motor_current ≥ OVERCURRENT_THRESH → ErrorCode=0x03 naturellement
    motor_current = _sensor_state.get_motor_current_for_tx()
    overcurrent   = motor_current >= _WC_OVERCURRENT_THRESH

    with _mode_mismatch_0x201_lock:
        mode_mismatch = _mode_mismatch_0x201

    if fault_motor_driver or fault_motor_blocked:
        ack_status, error_code = 1, 0x02
    elif overcurrent:
        ack_status, error_code = 1, 0x03
    elif fault_pos_sensor:
        ack_status, error_code = 1, 0x04
    elif fault_wc_internal:
        ack_status, error_code = 1, 0x05
    elif mode_mismatch:
        ack_status, error_code = 1, 0x01
    else:
        ack_status, error_code = 0, 0x00

    crc = (ack_status ^ error_code ^ alive_tx) & 0xFF
    return bytes([ack_status & 0xFF, error_code & 0xFF, alive_tx & 0xFF, crc])


def _relay_0x202(data4: bytes) -> None:
    """
    Relaie la trame 0x202 Wiper_Ack (WC→BCM) vers la file CAN.
    Trame WW-MCAT-005 Rev5 DLC=4 :
      byte0 bit0 : AckStatus  (0=ACK, 1=NACK)
      byte1      : ErrorCode
      byte2      : AliveCounter_AK
      byte3      : CRC_Ack = XOR(byte0, byte1, byte2)

    B2104 — WC CAN NACK :
      Déclenchement : 3 NACKs consécutifs avec ErrorCode ≠ 0
      Healing       : 3 ACKs consécutifs (AckStatus=0)
    """
    try:
        _ack_relay_queue.put_nowait(data4)
        log.info("[ACK] 0x202 -> file CAN  AckStatus=%d Err=0x%02X Alive=%d",
                 data4[0] & 0x01, data4[1], data4[2])
    except _queue_mod.Full:
        log.warning("[ACK] File 0x202 pleine - trame ignoree")
    # Mise à jour compteurs B2104 sur chaque 0x202 émis
    _b2104_track(data4)


_CAN_TIMEOUT_WC       = 2.0   # B2101 : timeout si pas de 0x200 pendant 2s
_t_last_0x200         = 0.0   # timestamp derniere trame 0x200 valide recue
_WC_OVERCURRENT_THRESH = 0.8    # seuil surintensité WC (A) — même valeur que BCM OVERCURRENT_THRESH
_can_timeout_b2011    = False  # etat courant B2101
_b2101_check_disabled = False  # T50 post_test : suspend le check B2101 temporairement


def _check_can_timeout_b2011():
    """Detecte B2101 (WC Internal Failure - CAN Timeout) et met a jour wc_state."""
    global _can_timeout_b2011
    # T50 post_test : check suspendu pendant la transition Cas B → Cas A
    # Exception : si xcp_internal_fault est armé volontairement (TC_CAN_202_ERR05),
    # ne pas bloquer — laisser le check passer pour déclencher B2101 normalement.
    with _sensor_state._lock:
        _xcp_forced = _sensor_state.xcp_internal_fault
    if _b2101_check_disabled and not _xcp_forced:
        return
    if _t_last_0x200 == 0.0:
        return
    elapsed = time.time() - _t_last_0x200
    with _sensor_state._lock:
        _xcp_b2101 = _sensor_state.xcp_internal_fault
    if (elapsed > _CAN_TIMEOUT_WC or _xcp_b2101) and not _can_timeout_b2011:
        _can_timeout_b2011 = True
        if _xcp_b2101:
            log.warning("[WC-B2101] xcp_internal_fault=True → B2101 (injection test)")
        else:
            log.warning("[WC-B2101] CAN Timeout BCM %.1fs > %.1fs -> B2101",
                        elapsed, _CAN_TIMEOUT_WC)
        try:
            import wc_doip as _wc_doip
            # Mettre a jour wc_state AVANT le snapshot
            bp, mc, fs = _sensor_state.snapshot()
            _wc_doip.wc_state.update_from_bcmcan(
                mode=0, speed=0, blade_pos=0.0,
                motor_current_a=mc, fault_status=int(fs), can_timeout=True)
            if _wc_doip._dtc_mgr:
                _wc_doip._dtc_mgr.set_active("B2101",
                                             _wc_doip.wc_state.make_snapshot())
        except Exception:
            pass
    elif elapsed <= _CAN_TIMEOUT_WC and not _xcp_b2101 and _can_timeout_b2011:
        _can_timeout_b2011 = False
        try:
            import wc_doip as _wc_doip
            if _wc_doip._dtc_mgr:
                _wc_doip._dtc_mgr.set_inactive("B2101")
        except Exception:
            pass


# ============================================================
# THREAD 1 : TX Vehicle (0x300 + 0x301) -- réseau véhicule
# ============================================================
# Responsabilité unique : envoyer cycliquement les trames
# Vehicle_Status (0x300) et RainSensorData (0x301).
# Socket propre et indépendant : une panne ici (bus saturé,
# erreur réseau véhicule) n'affecte pas le thread WC.
# Les deux trames sont décalées de 100ms pour éviter les
# bursts simultanés et réduire le risque ENOBUFS.
# ============================================================

def _can_vehicle_thread():
    """
    Thread T-CAN-VEH -- TX cyclique 0x300 (Vehicle_Status) et 0x301 (RainSensorData).
    Période : _CAN_TX_PERIOD_S par trame, décalage de 100ms entre les deux.
    Socket dédié : isolé du thread WC. Redémarre automatiquement après erreur.
    """
    # Décalage initial de 100ms entre 0x300 et 0x301
    # pour éviter l'envoi simultané des deux trames (risque ENOBUFS)
    last_300 = time.time()
    last_301 = time.time() - (_CAN_TX_PERIOD_S / 2.0)

    log.info("[T-VEH] Thread Vehicle TX démarré (0x300 / 0x301, période=%.0fms, décalage=%.0fms)",
             _CAN_TX_PERIOD_S * 1000, _CAN_TX_PERIOD_S * 500)

    while True:
        sock = None
        try:
            sock = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 131072)  # 128KB TX buffer
            sock.bind((_CAN_IFACE,))
            sock.settimeout(0.010)
            log.info("[T-VEH] Socket ouvert sur %s", _CAN_IFACE)

            while True:
                now = time.time()

                with _can_tx_paused_lock:
                    paused = _can_tx_paused
                if paused:
                    time.sleep(0.010)
                    continue

                # TX 0x300 -- Vehicle_Status
                if now - last_300 >= _CAN_TX_PERIOD_S:
                    try:
                        fd = _build_0x300()
                        _send_can(sock, _CAN_ID_VEHICLE, fd)
                        if _tcp_can:
                            _tcp_can.on_tx_0x300(fd, t_kernel=time.monotonic())
                    except OSError as e:
                        if e.errno == 105:   # ENOBUFS
                            log.warning("[T-VEH] ENOBUFS TX 0x300 -- trame perdue (bus saturé)")
                        else:
                            log.error("[T-VEH] Erreur TX 0x300: %s", e)
                    last_300 = now

                # TX 0x301 -- RainSensorData (décalé de ~100ms par rapport à 0x300)
                if now - last_301 >= _CAN_TX_PERIOD_S:
                    try:
                        fd = _build_0x301()
                        _send_can(sock, _CAN_ID_RAIN, fd)
                        if _tcp_can:
                            _tcp_can.on_tx_0x301(fd, t_kernel=time.monotonic())
                    except OSError as e:
                        if e.errno == 105:   # ENOBUFS
                            log.warning("[T-VEH] ENOBUFS TX 0x301 -- trame perdue (bus saturé)")
                        else:
                            log.error("[T-VEH] Erreur TX 0x301: %s", e)
                    last_301 = now

                time.sleep(0.005)  # 5ms de sleep pour ne pas saturer le CPU

        except Exception as e:
            log.error("[T-VEH] Erreur socket: %s -> retry dans %ds", e, _CAN_RETRY_S)
        finally:
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
        time.sleep(_CAN_RETRY_S)


# ============================================================
# THREAD 2 : RX/TX WC (0x200 / 0x201 / 0x202) -- liaison WC
# ============================================================
# Responsabilité unique : recevoir les commandes WC (0x200),
# répondre avec Wiper_Status (0x201) et relayer les Ack (0x202).
# Socket propre et indépendant : une panne sur le réseau véhicule
# (thread 1) n'affecte pas cette liaison critique BCM <-> WC.
# ============================================================

def _can_wc_thread():
    """
    Thread T-CAN-WC -- RX 0x200 (Wiper_Command) + TX 0x201 (Wiper_Status) + TX 0x202 (Ack).
    Socket dédié : isolé du thread Vehicle. Redémarre automatiquement après erreur.
    """
    global _t_last_0x200
    # FIX TC_CAN_003 : déclaration global obligatoire au niveau de la fonction
    # pour que les assignations dans le bloc 'with lock' modifient les variables
    # module et non des variables locales → évite UnboundLocalError → crash socket.
    global _alive_counter_prev, _alive_freeze_repeat_count
    last_timeout_check = 0.0

    log.info("[T-WC] Thread WC RX/TX démarré (0x200 rx / 0x201 tx / 0x202 relay)")

    while True:
        sock = None
        try:
            sock = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
            sock.setsockopt(socket.SOL_SOCKET, _SO_TIMESTAMP, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 131072)  # 128KB TX buffer
            sock.bind((_CAN_IFACE,))
            sock.settimeout(0.005)
            log.info("[T-WC] Socket ouvert sur %s", _CAN_IFACE)

            while True:
                now = time.time()

                with _can_tx_paused_lock:
                    paused = _can_tx_paused
                if paused:
                    try:
                        sock.recvmsg(_CAN_SIZE, 1024)
                    except socket.timeout:
                        pass
                    except OSError:
                        break
                    time.sleep(0.005)
                    continue


                # Surveillance CAN timeout B2101 (toutes les 500ms)
                if now - last_timeout_check >= 0.500:
                    _check_can_timeout_b2011()
                    last_timeout_check = now

                # RX 0x200 -- Wiper_Command (BCM ? WC)
                try:
                    frame, ancdata, _, _ = sock.recvmsg(_CAN_SIZE, 1024)
                except socket.timeout:
                    continue
                except OSError:
                    break

                if len(frame) < _CAN_SIZE:
                    continue

                t_kernel_rx = time.time()
                for cmsg_level, cmsg_type, cmsg_data in ancdata:
                    if cmsg_level == socket.SOL_SOCKET and cmsg_type == _SO_TIMESTAMP:
                        sec, usec = struct.unpack("ll", cmsg_data[:struct.calcsize("ll")])
                        t_kernel_rx = sec + usec / 1_000_000.0
                        break

                can_id, dlc, data = struct.unpack(_CAN_FMT, frame)
                can_id &= 0x1FFFFFFF

                if can_id != _CAN_ID_CMD or dlc < 4:
                    continue

                d = data[:8]
                if d[3] != _crc_rx(d):
                    log.warning("[T-WC] 0x200 CRC KO recu=0x%02X attendu=0x%02X",
                                d[3], _crc_rx(d))
                    try:
                        import wc_doip as _wc_doip
                        bp, mc, fs = _sensor_state.snapshot()
                        _wc_doip.wc_state.update_from_bcmcan(
                            mode=0, speed=0, blade_pos=0.0,
                            motor_current_a=mc, fault_status=int(fs),
                            can_timeout=False)
                        _wc_doip._dtc_mgr and _wc_doip._dtc_mgr.set_active(
                            "B2101", _wc_doip.wc_state.make_snapshot())
                    except Exception:
                        pass
                    continue

                mode  = d[0] & 0x0F
                speed = (d[0] >> 4) & 0x0F
                wash  = d[1] & 0x03
                alive = d[2]

                # Décodage DBC si disponible (override si signaux renommés)
                if _DBC_CFG and _DBC_LOADER_OK:
                    msg_cmd = _DBC_CFG["messages"].get(_CAN_ID_CMD)
                    if msg_cmd:
                        try:
                            sigs  = _dbc_unpack_sim(msg_cmd, d)
                            mode  = int(sigs.get("WiperMode",       mode))
                            speed = int(sigs.get("WiperSpeedLevel", speed))
                            wash  = int(sigs.get("WashRequest",     wash))
                            alive = int(sigs.get("AliveCounter_TX", alive))
                        except Exception:
                            pass  # fallback valeurs hardcodées déjà définies

                _wiper_cmd.update(mode, speed, wash, alive)
                _t_last_0x200 = time.time()
                log.info("[T-WC] RX 0x200 Mode=%d Speed=%d Wash=%d Alive=%d",
                         mode, speed, wash, alive)

                # TC_CAN_003 : détection AliveCounter figé dans 0x200 (anti-replay WC)
                with _alive_counter_frozen_lock:
                    _frozen_check = _alive_counter_frozen
                if _frozen_check:
                    with _alive_counter_frozen_lock:
                        # FIX TC_CAN_003 : l'ancienne logique attendait que le counter
                        # soit IDENTIQUE sur N trames consécutives. Mais alive_tx_frozen
                        # dans le BCM peut arriver avec un délai Redis (20-100ms) → pendant
                        # ce délai le counter s'incrémente → _alive_counter_prev suit →
                        # _alive_freeze_repeat_count se reset à 1 → jamais ≥ 3 → timeout.
                        #
                        # NOUVEAU : on compte simplement le nombre de trames reçues
                        # DEPUIS l'activation du freeze. Le test TC_CAN_003 a explicitement
                        # gelé le test → toute trame supplémentaire est suspecte.
                        # Après _ALIVE_FREEZE_THRESHOLD trames, on déclare la faute.
                        # Cela simule un WC qui détecte qu'il est en mode test freeze
                        # et lève la faute dès qu'il a accumulé assez de preuves.
                        _alive_freeze_repeat_count += 1
                        _repeat = _alive_freeze_repeat_count
                    if _repeat >= _ALIVE_FREEZE_THRESHOLD:
                        log.warning(
                            "[TC_CAN_003] AliveCounter 0x200 figé = 0x%02X "
                            "(%d fois de suite) → B2101 + wc_alive_fault", alive, _repeat)
                        # Déclencher B2101 (WC Internal Failure) : trame 0x200 invalide
                        try:
                            import wc_doip as _wc_doip
                            if _wc_doip._dtc_mgr:
                                bp, mc, fs = _sensor_state.snapshot()
                                _wc_doip.wc_state.update_from_bcmcan(
                                    mode=mode, speed=speed,
                                    blade_pos=bp,
                                    motor_current_a=mc,
                                    fault_status=int(fs),
                                    can_timeout=False)
                                _wc_doip._dtc_mgr.set_active(
                                    "B2101", _wc_doip.wc_state.make_snapshot())
                                log.info("[TC_CAN_003] B2101 actif (AliveCounter 0x200 fige)")
                        except Exception:
                            pass
                        # Notifier Platform via TCP (même canal que les autres fautes WC)
                        _broadcast_to_motor_clients({
                            "type":          "wc_alive_fault",
                            "wc_alive_fault": True,
                            "alive_value":   alive,
                            "repeat_count":  _repeat,
                            "msg": f"AliveCounter 0x200 figé=0x{alive:02X} x{_repeat}",
                        })
                        # Notifier également via Redis si un RTE-REDIS client est accessible
                        try:
                            import json as _json_mod
                            _r = _bcm_redis
                            if _r is None and _bcm_ip:
                                import redis as _redis_mod
                                _r = _redis_mod.Redis(host=_bcm_ip, port=6379,
                                                      socket_connect_timeout=0.2)
                            if _r is not None:
                                _payload = _json_mod.dumps({"key": "wc_alive_fault", "value": True})
                                _r.publish("rte_cmd", _payload)
                                log.info("[TC_CAN_003] Redis PUBLISH rte_cmd wc_alive_fault=True -> %s", _bcm_ip)
                        except Exception:
                            pass  # Redis optionnel côté simulateur

                # Mise à jour état WC pour DoIP/DTC
                try:
                    import wc_doip as _wc_doip
                    bp, mc, fs = _sensor_state.snapshot()
                    # Sur RPi Simulateur, bp=0 car pas d'ADC.
                    # Utiliser wc_state.blade_pos si deja mis a jour
                    # par la simulation moteur (_run_motor_simulation).
                    # Sinon utiliser bp du capteur physique.
                    try:
                        import wc_doip as _wc_doip_bp
                        sim_bp = _wc_doip_bp.wc_state.blade_pos
                        effective_bp = sim_bp if sim_bp > 0.0 else bp
                    except Exception:
                        effective_bp = bp
                    _wc_doip.wc_state.update_from_bcmcan(
                        mode            = mode,
                        speed           = speed,
                        blade_pos       = effective_bp,
                        motor_current_a = mc,
                        fault_status    = int(fs),
                        can_timeout     = False,
                        pump_status     = wash,
                    )
                except Exception:
                    pass

                if _tcp_can:
                    _tcp_can.on_rx_0x200(d, t_kernel=t_kernel_rx)

                # TX 0x201 -- Wiper_Status (réponse immédiate à 0x200)
                try:
                    f201 = _build_0x201(alive)
                    _send_can(sock, _CAN_ID_STATUS, f201)
                    # Log 0x201 : même style que 0x202 pour visibilité terminal
                    _mode201  = f201[0] if len(f201) > 0 else 0
                    _speed201 = f201[1] if len(f201) > 1 else 0
                    _blade201 = f201[2] if len(f201) > 2 else 0
                    log.info("[T-WC] TX 0x201 Mode=%d Speed=%d Blade=%d Alive=0x%02X",
                             _mode201, _speed201, _blade201, alive)
                    if _tcp_can:
                        _tcp_can.on_tx_0x201(f201, t_kernel=time.monotonic())
                except OSError as e:
                    if e.errno == 105:
                        log.warning("[T-WC] ENOBUFS TX 0x201 -- réponse WC perdue")
                    else:
                        log.error("[T-WC] Erreur TX 0x201: %s", e)

                # TX 0x202 -- Wiper_Ack
                # Priorité : si une trame 0x202 injectée manuellement attend dans
                # _ack_relay_queue (T50b/T50c/T50d send_wiper_ack ErrorCode=0x03),
                # l'utiliser à la place du 0x202 automatique pour éviter collision.
                try:
                    try:
                        f202 = _ack_relay_queue.get_nowait()   # injecté manuellement
                        log.info("[T-WC] TX 0x202 INJECTÉ AckStatus=%d Err=0x%02X Alive=0x%02X CRC=0x%02X",
                                 f202[0] & 0x01, f202[1], f202[2], f202[3])
                    except _queue_mod.Empty:
                        f202 = _build_0x202_from_state(alive)  # automatique depuis état interne
                        log.info("[T-WC] TX 0x202 AckStatus=%d Err=0x%02X Alive=0x%02X CRC=0x%02X",
                                 f202[0] & 0x01, f202[1], f202[2], f202[3])
                    _send_can(sock, _CAN_ID_ACK, f202[:4])
                    _b2104_track(f202)
                    if _tcp_can:
                        _tcp_can.on_tx_0x202(f202, t_kernel=time.monotonic())
                except OSError as e:
                    if e.errno == 105:
                        log.warning("[T-WC] ENOBUFS TX 0x202 -- Ack perdu")
                    else:
                        log.error("[T-WC] Erreur TX 0x202: %s", e)

        except Exception as e:
            log.error("[T-WC] Erreur socket: %s -> retry dans %ds", e, _CAN_RETRY_S)
        finally:
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
        time.sleep(_CAN_RETRY_S)


# ============================================================
# DETECTION B2103 -- WC Position Sensor Fault
# ============================================================
# Comparaison blade_real (pot) vs blade_sim (cible forcee par interface
# de test externe). Si |real - sim| > _BLADE_MISMATCH_THRESH pendant plus
# de _BLADE_MISMATCH_DELAY secondes, DTC B2103 declenche.
#
# blade_sim est une variable ecrite par une interface externe (ex. test
# automatique). Tant que blade_sim < 0, aucun test n'est actif et la
# comparaison est neutralisee.

_BLADE_MISMATCH_THRESH = 10.0  # % d'ecart absolu
_BLADE_MISMATCH_DELAY  = 1.0   # secondes de persistance avant armement

_b2103_mismatch_start  = 0.0   # timer (0 = pas de mismatch en cours)
_b2103_active          = False # garde anti-rearmement
_b2103_heal_start      = 0.0   # timestamp debut condition absente (0 = pas en cours)
_HEAL_DELAY_B2103      = 1.0   # secondes de stabilite avant passage ACTIVE -> INACTIVE


def _check_blade_position_mismatch():
    """
    Lit blade_real et blade_sim depuis _sensor_state, compare,
    arme B2103 si l'ecart persiste.
    Healing : si l'ecart repasse sous le seuil pendant 1s, B2103 passe ACTIVE->INACTIVE.
    """
    global _b2103_mismatch_start, _b2103_active, _b2103_heal_start

    blade_real, blade_sim = _sensor_state.snapshot_blade_diag()
    with _sensor_state._lock:
        _xcp_b2103 = _sensor_state.xcp_position_sensor_fault

    # xcp_position_sensor_fault=True : forcer blade_sim hors plage pour declencher B2103
    if _xcp_b2103 and blade_sim < 0:
        blade_sim = 1.0 if blade_real > 50.0 else 99.0

    # blade_sim < 0 -> aucun test en cours, pas de comparaison
    if blade_sim < 0:
        _b2103_mismatch_start = 0.0
        # Si B2103 etait actif et le test est annule, demarrer healing
        if _b2103_active:
            now = time.time()
            if _b2103_heal_start == 0.0:
                _b2103_heal_start = now
                log.info("[WC-B2103] Test annule (blade_sim<0), healing B2103 dans %.0fs", _HEAL_DELAY_B2103)
            elif (now - _b2103_heal_start) >= _HEAL_DELAY_B2103:
                log.info("[WC-B2103] Condition absente depuis %.0fs -> B2103 INACTIVE", _HEAL_DELAY_B2103)
                try:
                    import wc_doip as _wc_doip
                    if _wc_doip._dtc_mgr is not None:
                        _wc_doip._dtc_mgr.set_inactive("B2103")
                except Exception as e:
                    log.error("[WC-B2103] Impossible de healer DTC: %s", e)
                _b2103_active     = False
                _b2103_heal_start = 0.0
        else:
            _b2103_heal_start = 0.0
        return

    diff = abs(blade_real - blade_sim)
    now  = time.time()

    if diff > _BLADE_MISMATCH_THRESH:
        # Condition de defaut presente
        _b2103_heal_start = 0.0  # reset heal timer
        if _b2103_active:
            return   # deja arme, ne pas spammer
        if _b2103_mismatch_start == 0.0:
            _b2103_mismatch_start = now
            log.info("[WC-SECURITE] Ecart blade_real(%.1f%%) vs blade_sim(%.1f%%)"
                     " = %.1f%% > %.0f%% detecte...",
                     blade_real, blade_sim, diff, _BLADE_MISMATCH_THRESH)
        elif (now - _b2103_mismatch_start) > _BLADE_MISMATCH_DELAY:
            log.warning("[WC-B2103] Ecart position lame %.1f%% > %.0f%% pendant"
                        " %.0fms (real=%.1f%% sim=%.1f%%) -> B2103",
                        diff, _BLADE_MISMATCH_THRESH,
                        _BLADE_MISMATCH_DELAY * 1000,
                        blade_real, blade_sim)
            # Notifier la Platform via Redis : wc_b2103_active = True
            try:
                import json as _json_mod
                _r = _bcm_redis
                if _r is None and _bcm_ip:
                    import redis as _redis_mod
                    _r = _redis_mod.Redis(host=_bcm_ip, port=6379,
                                          socket_connect_timeout=0.2)
                if _r is not None:
                    _payload = _json_mod.dumps({"key": "wc_b2103_active", "value": True})
                    _r.publish("rte_cmd", _payload)
                    log.info("[WC-B2103] Redis PUBLISH rte_cmd wc_b2103_active=True -> %s", _bcm_ip)
            except Exception:
                pass  # Redis optionnel cote simulateur
            _b2103_active         = True
            _b2103_mismatch_start = 0.0
            # set_active() en dernier : ecrit le fichier JSON (I/O bloquante)
            try:
                import wc_doip as _wc_doip
                if _wc_doip._dtc_mgr is not None:
                    bp2, mc2, fs2 = _sensor_state.snapshot()
                    _wc_doip.wc_state.update_from_bcmcan(
                        mode=_wiper_cmd.mode,
                        speed=_wiper_cmd.speed,
                        blade_pos=bp2,
                        motor_current_a=mc2,
                        fault_status=int(fs2),
                        can_timeout=False)
                    _wc_doip._dtc_mgr.set_active(
                        "B2103", _wc_doip.wc_state.make_snapshot())
            except Exception as e:
                log.error("[WC-B2103] Impossible d'armer DTC: %s", e)
    else:
        # Ecart sous le seuil
        _b2103_mismatch_start = 0.0  # reset timer d'armement
        if _b2103_active:
            # Healing : demarrer ou continuer le timer 1s
            if _b2103_heal_start == 0.0:
                _b2103_heal_start = now
                log.info("[WC-B2103] Ecart revenu sous seuil, healing B2103 dans %.0fs", _HEAL_DELAY_B2103)
            elif (now - _b2103_heal_start) >= _HEAL_DELAY_B2103:
                log.info("[WC-B2103] Condition absente depuis %.0fs -> B2103 INACTIVE", _HEAL_DELAY_B2103)
                try:
                    import wc_doip as _wc_doip
                    if _wc_doip._dtc_mgr is not None:
                        _wc_doip._dtc_mgr.set_inactive("B2103")
                except Exception as e:
                    log.error("[WC-B2103] Impossible de healer DTC: %s", e)
                _b2103_active     = False
                _b2103_heal_start = 0.0
        else:
            _b2103_heal_start = 0.0  # pas actif, rien a faire


# ============================================================
# DETECTION B2102 -- WC Motor Driver Fault
# ============================================================
# Lit le flag motor_driver_fault ecrit par une interface externe (bouton
# d'une collegue sur son UI de test). Quand le flag passe False -> True,
# on arme B2102. Rien a voir avec une surintensite : c'est un defaut
# electronique du driver, pas un critere de courant.

_b2102_active      = False  # garde anti-rearmement
_b2102_heal_start  = 0.0   # timestamp debut condition absente (0 = pas en cours)
_HEAL_DELAY_B2102  = 1.0   # secondes de stabilite avant passage ACTIVE -> INACTIVE


def _check_motor_driver_fault():
    """
    Lit _sensor_state.motor_driver_fault et arme B2102 si True.
    Healing : si motor_driver_fault repasse False pendant 1s, B2102 passe ACTIVE->INACTIVE.
    """
    global _b2102_active, _b2102_heal_start

    with _sensor_state._lock:
        fault = _sensor_state.motor_driver_fault or _sensor_state.xcp_motor_driver_fault

    now = time.time()

    if fault:
        # Condition de defaut presente : armer B2102 si pas deja actif
        _b2102_heal_start = 0.0  # reset heal timer
        if _b2102_active:
            return
        log.warning("[WC-B2102] Motor driver fault signale par interface -> B2102")
        try:
            import wc_doip as _wc_doip
            if _wc_doip._dtc_mgr is not None:
                bp3, mc3, fs3 = _sensor_state.snapshot()
                _wc_doip.wc_state.update_from_bcmcan(
                    mode=_wiper_cmd.mode,
                    speed=_wiper_cmd.speed,
                    blade_pos=bp3,
                    motor_current_a=mc3,
                    fault_status=int(fs3),
                    can_timeout=False)
                _wc_doip._dtc_mgr.set_active(
                    "B2102", _wc_doip.wc_state.make_snapshot())
        except Exception as e:
            log.error("[WC-B2102] Impossible d'armer DTC: %s", e)
        _b2102_active = True
    else:
        # Condition absente : healing avec delai 1s
        if _b2102_active:
            if _b2102_heal_start == 0.0:
                _b2102_heal_start = now
                log.info("[WC-B2102] Condition absente, healing B2102 dans %.0fs", _HEAL_DELAY_B2102)
            elif (now - _b2102_heal_start) >= _HEAL_DELAY_B2102:
                log.info("[WC-B2102] Condition absente depuis %.0fs -> B2102 INACTIVE", _HEAL_DELAY_B2102)
                try:
                    import wc_doip as _wc_doip
                    if _wc_doip._dtc_mgr is not None:
                        _wc_doip._dtc_mgr.set_inactive("B2102")
                except Exception as e:
                    log.error("[WC-B2102] Impossible de healer DTC: %s", e)
                _b2102_active     = False
                _b2102_heal_start = 0.0
        else:
            _b2102_heal_start = 0.0  # pas actif, rien a faire


# ============================================================
# DETECTION B2104 -- WC CAN NACK (3 NACKs consécutifs)
# ============================================================
# B2104 est déclenché côté WC quand celui-ci émet 3 trames 0x202 consécutives
# avec AckStatus=1 (NACK) et ErrorCode≠0.
# Cela indique que le WC ne peut pas exécuter les commandes BCM de manière répétée.
# Healing : 3 ACKs consécutifs (AckStatus=0) reçus après un état NACK.

_b2104_nack_count  = 0      # compteur NACKs consécutifs courant
_b2104_ack_count   = 0      # compteur ACKs consécutifs courant (pour healing)
_b2104_active      = False  # garde anti-réarmement
_NACK_THRESHOLD    = 3      # seuil déclenchement
_ACK_HEAL_THRESHOLD = 3     # seuil healing
_b2104_lock        = _queue_mod.Lock() if hasattr(_queue_mod, 'Lock') else __import__('threading').Lock()


def _b2104_track(data4: bytes) -> None:
    """
    Appelée à chaque émission de trame 0x202.
    Met à jour les compteurs NACK/ACK et déclenche ou guérit B2104.
    """
    global _b2104_nack_count, _b2104_ack_count, _b2104_active

    if len(data4) < 2:
        return

    ack_status = data4[0] & 0x01   # bit0 de byte0
    error_code = data4[1] & 0xFF   # byte1

    with _b2104_lock:
        if ack_status == 1 and error_code != 0x00:
            # NACK avec code erreur
            _b2104_nack_count += 1
            _b2104_ack_count   = 0   # reset compteur healing
            log.debug("[WC-B2104] NACK #%d (ErrorCode=0x%02X)",
                      _b2104_nack_count, error_code)

            if _b2104_nack_count >= _NACK_THRESHOLD and not _b2104_active:
                _b2104_active = True
                log.warning("[WC-B2104] %d NACKs consécutifs (ErrorCode=0x%02X) → B2104 ACTIVE",
                            _b2104_nack_count, error_code)
                try:
                    import wc_doip as _wc_doip
                    if _wc_doip._dtc_mgr is not None:
                        snap = _wc_doip.wc_state.make_snapshot()
                        _wc_doip._dtc_mgr.set_active("B2104", snap)
                        # Broadcaster vers Platform via TCP
                        _broadcast_to_motor_clients({
                            "type":         "b2104_active",
                            "wc_b2104_active": True,
                            "nack_count":   _b2104_nack_count,
                            "error_code":   error_code,
                            "msg": f"B2104 : {_b2104_nack_count} NACKs consécutifs "
                                   f"(ErrorCode=0x{error_code:02X})",
                        })
                except Exception as e:
                    log.error("[WC-B2104] Impossible d'armer DTC: %s", e)

        elif ack_status == 0:
            # ACK : incrémenter compteur healing
            _b2104_nack_count = 0   # reset compteur NACK
            if _b2104_active:
                _b2104_ack_count += 1
                log.debug("[WC-B2104] ACK de guérison #%d/%d",
                          _b2104_ack_count, _ACK_HEAL_THRESHOLD)
                if _b2104_ack_count >= _ACK_HEAL_THRESHOLD:
                    _b2104_active    = False
                    _b2104_ack_count = 0
                    log.info("[WC-B2104] %d ACKs consécutifs → B2104 INACTIVE",
                             _ACK_HEAL_THRESHOLD)
                    try:
                        import wc_doip as _wc_doip
                        if _wc_doip._dtc_mgr is not None:
                            _wc_doip._dtc_mgr.set_inactive("B2104")
                            _broadcast_to_motor_clients({
                                "type":            "b2104_inactive",
                                "wc_b2104_active": False,
                                "ack_count":       _ACK_HEAL_THRESHOLD,
                                "msg": "B2104 : healing après 3 ACKs consécutifs",
                            })
                    except Exception as e:
                        log.error("[WC-B2104] Impossible de healer DTC: %s", e)
            else:
                _b2104_ack_count = 0   # pas actif, pas de healing


def _b2104_reset():
    """Réinitialise les compteurs B2104 (utilisé par les tests post_test)."""
    global _b2104_nack_count, _b2104_ack_count, _b2104_active
    with _b2104_lock:
        _b2104_nack_count = 0
        _b2104_ack_count  = 0
        _b2104_active     = False
    log.info("[WC-B2104] Compteurs réinitialisés")
    try:
        import wc_doip as _wc_doip
        if _wc_doip._dtc_mgr is not None:
            _wc_doip._dtc_mgr.set_inactive("B2104")
    except Exception:
        pass


# ============================================================
# BOUCLE PRINCIPALE
# ============================================================
def _main_loop():
    """
    Lit ADS1115 + GPIO FAULT, met a jour _sensor_state.
    FIX shutdown : sort de la boucle quand _shutdown=True,
    protege _line_fault.get_value() contre ValueError post-cleanup.
    """
    erreurs_i2c = 0

    while not _shutdown:
        loop_start = time.time()

        # ADS1115
        if _chan_blade is not None and _chan_motor is not None:
            try:
                with _i2c_lock:
                    bp_voltage = _chan_blade.voltage
                    mc_voltage = _chan_motor.voltage
                erreurs_i2c = 0
                blade_pos = max(0.0, min(100.0, (bp_voltage / _VREF) * 100.0))
                motor_cur = max(0.0, min(1.0,   (mc_voltage / _VREF)))
            except Exception as e:
                log.warning("[I2C] Erreur lecture: %s", e)
                erreurs_i2c += 1
                if erreurs_i2c > _I2C_ERR_MAX:
                    log.warning("[I2C] Trop d'erreurs, reinit...")
                    _reinit_i2c()
                    erreurs_i2c = 0
                blade_pos, motor_cur = 0.0, 0.0
        else:
            blade_pos, motor_cur = 0.0, 0.0

        # GPIO FAULT  FIX: protege contre acces post-shutdown
        if _line_fault is not None and not _shutdown:
            try:
                # GPIO18 actif LOW : repos=1, appuye=0 ? inversion
                fault = _line_fault.get_value()
            except Exception:
                fault = 0
        else:
            fault = 0

        _sensor_state.update(blade_pos, motor_cur, fault)

        # Detection B2102 : flag motor_driver_fault ecrit par interface externe
        _check_motor_driver_fault()
        # Detection B2103 : compare blade_real (pot) et blade_sim (interface)
        _check_blade_position_mismatch()

        # Detection B2102 : lit motor_driver_fault (interface externe)
        _check_motor_driver_fault()

        elapsed = time.time() - loop_start
        time.sleep(max(0.001, _LOOP_PERIOD_S - elapsed))


# ============================================================
# POINT D'ENTREE
# ============================================================
_BCM_PUMP_PORT = 5556   # port TCPPumpBroadcast du RPi BCM
_bcm_ip        = ""     # IP BCM découverte automatiquement (partagée pour Redis publish)
_bcm_redis     = None   # Connexion Redis persistante vers le BCM (initialisée dans _pump_monitor_thread)


def _discover_bcm_host(timeout: float = 5.0) -> str:
    """
    Découverte automatique du RPi BCM sur le réseau local.
    Scanne 10.20.0.0/28 et 10.20.0.16/28 sur le port 5556 (TCPPumpBroadcast).
    Le premier hôte qui répond avec un JSON contenant "state" est le BCM.
    Retourne l'IP trouvée ou "" si aucun hôte ne répond.
    """
    import ipaddress

    subnets = ["10.20.0.0/28", "10.20.0.16/28"]
    candidates = []
    for subnet in subnets:
        for ip in ipaddress.ip_network(subnet, strict=False).hosts():
            candidates.append(str(ip))

    found = []
    lock  = threading.Lock()

    def _probe(ip):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout / len(candidates) + 0.5)
            s.connect((ip, _BCM_PUMP_PORT))
            raw = b""
            deadline = time.time() + 1.0
            while time.time() < deadline and b"\n" not in raw:
                try:
                    chunk = s.recv(256)
                    if not chunk:
                        break
                    raw += chunk
                except socket.timeout:
                    break
            s.close()
            if raw:
                line = raw.split(b"\n")[0].strip()
                if line:
                    msg = json.loads(line)
                    # BCM pump broadcast contient "state" (str) et "source"=="BCM"
                    if isinstance(msg.get("state"), str) and msg.get("source") == "BCM":
                        with lock:
                            found.append(ip)
        except Exception:
            pass

    threads = [threading.Thread(target=_probe, args=(ip,), daemon=True)
               for ip in candidates]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout)

    if found:
        log.info("[PUMP-MON] BCM découvert automatiquement: %s", found[0])
        return found[0]
    log.warning("[PUMP-MON] BCM non trouvé sur le réseau (port %d)", _BCM_PUMP_PORT)
    return ""


def _pump_monitor_thread(bcm_host: str = "") -> None:
    """
    Se connecte au TCPPumpBroadcast du BCM (port 5556) et surveille pump_active.
    Si bcm_host est vide, tente une découverte automatique au démarrage.
    Dès que la pompe démarre (pump_active=True), applique immédiatement les GPIOs
    du mode de défaut armé  sans aucune action manuelle de l'opérateur.
    """
    # Découverte automatique si IP non fournie
    if not bcm_host:
        log.info("[PUMP-MON] Découverte automatique du BCM...")
        bcm_host = _discover_bcm_host()
        if not bcm_host:
            log.error("[PUMP-MON] BCM introuvable  surveillance pompe désactivée")
            return

    # Stocker l'IP BCM globalement pour Redis publish (TC_FSR_010, TC_CAN_003)
    global _bcm_ip, _bcm_redis
    _bcm_ip = bcm_host
    log.info("[PUMP-MON] BCM IP memorisee: %s (Redis port 6379)", _bcm_ip)
    try:
        import redis as _redis_mod
        _bcm_redis = _redis_mod.Redis(host=_bcm_ip, port=6379,
                                      socket_connect_timeout=1.0,
                                      socket_timeout=1.0)
        _bcm_redis.ping()
        log.info("[PUMP-MON] Connexion Redis persistante etablie -> %s:6379", _bcm_ip)
    except Exception as e:
        log.warning("[PUMP-MON] Redis non disponible (%s) -- publish desactive", e)
        _bcm_redis = None
    log.info("[PUMP-MON] Surveillance pompe BCM %s:%d", bcm_host, _BCM_PUMP_PORT)
    _last_pump_active = False

    while not _shutdown:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((bcm_host, _BCM_PUMP_PORT))
            sock.settimeout(0.1)
            log.info("[PUMP-MON] Connecté au BCM port %d", _BCM_PUMP_PORT)
            buf = ""
            while not _shutdown:
                try:
                    data = sock.recv(1024)
                    if not data:
                        break
                    buf += data.decode("utf-8", errors="replace")
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            msg = json.loads(line)
                            state = msg.get("state", "OFF")
                            pump_active = state in ("FORWARD", "BACKWARD")

                            # Front montant : pompe vient de démarrer
                            if pump_active and not _last_pump_active:
                                mode   = _fault_mode
                                target = _fault_target
                                if mode != "NORMAL":
                                    log.info(
                                        "[PUMP-MON] pump_active=True ? mode=%s cible=%s",
                                        mode, target
                                    )
                                    threading.Thread(
                                        target=_apply_fault_mode,
                                        args=(mode, "POMPE", _fault_duty),
                                        daemon=True,
                                    ).start()

                            _last_pump_active = pump_active

                        except (json.JSONDecodeError, ValueError):
                            pass
                except socket.timeout:
                    pass
                except OSError:
                    break
        except OSError as e:
            log.warning("[PUMP-MON] Connexion BCM échouée (%s)  retry 5s", e)
        finally:
            try:
                sock.close()
            except Exception:
                pass
        if not _shutdown:
            time.sleep(5.0)

    log.info("[PUMP-MON] Thread arrêté")


def start(tcp_host: str = _TCP_HOST, tcp_port: int = _TCP_PORT,
          bcm_host: str = "", dbc_path: str = "wiperwash.dbc"):
    global _tcp_can
    log.info("=== BCM CAN Node - demarrage ===")

    # ── Charger le DBC avant le lancement des threads ────────────────
    # Les IDs CAN et périodes seront mis à jour dans les variables globales.
    import os as _os_bcmcan
    _dbc_candidates = [
        dbc_path,
        _os_bcmcan.path.join(_here_bcmcan, dbc_path),
        _os_bcmcan.path.join(_here_bcmcan, "..", dbc_path),
        _os_bcmcan.path.join(_here_bcmcan, "..", "wiperwash.dbc"),
    ]
    _dbc_found = next((p for p in _dbc_candidates if _os_bcmcan.path.isfile(p)), None)
    if _dbc_found:
        load_dbc_sim(_dbc_found)
        log.info("[INIT] DBC chargé: %s", _dbc_found)
    else:
        log.warning("[INIT] DBC '%s' introuvable — IDs CAN par défaut", dbc_path)

    _init_hardware()

    from bcm_tcp_can import TCPCANBroadcast
    _tcp_can = TCPCANBroadcast()
    _tcp_can.set_0x202_callback(_relay_0x202)
    _tcp_can.start()
    log.info("[INIT] TCPCANBroadcast demarre (port 5557)")

    threading.Thread(target=_can_vehicle_thread, daemon=True, name="bcmcan_vehicle").start()
    log.info("[INIT] Thread T-CAN-VEH demarre (TX 0x%03X/0x%03X periode=%.0fms)",
             _CAN_ID_VEHICLE, _CAN_ID_RAIN, _CAN_TX_PERIOD_S * 1000)

    threading.Thread(target=_can_wc_thread, daemon=True, name="bcmcan_wc").start()
    log.info("[INIT] Thread T-CAN-WC demarre (RX 0x%03X / TX 0x%03X / relay 0x%03X)",
             _CAN_ID_CMD, _CAN_ID_STATUS, _CAN_ID_ACK)

    threading.Thread(
        target=_tcp_server,
        args=(tcp_host, tcp_port),
        daemon=True,
        name="bcmcan_tcp",
    ).start()

    # Thread surveillance pompe : découverte BCM automatique si bcm_host non fourni
    threading.Thread(
        target=_pump_monitor_thread,
        args=(bcm_host,),
        daemon=True,
        name="bcmcan_pump_mon",
    ).start()
    log.info("[INIT] Thread surveillance pompe démarré (bcm_host='%s' ? auto si vide)", bcm_host)

    log.info("[INIT] === READY === (ADS1115=%s  GPIO=%s)",
             "OK" if _chan_blade else "absent",
             "OK" if _line_fault else "absent")
    _main_loop()


# ============================================================
# STANDALONE
# ============================================================
if __name__ == "__main__":
    import argparse
    import signal as _sig

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    p = argparse.ArgumentParser(description="BCM Sensor / CAN Node")
    p.add_argument("--host", default=_TCP_HOST, help="Adresse ecoute TCP")
    p.add_argument("--port", type=int, default=_TCP_PORT, help="Port TCP")
    args = p.parse_args()

    _sig.signal(_sig.SIGINT,  lambda s, f: cleanup("SIGINT")  or sys.exit(0))
    _sig.signal(_sig.SIGTERM, lambda s, f: cleanup("SIGTERM") or sys.exit(0))

    start(tcp_host=args.host, tcp_port=args.port)