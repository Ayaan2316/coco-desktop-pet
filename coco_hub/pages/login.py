"""PIN screen."""

from __future__ import annotations

import time

from nicegui import app, ui
from starlette.requests import Request

from coco_hub import state
from coco_hub.auth import MAX_LEN, valid_pin
from coco_hub.theme import setup_page, signed_in

SESSION_DAYS = 30


def is_local(request: Request) -> bool:
    """Opened on this PC directly (not through Tailscale or anything else)."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0]
    forwarded = request.headers.get("x-forwarded-for") or request.headers.get("forwarded")
    client = request.client.host if request.client else ""
    return client == "127.0.0.1" and host in ("127.0.0.1", "localhost") and not forwarded


def sign_in() -> None:
    app.storage.user.update(auth_v=state.auth.version,
                            auth_until=time.time() + SESSION_DAYS * 86400)  # fmt: skip


def pin_pad(title: str, hint: str, on_pin) -> None:
    """A masked PIN field (phone: number keyboard; PC: just type) + a big keypad.
    Calls on_pin(pin, reset) on OK / Enter."""
    with ui.column().classes("items-center w-full gap-1") as wrap:
        ui.html(
            '<div class="sprite" style="position:relative;left:auto;bottom:auto;transform:none;'
            "width:150px;height:150px;--n:8;--dur:1.33s;background-image:url('/sprites/sit.png');"
            'animation-name:sprite-small"></div>'
        )
        ui.label(title).classes("display text-3xl text-center")
        msg = ui.label(hint).classes("quiet text-center")
        field = (
            ui.input(password=True)
            .props(
                f"borderless autofocus maxlength={MAX_LEN} inputmode=numeric "
                'autocomplete=current-password input-class="pin-field" aria-label="PIN"'
            )
            .classes("pin-box q-mt-sm")
        )
        with ui.element("div").classes("pad q-mt-md"):
            for label, value, aria, ghost in [
                *((k, k, k, False) for k in "123456789"),
                ("⌫", "back", "Delete", True),
                ("0", "0", "0", False),
                ("OK", "ok", "Unlock", True),
            ]:
                with (
                    ui.element("button")
                    .classes("key ghost" if ghost else "key")
                    .props(f'aria-label="{aria}" type="button"') as b
                ):
                    ui.label(label)
                b.on("click", lambda _e, v=value: press(v))

    def digits() -> str:
        return "".join(ch for ch in (field.value or "") if ch.isdigit())[:MAX_LEN]

    def reset(text: str | None = None, error: bool = False) -> None:
        field.set_value("")
        if text is not None:
            msg.set_text(text)
        if error:
            wrap.classes(add="shake")
            ui.timer(0.45, lambda: wrap.classes(remove="shake"), once=True)

    def submit() -> None:
        pin = digits()
        if pin:
            on_pin(pin, reset)

    def press(key: str) -> None:
        if key == "back":
            field.set_value(digits()[:-1])
        elif key == "ok":
            submit()
        elif len(digits()) < MAX_LEN:
            field.set_value(digits() + key)

    field.on("keydown.enter", submit)


@ui.page("/login")
def login(request: Request) -> None:
    setup_page("Sign in")
    ui.add_css("@keyframes sprite-small { to { background-position-x: calc(-150px * 8); } }"
               ".nicegui-content { min-height: 100vh; justify-content: center; }")  # fmt: skip
    if signed_in():
        ui.navigate.to("/")
        return
    auth = state.auth
    with ui.column().classes("shell items-center justify-center").style("padding-bottom: 32px"):
        if not auth.has_pin:
            if not is_local(request):
                ui.label("Set up Coco's dashboard on the laptop first").classes(
                    "display text-2xl text-center"
                )
                ui.label("Open http://127.0.0.1:8765 on the laptop and choose a PIN there. "
                         "Then come back here.").classes("quiet text-center")  # fmt: skip
                return
            first: dict = {"pin": None}

            def choose(pin: str, reset) -> None:
                if first["pin"] is None:
                    problem = valid_pin(pin)
                    if problem:
                        reset(problem, error=True)
                        return
                    first["pin"] = pin
                    reset("Type it once more to confirm.")
                    return
                if pin != first["pin"]:
                    first["pin"] = None
                    reset("Those didn't match. Choose a PIN again.", error=True)
                    return
                auth.set_pin(pin)
                sign_in()
                ui.navigate.to("/")

            pin_pad("Choose a PIN", "4 to 12 digits. You'll use it on your phone too.", choose)
            return

        def unlock(pin: str, reset) -> None:
            result = auth.check(pin)
            if result == "ok":
                sign_in()
                ui.navigate.to("/")
            elif result == "locked":
                mins = max(1, round(auth.locked_for() / 60))
                reset(f"Too many wrong PINs. Try again in {mins} min.", error=True)
            else:
                reset("Wrong PIN. Try again.", error=True)

        pin_pad("Hi! It's Coco", "Enter your PIN to come in~", unlock)
