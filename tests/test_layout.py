"""Layout geometry: the window is resizable, so nothing may fall off it."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cardio_surfers import layout as L  # noqa: E402
from cardio_surfers import theme as T  # noqa: E402

SIZES = [
    (T.MIN_W, T.MIN_H),
    (1024, 600),
    (1280, 720),
    (1440, 900),
    (1920, 1080),
    (2560, 1440),
    (1600, 600),      # very wide
    (1000, 1000),     # very tall
]


def all_rects(lay: L.Layout):
    return {
        "header": lay.header, "back": lay.back, "status": lay.status,
        "content": lay.content, "stage": lay.stage, "rail": lay.rail,
        "panel": lay.panel, "thumb": lay.thumb,
    }


def test_every_rect_stays_inside_the_window_at_every_size():
    for w, h in SIZES:
        lay = L.build(w, h)
        window = (0, 0, lay.width, lay.height)
        for name, rect in all_rects(lay).items():
            assert L.fits(rect, window), f"{name} escaped at {w}x{h}: {rect}"


def test_every_rect_has_positive_area():
    for w, h in SIZES:
        lay = L.build(w, h)
        for name, (_, _, rw, rh) in all_rects(lay).items():
            assert rw > 0 and rh > 0, f"{name} collapsed at {w}x{h}"


def test_smaller_than_the_minimum_is_clamped_up():
    """Dragging below the minimum must not produce a broken layout."""
    lay = L.build(320, 200)
    assert (lay.width, lay.height) == (T.MIN_W, T.MIN_H)
    assert L.fits(lay.rail, (0, 0, lay.width, lay.height))


def test_stage_keeps_a_16_by_9_aspect():
    for w, h in SIZES:
        lay = L.build(w, h)
        _, _, sw, sh = lay.stage
        assert abs(sw / sh - 16 / 9) < 0.05, f"stage aspect wrong at {w}x{h}"


def test_stage_and_rail_do_not_overlap():
    for w, h in SIZES:
        lay = L.build(w, h)
        sx, _, sw, _ = lay.stage
        rx, _, _, _ = lay.rail
        assert sx + sw <= rx, f"stage overlaps rail at {w}x{h}"


def test_panel_and_thumb_do_not_overlap():
    for w, h in SIZES:
        lay = L.build(w, h)
        px, _, pw, _ = lay.panel
        tx, _, _, _ = lay.thumb
        assert px + pw <= tx, f"panel overlaps thumb at {w}x{h}"


def test_content_sits_below_the_header():
    for w, h in SIZES:
        lay = L.build(w, h)
        assert lay.content[1] == lay.header[1] + lay.header[3]


def test_bigger_windows_get_bigger_stages():
    small = L.build(T.MIN_W, T.MIN_H).stage
    large = L.build(1920, 1080).stage
    assert large[2] > small[2] and large[3] > small[3]


def test_theme_scales_but_text_stays_readable():
    base = T.theme_for(1024, 600)
    big = T.theme_for(2560, 1440)
    assert base.scale == 1.0
    assert big.scale == 2.0                      # clamped
    assert big.px(16) == 32
    # Text grows more gently than boxes, so a big window is not shouty.
    assert big.font(T.T_BODY) < T.T_BODY * 2.0
    assert big.font(T.T_BODY) > T.T_BODY


def test_rows_and_split_helpers_tile_without_overlap():
    rects = L.rows((0, 0, 100, 0), [20, 20, 20], gap=5)
    assert [r[1] for r in rects] == [0, 25, 50]
    cols = L.split((0, 0, 100, 10), [0.5, 0.5], gap=10)
    assert cols[0][2] == cols[1][2] == 45
    assert cols[1][0] == 55
