"""Coco.exe: how she starts (source vs exe), her config copy, the startup entry."""

from coco.core import launch
from coco.core.paths import PROJECT_ROOT, config_file

EXE = r"C:\Users\me\AppData\Local\Programs\Coco\Coco.exe"
PY = r"C:\proj\.venv\Scripts\python.exe"


def test_launch_from_source_and_as_exe():
    cat = launch.launch_command(frozen=False, exe=PY)
    assert cat[0].endswith("pythonw.exe") and cat[1].endswith("run_coco.pyw")
    assert launch.launch_command(hub=True, frozen=False, exe=PY)[1].endswith("run_hub.pyw")
    assert launch.launch_command(frozen=True, exe=EXE) == [EXE]
    assert launch.launch_command(hub=True, frozen=True, exe=EXE) == [EXE, "--hub"]
    assert launch.command_line([EXE, "--hub"]) == f'"{EXE}" --hub'
    assert launch.launch_dir(frozen=True, exe=EXE).endswith("Coco")
    assert launch.launch_dir(frozen=False) == str(PROJECT_ROOT)


def test_is_coco_process():
    assert launch.is_coco_process(PY) and launch.is_coco_process(r"C:\x\pythonw.exe")
    assert launch.is_coco_process(EXE)
    assert not launch.is_coco_process(r"C:\Windows\notepad.exe")
    assert not launch.is_coco_process(None) and not launch.is_coco_process("")


def test_exe_gets_its_own_config_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    default = tmp_path / "bundle" / "config.yaml"
    default.parent.mkdir()
    default.write_text("owner_name: ''\n")
    assert config_file(frozen=False, default=default) == default
    mine = config_file(frozen=True, default=default)
    assert mine == tmp_path / "Coco" / "config.yaml" and mine.read_text() == "owner_name: ''\n"
    mine.write_text("owner_name: Mine\n")  # your edits survive the next start / an update
    assert config_file(frozen=True, default=default).read_text() == "owner_name: Mine\n"


def test_autostart_migrates_only_our_old_entry():
    reg = {}

    def write(name, cmd):
        reg[name] = cmd

    def read(name):
        return reg.get(name)

    assert not launch.migrate_autostart(read, write, frozen=True, exe=EXE)  # off: stays off
    assert reg == {}
    reg[launch.AUTOSTART_NAME] = rf'"{PY[:-10]}pythonw.exe" "C:\proj\run_coco.pyw"'
    assert not launch.migrate_autostart(read, write, frozen=False, exe=EXE)  # source: leave it
    assert launch.migrate_autostart(read, write, frozen=True, exe=EXE)
    assert reg[launch.AUTOSTART_NAME] == f'"{EXE}"'
    assert not launch.migrate_autostart(read, write, frozen=True, exe=EXE)  # already right
    reg[launch.AUTOSTART_NAME] = rf'"{PY[:-10]}pythonw.exe" "C:\proj\run_coco.pyw"'
    test_build = r"C:\proj\dist\Coco\Coco.exe"  # a build being tested: never takes over
    assert not launch.migrate_autostart(read, write, frozen=True, exe=test_build)
    write(launch.AUTOSTART_NAME, f'"{EXE}"')
    reg[launch.AUTOSTART_NAME] = r'"C:\Other\Thing.exe"'  # someone else's command: untouched
    assert not launch.migrate_autostart(read, write, frozen=True, exe=EXE)
