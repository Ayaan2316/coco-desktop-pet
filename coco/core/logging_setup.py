"""Logging to a rotating file in %LOCALAPPDATA%\\Coco\\logs."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from coco.core.paths import log_dir


def setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    file_handler = RotatingFileHandler(
        log_dir() / "coco.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # Kokoro's phonemizer warns "words count mismatch" on most Hinglish lines: harmless.
    logging.getLogger("phonemizer").setLevel(logging.ERROR)

    # pythonw.exe (used by "Start with Windows") has no console: sys.stderr is None there.
    if sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(fmt)
        root.addHandler(console)
