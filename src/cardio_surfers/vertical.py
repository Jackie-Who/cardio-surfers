"""VerticalFSM: NEUTRAL / JUMP / DUCK against two absolute lines.

Two static lines on the screen, fractions of frame height: the JUMP line
(above the shoulders at rest) and the DUCK line (below). The shoulder line
crossing the JUMP line IS the jump; crossing the DUCK line IS the duck. No
neutral line, no offsets, nothing derived from your position: the lines are
where calibration or Settings put them, and that is the whole contract.
Step closer, lean, slouch -- the lines do not move.

Exit is back past the line by `exit_band`, so the boundary cannot chatter. A
refractory window blocks double-fires and every refused re-entry is logged as
Suppressed(REFRACTORY). Hold timeouts release a gesture that outlives what a
gesture can be (nobody is airborne for a second); the FSM then waits for the
shoulder line to come back between the lines once before it may fire again.

Pure: no I/O, no clock reads; `tick(shoulder_y, now_ms)`. Image y grows
downward: "above" means a smaller y.
"""

from __future__ import annotations

from .events import Duck, Event, Jump, Log, Severity, Suppressed, SuppressReason

NEUTRAL, JUMP, DUCK = "NEUTRAL", "JUMP", "DUCK"


class VerticalFSM:
    def __init__(
        self,
        jump_y: float | None,
        duck_y: float | None,
        exit_band: float,
        refractory_ms: float,
        max_jump_ms: float,
        max_duck_ms: float,
    ) -> None:
        self.jump_y = None if jump_y is None else float(jump_y)
        self.duck_y = None if duck_y is None else float(duck_y)
        self.exit_band = float(exit_band)
        self.refractory_ms = float(refractory_ms)
        self.max_jump_ms = float(max_jump_ms)
        self.max_duck_ms = float(max_duck_ms)

        self.state = NEUTRAL
        self.last_edge_ms = -1e12   # last JUMP/DUCK entry; lanes and the run detector key off it
        self._refractory_until = -1e12
        self._was_outside = False   # for edge-triggered REFRACTORY suppression
        self._needs_neutral = False # after a timeout: come back between the lines first

    @property
    def neutral(self) -> bool:
        return self.state == NEUTRAL

    @property
    def lines_set(self) -> bool:
        return self.jump_y is not None and self.duck_y is not None

    def set_lines(self, jump_y: float, duck_y: float) -> None:
        self.jump_y = float(jump_y)
        self.duck_y = float(duck_y)

    def tick(self, shoulder_y: float, now_ms: float) -> list[Event]:
        events: list[Event] = []
        if self.jump_y is None or self.duck_y is None:
            return events
        jump_y, duck_y, band = self.jump_y, self.duck_y, self.exit_band

        if self.state == JUMP:
            held = now_ms - self.last_edge_ms
            if shoulder_y > jump_y + band:
                self._exit(now_ms)
            elif held > self.max_jump_ms:
                self._timeout(now_ms, held, events)
            return events

        if self.state == DUCK:
            held = now_ms - self.last_edge_ms
            if shoulder_y < duck_y - band:
                self._exit(now_ms)
            elif held > self.max_duck_ms:
                self._timeout(now_ms, held, events)
            return events

        # NEUTRAL
        wants_jump = shoulder_y < jump_y
        wants_duck = shoulder_y > duck_y
        outside = wants_jump or wants_duck

        if self._needs_neutral:
            if outside:
                return events
            self._needs_neutral = False

        if outside and now_ms < self._refractory_until:
            if not self._was_outside:
                # Edge-triggered: one line per refused bounce, not per frame.
                events.append(
                    Suppressed(
                        t_ms=now_ms,
                        what="jump" if wants_jump else "duck",
                        reason=SuppressReason.REFRACTORY,
                        detail=f"remaining={self._refractory_until - now_ms:.0f}ms",
                    )
                )
            self._was_outside = outside
            return events

        if wants_jump:
            self.state = JUMP
            self.last_edge_ms = now_ms
            events.append(Jump(t_ms=now_ms, dy_peak=jump_y - shoulder_y))
        elif wants_duck:
            self.state = DUCK
            self.last_edge_ms = now_ms
            events.append(Duck(t_ms=now_ms, dy_min=duck_y - shoulder_y))

        self._was_outside = outside
        return events

    def _exit(self, now_ms: float) -> None:
        self.state = NEUTRAL
        self._refractory_until = now_ms + self.refractory_ms
        # A fresh excursion after this exit is a new edge; without the reset a
        # double bounce would be refused *silently*.
        self._was_outside = False

    def _timeout(self, now_ms: float, held_ms: float, events: list[Event]) -> None:
        events.append(
            Log(
                t_ms=now_ms,
                severity=Severity.INFO,
                message=f"{self.state.lower()} held {held_ms / 1000.0:.1f}s -- released; "
                "come back between the lines to re-arm",
            )
        )
        self._needs_neutral = True
        self._exit(now_ms)
