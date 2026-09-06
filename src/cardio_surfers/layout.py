"""Proportional layout: every rect is computed from the live window size.

The window is resizable, so nothing may be a hardcoded pixel constant. Each
frame the app asks for a `Layout` built from the current (width, height) and
draws into the rects it hands back. A minimum window size is enforced by the
app, so these rects never collapse to nothing.

Pure geometry: no drawing, no OpenCV, no state. That makes the whole layout
testable without opening a window, which is the only way to be sure a resize
did not push a control off-screen.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import theme as T

Rect = tuple[int, int, int, int]


def _fit(area: Rect, aspect: float) -> Rect:
    """Largest rect of the given aspect ratio centred inside `area`."""
    ax, ay, aw, ah = area
    if aw / max(1, ah) > aspect:
        h = ah
        w = int(h * aspect)
    else:
        w = aw
        h = int(w / aspect)
    return (ax + (aw - w) // 2, ay + (ah - h) // 2, max(1, w), max(1, h))


@dataclass(frozen=True)
class Layout:
    """Every rect the screens need, for one window size."""

    width: int
    height: int
    theme: T.Theme

    header: Rect
    back: Rect
    status: Rect
    content: Rect

    stage: Rect          # camera area on play/debug/calibrate
    rail: Rect           # right-hand column on play/debug
    panel: Rect          # wide left column on menu/settings
    thumb: Rect          # small camera preview on menu/settings

    @property
    def pad(self) -> int:
        return self.theme.px(T.S_4)


def build(width: int, height: int) -> Layout:
    width = max(T.MIN_W, int(width))
    height = max(T.MIN_H, int(height))
    th = T.theme_for(width, height)
    pad = th.px(T.S_4)
    header_h = th.px(56)

    header = (0, 0, width, header_h)
    back = (th.px(T.S_3), th.px(T.S_2), th.px(38), header_h - th.px(T.S_4))
    status = (width // 2, 0, width // 2 - pad, header_h)

    content = (0, header_h, width, height - header_h)
    cx, cy, cw, ch = content

    # Play / debug: camera on the left, a fixed-ish rail on the right. The
    # rail has a floor and a ceiling so it stays readable at any window size
    # without eating the stage on a wide monitor.
    rail_w = max(th.px(300), min(int(cw * 0.36), th.px(420)))
    stage_area = (cx + pad, cy + pad, cw - rail_w - pad * 3, ch - pad * 2)
    stage = _fit(stage_area, 16 / 9)
    rail = (cx + cw - rail_w - pad, cy + pad, rail_w, ch - pad * 2)

    # Menu / settings: a wide control column, with a preview at the top right.
    thumb_w = max(th.px(260), min(int(cw * 0.30), th.px(380)))
    thumb = _fit((cx + cw - thumb_w - pad, cy + pad, thumb_w, int(ch * 0.42)), 16 / 9)
    panel = (cx + pad * 2, cy + pad, cw - thumb_w - pad * 4, ch - pad * 2)

    return Layout(
        width=width,
        height=height,
        theme=th,
        header=header,
        back=back,
        status=status,
        content=content,
        stage=stage,
        rail=rail,
        panel=panel,
        thumb=thumb,
    )


def rows(rect: Rect, heights: list[int], gap: int) -> list[Rect]:
    """Stack rows of the given heights down `rect`, separated by `gap`."""
    x, y, w, _ = rect
    out = []
    for h in heights:
        out.append((x, y, w, h))
        y += h + gap
    return out


def split(rect: Rect, fractions: list[float], gap: int) -> list[Rect]:
    """Split `rect` horizontally by fraction, leaving `gap` between columns."""
    x, y, w, h = rect
    usable = w - gap * (len(fractions) - 1)
    total = sum(fractions) or 1.0
    out = []
    for frac in fractions:
        cw = int(usable * frac / total)
        out.append((x, y, cw, h))
        x += cw + gap
    return out


def inset(rect: Rect, amount: int) -> Rect:
    x, y, w, h = rect
    return (x + amount, y + amount, max(1, w - amount * 2), max(1, h - amount * 2))


def fits(rect: Rect, container: Rect) -> bool:
    """True if `rect` lies entirely inside `container` -- the resize guard."""
    rx, ry, rw, rh = rect
    cx, cy, cw, ch = container
    return rx >= cx and ry >= cy and rx + rw <= cx + cw and ry + rh <= cy + ch
