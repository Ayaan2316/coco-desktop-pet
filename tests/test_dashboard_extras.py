"""Dashboard extras: how long chats are kept, "about you" in her prompt, screenshots."""

import os

from PIL import Image

from coco.brain.prompt import PromptContext, build_system_prompt
from coco.core.catdef import load_cat
from coco.tools.store import Store


def test_chats_older_than_the_limit_are_deleted(tmp_path):
    now = [1_000_000.0]
    st = Store(tmp_path / "coco.db", clock=lambda: now[0])
    st.add_chat("typed", "old question", "old answer")
    now[0] += 40 * 86400
    st.add_chat("voice", "new question", "new answer")
    assert st.oldest_chat_time() == 1_000_000.0
    assert st.prune_chats(0) == 0 and st.count_chats() == 2  # 0 = forever
    assert st.prune_chats(30) == 1  # the 40-day-old one goes
    assert [c.question for c in st.chats()] == ["new question"]
    assert st.prune_chats(30) == 0


def test_about_you_goes_into_her_prompt():
    ctx = PromptContext(owner="Sam", about="I'm an ECE student and I love cold coffee.")
    prompt = build_system_prompt(load_cat("coco"), ctx)
    assert "What Sam told you about themselves" in prompt and "cold coffee" in prompt
    assert "told you about themselves" not in build_system_prompt(load_cat("coco"))


def test_screenshots_newest_first_and_thumbnails(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    from coco_hub.pages.screenshots import list_shots, picture

    shots = tmp_path / "Coco"
    shots.mkdir()
    for i, name in enumerate(["Coco_2026-11-21_10-00-00.png", "Coco_2026-11-22_09-00-00.png"]):
        Image.new("RGB", (1920, 1200), (200, 100 + i * 50, 120)).save(shots / name)
        os.utime(shots / name, (1000 + i, 1000 + i))
    (shots / "notes.txt").write_text("not a screenshot")
    found = list_shots(shots)
    assert [p.name for p in found] == ["Coco_2026-11-22_09-00-00.png",
                                       "Coco_2026-11-21_10-00-00.png"]  # fmt: skip
    thumb = picture(found[0], 360)
    assert thumb.startswith("data:image/jpeg;base64,")
    cached = list((tmp_path / "appdata" / "Coco" / "hub" / "thumbs").glob("*.jpg"))
    assert len(cached) == 1 and picture(found[0], 360) == thumb  # second time: from the cache
