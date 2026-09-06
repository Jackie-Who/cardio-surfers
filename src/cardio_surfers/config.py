"""Config loading. The only source of tunables (PLAN §16.3).

`config.example.json` holds every default; a user `config.json` is deep-merged
over it. Defaults live in exactly one place -- the example file -- so there is
no second copy in Python to drift out of sync with it.

`Config.get` deliberately has no fallback argument. A module writing
`cfg.get("camera.width", 640)` would have reintroduced the magic number the
invariant forbids, so an unknown key raises instead.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from typing import Any, Iterator, Mapping

PACKAGE_ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(PACKAGE_ROOT, "..", ".."))

FROZEN = bool(getattr(sys, "frozen", False))


def resource_dir() -> str:
    """Where read-only files that ship with the app live.

    In a PyInstaller one-file build that is the temporary extraction directory
    (`sys._MEIPASS`), which is deleted on exit -- so nothing writable may go
    there. In a source checkout it is the project root.
    """
    if FROZEN:
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return PROJECT_ROOT


def user_dir() -> str:
    """Where files the app writes live: next to the .exe, or the project root.

    Calibration must survive a restart, so it cannot be written into the
    extraction directory that resource_dir() points at.
    """
    if FROZEN:
        return os.path.dirname(os.path.abspath(sys.executable))
    return PROJECT_ROOT


def resource_path(*parts: str) -> str:
    return os.path.join(resource_dir(), *parts)


def user_path(*parts: str) -> str:
    return os.path.join(user_dir(), *parts)


def resolve_asset(path: str) -> str:
    """Absolute path for a config value like `models/pose_landmarker_lite.task`.

    Bundled copy first, then the working directory, so a checkout keeps working
    and a frozen build finds what was packed into it.
    """
    if os.path.isabs(path):
        return path
    bundled = resource_path(path)
    if os.path.exists(bundled):
        return bundled
    return os.path.abspath(path)


EXAMPLE_PATH = resource_path("config.example.json")
DEFAULT_CONFIG_PATH = user_path("config.json")


class ConfigError(Exception):
    """Raised for a missing key or an unreadable config file."""


# Bump when the meaning of a calibrated value changes -- v3 changed the scale,
# v4 made the lines static, v5 made them absolute screen positions. A threshold measured under an older
# schema lands its line somewhere else entirely, so results from an older
# schema are dropped on load rather than trusted.
CALIBRATION_SCHEMA = 5

# Every key calibration writes. Kept here so load_config can revert exactly
# these to the example defaults when the schema is stale.
CALIBRATED_KEYS = (
    "lanes.mode",
    "lanes.boundary_left",
    "lanes.boundary_right",
    "lanes.hysteresis",
    "vertical.jump_y",
    "vertical.duck_y",
    "running.min_cadence_spm",
    "running.bounce_amp",
    "running.step_amp",
    "calibration.measured",
    "calibration.schema",
)


def _deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _read_json(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a JSON object at the top level")
    return data


def _pop_dotted(data: dict[str, Any], dotted: str) -> None:
    node: Any = data
    parts = dotted.split(".")
    for part in parts[:-1]:
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return
    if isinstance(node, dict):
        node.pop(parts[-1], None)


def strip_stale_calibration(user: dict[str, Any]) -> bool:
    """Remove calibrated keys from a user overlay whose schema is not current.

    Returns True if anything calibrated was present and dropped.
    """
    calibration = user.get("calibration")
    schema = calibration.get("schema") if isinstance(calibration, dict) else None
    if schema == CALIBRATION_SCHEMA:
        return False
    present = any(_has_dotted(user, key) for key in CALIBRATED_KEYS if key != "calibration.schema")
    if not present:
        return False
    for key in CALIBRATED_KEYS:
        _pop_dotted(user, key)
    return True


def _has_dotted(data: Mapping[str, Any], dotted: str) -> bool:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return False
        node = node[part]
    return True


class Config:
    """Dotted read-only access over the merged config tree."""

    def __init__(
        self,
        data: Mapping[str, Any],
        path: str | None = None,
        stale_calibration: bool = False,
    ) -> None:
        self._data = copy.deepcopy(dict(data))
        self.path = path
        self.stale_calibration = stale_calibration

    def get(self, dotted: str) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, Mapping) or part not in node:
                raise ConfigError(
                    f"missing config key: {dotted} "
                    f"(add it to config.example.json -- PLAN §16.3)"
                )
            node = node[part]
        return node

    def section(self, dotted: str) -> dict[str, Any]:
        node = self.get(dotted)
        if not isinstance(node, Mapping):
            raise ConfigError(f"config key {dotted} is not a section")
        return copy.deepcopy(dict(node))

    def has(self, dotted: str) -> bool:
        try:
            self.get(dotted)
        except ConfigError:
            return False
        return True

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    def __contains__(self, dotted: object) -> bool:
        return isinstance(dotted, str) and self.has(dotted)

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)


def load_config(path: str | None = None) -> tuple[Config, bool]:
    """Merge a user config over the example defaults.

    Returns the config and whether a user file was found, so the caller can tell
    a first run (which needs calibration) from a configured one.
    """
    defaults = _read_json(EXAMPLE_PATH)
    target = path or DEFAULT_CONFIG_PATH
    if not os.path.exists(target):
        return Config(defaults, path=None), False
    user = _read_json(target)
    stale = strip_stale_calibration(user)
    merged = _deep_merge(defaults, user)
    return Config(merged, path=target, stale_calibration=stale), True


def save_config(config: Mapping[str, Any], path: str) -> None:
    """Write a config file (used by calibration in P5)."""
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, sort_keys=False)
        handle.write("\n")


def update_config(path: str, dotted: str, value: Any) -> None:
    """Persist one setting to the user config, creating the file if needed.

    Writes only the user's overrides, never the whole merged tree: config.json
    stays a short diff against config.example.json, so defaults improving later
    are not frozen into a copy nobody remembers making.
    """
    data: dict[str, Any] = {}
    if os.path.exists(path):
        data = _read_json(path)

    node = data
    parts = dotted.split(".")
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value

    save_config(data, path)
