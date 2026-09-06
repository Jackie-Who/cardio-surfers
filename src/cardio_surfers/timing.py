"""Clocks and rolling statistics.

PLAN §16.1: every FSM tick takes `now_ms` as a parameter, and no state machine
ever reads a clock itself. The main loop reads the clock once per frame and
passes the value down; `--replay` swaps in a clock driven by the recorded
timestamps instead. That is what makes replay bit-for-bit deterministic and the
timing tests non-flaky.

This module is the one place a real clock is read, which is exactly why the AST
scan in tests/test_telemetry.py exempts it and nothing else.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Deque, Protocol


class Clock(Protocol):
    """Milliseconds since session start, monotonic and never decreasing."""

    def now_ms(self) -> float: ...


class MonotonicClock:
    """Wall-clock time for a live session, zeroed at construction."""

    def __init__(self) -> None:
        self._t0 = time.monotonic()

    def now_ms(self) -> float:
        return (time.monotonic() - self._t0) * 1000.0

    def reset(self) -> None:
        self._t0 = time.monotonic()


class ManualClock:
    """Test and replay clock: time only moves when you say so."""

    def __init__(self, start_ms: float = 0.0) -> None:
        self._now = float(start_ms)

    def now_ms(self) -> float:
        return self._now

    def advance(self, ms: float) -> float:
        if ms < 0:
            raise ValueError("time does not run backwards")
        self._now += float(ms)
        return self._now

    def set(self, ms: float) -> float:
        if ms < self._now:
            raise ValueError("time does not run backwards")
        self._now = float(ms)
        return self._now


class RollingWindow:
    """Fixed-size sample window with percentiles, for the latency readout.

    Nearest-rank percentiles on a sorted copy: at a few hundred samples this is
    far cheaper than the bookkeeping a streaming estimator would need, and it is
    exact, which matters when the number is a budget check (PLAN §4).
    """

    def __init__(self, size: int = 240) -> None:
        self._samples: Deque[float] = deque(maxlen=size)

    def add(self, value: float) -> None:
        self._samples.append(float(value))

    def __len__(self) -> int:
        return len(self._samples)

    def clear(self) -> None:
        self._samples.clear()

    def percentile(self, q: float) -> float:
        if not self._samples:
            return 0.0
        ordered = sorted(self._samples)
        idx = int(round((q / 100.0) * (len(ordered) - 1)))
        return ordered[max(0, min(idx, len(ordered) - 1))]

    @property
    def p50(self) -> float:
        return self.percentile(50.0)

    @property
    def p95(self) -> float:
        return self.percentile(95.0)

    @property
    def mean(self) -> float:
        if not self._samples:
            return 0.0
        return sum(self._samples) / len(self._samples)

    @property
    def last(self) -> float:
        return self._samples[-1] if self._samples else 0.0


class FpsCounter:
    """Frames per second over a sliding time window."""

    def __init__(self, window_ms: float = 2000.0) -> None:
        self.window_ms = float(window_ms)
        self._stamps: Deque[float] = deque()

    def tick(self, now_ms: float) -> float:
        self._stamps.append(now_ms)
        cutoff = now_ms - self.window_ms
        while self._stamps and self._stamps[0] < cutoff:
            self._stamps.popleft()
        return self.value(now_ms)

    def value(self, now_ms: float) -> float:
        if len(self._stamps) < 2:
            return 0.0
        span_ms = self._stamps[-1] - self._stamps[0]
        if span_ms <= 0:
            return 0.0
        return (len(self._stamps) - 1) * 1000.0 / span_ms
