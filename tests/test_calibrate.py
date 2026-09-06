"""CalibrationSession driven with synthetic features on a manual timeline.

Absolute-line edition: the JUMP and DUCK lines come out as plain screen
positions placed off the jogging shoulder line; compact lanes clamp the
hysteresis band instead of refusing.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cardio_surfers.calibrate import CalibrationSession  # noqa: E402
from cardio_surfers.config import CALIBRATION_SCHEMA, load_config  # noqa: E402
from cardio_surfers.events import Log  # noqa: E402
from cardio_surfers.features import Features  # noqa: E402

DT_MS = 33.0
ABSENT_CONFIG = os.path.join(os.path.dirname(__file__), "no-such-config.json")

STANDING_Y = 0.30


def feats(t_ms, cx=0.5, d=0.0, shoulder_y=STANDING_Y) -> Features:
    return Features(
        t_ms=t_ms, ok=True, missing=(), cx=cx, d=d, scale=0.19,
        shoulder_y=shoulder_y, shoulder_x0=0.43, shoulder_x1=0.57,
        shoulder_width=0.14, run_mode="bounce", hips_visible=True,
    )


def run_session(x_left, x_right, sway=0.005, jump_rise=0.15, duck_drop=0.30,
                bounce=0.02, jog_shoulder_y=0.32):
    """Feed each step what a cooperative human would produce."""
    config, _ = load_config(ABSENT_CONFIG)
    session = CalibrationSession(config)
    events = []
    t = 0.0
    for _ in range(4000):
        step = session.step_idx
        secs = t / 1000.0
        if step == 0:
            f = feats(t)
        elif step == 1:
            f = feats(t, cx=x_left)
        elif step == 2:
            f = feats(t, cx=x_right)
        elif step == 3:
            hop = jump_rise * max(0.0, math.sin(2 * math.pi * secs))
            f = feats(t, shoulder_y=STANDING_Y - hop)
        elif step == 4:
            f = feats(t, shoulder_y=STANDING_Y + duck_drop)
        else:
            sway_x = 0.5 + sway * math.sin(2 * math.pi * 0.7 * secs)
            f = feats(t, cx=sway_x, d=bounce * math.sin(2 * math.pi * 2.3 * secs),
                      shoulder_y=jog_shoulder_y)
        session.tick(f, t)
        events += session.events
        session.events.clear()
        t += DT_MS
        if session.finished:
            break
    return session, events


def test_lines_are_absolute_positions_off_the_jogging_posture():
    session, _ = run_session(0.30, 0.70, jump_rise=0.15, duck_drop=0.30,
                             jog_shoulder_y=0.32)
    assert session.finished
    result = session.result
    # jump line = jogging shoulder line - 0.55 * rise
    assert abs(result["vertical.jump_y"] - (0.32 - 0.55 * 0.15)) < 0.005
    # duck line = jogging shoulder line + 0.75 * drop
    assert abs(result["vertical.duck_y"] - (0.32 + 0.75 * 0.30)) < 0.005
    assert result["vertical.jump_y"] < result["vertical.duck_y"]
    assert session.measured["standing_shoulder_y"] == STANDING_Y
    assert session.measured["jog_shoulder_y"] == 0.32


def test_measured_rise_and_drop_come_from_the_shoulder_line():
    session, _ = run_session(0.30, 0.70, jump_rise=0.15, duck_drop=0.30)
    m = session.measured
    assert abs(m["jump_rise"] - 0.15) < 0.005
    assert abs(m["duck_drop"] - 0.30) < 0.005
    assert abs(m["jump_peak_y"] - (STANDING_Y - 0.15)) < 0.005
    assert abs(m["duck_hold_y"] - (STANDING_Y + 0.30)) < 0.005


def test_compact_lanes_are_clamped_not_refused():
    session, events = run_session(0.42, 0.58, sway=0.03)
    assert session.finished, "calibration must complete in a compact space"
    result = session.result
    half_width = (result["lanes.boundary_right"] - result["lanes.boundary_left"]) / 2.0
    assert abs(half_width - 0.04) < 0.01
    assert result["lanes.hysteresis"] <= 0.6 * half_width + 1e-9
    assert any(isinstance(e, Log) and "clamped" in e.message for e in events)


def test_generous_lanes_keep_the_full_band():
    session, events = run_session(0.20, 0.80, sway=0.005)
    assert session.finished
    assert not any(isinstance(e, Log) and "clamped" in e.message for e in events)
    assert session.result["lanes.hysteresis"] >= 0.015


def test_a_tiny_side_step_keeps_re_prompting():
    session, events = run_session(0.47, 0.53)
    assert not session.finished
    assert session.step_idx == 1
    assert any(isinstance(e, Log) and "step further" in e.message for e in events)


def test_shallow_duck_is_re_prompted():
    session, events = run_session(0.30, 0.70, duck_drop=0.02)
    assert not session.finished
    assert session.step_idx == 4
    assert any(isinstance(e, Log) and "duck lower" in e.message for e in events)


def test_a_low_jump_is_re_prompted():
    session, events = run_session(0.30, 0.70, jump_rise=0.01)
    assert not session.finished
    assert session.step_idx == 3
    assert any(isinstance(e, Log) and "jump higher" in e.message for e in events)


def test_result_carries_the_calibration_schema():
    session, _ = run_session(0.30, 0.70)
    assert session.result["calibration.schema"] == CALIBRATION_SCHEMA


def test_bounce_amplitude_and_cadence_are_measured():
    session, _ = run_session(0.30, 0.70, bounce=0.02)
    result = session.result
    assert abs(result["running.bounce_amp"] - 0.30 * 0.02) < 0.002
    assert abs(session.measured["jog_cadence_spm"] - 138.0) < 12.0
    assert abs(result["running.min_cadence_spm"] - 69.0) < 6.0
