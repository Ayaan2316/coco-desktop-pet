"""Ollama server auto-start logic (with fakes: no real processes are started)."""

from coco.brain.ollama_runtime import ensure_server


def test_already_running_does_nothing():
    started = []
    assert ensure_server(
        "h",
        autostart=True,
        is_up=lambda h: True,
        start=lambda: started.append(1) or True,
        sleep=lambda s: None,
    )
    assert started == []


def test_starts_server_and_waits_until_up():
    calls = {"n": 0}

    def is_up(_host):
        calls["n"] += 1
        return calls["n"] >= 4  # up on the 4th check

    started = []
    assert ensure_server(
        "h",
        autostart=True,
        is_up=is_up,
        start=lambda: started.append(1) or True,
        sleep=lambda s: None,
    )
    assert started == [1]


def test_gives_up_after_the_wait():
    assert not ensure_server(
        "h", autostart=True, wait=2, is_up=lambda h: False, start=lambda: True, sleep=lambda s: None
    )


def test_respects_autostart_off_and_missing_ollama():
    assert not ensure_server(
        "h", autostart=False, is_up=lambda h: False, start=lambda: True, sleep=lambda s: None
    )
    assert not ensure_server(
        "h", autostart=True, is_up=lambda h: False, start=lambda: False, sleep=lambda s: None
    )
