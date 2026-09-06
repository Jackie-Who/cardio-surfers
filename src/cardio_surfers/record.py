"""Record raw landmarks to JSONL; replay them through the identical pipeline
(PLAN §12.1).

`--replay recording.jsonl -vvv` is the core tuning workflow: change a
threshold, replay the same fixture, diff the events. No webcam, fully
deterministic -- the pipeline receives the recorded timestamps as `now_ms`,
which is the whole point of clock injection (PLAN §16.1).
"""

from __future__ import annotations

import json
from typing import Iterator, TextIO

import numpy as np

from .pose import NUM_LANDMARKS, PoseFrame


class PoseRecorder:
    """Writes one JSON object per pose frame. Cheap enough for the hot path."""

    def __init__(self, path: str) -> None:
        self._file: TextIO = open(path, "w", encoding="utf-8")
        self.frames = 0

    def record(self, pose: PoseFrame) -> None:
        row = {
            "t": round(pose.t_ms, 3),
            "seq": pose.seq,
            "present": pose.present,
            # Rounded: 4 decimals in [0,1] space is ~0.06 mm at 2 m -- far
            # below landmark noise -- and it halves the file size.
            "xy": [[round(float(v), 4) for v in p] for p in pose.xy],
            "vis": [round(float(v), 3) for v in pose.visibility],
        }
        self._file.write(json.dumps(row, separators=(",", ":")) + "\n")
        self.frames += 1

    def close(self) -> None:
        self._file.close()


def iter_recording(path: str) -> Iterator[PoseFrame]:
    """Yields PoseFrames exactly as the live pose tracker would have."""
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            xy = np.array(row["xy"], dtype=np.float32).reshape(NUM_LANDMARKS, 2)
            vis = np.array(row["vis"], dtype=np.float32)
            yield PoseFrame(
                t_ms=float(row["t"]),
                seq=int(row["seq"]),
                present=bool(row["present"]),
                xy=xy,
                z=np.zeros(NUM_LANDMARKS, dtype=np.float32),
                visibility=vis,
                latency_ms=0.0,
            )
