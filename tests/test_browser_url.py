from __future__ import annotations

import pytest

from metateam.services.browser_sandbox import (
    coerce_navigate_target,
    sanitize_browser_url,
)


def test_sanitize_strips_markdown_junk() -> None:
    assert sanitize_browser_url("http://localhost:5173**，已在/") in {
        "http://localhost:5173",
        "http://localhost:5173/",
    }


def test_coerce_full_https() -> None:
    assert coerce_navigate_target("https://example.com/x") == "https://example.com/x"


def test_coerce_bare_domain() -> None:
    assert coerce_navigate_target("example.com") == "https://example.com"
    assert coerce_navigate_target("www.baidu.com/s?wd=1").startswith("https://www.baidu.com")


def test_coerce_localhost() -> None:
    assert coerce_navigate_target("localhost:5173") in {
        "http://localhost:5173",
        "http://localhost:5173/",
    }
    assert coerce_navigate_target("127.0.0.1:8788").startswith("http://127.0.0.1:8788")


def test_coerce_rejects_screenshot_filename() -> None:
    with pytest.raises(ValueError, match="screenshot"):
        coerce_navigate_target("real_1_top.png")
    with pytest.raises(ValueError, match="screenshot"):
        coerce_navigate_target("shot_1710000000.png")


def test_coerce_recovers_screenshot_sidecar(tmp_path, monkeypatch) -> None:
    shot_dir = tmp_path / ".sidekick" / "browser"
    shot_dir.mkdir(parents=True)
    shot = shot_dir / "real_tsinghua_top.png"
    shot.write_bytes(b"png")
    (shot_dir / "real_tsinghua_top.png.json").write_text(
        '{"url": "https://www.tsinghua.edu.cn/"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "metateam.workspace.workspace_store.get_active_workspace",
        lambda: {"configured": True, "path": str(tmp_path)},
    )
    assert (
        coerce_navigate_target(".sidekick/browser/real_tsinghua_top.png")
        == "https://www.tsinghua.edu.cn/"
    )


def test_coerce_recovers_screenshot_from_session(monkeypatch) -> None:
    from metateam.services.browser_sandbox import SANDBOX

    monkeypatch.setattr(SANDBOX, "last_url", lambda user_id=None: "https://www.tsinghua.edu.cn/")
    assert coerce_navigate_target("real_tsinghua_top.png") == "https://www.tsinghua.edu.cn/"


def test_coerce_workspace_html(tmp_path, monkeypatch) -> None:
    page = tmp_path / "login.html"
    page.write_text("<html></html>", encoding="utf-8")
    nested = tmp_path / "web"
    nested.mkdir()
    (nested / "home.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(
        "metateam.browser.preview.preview_http_url",
        lambda html_file, workspace: f"http://127.0.0.1:9/{html_file.name}",
    )
    assert (
        coerce_navigate_target("login.html", workspace=tmp_path)
        == "http://127.0.0.1:9/login.html"
    )
    assert (
        coerce_navigate_target("home.html", workspace=tmp_path)
        == "http://127.0.0.1:9/home.html"
    )
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "home.html").write_text("<html></html>", encoding="utf-8")
    with pytest.raises(ValueError, match="more than one"):
        coerce_navigate_target("home.html", workspace=tmp_path)
    with pytest.raises(ValueError, match="not an existing file"):
        coerce_navigate_target("missing.html", workspace=tmp_path)


def test_coerce_empty() -> None:
    assert coerce_navigate_target("") == ""
    assert coerce_navigate_target("about:blank") == "about:blank"


def test_coerce_rejects_script_url() -> None:
    raw = (
        "javascript:void(document.getElementById('username').value='admin',"
        "document.getElementById('password').value='123456',"
        "document.getElementById('loginForm').onsubmit())"
    )
    with pytest.raises(ValueError, match="browser_input"):
        coerce_navigate_target(raw)
    with pytest.raises(ValueError, match="browser_click"):
        coerce_navigate_target("data:text/html,hi")


def test_dialog_note_waits_until_the_user_answers() -> None:
    from metateam.services.browser_sandbox import (
        dialog_interrupted,
        dialog_result_note,
        merge_dialog_log,
        release_unblocked_dialogs,
    )

    assert dialog_interrupted(RuntimeError("JavaScript dialog interrupted evaluation"))
    assert not dialog_interrupted(RuntimeError("element not found"))
    waiting = dialog_result_note(
        [{"type": "alert", "message": "saved", "open": True}]
    )
    assert "不要调用下一个工具" in waiting
    done = dialog_result_note(
        [{"type": "confirm", "message": "delete?", "open": False, "accepted": False}]
    )
    assert "点了取消" in done
    items = [
        {"type": "file", "open": True},
        {"type": "alert", "message": "saved", "open": True, "accepted": None, "_url": "http://a"},
    ]
    release_unblocked_dialogs(items, page_ready=True, url="http://a")
    assert items[0]["open"] is True
    assert items[1]["open"] is False
    merge_dialog_log(items, [{"type": "alert", "message": "saved", "accepted": True}])
    assert items[1]["accepted"] is True


def test_urls_match_ignores_slash_and_www() -> None:
    from metateam.services.browser_sandbox import urls_match

    assert urls_match("https://www.tsinghua.edu.cn/", "https://tsinghua.edu.cn")
    assert not urls_match("https://www.tsinghua.edu.cn/", "https://example.com")
