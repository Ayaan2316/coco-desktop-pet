"""Screenshots page: view, download, delete."""

from __future__ import annotations

import base64
import io
from datetime import datetime
from pathlib import Path

from nicegui import run, ui

from coco.core.config import load_config
from coco.core.paths import user_data_dir
from coco.tools.screenshot import shots_dir
from coco_hub.pages.lists import when_text
from coco_hub.theme import frame, need_login, title

PAGE = 24


def folder() -> Path:
    return shots_dir(load_config().screenshots.folder)


def list_shots(where: Path | None = None) -> list[Path]:
    where = where or folder()
    if not where.exists():
        return []
    return sorted(where.glob("Coco_*.png"), key=lambda p: p.stat().st_mtime, reverse=True)


def picture(path: Path, max_side: int, quality: int = 80) -> str:
    """A JPEG data URL of the picture, at most max_side px (thumbnails are cached)."""
    from PIL import Image

    cache = None
    if max_side <= 400:
        cache = user_data_dir() / "hub" / "thumbs" / f"{path.stem}_{max_side}.jpg"
        if cache.exists() and cache.stat().st_mtime >= path.stat().st_mtime:
            return "data:image/jpeg;base64," + base64.b64encode(cache.read_bytes()).decode()
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality)
    data = buf.getvalue()
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(data)
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def nice_time(path: Path) -> str:
    try:  # Coco_YYYY-MM-DD_HH-MM-SS.png
        t = datetime.strptime(path.stem.removeprefix("Coco_"), "%Y-%m-%d_%H-%M-%S").timestamp()
    except ValueError:
        t = path.stat().st_mtime
    return when_text(t)


@ui.page("/screenshots")
def screenshots_page() -> None:
    if need_login():
        return
    view = {"shown": PAGE}
    with frame("/", "Screenshots"):
        title("Screenshots", "photo_camera")
        info = ui.label().classes("quiet")
        grid = ui.element("div").classes("shots")
        more = ui.button("Show more", on_click=lambda: show_more()).props("flat no-caps")

    async def render() -> None:
        shots = list_shots()
        size = sum(p.stat().st_size for p in shots) / 1e6
        info.set_text(f"{len(shots)} pictures · {size:.1f} MB, kept only on the laptop "
                      f"(Pictures\\Coco)." if shots else "")  # fmt: skip
        grid.clear()
        with grid:
            if not shots:
                with ui.element("div").classes("empty card"):
                    ui.element("div").classes("nap")
                    ui.label("No screenshots yet").classes("display")
                    ui.label("Ask her: “take a screenshot”, or press Ctrl+Alt+S.")
            for p in shots[: view["shown"]]:
                thumb = await run.io_bound(picture, p, 360, 70)
                with (
                    ui.element("button")
                    .classes("shot")
                    .props(f'type="button" aria-label="Screenshot {nice_time(p)}"') as tile
                ):
                    ui.image(thumb).classes("shot-img")
                    ui.label(nice_time(p)).classes("shot-time")
                tile.on("click", lambda _e, path=p: open_shot(path))
        more.set_visibility(len(shots) > view["shown"])

    async def show_more() -> None:
        view["shown"] += PAGE
        await render()

    async def open_shot(path: Path) -> None:
        big = await run.io_bound(picture, path, 1600, 85)
        with ui.dialog() as d, ui.card().classes("card shot-dialog"):
            ui.image(big).classes("w-full rounded-xl")
            ui.label(nice_time(path)).classes("quiet text-sm")
            with ui.row().classes("w-full justify-between items-center q-mt-sm"):
                ui.button("Delete", icon="delete_outline",
                          on_click=lambda: confirm_delete(path, d)).props(
                    "flat no-caps color=negative")  # fmt: skip
                with ui.row().classes("gap-1"):
                    ui.button("Close", on_click=d.close).props("flat no-caps")
                    ui.button("Save to gallery", icon="download",
                              on_click=lambda: save(path)).props(
                        "unelevated no-caps color=primary text-color=dark")  # fmt: skip
        d.open()

    def save(path: Path) -> None:
        if not path.exists():
            ui.notify("That picture is gone.", type="warning")
            return
        ui.download.content(path.read_bytes(), path.name, "image/png")
        ui.notify("Saving… on your phone it goes to Downloads / your gallery.")

    def confirm_delete(path: Path, viewer) -> None:
        with ui.dialog() as d, ui.card().classes("card"):
            ui.label("Delete this screenshot?").classes("display text-xl")
            ui.label("It's removed from the laptop. This can't be undone.").classes("quiet")
            with ui.row().classes("justify-end w-full q-mt-md"):
                ui.button("Keep it", on_click=d.close).props("flat no-caps")

                async def delete() -> None:
                    path.unlink(missing_ok=True)
                    for cache in (user_data_dir() / "hub" / "thumbs").glob(f"{path.stem}_*.jpg"):
                        cache.unlink(missing_ok=True)
                    d.close()
                    viewer.close()
                    ui.notify("Deleted")
                    await render()

                ui.button("Delete", on_click=delete).props("unelevated no-caps color=negative")
        d.open()

    ui.timer(0.05, render, once=True)  # (the page shows first; pictures load right after)
