"""Multi-monitor: which screen she lives on, following you, plug / unplug."""

from coco.core.config import load_config
from coco.core.geometry import Rect
from coco.desktop.base import MonitorInfo
from coco.desktop.fake import FakeDesktop
from coco.desktop.sensors import DesktopSensor, MonitorFollower, monitor_containing, pick_monitor

LEFT = MonitorInfo("\\\\.\\DISPLAY1", Rect(0, 0, 1920, 1200), Rect(0, 0, 1920, 1200), 1.25, True)
RIGHT = MonitorInfo("\\\\.\\DISPLAY2", Rect(1920, 0, 3840, 1080), Rect(1920, 0, 3840, 1040), 1.0)


def sensor(*monitors, home="primary"):
    fake = FakeDesktop(monitor_list=list(monitors))
    cfg = load_config()
    s = DesktopSensor(fake, cfg)
    s.home = home
    s.refresh_monitors()
    return s, fake


def test_pick_monitor():
    assert pick_monitor([LEFT, RIGHT], "primary") is LEFT
    assert pick_monitor([LEFT, RIGHT], "follow") is LEFT  # (starts on the main screen)
    assert pick_monitor([LEFT, RIGHT], "\\\\.\\display2") is RIGHT
    assert pick_monitor([LEFT, RIGHT], "\\\\.\\DISPLAY9") is LEFT  # gone: the main one


def test_monitor_containing():
    assert monitor_containing([LEFT, RIGHT], Rect(2000, 100, 3000, 900)) is RIGHT
    assert monitor_containing([LEFT, RIGHT], Rect(1500, 100, 2100, 900)) is LEFT  # centre 1800
    assert monitor_containing([LEFT, RIGHT], Rect(5000, 0, 6000, 10)) is None


def test_follower_waits_and_cools_down():
    f = MonitorFollower(delay=4, cooldown=15)
    d1, d2 = LEFT.device, RIGHT.device
    assert f.update(0, d1, d2) is None  # a glance...
    assert f.update(2, d1, d1) is None  # ...and back: nothing
    assert f.update(3, d1, d2) is None
    assert f.update(6, d1, d2) is None  # only 3 s there
    assert f.update(7.5, d1, d2) == d2  # 4.5 s: move
    assert f.update(9, d2, d1) is None and f.update(14, d2, d1) is None  # (cooling down)
    assert f.update(23, d2, d1) == d1


def test_follow_mode_stays_put_and_unplugging_moves_her():
    s, fake = sensor(LEFT, RIGHT, home="follow")
    s.monitor = RIGHT  # she followed you to the right screen
    s.refresh_monitors()
    assert s.monitor.device == RIGHT.device  # a resolution change elsewhere: she stays
    fake.monitor_list = [LEFT]  # the right screen is unplugged
    s.refresh_monitors()
    assert s.monitor.device == LEFT.device


def test_a_chosen_screen():
    s, fake = sensor(LEFT, RIGHT, home=RIGHT.device)
    assert s.monitor.device == RIGHT.device
    fake.monitor_list = [LEFT]
    s.refresh_monitors()
    assert s.monitor.device == LEFT.device  # gone: the main screen...
    fake.monitor_list = [LEFT, RIGHT]
    s.refresh_monitors()
    assert s.monitor.device == RIGHT.device  # ...and back home when it's plugged in again


def test_moving_her_re_places_the_bed_and_restarts_her_there():
    """The app's _move_to_monitor on a stand-in app (no Qt windows needed)."""
    from types import SimpleNamespace

    from coco.app import CocoApp

    did = []
    s, _ = sensor(LEFT, RIGHT, home="follow")
    app = SimpleNamespace(
        sensor=s,
        mapper=SimpleNamespace(refresh=lambda ms: did.append("mapper")),
        cat_window=SimpleNamespace(monitor=None),
        overlay=SimpleNamespace(cover=lambda m: did.append(("overlay", m.device))),
        _apply_size=lambda: did.append(("bed on", s.monitor.device)),
        _frame_px=lambda: 128.0,
        engine=SimpleNamespace(start=lambda world: did.append("in her bed")),
        _kick=lambda: did.append("frame"),
    )
    CocoApp._move_to_monitor(app, RIGHT.device)
    assert s.monitor.device == RIGHT.device and app.cat_window.monitor is s.monitor
    assert did == ["mapper", ("overlay", RIGHT.device), ("bed on", RIGHT.device), "in her bed",
                   "frame"]  # fmt: skip
    did.clear()
    CocoApp._move_to_monitor(app, r"\\.\DISPLAY7")  # a screen that isn't there: nothing
    assert did == [] and s.monitor.device == RIGHT.device
