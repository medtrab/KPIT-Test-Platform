"""
sim_client.py  —  Client TCP vers le RPi Simulateur (port 5000)
================================================================
Permet à la Platform Qt d'envoyer des commandes d'injection de défauts
directement au RPi Simulateur, qui les applique physiquement via GPIO
sur la POMPE uniquement.

Architecture :
  PC (Platform Qt)
  └── SimClient  ─── TCP → RPi Simulateur :5000
                        └── sim_control.py → GPIO
                              ├── PIN_ISO   (GPIO18)  -- protection ADS1115
                              ├── PIN_MUX_A (GPIO6), PIN_MUX_B (GPIO13)
                              ├── PIN_Y0_GATE (GPIO5)  -- OPEN LOAD / SHORT TO GND
                              ├── PIN_Y2_BASE (GPIO16) -- SHORT TO VCC
                              └── PIN_VLOAD_PWM (GPIO26) -- VARIABLE LOAD

  [POMPE_ONLY] Cible MOTEUR = lecture seule via POT (ADS1115 A3)
               Aucun signal GPIO ne va vers le moteur depuis sim_control.

Commandes supportées (JSON sur TCP port 5000) :
  {"pump_fault_mode": "NORMAL"}
  {"pump_fault_mode": "OPEN LOAD"}
  {"pump_fault_mode": "SHORT TO VCC"}
  {"pump_fault_mode": "SHORT TO GND"}
  {"pump_fault_mode": "VARIABLE LOAD", "duty_cycle": 50.0}
  {"pump_fault_target": "PUMP"}   (MOTOR accepté mais ignoré = pas d'injection)
"""

import json
import socket
import threading


class SimClient:
    """
    Client TCP léger vers le RPi Simulateur.
    Toutes les méthodes sont non-bloquantes (timeout 2s).
    Si le simulateur est injoignable, les appels sont silencieusement ignorés.
    """

    def __init__(self, host: str = "", port: int = 5000):
        self._host = host
        self._port = port
        self._lock = threading.Lock()
        self._connected = False

    # ----------------------------------------------------------
    # Connexion / état
    # ----------------------------------------------------------

    def connect(self, host: str, port: int = 5000) -> bool:
        """
        Enregistre l'hôte du simulateur.
        La connexion est établie à chaque envoi (TCP stateless léger).
        """
        with self._lock:
            self._host = host
            self._port = port
            self._connected = bool(host)
        if self._connected:
            print(f"[SIM-CLIENT] Simulateur configuré: {host}:{port}")
        return self._connected

    def disconnect(self):
        with self._lock:
            self._host = ""
            self._connected = False
        print("[SIM-CLIENT] Déconnecté")

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected and bool(self._host)

    @property
    def host(self) -> str:
        with self._lock:
            return self._host

    # ----------------------------------------------------------
    # Envoi de commandes
    # ----------------------------------------------------------

    def send_fault(self, pump_fault_mode: str = None,
                   pump_fault_target: str = None,
                   duty_cycle: float = None) -> bool:
        """
        Envoie une commande d'injection de défaut au Simulateur.

        Args:
            pump_fault_mode:   "NORMAL" | "OPEN LOAD" | "SHORT TO VCC" |
                               "VARIABLE LOAD" | "SHORT TO GND"
            pump_fault_target: "PUMP"  (MOTOR = lecture seule via POT, pas d'injection)
            duty_cycle:        0.0 .. 100.0  -- duty PWM pour VARIABLE LOAD uniquement

        Returns:
            True si la commande a été envoyée avec succès.
        """
        if not self.is_connected():
            return False

        payload = {}
        if pump_fault_mode is not None:
            payload["pump_fault_mode"] = pump_fault_mode
        if pump_fault_target is not None:
            payload["pump_fault_target"] = pump_fault_target
        if duty_cycle is not None:
            payload["duty_cycle"] = round(float(duty_cycle), 1)

        if not payload:
            return False

        return self._send(payload)

    def send_blade_sim(self, value: float) -> bool:
        """
        Injecte une position cible (blade_sim) dans bcmcan du simulateur WC.
        value = -1.0  -> desactive la comparaison (aucun test B2103 actif).
        value = 0..100 -> active la comparaison avec blade_real (potentiometre).
        Un ecart |blade_real - value| > 10 % pendant > 1 000 ms declenche B2103.
        """
        if not self.is_connected():
            return False
        return self._send({"test_cmd": "set_blade_sim",
                           "value":    round(float(value), 1)})

    def reset_b2103(self) -> bool:
        """
        Reinitialise cote simulateur :
          - guard anti-rearmement _b2103_active = False
          - timer interne _b2103_mismatch_start = 0.0
          - blade_sim = -1.0 (comparaison desactivee)
        A appeler avant et apres chaque execution de TC_B2103.
        """
        if not self.is_connected():
            return False
        return self._send({"test_cmd": "reset_b2103"})

    def reset_b2101(self) -> bool:
        """
        Reinitialise B2101 (CAN Timeout WC) cote simulateur bcmcan :
          - _t_last_0x200 = time.time() → elapsed = 0 → B2101 se désactive
          - _can_timeout_b2011 = False
          - DTC B2101 mis inactive via wc_doip._dtc_mgr
        A appeler dans le post_test T50 apres que wc_available=False
        soit remis, pour eviter que B2101 persiste sur les tests suivants.
        """
        if not self.is_connected():
            return False
        return self._send({"test_cmd": "reset_b2101"})

    def reset_corrupt_crc(self) -> bool:
        """
        Remet _corrupt_crc_count=0 côté simulateur bcmcan (TC_FSR_010 cleanup).
        Utilise une connexion TCP directe (sim_client) plutôt que motor_w.queue_send
        pour éviter que count=0 survive à une déconnexion TCP et arrive après le
        count=8 d'un run suivant — ce qui produisait 0 trames corrompues → TIMEOUT.
        """
        if not self.is_connected():
            return False
        return self._send({"test_cmd": "corrupt_crc_0x201", "count": 0})

    def set_motor_driver_fault(self, value: bool) -> bool:
        """
        Injecte ou retire un défaut driver moteur B2102 côté simulateur bcmcan.
        value=True  → arme B2102 (WC Motor Driver Fault)
        value=False → retire le signal (DTC reste actif jusqu'au ClearDTC UDS)
        """
        if not self.is_connected():
            return False
        return self._send({"test_cmd": "set_motor_driver_fault", "value": value})

    # ── Nouveaux test_cmd pour T50 et T_B2009_CAN ────────────────────────────

    def start_blade_cycling(self, period_ms: float = 1500.0) -> bool:
        """
        T50 : démarre l'oscillation BladePosition 1↔99 dans bcmcan
        toutes les period_ms. Empêche B2009 (blade_pos>0 constant sans
        rester figé). A appeler après que le moteur soit commandé via CAN.
        """
        if not self.is_connected():
            return False
        return self._send({"test_cmd": "start_blade_cycling",
                           "period_ms": float(period_ms)})

    def stop_blade_cycling(self) -> bool:
        """T50 cleanup : arrête l'oscillation BladePosition et remet à 0."""
        if not self.is_connected():
            return False
        return self._send({"test_cmd": "stop_blade_cycling"})

    def freeze_blade_position(self, value: float = 50.0) -> bool:
        """
        T_B2009_CAN : gèle BladePosition à value% dans la trame 0x201.
        Simule une lame mécaniquement bloquée. value doit être >0 pour
        que front_motor_running=True en CAS B et déclencher B2009.
        """
        if not self.is_connected():
            return False
        return self._send({"test_cmd": "freeze_blade_position",
                           "value": float(value)})

    def unfreeze_blade_position(self) -> bool:
        """T_B2009_CAN cleanup : libère BladePosition (retour lecture ADS)."""
        if not self.is_connected():
            return False
        return self._send({"test_cmd": "unfreeze_blade_position"})

    def inject_motor_current(self, value: float = 0.95) -> bool:
        """
        T50b : force MotorCurrent dans la trame 0x201 pour simuler
        un surcourant moteur avant via CAN → déclenche B2001 côté BCM.
        """
        if not self.is_connected():
            return False
        return self._send({"test_cmd": "inject_motor_current",
                           "value": float(value)})

    def reset_motor_current(self) -> bool:
        """T50b cleanup : annule l'override MotorCurrent (retour lecture ADS)."""
        if not self.is_connected():
            return False
        return self._send({"test_cmd": "reset_motor_current"})

    # ── Nouvelles méthodes — LIN 0x17 CRS_Status (version28) ──────────────

    def set_crs_version(self, version: int) -> bool:
        """
        Injecte une valeur CRS_Version (byte1 de trame LIN 0x17).
        version=0x20 : valeur nominale (BCM accepte la trame).
        version=0xFF : valeur invalide → BCM ignore la trame (filtre activé).
        Toute autre valeur ≠ 0x20 → trame rejetée par le BCM.
        """
        if not self.is_connected():
            return False
        return self._send({"set_crs_version": int(version) & 0xFF})

    def reset_crs_version(self) -> bool:
        """Remet CRS_Version à la valeur nominale 0x20."""
        return self.set_crs_version(0x20)

    def set_crs_fault(self, fault_bits: int) -> bool:
        """
        Injecte un état de faute CRS dans la trame LIN 0x17 (byte0).
        fault_bits : combinaison de bits 0-2 :
          0x01 = CRS_InternalFault_Stick  (bit0) → contribue à B2011
          0x02 = CRS_InternalFault_Supply (bit1)
          0x04 = CRS_InternalFault_Comms  (bit2)
          0x00 = pas de faute (nominal)
        """
        if not self.is_connected():
            return False
        return self._send({"set_fault": int(fault_bits) & 0x07})

    def clear_crs_fault(self) -> bool:
        """Efface toutes les fautes CRS (remet byte0=0x00)."""
        return self.set_crs_fault(0x00)

    # ── Nouvelles méthodes — LIN 0x16 StickStatus Stuck (version28) ────────

    def set_stick_stuck(self, stuck: bool) -> bool:
        """
        Active/désactive le bit Stuck (bit2 de StickStatus = bit6 de byte0 trame 0x16).
        stuck=True  → bit6=1 : levier coincé — contribue à B2011 (si 0x17 bit0=1 aussi)
        stuck=False → bit6=0 : fonctionnement normal
        Timer B2011 = 10s de bit6=1 simultanément avec 0x17 bit0=1.
        """
        if not self.is_connected():
            return False
        return self._send({"set_stuck": bool(stuck)})

    # ── Nouvelles méthodes — CAN 0x202 ErrorCode injection (version28) ─────

    def send_wiper_ack(self, ack_status: int, error_code: int,
                       alive: int = 0) -> bool:
        """
        Envoie une trame Wiper_Ack (0x202) au BCM via la plateforme.
        ack_status : 0=ACK, 1=NACK
        error_code : 0x00-0x07 (selon catalogue WW-MCAT-005 Rev5)
          0x00 : No Error    0x01 : InvalidCmd   0x02 : MotorBlocked
          0x03 : Overcurrent 0x04 : PosSensorFault 0x05 : InternalFault
          0x06 : SupplyFault 0x07 : Busy
        """
        if not self.is_connected():
            return False
        alive   = alive & 0xFF
        b0      = ack_status & 0x01
        b1      = error_code & 0xFF
        b2      = alive
        crc     = (b0 ^ b1 ^ b2) & 0xFF
        payload = {
            "can_id_int": 0x202,
            "fields": {
                "ack_status": b0,
                "error_code": b1,
                "alive":      b2,
                "crc":        crc,
            }
        }
        return self._send(payload)

    def set_fault_status_bits(self, bits: int) -> bool:
        """
        Force le FaultStatus (byte4) de la trame 0x201 émise par le simulateur.
        bits : masque des bits à activer (OR) :
          0x01 = bit0 WC_Internal  → panels.py émet 0x202 ErrorCode=0x05
          0x02 = bit1 MotorDriver  → panels.py émet 0x202 ErrorCode=0x02
          0x04 = bit2 PosSensor    → panels.py émet 0x202 ErrorCode=0x04
        Utiliser avant d'activer wc_available pour que la 1ère trame 0x201
        porte déjà le défaut et génère le bon ErrorCode dans 0x202.
        """
        if not self.is_connected():
            return False
        return self._send({"test_cmd": "set_fault_status_bits",
                           "bits": int(bits) & 0x3F})

    def reset_fault_status_bits(self) -> bool:
        """Post_test : remet FaultStatus=0x00 dans les trames 0x201."""
        if not self.is_connected():
            return False
        return self._send({"test_cmd": "reset_fault_status_bits"})

    def set_xcp_internal_fault(self, value: bool = True) -> bool:
        """
        Déclenche B2101 (WC Internal Fault) côté simulateur.
        → FaultStatus_WC_Internal bit0=1 dans 0x201 automatiquement via get_fault_byte_for_tx()
        → panels.py émet naturellement 0x202 NACK + ErrorCode=0x05
        → BCM envoie WiperMode=OFF dans 0x200
        """
        if not self.is_connected():
            return False
        return self._send({"test_cmd": "set_xcp_internal_fault", "value": value})

    def set_xcp_position_sensor_fault(self, value: bool = True) -> bool:
        """
        Déclenche B2103 (WC Position Sensor Fault) côté simulateur.
        → FaultStatus_PosSensor bit2=1 dans 0x201 automatiquement via get_fault_byte_for_tx()
        → panels.py émet naturellement 0x202 NACK + ErrorCode=0x04
        → BCM pose wc_ack_pos_fault=True → condition conjointe → B2006
        """
        if not self.is_connected():
            return False
        return self._send({"test_cmd": "set_xcp_position_sensor_fault", "value": value})

    def set_mode_mismatch(self) -> bool:
        """
        Active le désaccord CurrentMode dans 0x201 (TC_CAN_202_ERR01).
        Après activation, _build_0x201 retourne CurrentMode=0x00 (OFF)
        quel que soit le WiperMode reçu dans 0x200 → le WC détecte le
        désaccord et émet naturellement 0x202 avec NACK=1 + ErrorCode=0x01.
        """
        if not self.is_connected():
            return False
        return self._send({"test_cmd": "set_mode_mismatch_0x201"})

    def reset_mode_mismatch(self) -> bool:
        """
        Désactive le désaccord CurrentMode (post_test TC_CAN_202_ERR01).
        Restaure le comportement normal : 0x201 reflète le mode de 0x200.
        """
        if not self.is_connected():
            return False
        return self._send({"test_cmd": "reset_mode_mismatch_0x201"})

    def reset_b2104(self) -> bool:
        """
        Réinitialise B2104 (WC CAN NACK) côté simulateur bcmcan :
          - Remet _b2104_nack_count = 0
          - Remet _b2104_ack_count  = 0
          - Remet _b2104_active     = False
          - Met B2104 INACTIVE via wc_doip._dtc_mgr
        À appeler avant et après chaque test TC_B2104.
        """
        if not self.is_connected():
            return False
        return self._send({"test_cmd": "reset_b2104"})

    def _send(self, payload: dict) -> bool:
        """Envoi TCP non-bloquant avec timeout."""
        host, port = self._host, self._port
        try:
            with socket.create_connection((host, port), timeout=2.0) as s:
                # Envoyer immédiatement après connexion.
                # bcmcan envoie d'abord {"front":{...}} puis attend les commandes
                # avec timeout 2s → suffisant pour recevoir notre commande.
                s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
                # Lire l'ACK optionnel
                s.settimeout(0.5)
                try:
                    raw = s.recv(512).decode("utf-8", errors="replace").strip()
                    if raw:
                        for line in raw.split("\n"):
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                ack = json.loads(line)
                                if "type" in ack or "front" not in ack:
                                    print(f"[SIM-CLIENT] ACK reçu: {ack}")
                            except json.JSONDecodeError:
                                pass
                except socket.timeout:
                    pass  # ACK optionnel
            return True
        except OSError as e:
            print(f"[SIM-CLIENT] Erreur envoi vers {host}:{port} -- {e}")
            with self._lock:
                self._connected = False
            return False