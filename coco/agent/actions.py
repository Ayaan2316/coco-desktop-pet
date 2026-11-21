"""Carry out one action (click, type, keys, scroll) on a live window."""

from __future__ import annotations

import contextlib
import logging
import time

from coco.agent.ui_tree import UiItem

log = logging.getLogger(__name__)


class ActionError(RuntimeError):
    """The action couldn't be done (element gone, app refused...)."""


def _front(hwnd: int | None) -> None:
    """Bring the app to the front, and make sure it is: keys and typing go to whatever window
    is in front, so if it isn't hers, she stops rather than type into the wrong app."""
    import win32gui

    from coco.desktop import windows as win

    if not hwnd:
        raise ActionError("I don't know which window to type into")
    for _ in range(3):
        win.force_foreground(hwnd)
        time.sleep(0.15)
        if _root(win32gui.GetForegroundWindow()) == _root(hwnd):
            return
    raise ActionError("I couldn't bring the app to the front, so I didn't type anything")


def _root(hwnd: int) -> int:
    import ctypes

    return ctypes.windll.user32.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT


def click(item: UiItem) -> str:
    c = item.control
    if c is None:
        raise ActionError("that element is gone")
    for name, do in (
        ("invoke", lambda: c.GetInvokePattern().Invoke()),
        ("toggle", lambda: c.GetTogglePattern().Toggle()),
        ("select", lambda: c.GetSelectionItemPattern().Select()),
        ("expand", lambda: _expand_or_collapse(c)),
    ):
        if name in item.patterns:
            try:
                do()
                return f'{name}d "{item.name}"' if name != "expand" else f'opened "{item.name}"'
            except Exception as exc:  # noqa: BLE001 - try the next way
                log.debug("%s failed on %s: %s", name, item.name, exc)
    try:
        c.Click(simulateMove=False)
        return f'clicked "{item.name}"'
    except Exception as exc:  # noqa: BLE001
        raise ActionError(f'couldn\'t click "{item.name}"') from exc


def _expand_or_collapse(c) -> None:
    p = c.GetExpandCollapsePattern()
    if p.ExpandCollapseState == 1:  # expanded
        p.Collapse()
    else:
        p.Expand()


def type_into(item: UiItem | None, text: str, hwnd: int | None) -> str:
    from coco.desktop import windows as win

    if item is not None and item.control is not None:
        c = item.control
        if item.kind == "Edit" and "value" in item.patterns:
            with contextlib.suppress(Exception):
                c.GetValuePattern().SetValue(text)  # replaces a box's text (search, name)
                return f'typed "{text[:40]}" into "{item.name}"'
        _front(hwnd)
        with contextlib.suppress(Exception):
            c.SetFocus()
            time.sleep(0.1)
    _front(hwnd)  # (checked again right before typing: never into another app)
    # One character at a time; some apps drop characters sent in a single burst.
    for i, ch in enumerate(text):
        if i % 20 == 0 and i:
            _front(hwnd)  # (still hers? stop rather than type into another app)
        win.type_text(ch)
        time.sleep(0.02)
    return f'typed "{text[:40]}"' + (f' into "{item.name}"' if item else "")


def press(keys: str, hwnd: int | None) -> str:
    from coco.desktop import windows as win

    _front(hwnd)
    try:
        win.press_keys(keys)
    except ValueError as exc:
        raise ActionError(str(exc)) from exc
    return f"pressed {keys}"


def scroll(direction: str, hwnd: int | None, item: UiItem | None = None) -> str:
    import uiautomation as auto

    target = item.control if item is not None and item.control is not None else None
    if target is None and hwnd:
        target = auto.ControlFromHandle(hwnd)
    if target is None:
        raise ActionError("nothing to scroll")
    down = direction.lower().startswith("d")
    try:
        if down:
            target.WheelDown(wheelTimes=5, waitTime=0.05)
        else:
            target.WheelUp(wheelTimes=5, waitTime=0.05)
    except Exception as exc:  # noqa: BLE001
        raise ActionError("couldn't scroll") from exc
    return f"scrolled {'down' if down else 'up'}"
