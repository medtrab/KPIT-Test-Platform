#!/usr/bin/env python3
"""
bcmcan.py - BCM Sensor / CAN Node
====================================
Lit les capteurs physiques (ADS1115 I2C, GPIO FAULT), recoit les donnees
Vehicle_Status et RainSensorData depuis un client TCP, et communique
sur le bus CAN socketcan (can0).

USAGE STANDALONE:
    python3 bcmcan.py [--host 0.0.0.0] [--port 5556]

USAGE MODULE (depuis main.py):
    import bcmcan
    bcmcan.start(tcp_host="0.0.0.0", tcp_port=5556)   # bloquant

HARDWARE:
    I2C  : ADS1115 @ 0x48  (canal 2 = position lame, canal 1 = courant moteur)
    GPIO : broche 19 = signal FAULT (entree avec pull-up interne)
    UART : GPIO 17 TX bit-bang pigpio (debug), GPIO 27 RX flush
    CAN  : interface socketcan can0

CAN FRAMES:
    RX 0x200 - Wiper_Cmd    : mode (nibble bas B0), vitesse (nibble haut B0),
                               wash_request (B1[1:0]), alive_counter (B2), CRC XOR (B3)
    TX 0x201 - Wiper_Status : mode, speed, blade_pos, current_hi, current_lo,
                               fault, alive_echo, CRC XOR
    TX 0x300 - Vehicle_Status  (periodique 200ms) : ignition, reverse, speed_hi, speed_lo
    TX 0x301 - RainSensorData  (periodique 200ms) : intensity, status

TCP SERVER (port 5556 par defaut):
    RX JSON : {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 50.0}
              {"rain_intensity": 75, "sensor_status": "OK"}
    TX JSON : etat capteurs courant a la connexion
"""

import time
import os
import signal
import sys
import gpiod
import board
import busio
import pigpio
import json
import socket
import threading
import struct
import logging
from dataclasses import dataclass, field
from enum import IntEnum

import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

# ============================================================
# CONFIGURATION PAR DEFAUT  (surchargeable via start())
# ============================================================
_TCP_HOST    = "0.0.0.0"
_TCP_PORT    = 5556          # port different de crslin (5555) pour cohabitation

# GPIO / UART
_CHIP_NAME   = "gpiochip0"
_PIN_FAULT   = 19
_UART_TX     = 17
_UART_RX     = 27
_UART_BAUD   = 9600

# CAN socketcan
_CAN_IFACE      = "can0"
_CAN_ID_CMD     = 0x200
_CAN_ID_STATUS  = 0x201
_CAN_ID_VEHICLE = 0x300
_CAN_ID_RAIN    = 0x301
_CAN_FMT        = "=IB3x8s"          # struct : CAN id (4B) + dlc (1B) + 3B pad + 8B data
_CAN_SIZE       = struct.calcsize(_CAN_FMT)

# Timings
_LOOP_PERIOD_S   = 0.200   # periode boucle principale
_CAN_TX_PERIOD_S = 0.200   # periode envoi 0x300 et 0x301
_CAN_RETRY_S     = 2       # delai retry apres echec socket CAN
_UART_FLUSH_S    = 0.1     # periode vidage buffer RX UART

# ADS1115
_ADS_ADDR       = 0x48
_ADS_GAIN       = 1
_ADS_CHAN_BLADE  = 2        # canal position lame  (0-3.3V -> 0-100%)
_ADS_CHAN_MOTOR  = 1        # canal courant moteur (0-3.3V -> 0-1A)
_VREF           = 3.3       # tension de reference ADC

# Seuil erreurs I2C avant reinitialisation
_I2C_ERR_MAX    = 5

# ============================================================
# LOGGING
# ============================================================
log = logging.getLogger("BCM")


# ============================================================
# ENUM IGNITION
# Mappe les etats contact cle vers des entiers CAN (0/1/2).
# ============================================================
class Ignition(IntEnum):
    OFF = 0
    ACC = 1
    ON  = 2

    @classmethod
    def from_value(cls, v) -> "Ignition":
        """Construit depuis une chaine ("OFF"/"ACC"/"ON") ou un entier."""
        if isinstance(v, str):
            return cls[v.upper()]
        return cls(max(0, min(2, int(v))))


# ============================================================
# ETATS PARTAGES  (dataclasses thread-safe avec lock interne)
# Chaque dataclass encapsule un sous-ensemble coherent de l'etat
# et expose update() / snapshot() pour un acces atomique.
# ============================================================

@dataclass
class SensorState:
    """Mesures lues par la boucle principale depuis l'ADS1115 et le GPIO."""
    blade_position: float = 0.0   # % (0-100)
    motor_current:  float = 0.0   # A (0-1)
    fault_status:   int   = 0     # 0 = OK, 1 = FAULT
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(self, blade: float, current: float, fault: int):
        with self._lock:
            self.blade_position = round(blade, 1)
            self.motor_current  = round(current, 3)
            self.fault_status   = fault

    def snapshot(self):
        with self._lock:
            return self.blade_position, self.motor_current, self.fault_status


@dataclass
class VehicleState:
    """Etat vehicule recu depuis TCP (Vehicle_Status, CAN 0x300)."""
    ignition: int = 0     # valeur Ignition enum (0/1/2)
    reverse:  int = 0     # 0 = marche avant, 1 = marche arriere
    speed:    int = 0     # vitesse en 1/10 km/h (entier 16 bits)
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
    """Etat capteur pluie recu depuis TCP (RainSensorData, CAN 0x301)."""
    intensity: int  = 0     # 0-100%
    sensor_ok: bool = True  # True = capteur operationnel
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
    """Derniere commande essuie-glace recue sur CAN 0x200."""
    mode:     int = 0   # mode essuie-glace (nibble bas B0)
    speed:    int = 0   # niveau de vitesse  (nibble haut B0)
    wash:     int = 0   # demande de lavage  (B1[1:0])
    alive_rx: int = 0   # compteur alive recu du maitre
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


# Instances uniques des etats (initialisees dans start())
_sensor_state  = SensorState()
_vehicle_state = VehicleState()
_rain_state    = RainState()
_wiper_cmd     = WiperCmd()

# ============================================================
# OBJETS MATERIELS  (initialises dans _init_hardware())
# Declares None ici pour permettre l'import du module sans
# hardware disponible (utile pour les tests unitaires).
# ============================================================
_chip       = None   # gpiod.Chip
_line_fault = None   # gpiod.Line
_pi         = None   # pigpio.pi
_i2c_bus    = None   # busio.I2C
_ads        = None   # ADS.ADS1115
_chan_blade  = None  # AnalogIn canal position lame
_chan_motor  = None  # AnalogIn canal courant moteur
_i2c_lock   = threading.Lock()
_uart_lock  = threading.Lock()

# ============================================================
# INITIALISATION DU MATERIEL
# ============================================================
def _init_i2c():
    """Cree et retourne les objets I2C/ADS1115 (appele aussi lors de la reinit)."""
    i2c = busio.I2C(board.SCL, board.SDA)
    ads = ADS.ADS1115(i2c, address=_ADS_ADDR)
    ads.gain = _ADS_GAIN
    return i2c, ads, AnalogIn(ads, _ADS_CHAN_BLADE), AnalogIn(ads, _ADS_CHAN_MOTOR)


def _reinit_i2c():
    """
    Reinitialise le bus I2C apres un nombre excessif d'erreurs de lecture.
    Le mot-cle global est necessaire ici car on reassigne des variables module.
    """
    global _i2c_bus, _ads, _chan_blade, _chan_motor
    try:
        _i2c_bus.deinit()
    except OSError:
        pass
    time.sleep(0.1)
    _i2c_bus, _ads, _chan_blade, _chan_motor = _init_i2c()
    log.info("[I2C] Reinit OK")


def _init_hardware():
    """
    Initialise tous les peripheriques materiels dans l'ordre :
    GPIO (gpiod) -> UART (pigpio) -> I2C + ADS1115.
    Appele une seule fois au demarrage depuis start().
    """
    global _chip, _line_fault, _pi, _i2c_bus, _ads, _chan_blade, _chan_motor

    # --- GPIO ---
    log.info("[INIT] gpiod chip...")
    _chip = gpiod.Chip(_CHIP_NAME)
    line  = _chip.get_line(_PIN_FAULT)
    try:
        line.request(consumer="fault", type=gpiod.LINE_REQ_DIR_IN,
                     flags=gpiod.LINE_REQ_FLAG_BIAS_PULL_UP)
    except OSError:
        line.request(consumer="fault", type=gpiod.LINE_REQ_DIR_IN)
    _line_fault = line

    # --- pigpio (UART bit-bang) ---
    log.info("[INIT] pigpio...")
    _pi = pigpio.pi()
    if not _pi.connected:
        log.error("[INIT] pigpiod non actif - arret")
        sys.exit(1)
    _pi.set_mode(_UART_TX, pigpio.OUTPUT)
    _pi.bb_serial_read_open(_UART_RX, _UART_BAUD)

    # --- I2C + ADS1115 ---
    log.info("[INIT] I2C + ADS1115...")
    _i2c_bus, _ads, _chan_blade, _chan_motor = _init_i2c()
    log.info("[INIT] I2C OK")


# ============================================================
# NETTOYAGE  (appele sur SIGINT / SIGTERM depuis main.py)
# ============================================================
def cleanup(reason: str = ""):
    """
    Libere toutes les ressources materielles dans l'ordre inverse
    de leur initialisation. Appele par main.py sur signal systeme.
    """
    log.info("[SHUTDOWN] BCM arret: %s", reason)
    for action in (
        lambda: _line_fault.release(),
        lambda: _chip.close(),
        lambda: _i2c_bus.deinit(),
        lambda: _pi.bb_serial_read_close(_UART_RX),
        lambda: _pi.stop(),
        lambda: os.system("ip link set " + _CAN_IFACE + " down 2>/dev/null"),
    ):
        try:
            action()
        except Exception:
            pass


# ============================================================
# TCP SERVER  (reception Vehicle_Status et RainSensorData)
# ============================================================
_tcp_clients      : list = []
_tcp_clients_lock = threading.Lock()

# Correspondance chaine ignition -> entier pour _handle_vehicle_status
_IGN_STR_MAP = {"OFF": 0, "ACC": 1, "ON": 2}


def _handle_vehicle_status(obj: dict):
    """
    Extrait ignition / reverse / speed depuis un message JSON TCP
    et met a jour VehicleState.
    La vitesse est stockee en 1/10 km/h (entier 16 bits) pour la trame CAN.
    """
    raw_ign = obj.get("ignition_status", "OFF")
    ign = _IGN_STR_MAP.get(raw_ign.upper(), 0) if isinstance(raw_ign, str) \
          else max(0, min(2, int(raw_ign)))
    rev       = 1 if obj.get("reverse_gear", 0) else 0
    speed_kmh = float(obj.get("vehicle_speed", 0.0))
    speed_raw = max(0, min(65535, int(round(speed_kmh * 10))))
    _vehicle_state.update(ign, rev, speed_raw)
    log.info("[TCP] VehicleStatus: IGN=%d REV=%d SPD=%.1f km/h", ign, rev, speed_kmh)


def _handle_rain_sensor(obj: dict):
    """
    Extrait intensity / sensor_status depuis un message JSON TCP
    et met a jour RainState.
    """
    intensity  = max(0, min(100, int(round(float(obj.get("rain_intensity", 0))))))
    status_raw = obj.get("sensor_status", "OK")
    ok = (status_raw.upper() == "OK") if isinstance(status_raw, str) else bool(status_raw)
    _rain_state.update(intensity, ok)
    log.info("[TCP] RainSensor: intensity=%d%% status=%s", intensity, "OK" if ok else "ERROR")


def _tcp_client_handler(conn: socket.socket, addr):
    """
    Thread dedie a un client TCP.
    Envoie l'etat capteurs courant a la connexion, puis ecoute
    les mises a jour JSON (Vehicle_Status et/ou RainSensorData).
    """
    log.info("[TCP] Client connecte: %s", addr)
    with _tcp_clients_lock:
        _tcp_clients.append(conn)

    # Envoi de l'etat initial (instantane courant des capteurs)
    bp, mc, fs = _sensor_state.snapshot()
    initial = json.dumps({"front": {
        "blade_position": bp,
        "motor_current":  mc,
        "fault_status":   fs,
    }})
    try:
        conn.sendall((initial + "\n").encode("utf-8"))
    except OSError:
        pass

    buf = ""
    try:
        while True:
            try:
                raw = conn.recv(1024).decode("utf-8", errors="replace")
                if not raw:
                    break   # connexion fermee par le client
                buf += raw
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        # Un meme message peut contenir les deux types de donnees
                        if any(k in obj for k in ("ignition_status", "reverse_gear", "vehicle_speed")):
                            _handle_vehicle_status(obj)
                        if any(k in obj for k in ("rain_intensity", "sensor_status")):
                            _handle_rain_sensor(obj)
                    except (json.JSONDecodeError, ValueError):
                        pass
            except socket.timeout:
                pass   # timeout non bloquant, on reboucle
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
    """Boucle d'acceptation TCP. Lance un thread par client."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((tcp_host, tcp_port))
    srv.listen(5)
    log.info("[TCP] Serveur en ecoute sur %s:%d", tcp_host, tcp_port)
    while True:
        try:
            conn, addr = srv.accept()
            conn.settimeout(0.1)
            threading.Thread(
                target=_tcp_client_handler,
                args=(conn, addr),
                daemon=True,
                name=f"bcmcan_client_{addr[1]}",
            ).start()
        except OSError as e:
            log.error("[TCP] Erreur accept: %s", e)


# ============================================================
# UART DEBUG (bit-bang via pigpio)
# ============================================================
def _send_uart(frame: str):
    """
    Envoie une trame de debug sur le GPIO UART TX (bit-bang pigpio).
    Format exemple : "BP:067,MC:012,FS:0"
    Non bloquant : une exception n'arrete pas la boucle principale.
    """
    try:
        with _uart_lock:
            _pi.wave_clear()
            _pi.wave_add_serial(_UART_TX, _UART_BAUD, frame.encode())
            wid = _pi.wave_create()
            _pi.wave_send_once(wid)
            while _pi.wave_tx_busy():
                time.sleep(0.001)
            _pi.wave_delete(wid)
    except Exception as e:
        log.warning("[UART] Erreur envoi: %s", e)


def _uart_rx_flush():
    """
    Thread de vidage continu du buffer RX UART.
    Evite le debordement du buffer interne pigpio sur GPIO 27.
    """
    while True:
        try:
            _pi.bb_serial_read(_UART_RX)
        except Exception:
            pass
        time.sleep(_UART_FLUSH_S)


# ============================================================
# CAN - CONSTRUCTION DES TRAMES
# ============================================================
def _build_0x300() -> bytes:
    """
    Construit la trame Vehicle_Status (8 octets) pour CAN 0x300.
    Format : [ignition, reverse, speed_hi, speed_lo, 0, 0, 0, 0]
    """
    ign, rev, spd = _vehicle_state.snapshot()
    return bytes([
        ign & 0xFF,
        rev & 0xFF,
        (spd >> 8) & 0xFF,
        spd        & 0xFF,
        0x00, 0x00, 0x00, 0x00,
    ])


def _build_0x301() -> bytes:
    """
    Construit la trame RainSensorData (8 octets) pour CAN 0x301.
    Format : [intensity, status(0=OK/1=ERR), 0, 0, 0, 0, 0, 0]
    """
    intensity, ok = _rain_state.snapshot()
    return bytes([
        intensity & 0xFF,
        0x00 if ok else 0x01,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    ])


def _crc_rx(data8: bytes) -> int:
    """
    Checksum XOR pour verification de la trame recue 0x200.
    Porte sur les 3 premiers octets de donnees utiles.
    """
    return (data8[0] ^ data8[1] ^ data8[2]) & 0xFF


def _crc_tx(payload7: bytes) -> int:
    """CRC XOR sur les 7 premiers octets pour la trame emise 0x201."""
    crc = 0
    for b in payload7:
        crc ^= b
    return crc & 0xFF


def _build_0x201(alive: int) -> bytes:
    """
    Construit la trame Wiper_Status (8 octets) pour CAN 0x201.
    Format : [mode, speed, blade%, current_hi, current_lo, fault, alive, CRC]
    Le courant moteur est encode en 1/10 A (entier 16 bits little-endian hi/lo).
    Le CRC XOR porte sur les 7 premiers octets.
    """
    bp, mc, fs      = _sensor_state.snapshot()
    mode, speed, _, _ = _wiper_cmd.snapshot()

    blade_byte = max(0, min(100, int(round(bp))))
    mc_raw     = max(0, min(65535, int(round(mc * 10))))
    payload7   = bytes([
        mode  & 0xFF,
        speed & 0xFF,
        blade_byte,
        (mc_raw >> 8) & 0xFF,
        mc_raw        & 0xFF,
        int(fs)       & 0xFF,
        alive         & 0xFF,
    ])
    return payload7 + bytes([_crc_tx(payload7)])


def _send_can(sock: socket.socket, can_id: int, payload: bytes):
    """Emballe payload dans une trame socketcan et l'envoie."""
    frame = struct.pack(_CAN_FMT, can_id, 8, payload)
    sock.send(frame)


# ============================================================
# THREAD CAN BIDIRECTIONNEL
# ============================================================
def _can_rxtx_thread():
    """
    Thread de communication CAN.

    Boucle externe : gere les reconnexions automatiques apres echec socket.
    Boucle interne :
      - Envoie 0x300 (Vehicle_Status) toutes les 200ms
      - Envoie 0x301 (RainSensorData) toutes les 200ms
      - Recoit 0x200 (Wiper_Cmd) avec verification CRC
        et repond immediatement par 0x201 (Wiper_Status)
    """
    last_300 = 0.0
    last_301 = 0.0

    while True:
        sock = None
        try:
            sock = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
            sock.bind((_CAN_IFACE,))
            sock.settimeout(0.005)   # timeout court pour ne pas bloquer les envois periodiques
            log.info("[CAN] Socket ouvert sur %s", _CAN_IFACE)
            last_300 = last_301 = time.time()

            while True:
                now = time.time()

                # --- Envoi periodique 0x300 (Vehicle_Status) ---
                if now - last_300 >= _CAN_TX_PERIOD_S:
                    try:
                        _send_can(sock, _CAN_ID_VEHICLE, _build_0x300())
                    except OSError as e:
                        log.error("[CAN] Erreur TX 0x300: %s", e)
                    last_300 = now

                # --- Envoi periodique 0x301 (RainSensorData) ---
                if now - last_301 >= _CAN_TX_PERIOD_S:
                    try:
                        _send_can(sock, _CAN_ID_RAIN, _build_0x301())
                    except OSError as e:
                        log.error("[CAN] Erreur TX 0x301: %s", e)
                    last_301 = now

                # --- Reception 0x200 (Wiper_Cmd) ---
                try:
                    frame = sock.recv(_CAN_SIZE)
                except socket.timeout:
                    continue   # pas de trame disponible, on reboucle
                except OSError:
                    break      # erreur socket -> reconnexion

                if len(frame) < _CAN_SIZE:
                    continue

                can_id, dlc, data = struct.unpack(_CAN_FMT, frame)
                can_id &= 0x1FFFFFFF   # masque bits d'extension EFF/RTR/ERR

                # Ignorer les trames qui ne nous sont pas destinees
                if can_id != _CAN_ID_CMD or dlc < 4:
                    continue

                # Verification CRC de la trame recue
                d = data[:8]
                if d[3] != _crc_rx(d):
                    log.warning("[CAN] 0x200 CRC KO recu=0x%02X attendu=0x%02X",
                                d[3], _crc_rx(d))
                    continue

                # Extraction des champs de commande
                mode  = d[0] & 0x0F
                speed = (d[0] >> 4) & 0x0F
                wash  = d[1] & 0x03
                alive = d[2]
                _wiper_cmd.update(mode, speed, wash, alive)
                log.info("[CAN] RX 0x200 Mode=%d Speed=%d Wash=%d Alive=%d",
                         mode, speed, wash, alive)

                # Reponse immediate avec l'etat complet du noeud (0x201)
                try:
                    _send_can(sock, _CAN_ID_STATUS, _build_0x201(alive))
                except OSError as e:
                    log.error("[CAN] Erreur TX 0x201: %s", e)

        except Exception as e:
            log.error("[CAN] Erreur socket: %s -> retry dans %ds", e, _CAN_RETRY_S)
        finally:
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
        time.sleep(_CAN_RETRY_S)


# ============================================================
# BOUCLE PRINCIPALE  (200ms)
# ============================================================
def _main_loop():
    """
    Boucle de lecture des capteurs a 200ms.
    1. Lecture ADS1115 (position lame + courant moteur)
    2. Lecture GPIO FAULT
    3. Mise a jour SensorState
    4. Envoi trame UART debug
    5. Respect de la periode 200ms (sleep adaptatif)
    En cas d'erreur I2C repetee, reinitialise le bus.
    """
    erreurs_i2c = 0

    while True:
        loop_start = time.time()

        # Lecture ADS1115 (protegee par i2c_lock partage avec _reinit_i2c)
        try:
            with _i2c_lock:
                bp_voltage = _chan_blade.voltage
                mc_voltage = _chan_motor.voltage
            erreurs_i2c = 0
        except Exception as e:
            log.warning("[I2C] Erreur lecture: %s", e)
            erreurs_i2c += 1
            if erreurs_i2c > _I2C_ERR_MAX:
                log.warning("[I2C] Trop d'erreurs, reinit...")
                _reinit_i2c()
                erreurs_i2c = 0
            time.sleep(0.05)
            continue

        # Conversion tension -> grandeur physique
        blade_pos = max(0.0, min(100.0, (bp_voltage / _VREF) * 100.0))
        motor_cur = max(0.0, min(1.0,   (mc_voltage / _VREF) * 1.0))
        fault     = _line_fault.get_value()

        # Mise a jour de l'etat partage (lu par _build_0x201 dans le thread CAN)
        _sensor_state.update(blade_pos, motor_cur, fault)

        # Trame debug UART (ex : "BP:067,MC:012,FS:0")
        _send_uart(f"BP:{int(blade_pos):03d},MC:{int(motor_cur * 1000):03d},FS:{fault}\n")

        # Attente pour completer la periode de 200ms
        elapsed = time.time() - loop_start
        time.sleep(max(0.001, _LOOP_PERIOD_S - elapsed))


# ============================================================
# POINT D'ENTREE DU MODULE
# ============================================================
def start(tcp_host: str = _TCP_HOST, tcp_port: int = _TCP_PORT):
    """
    Initialise le noeud BCM/CAN et demarre tous les threads.

    Sequence de demarrage :
      1. _init_hardware()       : GPIO, pigpio, I2C/ADS1115
      2. Thread CAN             : reception/emission socketcan (daemon)
      3. Thread UART flush      : vidage buffer RX pigpio (daemon)
      4. Thread TCP server      : accepte les clients (daemon)
      5. _main_loop()           : boucle principale 200ms (BLOQUANT)

    Le caractere bloquant de _main_loop() signifie que start() ne retourne
    pas. Dans main.py, cette fonction est lancee dans un thread dedie.

    Parametres :
        tcp_host : adresse d'ecoute TCP (defaut "0.0.0.0")
        tcp_port : port TCP (defaut 5556)
    """
    log.info("=== BCM CAN Node - demarrage ===")

    _init_hardware()

    threading.Thread(target=_can_rxtx_thread, daemon=True, name="bcmcan_can").start()
    log.info("[INIT] Thread CAN demarre")

    threading.Thread(target=_uart_rx_flush, daemon=True, name="bcmcan_uart_flush").start()

    threading.Thread(
        target=_tcp_server,
        args=(tcp_host, tcp_port),
        daemon=True,
        name="bcmcan_tcp",
    ).start()

    log.info("[INIT] === READY ===")
    _main_loop()   # bloquant


# ============================================================
# USAGE STANDALONE  (python3 bcmcan.py)
# ============================================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    p = argparse.ArgumentParser(description="BCM Sensor / CAN Node")
    p.add_argument("--host", default=_TCP_HOST, help="Adresse ecoute TCP")
    p.add_argument("--port", type=int, default=_TCP_PORT, help="Port TCP")
    args = p.parse_args()

    import signal as _sig
    _sig.signal(_sig.SIGINT,  lambda s, f: cleanup("SIGINT")  or sys.exit(0))
    _sig.signal(_sig.SIGTERM, lambda s, f: cleanup("SIGTERM") or sys.exit(0))

    start(tcp_host=args.host, tcp_port=args.port)
