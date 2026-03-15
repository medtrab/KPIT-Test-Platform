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
        while self.running:
            self.status_changed.emit(f"Scan port {PORT_MOTOR}…", False)
            host = auto_discover(PORT_MOTOR)
            if not host:
                self.status_changed.emit(f"Port {PORT_MOTOR} : aucun hôte", False)
                time.sleep(5)
                continue

            self._host = host
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.connect((host, PORT_MOTOR))
                self.status_changed.emit(f"Moteurs — {host}:{PORT_MOTOR}", True)
                self.sock.settimeout(0.05)
                buf = ""
                last_wiper = time.time()

                while self.running:
                    # ── Réception ─────────────────────────────────────
                    try:
                        data = self.sock.recv(512)
                        if not data:
                            break
                        buf += data.decode("utf-8", errors="replace")
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
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

                    # ── Envoi file d'attente (vehicle / rain) ──────────
                    with self._send_lock:
                        pending = list(self._send_queue)
                        self._send_queue.clear()
                    for msg in pending:
                        try:
                            self.sock.sendall(msg.encode())
                        except Exception:
                            break

                    # ── Envoi périodique wiper_op (~5 Hz) ──────────────
                    now = time.time()
                    if now - last_wiper >= 0.2:
                        with self._wiper_lock:
                            op = self._wiper_op
                            self._wiper_seq = (self._wiper_seq + 1) & 0xFFFF
                            seq = self._wiper_seq
                        try:
                            payload = json.dumps(
                                {"type": "wiper", "wiper_op": op, "seq": seq}) + "\n"
                            self.sock.sendall(payload.encode())
                            self.wiper_sent.emit(op, seq)
                        except Exception:
                            break
                        last_wiper = now

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


# ═══════════════════════════════════════════════════════════
#  WORKER LIN  (port 5555 — rx uniquement)
# ═══════════════════════════════════════════════════════════
class LINWorker(QObject):
    lin_received   = pyqtSignal(dict)
    status_changed = pyqtSignal(str, bool)

    def __init__(self) -> None:
        super().__init__()
        self.running = True
        self.sock: socket.socket | None = None
        self._host   = ""

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
                buf = ""
                while self.running:
                    try:
                        data = self.sock.recv(512)
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
    """Envoi commande pompe (port 5001) — logique exacte pump_monitor.py."""
    if not host:
        return
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect((host, PORT_PUMP_TX))
        s.sendall((json.dumps({"cmd": cmd, "duration": dur}) + "\n").encode())
        s.close()
        print(f"[PUMP] -> {host}:{PORT_PUMP_TX}  {cmd}  {dur:.1f}s")
    except Exception as e:
        print(f"[PUMP] Erreur commande: {e}")
