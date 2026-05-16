"""
Dockable log panel with coloured messages, filter, and export.
"""

from PySide6.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QTextEdit, QHBoxLayout,
    QPushButton, QComboBox, QLabel, QFileDialog, QMessageBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCursor, QFont
import time
class LogPanel(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("Communication Log", parent)
        self.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        self.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(5,5,5,5)

        # Filter combo
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Filter:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All", "SEND", "RECV", "ERROR", "NEGATIVE", "INFO"])
        self.filter_combo.currentTextChanged.connect(self.apply_filter)
        filter_layout.addWidget(self.filter_combo)
        layout.addLayout(filter_layout)

        # Log text
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 10))
        self.log_text.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(self.log_text)

        # Buttons
        btn_layout = QHBoxLayout()

        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear_log)

        self.export_btn = QPushButton("Export")
        self.export_btn.clicked.connect(self.export_log)
        btn_layout.addWidget(self.clear_btn)
        btn_layout.addWidget(self.export_btn)
        layout.addLayout(btn_layout)

        self.setWidget(widget)

        # Store all messages with level for filtering
        self.messages = []  # list of (level, message)

    def add_message(self, level: str, message: str):
        """Add timestamped, colour‑coded log entry."""

        now = time.time()
        ms = int((now % 1) * 1000)
        timestamp = time.strftime("%H:%M:%S") + f".{ms:03d}"
        formatted = f"[{timestamp}] {level}: {message}"
        self.messages.append((level, formatted))
        self.apply_filter()  # refresh view

    def apply_filter(self):
        """Show only messages matching selected filter."""
        filter_text = self.filter_combo.currentText()
        self.log_text.clear()
        for level, msg in self.messages:
            if filter_text == "All" or level == filter_text:
                colour = {
                    "SEND": "#89CFF0",
                    "RECV": "#98FB98",
                    "ERROR": "#FF6347",
                    "NEGATIVE": "#FFA500",
                    "INFO": "#D3D3D3",
                }.get(level, "#FFFFFF")
                self.log_text.setTextColor(QColor(colour))
                self.log_text.append(msg)
                self.log_text.setTextColor(QColor("#FFFFFF"))
        # Auto scroll
        self.log_text.moveCursor(QTextCursor.End)


    def clear_log(self):
        """Efface réellement l'affichage ET la mémoire interne des messages."""
        self.messages.clear()
        self.log_text.clear()

    def export_log(self):
        """
        Exporte les logs visibles selon le filtre courant.
        """
        if not self.messages:
            QMessageBox.warning(self, "Export impossible", "Aucun log à exporter.")
            return

        filter_text = self.filter_combo.currentText()

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Log",
            f"communication_log_{time.strftime('%Y%m%d_%H%M%S')}.txt",
            "Text Files (*.txt);;Log Files (*.log);;All Files (*)"
        )
        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("COMMUNICATION LOG EXPORT\n")
                f.write("=" * 60 + "\n")
                f.write(f"Filter: {filter_text}\n")
                f.write(f"Export time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("=" * 60 + "\n\n")

                for level, msg in self.messages:
                    if filter_text == "All" or level == filter_text:
                        f.write(msg + "\n")

            QMessageBox.information(
                self,
                "Export réussi",
                f"Log exporté avec succès.\n\nFilter: {filter_text}\nPath: {path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Export échoué", f"Erreur pendant l'export:\n{e}")