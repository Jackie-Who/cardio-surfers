"""Download pose_landmarker_lite.task into models/ (PLAN §16.7).

Skips the download if the file already exists, verifies non-zero size, and the
.task file is not committed. This script is outside the package, so unlike
everything in src/cardio_surfers it is allowed to print.

    python scripts/fetch_model.py
    python scripts/fetch_model.py --revision 1     # pin instead of latest
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import urllib.error
import urllib.request

URL_TEMPLATE = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_lite/float16/{revision}/pose_landmarker_lite.task"
)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_DEST = os.path.join(PROJECT_ROOT, "models", "pose_landmarker_lite.task")

# A truncated or error-page download would fail deep inside MediaPipe with a
# baffling message, so sanity-check the size here instead. The lite model is
# ~5 MB; anything under a megabyte is not a model.
MIN_BYTES = 1_000_000


def _progress(read: int, total: int) -> None:
    if total > 0:
        pct = 100.0 * read / total
        sys.stdout.write(f"\r  {read / 1e6:5.1f} / {total / 1e6:5.1f} MB  ({pct:5.1f}%)")
    else:
        sys.stdout.write(f"\r  {read / 1e6:5.1f} MB")
    sys.stdout.flush()


def fetch(dest: str, revision: str, force: bool) -> int:
    if os.path.exists(dest) and not force:
        size = os.path.getsize(dest)
        if size >= MIN_BYTES:
            print(f"already present: {dest} ({size / 1e6:.1f} MB)")
            return 0
        print(f"existing file is only {size} bytes; re-downloading")

    url = URL_TEMPLATE.format(revision=revision)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    print(f"downloading {url}")

    # Download to a temp file in the destination directory, then rename, so an
    # interrupted run cannot leave a half-written .task that looks valid.
    handle, tmp_path = tempfile.mkstemp(dir=os.path.dirname(dest), suffix=".partial")
    os.close(handle)
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            total = int(response.headers.get("Content-Length", 0))
            read = 0
            with open(tmp_path, "wb") as out:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    out.write(chunk)
                    read += len(chunk)
                    _progress(read, total)
        print()

        size = os.path.getsize(tmp_path)
        if size < MIN_BYTES:
            print(f"ERROR: downloaded only {size} bytes -- that is not the model")
            return 1

        os.replace(tmp_path, dest)
        tmp_path = ""
        print(f"saved {dest} ({size / 1e6:.1f} MB)")
        return 0
    except urllib.error.URLError as exc:
        print(f"ERROR: download failed: {exc}")
        return 1
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch the MediaPipe pose model.")
    parser.add_argument("--dest", default=DEFAULT_DEST, help="Destination path")
    parser.add_argument(
        "--revision",
        default="latest",
        help="Model revision; use '1' to pin a specific one (default: latest)",
    )
    parser.add_argument(
        "--force", action="store_true", help="Re-download even if the file exists"
    )
    args = parser.parse_args()
    return fetch(args.dest, args.revision, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
