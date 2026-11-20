r"""Run Coco through every behaviour, one after another.

.venv\Scripts\python tools_dev\demo_tour.py
.venv\Scripts\python tools_dev\demo_tour.py --target-window   # adds a window to jump on
.venv\Scripts\python tools_dev\demo_tour.py --snapshots out   # saves frames
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402

from coco.app import CocoApp  # noqa: E402
from coco.core.logging_setup import setup_logging  # noqa: E402

log = logging.getLogger("tour")

# (activity, seconds to watch it)
TOUR = [
    ("hop_out", 3), ("walk", 7), ("run", 5), ("stretch", 3), ("groom", 5), ("yawn", 3),
    ("jump_up", 8),       # onto the helper window (if --target-window)
    ("look_around", 6),   # the helper window moves at 40 s -> she falls off
    ("jump_up", 8),       # back up; the helper minimises at 48 s -> she falls again
    ("go_home", 16), ("hop_out", 4), ("peek", 28),
]  # fmt: skip

HELPER = r"""
import sys
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel
app = QApplication(sys.argv)
w = QLabel("  Coco test window: she can stand on my top edge.\n"
           "  I move at 40 s and minimise at 48 s.")
w.setWindowTitle("Coco test window")
w.setStyleSheet("background:#f4f1ea; color:#333; font-size:14px;")
w.setGeometry(380, 420, 620, 260)
w.show()
w.raise_()
w.activateWindow()
QTimer.singleShot(40000, lambda: w.move(w.x() + 90, w.y() + 60))
QTimer.singleShot(48000, w.showMinimized)
QTimer.singleShot(60000, app.quit)
app.exec()
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-window", action="store_true")
    parser.add_argument("--snapshots", type=Path)
    parser.add_argument("--only", help="comma-separated activities instead of the full tour")
    args = parser.parse_args()
    setup_logging(debug=True)

    app = CocoApp(argparse.Namespace(debug=True, cat=None, config=None))
    app.start()
    helper = None
    if args.target_window:
        helper = subprocess.Popen([sys.executable, "-c", HELPER])

    tour = [(a, 6) for a in args.only.split(",")] if args.only else list(TOUR)
    step = {"i": 0}

    def next_step() -> None:
        if step["i"] >= len(tour):
            log.info("tour finished")
            QTimer.singleShot(1500, app.quit)
            return
        activity, seconds = tour[step["i"]]
        step["i"] += 1
        log.info("TOUR -> %s (state before: %s)", activity, app.engine.state.describe())
        app.engine.force(activity)
        app._kick()
        QTimer.singleShot(int(seconds * 1000), next_step)

    if args.snapshots:
        args.snapshots.mkdir(parents=True, exist_ok=True)
        counter = {"n": 0}

        def snap() -> None:
            win = app.cat_window
            if not win.isVisible():
                return
            g = win.geometry().adjusted(-60, -60, 60, 20)
            pix = win.screen().grabWindow(0, g.x(), g.y(), g.width(), g.height())
            name = f"{counter['n']:04d}_{app.engine.state.name}.png"
            pix.save(str(args.snapshots / name))
            counter["n"] += 1

        snap_timer = QTimer()
        snap_timer.timeout.connect(snap)
        snap_timer.start(400)

    QTimer.singleShot(2500 if args.target_window else 1500, next_step)
    code = app.qapp.exec()
    if helper is not None and helper.poll() is None:
        helper.terminate()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
