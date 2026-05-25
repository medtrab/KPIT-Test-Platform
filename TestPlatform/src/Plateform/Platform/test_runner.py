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

from PySide6.QtCore import QObject, QTimer, Signal, Qt

from test_cases import (BaseTest, BaseBCMTest, TestResult, ALL_TESTS,
                        TC_B2103_PositionSensorFault)


class TestRunner(QObject):
    # ─── Signaux publics ──────────────────────────────────────────────
    test_started = Signal(str, str)   # (test_id, test_name)
    test_result  = Signal(object)     # TestResult
    all_done     = Signal(list)       # list[TestResult]
    progress     = Signal(int, int)   # (done, total)
    log_msg      = Signal(str)        # message texte libre

    def __init__(self, can_worker, lin_worker, motor_worker,
                 pump_signal=None, rte_client=None, sim_client=None,
                 can_panel=None, parent=None):
        super().__init__(parent)
        self._can_w      = can_worker
        self._lin_w      = lin_worker
        self._motor_w    = motor_worker
        self._rte_client = rte_client   # RTEClient Redis (optionnel)
        self._sim_client = sim_client   # SimClient TCP vers RPi Simulateur (optionnel)
        self._can_panel  = can_panel    # CANBusPanel — reset_wc_state() avant chaque ERR0x

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
        if tid_cur in ("T30","T31","T32","T33","T34","T35","T36","T37","T38","T38b","T38c","T39",
                       "T43","T45","TC_LIN_002","TC_LIN_017_BIT1_ALONE",
                       "TC_CAN_003","TC_GEN_001","TC_SPD_001","TC_AUTO_004",
                       "TC_FSR_008","TC_FSR_010","TC_COM_001","TC_B2103",
                       "LIN_INVALID_CMD_001","T_RAIN_AUTO_SENSOR_ERROR",
                       "T_CAS_B_SPEED1_REVERSE","T_B2009_CAN","T50b","T50c","T50d","T_B2009_CASA",
                       "TC_LIN_016_BIT4","TC_LIN_016_BIT6_ALONE",
                       "TC_B2011_AND","TC_LIN_017_VER","TC_CAN_202_ERR01",
                       "TC_CAN_202_ERR02","TC_CAN_202_ERR04","TC_CAN_202_ERR05",
                       "TC_B2104"):
            last = getattr(self, "_last_tid", "")
            delay = 8000 if last == "T22" else \
                    3000 if last == "TC_FSR_010" else \
                    2500 if last in ("T40", "T21", "T36", "T37", "T43", "T45") else 0
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
                    self._rte_client.set_cmd("wc_available", False)  # garantit CAS A
                    # Synchroniser aussi le simulateur via TCP : sans ça, bcmcan
                    # continue à envoyer CAN 0x300 avec ignition=0 → écrase Redis
                    self._motor_w.queue_send(
                        {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
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
        elif tid in ("T03", "T04", "T05"):
            # 0x200 est émis cycliquement dès que wc_available=True (CAS B).
            # Pas besoin de stimulus wiper.
            self.log_msg.emit(f"  → {tid} : wc_available=True (CAS B)")
            if self._rte_client:
                self._rte_client.set_cmd("wc_available", True)
                self._rte_client.set_cmd("ignition_status", 1)
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
        elif tid == "T11":
            self.log_msg.emit("  → stop_can_tx")
            # wc_available=True obligatoire : sans CAS B actif, le BCM n'observe
            # pas le timeout CAN 0x201 et ne lève jamais wc_timeout_active=True.
            # On active wc_available, on attend 300ms que bcmcan envoie au moins
            # une trame 0x201 pour initialiser t_last_wiper_status, puis on coupe.
            if self._rte_client:
                self._rte_client.set_cmd("wc_available", True)
            QTimer.singleShot(300, lambda: self._motor_w.queue_send(
                {"test_cmd": "stop_can_tx"}))
        elif tid == "T40":
            self.log_msg.emit("  → T40 : TOUCH — 1 cycle puis retour OFF (no repeat)")
            # Même logique que l'ancien T20, mais T40 vérifie en plus :
            # 1) state=OFF après le cycle  2) cycle_count==1 (pas de répétition)
            if self._rte_client:
                self._rte_client.set_cmd("rest_contact_sim_active", False)
                self._rte_client.set_cmd("rest_contact_sim",        False)
                self._rte_client.set_cmd("crs_wiper_op", 0)
            self._lin_w.queue_send({"cmd": "TOUCH"})
            def _send_t40():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                if self._rte_client:
                    self._rte_client.set_cmd("rest_contact_sim_active", True)
                    self._rte_client.set_cmd("rest_contact_sim", True)   # lame EN MOUVEMENT
                    self._rte_client.set_cmd("crs_wiper_op", 1)          # WOP_TOUCH
                # Retour repos à 1500ms → fin du 1er (et unique) cycle
                QTimer.singleShot(1500, lambda: self._rte_client and
                    self._rte_client.set_cmd("rest_contact_sim", False))
                # Stick maintenu en TOUCH encore 2s après le cycle pour vérifier
                # qu'aucun 2e cycle ne démarre (T40 vérifie cycle_count==1)
            QTimer.singleShot(200, _send_t40)

        elif tid == "T43":
            self.log_msg.emit("  → T43 : SPEED1 + reverse_gear=True (rear intermittent)")
            # IMPORTANT : envoyer ignition ON + reverse via TCP au simulateur
            # en plus du SET Redis vers le BCM. Sans ça, bcmcan envoie CAN 0x300
            # avec ignition=0 toutes les 200ms → écrase Redis → boucle OFF→SPEED1.
            self._motor_w.queue_send({
                "ignition_status": "ON", "reverse_gear": 1, "vehicle_speed": 0
            })
            self._lin_w.queue_send({"cmd": "SPEED1"})
            if self._rte_client:
                # Cycling 2500ms : empêche B2009 sans interférer avec le timer
                # arrière BCM (REVERSE_REAR_PERIOD=1700ms).
                # On choisit 2500ms > 1700ms pour ne jamais coïncider avec
                # les impulsions OFF du moteur arrière.
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim", True)
                self._rte_client.set_cmd("crs_wiper_op", 2)
                self._rte_client.set_cmd("reverse_gear", True)

                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen_t43 = self._rc_gen

                def _rc_cycle_t43():
                    if self._rc_gen != _gen_t43:
                        return
                    if self._rte_client:
                        self._rte_client.set_cmd("rest_contact_sim", False)
                        QTimer.singleShot(100, lambda: self._rte_client and
                            self._rc_gen == _gen_t43 and
                            self._rte_client.set_cmd("rest_contact_sim", True))

                # Cycle toutes les 2500ms (> REVERSE_REAR_PERIOD=1700ms)
                for _d in range(2500, 20000, 2500):
                    QTimer.singleShot(_d, _rc_cycle_t43)

            def _t43_start():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
            QTimer.singleShot(500, _t43_start)

        elif tid == "T45":
            self.log_msg.emit("  → T45 : SPEED1 puis ignition=0 (blade return to rest)")
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            self._lin_w.queue_send({"cmd": "SPEED1"})
            if self._rte_client:
                # Activer simulation rest_contact : lame EN MOUVEMENT (True)
                # Sans ça, _read_rest_contact() retourne False (GPIO indispo)
                # → BCM voit "lame déjà au repos" → ST_OFF direct → ST_PARK jamais déclenché
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim", True)   # lame EN MOUVEMENT
                self._rte_client.set_cmd("crs_wiper_op", 2)
                def _do_ignoff():
                    if hasattr(test, "reset_t0"): test.reset_t0()
                    # Envoyer LIN OFF AVANT ignition=0 pour éviter la boucle
                    # SPEED1→OFF→SPEED1 : sans ça le LIN worker continue à émettre
                    # SPEED1 et le BCM redémarre indéfiniment après ST_OFF.
                    self._lin_w.queue_send({"cmd": "OFF"})
                    self._rte_client.set_cmd("ignition_status", 0)
                    # Sync simulateur CAN 0x300
                    self._motor_w.queue_send(
                        {"ignition_status": "OFF", "reverse_gear": 0, "vehicle_speed": 0})
                    # Simuler retour lame au repos après 1500ms :
                    # ST_PARK maintient le moteur jusqu'au contact repos (False).
                    # Sans cette transition True→False, ST_PARK attend jusqu'au
                    # timeout (5s) au lieu de détecter le repos normalement.
                    QTimer.singleShot(1500, lambda: self._rte_client and
                        self._rte_client.set_cmd("rest_contact_sim", False))
                QTimer.singleShot(400, _do_ignoff)
            else:
                def _do_ignoff_fallback():
                    if hasattr(test, "reset_t0"): test.reset_t0()
                    self._lin_w.queue_send({"cmd": "OFF"})
                    self._motor_w.queue_send(
                        {"ignition_status": "OFF", "reverse_gear": 0, "vehicle_speed": 0})
                QTimer.singleShot(400, _do_ignoff_fallback)

        elif tid == "TC_LIN_002":
            self.log_msg.emit("  → TC_LIN_002 : geler AliveCounter LIN (anti-replay)")
            if hasattr(test, "reset_t0"): test.reset_t0()
            self._lin_w.queue_send({"test_cmd": "freeze_alive_counter"})


        elif tid == "TC_LIN_017_BIT1_ALONE":
            self.log_msg.emit("  → TC_LIN_017_BIT1_ALONE : 0x17 bit0=1 seul — B2011 ne doit PAS se déclencher")
            if self._rte_client:
                self._rte_client.set_cmd("wc_available",    False)
                self._rte_client.set_cmd("crs_wiper_op",    0)
                self._rte_client.set_cmd("ignition_status", 1)
                self._rte_client.set_cmd("b2011_active",    False)
            # Purger résidus : s'assurer que bit6=0 ET bit0=0 avant injection
            self._lin_w.queue_send({"set_stuck": False})
            self._lin_w.queue_send({"set_fault": 0})
            def _bit1_alone_start():
                # Injecter 0x17 bit0=1 (CRS_InternalFault_Stick) UNIQUEMENT
                # 0x16 bit6 reste à 0 → condition ET incomplète → B2011 impossible
                self._lin_w.queue_send({"set_fault": 1})
                self.log_msg.emit("  → 0x17 bit0=1 (fault_stick) injecté — bit6=0 maintenu")
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
            # 800ms : laisser la queue se vider + BCM stable avant injection
            QTimer.singleShot(800, _bit1_alone_start)

        elif tid == "TC_CAN_003":
            self.log_msg.emit("  → TC_CAN_003 : geler AliveCounter CAN 0x200 (BCM→WC)")
            # Incrémenter _rc_gen pour invalider tout QTimer résiduel du test précédent
            self._rc_gen = getattr(self, "_rc_gen", 0) + 1
            self._lin_w.queue_send({"cmd": "SPEED1"})
            if self._rte_client:
                # Cycle wc_available False→True avec délai 500ms pour que le simulateur
                # réponde avec au moins 1 trame 0x201 valide avant le setup.
                # Sans ça, t_last_wiper_status périmé → B2005 immédiat → OFF.
                self._rte_client.set_cmd("wc_available",   False)
                # FIX TC_CAN_003 : s'assurer que alive_tx_frozen est remis à False
                # avant le test (résidu éventuel d'un test précédent interrompu)
                self._rte_client.set_cmd("alive_tx_frozen", False)
                def _can003_init():
                    if not self._rte_client:
                        return
                    self._rte_client.set_cmd("lin_op_locked",  True)
                    self._rte_client.set_cmd("wc_available",   True)
                    self._rte_client.set_cmd("wc_alive_fault", False)
                    self._rte_client.set_cmd("crs_wiper_op",   2)
                    def _can003_freeze():
                        if hasattr(test, "reset_t0"): test.reset_t0()
                        # FIX TC_CAN_003 : geler l'AliveCounter_TX côté BCM EN PREMIER
                        # (bcm_protocol._build_wiper_command arrête d'incrémenter wc_can_alive_tx)
                        # PUIS envoyer freeze_can_alive au simulateur (activation de la détection WC).
                        # Sans ce fix, le BCM continuait d'incrémenter normalement → WC simulé
                        # ne voyait jamais deux trames consécutives avec le même counter → timeout.
                        if self._rte_client:
                            self._rte_client.set_cmd("alive_tx_frozen", True)
                        self._motor_w.queue_send({"test_cmd": "freeze_can_alive"})
                        if hasattr(test, "_stimulus_sent"):
                            test._stimulus_sent = True
                    QTimer.singleShot(600, _can003_freeze)
                QTimer.singleShot(500, _can003_init)

        elif tid == "TC_GEN_001":
            self.log_msg.emit("  → TC_GEN_001 : ignition=0 puis ON + SPEED1")
            if self._rte_client:
                self._rte_client.set_cmd("wc_available", False)
                self._rte_client.set_cmd("ignition_status", 0)
                self._rte_client.set_cmd("crs_wiper_op", 0)
            # TCP vers bcmcan : envoyer ignition=OFF pour que CAN 0x300
            # émette ignition=0 et n'écrase pas Redis toutes les 200ms.
            self._motor_w.queue_send(
                {"ignition_status": "OFF", "reverse_gear": 0, "vehicle_speed": 0})
            # 1000ms : assure 5 trames CAN 0x300 avec ignition=0 avant le stimulus.
            # Sans ça, bcmcan envoie encore ignition=2 → BCM ignore SPEED1.
            def _tc_gen001_start():
                if hasattr(test, "reset_t0"): test.reset_t0()
                self._motor_w.queue_send(
                    {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
                self._lin_w.queue_send({"cmd": "SPEED1"})
                if self._rte_client:
                    self._rte_client.set_cmd("ignition_status", 1)
            QTimer.singleShot(1000, _tc_gen001_start)

        elif tid == "TC_SPD_001":
            self.log_msg.emit("  → TC_SPD_001 : LIN SPEED1 continu 5 s")
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            self._lin_w.queue_send({"cmd": "SPEED1"})
            if self._rte_client:
                # Activer simulation rest_contact pour éviter B2009
                # (sans hardware GPIO, BCM détecte STUCK CLOSED après 3s)
                # Cycle : lame EN MOUVEMENT 1600ms / AU REPOS 100ms — simule un cycle complet
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim", True)  # lame en mouvement

                # Génération : incrémentée à chaque test pour annuler les QTimers résiduels
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen = self._rc_gen

                def _rc_rest():
                    """Passer brièvement au repos (False) puis reprendre mouvement (True)."""
                    if self._rc_gen != _gen:
                        return  # QTimer résiduel d'un test précédent — ignorer
                    if self._rte_client:
                        self._rte_client.set_cmd("rest_contact_sim", False)
                    QTimer.singleShot(100, lambda: self._rte_client and
                        self._rc_gen == _gen and
                        self._rte_client.set_cmd("rest_contact_sim", True))

                # Cycle toutes les 1700ms pendant 8s (couvre la fenêtre d'observation 5s + marge)
                for _i, _delay in enumerate(range(1700, 8500, 1700)):
                    QTimer.singleShot(_delay, _rc_rest)

            if hasattr(test, "reset_t0"):
                QTimer.singleShot(300, lambda: test.reset_t0())

        elif tid == "TC_AUTO_004":
            self.log_msg.emit("  → TC_AUTO_004 : AUTO avec rain_sensor_installed=False")
            if hasattr(test, "reset_t0"): test.reset_t0()
            if self._rte_client:
                self._rte_client.set_cmd("rain_sensor_installed", False)
                self._rte_client.set_cmd("crs_wiper_op", 4)   # WOP_AUTO
            self._lin_w.queue_send({"cmd": "AUTO"})

        elif tid == "TC_FSR_008":
            self.log_msg.emit("  → TC_FSR_008 : LIN SPEED1 puis watchdog trigger")
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            self._lin_w.queue_send({"cmd": "SPEED1"})
            if self._rte_client:
                def _fsr008_trigger():
                    if hasattr(test, "reset_t0"): test.reset_t0()
                    self._rte_client.set_cmd("watchdog_test_trigger", True)
                QTimer.singleShot(400, _fsr008_trigger)
            else:
                if hasattr(test, "reset_t0"): test.reset_t0()

        elif tid == "TC_FSR_010":
            self.log_msg.emit("  → TC_FSR_010 : CRC corrompu sur 0x201 (émission autonome)")
            # Incrémenter _rc_gen EN PREMIER : invalide tout _fsr010_cleanup résiduel
            # d'un run précédent encore en attente (délai 3500ms) qui poserait
            # wc_available=False / crs_wiper_op=0 pendant notre setup → BCM sort de SPEED1
            # avant que la corruption soit activée → 0 trames corrompues émises.
            self._rc_gen = getattr(self, "_rc_gen", 0) + 1
            # Garantir ignition=2 dans bcmcan EN PREMIER.
            # Si _vehicle_state est resté à ignition=0 (résidu d'un test précédent
            # comme TC_GEN_001 ou T45 qui envoient ignition=OFF), bcmcan continue
            # d'émettre CAN 0x300 avec ignition=0 toutes les 200ms. Après 1s la
            # priorité Redis expire → BCM lit le CAN → passe en ST_OFF → arrête 0x200
            # → simulator ne déclenche plus _build_0x201 → count=8 jamais décrémenté
            # → wc_crc_fault jamais levé → timeout TC_FSR_010.
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            # reset_b2101 : remet _t_last_0x200=now et suspend le check B2101 pendant 3s.
            # Sans ça, si _t_last_0x200 date du test précédent (TC_CAN_003) et est
            # périmé de >2s au moment du setup (1400ms), B2101 se déclenche pendant
            # le délai avant corruption et invalide le test avant même la 1ère trame CRC KO.
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.reset_b2101()
            if self._rte_client:
                self._rte_client.set_cmd("wc_timeout_active", False)
                self._rte_client.set_cmd("wc_crc_fault",      False)
                self._rte_client.set_cmd("wc_available",      False)
                # Verrouiller crs_wiper_op contre LIN 0x16 : empêche le LIN worker
                # d'écraser avec WiperOp=OFF et de faire chuter wc_available
                self._rte_client.set_cmd("lin_op_locked", True)
            self._lin_w.queue_send({"cmd": "SPEED1"})

            def _fsr010_activate():
                if self._rte_client:
                    # FIX : re-asserter wc_available=False à mi-chemin pour écraser
                    # tout résidu de commande Redis du cleanup TC_CAN_003 qui pourrait
                    # arriver en retard et écraser notre wc_available=True à venir.
                    self._rte_client.set_cmd("wc_available",  False)
                    self._rte_client.set_cmd("wc_crc_fault",  False)
                def _fsr010_set_available():
                    if self._rte_client:
                        self._rte_client.set_cmd("wc_available",  True)
                        self._rte_client.set_cmd("crs_wiper_op",  2)
                    def _fsr010_corrupt():
                        # FIX race condition Redis : re-affirmer crs_wiper_op=2 (SPEED1)
                        # juste avant le stimulus. Une commande Redis crs_wiper_op=0
                        # résiduelle du cleanup du test précédent (TC_FSR_008 _post_test
                        # ou le bloc inter-test) peut arriver APRÈS la commande =2 de
                        # _fsr010_set_available (400ms plus tôt) et l'écraser.
                        # BCM reste en OFF → n'envoie plus 0x200 → WC n'émet plus 0x201
                        # → count=8 jamais décrémenté → wc_crc_fault jamais levé → timeout.
                        # Re-poster ici (T_total=1400ms) garantit que crs_wiper_op=2
                        # est la DERNIÈRE commande Redis avant l'injection CRC KO.
                        if self._rte_client:
                            self._rte_client.set_cmd("wc_available", True)
                            self._rte_client.set_cmd("crs_wiper_op", 2)
                        if hasattr(test, "reset_t0"): test.reset_t0()
                        # Garantir count=0 avant count=8 via sim_client (direct, pas de queue)
                        # Ceinture + bretelles : reset_corrupt_crc dans _post_test suffit,
                        # mais ce reset supplémentaire élimine tout résidu d'un run précédent
                        # qui aurait survécu malgré tout (ex: sim_client indisponible au post_test).
                        if self._sim_client and self._sim_client.is_connected():
                            self._sim_client.reset_corrupt_crc()
                        self._motor_w.queue_send({"test_cmd": "corrupt_crc_0x201", "count": 8})
                        # Autoriser _check_rte() à observer seulement maintenant
                        if hasattr(test, "_stimulus_sent"):
                            test._stimulus_sent = True
                    QTimer.singleShot(600, _fsr010_corrupt)
                # FIX : délai 400ms entre le re-assert False et le set True
                # pour laisser tous les Redis en transit du cleanup précédent arriver
                QTimer.singleShot(400, _fsr010_set_available)

            # FIX : délai porté à 400ms (était 600ms) — la 2e étape ajoute 400ms supplémentaires
            # Total jusqu'au corrupt : 400ms + 400ms + 600ms = 1400ms (était 600ms+600ms=1200ms)
            QTimer.singleShot(400, _fsr010_activate)

        elif tid == "TC_COM_001":
            self.log_msg.emit("  → TC_COM_001 : mesure physique baudrate BREAK LIN")
            if hasattr(test, "reset_t0"): test.reset_t0()
            # Pas de stimulus actif : crslin mesure automatiquement la duree
            # du BREAK a chaque trame LIN recue, accumule 5 mesures, puis
            # envoie lin_baud_measured via TCP. Le LIN schedule tourne deja.

        # ── TC_LIN_CS : 5 trames checksum KO → timeout B2004 ────────────
        elif tid == "TC_LIN_CS":
            self.log_msg.emit("  → TC_LIN_CS : corrupt checksum ON + SPEED1 → 5 trames KO → B2004")
            # Séquence :
            # t=0   : reset lin_checksum_fault + lin_timeout_active
            # t=200 : corrupt_lin_checksum actif + reset_t0() + cmd=SPEED1
            #         → crslin émet WOP=2 AVEC checksum corrompu en continu
            #         → BCM rejette chaque trame (t_last_lin0x16 non mis à jour)
            #         → après LIN_TIMEOUT (2s = ~5 trames × 400ms) → B2004
            # La corruption reste active jusqu'au _post_test (restore_lin_checksum)
            if self._rte_client:
                self._rte_client.set_cmd("lin_checksum_fault", False)
                self._rte_client.set_cmd("crs_wiper_op",       0)
                self._rte_client.set_cmd("lin_timeout_active", False)

            def _tc_lin_cs_activate():
                self._lin_w.queue_send({"test_cmd": "corrupt_lin_checksum"})
                if hasattr(test, "_stimulus_sent"):
                    test._stimulus_sent = True
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # cmd=SPEED1 : crslin émet WOP=2 corrompu en continu
                # La corruption reste active → toutes les trames sont KO
                # → t_last_lin0x16 jamais mis à jour → timeout après 2s
                QTimer.singleShot(100, lambda: self._lin_w.queue_send({"cmd": "SPEED1"}))

            QTimer.singleShot(200, _tc_lin_cs_activate)


        # ── T44 : REAR_WIPE isolé (op=7) ─────────────────────────────────
        elif tid == "T44":
            self.log_msg.emit("  → T44 : REAR_WIPE op=7 (une seule fois) → OFF à 2000ms")
            # Préconditions :
            #  - rear_wiper_available=True  (inscriptible — REDIS_WRITABLE_KEYS)
            #  - wc_available=False         (Cas A)
            #  - reverse_gear=False         (éviter mode intermittent T43)
            #  - ignition=ON
            # PAS de rest_contact_sim : moteur arrière sans capteur fin de course.
            # PAS de _maintain_op7 : WOP=7 est envoyé UNE SEULE FOIS via LIN.
            # Le BCM maintient REAR_WIPE par sa propre logique tant que
            # crs_wiper_op=7 est dans le RTE (mis à jour par _lin_poll_0x16).
            # À t=2000ms (> CYCLE_MS=1700ms), cmd="OFF" est envoyé → crslin
            # émet WOP=0 → _lin_poll_0x16 écrit crs_wiper_op=0 → BCM sort.
            if self._rte_client:
                self._rte_client.set_cmd("rear_wiper_available", True)
                self._rte_client.set_cmd("wc_available",         False)
                self._rte_client.set_cmd("reverse_gear",         False)
                self._rte_client.set_cmd("ignition_status",      1)
                self._rte_client.set_cmd("crs_wiper_op",         0)
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

            def _t44_start():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # Stimulus unique : LIN cmd="REAR_WIPE" → crslin émet WOP=7
                # Le BCM lit WiperOp=7 via _lin_poll_0x16 → entre en REAR_WIPE
                self._lin_w.queue_send({"cmd": "REAR_WIPE"})

                # À t=2000ms (après au moins 1 cycle de 1700ms) : relâcher le levier
                # cmd="OFF" → crslin émet WOP=0 → BCM sort de REAR_WIPE
                QTimer.singleShot(2000, lambda: self._lin_w.queue_send({"cmd": "OFF"}))

            # Délai 300ms : BCM confirme rear_wiper_available=True et ignition=ON
            QTimer.singleShot(300, _t44_start)

        # ── T50 : Cas B — wc_available=True → H-Bridge GPIO non commandé ─
        elif tid == "T50":
            self.log_msg.emit("  → T50 : Cas B wc_available=True + LIN SPEED1 → CAN 0x200, pas RL2=LOW")
            # Préconditions :
            #  - wc_available=True   (Cas B — force le blocage GPIO)
            #  - lin_op_locked=True  : verrouille crs_wiper_op contre LIN 0x16
            #  - ignition=ON
            # PAS de rest_contact_sim : la garde "if rest_contact_sim_active: return"
            # dans _check_rest_contact_stuck() désactiverait B2009 artificiellement.
            # PAS de blade_cycling : incohérent avec rest_contact qui resterait fixe
            # (si lame ne bouge pas physiquement, les deux capteurs sont fixes).
            # L'observation dure OBS_MS=2000ms < REST_STUCK_DELAY=3000ms :
            # B2009 ne peut pas se déclencher dans cette fenêtre temporelle.
            if self._rte_client:
                self._rte_client.set_cmd("wc_available",  True)
                self._rte_client.set_cmd("lin_op_locked", True)
                self._rte_client.set_cmd("crs_wiper_op",  0)
                self._rte_client.set_cmd("ignition_status", 1)
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

            def _t50_start():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                self._lin_w.queue_send({"cmd": "SPEED1"})
                if self._rte_client:
                    self._rte_client.set_cmd("crs_wiper_op", 2)
            QTimer.singleShot(400, _t50_start)

        # ── T51 : Cas A — rest contact bloqué → FSR_006 ──────────────────
        elif tid == "T51":
            self.log_msg.emit("  → T51 : Cas A rest_contact bloqué EN MOUVEMENT → FSR_006")
            # Pré-conditions :
            #  - wc_available=False (Cas A obligatoire — FSR_006 Cas A surveille
            #    rest_contact GPIO, Cas B surveille wc_blade_position)
            #  - rest_contact_sim_active=True, rest_contact_sim=True (lame BLOQUÉE
            #    en position "en mouvement" — ne jamais passer à False)
            #  - ignition=ON
            if self._rte_client:
                self._rte_client.set_cmd("wc_available",            False)
                self._rte_client.set_cmd("ignition_status",         1)
                self._rte_client.set_cmd("crs_wiper_op",            0)
                # Activer simulation rest_contact BLOQUÉE en position mouvement.
                # IMPORTANT : ne PAS programmer de retour à False ici.
                # FSR_006 doit détecter ce blocage SEUL, sans aide du test_runner.
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim",        True)
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            def _t51_start():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # Stimulus : SPEED1 → moteur démarre → rest_contact reste bloqué
                self._lin_w.queue_send({"cmd": "SPEED1"})
                if self._rte_client:
                    self._rte_client.set_cmd("crs_wiper_op", 2)
            # Délai 300 ms : laisser le BCM confirmer wc_available=False
            QTimer.singleShot(300, _t51_start)

        elif tid == "TC_B2103":
            # ── TC_B2103 : WC Position Sensor Fault ─────────────────────────
            # Protocole :
            #   1. reset_b2103()        → guard + blade_sim=-1 côté simulateur
            #   2. wc_b2103_active=False → nettoie clé Redis résiduelle
            #   3. Délai 200 ms         → propagation du reset
            #   4. send_blade_sim(50.0) → injecte cible 50 % (lame ≈ 0 % au repos)
            #                             écart ≈ 50 % >> seuil 10 %
            #   5. reset_t0()           → chrono démarre à l'injection
            # Fenêtre PASS : 1 000–1 500 ms (délai persistance + marge Redis)
            # Nettoyage post-test : reset_b2103() + wc_b2103_active=False
            self.log_msg.emit(
                "  → TC_B2103 : reset guard B2103, puis injection blade_sim=50 % "
                "(écart ≈ 50 % > seuil 10 %)")

            # Étape 1 & 2 : remise à zéro immédiate
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.reset_b2103()
            if self._rte_client:
                self._rte_client.set_cmd("wc_b2103_active", False)

            def _b2103_inject():
                # Étape 4 : injecter blade_sim après propagation du reset
                if self._sim_client and self._sim_client.is_connected():
                    ok = self._sim_client.send_blade_sim(50.0)
                    self.log_msg.emit(
                        f"  → TC_B2103 : blade_sim=50 % {'injecté' if ok else 'ECHEC INJECTION'}"
                        " — attente détection B2103…")
                else:
                    self.log_msg.emit(
                        "  → TC_B2103 : SimClient non connecté — injection blade_sim impossible")
                # Étape 5 : démarrer le chrono à partir de l'injection physique
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                test._t_inject_ms = time.time() * 1000.0

            def _b2103_cleanup():
                # Nettoyage : désactiver comparaison + Redis pour le test suivant
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.reset_b2103()
                if self._rte_client:
                    self._rte_client.set_cmd("wc_b2103_active", False)
                self.log_msg.emit("  → TC_B2103 : nettoyage post-test (blade_sim=-1, Redis reset)")

            # Délai 200 ms : propagation du reset côté simulateur avant injection
            QTimer.singleShot(200, _b2103_inject)
            # Nettoyage différé : TIMEOUT + 500 ms pour ne pas polluer le test suivant
            QTimer.singleShot(
                int((TC_B2103_PositionSensorFault.TEST_TIMEOUT_S + 0.5) * 1000),
                _b2103_cleanup)

        elif tid == "T22":
            self.log_msg.emit("  → T22 : pompe FORWARD >5s → overtime FSR_005 (B2008)")
            if hasattr(test, "reset_t0"): test.reset_t0()
            if self._rte_client:
                # Cycling 1700ms : empêche B2009 (False→True reset timer chaque 1700ms).
                # La pompe tourne en FORWARD. FSR_005 la coupe à 5000ms (PUMP_MAX_RUNTIME),
                # avant que le 3e cycle se complète à 5100ms → wash ne se termine pas
                # normalement → seul FSR_005 coupe la pompe → B2008 déclenché.
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim", True)
                self._rte_client.set_cmd("crs_wiper_op", 5)   # WOP_FRONT_WASH

                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen_t22 = self._rc_gen

                def _rc_cycle_t22():
                    if self._rc_gen != _gen_t22:
                        return
                    if self._rte_client:
                        self._rte_client.set_cmd("rest_contact_sim", False)
                        QTimer.singleShot(100, lambda: self._rte_client and
                            self._rc_gen == _gen_t22 and
                            self._rte_client.set_cmd("rest_contact_sim", True))

                for _d in range(1700, 10000, 1700):
                    QTimer.singleShot(_d, _rc_cycle_t22)

            self._lin_w.queue_send({"cmd": "FRONT_WASH"})

        elif tid == "T21":
            self.log_msg.emit("  → T21 : FRONT_WASH — 3 cycles lame avant 5s → arrêt pompe normal")
            if self._rte_client:
                # Cycling rest_contact à 900ms pour que 3 cycles se terminent avant 5s.
                # 3 × 900ms ≈ 2.7s → wash_cycles_done=3 → pompe arrêtée AVANT FSR_005 (5s).
                # Si les cycles prenaient 1700ms (ancien), 3e cycle à ~5.1s > 5s → FSR → T22.
                # T21 vérifie l'arrêt normal (avant 5s). T22 vérifie l'arrêt FSR (>5s).
                # Cycle : True (mouvement) 800ms → False (repos) 100ms → True...
                # _track_blade_cycle compte sur front descendant True→False (count_on_rest=True).
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim", True)

                def _t21_start():
                    if self._rte_client:
                        self._rte_client.set_cmd("crs_wiper_op", 5)
                        self._lin_w.queue_send({"cmd": "FRONT_WASH"})
                        # Génération : annule les QTimers résiduels du test précédent
                        self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                        _gen_t21 = self._rc_gen
                        # Cycle à 900ms : 3 cycles = 2.7s < PUMP_MAX_RUNTIME (5s)
                        # → le BCM détecte 3 cycles et stoppe la pompe normalement
                        def _rc_cycle_t21():
                            if self._rc_gen != _gen_t21:
                                return
                            if self._rte_client:
                                self._rte_client.set_cmd("rest_contact_sim", False)
                                QTimer.singleShot(100, lambda: self._rte_client and
                                    self._rc_gen == _gen_t21 and
                                    self._rte_client.set_cmd("rest_contact_sim", True))
                        for _d in range(900, 10000, 900):
                            QTimer.singleShot(_d, _rc_cycle_t21)

                QTimer.singleShot(200, _t21_start)
            else:
                self._lin_w.queue_send({"cmd": "FRONT_WASH"})

        # ── Tests WSM BCM — stimulus LIN, observation Redis ──────────────
        # Le stimulus passe par LIN (crslin → bus physique → BCM protocol)
        # pour tester la chaîne complète : trame 0x16 → décodage wiper_op
        # → WSM → state. Redis est utilisé UNIQUEMENT pour observer state.
        # Un SET Redis direct court-circuiterait le protocole LIN et ne
        # testerait que la logique interne WSM, pas la chaîne hardware.
        elif tid == "T30":
            self.log_msg.emit("  → T30 : LIN cmd=SPEED1 (stimulus bus physique)")
            if hasattr(test, "reset_t0"): test.reset_t0()
            self._lin_w.queue_send({"cmd": "SPEED1"})

        elif tid == "T31":
            self.log_msg.emit("  → T31 : LIN cmd=SPEED2 (stimulus bus physique)")
            if hasattr(test, "reset_t0"): test.reset_t0()
            self._lin_w.queue_send({"cmd": "SPEED2"})

        elif tid == "T32":
            self.log_msg.emit("  → T32 : LIN SPEED1 puis LIN OFF (stimulus bus physique)")
            # reset_t0 au moment de la commande OFF (pas au SPEED1)
            # pour ne mesurer que la transition SPEED1→OFF
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
                # Sync simulateur : ignition ON pour démarrer en SPEED1
                self._motor_w.queue_send(
                    {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
                self._rte_client.set_cmd("crs_wiper_op", 0)
                QTimer.singleShot(200, lambda: self._rte_client and
                    self._rte_client.set_cmd("crs_wiper_op", 2))
                def _t33_ignoff():
                    test.reset_t0()
                    self._rte_client.set_cmd("ignition_status", 0)
                    # Sync simulateur : ignition OFF → CAN 0x300 avec ign=0
                    self._motor_w.queue_send(
                        {"ignition_status": "OFF", "reverse_gear": 0, "vehicle_speed": 0})
                QTimer.singleShot(600, _t33_ignoff)
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
                # _track_blade_cycle fonctionne (REST_CONTACT_HARDWARE_PRESENT=True).
                # Utiliser _rc_gen pour pouvoir annuler les timers au cleanup.
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim", False)
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen_t34 = self._rc_gen
                QTimer.singleShot(200, lambda: test.reset_t0())
                for cycle in range(3):
                    b = 300 + cycle * 1700
                    QTimer.singleShot(b, lambda b=b, g=_gen_t34: (
                        self._rc_gen == g and self._rte_client and
                        self._rte_client.set_cmd("rest_contact_sim", True)))
                    QTimer.singleShot(b + 1550, lambda b=b, g=_gen_t34: (
                        self._rc_gen == g and self._rte_client and
                        self._rte_client.set_cmd("rest_contact_sim", False)))
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
                # Activer simulation rest_contact avec _rc_gen (annulable au cleanup)
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim", False)
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen_t35 = self._rc_gen
                QTimer.singleShot(200, lambda: test.reset_t0())
                for cycle in range(3):
                    b = 300 + cycle * 1700
                    QTimer.singleShot(b, lambda b=b, g=_gen_t35: (
                        self._rc_gen == g and self._rte_client and
                        self._rte_client.set_cmd("rest_contact_sim", True)))
                    QTimer.singleShot(b + 1550, lambda b=b, g=_gen_t35: (
                        self._rc_gen == g and self._rte_client and
                        self._rte_client.set_cmd("rest_contact_sim", False)))
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
                    self._lin_w.queue_send({"cmd": "FRONT_WASH"})  # stimulus LIN uniquement
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
            self.log_msg.emit("  → T37 : LIN REAR_WASH (stimulus bus physique)")
            self._lin_w.queue_send({"cmd": "REAR_WASH"})
            self._motor_w.queue_send({"ignition_status": 1, "vehicle_speed": 0})
            # reset_t0 décalé de 200ms : absorbe la latence LIN→BCM pour que
            # la mesure démarre quand le BCM est réellement en WASH_REAR
            if hasattr(test, "reset_t0"):
                QTimer.singleShot(200, lambda: test.reset_t0())

        elif tid == "T38":
            self.log_msg.emit("  → T38 : LIN SPEED1 + injection surcourant")
            self._lin_w.queue_send({"cmd": "SPEED1"})
            if self._rte_client:
                # t=200ms : B2001 INACTIVE (200ms avant injection)
                QTimer.singleShot(200, lambda: self._rte_client and
                    self._rte_client.set_cmd("dtc_inactivate", "B2001"))
                # t=400ms : injection + chrono
                def _t38_inject():
                    if hasattr(test, "reset_t0"): test.reset_t0()
                    self._rte_client.set_cmd("motor_current_a", 0.95)
                QTimer.singleShot(400, _t38_inject)
            else:
                QTimer.singleShot(400, lambda: self._inject_overcurrent(6))

        elif tid == "T39":
            self.log_msg.emit("  → T39 : LIN SPEED1 puis stop_lin_tx (stimulus bus physique)")
            # Attendre 500ms pour que le BCM soit bien en SPEED1 avant de couper le LIN.
            # reset_t0 se déclenche au moment du stop_lin_tx : mesure = temps entre
            # coupure LIN et retour en OFF du BCM = délai détection timeout FSR_001.
            self._lin_w.queue_send({"cmd": "SPEED1"})
            def _t39_stop():
                if hasattr(test, "reset_t0"): test.reset_t0()
                self._lin_w.queue_send({"test_cmd": "stop_lin_tx"})
            QTimer.singleShot(500, _t39_stop)

        elif tid == "T38b":
            self.log_msg.emit("  → T38b : LIN REAR_WIPE + injection surcourant moteur arrière (B2002)")
            if self._rte_client:
                self._rte_client.set_cmd("rear_wiper_available", True)
            self._lin_w.queue_send({"cmd": "REAR_WIPE"})
            if self._rte_client:
                # Étape 1 à t=300ms : remettre B2002 INACTIVE
                # Laisser 200ms de marge avant l'injection pour que le BCM
                # traite set_inactive (cycle Redis ~100ms) → already_active=False
                QTimer.singleShot(300, lambda: self._rte_client and
                    self._rte_client.set_cmd("dtc_inactivate", "B2002"))
                # Étape 2 à t=500ms : injection surcourant + démarrage chrono
                def _t38b_inject():
                    if hasattr(test, "reset_t0"):
                        test.reset_t0()
                    self._rte_client.set_cmd("motor_current_a", 0.95)
                QTimer.singleShot(500, _t38b_inject)

        elif tid == "T38c":
            self.log_msg.emit("  → T38c : LIN FRONT_WASH (pompe active) + injection surcourant pompe (B2003)")
            if self._rte_client:
                # Activer simulation rest_contact : WASH_FRONT utilise le moteur avant,
                # sans cycles rest_contact le BCM déclenche B2009 après 3s → ERROR
                # avant que pump_error ne soit vu par _check_rte.
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim", False)
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen_t38c = self._rc_gen
                for cycle in range(4):
                    b = 200 + cycle * 1700
                    QTimer.singleShot(b, lambda g=_gen_t38c: (
                        self._rc_gen == g and self._rte_client and
                        self._rte_client.set_cmd("rest_contact_sim", True)))
                    QTimer.singleShot(b + 1550, lambda g=_gen_t38c: (
                        self._rc_gen == g and self._rte_client and
                        self._rte_client.set_cmd("rest_contact_sim", False)))
            self._lin_w.queue_send({"cmd": "FRONT_WASH"})
            if self._rte_client:
                # t=400ms : B2003 INACTIVE (200ms avant injection)
                QTimer.singleShot(400, lambda: self._rte_client and
                    self._rte_client.set_cmd("dtc_inactivate", "B2003"))
                # t=600ms : injection + chrono
                def _t38c_inject():
                    # Invalider les cycles rest_contact avant l'injection
                    self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                    if hasattr(test, "reset_t0"):
                        test.reset_t0()
                    self._rte_client.set_cmd("pump_current_a", 1.0)
                QTimer.singleShot(600, _t38c_inject)

        elif tid == "LIN_INVALID_CMD_001":
            self.log_msg.emit("  → LIN_INVALID_CMD_001 : BCM en OFF → envoi trame LIN op=10 (hors plage)")
            # S'assurer que le BCM est en OFF
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op", 0)
            # Injecter op=10 brut dans la prochaine trame LIN 0x16 via test_cmd.
            # Le simulateur insère la valeur dans les bits 3:0 de byte0 de 0x16
            # sans passer par l'enum WOp → le BCM reçoit WiperOp=0x0A (hors [0..7]).
            def _lin_invalid_inject():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                self._lin_w.queue_send({"test_cmd": "set_raw_wiper_op", "op": 10})
            QTimer.singleShot(200, _lin_invalid_inject)

        elif tid == "T_RAIN_AUTO_SENSOR_ERROR":
            self.log_msg.emit("  → T_RAIN_AUTO_SENSOR_ERROR : rain_sensor_installed + AUTO + SensorStatus=ERROR")
            if self._rte_client:
                # Étape 1 : déclarer le capteur disponible et sain
                # rain_intensity=25 > RAIN_SPEED2_THRESH(20) : démarre le moteur en Speed2
                # sans ça le moteur reste STOP et B2009 peut quand même se déclencher.
                self._rte_client.set_cmd("rain_sensor_installed", True)
                self._rte_client.set_cmd("rain_sensor_ok", True)
                self._rte_client.set_cmd("rain_intensity", 25)
                # Sync CAN 0x301 simulateur : évite que bcmcan écrase rain_intensity
                self._motor_w.queue_send({"rain_intensity": 25, "sensor_status": "OK"})
                # Simulation rest_contact avec cycles True→False (comme T34/T35)
                # pour éviter B2009 STUCK CLOSED pendant la phase AUTO.
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim", False)

                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen_rain = self._rc_gen

                for cycle in range(4):
                    b = 300 + cycle * 1700
                    QTimer.singleShot(b, lambda g=_gen_rain: (
                        self._rc_gen == g and self._rte_client and
                        self._rte_client.set_cmd("rest_contact_sim", True)))
                    QTimer.singleShot(b + 1550, lambda g=_gen_rain: (
                        self._rc_gen == g and self._rte_client and
                        self._rte_client.set_cmd("rest_contact_sim", False)))

            # Étape 2 : envoyer commande AUTO via LIN
            QTimer.singleShot(200, lambda: self._lin_w.queue_send({"cmd": "AUTO"}))

            # Étape 3a à t=1000ms : B2007 INACTIVE (200ms avant injection)
            QTimer.singleShot(1000, lambda: self._rte_client and
                self._rte_client.set_cmd("dtc_inactivate", "B2007"))

            # Étape 3b à t=1200ms : injection erreur capteur + chrono
            def _inject_sensor_error():
                # Invalider les cycles rest_contact
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                if hasattr(test, "notify_injection"):
                    test.notify_injection()
                if self._rte_client:
                    self._rte_client.set_cmd("rain_intensity", 0xFF)
                    self._rte_client.set_cmd("rain_sensor_ok", False)
                self._motor_w.queue_send({"rain_intensity": 255, "sensor_status": "ERROR"})
            QTimer.singleShot(1200, _inject_sensor_error)

        elif tid == "T_CAS_B_SPEED1_REVERSE":
            self.log_msg.emit("  → T_CAS_B_SPEED1_REVERSE : CAS B + SPEED1 LIN→CAN 0x200 + Reverse CAN 0x300")
            if self._rte_client:
                self._rte_client.set_cmd("wc_available",         True)
                self._rte_client.set_cmd("lin_op_locked",        False)  # LIN doit pouvoir écrire crs_wiper_op
                self._rte_client.set_cmd("rear_wiper_available", True)
                self._rte_client.set_cmd("crs_wiper_op",         0)
                self._rte_client.set_cmd("ignition_status",      1)
                self._rte_client.set_cmd("reverse_gear",         False)
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

            def _casb_speed1_start():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # blade_cycling : évite B2009 en CAS B (blade=0 permanent → STUCK)
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.start_blade_cycling(period_ms=1500)
                # SPEED1 via LIN uniquement → _lin_poll_frame écrit crs_wiper_op=2
                # → BCM entre SPEED1 → envoie CAN 0x200 mode=SPEED1 au WC
                # PAS de set_cmd("crs_wiper_op", 2) : le stimulus doit venir du bus LIN
                self._lin_w.queue_send({"cmd": "SPEED1"})
                # Reverse via CAN 0x300 uniquement (motor_w → bcmcan → trame 0x300)
                # PAS de set_cmd("reverse_gear", True) : la valeur doit venir du bus CAN
                def _activate_reverse():
                    self._motor_w.queue_send(
                        {"ignition_status": "ON", "reverse_gear": 1, "vehicle_speed": 0})
                QTimer.singleShot(500, _activate_reverse)

            QTimer.singleShot(400, _casb_speed1_start)

        elif tid == "T_B2009_CAN":
            if self._rte_client:
                self._rte_client.set_cmd("wc_available",   True)
                self._rte_client.set_cmd("lin_op_locked",  True)
                self._rte_client.set_cmd("crs_wiper_op",   0)
                self._rte_client.set_cmd("ignition_status", 1)
                # rest_contact_sim_active=True + rest_contact_sim=False :
                # lame figée AU REPOS (aucun cycle) → NE555 hardware ignoré
                # La garde BCM laisse passer B2009 quand sim=False (lame bloquée)
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim", False)
                # B2009 INACTIVE avant le test pour affichage complet
                QTimer.singleShot(200, lambda: self._rte_client and
                    self._rte_client.set_cmd("dtc_inactivate", "B2009"))
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

            def _t_b2009_start():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # Re-asserter ignition=1 juste avant le stimulus : défense contre
                # la race condition CAN 0x300 (bcmcan peut encore émettre ign=0
                # depuis un test précédent et écraser ignition_status dans le BCM).
                if self._rte_client:
                    self._rte_client.set_cmd("ignition_status", 1)
                # BladePosition figée à 50% (>0 → front_motor_running=True en CAS B)
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.freeze_blade_position(50.0)
                # LIN SPEED1 → BCM entre ST_SPEED1 → CAN 0x200 → 0x201 CurrentSpeed>0
                self._lin_w.queue_send({"cmd": "SPEED1"})
                if self._rte_client:
                    self._rte_client.set_cmd("crs_wiper_op", 2)
            # 400ms : laisser wc_available=True + dtc_inactivate se propager
            QTimer.singleShot(400, _t_b2009_start)

        elif tid == "T50b":
            self.log_msg.emit("  → T50b : CAS B SPEED1 + inject_motor_current + 0x202 ErrorCode=0x03 → B2001")
            if self._rte_client:
                self._rte_client.set_cmd("wc_available",   True)
                self._rte_client.set_cmd("lin_op_locked",  True)
                self._rte_client.set_cmd("crs_wiper_op",   0)
                self._rte_client.set_cmd("ignition_status", 1)
                # B2001 INACTIVE avant injection pour affichage complet
                QTimer.singleShot(200, lambda: self._rte_client and
                    self._rte_client.set_cmd("dtc_inactivate", "B2001"))
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

            def _t50b_start():
                self._lin_w.queue_send({"cmd": "SPEED1"})
                if self._rte_client:
                    self._rte_client.set_cmd("crs_wiper_op", 2)

            def _t50b_inject():
                # Étape 1 : injecter overcurrent moteur (> OVERCURRENT_THRESH=0.8A)
                # reset_t0 volontairement PAS ici : on attend que le courant soit
                # effectivement lu par le BCM avant de démarrer le chrono.
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.inject_motor_current(0.95)
                    self.log_msg.emit("  → T50b inject_motor_current 0.950A")

            def _t50b_start_chrono():
                # Étape 2 : reset_t0 ICI → chrono démarre.
                # Le courant 0.95A est déjà dans _sensor_state → _build_0x202_from_state()
                # génère naturellement Err=0x03 au prochain cycle 0x200 reçu.
                # Plus besoin de send_wiper_ack — le simulateur WC est la source de vérité.
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                if hasattr(test, "notify_ack_err_sent"):
                    test.notify_ack_err_sent()
                self.log_msg.emit("  → T50b chrono démarré — 0x202 Err=0x03 généré naturellement")

            # 400ms : wc_available propagé → SPEED1
            QTimer.singleShot(400, _t50b_start)
            # 700ms : SPEED1 stabilisé → injection courant
            QTimer.singleShot(700, _t50b_inject)
            # 1100ms : courant dans _sensor_state → prochain 0x202 aura Err=0x03 automatique
            QTimer.singleShot(1100, _t50b_start_chrono)

        elif tid == "T50c":
            self.log_msg.emit("  → T50c : CAS B SPEED1 + inject_motor_current + 0x202 ErrorCode=0x01 → B2001 NON attendu")
            if self._rte_client:
                self._rte_client.set_cmd("wc_available",   True)
                self._rte_client.set_cmd("lin_op_locked",  True)
                self._rte_client.set_cmd("crs_wiper_op",   0)
                self._rte_client.set_cmd("ignition_status", 1)
                QTimer.singleShot(200, lambda: self._rte_client and
                    self._rte_client.set_cmd("dtc_inactivate", "B2001"))
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

            def _t50c_start():
                self._lin_w.queue_send({"cmd": "SPEED1"})
                if self._rte_client:
                    self._rte_client.set_cmd("crs_wiper_op", 2)

            def _t50c_inject():
                # FIX : courant SOUS le seuil (0.3A < 0.8A)
                # _build_0x202_from_state() ne génèrera pas Err=0x03 automatiquement
                # → seul le send_wiper_ack(Err=0x01) manuel influencera le BCM
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.inject_motor_current(0.30)
                    self.log_msg.emit("  → T50c inject_motor_current 0.300A (< seuil 0.8A)")

            def _t50c_send_ack_err():
                # reset_t0 ici : chrono démarre au moment du NACK
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # ErrorCode=0x01 (≠ 0x03) + courant 0.3A < seuil
                # → condition conjonctive B2001 incomplète → B2001 ne doit pas déclencher
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.send_wiper_ack(ack_status=1, error_code=0x01, alive=0)
                    self.log_msg.emit("  → T50c send_wiper_ack NACK ErrorCode=0x01 (≠ 0x03, courant < seuil)")

            QTimer.singleShot(400, _t50c_start)
            QTimer.singleShot(700, _t50c_inject)
            QTimer.singleShot(1100, _t50c_send_ack_err)

        elif tid == "T50d":
            self.log_msg.emit("  → T50d : CAS B SPEED1 + courant normal 0.30A + 0x202 ErrorCode=0x03 → B2001 NON attendu")
            if self._rte_client:
                self._rte_client.set_cmd("wc_available",   True)
                self._rte_client.set_cmd("lin_op_locked",  True)
                self._rte_client.set_cmd("crs_wiper_op",   0)
                self._rte_client.set_cmd("ignition_status", 1)
                QTimer.singleShot(200, lambda: self._rte_client and
                    self._rte_client.set_cmd("dtc_inactivate", "B2001"))
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

            def _t50d_start():
                self._lin_w.queue_send({"cmd": "SPEED1"})
                if self._rte_client:
                    self._rte_client.set_cmd("crs_wiper_op", 2)

            def _t50d_inject():
                # Courant NORMAL (< seuil 0.8A) → condition courant fausse
                # reset_t0 volontairement pas ici
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.inject_motor_current(0.30)
                    self.log_msg.emit("  → T50d inject_motor_current 0.300A (< seuil)")

            def _t50d_send_ack_err():
                # reset_t0 ici : chrono démarre au moment du NACK
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # AckStatus=1 (NACK) + ErrorCode=0x03 : wc_ack_overcurrent=True posé
                # Mais courant=0.30A < seuil 0.8A → condition TRIPLE non satisfaite → pas de B2001
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.send_wiper_ack(ack_status=1, error_code=0x03, alive=0)
                    self.log_msg.emit("  → T50d send_wiper_ack NACK ErrorCode=0x03 (courant < seuil)")

            QTimer.singleShot(400, _t50d_start)
            QTimer.singleShot(700, _t50d_inject)
            QTimer.singleShot(1100, _t50d_send_ack_err)


        elif tid == "T_B2009_CASA":
            self.log_msg.emit("  → T_B2009_CASA : CAS A SPEED1 sans rest_contact → B2009")
            if self._rte_client:
                self._rte_client.set_cmd("wc_available",   False)
                self._rte_client.set_cmd("crs_wiper_op",   0)
                self._rte_client.set_cmd("ignition_status", 1)
                # rest_contact_sim_active=True + rest_contact_sim=False :
                # lame figée AU REPOS → NE555 hardware ignoré → aucun cycle
                # La garde BCM laisse passer B2009 quand sim=False (lame bloquée)
                self._rte_client.set_cmd("rest_contact_sim_active", True)
                self._rte_client.set_cmd("rest_contact_sim", False)
                # B2009 INACTIVE avant test pour affichage complet
                QTimer.singleShot(200, lambda: self._rte_client and
                    self._rte_client.set_cmd("dtc_inactivate", "B2009"))
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

            def _t_b2009_casa_start():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                self._lin_w.queue_send({"cmd": "SPEED1"})
                if self._rte_client:
                    self._rte_client.set_cmd("crs_wiper_op", 2)
            # 400ms : laisser dtc_inactivate se propager avant le stimulus
            QTimer.singleShot(400, _t_b2009_casa_start)

        # ── TC_LIN_016_BIT4 : StickStatus bit4=0 → B2004 après 2.5s ─────────
        elif tid == "TC_LIN_016_BIT4":
            self.log_msg.emit("  → TC_LIN_016_BIT4 : StickStatus bit4=0 (Valid=0) → B2004 après 2.5s")
            if self._rte_client:
                self._rte_client.set_cmd("wc_available",        False)
                # Remettre lin_timeout_active=False AVANT reset_t0 :
                # évite que _check_rte détecte un True résiduel du test précédent
                self._rte_client.set_cmd("lin_timeout_active",  False)
                self._rte_client.set_cmd("crs_wiper_op",        0)
                self._rte_client.set_cmd("ignition_status",     1)
            def _bit4_start():
                # reset_t0 ICI (après stabilisation Redis) :
                # garantit que _check_rte ne démarre la mesure qu'après
                # l'injection du stimulus, pas depuis le début du setup.
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # send_bit4_zero : force stick_status bit0=0 (Valid=0) uniquement
                # maintenu en continu → bcm_protocol.py accumule cycles bit4=0
                # → B2004 ACTIVE + lin_timeout_active=True après 2.5s
                self._lin_w.queue_send({"test_cmd": "send_bit4_zero"})
                self.log_msg.emit("  → StickStatus bit4=0 injecté — attente B2004 (2.5s)")
            # 600ms : laisser le BCM sortir de tout état OFF résiduel
            # (≥ 1 cycle LIN 400ms) avant d'injecter le stimulus
            QTimer.singleShot(600, _bit4_start)

        # ── TC_LIN_016_BIT6_ALONE : bit6=1 seul → B2011 NON déclenché ────────
        elif tid == "TC_LIN_016_BIT6_ALONE":
            self.log_msg.emit("  → TC_LIN_016_BIT6_ALONE : Stuck bit6=1 sans 0x17 bit0")
            if self._rte_client:
                self._rte_client.set_cmd("wc_available",    False)
                self._rte_client.set_cmd("crs_wiper_op",    0)
                self._rte_client.set_cmd("ignition_status", 1)
                self._rte_client.set_cmd("b2011_active",    False)
            # Purger résidu TC_B2011_AND : s'assurer que 0x17 bit0=0 ET bit6=0
            # via _lin_w (crslin.py port 5555) — sim_client envoie sur port 5000
            self._lin_w.queue_send({"set_fault": 0})
            self._lin_w.queue_send({"set_stuck": False})
            def _bit6_alone_start():
                # Injecter bit6=1 (Stuck) UNIQUEMENT — 0x17 bit0 reste à 0
                # → condition ET incomplète → B2011 ne doit PAS se déclencher
                self._lin_w.queue_send({"set_stuck": True})
                self.log_msg.emit("  → Stuck bit6=1 activé (0x17 bit0=0 → B2011 impossible)")
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
            # 800ms : laisse la queue _lin_w se vider des résidus TC_B2011_AND
            # (set_fault=1 + set_stuck=True) avant d'injecter le stimulus
            QTimer.singleShot(800, _bit6_alone_start)

        # ── TC_B2011_AND : bit6=1 ET 0x17 bit0=1 ≥ 10s → B2011 ─────────────
        elif tid == "TC_B2011_AND":
            self.log_msg.emit("  → TC_B2011_AND : 0x16 bit6=1 ET 0x17 bit0=1 → B2011 après 10s")
            if self._rte_client:
                self._rte_client.set_cmd("wc_available",    False)
                self._rte_client.set_cmd("crs_wiper_op",    0)
                self._rte_client.set_cmd("ignition_status", 1)
                # Ne pas écrire b2011_active ici : flag interne BCM géré par bcm_protocol
                # Le reset post_test l'écrit via REDIS_WRITABLE_KEYS
            # Nettoyer conditions résiduelles côté simulateur (via _lin_w → crslin port 5555)
            self._lin_w.queue_send({"set_stuck": False})
            self._lin_w.queue_send({"set_fault": 0})
            def _b2011_inject():
                # Étape 1 : 0x17 bit0=1 (CRS_InternalFault_Stick)
                # set_fault → crslin.py port 5555 via _lin_w.queue_send
                self._lin_w.queue_send({"set_fault": 1})
                self.log_msg.emit("  → CRS_InternalFault_Stick (0x17 bit0=1) injecté")
                # Étape 2 : 0x16 bit6=1 (Stuck) — les 2 conditions actives → timer 10s
                def _inject_stuck():
                    self._lin_w.queue_send({"set_stuck": True})
                    self.log_msg.emit("  → Stuck bit6=1 injecté — timer 10s démarré")
                    if hasattr(test, "reset_t0"):
                        test.reset_t0()
                QTimer.singleShot(200, _inject_stuck)
            QTimer.singleShot(500, _b2011_inject)

        # ── TC_LIN_017_VER : CRS_Version=0xFF → trame ignorée ───────────────
        elif tid == "TC_LIN_017_VER":
            self.log_msg.emit("  → TC_LIN_017_VER : CRS_Version=0xFF → trame rejetée")
            if self._rte_client:
                self._rte_client.set_cmd("wc_available",     False)
                self._rte_client.set_cmd("crs_wiper_op",     0)
                self._rte_client.set_cmd("ignition_status",  1)
                # Remettre lin_timeout_active=False : évite que B2004 résiduel
                # du test précédent (TC_LIN_016_BIT4) se déclenche pendant ce test
                self._rte_client.set_cmd("lin_timeout_active", False)
            def _ver_ff_start():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # Injecter via _lin_w (crslin.py port 5555)
                self._lin_w.queue_send({"set_crs_version": 0xFF})
                self._lin_w.queue_send({"set_fault": 1})   # bit0=1 (serait B2011 si trame acceptée)
                self.log_msg.emit("  → CRS_Version=0xFF + fault_stick=1 injectés")
            # 800ms : laisser le BCM recevoir au moins 2 cycles LIN 0x16 valides
            # pour réinitialiser t_last_lin0x16 avant de lancer le test
            QTimer.singleShot(800, _ver_ff_start)

        # ── TC_CAN_202_ERR01 : 0x200 SPEED1 → 0x201 OFF (mismatch) → 0x202 NACK+0x01 naturel ──
        elif tid == "TC_CAN_202_ERR01":
            self.log_msg.emit("  → TC_CAN_202_ERR01 : activation mismatch en premier, puis 0x200 SPEED1")
            # Réinitialiser _last_201 et _last_200 dans CANBusPanel :
            # évite que la 1ère 0x200 reçue calcule mode_mismatch ou FaultStatus
            # à partir de trames résiduelles du test précédent → FAIL immédiat.
            if self._can_panel is not None:
                self._can_panel.reset_wc_state()
            # FIX anti-pollution :
            # Si un test précédent (ERR02/ERR04/ERR05) s'est terminé récemment, ses faults
            # (motor_driver_fault, xcp_position_sensor_fault, xcp_internal_fault) peuvent
            # encore avoir leur bit actif dans get_fault_byte_for_tx() à cause du healing 1s.
            # panels.py donne la priorité bit0 > bit1 > bit2 pour ErrorCode → un bit résiduel
            # ferait émettre 0x202 Err=0x02/0x04/0x05 au lieu du 0x01 attendu → FAIL.
            # Solution : forcer tous les faults à False avant d'armer le mismatch.
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.set_motor_driver_fault(False)
                self._sim_client.set_xcp_position_sensor_fault(False)
                self._sim_client.set_xcp_internal_fault(False)
                self.log_msg.emit("  → [anti-pollution] motor_driver/pos_sensor/internal faults purgés")

            # Étape 1 : activer le mismatch AVANT d'ouvrir le bus CAN
            # → garantit que la 1ère trame 0x201 émise par le simulateur
            #   aura déjà CurrentMode=OFF, évitant tout ACK=0x00 intempestif
            # FIX re-run ERR01 : reset_mode_mismatch() d'abord pour purger un éventuel
            # mismatch résiduel d'un run précédent, puis attendre 800ms (≥ 2×400ms)
            # pour que _last_201 dans panels.py soit rechargé avec CurrentMode nominal.
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.reset_mode_mismatch()   # purge résidu run précédent
                self._sim_client.set_mode_mismatch()
                self.log_msg.emit("  → mode_mismatch_0x201 activé (CurrentMode=OFF)")
            else:
                self.log_msg.emit("  [WARN] TC_CAN_202_ERR01 : sim_client non connecté")

            def _err01_start():
                # Étape 2 : mettre le BCM en SPEED1 (après stabilisation du mismatch)
                #   crs_wiper_op=2 → BCM émet 0x200 WiperMode=SPEED1
                #   wc_available=True  → CAS B actif → échange 0x200/0x201/0x202
                #   lin_op_locked=True → verrouille crs_wiper_op contre LIN 0x16
                if self._rte_client:
                    self._rte_client.set_cmd("wc_available",    True)
                    self._rte_client.set_cmd("lin_op_locked",   True)
                    self._rte_client.set_cmd("crs_wiper_op",    2)   # SPEED1
                    self._rte_client.set_cmd("ignition_status", 1)
                self.log_msg.emit("  → BCM en SPEED1 (0x200 WiperMode=SPEED1 en cours)")

                # Étape 3 : démarrer le chrono
                #   La 1ère trame 0x201 répondra CurrentMode=OFF (mismatch actif)
                #   → le WC émet naturellement 0x202 NACK=1 + ErrorCode=0x01
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                self.log_msg.emit("  → chrono démarré — attente 0x202 naturelle (NACK=1, ErrorCode=0x01)")

            # Délai 800ms (≥ 2×400ms cycle 0x201) : laisse panels.py recharger _last_201
            # avec CurrentMode nominal avant l'ouverture du bus → pas de mismatch résiduel
            QTimer.singleShot(800, _err01_start)

        # ── TC_CAN_202_ERR02 : DTC B2102 → 0x201 bit1=MotorDriver → 0x202 NACK+0x02 → BCM OFF ──
        elif tid == "TC_CAN_202_ERR02":
            self.log_msg.emit("  → TC_CAN_202_ERR02 : déclencher B2102 → FaultStatus_MotorDriver=1 → 0x202 Err=0x02")
            # Réinitialiser _last_201 et _last_200 dans CANBusPanel :
            # évite que la 1ère 0x200 reçue calcule mode_mismatch ou FaultStatus
            # à partir de trames résiduelles du test précédent → FAIL immédiat.
            if self._can_panel is not None:
                self._can_panel.reset_wc_state()
            # FIX anti-pollution ERR01→ERR02 :
            # TC_CAN_202_ERR01 active mode_mismatch. Son cleanup appelle reset_mode_mismatch(),
            # mais _last_201 dans panels.py garde encore CurrentMode=OFF de la dernière trame
            # 0x201 reçue pendant ERR01. La 1ère trame 0x200 SPEED1 d'ERR02 produit alors
            # mode_mismatch=True dans panels.py → 0x202 Err=0x01 au lieu de 0x02 → FAIL.
            # Solution : reset_mode_mismatch() ICI + attendre 800ms (≥ 2×400ms cycle 0x201)
            # pour que _last_201 soit rechargé avec CurrentMode nominal avant d'ouvrir le bus.
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.reset_mode_mismatch()   # purge résidu ERR01
                self.log_msg.emit("  → [anti-pollution] reset_mode_mismatch forcé avant armer B2102")
            # Étape 1 : déclencher B2102 (WC Motor Driver Fault) AVANT d'ouvrir le bus
            # → motor_driver_fault=True → get_fault_byte_for_tx() met bit1=1 dans 0x201
            # → panels.py détecte bit1 et émet 0x202 NACK+0x02 naturellement
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.set_motor_driver_fault(True)
                self.log_msg.emit("  → B2102 armé : motor_driver_fault=True → 0x201 bit1=1 automatique")
            def _err02_start():
                if self._rte_client:
                    self._rte_client.set_cmd("wc_available",    True)
                    self._rte_client.set_cmd("lin_op_locked",   True)
                    self._rte_client.set_cmd("crs_wiper_op",    2)   # SPEED1
                    self._rte_client.set_cmd("ignition_status", 1)
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                self.log_msg.emit("  → chrono démarré — attente 0x201 bit1=1 + 0x202 NACK+0x02 + BCM→OFF")
            # Délai 800ms (≥ 2×400ms cycle 0x201) : laisse panels.py recharger _last_201
            # avec CurrentMode nominal → mode_mismatch=False garanti à l'ouverture du bus
            QTimer.singleShot(800, _err02_start)

        # ── TC_CAN_202_ERR04 : DTC B2103 → 0x201 bit2=PosSensor → 0x202 NACK+0x04 → B2006 ──
        elif tid == "TC_CAN_202_ERR04":
            self.log_msg.emit("  → TC_CAN_202_ERR04 : déclencher B2103 → FaultStatus_PosSensor=1 → 0x202 Err=0x04 → B2006")
            # Réinitialiser _last_201 et _last_200 dans CANBusPanel :
            # évite que la 1ère 0x200 reçue calcule mode_mismatch ou FaultStatus
            # à partir de trames résiduelles du test précédent → FAIL immédiat.
            if self._can_panel is not None:
                self._can_panel.reset_wc_state()
            # FIX anti-pollution ERR02→ERR04 :
            # TC_CAN_202_ERR02 arme motor_driver_fault=True. Son cleanup appelle
            # set_motor_driver_fault(False), mais _check_motor_driver_fault() dans bcmcan.py
            # a un healing de 1s → bit1 FaultStatus_MotorDriver reste à 1 dans 0x201 pendant
            # ~1s après la fin d'ERR02. panels.py donne la priorité bit1 (Err=0x02) sur
            # bit2 (Err=0x04) → ERR04 reçoit un NACK 0x02 au lieu du 0x04 attendu → FAIL.
            # De même, xcp_internal_fault résiduel d'ERR05 produirait Err=0x05 au lieu de 0x04.
            # Solution : forcer les deux faults à False ICI et attendre 1200ms (> healing 1s)
            # avant d'ouvrir le bus CAN → bits 0 et 1 garantis à 0.
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.reset_mode_mismatch()            # purge résidu ERR01
                self._sim_client.set_motor_driver_fault(False)    # purge résidu ERR02
                self._sim_client.set_xcp_internal_fault(False)    # purge résidu ERR05
                self.log_msg.emit("  → [anti-pollution] reset_mode_mismatch + motor_driver + xcp_internal purgés avant armer B2103")
            # Étape 1 : déclencher B2103 (WC PosSensor Fault) via xcp_position_sensor_fault
            # → get_fault_byte_for_tx() met bit2=1 dans 0x201 automatiquement
            # → panels.py détecte bit2 et émet 0x202 NACK+0x04 naturellement
            # → BCM : condition conjointe (0x201 bit2=1 ET 0x202 code=0x04) → B2006
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.set_xcp_position_sensor_fault(True)
                self.log_msg.emit("  → B2103 armé : xcp_position_sensor_fault=True → 0x201 bit2=1 automatique")
            def _err04_start():
                if self._rte_client:
                    # Nettoyer les résidus B2006/B2103 d'un run précédent AVANT d'ouvrir
                    # le bus — évite que _check_rte détecte une valeur Redis périmée
                    self._rte_client.set_cmd("wc_b2006_active",           False)
                    self._rte_client.set_cmd("wc_b2103_active",           False)
                    self._rte_client.set_cmd("wc_ack_pos_fault",          False)
                    self._rte_client.set_cmd("rest_contact_b2006_active", 0)
                    # Ouvrir le bus et commander SPEED1
                    self._rte_client.set_cmd("wc_available",    True)
                    self._rte_client.set_cmd("lin_op_locked",   True)
                    self._rte_client.set_cmd("crs_wiper_op",    2)   # SPEED1
                    self._rte_client.set_cmd("ignition_status", 1)
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                self.log_msg.emit("  → chrono démarré — attente 0x201 bit2=1 + 0x202 NACK+0x04 + B2006")
            # Délai 1200ms (au lieu de 300ms) : laisse le healing motor_driver_fault (1s)
            # s'achever avant d'ouvrir le bus → bit1=0 garanti dans get_fault_byte_for_tx()
            QTimer.singleShot(1200, _err04_start)

        # ── TC_CAN_202_ERR05 : DTC B2101 → 0x201 bit0=WC_Internal → 0x202 NACK+0x05 → BCM OFF ──
        elif tid == "TC_CAN_202_ERR05":
            self.log_msg.emit("  → TC_CAN_202_ERR05 : déclencher B2101 → FaultStatus_WC_Internal=1 → 0x202 Err=0x05")
            # Réinitialiser _last_201 et _last_200 dans CANBusPanel :
            # évite que la 1ère 0x200 reçue calcule mode_mismatch ou FaultStatus
            # à partir de trames résiduelles du test précédent → FAIL immédiat.
            if self._can_panel is not None:
                self._can_panel.reset_wc_state()
            # FIX anti-pollution ERR02→ERR05 :
            # TC_CAN_202_ERR02 arme motor_driver_fault=True côté simulateur.
            # Son cleanup appelle set_motor_driver_fault(False), mais _check_motor_driver_fault()
            # dans bcmcan.py a un healing de 1s → bit1 FaultStatus_MotorDriver reste à 1 dans
            # 0x201 pendant ~1s après la fin d'ERR02. Comme panels.py donne la priorité
            # fault_motor_driver (→ ErrorCode=0x02) sur fault_wc_internal (→ ErrorCode=0x05),
            # ERR05 reçoit un NACK 0x02 au lieu du 0x05 attendu → FAIL.
            # Solution : forcer motor_driver_fault=False ICI (avant d'armer B2101) et attendre
            # 1200ms (> healing 1s) avant d'ouvrir le bus CAN → bit1 garanti à 0.
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.reset_mode_mismatch()                # purge résidu ERR01
                self._sim_client.set_motor_driver_fault(False)        # purge résidu ERR02
                self._sim_client.set_xcp_position_sensor_fault(False) # purge résidu ERR04
                self.log_msg.emit("  → [anti-pollution] reset_mode_mismatch + motor_driver + xcp_pos_sensor purgés avant armer B2101")
            # Étape 1 : déclencher B2101 (WC Internal Fault) via xcp_internal_fault
            # → get_fault_byte_for_tx() met bit0=1 dans 0x201 automatiquement
            # → panels.py détecte bit0 et émet 0x202 NACK+0x05 naturellement
            # → BCM : _check_wc_fault_status → WiperMode=OFF
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.set_xcp_internal_fault(True)
                self.log_msg.emit("  → B2101 armé : xcp_internal_fault=True → 0x201 bit0=1 automatique")
            def _err05_start():
                if self._rte_client:
                    self._rte_client.set_cmd("wc_available",    True)
                    self._rte_client.set_cmd("lin_op_locked",   True)
                    self._rte_client.set_cmd("crs_wiper_op",    2)   # SPEED1
                    self._rte_client.set_cmd("ignition_status", 1)
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                self.log_msg.emit("  → chrono démarré — attente 0x201 bit0=1 + 0x202 NACK+0x05 + BCM→OFF")
            # Délai 1200ms (au lieu de 300ms) : laisse le healing motor_driver_fault (1s)
            # s'achever avant d'ouvrir le bus → bit1=0 garanti dans get_fault_byte_for_tx()
            QTimer.singleShot(1200, _err05_start)

        # ── TC_B2104 : 3 NACKs consécutifs → B2104 ACTIVE ───────────────────
        elif tid == "TC_B2104":
            self.log_msg.emit("  → TC_B2104 : 3 NACKs (AckStatus=1, ErrorCode=0x01) → B2104")
            # Pré-conditions : wc_available=True pour que la file 0x202 soit active
            if self._rte_client:
                self._rte_client.set_cmd("wc_available",   True)
                self._rte_client.set_cmd("crs_wiper_op",  2)   # SPEED1 → WC reçoit 0x200
                self._rte_client.set_cmd("ignition_status", 1)
                self._rte_client.set_cmd("lin_op_locked",  True)
            # Réinitialiser B2104 avant le test
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.reset_b2104()

            def _b2104_inject():
                if hasattr(test, "reset_t0"):
                    test.reset_t0()
                # Envoyer 3 NACKs consécutifs avec 50ms d'intervalle
                # (< 400ms cycle ACK auto → les 3 NACKs passent avant le prochain ACK)
                # Le 3e NACK déclenchera B2104 côté WC via _b2104_track()
                for i in range(3):
                    def _send_nack(idx=i):
                        alive = idx & 0xFF
                        ok = self._sim_client.send_wiper_ack(
                            ack_status=1, error_code=0x01, alive=alive) \
                            if (self._sim_client and self._sim_client.is_connected()) else False
                        self.log_msg.emit(
                            f"  → NACK 0x01 #{idx+1}/3 {'envoyé' if ok else 'échec'}")
                        # Notifier le test que le 3e NACK vient d'être envoyé
                        if idx == 2 and hasattr(test, "notify_nack3_sent"):
                            test.notify_nack3_sent()
                    QTimer.singleShot(i * 50, _send_nack)
            QTimer.singleShot(600, _b2104_inject)

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
        elif tid in ("T03", "T04", "T05"):
            self.log_msg.emit(f"  → {tid} post : wc_available=False (retour CAS A)")
            if self._rte_client:
                self._rte_client.set_cmd("wc_available", False)
        elif tid == "T11":
            self.log_msg.emit("  → start_can_tx")
            self._motor_w.queue_send({"test_cmd": "start_can_tx"})
            if self._rte_client:
                # Remettre wc_available=False (CAS A) après le test
                QTimer.singleShot(200, lambda: self._rte_client and
                    self._rte_client.set_cmd("wc_available", False))
                QTimer.singleShot(400, lambda: self._rte_client and
                    self._rte_client.set_cmd("wc_timeout_active", False))
                QTimer.singleShot(600, lambda: self._rte_client and (
                    self._rte_client.set_cmd("ignition_status", 1) or
                    self._rte_client.set_cmd("crs_wiper_op", 0)
                ))
        elif tid == "T21":
            # Invalider génération pour stopper QTimers cycling résiduels
            self._rc_gen = getattr(self, "_rc_gen", 0) + 1
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op", 0)
                self._rte_client.set_cmd("rest_contact_sim", True)  # maintenir True
            self._lin_w.queue_send({"cmd": "OFF"})
            # Attendre que BCM soit en OFF (max 4s) avant de désactiver sim.
            # Cela permet aux cycles restants de compléter sans déclencher B2009.
            if self._rte_client:
                _t21_gen = getattr(self, "_rc_gen", 0)
                _t21_attempts = [0]
                def _t21_wait_off():
                    if not self._rte_client:
                        return
                    state = self._rte_client.get("state")
                    _t21_attempts[0] += 1
                    if state == "OFF" or _t21_attempts[0] >= 20:  # max 20×200ms=4s
                        self._rte_client.set_cmd("rest_contact_sim_active", False)
                        self._rte_client.set_cmd("rest_contact_sim", False)
                        self._rte_client.set_cmd("crs_wiper_op", 0)
                    else:
                        QTimer.singleShot(200, _t21_wait_off)
                QTimer.singleShot(200, _t21_wait_off)
        elif tid == "T22":
            # Même logique que T21 : garder sim=True jusqu'à state=OFF
            self._rc_gen = getattr(self, "_rc_gen", 0) + 1
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op", 0)
                self._rte_client.set_cmd("rest_contact_sim", True)  # maintenir True
            self._lin_w.queue_send({"cmd": "OFF"})
            if self._rte_client:
                _t22_attempts = [0]
                def _t22_wait_off():
                    if not self._rte_client:
                        return
                    state = self._rte_client.get("state")
                    _t22_attempts[0] += 1
                    if state == "OFF" or _t22_attempts[0] >= 20:
                        self._rte_client.set_cmd("rest_contact_sim_active", False)
                        self._rte_client.set_cmd("rest_contact_sim", False)
                        self._rte_client.set_cmd("crs_wiper_op", 0)
                    else:
                        QTimer.singleShot(200, _t22_wait_off)
                QTimer.singleShot(200, _t22_wait_off)
        elif tid == "T40":
            # Cleanup T40 : même séquence que l'ancien T20
            if self._rte_client:
                self._rte_client.set_cmd("rest_contact_sim_active", False)
                self._rte_client.set_cmd("rest_contact_sim",        False)
                self._rte_client.set_cmd("crs_wiper_op", 0)
            self._lin_w.queue_send({"cmd": "OFF"})
        elif tid in ("T30", "T31", "T32", "T34", "T35", "T36", "T37", "T38"):
            # Remettre le BCM en OFF
            # T30/T31/T32 : LIN OFF obligatoire pour que crslin repasse en OFF
            # (stimulus était LIN — crslin a son état interne à réinitialiser)
            if tid in ("T30", "T31", "T32"):
                self._lin_w.queue_send({"cmd": "OFF"})
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op", 0)
                self._rte_client.set_cmd("ignition_status", 1)
                self._rte_client.set_cmd("rain_intensity", 0)
                if tid in ("T34", "T35"):
                    # Invalider _rc_gen : stoppe les QTimers cycling rest_contact
                    # résiduels qui continuaient à toggler après la fin du test.
                    self._rc_gen = getattr(self, "_rc_gen", 0) + 1
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
                    self._lin_w.queue_send({"cmd": "OFF"})
                    self._rte_client.set_cmd("crs_wiper_op", 0)
            # Toujours sync simulateur : ignition ON pour éviter boucle OFF→SPEED1
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
        elif tid == "T38b":
            self.log_msg.emit("  → T38b cleanup : motor_current=0 EN PREMIER")
            if self._rte_client:
                self._rte_client.set_cmd("motor_current_a", 0.0)
            self._lin_w.queue_send({"cmd": "OFF"})
            def _t38b_reset():
                if self._rte_client:
                    self._rte_client.set_cmd("crs_wiper_op", 0)
                    self._rte_client.set_cmd("rear_motor_error", False)
            QTimer.singleShot(150, _t38b_reset)
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
        elif tid == "T38c":
            self.log_msg.emit("  → T38c cleanup : pump_current=0 EN PREMIER, puis reset erreur pompe")
            if self._rte_client:
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                self._rte_client.set_cmd("pump_current_a", 0.0)
                # Garder rest_contact_sim=True jusqu'à ERROR→OFF :
                # remettre False immédiatement crée une race → _check_rest_contact_stuck
                # voit front_motor_on=True + GPIO False avant que _enter_error(pump_only)
                # n'ait posé _rest_contact_b2009_active=True → B2009 parasite.
                self._rte_client.set_cmd("rest_contact_sim", True)
            self._lin_w.queue_send({"cmd": "OFF"})
            def _t38c_reset():
                if self._rte_client:
                    self._rte_client.set_cmd("pump_error", False)
                    self._rte_client.set_cmd("crs_wiper_op", 0)
                    # Désactiver rest_contact_sim après ERROR→OFF confirmé
                    # (400ms >> cycle T-WSM 200ms → BCM est en OFF)
                    self._rte_client.set_cmd("rest_contact_sim_active", False)
                    self._rte_client.set_cmd("rest_contact_sim", False)
            QTimer.singleShot(400, _t38c_reset)
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
        elif tid == "LIN_INVALID_CMD_001":
            self.log_msg.emit("  → LIN_INVALID_CMD_001 cleanup : rien à restaurer")
            # Aucun état BCM modifié — simple nettoyage défensif
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op", 0)
        elif tid == "T_RAIN_AUTO_SENSOR_ERROR":
            self.log_msg.emit("  → T_RAIN_AUTO_SENSOR_ERROR cleanup : rain_sensor OK + rest_contact_sim OFF")
            if self._rte_client:
                self._rte_client.set_cmd("rain_sensor_ok", True)
                self._rte_client.set_cmd("rain_intensity", 0)
                self._rte_client.set_cmd("rain_sensor_installed", False)
                self._rte_client.set_cmd("crs_wiper_op", 0)
                self._rte_client.set_cmd("rest_contact_sim_active", False)
                self._rte_client.set_cmd("rest_contact_sim", False)
            self._lin_w.queue_send({"cmd": "OFF"})
            self._motor_w.queue_send({"rain_intensity": 0, "sensor_status": "OK"})
        elif tid == "T_CAS_B_SPEED1_REVERSE":
            self.log_msg.emit("  → T_CAS_B_SPEED1_REVERSE cleanup : stop reverse + blade_cycling + OFF")
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.stop_blade_cycling()
                self._sim_client.reset_b2101()
            self._lin_w.queue_send({"cmd": "OFF"})
            if self._rte_client:
                self._rte_client.set_cmd("reverse_gear",         False)
                self._rte_client.set_cmd("crs_wiper_op",         0)
                self._rte_client.set_cmd("lin_op_locked",        False)
                self._rte_client.set_cmd("wc_available",         False)
                self._rte_client.set_cmd("rear_wiper_available", True)
                QTimer.singleShot(400, lambda: self._rte_client and (
                    self._rte_client.set_cmd("wc_timeout_active",  False) or
                    self._rte_client.set_cmd("lin_timeout_active", False)
                ))
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

        elif tid == "T_B2009_CAN":
            self.log_msg.emit("  → T_B2009_CAN cleanup : unfreeze blade + reset B2009 + OFF")
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.unfreeze_blade_position()
                self._sim_client.reset_b2101()
            self._lin_w.queue_send({"cmd": "OFF"})
            if self._rte_client:
                self._rte_client.set_cmd("rest_contact_sim_active", False)
                self._rte_client.set_cmd("rest_contact_sim",        False)
                self._rte_client.set_cmd("crs_wiper_op",  0)
                self._rte_client.set_cmd("lin_op_locked", False)
                self._rte_client.set_cmd("wiper_fault", False)
                self._rte_client.set_cmd("_rest_contact_b2009_active", False)
                self._rte_client.set_cmd("wc_available", False)
                QTimer.singleShot(400, lambda: self._rte_client and (
                    self._rte_client.set_cmd("wc_timeout_active",  False) or
                    self._rte_client.set_cmd("lin_timeout_active", False)
                ))
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
        elif tid == "T50b":
            self.log_msg.emit("  → T50b cleanup : reset_motor_current + OFF")
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.reset_motor_current()
                self._sim_client.reset_b2101()
            self._lin_w.queue_send({"cmd": "OFF"})
            if self._rte_client:
                self._rte_client.set_cmd("motor_current_a",  0.0)
                self._rte_client.set_cmd("crs_wiper_op",     0)
                self._rte_client.set_cmd("lin_op_locked",    False)
                self._rte_client.set_cmd("front_motor_error", False)
                self._rte_client.set_cmd("wc_available",     False)
                # B2001 remis INACTIVE pour affichage complet au prochain run
                QTimer.singleShot(300, lambda: self._rte_client and
                    self._rte_client.set_cmd("dtc_inactivate", "B2001"))
                QTimer.singleShot(400, lambda: self._rte_client and (
                    self._rte_client.set_cmd("wc_timeout_active",  False) or
                    self._rte_client.set_cmd("lin_timeout_active", False)
                ))
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

        elif tid in ("T50c", "T50d"):
            self.log_msg.emit(f"  → {tid} cleanup : reset_motor_current + OFF")
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.reset_motor_current()
            self._lin_w.queue_send({"cmd": "OFF"})
            if self._rte_client:
                self._rte_client.set_cmd("motor_current_a",  0.0)
                self._rte_client.set_cmd("crs_wiper_op",     0)
                self._rte_client.set_cmd("lin_op_locked",    False)
                self._rte_client.set_cmd("front_motor_error", False)
                self._rte_client.set_cmd("wc_available",     False)
                QTimer.singleShot(300, lambda: self._rte_client and
                    self._rte_client.set_cmd("dtc_inactivate", "B2001"))
                QTimer.singleShot(400, lambda: self._rte_client and (
                    self._rte_client.set_cmd("wc_timeout_active",  False) or
                    self._rte_client.set_cmd("lin_timeout_active", False)
                ))
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

        elif tid == "T_B2009_CASA":
            self.log_msg.emit("  → T_B2009_CASA cleanup : reset B2009 + OFF")
            self._lin_w.queue_send({"cmd": "OFF"})
            if self._rte_client:
                self._rte_client.set_cmd("rest_contact_sim_active", False)
                self._rte_client.set_cmd("rest_contact_sim",        False)
                self._rte_client.set_cmd("crs_wiper_op",               0)
                self._rte_client.set_cmd("wiper_fault",                 False)
                self._rte_client.set_cmd("_rest_contact_b2009_active",  False)
                self._rte_client.set_cmd("ignition_status",             1)
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
        elif tid == "T33":
            if self._rte_client:
                self._rte_client.set_cmd("ignition_status", 1)
                self._rte_client.set_cmd("crs_wiper_op", 0)
            # Toujours sync simulateur (pas seulement en fallback)
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            self._lin_w.queue_send({"cmd": "OFF"})
            # Attendre 800ms que le BCM prenne ignition=1 avant le prochain test
            QTimer.singleShot(800, lambda: None)
        elif tid == "T39":
            self.log_msg.emit("  → start_lin_tx + OFF")
            # 1. Remettre crslin en mode OFF en premier (avant start_lin_tx) :
            #    ainsi la première trame 0x16 émise après le rétablissement
            #    transporte WOP_OFF et le BCM ne redémarre pas en SPEED1.
            self._lin_w.queue_send({"cmd": "OFF"})
            # 2. Rétablir le LIN
            self._lin_w.queue_send({"test_cmd": "start_lin_tx"})
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op", 0)
            # 3. Remettre lin_timeout_active=False APRÈS que le LIN ait produit
            #    au moins 1 trame valide (≥ 600ms = 1.5× période LIN 400ms).
            #    Si on le remet immédiatement, _check_lin_timeout (5ms) voit
            #    t_last_lin0x16 encore périmé → re-déclenche B2004 → double timeout.
            if self._rte_client:
                QTimer.singleShot(600, lambda: self._rte_client and (
                    self._rte_client.set_cmd("lin_timeout_active", False) or
                    self._rte_client.set_cmd("crs_wiper_op", 0)
                ))
        elif tid == "T43":
            # Cleanup T43 : invalider génération + garder sim=True jusqu'à state=OFF
            self._rc_gen = getattr(self, "_rc_gen", 0) + 1
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            self._lin_w.queue_send({"cmd": "OFF"})
            if self._rte_client:
                self._rte_client.set_cmd("reverse_gear", False)
                self._rte_client.set_cmd("crs_wiper_op", 0)
                self._rte_client.set_cmd("rest_contact_sim", True)  # maintenir True
                _t43_attempts = [0]
                def _t43_wait_off():
                    if not self._rte_client:
                        return
                    state = self._rte_client.get("state")
                    _t43_attempts[0] += 1
                    if state == "OFF" or _t43_attempts[0] >= 15:
                        self._rte_client.set_cmd("rest_contact_sim_active", False)
                        self._rte_client.set_cmd("rest_contact_sim", False)
                    else:
                        QTimer.singleShot(200, _t43_wait_off)
                QTimer.singleShot(200, _t43_wait_off)
        elif tid == "T45":
            # Cleanup T45 : rétablir ignition + OFF + désactiver sim rest_contact
            if self._rte_client:
                self._rte_client.set_cmd("ignition_status", 1)
                self._rte_client.set_cmd("crs_wiper_op", 0)
                self._rte_client.set_cmd("rest_contact_sim", False)
                self._rte_client.set_cmd("rest_contact_sim_active", False)
            # Toujours sync simulateur
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            self._lin_w.queue_send({"cmd": "OFF"})
            QTimer.singleShot(800, lambda: None)   # laisser BCM reprendre
        elif tid == "TC_LIN_002":
            # Cleanup : reprendre incrémentation AliveCounter normale
            self._lin_w.queue_send({"test_cmd": "restore_alive_counter"})
            if self._rte_client:
                QTimer.singleShot(300, lambda: self._rte_client and
                    self._rte_client.set_cmd("lin_alive_fault", False))

        elif tid == "TC_LIN_017_BIT1_ALONE":
            self.log_msg.emit("  → TC_LIN_017_BIT1_ALONE post : restore fault=0")
            self._lin_w.queue_send({"set_fault": 0})
            self._lin_w.queue_send({"set_stuck": False})
            if self._rte_client:
                self._rte_client.set_cmd("b2011_active", False)
                self._rte_client.set_cmd("crs_wiper_op", 0)
        elif tid == "TC_CAN_003":
            self._motor_w.queue_send({"test_cmd": "restore_can_alive"})
            self._lin_w.queue_send({"cmd": "OFF"})
            if self._rte_client:
                # Désactiver wc_available EN PREMIER, puis déverrouiller LIN.
                self._rte_client.set_cmd("wc_available",   False)
                self._rte_client.set_cmd("crs_wiper_op",   0)
                self._rte_client.set_cmd("lin_op_locked",  False)
                # FIX TC_CAN_003 : remettre alive_tx_frozen=False pour que le BCM
                # reprenne l'incrémentation normale de l'AliveCounter_TX dans 0x200
                self._rte_client.set_cmd("alive_tx_frozen", False)
                QTimer.singleShot(300, lambda: self._rte_client and
                    self._rte_client.set_cmd("wc_alive_fault", False))

        elif tid in ("TC_GEN_001", "TC_SPD_001", "TC_AUTO_004", "TC_FSR_008"):
            # Cleanup commun : moteur OFF + ignition ON simulateur
            # TC_GEN_001 : délai 300ms avant crs_wiper_op=0 pour laisser
            # _check_rte() (poll 200ms) détecter state=SPEED1 avant l'arrêt.
            def _do_cleanup():
                if self._rte_client:
                    self._rte_client.set_cmd("crs_wiper_op", 0)
                    self._rte_client.set_cmd("ignition_status", 1)
                    if tid == "TC_AUTO_004":
                        self._rte_client.set_cmd("rain_sensor_installed", False)
                    if tid == "TC_FSR_008":
                        self._rte_client.set_cmd("watchdog_test_trigger", False)
                    if tid == "TC_SPD_001":
                        # Désactiver simulation rest_contact + invalider génération
                        self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                        self._rte_client.set_cmd("rest_contact_sim", False)
                        self._rte_client.set_cmd("rest_contact_sim_active", False)
                self._motor_w.queue_send(
                    {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
                self._lin_w.queue_send({"cmd": "OFF"})
            delay_ms = 300 if tid == "TC_GEN_001" else 0
            if delay_ms:
                QTimer.singleShot(delay_ms, _do_cleanup)
            else:
                _do_cleanup()

        elif tid == "TC_FSR_010":
            self._lin_w.queue_send({"cmd": "OFF"})
            # Arrêt immédiat de la corruption via sim_client (connexion TCP directe).
            # NE PAS passer par motor_w.queue_send : si motor_w est déconnecté,
            # count=0 resterait dans la queue et arriverait après le count=8 du
            # run suivant → 0 trames corrompues → TIMEOUT.
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.reset_corrupt_crc()
            else:
                # Fallback : motor_w si sim_client indisponible
                self._motor_w.queue_send({"test_cmd": "corrupt_crc_0x201", "count": 0})
            # Incrémenter génération pour invalider les commandes Redis du singleShot
            # si un autre test démarre avant les 3500ms.
            self._rc_gen = getattr(self, "_rc_gen", 0) + 1
            _gen_fsr010 = self._rc_gen
            def _fsr010_cleanup(g=_gen_fsr010):
                if self._rc_gen != g:
                    return   # test suivant déjà démarré, ne pas interférer
                # Reset B2101 côté simulateur (déclenché si les trames corrompues
                # ont causé un timeout CAN WC > 2s avant la fin du test)
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.reset_b2101()
                if self._rte_client:
                    self._rte_client.set_cmd("wc_available",      False)
                    self._rte_client.set_cmd("wc_crc_fault",      False)
                    # B2005 peut s'être déclenché ~2s après les trames CRC KO
                    # (t_last_wiper_status non mis à jour sur CRC KO → timeout naturel).
                    # Reset explicite pour ne pas polluer les tests suivants.
                    self._rte_client.set_cmd("wc_timeout_active", False)
                    self._rte_client.set_cmd("crs_wiper_op",      0)
                    self._rte_client.set_cmd("lin_op_locked",     False)
                    # Sécurité : remettre alive_tx_frozen=False au cas où TC_CAN_003
                    # aurait été interrompu avant son propre cleanup
                    self._rte_client.set_cmd("alive_tx_frozen",   False)
                self._lin_w.queue_send({"cmd": "OFF"})
            QTimer.singleShot(3500, _fsr010_cleanup)  # 3500ms : Redis uniquement

        elif tid == "TC_COM_001":
            pass   # mesure passive BREAK — rien à nettoyer

        # ── TC_LIN_CS cleanup ─────────────────────────────────────────────
        elif tid == "TC_LIN_CS":
            # Ordre impératif :
            # 1. cmd=OFF EN PREMIER : crslin passe wiper_op=OFF en interne
            #    → la prochaine trame après restore sera WOP_OFF (pas SPEED1)
            self._lin_w.queue_send({"cmd": "OFF"})
            # 2. Restaurer checksum normal après 100ms
            QTimer.singleShot(100, lambda: self._lin_w.queue_send(
                {"test_cmd": "restore_lin_checksum"}))
            # 3. lin_timeout_active=False seulement APRÈS que le LIN ait produit
            #    une trame valide (≥ 600ms) — sinon _check_lin_timeout re-déclenche
            #    B2004 immédiatement car t_last_lin0x16 est encore périmé.
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op", 0)
                QTimer.singleShot(600, lambda: self._rte_client and (
                    self._rte_client.set_cmd("lin_timeout_active", False) or
                    self._rte_client.set_cmd("lin_checksum_fault",  False) or
                    self._rte_client.set_cmd("crs_wiper_op",        0)
                ))


        # ── T44 cleanup ───────────────────────────────────────────────────
        elif tid == "T44":
            # cmd="OFF" a déjà été envoyé par le QTimer à t=2000ms dans _pre_test.
            # Le BCM est déjà sorti de REAR_WIPE via _process_rear_wipe.
            # Cleanup Redis après 500ms pour laisser le LIN se stabiliser.
            def _t44_cleanup():
                if self._rte_client:
                    self._rte_client.set_cmd("crs_wiper_op",         0)
                    self._rte_client.set_cmd("reverse_gear",         False)
                    self._rte_client.set_cmd("ignition_status",      1)
                    self._rte_client.set_cmd("rear_wiper_available", True)
                self._motor_w.queue_send(
                    {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            QTimer.singleShot(500, _t44_cleanup)

        # ── T50 cleanup ───────────────────────────────────────────────────
        elif tid == "T50":
            # Remettre en OFF
            self._lin_w.queue_send({"cmd": "OFF"})
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op",  0)
                self._rte_client.set_cmd("ignition_status", 1)
                self._rte_client.set_cmd("lin_op_locked", False)
                self._rte_client.set_cmd("wc_available",  False)
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.reset_b2101()
                QTimer.singleShot(400, lambda: self._rte_client and (
                    self._rte_client.set_cmd("wc_timeout_active",  False) or
                    self._rte_client.set_cmd("wc_b2103_active",    False) or
                    self._rte_client.set_cmd("lin_timeout_active", False)
                ))
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

        # ── T51 cleanup ───────────────────────────────────────────────────
        elif tid == "T51":
            # 1. Débloquer le rest_contact (repassera à False = repos)
            # 2. Désactiver simulation rest_contact
            self._lin_w.queue_send({"cmd": "OFF"})
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op",            0)
                self._rte_client.set_cmd("rest_contact_sim",        False)
                # Petit délai avant de désactiver la sim : laisser BCM voir
                # rest_contact=False (lame revenue au repos) et sortir de ERROR
                def _t51_cleanup():
                    if self._rte_client:
                        self._rte_client.set_cmd("rest_contact_sim_active", False)
                        self._rte_client.set_cmd("ignition_status",         1)
                QTimer.singleShot(300, _t51_cleanup)
            self._motor_w.queue_send(
                {"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

        # ── Cleanup tests version28 ─────────────────────────────────────────

        elif tid == "TC_LIN_016_BIT4":
            self.log_msg.emit("  → TC_LIN_016_BIT4 post : restaurer bit4=1 (Valid=1)")
            # restore_bit4 : remet stick_status bit0=1 → BCM voit stick_valid=True
            # → bcm_protocol.py reset _t_b2004_invalid_start + B2004 INACTIVE
            self._lin_w.queue_send({"test_cmd": "restore_bit4"})
            if self._rte_client:
                self._rte_client.set_cmd("lin_timeout_active", False)
                self._rte_client.set_cmd("crs_wiper_op",       0)

        elif tid in ("TC_LIN_016_BIT6_ALONE", "TC_B2011_AND"):
            self.log_msg.emit(f"  → {tid} post : restore Stuck + CRS fault")
            # Restaurer via _lin_w (crslin.py port 5555) — sim_client envoie sur port 5000
            self._lin_w.queue_send({"set_stuck": False})
            self._lin_w.queue_send({"set_fault": 0})
            if self._rte_client:
                self._rte_client.set_cmd("b2011_active",  False)
                self._rte_client.set_cmd("crs_wiper_op",  0)
                # Si B2011 a mis le BCM en ERROR, le sortir
                QTimer.singleShot(300, lambda: self._rte_client and
                    self._rte_client.set_cmd("bcm_error_reset", True))

        elif tid == "TC_LIN_017_VER":
            self.log_msg.emit("  → TC_LIN_017_VER post : restaurer CRS_Version=0x20")
            # Restaurer via _lin_w (crslin.py port 5555)
            self._lin_w.queue_send({"set_crs_version": 0x20})
            self._lin_w.queue_send({"set_fault": 0})
            if self._rte_client:
                self._rte_client.set_cmd("crs_wiper_op", 0)

        elif tid == "TC_CAN_202_ERR01":
            self.log_msg.emit("  → TC_CAN_202_ERR01 post : reset mismatch + restore état nominal")
            # Désactiver en priorité le mismatch 0x201 pour éviter que
            # le simulateur continue d'émettre des 0x202 NACK spurieux
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.reset_mode_mismatch()
                self._sim_client.reset_b2101()
            if self._rte_client:
                self._rte_client.set_cmd("wc_available",    False)
                self._rte_client.set_cmd("lin_op_locked",   False)
                self._rte_client.set_cmd("crs_wiper_op",    0)

        elif tid == "TC_CAN_202_ERR02":
            self.log_msg.emit("  → TC_CAN_202_ERR02 post : reset B2102 (motor_driver_fault=False)")
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.set_motor_driver_fault(False)   # retire condition B2102
                self._sim_client.reset_b2101()
            if self._rte_client:
                self._rte_client.set_cmd("wc_available",          False)
                self._rte_client.set_cmd("lin_op_locked",         False)
                self._rte_client.set_cmd("crs_wiper_op",          0)
                self._rte_client.set_cmd("wc_fault_motor_driver", False)

        elif tid == "TC_CAN_202_ERR04":
            self.log_msg.emit("  → TC_CAN_202_ERR04 post : reset B2103 (xcp_position_sensor_fault=False)")
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.set_xcp_position_sensor_fault(False)  # retire condition B2103
                self._sim_client.reset_b2101()
            if self._rte_client:
                self._rte_client.set_cmd("wc_available",  False)
                self._rte_client.set_cmd("lin_op_locked", False)
                self._rte_client.set_cmd("crs_wiper_op",  0)
                # Délai 500ms : laisser le BCM finir le traitement B2006 ACTIVE
                # avant de remettre wc_ack_pos_fault=False — évite [DTC INACTIVE]
                # affiché avant [B2006] ACTIVE dans les logs BCM
                def _err04_post_cleanup():
                    if self._rte_client:
                        self._rte_client.set_cmd("wc_ack_pos_fault",          False)
                        self._rte_client.set_cmd("rest_contact_b2006_active", False)
                        self._rte_client.set_cmd("wc_b2006_active",           False)
                QTimer.singleShot(1500, _err04_post_cleanup)

        elif tid == "TC_CAN_202_ERR05":
            self.log_msg.emit("  → TC_CAN_202_ERR05 post : reset B2101 (xcp_internal_fault=False)")
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.set_xcp_internal_fault(False)   # retire condition B2101
                self._sim_client.reset_b2101()
            if self._rte_client:
                self._rte_client.set_cmd("wc_available",         False)
                self._rte_client.set_cmd("lin_op_locked",        False)
                self._rte_client.set_cmd("crs_wiper_op",         0)
                self._rte_client.set_cmd("wc_fault_wc_internal", False)

        elif tid == "TC_B2104":
            self.log_msg.emit("  → TC_B2104 post : reset B2104 + wc_available maintenu pour heal 3 ACK")
            # Armer le flag : _on_motor coupera wc_available dès réception de b2104_inactive
            self._wait_b2104_inactive = True
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.reset_b2104()   # remet compteurs + B2104 INACTIVE
                self._sim_client.reset_b2101()   # évite B2101 résiduel CAS B
            if self._rte_client:
                self._rte_client.set_cmd("wc_nack_consecutive", 0)
                self._rte_client.set_cmd("lin_op_locked",       False)
                self._rte_client.set_cmd("crs_wiper_op",        0)

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
        # TC_B2104 : détecter b2104_inactive → désactiver wc_available
        # Le test PASS en 10ms donc self._current=None au moment du healing.
        # On utilise _wait_b2104_inactive (armé dans le post_test) pour savoir
        # qu'on attend encore le signal de fin de healing.
        if (data.get("type") == "b2104_inactive"
                and getattr(self, "_wait_b2104_inactive", False)
                and self._rte_client):
            self._wait_b2104_inactive = False
            self.log_msg.emit("  → TC_B2104 : B2104 INACTIVE (3 ACK) → wc_available=False")
            self._rte_client.set_cmd("wc_available", False)
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