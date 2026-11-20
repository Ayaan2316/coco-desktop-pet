"""Gravity, drag, bounces, throws and jump arcs."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Body:
    x: float  # paw anchor
    y: float
    vx: float = 0.0
    vy: float = 0.0


def integrate(body: Body, dt: float, gravity: float, air_drag: float, max_fall: float) -> None:
    """Advance a falling/flying body by dt (semi-implicit Euler: velocity first)."""
    body.vy = min(body.vy + gravity * dt, max_fall)
    body.vx *= max(0.0, 1.0 - air_drag * dt)
    body.x += body.vx * dt
    body.y += body.vy * dt


def bounce_walls(
    body: Body,
    left: float,
    right: float,
    top: float,
    half_width: float,
    height: float,
    wall_bounce: float,
    ceiling_bounce: float,
) -> bool:
    """Keep her body inside [left, right] and below `top`. Returns True on any bounce."""
    hit = False
    if body.x - half_width < left:
        body.x = left + half_width
        body.vx = abs(body.vx) * wall_bounce
        hit = True
    elif body.x + half_width > right:
        body.x = right - half_width
        body.vx = -abs(body.vx) * wall_bounce
        hit = True
    if body.y - height < top:
        body.y = top + height
        body.vy = abs(body.vy) * ceiling_bounce
        hit = True
    return hit


def throw_velocity(
    samples: list[tuple[float, float, float]], window: float, max_speed: float
) -> tuple[float, float]:
    """Velocity of the mouse over the last `window` seconds of (t, x, y) drag samples.

    Using a short window (not just the last two samples) smooths out jitter; the result is
    capped at max_speed so a wild flick doesn't launch her into orbit.
    """
    if len(samples) < 2:
        return 0.0, 0.0
    t_end, x_end, y_end = samples[-1]
    start = samples[-2]
    for s in reversed(samples[:-1]):
        start = s
        if t_end - s[0] >= window:
            break
    dt = t_end - start[0]
    if dt <= 1e-4:
        return 0.0, 0.0
    vx, vy = (x_end - start[1]) / dt, (y_end - start[2]) / dt
    speed = math.hypot(vx, vy)
    if speed > max_speed:
        vx, vy = vx * max_speed / speed, vy * max_speed / speed
    return vx, vy


def solve_jump(
    x0: float, y0: float, x1: float, y1: float, gravity: float, apex_extra: float
) -> tuple[float, float, float]:
    """Initial (vx, vy) and flight time for a ballistic jump from (x0, y0) to (x1, y1).

    The arc peaks `apex_extra` px above the higher of the two points, so she clears the
    edge she's jumping onto. Works for jumping up, down, or level.
    """
    apex = min(y0, y1) - apex_extra
    vy = -math.sqrt(2 * gravity * max(y0 - apex, 1.0))
    t_up = -vy / gravity
    t_down = math.sqrt(2 * max(y1 - apex, 1.0) / gravity)
    t_total = t_up + t_down
    return (x1 - x0) / t_total, vy, t_total
