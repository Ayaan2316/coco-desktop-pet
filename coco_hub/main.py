"""Dashboard server on 127.0.0.1:8765."""

from __future__ import annotations

import logging
import os
import sys

from coco.core.paths import ASSETS_DIR, user_data_dir

HOST = "127.0.0.1"  # never 0.0.0.0: only this PC (and Tailscale, via serve)
PORT = int(os.environ.get("COCO_HUB_PORT", "8765"))

# NiceGUI keeps per-browser sessions on disk: keep them with Coco's data, not in the
# project folder (must be set before nicegui is imported).
os.environ.setdefault("NICEGUI_STORAGE_PATH", str(user_data_dir() / "hub" / "sessions"))

from nicegui import app, background_tasks, ui  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402

from coco_hub import state  # noqa: E402
from coco_hub.pages import (  # noqa: E402, F401 - routes
    about,
    chat,
    home,
    lists,
    login,
    memories,
    screenshots,
    settings,
)

log = logging.getLogger("coco_hub")


async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


def main() -> int:
    from coco.core.package_guard import hand_off, redirected

    if redirected() and not os.environ.get("COCO_ALLOW_PACKAGED") and hand_off("hub"):
        return 0  # started from inside another app's package: Task Scheduler starts it
    log_file = user_data_dir() / "logs" / "hub.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        # pythonw (no console): the web server writes to stdout/stderr, which don't exist
        # there and would crash it. Give it a sink (its log lines go to hub.log below).
        out = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - lives as long as the hub
        sys.stdout = sys.stdout or out
        sys.stderr = sys.stderr or out
    # Always into hub.log (rotating): when Coco or the keeper starts the hub its output goes
    # nowhere, and errors must still be findable.
    from logging.handlers import RotatingFileHandler

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    file_handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=2,
                                       encoding="utf-8")  # fmt: skip
    file_handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    if sys.stderr is not None and sys.stderr.isatty():
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(fmt)
        root.addHandler(console)
    app.add_middleware(BaseHTTPMiddleware, dispatch=security_headers)
    app.add_static_files("/sprites", str(state.SPRITES))
    app.on_startup(lambda: background_tasks.create(state.poll_forever(), name="coco-status"))
    state.store.prune()
    icon = ASSETS_DIR / "icons" / "tray.png"
    log.info("Coco's dashboard on http://%s:%d", HOST, PORT)
    ui.run(
        host=HOST,
        port=PORT,
        title="Coco",
        favicon=icon if icon.exists() else "🐱",
        dark=True,
        storage_secret=state.auth.cookie_secret,
        session_middleware_kwargs={"max_age": 30 * 86400, "same_site": "strict"},
        reload=False,
        show=False,
        show_welcome_message=False,
        reconnect_timeout=10.0,
        binding_refresh_interval=None,
    )
    return 0


if __name__ in {"__main__", "__mp_main__"}:
    raise SystemExit(main())
