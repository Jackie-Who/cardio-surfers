"""Typography, theme contrast, and the game-overlay geometry."""

from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cardio_surfers import game_overlay as go  # noqa: E402
from cardio_surfers import theme as T  # noqa: E402
from cardio_surfers.typography import BOLD, REGULAR, SEMIBOLD, TextRenderer  # noqa: E402


# -- typography ---------------------------------------------------------------

def test_text_measures_wider_for_longer_strings():
    r = TextRenderer()
    short = r.measure("SPM", 14)[0]
    long = r.measure("Running 160 SPM", 14)[0]
    assert 0 < short < long


def test_bigger_sizes_render_bigger():
    r = TextRenderer()
    small = r.measure("Cardio", 12)
    large = r.measure("Cardio", 24)
    assert large[0] > small[0] and large[1] > small[1]


def test_bold_is_at_least_as_wide_as_regular():
    r = TextRenderer()
    assert r.measure("Armed", 15, BOLD)[0] >= r.measure("Armed", 15, REGULAR)[0]


def test_empty_text_measures_zero_and_draws_nothing():
    r = TextRenderer()
    assert r.measure("", 14) == (0, 0)
    canvas = np.zeros((20, 60, 3), np.uint8)
    r.draw(canvas, "", 0, 0, 14, (255, 255, 255))
    assert canvas.sum() == 0


def test_drawing_actually_marks_the_canvas():
    r = TextRenderer()
    canvas = np.zeros((40, 200, 3), np.uint8)
    r.draw(canvas, "SPM", 10, 10, 18, (255, 255, 255), SEMIBOLD)
    assert canvas.sum() > 0


def test_text_is_clipped_at_the_canvas_edge_instead_of_raising():
    r = TextRenderer()
    canvas = np.zeros((30, 80, 3), np.uint8)
    for x, y in ((-40, -20), (70, 25), (500, 500), (-500, 5)):
        r.draw(canvas, "Cardio Surfers", x, y, 16, (255, 255, 255))
    assert canvas.shape == (30, 80, 3)


def test_masks_are_cached_and_bounded():
    r = TextRenderer()
    for i in range(700):
        r.measure(f"line {i}", 12)
    assert len(r._masks) <= 512


def test_non_ascii_renders_rather_than_becoming_question_marks():
    """The old Hershey font could not draw these at all."""
    r = TextRenderer()
    assert r.measure("240 × 135", 14)[0] > 0


# -- theme --------------------------------------------------------------------

def _luminance(bgr):
    b, g, r = (c / 255.0 for c in bgr)
    def lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(fg, bg):
    a, b = _luminance(fg), _luminance(bg)
    lo, hi = min(a, b), max(a, b)
    return (hi + 0.05) / (lo + 0.05)


def test_body_text_meets_wcag_aa_on_both_surfaces():
    for surface in (T.BG, T.SURFACE):
        assert contrast(T.TEXT, surface) >= 4.5
        assert contrast(T.TEXT_DIM, surface) >= 4.5


def test_accents_are_readable_on_the_surfaces():
    for accent in (T.ACCENT, T.WARN, T.DANGER, T.INFO):
        assert contrast(accent, T.SURFACE) >= 3.0


def test_dark_text_on_a_filled_accent_pill_is_readable():
    """Filled pills invert: BG text on an accent fill."""
    for accent in (T.ACCENT, T.WARN, T.DANGER):
        assert contrast(T.BG, accent) >= 4.5


def test_the_overlay_colour_key_is_pure_black():
    """The key must be a colour the HUD never paints, or holes appear in it."""
    assert T.OVERLAY_KEY == (0, 0, 0)
    assert T.BG != T.OVERLAY_KEY, "the ground must not vanish in overlay mode"
    assert T.SURFACE != T.OVERLAY_KEY


# -- game overlay geometry -----------------------------------------------------

SCREENS = [(1280, 720), (1920, 1080), (2048, 1152), (2560, 1440), (3840, 2160)]


def test_colorref_conversion_swaps_bgr_to_rgb_order():
    # BGR (0,0,255) is pure red; COLORREF 0x00BBGGRR is 0x0000FF.
    assert go.bgr_to_colorref((0, 0, 255)) == 0x0000FF
    assert go.bgr_to_colorref((255, 0, 0)) == 0xFF0000
    assert go.bgr_to_colorref((0, 0, 0)) == 0x000000


def test_every_overlay_position_stays_on_screen():
    for w, h in SCREENS:
        th = go.overlay_theme(w, h)
        for key, _ in go.POSITIONS:
            px, py, pw, ph = go.overlay_layout(w, h, th, key)["panel"]
            assert px >= 0 and py >= 0, f"{key} off the top/left at {w}x{h}"
            assert px + pw <= w and py + ph <= h, f"{key} off the edge at {w}x{h}"


def test_positions_actually_anchor_where_they_say():
    w, h = 1920, 1080
    th = go.overlay_theme(w, h)
    panels = {k: go.overlay_layout(w, h, th, k)["panel"] for k, _ in go.POSITIONS}
    left = panels["top-left"]
    centre = panels["top-centre"]
    right = panels["top-right"]
    assert left[0] < centre[0] < right[0]
    assert abs((centre[0] + centre[2] / 2) - w / 2) <= 1
    assert right[0] + right[2] <= w
    for key in ("top-left", "top-centre", "top-right"):
        assert panels[key][1] < h / 2, f"{key} should hug the top"
    for key in ("bottom-left", "bottom-centre", "bottom-right"):
        assert panels[key][1] > h / 2, f"{key} should hug the bottom"


def test_the_default_position_is_top_left():
    """It is the corner least likely to sit over the game's own HUD."""
    assert go.DEFAULT_POSITION == "top-left"
    w, h = 1920, 1080
    th = go.overlay_theme(w, h)
    assert go.overlay_layout(w, h, th)["panel"] ==         go.overlay_layout(w, h, th, "top-left")["panel"]


def test_an_unknown_position_falls_back_to_the_default():
    w, h = 1920, 1080
    th = go.overlay_theme(w, h)
    assert go.overlay_layout(w, h, th, "middle-of-nowhere")["panel"] ==         go.overlay_layout(w, h, th, go.DEFAULT_POSITION)["panel"]


def test_the_panel_is_small_relative_to_the_screen():
    """It sits over a game: it must not dominate the display."""
    for w, h in SCREENS:
        th = go.overlay_theme(w, h)
        _, _, pw, ph = go.overlay_layout(w, h, th)["panel"]
        assert pw <= w * 0.22, f"panel too wide at {w}x{h}: {pw}"
        assert ph <= h * 0.30, f"panel too tall at {w}x{h}: {ph}"


def test_overlay_children_stay_inside_the_panel():
    for w, h in SCREENS:
        th = go.overlay_theme(w, h)
        for key, _ in go.POSITIONS:
            rects = go.overlay_layout(w, h, th, key)
            px, py, pw, ph = rects["panel"]
            for name in ("close", "title", "camera", "status"):
                cx, cy, cw, ch = rects[name]
                assert cw > 0 and ch > 0, f"{name} collapsed at {w}x{h}"
                assert px <= cx and py <= cy, f"{name} escaped {key} at {w}x{h}"
                assert cx + cw <= px + pw and cy + ch <= py + ph,                     f"{name} escaped {key} at {w}x{h}"


def test_the_close_button_sits_in_the_panel_top_right():
    rects = go.overlay_layout(1920, 1080, go.overlay_theme(1920, 1080))
    px, py, pw, _ = rects["panel"]
    cx, cy, cw, _ = rects["close"]
    assert cx + cw <= px + pw
    assert cx > px + pw / 2
    assert cy < py + 60


def test_edge_frame_paints_only_the_border():
    canvas = np.zeros((100, 200, 3), np.uint8)
    go.draw_edge_frame(canvas, 200, 100, 5, (0, 255, 0))
    assert canvas[0, 0].tolist() == [0, 255, 0]
    assert canvas[50, 100].tolist() == [0, 0, 0], "the middle must stay click-through"
    assert canvas[99, 199].tolist() == [0, 255, 0]


def test_overlay_reports_platform_support_without_touching_win32():
    overlay = go.GameOverlay("nothing", T.OVERLAY_KEY)
    assert overlay.attached is False
    assert isinstance(overlay.available_here, bool)
    if not go.available():
        assert overlay.attach() is False
        assert "Windows" in (overlay.error or "")


def test_detach_is_safe_when_never_attached():
    go.GameOverlay("nothing", T.OVERLAY_KEY).detach()


# -- overlay arm control and focus handback ------------------------------------

def test_the_panel_has_an_arm_control_beside_the_close_button():
    """Restarting a run should not need the app window."""
    for w, h in SCREENS:
        th = go.overlay_theme(w, h)
        for key, _ in go.POSITIONS:
            rects = go.overlay_layout(w, h, th, key)
            px, py, pw, ph = rects["panel"]
            ax, ay, aw, ah = rects["arm"]
            cx, _, _, _ = rects["close"]
            assert aw > 0 and ah > 0
            assert ax + aw <= cx, f"arm overlaps close at {w}x{h} {key}"
            assert px <= ax and ax + aw <= px + pw, f"arm escaped at {w}x{h} {key}"
            assert py <= ay and ay + ah <= py + ph


def test_the_title_never_runs_under_the_arm_control():
    for w, h in SCREENS:
        th = go.overlay_theme(w, h)
        tx, _, tw, _ = go.overlay_layout(w, h, th)["title"]
        ax, _, _, _ = go.overlay_layout(w, h, th)["arm"]
        assert tw > 0
        assert tx + tw <= ax, f"title overruns the arm button at {w}x{h}"


def test_focus_helpers_fail_soft_on_a_dead_window():
    assert go.focus_window(0) is False
    assert go.focus_window(123456789) is False


def test_foreground_window_returns_a_pair():
    hwnd, title = go.foreground_window()
    assert isinstance(hwnd, int)
    assert isinstance(title, str)
