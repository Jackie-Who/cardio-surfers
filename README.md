# Cardio Surfers

Webcam pose tracking → lane / jump / duck / running detection → Win32 key
injection into a browser running Subway Surfers. **You jog in place (arms
pumping) the whole time; stop moving and your inputs stop working.**

Built for **desk range**: stand 1–2 m from an ordinary webcam with your
shoulders in the upper third of the frame. Side-to-side is your torso centre
line, jump/duck is your shoulder line crossing the drawn threshold lines, and
running is the **bounce of your shoulders** as you jog — no arm swing, no knees.

See [PLAN.md](PLAN.md) for the original spec and [CLAUDE.md](CLAUDE.md) for
the invariants and the v2 revisions.

---

## Setup

Python **3.12** (mediapipe has no 3.13+ wheels).

```bash
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python scripts\fetch_model.py
```

## Run

```bash
.venv\Scripts\cardio-surfers
```

One window, five screens:

| Screen | What it does |
|---|---|
| **Menu** | Play / Debug / Calibrate / Settings / Quit, plus a live camera thumbnail |
| **Play** | The real thing. Deliberately sparse: the video with its lines, the four key pads, cadence, and armed/gate state. Nothing else to read mid-dodge |
| **Debug** | Everything in one window: lane zones with your live position, jump/duck threshold lines, dy and arm-swing sparklines, key pads that light up per press, cadence vs floor, gate state, and the suppression feed. **Dry-run** — nothing is ever sent to the OS |
| **Calibrate** | 6 guided steps (~45 s) → writes `config.json`. Until you run it, lanes fall back to screen thirds |
| **Settings** | **Camera picker** and **sound-output picker**, both by real device name; **draggable JUMP/DUCK lines** on the live preview; **beep volume** with a test button; key layout |

The window is **resizable** (drag any edge; it will not go below 900x560). The **X** closes the app, the **back arrow** and `ESC` go back, and `SPACE` or `F8` arms in Play.

## How movement becomes key presses

**Two fixed lines on the screen.** A green **JUMP line** and an amber **DUCK
line** sit at set heights in the camera view. Your shoulder line crossing the
JUMP line presses `W`; crossing the DUCK line presses `S`. That is the entire
vertical model -- the lines are screen positions, not offsets from your body,
so stepping closer, leaning or slouching never moves them.

- **Lanes** -- a full-height **torso centre line** (your shoulder midpoint)
  against two calibrated boundaries. The third you are standing in is tinted
  green, and the tint flashes brighter for a moment as you cross into a new
  one, so a lane change reads as an event rather than only a moved line. Each
  boundary is wrapped in a hysteresis band sized from your measured jogging
  sway. Compact spaces are fine: if you sway more
  than narrow lanes can absorb, the band is clamped to fit and calibration
  says so instead of refusing. Changing lanes emits `|delta|` presses:
  centre-to-right is one `D`, left-to-right is two, spaced at least 110 ms
  apart so web ports register both.
- **Jump / duck** -- calibration places the two lines from your own jump
  height and duck depth, measured against your *jogging* posture; Settings
  lets you drag them anywhere afterwards. Raise the jump line to make jumps
  harder, lower the duck line to make ducks harder. A refractory window blocks
  double-fires, and holding past a line longer than a gesture can last (0.8 s
  airborne, 1.5 s ducked) releases the state and waits for you to come back
  between the lines.
- **Running** -- the **shoulder bounce**. Jogging on the spot dips your
  shoulders once per foot strike; a lightly filtered shoulder height is
  detrended by a short rolling mean, and a Schmitt trigger with mandatory
  alternation counts one step per dip. A step registers at 30% of your
  calibrated bounce, so a relaxed jog still counts. Cadence below 50% of your
  calibrated jog for 1.5 s gives the **KEEP MOVING** warning (beeps); 3 s more
  and it **LOCKS**, dropping every input until you jog for a second above the
  floor. **For one second after any jump or duck the running detector is
  paused entirely** -- not fed, half-cycle forgotten, and its cadence window's
  clock stopped, so the bar does not sag while you land and recover.

**Arming: hold a T-pose.** Stand straight with both arms out level for about
0.7 s and the controller arms itself **and sends the game's START key** (space),
so one gesture both starts the run and hands you the controls. F8 and Space
still toggle, and there is an Arm button.

**The cadence bar never opens locked.** Arming primes the window as if you were
already at your floor, so the bar starts full and only drains once you actually
fall behind; before arming the gate is frozen entirely. When a jump or duck
fires, the reported cadence is **pinned to its last value for a second** while
the detector is paused, so landing and recovering cannot drain the bar into a
lockout. The bar shows `HELD` while that is happening.

Every decision *not* to press a key is logged with a reason
(`GATE_LOCKED`, `NOT_ARMED`, `WINDOW_FOCUS`, `REFRACTORY`, `HYSTERESIS`, …) —
run with `-vvv` or watch the debug feed.

## Building a standalone .exe

```bash
.venv\Scripts\python scriptsetch_model.py
.venv\Scripts\python scriptsuild_exe.py --clean
```

Produces `dist/CardioSurfers.exe` -- about 112 MB, one file, no Python needed
on the target machine. It bundles OpenCV, MediaPipe and the pose model, so it
runs on a clean Windows box.

**Put it somewhere writable** (your desktop, not `Program Files`). It writes
`config.json` beside itself, which is where your calibration and camera choice
live. The console window is deliberate: this app depends on cameras, audio
devices and a pose model, and when one of those is missing the console message
is the whole diagnosis. Flip `console=False` in `cardio_surfers.spec` for a
silent build.

Two things the spec exists to get right, both of which fail only at runtime:
MediaPipe's `vision` package imports matplotlib at load time, so it cannot be
excluded however tempting its size is; and PyInstaller runs its entry script
as a top-level module, so `launcher.py` uses an absolute import rather than
the package's own `__main__.py`.

## Interface

One dark, flat surface: a near-black ground, one card step, hairline borders,
and four accents that each mean exactly one thing -- green for go/armed/jump,
amber for warning/duck, red for locked/disarmed, blue for inert. Text is real
**Segoe UI** rendered through Pillow, not OpenCV's single-stroke Hershey font,
which is what made earlier builds look soft. Every colour and size comes from
`theme.py`, every rectangle from `layout.py`, so the window resizes cleanly and
nothing is a hardcoded pixel. Contrast is checked in the test suite against
WCAG AA.

## Overlay on the game

Play has an **Overlay on game** button. It turns the app window into a
transparent, always-on-top HUD: a coloured frame around the screen (green while
running, amber on warning, red when locked) and one small panel with the
camera, gate state, cadence, key pads, an **Arm / Disarm** button and an **X**.
It occupies about 5% of the screen; the other 95% is click-through, so the game
keeps focus and receives every click and key.

**Focus is handed to the game automatically.** Turning the overlay on gives
focus straight back to the last window you had before this one, and every click
on the HUD does the same, so a T-pose reaches the game rather than the
controller. Windows only permits that handover from a process that currently
holds focus -- which is always true when you click our button -- so the app can
give focus away but can never steal it back from something else.

The panel can sit in any of six places -- top or bottom, left, centre or right
-- chosen in Settings. It defaults to **top left**, the corner least likely to
cover the game's own score and pause controls.

This uses a Win32 layered window: colour-keyed pixels are both invisible and
click-through, `WS_EX_NOACTIVATE` stops the panel stealing keyboard focus, and
the window is kept topmost. It works over **borderless / windowed** fullscreen,
which is what a browser game uses. A game in *exclusive* fullscreen owns the
display surface and nothing can draw over it -- run it borderless instead.

## Camera and keys## Camera and keys

The camera is opened at **640×360** — a native 16:9 mode on nearly every
webcam. Measured on the development camera it keeps the sensor's **full field
of view** (0.999 correlation with a downscaled 720p frame) and opens in under
a second; asking for 640×480 is what made drivers *crop* the sensor and the
play area look narrow. The window letterboxes the frame rather than
stretching it, and every overlay is drawn on the image's own rect. If a camera
refuses the ratio, a warning names what it negotiated instead.

Why not 720p: over DirectShow it arrives uncompressed at 10 fps, and the
Media Foundation backend — which does deliver 720p at 30 — took 8 s per open
and stalled the app at launch. Both remain available (`camera.width/height`,
`camera.backend`) and the app falls back to the other backend if one refuses.

Keys default to **WASD** (`A`/`D` lanes, `W` jump, `S` duck). To see presses
land without the game: open `tools/keytest.html` in a browser tab — a browser
title passes the focus gate, so keys go there and the page logs each one with
timing. Notepad will not receive anything unless you add it to
`keys.focus_regex`; that refusal shows up as `SUPPRESSED WINDOW_FOCUS` with the
window title, which is the gate doing its job.

## Safety

Keys go to the **foreground window**, whatever it is -- the game, a browser
tab, a text editor you are testing in -- with one hard exception: the
controller's own window is always excluded, so it can never type into itself.
Set `keys.focus_regex` to something like `(?i)subway|chrome` if you want
delivery restricted to matching titles instead. The app starts **disarmed**;
a T-pose, F8 or Space arms it, and `--dry-run` makes Play publish events
without touching the OS.

## CLI

```
cardio-surfers                  the windowed app
  --probe                      setup self-test (cameras, fps, visibility, focus regex)
  --recalibrate                open straight into calibration
  --camera N                   camera override (Settings dropdown persists a choice)
  --dry-run                    never send real keys
  --no-preview                 headless console pipeline (always dry-run)
  --record PATH / --replay PATH   record landmarks / replay deterministically
  -v / -vv / -vvv              console verbosity   --log-file PATH  JSONL log
  --log-session                write session stats JSON on exit
```

Tuning loop: `--replay tests/fixtures/synthetic_run.jsonl -vvv` — change a
threshold in config, replay, diff the events. Bit-for-bit deterministic.

## Development

```bash
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
```

152 tests: pure-FSM suites for lanes/vertical/running/features driven by
synthetic sequences on a manual clock, telemetry sink behaviour, config
merging, and AST scans enforcing two invariants — no `print()` outside
`telemetry.py`, and no clock reads inside the FSMs (they take `now_ms`).

### Layout

```
src/cardio_surfers/
  main.py      CLI: app / headless / replay / probe
  app.py       the windowed shell (menu, play, debug, calibrate, settings)
  pipeline.py  features -> FSMs -> gate -> press queue -> dispatcher
  camera.py    threaded capture, always-newest-frame slot, live switching
  pose.py      MediaPipe wrapper -> PoseFrame
  features.py  torso line / shoulder dy / shoulder bounce, One Euro filtered
  filters.py   One Euro (inline, ~30 lines) + EMA fallback
  lanes.py     LaneFSM          <- pure, unit-tested
  vertical.py  VerticalFSM      <- pure, unit-tested
  running.py   RunDetector + GateFSM  <- pure, unit-tested
  keys.py      PressQueue, focus gate, SendInput sender, F8 hotkey
  calibrate.py guided 6-step session -> config.json
  events.py    event dataclasses, SuppressReason, EventBus
  telemetry.py threaded console + JSONL sink  <- the only module that prints
  timing.py    clock injection + rolling stats <- the only module reading a clock
  overlay.py   drawing primitives <- the only module that uses pixels
  theme.py     design tokens: colour, type, spacing
  layout.py    proportional rects for a resizable window
  arming.py    T-pose detector
  typography.py  Segoe UI text rendering with a glyph cache
  game_overlay.py  Win32 layered click-through overlay
  stats.py     session counters + summary
  record.py    JSONL record / replay
  audio.py     winsound gate warnings (threaded)
  config.py    config merging; the only source of tunables
  probe.py     --probe self-test
```
