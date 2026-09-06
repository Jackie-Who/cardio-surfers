"""RunDetector + GateFSM (PLAN §12.4), shoulder-bounce revision.

Synthetic `d` waveforms driven on a manual timeline: cadence, alternation,
amplitude gating, the WARNING -> LOCKED schedule, recovery, grace freezing.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cardio_surfers.events import (  # noqa: E402
    GateTransition,
    Step,
    Suppressed,
    SuppressReason,
)
from cardio_surfers.running import (  # noqa: E402
    GateFSM,
    LOCKED,
    RUNNING,
    RunDetector,
    WARNING,
)

DT_MS = 33.0


def make_detector(step_amp=0.10) -> RunDetector:
    return RunDetector(step_amp=step_amp, cadence_window_s=3.0, noise_floor_ratio=0.4)


def make_gate(floor=96.0) -> GateFSM:
    return GateFSM(floor_spm=floor, grace_ms=1500.0, warning_ms=3000.0,
                   resume_ms=1000.0)


def sine_d(t_ms: float, spm: float, amp: float) -> float:
    """Alternating swing: one full cycle = 2 steps, so freq_hz = spm / 120."""
    return amp * math.sin(2.0 * math.pi * (spm / 120.0) * (t_ms / 1000.0))


def drive(detector, gate, seconds, t0=0.0, d_fn=None, freeze=False):
    events = []
    t = t0
    while t < t0 + seconds * 1000.0:
        d = d_fn(t) if d_fn else 0.0
        events += detector.tick(d, t)
        events += gate.tick(detector.spm, t, freeze=freeze)
        t += DT_MS
    return events, t


def gate_transitions(events):
    return [e for e in events if isinstance(e, GateTransition)]


def test_synthetic_sine_at_140_spm_holds_running():
    detector, gate = make_detector(), make_gate()
    # First 3s under startup grace, exactly as the pipeline drives it: the
    # cadence window is empty at t=0 and spm ramps from zero (PLAN 7.4).
    _, t = drive(detector, gate, 3.0, d_fn=lambda t: sine_d(t, 140.0, 0.25),
                 freeze=True)
    events, _ = drive(
        detector, gate, 10.0, t0=t, d_fn=lambda t: sine_d(t, 140.0, 0.25)
    )
    assert gate.state == RUNNING
    assert gate_transitions(events) == []
    assert abs(detector.spm - 140.0) < 15.0
    steps = [e for e in events if isinstance(e, Step)]
    assert len(steps) > 15
    assert {s.foot for s in steps} == {"L", "R"}


def test_amplitude_below_step_amp_scores_nothing_and_is_logged():
    """PLAN §12.4: sub-threshold swings emit Suppressed(STEP_AMPLITUDE)."""
    detector, gate = make_detector(), make_gate()
    events, _ = drive(
        detector, gate, 6.0, d_fn=lambda t: sine_d(t, 140.0, 0.05)
    )
    assert [e for e in events if isinstance(e, Step)] == []
    refused = [
        e for e in events
        if isinstance(e, Suppressed) and e.reason is SuppressReason.STEP_AMPLITUDE
    ]
    assert refused, "half-hearted swings must be visible, not silent"
    assert gate.state == LOCKED  # no steps -> the gate walked its schedule


def test_one_sided_twitch_scores_zero_steps():
    """Alternation is mandatory: you cannot farm steps with one arm."""
    detector = make_detector()
    t = 0.0
    steps = []
    for _ in range(200):  # ~6.6s of one-sided twitching
        phase = (t / 400.0) % 2.0
        d = 0.25 if phase < 1.0 else 0.0
        steps += [e for e in detector.tick(d, t) if isinstance(e, Step)]
        t += DT_MS
    assert steps == []
    assert detector.spm == 0.0


def test_jump_and_squat_add_no_steps_and_do_not_trip_the_gate():
    """PLAN §12.4: d ~ 0 during vertical gestures; the freeze covers the gap."""
    detector, gate = make_detector(), make_gate()
    jog = lambda t: sine_d(t, 140.0, 0.25)  # noqa: E731

    events, t = drive(detector, gate, 4.0, d_fn=jog)
    steps_before = len([e for e in events if isinstance(e, Step)])

    # 1.2s gesture: both arms move together, so d collapses to ~0. The
    # pipeline passes freeze=True for exactly this window (§7.4).
    events2, t = drive(detector, gate, 1.2, t0=t, d_fn=lambda t: 0.0, freeze=True)
    assert [e for e in events2 if isinstance(e, Step)] == []
    assert gate.state == RUNNING
    assert gate_transitions(events2) == []

    events3, t = drive(detector, gate, 3.0, t0=t, d_fn=jog)
    assert gate.state == RUNNING
    assert len([e for e in events3 if isinstance(e, Step)]) > 0
    assert steps_before > 0


def test_stopping_walks_warning_then_locked_on_schedule():
    """WARNING at ~1.5s below floor, LOCKED ~3s later (PLAN §7.3)."""
    detector, gate = make_detector(), make_gate()
    _, t = drive(detector, gate, 5.0, d_fn=lambda t: sine_d(t, 140.0, 0.25))

    stop_t = t
    transitions = []
    while t < stop_t + 10000.0:
        detector.tick(0.0, t)
        transitions += gate_transitions(gate.tick(detector.spm, t))
        t += DT_MS
        if gate.state == LOCKED:
            break

    assert [x.to_state for x in transitions] == [WARNING, LOCKED]
    warn_at = transitions[0].t_ms - stop_t
    lock_at = transitions[1].t_ms - stop_t
    # spm decays as the 3s cadence window empties, so "below floor" starts
    # slightly after the last step; the schedule still has to hold roughly.
    assert warn_at < 3800.0, f"WARNING came too late: {warn_at:.0f}ms"
    assert 2900.0 <= lock_at - warn_at <= 3200.0, "WARNING->LOCKED must be ~3s"


def test_resuming_recovers_within_a_second():
    detector, gate = make_detector(), make_gate()
    _, t = drive(detector, gate, 4.0, d_fn=lambda t: sine_d(t, 140.0, 0.25))
    _, t = drive(detector, gate, 8.0, t0=t, d_fn=lambda t: 0.0)
    assert gate.state == LOCKED

    resume_t = t
    while t < resume_t + 6000.0 and gate.state != RUNNING:
        d = sine_d(t - resume_t, 140.0, 0.25)
        detector.tick(d, t)
        gate.tick(detector.spm, t)
        t += DT_MS

    assert gate.state == RUNNING
    # Recovery = cadence climbing over the floor + resume_ms sustained.
    assert t - resume_t < 3500.0


def test_grace_freeze_survives_a_long_interruption():
    """A frozen accumulator does not tick toward WARNING (PLAN §7.4)."""
    detector, gate = make_detector(), make_gate()
    _, t = drive(detector, gate, 4.0, d_fn=lambda t: sine_d(t, 140.0, 0.25))
    # 5 seconds of nothing -- far past grace_ms -- but frozen throughout.
    events, t = drive(detector, gate, 5.0, t0=t, d_fn=lambda t: 0.0, freeze=True)
    assert gate.state == RUNNING
    assert gate_transitions(events) == []
    # Unfrozen, the schedule resumes from zero.
    events2, _ = drive(detector, gate, 2.0, t0=t, d_fn=lambda t: 0.0)
    assert [x.to_state for x in gate_transitions(events2)] == [WARNING]


# -- bounce mode: one step per cycle ------------------------------------------

def bounce_d(t_ms: float, spm: float, amp: float) -> float:
    """Shoulder bounce: one dip per step, so freq_hz = spm / 60."""
    return amp * math.sin(2.0 * math.pi * (spm / 60.0) * (t_ms / 1000.0))


def test_bounce_mode_counts_one_step_per_cycle():
    detector = RunDetector(step_amp=0.025, cadence_window_s=3.0,
                           noise_floor_ratio=0.4, count_both_edges=False)
    t = 0.0
    steps = []
    while t < 6000.0:
        steps += [e for e in detector.tick(bounce_d(t, 140.0, 0.06), t) if isinstance(e, Step)]
        t += DT_MS
    assert abs(detector.spm - 140.0) < 15.0, f"spm={detector.spm}"
    # Both-edge counting would have read this as 280 spm.
    assert len(steps) < 16


def test_reset_trigger_forgets_the_half_cycle():
    """After a jump the next crossing must not be paired with a pre-jump one."""
    detector = RunDetector(step_amp=0.025, cadence_window_s=3.0,
                           noise_floor_ratio=0.4, count_both_edges=False)
    detector.tick(-0.05, 0.0)          # DOWN
    detector.reset_trigger()
    events = detector.tick(0.05, 33.0)  # UP -- but from IDLE, so no step
    assert [e for e in events if isinstance(e, Step)] == []


# -- the post-gesture pause ---------------------------------------------------

def test_pause_and_resume_keep_the_cadence_window_intact():
    """The cadence bar must not sag while a jump is in progress: the window's
    clock stops, so the steps taken before the gesture are still inside it."""
    detector = RunDetector(step_amp=0.025, cadence_window_s=3.0,
                           noise_floor_ratio=0.4, count_both_edges=False)
    t = 0.0
    while t < 2500.0:
        detector.tick(bounce_d(t, 140.0, 0.06), t)
        t += DT_MS
    before = detector.spm
    assert before >= 100      # 2.5 s of 140 spm fills the 3 s window with 5 steps

    detector.pause(t)
    assert detector.paused
    t += 1000.0                      # a full second of gesture, no ticks
    detector.resume(t)
    assert not detector.paused

    detector.tick(0.0, t)            # recompute the window
    assert detector.spm >= before - 5, f"cadence sagged: {before} -> {detector.spm}"


def test_resume_without_pause_is_a_no_op():
    detector = RunDetector(step_amp=0.025, cadence_window_s=3.0,
                           noise_floor_ratio=0.4, count_both_edges=False)
    detector.resume(1000.0)
    assert not detector.paused
