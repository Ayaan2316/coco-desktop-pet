"""State machine scenarios, run headless on a fake desktop (see conftest.Sim)."""

from coco.core.events import Topic
from coco.core.geometry import Point, Rect
from coco.desktop.base import WindowInfo
from tests.conftest import GROUND_Y, Sim


def test_starts_in_bed_waking_up_then_sits():
    sim = Sim(start_in_bed=True)
    assert sim.state == "wake_up"
    assert sim.eng.surface.kind == "bed"
    assert sim.run(3, until=lambda s: s.state == "sit")
    assert sim.eng.surface.kind == "bed"


def test_starts_on_ground_without_bed_start():
    sim = Sim()
    assert sim.state == "sit"
    assert sim.eng.body.y == GROUND_Y


def test_drag_dangle_throw_bounce_and_land_on_feet():
    sim = Sim()
    sim.send(Topic.DRAG_START)
    sim.fake.cursor = Point(900, 600)
    sim.step()
    assert sim.state == "dangle"
    assert not sim.pose.click_through
    # she hangs below the cursor (grabbed by the scruff)
    # she hangs by the scruff: paws = cursor + grab offset from the manifest
    ox, oy = sim.eng.grab_offset()
    assert oy > 0 and sim.eng.body.y == 600 + oy and sim.eng.body.x == 900 + ox

    # fling her to the left: 40 px per frame = 2400 px/s
    for i in range(10):
        sim.fake.cursor = Point(900 - 40 * (i + 1), 600)
        sim.step()
    sim.send(Topic.DRAG_END)
    sim.step()
    assert sim.state == "fall"
    assert sim.eng.body.vx < -1500

    min_left, bounced = 1e9, False
    for _ in range(600):
        sim.step()
        min_left = min(min_left, sim.eng.body.x - sim.eng.half_w)
        bounced |= sim.eng.body.vx > 0
        if sim.state == "land":
            break
    assert bounced, "should bounce off the left screen edge"
    assert min_left >= 0, "never leaves the screen"
    assert sim.state == "land"
    assert sim.eng.body.y == GROUND_Y and sim.eng.surface.kind == "ground"
    assert sim.run(2, until=lambda s: s.state == "sit")


def test_thrown_up_hits_the_ceiling_and_comes_down():
    sim = Sim()
    sim.send(Topic.DRAG_START)
    sim.fake.cursor = Point(900, 400)
    sim.step()
    for i in range(6):
        sim.fake.cursor = Point(900, 400 - 60 * (i + 1))  # flick straight up
        sim.step()
    sim.send(Topic.DRAG_END)
    top, hit_ceiling = 1e9, False
    for _ in range(5 * 60):
        sim.step()
        top = min(top, sim.eng.body.y - sim.eng.height)
        hit_ceiling |= top <= 15  # bounced off the top edge (mid-frame)
        if sim.state == "land":
            break
    assert top >= 0, "her head never leaves the top of the screen"
    assert hit_ceiling
    assert sim.state == "land" and sim.eng.body.y == GROUND_Y


def test_falls_when_her_window_closes():
    win = WindowInfo(1, Rect(400, 500, 1200, 900), "Editor")
    sim = Sim([win])
    sim.stand_on(1, 800)
    assert sim.eng.support_hwnd == 1
    sim.run(1)
    assert sim.state == "sit"  # still standing
    sim.fake.close_window(1)
    sim.step()
    assert sim.state == "fall"
    assert sim.run(3, until=lambda s: s.state == "land")
    assert sim.eng.surface.kind == "ground"


def test_falls_when_window_moves_and_lands_on_its_new_top():
    sim = Sim([WindowInfo(1, Rect(400, 500, 1200, 900))])
    sim.stand_on(1, 800)
    sim.fake.move_window(1, Rect(400, 580, 1200, 980))  # dragged 80 px down
    sim.step()
    assert sim.state == "fall"
    assert sim.run(3, until=lambda s: s.state == "land")
    assert sim.eng.surface.id == 1 and sim.eng.body.y == 580


def test_falls_when_another_window_covers_her_spot():
    sim = Sim([WindowInfo(1, Rect(400, 500, 1200, 900))])
    sim.stand_on(1, 800)
    cover = WindowInfo(2, Rect(700, 300, 1000, 800))  # dragged in front, over her spot
    sim.fake.windows = [cover, *sim.fake.windows]
    sim.run(1.0, until=lambda s: s.state == "fall")  # noticed on the next window scan
    assert sim.state == "fall"


def test_tiny_window_jitter_does_not_drop_her():
    sim = Sim([WindowInfo(1, Rect(400, 500, 1200, 900))])
    sim.stand_on(1, 800)
    sim.fake.move_window(1, Rect(400, 501, 1200, 901))  # 1 px (DPI rounding)
    sim.run(0.5)
    assert sim.state == "sit" and sim.eng.body.y == 501


def test_jumps_onto_the_active_window():
    sim = Sim([WindowInfo(7, Rect(500, 600, 1300, 1000))], foreground=7)
    sim.eng.force("jump_up")
    assert sim.state == "jump"
    assert sim.run(4, until=lambda s: s.state == "land")
    assert sim.eng.surface.id == 7 and sim.eng.body.y == 600


def test_jump_up_falls_back_to_another_reachable_window():
    sim = Sim([WindowInfo(7, Rect(500, 600, 1300, 1000))], foreground=None)
    sim.eng.force("jump_up")
    assert sim.run(4, until=lambda s: s.state == "land")
    assert sim.eng.surface.id == 7


def test_walking_off_a_window_end_hops_down():
    from coco.behaviour.states import Walk

    sim = Sim([WindowInfo(1, Rect(400, 500, 1200, 900))])
    sim.stand_on(1, 1100)
    sim.eng._switch(Walk(sim.eng, 1300, allow_drop=True), clear_plan=True)
    assert sim.run(5, until=lambda s: s.state == "fall")
    assert sim.run(3, until=lambda s: s.state == "land")
    assert sim.eng.surface.kind == "ground"


def test_go_home_from_a_window_ends_asleep_in_the_bed():
    sim = Sim([WindowInfo(1, Rect(400, 500, 1200, 900))])
    sim.stand_on(1, 800)
    sim.eng.force("go_home")
    assert sim.run(60, until=lambda s: s.state == "sleep")
    assert sim.eng.surface.kind == "bed"
    assert {"walk", "fall", "land", "jump"} <= set(sim.seen)


def test_go_home_from_off_screen_walks_back_in():
    from coco.behaviour.states import PeekHide

    sim = Sim()
    sim.eng._switch(PeekHide(sim.eng, 100), clear_plan=True)  # hiding past the left edge
    sim.eng.body.x = -80
    sim.step()
    sim.eng.force("go_home")
    sim.run(1)
    assert sim.eng.body.x > -80 + 50  # actually walking, not stuck at the edge
    assert sim.run(60, until=lambda s: s.state == "sleep")


def test_sleep_click_wakes_her():
    sim = Sim(start_in_bed=True)
    sim.run(3, until=lambda s: s.state == "sit")
    sim.eng.force("sleep")
    assert sim.state == "sleep"
    sim.send(Topic.CLICK, Point(0, 0))
    sim.step()
    assert sim.state == "wake_up"


def test_hop_out_of_the_bed():
    sim = Sim(start_in_bed=True)
    sim.run(3, until=lambda s: s.state == "sit")
    sim.eng.force("hop_out")
    assert sim.run(4, until=lambda s: s.state == "land")
    assert sim.eng.surface.kind == "ground"


def test_petting_makes_her_purr_then_stop():
    sim = Sim()
    x, direction = sim.eng.body.x, 1
    purred = False
    for i in range(60):  # 2 s of gentle rubbing at 600 px/s, back and forth
        if i and i % 3 == 0:
            direction = -direction
        x += direction * 20
        sim.send(Topic.HOVER, Point(x, GROUND_Y - 40))
        sim.step(1 / 30)
        purred |= sim.state == "purr"
    assert purred
    assert sim.run(4, until=lambda s: s.state == "sit")  # stops after the linger time


def test_click_makes_her_look_when_the_brain_is_off():
    sim = Sim()
    sim.eng.chat_enabled = False
    sim.send(Topic.CLICK, Point(sim.eng.body.x + 50, GROUND_Y - 40))
    sim.step()
    assert sim.state == "look_around"


def test_click_with_brain_on_leaves_it_to_the_chat_bubble():
    sim = Sim()
    sim.send(Topic.CLICK, Point(sim.eng.body.x + 50, GROUND_Y - 40))
    sim.step()
    assert sim.state == "sit"


def test_stalk_and_pounce_on_the_cursor():
    sim = Sim(cursor=Point(1200, 900))
    sim.eng.force("stalk")
    assert sim.state == "stalk"
    click_through_seen = False
    pounced = False
    for _ in range(int(12 * 60)):
        sim.step()
        if sim.state == "pounce":
            pounced = True
            click_through_seen |= sim.pose.click_through
        if pounced and sim.state == "sit":
            break
    assert pounced and click_through_seen
    assert sim.eng.surface.kind == "ground"


def test_stalk_gives_up_when_cursor_leaves():
    sim = Sim(cursor=Point(1200, 900))
    sim.eng.force("stalk")
    sim.fake.cursor = Point(100, 100)  # far away, high up
    sim.step()
    assert sim.state == "sit"


def test_peek_hides_then_peeks_in_from_the_left_edge():
    sim = Sim()
    sim.eng.force("peek")
    assert sim.run(30, until=lambda s: s.state == "peek_hide")
    assert not sim.pose.visible
    assert sim.run(10, until=lambda s: s.state == "peek")
    assert sim.pose.visible and sim.eng.body.x < 0 and sim.eng.facing == 1
    assert sim.run(20, until=lambda s: s.state == "sit")
    assert sim.eng.body.x > 0 and sim.eng.body.y == GROUND_Y


def test_pause_wandering_refuses_moving_activities():
    sim = Sim()
    sim.eng.paused = True
    sim.eng.force("walk")
    assert sim.state == "sit"
    for _ in range(50):
        assert sim.eng.director.choose(sim.eng.now + 1000, sim.eng.situation()) not in (
            "walk",
            "run",
            "jump_up",
            "peek",
            "stalk",
            "go_home",
        )


def test_she_wanders_on_her_own():
    sim = Sim(idle_forever=False, seed=4)
    sim.run(300)
    assert len(set(sim.seen)) >= 4  # did several different things in 5 minutes


def test_sizes_scale_with_the_monitor():
    sim = Sim()
    sim.eng.set_size(192)
    assert sim.eng.frame_px == 192  # FakeDesktop is 100 % scale
    sim.fake.monitor_list = [
        type(m)(m.device, m.rect, m.work, 1.5, m.primary) for m in sim.fake.monitor_list
    ]
    sim.sensor.refresh_monitors()
    sim.step()
    assert sim.eng.frame_px == 192 * 1.5


def test_walking_speed_scales_with_her_size_so_paws_dont_slide():
    sim = Sim()
    at_m = sim.eng.gait(48)
    sim.eng.set_size(192)  # size L: frames 1.5x bigger -> steps 1.5x longer
    assert sim.eng.gait(48) == at_m * 1.5


def test_new_art_while_running_swaps_manifest_and_cat():
    from dataclasses import replace

    from coco.core.anims import SpriteManifest

    sim = Sim()
    sim.eng.play("happy")
    smaller = SpriteManifest(
        sim.eng.manifest.folder, {k: v for k, v in sim.eng.manifest.anims.items() if k != "happy"}
    )
    cat = replace(sim.eng.cat, behaviour_weights={"stalk": 99})
    sim.eng.set_art(cat, smaller)
    assert sim.eng.anim == "sit"  # the animation she was in disappeared -> sit
    assert sim.eng.director.weights["stalk"] == 99
    sim.run(1)  # keeps running fine on the new art
