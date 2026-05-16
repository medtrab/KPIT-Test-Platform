from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QGridLayout,
    QPushButton, QLabel, QFrame, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor

# Adresses logiques — même valeurs que constants.py
_LOGICAL_ADDR_BCM = 0x0700
_LOGICAL_ADDR_WC  = 0x0701


class DashboardPage(QWidget):
    # Signaux émis vers MainWindow
    discover_requested       = Signal()
    connect_requested        = Signal()
    read_dtc_requested       = Signal()
    clear_dtc_requested      = Signal()
    default_session_requested  = Signal()
    extended_session_requested = Signal()
    ecu_reset_requested = Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self.cards = {}
        self.dtc_database = {}
        self._target_addr = _LOGICAL_ADDR_BCM
        layout = QVBoxLayout(self)

        # ── Cartes d'état ────────────────────────────────────────────
        cards_layout = QHBoxLayout()
        self.conn_card = self.create_card("Connection", "Disconnected", "red")
        self.routing_card = self.create_card("Routing", "Inactive", "red")
        self.target_card = self.create_card("Target ECU", "BCM (0x700)", "green")
        self.session_card = self.create_card("Session", "Default", "orange")
        self.security_card = self.create_card("Security", "Locked", "red")

        for card in [self.conn_card, self.routing_card, self.target_card,
                     self.session_card, self.security_card]:
            cards_layout.addWidget(card)
        layout.addLayout(cards_layout)

        # ── Sélection ECU ────────────────────────────────────────────
        ecu_layout = QHBoxLayout()
        ecu_layout.addWidget(QLabel("Discovered ECUs:"))
        self.ecu_combo = QComboBox()
        self.ecu_combo.setMinimumWidth(300)
        ecu_layout.addWidget(self.ecu_combo)
        layout.addLayout(ecu_layout)

        # ── Actions rapides ──────────────────────────────────────────
        actions_group = QGroupBox("Quick Actions")
        actions_layout = QGridLayout()
        actions = [
            ("Discover ECUs", self.discover_requested),
            ("Connect", self.connect_requested),
            ("Read DTCs", self.read_dtc_requested),
            ("Clear DTCs", self.clear_dtc_requested),
            ("Default Session", self.default_session_requested),
            ("Extended Session", self.extended_session_requested),
            ("Soft Reset", self.ecu_reset_requested),
        ]
        for i, (text, signal) in enumerate(actions):
            btn = QPushButton(text)
            btn.clicked.connect(signal)
            actions_layout.addWidget(btn, i // 3, i % 3)
            if text == "Extended Session":
                self.extended_session_btn = btn
            if text == "Soft Reset":
                self.ecu_reset_btn = btn
        actions_group.setLayout(actions_layout)
        layout.addWidget(actions_group)

        # ── Tableau DTC ──────────────────────────────────────────────
        self.dtc_group = QGroupBox("Recent DTCs — BCM (0x0700)")
        dtc_layout = QVBoxLayout()

        self.dtc_table = QTableWidget(0, 4)
        self.dtc_table.verticalHeader().setVisible(False)
        self.dtc_table.verticalHeader().setDefaultSectionSize(48)
        self.dtc_table.setWordWrap(True)
        self.dtc_table.setAlternatingRowColors(True)
        self.dtc_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.dtc_table.setSelectionBehavior(QTableWidget.SelectRows)

        dtc_layout.addWidget(self.dtc_table)
        self.dtc_group.setLayout(dtc_layout)
        layout.addWidget(self.dtc_group)

        layout.addStretch()

        self.discovered_ecus = []

        self._setup_columns_bcm()

    # ──────────────────────────────────────────────────────────────────
    # Configuration des colonnes selon l'ECU cible
    # ──────────────────────────────────────────────────────────────────

    def set_extended_session_enabled(self, enabled: bool):
        if hasattr(self, 'extended_session_btn'):
            self.extended_session_btn.setEnabled(enabled)
    def _setup_columns_bcm(self):
        """Colonnes identiques à BCMPage : DTC | Status | Category | Description"""
        self.dtc_table.setColumnCount(4)
        self.dtc_table.setHorizontalHeaderLabels(
            ["DTC", "Status", "Category", "Description"]
        )
        hdr = self.dtc_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        self.dtc_group.setTitle("Recent DTCs — BCM (0x0700)")

    def _setup_columns_wc(self):
        """Colonnes identiques à WCPage : DTC | Description | Category | Status"""
        self.dtc_table.setColumnCount(4)
        self.dtc_table.setHorizontalHeaderLabels(
            ["DTC", "Description", "Category", "Status"]
        )
        hdr = self.dtc_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.dtc_group.setTitle("Recent DTCs — WC (0x0701)")

    # ──────────────────────────────────────────────────────────────────
    # API publique
    # ──────────────────────────────────────────────────────────────────

    def set_target(self, addr: int):
        """
        Appelé par MainWindow._on_target_changed().
        Reconfigure les colonnes et vide le tableau.
        """
        self._target_addr = addr
        self.dtc_table.setRowCount(0)
        if addr == _LOGICAL_ADDR_WC:
            self._setup_columns_wc()
        else:
            self._setup_columns_bcm()

    def set_dtc_database(self, database: dict):
        self.dtc_database = database

    def update_dtc_table(self, dtc_list):
        """
        Remplit le tableau selon le format de la page DTC active :
          - BCM  → format BCMPage  (DTC | Status | Category | Description)
          - WC   → format WCPage   (DTC | Description | Category | Status)
        """
        if self._target_addr == _LOGICAL_ADDR_WC:
            self._fill_wc(dtc_list)
        else:
            self._fill_bcm(dtc_list)

        self.dtc_table.resizeRowsToContents()

    # ──────────────────────────────────────────────────────────────────
    # Remplissage BCM — copie exacte de BCMPage.update_dtc_table()
    # ──────────────────────────────────────────────────────────────────

    def _fill_bcm(self, dtc_list):
        color_map = {
            "electrical":    "#FF6347",
            "communication": "#FFA500",
            "functional":    "#87CEFA",
            "protection":    "#2e7d32",
            "signal_fault":  "#DA70D6",
        }

        self.dtc_table.setRowCount(len(dtc_list))

        for row, (dtc, status) in enumerate(dtc_list):
            # ── DTC ──
            self.dtc_table.setItem(row, 0, QTableWidgetItem(f"0x{dtc:06X}"))

            # ── Status ──
            self.dtc_table.setItem(row, 1, QTableWidgetItem(f"0x{status:02X}"))

            info = self.dtc_database.get(dtc, {})
            category    = info.get("category",    "—")
            description = info.get("description", "—")

            # ── Category ──
            cat_item = QTableWidgetItem(category)
            cat_item.setForeground(QColor(color_map.get(category, "#A9A9A9")))
            self.dtc_table.setItem(row, 2, cat_item)

            # ── Description ──
            self.dtc_table.setItem(row, 3, QTableWidgetItem(description))

    # ──────────────────────────────────────────────────────────────────
    # Remplissage WC — copie exacte de WCPage.update_dtc_table()
    # ──────────────────────────────────────────────────────────────────

    def _fill_wc(self, dtc_list):
        color_map = {
            "electrical":    "#FF6347",
            "communication": "#FFA500",
            "functional":    "#64B5F6",
            "signal_fault":  "#FFD54F",
            "protection":    "#81C784",
        }

        self.dtc_table.setRowCount(len(dtc_list))

        for row, (dtc_id, status) in enumerate(dtc_list):
            info = self.dtc_database.get(dtc_id, {})
            name = info.get("name",        f"0x{dtc_id:06X}")
            desc = info.get("description", "—")
            cat  = info.get("category",    "—")

            # ── DTC (name) ──
            self.dtc_table.setItem(row, 0, QTableWidgetItem(name))

            # ── Description ──
            self.dtc_table.setItem(row, 1, QTableWidgetItem(desc))

            # ── Category ──
            cat_item = QTableWidgetItem(cat)
            cat_item.setForeground(QColor(color_map.get(cat, "#A9A9A9")))
            self.dtc_table.setItem(row, 2, cat_item)

            # ── Status ──
            self.dtc_table.setItem(row, 3, QTableWidgetItem(f"0x{status:02X}"))

    # ──────────────────────────────────────────────────────────────────
    # Méthodes de création et de mise à jour des cartes d'état
    # ──────────────────────────────────────────────────────────────────

    def create_card(self, title, value, color):
        card = QFrame()
        card.setFrameShape(QFrame.StyledPanel)
        card.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d;
                border: 1px solid #2e7d32;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        layout = QVBoxLayout(card)
        layout.addWidget(QLabel(title))
        value_label = QLabel(value)
        value_label.setStyleSheet(f"color: {color}; font-size: 20px; font-weight: bold;")
        layout.addWidget(value_label)
        self.cards[title] = value_label
        return card

    def update_ecu_list(self, ecus):
        self.discovered_ecus = ecus
        self.ecu_combo.clear()
        for ecu in ecus:
            self.ecu_combo.addItem(
                f"{ecu['vin']} @ {ecu['ip']} (0x{ecu['logical_address']:04X})", ecu
            )

    def get_selected_ecu(self):
        idx = self.ecu_combo.currentIndex()
        return self.ecu_combo.currentData() if idx >= 0 else None

    def set_connection_state(self, connected: bool):
        label = self.cards.get("Connection")
        if label:
            label.setText("Connected" if connected else "Disconnected")
            label.setStyleSheet(
                "color: #2e7d32; font-size: 20px; font-weight: bold;" if connected
                else "color: red; font-size: 20px; font-weight: bold;"
            )

    def set_routing_state(self, active: bool):
        label = self.cards.get("Routing")
        if label:
            label.setText("Active" if active else "Inactive")
            label.setStyleSheet(
                "color: #2e7d32; font-size: 20px; font-weight: bold;" if active
                else "color: red; font-size: 20px; font-weight: bold;"
            )

    def set_session(self, session: int):
        label = self.cards.get("Session")
        if not label:
            return
        if session == 0x01:
            label.setText("Default")
            label.setStyleSheet("color: orange; font-size: 20px; font-weight: bold;")
        elif session == 0x03:
            label.setText("Extended")
            label.setStyleSheet("color: #2e7d32; font-size: 20px; font-weight: bold;")
        else:
            label.setText(f"0x{session:02X}")

    def set_security(self, level: int):
        label = self.cards.get("Security")
        if label:
            if level == 0:
                label.setText("Locked")
                label.setStyleSheet("color: red; font-size: 20px; font-weight: bold;")
            else:
                label.setText(f"Level {level}")
                label.setStyleSheet("color: #2e7d32; font-size: 20px; font-weight: bold;")

    def set_security_none(self):
        label = self.cards.get("Security")
        if label:
            label.setText("N/A")
            label.setStyleSheet("color: #888888; font-size: 20px; font-weight: bold;")
