"""RunDetector + GateFSM (PLAN §7, shoulder-bounce revision).

The detector is a Schmitt trigger with mandatory alternation over any
oscillating signal. What differs per signal is how many steps a cycle is worth:

- **bounce** (default): shoulders dip once per foot strike, so one full cycle
  is one step. Only the dip->rise edge counts (`count_both_edges=False`).
- **arm / knee** differentials: antisymmetric, each half-cycle is a step, so
  both edges count.

Either way both thresholds must be crossed in turn: a one-sided twitch scores
zero and swings under the amplitude gate don't register.

Cadence is **steps per minute**. The gate: RUNNING -> WARNING after `grace_ms`
below the floor, -> LOCKED after `warning_ms` more, back after `resume_ms` at
or above the floor. The grace clock **freezes** during legitimate
interruptions (jumps, ducks, lane changes, startup) -- §7.4.

Pure FSMs: no I/O, no clock reads; every tick takes `now_ms`.
"""

from __future__ import annotations

from collections import deque
from typing import Deque

from .events import Event, GateTransition, GraceFreeze, Step, Suppressed, SuppressReason

IDLE, UP, DOWN = "IDLE", "UP", "DOWN"
RUNNING, WARNING, LOCKED = "RUNNING", "WARNING", "LOCKED"


class RunDetector:
    """Schmitt trigger with alternation -> timestamped steps -> cadence."""

    def __init__(
        self,
        step_amp: float,
        cadence_window_s: float,
        noise_floor_ratio: float,
        count_both_edges: bool = True,
    ) -> None:
        self.step_amp = float(step_amp)
        self.window_ms = float(cadence_window_s) * 1000.0
        # Swings above this fraction of step_amp but below step_amp are real
        # attempts worth logging; anything smaller is noise not worth a line.
        self.noise_floor = float(noise_floor_ratio) * float(step_amp)
        self.count_both_edges = count_both_edges

        self.trigger_state = IDLE
        self._steps: Deque[float] = deque()
        self._swing_peak = 0.0     # max |d| since the last zero crossing
        self._last_sign = 0
        self._foot = "L"
        self._paused_since: float | None = None
        # SPM hold: after a jump or duck the reported cadence is pinned to its
        # last value for a moment. Pausing the detector alone was not enough --
        # the bar still sagged as soon as it resumed and the re-established jog
        # had not yet put new steps in the window.
        self._hold_until_ms = -1e12
        self._held_spm = 0.0
        self.spm = 0.0

    @property
    def paused(self) -> bool:
        return self._paused_since is not None

    def spm_at(self, now_ms: float) -> float:
        """Cadence as the gate and the HUD should see it, holds included."""
        if now_ms < self._hold_until_ms:
            return self._held_spm
        return self.spm

    def holding(self, now_ms: float) -> bool:
        return now_ms < self._hold_until_ms

    def hold(self, now_ms: float, duration_ms: float) -> None:
        """Pin the reported cadence at its current value for `duration_ms`."""
        self._held_spm = self.spm
        self._hold_until_ms = now_ms + duration_ms

    def prime(self, spm: float, now_ms: float) -> None:
        """Seed the cadence window as if already running at `spm`.

        The gate must never open in a locked state: at arming the window is
        empty, which reads as zero cadence and starts the drain immediately.
        Priming fills it with evenly spaced synthetic steps so the bar starts
        full and only falls if you actually stop.
        """
        self._steps.clear()
        count = int(round(spm * self.window_ms / 60000.0))
        if count > 0:
            gap = self.window_ms / count
            for i in range(count):
                self._steps.append(now_ms - self.window_ms + gap * (i + 1))
        self._hold_until_ms = -1e12
        self._expire(now_ms)

    def pause(self, now_ms: float) -> None:
        """Stop the cadence clock (a jump or duck is in progress)."""
        if self._paused_since is None:
            self._paused_since = now_ms

    def resume(self, now_ms: float) -> None:
        """Restart the cadence clock as if the pause never happened: every
        remembered step slides forward by the paused duration, so the window
        still holds exactly the steps it held before the gesture and the
        cadence bar does not sag."""
        if self._paused_since is None:
            return
        shift = max(0.0, now_ms - self._paused_since)
        self._steps = deque(t + shift for t in self._steps)
        self._paused_since = None

    def reset_trigger(self) -> None:
        """Forget the current half-cycle (after a jump/duck, the signal is
        meaningless until the body settles)."""
        self.trigger_state = IDLE
        self._swing_peak = 0.0
        self._last_sign = 0

    def tick(self, d: float, now_ms: float) -> list[Event]:
        events: list[Event] = []
        amp = self.step_amp

        # Track sub-threshold swings so STEP_AMPLITUDE refusals are visible:
        # a half-hearted swing must produce a log line, not silence (§10.3).
        sign = 1 if d > 0 else (-1 if d < 0 else 0)
        if sign != 0 and sign != self._last_sign and self._last_sign != 0:
            if self.noise_floor < self._swing_peak < amp:
                events.append(
                    Suppressed(
                        t_ms=now_ms,
                        what="step",
                        reason=SuppressReason.STEP_AMPLITUDE,
                        detail=f"peak={self._swing_peak:.3f} amp={amp:.3f}",
                    )
                )
            self._swing_peak = 0.0
        if sign != 0:
            self._last_sign = sign
        self._swing_peak = max(self._swing_peak, abs(d))

        # The Schmitt trigger. Only UP <-> DOWN transitions count; the first
        # entry from IDLE is arming, not a step, so a repeated one-sided
        # twitch farms exactly zero (PLAN §7.1).
        if d > amp and self.trigger_state != UP:
            if self.trigger_state == DOWN:
                events.append(self._step(now_ms))      # dip -> rise: foot strike
            self.trigger_state = UP
        elif d < -amp and self.trigger_state != DOWN:
            if self.trigger_state == UP and self.count_both_edges:
                events.append(self._step(now_ms))
            self.trigger_state = DOWN

        self._expire(now_ms)
        return events

    def _step(self, now_ms: float) -> Step:
        self._steps.append(now_ms)
        self._expire(now_ms)
        self._foot = "R" if self._foot == "L" else "L"
        return Step(t_ms=now_ms, foot=self._foot, spm_now=self.spm)

    def _expire(self, now_ms: float) -> None:
        cutoff = now_ms - self.window_ms
        while self._steps and self._steps[0] < cutoff:
            self._steps.popleft()
        self.spm = len(self._steps) * 60000.0 / self.window_ms


class GateFSM:
    """RUNNING -> WARNING -> LOCKED, with a freezable grace clock (§7.3-7.4)."""

    def __init__(
        self,
        floor_spm: float,
        grace_ms: float,
        warning_ms: float,
        resume_ms: float,
    ) -> None:
        self.floor = float(floor_spm)
        self.grace_ms = float(grace_ms)
        self.warning_ms = float(warning_ms)
        self.resume_ms = float(resume_ms)

        self.state = RUNNING
        self._below_ms = 0.0        # unfrozen time spent under the floor
        self._warning_since = 0.0
        self._above_since: float | None = None
        self._last_tick_ms: float | None = None
        self._frozen = False
        self._freeze_reason = ""

    @property
    def locked(self) -> bool:
        return self.state == LOCKED

    def reset(self, now_ms: float) -> None:
        """Back to RUNNING with a clean drain clock (called on arming)."""
        self.state = RUNNING
        self._below_ms = 0.0
        self._above_since = None
        self._last_tick_ms = now_ms

    def warning_remaining_ms(self, now_ms: float) -> float:
        if self.state != WARNING:
            return 0.0
        return max(0.0, self.warning_ms - (now_ms - self._warning_since))

    def tick(
        self, spm: float, now_ms: float, freeze: bool = False, freeze_reason: str = ""
    ) -> list[Event]:
        events: list[Event] = []
        dt = 0.0
        if self._last_tick_ms is not None:
            dt = max(0.0, now_ms - self._last_tick_ms)
        self._last_tick_ms = now_ms

        if freeze != self._frozen:
            # Edge-triggered visibility for -vvv: exactly why the gate did or
            # did not advance (§7.4).
            events.append(
                GraceFreeze(
                    t_ms=now_ms,
                    active=freeze,
                    reason=freeze_reason if freeze else self._freeze_reason,
                )
            )
            self._frozen = freeze
            self._freeze_reason = freeze_reason

        above = spm >= self.floor

        if self.state == RUNNING:
            if above:
                self._below_ms = 0.0
            elif not freeze:
                # Only the time-below accumulator freezes; steps keep counting
                # in the detector regardless (§7.4).
                self._below_ms += dt
                if self._below_ms >= self.grace_ms:
                    self._transition(WARNING, spm, now_ms, events)
                    self._warning_since = now_ms

        elif self.state == WARNING:
            if above:
                self._transition(RUNNING, spm, now_ms, events)
                self._below_ms = 0.0
            elif not freeze and now_ms - self._warning_since >= self.warning_ms:
                self._transition(LOCKED, spm, now_ms, events)
                self._above_since = None

        elif self.state == LOCKED:
            if above:
                if self._above_since is None:
                    self._above_since = now_ms
                elif now_ms - self._above_since >= self.resume_ms:
                    self._transition(RUNNING, spm, now_ms, events)
                    self._below_ms = 0.0
            else:
                self._above_since = None

        return events

    def _transition(self, to_state: str, spm: float, now_ms: float, events: list[Event]) -> None:
        events.append(
            GateTransition(
                t_ms=now_ms,
                from_state=self.state,
                to_state=to_state,
                spm=spm,
                floor=self.floor,
            )
        )
        self.state = to_state
