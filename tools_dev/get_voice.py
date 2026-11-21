r"""Download the Kokoro voice files (~350 MB) into %LOCALAPPDATA%\Coco\models\kokoro.

.venv\Scripts\python tools_dev\get_voice.py
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coco.core.config import load_config  # noqa: E402
from coco.voice.tts import MODEL_FILE, VOICES_FILE, model_dir  # noqa: E402

BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"


def main() -> int:
    target = model_dir(load_config().voice)
    target.mkdir(parents=True, exist_ok=True)
    for name in (MODEL_FILE, VOICES_FILE):
        path = target / name
        if path.exists() and path.stat().st_size > 1_000_000:
            print(f"{name}: already there ({path.stat().st_size // 1_000_000} MB)")
            continue
        print(f"Downloading {name} ...", flush=True)
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(BASE + name, tmp)
        tmp.replace(path)
        print(f"  saved {path} ({path.stat().st_size // 1_000_000} MB)")
    print("Done. Restart Coco (or tray > Voice) to hear her.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
