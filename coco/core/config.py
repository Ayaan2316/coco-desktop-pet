"""Loads config.yaml and settings.json."""

from __future__ import annotations

import dataclasses
import json
import logging
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from coco.core.paths import CONFIG_FILE, user_data_dir

log = logging.getLogger(__name__)


@dataclass
class PhysicsConfig:
    gravity: float = 2600.0  # px/s^2
    air_drag: float = 0.35  # fraction of horizontal speed lost per second while airborne
    wall_bounce: float = 0.55  # 0 = dead stop, 1 = perfectly bouncy
    ceiling_bounce: float = 0.3
    max_throw_speed: float = 3200.0  # px/s, caps wild flicks
    throw_sample_window: float = 0.08  # seconds of mouse history used for the throw velocity
    max_fall_speed: float = 3500.0  # px/s terminal velocity


@dataclass
class MovementConfig:
    walk_speed: float = 48.0  # px/s at size M (scaled with her size); matches the art
    run_speed: float = 230.0
    stalk_speed: float = 28.0
    jump_apex_extra: float = 45.0  # how far above the higher end point a jump peaks
    jump_max_height: float = 900.0  # won't try to jump onto anything higher than this
    jump_crouch_time: float = 0.2  # s of crouch before take-off
    min_walk_distance: float = 80.0
    max_walk_distance: float = 650.0


@dataclass
class BehaviourConfig:
    decision_interval: list[float] = field(default_factory=lambda: [4.0, 10.0])
    fidget_interval: list[float] = field(default_factory=lambda: [3.0, 8.0])
    # Relative chance of each activity when she decides what to do next.
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "sit": 30,
            "loaf": 14,
            "groom": 8,
            "yawn": 4,
            "stretch": 4,
            "look_around": 8,
            "walk": 12,
            "run": 3,
            "go_home": 5,
            "jump_up": 4,
            "peek": 2,
            "stalk": 3,
            "sleep": 14,
            "sulk": 3,
            "hop_out": 18,
        }
    )
    # Minimum seconds between two uses of the same activity.
    cooldowns: dict[str, float] = field(
        default_factory=lambda: {
            "run": 45,
            "go_home": 120,
            "jump_up": 60,
            "peek": 240,
            "stalk": 240,
            "yawn": 30,
            "stretch": 30,
            "hop_out": 20,
        }
    )
    # [min, max] seconds for activities that last a while.
    durations: dict[str, list[float]] = field(
        default_factory=lambda: {
            "loaf": [10, 30],
            "groom": [4, 9],
            "sleep": [45, 150],
            "purr_max": [20, 20],
            "peek_hide": [3, 8],
        }
    )
    start_in_bed: bool = True  # (old configs may still say start_in_basket)
    drop_off_window_chance: float = 0.4  # at the end of a window top: hop down vs turn
    night_start_hour: int = 23
    night_end_hour: int = 7
    night_sleep_mult: float = 3.0
    night_move_mult: float = 0.5
    night_sleep_duration_mult: float = 2.0
    user_idle_sleepy_after: float = 600.0  # s without keyboard/mouse input -> sleepier


@dataclass
class PettingConfig:
    min_speed: float = 40.0  # px/s; slower = just resting the cursor
    max_speed: float = 1200.0  # px/s; faster = swiping past, not petting
    min_path: float = 260.0  # px of rubbing needed...
    min_reversals: int = 2  # ...with at least this many back-and-forth direction changes
    window: float = 2.0  # ...within this many seconds
    linger: float = 1.5  # keep purring this long after the rubbing stops


@dataclass
class CursorPlayConfig:
    reach: float = 520.0  # horizontal distance at which she notices the cursor
    max_height: float = 260.0  # cursor must be at most this far above her surface
    pounce_distance: float = 170.0  # starts the pounce when this close
    wiggle_time: float = 0.8  # butt-wiggle before the pounce
    give_up_after: float = 8.0  # s of stalking before losing interest
    click_through_time: float = 0.7  # s her window ignores clicks around a pounce


@dataclass
class PoliteConfig:
    enabled: bool = True
    poll_interval: float = 1.0  # s between fullscreen checks
    resume_delay: float = 2.0  # s to wait after fullscreen ends before reappearing


@dataclass
class SensorConfig:
    window_poll_interval: float = 0.5  # s between full window scans
    idle_window_poll_interval: float = 2.0  # same, while she's asleep in the bed
    support_tolerance: float = 2.0  # px a window may jitter before she counts it as moved


@dataclass
class UIConfig:
    default_size: str = "M"
    sizes: dict[str, int] = field(default_factory=lambda: {"S": 96, "M": 128, "L": 192})
    max_fps: int = 60
    click_mask: bool = True  # clip her window to the sprite's alpha (click-through)
    home_monitor: str = "primary"  # "primary" or a device name like \\.\DISPLAY2
    show_debug_overlay: bool = False
    hotkeys: dict[str, str] = field(
        default_factory=lambda: {
            "debug_overlay": "ctrl+alt+d",
            "toggle_visible": "ctrl+alt+c",
            "chat": "ctrl+alt+m",
            "talk": "ctrl+alt+v",  # hold to talk to her, release to send
            "screenshot": "ctrl+alt+s",  # area screenshot (drag a rectangle)
        }
    )


@dataclass
class BrainConfig:
    """The local AI brain (Ollama)."""

    enabled: bool = True
    model: str = "qwen3.5:9b"
    host: str = "http://127.0.0.1:11434"
    think: bool = False  # True = smarter but slower replies (thinks before answering)
    temperature: float = 0.7
    num_ctx: int = 8192  # context window in tokens
    keep_alive: str = "5m"  # Ollama unloads the model after this much idle time
    history_turns: int = 6  # question/answer pairs remembered within a chat
    history_reset_after: float = 1800.0  # s of no chatting -> forget the conversation
    max_reply_chars: int = 1200  # cut off runaway replies
    request_timeout: float = 120.0  # s
    autostart_ollama: bool = True  # run `ollama serve` (hidden) if it isn't running
    prewarm_on_battery: bool = False  # load the model while you type, even on battery
    name_every: int = 8  # she may say your name at most once per this many AI replies


@dataclass
class MoodConfig:
    """How Coco's mood drifts and reacts. Stats go from 0 to 1.

    Drifts use "half-life"-style rates: a stat moves part of the way to its target each
    second, so the result doesn't depend on the frame rate.
    """

    boredom_per_min: float = 0.02  # boredom rises this much per minute without attention
    energy_awake_minutes: float = 20.0  # awake: energy drifts to the time-of-day level
    energy_sleep_minutes: float = 10.0  # asleep: energy refills towards 1
    affection_minutes: float = 60.0  # affection drifts back to her bond-based baseline
    happiness_minutes: float = 10.0  # happiness eases towards a mix of the others
    pet_affection: float = 0.08
    pet_happiness: float = 0.06
    pet_bond: float = 0.004  # long-term bond grows a little with every pet
    chat_affection: float = 0.03
    chat_bond: float = 0.002
    play_boredom: float = 0.25  # pouncing/chatting removes this much boredom
    thrown_happiness: float = 0.05  # per 1000 px/s of throw speed
    bored_at: float = 0.6
    tired_at: float = 0.35
    sulk_affection: float = 0.3  # sulks if affection is below this...
    sulk_after: float = 2700.0  # ...and nobody played with her for 45 minutes
    save_every: float = 60.0  # s between saves of mood.json


@dataclass
class ChatUIConfig:
    """The speech bubble."""

    max_width: int = 340  # logical px
    max_height: int = 300
    chars_per_second: float = 40.0  # typewriter speed for replies
    linger_base: float = 4.0  # bubble stays this long after a reply...
    linger_per_char: float = 0.05  # ...plus this much per character
    unprompted_lines: bool = True  # rare canned lines (late night, welcome back...)
    line_cooldown: float = 1200.0  # s between two unprompted lines


@dataclass
class VoiceConfig:
    """Her speaking voice (Kokoro). The voice itself is set in cat.yaml `voice:`."""

    enabled: bool = True  # tray: Voice on/off (your choice is remembered)
    volume: float = 0.9  # 0..1
    model_dir: str = ""  # "" = %LOCALAPPDATA%\\Coco\\models\\kokoro
    max_chars: int = 600  # read at most this much of a long answer out loud


@dataclass
class SttConfig:
    """Listening: push-to-talk and speech recognition. Hotkey: ui.hotkeys.talk."""

    enabled: bool = True
    model: str = "large-v3-turbo"  # on the GPU
    # vs ~43% for small.en, ~0.3 s per sentence, ~1.1 GB VRAM)
    use_gpu: bool = True  # False = always use cpu_model on the CPU
    cpu_model: str = "small.en"  # used when there is no usable NVIDIA GPU
    hinglish: bool = True  # second ear for Hinglish (GPU only, ~1.1 GB more VRAM)
    hinglish_model: str = "hinglish-apex"  # folder in models/whisper (see README: Hinglish)
    keep_mic_ready: bool = True  # mic stays open (only the last ring_seconds kept in
    # memory) so your first word isn't lost while the mic wakes up. Tray: Microphone ready
    ring_seconds: float = 1.5  # rolling buffer length while waiting for the talk key
    pre_roll_seconds: float = 0.8  # how much from before the key press is kept
    tail_seconds: float = 0.35  # keep recording this long after you let go of the key
    cpu_threads: int = 8
    max_seconds: float = 15.0  # stop recording after this long
    device: str = ""  # microphone name (or part of it); "" = Windows default, auto-switch
    model_dir: str = ""  # "" = %LOCALAPPDATA%\\Coco\\models\\whisper


@dataclass
class ScreenshotConfig:
    """Screenshots. Saved in a local folder (Pictures\\Coco; never a OneDrive-
    synced one, so they don't leave this PC)."""

    folder: str = ""  # "" = Pictures\\Coco (local)
    keep_days: float = 0.0  # delete Coco's screenshots older than this (0 = keep forever)
    hide_ms: int = 150  # she turns invisible this long so she isn't in the picture
    max_side_for_ai: int = 1280  # "explain my screen": the AI gets a smaller copy


@dataclass
class AppsConfig:
    """ "open X". Your own names win over the built-in lists (tools/apps.py).
    Values: a program (path or exe name, %VARS% ok), "folder:Downloads", or a URL."""

    apps: dict[str, str] = field(default_factory=dict)  # "my project": "C:\\Code\\proj"
    sites: dict[str, str] = field(default_factory=dict)  # "college": "https://..."
    file_folders: list[str] = field(default_factory=list)  # extra folders to find files in


@dataclass
class FocusConfig:
    """Focus mode / Pomodoro."""

    work_minutes: float = 25.0
    break_minutes: float = 5.0


@dataclass
class SystemConfig:
    """Battery watch (she gets sleepy when your battery is low)."""

    low_battery: float = 20.0  # % (not charging): she says it once and gets sleepy
    very_low_battery: float = 10.0  # % : she says it again
    check_every: float = 60.0  # s
    fake_battery: float = -1.0  # testing: pretend the battery is at this % (unplugged)


@dataclass
class WakeConfig:
    """Hands-free "Hey Coco" (needs the mic open: stt.keep_mic_ready). The
    detector is trained on this PC: tools_dev/train_wake_word.py. Tray: Wake word."""

    enabled: bool = True
    threshold: float = 0.0  # 0 = the one picked by the trainer (saved with the model)
    confirm_with_whisper: bool = False  # Whisper double-check of each hit before recording
    # off: Whisper often mangles a lone "Hey Coco"
    cut_back: float = 0.15  # transcribe from this long before the hit
    confirm_seconds: float = 2.0  # how much audio the double-check looks at
    end_silence: float = 0.8  # s of quiet after you speak = you're done
    follow_up_seconds: float = 4.0  # only "Hey Coco"? wait this long for the rest
    max_seconds: float = 15.0
    min_speech_level: float = 0.006  # loudness that counts as speech (0..1)
    min_speech_after: float = 0.25  # s of speech after the hit = you said more


@dataclass
class Config:
    cat: str = "coco"
    owner_name: str = "friend"  # what she calls you (dashboard > About you)
    physics: PhysicsConfig = field(default_factory=PhysicsConfig)
    movement: MovementConfig = field(default_factory=MovementConfig)
    behaviour: BehaviourConfig = field(default_factory=BehaviourConfig)
    petting: PettingConfig = field(default_factory=PettingConfig)
    cursor_play: CursorPlayConfig = field(default_factory=CursorPlayConfig)
    polite: PoliteConfig = field(default_factory=PoliteConfig)
    sensors: SensorConfig = field(default_factory=SensorConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    brain: BrainConfig = field(default_factory=BrainConfig)
    chat_ui: ChatUIConfig = field(default_factory=ChatUIConfig)
    mood: MoodConfig = field(default_factory=MoodConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    stt: SttConfig = field(default_factory=SttConfig)
    wake: WakeConfig = field(default_factory=WakeConfig)
    screenshots: ScreenshotConfig = field(default_factory=ScreenshotConfig)
    open: AppsConfig = field(default_factory=AppsConfig)
    focus: FocusConfig = field(default_factory=FocusConfig)
    system: SystemConfig = field(default_factory=SystemConfig)


# ---
# Loading helpers
# ---


# Renamed keys, per section: {dataclass name: {old key: new key}}.
LEGACY_KEYS: dict[str, dict[str, str]] = {
    "BehaviourConfig": {"start_in_basket": "start_in_bed"},
}


def _build(cls: type, data: dict[str, Any] | None, where: str) -> Any:
    """Create dataclass `cls` from a dict, recursing into nested dataclasses.

    Dict-valued fields (weights, cooldowns, ...) are MERGED over their defaults, so your
    config only needs the keys you want to change.
    """
    data = dict(data or {})
    for old, new in LEGACY_KEYS.get(cls.__name__, {}).items():
        if old in data:  # renamed key: still accept the old spelling
            value = data.pop(old)
            data.setdefault(new, value)
            log.info("config: %s.%s is now called %s", where, old, new)
    hints = typing.get_type_hints(cls)
    obj = cls()
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue
        value = data.pop(f.name)
        hint = hints[f.name]
        current = getattr(obj, f.name)
        if dataclasses.is_dataclass(hint):
            value = _build(hint, value, f"{where}.{f.name}")
        elif isinstance(current, dict) and isinstance(value, dict):
            value = {**current, **value}
        elif isinstance(current, float) and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        setattr(obj, f.name, value)
    for unknown in data:
        log.warning("Unknown config key %s.%s (typo?) - ignored", where, unknown)
    return obj


def load_config(path: Path = CONFIG_FILE) -> Config:
    if not path.exists():
        log.warning("%s not found, using built-in defaults", path)
        return Config()
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return _build(Config, raw, "config")


# ---
# User settings (written by the app)
# ---


@dataclass
class UserSettings:
    size: str | None = None  # None = use cat.yaml / config default
    paused: bool = False  # "Pause wandering" in the tray
    voice: bool | None = None  # tray "Voice": None = use config.yaml
    mic: str | None = None  # the microphone that worked last time
    mic_ready: bool | None = None  # tray "Microphone ready": None = use config.yaml
    wake: bool | None = None  # tray "Wake word (Hey Coco)": None = use config.yaml
    disabled_tools: list[str] = field(default_factory=list)  # tray > Tools switches
    history: bool = True  # keep chat history for the dashboard (Settings > Chat history)
    phone_speaks: bool = False  # answers to questions from your phone: out loud too?
    excluded_apps: list[str] | None = None  # "do it for me" never touches these (None: default)
    start_dashboard: bool = True  # Coco starts her dashboard hub when she starts
    start_tailscale: bool = True  # ...and Tailscale's tray app (so your phone can connect)
    keep_running: bool = True  # the "Coco keeper" task brings everything back (coco/keeper.py)
    memory: bool = True  # she remembers things about you (dashboard > Memories)
    break_minutes: int = 60  # remind you to take a break after this long (0 = off)
    web_lookups: bool = False  # Wikipedia / Brave lookups (opt-in: questions go online)
    monitor: str = ""  # "" = config.yaml home_monitor, "primary", "follow", or a device
    owner_name: str = ""  # "" = config.yaml owner_name
    about_me: str = ""  # dashboard > About you: a few lines she always keeps in mind
    keep_chats_days: int = 0  # chat history: delete messages older than this (0 = forever)

    @staticmethod
    def path() -> Path:
        return user_data_dir() / "settings.json"

    @classmethod
    def load(cls) -> UserSettings:
        try:
            raw = json.loads(cls.path().read_text(encoding="utf-8"))
            return cls(
                size=raw.get("size"),
                paused=bool(raw.get("paused", False)),
                voice=raw.get("voice"),
                mic=raw.get("mic"),
                mic_ready=raw.get("mic_ready"),
                wake=raw.get("wake"),
                disabled_tools=[str(t) for t in raw.get("disabled_tools") or []],
                history=bool(raw.get("history", True)),
                phone_speaks=bool(raw.get("phone_speaks", False)),
                excluded_apps=raw.get("excluded_apps"),
                start_dashboard=bool(raw.get("start_dashboard", True)),
                start_tailscale=bool(raw.get("start_tailscale", True)),
                keep_running=bool(raw.get("keep_running", True)),
                memory=bool(raw.get("memory", True)),
                break_minutes=int(raw.get("break_minutes", 60) or 0),
                web_lookups=bool(raw.get("web_lookups", False)),
                monitor=str(raw.get("monitor") or ""),
                owner_name=str(raw.get("owner_name") or ""),
                about_me=str(raw.get("about_me") or ""),
                keep_chats_days=int(raw.get("keep_chats_days") or 0),
            )
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError):
            log.warning("Could not read %s, using defaults", cls.path())
            return cls()

    def save(self) -> None:
        try:
            self.path().write_text(json.dumps(dataclasses.asdict(self), indent=2), "utf-8")
        except OSError:
            log.exception("Could not save settings")
