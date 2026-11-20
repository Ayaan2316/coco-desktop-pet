"""Sprite manifest: format parsing, frame timing, and that the real PNG strips match it."""

import pytest
from PIL import Image

from coco.core.anims import load_manifest
from coco.core.catdef import load_cat


def test_frame_timing(tmp_path):
    p = tmp_path / "manifest.yaml"
    p.write_text(
        "frame_size: [100, 50]\nanchor: [50, 45]\nanimations:\n"
        "  walk: {frames: 4, fps: 10, loop: true}\n"
        "  land: {frames: 3, fps: 10, loop: false, next: sit, anchor: [40, 40]}\n",
        encoding="utf-8",
    )
    m = load_manifest(p)
    walk, land = m["walk"], m["land"]
    assert walk.frame_at(0.0) == 0 and walk.frame_at(0.35) == 3 and walk.frame_at(0.45) == 0
    assert land.frame_at(10.0) == 2  # one-shots hold their last frame
    assert land.duration == 0.3
    assert land.anchor == (40, 40) and walk.anchor == (50, 45)
    assert land.time_to_next_frame(1.0) == float("inf")
    assert abs(walk.time_to_next_frame(0.12) - 0.08) < 1e-9


SPRITE_DIRS = {
    "sprites": "final Blender art",
    "sprites_placeholder": "generated placeholders",
}


@pytest.mark.parametrize("folder", list(SPRITE_DIRS))
def test_sprite_strips_match_their_manifest(folder):
    """Every required animation exists, and every strip has exactly the size the manifest
    says (frames x frame_size), RGBA. Talk and emotion animations are optional."""
    sprites_dir = load_cat("coco").folder / folder
    m = load_manifest(sprites_dir / "manifest.yaml")
    assert m.missing() == [], f"{folder}: manifest lacks {m.missing()}"
    if folder == "sprites_placeholder":
        assert m.missing(include_emotions=True) == []
    for name, spec in m.anims.items():
        with Image.open(sprites_dir / spec.file) as img:
            assert img.mode == "RGBA", name
            assert img.size == (spec.frame_w * spec.frames, spec.frame_h), name


def test_final_art_paws_sit_on_the_anchor():
    """Feet-on-the-ground poses must reach exactly the anchor line (no floating)."""
    cat = load_cat("coco")
    m = load_manifest(cat.manifest_path)
    for name in ("walk", "run", "stalk", "peek"):
        spec = m[name]
        with Image.open(cat.sprites_dir / spec.file) as img:
            lowest = []
            for i in range(spec.frames):
                frame = img.crop((i * spec.frame_w, 0, (i + 1) * spec.frame_w, spec.frame_h))
                box = frame.getchannel("A").point(lambda v: 255 if v > 128 else 0).getbbox()
                lowest.append(box[3])
        assert max(lowest) == spec.anchor[1], (name, lowest)


def test_mood_icons_exist():
    from coco.core.paths import ASSETS_DIR

    for name in ("heart", "zzz", "exclaim", "question"):
        with Image.open(ASSETS_DIR / "bubbles" / f"{name}.png") as img:
            assert img.mode == "RGBA" and img.size == (64, 64)


def test_every_emotion_has_a_playable_animation():
    """Each AI emotion maps to an animation, or falls back to `sit` when the art for it
    isn't there yet (the engine does that in play())."""
    from coco.brain.schemas import EMOTIONS

    cat = load_cat("coco")
    placeholder = load_manifest(cat.folder / "sprites_placeholder" / "manifest.yaml")
    final = load_manifest(cat.manifest_path)
    assert "sit" in final.anims
    for emotion in EMOTIONS:
        assert cat.emotion_anim(emotion) in placeholder.anims, emotion


def test_bed_assets_match_bed_yaml():
    import yaml

    from coco.core.paths import ASSETS_DIR

    meta = yaml.safe_load((ASSETS_DIR / "bed" / "bed.yaml").read_text("utf-8"))
    for layer in ("back", "front"):
        with Image.open(ASSETS_DIR / "bed" / meta[layer]) as img:
            assert img.mode == "RGBA" and list(img.size) == meta["size"]
    x, y = meta["bed"]
    assert 0 < x < meta["size"][0] and 0 < y < meta["size"][1]
