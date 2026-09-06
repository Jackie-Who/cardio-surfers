"""Audible gate warnings (PLAN §7.3), with volume and output-device choice.

`winsound` is not usable here for two reasons: it always plays to the default
device, and `PlaySound(SND_MEMORY | SND_ASYNC)` raises "Cannot play
asynchronously from memory" -- which is why the Test button silently did
nothing. So playback goes through `sounddevice` instead, which was already a
mediapipe dependency, plays a numpy buffer asynchronously, and can target a
specific output device.

Tones are synthesised at the requested amplitude with short fades so they do
not click. Every beep happens on a daemon thread; the bus subscriber only
records the pattern it wants.
"""

from __future__ import annotations

import math
import threading

import numpy as np

from .events import Event, EventBus, GateTransition

SAMPLE_RATE = 44100


def synth_tone(hz: int, ms: int, volume: float, rate: int = SAMPLE_RATE) -> np.ndarray:
    """A mono float32 sine at `volume` in [0,1], with 5 ms fades."""
    n = max(1, int(rate * ms / 1000.0))
    t = np.arange(n, dtype=np.float32) / rate
    wave = np.sin(2.0 * math.pi * hz * t, dtype=np.float32)
    fade = min(n // 4, int(rate * 0.005))
    if fade > 0:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        wave[:fade] *= ramp
        wave[-fade:] *= ramp[::-1]
    return (wave * max(0.0, min(1.0, volume))).astype(np.float32)


def output_devices() -> list[dict]:
    """Selectable output devices, one entry per real endpoint.

    `query_devices` lists every device once per host API (MME, DirectSound,
    WASAPI, WDM-KS), which would show the same speakers four times. Only the
    default host API is offered, plus a "system default" entry.
    """
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        # Prefer WASAPI: MME truncates every device name to 31 characters, so
        # "Logitech Speakers (Realtek USB2.0 Audio)" arrives cut in half.
        default_api = 0
        try:
            for i, api in enumerate(sd.query_hostapis()):
                if "WASAPI" in str(api["name"]).upper():
                    default_api = i
                    break
            else:
                default_api = sd.default.hostapi
        except Exception:  # noqa: BLE001
            default_api = 0
        out = [{"index": None, "name": "System default"}]
        seen: set[str] = set()
        for i, d in enumerate(devices):
            if d["max_output_channels"] <= 0 or d["hostapi"] != default_api:
                continue
            name = str(d["name"]).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            out.append({"index": i, "name": name})
        return out
    except Exception:  # noqa: BLE001 - audio is a nicety, never fatal
        return [{"index": None, "name": "System default"}]


def device_label(index: int | None) -> str:
    if index is None:
        return "System default"
    for device in output_devices():
        if device["index"] == index:
            return device["name"]
    return f"Device {index}"


class AudioAlerts:
    def __init__(self, bus: EventBus, config) -> None:
        self.enabled = bool(config.get("running.audio_warnings"))
        self.volume = float(config.get("running.audio_volume"))
        self.device = config.get("running.audio_device")
        self._warn_hz = int(config.get("running.warn_beep_hz"))
        self._warn_ms = int(config.get("running.warn_beep_ms"))
        self._repeat_ms = int(config.get("running.warn_beep_repeat_ms"))
        self._lock_hz = int(config.get("running.lock_beep_hz"))

        self._mode = "quiet"           # quiet | warning
        self._lock = threading.Lock()
        self._kick = threading.Event()
        self._stop = threading.Event()
        self._oneshot: list[tuple[int, int]] = []
        self._rates: dict = {}
        self.last_error: str | None = None

        self._thread = threading.Thread(target=self._run, name="audio", daemon=True)
        self._thread.start()
        bus.subscribe(self._on_event)

    # -- settings ---------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        if not enabled:
            with self._lock:
                self._mode = "quiet"
        self.enabled = enabled

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(1.0, float(volume)))

    def set_device(self, index: int | None) -> None:
        self.device = index
        self._rates.pop(index, None)

    def test_beep(self) -> None:
        """One warning-style beep at the current volume and device."""
        with self._lock:
            self._oneshot = [(self._warn_hz, self._warn_ms)]
        self._kick.set()

    # -- bus ---------------------------------------------------------------------

    def _on_event(self, event: Event) -> None:
        if not isinstance(event, GateTransition) or not self.enabled:
            return
        with self._lock:
            if event.to_state == "WARNING":
                self._mode = "warning"
            elif event.to_state == "LOCKED":
                self._mode = "quiet"
                self._oneshot = [(self._lock_hz, self._warn_ms)] * 2
            elif event.to_state == "RUNNING" and event.from_state != "RUNNING":
                self._mode = "quiet"
                self._oneshot = [(self._warn_hz, self._warn_ms),
                                 (int(self._warn_hz * 1.5), self._warn_ms)]
        self._kick.set()

    # -- player thread -----------------------------------------------------------

    def _device_rate(self, sd) -> int:
        """A device's own default rate.

        WASAPI endpoints refuse anything else -- playing 44.1 kHz at a 48 kHz
        device raises "Invalid sample rate" rather than resampling -- so the
        tone is synthesised at whatever the chosen device wants.
        """
        cached = self._rates.get(self.device)
        if cached is not None:
            return cached
        rate = SAMPLE_RATE
        try:
            info = sd.query_devices(self.device, output)
            rate = int(info[default_samplerate])
        except Exception:  # noqa: BLE001
            pass
        self._rates[self.device] = rate
        return rate

    def _play(self, hz: int, ms: int) -> None:
        if self.volume <= 0.0:
            self._stop.wait(ms / 1000.0)
            return
        try:
            import sounddevice as sd

            rate = self._device_rate(sd)
            sd.play(synth_tone(hz, ms, self.volume, rate), samplerate=rate,
                    device=self.device, blocking=False)
            self._stop.wait((ms + 40) / 1000.0)
        except Exception as exc:  # noqa: BLE001 - a dead device must not kill the app
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._stop.wait(ms / 1000.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                oneshot, self._oneshot = self._oneshot, []
                warning = self._mode == "warning" and self.enabled
            for hz, ms in oneshot:
                self._play(hz, ms)
            if warning:
                self._play(self._warn_hz, self._warn_ms)
                self._kick.wait(self._repeat_ms / 1000.0)
            else:
                self._kick.wait(0.5)
            self._kick.clear()

    def close(self) -> None:
        self._stop.set()
        self._kick.set()
        try:
            import sounddevice as sd

            sd.stop()
        except Exception:  # noqa: BLE001
            pass
        self._thread.join(timeout=1.0)
