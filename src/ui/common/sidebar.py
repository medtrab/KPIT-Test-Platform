"""
Custom sidebar with animated hover and collapsible mode.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QFrame
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, Signal, QSize, QParallelAnimationGroup
from PySide6.QtGui import QIcon

class SidebarButton(QPushButton):
    """Custom button with hover animation and icon."""

    def __init__(self, text, icon_text, parent=None):
        super().__init__(text, parent)
        self.setFixedHeight(50)
        self.setCheckable(True)
        self.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #e0e0e0;
                text-align: left;
                padding-left: 20px;
                border: none;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #2e7d32;
                color: white;
            }
            QPushButton:checked {
                background-color: #1b5e20;
                color: white;
                border-left: 4px solid #a5d6a7;
            }
        """)
        self.icon_text = icon_text


class Sidebar(QFrame):
    """Vertical navigation sidebar with collapsible feature."""

    page_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(200)
        self.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border-right: 2px solid #2e7d32;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 20, 0, 0)
        layout.setSpacing(2)

        # Collapse/expand button
        self.collapse_btn = QPushButton("◀ Collapse")
        self.collapse_btn.clicked.connect(self.toggle_collapse)
        self.collapse_btn.setStyleSheet("""
            QPushButton {
                background-color: #2e7d32;
                color: white;
                border: none;
                padding: 8px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1b5e20;
            }
        """)
        layout.addWidget(self.collapse_btn)

        # Buttons for each category
        self.buttons = []
        categories = [
            ("Dashboard", "🏠"),
            ("Session & Security", "🔒"),
            ("HIL", "🧪"),
            ("DTC Management", "⚠️"),
            ("Actuator Lab", "💉"),
            ("UDS Console", "⌨️"),
            ("Communication Control", "📡"),
        ]

        for text, icon in categories:
            btn = SidebarButton(f"{icon} {text}", icon)
            btn.clicked.connect(self._handle_button_click)
            btn.page_name = text
            self.buttons.append(btn)
            layout.addWidget(btn)

        layout.addStretch()

        # Start with Dashboard selected
        self.buttons[0].setChecked(True)

    def _handle_button_click(self):
        button = self.sender()
        if not button:
            return
        for btn in self.buttons:
            btn.setChecked(False)
        button.setChecked(True)
        page_name = getattr(button, "page_name", None)
        if page_name:
            self.page_selected.emit(page_name)


    def set_selected(self, page_name):
        pairs = [
            ("Dashboard", ""),
            ("Session & Security", ""),
            ("HIL", ""),
            ("DTC Management", ""),
            ("Communication Control", ""),
        ]
        for btn, (text, _) in zip(self.buttons, pairs):
            btn.setChecked(text == page_name)

    def toggle_collapse(self):
        """Collapse or expand sidebar with robust animation."""
        collapsed_width = 60
        expanded_width = 200

        is_collapsing = self.width() > 70
        start_width = self.width()
        end_width = collapsed_width if is_collapsing else expanded_width

        self.anim_group = QParallelAnimationGroup(self)

        self.min_anim = QPropertyAnimation(self, b"minimumWidth")
        self.min_anim.setDuration(220)
        self.min_anim.setStartValue(start_width)
        self.min_anim.setEndValue(end_width)
        self.min_anim.setEasingCurve(QEasingCurve.OutCubic)

        self.max_anim = QPropertyAnimation(self, b"maximumWidth")
        self.max_anim.setDuration(220)
        self.max_anim.setStartValue(start_width)
        self.max_anim.setEndValue(end_width)
        self.max_anim.setEasingCurve(QEasingCurve.OutCubic)

        self.anim_group.addAnimation(self.min_anim)
        self.anim_group.addAnimation(self.max_anim)

        if is_collapsing:
            self.collapse_btn.setText("▶ Expand")
            for btn in self.buttons:
                btn.setText(btn.icon_text)
        else:
            self.collapse_btn.setText("◀ Collapse")
            for btn, (text, icon) in zip(self.buttons, [
                ("Dashboard", "🏠"),
                ("Session & Security", "🔒"),
                ("HIL", "🧪"),
                ("DTC Management", "⚠️"),
                ("Actuator Lab", "💉"),
                ("UDS Console", "⌨️"),
                ("Communication Control", "📡"),
            ]):
                btn.setText(f"{icon} {text}")

        def _finalize_width():
            self.setMinimumWidth(end_width)
            self.setMaximumWidth(end_width)
            self.updateGeometry()

        self.anim_group.finished.connect(_finalize_width)
        self.anim_group.start()