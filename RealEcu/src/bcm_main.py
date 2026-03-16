#!/usr/bin/env python3
import threading
import time

from bcm_rte         import RTE
from bcm_protocol    import ProtocolLayer, DOIP_PORT, DSC_DEFAULT
from bcm_application import ApplicationLayer
from dtc_manager     import DTCManager


class BCM:
    """
    Facade principale -- assemble les 3 couches.
    Responsabilite de BCM :
      1. Instancier RTE, DTCManager, ProtocolLayer, ApplicationLayer
      2. Appeler init_can() et init_lin() sur ProtocolLayer
      3. Lancer les 5 threads daemon
      4. Appeler protocol.run() (boucle TCP bloquante)
      5. Gerer l'arret propre (Ctrl+C)
    """

    def __init__(self):
        print("=" * 60)
        print("  BCM WipeWash -- Architecture 3 Couches ")
        print("  LIN = CAN = DoIP : tous passent par le RTE")
        print("=" * 60)

        # ── Couche RTE (memoire partagee) ────────────────
        self.rte = RTE()
        self.dtc = DTCManager()
        print("[BCM] RTE + DTC initialises")

        # ── Couche Protocole (LIN + CAN + DoIP) ──────────
        self.protocol = ProtocolLayer(self.rte, self.dtc)
        self.protocol.init_can()
        self.protocol.init_lin()
        print("[BCM] Couche Protocole initialisee (LIN + CAN + DoIP)")

        # ── Couche Application (WSM + Diagnostic) ────────
        self.app = ApplicationLayer(self.rte, self.dtc)
        print("[BCM] Couche Application initialisee")

        print("=" * 60)
        print(f"  LIN        : 19.2kbps | Cycle 0x16=20ms | Cycle 0x17=100ms")
        print(f"  CAN        : can0")
        print(f"  DoIP       : port {DOIP_PORT} (TCP + UDP)")
        print(f"  RainSensor : {'OUI' if self.rte.rain_sensor_installed else 'NON'}")
        print(f"  RearWiper  : {'OUI' if self.rte.rear_wiper_available else 'NON'}")
        print("=" * 60)
        print()
        print("  Flux de donnees uniforme :")
        print("  LIN  trame → rte.crs_wiper_op  → T-WSM  agit")
        print("  CAN  trame → rte.rain_intensity → T-WSM  agit")
        print("  DoIP trame → rte.uds_payload    → T-DIAG traite → T-DOIP renvoie")
        print("=" * 60)

    # --------------------------------------------------
    # Demarrage
    # --------------------------------------------------

    def start(self):
        """
        Lance tous les threads puis demarre la boucle TCP DoIP (bloquante).

        Ordre de demarrage :
          1. protocol.start() → _running=True + thread UDP discovery
          2. app.start()      → _running=True
          3. Threads T-LIN, T-CAN, T-WSM, T-PUMP, T-DIAG (daemon)
          4. protocol.run()   → boucle TCP DoIP (bloquant, thread principal)
        """
        # Activer les couches
        self.protocol.start()   # _running=True + thread UDP DoIP discovery
        self.app.start()        # _running=True

        # Definition des 5 threads daemon
        threads = [
            ("T-LIN",  self.protocol.thread_lin_scheduler),
            ("T-CAN",  self.protocol.thread_can_receiver),
            ("T-WSM",  self.app.thread_wsm_control),
            ("T-PUMP", self.app.thread_pump_guard),
            ("T-DIAG", self.app.thread_diagnostic),
        ]

        print("[BCM] Lancement des threads :")
        for name, target in threads:
            t = threading.Thread(target=target, daemon=True, name=name)
            t.start()
            print(f"  + Thread {name} demarre")

        print("[BCM] Tous les threads actifs.\n")
        print("[BCM] Boucle TCP DoIP -- demarrage maintenant (bloquant)")

        try:
            # Boucle TCP bloquante -- remplace DoIPServer.run()
            self.protocol.run()
        except KeyboardInterrupt:
            print("\n[BCM] Arret (Ctrl+C)...")
        finally:
            self._shutdown()

    def _shutdown(self):
        """Arret propre de toutes les couches."""
        print("[BCM] Arret en cours...")
        self.app.stop()         # arrete moteurs + GPIO
        self.protocol.stop()    # ferme TCP + CAN + LIN
        print("[BCM] Arrete proprement")


if __name__ == "__main__":
    bcm = BCM()
    bcm.start()