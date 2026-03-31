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

_ADS_ADDR      = 0x48
_ADS_GAIN      = 1
_ADS_CHAN_BLADE = 2
_ADS_CHAN_MOTOR = 1
_VREF          = 3.3
_I2C_ERR_MAX   = 5

_shutdown = False   # FIX: flag arret propre pour _main_loop

log = logging.getLogger("BCM")


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
    blade_position: float = 0.0
    motor_current:  float = 0.0
    fault_status:   int   = 0
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
_vehicle_state = VehicleState()
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
        log.warning("[I2C] Reinit echec: %s — blade=0 motor=0", e)
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
            log.info("[INIT] gpiod OK — fault line=%d", _PIN_FAULT)
        except Exception as e:
            log.warning("[INIT] gpiod echec (%s) — fault_status=0", e)
    else:
        log.warning("[INIT] gpiod absent — fault_status=0")

    # ADS1115
    if _HW_I2C:
        try:
            log.info("[INIT] I2C + ADS1115...")
            _i2c_bus, _ads, _chan_blade, _chan_motor = _init_i2c()
            log.info("[INIT] ADS1115 OK — canal_blade=A%d canal_motor=A%d",
                     _ADS_CHAN_BLADE, _ADS_CHAN_MOTOR)
        except Exception as e:
            log.warning("[INIT] ADS1115 indisponible (%s) — blade=0.0 motor=0.0", e)
    else:
        log.warning("[INIT] bibliotheques I2C absentes — blade=0.0 motor=0.0")


def cleanup(reason: str = ""):
    global _shutdown
    _shutdown = True   # FIX: stoppe _main_loop avant de fermer gpiod
    log.info("[SHUTDOWN] BCM arret: %s", reason)
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


def _handle_vehicle_status(obj: dict):
    raw_ign = obj.get("ignition_status", "OFF")
    ign = _IGN_STR_MAP.get(raw_ign.upper(), 0) if isinstance(raw_ign, str) \
          else max(0, min(2, int(raw_ign)))
    rev       = 1 if obj.get("reverse_gear", 0) else 0
    speed_kmh = float(obj.get("vehicle_speed", 0.0))
    speed_raw = max(0, min(65535, int(round(speed_kmh * 10))))
    _vehicle_state.update(ign, rev, speed_raw)
    log.info("[TCP] VehicleStatus: IGN=%d REV=%d SPD=%.1f km/h", ign, rev, speed_kmh)


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
                            else:
                                log.warning("[TEST] test_cmd inconnu: %s", tc)
                        elif any(k in obj for k in ("ignition_status", "reverse_gear", "vehicle_speed")):
                            _handle_vehicle_status(obj)
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
# CAN
# ============================================================
def _build_0x300() -> bytes:
    ign, rev, spd = _vehicle_state.snapshot()
    return bytes([ign & 0xFF, rev & 0xFF, (spd >> 8) & 0xFF, spd & 0xFF,
                  0x00, 0x00, 0x00, 0x00])


def _build_0x301() -> bytes:
    intensity, ok = _rain_state.snapshot()
    return bytes([intensity & 0xFF, 0x00 if ok else 0x01,
                  0x00, 0x00, 0x00, 0x00, 0x00, 0x00])


def _crc_rx(data8: bytes) -> int:
    return (data8[0] ^ data8[1] ^ data8[2]) & 0xFF


def _crc_tx(payload7: bytes) -> int:
    crc = 0
    for b in payload7:
        crc ^= b
    return crc & 0xFF


def _build_0x201(alive: int) -> bytes:
    bp, mc, fs        = _sensor_state.snapshot()
    mode, speed, _, _ = _wiper_cmd.snapshot()
    blade_byte = max(0, min(100, int(round(bp))))
    mc_raw     = max(0, min(65535, int(round(mc * 10))))
    payload7   = bytes([mode & 0xFF, speed & 0xFF, blade_byte,
                        (mc_raw >> 8) & 0xFF, mc_raw & 0xFF,
                        int(fs) & 0xFF, alive & 0xFF])
    return payload7 + bytes([_crc_tx(payload7)])


def _send_can(sock: socket.socket, can_id: int, payload: bytes):
    sock.send(struct.pack(_CAN_FMT, can_id, 8, payload))


def _relay_0x202(data4: bytes) -> None:
    try:
        _ack_relay_queue.put_nowait(data4)
        log.info("[ACK] 0x202 -> file CAN  Ack=%d Err=%d Alive=%d",
                 data4[0], data4[1], data4[2])
    except _queue_mod.Full:
        log.warning("[ACK] File 0x202 pleine - trame ignoree")


_CAN_TIMEOUT_WC    = 2.0   # B2101 : timeout si pas de 0x200 pendant 2s
_t_last_0x200      = 0.0   # timestamp derniere trame 0x200 valide recue
_can_timeout_b2011 = False # etat courant B2101


def _check_can_timeout_b2011():
    """Detecte B2101 (WC Internal Failure - CAN Timeout) et met a jour wc_state."""
    global _can_timeout_b2011
    if _t_last_0x200 == 0.0:
        return
    elapsed = time.time() - _t_last_0x200
    if elapsed > _CAN_TIMEOUT_WC and not _can_timeout_b2011:
        _can_timeout_b2011 = True
        log.warning("[WC-B2101] CAN Timeout BCM %.1fs > %.1fs -> B2101",
                    elapsed, _CAN_TIMEOUT_WC)
        try:
            import wc_doip as _wc_doip
            if _wc_doip._dtc_mgr:
                _wc_doip._dtc_mgr.set_active("B2101",
                                             _wc_doip.wc_state.make_snapshot())
            _wc_doip.wc_state.update_from_bcmcan(
                mode=0, speed=0, blade_moving=False,
                motor_current_a=0.0, fault_status=0, can_timeout=True)
        except Exception:
            pass
    elif elapsed <= _CAN_TIMEOUT_WC and _can_timeout_b2011:
        _can_timeout_b2011 = False
        try:
            import wc_doip as _wc_doip
            if _wc_doip._dtc_mgr:
                _wc_doip._dtc_mgr.set_inactive("B2101")
        except Exception:
            pass


def _can_rxtx_thread():
    global _t_last_0x200
    last_300 = 0.0
    last_301 = 0.0
    last_timeout_check = 0.0

    while True:
        sock = None
        try:
            sock = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
            sock.setsockopt(socket.SOL_SOCKET, _SO_TIMESTAMP, 1)
            sock.bind((_CAN_IFACE,))
            sock.settimeout(0.005)
            log.info("[CAN] Socket ouvert sur %s", _CAN_IFACE)
            last_300 = last_301 = time.time()

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

                # Relais 0x202
                while True:
                    try:
                        data4 = _ack_relay_queue.get_nowait()
                    except _queue_mod.Empty:
                        break
                    try:
                        _send_can(sock, _CAN_ID_ACK, (data4 + bytes(4))[:8])
                        log.info("[CAN] TX 0x202 Ack=%d Err=%d Alive=0x%02X",
                                 data4[0], data4[1], data4[2])
                    except OSError as e:
                        log.error("[CAN] Erreur TX 0x202: %s", e)

                # Surveillance CAN timeout B2101 (toutes les 500ms)
                if now - last_timeout_check >= 0.500:
                    _check_can_timeout_b2011()
                    last_timeout_check = now

                # TX 0x300
                if now - last_300 >= _CAN_TX_PERIOD_S:
                    try:
                        fd = _build_0x300()
                        _send_can(sock, _CAN_ID_VEHICLE, fd)
                        if _tcp_can:
                            _tcp_can.on_tx_0x300(fd, t_kernel=time.monotonic())
                    except OSError as e:
                        log.error("[CAN] Erreur TX 0x300: %s", e)
                    last_300 = now

                # TX 0x301
                if now - last_301 >= _CAN_TX_PERIOD_S:
                    try:
                        fd = _build_0x301()
                        _send_can(sock, _CAN_ID_RAIN, fd)
                        if _tcp_can:
                            _tcp_can.on_tx_0x301(fd, t_kernel=time.monotonic())
                    except OSError as e:
                        log.error("[CAN] Erreur TX 0x301: %s", e)
                    last_301 = now

                # RX 0x200
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
                    log.warning("[CAN] 0x200 CRC KO recu=0x%02X attendu=0x%02X",
                                d[3], _crc_rx(d))
                    # WC DTC B2101 : WC Internal Failure (CAN Checksum Error)
                    try:
                        import wc_doip as _wc_doip
                        _wc_doip._dtc_mgr and _wc_doip._dtc_mgr.set_active(
                            "B2101", _wc_doip.wc_state.make_snapshot())
                    except Exception:
                        pass
                    continue

                mode  = d[0] & 0x0F
                speed = (d[0] >> 4) & 0x0F
                wash  = d[1] & 0x03
                alive = d[2]
                _wiper_cmd.update(mode, speed, wash, alive)
                _t_last_0x200 = time.time()   # mise a jour timestamp pour B2101
                log.info("[CAN] RX 0x200 Mode=%d Speed=%d Wash=%d Alive=%d",
                         mode, speed, wash, alive)

                # Mise a jour etat WC pour DoIP/DTC (lecture capteurs existants)
                try:
                    import wc_doip as _wc_doip
                    bp, mc, fs = _sensor_state.snapshot()
                    _wc_doip.wc_state.update_from_bcmcan(
                        mode         = mode,
                        speed        = speed,
                        blade_moving = speed > 0,
                        motor_current_a = mc,
                        fault_status = int(fs),
                        can_timeout  = False,
                    )
                except Exception:
                    pass

                if _tcp_can:
                    _tcp_can.on_rx_0x200(d, t_kernel=t_kernel_rx)

                try:
                    f201 = _build_0x201(alive)
                    _send_can(sock, _CAN_ID_STATUS, f201)
                    if _tcp_can:
                        _tcp_can.on_tx_0x201(f201, t_kernel=time.monotonic())
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

        # GPIO FAULT — FIX: protege contre acces post-shutdown
        if _line_fault is not None and not _shutdown:
            try:
                # GPIO19 actif LOW : repos=1, appuye=0 → inversion
                fault = _line_fault.get_value()
            except Exception:
                fault = 0
        else:
            fault = 0

        _sensor_state.update(blade_pos, motor_cur, fault)

        elapsed = time.time() - loop_start
        time.sleep(max(0.001, _LOOP_PERIOD_S - elapsed))


# ============================================================
# POINT D'ENTREE
# ============================================================
def start(tcp_host: str = _TCP_HOST, tcp_port: int = _TCP_PORT):
    global _tcp_can
    log.info("=== BCM CAN Node - demarrage ===")

    _init_hardware()

    from bcm_tcp_can import TCPCANBroadcast
    _tcp_can = TCPCANBroadcast()
    _tcp_can.set_0x202_callback(_relay_0x202)
    _tcp_can.start()
    log.info("[INIT] TCPCANBroadcast demarre (port 5557)")

    threading.Thread(target=_can_rxtx_thread, daemon=True, name="bcmcan_can").start()
    log.info("[INIT] Thread CAN demarre")

    threading.Thread(
        target=_tcp_server,
        args=(tcp_host, tcp_port),
        daemon=True,
        name="bcmcan_tcp",
    ).start()

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