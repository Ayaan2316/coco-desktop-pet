"""Media keys and volume."""

from __future__ import annotations

import logging
import re

from coco.tools.intents import intent
from coco.tools.registry import ToolCall, ToolResult, tool

log = logging.getLogger(__name__)

VK = {
    "play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2,
    "mute_toggle": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
}  # fmt: skip


class Media:
    """The real thing. Tests pass a fake with the same methods."""

    def key(self, name: str) -> None:
        from coco.desktop.windows import press_key

        press_key(VK[name])

    def _endpoint(self):
        from pycaw.pycaw import AudioUtilities

        return AudioUtilities.GetSpeakers().EndpointVolume

    def volume(self) -> float:
        return float(self._endpoint().GetMasterVolumeLevelScalar()) * 100

    def set_volume(self, percent: float) -> None:
        ep = self._endpoint()
        ep.SetMasterVolumeLevelScalar(max(0.0, min(100.0, percent)) / 100, None)
        if percent > 0:
            ep.SetMute(0, None)

    def set_mute(self, muted: bool) -> None:
        self._endpoint().SetMute(int(muted), None)


def _r(lang, en, hinglish, devanagari="", emotion="happy") -> ToolResult:
    if lang == "hinglish":
        return ToolResult(say=hinglish, speech_hi=devanagari, emotion=emotion)
    return ToolResult(say=en, emotion=emotion)


@tool(
    "media_control",
    "music / media / volume (target: play | pause | next | previous | volume_up | "
    "volume_down | set_volume | mute | unmute; amount: volume %)",
    words=r"music|song|track|play|pause|resume|next|skip|previous|volume|louder|quieter|"
    r"mute|unmute|sound|gaana|gana|awaaz|awaz|aawaz|band",
    label="Media and volume",
)
def media_control(call: ToolCall, ctx) -> ToolResult:
    media: Media = ctx.media
    lang = call.lang
    action = call.arg("target", "pause").lower()
    if action in ("play", "pause", "resume", "toggle", "play_pause"):
        media.key("play_pause")
        if action == "pause":
            return _r(lang, "Paused~ *ears flick*", "Gaana roka~", "गाना रोका।", "neutral")
        return _r(lang, "Music on! *tail sways*", "Gaana chalu! *tail sways*", "गाना चालू!")
    if action in ("next", "skip"):
        media.key("next")
        return _r(lang, "Next one~", "Agla gaana~", "अगला गाना!")
    if action in ("previous", "back", "prev"):
        media.key("previous")
        return _r(lang, "Going back one~", "Pichla gaana~", "पिछला गाना!")
    if action in ("volume_up", "volume_down", "set_volume"):
        try:
            now = media.volume()
            if action == "set_volume":
                num = re.search(r"\d+", call.arg("amount") or call.said or "")
                target = float(num.group(0)) if num else now
            else:
                step = float(re.search(r"\d+", call.arg("amount")).group(0)) if re.search(
                    r"\d+", call.arg("amount")) else 10.0  # fmt: skip
                target = now + step if action == "volume_up" else now - step
            target = max(0.0, min(100.0, target))
            media.set_volume(target)
        except Exception:
            log.debug("pycaw failed: using the volume keys", exc_info=True)
            media.key("volume_up" if action != "volume_down" else "volume_down")
            return _r(lang, "Done~", "Ho gaya~", "हो गया।")
        return _r(lang, f"Volume {target:.0f}%.", f"Awaaz {target:.0f}% kar di.",
                  f"आवाज़ {target:.0f} प्रतिशत कर दी।")  # fmt: skip
    if action in ("mute", "unmute"):
        try:
            media.set_mute(action == "mute")
        except Exception:
            media.key("mute_toggle")
        if action == "mute":
            return _r(lang, "Shh... muted. *whispers*", "Shh... mute kar diya.", "म्यूट कर दिया।",
                      "neutral")  # fmt: skip
        return _r(lang, "Sound's back!", "Awaaz wapas!", "आवाज़ वापस!")
    return _r(lang, "Hmm? Meow?", "Hmm? Meow?", emotion="curious")


# --- phrases
def _act(action: str):
    return lambda m, said: {"target": action}


def _set(m: re.Match, said: str) -> dict[str, str]:
    return {"target": "set_volume", "amount": m.group("n")}


_MUSIC = r"(?:the\s+)?(?:music|song|songs|track|video|media|player|spotify|gaana|gana|music)"
intent("media_control",
       r"^(?:set\s+)?(?:the\s+)?volume\s+(?:to\s+|at\s+)?(?P<n>\d{1,3})\s*%?(?:\s+percent)?$",
       r"^(?:awaaz|awaz|aawaz|volume)\s+(?P<n>\d{1,3})\s*%?\s*(?:karo|kar do|pe karo|par karo)?$",
       args=_set, priority=7)  # fmt: skip
intent("media_control",
       r"^(?:turn\s+(?:the\s+)?)?volume\s+up$|^(?:turn it |make it )?louder$|^turn it up$",
       r"\b(?:awaaz|awaz|aawaz|volume)\s+(?:badhao|badha do|tez karo|zyada karo|upar karo)\b",
       args=_act("volume_up"), priority=7)  # fmt: skip
intent("media_control",
       r"^(?:turn\s+(?:the\s+)?)?volume\s+down$|^(?:make it )?quieter$|^turn it down$",
       r"\b(?:awaaz|awaz|aawaz|volume)\s+(?:kam karo|kam kar do|dheere karo|ghatao|neeche karo)\b",
       args=_act("volume_down"), priority=7)  # fmt: skip
intent("media_control", r"^mute(?:\s+(?:it|the sound|sound|volume))?$",
       r"^(?:mute karo|mute kar do|awaaz band karo)$",
       args=_act("mute"), priority=7)  # fmt: skip
intent("media_control", r"^unmute(?:\s+(?:it|the sound|sound))?$", r"^unmute karo$",
       args=_act("unmute"), priority=7)  # fmt: skip
intent("media_control",
       rf"^(?:next|skip)(?:\s+{_MUSIC})?$|^(?:play\s+)?(?:the\s+)?next\s+(?:song|track|video)$",
       r"^(?:agla|next) (?:gaana|gana|song)(?: lagao| chalao)?$",
       args=_act("next"), priority=7)  # fmt: skip
intent("media_control",
       rf"^(?:previous|last)(?:\s+{_MUSIC})?$|^(?:play\s+)?(?:the\s+)?previous\s+(?:song|track)$",
       r"^(?:pichla|previous) (?:gaana|gana|song)(?: lagao| chalao)?$",
       args=_act("previous"), priority=7)  # fmt: skip
intent("media_control",
       rf"^(?:pause|stop)\s+{_MUSIC}$|^pause$|^pause (?:it|this)$",
       r"^(?:gaana|gana|music|song) (?:band|roko|rok do|pause) (?:karo|kar do)?$",
       r"^(?:gaana|gana|music) (?:band|roko|rok do)$",
       args=_act("pause"), priority=7)  # fmt: skip
intent("media_control",
       rf"^(?:play|resume|unpause|start)\s+{_MUSIC}$|^resume$|^play$|^play (?:it|some music)$",
       r"^(?:gaana|gana|music|song) (?:chalao|chala do|lagao|laga do|play karo|chalu karo)$",
       args=_act("play"), priority=7)  # fmt: skip
