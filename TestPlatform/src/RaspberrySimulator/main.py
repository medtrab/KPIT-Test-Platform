#!/usr/bin/env python3

import argparse
import logging
import signal
import sys
import threading
import time

# ============================================================
# LOGGING GLOBAL
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("MAIN")

# ============================================================
# ARGUMENTS EN LIGNE DE COMMANDE
# ============================================================
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Systeme de controle essuie-glace",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples:\n"
            "  python3 main.py --mode lin\n"
            "  python3 main.py --mode can\n"
            "  python3 main.py --mode both\n"
            "  python3 main.py --mode both --lin-port 5555 --can-port 5556\n"
            "  python3 main.py --mode lin  --lin-serial /dev/ttyAMA0\n"
        ),
    )
    p.add_argument(
        "--mode",
        choices=["lin", "can", "both"],
        default="both",
        help="Noeud(s) a demarrer (defaut: both)",
    )
    p.add_argument(
        "--host",
        default="0.0.0.0",
        help="Adresse d'ecoute TCP commune (defaut: 0.0.0.0)",
    )
    p.add_argument(
        "--lin-port",
        type=int,
        default=5555,
        metavar="PORT",
        help="Port TCP du noeud LIN (defaut: 5555)",
    )
    p.add_argument(
        "--can-port",
        type=int,
        default=5556,
        metavar="PORT",
        help="Port TCP du noeud CAN (defaut: 5556)",
    )
    p.add_argument(
        "--lin-serial",
        default="/dev/serial0",
        metavar="DEV",
        help="Port serie LIN (defaut: /dev/serial0)",
    )
    p.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Niveau de log (defaut: INFO)",
    )
    return p.parse_args()


# ============================================================
# SURVEILLANCE DES THREADS
# Fonction utilitaire appelee dans la boucle principale.
# ============================================================
def _all_alive(threads: list[threading.Thread]) -> bool:
    """Retourne True si tous les threads de la liste sont encore actifs."""
    return all(t.is_alive() for t in threads)


# ============================================================
# GESTION DES SIGNAUX
# ============================================================
def _build_signal_handler(threads: list[threading.Thread]):
    """
    Construit un handler SIGINT/SIGTERM qui :
      1. Tente un cleanup propre du noeud CAN (si actif)
      2. Attend la fin des threads avec timeout
      3. Force sys.exit()
    """
    def _handler(sig, frame):
        sig_name = "SIGINT" if sig == signal.SIGINT else "SIGTERM"
        log.info("[MAIN] Signal %s recu - arret en cours...", sig_name)

        # Cleanup materiel BCM (libere GPIO, I2C, pigpio, CAN)
        try:
            import bcmcan
            bcmcan.cleanup(sig_name)
        except Exception:
            pass  # bcmcan non charge (mode lin only) ou deja arrete

        # Attendre les threads jusqu'a 3 secondes
        deadline = time.time() + 3.0
        for t in threads:
            remaining = max(0.0, deadline - time.time())
            t.join(timeout=remaining)
            if t.is_alive():
                log.warning("[MAIN] Thread %s toujours actif - arret force", t.name)

        log.info("[MAIN] Arret termine")
        sys.exit(0)

    return _handler


# ============================================================
# POINT D'ENTREE PRINCIPAL
# ============================================================
def main():
    args = _parse_args()

    # Appliquer le niveau de log choisi
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    log.info("=== Systeme essuie-glace - mode: %s ===", args.mode.upper())
    log.info("[MAIN] Host TCP: %s", args.host)

    threads = []   # threads non-daemon lances ci-dessous

    # ----------------------------------------------------------
    # MODE LIN  (noeud CRS LIN esclave)
    # ----------------------------------------------------------
    if args.mode in ("lin", "both"):
        import crslin
        log.info("[MAIN] Noeud LIN -> TCP port %d | serie %s",
                 args.lin_port, args.lin_serial)

        # crslin.start() est bloquant (tcp_server en dernier) :
        # on le lance dans un thread non-daemon pour pouvoir l'attendre.
        t_lin = threading.Thread(
            target=crslin.start,
            kwargs={
                "tcp_host":  args.host,
                "tcp_port":  args.lin_port,
                "lin_port":  args.lin_serial,
            },
            daemon=False,
            name="crslin_main",
        )
        t_lin.start()
        threads.append(t_lin)
        log.info("[MAIN] Thread LIN demarre (id=%d)", t_lin.ident)

    # ----------------------------------------------------------
    # MODE CAN  (noeud BCM capteurs / CAN)
    # ----------------------------------------------------------
    if args.mode in ("can", "both"):
        import bcmcan
        log.info("[MAIN] Noeud CAN -> TCP port %d", args.can_port)

        # bcmcan.start() est bloquant (_main_loop en dernier) :
        # meme traitement que pour crslin.
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
        log.info("[MAIN] Thread CAN demarre (id=%d)", t_can.ident)

    if not threads:
        log.error("[MAIN] Aucun thread demarre - verifier --mode")
        sys.exit(1)

    # Installer les handlers de signaux maintenant que les threads existent
    handler = _build_signal_handler(threads)
    signal.signal(signal.SIGINT,  handler)
    signal.signal(signal.SIGTERM, handler)

    log.info("[MAIN] %d noeud(s) actif(s) - Ctrl+C pour arreter", len(threads))

    # ----------------------------------------------------------
    # BOUCLE DE SURVEILLANCE
    # Permet de detecter un crash de thread et de logger un avertissement.
    # La boucle se termine quand tous les threads sont morts.
    # ----------------------------------------------------------
    try:
        while _all_alive(threads):
            time.sleep(2.0)

        # Si on arrive ici, un thread est mort de maniere inattendue
        dead = [t.name for t in threads if not t.is_alive()]
        log.error("[MAIN] Thread(s) arrete(s) de maniere inattendue: %s", dead)

    except KeyboardInterrupt:
        # Le signal SIGINT est gere par _handler ci-dessus.
        # Ce bloc est present en securite si le handler n'est pas installe.
        log.info("[MAIN] KeyboardInterrupt - arret")

    # Attendre les threads restants avant de quitter
    for t in threads:
        t.join(timeout=5.0)

    log.info("[MAIN] Fin")
    sys.exit(0)


if __name__ == "__main__":
    main()
