"""Petting gesture: slow back-and-forth rubbing counts; resting or swiping doesn't."""

from coco.behaviour.petting import PetDetector
from coco.core.config import PettingConfig


def rub(det, start_t=0.0, step_px=20, dt=1 / 30, n=40, amplitude=3):
    """Cursor moving back and forth: `amplitude` steps right, then the same left..."""
    x, direction, t = 500.0, 1, start_t
    for i in range(n):
        if i and i % amplitude == 0:
            direction = -direction
        x += direction * step_px
        t += dt
        if det.add_sample(t, x, 300):
            return t
    return None


def test_slow_rubbing_is_a_pet():
    det = PetDetector(PettingConfig())
    t = rub(det)
    assert t is not None
    assert det.is_petting(t + 1.0)
    assert not det.is_petting(t + 5.0)


def test_fast_swipe_is_not_a_pet():
    det = PetDetector(PettingConfig())
    assert rub(det, step_px=80) is None  # 2400 px/s: faster than max_speed


def test_one_direction_is_not_a_pet():
    det = PetDetector(PettingConfig())
    assert rub(det, amplitude=1000) is None  # no back-and-forth


def test_resting_cursor_is_not_a_pet():
    det = PetDetector(PettingConfig())
    assert rub(det, step_px=1) is None  # 30 px/s: too slow


def test_pause_resets_the_gesture():
    det = PetDetector(PettingConfig())
    rub(det, n=8)  # not enough path yet
    assert rub(det, start_t=10.0, n=8) is None  # 10 s later: starts over, still too short


def test_scale_factor_applies():
    det = PetDetector(PettingConfig(), scale=2.0)  # 200 % display: needs 2x the path
    assert rub(det, n=14) is None
