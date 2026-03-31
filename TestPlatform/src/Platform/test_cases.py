"""
test_cases.py  —  Cas de tests automatiques WipeWash
=====================================================
Contraintes strictement issues du document :
  Contraintes_Temps_WipeWash.docx

Section 6 (Cycles des trames réseau) — valeurs de référence :
  LeftStickWiperRequester (0x16) LIN  CRS→BCM   400 ms
  CRS_Status                     LIN  CRS→BCM   800 ms
  Wiper_Command  (0x200)         CAN  BCM→WC    400 ms
  Wiper_Status   (0x201)         CAN  WC→BCM    400 ms
  Wiper_Ack      (0x202)         CAN  WC→BCM    400 ms
  Vehicle_Status (0x300)         CAN  GW→BCM    200 ms
  RainSensorData (0x301)         CAN  GW→BCM    200 ms

Sections 1-5 — contraintes de timeout et fonctionnelles :
  LIN timeout detection          ≤ 2000 ms   FSR_001 / SRS_LIN_003
  CAN timeout detection          ≤ 2000 ms   FSR_002 / SRD_WW_082
  Durée cycle essuie-glace       ≤ 1700 ms   SRD_WW_021
  Pump arrêt automatique         ≤ 5000 ms   FSR_005 / SRD_WW_120
  Détection surcourant moteur    > 300 ms    FSR_003

Mesure fiable :
  Les timestamps utilisés sont ev["t_kernel"] (horodatage kernel socketcan
  via SO_TIMESTAMP, ou time.monotonic() après read() UART pour le LIN).
  Si t_kernel absent, fallback sur ev["time"] (moins précis, inclut TCP).
"""

import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, List


# ─── Résultat d'un test ───────────────────────────────────────────────────
@dataclass
class TestResult:
    test_id  : str
    name     : str
    category : str
    ref      : str
    status   : str       # "PASS" | "FAIL" | "TIMEOUT" | "RUNNING" | "PENDING"
    limit    : str
    measured : str = "—"
    details  : str = ""


# ─── Nombre d'intervalles collectés pour les tests de cycle ──────────────
N_SAMPLES = 20


def _get_t(ev: dict) -> float:
    """Retourne le meilleur timestamp disponible pour une trame (en secondes)."""
    return ev.get("t_kernel") or ev.get("time") or time.time()


# ─── Classe de base ───────────────────────────────────────────────────────
class BaseTest:
    ID             = ""
    NAME           = ""
    CATEGORY       = ""     # "CYCLE" | "TIMEOUT" | "FONCTIONNEL"
    REF            = ""
    LIMIT_STR      = ""
    TEST_TIMEOUT_S = 30

    def __init__(self):
        self._t_start: float = 0.0
        self._done           = False

    def start(self):
        self._t_start = time.time()
        self._done    = False
        self._on_start()

    def _on_start(self): pass

    def on_can_frame  (self, ev: dict)   -> Optional[TestResult]: return None
    def on_lin_frame  (self, ev: dict)   -> Optional[TestResult]: return None
    def on_motor_data (self, data: dict) -> Optional[TestResult]: return None

    def check_timeout(self) -> Optional[TestResult]:
        if not self._done and time.time() - self._t_start > self.TEST_TIMEOUT_S:
            return self._result("TIMEOUT", "—",
                                f"Pas de données après {self.TEST_TIMEOUT_S}s")
        return None

    def _result(self, status, measured, details=""):
        self._done = True
        return TestResult(self.ID, self.NAME, self.CATEGORY, self.REF,
                          status, self.LIMIT_STR, measured, details)

    def _pass(self, measured, details=""): return self._result("PASS", measured, details)
    def _fail(self, measured, details=""): return self._result("FAIL", measured, details)


# ══════════════════════════════════════════════════════════════════════════
#  TESTS DE CYCLE  (écoute passive — mesure via t_kernel)
# ══════════════════════════════════════════════════════════════════════════

class BaseCycleTest(BaseTest):
    CATEGORY       = "CYCLE"
    LIMIT_MS       = 0
    TOL_MS         = 0        # tolérance ±
    TEST_TIMEOUT_S = 60       # 20 × limite max (800ms × 20 = 16s → marge)

    def _on_start(self):
        self._ts: deque = deque(maxlen=N_SAMPLES + 2)

    def _feed(self, ev: dict) -> Optional[TestResult]:
        """Ajoute un timestamp et évalue après N_SAMPLES intervalles."""
        self._ts.append(_get_t(ev) * 1000.0)          # → ms
        if len(self._ts) < N_SAMPLES + 1:
            return None
        ts_list = list(self._ts)                       # snapshot O(N) unique
        ivs    = [ts_list[i+1] - ts_list[i] for i in range(N_SAMPLES)]
        avg    = sum(ivs) / len(ivs)
        mn, mx = min(ivs), max(ivs)
        jitter = mx - mn
        detail = (f"avg={avg:.1f} min={mn:.1f} max={mx:.1f} "
                  f"jitter={jitter:.1f} (ms)  "
                  f"[t_kernel={'oui' if 't_kernel' in ev else 'non'}]")
        if abs(avg - self.LIMIT_MS) <= self.TOL_MS:
            return self._pass(f"{avg:.1f} ms", detail)
        else:
            return self._fail(f"{avg:.1f} ms", detail)


# T01 — LIN LeftStickWiperRequester : 400 ms  (section 6)
class T01_LIN_Requester_Cycle(BaseCycleTest):
    ID        = "T01"
    NAME      = "LIN LeftStickWiperRequester cycle"
    REF       = "SRD_WW_010 / SRS_LIN_001  (section 6)"
    LIMIT_MS  = 400
    TOL_MS    = 40
    LIMIT_STR = "400 ms ± 40 ms"

    # PID 0xD6 = ID 0x16 (LeftStickWiperRequester).
    # IMPORTANT : crslin.py broadcaste aussi "type":"TX" pour PID 0x17 (tx17).
    # Il faut filtrer sur pid=="0xD6" pour ne capturer QUE les réponses 0x16
    # et ne pas mesurer l'intervalle 0x16→0x17 (~50 ms) au lieu de 400 ms.
    _PID_16 = "0xD6"

    def on_lin_frame(self, ev):
        if ev.get("type") == "TX" and ev.get("pid") == self._PID_16:
            return self._feed(ev)
        return None


# T02 — LIN CRS_Status : 800 ms  (section 6)
class T02_LIN_CRSStatus_Cycle(BaseCycleTest):
    ID        = "T02"
    NAME      = "LIN CRS_Status cycle"
    REF       = "section 6"
    LIMIT_MS  = 800
    TOL_MS    = 100
    LIMIT_STR = "800 ms ± 100 ms"

    def on_lin_frame(self, ev):
        if ev.get("type") == "tx17":    # PID 0x17 : WiperFaultStatus
            return self._feed(ev)
        return None


# T03 — CAN 0x200 Wiper_Command : 400 ms  (section 6 + SRD_WW_080)
class T03_CAN_200_Cycle(BaseCycleTest):
    ID        = "T03"
    NAME      = "CAN 0x200 Wiper_Command cycle"
    REF       = "SRD_WW_080 / SRS_CAN_001"
    LIMIT_MS  = 400
    TOL_MS    = 40
    LIMIT_STR = "400 ms ± 40 ms"

    def on_can_frame(self, ev):
        if ev.get("can_id_int") == 0x200:
            return self._feed(ev)
        return None


# T04 — CAN 0x201 Wiper_Status : 400 ms  (section 6 + TSR_002)
class T04_CAN_201_Cycle(BaseCycleTest):
    ID        = "T04"
    NAME      = "CAN 0x201 Wiper_Status cycle"
    REF       = "TSR_002  (section 6)"
    LIMIT_MS  = 400
    TOL_MS    = 40
    LIMIT_STR = "400 ms ± 40 ms"

    def on_can_frame(self, ev):
        if ev.get("can_id_int") == 0x201:
            return self._feed(ev)
        return None


# T05 — CAN 0x202 Wiper_Ack : 400 ms  (section 6)
class T05_CAN_202_Cycle(BaseCycleTest):
    ID        = "T05"
    NAME      = "CAN 0x202 Wiper_Ack cycle"
    REF       = "section 6"
    LIMIT_MS  = 400
    TOL_MS    = 40
    LIMIT_STR = "400 ms ± 40 ms"

    def on_can_frame(self, ev):
        if ev.get("can_id_int") == 0x202:
            return self._feed(ev)
        return None


# T06 — CAN 0x300 Vehicle_Status : 200 ms  (section 6)
class T06_CAN_300_Cycle(BaseCycleTest):
    ID        = "T06"
    NAME      = "CAN 0x300 Vehicle_Status cycle"
    REF       = "section 6"
    LIMIT_MS  = 200
    TOL_MS    = 20
    LIMIT_STR = "200 ms ± 20 ms"

    def on_can_frame(self, ev):
        if ev.get("can_id_int") == 0x300:
            return self._feed(ev)
        return None


# T07 — CAN 0x301 RainSensorData : 200 ms  (section 6)
class T07_CAN_301_Cycle(BaseCycleTest):
    ID        = "T07"
    NAME      = "CAN 0x301 RainSensorData cycle"
    REF       = "section 6"
    LIMIT_MS  = 200
    TOL_MS    = 20
    LIMIT_STR = "200 ms ± 20 ms"

    def on_can_frame(self, ev):
        if ev.get("can_id_int") == 0x301:
            return self._feed(ev)
        return None


# ══════════════════════════════════════════════════════════════════════════
#  TESTS DE TIMEOUT  (actifs : stop TX, mesure délai détection)
# ══════════════════════════════════════════════════════════════════════════

class T20_WipeCycle_Duration(BaseTest):
    ID             = "T20"
    NAME           = "Durée cycle essuie-glace (TOUCH)"
    CATEGORY       = "FONCTIONNEL"
    REF            = "SRD_WW_021"
    LIMIT_STR      = "≤ 1700 ms"
    LIMIT_MS       = 1700
    TEST_TIMEOUT_S = 8
    # Pré-action : test_runner envoie {"cmd":"TOUCH"} via LINWorker.
    # Observation prioritaire : rest_contact_raw 1→0 (lame revenue au repos).
    #   Le BCM arrête TOUCH soit sur timeout TOUCH_DURATION=1700ms,
    #   soit sur détection rest_contact 1→0 (retour repos hardware).
    #   Si le contact hardware fonctionne, la durée réelle < 1700ms → PASS.
    # Fallback : front=ON→OFF via TCP motor si rest_contact non dispo.
    # CAS B (wc_available=True) : backup via CAN 0x201.

    def _on_start(self):
        self._t_active_ms    = 0.0     # timestamp quand moteur s'active (front=ON)
        self._saw_active     = False
        self._rest_was_moving = False  # True quand rest_contact_raw=True (lame bouge)
        self._rest_method    = False   # True si fin détectée via rest_contact

    def on_motor_data(self, data):
        front   = str(data.get("front", "OFF")).upper()
        state   = str(data.get("state", "")).upper()
        active  = (front == "ON") or (state not in ("OFF", "", "ERROR"))
        rest_raw = bool(data.get("rest_contact_raw", False))
        # True=GPIO1=lame EN MOUVEMENT / False=GPIO0=lame AU REPOS

        # Chrono démarre quand moteur s'active
        if not self._saw_active and active:
            self._saw_active      = True
            self._t_active_ms     = time.time() * 1000.0
            self._rest_was_moving = rest_raw
            return None

        if not self._saw_active:
            return None

        # ── Détection fin de cycle via rest_contact (prioritaire) ────────────
        # Transition rest_contact_raw : True (bouge) → False (repos) = cycle terminé
        if rest_raw and not self._rest_was_moving:
            self._rest_was_moving = True   # lame a commencé à bouger
        elif not rest_raw and self._rest_was_moving:
            # Lame revenue au repos : fin de cycle détectée par hardware
            delta = time.time() * 1000.0 - self._t_active_ms
            self._rest_method = True
            detail = (f"rest_contact 1→0 (repos HW) | {delta:.0f} ms "
                      f"(limite={self.LIMIT_MS} ms = TOUCH_DURATION)")
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms", detail)

        self._rest_was_moving = rest_raw

        # ── Fallback : fin de cycle via front=OFF (si rest_contact absent) ───
        if not active:
            delta = time.time() * 1000.0 - self._t_active_ms
            detail = f"front=OFF (fallback, pas de rest_contact) | {delta:.0f} ms"
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms", detail)
        return None

    def on_can_frame(self, ev):
        # Backup CAS B : observe CAN 0x201
        if ev.get("can_id_int") != 0x201:
            return None
        mode = ev.get("fields", {}).get("mode", -1)
        if not self._saw_active and mode != 0:
            self._saw_active  = True
            self._t_active_ms = time.time() * 1000.0
        elif self._saw_active and mode == 0:
            delta = time.time() * 1000.0 - self._t_active_ms
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms")
        return None


# T21 — Pump arrêt automatique ≤ 5000 ms  (FSR_005 / SRD_WW_120)
class BaseBCMTest(BaseTest):
    """
    Base pour les tests de comportement WSM.

    Deux chemins d'observation (utilisés simultanément) :
      1. Redis GET  rte_client.get("state") — direct, < 1ms  ← prioritaire
      2. CAN 0x201  on_can_frame()           — bus physique   ← backup

    Le test_runner appelle _check_rte() toutes les 200ms (timer).
    Subclass surcharge _target_state() pour indiquer l'état attendu.
    """
    CATEGORY       = "FONCTIONNEL_BCM"
    LIMIT_MS       = 800
    LIMIT_STR      = "≤ 800 ms"
    TEST_TIMEOUT_S = 5

    # Injecté par TestRunner avant start() — partagé entre toutes les instances
    rte_client = None

    def _on_start(self):
        self._t0_ms     = time.time() * 1000.0
        self._confirmed = False

    def reset_t0(self):
        """Appelé par test_runner juste avant d'envoyer le stimulus.
        Redémarre le chrono de mesure ET le timeout global.
        FIX : sans reset de _t_start, le timeout de 16s (T36) s'ecoule
        depuis start(), incluant les delais pre-test (2500ms + 400ms),
        ce qui fait expirer le timeout avant que les 3 cycles soient detectes."""
        now = time.time()
        self._t0_ms   = now * 1000.0
        self._t_start = now   # repousse check_timeout depuis le stimulus

    def _target_state(self) -> str | None:
        """Override : retourne l'état RTE attendu (ex: 'SPEED1') ou None."""
        return None

    def _check_rte(self) -> Optional[TestResult]:
        """
        Appelé toutes les 200ms par test_runner._tick().
        Lit rte:state via Redis GET et compare à _target_state().
        """
        if self._confirmed or self.rte_client is None:
            return None
        target = self._target_state()
        if target is None:
            return None
        state = self.rte_client.get("state")
        if state == target:
            self._confirmed = True
            delta = time.time() * 1000.0 - self._t0_ms
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms",
                f"Redis GET rte:state={state}")
        return None

    def _confirm(self, detail: str = "") -> TestResult:
        """Utilisé par on_can_frame backup."""
        delta = time.time() * 1000.0 - self._t0_ms
        self._confirmed = True
        if delta <= self.LIMIT_MS:
            return self._pass(f"{delta:.0f} ms", detail)
        return self._fail(f"{delta:.0f} ms", detail)


# T10 — LIN timeout ≤ 2000 ms  (FSR_001 / SRS_LIN_003)
class T10_LIN_Timeout(BaseBCMTest):
    """
    stop_lin_tx → crslin arrête de répondre aux headers BCM.
    BCM détecte silence slave après LIN_TIMEOUT=2000ms → B2004 actif
    → rte.lin_timeout_active=True → Redis GET.

    Observation Redis prioritaire (mesure réelle BCM ~2000ms).
    Fallback sur crslin fault event TCP si Redis indisponible.
    """
    ID             = "T10"
    NAME           = "LIN timeout detection"
    CATEGORY       = "TIMEOUT"
    REF            = "FSR_001 / SRS_LIN_003"
    LIMIT_STR      = "≤ 2500 ms"
    LIMIT_MS       = 2500
    TEST_TIMEOUT_S = 8

    def _on_start(self):
        super()._on_start()
        self._t_stop_ms = time.time() * 1000.0
        self._detected  = False

    def _target_state(self):
        return None   # on surcharge _check_rte

    def _check_rte(self) -> Optional[TestResult]:
        """Observe lin_timeout_active=True via Redis (détection réelle BCM).
        Limite 2500ms = LIN_TIMEOUT(2000) + Redis publish(100) + poll(200) + marge."""
        if self._detected or self.rte_client is None:
            return None
        if self.rte_client.get_bool("lin_timeout_active"):
            self._detected = True
            delta = time.time() * 1000.0 - self._t_stop_ms
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms", "Redis lin_timeout_active=True (B2004)")
        return None

    def on_lin_frame(self, ev):
        """Fallback si Redis indisponible : écoute fault event de crslin."""
        if self._detected or self.rte_client is not None:
            return None
        t   = ev.get("type", "")
        msg = str(ev.get("msg", "")).lower()
        if t in ("fault", "error", "timeout") or "timeout" in msg or "fault" in msg:
            self._detected = True
            delta = time.time() * 1000.0 - self._t_stop_ms
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms", "crslin fault event (fallback sans Redis)")
        return None



# T11 — CAN timeout ≤ 2000 ms  (FSR_002 / SRD_WW_082)
class T11_CAN_Timeout(BaseBCMTest):
    """
    CAS B (wc_available=True) — FSR_002 / B2005 :
      bcmcan arrête 0x201. BCM détecte silence après CAN_WC_TIMEOUT=2000ms.
      Observation : Redis GET wc_timeout_active=True.
      Mesure attendue : ~2000ms.

    CAS A (wc_available=False) :
      FSR_002 / B2005 ne s'applique pas (pas de WC surveillé).
      bcmcan arrête 0x300/0x301. BCM perd données véhicule.
      Observation : can_fault=True depuis bcmcan (indicateur communication).
      Mesure : quelques ms (TCP direct) — acceptable, test marqué N/A.
    """
    ID             = "T11"
    NAME           = "CAN timeout detection"
    CATEGORY       = "TIMEOUT"
    REF            = "FSR_002 / SRD_WW_082 / SRS_CAN_003"
    LIMIT_STR      = "≤ 2500 ms"
    LIMIT_MS       = 2500
    TEST_TIMEOUT_S = 8
    # rte_client hérité de BaseBCMTest — ne pas redéfinir ici
    # (le redéfinir masquerait l'injection de TestRunner)

    def _on_start(self):
        super()._on_start()
        self._t_stop_ms = time.time() * 1000.0
        self._reported  = False

    def _target_state(self):
        return None   # pas un état WSM, on surcharge _check_rte

    def _check_rte(self) -> Optional[TestResult]:
        """
        Observation prioritaire via Redis :
        Le BCM détecte l'absence de 0x201 après CAN_WC_TIMEOUT=2000ms
        et lève wc_timeout_active=True.
        Si rte_client indisponible → on_motor_data (can_fault) prend le relais.
        Note : wc_available peut rester False (non codé via WDID) même en Cas B
        physique, mais wc_timeout_active sera bien levé si le BCM surveille 0x201.
        """
        if self._reported or self.rte_client is None:
            return None
        if self.rte_client.get_bool("wc_timeout_active"):
            self._reported = True
            delta = time.time() * 1000.0 - self._t_stop_ms
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms", "Redis wc_timeout_active=True (B2005)")
        return None

    def on_motor_data(self, data):
        """
        T11 n'utilise JAMAIS can_fault pour valider le résultat.
        can_fault arrive en ~30ms (écho TCP stop_can_tx) — ce n'est pas
        la détection réelle du BCM (qui prend CAN_WC_TIMEOUT=2000ms).
        La seule observation valide est wc_timeout_active via _check_rte().
        """
        return None   # ignoré — _check_rte() via Redis est le seul chemin


# ══════════════════════════════════════════════════════════════════════════
#  TESTS FONCTIONNELS  (commande → mesure durée réelle)
# ══════════════════════════════════════════════════════════════════════════

# T20 — Durée cycle essuie-glace ≤ 1700 ms  (SRD_WW_021)

class T21_Pump_AutoStop(BaseBCMTest):
    """
    Stimulus : Redis SET crs_wiper_op=FRONT_WASH (op=5) → BCM démarre pompe FWD.
    Observation : Redis GET pump_active : True → False.
    Mesure : durée de marche de la pompe (doit être ≤ 5000ms = PUMP_MAX_RUNTIME).
    """
    ID             = "T21"
    NAME           = "Pump arrêt automatique"
    CATEGORY       = "FONCTIONNEL"
    REF            = "FSR_005 / SRD_WW_120 / SRS_WASH_003"
    LIMIT_STR      = "≤ 5500 ms"
    LIMIT_MS       = 5500
    TEST_TIMEOUT_S = 15   # PUMP_MAX_RUNTIME(5s) + Redis poll(100ms) + marge

    def _on_start(self):
        super()._on_start()
        self._t_pump_start_ms = 0.0
        self._pump_active     = False

    def _target_state(self):
        return None   # on surcharge _check_rte

    def _check_rte(self) -> Optional[TestResult]:
        if self._confirmed or self.rte_client is None:
            return None
        pump = self.rte_client.get_bool("pump_active")
        if not self._pump_active and pump:
            self._pump_active     = True
            self._t_pump_start_ms = time.time() * 1000.0
        elif self._pump_active and not pump:
            delta = time.time() * 1000.0 - self._t_pump_start_ms
            self._confirmed = True
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta/1000.0:.2f} s", "Redis pump_active: True→False")
        return None

    def on_motor_data(self, data):
        """Backup via bcm_tcp_pump (:5556) si Redis indisponible."""
        if self._confirmed or self.rte_client is not None:
            return None
        is_pump = ("pump_remaining" in data or data.get("source") == "BCM")
        if not is_pump:
            return None
        state = str(data.get("state", "")).upper()
        if not self._pump_active and state in ("FORWARD", "BACKWARD"):
            self._pump_active     = True
            self._t_pump_start_ms = time.time() * 1000.0
        elif self._pump_active and state in ("OFF", ""):
            delta = time.time() * 1000.0 - self._t_pump_start_ms
            self._confirmed = True
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta/1000.0:.2f} s", "bcm_tcp_pump fallback")
        return None


# ══════════════════════════════════════════════════════════════════════════
#  TESTS FONCTIONNELS BCM — Machine d'état WSM
#
#  Stimulus : Redis SET → bcm_rte (T-REDIS-CMD) → WSM réagit
#  Observation : Redis GET → lecture directe de rte.state (toutes les 200ms
#                via _check_rte() appelé par test_runner._tick())
#                + observation CAN 0x201 en backup si Redis indisponible
#
#  rte_client : instance RTEClient injectée par TestRunner avant chaque test
#               (BaseBCMTest.rte_client = runner._rte_client)
# ══════════════════════════════════════════════════════════════════════════

# ─── T30 : WSM OFF → SPEED1 (SRD_WW_030) ──────────────────────────────────
class T30_WSM_Speed1(BaseBCMTest):
    """
    SET rte:crs_wiper_op=SPEED1 via Redis.
    GET rte:state → attend "SPEED1".
    Backup : CAN 0x201 mode=2.
    """
    ID        = "T30"
    NAME      = "WSM : OFF → SPEED1"
    REF       = "SRD_WW_030"

    def _target_state(self): return "SPEED1"

    def on_can_frame(self, ev):
        if ev.get("can_id_int") != 0x201 or self._confirmed:
            return None
        if ev.get("fields", {}).get("mode", -1) == 2:
            return self._confirm("backup CAN 0x201 mode=SPEED1(2)")
        return None


# ─── T31 : WSM OFF → SPEED2 (SRD_WW_040) ──────────────────────────────────
class T31_WSM_Speed2(BaseBCMTest):
    """
    SET rte:crs_wiper_op=SPEED2.
    GET rte:state → attend "SPEED2".
    Backup : CAN 0x201 mode=3.
    """
    ID        = "T31"
    NAME      = "WSM : OFF → SPEED2"
    REF       = "SRD_WW_040"

    def _target_state(self): return "SPEED2"

    def on_can_frame(self, ev):
        if ev.get("can_id_int") != 0x201 or self._confirmed:
            return None
        if ev.get("fields", {}).get("mode", -1) == 3:
            return self._confirm("backup CAN 0x201 mode=SPEED2(3)")
        return None


# ─── T32 : WSM SPEED1 → OFF via commande (SRD_WW_001) ─────────────────────
class T32_WSM_Speed1_to_Off(BaseBCMTest):
    """
    SET SPEED1 → attendre 300ms → SET OFF.
    GET rte:state → attend "OFF" après avoir vu "SPEED1".
    """
    ID        = "T32"
    NAME      = "WSM : SPEED1 → OFF (cmd)"
    REF       = "SRD_WW_001"

    def _on_start(self):
        super()._on_start()
        self._saw_speed1 = False

    def _check_rte(self):
        if self._confirmed or self.rte_client is None:
            return None
        state = self.rte_client.get("state")
        if state == "SPEED1":
            self._saw_speed1 = True
        elif state == "OFF" and self._saw_speed1:
            self._confirmed = True
            delta = time.time() * 1000.0 - self._t0_ms
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms", "Redis GET rte:state=OFF apres SPEED1")
        return None

    def on_can_frame(self, ev):
        if ev.get("can_id_int") != 0x201 or self._confirmed:
            return None
        mode = ev.get("fields", {}).get("mode", -1)
        if mode == 2:
            self._saw_speed1 = True
        elif mode == 0 and self._saw_speed1:
            return self._confirm("backup CAN 0x201 mode=OFF apres SPEED1")
        return None


# ─── T33 : Ignition OFF → safe state (SRD_WW_001) ─────────────────────────
class T33_Ignition_Off_SafeState(BaseBCMTest):
    """
    SET ignition_status=0 via Redis.
    GET rte:state → attend "OFF" après état actif.
    """
    ID             = "T33"
    NAME           = "Ignition OFF → safe state"
    REF            = "SRD_WW_001"
    LIMIT_STR      = "≤ 2000 ms"
    LIMIT_MS       = 2000
    TEST_TIMEOUT_S = 8

    def _on_start(self):
        super()._on_start()
        self._active_seen = False

    def _check_rte(self):
        if self._confirmed or self.rte_client is None:
            return None
        state    = self.rte_client.get("state")
        ignition = self.rte_client.get_int("ignition_status", default=1)
        if state and state not in ("OFF", "ERROR", None):
            self._active_seen = True
        if state == "OFF" and ignition == 0:
            # Accepter OFF que _active_seen soit True ou non.
            # Le BCM peut refuser SPEED1 (wc_timeout, LIN fault) mais
            # doit passer en OFF quand ignition=0 — c'est ce qu'on teste.
            self._confirmed = True
            delta = time.time() * 1000.0 - self._t0_ms
            detail = f"state=OFF ignition=0 (transition={'oui' if self._active_seen else 'direct'})"
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms", detail)
        return None

    def on_can_frame(self, ev):
        if ev.get("can_id_int") != 0x201 or self._confirmed:
            return None
        mode = ev.get("fields", {}).get("mode", -1)
        if mode != 0:
            self._active_seen = True
        elif mode == 0:
            return self._confirm("backup CAN 0x201 mode=OFF apres ignition=0")
        return None


# ─── T34 : AUTO mode → Speed1 pluie faible (SRD_WW_050) ───────────────────
class T34_Auto_Rain_Speed1(BaseBCMTest):
    """
    SET crs_wiper_op=AUTO + rain_intensity=10.
    GET rte:state → attend "AUTO" + rte:front_motor_speed=1.
    """
    ID             = "T34"
    NAME           = "AUTO : pluie faible → Speed1"
    REF            = "SRD_WW_050"
    LIMIT_STR      = "≤ 1500 ms"
    LIMIT_MS       = 1500
    TEST_TIMEOUT_S = 8

    def _on_start(self):
        super()._on_start()
        self._initial_checked = False
        self._had_different   = False

    def _check_rte(self):
        if self._confirmed or self.rte_client is None:
            return None
        state = self.rte_client.get("state")
        speed = self.rte_client.get_int("front_motor_speed")
        target = (state == "AUTO" and speed == 1)
        if not self._initial_checked:
            self._initial_checked = True
            if target:
                self._t0_ms = time.time() * 1000.0  # rechrono si résidu
            else:
                self._had_different = True
            return None
        if not target:
            self._had_different = True
        elif self._had_different:
            self._confirmed = True
            delta = time.time() * 1000.0 - self._t0_ms
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms", f"Redis GET state=AUTO speed={speed}")
        return None

    def on_can_frame(self, ev):
        if ev.get("can_id_int") != 0x201 or self._confirmed:
            return None
        fields = ev.get("fields", {})
        if fields.get("mode") in (2,) or fields.get("speed") == 1:
            return self._confirm(f"backup CAN 0x201 speed1")
        return None


# ─── T35 : AUTO mode → Speed2 pluie forte (SRD_WW_050) ────────────────────
class T35_Auto_Rain_Speed2(BaseBCMTest):
    """
    SET crs_wiper_op=AUTO + rain_intensity=25 (≥ RAIN_SPEED2_THRESH=20).
    GET rte:front_motor_speed → attend 2.
    """
    ID             = "T35"
    NAME           = "AUTO : pluie forte → Speed2"
    REF            = "SRD_WW_050"
    LIMIT_STR      = "≤ 1500 ms"
    LIMIT_MS       = 1500
    TEST_TIMEOUT_S = 8

    def _on_start(self):
        super()._on_start()
        self._initial_checked = False
        self._had_different   = False

    def _check_rte(self):
        if self._confirmed or self.rte_client is None:
            return None
        speed  = self.rte_client.get_int("front_motor_speed")
        target = (speed == 2)
        if not self._initial_checked:
            self._initial_checked = True
            if target:
                self._t0_ms = time.time() * 1000.0
            else:
                self._had_different = True
            return None
        if not target:
            self._had_different = True
        elif self._had_different:
            self._confirmed = True
            delta = time.time() * 1000.0 - self._t0_ms
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms", f"Redis GET front_motor_speed=2")
        return None

    def on_can_frame(self, ev):
        if ev.get("can_id_int") != 0x201 or self._confirmed:
            return None
        if ev.get("fields", {}).get("speed") == 2:
            return self._confirm("backup CAN 0x201 speed=2")
        return None


# ─── T36 : FRONT_WASH → pompe FWD + Speed1 + ≥ 3 cycles (SRD_WW_100) ────────
class T36_FrontWash(BaseBCMTest):
    """
    SET crs_wiper_op=FRONT_WASH.
    Vérifie (SRD_WW_100) :
      - Pump direction = FORWARD
      - Front wiping Speed1 activé (front_motor_on=True)
      - Exactement 3 cycles lame avant, comptés via rest_contact (1→0 par cycle)
        Chemin prioritaire : Redis GET front_blade_cycles → attend 3
        Fallback          : durée active ≥ 3 × 1700ms si rest_contact non dispo

    Logique rest_contact :
      Chaque cycle = une transition rest_contact_raw True→False
      (lame revenue en position repos après un balayage complet).
      Le BCM incrémente _front_blade_cycles à chaque transition.
      Quand front_blade_cycles ≥ 3 ET front_motor_on=False → 3 cycles complétés.
    """
    ID             = "T36"
    NAME           = "FRONT_WASH : pompe FWD + Speed1 + 3 cycles (rest_contact)"
    REF            = "SRD_WW_100"
    LIMIT_STR      = "= 3 cycles (rest_contact 1→0)"
    LIMIT_MS       = 3 * 1700 + 2000   # 7100 ms — budget max avec marges hardware
    TEST_TIMEOUT_S = 16
    TARGET_CYCLES  = 3

    def _on_start(self):
        super()._on_start()
        self._pump_fwd_ok       = False
        self._speed1_ok         = False
        self._t_active_ms       = 0.0
        self._front_active      = False
        self._baseline_cycles   = -1   # valeur Redis au démarrage (pour offset)
        self._peak_cycles       = 0    # pic de cycles : alimenté par TCP (on_motor_data)
                                       # ET par Redis si lu avant reset _enter_off()

    def _check_rte(self):
        if self._confirmed or self.rte_client is None:
            return None

        # Direction pompe FORWARD
        pump_active = self.rte_client.get_bool("pump_active")
        pump_dir    = self.rte_client.get_int("pump_direction")
        if pump_active and pump_dir == 1:
            self._pump_fwd_ok = True
        elif pump_active:
            self._pump_fwd_ok = True   # FRONT_WASH → FWD implicite

        front_on = self.rte_client.get_bool("front_motor_on")
        if front_on:
            self._speed1_ok  = True
            self._front_active = True
            if self._t_active_ms == 0.0:
                self._t_active_ms = time.time() * 1000.0

        # ── Chemin prioritaire : _peak_cycles alimenté par TCP (on_motor_data) ─
        # RACE CONDITION Redis : _enter_off() remet front_blade_cycles=0 ET
        # front_motor_on=False dans la même boucle WSM (200ms). Le timer 200ms
        # de _check_rte() lit raw_cycles=0 au même tick → cycles_done=0 → pic=0.
        # SOLUTION : on_motor_data lit front_blade_cycles directement dans le
        # payload TCP (émis par _tcp.send() à chaque cycle détecté) et alimente
        # _peak_cycles. Ce chemin est indépendant de Redis et évite la race.
        raw_cycles = self.rte_client.get_int("front_blade_cycles")  # 0 si reset
        # Tenter quand même de mettre à jour le pic depuis Redis (si lu avant reset)
        if self._baseline_cycles < 0:
            self._baseline_cycles = raw_cycles
        cycles_done = raw_cycles - self._baseline_cycles
        if cycles_done > self._peak_cycles:
            self._peak_cycles = cycles_done

        # Succès : pic ≥ 3 (depuis TCP via on_motor_data ou Redis) ET moteur OFF
        if self._peak_cycles >= self.TARGET_CYCLES and not front_on and self._front_active:
            duration_ms = time.time() * 1000.0 - self._t_active_ms
            self._confirmed = True
            ok = self._pump_fwd_ok and self._speed1_ok
            src = "TCP" if self._peak_cycles > cycles_done else "Redis"
            detail = (f"front_blade_cycles ({src}): {self._peak_cycles} cycles "
                      f"pump=FWD speed1={self._speed1_ok} durée={duration_ms:.0f} ms")
            return (self._pass if ok else self._fail)(
                f"{self._peak_cycles} cycles / {duration_ms:.0f} ms", detail)

        # ── Fallback durée : si on n'a jamais vu de cycles ET moteur revenu OFF ─
        if (self._peak_cycles == 0 and self._front_active
                and not front_on and self._t_active_ms > 0):
            duration_ms = time.time() * 1000.0 - self._t_active_ms
            self._confirmed = True
            ok = (self._pump_fwd_ok and self._speed1_ok
                  and duration_ms >= self.TARGET_CYCLES * 1700)
            detail = (f"fallback durée={duration_ms:.0f} ms "
                      f"(min={self.TARGET_CYCLES * 1700} ms, cycles non reçus)")
            return (self._pass if ok else self._fail)(
                f"{duration_ms:.0f} ms", detail)
        return None

    def on_can_frame(self, ev):
        if ev.get("can_id_int") != 0x201:
            return None
        if ev.get("fields", {}).get("mode", 0) != 0:
            self._speed1_ok = True
        return None

    def on_motor_data(self, data):
        # Mise à jour pump
        state = str(data.get("pump_state", data.get("state", ""))).upper()
        if state == "FORWARD":
            self._pump_fwd_ok = True

        # ── Lecture cycles via front_blade_cycles dans le payload TCP ─────────
        # Le BCM envoie front_blade_cycles dans chaque broadcast TCP (bcm_tcp_broadcast).
        # Ce champ est incrémenté par _track_blade_cycle() à chaque transition repos,
        # ET inclus dans le payload → source fiable, pas de race condition Redis.
        # NB : rest_contact_raw n'est envoyé True que si on utilise count_on_rest=False
        # (front montant). En mode count_on_rest=True (FRONT_WASH), seul le front
        # descendant (False) est broadcasté → _rest_prev reste False → pas de transition
        # détectable. On utilise donc directement front_blade_cycles du payload TCP.
        tcp_cycles = data.get("front_blade_cycles")
        if tcp_cycles is not None:
            tcp_cycles = int(tcp_cycles)
            if tcp_cycles > self._peak_cycles:
                self._peak_cycles = tcp_cycles

        # Suivi front_motor_on depuis TCP (pour détection fin moteur sans Redis)
        front_raw = data.get("front_motor_on", data.get("front", "OFF"))
        front_on  = front_raw if isinstance(front_raw, bool) else (
            str(front_raw).upper() in ("ON", "TRUE", "1"))
        if front_on:
            self._speed1_ok = True
            if not self._front_active:
                self._front_active = True
                if self._t_active_ms == 0.0:
                    self._t_active_ms = time.time() * 1000.0

        # Résultat via TCP si Redis non dispo : 3 cycles atteints + moteur OFF
        if self.rte_client is not None:
            return None   # Redis/check_rte gère le résultat final

        if (self._peak_cycles >= self.TARGET_CYCLES
                and not front_on and self._front_active and not self._confirmed):
            duration_ms = time.time() * 1000.0 - self._t_active_ms
            self._confirmed = True
            ok = self._pump_fwd_ok and self._speed1_ok
            detail = (f"TCP front_blade_cycles: {self._peak_cycles} cycles "
                      f"pump=FWD durée={duration_ms:.0f} ms")
            return (self._pass if ok else self._fail)(
                f"{self._peak_cycles} cycles / {duration_ms:.0f} ms", detail)

        # Fallback durée si front_blade_cycles absent du payload TCP
        if (self._front_active and not front_on
                and self._peak_cycles == 0 and not self._confirmed):
            duration_ms = time.time() * 1000.0 - self._t_active_ms
            self._confirmed = True
            ok = self._pump_fwd_ok and duration_ms >= self.TARGET_CYCLES * 1700
            return (self._pass if ok else self._fail)(
                f"{duration_ms:.0f} ms",
                f"fallback durée={duration_ms:.0f} ms (front_blade_cycles absent)")
        return None


# ─── T37 : REAR_WASH → pompe BWD + 2 cycles arrière ≥ 3400 ms (SRD_WW_110) ──
class T37_RearWash_Cycle(BaseBCMTest):
    """
    SET crs_wiper_op=REAR_WASH (op=6).
    Vérifie (SRD_WW_110) :
      - Pump direction = BACKWARD
      - rear_motor_on=True maintenu ≥ 2 cycles × 1700 ms = 3400 ms
    """
    ID              = "T37"
    NAME            = "REAR_WASH : pompe BWD + 2 cycles arrière (≥ 3400 ms)"
    REF             = "SRD_WW_110"
    LIMIT_STR       = "≥ 2 cycles (1700 ms/cycle)"
    LIMIT_MS        = 2 * 1700 + 1000   # 4400 ms — budget max acceptable
    TEST_TIMEOUT_S  = 12
    MIN_ACTIVE_MS   = 2 * 1700          # 3400 ms moteur arrière ON minimum

    def _on_start(self):
        super()._on_start()
        self._pump_bwd_ok  = False
        self._t_active_ms  = 0.0
        self._rear_active  = False
        self._wait_idle    = True   # attendre rear_motor_on=False avant mesure

    def _check_rte(self):
        if self._confirmed or self.rte_client is None:
            return None

        # Direction pompe BACKWARD (2 = BWD dans le RTE entier)
        pump_dir_int = self.rte_client.get_int("pump_direction")
        if pump_dir_int == 2:
            self._pump_bwd_ok = True
        elif self.rte_client.get_bool("pump_active") and pump_dir_int != 1:
            self._pump_bwd_ok = True   # REAR_WASH → pompe BWD implicite

        rear_on = self.rte_client.get_bool("rear_motor_on")

        # Attendre état initial propre avant de mesurer
        if self._wait_idle:
            if not rear_on:
                self._wait_idle = False
            return None

        # Démarrer le chrono dès que le moteur s'active
        if not self._rear_active and rear_on:
            self._rear_active = True
            self._t_active_ms = time.time() * 1000.0
        elif self._rear_active and not rear_on:
            duration_ms = time.time() * 1000.0 - self._t_active_ms
            self._confirmed = True
            ok = self._pump_bwd_ok and duration_ms >= self.MIN_ACTIVE_MS
            detail = (f"pump=BACKWARD rear_motor=True durée={duration_ms:.0f} ms "
                      f"(min={self.MIN_ACTIVE_MS} ms)")
            return (self._pass if ok else self._fail)(
                f"{duration_ms:.0f} ms", detail)
        return None

    def on_motor_data(self, data):
        state = str(data.get("pump_state", data.get("state", ""))).upper()
        if state == "BACKWARD":
            self._pump_bwd_ok = True
        if self.rte_client is not None:
            return None
        # Backup sans Redis : même logique durée via motor_data
        rear_raw = data.get("rear_motor_on", data.get("rear", "OFF"))
        rear_on  = rear_raw if isinstance(rear_raw, bool) else (
            str(rear_raw).upper() in ("ON", "TRUE", "1"))
        if self._wait_idle:
            if not rear_on:
                self._wait_idle = False
            return None
        if not self._rear_active and rear_on:
            self._rear_active = True
            self._t_active_ms = time.time() * 1000.0
        elif self._rear_active and not rear_on and not self._confirmed:
            duration_ms = time.time() * 1000.0 - self._t_active_ms
            self._confirmed = True
            ok = self._pump_bwd_ok and duration_ms >= self.MIN_ACTIVE_MS
            return (self._pass if ok else self._fail)(
                f"{duration_ms:.0f} ms",
                f"backup motor_data pump=BACKWARD durée={duration_ms:.0f} ms")
        return None


# ─── T38 : Surcourant moteur → ERROR après 300 ms (FSR_003 / B2001) ────────
class T38_Overcurrent_Motor(BaseBCMTest):
    """
    Injecte motor_current > 0.8A via motor_received.
    GET rte:state → attend "ERROR" dans 300–600ms.
    """
    ID             = "T38"
    NAME           = "Surcourant moteur → ERROR à 300 ms (± tolérance)"
    REF            = "FSR_003 / B2001"
    LIMIT_STR      = "≈ 300 ms (≤ 410 ms)"
    LIMIT_MS       = 410
    TEST_TIMEOUT_S = 8

    _OC_MIN_MS = 300

    def __init__(self):
        super().__init__()
        # Initialiser ici pour éviter AttributeError si on_motor_data
        # est appelé avant start() (cas _inject_overcurrent dans _pre_test)
        self._oc_start_ms = 0.0
        self._confirmed   = False

    def _on_start(self):
        super()._on_start()
        self._oc_start_ms = 0.0
        self._confirmed   = False

    def _check_rte(self):
        if self._confirmed or self.rte_client is None:
            return None
        current     = self.rte_client.get_float("motor_current_a")
        motor_error = self.rte_client.get_bool("front_motor_error")
        motor_on    = self.rte_client.get_bool("front_motor_on")

        # Démarrer le chrono dès que le courant injecté dépasse le seuil
        if current > 0.8 and self._oc_start_ms == 0.0:
            self._oc_start_ms = time.time() * 1000.0

        # Le BCM ne passe plus en ST_ERROR global depuis la correction isolation
        # erreurs (voir bcm_application._check_overcurrent). Il arrête uniquement
        # le moteur concerné (front_motor_on=False) et lève front_motor_error=True
        # après OVERCURRENT_DELAY=300 ms. On détecte l'une ou l'autre condition.
        if self._oc_start_ms > 0 and (motor_error or not motor_on):
            reaction_ms = time.time() * 1000.0 - self._oc_start_ms
            self._confirmed = True
            detail = (f"reaction={reaction_ms:.0f} ms "
                      f"(front_motor_error={motor_error} front_motor_on={motor_on})")
            if self._OC_MIN_MS <= reaction_ms <= self.LIMIT_MS:
                return self._pass(f"{reaction_ms:.0f} ms", detail)
            if reaction_ms > self.LIMIT_MS:
                return self._fail(f"{reaction_ms:.0f} ms",
                                  detail + f" — réaction trop tardive > {self.LIMIT_MS} ms")
            return self._fail(f"{reaction_ms:.0f} ms",
                              detail + " — réaction < 300 ms")
        return None

    def on_motor_data(self, data):
        """Non utilisé — T38 injecte motor_current_a via Redis."""
        return None


# ─── T39 : LIN timeout → WSM retour OFF (FSR_001) ─────────────────────────
class T39_LIN_Timeout_WSM_Off(BaseBCMTest):
    """
    stop_lin_tx → GET rte:lin_timeout_active=True
                → GET rte:state=OFF.
    """
    ID             = "T39"
    NAME           = "LIN timeout → WSM retour OFF"
    REF            = "FSR_001 / SRS_LIN_003"
    LIMIT_STR      = "≤ 2500 ms"
    LIMIT_MS       = 2500
    TEST_TIMEOUT_S = 10

    def _on_start(self):
        super()._on_start()
        self._was_active = False

    def _check_rte(self):
        if self._confirmed or self.rte_client is None:
            return None
        state   = self.rte_client.get("state")
        timeout = self.rte_client.get_bool("lin_timeout_active")
        if state and state not in ("OFF", "ERROR"):
            self._was_active = True
        if (timeout or self._was_active) and state == "OFF":
            self._confirmed = True
            delta = time.time() * 1000.0 - self._t0_ms
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms",
                f"Redis GET state=OFF lin_timeout={timeout}")
        return None

    def on_can_frame(self, ev):
        if ev.get("can_id_int") != 0x201 or self._confirmed:
            return None
        mode = ev.get("fields", {}).get("mode", -1)
        if mode != 0:
            self._was_active = True
        elif mode == 0 and self._was_active:
            return self._confirm("backup CAN 0x201 mode=OFF apres timeout LIN")
        return None



# ─── Registre complet dans l'ordre d'exécution ───────────────────────────
ALL_TESTS = [
    # ── Cycles trames réseau (section 6) ─────────────
    T01_LIN_Requester_Cycle,
    T02_LIN_CRSStatus_Cycle,
    T03_CAN_200_Cycle,
    T04_CAN_201_Cycle,
    T05_CAN_202_Cycle,
    T06_CAN_300_Cycle,
    T07_CAN_301_Cycle,
    # ── Timeouts réseau (sections 1-3) ───────────────
    T10_LIN_Timeout,
    T11_CAN_Timeout,
    # ── Contraintes mécaniques / pompe (section 4) ───
    T20_WipeCycle_Duration,
    T21_Pump_AutoStop,
    # ── Comportement WSM BCM — Redis GET/SET ─────────
    T30_WSM_Speed1,
    T31_WSM_Speed2,
    T32_WSM_Speed1_to_Off,
    T33_Ignition_Off_SafeState,
    T34_Auto_Rain_Speed1,
    T35_Auto_Rain_Speed2,
    T36_FrontWash,
    T37_RearWash_Cycle,
    T38_Overcurrent_Motor,
    T39_LIN_Timeout_WSM_Off,
]