"""Config loading: defaults, partial overrides, merged dicts, unknown keys, the real files."""

import logging

from coco.core.catdef import load_cat
from coco.core.config import Config, load_config
from coco.core.paths import CONFIG_FILE


def test_missing_file_gives_defaults(tmp_path):
    cfg = load_config(tmp_path / "nope.yaml")
    assert cfg == Config()


def test_partial_override_and_dict_merge(tmp_path, caplog):
    path = tmp_path / "config.yaml"
    path.write_text(
        "physics:\n  gravity: 1000\n  gravtiy: 5\nbehaviour:\n  weights: {stalk: 50}\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        cfg = load_config(path)
    assert cfg.physics.gravity == 1000.0 and isinstance(cfg.physics.gravity, float)
    assert cfg.physics.wall_bounce == Config().physics.wall_bounce  # untouched default
    assert cfg.behaviour.weights["stalk"] == 50
    assert cfg.behaviour.weights["sit"] == Config().behaviour.weights["sit"]  # merged
    assert "gravtiy" in caplog.text  # typo reported


def test_project_config_and_cat_load():
    cfg = load_config(CONFIG_FILE)
    assert cfg.cat == "coco"
    cat = load_cat(cfg.cat)
    assert cat.display_name == "Coco"
    assert cat.manifest_path.exists()


def test_old_basket_key_still_works(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("behaviour:\n  start_in_basket: false\n", encoding="utf-8")
    assert load_config(path).behaviour.start_in_bed is False


def test_bed_is_scaled_like_the_cat():
    import yaml

    from coco.behaviour.world import place_bed
    from coco.core.geometry import Rect
    from coco.core.paths import ASSETS_DIR

    meta = yaml.safe_load((ASSETS_DIR / "bed" / "bed.yaml").read_text("utf-8"))
    work = Rect(0, 0, 1920, 1200)
    bed = place_bed(meta, work, 1.25, 128, cat_frame_w=256)  # size M at 125 %
    k = 128 / 256 * 1.25  # the same factor her sprite frames get
    assert bed.rect.width == meta["size"][0] * k  # 384 px art -> 240 physical px
    assert bed.rect.bottom == work.bottom and bed.rect.right == 1920 - 16 * 1.25
    assert bed.spot.y == bed.rect.top + meta["bed"][1] * k
    assert bed.half_width == meta["bed_half_width"] * k
