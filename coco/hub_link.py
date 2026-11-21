"""Coco's side of the dashboard link, and the chat recorder."""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal

from coco.core.paths import user_data_dir

log = logging.getLogger(__name__)

MAX_BODY = 64 * 1024


def link_file() -> Path:
    return user_data_dir() / "hub" / "link.json"


class MainThread(QObject):
    """Runs a function on the Qt main thread and waits for its result (from any thread)."""

    _call = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def _run(self, job) -> None:
        fn, box, done = job
        try:
            box["value"] = fn()
        except Exception as exc:  # noqa: BLE001 - handed back to the caller's thread
            box["error"] = exc
        done.set()

    def run(self, fn: Callable[[], Any], timeout: float = 5.0) -> Any:
        box: dict[str, Any] = {}
        done = threading.Event()
        self._call.emit((fn, box, done))
        if not done.wait(timeout):
            raise TimeoutError("Coco is busy")
        if "error" in box:
            raise box["error"]
        return box.get("value")


class LinkServer:
    """localhost-only JSON server. `handlers` run on the server's thread: wrap them with
    MainThread.run when they touch Qt or app state."""

    def __init__(self, handlers: dict[str, Callable], token: str | None = None,
                 port: int = 0, path: Path | None = None) -> None:  # fmt: skip
        self.handlers = handlers
        self.token = token or secrets.token_urlsafe(32)
        self.path = path or link_file()
        self._server = ThreadingHTTPServer(("127.0.0.1", port), self._handler_class())
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="coco-hub-link"
        )
        self._thread.start()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"port": self.port, "token": self.token, "pid": os.getpid(),
                                   "started": time.time()}), encoding="utf-8")  # fmt: skip
        os.replace(tmp, self.path)
        log.info("Dashboard link listening on 127.0.0.1:%d", self.port)

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("pid") == os.getpid():
                self.path.unlink()
        except (OSError, ValueError):
            pass

    def _handler_class(self):
        link = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args) -> None:  # quiet: no request lines in the log
                return

            def _reply(self, code: int, data: dict) -> None:
                body = json.dumps(data).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _allowed(self) -> bool:
                host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
                if self.client_address[0] != "127.0.0.1" or host not in ("127.0.0.1", "localhost"):
                    self._reply(403, {"error": "local only"})
                    return False
                auth = self.headers.get("Authorization") or ""
                if not hmac.compare_digest(auth.encode(), f"Bearer {link.token}".encode()):
                    self._reply(401, {"error": "bad token"})
                    return False
                return True

            def _body(self) -> dict | None:
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = -1
                if n < 0 or n > MAX_BODY:
                    self._reply(413, {"error": "too big"})
                    return None
                try:
                    data = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    self._reply(400, {"error": "bad json"})
                    return None
                if not isinstance(data, dict):
                    self._reply(400, {"error": "bad json"})
                    return None
                return data

            def _call(self, name: str, *args) -> None:
                handler = link.handlers.get(name)
                if handler is None:
                    self._reply(404, {"error": "unknown"})
                    return
                try:
                    self._reply(200, handler(*args) or {"ok": True})
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    log.debug("Dashboard link: %s: the dashboard stopped waiting", name)
                except TimeoutError:
                    self._reply(503, {"error": "busy"})
                except (KeyError, ValueError) as exc:
                    self._reply(400, {"error": str(exc)})
                except Exception:  # noqa: BLE001 - never crash the link thread
                    log.exception("Dashboard link: %s failed", name)
                    self._reply(500, {"error": "failed"})

            def do_GET(self) -> None:  # noqa: N802 - http.server's names
                if not self._allowed():
                    return
                if self.path == "/status":
                    self._call("status")
                else:
                    self._reply(404, {"error": "unknown"})

            def do_POST(self) -> None:  # noqa: N802
                if not self._allowed():
                    return
                data = self._body()
                if data is None:
                    return
                if self.path == "/command":
                    self._call("command", data)
                elif self.path == "/chat":
                    text = str(data.get("text", "")).strip()
                    if not text or len(text) > 2000:
                        self._reply(400, {"error": "say something (up to 2000 letters)"})
                        return
                    self._call("chat", text)
                else:
                    self._reply(404, {"error": "unknown"})

        return Handler


class ChatRecorder:
    """Writes every exchange into coco.db's chat_log, for the dashboard's Chat page.

    asked()     a question (typed / voice / phone) opens an entry
    answered()  her reply text; tool results and command replies after it are added
    said()      a line on her own (late-night tease...) when no question is open
    An entry stays open for `window` seconds. `enabled()` False = history paused."""

    def __init__(
        self,
        store,
        enabled: Callable[[], bool] = lambda: True,
        clock: Callable[[], float] = time.monotonic,
        window: float = 120.0,
    ) -> None:
        self.store = store
        self.enabled = enabled
        self.clock = clock
        self.window = window
        self._open: int | None = None
        self._opened = 0.0
        self._answer = ""
        self.last_id: int | None = None

    def asked(self, source: str, text: str) -> int | None:
        self._open = None
        if not self.enabled():
            return None
        self._open = self.store.add_chat(source, text.strip())
        self._opened, self._answer = self.clock(), ""
        self.last_id = self._open
        return self._open

    def _is_open(self) -> bool:
        return self._open is not None and self.clock() - self._opened <= self.window

    def answered(self, text: str) -> None:
        text = (text or "").strip()
        if not text or not self.enabled():
            return
        if not self._is_open():
            self.said(text)
            return
        if text in self._answer:
            return  # the same line twice (bubble + voice)
        self._answer = f"{self._answer}\n{text}" if self._answer else text
        self.store.set_chat_answer(self._open, self._answer)

    def said(self, text: str) -> None:
        text = (text or "").strip()
        if not text or not self.enabled():
            return
        if self._is_open():
            self.answered(text)
            return
        self.last_id = self.store.add_chat("coco", "", text)
