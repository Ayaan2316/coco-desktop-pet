r"""Compare Whisper models on recorded test clips (word error rate and speed).

.venv\Scripts\python tools_dev\record_voice_test.py
.venv\Scripts\python tools_dev\compare_ears.py
"""

from __future__ import annotations

import json
import re
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from coco.core.paths import user_data_dir  # noqa: E402
from coco.voice.stt import boost, enable_cuda_dlls, vocabulary_prompt  # noqa: E402

MODELS = ["base.en", "small.en", "distil-large-v3", "large-v3-turbo"]


def words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]", "", text.lower().replace("'", "")).split()


def wer(ref: str, hyp: str) -> float:
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / max(len(r), 1)


def load_clips(folder: Path) -> list[tuple[str, np.ndarray]]:
    meta = json.loads((folder / "sentences.json").read_text(encoding="utf-8"))
    clips = []
    for c in meta["clips"]:
        with wave.open(str(folder / c["file"])) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), np.int16)
        clips.append((c["text"], pcm.astype(np.float32) / 32768))
    return clips


def main() -> int:
    from faster_whisper import WhisperModel

    folder = user_data_dir() / "voice_test"
    clips = load_clips(folder)
    root = str(user_data_dir() / "models" / "whisper")
    gpu = enable_cuda_dlls()
    devices = [("cpu", "int8")] + ([("cuda", "int8_float16")] if gpu else [])
    print(f"{len(clips)} clips; GPU available: {gpu}\n")
    for name in MODELS:
        for device, compute in devices:
            try:
                model = WhisperModel(
                    name, device=device, compute_type=compute, cpu_threads=8, download_root=root
                )
            except Exception as exc:  # noqa: BLE001
                print(f"{name:16s} {device}: could not load ({exc})")
                continue
            model.transcribe(np.zeros(16000, np.float32), language="en")  # warm-up
            errs, times, wrong = [], [], []
            for ref, audio in clips:
                t0 = time.perf_counter()
                segs, _ = model.transcribe(
                    boost(audio),
                    language="en",
                    beam_size=1,
                    without_timestamps=True,
                    vad_filter=True,
                    initial_prompt=vocabulary_prompt(),
                )
                hyp = " ".join(s.text for s in segs).strip()
                times.append(time.perf_counter() - t0)
                e = wer(ref, hyp)
                errs.append(e)
                if e > 0:
                    wrong.append(f"{ref!r} -> {hyp!r}")
            print(
                f"{name:16s} {device:4s}: word errors {np.mean(errs) * 100:5.1f}%   "
                f"{np.mean(times):.2f} s/sentence   ({len(wrong)}/{len(clips)} not perfect)"
            )
            for w_ in wrong[:6]:
                print("      ", w_)
            del model
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
