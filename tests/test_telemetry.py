"""Telemetry sink behaviour and the two invariant scans (PLAN §12.6, §16.1-2).

The scans are cheap and they stay true as the code grows, which is the whole
point: the modules they guard mostly do not exist yet, so the scanner itself is
tested against synthetic source to prove the mechanism works now rather than
discovering it was a no-op at P4.
"""

from __future__ import annotations

import ast
import json
import os
import queue
import sys
import time
from io import StringIO

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cardio_surfers import events as events_module  # noqa: E402
from cardio_surfers.events import (  # noqa: E402
    EventBus,
    GateTransition,
    LaneChange,
    Log,
    Severity,
    Step,
    Suppressed,
    SuppressReason,
    Tick,
    Verbosity,
)
from cardio_surfers.telemetry import (  # noqa: E402
    TelemetrySink,
    event_to_json,
    format_event,
    format_ticker,
)

PACKAGE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "src", "cardio_surfers")
)

# The one module allowed to write text (PLAN §16.2).
PRINT_EXEMPT = {"telemetry.py"}

# main.py may use sys.stderr for bootstrap failures that happen before a sink
# exists (an unreadable config file); nothing else may touch the streams.
STREAM_EXEMPT = {"telemetry.py", "main.py"}

# Clock injection (PLAN §16.1): these must never read a clock themselves. They
# take `now_ms` as a parameter, which is what makes --replay deterministic and
# the timing tests non-flaky. Files not yet written are simply skipped.
NO_CLOCK_MODULES = (
    "lanes.py",
    "vertical.py",
    "running.py",
    "keys.py",
    "features.py",
    "filters.py",
)
CLOCK_FUNCTIONS = {"monotonic", "time", "perf_counter", "monotonic_ns", "time_ns"}


def _package_files() -> list[str]:
    return sorted(
        name
        for name in os.listdir(PACKAGE_DIR)
        if name.endswith(".py")
    )


def find_print_calls(source: str) -> list[int]:
    """Line numbers of `print(...)` calls."""
    tree = ast.parse(source)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    ]


def find_stream_writes(source: str) -> list[int]:
    """Line numbers touching sys.stdout / sys.stderr."""
    tree = ast.parse(source)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("stdout", "stderr"):
            value = node.value
            if isinstance(value, ast.Name) and value.id == "sys":
                hits.append(node.lineno)
    return hits


def find_clock_calls(source: str) -> list[tuple[int, str]]:
    """Line numbers of time.monotonic() / time.perf_counter() and friends."""
    tree = ast.parse(source)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in CLOCK_FUNCTIONS:
            base = func.value
            if isinstance(base, ast.Name) and base.id == "time":
                hits.append((node.lineno, f"time.{func.attr}"))
        elif isinstance(func, ast.Name) and func.id in CLOCK_FUNCTIONS:
            # `from time import monotonic` then a bare call.
            hits.append((node.lineno, func.id))
    return hits


# ---------------------------------------------------------------------------
# The scanners themselves (proven now, before the modules they guard exist)
# ---------------------------------------------------------------------------

def test_print_scanner_detects_a_print():
    assert find_print_calls("x = 1\nprint('hi')\n") == [2]
    assert find_print_calls("x = 1\nlogger.print_it()\n") == []


def test_clock_scanner_detects_clock_reads():
    assert find_clock_calls("import time\nt = time.monotonic()\n") == [
        (2, "time.monotonic")
    ]
    assert find_clock_calls("from time import perf_counter\nt = perf_counter()\n") == [
        (2, "perf_counter")
    ]
    # Passing a timestamp in is the pattern we want, and must not trip the scan.
    assert find_clock_calls("def tick(self, now_ms):\n    return now_ms\n") == []


def test_stream_scanner_detects_writes():
    assert find_stream_writes("import sys\nsys.stderr.write('x')\n") == [2]
    assert find_stream_writes("self.stream.write('x')\n") == []


# ---------------------------------------------------------------------------
# The invariants (PLAN §16.1, §16.2)
# ---------------------------------------------------------------------------

def test_no_print_outside_telemetry():
    offenders = []
    for name in _package_files():
        if name in PRINT_EXEMPT:
            continue
        with open(os.path.join(PACKAGE_DIR, name), encoding="utf-8") as handle:
            for line in find_print_calls(handle.read()):
                offenders.append(f"{name}:{line}")
    assert not offenders, (
        "print() outside telemetry.py (PLAN §16.2) -- console I/O on Windows "
        f"blocks and eats the latency budget: {offenders}"
    )


def test_no_direct_stream_writes_outside_telemetry():
    offenders = []
    for name in _package_files():
        if name in STREAM_EXEMPT:
            continue
        with open(os.path.join(PACKAGE_DIR, name), encoding="utf-8") as handle:
            for line in find_stream_writes(handle.read()):
                offenders.append(f"{name}:{line}")
    assert not offenders, f"direct sys.stdout/stderr use (PLAN §10.4): {offenders}"


def test_fsm_modules_never_read_a_clock():
    """PLAN §16.1. Skips modules from phases that have not landed yet."""
    offenders = []
    for name in NO_CLOCK_MODULES:
        path = os.path.join(PACKAGE_DIR, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as handle:
            for line, call in find_clock_calls(handle.read()):
                offenders.append(f"{name}:{line} {call}")
    assert not offenders, (
        "an FSM read a clock instead of taking now_ms (PLAN §16.1) -- this is "
        f"what turns every timing test into a coin flip: {offenders}"
    )


def test_timing_module_is_the_only_clock_holder():
    """The exemption in §16.1 exists for exactly one module."""
    with open(os.path.join(PACKAGE_DIR, "timing.py"), encoding="utf-8") as handle:
        assert find_clock_calls(handle.read()), "timing.py should own the real clock"


# ---------------------------------------------------------------------------
# Sink behaviour
# ---------------------------------------------------------------------------

def test_sink_drops_rather_than_blocks_when_full():
    """PLAN §10.4: producers must never block on a full queue."""
    sink = TelemetrySink(Verbosity.TRACE, queue_size=8, stream=StringIO())
    # Deliberately not started, so nothing drains the queue.
    for i in range(20):
        sink.handle(Log(t_ms=float(i), severity=Severity.INFO, message=f"m{i}"))
    assert sink.dropped == 12
    assert sink._queue.qsize() == 8


def test_sink_handle_is_fast_when_full():
    sink = TelemetrySink(Verbosity.TRACE, queue_size=4, stream=StringIO())
    for i in range(4):
        sink.handle(Log(t_ms=float(i), severity=Severity.INFO, message="fill"))
    start = time.perf_counter()
    for i in range(1000):
        sink.handle(Log(t_ms=float(i), severity=Severity.INFO, message="overflow"))
    elapsed = time.perf_counter() - start
    assert sink.dropped == 1000
    assert elapsed < 0.5, f"handle() blocked for {elapsed:.3f}s on a full queue"


def test_sink_writes_events_at_matching_verbosity():
    stream = StringIO()
    sink = TelemetrySink(Verbosity.EVENTS, stream=stream, color=False)
    sink.start()
    sink.handle(LaneChange(t_ms=12480.0, from_lane=1, to_lane=2, delta=1, cx=0.671))
    sink.handle(Step(t_ms=12500.0, foot="L", spm_now=152.0))  # TRACE only
    sink.close()

    output = stream.getvalue()
    assert "LANE_CHANGE" in output
    assert "STEP" not in output, "a -vvv event leaked into -v output"


def test_sink_trace_shows_steps_and_suppressions():
    stream = StringIO()
    sink = TelemetrySink(Verbosity.TRACE, stream=stream, color=False)
    sink.start()
    sink.handle(Step(t_ms=100.0, foot="R", spm_now=148.0))
    sink.handle(
        Suppressed(
            t_ms=200.0,
            what="jump",
            reason=SuppressReason.REFRACTORY,
            detail="remaining=182ms",
        )
    )
    sink.close()

    output = stream.getvalue()
    assert "STEP" in output
    assert "SUPPRESSED" in output
    assert "REFRACTORY" in output


def test_warnings_survive_default_verbosity():
    """Errors must not need -v to appear."""
    stream = StringIO()
    sink = TelemetrySink(Verbosity.QUIET, stream=stream, color=False)
    sink.start()
    sink.handle(Log(t_ms=1.0, severity=Severity.ERROR, message="camera gone"))
    sink.handle(Log(t_ms=2.0, severity=Severity.INFO, message="chatter"))
    sink.close()

    output = stream.getvalue()
    assert "camera gone" in output
    assert "chatter" not in output


def test_ticker_redraws_in_place():
    stream = StringIO()
    sink = TelemetrySink(Verbosity.TICKER, stream=stream, color=False)
    sink.start()
    sink.handle(Tick(t_ms=1000.0, state={"fps": 30.0, "gate": "RUNNING"}))
    sink.handle(Tick(t_ms=1200.0, state={"fps": 29.0, "gate": "RUNNING"}))
    sink.close()

    output = stream.getvalue()
    assert output.startswith("\r"), "the ticker must redraw in place, not scroll"
    assert output.count("\n") <= 1


def test_log_file_captures_everything_regardless_of_console_tier(tmp_path):
    """PLAN §10.2: quiet on screen, complete on disk."""
    log_path = tmp_path / "debug.jsonl"
    stream = StringIO()
    sink = TelemetrySink(
        Verbosity.QUIET, log_path=str(log_path), stream=stream, color=False
    )
    sink.start()
    sink.handle(LaneChange(t_ms=1.0, from_lane=0, to_lane=2, delta=2, cx=0.8))
    sink.handle(Step(t_ms=2.0, foot="L", spm_now=150.0))
    sink.handle(
        Suppressed(t_ms=3.0, what="lane", reason=SuppressReason.GATE_LOCKED, detail="spm=40")
    )
    sink.close()

    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert [r["event"] for r in records] == ["LANE_CHANGE", "STEP", "SUPPRESSED"]
    assert stream.getvalue() == "", "nothing should reach a quiet console"


# ---------------------------------------------------------------------------
# Formatting (a separate consumer, tested separately -- PLAN §12)
# ---------------------------------------------------------------------------

def test_format_event_matches_the_documented_shape():
    line = format_event(
        LaneChange(t_ms=12480.0, from_lane=1, to_lane=2, delta=1, cx=0.671)
    )
    assert line == "[  12.480] LANE_CHANGE   from=1 to=2 delta=+1 cx=0.671"


def test_format_event_signs_the_fields_that_need_it():
    line = format_event(
        GateTransition(
            t_ms=14200.0, from_state="RUNNING", to_state="WARNING", spm=71.0, floor=96.0
        )
    )
    assert "from_state=RUNNING" in line
    assert "to_state=WARNING" in line


def test_format_ticker_is_one_line():
    line = format_ticker(
        Tick(
            t_ms=1.0,
            state={"lane": "CENTRE", "cx": 0.501, "dy": 0.01, "d": -0.14, "fps": 30},
        )
    )
    assert "\n" not in line
    assert "dy=+0.010" in line and "d=-0.140" in line
    assert "cx=0.501" in line


def test_event_to_json_renames_reserved_fields():
    record = json.loads(
        event_to_json(LaneChange(t_ms=1.0, from_lane=0, to_lane=1, delta=1, cx=0.4))
    )
    assert record["from"] == 0 and record["to"] == 1
    assert record["event"] == "LANE_CHANGE"


def test_event_to_json_serializes_enums():
    record = json.loads(
        event_to_json(
            Suppressed(
                t_ms=1.0, what="lane", reason=SuppressReason.WINDOW_FOCUS, detail="Notepad"
            )
        )
    )
    assert record["reason"] == "WINDOW_FOCUS"


# ---------------------------------------------------------------------------
# Event bus
# ---------------------------------------------------------------------------

def test_bus_fans_out_to_every_subscriber():
    bus = EventBus()
    a, b = [], []
    bus.subscribe(a.append)
    bus.subscribe(b.append)
    event = Log(t_ms=1.0, severity=Severity.INFO, message="x")
    bus.publish(event)
    assert a == [event] and b == [event], "subscribers must see the same object"


def test_bus_survives_a_broken_subscriber():
    """A bad HUD must not take the key sender down with it."""
    bus = EventBus()
    received = []

    def explode(_event):
        raise RuntimeError("boom")

    bus.subscribe(explode)
    bus.subscribe(received.append)
    bus.publish(Log(t_ms=1.0, severity=Severity.INFO, message="x"))

    assert len(received) == 1
    assert bus.subscriber_errors == 1
    assert "boom" in (bus.last_error or "")


def test_events_are_frozen():
    event = LaneChange(t_ms=1.0, from_lane=0, to_lane=1, delta=1, cx=0.5)
    with pytest.raises(Exception):
        event.cx = 0.9  # type: ignore[misc]


def test_suppress_reasons_are_the_closed_set_from_the_plan():
    """PLAN §10.3 -- the enum is the contract, so drift here is a spec change."""
    assert {r.value for r in SuppressReason} == {
        "GATE_LOCKED",
        "NOT_ARMED",
        "WINDOW_FOCUS",
        "REFRACTORY",
        "HYSTERESIS",
        "POST_JUMP_LANE",
        "LOW_VISIBILITY",
        "STEP_AMPLITUDE",
        "STARTUP_GRACE",
    }
