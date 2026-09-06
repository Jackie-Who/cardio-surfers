"""Event taxonomy and the one event bus (PLAN §10.1, §3).

Everything that consumes app state subscribes here: the key sender, the HUD, the
console logger, the JSONL writer, the stats counter. One stream, so the debug
output can never drift from actual behaviour -- if the console says a key was
pressed, it is because the same event object went to the sender.

Events are frozen: a published event is a fact, and subscribers do not get to
edit facts. Nothing in this package prints; text comes from telemetry.py only.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, fields as dataclass_fields
from enum import Enum, IntEnum
from typing import Any, Callable, ClassVar, Mapping, Sequence


class Verbosity(IntEnum):
    """Console tiers (PLAN §10.2). The JSONL log ignores this entirely."""

    QUIET = 0    # warnings and errors only
    EVENTS = 1   # -v    discrete events
    TICKER = 2   # -vv   + the 5 Hz state ticker
    TRACE = 3    # -vvv  + latency, steps, freeze windows, suppressions


class SuppressReason(str, Enum):
    """Closed enum: every decision *not* to act names one of these (PLAN §10.3).

    Silence is not an acceptable failure mode -- a phantom lane change and a
    missing one look identical from the outside, and only the reason tells them
    apart.
    """

    GATE_LOCKED = "GATE_LOCKED"        # detail: current spm, floor
    NOT_ARMED = "NOT_ARMED"
    WINDOW_FOCUS = "WINDOW_FOCUS"      # detail: the actual foreground title
    REFRACTORY = "REFRACTORY"          # detail: ms remaining
    HYSTERESIS = "HYSTERESIS"          # detail: cx and the band it sat inside
    POST_JUMP_LANE = "POST_JUMP_LANE"  # detail: ms left of the 150 ms window
    LOW_VISIBILITY = "LOW_VISIBILITY"  # detail: which landmarks failed
    STEP_AMPLITUDE = "STEP_AMPLITUDE"  # detail: measured d peak vs step_amp
    STARTUP_GRACE = "STARTUP_GRACE"    # detail: ms remaining


class Severity(str, Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


# Dataclass fields cannot be named `from`, so a few are renamed on the wire to
# match the field names given in PLAN §10.1.
_WIRE_NAMES = {
    "from_lane": "from",
    "to_lane": "to",
    "from_mode": "from",
    "to_mode": "to",
}


@dataclass(frozen=True, kw_only=True)
class Event:
    """Base event.

    `t_ms` is monotonic and relative to session start; `frame_t_ms` is the pose
    frame it derives from, so every telemetry line correlates exactly with a
    recording (PLAN §10.1).
    """

    t_ms: float
    frame_t_ms: float | None = None

    NAME: ClassVar[str] = "EVENT"
    LEVEL: ClassVar[Verbosity] = Verbosity.EVENTS

    def payload(self) -> dict[str, Any]:
        """Event-specific fields, in the order the console should print them."""
        out: dict[str, Any] = {}
        for f in dataclass_fields(self):
            if f.name in ("t_ms", "frame_t_ms"):
                continue
            out[_WIRE_NAMES.get(f.name, f.name)] = getattr(self, f.name)
        return out


# ---------------------------------------------------------------------------
# Session lifecycle and diagnostics
# ---------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class Started(Event):
    version: str
    phase: str
    config_path: str
    NAME = "STARTED"
    LEVEL = Verbosity.QUIET


@dataclass(frozen=True, kw_only=True)
class Stopped(Event):
    reason: str
    duration_ms: float
    NAME = "STOPPED"
    LEVEL = Verbosity.QUIET


@dataclass(frozen=True, kw_only=True)
class Log(Event):
    """The only way a module reports something in words (PLAN §16.2)."""

    severity: Severity
    message: str
    detail: str | None = None
    NAME = "LOG"
    LEVEL = Verbosity.QUIET


@dataclass(frozen=True, kw_only=True)
class CameraOpened(Event):
    index: int
    backend: str
    width: int
    height: int
    fps_requested: float
    fps_actual: float
    NAME = "CAMERA"
    LEVEL = Verbosity.QUIET


@dataclass(frozen=True, kw_only=True)
class ModelLoaded(Event):
    path: str
    load_ms: float
    NAME = "MODEL"
    LEVEL = Verbosity.QUIET


@dataclass(frozen=True, kw_only=True)
class FrameInvalid(Event):
    """Edge-triggered: published only when the missing set changes (PLAN §5).

    Level-triggering this would spam one line per frame the moment you step out
    of shot, which is exactly the sort of noise that makes a log unreadable.
    """

    missing: Sequence[str]
    NAME = "FRAME_INVALID"
    LEVEL = Verbosity.EVENTS


@dataclass(frozen=True, kw_only=True)
class Tick(Event):
    """5 Hz state snapshot for the in-place console ticker (PLAN §10.2).

    A free-form ordered mapping, so later phases add keys without touching the
    sink; the sink renders `k=v` in insertion order and nothing more.
    """

    state: Mapping[str, Any]
    NAME = "TICK"
    LEVEL = Verbosity.TICKER


@dataclass(frozen=True, kw_only=True)
class Latency(Event):
    """Per-stage attribution against the PLAN §4 budget."""

    stages: Mapping[str, float]
    total_ms: float
    NAME = "LATENCY"
    LEVEL = Verbosity.TRACE


# ---------------------------------------------------------------------------
# Gameplay events (produced from P2 onward)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class LaneChange(Event):
    from_lane: int
    to_lane: int
    delta: int
    cx: float
    NAME = "LANE_CHANGE"


@dataclass(frozen=True, kw_only=True)
class Jump(Event):
    dy_peak: float
    NAME = "JUMP"


@dataclass(frozen=True, kw_only=True)
class Duck(Event):
    dy_min: float
    NAME = "DUCK"


@dataclass(frozen=True, kw_only=True)
class Step(Event):
    foot: str          # "L" or "R"
    spm_now: float
    NAME = "STEP"
    LEVEL = Verbosity.TRACE


@dataclass(frozen=True, kw_only=True)
class GateTransition(Event):
    from_state: str
    to_state: str
    spm: float
    floor: float
    NAME = "GATE"


@dataclass(frozen=True, kw_only=True)
class KeyPress(Event):
    key: str
    source: str        # lane / jump / duck
    queued_ms: float
    NAME = "KEY_PRESS"


@dataclass(frozen=True, kw_only=True)
class ModeSwitch(Event):
    from_mode: str     # knee / bob
    to_mode: str
    knee_visibility: float
    NAME = "MODE_SWITCH"


@dataclass(frozen=True, kw_only=True)
class Armed(Event):
    NAME = "ARMED"


@dataclass(frozen=True, kw_only=True)
class Disarmed(Event):
    NAME = "DISARMED"


@dataclass(frozen=True, kw_only=True)
class CalibStep(Event):
    step: int
    values: Mapping[str, Any]
    NAME = "CALIB"
    LEVEL = Verbosity.QUIET


@dataclass(frozen=True, kw_only=True)
class GraceFreeze(Event):
    """Edge-triggered: the gate's grace clock froze or thawed (PLAN §7.4).

    Emitted so `-vvv` shows exactly why the gate did or did not advance during
    a jump-land-dodge-duck sequence.
    """

    active: bool
    reason: str
    NAME = "GRACE_FREEZE"
    LEVEL = Verbosity.TRACE


@dataclass(frozen=True, kw_only=True)
class Suppressed(Event):
    """Every refusal to act, with a reason from the closed enum (PLAN §10.3)."""

    what: str
    reason: SuppressReason
    detail: str | None = None
    NAME = "SUPPRESSED"
    LEVEL = Verbosity.TRACE


Subscriber = Callable[[Event], None]


class EventBus:
    """Fan-out to every subscriber, synchronously and without blocking.

    Subscribers must be cheap: TelemetrySink does a `put_nowait` onto a bounded
    queue, the HUD appends to a ring buffer. A subscriber that raises is counted
    rather than allowed to take the pipeline down -- and since it cannot be
    reported by printing, the count surfaces on the ticker instead.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: tuple[Subscriber, ...] = ()
        self.published = 0
        self.subscriber_errors = 0
        self.last_error: str | None = None

    def subscribe(self, fn: Subscriber) -> None:
        with self._lock:
            self._subscribers = self._subscribers + (fn,)

    def unsubscribe(self, fn: Subscriber) -> None:
        with self._lock:
            self._subscribers = tuple(s for s in self._subscribers if s is not fn)

    def publish(self, event: Event) -> None:
        self.published += 1
        # Immutable snapshot: iteration needs no lock and allocates nothing.
        for sub in self._subscribers:
            try:
                sub(event)
            except Exception as exc:  # noqa: BLE001 - never kill the pipeline
                self.subscriber_errors += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
