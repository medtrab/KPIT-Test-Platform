#!/usr/bin/env python3
"""
run_tests_headless.py  —  WipeWash BCM Test Runner (mode headless / Jenkins Windows)
=====================================================================================
Exécute la campagne de tests sans GUI PySide6, en mode ligne de commande.
Conçu pour être appelé par Jenkins sous Windows.

Ce fichier doit être placé dans :
    <depot>/ci/run_tests_headless.py

Les modules de la plateforme (test_cases.py, rte_client.py, etc.) sont dans :
    <depot>/platform/

Usage :
    python ci\\run_tests_headless.py [options]

Options :
    --bcm-host   IP ou hostname du RPiBCM   (défaut : auto-découverte)
    --sim-host   IP ou hostname du RPiSIM   (défaut : auto-découverte)
    --redis-host IP du serveur Redis        (défaut : même que bcm-host)
    --redis-port Port Redis                 (défaut : 6379)
    --tests      IDs séparés par virgule (ex: T30,T31,T32)
                 Si absent → tous les tests ALL_TESTS
    --output     Chemin rapport HTML        (défaut : report_<ts>.html)
    --json       Chemin rapport JSON        (défaut : results_<ts>.json)
    --junit      Chemin rapport JUnit XML   (défaut : junit_<ts>.xml)
    --timeout    Timeout global en secondes (défaut : 600)
    --bench-id   Identifiant banc           (défaut : WipeWash-Bench-CI)
    --operator   Nom opérateur/job Jenkins  (défaut : jenkins)
    --fail-fast  Arrêter dès le premier FAIL

Exit codes :
    0  → tous les tests PASS
    1  → au moins un FAIL ou TIMEOUT
    2  → erreur de connexion / infrastructure
    3  → timeout global dépassé
"""

import argparse
import io
import sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
import datetime
import json
import os
import sys
import time
import threading
import signal

# ─────────────────────────────────────────────────────────────────────────────
# Résolution du chemin vers le dossier platform/
# Structure attendue :
#   <depot>/
#     ci/run_tests_headless.py      ← ce fichier
#     platform/test_cases.py
#     platform/rte_client.py
#     platform/sim_client.py
#     platform/network.py
#     platform/report_generator.py
# ─────────────────────────────────────────────────────────────────────────────
_HERE         = os.path.dirname(os.path.abspath(__file__))
_DEPOT_ROOT   = os.path.dirname(_HERE)
_PLATFORM_DIR = os.path.join(_DEPOT_ROOT, "platform")

if not os.path.isdir(_PLATFORM_DIR):
    # Fallback : chercher "platform - Copie" (nom original du zip)
    _PLATFORM_DIR = os.path.join(_DEPOT_ROOT, "platform - Copie")

if not os.path.isdir(_PLATFORM_DIR):
    print(
        f"[ERREUR] Dossier platform introuvable.\n"
        f"  Cherché dans : {_PLATFORM_DIR}\n"
        f"  Vérifiez la structure du dépôt (voir README_JENKINS.md).",
        file=sys.stderr,
    )
    sys.exit(2)

# Si test_cases.py n'est pas directement dans platform/, descendre dans les sous-dossiers
# (cas où les sources sont dans platform/project_final/controldesk - .../test_cases.py)
if not os.path.isfile(os.path.join(_PLATFORM_DIR, "test_cases.py")):
    _found = None
    for _root, _dirs, _files in os.walk(_PLATFORM_DIR):
        if "test_cases.py" in _files:
            _found = _root
            break
    if _found:
        _PLATFORM_DIR = _found
    else:
        print(
            f"[ERREUR] test_cases.py introuvable sous {_PLATFORM_DIR}.\n"
            f"  Vérifiez la structure du dépôt (voir README_JENKINS.md).",
            file=sys.stderr,
        )
        sys.exit(2)

sys.path.insert(0, _PLATFORM_DIR)

# ─── Imports plateforme ───────────────────────────────────────────────────────
try:
    from test_cases       import ALL_TESTS, BaseTest, BaseBCMTest, TestResult
    from rte_client       import RTEClient
    from sim_client       import SimClient
    from network          import auto_discover_all
    from report_generator import ReportGenerator
except ImportError as e:
    print(f"[ERREUR] Import plateforme échoué : {e}", file=sys.stderr)
    print(f"  PLATFORM_DIR = {_PLATFORM_DIR}", file=sys.stderr)
    sys.exit(2)


# ═════════════════════════════════════════════════════════════════════════════
#  STUBS TCP HEADLESS  —  remplacent les Workers PySide6 par des threads purs
# ═════════════════════════════════════════════════════════════════════════════

class _FakeSignal:
    """Émule un Signal Qt (connect / emit) sans dépendance à PySide6."""
    def __init__(self):
        self._cbs = []

    def connect(self, cb, *args, **kwargs):
        self._cbs.append(cb)

    def emit(self, *args):
        for cb in self._cbs:
            try:
                cb(*args)
            except Exception as exc:
                print(f"[FakeSignal] callback error: {exc}", file=sys.stderr)


class _TCPReader(threading.Thread):
    """
    Thread générique : connexion TCP, lecture ligne à ligne (JSON\\n),
    dispatch vers un _FakeSignal.
    Reconnexion automatique si la connexion est perdue.
    """
    def __init__(self, host: str, port: int, signal: _FakeSignal, name: str):
        super().__init__(name=name, daemon=True)
        self._host   = host
        self._port   = port
        self._signal = signal
        self._stop   = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        import socket
        buf = b""
        while not self._stop.is_set():
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(3)
                s.connect((self._host, self._port))
                buf = b""
                while not self._stop.is_set():
                    try:
                        chunk = s.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        try:
                            ev = json.loads(line.decode("utf-8", errors="replace"))
                            self._signal.emit(ev)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass
                s.close()
            except Exception:
                pass
            # Attente avant reconnexion (évite la boucle rapide si hôte absent)
            self._stop.wait(timeout=1.5)


class HeadlessCANWorker:
    """Remplace CANWorker (PySide6) — écoute les frames CAN depuis RPiSIM:5557."""
    PORT = 5557

    def __init__(self, host: str):
        # host = RPiSIM (bcm_tcp_can.py sert le port 5557 sur le simulateur)
        self.can_received   = _FakeSignal()
        self.status_changed = _FakeSignal()
        self._reader = _TCPReader(host, self.PORT, self.can_received, "CAN-RX")

    def start(self):
        self._reader.start()

    def queue_send(self, obj: dict):
        pass  # TX CAN non utilisé par les tests

    def stop(self):
        self._reader.stop()


class HeadlessLINWorker:
    """Remplace LINWorker (PySide6) — bidirectionnel RPiSIM:5555."""
    PORT = 5555

    def __init__(self, host: str):
        self.lin_received   = _FakeSignal()
        self.status_changed = _FakeSignal()
        self._host     = host
        self._reader   = _TCPReader(host, self.PORT, self.lin_received, "LIN-RX")
        self._tx_queue: list = []
        self._tx_lock  = threading.Lock()
        self._stop_ev  = threading.Event()

    def start(self):
        self._reader.start()
        threading.Thread(target=self._tx_loop, name="LIN-TX", daemon=True).start()

    def _tx_loop(self):
        import socket
        while not self._stop_ev.is_set():
            with self._tx_lock:
                cmds = list(self._tx_queue)
                self._tx_queue.clear()
            for cmd in cmds:
                # FIX JENKINS : timeout porté à 5s (vs 2s) + 1 retry pour absorber
                # la latence TCP RPiSIM sous Jenkins. Perte silencieuse corrigée.
                sent = False
                for _attempt in range(2):
                    try:
                        with socket.create_connection(
                                (self._host, self.PORT), timeout=5.0) as s:
                            s.sendall((json.dumps(cmd) + "\n").encode())
                        sent = True
                        break
                    except Exception:
                        self._stop_ev.wait(timeout=0.1)
            self._stop_ev.wait(timeout=0.05)

    def queue_send(self, obj: dict):
        with self._tx_lock:
            self._tx_queue.append(obj)

    def set_wiper_op(self, op: int):
        self.queue_send({"cmd": ["OFF","TOUCH","SPEED1","SPEED2","AUTO",
                                 "FRONT_WASH","REAR_WASH","REAR_WIPE"][op]
                                if 0 <= op <= 7 else "OFF"})

    def stop(self):
        self._stop_ev.set()
        self._reader.stop()


class HeadlessMotorWorker:
    """
    Remplace MotorVehicleWorker (PySide6).
    RX depuis RPiBCM:5000 (état moteur), TX vers RPiSIM:5000 (commandes véhicule).

    NOTE : la plateforme Qt utilise PORT_BCMCAN=5002 (constants.py), mais bcmcan
    est démarré via main.py avec canport=5000 par défaut sur le RPiSIM.
    En CI, bcmcan n'est pas démarré séparément sur 5002 → on cible directement
    le port 5000 qui est le serveur TCP actif de bcmcan sur le RPiSIM.
    """
    PORT_RX = 5000   # RPiBCM bcm_tcp_broadcast
    PORT_TX = 5000   # RPiSIM bcmcan via main.py (canport=5000 par défaut)

    def __init__(self, bcm_host: str, sim_host: str):
        self.motor_received = _FakeSignal()
        self.status_changed = _FakeSignal()
        self._bcm_host = bcm_host
        self._sim_host = sim_host
        self._tx_queue: list = []
        self._tx_lock  = threading.Lock()
        self._stop_ev  = threading.Event()
        self._reader   = _TCPReader(bcm_host, self.PORT_RX, self.motor_received, "Motor-RX")

    def start(self):
        self._reader.start()
        # Thread TX (commandes → RPiSIM)
        threading.Thread(target=self._tx_loop, name="Motor-TX", daemon=True).start()

    def _tx_loop(self):
        import socket
        while not self._stop_ev.is_set():
            with self._tx_lock:
                cmds = list(self._tx_queue)
                self._tx_queue.clear()
            for cmd in cmds:
                # FIX JENKINS : timeout porté à 5s + 1 retry (cohérent avec LINWorker)
                for _attempt in range(2):
                    try:
                        with socket.create_connection(
                                (self._sim_host, self.PORT_TX), timeout=5.0) as s:
                            s.sendall((json.dumps(cmd) + "\n").encode())
                        break
                    except Exception:
                        self._stop_ev.wait(timeout=0.1)
            self._stop_ev.wait(timeout=0.05)

    def queue_send(self, obj: dict):
        with self._tx_lock:
            self._tx_queue.append(obj)

    def set_wiper_op(self, op: int):
        self.queue_send({"wiper_op": op})

    def stop(self):
        self._stop_ev.set()
        self._reader.stop()


class HeadlessPumpSignal:
    """Remplace PumpDataClient (PySide6) — écoute données pompe RPiBCM:5556."""
    PORT = 5556

    def __init__(self, host: str):
        self.data_received = _FakeSignal()
        self._reader = _TCPReader(host, self.PORT, self.data_received, "Pump-RX")

    def start(self):
        self._reader.start()

    def stop(self):
        self._reader.stop()


# ═════════════════════════════════════════════════════════════════════════════
#  RUNNER HEADLESS
#  Reproduit fidèlement la logique de TestRunner (test_runner.py)
#  sans aucune dépendance Qt.
# ═════════════════════════════════════════════════════════════════════════════

class HeadlessTestRunner:
    """
    Exécute les tests séquentiellement dans un thread pur.
    Signaux Qt → threading.Event + callbacks directs.
    """

    TICK_INTERVAL_S   = 0.2    # 200 ms, identique au QTimer de TestRunner
    SETTLE_AFTER_INIT = 2.0    # secondes d'attente après connexion workers

    def __init__(self,
                 can_worker, lin_worker, motor_worker,
                 pump_signal  = None,
                 rte_client   = None,
                 sim_client   = None,
                 fail_fast    = False):

        self._can_w      = can_worker
        self._lin_w      = lin_worker
        self._motor_w    = motor_worker
        self._rte_client = rte_client
        self._sim_client = sim_client
        self._fail_fast  = fail_fast

        self._results:   list = []
        self._log_lines: list = []

        self._current: BaseTest | None = None
        self._lock     = threading.Lock()
        self._done_ev  = threading.Event()

        # Câblage des callbacks
        can_worker.can_received.connect(self._on_can)
        lin_worker.lin_received.connect(self._on_lin)
        motor_worker.motor_received.connect(self._on_motor)
        if pump_signal is not None:
            pump_signal.data_received.connect(self._on_motor)

    # ── Callbacks workers ─────────────────────────────────────────────────
    def _on_can(self, ev):
        with self._lock:
            if not self._current:
                return
            res = self._current.on_can_frame(ev)
        if res:
            self._finish(res)

    def _on_lin(self, ev):
        with self._lock:
            if not self._current:
                return
            res = self._current.on_lin_frame(ev)
        if res:
            self._finish(res)

    def _on_motor(self, data):
        with self._lock:
            if not self._current:
                return
            res = self._current.on_motor_data(data)
        if res:
            self._finish(res)

    # ── Gestion résultat ─────────────────────────────────────────────────
    def _finish(self, result: TestResult):
        """Appelé dès qu'un test produit un résultat (depuis n'importe quel thread)."""
        with self._lock:
            if self._current is None:
                return        # déjà traité (double callback rare)
            self._current = None
        self._results.append(result)
        icon = {"PASS": "[PASS]", "FAIL": "[FAIL]", "TIMEOUT": "[TIMEOUT]"}.get(result.status, "?")
        msg  = f"  {icon} [{result.test_id}] {result.name:<50} → {result.status}"
        if result.details:
            msg += f"  ({result.details})"
        self._log(msg)
        self._done_ev.set()

    # ── Log horodaté ─────────────────────────────────────────────────────
    def _log(self, msg: str):
        ts   = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] {msg}"
        print(line.encode("utf-8", errors="replace").decode("utf-8"), flush=True)
        self._log_lines.append(line)

    # ── Délai inter-test (reproduit test_runner.py exactement) ───────────
    @staticmethod
    def _inter_delay_ms(tid: str, last_tid: str) -> int:
        NEEDS_DELAY = {
            "T30","T31","T32","T33","T34","T35","T36","T37","T38","T38b","T38c","T39",
            "T43","T45","TC_LIN_002","TC_LIN_017_BIT1_ALONE",
            "TC_CAN_003","TC_GEN_001","TC_SPD_001","TC_AUTO_004",
            "TC_FSR_008","TC_FSR_010","TC_COM_001","TC_B2103",
            "LIN_INVALID_CMD_001","T_RAIN_AUTO_SENSOR_ERROR","T_B2009_CAN","T50b",
            "T50c","T50d","T_CAS_B_SPEED1_REVERSE","T_B2009_CASA",
            "TC_LIN_016_BIT4","TC_LIN_016_BIT6_ALONE","TC_B2011_AND",
            "TC_LIN_017_VER","TC_CAN_202_ERR01","TC_CAN_202_ERR02",
            "TC_CAN_202_ERR04","TC_CAN_202_ERR05","TC_B2104",
        }
        if tid not in NEEDS_DELAY:
            return 0
        if last_tid == "T22":
            return 8000
        if last_tid == "TC_FSR_010":
            return 3000
        if last_tid in ("T40", "T21", "T36", "T37", "T43", "T45"):
            return 2500
        return 300

    # ── Stimuli avant test (port headless de test_runner._pre_test) ──────
    def _pre_test(self, test: BaseTest):
        """Port headless de test_runner._pre_test() — QTimer.singleShot → time.sleep + appel direct."""
        tid = test.ID
        rc  = self._rte_client
        lw  = self._lin_w
        mw  = self._motor_w

        if tid == "T10":
            self._log("  → stop_lin_tx")
            lw.queue_send({"test_cmd": "stop_lin_tx"})

        elif tid in ("T03", "T04", "T05"):
            # Port fidèle de test_runner._pre_test (T03/T04/T05) :
            # 0x200 (T03) / 0x201 (T04) / 0x202 (T05) ne sont émis par le BCM
            # qu'en CAS B (wc_available=True).  Sans ce pré-requis les trames
            # n'arrivent jamais et le test TIMEOUT immédiatement.
            self._log(f"  → {tid} : wc_available=True (CAS B)")
            if rc:
                rc.set_cmd("wc_available",    True)
                rc.set_cmd("ignition_status", 1)
            mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

        elif tid == "T11":
            self._log("  → stop_can_tx")
            if rc: rc.set_cmd("wc_available", True)
            time.sleep(0.3)
            mw.queue_send({"test_cmd": "stop_can_tx"})

        elif tid == "T40":
            self._log("  → T40 : TOUCH — 1 cycle puis retour OFF (no repeat)")
            # ROOT CAUSE B2009 sur banc réel (REST_CONTACT_HARDWARE_PRESENT=True) :
            # Le BCM lit le GPIO physique (lame immobile=False) tant que
            # rest_contact_sim_active=False. Si sim_active n'est positionné qu'APRÈS
            # l'entrée en TOUCH, le timer B2009 (_rest_contact_stuck_start) s'écoule
            # sur le GPIO physique → à 3s → ERROR, même si sim=True arrive ensuite.
            #
            # FIX : envoyer rest_contact_sim_active=True + sim=False AVANT le LIN TOUCH.
            # Le BCM bascule immédiatement sur la simulation Redis dès l'entrée en TOUCH.
            # Le thread différé n'envoie plus que sim=True (début mouvement) puis False.
            if rc:
                rc.set_cmd("rest_contact_sim_active", True)   # avant TOUCH !
                rc.set_cmd("rest_contact_sim",        False)  # repos initial
                rc.set_cmd("crs_wiper_op", 0)
            lw.queue_send({"cmd": "TOUCH"})
            if hasattr(test, "reset_t0"): test.reset_t0()
            _rc_ref = rc
            def _t40_delayed():
                # Attendre que la supervision démarre et que le BCM entre en TOUCH
                time.sleep(0.4)
                if _rc_ref:
                    _rc_ref.set_cmd("rest_contact_sim", True)   # lame EN MOUVEMENT
                    _rc_ref.set_cmd("crs_wiper_op", 1)
                time.sleep(1.5)   # durée cycle (≤ TOUCH_DURATION = 1700ms)
                if _rc_ref: _rc_ref.set_cmd("rest_contact_sim", False)  # retour repos → cycle compté
            threading.Thread(target=_t40_delayed, daemon=True).start()

        elif tid == "T43":
            self._log("  → T43 : SPEED1 + reverse_gear=True (rear intermittent)")
            # ROOT CAUSES :
            # RC1 — CAN 0x300 écrase reverse_gear : bcmcan.py émet 0x300 avec reverse=0
            #   toutes les 200ms. Contrairement à ignition_status, reverse_gear n'a pas
            #   de fenêtre Redis priority dans _can_process_0x300 → il est écrasé à 0
            #   à chaque trame → [SRD_WW_060] Moteur arriere OFF après le 1er cycle.
            #   _check_rte T43 : "if not rev: return None" → ignore tous les cycles suivants.
            #   FIX : boucle de rafraîchissement reverse_gear=True toutes les 150ms.
            #
            # RC2 — Pas de délai avant la boucle rest_contact : la boucle _cycle_t43_loop
            #   envoyait rest_contact=False immédiatement, avant que le BCM soit en SPEED1.
            #   FIX : délai 300ms avant de démarrer la boucle (laisser SPEED1 s'établir).
            #
            # RC3 — lw.queue_send(SPEED1) inutile et risqué : utiliser uniquement Redis.
            mw.queue_send({"ignition_status": "ON", "reverse_gear": 1, "vehicle_speed": 0})
            if rc:
                rc.set_cmd("lin_op_locked", True)  # bloque LIN 0x16 → évite OFF résiduel
                rc.set_cmd("rest_contact_sim_active", True)
                rc.set_cmd("rest_contact_sim", True)
                time.sleep(0.15)                   # propagation Redis → BCM
                rc.set_cmd("crs_wiper_op", 2)      # SPEED1 via Redis uniquement
                rc.set_cmd("reverse_gear", True)
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen = self._rc_gen

                def _t43_reverse_refresh():
                    """Rafraîchit reverse_gear=True toutes les 800ms.
                    bcm_protocol.py _can_process_0x300 a une fenêtre Redis priority
                    de 1s pour reverse_gear (_t_reverse_redis) — symétrique à
                    ignition_status. Ce refresh maintient la fenêtre active."""
                    while getattr(self, "_rc_gen", 0) == _gen:
                        rc.set_cmd("reverse_gear", True)
                        time.sleep(0.80)
                threading.Thread(target=_t43_reverse_refresh, daemon=True).start()

                def _cycle_t43_loop():
                    time.sleep(0.30)               # attendre que BCM soit en SPEED1
                    for _ in range(8):
                        if getattr(self, "_rc_gen", 0) != _gen: return
                        rc.set_cmd("rest_contact_sim", False)
                        time.sleep(0.10)
                        if getattr(self, "_rc_gen", 0) != _gen: return
                        rc.set_cmd("rest_contact_sim", True)
                        time.sleep(2.4)
                threading.Thread(target=_cycle_t43_loop, daemon=True).start()

            time.sleep(0.5)
            if hasattr(test, "reset_t0"): test.reset_t0()

        elif tid == "T45":
            self._log("  → T45 : SPEED1 puis ignition=0 (blade return to rest)")
            # ROOT CAUSES identifiées (builds 87/91) :
            #
            # RC1 — LIN 0x16 résiduel : lw.queue_send(SPEED1) arrivait pendant ST_PARK
            #   et réécrivait crs_wiper_op=SPEED1. Après PARK→OFF, si ignition redevenait ≠0,
            #   le BCM re-démarrait en SPEED1 → B2009 STUCK CLOSED → ERROR.
            #   FIX : ne plus envoyer de commande LIN ; utiliser UNIQUEMENT crs_wiper_op via Redis.
            #   lin_op_locked=True protège crs_wiper_op contre les frames LIN pendant le test.
            #
            # RC2 — CAN 0x300 écrase ignition_status après 1s : bcmcan.py émet 0x300 avec
            #   ignition=2 (START) toutes les 200ms. La fenêtre Redis priority = 1s.
            #   Le PARK dure ~1500ms → après 1s la fenêtre expire → CAN remet ignition=2
            #   → BCM voit ignition≠0 après PARK→OFF → crs_wiper_op=SPEED1 non bloqué → re-loop.
            #   FIX : boucle de rafraîchissement ignition=0 toutes les 800ms (< 1s) pendant
            #   toute la durée du test, pour maintenir la fenêtre Redis active.
            mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            if rc:
                rc.set_cmd("lin_op_locked", True)       # bloque LIN 0x16 → pas de résidu SPEED1
                rc.set_cmd("rest_contact_sim_active", True)
                rc.set_cmd("rest_contact_sim", True)    # lame EN MOUVEMENT avant SPEED1
                time.sleep(0.15)                         # propagation Redis → BCM
                rc.set_cmd("crs_wiper_op", 2)            # SPEED1 uniquement via Redis
                time.sleep(0.30)                         # ≥ 2 ticks T-WSM(50ms) en SPEED1
                if hasattr(test, "reset_t0"): test.reset_t0()
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen = self._rc_gen

                def _t45_ign_refresh():
                    """Rafraîchit ignition=0 toutes les 800ms pour contrer CAN 0x300
                    (fenêtre Redis priority = 1s, bcmcan émet ignition=2 toutes les 200ms)."""
                    while getattr(self, "_rc_gen", 0) == _gen:
                        rc.set_cmd("ignition_status", 0)
                        time.sleep(0.80)
                threading.Thread(target=_t45_ign_refresh, daemon=True).start()

                rc.set_cmd("ignition_status", 0)         # déclencheur initial FSR_004
                time.sleep(1.5)
                rc.set_cmd("rest_contact_sim", False)     # front ↓ → rest_contact_raw=False
            else:
                rc.set_cmd("crs_wiper_op", 2)
                time.sleep(0.4)
                if hasattr(test, "reset_t0"): test.reset_t0()
                rc.set_cmd("ignition_status", 0)
                mw.queue_send({"ignition_status": "OFF", "reverse_gear": 0, "vehicle_speed": 0})

        elif tid == "TC_LIN_002":
            self._log("  → TC_LIN_002 : geler AliveCounter LIN")
            if hasattr(test, "reset_t0"): test.reset_t0()
            lw.queue_send({"test_cmd": "freeze_alive_counter"})

        elif tid == "TC_LIN_004":
            self._log("  → TC_LIN_004 : envoyer stickStatus invalide (0xFF)")
            if hasattr(test, "reset_t0"): test.reset_t0()
            lw.queue_send({"test_cmd": "send_invalid_stick_status"})

        elif tid == "TC_LIN_005":
            self._log("  → TC_LIN_005 : simuler CRS_InternalFault=1 sur LIN 0x17")
            if hasattr(test, "reset_t0"): test.reset_t0()
            lw.queue_send({"test_cmd": "crs_internal_fault"})

        elif tid == "TC_CAN_003":
            self._log("  → TC_CAN_003 : geler AliveCounter CAN 0x200")
            self._rc_gen = getattr(self, "_rc_gen", 0) + 1
            _gen = self._rc_gen
            lw.queue_send({"cmd": "SPEED1"})
            if rc:
                rc.set_cmd("wc_available", False)
                rc.set_cmd("alive_tx_frozen", False)
                time.sleep(0.5)
                if getattr(self, "_rc_gen", 0) != _gen: return
                rc.set_cmd("lin_op_locked",  True)
                rc.set_cmd("wc_available",   True)
                rc.set_cmd("wc_alive_fault", False)
                rc.set_cmd("crs_wiper_op",   2)
                time.sleep(0.6)
                if getattr(self, "_rc_gen", 0) != _gen: return
                if hasattr(test, "reset_t0"): test.reset_t0()
                rc.set_cmd("alive_tx_frozen", True)
                mw.queue_send({"test_cmd": "freeze_can_alive"})
                if hasattr(test, "_stimulus_sent"): test._stimulus_sent = True

        elif tid == "TC_GEN_001":
            self._log("  → TC_GEN_001 : ignition=0 puis ON + SPEED1")
            if rc:
                rc.set_cmd("wc_available", False)
                rc.set_cmd("ignition_status", 0)
                rc.set_cmd("crs_wiper_op", 0)
            mw.queue_send({"ignition_status": "OFF", "reverse_gear": 0, "vehicle_speed": 0})
            time.sleep(1.0)
            if hasattr(test, "reset_t0"): test.reset_t0()
            mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            lw.queue_send({"cmd": "SPEED1"})
            if rc: rc.set_cmd("ignition_status", 1)

        elif tid == "TC_SPD_001":
            self._log("  → TC_SPD_001 : LIN SPEED1 continu 5s")
            mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            lw.queue_send({"cmd": "SPEED1"})
            if rc:
                rc.set_cmd("rest_contact_sim_active", True)
                rc.set_cmd("rest_contact_sim", True)
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen = self._rc_gen
                def _spd_cycle():
                    for _delay in range(1700, 8500, 1700):
                        time.sleep(1.6)
                        if getattr(self, "_rc_gen", 0) != _gen: return
                        rc.set_cmd("rest_contact_sim", False)
                        time.sleep(0.1)
                        if getattr(self, "_rc_gen", 0) == _gen:
                            rc.set_cmd("rest_contact_sim", True)
                threading.Thread(target=_spd_cycle, daemon=True).start()
            time.sleep(0.3)
            if hasattr(test, "reset_t0"): test.reset_t0()

        elif tid == "TC_AUTO_004":
            self._log("  → TC_AUTO_004 : AUTO avec rain_sensor_installed=False")
            if hasattr(test, "reset_t0"): test.reset_t0()
            if rc:
                rc.set_cmd("rain_sensor_installed", False)
                rc.set_cmd("crs_wiper_op", 4)
            lw.queue_send({"cmd": "AUTO"})

        elif tid == "TC_FSR_008":
            self._log("  → TC_FSR_008 : LIN SPEED1 puis watchdog trigger")
            mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            lw.queue_send({"cmd": "SPEED1"})
            if rc:
                time.sleep(0.4)
                if hasattr(test, "reset_t0"): test.reset_t0()
                rc.set_cmd("watchdog_test_trigger", True)
            else:
                if hasattr(test, "reset_t0"): test.reset_t0()

        elif tid == "TC_FSR_010":
            self._log("  → TC_FSR_010 : CRC corrompu sur 0x201")
            if rc:
                rc.set_cmd("wc_timeout_active", False)
                rc.set_cmd("wc_crc_fault",      False)
                rc.set_cmd("wc_available",      False)
                rc.set_cmd("lin_op_locked", True)
            lw.queue_send({"cmd": "SPEED1"})
            time.sleep(0.4)
            if rc: rc.set_cmd("wc_available", False); rc.set_cmd("wc_crc_fault", False)
            time.sleep(0.4)
            if rc:
                rc.set_cmd("wc_available",  True)
                rc.set_cmd("crs_wiper_op",  2)
            time.sleep(0.6)
            if hasattr(test, "reset_t0"): test.reset_t0()
            mw.queue_send({"test_cmd": "corrupt_crc_0x201", "count": 20})
            if hasattr(test, "_stimulus_sent"): test._stimulus_sent = True

        elif tid == "TC_COM_001":
            self._log("  → TC_COM_001 : mesure baudrate BREAK LIN")
            if hasattr(test, "reset_t0"): test.reset_t0()

        elif tid == "TC_LIN_CS":
            self._log("  → TC_LIN_CS : corruption checksum AVANT SPEED1")
            if rc:
                rc.set_cmd("lin_checksum_fault", False)
                rc.set_cmd("crs_wiper_op",       0)
                rc.set_cmd("lin_timeout_active", False)
            time.sleep(0.2)
            lw.queue_send({"test_cmd": "corrupt_lin_checksum"})
            if hasattr(test, "_stimulus_sent"): test._stimulus_sent = True
            if hasattr(test, "reset_t0"): test.reset_t0()
            time.sleep(0.1)
            lw.queue_send({"cmd": "SPEED1"})

        elif tid == "T44":
            self._log("  → T44 : REAR_WIPE op=7 (once) → OFF à 2000ms")
            if rc:
                rc.set_cmd("rear_wiper_available", True)
                rc.set_cmd("wc_available",         False)
                rc.set_cmd("reverse_gear",         False)
                rc.set_cmd("ignition_status",      1)
                rc.set_cmd("crs_wiper_op",         0)
            mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            time.sleep(0.3)
            if hasattr(test, "reset_t0"): test.reset_t0()
            lw.queue_send({"cmd": "REAR_WIPE"})
            threading.Thread(
                target=lambda: (time.sleep(2.0), lw.queue_send({"cmd": "OFF"})),
                daemon=True).start()

        elif tid == "T50":
            self._log("  → T50 : Cas B wc_available=True + LIN SPEED1 → CAN 0x200, pas RL2=LOW")
            # IMPORTANT : PAS de rest_contact_sim pour T50.
            # test_runner.py le précise explicitement : rest_contact_sim_active=True
            # avec rest_contact_sim=False (lame au repos) AVANT SPEED1 bloquerait
            # _check_rest_contact_stuck() et empêcherait state=SPEED1 d'être atteint.
            # Préconditions : wc_available=True (Cas B), lin_op_locked=True, ignition=ON.
            if rc:
                rc.set_cmd("wc_available",   True)
                rc.set_cmd("lin_op_locked",  True)
                rc.set_cmd("crs_wiper_op",   0)
                rc.set_cmd("ignition_status", 1)
            mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            time.sleep(0.4)
            if hasattr(test, "reset_t0"): test.reset_t0()
            lw.queue_send({"cmd": "SPEED1"})
            if rc: rc.set_cmd("crs_wiper_op", 2)

        elif tid == "T50b":
            self._log("  → T50b : CAS B SPEED1 + inject_motor_current → B2001")
            # Préconditions : wc_available=True (Cas B), lin_op_locked=True, ignition=ON.
            # B2001 inactivé avant injection pour affichage complet.
            if rc:
                rc.set_cmd("wc_available",   True)
                rc.set_cmd("lin_op_locked",  True)
                rc.set_cmd("crs_wiper_op",   0)
                rc.set_cmd("ignition_status", 1)
                # B2001 INACTIVE avant injection pour affichage complet
                time.sleep(0.2)
                rc.set_cmd("dtc_inactivate", "B2001")
            mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            # 400ms : wc_available propagé + BCM prêt
            time.sleep(0.4)
            # LIN SPEED1 → BCM ST_SPEED1 → CAN 0x200 → WC répond 0x201 speed=1
            lw.queue_send({"cmd": "SPEED1"})
            if rc: rc.set_cmd("crs_wiper_op", 2)
            # 300ms supplémentaires : SPEED1 stabilisé avant injection overcurrent
            time.sleep(0.3)
            if hasattr(test, "reset_t0"): test.reset_t0()
            # Injecter MotorCurrent=0.95A dans trame 0x201 (> OVERCURRENT_THRESH=0.8A)
            # BCM : _can_process_0x201 → motor_current_a=0.95 → _check_overcurrent → B2001
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.inject_motor_current(0.95)

        elif tid == "T51":
            self._log("  → T51 : rest_contact bloqué EN MOUVEMENT → FSR_006")
            if rc:
                rc.set_cmd("wc_available",            False)
                rc.set_cmd("ignition_status",         1)
                rc.set_cmd("crs_wiper_op",            0)
                rc.set_cmd("rest_contact_sim_active", True)
                rc.set_cmd("rest_contact_sim",        True)
            mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            time.sleep(0.3)
            if hasattr(test, "reset_t0"): test.reset_t0()
            lw.queue_send({"cmd": "SPEED1"})
            if rc: rc.set_cmd("crs_wiper_op", 2)

        elif tid == "T_CAS_B_SPEED1_REVERSE":
            self._log("  → T_CAS_B_SPEED1_REVERSE : CAS B + SPEED1 LIN→CAN + Reverse → front+rear")
            if rc:
                rc.set_cmd("wc_available",         True)
                rc.set_cmd("lin_op_locked",        False)  # LIN doit pouvoir écrire crs_wiper_op
                rc.set_cmd("rear_wiper_available", True)
                rc.set_cmd("crs_wiper_op",         0)
                rc.set_cmd("ignition_status",      1)
                rc.set_cmd("reverse_gear",         False)
            mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            # 400ms : préconditions stabilisées (équivalent QTimer.singleShot(400, _casb_speed1_start))
            time.sleep(0.4)
            if hasattr(test, "reset_t0"): test.reset_t0()
            # blade_cycling AVANT SPEED1 : évite B2009 en CAS B (blade=0 permanent → STUCK)
            # Envoyé ici (t=400ms) simultanément avec SPEED1, comme dans test_runner
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.start_blade_cycling(period_ms=1500)
            # SPEED1 via LIN uniquement → _lin_poll_frame écrit crs_wiper_op=2
            # PAS de set_cmd("crs_wiper_op", 2) : le stimulus doit venir du bus LIN
            lw.queue_send({"cmd": "SPEED1"})
            # Reverse via CAN 0x300 après 500ms (SPEED1 stabilisé)
            # PAS de set_cmd("reverse_gear", True) : la valeur doit venir du bus CAN
            time.sleep(0.5)
            mw.queue_send({"ignition_status": "ON", "reverse_gear": 1, "vehicle_speed": 0})

        elif tid == "T_B2009_CASA":
            self._log("  → T_B2009_CASA : CAS A SPEED1 sans rest_contact → B2009")
            if rc:
                rc.set_cmd("wc_available",            False)
                rc.set_cmd("crs_wiper_op",             0)
                rc.set_cmd("ignition_status",          1)
                # rest_contact_sim_active=True + rest_contact_sim=False :
                # lame figée AU REPOS → NE555 hardware ignoré → aucun cycle
                rc.set_cmd("rest_contact_sim_active", True)
                rc.set_cmd("rest_contact_sim",        False)
                # B2009 INACTIVE avant test pour affichage complet
                time.sleep(0.2)
                rc.set_cmd("dtc_inactivate", "B2009")
            mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            # 400ms : laisser dtc_inactivate se propager avant le stimulus
            time.sleep(0.4)
            if hasattr(test, "reset_t0"): test.reset_t0()
            lw.queue_send({"cmd": "SPEED1"})
            if rc: rc.set_cmd("crs_wiper_op", 2)

        elif tid == "TC_B2103":
            self._log("  → TC_B2103 : reset guard B2103, puis injection blade_sim=50%")
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.reset_b2103()
            if rc: rc.set_cmd("wc_b2103_active", False)
            time.sleep(0.2)
            if self._sim_client and self._sim_client.is_connected():
                ok = self._sim_client.send_blade_sim(50.0)
                self._log(f"  → TC_B2103 : blade_sim=50% {'injecté' if ok else 'ECHEC INJECTION'}")
            if hasattr(test, "reset_t0"): test.reset_t0()
            if hasattr(test, "_t_inject_ms"): test._t_inject_ms = time.time() * 1000.0

        elif tid == "T22":
            self._log("  → T22 : pompe FORWARD >5s → overtime FSR_005 (B2008)")
            if hasattr(test, "reset_t0"): test.reset_t0()
            if rc:
                rc.set_cmd("rest_contact_sim_active", True)
                rc.set_cmd("rest_contact_sim", True)
                rc.set_cmd("crs_wiper_op", 5)
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen = self._rc_gen
                def _t22_cycles():
                    for _ in range(6):
                        time.sleep(1.6)
                        if getattr(self, "_rc_gen", 0) != _gen: return
                        rc.set_cmd("rest_contact_sim", False)
                        time.sleep(0.1)
                        if getattr(self, "_rc_gen", 0) == _gen:
                            rc.set_cmd("rest_contact_sim", True)
                threading.Thread(target=_t22_cycles, daemon=True).start()
            lw.queue_send({"cmd": "FRONT_WASH"})

        elif tid == "T21":
            self._log("  → T21 : FRONT_WASH — 3 cycles lame avant 5s")
            if rc:
                rc.set_cmd("rest_contact_sim_active", True)
                rc.set_cmd("rest_contact_sim", True)
                time.sleep(0.2)
                rc.set_cmd("crs_wiper_op", 5)
                lw.queue_send({"cmd": "FRONT_WASH"})
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen = self._rc_gen
                def _t21_cycles():
                    for _ in range(11):
                        time.sleep(0.8)
                        if getattr(self, "_rc_gen", 0) != _gen: return
                        rc.set_cmd("rest_contact_sim", False)
                        time.sleep(0.1)
                        if getattr(self, "_rc_gen", 0) == _gen:
                            rc.set_cmd("rest_contact_sim", True)
                threading.Thread(target=_t21_cycles, daemon=True).start()
            else:
                lw.queue_send({"cmd": "FRONT_WASH"})

        elif tid == "T30":
            self._log("  → T30 : LIN SPEED1")
            if hasattr(test, "reset_t0"): test.reset_t0()
            lw.queue_send({"cmd": "SPEED1"})

        elif tid == "T31":
            self._log("  → T31 : LIN SPEED2")
            if hasattr(test, "reset_t0"): test.reset_t0()
            lw.queue_send({"cmd": "SPEED2"})

        elif tid == "T32":
            self._log("  → T32 : LIN SPEED1 puis OFF")
            lw.queue_send({"cmd": "SPEED1"})
            time.sleep(0.3)
            if hasattr(test, "reset_t0"): test.reset_t0()
            lw.queue_send({"cmd": "OFF"})

        elif tid == "T33":
            if hasattr(test, "reset_t0"): test.reset_t0()
            if rc:
                mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
                rc.set_cmd("crs_wiper_op", 0)
                time.sleep(0.2)
                rc.set_cmd("crs_wiper_op", 2)
                time.sleep(0.4)
                if hasattr(test, "reset_t0"): test.reset_t0()
                rc.set_cmd("ignition_status", 0)
                mw.queue_send({"ignition_status": "OFF", "reverse_gear": 0, "vehicle_speed": 0})
            else:
                lw.queue_send({"cmd": "SPEED1"})
                time.sleep(0.4)
                mw.queue_send({"ignition_status": 0, "reverse_gear": 0, "vehicle_speed": 0})

        elif tid == "T34":
            self._log("  → T34 : Redis SET AUTO + rain=10")
            if rc:
                # FIX JENKINS T34 (v2) : TIMEOUT causé par _had_different=False.
                # _check_rte T34 exige de voir state≠AUTO avant AUTO+Speed1.
                # Si le stimulus est envoyé dans _pre_test (bloquant), le BCM entre
                # en AUTO AVANT que la supervision démarre → premier tick voit déjà
                # AUTO+Speed1 → _initial_checked=True, _had_different=False → jamais
                # confirmé → TIMEOUT à 8s.
                #
                # FIX : pré-configurer rain + sim_active MAINTENANT (avant retour
                # _pre_test), mais envoyer lw(AUTO) + crs_wiper_op=4 dans un thread
                # différé de 0.4s. La supervision démarre, voit state=OFF
                # (_had_different=True), puis le thread envoie le stimulus →
                # BCM passe en AUTO → PASS.
                rc.set_cmd("rain_sensor_installed", True)
                rc.set_cmd("rain_intensity", 10)
                rc.set_cmd("rest_contact_sim_active", True)
                rc.set_cmd("rest_contact_sim", False)
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen = self._rc_gen
                if hasattr(test, "reset_t0"): test.reset_t0()

                def _t34_rain_refresh():
                    while getattr(self, "_rc_gen", 0) == _gen:
                        rc.set_cmd("rain_intensity", 10)
                        time.sleep(0.15)
                threading.Thread(target=_t34_rain_refresh, daemon=True).start()

                def _t34_stimulus_and_cycles():
                    time.sleep(0.4)   # supervision démarre + voit state=OFF
                    if getattr(self, "_rc_gen", 0) != _gen: return
                    lw.queue_send({"cmd": "AUTO"})
                    rc.set_cmd("crs_wiper_op", 4)
                    mw.queue_send({"rain_intensity": 10, "sensor_status": "OK"})
                    # Attendre state=AUTO avant les cycles
                    for _ in range(20):
                        if rc.get("state") == "AUTO":
                            break
                        time.sleep(0.1)
                    if getattr(self, "_rc_gen", 0) != _gen: return
                    prev_cycles = rc.get_int("front_blade_cycles", 0)
                    for cycle in range(3):
                        time.sleep(0.3 if cycle == 0 else 0.2)
                        if getattr(self, "_rc_gen", 0) != _gen: return
                        rc.set_cmd("rest_contact_sim", True)
                        time.sleep(1.4)
                        if getattr(self, "_rc_gen", 0) != _gen: return
                        rc.set_cmd("rest_contact_sim", False)
                        for _ in range(10):
                            c = rc.get_int("front_blade_cycles", 0)
                            if c > prev_cycles:
                                prev_cycles = c
                                break
                            time.sleep(0.1)
                threading.Thread(target=_t34_stimulus_and_cycles, daemon=True).start()
            else:
                lw.queue_send({"cmd": "AUTO"})
                mw.queue_send({"rain_intensity": 10, "sensor_status": "OK"})

        elif tid == "T35":
            self._log("  → T35 : Redis SET AUTO + rain=25")
            if rc:
                # FIX JENKINS T35 (v2) : même fix que T34 — stimulus AUTO différé.
                rc.set_cmd("rain_sensor_installed", True)
                rc.set_cmd("rain_intensity", 25)
                rc.set_cmd("rest_contact_sim_active", True)
                rc.set_cmd("rest_contact_sim", False)
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen = self._rc_gen
                if hasattr(test, "reset_t0"): test.reset_t0()

                def _t35_rain_refresh():
                    while getattr(self, "_rc_gen", 0) == _gen:
                        rc.set_cmd("rain_intensity", 25)
                        time.sleep(0.15)
                threading.Thread(target=_t35_rain_refresh, daemon=True).start()

                def _t35_stimulus_and_cycles():
                    time.sleep(0.4)
                    if getattr(self, "_rc_gen", 0) != _gen: return
                    lw.queue_send({"cmd": "AUTO"})
                    rc.set_cmd("crs_wiper_op", 4)
                    mw.queue_send({"rain_intensity": 25, "sensor_status": "OK"})
                    for _ in range(20):
                        if rc.get("state") == "AUTO":
                            break
                        time.sleep(0.1)
                    if getattr(self, "_rc_gen", 0) != _gen: return
                    prev_cycles = rc.get_int("front_blade_cycles", 0)
                    for cycle in range(3):
                        time.sleep(0.3 if cycle == 0 else 0.2)
                        if getattr(self, "_rc_gen", 0) != _gen: return
                        rc.set_cmd("rest_contact_sim", True)
                        time.sleep(1.4)
                        if getattr(self, "_rc_gen", 0) != _gen: return
                        rc.set_cmd("rest_contact_sim", False)
                        for _ in range(10):
                            c = rc.get_int("front_blade_cycles", 0)
                            if c > prev_cycles:
                                prev_cycles = c
                                break
                            time.sleep(0.1)
                threading.Thread(target=_t35_stimulus_and_cycles, daemon=True).start()
            else:
                lw.queue_send({"cmd": "AUTO"})
                mw.queue_send({"rain_intensity": 25, "sensor_status": "OK"})

        elif tid == "T36":
            self._log("  → T36 : FRONT_WASH + cycles rest_contact")
            # FIX JENKINS T36 : port fidèle de test_runner avec QTimer.singleShot(400).
            # La plateforme envoie FRONT_WASH + rest_contact_cycles 400ms APRÈS le reset,
            # pendant que la supervision Qt tourne déjà. Dans le headless, _pre_test
            # bloque → supervision ne démarre pas → les cycles arrivent avant que
            # _check_rte soit actif → fallback durée=49ms (moteur déjà OFF).
            # FIX : envoyer tout dans un thread différé pour que la supervision soit
            # active avant le stimulus. Attente de state=WASH_FRONT avant les cycles.
            if rc: rc.set_cmd("crs_wiper_op", 0)
            mw.queue_send({"ignition_status": "ON", "vehicle_speed": 0})
            if hasattr(test, "reset_t0"): test.reset_t0()
            _rc_ref = rc
            _lw_ref = lw
            self._rc_gen = getattr(self, "_rc_gen", 0) + 1
            _gen = self._rc_gen

            def _t36_deferred():
                time.sleep(0.4)   # laisser supervision démarrer
                if getattr(self, "_rc_gen", 0) != _gen: return
                if _rc_ref:
                    _rc_ref.set_cmd("rest_contact_sim_active", True)
                    _rc_ref.set_cmd("rest_contact_sim", False)
                _lw_ref.queue_send({"cmd": "FRONT_WASH"})
                # Attendre confirmation state=WASH_FRONT avant les cycles
                for _ in range(20):   # max 2s
                    if _rc_ref and _rc_ref.get("state") == "WASH_FRONT":
                        break
                    time.sleep(0.1)
                if getattr(self, "_rc_gen", 0) != _gen: return
                # FIX FSR-005 T36 : watchdog pompe coupe à 5s.
                # Période cycle réduite : True=1.1s + False=0.15s = 1.25s/cycle.
                # 3 cycles × 1.25s + init 0.15s + confirm 3×0.2s = ~4.4s < 5s.
                # B2009 : période totale True+False = 1.1+0.15+0.15=1.4s < 3s → OK.
                prev_cycles = _rc_ref.get_int("front_blade_cycles", 0) if _rc_ref else 0
                for cycle in range(3):
                    time.sleep(0.15)
                    if getattr(self, "_rc_gen", 0) != _gen: return
                    if _rc_ref: _rc_ref.set_cmd("rest_contact_sim", True)
                    time.sleep(1.1)   # cycle court mais suffisant pour True→False
                    if getattr(self, "_rc_gen", 0) != _gen: return
                    if _rc_ref: _rc_ref.set_cmd("rest_contact_sim", False)
                    # Attendre confirmation cycle compté (max 0.5s)
                    for _ in range(5):
                        if not _rc_ref: break
                        c = _rc_ref.get_int("front_blade_cycles", 0)
                        if c > prev_cycles:
                            prev_cycles = c
                            break
                        time.sleep(0.1)
            threading.Thread(target=_t36_deferred, daemon=True).start()

        elif tid == "T37":
            self._log("  → T37 : LIN REAR_WASH")
            # FIX JENKINS (v4) : le problème fondamental est _wait_idle dans _check_rte.
            # _wait_idle=True → attend rear_motor_on=False (état initial propre).
            # Si le LIN est envoyé avant que _pre_test retourne (et que la boucle de
            # supervision démarre), le BCM peut avoir déjà fini ses 2 cycles quand
            # _check_rte commence à poller → _wait_idle voit False, puis attend True
            # qui ne vient jamais → TIMEOUT.
            #
            # Solution : envoyer REAR_WASH dans un thread différé de 0.8s APRÈS que
            # _pre_test retourne. La supervision démarre, _check_rte voit rear_motor_on=False
            # (_wait_idle=False), puis le thread envoie REAR_WASH → BCM démarre → _check_rte
            # voit True (chrono démarre) puis False (résultat calculé) → PASS.
            mw.queue_send({"ignition_status": "ON", "vehicle_speed": 0})
            time.sleep(0.2)
            if hasattr(test, "reset_t0"): test.reset_t0()
            _lin_w_ref = lw
            def _delayed_rear_wash():
                time.sleep(0.8)   # attendre que la supervision soit démarrée et _wait_idle=False
                _lin_w_ref.queue_send({"cmd": "REAR_WASH"})
            threading.Thread(target=_delayed_rear_wash, daemon=True).start()

        elif tid == "T38":
            self._log("  → T38 : LIN SPEED1 + injection surcourant")
            lw.queue_send({"cmd": "SPEED1"})
            if rc:
                time.sleep(0.2)
                rc.set_cmd("dtc_inactivate", "B2001")
                time.sleep(0.2)
                if hasattr(test, "reset_t0"): test.reset_t0()
                rc.set_cmd("motor_current_a", 0.95)
            else:
                time.sleep(0.4)

        elif tid == "T39":
            self._log("  → T39 : LIN SPEED1 puis stop_lin_tx")
            lw.queue_send({"cmd": "SPEED1"})
            time.sleep(0.5)
            if hasattr(test, "reset_t0"): test.reset_t0()
            lw.queue_send({"test_cmd": "stop_lin_tx"})

        elif tid == "T38b":
            self._log("  → T38b : LIN REAR_WIPE + surcourant moteur arrière (B2002)")
            if rc: rc.set_cmd("rear_wiper_available", True)
            lw.queue_send({"cmd": "REAR_WIPE"})
            if rc:
                time.sleep(0.3)
                rc.set_cmd("dtc_inactivate", "B2002")
                time.sleep(0.2)
                if hasattr(test, "reset_t0"): test.reset_t0()
                rc.set_cmd("motor_current_a", 0.95)

        elif tid == "T38c":
            self._log("  → T38c : LIN FRONT_WASH + surcourant pompe (B2003)")
            if rc:
                rc.set_cmd("rest_contact_sim_active", True)
                rc.set_cmd("rest_contact_sim", False)
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen = self._rc_gen
                def _t38c_cycles():
                    for cycle in range(4):
                        time.sleep(0.2 + cycle * 1.7 - (0.2 if cycle > 0 else 0))
                        if getattr(self, "_rc_gen", 0) != _gen: return
                        rc.set_cmd("rest_contact_sim", True)
                        time.sleep(1.55)
                        if getattr(self, "_rc_gen", 0) != _gen: return
                        rc.set_cmd("rest_contact_sim", False)
                threading.Thread(target=_t38c_cycles, daemon=True).start()
            lw.queue_send({"cmd": "FRONT_WASH"})
            if rc:
                time.sleep(0.4)
                rc.set_cmd("dtc_inactivate", "B2003")
                time.sleep(0.2)
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                if hasattr(test, "reset_t0"): test.reset_t0()
                rc.set_cmd("pump_current_a", 1.0)

        elif tid == "LIN_INVALID_CMD_001":
            self._log("  → LIN_INVALID_CMD_001 : op=10 brut")
            if rc: rc.set_cmd("crs_wiper_op", 0)
            time.sleep(0.2)
            if hasattr(test, "reset_t0"): test.reset_t0()
            lw.queue_send({"test_cmd": "set_raw_wiper_op", "op": 10})

        elif tid == "T_RAIN_AUTO_SENSOR_ERROR":
            self._log("  → T_RAIN_AUTO_SENSOR_ERROR : AUTO + SensorStatus=ERROR")
            if rc:
                rc.set_cmd("rain_sensor_installed", True)
                rc.set_cmd("rain_sensor_ok",        True)
                rc.set_cmd("rain_intensity",        25)
                mw.queue_send({"rain_intensity": 25, "sensor_status": "OK"})
                rc.set_cmd("rest_contact_sim_active", True)
                rc.set_cmd("rest_contact_sim", False)
                self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                _gen = self._rc_gen
                def _rain_cycles():
                    for cycle in range(4):
                        time.sleep(0.3 + cycle * 1.7 - (0.3 if cycle > 0 else 0))
                        if getattr(self, "_rc_gen", 0) != _gen: return
                        rc.set_cmd("rest_contact_sim", True)
                        time.sleep(1.55)
                        if getattr(self, "_rc_gen", 0) != _gen: return
                        rc.set_cmd("rest_contact_sim", False)
                threading.Thread(target=_rain_cycles, daemon=True).start()
            time.sleep(0.2)
            lw.queue_send({"cmd": "AUTO"})
            time.sleep(0.8)
            if rc: rc.set_cmd("dtc_inactivate", "B2007")
            time.sleep(0.2)
            self._rc_gen = getattr(self, "_rc_gen", 0) + 1
            if hasattr(test, "reset_t0"): test.reset_t0()
            if hasattr(test, "notify_injection"): test.notify_injection()
            if rc:
                rc.set_cmd("rain_intensity",    0xFF)
                rc.set_cmd("rain_sensor_ok",    False)
            mw.queue_send({"rain_intensity": 255, "sensor_status": "ERROR"})

        elif tid == "T_B2009_CAN":
            self._log("  → T_B2009_CAN : CAS B + blade figée + rest_contact fixe → B2009")
            if rc:
                rc.set_cmd("wc_available",   True)
                rc.set_cmd("lin_op_locked",  True)
                rc.set_cmd("crs_wiper_op",   0)
                rc.set_cmd("ignition_status", 1)
                time.sleep(0.2)
                rc.set_cmd("dtc_inactivate", "B2009")
                rc.set_cmd("rest_contact_sim_active", True)
                rc.set_cmd("rest_contact_sim",        False)
            mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            time.sleep(0.4)
            if hasattr(test, "reset_t0"): test.reset_t0()
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.freeze_blade_position(50.0)
            lw.queue_send({"cmd": "SPEED1"})
            if rc: rc.set_cmd("crs_wiper_op", 2)

        elif tid == "TC_LIN_017_BIT1_ALONE":
            self._log("  → TC_LIN_017_BIT1_ALONE : 0x17 bit0=1 seul — B2011 ne doit PAS se déclencher")
            if rc:
                rc.set_cmd("wc_available",    False)
                rc.set_cmd("crs_wiper_op",    0)
                rc.set_cmd("ignition_status", 1)
                rc.set_cmd("b2011_active",    False)
            # Purger résidus : s'assurer que bit6=0 ET bit0=0 avant injection
            lw.queue_send({"set_stuck": False})
            lw.queue_send({"set_fault": 0})
            # 800ms : laisser la queue se vider + BCM stable avant injection
            time.sleep(0.8)
            # Injecter 0x17 bit0=1 (CRS_InternalFault_Stick) UNIQUEMENT
            # 0x16 bit6 reste à 0 → condition ET incomplète → B2011 impossible
            lw.queue_send({"set_fault": 1})
            self._log("  → 0x17 bit0=1 (fault_stick) injecté — bit6=0 maintenu")
            if hasattr(test, "reset_t0"):
                test.reset_t0()

        elif tid == "T50c":
            self._log("  → T50c : CAS B SPEED1 + inject_motor_current + 0x202 ErrorCode=0x01 → B2001 NON attendu")
            if rc:
                rc.set_cmd("wc_available",    True)
                rc.set_cmd("lin_op_locked",   True)
                rc.set_cmd("crs_wiper_op",    0)
                rc.set_cmd("ignition_status", 1)
                time.sleep(0.2)
                rc.set_cmd("dtc_inactivate",  "B2001")
            mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            # 400ms : BCM prêt
            time.sleep(0.4)
            lw.queue_send({"cmd": "SPEED1"})
            if rc: rc.set_cmd("crs_wiper_op", 2)
            # 300ms : SPEED1 stabilisé → injection courant SOUS le seuil
            time.sleep(0.3)
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.inject_motor_current(0.30)
                self._log("  → T50c inject_motor_current 0.300A (< seuil 0.8A)")
            # 400ms : courant dans _sensor_state
            time.sleep(0.4)
            if hasattr(test, "reset_t0"): test.reset_t0()
            # ErrorCode=0x01 (≠ 0x03) → condition conjonctive B2001 incomplète
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.send_wiper_ack(ack_status=1, error_code=0x01, alive=0)
                self._log("  → T50c send_wiper_ack NACK ErrorCode=0x01 (≠ 0x03, courant < seuil)")

        elif tid == "T50d":
            self._log("  → T50d : CAS B SPEED1 + courant normal 0.30A + 0x202 ErrorCode=0x03 → B2001 NON attendu")
            if rc:
                rc.set_cmd("wc_available",    True)
                rc.set_cmd("lin_op_locked",   True)
                rc.set_cmd("crs_wiper_op",    0)
                rc.set_cmd("ignition_status", 1)
                time.sleep(0.2)
                rc.set_cmd("dtc_inactivate",  "B2001")
            mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
            time.sleep(0.4)
            lw.queue_send({"cmd": "SPEED1"})
            if rc: rc.set_cmd("crs_wiper_op", 2)
            time.sleep(0.3)
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.inject_motor_current(0.30)
                self._log("  → T50d inject_motor_current 0.300A (< seuil)")
            time.sleep(0.4)
            if hasattr(test, "reset_t0"): test.reset_t0()
            # AckStatus=1, ErrorCode=0x03 : courant < seuil → condition TRIPLE non satisfaite
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.send_wiper_ack(ack_status=1, error_code=0x03, alive=0)
                self._log("  → T50d send_wiper_ack NACK ErrorCode=0x03 (courant < seuil)")

        elif tid == "TC_LIN_016_BIT4":
            self._log("  → TC_LIN_016_BIT4 : StickStatus bit4=0 (Valid=0) → B2004 après 2.5s")
            if rc:
                rc.set_cmd("wc_available",       False)
                rc.set_cmd("lin_timeout_active", False)
                rc.set_cmd("crs_wiper_op",       0)
                rc.set_cmd("ignition_status",    1)
            # 600ms : BCM stable avant injection
            time.sleep(0.6)
            if hasattr(test, "reset_t0"): test.reset_t0()
            lw.queue_send({"test_cmd": "send_bit4_zero"})
            self._log("  → StickStatus bit4=0 injecté — attente B2004 (2.5s)")

        elif tid == "TC_LIN_016_BIT6_ALONE":
            self._log("  → TC_LIN_016_BIT6_ALONE : Stuck bit6=1 sans 0x17 bit0 → B2011 NON déclenché")
            if rc:
                rc.set_cmd("wc_available",    False)
                rc.set_cmd("crs_wiper_op",    0)
                rc.set_cmd("ignition_status", 1)
                rc.set_cmd("b2011_active",    False)
            lw.queue_send({"set_fault": 0})
            lw.queue_send({"set_stuck": False})
            # 800ms : vider la queue _lin_w des résidus TC_B2011_AND
            time.sleep(0.8)
            # Injecter bit6=1 (Stuck) UNIQUEMENT — 0x17 bit0 reste à 0
            lw.queue_send({"set_stuck": True})
            self._log("  → Stuck bit6=1 activé (0x17 bit0=0 → B2011 impossible)")
            if hasattr(test, "reset_t0"): test.reset_t0()

        elif tid == "TC_B2011_AND":
            self._log("  → TC_B2011_AND : 0x16 bit6=1 ET 0x17 bit0=1 → B2011 après 10s")
            if rc:
                rc.set_cmd("wc_available",    False)
                rc.set_cmd("crs_wiper_op",    0)
                rc.set_cmd("ignition_status", 1)
            lw.queue_send({"set_stuck": False})
            lw.queue_send({"set_fault": 0})
            # 500ms : laisser la queue se vider avant injection
            time.sleep(0.5)
            # Étape 1 : 0x17 bit0=1 (CRS_InternalFault_Stick)
            lw.queue_send({"set_fault": 1})
            self._log("  → CRS_InternalFault_Stick (0x17 bit0=1) injecté")
            time.sleep(0.2)
            # Étape 2 : 0x16 bit6=1 (Stuck) — les 2 conditions actives → timer 10s
            lw.queue_send({"set_stuck": True})
            self._log("  → Stuck bit6=1 injecté — timer 10s démarré")
            if hasattr(test, "reset_t0"): test.reset_t0()

        elif tid == "TC_LIN_017_VER":
            self._log("  → TC_LIN_017_VER : CRS_Version=0xFF → trame rejetée")
            if rc:
                rc.set_cmd("wc_available",       False)
                rc.set_cmd("crs_wiper_op",       0)
                rc.set_cmd("ignition_status",    1)
                rc.set_cmd("lin_timeout_active", False)
            # 800ms : BCM stable, au moins 2 cycles LIN 0x16 valides
            time.sleep(0.8)
            if hasattr(test, "reset_t0"): test.reset_t0()
            lw.queue_send({"set_crs_version": 0xFF})
            lw.queue_send({"set_fault": 1})  # bit0=1 : serait B2011 si trame acceptée
            self._log("  → CRS_Version=0xFF + fault_stick=1 injectés")

        elif tid == "TC_CAN_202_ERR01":
            self._log("  → TC_CAN_202_ERR01 : mode_mismatch → 0x200 SPEED1 → 0x202 NACK+0x01 naturel")
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.set_motor_driver_fault(False)
                self._sim_client.set_xcp_position_sensor_fault(False)
                self._sim_client.set_xcp_internal_fault(False)
                self._sim_client.reset_mode_mismatch()
                self._sim_client.set_mode_mismatch()
                self._log("  → mode_mismatch_0x201 activé + faults purgés")
            # 800ms : ≥ 2 cycles 0x201 pour recharger _last_201 avec CurrentMode nominal
            time.sleep(0.8)
            if rc:
                rc.set_cmd("wc_available",    True)
                rc.set_cmd("lin_op_locked",   True)
                rc.set_cmd("crs_wiper_op",    2)
                rc.set_cmd("ignition_status", 1)
            if hasattr(test, "reset_t0"): test.reset_t0()
            self._log("  → chrono démarré — attente 0x202 naturelle (NACK=1, ErrorCode=0x01)")

        elif tid == "TC_CAN_202_ERR02":
            self._log("  → TC_CAN_202_ERR02 : B2102 → FaultStatus_MotorDriver=1 → 0x202 Err=0x02")
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.reset_mode_mismatch()
                self._sim_client.set_motor_driver_fault(True)
                self._log("  → B2102 armé : motor_driver_fault=True")
            # 800ms : laisser _last_201 se recharger avec CurrentMode nominal
            time.sleep(0.8)
            if rc:
                rc.set_cmd("wc_available",    True)
                rc.set_cmd("lin_op_locked",   True)
                rc.set_cmd("crs_wiper_op",    2)
                rc.set_cmd("ignition_status", 1)
            if hasattr(test, "reset_t0"): test.reset_t0()
            self._log("  → chrono démarré — attente 0x201 bit1=1 + 0x202 NACK+0x02 + BCM→OFF")

        elif tid == "TC_CAN_202_ERR04":
            self._log("  → TC_CAN_202_ERR04 : B2103 → FaultStatus_PosSensor=1 → 0x202 Err=0x04 → B2006")
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.reset_mode_mismatch()
                self._sim_client.set_motor_driver_fault(False)
                self._sim_client.set_xcp_internal_fault(False)
                self._sim_client.set_xcp_position_sensor_fault(True)
                self._log("  → B2103 armé : xcp_position_sensor_fault=True")
            # 1200ms : laisser le healing motor_driver_fault (1s) s'achever
            time.sleep(1.2)
            if rc:
                rc.set_cmd("wc_b2006_active",           False)
                rc.set_cmd("wc_b2103_active",           False)
                rc.set_cmd("wc_ack_pos_fault",          False)
                rc.set_cmd("rest_contact_b2006_active", 0)
                rc.set_cmd("wc_available",    True)
                rc.set_cmd("lin_op_locked",   True)
                rc.set_cmd("crs_wiper_op",    2)
                rc.set_cmd("ignition_status", 1)
            if hasattr(test, "reset_t0"): test.reset_t0()
            self._log("  → chrono démarré — attente 0x201 bit2=1 + 0x202 NACK+0x04 + B2006")

        elif tid == "TC_CAN_202_ERR05":
            self._log("  → TC_CAN_202_ERR05 : B2101 → FaultStatus_WC_Internal=1 → 0x202 Err=0x05 → BCM OFF")
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.reset_mode_mismatch()
                self._sim_client.set_motor_driver_fault(False)
                self._sim_client.set_xcp_position_sensor_fault(False)
                self._sim_client.set_xcp_internal_fault(True)
                self._log("  → B2101 armé : xcp_internal_fault=True")
            # 1200ms : laisser le healing motor_driver_fault (1s) s'achever
            time.sleep(1.2)
            if rc:
                rc.set_cmd("wc_available",    True)
                rc.set_cmd("lin_op_locked",   True)
                rc.set_cmd("crs_wiper_op",    2)
                rc.set_cmd("ignition_status", 1)
            if hasattr(test, "reset_t0"): test.reset_t0()
            self._log("  → chrono démarré — attente 0x201 bit0=1 + 0x202 NACK+0x05 + BCM→OFF")

        elif tid == "TC_B2104":
            self._log("  → TC_B2104 : 3 NACKs consécutifs (AckStatus=1, ErrorCode=0x01) → B2104")
            if rc:
                rc.set_cmd("wc_available",    True)
                rc.set_cmd("crs_wiper_op",    2)
                rc.set_cmd("ignition_status", 1)
                rc.set_cmd("lin_op_locked",   True)
            if self._sim_client and self._sim_client.is_connected():
                self._sim_client.reset_b2104()
            # 600ms : BCM stable, WC en attente de commandes
            time.sleep(0.6)
            if hasattr(test, "reset_t0"): test.reset_t0()
            # Envoyer 3 NACKs consécutifs avec 50ms d'intervalle
            for i in range(3):
                time.sleep(0.05)
                alive = i & 0xFF
                ok = self._sim_client.send_wiper_ack(
                    ack_status=1, error_code=0x01, alive=alive) \
                    if (self._sim_client and self._sim_client.is_connected()) else False
                self._log(f"  → NACK 0x01 #{i+1}/3 {'envoyé' if ok else 'échec'}")
                if i == 2 and hasattr(test, "notify_nack3_sent"):
                    test.notify_nack3_sent()

    # ── Reset état BCM avant chaque test ─────────────────────────────────
    def _reset_bcm_state(self):
        """Remet le BCM dans un état stable (ignition ON, pas de timeout actif)."""
        rc = self._rte_client
        mw = self._motor_w
        if rc:
            rc.set_cmd("wc_timeout_active",       False)
            rc.set_cmd("lin_timeout_active",       False)
            rc.set_cmd("crs_wiper_op",             0)
            rc.set_cmd("ignition_status",          1)
            rc.set_cmd("wc_available",             False)
            rc.set_cmd("rain_intensity",           0)
            rc.set_cmd("rain_sensor_installed",    False)
            rc.set_cmd("rest_contact_sim_active",  False)
            rc.set_cmd("rest_contact_sim",         False)
            rc.set_cmd("lin_op_locked",            False)
            # FIX T43 : stopper _handle_reverse_intermittent qui tourne en autonome
            # dans le BCM tant que reverse_gear=True. Sans ce reset, le moteur
            # arrière continue d'osciller après la fin du test.
            rc.set_cmd("reverse_gear",             False)
        if mw:
            mw.queue_send({"ignition_status": "ON",
                           "reverse_gear": 0,
                           "vehicle_speed": 0})

    # ── Boucle principale ─────────────────────────────────────────────────
    def run(self, test_classes: list, global_timeout_s: float = 600.0) -> list:
        """
        Exécute les tests séquentiellement.
        Retourne la liste complète des TestResult.
        """
        t_global = time.monotonic()
        total    = len(test_classes)

        self._log(f"[START] Démarrage campagne  —  {total} test(s) à exécuter")
        if self._rte_client:
            self._log(f"   Redis  : {self._rte_client.host}:{self._rte_client.port}")
        self._log(f"   Timeout global : {global_timeout_s}s")
        self._log("-" * 70)

        last_tid = ""

        for idx, cls in enumerate(test_classes):

            # ── Timeout global ────────────────────────────────────────────
            if time.monotonic() - t_global > global_timeout_s:
                self._log(f"[WARN]  Timeout global ({global_timeout_s}s) — arrêt après {idx} tests")
                return self._results

            test = cls()
            self._log(f"\n>> [{idx+1}/{total}]  [{test.ID}]  {test.NAME}")

            # ── Injecter rte_client pour BaseBCMTest ──────────────────────
            if isinstance(test, BaseBCMTest):
                BaseBCMTest.rte_client = self._rte_client

            # ── Délai inter-test ──────────────────────────────────────────
            delay_ms = self._inter_delay_ms(test.ID, last_tid)
            if delay_ms > 0:
                self._log(f"   [WAIT] Pause inter-test {delay_ms} ms…")
                time.sleep(delay_ms / 1000.0)

            # ── Reset état BCM ────────────────────────────────────────────
            self._reset_bcm_state()
            time.sleep(0.3)   # laisser Redis + BCM traiter les commandes

            # ── Démarrer le test ──────────────────────────────────────────
            # FIX JENKINS : overrides CI pour les tests sensibles à la latence Redis.
            # T37 : TEST_TIMEOUT_S 18s (vs 12s nominal).
            #        MIN_ACTIVE_MS réduit à 3200ms (vs 3400ms) : la mesure Redis est
            #        inférieure à la durée physique (~100ms de délai publication T-REDIS).
            #        3400ms physique → ~3300ms Redis mesuré → 3200ms accepté en CI.
            _CI_TIMEOUT_OVERRIDES  = {"T37": 18}
            _CI_MIN_ACTIVE_OVERRIDES = {"T37": 3200}
            if test.ID in _CI_TIMEOUT_OVERRIDES:
                test.TEST_TIMEOUT_S = _CI_TIMEOUT_OVERRIDES[test.ID]
            if test.ID in _CI_MIN_ACTIVE_OVERRIDES and hasattr(test, "MIN_ACTIVE_MS"):
                test.MIN_ACTIVE_MS = _CI_MIN_ACTIVE_OVERRIDES[test.ID]
            self._done_ev.clear()
            with self._lock:
                self._current = test
            test.start()
            self._pre_test(test)            # stimulus envoyé (avec ses sleep internes)

            # ── Boucle supervision (tick 200ms) ───────────────────────────
            while True:
                finished = self._done_ev.wait(timeout=self.TICK_INTERVAL_S)
                if finished:
                    break

                # Vérifications timeout et _check_rte
                with self._lock:
                    cur = self._current
                if cur is None:
                    break   # _finish() déjà appelé

                res = cur.check_timeout()
                if res is None and isinstance(cur, BaseBCMTest):
                    res = cur._check_rte()

                if res is not None:
                    with self._lock:
                        self._current = None
                    self._results.append(res)
                    icon = {"PASS": "[PASS]", "FAIL": "[FAIL]", "TIMEOUT": "[TIMEOUT]"}.get(res.status, "?")
                    msg  = (f"  {icon} [{res.test_id}] {res.name:<50}"
                            f" → {res.status}")
                    if res.details:
                        msg += f"  ({res.details})"
                    self._log(msg)
                    break

            # ── Invalider _rc_gen → arrêter tous les threads daemon du test ──
            self._rc_gen = getattr(self, "_rc_gen", 0) + 1

            # ── Nettoyage actif post-test ─────────────────────────────────
            # Remet les actionneurs dans un état neutre IMMÉDIATEMENT après
            # le résultat, sans attendre le _reset_bcm_state du test suivant.
            # Nécessaire pour T43 : bcmcan continue d'émettre CAN 0x300 avec
            # reverse=1 après le test → BCM reste en mode intermittent arrière.
            # mw.queue_send(reverse=0) met à jour _vehicle_state dans bcmcan
            # → les trames 0x300 suivantes auront reverse=0 → BCM arrête le
            # moteur arrière via _handle_reverse_intermittent (Cas 1).
            mw = self._motor_w
            rc = self._rte_client
            if mw:
                mw.queue_send({"ignition_status": "ON",
                               "reverse_gear": 0,
                               "vehicle_speed": 0})
            if rc:
                rc.set_cmd("crs_wiper_op", 0)
                rc.set_cmd("reverse_gear", False)

            # ── Nettoyage ciblé par test ────────────────────────────
            # FIX T38/T38b/T38c/TC_GEN_001 : après un surcourant, le BCM entre
            # en ST_ERROR puis l'auto-healing (1s sans courant) le ramène en
            # SPEED1 si le LIN diffuse encore SPEED1 / REAR_WIPE / FRONT_WASH.
            # → il faut : 1) couper le LIN (cmd=OFF) AVANT l'auto-healing,
            #              2) remettre motor_current_a / pump_current_a à 0
            #              3) laisser 1.2s pour que l'auto-healing finisse proprement.
            _tid = test.ID
            if _tid in ("T38", "T38b", "T38c"):
                self._log(f"  → {_tid} post : LIN OFF + courant=0 (évite boucle SPEED1 après auto-heal ERROR)")
                lw = self._lin_w
                if lw:
                    lw.queue_send({"cmd": "OFF"})
                if rc:
                    rc.set_cmd("motor_current_a", 0.0)
                    if _tid == "T38c":
                        rc.set_cmd("pump_current_a", 0.0)
                        rc.set_cmd("rest_contact_sim", False)
                        rc.set_cmd("rest_contact_sim_active", False)
                    if _tid == "T38b":
                        rc.set_cmd("rear_motor_error", False)
                    rc.set_cmd("wc_timeout_active", False)
                    rc.set_cmd("crs_wiper_op", 0)
                time.sleep(1.2)   # laisser auto-heal terminer → BCM repasse en OFF

            elif _tid == "TC_GEN_001":
                self._log("  → TC_GEN_001 post : LIN OFF + 300ms (évite cycles résiduels)")
                lw = self._lin_w
                if lw:
                    lw.queue_send({"cmd": "OFF"})
                if rc:
                    rc.set_cmd("crs_wiper_op", 0)
                    rc.set_cmd("ignition_status", 1)
                time.sleep(0.3)

            elif _tid == "TC_SPD_001":
                # FIX TC_SPD_001 : le thread _spd_cycle tourne encore quand le test
                # se termine. _rc_gen a déjà été incrémenté (ligne au-dessus) pour
                # stopper le thread, mais rest_contact_sim_active=True reste dans Redis.
                # Le LIN continue à diffuser SPEED1 → BCM repart en SPEED1 après OFF.
                # Correction : désactiver la sim rest_contact + couper le LIN.
                self._log("  → TC_SPD_001 post : rest_contact_sim OFF + LIN OFF")
                lw = self._lin_w
                if rc:
                    rc.set_cmd("rest_contact_sim", False)
                    rc.set_cmd("rest_contact_sim_active", False)
                    rc.set_cmd("crs_wiper_op", 0)
                if lw:
                    lw.queue_send({"cmd": "OFF"})
                time.sleep(0.2)

            elif _tid == "T50b":
                # Cleanup T50b : stopper l'injection overcurrent, remettre BCM en OFF.
                # reset_motor_current : remet motor_current_a=0 dans trame 0x201.
                # reset_b2101 : suspend le check B2101 3s pour éviter un faux FAIL
                #               sur le prochain test qui démarre le moteur.
                self._log("  → T50b post : reset_motor_current + LIN OFF + reset B2001")
                lw = self._lin_w
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.reset_motor_current()
                    self._sim_client.reset_b2101()
                if lw:
                    lw.queue_send({"cmd": "OFF"})
                if rc:
                    rc.set_cmd("motor_current_a",  0.0)
                    rc.set_cmd("crs_wiper_op",     0)
                    rc.set_cmd("lin_op_locked",    False)
                    rc.set_cmd("front_motor_error", False)
                    rc.set_cmd("wc_available",     False)
                    # B2001 INACTIVE pour affichage complet au prochain run
                    time.sleep(0.3)
                    rc.set_cmd("dtc_inactivate", "B2001")
                    time.sleep(0.1)
                    rc.set_cmd("wc_timeout_active",  False)
                    rc.set_cmd("lin_timeout_active", False)

            elif _tid in ("T50c", "T50d"):
                # Cleanup T50c/T50d : reset courant + LIN OFF
                lw = self._lin_w
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.reset_motor_current()
                if lw: lw.queue_send({"cmd": "OFF"})
                if rc:
                    rc.set_cmd("motor_current_a",   0.0)
                    rc.set_cmd("crs_wiper_op",      0)
                    rc.set_cmd("lin_op_locked",     False)
                    rc.set_cmd("front_motor_error", False)
                    rc.set_cmd("wc_available",      False)
                    time.sleep(0.3)
                    rc.set_cmd("wc_timeout_active",  False)
                    rc.set_cmd("lin_timeout_active", False)

            elif _tid in ("T03", "T04", "T05"):
                # Retour CAS A après test CAS B
                if rc: rc.set_cmd("wc_available", False)

            elif _tid in ("T30", "T31", "T32", "T34", "T35", "T36", "T37", "T38"):
                # LIN OFF obligatoire pour T30/T31/T32/T34/T35/T36/T37 (stimulus LIN)
                lw = self._lin_w
                if _tid in ("T30", "T31", "T32", "T34", "T35", "T36", "T37") and lw:
                    lw.queue_send({"cmd": "OFF"})
                if rc:
                    rc.set_cmd("crs_wiper_op",   0)
                    rc.set_cmd("ignition_status", 1)
                    rc.set_cmd("rain_intensity",  0)
                    if _tid in ("T34", "T35"):
                        # Invalider _rc_gen : stoppe les threads refresh/cycles résiduels
                        self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                        rc.set_cmd("rain_sensor_installed", False)
                        rc.set_cmd("rest_contact_sim_active", False)
                        rc.set_cmd("rest_contact_sim",        False)
                        mw = self._motor_w
                        if mw: mw.queue_send({"rain_intensity": 0, "sensor_status": "OK"})
                    if _tid == "T36":
                        # Invalider _rc_gen + désactiver rest_contact sim
                        self._rc_gen = getattr(self, "_rc_gen", 0) + 1
                        rc.set_cmd("rest_contact_sim_active", False)
                        rc.set_cmd("rest_contact_sim",        False)

            elif _tid in ("TC_AUTO_004", "TC_FSR_008"):
                # Cleanup commun : crs_wiper_op=0 + ignition ON simulateur
                lw = self._lin_w
                mw = self._motor_w
                if rc:
                    rc.set_cmd("crs_wiper_op",   0)
                    rc.set_cmd("ignition_status", 1)
                if mw: mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
                if lw: lw.queue_send({"cmd": "OFF"})

            elif _tid == "T11":
                # Rétablir CAN TX + remettre wc_available=False
                mw = self._motor_w
                if mw: mw.queue_send({"test_cmd": "start_can_tx"})
                if rc:
                    time.sleep(0.2)
                    rc.set_cmd("wc_available", False)
                    time.sleep(0.2)
                    rc.set_cmd("wc_timeout_active", False)
                    time.sleep(0.2)
                    rc.set_cmd("ignition_status", 1)
                    rc.set_cmd("crs_wiper_op", 0)
            elif _tid in ("T21", "T22"):
                # Garder rest_contact_sim=True jusqu'à state=OFF, puis désactiver.
                # Invalider _rc_gen déjà fait plus haut (stoppe threads cycling).
                lw = self._lin_w
                if rc: rc.set_cmd("crs_wiper_op", 0)
                if rc: rc.set_cmd("rest_contact_sim", True)
                if lw: lw.queue_send({"cmd": "OFF"})
                # Attendre max 4s que BCM revienne en OFF
                for _ in range(20):
                    time.sleep(0.2)
                    if rc and rc.get("state") == "OFF":
                        break
                if rc:
                    rc.set_cmd("rest_contact_sim_active", False)
                    rc.set_cmd("rest_contact_sim",        False)
                    rc.set_cmd("crs_wiper_op", 0)

            elif _tid == "T33":
                lw = self._lin_w
                mw = self._motor_w
                if rc:
                    rc.set_cmd("ignition_status", 1)
                    rc.set_cmd("crs_wiper_op", 0)
                if mw: mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
                if lw: lw.queue_send({"cmd": "OFF"})
                time.sleep(0.8)  # laisser BCM prendre ignition=1

            elif _tid == "T39":
                lw = self._lin_w
                # OFF avant start_lin_tx → 1ère trame rétablie = WOP_OFF
                if lw:
                    lw.queue_send({"cmd": "OFF"})
                    lw.queue_send({"test_cmd": "start_lin_tx"})
                if rc:
                    rc.set_cmd("crs_wiper_op", 0)
                    time.sleep(0.6)  # ≥1.5× période LIN avant de remettre lin_timeout_active
                    rc.set_cmd("lin_timeout_active", False)
                    rc.set_cmd("crs_wiper_op", 0)

            elif _tid == "T40":
                lw = self._lin_w
                if rc:
                    rc.set_cmd("rest_contact_sim_active", False)
                    rc.set_cmd("rest_contact_sim",        False)
                    rc.set_cmd("crs_wiper_op", 0)
                if lw: lw.queue_send({"cmd": "OFF"})

            elif _tid == "T43":
                # Garder sim=True jusqu'à state=OFF, invalider génération déjà fait.
                lw = self._lin_w
                mw = self._motor_w
                if mw: mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
                if lw: lw.queue_send({"cmd": "OFF"})
                if rc:
                    rc.set_cmd("reverse_gear", False)
                    rc.set_cmd("crs_wiper_op", 0)
                    rc.set_cmd("rest_contact_sim", True)
                for _ in range(15):
                    time.sleep(0.2)
                    if rc and rc.get("state") == "OFF":
                        break
                if rc:
                    rc.set_cmd("rest_contact_sim_active", False)
                    rc.set_cmd("rest_contact_sim",        False)

            elif _tid == "T44":
                # cmd=OFF déjà envoyé à t=2000ms dans _pre_test.
                time.sleep(0.5)
                if rc:
                    rc.set_cmd("crs_wiper_op",         0)
                    rc.set_cmd("reverse_gear",         False)
                    rc.set_cmd("ignition_status",      1)
                    rc.set_cmd("rear_wiper_available", True)
                mw = self._motor_w
                if mw: mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

            elif _tid == "T45":
                lw = self._lin_w
                mw = self._motor_w
                if rc:
                    rc.set_cmd("ignition_status", 1)
                    rc.set_cmd("crs_wiper_op", 0)
                    rc.set_cmd("rest_contact_sim",        False)
                    rc.set_cmd("rest_contact_sim_active", False)
                if mw: mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})
                if lw: lw.queue_send({"cmd": "OFF"})
                time.sleep(0.8)

            elif _tid == "T50":
                lw = self._lin_w
                if lw: lw.queue_send({"cmd": "OFF"})
                if rc:
                    rc.set_cmd("crs_wiper_op",  0)
                    rc.set_cmd("ignition_status", 1)
                    rc.set_cmd("lin_op_locked", False)
                    rc.set_cmd("wc_available",  False)
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.reset_b2101()
                if rc:
                    time.sleep(0.4)
                    rc.set_cmd("wc_timeout_active",  False)
                    rc.set_cmd("wc_b2103_active",    False)
                    rc.set_cmd("lin_timeout_active", False)

            elif _tid == "T51":
                lw = self._lin_w
                mw = self._motor_w
                if lw: lw.queue_send({"cmd": "OFF"})
                if rc:
                    rc.set_cmd("crs_wiper_op",     0)
                    rc.set_cmd("rest_contact_sim", False)
                    time.sleep(0.3)
                    rc.set_cmd("rest_contact_sim_active", False)
                    rc.set_cmd("ignition_status",         1)
                if mw: mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

            elif _tid == "T38b":
                lw = self._lin_w
                mw = self._motor_w
                if rc: rc.set_cmd("motor_current_a", 0.0)
                if lw: lw.queue_send({"cmd": "OFF"})
                time.sleep(0.15)
                if rc:
                    rc.set_cmd("crs_wiper_op",    0)
                    rc.set_cmd("rear_motor_error", False)
                if mw: mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

            elif _tid == "T38c":
                lw = self._lin_w
                if rc:
                    rc.set_cmd("pump_current_a", 0.0)
                    rc.set_cmd("rest_contact_sim", True)  # maintenir True jusqu'à ERROR→OFF
                if lw: lw.queue_send({"cmd": "OFF"})
                time.sleep(0.4)  # >> cycle T-WSM 200ms → BCM est en OFF
                if rc:
                    rc.set_cmd("pump_error",              False)
                    rc.set_cmd("crs_wiper_op",            0)
                    rc.set_cmd("rest_contact_sim_active", False)
                    rc.set_cmd("rest_contact_sim",        False)

            elif _tid == "LIN_INVALID_CMD_001":
                if rc: rc.set_cmd("crs_wiper_op", 0)

            elif _tid == "TC_COM_001":
                pass   # mesure passive BREAK — rien à nettoyer

            elif _tid == "TC_LIN_002":
                lw = self._lin_w
                if lw: lw.queue_send({"test_cmd": "restore_alive_counter"})
                if rc:
                    time.sleep(0.3)
                    rc.set_cmd("lin_alive_fault", False)

            elif _tid == "TC_LIN_017_BIT1_ALONE":
                lw = self._lin_w
                # Remettre 0x17 bit0=0 et 0x16 bit6=0
                if lw:
                    lw.queue_send({"set_fault": 0})
                    lw.queue_send({"set_stuck": False})
                if rc:
                    time.sleep(0.3)
                    rc.set_cmd("b2011_active", False)

            elif _tid == "TC_LIN_016_BIT4":
                lw = self._lin_w
                if lw: lw.queue_send({"test_cmd": "restore_lin_normal"})
                if rc:
                    time.sleep(0.3)
                    rc.set_cmd("lin_timeout_active", False)

            elif _tid == "TC_LIN_016_BIT6_ALONE":
                lw = self._lin_w
                if lw:
                    lw.queue_send({"set_stuck": False})
                    lw.queue_send({"set_fault": 0})
                if rc:
                    time.sleep(0.3)
                    rc.set_cmd("b2011_active", False)

            elif _tid == "TC_B2011_AND":
                lw = self._lin_w
                if lw:
                    lw.queue_send({"set_stuck": False})
                    lw.queue_send({"set_fault": 0})
                if rc:
                    time.sleep(0.4)
                    rc.set_cmd("b2011_active", False)

            elif _tid == "TC_LIN_017_VER":
                lw = self._lin_w
                if lw:
                    lw.queue_send({"set_crs_version": 0x20})  # Restaurer version nominale
                    lw.queue_send({"set_fault": 0})
                if rc:
                    time.sleep(0.3)
                    rc.set_cmd("crs_fault_active", False)

            elif _tid in ("TC_CAN_202_ERR01", "TC_CAN_202_ERR02",
                          "TC_CAN_202_ERR04", "TC_CAN_202_ERR05"):
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.reset_mode_mismatch()
                    self._sim_client.set_motor_driver_fault(False)
                    self._sim_client.set_xcp_position_sensor_fault(False)
                    self._sim_client.set_xcp_internal_fault(False)
                if rc:
                    rc.set_cmd("wc_available",  False)
                    rc.set_cmd("crs_wiper_op",  0)
                    rc.set_cmd("lin_op_locked", False)
                    time.sleep(0.4)
                    rc.set_cmd("wc_timeout_active", False)

            elif _tid == "TC_B2104":
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.reset_b2104()
                if rc:
                    rc.set_cmd("b2104_active",   False)
                    rc.set_cmd("b2104_inactive",  True)
                    rc.set_cmd("wc_available",    False)
                    rc.set_cmd("crs_wiper_op",    0)
                    rc.set_cmd("lin_op_locked",   False)

            elif _tid == "TC_LIN_CS":
                lw = self._lin_w
                # OFF avant restore → 1ère trame rétablie = WOP_OFF
                if lw:
                    lw.queue_send({"cmd": "OFF"})
                    time.sleep(0.1)
                    lw.queue_send({"test_cmd": "restore_lin_checksum"})
                if rc:
                    rc.set_cmd("crs_wiper_op", 0)
                    # Attendre ≥600ms avant de remettre lin_timeout_active
                    time.sleep(0.6)
                    rc.set_cmd("lin_timeout_active", False)
                    rc.set_cmd("lin_checksum_fault", False)
                    rc.set_cmd("crs_wiper_op",       0)

            elif _tid == "TC_CAN_003":
                lw = self._lin_w
                mw = self._motor_w
                if mw: mw.queue_send({"test_cmd": "restore_can_alive"})
                if lw: lw.queue_send({"cmd": "OFF"})
                if rc:
                    rc.set_cmd("wc_available",   False)
                    rc.set_cmd("crs_wiper_op",   0)
                    rc.set_cmd("lin_op_locked",  False)
                    rc.set_cmd("alive_tx_frozen", False)
                    time.sleep(0.3)
                    rc.set_cmd("wc_alive_fault", False)

            elif _tid == "TC_FSR_010":
                lw = self._lin_w
                if lw: lw.queue_send({"cmd": "OFF"})
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.reset_corrupt_crc()
                else:
                    mw = self._motor_w
                    if mw: mw.queue_send({"test_cmd": "corrupt_crc_0x201", "count": 0})
                # Attendre 3.5s : _check_wc_timeout a besoin que les trames 0x201
                # reprennent normalement avant le prochain test.
                time.sleep(3.5)
                if rc:
                    rc.set_cmd("wc_timeout_active", False)

            elif _tid == "T_RAIN_AUTO_SENSOR_ERROR":
                lw = self._lin_w
                mw = self._motor_w
                if rc:
                    rc.set_cmd("rain_sensor_ok",          True)
                    rc.set_cmd("rain_intensity",           0)
                    rc.set_cmd("rain_sensor_installed",   False)
                    rc.set_cmd("crs_wiper_op",             0)
                    rc.set_cmd("rest_contact_sim_active", False)
                    rc.set_cmd("rest_contact_sim",        False)
                if lw: lw.queue_send({"cmd": "OFF"})
                if mw: mw.queue_send({"rain_intensity": 0, "sensor_status": "OK"})

            elif _tid == "T_CAS_B_SPEED1_REVERSE":
                lw = self._lin_w
                mw = self._motor_w
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.stop_blade_cycling()
                    self._sim_client.reset_b2101()
                if lw: lw.queue_send({"cmd": "OFF"})
                if rc:
                    rc.set_cmd("reverse_gear",         False)
                    rc.set_cmd("crs_wiper_op",         0)
                    rc.set_cmd("lin_op_locked",        False)
                    rc.set_cmd("wc_available",         False)
                    rc.set_cmd("rear_wiper_available", True)
                    time.sleep(0.4)
                    rc.set_cmd("wc_timeout_active",  False)
                    rc.set_cmd("lin_timeout_active", False)
                if mw: mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

            elif _tid == "T_B2009_CAN":
                lw = self._lin_w
                mw = self._motor_w
                if self._sim_client and self._sim_client.is_connected():
                    self._sim_client.unfreeze_blade_position()
                    self._sim_client.reset_b2101()
                if lw: lw.queue_send({"cmd": "OFF"})
                if rc:
                    rc.set_cmd("rest_contact_sim_active",      False)
                    rc.set_cmd("rest_contact_sim",             False)
                    rc.set_cmd("crs_wiper_op",                 0)
                    rc.set_cmd("lin_op_locked",                False)
                    rc.set_cmd("wiper_fault",                  False)
                    rc.set_cmd("_rest_contact_b2009_active",   False)
                    rc.set_cmd("wc_available",                 False)
                    time.sleep(0.4)
                    rc.set_cmd("wc_timeout_active",  False)
                    rc.set_cmd("lin_timeout_active", False)
                if mw: mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

            elif _tid == "T_B2009_CASA":
                lw = self._lin_w
                mw = self._motor_w
                if lw: lw.queue_send({"cmd": "OFF"})
                if rc:
                    rc.set_cmd("rest_contact_sim_active",     False)
                    rc.set_cmd("rest_contact_sim",            False)
                    rc.set_cmd("crs_wiper_op",                0)
                    rc.set_cmd("wiper_fault",                 False)
                    rc.set_cmd("_rest_contact_b2009_active",  False)
                    rc.set_cmd("ignition_status",             1)
                if mw: mw.queue_send({"ignition_status": "ON", "reverse_gear": 0, "vehicle_speed": 0})

            last_tid = test.ID

            # ── Fail-fast ─────────────────────────────────────────────────
            if self._fail_fast and self._results:
                last = self._results[-1]
                if last.status != "PASS":
                    self._log(f"[STOP] --fail-fast : arrêt après [{test.ID}]  "
                              f"status={last.status}")
                    break

        # ── Résumé ────────────────────────────────────────────────────────
        elapsed = time.monotonic() - t_global
        n_pass  = sum(1 for r in self._results if r.status == "PASS")
        n_fail  = sum(1 for r in self._results if r.status == "FAIL")
        n_to    = sum(1 for r in self._results if r.status == "TIMEOUT")
        self._log("")
        self._log("=" * 70)
        self._log(f"Campagne terminée en {elapsed:.1f}s")
        self._log(f"PASS={n_pass}   FAIL={n_fail}   TIMEOUT={n_to}   "
                  f"TOTAL={len(self._results)}")
        self._log("=" * 70)

        return self._results


# ═════════════════════════════════════════════════════════════════════════════
#  EXPORTS RAPPORTS
# ═════════════════════════════════════════════════════════════════════════════

def export_json(results: list, t_start: datetime.datetime,
                t_end: datetime.datetime, bench_id: str, path: str):
    """Écrit un fichier JSON structuré — compatible importateurs XRAY/Jira."""
    data = {
        "bench_id":   bench_id,
        "t_start":    t_start.isoformat(),
        "t_end":      t_end.isoformat(),
        "duration_s": (t_end - t_start).total_seconds(),
        "summary": {
            "total":   len(results),
            "pass":    sum(1 for r in results if r.status == "PASS"),
            "fail":    sum(1 for r in results if r.status == "FAIL"),
            "timeout": sum(1 for r in results if r.status == "TIMEOUT"),
        },
        "tests": [
            {
                "id":       r.test_id,
                "name":     r.name,
                "category": r.category,
                "ref":      r.ref,
                "status":   r.status,
                "limit":    r.limit,
                "measured": r.measured,
                "details":  r.details,
            }
            for r in results
        ],
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def export_junit(results: list, t_start: datetime.datetime,
                 t_end: datetime.datetime, bench_id: str, path: str):
    """
    Écrit un rapport JUnit XML compatible Jenkins (plugin JUnit).
    Chaque TestResult → <testcase> ; FAIL → <failure> ; TIMEOUT → <error>.
    """
    import xml.etree.ElementTree as ET

    n_fail = sum(1 for r in results if r.status == "FAIL")
    n_to   = sum(1 for r in results if r.status == "TIMEOUT")
    dur    = (t_end - t_start).total_seconds()

    suite = ET.Element("testsuite", {
        "name":      bench_id,
        "tests":     str(len(results)),
        "failures":  str(n_fail),
        "errors":    str(n_to),
        "skipped":   "0",
        "time":      f"{dur:.3f}",
        "timestamp": t_start.isoformat(),
    })

    for r in results:
        tc = ET.SubElement(suite, "testcase", {
            "classname": r.category or "WipeWash",
            "name":      f"[{r.test_id}] {r.name}",
            "time":      "0",
        })
        if r.status == "FAIL":
            fail = ET.SubElement(tc, "failure", {
                "message": r.details or "FAIL",
                "type":    "AssertionError",
            })
            fail.text = (
                f"Test   : {r.test_id} — {r.name}\n"
                f"Réf    : {r.ref}\n"
                f"Limite : {r.limit}\n"
                f"Mesuré : {r.measured}\n"
                f"Détail : {r.details}"
            )
        elif r.status == "TIMEOUT":
            err = ET.SubElement(tc, "error", {
                "message": r.details or "TIMEOUT",
                "type":    "TimeoutError",
            })
            err.text = f"Test {r.test_id} n'a pas répondu dans le délai imparti."

    ET.indent(suite, space="  ")
    tree = ET.ElementTree(suite)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(ET.tostring(suite, encoding="unicode"))


def export_all(results, t_start, t_end, args, ts):
    """Génère les trois formats de rapport."""
    bench_id = args.bench_id

    # ── HTML ──────────────────────────────────────────────────────────────
    html_path = args.output or f"report_{ts}.html"
    try:
        gen = ReportGenerator(bench_id=bench_id, operator=args.operator)
        gen.generate(results, html_path, t_start=t_start, t_end=t_end)
        print(f"[RAPPORT] HTML  → {os.path.abspath(html_path)}", flush=True)
    except Exception as exc:
        print(f"[WARN]  Rapport HTML échoué : {exc}", file=sys.stderr)

    # ── JSON ──────────────────────────────────────────────────────────────
    json_path = args.json or f"results_{ts}.json"
    try:
        export_json(results, t_start, t_end, bench_id, json_path)
        print(f"[RAPPORT] JSON  → {os.path.abspath(json_path)}", flush=True)
    except Exception as exc:
        print(f"[WARN]  Rapport JSON échoué : {exc}", file=sys.stderr)

    # ── JUnit XML ─────────────────────────────────────────────────────────
    junit_path = args.junit or f"junit_{ts}.xml"
    try:
        export_junit(results, t_start, t_end, bench_id, junit_path)
        print(f"[RAPPORT] JUnit → {os.path.abspath(junit_path)}", flush=True)
    except Exception as exc:
        print(f"[WARN]  Rapport JUnit échoué : {exc}", file=sys.stderr)


# ═════════════════════════════════════════════════════════════════════════════
#  POINT D'ENTRÉE CLI
# ═════════════════════════════════════════════════════════════════════════════

def parse_args():
    p = argparse.ArgumentParser(
        description="WipeWash BCM — Runner de tests headless (Windows/Jenkins)"
    )
    p.add_argument("--bcm-host",   default="",
                   help="IP RPiBCM (défaut : auto-découverte)")
    p.add_argument("--sim-host",   default="",
                   help="IP RPiSIM (défaut : auto-découverte)")
    p.add_argument("--redis-host", default="",
                   help="IP Redis  (défaut : même que bcm-host)")
    p.add_argument("--redis-port", type=int, default=6379,
                   help="Port Redis (défaut : 6379)")
    p.add_argument("--tests",      default="",
                   help="IDs séparés par virgule. Ex: T30,T31,T32")
    p.add_argument("--output",     default="",
                   help="Chemin rapport HTML")
    p.add_argument("--json",       default="",
                   help="Chemin rapport JSON")
    p.add_argument("--junit",      default="",
                   help="Chemin rapport JUnit XML")
    p.add_argument("--timeout",    type=float, default=600.0,
                   help="Timeout global en secondes (défaut : 600)")
    p.add_argument("--bench-id",   default="WipeWash-Bench-CI",
                   help="Identifiant banc pour les rapports")
    p.add_argument("--operator",   default="jenkins",
                   help="Nom opérateur / job Jenkins")
    p.add_argument("--fail-fast",  action="store_true",
                   help="Arrêter dès le premier FAIL")
    return p.parse_args()


def main():
    args = parse_args()
    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    print("=" * 70, flush=True)
    print("  WipeWash BCM — Test Runner Headless (Windows / Jenkins CI)", flush=True)
    print(f"  Démarrage : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print("=" * 70, flush=True)

    # ── 1. Découverte réseau ──────────────────────────────────────────────
    bcm_host = args.bcm_host.strip()
    sim_host = args.sim_host.strip()

    if not bcm_host or not sim_host:
        print("[INFRA] Auto-découverte des RPi sur 10.20.0.0/28…", flush=True)
        hosts = auto_discover_all(port=5000, timeout=4.0)
        print(f"[INFRA] Hôtes détectés (port 5000) : {hosts}", flush=True)
        if not bcm_host:
            bcm_host = hosts[0] if hosts else ""
        if not sim_host:
            sim_host = hosts[1] if len(hosts) > 1 else (hosts[0] if hosts else "")

    if not bcm_host:
        print("[ERREUR] RPiBCM introuvable. Vérifiez le réseau ou utilisez --bcm-host.",
              file=sys.stderr)
        sys.exit(2)

    redis_host = args.redis_host.strip() or bcm_host
    print(f"[INFRA] BCM={bcm_host}  SIM={sim_host}  "
          f"Redis={redis_host}:{args.redis_port}", flush=True)

    # ── 2. Connexion Redis ────────────────────────────────────────────────
    rte_client = RTEClient(redis_host, args.redis_port)
    if not rte_client.is_connected():
        print(f"[ERREUR] Redis inaccessible sur {redis_host}:{args.redis_port}",
              file=sys.stderr)
        sys.exit(2)
    print(f"[INFRA] Redis OK", flush=True)

    # ── 3. SimClient ──────────────────────────────────────────────────────
    sim_client = SimClient()
    sim_client.connect(sim_host)

    # ── 4. Workers TCP ────────────────────────────────────────────────────
    # CAN (port 5557) et LIN (port 5555) sont servis par le RPiSIM (crslin.py + bcm_tcp_can.py)
    # Motor RX (port 5000) et Pump (port 5556) sont servis par le RPiBCM
    can_worker   = HeadlessCANWorker(sim_host)
    lin_worker   = HeadlessLINWorker(sim_host)
    motor_worker = HeadlessMotorWorker(bcm_host, sim_host)
    pump_signal  = HeadlessPumpSignal(bcm_host)

    can_worker.start()
    lin_worker.start()
    motor_worker.start()
    pump_signal.start()

    print("[INFRA] Workers TCP démarrés — attente stabilisation 2s…", flush=True)
    time.sleep(HeadlessTestRunner.SETTLE_AFTER_INIT)

    # ── 5. Sélection des tests ────────────────────────────────────────────
    if args.tests.strip():
        ids      = [t.strip() for t in args.tests.split(",") if t.strip()]
        selected = [cls for cls in ALL_TESTS if cls.ID in ids]
        if not selected:
            print(f"[ERREUR] Aucun test trouvé pour IDs : {ids}", file=sys.stderr)
            sys.exit(2)
        print(f"[INFO] {len(selected)} test(s) sélectionné(s) : {ids}", flush=True)
    else:
        selected = list(ALL_TESTS)
        print(f"[INFO] {len(selected)} test(s) — campagne complète", flush=True)

    # ── 6. Gestionnaire SIGINT/SIGTERM (rapport partiel si interruption) ──
    t_start = datetime.datetime.now()

    def _on_interrupt(sig, frame):
        print("\n[CI] Interruption — génération rapport partiel…", flush=True)
        export_all(runner._results, t_start, datetime.datetime.now(), args, ts)
        sys.exit(130)

    signal.signal(signal.SIGINT,  _on_interrupt)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_interrupt)

    # ── 7. Exécution ──────────────────────────────────────────────────────
    runner = HeadlessTestRunner(
        can_worker, lin_worker, motor_worker,
        pump_signal  = pump_signal,
        rte_client   = rte_client,
        sim_client   = sim_client,
        fail_fast    = args.fail_fast,
    )

    results = runner.run(selected, global_timeout_s=args.timeout)
    t_end   = datetime.datetime.now()

    # ── 8. Export rapports ────────────────────────────────────────────────
    export_all(results, t_start, t_end, args, ts)

    # ── 9. Code de sortie ─────────────────────────────────────────────────
    n_fail = sum(1 for r in results if r.status == "FAIL")
    n_to   = sum(1 for r in results if r.status == "TIMEOUT")

    if n_fail > 0 or n_to > 0:
        print(f"\n[CI] [FAIL]  FAIL={n_fail}  TIMEOUT={n_to}  →  exit 1", flush=True)
        sys.exit(1)
    else:
        print(f"\n[CI] [PASS]  Tous les tests PASS  →  exit 0", flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
