"""Speech to text with faster-whisper, English and Hinglish."""

from __future__ import annotations

import contextlib
import logging
import re
import threading
import time
from collections import deque

import numpy as np

from coco.behaviour.commands import command_language, parse_command
from coco.core.bus import EventBus
from coco.core.config import SttConfig
from coco.core.events import Event, Topic
from coco.core.paths import user_data_dir
from coco.voice.music_words import music_pick

log = logging.getLogger(__name__)

RATE = 16000  # Whisper wants 16 kHz mono

# Whisper's well-known "phantom" outputs on silence or noise: never treat them as speech.
_HALLUCINATIONS = {
    "", "you", "thank you", "thank you.", "thanks for watching", "thanks for watching!",
    "bye", "bye.", ".", "...", "[blank_audio]", "(silence)", "[music]", "♪",
}  # fmt: skip


# Ways Whisper mishears her name at the start of a sentence ("Googoo, stop").
_NAME_SOUNDALIKES = re.compile(
    r"^(hey,?\s+)?(googoo|goo goo|gogo|go go|koko|kuku|cuckoo|cocoa|coca|kokko|coko)\b",
    re.IGNORECASE,
)


def clean_transcript(text: str) -> str:
    """Tidy Whisper's output; returns "" for silence/phantom phrases."""
    text = " ".join(text.split()).strip()
    text = _NAME_SOUNDALIKES.sub(lambda m: (m.group(1) or "") + "Coco", text)
    if text.lower().strip(" .!?") in {h.strip(" .!?") for h in _HALLUCINATIONS}:
        return ""
    return text


def judge_audio(audio: np.ndarray, min_seconds: float = 0.35, min_rms: float = 0.004) -> str:
    """ "ok", "too_short" or "silence" for a recording (float32, 16 kHz)."""
    if len(audio) < min_seconds * RATE:
        return "too_short"
    if float(np.sqrt(np.mean(np.square(audio)))) < min_rms:
        return "silence"
    return "ok"


def boost(audio: np.ndarray, target_peak: float = 0.9, max_gain: float = 30.0) -> np.ndarray:
    """Make quiet speech louder; a built-in mic often records very softly."""
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if peak <= 0:
        return audio
    return (audio * min(target_peak / peak, max_gain)).astype(np.float32)


def enable_cuda_dlls() -> bool:
    """Let faster-whisper find NVIDIA's CUDA libraries (pip packages nvidia-cublas-cu12 and
    nvidia-cudnn-cu12 put their DLLs in site-packages/nvidia/*/bin). True if a GPU is usable."""
    import os
    import site
    import sys

    bases = list(site.getsitepackages())
    if getattr(sys, "frozen", False):  # Coco.exe: the trimmed copy inside the build
        bases.insert(0, getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    for base in bases:
        nvidia = os.path.join(base, "nvidia")
        if not os.path.isdir(nvidia):
            continue
        for lib in os.listdir(nvidia):
            bin_dir = os.path.join(nvidia, lib, "bin")
            if os.path.isdir(bin_dir):
                os.add_dll_directory(bin_dir)
                os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001
        return False


def vocabulary_prompt() -> str:
    """Whisper's initial prompt: her name and her commands, so "Coco, run" isn't heard
    as "go go, ran". Keep it short: extra words in here get hallucinated.
    """
    return (
        "Coco is a cat. Talking to Coco: Coco, run. Coco, go to sleep. Come here, Coco. "
        "Jump on my window. Stop. Play with me. Get up."
    )


HINGLISH_PROMPT = (
    "Coco ek billi hai. Coco, so jao. Coco, idhar aao. Ruko. Bhaago. Mere saath khelo. "
    "Mera code kyun crash ho raha hai? Samjhao na yaar."
)

# The Hinglish ear writes her name strangely at the start: "co co", "Ecko ko", "To to", "Ego"
_HINGLISH_NAME = re.compile(
    r"^(hey,?\s+)?(?:(?:a|e)?(?:c|k)k?o,?\s*(?:c|k)o|co,?\s*co|ko,?\s*ko|to,?\s*to|go,?\s*go|"
    r"ego(?:\s+code)?|acko|ecko|coco)(?:\s+ko)?\b[,.]?\s*",
    re.IGNORECASE,
)


def fix_hinglish_name(text: str) -> str:
    """ "co co idhar aao" -> "Coco, idhar aao" (only at the very start)."""
    m = _HINGLISH_NAME.match(text)
    return ("Coco, " + text[m.end() :]).strip() if m else text


def pick_transcript(english: tuple[str, float], hinglish: tuple[str, float] | None) -> str:
    """Choose between the two ears' (text, confidence) results.

    1. A Hinglish command from the Hinglish ear wins (the English ear tends to map
       those onto its hint phrases).
    2. Else an English command from the English ear.
    3. Else whichever ear was more confident (average log-probability).
    """
    if hinglish is None:
        return english[0]
    hi_text = fix_hinglish_name(hinglish[0])
    if parse_command(hi_text) and command_language(hi_text) == "hinglish":
        return hi_text
    if parse_command(english[0]):
        return english[0]
    return hi_text if hinglish[1] > english[1] else english[0]


def to_16k_mono(audio: np.ndarray, rate: int) -> np.ndarray:
    """Mix down to mono and resample to Whisper's 16 kHz (linear interpolation is plenty
    for speech)."""
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    audio = audio.astype(np.float32)
    if rate == RATE or len(audio) == 0:
        return audio
    n_out = int(len(audio) * RATE / rate)
    x_out = np.arange(n_out) * (rate / RATE)
    return np.interp(x_out, np.arange(len(audio)), audio).astype(np.float32)


def next_device(names: list[str], current: str) -> str | None:
    """The microphone to try after `current` (round robin), or None if there's only one."""
    if len(names) < 2:
        return None
    i = names.index(current) if current in names else -1
    return names[(i + 1) % len(names)]


def input_devices() -> list[tuple[int, str, int, int]]:
    """Microphones as (index, name, channels, native rate), preferring Windows' modern
    audio API (WASAPI), default input first."""
    import sounddevice as sd

    apis = sd.query_hostapis()
    wasapi = next((i for i, a in enumerate(apis) if a["name"] == "Windows WASAPI"), None)
    default_name = ""
    with contextlib.suppress(Exception):  # no default input: keep ""
        default_name = sd.query_devices(kind="input")["name"]
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] < 1 or (wasapi is not None and d["hostapi"] != wasapi):
            continue
        out.append((i, d["name"], int(d["max_input_channels"]), int(d["default_samplerate"])))
    # the Windows default first (names can be cut short in other APIs: compare prefixes)
    out.sort(key=lambda dev: not dev[1].startswith(default_name[:25]) if default_name else 0)
    return out


class Listener:
    def __init__(self, cfg: SttConfig, bus: EventBus) -> None:
        self.cfg = cfg
        self.bus = bus
        self._model = None
        self._hinglish = None  # the second ear (GPU only)
        self._hinglish_lock = threading.Lock()
        self._hinglish_parked = False  # moved to RAM while the AI looks at a screenshot
        self._model_lock = threading.Lock()
        self.device_type = "cpu"  # where the speech model runs ("cuda" = GPU)
        self._stream = None
        self._lock = threading.Lock()  # the audio thread and the app share the buffers
        self._ring: deque[np.ndarray] = deque()  # the last ~1.5 s (mic ready mode)
        self._ring_frames = 0
        self._recording = False
        self._chunks: list[np.ndarray] = []
        self._started = 0.0
        self.device_name: str = cfg.device  # chosen mic (name); "" = Windows default
        self._current = ""  # the mic open now / used last
        self.owner_name = "you"  # set by the app (config: owner_name)
        self._switched_to: str | None = None
        self._rate = RATE
        self.keep_ready = cfg.keep_mic_ready  # the tray can change this at runtime
        self.wake_sink = None  # set by the app: gets every block as 16 kHz mono ("Hey Coco")
        self.music_words = None  # set by the app: music names she should hear right

    # --- model
    def available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    def _load(self):
        with self._model_lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                root = self.cfg.model_dir or str(user_data_dir() / "models" / "whisper")
                t0 = time.perf_counter()
                name = self.cfg.model
                if self.cfg.use_gpu and enable_cuda_dlls():
                    try:
                        self._model = WhisperModel(
                            name,
                            device="cuda",
                            compute_type="int8_float16",
                            download_root=root,
                        )
                        self.device_type = "cuda"
                    except Exception:
                        log.warning("Speech model can't use the GPU; using the CPU", exc_info=True)
                if self._model is None:
                    name = self.cfg.cpu_model
                    self._model = WhisperModel(
                        name,
                        device="cpu",
                        compute_type="int8",
                        cpu_threads=self.cfg.cpu_threads,
                        download_root=root,
                    )
                    self.device_type = "cpu"
                log.info(
                    "Speech recognition ready (%s on the %s, %.1f s)",
                    name,
                    "GPU" if self.device_type == "cuda" else "CPU",
                    time.perf_counter() - t0,
                )
                self._load_hinglish(root)
            return self._model

    def _load_hinglish(self, root: str) -> None:
        """The Hinglish ear: only on the GPU (on the CPU it would take seconds per phrase)."""
        from pathlib import Path

        from faster_whisper import WhisperModel

        if not self.cfg.hinglish or self.device_type != "cuda":
            return
        path = Path(self.cfg.hinglish_model)
        if not path.is_absolute():
            path = Path(root) / path
        if not (path / "model.bin").exists():
            log.info("No Hinglish ear at %s (see README: Hinglish); English only", path)
            return
        t0 = time.perf_counter()
        try:
            self._hinglish = WhisperModel(str(path), device="cuda", compute_type="int8_float16")
            log.info("Hinglish ear ready (%s, %.1f s)", path.name, time.perf_counter() - t0)
        except Exception:
            log.warning("Could not load the Hinglish ear; English only", exc_info=True)

    def park_hinglish(self) -> None:
        """Move the Hinglish ear to system RAM for a while, freeing VRAM for the vision
        model. She only hears English until it's back.
        """
        with self._hinglish_lock:
            if self._hinglish is None or self._hinglish_parked:
                return
            try:
                self._hinglish.model.unload_model(to_cpu=True)
                self._hinglish_parked = True
                log.info("Hinglish ear parked in RAM (the AI is looking at your screen)")
            except Exception:
                log.warning("Could not park the Hinglish ear", exc_info=True)

    def unpark_hinglish(self) -> None:
        with self._hinglish_lock:
            if self._hinglish is None or not self._hinglish_parked:
                return
            t0 = time.perf_counter()
            try:
                self._hinglish.model.load_model()
            except Exception:
                log.warning("Could not bring the Hinglish ear back", exc_info=True)
                return
            self._hinglish_parked = False
            log.info("Hinglish ear back on the GPU (%.1f s)", time.perf_counter() - t0)

    def warm_up(self) -> None:
        threading.Thread(target=self._load, daemon=True, name="coco-stt-load").start()

    # --- microphone
    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def mic_open(self) -> bool:
        return self._stream is not None

    @property
    def current_device(self) -> str:
        return self._current

    def _on_audio(self, indata, frames, t, status) -> None:  # runs on the audio thread
        block = indata.copy()
        self._push(block)
        sink = self.wake_sink
        if sink is not None:
            sink(to_16k_mono(block, self._rate))  # just queued: the wake thread does the work

    def _push(self, block: np.ndarray) -> None:
        with self._lock:
            self._ring.append(block)
            self._ring_frames += len(block)
            keep = int(self.cfg.ring_seconds * self._rate)
            while len(self._ring) > 1 and self._ring_frames - len(self._ring[0]) >= keep:
                self._ring_frames -= len(self._ring.popleft())
            if self._recording:
                self._chunks.append(block)

    def _open_first_working(self, sd) -> bool:
        """Open the preferred microphone, or the next one that works."""
        devices = input_devices()
        if self.device_name:
            named = [d for d in devices if self.device_name.lower() in d[1].lower()]
            devices = named + [d for d in devices if d not in named]
        for index, name, channels, rate in devices:
            try:
                # Open the mic the way it wants (often 48 kHz stereo); we convert to 16 kHz.
                self._rate = rate
                stream = sd.InputStream(
                    device=index,
                    samplerate=rate,
                    channels=channels,
                    dtype="float32",
                    blocksize=rate // 20,
                    callback=self._on_audio,
                )
                stream.start()
            except Exception as exc:
                log.info("Mic %r won't open (%s); trying the next one", name, exc)
                continue
            self._stream, self._current = stream, name
            log.info("Microphone open: %r", name)
            return True
        log.warning("No microphone could be opened")
        return False

    def open_mic(self) -> bool:
        """Open the microphone (if it isn't already). False if none works."""
        if self._stream is not None:
            return True
        import sounddevice as sd

        with self._lock:
            self._ring.clear()
            self._ring_frames = 0
        if self._open_first_working(sd):
            return True
        # Windows may have changed its device list (headset plugged/unplugged):
        # refresh PortAudio's view of the devices and try once more.
        try:
            sd._terminate()
            sd._initialize()
        except Exception:  # noqa: BLE001
            log.debug("PortAudio refresh failed", exc_info=True)
        return self._open_first_working(sd)

    def close_mic(self) -> None:
        """Close the microphone completely and forget the rolling buffer."""
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.debug("closing the mic stream failed", exc_info=True)
            log.info("Microphone closed")
        with self._lock:
            self._ring.clear()
            self._ring_frames = 0

    # --- recording
    def start(self) -> bool:
        """Talk key pressed: start recording (including the moment just before it)."""
        if self._recording:
            return True
        if self._stream is None and not self.open_mic():
            self.bus.post(Event(Topic.HEARD, {"ok": False, "reason": "no_mic", "text": ""}))
            return False
        with self._lock:
            # Pre-roll: keep the last `pre_roll_seconds` from before the key press.
            want = int(self.cfg.pre_roll_seconds * self._rate)
            pre: list[np.ndarray] = []
            have = 0
            for block in reversed(self._ring):
                if have >= want:
                    break
                pre.insert(0, block)
                have += len(block)
            self._chunks = pre
            self._recording = True
        self._started = time.monotonic()
        self.bus.post(Event(Topic.LISTEN_START))
        return True

    def _stop_recording(self) -> np.ndarray:
        with self._lock:
            self._recording = False
            chunks, self._chunks = self._chunks, []
        raw = np.concatenate(chunks) if chunks else np.zeros((0, 1), np.float32)
        audio = to_16k_mono(raw, self._rate)
        keep_open = self.keep_ready and self._stream is not None
        if judge_audio(audio) == "silence" and not self.cfg.device:
            # Nothing heard. Either you didn't speak, or this mic hears nothing (an empty
            # headset jack). We can't tell which (some built-in mics output pure zeros in a
            # quiet room), so switch to the next microphone. Once a mic hears words, the app
            # remembers it.
            nxt = next_device([d[1] for d in input_devices()], self._current)
            if nxt is not None and nxt != self._current:
                log.info("Heard nothing on %r; switching to %r", self._current, nxt)
                self.device_name = nxt
                self._switched_to = nxt
                self.close_mic()
                if keep_open:
                    self.open_mic()
        if not self.keep_ready:
            self.close_mic()
        return audio

    def cancel(self) -> None:
        """Kill switch / hidden: stop recording, throw it away, CLOSE the microphone."""
        was_recording = self._recording
        with self._lock:
            self._recording = False
            self._chunks = []
        self.close_mic()
        if was_recording:
            self.bus.post(Event(Topic.LISTEN_END, {"cancelled": True}))

    def finish(self) -> None:
        """Key released: transcribe what was said (in the background)."""
        if not self._recording:
            return
        audio = self._stop_recording()
        self.bus.post(Event(Topic.LISTEN_END, {"cancelled": False}))
        threading.Thread(
            target=self._transcribe, args=(audio,), daemon=True, name="coco-stt"
        ).start()

    def seconds_recording(self) -> float:
        return time.monotonic() - self._started if self._recording else 0.0

    # --- transcription
    @staticmethod
    def _run(model, audio: np.ndarray, prompt: str | None) -> tuple[str, float]:
        segments, _info = model.transcribe(
            audio,
            language="en",  # (both ears write English letters)
            beam_size=1,  # (5 is slower, not more accurate)
            without_timestamps=True,
            vad_filter=True,
            initial_prompt=prompt,
        )
        segments = list(segments)
        text = clean_transcript(" ".join(s.text for s in segments))
        confidence = float(np.mean([s.avg_logprob for s in segments])) if segments else -9.0
        return text, confidence

    def quick_hear(self, audio: np.ndarray) -> str:
        """English ear only, no hint words (hints full of "Coco" could make it imagine her
        name): the wake word's double-check."""
        model = self._load()
        return self._run(model, boost(audio), None)[0]

    def hear(self, audio: np.ndarray) -> str:
        """Transcribe 16 kHz audio with both ears and keep the better result (blocking)."""
        model = self._load()
        audio = boost(audio)
        t0 = time.perf_counter()
        english = self._run(model, audio, vocabulary_prompt())
        hinglish = None
        with self._hinglish_lock:
            if self._hinglish is not None and not self._hinglish_parked:
                hinglish = self._run(
                    self._hinglish, audio, vocabulary_prompt() + " " + HINGLISH_PROMPT
                )
        text = None
        if hinglish is not None and self.music_words is not None:
            text = music_pick(english[0], fix_hinglish_name(hinglish[0]), self.music_words)
        text = text or pick_transcript(english, hinglish)
        log.info(
            "Heard %r (%.2f s audio, %.2f s; english %r %.2f, hinglish %r %.2f)",
            text,
            len(audio) / RATE,
            time.perf_counter() - t0,
            english[0],
            english[1],
            hinglish[0] if hinglish else None,
            hinglish[1] if hinglish else 0.0,
        )
        return text

    def _transcribe(self, audio: np.ndarray) -> None:
        switched = self._switched_to
        if switched:
            self._switched_to = None
            self.bus.post(
                Event(
                    Topic.HEARD,
                    {"ok": False, "reason": "switched_mic", "text": "", "mic": switched},
                )
            )
            return
        verdict = judge_audio(audio)
        if verdict != "ok":
            self.bus.post(Event(Topic.HEARD, {"ok": False, "reason": verdict, "text": ""}))
            return
        try:
            text = self.hear(audio)
        except Exception:
            log.exception("Speech recognition failed")
            self.bus.post(Event(Topic.HEARD, {"ok": False, "reason": "error", "text": ""}))
            return
        ok = bool(text)
        self.bus.post(
            Event(Topic.HEARD, {"ok": ok, "reason": "" if ok else "silence", "text": text})
        )
