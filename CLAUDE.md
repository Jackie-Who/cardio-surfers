# Cardio Surfers — working agreement

Read `PLAN.md` for the full spec. This file is the short list of things that must stay true
in every session, because they're the ones that get quietly violated and cost a day later.

## Project in one line

Webcam pose tracking (MediaPipe) → lane/jump/duck/running detection → Win32 key injection
into a browser running Subway Surfers. You must jog in place or your inputs stop working.

## Hard invariants

1. **Inject the clock.** FSMs (`lanes.py`, `vertical.py`, `running.py`, `keys.py`) take
   `now_ms` as a parameter. They never call `time.monotonic/time/perf_counter` internally.
   This is what makes `--replay` deterministic and the timing tests non-flaky.
2. **No `print()` outside `telemetry.py`.** Everything goes through the event bus and the
   threaded telemetry sink. Console I/O on Windows blocks and eats the latency budget.
3. **No magic numbers.** Every threshold, window, gap and cutoff comes from `config.json`.
   The numbers in PLAN.md are defaults for `config.example.json`, not literals for code.
4. **Normalized coordinates only.** `[0,1]` space or torso-lengths everywhere; pixels appear
   only in `overlay.py` at draw time.
5. **One event bus.** Key sender, HUD, console, JSONL log and stats are all subscribers to
   the same stream. Never log next to the logic — the two drift.
6. **Log refusals.** Every decision *not* to press a key publishes `Suppressed(what, reason,
   detail)` with a reason from the closed enum. Silence is not an acceptable failure mode.
7. **Pure FSMs.** Input is `(features, calibration, previous_state, now_ms)`. No I/O, no
   globals, no side effects. They are unit-tested against synthetic sequences.

## Environment

- Python **3.12** (mediapipe has no 3.13+ wheels). Pin exact versions in `requirements.txt`.
- Windows only for v1 — `pydirectinput-rgx` (SendInput scancodes), `winsound` for alerts.
- Model: `pose_landmarker_lite.task`, fetched by `scripts/fetch_model.py`, not committed.

## Workflow

Build **one phase from PLAN.md §13, then stop** and hand back for manual verification.
Most acceptance criteria need a human standing in front of a camera; you cannot self-verify
that a squat registers. What you *can* verify alone is the pure-logic layer — run the tests.

Tuning loop is `--replay fixtures/<name>.jsonl -vvv`, not live webcam iteration.

## Current phase

All phases P0-P7 built (P8 exe packaging skipped). **Awaiting manual
verification of the gesture layer** -- the pure-logic layer is covered by 152
tests (152) including a bit-for-bit replay determinism check against
`tests/fixtures/synthetic_run.jsonl`.

## Spec revisions (v2, user-directed) on top of PLAN.md

1. **Close range.** Target distance is 1-2 m from a desk webcam, not 2.5-3 m.
2. **Shoulder-bounce running detection (v3).** `features.run_signal: "bounce"`:
   a fast-filtered shoulder height detrended by a 0.8 s rolling mean; one step
   per dip (single-edge Schmitt trigger). Arm-swing (`"arm"`) and knee
   (`"knee"`) differentials remain selectable but demanded too much motion at
   desk range. The §7.2 FFT torso-bob was never built: the bounce trigger has
   no window latency.
   - **The shoulder line is the focus.** Jump/duck lines are drawn from the
     same baseline/scale the FSM compares; the filtered shoulder line is drawn
     too, so what crosses on screen is what fires.
   - **v5: TWO ABSOLUTE LINES.** `vertical.jump_y` and `vertical.duck_y` are
     plain fractions of frame height -- screen positions, not offsets from a
     neutral posture, which is gone entirely along with body scale in the
     vertical path. `VerticalFSM.tick(shoulder_y, now_ms)` compares the
     shoulder line against them directly. Calibration places them off the
     *jogging* posture; Settings drags them on the live preview; uncalibrated
     falls back to `default_jump_y` / `default_duck_y` (never derived from the
     first pose -- a spurious detection once put the duck line at y=1.14,
     off-screen and uncrossable). History: torso-length scale flickered with
     hip visibility; shoulder-width scale fixed that but still hid the lines
     behind units nobody could nudge; relative-to-neutral still drifted when
     the body moved.
   - **T-pose arming** (`arming.*`, `arming.py`): both wrists level with the
     shoulder line and a wrist span of >= 2 shoulder widths, held 700 ms.
     Latched until released, plus a cooldown. F8/Space still work.
   - **The post-gesture pause stops the cadence clock**, it does not merely
     skip ticks: `RunDetector.pause/resume` shifts the remembered step
     timestamps forward by the paused duration so the 3 s window still holds
     them. Skipping ticks alone let the window slide and the bar sagged.
   - **Lanes judge the shoulder-midpoint torso line**, drawn full-height
     (`features.cx_hip_weight` blends hips in, ramped by visibility -- never a
     hard switch, which read as sway). Compact lanes clamp the hysteresis
     band instead of refusing calibration.
   - **Vertical gestures pause the running detector** for
     `running.vertical_pause_ms` (1 s) and reset its half-cycle, and the
     vertical FSM has hold timeouts (`max_jump_ms` / `max_duck_ms`) that
     re-baseline to the new posture -- without them a duck could strand the
     FSM forever (settled lower than the old neutral -> never exits).
3. **Single windowed app.** `cardio-surfers` opens one OpenCV window holding
   menu / game / debug / calibrate / settings (camera dropdown lives there).
   Debug mode is dry-run + always-armed + no focus gate: detections light up
   key pads in-window and nothing touches the OS.
4. **16:9 capture: DSHOW at 640x360.** Measured on the dev camera: 640x360
   keeps the full field of view (0.999 correlation with downscaled 720p) and
   opens in 0.7 s; 640x480 crops the sensor; 720p over DSHOW is uncompressed
   at 10 fps; MSMF gets 720p at 30 but took 8 s to open and stalled launch.
   `camera.process_width` is only a safety cap. The window letterboxes; every
   overlay draws on the fitted image rect that `_blit_camera` returns.
5. **WASD is the default mapping**, and `keys.focus_regex` defaults to **empty**
   = deliver to any foreground window, with the app's own window excluded by
   title. The old `(?i)subway|chrome|edge|firefox` default silently refused
   Notepad, which read as "WASD is broken" during testing -- the scancodes
   were always fine.
7. **Window chrome:** the X closes the app (no q-to-quit), a back arrow in the
   header pops a screen-history stack, and `draw_button` renders a distinctly
   brighter hover state. Beep volume is a real slider: `winsound.Beep` has no
   volume, so `audio.synth_tone` builds an in-memory WAV at the requested
   amplitude and `PlaySound` plays it.
8. **v6 UI: tokens + proportional layout.** `theme.py` owns every colour, font
   size and gap; `layout.py` computes every rect from the live window size.
   **No module may hardcode a pixel or a colour.** The window is
   `WINDOW_NORMAL` and resizable; `_sync_window_size` clamps to
   `ui.min_width/min_height` and rebuilds the layout. `tests/test_layout.py`
   asserts every rect stays inside the window at eight sizes -- that test is
   the resize guard, so add new rects to `all_rects` there.
   - **Hershey fonts are ASCII-only**: any other character renders as `??`.
   - The Play screen is deliberately sparse (video + lines + pads + cadence +
     armed/gate). Numbers and traces belong in Debug.
9. **The gate never opens locked.** `Pipeline.set_active(True, now)` primes the
   cadence window to the floor via `RunDetector.prime`, and while inactive the
   gate is frozen and `Pipeline.spm()` reports the floor. `--replay` and
   headless runs call `set_active(True)` explicitly or the gate never advances.
10. **SPM hold beats pausing alone.** `RunDetector.hold()` pins the reported
   cadence for `running.spm_hold_ms` after a jump/duck. Pausing the detector
   stopped new steps but the bar still sagged when it resumed; the hold is what
   the HUD and the gate both read (`Pipeline.spm(now_ms)`).
11. **Camera names** come from `pygrabber` (`camera.device_names`), whose
   DirectShow enumeration order matches OpenCV's `CAP_DSHOW` indices.
12. **Text is Pillow + Segoe UI** (`typography.py`), never `cv2.putText`.
   Hershey is a single-stroke font: soft at UI sizes and ASCII-only. Masks are
   cached by (text, size, weight) and tinted at blend time.
13. **Audio is `sounddevice`, not `winsound`.** `PlaySound(SND_MEMORY|SND_ASYNC)`
   raises "Cannot play asynchronously from memory" -- that was the dead Test
   button. Synthesise at the *device's* `default_samplerate`: WASAPI endpoints
   reject anything else rather than resampling. Device lists come from the
   WASAPI host API because MME truncates names to 31 characters.
14. **Never retry a busy camera on the other backend.** Measured here: when
   DirectShow refuses a camera another app holds, opening it over MSMF took
   55 s and then delivered no frames. `camera.backend_fallback` is off;
   `start_any()` tries the *next* camera on the fast backend instead and says
   which one it chose.
15. **Overlay mode** (`game_overlay.py`) is a Win32 layered window:
   colour-keyed pixels are invisible AND click-through, `WS_EX_NOACTIVATE`
   keeps focus on the game, topmost keeps it visible. Works over borderless
   fullscreen only. The colour key is pure black, so no surface may be black.
   The panel is deliberately small (~5% of the screen) and uses `overlay_theme`,
   a gentler scale than the app window, with **unscaled** width bounds so it is
   the same modest size on 1080p and 1440p. Six anchors (`POSITIONS`), default
   `top-left`; `ui.overlay_position` persists the choice.
16. **Overlay focus handback.** `_remember_game_window()` polls the foreground
   window each render and keeps the last one that is not ours;
   `_refocus_game()` hands focus to it on entering overlay mode, after every
   HUD click, and before a T-pose arms. `SetForegroundWindow` is only
   permitted from the process that already owns the foreground -- verified: a
   handover from our own process succeeds, and taking focus back afterwards is
   refused by Windows even with the AttachThreadInput fallback. So never rely
   on stealing focus; only on giving it away.
17. **Frozen build (`cardio_surfers.spec`, `scripts/build_exe.py`).**
   - Entry point is `launcher.py`, never `src/cardio_surfers/__main__.py`:
     PyInstaller runs the entry script as a top-level module, where the
     package's relative imports raise ImportError.
   - **matplotlib cannot be excluded.** `mediapipe.tasks.python.vision`
     imports `drawing_utils` at load, which imports matplotlib; excluding it
     builds fine and dies on the first `create_from_options`.
   - `config.resource_dir()` is the bundle (`sys._MEIPASS`, deleted on exit);
     `config.user_dir()` is beside the .exe. Anything writable -- config.json,
     session stats -- must use `user_dir`, or calibration vanishes on restart.
   - `TelemetrySink` must tolerate `sys.stdout is None` (windowed builds).
18. **The active lane is tinted, and flashes on entry** (`draw_lane_lines`,
   `flash` in [0,1] driven by `ui.lane_flash_ms`). Plain lines alone made a
   lane change hard to notice on a small overlay panel.
6. Cheating (swinging arms while sitting) is accepted -- smooth gameplay at
   desk range is the priority; honest use is on the user.

Additions beyond the PLAN §3 module list: `timing.py`, `config.py`,
`pipeline.py` (the features->FSMs->gate->queue orchestrator), `app.py` (the
windowed shell), `audio.py` (WAV-synth beeper), `arming.py` (T-pose),
`theme.py` (design tokens) and `layout.py` (proportional rects).
The app window is titled "Motion Controller" ON PURPOSE -- naming it anything
matching the focus regex (e.g. "Subway...") would let the focus gate pass when
our own window is focused.
