"""Config loading and persistence (PLAN §16.3).

Config is the only source of tunables, so the merge behaviour and the
write-back path are worth pinning down: a bug here silently ignores calibration.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cardio_surfers.config import (  # noqa: E402
    Config,
    ConfigError,
    load_config,
    save_config,
    update_config,
)


def test_defaults_load_without_a_user_config(tmp_path):
    # Point at a path that cannot exist, so a developer's real config.json
    # in the project root does not change the result.
    config, found = load_config(str(tmp_path / "absent.json"))
    assert found is False
    assert config.get("camera.width") == 640
    assert config.get("keys.multi_press_gap_ms") == 110


def test_user_config_is_merged_over_defaults(tmp_path):
    path = tmp_path / "config.json"
    save_config({"camera": {"index": 3}}, str(path))

    config, found = load_config(str(path))
    assert found is True
    assert config.get("camera.index") == 3          # overridden
    assert config.get("camera.width") == 640        # still the default
    assert config.get("running.grace_ms") == 1500   # untouched section survives


def test_get_has_no_silent_fallback():
    """A default argument here would reintroduce the magic numbers §16.3 bans."""
    config, _ = load_config(None)
    with pytest.raises(ConfigError):
        config.get("camera.nonexistent")
    with pytest.raises(ConfigError):
        config.get("nosuchsection.key")


def test_update_config_creates_a_minimal_override_file(tmp_path):
    path = tmp_path / "config.json"
    update_config(str(path), "camera.index", 2)

    written = json.loads(path.read_text())
    # Only the override, not a frozen copy of the whole default tree -- so
    # improvements to config.example.json still reach an existing user.
    assert written == {"camera": {"index": 2}}


def test_update_config_preserves_unrelated_settings(tmp_path):
    path = tmp_path / "config.json"
    save_config(
        {"camera": {"index": 0, "fps": 60}, "keys": {"hold_ms": 80}}, str(path)
    )
    update_config(str(path), "camera.index", 4)

    written = json.loads(path.read_text())
    assert written["camera"] == {"index": 4, "fps": 60}
    assert written["keys"] == {"hold_ms": 80}


def test_update_config_replaces_a_non_section_on_the_path(tmp_path):
    """A scalar where a section is needed must not crash the write."""
    path = tmp_path / "config.json"
    save_config({"camera": 5}, str(path))
    update_config(str(path), "camera.index", 1)
    assert json.loads(path.read_text()) == {"camera": {"index": 1}}


def test_switched_camera_index_survives_a_reload(tmp_path):
    """The whole point of the c key: the choice sticks across restarts."""
    path = tmp_path / "config.json"
    update_config(str(path), "camera.index", 3)

    config, found = load_config(str(path))
    assert found is True
    assert config.get("camera.index") == 3


def test_config_is_a_snapshot_not_a_live_view():
    data = {"camera": {"index": 0}}
    config = Config(data)
    data["camera"]["index"] = 99
    assert config.get("camera.index") == 0


def test_stale_calibration_is_dropped_with_a_flag(tmp_path):
    """Thresholds derived under an older scale would land the lines in the
    wrong place; they must not be trusted, and the menu must say so."""
    path = tmp_path / "config.json"
    save_config(
        {
            "camera": {"index": 2},
            "lanes": {"mode": "calibrated", "boundary_left": 0.3, "boundary_right": 0.7},
            "vertical": {"duck_y": 0.55, "jump_y": 0.25},
            "calibration": {"measured": {"scale0": 0.59}},   # no schema: pre-v5
        },
        str(path),
    )
    config, found = load_config(str(path))
    assert found is True
    assert config.stale_calibration is True
    assert config.get("camera.index") == 2                  # non-calibration keys survive
    assert config.get("lanes.boundary_left") is None        # calibrated keys reverted
    assert config.get("lanes.mode") == "thirds"
    assert config.get("vertical.duck_y") is None            # example default
    assert config.get("vertical.jump_y") is None


def test_current_calibration_is_kept(tmp_path):
    from cardio_surfers.config import CALIBRATION_SCHEMA

    path = tmp_path / "config.json"
    save_config(
        {
            "lanes": {"mode": "calibrated", "boundary_left": 0.3, "boundary_right": 0.7},
            "vertical": {"duck_y": 0.5},
            "calibration": {"schema": CALIBRATION_SCHEMA, "measured": {}},
        },
        str(path),
    )
    config, _ = load_config(str(path))
    assert config.stale_calibration is False
    assert config.get("lanes.boundary_left") == 0.3
    assert config.get("vertical.duck_y") == 0.5
