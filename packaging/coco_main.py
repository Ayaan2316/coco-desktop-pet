"""Entry point for Coco.exe.

Coco.exe                                  the cat
Coco.exe --hub                            the dashboard
Coco.exe --keep                           the keeper
Coco.exe --selftest [--gpu] [--out FILE]  load every part and write a report
"""

from __future__ import annotations

import multiprocessing
import sys
import time
import traceback
from pathlib import Path

if not getattr(sys, "frozen", False):  # from source: python packaging\coco_main.py ...
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def selftest(gpu: bool, out: Path | None) -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, fn) -> None:
        t = time.perf_counter()
        try:
            detail = fn() or ""
            results.append((name, True, f"{detail} ({time.perf_counter() - t:.1f}s)".strip()))
        except BaseException as exc:  # noqa: BLE001 - report everything
            tb = traceback.format_exception_only(type(exc), exc)[-1].strip()
            results.append((name, False, tb))

    def qt():
        from PySide6.QtGui import QPixmap
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication(sys.argv[:1])
        from coco.core.paths import ASSETS_DIR

        pix = QPixmap(str(ASSETS_DIR / "icons" / "tray.png"))
        assert not pix.isNull(), "tray icon didn't load (Qt image plugins missing?)"
        return f"Qt {app.applicationVersion() or 'ok'}"

    def files():
        from coco.core.catdef import load_cat
        from coco.core.config import CONFIG_FILE, load_config
        from coco.core.paths import ASSETS_DIR, CATS_DIR

        cfg = load_config(CONFIG_FILE)
        cat = load_cat(cfg.cat)
        sprites = len(list((CATS_DIR / cfg.cat / "sprites").rglob("*.png")))
        assert sprites > 0, "no sprites"
        assert (ASSETS_DIR / "bed").exists(), "no bed art"
        return f"{cat.display_name}, {sprites} sprites, config {CONFIG_FILE}"

    def app_modules():
        import coco.app  # noqa: F401 - every tool, the brain, the UI
        from coco.tools.registry import REGISTRY

        assert len(REGISTRY.specs) >= 15, f"only {len(REGISTRY.specs)} tools registered"
        return f"{len(REGISTRY.specs)} tools"

    def hub_modules():
        import coco_hub.main  # noqa: F401 - nicegui, the pages

        return "ok"

    def windows_bits():
        import mss  # noqa: F401
        import psutil
        import pycaw.pycaw  # noqa: F401
        import uiautomation as auto
        import win32clipboard  # noqa: F401

        root = auto.GetRootControl()
        return f"desktop '{root.Name}', {psutil.cpu_count()} CPUs"

    def audio():
        import sounddevice as sd

        return f"{len(sd.query_devices())} audio devices"

    def voice():
        import espeakng_loader  # noqa: F401
        import kokoro_onnx  # noqa: F401
        import onnxruntime as ort

        return "onnxruntime " + ort.__version__

    def ears():
        import ctranslate2
        import faster_whisper  # noqa: F401
        from openwakeword.utils import AudioFeatures  # noqa: F401

        from coco.voice.stt import enable_cuda_dlls

        cuda = enable_cuda_dlls()
        return f"ctranslate2 {ctranslate2.__version__}, CUDA GPUs: " + (
            str(ctranslate2.get_cuda_device_count()) if cuda else "0"
        )

    def hear_on_gpu():
        import numpy as np
        from faster_whisper import WhisperModel

        from coco.core.config import CONFIG_FILE, load_config
        from coco.core.paths import user_data_dir
        from coco.voice.stt import enable_cuda_dlls

        assert enable_cuda_dlls(), "no CUDA GPU"
        stt = load_config(CONFIG_FILE).stt
        root = stt.model_dir or str(user_data_dir() / "models" / "whisper")
        model = WhisperModel(stt.model, device="cuda", compute_type="int8_float16",
                             download_root=root, local_files_only=True)  # fmt: skip
        noise = (np.random.default_rng(0).standard_normal(32000) * 0.01).astype(np.float32)
        segments, _ = model.transcribe(noise, language="en", beam_size=1)
        list(segments)
        return f"{stt.model} on the GPU"

    for name, fn in (("qt", qt), ("files", files), ("app", app_modules), ("hub", hub_modules),
                     ("windows", windows_bits), ("audio", audio), ("voice", voice),
                     ("ears", ears)):  # fmt: skip
        check(name, fn)
    if gpu:
        check("gpu-hearing", hear_on_gpu)

    from coco import __version__

    lines = [f"Coco {__version__} selftest ({sys.executable})"]
    lines += [f"{'ok  ' if ok else 'FAIL'} {name}: {detail}" for name, ok, detail in results]
    report = "\n".join(lines) + "\n"
    from coco.core.paths import log_dir

    (log_dir() / "selftest.txt").write_text(report, encoding="utf-8")
    if out is not None:
        out.write_text(report, encoding="utf-8")
    if sys.stdout is not None:
        print(report, end="")
    return 0 if all(ok for _n, ok, _d in results) else 1


def main() -> int:
    multiprocessing.freeze_support()
    argv = sys.argv[1:]
    if "--selftest" in argv:
        out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else None
        return selftest("--gpu" in argv, out)
    if "--keep" in argv:
        from coco.keeper import main as keeper_main

        return keeper_main()
    if "--hub" in argv:
        from coco_hub.main import main as hub_main

        return hub_main()
    from coco.app import main as cat_main

    return cat_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
