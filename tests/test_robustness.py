"""Single instance detection and off-screen respawn timing."""

from types import SimpleNamespace

from coco.core.launch import other_coco_pids


def proc(pid, *cmd):
    return SimpleNamespace(info={"pid": pid, "cmdline": list(cmd)})


def test_other_cocos_are_found_but_not_her_dashboard_or_keeper():
    procs = [
        proc(10, r"C:\v\Scripts\pythonw.exe", r"C:\p\run_coco.pyw"),  # another Coco
        proc(11, r"C:\v\Scripts\pythonw.exe", r"C:\p\run_hub.pyw"),  # her dashboard
        proc(12, r"C:\v\Scripts\pythonw.exe", r"C:\p\run_keeper.pyw"),  # the keeper
        proc(13, r"C:\Apps\Coco\Coco.exe"),  # Coco.exe
        proc(14, r"C:\Apps\Coco\Coco.exe", "--hub"),
        proc(15, r"C:\Apps\Coco\Coco.exe", "--selftest", "--gpu"),
        proc(16, r"C:\Windows\notepad.exe", r"C:\p\run_coco.pyw.txt"),
        proc(17),  # (no command line: system process)
    ]
    assert other_coco_pids(procs, me=99999) == [10, 13]


def test_she_is_not_counted_twice_with_her_own_launcher():
    import os

    procs = [proc(os.getpid(), "pythonw.exe", "run_coco.pyw")]
    assert other_coco_pids(procs) == []


def test_a_moment_off_screen_is_not_a_respawn(sim_factory):
    sim = sim_factory()
    sim.run(1)
    eng = sim.eng
    x, y = eng.body.x, eng.body.y
    eng.body.x = -10_000  # a display change reports a tiny fake monitor for a moment
    sim.step()
    eng.body.x = x  # ...and the real one is back
    sim.run(2)
    assert abs(eng.body.y - y) < 1 and eng._off_since is None  # never respawned
    eng.body.x = -10_000  # really lost: respawned after 1.5 s
    sim.run(2)
    assert eng.body.x > 0
