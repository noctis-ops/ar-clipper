"""تهيئة السجلات (Logging) بشكل موحّد لكل الوحدات."""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

_CONFIGURED = False
_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATEFMT = "%H:%M:%S"


def setup_logging(level: str | int = "INFO", force: bool = False) -> None:
    """يهيّئ الجذر مرة واحدة. ``AR_CLIPPER_LOG_LEVEL`` يتجاوز المعامل."""
    global _CONFIGURED
    if _CONFIGURED and not force:
        return

    env_level = os.getenv("AR_CLIPPER_LOG_LEVEL")
    if env_level:
        level = env_level
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # إسكات المكتبات الثرثارة حتى لا تخفي سجلاتنا
    for noisy in ("urllib3", "huggingface_hub", "filelock", "numba", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: Optional[str] = None) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name or "arclipper")
