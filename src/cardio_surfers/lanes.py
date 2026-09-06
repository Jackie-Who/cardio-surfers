"""LaneFSM: three discrete zones with hysteresis and doubles (PLAN §6).

Pure: no I/O, no globals, no clock reads. `tick` takes `now_ms` and returns the
events; the caller publishes them and turns LaneChange into presses.

The rules that matter:

- **No dwell time.** A fast left-right sweep is the core mechanic; noise
  suppression is hysteresis only (PLAN §6.2).
- **Emit the difference.** 0->2 in one frame is delta 2 (two presses); 0->1->2
  across frames is two deltas of 1. Both yield two presses.
- **Hysteresis refusals are logged.** A phantom lane change and a missed one
  look identical from outside; only Suppressed(HYSTERESIS) tells them apart.
- **Freeze after a jump edge.** cx is noisy on takeoff; the caller passes
  `freeze=True` for the configured window and changes are suppressed and
  logged, not acted on (PLAN §6.3).
"""

from __future__ import annotations

from .events import Event, LaneChange, Suppressed, SuppressReason

LEFT, CENTRE, RIGHT = 0, 1, 2
LANE_NAMES = {LEFT: "LEFT", CENTRE: "CENTRE", RIGHT: "RIGHT"}


class LaneFSM:
    def __init__(
        self,
        boundary_left: float,
        boundary_right: float,
        hysteresis: float,
    ) -> None:
        self.boundary_left = float(boundary_left)
        self.boundary_right = float(boundary_right)
        self.hysteresis = float(hysteresis)
        self.lane = CENTRE
        self.last_change_ms = -1e12
        self._raw_lane = CENTRE

    # -- helpers -------------------------------------------------------------

    def _raw(self, cx: float) -> int:
        """Zone with no hysteresis -- what a naive detector would say."""
        if cx < self.boundary_left:
            return LEFT
        if cx > self.boundary_right:
            return RIGHT
        return CENTRE

    def _with_hysteresis(self, cx: float) -> int:
        """Zone the FSM actually moves to, given where it currently is."""
        h = self.hysteresis
        lane = self.lane
        if lane == LEFT:
            # Leaving LEFT needs cx clearly past the boundary.
            if cx > self.boundary_left + h:
                lane = CENTRE
            else:
                return LEFT
        if lane == RIGHT:
            if cx < self.boundary_right - h:
                lane = CENTRE
            else:
                return RIGHT
        # From CENTRE (possibly just arrived), entering an outer lane needs the
        # same clearance. A single fast sweep can pass through both tests in
        # one tick, which is exactly how 0->2 happens in one frame.
        if cx < self.boundary_left - h:
            return LEFT
        if cx > self.boundary_right + h:
            return RIGHT
        return lane

    # -- the tick ------------------------------------------------------------

    def tick(self, cx: float, now_ms: float, freeze: bool = False) -> list[Event]:
        events: list[Event] = []

        raw = self._raw(cx)
        target = self._with_hysteresis(cx)

        if freeze:
            if target != self.lane:
                events.append(
                    Suppressed(
                        t_ms=now_ms,
                        what="lane",
                        reason=SuppressReason.POST_JUMP_LANE,
                        detail=f"cx={cx:.3f} held in {LANE_NAMES[self.lane]}",
                    )
                )
            self._raw_lane = raw
            return events

        if target != self.lane:
            events.append(
                LaneChange(
                    t_ms=now_ms,
                    from_lane=self.lane,
                    to_lane=target,
                    delta=target - self.lane,
                    cx=cx,
                )
            )
            self.lane = target
            self.last_change_ms = now_ms
        elif raw != self._raw_lane and raw != self.lane:
            # The naive detector crossed a boundary but hysteresis held: this is
            # the §6.3 sway case, and it must be visible (PLAN §10.3).
            band_lo = (
                self.boundary_left - self.hysteresis
                if raw == LEFT or self.lane == LEFT
                else self.boundary_right - self.hysteresis
            )
            band_hi = band_lo + 2.0 * self.hysteresis
            events.append(
                Suppressed(
                    t_ms=now_ms,
                    what="lane",
                    reason=SuppressReason.HYSTERESIS,
                    detail=f"cx={cx:.3f} inside [{band_lo:.3f},{band_hi:.3f}]",
                )
            )

        self._raw_lane = raw
        return events
