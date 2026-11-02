"""Logging setup for REW."""

import logging
import sys
from pathlib import Path


def configure_logging(verbosity: int = 0, log_file: Path | None = None) -> None:
    """Configure root logger.

    verbosity: 0=WARNING, 1=INFO, 2=DEBUG
    """
    levels = {0: logging.WARNING, 1: logging.INFO, 2: logging.DEBUG}
    level = levels.get(verbosity, logging.DEBUG)

    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%H:%M:%S"

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(level=level, format=fmt, datefmt=datefmt, handlers=handlers)

    # Suppress noisy third-party loggers unless debug is requested.
    if level > logging.DEBUG:
        for noisy in ("httpx", "httpcore", "playwright", "asyncio"):
            logging.getLogger(noisy).setLevel(logging.ERROR)
