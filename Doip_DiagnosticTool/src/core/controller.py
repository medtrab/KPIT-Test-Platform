"""
BackendController – vit dans le thread principal et orchestre le DiagnosticWorker.
Fournit une API synchrone (mais non‑bloquante) à l'IHM.
"""

from typing import Optional, Dict
from PySide6.QtCore import QObject, Signal, QThread
from core.worker import DiagnosticWorker
class BackendController(QObject):
    """
    Contrôleur central. Instancié dans le thread principal.
    Toutes ses méthodes sont thread‑safe et non‑bloquantes.
    """

    # Signaux relayés du worker (à connecter à l'IHM)
    log_message = Signal(str, str)
    ecu_discovered = Signal(list)

    connection_state_changed = Signal(bool, str)
    routing_state_changed = Signal(bool)
    session_changed = Signal(int)
    security_level_changed = Signal(int)
    dtc_list_updated = Signal(list)
    seed_received = Signal(bytes)
    uds_positive = Signal(bytes)
    uds_sent = Signal(bytes)  # trame UDS envoyée (TX)
    uds_negative = Signal(int, int)
    error_occurred = Signal(str)
    snapshot_received = Signal(int, int, bytes)
    extended_received = Signal(int, int, bytes)
    dtc_database_ready = Signal(dict)
    def __init__(self):
        super().__init__()
        self._worker = DiagnosticWorker()
        self._thread = QThread()

        # Connecter les signaux du worker vers les nôtres
        self._worker.log_signal.connect(self.log_message)
        self._worker.ecu_discovered.connect(self.ecu_discovered)
        self._worker.connection_state.connect(self.connection_state_changed)
        self._worker.routing_state.connect(self.routing_state_changed)
        self._worker.session_changed.connect(self.session_changed)
        self._worker.security_level_changed.connect(self.security_level_changed)
        self._worker.dtc_list_updated.connect(self.dtc_list_updated)
        self._worker.seed_received.connect(self.seed_received)
        self._worker.uds_positive.connect(self.uds_positive)
        self._worker.uds_sent.connect(self.uds_sent)
        self._worker.uds_negative.connect(self.uds_negative)
        self._worker.error_occurred.connect(self.error_occurred)
        self._worker.snapshot_received.connect(self.snapshot_received)
        self._worker.extended_received.connect(self.extended_received)
        self._worker.dtc_database_ready.connect(self.dtc_database_ready)
        # Déplacer le worker dans le thread
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._thread.start()

    def shutdown(self):
        """Arrête proprement le thread et le worker."""
        if self._thread.isRunning():
            self._worker.stop()
            self._thread.quit()
            self._thread.wait()

    # ------------------------------------------------------------------
    # API publique (slots pour l'IHM)
    # ------------------------------------------------------------------
    def discover_ecus(self):
        self._worker.discover_ecus()

    def connect_to_ecu(self, ecu_info: Dict):
        self._worker.connect_to_ecu(ecu_info)

    def disconnect(self):
        self._worker.disconnect()

    def set_target(self, addr: int):
        self._worker.set_target(addr)

    def switch_session(self, session_type: int):
        self._worker.switch_session(session_type)

    def request_seed(self, level: int):
        self._worker.request_seed(level)

    def send_key(self, level: int, key_bytes: bytes):
        self._worker.send_key(level, key_bytes)

    def read_dtc(self, status_mask: int = 0xFF):
        self._worker.read_dtc(status_mask)

    def request_routine_results(self, routine_id: int):
        self._worker.request_routine_results(routine_id)

    def clear_dtc(self):
        self._worker.clear_dtc()

    def read_data_by_identifier(self, did: int):
        self._worker.read_data_by_identifier(did)

    def read_dtc_snapshot(self, dtc_code: int, record_num: int):
        self._worker.read_dtc_snapshot(dtc_code, record_num)

    def read_dtc_extended(self, dtc_code: int, record_num: int):
        self._worker.read_dtc_extended(dtc_code, record_num)

    def start_routine(self, routine_id: int, value: int):
        self._worker.start_routine(routine_id, value)

    def stop_routine(self, routine_id: int):
        self._worker.stop_routine(routine_id)

    def write_coding(self, did: int, value: int):
        self._worker.write_coding(did, value)

    def communication_control(self, subf: int):
        self._worker.communication_control(subf)

    def send_uds_request(self, data: bytes):
        self._worker.send_uds_request(data)