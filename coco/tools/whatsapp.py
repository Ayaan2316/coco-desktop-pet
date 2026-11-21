"""WhatsApp chats and messages (asks before sending)."""

from __future__ import annotations

import difflib
import logging
import re
import urllib.parse

from coco.tools.intents import intent
from coco.tools.registry import ToolCall, ToolResult, tool
from coco.tools.store import Contact

log = logging.getLogger(__name__)

WHATSAPP_EXES = ("WhatsApp.Root.exe", "WhatsApp.exe")


def normalise_phone(raw: str, country: str = "91") -> str | None:
    """ "+91 98765-43210" / "098765 43210" / "9876543210" -> "919876543210"."""
    digits = re.sub(r"\D", "", raw or "")
    if raw.strip().startswith("+") and len(digits) >= 10:
        return digits
    digits = digits.lstrip("0")
    if len(digits) == 10:
        return country + digits
    if 11 <= len(digits) <= 15:
        return digits
    return None


def find_contact(contacts: list[Contact], name: str) -> Contact | None:
    want = re.sub(r"\s+", " ", name.lower()).strip(" .,'\"")
    want = re.sub(r"^(my|meri|mera|mere)\s+", "", want)
    for c in contacts:
        names = [c.name.lower(), *(a.lower() for a in c.aliases)]
        if want in names:
            return c
    for c in contacts:  # first names: "Rahul" -> "Rahul Sharma"
        if c.name.lower().split()[0] == want:
            return c
    pool = {
        n: c
        for c in contacts
        for n in [c.name.lower(), c.name.lower().split()[0], *(a.lower() for a in c.aliases)]
    }
    # lenient on purpose (speech: "Rahool"): sending always shows the full name and asks
    close = difflib.get_close_matches(want, list(pool), n=1, cutoff=0.72)
    return pool[close[0]] if close else None


def chat_link(phone: str, text: str = "") -> str:
    q = {"phone": phone}
    if text:
        q["text"] = text
    return "whatsapp://send?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)


def _r(lang, en, hinglish, devanagari="", ok=True, emotion="happy") -> ToolResult:
    if lang == "hinglish":
        return ToolResult(ok=ok, say=hinglish, speech_hi=devanagari, emotion=emotion)
    return ToolResult(ok=ok, say=en, emotion=emotion)


@tool(
    "whatsapp",
    "WhatsApp: open a chat or send a message (target: the person; text: the message, or "
    "'' to just open the chat). Always asks before sending.",
    words=r"whatsapp|message|msg|text|send|tell|chat|bolo|batao|bhejo|bhej do",
    label="WhatsApp",
)
def whatsapp(call: ToolCall, ctx) -> ToolResult | None:
    wa = ctx.whatsapp
    lang = call.lang
    who = call.arg("target")
    message = call.arg("text")
    if not who:
        wa.open_app()
        return _r(lang, "Opening WhatsApp~", "WhatsApp khol rahi hoon~", "व्हाट्सऐप खोल रही हूँ।")
    contact = find_contact(wa.contacts(), who)
    if contact is None:
        wa.search(who)
        return _r(lang, f"{who} isn't in my contacts, so I searched WhatsApp for them. Pick the "
                  "chat, and add them to my contacts (tray) for next time~",
                  f"{who} mere contacts mein nahi hai, WhatsApp mein search kar diya. Chat chun "
                  "lo, aur agli baar ke liye contacts mein daal do~",
                  "व्हाट्सऐप में सर्च कर दिया।", emotion="curious")  # fmt: skip
    wa.open_chat(contact.phone, message)
    if not message:
        return _r(lang, f"Here's your chat with {contact.name}~",
                  f"{contact.name} ki chat khol di~", "चैट खोल दी।")  # fmt: skip
    question = (f"Send this to {contact.name}? “{message}”" if lang != "hinglish"
                else f"{contact.name} ko bhej doon? “{message}”")  # fmt: skip
    choices = [("yes", "Haan, bhejo" if lang == "hinglish" else "Send"),
               ("no", "Nahi" if lang == "hinglish" else "Don't send")]  # fmt: skip

    def answered(key: str) -> None:
        if key != "yes":
            ctx.report(call, _r(lang, "Okay, not sent. The message is still in the box~",
                                "Theek hai, nahi bheja. Message box mein hai~", "नहीं भेजा।",
                                emotion="neutral"))  # fmt: skip
            return
        if wa.press_send():
            ctx.report(call, _r(lang, f"Sent to {contact.name}! *happy tail*",
                                f"{contact.name} ko bhej diya!", "भेज दिया!"))  # fmt: skip
        else:
            ctx.report(call, _r(lang, "I couldn't get to WhatsApp to press Send. It's still "
                                "typed in, just press Enter~", "WhatsApp tak nahi pahunch payi. "
                                "Message type hai, Enter daba do~", "एंटर दबा दो।", ok=False,
                                emotion="sad"))  # fmt: skip

    ctx.ask(call, question, choices, answered)
    return None


class WhatsApp:
    """The real thing (Windows). Tests use a fake with the same methods."""

    def __init__(self, store, launcher, settle: float = 2.5) -> None:
        self.store = store
        self.launcher = launcher
        self.settle = settle

    def contacts(self) -> list[Contact]:
        return self.store.contacts()

    def open_app(self) -> None:
        self.launcher.open(self.launcher.resolve("whatsapp"))

    def open_chat(self, phone: str, text: str = "") -> None:
        self.launcher.open_uri(chat_link(phone, text))

    def _window(self) -> int | None:
        from coco.desktop import windows as win

        return win.find_app_window(WHATSAPP_EXES, "WhatsApp")

    def search(self, name: str) -> None:
        """Open WhatsApp and type the name into its chat search (you pick the chat)."""
        import time

        from coco.desktop import windows as win

        hwnd = self._window()
        if hwnd is None:
            self.open_app()
            for _ in range(20):
                time.sleep(0.5)
                hwnd = self._window()
                if hwnd:
                    break
        if hwnd and win.force_foreground(hwnd):
            time.sleep(0.6)
            win.press_keys("ctrl+f")
            time.sleep(0.4)
            win.type_text(name)

    def press_send(self) -> bool:
        """Bring WhatsApp (with the opened chat and typed message) to the front, press Enter."""
        import time

        from coco.desktop import windows as win

        hwnd = self._window()
        if hwnd is None or not win.force_foreground(hwnd):
            return False
        time.sleep(0.4)
        win.press_keys("enter")
        log.info("WhatsApp: Send pressed after you said yes")
        return True


# --- phrases
def _send(m: re.Match, said: str) -> dict[str, str] | None:
    who = (m.group("who") or "").strip(" ,.")
    msg = (m.group("msg") or "").strip(" ,.'\"")
    if not who or not msg or len(who.split()) > 4:
        return None
    return {"target": who, "text": msg}


def _open(m: re.Match, said: str) -> dict[str, str] | None:
    who = (m.group("who") or "").strip(" ,.")
    if not who or who in ("whatsapp", "my whatsapp") or len(who.split()) > 4:
        return None
    return {"target": who}


intent(
    "whatsapp",
    # English
    r"^(?:send|whatsapp|text)\s+['\"]?(?P<msg>.+?)['\"]?\s+to\s+(?P<who>[a-z][a-z ]{0,30}?)"
    r"(?:\s+on whatsapp)?$",
    r"^(?:message|whatsapp|text|msg)\s+(?P<who>[a-z][a-z ]{0,30}?)\s+(?:saying|that|to say)\s+"
    r"(?P<msg>.+)$",
    r"^tell\s+(?P<who>[a-z][a-z ]{0,30}?)\s+on whatsapp\s+(?:that\s+)?(?P<msg>.+)$",
    # Hinglish: "Rahul ko message karo ki ..." / "Mummy ko bolo ki ..."
    r"^(?P<who>[a-z][a-z ]{0,30}?)\s+ko\s+(?:whatsapp\s+)?(?:message|msg|bolo|bol do|batao|"
    r"bata do|bhejo|bhej do|text)\s+(?:karo|kar do)?\s*(?:ki|ke)\s+(?P<msg>.+)$",
    args=_send,
    priority=7,
)
intent(
    "whatsapp",
    r"^open\s+(?:my\s+)?(?:whatsapp\s+)?chat\s+with\s+(?P<who>.+?)(?:\s+on whatsapp)?$",
    r"^open\s+(?P<who>.+?)'s\s+(?:whatsapp\s+)?chat$",
    r"^(?:message|whatsapp)\s+(?P<who>[a-z][a-z ]{0,30})$",
    r"^(?P<who>.+?)\s+ki\s+(?:whatsapp\s+)?chat\s+(?:kholo|khol do)$",
    r"^(?P<who>.+?)\s+ko\s+(?:whatsapp|message)\s+karo$",
    args=_open,
    priority=7,
)
