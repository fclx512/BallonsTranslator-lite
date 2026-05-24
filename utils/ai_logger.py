"""
Logging setup for the AI chat subsystem.

Provides a module-level ``ai_logger`` singleton configured with:
- RotatingFileHandler → config/ai_chat.log (5 MB, 3 backups)
- StreamHandler → stderr (for console/debug output)
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

_logger: logging.Logger | None = None


def get_ai_logger() -> logging.Logger:
    """Return the singleton AI chat logger, creating it on first call."""
    global _logger
    if _logger is not None:
        return _logger

    _logger = logging.getLogger('ai_chat')
    _logger.setLevel(logging.DEBUG)
    _logger.propagate = False  # don't bubble to root logger

    fmt = logging.Formatter(
        '%(asctime)s [%(levelname)-5s] %(name)s | %(message)s',
        datefmt='%H:%M:%S',
    )

    # File handler — write next to config.json
    log_dir = os.path.join(os.path.dirname(__file__), '..', 'config')
    os.makedirs(log_dir, exist_ok=True)
    fh = RotatingFileHandler(
        os.path.join(log_dir, 'ai_chat.log'),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding='utf-8',
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    _logger.addHandler(fh)

    # Console handler — only INFO and above
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    _logger.addHandler(ch)

    return _logger


# Auto-initialize on first import so downstream `logging.getLogger('ai_chat')` works
get_ai_logger()
