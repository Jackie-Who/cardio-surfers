"""VerticalFSM: two absolute lines, one event per crossing, refusals logged."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cardio_surfers.events import Duck, Jump, Log, Suppressed, SuppressReason  # noqa: E402
from cardio_surfers.vertical import DUCK, JUMP, NEUTRAL, VerticalFSM  # noqa: E402

# Lines at 0.25 (jump) and 0.55 (duck); shoulders rest around 0.40.
JUMP_Y, DUCK_Y, REST = 0.25, 0.55, 0.40


def make_fsm() -> VerticalFSM:
    return VerticalFSM(jump_y=JUMP_Y, duck_y=DUCK_Y, exit_band=0.02,
                       refractory_ms=400.0, max_jump_ms=800.0, max_duck_ms=1500.0)


def run_sequence(fsm, samples, dt_ms=33.0, t0=0.0):
    events = []
    t = t0
    for shoulder_y in samples:
        events += fsm.tick(shoulder_y, t)
        t += dt_ms
    return events, t


def test_crossing_the_jump_line_fires_once():
    fsm = make_fsm()
    # Rise past 0.25, come back down.
    events, _ = run_sequence(fsm, [REST, 0.35, 0.28, 0.22, 0.20, 0.24, 0.32, REST, REST])
    assert len([e for e in events if isinstance(e, Jump)]) == 1
    assert fsm.state == NEUTRAL


def test_crossing_the_duck_line_fires_once():
    fsm = make_fsm()
    events, _ = run_sequence(fsm, [REST, 0.48, 0.53, 0.58, 0.60, 0.56, 0.50, REST])
    assert len([e for e in events if isinstance(e, Duck)]) == 1


def test_staying_between_the_lines_fires_nothing():
    """The whole point of absolute lines: ordinary movement is quiet."""
    fsm = make_fsm()
    events, _ = run_sequence(fsm, [0.30, 0.35, 0.40, 0.45, 0.50, 0.45, 0.35] * 4)
    assert events == []


def test_lines_do_not_follow_the_body():
    """Settle 0.08 lower for six seconds: still no duck, because the duck line
    is a fixed screen position and 0.48 is above it."""
    fsm = make_fsm()
    events, _ = run_sequence(fsm, [0.48] * 180)
    assert events == []
    assert fsm.state == NEUTRAL


def test_double_bounce_inside_refractory_is_one_jump_plus_suppression():
    fsm = make_fsm()
    events, _ = run_sequence(fsm, [REST, 0.20, 0.20, 0.35, 0.20, 0.20, 0.35])
    jumps = [e for e in events if isinstance(e, Jump)]
    refused = [
        e for e in events
        if isinstance(e, Suppressed) and e.reason is SuppressReason.REFRACTORY
    ]
    assert len(jumps) == 1
    assert len(refused) == 1
    assert "remaining=" in (refused[0].detail or "")


def test_second_jump_after_refractory_fires():
    fsm = make_fsm()
    _, t = run_sequence(fsm, [REST, 0.20, 0.20, 0.35])
    events, _ = run_sequence(fsm, [REST, 0.20, 0.20, 0.35], t0=t + 500.0)
    assert len([e for e in events if isinstance(e, Jump)]) == 1


def test_exit_band_stops_boundary_chatter():
    """Hovering on the line must not fire once per crossing: 10 crossings
    inside the hold timeout produce exactly one jump."""
    fsm = make_fsm()
    events, _ = run_sequence(fsm, [REST] + [0.249, 0.251] * 10)   # ~0.7 s
    assert len([e for e in events if isinstance(e, Jump)]) == 1


def test_hovering_past_the_hold_timeout_re_arms_rather_than_sticking():
    """Sitting on the line longer than a jump can last releases the state --
    that is the timeout doing its job, not chatter."""
    fsm = make_fsm()
    events, _ = run_sequence(fsm, [REST] + [0.249, 0.251] * 30)   # ~2 s
    jumps = [e for e in events if isinstance(e, Jump)]
    assert 1 < len(jumps) <= 3, f"{len(jumps)} jumps over 30 crossings"


def test_holding_a_duck_times_out_and_needs_a_neutral_pass():
    fsm = make_fsm()
    events, t = run_sequence(fsm, [0.60] * 60)      # ~2 s, past max_duck_ms
    assert len([e for e in events if isinstance(e, Duck)]) == 1
    assert fsm.state == NEUTRAL
    assert any(isinstance(e, Log) and "released" in e.message for e in events)

    # Still below the duck line: must NOT re-fire.
    events, t = run_sequence(fsm, [0.60] * 30, t0=t)
    assert [e for e in events if isinstance(e, Duck)] == []

    # Come back between the lines, then duck again: fires.
    _, t = run_sequence(fsm, [REST] * 20, t0=t)
    events, _ = run_sequence(fsm, [0.60] * 10, t0=t)
    assert len([e for e in events if isinstance(e, Duck)]) == 1


def test_no_lines_means_no_events():
    fsm = VerticalFSM(jump_y=None, duck_y=None, exit_band=0.02, refractory_ms=400.0,
                      max_jump_ms=800.0, max_duck_ms=1500.0)
    assert not fsm.lines_set
    events, _ = run_sequence(fsm, [0.1, 0.9, 0.1])
    assert events == []


def test_set_lines_arms_the_fsm():
    fsm = VerticalFSM(jump_y=None, duck_y=None, exit_band=0.02, refractory_ms=400.0,
                      max_jump_ms=800.0, max_duck_ms=1500.0)
    fsm.set_lines(JUMP_Y, DUCK_Y)
    assert fsm.lines_set
    events, _ = run_sequence(fsm, [REST, 0.20])
    assert len([e for e in events if isinstance(e, Jump)]) == 1


def test_state_exposed_for_the_run_pause():
    fsm = make_fsm()
    fsm.tick(REST, 0.0)
    fsm.tick(0.20, 33.0)
    assert fsm.state == JUMP
    assert not fsm.neutral
    assert fsm.last_edge_ms == 33.0
