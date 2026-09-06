# Notices

Cardio Surfers is released under the [MIT License](LICENSE).

This file records the third-party terms that come with it. It is a summary
written in good faith, not legal advice.

## Trademarks

**Subway Surfers** is a trademark of SYBO Games ApS. This project is an
independent, unofficial input device: it reads a webcam and presses keyboard
keys. It contains no game code, art, or assets, is not affiliated with or
endorsed by SYBO Games or Kiloo, and works with any game that takes keyboard
input.

## Runtime dependencies

| Package | Licence | Notes |
|---|---|---|
| [MediaPipe](https://github.com/google-ai-edge/mediapipe) | Apache 2.0 | Pose estimation |
| [OpenCV](https://github.com/opencv/opencv-python) (`opencv-contrib-python`) | Apache 2.0 | Capture, drawing, window |
| [NumPy](https://numpy.org/) | BSD 3-Clause | Arrays throughout |
| [Pillow](https://python-pillow.org/) | MIT-CMU (HPND) | TrueType text rendering |
| [sounddevice](https://github.com/spatialaudio/python-sounddevice) | MIT | Beep playback and device selection |
| [pydirectinput-rgx](https://github.com/ReggX/pydirectinput_rgx) | MIT | SendInput scancode injection |
| [pygrabber](https://github.com/andreaschiavinato/python_grabber) | MIT | Real DirectShow camera names |
| [PyGetWindow](https://github.com/asweigart/pygetwindow) | BSD 3-Clause | Foreground window title |
| **[pynput](https://github.com/moses-palmer/pynput)** | **LGPL v3** | Global F8 hotkey — see below |

### The pose model

`pose_landmarker_lite.task` is Google's BlazePose GHUM Lite model, distributed
under Apache 2.0 and covered by the
[MediaPipe model terms](https://developers.google.com/mediapipe/solutions/vision/pose_landmarker).
It is **not committed to this repository** — `scripts/fetch_model.py`
downloads it — but `scripts/build_exe.py` does bundle it into the `.exe`.

### pynput and the bundled executable

pynput is **LGPL v3**. The LGPL is written to allow software under other
licences to use it, so it does not change this project's MIT licence. It does
ask that anyone receiving a binary be able to swap in their own build of the
library. If you distribute `CardioSurfers.exe`:

- keep this file with it, so recipients know pynput is inside and where to get
  its source, and
- note that the project can be rebuilt from source with a modified pynput via
  `scripts/build_exe.py`.

If you would rather not carry that obligation, pynput is used for exactly one
thing — the global F8 arm/disarm hotkey in `keys.py`. Removing it costs you
that hotkey; the on-screen Arm button, `Space`, and the T-pose all still work.

## Build dependency

[PyInstaller](https://pyinstaller.org/) is GPL v2-or-later **with a linking
exception** that explicitly permits building and distributing programs under
any licence, including proprietary ones. It is a build tool only and none of
its code beyond the permitted bootloader ships in the executable.

## Fonts

The interface renders with **Segoe UI**, a Microsoft font already present on
Windows. It is used from the system font directory and is **not** redistributed
in this repository or in the executable. On a machine without it, the renderer
falls back to Arial and then to DejaVu Sans.
