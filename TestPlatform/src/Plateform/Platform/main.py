
"""
WipeWash — HIL Test Bench Dashboard
Point d'entrée : python main.py
"""

import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtGui     import QPalette, QColor, QIcon

from constants   import W_BG, W_TEXT, W_PANEL, W_PANEL2, A_TEAL
from main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # ── Icône application (barre des tâches + coin fenêtre) ──────────────────
    import os as _os
    _icon_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "app_icon.png")
    if _os.path.exists(_icon_path):
        app.setWindowIcon(QIcon(_icon_path))

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

    # ── Barre de titre sombre Windows (appliqué après show() pour hwnd valide) ──
    if sys.platform == "win32":
        try:
            import ctypes
            hwnd = int(win.winId())
            DWMWA_DARK = 20  # Windows 11
            value = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_DARK, ctypes.byref(value), ctypes.sizeof(value)
            )
        except Exception:
            try:
                DWMWA_DARK = 19  # Windows 10 fallback
                value = ctypes.c_int(1)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, DWMWA_DARK, ctypes.byref(value), ctypes.sizeof(value)
                )
            except Exception:
                pass

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
