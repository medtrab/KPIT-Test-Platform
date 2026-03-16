#!/usr/bin/env python3
"""
bcm_tcp_pump.py
===============
Serveur TCP dédié pompe -- port 5000
Diffuse l'état de la pompe vers l'interface WipeWash Pump Monitor (PyQt6).

FORMAT JSON envoyé :
{
    "state":          "FORWARD",   // FORWARD / BACKWARD / OFF / FAULT / OVERCURRENT
    "current":        0.35,        // courant ADS1115 (amperes)
    "voltage":        0.0,         // pas de capteur tension → toujours 0.0
    "fault":          false,       // ST_ERROR actif
    "fault_reason":   "",          // OVERCURRENT / ""
    "pump_remaining": 3.2,         // secondes restantes (PUMP_MAX_RUNTIME - elapsed)
    "pump_duration":  5.0,         // PUMP_MAX_RUNTIME
    "source":         "BCM"        // toujours BCM
}

UTILISATION dans bcm_application.py :
    from bcm_tcp_pump import TCPPumpBroadcast
    self._tcp_pump = TCPPumpBroadcast()
    self._tcp_pump.start()
    self._tcp_pump.send(rte)       # appeler apres chaque changement d'etat pompe
"""

import json
import socket
import threading
import time

from bcm_rte import ST_ERROR, PUMP_MAX_RUNTIME, PUMP_OVERCURRENT_THRESH

TCP_PUMP_HOST = "0.0.0.0"
TCP_PUMP_PORT = 5000


class TCPPumpBroadcast:
    """
    Serveur TCP léger sur port 5000.
    Envoie l'état pompe au format attendu par WipeWash Pump Monitor.
    Lecture seule du RTE -- ne modifie rien.
    """

    def __init__(self):
        self._clients      = []
        self._clients_lock = threading.Lock()
        self._running      = False
        self._last_msg     = None   # dernier JSON envoye -- pour les nouveaux clients

    def start(self):
        self._running = True
        t = threading.Thread(
            target=self._accept_loop,
            daemon=True,
            name="T-TCP-PUMP"
        )
        t.start()
        print(f"[TCP-PUMP] Serveur demarre sur port {TCP_PUMP_PORT}")

    def stop(self):
        self._running = False

    def send(self, rte) -> None:
        """
        Construit le JSON pompe depuis le RTE et l'envoie a tous les clients.
        Appeler apres chaque changement d'etat pompe ou de courant.
        """
        # --- Etat pompe ---
        direction = rte.pump_direction  # 0=off / 1=FWD / 2=BWD
        if rte.pump_active:
            pump_state = "FORWARD" if direction == 1 else "BACKWARD"
        else:
            pump_state = "OFF"

        # --- Fault / overcurrent ---
        is_error      = rte.state == ST_ERROR
        is_overcurrent = rte.motor_current_a > PUMP_OVERCURRENT_THRESH and rte.pump_active
        fault         = is_error or is_overcurrent
        fault_reason  = "OVERCURRENT" if is_overcurrent else ""
        if fault and pump_state not in ("FORWARD", "BACKWARD"):
            pump_state = "FAULT"

        # --- Temps restant ---
        if rte.pump_active and rte.t_pump_start > 0:
            elapsed         = time.time() - rte.t_pump_start
            pump_remaining  = round(max(0.0, PUMP_MAX_RUNTIME - elapsed), 1)
        else:
            pump_remaining  = 0.0

        payload = {
            "state":          pump_state,
            "current":        round(rte.pump_current_a, 3),   # ACS712 canal A0
            "voltage":        round(rte.pump_voltage_v, 2),   # calcule depuis ACS712
            "fault":          fault,
            "fault_reason":   fault_reason,
            "pump_remaining": pump_remaining,
            "pump_duration":  PUMP_MAX_RUNTIME,
            "source":         "BCM",
        }

        msg = json.dumps(payload) + "\n"
        self._last_msg = msg.encode()
        self._broadcast(self._last_msg)

    # ─────────────────────────────────────────────────
    # Interne
    # ─────────────────────────────────────────────────

    def _accept_loop(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((TCP_PUMP_HOST, TCP_PUMP_PORT))
        srv.listen(5)
        srv.settimeout(1.0)
        while self._running:
            try:
                conn, addr = srv.accept()
                print(f"[TCP-PUMP] Client connecte : {addr}")
                with self._clients_lock:
                    self._clients.append(conn)
                threading.Thread(
                    target=self._watch_disconnect,
                    args=(conn, addr),
                    daemon=True
                ).start()
            except socket.timeout:
                continue
            except Exception as e:
                print(f"[TCP-PUMP] Erreur accept : {e}")
        srv.close()

    def _watch_disconnect(self, conn, addr):
        # Envoie immediatement l'etat courant au nouveau client
        if self._last_msg:
            try:
                conn.sendall(self._last_msg)
            except Exception:
                pass
        try:
            while self._running:
                data = conn.recv(64)
                if not data:
                    break
        except Exception:
            pass
        finally:
            with self._clients_lock:
                if conn in self._clients:
                    self._clients.remove(conn)
            try:
                conn.close()
            except Exception:
                pass
            print(f"[TCP-PUMP] Client deconnecte : {addr}")

    def _broadcast(self, msg: bytes):
        dead = []
        with self._clients_lock:
            for c in self._clients:
                try:
                    c.sendall(msg)
                except Exception:
                    dead.append(c)
            for c in dead:
                self._clients.remove(c)