"""The keeper: what it brings back, and the Task Scheduler task it runs from."""

import socket
import xml.dom.minidom
from datetime import datetime

from coco import keeper
from coco.core import keeper_task, launch


def world(**kw):
    started = []
    base = dict(
        coco_running=lambda: False, coco_wanted=lambda: True, hub_wanted=lambda: True,
        tailscale_wanted=lambda: True, hub_up=lambda: False, ollama_up=lambda: False,
        tailscale_installed=lambda: True, tailscale_running=lambda: False,
        start_coco=lambda: started.append("coco"), start_hub=lambda: started.append("hub"),
        start_ollama=lambda: started.append("ollama") or True,
        start_tailscale=lambda: started.append("tailscale"),
    )  # fmt: skip
    base.update(kw)
    return keeper.World(**base), started


def test_after_a_failed_shutdown_everything_comes_back(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    w, started = world()
    done = keeper.check(w)
    assert started == ["tailscale", "hub", "ollama", "coco"]
    assert done == {"tailscale": "started", "dashboard": "started", "ollama": "started",
                    "coco": "started"}  # fmt: skip


def test_nothing_twice_when_all_is_running(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    w, started = world(coco_running=lambda: True, hub_up=lambda: True, ollama_up=lambda: True,
                       tailscale_running=lambda: True)  # fmt: skip
    assert keeper.check(w)["coco"] == "running" and started == []


def test_you_turned_her_off_stays_off(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    keeper.note_turned_off(True)
    assert keeper.turned_off_by_you()
    w, started = world(coco_wanted=lambda: False, hub_up=lambda: True,
                       tailscale_running=lambda: True)  # fmt: skip
    done = keeper.check(w)
    assert done["coco"] == "off (you turned her off)" and started == []  # (no Ollama either)
    keeper.note_turned_off(False)
    assert not keeper.turned_off_by_you()


def test_dashboard_and_tailscale_follow_their_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    w, started = world(hub_wanted=lambda: False, tailscale_wanted=lambda: False,
                       coco_running=lambda: True, ollama_up=lambda: True)  # fmt: skip
    keeper.check(w)
    assert started == []


def test_listening():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    port = srv.getsockname()[1]
    assert keeper.listening(port)
    srv.close()
    assert not keeper.listening(port)


def test_task_xml_has_every_trigger_and_the_right_command():
    cmd = launch.launch_command(keeper=True, frozen=True, exe=r"C:\Apps\Coco\Coco.exe")
    assert cmd[-1] == "--keep"
    text = keeper_task.task_xml(cmd, r"C:\Apps\Coco", r"PC\me", now=datetime(2026, 11, 22, 15))
    doc = xml.dom.minidom.parseString(text.replace('encoding="UTF-16"', 'encoding="UTF-8"'))
    names = [n.tagName for n in doc.getElementsByTagName("Triggers")[0].childNodes
             if n.nodeType == n.ELEMENT_NODE]  # fmt: skip
    assert names == ["LogonTrigger", "SessionStateChangeTrigger", "EventTrigger", "TimeTrigger"]
    assert "SessionUnlock" in text and "EventID=507" in text and "PT5M" in text
    assert "<Command>C:\\Apps\\Coco\\Coco.exe</Command><Arguments>--keep</Arguments>" in text
    src = launch.launch_command(keeper=True, frozen=False, exe=r"C:\p\.venv\Scripts\python.exe")
    assert src[0].endswith("pythonw.exe") and src[1].endswith("run_keeper.pyw")


def test_a_start_handed_off_from_a_package_is_honored(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    w, started = world(coco_wanted=lambda: False, hub_wanted=lambda: False,
                       requested=lambda: {"coco", "hub"},
                       tailscale_running=lambda: True)  # fmt: skip
    done = keeper.check(w)
    assert done["coco"] == "started" and done["dashboard"] == "started"
    assert started == ["hub", "ollama", "coco"]


def test_hand_off_and_requests(tmp_path, monkeypatch):
    from coco.core import package_guard

    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    ran = []
    monkeypatch.setattr(keeper_task, "install", lambda: True)
    monkeypatch.setattr(keeper_task, "run_now", lambda: ran.append(1) or True)
    assert package_guard.hand_off("hub") and package_guard.hand_off("coco")
    assert len(ran) == 2
    assert package_guard.take_requests() == {"coco", "hub"}
    assert package_guard.take_requests() == set()  # (asked once, done once)


def test_not_redirected_normally(tmp_path):
    from coco.core import package_guard

    assert package_guard.redirected_to(tmp_path) is None
    assert list((tmp_path / "Coco").iterdir()) == []  # (the probe cleans up after itself)
