"""
WipeWash — Réseau
Auto-découverte par port : scan du subnet 10.20.0.0/24.
"""

import socket
import threading
import ipaddress


def _get_local_subnets() -> list[str]:
    return ["10.20.0.0/24"]


def _probe(ip_str: str, port: int, results: list, lock: threading.Lock) -> None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.3)
        s.connect((ip_str, port))
        s.close()
        with lock:
            results.append(ip_str)
    except Exception:
        pass


def auto_discover(port: int, timeout: float = 2.0) -> str | None:
    """Scan synchrone — retourne le 1er hôte avec ce port ouvert, ou None."""
    results: list[str] = []
    lock = threading.Lock()
    threads: list[threading.Thread] = []
    for subnet in _get_local_subnets():
        try:
            for ip in ipaddress.ip_network(subnet, strict=False).hosts():
                t = threading.Thread(
                    target=_probe, args=(str(ip), port, results, lock), daemon=True)
                t.start()
                threads.append(t)
        except Exception:
            pass
    for t in threads:
        t.join(timeout=timeout)
    return results[0] if results else None


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
