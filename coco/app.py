"""Coco's entry point. Wires everything together and runs the Qt event loop."""

from __future__ import annotations

import argparse
import faulthandler
import logging
import os
import random
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import yaml
from PySide6.QtCore import QObject, QPointF, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

import coco.tools  # noqa: F401 - registers every tool
from coco.agent.guard import DEFAULT_EXCLUDED
from coco.agent.host import AgentHost
from coco.behaviour.commands import command_language, parse_command
from coco.behaviour.engine import BehaviourEngine, CatPose
from coco.behaviour.lines import LinePicker
from coco.behaviour.mood import Mood
from coco.behaviour.states import Sleep
from coco.behaviour.world import WorldSnapshot, place_bed
from coco.brain.backend import FakeBackend, OllamaBackend
from coco.brain.brain import Brain
from coco.brain.prompt import PromptContext
from coco.brain.schemas import BrainReply
from coco.core.anims import load_manifest
from coco.core.bus import EventBus
from coco.core.catdef import load_cat, personalize
from coco.core.config import CONFIG_FILE, UserSettings, load_config
from coco.core.events import Event, Topic
from coco.core.geometry import Rect
from coco.core.launch import (
    AUTOSTART_NAME,
    autostart_command,
    just_signed_in,
    migrate_autostart,
    other_coco_pids,
)
from coco.core.logging_setup import setup_logging
from coco.core.paths import ASSETS_DIR, log_dir, user_data_dir
from coco.core.secrets import Secrets
from coco.desktop import windows as win
from coco.desktop.sensors import DesktopSensor, MonitorFollower, monitor_containing
from coco.hub_link import ChatRecorder, LinkServer, MainThread
from coco.memory import MemoryBook, ollama_embedder
from coco.screen_time import ScreenTime, describe_minutes
from coco.tools.apps import DEFAULT_APPS, DEFAULT_SITES, Launcher
from coco.tools.clipboard import Clipboard
from coco.tools.files import FileFinder
from coco.tools.focus import FocusSession, clock_text
from coco.tools.intents import is_no, is_yes, parse_task
from coco.tools.lookup import WebLookup
from coco.tools.media import Media
from coco.tools.notes import NotesBook, notes_md_path
from coco.tools.power import Power
from coco.tools.registry import REGISTRY, ToolCall
from coco.tools.reminders import ReminderBook
from coco.tools.runner import ToolContext, ToolRunner
from coco.tools.screenshot import shots_dir
from coco.tools.spotify import Spotify, authorize_url, make_pkce, wait_for_code
from coco.tools.spotify_desktop import SpotifyDesktop
from coco.tools.store import Reminder, Store
from coco.tools.system import battery_only
from coco.tools.whatsapp import WhatsApp
from coco.ui.bed_window import BedLayer
from coco.ui.cat_window import CatWindow
from coco.ui.coords import CoordMapper
from coco.ui.debug_overlay import DebugOverlay
from coco.ui.driving_chip import DrivingChip
from coco.ui.focus_chip import FocusChip
from coco.ui.mood_bubble import MoodBubble
from coco.ui.screenshotter import Screenshotter
from coco.ui.speech_bubble import SpeechBubble
from coco.ui.sprites import SpriteLibrary
from coco.ui.tray import Tray
from coco.voice.music_words import MusicWords
from coco.voice.stt import Listener
from coco.voice.tts import Speaker
from coco.voice.wake import WakeDetector, WakeFlow

log = logging.getLogger("coco")
MOOD_STATS = ("happiness", "energy", "affection", "boredom", "bond")

HOTKEY_OVERLAY = 1
HOTKEY_TOGGLE = 2
HOTKEY_CHAT = 3
HOTKEY_TALK = 4
HOTKEY_SHOT = 5
HOTKEY_STOP = 6  # Esc, only while she's driving an app
# the little icon above her head after a task (assets/bubbles; tools_dev/make_tool_icons.py)
TOOL_ICONS = {
    "screenshot": "camera",
    "explain_screen": "camera",
    "notes": "note",
    "reminder": "note",
    "focus": "timer",
    "media_control": "music",
}


def load_bed_assets() -> tuple[Path, dict]:
    """Coco's home: assets/bed/ (the hammock). Falls back to the old assets/basket/ so an
    older checkout or custom art folder keeps working."""
    for folder, name in (("bed", "bed.yaml"), ("basket", "basket.yaml")):
        path = ASSETS_DIR / folder / name
        if path.exists():
            if folder != "bed":
                log.warning("assets/bed/bed.yaml not found, using %s", path)
            return path.parent, yaml.safe_load(path.read_text("utf-8"))
    raise FileNotFoundError("No bed art found in assets/bed/ (or assets/basket/)")


class _Waker(QObject):
    """Lets other threads (the AI worker) wake the Qt main loop: emitting a Qt signal from
    any thread is safe; the connected slot then runs on the main thread."""

    wake = Signal()


class CocoApp:
    def __init__(self, args: argparse.Namespace) -> None:
        self.config_path = Path(args.config) if args.config else CONFIG_FILE
        self.cfg = load_config(self.config_path)
        self.settings = UserSettings.load()
        if self.settings.owner_name:  # dashboard > About you
            self.cfg.owner_name = self.settings.owner_name
        self.cat = load_cat(args.cat or self.cfg.cat)
        self.manifest = load_manifest(self.cat.manifest_path)
        if self.manifest.missing():
            log.warning("Sprite manifest is missing animations: %s", self.manifest.missing())
        bed_dir, self.bed_meta = load_bed_assets()

        self.qapp = QApplication.instance() or QApplication(sys.argv)
        self.qapp.setQuitOnLastWindowClosed(False)  # closing windows mustn't quit: tray app
        self.qapp.setApplicationName("Coco")

        self.bus = EventBus()
        self.desktop = win.WindowsDesktop()
        self.sensor = DesktopSensor(
            self.desktop, self.cfg, self.bus, own_pids=frozenset({os.getpid()})
        )
        if self.settings.monitor:  # dashboard: which screen she lives on (or "follow")
            self.sensor.home = self.settings.monitor
            self.sensor.refresh_monitors()
        self.follower = MonitorFollower()
        self.mapper = CoordMapper(self.sensor.monitors)
        # Mood survives restarts: load it and catch up on the time Coco was closed.
        self.mood_path = user_data_dir() / "mood.json"
        mood = Mood.load(self.mood_path, time.time(), self.cfg.mood)
        self.engine = BehaviourEngine(self.cfg, self.cat, self.manifest, bus=self.bus, mood=mood)
        self.lines = LinePicker(self.cat.lines, self.cfg.chat_ui.line_cooldown)
        self._next_mood_save = time.monotonic() + self.cfg.mood.save_every
        self.engine.paused = self.settings.paused

        # --- the AI brain (Ollama, or scripted replies with --fake-brain)
        self.chat_enabled = self.cfg.brain.enabled
        self.engine.chat_enabled = self.chat_enabled
        fake = getattr(args, "fake_brain", False)
        backend = FakeBackend() if fake else OllamaBackend(self.cfg.brain)
        self.brain = Brain(
            self.cfg.brain, self.cat, self.bus, backend, context=self._prompt_context
        )
        self.brain.owner_name = self.cfg.owner_name
        # --- PC tasks (only cat.yaml allowed_tools, minus the tray's switches)
        self.tool_ctx = ToolContext(cfg=self.cfg)
        self.runner = ToolRunner(
            self.bus,
            self.tool_ctx,
            allowed=lambda: self.cat.allowed_tools,
            # web lookups are opt-in: hidden from her (and her phrases) until switched on
            disabled=lambda: [
                *self.settings.disabled_tools,
                *([] if self.settings.web_lookups else ["lookup"]),
            ],  # fmt: skip
            ask_confirm=self._ask_confirm,
        )
        self.brain.tools = self.runner.available
        self._last_question = ""
        self.store = Store(user_data_dir() / "coco.db")  # notes + reminders (local)
        # chat history for the dashboard (Settings there can pause it)
        self.chats = ChatRecorder(self.store, enabled=lambda: self.settings.history)
        self._next_source = "typed"  # who asks next: typed | voice | phone
        self._turn_source, self._turn_time = "typed", 0.0
        self._started_at = time.time()
        self.link: LinkServer | None = None
        self.tool_ctx.services["notes"] = NotesBook(self.store, notes_md_path())
        self.tool_ctx.services["reminders"] = ReminderBook(self.store)
        self.reminder_timer = QTimer()  # one timer, set for the next reminder due
        self.reminder_timer.setSingleShot(True)
        self.reminder_timer.timeout.connect(self._check_reminders)
        self._reminder_queue: list[Reminder] = []
        self.focus = FocusSession(self.cfg.focus.work_minutes, self.cfg.focus.break_minutes)
        self.tool_ctx.services["focus"] = self.focus
        self.tool_ctx.services["media"] = Media()
        self._battery: tuple[float, bool] | None = None  # (percent, plugged)
        self._battery_warned = 101.0  # the lowest level she already told you about
        self._next_battery = 0.0
        self._focus_lang = "en"
        self.focus_timer = QTimer()  # one timer: the end of the current work / break phase
        self.focus_timer.setSingleShot(True)
        self.focus_timer.timeout.connect(self._on_focus_phase_end)
        self.chip_timer = QTimer()  # updates the countdown, only while focus mode is on
        self.chip_timer.setInterval(1000)
        self.chip_timer.timeout.connect(self._update_focus_chip)
        # --- her voice
        self.speaker = Speaker(self.cfg.voice, self.cat.voice, self.bus)
        if self.settings.voice is not None:
            self.speaker.enabled = self.settings.voice
        self.engine.voice_on = self.speaker.enabled and self.speaker.available()
        self.listener = Listener(self.cfg.stt, self.bus)
        self.listener.owner_name = self.cfg.owner_name
        self.music_words = MusicWords()  # song / artist names she should hear right
        # --- memory (coco/memory.py): facts about you, recalled by meaning
        self.memory = MemoryBook(self.store, ollama_embedder(self.cfg.brain.host))
        self.tool_ctx.services["memory"] = self.memory
        self._apply_memory_setting()
        # --- what you're doing, screen time, break reminders (coco/screen_time.py)
        self.screen_time = ScreenTime(self.settings.break_minutes * 60)
        self.screen_time.load(user_data_dir() / "screen_time.json")  # today, after a restart
        self._fg_app: tuple[int, str] = (0, "")
        self.listener.music_words = self.music_words
        if self.settings.mic and not self.cfg.stt.device:
            self.listener.device_name = self.settings.mic  # the mic that worked last time
        if self.settings.mic_ready is not None:
            self.listener.keep_ready = self.settings.mic_ready
        self.listen_on = self.cfg.stt.enabled and self.listener.available()
        self.wake = self._make_wake()  # "Hey Coco" (None: off, or not trained yet)
        self._speaking = False
        self._talk_vk = 0  # key to watch for the push-to-talk release
        self._talk_timer = QTimer()  # runs only while you hold push-to-talk
        self._talk_timer.setInterval(33)
        self._talk_timer.timeout.connect(self._check_talk_key)
        self._waker = _Waker()
        self._waker.wake.connect(self._kick, Qt.ConnectionType.QueuedConnection)
        self.bus.on_post = self._waker.wake.emit  # AI worker posts -> main loop wakes

        sizes = self.cfg.ui.sizes
        self.size_name = next(
            (
                s
                for s in (self.settings.size, self.cat.size, self.cfg.ui.default_size)
                if s in sizes
            ),
            next(iter(sizes)),
        )
        self.engine.set_size(self.size_px)

        self.sprites = SpriteLibrary(
            self.manifest, self.size_px, self.mapper.dpr_for(self.sensor.monitor)
        )
        self.cat_window = CatWindow(
            self.bus, self.sprites, self.mapper, self.desktop, click_mask=self.cfg.ui.click_mask
        )
        self.cat_window.monitor = self.sensor.monitor
        self.cat_window.hotkey_handler = self._on_hotkey
        self.bed_back = BedLayer(bed_dir / self.bed_meta["back"], "back")
        self.bed_front = BedLayer(bed_dir / self.bed_meta["front"], "front")
        self.overlay = DebugOverlay(self.mapper, self.debug_info)
        self.overlay.cover(self.sensor.monitor)
        self.tray = Tray(
            self.bus, ASSETS_DIR / "icons" / "tray.png", list(sizes), self.cat.display_name
        )

        self.visible = True  # user's choice (tray / hotkey)
        self.polite_hidden = False  # hidden because a fullscreen app is running
        self.overlay_on = self.cfg.ui.show_debug_overlay
        self.world: WorldSnapshot | None = None
        self.pose: CatPose | None = None
        self._last = time.monotonic()
        self._fps_count, self._fps_since, self.fps = 0, time.monotonic(), 0.0

        self.bubble = SpeechBubble(self.bus, self.cfg.chat_ui, self.cat.display_name)
        self.bubble.owner_name = self.cfg.owner_name
        self.mood_bubble = MoodBubble()
        self.focus_chip = FocusChip()
        self.tool_ctx.services["launcher"] = Launcher(
            apps={**DEFAULT_APPS, **self.cfg.open.apps},
            sites={**DEFAULT_SITES, **self.cfg.open.sites},
        )
        # list the Start / Store apps in the background (~1 s of COM) so "open X" is instant
        threading.Thread(target=self.tool_ctx.launcher.start_apps, daemon=True).start()
        finder = FileFinder()
        if self.cfg.open.file_folders:
            extra = [Path(os.path.expandvars(f)) for f in self.cfg.open.file_folders]
            finder = FileFinder(finder.roots() + [p for p in extra if p.exists()])
        self.tool_ctx.services["files"] = finder
        self.tool_ctx.services["open_file"] = os.startfile  # only SAFE_TO_OPEN types
        self.tool_ctx.services["show_in_folder"] = win.show_in_folder
        self.tool_ctx.services["ask"] = self._ask_choice
        # --- clipboard (only read when you ask) + power (risky ones ask first)
        self.tool_ctx.services["clipboard"] = Clipboard()
        self.tool_ctx.services["power"] = Power()
        # (the Brave key is read fresh each time: the dashboard may have just saved one)
        self.tool_ctx.services["lookup"] = WebLookup(
            brave_key=lambda: str(Secrets().get("brave.api_key") or "")
        )
        self._last_answer = ""
        self.tool_ctx.services["last_answer"] = lambda: self._last_answer
        # --- do it for me (coco/agent): any app, step by step, behind the guard's rails
        self._main = MainThread()
        self.driving_chip = DrivingChip()
        self.agent = AgentHost(
            self.brain.backend,
            self.tool_ctx.launcher,
            begin=lambda goal: self._main.run(lambda: self._set_driving(True)),
            end=lambda: self._main.run(lambda: self._set_driving(False)),
            confirm=self._agent_confirm,
            active_window=self._agent_window,
            log_activity=lambda name, text, ok: self.store.log_activity("tool", name, text, ok),
            excluded=lambda: self.settings.excluded_apps or DEFAULT_EXCLUDED,
            own_pid=os.getpid(),
            owner=self.cfg.owner_name,
        )
        self.tool_ctx.services["agent_host"] = self.agent
        self.secrets = Secrets()
        launcher = self.tool_ctx.launcher
        self.tool_ctx.services["spotify"] = Spotify(
            self.secrets, launch=lambda: launcher.open(launcher.resolve("spotify"))
        )
        # Web API refused (family / student Premium)? Drive the Spotify app instead.
        self.tool_ctx.services["spotify_desktop"] = SpotifyDesktop(launcher)
        self._contacts_dialog = None
        self.tool_ctx.services["whatsapp"] = WhatsApp(self.store, self.tool_ctx.launcher)
        self.tool_ctx.services["screen"] = Screenshotter(
            self.cfg.screenshots,
            report=self.tool_ctx.report,
            monitor_rect=lambda: self.sensor.monitor.rect,
            active_window=self._active_window_for_shot,
            windows=lambda: [
                self.focus_chip,
                self.cat_window,
                self.bed_front,
                self.bed_back,
                self.bubble,
                self.mood_bubble,
                self.overlay,
            ],  # fmt: skip
            to_physical=self._logical_to_physical,
        )
        self.bus.subscribe("input.*", self._on_input)
        self.bus.subscribe("chat.*", self.engine.handle)
        self.bus.subscribe("brain.*", self.engine.handle)
        self.bus.subscribe(Topic.CHAT_ASK, self._on_ask)
        self.bus.subscribe(Topic.COMMAND_DONE, self._on_command_done)
        self.bus.subscribe(Topic.CHAT_SAY, self._on_say)
        self.bus.subscribe(Topic.BRAIN_REPLY, self._on_reply)
        self.bus.subscribe(Topic.TOOL_RESULT, self._on_tool_result)
        self.bus.subscribe("voice.*", self.engine.handle)
        self.bus.subscribe(Topic.HEARD, self._on_heard)
        self.bus.subscribe(Topic.VOICE_START, self._on_voice_start)
        self.bus.subscribe(Topic.VOICE_END, self._on_voice_end)
        self.bus.subscribe(Topic.BRAIN_STATUS, lambda e: self.bubble.on_status(e.payload))
        self.bus.subscribe(Topic.COMMAND, self._on_command)
        self.bus.subscribe(Topic.FULLSCREEN, self._on_fullscreen)
        self.bus.subscribe(Topic.STATE_CHANGED, self._on_state_changed)

        self.frame_timer = QTimer()
        self.frame_timer.setSingleShot(True)
        self.frame_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.frame_timer.timeout.connect(self._on_frame)

        self.slow_timer = QTimer()
        self.slow_timer.setInterval(int(self.cfg.polite.poll_interval * 1000))
        self.slow_timer.timeout.connect(self._on_slow)
        # Watchdog: dump all thread stacks to logs/hang.txt if the UI thread stalls for 30 s.
        self._hang_file = open(log_dir() / "hang.txt", "a", encoding="utf-8")  # noqa: SIM115

        self._display_debounce = QTimer()
        self._display_debounce.setSingleShot(True)
        self._display_debounce.setInterval(600)
        self._display_debounce.timeout.connect(self._on_display_changed)
        self.qapp.screenAdded.connect(self._watch_screen)
        self.qapp.screenRemoved.connect(lambda _s: self._display_debounce.start())
        self.qapp.primaryScreenChanged.connect(lambda _s: self._display_debounce.start())
        for screen in self.qapp.screens():
            self._watch_screen(screen, initial=True)

    # --- properties
    @property
    def size_px(self) -> int:
        return int(self.cfg.ui.sizes.get(self.size_name, 128))

    def _frame_px(self) -> float:
        return self.size_px * self.sensor.monitor.scale

    def _active(self) -> bool:
        return self.visible and not self.polite_hidden

    # --- startup
    def start(self) -> None:
        self._place_bed()
        self.world = self.sensor.snapshot(self._frame_px())
        self.engine.start(self.world)
        self.pose = self.engine.pose()
        self.cat_window.show_pose(self.pose)
        self._apply_visibility()

        hwnd = self.cat_window.hwnd()
        hk = self.cfg.ui.hotkeys
        win.register_hotkey(hwnd, HOTKEY_OVERLAY, hk.get("debug_overlay", "ctrl+alt+d"))
        win.register_hotkey(hwnd, HOTKEY_TOGGLE, hk.get("toggle_visible", "ctrl+alt+c"))
        if self.chat_enabled:
            win.register_hotkey(hwnd, HOTKEY_CHAT, hk.get("chat", "ctrl+alt+m"))
        if self.listen_on:
            talk = hk.get("talk", "ctrl+alt+v")
            if win.register_hotkey(hwnd, HOTKEY_TALK, talk):
                self._talk_vk = win.parse_hotkey(talk)[1]
        win.register_hotkey(hwnd, HOTKEY_SHOT, hk.get("screenshot", "ctrl+alt+s"))
        self.slow_timer.start()
        self.store.reset_shown()  # unanswered reminders from last time come back
        QTimer.singleShot(8000, self._check_reminders)  # (+ any missed while she was off)
        if self.listen_on:
            QTimer.singleShot(7000, self.listener.warm_up)  # two ears (~2.2 GB VRAM)
        if self.wake is not None:
            self.wake.start()
        if self.speaker.enabled and self.speaker.available():
            # Load her voice a few seconds after start (background thread, ~350 MB RAM),
            # so she can answer out loud without a 3 s "cold start" the first time.
            QTimer.singleShot(5000, self.speaker.warm_up)
        self._sync_tray()
        self._start_link()
        # Ollama, the dashboard and Tailscale come up with her (after start: no slowdown)
        QTimer.singleShot(
            3000, lambda: threading.Thread(target=self._bring_up, daemon=True).start()
        )
        self.store.log_activity("system", "started", "Coco started")
        m = self.sensor.monitor
        tb = self.desktop.taskbar()
        log.info(
            "Coco started: monitor %s %dx%d @ %.0f%%, work area %s, taskbar %s, size %s (%d px)",
            m.device,
            m.rect.width,
            m.rect.height,
            m.scale * 100,
            m.work,
            f"{tb.edge}{' auto-hide' if tb.autohide else ''}" if tb else "?",
            self.size_name,
            self.size_px,
        )

    def run(self) -> int:
        self.start()
        # Windows shutting down / signing out ends the app without her Quit: still save
        # her mood and today's screen time (and don't leave the "turned off" note).
        self.qapp.aboutToQuit.connect(self._save_state)
        return self.qapp.exec()

    def _save_state(self) -> None:
        faulthandler.cancel_dump_traceback_later()
        try:
            self.engine.mood.save(self.mood_path, time.time())
            self.screen_time.save(user_data_dir() / "screen_time.json")
        except Exception:  # noqa: BLE001 - shutting down: never crash here
            log.exception("Saving state on exit failed")

    # --- main loop
    def _on_frame(self) -> None:
        if not self._active():
            return
        now = time.monotonic()
        dt, self._last = now - self._last, now
        self.bus.drain()
        self.world = self.sensor.snapshot(
            self._frame_px(),
            self.engine.support_hwnd,
            sleeping=isinstance(self.engine.state, Sleep),
        )
        pose = self.engine.tick(dt, self.world)
        self.pose = pose
        if pose.visible:
            self.cat_window.show_pose(pose)
            if not self.cat_window.isVisible():
                self.cat_window.show()
                self._restack()
        elif self.cat_window.isVisible():
            self.cat_window.hide()
        if self.bubble.isVisible():
            self.bubble.follow(self._head_point(), self.cat_window.screen().geometry())
        icon = pose.mood_icon if pose.visible else None
        if icon is not None or self.mood_bubble.isVisible():
            was_visible = self.mood_bubble.isVisible()
            self.mood_bubble.update_icon(icon, self._head_point(), pose.facing)
            if not was_visible and self.mood_bubble.isVisible():
                self._restack()

        self._fps_count += 1
        if now - self._fps_since >= 1.0:
            self.fps = self._fps_count / (now - self._fps_since)
            self._fps_count, self._fps_since = 0, now

        frame_time = 1.0 / max(self.cfg.ui.max_fps, 1)
        delay = frame_time if pose.moving else min(max(pose.next_frame_in, frame_time), 0.25)
        self.frame_timer.start(max(1, int(delay * 1000)))

    def _kick(self) -> None:
        """Run the next frame right away (after mouse input) instead of waiting."""
        if not self._active():
            self.bus.drain()  # hidden: no frames, but answers (e.g. to your phone) still flow
        elif self.frame_timer.remainingTime() > 20:
            self.frame_timer.start(0)

    def _on_slow(self) -> None:
        faulthandler.dump_traceback_later(30, exit=False, file=self._hang_file)  # (re-armed)
        self.sensor.poll_fullscreen()  # publishes Topic.FULLSCREEN when it flips
        idle = self.desktop.user_idle_seconds()
        if self.screen_time.update(time.time(), idle, self._current_app()) == "break_due":
            self._offer_break()
        if self.sensor.home == "follow" and len(self.sensor.monitors) > 1 and idle < 30:
            w = self.sensor.active_window()
            m = monitor_containing(self.sensor.monitors, w.rect) if w is not None else None
            move = self.follower.update(
                time.monotonic(), self.sensor.monitor.device, m.device if m else None
            )
            if move and not (self.engine.chatting or self.agent.driving):
                self._move_to_monitor(move)
        if time.monotonic() >= self._next_battery:
            self._next_battery = time.monotonic() + self.cfg.system.check_every
            self._check_battery()
        if self._active():
            self._restack()
            self._maybe_say_something()
        now = time.monotonic()
        if now >= self._next_mood_save:
            self.engine.mood.save(self.mood_path, time.time())
            self.screen_time.save(user_data_dir() / "screen_time.json")
            self.store.prune_chats(self.settings.keep_chats_days)  # (old questions go)
            self._next_mood_save = now + self.cfg.mood.save_every

    def _maybe_say_something(self) -> None:
        """Rare canned lines (late at night, welcome back). No AI involved."""
        w = self.world
        quiet = self.engine.chatting or self.focus.active  # no chatty lines in focus mode
        if not self.cfg.chat_ui.unprompted_lines or w is None or quiet:
            return
        said = self.lines.check(time.monotonic(), w.hour, w.user_idle)
        if said is not None:
            kind, text = said
            text = personalize(text, self.cfg.owner_name)
            emotion = {"late_night": "sleepy", "welcome_back": "happy"}.get(kind, "neutral")
            log.info("Coco says (%s): %s", kind, text)
            self.bus.publish(Event(Topic.CHAT_SAY, {"text": text, "emotion": emotion}))
            self.bubble.follow(self._head_point(), self.cat_window.screen().geometry())

    # --- events
    def _on_input(self, event: Event) -> None:
        self.engine.handle(event)
        if event.topic == Topic.CLICK and self.chat_enabled:
            self.open_chat()
        self._kick()

    # --- chat
    def open_chat(self) -> None:
        """Click on Coco / chat hotkey: open the bubble and get the model ready."""
        if not self._active():
            return
        self.bus.publish(Event(Topic.CHAT_OPEN))
        self.bubble.follow(self._head_point(), self.cat_window.screen().geometry())
        self.bubble.open_chat()
        self._restack()
        if self.cfg.brain.prewarm_on_battery or not win.on_battery():
            self.brain.prewarm()
            if self.speaker.enabled:
                self.speaker.warm_up()  # load her voice too (~2 s, background)

    # --- voice input
    def _make_wake(self) -> WakeFlow | None:
        if not (self.listen_on and self.cfg.wake.enabled and WakeDetector.available()):
            if self.listen_on and self.cfg.wake.enabled:
                log.info("No 'Hey Coco' model yet (tools_dev/train_wake_word.py): push-to-talk")
            return None
        try:
            detector = WakeDetector.load()
        except Exception:
            log.exception("Could not load the 'Hey Coco' detector")
            return None
        confirm = self.listener.quick_hear if self.cfg.wake.confirm_with_whisper else None
        flow = WakeFlow(
            self.cfg.wake,
            self.bus,
            detector,
            hear=self.listener.hear,
            confirm=confirm,
            busy=lambda: self.listener.recording,
        )
        flow.enabled = self.settings.wake if self.settings.wake is not None else True
        if flow.enabled:
            self.listener.keep_ready = True  # the wake word needs the mic open
            self.listener.wake_sink = flow.push
        log.info(
            "Wake word ready (threshold %.2f, %s)",
            flow.threshold,
            "on" if flow.enabled else "off in the tray",
        )
        return flow

    def _on_voice_start(self, _event: Event) -> None:
        """Her own voice must never wake her: pause the wake word while she speaks."""
        self._speaking = True
        if self.wake is not None:
            self.wake.paused = True

    def _on_voice_end(self, _event: Event) -> None:
        self._speaking = False

        def resume() -> None:
            if self.wake is not None and not self._speaking:
                self.wake.paused = False

        QTimer.singleShot(500, resume)

    def _set_wake(self, on: bool) -> None:
        """Tray: Wake word (Hey Coco). On also keeps the mic ready (it must be open)."""
        if self.wake is None:
            return
        self.wake.enabled = on
        self.settings.wake = on
        self.listener.wake_sink = self.wake.push if on else None
        self.wake.reset()
        if on:
            self.listener.keep_ready = True
            self.settings.mic_ready = True
            self._open_ready_mic()

    def _open_ready_mic(self) -> None:
        """Microphone ready: keep the mic open while she's shown, so the first word you say
        after pressing the talk key is already captured (only ~1.5 s kept in memory)."""
        if self.listen_on and self.listener.keep_ready and self._active():
            self.listener.open_mic()

    def _start_talking(self) -> None:
        """Push-to-talk pressed: she stops talking and listens until you let go."""
        if not self._active() or self.listener.recording:
            return
        self.speaker.stop()
        if self.listener.start():
            self._talk_timer.start()
            self._kick()

    def _check_talk_key(self) -> None:
        released = not win.key_down(self._talk_vk)
        if released or self.listener.seconds_recording() > self.cfg.stt.max_seconds:
            self._talk_timer.stop()
            # keep listening a moment longer, so your last word isn't cut off
            QTimer.singleShot(int(self.cfg.stt.tail_seconds * 1000), self.listener.finish)

    def _on_heard(self, event: Event) -> None:
        r = event.payload
        if r["ok"]:
            mic = self.listener.current_device
            if mic and self.settings.mic != mic:  # this mic hears you: remember it
                self.settings.mic = mic
                self.settings.save()
            self.bus.publish(Event(Topic.CHAT_OPEN))
            self.bubble.show_heard(r["text"])
            self.bubble.follow(self._head_point(), self.cat_window.screen().geometry())
            self._restack()
            self._next_source = "voice"
            self.bus.publish(Event(Topic.CHAT_ASK, r["text"]))
            return
        lines = {
            "switched_mic": "Meow? I heard nothing. I'll listen with "
            f"{(r.get('mic') or 'another microphone').split(' (')[0]} next time: try again!",
            "no_mic": "I can't hear you: no microphone, or Windows is blocking it "
            "(Settings > Privacy > Microphone > let desktop apps use it).",
            "too_short": "Hold the key while you talk, then let go. Meow!",
            "wake_only": "Meow? I'm listening! Say Hey Coco and then what you want.",
        }
        text = lines.get(r["reason"], "Meow? Sorry, I didn't catch that.")
        self.bus.publish(Event(Topic.CHAT_SAY, {"text": text, "emotion": "curious"}))
        self.bubble.follow(self._head_point(), self.cat_window.screen().geometry())

    def _on_reply(self, event: Event) -> None:
        r = event.payload
        self.bubble.on_reply(r)
        if r.final:
            self.chats.answered(r.text)
            if r.text:
                self._last_answer = r.text  # ("copy that" copies this)
            if r.remember and self.settings.memory:
                threading.Thread(target=self._remember, args=(r.remember,), daemon=True).start()
        # speaks sentence by sentence (Hinglish: the Devanagari copy, in her Hindi voice)
        if not self._quiet_turn():
            self.speaker.feed(r.text, r.final, r.request_id, speech=r.speech, hindi=r.hindi)
        if r.final and r.request_id == getattr(self, "_vision_request", -1):
            self._vision_request = -1  # she's done looking: the Hinglish ear comes back
            threading.Thread(target=self.listener.unpark_hinglish, daemon=True).start()
        if r.final and r.tool != "none":  # the AI chose a PC task: run it (allow-listed)
            lang = "hinglish" if r.hindi else "en"
            call = ToolCall(r.tool, r.tool_args, said=self._last_question, lang=lang, source="ai")
            self.runner.handle(call)

    # --- tools
    def _on_tool_result(self, event: Event) -> None:
        call, result = event.payload
        args = ", ".join(f"{k}={v}" for k, v in call.args.items() if v)
        said = (result.say or result.facts or "").strip().replace("\n", " ")
        self.store.log_activity("tool", call.tool, f"{args} | {said[:200]}", result.ok)
        icon = TOOL_ICONS.get(call.tool)
        if call.tool == "reminder" and call.args.get("target") == "timer":
            icon = "timer"
        if icon and result.ok:
            self.engine.show_icon(icon, 3.0)  # (until the camera / sticky-note art exists)
        if call.tool == "spotify" and result.ok and result.data.get("played"):
            self.music_words.learn_played(str(result.data["played"]))
        if call.tool == "reminder":
            self._arm_reminders()  # a reminder was added / cancelled
        if call.tool == "focus" and result.data.get("focus"):
            self._focus_lang = call.lang
            self._apply_focus()
        if (result.facts or result.image_b64) and self.cfg.brain.enabled:
            # info / screen tools: she explains the result in her own words
            if result.image_b64 and self.listen_on:
                self.listener.park_hinglish()  # makes room on the GPU for the picture
            self._vision_request = self.brain.ask(
                call.said or call.tool,
                facts=result.facts,
                image_b64=result.image_b64,
                tools=False,
            )
            return
        text = result.say or result.facts
        if text:
            emotion = result.emotion if result.ok else "sad"
            self.bus.publish(
                Event(
                    Topic.CHAT_SAY,
                    {"text": text, "emotion": emotion, "speech": result.speech_hi},
                )
            )
            self.bubble.follow(self._head_point(), self.cat_window.screen().geometry())

    def _active_window_for_shot(self):
        w = self.sensor.active_window()
        if w is None:
            return None
        return w.rect, f'the window "{w.title}"' if w.title else "the active window"

    def _logical_to_physical(self, screen, qrect) -> Rect:
        """A QRect in Qt's logical px on `screen` -> physical screen px (for mss)."""
        m = next(
            (m for m in self.sensor.monitors if self.mapper.screen_for(m) is screen),
            self.sensor.monitor,
        )
        g, dpr = screen.geometry(), screen.devicePixelRatio()
        left = m.rect.left + (qrect.x() - g.x()) * dpr
        top = m.rect.top + (qrect.y() - g.y()) * dpr
        return Rect(left, top, left + qrect.width() * dpr, top + qrect.height() * dpr)

    # --- battery
    def _check_battery(self) -> None:
        """Low battery (not charging): she gets sleepy, and says so once (again lower)."""
        sc = self.cfg.system
        self._battery = (sc.fake_battery, False) if sc.fake_battery >= 0 else battery_only()
        if self._battery is None:
            return
        percent, plugged = self._battery
        if plugged or percent > sc.low_battery:
            self._battery_warned = 101.0  # charging / fine again: warn again next time
            return
        mood = self.engine.mood
        mood.energy = min(mood.energy, 0.25 if percent <= sc.very_low_battery else 0.4)
        level = sc.very_low_battery if percent <= sc.very_low_battery else sc.low_battery
        if level >= self._battery_warned or not self._active():
            return
        self._battery_warned = level
        if percent <= sc.very_low_battery:
            text = (
                f"Battery's at {percent:.0f}%!! *flops over* Charger, please, before we both nap!"
            )
        else:
            text = f"Your battery's at {percent:.0f}%... I'm getting sleepy too. *yawns* Charger?"
        log.info("Low battery: %.0f%%", percent)
        self.bus.publish(Event(Topic.CHAT_SAY, {"text": text, "emotion": "sleepy"}))
        self.bubble.follow(self._head_point(), self.cat_window.screen().geometry())

    # --- focus mode
    def _apply_focus(self) -> None:
        """Focus mode started / stopped / changed phase: her behaviour, timer, countdown."""
        on = self.focus.active
        self.engine.focusing = on
        if on:
            self.focus_timer.start(int(self.focus.remaining() * 1000) + 50)
            self.chip_timer.start()
            self._update_focus_chip()
        else:
            self.focus_timer.stop()
            self.chip_timer.stop()
            self.focus_chip.hide()
        self._sync_tray()

    def _update_focus_chip(self) -> None:
        if not self.focus.active or not self._active():
            self.focus_chip.hide()
            return
        icon = "☕" if not self.focus.working else "🍅"
        self.focus_chip.set_text(f"{icon} {clock_text(self.focus.remaining())}",
                                 on_break=not self.focus.working)  # fmt: skip
        self.focus_chip.follow(self._head_point())
        if not self.focus_chip.isVisible() and self.cat_window.isVisible():
            self.focus_chip.show()
            self._restack()

    def _on_focus_phase_end(self) -> None:
        phase = self.focus.advance()
        if phase is None:
            return
        hi = self._focus_lang == "hinglish"
        if phase.kind == "break":
            text = (f"Break time! {int(self.focus.break_min)} minute aaram karo, thoda stretch~"
                    if hi else f"Break time! Stretch a bit for {int(self.focus.break_min)} "
                    "minutes~ *yawns*")  # fmt: skip
            speech = "ब्रेक टाइम! थोड़ा स्ट्रेच करो।" if hi else ""
            self.engine.command("sleep")  # she naps in her hammock
            emotion = "sleepy"
        else:
            text = (
                "Wapas kaam pe! Tum kar sakte ho~ *serious cat face*"
                if hi
                else f"Back to work! Round {phase.round}, you got this~ *serious cat face*"
            )
            speech = "वापस काम पे! तुम कर सकते हो।" if hi else ""
            self.engine.command("get_up")
            emotion = "happy"
        log.info("Focus mode: %s (round %d)", phase.kind, phase.round)
        if self._active():
            self.bus.publish(
                Event(Topic.CHAT_SAY, {"text": text, "emotion": emotion, "speech": speech})
            )
            self.bubble.follow(self._head_point(), self.cat_window.screen().geometry())
        self._apply_focus()

    # --- Spotify
    def _connect_spotify(self) -> None:
        """Tray: Connect Spotify. Asks for your app's Client ID once, then you log in in
        your browser (PKCE; the page comes back to http://127.0.0.1:8890/callback)."""
        from PySide6.QtWidgets import QInputDialog

        sp = self.tool_ctx.spotify
        if not sp.client_id:
            text, ok = QInputDialog.getText(
                None,
                "Connect Spotify",
                "1. Open developer.spotify.com/dashboard and create an app\n"
                "2. Redirect URI:  http://127.0.0.1:8890/callback   (tick Web API)\n"
                "3. Paste the app's Client ID here:",
            )
            if not ok or not text.strip():
                return
            self.secrets.set("spotify.client_id", text.strip())
        verifier, challenge = make_pkce()
        state = make_pkce()[0][:16]

        def login() -> None:
            import webbrowser

            webbrowser.open(authorize_url(sp.client_id, challenge, state))
            code = wait_for_code(state)
            if code is None:
                text = "Spotify login didn't finish. Try Connect Spotify again?"
            else:
                try:
                    sp.finish_login(code, verifier)
                    text = "Spotify connected! Ask me to play anything~ 🎵"
                except Exception:
                    log.exception("Spotify login failed")
                    text = "Spotify login failed. Check the Client ID and the redirect URI?"
            self.bus.post(Event(Topic.CHAT_SAY, {"text": text, "emotion": "happy"}))

        threading.Thread(target=login, daemon=True, name="coco-spotify-login").start()

    # --- reminders
    def _arm_reminders(self) -> None:
        nxt = self.tool_ctx.reminders.next_due()
        if nxt is None:
            self.reminder_timer.stop()
            return
        ms = max(0.0, min((nxt - time.time()) * 1000, 3_600_000))  # re-check hourly
        self.reminder_timer.start(int(ms) + 50)

    def _check_reminders(self) -> None:
        if not self._active():  # hidden (fullscreen / kill switch): show them when she's back
            self.reminder_timer.start(30_000)
            return
        self._reminder_queue += self.tool_ctx.reminders.take_due()
        self._show_next_reminder()
        self._arm_reminders()

    def _show_next_reminder(self) -> None:
        if not self._reminder_queue or self.bubble.asking or not self._active():
            return
        r = self._reminder_queue.pop(0)
        hi = r.lang == "hinglish"
        late = time.time() - r.due > 120
        if r.kind == "timer":
            text = f"⏰ Time ho gaya! {r.text} khatam." if hi else f"⏰ Time's up! ({r.text})"
            choices = [("done", "OK")]
        else:
            if late:
                text = (f"📝 Jab main band thi: {r.text}" if hi
                        else f"📝 While I was off, you had: {r.text}")  # fmt: skip
            else:
                text = f"📝 Yaad hai? {r.text}" if hi else f"📝 Reminder: {r.text}"
            choices = [("done", "Ho gaya" if hi else "Done"),
                       ("snooze", "10 min baad" if hi else "Snooze 10 min")]  # fmt: skip
        book = self.tool_ctx.reminders

        def answered(key: str) -> None:
            if key == "snooze":
                book.snooze(r.id, 10)
            elif key == "dismissed":  # closed / kill switch: ask again in 5 minutes
                book.snooze(r.id, 5)
            else:
                book.done(r.id)
            self._arm_reminders()
            QTimer.singleShot(600, self._show_next_reminder)

        log.info("Reminder due: %s", r.text)
        self.engine.show_icon("timer" if r.kind == "timer" else "note", 8.0)
        self.engine.command("come_here")  # she comes over with it
        self.bubble.ask(text, choices, answered)
        self.bubble.follow(self._head_point(), self.cat_window.screen().geometry())
        self._restack()
        self.speaker.say(text.lstrip("⏰📝 "))

    def _ask_choice(self, call: ToolCall, question: str, choices, on_choice) -> None:
        """A tool asks you to pick (e.g. which of 3 files): buttons in her bubble."""
        if threading.current_thread() is not threading.main_thread():  # (tools' threads)
            self._main.run(lambda: self._ask_choice(call, question, choices, on_choice))
            return
        self.bubble.ask(question, choices, on_choice)
        self.bubble.follow(self._head_point(), self.cat_window.screen().geometry())
        self._restack()
        self.speaker.say(question)

    def _ask_confirm(self, call: ToolCall, question: str, done) -> None:
        """Risky tool: a Yes / No bubble (or answer by voice: yes / haan / no / nahi)."""
        hi = call.lang == "hinglish"
        choices = [("yes", "Haan" if hi else "Yes"), ("no", "Nahi" if hi else "No")]
        self.bubble.ask(question, choices, lambda key: done(key == "yes"))
        self.bubble.follow(self._head_point(), self.cat_window.screen().geometry())
        self._restack()
        self.speaker.say(question)

    def _on_say(self, event: Event) -> None:
        """A line without the AI (command reply, late-night tease): bubble + voice."""
        text = event.payload["text"]
        self.chats.said(text)
        if event.payload.get("animate", True):
            self.bubble.say(text)
        if not self._quiet_turn():
            self.speaker.say(text, speech=event.payload.get("speech", ""))

    def _on_ask(self, event: Event) -> None:
        """Typed (later: spoken) text. Short commands are handled by the engine itself
        (instant, no AI); anything else goes to the AI brain."""
        self.speaker.stop()  # a new question interrupts whatever she was saying
        text = event.payload or ""
        if self.bubble.asking and (is_yes(text) or is_no(text)):
            self.bubble.answer("yes" if is_yes(text) else "no")  # spoken Yes / No
            return
        self._command_lang = command_language(text)  # reply in kind
        self._last_question = text
        source, self._next_source = self._next_source, "typed"
        self._turn_source, self._turn_time = source, time.monotonic()
        self.chats.asked(source, text)
        if parse_command(text) is not None:
            return  # the engine does it (instant, no AI)
        call = parse_task(text, self.runner.available())
        if call is not None:
            self.runner.handle(call)  # a clear task: no AI needed
        else:
            self.brain.ask(text)

    def _on_command_done(self, event: Event) -> None:
        """Show her reply to a command ("Zoom zoom!" or why she can't)."""
        r = event.payload
        key = r["action"] if r["ok"] else f"cant_{r['reason']}"
        fallback = "ok" if r["ok"] else "cant"
        replies = self.cat.command_replies
        options = None
        if getattr(self, "_command_lang", "en") == "hinglish":
            hi = self.cat.command_replies_hinglish
            options = hi.get(key) or hi.get(fallback)
        options = options or replies.get(key) or replies.get(fallback) or ["Hmm?"]
        if isinstance(options, (str, dict)):
            options = [options]
        choice = random.choice(options)
        speech = ""
        if isinstance(choice, dict):  # Hinglish: {text: shown, say: Devanagari for voice}
            choice, speech = str(choice.get("text", "")), str(choice.get("say", "") or "")
        text = personalize(choice, self.cfg.owner_name)
        emotion = "happy" if r["ok"] else "sad"
        self.bubble.on_reply(
            BrainReply(text, emotion, final=True, request_id=self.bubble.request_id)
        )
        self.bubble.follow(self._head_point(), self.cat_window.screen().geometry())
        self.bus.publish(
            Event(
                Topic.CHAT_SAY,
                {"text": text, "emotion": emotion, "animate": not r["ok"], "speech": speech},
            )
        )

    def _head_point(self) -> QPointF:
        """Top-middle of her sprite (logical px): where the bubble's tail points."""
        g = self.cat_window.geometry()
        return QPointF(g.center().x(), g.top() + g.height() * 0.22)

    def _prompt_context(self) -> PromptContext:
        """Called from the AI worker thread for every question: only reads simple data."""
        state = self.engine.state.name if self.engine.state else "sit"
        surface = self.engine.surface.kind if self.engine.surface else "air"
        where = {"bed": "in your bed", "window": "on top of a window"}.get(
            surface, "on the desktop"
        )
        mood = getattr(self.engine.mood, "words", None)
        battery = ""
        b = self._battery
        if b is not None and not b[1] and b[0] <= self.cfg.system.low_battery:
            battery = f"Their laptop battery is at {b[0]:.0f}% and not charging."
        return PromptContext(
            owner=self.cfg.owner_name,
            now=datetime.now(),
            mood_words=mood() if mood else "",
            doing=f"{state.replace('_', ' ')} {where}",
            battery=battery,
            screen=self.screen_time.sentence(),
            about=self.settings.about_me,
        )

    def _on_state_changed(self, event: Event) -> None:
        log.debug("Coco: %s -> %s", event.payload["old"], event.payload["new"])

    def _on_hotkey(self, hotkey_id: int) -> None:
        if hotkey_id == HOTKEY_OVERLAY:
            self._command("toggle_overlay")
        elif hotkey_id == HOTKEY_TOGGLE:
            self._command("toggle_visible")
        elif hotkey_id == HOTKEY_CHAT and self.chat_enabled:
            self.open_chat()
        elif hotkey_id == HOTKEY_TALK and self.listen_on:
            self._start_talking()
        elif hotkey_id == HOTKEY_STOP:
            self.agent.stop()
        elif hotkey_id == HOTKEY_SHOT and self._active():
            self.runner.handle(ToolCall("screenshot", {"target": "area"}, said="(hotkey)"))

    def _on_fullscreen(self, event: Event) -> None:
        self.polite_hidden = bool(event.payload)
        self._apply_visibility()

    def _on_command(self, event: Event) -> None:
        payload = event.payload or {}
        self._command(payload.get("name", ""), **{k: v for k, v in payload.items() if k != "name"})

    def _command(self, name: str, **kw) -> None:
        log.info("command: %s %s", name, kw or "")
        if name == "toggle_visible":
            self.visible = not self.visible
            self._apply_visibility()
        elif name == "set_size" and kw.get("size") in self.cfg.ui.sizes:
            self.size_name = kw["size"]
            self.settings.size = self.size_name
            self.settings.save()
            self._apply_size()
        elif name == "toggle_voice":
            self.speaker.enabled = not self.speaker.enabled
            if not self.speaker.enabled:
                self.speaker.stop()
            self.engine.voice_on = self.speaker.enabled and self.speaker.available()
            self.settings.voice = self.speaker.enabled
            self.settings.save()
        elif name == "toggle_mic_ready" and self.listen_on:
            self.listener.keep_ready = not self.listener.keep_ready
            self.settings.mic_ready = self.listener.keep_ready
            if not self.listener.keep_ready and self.wake is not None and self.wake.enabled:
                self._set_wake(False)  # "Hey Coco" can't hear you with the mic closed
            self.settings.save()
            if self.listener.keep_ready:
                self._open_ready_mic()
            elif not self.listener.recording:
                self.listener.close_mic()
        elif name == "toggle_tool" and kw.get("tool") in REGISTRY.specs:
            off = set(self.settings.disabled_tools)
            off ^= {kw["tool"]}
            self.settings.disabled_tools = sorted(off)
            self.settings.save()
        elif name == "toggle_focus":
            if self.focus.active:
                self.focus.stop()
            else:
                self.focus.start()
            self._apply_focus()
        elif name == "toggle_wake" and self.wake is not None:
            self._set_wake(not self.wake.enabled)
            self.settings.save()
        elif name == "toggle_pause":
            self.engine.paused = not self.engine.paused
            self.settings.paused = self.engine.paused
            self.settings.save()
        elif name == "toggle_autostart":
            self._toggle_autostart()
        elif name == "toggle_overlay":
            self.overlay_on = not self.overlay_on
            self._apply_visibility()
        elif name == "force" and kw.get("activity"):
            self.engine.force(kw["activity"])
            self._kick()
        elif name == "reload_config":
            self._reload_config()
        elif name == "whatsapp_contacts":
            from coco.ui.contacts_dialog import ContactsDialog

            self._contacts_dialog = ContactsDialog(self.store)
            self._contacts_dialog.show()
            self._contacts_dialog.raise_()
            self._contacts_dialog.activateWindow()
        elif name == "connect_spotify":
            self._connect_spotify()
        elif name == "open_notes":
            book = self.tool_ctx.notes
            book.write_md()
            if book.md_path is not None and book.md_path.exists():
                os.startfile(book.md_path)  # noqa: S606 - our own notes file
        elif name == "open_screenshots":
            folder = shots_dir(self.cfg.screenshots.folder)
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(folder)  # noqa: S606 - our own folder
        elif name == "open_logs":
            os.startfile(log_dir())  # noqa: S606 - opens Explorer on our own folder
        elif name == "quit":
            self.quit()
            return
        self._sync_tray()

    # --- helpers
    def _apply_visibility(self) -> None:
        if self._active():
            self.bed_back.show()
            if self.pose is None or self.pose.visible:
                self.cat_window.show()
            self.bed_front.show()
            self.overlay.set_active(self.overlay_on)
            self._restack()
            self._last = time.monotonic()  # don't count hidden time as one giant dt
            self.frame_timer.start(0)
            self._open_ready_mic()
        else:
            self.frame_timer.stop()
            if not self.visible:
                self.agent.stop()  # kill switch: stop driving an app at once
            self.brain.cancel()  # kill switch / fullscreen: stop talking too
            self.speaker.stop()
            self.listener.cancel()  # kill switch: stop listening instantly
            self.tool_ctx.screen.cancel()  # ...and close the area picker
            if self.wake is not None:
                self.wake.reset()
            self._talk_timer.stop()
            self.bubble.close_chat()
            self.mood_bubble.hide_now()
            self.focus_chip.hide()
            for w in (self.cat_window, self.bed_front, self.bed_back):
                w.hide()
            self.overlay.set_active(False)

    def _restack(self) -> None:
        """Front-to-back: overlay, bed front, Coco, bed back. All topmost."""
        order = []
        if self.bubble.isVisible():
            order.append(int(self.bubble.winId()))
        if self.overlay.isVisible():
            order.append(int(self.overlay.winId()))
        if self.mood_bubble.isVisible():
            order.append(self.mood_bubble.hwnd())
        if self.focus_chip.isVisible():
            order.append(self.focus_chip.hwnd())
        order.append(self.bed_front.hwnd())
        if self.cat_window.isVisible():
            order.append(self.cat_window.hwnd())
        order.append(self.bed_back.hwnd())
        win.stack_topmost(order)

    def _place_bed(self) -> None:
        m = self.sensor.monitor
        geom = place_bed(
            self.bed_meta,
            m.work,
            m.scale,
            self.size_px,
            self.cat.home_corner,
            self.cat.home_margin,
            cat_frame_w=self.manifest["sit"].frame_w,
        )
        self.sensor.bed = geom
        for layer in (self.bed_back, self.bed_front):
            layer.place(geom, self.mapper, m)
        # If she's in the bed, keep her in it when it moves/resizes.
        s = self.engine.surface
        if s is not None and s.kind == "bed":
            self.engine.body.x, self.engine.body.y = geom.spot.x, geom.spot.y

    def _apply_size(self) -> None:
        self.engine.set_size(self.size_px)
        self.sprites.configure(self.size_px, self.mapper.dpr_for(self.sensor.monitor))
        self.cat_window.invalidate()
        self._place_bed()
        self._kick()

    def _watch_screen(self, screen, initial: bool = False) -> None:
        screen.geometryChanged.connect(lambda _g: self._display_debounce.start())
        screen.logicalDotsPerInchChanged.connect(lambda _d: self._display_debounce.start())
        if not initial:
            self._display_debounce.start()

    def _on_display_changed(self) -> None:
        """Monitors plugged/unplugged, resolution or scale changed: re-measure everything."""
        log.info("Display configuration changed: re-measuring")
        before = self.sensor.monitor.device
        self.sensor.refresh_monitors()
        if self.sensor.monitor.device != before:  # her screen went away (or her home came back)
            self._move_to_monitor(self.sensor.monitor.device, why="screens changed")
            return
        self.mapper.refresh(self.sensor.monitors)
        self.cat_window.monitor = self.sensor.monitor
        self.overlay.cover(self.sensor.monitor)
        self._apply_size()  # also re-places the bed with the new scale

    def _move_to_monitor(self, device: str, why: str = "following you") -> None:
        """Move her (and her bed) to another screen: she wakes up in her bed there."""
        m = next((x for x in self.sensor.monitors if x.device == device), None)
        if m is None:
            return
        log.info("Moving to %s (%s)", m.device, why)
        self.sensor.monitor = m
        self.sensor.refresh_surfaces()
        self.mapper.refresh(self.sensor.monitors)
        self.cat_window.monitor = m
        self.overlay.cover(m)
        self._apply_size()  # (re-places the bed on the new screen, at its scale)
        self.world = self.sensor.snapshot(self._frame_px())
        self.engine.start(self.world)  # she appears in her bed there
        self._kick()

    def _set_owner(self, name: str) -> None:
        """Her name for you, live (dashboard > About you)."""
        self.cfg.owner_name = name
        self.brain.owner_name = name
        self.listener.owner_name = name
        self.bubble.owner_name = name
        self.agent.owner = name

    def _set_monitor(self, value: str) -> None:
        """Dashboard: "primary", "follow" or a device name."""
        self.settings.monitor = value
        self.settings.save()
        self.sensor.home = value
        if value != "follow":
            target = next((m for m in self.sensor.monitors if m.device == value), None)
            target = target or next((m for m in self.sensor.monitors if m.primary), None)
            if target is not None and target.device != self.sensor.monitor.device:
                self._move_to_monitor(target.device, why="you chose this screen")

    def _reload_config(self) -> None:
        """Tray: Reload config + art. Re-reads config.yaml, cat.yaml, the sprite manifest
        + strips and the bed art, so new settings or new renders show without a restart."""
        cfg = load_config(self.config_path)
        self.cfg = cfg
        self.engine.cfg = cfg
        self.engine.director.cfg = cfg.behaviour
        self.engine.petter.cfg = cfg.petting
        self.sensor.cfg = cfg
        self.brain.cfg = cfg.brain
        self.bubble.cfg = cfg.chat_ui
        art_ok = self._reload_art()
        self._apply_size()
        log.info("config.yaml reloaded%s", " (and art)" if art_ok else "; art NOT reloaded")

    def _reload_art(self) -> bool:
        """Load cat.yaml + sprites + bed again. If the new art is broken (missing
        animation, bad YAML, missing file) she keeps her current art and the log says why."""
        try:
            cat = load_cat(self.cat.name)
            manifest = load_manifest(cat.manifest_path)
            if manifest.missing():
                raise ValueError(f"manifest lacks {manifest.missing()}")
            missing_files = [
                spec.file
                for spec in manifest.anims.values()
                if not (manifest.folder / spec.file).exists()
            ]
            if missing_files:
                raise FileNotFoundError(f"missing sprite files: {missing_files}")
            bed_dir, bed_meta = load_bed_assets()
        except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
            log.error("Reload: kept the current art, the new art has a problem: %s", exc)
            return False
        self.cat, self.manifest, self.bed_meta = cat, manifest, bed_meta
        self.engine.set_art(cat, manifest)
        self.sprites.set_manifest(manifest)
        self.cat_window.invalidate()
        self.brain.cat = cat
        self.speaker.voice = cat.voice
        self.lines.lines = cat.lines
        self.bed_back.load(bed_dir / bed_meta["back"])
        self.bed_front.load(bed_dir / bed_meta["front"])
        log.info("Art reloaded: %d animations", len(manifest.anims))
        return True

    def _toggle_autostart(self) -> None:
        if win.autostart_enabled(AUTOSTART_NAME):
            win.set_autostart(AUTOSTART_NAME, None)
        else:
            win.set_autostart(AUTOSTART_NAME, autostart_command())

    def _sync_tray(self) -> None:
        self.tray.sync(
            visible=self.visible,
            size=self.size_name,
            paused=self.engine.paused,
            autostart=win.autostart_enabled(AUTOSTART_NAME),
            overlay=self.overlay_on,
            voice=self.speaker.enabled,
            mic_ready=self.listener.keep_ready if self.listen_on else None,
            wake=self.wake.enabled if self.wake is not None else None,
            focus_left=self.focus.remaining() if self.focus.active else None,
            tools=[
                (n, REGISTRY.specs[n].label, n not in self.settings.disabled_tools)
                for n in REGISTRY.available(self.cat.allowed_tools)
            ],
        )

    # --- do it for me
    def _bring_up(self) -> None:
        """Make sure Ollama, her dashboard hub and Tailscale's tray app are running."""
        from coco.brain.ollama_runtime import ensure_server
        from coco.core.companions import bring_up

        done = bring_up(
            hub=self.settings.start_dashboard,
            tailscale=self.settings.start_tailscale,
            ensure_ollama=lambda: ensure_server(
                self.cfg.brain.host, self.cfg.brain.autostart_ollama, wait=30.0
            ),
        )
        if done.get("ollama") == "up" and self.settings.memory:
            self.memory.warm_up()  # load the memory model now (~6 s), not on your question
        self._sync_keeper()
        try:
            self._check_first_run(done.get("ollama") == "up")
        except Exception:  # noqa: BLE001 - never break her start
            log.exception("First-run check failed")

    def _sync_keeper(self) -> None:
        """The "Coco keeper" task (brings everything back after sleep / a failed shutdown)."""
        from coco.core import keeper_task

        try:
            ok = keeper_task.install() if self.settings.keep_running else keeper_task.uninstall()
            if not ok:
                log.warning("Keeper task: couldn't update it")
        except OSError as exc:
            log.warning("Keeper task: %s", exc)

    # --- first run
    def _check_first_run(self, ollama_up: bool) -> None:
        """A new PC: offer what she still needs (Ollama, her models, her voice)."""
        from coco import first_run
        from coco.core.companions import ollama_exe

        needs = first_run.missing(
            ollama_installed=ollama_up or ollama_exe() is not None,
            ollama_up=ollama_up,
            models=first_run.ollama_models(self.cfg.brain.host) if ollama_up else None,
            brain_model=self.cfg.brain.model,
            memory_model="qwen3-embedding:0.6b" if self.settings.memory else None,
            voice_ok=self.speaker.available(),
            skipped=first_run.load_skipped(user_data_dir() / "first_run.json"),
        )
        if needs:
            log.info("First run: she still needs %s", ", ".join(n.key for n in needs))
            self._main.run(lambda: self._offer_setup(needs))

    def _offer_setup(self, needs: list) -> None:
        """Ask about the first missing thing; Yes does it (on a thread), then the next."""
        if not needs:
            return
        from coco import first_run

        need, rest = needs[0], needs[1:]

        def answered(key: str) -> None:
            if key == "yes":
                threading.Thread(
                    target=self._do_setup, args=(need, rest), daemon=True, name="coco-setup"
                ).start()
            else:
                first_run.skip(user_data_dir() / "first_run.json", need.key)
                QTimer.singleShot(800, lambda: self._offer_setup(rest))

        self.bubble.ask(need.question, [("yes", "Yes"), ("no", "Not now")], answered)
        self.bubble.follow(self._head_point(), self.cat_window.screen().geometry())
        self._restack()
        self.speaker.say(need.question)

    def _setup_note(self, text: str) -> None:
        self.bubble.say(text)
        self.bubble.follow(self._head_point(), self.cat_window.screen().geometry())
        self._restack()

    def _do_setup(self, need, rest: list) -> None:
        from coco import first_run
        from coco.voice.tts import model_dir

        def note(text: str) -> None:
            self._main.run(lambda: self._setup_note(text))

        shown = [-1]

        def progress(f: float) -> None:
            pct = int(f * 100) // 5 * 5
            if pct != shown[0]:
                shown[0] = pct
                note(f"Downloading... {pct}%")

        try:
            if need.key == "ollama":
                os.startfile(first_run.OLLAMA_PAGE)  # noqa: S606 - a fixed https page
                note("Install Ollama, then restart me~")
            elif need.key in ("brain", "memory"):
                first_run.pull_model(self.cfg.brain.host, need.model, progress)
                if need.key == "memory":
                    self.memory.warm_up()
                note("Got it! *happy blink*")
            elif need.key == "voice":
                first_run.download_voice(model_dir(self.speaker.cfg), progress)
                self.speaker._load_failed = False  # (she can load it now)
                note("Now I can talk!")
            log.info("First run: %s done", need.key)
        except Exception as exc:  # noqa: BLE001 - network / disk: try again next start
            log.warning("First run: %s failed: %s", need.key, exc)
            note("That download didn't work. I'll ask again next time.")
        self._main.run(lambda: QTimer.singleShot(2500, lambda: self._offer_setup(rest)))

    # --- breaks
    def _current_app(self) -> str:
        """The program of the window you're using (cached per window: ~free each second)."""
        w = self.sensor.active_window()
        hwnd = w.hwnd if w is not None else 0
        if hwnd != self._fg_app[0]:
            from coco.agent.ui_tree import window_app

            self._fg_app = (hwnd, window_app(hwnd) if hwnd else "")
        return self._fg_app[1]

    def _offer_break(self) -> None:
        """You've been at it for a while: suggest a break (never mid-focus / mid-chat)."""
        busy = (
            not self._active()
            or self.focus.active
            or self.engine.chatting
            or self.agent.driving
            or self.bubble.asking
        )
        if busy:
            self.screen_time.snooze(300)  # ask again in 5 minutes
            return
        hi = getattr(self, "_command_lang", "en") == "hinglish"
        mins = describe_minutes(self.screen_time.session)
        question = (
            f"{mins} ho gaye screen pe~ thoda stretch, paani, aankhon ko aaram? 🐾"
            if hi
            else f"You've been at the screen for {mins}~ Stretch, some water, rest your eyes? 🐾"
        )
        choices = [("break", "Haan, break!" if hi else "Taking a break"),
                   ("later", "15 min baad" if hi else "In 15 min"),
                   ("today", "Aaj nahi" if hi else "Not today")]  # fmt: skip

        def answered(key: str | None) -> None:
            if key == "break":
                self.screen_time.took_break()
                text = ("Yay! Main bhi nap leti hoon~ *stretches*" if hi
                        else "Yay! I'll nap too~ *stretches*")  # fmt: skip
                self.engine.force("sleep")
            elif key == "today":
                self.screen_time.skip_today()
                text = ("Theek hai, aaj aur nahi poochungi~" if hi
                        else "Okay, no more break reminders today~")  # fmt: skip
            else:
                self.screen_time.snooze(900)
                text = "Theek hai, 15 minute aur~" if hi else "Okay, 15 more minutes~"
            self.bus.publish(Event(Topic.CHAT_SAY, {"text": text, "emotion": "happy"}))
            self.store.log_activity("tool", "break", f"break reminder | {key or 'closed'}", True)

        self.bus.publish(Event(Topic.CHAT_OPEN))
        self._ask_choice(ToolCall("break", {}, said=""), question, choices, answered)

    # --- memory
    def _apply_memory_setting(self) -> None:
        """Memory on: her brain recalls before answering and may save facts; off: neither."""
        if self.settings.memory:
            self.brain.recall = lambda question: [m.text for m in self.memory.recall(question)]
        else:
            self.brain.recall = None

    def _remember(self, text: str) -> None:
        """Her AI found something worth keeping (worker thread: the embedding takes ~0.1 s)."""
        saved = self.memory.add(text)
        if saved is None:
            return
        self.store.log_activity("tool", "memory", f"remembered | {saved.text}", True)
        self._main.run(lambda: self.engine.show_icon("note", 3.0))  # a little note: kept!

    def _set_driving(self, on: bool) -> None:
        """The red chip + Esc as a stop key, only while she's working inside an app."""
        hwnd = self.cat_window.hwnd()
        if on:
            self.driving_chip.show_on(self.cat_window.screen().geometry())
            win.register_hotkey(hwnd, HOTKEY_STOP, "esc")
        else:
            win.unregister_hotkey(hwnd, HOTKEY_STOP)
            self.driving_chip.hide()

    def _agent_confirm(self, question: str) -> bool:
        """Her Yes / No bubble from the agent's thread (60 s without an answer = No)."""
        box: dict = {}
        answered = threading.Event()

        def done(yes: bool) -> None:
            box["yes"] = yes
            answered.set()

        call = ToolCall("do_it", {}, said=question)
        self._main.run(lambda: self._ask_confirm(call, f"Should I {question}?", done))
        answered.wait(60)
        return bool(box.get("yes"))

    def _agent_window(self) -> int | None:
        w = self.sensor.active_window()
        return w.hwnd if w is not None else None

    # --- dashboard link
    def _quiet_turn(self) -> bool:
        """Answers to a question from your phone stay silent on the laptop (unless
        Settings > "Speak phone replies" is on)."""
        return (
            self._turn_source == "phone"
            and not self.settings.phone_speaks
            and time.monotonic() - self._turn_time < 120
        )

    def _start_link(self) -> None:
        main = self._main
        try:
            self.link = LinkServer({
                "status": lambda: main.run(self._hub_status),
                "command": lambda data: main.run(lambda: self._hub_command(data)),
                "chat": lambda text: main.run(lambda: self._hub_chat(text)),
            })  # fmt: skip
            self.link.start()
        except OSError:
            log.exception("Dashboard link could not start")
            self.link = None

    def _hub_status(self) -> dict:
        m = self.engine.mood
        battery = self._battery
        return {
            "name": self.cat.display_name,
            "owner": self.cfg.owner_name,
            "about_me": self.settings.about_me,
            "keep_chats_days": self.settings.keep_chats_days,
            "pid": os.getpid(),
            "uptime": time.time() - self._started_at,
            "visible": self.visible,
            "fullscreen_hidden": self.polite_hidden,
            "paused": self.engine.paused,
            "state": self.engine.state.name if self.engine.state else "sit",
            "anim": self.pose.anim if self.pose else "sit",
            "surface": self.engine.surface.kind if self.engine.surface else "air",
            "voice": self.speaker.enabled,
            "listen_on": self.listen_on,
            "mic_ready": self.listener.keep_ready if self.listen_on else None,
            "listening": self.listener.recording,
            "wake": self.wake.enabled if self.wake is not None else None,
            "speaking": self._speaking,
            "chat_enabled": self.chat_enabled,
            "mood": {s: round(getattr(m, s), 3) for s in MOOD_STATS} | {"words": m.words()},
            "focus": {
                "active": self.focus.active,
                "working": self.focus.working,
                "remaining": self.focus.remaining(),
            },
            "battery": {"percent": battery[0], "plugged": battery[1]} if battery else None,
            "size": self.size_name,
            "sizes": list(self.cfg.ui.sizes),
            "tools": [
                {"name": n, "label": REGISTRY.specs[n].label,
                 "on": n not in self.settings.disabled_tools}
                for n in REGISTRY.available(self.cat.allowed_tools)
            ],
            "history": self.settings.history,
            "phone_speaks": self.settings.phone_speaks,
            "autostart": win.autostart_enabled(AUTOSTART_NAME),
            "last_chat_id": self.chats.last_id,
            "driving": self.agent.driving,
            "memory": self.settings.memory,
            "screen_today": int(self.screen_time.today // 60),
            "screen_session": int(self.screen_time.session // 60),
            "app": self.screen_time.label,
            "break_minutes": self.settings.break_minutes,
            "web_lookups": self.settings.web_lookups,
            "monitor_mode": self.sensor.home,
            "monitor": self.sensor.monitor.device,
            "monitors": [
                {"device": m.device, "primary": m.primary,
                 "size": f"{m.rect.width:.0f}x{m.rect.height:.0f}"}
                for m in self.sensor.monitors
            ],  # fmt: skip
            "memories": self.memory.count(),
        }  # fmt: skip

    _TOGGLES = {
        "visible": "toggle_visible",
        "voice": "toggle_voice",
        "mic_ready": "toggle_mic_ready",
        "wake": "toggle_wake",
        "paused": "toggle_pause",
        "focus": "toggle_focus",
        "autostart": "toggle_autostart",
    }

    def _hub_command(self, data: dict) -> dict:
        """The dashboard's buttons. "set" takes the value you want (not a toggle), so two
        quick taps (or two phones) can't flip a switch back by accident."""
        name = str(data.get("name", ""))
        log.info("dashboard: %s", {k: v for k, v in data.items() if k != "text"})
        if name == "set":
            key, value = str(data.get("key", "")), bool(data.get("value"))
            status = self._hub_status()
            if key in self._TOGGLES:
                current = status["focus"]["active"] if key == "focus" else status.get(key)
                if current is None:
                    raise ValueError(f"{key} isn't available")
                if bool(current) != value:
                    self._command(self._TOGGLES[key])
            elif key.startswith("tool:"):
                tool = key.removeprefix("tool:")
                if tool not in REGISTRY.specs:
                    raise ValueError(f"no tool {tool}")
                if (tool not in self.settings.disabled_tools) != value:
                    self._command("toggle_tool", tool=tool)
            elif key in ("history", "phone_speaks", "start_dashboard", "start_tailscale",
                         "memory", "web_lookups", "keep_running"):  # fmt: skip
                setattr(self.settings, key, value)
                self.settings.save()
                self._apply_memory_setting()
                if key == "keep_running":
                    threading.Thread(target=self._sync_keeper, daemon=True).start()
            else:
                raise ValueError(f"unknown setting {key}")
        elif name == "set_size":
            if data.get("size") not in self.cfg.ui.sizes:
                raise ValueError("unknown size")
            self._command("set_size", size=data["size"])
        elif name == "kill":  # hide her and close the mic, right now
            if self.visible:
                self._command("toggle_visible")
        elif name == "force":
            self._command("force", activity=str(data.get("activity", "")))
        elif name == "reload_reminders":
            self._arm_reminders()
        elif name == "stop_driving":
            self.agent.stop()
        elif name == "set_about":
            name_ = str(data.get("owner_name", "")).strip()[:40]
            self.settings.owner_name = name_
            self.settings.about_me = str(data.get("about_me", "")).strip()[:600]
            self.settings.save()
            self._set_owner(name_ or load_config(self.config_path).owner_name)
        elif name == "set_keep_chats":
            days = int(data.get("days", 0))
            if days not in (0, 7, 30, 90, 365):
                raise ValueError("keep chats: 0 (forever), 7, 30, 90 or 365 days")
            self.settings.keep_chats_days = days
            self.settings.save()
            self.store.prune_chats(days)
        elif name == "set_monitor":
            value = str(data.get("value", ""))
            devices = {m.device for m in self.sensor.monitors}
            if value not in ("primary", "follow") and value not in devices:
                raise ValueError("unknown screen")
            self._set_monitor(value)
        elif name == "set_break_minutes":
            minutes = int(data.get("minutes", 60))
            if minutes not in (0, 30, 45, 60, 90, 120):
                raise ValueError("break reminders: 0, 30, 45, 60, 90 or 120 minutes")
            self.settings.break_minutes = minutes
            self.settings.save()
            self.screen_time.break_after = minutes * 60
        elif name == "set_excluded_apps":
            apps = [str(a).strip() for a in data.get("apps") or [] if str(a).strip()]
            self.settings.excluded_apps = apps[:100]
            self.settings.save()
        elif name == "quit":
            QTimer.singleShot(300, self.quit)  # answer the dashboard first
        else:
            raise ValueError(f"unknown command {name}")
        return {"ok": True, "status": self._hub_status()}

    def _hub_chat(self, text: str) -> dict:
        """A question typed on your phone: same path as one typed in her bubble."""
        if self._active():
            self.bus.publish(Event(Topic.CHAT_OPEN))
            self.bubble.show_heard(text)
            self.bubble.follow(self._head_point(), self.cat_window.screen().geometry())
            self._restack()
        self._next_source = "phone"
        self.bus.publish(Event(Topic.CHAT_ASK, text))
        return {"ok": True, "chat_id": self.chats.last_id}

    def debug_info(self) -> dict:
        eng, w, p = self.engine, self.world, self.pose
        m = eng.mood
        if w is None or p is None:
            return {}
        b = eng.body
        surface = eng.surface
        tb = self.desktop.taskbar()
        lines = [
            f"state     {p.state}",
            f"anim      {p.anim} #{p.frame}  facing {'right' if p.facing > 0 else 'left'}",
            f"paws      ({b.x:.0f}, {b.y:.0f}) px   v=({b.vx:.0f}, {b.vy:.0f})",
            f"surface   {surface.kind + ' ' + str(surface.id) if surface else '(in the air)'}",
            f"plan      {len(eng.plan)} step(s) queued   paused={eng.paused}",
            f"fps       {self.fps:.1f}   moving={p.moving}",
            f"size      {self.size_name} = {self.size_px} px  x{w.scale:.2f} scale",
            f"monitor   {self.sensor.monitor.device}  work {w.work.width:.0f}x{w.work.height:.0f}",
            f"taskbar   {tb.edge + (' auto-hide' if tb.autohide else '') if tb else '?'}",
            f"windows   {len(self.sensor.windows)} scanned, {len(w.surfaces)} surfaces",
            f"cursor    ({w.cursor.x:.0f}, {w.cursor.y:.0f})   idle {w.user_idle:.0f}s",
            f"sleepy    {eng.is_sleepy()}   fullscreen={self.sensor.fullscreen}",
            f"mood      H{m.happiness:.2f} E{m.energy:.2f} A{m.affection:.2f} "
            f"B{m.boredom:.2f} bond {m.bond:.2f}",
            f"feels     {m.words()}   chatting={eng.chatting}",
        ]
        return {
            "world": w,
            "lines": lines,
            "body_box": (b.x - eng.half_w, b.y - eng.height, b.x + eng.half_w, b.y, b.x, b.y),
        }

    def quit(self) -> None:
        """You turned her off (tray Quit / the dashboard): the keeper leaves her off."""
        log.info("Bye from Coco")
        from coco.keeper import note_turned_off

        note_turned_off(True)
        hwnd = self.cat_window.hwnd()
        win.unregister_hotkey(hwnd, HOTKEY_OVERLAY)
        win.unregister_hotkey(hwnd, HOTKEY_TOGGLE)
        win.unregister_hotkey(hwnd, HOTKEY_CHAT)
        win.unregister_hotkey(hwnd, HOTKEY_TALK)
        win.unregister_hotkey(hwnd, HOTKEY_SHOT)
        if self.wake is not None:
            self.wake.stop()
        if self.link is not None:
            self.link.stop()
        self.store.log_activity("system", "stopped", "Coco closed")
        self.listener.cancel()
        self.brain.cancel()
        self.speaker.shutdown()
        self.engine.mood.save(self.mood_path, time.time())
        self.store.close()
        self.frame_timer.stop()
        self.slow_timer.stop()
        faulthandler.cancel_dump_traceback_later()
        self.tray.icon.hide()
        self.qapp.quit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="coco", description="Coco, the desktop cat")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    parser.add_argument("--cat", help="which cats/<name> folder to load (default: config)")
    parser.add_argument("--config", help="path to a config.yaml")
    parser.add_argument(
        "--fake-brain",
        action="store_true",
        help="scripted replies instead of Ollama (test the chat UI)",
    )
    args = parser.parse_args(argv)
    from coco.core.package_guard import hand_off, redirected

    if redirected() and not os.environ.get("COCO_ALLOW_PACKAGED") and hand_off("coco"):
        return 0  # started from inside another app's package: Task Scheduler starts her
    setup_logging(args.debug)

    def log_uncaught(exc_type, exc, tb) -> None:
        log.critical("Uncaught exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = log_uncaught

    others = [] if os.environ.get("COCO_SKIP_PROCESS_CHECK") else other_coco_pids()
    if not win.acquire_single_instance("Local\\CocoDesktopCat") or others:
        log.info("Coco is already running%s; exiting", f" (pid {others[0]})" if others else "")
        if os.environ.get("COCO_FROM_KEEPER") or just_signed_in():
            return 0  # (the keeper and Start with Windows both started her: no box)
        QApplication.instance() or QApplication(sys.argv)
        QMessageBox.information(None, "Coco", "Coco is already on your desktop! (see the tray)")
        return 0
    try:  # Coco.exe: "Start with Windows" may still point at the source version
        if migrate_autostart(win.autostart_value, win.set_autostart):
            log.info("Start with Windows now starts %s", sys.executable)
    except OSError as exc:
        log.warning("Couldn't update the startup entry: %s", exc)
    from coco.keeper import note_turned_off

    note_turned_off(False)  # she's on: the keeper may bring her back if she's closed
    return CocoApp(args).run()
