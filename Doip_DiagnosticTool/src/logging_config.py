#!/usr/bin/env python3
"""
Centralised logging configuration for DoIP/UDS Simulator.
Provides structured logging, per‑module file handlers with rotation,
console output, and ASPICE‑like traceability.

All log entries follow the format:
    YYYY-MM-DD HH:MM:SS,mmm | LEVEL | module.name | function_name | message

Log files are stored in ./logs/ and rotated (1 MB, 3 backups).

This version includes:
- Absolute path for logs/ (always at project root)
- Reliable main script detection (works with -m, IDE, absolute path)
- Permission and directory creation error handling
- Fallback to console only if file logging fails
- Idempotent, safe to call multiple times
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from typing import Dict, Optional

# -----------------------------------------------------------------------------
# Global flag to ensure configuration runs only once
# -----------------------------------------------------------------------------
_configured = False

# -----------------------------------------------------------------------------
# Module‑to‑logfile mapping
# -----------------------------------------------------------------------------
MODULE_LOG_FILES: Dict[str, str] = {
    'doip_protocol': 'doip_protocol.log',
    'ecu_server': 'ecu_server.log',
    'diagnostic_client': 'diagnostic_client.log',
}

# -----------------------------------------------------------------------------
# Log format (ASPICE traceability)
# -----------------------------------------------------------------------------
LOG_FORMAT = '%(asctime)s | %(levelname)-7s | %(name)s | %(funcName)s | %(message)s'
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

# -----------------------------------------------------------------------------
# Log directory setup – absolute path to project root/logs/
# -----------------------------------------------------------------------------
# logging_config.py is in src/, project root is one level above
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')

def ensure_log_dir() -> bool:
    """Create log directory if it does not exist. Returns True on success."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        # Test write access
        test_file = os.path.join(LOG_DIR, '.write_test')
        with open(test_file, 'w') as f:
            f.write('test')
        os.remove(test_file)
        return True
    except Exception as e:
        # Print directly to stderr – logging not yet configured
        print(f"[FATAL] Cannot create/write to log directory '{LOG_DIR}': {e}", file=sys.stderr)
        return False

# -----------------------------------------------------------------------------
# Formatter singleton
# -----------------------------------------------------------------------------
def get_formatter() -> logging.Formatter:
    return logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)

# -----------------------------------------------------------------------------
# Console handler (INFO+)
# -----------------------------------------------------------------------------
def create_console_handler() -> logging.Handler:
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(get_formatter())
    return handler

# -----------------------------------------------------------------------------
# File handler for a specific module – with failure tolerance
# -----------------------------------------------------------------------------
def create_file_handler(module_name: str) -> Optional[logging.Handler]:
    """
    Create a RotatingFileHandler for the given module.
    Returns None if creation fails (e.g., permission denied).
    """
    log_file = os.path.join(LOG_DIR, MODULE_LOG_FILES[module_name])
    try:
        handler = RotatingFileHandler(
            log_file,
            maxBytes=1_048_576,   # 1 MB
            backupCount=3,
            encoding='utf-8'
        )
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(get_formatter())
        return handler
    except Exception as e:
        print(f"[ERROR] Cannot create log file '{log_file}': {e}", file=sys.stderr)
        return None

# -----------------------------------------------------------------------------
# Reliable detection of the main script name
# -----------------------------------------------------------------------------
def _get_main_script_name() -> str:
    """
    Return the base name of the main script (without .py extension),
    regardless of how the script was launched: direct, -m, IDE, absolute path.
    """
    main_module = sys.modules.get('__main__')
    if main_module and hasattr(main_module, '__file__') and main_module.__file__:
        filename = main_module.__file__
        return os.path.splitext(os.path.basename(filename))[0]
    # Last resort fallback (unlikely in normal use)
    return os.path.splitext(os.path.basename(sys.argv[0]))[0]

# -----------------------------------------------------------------------------
# Main setup function – idempotent, error‑tolerant
# -----------------------------------------------------------------------------
def setup_logging() -> None:
    """Configure the entire logging system with robust error handling."""
    global _configured
    if _configured:
        return

    # ----- Prepare log directory ---------------------------------------------
    log_dir_ok = ensure_log_dir()

    # ----- Root logger configuration -----------------------------------------
    root_logger = logging.getLogger()
    # Remove any existing handlers (e.g., from basicConfig)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    root_logger.setLevel(logging.DEBUG)

    # Always add a console handler
    root_logger.addHandler(create_console_handler())

    # ----- Per‑module file handlers (if directory is writable) --------------
    if log_dir_ok:
        for module_name in MODULE_LOG_FILES:
            module_logger = logging.getLogger(module_name)
            # Avoid duplicate file handlers
            module_logger.handlers.clear()
            file_handler = create_file_handler(module_name)
            if file_handler:
                module_logger.addHandler(file_handler)
            module_logger.propagate = True

        # ----- Main script logger ('__main__') ------------------------------
        script_name = _get_main_script_name()
        if script_name in MODULE_LOG_FILES:
            main_logger = logging.getLogger('__main__')
            main_logger.handlers.clear()
            file_handler = create_file_handler(script_name)
            if file_handler:
                main_logger.addHandler(file_handler)
            main_logger.propagate = True
    else:
        # Log directory not usable – console only
        print("[WARNING] File logging disabled (logs/ not writable).", file=sys.stderr)

    # ----- Finalisation -----------------------------------------------------
    _configured = True
    # Log initialisation confirmation (will appear in console and possibly file)
    logging.getLogger(__name__).info(
        f"Logging initialised. Main script: {_get_main_script_name()}, "
        f"File logging: {'enabled' if log_dir_ok else 'disabled'}"
    )