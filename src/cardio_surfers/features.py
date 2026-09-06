"""Landmarks -> features (v5: two absolute lines, nothing derived).

The shoulder line is the focus:

- **Lanes**: the torso centre line `cx` (shoulder midpoint, optionally
  blended toward the hips) against the three calibrated zones.
- **Jump / duck**: the filtered shoulder line `shoulder_y`, a plain fraction
  of frame height, compared by the vertical FSM against two absolute lines on
  the screen. Nothing here is relative to a neutral posture, so nothing moves
  when you step closer or slouch.
- **Running**: the **shoulder bounce**, also in frame units -- a fast-filtered
  shoulder height detrended by a short rolling mean.

`scale` (shoulder width x a ratio) survives only for the optional arm/knee
differentials. Normalized space only (PLAN §16.4). No clock reads and no I/O
(PLAN §16.1): `update` takes `now_ms`.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque

from .filters import make_filter
from .pose import (
    L_ELB,
    L_HIP,
    L_KNEE,
    L_SH,
    L_WRI,
    R_ELB,
    R_HIP,
    R_KNEE,
    R_SH,
    R_WRI,
    PoseFrame,
)

EPS = 1e-6
BOUNCE, ARM, KNEE, NONE = "bounce", "arm", "knee", "none"


@dataclass(frozen=True)
class Features:
    """One frame of derived quantities. Frame units unless stated."""

    t_ms: float
    ok: bool
    missing: tuple[str, ...]
    cx: float               # torso centre line x, filtered
    d: float                # running signal: bounce (frame units) or arm/knee (scale units)
    scale: float            # shoulder_width * scale_ratio; arm/knee modes only
    shoulder_y: float       # filtered shoulder line y -- what the vertical FSM compares
    shoulder_x0: float      # shoulder line extent, for drawing
    shoulder_x1: float
    shoulder_width: float
    run_mode: str           # bounce | arm | knee | none
    hips_visible: bool


class FeatureExtractor:
    """Stateful (filters) but clock-free and I/O-free."""

    def __init__(self, config) -> None:
        oe = config.section("filters.one_euro")
        kind = config.get("filters.type")
        ema = float(config.get("filters.ema_alpha"))
        self._f_cx = make_filter(kind, oe["cx"], ema)
        self._f_sy = make_filter(kind, oe["shoulder_y"], ema)
        self._f_scale = make_filter(kind, oe["scale"], ema)
        self._f_d = make_filter(kind, oe["d"], ema)
        # The bounce needs a much lighter filter than the gesture signals: a
        # 2.5 Hz oscillation at min_cutoff=1 Hz would be halved.
        self._f_bounce = make_filter(kind, oe["bounce"], ema)

        self._vis_thr = float(config.get("pose.visibility_threshold"))
        self._run_signal = config.get("features.run_signal")
        self._arm_landmark = config.get("features.arm_landmark")
        self._scale_ratio = float(config.get("features.scale_ratio"))
        self._hip_weight = float(config.get("features.cx_hip_weight"))

        # Bounce detrend: short rolling mean of the fast-filtered shoulder y.
        self._detrend_ms = float(config.get("running.bounce_detrend_window_s")) * 1000.0
        self._detrend: Deque[tuple[float, float]] = deque()
        self._detrend_sum = 0.0

        self._last_scale: float | None = None
        self._last_shoulder_y: float | None = None
        self._last_run_mode = NONE

    @property
    def run_mode(self) -> str:
        return self._last_run_mode

    # -- per-frame -----------------------------------------------------------

    def update(self, pose: PoseFrame, now_ms: float) -> Features:
        thr = self._vis_thr

        # Shoulders are non-negotiable: without them there is no shoulder line.
        if not pose.present or not pose.visible(L_SH, R_SH, threshold=thr):
            missing = ("no_body",) if not pose.present else tuple(
                pose.missing(L_SH, R_SH, threshold=thr)
            )
            return self._invalid(pose.t_ms, missing)

        xy = pose.xy
        sh_mid_x = (xy[L_SH, 0] + xy[R_SH, 0]) / 2.0
        sh_mid_y = (xy[L_SH, 1] + xy[R_SH, 1]) / 2.0
        shoulder_width = abs(xy[L_SH, 0] - xy[R_SH, 0])

        raw_scale = max(shoulder_width * self._scale_ratio, EPS)
        self._last_scale = raw_scale

        # Torso centre line: the shoulder midpoint, optionally blended toward
        # the hips with a weight that ramps up smoothly from the visibility
        # threshold (a hard on/off there read as sway at the frame edge).
        hip_conf = float(min(pose.visibility[L_HIP], pose.visibility[R_HIP]))
        hips_ok = hip_conf >= thr
        raw_cx = sh_mid_x
        if self._hip_weight > 0.0 and hips_ok:
            ramp = min(1.0, max(0.0, (hip_conf - thr) / max(1e-6, 1.0 - thr)))
            w = self._hip_weight * ramp
            hip_mid_x = (xy[L_HIP, 0] + xy[R_HIP, 0]) / 2.0
            raw_cx = (1.0 - w) * sh_mid_x + w * hip_mid_x

        cx = self._f_cx.update(float(raw_cx), now_ms)
        shoulder_y = self._f_sy.update(float(sh_mid_y), now_ms)
        scale = max(self._f_scale.update(float(raw_scale), now_ms), EPS)
        self._last_shoulder_y = shoulder_y

        bounce_raw = self._bounce(float(sh_mid_y), now_ms)
        d_raw, run_mode = self._run_signal_value(pose, sh_mid_y, scale, bounce_raw)
        d = bounce_raw if run_mode == BOUNCE else self._f_d.update(d_raw, now_ms)
        self._last_run_mode = run_mode

        return Features(
            t_ms=pose.t_ms,
            ok=True,
            missing=(),
            cx=cx,
            d=d,
            scale=scale,
            shoulder_y=shoulder_y,
            shoulder_x0=float(min(xy[L_SH, 0], xy[R_SH, 0])),
            shoulder_x1=float(max(xy[L_SH, 0], xy[R_SH, 0])),
            shoulder_width=float(shoulder_width),
            run_mode=run_mode,
            hips_visible=hips_ok,
        )

    # -- internals -----------------------------------------------------------

    def _bounce(self, sh_mid_y: float, now_ms: float) -> float:
        """Shoulder oscillation about its own short-term mean, frame units, +up."""
        fast = self._f_bounce.update(sh_mid_y, now_ms)
        self._detrend.append((now_ms, fast))
        self._detrend_sum += fast
        cutoff = now_ms - self._detrend_ms
        while self._detrend and self._detrend[0][0] < cutoff:
            _, old = self._detrend.popleft()
            self._detrend_sum -= old
        mean = self._detrend_sum / len(self._detrend)
        return mean - fast

    def _run_signal_value(
        self, pose: PoseFrame, sh_mid_y: float, scale: float, bounce: float
    ) -> tuple[float, str]:
        want = self._run_signal
        if want == BOUNCE:
            return bounce, BOUNCE

        thr = self._vis_thr
        xy = pose.xy
        if want in ("auto", ARM):
            left, right = (L_ELB, R_ELB) if self._arm_landmark == "elbow" else (L_WRI, R_WRI)
            if pose.visible(left, right, threshold=thr):
                lift_l = (sh_mid_y - xy[left, 1]) / scale
                lift_r = (sh_mid_y - xy[right, 1]) / scale
                return float(lift_r - lift_l), ARM
            if self._arm_landmark != "elbow" and pose.visible(L_ELB, R_ELB, threshold=thr):
                lift_l = (sh_mid_y - xy[L_ELB, 1]) / scale
                lift_r = (sh_mid_y - xy[R_ELB, 1]) / scale
                return float(lift_r - lift_l), ARM
            if want == ARM:
                return 0.0, NONE

        if want in ("auto", KNEE) and pose.visible(L_HIP, R_HIP, L_KNEE, R_KNEE, threshold=thr):
            hip_mid_y = (xy[L_HIP, 1] + xy[R_HIP, 1]) / 2.0
            lift_l = (hip_mid_y - xy[L_KNEE, 1]) / scale
            lift_r = (hip_mid_y - xy[R_KNEE, 1]) / scale
            return float(lift_r - lift_l), KNEE

        return 0.0, NONE

    def _invalid(self, t_ms: float, missing: tuple[str, ...]) -> Features:
        return Features(
            t_ms=t_ms,
            ok=False,
            missing=missing,
            cx=0.5,
            d=0.0,
            scale=self._last_scale or EPS,
            shoulder_y=self._last_shoulder_y or 0.0,
            shoulder_x0=0.0,
            shoulder_x1=0.0,
            shoulder_width=0.0,
            run_mode=NONE,
            hips_visible=False,
        )
