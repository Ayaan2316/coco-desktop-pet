"""Dashboard theme and page frame."""

from __future__ import annotations

import contextlib
import time

from nicegui import app, ui

from coco_hub import state

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Fredoka:wght@500;600;700&'
    'family=Nunito:wght@400;600;700;800&display=swap" rel="stylesheet">'
    '<meta name="theme-color" content="#FFF9F6">'
)

# a faint paw print (page background) and Coco's face (chat avatar)
PAW = (
    "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='120' height='120'"
    " viewBox='0 0 120 120'><g fill='%23F4A3B5' fill-opacity='.06' transform='rotate(-18 60 60)'>"
    "<ellipse cx='60' cy='70' rx='13' ry='11'/><circle cx='44' cy='52' r='5.5'/>"
    "<circle cx='55' cy='45' r='5.5'/><circle cx='67' cy='45' r='5.5'/>"
    "<circle cx='77' cy='52' r='5.5'/></g></svg>"
)

CSS = (
    r"""
:root {
  --milk: #FFF9F6; --fluff: #FFFFFF; --crumb: #F3E7E3; --cocoa: #4B3B40; --mist: #A08F94;
  --cheek: #F4A3B5; --blush: #FFE6EC; --nose: #E9879C; --eye: #7DB8F2; --sky: #E6F2FF;
  --tail: #D9D4DA; --danger: #E26D80;
  --radius: 26px;
  --round: 'Fredoka', 'Nunito', system-ui, sans-serif;
  --body: 'Nunito', system-ui, sans-serif;
  --shadow: 0 6px 22px rgba(160, 110, 120, .10);
}
html, body { background: var(--milk); color: var(--cocoa); font-family: var(--body); }
body { -webkit-font-smoothing: antialiased; background-image: url("PAW_URL");
       background-size: 120px 120px; }
.q-page, .nicegui-content { padding: 0 !important; }
.nicegui-content { align-items: stretch; }
a { color: inherit; }
:focus-visible { outline: 3px solid var(--eye) !important; outline-offset: 3px; }

.display { font-family: var(--round); font-weight: 600; letter-spacing: -.01em; }
.mono { font-family: var(--body); font-variant-numeric: tabular-nums; }
.quiet { color: var(--mist); }
.eyebrow { font-family: var(--round); font-size: 15px; font-weight: 600; color: var(--cocoa); }

/* ---- page frame ------------------------------------------------------------------- */
.shell { max-width: 600px; margin: 0 auto; width: 100%;
         padding: 14px 16px calc(100px + env(safe-area-inset-bottom)); gap: 16px; }
.shell > .topbar, .shell > .room-wrap, .shell > .tiles, .shell > .kill, .shell > .card,
.shell > .thread, .shell > .empty { width: 100%; align-self: stretch; }
.topbar { display: flex; align-items: center; justify-content: space-between; height: 46px; }
.wordmark { font-family: var(--round); font-size: 28px; font-weight: 700; color: var(--cocoa);
            display: inline-flex; align-items: center; gap: 6px; }
.wordmark .q-icon { font-size: 20px; color: var(--cheek); transform: rotate(-14deg); }
.live { display: inline-flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 700;
        color: var(--mist); background: var(--fluff); border: 1px solid var(--crumb);
        padding: 6px 12px; border-radius: 999px; }
.live .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--tail); }
.live.on { color: var(--nose); }
.live.on .dot { background: var(--cheek); box-shadow: 0 0 0 3px var(--blush); }

.tabs { position: fixed; left: 50%; transform: translateX(-50%);
        bottom: calc(12px + env(safe-area-inset-bottom)); z-index: 50;
        display: flex; gap: 2px; padding: 6px; border-radius: 999px;
        background: rgba(255, 255, 255, .92); backdrop-filter: blur(12px);
        border: 1px solid var(--crumb); box-shadow: 0 8px 26px rgba(160, 110, 120, .16); }
.tab { position: relative; display: flex; flex-direction: column; align-items: center; gap: 1px;
       min-width: 62px; padding: 6px 8px 9px; border-radius: 999px; color: var(--mist);
       font-size: 11px; font-weight: 700; text-decoration: none;
       transition: background .2s, color .2s; }
.tab .q-icon { font-size: 22px; }
.tab:hover { color: var(--cocoa); }
.tab.here { background: var(--blush); color: var(--nose); }
.tab.here::after { content: ''; position: absolute; bottom: 2px; width: 5px; height: 5px;
                   border-radius: 50%; background: var(--cheek); }

.card { background: var(--fluff); border: 1px solid var(--crumb); border-radius: var(--radius);
        padding: 18px; box-shadow: var(--shadow); }
.page-title { font-family: var(--round); font-size: 32px; font-weight: 700; line-height: 1.1;
              display: inline-flex; align-items: center; gap: 8px; }
.page-title .q-icon { font-size: 24px; color: var(--cheek); }

/* ---- her room (home) --------------------------------------------------------------- */
.room-wrap { position: relative; margin-top: 30px; }
/* her ears: the room is her head */
.ear { position: absolute; top: -30px; width: 74px; height: 62px; z-index: 0;
       background: #E3DEF3; clip-path: polygon(50% 0, 100% 100%, 0 100%);
       transition: background 1.2s ease; }
.ear::after { content: ''; position: absolute; left: 22px; right: 22px; top: 20px; bottom: 6px;
              background: #F3C9D4; clip-path: polygon(50% 0, 100% 100%, 0 100%); }
.ear.left { left: 30px; transform: rotate(-12deg); }
.ear.right { right: 30px; transform: rotate(12deg); }
.room-wrap:has(.room.on) .ear { background: #FFDCE5; }
.room { position: relative; z-index: 1; height: 320px; border-radius: 34px; overflow: hidden;
        border: 1px solid var(--crumb); box-shadow: var(--shadow);
        background: linear-gradient(180deg, #E9E5F6 0%, #F3EEF8 70%, #F6EFF3 100%); }
.room .glow { position: absolute; inset: 0; opacity: 0; transition: opacity 1.2s ease;
        background: linear-gradient(180deg, #FFE7EE 0%, #FFF4EC 65%, #FFF7F2 100%); }
.room.on .glow { opacity: 1; }
.room .stars { position: absolute; inset: 0; transition: opacity 1.2s ease; opacity: 1;
        background-image:
          radial-gradient(2px 2px at 14% 20%, #FFFFFF 50%, transparent 51%),
          radial-gradient(1.6px 1.6px at 30% 38%, #FFFFFF 50%, transparent 51%),
          radial-gradient(2px 2px at 72% 16%, #FFFFFF 50%, transparent 51%),
          radial-gradient(1.6px 1.6px at 86% 40%, #FFFFFF 50%, transparent 51%),
          radial-gradient(1.6px 1.6px at 48% 12%, #FFFFFF 50%, transparent 51%); }
.room .moon { position: absolute; top: 26px; left: 30px; width: 34px; height: 34px;
        border-radius: 50%; box-shadow: -9px 4px 0 0 #FFF6D9; transform: rotate(-20deg);
        transition: opacity 1.2s ease; }
.room.on .stars, .room.on .moon { opacity: 0; }
.room .hearts { position: absolute; inset: 0; opacity: 0; transition: opacity 1.2s ease;
        pointer-events: none; }
.room.on .hearts { opacity: 1; }
.hearts i { position: absolute; color: var(--cheek); font-style: normal; font-size: 18px;
        animation: float 5s ease-in-out infinite; }
.hearts i:nth-child(1) { left: 22%; top: 34%; }
.hearts i:nth-child(2) { left: 70%; top: 46%; font-size: 13px; animation-delay: 1.6s; }
.hearts i:nth-child(3) { left: 30%; top: 58%; font-size: 12px; animation-delay: 3.1s; }
@keyframes float { 0%, 100% { transform: translateY(0); opacity: .9; }
                   50% { transform: translateY(-10px); opacity: .5; } }
.room .floor { position: absolute; left: 0; right: 0; bottom: 0; height: 70px;
        background: rgba(255, 255, 255, .55); border-top: 1px solid rgba(243, 231, 227, .9); }
.room .shadow { position: absolute; left: 50%; bottom: 50px; width: 140px; height: 16px;
        transform: translateX(-50%); border-radius: 50%; background: rgba(140, 110, 130, .16);
        filter: blur(3px); }
.sprite { position: absolute; left: 50%; bottom: 38px; width: 210px; height: 210px;
          transform: translateX(-50%); background-repeat: no-repeat;
          background-size: auto 100%;
          animation: sprite-play var(--dur, 1s) steps(var(--n, 1)) infinite;
          transition: filter 1.2s ease; }
@keyframes sprite-play { from { background-position-x: 0; }
                         to { background-position-x: calc(-210px * var(--n, 1)); } }
.room:not(.on) .sprite { filter: saturate(.75) brightness(.96); }

/* the pull cord: her on/off switch */
.cord { position: absolute; top: 0; right: 28px; width: 44px; height: 140px; border: 0;
        background: none; cursor: pointer; padding: 0; z-index: 2;
        -webkit-tap-highlight-color: transparent; }
.cord .string { position: absolute; left: 50%; top: 0; width: 2px; height: 98px;
        transform: translateX(-50%); background: repeating-linear-gradient(
          180deg, #E3CBD2 0 6px, #F4DDE3 6px 12px); }
.cord .bead { position: absolute; left: 50%; top: 92px; width: 30px; height: 30px;
        transform: translateX(-50%); border-radius: 50%; display: grid; place-items: center;
        background: var(--cheek); color: #fff; border: 3px solid #fff;
        box-shadow: 0 4px 12px rgba(233, 135, 156, .35); transition: background .4s; }
.cord .bead .q-icon { font-size: 15px; }
.room:not(.on) .cord .bead { background: #B9B3D6; box-shadow: 0 4px 10px rgba(120,110,170,.3); }
.cord:hover .bead { transform: translateX(-50%) scale(1.08); }
.cord.pulled { animation: cord-pull .55s ease; }
@keyframes cord-pull { 0% { transform: translateY(0); } 35% { transform: translateY(16px); }
                       70% { transform: translateY(-4px); } 100% { transform: translateY(0); } }
.cord-hint { position: absolute; top: 128px; right: 2px; width: 96px; text-align: center;
        font-size: 11px; font-weight: 700; color: var(--mist); z-index: 2; }
.room .busy { position: absolute; inset: 0; z-index: 3; display: grid; place-items: center;
        background: rgba(255, 249, 246, .7); backdrop-filter: blur(2px);
        font-family: var(--round); font-size: 18px; font-weight: 600; color: var(--nose); }

.status-line { font-family: var(--round); font-size: 28px; line-height: 1.15; font-weight: 600; }
.status-line em { color: var(--nose); font-style: normal; }
.feels { font-size: 15px; color: var(--mist); font-weight: 600; }

/* ---- switches ---------------------------------------------------------------------- */
.tiles { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
@media (max-width: 400px) { .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.tile { display: flex; flex-direction: column; align-items: flex-start; gap: 10px; padding: 14px;
        min-height: 100px; border-radius: 22px; border: 1px solid var(--crumb);
        background: var(--fluff); color: var(--mist); cursor: pointer; text-align: left;
        box-shadow: var(--shadow); font: inherit; -webkit-tap-highlight-color: transparent;
        transition: background .25s, border-color .25s, color .25s, transform .12s; }
.tile:active { transform: scale(.96); }
.tile > .q-icon { font-size: 22px; width: 38px; height: 38px; border-radius: 50%;
        display: grid; place-items: center; background: #F6F1F2; }
.tile .t-name { font-weight: 800; font-size: 14px; color: var(--cocoa); }
.tile .t-state { font-size: 12px; font-weight: 700; }
.tile.on { background: var(--blush); border-color: #F8C9D3; color: var(--nose); }
.tile.on > .q-icon { background: #fff; color: var(--nose); }
.tile.listen.on { background: var(--sky); border-color: #C6DFF8; color: #3E83C9; }
.tile.listen.on > .q-icon { color: #3E83C9; }
.tile[disabled] { cursor: not-allowed; }
.tiles.asleep .tile { background: #FBF6F5; box-shadow: none; color: var(--tail); }
.tiles.asleep .tile .t-name { color: var(--mist); }
.tiles.asleep .tile > .q-icon { background: #F3EDEE; }

.driving { display: flex; align-items: center; gap: 10px; width: 100%; align-self: stretch;
           padding: 10px 10px 10px 16px; border-radius: 999px; background: #FFE3E8;
           color: var(--danger); font-weight: 800; border: 1px solid #F6BCC7; }
.driving .grow { flex: 1; }
.kill { width: 100%; border-radius: 999px; padding: 13px; font: inherit; font-weight: 800;
        background: var(--fluff); color: var(--danger); cursor: pointer;
        border: 1.5px dashed #F2B8C2; display: flex; gap: 10px; align-items: center;
        justify-content: center; }
.kill:hover { background: #FFF1F3; }

.meters { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px 18px; }
.meter .lbl { font-size: 13px; font-weight: 700; color: var(--mist); }
.meter .paws { display: flex; flex-wrap: nowrap; gap: 2px; margin-top: 2px; }
.meter .paws .q-icon { font-size: 19px; color: var(--cheek); }
.meter .paws .q-icon.off { color: #F1E3E6; }

.facts { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
.fact { background: var(--milk); border-radius: 18px; padding: 10px 12px; }
.fact .big { font-family: var(--round); font-size: 22px; font-weight: 600; color: var(--cocoa); }
.fact .lbl { font-size: 12px; font-weight: 700; color: var(--mist); }

/* ---- chat ---------------------------------------------------------------------------- */
.shell.with-composer { padding-bottom: calc(176px + env(safe-area-inset-bottom)); }
.thread { display: flex; flex-direction: column; gap: 12px; }
.msg { max-width: 84%; padding: 11px 15px; border-radius: 22px; line-height: 1.45;
       white-space: pre-wrap; word-wrap: break-word; font-size: 15px; }
.msg.me { align-self: flex-end; background: var(--blush); color: var(--cocoa);
          border-bottom-right-radius: 8px; }
.coco-row { display: flex; gap: 8px; align-items: flex-end; align-self: flex-start;
            max-width: 90%; }
.avatar { flex: none; width: 34px; height: 34px; border-radius: 50%; background-color: #fff;
          border: 2px solid #F8C9D3; background-image: url('/sprites/sit.png');
          background-repeat: no-repeat; background-size: 480px 60px;
          background-position: -13px -3px; }
.msg.coco { background: var(--fluff); border: 1px solid var(--crumb);
            border-bottom-left-radius: 8px; box-shadow: var(--shadow); max-width: none; }
.msg .meta { display: flex; gap: 5px; align-items: center; margin-top: 4px; font-size: 11px;
             font-weight: 700; color: var(--mist); }
.day { align-self: center; font-size: 12px; font-weight: 700; color: var(--mist);
       padding: 4px 12px; border-radius: 999px; background: var(--fluff);
       border: 1px solid var(--crumb); }
.composer { position: fixed; left: 50%; transform: translateX(-50%); width: min(600px, 100%);
            bottom: calc(88px + env(safe-area-inset-bottom)); padding: 0 16px; z-index: 40; }
.composer .box { display: flex; gap: 8px; align-items: center; padding: 5px 5px 5px 18px;
            border-radius: 999px; background: var(--fluff); border: 1px solid var(--crumb);
            box-shadow: 0 8px 26px rgba(160, 110, 120, .16); }
.typing span { display: inline-block; width: 7px; height: 7px; margin: 0 2px; border-radius: 50%;
            background: var(--cheek); animation: blink 1.2s infinite; }
.typing span:nth-child(2) { animation-delay: .2s; }
.typing span:nth-child(3) { animation-delay: .4s; }
@keyframes blink { 0%, 80%, 100% { opacity: .3; } 40% { opacity: 1; } }

.chips { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.chip { background: var(--milk) !important; color: var(--cocoa) !important;
        border: 1px solid var(--crumb); border-radius: 16px !important; padding: 10px !important;
        justify-content: flex-start; box-shadow: none !important; }
.chip .q-icon { color: var(--nose); }

/* ---- screenshots ------------------------------------------------------------------- */
.shell > .shots { width: 100%; align-self: stretch; }
.shots { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
@media (min-width: 560px) { .shots { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
.shots > .empty { grid-column: 1 / -1; }
.shot { display: flex; flex-direction: column; gap: 6px; padding: 6px; border-radius: 18px;
        border: 1px solid var(--crumb); background: var(--fluff); box-shadow: var(--shadow);
        cursor: pointer; font: inherit; text-align: left;
        -webkit-tap-highlight-color: transparent; }
.shot:active { transform: scale(.97); }
.shot-img { width: 100%; aspect-ratio: 16 / 10; border-radius: 12px; overflow: hidden; }
.shot-img img { object-fit: cover; }
.shot-time { font-size: 12px; font-weight: 700; color: var(--mist); padding: 0 4px 2px; }
.shot-dialog { width: min(92vw, 900px); max-width: 92vw !important; }

/* ---- lists ----------------------------------------------------------------------------- */
.item { display: flex; align-items: center; gap: 12px; padding: 12px 2px;
        border-bottom: 1px dashed var(--crumb); }
.item:last-child { border-bottom: 0; }
.item .grow { flex: 1; min-width: 0; }
.item .sub { font-size: 12px; font-weight: 600; color: var(--mist); }
.done-text { text-decoration: line-through; color: var(--mist); }
.path, .item .sub, .feels { overflow-wrap: anywhere; }
.shell { overflow-x: hidden; }
.empty { text-align: center; padding: 22px 8px; color: var(--mist); font-weight: 600; }
.empty .display { font-size: 20px; color: var(--cocoa); margin-bottom: 2px; }
.empty .nap { width: 120px; height: 120px; margin: 0 auto 4px;
       background: url('/sprites/sleep.png') no-repeat 0 0 / auto 100%; }

/* ---- PIN ------------------------------------------------------------------------------- */
.pin-box { width: 230px; border-bottom: 2px dashed var(--crumb); }
.pin-box:focus-within { border-bottom-color: var(--cheek); }
.pin-field { text-align: center; font-family: var(--round) !important; font-size: 30px !important;
             letter-spacing: .45em; color: var(--nose) !important; caret-color: var(--nose); }
.pad { display: grid; grid-template-columns: repeat(3, 74px); gap: 14px; justify-content: center; }
.key { width: 74px; height: 74px; border-radius: 50%; border: 1px solid var(--crumb);
       background: var(--fluff); color: var(--cocoa); font-family: var(--round); font-size: 26px;
       font-weight: 600; cursor: pointer; box-shadow: var(--shadow);
       transition: background .15s, transform .1s; -webkit-tap-highlight-color: transparent; }
.key:active { background: var(--blush); transform: scale(.95); }
.key.ghost { background: transparent; border-color: transparent; box-shadow: none;
       font-family: var(--body); font-size: 14px; font-weight: 800; color: var(--mist); }
.shake { animation: shake .4s; }
@keyframes shake { 20%, 60% { transform: translateX(-8px); }
                   40%, 80% { transform: translateX(8px); } }

.q-field--outlined .q-field__control { border-radius: 16px; background: var(--fluff); }
.q-field--outlined .q-field__control:before { border-color: var(--crumb); }
.q-btn { text-transform: none; font-weight: 800; letter-spacing: 0; border-radius: 999px; }
.q-dialog .card { min-width: 300px; }

@media (prefers-reduced-motion: reduce) {
  .sprite, .cord.pulled, .typing span, .hearts i { animation: none !important; }
  .room .glow, .room .stars, .sprite { transition: none; }
}
"""
).replace("PAW_URL", PAW)

TABS = [
    ("/", "home", "Home"),
    ("/chat", "forum", "Chat"),
    ("/notes", "sticky_note_2", "Notes"),
    ("/reminders", "alarm", "Reminders"),
    ("/settings", "tune", "Settings"),
]


def setup_page(title: str) -> None:
    ui.page_title(f"{title} · Coco")
    ui.add_head_html(FONTS)
    ui.add_css(CSS)
    ui.colors(primary="#F4A3B5", secondary="#7DB8F2", accent="#E9879C", dark="#4B3B40",
              positive="#7CCBA2", negative="#E26D80")  # fmt: skip
    ui.dark_mode(False)


def signed_in() -> bool:
    s = app.storage.user
    return s.get("auth_v") == state.auth.version and float(s.get("auth_until", 0)) > time.time()


@contextlib.contextmanager
def frame(here: str, title: str):
    """Every signed-in page: top bar, content column, bottom tabs."""
    setup_page(title)
    with ui.column().classes("shell") as shell:
        with ui.element("div").classes("topbar"):
            with ui.element("span").classes("wordmark"):
                ui.label("coco")
                ui.icon("pets")
            live = ui.html('<span class="live"><span class="dot"></span>checking…</span>')
        yield shell
    with ui.element("nav").classes("tabs").props('aria-label="Pages"'):
        for path, icon, label in TABS:
            with ui.link(target=path).classes("tab" + (" here" if path == here else "")):
                ui.icon(icon)
                ui.label(label)

    def show_live() -> None:
        on = state.snapshot.get("running", False)
        text = "awake" if on else "off"
        live.set_content(f'<span class="live{" on" if on else ""}"><span class="dot"></span>'
                         f"Coco is {text}</span>")  # fmt: skip

    show_live()
    ui.timer(2.0, show_live)


def title(text: str, icon: str = "pets") -> None:
    with ui.element("div").classes("page-title"):
        ui.label(text)
        ui.icon(icon)


def need_login() -> bool:
    """True (and sends you to the PIN screen) when this browser isn't signed in."""
    if signed_in():
        return False
    ui.navigate.to("/login")
    return True
