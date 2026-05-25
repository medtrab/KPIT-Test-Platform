#!/usr/bin/env python3


import json
import socket
import threading
import time

TCP_CAN_HOST = "0.0.0.0"
TCP_CAN_PORT = 5557   # PORT_CAN dans Platform/constants.py


# ----------------------------------------------------------------
#  Helpers decodage
# ----------------------------------------------------------------
def _bytes_to_hex(data8: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data8[:8])


def _crc_xor(payload7: bytes) -> int:
    crc = 0
    for b in payload7:
        crc ^= b
    return crc & 0xFF


def _decode_0x200(data8: bytes) -> dict:
    """Decode Wiper_Cmd recue depuis le maitre CAN."""
    if len(data8) < 4:
        return {}
    mode   = data8[0] & 0x0F
    speed  = (data8[0] >> 4) & 0x0F
    wash   = data8[1] & 0x03
    alive  = data8[2]
    crc_rx = data8[3]
    crc_ok = (crc_rx == ((data8[0] ^ data8[1] ^ data8[2]) & 0xFF))
    return {
        "mode"   : mode,
        "speed"  : speed,
        "wash"   : wash,
        "alive"  : alive,
        "crc_ok" : crc_ok,
    }


def _decode_0x201(data8: bytes) -> dict:
    """Decode Wiper_Status — WW-MCAT-005 Rev5 layout :
      byte0 : CurrentMode   byte1 : CurrentSpeed   byte2 : BladePosition
      byte3 : MotorCurrent (8-bit, 0.1A/bit)
      byte4 : FaultStatus bitfield (bits 0-5)
      byte5 : AliveCounter_RX    byte6 : CRC_Low    byte7 : Reserved
    """
    if len(data8) < 7:
        return {}
    mode      = data8[0] & 0xFF
    speed     = data8[1] & 0xFF
    blade_pct = max(0, min(100, int(data8[2])))
    current_A = round(data8[3] * 0.1, 3)        # 8-bit, 0.1A/bit
    fault_byte = data8[4] & 0xFF                 # FaultStatus bitfield
    alive     = data8[5] & 0xFF                  # AliveCounter_RX
    crc       = data8[6] & 0xFF                  # CRC_Low
    return {
        "mode"       : mode,
        "speed"      : speed,
        "blade_pct"  : blade_pct,
        "current_A"  : current_A,
        "fault"      : bool(fault_byte),          # rétrocompatibilité (OR des bits)
        "fault_byte" : fault_byte,                # valeur brute pour tests fins
        "fault_wc_internal"   : bool(fault_byte & 0x01),
        "fault_motor_driver"  : bool(fault_byte & 0x02),
        "fault_pos_sensor"    : bool(fault_byte & 0x04),
        "fault_supply"        : bool(fault_byte & 0x08),
        "fault_can_timeout"   : bool(fault_byte & 0x10),
        "fault_motor_blocked" : bool(fault_byte & 0x20),
        "alive"      : alive,
        "crc"        : crc,
    }


def _decode_0x202(data4: bytes) -> dict:
    """
    Décode Wiper_Ack (0x202) — WW-MCAT-005 Rev5, DLC=4.
      byte0 bit0    : AckStatus  (0=ACK, 1=NACK)
      byte0 bits7:1 : Ack_Reserved (Set to 0)
      byte1         : ErrorCode
      byte2         : AliveCounter_AK
      byte3         : CRC-8 = XOR(byte0, byte1, byte2)
    """
    if len(data4) < 4:
        return {}
    ack_byte = data4[0]
    ack_status = ack_byte & 0x01   # bit0 seulement
    err        = data4[1]
    alive      = data4[2]
    crc        = data4[3]
    crc_ok = (crc == ((ack_byte ^ err ^ alive) & 0xFF))
    return {
        "ack_status" : ack_status,   # 0=ACK, 1=NACK
        "ack_byte"   : ack_byte,
        "error_code" : err,
        "alive"      : alive,
        "crc"        : crc,
        "crc_ok"     : crc_ok,
    }


def _decode_0x300(data8: bytes) -> dict:
    """Decode Vehicle_Status emise periodiquement."""
    if len(data8) < 4:
        return {}
    ignition  = data8[0] & 0x03
    reverse   = bool(data8[1] & 0x01)
    speed_raw = (data8[2] << 8) | data8[3]
    speed_kmh = round(speed_raw / 10.0, 1)
    return {
        "ignition" : ignition,
        "reverse"  : reverse,
        "speed_kmh": speed_kmh,
    }


def _decode_0x301(data8: bytes) -> dict:
    """Decode RainSensorData emise periodiquement."""
    if len(data8) < 2:
        return {}
    intensity = max(0, min(100, int(data8[0])))
    sensor_ok = (data8[1] == 0x00)
    return {
        "intensity" : intensity,
        "sensor_ok" : sensor_ok,
    }


# ----------------------------------------------------------------
#  TCPCANBroadcast
# ----------------------------------------------------------------
class TCPCANBroadcast:
    """
    Serveur TCP leger sur PORT 5557.
    Recoit les callbacks de trames CAN (0x200 RX / 0x201 TX / 0x202 RX / 0x300 TX / 0x301 TX)
    et les diffuse au format JSON a tous les clients Platform connectes.
    Recoit aussi les trames 0x202 JSON envoyees par Platform (CANWorker) et
    appelle le callback on_rx_0x202_raw(data4: bytes) vers bcmcan pour relais CAN.
    """

    _FRAME_META = {
        0x200: ("RX", "0x200", "Wiper_Cmd",     _decode_0x200),
        0x201: ("TX", "0x201", "Wiper_Status",  _decode_0x201),
        0x202: ("TX", "0x202", "Wiper_Ack",     _decode_0x202),
        0x300: ("TX", "0x300", "Vehicle_Status", _decode_0x300),
        0x301: ("TX", "0x301", "RainSensorData", _decode_0x301),
    }

    def __init__(self):
        self._clients      : list[socket.socket] = []
        self._clients_lock = threading.Lock()
        self._running      = False
        self._last_msgs: dict[int, bytes] = {}
        # Callback appele quand on recoit un 0x202 JSON depuis Platform
        # Signature : callback(data4: bytes) ? None
        self._on_202_callback = None

    # -- Demarrage / arret -------------------------------------
    def start(self):
        self._running = True
        threading.Thread(
            target=self._accept_loop,
            daemon=True,
            name="T-TCP-CAN",
        ).start()
        print(f"[TCP-CAN] Serveur demarre sur port {TCP_CAN_PORT}")

    def stop(self):
        self._running = False

    def set_0x202_callback(self, callback) -> None:
        """
        Enregistre le callback a appeler quand Platform envoie une trame 0x202.
        callback(data4: bytes) : les 4 octets bruts [AckStatus, ErrorCode, Alive, CRC]
        """
        self._on_202_callback = callback

    # -- API publique : une methode par CAN ID emis vers Platform --
    def on_rx_0x200(self, data8: bytes, t_kernel: float = None) -> None:
        """Appeler apres reception d'une trame Wiper_Cmd (0x200)."""
        self._dispatch(0x200, data8, t_kernel=t_kernel)

    def on_tx_0x201(self, data8: bytes, t_kernel: float = None) -> None:
        """Appeler apres emission d'une trame Wiper_Status (0x201)."""
        self._dispatch(0x201, data8, t_kernel=t_kernel)

    def on_tx_0x202(self, data4: bytes, t_kernel: float = None) -> None:
        """Appeler apres emission d'une trame Wiper_Ack (0x202) par bcmcan."""
        self._dispatch(0x202, data4, t_kernel=t_kernel)

    def on_tx_0x300(self, data8: bytes, t_kernel: float = None) -> None:
        """Appeler apres emission d'une trame Vehicle_Status (0x300)."""
        self._dispatch(0x300, data8, t_kernel=t_kernel)

    def on_tx_0x301(self, data8: bytes, t_kernel: float = None) -> None:
        """Appeler apres emission d'une trame RainSensorData (0x301)."""
        self._dispatch(0x301, data8, t_kernel=t_kernel)

    # -- Construction et diffusion du message JSON -------------
    def _dispatch(self, can_id: int, data8: bytes,
                  t_kernel: float = None) -> None:
        direction, can_id_str, desc, decoder = self._FRAME_META[can_id]

        # Securiser la longueur du vecteur de donnees
        d8 = (data8 + bytes(8))[:8]

        # t_kernel : timestamp bus physique (SO_TIMESTAMP kernel socketcan)
        # Priorit? : t_kernel fourni > fallback time.time()
        t_now = time.time()
        payload = {
            "type"      : direction,
            "can_id"    : can_id_str,
            "can_id_int": can_id,
            "dlc"       : 8,
            "data"      : _bytes_to_hex(d8),
            "desc"      : desc,
            "time"      : t_now,
            "t_kernel"  : t_kernel if t_kernel is not None else t_now,
            "fields"    : decoder(d8),
        }
        msg = (json.dumps(payload) + "\n").encode("utf-8")
        self._last_msgs[can_id] = msg
        self._broadcast(msg)

    # -- Serveur TCP -------------------------------------------
    def _accept_loop(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((TCP_CAN_HOST, TCP_CAN_PORT))
        srv.listen(5)
        srv.settimeout(1.0)
        while self._running:
            try:
                conn, addr = srv.accept()
                print(f"[TCP-CAN] Client connecte : {addr}")
                with self._clients_lock:
                    self._clients.append(conn)
                threading.Thread(
                    target=self._watch_client,
                    args=(conn, addr),
                    daemon=True,
                ).start()
            except socket.timeout:
                continue
            except Exception as e:
                print(f"[TCP-CAN] Erreur accept : {e}")
        srv.close()

    def _watch_client(self, conn: socket.socket, addr):
        """
        Gere un client connecte :
        - Envoie les derniers etats connus au nouveau client
        - Recoit les trames JSON envoyees par Platform (0x202 Wiper_Ack)
        - Relais 0x202 ? callback ? bcmcan ? CAN bus
        """
        # Rejouer les derniers messages de chaque CAN ID
        for can_id in (0x200, 0x201, 0x300, 0x301):
            msg = self._last_msgs.get(can_id)
            if msg:
                try:
                    conn.sendall(msg)
                except Exception:
                    break

        # ecouter les trames entrantes depuis Platform
        buf = ""
        conn.settimeout(1.0)
        try:
            while self._running:
                try:
                    data = conn.recv(1024)
                    if not data:
                        break
                    buf += data.decode("utf-8", errors="replace")
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                            cid = ev.get("can_id_int", 0)
                            if cid == 0x202 and self._on_202_callback is not None:
                                # Reconstruire les 4 octets bruts depuis les champs JSON
                                f   = ev.get("fields", {})
                                ack = int(f.get("ack_status", 0)) & 0xFF
                                err = int(f.get("error_code", 0)) & 0xFF
                                alv = int(f.get("alive", 0))      & 0xFF
                                crc = int(f.get("crc", (ack ^ err ^ alv) & 0xFF)) & 0xFF
                                self._on_202_callback(bytes([ack, err, alv, crc]))
                                # Diffuser aussi le 0x202 JSON aux autres clients
                                raw_msg = (line + "\n").encode("utf-8")
                                self._last_msgs[0x202] = raw_msg
                                self._broadcast_except(raw_msg, conn)
                        except (json.JSONDecodeError, ValueError, KeyError):
                            pass
                except socket.timeout:
                    pass
                except OSError:
                    break
        finally:
            with self._clients_lock:
                if conn in self._clients:
                    self._clients.remove(conn)
            try:
                conn.close()
            except Exception:
                pass
            print(f"[TCP-CAN] Client deconnecte : {addr}")

    def _broadcast(self, msg: bytes):
        """Envoie msg a tous les clients, retire les sockets mortes."""
        dead = []
        with self._clients_lock:
            for c in self._clients:
                try:
                    c.sendall(msg)
                except Exception:
                    dead.append(c)
            for c in dead:
                self._clients.remove(c)

    def _broadcast_except(self, msg: bytes, exclude: socket.socket):
        """Envoie msg a tous les clients sauf exclude."""
        dead = []
        with self._clients_lock:
            for c in self._clients:
                if c is exclude:
                    continue
                try:
                    c.sendall(msg)
                except Exception:
                    dead.append(c)
            for c in dead:
                self._clients.remove(c)


# ----------------------------------------------------------------
#  Integration dans bcmcan.py  (a appeler depuis _can_rxtx_thread)
# ----------------------------------------------------------------
def integrate_with_bcmcan():
    """
    Exemple d'integration dans bcmcan._can_rxtx_thread() :

        from bcm_tcp_can import TCPCANBroadcast
        _tcp_can = TCPCANBroadcast()
        _tcp_can.start()

        # Dans la boucle CAN, apres chaque envoi/reception :
        if can_id == _CAN_ID_CMD:
            _tcp_can.on_rx_0x200(frame_data)          # 0x200 recu
        if emitting_0x201:
            _tcp_can.on_tx_0x201(built_frame_data)    # 0x201 emis
        if emitting_0x300:
            _tcp_can.on_tx_0x300(built_frame_data)    # 0x300 emis
        if emitting_0x301:
            _tcp_can.on_tx_0x301(built_frame_data)    # 0x301 emis
    """
    pass  # documentation seulement