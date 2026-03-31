"""
test_runner.py  —  Moteur d'exécution des tests automatiques WipeWash
======================================================================
Se branche sur les workers Qt existants (CANWorker, LINWorker, MotorWorker)
via leurs signaux. Exécute les tests séquentiellement depuis ALL_TESTS.

Modifications :
  - pump_signal : connecté à _on_motor pour T21.
  - rte_client  : RTEClient Redis injecté dans BaseBCMTest.rte_client
                  avant chaque test T30-T39. Permet GET/SET direct sur
                  le RTE du RpiBCM sans TCP LIN/CAN.
  - _tick       : appelle _check_rte() sur les tests BaseBCMTest
                  en plus de check_timeout().
"""

import time
import threading
from typing import List, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, Qt

from test_cases import BaseTest, BaseBCMTest, TestResult, ALL_TESTS


class TestRunner(QObject):
    # ─── Signaux publics ──────────────────────────────────────────────
    test_started = pyqtSignal(str, str)   # (test_id, test_name)
    test_result  = pyqtSignal(object)     # TestResult
    all_done     = pyqtSignal(list)       # list[TestResult]
    progress     = pyqtSignal(int, int)   # (done, total)
    log_msg      = pyqtSignal(str)        # message texte libre

    def __init__(self, can_worker, lin_worker, motor_worker,
                 pump_signal=None, rte_client=None, parent=None):
        super().__init__(parent)
        self._can_w      = can_worker
        self._lin_w      = lin_worker
        self._motor_w    = motor_worker
        self._rte_client = rte_client   # RTEClient Redis (optionnel)

        self._queue  : List[BaseTest]   = []
        self._current: Optional[BaseTest] = None
        self._results: List[TestResult] = []
        self._running  = False
        self._total    = 0

        # DirectConnection : appel immédiat dans le thread GUI
        # (évite la queue Qt qui ne serait jamais drainée par la boucle Python)
        dc = Qt.ConnectionType.DirectConnection
        can_worker.can_received    .connect(self._on_can,   dc)
        lin_worker.lin_received    .connect(self._on_lin,   dc)
        motor_worker.motor_received.connect(self._on_motor, dc)

        # Connexion pompe pour T21 :
        # PumpDataClient émet pump_signal.data_received avec les données
        # de TCPPumpBroadcast :5556 → {"state":"FORWARD"/"OFF", ...}
        # T21.on_motor_data() attend exactement ce format.
        # Sans cette connexion, T21 ne reçoit jamais les données pompe
        # car elles transitent par pump_signal et non motor_received.
        if pump_signal is not None:
            pump_signal.data_received.connect(self._on_motor, dc)

        # Timer de supervision timeout (toutes les 200 ms)
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)

    # ─── API publique ─────────────────────────────────────────────────
    def run_all(self):
        self._queue   = [cls() for cls in ALL_TESTS]
        self._results = []
        self._total   = len(self._queue)
        self._running = True
        self._timer.start()
        self._start_next()

    def run_selected(self, ids: list):
        self._queue   = [cls() for cls in ALL_TESTS if cls.ID in ids]
        self._results = []
        self._total   = len(self._queue)
        self._running = True
        self._timer.start()
        self._start_next()

    def stop(self):
        self._running = False
        self._timer.stop()
        self._current = None
        self.log_msg.emit("⏹ Tests interrompus")

    # ─── Logique interne ──────────────────────────────────────────────
    def _start_next(self):
        if not self._queue:
            self._running = False
            self._timer.stop()
            n_pass = sum(1 for r in self._results if r.status == "PASS")
            n_fail = sum(1 for r in self._results if r.status == "FAIL")
            n_to   = sum(1 for r in self._results if r.status == "TIMEOUT")
            self.all_done.emit(self._results)
            self.log_msg.emit(
                f"✅ Terminé — PASS:{n_pass}  FAIL:{n_fail}  TIMEOUT:{n_to}")
            return

        self._current = self._queue.pop(0)
        self.log_msg.emit(f"▶ [{self._current.ID}]  {self._current.NAME}")
        self.test_started.emit(self._current.ID, self._current.NAME)
        # Injecter rte_client dans les tests BCM (T30-T39)
        if isinstance(self._current, BaseBCMTest):
            BaseBCMTest.rte_client = self._rte_client
        self._current.start()
        tid_cur = self._current.ID
        # Après T21 (FRONT_WASH), le BCM met ~2s à terminer les cycles
        # et revenir en OFF. Attendre avant le pre_test des tests suivants.
        if tid_cur in ("T30","T31","T32","T33","T34","T35","T36","T37","T38","T39"):
            last = getattr(self, "_last_tid", "")
            delay = 2500 if last in ("T20", "T21", "T36", "T37") else 0
            if delay:
                QTimer.singleShot(delay, lambda t=self._current: self._pre_test_delayed(t))
            else:
                # Garantir que BCM est en OFF avant d'envoyer le stimulus
                # en resettant wc_timeout/lin_timeout résiduels
                if self._rte_client:
                    self._rte_client.set_cmd("wc_timeout_active",  False)
                    self._rte_client.set_cmd("lin_timeout_active", False)
                    self._rte_client.set_cmd("crs_wiper_op", 0)
                    self._rte_client.set_cmd("ignition_status", 1)
                    QTimer.singleShot(300, lambda t=self._current: self._pre_test_delayed(t))
                else:
                    self._pre_test(self._current)
        else:
            self._pre_test(self._current)
        self._last_tid = tid_cur
        done = self._total - len(self._queue) - 1
        self.progress.emit(done, self._total)

    def _pre_test_delayed(self, test: BaseTest):
        """Appelé avec délai pour laisser le BCM se stabiliser en OFF."""
        if self._current is test:
            self._pre_test(test)

    def _pre_test(self, test: BaseTest):
        """Envoie les commandes préalables selon le type de test."""
        tid = test.ID

        # ── Tests réseau ──────────────────────────────────────────────────
        if tid == "T10":
            self.log_msg.emit("  → stop_lin_tx")
            self._lin_w.queue_send({"test_cmd": "stop_lin_tx"})
        elif tid == "T11":
            self.log_msg.emit("  → stop_can_tx")
            self._motor_w.queue_send({"test_cmd": "stop_can_tx"})
        elif tid == "T20":
            self.log_msg.emit("  → commande TOUCH (reset baseline cycles)")
            # Reset état BCM avant TOUCH — s'assurer que rest_contact_sim est OFF
            if self._rte_client:
                self._rte_client.set_cmd("rest_contact_sim_active", False)
                self._rte_client.set_cmd("rest_contact_sim",        False)
                self._rte_client.set_cmd("crs_wiper_op", 0)
            # Sync simulateur LIN en TOUCH
            self._lin_w.queue_send({"cmd": "TOUCH"})
            # Délai 200ms pour que BCM soit bien en OFF avant TOUCH
            def _send_touch():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # FIX T20 : injecter True (lame EN MOUVEMENT) AVANT crs_wiper_op=1.
                # Le BCM lit _read_rest_contact() dans _enter_touch() pour initialiser
                # _rest_contact_prev. Si on injecte False (repos), _process_touch()
                # détecte rest_detected=True dès le 1er cycle WSM (200ms) et termine
                # immédiatement → durée ~0ms au lieu de ~1500ms (< TOUCH_DURATION).
                # Avec True, _rest_contact_prev=True et la fin de cycle n'est détectée
                # que sur la transition True→False à ~1500ms → durée correcte.
                if self._rte_client:
                    self._rte_client.set_cmd("rest_contact_sim_active", True)
                    self._rte_client.set_cmd("rest_contact_sim", True)   # lame EN MOUVEMENT
                    self._rte_client.set_cmd("crs_wiper_op", 1)          # WOP_TOUCH
                # Retour repos à 1500ms → BCM détecte rest_detected=True → fin normale
                # → durée ~1500ms < TOUCH_DURATION=1700ms → PASS (SRD_WW_021)
                QTimer.singleShot(1500, lambda: self._rte_client and
                    self._rte_client.set_cmd("rest_contact_sim", False))
            QTimer.singleShot(200, _send_touch)
        elif tid == "T21":
            self.log_msg.emit("  → pompe FORWARD via FRONT_WASH (Redis)")
            if self._rte_client:
                # Activer simulation rest_contact : BCM _process_front_wash utilisera
                # le comptage par contact repos plutôt que le fallback par temps.
                # T21 mesure uniquement la durée pompe (pump_active True→False),
                # mais il faut que les 3 cycles lame se déroulent correctement
                # pour que FRONT_WASH se termine normalement via wash_cycles_done=3.
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim", False)  # repos initial
                self._rte_client.set_cmd("crs_wiper_op", 5)           # WOP_FRONT_WASH
                self._lin_w.queue_send({"cmd": "FRONT_WASH"})
                # Simuler 3 cycles lame (même séquence que T36)
                for cycle in range(3):
                    b = cycle * 1700
                    QTimer.singleShot(b + 150,  lambda b=b: self._rte_client and
                        self._rte_client.set_cmd("rest_contact_sim", True))
                    QTimer.singleShot(b + 1600, lambda b=b: self._rte_client and
                        self._rte_client.set_cmd("rest_contact_sim", False))
            else:
                self._lin_w.queue_send({"cmd": "FRONT_WASH"})

        # ── Tests WSM BCM — Redis SET ─────────────────────────────────────
        elif tid == "T30":
            self.log_msg.emit("  → Redis SET crs_wiper_op=SPEED1")
            if hasattr(test, "reset_t0"): test.reset_t0()
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op", 2)
            else:
                self._lin_w.queue_send({"cmd": "SPEED1"})

        elif tid == "T31":
            self.log_msg.emit("  → Redis SET crs_wiper_op=SPEED2")
            if hasattr(test, "reset_t0"): test.reset_t0()
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op", 3)
            else:
                self._lin_w.queue_send({"cmd": "SPEED2"})

        elif tid == "T32":
            self.log_msg.emit("  → Redis SET SPEED1 puis OFF")
            # BUG FIX T32 : reset_t0() doit être appelé AU MOMENT de la commande OFF,
            # pas avant SPEED1. Sinon la mesure inclut les 300 ms d'attente + montée
            # SPEED1, ce qui gonfle artificiellement la durée mesurée (~2338 ms).
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op", 2)
                def _send_off_with_t0():
                    if hasattr(test, "reset_t0"):
                        test.reset_t0()
                    self._rte_client.set_cmd("crs_wiper_op", 0)
                QTimer.singleShot(300, _send_off_with_t0)
            else:
                self._lin_w.queue_send({"cmd": "SPEED1"})
                def _send_off_lin_with_t0():
                    if hasattr(test, "reset_t0"):
                        test.reset_t0()
                    self._lin_w.queue_send({"cmd": "OFF"})
                QTimer.singleShot(300, _send_off_lin_with_t0)

        elif tid == "T33":
            self.log_msg.emit("  → Redis SET SPEED1 puis ignition=0")
            if hasattr(test, "reset_t0"): test.reset_t0()
            if self._rte_client:
                # S'assurer que BCM sort de ERROR si besoin, puis SPEED1, puis ignition=0
                self._rte_client.set_cmd("crs_wiper_op", 0)          # force OFF d'abord
                QTimer.singleShot(200, lambda: self._rte_client and
                    self._rte_client.set_cmd("crs_wiper_op", 2))      # SPEED1
                QTimer.singleShot(600, lambda: self._rte_client and (
                    test.reset_t0() or
                    self._rte_client.set_cmd("ignition_status", 0)))  # ignition=0
            else:
                self._lin_w.queue_send({"cmd": "SPEED1"})
                QTimer.singleShot(400, lambda: self._motor_w.queue_send(
                    {"ignition_status": 0, "reverse_gear": 0, "vehicle_speed": 0}))

        elif tid == "T34":
            self.log_msg.emit("  → Redis SET AUTO + rain=10")
            if self._rte_client:
                # FIX T34/T35 : synchroniser RPiSIM en AUTO via LIN AVANT
                # l'injection Redis. Sans cela, LIN 0x16 continue à envoyer
                # WiperOp=OFF toutes les 400ms et _lin_poll_0x16() écrase
                # crs_wiper_op=AUTO → BCM repasse en OFF → TIMEOUT.
                self._lin_w.queue_send({"cmd": "AUTO"})
                self._rte_client.set_cmd("rain_sensor_installed", True)
                self._rte_client.set_cmd("rain_intensity", 10)
                self._motor_w.queue_send({"rain_intensity": 10, "sensor_status": "OK"})
                # Activer simulation rest_contact pour que _process_auto →
                # _track_blade_cycle fonctionne (REST_CONTACT_HARDWARE_PRESENT=False).
                # En AUTO/Speed, le moteur tourne en continu → on simule un cycle
                # répété : True (mouvement) pendant 1550ms, False (repos) 150ms.
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim", False)
                QTimer.singleShot(200, lambda: self._rte_client and (
                    test.reset_t0() or
                    self._rte_client.set_cmd("crs_wiper_op", 4)))
                # Simuler cycles continus (suffisamment pour la durée du test ~1.5s)
                for cycle in range(3):
                    b = 300 + cycle * 1700
                    QTimer.singleShot(b,        lambda b=b: self._rte_client and
                        self._rte_client.set_cmd("rest_contact_sim", True))
                    QTimer.singleShot(b + 1550, lambda b=b: self._rte_client and
                        self._rte_client.set_cmd("rest_contact_sim", False))
            else:
                self._lin_w.queue_send({"cmd": "AUTO"})
                self._motor_w.queue_send({"rain_intensity": 10, "sensor_status": "OK"})
        elif tid == "T35":
            self.log_msg.emit("  → Redis SET AUTO + rain=25")
            if self._rte_client:
                # FIX T35 : même correctif que T34.
                self._lin_w.queue_send({"cmd": "AUTO"})
                self._rte_client.set_cmd("rain_sensor_installed", True)
                self._rte_client.set_cmd("rain_intensity", 25)
                self._motor_w.queue_send({"rain_intensity": 25, "sensor_status": "OK"})
                # Activer simulation rest_contact (même logique que T34)
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim", False)
                QTimer.singleShot(200, lambda: self._rte_client and (
                    test.reset_t0() or
                    self._rte_client.set_cmd("crs_wiper_op", 4)))
                for cycle in range(3):
                    b = 300 + cycle * 1700
                    QTimer.singleShot(b,        lambda b=b: self._rte_client and
                        self._rte_client.set_cmd("rest_contact_sim", True))
                    QTimer.singleShot(b + 1550, lambda b=b: self._rte_client and
                        self._rte_client.set_cmd("rest_contact_sim", False))
            else:
                self._lin_w.queue_send({"cmd": "AUTO"})
                self._motor_w.queue_send({"rain_intensity": 25, "sensor_status": "OK"})

        # ─── REMPLACEMENT T36 ─────────────────────────────────────────────
        elif tid == "T36":
            self.log_msg.emit("  → Redis SET crs_wiper_op=FRONT_WASH (reset cycles)")
            # FIX T36 : synchroniser RPiSIM LIN slave en FRONT_WASH AVANT Redis.
            # Sans cela, RPiSIM continue à renvoyer WiperOp=OFF dans chaque
            # frame LIN 0x16 (400ms) → _lin_poll_0x16() écrit crs_wiper_op=0
            # → WSM sort de WASH_FRONT immédiatement.
            # FIX T36 rest_contact : reset crs_wiper_op=0 d'abord pour que le BCM
            # remette _front_blade_cycles=0 via _enter_off() avant FRONT_WASH.
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op", 0)   # force OFF → reset _front_blade_cycles
            self._motor_w.queue_send({"ignition_status": 1, "vehicle_speed": 0})

            def _send_front_wash():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # Activer simulation rest_contact pour T36
                # Le runner va simuler 3 cycles complets :
                #   False → True → False (cycle 1)
                #   False → True → False (cycle 2)
                #   False → True → False (cycle 3)
                # Chaque cycle = WIPE_CYCLE_DURATION = 1700ms
                if self._rte_client:
                    self._rte_client.set_cmd("rest_contact_sim_active", True)
                    self._rte_client.set_cmd("rest_contact_sim", False)  # repos initial
                    self._rte_client.set_cmd("crs_wiper_op", 5)          # WOP_FRONT_WASH
                    self._lin_w.queue_send({"cmd": "FRONT_WASH"})
                    # Simuler 3 cycles via transitions temporisées
                    # Chaque cycle : 150ms → True (départ) puis 1550ms → False (retour repos)
                    # IMPORTANT : capturer b par valeur dans la lambda (pas par référence)
                    for cycle in range(3):
                        b = cycle * 1700
                        QTimer.singleShot(b + 150,  lambda b=b: self._rte_client and
                            self._rte_client.set_cmd("rest_contact_sim", True))
                        QTimer.singleShot(b + 1600, lambda b=b: self._rte_client and
                            self._rte_client.set_cmd("rest_contact_sim", False))

            QTimer.singleShot(400, _send_front_wash)

        # ─── REMPLACEMENT T37 ─────────────────────────────────────────────
        elif tid == "T37":
            self.log_msg.emit("  → Redis SET crs_wiper_op=REAR_WASH")
            # FIX T37 : sync RPiSIM LIN slave en REAR_WASH AVANT Redis.
            # Sans cela, RPiSIM continue à renvoyer WiperOp=OFF dans chaque
            # frame LIN 0x16 → _lin_poll_0x16() écrase crs_wiper_op → BCM
            # quitte REAR_WASH avant activation pompe + cycles arrière.
            self._lin_w.queue_send({"cmd": "REAR_WASH"})
            self._motor_w.queue_send({"ignition_status": 1, "vehicle_speed": 0})
            if hasattr(test, "reset_t0"): test.reset_t0()
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op", 6)

        elif tid == "T38":
            self.log_msg.emit("  → Redis SET SPEED1 + injection surcourant")
            if self._rte_client:
                # FIX T38 : synchroniser le simulateur RPiSIM en SPEED1 AVANT
                # l'injection Redis. Sans cela, RPiSIM continue à envoyer
                # WiperOp=OFF dans chaque frame LIN 0x16 (toutes les 400ms).
                # _lin_poll_0x16() écrit alors rte.crs_wiper_op=0, ce qui
                # déclenche SPEED1→OFF avant que _check_overcurrent() puisse
                # détecter motor_current_a=0.95 → TIMEOUT T38.
                self._lin_w.queue_send({"cmd": "SPEED1"})
                self._rte_client.set_cmd("crs_wiper_op", 2)
                # Injecter motor_current_a=0.95A après 400ms
                # (laisser BCM entrer en SPEED1 + LIN stabilisée)
                QTimer.singleShot(400, lambda: self._rte_client and
                    self._rte_client.set_cmd("motor_current_a", 0.95))
            else:
                self._lin_w.queue_send({"cmd": "SPEED1"})
                QTimer.singleShot(400, lambda: self._inject_overcurrent(6))

        elif tid == "T39":
            self.log_msg.emit("  → Redis SET SPEED1 puis stop_lin_tx")
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op", 2)
            else:
                self._lin_w.queue_send({"cmd": "SPEED1"})
            QTimer.singleShot(300, lambda: self._lin_w.queue_send(
                {"test_cmd": "stop_lin_tx"}))

    def _inject_overcurrent(self, remaining: int):
        """Injecte motor_current=0.95A dans motor_received toutes les 50 ms."""
        if remaining <= 0 or not self._running:
            return
        self._motor_w.motor_received.emit(
            {"state": "SPEED1", "motor_current": 0.95,
             "front_motor_on": True, "fault": False})
        QTimer.singleShot(50, lambda: self._inject_overcurrent(remaining - 1))

    def _post_test(self, test: BaseTest):
        """Restaure l'état nominal après un test actif."""
        tid = test.ID
        if tid == "T10":
            self.log_msg.emit("  → start_lin_tx")
            self._lin_w.queue_send({"test_cmd": "start_lin_tx"})
            if self._rte_client:
                QTimer.singleShot(300, lambda: self._rte_client and
                    self._rte_client.set_cmd("lin_timeout_active", False))
        elif tid == "T11":
            self.log_msg.emit("  → start_can_tx")
            self._motor_w.queue_send({"test_cmd": "start_can_tx"})
            if self._rte_client:
                # Attendre que bcmcan reprenne 0x201, puis reset wc_timeout
                QTimer.singleShot(300, lambda: self._rte_client and
                    self._rte_client.set_cmd("wc_timeout_active", False))
                QTimer.singleShot(600, lambda: self._rte_client and (
                    self._rte_client.set_cmd("ignition_status", 1) or
                    self._rte_client.set_cmd("crs_wiper_op", 0)
                ))
        elif tid == "T21":
            if self._rte_client:
                self._rte_client.set_cmd("rest_contact_sim_active", False)
                self._rte_client.set_cmd("rest_contact_sim",        False)
                self._rte_client.set_cmd("crs_wiper_op", 0)
                self._lin_w.queue_send({"cmd": "OFF"})
            else:
                self._lin_w.queue_send({"cmd": "OFF"})
        elif tid == "T20":
            # Remettre BCM en OFF + simulateur LIN en OFF après TOUCH
            # Désactiver simulation rest_contact → retour lecture GPIO hardware
            if self._rte_client:
                self._rte_client.set_cmd("rest_contact_sim_active", False)
                self._rte_client.set_cmd("rest_contact_sim",        False)
                self._rte_client.set_cmd("crs_wiper_op", 0)
            self._lin_w.queue_send({"cmd": "OFF"})
        elif tid in ("T30", "T31", "T32", "T34", "T35", "T36", "T37", "T38"):
            # Remettre le BCM en OFF via Redis ou LIN
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op", 0)
                self._rte_client.set_cmd("ignition_status", 1)
                self._rte_client.set_cmd("rain_intensity", 0)
                if tid in ("T34", "T35"):
                    self._rte_client.set_cmd("rain_sensor_installed", False)
                    # FIX T34/T35 cleanup : remettre rain=0 dans RPiSIM pour que
                    # CAN 0x301 ne continue pas à diffuser rain>0 aux tests suivants.
                    self._motor_w.queue_send({"rain_intensity": 0, "sensor_status": "OK"})
                    # Désactiver simulation rest_contact activée dans _pre_test T34/T35
                    self._rte_client.set_cmd("rest_contact_sim_active", False)
                    self._rte_client.set_cmd("rest_contact_sim",        False)
                    self._lin_w.queue_send({"cmd": "OFF"})
                if tid == "T37":
                    # FIX T37 cleanup : arrêter pompe BACKWARD + simulateur en OFF
                    self._lin_w.queue_send({"cmd": "OFF"})
                if tid == "T36":
                    # FIX T36 cleanup : remettre simulateur LIN en OFF après FRONT_WASH.
                    # Désactiver simulation rest_contact → retour lecture GPIO hardware.
                    self._rte_client.set_cmd("rest_contact_sim_active", False)
                    self._rte_client.set_cmd("rest_contact_sim",        False)
                    self._lin_w.queue_send({"cmd": "OFF"})
                if tid == "T38":
                    self._rte_client.set_cmd("motor_current_a", 0.0)
                    self._rte_client.set_cmd("wc_timeout_active", False)
                    # FIX T38 cleanup : remettre simulateur en OFF
                    # pour que LIN 0x16 cesse d'envoyer WiperOp=SPEED1
                    self._lin_w.queue_send({"cmd": "OFF"})
                    # front_motor_error est remis à False automatiquement par _enter_off()
                    # (clé non inscriptible depuis la Platform — pas besoin de la forcer)
                    self._rte_client.set_cmd("crs_wiper_op", 0)
            else:
                self._lin_w.queue_send({"cmd": "OFF"})
                self._motor_w.queue_send(
                    {"ignition_status": 1, "reverse_gear": 0, "vehicle_speed": 0})
                self._motor_w.queue_send({"rain_intensity": 0, "sensor_status": "OK"})
        elif tid == "T33":
            if self._rte_client:
                self._rte_client.set_cmd("ignition_status", 1)
                self._rte_client.set_cmd("crs_wiper_op", 0)
            else:
                self._motor_w.queue_send(
                    {"ignition_status": 1, "reverse_gear": 0, "vehicle_speed": 0})
                self._lin_w.queue_send({"cmd": "OFF"})
            # Attendre 800ms que le BCM prenne ignition=1 avant le prochain test
            QTimer.singleShot(800, lambda: None)
        elif tid == "T39":
            self.log_msg.emit("  → start_lin_tx + OFF")
            self._lin_w.queue_send({"test_cmd": "start_lin_tx"})
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op", 0)
            else:
                self._lin_w.queue_send({"cmd": "OFF"})

    def _record(self, result: TestResult):
        if not self._running:
            return
        self._post_test(self._current)
        self._results.append(result)
        self.test_result.emit(result)
        done = self._total - len(self._queue)
        self.progress.emit(done, self._total)
        icon = "✅" if result.status == "PASS" else ("❌" if result.status == "FAIL" else "⚠")
        self.log_msg.emit(
            f"  {icon} {result.status}  mesure={result.measured}  "
            f"limite={result.limit}")
        self._current = None
        QTimer.singleShot(100, self._start_next)

    # ─── Slots (DirectConnection → thread GUI) ────────────────────────
    def _on_can(self, ev: dict):
        if self._current:
            try:
                r = self._current.on_can_frame(ev)
                if r:
                    self._record(r)
            except Exception as e:
                self.log_msg.emit(f"  ⚠ Erreur on_can_frame [{self._current.ID}]: {e}")

    def _on_lin(self, ev: dict):
        if self._current:
            try:
                r = self._current.on_lin_frame(ev)
                if r:
                    self._record(r)
            except Exception as e:
                self.log_msg.emit(f"  ⚠ Erreur on_lin_frame [{self._current.ID}]: {e}")

    def _on_motor(self, data: dict):
        if self._current:
            try:
                r = self._current.on_motor_data(data)
                if r:
                    self._record(r)
            except Exception as e:
                self.log_msg.emit(f"  ⚠ Erreur on_motor_data [{self._current.ID}]: {e}")

    # ─── Timer 200 ms : vérification timeout + Redis GET ─────────────
    def _tick(self):
        if not self._running or not self._current:
            return
        try:
            # 1. Timeout global
            r = self._current.check_timeout()
            if r:
                self._record(r)
                return
            # 2. Redis GET — pour tous les tests BaseBCMTest (T10,T11,T21,T30-T39)
            if isinstance(self._current, BaseBCMTest):
                r = self._current._check_rte()
                if r:
                    self._record(r)
        except Exception as e:
            self.log_msg.emit(f"  ⚠ Erreur _tick [{self._current.ID if self._current else '?'}]: {e}")