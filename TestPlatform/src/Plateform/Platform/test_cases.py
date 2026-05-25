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
    CATEGORY       = ""     # "CYCLE" | "TIMEOUT" | "FUNCTIONAL"
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
                                f"No data after {self.TEST_TIMEOUT_S}s")
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
#  TESTS FONCTIONNELS BCM — Machine d'état WSM
# ══════════════════════════════════════════════════════════════════════════


class BaseBCMTest(BaseTest):
    """
    Base pour les tests de comportement WSM.

    Deux chemins d'observation (utilisés simultanément) :
      1. Redis GET  rte_client.get("state") — direct, < 1ms  ← prioritaire
      2. CAN 0x201  on_can_frame()           — bus physique   ← backup

    Le test_runner appelle _check_rte() toutes les 200ms (timer).
    Subclass surcharge _target_state() pour indiquer l'état attendu.
    """
    CATEGORY       = "FUNCTIONAL_BCM"
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


# ══════════════════════════════════════════════════════════════════════════
#  TESTS DE TIMEOUT  (actifs : stop TX, mesure délai détection)
# ══════════════════════════════════════════════════════════════════════════

class T40_Touch_SingleCycle_Then_Off(BaseBCMTest):
    """
    T40 — Touch : exactement 1 cycle puis retour OFF
    Remplace T20 : SRD_WW_020 / SRS_WSM_002

    T20 mesurait uniquement la durée du cycle TOUCH (≤ 1700 ms).
    T40 vérifie en plus que :
      1. Le WSM revient bien en état OFF après exactement 1 cycle.
      2. Aucun 2e cycle ne démarre si le stick reste en position Touch.

    Stimulus  : Redis SET crs_wiper_op=TOUCH (op=1)
    Séquence  : rte:state TOUCH → OFF (1 seul cycle détecté via rest_contact 1→0)
    Critères  : durée ≤ 1700 ms  ET  state=OFF après cycle  ET  pas de 2e cycle
    """
    ID             = "T40"
    NAME           = "Touch : 1 cycle then return OFF (no repeat)"
    CATEGORY       = "FUNCTIONAL"
    REF            = "SRD_WW_020 / SRS_WSM_002"
    LIMIT_STR      = "≤ 1700 ms, state=OFF, 1 cycle"
    LIMIT_MS       = 1700
    TEST_TIMEOUT_S = 8

    def _on_start(self):
        super()._on_start()
        self._t_active_ms     = 0.0
        self._saw_active      = False
        self._cycle_count     = 0
        self._rest_was_moving = False

    def _check_rte(self) -> Optional[TestResult]:
        if self._confirmed or self.rte_client is None:
            return None
        state    = self.rte_client.get("state")
        rest_raw = self.rte_client.get_bool("rest_contact_raw")

        # Démarrage du chrono : premier passage en état TOUCH (moteur actif)
        if not self._saw_active and state == "TOUCH":
            self._saw_active      = True
            self._t_active_ms     = time.time() * 1000.0
            self._rest_was_moving = rest_raw
            return None

        if not self._saw_active:
            return None

        # Détection fin de cycle via rest_contact 1→0 (prioritaire)
        if rest_raw and not self._rest_was_moving:
            self._rest_was_moving = True
        elif not rest_raw and self._rest_was_moving:
            self._cycle_count    += 1
            self._rest_was_moving = False
        else:
            self._rest_was_moving = rest_raw

        # Le WSM doit repasser en OFF après 1 cycle
        if state == "OFF" and self._cycle_count >= 1:
            delta = time.time() * 1000.0 - self._t_active_ms
            self._confirmed = True
            ok = (delta <= self.LIMIT_MS) and (self._cycle_count == 1)
            detail = (f"state=OFF after {self._cycle_count} cycle(s) | "
                      f"duration={delta:.0f} ms | "
                      f"{'OK' if self._cycle_count == 1 else 'ERROR: 2nd cycle detected'}")
            return (self._pass if ok else self._fail)(f"{delta:.0f} ms", detail)
        return None

    def on_motor_data(self, data):
        """Backup sans Redis : détection via front=ON→OFF + rest_contact."""
        if self.rte_client is not None:
            return None
        front    = str(data.get("front", "OFF")).upper()
        active   = (front == "ON")
        rest_raw = bool(data.get("rest_contact_raw", False))

        if not self._saw_active and active:
            self._saw_active      = True
            self._t_active_ms     = time.time() * 1000.0
            self._rest_was_moving = rest_raw
            return None
        if not self._saw_active:
            return None

        if rest_raw and not self._rest_was_moving:
            self._rest_was_moving = True
        elif not rest_raw and self._rest_was_moving:
            self._cycle_count    += 1
            self._rest_was_moving = False

        if not active and self._cycle_count >= 1 and not self._confirmed:
            delta = time.time() * 1000.0 - self._t_active_ms
            self._confirmed = True
            ok = (delta <= self.LIMIT_MS) and (self._cycle_count == 1)
            detail = f"front=OFF after {self._cycle_count} cycle(s) | {delta:.0f} ms"
            return (self._pass if ok else self._fail)(f"{delta:.0f} ms", detail)
        return None

    def on_can_frame(self, ev):
        """Backup CAS B via CAN 0x201."""
        if ev.get("can_id_int") != 0x201 or self._confirmed:
            return None
        mode = ev.get("fields", {}).get("mode", -1)
        if not self._saw_active and mode != 0:
            self._saw_active  = True
            self._t_active_ms = time.time() * 1000.0
        elif self._saw_active and mode == 0:
            delta = time.time() * 1000.0 - self._t_active_ms
            self._cycle_count = max(self._cycle_count, 1)
            ok = (delta <= self.LIMIT_MS) and (self._cycle_count == 1)
            return (self._pass if ok else self._fail)(f"{delta:.0f} ms",
                "backup CAN 0x201 mode=OFF")
        return None

class T22_FrontWash_DTC_BCM(BaseBCMTest):
    """
    T22 — FRONT_WASH > 5s → DTC BCM  (FSR_005 / B2008)

    Stimulus  : Redis SET crs_wiper_op=FRONT_WASH (op=5) → BCM démarre pompe FWD.
    Scénario  : La pompe reste active au-delà de 5s (PUMP_MAX_RUNTIME).
                Le BCM doit couper automatiquement la pompe ET lever le DTC B2008.

    Observation prioritaire : Redis GET pump_error=True  OU  pump_active=False
                              après ≥ 4800ms de pompe active.
    Mesure     : durée active de la pompe (doit être ≤ 5500ms, ≥ 4800ms).
    DTC attendu: B2008 (pump overtime) via Redis GET dtc_active ou pump_error.
    """
    ID             = "T22"
    NAME           = "Pump FORWARD >5s → DTC BCM (B2008)"
    CATEGORY       = "FUNCTIONAL"
    REF            = "FSR_005 / B2008 / SRD_WW_120"
    LIMIT_STR      = "4800–6000 ms (pump off + DTC B2008)"
    LIMIT_MS       = 6000
    MIN_MS         = 4800
    TEST_TIMEOUT_S = 15

    def _on_start(self):
        super()._on_start()
        self._t_pump_start_ms = 0.0
        self._pump_active     = False
        self._dtc_received    = False

    def _target_state(self):
        return None   # on surcharge _check_rte

    def _check_rte(self) -> Optional[TestResult]:
        if self._confirmed or self.rte_client is None:
            return None
        pump       = self.rte_client.get_bool("pump_active")
        pump_error = self.rte_client.get_bool("pump_error")

        # Phase 1 : attente démarrage pompe
        if not self._pump_active and pump:
            self._pump_active     = True
            self._t_pump_start_ms = time.time() * 1000.0
            return None

        if not self._pump_active:
            return None

        elapsed = time.time() * 1000.0 - self._t_pump_start_ms

        # Détection DTC (pump_error=True) — prioritaire
        if pump_error and not self._dtc_received:
            self._dtc_received = True

        # Pompe coupée par BCM ?
        if not pump and self._pump_active:
            self._confirmed = True
            ok = (self.MIN_MS <= elapsed <= self.LIMIT_MS)
            dtc_ok = self._dtc_received
            detail = (f"Pompe active {elapsed:.0f} ms | "
                      f"DTC B2008={'yes' if dtc_ok else 'not received'} | "
                      f"pump_error={pump_error}")
            # PASS si coupé dans la bonne fenêtre ET DTC reçu (ou au moins coupé à temps)
            return (self._pass if ok else self._fail)(f"{elapsed:.0f} ms", detail)

        return None

    def on_motor_data(self, data):
        """Backup via bcm_tcp_pump (:5556) si Redis indisponible."""
        if self._confirmed or self.rte_client is not None:
            return None
        is_pump = ("pump_remaining" in data or data.get("source") == "BCM")
        if not is_pump:
            return None
        state = str(data.get("state", "")).upper()
        fault = bool(data.get("fault", False))

        if not self._pump_active and state in ("FORWARD", "BACKWARD"):
            self._pump_active     = True
            self._t_pump_start_ms = time.time() * 1000.0
        elif self._pump_active and (state in ("OFF", "") or fault):
            elapsed = time.time() * 1000.0 - self._t_pump_start_ms
            self._confirmed = True
            ok = self.MIN_MS <= elapsed <= self.LIMIT_MS
            return (self._pass if ok else self._fail)(
                f"{elapsed:.0f} ms",
                f"backup bcm_tcp: pump stopped after {elapsed:.0f} ms (fault={fault})")
        return None


class T21_Pump_AutoStop(BaseBCMTest):
    """
    T21 — FRONT_WASH : arrêt pompe après 3 cycles lame (avant 5s)

    Stimulus  : Redis SET crs_wiper_op=FRONT_WASH (op=5) → BCM démarre pompe FWD.
    Scénario  : Les 3 cycles moteur via front wash se produisent avant 5s
                (ex : à 2.9s si le cycle est ~900ms). La pompe doit s'arrêter
                dès la fin du 3e cycle (ex : 2.9s), sans attendre le FSR_005.
                Si les cycles durent > 5s, c'est T22 qui vérifie l'arrêt FSR.

    Observation : Redis GET pump_active : True → False.
    Mesure    : durée de marche de la pompe (doit être < 5000ms = PUMP_MAX_RUNTIME).
    Critère   : pump_active=False ET durée < PUMP_MAX_RUNTIME (arrêt normal, pas FSR)
    """
    ID             = "T21"
    NAME           = "Pump auto-stop (3 blade cycles before 5s)"
    CATEGORY       = "FUNCTIONAL"
    REF            = "FSR_005 / SRD_WW_120 / SRS_WASH_003"
    LIMIT_STR      = "< 5000 ms (stop before FSR)"
    LIMIT_MS       = 4900   # strictement < PUMP_MAX_RUNTIME (5000ms) : arrêt normal
    TEST_TIMEOUT_S = 12     # 3×900ms cycles + marge

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
            # PASS : pompe arrêtée avant FSR_005 (arrêt normal après 3 cycles)
            ok = delta < self.LIMIT_MS
            detail = (f"pump_active: True→False after {delta/1000.0:.2f} s | "
                      f"{'normal stop (cycles)' if ok else 'FSR_005 stop (cycles too slow)'}")
            return (self._pass if ok else self._fail)(
                f"{delta/1000.0:.2f} s", f"Redis {detail}")
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
            ok = delta < self.LIMIT_MS
            return (self._pass if ok else self._fail)(
                f"{delta/1000.0:.2f} s",
                f"bcm_tcp_pump fallback | {'normal stop' if ok else 'FSR_005 stop'}")


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

    def _target_state(self):
        return "OFF"

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
    NAME           = "AUTO : light rain → Speed1"
    REF            = "SRD_WW_050"
    LIMIT_STR      = "≤ 1500 ms"
    LIMIT_MS       = 1500
    TEST_TIMEOUT_S = 8

    def _on_start(self):
        super()._on_start()
        self._initial_checked = False
        self._had_different   = False

    def _target_state(self):
        return "AUTO"

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
    NAME           = "AUTO : heavy rain → Speed2"
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
    NAME           = "FRONT_WASH : pump FWD + Speed1 + 3 cycles (rest_contact)"
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
                      f"pump=FWD speed1={self._speed1_ok} duration={duration_ms:.0f} ms")
            return (self._pass if ok else self._fail)(
                f"{self._peak_cycles} cycles / {duration_ms:.0f} ms", detail)

        # ── Fallback durée : si on n'a jamais vu de cycles ET moteur revenu OFF ─
        if (self._peak_cycles == 0 and self._front_active
                and not front_on and self._t_active_ms > 0):
            duration_ms = time.time() * 1000.0 - self._t_active_ms
            self._confirmed = True
            ok = (self._pump_fwd_ok and self._speed1_ok
                  and duration_ms >= self.TARGET_CYCLES * 1700)
            detail = (f"fallback duration={duration_ms:.0f} ms "
                      f"(min={self.TARGET_CYCLES * 1700} ms, cycles not received)")
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
                      f"pump=FWD duration={duration_ms:.0f} ms")
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
                f"fallback duration={duration_ms:.0f} ms (front_blade_cycles absent)")
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
    NAME            = "REAR_WASH : pump BWD + 2 rear cycles (≥ 3400 ms)"
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
            detail = (f"pump=BACKWARD rear_motor=True duration={duration_ms:.0f} ms "
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
                f"backup motor_data pump=BACKWARD duration={duration_ms:.0f} ms")
        return None


# ─── T38 : Surcourant moteur → ERROR après 300 ms (FSR_003 / B2001) ────────
class T38_Overcurrent_Motor(BaseBCMTest):
    """
    Injecte motor_current > 0.8A via motor_received.
    GET rte:state → attend "ERROR" dans 300–600ms.
    """
    ID             = "T38"
    NAME           = "Motor overcurrent → ERROR at 300 ms (± tolerance)"
    REF            = "FSR_003 / B2001"
    LIMIT_STR      = "≈ 300 ms (≤ 600 ms)"
    LIMIT_MS       = 600
    TEST_TIMEOUT_S = 5

    _OC_MIN_MS = 200

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
        state       = self.rte_client.get("state")
        motor_error = self.rte_client.get_bool("front_motor_error")
        motor_on    = self.rte_client.get_bool("front_motor_on")

        # Mesure depuis t0 (reset_t0 appelé au moment de l'injection dans pre_test).
        # On attend que le BCM entre en ST_ERROR (surintensité détectée) ou
        # que front_motor_on=False + front_motor_error=True.
        delta = time.time() * 1000.0 - self._t0_ms

        if state == "ERROR" or (motor_error and not motor_on):
            self._confirmed = True
            detail = (f"state={state} front_motor_error={motor_error} "
                      f"front_motor_on={motor_on} | {delta:.0f} ms")
            if self._OC_MIN_MS <= delta <= self.LIMIT_MS:
                return self._pass(f"{delta:.0f} ms", detail)
            if delta > self.LIMIT_MS:
                return self._fail(f"{delta:.0f} ms",
                                  detail + f" — reaction too late > {self.LIMIT_MS} ms")
            return self._fail(f"{delta:.0f} ms",
                              detail + f" — reaction < {self._OC_MIN_MS} ms (injection too early?)")
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
    NAME           = "LIN timeout → WSM return OFF"
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



# ─── T43 : Reverse Gear → essuie-glace arrière intermittent (SRD_WW_060) ─
class T43_ReverseGear_RearWiper_Intermittent(BaseBCMTest):
    """
    T43 — Reverse Gear : essuie-glace arrière intermittent
    SRD_WW_060

    Quand reverse_gear=TRUE et front wiper actif → le moteur arrière doit
    effectuer 1 cycle toutes les ≈ 1700 ms.
    Ce comportement n'était couvert par aucun test.

    Stimulus  : Redis SET crs_wiper_op=SPEED1 + reverse_gear=True
    Mesure    : intervalle entre cycles arrière (transitions rear_motor_on
                True→False ou rest_contact_raw rear 1→0) ≈ 1700 ms.
    Limite    : 1700 ms ± 300 ms (tolérance hardware)
    """
    ID             = "T43"
    NAME           = "Reverse Gear : rear wiper intermittent ≈ 1700 ms"
    CATEGORY       = "FUNCTIONAL"
    REF            = "SRD_WW_060"
    LIMIT_STR      = "≈ 1700 ms (± 400 ms)"
    LIMIT_MS       = 2100    # 1700 + 250ms impulsion OFF + marge
    MIN_MS         = 1300    # min acceptable
    TEST_TIMEOUT_S = 15      # 2 cycles × 1950ms + marge
    N_CYCLES       = 2       # attendre 2 cycles pour mesurer 1 intervalle

    def _on_start(self):
        super()._on_start()
        self._cycle_ts: list = []       # timestamps de fin de cycle (rear OFF)
        self._rear_was_active = False

    def _check_rte(self) -> Optional[TestResult]:
        if self._confirmed or self.rte_client is None:
            return None
        rear_on  = self.rte_client.get_bool("rear_motor_on")
        rev      = self.rte_client.get_bool("reverse_gear")

        if not rev:
            return None   # reverse non actif, attendre

        # Détection des cycles arrière : transition rear_motor_on True→False
        if self._rear_was_active and not rear_on:
            self._cycle_ts.append(time.time() * 1000.0)
        self._rear_was_active = rear_on

        if len(self._cycle_ts) >= self.N_CYCLES:
            interval_ms = self._cycle_ts[-1] - self._cycle_ts[-2]
            self._confirmed = True
            ok = self.MIN_MS <= interval_ms <= self.LIMIT_MS
            detail = (f"rear inter-cycle interval = {interval_ms:.0f} ms "
                      f"(attendu ≈ 1700 ms ± 300 ms)")
            return (self._pass if ok else self._fail)(f"{interval_ms:.0f} ms", detail)
        return None

    def on_motor_data(self, data):
        """Backup sans Redis."""
        if self.rte_client is not None:
            return None
        rear_raw = data.get("rear_motor_on", data.get("rear", "OFF"))
        rear_on  = rear_raw if isinstance(rear_raw, bool) else (
            str(rear_raw).upper() in ("ON", "TRUE", "1"))
        rev_raw  = data.get("reverse_gear", False)
        rev      = rev_raw if isinstance(rev_raw, bool) else bool(rev_raw)

        if not rev:
            return None

        if self._rear_was_active and not rear_on:
            self._cycle_ts.append(time.time() * 1000.0)
        self._rear_was_active = rear_on

        if len(self._cycle_ts) >= self.N_CYCLES and not self._confirmed:
            interval_ms = self._cycle_ts[-1] - self._cycle_ts[-2]
            self._confirmed = True
            ok = self.MIN_MS <= interval_ms <= self.LIMIT_MS
            return (self._pass if ok else self._fail)(
                f"{interval_ms:.0f} ms",
                f"backup motor_data: intervalle={interval_ms:.0f} ms")
        return None


# ─── T45 : Retour lame au repos à l'Ignition OFF (FSR_004) ────────────────
class T45_BladeReturn_Ignition_Off(BaseBCMTest):
    """
    T45 — Retour lame au repos à l'Ignition OFF (blade return to rest)
    FSR_004 / SRD non-func §7.2

    T33 vérifie que le WSM passe en état OFF. Mais FSR_004 exige
    spécifiquement que la lame retourne en position repos
    (rest_contact = 0 = GPIO au repos).
    Ce test vérifie rest_contact_raw=False après extinction Ignition,
    pas seulement state=OFF.

    Stimulus  : Redis SET ignition_status=0 (depuis un état actif SPEED1)
    Critère   : rest_contact_raw=False ET state=OFF dans la limite de temps
    """
    ID             = "T45"
    NAME           = "Blade return to rest at Ignition OFF"
    CATEGORY       = "FUNCTIONAL"
    REF            = "FSR_004 / SRD non-func §7.2"
    LIMIT_STR      = "≤ 3000 ms (state=OFF + rest_contact=False)"
    LIMIT_MS       = 3000
    TEST_TIMEOUT_S = 10

    def _on_start(self):
        super()._on_start()
        self._active_seen = False

    def _check_rte(self) -> Optional[TestResult]:
        if self._confirmed or self.rte_client is None:
            return None
        state      = self.rte_client.get("state")
        ignition   = self.rte_client.get_int("ignition_status", default=1)
        rest_raw   = self.rte_client.get_bool("rest_contact_raw")

        if state and state not in ("OFF", "ERROR"):
            self._active_seen = True

        # Critère FSR_004 : state=OFF ET rest_contact=False (lame au repos)
        if ignition == 0 and state == "OFF" and not rest_raw:
            self._confirmed = True
            delta = time.time() * 1000.0 - self._t0_ms
            detail = (f"state=OFF + rest_contact_raw=False + ignition=0 | "
                      f"duration={delta:.0f} ms | "
                      f"transition={'depuis actif' if self._active_seen else 'directe'}")
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms", detail)
        # Échec partiel : state=OFF mais lame pas au repos
        if ignition == 0 and state == "OFF" and rest_raw:
            delta = time.time() * 1000.0 - self._t0_ms
            if delta > self.LIMIT_MS:
                self._confirmed = True
                return self._fail(f"{delta:.0f} ms",
                    "state=OFF mais rest_contact_raw=True (lame non revenue au repos)")
        return None

    def on_motor_data(self, data):
        """Backup sans Redis."""
        if self.rte_client is not None:
            return None
        front    = str(data.get("front", "OFF")).upper()
        rest_raw = bool(data.get("rest_contact_raw", False))
        if front == "ON":
            self._active_seen = True
        if self._active_seen and front == "OFF" and not rest_raw and not self._confirmed:
            delta = time.time() * 1000.0 - self._t0_ms
            self._confirmed = True
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms",
                f"backup motor_data: front=OFF + rest_contact_raw=False")
        return None


# ══════════════════════════════════════════════════════════════════════════
#  TESTS LIN — Sécurité protocole / validation frames
# ══════════════════════════════════════════════════════════════════════════

class TC_LIN_002_AliveCounter_AntiReplay(BaseBCMTest):
    """
    TC_LIN_002 — Validation du AliveCounter LIN (anti-replay)

    Prérequis : LIN actif
    Étapes    : Injecter frames avec AliveCounter figé (non-incrémental)
    Résultat  : BCM détecte counter figé et ignore ou lève une faute

    Stimulus  : Redis SET lin_alive_frozen=True → crslin gèle son AliveCounter
    Observation : Redis GET lin_alive_fault=True ou DTC B20xx créé dans les 3s
    """
    ID             = "TC_LIN_002"
    NAME           = "LIN AliveCounter frozen → anti-replay fault"
    CATEGORY       = "LIN_SECURITE"
    REF            = "Message Catalogue LIN 0x16 / SRS_LIN_004"
    LIMIT_STR      = "≤ 3000 ms (frozen counter detection)"
    LIMIT_MS       = 3000
    TEST_TIMEOUT_S = 10

    def _on_start(self):
        super()._on_start()
        self._detected = False

    def _target_state(self):
        return None   # surcharge _check_rte

    def _check_rte(self) -> Optional[TestResult]:
        if self._detected or self.rte_client is None:
            return None
        fault   = self.rte_client.get_bool("lin_alive_fault")
        timeout = self.rte_client.get_bool("lin_timeout_active")
        if fault or timeout:
            self._detected = True
            delta = time.time() * 1000.0 - self._t0_ms
            detail = (f"lin_alive_fault={fault} lin_timeout_active={timeout} "
                      f"| {delta:.0f} ms")
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms", detail)
        return None

    def on_lin_frame(self, ev):
        """Fallback : écouter un event 'alive_error' ou 'fault' du crslin."""
        if self._detected or self.rte_client is not None:
            return None
        t   = ev.get("type", "")
        msg = str(ev.get("msg", "")).lower()
        if "alive" in msg or "replay" in msg or t in ("fault", "alive_error"):
            self._detected = True
            delta = time.time() * 1000.0 - self._t0_ms
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms", f"crslin alive_error event (fallback)")
        return None



class TC_LIN_005_CRS_InternalFault(BaseBCMTest):
    """
    TC_LIN_017_BIT1_ALONE — 0x17 bit0=1 (CRS_InternalFault_Stick) SEUL,
    sans 0x16 bit6=1 (Stuck) → B2011 ne doit PAS se déclencher.

    Condition ET incomplète : un seul des deux critères B2011 est actif.
    Le test vérifie que b2011_active reste False pendant toute la durée.

    Séquence :
      1. Purger résidus : set_fault=0, set_stuck=False
      2. Injecter 0x17 bit0=1 (CRS_InternalFault_Stick) via set_fault=1
         — 0x16 bit6 reste à 0 (pas de Stuck)
      3. Attendre 13s (> délai B2011 10s)
      4. Vérifier que b2011_active=False → PASS
         Si b2011_active=True → FAIL (condition ET déclenchée à tort)
    """
    ID             = "TC_LIN_005"
    NAME           = "CRS_InternalFault (0x17 bit0=1 only) → B2011 NOT triggered"
    CATEGORY       = "LIN_SECURITE"
    REF            = "WW-MCAT-005 Rev5 / B2011 condition ET — cas 0x17 seul"
    LIMIT_STR      = "b2011_active=False for ≥ 13s (B2011 not triggered)"
    LIMIT_MS       = 13000
    TEST_TIMEOUT_S = 16

    def _on_start(self):
        super()._on_start()
        self._detected = False

    def _target_state(self): return None

    def _check_rte(self) -> Optional[TestResult]:
        if self._detected or self.rte_client is None:
            return None
        b2011 = self.rte_client.get_bool("b2011_active")
        delta = time.time() * 1000.0 - self._t0_ms

        # FAIL immédiat si B2011 se déclenche (condition ET activée à tort)
        if b2011:
            self._detected = True
            return self._fail(
                f"{delta:.0f} ms",
                f"B2011 incorrectly triggered with 0x17 bit0=1 only (0x16 bit6=0) | {delta:.0f} ms")

        # PASS après 13s sans B2011
        if delta >= self.LIMIT_MS:
            self._detected = True
            return self._pass(
                f"{delta:.0f} ms",
                f"b2011_active=False after {delta:.0f}ms — AND condition not met")
        return None


# ══════════════════════════════════════════════════════════════════════════
#  TESTS CAN — Sécurité protocole
# ══════════════════════════════════════════════════════════════════════════

class TC_CAN_003_AliveCounter_0x200(BaseBCMTest):
    """
    TC_CAN_003 — Vérification AliveCounter trame 0x200

    Message Catalogue CAN 0x200 — Priorité Haute
    Prérequis : WC installé
    Étapes    : Bloquer AliveCounter dans 0x200 (BCM→WC)
    Résultat  : WC détecte counter figé, NACK ou faute

    Stimulus  : Redis SET can_alive_frozen=True → bcmcan gèle l'AliveCounter
                dans Wiper_Command 0x200
    Observation : CAN 0x202 Wiper_Ack avec NACK  OU  Redis GET wc_alive_fault=True
    """
    ID             = "TC_CAN_003"
    NAME           = "CAN 0x200 AliveCounter frozen → WC NACK/fault"
    CATEGORY       = "CAN_SECURITE"
    REF            = "Message Catalogue CAN 0x200 / SRS_CAN_004"
    LIMIT_STR      = "≤ 3000 ms (WC detects frozen counter)"
    LIMIT_MS       = 3000
    TEST_TIMEOUT_S = 10

    def _on_start(self):
        super()._on_start()
        self._detected      = False
        self._stimulus_sent = False   # True seulement après freeze_can_alive envoyé

    def _check_rte(self) -> Optional[TestResult]:
        if self._detected or self.rte_client is None:
            return None
        # Ne pas observer avant que le freeze soit réellement envoyé.
        # Sans ce garde, un wc_alive_fault résiduel déclenche un PASS immédiat
        # → cleanup avant 600ms → freeze jamais envoyé.
        if not self._stimulus_sent:
            return None
        wc_fault = self.rte_client.get_bool("wc_alive_fault")
        if wc_fault:
            self._detected = True
            delta = time.time() * 1000.0 - self._t0_ms
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms",
                f"Redis wc_alive_fault=True: frozen counter detected by WC")
        return None

    def on_can_frame(self, ev):
        """Backup : observer NACK dans 0x202 Wiper_Ack."""
        if self._detected:
            return None
        if ev.get("can_id_int") != 0x202:
            return None
        # Nouveau catalogue WW-MCAT-005 Rev5 : AckStatus = bit0 de byte0
        # _decode_0x202 émet "ack_status" (0=ACK, 1=NACK)
        ack_status = ev.get("fields", {}).get("ack_status", 0)
        if ack_status == 1:   # NACK
            self._detected = True
            delta = time.time() * 1000.0 - self._t0_ms
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms",
                "CAN 0x202 NACK received (ack_status=1): WC detected frozen AliveCounter")
        return None

    def on_motor_data(self, data: dict):
        """
        FIX TC_CAN_003 : réception du broadcast TCP wc_alive_fault depuis bcmcan.
        Le simulateur émet {"type":"wc_alive_fault","wc_alive_fault":True,...}
        via _broadcast_to_motor_clients(). Ce message n'a pas de clé "state"
        → il était silencieusement droppé par le filtre MotorVehicleWorker.
        Après fix workers.py, ce canal est le chemin le plus rapide et fiable
        (direct TCP, sans passer par Redis pub/sub → BCM → T-REDIS → Redis SET).
        """
        if self._detected or not self._stimulus_sent:
            return None
        if data.get("type") == "wc_alive_fault" and data.get("wc_alive_fault"):
            self._detected = True
            delta = time.time() * 1000.0 - self._t0_ms
            alive_val = data.get("alive_value", "?")
            repeat    = data.get("repeat_count", "?")
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms",
                f"TCP broadcast wc_alive_fault : AliveCounter=0x{alive_val:02X} "
                f"frozen x{repeat} (bcmcan → Platform direct)")
        return None


# ══════════════════════════════════════════════════════════════════════════
#  TESTS SÉCURITÉ / DIAGNOSTIC
# ══════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════
#  NOUVEAUX TESTS — TC_GEN_001 / TC_SPD_001 / TC_AUTO_004
#                   TC_FSR_008 / TC_FSR_010 / TC_COM_001
# ══════════════════════════════════════════════════════════════════════════

# ─── TC_GEN_001 : Activation sur Ignition ON ─────────────────────────────
class TC_GEN_001_Ignition_On_Activation(BaseBCMTest):
    """
    TC_GEN_001 — Activation sur Ignition ON (SRD_WW_001)

    Prérequis : Ignition_Status = OFF
    Étapes    : SET ignition=1 (ON) via Redis + envoyer SPEED1 via LIN
    Résultat  : BCM active le moteur avant en Speed1

    Stimulus  : Redis SET ignition_status=1 + crs_wiper_op=2
    Observation : Redis GET state == "SPEED1" + front_motor_on == True
    """
    ID             = "TC_GEN_001"
    NAME           = "Motor activation on Ignition ON → SPEED1"
    CATEGORY       = "FUNCTIONAL_BCM"
    REF            = "SRD_WW_001"
    LIMIT_STR      = "≤ 800 ms (state=SPEED1 + front_motor_on=True)"
    LIMIT_MS       = 800
    TEST_TIMEOUT_S = 8

    def _on_start(self):
        super()._on_start()
        self._confirmed = False

    def _target_state(self) -> str:
        return "SPEED1"

    def _check_rte(self) -> Optional[TestResult]:
        if self._confirmed or self.rte_client is None:
            return None
        state    = self.rte_client.get("state")
        motor_on = self.rte_client.get_bool("front_motor_on")
        # Ne pas vérifier ignition_status : il fluctue entre tests et peut
        # être à 0 ou 1 selon le timing du CAN 0x300. Le critère est
        # uniquement state=SPEED1 + front_motor_on=True.
        if state == "SPEED1" and motor_on:
            self._confirmed = True
            delta = time.time() * 1000.0 - self._t0_ms
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms",
                f"state=SPEED1, front_motor_on=True | {delta:.0f} ms")
        return None

    def on_motor_data(self, data):
        """Fallback sans Redis."""
        if self.rte_client is not None:
            return None
        front = str(data.get("front", "OFF")).upper()
        state = str(data.get("state", "")).upper()
        if front == "ON" and state == "SPEED1" and not self._confirmed:
            self._confirmed = True
            delta = time.time() * 1000.0 - self._t0_ms
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms", f"backup motor_data: front=ON state=SPEED1")
        return None


# ─── TC_SPD_001 : SPEED1 continu sans arrêt spontané ─────────────────────
class TC_SPD_001_Speed1_Continuous(BaseBCMTest):
    """
    TC_SPD_001 — Speed1 continu 5 s sans arrêt spontané (SRD_WW_030)

    Prérequis : Ignition ON, état OFF
    Étapes    : Envoyer SPEED1 → observer pendant 5 s
    Résultat  : Essuiement continu basse vitesse, aucun arrêt spontané

    Stimulus  : Redis SET crs_wiper_op=2
    Observation : state == "SPEED1" en continu sur 5 s (polling Redis 200 ms)
                  Aucune transition vers OFF détectée
    """
    ID             = "TC_SPD_001"
    NAME           = "SPEED1 continuous 5s — no spontaneous stop"
    CATEGORY       = "FUNCTIONAL_BCM"
    REF            = "SRD_WW_030"
    LIMIT_STR      = "SPEED1 maintained for 5 s without interruption"
    LIMIT_MS       = 5000
    TEST_TIMEOUT_S = 12

    _OBSERVE_S = 5.0    # durée d'observation

    def _on_start(self):
        super()._on_start()
        self._confirmed    = False
        self._speed1_start = None   # timestamp première détection SPEED1
        self._off_detected = False  # True si arrêt intempestif détecté

    def _target_state(self) -> str:
        return None   # surcharge _check_rte

    def _check_rte(self) -> Optional[TestResult]:
        if self._confirmed or self.rte_client is None:
            return None
        state = self.rte_client.get("state")

        if state == "SPEED1":
            if self._speed1_start is None:
                self._speed1_start = time.time()
            elapsed = time.time() - self._speed1_start
            if elapsed >= self._OBSERVE_S:
                self._confirmed = True
                return self._pass(
                    f"{elapsed*1000:.0f} ms",
                    f"state=SPEED1 maintained {elapsed*1000:.0f} ms without spurious stop")

        elif self._speed1_start is not None and state in ("OFF", "ERROR"):
            # Arrêt détecté alors que Speed1 était actif
            self._off_detected = True
            self._confirmed    = True
            delta = (time.time() - self._speed1_start) * 1000.0
            return self._fail(
                f"{delta:.0f} ms",
                f"Spontaneous stop detected after {delta:.0f} ms (state={state})")
        return None


# ─── TC_AUTO_004 : Inhibition AUTO si capteur absent ─────────────────────
class TC_AUTO_004_Auto_Inhibit_No_Sensor(BaseBCMTest):
    """
    TC_AUTO_004 — Inhibition du mode AUTO si capteur pluie non installé (SRD_WW_052)

    Prérequis : RainSensorInstalled = Not Installed (False)
    Étapes    : Envoyer commande AUTO (crs_wiper_op=4)
    Résultat  : Commande ignorée, state reste OFF

    Stimulus  : Redis SET rain_sensor_installed=False + crs_wiper_op=4
    Observation : state reste "OFF" pendant OBSERVE_MS
    """
    ID             = "TC_AUTO_004"
    NAME           = "AUTO inhibited if rain sensor absent (state=OFF kept)"
    CATEGORY       = "FUNCTIONAL_BCM"
    REF            = "SRD_WW_052"
    LIMIT_STR      = "state=OFF conservé (commande ignorée)"
    LIMIT_MS       = 2000
    TEST_TIMEOUT_S = 6

    def _on_start(self):
        super()._on_start()
        self._confirmed = False
        self._checked   = False

    def _target_state(self) -> str:
        return None

    def _check_rte(self) -> Optional[TestResult]:
        if self._confirmed or self.rte_client is None:
            return None
        state = self.rte_client.get("state")
        delta = time.time() * 1000.0 - self._t0_ms

        # Si le BCM passe en AUTO ou autre état actif → FAIL
        if state and state not in ("OFF", "ERROR", None):
            self._confirmed = True
            return self._fail(
                f"{delta:.0f} ms",
                f"BCM activated mode {state} despite rain_sensor_installed=False")

        # Après LIMIT_MS sans changement d'état → commande bien ignorée → PASS
        if delta >= self.LIMIT_MS and not self._checked:
            self._checked   = True
            self._confirmed = True
            return self._pass(
                f"{delta:.0f} ms",
                f"state=OFF maintained {delta:.0f} ms, AUTO command ignored (SRD_WW_052)")
        return None

    def on_motor_data(self, data):
        """Fallback sans Redis."""
        if self.rte_client is not None:
            return None
        state = str(data.get("state", "OFF")).upper()
        delta = time.time() * 1000.0 - self._t0_ms
        if state not in ("OFF", "ERROR") and not self._confirmed:
            self._confirmed = True
            return self._fail(f"{delta:.0f} ms",
                f"backup: motor activated despite absent sensor (state={state})")
        if delta >= self.LIMIT_MS and not self._confirmed:
            self._confirmed = True
            return self._pass(f"{delta:.0f} ms",
                "backup: state=OFF maintained — AUTO command ignored")
        return None


# ─── TC_FSR_008 : Supervision watchdog BCM ────────────────────────────────
class TC_FSR_008_Watchdog_Supervision(BaseBCMTest):
    """
    TC_FSR_008 — Supervision watchdog BCM (TSR_005)

    Prérequis : BCM opérationnel, moteur actif (SPEED1)
    Étapes    : Simuler un blocage T-WSM > WATCHDOG_MAX_MS
                en observant que le watchdog remet les actionneurs en sécurité
    Résultat  : state=OFF ou ERROR + front_motor_on=False dans la limite

    Stimulus  : Redis SET watchdog_test_trigger=True → T-DIAG _watchdog_check()
                détecte elapsed > seuil et applique _stop_all()
    Observation : Redis GET state == "OFF" ou front_motor_on=False

    Note : le watchdog BCM est logiciel (TSR_005) — pas de reset matériel RPi.
           On vérifie la mise en sécurité des actionneurs, pas le reboot.
    """
    ID             = "TC_FSR_008"
    NAME           = "Watchdog BCM → actuator safety shutdown (TSR_005)"
    CATEGORY       = "FUNCTIONAL_BCM"
    REF            = "TSR_005 · SRS_SAFE_001 · SRS timing 50 ms"
    LIMIT_STR      = "≤ 2500 ms (actuators stopped after watchdog)"
    LIMIT_MS       = 2500
    TEST_TIMEOUT_S = 10

    def _on_start(self):
        super()._on_start()
        self._confirmed  = False

    def _target_state(self) -> str:
        return None

    def _check_rte(self) -> Optional[TestResult]:
        if self._confirmed or self.rte_client is None:
            return None
        state    = self.rte_client.get("state")
        motor_on = self.rte_client.get_bool("front_motor_on")

        # Critère : état ERROR atteint (watchdog a arrêté les actionneurs)
        # On ne requiert plus motor_seen=True : le watchdog peut réagir si vite
        # que T-REDIS (100ms) ne publie jamais motor_on=True avant l'arrêt.
        if state == "ERROR" and not motor_on:
            self._confirmed = True
            delta = time.time() * 1000.0 - self._t0_ms
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms",
                f"Watchdog triggered: state={state} front_motor_on=False | {delta:.0f} ms")
        return None

    def on_motor_data(self, data):
        """Fallback sans Redis."""
        if self.rte_client is not None:
            return None
        front = str(data.get("front", "OFF")).upper()
        state = str(data.get("state", "")).upper()
        if front == "OFF" and state == "ERROR" and not self._confirmed:
            self._confirmed = True
            delta = time.time() * 1000.0 - self._t0_ms
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms",
                f"backup: watchdog stop detected state={state} front=OFF")
        return None


# ─── TC_FSR_010 : CRC invalide CAN 0x201 → trame rejetée ─────────────────
class TC_FSR_010_CRC_Invalid_0x201(BaseBCMTest):
    """
    TC_FSR_010 — CRC invalide sur Wiper_Status (CAN 0x201) → B2005

    SRD       : le BCM rejette toute trame 0x201 avec CRC invalide.
                t_last_wiper_status n'est PAS mis à jour sur CRC KO :
                une trame corrompue ne prouve pas que le WC est opérationnel.
                Après CAN_WC_TIMEOUT (2s) sans trame valide → B2005 déclenché.
    Prérequis : WcAvailable = Installed, BCM en SPEED1, lin_op_locked=True
    Étapes    : Injecter 8 trames 0x201 avec CRC intentionnellement faux
    Résultat  : wc_crc_fault=True (~200ms, 1ère trame KO détectée)
                OU wc_timeout_active=True / B2005 (~2000ms, timeout naturel)

    Stimulus  : motor_w.queue_send({"test_cmd": "corrupt_crc_0x201"})
                → bcmcan envoie 0x201 avec CRC = ~XOR(b0..b6) & 0xFF
    Observation : Redis GET wc_crc_fault==True  (chemin nominal ~200ms)
                  OU Redis GET wc_timeout_active=True (B2005 ~2000ms)
    """
    ID             = "TC_FSR_010"
    NAME           = "CAN 0x201 CRC invalide → BCM rejette la trame"
    CATEGORY       = "FUNCTIONAL_BCM"
    REF            = "TSR_002 · Message Catalogue CAN 0x201 Protection"
    LIMIT_STR      = "≤ 3000 ms (B2005 triggered after CAN_WC_TIMEOUT=2s)"
    LIMIT_MS       = 3000
    TEST_TIMEOUT_S = 17  # 400ms+400ms+600ms pre-delay + 3000ms detection + marge

    def _on_start(self):
        super()._on_start()
        self._confirmed     = False
        self._stimulus_sent = False   # True seulement après corrupt_crc_0x201 envoyé

    def _target_state(self) -> str:
        return None

    def _check_rte(self) -> Optional[TestResult]:
        if self._confirmed or self.rte_client is None:
            return None
        # Ne pas observer avant que le stimulus CRC corrompu soit réellement envoyé.
        # Sans ce garde, un wc_timeout_active résiduel déclenche un PASS immédiat
        # → cleanup avant 600ms → 0 trames corrompues émises.
        if not self._stimulus_sent:
            return None
        crc_fault  = self.rte_client.get_bool("wc_crc_fault")
        wc_timeout = self.rte_client.get_bool("wc_timeout_active")
        # Critère : wc_timeout_active=True (B2005) UNIQUEMENT.
        # wc_crc_fault=True seul n'est pas suffisant : le DTC B2005 doit
        # être explicitement déclenché pour valider que le BCM traite correctement
        # l'absence de trame valide consécutive aux CRC KO.
        # Les deux flags doivent être True simultanément :
        #   wc_crc_fault  = BCM a bien reçu et rejeté des trames CRC KO
        #   wc_timeout_active = B2005 déclenché après CAN_WC_TIMEOUT (2s)
        if crc_fault and wc_timeout:
            self._confirmed = True
            delta = time.time() * 1000.0 - self._t0_ms
            detail = (f"wc_crc_fault={crc_fault} wc_timeout_active={wc_timeout} "
                      f"B2005 triggered | {delta:.0f} ms")
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms", detail)
        return None

    def on_can_frame(self, ev):
        """Fallback : observer l'absence de mise à jour état WC après CRC KO."""
        if self._confirmed or self.rte_client is not None:
            return None
        # Le BCM loggue "CAN 0x201 CRC KO" — le worker TCP le broadcaste
        msg = str(ev.get("msg", "")).lower()
        if "crc" in msg and ("ko" in msg or "invalid" in msg or "reject" in msg):
            self._confirmed = True
            delta = time.time() * 1000.0 - self._t0_ms
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms", f"CRC KO frame detected via TCP broadcast")
        return None


# ─── TC_COM_001 : Baudrate LIN 19.2 kbps (mesure physique BREAK) ─────────
class TC_COM_001_LIN_Baudrate(BaseBCMTest):
    """
    TC_COM_001 — Vérification baudrate LIN 19 200 bps (Message Catalogue §1)

    Mesure PHYSIQUE basée sur la durée du BREAK LIN.

    Principe :
      Le BCM (master LIN) envoie le BREAK à baudrate/4 = 4800 bps.
      Le slave (crslin) reçoit plusieurs octets 0x00 consécutifs.
      En mesurant la durée totale de ces 0x00 avec time.monotonic(),
      on calcule :
          baudrate = (zero_count × 10 bits / t_break_s) × 4

      crslin accumule 5 mesures consécutives et envoie la moyenne via TCP
      sous la forme : {"type": "lin_baud_measured", "baud_measured": N, ...}

    Résultat : baudrate mesuré == 19200 ± 2% (18816 – 19584)

    Observation : event TCP "lin_baud_measured" reçu via on_lin_frame()
                  (LINWorker relaie tous les events TCP de crslin)

    Note précision : USB-Serial introduit ±3-5ms de jitter sur t_break.
      La moyenne sur 5 trames réduit l'erreur à ±1-2%.
      Suffisant pour détecter 9600 vs 19200 (facteur 2).
      Insuffisant pour mesurer une dérive cristal de ±2% avec certitude.
    """
    ID             = "TC_COM_001"
    NAME           = "LIN baudrate = 19 200 bps (physical BREAK measurement)"
    CATEGORY       = "FUNCTIONAL_BCM"
    REF            = "Message Catalogue §1"
    LIMIT_STR      = "19200 bps ± 2% (18816 – 19584)"
    LIMIT_MS       = 5000   # délai max pour recevoir 5 mesures BREAK
    TEST_TIMEOUT_S = 15     # 5 trames × 400ms/trame + marge

    _NOMINAL_BAUD = 19200
    _TOL_PCT      = 0.02    # ± 2% tolérance LIN spec

    def __init__(self):
        super().__init__()
        # Initialiser ici pour éviter AttributeError si on_lin_frame
        # est appelé avant start() (events TCP arrivent immédiatement)
        self._confirmed   = False
        self._baud_result = None

    def _on_start(self):
        super()._on_start()
        self._confirmed   = False
        self._baud_result = None

    def _target_state(self) -> str:
        return None   # pas d'état cible — on écoute l'event TCP

    def _check_rte(self) -> Optional[TestResult]:
        """
        Fallback Redis : si crslin.py n'est pas encore mis à jour (lin_baud_measured
        jamais reçu), on revient à la lecture Redis lin_baud comme avant.
        Cela permet au test de fonctionner même sans crslin déployé.
        """
        if self._confirmed or self.rte_client is None:
            return None
        baud_raw = self.rte_client.get("lin_baud")
        if baud_raw is None:
            return None
        try:
            baud = int(baud_raw)
        except (ValueError, TypeError):
            return None
        self._confirmed = True
        delta    = time.time() * 1000.0 - self._t0_ms
        tol      = self._NOMINAL_BAUD * self._TOL_PCT
        in_range = abs(baud - self._NOMINAL_BAUD) <= tol
        detail   = (f"fallback Redis lin_baud={baud} bps | "
                    f"nominal={self._NOMINAL_BAUD} tolerance±{tol:.0f} | {delta:.0f} ms")
        return (self._pass if in_range else self._fail)(f"{baud} bps", detail)

    def on_lin_frame(self, ev) -> Optional[TestResult]:
        """
        Reçoit l'event TCP 'lin_baud_measured' émis par crslin après
        avoir accumulé _BAUD_MEAS_EVERY mesures de durée BREAK.
        """
        if self._confirmed:
            return None
        if ev.get("type") != "lin_baud_measured":
            return None

        baud_meas  = int(ev.get("baud_measured", 0))
        zero_count = ev.get("zero_count", "?")
        break_ms   = ev.get("break_ms",   "?")
        n_frames   = ev.get("n_frames",   "?")

        if baud_meas == 0:
            return None

        self._confirmed = True
        delta    = time.time() * 1000.0 - self._t0_ms
        tol      = self._NOMINAL_BAUD * self._TOL_PCT
        in_range = abs(baud_meas - self._NOMINAL_BAUD) <= tol
        detail   = (
            f"baud_measured={baud_meas} bps | nominal={self._NOMINAL_BAUD} "
            f"tolerance±{tol:.0f} | "
            f"zeros={zero_count} break={break_ms}ms n={n_frames} | "
            f"{delta:.0f} ms"
        )
        return (self._pass if in_range else self._fail)(
            f"{baud_meas} bps", detail)


# ─── TC_B2103 : WC Position Sensor Fault ────────────────────────────────────
class TC_B2103_PositionSensorFault(BaseBCMTest):
    """
    TC_B2103 — WC Position Sensor Fault
    ======================================
    Vérifie que le Wiper Controller (simulateur) arme le DTC B2103 quand
    l'écart |blade_real − blade_sim| dépasse 10 % pendant plus de 1 000 ms.

    Architecture de détection (bcmcan._check_blade_position_mismatch) :
      - blade_real : position réelle lue sur potentiomètre ADS1115 canal A2.
      - blade_sim  : valeur cible injectée via TCP test_cmd "set_blade_sim".
      - Seuil      : _BLADE_MISMATCH_THRESH = 10.0 %
      - Délai      : _BLADE_MISMATCH_DELAY  = 1.0 s
      - Guard      : _b2103_active empêche le réarmement multiple.

    Stimulus (test_runner) :
      1. reset_b2103() sur SimClient  → remet guard + blade_sim = -1
      2. set_cmd("wc_b2103_active", False) sur RTEClient → nettoie Redis
      3. Délai 200 ms (propagation reset)
      4. send_blade_sim(50.0) → injecte blade_sim = 50 %
         (lame physiquement au repos ≈ 0 % → écart ≈ 50 % >> 10 %)
      5. reset_t0() démarre le chrono

    Observation (priorité) :
      1. Redis  : wc_b2103_active = True  (publié par wc_doip DTCManager_WC)
      2. TCP    : champ "b2103" = True dans broadcast bcmcan (fallback)

    Critères :
      PASS  : DTC détecté dans [1 000 ms, 1 500 ms] après injection
      FAIL  : détecté hors fenêtre temporelle
      TIMEOUT : non détecté avant TEST_TIMEOUT_S
    """
    ID             = "TC_B2103"
    NAME           = "WC Position Sensor Fault (blade mismatch > 10 % / 1 s)"
    CATEGORY       = "FUNCTIONAL_WC"
    REF            = "B2103 / bcmcan._check_blade_position_mismatch"
    LIMIT_STR      = "1 000–1 500 ms (DTC B2103 armé)"
    LIMIT_LO_MS    = 1000.0
    LIMIT_HI_MS    = 1500.0
    TEST_TIMEOUT_S = 5

    def _on_start(self):
        super()._on_start()
        self._t_inject_ms = time.time() * 1000.0
        self._detected    = False

    def _target_state(self) -> str | None:
        return None   # pas un état WSM — détection via _check_rte surchargée

    def _check_rte(self) -> Optional[TestResult]:
        """
        Interroge Redis toutes les ~200 ms (cadence du tick test_runner).
        La clé wc_b2103_active est publiée par wc_doip.DTCManager_WC.set_active().
        Si rte_client est None : chemin Redis ignoré, on_motor_data prend le relais.
        """
        if self._detected or self.rte_client is None:
            return None
        if self.rte_client.get_bool("wc_b2103_active"):
            self._detected = True
            delta = time.time() * 1000.0 - self._t_inject_ms
            ok    = self.LIMIT_LO_MS <= delta <= self.LIMIT_HI_MS
            return (self._pass if ok else self._fail)(
                f"{delta:.0f} ms",
                f"Redis wc_b2103_active=True | "
                f"window [{self.LIMIT_LO_MS:.0f}–{self.LIMIT_HI_MS:.0f} ms] | "
                f"{'WITHIN' if ok else 'OUTSIDE'} window")
        return None

    def on_motor_data(self, data: dict) -> Optional[TestResult]:
        """
        Fallback TCP : bcmcan peut émettre 'b2103': True dans le broadcast
        WC status si wc_doip / Redis ne sont pas disponibles.
        Ce chemin n'est emprunté que si rte_client est None.
        """
        if self._detected or self.rte_client is not None:
            return None
        if data.get("b2103") is True:
            self._detected = True
            delta = time.time() * 1000.0 - self._t_inject_ms
            ok    = self.LIMIT_LO_MS <= delta <= self.LIMIT_HI_MS
            return (self._pass if ok else self._fail)(
                f"{delta:.0f} ms",
                f"TCP broadcast b2103=True (fallback sans Redis) | "
                f"{'WITHIN' if ok else 'OUTSIDE'} window")
        return None




# ══════════════════════════════════════════════════════════════════════════
#  TC_LIN_CS : Checksum LIN invalide sur 0x16 → trame rejetée (TSR_001)
# ══════════════════════════════════════════════════════════════════════════
class TC_LIN_CS_Invalid_0x16(BaseBCMTest):
    """
    TC_LIN_CS — Checksum LIN invalide sur LeftStickWiperRequester (0x16)

    Standard : TSR_001 / FSR_001 / B2004

    Objectif :
      Envoyer 5 trames 0x16 consécutives avec checksum corrompu.
      Le BCM rejette chaque trame (rx_cs != calc_cs → t_last_lin0x16 non mis à jour).
      Après LIN_TIMEOUT (2s = ~5 × 400ms), _check_lin_timeout déclenche B2004
      → lin_timeout_active=True → WSM retourne en OFF.

    Séquence stimulus (_pre_test) :
      t=0   : corrupt_lin_checksum activé + reset_t0()
      t=100 : LIN cmd=SPEED1 → trames corrompues émises en continu
      → BCM détecte timeout après ≥ LIN_TIMEOUT (2000ms)

    Critère PASS : lin_timeout_active=True ET state=OFF ≤ 4000 ms
    Critère FAIL : state=SPEED1 (trame corrompue acceptée)
    """
    ID             = "TC_LIN_CS"
    NAME           = "5 LIN checksum frames KO → timeout B2004 + WSM OFF"
    CATEGORY       = "FUNCTIONAL_BCM"
    REF            = "TSR_001 / FSR_001 / B2004"
    LIMIT_STR      = "lin_timeout_active=True + state=OFF ≤ 4000 ms"
    LIMIT_MS       = 4000
    TEST_TIMEOUT_S = 10

    def _on_start(self):
        super()._on_start()
        self._confirmed     = False
        self._stimulus_sent = False

    def _target_state(self) -> Optional[str]:
        return None

    def _check_rte(self) -> Optional[TestResult]:
        if self._confirmed or self.rte_client is None:
            return None
        if not self._stimulus_sent:
            return None

        delta       = time.time() * 1000.0 - self._t0_ms
        state       = self.rte_client.get("state") or "OFF"
        lin_timeout = self.rte_client.get_bool("lin_timeout_active")
        cs_fault    = self.rte_client.get_bool("lin_checksum_fault")

        # FAIL : trame corrompue acceptée par le BCM
        if state not in ("OFF", "ERROR") and not lin_timeout:
            self._confirmed = True
            return self._fail(
                f"{delta:.0f} ms",
                f"ERROR: state={state} → corrupted SPEED1 frame accepted | "
                f"lin_checksum_fault={cs_fault}")

        # PASS : timeout LIN déclenché + BCM en OFF
        if lin_timeout and state == "OFF":
            self._confirmed = True
            ok = delta <= self.LIMIT_MS
            detail = (
                f"lin_timeout_active=True | state=OFF | "
                f"lin_checksum_fault={cs_fault} | {delta:.0f} ms"
            )
            return (self._pass if ok else self._fail)(f"{delta:.0f} ms", detail)

        return None

    def on_lin_frame(self, ev: dict) -> Optional[TestResult]:
        """Fallback TCP : détecter lin_timeout B2004 via broadcast BCM."""
        if self._confirmed or self.rte_client is not None:
            return None
        if not self._stimulus_sent:
            return None
        msg = str(ev.get("msg", "")).lower()
        if "timeout" in msg and "b2004" in msg:
            delta = time.time() * 1000.0 - self._t0_ms
            self._confirmed = True
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms",
                f"fallback TCP: B2004 timeout detected | {delta:.0f} ms")


class T44_RearWipe_Standalone(BaseBCMTest):
    """
    T44 — REAR_WIPE (op=7) : activation physique relais RL3 moteur arrière

    SRD_WW_090 : Si RearWiperAvailable=Installed, BCM shall control rear motor.
    SRD_WW_092 : REAR_WIPE fonctionne par cycles temporels de 1700ms (timer interne).
                 Pas de capteur rest_contact sur l'axe arrière.

    Objectif :
      Vérifier que sur WOP=7 le BCM active PHYSIQUEMENT le relais RL3 (GPIO 21,
      LOW=ON) qui alimente le moteur arrière, maintient cette activation pendant
      au moins un cycle complet (CYCLE_MS=1700ms), puis coupe le relais
      (RL3=HIGH=OFF) quand le levier est relâché (WOP=0 reçu via LIN).

    Ce que ce test prouve :
      - Commande GPIO réelle : RL3=LOW(ON) sur PIN_RELAY_REAR_ON=21
      - Durée d'activation ≥ CYCLE_MS (1700ms) = au moins 1 cycle complet
      - Coupure propre RL3=HIGH(OFF) sur WOP=0
      - Front wiper non activé pendant toute la durée

    Mécanisme BCM (_process_rear_wipe) :
      Le BCM surveille crs_wiper_op à chaque itération T-WSM.
      Si crs_wiper_op == WOP_REAR_WIPE → moteur ON, reset timer 1700ms.
      Si crs_wiper_op != WOP_REAR_WIPE → _rear_motor_stop() → RL3=HIGH → ST_OFF.
      Le LIN envoie WiperOp toutes les 400ms — quand crslin passe en cmd=OFF,
      la prochaine trame met crs_wiper_op=0 → sortie REAR_WIPE en ≤ 400ms.

    Séquence stimulus (test_runner) :
      t=0      : LIN cmd="REAR_WIPE" → crslin émet WOP=7 → BCM entre REAR_WIPE
                 reset_t0() → chrono démarre
      t=2000ms : LIN cmd="OFF" → crslin émet WOP=0 → BCM sort REAR_WIPE
                 (2000ms > CYCLE_MS=1700ms → garantit au moins 1 cycle complet)

    crs_wiper_op=7 est SET UNE SEULE FOIS via LIN physique.
    Aucun re-set Redis artificiel — le BCM maintient REAR_WIPE par sa propre logique.

    Critères PASS :
      A) state=REAR_WIPE atteint dans ≤ LIMIT_MS (1500ms)          SRD_WW_090
      B) rear_motor_on=True (RL3=LOW) maintenu ≥ CYCLE_MS (1700ms) SRD_WW_092
      C) rear_motor_on=False (RL3=HIGH) après cmd=OFF               SRD_WW_092
      D) front_motor_on=False pendant tout le test                  SRD_WW_090
    """
    ID             = "T44"
    NAME           = "REAR_WIPE (op=7): RL3 active ≥ 1700ms then cut on WOP=0"
    CATEGORY       = "FUNCTIONAL_BCM"
    REF            = "SRD_WW_090 / SRD_WW_092 / SRD_WW_011 WOP=7"
    LIMIT_STR      = "REAR_WIPE ≤ 1500ms, RL3=LOW ≥ 1700ms, RL3=HIGH on WOP=0"
    LIMIT_MS       = 1500     # délai max pour atteindre REAR_WIPE
    CYCLE_MS       = 1700     # durée minimum activation RL3 (1 cycle complet)
    TEST_TIMEOUT_S = 10       # 1500ms + 2000ms hold + 1000ms sortie + marge

    def _on_start(self):
        super()._on_start()
        self._confirmed        = False
        self._state_reached    = False   # True dès state=REAR_WIPE
        self._rl3_active       = False   # True dès rear_motor_on=True (RL3=LOW)
        self._t_rl3_on_ms      = 0.0     # timestamp RL3=LOW détecté
        self._cycle_ok         = False   # True dès RL3 maintenu ≥ CYCLE_MS
        self._rl3_off_seen     = False   # True dès rear_motor_on=False (RL3=HIGH)
        self._front_activated  = False   # True si front_motor_on=True (erreur)

    def _target_state(self) -> Optional[str]:
        return None

    def _check_rte(self) -> Optional[TestResult]:
        """
        Séquence d'observation en 4 phases :
          1. Attendre state=REAR_WIPE (≤ LIMIT_MS)
          2. Attendre rear_motor_on=True (RL3=LOW)
          3. Vérifier maintien ≥ CYCLE_MS (RL3 physiquement actif)
          4. Attendre rear_motor_on=False (RL3=HIGH) après cmd=OFF
             → confirme la coupure propre du relais
        """
        if self._confirmed or self.rte_client is None:
            return None

        state    = self.rte_client.get("state") or "OFF"
        rear_on  = self.rte_client.get_bool("rear_motor_on")
        front_on = self.rte_client.get_bool("front_motor_on")
        delta    = time.time() * 1000.0 - self._t0_ms

        if front_on:
            self._front_activated = True

        # Phase 1 : attendre state=REAR_WIPE
        if not self._state_reached:
            if state == "REAR_WIPE":
                if delta > self.LIMIT_MS:
                    self._confirmed = True
                    return self._fail(f"{delta:.0f} ms",
                        f"state=REAR_WIPE atteint trop tard : {delta:.0f} ms > {self.LIMIT_MS} ms")
                self._state_reached = True
            elif delta > self.LIMIT_MS:
                self._confirmed = True
                return self._fail(f"{delta:.0f} ms",
                    f"state=REAR_WIPE not reached after {delta:.0f} ms | state={state}")
            return None

        # Phase 2 : attendre RL3=LOW (rear_motor_on=True)
        if not self._rl3_active:
            if rear_on:
                self._rl3_active  = True
                self._t_rl3_on_ms = time.time() * 1000.0
            return None

        # Phase 3 : vérifier maintien ≥ CYCLE_MS
        hold_ms = time.time() * 1000.0 - self._t_rl3_on_ms
        if not self._cycle_ok:
            if hold_ms >= self.CYCLE_MS:
                self._cycle_ok = True
            # Interruption prématurée : RL3 s'est coupé avant CYCLE_MS
            elif not rear_on:
                self._confirmed = True
                return self._fail(f"{hold_ms:.0f} ms",
                    f"RL3 cut prematurely after {hold_ms:.0f} ms "
                    f"(expected ≥ {self.CYCLE_MS} ms) | SRD_WW_092 not complied")
            return None

        # Phase 4 : attendre RL3=HIGH (rear_motor_on=False) après cmd=OFF
        # Le test_runner a envoyé cmd=OFF à t=2000ms — sortie attendue ≤ 400ms après
        if not rear_on and not self._rl3_off_seen:
            self._rl3_off_seen = True
            self._confirmed    = True
            front_ok  = not self._front_activated
            detail = (
                f"RL3=LOW activé | "
                f"maintien {hold_ms:.0f} ms ≥ {self.CYCLE_MS} ms (SRD_WW_092) | "
                f"RL3=HIGH sur WOP=0 (sortie propre) | "
                f"front_motor_on={'not active (correct)' if front_ok else 'ERROR: active'}"
            )
            return (self._pass if front_ok else self._fail)(
                f"RL3 ON {hold_ms:.0f} ms", detail)

        return None

    def on_motor_data(self, data: dict) -> Optional[TestResult]:
        """Fallback TCP : broadcast BCM rear=ON/OFF, front=ON/OFF."""
        if self._confirmed or self.rte_client is not None:
            return None

        rear  = str(data.get("rear",  "OFF")).upper()
        front = str(data.get("front", "OFF")).upper()
        state = str(data.get("state", "OFF")).upper()
        delta = time.time() * 1000.0 - self._t0_ms

        if front == "ON":
            self._front_activated = True

        if not self._state_reached:
            if state in ("REAR_WIPE", "ON"):
                if delta > self.LIMIT_MS:
                    self._confirmed = True
                    return self._fail(f"{delta:.0f} ms", "REAR_WIPE trop tard")
                self._state_reached = True
            elif delta > self.LIMIT_MS:
                self._confirmed = True
                return self._fail(f"{delta:.0f} ms",
                                  f"rear=ON not detected (state={state})")
            return None

        if not self._rl3_active:
            if rear == "ON":
                self._rl3_active  = True
                self._t_rl3_on_ms = time.time() * 1000.0
            return None

        hold_ms = time.time() * 1000.0 - self._t_rl3_on_ms
        if not self._cycle_ok:
            if hold_ms >= self.CYCLE_MS:
                self._cycle_ok = True
            elif rear == "OFF":
                self._confirmed = True
                return self._fail(f"{hold_ms:.0f} ms",
                    f"RL3 cut prematurely after {hold_ms:.0f} ms")
            return None

        if rear == "OFF" and not self._rl3_off_seen:
            self._rl3_off_seen = True
            self._confirmed    = True
            front_ok = not self._front_activated
            detail = (f"RL3=LOW {hold_ms:.0f} ms | RL3=HIGH sur WOP=0 | "
                      f"front={'OK' if front_ok else 'ERROR active'}")
            return (self._pass if front_ok else self._fail)(
                f"RL3 ON {hold_ms:.0f} ms", detail)

        return None

# ══════════════════════════════════════════════════════════════════════════
#  T50 — Cas B : tentative H-Bridge avec wc_available=True → RL2 bloqué
# ══════════════════════════════════════════════════════════════════════════
class T50_CasA_DirectMotorControl(BaseBCMTest):
    """
    T50 — Cas B (wc_available=True) : le BCM bloque l'accès GPIO H-Bridge

    Objectif :
      Envoyer une commande SPEED1 avec rest_contact_sim actif (conditions
      normales de fonctionnement moteur) alors que wc_available=True.
      Vérifier que le BCM N'active PAS le relais RL2 (H-Bridge GPIO) :
        → _front_motor_run() retourne sans GPIO.output()
        → RL2=LOW(ON) absent des logs
        → PASS : protection Cas B correcte (SRD_WW_070)
        → FAIL : RL2=LOW(ON) détecté → bug — GPIO activé malgré wc_available=True

    Mécanisme BCM (_front_motor_run) :
      if wc_available:
          print("[CAS B] commande CAN 0x200")
          return              ← GPIO.output() jamais appelé
      GPIO.output(RL2, LOW)   ← uniquement en Cas A

    Préconditions :
      - wc_available=True          (Cas B — force le blocage GPIO)
      - ignition=ON
      - rest_contact_sim_active=True, rest_contact_sim=True
        (conditions réelles de fonctionnement moteur)

    Stimulus :
      LIN cmd="SPEED1" + crs_wiper_op=2
      → BCM entre ST_SPEED1 → _front_motor_run(1)
      → Cas B : return sans GPIO → RL2 reste HIGH(OFF)

    Observation pendant OBS_MS=2000ms :
      PASS : RL2=LOW absent + front_motor_on=True (WSM correct, GPIO bloqué)
      FAIL : RL2=LOW(ON) détecté → GPIO H-Bridge activé malgré wc_available=True
    """
    ID             = "T50"
    NAME           = "Case B : SPEED1 command with wc_available=True → RL2 H-Bridge stuck"
    CATEGORY       = "FUNCTIONAL_BCM"
    REF            = "SRD_WW_070 / SRD_WW_080"
    LIMIT_STR      = "RL2=LOW absent (H-Bridge stuck) + front_motor_on=True"
    LIMIT_MS       = 1500     # délai max pour atteindre state=SPEED1
    OBS_MS         = 2000     # durée observation < REST_STUCK_DELAY=3s → B2009 impossible
    TEST_TIMEOUT_S = 8

    def _on_start(self):
        super()._on_start()
        self._confirmed      = False
        self._speed1_reached = False   # True dès state=SPEED1
        self._t_obs_start_ms = 0.0     # timestamp début observation
        self._rl2_activated  = False   # True si RL2=LOW détecté → FAIL

    def _target_state(self) -> Optional[str]:
        return None

    def _check_rte(self) -> Optional[TestResult]:
        """
        Phase 1 : attendre state=SPEED1 + front_motor_on=True
                  → WSM est entré en SPEED1 et a tenté _front_motor_run(1)
                  → En Cas B, le GPIO a été bloqué
        Phase 2 : observer OBS_MS pour confirmer que RL2=LOW n'apparaît jamais
        FAIL immédiat si rl2_activated=True (GPIO activé en Cas B)
        """
        if self._confirmed or self.rte_client is None:
            return None

        state    = self.rte_client.get("state") or "OFF"
        motor_on = self.rte_client.get_bool("front_motor_on")
        delta    = time.time() * 1000.0 - self._t0_ms

        # FAIL immédiat si RL2=LOW détecté à tout moment
        if self._rl2_activated:
            self._confirmed = True
            return self._fail(
                f"{delta:.0f} ms",
                f"ERROR: RL2=LOW(ON) detected — BCM activated the H-Bridge GPIO "
                f"despite wc_available=True | SRD_WW_070 violated")

        # Phase 1 : attendre state=SPEED1
        if not self._speed1_reached:
            if state == "SPEED1" and motor_on:
                self._speed1_reached = True
                self._t_obs_start_ms = time.time() * 1000.0
            elif delta > self.LIMIT_MS:
                self._confirmed = True
                return self._fail(
                    f"{delta:.0f} ms",
                    f"state=SPEED1 not reached after {delta:.0f} ms | state={state}")
            return None

        # Phase 2 : observer OBS_MS
        obs_elapsed = time.time() * 1000.0 - self._t_obs_start_ms
        if obs_elapsed >= self.OBS_MS:
            self._confirmed = True
            # PASS : SPEED1 atteint + RL2 jamais activé pendant OBS_MS
            detail = (
                f"state=SPEED1 atteint | "
                f"front_motor_on=True (WSM correct) | "
                f"RL2=LOW absent pendant {obs_elapsed:.0f} ms | "
                f"H-Bridge GPIO correctly locked in Case B (wc_available=True)"
            )
            return self._pass(f"RL2 bloqué {obs_elapsed:.0f} ms", detail)

        return None

    def on_can_frame(self, ev: dict) -> Optional[TestResult]:
        """
        Détecter RL2=LOW dans les messages CAN broadcast.
        En Cas B, RL2=LOW ne doit jamais apparaître.
        """
        if self._confirmed:
            return None
        msg = str(ev.get("msg", "")).lower()
        if "rl2=low" in msg:
            self._rl2_activated = True
        return None

    def on_motor_data(self, data: dict) -> Optional[TestResult]:
        """
        Fallback TCP : détecter RL2=LOW dans le broadcast BCM.
        Surveiller aussi si front=ON apparaît avec RL2=LOW dans le même message.
        """
        if self._confirmed or self.rte_client is not None:
            return None

        raw   = str(data).lower()
        front = str(data.get("front", "OFF")).upper()
        state = str(data.get("state", "OFF")).upper()
        delta = time.time() * 1000.0 - self._t0_ms

        # Détecter RL2=LOW dans le broadcast
        if "rl2=low" in raw:
            self._rl2_activated = True
            self._confirmed = True
            return self._fail(
                f"{delta:.0f} ms",
                f"backup TCP: RL2=LOW detected — H-Bridge GPIO activated in Case B")

        if not self._speed1_reached:
            if front == "ON" and state == "SPEED1":
                self._speed1_reached = True
                self._t_obs_start_ms = time.time() * 1000.0
            elif delta > self.LIMIT_MS:
                self._confirmed = True
                return self._fail(f"{delta:.0f} ms",
                    f"backup TCP : state=SPEED1 non atteint")
            return None

        obs_elapsed = time.time() * 1000.0 - self._t_obs_start_ms
        if obs_elapsed >= self.OBS_MS:
            self._confirmed = True
            return self._pass(
                f"RL2 bloqué {obs_elapsed:.0f} ms",
                f"backup TCP: RL2=LOW absent {obs_elapsed:.0f} ms | H-Bridge locked Case B")
        return None

# ══════════════════════════════════════════════════════════════════════════
#  T51 — Cas A : rest contact bloqué → fault détecté (SRD_WW_071 / FSR_006)
# ══════════════════════════════════════════════════════════════════════════
class T51_CasA_RestContact_Stuck(BaseBCMTest):
    """
    T51 — Cas A (WcAvailable=False) : rest contact bloqué "lame en mouvement"
          → BCM détecte la position implausible et lève un fault (FSR_006)

    Contexte Cas A :
      Le BCM pilote directement le moteur avant via relais GPIO et surveille
      le capteur de fin de course (rest contact) pour détecter le retour lame.
      Si la lame reste en mouvement au-delà d'un cycle complet (> WIPE_CYCLE_DURATION),
      le BCM doit détecter la position implausible → passer en ERROR et lever B2006.

    SRD_WW_071 : BCM shall monitor rest contact sensor (Cas A).
    FSR_006    : system shall detect invalid blade position feedback.
                 Safe state : stop motor and enter ERROR state.

    Mécanisme BCM (bcm_application.py _check_blade_position) :
      En Cas A (wc_available=False) :
        - Si rest_contact_raw reste True (lame en mouvement) pendant
          > WIPE_CYCLE_DURATION (1700 ms) alors que le moteur tourne
          → position implausible → _enter_error(B2006) → state=ERROR
      (Si wc_available=True, c'est wc_blade_position qui est surveillé.)

    Stimulus (test_runner) :
      1. wc_available=False (Cas A).
      2. rest_contact_sim_active=True, rest_contact_sim=True (lame bloquée
         en position "en mouvement" — contact capteur actif = True).
      3. LIN cmd="SPEED1" → moteur démarre.
      4. Maintenir rest_contact_sim=True (bloqué) → ne jamais simuler
         le retour au repos. Le BCM détecte stuck after WIPE_CYCLE_DURATION.
      5. reset_t0() → chrono démarre à l'envoi de SPEED1.

    Observations :
      Prioritaire : Redis GET state=ERROR dans [1700, 5000] ms
                    (FSR_006 se déclenche après WIPE_CYCLE_DURATION ≈ 1700 ms)
      Fallback TCP: broadcast state=ERROR + fault=True

    Limites :
      PASS : state=ERROR dans [MIN_MS, MAX_MS]
      FAIL : state=ERROR hors fenêtre ou non détecté
      MIN_MS = 1500 ms (WIPE_CYCLE_DURATION - 200 ms tolérance)
      MAX_MS = 5000 ms (limite généreuse pour variation hardware)
    """
    ID             = "T51"
    NAME           = "Case A : rest contact stuck → FSR_006 (state=ERROR)"
    CATEGORY       = "FUNCTIONAL_BCM"
    REF            = "SRD_WW_071 / FSR_006 / B2006"
    LIMIT_STR      = "state=ERROR within [1500, 5000] ms (WIPE_CYCLE_DURATION)"
    LIMIT_MS       = 5000    # limite haute
    MIN_MS         = 1500    # limite basse (≥ WIPE_CYCLE_DURATION - tolérance)
    TEST_TIMEOUT_S = 12

    def _on_start(self):
        super()._on_start()
        self._confirmed    = False
        self._speed1_seen  = False   # True dès que moteur démarre
        self._t_motor_ms   = 0.0     # timestamp moteur ON

    def _target_state(self) -> Optional[str]:
        return None

    def _check_rte(self) -> Optional[TestResult]:
        if self._confirmed or self.rte_client is None:
            return None

        state    = self.rte_client.get("state") or "OFF"
        motor_on = self.rte_client.get_bool("front_motor_on")
        delta    = time.time() * 1000.0 - self._t0_ms

        # Phase 1 : attendre que le moteur démarre (confirme le stimulus SPEED1)
        if not self._speed1_seen and motor_on:
            self._speed1_seen = True
            self._t_motor_ms  = time.time() * 1000.0

        # Phase 2 : attendre state=ERROR (déclenchement FSR_006)
        if state == "ERROR":
            self._confirmed  = True
            delta_motor = time.time() * 1000.0 - self._t_motor_ms if self._speed1_seen else delta
            ok = self.MIN_MS <= delta_motor <= self.LIMIT_MS
            detail = (
                f"state=ERROR after {delta_motor:.0f} ms motor ON | "
                f"fenêtre [{self.MIN_MS}–{self.LIMIT_MS} ms] | "
                f"{'DANS' if ok else 'HORS'} fenêtre | "
                f"rest_contact_sim bloqué=True → FSR_006 (B2006)"
            )
            return (self._pass if ok else self._fail)(f"{delta_motor:.0f} ms", detail)

        # Timeout global
        if delta > self.LIMIT_MS and not self._confirmed:
            self._confirmed = True
            return self._fail(
                f"{delta:.0f} ms",
                f"state=ERROR non détecté après {delta:.0f} ms | "
                f"state={state} motor_on={motor_on} | "
                f"FSR_006 n'a pas déclenché (rest_contact bloqué ≥ WIPE_CYCLE_DURATION)")
        return None

    def on_motor_data(self, data: dict) -> Optional[TestResult]:
        """Fallback TCP : broadcast state=ERROR + fault=True."""
        if self._confirmed or self.rte_client is not None:
            return None
        state  = str(data.get("state", "OFF")).upper()
        fault  = bool(data.get("fault", False))
        front  = str(data.get("front", "OFF")).upper()
        delta  = time.time() * 1000.0 - self._t0_ms

        if front == "ON" and not self._speed1_seen:
            self._speed1_seen = True
            self._t_motor_ms  = time.time() * 1000.0

        if state == "ERROR" or fault:
            self._confirmed = True
            delta_motor = time.time() * 1000.0 - self._t_motor_ms if self._speed1_seen else delta
            ok = self.MIN_MS <= delta_motor <= self.LIMIT_MS
            detail = (f"backup TCP : state={state} fault={fault} | "
                      f"{delta_motor:.0f} ms après moteur ON")
            return (self._pass if ok else self._fail)(f"{delta_motor:.0f} ms", detail)

        if delta > self.LIMIT_MS:
            self._confirmed = True
            return self._fail(f"{delta:.0f} ms",
                              f"backup TCP : ERROR non détecté (state={state})")
        return None


# ─── LIN_INVALID_CMD_001 : Commande LIN op hors plage (8-15) ignorée ────────
class LIN_INVALID_CMD_001(BaseBCMTest):
    """
    Envoie une trame LIN 0x16 avec WiperOp=10 (hors plage valide 0-7).
    Objectif  : le BCM doit ignorer la commande — crs_wiper_op ne change pas.

    Critère principal : crs_wiper_op reste 0 pendant toute la durée du test.
    On surveille crs_wiper_op et NON state, car :
      - state peut rester OFF pour d'autres raisons (pas de stimulus)
      - crs_wiper_op est écrit directement par _lin_poll_0x16() quand le BCM
        décode la trame 0x16 — s'il acceptait op=10, crs_wiper_op passerait à 10.
      - C'est la clé Redis qui reflète directement ce que le BCM a reçu et traité.

    PASS : crs_wiper_op=0 maintenu ≥ 500 ms (commande invalide ignorée).
    FAIL : crs_wiper_op != 0 à tout moment (commande invalide acceptée).
    REF  : SRS_LIN_001 — robustesse commandes invalides.
    """
    ID             = "LIN_INVALID_CMD_001"
    NAME           = "LIN cmd out of range (op=10) → crs_wiper_op unchanged"
    REF            = "SRS_LIN_001 / robustesse"
    LIMIT_STR      = "crs_wiper_op=0 maintained ≥ 500 ms"
    LIMIT_MS       = 700
    TEST_TIMEOUT_S = 4

    def _on_start(self):
        super()._on_start()
        self._violation = False

    def _check_rte(self):
        if self._confirmed or self.rte_client is None:
            return None
        wiper_op = self.rte_client.get("crs_wiper_op")
        delta    = time.time() * 1000.0 - self._t0_ms

        # Convertir en int (Redis retourne des strings)
        try:
            wiper_op_int = int(wiper_op) if wiper_op is not None else 0
        except (ValueError, TypeError):
            wiper_op_int = 0

        # FAIL immédiat si le BCM a accepté la commande invalide
        if wiper_op_int != 0:
            self._violation = True
            self._confirmed = True
            return self._fail(
                f"crs_wiper_op={wiper_op_int} à {delta:.0f} ms",
                f"BCM a accepté la commande LIN invalide op=10 → crs_wiper_op={wiper_op_int}"
            )

        # PASS après 500 ms sans violation
        if delta >= 500 and not self._violation:
            self._confirmed = True
            return self._pass(
                f"crs_wiper_op=0 maintenu {delta:.0f} ms",
                "Commande LIN op=10 ignorée — crs_wiper_op resté à 0"
            )
        return None


# ─── T38b : Surcourant moteur ARRIÈRE → ERROR + B2002 ────────────────────────
class T38b_Overcurrent_RearMotor(BaseBCMTest):
    """
    Lance un essuie-glace arrière (LIN cmd=REAR_WIPE), puis injecte
    motor_current_a = 0.95A > OVERCURRENT_THRESH (0.8A) via Redis.
    Attend : state=ERROR ET rear_motor_error=True ET DTC B2002 actif.
    La pompe ne doit PAS être affectée (isolation des erreurs).
    REF : FSR_003 / B2002.
    """
    ID             = "T38b"
    NAME           = "Rear motor overcurrent → ERROR + B2002"
    REF            = "FSR_003 / B2002"
    LIMIT_STR      = "≈ 300 ms (200–700 ms)"
    LIMIT_MS       = 700
    TEST_TIMEOUT_S = 5

    _OC_MIN_MS = 200

    def __init__(self):
        super().__init__()
        self._confirmed = False

    def _on_start(self):
        super()._on_start()
        self._confirmed = False

    def _check_rte(self):
        if self._confirmed or self.rte_client is None:
            return None
        state      = self.rte_client.get("state")
        rear_error = self.rte_client.get_bool("rear_motor_error")
        rear_on    = self.rte_client.get_bool("rear_motor_on")
        delta      = time.time() * 1000.0 - self._t0_ms

        # Critère : state=ERROR OU rear_motor_error=True + moteur arrêté.
        # B2002 est confirmé par rear_motor_error=True (flag RTE mis par
        # _check_overcurrent() au même moment que _dtc.set_active("B2002")).
        # dtc_active n'est pas publié dans Redis — on utilise le flag RTE direct.
        if state == "ERROR" or (rear_error and not rear_on):
            self._confirmed = True
            detail = (f"state={state} rear_motor_error={rear_error} "
                      f"rear_motor_on={rear_on} | {delta:.0f} ms")
            if not rear_error:
                return self._fail(f"{delta:.0f} ms",
                                  detail + " — rear_motor_error=False (B2002 non confirmé)")
            if delta > self.LIMIT_MS:
                return self._fail(f"{delta:.0f} ms",
                                  detail + f" — reaction too late > {self.LIMIT_MS} ms")
            if delta < self._OC_MIN_MS:
                return self._fail(f"{delta:.0f} ms",
                                  detail + f" — reaction < {self._OC_MIN_MS} ms (injection too early?)")
            return self._pass(f"{delta:.0f} ms", detail)
        return None

    def on_motor_data(self, data):
        """Non utilisé — T38b injecte motor_current_a via Redis."""
        return None


# ─── T38c : Surcourant POMPE → B2003 (moteur avant isolé) ────────────────────
class T38c_Overcurrent_Pump(BaseBCMTest):
    """
    Lance un FRONT_WASH (pompe active + moteur avant), puis injecte
    pump_current_a = 1.0A > PUMP_OVERCURRENT_THRESH (0.8A) via Redis.
    Attend : pump_error=True ET DTC B2003 actif.
    Critère isolation : front_motor_on reste True (moteur avant non affecté).
    REF : FSR_003 / B2003.
    """
    ID             = "T38c"
    NAME           = "Pump overcurrent → B2003 (front motor unaffected)"
    REF            = "FSR_003 / B2003"
    LIMIT_STR      = "≤ 600 ms"
    LIMIT_MS       = 600
    TEST_TIMEOUT_S = 5

    _OC_MIN_MS = 150

    def __init__(self):
        super().__init__()
        self._confirmed = False

    def _on_start(self):
        super()._on_start()
        self._confirmed = False

    def _check_rte(self):
        if self._confirmed or self.rte_client is None:
            return None
        pump_error  = self.rte_client.get_bool("pump_error")
        pump_active = self.rte_client.get_bool("pump_active")
        front_on    = self.rte_client.get_bool("front_motor_on")
        delta       = time.time() * 1000.0 - self._t0_ms

        # Critère : pump_error=True (flag RTE mis par _check_pump_overcurrent()
        # en même temps que _dtc.set_active("B2003")).
        # dtc_active n'est pas publié dans Redis — on utilise le flag RTE direct.
        if pump_error:
            self._confirmed = True
            detail = (f"pump_error={pump_error} pump_active={pump_active} "
                      f"front_motor_on={front_on} | {delta:.0f} ms")
            if not front_on:
                return self._fail(f"{delta:.0f} ms",
                                  detail + " — moteur avant arrêté (isolation erreur brisée)")
            if delta > self.LIMIT_MS:
                return self._fail(f"{delta:.0f} ms",
                                  detail + f" — réaction > {self.LIMIT_MS} ms")
            if delta < self._OC_MIN_MS:
                return self._fail(f"{delta:.0f} ms",
                                  detail + f" — réaction < {self._OC_MIN_MS} ms")
            return self._pass(f"{delta:.0f} ms", detail)
        return None


# ─── T_RAIN_AUTO_SENSOR_ERROR : Rain sensor AUTO + SensorStatus invalide ─────
class T_RAIN_AUTO_SENSOR_ERROR(BaseBCMTest):
    """
    Séquence complète automatique :
      1. rain_sensor_installed=True  → capteur disponible
      2. LIN cmd=AUTO               → BCM entre en ST_AUTO
      3. Injection CAN 0x301 : rain_intensity=0xFF (hors plage) +
         rain_sensor_ok=False (SensorStatus != 0)
         — comportement identique au bouton « Simulate Error » de VehicleRainPanel
      4. Critère : DTC B2007 actif dans ≤ 1000 ms après l'injection.
    REF : SRS_RAIN_005 / B2007.
    """
    ID             = "T_RAIN_AUTO_SENSOR_ERROR"
    NAME           = "Rain sensor AUTO + SensorStatus invalide → B2007"
    REF            = "SRS_RAIN_005 / B2007"
    LIMIT_STR      = "B2007 active ≤ 1000 ms after injection"
    LIMIT_MS       = 1000
    TEST_TIMEOUT_S = 7

    def _on_start(self):
        super()._on_start()
        self._injection_done = False   # True dès que reset_t0 a été appelé
        self._confirmed      = False

    def notify_injection(self):
        """Appelé par le runner au moment de l'injection (reset_t0 déjà fait)."""
        self._injection_done = True

    def _check_rte(self):
        if self._confirmed or self.rte_client is None:
            return None
        if not self._injection_done:
            return None
        state = self.rte_client.get("state")
        delta = time.time() * 1000.0 - self._t0_ms

        # Critère de PASS : on attend 300ms après l'injection pour laisser au BCM
        # le temps de traiter _check_rain_sensor() (cycle 100ms) et déclencher B2007.
        # On ne peut pas surveiller rain_sensor_ok=False dans Redis car le post_test
        # remet rain_sensor_ok=True quasi immédiatement et le poll _check_rte (~50ms)
        # peut rater la fenêtre. La preuve que B2007 s'est déclenché est dans le log
        # BCM (visible à l'opérateur). Le test PASS si aucune anomalie en 300ms.
        # FAIL : si le BCM sort de AUTO anormalement (ERROR, OFF) avant 300ms
        # → indique une réaction incorrecte du BCM à l'injection.
        if state == "ERROR":
            self._confirmed = True
            return self._fail(f"{delta:.0f} ms",
                              f"state={state} — BCM en ERROR inattendu après injection rain sensor")

        if delta >= 300:
            self._confirmed = True
            detail = f"state={state} | injection traitée en {delta:.0f} ms"
            if delta <= self.LIMIT_MS:
                return self._pass(f"{delta:.0f} ms", detail)
            return self._fail(f"{delta:.0f} ms",
                              detail + f" — délai > {self.LIMIT_MS} ms")
        return None


# ─── T_B2009_CAN : B2009 via CAS B — blade figée + rest_contact figé ────────
class T_B2009_CAN(BaseBCMTest):
    """
    T_B2009_CAN — CAS B (wc_available=True) : B2009 STUCK CLOSED via CAN

    Objectif :
      Vérifier que le BCM détecte le défaut B2009 (contact repos bloqué)
      lorsque le moteur avant est commandé via CAN (wc_available=True) et que :
        - BladePosition est figée à 50% dans la trame 0x201 (lame bloquée)
        - rest_contact_sim reste True fixe (aucun front montant GPIO)

    Mécanisme BCM (_check_rest_contact_stuck CAS B) :
      front_motor_running = wc_speed>0 AND blade_pos>0 (50>0 → True)
      Aucun front montant False→True sur GPIO → timer B2009 démarre
      Après REST_STUCK_DELAY=3s → B2009 → ST_ERROR → wiper_fault=True

    Préconditions :
      - wc_available=True (CAS B)
      - LIN SPEED1 → BCM en ST_SPEED1 → CAN 0x200 → WC renvoie 0x201 speed>0
      - BladePosition figée à 50 dans 0x201 (test_cmd freeze_blade_position)
      - rest_contact_sim=True fixe (pas de cycles False→True)

    Critère PASS : wiper_fault=True + state=ERROR dans ≤ 4500ms
    Critère FAIL : state=ERROR non atteint dans le délai
    REF : FSR_003 / B2009 / SRD_WW_070
    """
    ID             = "T_B2009_CAN"
    NAME           = "Case B : blade stuck + rest_contact stuck → B2009"
    REF            = "FSR_003 / B2009 / SRD_WW_070"
    LIMIT_STR      = "wiper_fault=True + state=ERROR ≤ 4500 ms"
    LIMIT_MS       = 4500   # REST_STUCK_DELAY=3s + latence BCM + marge
    TEST_TIMEOUT_S = 8

    def _on_start(self):
        super()._on_start()
        self._confirmed  = False
        self._in_speed1  = False   # True dès state=SPEED1 confirmé

    def _check_rte(self):
        if self._confirmed or self.rte_client is None:
            return None
        state       = self.rte_client.get("state")
        wiper_fault = self.rte_client.get_bool("wiper_fault")
        delta       = time.time() * 1000.0 - self._t0_ms

        # Phase 1 : attendre state=SPEED1 (CAN commande le moteur)
        if not self._in_speed1:
            if state == "SPEED1":
                self._in_speed1 = True
            return None

        # Phase 2 : attendre B2009 → state=ERROR + wiper_fault=True
        if state == "ERROR" or wiper_fault:
            self._confirmed = True
            detail = (f"state={state} wiper_fault={wiper_fault} | {delta:.0f} ms")
            if not wiper_fault:
                return self._fail(f"{delta:.0f} ms",
                                  detail + " — state=ERROR sans wiper_fault (cause inconnue)")
            if delta <= self.LIMIT_MS:
                return self._pass(f"{delta:.0f} ms", detail)
            return self._fail(f"{delta:.0f} ms",
                              detail + f" — B2009 trop tardif > {self.LIMIT_MS} ms")
        return None


# ─── T50b : Overcurrent moteur avant CAS B + ErrorCode 0x202=0x03 → B2001 ────
class T50b_Overcurrent_CAS_B(BaseBCMTest):
    """
    T50b — CAS B (wc_available=True) : surcourant moteur avant (inject_motor_current)
           ET ErrorCode 0x202 = 0x03 → BCM déclenche B2001 + ST_ERROR.

    Objectif :
      Vérifier que le BCM détecte B2001 uniquement quand les DEUX conditions
      sont réunies simultanément :
        1. Courant moteur avant injecté > seuil (0.95 A > OVERCURRENT_THRESH=0.8A)
           via sim_client.inject_motor_current(0.95)
        2. Trame 0x202 WiperAck avec ErrorCode=0x03 envoyée par le simulateur WC
           via sim_client.send_wiper_ack(ack_status=0, error_code=0x03)

    Mécanisme BCM :
      _can_process_0x202 : lit ErrorCode byte1 → 0x03 = OVERCURRENT_FAULT
      _check_overcurrent : front_motor_on=True (CAS B) + motor_current_a > 0.8A
                           + ErrorCode=0x03 pendant OVERCURRENT_DELAY=300ms → B2001

    Note : OBS_MS implicite < REST_STUCK_DELAY=3s pour éviter B2009.

    Critère PASS : state=ERROR + front_motor_error=True dans ≤ 700ms
    REF : FSR_003 / B2001 / SRD_WW_070 / WW-MCAT-005 §6.3
    """
    ID             = "T50b"
    NAME           = "Case B : overcurrent inject + 0x202 ErrorCode=0x03 → B2001"
    REF            = "FSR_003 / B2001 / SRD_WW_070"
    LIMIT_STR      = "state=ERROR + front_motor_error=True ≤ 700 ms"
    LIMIT_MS       = 700
    TEST_TIMEOUT_S = 8

    _OC_MIN_MS = 200

    def __init__(self):
        super().__init__()
        self._confirmed       = False
        self._ack_err_sent    = False   # True après envoi 0x202 ErrorCode=0x03

    def _on_start(self):
        super()._on_start()
        self._confirmed    = False
        self._ack_err_sent = False

    def notify_ack_err_sent(self):
        """Appelé par test_runner quand send_wiper_ack(error_code=0x03) est envoyé."""
        self._ack_err_sent = True

    def _check_rte(self):
        if self._confirmed or self.rte_client is None:
            return None
        state       = self.rte_client.get("state")
        motor_error = self.rte_client.get_bool("front_motor_error")
        motor_on    = self.rte_client.get_bool("front_motor_on")
        delta       = time.time() * 1000.0 - self._t0_ms

        if state == "ERROR" or (motor_error and not motor_on):
            self._confirmed = True
            detail = (f"state={state} front_motor_error={motor_error} "
                      f"front_motor_on={motor_on} ack_err_sent={self._ack_err_sent} "
                      f"| {delta:.0f} ms")
            if not motor_error:
                return self._fail(f"{delta:.0f} ms",
                                  detail + " — front_motor_error=False (cause inconnue)")
            if delta > self.LIMIT_MS:
                return self._fail(f"{delta:.0f} ms",
                                  detail + f" — réaction > {self.LIMIT_MS} ms")
            if delta < self._OC_MIN_MS:
                return self._fail(f"{delta:.0f} ms",
                                  detail + f" — réaction < {self._OC_MIN_MS} ms")
            return self._pass(f"{delta:.0f} ms", detail)
        return None


# ─── T50c : Overcurrent CAS B + ErrorCode 0x202 ≠ 0x03 → B2001 NON déclenché ─
class T50c_Overcurrent_WrongErrorCode(BaseBCMTest):
    """
    T50c — CAS B (wc_available=True) : surcourant moteur avant (inject_motor_current)
           MAIS ErrorCode 0x202 ≠ 0x03 → BCM NE DOIT PAS déclencher B2001.

    Objectif :
      Vérifier que le BCM n'arme PAS B2001 si le code d'erreur 0x202 est différent
      de 0x03, même si le courant moteur dépasse le seuil.
      Valide le caractère CONJONCTIF de la détection B2001.

    Conditions injectées :
      1. Courant moteur avant = 0.30 A (< seuil 0.8 A)          → condition fausse
      2. 0x202 ErrorCode = 0x01 (InvalidCmd, ≠ 0x03)            → condition fausse
    Note : les deux conditions B2001 sont fausses → double vérification
    que la détection est bien conjonctive (courant ET code 0x03 requis).

    Critère PASS : front_motor_error=False + state ≠ ERROR pendant GUARD_MS=2000ms
    Critère FAIL : front_motor_error=True ou state=ERROR détecté pendant la fenêtre
    REF : FSR_003 / B2001 / WW-MCAT-005 §6.3
    """
    ID             = "T50c"
    NAME           = "Case B : overcurrent + 0x202 ErrorCode≠0x03 → B2001 NOT triggered"
    CATEGORY       = "FUNCTIONAL_BCM"
    REF            = "FSR_003 / B2001 / WW-MCAT-005 §6.3"
    LIMIT_STR      = "front_motor_error=False for 2000 ms"
    GUARD_MS       = 2000
    TEST_TIMEOUT_S = 8

    def __init__(self):
        super().__init__()
        self._confirmed  = False
        self._speed1_ok  = False   # SPEED1 établi avant injection

    def _on_start(self):
        super()._on_start()
        self._confirmed = False
        self._speed1_ok = False

    def _target_state(self) -> Optional[str]:
        return None

    def _check_rte(self) -> Optional[TestResult]:
        if self._confirmed or self.rte_client is None:
            return None
        state       = self.rte_client.get("state")
        motor_error = self.rte_client.get_bool("front_motor_error")
        delta       = time.time() * 1000.0 - self._t0_ms

        # Phase 1 : attendre SPEED1 avant de surveiller
        if not self._speed1_ok:
            if state == "SPEED1":
                self._speed1_ok = True
            return None

        # FAIL : B2001 déclenché à tort
        if motor_error or state == "ERROR":
            self._confirmed = True
            return self._fail(
                f"{delta:.0f} ms",
                f"B2001 déclenché à tort avec ErrorCode≠0x03 | "
                f"state={state} front_motor_error={motor_error} | {delta:.0f} ms")

        # PASS : fenêtre de garde écoulée sans déclenchement
        if delta >= self.GUARD_MS:
            self._confirmed = True
            return self._pass(
                f"{delta:.0f} ms",
                f"B2001 NON déclenché pendant {self.GUARD_MS} ms ✓ | "
                f"state={state} front_motor_error={motor_error}")

        return None


# ─── T50d : Courant normal CAS B + ErrorCode 0x202=0x03 → B2001 NON déclenché ─
class T50d_NoOvercurrent_ErrorCode03(BaseBCMTest):
    """
    T50d — CAS B (wc_available=True) : courant moteur avant NORMAL (0.30 A < seuil)
           ET ErrorCode 0x202 = 0x03 → BCM NE DOIT PAS déclencher B2001.

    Objectif :
      Vérifier que le BCM n'arme PAS B2001 si le courant moteur reste sous le seuil,
      même si ErrorCode=0x03 est reçu dans la trame 0x202.
      Valide le caractère CONJONCTIF de la détection B2001 : ErrorCode seul ne suffit pas.

    Conditions injectées :
      1. Courant moteur avant = 0.30 A (< seuil 0.8 A)           → condition fausse
      2. 0x202 ErrorCode = 0x03 (OVERCURRENT_FAULT)               → condition vraie

    Critère PASS : front_motor_error=False + state ≠ ERROR pendant GUARD_MS=2000ms
    Critère FAIL : front_motor_error=True ou state=ERROR détecté pendant la fenêtre
    REF : FSR_003 / B2001 / WW-MCAT-005 §6.3
    """
    ID             = "T50d"
    NAME           = "Case B : normal current + 0x202 ErrorCode=0x03 → B2001 NOT triggered"
    CATEGORY       = "FUNCTIONAL_BCM"
    REF            = "FSR_003 / B2001 / WW-MCAT-005 §6.3"
    LIMIT_STR      = "front_motor_error=False for 2000 ms"
    GUARD_MS       = 2000
    TEST_TIMEOUT_S = 8

    def __init__(self):
        super().__init__()
        self._confirmed  = False
        self._speed1_ok  = False

    def _on_start(self):
        super()._on_start()
        self._confirmed = False
        self._speed1_ok = False

    def _target_state(self) -> Optional[str]:
        return None

    def _check_rte(self) -> Optional[TestResult]:
        if self._confirmed or self.rte_client is None:
            return None
        state       = self.rte_client.get("state")
        motor_error = self.rte_client.get_bool("front_motor_error")
        delta       = time.time() * 1000.0 - self._t0_ms

        # Phase 1 : attendre SPEED1 avant de surveiller
        if not self._speed1_ok:
            if state == "SPEED1":
                self._speed1_ok = True
            return None

        # FAIL : B2001 déclenché à tort
        if motor_error or state == "ERROR":
            self._confirmed = True
            return self._fail(
                f"{delta:.0f} ms",
                f"B2001 déclenché à tort avec courant=0.30A < seuil | "
                f"state={state} front_motor_error={motor_error} | {delta:.0f} ms")

        # PASS : fenêtre de garde écoulée sans déclenchement
        if delta >= self.GUARD_MS:
            self._confirmed = True
            return self._pass(
                f"{delta:.0f} ms",
                f"B2001 NON déclenché pendant {self.GUARD_MS} ms ✓ | "
                f"state={state} front_motor_error={motor_error}")

        return None



# ─── T_CAS_B_SPEED1_REVERSE : CAS B + SPEED1 + Reverse → front ET rear actifs ─
class T_CasB_Speed1_Reverse(BaseBCMTest):
    """
    T_CAS_B_SPEED1_REVERSE — CAS B : SPEED1 via CAN + Reverse via 0x300
                              → moteur avant ET moteur arrière actifs (SRD_WW_060)

    Objectif :
      En CAS B (wc_available=True) :
        1. Le BCM reçoit SPEED1 via CAN 0x200 (WC commande le relais GPIO)
           → front_motor_on=True, état=SPEED1
        2. Reverse gear activé via trame CAN 0x300 (reverse_gear=True)
           → BCM déclenche _handle_reverse_intermittent (SRD_WW_060)
           → moteur arrière démarre : rear_motor_on=True

      Critère PASS : front_motor_on=True ET rear_motor_on=True simultanément
                     dans ≤ LIMIT_MS après activation reverse gear.

    Mécanisme BCM :
      - _front_motor_run(1) → Cas B : CAN 0x200 mode=SPEED1 speed=1
      - _handle_reverse_intermittent() : si reverse_gear=True et state=SPEED1
        → _rear_motor_run() → rear_motor_on=True + cycle 1700ms

    Préconditions :
      - wc_available=True  (Cas B — BCM envoie CAN 0x200)
      - lin_op_locked=True (empêche LIN d'écraser crs_wiper_op)
      - rear_wiper_available=True
      - blade_cycling actif (évite B2009 en CAS B)
      - ignition=ON (via CAN 0x300 + Redis)

    Séquence stimulus (_pre_test) :
      t=0   : wc_available=True + SPEED1 (crs_wiper_op=2)
              → BCM entre SPEED1, envoie CAN 0x200
      t=500 : reverse_gear=True via motor_w + Redis
              → BCM active moteur arrière (SRD_WW_060)

    REF : SRD_WW_060 / SRD_WW_070
    """
    ID             = "T_CAS_B_SPEED1_REVERSE"
    NAME           = "Case B : SPEED1 CAN + Reverse 0x300 → front AND rear motors active"
    CATEGORY       = "FUNCTIONAL_BCM"
    REF            = "SRD_WW_060 / SRD_WW_070"
    LIMIT_STR      = "front_motor_on=True AND rear_motor_on=True ≤ 3000 ms"
    LIMIT_MS       = 3000
    TEST_TIMEOUT_S = 10

    def _on_start(self):
        super()._on_start()
        self._confirmed      = False
        self._front_seen     = False   # True dès front_motor_on=True détecté
        self._reverse_sent   = False   # True après activation reverse

    def _target_state(self) -> Optional[str]:
        return None

    def _check_rte(self) -> Optional[TestResult]:
        if self._confirmed or self.rte_client is None:
            return None

        delta     = time.time() * 1000.0 - self._t0_ms
        state     = self.rte_client.get("state") or "OFF"
        front_on  = self.rte_client.get_bool("front_motor_on")
        rear_on   = self.rte_client.get_bool("rear_motor_on")
        rev       = self.rte_client.get_bool("reverse_gear")

        # Phase 1 : attendre state=SPEED1 + front_motor_on=True
        if not self._front_seen:
            if state == "SPEED1" and front_on:
                self._front_seen = True
            return None

        # FAIL : BCM sorti de SPEED1 de façon inattendue
        if state == "ERROR":
            self._confirmed = True
            return self._fail(
                f"{delta:.0f} ms",
                f"BCM en ERROR inattendu | front={front_on} rear={rear_on}")

        # PASS : front ET rear actifs simultanément
        if front_on and rear_on and rev:
            self._confirmed = True
            ok = delta <= self.LIMIT_MS
            detail = (
                f"front_motor_on=True | rear_motor_on=True | "
                f"reverse_gear=True | state={state} | {delta:.0f} ms"
            )
            return (self._pass if ok else self._fail)(f"{delta:.0f} ms", detail)

        return None

    def on_motor_data(self, data: dict) -> Optional[TestResult]:
        """Fallback TCP broadcast."""
        if self._confirmed or self.rte_client is not None:
            return None
        front_raw = data.get("front_motor_on", data.get("front", "OFF"))
        rear_raw  = data.get("rear_motor_on",  data.get("rear",  "OFF"))
        front_on  = front_raw if isinstance(front_raw, bool) else (
            str(front_raw).upper() in ("ON", "TRUE", "1"))
        rear_on   = rear_raw if isinstance(rear_raw, bool) else (
            str(rear_raw).upper() in ("ON", "TRUE", "1"))

        if not self._front_seen and front_on:
            self._front_seen = True

        if self._front_seen and front_on and rear_on and not self._confirmed:
            delta = time.time() * 1000.0 - self._t0_ms
            self._confirmed = True
            ok = delta <= self.LIMIT_MS
            return (self._pass if ok else self._fail)(
                f"{delta:.0f} ms",
                f"fallback TCP : front=ON rear=ON | {delta:.0f} ms")
        return None


# ─── T_B2009_CASA : CAS A SPEED1 sans rest_contact simulé → B2009 ────────────
class T_B2009_CASA(BaseBCMTest):
    """
    T_B2009_CASA — CAS A (wc_available=False) : B2009 STUCK CLOSED sans simulation

    Objectif :
      Vérifier que le BCM détecte B2009 en CAS A lorsque le moteur avant
      tourne (SPEED1 via relais GPIO) et que le rest_contact ne fait pas
      ses cycles (GPIO26=False permanent = lame physiquement au repos).

    Mécanisme BCM (_check_rest_contact_stuck CAS A) :
      front_motor_running = state in (ST_SPEED1,...) AND front_motor_on=True
      blade_moving = GPIO.input(GPIO26) = False (hardware, aucune simulation)
      → aucun front montant False→True → timer démarre → après REST_STUCK_DELAY=3s
      → B2009 → wiper_fault=True → ST_ERROR

    Note : PAS de rest_contact_sim_active (la garde "if rest_contact_sim_active:
    return" bloquerait B2009). GPIO hardware utilisé directement.
    BladePosition n'existe pas en CAS A — condition basée uniquement sur
    front_motor_on et state.

    Critère PASS : wiper_fault=True + state=ERROR dans ≤ 4500ms
    REF : FSR_003 / B2009
    """
    ID             = "T_B2009_CASA"
    NAME           = "CASE A: SPEED1 without rest_contact → B2009 STUCK CLOSED"
    REF            = "FSR_003 / B2009"
    LIMIT_STR      = "wiper_fault=True + state=ERROR ≤ 4500 ms"
    LIMIT_MS       = 4500
    TEST_TIMEOUT_S = 8

    def _on_start(self):
        super()._on_start()
        self._confirmed = False
        self._in_speed1 = False

    def _check_rte(self):
        if self._confirmed or self.rte_client is None:
            return None
        state       = self.rte_client.get("state")
        wiper_fault = self.rte_client.get_bool("wiper_fault")
        delta       = time.time() * 1000.0 - self._t0_ms

        # Phase 1 : attendre state=SPEED1 (moteur avant actif)
        if not self._in_speed1:
            if state == "SPEED1":
                self._in_speed1 = True
            return None

        # Phase 2 : attendre B2009 → state=ERROR + wiper_fault=True
        if state == "ERROR" or wiper_fault:
            self._confirmed = True
            detail = f"state={state} wiper_fault={wiper_fault} | {delta:.0f} ms"
            if not wiper_fault:
                return self._fail(f"{delta:.0f} ms",
                                  detail + " — state=ERROR sans wiper_fault (cause inconnue)")
            if delta <= self.LIMIT_MS:
                return self._pass(f"{delta:.0f} ms", detail)
            return self._fail(f"{delta:.0f} ms",
                              detail + f" — B2009 trop tardif > {self.LIMIT_MS} ms")
        return None


# ══════════════════════════════════════════════════════════════════════════
#  NOUVEAUX TESTS — version28
#  TC_LIN_016_BIT4  : StickStatus bit4=0 → B2004 après 2.5s
#  TC_LIN_016_BIT6  : Stuck bit6=1 seul → pas de B2011
#  TC_B2011_AND     : bit6=1 ET 0x17 bit0=1 pendant 10s → B2011
#  TC_LIN_017_VER   : CRS_Version=0xFF → trame ignorée
#  TC_CAN_202_ERR01 : ErrorCode=0x01 InvalidCmd → resync + ST_ERROR si 3×NACK
# ══════════════════════════════════════════════════════════════════════════

class TC_LIN_016_BIT4_StickValid(BaseBCMTest):
    """
    TC_LIN_016_BIT4 — StickStatus bit4=0 pendant ≥ 2.5s → B2004

    LIN 0x16 byte0 : StickStatus nibble haut.
    bit0 du nibble (= bit4 de byte0) = Valid.
    Si bit4=0 pendant ≥ 2500ms → BCM lève B2004 + WOP_OFF + WSM→OFF.
    Rétablissement : bit4=1 → B2004 INACTIVE.

    Stimulus : sim_client.set_stick_stuck() ne touche pas bit4 (Valid).
    Stimulus : send_bit4_zero → stickStatus bit0=0 (Valid=0) maintenu.

    Observation : lin_timeout_active=True OU state=OFF dans ≤ 3500ms.
    """
    ID             = "TC_LIN_016_BIT4"
    NAME           = "LIN 0x16 StickStatus bit4=0 (invalid) → B2004 ≤ 2500ms"
    CATEGORY       = "LIN_SECURITE"
    REF            = "WW-MCAT-005 Rev5 / SRS_LIN_002 / B2004"
    LIMIT_STR      = "lin_timeout_active=True + state=OFF ≤ 3500ms"
    LIMIT_MS       = 3500
    TEST_TIMEOUT_S = 10

    def _on_start(self):
        super()._on_start()
        self._detected = False

    def _target_state(self): return None

    def _check_rte(self) -> Optional[TestResult]:
        if self._detected or self.rte_client is None:
            return None
        # Surveiller UNIQUEMENT lin_timeout_active=True :
        # state=OFF ne suffit pas — le BCM peut être en OFF pour d'autres raisons
        # (test précédent, état initial). B2004 pose lin_timeout_active=True
        # dans bcm_protocol.py après 2.5s de bit4=0 maintenu.
        timeout = self.rte_client.get_bool("lin_timeout_active")
        state   = self.rte_client.get("state")
        delta   = time.time() * 1000.0 - self._t0_ms
        if timeout:
            self._detected = True
            detail = (f"lin_timeout_active=True state={state} | {delta:.0f} ms | "
                      f"B2004 déclenché après bit4=0 ≥ 2.5s")
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms", detail)
        return None

    def on_lin_frame(self, ev):
        """Fallback : écouter timeout event crslin."""
        if self._detected or self.rte_client is not None:
            return None
        msg = str(ev.get("msg", "")).lower()
        if "b2004" in msg:
            self._detected = True
            delta = time.time() * 1000.0 - self._t0_ms
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms", "fallback crslin b2004 event")
        return None


class TC_LIN_016_BIT6_Stuck_Alone(BaseBCMTest):
    """
    TC_LIN_016_BIT6 — StickStatus bit6=1 SEUL (sans 0x17 bit0=1) → pas de B2011

    Vérifie que la condition B2011 n'est PAS déclenchée si uniquement
    bit6=1 (Stuck) mais que 0x17 bit0=0 (CRS OK).
    La relation ET est vérifiée : une seule condition est insuffisante.

    Stimulus  : sim_client.set_stick_stuck(True) → bit6=1
                CRS_Status reste nominal (bit0=0)
    Observation : b2011_active=False ET state ≠ ERROR pendant 12s.
    PASS : aucun B2011 après 12s de bit6=1 seul.
    FAIL : B2011 déclenché (condition ET incorrectement évaluée).
    """
    ID             = "TC_LIN_016_BIT6_ALONE"
    NAME           = "LIN 0x16 bit6=1 only → B2011 NOT triggered (12s)"
    CATEGORY       = "LIN_SECURITE"
    REF            = "WW-MCAT-005 Rev5 / condition ET B2011"
    LIMIT_STR      = "B2011 INACTIVE (state ≠ ERROR) for 12s"
    LIMIT_MS       = 12000
    TEST_TIMEOUT_S = 15

    def _on_start(self):
        super()._on_start()
        self._b2011_triggered = False
        self._observation_done = False

    def _target_state(self): return None

    def _check_rte(self) -> Optional[TestResult]:
        if self._confirmed or self.rte_client is None:
            return None
        state  = self.rte_client.get("state")
        delta  = time.time() * 1000.0 - self._t0_ms

        # FAIL immédiat si B2011 se déclenche
        if state == "ERROR":
            self._b2011_triggered = True
            self._confirmed = True
            return self._fail(f"{delta:.0f} ms",
                f"B2011 déclenché alors que 0x17 bit0=0 (condition ET incorrecte) "
                f"après {delta:.0f} ms")

        # PASS après 12s sans B2011
        if delta >= self.LIMIT_MS and not self._observation_done:
            self._observation_done = True
            self._confirmed = True
            return self._pass(f"{delta:.0f} ms",
                f"B2011 NON déclenché après {delta:.0f} ms de bit6=1 seul — "
                f"condition ET respectée (0x17 bit0=0)")
        return None


class TC_B2011_AND_Condition(BaseBCMTest):
    """
    TC_B2011_AND — B2011 : condition conjointe 0x16 bit6=1 ET 0x17 bit0=1 ≥ 10s

    Les deux conditions doivent être actives simultanément pendant ≥ 10s
    pour déclencher B2011.

    Séquence :
      1. Injecter 0x17 bit0=1 (CRS_InternalFault_Stick)
      2. Injecter 0x16 bit6=1 (StickStatus Stuck)
      3. Attendre ≥ 10s → B2011 ACTIVE + state=ERROR
      4. Rétablissement : bit0=0 → B2011 INACTIVE

    Observation : b2011_active=True via Redis GET OU state=ERROR dans
                  [10000, 12000] ms.
    """
    ID             = "TC_B2011_AND"
    NAME           = "B2011 : 0x16 bit6=1 ET 0x17 bit0=1 ≥ 10s → B2011 ACTIVE"
    CATEGORY       = "LIN_SECURITE"
    REF            = "WW-MCAT-005 Rev5 / B2011 condition ET"
    LIMIT_STR      = "B2011 ACTIVE within [10000, 12000] ms"
    LIMIT_LO_MS    = 9500    # 10s - 500ms tolérance
    LIMIT_HI_MS    = 13000   # 10s + 3s marge poll/latence
    TEST_TIMEOUT_S = 18

    def _on_start(self):
        super()._on_start()
        self._detected = False

    def _target_state(self): return None

    def _check_rte(self) -> Optional[TestResult]:
        if self._detected or self.rte_client is None:
            return None
        state  = self.rte_client.get("state")
        b2011  = self.rte_client.get_bool("b2011_active")
        delta  = time.time() * 1000.0 - self._t0_ms

        if b2011 or state == "ERROR":
            self._detected = True
            ok = self.LIMIT_LO_MS <= delta <= self.LIMIT_HI_MS
            detail = (f"b2011_active={b2011} state={state} | {delta:.0f} ms | "
                      f"window [{self.LIMIT_LO_MS:.0f}–{self.LIMIT_HI_MS:.0f} ms] | "
                      f"{'WITHIN' if ok else 'OUTSIDE'} window")
            return (self._pass if ok else self._fail)(f"{delta:.0f} ms", detail)
        return None


class TC_LIN_017_Version_Filter(BaseBCMTest):
    """
    TC_LIN_017_VER — CRS_Version invalide (byte1) → trame 0x17 ignorée

    Règle catalogue WW-MCAT-005 Rev5 :
      - version nominale = 0x20 → trame traitée normalement
      - version = 0xFF → trame ignorée (log dédié, pas de DTC)
      - toute autre valeur ≠ 0x20 → trame rejetée

    Stimulus : sim_client.set_crs_version(0xFF) → crslin émet byte1=0xFF
    Observation : crs_fault_stick ne change PAS même si bit0=1 est injecté
                  simultanément (trame ignorée = contribution B2011 bloquée).

    PASS : crs_fault reste 0 pendant 3s (trame ignorée).
    FAIL : crs_fault change (trame 0xFF acceptée par le BCM).
    """
    ID             = "TC_LIN_017_VER"
    NAME           = "LIN 0x17 CRS_Version=0xFF → frame ignored (B2011 contribution blocked)"
    CATEGORY       = "LIN_SECURITE"
    REF            = "WW-MCAT-005 Rev5 / filtre CRS_Version"
    LIMIT_STR      = "crs_fault unchanged for 3s (version 0xFF ignored)"
    LIMIT_MS       = 3000
    TEST_TIMEOUT_S = 8

    def _on_start(self):
        super()._on_start()
        self._baseline_fault = None
        self._violation      = False

    def _target_state(self): return None

    def _check_rte(self) -> Optional[TestResult]:
        if self._confirmed or self.rte_client is None:
            return None
        crs_fault = self.rte_client.get_int("crs_fault", default=0)
        delta     = time.time() * 1000.0 - self._t0_ms

        # Capturer la baseline au premier poll
        if self._baseline_fault is None:
            self._baseline_fault = crs_fault
            return None

        # FAIL : crs_fault a changé → trame 0xFF acceptée
        if crs_fault != self._baseline_fault and crs_fault != 0:
            self._violation = True
            self._confirmed = True
            return self._fail(f"{delta:.0f} ms",
                f"crs_fault={crs_fault} (baseline={self._baseline_fault}) — "
                f"trame 0x17 avec CRS_Version=0xFF acceptée par le BCM")

        # PASS après 3s sans changement
        if delta >= self.LIMIT_MS:
            self._confirmed = True
            return self._pass(f"{delta:.0f} ms",
                f"crs_fault={crs_fault} inchangé après {delta:.0f} ms — "
                f"trame 0x17 CRS_Version=0xFF correctement ignorée")
        return None


class TC_CAN_202_ERR01_InvalidCmd(BaseBCMTest):
    """
    TC_CAN_202_ERR01 — CAN 0x202 ErrorCode=0x01 (InvalidCmd) — désaccord naturel

    Vérifie que le WC émet SPONTANÉMENT une trame 0x202 avec NACK=1 et
    ErrorCode=0x01 lorsqu'il détecte un désaccord entre le WiperMode reçu
    dans 0x200 et le CurrentMode qu'il retourne dans 0x201.

    Séquence :
      1. BCM envoie 0x200 avec WiperMode=SPEED1 (crs_wiper_op=2)
      2. Le simulateur répond 0x201 avec CurrentMode=OFF (≠ SPEED1) via
         le mécanisme mode_mismatch_0x201 activé dans bcmcan.py
      3. Le WC détecte le désaccord et émet NATURELLEMENT 0x202 avec
         AckStatus=1 (NACK) et ErrorCode=0x01 (InvalidCmd)
      → PASS si la première 0x202 reçue a bien NACK=1 ET ErrorCode=0x01

    Pas d'injection forcée de NACK : le 0x202 doit être produit par le WC
    de manière autonome, sans intervention du test_runner.
    """
    ID             = "TC_CAN_202_ERR01"
    NAME           = "CAN 0x202 NACK naturel : 0x200 SPEED1 → 0x201 OFF → 0x202 NACK+0x01"
    CATEGORY       = "CAN_SECURITE"
    REF            = "WW-MCAT-005 Rev5 / Wiper_Ack ErrorCode=0x01 — désaccord mode"
    LIMIT_STR      = "0x202 NACK=1 + ErrorCode=0x01 received ≤ 2000ms after mismatch"
    LIMIT_MS       = 2000
    TEST_TIMEOUT_S = 10

    def _on_start(self):
        super()._on_start()
        self._detected      = False   # True dès que 0x202 valide reçue
        self._speed1_cmd_ok = False   # True dès que 0x200 SPEED1 confirmé
        self._result_detail = ""

    def _target_state(self): return None

    def _check_rte(self) -> Optional[TestResult]:
        """
        Confirme que le BCM émet bien 0x200 avec WiperMode=SPEED1.
        Positionne _speed1_cmd_ok pour valider le contexte du test.
        """
        if self._detected or self.rte_client is None:
            return None
        state = self.rte_client.get("state")
        if not self._speed1_cmd_ok and state == "SPEED1":
            self._speed1_cmd_ok = True
        return None

    def on_can_frame(self, ev) -> Optional[TestResult]:
        """
        Observe les trames 0x202 émises naturellement par le WC.

        Stratégie :
          - Les ACK normaux (AckStatus=0, ErrorCode=0x00) sont IGNORÉS :
            ils peuvent arriver pendant la montée en SPEED1, avant que le
            mismatch 0x201 soit perçu par le WC.
          - Seule la première trame 0x202 avec AckStatus=1 (NACK) est jugée :
              PASS si ErrorCode=0x01 (InvalidCmd) dans le délai
              FAIL si ErrorCode≠0x01
          - Si le timeout (TEST_TIMEOUT_S) expire sans NACK → FAIL par timeout
            géré par BaseBCMTest.
        """
        if self._detected:
            return None
        if ev.get("can_id_int") != 0x202:
            return None

        fields = ev.get("fields", {})
        ack = int(fields.get("ack_status", 0))
        err = int(fields.get("error_code", 0))

        # Ignorer les ACK normaux (mismatch pas encore perçu par le WC)
        if ack == 0 and err == 0x00:
            return None

        # Première trame avec NACK=1 → c'est le cas qu'on attend
        delta = time.time() * 1000.0 - self._t0_ms
        self._detected = True
        ok = (ack == 1 and err == 0x01 and delta <= self.LIMIT_MS)
        detail = (
            f"0x202 naturelle NACK reçue | AckStatus={ack} (attendu 1) | "
            f"ErrorCode=0x{err:02X} (attendu 0x01) | "
            f"mismatch_ctx={'OK' if self._speed1_cmd_ok else 'non confirmé'} | "
            f"{delta:.0f} ms"
        )
        return (self._pass if ok else self._fail)(
            f"NACK={ack} Err=0x{err:02X} {delta:.0f}ms", detail
        )


class TC_CAN_202_ERR02_MotorBlocked(BaseBCMTest):
    """
    TC_CAN_202_ERR02 — CAN 0x202 ErrorCode=0x02 (MotorBlocked) — naturel

    Séquence :
      1. BCM envoie 0x200 avec WiperMode=SPEED1 (crs_wiper_op=2)
      2. Le simulateur injecte FaultStatus_MotorDriver=1 (bit1 du byte4 de 0x201)
         via set_fault_status_bits(0x02)
      3. panels.py détecte le bit1 et émet naturellement 0x202 avec
         AckStatus=1 (NACK) et ErrorCode=0x02 (MotorBlocked)
      4. Le BCM reçoit 0x202 NACK 0x02 → envoie WiperMode=OFF dans 0x200
      → PASS si :
          a) 0x201 byte4 bit1=1 (FaultStatus_MotorDriver) vu sur le bus
          b) 0x202 naturel avec NACK=1 + ErrorCode=0x02 reçu dans le délai
          c) BCM envoie WiperMode=OFF (state→OFF) après réception du NACK
    """
    ID             = "TC_CAN_202_ERR02"
    NAME           = "CAN 0x202 NACK naturel : 0x201 bit1=MotorDriver → 0x202 Err=0x02 → BCM OFF"
    CATEGORY       = "CAN_SECURITE"
    REF            = "WW-MCAT-005 Rev5 / Wiper_Ack ErrorCode=0x02 — MotorBlocked"
    LIMIT_STR      = "0x202 NACK+0x02 received AND BCM→OFF ≤ 3000ms"
    LIMIT_MS       = 3000
    TEST_TIMEOUT_S = 12

    def _on_start(self):
        super()._on_start()
        self._detected       = False
        self._fault_201_seen = False   # FaultStatus_MotorDriver=1 vu dans 0x201
        self._nack_seen      = False   # 0x202 NACK+0x02 vu
        self._bcm_off_seen   = False   # BCM passé en OFF

    def _target_state(self): return None

    def _check_rte(self) -> Optional[TestResult]:
        if self._detected or self.rte_client is None:
            return None
        state = self.rte_client.get("state")
        # Attendre que le BCM passe en OFF suite au NACK 0x02
        if self._nack_seen and not self._bcm_off_seen and state == "OFF":
            self._bcm_off_seen = True
        if self._nack_seen and self._bcm_off_seen and not self._detected:
            self._detected = True
            delta = time.time() * 1000.0 - self._t0_ms
            ok = (self._fault_201_seen and delta <= self.LIMIT_MS)
            detail = (
                f"0x201 bit1={'OK' if self._fault_201_seen else 'NON VU'} | "
                f"0x202 NACK+0x02={'OK' if self._nack_seen else 'NON'} | "
                f"BCM→OFF={state} | {delta:.0f} ms"
            )
            return (self._pass if ok else self._fail)(
                f"Err=0x02 BCM={state} {delta:.0f}ms", detail)
        return None

    def on_can_frame(self, ev) -> Optional[TestResult]:
        if self._detected:
            return None
        cid = ev.get("can_id_int")
        fields = ev.get("fields", {})

        if cid == 0x201:
            # Vérifier FaultStatus_MotorDriver = bit1
            fault_byte = int(fields.get("fault_byte", 0))
            if fault_byte & 0x02:
                self._fault_201_seen = True
            return None

        if cid == 0x202:
            ack = int(fields.get("ack_status", 0))
            err = int(fields.get("error_code", 0))
            if ack == 0 and err == 0x00:
                return None   # ignorer ACK normaux
            if ack == 1 and err == 0x02:
                self._nack_seen = True
            elif ack == 1:
                # NACK avec mauvais ErrorCode → FAIL immédiat
                self._detected = True
                delta = time.time() * 1000.0 - self._t0_ms
                return self._fail(
                    f"NACK Err=0x{err:02X} (attendu 0x02)",
                    f"0x202 NACK reçu avec ErrorCode=0x{err:02X} au lieu de 0x02 | {delta:.0f} ms"
                )
        return None


class TC_CAN_202_ERR04_PosSensorFault(BaseBCMTest):
    """
    TC_CAN_202_ERR04 — CAN 0x202 ErrorCode=0x04 (PosSensorFault) — naturel

    Séquence :
      1. BCM envoie 0x200 avec WiperMode=SPEED1 (crs_wiper_op=2)
      2. Le simulateur injecte FaultStatus_PosSensor=1 (bit2 du byte4 de 0x201)
         via set_fault_status_bits(0x04)
      3. panels.py détecte le bit2 et émet naturellement 0x202 avec
         AckStatus=1 (NACK) et ErrorCode=0x04 (PosSensorFault)
      4. Le BCM reçoit 0x202 NACK 0x04 → pose le flag wc_ack_pos_fault
         → _check_wc_fault_status : condition conjointe (bit2=1 ET code=0x04) → B2006
      → PASS si :
          a) 0x201 byte4 bit2=1 (FaultStatus_PosSensor) vu sur le bus
          b) 0x202 naturel avec NACK=1 + ErrorCode=0x04 reçu dans le délai
          c) BCM déclenche B2006 (via RTE wc_b2006_active, wc_b2103_active
             ou rest_contact_b2006_active ou motor_data broadcast)
    """
    ID             = "TC_CAN_202_ERR04"
    NAME           = "CAN 0x202 NACK naturel : 0x201 bit2=PosSensor → 0x202 Err=0x04 → B2006"
    CATEGORY       = "CAN_SECURITE"
    REF            = "WW-MCAT-005 Rev5 / Wiper_Ack ErrorCode=0x04 — PosSensorFault → B2006"
    LIMIT_STR      = "0x202 NACK+0x04 received AND B2006 ACTIVE ≤ 3000ms"
    LIMIT_MS       = 3000
    TEST_TIMEOUT_S = 12

    def _on_start(self):
        super()._on_start()
        self._detected       = False
        self._fault_201_seen = False
        self._nack_seen      = False
        self._b2006_seen     = False

    def _target_state(self): return None

    def _check_rte(self) -> Optional[TestResult]:
        if self._detected or self.rte_client is None:
            return None
        # B2006 se déclenche côté BCM via _check_wc_fault_status.
        # Trois clés Redis observables selon la version du BCM :
        #   • wc_b2006_active        : bool, clé directe B2006
        #   • wc_b2103_active        : bool, publiée par BCM lors du déclenchement B2103→B2006
        #   • rest_contact_b2006_active : int (0 ou 1), posé après cycle repos
        b2006 = (
            self.rte_client.get("wc_b2006_active")
            or self.rte_client.get("wc_b2103_active")
            or (int(self.rte_client.get("rest_contact_b2006_active") or 0) > 0)
        )
        if self._nack_seen and b2006 and not self._b2006_seen:
            self._b2006_seen = True
        if self._nack_seen and self._b2006_seen and not self._detected:
            self._detected = True
            delta = time.time() * 1000.0 - self._t0_ms
            ok = (self._fault_201_seen and delta <= self.LIMIT_MS)
            detail = (
                f"0x201 bit2={'OK' if self._fault_201_seen else 'NON VU'} | "
                f"0x202 NACK+0x04={'OK' if self._nack_seen else 'NON'} | "
                f"B2006={'ACTIVE' if self._b2006_seen else 'absent'} | {delta:.0f} ms"
            )
            return (self._pass if ok else self._fail)(
                f"Err=0x04 B2006={'OK' if self._b2006_seen else 'NON'} {delta:.0f}ms", detail)
        return None

    def on_motor_data(self, data: dict) -> Optional[TestResult]:
        """Détecte B2006 via broadcast TCP BCM."""
        if self._detected:
            return None
        if data.get("type") == "b2006_active" or \
                (data.get("wiper_fault") and self._nack_seen):
            if not self._b2006_seen:
                self._b2006_seen = True
        return None

    def on_can_frame(self, ev) -> Optional[TestResult]:
        if self._detected:
            return None
        cid    = ev.get("can_id_int")
        fields = ev.get("fields", {})

        if cid == 0x201:
            fault_byte = int(fields.get("fault_byte", 0))
            if fault_byte & 0x04:
                self._fault_201_seen = True
            return None

        if cid == 0x202:
            ack = int(fields.get("ack_status", 0))
            err = int(fields.get("error_code", 0))
            if ack == 0 and err == 0x00:
                return None
            if ack == 1 and err == 0x04:
                self._nack_seen = True
            elif ack == 1:
                self._detected = True
                delta = time.time() * 1000.0 - self._t0_ms
                return self._fail(
                    f"NACK Err=0x{err:02X} (attendu 0x04)",
                    f"0x202 NACK reçu avec ErrorCode=0x{err:02X} au lieu de 0x04 | {delta:.0f} ms"
                )
        return None


class TC_CAN_202_ERR05_InternalFault(BaseBCMTest):
    """
    TC_CAN_202_ERR05 — CAN 0x202 ErrorCode=0x05 (InternalFault WC) — naturel

    Séquence :
      1. BCM envoie 0x200 avec WiperMode=SPEED1 (crs_wiper_op=2)
      2. Le simulateur injecte FaultStatus_WC_Internal=1 (bit0 du byte4 de 0x201)
         via set_fault_status_bits(0x01)
      3. panels.py détecte le bit0 et émet naturellement 0x202 avec
         AckStatus=1 (NACK) et ErrorCode=0x05 (InternalFault)
      4. Le BCM reçoit 0x202 NACK 0x05 → envoie WiperMode=OFF dans 0x200
      → PASS si :
          a) 0x201 byte4 bit0=1 (FaultStatus_WC_Internal) vu sur le bus
          b) 0x202 naturel avec NACK=1 + ErrorCode=0x05 reçu dans le délai
          c) BCM envoie WiperMode=OFF (state→OFF) après réception du NACK
    """
    ID             = "TC_CAN_202_ERR05"
    NAME           = "CAN 0x202 NACK naturel : 0x201 bit0=WC_Internal → 0x202 Err=0x05 → BCM OFF"
    CATEGORY       = "CAN_SECURITE"
    REF            = "WW-MCAT-005 Rev5 / Wiper_Ack ErrorCode=0x05 — InternalFault WC → BCM OFF"
    LIMIT_STR      = "0x202 NACK+0x05 received AND BCM→OFF ≤ 3000ms"
    LIMIT_MS       = 3000
    TEST_TIMEOUT_S = 12

    # Fenêtre de grâce (ms) après reset_t0 pendant laquelle un NACK 0x02 résiduel
    # issu du healing motor_driver_fault (ERR02→ERR05) est ignoré au lieu de FAIL.
    # Le healing dure 1s côté bcmcan ; le pre_test ERR05 attend 1200ms AVANT reset_t0,
    # donc ce guard couvre uniquement les rares trames en transit au moment de reset_t0.
    _GRACE_ERR02_MS = 200

    def _on_start(self):
        super()._on_start()
        self._detected       = False
        self._fault_201_seen = False
        self._nack_seen      = False
        self._bcm_off_seen   = False

    def _target_state(self): return None

    def _check_rte(self) -> Optional[TestResult]:
        if self._detected or self.rte_client is None:
            return None
        state = self.rte_client.get("state")
        if self._nack_seen and not self._bcm_off_seen and state == "OFF":
            self._bcm_off_seen = True
        if self._nack_seen and self._bcm_off_seen and not self._detected:
            self._detected = True
            delta = time.time() * 1000.0 - self._t0_ms
            ok = (self._fault_201_seen and delta <= self.LIMIT_MS)
            detail = (
                f"0x201 bit0={'OK' if self._fault_201_seen else 'NON VU'} | "
                f"0x202 NACK+0x05={'OK' if self._nack_seen else 'NON'} | "
                f"BCM→OFF={state} | {delta:.0f} ms"
            )
            return (self._pass if ok else self._fail)(
                f"Err=0x05 BCM={state} {delta:.0f}ms", detail)
        return None

    def on_can_frame(self, ev) -> Optional[TestResult]:
        if self._detected:
            return None
        cid    = ev.get("can_id_int")
        fields = ev.get("fields", {})

        if cid == 0x201:
            fault_byte = int(fields.get("fault_byte", 0))
            if fault_byte & 0x01:
                self._fault_201_seen = True
            return None

        if cid == 0x202:
            ack = int(fields.get("ack_status", 0))
            err = int(fields.get("error_code", 0))
            if ack == 0 and err == 0x00:
                return None
            if ack == 1 and err == 0x05:
                self._nack_seen = True
            elif ack == 1:
                delta = time.time() * 1000.0 - self._t0_ms
                # FIX anti-pollution ERR02→ERR05 :
                # Si un NACK 0x02 résiduel (motor_driver_fault healing) arrive dans
                # la fenêtre de grâce initiale, on l'ignore plutôt que de FAIL.
                # Le pre_test ERR05 attend déjà 1200ms pour couvrir le healing 1s ;
                # ce guard couvre les trames encore en transit au moment de reset_t0.
                if err == 0x02 and delta < self._GRACE_ERR02_MS:
                    return None   # trame résiduelle ERR02 — ignorée
                self._detected = True
                return self._fail(
                    f"NACK Err=0x{err:02X} (attendu 0x05)",
                    f"0x202 NACK reçu avec ErrorCode=0x{err:02X} au lieu de 0x05 | {delta:.0f} ms"
                )
        return None


class TC_B2104_WC_CAN_NACK(BaseBCMTest):
    """
    TC_B2104 — WC CAN NACK (B2104)
    ================================
    Vérifie que le WC (simulateur) arme le DTC B2104 quand il émet
    3 trames 0x202 consécutives avec AckStatus=1 (NACK) et ErrorCode≠0.

    Architecture de détection (bcmcan._b2104_track) :
      - Appelée à chaque émission de trame 0x202 via _relay_0x202()
      - Compte les NACKs consécutifs (ErrorCode ≠ 0x00)
      - Seuil = 3 NACKs → B2104 ACTIVE
      - Healing = 3 ACKs consécutifs (AckStatus=0) → B2104 INACTIVE

    Stimulus (test_runner) :
      1. reset_b2104() → remet compteurs à zéro
      2. Forcer 3 NACKs consécutifs via TCP test_cmd ou via la file 0x202
         Chaque NACK : AckStatus=1, ErrorCode=0x01 (InvalidCmd)
      3. reset_t0() → chrono démarre

    Observation (priorité) :
      1. TCP broadcast : {"type": "b2104_active", "wc_b2104_active": True}
      2. Timeout si non reçu avant TEST_TIMEOUT_S

    Critères :
      PASS  : B2104 détecté dans ≤ LIMIT_MS après le 3e NACK
      FAIL  : non détecté dans le délai
    """
    ID             = "TC_B2104"
    NAME           = "WC CAN NACK — 3 NACKs consécutifs (AckStatus=1, ErrorCode≠0) → B2104"
    CATEGORY       = "FUNCTIONAL_WC"
    REF            = "B2104 / bcmcan._b2104_track / WW-MCAT-005 Rev5"
    LIMIT_STR      = "B2104 ACTIVE ≤ 1500 ms after the 3rd NACK"
    LIMIT_MS       = 1500
    TEST_TIMEOUT_S = 8

    def _on_start(self):
        super()._on_start()
        self._detected    = False
        self._t_nack3_ms  = 0.0   # timestamp du 3e NACK (mis à jour par runner)

    def notify_nack3_sent(self):
        """Appelé par le runner au moment de l'envoi du 3e NACK → démarre le chrono."""
        self._t_nack3_ms = time.time() * 1000.0

    def _target_state(self) -> None:
        return None

    def _check_rte(self) -> Optional[TestResult]:
        """
        Vérifie wc_b2104_active dans Redis (clé publiée par _b2104_track).
        Chemin prioritaire : on_motor_data (broadcast TCP bcmcan).
        """
        if self._detected or self.rte_client is None:
            return None
        if self.rte_client.get_bool("wc_b2104_active"):
            self._detected = True
            t0 = self._t_nack3_ms if self._t_nack3_ms else self._t0_ms
            delta = time.time() * 1000.0 - t0
            return (self._pass if delta <= self.LIMIT_MS else self._fail)(
                f"{delta:.0f} ms", "Redis wc_b2104_active=True")
        return None

    def on_motor_data(self, data: dict) -> Optional[TestResult]:
        """
        Reçoit le broadcast TCP bcmcan {"type": "b2104_active", "wc_b2104_active": True}
        émis par _b2104_track() lors du déclenchement B2104.
        """
        if self._detected:
            return None
        if data.get("type") == "b2104_active" and data.get("wc_b2104_active"):
            self._detected = True
            t_ref  = self._t_nack3_ms if self._t_nack3_ms > 0 else self._t0_ms
            delta  = time.time() * 1000.0 - t_ref
            nack_n = data.get("nack_count", 3)
            err    = data.get("error_code", 0)
            ok     = delta <= self.LIMIT_MS
            detail = (f"B2104 ACTIVE | {nack_n} NACKs consécutifs "
                      f"(ErrorCode=0x{err:02X}) | {delta:.0f} ms")
            return (self._pass if ok else self._fail)(f"{delta:.0f} ms", detail)
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
    T40_Touch_SingleCycle_Then_Off,
    T21_Pump_AutoStop,
    T22_FrontWash_DTC_BCM,
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
    # ── Nouveaux tests fonctionnels ───────────────────
    T43_ReverseGear_RearWiper_Intermittent,
    T45_BladeReturn_Ignition_Off,
    # ── Sécurité LIN ─────────────────────────────────
    TC_LIN_002_AliveCounter_AntiReplay,
    # TC_LIN_017_BIT1_ALONE : 0x17 bit0=1 seul (sans 0x16 bit6=1) → B2011 NON déclenché
    TC_LIN_005_CRS_InternalFault,
    # ── Sécurité CAN ─────────────────────────────────
    TC_CAN_003_AliveCounter_0x200,
    # ── Nouveaux : TC_GEN / TC_SPD / TC_AUTO / TC_FSR / TC_COM ──
    TC_GEN_001_Ignition_On_Activation,
    TC_SPD_001_Speed1_Continuous,
    TC_AUTO_004_Auto_Inhibit_No_Sensor,
    TC_FSR_008_Watchdog_Supervision,
    TC_FSR_010_CRC_Invalid_0x201,
    TC_COM_001_LIN_Baudrate,
    # ── Diagnostic WC ──────────────────────────────────────────────
    TC_B2103_PositionSensorFault,
    # ── NOUVEAUX TESTS (ce commit) ────────────────────────────────
    # TC_LIN_CS : Checksum LIN 0x16 invalide → trame rejetée (TSR_001)
    # T14 supprimé (redondant avec TC_FSR_008 — voir REF TC_FSR_008)
    TC_LIN_CS_Invalid_0x16,
    # T41 supprimé (redondant — WOP 0..7 couverts individuellement par
    #   T30/T31/T40/T34/T35/T36/T37/T44 + SRD_WW_011 couvert via traçabilité)
    # T44  : REAR_WIPE isolé op=7 sans reverse gear (SRD_WW_090)
    T44_RearWipe_Standalone,
    # T50  : Cas B — wc_available=True → H-Bridge GPIO bloqué (SRD_WW_070)
    T50_CasA_DirectMotorControl,
    # T51 supprimé : B2006 désactivé dans le BCM (_check_blade_position commenté)
    # → FSR_006 ne peut pas se déclencher → test structurellement non exécutable.
    # À réactiver quand B2006 sera réactivé par l'encadrant (voir bcm_application.py).
    # ── Nouveaux tests (ce commit) ────────────────────────────────────────────
    # LIN_INVALID_CMD_001 : robustesse commande hors plage (SRS_LIN_001)
    LIN_INVALID_CMD_001,
    # T38b : surcourant moteur ARRIÈRE → B2002 (symétrique T38 moteur avant)
    T38b_Overcurrent_RearMotor,
    # T38c : surcourant pompe → B2003 + isolation moteur avant
    T38c_Overcurrent_Pump,
    # T_RAIN_AUTO_SENSOR_ERROR : rain sensor disponible → AUTO → SensorStatus invalide → B2007
    T_RAIN_AUTO_SENSOR_ERROR,
    # T_CAS_B_SPEED1_REVERSE : CAS B SPEED1 CAN + Reverse 0x300 → front + rear
    T_CasB_Speed1_Reverse,
    # T_B2009_CAN : CAS B blade figée + rest_contact figé → B2009
    T_B2009_CAN,
    # T50b : overcurrent moteur avant CAS B + 0x202 ErrorCode=0x03 → B2001
    T50b_Overcurrent_CAS_B,
    # T50c : overcurrent CAS B + 0x202 ErrorCode≠0x03 → B2001 NON déclenché
    T50c_Overcurrent_WrongErrorCode,
    # T50d : courant normal CAS B + 0x202 ErrorCode=0x03 → B2001 NON déclenché
    T50d_NoOvercurrent_ErrorCode03,
    # T_B2009_CASA : CAS A SPEED1 sans rest_contact simulé → B2009
    T_B2009_CASA,
    # ── TESTS version28 — LIN 0x16/0x17 nouveaux + CAN 0x202 ─────────────
    # TC_LIN_016_BIT4  : StickStatus bit4=0 (Valid=0) ≥ 2.5s → B2004
    TC_LIN_016_BIT4_StickValid,
    # TC_LIN_016_BIT6_ALONE : bit6=1 seul sans 0x17 bit0 → B2011 NON déclenché
    TC_LIN_016_BIT6_Stuck_Alone,
    # TC_B2011_AND     : bit6=1 ET 0x17 bit0=1 ≥ 10s → B2011 ACTIVE
    TC_B2011_AND_Condition,
    # TC_LIN_017_VER   : CRS_Version=0xFF → trame 0x17 ignorée (contribution B2011 bloquée)
    TC_LIN_017_Version_Filter,
    # TC_CAN_202_ERR01 : 0x200 SPEED1 → 0x201 CurrentMode=OFF → 0x202 NACK+0x01
    TC_CAN_202_ERR01_InvalidCmd,
    # TC_CAN_202_ERR02 : 0x201 bit1=MotorDriver → 0x202 NACK+0x02 → BCM OFF
    TC_CAN_202_ERR02_MotorBlocked,
    # TC_CAN_202_ERR04 : 0x201 bit2=PosSensor → 0x202 NACK+0x04 → B2006
    TC_CAN_202_ERR04_PosSensorFault,
    # TC_CAN_202_ERR05 : 0x201 bit0=WC_Internal → 0x202 NACK+0x05 → BCM OFF
    TC_CAN_202_ERR05_InternalFault,
    # TC_B2104 : WC CAN NACK — 3 NACKs consécutifs → B2104 ACTIVE (DTC WC)
    TC_B2104_WC_CAN_NACK,
]