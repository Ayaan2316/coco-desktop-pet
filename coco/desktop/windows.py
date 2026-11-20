"""All Win32 calls (pywin32, ctypes, winreg)."""

from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import winreg
from ctypes import wintypes

import win32api
import win32con
import win32gui
import win32process

from coco.core.geometry import Point, Rect
from coco.desktop.base import MonitorInfo, TaskbarInfo, WindowInfo

log = logging.getLogger(__name__)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_dwmapi = ctypes.WinDLL("dwmapi")
_shell32 = ctypes.WinDLL("shell32")
_shcore = ctypes.WinDLL("shcore")
_kernel32 = ctypes.WinDLL("kernel32")
_ole32 = ctypes.WinDLL("ole32")

# DwmGetWindowAttribute attributes
DWMWA_EXTENDED_FRAME_BOUNDS = 9  # visible frame, without the invisible 7 px resize borders
DWMWA_CLOAKED = 14  # "cloaked" = exists but invisible (other virtual desktop, suspended UWP)

# SHQueryUserNotificationState results that mean "fullscreen, please hide"
QUNS_BUSY = 2  # a fullscreen app (most games, F11 browsers, videos)
QUNS_RUNNING_D3D_FULL_SCREEN = 3  # exclusive-fullscreen Direct3D game
QUNS_PRESENTATION_MODE = 4  # PowerPoint slideshow / presentation settings

ABM_GETSTATE = 4
ABM_GETTASKBARPOS = 5
ABS_AUTOHIDE = 1

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

# Shell / system windows that look like app windows but must never be walked on.
_IGNORED_CLASSES = {
    "Progman",
    "WorkerW",
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "NotifyIconOverflowWindow",
    "Windows.UI.Core.CoreWindow",
    "TopLevelWindowForOverflowXamlIsland",
    "XamlExplorerHostIslandWindow",
    "ForegroundStaging",
    "MultitaskingViewFrame",
    "TaskListThumbnailWnd",
}

# Extended styles of windows that aren't real app windows for Coco's purposes:
#   TOOLWINDOW  - palettes, tooltips, our own windows
#   TRANSPARENT - click-through overlays (e.g. an invisible full-screen topmost overlay);
#                 treating those as windows would hide every other window edge behind them
#   NOACTIVATE  - overlays/notifications that can never be the active window
_NOT_A_REAL_WINDOW = (
    win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_NOACTIVATE
)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class _APPBARDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uCallbackMessage", wintypes.UINT),
        ("uEdge", wintypes.UINT),
        ("rc", wintypes.RECT),
        ("lParam", wintypes.LPARAM),
    ]


_shell32.SHAppBarMessage.restype = ctypes.c_size_t
_shell32.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.POINTER(_APPBARDATA)]
_dwmapi.DwmGetWindowAttribute.argtypes = [
    wintypes.HWND,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
]


def _rect(r) -> Rect:
    return Rect(r[0], r[1], r[2], r[3])


def _is_cloaked(hwnd: int) -> bool:
    cloaked = wintypes.DWORD(0)
    res = _dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), 4)
    return res == 0 and cloaked.value != 0


def _frame_rect(hwnd: int) -> Rect:
    """The window rectangle you actually see (GetWindowRect includes invisible borders)."""
    r = wintypes.RECT()
    res = _dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)
    )
    if res == 0:
        return Rect(r.left, r.top, r.right, r.bottom)
    return _rect(win32gui.GetWindowRect(hwnd))


class WindowsDesktop:
    """DesktopAPI implementation for Windows 10/11."""

    # --- monitors
    def monitors(self) -> list[MonitorInfo]:
        result: list[MonitorInfo] = []
        try:
            for hmon, _hdc, _r in win32api.EnumDisplayMonitors():
                info = win32api.GetMonitorInfo(hmon)
                dpi_x, dpi_y = ctypes.c_uint(96), ctypes.c_uint(96)
                # MDT_EFFECTIVE_DPI (0): the DPI matching the user's scale setting
                _shcore.GetDpiForMonitor(int(hmon), 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
                result.append(
                    MonitorInfo(
                        device=info.get("Device", ""),
                        rect=_rect(info["Monitor"]),
                        work=_rect(info["Work"]),
                        scale=dpi_x.value / 96.0,
                        primary=bool(info.get("Flags", 0) & 1),
                    )
                )
        except win32api.error:
            log.exception("EnumDisplayMonitors failed")
        if not result:  # never return nothing: fall back to the primary screen metrics
            w = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
            h = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
            result.append(MonitorInfo("", Rect(0, 0, w, h), Rect(0, 0, w, h), 1.0, True))
        return result

    # --- windows
    def _describe(self, hwnd: int, exclude_pids: frozenset[int]) -> WindowInfo | None:
        try:
            if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
                return None
            ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            if ex & _NOT_A_REAL_WINDOW:
                return None
            if win32gui.GetWindowTextLength(hwnd) == 0:
                return None
            cls = win32gui.GetClassName(hwnd)
            if cls in _IGNORED_CLASSES:
                return None
            if exclude_pids:
                _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid in exclude_pids:
                    return None
            if _is_cloaked(hwnd):
                return None
            rect = _frame_rect(hwnd)
            if rect.width < 40 or rect.height < 20:
                return None
            return WindowInfo(hwnd, rect, win32gui.GetWindowText(hwnd), cls)
        except win32gui.error:  # window vanished while we looked at it
            return None

    def top_level_windows(self, exclude_pids: frozenset[int] = frozenset()) -> list[WindowInfo]:
        hwnds: list[int] = []
        try:
            win32gui.EnumWindows(lambda h, acc: acc.append(h) or True, hwnds)  # z-order, top first
        except win32gui.error:
            log.debug("EnumWindows interrupted", exc_info=True)
        result = []
        for hwnd in hwnds:
            info = self._describe(hwnd, exclude_pids)
            if info is not None:
                result.append(info)
        return result

    def window_info(self, hwnd: int) -> WindowInfo | None:
        if not win32gui.IsWindow(hwnd):
            return None
        return self._describe(hwnd, frozenset())

    def foreground_window(self) -> int | None:
        hwnd = win32gui.GetForegroundWindow()
        return hwnd or None

    # --- input
    def cursor_pos(self) -> Point:
        try:
            x, y = win32api.GetCursorPos()
            return Point(x, y)
        except win32api.error:  # e.g. on the secure desktop (Ctrl+Alt+Del, UAC prompt)
            return Point(-10_000, -10_000)

    def user_idle_seconds(self) -> float:
        info = _LASTINPUTINFO(ctypes.sizeof(_LASTINPUTINFO), 0)
        if not _user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        now = _kernel32.GetTickCount() & 0xFFFFFFFF
        return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0

    # --- polite mode
    def fullscreen_app_active(self) -> bool:
        """Is a game / video / presentation fullscreen right now?

        1. SHQueryUserNotificationState reliably reports exclusive-fullscreen Direct3D games
           and presentation mode. Its generic QUNS_BUSY is not trusted: an invisible,
           click-through, topmost full-screen overlay (some apps keep one open) makes
           Windows report BUSY all the time, which would hide Coco forever.
        2. Otherwise: the FOREGROUND window is borderless, not maximised, and covers its
           whole monitor (F11 browsers, YouTube fullscreen, borderless games, VLC...).
           A maximised window doesn't count, even when the taskbar auto-hides.
        """
        state = ctypes.c_int(0)
        if _shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0 and state.value in (
            QUNS_RUNNING_D3D_FULL_SCREEN,
            QUNS_PRESENTATION_MODE,
        ):
            return True
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return False
        try:
            if win32gui.GetClassName(hwnd) in _IGNORED_CLASSES:
                return False
            if win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & _NOT_A_REAL_WINDOW:
                return False
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            if (style & win32con.WS_CAPTION) == win32con.WS_CAPTION or _user32.IsZoomed(hwnd):
                return False  # a normal or maximised window with a title bar
            _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid == os.getpid():
                return False
            rect = _rect(win32gui.GetWindowRect(hwnd))
            hmon = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
            mon = _rect(win32api.GetMonitorInfo(hmon)["Monitor"])
        except win32api.error:
            return False
        return (
            rect.left <= mon.left
            and rect.top <= mon.top
            and rect.right >= mon.right
            and rect.bottom >= mon.bottom
        )

    def taskbar(self) -> TaskbarInfo | None:
        data = _APPBARDATA()
        data.cbSize = ctypes.sizeof(_APPBARDATA)
        if not _shell32.SHAppBarMessage(ABM_GETTASKBARPOS, ctypes.byref(data)):
            return None
        edge = {0: "left", 1: "top", 2: "right", 3: "bottom"}.get(data.uEdge, "bottom")
        state = _shell32.SHAppBarMessage(ABM_GETSTATE, ctypes.byref(data))
        rc = data.rc
        return TaskbarInfo(
            edge, Rect(rc.left, rc.top, rc.right, rc.bottom), autohide=bool(state & ABS_AUTOHIDE)
        )


# Helpers for Coco's own windows

_SWP_FLAGS = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE


def make_unfocusable(hwnd: int) -> None:
    """WS_EX_NOACTIVATE: clicking her never steals focus from the app you're typing in.
    WS_EX_TOOLWINDOW: no taskbar button and not in Alt+Tab."""
    ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    ex |= win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOOLWINDOW
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex)


def set_click_through(hwnd: int, enabled: bool) -> None:
    """WS_EX_TRANSPARENT on a layered window: every click passes through to what's below."""
    ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    new = (ex | win32con.WS_EX_TRANSPARENT) if enabled else (ex & ~win32con.WS_EX_TRANSPARENT)
    if new != ex:
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, new)


def stack_topmost(hwnds_front_to_back: list[int]) -> None:
    """Keep our windows topmost, in this order (first = in front).

    SetWindowPos(h, after) puts h directly below `after`, so we chain them.
    """
    try:
        after = win32con.HWND_TOPMOST
        for hwnd in hwnds_front_to_back:
            win32gui.SetWindowPos(hwnd, after, 0, 0, 0, 0, _SWP_FLAGS)
            after = hwnd
    except win32gui.error:
        log.debug("SetWindowPos failed", exc_info=True)


def window_at(x: int, y: int) -> int:
    """Top-level window under a physical point (used by the click-through self-check)."""
    hwnd = win32gui.WindowFromPoint((int(x), int(y)))
    return win32gui.GetAncestor(hwnd, win32con.GA_ROOT) if hwnd else 0


# --- hotkeys

_VK_NAMES = {f"f{i}": win32con.VK_F1 + i - 1 for i in range(1, 13)}
_VK_NAMES.update(
    {"space": win32con.VK_SPACE, "esc": win32con.VK_ESCAPE, "pause": win32con.VK_PAUSE}
)


def parse_hotkey(spec: str) -> tuple[int, int]:
    """ "ctrl+alt+d" -> (modifiers, virtual-key code). Raises ValueError if malformed."""
    mods, vk = MOD_NOREPEAT, None
    for part in spec.lower().replace(" ", "").split("+"):
        if part in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part == "shift":
            mods |= MOD_SHIFT
        elif part == "win":
            mods |= MOD_WIN
        elif part in _VK_NAMES:
            vk = _VK_NAMES[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        else:
            raise ValueError(f"Unknown key {part!r} in hotkey {spec!r}")
    if vk is None:
        raise ValueError(f"Hotkey {spec!r} has no key")
    return mods, vk


def register_hotkey(hwnd: int, hotkey_id: int, spec: str) -> bool:
    try:
        mods, vk = parse_hotkey(spec)
    except ValueError:
        log.warning("Bad hotkey %r in config", spec)
        return False
    if not _user32.RegisterHotKey(wintypes.HWND(hwnd), hotkey_id, mods, vk):
        log.warning("Hotkey %s is already used by another program", spec)
        return False
    return True


def unregister_hotkey(hwnd: int, hotkey_id: int) -> None:
    _user32.UnregisterHotKey(wintypes.HWND(hwnd), hotkey_id)


def key_down(vk: int) -> bool:
    """Is this key held right now? (push-to-talk: polled only while recording)"""
    return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


def hotkey_id_from_message(message_ptr: int) -> int | None:
    """Given the MSG* Qt passes to nativeEvent(), return the hotkey id for WM_HOTKEY."""
    msg = wintypes.MSG.from_address(message_ptr)
    if msg.message == WM_HOTKEY:
        return int(msg.wParam)
    return None


# --- power + focus


class _SYSTEM_POWER_STATUS(ctypes.Structure):  # noqa: N801 (Win32 name)
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", wintypes.DWORD),
        ("BatteryFullLifeTime", wintypes.DWORD),
    ]


def on_battery() -> bool:
    """True when the laptop runs on battery (then Coco doesn't pre-load the AI model)."""
    status = _SYSTEM_POWER_STATUS()
    if not _kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return False
    return status.ACLineStatus == 0  # 0 = battery, 1 = plugged in, 255 = unknown


def force_foreground(hwnd: int) -> bool:
    """Give `hwnd` keyboard focus.

    Tries SetForegroundWindow first. If that fails, briefly attaches to the foreground
    thread's input queue, but never to a window that isn't responding.
    """
    try:
        fg = win32gui.GetForegroundWindow()
        if fg == hwnd:
            return True
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        except win32gui.error:
            pass
        fg = win32gui.GetForegroundWindow()
        if fg == hwnd:
            return True
        if not fg or _user32.IsHungAppWindow(fg):
            return False
        fg_thread, _ = win32process.GetWindowThreadProcessId(fg)
        our_thread = win32api.GetCurrentThreadId()
        attached = (
            bool(fg_thread)
            and fg_thread != our_thread
            and bool(_user32.AttachThreadInput(fg_thread, our_thread, True))
        )
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        finally:
            if attached:
                _user32.AttachThreadInput(fg_thread, our_thread, False)
        return win32gui.GetForegroundWindow() == hwnd
    except win32gui.error:
        return False


# --- single instance

_instance_mutex = None


def acquire_single_instance(name: str) -> bool:
    """False if another Coco is already running (e.g. autostart + a manual launch)."""
    global _instance_mutex
    import win32event  # local import: only needed here
    import winerror

    _instance_mutex = win32event.CreateMutex(None, False, name)
    return win32api.GetLastError() != winerror.ERROR_ALREADY_EXISTS


# --- start with Windows


def autostart_enabled(name: str) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, name)
            return True
    except OSError:
        return False


def autostart_value(name: str) -> str | None:
    """The command "Start with Windows" runs for this entry, or None."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, name)
            return str(value)
    except OSError:
        return None


def set_autostart(name: str, command: str | None) -> None:
    """Add (command) or remove (None) Coco from HKCU\\...\\Run. No admin rights needed."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if command:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
        else:
            with contextlib.suppress(FileNotFoundError):
                winreg.DeleteValue(key, name)


# --- known folders
class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> _GUID:
    import uuid

    b = uuid.UUID(text).bytes_le
    g = _GUID()
    ctypes.memmove(ctypes.byref(g), b, 16)
    return g


_KNOWN_FOLDERS = {
    "Pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "Documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "Downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "Desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
}


def known_folder(name: str):
    """The user's real Pictures / Documents / Downloads / Desktop folder (works when
    Windows has moved it, e.g. into OneDrive). Falls back to ~/<name>."""
    from pathlib import Path

    path = ctypes.c_wchar_p()
    try:
        hr = _shell32.SHGetKnownFolderPath(
            ctypes.byref(_guid(_KNOWN_FOLDERS[name])), 0, None, ctypes.byref(path)
        )
        if hr == 0 and path.value:
            return Path(path.value)
    except (KeyError, OSError):
        log.debug("known folder %s lookup failed", name, exc_info=True)
    finally:
        if path:
            _ole32.CoTaskMemFree(path)
    return Path.home() / name


def _inside_onedrive(path) -> bool:
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        root = os.environ.get(var)
        if root:
            with contextlib.suppress(ValueError):
                path.resolve().relative_to(os.path.realpath(root))
                return True
    return False


def local_folder(name: str):
    """Like known_folder(), but never a cloud-synced one: if Windows moved Pictures /
    Documents into OneDrive, use the plain local ~/<name> instead (screenshots and notes
    must stay on this PC: they'd be uploaded otherwise)."""
    from pathlib import Path

    path = known_folder(name)
    if _inside_onedrive(path):
        log.info(
            "%s is synced by OneDrive (%s): using the local %s instead",
            name,
            path,
            Path.home() / name,
        )
        return Path.home() / name
    return path


# --- media keys
KEYEVENTF_KEYUP = 0x0002


def press_key(vk: int) -> None:
    """Tap a key (used for the media keys: play/pause, next, volume...)."""
    _user32.keybd_event(vk, 0, 0, 0)
    _user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


# --- apps & files
def start_apps() -> dict[str, str]:
    """Every app in the Start menu, Store apps included (WhatsApp, Spotify...):
    {name: AppUserModelID}. Open one with  shell:AppsFolder\\<id>."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()  # (safe to call again; needed on worker threads)
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        folder = shell.NameSpace("shell:::{4234d49b-0245-4df3-b780-3893943456e1}")
        apps = {}
        for item in folder.Items():
            name, app_id = str(item.Name).strip(), str(item.Path).strip()
            if name and app_id and "uninstall" not in name.lower():
                apps.setdefault(name, app_id)
        return apps
    finally:
        pythoncom.CoUninitialize()


def show_in_folder(path) -> None:
    """Open Explorer with the file selected (never runs the file)."""
    import subprocess

    subprocess.Popen(["explorer.exe", f"/select,{path}"])  # noqa: S603,S607 - fixed program


# --- typing & windows
INPUT_KEYBOARD = 1
KEYEVENTF_UNICODE = 0x0004


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("padding", ctypes.c_ubyte * 32)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def type_text(text: str) -> None:
    """Type any text (Hindi, emoji...) into the focused field, like a keyboard would."""
    events = []
    for ch in text:
        for unit in _utf16_units(ch):
            for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                inp = _INPUT(type=INPUT_KEYBOARD)
                inp.u.ki = _KEYBDINPUT(0, unit, flags, 0, None)
                events.append(inp)
    if events:
        arr = (_INPUT * len(events))(*events)
        _user32.SendInput(len(events), arr, ctypes.sizeof(_INPUT))


def _utf16_units(ch: str) -> list[int]:
    data = ch.encode("utf-16-le")
    return [int.from_bytes(data[i : i + 2], "little") for i in range(0, len(data), 2)]


def press_keys(spec: str) -> None:
    """ "ctrl+f", "enter", "alt+tab"... (a fixed set of key names)."""
    names = {
        "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B,
        "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
        "space": 0x20, "backspace": 0x08, "delete": 0x2E, "up": 0x26, "down": 0x28,
        "left": 0x25, "right": 0x27, "home": 0x24, "end": 0x23, "pageup": 0x21,
        "pagedown": 0x22,
    }  # fmt: skip
    vks = []
    for part in spec.lower().replace(" ", "").split("+"):
        if part in names:
            vks.append(names[part])
        elif len(part) == 1 and part.isalnum():
            vks.append(ord(part.upper()))
        elif part.startswith("f") and part[1:].isdigit():
            vks.append(0x6F + int(part[1:]))
        else:
            raise ValueError(f"unknown key {part!r}")
    for vk in vks:
        _user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vks):
        _user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def find_app_window(exe_names: tuple[str, ...], title_hint: str = "") -> int | None:
    """The main visible window of a running program (e.g. WhatsApp.Root.exe), or None."""
    import psutil

    wanted = {n.lower() for n in exe_names}
    found: list[int] = []

    def visit(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd) or not win32gui.GetWindowText(hwnd):
            return True
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            name = psutil.Process(pid).name().lower()
        except Exception:  # noqa: BLE001
            return True
        title = win32gui.GetWindowText(hwnd)
        if name in wanted or (title_hint and title_hint.lower() in title.lower()
                              and name == "applicationframehost.exe"):  # fmt: skip
            found.append(hwnd)
        return True

    win32gui.EnumWindows(visit, None)
    return found[0] if found else None
