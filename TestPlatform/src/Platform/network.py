"""
WipeWash — Réseau
Auto-découverte par port : scan rapide sur 10.20.0.0/28 + 10.20.0.16/28
(couvre .1–.15 et .16–.31, soit 30 IPs au lieu de 254)

Optimisation : auto_discover utilise threading.Event pour sortir dès le
premier hôte trouvé sans attendre le timeout complet (gain ~1.7s typique).
"""

import socket
import threading
import ipaddress


def _get_local_subnets() -> list[str]:
    return ["10.20.0.25/28", "10.20.0.7/28"]


def _probe(ip_str: str, port: int, results: list, lock: threading.Lock,
           found=None) -> None:
    """Sonde une IP:port. Signal found dès le premier succès (early-exit)."""
    if found and found.is_set():
        return   # déjà trouvé → inutile de continuer
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.3)
        s.connect((ip_str, port))
        s.close()
        with lock:
            results.append(ip_str)
        if found:
            found.set()   # réveille auto_discover immédiatement
    except Exception:
        pass


def auto_discover(port: int, timeout: float = 2.0) -> str | None:
    """Scan synchrone — retourne le 1er hôte avec ce port ouvert, ou None.
    Retour immédiat dès le premier hôte trouvé (pas besoin d'attendre timeout)."""
    results: list[str] = []
    lock  = threading.Lock()
    found = threading.Event()
    threads: list[threading.Thread] = []
    for subnet in _get_local_subnets():
        try:
            for ip in ipaddress.ip_network(subnet, strict=False).hosts():
                t = threading.Thread(
                    target=_probe, args=(str(ip), port, results, lock, found),
                    daemon=True)
                t.start()
                threads.append(t)
        except Exception:
            pass
    # Attendre uniquement jusqu'au premier résultat (ou timeout)
    found.wait(timeout=timeout)
    return results[0] if results else None


def auto_discover_all(port: int, timeout: float = 2.0) -> list[str]:
    """
    Scan synchrone — retourne TOUS les hôtes avec ce port ouvert.
    Attend le timeout complet pour collecter toutes les réponses.
    Utilisé par MotorVehicleWorker pour trouver les deux RPi sur port 5000 :
      - RPiBCM (10.20.0.25) : source de l'état moteur (bcm_tcp_broadcast)
      - RPiSIM (10.20.0.7)  : destinataire vehicle/rain/wiper (bcmcan)
    """
    results: list[str] = []
    lock    = threading.Lock()
    threads: list[threading.Thread] = []
    for subnet in _get_local_subnets():
        try:
            for ip in ipaddress.ip_network(subnet, strict=False).hosts():
                t = threading.Thread(
                    target=_probe, args=(str(ip), port, results, lock, None),
                    daemon=True)
                t.start()
                threads.append(t)
        except Exception:
            pass
    # Attendre le timeout complet : on veut TOUS les hôtes
    for t in threads:
        t.join(timeout=timeout)
    return sorted(results)


def scan_async(port: int, progress_cb, done_cb) -> None:
    """
    Scan asynchrone avec callbacks :
      progress_cb(pct: int)          — progression 0..100
      done_cb(hosts: list[str])      — liste triée des hôtes trouvés
    """
    def _run():
        all_ips = []
        for subnet in _get_local_subnets():
            try:
                all_ips.extend(ipaddress.ip_network(subnet, strict=False).hosts())
            except Exception:
                pass

        total = len(all_ips)
        done = [0]
        results: list[str] = []
        lock = threading.Lock()
        threads: list[threading.Thread] = []

        def _probe_progress(ip_str: str, port_: int) -> None:
            _probe(ip_str, port_, results, lock)
            with lock:
                done[0] += 1
                pct = int(done[0] / total * 100) if total else 100
            progress_cb(pct)

        for ip in all_ips:
            t = threading.Thread(
                target=_probe_progress, args=(str(ip), port), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout=1.8)
        done_cb(sorted(results))

    threading.Thread(target=_run, daemon=True).start()