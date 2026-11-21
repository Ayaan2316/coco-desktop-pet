"""Store apps (WhatsApp, Spotify...) and opening your files by name."""

import os

import pytest

import coco.tools  # noqa: F401 - registers the tools and their phrases
from coco.tools.apps import Launcher
from coco.tools.files import FileFinder, name_score, query_words
from coco.tools.intents import parse_task
from coco.tools.registry import REGISTRY, ToolCall
from coco.tools.runner import ToolContext

STORE = {
    "WhatsApp": "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
    "Spotify": "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify",
    "Arduino IDE": "cc.arduino.IDE2",
    "Telegram Desktop": "TelegramMessengerLLP.TelegramDesktop_t4vj0pshhgkwm"
    "!Telegram.TelegramDesktop.Store",
}  # fmt: skip


def launcher(opened=None):
    return Launcher(apps={}, sites={"whatsapp": "https://web.whatsapp.com",
                                    "youtube": "https://www.youtube.com"},
                    opener=(opened if opened is not None else []).append, menu_dirs=[],
                    start_apps=dict(STORE))  # fmt: skip


def test_installed_apps_beat_websites():
    lau = launcher()
    t = lau.resolve("WhatsApp")
    assert t.kind == "store" and t.value == "shell:AppsFolder\\" + STORE["WhatsApp"]
    assert t.label == "WhatsApp"
    assert lau.resolve("youtube").kind == "site"  # no YouTube app: the website
    assert lau.resolve("telegram").label == "Telegram Desktop"
    assert lau.resolve("arduino").label == "Arduino IDE"
    assert lau.resolve("meri whatsapp").kind == "store"


@pytest.fixture
def tree(tmp_path):
    docs = tmp_path / "Documents"
    (docs / "college" / "sem5").mkdir(parents=True)
    (docs / "node_modules").mkdir()
    files = {
        docs / "college" / "sem5" / "IoT_Project_Report_Final.docx": 10,
        docs / "college" / "sem5" / "IoT Report draft.pdf": 50,
        docs / "Sam_Resume.pdf": 10,
        docs / "lab_manual.pdf": 10,
        docs / "resume_builder.exe": 200,
        docs / "node_modules" / "resume.js": 300,  # skipped folder
    }
    for f, age in files.items():
        f.write_text("x")
        os.utime(f, (1_700_000_000 - age, 1_700_000_000 - age))
    return tmp_path


def test_scoring():
    assert query_words("my IoT report") == ["iot", "report"]
    assert name_score(["iot", "report"], "IoT_Project_Report_Final.docx") == 1.0
    assert name_score(["iot", "report"], "holiday.jpg") == 0.0
    assert name_score(["resume"], "SamResume.pdf") > 0.8  # camelCase


def test_finder(tree):
    finder = FileFinder([tree / "Documents"])
    names = [f.path.name for f in finder.find("my IoT report")]
    assert names[0] == "IoT_Project_Report_Final.docx"  # newer one first on a tie
    assert "resume.js" not in [f.path.name for f in finder.find("resume")]  # node_modules


def ctx_for(tree, asked=None):
    opened, shown = [], []
    services = {"files": FileFinder([tree / "Documents"]), "open_file": opened.append,
                "show_in_folder": shown.append, "launcher": launcher(),
                "report": lambda call, result: asked.append(("report", result))}  # fmt: skip
    if asked is not None:
        services["ask"] = lambda call, q, choices, cb: asked.append((q, choices, cb))
    return ToolContext(services=services), opened, shown


def test_programs_are_never_run(tree):
    ctx, opened, shown = ctx_for(tree)
    r = REGISTRY.run(ToolCall("open_file", {"target": "resume builder"}), ctx, ["open_file"])
    assert opened == [] and shown[0].name == "resume_builder.exe" and "don't run" in r.say


def test_several_matches_ask_which(tree):
    asked = []
    ctx, opened, _ = ctx_for(tree, asked)
    r = REGISTRY.run(ToolCall("open_file", {"target": "iot report"}), ctx, ["open_file"])
    assert r is None and opened == []
    question, choices, pick = asked[0]
    assert "Which one" in question and len(choices) == 2
    pick("1")
    assert opened[0].name == "IoT Report draft.pdf"


def test_open_app_falls_back_to_a_file(tree):
    ctx, opened, _ = ctx_for(tree)
    r = REGISTRY.run(ToolCall("open_app", {"target": "my lab manual"}), ctx,
                     ["open_app", "open_file"])  # fmt: skip
    assert opened[0].name == "lab_manual.pdf" and "Opening lab_manual.pdf" in r.say


def test_folder_request(tree):
    ctx, opened, shown = ctx_for(tree)
    call = ToolCall("open_file", {"target": "lab manual"},
                    said="open the folder where my lab manual is")  # fmt: skip
    REGISTRY.run(call, ctx, ["open_file"])
    assert opened == [] and shown[0].name == "lab_manual.pdf"


@pytest.mark.parametrize(
    ("said", "tool", "target"),
    [
        ("find my IoT report", "open_file", "iot report"),
        ("where's my resume?", "open_file", "resume"),
        ("open my lab manual pdf", "open_file", "lab manual pdf"),
        ("open the folder where my resume is", "open_file", "resume"),
        ("meri resume kholo", "open_file", "resume"),
        ("open whatsapp", "open_app", "whatsapp"),
        ("whatsapp kholo", "open_app", "whatsapp"),
    ],
)
def test_phrases(said, tool, target):
    call = parse_task(said, ["open_file", "open_app", "web_search"])
    assert call is not None and call.tool == tool, said
    assert call.args["target"] == target
