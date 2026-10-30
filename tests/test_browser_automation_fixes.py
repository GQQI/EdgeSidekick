"""Browser automation: observe/act-by-index architecture + rewind/captcha guards."""

from __future__ import annotations

from metateam.services.browser_sandbox import (
    challenge_js_source,
    is_shallow_site_rewind,
    urls_match,
)
from metateam.services import browser_observe as bobs


def test_urls_match_ignores_www_and_slash():
    assert urls_match("https://www.example.com/", "https://example.com")
    assert not urls_match("https://example.com/a", "https://example.com/b")


def test_shallow_rewind_detects_home_from_results():
    assert is_shallow_site_rewind(
        "https://www.example.com/search?q=hello",
        "https://www.example.com/",
    )
    assert is_shallow_site_rewind(
        "https://example.com/s?wd=test",
        "https://example.com",
    )
    assert is_shallow_site_rewind(
        "https://news.example.com/article/1",
        "https://news.example.com/",
    )


def test_shallow_rewind_allows_true_navigation():
    assert not is_shallow_site_rewind(
        "https://example.com/",
        "https://example.com/search?q=x",
    )
    assert not is_shallow_site_rewind(
        "https://example.com/a",
        "https://other.com/",
    )
    assert not is_shallow_site_rewind(
        "about:blank",
        "https://example.com/",
    )
    assert not is_shallow_site_rewind(
        "https://example.com/search?q=x",
        "https://example.com/search?q=y",
    )


def test_challenge_js_contains_generic_cues():
    src = challenge_js_source()
    assert "验证码" in src
    assert "captcha" in src
    assert "challenged" in src


def test_observe_js_indexes_and_filters():
    js = bobs.observe_js(limit=50, expand=200, highlight_all=False)
    assert bobs.IDX_ATTR in js
    assert "elementFromPoint" in js or "elementsFromPoint" in js
    assert "shadowRoot" in js
    # same-origin iframe walk (when implemented)
    assert "iframe" in js.lower() or "contentDocument" in js
    assert "home_link" in js
    assert "__LIMIT__" not in js


def test_format_observation_sample():
    text = bobs.format_observation(
        {
            "url": "https://www.baidu.com/s?wd=playwright",
            "title": "playwright_百度搜索",
            "scroll": {"above": 0, "below": 2400},
            "elements": [
                {
                    "index": 1,
                    "tag": "input",
                    "role": "textbox",
                    "name": "playwright",
                    "placeholder": "请输入搜索词",
                    "value": "playwright",
                },
                {
                    "index": 12,
                    "tag": "a",
                    "role": "link",
                    "name": "Playwright 官网",
                    "href": "https://playwright.dev/",
                },
            ],
        }
    )
    assert "url: https://www.baidu.com/s?wd=playwright" in text
    assert "[1]<input" in text
    assert "[12]<a" in text
    assert "Playwright 官网" in text
    assert "2400px below" in text


def test_highlight_js_uses_overlay_root():
    js = bobs.highlight_index_js()
    assert bobs.HIGHLIGHT_ROOT_ID in js
    assert "pointer-events" in js
    assert "2147483647" in js or "z-index" in js.lower() or "zIndex" in js


def test_find_elements_tool_retired():
    from unittest.mock import MagicMock
    from metateam.runtime.tool_registry import ToolRegistry
    from metateam.runtime.tools.browser import register_browser_tools

    reg = ToolRegistry()
    ctx = MagicMock()
    ctx.live_ws = MagicMock()
    register_browser_tools(reg, ctx)
    assert "browser_find_elements" not in reg._tools
    assert "browser_observe" in reg._tools
    assert "browser_click" in reg._tools
    assert "browser_input" in reg._tools
    assert "browser_scroll_to_text" in reg._tools
    assert "browser_extract_content" in reg._tools


def test_element_is_fillable_contract():
    from metateam.services.browser_sandbox import element_is_fillable, non_fillable_input_error

    assert element_is_fillable(tag="input")
    assert element_is_fillable(tag="textarea")
    assert element_is_fillable(tag="select")
    assert element_is_fillable(tag="div", contenteditable=True)
    assert element_is_fillable(tag="div", role="textbox")
    assert element_is_fillable(tag="div", role="searchbox")
    assert element_is_fillable(tag="div", role="combobox")
    assert element_is_fillable(tag="div", role="spinbutton")
    assert not element_is_fillable(tag="a", role="link")
    assert not element_is_fillable(tag="button", role="button")
    assert not element_is_fillable(tag="div")
    assert not element_is_fillable(tag="span")


def test_non_fillable_input_error_guides_click():
    from metateam.services.browser_sandbox import non_fillable_input_error

    msg = non_fillable_input_error(tag="a", role="link", index=1)
    assert "not editable" in msg
    assert "index=1" in msg
    assert "<a>" in msg
    assert "browser_click" in msg
    assert "Locator.fill" not in msg

    generic = non_fillable_input_error(tag="div", index=3)
    assert "browser_observe" in generic or "browser_click" in generic
    assert "index=3" in generic
