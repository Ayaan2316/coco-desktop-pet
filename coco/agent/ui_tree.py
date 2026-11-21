"""Reads a window's accessibility tree into a short numbered list."""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field

ACTIONABLE = {
    "Button", "SplitButton", "MenuItem", "Edit", "Document", "ListItem", "TreeItem",
    "TabItem", "CheckBox", "RadioButton", "ComboBox", "Hyperlink", "Slider", "Spinner",
    "DataItem", "MenuBar",
}  # fmt: skip
SHORT = {"SplitButton": "button", "MenuItem": "menu", "ListItem": "item", "TreeItem": "item",
         "TabItem": "tab", "CheckBox": "checkbox", "RadioButton": "option",
         "ComboBox": "dropdown", "Hyperlink": "link", "DataItem": "item"}  # fmt: skip
PATTERNS = ("invoke", "value", "toggle", "select", "expand", "scroll")


class UiReadError(RuntimeError):
    """The window couldn't be read (closed, or accessibility refused)."""


def _glyph(text: str) -> bool:
    """Icon-font characters (Segoe Fluent's private use area) are not words."""
    return bool(text) and all(0xE000 <= ord(c) <= 0xF8FF or c.isspace() for c in text)


@dataclass
class UiItem:
    n: int
    kind: str  # uiautomation control type without "Control": Button, Edit...
    name: str
    value: str = ""
    patterns: tuple[str, ...] = ()
    focused: bool = False
    password: bool = False
    checked: bool | None = None
    context: str = ""  # the nearest named group / pane / list above it
    where: str = ""  # shown only when several items share a name
    control: object = field(default=None, repr=False, compare=False)

    def line(self) -> str:
        kind = SHORT.get(self.kind, self.kind.lower())
        out = f'#{self.n} {kind} "{self.name[:70]}"'
        if self.where:
            out += f' (in "{self.where[:40]}")'
        if self.value and self.value != self.name:
            out += f' = "{self.value[:60]}"'
        if self.checked is not None:
            out += " [on]" if self.checked else " [off]"
        if self.focused:
            out += " [focused]"
        if self.password:
            out += " [password]"
        return out


@dataclass
class UiView:
    title: str
    app: str  # program file name, lower case: "notepad.exe"
    items: list[UiItem]
    info: list[str] = field(default_factory=list)  # text you can read (results, labels)
    seconds: float = 0.0
    total: int = 0  # elements walked
    hwnd: int = 0  # the window it was read from

    def item(self, n: int) -> UiItem | None:
        return next((i for i in self.items if i.n == n), None)

    def focused(self) -> UiItem | None:
        return next((i for i in self.items if i.focused), None)

    def text(self, max_lines: int = 150, max_chars: int = 6000) -> str:
        lines = [f'Window: "{self.title}" ({self.app})']
        if self.info:  # first: results and states (a calculator's display, "Saved"...)
            lines.append("You can read: " + " · ".join(f'"{t[:60]}"' for t in self.info[:25]))
        size = sum(len(x) for x in lines)
        for shown, it in enumerate(self.items):
            line = it.line()
            if shown >= max_lines or size + len(line) > max_chars:
                lines.append(f"(+{len(self.items) - shown} more not shown: scroll to see them)")
                break
            lines.append(line)
            size += len(line) + 1
        if not self.items:
            lines.append("(nothing to act on: this app doesn't show its buttons to Windows)")
        return "\n".join(lines)


def build_view(title: str, app: str, nodes: list[dict], seconds: float = 0.0) -> UiView:
    """nodes: [{i, parent, type, name, value?, patterns, enabled, offscreen, focus, password,
    checked?, control?}] in tree order (from read_window, or a recorded test file)."""
    by_id = {n["i"]: n for n in nodes}
    items: list[UiItem] = []
    info: list[str] = []
    kept_names: dict[int, str] = {}  # node id -> name of the item kept for it
    for node in nodes:
        kind, name = node["type"], (node.get("name") or "").strip()
        if not node.get("enabled", True) or node.get("offscreen", False):
            continue
        if _glyph(name):
            name = ""
        value = (node.get("value") or "").strip()
        parent = by_id.get(node.get("parent", -1))
        # skip the inner copy of an item (a menu "File" holding a button "File")
        dup = False
        p = parent
        for _ in range(3):
            if p is None:
                break
            if kept_names.get(p["i"]) and kept_names[p["i"]] == name:
                dup = True
                break
            p = by_id.get(p.get("parent", -1))
        if dup:
            continue
        if kind in ACTIONABLE and (name or value or kind in ("Edit", "Document", "ComboBox")):
            if kind == "MenuBar" and name == "System":
                continue  # the window's system menu: never needed
            context = ""
            p = parent
            for _ in range(6):
                if p is None:
                    break
                if p["type"] in ("Group", "Pane", "List", "Tree", "Tab", "Menu", "ToolBar") and (
                    p.get("name") and not _glyph(p["name"])
                ):
                    context = p["name"]
                    break
                p = by_id.get(p.get("parent", -1))
            items.append(UiItem(
                n=len(items) + 1, kind=kind, name=name, value=value,
                patterns=tuple(node.get("patterns", ())), focused=bool(node.get("focus")),
                password=bool(node.get("password")), checked=node.get("checked"),
                context=context, control=node.get("control"),
            ))  # fmt: skip
            kept_names[node["i"]] = name
        elif kind == "Text" and name and len(name) <= 80 and name not in info:
            in_item = False  # a button's own label ("1" inside "One") isn't news
            p = parent
            for _ in range(3):
                if p is None:
                    break
                if p["i"] in kept_names:
                    in_item = True
                    break
                p = by_id.get(p.get("parent", -1))
            if not in_item:
                info.append(name)
    # the same name more than once ("Show more settings" x9): say where each one is
    counts: dict[str, int] = {}
    for it in items:
        counts[it.name] = counts.get(it.name, 0) + 1
    for it in items:
        if counts[it.name] > 1 and it.context:
            it.where = it.context
    return UiView(title, app, items, info, seconds, len(nodes))


def read_window(hwnd: int, budget: float = 4.0, max_nodes: int = 3000) -> UiView:
    """Walk a live window (call inside uiautomation.UIAutomationInitializerInThread)."""
    import uiautomation as auto
    import win32gui

    pids = {
        "invoke": auto.PatternId.InvokePattern, "value": auto.PatternId.ValuePattern,
        "toggle": auto.PatternId.TogglePattern, "select": auto.PatternId.SelectionItemPattern,
        "expand": auto.PatternId.ExpandCollapsePattern, "scroll": auto.PatternId.ScrollPattern,
    }  # fmt: skip
    t0 = time.perf_counter()
    root = None
    for _ in range(3):  # Windows' accessibility sometimes refuses for a moment
        try:
            root = auto.ControlFromHandle(hwnd)
            if root is not None:
                break
        except Exception:  # noqa: BLE001 - COMError: try again
            time.sleep(0.4)
    if root is None:
        raise UiReadError("couldn't read that window")
    nodes: list[dict] = []
    stack = [(root, -1)]
    while stack and len(nodes) < max_nodes and time.perf_counter() - t0 < budget:
        c, parent = stack.pop()
        try:
            kind = c.ControlTypeName.removesuffix("Control")
            node = {
                "i": len(nodes), "parent": parent, "type": kind, "name": c.Name or "",
                "enabled": bool(c.IsEnabled), "offscreen": bool(c.IsOffscreen),
                "focus": bool(c.HasKeyboardFocus), "password": bool(c.IsPassword),
                "control": c,
            }  # fmt: skip
            if kind in ACTIONABLE:
                pats = []
                for name, pid in pids.items():
                    try:
                        if c.GetPattern(pid) is not None:
                            pats.append(name)
                    except Exception:  # noqa: BLE001
                        pass
                node["patterns"] = pats
                if "value" in pats and not node["password"]:
                    with contextlib.suppress(Exception):
                        node["value"] = (c.GetValuePattern().Value or "")[:200]
                if "toggle" in pats:
                    with contextlib.suppress(Exception):
                        node["checked"] = c.GetTogglePattern().ToggleState == 1
            kids = c.GetChildren()
        except Exception:  # noqa: BLE001 - the element vanished mid-walk
            continue
        nodes.append(node)
        stack.extend((k, node["i"]) for k in reversed(kids))
    view = build_view(win32gui.GetWindowText(hwnd), window_app(hwnd), nodes,
                      time.perf_counter() - t0)  # fmt: skip
    view.hwnd = hwnd
    return view


def window_app(hwnd: int) -> str:
    """The program behind a window, lower case: "notepad.exe" ("" if unknown)."""
    try:
        import psutil
        import win32process

        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return psutil.Process(pid).name().lower()
    except Exception:  # noqa: BLE001 - gone / no access
        return ""
