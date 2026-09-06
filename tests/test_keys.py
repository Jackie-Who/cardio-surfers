"""Focus gate policy and the press queue's rate limiting."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cardio_surfers.keys import FocusGate, PressQueue  # noqa: E402


class FakeGate(FocusGate):
    """FocusGate with the foreground title stubbed, so no window is needed."""

    def __init__(self, pattern, exclude_title=None, title=None):
        super().__init__(pattern, exclude_title=exclude_title)
        self._title = title

    def current_title(self):
        return self._title


def test_empty_regex_allows_any_window():
    """The default: keys go wherever focus is -- the game, a text editor, a
    browser tab -- so you can watch them land while testing."""
    gate = FakeGate("", exclude_title="Motion Controller", title="Untitled - Notepad")
    ok, title = gate.check()
    assert ok is True
    assert title == "Untitled - Notepad"


def test_our_own_window_is_always_excluded():
    """The controller must never be able to type into itself."""
    gate = FakeGate("", exclude_title="Motion Controller", title="Motion Controller")
    ok, _ = gate.check()
    assert ok is False


def test_a_regex_still_restricts_delivery():
    gate = FakeGate("(?i)chrome", exclude_title="Motion Controller",
                    title="Untitled - Notepad")
    assert gate.check()[0] is False
    gate = FakeGate("(?i)chrome", exclude_title="Motion Controller",
                    title="Subway Surfers - Google Chrome")
    assert gate.check()[0] is True


def test_no_foreground_window_is_a_refusal():
    gate = FakeGate("", exclude_title="Motion Controller", title=None)
    ok, title = gate.check()
    assert ok is False
    assert "no foreground" in title


def test_queue_spaces_a_double_by_the_gap():
    queue = PressQueue(gap_ms=110.0)
    queue.enqueue("d", "lane", 0.0)
    queue.enqueue("d", "lane", 0.0)
    assert [p.key for p in queue.drain(0.0)] == ["d"]
    assert queue.drain(50.0) == []
    assert queue.drain(109.0) == []
    assert [p.key for p in queue.drain(110.0)] == ["d"]
    assert len(queue) == 0
