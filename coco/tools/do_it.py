"""The do_it tool and its phrases."""

from __future__ import annotations

import re

from coco.tools.intents import intent
from coco.tools.registry import ToolCall, ToolResult, tool

BUSY = {"en": "I'm already doing something. Say stop first~",
        "hinglish": "Main pehle se kuch kar rahi hoon. Pehle stop bolo~"}  # fmt: skip


@tool(
    "do_it",
    "do a task INSIDE an app by pressing its buttons step by step; only when no other tool "
    "fits (target: the app's name, or empty for the window you're using; text: the task)",
    words=r"\bin\b|mein|\bme\b|do it|kar do|karo|click|press|type|write|rename|turn on|"
    r"turn off|switch|enable|disable|new|open|create|select|go to",
    label="Do it for me (any app)",
)
def do_it(call: ToolCall, ctx) -> ToolResult:
    goal = call.arg("text") or call.said
    app = call.arg("target")
    if not goal:
        return ToolResult(ok=False, say="What should I do? Meow?", emotion="curious")
    outcome = ctx.agent_host.run(goal, app)
    hindi = call.lang == "hinglish"
    if outcome.kind == "busy":
        return ToolResult(ok=False, say=BUSY["hinglish" if hindi else "en"], emotion="neutral")
    say = outcome.summary
    if outcome.ok:
        say = f"Done! {say} *tail swish*" if not say.lower().startswith("done") else say
    emotion = {"done": "happy", "asked": "curious", "said_no": "neutral",
               "stopped": "neutral"}.get(outcome.kind, "sad")  # fmt: skip
    return ToolResult(ok=outcome.ok, say=say, emotion=emotion,
                      data={"steps": outcome.steps, "kind": outcome.kind})  # fmt: skip


# --- phrases
_VERBS = (r"open|type|write|turn|go|click|press|make|create|rename|close|find|search|switch|"
          r"calculate|enable|disable|add|select|save|start|show|hide|change|set|put|delete|"
          r"remove|copy|"
          r"paste|undo|zoom|toggle|bold|underline")  # fmt: skip


def _args(m: re.Match, said: str) -> dict[str, str] | None:
    goal = (m.groupdict().get("goal") or "").strip(" .,!?")
    app = (m.groupdict().get("app") or "").strip(" .,!?")
    if not goal or len(goal) < 3:
        return None
    if app.lower() in ("it", "this", "that", "the", "a", "my", "there", "here", "general"):
        return None
    return {"target": app, "text": goal}


intent(
    "do_it",
    r"^(?:please\s+|can you\s+|could you\s+)?do it for me[:,]?\s+(?P<goal>.+)$",
    rf"^(?:please\s+|can you\s+)?in\s+(?P<app>[\w.+#-]+(?:\s+[\w.+#-]+){{0,2}}?)[,:]?\s+"
    rf"(?P<goal>(?:{_VERBS})\b.+)$",
    r"^(?P<app>[\w.+#-]+(?:\s+[\w.+#-]+){0,2}?)\s+(?:mein|me|pe|par)\s+(?P<goal>.+?)\s+"
    r"(?:kar do|kardo|karo|khol do|kholo|likh do|likho|daba do|dabao|band karo|on karo|"
    r"off karo)$",
    args=_args,
    priority=-5,  # specific skills (spotify, files, focus...) always win
)
