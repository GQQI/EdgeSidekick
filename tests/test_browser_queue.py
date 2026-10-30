"""Browser action gate: human hold and a click that did not move the page."""

from __future__ import annotations

from metateam.services.browser_queue import (
    human_hold_result,
    page_fingerprint,
    page_unchanged,
    repeated_click_result,
    skip_repeated_click,
)


def test_fingerprint_uses_url_and_scroll_above():
    assert page_fingerprint("https://example.com/a", {"above": 120, "below": 40}) == (
        "https://example.com/a",
        120,
    )
    assert page_fingerprint("https://example.com/a", None) == ("https://example.com/a", 0)


def test_unchanged_page_is_the_same_fingerprint():
    before = page_fingerprint("https://example.com/a", {"above": 0})
    after = page_fingerprint("https://example.com/a", {"above": 0})
    assert page_unchanged(before, after)
    moved = page_fingerprint("https://example.com/b", {"above": 0})
    assert not page_unchanged(before, moved)
    scrolled = page_fingerprint("https://example.com/a", {"above": 800})
    assert not page_unchanged(before, scrolled)


def test_skip_only_the_click_that_already_failed_to_move():
    last = {"index": 9, "unchanged": True}
    assert skip_repeated_click(last, 9)
    assert not skip_repeated_click(last, 3)
    assert not skip_repeated_click({"index": 9, "unchanged": False}, 9)
    assert not skip_repeated_click(None, 9)


def test_hold_payloads_tell_the_model_to_stop():
    hold = human_hold_result("https://example.com/check")
    assert hold["needs_human"] is True
    assert hold["stopped"] is True
    again = repeated_click_result("https://example.com/a", 9)
    assert again["stopped"] is True
    assert again["index"] == 9
    assert "停止" in again["note"]
