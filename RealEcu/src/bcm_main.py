#!/usr/bin/env python3
"""
bcm_main.py  v4  -- Redis integre
"""
import json
import threading
import time

from bcm_rte         import RTE
from bcm_protocol    import ProtocolLayer, DOIP_PORT, DSC_DEFAULT
from bcm_application import ApplicationLayer
from dtc_manager     import DTCManager

_REDIS_PUBLISH_PERIOD = 0.100   # 100ms


class BCM:
    def __init__(self, redis_host="127.0.0.1", redis_port=6379):
        print("=" * 60)
        print("  BCM WipeWash -- Architecture 3 Couches + Redis")
        print("=" * 60)

        self.rte      = RTE()
        self.dtc      = DTCManager()
        self._rhost   = redis_host
        self._rport   = redis_port

        # Connexion Redis (mode degrade si indisponible)
        self.rte.redis_connect(redis_host, redis_port)

        self.protocol = ProtocolLayer(self.rte, self.dtc)
        self.protocol.init_can()
        self.protocol.init_lin()

        self.app = ApplicationLayer(self.rte, self.dtc)

        print(f"  LIN   : 19.2kbps | Cycle 0x16=400ms | Cycle 0x17=800ms")
        print(f"  CAN   : can0")
        print(f"  DoIP  : port {DOIP_PORT}")
        print(f"  Redis : {redis_host}:{redis_port}  ({'OK' if self.rte._redis_ok else 'INDISPONIBLE'})")
        print("=" * 60)

    # ── Thread T-REDIS  (publication 100ms) ─────────────────────────
    def _thread_redis_publish(self):
        print(f"[T-REDIS] Demarre | periode={_REDIS_PUBLISH_PERIOD*1000:.0f}ms")
        while True:
            self.rte.redis_publish()
            time.sleep(_REDIS_PUBLISH_PERIOD)

    # ── Thread T-REDIS-CMD  (ecoute stimuli tests Platform) ─────────
    def _thread_redis_cmd(self):
        """
        Ecoute le canal pub/sub 'rte_cmd' et applique les commandes SET
        recues depuis la Platform au RTE local.

        ISOLATION : utilise une connexion Redis DISTINCTE de T-REDIS.
        Le pubsub Redis est bloquant (listen()) et ne doit jamais partager
        une connexion avec le thread de publication (pipeline).
        Reconnexion automatique en cas d'erreur.
        """
        print("[T-REDIS-CMD] Demarre | ecoute canal 'rte_cmd'")
        _RETRY_S = 3   # secondes entre tentatives
        while True:
            # Attendre que Redis soit disponible (T-REDIS peut etre encore en init)
            if not self.rte._redis_ok:
                print("[T-REDIS-CMD] Redis non disponible -- attente 3s...")
                time.sleep(_RETRY_S)
                continue
            ps = None
            r  = None
            try:
                import redis as _r
                # Connexion independante : ne jamais reutiliser self.rte._redis
                r  = _r.Redis(
                    host=self._rhost, port=self._rport, db=0,
                    socket_connect_timeout=2,
                    socket_timeout=30,   # timeout long pour listen() bloquant
                )
                r.ping()
                ps = r.pubsub(ignore_subscribe_messages=True)
                ps.subscribe("rte_cmd")
                print("[T-REDIS-CMD] Abonne sur 'rte_cmd'")

                # Boucle de reception non-bloquante (timeout=1s pour pouvoir
                # verifier _running si on ajoute un flag d'arret plus tard)
                while True:
                    msg = ps.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if msg is None:
                        continue
                    if msg.get("type") != "message":
                        continue
                    try:
                        data = json.loads(msg["data"])
                        key  = data.get("key")
                        val  = data.get("value")
                        if key and val is not None:
                            self.rte.redis_apply_cmd(key, val)
                    except (json.JSONDecodeError, TypeError) as e:
                        print(f"[T-REDIS-CMD] Parse erreur: {e}")
                    except Exception as e:
                        print(f"[T-REDIS-CMD] Apply erreur: {e}")

            except Exception as e:
                print(f"[T-REDIS-CMD] Erreur connexion/listen: {e} -- retry {_RETRY_S}s")
            finally:
                # Nettoyage propre avant reconnexion
                if ps is not None:
                    try:
                        ps.unsubscribe()
                        ps.close()
                    except Exception:
                        pass
                if r is not None:
                    try:
                        r.close()
                    except Exception:
                        pass
            time.sleep(_RETRY_S)

    # ── Demarrage ────────────────────────────────────────────────────
    def start(self):
        self.protocol.start()
        self.app.start()

        threads = [
            ("T-REDIS",     self._thread_redis_publish),
            ("T-REDIS-CMD", self._thread_redis_cmd),
            ("T-LIN",       self.protocol.thread_lin_scheduler),
            ("T-CAN",       self.protocol.thread_can_receiver),
            ("T-CAN-WC",    self.protocol.thread_can_wc_command),
            ("T-WSM",       self.app.thread_wsm_control),
            ("T-PUMP",      self.app.thread_pump_guard),
            ("T-DIAG",      self.app.thread_diagnostic),
        ]

        print("[BCM] Lancement threads :")
        for name, target in threads:
            t = threading.Thread(target=target, daemon=True, name=name)
            t.start()
            print(f"  + {name}")

        print("[BCM] Tous les threads actifs")
        try:
            self.protocol.run()
        except KeyboardInterrupt:
            print("\n[BCM] Arret (Ctrl+C)")
        finally:
            self.app.stop()
            self.protocol.stop()
            print("[BCM] Arrete proprement")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--redis-host", default="127.0.0.1")
    p.add_argument("--redis-port", type=int, default=6379)
    args = p.parse_args()
    BCM(redis_host=args.redis_host, redis_port=args.redis_port).start()
