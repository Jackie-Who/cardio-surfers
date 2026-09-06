"""One Euro filter, implemented inline (PLAN §16.5), plus the EMA fallback.

One Euro smooths hard when the signal is still (no phantom lane changes) and
barely filters when it moves fast (no lag on the dodge). ~30 lines; there is no
dependable maintained pip package for it, so it lives here.

No clock reads (PLAN §16.1): every update takes the timestamp as a parameter.
"""

from __future__ import annotations

import math
from typing import Protocol


class Filter(Protocol):
    def update(self, value: float, t_ms: float) -> float: ...
    def reset(self) -> None: ...


def _alpha(cutoff_hz: float, dt_s: float) -> float:
    tau = 1.0 / (2.0 * math.pi * cutoff_hz)
    return 1.0 / (1.0 + tau / dt_s)


class OneEuroFilter:
    """Adaptive low-pass: cutoff rises with the signal's own derivative."""

    def __init__(self, min_cutoff: float, beta: float, d_cutoff: float) -> None:
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self._x: float | None = None
        self._dx = 0.0
        self._t_ms: float | None = None

    def update(self, value: float, t_ms: float) -> float:
        if self._x is None or self._t_ms is None or t_ms <= self._t_ms:
            self._x = value
            self._t_ms = t_ms
            self._dx = 0.0
            return value
        dt_s = (t_ms - self._t_ms) / 1000.0
        self._t_ms = t_ms

        dx = (value - self._x) / dt_s
        a_d = _alpha(self.d_cutoff, dt_s)
        self._dx = a_d * dx + (1.0 - a_d) * self._dx

        cutoff = self.min_cutoff + self.beta * abs(self._dx)
        a = _alpha(cutoff, dt_s)
        self._x = a * value + (1.0 - a) * self._x
        return self._x

    def reset(self) -> None:
        self._x = None
        self._t_ms = None
        self._dx = 0.0


class EmaFilter:
    """Plain EMA behind the same interface, as the fallback (PLAN §5)."""

    def __init__(self, alpha: float) -> None:
        self.alpha = float(alpha)
        self._x: float | None = None

    def update(self, value: float, t_ms: float) -> float:  # t_ms unused, same shape
        if self._x is None:
            self._x = value
        else:
            self._x = self.alpha * value + (1.0 - self.alpha) * self._x
        return self._x

    def reset(self) -> None:
        self._x = None


def make_filter(kind: str, one_euro_params: dict, ema_alpha: float) -> Filter:
    """Build the configured filter for one signal (config `filters.type`)."""
    if kind == "one_euro":
        return OneEuroFilter(
            min_cutoff=float(one_euro_params["min_cutoff"]),
            beta=float(one_euro_params["beta"]),
            d_cutoff=float(one_euro_params["d_cutoff"]),
        )
    return EmaFilter(ema_alpha)
