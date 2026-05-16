"""
Global styling and constants.
"""

# Dark theme with green accents – professional automotive look.
DARK_STYLESHEET = """
QMainWindow, QDialog {
    background-color: #1a1a1a;
}

QWidget {
    background-color: #1a1a1a;
    color: #e0e0e0;
    font-family: 'Segoe UI', 'Arial', sans-serif;
    font-size: 13px;
}

QGroupBox {
    border: 2px solid #2e7d32;
    border-radius: 8px;
    margin-top: 1.5em;
    padding-top: 10px;
    font-weight: bold;
    color: #a5d6a7;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px 0 5px;
}

QPushButton {
    background-color: #2e7d32;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 10px 16px;
    font-weight: bold;
    min-width: 80px;
}

QPushButton:hover {
    background-color: #1b5e20;
}

QPushButton:pressed {
    background-color: #0d4d0d;
}

QPushButton:disabled {
    background-color: #4a4a4a;
    color: #aaaaaa;
}

QLineEdit, QSpinBox, QComboBox, QTextEdit {
    background-color: #2d2d2d;
    color: #e0e0e0;
    border: 1px solid #2e7d32;
    border-radius: 4px;
    padding: 6px;
}

QTableWidget {
    background-color: #252525;
    color: #e0e0e0;
    gridline-color: #2e7d32;
    selection-background-color: #2e7d32;
}

QHeaderView::section {
    background-color: #2e7d32;
    color: white;
    padding: 6px;
    border: none;
    font-weight: bold;
}

QCheckBox {
    color: #e0e0e0;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
}

QCheckBox::indicator:unchecked {
    background-color: #2d2d2d;
    border: 1px solid #2e7d32;
}

QCheckBox::indicator:checked {
    background-color: #2e7d32;
    border: 1px solid #2e7d32;
}

QDockWidget {
    titlebar-close-icon: url(close.png);
    titlebar-normal-icon: url(float.png);
}

QDockWidget::title {
    background-color: #2e7d32;
    color: white;
    padding: 6px;
    text-align: center;
}

QStatusBar {
    background-color: #1a1a1a;
    color: #e0e0e0;
}

"""

# Logical addresses (BCM and WC)
LOGICAL_ADDR_BCM = 0x0700
LOGICAL_ADDR_WC = 0x0701


LIGHT_STYLESHEET = """
QMainWindow, QDialog {
    background-color: #D6DBE0;
}

QWidget {
    background-color: #D6DBE0;
    color: #0D1F1F;
    font-family: 'Segoe UI', 'Arial', sans-serif;
    font-size: 13px;
}

QGroupBox {
    border: 1.5px solid #90A4AE;
    border-radius: 8px;
    margin-top: 1.5em;
    padding-top: 12px;
    font-weight: bold;
    color: #1B5E20;
    background-color: #E2E7EB;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 2px 8px;
    background-color: #E2E7EB;
    color: #1B5E20;
    font-size: 12px;
    font-weight: bold;
}

QPushButton {
    background-color: #2E7D32;
    color: #FFFFFF;
    border: none;
    border-radius: 6px;
    padding: 10px 16px;
    font-weight: bold;
    min-width: 80px;
    font-size: 13px;
}

QPushButton:hover {
    background-color: #1B5E20;
}

QPushButton:pressed {
    background-color: #0D4D0D;
}

QPushButton:disabled {
    background-color: #90A4AE;
    color: #CFD8DC;
}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit {
    background-color: #FFFFFF;
    color: #0D1F1F;
    border: 2px solid #546E7A;
    border-radius: 5px;
    padding: 5px 8px;
    selection-background-color: #81C784;
    selection-color: #0D1F1F;
}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 2px solid #2E7D32;
    background-color: #FFFFFF;
}

QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    background-color: #CFD8DC;
    color: #78909C;
    border: 2px solid #90A4AE;
}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1.5px solid #2E7D32;
    background-color: #F5F7F9;
}

QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    background-color: #C5CBD0;
    color: #78909C;
    border: 1.5px solid #90A4AE;
}

QComboBox::drop-down {
    border: none;
    background-color: transparent;
    width: 20px;
}

QComboBox QAbstractItemView {
    background-color: #EEF1F4;
    color: #0D1F1F;
    selection-background-color: #A5D6A7;
    selection-color: #0D1F1F;
    border: 1px solid #78909C;
    outline: none;
}

QSlider::groove:horizontal {
    background-color: #90A4AE;
    height: 6px;
    border-radius: 3px;
}

QSlider::handle:horizontal {
    background-color: #2E7D32;
    border: 2px solid #1B5E20;
    width: 16px;
    height: 16px;
    border-radius: 8px;
    margin: -5px 0;
}

QSlider::sub-page:horizontal {
    background-color: #4CAF50;
    border-radius: 3px;
}

QSlider:disabled::groove:horizontal {
    background-color: #B0BEC5;
}

QSlider:disabled::handle:horizontal {
    background-color: #90A4AE;
    border-color: #78909C;
}

QTableWidget {
    background-color: #EEF1F4;
    color: #0D1F1F;
    gridline-color: #90A4AE;
    selection-background-color: #A5D6A7;
    selection-color: #0D1F1F;
    alternate-background-color: #E2E7EB;
    border: 1px solid #90A4AE;
    border-radius: 4px;
    font-size: 12px;
}

QHeaderView::section {
    background-color: #2E7D32;
    color: #FFFFFF;
    padding: 8px;
    border: none;
    font-weight: bold;
    font-size: 12px;
    letter-spacing: 0.5px;
}

QHeaderView::section:hover {
    background-color: #1B5E20;
}

QCheckBox {
    color: #0D1F1F;
    spacing: 8px;
    font-size: 13px;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 3px;
    border: 1.5px solid #546E7A;
    background-color: #EEF1F4;
}

QCheckBox::indicator:hover {
    border: 1.5px solid #2E7D32;
}

QCheckBox::indicator:checked {
    background-color: #2E7D32;
    border: 1.5px solid #1B5E20;
}

QCheckBox:disabled {
    color: #78909C;
}

QCheckBox::indicator:disabled {
    background-color: #C5CBD0;
    border: 1.5px solid #90A4AE;
}

QDockWidget {
    color: #0D1F1F;
    font-weight: bold;
}

QDockWidget::title {
    background-color: #2E7D32;
    color: #FFFFFF;
    padding: 8px 12px;
    font-weight: bold;
    font-size: 13px;
}

QTextEdit {
    background-color: #1A2332;
    color: #E0E0E0;
    border: 2px solid #546E7A;
    border-radius: 4px;
    padding: 6px;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 12px;
    selection-background-color: #81C784;
    selection-color: #0D1F1F;
}

QStatusBar {
    background-color: #B0BEC5;
    color: #0D1F1F;
    border-top: 1px solid #78909C;
    font-size: 12px;
    padding: 2px 8px;
}

QScrollArea {
    background-color: transparent;
    border: none;
}

QScrollBar:vertical {
    background-color: #C5CBD0;
    width: 8px;
    border-radius: 4px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background-color: #78909C;
    border-radius: 4px;
    min-height: 24px;
}

QScrollBar::handle:vertical:hover {
    background-color: #2E7D32;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    background-color: #C5CBD0;
    height: 8px;
    border-radius: 4px;
}

QScrollBar::handle:horizontal {
    background-color: #78909C;
    border-radius: 4px;
    min-width: 24px;
}

QScrollBar::handle:horizontal:hover {
    background-color: #2E7D32;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}

QTabWidget::pane {
    background-color: #E2E7EB;
    border: 1.5px solid #90A4AE;
    border-radius: 0 6px 6px 6px;
}

QTabBar::tab {
    background-color: #B0BEC5;
    color: #0D1F1F;
    padding: 8px 18px;
    border: 1px solid #90A4AE;
    border-bottom: none;
    border-radius: 6px 6px 0 0;
    margin-right: 2px;
    font-weight: bold;
    font-size: 13px;
}

QTabBar::tab:selected {
    background-color: #2E7D32;
    color: #FFFFFF;
    border-color: #2E7D32;
}

QTabBar::tab:hover:!selected {
    background-color: #90A4AE;
    color: #0D1F1F;
}

QProgressBar {
    background-color: #B0BEC5;
    border: 1.5px solid #78909C;
    border-radius: 6px;
    text-align: center;
    color: #0D1F1F;
    font-weight: bold;
    font-size: 12px;
    min-height: 18px;
}

QProgressBar::chunk {
    background-color: #2E7D32;
    border-radius: 5px;
}

QFrame[frameShape="4"],
QFrame[frameShape="5"] {
    color: #78909C;
    background-color: #78909C;
}

QToolTip {
    background-color: #263238;
    color: #E0E0E0;
    border: 1px solid #2E7D32;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
}

QMenu {
    background-color: #EEF1F4;
    color: #0D1F1F;
    border: 1px solid #90A4AE;
    border-radius: 4px;
}

QMenu::item:selected {
    background-color: #A5D6A7;
    color: #0D1F1F;
}

QLabel {
    background-color: transparent;
    color: #0D1F1F;
}
"""