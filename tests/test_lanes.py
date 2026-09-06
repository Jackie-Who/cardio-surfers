"""LaneFSM (PLAN §12.2): hysteresis, doubles, no dwell, logged refusals."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cardio_surfers.events import LaneChange, Suppressed, SuppressReason  # noqa: E402
from cardio_surfers.keys import PressQueue  # noqa: E402
from cardio_surfers.lanes import CENTRE, LEFT, RIGHT, LaneFSM  # noqa: E402

BL, BR, H = 0.35, 0.65, 0.03


def make_fsm() -> LaneFSM:
    return LaneFSM(boundary_left=BL, boundary_right=BR, hysteresis=H)


def changes(events):
    return [e for e in events if isinstance(e, LaneChange)]


def suppressions(events, reason):
    return [e for e in events if isinstance(e, Suppressed) and e.reason is reason]


def test_boundary_straddling_yields_exactly_one_event():
    """Noise around a boundary must not machine-gun lane changes."""
    fsm = make_fsm()
    t = 0.0
    all_events = []
    # Drift into LEFT clearly, then jitter tightly around the boundary.
    for cx in [0.50, 0.40, 0.31, 0.33, 0.35, 0.34, 0.36, 0.33, 0.37, 0.34]:
        all_events += fsm.tick(cx, t)
        t += 33.0
    assert len(changes(all_events)) == 1
    assert fsm.lane == LEFT


def test_single_frame_sweep_is_delta_two():
    """0 -> 2 in one frame: |2-0| presses (PLAN §6.2)."""
    fsm = make_fsm()
    fsm.tick(0.20, 0.0)          # into LEFT
    assert fsm.lane == LEFT
    events = fsm.tick(0.80, 33.0)  # all the way to RIGHT in one frame
    lane_changes = changes(events)
    assert len(lane_changes) == 1
    assert lane_changes[0].delta == 2
    assert fsm.lane == RIGHT


def test_two_frame_sweep_is_two_singles():
    """0 -> 1 -> 2 across frames: one press, then another."""
    fsm = make_fsm()
    fsm.tick(0.20, 0.0)
    events = []
    events += fsm.tick(0.50, 33.0)
    events += fsm.tick(0.80, 66.0)
    deltas = [e.delta for e in changes(events)]
    assert deltas == [1, 1]


def test_no_dwell_requirement_blocks_a_fast_sweep():
    """Left <-> right as fast as the camera can see it (PLAN §6.2)."""
    fsm = make_fsm()
    fsm.tick(0.20, 0.0)
    total = 0
    t = 33.0
    for cx in [0.80, 0.20, 0.80, 0.20]:
        total += sum(abs(e.delta) for e in changes(fsm.tick(cx, t)))
        t += 33.0
    assert total == 8  # four full double-crossings

def test_presses_are_spaced_by_the_gap():
    """A delta-2 lane change becomes two presses >= gap_ms apart (PLAN §6.2)."""
    queue = PressQueue(gap_ms=110.0)
    queue.enqueue("right", "lane", 0.0)
    queue.enqueue("right", "lane", 0.0)

    assert [p.key for p in queue.drain(0.0)] == ["right"]
    assert queue.drain(50.0) == []          # gap not yet elapsed
    assert queue.drain(109.0) == []
    assert [p.key for p in queue.drain(110.0)] == ["right"]
    assert len(queue) == 0


def test_hysteresis_hold_is_logged_not_silent():
    """§6.3: sway across a boundary without clearance -> Suppressed(HYSTERESIS)."""
    fsm = make_fsm()
    fsm.tick(0.50, 0.0)
    # Cross the raw boundary but stay inside the hysteresis band.
    events = fsm.tick(BL - 0.01, 33.0)
    assert changes(events) == []
    held = suppressions(events, SuppressReason.HYSTERESIS)
    assert len(held) == 1
    assert "cx=" in (held[0].detail or "")


def test_post_jump_freeze_suppresses_and_holds_state():
    """Lane events are suppressed and logged during the post-jump window."""
    fsm = make_fsm()
    fsm.tick(0.50, 0.0)
    events = fsm.tick(0.20, 33.0, freeze=True)
    assert changes(events) == []
    assert len(suppressions(events, SuppressReason.POST_JUMP_LANE)) == 1
    assert fsm.lane == CENTRE
    # After the freeze the real position is picked up cleanly.
    events = fsm.tick(0.20, 200.0)
    assert [e.delta for e in changes(events)] == [-1]
