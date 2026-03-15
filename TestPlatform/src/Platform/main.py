#!/usr/bin/env python3
"""
WipeWash — HIL Test Bench Dashboard
Point d'entrée : python main.py
"""

import sys

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui     import QPalette, QColor

from constants   import W_BG, W_TEXT, W_PANEL, W_PANEL2, A_TEAL
from main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,           QColor(W_BG))
    pal.setColor(QPalette.ColorRole.WindowText,       QColor(W_TEXT))
    pal.setColor(QPalette.ColorRole.Base,             QColor(W_PANEL))
    pal.setColor(QPalette.ColorRole.AlternateBase,    QColor(W_PANEL2))
    pal.setColor(QPalette.ColorRole.Text,             QColor(W_TEXT))
    pal.setColor(QPalette.ColorRole.Button,           QColor(W_PANEL2))
    pal.setColor(QPalette.ColorRole.ButtonText,       QColor(W_TEXT))
    pal.setColor(QPalette.ColorRole.Highlight,        QColor(A_TEAL))
    pal.setColor(QPalette.ColorRole.HighlightedText,  QColor("#FFFFFF"))
    pal.setColor(QPalette.ColorRole.ToolTipBase,      QColor(W_PANEL))
    pal.setColor(QPalette.ColorRole.ToolTipText,      QColor(W_TEXT))
    app.setPalette(pal)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
