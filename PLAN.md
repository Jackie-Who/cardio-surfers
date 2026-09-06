# Subway Motion — Full-Body Motion Controller for Subway Surfers

**Spec for Claude Code.** Platform: **Windows**. Form factor: **standalone Python app**. Phase-1 scope: **playable MVP + guided calibration + running-in-place gate + session stats + full observability**.

> **Revision 4.** Adds §16, the implementation invariants a coding agent must not violate — chiefly clock injection, which is what makes `--replay` and the test suite deterministic. Revision 3 added the observability layer (§10) — event bus, tiered console telemetry, and suppression logging — and the CLI surface (§11). Revision 2 added the running-in-place gate (§7) and explicit three-lane doubles (§6); §6.3 remains the constraint most likely to bite during tuning.

---

## 1. What we're building

A webcam sits in front of you. You **jog in place** the whole time. You step left, step right, jump, and squat to dodge. The app watches your body, decides what you just did, and injects the corresponding key press into whatever window has focus — a browser running a Subway Surfers web port. The game knows nothing about us; we're a virtual keyboard driven by a pose model.

The jogging is not decoration. **If you stop moving your feet, you get a warning, and then your inputs stop working.** That's the mechanic that turns a novelty controller into a workout: there is no way to stand still and play.

Success criteria:

1. You can survive 60+ seconds of a real run.
2. Standing still for ~4.5 s reliably locks you out; resuming a jog restores control within ~1 s.
3. Zero false lockouts during a clean, continuously-jogged run.
4. Every decision the app makes — including every decision *not* to press a key — is visible in the telemetry (§10).

Non-goals for v1: hoverboard (double-tap Space), multiplayer, mobile, phone-as-camera, auto-pausing the game.

---

## 2. Why a standalone Python app, not a browser thing

I checked the target. `subwaygame.io/classic` does **not** host the game itself — it embeds it from a third-party origin (`g2.igroutka.ru`) in an iframe, and the documented controls are **Arrow keys** (←/→ move, ↑ jump, ↓ roll, Space hoverboard), not WASD.

| Approach | How keys get in | Verdict |
|---|---|---|
| **OS-level injection (chosen)** | Python → Win32 `SendInput` with scancodes → focused window | Works with *any* clone site, any browser, and later with an Android emulator running the real APK. No origin/CSP/iframe problems. Only constraint: the game window must have focus. |
| Web page + Chrome extension | Content script dispatches synthetic `KeyboardEvent`s | Needs host permissions for whichever third-party origin *that particular site* embeds, with `all_frames: true`. Many HTML5 games accept untrusted events (they only read `event.code`), but canvas/Unity-WebGL builds often don't, and the embed origin differs per site. Fragile, site-specific. |
| Extension + `chrome.debugger` → `Input.dispatchKeyEvent` | Genuinely trusted events via CDP | Works, but needs the `debugger` permission and leaves a permanent "Chrome is being debugged by software" banner. |

**Key mapping is a config field** — arrows by default, WASD preset available.

---

## 3. Architecture

```
┌──────────────┐  latest frame only  ┌───────────────┐  landmarks  ┌────────────────────┐
│ CameraThread │ ───────────────────►│ PoseLandmarker│ ───────────►│ Features           │
│ (cv2, DSHOW) │                     │ (MediaPipe)   │             │ cx, dy, scale, d   │
└──────────────┘                     └───────────────┘             └─────────┬──────────┘
                                                                             │ filtered
                 ┌───────────────────────────────────────────────────────────▼─────────┐
                 │ LaneFSM (3 lanes + hysteresis)      → LaneChange(delta)              │
                 │ VerticalFSM (NEUTRAL/JUMP/DUCK)     → Jump / Duck                    │
                 │ RunDetector (step trigger→cadence)  → Step, GateTransition           │
                 └───────────────────────────────┬─────────────────────────────────────┘
                                                 │
                                    ┌────────────▼────────────┐
                                    │       EVENT BUS         │  ← single source of truth
                                    └─┬────────┬────────┬─────┘
                    ┌─────────────────┘        │        └───────────────┐
                    ▼                          ▼                        ▼
        ┌───────────────────────┐   ┌──────────────────┐   ┌────────────────────────┐
        │ Gate → PressQueue →   │   │ Debug HUD        │   │ TelemetrySink (thread) │
        │ focus gate → armed? → │   │ (OpenCV overlay) │   │ console + JSONL        │
        │ SendInput scancode    │   └──────────────────┘   └────────────────────────┘
        └───────────────────────┘
```

**Everything that consumes app state subscribes to one event bus.** The key sender, the HUD, the console logger, the JSONL writer, and the stats counter are all observers of the same stream. This is why the debug output can never drift from actual behaviour: if the console says a key was pressed, it's because the same event object went to the sender. Do not scatter `print()` calls through the pipeline — there is exactly one place text comes from (§10.4).

Everything between the camera and the key sender is a pure function of `(landmarks, calibration, previous_state)`, which is what makes replay (§12) work.

### Repo layout

```
subway-motion/
  README.md
  requirements.txt
  config.example.json
  models/pose_landmarker_lite.task        # downloaded by scripts/fetch_model.py
  scripts/fetch_model.py
  tools/keytest.html
  src/subway_motion/
    __init__.py
    main.py          # CLI + main loop wiring
    camera.py        # threaded capture, always-newest-frame
    pose.py          # MediaPipe wrapper -> PoseFrame dataclass
    features.py      # torso centre, normalized vertical, body scale, knee differential
    filters.py       # OneEuroFilter (+ EMA fallback)
    events.py        # Event dataclasses, SuppressReason enum, EventBus
    lanes.py         # LaneFSM                                  <-- pure, unit-tested
    vertical.py      # VerticalFSM                              <-- pure, unit-tested
    running.py       # RunDetector + GateFSM                    <-- pure, unit-tested
    keys.py          # PressQueue, KeySender, focus gate, dry-run backend
    telemetry.py     # TelemetrySink: threaded console + JSONL writer
    calibrate.py     # guided calibration -> config.json
    stats.py         # session counters + summary
    overlay.py       # debug HUD
    record.py        # JSONL record + replay
    probe.py         # --probe setup self-test
  tests/
    test_lanes.py  test_vertical.py  test_running.py  test_features.py  test_telemetry.py
    fixtures/*.jsonl
```

---

## 4. Vision stack

**Model: MediaPipe Pose Landmarker (BlazePose GHUM), `pose_landmarker_lite.task`.** `pip install mediapipe` (1.0.1 as of Aug 2026; wheels for **Python 3.9–3.12** — do *not* create the venv on 3.13+). 33 landmarks, normalized `[0,1]` x/y, plus `visibility` per landmark. Lite is correct: we need coarse limb geometry, not finger precision, and every millisecond of inference is input lag. MoveNet Lightning via TFLite is the documented fallback; it should not be needed.

```python
PoseLandmarkerOptions(
    base_options=BaseOptions(model_asset_path="models/pose_landmarker_lite.task"),
    running_mode=VisionRunningMode.LIVE_STREAM,   # async, result_callback
    num_poses=1,
    min_pose_detection_confidence=0.5,
    min_pose_presence_confidence=0.5,
    min_tracking_confidence=0.5,
    output_segmentation_masks=False,              # costs time, unused
    result_callback=on_result,
)
```

`LIVE_STREAM` requires **monotonically increasing millisecond timestamps** in `detect_async` — derive from a monotonic clock, never repeat a value or it throws.

### Camera — the latency trap

Do **not** call `cap.read()` in the same loop as inference. `VideoCapture` buffers; if inference lags capture even briefly you start consuming stale frames and the lag compounds until the game is unplayable.

- `cv2.VideoCapture(index, cv2.CAP_DSHOW)` — DirectShow opens far faster than MSMF on Windows.
- 640×480 @ 30 fps. Higher resolution buys nothing; the model downsamples.
- `cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)` **and** a dedicated grab thread overwriting a single `latest_frame` slot under a lock. The main loop always takes the newest frame and drops the rest.
- `cv2.flip(frame, 1)` — mirror it. Your left must be screen-left.

### Framing and screen setup

The running detector wants your **knees** in frame: head to at least mid-shin, roughly **2.5–3 m back, camera at hip/waist height**. If knees aren't visible the app falls back to torso-bob detection (§7.2) and says so.

**Screen setup: run the game windowed, or on a second monitor, with the HUD window visible beside it.** The HUD is the primary real-time readout; console telemetry is for tuning and post-hoc replay, not for reading mid-dodge. Put both in the README with a diagram.

### Latency budget (target ≤ 120 ms end to end)

| Stage | Budget |
|---|---|
| Camera exposure + USB transport | 15–35 ms |
| Inference (lite, CPU) | 10–25 ms |
| Filtering lag | 15–30 ms |
| FSM + press queue + injection | < 5 ms (plus the deliberate multi-press gap, §6.2) |
| Browser/game frame | 16–33 ms |

Log rolling p50/p95 of capture→keypress in the HUD. Past ~150 ms p95, tune the One Euro cutoff before touching the model.

---

## 5. Features — the actual math

Every derived quantity is **normalized by body scale**, so stepping toward or away from the camera doesn't move the thresholds. This is the biggest single improvement over the existing tutorials, which compare raw pixel y-values against a fixed line and break the moment you drift forward.

Gate everything on `visibility > 0.5` for the landmarks used; if a required landmark is missing, emit no event and publish `FrameInvalid` with the offending landmark names.

```
L_SH=11  R_SH=12  L_HIP=23  R_HIP=24  L_KNEE=25  R_KNEE=26  L_ANK=27  R_ANK=28

shoulder_mid = midpoint(L_SH, R_SH)
hip_mid      = midpoint(L_HIP, R_HIP)

# body scale: torso length in normalized units.
# Rigid during a jump, shrinks with distance from camera — exactly the invariant we want.
scale = max(|shoulder_mid.y - hip_mid.y|, EPS)

# horizontal position: torso centroid, not shoulders alone.
# Shoulders jitter when you swing your arms — and you WILL swing your arms while jogging.
cx = mean(L_SH.x, R_SH.x, L_HIP.x, R_HIP.x)
     # if hip visibility < 0.5, fall back to shoulder midpoint + HUD warning

# vertical displacement, positive = higher than baseline
dy = (baseline_shoulder_y - shoulder_mid.y) / scale

# knee differential — the running signal. Antisymmetric by construction (§7.1).
knee_lift_L = (hip_mid.y - L_KNEE.y) / scale
knee_lift_R = (hip_mid.y - R_KNEE.y) / scale
d = knee_lift_R - knee_lift_L
```

Use **shoulder** height, not hip height, for `dy`: it captures the full travel of both a jump and a squat, and survives cropped hips. `scale` in the denominator is what makes `dy` and `d` distance-invariant.

### Filtering

Apply a **One Euro filter** independently to `cx`, `shoulder_mid.y`, `scale`, and `d`. One Euro smooths hard when you're still (no phantom lane changes) and barely filters when you move fast (no lag on the dodge). Start `min_cutoff=1.0, beta=0.5, d_cutoff=1.0`; expose all three. A plain EMA (`alpha=0.5`) sits behind the same interface as a fallback.

Filter `d` **more gently** than `cx` (`min_cutoff≈1.5`) — over-smoothing `d` rounds off step transitions and drops your measured cadence.

Note the MediaPipe **Tasks** API dropped `smooth_landmarks=True` from the legacy `mp.solutions.pose`, so this filtering is on us.

### Baseline drift

`baseline_shoulder_y` is set at calibration, then maintained as a slow EMA (`alpha≈0.01`) updated **only while VerticalFSM is NEUTRAL**. Over five minutes you'll creep forward or slouch; without this the duck threshold silently becomes unreachable. Never update during a jump or duck or the baseline chases the gesture and cancels it.

**Jogging complicates this**: your torso bobs continuously, so instantaneous `shoulder_y` is never at rest. Feed the drift EMA the *rolling median* of `shoulder_y` over the last ~1 s, not the raw value, so the bob averages out instead of dragging the baseline down.

---

## 6. Lanes — three discrete zones, with doubles

### 6.1 Three zones, not geometric thirds

The player-facing model is exactly three distinct sections, and you jog in place inside whichever one you're in. The HUD draws them as three labelled zones so it *reads* as thirds.

What's calibrated is where the boundaries sit. Literal geometric thirds are a poor fit: with a ~60° webcam at 2.5 m, each outer third is roughly a metre of sideways travel — you'd be sprinting sideways and leaving frame. So:

```
boundary_left  = (x_centre + x_left_sample)  / 2
boundary_right = (x_centre + x_right_sample) / 2
```

from the calibration step where you actually step to each side. `lane_mode: "thirds"` remains available for comparison. Fallback if samples are rejected: `x_centre ± 1.1 × shoulder_width`.

### 6.2 Doubles — the core mechanic

```
lanes: 0 = LEFT, 1 = CENTRE, 2 = RIGHT

on lane index change i -> j:
    direction = 'right' if j > i else 'left'
    enqueue |j - i| presses of `direction`
```

Centre→right is one press; **left→right is two presses of the same key**, which is what the game needs for double-swipe dodges.

**Presses go through a rate-limited queue, never straight to the keyboard.** `PressQueue` drains one press every `multi_press_gap_ms` (default **110 ms**, tune 80–160). Web ports run a lane-change animation and many drop input received during it; two presses fired 5 ms apart will very likely register as one, and your double-dodge silently becomes a single. **Verifying and tuning this gap against the real game is the exit criterion for phase P4.**

Both sampling paths must yield two presses, and "emit the difference" handles both for free:

- Fast sweep, camera sees `0 → 2` in consecutive frames → `|2−0| = 2` presses. ✓
- Slower sweep, camera sees `0 → 1 → 2` → one press, then another; the queue spaces them. ✓

**Do not add a minimum dwell time in a lane.** It's a tempting noise fix and it would make fast left↔right sweeps impossible — exactly the mechanic you want. Noise suppression is hysteresis only.

```
enter LEFT  : cx < boundary_left  - H
leave LEFT  : cx > boundary_left  + H          H = lane_hysteresis
(mirrored for RIGHT)
```

### 6.3 The constraint where §6 and §7 collide

**Jogging in place sways your torso laterally.** If that sway exceeds the hysteresis band you emit phantom lane changes while standing still in one lane — and because of §6.2, a sway across two boundaries fires *doubles*. This is the most likely source of maddening bugs in the project, and it exists only because we added the running requirement.

Fix it at calibration. The jog step (§9, step 5) measures the standard deviation of `cx` while you jog:

```
lane_hysteresis = clamp(3 * sway_std, 0.02, 0.06)
```

Then validate: if `lane_hysteresis > 0.4 × (centre-lane half-width)`, the lanes are too narrow for your sway — **fail calibration with "step further apart"** rather than saving a config that misfires all session. Both numbers stay visible on the HUD.

Second-order: suppress lane events for ~150 ms after a jump edge (`cx` is noisy on takeoff), and log every such suppression (§10.3).

---

## 7. Running in place — detection

### 7.1 Primary signal: knee differential

```
d = knee_lift_R - knee_lift_L          # from §5, in torso-lengths
```

Jogging produces a clean alternating oscillation in `d`. **The differential is the important choice**: a jump lifts both knees and a squat drops both, so `d` stays near zero through both — vertical gestures cannot manufacture fake steps. Absolute knee height fails this badly.

Step detection is a **Schmitt trigger with mandatory alternation**:

```
state RIGHT_UP  when d > +step_amp        step_amp default 0.10 torso-lengths
state LEFT_UP   when d < -step_amp
every RIGHT_UP <-> LEFT_UP transition = one step, timestamped
```

Both thresholds must be crossed in turn, so you cannot farm steps by twitching one leg, and shuffles below `step_amp` don't register. That's the anti-cheese property; it's free, which is why the amplitude gate is not optional.

**Cadence, in steps per minute (spm), one step = one foot strike:**

```
spm = 60 * (steps within the last cadence_window_s) / cadence_window_s     # default 3.0 s
```

Mind the units — some running literature reports cadence as full stride *cycles* per minute, half of spm. We use spm throughout; a factor-of-2 error makes every threshold nonsense. For reference, recreational runners sit around 160–170 spm at easy pace, so a comfortable jog in place lands roughly in the 120–160 spm range.

### 7.2 Fallback signal: torso bob

If knee visibility is poor (cramped room, camera too high, baggy clothing), fall back to vertical torso oscillation. Your centre of mass dips once per foot strike, so bob frequency ≈ step frequency.

```
window   = last 2.5 s of hip_mid.y, detrended (subtract rolling mean)
spectrum = numpy.fft.rfft(window * hann)
f_dom    = argmax over the 1.0–3.5 Hz band
require   power(f_dom) / total_in_band_power > bob_power_ratio   (default 0.35)
spm      = 60 * f_dom
```

Materially worse: ~2.5 s of latency, and arm-waving or bouncing can fake it. Fallback only, marked `LOW CONFIDENCE` on the HUD and in telemetry, with a longer grace window while active.

**Mode selection is automatic**: knee mode if median knee visibility over the last 1 s > 0.6, else bob mode. `running.mode` can force either. Every mode switch is an event.

### 7.3 The gate FSM

```
                spm < floor, sustained grace_ms (1500)
   RUNNING ────────────────────────────────────────────► WARNING
      ▲                                                     │
      │ spm >= floor                                        │ warning_ms (3000) elapsed
      │                                                     ▼
      └──── spm >= floor sustained resume_ms (1000) ──── LOCKED   [events dropped]
```

- **WARNING**: inputs still work. The "you're about to lose control" window.
- **LOCKED**: lane/jump/duck events are dropped at the gate and logged as `SUPPRESSED reason=GATE_LOCKED` — the key sender never sees them. **No auto-pause**: the game keeps running and you'll most likely crash, but a fast recovery inside your last move's animation can still save you. Also avoids assuming anything about a clone site's pause key.
- Stop → lockout is ~4.5 s total.

**Warnings must be audible.** Your eyes are on the game. `winsound.Beep(880, 120)` at WARNING onset, repeating every 750 ms; a lower double-beep (440 Hz ×2) on LOCKED; a rising pair on recovery. Stdlib, no dependency. `running.audio_warnings` toggles it.

### 7.4 Freeze the grace clock during legitimate interruptions

This decides whether the gate is a good feature or an infuriating one. A jump-land-double-dodge-duck sequence genuinely interrupts your stride for over a second. If that accrues toward `grace_ms`, the gate punishes you for playing well.

So: while VerticalFSM is JUMP or DUCK (including refractory), and for 250 ms after any lane change, **do not accrue time toward the grace timer**. Keep counting steps throughout; only the "time below floor" accumulator freezes. Same for `startup_grace_ms` (3 s after arming) and all of calibration. Each freeze window is visible in `-vvv` telemetry so you can see exactly why the gate did or didn't advance.

### 7.5 Cadence floor

Set from **your own** jog, measured at calibration:

```
min_cadence_spm = clamp(0.6 * jog_cadence_spm, 70, 140)
```

60% of your natural cadence is a floor you must genuinely stop or drop to a walk to breach — it gates idling, not pace. It also self-corrects for a camera angle that under-counts steps, which a fixed constant cannot.

---

## 8. Key injection (Windows)

**Library: `pydirectinput-rgx`** (maintained fork of `pydirectinput`). Uses `SendInput` with **scancodes** rather than virtual key codes. For a browser game plain `pyautogui`/`pynput` would probably work, but scancodes are strictly more compatible and cost nothing — and they're what you'd need for an emulator or desktop game later.

```python
sender.key_down(key); sleep(hold_ms/1000); sender.key_up(key)   # hold_ms default 60
```

Explicit down/hold/up, not `press()` — some engines poll input once per frame and a sub-frame tap can be missed.

Four safety mechanisms, all required, **all of which log their refusals** (§10.3):

1. **Gate check.** GateFSM `LOCKED` drops events before the queue.
2. **Focus gate.** OS keys go to the foreground window. `pygetwindow.getActiveWindow()`, send only if the title matches a configurable regex (default `(?i)subway|chrome|edge|firefox`). Without this, the first alt-tab to Slack types arrow keys into a colleague's DM. On refusal, log the actual window title — "why did nothing happen" is answered instantly.
3. **Arm/disarm hotkey.** Global `F8` via `pynput.keyboard.GlobalHotKeys` on its own thread. Shown huge on the HUD and in the console. **Start disarmed.**
4. **Dry-run backend.** `--dry-run` swaps `KeySender` for one that only publishes events. Default through phase P3.

The OpenCV preview must **never take focus** — `cv2.moveWindow` it aside, tell the user to click the game once, never raise it.

---

## 9. Calibration

First launch, or `--recalibrate`. Full-screen OpenCV window, large text, 3-2-1 countdowns, ~60 frames per step, **median** of each sample so one bad frame can't poison it.

| Step | Prompt | Captures |
|---|---|---|
| 0 | "Stand centred, arms at your sides, whole body including knees in frame" | `x_centre`, `baseline_shoulder_y`, `scale0`, `shoulder_width` |
| 1 | "Step to your LEFT and hold" | `x_left_sample` |
| 2 | "Step to your RIGHT and hold" | `x_right_sample` |
| 3 | "Jump once, straight up" (peak over 3 s) | `jump_peak_dy` |
| 4 | "Squat down and hold" | `duck_peak_dy` |
| 5 | **"Jog in place for 8 seconds"** | `jog_cadence_spm`, `sway_std`, `d_amplitude` |

Derived:

```
jump_enter      = clamp(0.55 * jump_peak_dy,   0.10, 0.30)
duck_enter      = clamp(0.60 * |duck_peak_dy|, 0.08, 0.25)
min_cadence_spm = clamp(0.60 * jog_cadence_spm, 70, 140)
step_amp        = clamp(0.45 * d_amplitude,    0.06, 0.18)
lane_hysteresis = clamp(3.0  * sway_std,       0.02, 0.06)
```

Step 5 does triple duty — cadence floor, step amplitude, and the sway measurement §6.3 depends on. Make it 8 s, not 3; the cadence estimate needs samples.

**Validation gates** (re-prompt, never save garbage):

- Hip or knee visibility < 0.5 in step 0 → "step back, we can't see your legs" (or offer bob mode).
- `|x_left − x_centre| < 0.05` → "step further".
- `jump_peak_dy < 0.08` → "jump higher".
- `d_amplitude < 0.08` in step 5 → "lift your knees higher".
- `lane_hysteresis > 0.4 × centre-lane half-width` → "you sway more than your lanes are wide — step further apart" (§6.3).

Calibration writes a full transcript of every measured and derived value to the telemetry log, so a bad session is diagnosable after the fact.

---

## 10. Observability — HUD, telemetry, logs

Three surfaces over **one** event stream. The HUD is what you watch live (beside a windowed game or on a second monitor); the console is what you read while tuning; the JSONL log is what you replay and grep afterwards. All three render the same events, so they can never disagree.

### 10.1 Event taxonomy

A closed set in `events.py`. Every event carries `t_ms` (monotonic, relative to session start) and `frame_t_ms` (the pose frame it derives from) so telemetry lines correlate exactly with a recording.

| Event | Fields |
|---|---|
| `LaneChange` | `from`, `to`, `delta`, `cx` |
| `Jump` | `dy_peak` |
| `Duck` | `dy_min` |
| `Step` | `foot` (L/R), `spm_now` — high volume, `-vvv` only |
| `GateTransition` | `from_state`, `to_state`, `spm`, `floor` |
| `KeyPress` | `key`, `source` (lane/jump/duck), `queued_ms` |
| `ModeSwitch` | `from`, `to` (knee ↔ bob), `knee_visibility` |
| `FrameInvalid` | `missing` (landmark names) |
| `Armed` / `Disarmed` | — |
| `CalibStep` | `step`, measured values |
| **`Suppressed`** | `what`, `reason`, `detail` — see §10.3 |

### 10.2 Verbosity tiers

| Flag | Output |
|---|---|
| *(none)* | Warnings and errors only |
| `-v` | Discrete events: key presses, lane changes, jumps, ducks, gate transitions, arm/disarm, mode switches |
| `-vv` | `-v` plus a **5 Hz single-line state ticker** (carriage-return updated, so it doesn't scroll) |
| `-vvv` | `-vv` plus per-stage latency attribution, individual `Step` events, grace-clock freeze windows, and **all suppression events** |

Line formats — human readable, still greppable:

```
[  12.480] LANE_CHANGE   from=1 to=2 delta=+1 cx=0.671
[  12.483] KEY_PRESS     key=right source=lane q=0ms
[  12.593] KEY_PRESS     key=right source=lane q=110ms
[  13.010] SUPPRESSED    what=jump reason=REFRACTORY remaining=182ms
[  14.200] GATE          RUNNING -> WARNING spm=71 floor=96
```

Ticker (`-vv`, redrawn in place at 5 Hz):

```
lane=CENTRE cx=0.501 dy=+0.01 d=-0.14 spm=152/96 gate=RUNNING mode=knee fps=30 p95=104ms q=0
```

`--log-file debug.jsonl` writes the same events as one JSON object per line regardless of console verbosity, so you can be quiet on screen and complete on disk.

### 10.3 Suppression logging — the part people forget

When a double doesn't register, the absence of a log line tells you nothing. So **every decision not to act publishes a `Suppressed` event with a reason from a closed enum**:

| Reason | Detail |
|---|---|
| `GATE_LOCKED` | current spm, floor |
| `NOT_ARMED` | — |
| `WINDOW_FOCUS` | **the actual foreground window title** |
| `REFRACTORY` | ms remaining |
| `HYSTERESIS` | `cx` and the band it sat inside |
| `POST_JUMP_LANE` | ms remaining in the 150 ms window |
| `LOW_VISIBILITY` | which landmarks failed |
| `STEP_AMPLITUDE` | measured `d` peak vs `step_amp` |
| `STARTUP_GRACE` | ms remaining |

This is what turns "why didn't that work" from a debugging session into reading one line. It's also the cheapest insurance against the §6.3 sway problem: phantom lane changes and *missing* lane changes look identical from the outside, and only the suppression reason distinguishes them.

### 10.4 Never print from the hot path

Console writes on Windows are synchronous and can block for milliseconds. Printing per-frame from the pipeline would quietly eat the latency budget §4 exists to protect — an ironic way to break the project.

So: `TelemetrySink` owns a `queue.Queue(maxsize=256)` and a **daemon writer thread** that does all console and file I/O. Producers call `put_nowait` and bump a `dropped` counter on `Full`; the ticker shows drops so silent loss is visible. Nothing in `lanes.py`, `vertical.py`, `running.py`, `features.py`, or `keys.py` ever calls `print()`.

### 10.5 HUD contents

- Skeleton overlay; feature landmarks drawn larger, red when `visibility < 0.5`.
- **Three shaded lane zones**, labelled, with the hysteresis band drawn at each boundary.
- Horizontal jump/duck threshold lines, projected back into pixel space from `dy`.
- **Cadence bar**: live spm vs `min_cadence_spm`, colour-coded.
- **Gate banner**: `RUNNING` (green) / `KEEP MOVING` (amber, with countdown to lock) / `LOCKED` (red, full-screen border).
- Numerics: `cx`, `dy`, `scale`, `d`, lane, vertical state, detector mode, fps, capture→keypress p50/p95, press-queue depth, telemetry drops.
- Last 5 events with ages, **including suppressions**; big **ARMED / DISARMED**.
- Rolling sparklines for `dy` and `d` over ~3 s — the `d` trace makes step detection instantly debuggable.
- Live `lane_hysteresis` vs measured sway (§6.3).

Design the HUD to be legible at a glance from across the room at 2.5 m: large fonts, colour-coded states, no dense text. `--no-preview` disables the window entirely for a pure-console session.

---

## 11. CLI surface

```
subway-motion [OPTIONS]

  --dry-run              Publish events but never send keys (default through P3)
  --recalibrate          Force the guided calibration flow
  --config PATH          Config file (default ./config.json)
  --camera N             Camera index override
  -v / -vv / -vvv        Console verbosity (§10.2)
  --log-file PATH        Write all events as JSONL, independent of console verbosity
  --record PATH          Record raw landmarks to JSONL for replay
  --replay PATH          Replay a recording through the identical pipeline, sender stubbed
  --no-preview           Headless: no OpenCV window, console only
  --probe                Setup self-test: enumerate cameras, report actual fps, model
                         load time, landmark visibility summary, and whether the current
                         foreground window matches the focus regex. Then exit.
  --log-session          Write a per-session stats JSON (off by default)
```

`--replay recording.jsonl -vvv` is the core tuning workflow: change a threshold, replay the same fixture, diff the events. No webcam, no jumping around the room, fully deterministic.

`--probe` exists because "it doesn't work" is nearly always camera index, framing, or focus regex, and answering those three takes one command.

---

## 12. Testing

The FSMs are pure — test them properly rather than by flailing at a webcam.

1. **Record/replay.** `--record` dumps `{t, landmarks[], visibility[]}` per frame; `--replay` feeds it back through the identical pipeline. Fixtures to record: clean single lane changes; **clean left↔right doubles**; clean jumps; clean squats; **jogging in place, stationary, for 30 s** (must produce zero lane events — the §6.3 regression test); **stopping dead mid-run** (must warn then lock); a full game run; a deliberately messy run (arm waving, a second person walking behind).
2. **`test_lanes.py`** — boundary-straddling `cx` yields exactly one event; a `0→2` single-frame jump yields exactly 2 presses; a `0→1→2` two-frame sweep also yields exactly 2; presses spaced ≥ `multi_press_gap_ms`; no dwell requirement blocks a fast sweep.
3. **`test_vertical.py`** — a jump-shaped `dy` pulse yields exactly one press; a double bounce inside the refractory window yields one **plus one `Suppressed(REFRACTORY)`**; simultaneous lane + jump both fire.
4. **`test_running.py`** — synthetic sine `d` at 140 spm holds `RUNNING`; amplitude below `step_amp` does not and emits `Suppressed(STEP_AMPLITUDE)`; a one-leg twitch scores zero steps; **a jump and a squat inserted into a jogging sequence add no steps and do not trip the gate**; stopping trips WARNING at 1.5 s and LOCKED at 4.5 s; resuming recovers within 1 s; grace-clock freezing works across a 1.2 s jump + lane-change sequence.
5. **`test_features.py`** — the same gesture at two simulated distances (landmarks scaled about the centroid) yields near-identical `dy` and `d`. The regression test for the scale-invariance the whole design rests on.
6. **`test_telemetry.py`** — the sink drops rather than blocks when the queue is full and the drop counter increments; no module outside `telemetry.py` calls `print` (assert by AST-scanning the package — cheap, and it stays true as the code grows).
7. **Keyboard smoke test.** `tools/keytest.html` logs `keydown`/`keyup` with `event.code` and timestamps. Confirms keys land in the browser and is how you **measure whether both presses of a double survive**, separately from whether the game likes them.
8. **Manual acceptance:** survive 60 s of a real run; zero unintended presses during 60 s of stationary jogging; zero presses while Notepad is focused (and a `Suppressed(WINDOW_FOCUS)` line proving it); a deliberate stop locks within ~4.5 s; doubles visibly move you two lanes in-game.

Assertions target the **event stream**, not printed text — the console formatter is one more consumer, tested separately.

---

## 13. Build phases

Instrumentation is not a late phase. `events.py` and `telemetry.py` land in **P0** and every subsequent phase publishes into them, because the alternative is debugging P4 by adding print statements you then have to remove.

| Phase | Deliverable | Done when |
|---|---|---|
| **P0** | Scaffold, `requirements.txt`, model fetch, threaded camera + skeleton overlay, **`events.py` + `telemetry.py` + `-v/-vv/-vvv` plumbing**, `--probe` | 30 fps sustained; `--probe` reports a working camera; the ticker renders |
| **P1** | `features.py` + `filters.py` + HUD numerics | `dy` ≈ 0 standing, spikes on jump, dips on squat, stable as you walk toward/away; `d` visibly alternates when you jog |
| **P2** | `lanes.py` + `vertical.py` publishing events, `--dry-run` | Exactly one correct event per deliberate gesture; left↔right prints two `KEY_PRESS` lines |
| **P3** | `running.py`: step trigger, cadence, GateFSM (telemetry only, no suppression yet) | Jogging holds RUNNING; stopping walks WARNING → LOCKED on schedule; jumps/squats add no steps |
| **P4** | `keys.py`: sender, `PressQueue`, focus gate, F8 arm/disarm, gate enforcement, **full suppression logging** | `keytest.html` shows correct codes **and both presses of a double**; `multi_press_gap_ms` tuned against the live game; nothing fires when Notepad is focused, with a `Suppressed(WINDOW_FOCUS)` line to prove it |
| **P5** | `calibrate.py` (6 steps) + config persistence + validation gates | Fresh user, new room → playable without hand-editing a threshold |
| **P6** | `record.py` + full test suite + tuning pass on real fixtures | Tests green; a full run is playable; `--replay X -vvv` reproduces a session exactly |
| **P7** | `stats.py` + end-of-session summary | Steps, jumps, squats, single/double lane changes, active time, warned time, locked time, mean/peak cadence, longest unbroken run |
| **P8** *(optional)* | PyInstaller one-file exe, README with camera/screen diagram | Runs on a machine with no Python |

**Still out of scope:** auto-pause on lockout, hoverboard gesture, always-on-top overlay window, phone-as-webcam, Android-emulator target, browser-extension delivery, cross-workout progress charts.

---

## 14. Known risks

| Risk | Mitigation |
|---|---|
| **Sway while jogging fires phantom lane changes / doubles** | §6.3: hysteresis derived from measured sway, plus a calibration gate refusing configs where sway rivals lane width. **Highest-probability bug in the project** — and suppression logging (§10.3) is what tells phantom fires apart from missed ones |
| **Doubles register as singles** in the game | `PressQueue` with a tunable gap; `keytest.html` measures delivery; P4 exit criterion |
| **False lockouts during legitimate play** | §7.4 grace-clock freezing; floor at 60% of *your* cadence; `-vvv` shows every freeze window |
| **Debug output eats the latency budget** | §10.4: threaded sink, bounded queue, drop-on-overflow, 5 Hz ticker, zero prints in the pipeline |
| **Knees not visible** — cramped room or high camera | Auto-fallback to torso-bob with longer grace and a confidence flag; calibration says "step back" first |
| **Jump/squat mistaken for steps** | Knee *differential* is near-zero when both legs move together — structural immunity, not a threshold hack |
| **Camera FOV too narrow** to step far enough | Calibrated boundaries adapt to your range; HUD warns when `cx` nears the frame edge; README recommends a wide-angle webcam |
| **Second person in frame** — `num_poses=1` may hop between people | Lock onto the detection whose centroid is nearest the previous frame's; emit an event on large centroid jumps |
| **Input lag makes the game unplayable** | Latency budget + on-screen p95 + `-vvv` per-stage attribution; tune One Euro cutoff first, input resolution second |
| **Runaway key presses** into the wrong window | Focus gate + start-disarmed + F8 kill, each logging its refusal |
| **Exhaustion vs. game speed** | The game accelerates faster than a human can sustain. That's fine — it's a workout, not a high-score run. Session stats are the real scoreboard |
| **Poor/backlit lighting** | HUD shows mean landmark visibility; README asks for front lighting, no window behind you |

---

## 16. Implementation invariants

Non-negotiable rules for the implementation. Each exists because violating it breaks something later and expensively.

### 16.1 Inject the clock — never read it inside a state machine

Every FSM tick takes `now_ms` as a parameter. `lanes.py`, `vertical.py`, `running.py`, and `keys.py` must never call `time.monotonic()`, `time.time()`, or `time.perf_counter()` internally. The main loop reads the clock once per frame and passes it down; `--replay` passes the recorded timestamps instead.

This is what makes replay bit-for-bit deterministic and the test suite non-flaky. Refractory windows, the grace clock, the press-queue gap, and cadence windows are all time-dependent, so a single hidden clock call inside an FSM turns every timing test into a coin flip on a busy machine. Add a test that AST-scans those modules for time calls, alongside the `print` scan in §12.6.

### 16.2 No `print()` outside `telemetry.py`

See §10.4. Enforced by test.

### 16.3 Config is the only source of tunables

Every number in this document is a **default for `config.example.json`**, not a literal to paste into code. If a threshold, window, gap, or cutoff appears as a magic number in a module, that's a bug — tuning happens by editing config and replaying, and a hardcoded value silently ignores calibration.

### 16.4 Normalized coordinates everywhere

`cx`, `dy`, `scale`, `d`, and every threshold live in MediaPipe's normalized `[0,1]` space or in torso-lengths. Pixel coordinates appear **only** in `overlay.py`, at draw time. Mixing the two is how the app stops working when you change camera resolution.

### 16.5 Implement One Euro inline

There is no dependable maintained pip package for it. It's about 30 lines (a low-pass filter whose cutoff rises with the signal's derivative) and belongs in `filters.py` behind the same interface as the EMA fallback. Don't add a dependency for this.

### 16.6 Pin dependencies

`requirements.txt` gets exact versions, not ranges — `mediapipe==1.0.1` in particular, since the Tasks API surface has moved before. Python **3.12**; mediapipe has no 3.13+ wheels.

### 16.7 Model download

`scripts/fetch_model.py` fetches:

```
https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task
```

Swap `latest` for `1` to pin a specific revision. Skip the download if the file already exists; verify non-zero size; do not commit the `.task` file.

### 16.8 One phase at a time, with a human in the loop

Most "done when" criteria in §13 require a person standing in front of a camera — no agent can self-verify that a squat registers. So: build **one phase, stop, and hand back for manual verification** before starting the next. The pure-logic parts (§12.2–12.6) are the parts that can be verified without a body in the room, which is exactly why the synthetic-fixture tests exist and why `--replay` matters.

---

## 17. First message to give Claude Code

> Build phase P0 of the plan in PLAN.md: project scaffold, `requirements.txt` (mediapipe, opencv-python, numpy, pydirectinput-rgx, pynput, pygetwindow), a `scripts/fetch_model.py` that downloads `pose_landmarker_lite.task` into `models/`, and `camera.py` + `pose.py` + `events.py` + `telemetry.py` + a minimal `main.py` showing a mirrored 640×480 preview with the skeleton drawn and an FPS counter. Threaded capture with a single latest-frame slot, `CAP_DSHOW`, `BUFFERSIZE=1`, monotonic ms timestamps into `detect_async`. Wire up the event bus, the threaded telemetry sink with `-v/-vv/-vvv` and `--log-file`, and `--probe`. No key sending yet, no FSMs yet — but nothing in the pipeline may call `print()`. Python 3.12 venv; mediapipe has no 3.13 wheels.
