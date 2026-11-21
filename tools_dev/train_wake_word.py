r"""Train the "Hey Coco" wake word locally.

    .venv\Scripts\python tools_dev\record_voice_test.py --set all
    .venv\Scripts\python tools_dev\train_wake_word.py

Positives: your clips plus synthetic ones from Kokoro. Negatives: your normal speech,
similar-sounding words and other sentences. Some of your clips are held out for testing.
The model and its threshold go to %LOCALAPPDATA%\Coco\models\wake\hey_coco.npz.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from coco.core.paths import user_data_dir  # noqa: E402
from coco.voice.stt import RATE, to_16k_mono  # noqa: E402
from coco.voice.wake import WakeDetector, WakeNet, feature_extractor, wake_dir  # noqa: E402
from tools_dev.compare_ears import load_clips  # noqa: E402

CLIP = 33280  # 2.08 s: exactly 16 openWakeWord embeddings
FRAMES = 16
SEED = 7

POSITIVE_TEXTS = ["Hey Coco", "Hey Coco!", "Hey, Coco.", "Hey Coco?", "Hey, Coco!"]
NEAR_MISSES = [
    "Hey cocoa", "cuckoo", "taco", "Hey Google", "okay go", "coffee", "go go", "hello",
    "Hey Siri", "Hey Cortana", "Hey Jarvis", "poco", "loco", "rococo", "cocoa butter",
    "go home", "hey, go", "hey you", "hey there", "local", "yoyo", "hey Kiko", "hey cool",
    "okay cool", "hey Cody", "Coca-Cola", "hey Bobo", "hey yo", "hey, hold on", "hey Kuku",
]  # fmt: skip
SENTENCES = [
    "Can you send me the notes from today's lecture?",
    "I think the circuit needs a pull-up resistor.",
    "Let's grab some food after class.", "The exam is on Monday morning at nine.",
    "My code keeps crashing when I read from the serial port.", "Turn on the lights please.",
    "I'll call you back in five minutes.", "What time does the library close today?",
    "This transistor is getting really hot.", "Did you finish the assignment yet?",
    "Okay, so first we measure the voltage across the capacitor.", "Hello, can you hear me?",
    "I was going to go home but it started raining.", "Hey, how was your weekend?",
    "The microcontroller has two timers and an ADC.", "Play some music, I'm studying.",
    "Kal class mein kya hua tha?", "Mera project abhi tak complete nahi hua.",
    "Yaar, aaj bahut thak gaya hoon.", "Chalo, kuch khana order karte hain.",
    "Go to the settings and open bluetooth.", "Coffee or tea? I need caffeine.",
    "My cat is sleeping on the keyboard again.", "That cocoa was delicious.",
    "Hey, can you pass me the charger?", "Okay Google, what's the weather like?",
    "The cuckoo clock in my grandma's house is so loud.", "We need to go over the code again.",
    "Good morning everyone, let's get started.", "I'm going to the market, need anything?",
]  # fmt: skip
# Held-out wake clips for the test (2 of each style + 2 commands)
HELD_OUT_WAKE = {7, 8, 13, 14, 19, 20, 24, 25, 29, 30, 39, 40}


# --- synthesis
def kokoro(threads: int = 0):
    """Kokoro TTS. threads > 0 makes a small ONNX session; several in parallel are faster
    than one big one for short phrases."""
    from kokoro_onnx import Kokoro

    d = user_data_dir() / "models" / "kokoro"
    if not threads:
        return Kokoro(str(d / "kokoro-v1.0.onnx"), str(d / "voices-v1.0.bin"))
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = threads
    opts.inter_op_num_threads = 1
    session = ort.InferenceSession(
        str(d / "kokoro-v1.0.onnx"), opts, providers=["CPUExecutionProvider"]
    )
    return Kokoro.from_session(session, str(d / "voices-v1.0.bin"))


_WORKER_TTS = None


def _worker_init() -> None:
    global _WORKER_TTS
    _WORKER_TTS = kokoro(threads=2)


def _worker_synth(job: tuple[str, str, float]) -> np.ndarray | None:
    text, voice, speed = job
    lang = "en-gb" if voice.startswith("b") else "en-us"
    try:
        samples, sr = _WORKER_TTS.create(text, voice=voice, speed=speed, lang=lang)
    except Exception:  # noqa: BLE001 - a voice that can't say it: skip
        return None
    return to_16k_mono(np.asarray(samples, np.float32), sr)


def all_voices() -> list[str]:
    v = np.load(str(user_data_dir() / "models" / "kokoro" / "voices-v1.0.bin"))
    return sorted(v.files)


def synth_set(pool, name: str, jobs: list[tuple[str, str, float]], cache: Path) -> list:
    """Synthesize (text, voice, speed) jobs in parallel; cached as <name>.npz."""
    path = cache / f"{name}.npz"
    if path.exists():
        data = np.load(path, allow_pickle=False)
        return [data[key] for key in data.files]
    out, t0 = [], time.perf_counter()
    for i, audio in enumerate(pool.imap(_worker_synth, jobs, chunksize=4)):
        if audio is not None:
            out.append(audio)
        if i % 200 == 0:
            print(f"   {name}: {i}/{len(jobs)} ({time.perf_counter() - t0:.0f} s)", flush=True)
    np.savez(path, *out)
    print(f"   {name}: {len(out)} clips in {time.perf_counter() - t0:.0f} s", flush=True)
    return out


def jobs_for(texts, voices, speeds, every: int = 1) -> list[tuple[str, str, float]]:
    """All (text, voice, speed) combinations; every=2 -> each text in every other voice."""
    out = []
    for ti, t in enumerate(texts):
        for vi, v in enumerate(voices):
            if (vi + ti) % every:
                continue
            out.extend((t, v, s) for s in speeds)
    return out


# --- audio tools
def trim(audio: np.ndarray, rel: float = 0.08) -> np.ndarray:
    """Cut silence around speech (by loudness in 10 ms frames)."""
    if len(audio) < 320:
        return audio
    frames = audio[: len(audio) // 160 * 160].reshape(-1, 160)
    level = np.sqrt((frames**2).mean(axis=1))
    loud = np.where(level > max(level.max() * rel, 1e-4))[0]
    if len(loud) == 0:
        return audio[:0]
    return audio[max(loud[0] - 3, 0) * 160 : min(loud[-1] + 4, len(level)) * 160]


def band(audio: np.ndarray, rng) -> np.ndarray:
    """A laptop-mic-ish tone: roll off lows and highs by random amounts (FFT filter)."""
    spec = np.fft.rfft(audio)
    f = np.fft.rfftfreq(len(audio), 1 / RATE)
    lo, hi = rng.uniform(60, 250), rng.uniform(3500, 7800)
    gain = 1 / (1 + (lo / np.maximum(f, 1)) ** 2) / (1 + (f / hi) ** 4)
    return np.fft.irfft(spec * gain, len(audio)).astype(np.float32)


def reverb(audio: np.ndarray, rng) -> np.ndarray:
    if rng.random() < 0.35:
        return audio
    n = int(RATE * rng.uniform(0.05, 0.35))
    ir = rng.standard_normal(n) * np.exp(-np.linspace(0, rng.uniform(4, 9), n))
    ir[0] = 1.0
    wet = np.convolve(audio, ir * rng.uniform(0.05, 0.25))[: len(audio)]
    return (audio + wet).astype(np.float32)


def noise(n: int, rng) -> np.ndarray:
    white = rng.standard_normal(n).astype(np.float32)
    if rng.random() < 0.5:  # pinkish: smoothed
        white = np.convolve(white, np.ones(8) / 8, mode="same").astype(np.float32)
    return white


def place(speech: np.ndarray, rng, bg: np.ndarray | None, end_gap=(0.03, 0.45)) -> np.ndarray:
    """A CLIP-long example with `speech` ending near the end (that's when the score
    should fire), on top of background (noise and/or other audio)."""
    out = np.zeros(CLIP, np.float32)
    speech = speech[-(CLIP - 400) :]
    end = CLIP - int(rng.uniform(*end_gap) * RATE)
    start = max(end - len(speech), 0)
    out[start : start + len(speech)] = speech[: CLIP - start]
    return mix(out, rng, bg)


def mix(clip: np.ndarray, rng, bg: np.ndarray | None) -> np.ndarray:
    clip = reverb(band(clip, rng), rng)
    peak = np.abs(clip).max() or 1.0
    clip = clip / peak * rng.uniform(0.05, 0.9)  # near and far, loud and soft
    level = np.sqrt((clip**2).mean()) or 1e-3
    clip = clip + noise(CLIP, rng) * level * 10 ** (-rng.uniform(10, 40) / 20)
    if bg is not None and len(bg) > CLIP and rng.random() < 0.4:
        s = rng.integers(0, len(bg) - CLIP)
        b = bg[s : s + CLIP]
        bl = np.sqrt((b**2).mean()) or 1e-3
        clip = clip + b / bl * level * 10 ** (-rng.uniform(8, 25) / 20)
    return np.clip(clip, -1, 1).astype(np.float32)


def crops(audio: np.ndarray, step: float = 0.5) -> list[np.ndarray]:
    hop = int(step * RATE)
    return [audio[s : s + CLIP] for s in range(0, max(len(audio) - CLIP, 0) + 1, hop)
            if len(audio[s : s + CLIP]) == CLIP]  # fmt: skip


def to_int16(clips: list[np.ndarray]) -> np.ndarray:
    return (np.clip(np.stack(clips), -1, 1) * 32767).astype(np.int16)


# --- evaluation
def stream_scores(net: WakeNet, audio: np.ndarray) -> np.ndarray:
    det = WakeDetector(net)
    pad = np.zeros(RATE * 2, np.float32)
    return np.array(det.feed(np.concatenate([pad, audio, np.zeros(RATE, np.float32)])))


def count_triggers(scores: np.ndarray, thr: float, refractory: int = 25) -> int:
    n, last = 0, -(10**9)
    for i, s in enumerate(scores):
        if s >= thr and i - last > refractory:
            n, last = n + 1, i
    return n


# --- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true", help="re-synthesize the TTS clips")
    args = ap.parse_args()
    rng = np.random.default_rng(SEED)
    random.seed(SEED)
    cache = user_data_dir() / "wake_train"
    cache.mkdir(exist_ok=True)
    if args.fresh:
        for f in cache.glob("*.npz"):
            f.unlink()
    base = user_data_dir() / "voice_test"
    t_start = time.perf_counter()

    # ---- your recordings
    wake = load_clips(base / "wake")
    talk = [a for _t, a in load_clips(base / "talk")]
    hinglish = load_clips(base / "hinglish")
    default = load_clips(base)
    real_pos_train = [trim(a) for i, (t, a) in enumerate(wake, 1)
                      if i not in HELD_OUT_WAKE and "," not in t]  # fmt: skip
    real_pos_test = [(t, a) for i, (t, a) in enumerate(wake, 1) if i in HELD_OUT_WAKE]
    talk_train = [a[: int(len(a) * 0.7)] for a in talk]
    talk_test = [a[int(len(a) * 0.7) :] for a in talk]
    bg_audio = np.concatenate(talk_train) if talk_train else None
    print(
        f"Your clips: {len(real_pos_train)} wake (train), {len(real_pos_test)} wake (test), "
        f"{sum(len(a) for a in talk) / RATE:.0f} s of talk"
    )

    # ---- synthetic speech (cached)
    voices = all_voices()
    test_voices = voices[::9]  # every 9th voice is never trained on
    train_voices = [v for v in voices if v not in test_voices]
    print(f"Kokoro voices: {len(train_voices)} train, {len(test_voices)} test")
    import multiprocessing as mp

    with mp.Pool(4, initializer=_worker_init) as pool:
        pos_tr = synth_set(
            pool, "pos_train", jobs_for(POSITIVE_TEXTS, train_voices, [0.85, 1.0, 1.2]), cache
        )
        pos_te = synth_set(
            pool, "pos_test", jobs_for(POSITIVE_TEXTS[:2], test_voices, [0.9, 1.1]), cache
        )
        near_tr = synth_set(
            pool, "near_train", jobs_for(NEAR_MISSES, train_voices, [1.0], every=2), cache
        )
        sent_tr = synth_set(
            pool,
            "sent_train",
            jobs_for(SENTENCES[::2] + SENTENCES[1::4], train_voices, [1.0], every=4),
            cache,
        )
        near_te = synth_set(
            pool, "near_test", jobs_for(NEAR_MISSES, test_voices, [1.0], every=2), cache
        )
        sent_te = synth_set(pool, "sent_test", jobs_for(SENTENCES[3::4], test_voices, [1.0]), cache)
    print(
        f"Synth: {len(pos_tr)} positives, {len(near_tr)} near-misses, {len(sent_tr)} "
        f"sentences ({time.perf_counter() - t_start:.0f} s so far)"
    )

    # ---- build training examples
    pos, neg = [], []
    for a in pos_tr:
        speech = trim(a)
        for _ in range(2):
            pos.append(place(speech, rng, bg_audio))
    for a in real_pos_train:
        for _ in range(25):  # your voice counts a lot
            pos.append(place(a, rng, bg_audio))
    for a in near_tr:
        neg.append(place(trim(a), rng, bg_audio))
        if rng.random() < 0.5:
            neg.append(place(trim(a), rng, bg_audio, end_gap=(0.3, 1.0)))
    for a in sent_tr:
        for c in crops(np.concatenate([np.zeros(RATE // 2, np.float32), a]), step=0.4):
            neg.append(mix(c, rng, bg_audio))
    for a in talk_train:
        for c in crops(a, step=0.25):
            neg.append(mix(c, rng, None))
            neg.append(mix(c, rng, None))
    for _ in range(600):  # silence / room noise only
        neg.append(mix(np.zeros(CLIP, np.float32), rng, bg_audio))
    # "Hey Coco" cut off before the end, or long finished: shouldn't fire (yet / again)
    for a in pos_tr[::3]:
        s = trim(a)
        neg.append(place(s[: int(len(s) * rng.uniform(0.3, 0.6))], rng, bg_audio))
    print(f"Training examples: {len(pos)} positive, {len(neg)} negative")

    feats = feature_extractor()
    t0 = time.perf_counter()
    xp = feats.embed_clips(to_int16(pos), batch_size=256)[:, -FRAMES:]
    xn = feats.embed_clips(to_int16(neg), batch_size=256)[:, -FRAMES:]
    print(f"Features: {xp.shape} / {xn.shape} in {time.perf_counter() - t0:.0f} s")

    # ---- train (scikit-learn MLP; exported to plain numpy for Coco)
    from sklearn.neural_network import MLPClassifier

    x = np.concatenate([xp, xn]).reshape(len(xp) + len(xn), -1)
    y = np.concatenate([np.ones(len(xp)), np.zeros(len(xn))])
    mean, std = x.mean(axis=0), x.std(axis=0) + 1e-3
    clf = MLPClassifier(
        hidden_layer_sizes=(64, 32),
        alpha=1e-3,
        batch_size=256,
        learning_rate_init=1e-3,
        max_iter=60,
        early_stopping=True,
        validation_fraction=0.1,
        n_iter_no_change=8,
        random_state=SEED,
    )
    t0 = time.perf_counter()
    clf.fit((x - mean) / std, y)
    print(
        f"Trained in {time.perf_counter() - t0:.0f} s ({clf.n_iter_} epochs, "
        f"val acc {max(clf.validation_scores_):.4f})"
    )
    layers = [(w.astype(np.float32), b.astype(np.float32))
              for w, b in zip(clf.coefs_, clf.intercepts_, strict=True)]  # fmt: skip
    net = WakeNet(layers, mean.astype(np.float32), std.astype(np.float32), FRAMES, 0.5)

    # ---- test: streaming, on held-out audio
    pos_scores = [(t, stream_scores(net, a).max()) for t, a in real_pos_test]
    syn_scores = [stream_scores(net, trim(a)).max() for a in pos_te]
    neg_streams = {
        "your talk (held back)": np.concatenate(talk_test),
        "near-misses (new voices)": np.concatenate(
            [np.concatenate([trim(a), np.zeros(RATE // 2, np.float32)]) for a in near_te]
        ),
        "sentences (new voices)": np.concatenate(sent_te),
        "your English clips": np.concatenate([a for t, a in default if "Hey" not in t]),
    }
    neg_scores = {name: stream_scores(net, a) for name, a in neg_streams.items()}
    neg_seconds = {name: len(a) / RATE for name, a in neg_streams.items()}
    hing_scores = [(t, stream_scores(net, a).max()) for t, a in hinglish]

    total_s = sum(neg_seconds.values())
    print(
        f"\nTest audio: {len(pos_scores)} of your Hey Coco clips, {len(syn_scores)} new-voice "
        f"Hey Coco, {total_s / 60:.1f} min of other speech"
    )
    print(" thresh | your misses | new-voice misses | false wakes (per hour)")
    best = None
    for thr in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 0.98):
        miss = sum(s < thr for _t, s in pos_scores)
        smiss = sum(s < thr for s in syn_scores)
        fw = sum(count_triggers(s, thr) for s in neg_scores.values())
        per_hour = fw / total_s * 3600
        print(
            f"  {thr:4.2f}  |   {miss:2d}/{len(pos_scores)}      |     {smiss:3d}/{len(syn_scores)}"
            f"       | {fw:3d}  ({per_hour:5.1f}/h)"
        )
        if per_hour <= 1.0 and (best is None or miss < best[1]):
            best = (thr, miss)
    thr = best[0] if best else 0.9
    net.threshold = thr
    print(f"\nChosen threshold {thr} (fewest misses with <= 1 false wake per hour)")
    for name, s in neg_scores.items():
        print(
            f"   {name:26s} {neg_seconds[name]:5.0f} s  false wakes: {count_triggers(s, thr)}"
            f"  (max score {s.max():.2f})"
        )
    print("   your clips at that threshold:")
    for t, s in pos_scores:
        print(f"      {'ok  ' if s >= thr else 'MISS'} {s:.2f}  {t}")
    print("   Hinglish clips ('Coco, ...' without 'hey': waking is optional):")
    for t, s in hing_scores[:7]:
        print(f"      {s:.2f}  {t}")

    out = wake_dir() / "hey_coco.npz"
    net.save(out)
    report = {
        "threshold": float(thr),
        "your_misses": int(sum(s < thr for _t, s in pos_scores)),
        "your_clips": len(pos_scores),
        "false_wakes_per_hour": float(sum(count_triggers(s, thr) for s in neg_scores.values()))
        / total_s
        * 3600,
        "test_minutes": total_s / 60,
    }
    (wake_dir() / "hey_coco_report.json").write_text(json.dumps(report, indent=2), "utf-8")
    print(f"\nSaved {out} ({time.perf_counter() - t_start:.0f} s total)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
