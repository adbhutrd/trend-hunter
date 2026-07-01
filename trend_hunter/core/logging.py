"""loguru configuration — once-only, idempotent, structured JSONL with daily rotation."""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

_CONFIGURED = False


def configure(log_dir: Path = Path("./data/logs"), level: str = "INFO") -> None:
    """Configure the global loguru logger.

    Safe to call multiple times — only the first call applies. Two sinks:
      1. stderr (colourised, level-controlled)
      2. JSONL file with daily rotation, 30-day retention, gzip compression
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        colorize=True,
        format=(
            "<green>{time:HH:mm:ss}</green> <level>{level: <7}</level>"
            " {name}:{function}:{line} — {message}"
        ),
        backtrace=False,
        diagnose=False,
    )
    logger.add(
        log_dir / "trend_hunter_{time:YYYY-MM-DD}.jsonl",
        rotation="00:00",
        retention="30 days",
        compression="gz",
        serialize=True,
        level="DEBUG",
        enqueue=True,
    )
    _CONFIGURED = True


def get():
    """Return the configured loguru logger."""
    return logger
