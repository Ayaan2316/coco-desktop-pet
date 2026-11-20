"""Sprite frames for Qt, cut from the PNG strips and cached."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBitmap, QImage, QPixmap, QRegion, QTransform

from coco.core.anims import SpriteManifest

log = logging.getLogger(__name__)


@dataclass
class Frame:
    pixmap: QPixmap
    mask: QRegion  # in logical window coordinates
    anchor: QPointF  # paw point inside the frame, logical px
    width: int  # logical size of the window needed for this frame
    height: int


class SpriteLibrary:
    def __init__(self, manifest: SpriteManifest, size_logical: int, dpr: float) -> None:
        self.manifest = manifest
        self._strips: dict[str, QImage] = {}
        self._cache: dict[tuple[str, int, bool], Frame] = {}
        self.configure(size_logical, dpr)

    def set_manifest(self, manifest: SpriteManifest) -> None:
        """New art: forget every loaded strip and scaled frame (they're re-read on use)."""
        self.manifest = manifest
        self._strips.clear()
        self._cache.clear()

    def configure(self, size_logical: int, dpr: float) -> None:
        """Change display size or DPI: drops the scaled cache (strips stay loaded)."""
        self.size_logical = size_logical
        self.dpr = dpr
        self._cache.clear()

    def _strip(self, anim: str) -> QImage:
        img = self._strips.get(anim)
        if img is None:
            path = self.manifest.folder / self.manifest[anim].file
            img = QImage(str(path))
            if img.isNull():
                log.error("Could not load sprite strip %s", path)
                spec = self.manifest[anim]
                img = QImage(
                    spec.frame_w * spec.frames,
                    spec.frame_h,
                    QImage.Format.Format_ARGB32_Premultiplied,
                )
                img.fill(Qt.GlobalColor.transparent)
            self._strips[anim] = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        return self._strips[anim]

    def frame(self, anim: str, index: int, mirrored: bool) -> Frame:
        key = (anim, index, mirrored)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        spec = self.manifest[anim]
        scale = self.size_logical / self.manifest["sit"].frame_w  # logical px per source px
        w_log = max(1, round(spec.frame_w * scale))
        h_log = max(1, round(spec.frame_h * scale))
        w_phys, h_phys = round(w_log * self.dpr), round(h_log * self.dpr)

        src = self._strip(anim).copy(index * spec.frame_w, 0, spec.frame_w, spec.frame_h)
        if mirrored:
            src = src.transformed(QTransform().scale(-1, 1))
        img = src.scaled(
            w_phys,
            h_phys,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pixmap = QPixmap.fromImage(img)
        pixmap.setDevicePixelRatio(self.dpr)

        # Click mask at logical resolution: pixels at least ~50 % opaque catch the mouse.
        small = src.scaled(
            w_log,
            h_log,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        mask = QRegion(
            QBitmap.fromImage(small.createAlphaMask(Qt.ImageConversionFlag.ThresholdAlphaDither))
        )

        ax = spec.anchor[0] * scale
        if mirrored:
            ax = w_log - ax
        frame = Frame(pixmap, mask, QPointF(ax, spec.anchor[1] * scale), w_log, h_log)
        self._cache[key] = frame
        return frame
