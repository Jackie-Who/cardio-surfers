"""Threaded capture with an always-newest single-frame slot (PLAN §4).

The latency trap this exists to avoid: `VideoCapture` buffers frames, so calling
`cap.read()` in the same loop as inference means that the moment inference lags
capture you start consuming stale frames, and the lag compounds until the game
is unplayable. A dedicated grab thread overwriting one slot means the main loop
always gets the newest frame and the stale ones are dropped on the floor, which
is what `frames_dropped` counts.

No print() here: failures publish `Log` onto the bus (PLAN §16.2).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from .events import CameraOpened, EventBus, Log, Severity
from .timing import Clock

# DirectShow, at a native 16:9 mode. Measured on the dev camera: 640x360 keeps
# the sensor's full field of view (0.999 correlation with a downscaled 720p
# frame) and opens in under a second, while 1280x720 arrives uncompressed at
# 10 fps and MSMF -- which does negotiate 720p at 30 -- took 8 s to open and
# hung the app at launch. Asking for 640x480 is what crops the sensor.
BACKENDS = {
    "dshow": cv2.CAP_DSHOW,
    "msmf": cv2.CAP_MSMF,
    "any": cv2.CAP_ANY,
}


@dataclass(frozen=True)
class Frame:
    """One captured frame, already mirrored."""

    image: np.ndarray   # BGR, mirrored so your left is screen-left
    seq: int
    t_ms: float         # capture time on the session clock


class CameraThread:
    """Grabs frames as fast as the device allows into a single latest slot."""

    def __init__(
        self,
        bus: EventBus,
        clock: Clock,
        index: int = 0,
        width: int = 640,
        height: int = 480,
        fps: float = 30.0,
        backend: str = "dshow",
        flip: bool = True,
        process_width: int | None = None,
        backend_fallback: bool = False,
    ) -> None:
        self._bus = bus
        self._clock = clock
        self.index = index
        self.width = width
        self.height = height
        self.fps = fps
        self.backend = backend
        self.flip = flip
        # Safety cap: if a driver hands back something wider than requested,
        # downscale here so the rest of the pipeline stays cheap.
        self.process_width = process_width
        self.backend_fallback = backend_fallback

        self._cap: cv2.VideoCapture | None = None
        self._lock = threading.Lock()
        self._latest: Frame | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._seq = 0
        self._last_delivered = 0
        self.frames_captured = 0
        self.frames_dropped = 0   # captured but superseded before anyone looked
        self.read_failures = 0

        self.actual_width = 0
        self.actual_height = 0
        self.actual_fps = 0.0

    # -- lifecycle -----------------------------------------------------------

    def open(self) -> bool:
        api = BACKENDS.get(self.backend, cv2.CAP_DSHOW)
        cap = cv2.VideoCapture(self.index, api)
        if not cap.isOpened():
            cap.release()
            # No cross-backend retry by default. Measured on this machine: when
            # DirectShow refuses a busy camera, opening the same device over
            # MSMF took 55 s and then delivered no frames. Trying the *next*
            # camera on the fast backend is both quicker and more likely to
            # work, so `start_any` does that instead.
            if not self.backend_fallback:
                self._bus.publish(
                    Log(
                        t_ms=self._clock.now_ms(),
                        severity=Severity.WARN,
                        message=f"{name_of(self.index)} would not open",
                        detail="usually another app is using it",
                    )
                )
                return False
            other = "dshow" if self.backend != "dshow" else "msmf"
            self._bus.publish(
                Log(
                    t_ms=self._clock.now_ms(),
                    severity=Severity.WARN,
                    message=f"{name_of(self.index)} refused {self.backend}, trying {other}",
                    detail="this can take a long time",
                )
            )
            cap = cv2.VideoCapture(self.index, BACKENDS[other])
            if cap.isOpened():
                self.backend = other
            else:
                cap.release()
                self._bus.publish(
                    Log(
                        t_ms=self._clock.now_ms(),
                        severity=Severity.ERROR,
                        message=f"{name_of(self.index)} would not open",
                        detail="try --probe, or pick another camera in Settings",
                    )
                )
                return False

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        # Ask the driver for a shallow buffer too; the grab thread is what
        # actually guarantees freshness, but this reduces what it has to discard.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        self._cap = cap
        self.actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.actual_fps = float(cap.get(cv2.CAP_PROP_FPS))

        self._bus.publish(
            CameraOpened(
                t_ms=self._clock.now_ms(),
                index=self.index,
                backend=self.backend,
                width=self.actual_width,
                height=self.actual_height,
                fps_requested=self.fps,
                fps_actual=self.actual_fps,
            )
        )
        if self.actual_width and self.actual_height:
            wanted = self.width / self.height
            got = self.actual_width / self.actual_height
            if abs(wanted - got) > 0.05:
                self._bus.publish(
                    Log(
                        t_ms=self._clock.now_ms(),
                        severity=Severity.WARN,
                        message=f"camera {self.index} negotiated {self.actual_width}x"
                        f"{self.actual_height}, not the {self.width}x{self.height} "
                        "requested",
                        detail="field of view may be cropped; try another camera.width/height",
                    )
                )
        return True

    def start_any(self, max_index: int, first_frame_timeout_s: float) -> bool:
        """Start on the configured camera, or the first other one that works.

        A saved camera can be busy -- another app has it, or it was unplugged.
        Failing to start at all is a worse answer than quietly using the webcam
        that *is* free and saying which one it picked.
        """
        if self.start() and self.wait_for_frame(first_frame_timeout_s):
            return True
        self.stop()
        wanted = self.index
        for candidate in range(max_index):
            if candidate == wanted:
                continue
            self.index = candidate
            if self.start() and self.wait_for_frame(first_frame_timeout_s):
                self._bus.publish(
                    Log(
                        t_ms=self._clock.now_ms(),
                        severity=Severity.WARN,
                        message=f"{name_of(wanted)} unavailable; using "
                                f"{name_of(candidate)}",
                        detail="pick a different one in Settings to make it stick",
                    )
                )
                return True
            self.stop()
        self.index = wanted
        return False

    def start(self) -> bool:
        if self._cap is None and not self.open():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def wait_for_frame(self, timeout_s: float) -> bool:
        """Block until a frame actually arrives, or the timeout expires.

        Opening a device is not the same as it working: some cameras open
        instantly and take a second to deliver anything, and some open and never
        deliver at all. Only a real frame settles it.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._lock:
                if self._latest is not None:
                    return True
            time.sleep(0.02)
        return False

    def switch(self, index: int, first_frame_timeout_s: float) -> bool:
        """Reopen on a different camera index.

        Success means the new camera delivered a frame, not merely that it
        opened -- otherwise cycling can strand you on a black preview. On
        failure the previous camera is restored rather than leaving the app with
        no camera at all: cycling past a dead index should be a pause, not the
        end of the session.
        """
        previous = self.index
        self.stop()
        with self._lock:
            # Drop the old camera's last frame so the HUD cannot show a stale
            # image from a device that is no longer open.
            self._latest = None
        self.index = index
        if self.start() and self.wait_for_frame(first_frame_timeout_s):
            return True

        self.stop()
        with self._lock:
            self._latest = None
        self.index = previous
        self.start()
        return False

    def __enter__(self) -> "CameraThread":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- grab thread ---------------------------------------------------------

    def _run(self) -> None:
        cap = self._cap
        if cap is None:
            return
        consecutive_failures = 0
        while not self._stop.is_set():
            ok, image = cap.read()
            if not ok or image is None:
                self.read_failures += 1
                consecutive_failures += 1
                if consecutive_failures == 30:
                    self._bus.publish(
                        Log(
                            t_ms=self._clock.now_ms(),
                            severity=Severity.ERROR,
                            message="camera stopped delivering frames",
                            detail=f"{consecutive_failures} consecutive failed reads",
                        )
                    )
                self._stop.wait(0.01)
                continue
            consecutive_failures = 0

            if self.flip:
                # Mirror: your left must be screen-left, or every lane is inverted.
                image = cv2.flip(image, 1)
            if self.process_width and image.shape[1] > self.process_width:
                h = int(round(image.shape[0] * self.process_width / image.shape[1]))
                image = cv2.resize(image, (self.process_width, h), interpolation=cv2.INTER_AREA)

            self._seq += 1
            self.frames_captured += 1
            frame = Frame(image=image, seq=self._seq, t_ms=self._clock.now_ms())
            with self._lock:
                if self._latest is not None and self._latest.seq > self._last_delivered:
                    self.frames_dropped += 1
                self._latest = frame

    # -- consumer side -------------------------------------------------------

    def read(self) -> Frame | None:
        """Newest frame, or None if nothing new since the last call."""
        with self._lock:
            frame = self._latest
            if frame is None or frame.seq == self._last_delivered:
                return None
            self._last_delivered = frame.seq
            return frame

    def peek(self) -> Frame | None:
        """Newest frame whether or not it has been delivered (for the HUD)."""
        with self._lock:
            return self._latest

    def describe(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "backend": self.backend,
            "width": self.actual_width,
            "height": self.actual_height,
            "fps": self.actual_fps,
            "captured": self.frames_captured,
            "dropped": self.frames_dropped,
            "read_failures": self.read_failures,
        }


def name_of(index: int, names: list[str] | None = None) -> str:
    """Friendly name for a camera index, or a plain fallback."""
    if names is None:
        names = device_names()
    if 0 <= index < len(names) and names[index]:
        return names[index]
    return f"Camera {index}"


def device_names() -> list[str]:
    """Real DirectShow device names, in the same order OpenCV indexes them.

    pygrabber walks the DirectShow capture-device category, which is exactly
    the enumeration CAP_DSHOW indexes into -- so position N in this list is
    camera index N. Falls back to an empty list rather than guessing.
    """
    try:
        from pygrabber.dshow_graph import FilterGraph

        return list(FilterGraph().get_input_devices())
    except Exception:  # noqa: BLE001 - names are a nicety, never fatal
        return []


def enumerate_cameras(
    max_index: int = 6, backend: str = "dshow", skip: int | None = None
) -> list[dict[str, Any]]:
    """Probe indices 0..max_index-1. Used by --probe (PLAN §11).

    Opening a camera is slow and noisy, so this is deliberately not called
    anywhere on the normal startup path.
    """
    api = BACKENDS.get(backend, cv2.CAP_DSHOW)
    names = device_names()
    found: list[dict[str, Any]] = []
    for idx in range(max_index):
        if idx == skip:
            # The camera the app currently holds: probing it would fail (the
            # device is busy), so report it as present without opening it.
            found.append({"index": idx, "opened": True, "reads": True,
                          "width": 0, "height": 0, "fps": 0.0, "in_use": True,
                          "name": name_of(idx, names)})
            continue
        cap = cv2.VideoCapture(idx, api)
        try:
            if not cap.isOpened():
                continue
            ok, image = cap.read()
            found.append(
                {
                    "index": idx,
                    "name": name_of(idx, names),
                    "opened": True,
                    "reads": bool(ok and image is not None),
                    "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                    "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                    "fps": float(cap.get(cv2.CAP_PROP_FPS)),
                }
            )
        finally:
            cap.release()
    return found
