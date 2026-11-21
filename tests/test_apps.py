"""Open apps / sites / searches: phrases, lookup order, and what she must never run."""

import pytest

import coco.tools  # noqa: F401 - registers the tools and their phrases
from coco.tools.apps import Launcher, Target, search_url
from coco.tools.intents import parse_task
from coco.tools.registry import REGISTRY, ToolCall
from coco.tools.runner import ToolContext

TOOLS = ["open_app", "web_search", "youtube", "screenshot", "explain_screen"]


@pytest.mark.parametrize(
    ("said", "tool", "args"),
    [
        ("Coco, open VS Code", "open_app", {"target": "vs code"}),
        ("open youtube", "open_app", {"target": "youtube"}),
        ("launch blender", "open_app", {"target": "blender"}),
        ("go to github", "open_app", {"target": "github"}),
        ("open my downloads folder", "open_app", {"target": "downloads folder"}),
        ("youtube kholo", "open_app", {"target": "youtube"}),
        ("vs code open karo", "open_app", {"target": "vs code"}),
        ("search youtube for lofi beats", "web_search",
         {"target": "youtube", "text": "lofi beats"}),
        ("search for ohms law on wikipedia", "web_search",
         {"target": "wikipedia", "text": "ohms law"}),
        ("play arijit singh on youtube", "youtube", {"text": "arijit singh"}),
        ("google esp32 pinout", "web_search", {"target": "google", "text": "esp32 pinout"}),
        ("youtube pe lofi chalao", "youtube", {"text": "lofi"}),
        ("take a screenshot", "screenshot", {"target": "screen"}),  # specific beats "open"
    ],
)  # fmt: skip
def test_phrases(said, tool, args):
    call = parse_task(said, TOOLS)
    assert call is not None and call.tool == tool, said
    assert call.args == args


@pytest.mark.parametrize(
    "said",
    ["how do I open a jar file?", "what is youtube?", "start focus", "I opened it already"],
)
def test_not_open_requests(said):
    call = parse_task(said, TOOLS)
    assert call is None or call.tool != "open_app", said


@pytest.fixture
def launcher(tmp_path):
    menu = tmp_path / "Start Menu"
    (menu / "Blender").mkdir(parents=True)
    (menu / "Blender" / "Blender 5.1.lnk").write_text("")
    (menu / "Blender" / "Uninstall Blender.lnk").write_text("")
    (menu / "Arduino IDE.lnk").write_text("")
    opened = []
    lau = Launcher(apps={"vs code": "code.exe", "downloads": "folder:Downloads"},
                   sites={"youtube": "https://www.youtube.com"}, opener=opened.append,
                   menu_dirs=[menu], folders={"Downloads": tmp_path / "dl"},
                   start_apps={})  # fmt: skip
    return lau, opened


def test_lookup_order(launcher):
    lau, _ = launcher
    assert lau.resolve("VS Code") == Target("app", "code.exe", "VS Code")
    assert lau.resolve("the YouTube website").kind == "site"
    assert lau.resolve("blender").value.endswith("Blender 5.1.lnk")  # never the uninstaller
    assert lau.resolve("arduino").label == "Arduino IDE"
    assert lau.resolve("downloads").kind == "folder"
    assert lau.resolve("stackoverflow.com") == Target("site", "https://stackoverflow.com",
                                                      "stackoverflow.com")  # fmt: skip


def test_typed_paths_and_commands_are_never_run(launcher):
    lau, _ = launcher
    for name in (r"C:\Windows\System32\cmd.exe", "cmd /c del *", "powershell -c rm -r ~",
                 "format c:"):  # fmt: skip
        t = lau.resolve(name)
        assert t.kind == "search" and t.value.startswith("https://www.google.com/search?q=")


def test_open_and_search_tools(launcher):
    lau, opened = launcher
    ctx = ToolContext(services={"launcher": lau})
    allowed = ["open_app", "web_search"]
    r = REGISTRY.run(ToolCall("open_app", {"target": "youtube"}), ctx, allowed)
    assert opened[-1] == "https://www.youtube.com" and "Opening" in r.say
    r = REGISTRY.run(ToolCall("open_app", {"target": "youtube"}, lang="hinglish"), ctx, allowed)
    assert "khol rahi hoon" in r.say and r.speech_hi
    r = REGISTRY.run(ToolCall("web_search", {"target": "youtube", "text": "lofi"}), ctx, allowed)
    assert opened[-1] == "https://www.youtube.com/results?search_query=lofi"
    assert search_url("nope", "a b") == "https://www.google.com/search?q=a+b"


def test_missing_program_falls_back_to_a_search(tmp_path):
    opened = []
    lau = Launcher(apps={"ghost": str(tmp_path / "ghost.exe")}, sites={}, opener=opened.append,
                   menu_dirs=[], start_apps={})  # fmt: skip
    ctx = ToolContext(services={"launcher": lau})
    r = REGISTRY.run(ToolCall("open_app", {"target": "ghost"}), ctx, ["open_app"])
    assert opened == ["https://www.google.com/search?q=ghost"] and "searched" in r.say
