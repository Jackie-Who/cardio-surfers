"""End-to-end through the pipeline: synthetic poses in, key presses out.

Covers the live-testing findings: jump/duck must produce their keys, a jump
must pause the running detector for a second without the cadence sagging, and
the two lines must not move when the body does.
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cardio_surfers.config import Config, load_config  # noqa: E402
from cardio_surfers.events import (  # noqa: E402
    Duck,
    EventBus,
    GateTransition,
    Jump,
    KeyPress,
    Step,
)
from cardio_surfers.keys import DryRunSender, KeyDispatcher  # noqa: E402
from cardio_surfers.pipeline import Pipeline  # noqa: E402
from cardio_surfers.pose import L_HIP, L_SH, NUM_LANDMARKS, PoseFrame, R_HIP, R_SH  # noqa: E402

DT_MS = 33.0
ABSENT_CONFIG = os.path.join(os.path.dirname(__file__), "no-such-config.json")

# Shoulders rest at 0.30; lines placed either side of that.
REST_Y = 0.30
JUMP_Y, DUCK_Y = 0.22, 0.48


def body(cx=0.5, rise=0.0) -> np.ndarray:
    """Standing figure; `rise` lifts the whole body (positive = up)."""
    xy = np.full((NUM_LANDMARKS, 2), 0.5, dtype=np.float32)
    dx = cx - 0.5
    xy[L_SH] = (0.43 + dx, REST_Y - rise)
    xy[R_SH] = (0.57 + dx, REST_Y - rise)
    xy[L_HIP] = (0.45 + dx, 0.55 - rise)
    xy[R_HIP] = (0.55 + dx, 0.55 - rise)
    return xy


def frame(xy, t_ms):
    return PoseFrame(
        t_ms=t_ms, seq=int(t_ms), present=True, xy=xy,
        z=np.zeros(NUM_LANDMARKS, np.float32),
        visibility=np.full(NUM_LANDMARKS, 0.9, np.float32), latency_ms=0.0,
    )


class Harness:
    def __init__(self, jump_y=JUMP_Y, duck_y=DUCK_Y, active=True):
        config, _ = load_config(ABSENT_CONFIG)
        data = config.as_dict()
        data["vertical"]["jump_y"] = jump_y
        data["vertical"]["duck_y"] = duck_y
        config = Config(data)
        self.bus = EventBus()
        self.events = []
        self.bus.subscribe(self.events.append)
        mapping = config.section("keys.presets")["wasd"]
        dispatcher = KeyDispatcher(self.bus, DryRunSender(), None, mapping)
        dispatcher.armed = True
        self.pipeline = Pipeline(self.bus, config, dispatcher)
        self.floor = self.pipeline.gate.floor
        if active:
            # Arming is what makes the gate live and primes the cadence
            # window; without it the pipeline sits inactive by design.
            self.pipeline.set_active(True, 0.0)
        self.t = 0.0

    def feed(self, xy):
        self.pipeline.tick(frame(xy, self.t), self.t)
        self.t += DT_MS

    def jog(self, seconds, amp=0.02, spm=140.0, cx=0.5):
        n = int(seconds * 1000.0 / DT_MS)
        for _ in range(n):
            phase = 2 * math.pi * (spm / 60.0) * (self.t / 1000.0)
            self.feed(body(cx=cx, rise=-amp * math.sin(phase)))

    def jump(self, height=0.12):
        for f in [0.3, 0.7, 1.0, 1.0, 0.7, 0.3, 0.0]:
            self.feed(body(rise=height * f))

    def keys(self):
        return [e.key for e in self.events if isinstance(e, KeyPress)]

    def steps_between(self, t0, t1):
        return [e for e in self.events if isinstance(e, Step) and t0 < e.t_ms < t1]


def test_jog_holds_running_and_produces_no_keys():
    h = Harness()
    h.jog(8.0)
    assert h.pipeline.gate.state == "RUNNING"
    assert h.pipeline.run.spm > 100
    assert h.keys() == []
    assert not any(isinstance(e, (Jump, Duck)) for e in h.events)


def test_jump_produces_the_jump_key():
    h = Harness()
    h.jog(4.0)
    h.jump()
    assert [e for e in h.events if isinstance(e, Jump)]
    assert "w" in h.keys()


def test_duck_produces_the_duck_key():
    h = Harness()
    h.jog(4.0)
    for _ in range(15):
        h.feed(body(rise=-0.22))       # shoulders to 0.52, past the 0.48 line
    assert [e for e in h.events if isinstance(e, Duck)]
    assert "s" in h.keys()


def test_the_lines_do_not_move_when_the_body_does():
    """Step closer (bigger shoulders) and settle lower: still no false duck."""
    h = Harness()
    h.jog(4.0)
    before = (h.pipeline.vertical.jump_y, h.pipeline.vertical.duck_y)
    for _ in range(120):               # 4 s at 0.06 lower, inside the lines
        h.feed(body(rise=-0.06))
    assert (h.pipeline.vertical.jump_y, h.pipeline.vertical.duck_y) == before
    assert not [e for e in h.events if isinstance(e, (Jump, Duck))]


def test_jump_pauses_the_running_detector_for_a_second():
    """The shoulder excursion of a jump and its landing must not count as
    steps, and the gate must stay RUNNING through the pause."""
    h = Harness()
    h.jog(5.0)
    assert len(h.steps_between(-1, h.t)) > 5

    h.jump()
    edge = next(e.t_ms for e in h.events if isinstance(e, Jump))
    h.jog(1.5)      # keep jogging straight through the pause and past it

    assert h.steps_between(edge, edge + 1000.0) == [], "no steps during the pause"
    assert h.steps_between(edge + 1000.0, h.t), "steps resume after the pause"
    assert h.pipeline.gate.state == "RUNNING"
    assert not [e for e in h.events if isinstance(e, GateTransition)]


def test_the_cadence_bar_does_not_sag_across_a_jump():
    """The bug: the 3 s cadence window kept sliding during the pause, so a
    jump dropped spm by a third. The window's clock stops with the detector."""
    h = Harness()
    h.jog(5.0)
    before = h.pipeline.run.spm
    assert before > 100

    h.jump()
    for _ in range(int(1000 / DT_MS)):   # a full second of the pause
        h.feed(body())
    during = h.pipeline.run.spm
    assert during >= before - 5, f"cadence sagged during the pause: {before} -> {during}"


def test_uncalibrated_falls_back_to_fixed_default_lines():
    """Without calibration the lines sit at known on-screen defaults -- never
    derived from the first pose, which could be a spurious detection that put
    the duck line off the bottom of the frame."""
    h = Harness(jump_y=None, duck_y=None)
    assert h.pipeline.uncalibrated_lines
    assert h.pipeline.vertical.lines_set
    assert h.pipeline.vertical.jump_y == 0.30
    assert h.pipeline.vertical.duck_y == 0.62
    for _ in range(60):
        h.feed(body(rise=-0.05))
    assert (h.pipeline.vertical.jump_y, h.pipeline.vertical.duck_y) == (0.30, 0.62)


# -- priming and the SPM hold -------------------------------------------------

def test_the_bar_starts_full_and_the_gate_starts_running():
    """The app must never open in a locked state: arming primes the cadence
    window so the bar is full, and only drains once you fail to keep up."""
    h = Harness()
    assert h.pipeline.active
    assert h.pipeline.spm(0.0) >= h.floor
    assert h.pipeline.gate.state == "RUNNING"
    assert h.pipeline.hud_state(0.0)["spm"] >= h.floor


def test_before_arming_the_gate_is_frozen_and_reads_full():
    h = Harness(active=False)
    assert not h.pipeline.active
    assert h.pipeline.spm(0.0) == h.floor
    for _ in range(400):                 # 13 s of standing perfectly still
        h.feed(body())
    assert h.pipeline.gate.state == "RUNNING", "an unarmed app must not lock"
    assert h.pipeline.hud_state(h.t)["spm"] == h.floor


def test_standing_still_after_arming_still_locks():
    """Priming must not make the gate toothless."""
    h = Harness()
    for _ in range(400):
        h.feed(body())
    assert h.pipeline.gate.state == "LOCKED"


def test_a_jump_holds_the_reported_cadence():
    h = Harness()
    h.jog(5.0)
    before = h.pipeline.spm(h.t)
    h.jump()
    edge = next(e.t_ms for e in h.events if isinstance(e, Jump))
    assert h.pipeline.run.holding(edge + 500.0)
    assert h.pipeline.spm(edge + 500.0) == before
    # And the hold expires rather than pinning the cadence forever.
    assert not h.pipeline.run.holding(edge + 1500.0)


def test_cadence_never_sags_through_a_jump_and_landing():
    """The reported number is what the bar draws and the gate reads."""
    h = Harness()
    h.jog(5.0)
    before = h.pipeline.spm(h.t)
    h.jump()
    lowest = before
    for _ in range(int(1200 / DT_MS)):
        h.feed(body())
        lowest = min(lowest, h.pipeline.spm(h.t))
    assert lowest >= before - 5, f"cadence sagged {before} -> {lowest}"
    assert h.pipeline.gate.state == "RUNNING"


def test_arming_sends_the_start_key():
    """T-pose arms and starts the game in one gesture."""
    h = Harness()
    h.pipeline.send_start(h.t)
    h.feed(body())
    assert "space" in h.keys()
