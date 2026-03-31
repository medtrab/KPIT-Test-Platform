#!/usr/bin/env python3
"""
main.py  --  RPi Simulateur WipeWash
======================================
Lance le noeud LIN (CRS esclave) et/ou le noeud CAN (BCM capteurs).

Architecture threads :
  crslin_main   → crslin.start() → TCP server + thread LIN esclave
  bcmcan_main   → bcmcan.start() → TCP server + thread CAN

Modifications v4 (corrections LIN + Redis) :
  - LIN : _lin_read_header et _lin_send_response robustifiés
    (alignement stratégie Arduino CRS v7, drain echo loopback TJA1020)
  - LIN : timing LIN_HDR_TMO=500ms, LIN_BYTE_TMO=10ms, LIN_ECHO_TMO=15ms
  - Redis : reconnexion automatique, connexions isolees T-REDIS/T-REDIS-CMD
  - --mode lin/can/both (defaut: both)
  - Signal SIGINT/SIGTERM propre
  - Thread de surveillance avec redemarrage automatique

Usage :
  python3 main.py                            # LIN + CAN (defaut)
  python3 main.py --mode lin                 # CRS LIN esclave seul
  python3 main.py --mode can                 # Noeud CAN seul
  python3 main.py --lin-port /dev/ttyAMA0    # port serie personnalise
  python3 main.py --log-level DEBUG          # traces LIN detaillees
"""

import argparse
import logging
import signal
import sys
import threading
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("MAIN")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="RPi Simulateur WipeWash (LIN + CAN)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples:\n"
            "  python3 main.py                      # LIN + CAN (defaut)\n"
            "  python3 main.py --mode lin            # CRS LIN esclave seul\n"
            "  python3 main.py --mode can            # Noeud CAN seul\n"
            "  python3 main.py --lin-port /dev/ttyAMA0\n"
        ),
    )
    p.add_argument("--mode",     choices=["lin", "can", "both"], default="both",
                   help="Noeuds a demarrer (defaut: both)")
    p.add_argument("--host",     default="0.0.0.0",
                   help="Adresse TCP commune (defaut: 0.0.0.0)")
    p.add_argument("--lin-port", type=int, default=5555, metavar="PORT",
                   help="Port TCP LIN (defaut: 5555)")
    p.add_argument("--can-port", type=int, default=5000, metavar="PORT",
                   help="Port TCP CAN (defaut: 5000)")
    p.add_argument("--lin-serial", default="/dev/serial0", metavar="DEV",
                   help="Port serie LIN (defaut: /dev/serial0)")
    p.add_argument("--log-level", choices=["DEBUG","INFO","WARNING","ERROR"],
                   default="INFO")
    return p.parse_args()


def _build_signal_handler(threads):
    def _handler(sig, frame):
        sig_name = "SIGINT" if sig == signal.SIGINT else "SIGTERM"
        log.info("[MAIN] %s recu - arret...", sig_name)
        try:
            import bcmcan
            bcmcan.cleanup(sig_name)
        except Exception:
            pass
        deadline = time.time() + 3.0
        for t in threads:
            remaining = max(0.0, deadline - time.time())
            t.join(timeout=remaining)
        log.info("[MAIN] Arret termine")
        sys.exit(0)
    return _handler


def main():
    args = _parse_args()
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    log.info("=== RPi Simulateur WipeWash -- mode: %s ===", args.mode.upper())
    log.info("[MAIN] Host TCP: %s", args.host)

    threads = []

    # ── Noeud LIN (CRS esclave) ──────────────────────────────────────
    if args.mode in ("lin", "both"):
        import crslin
        log.info("[MAIN] Noeud LIN -> TCP:%d | serie:%s",
                 args.lin_port, args.lin_serial)
        t_lin = threading.Thread(
            target=crslin.start,
            kwargs={
                "tcp_host": args.host,
                "tcp_port": args.lin_port,
                "lin_port": args.lin_serial,
            },
            daemon=False,
            name="crslin_main",
        )
        t_lin.start()
        threads.append(t_lin)
        log.info("[MAIN] Thread LIN demarre")

    # ── Noeud CAN (BCM capteurs) ─────────────────────────────────────
    if args.mode in ("can", "both"):
        import bcmcan
        log.info("[MAIN] Noeud CAN -> TCP:%d", args.can_port)
        t_can = threading.Thread(
            target=bcmcan.start,
            kwargs={
                "tcp_host": args.host,
                "tcp_port": args.can_port,
            },
            daemon=False,
            name="bcmcan_main",
        )
        t_can.start()
        threads.append(t_can)
        log.info("[MAIN] Thread CAN demarre")

        # ── WC DoIP/DTC (lance avec le noeud CAN) ────────────────────
        import wc_doip
        t_wc = threading.Thread(
            target=wc_doip.start,
            daemon=True,
            name="wc_doip_main",
        )
        t_wc.start()
        log.info("[MAIN] Thread WC DoIP/DTC demarre (port %d, addr 0x0701)",
                 wc_doip.DOIP_PORT)

    if not threads:
        log.error("[MAIN] Aucun thread -- verifier --mode")
        sys.exit(1)

    handler = _build_signal_handler(threads)
    signal.signal(signal.SIGINT,  handler)
    signal.signal(signal.SIGTERM, handler)

    log.info("[MAIN] %d noeud(s) actif(s) -- Ctrl+C pour arreter", len(threads))

    try:
        while True:
            time.sleep(2.0)
            dead = [t for t in threads if not t.is_alive()]
            if dead:
                dead_names = [t.name for t in dead]
                log.error("[MAIN] Thread(s) mort(s) inattendu(s): %s", dead_names)
                # Un thread mort = arret total pour forcer redemarrage propre
                break
    except KeyboardInterrupt:
        log.info("[MAIN] KeyboardInterrupt - arret")

    for t in threads:
        t.join(timeout=5.0)

    log.info("[MAIN] Fin")
    sys.exit(0)


if __name__ == "__main__":
    main()
