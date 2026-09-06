"""Build the single-file Windows executable.

    python scripts/build_exe.py            # build
    python scripts/build_exe.py --clean    # rebuild from scratch

Outputs `dist/CardioSurfers.exe`. This script is outside the package, so unlike
everything in src/cardio_surfers it is allowed to print.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SPEC = os.path.join(ROOT, "cardio_surfers.spec")
MODEL = os.path.join(ROOT, "models", "pose_landmarker_lite.task")
DIST = os.path.join(ROOT, "dist")
EXE = os.path.join(DIST, "CardioSurfers.exe")


def check_prerequisites() -> bool:
    ok = True
    if sys.version_info[:2] != (3, 12):
        print(f"WARNING: building on Python {sys.version_info.major}."
              f"{sys.version_info.minor}; mediapipe wheels target 3.12")
    if not os.path.exists(MODEL):
        print("ERROR: the pose model is missing. It is not in the wheel and is")
        print("       not committed, so fetch it before building:")
        print("         python scripts/fetch_model.py")
        ok = False
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("ERROR: PyInstaller is not installed. Run:")
        print("         python -m pip install -r requirements-dev.txt")
        ok = False
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Build CardioSurfers.exe")
    parser.add_argument("--clean", action="store_true",
                        help="delete build/ and dist/ first")
    args = parser.parse_args()

    if not check_prerequisites():
        return 1

    if args.clean:
        for folder in ("build", "dist"):
            path = os.path.join(ROOT, folder)
            if os.path.isdir(path):
                shutil.rmtree(path)
                print(f"removed {folder}/")

    print("building -- this takes a few minutes the first time")
    started = time.time()
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", SPEC],
        cwd=ROOT,
    )
    if result.returncode != 0:
        print("build FAILED")
        return result.returncode

    if not os.path.exists(EXE):
        print(f"build reported success but {EXE} is missing")
        return 1

    size_mb = os.path.getsize(EXE) / 1e6
    print(f"\nbuilt {EXE}")
    print(f"  {size_mb:.0f} MB, in {time.time() - started:.0f}s")
    print("\nShip the .exe on its own. It writes config.json beside itself,")
    print("so put it somewhere writable (not Program Files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
