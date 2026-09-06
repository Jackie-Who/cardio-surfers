"""T-pose arming: hold both arms out straight to arm the controller.

Standing in a T (arms horizontal at shoulder height, straight out) is not a
game move and is not something you do by accident while jogging, which makes
it a good arming signal: click the game's start button, step back into
position, T-pose, run. No hotkey needed, though F8 still works.

The check is geometric and pure: both wrists within `wrist_y_tol` of the
shoulder line vertically, and the wrist-to-wrist span at least
`min_span_ratio` shoulder widths. Held for `hold_ms` it triggers once; the
pose must be released before it can trigger again, and a cooldown stops a
long hold from toggling twice. No clock reads: `tick` takes `now_ms`.
"""

from __future__ import annotations

from .pose import L_SH, L_WRI, PoseFrame, R_SH, R_WRI


class TPoseDetector:
    def __init__(
        self,
        hold_ms: float,
        wrist_y_tol: float,
        min_span_ratio: float,
        cooldown_ms: float,
        visibility_threshold: float,
    ) -> None:
        self.hold_ms = float(hold_ms)
        self.wrist_y_tol = float(wrist_y_tol)
        self.min_span_ratio = float(min_span_ratio)
        self.cooldown_ms = float(cooldown_ms)
        self._vis_thr = float(visibility_threshold)

        self._held_since: float | None = None
        self._last_trigger_ms = -1e12
        self._latched = False   # triggered; wait for the pose to be released

    def posing(self, pose: PoseFrame) -> bool:
        if not pose.present or not pose.visible(L_SH, R_SH, L_WRI, R_WRI, threshold=self._vis_thr):
            return False
        xy = pose.xy
        sh_y = (xy[L_SH, 1] + xy[R_SH, 1]) / 2.0
        shoulder_width = abs(xy[L_SH, 0] - xy[R_SH, 0])
        if shoulder_width <= 1e-6:
            return False
        level = (
            abs(xy[L_WRI, 1] - sh_y) < self.wrist_y_tol
            and abs(xy[R_WRI, 1] - sh_y) < self.wrist_y_tol
        )
        span = abs(xy[L_WRI, 0] - xy[R_WRI, 0])
        return level and span >= self.min_span_ratio * shoulder_width

    def hold_progress(self, now_ms: float) -> float:
        """0..1 while the pose is being held, for on-screen feedback."""
        if self._held_since is None or self._latched:
            return 0.0
        return max(0.0, min(1.0, (now_ms - self._held_since) / self.hold_ms))

    def tick(self, pose: PoseFrame, now_ms: float) -> bool:
        """True exactly once per completed hold."""
        if not self.posing(pose):
            self._held_since = None
            self._latched = False
            return False
        if self._latched:
            return False
        if self._held_since is None:
            self._held_since = now_ms
            return False
        if now_ms - self._held_since < self.hold_ms:
            return False
        if now_ms - self._last_trigger_ms < self.cooldown_ms:
            return False
        self._last_trigger_ms = now_ms
        self._latched = True
        return True
