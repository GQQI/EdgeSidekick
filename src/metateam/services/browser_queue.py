"""Decide when a browser action must wait or must not be repeated.

The Playwright worker already runs one action at a time. These checks sit in
front of that queue: a page that is waiting for a person accepts no further
agent clicks, and a click that did not move the page is not issued again.
"""

from __future__ import annotations

from typing import Any


def page_fingerprint(url: str, scroll: dict[str, Any] | None) -> tuple[str, int]:
    above = 0
    if isinstance(scroll, dict):
        try:
            above = int(scroll.get("above") or 0)
        except (TypeError, ValueError):
            above = 0
    return (str(url or "").strip(), above)


def page_unchanged(before: tuple[str, int], after: tuple[str, int]) -> bool:
    return bool(before[0]) and before == after


def skip_repeated_click(last: dict[str, Any] | None, index: int) -> bool:
    if not isinstance(last, dict) or not last.get("unchanged"):
        return False
    try:
        return int(last.get("index")) == int(index)
    except (TypeError, ValueError):
        return False


def human_hold_result(url: str) -> dict[str, Any]:
    return {
        "stopped": True,
        "needs_human": True,
        "url": url,
        "note": "页面在等用户完成验证，自动点击已暂停。用户完成后会继续。",
    }


def repeated_click_result(url: str, index: int) -> dict[str, Any]:
    return {
        "stopped": True,
        "url": url,
        "index": int(index),
        "action": "click",
        "note": "同一次点击没有改变页面，已停止重复。请换一个元素或滚动一屏。",
    }
