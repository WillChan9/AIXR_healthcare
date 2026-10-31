"""Centralized logging for all Agent modules.

Every module obtains its logger from here::

    from .logger import get_logger
    logger = get_logger("ModuleName")

All loggers write to the console and to a single combined log file,
``Agent/api_files/agent.log`` (the evaluation scripts parse this file).
A module can optionally also keep its own per-module log file, e.g.
``api_files/reflection_log.log``. The ``api_files/`` directory is created
at import time if it does not exist.
"""

import logging
import os
from typing import Optional

# Resolve paths once at import time.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_DIR = os.path.join(_PROJECT_ROOT, "api_files")
os.makedirs(_LOG_DIR, exist_ok=True)

_COMBINED_LOG = os.path.join(_LOG_DIR, "agent.log")

_FORMATTER = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Shared file handler: created once, reused by every module logger.
_combined_handler: Optional[logging.FileHandler] = None


def _get_combined_handler() -> logging.FileHandler:
    """Return the shared agent.log handler, creating it on first use."""
    global _combined_handler
    if _combined_handler is None:
        _combined_handler = logging.FileHandler(_COMBINED_LOG, encoding="utf-8")
        _combined_handler.setLevel(logging.DEBUG)
        _combined_handler.setFormatter(_FORMATTER)
    return _combined_handler


def get_logger(
    name: str,
    *,
    module_log_file: Optional[str] = None,
    level: int = logging.DEBUG,
) -> logging.Logger:
    """Return a logger that writes to the combined agent.log **and** console.

    Calling this again for the same name replaces the logger's handlers
    rather than duplicating them.

    Args:
        name: Logger name (typically the module name, e.g. "ReflectionModule").
        module_log_file: Optional path (relative to project root) for a
            per-module log file, e.g. "api_files/reflection_log.log".
        level: Logging level (default DEBUG).

    Returns:
        The configured ``logging.Logger``.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid duplicate handlers if get_logger is called more than once for the
    # same name (e.g. during module reload).
    if logger.handlers:
        logger.handlers.clear()

    # 1. Combined log file (shared across all modules).
    logger.addHandler(_get_combined_handler())

    # 2. Console output.
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(_FORMATTER)
    logger.addHandler(console)

    # 3. Optional per-module log file.
    if module_log_file:
        path = os.path.join(_PROJECT_ROOT, module_log_file)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(_FORMATTER)
        logger.addHandler(fh)

    return logger
