"""Project and user data paths, for both source runs and Coco.exe."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:  # PyInstaller: the bundle's data folder (_internal next to Coco.exe)
    PROJECT_ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATS_DIR = PROJECT_ROOT / "cats"
ASSETS_DIR = PROJECT_ROOT / "assets"
DEFAULT_CONFIG = PROJECT_ROOT / "config.yaml"


def user_data_dir() -> Path:
    """Per-user writable folder for settings + logs: %LOCALAPPDATA%\\Coco.

    Kept outside the project so it survives updates and works the same for Coco.exe.
    """
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    path = Path(base) / "Coco"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    path = user_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_file(frozen: bool = FROZEN, default: Path = DEFAULT_CONFIG) -> Path:
    """Which config.yaml to read: the project's, or (Coco.exe) the user's own copy."""
    if not frozen:
        return default
    mine = user_data_dir() / "config.yaml"
    if not mine.exists() and default.exists():
        shutil.copyfile(default, mine)
    return mine


CONFIG_FILE = config_file()
