"""What Coco brings up when she starts: Ollama, her dashboard, Tailscale's tray app."""

from coco.core.companions import bring_up


def run(**kw):
    started = []
    defaults = dict(
        hub=True,
        tailscale=True,
        ensure_ollama=lambda: True,
        hub_up=lambda: False,
        start_hub_=lambda: started.append("dashboard"),
        tailscale_installed=lambda: True,
        tailscale_running=lambda: False,
        start_tailscale=lambda: started.append("tailscale"),
    )
    defaults.update(kw)
    return bring_up(**defaults), started


def test_starts_what_isnt_running():
    done, started = run()
    assert started == ["tailscale", "dashboard"]
    assert done == {"tailscale": "started", "dashboard": "started", "ollama": "up"}


def test_leaves_running_things_alone():
    done, started = run(hub_up=lambda: True, tailscale_running=lambda: True)
    assert started == [] and done["dashboard"] == "running" and done["tailscale"] == "running"


def test_switches_and_missing_tailscale():
    done, started = run(hub=False, tailscale_installed=lambda: False)
    assert started == [] and "dashboard" not in done and "tailscale" not in done


def test_ollama_problems_never_break_her_start():
    def boom():
        raise OSError("no ollama")

    done, _ = run(ensure_ollama=boom)
    assert done["ollama"].startswith("error")
    assert run(ensure_ollama=lambda: False)[0]["ollama"] == "down"
