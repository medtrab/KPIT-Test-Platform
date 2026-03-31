"""
WipeWash — Workers TCP
  MotorVehicleWorker  — port 5000  (rx moteurs + tx vehicle/rain/wiper)
  LINWorker           — port 5555  (rx LIN events)
  PumpSignal          — signaux Qt pour la pompe
  PumpDataClient      — port 5556  (rx données pompe, threading.Thread)
  send_pump_cmd       — port 5001  (tx commandes pompe)
"""

import json
import socket
import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal

from constants import PORT_MOTOR, PORT_LIN, PORT_PUMP_RX, PORT_PUMP_TX
try:
    from constants import PORT_CAN
except ImportError:
    PORT_CAN = 5557
from network   import auto_discover


# ═══════════════════════════════════════════════════════════
#  WORKER MOTEURS + VEHICLE/RAIN/WIPER  (port 5000)
# ═══════════════════════════════════════════════════════════
class MotorVehicleWorker(QObject):
    motor_received = pyqtSignal(dict)
    status_changed = pyqtSignal(str, bool)   # (message, connected)
    wiper_sent     = pyqtSignal(int, int)    # (op, seq)

    def __init__(self) -> None:
        super().__init__()
        self.running   = True
        self.sock: socket.socket | None = None
        self._host     = ""
        self._send_lock  = threading.Lock()
        self._send_queue: list[str] = []
        self._wiper_lock = threading.Lock()
        self._wiper_op   = 0
        self._wiper_seq  = 0

    # ── API publique ─────────────────────────────────────────
    def queue_send(self, obj: dict) -> None:
        """Ajoute un message JSON à envoyer (vehicle / rain)."""
        with self._send_lock:
            self._send_queue.append(json.dumps(obj) + "\n")

    def set_wiper_op(self, op: int) -> None:
        with self._wiper_lock:
            self._wiper_op = op

    @property
    def host(self) -> str:
        return self._host

    def stop(self) -> None:
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

    # ── Boucle principale (exécutée dans QThread) ────────────
    def run(self) -> None:
        """
        Gère deux connexions TCP simultanées sur le MÊME port 5000 :

          sock RX → RPiBCM (10.20.0.25:5000) bcm_tcp_broadcast
                    Reçoit {"state":"SPEED1","front":"ON","rear":"OFF",...}
                    → motor_received → MotorDashPanel affiche les états

          sock TX → RPiSIM (10.20.0.7:5000)  bcmcan
                    Envoie {"ignition_status":1,"vehicle_speed":0,...}
                    Reçoit {"front":{"blade_position":0,...}} — ignoré (format capteurs)

        Identification : à la connexion, chaque serveur envoie immédiatement
        son état JSON. On lit le premier message pour savoir à qui on parle :
          - contient "state" (str) → RPiBCM → socket RX moteur
          - contient "front" (dict) → RPiSIM → socket TX vehicle/rain
        """
        # Lancer le thread TX en arrière-plan
        threading.Thread(
            target=self._run_tx, daemon=True, name="MotorWorker-TX"
        ).start()

        # Thread RX : réception état moteur depuis RPiBCM:5000
        while self.running:
            self.status_changed.emit(f"Scan port {PORT_MOTOR}…", False)
            from network import auto_discover_all
            hosts = auto_discover_all(PORT_MOTOR, timeout=2.0)
            # Identifier le RPiBCM parmi les hôtes trouvés
            host_rx = self._identify_bcm(hosts)
            if not host_rx:
                self.status_changed.emit(f"Port {PORT_MOTOR} : RPiBCM non trouvé", False)
                time.sleep(5)
                continue

            self._host = host_rx
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.connect((host_rx, PORT_MOTOR))
                self.status_changed.emit(f"Moteurs — {host_rx}:{PORT_MOTOR}", True)
                self.sock.settimeout(0.05)
                buf = ""

                while self.running:
                    try:
                        data = self.sock.recv(4096)
                        if not data:
                            break
                        buf += data.decode("utf-8", errors="replace")
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            line = line.strip()
                            if line:
                                try:
                                    parsed = json.loads(line)
                                    # N'émettre que les messages moteur RPiBCM
                                    # (contiennent "state" comme string)
                                    if isinstance(parsed.get("state"), str):
                                        self.motor_received.emit(parsed)
                                except Exception:
                                    pass
                    except socket.timeout:
                        pass
                    except Exception:
                        break

            except Exception as e:
                self.status_changed.emit(f"Port {PORT_MOTOR} erreur: {e}", False)
            finally:
                if self.sock:
                    try:
                        self.sock.close()
                    except Exception:
                        pass
                self.sock = None

            if self.running:
                self.status_changed.emit(f"Port {PORT_MOTOR} reconnexion…", False)
                time.sleep(3)

    def _identify_bcm(self, hosts: list) -> str | None:
        """
        Parmi une liste d'hôtes trouvés sur port 5000, identifie le RPiBCM :
        celui dont le premier message JSON contient "state" sous forme de string
        (format bcm_tcp_broadcast : {"state":"SPEED1","front":"ON",...}).
        Le RPiSIM envoie {"front":{"blade_position":...}} — format bcmcan.
        Retourne l'IP du RPiBCM, ou None si introuvable.
        """
        for host in hosts:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                s.connect((host, PORT_MOTOR))
                # Lire le premier message envoyé par le serveur à la connexion
                raw = b""
                deadline = time.time() + 0.5
                while time.time() < deadline and b"\n" not in raw:
                    try:
                        chunk = s.recv(512)
                        if not chunk:
                            break
                        raw += chunk
                    except socket.timeout:
                        break
                s.close()
                if raw:
                    line = raw.split(b"\n")[0].strip()
                    if line:
                        try:
                            msg = json.loads(line)
                            # RPiBCM : "state" est une string ("OFF","SPEED1",...)
                            if isinstance(msg.get("state"), str):
                                return host
                        except Exception:
                            pass
            except Exception:
                pass
        return None

    def _run_tx(self) -> None:
        """
        Thread TX dédié : envoie vehicle/rain/wiper vers RPiSIM:5000 (bcmcan).
        Identification : premier message = {"front":{...}} (dict, pas string).
        Reconnexion automatique, complètement indépendant du thread RX.
        """
        sock_tx    = None
        last_wiper = time.time()

        while self.running:
            from network import auto_discover_all
            hosts = auto_discover_all(PORT_MOTOR, timeout=2.0)
            # Identifier le RPiSIM : premier message contient "front" comme dict
            host_tx = None
            for host in hosts:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(0.5)
                    s.connect((host, PORT_MOTOR))
                    raw = b""
                    deadline = time.time() + 0.5
                    while time.time() < deadline and b"\n" not in raw:
                        try:
                            chunk = s.recv(512)
                            if not chunk:
                                break
                            raw += chunk
                        except socket.timeout:
                            break
                    s.close()
                    if raw:
                        line = raw.split(b"\n")[0].strip()
                        if line:
                            try:
                                msg = json.loads(line)
                                # RPiSIM/bcmcan : "front" est un dict
                                if isinstance(msg.get("front"), dict):
                                    host_tx = host
                                    break
                            except Exception:
                                pass
                except Exception:
                    pass

            if not host_tx:
                time.sleep(5)
                continue

            try:
                sock_tx = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock_tx.connect((host_tx, PORT_MOTOR))
                sock_tx.settimeout(0.05)

                buf_tx = ""
                while self.running:
                    # ── Lecture réponses bcmcan (ex: can_fault T11) ────
                    try:
                        data = sock_tx.recv(4096)
                        if not data:
                            break
                        buf_tx += data.decode("utf-8", errors="replace")
                        while "\n" in buf_tx:
                            line, buf_tx = buf_tx.split("\n", 1)
                            line = line.strip()
                            if line:
                                try:
                                    self.motor_received.emit(json.loads(line))
                                except Exception:
                                    pass
                    except socket.timeout:
                        pass
                    except Exception:
                        break

                    # ── Envoi file d'attente (vehicle / rain / test_cmd) ─
                    with self._send_lock:
                        pending = list(self._send_queue)
                        self._send_queue.clear()
                    for msg in pending:
                        try:
                            sock_tx.sendall(msg.encode())
                        except Exception:
                            break

                    # ── Envoi périodique wiper_op (~5 Hz) ─────────────
                    now = time.time()
                    if now - last_wiper >= 0.2:
                        with self._wiper_lock:
                            op  = self._wiper_op
                            self._wiper_seq = (self._wiper_seq + 1) & 0xFFFF
                            seq = self._wiper_seq
                        try:
                            payload = json.dumps(
                                {"type": "wiper", "wiper_op": op, "seq": seq}) + "\n"
                            sock_tx.sendall(payload.encode())
                            self.wiper_sent.emit(op, seq)
                        except Exception:
                            break
                        last_wiper = now

            except Exception:
                pass
            finally:
                if sock_tx:
                    try:
                        sock_tx.close()
                    except Exception:
                        pass
                    sock_tx = None

            if self.running:
                time.sleep(3)


# ═══════════════════════════════════════════════════════════
#  WORKER LIN  (port 5555 — rx uniquement)
# ═══════════════════════════════════════════════════════════
class LINWorker(QObject):
    lin_received   = pyqtSignal(dict)
    status_changed = pyqtSignal(str, bool)

    def __init__(self) -> None:
        super().__init__()
        self.running   = True
        self.sock: socket.socket | None = None
        self._host     = ""
        self._send_lock  = threading.Lock()
        self._send_queue: list[str] = []

    @property
    def host(self) -> str:
        return self._host

    def queue_send(self, obj: dict) -> None:
        """Envoie un message JSON vers crslin (ex: {"cmd": "SPEED1"})."""
        with self._send_lock:
            self._send_queue.append(json.dumps(obj) + "\n")

    def set_wiper_op(self, op: int) -> None:
        """Convertit un entier op en commande JSON cmd et l'enqueue."""
        from constants import WOP
        op_name = WOP.get(op, {}).get("name", "OFF")
        self.queue_send({"cmd": op_name})

    def stop(self) -> None:
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

    def run(self) -> None:
        while self.running:
            self.status_changed.emit(f"Scan port {PORT_LIN}…", False)
            host = auto_discover(PORT_LIN)
            if not host:
                self.status_changed.emit(f"Port {PORT_LIN} : aucun hôte", False)
                time.sleep(5)
                continue

            self._host = host
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.connect((host, PORT_LIN))
                self.status_changed.emit(f"LIN — {host}:{PORT_LIN}", True)
                self.sock.settimeout(0.05)
                buf = ""
                while self.running:
                    # ── Réception événements LIN ───────────────────────
                    try:
                        data = self.sock.recv(4096)
                        if not data:
                            break
                        buf += data.decode("utf-8", errors="replace")
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            line = line.strip()
                            if line:
                                try:
                                    self.lin_received.emit(json.loads(line))
                                except Exception:
                                    pass
                    except socket.timeout:
                        pass
                    except Exception:
                        break

                    # ── Envoi file d'attente (commandes wiper) ─────────
                    with self._send_lock:
                        pending = list(self._send_queue)
                        self._send_queue.clear()
                    for msg in pending:
                        try:
                            self.sock.sendall(msg.encode())
                        except Exception:
                            break

            except Exception as e:
                self.status_changed.emit(f"Port {PORT_LIN} erreur: {e}", False)
            finally:
                if self.sock:
                    try:
                        self.sock.close()
                    except Exception:
                        pass
                self.sock = None

            if self.running:
                self.status_changed.emit(f"Port {PORT_LIN} reconnexion…", False)
                time.sleep(3)


# ═══════════════════════════════════════════════════════════
#  POMPE  (ports 5556 rx / 5001 tx)
# ═══════════════════════════════════════════════════════════
class PumpSignal(QObject):
    data_received   = pyqtSignal(dict)
    connection_lost = pyqtSignal()
    connection_ok   = pyqtSignal(str)   # host découvert


class PumpDataClient(threading.Thread):
    """
    Thread daemon TCP — réception données pompe (port 5556).
    Logique exacte de pump_monitor.py.
    """
    def __init__(self, signal: PumpSignal) -> None:
        super().__init__(daemon=True)
        self.signal = signal
        self._host: str | None = None

    @property
    def host(self) -> str | None:
        return self._host

    def run(self) -> None:
        while True:
            host = auto_discover(PORT_PUMP_RX)
            if not host:
                time.sleep(5)
                continue
            self._host = host
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((host, PORT_PUMP_RX))
                self.signal.connection_ok.emit(host)
                buf = ""
                while True:
                    data = sock.recv(1024).decode()
                    if not data:
                        break
                    buf += data
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        try:
                            self.signal.data_received.emit(json.loads(line))
                        except Exception:
                            pass
            except Exception:
                self.signal.connection_lost.emit()
                self._host = None
            time.sleep(3)


def send_pump_cmd(host: str | None, cmd: str, dur: float = 0.0) -> None:
    """
    Ancienne commande pompe TCP (port 5001) — désactivée.
    Le BCM n'a pas de serveur sur port 5001.
    La pompe est désormais commandée via Redis set_cmd("crs_wiper_op", 5/6/0).
    Fonction conservée pour compatibilité des imports.
    """
    if not host:
        return
    # Port 5001 n'existe pas — ne rien faire pour éviter l'erreur WinError 10061
    print(f"[PUMP] send_pump_cmd désactivé — utiliser Redis set_cmd à la place")


# ═══════════════════════════════════════════════════════════════
#  WORKER CAN  (port 5557 — rx uniquement, événements CAN JSON)
# ═══════════════════════════════════════════════════════════════
class CANWorker(QObject):
    """
    Thread TCP bidirectionnel sur port 5557 :
      - RX : reçoit 0x200/0x201/0x300/0x301 depuis bcmcan → signal can_received
      - TX : envoie 0x202 (Wiper_Ack) vers bcmcan quand le panel l'exige
    """
    can_received   = pyqtSignal(dict)
    status_changed = pyqtSignal(str, bool)

    def __init__(self) -> None:
        super().__init__()
        self.running     = True
        self.sock: socket.socket | None = None
        self._host       = ""
        self._send_lock  = threading.Lock()
        self._send_queue: list[str] = []   # JSON lines à envoyer (0x202)

    @property
    def host(self) -> str:
        return self._host

    def queue_send(self, obj: dict):
        """Ajoute un message JSON à envoyer (commandes de test)."""
        with self._send_lock:
            self._send_queue.append(json.dumps(obj) + "\n")

    def stop(self) -> None:
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

    def send_0x202(self, ack_status: int, error_code: int, alive: int) -> None:
        """
        Envoie Wiper_Ack (0x202) vers bcmcan — direction WC → BCM.
        Thread-safe : le message est mis en file et envoyé par le thread run().
        Émet aussi can_received pour afficher la trame dans le panneau.
        """
        crc = (ack_status ^ error_code ^ alive) & 0xFF
        data_bytes = bytes([ack_status & 0xFF, error_code & 0xFF, alive & 0xFF, crc])
        ev = {
            "type"      : "TX",
            "can_id"    : "0x202",
            "can_id_int": 0x202,
            "dlc"       : 4,
            "data"      : " ".join(f"{b:02X}" for b in data_bytes),
            "desc"      : "Wiper_Ack",
            "time"      : time.time(),
            "fields"    : {
                "ack_status" : ack_status,
                "error_code" : error_code,
                "alive"      : alive,
                "crc"        : crc,
            },
        }
        with self._send_lock:
            self._send_queue.append(json.dumps(ev) + "\n")
        # Émettre immédiatement pour mise à jour UI (thread-safe via Qt queued)
        self.can_received.emit(ev)

    def run(self) -> None:
        while self.running:
            self.status_changed.emit(f"Scan port {PORT_CAN}…", False)
            host = auto_discover(PORT_CAN)
            if not host:
                self.status_changed.emit(f"Port {PORT_CAN} : aucun hôte", False)
                time.sleep(5)
                continue

            self._host = host
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.connect((host, PORT_CAN))
                self.status_changed.emit(f"CAN — {host}:{PORT_CAN}", True)
                self.sock.settimeout(0.05)
                buf = ""
                while self.running:
                    # ── Réception événements CAN depuis bcmcan ────────
                    try:
                        data = self.sock.recv(1024)
                        if not data:
                            break
                        buf += data.decode("utf-8", errors="replace")
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            line = line.strip()
                            if line:
                                try:
                                    self.can_received.emit(json.loads(line))
                                except Exception:
                                    pass
                    except socket.timeout:
                        pass
                    except Exception:
                        break

                    # ── Envoi de la file (0x202 Wiper_Ack) ───────────
                    with self._send_lock:
                        pending = list(self._send_queue)
                        self._send_queue.clear()
                    for msg in pending:
                        try:
                            self.sock.sendall(msg.encode())
                        except Exception:
                            break

            except Exception as e:
                self.status_changed.emit(f"Port {PORT_CAN} erreur: {e}", False)
            finally:
                if self.sock:
                    try:
                        self.sock.close()
                    except Exception:
                        pass
                self.sock = None

            if self.running:
                self.status_changed.emit(f"Port {PORT_CAN} reconnexion…", False)
                time.sleep(3)