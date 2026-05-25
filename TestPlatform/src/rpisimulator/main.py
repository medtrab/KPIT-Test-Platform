#!/usr/bin/env python3

"""
main.py  RPi Simulateur WipeWash

Lance le noeud LIN CRS esclave et ou le noeud CAN BCM capteurs

Architecture threads :
  crslin_main   > crslin.start() > TCP server + thread LIN esclave
  bcmcan_main   > bcmcan.start() > TCP server + thread CAN

Modifications v4 :
  LIN robustifie lecture et reponse
  Redis reconnexion automatique
  mode lin can both defaut both
  Signal SIGINT SIGTERM propre
  Thread surveillance avec redemarrage

Usage :
  python3 main.py
  python3 main.py mode lin
  python3 main.py mode can
  python3 main.py linport devttyAMA0
  python3 main.py loglevel DEBUG
"""

import argparse
import os
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


def parse_args():
    p = argparse.ArgumentParser(
        description="RPi Simulateur WipeWash LIN CAN",
    )
    p.add_argument("mode", choices=["lin", "can", "both"], default="both",
                   nargs="?", help="Noeuds a demarrer")
    p.add_argument("host", default="0.0.0.0", nargs="?",
                   help="Adresse TCP")
    p.add_argument("linport", type=int, default=5555, nargs="?",
                   help="Port TCP LIN")
    p.add_argument("canport", type=int, default=5000, nargs="?",
                   help="Port TCP CAN")
    p.add_argument("linserial", default="/dev/serial0", nargs="?",
                   help="Port serie LIN")
    p.add_argument("bcmhost", default="", nargs="?",
                   help="IP BCM")
    p.add_argument("--loglevel", choices=["DEBUG","INFO","WARNING","ERROR"],
                   default="INFO")
    p.add_argument("--ldf", default="wiperwash.ldf",
                   help="Chemin vers le fichier LDF (défaut: wiperwash.ldf)")
    p.add_argument("--dbc", default="wiperwash.dbc",
                   help="Chemin vers le fichier DBC (défaut: wiperwash.dbc)")
    return p.parse_args()


def build_signal_handler(threads, stop_event):
    def handler(sig, frame):
        sig_name = "SIGINT" if sig == signal.SIGINT else "SIGTERM"
        log.info("MAIN signal recu arret %s", sig_name)
        try:
            import bcmcan
            bcmcan.cleanup(sig_name)
        except Exception:
            pass
        stop_event.set()
    return handler


def main():
    args = parse_args()
    logging.getLogger().setLevel(getattr(logging, args.loglevel))

    log.info("RPi Simulateur WipeWash mode %s", args.mode.upper())
    log.info("Host TCP %s", args.host)

    threads = []

    if args.mode in ("lin", "both"):
        import crslin
        log.info("Noeud LIN TCP %d serie %s",
                 args.linport, args.linserial)
        t_lin = threading.Thread(
            target=crslin.start,
            kwargs={
                "tcp_host": args.host,
                "tcp_port": args.linport,
                "lin_port": args.linserial,
                "ldf_path": args.ldf,
            },
            daemon=True,
            name="crslin_main",
        )
        t_lin.start()
        threads.append(t_lin)
        log.info("Thread LIN demarre")

    if args.mode in ("can", "both"):
        import bcmcan
        log.info("Noeud CAN TCP %d", args.canport)
        t_can = threading.Thread(
            target=bcmcan.start,
            kwargs={
                "tcp_host": args.host,
                "tcp_port": args.canport,
                "bcm_host": args.bcmhost,
                "dbc_path": args.dbc,
            },
            daemon=True,
            name="bcmcan_main",
        )
        t_can.start()
        threads.append(t_can)
        log.info("Thread CAN demarre")

        import wc_doip
        t_wc = threading.Thread(
            target=wc_doip.start,
            daemon=True,
            name="wc_doip_main",
        )
        t_wc.start()
        log.info("Thread WC DoIP demarre port %d",
                 wc_doip.DOIP_TCP_PORT)

        from xcp_server_wc import XCPServerWC
        t_xcp_wc = threading.Thread(
            target=XCPServerWC().run,
            daemon=True,
            name="T-XCP-WC-SRV",
        )
        t_xcp_wc.start()
        log.info("Thread XCP WC server demarre port 17726")

    if not threads:
        log.error("Aucun thread verifier mode")
        sys.exit(1)

    stop_event = threading.Event()
    handler = build_signal_handler(threads, stop_event)
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    log.info("Noeuds actifs %d Ctrl C pour arreter", len(threads))

    while not stop_event.is_set():
        stop_event.wait(timeout=2.0)
        dead = [t for t in threads if not t.is_alive()]
        if dead:
            log.error("Thread mort %s", [t.name for t in dead])
            break

    log.info("MAIN arret termine")
    deadline = time.time() + 3.0
    for t in threads:
        t.join(timeout=max(0.0, deadline - time.time()))

    log.info("Fin")
    os._exit(0)


if __name__ == "__main__":
    main()