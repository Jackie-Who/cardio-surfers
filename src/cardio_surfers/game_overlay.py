"""A transparent HUD drawn on top of a fullscreen game (Windows).

The problem: in Play you need the game focused so it receives the keys, but you
also want to see the camera, the gate state and the cadence. Alt-tabbing to a
separate app window defeats the focus gate.

The solution is a Win32 **layered** window:

- `WS_EX_LAYERED` + `SetLayeredWindowAttributes(colour_key, LWA_COLORKEY)`
  makes every pixel of one exact colour invisible **and click-through** -- so a
  mostly-black canvas becomes a floating HUD with holes you can click through
  to the game underneath.
- `WS_EX_NOACTIVATE` means clicking the parts that *are* painted never steals
  keyboard focus from the game, so the X stays clickable while WASD keeps
  going where it should.
- `WS_EX_TOOLWINDOW` keeps it out of the alt-tab list, and `HWND_TOPMOST`
  keeps it above a borderless-fullscreen game.
- Dropping `WS_CAPTION | WS_THICKFRAME` removes the title bar and border.

The one hard limit: a game running in **exclusive** fullscreen (not borderless)
owns the display surface, and nothing can draw over it. Borderless/windowed
fullscreen -- which is what browser Subway Surfers uses -- works.

Everything here is Windows-only and fails soft: `attach()` returns False on any
other platform or if a call fails, and the caller falls back to the normal
window.
"""

from __future__ import annotations

import ctypes
import sys

import numpy as np

# -- Win32 constants ---------------------------------------------------------
GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_CAPTION, WS_THICKFRAME = 0x00C00000, 0x00040000
WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = 0x00080000, 0x08000000, 0x00000080
WS_EX_APPWINDOW = 0x00040000
LWA_COLORKEY = 0x00000001
SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x0002, 0x0001, 0x0010, 0x0020
SM_CXSCREEN, SM_CYSCREEN = 0, 1


def available() -> bool:
    return sys.platform == "win32"


def screen_size() -> tuple[int, int]:
    """Primary display size, or a sane default off-Windows."""
    if not available():
        return (1920, 1080)
    user32 = ctypes.windll.user32
    try:
        user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001 - already set, or not permitted
        pass
    return int(user32.GetSystemMetrics(SM_CXSCREEN)), int(user32.GetSystemMetrics(SM_CYSCREEN))


def foreground_window() -> tuple[int, str]:
    """The window with focus right now, as (hwnd, title)."""
    if not available():
        return 0, ""
    user32 = ctypes.windll.user32
    hwnd = int(user32.GetForegroundWindow())
    if not hwnd:
        return 0, ""
    length = int(user32.GetWindowTextLengthW(hwnd))
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return hwnd, buf.value


def focus_window(hwnd: int) -> bool:
    """Give focus back to `hwnd`.

    Windows refuses `SetForegroundWindow` from a process that does not already
    own the foreground -- which is fine here, because this is only ever called
    while the app itself has focus and is handing it away. The
    AttachThreadInput dance is the documented fallback for the cases where the
    plain call is still refused (a different input queue owns the target).
    """
    if not available() or not hwnd:
        return False
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    try:
        if not user32.IsWindow(hwnd):
            return False
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)          # SW_RESTORE
        if user32.SetForegroundWindow(hwnd):
            return True

        target = user32.GetWindowThreadProcessId(hwnd, None)
        current = kernel32.GetCurrentThreadId()
        if target and target != current:
            user32.AttachThreadInput(current, target, True)
            try:
                user32.BringWindowToTop(hwnd)
                ok = bool(user32.SetForegroundWindow(hwnd))
            finally:
                user32.AttachThreadInput(current, target, False)
            return ok
        return False
    except Exception:  # noqa: BLE001 - focus is best effort, never fatal
        return False


def bgr_to_colorref(colour) -> int:
    """OpenCV gives BGR tuples; COLORREF is 0x00BBGGRR."""
    b, g, r = (int(c) & 0xFF for c in colour)
    return (b << 16) | (g << 8) | r


class GameOverlay:
    """Applies and removes the layered-window styles on an OpenCV window."""

    def __init__(self, window_title: str, colour_key) -> None:
        self.window_title = window_title
        self.colour_key = colour_key
        self.attached = False
        self.available_here = available()
        self.error: str | None = None
        self._hwnd = 0
        self._saved_style: int | None = None
        self._saved_exstyle: int | None = None

    # -- lifecycle -------------------------------------------------------------

    def find_window(self) -> int:
        if not available():
            return 0
        return int(ctypes.windll.user32.FindWindowW(None, self.window_title))

    def attach(self) -> bool:
        """Turn the OpenCV window into a click-through topmost overlay."""
        if not available():
            self.error = "overlay mode is Windows-only"
            return False
        hwnd = self.find_window()
        if not hwnd:
            self.error = "could not find the app window"
            return False

        user32 = ctypes.windll.user32
        user32.SetWindowPos.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        try:
            self._hwnd = hwnd
            self._saved_style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            self._saved_exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)

            user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                (self._saved_exstyle | WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
                & ~WS_EX_APPWINDOW,
            )
            # Colour-keyed pixels are both invisible and click-through, which is
            # what lets the game receive every click outside the HUD.
            if not user32.SetLayeredWindowAttributes(
                hwnd, bgr_to_colorref(self.colour_key), 0, LWA_COLORKEY
            ):
                self.error = "SetLayeredWindowAttributes failed"
                return False
            user32.SetWindowLongW(
                hwnd, GWL_STYLE, self._saved_style & ~WS_CAPTION & ~WS_THICKFRAME
            )
            width, height = screen_size()
            user32.SetWindowPos(
                ctypes.c_void_p(hwnd), ctypes.c_void_p(-1),  # HWND_TOPMOST
                0, 0, width, height, SWP_NOACTIVATE | SWP_FRAMECHANGED,
            )
            self.attached = True
            self.error = None
            return True
        except Exception as exc:  # noqa: BLE001 - never take the app down
            self.error = f"{type(exc).__name__}: {exc}"
            return False

    def detach(self) -> None:
        """Restore the ordinary window styles."""
        if not self.attached or not available() or not self._hwnd:
            self.attached = False
            return
        user32 = ctypes.windll.user32
        user32.SetWindowPos.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        try:
            if self._saved_exstyle is not None:
                user32.SetWindowLongW(self._hwnd, GWL_EXSTYLE, self._saved_exstyle)
            if self._saved_style is not None:
                user32.SetWindowLongW(self._hwnd, GWL_STYLE, self._saved_style)
            user32.SetWindowPos(
                ctypes.c_void_p(self._hwnd), ctypes.c_void_p(-2),  # HWND_NOTOPMOST
                0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
            )
        except Exception:  # noqa: BLE001 - best effort
            pass
        self.attached = False

    def raise_above_game(self) -> None:
        """Re-assert topmost: a game going fullscreen can steal the z-order."""
        if not self.attached or not available() or not self._hwnd:
            return
        user32 = ctypes.windll.user32
        user32.SetWindowPos.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        try:
            user32.SetWindowPos(
                ctypes.c_void_p(self._hwnd), ctypes.c_void_p(-1),
                0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        except Exception:  # noqa: BLE001
            pass


POSITIONS = (
    ("top-left", "Top left"),
    ("top-centre", "Top centre"),
    ("top-right", "Top right"),
    ("bottom-left", "Bottom left"),
    ("bottom-centre", "Bottom centre"),
    ("bottom-right", "Bottom right"),
)
POSITION_KEYS = tuple(key for key, _ in POSITIONS)
DEFAULT_POSITION = "top-left"


def overlay_theme(width: int, height: int):
    """A gentler scale than the app window uses.

    The overlay panel is meant to sit out of the way, so it does not follow the
    screen the way a resizable window does -- it grows only slightly on very
    large displays and stays compact everywhere else.
    """
    from . import theme as T

    return T.Theme(scale=max(0.9, min(width / 1920.0, 1.4)))


def overlay_layout(width: int, height: int, theme, position: str = DEFAULT_POSITION) -> dict:
    """Rects for the overlay: a screen-edge frame and one small HUD panel.

    Returned in screen coordinates, so the caller draws straight onto a
    full-screen canvas. Bounds are deliberately unscaled pixels: the panel
    should be the same modest size on a 1080p and a 1440p display, not twice
    as large on the bigger one.
    """
    border = theme.px(5)
    pad = theme.px(8)
    row = theme.px(20)

    panel_w = max(220, min(int(width * 0.16), 380))
    inner_w = panel_w - pad * 2
    # The camera drives the height: sizing the panel first left wide letterbox
    # gaps either side of a 16:9 frame.
    cam_h = int(inner_w * 9 / 16)
    panel_h = pad * 4 + row + cam_h + row

    margin = border + theme.px(6)
    if position not in POSITION_KEYS:
        position = DEFAULT_POSITION
    vertical, _, horizontal = position.partition("-")
    if horizontal == "left":
        panel_x = margin
    elif horizontal == "right":
        panel_x = width - panel_w - margin
    else:
        panel_x = (width - panel_w) // 2
    panel_y = margin if vertical == "top" else height - panel_h - margin

    title_y = panel_y + pad
    cam_y = title_y + row + pad
    status_y = cam_y + cam_h + pad
    arm_w = theme.px(56)
    close_x = panel_x + panel_w - pad - row
    arm_x = close_x - theme.px(6) - arm_w
    return {
        "frame": (0, 0, width, height),
        "border": border,
        "panel": (panel_x, panel_y, panel_w, panel_h),
        "close": (close_x, title_y, row, row),
        "arm": (arm_x, title_y, arm_w, row),
        "title": (panel_x + pad, title_y, arm_x - panel_x - pad * 2, row),
        "camera": (panel_x + pad, cam_y, inner_w, cam_h),
        "status": (panel_x + pad, status_y, inner_w, row),
    }


def draw_edge_frame(canvas: np.ndarray, width: int, height: int, thickness: int,
                    colour) -> None:
    """The green frame that says the controller is live."""
    canvas[0:thickness, :] = colour
    canvas[height - thickness:height, :] = colour
    canvas[:, 0:thickness] = colour
    canvas[:, width - thickness:width] = colour
