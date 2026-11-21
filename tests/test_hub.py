"""Coco's dashboard hub: the PIN, how her status reads, and the hub's link client."""

import json
import os

import pytest

from coco.hub_link import LinkServer
from coco_hub.auth import FIRST_LOCK, FREE_TRIES, PinAuth, valid_pin
from coco_hub.coco_link import CocoLink, process_exe
from coco_hub.view import describe, sprite_for, tile_state


# --- PIN
def test_pin_rules():
    assert valid_pin("482913") is None
    assert valid_pin("12a4") and valid_pin("123") and valid_pin("1" * 13)
    assert valid_pin("1111") and valid_pin("1234") and valid_pin("9876")  # too easy


def test_pin_is_hashed_and_checked(tmp_path):
    auth = PinAuth(tmp_path / "auth.json")
    assert not auth.has_pin and auth.check("482913") == "wrong"
    auth.set_pin("482913")
    raw = (tmp_path / "auth.json").read_text()
    assert "482913" not in raw and json.loads(raw)["hash"]
    assert PinAuth(tmp_path / "auth.json").check("482913") == "ok"  # (survives a restart)
    assert auth.check("000999") == "wrong"


def test_lockout_grows(tmp_path):
    now = [1000.0]
    auth = PinAuth(tmp_path / "auth.json", clock=lambda: now[0])
    auth.set_pin("482913")
    results = [auth.check("000999") for _ in range(FREE_TRIES)]
    assert results[-1] == "locked" and set(results[:-1]) == {"wrong"}
    assert auth.check("482913") == "locked"  # even the right PIN waits
    now[0] += FIRST_LOCK + 1
    for _ in range(FREE_TRIES):
        auth.check("000999")
    assert auth.locked_for() == pytest.approx(2 * FIRST_LOCK)  # doubled
    now[0] += 2 * FIRST_LOCK + 1
    assert auth.check("482913") == "ok"


def test_changing_the_pin_signs_everyone_out(tmp_path):
    auth = PinAuth(tmp_path / "auth.json")
    auth.set_pin("482913")
    v = auth.version
    auth.sign_out_everywhere()
    assert auth.version == v + 1
    auth.set_pin("559031")
    assert auth.version == v + 2
    assert auth.cookie_secret == PinAuth(tmp_path / "auth.json").cookie_secret


# --- how it reads
STATUS = {
    "visible": True, "state": "sleep", "anim": "sleep", "surface": "bed",
    "mood": {"words": "happy, a bit sleepy"}, "voice": True, "wake": None, "paused": False,
    "focus": {"active": True, "working": True, "remaining": 754},
}  # fmt: skip


def test_describe():
    assert describe(None)[0] == "Coco is <em>off</em>"
    line, sub = describe(STATUS)
    assert line == "Coco is <em>napping</em> in her bed" and sub == "She feels happy, a bit sleepy."
    assert "hidden" in describe({**STATUS, "visible": False})[0]
    assert "waiting" in describe({**STATUS, "fullscreen_hidden": True})[0]


def test_sprite_and_switches():
    anims = {"sit": (8, 6), "sleep": (6, 4), "sad": (6, 5)}
    assert sprite_for(None, anims) == "sleep"
    assert sprite_for({"anim": "walk", "state": "sulk"}, anims) == "sad"
    assert sprite_for({"anim": "zoom"}, anims) == "sit"
    assert tile_state("voice", STATUS) == (True, "On")
    assert tile_state("wake", STATUS) == (None, "Not set up")
    assert tile_state("paused", STATUS) == (True, "Roaming")
    assert tile_state("focus", STATUS) == (True, "Work 12:34")


# --- the link client
def test_link_client_talks_to_coco(tmp_path):
    calls = []
    server = LinkServer(
        {
            "status": lambda: {"state": "sit"},
            "command": lambda d: calls.append(d) or {"ok": True},
            "chat": lambda t: {"ok": True, "chat_id": 7},
        },
        path=tmp_path / "link.json",
    )
    server.start()
    try:
        coco = CocoLink(path=tmp_path / "link.json")
        assert coco.running and coco.status() == {"state": "sit"}
        coco.command("set", key="voice", value=False)
        assert calls == [{"name": "set", "key": "voice", "value": False}]
        assert coco.chat("hi") == 7
    finally:
        server.stop()
    assert not coco.running and coco.status() is None  # she's gone: no crash, just None


def test_stale_link_file_means_off(tmp_path):
    (tmp_path / "link.json").write_text(json.dumps({"port": 1, "token": "x", "pid": 999999}))
    coco = CocoLink(path=tmp_path / "link.json")
    assert not coco.running and coco.status() is None and coco.stop()
    assert process_exe(os.getpid()).lower().endswith(".exe")
