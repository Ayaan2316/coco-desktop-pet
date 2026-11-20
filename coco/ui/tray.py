"""Tray icon and menu."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QAction, QActionGroup, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from coco.core.bus import EventBus
from coco.core.events import Event, Topic


class Tray:
    def __init__(self, bus: EventBus, icon_path: Path, sizes: list[str], cat_name: str) -> None:
        self.bus = bus
        self.icon = QSystemTrayIcon(QIcon(str(icon_path)))
        self.icon.setToolTip(f"{cat_name} the desktop cat")
        menu = QMenu()
        self.menu = menu

        self.show_action = self._add(menu, "Hide Coco", "toggle_visible")
        menu.addSeparator()

        size_menu = menu.addMenu("Size")
        group = QActionGroup(size_menu)
        self.size_actions: dict[str, QAction] = {}
        for s in sizes:
            a = QAction(s, size_menu, checkable=True)
            a.triggered.connect(lambda _=False, s=s: self._send("set_size", size=s))
            group.addAction(a)
            size_menu.addAction(a)
            self.size_actions[s] = a

        self.pause_action = self._add(menu, "Pause wandering", "toggle_pause", checkable=True)
        self.autostart_action = self._add(
            menu, "Start with Windows", "toggle_autostart", checkable=True
        )
        self.voice_action = self._add(menu, "Voice (she speaks)", "toggle_voice", checkable=True)
        self.mic_action = self._add(
            menu, "Microphone ready (hears your first word)", "toggle_mic_ready", checkable=True
        )
        self.wake_action = self._add(menu, 'Wake word ("Hey Coco")', "toggle_wake", checkable=True)
        self.focus_action = self._add(menu, "Focus mode (25 min)", "toggle_focus", checkable=True)
        self.tools_menu = menu.addMenu("Tools (what she may do on your PC)")
        self.tools_menu.menuAction().setVisible(False)
        self.tool_actions: dict[str, QAction] = {}
        menu.addSeparator()
        self.overlay_action = self._add(
            menu, "Debug overlay (Ctrl+Alt+D)", "toggle_overlay", checkable=True
        )
        # Testing aid: trigger any behaviour now instead of waiting for her to pick it.
        make = menu.addMenu("Make Coco...")
        for label, activity in (
            ("walk", "walk"), ("run", "run"), ("jump onto the active window", "jump_up"),
            ("peek from the screen edge", "peek"), ("stalk the cursor", "stalk"),
            ("go home and nap", "go_home"), ("hop out of the bed", "hop_out"),
            ("loaf", "loaf"), ("groom", "groom"), ("yawn", "yawn"), ("stretch", "stretch"),
        ):  # fmt: skip
            a = QAction(label, make)
            a.triggered.connect(lambda _=False, act=activity: self._send("force", activity=act))
            make.addAction(a)
        self._add(menu, "Reload config + art", "reload_config")
        self._add(menu, "Connect Spotify...", "connect_spotify")
        self._add(menu, "WhatsApp contacts...", "whatsapp_contacts")
        self._add(menu, "Open notes", "open_notes")
        self._add(menu, "Open screenshots folder", "open_screenshots")
        self._add(menu, "Open log folder", "open_logs")
        menu.addSeparator()
        self._add(menu, "Quit", "quit")

        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self._activated)
        self.icon.show()

    def _add(self, menu: QMenu, text: str, command: str, checkable: bool = False) -> QAction:
        action = QAction(text, menu, checkable=checkable)
        action.triggered.connect(lambda _=False: self._send(command))
        menu.addAction(action)
        return action

    def _send(self, name: str, **kwargs) -> None:
        self.bus.publish(Event(Topic.COMMAND, {"name": name, **kwargs}))

    def _activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._send("toggle_visible")

    def sync(
        self,
        *,
        visible: bool,
        size: str,
        paused: bool,
        autostart: bool,
        overlay: bool,
        voice: bool = False,
        mic_ready: bool | None = None,
        wake: bool | None = None,
        tools: list[tuple[str, str, bool]] | None = None,
        focus_left: float | None = None,
    ) -> None:
        """Make the checkmarks match the app's state."""
        self.show_action.setText("Hide Coco" if visible else "Show Coco")
        if size in self.size_actions:
            self.size_actions[size].setChecked(True)
        self.pause_action.setChecked(paused)
        self.autostart_action.setChecked(autostart)
        self.overlay_action.setChecked(overlay)
        self.voice_action.setChecked(voice)
        self.mic_action.setVisible(mic_ready is not None)  # None = listening is off
        self.mic_action.setChecked(bool(mic_ready))
        self.wake_action.setVisible(wake is not None)  # None = not trained / listening off
        self.wake_action.setChecked(bool(wake))
        self._sync_tools(tools or [])
        self.focus_action.setChecked(focus_left is not None)
        self.focus_action.setText(
            f"Focus mode ({int(focus_left // 60) + 1} min left)"
            if focus_left is not None
            else "Focus mode (25 min)"
        )

    def _sync_tools(self, tools: list[tuple[str, str, bool]]) -> None:
        """Tray > Tools: one checkbox per allowed tool (name, label, on)."""
        names = [n for n, _l, _on in tools]
        if names != list(self.tool_actions):
            self.tools_menu.clear()
            self.tool_actions = {}
            for name, label, _on in tools:
                a = QAction(label, self.tools_menu, checkable=True)
                a.triggered.connect(lambda _=False, n=name: self._send("toggle_tool", tool=n))
                self.tools_menu.addAction(a)
                self.tool_actions[name] = a
        for name, _label, on in tools:
            self.tool_actions[name].setChecked(on)
        self.tools_menu.menuAction().setVisible(bool(tools))
