#!/usr/bin/env python3
"""
bcm_tcp_broadcast.py
====================
Module TCP isolé -- diffusion état BCM aux clients connectés.

UTILISATION dans bcm_application.py :
    from bcm_tcp_broadcast import TCPBroadcast
    self._tcp = TCPBroadcast()
    self._tcp.start()
    self._tcp.send(rte)   # appeler apres chaque changement d'etat

FORMAT JSON envoyé :
    {
        "state":   "SPEED1",       // etat machine WSM
        "front":   "ON",           // moteur avant
        "rear":    "OFF",          // moteur arriere
        "speed":   "Speed1",       // vitesse active
        "current": 0.35,           // courant ADS1115 (amperes)
        "rest":    "PARKING",      // contact repos
        "fault":   false           // ST_ERROR actif
    }

PORT : 5000 (TCP)
"""

import json
import socket
import threading


TCP_HOST = "0.0.0.0"
TCP_PORT = 5000


class TCPBroadcast:
    """
    Serveur TCP léger.
    - Accepte N clients simultanement.
    - Envoie un JSON d'état a tous les clients connectés.
    - Envoie l'etat courant immediatement a chaque nouveau client.
    - Totalement passif : ne touche pas au RTE, ne modifie rien.
    """

    def __init__(self):
        self._clients      = []
        self._clients_lock = threading.Lock()
        self._running      = False
        self._last_msg     = None   # dernier JSON envoye -- pour les nouveaux clients

    def start(self):
        """Lance le thread d'acceptation TCP (daemon)."""
        self._running = True
        t = threading.Thread(
            target=self._accept_loop,
            daemon=True,
            name="T-TCP"
        )
        t.start()
        print(f"[TCP] Serveur demarre sur port {TCP_PORT}")

    def stop(self):
        self._running = False

    def send(self, rte) -> None:
        """
        Construit le JSON depuis le RTE et l'envoie a tous les clients.
        Appeler apres chaque transition d'etat ou changement significatif.
        """
        from bcm_rte import ST_ERROR, ST_DIAG
        state = rte.state
        payload = {
            "state":   state,
            "front":   "ON"      if rte.front_motor_on     else "OFF",
            "rear":    "ON"      if rte.rear_motor_on      else "OFF",
            "speed":   "Speed2"  if rte.front_motor_speed == 2 else "Speed1",
            "current": round(rte.motor_current_a, 2),
            "rest":    "PARKING" if not rte.front_blade_moving else "EN MOUVEMENT",
            "fault":   state == ST_ERROR,
            # ── Rest contact temps réel (GPIO26) ──────────────────────────
            # rest_contact_raw : True=GPIO1=lame EN MOUVEMENT / False=GPIO0=repos
            "rest_contact_raw":   bool(getattr(rte, "rest_contact_raw",   False)),
            "front_blade_cycles": int(getattr(rte, "front_blade_cycles",  0)),
            # ── CRS fault reçu de la trame LIN 0x17 ──────────────────────
            "crs_fault":          int(getattr(rte, "crs_fault",           0x00)),
        }
        msg = json.dumps(payload) + "\n"
        self._last_msg = msg.encode()   # sauvegarde pour nouveaux clients
        self._broadcast(self._last_msg)

    # ─────────────────────────────────────────────────
    # Interne
    # ─────────────────────────────────────────────────

    def _accept_loop(self):
        import time as _time
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass
        # Retry bind : au redemarrage rapide, le port peut etre encore en TIME_WAIT
        for attempt in range(10):
            try:
                srv.bind((TCP_HOST, TCP_PORT))
                break
            except OSError:
                print(f"[TCP] Port {TCP_PORT} occupe, attente 1s (tentative {attempt+1}/10)...")
                _time.sleep(1.0)
        else:
            print(f"[TCP] ERREUR : port {TCP_PORT} toujours occupe apres 10s -- TCP broadcast desactive")
            return
        srv.listen(5)
        srv.settimeout(1.0)
        while self._running:
            try:
                conn, addr = srv.accept()
                print(f"[TCP] Client connecte : {addr}")
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
                print(f"[TCP] Erreur accept : {e}")
        srv.close()

    def _watch_disconnect(self, conn, addr):
        """Detecte la deconnexion d'un client (recv retourne vide)."""
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
            print(f"[TCP] Client deconnecte : {addr}")

    def _broadcast(self, msg: bytes):
        """Envoie msg a tous les clients, retire les morts."""
        dead = []
        with self._clients_lock:
            for c in self._clients:
                try:
                    c.sendall(msg)
                except Exception:
                    dead.append(c)
            for c in dead:
                self._clients.remove(c)