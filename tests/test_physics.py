"""Physics: gravity, wall/ceiling bounces, throw velocity, jump arcs."""

import math

import pytest

from coco.behaviour.physics import Body, bounce_walls, integrate, solve_jump, throw_velocity


def test_gravity_accelerates_and_caps_at_terminal_velocity():
    b = Body(0, 0)
    for _ in range(60):
        integrate(b, 1 / 60, gravity=1000, air_drag=0, max_fall=10_000)
    assert b.vy == pytest.approx(1000, rel=1e-6)
    assert b.y == pytest.approx(500, rel=0.02)  # s = g t^2 / 2
    for _ in range(600):
        integrate(b, 1 / 60, gravity=1000, air_drag=0, max_fall=3000)
    assert b.vy == 3000


def test_air_drag_slows_sideways_motion():
    b = Body(0, 0, vx=1000)
    integrate(b, 0.5, gravity=0, air_drag=0.4, max_fall=1e9)
    assert b.vx == pytest.approx(800)


def test_bounce_off_left_wall_and_ceiling():
    b = Body(5, 300, vx=-1000, vy=-500)
    hit = bounce_walls(
        b, left=0, right=1920, top=0, half_width=40, height=60, wall_bounce=0.5, ceiling_bounce=0.3
    )
    assert hit
    assert b.x == 40 and b.vx == 500  # pushed back inside, reflected and damped
    b = Body(500, 30, vx=0, vy=-800)
    bounce_walls(b, 0, 1920, 0, 40, 60, 0.5, 0.3)
    assert b.y == 60 and b.vy == pytest.approx(240)


def test_bounce_off_right_wall():
    b = Body(1900, 500, vx=800)
    bounce_walls(b, 0, 1920, 0, 40, 60, 0.5, 0.3)
    assert b.x == 1880 and b.vx == -400


def test_throw_velocity_uses_recent_samples_and_is_capped():
    samples = [(0.00, 0, 0), (0.05, 50, 0), (0.10, 150, -50), (0.12, 190, -70)]
    vx, vy = throw_velocity(samples, window=0.08, max_speed=1e9)
    # window 0.08 s back from t=0.12 -> starts at the t=0.00 sample (first one >= 0.08 s old)
    assert vx == pytest.approx(190 / 0.12) and vy == pytest.approx(-70 / 0.12)
    vx, vy = throw_velocity(samples, window=0.08, max_speed=500)
    assert math.hypot(vx, vy) == pytest.approx(500)


def test_throw_velocity_needs_two_samples():
    assert throw_velocity([(0, 1, 1)], 0.08, 1000) == (0.0, 0.0)
    assert throw_velocity([], 0.08, 1000) == (0.0, 0.0)


@pytest.mark.parametrize(
    "start,end", [((100, 1000), (600, 500)), ((600, 500), (100, 1000)), ((0, 800), (300, 800))]
)
def test_solve_jump_reaches_the_target(start, end):
    g = 2600
    vx, vy, t_total = solve_jump(*start, *end, gravity=g, apex_extra=40)
    b = Body(*start, vx, vy)
    dt = t_total / 1000
    peak = b.y
    for _ in range(1000):
        integrate(b, dt, gravity=g, air_drag=0, max_fall=1e9)
        peak = min(peak, b.y)
    assert b.x == pytest.approx(end[0], abs=2)
    assert b.y == pytest.approx(end[1], abs=3)
    assert peak <= min(start[1], end[1]) - 39  # clears the higher end by apex_extra
