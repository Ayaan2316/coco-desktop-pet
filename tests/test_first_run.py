"""First run on a new PC: what she asks for, what a No remembers, the voice download."""

import contextlib

import pytest

from coco import first_run as fr

ALL = {"ollama_installed": True, "ollama_up": True, "brain_model": "qwen3.5:9b",
       "memory_model": "qwen3-embedding:0.6b", "voice_ok": True}  # fmt: skip


def keys(**kw):
    return [n.key for n in fr.missing(**{**ALL, **kw})]


def test_nothing_asked_when_everything_is_there():
    assert keys(models=["qwen3.5:9b", "qwen3-embedding:0.6b", "llama3:latest"]) == []


def test_what_a_new_pc_is_asked():
    assert keys(models=[], voice_ok=False) == ["brain", "memory", "voice"]
    assert keys(models=[], memory_model=None) == ["brain"]  # memory off: no memory model
    assert keys(ollama_installed=False, ollama_up=False, models=None) == ["ollama"]
    assert keys(ollama_up=False, models=None) == []  # installed, not answering: ask nothing
    assert keys(models=[], skipped=["brain"]) == ["memory"]
    need = fr.missing(**{**ALL, "models": []})[0]
    assert need.model == "qwen3.5:9b" and "6 GB" in need.question


def test_model_names_match_with_and_without_tag():
    assert fr.has_model(["mistral:latest"], "mistral")
    assert fr.has_model(["qwen3.5:9b"], "qwen3.5:9b")
    assert not fr.has_model(["qwen3.5:4b"], "qwen3.5:9b")


def test_a_no_is_remembered(tmp_path):
    path = tmp_path / "first_run.json"
    assert fr.load_skipped(path) == []
    fr.skip(path, "voice")
    fr.skip(path, "voice")
    fr.skip(path, "brain")
    assert fr.load_skipped(path) == ["voice", "brain"]
    path.write_text("not json")
    assert fr.load_skipped(path) == []


def fake_get(sizes):
    @contextlib.contextmanager
    def get(url):
        name = url.rsplit("/", 1)[1]
        n = sizes[name]
        yield iter([b"x" * (n // 2), b"x" * (n - n // 2)])

    return get


def test_voice_download(tmp_path, monkeypatch):
    monkeypatch.setattr(fr, "KOKORO_FILES", {"a.onnx": 1000, "b.bin": 10})
    seen = []
    fr.download_voice(tmp_path / "kokoro", seen.append, get=fake_get({"a.onnx": 1000, "b.bin": 10}))
    assert (tmp_path / "kokoro" / "a.onnx").stat().st_size == 1000
    assert seen[-1] == 1.0 and not list((tmp_path / "kokoro").glob("*.part"))


def test_voice_download_never_leaves_half_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(fr, "KOKORO_FILES", {"a.onnx": 1000})
    with pytest.raises(OSError):
        fr.download_voice(tmp_path, lambda f: None, get=fake_get({"a.onnx": 600}))
    assert list(tmp_path.iterdir()) == []
