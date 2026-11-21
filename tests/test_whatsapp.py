"""WhatsApp: contacts, links, phrases, and never sending without your Yes (fakes only)."""

import pytest

import coco.tools  # noqa: F401 - registers the tools and their phrases
from coco.tools.intents import parse_task
from coco.tools.registry import REGISTRY, ToolCall
from coco.tools.runner import ToolContext
from coco.tools.store import Contact, Store
from coco.tools.whatsapp import chat_link, find_contact, normalise_phone

CONTACTS = [
    Contact(1, "Rahul Sharma", [], "919876543210"),
    Contact(2, "Ammi", ["Mom", "Mummy", "Maa"], "919812345678"),
]


class FakeWA:
    def __init__(self):
        self.opened, self.searched, self.sent = [], [], 0

    def contacts(self):
        return CONTACTS

    def open_app(self):
        self.opened.append("app")

    def open_chat(self, phone, text=""):
        self.opened.append((phone, text))

    def search(self, name):
        self.searched.append(name)

    def press_send(self):
        self.sent += 1
        return True


def run(call):
    wa, asked, reported = FakeWA(), [], []
    ctx = ToolContext(services={
        "whatsapp": wa, "ask": lambda c, q, choices, cb: asked.append((q, choices, cb)),
        "report": lambda c, r: reported.append(r),
    })  # fmt: skip
    result = REGISTRY.run(call, ctx, ["whatsapp"])
    return wa, asked, reported, result


def test_phone_numbers():
    assert normalise_phone("+91 98765-43210") == "919876543210"
    assert normalise_phone("098765 43210") == "919876543210"
    assert normalise_phone("9876543210") == "919876543210"
    assert normalise_phone("+1 415 555 0100") == "14155550100"
    assert normalise_phone("12345") is None


def test_who_is_who():
    assert find_contact(CONTACTS, "Rahul").id == 1  # first name
    assert find_contact(CONTACTS, "mummy").id == 2  # nickname
    assert find_contact(CONTACTS, "my mom").id == 2
    assert find_contact(CONTACTS, "Rahool").id == 1  # close enough
    assert find_contact(CONTACTS, "Priya") is None


def test_link():
    assert chat_link("919876543210", "reaching in 10 min") == (
        "whatsapp://send?phone=919876543210&text=reaching%20in%2010%20min"
    )


def test_send_only_after_yes():
    call = ToolCall("whatsapp", {"target": "Rahul", "text": "reaching in 10 min"})
    wa, asked, reported, result = run(call)
    assert result is None and wa.opened == [("919876543210", "reaching in 10 min")]
    assert wa.sent == 0  # the draft is open, not sent
    question, choices, answer = asked[0]
    assert "Rahul Sharma" in question and "reaching in 10 min" in question
    answer("no")
    assert wa.sent == 0 and "not sent" in reported[-1].say
    answer("dismissed")  # closing the bubble / kill switch = no
    assert wa.sent == 0
    answer("yes")
    assert wa.sent == 1 and reported[-1].say == "Sent to Rahul Sharma! *happy tail*"


def test_open_chat_and_unknown_people():
    wa, asked, _, r = run(ToolCall("whatsapp", {"target": "Mom"}))
    assert wa.opened == [("919812345678", "")] and asked == [] and "Ammi" in r.say
    wa, asked, _, r = run(ToolCall("whatsapp", {"target": "Priya", "text": "hi"}))
    assert wa.searched == ["Priya"] and wa.sent == 0 and asked == []


@pytest.mark.parametrize(
    ("said", "args"),
    [
        ("send 'reaching in 10 min' to Rahul", {"target": "rahul", "text": "reaching in 10 min"}),
        ("message Rahul saying I'm running late", {"target": "rahul", "text": "i'm running late"}),
        ("tell Mom on whatsapp that I reached", {"target": "mom", "text": "i reached"}),
        ("Rahul ko message karo ki main aa raha hoon",
         {"target": "rahul", "text": "main aa raha hoon"}),
        ("Mummy ko bolo ki khana kha liya", {"target": "mummy", "text": "khana kha liya"}),
        ("open my chat with Mom", {"target": "mom"}),
        ("open Rahul's chat", {"target": "rahul"}),
        ("Rahul ki chat kholo", {"target": "rahul"}),
    ],
)  # fmt: skip
def test_phrases(said, args):
    call = parse_task(said, ["whatsapp", "open_app", "spotify", "notes", "reminder"])
    assert call is not None and call.tool == "whatsapp", said
    assert call.args == args


def test_contacts_store(tmp_path):
    store = Store(tmp_path / "coco.db")
    store.add_contact("Ammi", "919812345678", ["Mom", " Mummy "])
    c = store.contacts()[0]
    assert c.aliases == ["Mom", "Mummy"] and c.phone == "919812345678"
    store.delete_contact(c.id)
    assert store.contacts() == []
    store.close()
