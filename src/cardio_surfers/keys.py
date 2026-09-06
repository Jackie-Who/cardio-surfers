"""PressQueue, focus gate, key senders, arm/disarm hotkey (PLAN §8).

The queue is pure (takes `now_ms`, no clock reads -- PLAN §16.1). The senders
do I/O by definition, but even they never read a clock; hold timing uses a
waitable event on a worker thread.

Four safety mechanisms, all logging their refusals (§10.3):
gate check (in pipeline), focus gate, arm/disarm hotkey, dry-run backend.
"""

from __future__ import annotations

import re
import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque

from .events import EventBus, KeyPress, Suppressed, SuppressReason


@dataclass(frozen=True)
class Press:
    key: str        # concrete key name ("right", "up", "d", ...)
    source: str     # lane | jump | duck
    enqueued_ms: float


class PressQueue:
    """Rate-limited: one press per `gap_ms`, never straight to the keyboard.

    Web ports run a lane-change animation and drop input received during it;
    two presses 5 ms apart very likely register as one, and the double-dodge
    silently becomes a single (PLAN §6.2). Pure; `drain` takes `now_ms`.
    """

    def __init__(self, gap_ms: float) -> None:
        self.gap_ms = float(gap_ms)
        self._queue: Deque[Press] = deque()
        self._last_emit_ms = -1e12

    def enqueue(self, key: str, source: str, now_ms: float) -> None:
        self._queue.append(Press(key=key, source=source, enqueued_ms=now_ms))

    def drain(self, now_ms: float) -> list[Press]:
        """At most one press per call window, spaced >= gap_ms apart."""
        out: list[Press] = []
        while self._queue and now_ms - self._last_emit_ms >= self.gap_ms:
            out.append(self._queue.popleft())
            self._last_emit_ms = now_ms
            # Only one per drain unless the gap is zero: the next press earns
            # its own tick once the gap has elapsed again.
            if self.gap_ms > 0:
                break
        return out

    def __len__(self) -> int:
        return len(self._queue)

    def clear(self) -> None:
        self._queue.clear()


class FocusGate:
    """Refuse to type into our own window; optionally require a title match.

    With `pattern` empty (the default) keys go to whatever window is in the
    foreground -- the game, a text editor you are testing in, anything --
    except the controller's own window, which never receives its own presses.
    A non-empty regex restricts delivery to matching titles (PLAN §8's
    original policy). On refusal the actual title is reported, so "why did
    nothing happen" is answered by reading one line.
    """

    def __init__(self, pattern: str, exclude_title: str | None = None) -> None:
        self._regex = re.compile(pattern) if pattern else None
        self._exclude = exclude_title

    def current_title(self) -> str | None:
        try:
            import pygetwindow

            window = pygetwindow.getActiveWindow()
        except Exception:  # noqa: BLE001 - odd window states raise inside pygetwindow
            return None
        if window is None:
            return None
        return getattr(window, "title", None)

    def check(self) -> tuple[bool, str]:
        title = self.current_title()
        if title is None:
            return False, "(no foreground window)"
        if self._exclude and self._exclude in title:
            return False, title
        if self._regex is not None and self._regex.search(title) is None:
            return False, title
        return True, title


class DryRunSender:
    """Publishes nothing itself; the dispatcher publishes KeyPress as usual."""

    name = "dry-run"

    def send(self, key: str) -> None:
        return

    def close(self) -> None:
        return


class RealKeySender:
    """SendInput scancodes via pydirectinput-rgx, on a worker thread.

    Explicit down/hold/up rather than press(): some engines poll input once per
    frame and a sub-frame tap can be missed (PLAN §8). The hold happens off the
    main loop so a 60 ms hold never stalls a 33 ms frame budget.
    """

    name = "sendinput"

    def __init__(self, hold_ms: float) -> None:
        import pydirectinput

        pydirectinput.PAUSE = 0.0    # no library-imposed sleep between calls
        self._lib = pydirectinput
        self._hold_s = float(hold_ms) / 1000.0
        self._work: Deque[str] = deque()
        self._signal = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="keysender", daemon=True)
        self._thread.start()

    def send(self, key: str) -> None:
        self._work.append(key)
        self._signal.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._signal.wait(timeout=0.25)
            self._signal.clear()
            while self._work:
                key = self._work.popleft()
                try:
                    self._lib.keyDown(key)
                    # A waitable event as the hold timer: interruptible on
                    # shutdown, and no time.* import in this module.
                    self._stop.wait(self._hold_s)
                    self._lib.keyUp(key)
                except Exception:  # noqa: BLE001 - a failed press must not kill the thread
                    pass

    def close(self) -> None:
        self._stop.set()
        self._signal.set()
        self._thread.join(timeout=1.0)


class KeyDispatcher:
    """The last stop before the OS: armed check, focus check, send, publish."""

    def __init__(
        self,
        bus: EventBus,
        sender,
        focus: FocusGate | None,
        mapping: dict[str, str],
    ) -> None:
        self._bus = bus
        self._sender = sender
        self._focus = focus            # None = skip (debug mode / replay)
        self._mapping = dict(mapping)  # action name -> concrete key
        self.armed = False
        self.presses_sent = 0

    def key_for(self, action: str) -> str:
        return self._mapping[action]

    def set_mapping(self, mapping: dict[str, str]) -> None:
        self._mapping = dict(mapping)

    def dispatch(self, press: Press, now_ms: float) -> None:
        if not self.armed:
            self._bus.publish(
                Suppressed(
                    t_ms=now_ms,
                    what=press.source,
                    reason=SuppressReason.NOT_ARMED,
                    detail=f"key={press.key}",
                )
            )
            return
        if self._focus is not None:
            ok, title = self._focus.check()
            if not ok:
                self._bus.publish(
                    Suppressed(
                        t_ms=now_ms,
                        what=press.source,
                        reason=SuppressReason.WINDOW_FOCUS,
                        detail=f"foreground={title!r}",
                    )
                )
                return
        self._sender.send(press.key)
        self.presses_sent += 1
        self._bus.publish(
            KeyPress(
                t_ms=now_ms,
                key=press.key,
                source=press.source,
                queued_ms=now_ms - press.enqueued_ms,
            )
        )

    def close(self) -> None:
        self._sender.close()


class ArmHotkey:
    """Global arm/disarm hotkey on its own thread (PLAN §8). Start disarmed.

    The callback receives no timestamp on purpose: the owner supplies a closure
    that reads its own clock, keeping this module clock-free.
    """

    def __init__(self, key_name: str, on_toggle: Callable[[], None]) -> None:
        self._combo = f"<{key_name}>"
        self._on_toggle = on_toggle
        self._listener = None

    def start(self) -> bool:
        try:
            from pynput import keyboard

            self._listener = keyboard.GlobalHotKeys({self._combo: self._on_toggle})
            self._listener.daemon = True
            self._listener.start()
            return True
        except Exception:  # noqa: BLE001 - a dead hotkey is reported, not fatal
            self._listener = None
            return False

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:  # noqa: BLE001
                pass
            self._listener = None
