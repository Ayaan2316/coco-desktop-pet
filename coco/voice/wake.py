"""The "Hey Coco" wake word detector."""

from __future__ import annotations

import contextlib
import logging
import queue
import re
import threading
from collections import deque
from pathlib import Path

import numpy as np

from coco.core.events import Event, Topic
from coco.core.paths import user_data_dir

log = logging.getLogger(__name__)

CHUNK = 1280  # 80 ms at 16 kHz: openWakeWord makes one embedding per chunk
RATE = 16000


def wake_dir() -> Path:
    return user_data_dir() / "models" / "wake"


def mlp_forward(x: np.ndarray, layers: list[tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
    """A small ReLU network with a sigmoid output: rows of x -> scores 0..1."""
    for w, b in layers[:-1]:
        x = np.maximum(x @ w + b, 0.0)
    w, b = layers[-1]
    z = (x @ w + b).reshape(-1)
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


class WakeNet:
    """The trained 'Hey Coco' network: 16 x 96 embeddings -> score."""

    def __init__(self, layers, mean, std, frames: int = 16, threshold: float = 0.5) -> None:
        self.layers = layers
        self.mean = mean
        self.std = std
        self.frames = frames
        self.threshold = threshold

    @classmethod
    def load(cls, path: Path) -> WakeNet:
        w = np.load(path)
        n = int(w["n_layers"])
        layers = [(w[f"W{i}"].astype(np.float32), w[f"b{i}"].astype(np.float32)) for i in range(n)]
        return cls(layers, w["mean"], w["std"], int(w["frames"]), float(w["threshold"]))

    def save(self, path: Path) -> None:
        arrays = {f"W{i}": w for i, (w, _b) in enumerate(self.layers)}
        arrays |= {f"b{i}": b for i, (_w, b) in enumerate(self.layers)}
        np.savez(
            path,
            n_layers=len(self.layers),
            mean=self.mean,
            std=self.std,
            frames=self.frames,
            threshold=self.threshold,
            **arrays,
        )

    def score(self, windows: np.ndarray) -> np.ndarray:
        """windows: (n, frames, 96) embeddings -> (n,) scores."""
        x = windows.reshape(len(windows), -1)
        return mlp_forward((x - self.mean) / self.std, self.layers)


def feature_extractor(folder: Path | None = None):
    from openwakeword.utils import AudioFeatures

    folder = folder or wake_dir()
    return AudioFeatures(
        melspec_model_path=str(folder / "melspectrogram.onnx"),
        embedding_model_path=str(folder / "embedding_model.onnx"),
        inference_framework="onnx",
    )


class WakeDetector:
    """Streaming: feed audio as it arrives, get one score per 80 ms."""

    def __init__(self, net: WakeNet, features=None) -> None:
        self.net = net
        self.features = features or feature_extractor()
        self._pending = np.zeros(0, np.int16)
        self._warm = 0  # chunks fed since reset (the first scores are meaningless)

    @classmethod
    def load(cls, path: Path | None = None) -> WakeDetector:
        return cls(WakeNet.load(path or wake_dir() / "hey_coco.npz"))

    @staticmethod
    def available(path: Path | None = None) -> bool:
        folder = wake_dir()
        need = [path or folder / "hey_coco.npz", folder / "melspectrogram.onnx",
                folder / "embedding_model.onnx"]  # fmt: skip
        return all(p.exists() for p in need)

    @property
    def threshold(self) -> float:
        return self.net.threshold

    def reset(self) -> None:
        self.features.reset()
        self._pending = np.zeros(0, np.int16)
        self._warm = 0

    def feed(self, audio: np.ndarray) -> list[float]:
        """16 kHz mono float32 (or int16) audio -> a score for every complete 80 ms."""
        if audio.dtype != np.int16:
            audio = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
        data = np.concatenate([self._pending, audio])
        scores = []
        n = len(data) // CHUNK
        for i in range(n):
            self.features(data[i * CHUNK : (i + 1) * CHUNK])
            self._warm += 1
            if self._warm < self.net.frames + 10:  # buffers still filling with silence
                scores.append(0.0)
                continue
            emb = self.features.get_features(self.net.frames)  # (1, frames, 96)
            scores.append(float(self.net.score(emb)[0]))
        self._pending = data[n * CHUNK :]
        return scores


# The hands-free flow: "Hey Coco" ... "go to sleep"
_WAKE_PREFIX = re.compile(
    # "Hey" is sometimes heard as "Echo"; a split name leaves a stray "go." ("Coco, go. Stop.")
    r"^\W*(?:(?:hey|hi|hay|hello|a|ok|okay|echo|eco|ecco)\W+)?"
    r"(?:coco|koko|kuku|cocoa|coca|gogo|go go)\b(?:\W+(?:co|go|ko)[.!,])?\W*",
    re.IGNORECASE,
)
_HAS_NAME = re.compile(r"\b(coco|koko|kuku|cocoa|coca|gogo|go go|cuckoo)\b", re.IGNORECASE)


# What Whisper tends to hear in the pause after a lone "Hey Coco"
_FILLERS = {"so", "yes", "oh", "okay", "ok", "hmm", "uh", "um", "ah", "yeah", "hey", "hi",
            "and", "the", "a", "no", "huh", "mm", "you"}  # fmt: skip


def strip_wake_word(text: str) -> str:
    """ "Hey Coco, go to sleep." -> "go to sleep." ; "Hey Coco." / "Hey Coco. So," -> "" """
    rest = _WAKE_PREFIX.sub("", text, count=1).strip()
    if rest.lower().strip(" .,!?~") in _FILLERS:
        return ""
    return rest


def says_her_name(text: str) -> bool:
    return bool(_HAS_NAME.search(text))


class WakeFlow:
    """Listens for "Hey Coco" in the always-open mic stream, then records what you say.

    Feed it 16 kHz mono blocks with push() (from the audio thread: cheap, just queues
    them). A worker thread runs process():
      idle       -> the detector scores each 80 ms; a hit is double-checked by Whisper on
                    the last ~2 s ("does it say Coco?"): yes -> recording
      recording  -> starts with the wake word (so "Hey Coco, run" in one breath works),
                    ends after `end_silence` s of quiet once you've spoken, or after
                    `follow_up_seconds` if you only said "Hey Coco", or `max_seconds`
      -> transcribe, drop the "Hey Coco" from the front, post voice.heard like push-to-talk
         does (or reason "wake_only" if nothing came after the wake word)
    """

    def __init__(self, cfg, bus, detector, hear, confirm=None, busy=None) -> None:
        self.cfg = cfg  # core.config.WakeConfig
        self.bus = bus
        self.detector = detector
        self.hear = hear  # 16 kHz audio -> text (both ears)
        self.confirm = confirm  # 16 kHz audio -> text (quick check), None = trust detector
        self.busy = busy or (lambda: False)  # e.g. push-to-talk is recording
        self.paused = False  # she's speaking (her own voice must not wake her)
        self.enabled = True
        self.state = "idle"
        self._hist: deque[np.ndarray] = deque()
        self._hist_len = 0
        self._levels: deque[float] = deque(maxlen=60)  # recent block loudness (noise floor)
        self._buf: list[np.ndarray] = []
        self._since_wake = 0  # samples recorded after the wake word
        self._quiet = 0  # samples of quiet in a row
        self._spoke = False
        self._speech = 0  # samples of speech after the wake word
        self._cooldown = 0  # samples to ignore the detector after a rejected hit
        self.stats = {"hits": 0, "rejected": 0, "woke": 0}
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=400)
        self._thread: threading.Thread | None = None

    @property
    def threshold(self) -> float:
        return self.cfg.threshold or self.detector.threshold

    # --- threading
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="coco-wake")
            self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._queue.put(None)
            self._thread = None

    def push(self, block: np.ndarray) -> None:
        """From the audio thread: queue a 16 kHz mono block (dropped if we fall behind)."""
        if self.enabled:
            with contextlib.suppress(queue.Full):
                self._queue.put_nowait(block)

    def reset(self) -> None:
        """Mic closed / reopened: forget everything (state, history, detector buffers)."""
        self.state = "idle"
        self._hist.clear()
        self._hist_len = 0
        self._buf = []
        self.detector.reset()
        while not self._queue.empty():
            with contextlib.suppress(queue.Empty):
                self._queue.get_nowait()

    def _run(self) -> None:
        while True:
            block = self._queue.get()
            if block is None:
                return
            try:
                self.process(block)
            except Exception:
                log.exception("wake word: processing failed")
                self.state = "idle"

    # --- the flow
    def process(self, block: np.ndarray) -> None:
        block = np.asarray(block, np.float32).reshape(-1)
        self._hist.append(block)
        self._hist_len += len(block)
        while self._hist and self._hist_len - len(self._hist[0]) >= 3 * RATE:
            self._hist_len -= len(self._hist.popleft())
        level = float(np.sqrt(np.mean(block**2))) if len(block) else 0.0
        if self.state == "recording":
            self._record(block, level)
            return
        self._levels.append(level)
        scores = self.detector.feed(block)  # always fed, so its buffers stay continuous
        if self._cooldown > 0:
            self._cooldown -= len(block)
            return
        if self.paused or self.busy() or not scores or max(scores) < self.threshold:
            return
        self.stats["hits"] += 1
        clip = self._recent(self.cfg.confirm_seconds)
        if self.confirm is not None:
            heard = self.confirm(clip)
            if not says_her_name(heard):
                self.stats["rejected"] += 1
                log.info(
                    "wake word: detector hit (%.2f) but Whisper heard %r: ignored",
                    max(scores),
                    heard,
                )
                self._cooldown = RATE  # 1 s
                return
        self.stats["woke"] += 1
        log.info("wake word: Hey Coco! (score %.2f)", max(scores))
        self.state = "recording"
        self._buf = [clip]
        self._since_wake = 0
        self._quiet = 0
        self._spoke = False
        self._speech = 0
        self.bus.post(Event(Topic.LISTEN_START, {"wake": True}))

    def _recent(self, seconds: float) -> np.ndarray:
        audio = np.concatenate(list(self._hist)) if self._hist else np.zeros(0, np.float32)
        return audio[-int(seconds * RATE) :]

    def _speech_level(self) -> float:
        # room noise = the quieter part of the last few seconds (needs a few blocks first)
        floor = float(np.percentile(self._levels, 20)) if len(self._levels) >= 10 else 0.0
        return max(self.cfg.min_speech_level, floor * 3)

    def _record(self, block: np.ndarray, level: float) -> None:
        self._buf.append(block)
        self._since_wake += len(block)
        if level > self._speech_level():
            self._speech += len(block)
            # the last bit of "Coco" itself is still loud right after the hit: real speech
            # after the wake word = at least min_speech_after seconds of it
            self._spoke = self._speech >= self.cfg.min_speech_after * RATE
            self._quiet = 0
        else:
            self._quiet += len(block)
        done = (
            (self._spoke and self._quiet >= self.cfg.end_silence * RATE)
            or (not self._spoke and self._since_wake >= self.cfg.follow_up_seconds * RATE)
            or self._since_wake >= self.cfg.max_seconds * RATE
        )
        if done:
            self._finish()

    def _safe_hear(self, audio: np.ndarray) -> str:
        try:
            return self.hear(audio)
        except Exception:
            log.exception("wake word: speech recognition failed")
            return ""

    def _finish(self) -> None:
        """Transcribe what was said after "Hey Coco". Audio is cut `cut_back` seconds
        before the detector hit; sending the wake word to Whisper too makes it mangle
        the words around it.
        """
        before, after = self._buf[0], self._buf[1:]
        self._buf = []
        self.state = "busy"
        self.bus.post(Event(Topic.LISTEN_END, {"cancelled": False}))
        rest = ""
        if self._spoke and after:
            tail = before[-int(self.cfg.cut_back * RATE) :] if self.cfg.cut_back > 0 else before[:0]
            rest = strip_wake_word(self._safe_hear(np.concatenate([tail, *after])))
        if not rest:
            # Nothing after the hit: maybe it all came in one go before the detector was sure
            # ("Hey Coco, run!"). Look at the whole thing, but only trust it if Whisper heard
            # her name too (random audio must never become a command).
            full = self._safe_hear(np.concatenate([before, *after]))
            if says_her_name(full):
                rest = strip_wake_word(full)
                if not rest:
                    self.bus.post(
                        Event(Topic.HEARD, {"ok": False, "reason": "wake_only", "text": ""})
                    )
            else:
                # Whisper often mangles a lone "Hey Coco": answer "Meow?" but don't act on it.
                log.info("wake word: woke, but nothing to act on (heard %r)", full)
                self.bus.post(Event(Topic.HEARD, {"ok": False, "reason": "wake_only", "text": ""}))
        if rest:
            self.bus.post(Event(Topic.HEARD, {"ok": True, "reason": "", "text": rest}))
        self.state = "idle"
        self.detector.reset()
        self._cooldown = RATE // 2
