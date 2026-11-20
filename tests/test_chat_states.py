"""Chat flow in the behaviour engine: open -> think -> talk -> emote -> sit."""

from coco.brain.schemas import BrainReply, BrainStatus
from coco.core.events import Topic
from coco.core.geometry import Point
from tests.conftest import Sim


def ask(sim: Sim, rid: int = 1) -> None:
    sim.send(Topic.CHAT_OPEN)
    sim.send(Topic.CHAT_ASK, "what is a capacitor?")
    sim.send(Topic.BRAIN_STATUS, BrainStatus("thinking", request_id=rid))
    sim.step()


def test_full_chat_flow():
    sim = Sim()
    ask(sim)
    assert sim.state == "think" and sim.pose.mood_icon == "question"
    sim.send(Topic.BRAIN_REPLY, BrainReply("A capacitor", None, final=False, request_id=1))
    sim.step()
    assert sim.state == "talk"
    sim.send(
        Topic.BRAIN_REPLY,
        BrainReply("A capacitor stores charge.", "curious", final=True, request_id=1),
    )
    sim.step()
    assert sim.state == "talk"  # keeps talking while the bubble types the rest
    assert sim.run(3, until=lambda s: s.state == "emote")
    assert sim.eng.state.emotion == "curious"
    assert sim.run(5, until=lambda s: s.state == "sit")


def test_chatting_keeps_her_in_place():
    sim = Sim(idle_forever=False, seed=3)
    sim.send(Topic.CHAT_OPEN)
    sim.step()
    assert sim.eng.situation().paused
    x = sim.eng.body.x
    sim.run(60)
    assert abs(sim.eng.body.x - x) < 1  # never wandered off while the bubble was open
    sim.send(Topic.CHAT_CLOSE)
    sim.step()
    assert not sim.eng.chatting


def test_chat_open_wakes_her_and_stops_walking():
    sim = Sim(start_in_bed=True)
    sim.run(3, until=lambda s: s.state == "sit")
    sim.eng.force("sleep")
    sim.send(Topic.CHAT_OPEN)
    sim.step()
    assert sim.state == "wake_up"

    sim2 = Sim()
    sim2.eng.force("walk")
    assert sim2.state == "walk"
    sim2.send(Topic.CHAT_OPEN)
    sim2.step()
    assert sim2.state == "sit"


def test_reply_while_held_does_not_interrupt_the_dangle():
    sim = Sim()
    ask(sim)
    sim.send(Topic.DRAG_START)
    sim.fake.cursor = Point(900, 600)
    sim.step()
    sim.send(Topic.BRAIN_REPLY, BrainReply("Hi!", "happy", final=True, request_id=1))
    sim.step()
    assert sim.state == "dangle"


def test_stale_replies_are_ignored():
    sim = Sim()
    ask(sim, rid=2)
    sim.send(Topic.BRAIN_REPLY, BrainReply("old answer", "sad", final=True, request_id=1))
    sim.step()
    assert sim.state == "think"


def test_short_reply_without_partials_goes_straight_to_emote():
    sim = Sim()
    ask(sim)
    sim.send(Topic.BRAIN_REPLY, BrainReply("Mrrp!", "love", final=True, request_id=1))
    sim.step()
    assert sim.state == "emote" and sim.pose.mood_icon == "heart"


def test_unknown_or_missing_emotion_art_falls_back_to_sit_anim():
    sim = Sim()
    ask(sim)
    sim.send(Topic.BRAIN_REPLY, BrainReply("ok", "neutral", final=True, request_id=1))
    sim.step()
    assert sim.state == "emote" and sim.eng.anim == "sit"


def test_canned_line_talks_then_emotes():
    sim = Sim()
    sim.send(Topic.CHAT_SAY, {"text": "It's late. Bed?", "emotion": "sleepy"})
    sim.step()
    assert sim.state == "talk"
    assert sim.run(3, until=lambda s: s.state == "emote")


def test_petting_shows_a_heart_and_sleep_shows_zzz():
    sim = Sim(start_in_bed=True)
    sim.run(3, until=lambda s: s.state == "sit")
    sim.eng.force("sleep")
    sim.step()
    assert sim.pose.mood_icon == "zzz"
    sim.eng._on_pet()
    sim.step()
    assert sim.pose.mood_icon == "heart"


def test_ignored_too_long_she_sulks_and_petting_fixes_it():
    sim = Sim()
    sim.eng.mood.affection = 0.1
    sim.eng.mood.since_interaction = 4000
    assert sim.eng.situation().can_sulk
    sim.eng.force("sulk")
    expected = "sad" if "sad" in sim.eng.manifest.anims else "sit"  # art may lack it
    assert sim.state == "sulk" and sim.eng.anim == expected
    sim.eng.petter.last_pet = sim.t  # as if the rub gesture was just recognised
    sim.eng._on_pet()
    sim.step()
    assert sim.state == "purr"
    assert not sim.eng.mood.should_sulk()


def test_throw_and_pounce_feed_the_mood():
    sim = Sim(cursor=Point(1200, 900))
    happy_before = sim.eng.mood.happiness
    sim.send(Topic.DRAG_START)
    sim.fake.cursor = Point(900, 600)
    sim.step()
    for i in range(8):
        sim.fake.cursor = Point(900 - 50 * (i + 1), 600)
        sim.step()
    sim.send(Topic.DRAG_END)
    sim.step()
    assert sim.eng.mood.happiness < happy_before
    sim.eng.mood.boredom = 0.8
    sim.run(3, until=lambda s: s.state == "sit")
    sim.fake.cursor = Point(sim.eng.body.x + 250, 900)
    sim.step()  # let her see where the cursor is now
    sim.eng.force("stalk")
    sim.run(12, until=lambda s: s.state == "pounce")
    assert sim.eng.mood.boredom < 0.8
