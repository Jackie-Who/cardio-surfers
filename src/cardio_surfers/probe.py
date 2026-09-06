"""`--probe`: setup self-test, then exit (PLAN §11).

"It doesn't work" is nearly always one of three things -- camera index, framing,
or the focus regex -- and this answers all three in one command.

Everything goes out as `Log` events like the rest of the app; main raises the
console tier for a probe run so the report is visible at default verbosity.
"""

from __future__ import annotations

import re
import time
from typing import Any

import numpy as np

from .camera import CameraThread, enumerate_cameras
from .config import Config
from .events import EventBus, Log, Severity
from .pose import (
    L_ELB,
    L_HIP,
    L_KNEE,
    L_SH,
    L_WRI,
    PoseTracker,
    R_ELB,
    R_HIP,
    R_KNEE,
    R_SH,
    R_WRI,
)
from .timing import Clock, FpsCounter

# Close-range revision: arms are the running signal, so wrists and elbows are
# what matter; knees are informational only.
_GROUPS = {
    "shoulders": (L_SH, R_SH),
    "elbows": (L_ELB, R_ELB),
    "wrists": (L_WRI, R_WRI),
    "hips": (L_HIP, R_HIP),
    "knees": (L_KNEE, R_KNEE),
}


def _say(
    bus: EventBus,
    clock: Clock,
    message: str,
    detail: str | None = None,
    severity: Severity = Severity.INFO,
) -> None:
    bus.publish(
        Log(t_ms=clock.now_ms(), severity=severity, message=message, detail=detail)
    )


def _foreground_window_title() -> str | None:
    try:
        import pygetwindow
    except Exception:  # noqa: BLE001 - optional at probe time
        return None
    try:
        window = pygetwindow.getActiveWindow()
    except Exception:  # noqa: BLE001 - pygetwindow throws on odd window states
        return None
    if window is None:
        return None
    return getattr(window, "title", None)


def run_probe(bus: EventBus, clock: Clock, config: Config, camera_index: int) -> int:
    """Returns a process exit code: 0 if the setup looks usable."""
    problems: list[str] = []

    _say(bus, clock, "=== cardio-surfers probe ===")

    # -- 1. cameras ---------------------------------------------------------
    backend = config.get("camera.backend")
    max_index = int(config.get("probe.max_camera_index"))
    _say(bus, clock, f"scanning camera indices 0..{max_index - 1} ({backend})")
    cameras = enumerate_cameras(max_index=max_index, backend=backend)
    if not cameras:
        _say(
            bus,
            clock,
            "no cameras found",
            "check the webcam is plugged in and not held by another app",
            Severity.ERROR,
        )
        return 1
    for cam in cameras:
        _say(
            bus,
            clock,
            f"camera {cam['index']}: {cam['width']}x{cam['height']} "
            f"@ {cam['fps']:.0f} fps reported, reads={'yes' if cam['reads'] else 'NO'}",
        )

    # -- 2. measured capture rate -------------------------------------------
    sample_seconds = float(config.get("probe.sample_seconds"))
    camera = CameraThread(
        bus,
        clock,
        index=camera_index,
        width=int(config.get("camera.width")),
        height=int(config.get("camera.height")),
        fps=float(config.get("camera.fps")),
        backend=backend,
        flip=bool(config.get("camera.flip")),
        process_width=int(config.get("camera.process_width")),
    )
    if not camera.start():
        _say(bus, clock, f"cannot open camera {camera_index}", None, Severity.ERROR)
        return 1

    # -- 3. model -----------------------------------------------------------
    tracker = PoseTracker(
        bus,
        clock,
        model_path=config.get("pose.model_path"),
        num_poses=int(config.get("pose.num_poses")),
        min_pose_detection_confidence=float(
            config.get("pose.min_pose_detection_confidence")
        ),
        min_pose_presence_confidence=float(
            config.get("pose.min_pose_presence_confidence")
        ),
        min_tracking_confidence=float(config.get("pose.min_tracking_confidence")),
    )
    model_ok = tracker.start()
    if not model_ok:
        problems.append("model missing (run scripts/fetch_model.py)")

    _say(bus, clock, f"sampling for {sample_seconds:.0f}s -- stand in front of the camera")

    fps_counter = FpsCounter()
    vis_samples: list[np.ndarray] = []
    frames = 0
    present_frames = 0
    latencies: list[float] = []

    deadline = time.monotonic() + sample_seconds
    while time.monotonic() < deadline:
        frame = camera.read()
        if frame is None:
            time.sleep(0.002)
            continue
        frames += 1
        fps_counter.tick(clock.now_ms())
        if model_ok:
            tracker.submit(frame)
        time.sleep(0.001)

    # Let the last few async results land before reading the tally.
    time.sleep(0.3)
    pose_frame = tracker.latest() if model_ok else None
    if pose_frame is not None and pose_frame.present:
        vis_samples.append(pose_frame.visibility.copy())
        latencies.append(pose_frame.latency_ms)
        present_frames = tracker.results - tracker.empty_results

    measured_fps = fps_counter.value(clock.now_ms())
    stats = camera.describe()
    _say(
        bus,
        clock,
        f"capture: {frames} frames, {measured_fps:.1f} fps measured, "
        f"{stats['dropped']} stale frames dropped, {stats['read_failures']} read failures",
    )
    if measured_fps < float(config.get("camera.fps")) * 0.7:
        problems.append(
            f"measured {measured_fps:.1f} fps against a requested "
            f"{float(config.get('camera.fps')):.0f} -- check lighting and USB bandwidth"
        )

    # -- 4. landmark visibility --------------------------------------------
    if model_ok:
        _say(
            bus,
            clock,
            f"inference: {tracker.submitted} submitted, {tracker.results} results, "
            f"{tracker.empty_results} with no body",
        )
        if tracker.results == 0:
            problems.append("no inference results came back at all")
        elif present_frames == 0 and pose_frame is not None and not pose_frame.present:
            problems.append("a camera image arrived but no body was detected in it")
        if vis_samples:
            mean_vis = np.mean(np.stack(vis_samples), axis=0)
            for name, (left, right) in _GROUPS.items():
                value = float((mean_vis[left] + mean_vis[right]) / 2.0)
                marker = "ok" if value >= 0.5 else "LOW"
                _say(bus, clock, f"visibility {name:<10} {value:.2f}  {marker}")
            thr = float(config.get("pose.visibility_threshold"))
            wrists = float((mean_vis[L_WRI] + mean_vis[R_WRI]) / 2.0)
            elbows = float((mean_vis[L_ELB] + mean_vis[R_ELB]) / 2.0)
            if wrists < thr and elbows < thr:
                problems.append(
                    f"arm visibility too low (wrists {wrists:.2f}, elbows "
                    f"{elbows:.2f}) -- running detection needs your arms in "
                    "frame; raise the camera or step back slightly"
                )
        if latencies:
            _say(
                bus,
                clock,
                f"capture->landmarks latency: {latencies[-1]:.0f} ms (single sample)",
            )

    # -- 5. focus gate -------------------------------------------------------
    pattern = config.get("keys.focus_regex")
    title = _foreground_window_title()
    if title is None:
        _say(
            bus,
            clock,
            "foreground window: unavailable",
            "pygetwindow could not read it; the focus gate will refuse to send keys",
            Severity.WARN,
        )
    else:
        matches = re.search(pattern, title) is not None
        _say(
            bus,
            clock,
            f"foreground window: {title!r}",
            f"focus regex {pattern!r} {'matches' if matches else 'does NOT match'} "
            "(this is your terminal right now, so a mismatch here is expected -- "
            "what matters is that the game window matches)",
        )

    camera.stop()
    tracker.close()

    # -- verdict -------------------------------------------------------------
    if problems:
        for problem in problems:
            _say(bus, clock, f"PROBLEM: {problem}", None, Severity.WARN)
        _say(bus, clock, f"probe finished with {len(problems)} problem(s)")
        return 1
    _say(bus, clock, "probe finished: setup looks usable")
    return 0
