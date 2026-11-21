"""Runs a do_it task: finds the window, drives the loop, reports back."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from coco.agent.guard import Guard
from coco.agent.loop import Agent, Outcome, live_act, ollama_json
from coco.agent.ui_tree import read_window, window_app

log = logging.getLogger(__name__)


class AgentHost:
    def __init__(
        self,
        backend,
        launcher,
        *,
        begin: Callable[[str], None],
        end: Callable[[], None],
        confirm: Callable[[str], bool],
        active_window: Callable[[], int | None],
        log_activity: Callable[[str, str, bool], None],
        excluded: Callable[[], list[str]],
        own_pid: int,
        owner: str = "you",
        max_steps: int = 15,
        max_seconds: float = 180.0,
    ) -> None:
        self.backend, self.launcher = backend, launcher
        self.begin, self.end, self.confirm = begin, end, confirm
        self.active_window, self.log_activity, self.excluded = active_window, log_activity, excluded
        self.own_pid, self.owner = own_pid, owner
        self.max_steps, self.max_seconds = max_steps, max_seconds
        self._stop = threading.Event()
        self._busy = threading.Lock()

    @property
    def driving(self) -> bool:
        return self._busy.locked()

    def stop(self) -> None:
        if self.driving:
            log.info("do_it: stop requested")
        self._stop.set()

    # --- windows
    def _windows(self) -> list[tuple[int, str, str]]:
        """Visible top-level app windows: (hwnd, title, program), front first."""
        import win32gui
        import win32process

        out = []

        def visit(h, _):
            if not win32gui.IsWindowVisible(h) or not win32gui.GetWindowText(h):
                return
            _, pid = win32process.GetWindowThreadProcessId(h)
            if pid == self.own_pid:
                return
            out.append((h, win32gui.GetWindowText(h), window_app(h)))

        win32gui.EnumWindows(visit, None)
        return out

    def find_window(self, app: str) -> int | None:
        words = [w for w in app.lower().replace(".exe", "").split() if len(w) > 1]
        if not words:
            return None
        for h, title, exe in self._windows():
            hay = f"{title.lower()} {exe}"
            if all(w in hay for w in words) or app.lower().replace(" ", "") in exe:
                return self._frame(h)
        return None

    def _frame(self, hwnd: int) -> int:
        """A Store app's inner window -> its outer frame (the one Windows lets come to the
        front). Settings has both, with the same title."""
        import win32gui

        if win32gui.GetClassName(hwnd) != "Windows.UI.Core.CoreWindow":
            return hwnd
        title = win32gui.GetWindowText(hwnd)
        for h, t, _exe in self._windows():
            if t == title and win32gui.GetClassName(h) == "ApplicationFrameWindow":
                return h
        return hwnd

    def open_window(self, app: str, wait: float = 12.0) -> int | None:
        """The app's window: an open one, else start it and wait for its window."""
        h = self.find_window(app)
        if h:
            return h
        try:
            target = self.launcher.resolve(app)
            before = {w[0] for w in self._windows()}
            self.launcher.open(target)
        except Exception as exc:  # noqa: BLE001 - unknown app name
            log.info("do_it: can't open %r: %s", app, exc)
            return None
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            time.sleep(0.5)
            h = self.find_window(app) or next(
                (w[0] for w in self._windows() if w[0] not in before), None
            )
            if h:
                time.sleep(1.0)  # let it finish drawing
                return h
        return None

    def _follow(self, hwnd: int, app_exe: str, app_title: str = "") -> int:
        """The window to look at now: the same app's window in front (a dialog it opened, a
        new window), else the one we had, else another window of the same app."""
        import win32gui

        # Store apps (Settings, Calculator...) all live in ApplicationFrameHost.exe: there
        # the window's title must match too, or she could wander into another app.
        shared = app_exe == "applicationframehost.exe"
        title = app_title or ""

        def same_app(h: int) -> bool:
            if not app_exe or window_app(h) != app_exe:
                return False
            return not shared or win32gui.GetWindowText(h) == title

        fg = win32gui.GetForegroundWindow()
        if fg and fg != hwnd and same_app(fg):
            return fg
        if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
            return hwnd
        again = next((h for h, _t, _e in self._windows() if same_app(h)), None)
        return again or hwnd

    # --- run
    def run(self, goal: str, app: str = "") -> Outcome:
        if not self._busy.acquire(blocking=False):
            return Outcome(False, "busy", "I'm already doing something: say stop first.")
        import uiautomation as auto

        self._stop.clear()
        try:
            if app:  # a terminal / an excluded app: refuse before even opening it
                from coco.agent.ui_tree import UiView

                refused = Guard(self.excluded()).check_app(UiView(app, app.lower(), []))
                if refused:
                    return Outcome(False, "refused", refused)
            hwnd = self.open_window(app) if app else self.active_window()
            hwnd = self._frame(hwnd) if hwnd else hwnd
            if not hwnd:
                where = f"the {app} window" if app else "which window you mean"
                return Outcome(False, "error", f"I couldn't find {where}. Meow?")
            self.begin(goal)
            self.log_activity("do_it", f"start: {goal}" + (f" (in {app})" if app else ""), True)
            import win32gui

            app_exe, app_title = window_app(hwnd), win32gui.GetWindowText(hwnd)

            def read(h: int):
                h = self._frame(self._follow(h, app_exe, app_title))
                view = read_window(h)
                if view.total <= 2:  # a Store app asleep in the background shows nothing
                    from coco.desktop import windows as win

                    win.force_foreground(h)
                    time.sleep(1.0)
                    view = read_window(h)
                return view

            agent = Agent(
                ollama_json(self.backend),
                read,
                live_act,
                Guard(self.excluded()),
                confirm=self.confirm,
                open_app=self.open_window,
                stopped=self._stop.is_set,
                log_step=lambda text, ok: self.log_activity("do_it", text, ok),
                owner=self.owner,
                max_steps=self.max_steps,
                max_seconds=self.max_seconds,
            )
            with auto.UIAutomationInitializerInThread():
                outcome = agent.run(goal, hwnd)
            log.info("do_it %r: %s in %.1f s, %d steps: %s", goal, outcome.kind,
                     outcome.seconds, len(outcome.steps), outcome.summary)  # fmt: skip
            return outcome
        finally:
            try:
                self.end()
            finally:
                self._busy.release()
