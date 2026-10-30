"""Index-based observe/click on a local HTML fixture (Playwright)."""

from __future__ import annotations

import json

import pytest

from metateam.services import browser_observe as bobs

FIXTURE = """<!doctype html>
<html><head><title>Fixture</title></head>
<body>
  <h1>Search demo</h1>
  <input id="q" placeholder="query" value="" />
  <button id="go">Search</button>
  <div style="height:1200px"></div>
  <a id="below" href="https://example.com/playwright">Playwright docs below fold</a>
  <div style="height:400px"></div>
</body></html>
"""


@pytest.fixture(scope="module")
def page():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        pg = browser.new_page(viewport={"width": 900, "height": 600})
        pg.set_content(FIXTURE)
        yield pg
        browser.close()


def test_observe_returns_indexed_inputs_and_buttons(page):
    raw = page.evaluate(bobs.observe_js(limit=50, expand=2000, highlight_all=False))
    assert raw["title"] == "Fixture"
    els = raw["elements"]
    assert els, "expected interactive elements"
    tags = {e["tag"] for e in els}
    assert "input" in tags
    assert "button" in tags
    # indices are stable ints starting at 1
    idxs = [e["index"] for e in els]
    assert idxs == list(range(1, len(idxs) + 1))
    # stamped attributes exist
    stamped = page.evaluate(
        f"() => document.querySelectorAll('[{bobs.IDX_ATTR}]').length"
    )
    assert stamped == len(els)


def test_observe_includes_below_fold_with_expand(page):
    raw = page.evaluate(bobs.observe_js(limit=50, expand=2000, highlight_all=False))
    names = " ".join(e.get("name", "") for e in raw["elements"])
    assert "Playwright docs" in names
    link = next(e for e in raw["elements"] if "Playwright" in (e.get("name") or ""))
    assert link.get("href", "").endswith("/playwright")


def test_highlight_index_draws_overlay(page):
    page.evaluate(bobs.observe_js(limit=50, expand=2000, highlight_all=False))
    raw = page.evaluate(bobs.observe_js(limit=50, expand=2000, highlight_all=False))
    idx = raw["elements"][0]["index"]
    page.evaluate(bobs.highlight_index_js(), idx)
    exists = page.evaluate(
        f"() => !!document.getElementById('{bobs.HIGHLIGHT_ROOT_ID}')"
    )
    assert exists
    page.evaluate(bobs.clear_highlight_js())
    gone = page.evaluate(
        f"() => !document.getElementById('{bobs.HIGHLIGHT_ROOT_ID}')"
    )
    assert gone


def test_format_observation_roundtrip(page):
    raw = page.evaluate(bobs.observe_js(limit=50, expand=2000, highlight_all=False))
    text = bobs.format_observation(raw)
    assert "url:" in text
    assert "[1]<" in text


def test_observe_viewport_first_default_skips_below_fold(page):
    """Default expand=0 must not dump below-fold links into the LLM context."""
    raw = page.evaluate(bobs.observe_js())  # defaults: limit=40, expand=0
    names = " ".join(e.get("name", "") for e in raw["elements"])
    assert "Playwright docs" not in names
    assert raw.get("scroll", {}).get("below", 0) > 0


def test_observe_visual_order_and_content_ordinal(page):
    """Indexes follow top-to-bottom order; content=N counts result links."""
    html = """<!doctype html><html><body>
      <a href="https://news.example/a">第一篇人工智能</a>
      <a href="https://news.example/b">第二篇人工智能</a>
      <a href="https://example.com/">首页</a>
      <button>搜索</button>
    </body></html>"""
    page.set_content(html)
    raw = page.evaluate(bobs.observe_js(limit=20, expand=0))
    names = [e.get("name") for e in raw["elements"]]
    assert names.index("第一篇人工智能") < names.index("第二篇人工智能")
    by_name = {e.get("name"): e for e in raw["elements"]}
    assert by_name["第一篇人工智能"].get("content_n") == 1
    assert by_name["第二篇人工智能"].get("content_n") == 2
    assert "content_n" not in by_name["首页"]
    page.set_content(FIXTURE)


def test_observe_ranks_and_truncates(page):
    raw = page.evaluate(bobs.observe_js(limit=1, expand=2000, highlight_all=False))
    assert raw["count"] <= 1
    assert raw.get("truncated") is True or int(raw.get("total_candidates") or 0) >= 1
    text = bobs.format_observation(raw)
    assert "viewport-first" in text or "capped" in text


def test_format_observation_truncation_hint():
    text = bobs.format_observation(
        {
            "url": "https://example.com/results?q=x",
            "title": "results",
            "scroll": {"above": 0, "below": 900},
            "truncated": True,
            "total_candidates": 120,
            "elements": [
                {"index": 1, "tag": "a", "role": "link", "name": "Result 1", "href": "https://example.com/a"},
            ],
        }
    )
    assert "truncated" in text
    assert "browser_scroll" in text
    assert "browser_extract_content" in text
