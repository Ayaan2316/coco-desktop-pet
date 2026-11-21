"""Coco's dashboard link (localhost + token) and the chat recorder."""

import http.client
import json

import pytest

from coco.hub_link import ChatRecorder, LinkServer
from coco.tools.store import Store


@pytest.fixture
def link(tmp_path):
    calls = []
    server = LinkServer(
        {
            "status": lambda: {"visible": True},
            "command": lambda data: calls.append(data) or {"ok": True},
            "chat": lambda text: {"chat_id": len(text)},
        },
        token="secret-token",
        path=tmp_path / "link.json",
    )
    server.start()
    server.calls = calls
    yield server
    server.stop()


def request(link, method, path, body=None, token="secret-token", host=None):
    conn = http.client.HTTPConnection("127.0.0.1", link.port, timeout=5)
    headers = {"Host": host or f"127.0.0.1:{link.port}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=headers)
    r = conn.getresponse()
    out = r.status, json.loads(r.read() or b"{}")
    conn.close()
    return out


def test_link_file_and_status(link, tmp_path):
    info = json.loads((tmp_path / "link.json").read_text())
    assert info["port"] == link.port and info["token"] == "secret-token"
    assert request(link, "GET", "/status") == (200, {"visible": True})


def test_needs_the_token_and_localhost(link):
    assert request(link, "GET", "/status", token=None)[0] == 401
    assert request(link, "GET", "/status", token="wrong")[0] == 401
    # a web page that points evil.example at 127.0.0.1 (DNS rebinding) is refused
    assert request(link, "GET", "/status", host="evil.example:80")[0] == 403


def test_commands_and_chat(link):
    assert request(link, "POST", "/command", {"name": "kill"}) == (200, {"ok": True})
    assert link.calls == [{"name": "kill"}]
    assert request(link, "POST", "/chat", {"text": "hello"}) == (200, {"chat_id": 5})
    assert request(link, "POST", "/chat", {"text": "  "})[0] == 400
    assert request(link, "POST", "/chat", {"text": "x" * 2001})[0] == 400
    assert request(link, "POST", "/nope", {})[0] == 404


def test_big_bodies_are_refused(link):
    conn = http.client.HTTPConnection("127.0.0.1", link.port, timeout=5)
    conn.putrequest("POST", "/command", skip_host=True)
    conn.putheader("Host", f"127.0.0.1:{link.port}")
    conn.putheader("Authorization", "Bearer secret-token")
    conn.putheader("Content-Length", str(10**7))
    conn.endheaders()
    assert conn.getresponse().status == 413
    conn.close()


def test_stop_removes_its_link_file(tmp_path):
    server = LinkServer({}, path=tmp_path / "link.json")
    server.start()
    server.stop()
    assert not (tmp_path / "link.json").exists()


def test_chat_recorder(tmp_path):
    store = Store(tmp_path / "coco.db")
    now = [0.0]
    on = [True]
    rec = ChatRecorder(store, enabled=lambda: on[0], clock=lambda: now[0], window=60)
    rec.asked("voice", "play Kesariya")
    rec.answered("On it!")
    rec.said("Playing Kesariya! 🎵")  # the tool's result joins the same entry
    rec.said("Playing Kesariya! 🎵")  # (said twice: kept once)
    now[0] = 100
    rec.said("It's late, go to sleep~")  # nobody asked: her own line
    chats = store.chats()
    assert [(c.source, c.question, c.answer) for c in chats] == [
        ("coco", "", "It's late, go to sleep~"),
        ("voice", "play Kesariya", "On it!\nPlaying Kesariya! 🎵"),
    ]
    on[0] = False  # history paused
    rec.asked("typed", "secret stuff")
    rec.answered("ok")
    assert store.count_chats() == 2
    assert [c.question for c in store.chats(query="kesariya")] == ["play Kesariya"]
    assert store.delete_all_chats() == 2 and store.chats() == []


def test_activity_log(tmp_path):
    store = Store(tmp_path / "coco.db")
    store.log_activity("tool", "spotify", "text=kesariya | Playing!", ok=True)
    store.log_activity("system", "started")
    assert [(a.kind, a.name, a.ok) for a in store.activity()] == [
        ("system", "started", True),
        ("tool", "spotify", True),
    ]
