"""Session counters + end-of-session summary (PLAN §13 P7).

One more subscriber on the same bus (PLAN §3): it counts what actually
happened, so it can never disagree with the log. Time-in-state accounting uses
event timestamps, not a clock of its own.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .events import (
    Duck,
    Event,
    EventBus,
    GateTransition,
    Jump,
    KeyPress,
    LaneChange,
    Step,
    Suppressed,
)


class SessionStats:
    def __init__(self, bus: EventBus) -> None:
        self.steps = 0
        self.jumps = 0
        self.ducks = 0
        self.lane_singles = 0
        self.lane_doubles = 0
        self.key_presses = 0
        self.suppressed = Counter()
        self.peak_spm = 0.0
        self._spm_sum = 0.0
        self._spm_samples = 0

        self._gate_state = "RUNNING"
        self._gate_since_ms = 0.0
        self.gate_time_ms = Counter()          # state -> ms
        self._run_started_ms = 0.0
        self.longest_run_ms = 0.0

        self._first_t_ms: float | None = None
        self._last_t_ms = 0.0
        bus.subscribe(self._on_event)

    def _on_event(self, event: Event) -> None:
        if self._first_t_ms is None:
            self._first_t_ms = event.t_ms
            self._gate_since_ms = event.t_ms
            self._run_started_ms = event.t_ms
        self._last_t_ms = max(self._last_t_ms, event.t_ms)

        if isinstance(event, Step):
            self.steps += 1
            self.peak_spm = max(self.peak_spm, event.spm_now)
            self._spm_sum += event.spm_now
            self._spm_samples += 1
        elif isinstance(event, Jump):
            self.jumps += 1
        elif isinstance(event, Duck):
            self.ducks += 1
        elif isinstance(event, LaneChange):
            if abs(event.delta) >= 2:
                self.lane_doubles += 1
            else:
                self.lane_singles += 1
        elif isinstance(event, KeyPress):
            self.key_presses += 1
        elif isinstance(event, Suppressed):
            self.suppressed[event.reason.value] += 1
        elif isinstance(event, GateTransition):
            span = max(0.0, event.t_ms - self._gate_since_ms)
            self.gate_time_ms[self._gate_state] += span
            if self._gate_state == "RUNNING":
                self.longest_run_ms = max(
                    self.longest_run_ms, event.t_ms - self._run_started_ms
                )
            if event.to_state == "RUNNING":
                self._run_started_ms = event.t_ms
            self._gate_state = event.to_state
            self._gate_since_ms = event.t_ms

    def summary(self) -> dict[str, Any]:
        # Close the open gate interval so a session that never locked still
        # reports its running time.
        gate = Counter(self.gate_time_ms)
        gate[self._gate_state] += max(0.0, self._last_t_ms - self._gate_since_ms)
        longest = self.longest_run_ms
        if self._gate_state == "RUNNING":
            longest = max(longest, self._last_t_ms - self._run_started_ms)
        duration = self._last_t_ms - (self._first_t_ms or 0.0)
        return {
            "duration_s": round(duration / 1000.0, 1),
            "steps": self.steps,
            "jumps": self.jumps,
            "ducks": self.ducks,
            "lane_singles": self.lane_singles,
            "lane_doubles": self.lane_doubles,
            "key_presses": self.key_presses,
            "mean_spm": round(self._spm_sum / self._spm_samples, 1)
            if self._spm_samples
            else 0.0,
            "peak_spm": round(self.peak_spm, 1),
            "gate_running_s": round(gate["RUNNING"] / 1000.0, 1),
            "gate_warned_s": round(gate["WARNING"] / 1000.0, 1),
            "gate_locked_s": round(gate["LOCKED"] / 1000.0, 1),
            "longest_unbroken_run_s": round(longest / 1000.0, 1),
            "suppressed": dict(self.suppressed),
        }
