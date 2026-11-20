"""The behaviour engine. Steps Coco's state machine one frame at a time."""

from __future__ import annotations

import logging
import math
import random
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from coco.behaviour.commands import parse_command
from coco.behaviour.director import Director, Situation
from coco.behaviour.mood import Mood
from coco.behaviour.petting import PetDetector
from coco.behaviour.physics import Body
from coco.behaviour.states import (
    DONE,
    Airborne,
    Dragged,
    Emote,
    Fall,
    Jump,
    Land,
    Loop,
    OneShot,
    PeekHide,
    PeekIn,
    Purr,
    Sit,
    Sleep,
    Stalk,
    State,
    Sulk,
    Talk,
    Think,
    WakeUp,
    Walk,
)
from coco.behaviour.surfaces import find_support
from coco.behaviour.world import WorldSnapshot
from coco.core.anims import SpriteManifest
from coco.core.bus import EventBus
from coco.core.catdef import CatDef
from coco.core.config import Config
from coco.core.events import Event, Topic
from coco.core.geometry import Point, Surface

log = logging.getLogger(__name__)

StepFactory = Callable[[], State | None]


@dataclass(frozen=True)
class CatPose:
    """Everything the UI needs to draw one frame of Coco."""

    x: float  # paw anchor, physical px
    y: float
    anim: str
    frame: int
    facing: int  # -1 = left (as drawn), +1 = right (mirrored)
    visible: bool = True
    click_through: bool = False
    state: str = ""
    moving: bool = False  # True -> UI should tick at full frame rate
    next_frame_in: float = math.inf  # s until the frame changes (when not moving)
    mood_icon: str | None = None  # "heart" | "zzz" | "exclaim" | "question"


class BehaviourEngine:
    def __init__(
        self,
        cfg: Config,
        cat: CatDef,
        manifest: SpriteManifest,
        *,
        rng: random.Random | None = None,
        bus: EventBus | None = None,
        mood: Mood | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg = cfg
        self.cat = cat
        self.manifest = manifest
        self.rng = rng or random.Random()
        self.bus = bus
        self.mood = mood or Mood(cfg=cfg.mood)
        self.clock = clock  # wall clock for mouse timing (petting, throws)
        self.director = Director(cfg.behaviour, self.rng, cat.behaviour_weights)
        self.petter = PetDetector(cfg.petting)

        self.body = Body(0.0, 0.0)
        self.facing = -1
        self.surface: Surface | None = None  # what she's standing on (None = in the air)
        self.world: WorldSnapshot | None = None
        self.state: State | None = None
        self.plan: deque[StepFactory] = deque()
        self.now = 0.0  # engine time: sum of all dt
        self._off_since: float | None = None  # when she was first seen off her monitor
        self.anim = "sit"
        self.anim_t = 0.0
        self.size_logical = 128  # frame display size at 100 % scaling (S/M/L)
        self.paused = False  # "Pause wandering"
        self.focusing = False  # focus mode: she guards the taskbar (stays put, no hunting)
        self.drag_released = False
        self.click_through_until = -math.inf
        # --- chat
        self.chat_enabled = True  # False = clicks just make her look (no AI)
        self.chatting = False  # bubble open: she stays put
        self.chat_request = 0  # newest question id; older replies are ignored
        self.reply_final = False  # Talk ends when the final reply arrives
        self._icon: str | None = None  # short-lived mood icon...
        self._icon_until = -math.inf  # ...and when it disappears
        self._events: list[Event] = []
        self._warned_missing: set[str] = set()
        self.last_command: tuple[str, bool, str] | None = None  # (action, ok, reason)
        self.voice_on = False  # her voice is enabled (talk waits for the audio)
        self.speaking = False  # audio is playing right now
        self.listening = False  # the mic is open (push-to-talk / after "Hey Coco")
        self._base_frame_w = manifest["sit"].frame_w if "sit" in manifest.anims else 256

    # --- sizes & units
    @property
    def k(self) -> float:
        """Physical px per logical px on her monitor (1.25 at 125 %)."""
        return self.world.scale if self.world else 1.0

    def px(self, logical: float) -> float:
        return logical * self.k

    @property
    def frame_px(self) -> float:
        return self.size_logical * self.k

    @property
    def px_per_src(self) -> float:
        return self.frame_px / self._base_frame_w

    @property
    def half_w(self) -> float:
        return self.cat.collision_width * self.frame_px / 2

    @property
    def height(self) -> float:
        return self.cat.collision_height * self.frame_px

    def gait(self, speed_at_m: float) -> float:
        """Walking/running speed in physical px/s. Config speeds are for size M (128 px);
        a bigger or smaller Coco takes bigger or smaller steps, so her speed scales with
        her size too. Otherwise her paws would slide over the ground at S and L."""
        return self.px(speed_at_m) * self.size_logical / 128

    def set_art(self, cat: CatDef, manifest: SpriteManifest) -> None:
        """New cat.yaml / sprites while running (tray: Reload config + art)."""
        self.cat = cat
        self.manifest = manifest
        self.director.weights = {**self.cfg.behaviour.weights, **cat.behaviour_weights}
        self._base_frame_w = manifest["sit"].frame_w if "sit" in manifest.anims else 256
        self._warned_missing.clear()
        if self.anim not in manifest.anims:  # the animation she's in was removed
            self.play("sit")

    def set_size(self, logical_px: int) -> None:
        self.size_logical = logical_px

    # --- helpers for states
    def rand_range(self, pair: list[float] | tuple[float, float]) -> float:
        lo, hi = pair[0], pair[-1]
        return self.rng.uniform(lo, hi)

    def play(self, anim: str) -> None:
        if anim not in self.manifest.anims:
            if anim not in self._warned_missing:  # say it once, not on every chat
                self._warned_missing.add(anim)
                log.info("Animation %r isn't in this cat's art yet: showing 'sit'", anim)
            anim = "sit"
        self.anim = anim
        self.anim_t = 0.0

    def anim_finished(self) -> bool:
        spec = self.manifest[self.anim]
        return not spec.loop and self.anim_t >= spec.duration

    def face_toward(self, x: float) -> None:
        if abs(x - self.body.x) > 1:
            self.facing = 1 if x > self.body.x else -1

    def grab_offset(self) -> tuple[float, float]:
        """Where her paw anchor sits relative to the mouse while she dangles."""
        spec = self.manifest["dangle"]
        gx, gy = spec.grab or (spec.anchor[0], int(spec.frame_h * 0.2))
        dx = (spec.anchor[0] - gx) * self.px_per_src
        if self.facing > 0:
            dx = -dx  # mirrored art
        return dx, (spec.anchor[1] - gy) * self.px_per_src

    def is_sleepy(self) -> bool:
        w = self.world
        if w is None:
            return False
        return (
            self.director.is_night(w.hour)
            or w.user_idle >= self.cfg.behaviour.user_idle_sleepy_after
        )

    def land_on(self, s: Surface) -> State:
        self.body.y = s.y
        self.body.vx = self.body.vy = 0.0
        self.surface = s
        return Land(self)

    # --- lifecycle
    def start(self, world: WorldSnapshot) -> None:
        """Place Coco: in her bed (waking up) or sitting on the ground."""
        self.world = world
        self.plan.clear()
        bed = next((s for s in world.surfaces if s.kind == "bed"), None)
        if self.cfg.behaviour.start_in_bed and bed is not None and world.bed:
            self.body.x, self.body.y = world.bed.spot.x, world.bed.spot.y
            self.surface = bed
            self.facing = world.bed.outside
            self._switch(WakeUp(self))
        else:
            g = world.ground
            self.body.x, self.body.y = (g.x1 + g.x2) / 2, g.y
            self.surface = g
            self._switch(Sit(self))

    def handle(self, event: Event) -> None:
        """Bus handler: queue events; they're processed at the start of the next tick."""
        self._events.append(event)

    def tick(self, dt: float, world: WorldSnapshot) -> CatPose:
        self.world = world
        dt = max(0.0, min(dt, 0.1))  # after a stall (sleep/hibernate) don't jump ahead
        if self.state is None:
            self.start(world)
        self.now += dt
        self.anim_t += dt
        self.petter.scale = world.scale

        self._process_events()
        self.mood.update(
            dt,
            self.is_sleepy(),
            asleep=isinstance(self.state, Sleep),
            hour=world.hour,
            user_active=world.user_idle < 120,
        )
        self._apply(self.state.update(dt))
        self._check_support()
        self._check_on_screen()
        return self.pose()

    def pose(self) -> CatPose:
        spec = self.manifest[self.anim]
        st = self.state
        return CatPose(
            x=self.body.x,
            y=self.body.y,
            anim=self.anim,
            frame=spec.frame_at(self.anim_t),
            facing=self.facing,
            visible=st.visible if st else True,
            click_through=self.now < self.click_through_until,
            state=st.describe() if st else "",
            moving=bool(st and st.moving),
            next_frame_in=spec.time_to_next_frame(self.anim_t),
            mood_icon=self.mood_icon(),
        )

    # --- mood icons
    def show_icon(self, icon: str | None, seconds: float = 2.5) -> None:
        self._icon, self._icon_until = icon, self.now + seconds

    def mood_icon(self) -> str | None:
        """A short-lived icon (heart after petting...) wins over the state's own one
        (zzz while sleeping, ? while thinking)."""
        if self.listening:
            return "ear"  # privacy: always visible while the microphone is open
        if self._icon and self.now < self._icon_until:
            return self._icon
        return getattr(self.state, "icon", None)

    @property
    def support_hwnd(self) -> int | None:
        """Window she stands on (sensors re-check that one every frame)."""
        s = self.surface
        return s.id if s is not None and s.kind == "window" else None  # type: ignore[return-value]

    # --- state switching
    def _switch(self, new: State, clear_plan: bool = False) -> None:
        old = self.state
        if old is not None:
            old.exit()
        if clear_plan:
            self.plan.clear()
        self.state = new
        new.enter()
        old_name = old.describe() if old else "-"
        log.debug("state %s -> %s", old_name, new.describe())
        if self.bus is not None:
            self.bus.publish(Event(Topic.STATE_CHANGED, {"old": old_name, "new": new.name}))

    def _interrupt(self, new: State) -> None:
        self._switch(new, clear_plan=True)

    def _apply(self, result: State | str | None) -> None:
        if result is None:
            return
        if result == DONE:
            self._switch(self._next_from_plan())
        elif isinstance(result, State):
            self._switch(result)

    def _next_from_plan(self) -> State:
        while self.plan:
            step = self.plan.popleft()()
            if step is not None:
                return step
        return Sit(self)

    def _check_support(self) -> None:
        st, w, b = self.state, self.world, self.body
        if st is None or not st.grounded or self.surface is None:
            return
        tol = self.px(self.cfg.sensors.support_tolerance)
        s = find_support(w.surfaces, self.surface.id, b.x, b.y, tol)
        if s is None:
            log.info("Support lost under Coco (%s) -> fall", self.surface.id)
            self._interrupt(Fall(self, vx=b.vx * 0.5, vy=0.0))
        else:
            self.surface = s
            b.y = s.y

    def _check_on_screen(self) -> None:
        """If her monitor changed (unplugged, resolution switch) and she's lost, reset."""
        w, b = self.world, self.body
        m = w.monitor
        margin = self.frame_px * 1.5
        if isinstance(self.state, Dragged):
            self._off_since = None
            return
        if (
            m.left - margin <= b.x <= m.right + margin
            and m.top - margin * 3 <= b.y <= m.bottom + margin
        ):
            self._off_since = None
            return
        # A display change (screen off, resolution flip, waking up) can report a tiny fake
        # monitor for a moment: only respawn if she's still off it 1.5 s later.
        if self._off_since is None:
            self._off_since = self.now
        elif self.now - self._off_since >= 1.5:
            log.warning("Coco was off her monitor at (%.0f, %.0f), monitor %s; respawning",
                        b.x, b.y, (m.left, m.top, m.right, m.bottom))  # fmt: skip
            self._off_since = None
            self.start(w)

    # --- events
    def _process_events(self) -> None:
        events, self._events = self._events, []
        for ev in events:
            st = self.state
            if ev.topic == Topic.DRAG_START:
                self.drag_released = False
                if not isinstance(st, Dragged):
                    self._interrupt(Dragged(self))
            elif ev.topic == Topic.DRAG_END:
                self.drag_released = True
            elif ev.topic == Topic.CLICK:
                self._on_click()
            elif ev.topic == Topic.HOVER:
                p = ev.payload
                if self.petter.add_sample(ev.timestamp, p.x, p.y):
                    self._on_pet()
            elif ev.topic == Topic.CHAT_OPEN:
                self._on_chat_open()
            elif ev.topic == Topic.CHAT_CLOSE:
                self.chatting = False
            elif ev.topic == Topic.CHAT_ASK:
                self.chatting = True
                action = parse_command(ev.payload or "")
                if action is not None:  # "run!", "go to sleep": do it, no AI needed
                    self._do_command(action)
                else:
                    self.reply_final = False
                    self._act(Think(self))
            elif ev.topic == Topic.BRAIN_STATUS:
                status = ev.payload
                self.chat_request = max(self.chat_request, status.request_id)
            elif ev.topic == Topic.BRAIN_REPLY:
                self._on_reply(ev.payload)
            elif ev.topic == Topic.LISTEN_START:
                self.listening = True
                self._on_chat_open()  # stop and listen (wakes her if she's asleep)
            elif ev.topic == Topic.LISTEN_END:
                self.listening = False
                if not (ev.payload or {}).get("cancelled"):
                    self._act(Think(self))  # working out what you said
            elif ev.topic == Topic.VOICE_START:
                self.speaking = True
            elif ev.topic == Topic.VOICE_END:
                self.speaking = False
            elif ev.topic == Topic.CHAT_SAY:  # canned line (no AI)
                line = ev.payload or {}
                if line.get("animate", True):  # (command replies: she's busy moving)
                    seconds = 0.8 + len(line.get("text", "")) / 40
                    self._act(Talk(self, duration=seconds), then=line.get("emotion"))

    def _on_click(self) -> None:
        st = self.state
        if isinstance(st, Sleep):
            self._interrupt(WakeUp(self))
        elif not self.chat_enabled and st is not None and st.clickable:
            # Without the AI brain a click just makes her look at you. With it, the
            # app answers the click with chat.open (see _on_chat_open).
            self.face_toward(self.world.cursor.x)
            self._interrupt(OneShot(self, "look_around"))

    # --- chat
    def _can_act(self) -> bool:
        """Can a chat reaction take over right now? Not mid-air, while held, while
        hidden past the screen edge, or while walking somewhere in the middle of a plan."""
        st = self.state
        if st is None or isinstance(st, Dragged | Airborne) or not st.visible:
            return False
        return st.grounded

    def _act(self, state: State, then: str | None = None) -> None:
        """Switch to a chat state (dropping any plan); `then` = emotion to show after."""
        if not self._can_act():
            return
        self._interrupt(state)
        if then:
            self.plan.append(lambda e=then: Emote(self, e))

    def _on_chat_open(self) -> None:
        self.chatting = True
        st = self.state
        if isinstance(st, Sleep):
            self._interrupt(WakeUp(self))
        elif st is not None and st.moving and self._can_act():
            self._interrupt(Sit(self))  # stop walking/stalking to listen

    def _on_reply(self, reply) -> None:
        if reply.request_id and reply.request_id < self.chat_request:
            return  # answer to a question you already replaced
        self.chat_request = max(self.chat_request, reply.request_id)
        if not reply.final:
            if isinstance(self.state, Think):
                self._act(Talk(self))
            return
        self.reply_final = True
        emotion = reply.emotion or "neutral"
        if hasattr(self.mood, "on_chat"):
            self.mood.on_chat(emotion)
        action = getattr(reply, "action", "none")
        if action and action != "none":  # the AI understood a request: do it
            self._do_command(action, announce=False)
            return
        icon = {
            "love": "heart",
            "happy": "heart",
            "surprised": "exclaim",
            "curious": "question",
            "sleepy": "zzz",
        }.get(emotion)
        if icon:
            self.show_icon(icon)
        if isinstance(self.state, Talk):
            # Keep talking while the bubble types the rest out, then show the emotion.
            self.state.finish_in(min(len(reply.text) / 40, 6.0), emotion)
        else:
            self._act(Emote(self, emotion))

    def _on_pet(self) -> None:
        self.mood.on_pet()
        self.show_icon("heart")
        if self.bus is not None:
            self.bus.publish(Event(Topic.PETTED))
        st = self.state
        if st is not None and st.pettable:
            self._interrupt(Purr(self))

    # --- decisions
    def situation(self) -> Situation:
        kind = self.surface.kind if self.surface else "ground"
        lo, hi = self._walk_range()
        min_d = self.px(self.cfg.movement.min_walk_distance)
        x = self.body.x
        return Situation(
            surface_kind=kind,
            can_walk=kind != "bed" and (x - lo >= min_d or hi - x >= min_d),
            can_jump_up=kind != "bed" and self._jump_up_target() is not None,
            can_peek=kind == "ground" and bool(self._peek_edges()),
            can_stalk=kind != "bed" and self.cursor_in_play_range(),
            has_bed=self.world.bed is not None,
            sleepy=self.is_sleepy(),
            can_sulk=self.mood.should_sulk(),
            paused=self.paused or self.chatting or self.focusing,  # she stays put
        )

    def decide(self) -> State:
        """Ask the Director for the next activity; returns its first state."""
        activity = self.director.choose(self.now, self.situation(), self.mood.weight_multipliers())
        log.debug("decided: %s", activity)
        self.plan.clear()
        return self._start_activity(activity)

    def force(self, activity: str) -> None:
        """Start an activity right now (tray "Make Coco..." menu, tests, tools).

        Skips the Director's dice and cooldowns, but not physics: if the activity is
        impossible right now (no window to jump on, cursor too far to stalk) she just sits.
        """
        if self.state is None or isinstance(self.state, Dragged):
            return
        sit = self.situation()
        if not self.director.allowed(activity, sit):
            log.info(
                "can't %s right now (on %s, paused=%s)", activity, sit.surface_kind, sit.paused
            )
            return
        log.info("forced activity: %s", activity)
        self.plan.clear()  # clear first: _start_activity may queue follow-up steps
        self.director.last_used[activity] = self.now
        self._switch(self._start_activity(activity))

    # --- commands
    def command(self, action: str) -> tuple[bool, str]:
        """Do what you asked ("run", "sleep", "come_here"...; see behaviour/commands.py).

        Unlike force(), a command works while wandering is paused or the chat is open
        (you explicitly asked), and it chains the steps needed to get there: "run" while
        she's asleep in bed = wake up -> hop out -> run.
        Returns (done, reason); `reason` explains a refusal ("held", "no_window", ...).
        """
        st = self.state
        if st is None:
            return False, "busy"
        if isinstance(st, Dragged):
            return False, "held"
        if isinstance(st, Airborne) or not st.visible:
            return False, "busy"
        in_bed = self.surface is not None and self.surface.kind == "bed"
        asleep = isinstance(st, Sleep)

        if action == "stop":
            self._interrupt(Sit(self))
            return True, ""
        if action == "sleep":
            if asleep:
                return False, "already_asleep"
            if self.world.bed is None:
                return False, "no_bed"
            return self._run_steps([lambda: Sleep(self) if in_bed else self._go_home()])
        if action == "get_up":
            if not (in_bed or asleep):
                return False, "already_up"
            steps = [lambda: WakeUp(self)] if asleep else []
            return self._run_steps([*steps, self._hop_out] if in_bed else steps)

        floor_actions = {"run", "walk", "jump_up", "come_here", "peek", "play", "stretch"}
        steps: list[StepFactory] = []
        if asleep:
            steps.append(lambda: WakeUp(self))
        if in_bed and action in floor_actions:
            steps.append(self._hop_out)

        if action in ("run", "walk"):
            steps.append(lambda: self._walk_somewhere(run=action == "run"))
        elif action == "jump_up":
            if not in_bed and self._jump_up_target() is None:
                return False, "no_window"
            steps.append(self._jump_up)
        elif action == "come_here":
            if not self.world.monitor.contains(self.world.cursor.x, self.world.cursor.y):
                return False, "cant_see_you"
            steps.append(self._come_here)
        elif action == "peek":
            on_window = self.surface is not None and self.surface.kind == "window"
            if on_window or not self._peek_edges():
                return False, "no_edge"
            steps.append(self._peek)
        elif action == "play":
            steps.append(self._play)
        elif action in ("stretch", "groom", "yawn", "loaf"):
            steps.append(lambda: self._start_activity(action))
        else:
            return False, "unknown"
        return self._run_steps(steps)

    def _do_command(self, action: str, announce: bool = True) -> None:
        """Run a command and tell the app how it went (it picks her reply line)."""
        ok, reason = self.command(action)
        self.last_command = (action, ok, reason)
        log.info("command %s -> %s %s", action, "ok" if ok else "refused", reason)
        if ok and hasattr(self.mood, "on_chat"):
            self.mood.on_chat("neutral")  # you paid attention to her
        if announce and self.bus is not None:
            self.bus.publish(
                Event(Topic.COMMAND_DONE, {"action": action, "ok": ok, "reason": reason})
            )

    def _run_steps(self, steps: list[StepFactory]) -> tuple[bool, str]:
        """Start the first step now; the rest wait in the plan."""
        if not steps:
            return False, "nothing_to_do"
        self.plan.clear()
        self.plan.extend(steps[1:])
        first = steps[0]()
        self._switch(first if first is not None else self._next_from_plan())
        return True, ""

    def _come_here(self) -> State:
        """Walk (or run, if it's far) to just below your mouse cursor."""
        lo, hi = self._walk_range()
        target = min(max(self.world.cursor.x, lo), hi)
        far = abs(target - self.body.x) > self.px(400)
        return Walk(self, target, run=far)

    def _play(self) -> State:
        """Go over to the cursor if it's far, then stalk and pounce on it."""
        if self.cursor_in_play_range():
            return Stalk(self)
        lo, hi = self._walk_range()
        c = self.world.cursor.x
        side = 1 if self.body.x > c else -1  # stop on her side of the cursor...
        target = min(max(c + side * self.px(260), lo), hi)  # ...with room to pounce
        self.plan.appendleft(lambda: Stalk(self))
        return Walk(self, target, run=abs(target - self.body.x) > self.px(400))

    def _start_activity(self, activity: str) -> State:
        bc = self.cfg.behaviour
        dur = bc.durations
        if activity in ("loaf", "groom"):
            return Loop(self, activity, self.rand_range(dur.get(activity, [5, 10])))
        if activity in ("yawn", "stretch", "look_around"):
            if activity == "look_around" and self.rng.random() < 0.5:
                self.facing = -self.facing
            return OneShot(self, activity)
        if activity in ("walk", "run"):
            return self._walk_somewhere(run=activity == "run")
        if activity == "go_home":
            return self._go_home()
        if activity == "jump_up":
            return self._jump_up()
        if activity == "peek":
            return self._peek()
        if activity == "stalk":
            return Stalk(self)
        if activity == "sleep":
            return Sleep(self)
        if activity == "sulk":
            return Sulk(self, self.rng.uniform(20, 40))
        if activity == "hop_out":
            return self._hop_out()
        return Sit(self)

    # --- walking
    def _walk_range(self) -> tuple[float, float]:
        """Where her paws may go on the current surface (keeps clear of the bed)."""
        s = self.surface or self.world.ground
        margin = self.half_w * 0.3
        lo, hi = s.x1 + margin, s.x2 - margin
        bk = self.world.bed
        if s.kind == "ground" and bk is not None:
            if bk.outside < 0:
                hi = min(hi, bk.rect.left - self.half_w)
            else:
                lo = max(lo, bk.rect.right + self.half_w)
        return lo, max(lo, hi)

    def _walk_somewhere(self, run: bool) -> State:
        s = self.surface or self.world.ground
        x = self.body.x
        mv = self.cfg.movement
        if (
            s.kind == "window"
            and not run
            and self.rng.random() < self.cfg.behaviour.drop_off_window_chance
        ):
            # walk off one end of the window and hop down
            end = s.x1 - self.half_w if self.rng.random() < 0.5 else s.x2 + self.half_w
            return Walk(self, end, allow_drop=True)
        lo, hi = self._walk_range()
        dist = self.px(self.rng.uniform(mv.min_walk_distance, mv.max_walk_distance))
        options = [t for t in (x - dist, x + dist) if lo <= t <= hi]
        if not options:  # not enough room for that distance: go to the farther end
            options = [lo if x - lo > hi - x else hi]
        return Walk(self, self.rng.choice(options), run=run)

    # --- going home
    def _bed_side_x(self) -> float:
        bk = self.world.bed
        if bk.outside < 0:
            return bk.rect.left - self.half_w * 0.6
        return bk.rect.right + self.half_w * 0.6

    def _over_rim(self) -> float:
        """Jump height (apex_extra) that clears the bed rim from bed level."""
        bk = self.world.bed
        return bk.spot.y - bk.rect.top + self.px(12)

    def _go_home(self) -> State:
        bk = self.world.bed
        s = self.surface
        if bk is None:
            return Sit(self)
        if s is not None and s.kind == "bed":
            return Sleep(self)

        def walk_to_side() -> State | None:
            target = self._bed_side_x()
            if abs(target - self.body.x) < self.px(8):
                return None
            lo, hi = (self.surface.x1, self.surface.x2) if self.surface else (target, target)
            return Walk(
                self, min(max(target, lo), hi), run=abs(target - self.body.x) > self.px(700)
            )

        def jump_in() -> State | None:
            b = self.world.bed
            return Jump(self, Point(b.spot.x, b.spot.y), apex_extra=self._over_rim()) if b else None

        def sleep_if_home() -> State | None:
            return Sleep(self) if self.surface is not None and self.surface.kind == "bed" else None

        self.plan.extend([walk_to_side, jump_in, sleep_if_home])
        if s is not None and s.kind == "window":
            # first walk off the nearer end of this window and hop down
            end = (
                s.x1 - self.half_w
                if self.body.x - s.x1 < s.x2 - self.body.x
                else s.x2 + self.half_w
            )
            return Walk(self, end, allow_drop=True)
        return self._next_from_plan()

    def _hop_out(self) -> State:
        bk = self.world.bed
        if bk is None:
            return Sit(self)
        target = Point(self._bed_side_x() + bk.outside * self.half_w, self.world.ground.y)
        return Jump(self, target, apex_extra=self._over_rim())

    # --- jumping onto the active window
    def _jump_up_target(self) -> Point | None:
        """Best spot on a window top above her: the active window if it's reachable,
        otherwise the nearest other window top in reach."""
        w = self.world
        if w.foreground is not None:
            target = self._best_spot_on([s for s in w.surfaces if s.id == w.foreground])
            if target is not None:
                return target
        return self._best_spot_on([s for s in w.surfaces if s.kind == "window"])

    def _best_spot_on(self, segments: list[Surface]) -> Point | None:
        b = self.body
        mv = self.cfg.movement
        best: Point | None = None
        for s in segments:
            if s.width < self.half_w * 2:
                continue
            if self.surface is not None and s.id == self.surface.id:
                continue
            rise = b.y - s.y
            if rise < self.height * 0.6 or rise > self.px(mv.jump_max_height):
                continue
            tx = min(max(b.x, s.x1 + self.half_w), s.x2 - self.half_w)
            if best is None or abs(tx - b.x) < abs(best.x - b.x):
                best = Point(tx, s.y)
        return best

    def _jump_up(self) -> State:
        target = self._jump_up_target()
        if target is None:
            return Sit(self)
        if abs(target.x - self.body.x) > self.px(260):
            # walk closer first, then jump (target recomputed when the jump starts)
            lo, hi = self._walk_range()
            near = target.x - (1 if target.x > self.body.x else -1) * self.px(140)
            self.plan.append(lambda: Jump(self, t) if (t := self._jump_up_target()) else None)
            return Walk(self, min(max(near, lo), hi), run=True)
        return Jump(self, target)

    # --- peeking
    def _peek_edges(self) -> list[int]:
        """Screen edges she can disappear behind: real outer edges, not where the bed
        is, and not an edge shared with another monitor (she'd pop up over there)."""
        w = self.world
        edges = []
        tol = 2
        if (
            abs(w.work.left - w.monitor.left) <= tol
            and abs(w.monitor.left - w.virtual.left) <= tol
            and not (w.bed and w.bed.outside > 0)
        ):
            edges.append(-1)
        if (
            abs(w.work.right - w.monitor.right) <= tol
            and abs(w.monitor.right - w.virtual.right) <= tol
            and not (w.bed and w.bed.outside < 0)
        ):
            edges.append(1)
        return edges

    def _peek(self) -> State:
        edges = self._peek_edges()
        if not edges:
            return Sit(self)
        side = self.rng.choice(edges)
        w = self.world
        edge_x = w.work.right if side > 0 else w.work.left
        hidden_x = edge_x + side * self.frame_px * 0.6
        back_x = edge_x - side * self.frame_px * 1.2
        hide = self.rand_range(self.cfg.behaviour.durations.get("peek_hide", [3, 8]))
        self.plan.extend(
            [
                lambda: PeekHide(self, hide),
                lambda: PeekIn(self, side),
                lambda: Walk(self, back_x, free=True),
            ]
        )
        far = abs(hidden_x - self.body.x) > self.px(500)
        return Walk(self, hidden_x, run=far, free=True)

    # --- cursor play
    def cursor_in_play_range(self, slack: float = 1.0) -> bool:
        s, c, b = self.surface, self.world.cursor, self.body
        if s is None or s.kind == "bed":
            return False
        cp = self.cfg.cursor_play
        return (
            abs(c.x - b.x) <= self.px(cp.reach) * slack
            and s.y - self.px(cp.max_height) * slack <= c.y <= s.y + self.px(5)
            and s.x1 - self.half_w <= c.x <= s.x2 + self.half_w
        )
