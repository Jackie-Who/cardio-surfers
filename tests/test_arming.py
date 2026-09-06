"""TPoseDetector: hold both arms out to arm the controller."""

from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cardio_surfers.arming import TPoseDetector  # noqa: E402
from cardio_surfers.pose import (  # noqa: E402
    L_SH,
    L_WRI,
    NUM_LANDMARKS,
    PoseFrame,
    R_SH,
    R_WRI,
)

DT_MS = 33.0


def make_detector(**kw) -> TPoseDetector:
    opts = dict(hold_ms=700.0, wrist_y_tol=0.07, min_span_ratio=2.0,
                cooldown_ms=2000.0, visibility_threshold=0.5)
    opts.update(kw)
    return TPoseDetector(**opts)


def pose_frame(l_wri, r_wri, t_ms=0.0, hide=(), sh_y=0.30) -> PoseFrame:
    xy = np.full((NUM_LANDMARKS, 2), 0.5, dtype=np.float32)
    xy[L_SH] = (0.43, sh_y)
    xy[R_SH] = (0.57, sh_y)          # shoulder width 0.14
    xy[L_WRI] = l_wri
    xy[R_WRI] = r_wri
    vis = np.full(NUM_LANDMARKS, 0.9, dtype=np.float32)
    for i in hide:
        vis[i] = 0.1
    return PoseFrame(t_ms=t_ms, seq=int(t_ms), present=True, xy=xy,
                     z=np.zeros(NUM_LANDMARKS, np.float32), visibility=vis,
                     latency_ms=0.0)


def t_pose(t_ms=0.0) -> PoseFrame:
    """Arms straight out: wrists level with the shoulders, span 0.60 = 4.3x."""
    return pose_frame((0.20, 0.30), (0.80, 0.30), t_ms)


def arms_down(t_ms=0.0) -> PoseFrame:
    return pose_frame((0.38, 0.55), (0.62, 0.55), t_ms)


def hold(detector, frame_fn, seconds, t0=0.0):
    triggers = []
    t = t0
    while t < t0 + seconds * 1000.0:
        if detector.tick(frame_fn(t), t):
            triggers.append(t)
        t += DT_MS
    return triggers, t


def test_a_held_t_pose_triggers_once():
    detector = make_detector()
    triggers, _ = hold(detector, t_pose, 2.0)
    assert len(triggers) == 1
    assert 660.0 <= triggers[0] <= 760.0, "should fire at ~700 ms"


def test_a_brief_t_pose_does_not_trigger():
    detector = make_detector()
    triggers, _ = hold(detector, t_pose, 0.5)
    assert triggers == []


def test_arms_down_is_not_a_t_pose():
    detector = make_detector()
    triggers, _ = hold(detector, arms_down, 3.0)
    assert triggers == []


def test_arms_up_is_not_a_t_pose():
    """Wrists high above the shoulder line: a cheer, not a T."""
    detector = make_detector()
    triggers, _ = hold(detector, lambda t: pose_frame((0.20, 0.10), (0.80, 0.10), t), 3.0)
    assert triggers == []


def test_narrow_arms_are_not_a_t_pose():
    """Level but tucked in: span under the required shoulder-width multiple."""
    detector = make_detector()
    triggers, _ = hold(detector, lambda t: pose_frame((0.42, 0.30), (0.58, 0.30), t), 3.0)
    assert triggers == []


def test_jogging_never_trips_it():
    """Arms swinging near the sides: nowhere near level-and-wide."""
    import math

    detector = make_detector()

    def jog(t):
        swing = 0.06 * math.sin(2 * math.pi * (140 / 60.0) * (t / 1000.0))
        return pose_frame((0.38, 0.55 + swing), (0.62, 0.55 - swing), t)

    triggers, _ = hold(detector, jog, 8.0)
    assert triggers == []


def test_holding_longer_does_not_re_trigger():
    detector = make_detector()
    triggers, _ = hold(detector, t_pose, 6.0)
    assert len(triggers) == 1, "one hold, one trigger"


def test_releasing_and_posing_again_triggers_again():
    detector = make_detector(cooldown_ms=0.0)
    triggers, t = hold(detector, t_pose, 1.5)
    _, t = hold(detector, arms_down, 1.0, t0=t)
    more, _ = hold(detector, t_pose, 1.5, t0=t)
    assert len(triggers) == 1 and len(more) == 1


def test_cooldown_blocks_a_rapid_second_arm():
    detector = make_detector(cooldown_ms=5000.0)
    triggers, t = hold(detector, t_pose, 1.5)
    _, t = hold(detector, arms_down, 0.5, t0=t)
    more, _ = hold(detector, t_pose, 1.5, t0=t)
    assert len(triggers) == 1 and more == []


def test_hidden_wrists_are_not_a_t_pose():
    detector = make_detector()
    triggers, _ = hold(
        detector, lambda t: pose_frame((0.20, 0.30), (0.80, 0.30), t, hide=(L_WRI,)), 3.0
    )
    assert triggers == []


def test_hold_progress_reports_the_fraction_held():
    detector = make_detector()
    assert detector.hold_progress(0.0) == 0.0
    detector.tick(t_pose(0.0), 0.0)
    detector.tick(t_pose(350.0), 350.0)
    assert 0.45 < detector.hold_progress(350.0) < 0.55
    detector.tick(arms_down(400.0), 400.0)
    assert detector.hold_progress(400.0) == 0.0
