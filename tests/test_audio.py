"""Tone synthesis and output-device selection.

The bug this suite exists for: `winsound.PlaySound(SND_MEMORY | SND_ASYNC)`
raises "Cannot play asynchronously from memory", so the Test button silently
did nothing. Playback moved to sounddevice, which also allows choosing a
device.
"""

from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cardio_surfers.audio import (  # noqa: E402
    SAMPLE_RATE,
    device_label,
    output_devices,
    synth_tone,
)


def test_tone_has_the_right_length_and_type():
    tone = synth_tone(880, 120, 0.6)
    assert tone.dtype == np.float32
    duration_ms = 1000.0 * len(tone) / SAMPLE_RATE
    assert 115 < duration_ms < 125


def test_tone_is_the_requested_frequency():
    """An 880 Hz tone should peak at 880 Hz in its spectrum."""
    tone = synth_tone(880, 300, 1.0)
    spectrum = np.abs(np.fft.rfft(tone.astype(np.float64)))
    freqs = np.fft.rfftfreq(len(tone), 1.0 / SAMPLE_RATE)
    assert abs(freqs[int(np.argmax(spectrum))] - 880) < 15


def test_volume_scales_the_amplitude():
    quiet = float(np.max(np.abs(synth_tone(880, 120, 0.2))))
    loud = float(np.max(np.abs(synth_tone(880, 120, 1.0))))
    assert loud > quiet * 3
    assert loud <= 1.0


def test_zero_volume_is_silent():
    assert float(np.max(np.abs(synth_tone(880, 120, 0.0)))) == 0.0


def test_volume_is_clamped():
    assert float(np.max(np.abs(synth_tone(880, 120, 5.0)))) <= 1.0
    assert float(np.max(np.abs(synth_tone(880, 120, -3.0)))) == 0.0


def test_tone_fades_in_and_out_so_it_does_not_click():
    tone = synth_tone(880, 120, 1.0)
    assert abs(float(tone[0])) < 0.02
    assert abs(float(tone[-1])) < 0.02


def test_device_list_always_offers_a_system_default():
    devices = output_devices()
    assert devices, "there must always be at least one choice"
    assert devices[0]["index"] is None
    assert devices[0]["name"] == "System default"


def test_device_list_has_no_duplicate_names():
    """query_devices repeats each endpoint once per host API; one wins."""
    names = [d["name"] for d in output_devices()]
    assert len(names) == len(set(names))


def test_device_label_round_trips():
    assert device_label(None) == "System default"
    for device in output_devices():
        if device["index"] is not None:
            assert device_label(device["index"]) == device["name"]
            break


def test_unknown_device_label_is_still_readable():
    assert device_label(9999) == "Device 9999"
