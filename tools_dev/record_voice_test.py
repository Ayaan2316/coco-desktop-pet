r"""Record test clips for tuning speech recognition and the wake word.

    .venv\Scripts\python tools_dev\record_voice_test.py                 # 12 sentences
    .venv\Scripts\python tools_dev\record_voice_test.py --set wake      # "Hey Coco" x 40
    .venv\Scripts\python tools_dev\record_voice_test.py --set talk      # normal speech
    .venv\Scripts\python tools_dev\record_voice_test.py --set hinglish  # Hinglish lines
    .venv\Scripts\python tools_dev\record_voice_test.py --set all

Clips are saved in %LOCALAPPDATA%\Coco\voice_test.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import sounddevice as sd  # noqa: E402

from coco.core.config import UserSettings  # noqa: E402
from coco.core.paths import user_data_dir  # noqa: E402
from coco.voice.stt import RATE, input_devices, to_16k_mono  # noqa: E402

# (text to read, seconds, hint) -- hint is shown in brackets, not read out
DEFAULT = [(s, 5.0, "") for s in (
    "Coco, run.", "Coco, go to sleep.", "Come here, Coco.", "Coco, jump on my window.",
    "Hey Coco, stop.", "Coco, play with me.", "What is a capacitor?",
    "How does a transistor work?", "Explain Ohm's law in one line.",
    "Tell me a joke about cats.", "My exam went really badly today.",
    "Can you help me with my Python code?",
)]  # fmt: skip

_WAKE_STYLES = (
    [("normally", 8)] + [("softly, almost whispering", 6)]
    + [("from a step back (about 1 m from the laptop)", 6)]
    + [("quickly", 5)] + [("slowly", 5)]
)  # fmt: skip
WAKE = [("Hey Coco", 2.5, style) for style, n in _WAKE_STYLES for _ in range(n)] + [
    (f"Hey Coco, {cmd}", 4.0, "in one breath, no pause")
    for cmd in (
        "run", "go to sleep", "come here", "stop", "play with me",
        "what time is it?", "so jao", "idhar aao", "tell me a joke", "jump on my window",
    )
]  # fmt: skip

TALK = [  # never say "Coco" here: these teach her what not to wake up on
    ("(talk freely) What did you do today? In English.", 30.0, "don't say Coco"),
    ("(talk freely) Explain a college project or a circuit you like. In English.", 30.0,
     "don't say Coco"),
    ("(talk freely) The same thing again, in Hinglish.", 30.0, "don't say Coco"),
    ("(talk freely) Your favourite food or movie, in Hinglish.", 30.0, "don't say Coco"),
    ("(talk freely) Pretend you're on a phone call with a friend, Hinglish.", 30.0,
     "don't say Coco"),
    ("Say these, slowly, a few times each: cocoa, cuckoo, taco, coffee, go go, hello, "
     "okay Google, hey Cortana, local, poco, yoyo, hey Siri", 30.0, "near-misses"),
    ("Count from 1 to 30 in English, then 1 to 20 in Hindi.", 30.0, "don't say Coco"),
    ("(talk freely) Explain Ohm's law or anything from your ECE classes.", 30.0,
     "don't say Coco"),
    ("Stay quiet and just do normal stuff: type, click, move in your chair.", 30.0,
     "background noise"),
    ("(talk freely) Anything you like, mixing English and Hinglish.", 30.0,
     "don't say Coco"),
]  # fmt: skip

HINGLISH = [(s, 5.0, "") for s in (
    "Coco, so jao.", "Coco, idhar aao.", "Coco, bhaag!", "Coco, ruko.",
    "Coco, mere saath khelo.", "Coco, uth jao.", "Coco, chhup jao.",
    "Coco, mera code kyun crash ho raha hai?", "Aaj mera exam bahut bura gaya yaar.",
    "Capacitor kya hota hai, simple mein samjhao.", "Mujhe ek joke sunao.",
    "Kal subah nau baje mujhe yaad dilana.",
    "Python mein list aur tuple mein kya difference hai?", "Aaj ka mausam kaisa hai?",
    "Thoda music laga do na.",
)]  # fmt: skip

MUSIC = [(f"Hey Coco, {s}", 5.0, "in one breath, like you'd really ask") for s in (
    "play Madira by Seedhe Maut", "play Kesariya", "play Tum Hi Ho by Arijit Singh",
    "play Sahiba", "play Brown Munde on YouTube", "play songs by Seedhe Maut",
    "Seedhe Maut ke gaane lagao", "Kesariya bajao", "play Apna Bana Le",
    "play Excuses by AP Dhillon", "play my liked songs", "play some lofi",
    "play Raataan Lambiyan", "what's playing?", "play with me",
    "play Namastute by Seedhe Maut", "open WhatsApp", "play Chaleya on YouTube",
    "play Madira by Seedhe Maut", "play Kesariya by Arijit Singh",
)]  # fmt: skip

SETS = {"default": DEFAULT, "wake": WAKE, "talk": TALK, "hinglish": HINGLISH, "music": MUSIC}


def pick_mic(wanted: str = "") -> tuple[int, str, int, int]:
    devices = input_devices()
    preferred = wanted or UserSettings.load().mic or ""
    for d in devices:
        if preferred and preferred.lower() in d[1].lower():
            return d
    for d in devices:  # the built-in array is usually the real mic on a laptop
        if "array" in d[1].lower():
            return d
    return devices[0]


class Recorder:
    """One mic stream for the whole session; record() grabs the next N seconds."""

    def __init__(self, index: int, channels: int, rate: int) -> None:
        self.rate = rate
        self._lock = threading.Lock()
        self._chunks: list[np.ndarray] | None = None
        self.stream = sd.InputStream(
            device=index, samplerate=rate, channels=channels, dtype="float32", callback=self._cb
        )
        self.stream.start()
        time.sleep(1.0)  # let the mic wake up before the first prompt

    def _cb(self, indata, frames, t, status) -> None:
        with self._lock:
            if self._chunks is not None:
                self._chunks.append(indata.copy())

    def record(self, seconds: float) -> np.ndarray:
        with self._lock:
            self._chunks = []
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            left = end - time.monotonic()
            print(f"\r   recording... {left:4.1f} s ", end="", flush=True)
            time.sleep(min(0.1, max(left, 0)))
        with self._lock:
            chunks, self._chunks = self._chunks, None
        print("\r" + " " * 30 + "\r", end="")
        raw = np.concatenate(chunks) if chunks else np.zeros((0, 1), np.float32)
        self.last_raw = raw  # the mic's own format (e.g. 48 kHz stereo), for pipeline tests
        return to_16k_mono(raw, self.rate)


def save_wav(path: Path, audio: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())


def run_set(name: str, rec: Recorder, mic: str, folder: str = "") -> None:
    prompts = SETS[name]
    out = user_data_dir() / "voice_test"
    if folder or name != "default":
        out = out / (folder or name)
    out.mkdir(parents=True, exist_ok=True)
    print(f"\n=== {name}: {len(prompts)} clips -> {out}\n")
    meta: list[dict] = []
    i = 0
    while i < len(prompts):
        text, seconds, hint = prompts[i]
        hint_s = f"  [{hint}]" if hint else ""
        answer = input(f'[{i + 1}/{len(prompts)}] Enter, then say: "{text}"{hint_s}  ')
        if answer.strip().lower() == "r" and meta:
            i -= 1
            meta.pop()
            print("   redoing the previous one")
            continue
        audio = rec.record(seconds)
        level = float(np.sqrt(np.mean(audio**2))) if len(audio) else 0.0
        path = out / f"{i + 1:02d}.wav"
        save_wav(path, audio)
        np.save(path.with_suffix(".raw.npy"), rec.last_raw)  # unprocessed, for comparisons
        meta.append({"file": path.name, "text": text, "style": hint, "rms": round(level, 5),
                     "raw": path.with_suffix(".raw.npy").name, "rate": rec.rate})  # fmt: skip
        quiet = "  - very quiet! (r + Enter to redo)" if level < 0.003 else ""
        print(f"   saved {path.name} (loudness {level:.4f}){quiet}")
        i += 1
    (out / "sentences.json").write_text(
        json.dumps({"mic": mic, "set": name, "clips": meta}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--set", default="default", choices=[*SETS, "all"])
    ap.add_argument("--out", default="", help="folder name under voice_test (default: the set)")
    ap.add_argument("--mic", default="", help="part of the microphone's name, e.g. headset")
    args = ap.parse_args()
    index, mic, channels, rate = pick_mic(args.mic)
    print(f"Microphone: {mic}")
    rec = Recorder(index, channels, rate)
    try:
        for name in ("wake", "talk", "hinglish") if args.set == "all" else (args.set,):
            run_set(name, rec, mic, args.out if args.set != "all" else "")
    finally:
        rec.stream.stop()
        rec.stream.close()
    print("\nDone! The recordings are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
