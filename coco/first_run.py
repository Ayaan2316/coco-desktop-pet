"""First-run checks: Ollama, the models and the voice files."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

OLLAMA_PAGE = "https://ollama.com/download"
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_FILES = {"kokoro-v1.0.onnx": 325_532_387, "voices-v1.0.bin": 28_214_398}

Progress = Callable[[float], None]  # 0..1


@dataclass(frozen=True)
class Need:
    key: str  # "ollama" | "brain" | "memory" | "voice"
    question: str
    model: str = ""


def has_model(names: Iterable[str], model: str) -> bool:
    want = model if ":" in model else f"{model}:latest"
    return any(n in (want, model) for n in names)


def missing(
    *,
    ollama_installed: bool,
    ollama_up: bool,
    models: Iterable[str] | None,
    brain_model: str,
    memory_model: str | None,
    voice_ok: bool,
    skipped: Iterable[str] = (),
) -> list[Need]:
    """What's missing, in the order she asks. `models` = what Ollama has (None: unknown)."""
    skip = set(skipped)
    needs: list[Need] = []
    if not ollama_installed:
        needs.append(Need("ollama", "I need Ollama to think. Open its download page?"))
    elif ollama_up and models is not None:
        names = list(models)
        if not has_model(names, brain_model):
            needs.append(Need("brain", f"Download my brain ({brain_model}, about 6 GB)?",
                              brain_model))  # fmt: skip
        if memory_model and not has_model(names, memory_model):
            needs.append(Need("memory", "Download my memory model (about 640 MB)?",
                              memory_model))  # fmt: skip
    if not voice_ok:
        needs.append(Need("voice", "Download my voice (about 350 MB)?"))
    return [n for n in needs if n.key not in skip]


# --- remembered "No"s
def load_skipped(path: Path) -> list[str]:
    try:
        return list(json.loads(path.read_text(encoding="utf-8")).get("skipped", []))
    except (OSError, ValueError, AttributeError):
        return []


def skip(path: Path, key: str) -> None:
    keys = load_skipped(path)
    if key not in keys:
        keys.append(key)
    path.write_text(json.dumps({"skipped": keys}), encoding="utf-8")


# --- doing it
def ollama_models(host: str) -> list[str] | None:
    try:
        import ollama

        return [m.model for m in ollama.Client(host=host).list().models]
    except Exception as exc:  # noqa: BLE001 - can't tell: ask nothing about models
        log.info("Couldn't list Ollama's models: %s", exc)
        return None


def pull_model(host: str, model: str, progress: Progress) -> None:
    """Ollama downloads the model (it resumes if interrupted). Raises on failure."""
    import ollama

    for part in ollama.Client(host=host).pull(model, stream=True):
        if part.total:
            progress(min(1.0, (part.completed or 0) / part.total))
    progress(1.0)


def download_voice(dest: Path, progress: Progress, get: Callable | None = None) -> None:
    """The two Kokoro files, each into a .part file first (never a half file in place)."""
    dest.mkdir(parents=True, exist_ok=True)
    total = sum(KOKORO_FILES.values())
    done = 0
    for name, size in KOKORO_FILES.items():
        target = dest / name
        if target.exists() and target.stat().st_size == size:
            done += size
            continue
        part = target.with_suffix(target.suffix + ".part")
        with _stream(KOKORO_URL + name, get) as chunks, part.open("wb") as fh:
            last = 0.0
            for chunk in chunks:
                fh.write(chunk)
                done += len(chunk)
                if time.monotonic() - last > 0.5:
                    progress(done / total)
                    last = time.monotonic()
        if part.stat().st_size != size:
            part.unlink(missing_ok=True)
            raise OSError(f"{name}: download incomplete")
        part.replace(target)
    progress(1.0)


def _stream(url: str, get: Callable | None):
    if get is not None:  # tests
        return get(url)
    import contextlib

    import httpx

    @contextlib.contextmanager
    def chunks():
        with httpx.stream("GET", url, follow_redirects=True, timeout=30) as r:
            r.raise_for_status()
            yield r.iter_bytes(1 << 20)

    return chunks()
