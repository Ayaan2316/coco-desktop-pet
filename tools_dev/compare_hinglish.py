r"""Compare Whisper decoding settings on Hinglish and English clips.

.venv\Scripts\python tools_dev\record_voice_test.py --set hinglish
.venv\Scripts\python tools_dev\compare_hinglish.py
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from coco.behaviour.commands import parse_command  # noqa: E402
from coco.core.hindi import to_roman  # noqa: E402
from coco.core.paths import user_data_dir  # noqa: E402
from coco.voice.stt import (  # noqa: E402
    HINGLISH_PROMPT,
    boost,
    clean_transcript,
    enable_cuda_dlls,
    vocabulary_prompt,
)
from tools_dev.compare_ears import load_clips, wer  # noqa: E402

DEVANAGARI_PROMPT = "कोको एक बिल्ली है। कोको, सो जाओ। इधर आओ। रुको। भागो। मेरे साथ खेलो।"


def squeeze(text: str) -> str:
    return re.sub(r"([aeiou])\1+", r"\1", to_roman(text).lower())


def run(model, audio, language, prompt):
    segs, _ = model.transcribe(
        boost(audio),
        language=language,
        beam_size=1,
        without_timestamps=True,
        vad_filter=True,
        initial_prompt=prompt,
    )
    return clean_transcript(" ".join(s.text for s in segs))


def detect(model, audio) -> str:
    """English vs Hindi only (free detection guesses wild languages on short clips)."""
    _lang, _p, probs = model.detect_language(boost(audio))
    p = dict(probs)
    return "hi" if p.get("hi", 0) + p.get("ur", 0) > p.get("en", 0) else "en"


def main() -> int:
    from faster_whisper import WhisperModel

    base = user_data_dir() / "voice_test"
    hinglish = load_clips(base / "hinglish")
    english = load_clips(base)
    wake = [(t, a) for t, a in load_clips(base / "wake") if "," in t]  # "Hey Coco, ..."
    enable_cuda_dlls()
    model = WhisperModel(
        "large-v3-turbo",
        device="cuda",
        compute_type="int8_float16",
        download_root=str(user_data_dir() / "models" / "whisper"),
    )
    model.transcribe(np.zeros(16000, np.float32), language="en")

    modes = {
        "en + English hints": lambda a: run(model, a, "en", vocabulary_prompt()),
        "en + Hinglish hints": lambda a: run(model, a, "en", HINGLISH_PROMPT),
        "en + both hints": lambda a: run(
            model, a, "en", vocabulary_prompt() + " " + HINGLISH_PROMPT
        ),
        "hi + Devanagari hints": lambda a: run(model, a, "hi", DEVANAGARI_PROMPT),
        "hi + Hinglish hints": lambda a: run(model, a, "hi", HINGLISH_PROMPT),
        "auto en/hi": lambda a: (
            run(model, a, "en", vocabulary_prompt() + " " + HINGLISH_PROMPT)
            if detect(model, a) == "en"
            else run(model, a, "hi", HINGLISH_PROMPT)
        ),
    }
    for name, fn in modes.items():
        print(f"\n=== {name}")
        for label, clips in (("hinglish", hinglish), ("english", english), ("wake+cmd", wake)):
            errs, cmd_ok, cmd_n, t = [], 0, 0, []
            wrong = []
            for ref, audio in clips:
                t0 = time.perf_counter()
                hyp = fn(audio)
                t.append(time.perf_counter() - t0)
                e = wer(squeeze(ref), squeeze(hyp))
                errs.append(e)
                want = parse_command(ref.replace("Hey Coco, ", "Coco, "))
                if want:
                    cmd_n += 1
                    got = parse_command(re.sub(r"^(hey,? )?coco,? ", "Coco, ", hyp, flags=re.I))
                    cmd_ok += got == want
                if e > 0:
                    wrong.append(f"{ref!r} -> {hyp!r}")
            print(
                f"  {label:9s} word errors {np.mean(errs) * 100:5.1f}%  commands {cmd_ok}/{cmd_n}"
                f"  {np.mean(t):.2f} s/clip"
            )
            if label == "hinglish":
                for w in wrong[:15]:
                    print("      ", w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
