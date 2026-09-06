"""Guided calibration session (PLAN 9, absolute-line revision).

Runs inside the app window as a mode: the app feeds each frame's features into
`tick`, renders the returned status, and writes the derived config when the
session finishes. Pure in the project sense: no I/O, no clock reads -- the app
passes `now_ms` and does the drawing and saving.

Everything vertical is a plain screen position (fraction of frame height):

0 stand centred  -> x_centre (torso line), standing shoulder line
1 step LEFT      -> x_left
2 step RIGHT     -> x_right
3 jump           -> the highest point the shoulder line reached
4 duck and hold  -> where the shoulder line sits while ducked
5 jog in place   -> jogging shoulder line (the posture you play in), bounce
                    amplitude, cadence, torso-line sway

The JUMP line is placed a fraction of your measured rise above the jogging
shoulder line, the DUCK line a fraction of your measured drop below it. From
then on they are simply two numbers on the screen, and Settings can drag them.

Medians everywhere a hold is sampled, so one bad frame cannot poison a step.
Validation gates re-prompt rather than saving garbage. Compact spaces are
allowed: if the torso line sways more than the lanes can absorb, the
hysteresis band is clamped to a fraction of the lane half-width and logged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .config import CALIBRATION_SCHEMA
from .events import CalibStep, Event, Log, Severity
from .features import BOUNCE, Features

COUNTDOWN, CAPTURE, DONE = "COUNTDOWN", "CAPTURE", "DONE"


@dataclass
class StepSpec:
    index: int
    prompt: str
    detail: str
    duration_s: float


@dataclass
class CalibStatus:
    step: int
    total_steps: int
    phase: str                 # COUNTDOWN | CAPTURE | DONE
    prompt: str
    detail: str
    seconds_left: float
    progress: float            # 0..1 within the current phase
    message: str | None = None  # validation feedback, shown in amber
    finished: bool = False


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round(q / 100.0 * (len(ordered) - 1)))
    return ordered[max(0, min(idx, len(ordered) - 1))]


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def count_steps(samples: list[tuple[float, float]], amp: float, both_edges: bool) -> int:
    """Alternating threshold crossings, the same rule the live detector uses."""
    state = 0
    steps = 0
    for _, d in samples:
        if d > amp and state != 1:
            if state == -1:
                steps += 1
            state = 1
        elif d < -amp and state != -1:
            if state == 1 and both_edges:
                steps += 1
            state = -1
    return steps


@dataclass
class _Capture:
    cx: list[float] = field(default_factory=list)
    shoulder_y: list[float] = field(default_factory=list)
    shoulder_width: list[float] = field(default_factory=list)
    d: list[tuple[float, float]] = field(default_factory=list)  # (t_ms, d)


class CalibrationSession:
    def __init__(self, config) -> None:
        self._countdown_s = float(config.get("calibration.countdown_s"))
        hold_s = float(config.get("calibration.frames_per_step")) / max(
            1.0, float(config.get("camera.fps"))
        )
        jump_s = float(config.get("calibration.jump_capture_s"))
        jog_s = float(config.get("calibration.jog_seconds"))

        self._derive = config.section("calibration.derive")
        self._validate = config.section("calibration.validate")
        self._bounce_mode = config.get("features.run_signal") == BOUNCE

        self.steps = [
            StepSpec(0, "Stand centred, relaxed",
                     "face the camera, shoulders level", hold_s),
            StepSpec(1, "Step to your LEFT and hold", "one comfortable side-step", hold_s),
            StepSpec(2, "Step to your RIGHT and hold", "one comfortable side-step", hold_s),
            StepSpec(3, "Jump once, straight up", "any time in the window", jump_s),
            StepSpec(4, "Duck down and hold",
                     "drop your shoulders as low as you would in play", hold_s),
            StepSpec(5, "Jog in place",
                     "a steady jog -- your shoulders bounce, arms relaxed", jog_s),
        ]

        self.step_idx = 0
        self.phase = COUNTDOWN
        self._phase_started_ms: float | None = None
        self._capture = _Capture()
        self.measured: dict[str, float] = {}
        self.finished = False
        self.result: dict[str, Any] | None = None
        self.events: list[Event] = []       # drained by the app each tick
        self._message: str | None = None
        self.last_status: CalibStatus | None = None

    # -- driving -------------------------------------------------------------

    def tick(self, feats: Features | None, now_ms: float) -> CalibStatus:
        if self.finished:
            return self._status(now_ms, DONE, 0.0, 1.0)

        spec = self.steps[self.step_idx]
        if self._phase_started_ms is None:
            self._phase_started_ms = now_ms
        elapsed_s = (now_ms - self._phase_started_ms) / 1000.0

        if self.phase == COUNTDOWN:
            left = self._countdown_s - elapsed_s
            if left <= 0.0:
                self.phase = CAPTURE
                self._phase_started_ms = now_ms
                self._capture = _Capture()
                return self._status(now_ms, CAPTURE, spec.duration_s, 0.0)
            return self._status(now_ms, COUNTDOWN, left, elapsed_s / self._countdown_s)

        # CAPTURE
        if feats is not None and feats.ok:
            self._collect(feats)
        left = spec.duration_s - elapsed_s
        if left <= 0.0:
            self._finish_step(now_ms)
            return self._status(now_ms, self.phase, 0.0, 1.0)
        return self._status(now_ms, CAPTURE, left, elapsed_s / spec.duration_s)

    def _collect(self, feats: Features) -> None:
        cap = self._capture
        cap.cx.append(feats.cx)
        cap.shoulder_y.append(feats.shoulder_y)
        cap.shoulder_width.append(feats.shoulder_width)
        cap.d.append((feats.t_ms, feats.d))

    # -- step completion -----------------------------------------------------

    def _finish_step(self, now_ms: float) -> None:
        cap = self._capture
        if len(cap.cx) < 10:
            self._retry(now_ms, "could not see you -- check the framing")
            return

        idx = self.step_idx
        error: str | None = None
        v = self._validate

        if idx == 0:
            self.measured["x_centre"] = _median(cap.cx)
            self.measured["standing_shoulder_y"] = _median(cap.shoulder_y)
            self.measured["shoulder_width"] = _median(cap.shoulder_width)
        elif idx == 1:
            self.measured["x_left"] = _median(cap.cx)
            if abs(self.measured["x_left"] - self.measured["x_centre"]) < float(v["min_lane_offset"]):
                error = "step further to the left"
        elif idx == 2:
            self.measured["x_right"] = _median(cap.cx)
            if abs(self.measured["x_right"] - self.measured["x_centre"]) < float(v["min_lane_offset"]):
                error = "step further to the right"
        elif idx == 3:
            standing = self.measured["standing_shoulder_y"]
            self.measured["jump_peak_y"] = min(cap.shoulder_y)
            self.measured["jump_rise"] = standing - self.measured["jump_peak_y"]
            if self.measured["jump_rise"] < float(v["min_jump_rise"]):
                error = "jump higher"
        elif idx == 4:
            # Median of the hold, not the extreme, so the descent frames don't
            # count; the duck line lands at a fraction of this drop.
            standing = self.measured["standing_shoulder_y"]
            self.measured["duck_hold_y"] = _median(cap.shoulder_y)
            self.measured["duck_drop"] = self.measured["duck_hold_y"] - standing
            if self.measured["duck_drop"] < float(v["min_duck_drop"]):
                error = "duck lower -- drop your shoulders further"
        elif idx == 5:
            # Neutral is your JOGGING shoulder height: that is the posture you
            # will actually be in when a jump or duck has to fire.
            self.measured["jog_shoulder_y"] = _median(cap.shoulder_y)
            self.measured["sway_std"] = _std(cap.cx)
            d_values = [abs(d) for _, d in cap.d]
            amplitude = _percentile(d_values, 90.0)
            if self._bounce_mode:
                self.measured["bounce_amplitude"] = amplitude
                if amplitude < float(v["min_bounce_amplitude"]):
                    error = "jog with a bit more bounce"
                else:
                    amp = self._derived("bounce_amp", amplitude)
                    self.measured["jog_cadence_spm"] = self._cadence(cap.d, amp, both_edges=False)
            else:
                self.measured["d_amplitude"] = amplitude
                if amplitude < float(v["min_d_amplitude"]):
                    error = "swing your arms more"
                else:
                    amp = self._derived("step_amp", amplitude)
                    self.measured["jog_cadence_spm"] = self._cadence(cap.d, amp, both_edges=True)

        if error:
            self._retry(now_ms, error)
            return

        self.events.append(
            CalibStep(t_ms=now_ms, step=idx, values={k: round(x, 4) for k, x in self.measured.items()})
        )
        self._message = None
        self.step_idx += 1
        self.phase = COUNTDOWN
        self._phase_started_ms = None

        if self.step_idx >= len(self.steps):
            self._derive_result(now_ms)

    def _retry(self, now_ms: float, message: str) -> None:
        self._message = message
        self.events.append(
            Log(t_ms=now_ms, severity=Severity.WARN, message=f"calibration step {self.step_idx}: {message}")
        )
        self.phase = COUNTDOWN
        self._phase_started_ms = None

    def _cadence(self, samples: list[tuple[float, float]], amp: float, both_edges: bool) -> float:
        steps = count_steps(samples, amp, both_edges)
        span_s = (samples[-1][0] - samples[0][0]) / 1000.0
        return steps * 60.0 / span_s if span_s > 0 else 0.0

    def _derived(self, key: str, value: float) -> float:
        rule = self._derive[key]
        return _clamp(float(rule["factor"]) * value, float(rule["min"]), float(rule["max"]))

    # -- derivation ----------------------------------------------------------

    def _derive_result(self, now_ms: float) -> None:
        m = self.measured
        boundary_left = (m["x_centre"] + m["x_left"]) / 2.0
        boundary_right = (m["x_centre"] + m["x_right"]) / 2.0
        hysteresis = self._derived("lane_hysteresis", m["sway_std"])

        # §6.3, relaxed: compact spaces are allowed. If the torso line sways
        # more than the lanes can absorb, the hysteresis band is clamped to a
        # fraction of the lane half-width and the session says so, rather than
        # refusing to save. Only lanes too narrow to be lanes at all are
        # rejected.
        half_width = (boundary_right - boundary_left) / 2.0
        if half_width < float(self._validate["min_lane_half_width"]):
            self._message = "lanes too narrow -- step a little further apart"
            self.events.append(
                Log(
                    t_ms=now_ms,
                    severity=Severity.WARN,
                    message=self._message,
                    detail=f"half_width={half_width:.3f}",
                )
            )
            self.step_idx = 1
            self.phase = COUNTDOWN
            self._phase_started_ms = None
            return
        max_ratio = float(self._validate["max_hysteresis_vs_half_width"])
        limit = max_ratio * half_width
        if hysteresis > limit:
            self.events.append(
                Log(
                    t_ms=now_ms,
                    severity=Severity.WARN,
                    message="torso sway is large for these lane widths; hysteresis clamped",
                    detail=f"sway_std={m['sway_std']:.4f} wanted={hysteresis:.3f} "
                    f"clamped_to={limit:.3f} half_width={half_width:.3f}",
                )
            )
            hysteresis = limit

        result: dict[str, Any] = {
            "lanes.mode": "calibrated",
            "lanes.boundary_left": round(boundary_left, 4),
            "lanes.boundary_right": round(boundary_right, 4),
            "lanes.hysteresis": round(hysteresis, 4),
            # Two absolute screen positions, placed off the JOGGING shoulder
            # line: that is the posture the lines have to be crossed from.
            "vertical.jump_y": round(
                m["jog_shoulder_y"] - self._derived("jump_offset", m["jump_rise"]), 4
            ),
            "vertical.duck_y": round(
                m["jog_shoulder_y"] + self._derived("duck_offset", m["duck_drop"]), 4
            ),
            "running.min_cadence_spm": round(self._derived("min_cadence_spm", m["jog_cadence_spm"]), 1),
            "calibration.measured": {k: round(x, 4) for k, x in m.items()},
            "calibration.schema": CALIBRATION_SCHEMA,
        }
        if self._bounce_mode:
            result["running.bounce_amp"] = round(self._derived("bounce_amp", m["bounce_amplitude"]), 4)
        else:
            result["running.step_amp"] = round(self._derived("step_amp", m["d_amplitude"]), 4)
        self.result = result

        # Full transcript to telemetry, so a bad session is diagnosable (PLAN §9).
        self.events.append(CalibStep(t_ms=now_ms, step=99, values=dict(result)))
        self.finished = True
        self.phase = DONE

    # -- status --------------------------------------------------------------

    def _status(self, now_ms: float, phase: str, seconds_left: float, progress: float) -> CalibStatus:
        spec = self.steps[self.step_idx] if self.step_idx < len(self.steps) else self.steps[-1]
        status = CalibStatus(
            step=min(self.step_idx, len(self.steps) - 1),
            total_steps=len(self.steps),
            phase=phase,
            prompt=spec.prompt if not self.finished else "Calibration complete",
            detail=spec.detail if not self.finished else "saving...",
            seconds_left=max(0.0, seconds_left),
            progress=max(0.0, min(1.0, progress)),
            message=self._message,
            finished=self.finished,
        )
        self.last_status = status
        return status
