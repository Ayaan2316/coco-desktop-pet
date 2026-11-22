r"""Create Start Menu and desktop shortcuts that run Coco from source.

.venv\Scripts\python tools_dev\make_shortcuts.py
.venv\Scripts\python tools_dev\make_shortcuts.py --no-desktop
.venv\Scripts\python tools_dev\make_shortcuts.py --remove
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

START_MENU = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def desktop() -> Path:
    from coco.desktop.windows import known_folder  # (OneDrive may move the desktop)

    return Path(known_folder("Desktop"))


def shortcuts(with_desktop: bool) -> dict[Path, tuple[list[str], str]]:
    from coco.core.launch import launch_command

    cat, hub = launch_command(frozen=False), launch_command(hub=True, frozen=False)
    out = {
        START_MENU / "Coco.lnk": (cat, "Coco, the desktop cat"),
        START_MENU / "Coco's dashboard.lnk": (hub, "Coco's dashboard (when it isn't running)"),
    }
    if with_desktop:
        out[desktop() / "Coco.lnk"] = (cat, "Coco, the desktop cat")
    return out


def make(path: Path, args: list[str], comment: str, icon: Path) -> None:
    import win32com.client

    shell = win32com.client.Dispatch("WScript.Shell")
    link = shell.CreateShortcut(str(path))
    link.TargetPath = args[0]
    link.Arguments = " ".join(f'"{a}"' for a in args[1:])
    link.WorkingDirectory = str(ROOT)
    link.IconLocation = f"{icon},0"
    link.Description = comment
    link.Save()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-desktop", action="store_true")
    ap.add_argument("--remove", action="store_true")
    args = ap.parse_args()
    icon = ROOT / "assets" / "icons" / "coco.ico"
    if not icon.exists():
        sys.path.insert(0, str(ROOT / "tools_dev"))
        from build_exe import make_icon

        icon = make_icon()
    for path, (cmd, comment) in shortcuts(not args.no_desktop).items():
        if args.remove:
            path.unlink(missing_ok=True)
            print(f"removed {path}")
        else:
            make(path, cmd, comment, icon)
            print(f"made {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
