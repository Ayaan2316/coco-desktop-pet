"""Surface detection with fake window rectangles: occlusion, filters, support, landing."""

from coco.behaviour.surfaces import build_surfaces, find_landing, find_support
from coco.core.geometry import Rect, Surface, subtract_intervals
from coco.desktop.base import WindowInfo

MON = Rect(0, 0, 1920, 1080)
WORK = Rect(0, 0, 1920, 1032)


def build(windows, **kw):
    return build_surfaces(
        windows, MON, WORK, min_width=kw.get("min_width", 50), headroom=kw.get("headroom", 90)
    )


def tops(surfaces, hwnd):
    return sorted((s.x1, s.x2) for s in surfaces if s.id == hwnd)


def test_ground_is_work_area_bottom():
    surfaces = build([])
    assert surfaces == [Surface("ground", 1032, 0, 1920, "ground")]


def test_ground_with_autohide_or_side_taskbar():
    # auto-hide: work area == monitor -> she walks on the screen bottom
    s = build_surfaces([], MON, MON, min_width=50, headroom=90)
    assert s[0].y == 1080
    # taskbar on the left: ground spans the work area only
    s = build_surfaces([], MON, Rect(60, 0, 1920, 1080), min_width=50, headroom=90)
    assert (s[0].x1, s[0].x2, s[0].y) == (60, 1920, 1080)


def test_window_top_edge_is_a_surface():
    surfaces = build([WindowInfo(1, Rect(100, 300, 900, 800))])
    assert tops(surfaces, 1) == [(100, 900)]
    assert next(s for s in surfaces if s.id == 1).y == 300


def test_window_in_front_cuts_a_hole():
    front = WindowInfo(2, Rect(400, 200, 600, 700))  # covers y=300 between x 400..600
    back = WindowInfo(1, Rect(100, 300, 900, 800))
    surfaces = build([front, back])  # z-order: topmost first
    assert tops(surfaces, 1) == [(100, 400), (600, 900)]
    assert tops(surfaces, 2) == [(400, 600)]


def test_window_behind_does_not_cut_the_front_one():
    front = WindowInfo(1, Rect(100, 300, 900, 800))
    back = WindowInfo(2, Rect(400, 200, 600, 700))
    surfaces = build([front, back])
    assert tops(surfaces, 1) == [(100, 900)]


def test_filters_maximised_narrow_and_offscreen_windows():
    maximised = WindowInfo(1, Rect(0, 0, 1920, 1032))  # no room above it
    narrow = WindowInfo(2, Rect(100, 400, 130, 600))
    other_monitor = WindowInfo(3, Rect(2000, 400, 2600, 800))
    below_ground = WindowInfo(4, Rect(100, 1040, 600, 1070))
    surfaces = build([maximised, narrow, other_monitor, below_ground])
    assert [s.id for s in surfaces] == ["ground"]


def test_tiny_leftover_pieces_are_dropped():
    front = WindowInfo(2, Rect(120, 100, 880, 700))  # leaves 20 px stubs on each side
    back = WindowInfo(1, Rect(100, 300, 900, 800))
    assert tops(build([front, back]), 1) == []


def test_window_clipped_to_the_monitor():
    surfaces = build([WindowInfo(1, Rect(-300, 400, 500, 800))])
    assert tops(surfaces, 1) == [(0, 500)]


def test_find_support_tolerance_and_disappearance():
    surfaces = [Surface(1, 300, 100, 900)]
    assert find_support(surfaces, 1, 500, 301, tol=2) is not None  # 1 px jitter: fine
    assert find_support(surfaces, 1, 500, 310, tol=2) is None  # moved 10 px: gone
    assert find_support(surfaces, 1, 950, 300, tol=2) is None  # walked off the end
    assert find_support(surfaces, 2, 500, 300, tol=2) is None  # different window


def test_find_landing_is_swept_and_picks_the_first_hit():
    surfaces = [
        Surface("ground", 1032, 0, 1920, "ground"),
        Surface(1, 500, 100, 900),
        Surface(2, 700, 100, 900),
    ]
    # a huge step from y=100 to y=1000 must still hit the window at 500 first
    assert find_landing(surfaces, 400, 100, 1000).id == 1
    # starting below the window's edge (e.g. falling from inside it) skips it
    assert find_landing(surfaces, 400, 600, 1100).id == 2
    # outside the window's x range: only the ground
    assert find_landing(surfaces, 1500, 100, 1100).id == "ground"
    assert find_landing(surfaces, 1500, 100, 900) is None


def test_subtract_intervals():
    assert subtract_intervals(0, 100, [(20, 30), (50, 60)]) == [(0, 20), (30, 50), (60, 100)]
    assert subtract_intervals(0, 100, [(-10, 200)]) == []
    assert subtract_intervals(0, 100, [(200, 300)]) == [(0, 100)]
