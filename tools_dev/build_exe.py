r"""Build Coco.exe and the installer.

    .venv\Scripts\python tools_dev\build_exe.py                 build + selftest
    .venv\Scripts\python tools_dev\build_exe.py --zip           also zip dist\Coco
    .venv\Scripts\python tools_dev\build_exe.py --installer     also build Coco-Setup.exe
    .venv\Scripts\python tools_dev\build_exe.py --measure-cuda  check which CUDA DLLs are needed

Needs PyInstaller; the installer needs Inno Setup 6. Models are not bundled.
"""

from __future__ import annotations

import argparse
import os
import shutil
import site
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from coco import __version__  # noqa: E402

BUILD = ROOT / "build"
DIST = ROOT / "dist"
APP = DIST / "Coco"
SITE = Path(next(p for p in site.getsitepackages() if p.endswith("site-packages")))

# CUDA DLLs Whisper needs (check with --measure-cuda). cuDNN isn't required:
# ctranslate2 ships its own cudnn64_9.dll loader.
CUDA_KEEP = [
    "nvidia/cublas/bin/cublas64_12.dll",
    "nvidia/cublas/bin/cublasLt64_12.dll",
]

VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers=({v}, 0), prodvers=({v}, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
      StringStruct('ProductName', 'Coco'),
      StringStruct('FileDescription', 'Coco, the desktop cat'),
      StringStruct('FileVersion', '{s}'),
      StringStruct('ProductVersion', '{s}'),
      StringStruct('OriginalFilename', 'Coco.exe'),
      StringStruct('LegalCopyright', 'MIT licence')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"""


def make_icon() -> Path:
    """assets/icons/coco.ico from tray.png (the exe, the installer and the shortcuts)."""
    from PIL import Image

    png = ROOT / "assets" / "icons" / "tray.png"
    ico = png.with_name("coco.ico")
    if not ico.exists() or ico.stat().st_mtime < png.stat().st_mtime:
        with Image.open(png) as im:
            im.convert("RGBA").save(ico, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    return ico


def make_version_file() -> Path:
    parts = [int(x) for x in __version__.split(".")] + [0, 0, 0]
    path = BUILD / "version_info.txt"
    v = ", ".join(str(x) for x in parts[:3])
    path.write_text(VERSION_INFO.format(v=v, s=__version__), encoding="utf-8")
    return path


def build(cuda: list[str]) -> None:
    BUILD.mkdir(exist_ok=True)
    missing = [c for c in cuda if not (SITE / c).exists()]
    if missing:
        sys.exit(f"CUDA DLLs not found in {SITE}: {missing}")
    env = {
        **os.environ,
        "COCO_SITE_PACKAGES": str(SITE),
        "COCO_ICON": str(make_icon()),
        "COCO_VERSION_FILE": str(make_version_file()),
        "COCO_CUDA_DLLS": ";".join(cuda),
    }
    t = time.monotonic()
    subprocess.run(  # noqa: S603 - our own build
        [sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "coco.spec"),
         "--noconfirm", "--clean", "--distpath", str(DIST), "--workpath", str(BUILD / "pyi")],
        cwd=ROOT, env=env, check=True,
    )  # fmt: skip
    print(f"Built {APP / 'Coco.exe'} in {time.monotonic() - t:.0f} s")


def selftest(gpu: bool = True, quiet: bool = False) -> bool | None:
    """True / False, or None when Windows won't run the exe at all (Smart App Control
    blocks programs that aren't code-signed: the build may be fine, it can't be tested)."""
    out = BUILD / "selftest.txt"
    out.unlink(missing_ok=True)
    args = [str(APP / "Coco.exe"), "--selftest", "--out", str(out)] + (["--gpu"] if gpu else [])
    t = time.monotonic()
    try:
        code = subprocess.run(args, check=False, timeout=600).returncode  # noqa: S603
    except OSError as exc:
        if getattr(exc, "winerror", 0) == 4551:
            print("Windows (Smart App Control) won't start Coco.exe: it isn't code-signed. "
                  "The build can't be test-run on this PC.")  # fmt: skip
            return None
        raise
    took = time.monotonic() - t
    report = out.read_text(encoding="utf-8") if out.exists() else "(no report)\n"
    if not quiet:
        print(report + f"selftest exit {code} in {took:.1f} s")
    return code == 0


def folder_size(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6


HEAR = r"""
import os, sys, time
d, root = sys.argv[1], sys.argv[2]
os.add_dll_directory(d)
os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
sys.path.insert(0, root)
import numpy as np
from faster_whisper import WhisperModel
from coco.core.config import load_config
from coco.core.paths import user_data_dir
stt = load_config().stt
models = user_data_dir() / "models" / "whisper"
wav = user_data_dir() / "voice_test" / "01.wav"
if wav.exists():
    import wave
    with wave.open(str(wav)) as w:
        raw = np.frombuffer(w.readframes(w.getnframes()), np.int16)
        if w.getnchannels() > 1:
            raw = raw.reshape(-1, w.getnchannels()).mean(axis=1)
        rate = w.getframerate()
    audio = (raw / 32768.0).astype(np.float32)
    if rate != 16000:
        at = np.arange(0, len(audio), rate / 16000)
        audio = np.interp(at, np.arange(len(audio)), audio).astype(np.float32)
else:
    audio = (np.random.default_rng(0).standard_normal(48000) * 0.01).astype(np.float32)
t = time.perf_counter()
for name in (stt.model, str(models / stt.hinglish_model)):
    if os.sep in name and not os.path.isdir(name):
        continue
    m = WhisperModel(name, device="cuda", compute_type="int8_float16",
                     download_root=str(models), local_files_only=True)
    text = " ".join(s.text for s in m.transcribe(audio, beam_size=1)[0])
print(f"{time.perf_counter() - t:.1f}s {text[:40]!r}")
"""


def hears_with(dlls: list[str]) -> tuple[bool, str]:
    """Hard-link just these DLLs into a folder and transcribe on the GPU with only them."""
    test = BUILD / "cuda_test"
    shutil.rmtree(test, ignore_errors=True)
    test.mkdir(parents=True)
    for rel in dlls:
        os.link(SITE / rel, test / Path(rel).name)
    r = subprocess.run(  # noqa: S603 - our own test
        [sys.executable, "-c", HEAR, str(test), str(ROOT)],
        capture_output=True, text=True, timeout=600, check=False,
    )  # fmt: skip
    shutil.rmtree(test, ignore_errors=True)
    return r.returncode == 0, (r.stdout.strip() or r.stderr.strip()[-160:])


def measure_cuda() -> None:
    """From source (same DLLs as the build): take out each DLL in turn; keep it only if
    GPU hearing fails or gets much slower without it."""
    every = sorted(str(p.relative_to(SITE)).replace("\\", "/")
                   for p in (SITE / "nvidia").glob("*/bin/*.dll"))  # fmt: skip
    ok, base = hears_with(every)
    print(f"  with all {len(every)} DLLs: {base}")
    if not ok:
        sys.exit("She can't hear on the GPU even with every DLL: fix that first")
    base_s = float(base.split("s", 1)[0])
    keep = list(every)
    for rel in every:
        trial = [r for r in keep if r != rel]
        ok, out = hears_with(trial)
        slow = ok and float(out.split("s", 1)[0]) > base_s * 1.5 + 1
        verdict = "NEEDED" if not ok else ("NEEDED (slower)" if slow else "not needed")
        print(f"  without {Path(rel).name:42} -> {verdict}: {out}")
        if ok and not slow:
            keep = trial
    ok, out = hears_with(keep)
    print(f"  only the kept ones: {out}")
    size = sum((SITE / r).stat().st_size for r in keep) / 1e6
    print(f"CUDA_KEEP should be ({size:.0f} MB):\n" + "\n".join(f'    "{r}",' for r in keep))


def make_zip() -> Path:
    path = DIST / f"Coco-{__version__}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in APP.rglob("*"):
            z.write(f, Path("Coco") / f.relative_to(APP))
    print(f"{path}: {path.stat().st_size / 1e6:.0f} MB")
    return path


def iscc() -> Path | None:
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("PROGRAMFILES(X86)", ""),
                 os.environ.get("PROGRAMFILES", "")):  # fmt: skip
        for sub in (r"Programs\Inno Setup 6", "Inno Setup 6"):
            exe = Path(base) / sub / "ISCC.exe"
            if base and exe.exists():
                return exe
    found = shutil.which("iscc")
    return Path(found) if found else None


def make_installer() -> Path:
    exe = iscc()
    if exe is None:
        sys.exit("Inno Setup 6 isn't installed (ISCC.exe not found)")
    t = time.monotonic()
    subprocess.run(  # noqa: S603 - our own installer script
        [str(exe), f"/DAppVersion={__version__}", f"/DSourceDir={APP}", f"/DOutDir={DIST}",
         f"/DIconFile={make_icon()}", str(ROOT / "packaging" / "coco.iss")],
        check=True,
    )  # fmt: skip
    path = DIST / f"Coco-Setup-{__version__}.exe"
    print(f"{path}: {path.stat().st_size / 1e6:.0f} MB in {time.monotonic() - t:.0f} s")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--zip", action="store_true")
    ap.add_argument("--installer", action="store_true")
    ap.add_argument("--measure-cuda", action="store_true")
    ap.add_argument("--no-build", action="store_true", help="only test / package dist\\Coco")
    ap.add_argument("--all-cuda", action="store_true", help="bundle every CUDA DLL")
    ap.add_argument(
        "--untested",
        action="store_true",
        help="package even if Windows won't let the selftest run (Smart App Control)",
    )
    args = ap.parse_args()
    if args.measure_cuda:
        measure_cuda()
        return 0
    if not args.no_build:
        cuda = CUDA_KEEP
        if args.all_cuda:
            cuda = sorted(str(p.relative_to(SITE)).replace("\\", "/")
                          for p in (SITE / "nvidia").glob("*/bin/*.dll"))  # fmt: skip
        build(cuda)
    ok = selftest()
    print(f"dist\\Coco: {folder_size(APP):.0f} MB")
    if ok is False or (ok is None and not args.untested):
        print("Selftest " + ("FAILED" if ok is False else "couldn't run (add --untested)")
              + ": not packaging this build")  # fmt: skip
        return 1
    if args.zip:
        make_zip()
    if args.installer:
        make_installer()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
