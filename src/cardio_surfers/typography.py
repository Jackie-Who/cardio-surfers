"""Crisp text rendering with real TrueType fonts.

OpenCV's Hershey fonts are single-stroke vector shapes. At UI sizes they look
soft and slightly smeared -- the "blurry" look -- because every glyph is drawn
as anti-aliased polylines rather than a hinted, filled outline. They are also
ASCII-only, so anything else renders as `??`.

So text goes through Pillow instead: Segoe UI at three weights, rendered into
an alpha mask and blended onto the BGR canvas. Masks are cached by
(text, size, weight) and are colour-independent, so the same string tinted two
ways costs one rasterisation. The cache is bounded; the HUD draws a small,
repetitive set of strings, so the hit rate is close to 1 after a second.
"""

from __future__ import annotations

import os
from collections import OrderedDict

import numpy as np
from PIL import Image, ImageDraw, ImageFont

REGULAR, SEMIBOLD, BOLD = "regular", "semibold", "bold"

# Segoe UI is the Windows system face: it is what a native app looks like, and
# it is already on every machine this app targets. The fallbacks matter only
# for running the render tests off-Windows.
_FONT_FILES = {
    REGULAR: ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"),
    SEMIBOLD: ("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"),
    BOLD: ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"),
}
_FONT_DIRS = (
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    "/usr/share/fonts/truetype/dejavu",
    "/Library/Fonts",
)

_MAX_CACHE = 512


class TextRenderer:
    """Loads faces lazily and caches rendered alpha masks."""

    def __init__(self) -> None:
        self._fonts: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
        self._masks: OrderedDict[tuple[str, int, str], np.ndarray] = OrderedDict()

    def font(self, size_px: int, weight: str = REGULAR) -> ImageFont.FreeTypeFont:
        key = (weight, int(size_px))
        cached = self._fonts.get(key)
        if cached is not None:
            return cached
        for name in _FONT_FILES.get(weight, _FONT_FILES[REGULAR]):
            for directory in _FONT_DIRS:
                path = os.path.join(directory, name)
                if os.path.exists(path):
                    try:
                        font = ImageFont.truetype(path, int(size_px))
                        self._fonts[key] = font
                        return font
                    except OSError:
                        continue
        font = ImageFont.load_default()
        self._fonts[key] = font
        return font

    # -- measuring -----------------------------------------------------------

    def measure(self, text: str, size_px: int, weight: str = REGULAR) -> tuple[int, int]:
        """Width and cap-ish height of `text`, in pixels."""
        if not text:
            return 0, 0
        mask = self._mask(text, int(size_px), weight)
        return mask.shape[1], mask.shape[0]

    def _mask(self, text: str, size_px: int, weight: str) -> np.ndarray:
        key = (text, size_px, weight)
        cached = self._masks.get(key)
        if cached is not None:
            self._masks.move_to_end(key)
            return cached

        font = self.font(size_px, weight)
        # `getbbox` can report negative origins for glyphs with side bearings,
        # so render into a padded canvas and crop to the inked box.
        pad = max(2, size_px // 3)
        box = font.getbbox(text)
        w = max(1, int(box[2] - box[0]) + pad * 2)
        h = max(1, int(box[3] - box[1]) + pad * 2)
        image = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(image)
        draw.text((pad - box[0], pad - box[1]), text, font=font, fill=255)
        mask = np.asarray(image, dtype=np.float32) / 255.0

        self._masks[key] = mask
        if len(self._masks) > _MAX_CACHE:
            self._masks.popitem(last=False)
        return mask

    # -- drawing -------------------------------------------------------------

    def draw(self, canvas: np.ndarray, text: str, x: int, y: int,
             size_px: int, colour, weight: str = REGULAR) -> None:
        """Blend `text` onto `canvas` with its top-left at (x, y)."""
        if not text:
            return
        mask = self._mask(text, int(size_px), weight)
        mh, mw = mask.shape
        x, y = int(x), int(y)
        ch, cw = canvas.shape[:2]

        # Clip against the canvas so text near an edge does not raise.
        sx0, sy0 = max(0, -x), max(0, -y)
        dx0, dy0 = max(0, x), max(0, y)
        dx1, dy1 = min(cw, x + mw), min(ch, y + mh)
        if dx1 <= dx0 or dy1 <= dy0:
            return
        sub = mask[sy0:sy0 + (dy1 - dy0), sx0:sx0 + (dx1 - dx0)][..., None]
        region = canvas[dy0:dy1, dx0:dx1].astype(np.float32)
        tint = np.array(colour, dtype=np.float32)
        canvas[dy0:dy1, dx0:dx1] = (region * (1.0 - sub) + tint * sub).astype(np.uint8)


# One renderer for the process: the mask cache is the whole point.
RENDERER = TextRenderer()
