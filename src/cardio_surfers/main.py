"""CLI + entry wiring (PLAN §11).

`cardio-surfers` with no flags launches the windowed app: menu, game, debug,
calibration, settings in one window. The clock is read here (and in app.py)
once per frame and passed down (PLAN §16.1); `--replay` substitutes recorded
timestamps through the identical pipeline.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from . import PHASE, __version__
from .config import DEFAULT_CONFIG_PATH, ConfigError, load_config
from .events import EventBus, Log, Severity, Started, Stopped, Tick, Verbosity
from .telemetry import TelemetrySink
from .timing import ManualClock, MonotonicClock


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cardio-surfers",
        description="Full-body motion controller for Subway Surfers. "
        "Run with no flags for the windowed app.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Game mode publishes KeyPress events but never touches the OS",
    )
    parser.add_argument(
        "--recalibrate", action="store_true",
        help="Open the app straight into calibration",
    )
    parser.add_argument("--config", metavar="PATH", default=None,
                        help="Config file (default ./config.json)")
    parser.add_argument("--camera", metavar="N", type=int, default=None,
                        help="Camera index override (Settings has a dropdown too)")
    parser.add_argument("-v", dest="verbose", action="count", default=0,
                        help="Console verbosity: -v events, -vv + ticker, -vvv + trace")
    parser.add_argument("--log-file", metavar="PATH", default=None,
                        help="Write all events as JSONL, independent of console verbosity")
    parser.add_argument("--record", metavar="PATH", default=None,
                        help="Record raw landmarks to JSONL for replay")
    parser.add_argument("--replay", metavar="PATH", default=None,
                        help="Replay a recording through the identical pipeline, sender stubbed")
    parser.add_argument("--no-preview", action="store_true",
                        help="Headless: no window, console only (debug-style dry run)")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colour")
    parser.add_argument("--probe", action="store_true",
                        help="Setup self-test, then exit")
    parser.add_argument("--log-session", action="store_true",
                        help="Write a per-session stats JSON on exit")
    parser.add_argument("--version", action="version",
                        version=f"cardio-surfers {__version__}")
    return parser


def _verbosity(count: int) -> Verbosity:
    return Verbosity(min(count, int(Verbosity.TRACE)))


def run(args: argparse.Namespace) -> int:
    clock = MonotonicClock()
    try:
        config, found = load_config(args.config)
    except ConfigError as exc:
        sys.stderr.write(f"config error: {exc}\n")
        return 2

    verbosity = _verbosity(args.verbose)
    if args.probe:
        verbosity = max(verbosity, Verbosity.EVENTS)

    sink = TelemetrySink(
        verbosity,
        log_path=args.log_file,
        color=bool(config.get("telemetry.color")) and not args.no_color,
        queue_size=int(config.get("telemetry.queue_size")),
    )
    bus = EventBus()
    bus.subscribe(sink.handle)
    sink.start()

    bus.publish(
        Started(
            t_ms=clock.now_ms(),
            version=__version__,
            phase=PHASE,
            config_path=config.path or "(defaults from config.example.json)",
        )
    )
    if not found and not args.probe and args.replay is None:
        bus.publish(
            Log(
                t_ms=clock.now_ms(),
                severity=Severity.WARN,
                message="no config.json found; running on defaults",
                detail="use CALIBRATE in the menu -- lanes fall back to thirds until then",
            )
        )
    if config.stale_calibration:
        bus.publish(
            Log(
                t_ms=clock.now_ms(),
                severity=Severity.WARN,
                message="calibration in config.json is from an older version and was ignored",
                detail="its thresholds were measured in different units; "
                "run CALIBRATE again (defaults apply until then)",
            )
        )

    try:
        if args.probe:
            from .probe import run_probe

            camera_index = (
                args.camera if args.camera is not None
                else int(config.get("camera.index"))
            )
            return run_probe(bus, clock, config, camera_index)
        if args.replay:
            return _run_replay(bus, config, sink, args)
        if args.no_preview:
            return _run_headless(bus, clock, config, sink, args)

        from .app import App

        app = App(
            bus, clock, config, sink, args,
            config_path=args.config or DEFAULT_CONFIG_PATH,
        )
        if args.recalibrate:
            app._enter("calibrate")
        return app.run()
    finally:
        bus.publish(
            Stopped(t_ms=clock.now_ms(), reason="exit", duration_ms=clock.now_ms())
        )
        sink.close()


def _make_dry_pipeline(bus: EventBus, config):
    """Pipeline with a stubbed sender and no focus gate, always armed."""
    from .keys import DryRunSender, KeyDispatcher
    from .pipeline import Pipeline

    mapping = config.section("keys.presets")[config.get("keys.mapping")]
    dispatcher = KeyDispatcher(bus, DryRunSender(), None, mapping)
    dispatcher.armed = True
    pipeline = Pipeline(bus, config, dispatcher)
    # Replay and headless runs simulate an armed session, so the gate must be
    # live -- otherwise it is frozen and never reaches WARNING or LOCKED.
    pipeline.set_active(True, 0.0)
    return pipeline


def _run_replay(bus: EventBus, config, sink: TelemetrySink, args) -> int:
    """PLAN §12.1: the identical pipeline, driven by recorded timestamps."""
    from .record import iter_recording

    if not os.path.exists(args.replay):
        sys.stderr.write(f"recording not found: {args.replay}\n")
        return 2

    clock = ManualClock()
    pipeline = _make_dry_pipeline(bus, config)
    ticker_period = 1000.0 / float(config.get("telemetry.ticker_hz"))
    last_tick = -1e12
    frames = 0

    for pose in iter_recording(args.replay):
        clock.set(max(clock.now_ms(), pose.t_ms))
        now_ms = clock.now_ms()
        pipeline.tick(pose, now_ms)
        frames += 1
        if now_ms - last_tick >= ticker_period:
            last_tick = now_ms
            bus.publish(Tick(t_ms=now_ms, state=pipeline.hud_state(now_ms)))

    bus.publish(
        Log(
            t_ms=clock.now_ms(),
            severity=Severity.INFO,
            message=f"replay finished: {frames} frames",
            detail=args.replay,
        )
    )
    return 0


def _run_headless(bus: EventBus, clock: MonotonicClock, config, sink, args) -> int:
    """Console-only pipeline: live camera, dry-run keys, no window."""
    from .camera import CameraThread
    from .pose import PoseTracker
    from .record import PoseRecorder
    from .stats import SessionStats

    camera = CameraThread(
        bus, clock,
        index=(args.camera if args.camera is not None
               else int(config.get("camera.index"))),
        width=int(config.get("camera.width")),
        height=int(config.get("camera.height")),
        fps=float(config.get("camera.fps")),
        backend=config.get("camera.backend"),
        flip=bool(config.get("camera.flip")),
        process_width=int(config.get("camera.process_width")),
    )
    if not camera.start():
        return 1
    tracker = PoseTracker(
        bus, clock,
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
    if not tracker.start():
        camera.stop()
        return 1

    pipeline = _make_dry_pipeline(bus, config)
    stats = SessionStats(bus)
    recorder = PoseRecorder(args.record) if args.record else None
    ticker_period = 1000.0 / float(config.get("telemetry.ticker_hz"))
    last_tick = -1e12
    last_seq = -1

    bus.publish(
        Log(
            t_ms=clock.now_ms(),
            severity=Severity.INFO,
            message="running headless (dry-run keys) -- ctrl-c to quit",
        )
    )
    try:
        while True:
            now_ms = clock.now_ms()
            frame = camera.read()
            if frame is not None:
                tracker.submit(frame)
            pose = tracker.latest()
            if pose is not None and pose.seq != last_seq:
                last_seq = pose.seq
                if recorder is not None:
                    recorder.record(pose)
                pipeline.tick(pose, now_ms)
            if now_ms - last_tick >= ticker_period:
                last_tick = now_ms
                bus.publish(Tick(t_ms=now_ms, state=pipeline.hud_state(now_ms)))
            if frame is None:
                time.sleep(0.002)
    except KeyboardInterrupt:
        pass
    finally:
        if recorder is not None:
            recorder.close()
        camera.stop()
        tracker.close()
        bus.publish(
            Log(
                t_ms=clock.now_ms(),
                severity=Severity.INFO,
                message="session summary",
                detail=" ".join(f"{k}={v}" for k, v in stats.summary().items()),
            )
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
