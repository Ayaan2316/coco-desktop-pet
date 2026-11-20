"""Coco's states, one small class per thing she can be doing."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from coco.behaviour.physics import bounce_walls, integrate, solve_jump, throw_velocity
from coco.behaviour.surfaces import find_landing
from coco.core.geometry import Point, Surface

if TYPE_CHECKING:
    from coco.behaviour.engine import BehaviourEngine

DONE = "done"


class State:
    name = "state"
    grounded = True
    moving = False
    pettable = False
    clickable = False
    visible = True
    icon: str | None = None  # mood bubble shown while in this state

    def __init__(self, eng: BehaviourEngine) -> None:
        self.eng = eng
        self.t = 0.0  # seconds spent in this state

    def enter(self) -> None:
        pass

    def update(self, dt: float) -> State | str | None:
        return None

    def exit(self) -> None:
        pass

    def describe(self) -> str:
        return self.name


# Idle


class Sit(State):
    """The hub: sits, blinks, swishes her tail, then asks the Director what's next."""

    name = "sit"
    pettable = True
    clickable = True

    def __init__(self, eng: BehaviourEngine, duration: float | None = None) -> None:
        super().__init__(eng)
        self.duration = duration

    def enter(self) -> None:
        eng = self.eng
        eng.play("sit")
        if self.duration is None:
            self.duration = eng.rand_range(eng.cfg.behaviour.decision_interval)
            if eng.is_sleepy():
                self.duration *= 1.5
        self.next_fidget = eng.rand_range(eng.cfg.behaviour.fidget_interval)

    def update(self, dt: float) -> State | str | None:
        eng = self.eng
        self.t += dt
        if eng.anim != "sit" and eng.anim_finished():
            eng.play("sit")
        if self.t >= self.next_fidget and eng.anim == "sit":
            eng.play(eng.rng.choice(("blink", "blink", "tail_swish")))
            self.next_fidget = self.t + eng.rand_range(eng.cfg.behaviour.fidget_interval)
        if self.t >= self.duration:
            return eng.decide()
        return None


class Loop(State):
    """Loops one animation for a while: loaf, groom."""

    pettable = True
    clickable = True

    def __init__(self, eng: BehaviourEngine, name: str, duration: float) -> None:
        super().__init__(eng)
        self.name = name
        self.duration = duration

    def enter(self) -> None:
        self.eng.play(self.name)

    def update(self, dt: float) -> State | str | None:
        self.t += dt
        return DONE if self.t >= self.duration else None


class OneShot(State):
    """Plays a one-shot animation once: yawn, stretch, look_around, land, wake_up."""

    def __init__(self, eng: BehaviourEngine, anim: str, pettable: bool = True) -> None:
        super().__init__(eng)
        self.name = anim
        self.pettable = pettable

    def enter(self) -> None:
        self.eng.play(self.name)

    def update(self, dt: float) -> State | str | None:
        self.t += dt
        return DONE if self.eng.anim_finished() else None


class Land(OneShot):
    def __init__(self, eng: BehaviourEngine) -> None:
        super().__init__(eng, "land", pettable=False)


class WakeUp(OneShot):
    def __init__(self, eng: BehaviourEngine) -> None:
        super().__init__(eng, "wake_up", pettable=False)


class Purr(State):
    """Being petted: purrs while the rubbing continues."""

    name = "purr"
    clickable = False

    def enter(self) -> None:
        self.eng.play("purr")
        self.max_t = self.eng.cfg.behaviour.durations.get("purr_max", [20, 20])[1]

    def update(self, dt: float) -> State | str | None:
        self.t += dt
        if not self.eng.petter.is_petting(self.eng.clock()) or self.t >= self.max_t:
            return DONE
        return None


class Sleep(State):
    """Curled up in the bed, breathing. A click (or the timer) wakes her."""

    name = "sleep"
    clickable = True
    icon = "zzz"

    def enter(self) -> None:
        eng = self.eng
        eng.play("sleep")
        self.duration = eng.rand_range(eng.cfg.behaviour.durations.get("sleep", [90, 300]))
        if eng.is_sleepy():
            self.duration *= eng.cfg.behaviour.night_sleep_duration_mult

    def update(self, dt: float) -> State | str | None:
        self.t += dt
        if self.t >= self.duration:
            return WakeUp(self.eng)
        return None


# Moving along a surface


class Walk(State):
    """Walk (or run) to target_x along the current surface.

    allow_drop: walking past the end of a window top makes her hop down (-> Fall).
    free:       used for peeking: she may walk past the screen edge; no surface checks.
    """

    moving = True

    def __init__(
        self,
        eng: BehaviourEngine,
        target_x: float,
        run: bool = False,
        allow_drop: bool = False,
        free: bool = False,
    ) -> None:
        super().__init__(eng)
        self.target = target_x
        self.run = run
        self.allow_drop = allow_drop
        self.free = free
        self.name = "run" if run else "walk"

    @property
    def grounded(self) -> bool:  # type: ignore[override]
        # While walking back in from outside her surface (e.g. off-screen after a peek)
        # there's nothing under her yet: don't let the support check drop her.
        if self.free:
            return False
        s = self.eng.surface
        return s is None or s.covers_x(self.eng.body.x)

    def enter(self) -> None:
        eng = self.eng
        eng.play(self.name)
        mv = eng.cfg.movement
        self.speed = eng.gait(mv.run_speed if self.run else mv.walk_speed)
        eng.face_toward(self.target)
        self.max_t = abs(self.target - eng.body.x) / max(self.speed, 1) * 2 + 2

    def update(self, dt: float) -> State | str | None:
        eng, b = self.eng, self.eng.body
        self.t += dt
        dx = self.target - b.x
        step = self.speed * dt
        if abs(dx) <= step or self.t > self.max_t:
            b.x = self.target if abs(dx) <= step else b.x
            b.vx = 0.0
            return DONE
        direction = 1 if dx > 0 else -1
        eng.facing = direction
        b.x += direction * step
        b.vx = direction * self.speed
        if self.free:
            b.y = eng.world.ground.y
            return None
        s = eng.surface
        # Past the end of her surface, moving outwards? (Walking back in from outside it,
        # e.g. after a peek, is fine.)
        past_end = s is not None and (
            (direction > 0 and b.x > s.x2) or (direction < 0 and b.x < s.x1)
        )
        if past_end:
            if self.allow_drop:  # hop off the end of the window
                return Fall(eng, vx=direction * self.speed * 0.8, vy=-eng.px(260))
            b.x = min(max(b.x, s.x1), s.x2)
            b.vx = 0.0
            return DONE
        return None

    def describe(self) -> str:
        return f"{self.name} -> x={self.target:.0f}"


# In the air


class Airborne(State):
    """Shared physics for everything in the air: gravity, wall bounces, landing."""

    grounded = False
    moving = True
    air_drag = True  # aimed jumps turn this off so they land exactly on target

    def step_air(self, dt: float) -> Surface | None:
        eng, b, w = self.eng, self.eng.body, self.eng.world
        ph = eng.cfg.physics
        eng.surface = None
        steps = max(1, math.ceil(dt / (1 / 120)))  # sub-steps keep fast throws stable
        h = dt / steps
        for _ in range(steps):
            prev_y = b.y
            drag = ph.air_drag if self.air_drag else 0.0
            integrate(b, h, eng.px(ph.gravity), drag, eng.px(ph.max_fall_speed))
            bounce_walls(
                b,
                w.work.left,
                w.work.right,
                w.monitor.top,
                eng.half_w,
                eng.height,
                ph.wall_bounce,
                ph.ceiling_bounce,
            )
            if b.vy > 0:
                s = find_landing(w.surfaces, b.x, prev_y, b.y)
                if s is not None:
                    return s
            g = w.ground
            if b.y >= g.y:  # safety net: never fall through the ground
                b.x = min(max(b.x, g.x1), g.x2)
                return g
        return None


class Fall(Airborne):
    """Falling or thrown. Lands on the first surface she crosses."""

    name = "fall"

    def __init__(self, eng: BehaviourEngine, vx: float = 0.0, vy: float = 0.0) -> None:
        super().__init__(eng)
        self.vx, self.vy = vx, vy

    def enter(self) -> None:
        self.eng.play("fall")
        self.eng.body.vx, self.eng.body.vy = self.vx, self.vy
        if abs(self.vx) > 1:
            self.eng.facing = 1 if self.vx > 0 else -1

    def update(self, dt: float) -> State | str | None:
        self.t += dt
        s = self.step_air(dt)
        return self.eng.land_on(s) if s is not None else None


class Jump(Airborne):
    """Crouch, then a ballistic arc to `target` (a window top, into/out of the bed)."""

    name = "jump"
    air_drag = False

    def __init__(self, eng: BehaviourEngine, target: Point, apex_extra: float | None = None):
        super().__init__(eng)
        self.target = target
        self.apex_extra = apex_extra
        self.flying = False

    @property
    def grounded(self) -> bool:  # type: ignore[override]
        return not self.flying  # still standing while crouching

    def enter(self) -> None:
        self.eng.play("jump")
        self.eng.face_toward(self.target.x)

    def update(self, dt: float) -> State | str | None:
        eng, b = self.eng, self.eng.body
        self.t += dt
        if not self.flying:
            if self.t < eng.cfg.movement.jump_crouch_time:
                return None
            extra = (
                self.apex_extra
                if self.apex_extra is not None
                else eng.px(eng.cfg.movement.jump_apex_extra)
            )
            b.vx, b.vy, _ = solve_jump(
                b.x, b.y, self.target.x, self.target.y, eng.px(eng.cfg.physics.gravity), extra
            )
            self.flying = True
            return None
        if b.vy > 0 and eng.anim == "jump":
            eng.play("fall")
        s = self.step_air(dt)
        return eng.land_on(s) if s is not None else None

    def describe(self) -> str:
        return f"jump -> ({self.target.x:.0f}, {self.target.y:.0f})"


class Dragged(State):
    """Held by the mouse: dangles from the scruff, follows the cursor. Release = throw."""

    name = "dangle"
    grounded = False
    moving = True

    def enter(self) -> None:
        self.eng.play("dangle")
        self.eng.surface = None
        self.samples: list[tuple[float, float, float]] = []

    def update(self, dt: float) -> State | str | None:
        eng, b = self.eng, self.eng.body
        self.t += dt
        c = eng.world.cursor
        ox, oy = eng.grab_offset()
        b.x, b.y = c.x + ox, c.y + oy
        now = eng.clock()
        self.samples.append((now, c.x, c.y))
        self.samples = [s for s in self.samples if now - s[0] <= 0.3]
        if eng.drag_released:
            ph = eng.cfg.physics
            vx, vy = throw_velocity(
                self.samples, ph.throw_sample_window, eng.px(ph.max_throw_speed)
            )
            speed = math.hypot(vx, vy) / eng.k  # logical px/s
            if speed > 300:
                eng.mood.on_thrown(speed)
            return Fall(eng, vx, vy)
        return None


# Cursor play


class Stalk(State):
    """Crouch and creep towards the cursor, wiggle... then pounce."""

    name = "stalk"
    moving = True

    def enter(self) -> None:
        self.eng.play("stalk")
        self.wiggle = 0.0

    def update(self, dt: float) -> State | str | None:
        eng, b = self.eng, self.eng.body
        cp = eng.cfg.cursor_play
        self.t += dt
        c = eng.world.cursor
        if self.t > cp.give_up_after or not eng.cursor_in_play_range(slack=1.3):
            return DONE  # lost interest
        dx = c.x - b.x
        eng.facing = 1 if dx > 0 else -1
        if abs(dx) > eng.px(cp.pounce_distance):
            self.wiggle = 0.0
            step = eng.gait(eng.cfg.movement.stalk_speed) * dt
            new_x = b.x + eng.facing * step
            s = eng.surface
            if s is not None and not s.covers_x(new_x):
                return DONE  # can't get closer without falling off
            b.x = new_x
            return None
        self.wiggle += dt
        if self.wiggle >= cp.wiggle_time:
            return Pounce(eng, Point(c.x, c.y))
        return None


class Pounce(Airborne):
    """Leap so her paws reach the cursor at the top of the arc. Her window ignores clicks
    for a moment, so a pounce never eats the click you were about to make."""

    air_drag = False

    name = "pounce"

    def __init__(self, eng: BehaviourEngine, target: Point) -> None:
        super().__init__(eng)
        self.target = target

    def enter(self) -> None:
        eng, b = self.eng, self.eng.body
        eng.play("pounce")
        g = eng.px(eng.cfg.physics.gravity)
        apex_y = self.target.y + eng.height * 0.35
        rise = max(b.y - apex_y, eng.px(40))
        b.vy = -math.sqrt(2 * g * rise)
        t_up = -b.vy / g
        max_vx = eng.px(eng.cfg.movement.run_speed) * 4
        b.vx = max(-max_vx, min(max_vx, (self.target.x - b.x) / t_up))
        eng.face_toward(self.target.x)
        eng.click_through_until = eng.now + eng.cfg.cursor_play.click_through_time + 2 * t_up
        eng.mood.on_play()  # pouncing is the best game

    def update(self, dt: float) -> State | str | None:
        self.t += dt
        s = self.step_air(dt)
        return self.eng.land_on(s) if s is not None else None


# Peeking from a screen edge


class PeekHide(State):
    """Waiting just past the screen edge, invisible."""

    name = "peek_hide"
    grounded = False
    visible = False

    def __init__(self, eng: BehaviourEngine, duration: float) -> None:
        super().__init__(eng)
        self.duration = duration

    def enter(self) -> None:
        self.eng.play("sit")

    def update(self, dt: float) -> State | str | None:
        self.t += dt
        return DONE if self.t >= self.duration else None


class PeekIn(State):
    """Only her head pokes in from the edge (the rest of the window is off-screen)."""

    name = "peek"
    grounded = False

    def __init__(self, eng: BehaviourEngine, side: int) -> None:
        super().__init__(eng)
        self.side = side  # +1 = right edge, -1 = left edge

    def enter(self) -> None:
        eng = self.eng
        w = eng.world
        edge = w.work.right if self.side > 0 else w.work.left
        eng.body.x = edge + self.side * eng.frame_px * 0.08
        eng.body.y = w.ground.y
        eng.facing = -self.side  # look inwards
        eng.play("peek")

    def update(self, dt: float) -> State | str | None:
        self.t += dt
        return DONE if self.eng.anim_finished() else None


# Chat + emotions


class Think(State):
    """Waiting for the AI's first words: thinking pose with a "?" above her head."""

    name = "think"
    icon = "question"

    def enter(self) -> None:
        self.eng.play("think")

    def update(self, dt: float) -> State | str | None:
        self.t += dt
        return DONE if self.t > 60 else None  # safety net if no reply ever comes


class Talk(State):
    """Mouth moving while her words appear in the bubble.

    For AI replies the engine calls finish_in() when the final text has arrived (she keeps
    talking while the bubble finishes typing it), then she shows the reply's emotion.
    For canned lines `duration` is known up front.
    """

    name = "talk"

    def __init__(self, eng: BehaviourEngine, duration: float | None = None) -> None:
        super().__init__(eng)
        self.duration = duration
        self.end_at: float | None = None
        self.then: str | None = None
        self.heard_voice = False
        self.voice_deadline = (duration or 0) + 6.0  # give up waiting for audio

    def enter(self) -> None:
        self.eng.play("talk")

    def finish_in(self, seconds: float, emotion: str | None) -> None:
        self.end_at = self.t + seconds
        self.then = emotion
        self.voice_deadline = self.t + seconds + 6.0

    def _voice_done(self) -> bool:
        """With her voice on, keep talking until she has actually finished speaking
        (the audio starts ~1 s after the text, so also wait for it to begin)."""
        eng = self.eng
        if not eng.voice_on:
            return True
        if eng.speaking:
            self.heard_voice = True
            return False
        return getattr(self, "heard_voice", False) or self.t > self.voice_deadline

    def update(self, dt: float) -> State | str | None:
        self.t += dt
        if self.t > 90:
            return DONE
        end = self.duration if self.duration is not None else self.end_at
        if end is None or self.t < end or not self._voice_done():
            return None
        if self.duration is not None:
            return DONE
        return Emote(self.eng, self.then or "neutral")


class Emote(State):
    """Show an emotion: the animation cat.yaml maps it to (happy, sad, love...)."""

    pettable = True
    clickable = True

    def __init__(self, eng: BehaviourEngine, emotion: str, duration: float = 2.5) -> None:
        super().__init__(eng)
        self.emotion = emotion
        self.duration = duration
        self.name = "emote"

    def enter(self) -> None:
        eng = self.eng
        eng.play(eng.cat.emotion_anim(self.emotion))  # missing art falls back to sit
        spec = eng.manifest[eng.anim]
        if not spec.loop:
            self.duration = max(self.duration, spec.duration + 0.4)

    def update(self, dt: float) -> State | str | None:
        self.t += dt
        return DONE if self.t >= self.duration else None

    def describe(self) -> str:
        return f"emote {self.emotion}"


class Sulk(State):
    """Ignored for too long: sad, facing away. Petting her (-> purr) ends it."""

    name = "sulk"
    pettable = True
    clickable = True

    def __init__(self, eng: BehaviourEngine, duration: float = 30.0) -> None:
        super().__init__(eng)
        self.duration = duration

    def enter(self) -> None:
        self.eng.facing = -self.eng.facing  # turns her back on you
        self.eng.play("sad")

    def update(self, dt: float) -> State | str | None:
        self.t += dt
        return DONE if self.t >= self.duration else None
