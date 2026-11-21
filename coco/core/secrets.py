"""Local secrets (API keys) in %LOCALAPPDATA%\\Coco\\secrets.json."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from coco.core.paths import user_data_dir

log = logging.getLogger(__name__)


class Secrets:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (user_data_dir() / "secrets.json")
        self._lock = threading.Lock()
        self._data: dict[str, object] = {}
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            log.warning("Could not read the secrets file; starting empty")

    def get(self, key: str, default=None):
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        with self._lock:
            if value is None:
                self._data.pop(key, None)
            else:
                self._data[key] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def __repr__(self) -> str:  # never print the values
        return f"Secrets({len(self._data)} keys)"
