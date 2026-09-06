"""Wires features -> FSMs -> gate -> press queue -> dispatcher (PLAN §3).

One `tick(pose_frame, now_ms)` per new pose result. The clock is read by the
caller and passed in (PLAN §16.1), so the identical pipeline runs live, in the
windowed app, headless, and under --replay with recorded timestamps.

Enforcement order for an action event (PLAN §8's four safety mechanisms):
gate LOCKED drops it here with Suppressed(GATE_LOCKED); the queue rate-limits;
the dispatcher checks armed and focus and logs those refusals itself.

Two rules keep the gate from punishing you for things that are not standing
still:

- **Priming.** Arming seeds the cadence window as if you were already at the
  floor, so the bar starts full and the session can never open locked. Before
  arming the gate is inactive: frozen, and reported at the floor.
- **The SPM hold.** A jump or duck is a huge shoulder excursion that the bounce
  detector would read as a burst of steps, and the landing wobble as more. So
  the detector is paused for `running.vertical_pause_ms`, *and* the reported
  cadence is pinned to its last value for `running.spm_hold_ms`. Pausing alone
  was not enough: the bar sagged the moment it resumed, because the
  re-established jog had not yet put fresh steps in the window.
"""

from __future__ import annotations

from typing import Any

from .events import (
    Duck,
    Event,
    EventBus,
    FrameInvalid,
    Jump,
    LaneChange,
    Log,
    ModeSwitch,
    Severity,
    Suppressed,
    SuppressReason,
)
from .features import BOUNCE, FeatureExtractor, Features
from .keys import KeyDispatcher, PressQueue
from .lanes import LANE_NAMES, LaneFSM
from .pose import PoseFrame
from .running import GateFSM, RunDetector
from .vertical import VerticalFSM


def _lane_boundaries(config) -> tuple[float, float]:
    mode = config.get("lanes.mode")
    left = config.get("lanes.boundary_left")
    right = config.get("lanes.boundary_right")
    if mode == "calibrated" and left is not None and right is not None:
        return float(left), float(right)
    thirds = config.get("lanes.thirds")
    return float(thirds[0]), float(thirds[1])


def make_run_detector(config) -> RunDetector:
    """Amplitude and edge-counting depend on which signal is configured."""
    bounce = config.get("features.run_signal") == BOUNCE
    return RunDetector(
        step_amp=float(config.get("running.bounce_amp" if bounce else "running.step_amp")),
        cadence_window_s=float(config.get("running.cadence_window_s")),
        noise_floor_ratio=float(config.get("running.step_noise_floor_ratio")),
        count_both_edges=not bounce,
    )


class Pipeline:
    def __init__(self, bus: EventBus, config, dispatcher: KeyDispatcher) -> None:
        self._bus = bus
        self._dispatcher = dispatcher

        self.features = FeatureExtractor(config)
        bl, br = _lane_boundaries(config)
        self.lanes = LaneFSM(
            boundary_left=bl,
            boundary_right=br,
            hysteresis=float(config.get("lanes.hysteresis")),
        )
        # Two absolute lines. Calibration sets them; without it they sit at
        # fixed default screen positions. Deriving them from the first pose
        # instead put the duck line off-screen whenever the first detection
        # was spurious, which is exactly the fragility static lines exist to
        # avoid -- a default you can see and drag beats a guess you cannot.
        jump_y = config.get("vertical.jump_y")
        duck_y = config.get("vertical.duck_y")
        self.uncalibrated_lines = jump_y is None or duck_y is None
        self.vertical = VerticalFSM(
            jump_y=float(config.get("vertical.default_jump_y")) if jump_y is None else jump_y,
            duck_y=float(config.get("vertical.default_duck_y")) if duck_y is None else duck_y,
            exit_band=float(config.get("vertical.exit_band")),
            refractory_ms=float(config.get("vertical.refractory_ms")),
            max_jump_ms=float(config.get("vertical.max_jump_ms")),
            max_duck_ms=float(config.get("vertical.max_duck_ms")),
        )
        self.run = make_run_detector(config)
        self.gate = GateFSM(
            floor_spm=float(config.get("running.min_cadence_spm")),
            grace_ms=float(config.get("running.grace_ms")),
            warning_ms=float(config.get("running.warning_ms")),
            resume_ms=float(config.get("running.resume_ms")),
        )
        self.queue = PressQueue(gap_ms=float(config.get("keys.multi_press_gap_ms")))

        self._post_jump_ms = float(config.get("lanes.post_jump_suppress_ms"))
        self._post_lane_freeze_ms = float(config.get("running.post_lane_freeze_ms"))
        self._startup_grace_ms = float(config.get("running.startup_grace_ms"))
        self._vertical_pause_ms = float(config.get("running.vertical_pause_ms"))
        self._spm_hold_ms = float(config.get("running.spm_hold_ms"))
        self._prime_on_arm = bool(config.get("running.prime_on_arm"))
        self._gate_enforced = bool(config.get("running.gate_enforced"))
        # Inactive until armed: the gate is frozen and cadence reads full, so
        # the app never opens in a locked state.
        self.active = False

        self._armed_at_ms: float | None = None
        self._last_missing: tuple[str, ...] | None = None
        self._last_features: Features | None = None
        self._last_run_mode = "none"

    # -- external state ------------------------------------------------------

    def notify_armed(self, now_ms: float) -> None:
        """Starts the startup grace window (§7.4)."""
        self._armed_at_ms = now_ms

    def set_active(self, active: bool, now_ms: float) -> None:
        """Arm/disarm the gate. Becoming active primes the cadence window."""
        if active and not self.active:
            self._armed_at_ms = now_ms
            if self._prime_on_arm:
                self.run.prime(self.gate.floor, now_ms)
            self.gate.reset(now_ms)
        self.active = active

    def send_start(self, now_ms: float) -> None:
        """Queue the game's start key (T-pose arms and starts in one go)."""
        try:
            key = self._dispatcher.key_for("start")
        except KeyError:
            return
        self.queue.enqueue(key, "start", now_ms)

    def spm(self, now_ms: float) -> float:
        """Cadence as the gate and the HUD see it: held, or full while idle."""
        if not self.active:
            return self.gate.floor
        return self.run.spm_at(now_ms)

    def set_dispatcher(self, dispatcher: KeyDispatcher) -> None:
        """Mode switch: game uses the real sender, debug the dry-run one."""
        self._dispatcher = dispatcher

    @property
    def last_features(self) -> Features | None:
        return self._last_features

    def observe(self, pose: PoseFrame, now_ms: float) -> Features:
        """Update features without running the FSMs (menu/settings screens)."""
        feats = self.features.update(pose, now_ms)
        self._last_features = feats
        return feats

    def run_paused(self, now_ms: float) -> bool:
        return now_ms - self.vertical.last_edge_ms < self._vertical_pause_ms

    # -- the tick ------------------------------------------------------------

    def tick(self, pose: PoseFrame, now_ms: float) -> Features:
        feats = self.features.update(pose, now_ms)
        self._last_features = feats

        # Edge-triggered FrameInvalid (PLAN §5): one line per change of the
        # missing set, not one per bad frame.
        missing = feats.missing if not feats.ok else ()
        if missing != (self._last_missing or ()):
            if missing:
                self._bus.publish(
                    FrameInvalid(t_ms=now_ms, frame_t_ms=pose.t_ms, missing=list(missing))
                )
            self._last_missing = missing

        if not feats.ok:
            # No landmarks worth trusting: FSMs hold state, the queue still
            # drains anything already earned.
            self._drain(now_ms)
            return feats

        if feats.run_mode != self._last_run_mode and feats.run_mode != "none":
            if self._last_run_mode != "none":
                self._bus.publish(
                    ModeSwitch(
                        t_ms=now_ms,
                        frame_t_ms=pose.t_ms,
                        from_mode=self._last_run_mode,
                        to_mode=feats.run_mode,
                        knee_visibility=0.0,
                    )
                )
            self._last_run_mode = feats.run_mode

        events: list[Event] = []

        vertical_events = self.vertical.tick(feats.shoulder_y, now_ms)
        events += vertical_events
        if any(isinstance(e, (Jump, Duck)) for e in vertical_events):
            # The shoulders are about to do something that is not a jog: stop
            # the cadence clock, forget the half-cycle, and pin the reported
            # cadence so the bar cannot drain through the landing.
            self.run.reset_trigger()
            self.run.pause(now_ms)
            self.run.hold(now_ms, self._spm_hold_ms)

        lane_freeze = now_ms - self.vertical.last_edge_ms < self._post_jump_ms
        events += self.lanes.tick(feats.cx, now_ms, freeze=lane_freeze)

        if not self.run_paused(now_ms):
            if self.run.paused:
                self.run.resume(now_ms)
            events += self.run.tick(feats.d, now_ms)

        freeze, freeze_reason = self._gate_freeze(now_ms)
        events += self.gate.tick(
            self.spm(now_ms), now_ms, freeze=freeze, freeze_reason=freeze_reason
        )

        for event in events:
            self._bus.publish(event)
            self._maybe_enqueue(event, now_ms)

        self._drain(now_ms)
        return feats

    # -- internals -----------------------------------------------------------

    def _gate_freeze(self, now_ms: float) -> tuple[bool, str]:
        """Legitimate interruptions do not accrue toward the grace timer (§7.4)."""
        if not self.active:
            return True, "not_armed"
        if self.run.holding(now_ms):
            return True, "spm_hold"
        if self._armed_at_ms is not None and now_ms - self._armed_at_ms < self._startup_grace_ms:
            return True, "startup_grace"
        if not self.vertical.neutral:
            return True, f"vertical_{self.vertical.state.lower()}"
        if self.run_paused(now_ms):
            return True, "post_vertical_pause"
        if now_ms - self.lanes.last_change_ms < self._post_lane_freeze_ms:
            return True, "post_lane_change"
        return False, ""

    def _maybe_enqueue(self, event: Event, now_ms: float) -> None:
        if isinstance(event, LaneChange):
            action = "right" if event.delta > 0 else "left"
            what = "lane"
            count = abs(event.delta)
        elif isinstance(event, Jump):
            action, what, count = "jump", "jump", 1
        elif isinstance(event, Duck):
            action, what, count = "duck", "duck", 1
        else:
            return

        if self._gate_enforced and self.gate.locked:
            # Dropped at the gate: the key sender never sees it (§7.3).
            self._bus.publish(
                Suppressed(
                    t_ms=now_ms,
                    what=what,
                    reason=SuppressReason.GATE_LOCKED,
                    detail=f"spm={self.run.spm:.0f} floor={self.gate.floor:.0f}",
                )
            )
            return

        key = self._dispatcher.key_for(action)
        for _ in range(count):
            self.queue.enqueue(key, what, now_ms)

    def _drain(self, now_ms: float) -> None:
        for press in self.queue.drain(now_ms):
            self._dispatcher.dispatch(press, now_ms)

    # -- HUD state -----------------------------------------------------------

    def hud_state(self, now_ms: float) -> dict[str, Any]:
        feats = self._last_features
        return {
            "lane": LANE_NAMES[self.lanes.lane],
            "cx": round(feats.cx, 3) if feats else 0.5,
            "sy": round(feats.shoulder_y, 3) if feats else 0.0,
            "d": round(feats.d, 3) if feats else 0.0,
            "spm": int(round(self.spm(now_ms))),
            "floor": int(round(self.gate.floor)),
            "held": self.run.holding(now_ms),
            "active": self.active,
            "gate": self.gate.state,
            "mode": feats.run_mode if feats else "none",
            "vertical": self.vertical.state,
            "paused": self.run_paused(now_ms),
            "q": len(self.queue),
        }
