"""
xcp_master.py  —  XCP Master côté Platform (PC)
================================================
Transport : Redis pub/sub.

API publique :
  master.upload("TOUCH_DURATION")         → valeur courante (float/int)
  master.download("TOUCH_DURATION", 1.6)  → bool
  master.get_a2l()                        → dict descripteur paramètres
  master.get_status()                     → dict valeurs courantes
  master.restore_default("TOUCH_DURATION")    → bool
  master.restore_all_defaults()               → dict[str, bool]
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any

_REDIS_AVAILABLE = False
try:
    import redis as _redis_mod
    _REDIS_AVAILABLE = True
except ImportError:
    pass

# ── Chargement local du fichier A2L ───────────────────────────────
_LOCAL_A2L: dict | None = None

try:
    from a2l_loader import load_a2l as _load_a2l

    def _resolve_local_a2l() -> str | None:
        env = os.environ.get("XCP_A2L_PATH", "").strip()
        if env and os.path.isfile(env):
            return env
        here = os.path.dirname(os.path.abspath(__file__))
        candidate = os.path.join(here, "wiperwash_xcp.a2l")
        if os.path.isfile(candidate):
            return candidate
        if os.path.isfile("wiperwash_xcp.a2l"):
            return "wiperwash_xcp.a2l"
        return None

    _a2l_path = _resolve_local_a2l()
    if _a2l_path:
        _LOCAL_A2L = _load_a2l(_a2l_path)
    else:
        print("[XCPMaster] wiperwash_xcp.a2l introuvable")

except Exception as _e:
    print(f"[XCPMaster] Avertissement chargement A2L local: {_e}")

# Canaux Redis
_CH_CMD      = "xcp_cmd"
_CH_RESP     = "xcp_resp"
_CMD_TIMEOUT = 3.0


class XCPError(Exception):
    pass


class XCPMaster:
    """
    Client XCP master — utilisé par XCPPanel.

    Usage :
        master = XCPMaster("192.168.1.10")
        val = master.upload("TOUCH_DURATION")
        master.download("TOUCH_DURATION", 1.6)
    """

    CLIENT_ID = f"Platform-{uuid.uuid4().hex[:6].upper()}"

    def __init__(self, host: str = "", port: int = 6379,
                 on_response=None):
        self._host        = host
        self._port        = port
        self._on_response = on_response
        self._r           = None
        self._lock        = threading.Lock()
        self._pending:   dict[str, threading.Event] = {}
        self._responses: dict[str, dict]            = {}
        self._a2l_cache: dict | None                = None

        if not _REDIS_AVAILABLE:
            print("[XCPMaster] package 'redis' absent — pip install redis")
            return

        self._redis_connect()

    # ── Connexion Redis ────────────────────────────────────────────

    def connect(self, host: str = None, port: int = None) -> bool:
        """Public connect method — reconnects to host (or re-uses stored host)."""
        if host is not None:
            self._host = host
        if port is not None:
            self._port = port
        return self._redis_connect()

    def _redis_connect(self) -> bool:
        try:
            self._r = _redis_mod.Redis(
                host=self._host, port=self._port, db=0,
                socket_connect_timeout=2, socket_timeout=2,
            )
            self._r.ping()
            self._start_listener()
            return True
        except Exception as e:
            print(f"[XCPMaster] Redis indisponible: {e}")
            self._r = None
            return False

    def _start_listener(self):
        self._listener_ready = threading.Event()
        t = threading.Thread(target=self._listen_responses,
                             daemon=True, name="XCPMaster-SUB")
        t.start()

    def _listen_responses(self):
        try:
            r  = _redis_mod.Redis(host=self._host, port=self._port, db=0,
                                  socket_connect_timeout=2, socket_timeout=30)
            ps = r.pubsub(ignore_subscribe_messages=True)
            ps.subscribe(_CH_RESP)
            if hasattr(self, "_listener_ready"):
                self._listener_ready.set()
            while True:
                msg = ps.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg is None:
                    continue
                if msg.get("type") != "message":
                    continue
                try:
                    resp = json.loads(msg["data"])
                    if resp.get("client") != self.CLIENT_ID:
                        continue
                    self._on_resp(resp)
                except Exception:
                    pass
        except Exception as e:
            if hasattr(self, "_listener_ready"):
                self._listener_ready.set()
            print(f"[XCPMaster] Listener perdu: {e}")

    def _on_resp(self, resp: dict):
        req_id   = resp.get("req_id", "")
        cmd_name = resp.get("cmd",    "")
        with self._lock:
            if req_id and req_id in self._pending:
                self._responses[req_id] = resp
                self._pending[req_id].set()
            elif cmd_name and cmd_name in self._pending:
                self._responses[cmd_name] = resp
                self._pending[cmd_name].set()
        if self._on_response:
            try:
                self._on_response(
                    resp.get("cmd"), resp.get("status"),
                    resp.get("data"), resp.get("error"),
                )
            except Exception:
                pass

    # ── Envoi + attente réponse ────────────────────────────────────

    def _send(self, cmd: dict, timeout: float = _CMD_TIMEOUT) -> dict:
        if self._r is None:
            raise XCPError("Redis non connecté")

        if hasattr(self, "_listener_ready") and not self._listener_ready.is_set():
            self._listener_ready.wait(timeout=0.5)

        cmd_name = cmd.get("cmd", "")
        req_id   = uuid.uuid4().hex
        event    = threading.Event()

        with self._lock:
            self._pending[req_id]   = event
            self._pending[cmd_name] = event
            self._responses.pop(req_id,   None)
            self._responses.pop(cmd_name, None)

        try:
            payload = json.dumps({**cmd, "client": self.CLIENT_ID, "req_id": req_id})
            self._r.publish(_CH_CMD, payload)
        except Exception as e:
            with self._lock:
                self._pending.pop(req_id,   None)
                self._pending.pop(cmd_name, None)
            raise XCPError(f"Envoi échoué: {e}")

        if not event.wait(timeout):
            with self._lock:
                self._pending.pop(req_id,   None)
                self._pending.pop(cmd_name, None)
            raise XCPError(f"Timeout {cmd_name} après {timeout}s")

        with self._lock:
            resp = (self._responses.pop(req_id,   None) or
                    self._responses.pop(cmd_name, {}))
            self._pending.pop(req_id,   None)
            self._pending.pop(cmd_name, None)

        if resp.get("status") == "ERR":
            raise XCPError(resp.get("error", "Erreur slave inconnue"))

        return resp.get("data", {})

    # ── API publique ───────────────────────────────────────────────

    def upload(self, key: str) -> Any:
        """SHORT_UPLOAD — lit la valeur courante d'un paramètre BCM."""
        data = self._send({"cmd": "SHORT_UPLOAD", "key": key})
        return data.get("value")

    def download(self, key: str, value: Any) -> bool:
        """DOWNLOAD — écrit une valeur dans bcm_rte (RAM + disque)."""
        self._send({"cmd": "DOWNLOAD", "key": key, "value": value})
        return True

    def get_a2l(self) -> dict:
        """Retourne le descripteur complet des paramètres."""
        if self._a2l_cache is not None:
            return self._a2l_cache
        if _LOCAL_A2L is not None:
            self._a2l_cache = _LOCAL_A2L
            return self._a2l_cache
        data = self._send({"cmd": "GET_A2L"})
        self._a2l_cache = data.get("a2l", {})
        return self._a2l_cache

    def get_status(self) -> dict:
        """GET_STATUS — valeurs courantes de tous les paramètres."""
        return self._send({"cmd": "GET_STATUS"})

    def restore_default(self, key: str) -> bool:
        """Remet un paramètre à sa valeur par défaut A2L."""
        a2l  = self.get_a2l()
        meta = a2l.get(key)
        if meta is None:
            raise XCPError(f"Paramètre inconnu: {key}")
        return self.download(key, meta["default"])

    def restore_all_defaults(self) -> dict[str, bool]:
        """Remet tous les paramètres à leurs valeurs A2L par défaut."""
        a2l     = self.get_a2l()
        results = {}
        for key, meta in a2l.items():
            try:
                self.download(key, meta["default"])
                results[key] = True
            except XCPError:
                results[key] = False
        return results

    def is_redis_ok(self) -> bool:
        if self._r is None:
            return False
        try:
            return bool(self._r.ping())
        except Exception:
            return False