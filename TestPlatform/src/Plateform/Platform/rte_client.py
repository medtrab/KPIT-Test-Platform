"""
rte_client.py  —  Client Redis GET/SET pour la Platform WipeWash
================================================================
Permet a la Platform Qt de lire (GET) et d'ecrire (SET) directement
les variables du RTE du RpiBCM, sans passer par TCP/LIN/CAN.

Architecture :
  RpiBCM
  ├── bcm_rte.py   : ecrit rte:<key> dans Redis (T-REDIS, 100ms)
  │                  ecoute rte_cmd → applique au RTE local (T-REDIS-CMD)
  └── redis-server : port 6379

  PC (Platform Qt)
  └── RTEClient    : GET  redis.get("rte:state")
                     SET  redis.publish("rte_cmd", {"key":..,"value":..})
                     SUB  ecoute rte_changed pour push events

Cles publiees par le BCM (rte:<key>) :
  state, crs_wiper_op, ignition_status, reverse_gear, vehicle_speed,
  rain_intensity, front_motor_on, front_motor_speed, rear_motor_on,
  pump_active, pump_direction, motor_current_a, pump_current_a,
  lin_timeout_active, wc_available

Cles inscriptibles (stimuli de test) :
  crs_wiper_op, ignition_status, rain_intensity, vehicle_speed, reverse_gear
"""

import json
import threading
import uuid as _uuid

_REDIS_AVAILABLE = False
try:
    import redis as _redis_mod
    _REDIS_AVAILABLE = True
except ImportError:
    pass

# Codes wiper op (miroir de bcm_rte.py)
WOP_OFF        = 0
WOP_TOUCH      = 1
WOP_SPEED1     = 2
WOP_SPEED2     = 3
WOP_AUTO       = 4
WOP_FRONT_WASH = 5
WOP_REAR_WASH  = 6
WOP_REAR_WIPE  = 7

WOP_NAMES = {
    "OFF": WOP_OFF, "TOUCH": WOP_TOUCH, "SPEED1": WOP_SPEED1,
    "SPEED2": WOP_SPEED2, "AUTO": WOP_AUTO, "FRONT_WASH": WOP_FRONT_WASH,
    "REAR_WASH": WOP_REAR_WASH, "REAR_WIPE": WOP_REAR_WIPE,
}


class RTEClient:
    """
    Client Redis pour acceder au RTE du RpiBCM.
    Toutes les methodes sont non-bloquantes (timeout 1s).
    Si Redis est indisponible, les appels sont silencieusement ignores.
    """

    REDIS_PORT = 6379

    def __init__(self, host: str, port: int = 6379):
        self._host      = host
        self._port      = port
        self._connected = False
        self._r         = None
        self._sub_thread = None

        if not _REDIS_AVAILABLE:
            print("[RTEClient] package 'redis' absent — pip install redis")
            return

        self._connect()

    def _connect(self):
        try:
            self._r = _redis_mod.Redis(
                host=self._host, port=self._port, db=0,
                socket_connect_timeout=2,
                socket_timeout=1,
            )
            self._r.ping()
            self._connected = True
            print(f"[RTEClient] Connecte sur {self._host}:{self._port}")
        except Exception as e:
            self._connected = False
            print(f"[RTEClient] Connexion impossible ({e})")

    # ── GET ──────────────────────────────────────────────────────────────────
    def get(self, key: str) -> str | None:
        """
        Lit la valeur d'une variable RTE depuis Redis.
        Retourne une string ou None si cle absente / Redis indisponible.

        Exemple :
            state = client.get("state")          # "SPEED1"
            current = float(client.get("motor_current_a") or 0)
        """
        if not self._connected or self._r is None:
            return None
        try:
            val = self._r.get(f"rte:{key}")
            return val.decode("utf-8") if val else None
        except Exception:
            return None

    def get_int(self, key: str, default: int = 0) -> int:
        v = self.get(key)
        try:
            return int(v) if v is not None else default
        except (ValueError, TypeError):
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        v = self.get(key)
        try:
            return float(v) if v is not None else default
        except (ValueError, TypeError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        v = self.get(key)
        if v is None:
            return default
        return v.lower() in ("true", "1", "yes")

    # ── SET (stimuli de test) ─────────────────────────────────────────────────
    # Identifiant unique de cette instance Platform
    _CLIENT_ID = f"Redis-{_uuid.uuid4().hex[:6].upper()}"

    def set_cmd(self, key: str, value, client: str = None) -> bool:
        """
        Envoie une commande SET au RpiBCM via Redis pub/sub.
        Le BCM applique la valeur a son RTE local (T-REDIS-CMD).
        Seules les cles autorisees sont acceptees cote BCM.
        client : identifiant optionnel (défaut: _CLIENT_ID unique)

        Exemples :
            client.set_cmd("crs_wiper_op", 2)        # SPEED1
            client.set_cmd("ignition_status", 0)     # coupure ignition
            client.set_cmd("rain_intensity", 25)     # pluie forte
        """
        if not self._connected or self._r is None:
            return False
        try:
            payload = json.dumps({
                "key":    key,
                "value":  value,
                "client": client or self._CLIENT_ID,
            })
            self._r.publish("rte_cmd", payload)
            return True
        except Exception as e:
            print(f"[RTEClient] set_cmd({key}) erreur: {e}")
            return False

    def acquire_write_lock(self) -> bool:
        """Demande le verrou écriture RTE au BCM."""
        return self.set_cmd("__lock_acquire__", 1, client=self._CLIENT_ID)

    def release_write_lock(self) -> bool:
        """Libère le verrou écriture RTE."""
        return self.set_cmd("__lock_release__", 1, client=self._CLIENT_ID)

    def renew_write_lock(self) -> bool:
        """Renouvelle le TTL du verrou (heartbeat)."""
        return self.set_cmd("__lock_renew__", 1, client=self._CLIENT_ID)

    def subscribe_lock_status(self, callback) -> None:
        """Écoute les notifications du verrou sur rte_lock_status."""
        if not self._connected or self._r is None:
            return
        def _listen():
            try:
                import redis as _rm
                r  = _rm.Redis(host=self._host, port=self._port, db=0,
                               socket_connect_timeout=2, socket_timeout=30)
                ps = r.pubsub(ignore_subscribe_messages=True)
                ps.subscribe("rte_lock_status")
                for msg in ps.listen():
                    if msg["type"] == "message":
                        try:
                            callback(json.loads(msg["data"]))
                        except Exception:
                            pass
            except Exception as e:
                print(f"[RTEClient] subscribe_lock_status perdu: {e}")
        import threading
        threading.Thread(target=_listen, daemon=True,
                         name="RTEClient-LOCK-SUB").start()

    def set_wiper_op(self, op_name: str) -> bool:
        """
        Raccourci : set_cmd("crs_wiper_op", code).
        op_name : "OFF" | "TOUCH" | "SPEED1" | "SPEED2" | "AUTO"
                  "FRONT_WASH" | "REAR_WASH" | "REAR_WIPE"
        """
        code = WOP_NAMES.get(op_name.upper())
        if code is None:
            print(f"[RTEClient] Op inconnue : {op_name}")
            return False
        return self.set_cmd("crs_wiper_op", code)

    # ── Subscribe (push events) ───────────────────────────────────────────────
    def subscribe_changes(self, callback) -> None:
        """
        Lance un thread d'ecoute pub/sub sur rte_changed.
        callback(keys: list[str]) est appele a chaque flush BCM.

        Exemple :
            client.subscribe_changes(lambda keys: print("Changed:", keys))
        """
        if not self._connected or self._r is None:
            return

        def _listen():
            try:
                ps = self._r.pubsub()
                ps.subscribe("rte_changed")
                for msg in ps.listen():
                    if msg["type"] == "message":
                        try:
                            keys = json.loads(msg["data"])
                            callback(keys)
                        except Exception:
                            pass
            except Exception as e:
                print(f"[RTEClient] subscribe perdu: {e}")

        self._sub_thread = threading.Thread(
            target=_listen, daemon=True, name="RTEClient-SUB")
        self._sub_thread.start()

    # ── Propriétés publiques ──────────────────────────────────────────────────
    @property
    def host(self) -> str:
        """Adresse IP / hostname du serveur Redis."""
        return self._host

    @property
    def port(self) -> int:
        """Port du serveur Redis."""
        return self._port

    # ── Utilitaires ───────────────────────────────────────────────────────────
    def is_connected(self) -> bool:
        if not self._connected or self._r is None:
            return False
        try:
            return self._r.ping()
        except Exception:
            self._connected = False
            return False

    def get_all_public(self) -> dict:
        """Retourne toutes les cles RTE publiees en un seul appel pipeline."""
        keys = [
            "state", "crs_wiper_op", "ignition_status", "reverse_gear",
            "vehicle_speed", "rain_intensity", "front_motor_on",
            "front_motor_speed", "rear_motor_on", "pump_active",
            "motor_current_a", "pump_current_a", "lin_timeout_active",
        ]
        if not self._connected or self._r is None:
            return {}
        try:
            pipe = self._r.pipeline(transaction=False)
            for k in keys:
                pipe.get(f"rte:{k}")
            values = pipe.execute()
            return {
                k: (v.decode("utf-8") if v else None)
                for k, v in zip(keys, values)
            }
        except Exception:
            return {}