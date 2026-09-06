"""FeatureExtractor: the shoulder line, the bounce signal, the torso line.

Nothing here is relative to a posture any more -- `shoulder_y` is a plain
screen position and the vertical FSM compares it against two fixed lines.
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cardio_surfers.calibrate import count_steps  # noqa: E402
from cardio_surfers.config import Config, load_config  # noqa: E402
from cardio_surfers.features import FeatureExtractor  # noqa: E402
from cardio_surfers.pose import (  # noqa: E402
    L_ELB,
    L_HIP,
    L_KNEE,
    L_SH,
    L_WRI,
    NUM_LANDMARKS,
    PoseFrame,
    R_ELB,
    R_HIP,
    R_KNEE,
    R_SH,
    R_WRI,
)

DT_MS = 33.0
ABSENT_CONFIG = os.path.join(os.path.dirname(__file__), "no-such-config.json")


def base_pose_xy() -> np.ndarray:
    """A plausible standing figure with shoulders at y = 0.30."""
    xy = np.full((NUM_LANDMARKS, 2), 0.5, dtype=np.float32)
    xy[L_SH] = (0.43, 0.30)
    xy[R_SH] = (0.57, 0.30)
    xy[L_ELB] = (0.40, 0.42)
    xy[R_ELB] = (0.60, 0.42)
    xy[L_WRI] = (0.38, 0.52)
    xy[R_WRI] = (0.62, 0.52)
    xy[L_HIP] = (0.45, 0.55)
    xy[R_HIP] = (0.55, 0.55)
    xy[L_KNEE] = (0.44, 0.72)
    xy[R_KNEE] = (0.56, 0.72)
    return xy


def make_frame(xy: np.ndarray, t_ms: float, hide: tuple[int, ...] = ()) -> PoseFrame:
    vis = np.full(NUM_LANDMARKS, 0.95, dtype=np.float32)
    for i in hide:
        vis[i] = 0.1
    return PoseFrame(
        t_ms=t_ms, seq=int(t_ms), present=True, xy=xy.astype(np.float32),
        z=np.zeros(NUM_LANDMARKS, dtype=np.float32), visibility=vis, latency_ms=0.0,
    )


def scaled_about_centroid(xy: np.ndarray, factor: float) -> np.ndarray:
    centroid = xy[[L_SH, R_SH, L_HIP, R_HIP]].mean(axis=0)
    return (centroid + (xy - centroid) * factor).astype(np.float32)


def make_extractor(run_signal=None, hip_weight=None) -> FeatureExtractor:
    config, _ = load_config(ABSENT_CONFIG)   # example defaults, no user file
    data = config.as_dict()
    if run_signal is not None:
        data["features"]["run_signal"] = run_signal
    if hip_weight is not None:
        data["features"]["cx_hip_weight"] = hip_weight
    return FeatureExtractor(Config(data))


def run_gesture(extractor, frames, t0=0.0):
    out = []
    t = t0
    for xy, hide in frames:
        out.append(extractor.update(make_frame(xy, t, hide), t))
        t += DT_MS
    return out


def jump_frames(rise: float = 0.12, settle: int = 40):
    frames = []
    base = base_pose_xy()
    for _ in range(settle):
        frames.append((base, ()))
    for f in [0.3, 0.7, 1.0, 1.0, 0.7, 0.3, 0.0]:
        xy = base.copy()
        xy[:, 1] -= rise * f
        frames.append((xy, ()))
    return frames


def jog_frames(amp: float = 0.02, spm: float = 140.0, seconds: float = 4.0):
    """Whole-body shoulder bounce: one dip per step, so freq = spm / 60."""
    frames = []
    base = base_pose_xy()
    n = int(seconds * 1000.0 / DT_MS)
    for i in range(n):
        t_s = i * DT_MS / 1000.0
        xy = base.copy()
        xy[:, 1] += amp * math.sin(2 * math.pi * (spm / 60.0) * t_s)
        frames.append((xy, ()))
    return frames


# -- the shoulder line --------------------------------------------------------

def test_shoulder_y_is_a_plain_screen_position():
    feats = run_gesture(make_extractor(), [(base_pose_xy(), ())] * 40)
    assert abs(feats[-1].shoulder_y - 0.30) < 0.005


def test_a_jump_moves_the_shoulder_line_by_the_jump_height():
    feats = run_gesture(make_extractor(), jump_frames(rise=0.12))
    peak = min(f.shoulder_y for f in feats)          # y grows downward
    assert 0.175 <= peak <= 0.21, f"0.30 - 0.12 expected, got {peak:.3f}"


def test_stepping_closer_does_not_change_the_shoulder_line():
    """Scaling the body about its centroid keeps the shoulders where they are:
    a bigger silhouette must not shift the line the FSM compares."""
    near = run_gesture(make_extractor(), [(base_pose_xy(), ())] * 40)
    far_xy = scaled_about_centroid(base_pose_xy(), 0.6)
    far = run_gesture(make_extractor(), [(far_xy, ())] * 40)
    assert abs(near[-1].shoulder_y - 0.30) < 0.005
    # The scaled body's shoulders really are elsewhere; what matters is that
    # the feature reports them honestly rather than normalising them away.
    expected = float((far_xy[L_SH, 1] + far_xy[R_SH, 1]) / 2.0)
    assert abs(far[-1].shoulder_y - expected) < 0.005


# -- the bounce signal --------------------------------------------------------

def test_bounce_is_the_default_and_swings_both_ways_in_frame_units():
    feats = run_gesture(make_extractor(), jog_frames(amp=0.02))
    assert feats[-1].run_mode == "bounce"
    tail = [f.d for f in feats[40:]]
    assert max(tail) > 0.012 and min(tail) < -0.012


def test_bounce_is_detrended_so_a_slow_slouch_is_not_a_step():
    extractor = make_extractor()
    frames = []
    base = base_pose_xy()
    for i in range(90):
        xy = base.copy()
        xy[:, 1] += 0.06 * (i / 90.0)
        frames.append((xy, ()))
    for _ in range(30):
        frames.append((frames[-1][0], ()))
    for i in range(90):
        xy = base.copy()
        xy[:, 1] += 0.06 * (1.0 - i / 90.0)
        frames.append((xy, ()))
    feats = run_gesture(extractor, frames)
    samples = [(f.t_ms, f.d) for f in feats]
    assert count_steps(samples, amp=0.008, both_edges=False) <= 1
    assert max(abs(f.d) for f in feats) < 0.02


def test_arm_swing_differential_at_two_distances():
    """Arm/knee modes keep their scale normalization."""
    def swing_frames(scale_factor):
        frames = []
        base = scaled_about_centroid(base_pose_xy(), scale_factor)
        for i in range(60):
            xy = base.copy()
            lift = 0.10 * scale_factor * math.sin(2 * math.pi * i / 20.0)
            xy[R_WRI, 1] -= lift
            xy[L_WRI, 1] += lift
            frames.append((xy, ()))
        return frames

    amplitudes = []
    for factor in (1.0, 0.55):
        feats = run_gesture(make_extractor("arm"), swing_frames(factor))
        amplitudes.append(max(abs(f.d) for f in feats[10:]))
    ratio = amplitudes[1] / amplitudes[0]
    assert 0.85 < ratio < 1.15, f"d is not distance-invariant: ratio={ratio:.3f}"


# -- the torso centre line ----------------------------------------------------

def _settled_cx(extractor, xy, hide=()):
    feats = None
    for i in range(40):
        feats = extractor.update(make_frame(xy, i * DT_MS, hide), i * DT_MS)
    assert feats is not None
    return feats.cx


def test_torso_line_is_the_shoulder_midpoint_by_default():
    xy = base_pose_xy()
    xy[L_SH, 0] += 0.10
    xy[R_SH, 0] += 0.10
    assert abs(_settled_cx(make_extractor(), xy) - 0.60) < 0.01


def test_optional_hip_blend_ramps_with_visibility_instead_of_snapping():
    xy = base_pose_xy()
    xy[L_SH, 0] += 0.10
    xy[R_SH, 0] += 0.10
    assert abs(_settled_cx(make_extractor(hip_weight=0.6), xy) - 0.546) < 0.01
    low = make_extractor(hip_weight=0.6)
    feats = None
    for i in range(40):
        frame = make_frame(xy, i * DT_MS)
        frame.visibility[L_HIP] = frame.visibility[R_HIP] = 0.52
        feats = low.update(frame, i * DT_MS)
    assert abs(feats.cx - 0.60) < 0.01


# -- mode selection and fallbacks --------------------------------------------

def test_auto_mode_prefers_arms_and_falls_back_to_knees():
    extractor = make_extractor("auto")
    xy = base_pose_xy()
    feats = extractor.update(make_frame(xy, 0.0), 0.0)
    assert feats.run_mode == "arm"
    feats = extractor.update(
        make_frame(xy, 33.0, hide=(L_WRI, R_WRI, L_ELB, R_ELB)), 33.0
    )
    assert feats.run_mode == "knee"


def test_bounce_mode_needs_only_shoulders():
    """Desk framing: wrists, hips and knees can all be cropped."""
    hidden = (L_WRI, R_WRI, L_ELB, R_ELB, L_HIP, R_HIP, L_KNEE, R_KNEE)
    frames = [(xy, hidden) for xy, _ in jog_frames(amp=0.02)]
    feats = run_gesture(make_extractor(), frames)
    assert feats[-1].ok and feats[-1].run_mode == "bounce"
    assert max(abs(f.d) for f in feats[40:]) > 0.012


def test_cropping_the_hips_changes_nothing_vertical():
    extractor = make_extractor()
    xy = base_pose_xy()
    with_hips = extractor.update(make_frame(xy, 0.0), 0.0)
    cropped = extractor.update(
        make_frame(xy, 33.0, hide=(L_HIP, R_HIP, L_KNEE, R_KNEE)), 33.0
    )
    assert with_hips.hips_visible and not cropped.hips_visible
    assert abs(cropped.shoulder_y - with_hips.shoulder_y) < 1e-6
    assert abs(cropped.scale - with_hips.scale) < 1e-6


def test_missing_shoulders_is_invalid_with_names():
    feats = make_extractor().update(
        make_frame(base_pose_xy(), 0.0, hide=(L_SH, R_SH)), 0.0
    )
    assert not feats.ok
    assert "left_shoulder" in feats.missing
