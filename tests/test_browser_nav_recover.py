"""browser_navigate load failures must not hard-stop the agent turn."""

from __future__ import annotations

from metateam.runtime.agent import Agent
from metateam.runtime.tools import browser as browser_tools


def test_browser_nav_result_failed_detects_refused_and_chrome_error() -> None:
    assert Agent._browser_nav_result_failed("ERROR: page load failed: ERR_CONNECTION_REFUSED")
    assert Agent._browser_nav_result_failed(
        '{"url": "chrome-error://chromewebdata/", "ready": false}'
    )
    assert Agent._browser_nav_result_failed(
        '{"url": "http://127.0.0.1:49958/pku.html", "ready": false, '
        '"deferred": true, "note": "net::ERR_CONNECTION_REFUSED"}'
    )
    assert Agent._browser_nav_result_failed("页面加载失败: ERR_CONNECTION_REFUSED")
    assert not Agent._browser_nav_result_failed(
        '{"url": "http://127.0.0.1:5173/", "ready": true, "title": "ok"}'
    )


def test_connection_refused_hint_is_actionable_not_stop() -> None:
    msg = browser_tools._navigate_failure_message(
        "net::ERR_CONNECTION_REFUSED", "http://127.0.0.1:49958/pku.html"
    )
    assert msg.startswith("ERROR:")
    assert "ERR_CONNECTION_REFUSED" in msg or "connection" in msg.lower()
    assert "Do NOT stop" in msg or "do not stop" in msg.lower()
    assert "server" in msg.lower() or "port" in msg.lower()
    assert "已停止" not in msg
    assert "停止重复" not in msg


def test_navigate_info_failure_from_deferred_json() -> None:
    info = {
        "url": "http://127.0.0.1:49958/pku.html",
        "ready": False,
        "deferred": True,
        "note": "net::ERR_CONNECTION_REFUSED",
    }
    msg = browser_tools._navigate_info_failure(info, "http://127.0.0.1:49958/pku.html")
    assert msg.startswith("ERROR:")
    assert "Do NOT stop" in msg or "do not stop" in msg.lower()


def test_agent_source_has_no_browser_stop_phrase() -> None:
    from pathlib import Path
    import metateam.runtime.agent as agent_mod

    root = Path(agent_mod.__file__).resolve().parent
    src = "\n".join(p.read_text(encoding="utf-8") for p in sorted(root.glob("*.py")))
    assert "页面没有打开，已停止重复尝试" not in src
    assert "已停止重复尝试" not in src
    assert "_BROWSER_NAV_RECOVER_NUDGE" in src
    assert "browser_nav_recover" in src


def test_chromium_unsafe_port_helpers() -> None:
    from metateam.services.browser_sandbox import (
        is_chromium_unsafe_port,
        url_has_chromium_unsafe_port,
        url_port,
    )

    assert url_port("http://127.0.0.1:6000/app") == 6000
    assert is_chromium_unsafe_port(6000) is True
    assert is_chromium_unsafe_port(5173) is False
    assert url_has_chromium_unsafe_port("http://127.0.0.1:6000/") is True
    assert url_has_chromium_unsafe_port("http://127.0.0.1:5173/") is False


def test_navigate_rejects_chromium_unsafe_port_early() -> None:
    from metateam.services.browser_sandbox import BrowserSandbox

    sandbox = BrowserSandbox.__new__(BrowserSandbox)
    try:
        sandbox.navigate("http://127.0.0.1:6000/demo")
        assert False, "expected ValueError for unsafe port"
    except ValueError as exc:
        msg = str(exc)
        assert "UNSAFE_PORT" in msg or "6000" in msg


def test_navigate_failure_message_for_unsafe_port_is_actionable() -> None:
    msg = browser_tools._navigate_failure_message(
        "net::ERR_UNSAFE_PORT", "http://127.0.0.1:6000/"
    )
    assert msg.startswith("ERROR:")
    assert "UNSAFE_PORT" in msg or "unsafe" in msg.lower()
    assert "Do NOT stop" in msg or "do not stop" in msg.lower()
    assert "Do NOT retry" in msg or "do not retry" in msg.lower() or "5173" in msg
