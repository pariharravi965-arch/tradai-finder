"""Central logging setup using loguru."""
from __future__ import annotations

import sys
from pathlib import Path
from loguru import logger


def setup_logger(level: str = "INFO", log_file: str | None = None) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
    )
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_file,
            level=level,
            rotation="50 MB",
            retention="14 days",
            compression="zip",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function} - {message}",
        )
