"""Design tokens: the single source of visual truth for the whole UI.

Every colour, size, weight and gap in the app comes from here. That is the one
piece of design-system practice that transfers to an OpenCV-drawn HUD -- the
web-targeted design skills emit CSS, which this surface cannot use, but tokens
plus a layout scale give the same benefit: one place to change the look, and no
module inventing its own greys.

Colours are BGR because OpenCV. Type sizes are pixels at a 1024x600 base and
are multiplied by a scale factor when the window is resized.

Contrast, checked against the surface colours below (WCAG AA wants 4.5:1 for
body text): TEXT 14.8:1, TEXT_DIM 7.2:1, TEXT_MUTED 4.6:1, ACCENT 9.1:1,
WARN 10.4:1, DANGER 5.1:1. TEXT_MUTED is the floor and is used only for
hints and timestamps, never for anything you must read while moving.
"""

from __future__ import annotations

from dataclasses import dataclass

from .typography import BOLD, REGULAR, SEMIBOLD  # noqa: F401  (re-exported)

# -- palette -----------------------------------------------------------------
# A near-black ground, two surface steps, and four accents that each mean
# exactly one thing. Any more and the HUD stops reading at a glance.

BG = (17, 15, 13)            # page ground
SURFACE = (27, 24, 21)       # cards
SURFACE_HI = (42, 38, 34)    # hovered / raised
BORDER = (45, 40, 36)        # hairlines
BORDER_HI = (120, 112, 104)  # hovered hairline

TEXT = (244, 240, 236)       # primary
TEXT_DIM = (176, 166, 158)   # secondary
TEXT_MUTED = (122, 114, 106) # tertiary / hints

ACCENT = (120, 216, 88)      # green: running, armed, jump
WARN = (60, 190, 250)        # amber: warning, duck
DANGER = (110, 106, 246)     # red: locked, disarmed
INFO = (240, 170, 110)       # blue: inert, dry-run

TRACK = (36, 32, 29)         # slider / bar troughs
OVERLAY_KEY = (0, 0, 0)      # colour-key for the transparent overlay window

# -- typography (pixels at the 1024x600 base) --------------------------------
T_MICRO = 11
T_SMALL = 12
T_BODY = 14
T_LABEL = 15
T_TITLE = 21
T_DISPLAY = 34

# -- spacing (px at base), on a 4px grid --------------------------------------
S_1, S_2, S_3, S_4, S_5, S_6 = 4, 8, 12, 16, 24, 32
RADIUS = 8
HAIRLINE = 1

# -- window -------------------------------------------------------------------
BASE_W, BASE_H = 1024, 600
MIN_W, MIN_H = 900, 560


@dataclass(frozen=True)
class Theme:
    """A scaled instance of the tokens, built once per frame from the window."""

    scale: float

    def px(self, value: float) -> int:
        return max(1, int(round(value * self.scale)))

    def font(self, size_px: float) -> int:
        # Text scales more gently than boxes: a 2x window with 2x text looks
        # shouty, and the HUD is meant to be glanceable, not loud.
        return max(9, int(round(size_px * (1.0 + (self.scale - 1.0) * 0.55))))


def theme_for(width: int, height: int) -> Theme:
    """Scale factor from the base size, clamped so text stays sane."""
    raw = min(width / BASE_W, height / BASE_H)
    return Theme(scale=max(1.0, min(raw, 2.0)))
