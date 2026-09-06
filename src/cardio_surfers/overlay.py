"""Drawing primitives for the app window (PLAN §10.5).

The only module that works in pixels (PLAN §16.4): everything arrives in
normalized [0,1] space and becomes pixels here, at draw time. Colours, sizes
and spacing come from theme.py; text goes through typography.py (real Segoe UI,
not OpenCV's soft stroke font); app.py composes these into screens using rects
from layout.py.

Design intent: flat and quiet. One ground, one surface step, hairline borders,
generous space, and four accents that each mean one thing. Nothing on screen
that is not the video, a control, or a number you act on.
"""

from __future__ import annotations

from typing import Deque, Sequence

import cv2
import numpy as np

from . import theme as T
from .pose import NUM_LANDMARKS, POSE_CONNECTIONS, PoseFrame
from .typography import BOLD, REGULAR, RENDERER, SEMIBOLD

FEATURE_LANDMARKS = frozenset({11, 12, 23, 24})


# -- text ---------------------------------------------------------------------

def measure(s: str, size: int, weight: str = REGULAR) -> tuple[int, int]:
    return RENDERER.measure(s, size, weight)


def text(canvas, s, xy, size: int, colour=T.TEXT, weight: str = REGULAR) -> None:
    """Draw with the top-left of the text box at `xy`."""
    RENDERER.draw(canvas, s, int(xy[0]), int(xy[1]), size, colour, weight)


def text_mid(canvas, s, x, cy, size: int, colour=T.TEXT, weight: str = REGULAR) -> None:
    """Left-aligned at `x`, vertically centred on `cy`."""
    _, h = measure(s, size, weight)
    RENDERER.draw(canvas, s, int(x), int(cy - h / 2), size, colour, weight)


def text_centred(canvas, s, cx, cy, size: int, colour=T.TEXT, weight: str = REGULAR) -> None:
    w, h = measure(s, size, weight)
    RENDERER.draw(canvas, s, int(cx - w / 2), int(cy - h / 2), size, colour, weight)


def text_right(canvas, s, rx, cy, size: int, colour=T.TEXT, weight: str = REGULAR) -> None:
    w, h = measure(s, size, weight)
    RENDERER.draw(canvas, s, int(rx - w), int(cy - h / 2), size, colour, weight)


def ellipsize(s: str, size: int, max_w: int, weight: str = REGULAR) -> str:
    """Trim to fit -- text running past its panel makes a clean layout look broken."""
    if measure(s, size, weight)[0] <= max_w:
        return s
    out = s
    while out and measure(out + "...", size, weight)[0] > max_w:
        out = out[:-1]
    return (out + "...") if out else ""


# -- surfaces -----------------------------------------------------------------

def rounded_rect(canvas, rect, radius, colour, thickness=-1) -> None:
    x, y, w, h = (int(v) for v in rect)
    r = max(0, min(int(radius), w // 2, h // 2))
    if r == 0:
        cv2.rectangle(canvas, (x, y), (x + w, y + h), colour, thickness, cv2.LINE_AA)
        return
    if thickness < 0:
        cv2.rectangle(canvas, (x + r, y), (x + w - r, y + h), colour, -1)
        cv2.rectangle(canvas, (x, y + r), (x + w, y + h - r), colour, -1)
        for cx, cy in ((x + r, y + r), (x + w - r, y + r),
                       (x + r, y + h - r), (x + w - r, y + h - r)):
            cv2.circle(canvas, (cx, cy), r, colour, -1, cv2.LINE_AA)
        return
    for (a, b), (c, d) in (((x + r, y), (x + w - r, y)),
                           ((x + r, y + h), (x + w - r, y + h)),
                           ((x, y + r), (x, y + h - r)),
                           ((x + w, y + r), (x + w, y + h - r))):
        cv2.line(canvas, (a, b), (c, d), colour, thickness, cv2.LINE_AA)
    for (cx, cy), ang in (((x + r, y + r), 180), ((x + w - r, y + r), 270),
                          ((x + w - r, y + h - r), 0), ((x + r, y + h - r), 90)):
        cv2.ellipse(canvas, (cx, cy), (r, r), ang, 0, 90, colour, thickness, cv2.LINE_AA)


def card(canvas, rect, th: T.Theme, *, fill=T.SURFACE, border=T.BORDER) -> None:
    rounded_rect(canvas, rect, th.px(T.RADIUS), fill, -1)
    if border is not None:
        rounded_rect(canvas, rect, th.px(T.RADIUS), border, T.HAIRLINE)


def hit(rect, x: int, y: int) -> bool:
    rx, ry, rw, rh = rect
    return rx <= x < rx + rw and ry <= y < ry + rh


# -- controls -----------------------------------------------------------------

def draw_button(canvas, rect, label, th: T.Theme, *, hover=False, accent=None,
                subtle=False) -> None:
    fill = T.SURFACE_HI if hover else (T.BG if subtle else T.SURFACE)
    border = (accent or T.BORDER_HI) if hover else (accent or T.BORDER)
    card(canvas, rect, th, fill=fill, border=border)
    colour = T.TEXT if (hover or accent is None) else accent
    text_centred(canvas, label, rect[0] + rect[2] // 2, rect[1] + rect[3] // 2,
                 th.font(T.T_BODY), colour, SEMIBOLD if hover else REGULAR)


def draw_back_arrow(canvas, rect, th: T.Theme, hover: bool) -> None:
    if hover:
        card(canvas, rect, th, fill=T.SURFACE_HI, border=T.BORDER_HI)
    x, y, w, h = rect
    cx, cy = x + w // 2, y + h // 2
    arm = th.px(5)
    pts = np.array([[cx + arm, cy - arm - 2], [cx - arm + 2, cy], [cx + arm, cy + arm + 2]],
                   dtype=np.int32)
    cv2.polylines(canvas, [pts], False, T.TEXT if hover else T.TEXT_DIM, 2, cv2.LINE_AA)


def draw_close_button(canvas, rect, th: T.Theme, hover: bool) -> None:
    """The X on the game overlay."""
    x, y, w, h = rect
    if hover:
        rounded_rect(canvas, rect, th.px(T.RADIUS), T.DANGER, -1)
    else:
        card(canvas, rect, th, fill=T.SURFACE, border=T.BORDER_HI)
    colour = T.BG if hover else T.TEXT_DIM
    pad = th.px(6)
    cv2.line(canvas, (x + pad, y + pad), (x + w - pad, y + h - pad), colour, 2, cv2.LINE_AA)
    cv2.line(canvas, (x + w - pad, y + pad), (x + pad, y + h - pad), colour, 2, cv2.LINE_AA)


def draw_slider(canvas, rect, value: float, th: T.Theme, *, hover=False) -> None:
    x, y, w, h = rect
    mid = y + h // 2
    bar = th.px(4)
    rounded_rect(canvas, (x, mid - bar // 2, w, bar), bar // 2, T.TRACK, -1)
    fill = int(w * max(0.0, min(1.0, value)))
    if fill > 0:
        rounded_rect(canvas, (x, mid - bar // 2, fill, bar), bar // 2, T.ACCENT, -1)
    r = th.px(9 if hover else 7)
    cv2.circle(canvas, (x + fill, mid), r, T.TEXT if hover else T.TEXT_DIM, -1, cv2.LINE_AA)


def draw_pill(canvas, rect, label, th: T.Theme, colour, *, filled=False) -> None:
    radius = rect[3] // 2
    if filled:
        rounded_rect(canvas, rect, radius, colour, -1)
        fg = T.BG
    else:
        rounded_rect(canvas, rect, radius, T.SURFACE, -1)
        rounded_rect(canvas, rect, radius, colour, T.HAIRLINE)
        fg = colour
    text_centred(canvas, label, rect[0] + rect[2] // 2, rect[1] + rect[3] // 2,
                 th.font(T.T_LABEL), fg, SEMIBOLD)


# -- the camera stage ---------------------------------------------------------

def draw_skeleton(canvas, pose: PoseFrame, rect, visibility_threshold: float,
                  th: T.Theme) -> None:
    rx, ry, rw, rh = rect
    pts = np.empty((NUM_LANDMARKS, 2), dtype=np.int32)
    pts[:, 0] = np.clip(pose.xy[:, 0] * rw + rx, rx, rx + rw - 1)
    pts[:, 1] = np.clip(pose.xy[:, 1] * rh + ry, ry, ry + rh - 1)
    vis = pose.visibility
    for a, b in POSE_CONNECTIONS:
        if vis[a] < visibility_threshold or vis[b] < visibility_threshold:
            continue
        cv2.line(canvas, tuple(pts[a]), tuple(pts[b]), T.TEXT_DIM, max(1, th.px(2)),
                 cv2.LINE_AA)
    for i in range(NUM_LANDMARKS):
        if vis[i] < visibility_threshold:
            continue
        cv2.circle(canvas, tuple(pts[i]), th.px(4 if i in FEATURE_LANDMARKS else 2),
                   T.TEXT, -1, cv2.LINE_AA)


def draw_lane_lines(canvas, rect, boundary_left: float, boundary_right: float,
                    cx: float, lane: int, th: T.Theme, *, labels=True,
                    flash: float = 0.0) -> None:
    """Two boundaries, the torso line, and a tint on the third you are in.

    `flash` in [0,1] brightens that tint briefly after a lane change, so a
    crossing reads as an event and not only as a moved line.
    """
    rx, ry, rw, rh = rect
    edges_px = (rx, int(rx + boundary_left * rw), int(rx + boundary_right * rw), rx + rw)
    zx0, zx1 = edges_px[lane], edges_px[lane + 1]
    if zx1 > zx0:
        alpha = 0.10 + 0.30 * max(0.0, min(1.0, flash))
        zone = canvas[ry:ry + rh, zx0:zx1]
        wash = np.empty_like(zone)
        wash[:] = T.ACCENT
        canvas[ry:ry + rh, zx0:zx1] = cv2.addWeighted(zone, 1.0 - alpha, wash, alpha, 0)
    for b in (boundary_left, boundary_right):
        bx = int(rx + b * rw)
        cv2.line(canvas, (bx, ry), (bx, ry + rh), T.BORDER_HI, 1, cv2.LINE_AA)
    if labels:
        edges = [rx, int(rx + boundary_left * rw), int(rx + boundary_right * rw), rx + rw]
        for i, name in enumerate(("LEFT", "CENTRE", "RIGHT")):
            colour = T.ACCENT if i == lane else T.TEXT_MUTED
            text_centred(canvas, name, (edges[i] + edges[i + 1]) // 2,
                         ry + th.px(14), th.font(T.T_MICRO), colour,
                         SEMIBOLD if i == lane else REGULAR)
    px = int(rx + cx * rw)
    cv2.line(canvas, (px, ry), (px, ry + rh), T.ACCENT, 2, cv2.LINE_AA)


def draw_threshold_lines(canvas, rect, jump_y, duck_y, th: T.Theme, *,
                         grabbed=None, handles=False, labels=True) -> None:
    rx, ry, rw, rh = rect
    for y, colour, label, key in ((jump_y, T.ACCENT, "JUMP", "jump"),
                                  (duck_y, T.WARN, "DUCK", "duck")):
        if y is None:
            continue
        y_px = int(ry + y * rh)
        if not ry <= y_px <= ry + rh:
            continue
        cv2.line(canvas, (rx, y_px), (rx + rw, y_px), colour,
                 3 if grabbed == key else 2, cv2.LINE_AA)
        if labels:
            text(canvas, label, (rx + th.px(T.S_2), y_px - th.px(T.S_4)),
                 th.font(T.T_MICRO), colour, SEMIBOLD)
        if handles:
            hw, hh = th.px(12), th.px(5)
            rounded_rect(canvas, (rx + rw - hw - th.px(2), y_px - hh, hw, hh * 2),
                         hh, colour, -1)


def draw_shoulder_line(canvas, rect, shoulder_y: float, x0: float, x1: float,
                       state: str, th: T.Theme) -> None:
    rx, ry, rw, rh = rect
    y = int(ry + shoulder_y * rh)
    if not ry <= y <= ry + rh:
        return
    pad = int(0.05 * rw)
    xa = max(rx, int(rx + x0 * rw) - pad)
    xb = min(rx + rw, int(rx + x1 * rw) + pad)
    colour = T.ACCENT if state == "JUMP" else (T.WARN if state == "DUCK" else T.TEXT)
    cv2.line(canvas, (xa, y), (xb, y), colour, th.px(3), cv2.LINE_AA)


def draw_gesture_flash(canvas, rect, label, colour, th: T.Theme) -> None:
    rx, ry, rw, rh = rect
    size = th.font(T.T_DISPLAY)
    w, h = measure(label, size, BOLD)
    pad = th.px(T.S_4)
    box = (rx + (rw - w) // 2 - pad, ry + rh // 2 - h // 2 - pad, w + pad * 2, h + pad * 2)
    rounded_rect(canvas, box, th.px(T.RADIUS), T.BG, -1)
    text_centred(canvas, label, rx + rw // 2, ry + rh // 2, size, colour, BOLD)


def draw_hold_progress(canvas, rect, progress: float, label: str, th: T.Theme) -> None:
    rx, ry, rw, rh = rect
    bar_w = int(rw * 0.44)
    x0 = rx + (rw - bar_w) // 2
    y0 = ry + rh - th.px(32)
    h = th.px(6)
    rounded_rect(canvas, (x0, y0, bar_w, h), h // 2, T.TRACK, -1)
    if progress > 0:
        rounded_rect(canvas, (x0, y0, int(bar_w * progress), h), h // 2, T.ACCENT, -1)
    text_centred(canvas, label, rx + rw // 2, y0 - th.px(T.S_3),
                 th.font(T.T_LABEL), T.ACCENT, SEMIBOLD)


def draw_stage_banner(canvas, rect, label, colour, th: T.Theme) -> None:
    rx, ry, rw, _ = rect
    size = th.font(T.T_LABEL)
    w, h = measure(label, size, SEMIBOLD)
    pad = th.px(T.S_3)
    box = (rx + (rw - w) // 2 - pad, ry + th.px(T.S_4), w + pad * 2, h + pad * 2)
    rounded_rect(canvas, box, th.px(T.RADIUS), T.BG, -1)
    rounded_rect(canvas, box, th.px(T.RADIUS), colour, T.HAIRLINE)
    text_centred(canvas, label, rx + rw // 2, box[1] + box[3] // 2, size, colour, SEMIBOLD)


# -- readouts -----------------------------------------------------------------

def draw_cadence_bar(canvas, rect, spm: float, floor: float, th: T.Theme, *,
                     held=False) -> None:
    x, y, w, h = rect
    top = max(floor * 1.6, 1.0)
    frac = max(0.0, min(1.0, spm / top))
    colour = T.ACCENT if spm >= floor else (T.WARN if spm >= floor * 0.7 else T.DANGER)
    rounded_rect(canvas, rect, th.px(T.RADIUS), T.TRACK, -1)
    if frac > 0:
        rounded_rect(canvas, (x, y, max(th.px(T.RADIUS) * 2, int(w * frac)), h),
                     th.px(T.RADIUS), colour, -1)
    notch = int(x + (floor / top) * w)
    cv2.line(canvas, (notch, y + th.px(3)), (notch, y + h - th.px(3)), T.BG, 2, cv2.LINE_AA)
    label = f"{spm:.0f} SPM"
    text_mid(canvas, label, x + th.px(T.S_3), y + h // 2, th.font(T.T_SMALL),
             T.BG if frac > 0.30 else T.TEXT, SEMIBOLD)
    if held:
        text_right(canvas, "HELD", x + w - th.px(T.S_3), y + h // 2,
                   th.font(T.T_MICRO), T.BG if frac > 0.85 else T.TEXT_DIM, SEMIBOLD)


def draw_key_pads(canvas, rect, flashes: dict, now_ms: float, flash_ms: float,
                  th: T.Theme, labels: dict) -> None:
    x, y, w, h = rect
    gap = th.px(T.S_2)
    pad_w = (w - gap * 3) // 4
    for i, action in enumerate(("left", "up", "down", "right")):
        px = x + i * (pad_w + gap)
        lit = now_ms - flashes.get(action, -1e12) < flash_ms
        rect_i = (px, y, pad_w, h)
        if lit:
            rounded_rect(canvas, rect_i, th.px(T.RADIUS), T.ACCENT, -1)
        else:
            card(canvas, rect_i, th)
        text_centred(canvas, labels.get(action, "?"), px + pad_w // 2, y + h // 2,
                     th.font(T.T_LABEL), T.BG if lit else T.TEXT_DIM, SEMIBOLD)


def draw_sparkline(canvas, rect, samples: Sequence | Deque, now_ms: float,
                   window_ms: float, y_range: float, colour, label: str,
                   th: T.Theme, threshold: float | None = None) -> None:
    rx, ry, rw, rh = rect
    card(canvas, rect, th, fill=T.SURFACE, border=None)
    mid = ry + rh // 2
    cv2.line(canvas, (rx, mid), (rx + rw, mid), T.BORDER, 1)
    if threshold is not None and y_range > 0:
        for sign in (1, -1):
            ty = int(mid - sign * (threshold / y_range) * (rh / 2))
            cv2.line(canvas, (rx, ty), (rx + rw, ty), T.BORDER_HI, 1)
    pts = []
    for t_ms, value in samples:
        age = now_ms - t_ms
        if age > window_ms:
            continue
        px = int(rx + rw - (age / window_ms) * rw)
        py = int(mid - max(-1.0, min(1.0, value / y_range)) * (rh / 2 - 2))
        pts.append((px, py))
    if len(pts) >= 2:
        cv2.polylines(canvas, [np.array(pts, dtype=np.int32)], False, colour, 1, cv2.LINE_AA)
    text(canvas, label, (rx + th.px(T.S_2), ry + th.px(T.S_1)),
         th.font(T.T_MICRO), T.TEXT_MUTED)
