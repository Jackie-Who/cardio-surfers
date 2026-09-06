"""The only module in this package that writes text (PLAN §10.4, §16.2).

Console writes on Windows are synchronous and can block for milliseconds.
Printing per frame from the pipeline would quietly eat the latency budget that
PLAN §4 exists to protect, which would be an ironic way to break the project.

So: a bounded queue and a daemon writer thread do all console and file I/O.
Producers call `put_nowait` and bump a counter on `Full`; the ticker shows the
drop count, so silent loss is visible. The sink is just another subscriber on
the bus -- it never reaches back into the pipeline.
"""

from __future__ import annotations

import json
import queue
import sys
import threading
from enum import Enum
from typing import Any, Mapping, Sequence, TextIO

from .events import Event, Log, Severity, Tick, Verbosity

# Fields printed with an explicit sign: reading `dy=-0.02` at a glance beats
# working out whether 0.02 was up or down.
_SIGNED_KEYS = frozenset({"delta", "dy", "d", "dy_peak", "dy_min", "spm_delta"})

_RESET = "\033[0m"
_COLORS = {
    "LOG.error": "\033[91m",
    "LOG.warn": "\033[93m",
    "KEY_PRESS": "\033[96m",
    "LANE_CHANGE": "\033[95m",
    "JUMP": "\033[92m",
    "DUCK": "\033[92m",
    "GATE": "\033[93m",
    "SUPPRESSED": "\033[90m",
    "STEP": "\033[90m",
    "LATENCY": "\033[90m",
    "ARMED": "\033[92m",
    "DISARMED": "\033[91m",
    "STARTED": "\033[1m",
    "STOPPED": "\033[1m",
    "CALIB": "\033[96m",
}


class _NullStream:
    """Stand-in for stdout when there is none (windowed frozen build)."""

    def write(self, _text: str) -> int:
        return 0

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False


def _enable_vt_on_windows() -> bool:
    """Turn on ANSI escape handling for legacy consoles. Harmless elsewhere."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:  # noqa: BLE001 - colour is cosmetic, never fatal
        return False


def _fmt_value(key: str, value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:+d}" if key in _SIGNED_KEYS else str(value)
    if isinstance(value, float):
        return f"{value:+.3f}" if key in _SIGNED_KEYS else f"{value:.3f}"
    if isinstance(value, Mapping):
        return " ".join(f"{k}={_fmt_value(k, v)}" for k, v in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return ",".join(str(v) for v in value) if value else "-"
    return str(value)


def format_event(event: Event) -> str:
    """`[  12.480] LANE_CHANGE   from=1 to=2 delta=+1 cx=0.671` (PLAN §10.2).

    Human readable, still greppable. Pure function of the event, which is what
    lets test_telemetry assert on formatting without a running sink.
    """
    stamp = f"[{event.t_ms / 1000.0:8.3f}]"
    if isinstance(event, Log):
        body = event.message
        if event.detail:
            body = f"{body} ({event.detail})"
        return f"{stamp} {event.severity.value.upper():<13} {body}"
    body = " ".join(f"{k}={_fmt_value(k, v)}" for k, v in event.payload().items())
    return f"{stamp} {event.NAME:<13} {body}".rstrip()


def format_ticker(event: Tick) -> str:
    """One line, redrawn in place at 5 Hz (PLAN §10.2)."""
    return " ".join(f"{k}={_fmt_value(k, v)}" for k, v in event.state.items())


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Mapping):
        return dict(obj)
    if isinstance(obj, Sequence) and not isinstance(obj, (str, bytes)):
        return list(obj)
    return str(obj)


def event_to_json(event: Event) -> str:
    record: dict[str, Any] = {
        "event": event.NAME,
        "t_ms": round(event.t_ms, 3),
        "frame_t_ms": None if event.frame_t_ms is None else round(event.frame_t_ms, 3),
    }
    record.update(event.payload())
    return json.dumps(record, default=_json_default, separators=(",", ":"))


class TelemetrySink:
    """Threaded console + JSONL writer. Attach with `bus.subscribe(sink.handle)`.

    `--log-file` writes every event regardless of console verbosity, so you can
    be quiet on screen and complete on disk.
    """

    def __init__(
        self,
        verbosity: Verbosity = Verbosity.QUIET,
        log_path: str | None = None,
        *,
        color: bool = True,
        queue_size: int = 256,
        stream: TextIO | None = None,
    ) -> None:
        self.verbosity = Verbosity(verbosity)
        self.log_path = log_path
        self.dropped = 0
        self.written = 0

        # A windowed PyInstaller build has no stdout at all, and `.isatty()`
        # on None would take the app down before it drew a frame.
        self._stream = stream if stream is not None else (sys.stdout or _NullStream())
        self._color = (
            color
            and _enable_vt_on_windows()
            and hasattr(self._stream, "isatty")
            and self._stream.isatty()
        )
        self._queue: queue.Queue[Event | None] = queue.Queue(maxsize=queue_size)
        self._log_file: TextIO | None = None
        self._ticker_width = 0
        self._closed = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="telemetry", daemon=True
        )
        self._started = False

    # -- producer side: called from the hot path, must never block -----------

    def handle(self, event: Event) -> None:
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            # Dropping is the correct failure mode: a blocked producer would
            # cost frames, and the drop counter makes the loss visible.
            self.dropped += 1

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        if self.log_path:
            self._log_file = open(self.log_path, "a", encoding="utf-8", buffering=1)
        self._started = True
        self._thread.start()

    def close(self, timeout: float = 2.0) -> None:
        if not self._started:
            self._close_log()
            return
        self._closed.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout=timeout)
        self._clear_ticker()
        self._flush_stream()
        self._close_log()
        self._started = False

    def __enter__(self) -> "TelemetrySink":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- consumer side: the writer thread ------------------------------------

    def _run(self) -> None:
        while True:
            try:
                event = self._queue.get(timeout=0.25)
            except queue.Empty:
                if self._closed.is_set():
                    break
                continue
            if event is None:
                break
            try:
                self._write(event)
            except Exception:  # noqa: BLE001 - a bad line must not kill logging
                pass
        # Drain whatever is still queued so a crash report is not lost.
        while True:
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break
            if event is None:
                continue
            try:
                self._write(event)
            except Exception:  # noqa: BLE001
                pass
        self._flush_stream()

    def _write(self, event: Event) -> None:
        if self._log_file is not None:
            self._log_file.write(event_to_json(event) + "\n")
        self.written += 1

        if isinstance(event, Tick):
            if self.verbosity >= Verbosity.TICKER:
                self._write_ticker(format_ticker(event))
            return

        # Log(info) is chatter; warnings and errors always get through.
        if isinstance(event, Log) and event.severity is Severity.INFO:
            if self.verbosity < Verbosity.EVENTS:
                return
        elif event.LEVEL > self.verbosity:
            return

        self._write_line(format_event(event), self._color_for(event))

    def _color_for(self, event: Event) -> str:
        if not self._color:
            return ""
        if isinstance(event, Log):
            return _COLORS.get(f"LOG.{event.severity.value}", "")
        return _COLORS.get(event.NAME, "")

    def _write_line(self, text: str, color: str = "") -> None:
        self._clear_ticker()
        if color:
            text = f"{color}{text}{_RESET}"
        self._stream.write(text + "\n")
        self._flush_stream()

    def _write_ticker(self, text: str) -> None:
        pad = max(0, self._ticker_width - len(text))
        self._stream.write("\r" + text + " " * pad)
        self._ticker_width = len(text)
        self._flush_stream()

    def _clear_ticker(self) -> None:
        if self._ticker_width:
            self._stream.write("\r" + " " * self._ticker_width + "\r")
            self._ticker_width = 0

    def _flush_stream(self) -> None:
        try:
            self._stream.flush()
        except Exception:  # noqa: BLE001 - a closed stream is not worth crashing over
            pass

    def _close_log(self) -> None:
        if self._log_file is not None:
            try:
                self._log_file.close()
            finally:
                self._log_file = None
