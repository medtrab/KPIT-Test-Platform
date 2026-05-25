"""
bus_config_widget.py — Éditeur LDF/DBC embarqué dans QWebEngineView
====================================================================
Embarque bus_config_editor.html dans un QWebEngineView.
La communication JS → Python se fait via window.__pyAction__ injecté
par runJavaScript — pas de QWebChannel (évite les problèmes de timing
et de contexte non-sécurisé dans Chromium embarqué).

Intégration dans main_window.py
────────────────────────────────
    from bus_config_widget import BusConfigWidget

    def _open_bus_config(self) -> None:
        from PySide6.QtWidgets import QDialog, QVBoxLayout
        from bus_config_widget import BusConfigWidget
        dlg = QDialog(self)
        dlg.setWindowTitle("Bus Configuration — LDF / DBC")
        dlg.setMinimumSize(1100, 700)
        dlg.resize(1280, 800)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        w = BusConfigWidget(dlg)
        w.file_saved.connect(self._on_bus_file_saved)
        lay.addWidget(w)
        dlg.exec()

    def _on_bus_file_saved(self, filename: str, content: str) -> None:
        import os, sys
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        try:
            from network_config import active_paths, reload_bus
            paths = active_paths()
            is_ldf = filename.endswith(".ldf")
            base   = os.path.dirname(paths["ldf"] if is_ldf else paths["dbc"])
            dest   = os.path.join(base, filename)
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(content)
            reload_bus(ldf_path=dest) if is_ldf else reload_bus(dbc_path=dest)
            self._on_bus_config_changed()
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Sauvegardé",
                f"✓  {filename}\\n\\nDans : {dest}\\n\\nConfig rechargée.")
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Erreur", f"Impossible d'écrire {filename}:\\n{e}")
"""

import os
import sys
from pathlib import Path

from PySide6.QtCore    import Qt, QUrl, QTimer, Signal, QObject, Slot
from PySide6.QtGui     import QColor
from PySide6.QtWidgets import QApplication, QFileDialog
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore    import QWebEngineSettings, QWebEnginePage


# ── Silence JS console ────────────────────────────────────────────────────────
class _SilentPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, line, source):
        pass  # Décommenter pour debug : print(f"[JS] {message}")


# ── Widget principal ──────────────────────────────────────────────────────────
class BusConfigWidget(QWebEngineView):
    """
    QWebEngineView chargeant bus_config_editor.html.

    Communication JS → Python via window.__pyAction__(action, filename, content) :
      action = "copy"     → copie content dans QApplication.clipboard()
      action = "download" → ouvre QFileDialog.getSaveFileName puis écrit le fichier
                            et émet file_saved(filename, content)

    Signal file_saved(filename, content) émis après écriture sur disque.
    """
    file_saved = Signal(str, str)   # (filename, content)

    HTML_FILE = "bus_config_editor.html"

    # Script injecté DANS la page pour exposer window.__pyAction__
    # pywebchannel classique est remplacé par un polling sur window.__pendingAction__
    # que Python lit via runJavaScript toutes les 200ms.
    _POLL_JS = """
window.__pendingAction__ = null;
window.__pyAction__ = function(action, filename, content) {
    window.__pendingAction__ = { action: action, filename: filename, content: content };
};
"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setPage(_SilentPage(self))

        s = self.page().settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,   True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages,                  True)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled,               True)
        s.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture,     False)

        self.page().setBackgroundColor(QColor("#F5FFF0"))

        self._ready = False
        self._pending_js: list[str] = []

        self.loadFinished.connect(self._on_loaded)
        self._load_html()

        # Polling timer — vérifie si JS a posté une action toutes les 200ms
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(200)
        self._poll_timer.timeout.connect(self._poll_action)

    # ── Chargement ────────────────────────────────────────────────────────────
    def _load_html(self) -> None:
        html_path = Path(__file__).parent / self.HTML_FILE
        if not html_path.exists():
            alt = Path(self.HTML_FILE)
            html_path = alt if alt.exists() else None
        if not html_path or not html_path.exists():
            self._show_fallback(f"Fichier introuvable : {self.HTML_FILE}")
            return
        try:
            html = html_path.read_text(encoding="utf-8")
        except Exception as e:
            self._show_fallback(str(e))
            return

        # Injecter le shim __pyAction__ dans <head>
        shim = f"<script>{self._POLL_JS}</script>"
        html = html.replace("<head>", "<head>\n" + shim, 1)

        base_url = QUrl.fromLocalFile(str(html_path.parent) + "/")
        self.setHtml(html, base_url)

    def _show_fallback(self, err: str = "") -> None:
        self.setHtml(f"""<!DOCTYPE html><html><body
            style="background:#F5FFF0;font-family:Segoe UI,sans-serif;
                   display:flex;align-items:center;justify-content:center;height:100vh;">
        <div style="text-align:center;padding:40px;">
            <div style="font-size:48px">⚙</div>
            <div style="font-size:18px;margin-top:12px;font-weight:600;color:#5A8A20;">
                BusConfig — Fichier introuvable</div>
            <div style="font-size:12px;color:#5A6A4A;margin-top:8px;font-family:monospace;">
                {err}</div>
            <div style="font-size:11px;color:#8A9E8A;margin-top:6px;">
                Placer <b>bus_config_editor.html</b> dans le même dossier que ce fichier.</div>
        </div></body></html>""")

    def _on_loaded(self, ok: bool) -> None:
        self._ready = True
        for js in self._pending_js:
            self.page().runJavaScript(js)
        self._pending_js.clear()
        self._poll_timer.start()

    # ── Polling : lit window.__pendingAction__ depuis Python ──────────────────
    def _poll_action(self) -> None:
        """Vérifie toutes les 200ms si le JS a posté une action."""
        self.page().runJavaScript(
            "(function(){ var a=window.__pendingAction__; "
            "window.__pendingAction__=null; return a ? JSON.stringify(a) : null; })()",
            self._handle_action
        )

    def _handle_action(self, result) -> None:
        """Traite l'action reçue depuis JS."""
        if not result:
            return
        try:
            import json
            data     = json.loads(result)
            action   = data.get("action", "")
            filename = data.get("filename", "file.txt")
            content  = data.get("content", "")
        except Exception:
            return

        if action == "copy":
            self._do_copy(content)
        elif action == "download":
            self._do_download(filename, content)

    # ── Actions Python ────────────────────────────────────────────────────────
    def _do_copy(self, content: str) -> None:
        """Copie le contenu dans le presse-papier système via Qt."""
        clipboard = QApplication.clipboard()
        clipboard.setText(content)
        # Feedback visuel dans la page
        self.page().runJavaScript(
            "if(typeof notify==='function'){notify('Copied to clipboard');}"
        )

    def _do_download(self, filename: str, content: str) -> None:
        """Ouvre QFileDialog, écrit le fichier, recharge la config à chaud."""
        ext = "LIN Description File (*.ldf)" if filename.endswith(".ldf") \
              else "CAN Database (*.dbc)"

        # Répertoire par défaut = dossier de ce fichier Python (platform_work/)
        default_dir = os.path.dirname(os.path.abspath(__file__))

        dest, _ = QFileDialog.getSaveFileName(
            self,
            f"Enregistrer {filename}",
            os.path.join(default_dir, filename),
            f"{ext};;Tous les fichiers (*)"
        )
        if not dest:
            return  # Annulé par l'utilisateur

        # 1. Écriture — aucune dépendance externe
        try:
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(content)
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Erreur écriture",
                f"Impossible d'écrire :\n{dest}\n\n{e}")
            return

        # 2. Reload à chaud via network_config (même répertoire que ce fichier)
        _platform_dir = os.path.dirname(os.path.abspath(__file__))
        reload_ok = False
        try:
            if _platform_dir not in sys.path:
                sys.path.insert(0, _platform_dir)
            from network_config import reload_bus
            if dest.endswith(".ldf"):
                reload_bus(ldf_path=dest)
            else:
                reload_bus(dbc_path=dest)
            reload_ok = True
        except Exception:
            pass  # Ne pas bloquer si network_config absent (mode standalone)

        # 3. Feedback dans la page
        short = os.path.basename(dest)
        msg   = short + (" — config reloaded" if reload_ok else " — saved")
        self.page().runJavaScript(
            f"if(typeof notify==='function'){{notify('{msg}');}}"
        )

        # 4. Signal vers main_window avec le CHEMIN COMPLET
        #    main_window n'a plus qu'à rafraîchir l'oscilloscope/table CAN
        self.file_saved.emit(dest, content)


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("BusConfig Editor")

    w = BusConfigWidget()
    w.setWindowTitle("BusConfig — LDF / DBC Editor")
    w.resize(1280, 800)

    def on_file(filename, content):
        print(f"[BusConfig] file_saved: {filename} ({len(content)} chars)")

    w.file_saved.connect(on_file)
    w.show()
    sys.exit(app.exec())
