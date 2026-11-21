"""Text to speech with Kokoro."""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from pathlib import Path

from coco.core.bus import EventBus
from coco.core.config import VoiceConfig
from coco.core.events import Event, Topic
from coco.core.paths import user_data_dir
from coco.voice.speech_text import SentenceStream, speakable, split_sentences

log = logging.getLogger(__name__)

MODEL_FILE = "kokoro-v1.0.onnx"
VOICES_FILE = "voices-v1.0.bin"


def model_dir(cfg: VoiceConfig) -> Path:
    return Path(cfg.model_dir) if cfg.model_dir else user_data_dir() / "models" / "kokoro"


class Speaker:
    def __init__(self, cfg: VoiceConfig, voice: dict, bus: EventBus) -> None:
        self.cfg = cfg
        self.voice = voice  # cat.yaml `voice:`
        self.bus = bus
        self.enabled = cfg.enabled
        self._queue: queue.Queue[tuple[int, str, str] | None] = queue.Queue()
        self._generation = 0  # bumped by stop(): queued/old sentences are dropped
        self._stream = SentenceStream(cfg.max_chars)
        self._stream_id = -1
        self._kokoro = None
        self._load_failed = False
        self._speaking = False
        self._audio: queue.Queue = queue.Queue()  # synthesized pieces waiting to be played
        self._pending = 0  # pieces being synthesized or waiting to play
        for target, name in ((self._run, "coco-tts"), (self._play, "coco-tts-play")):
            threading.Thread(target=target, daemon=True, name=name).start()

    # --- public API
    def available(self) -> bool:
        d = model_dir(self.cfg)
        return (d / MODEL_FILE).exists() and (d / VOICES_FILE).exists()

    def warm_up(self) -> None:
        """Load the model in the background now (1-2 s) so her first words aren't late."""
        self._queue.put((self._generation, "", "en"))

    def feed(
        self,
        text_so_far: str,
        final: bool,
        request_id: int,
        *,
        speech: str = "",
        hindi: bool = False,
    ) -> None:
        """Streamed AI reply: speak each sentence as soon as it's complete.

        hindi=True: read `speech` (Devanagari) with the Hindi voice instead of the text.
        If the reply ends without a Devanagari copy, the text is read after all."""
        if not self.enabled:
            return
        if request_id != self._stream_id:  # a new answer: start a fresh sentence stream
            self._stream_id = request_id
            self._stream = SentenceStream(self.cfg.max_chars)
        if hindi and (speech or not final):
            source, lang = speech, "hi"
        else:
            source, lang = text_so_far, "en"
        if hindi and final and not speech:
            log.info("voice: Hinglish reply without a Devanagari copy; reading the text")
        for sentence in self._stream.feed(source, final):
            log.debug("voice: queued %r (%s)", sentence[:40], lang)
            self._queue.put((self._generation, sentence, lang))

    def say(self, text: str, speech: str = "") -> None:
        """A short line. `speech` = a Devanagari version to say with the Hindi voice."""
        if not self.enabled:
            return
        lang = "hi" if speech else "en"
        for sentence in split_sentences(speakable(speech or text)):
            self._queue.put((self._generation, sentence, lang))

    def stop(self) -> None:
        """Stop talking right now and forget everything queued."""
        self._generation += 1
        self._set_speaking(False)
        try:
            import sounddevice as sd

            sd.stop()
        except Exception:  # noqa: BLE001 - no audio device: nothing to stop
            pass

    def shutdown(self) -> None:
        self.stop()
        self._queue.put(None)

    # --- worker
    def _load(self):
        if self._kokoro is None and not self._load_failed:
            if not self.available():
                log.warning(
                    "Voice model not found in %s (see README: Her voice)", model_dir(self.cfg)
                )
                self._load_failed = True
                return None
            try:
                from kokoro_onnx import Kokoro

                d = model_dir(self.cfg)
                os.environ.setdefault("ORT_LOGGING_LEVEL", "3")
                self._kokoro = Kokoro(str(d / MODEL_FILE), str(d / VOICES_FILE))
                log.info("Voice model loaded (%s)", self.voice.get("voice_id", "af_heart"))
            except Exception:
                log.exception("Could not load the Kokoro voice model")
                self._load_failed = True
        return self._kokoro

    def _set_speaking(self, on: bool) -> None:
        if on != self._speaking:
            self._speaking = on
            self.bus.post(Event(Topic.VOICE_START if on else Topic.VOICE_END))

    # Two threads form a little pipeline, so there are no gaps between pieces:
    #   _run (synthesizer): text piece -> audio, put on _audio
    #   _play (player):     plays _audio pieces one after another
    # While piece 1 is playing, piece 2 is already being synthesized.
    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                self._audio.put(None)
                return
            generation, sentence, lang = item
            first_load = self._kokoro is None
            kokoro = self._load()
            if kokoro is not None and not sentence and first_load:
                # warm_up(): one throwaway word also starts the pronunciation engine
                # (espeak) and ONNX's first run, so her first real words come faster.
                self._synthesize(kokoro, "Hello.")
            if not sentence or kokoro is None or generation != self._generation:
                continue
            try:
                self._pending += 1
                audio = self._synthesize(kokoro, sentence, lang)
                if generation == self._generation:
                    self._audio.put((generation, *audio))
                else:
                    self._pending -= 1
            except Exception:
                self._pending -= 1
                log.exception("Speaking failed")

    def _synthesize(self, kokoro, sentence: str, lang: str = "en"):
        if lang == "hi":
            voice_id = self.voice.get("voice_hi", "hf_alpha")
            code = "hi"
        else:
            voice_id = self.voice.get("voice_id", "af_heart")
            code = "en-gb" if voice_id.startswith("b") else "en-us"
        pitch = float(self.voice.get("pitch", 1.0))
        # Synthesize a bit slower when raising the pitch, so the pace stays natural;
        # playing it back faster (sr * pitch) then raises the pitch.
        speed = float(self.voice.get("speed", 1.0)) / pitch
        t0 = time.perf_counter()
        samples, sr = kokoro.create(sentence, voice=voice_id, speed=speed, lang=code)
        log.debug(
            "voice: %.2f s to synthesize %.1f s of %r",
            time.perf_counter() - t0,
            len(samples) / sr,
            sentence[:40],
        )
        return samples * self.cfg.volume, int(sr * pitch)

    def _play(self) -> None:
        import sounddevice as sd

        while True:
            item = self._audio.get()  # sleeps until there's something to play (no polling)
            if item is None:
                return
            generation, samples, rate = item
            self._pending -= 1
            if generation == self._generation:  # (stop() was called: drop it)
                self._set_speaking(True)
                try:
                    sd.play(samples, samplerate=rate)
                    sd.wait()
                except Exception:
                    log.exception("Audio playback failed (no output device?)")
            if self._pending <= 0 and self._queue.empty() and self._audio.empty():
                self._set_speaking(False)  # nothing left to say
