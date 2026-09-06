"""MediaPipe Pose Landmarker wrapper -> PoseFrame (PLAN §4).

BlazePose GHUM lite, LIVE_STREAM mode: `detect_async` returns immediately and
results arrive on a callback thread, so inference never stalls the capture loop.
Lite is the right model here -- we need coarse limb geometry, not finger
precision, and every millisecond of inference is input lag.

Landmarks stay in MediaPipe's normalized [0,1] space the whole way through
(PLAN §16.4). Pixels appear only in overlay.py, at draw time.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from .camera import Frame
from .config import resolve_asset
from .events import EventBus, Log, ModelLoaded, Severity
from .timing import Clock

# Landmark indices used by features.py (PLAN §5).
NOSE = 0
L_SH, R_SH = 11, 12
L_ELB, R_ELB = 13, 14
L_WRI, R_WRI = 15, 16
L_HIP, R_HIP = 23, 24
L_KNEE, R_KNEE = 25, 26
L_ANK, R_ANK = 27, 28

NUM_LANDMARKS = 33

LANDMARK_NAMES = {
    NOSE: "nose",
    L_SH: "left_shoulder",
    R_SH: "right_shoulder",
    L_ELB: "left_elbow",
    R_ELB: "right_elbow",
    L_WRI: "left_wrist",
    R_WRI: "right_wrist",
    L_HIP: "left_hip",
    R_HIP: "right_hip",
    L_KNEE: "left_knee",
    R_KNEE: "right_knee",
    L_ANK: "left_ankle",
    R_ANK: "right_ankle",
}

# Hardcoded rather than imported from the legacy `mp.solutions` API, which the
# Tasks migration leaves in an uncertain state (PLAN §4 notes it already dropped
# smooth_landmarks). The 33-point topology itself is stable.
POSE_CONNECTIONS: tuple[tuple[int, int], ...] = (
    (0, 1), (1, 2), (2, 3), (3, 7), (0, 4), (4, 5), (5, 6), (6, 8), (9, 10),
    (11, 12), (11, 13), (13, 15), (15, 17), (15, 19), (15, 21), (17, 19),
    (12, 14), (14, 16), (16, 18), (16, 20), (16, 22), (18, 20),
    (11, 23), (12, 24), (23, 24),
    (23, 25), (25, 27), (27, 29), (29, 31), (27, 31),
    (24, 26), (26, 28), (28, 30), (30, 32), (28, 32),
)


@dataclass(frozen=True)
class PoseFrame:
    """One inference result, tied back to the frame it came from."""

    t_ms: float            # capture time of the source frame, session clock
    seq: int               # source frame sequence number
    present: bool          # was a body detected at all
    xy: np.ndarray         # (33, 2) normalized [0,1]
    z: np.ndarray          # (33,)   normalized depth
    visibility: np.ndarray  # (33,)   [0,1]
    latency_ms: float      # capture -> landmarks available

    def visible(self, *indices: int, threshold: float = 0.5) -> bool:
        return bool(all(self.visibility[i] >= threshold for i in indices))

    def missing(self, *indices: int, threshold: float = 0.5) -> list[str]:
        """Names of the requested landmarks that failed the visibility gate."""
        return [
            LANDMARK_NAMES.get(i, str(i))
            for i in indices
            if self.visibility[i] < threshold
        ]


def _empty_arrays() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return (
        np.zeros((NUM_LANDMARKS, 2), dtype=np.float32),
        np.zeros(NUM_LANDMARKS, dtype=np.float32),
        np.zeros(NUM_LANDMARKS, dtype=np.float32),
    )


class PoseTracker:
    """Async pose inference. `submit()` from the main loop, `latest()` to read."""

    def __init__(
        self,
        bus: EventBus,
        clock: Clock,
        model_path: str,
        *,
        num_poses: int = 1,
        min_pose_detection_confidence: float = 0.5,
        min_pose_presence_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ) -> None:
        self._bus = bus
        self._clock = clock
        # Resolve here so every caller -- app, headless, probe -- gets the
        # bundled model in a frozen build without repeating the lookup.
        self.model_path = resolve_asset(model_path)
        self._opts = {
            "num_poses": num_poses,
            "min_pose_detection_confidence": min_pose_detection_confidence,
            "min_pose_presence_confidence": min_pose_presence_confidence,
            "min_tracking_confidence": min_tracking_confidence,
        }

        self._landmarker: Any = None
        self._lock = threading.Lock()
        self._latest: PoseFrame | None = None
        self._last_ts = 0
        self._pending: dict[int, tuple[int, float]] = {}
        self.submitted = 0
        self.results = 0
        self.empty_results = 0

    def start(self) -> bool:
        if not os.path.exists(self.model_path):
            self._bus.publish(
                Log(
                    t_ms=self._clock.now_ms(),
                    severity=Severity.ERROR,
                    message=f"model not found: {self.model_path}",
                    detail="run: python scripts/fetch_model.py",
                )
            )
            return False

        # Imported here, not at module scope: mediapipe takes seconds to import
        # and emits its own noise, which would happen even for --help.
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        t0 = time.perf_counter()
        options = mp_vision.PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=self.model_path),
            running_mode=mp_vision.RunningMode.LIVE_STREAM,
            output_segmentation_masks=False,   # costs time, unused
            result_callback=self._on_result,
            **self._opts,
        )
        self._landmarker = mp_vision.PoseLandmarker.create_from_options(options)
        load_ms = (time.perf_counter() - t0) * 1000.0

        self._bus.publish(
            ModelLoaded(
                t_ms=self._clock.now_ms(),
                path=self.model_path,
                load_ms=load_ms,
            )
        )
        return True

    def submit(self, frame: Frame) -> bool:
        """Queue a frame for inference. Returns False if it could not be sent."""
        if self._landmarker is None:
            return False

        import mediapipe as mp

        # LIVE_STREAM demands strictly increasing millisecond timestamps and
        # throws on a repeat, so a fast camera that delivers two frames inside
        # one millisecond must not be allowed to collide.
        ts = int(frame.t_ms)
        if ts <= self._last_ts:
            ts = self._last_ts + 1
        self._last_ts = ts

        rgb = cv2.cvtColor(frame.image, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        with self._lock:
            self._pending[ts] = (frame.seq, frame.t_ms)
            if len(self._pending) > 120:
                for stale in sorted(self._pending)[:60]:
                    self._pending.pop(stale, None)

        try:
            self._landmarker.detect_async(image, ts)
        except Exception as exc:  # noqa: BLE001
            self._bus.publish(
                Log(
                    t_ms=self._clock.now_ms(),
                    severity=Severity.ERROR,
                    message="detect_async rejected a frame",
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
            return False
        self.submitted += 1
        return True

    def _on_result(self, result: Any, output_image: Any, timestamp_ms: int) -> None:
        """Runs on MediaPipe's callback thread -- keep it short and lock-light."""
        now_ms = self._clock.now_ms()
        with self._lock:
            seq, capture_t_ms = self._pending.pop(timestamp_ms, (0, now_ms))

        xy, z, vis = _empty_arrays()
        landmarks = getattr(result, "pose_landmarks", None) or []
        present = bool(landmarks)
        if present:
            for i, lm in enumerate(landmarks[0][:NUM_LANDMARKS]):
                xy[i, 0] = lm.x
                xy[i, 1] = lm.y
                z[i] = lm.z
                vis[i] = getattr(lm, "visibility", 0.0)

        frame = PoseFrame(
            t_ms=capture_t_ms,
            seq=seq,
            present=present,
            xy=xy,
            z=z,
            visibility=vis,
            latency_ms=now_ms - capture_t_ms,
        )
        with self._lock:
            self._latest = frame
            self.results += 1
            if not present:
                self.empty_results += 1

    def latest(self) -> PoseFrame | None:
        with self._lock:
            return self._latest

    def close(self) -> None:
        if self._landmarker is not None:
            try:
                self._landmarker.close()
            except Exception:  # noqa: BLE001 - shutdown is best effort
                pass
            self._landmarker = None

    def __enter__(self) -> "PoseTracker":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
