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

from PySide6.QtCore import QObject, Signal

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
    motor_received = Signal(dict)
    status_changed = Signal(str, bool)   # (message, connected)
    wiper_sent     = Signal(int, int)    # (op, seq)
    sim_host_found = Signal(str)         # IP du RPi Simulateur (injection défauts)

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
        # Discovery manuelle : set_host() impose l'IP, sinon auto_discover est utilisé
        self._host_event: threading.Event = threading.Event()
        self._forced_host: str = ""
        # Discovery manuelle RPiSIM (TX)
        self._sim_event: threading.Event = threading.Event()
        self._forced_sim: str = ""

    # ── API publique ─────────────────────────────────────────
    def queue_send(self, obj: dict) -> None:
        """Ajoute un message JSON à envoyer (vehicle / rain)."""
        with self._send_lock:
            self._send_queue.append(json.dumps(obj) + "\n")

    def set_host(self, ip: str) -> None:
        """Impose l'IP du RPiBCM — découverte manuelle depuis le DiscoveryDialog."""
        self._forced_host = ip
        self._host_event.set()

    def set_sim_host(self, ip: str) -> None:
        """Impose l'IP du RPiSIM — découverte manuelle depuis le DiscoveryDialog."""
        self._forced_sim = ip
        self._sim_event.set()

    def set_wiper_op(self, op: int) -> None:
        with self._wiper_lock:
            self._wiper_op = op

    @property
    def host(self) -> str:
        return self._host

    def stop(self) -> None:
        self.running = False
        self._host_event.set()   # débloquer wait() si en attente
        self._sim_event.set()
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
            # Attendre un host imposé manuellement (DiscoveryDialog)
            if not self._host_event.is_set():
                self.status_changed.emit("En attente de connexion (Discovery)…", False)
                self._host_event.wait()   # bloque jusqu'à set_host()
            if not self.running:
                break
            host_rx = self._forced_host
            self._host_event.clear()
            self._forced_host = ""
            if not host_rx:
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
                                    # Émettre les messages moteur RPiBCM (contiennent "state")
                                    # ET les messages de faute test (wc_alive_fault, wc_crc_fault, info)
                                    # FIX TC_CAN_003/TC_FSR_010 : le broadcast de faute n'a pas de "state"
                                    # → il était silencieusement droppé, empêchant la détection.
                                    _TEST_TYPES = ("wc_alive_fault", "wc_crc_fault", "info",
                                                   "alive_error", "can_fault",
                                                   "b2104_active", "b2104_inactive")
                                    if (isinstance(parsed.get("state"), str) or
                                            parsed.get("type") in _TEST_TYPES or
                                            parsed.get("wc_alive_fault") or
                                            parsed.get("wc_crc_fault")):
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
            # Attendre un host imposé manuellement (DiscoveryDialog)
            if not self._sim_event.is_set():
                self._sim_event.wait()
            if not self.running:
                break
            host_tx = self._forced_sim
            self._sim_event.clear()
            self._forced_sim = ""
            if not host_tx:
                time.sleep(5)
                continue

            # Notifier main_window de l'IP du simulateur (pour SimClient)
            self.sim_host_found.emit(host_tx)

            try:
                sock_tx = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock_tx.connect((host_tx, PORT_MOTOR))
                sock_tx.settimeout(0.05)

                # Purger les test_cmd résiduels accumulés pendant la déconnexion.
                # Ces commandes one-shot (corrupt_crc_0x201 count=0, restore_can_alive…)
                # ne doivent pas survivre à une reconnexion : elles appartiennent à un
                # run précédent et écraseraient les commandes du run courant si elles
                # arrivaient après (ex: count=0 après count=8 → 0 trames corrompues).
                with self._send_lock:
                    self._send_queue = [
                        m for m in self._send_queue
                        if "test_cmd" not in m
                    ]

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
    lin_received   = Signal(dict)
    status_changed = Signal(str, bool)

    def __init__(self) -> None:
        super().__init__()
        self.running   = True
        self.sock: socket.socket | None = None
        self._host     = ""
        self._send_lock  = threading.Lock()
        self._send_queue: list[str] = []
        self._host_event: threading.Event = threading.Event()
        self._forced_host: str = ""

    @property
    def host(self) -> str:
        return self._host

    def set_host(self, ip: str) -> None:
        """Impose l'IP du RPiBCM pour LIN — découverte manuelle."""
        self._forced_host = ip
        self._host_event.set()

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
        self._host_event.set()   # débloquer wait() si en attente
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

    def run(self) -> None:
        while self.running:
            if not self._host_event.is_set():
                self.status_changed.emit("En attente de connexion (Discovery)…", False)
                self._host_event.wait()
            if not self.running:
                break
            host = self._forced_host
            self._host_event.clear()
            self._forced_host = ""
            if not host:
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
    data_received   = Signal(dict)
    connection_lost = Signal()
    connection_ok   = Signal(str)   # host découvert


class PumpDataClient(threading.Thread):
    """
    Thread daemon TCP — réception données pompe (port 5556).
    Logique exacte de pump_monitor.py.
    """
    def __init__(self, signal: PumpSignal) -> None:
        super().__init__(daemon=True)
        self.signal = signal
        self._host: str | None = None
        self._host_event: threading.Event = threading.Event()
        self._forced_host: str = ""

    @property
    def host(self) -> str | None:
        return self._host

    def set_host(self, ip: str) -> None:
        """Impose l'IP pour la pompe — découverte manuelle."""
        self._forced_host = ip
        self._host_event.set()

    def run(self) -> None:
        while True:
            if not self._host_event.is_set():
                self._host_event.wait()
            host = self._forced_host
            self._host_event.clear()
            self._forced_host = ""
            if not host:
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
    can_received   = Signal(dict)
    status_changed = Signal(str, bool)

    def __init__(self) -> None:
        super().__init__()
        self.running     = True
        self.sock: socket.socket | None = None
        self._host       = ""
        self._send_lock  = threading.Lock()
        self._send_queue: list[str] = []   # JSON lines à envoyer (0x202)
        self._host_event: threading.Event = threading.Event()
        self._forced_host: str = ""

    @property
    def host(self) -> str:
        return self._host

    def set_host(self, ip: str) -> None:
        """Impose l'IP pour CAN — découverte manuelle."""
        self._forced_host = ip
        self._host_event.set()

    def queue_send(self, obj: dict):
        """Ajoute un message JSON à envoyer (commandes de test)."""
        with self._send_lock:
            self._send_queue.append(json.dumps(obj) + "\n")

    def stop(self) -> None:
        self.running = False
        self._host_event.set()   # débloquer wait() si en attente
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
            if not self._host_event.is_set():
                self.status_changed.emit("En attente de connexion (Discovery)…", False)
                self._host_event.wait()
            if not self.running:
                break
            host = self._forced_host
            self._host_event.clear()
            self._forced_host = ""
            if not host:
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