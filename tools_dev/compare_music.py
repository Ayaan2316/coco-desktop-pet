r"""Score song requests ("Hey Coco, play ...") against the expected tool and song name.

.venv\Scripts\python tools_dev\record_voice_test.py --set music
.venv\Scripts\python tools_dev\compare_music.py
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

import coco.tools  # noqa: E402, F401 - registers the tool phrases
from coco.behaviour.commands import parse_command  # noqa: E402
from coco.core.config import load_config  # noqa: E402
from coco.core.paths import user_data_dir  # noqa: E402
from coco.tools.intents import parse_task  # noqa: E402
from coco.voice import stt  # noqa: E402
from coco.voice.music_words import MusicWords, music_pick  # noqa: E402
from coco.voice.wake import WakeDetector, WakeFlow, strip_wake_word  # noqa: E402
from tools_dev.compare_ears import load_clips  # noqa: E402

TOOLS = ["spotify", "youtube", "open_app", "web_search", "media_control", "open_file"]


def route(text: str):
    """What she'd do: ("cmd", ...) for a cat command, else (tool, args) or None."""
    text = text.strip()
    cmd = parse_command(text)
    if cmd:
        return ("cmd", str(cmd))
    call = parse_task(text, TOOLS)
    return (call.tool, call.args) if call else None


def squeeze(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "", s.lower())
    return re.sub(r"([a-z])\1+", r"\1", s).replace("ee", "i").replace("aa", "a")


def same(want, got) -> bool:
    if want is None or got is None:
        return want == got
    if want[0] != got[0]:
        return False
    if want[0] == "cmd":
        return want == got
    w, g = dict(want[1]), dict(got[1])
    wq, gq = w.pop("text", ""), g.pop("text", "")
    if w != g:
        return False
    return difflib.SequenceMatcher(None, squeeze(wq), squeeze(gq)).ratio() >= 0.8


def after_wake(detector: WakeDetector, cfg, audio: np.ndarray) -> np.ndarray | None:
    """The audio the real wake flow would transcribe (None: the wake word wasn't found)."""
    got: list[np.ndarray] = []

    def hear(a: np.ndarray) -> str:
        got.append(a)
        return "something"

    bus = type("Bus", (), {"post": lambda self, e: None})()
    flow = WakeFlow(cfg, bus, detector, hear)
    detector.reset()
    noise = np.random.default_rng(0).normal(0, 0.002, int(2.5 * stt.RATE)).astype(np.float32)
    data = np.concatenate([noise, audio, noise, noise])
    for i in range(0, len(data), stt.RATE // 20):
        flow.process(data[i : i + stt.RATE // 20])
        if got:
            return got[0]
    return None


def main() -> int:
    from faster_whisper import WhisperModel

    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="print every clip, not just misses")
    ap.add_argument(
        "--sets",
        nargs="+",
        default=["music", "music_2", "music_earphones"],
        help="recorded folders under voice_test (record_voice_test.py --out)",
    )
    ap.add_argument("--quick", action="store_true", help="only Coco's way vs the old way")
    args = ap.parse_args()

    base = user_data_dir() / "voice_test"
    clips = []
    for folder in args.sets:
        if (base / folder / "sentences.json").exists():
            clips += load_clips(base / folder)
    clips += [(t, a) for t, a in load_clips(base / "wake") if t.startswith("Hey Coco, ")]
    cfg = load_config()
    detector = WakeDetector.load()
    stt.enable_cuda_dlls()
    root = str(user_data_dir() / "models" / "whisper")
    english = WhisperModel(cfg.stt.model, device="cuda", compute_type="int8_float16",
                           download_root=root)  # fmt: skip
    hpath = Path(cfg.stt.hinglish_model)
    hpath = hpath if hpath.is_absolute() else Path(root) / hpath
    hinglish = WhisperModel(str(hpath), device="cuda", compute_type="int8_float16")

    cut = []
    for ref, audio in clips:
        a = after_wake(detector, cfg.wake, audio)
        if a is None:
            print(f"  (wake word not found in {ref!r}: skipped)")
            continue
        cut.append((ref, stt.boost(a)))
    print(f"{len(cut)} clips\n")

    def run(model, audio, prompt, beam=1):
        segs, _ = model.transcribe(audio, language="en", beam_size=beam,
                                   without_timestamps=True, vad_filter=True,
                                   initial_prompt=prompt)  # fmt: skip
        segs = list(segs)
        text = stt.clean_transcript(" ".join(s.text for s in segs))
        conf = float(np.mean([s.avg_logprob for s in segs])) if segs else -9.0
        return text, conf

    vp, hp = stt.vocabulary_prompt(), stt.vocabulary_prompt() + " " + stt.HINGLISH_PROMPT
    words = MusicWords()  # your real list: built-in + learned + yours from the dashboard

    def coco_now(a):
        e, h = run(english, a, vp), run(hinglish, a, hp)
        return music_pick(e[0], stt.fix_hinglish_name(h[0]), words) or stt.pick_transcript(e, h)

    modes = {
        "Coco now (+ music names)": coco_now,
        "before music names": lambda a: stt.pick_transcript(run(english, a, vp),
                                                            run(hinglish, a, hp)),
        "english, hints": lambda a: run(english, a, vp)[0],
        "english, no hints": lambda a: run(english, a, None)[0],
        "hinglish, hints": lambda a: stt.fix_hinglish_name(run(hinglish, a, hp)[0]),
        "hinglish, no hints": lambda a: stt.fix_hinglish_name(run(hinglish, a, None)[0]),
        "no hints, pick": lambda a: stt.pick_transcript(run(english, a, None),
                                                        run(hinglish, a, None)),
        "no hints, beam 5, pick": lambda a: stt.pick_transcript(run(english, a, None, 5),
                                                                run(hinglish, a, None, 5)),
    }  # fmt: skip
    if args.quick:
        modes = dict(list(modes.items())[:2])
    for name, fn in modes.items():
        ok, t, misses = 0, [], []
        for ref, audio in cut:
            t0 = time.perf_counter()
            heard = strip_wake_word(fn(audio))
            t.append(time.perf_counter() - t0)
            want, got = route(ref.removeprefix("Hey Coco, ")), route(heard)
            good = same(want, got)
            ok += good
            if not good or args.show:
                misses.append(f"    {'ok ' if good else 'BAD'} {ref[10:]!r} -> {heard!r}")
        print(f"=== {name}: {ok}/{len(cut)} right, {np.mean(t):.2f} s per clip")
        print("\n".join(misses))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
