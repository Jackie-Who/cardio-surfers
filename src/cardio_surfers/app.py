"""The windowed application: menu, game, debug, calibration, settings.

One window, one pipeline, one event bus. Modes only change what is rendered
and which key dispatcher is active:

- GAME:  real key injection (unless --dry-run). Hold a **T-pose** to arm, which
  also sends the game's START key, so you can click into the game, step back,
  T-pose, and be running. The screen is deliberately sparse: the video, the
  lines, four key pads, cadence, and the two states that matter.
- DEBUG: dry-run dispatcher, always armed, plus every number and trace.
- CALIBRATE: features only; a CalibrationSession consumes them.
- SETTINGS: camera picker (real device names), draggable JUMP/DUCK lines,
  beep volume, key layout.

The window is resizable: every rect comes from layout.py, computed each frame
from the live size and clamped to a minimum. Closing the window (X) exits; the
back arrow and ESC return to the previous screen.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Deque

import cv2
import numpy as np

from . import layout as L
from . import overlay as ov
from . import theme as T
from .arming import TPoseDetector
from .audio import device_label, output_devices
from .audio import AudioAlerts
from .calibrate import CalibrationSession
from .camera import CameraThread, enumerate_cameras, name_of
from .config import DEFAULT_CONFIG_PATH, Config, load_config, update_config
from .game_overlay import (
    POSITIONS,
    GameOverlay,
    draw_edge_frame,
    focus_window,
    foreground_window,
    overlay_layout,
    overlay_theme,
    screen_size,
)
from .events import (
    Armed,
    Disarmed,
    Duck,
    LaneChange,
    EventBus,
    Event,
    GraceFreeze,
    Jump,
    KeyPress,
    Latency,
    Log,
    Severity,
    Step,
    Suppressed,
    Tick,
)
from .keys import ArmHotkey, DryRunSender, FocusGate, KeyDispatcher, RealKeySender
from .pipeline import Pipeline
from .pose import PoseTracker
from .record import PoseRecorder
from .stats import SessionStats
from .telemetry import TelemetrySink
from .timing import FpsCounter, MonotonicClock, RollingWindow
from .typography import BOLD, REGULAR, SEMIBOLD

MENU, GAME, DEBUG, CALIBRATE, SETTINGS = "menu", "game", "debug", "calibrate", "settings"

PAD_OF_ACTION = {"left": "left", "right": "right", "jump": "up", "duck": "down"}
PAD_GLYPH = {"left": "<", "up": "^", "down": "v", "right": ">"}


class App:
    def __init__(
        self,
        bus: EventBus,
        clock: MonotonicClock,
        config: Config,
        sink: TelemetrySink,
        args,
        config_path: str,
    ) -> None:
        self._bus = bus
        self._clock = clock
        self._config = config
        self._sink = sink
        self._args = args
        self._config_path = config_path

        ui = config.section("ui")
        self._win = ui["window_name"]
        self._w = int(ui["width"])
        self._h = int(ui["height"])
        self._min_w = int(ui["min_width"])
        self._min_h = int(ui["min_height"])
        self._flash_ms = float(ui["key_flash_ms"])
        self._gesture_flash_ms = float(ui["gesture_flash_ms"])
        self._last_gesture: tuple[str, float] = ("", -1e12)
        self._last_lane_ms = -1e12
        self._lane_flash_ms = float(ui["lane_flash_ms"])
        self._spark_ms = float(ui["sparkline_seconds"]) * 1000.0
        self._grab_px = int(ui["drag_grab_px"])
        self._vis_thr = float(config.get("pose.visibility_threshold"))

        self.mode = MENU
        self._history: list[str] = []
        self._running = True
        self._exit_code = 0

        # -- pipeline plumbing ------------------------------------------------
        self.camera = CameraThread(
            bus, clock,
            index=(args.camera if args.camera is not None
                   else int(config.get("camera.index"))),
            width=int(config.get("camera.width")),
            height=int(config.get("camera.height")),
            fps=float(config.get("camera.fps")),
            backend=config.get("camera.backend"),
            flip=bool(config.get("camera.flip")),
            process_width=int(config.get("camera.process_width")),
            backend_fallback=bool(config.get("camera.backend_fallback")),
        )
        self.tracker = PoseTracker(
            bus, clock,
            model_path=config.get("pose.model_path"),
            num_poses=int(config.get("pose.num_poses")),
            min_pose_detection_confidence=float(
                config.get("pose.min_pose_detection_confidence")),
            min_pose_presence_confidence=float(
                config.get("pose.min_pose_presence_confidence")),
            min_tracking_confidence=float(config.get("pose.min_tracking_confidence")),
        )

        mapping = config.section("keys.presets")[config.get("keys.mapping")]
        if args.dry_run:
            game_sender = DryRunSender()
            game_focus = None
        else:
            game_sender = RealKeySender(hold_ms=float(config.get("keys.hold_ms")))
            game_focus = FocusGate(config.get("keys.focus_regex"), exclude_title=self._win)
        self._game_dispatch = KeyDispatcher(bus, game_sender, game_focus, mapping)
        self._game_dispatch.armed = bool(config.get("keys.start_armed"))
        self._debug_dispatch = KeyDispatcher(bus, DryRunSender(), None, mapping)
        self._debug_dispatch.armed = True

        self.pipeline = Pipeline(bus, config, self._game_dispatch)
        self.stats = SessionStats(bus)
        self.audio = AudioAlerts(bus, config)
        self.recorder = PoseRecorder(args.record) if args.record else None
        self._send_start_on_arm = bool(config.get("keys.send_start_on_arm"))

        self.tpose = TPoseDetector(
            hold_ms=float(config.get("arming.tpose_hold_ms")),
            wrist_y_tol=float(config.get("arming.tpose_wrist_y_tol")),
            min_span_ratio=float(config.get("arming.tpose_min_span_ratio")),
            cooldown_ms=float(config.get("arming.cooldown_ms")),
            visibility_threshold=self._vis_thr,
        )

        self._hotkey = ArmHotkey(config.get("keys.arm_hotkey"), self._toggle_armed)
        self._hotkey_ok = self._hotkey.start()

        # -- UI state ---------------------------------------------------------
        self._mouse_click: tuple[int, int] | None = None
        self._mouse_pos = (0, 0)
        self._buttons: list[tuple[tuple[int, int, int, int], str]] = []
        self._feed: Deque[tuple[float, str, bool]] = deque(maxlen=int(ui["event_history"]))
        self._flashes: dict[str, float] = {}
        self._mapping = dict(mapping)
        self._action_of_key = {v: k for k, v in mapping.items()}
        self._spark_sy: Deque[tuple[float, float]] = deque(maxlen=240)
        self._spark_d: Deque[tuple[float, float]] = deque(maxlen=240)
        self._calib: CalibrationSession | None = None
        self._cam_list: list[dict[str, Any]] | None = None
        self._cam_scanning = False
        self._cam_dropdown_open = False
        self._drag: str | None = None
        self._pending_save: list[tuple[str, Any]] = []
        self._stage_rect: tuple[int, int, int, int] | None = None
        self._volume_rect: tuple[int, int, int, int] | None = None
        self._back_rect = (0, 0, 0, 0)
        self._layout = L.build(self._w, self._h)
        # Overlay mode: the same Play HUD, drawn on a click-through window
        # sitting on top of the game.
        self.overlay = GameOverlay(self._win, T.OVERLAY_KEY)
        self._overlay_on = False
        self._overlay_rects: dict = {}
        self._overlay_position = str(config.get("ui.overlay_position"))
        # The window that had focus before ours did -- almost always the game.
        # Remembered continuously so overlay mode can hand focus straight back.
        self._game_hwnd = 0
        self._game_title = ""
        self._windowed_size = (self._w, self._h)
        self._audio_dropdown_open = False
        self._fps = FpsCounter()
        self._fps_now = 0.0
        self._latency = RollingWindow(int(config.get("telemetry.latency_window")))
        self._last_pose_seq = -1
        self._last_tick_ms = -1e12
        self._ticker_period_ms = 1000.0 / float(config.get("telemetry.ticker_hz"))
        self._last_render_ms = -1e12
        self._render_period_ms = 1000.0 / float(config.get("camera.fps"))

        bus.subscribe(self._on_event)

    # -- bus subscriber --------------------------------------------------------

    def _on_event(self, event: Event) -> None:
        if isinstance(event, KeyPress):
            action = self._action_of_key.get(event.key, event.key)
            self._flashes[PAD_OF_ACTION.get(action, action)] = event.t_ms
        elif isinstance(event, Jump):
            self._last_gesture = ("JUMP", event.t_ms)
        elif isinstance(event, Duck):
            self._last_gesture = ("DUCK", event.t_ms)
        elif isinstance(event, LaneChange):
            self._last_lane_ms = event.t_ms
        if isinstance(event, (Tick, Step, Latency, GraceFreeze)):
            return
        if isinstance(event, Log):
            if event.severity is Severity.INFO:
                return
            label, warn = f"{event.severity.value.upper()} {event.message}", True
        elif isinstance(event, Suppressed):
            label = f"SUPPRESSED {event.reason.value} {event.detail or ''}"
            warn = True
        else:
            body = " ".join(f"{k}={v}" for k, v in event.payload().items())
            label, warn = f"{event.NAME} {body}".strip(), False
        self._feed.append((event.t_ms, label[:72], warn))

    def _toggle_armed(self, source: str = "hotkey") -> None:
        now_ms = self._clock.now_ms()
        # A T-pose arms and sends START; both are worthless if focus drifted
        # off the game, so put it back first.
        if self._overlay_on:
            self._refocus_game()
        armed = not self._game_dispatch.armed
        self._game_dispatch.armed = armed
        self.pipeline.set_active(armed, now_ms)
        if armed:
            self._bus.publish(Armed(t_ms=now_ms))
            if self._send_start_on_arm and self.mode == GAME:
                # Arming and starting the game are the same gesture.
                self.pipeline.send_start(now_ms)
        else:
            self._bus.publish(Disarmed(t_ms=now_ms))
        self._bus.publish(
            Log(t_ms=now_ms, severity=Severity.INFO,
                message=f"{'armed' if armed else 'disarmed'} by {source}")
        )

    # -- mode switching --------------------------------------------------------

    def _enter(self, mode: str, remember: bool = True) -> None:
        if mode != GAME and self._overlay_on:
            self._exit_overlay()
        if remember and mode != self.mode:
            self._history.append(self.mode)
        self.mode = mode
        self._mouse_click = None
        self._drag = None
        now_ms = self._clock.now_ms()
        if mode == GAME:
            self.pipeline.set_dispatcher(self._game_dispatch)
            self.pipeline.set_active(self._game_dispatch.armed, now_ms)
        elif mode == DEBUG:
            self.pipeline.set_dispatcher(self._debug_dispatch)
            # Debug is always live, so it primes immediately: the bar starts
            # full there too rather than opening in a locked state.
            self.pipeline.set_active(False, now_ms)
            self.pipeline.set_active(True, now_ms)
        elif mode == CALIBRATE:
            self._calib = CalibrationSession(self._config)
        elif mode == SETTINGS:
            self._cam_dropdown_open = False
            self._start_camera_scan()
        if mode not in (GAME, DEBUG):
            self.pipeline.set_active(False, now_ms)
        self.pipeline.queue.clear()

    def _remember_game_window(self) -> None:
        """Track the last focused window that is not ours."""
        hwnd, title = foreground_window()
        if hwnd and title and title != self._win:
            self._game_hwnd, self._game_title = hwnd, title

    def _refocus_game(self) -> None:
        """Hand focus back to the game.

        Overlay mode only works if the game keeps keyboard focus: the T-pose
        sends START and WASD to whatever is focused, and clicking our own
        button is exactly the moment focus would otherwise sit on us.
        """
        if not self._overlay_on or not self._game_hwnd:
            return
        if not focus_window(self._game_hwnd):
            self._bus.publish(
                Log(t_ms=self._clock.now_ms(), severity=Severity.WARN,
                    message=f"could not focus {self._game_title!r}",
                    detail="click the game once to give it focus"))

    def _enter_overlay(self) -> bool:
        """Go click-through and full-screen, on top of the game."""
        if self._overlay_on:
            return True
        self._windowed_size = (self._w, self._h)
        width, height = screen_size()
        cv2.setWindowProperty(self._win, cv2.WND_PROP_AUTOSIZE, 0)
        cv2.resizeWindow(self._win, width, height)
        cv2.moveWindow(self._win, 0, 0)
        cv2.waitKey(1)                       # let the move/resize land first
        if not self.overlay.attach():
            cv2.resizeWindow(self._win, *self._windowed_size)
            self._bus.publish(
                Log(t_ms=self._clock.now_ms(), severity=Severity.WARN,
                    message="could not enter overlay mode",
                    detail=self.overlay.error or "unknown"))
            return False
        self._overlay_on = True
        self._w, self._h = width, height
        self._layout = L.build(width, height)
        # Hand focus straight to the game: we still hold it from the click that
        # got us here, and Windows only permits the handover while we do.
        self._refocus_game()
        self._bus.publish(
            Log(t_ms=self._clock.now_ms(), severity=Severity.INFO,
                message="overlay mode on",
                detail=f"focus -> {self._game_title or 'no game window seen yet'}"))
        return True

    def _exit_overlay(self) -> None:
        if not self._overlay_on:
            return
        self.overlay.detach()
        self._overlay_on = False
        w, h = self._windowed_size
        cv2.resizeWindow(self._win, w, h)
        cv2.moveWindow(self._win, int(self._config.get("ui.window_x")),
                       int(self._config.get("ui.window_y")))
        self._w, self._h = w, h
        self._layout = L.build(w, h)
        self._bus.publish(
            Log(t_ms=self._clock.now_ms(), severity=Severity.INFO,
                message="overlay mode off"))

    def _go_back(self) -> None:
        previous = self._history.pop() if self._history else MENU
        self._enter(previous, remember=False)

    def _start_camera_scan(self) -> None:
        if self._cam_scanning:
            return
        self._cam_scanning = True
        self._cam_list = None

        def scan() -> None:
            try:
                self._cam_list = enumerate_cameras(
                    max_index=int(self._config.get("probe.max_camera_index")),
                    backend=self._config.get("camera.backend"),
                    skip=self.camera.index,
                )
            finally:
                self._cam_scanning = False

        threading.Thread(target=scan, name="cam-scan", daemon=True).start()

    # -- main loop -------------------------------------------------------------

    def run(self) -> int:
        if not self.camera.start_any(
            int(self._config.get("probe.max_camera_index")),
            float(self._config.get("camera.first_frame_timeout_s")),
        ):
            return 1
        if not self.tracker.start():
            self.camera.stop()
            return 1

        cv2.namedWindow(self._win, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self._win, self._w, self._h)
        cv2.moveWindow(self._win, int(self._config.get("ui.window_x")),
                       int(self._config.get("ui.window_y")))
        cv2.setMouseCallback(self._win, self._on_mouse)

        self._bus.publish(
            Log(t_ms=self._clock.now_ms(), severity=Severity.INFO, message="app started",
                detail=f"hotkey={'ok' if self._hotkey_ok else 'FAILED'}")
        )
        try:
            while self._running:
                self._step()
        except KeyboardInterrupt:
            pass
        finally:
            self._shutdown()
        return self._exit_code

    def _window_closed(self) -> bool:
        try:
            return cv2.getWindowProperty(self._win, cv2.WND_PROP_VISIBLE) < 1
        except cv2.error:
            return True

    def _sync_window_size(self) -> None:
        """Track the user's resize, enforcing the minimum in both directions."""
        if self._overlay_on:
            # The overlay owns its geometry; a resize sync here would fight it.
            self.overlay.raise_above_game()
            return
        try:
            _, _, w, h = cv2.getWindowImageRect(self._win)
        except cv2.error:
            return
        if w <= 0 or h <= 0:
            return
        clamped_w, clamped_h = max(w, self._min_w), max(h, self._min_h)
        if (clamped_w, clamped_h) != (w, h):
            cv2.resizeWindow(self._win, clamped_w, clamped_h)
        if (clamped_w, clamped_h) != (self._w, self._h):
            self._w, self._h = clamped_w, clamped_h
            self._layout = L.build(self._w, self._h)

    def _step(self) -> None:
        now_ms = self._clock.now_ms()

        frame = self.camera.read()
        if frame is not None:
            self._fps_now = self._fps.tick(now_ms)
            self.tracker.submit(frame)

        pose = self.tracker.latest()
        new_pose = pose is not None and pose.seq != self._last_pose_seq
        if new_pose:
            self._last_pose_seq = pose.seq
            self._latency.add(pose.latency_ms)
            if self.recorder is not None:
                self.recorder.record(pose)

            if self.mode in (GAME, DEBUG):
                if self.tpose.tick(pose, now_ms) and self.mode == GAME:
                    self._toggle_armed(source="T-pose")
                feats = self.pipeline.tick(pose, now_ms)
                if feats.ok:
                    self._spark_sy.append((now_ms, feats.shoulder_y))
                    self._spark_d.append((now_ms, feats.d))
            elif self.mode == CALIBRATE and self._calib is not None:
                feats = self.pipeline.observe(pose, now_ms)
                status = self._calib.tick(feats if feats.ok else None, now_ms)
                for event in self._calib.events:
                    self._bus.publish(event)
                self._calib.events.clear()
                if status.finished and self._calib.result is not None:
                    self._save_calibration(self._calib.result)
                    self._calib = None
                    self._enter(MENU, remember=False)
            else:
                self.pipeline.observe(pose, now_ms)

        if now_ms - self._last_tick_ms >= self._ticker_period_ms:
            self._last_tick_ms = now_ms
            state = {"mode": self.mode, "cam": self.camera.index,
                     "fps": int(round(self._fps_now))}
            if self.mode in (GAME, DEBUG):
                state.update(self.pipeline.hud_state(now_ms))
            state["p95"] = int(round(self._latency.p95))
            state["drops"] = self._sink.dropped
            self._bus.publish(Tick(t_ms=now_ms, state=state))

        self._flush_pending_saves()

        if now_ms - self._last_render_ms >= self._render_period_ms:
            self._last_render_ms = now_ms
            self._sync_window_size()
            self._remember_game_window()
            canvas = self._render(now_ms, pose)
            cv2.imshow(self._win, canvas)
            key = cv2.waitKey(1) & 0xFF
            if self._window_closed():
                self._running = False
                return
            self._handle_key(key)
            self._handle_click()
        elif frame is None and not new_pose:
            time.sleep(0.001)

    def _handle_key(self, key: int) -> None:
        if key == 27:
            if self.mode == MENU:
                self._running = False
            else:
                self._go_back()
        elif key == ord(" ") and self.mode == GAME:
            self._toggle_armed(source="space")

    def _handle_click(self) -> None:
        if self._mouse_click is None:
            return
        x, y = self._mouse_click
        self._mouse_click = None
        if self.mode != MENU and ov.hit(self._back_rect, x, y):
            self._go_back()
            return
        for rect, action in self._buttons:
            if ov.hit(rect, x, y):
                overlaid = self._overlay_on
                self._on_button(action)
                if overlaid:
                    # Clicking our HUD must not leave focus on us.
                    self._refocus_game()
                return

    def _on_button(self, action: str) -> None:
        if action.startswith("goto:"):
            target = action.split(":", 1)[1]
            if target == "quit":
                self._running = False
            elif target == "back":
                self._go_back()
            else:
                self._enter(target)
        elif action == "arm":
            self._toggle_armed(source="button")
        elif action == "cam-dropdown":
            self._cam_dropdown_open = not self._cam_dropdown_open
            self._audio_dropdown_open = False
        elif action.startswith("cam:"):
            self._select_camera(int(action.split(":")[1]))
        elif action == "toggle-keys":
            current = self._config.get("keys.mapping")
            self._set_mapping("wasd" if current == "arrows" else "arrows")
        elif action == "toggle-audio":
            enabled = not bool(self._config.get("running.audio_warnings"))
            update_config(self._config_path, "running.audio_warnings", enabled)
            self._reload_config()
            self.audio.set_enabled(enabled)
        elif action == "test-beep":
            self.audio.test_beep()
        elif action == "audio-dropdown":
            self._audio_dropdown_open = not self._audio_dropdown_open
            self._cam_dropdown_open = False
        elif action.startswith("audio-dev:"):
            raw = action.split(":", 1)[1]
            index = None if raw == "default" else int(raw)
            self._audio_dropdown_open = False
            self.audio.set_device(index)
            update_config(self._config_path, "running.audio_device", index)
            self._reload_config()
            self.audio.test_beep()
        elif action.startswith("overlay-pos:"):
            self._overlay_position = action.split(":", 1)[1]
            update_config(self._config_path, "ui.overlay_position",
                          self._overlay_position)
            self._reload_config()
        elif action == "overlay-on":
            self._enter_overlay()
        elif action == "overlay-off":
            self._exit_overlay()
        elif action == "rescan":
            self._start_camera_scan()
        elif action in ("jump-", "jump+", "duck-", "duck+"):
            # "+" raises the line on screen, "-" lowers it: the button matches
            # the direction the line moves.
            self._nudge_line(action[:4], -1.0 if action.endswith("+") else 1.0)

    # -- settings actions ------------------------------------------------------

    def _select_camera(self, index: int) -> None:
        self._cam_dropdown_open = False
        if index == self.camera.index:
            return
        timeout = float(self._config.get("camera.first_frame_timeout_s"))
        if self.camera.switch(index, timeout):
            update_config(self._config_path, "camera.index", index)
            self._reload_config()
            self._bus.publish(
                Log(t_ms=self._clock.now_ms(), severity=Severity.INFO,
                    message=f"switched to {name_of(index)}", detail="saved"))
        else:
            self._bus.publish(
                Log(t_ms=self._clock.now_ms(), severity=Severity.WARN,
                    message=f"{name_of(index)} gave no frames; keeping "
                            f"{name_of(self.camera.index)}"))

    def _set_line(self, which: str, value: float, persist: bool) -> None:
        lo, hi = (float(v) for v in self._config.get("vertical.line_range"))
        value = round(max(lo, min(hi, value)), 4)
        fsm = self.pipeline.vertical
        jump_y = value if which == "jump" else (fsm.jump_y or 0.0)
        duck_y = value if which == "duck" else (fsm.duck_y or 0.0)
        if jump_y >= duck_y:
            return
        fsm.set_lines(jump_y, duck_y)
        if persist:
            self._pending_save.append((f"vertical.{which}_y", value))

    def _nudge_line(self, which: str, direction: float) -> None:
        step = float(self._config.get("vertical.nudge")) * direction
        fsm = self.pipeline.vertical
        current = fsm.jump_y if which == "jump" else fsm.duck_y
        if current is None:
            return
        self._set_line(which, current + step, persist=True)

    def _set_volume(self, value: float, persist: bool) -> None:
        value = round(max(0.0, min(1.0, value)), 2)
        self.audio.set_volume(value)
        if persist:
            self._pending_save.append(("running.audio_volume", value))

    def _flush_pending_saves(self) -> None:
        if not self._pending_save:
            return
        pending, self._pending_save = self._pending_save, []
        seen: dict[str, Any] = {}
        for key, value in pending:
            seen[key] = value
        for key, value in seen.items():
            update_config(self._config_path, key, value)
        self._reload_config()
        self._bus.publish(
            Log(t_ms=self._clock.now_ms(), severity=Severity.INFO,
                message="saved: " + ", ".join(f"{k.split('.')[-1]}={v}"
                                              for k, v in seen.items())))

    def _set_mapping(self, name: str) -> None:
        update_config(self._config_path, "keys.mapping", name)
        self._reload_config()
        mapping = self._config.section("keys.presets")[name]
        self._game_dispatch.set_mapping(mapping)
        self._debug_dispatch.set_mapping(mapping)
        self._mapping = dict(mapping)
        self._action_of_key = {v: k for k, v in mapping.items()}

    def _save_calibration(self, result: dict[str, Any]) -> None:
        for dotted, value in result.items():
            update_config(self._config_path, dotted, value)
        self._reload_config()
        current = self.pipeline._dispatcher
        self.pipeline = Pipeline(self._bus, self._config, current)
        self._bus.publish(
            Log(t_ms=self._clock.now_ms(), severity=Severity.INFO,
                message="calibration saved", detail=self._config_path))

    def _reload_config(self) -> None:
        self._config, _ = load_config(
            self._config_path if self._config_path != DEFAULT_CONFIG_PATH else None)

    # -- mouse -----------------------------------------------------------------

    def _grab_target(self, x: int, y: int) -> str | None:
        if self.mode != SETTINGS:
            return None
        if self._volume_rect and ov.hit(self._volume_rect, x, y):
            return "volume"
        rect = self._stage_rect
        if rect is None:
            return None
        rx, ry, rw, rh = rect
        if not (rx <= x <= rx + rw):
            return None
        fsm = self.pipeline.vertical
        for which, value in (("jump", fsm.jump_y), ("duck", fsm.duck_y)):
            if value is None:
                continue
            if abs(y - (ry + value * rh)) <= self._grab_px:
                return which
        return None

    def _apply_drag(self, x: int, y: int, persist: bool) -> None:
        if self._drag == "volume" and self._volume_rect:
            vx, _, vw, _ = self._volume_rect
            self._set_volume((x - vx) / max(1, vw), persist)
        elif self._drag in ("jump", "duck") and self._stage_rect:
            _, ry, _, rh = self._stage_rect
            self._set_line(self._drag, (y - ry) / max(1, rh), persist)

    def _on_mouse(self, event: int, x: int, y: int, flags: int, param) -> None:
        self._mouse_pos = (x, y)
        if event == cv2.EVENT_LBUTTONDOWN:
            target = self._grab_target(x, y)
            if target is not None:
                self._drag = target
                self._apply_drag(x, y, persist=False)
            else:
                self._mouse_click = (x, y)
        elif event == cv2.EVENT_MOUSEMOVE and self._drag is not None:
            self._apply_drag(x, y, persist=False)
        elif event == cv2.EVENT_LBUTTONUP and self._drag is not None:
            self._apply_drag(x, y, persist=True)
            self._drag = None

    # -- rendering -------------------------------------------------------------

    def _render(self, now_ms: float, pose) -> np.ndarray:
        lay = self._layout
        canvas = np.full((lay.height, lay.width, 3), T.BG, np.uint8)
        self._canvas = canvas
        self._buttons = []
        self._stage_rect = None
        self._volume_rect = None
        if self._overlay_on and self.mode == GAME:
            return self._render_overlay(now_ms, pose)
        if self.mode == MENU:
            self._render_menu(canvas, lay, now_ms, pose)
        elif self.mode == SETTINGS:
            self._render_settings(canvas, lay, now_ms, pose)
        elif self.mode == CALIBRATE:
            self._render_calibrate(canvas, lay, now_ms, pose)
        else:
            self._render_play(canvas, lay, now_ms, pose, debug=self.mode == DEBUG)
        return canvas

    def _button(self, rect, label, action, lay: L.Layout, accent=None, subtle=False):
        hover = ov.hit(rect, *self._mouse_pos)
        ov.draw_button(self._canvas, rect, label, lay.theme, hover=hover,
                       accent=accent, subtle=subtle)
        self._buttons.append((rect, action))

    def _blit_camera(self, canvas, pose, area, lay: L.Layout, *, chrome=True):
        """Draw the frame fitted inside `area`, preserving aspect ratio."""
        frame = self.camera.peek()
        th = lay.theme
        ax, ay, aw, ah = area
        if chrome:
            ov.card(canvas, area, th, fill=T.SURFACE, border=T.BORDER)
        if frame is not None:
            img = frame.image
            ih, iw = img.shape[:2]
            fit = min(aw / iw, ah / ih)
            w, h = max(1, int(iw * fit)), max(1, int(ih * fit))
            rx, ry = ax + (aw - w) // 2, ay + (ah - h) // 2
            if (w, h) != (iw, ih):
                img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
            canvas[ry:ry + h, rx:rx + w] = img
            rect = (rx, ry, w, h)
        else:
            rect = area
            ov.text_centred(canvas, "no camera", ax + aw // 2, ay + ah // 2,
                            th.font(T.T_LABEL), T.DANGER, SEMIBOLD)
        if pose is not None and pose.present:
            ov.draw_skeleton(canvas, pose, rect, self._vis_thr, th)
        elif frame is not None:
            ov.draw_stage_banner(canvas, rect, "NO BODY DETECTED", T.DANGER, th)
        return rect

    def _render_header(self, canvas, lay: L.Layout, title: str) -> None:
        th = lay.theme
        hx, hy, hw, hh = lay.header
        cv2.rectangle(canvas, (hx, hy), (hx + hw, hy + hh), T.SURFACE, -1)
        cv2.line(canvas, (hx, hy + hh - 1), (hx + hw, hy + hh - 1), T.BORDER, 1)

        left = th.px(T.S_5)
        if self.mode != MENU:
            self._back_rect = lay.back
            ov.draw_back_arrow(canvas, lay.back, th, ov.hit(lay.back, *self._mouse_pos))
            left = lay.back[0] + lay.back[2] + th.px(T.S_3)
        else:
            self._back_rect = (0, 0, 0, 0)
        ov.text_mid(canvas, title, left, hy + hh // 2, th.font(T.T_TITLE), T.TEXT, SEMIBOLD)

        p95 = self._latency.p95
        colour = T.TEXT_MUTED if p95 < 120 else (T.WARN if p95 < 150 else T.DANGER)
        ov.text_right(canvas, f"{self._fps_now:.0f} fps    {p95:.0f} ms",
                      hx + hw - th.px(T.S_5), hy + hh // 2, th.font(T.T_SMALL), colour)

    def _render_feed(self, canvas, lay: L.Layout, now_ms: float, x: int, y: int,
                     limit: int | None = None, width: int | None = None) -> None:
        th = lay.theme
        size = th.font(T.T_MICRO)
        avail = width if width is not None else lay.width - x - th.px(T.S_4)
        for t_ms, label, warn in list(self._feed)[-(limit or len(self._feed)):]:
            age = max(0.0, (now_ms - t_ms) / 1000.0)
            line = ov.ellipsize(f"{age:4.1f}s   {label}", size, avail)
            ov.text(canvas, line, (x, y), size, T.WARN if warn else T.TEXT_MUTED)
            y += th.px(16)

    # -- screens ---------------------------------------------------------------

    def _render_menu(self, canvas, lay: L.Layout, now_ms, pose) -> None:
        th = lay.theme
        self._render_header(canvas, lay, "Cardio Surfers")
        self._blit_camera(canvas, pose, lay.thumb, lay)

        px, py, pw, _ = lay.panel
        calibrated = self._config.get("lanes.boundary_left") is not None
        ov.draw_pill(canvas, (px, py + th.px(T.S_2), th.px(180), th.px(28)),
                     "CALIBRATED" if calibrated else "NOT CALIBRATED", th,
                     T.ACCENT if calibrated else T.WARN)

        w, h, gap = min(pw, th.px(300)), th.px(48), th.px(T.S_3)
        y = py + th.px(66)
        for label, action, accent in (
            ("Play", "goto:game", T.ACCENT),
            ("Debug", "goto:debug", None),
            ("Calibrate", "goto:calibrate", None),
            ("Settings", "goto:settings", None),
            ("Quit", "goto:quit", None),
        ):
            self._button((px, y, w, h), label, action, lay, accent)
            y += h + gap

        ov.text(canvas, "T-pose to arm and start      F8 / Space arm      ESC back",
                (px, lay.height - th.px(T.S_5)), th.font(T.T_MICRO), T.TEXT_MUTED)
        tx, ty, tw, tht = lay.thumb
        self._render_feed(canvas, lay, now_ms, tx, ty + tht + th.px(T.S_5),
                          limit=5, width=tw)

    def _render_settings(self, canvas, lay: L.Layout, now_ms, pose) -> None:
        th = lay.theme
        self._render_header(canvas, lay, "Settings")
        stage = self._blit_camera(canvas, pose, lay.thumb, lay)
        self._stage_rect = stage
        fsm = self.pipeline.vertical
        ov.draw_threshold_lines(canvas, stage, fsm.jump_y, fsm.duck_y, th,
                                grabbed=self._drag, handles=True)
        feats = self.pipeline.last_features
        if feats is not None and feats.ok:
            ov.draw_shoulder_line(canvas, stage, feats.shoulder_y,
                                  feats.shoulder_x0, feats.shoulder_x1, "NEUTRAL", th)

        px, py, pw, ph = lay.panel
        w = min(pw, th.px(410))
        h, gap = th.px(38), th.px(T.S_4)
        y = py + th.px(T.S_2)
        back_rect = (px, py + ph - h, th.px(140), h)
        limit = back_rect[1] - th.px(T.S_3)

        def label(t, yy):
            ov.text(canvas, t, (px, yy), th.font(T.T_SMALL), T.TEXT_DIM)
            return yy + th.px(18)

        def row(fits: int = 1) -> bool:
            return y + fits * (h + th.px(T.S_1)) <= limit

        def option(text_, action, yy):
            self._button((px, yy, w, h),
                         ov.ellipsize(text_, th.font(T.T_BODY), w - th.px(T.S_5)),
                         action, lay, subtle=True)
            return yy + h + th.px(T.S_1)

        # An open dropdown owns the column. Drawing the rest underneath it is
        # what pushed controls off the panel and under the Back button.
        y = label("CAMERA", y)
        self._button((px, y, w, h), f"{name_of(self.camera.index)}      v",
                     "cam-dropdown", lay)
        y += h + th.px(T.S_1)
        if self._cam_dropdown_open:
            if self._cam_scanning or self._cam_list is None:
                ov.draw_button(canvas, (px, y, w, h), "scanning...", th, subtle=True)
            else:
                for cam in self._cam_list:
                    if not row(2):          # leave space for Rescan
                        break
                    name = cam.get("name") or name_of(cam["index"])
                    if cam["index"] == self.camera.index:
                        name += "      in use"
                    y = option(name, f"cam:{cam['index']}", y)
                if row():
                    option("Rescan", "rescan", y)
            self._button(back_rect, "Back", "goto:back", lay, subtle=True)
            return
        y += gap

        y = label("SOUND OUTPUT", y)
        self._button((px, y, w, h), f"{device_label(self.audio.device)}      v",
                     "audio-dropdown", lay)
        y += h + th.px(T.S_1)
        if self._audio_dropdown_open:
            for device in output_devices():
                if not row():
                    break
                name = device["name"]
                if device["index"] == self.audio.device:
                    name += "      in use"
                key = "default" if device["index"] is None else str(device["index"])
                y = option(name, f"audio-dev:{key}", y)
            self._button(back_rect, "Back", "goto:back", lay, subtle=True)
            return
        y += gap

        audio = bool(self._config.get("running.audio_warnings"))
        y = label(f"BEEP VOLUME      {self.audio.volume * 100:.0f}%", y)
        self._volume_rect = (px, y, w, th.px(22))
        ov.draw_slider(canvas, self._volume_rect, self.audio.volume, th,
                       hover=self._drag == "volume"
                       or ov.hit(self._volume_rect, *self._mouse_pos))
        y += th.px(22) + th.px(T.S_3)
        cols = L.split((px, y, w, h), [0.5, 0.5], th.px(T.S_2))
        self._button(cols[0], "Warnings on" if audio else "Warnings off",
                     "toggle-audio", lay, T.ACCENT if audio else None)
        self._button(cols[1], "Test sound", "test-beep", lay, subtle=True)
        y += h + gap

        y = label("KEY LAYOUT", y)
        self._button((px, y, th.px(200), h), self._config.get("keys.mapping").upper(),
                     "toggle-keys", lay)
        y += h + gap

        y = label("OVERLAY HUD POSITION", y)
        cell_h = th.px(32)
        for row_i in range(2):
            cells = L.split((px, y, w, cell_h), [1, 1, 1], th.px(T.S_2))
            for col_i, cell in enumerate(cells):
                key, name = POSITIONS[row_i * 3 + col_i]
                chosen = key == self._overlay_position
                self._button(cell, name, f"overlay-pos:{key}", lay,
                             T.ACCENT if chosen else None, subtle=not chosen)
            y += cell_h + th.px(T.S_1)

        self._button(back_rect, "Back", "goto:back", lay, subtle=True)

        # Right column: the two lines, dragged on the preview or nudged here.
        sx, sy, sw, sh = lay.thumb
        ry = sy + sh + th.px(T.S_5)
        ov.text(canvas, "Drag the lines on the preview", (sx, ry),
                th.font(T.T_SMALL), T.TEXT_DIM)
        ry += th.px(T.S_5)
        for name, key, value, colour in (("Jump", "jump", fsm.jump_y, T.ACCENT),
                                         ("Duck", "duck", fsm.duck_y, T.WARN)):
            btn = th.px(34)
            ov.text_mid(canvas, name, sx, ry + btn // 2, th.font(T.T_BODY), colour,
                        SEMIBOLD)
            self._button((sx + th.px(62), ry, btn, btn), "-", f"{key}-", lay, subtle=True)
            ov.text_centred(canvas, f"{value:.2f}" if value is not None else "--",
                            sx + th.px(62) + btn + th.px(30), ry + btn // 2,
                            th.font(T.T_BODY), T.TEXT)
            self._button((sx + th.px(62) + btn + th.px(60), ry, btn, btn), "+",
                         f"{key}+", lay, subtle=True)
            ry += btn + th.px(T.S_2)
        self._render_feed(canvas, lay, now_ms, sx, ry + th.px(T.S_3), limit=4, width=sw)

    def _render_calibrate(self, canvas, lay: L.Layout, now_ms, pose) -> None:
        th = lay.theme
        self._render_header(canvas, lay, "Calibration")
        stage = self._blit_camera(canvas, pose, lay.stage, lay)
        if self._calib is None or self._calib.last_status is None:
            return
        status = self._calib.last_status
        rx, ry, rw, _ = lay.rail

        ov.text(canvas, f"STEP {status.step + 1} OF {status.total_steps}",
                (rx, ry + th.px(T.S_2)), th.font(T.T_MICRO), T.TEXT_MUTED)
        ov.text(canvas, status.prompt, (rx, ry + th.px(30)),
                th.font(T.T_LABEL), T.TEXT, SEMIBOLD)
        detail = status.detail
        if status.phase == "COUNTDOWN":
            detail = f"starting in {status.seconds_left + 0.99:.0f}"
        elif status.phase == "CAPTURE":
            detail = f"hold    {status.seconds_left:.1f}s"
        ov.text(canvas, detail, (rx, ry + th.px(56)), th.font(T.T_SMALL), T.TEXT_DIM)

        bar = (rx, ry + th.px(84), rw, th.px(6))
        ov.rounded_rect(canvas, bar, th.px(3), T.TRACK, -1)
        if status.progress > 0:
            ov.rounded_rect(canvas, (bar[0], bar[1], int(rw * status.progress), bar[3]),
                            th.px(3), T.ACCENT, -1)
        if status.message:
            ov.draw_stage_banner(canvas, stage, status.message, T.WARN, th)
        ov.text(canvas, "ESC or back cancels", (rx, ry + th.px(108)),
                th.font(T.T_MICRO), T.TEXT_MUTED)
        self._render_feed(canvas, lay, now_ms, rx, ry + th.px(140), limit=5, width=rw)

    def _play_state(self, now_ms):
        return (self.pipeline.hud_state(now_ms), self.pipeline.last_features,
                self.pipeline.vertical, self.pipeline._dispatcher.armed)

    def _draw_stage_overlays(self, canvas, stage, lay, now_ms, state, feats, fsm,
                             *, lane_labels=True) -> None:
        th = lay.theme
        lane_idx = {"LEFT": 0, "CENTRE": 1, "RIGHT": 2}[state["lane"]]
        flash = max(0.0, 1.0 - (now_ms - self._last_lane_ms) / self._lane_flash_ms)
        ov.draw_lane_lines(canvas, stage, self.pipeline.lanes.boundary_left,
                           self.pipeline.lanes.boundary_right, state["cx"],
                           lane_idx, th, labels=lane_labels, flash=flash)
        ov.draw_threshold_lines(canvas, stage, fsm.jump_y, fsm.duck_y, th,
                                labels=lane_labels)
        if feats is not None and feats.ok:
            ov.draw_shoulder_line(canvas, stage, feats.shoulder_y,
                                  feats.shoulder_x0, feats.shoulder_x1,
                                  state["vertical"], th)
        label, at_ms = self._last_gesture
        if now_ms - at_ms < self._gesture_flash_ms:
            ov.draw_gesture_flash(canvas, stage, label,
                                  T.ACCENT if label == "JUMP" else T.WARN, th)
        progress = self.tpose.hold_progress(now_ms)
        if progress > 0.0:
            ov.draw_hold_progress(canvas, stage, progress, "HOLD T-POSE", th)

    def _render_play(self, canvas, lay: L.Layout, now_ms, pose, debug: bool) -> None:
        th = lay.theme
        state, feats, fsm, armed = self._play_state(now_ms)

        self._render_header(canvas, lay, "Debug" if debug else "Play")
        stage = self._blit_camera(canvas, pose, lay.stage, lay)
        self._draw_stage_overlays(canvas, stage, lay, now_ms, state, feats, fsm)

        rx, ry, rw, _ = lay.rail
        y = ry

        gate = state["gate"]
        gate_colour = {"RUNNING": T.ACCENT, "WARNING": T.WARN}.get(gate, T.DANGER)
        gate_label = gate.title()
        if gate == "WARNING":
            left = self.pipeline.gate.warning_remaining_ms(now_ms) / 1000
            gate_label = f"Keep moving   {left:.1f}s"
        ov.draw_pill(canvas, (rx, y, rw, th.px(44)), gate_label, th, gate_colour,
                     filled=gate != "RUNNING")
        y += th.px(44 + T.S_3)

        if debug:
            ov.draw_pill(canvas, (rx, y, th.px(140), th.px(30)), "Dry run", th, T.INFO)
        else:
            ov.draw_pill(canvas, (rx, y, th.px(140), th.px(30)),
                         "Armed" if armed else "Disarmed", th,
                         T.ACCENT if armed else T.DANGER, filled=armed)
            self._button((rx + rw - th.px(110), y, th.px(110), th.px(30)),
                         "Disarm" if armed else "Arm", "arm", lay,
                         T.DANGER if armed else T.ACCENT)
        y += th.px(30 + T.S_4)

        ov.draw_cadence_bar(canvas, (rx, y, rw, th.px(28)), state["spm"],
                            state["floor"], th, held=state.get("held", False))
        y += th.px(28 + T.S_4)

        ov.draw_key_pads(canvas, (rx, y, rw, th.px(58)), self._flashes, now_ms,
                         self._flash_ms, th,
                         {p: self._mapping.get(a, "?").upper()
                          for a, p in PAD_OF_ACTION.items()})
        y += th.px(58 + T.S_4)

        if not debug:
            if self.overlay.available_here:
                self._button((rx, y, rw, th.px(38)), "Overlay on game",
                             "overlay-on", lay, subtle=True)
                y += th.px(38 + T.S_3)
            if not armed:
                ov.text(canvas, "T-pose (arms out) to arm and start",
                        (rx, y), th.font(T.T_SMALL), T.TEXT_DIM)
            return

        ov.text(canvas, f"lane {state['lane'].lower()}      cx {state['cx']:.3f}"
                        f"      shoulder {state['sy']:.3f}",
                (rx, y), th.font(T.T_MICRO), T.TEXT_DIM)
        ov.text(canvas, f"{state['vertical'].lower()}      {state['mode']}"
                        f"{'      paused' if state['paused'] else ''}"
                        f"      queue {state['q']}",
                (rx, y + th.px(17)), th.font(T.T_MICRO),
                T.WARN if state["paused"] else T.TEXT_MUTED)
        y += th.px(40)

        if fsm.jump_y is not None and fsm.duck_y is not None:
            mid = (fsm.jump_y + fsm.duck_y) / 2.0
            half = max((fsm.duck_y - fsm.jump_y) / 2.0, 1e-3)
            trace = [(t, mid - sy) for t, sy in self._spark_sy]
            ov.draw_sparkline(canvas, (rx, y, rw, th.px(56)), trace, now_ms,
                              self._spark_ms, half * 1.4, T.TEXT_DIM,
                              "shoulder line", th, threshold=half)
            y += th.px(56 + T.S_2)
        amp = self.pipeline.run.step_amp
        ov.draw_sparkline(canvas, (rx, y, rw, th.px(56)), self._spark_d, now_ms,
                          self._spark_ms, max(amp * 4.0, 0.05), T.INFO,
                          f"bounce      amp {amp:.3f}", th, threshold=amp)
        y += th.px(56 + T.S_3)
        self._render_feed(canvas, lay, now_ms, rx, y, limit=5, width=rw)

    def _render_overlay(self, now_ms, pose) -> np.ndarray:
        """The Play HUD painted on a transparent, click-through full-screen
        canvas: a green frame round the display and one panel at top centre."""
        lay = self._layout
        # The panel keeps its own modest scale: it should sit out of the way,
        # not grow with the display the way the app window does.
        th = overlay_theme(lay.width, lay.height)
        width, height = lay.width, lay.height
        # Everything left at the colour key is invisible AND click-through, so
        # the game underneath receives those clicks.
        canvas = np.full((height, width, 3), T.OVERLAY_KEY, np.uint8)

        state, feats, fsm, armed = self._play_state(now_ms)
        rects = overlay_layout(width, height, th, self._overlay_position)
        self._overlay_rects = rects
        self._buttons = []

        gate = state["gate"]
        frame_colour = {"RUNNING": T.ACCENT, "WARNING": T.WARN}.get(gate, T.DANGER)
        draw_edge_frame(canvas, width, height, rects["border"], frame_colour)

        ov.card(canvas, rects["panel"], th, fill=T.SURFACE, border=frame_colour)

        tx, ty, tw, tht = rects["title"]
        title = gate.title() if gate != "WARNING" else "Keep moving"
        ov.text_mid(canvas, ov.ellipsize(title, th.font(T.T_LABEL), tw, SEMIBOLD),
                    tx, ty + tht // 2, th.font(T.T_LABEL), frame_colour, SEMIBOLD)

        arm_rect = rects["arm"]
        ov.draw_button(canvas, arm_rect, "Disarm" if armed else "Arm", th,
                       hover=ov.hit(arm_rect, *self._mouse_pos),
                       accent=T.DANGER if armed else T.ACCENT)
        self._buttons.append((arm_rect, "arm"))

        close = rects["close"]
        ov.draw_close_button(canvas, close, th, ov.hit(close, *self._mouse_pos))
        self._buttons.append((close, "overlay-off"))

        stage = self._blit_camera(canvas, pose, rects["camera"], lay, chrome=False)
        self._draw_stage_overlays(canvas, stage, lay, now_ms, state, feats, fsm,
                                  lane_labels=False)

        sx, sy, sw, sh = rects["status"]
        gap = th.px(T.S_2)
        bar_w = int(sw * 0.46)
        ov.draw_cadence_bar(canvas, (sx, sy, bar_w, sh), state["spm"],
                            state["floor"], th, held=state.get("held", False))
        pads = (sx + bar_w + gap, sy, sw - bar_w - gap, sh)
        ov.draw_key_pads(canvas, pads, self._flashes, now_ms, self._flash_ms, th,
                         {p: self._mapping.get(a, "?").upper()
                          for a, p in PAD_OF_ACTION.items()})
        return canvas

    # -- shutdown --------------------------------------------------------------

    def _shutdown(self) -> None:
        summary = self.stats.summary()
        self._bus.publish(
            Log(t_ms=self._clock.now_ms(), severity=Severity.INFO,
                message="session summary",
                detail=" ".join(f"{k}={v}" for k, v in summary.items())))
        if self._args.log_session:
            import json

            path = f"session-{int(self._clock.now_ms())}.json"
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(summary, handle, indent=2)
        if self.recorder is not None:
            self.recorder.close()
        self._hotkey.stop()
        self.audio.close()
        self._game_dispatch.close()
        cv2.destroyAllWindows()
        self.camera.stop()
        self.tracker.close()
