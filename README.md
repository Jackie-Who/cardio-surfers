# Cardio Surfers

**Play Subway Surfers by actually running.**

Your webcam watches you jog on the spot. Step left and right to switch lanes,
hop to jump, crouch to duck. If you stop running, your controls stop working —
so there is no way to play sitting down.

Free, works with the browser version of the game, and needs nothing but a
webcam.

---

# Getting started

## What you need

- A **Windows 10 or 11** PC
- A **webcam** (a laptop's built-in one is fine)
- Room to stand about **1-2 metres back** from it
- The game: **[subwaygame.io](https://subwaygame.io/)** — recommended, because
  it has a proper full-screen button

---

# First-time setup

You only do this once. It takes about two minutes.

## 1. Download the app

Go to the **[Downloads page](https://github.com/Jackie-Who/cardio-surfers/releases/latest)**
and download **CardioSurfers.exe**.

Save it somewhere like your **Desktop** or **Documents**. Do not put it in
`Program Files` — the app saves your settings next to itself, and Windows
blocks that there.

There is nothing to install. It is one file.

## 2. Open it

Double-click **CardioSurfers.exe**.

Windows will probably show a blue box saying *"Windows protected your PC"*.
That is normal for any app that has not paid for a certificate:

1. Click **More info**
2. Click **Run anyway**

A window called *Cardio Surfers* opens, with a black text window behind it.
Leave the black one alone — it only shows messages if something goes wrong.

## 3. Check the camera can see you

You should see yourself in the preview on the right.

Seeing a black box, or the wrong camera?

1. Click **Settings**
2. Click the box under **Camera**
3. Pick your webcam from the list by name
4. Click **Back**

Now stand so that:

- You are **1-2 metres** from the camera
- Your **head and shoulders** fill the top part of the picture
- The light is **in front of you** — a bright window behind you makes you hard
  to see

## 4. Calibrate

The app needs to learn how tall you are and how you move. About 45 seconds.

1. Click **Calibrate**
2. Follow the six prompts. Each counts down, then asks you to hold still:

   | It asks you to | What to do |
   |---|---|
   | Stand centred | Stand normally, facing the camera |
   | Step to your left | One comfortable side-step, then hold |
   | Step to your right | One comfortable side-step, then hold |
   | Jump | One jump straight up |
   | Duck down and hold | Crouch as low as you would while playing |
   | Jog in place | Jog steadily, at the pace you will really play at |

3. When it finishes, the label at the top says **CALIBRATED**

If it asks you to repeat a step, it simply could not see enough movement —
step further, jump higher or crouch lower, and it carries on.

---

# Every time you play

## 1. Open the game

Go to **[subwaygame.io](https://subwaygame.io/)** and put it in **full screen**.

## 2. Open Cardio Surfers

Double-click the app. If you have already calibrated, it is ready to go.

## 3. Click Play, then "Overlay on game"

The app shrinks to a small see-through panel floating on top of your game. The
game keeps the whole screen; you still see your camera, whether you are
running, and which keys are firing.

## 4. T-pose, then run

Stand back and hold a **T-pose** — stand up straight with both arms straight
out to the sides, like the letter T. Hold it for about a second.

That one gesture starts the game *and* hands the controls to your body. Start
jogging straight away.

To stop, click the **X** on the little panel.

---

# How to play

Once you are running, your body is the controller:

| To do this | Do this |
|---|---|
| **Move left or right** | Step sideways |
| **Jump** | Hop up |
| **Duck / roll** | Crouch down |
| **Keep playing** | Keep jogging on the spot |

**You have to keep jogging.** If you slow down or stop:

1. The bar turns amber and beeps — this is your warning, keep moving
2. A few seconds later it turns red and says **Locked** — your controls stop
   working until you start jogging again

That is the whole point of the app. You cannot stand still and play.

## More about the overlay panel

The floating panel is deliberately tiny, and everywhere except the panel itself
is click-through, so it never gets in the way of the game.

A coloured border around the edge of the screen tells you how you are doing
without you having to look away from the game:

- **Green** — running, all good
- **Amber** — slow down warning, speed up
- **Red** — locked, start jogging

The panel has an **Arm / Disarm** button and an **X** to close it. You can move
it to any corner: **Settings -> Overlay HUD position**.

> The overlay works over **windowed** and **borderless** full screen, which is
> how browser games run — including subwaygame.io's full-screen button. It
> cannot draw on top of a game using true exclusive full screen.

You do not have to use it. The game and the app side by side works too, it is
just smaller.

## Making it easier or harder

**Settings → drag the lines on the preview.**

There are two coloured lines across the camera picture:

- The **green JUMP line** — your shoulders have to go above it to jump
- The **amber DUCK line** — your shoulders have to go below it to duck

Drag them with your mouse, or use the **−** and **+** buttons. Move the jump
line up to make jumping harder, move the duck line down to make ducking
harder.

## Other settings

| Setting | What it does |
|---|---|
| **Camera** | Pick which webcam to use, by name |
| **Sound output** | Pick which speakers or headphones the warning beeps play through |
| **Beep volume** | How loud the warnings are, with a Test button |
| **Key layout** | WASD (default) or arrow keys — match whatever your game uses |
| **Overlay HUD position** | Which corner the floating panel sits in |

## Buttons and keys

- **X** on the window — closes the app
- **Back arrow** (top left) or **Esc** — goes back a screen
- **Space** or **F8** — arms and disarms, if you would rather not T-pose
- **Arm / Disarm** button — same thing, on screen

---

# If something is not working

**"It cannot see me"**
Stand 1–2 metres back so your head and shoulders fill the top of the picture.
Turn on a light in front of you and avoid having a bright window behind you.

**"Nothing happens when I move"**
Check it says **Armed**. Hold the T-pose, or press Space. Also make sure you
clicked on the game window — key presses go to whatever window you last
clicked.

**"It says Locked straight away"**
You need to be jogging. If it locks while you genuinely are jogging, your
calibration may not match how you actually play — run **Calibrate** again and
jog at your real playing pace during the last step.

**"It jumps when I did not mean to"**
Your jump line is too low. **Settings** → drag the green line higher.

**"The wrong camera opens"**
**Settings → Camera** and pick yours by name. It remembers your choice.

**"My camera is already in use"**
Close anything else using it — Zoom, Teams, OBS. The app will tell you which
camera it could not open, and will fall back to another one if it finds a
working one.

**Want to see exactly what it is detecting?**
Click **Debug** from the menu. It shows every measurement live, and it never
sends any key presses, so it is safe to experiment in.

---

*Everything below is for people who want to know how it works, change it, or build it themselves. You do not need any of it to play.*

# Technical details

## How movement becomes key presses

MediaPipe BlazePose tracks 33 body landmarks at 30 fps from a threaded camera
grabber that always takes the newest frame, so inference lag never compounds.
Everything downstream is a pure function of `(features, previous_state,
now_ms)` with the clock injected, which is what makes `--replay` deterministic.

**Two fixed lines on the screen.** A green **JUMP line** and an amber **DUCK
line** sit at set heights in the camera view. Your shoulder line crossing the
JUMP line presses `W`; crossing the DUCK line presses `S`. The lines are
absolute screen positions, not offsets from your body, so stepping closer,
leaning or slouching never moves them.

- **Lanes** — a full-height **torso centre line** (your shoulder midpoint)
  against two calibrated boundaries. The third you are standing in is tinted
  green, and the tint flashes brighter as you cross into a new one. Each
  boundary is wrapped in a hysteresis band sized from your measured jogging
  sway; if you sway more than narrow lanes can absorb, the band is clamped to
  fit and calibration says so instead of refusing. Changing lanes emits
  `|delta|` presses: centre-to-right is one `D`, left-to-right is two, spaced
  at least 110 ms apart so web ports register both.
- **Jump / duck** — calibration places the two lines from your own jump height
  and duck depth, measured against your *jogging* posture. A refractory window
  blocks double-fires, and holding past a line longer than a gesture can last
  (0.8 s airborne, 1.5 s ducked) releases the state and waits for you to come
  back between the lines.
- **Running** — the **shoulder bounce**. Jogging dips your shoulders once per
  foot strike; a lightly filtered shoulder height is detrended by a short
  rolling mean, and a Schmitt trigger with mandatory alternation counts one
  step per dip. A step registers at 30% of your calibrated bounce. Cadence
  below 50% of your calibrated jog for 1.5 s gives the warning; 3 s more and it
  locks, dropping every input until you jog for a second above the floor.

**The cadence bar never opens locked.** Arming primes the window as if you were
already at your floor. When a jump or duck fires, the reported cadence is
pinned to its last value for a second while the detector is paused, so landing
and recovering cannot drain the bar into a lockout.

Every decision *not* to press a key is logged with a reason (`GATE_LOCKED`,
`NOT_ARMED`, `WINDOW_FOCUS`, `REFRACTORY`, `HYSTERESIS`, …) — run with `-vvv`
or watch the Debug feed.

## Key delivery and safety

Keys go to the **foreground window**, whatever it is, with one hard exception:
the controller's own window is always excluded, so it can never type into
itself. Set `keys.focus_regex` to something like `(?i)subway|chrome` to
restrict delivery to matching titles. The app starts **disarmed**; `--dry-run`
makes Play publish events without touching the OS.

Injection is `SendInput` with scancodes via `pydirectinput-rgx`. To watch
presses land without the game, open `tools/keytest.html` in a browser tab — it
logs each `event.code` with timing, which is how the double-press gap was
tuned.

## Camera

Opened at **640×360**, a native 16:9 mode on nearly every webcam. Measured on
the development camera it keeps the sensor's full field of view (0.999
correlation with a downscaled 720p frame) and opens in under a second; asking
for 640×480 makes drivers *crop* the sensor. 720p over DirectShow arrives
uncompressed at 10 fps, and the Media Foundation backend took 8 s per open, so
neither is the default — both remain available via `camera.width/height` and
`camera.backend`.

If the saved camera is busy, the app tries the next working one rather than
hanging, and names what it picked.

## The overlay

A Win32 layered window: colour-keyed pixels are both invisible and
click-through, `WS_EX_NOACTIVATE` stops the panel stealing keyboard focus, and
the window is kept topmost. Focus is handed back to the game on entering
overlay mode and after every click on the HUD — Windows only permits that
handover from a process that currently holds focus, so the app can give focus
away but can never steal it back.

## Interface

One dark, flat surface: a near-black ground, one card step, hairline borders,
and four accents that each mean exactly one thing — green for go/armed/jump,
amber for warning/duck, red for locked/disarmed, blue for inert. Text is real
**Segoe UI** rendered through Pillow, not OpenCV's single-stroke Hershey font.
Every colour and size comes from `theme.py`, every rectangle from `layout.py`,
so the window resizes cleanly and nothing is a hardcoded pixel. Contrast is
checked in the test suite against WCAG AA.

## Running from source

Python **3.12** (mediapipe has no 3.13+ wheels).

```bash
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python scripts\fetch_model.py
.venv\Scripts\cardio-surfers
```

## Building the .exe

```bash
.venv\Scripts\python scripts\build_exe.py --clean
```

Produces `dist/CardioSurfers.exe`, about 112 MB, bundling OpenCV, MediaPipe
and the pose model. Two things the spec exists to get right, both of which
fail only at runtime: MediaPipe's `vision` package imports matplotlib at load
time, so it cannot be excluded however tempting its size is; and PyInstaller
runs its entry script as a top-level module, so `launcher.py` uses an absolute
import rather than the package's own `__main__.py`.

Pushing a `v*` tag builds and publishes a release automatically
(`.github/workflows/release.yml`).

## Command line

```
cardio-surfers                   the windowed app
  --probe                        setup self-test (cameras, fps, visibility, focus)
  --recalibrate                  open straight into calibration
  --camera N                     camera override
  --dry-run                      never send real keys
  --no-preview                   headless console pipeline
  --record PATH / --replay PATH  record landmarks / replay deterministically
  -v / -vv / -vvv                console verbosity
  --log-file PATH                JSONL log of every event
  --log-session                  session stats JSON on exit
```

Tuning loop: `--replay tests/fixtures/synthetic_run.jsonl -vvv` — change a
threshold in config, replay, diff the events. Bit-for-bit deterministic.

## Tests

```bash
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
```

160 tests: pure-FSM suites for lanes, vertical, running and features driven by
synthetic sequences on a manual clock; layout geometry at eight window sizes;
WCAG contrast on the palette; telemetry sink behaviour; config merging; and
AST scans enforcing two invariants — no `print()` outside `telemetry.py`, and
no clock reads inside the FSMs.

## Module layout

```
src/cardio_surfers/
  main.py          CLI: app / headless / replay / probe
  app.py           the windowed shell (menu, play, debug, calibrate, settings)
  pipeline.py      features -> FSMs -> gate -> press queue -> dispatcher
  camera.py        threaded capture, always-newest-frame slot, live switching
  pose.py          MediaPipe wrapper -> PoseFrame
  features.py      torso line / shoulder line / shoulder bounce, One Euro filtered
  filters.py       One Euro (inline, ~30 lines) + EMA fallback
  lanes.py         LaneFSM               <- pure, unit-tested
  vertical.py      VerticalFSM           <- pure, unit-tested
  running.py       RunDetector + GateFSM <- pure, unit-tested
  arming.py        T-pose detector
  keys.py          PressQueue, focus gate, SendInput sender, F8 hotkey
  calibrate.py     guided 6-step session -> config.json
  events.py        event dataclasses, SuppressReason, EventBus
  telemetry.py     threaded console + JSONL sink <- the only module that prints
  timing.py        clock injection + rolling stats <- the only module reading a clock
  overlay.py       drawing primitives    <- the only module that uses pixels
  typography.py    Segoe UI text rendering with a glyph cache
  theme.py         design tokens: colour, type, spacing
  layout.py        proportional rects for a resizable window
  game_overlay.py  Win32 layered click-through overlay
  audio.py         warning beeps via sounddevice, with device selection
  stats.py         session counters + summary
  record.py        JSONL record / replay
  config.py        config merging; the only source of tunables
  probe.py         --probe self-test
```

See [docs/PLAN.md](docs/PLAN.md) for the original spec and [CLAUDE.md](CLAUDE.md) for the
invariants and how the design changed along the way.

---

## Licence

MIT — see [LICENSE](LICENSE). Third-party terms, the pose model's licence and a
note on redistributing the built `.exe` are in [NOTICE.md](NOTICE.md).

**Subway Surfers** is a trademark of SYBO Games ApS. This project is an
unofficial input device with no affiliation to, or endorsement by, SYBO Games.
It ships no game code or assets and works with anything that takes keyboard
input.
