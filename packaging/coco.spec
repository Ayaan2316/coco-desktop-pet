# PyInstaller recipe for Coco.exe (one folder: dist\Coco\Coco.exe + _internal\).
# Build with:  .venv\Scripts\python tools_dev\build_exe.py   (it runs this, then tests it)
# ruff: noqa
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
SITE = Path(os.environ["COCO_SITE_PACKAGES"])
ICON = os.environ.get("COCO_ICON") or None
# The CUDA DLLs her ears need (the build script decides which; see CUDA_KEEP there).
CUDA = [line for line in os.environ.get("COCO_CUDA_DLLS", "").split(";") if line]

datas = [
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "cats"), "cats"),
    (str(ROOT / "config.yaml"), "."),
]
binaries = []
hiddenimports = []

for pkg in ("nicegui", "espeakng_loader", "kokoro_onnx", "uiautomation", "faster_whisper",
            "openwakeword", "ctranslate2", "phonemizer"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("coco") + collect_submodules("coco_hub")
hiddenimports += collect_submodules("uvicorn") + ["win32timezone"]

for rel in CUDA:  # e.g. nvidia/cublas/bin/cublas64_12.dll -> same place inside _internal
    src = SITE / rel
    binaries.append((str(src), str(Path(rel).parent)))

a = Analysis(
    [str(ROOT / "packaging" / "coco_main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "ruff", "PyQt5", "PyQt6",
              "PySide6.QtWebEngineCore", "PySide6.Qt3DCore", "torch", "tensorflow",
              "tools_dev", "tests"],
    noarchive=False,
)
# never let PyInstaller drag in the whole CUDA set on its own: only the CUDA list above
a.binaries = [b for b in a.binaries
              if not (b[0].replace("\\", "/").startswith("nvidia/") and b[0].replace("\\", "/") not in CUDA)]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Coco",
    console=False,
    icon=ICON,
    version=os.environ.get("COCO_VERSION_FILE") or None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Coco")
