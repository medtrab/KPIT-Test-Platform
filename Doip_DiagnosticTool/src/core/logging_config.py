#!/usr/bin/env python3
"""
core/logging_config.py

Centralised logging configuration — DoIP/UDS HIL Diagnostic Platform
ASPICE-compliant structured logging with per-module file rotation.

Log format:
    YYYY-MM-DD HH:MM:SS | LEVEL   | module.name | function | message

Log files (logs/ at project root, 1 MB rotation, 3 backups):
    session.log                 — full session trace (all modules)
    doip_protocol.log           — DoIP frame encoding/decoding
    diagnostic_client.log       — ECU discovery & routing activation
    ecu_server.log              — UDS server responses

Usage:
    from core.logging_config import setup_logging
    setup_logging()
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

LOG_FORMAT  = "%(asctime)s | %(levelname)-7s | %(name)-40s | %(funcName)-30s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

MAX_BYTES    = 1_048_576   # 1 MB per file
BACKUP_COUNT = 3

# Project root = two levels above core/logging_config.py
_HERE         = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT  = os.path.dirname(_HERE)
LOG_DIR       = os.path.join(PROJECT_ROOT, "logs")

# Per-module log files
_MODULE_FILES = {
    "core.transport.doip_protocol":    "doip_protocol.log",
    "core.transport.diagnostic_client":"diagnostic_client.log",
    "core.transport.ecu_server":       "ecu_server.log",
}

# Session log — captures everything
_SESSION_LOG = "session.log"

# Guard against double-initialisation
_configured = False

# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_log_dir() -> bool:
    """Create logs/ directory and verify write access. Returns True on success."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        probe = os.path.join(LOG_DIR, ".probe")
        with open(probe, "w") as f:
            f.write("")
        os.remove(probe)
        return True
    except OSError as exc:
        print(
            f"[HIL-LOGGING] WARNING: Cannot write to log directory '{LOG_DIR}': {exc}\n"
            f"              File logging is disabled — console only.",
            file=sys.stderr,
        )
        return False


def _formatter() -> logging.Formatter:
    return logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)


def _console_handler() -> logging.Handler:
    h = logging.StreamHandler(sys.stdout)
    h.setLevel(logging.INFO)
    h.setFormatter(_formatter())
    return h


def _file_handler(filename: str, level: int = logging.DEBUG) -> Optional[logging.Handler]:
    """Create a RotatingFileHandler. Returns None on failure."""
    path = os.path.join(LOG_DIR, filename)
    try:
        h = RotatingFileHandler(
            path,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        h.setLevel(level)
        h.setFormatter(_formatter())
        return h
    except OSError as exc:
        print(f"[HIL-LOGGING] ERROR: Cannot open log file '{path}': {exc}", file=sys.stderr)
        return None

# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def setup_logging(console_level: int = logging.INFO) -> None:
    """
    Initialise the logging system for the HIL Diagnostic Platform.

    Call once at application startup (main.py). Safe to call multiple times
    — subsequent calls are no-ops.

    Args:
        console_level: Minimum log level for console output (default: INFO).
    """
    global _configured
    if _configured:
        return

    file_ok = _ensure_log_dir()

    # ── Root logger ───────────────────────────────────────────────────────────
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)
    root.addHandler(_console_handler())

    if file_ok:
        # ── Session log — full trace of everything ────────────────────────────
        session_h = _file_handler(_SESSION_LOG, level=logging.DEBUG)
        if session_h:
            root.addHandler(session_h)

        # ── Per-module dedicated log files ────────────────────────────────────
        for module_name, filename in _MODULE_FILES.items():
            logger = logging.getLogger(module_name)
            logger.handlers.clear()
            fh = _file_handler(filename, level=logging.DEBUG)
            if fh:
                logger.addHandler(fh)
            logger.propagate = True   # also goes to session.log

    _configured = True

    # Announce initialisation
    _log = logging.getLogger(__name__)
    _log.info(
        "HIL Diagnostic Platform — Logging initialised | "
        f"Project: {PROJECT_ROOT} | "
        f"Log dir: {LOG_DIR if file_ok else 'DISABLED (console only)'}"
    )


def get_logger(name: str) -> logging.Logger:
    """
    Convenience wrapper — returns a named logger.

    Usage:
        from core.logging_config import get_logger
        log = get_logger(__name__)
        log.info("Worker started")
    """
    return logging.getLogger(name)
