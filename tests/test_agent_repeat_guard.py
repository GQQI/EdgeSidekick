from __future__ import annotations

from types import SimpleNamespace

from metateam.runtime.agent import Agent


def _tc(name: str, args_json: str) -> dict:
    return {"function": {"name": name, "arguments": args_json}}


def test_tool_call_sig_set_ignores_json_formatting() -> None:
    a = {"tool_calls": [_tc("browser_navigate", '{"url": "https://a.com/"}')]}
    b = {"tool_calls": [_tc("browser_navigate", '{"url":"https://a.com/"}   ')]}
    assert Agent._tool_call_sig_set(a) == Agent._tool_call_sig_set(b)


def test_tool_call_sig_set_differs_for_different_args() -> None:
    a = {"tool_calls": [_tc("browser_navigate", '{"url": "https://a.com/"}')]}
    b = {"tool_calls": [_tc("browser_navigate", '{"url": "https://b.com/"}')]}
    assert Agent._tool_call_sig_set(a) != Agent._tool_call_sig_set(b)


def _fake_agent(messages: list[dict]) -> SimpleNamespace:
    fake = SimpleNamespace(messages=messages)
    fake._tool_call_sig_set = Agent._tool_call_sig_set
    return fake


def test_collapse_repeated_assistant_leaves_tool_repeats_to_the_tracker() -> None:
    """The same call can return a new observation; the tracker decides."""
    messages = [
        {"role": "user", "content": "open tsinghua"},
        {
            "role": "assistant",
            "content": "opening the site",
            "tool_calls": [_tc("browser_navigate", '{"url": "https://www.tsinghua.edu.cn/"}')],
        },
        {"role": "tool", "name": "browser_navigate", "content": "ERROR: blocked repeated failing call"},
    ]
    new_assistant = {
        "role": "assistant",
        "content": "opening the site again",
        "tool_calls": [_tc("browser_navigate", '{"url":  "https://www.tsinghua.edu.cn/" }')],
    }
    messages.append(new_assistant)
    fake = _fake_agent(messages)
    assert Agent._collapse_repeated_assistant(fake, new_assistant) is False
    assert new_assistant["tool_calls"]


def test_collapse_allows_successful_browser_click_again() -> None:
    messages = [
        {"role": "user", "content": "点取消"},
        {
            "role": "assistant",
            "content": "点击取消",
            "tool_calls": [_tc("browser_click", '{"index": 4}')],
        },
        {
            "role": "tool",
            "name": "browser_click",
            "content": '{"url": "http://localhost:5175/login", "action": "click", "index": 4}',
        },
    ]
    new_assistant = {
        "role": "assistant",
        "content": "再点一次取消",
        "tool_calls": [_tc("browser_click", '{"index": 4}')],
    }
    messages.append(new_assistant)
    fake = _fake_agent(messages)
    assert Agent._collapse_repeated_assistant(fake, new_assistant) is False
    assert new_assistant["tool_calls"]


def test_collapse_repeated_assistant_allows_different_args() -> None:
    messages = [
        {"role": "user", "content": "browse"},
        {
            "role": "assistant",
            "content": "step one",
            "tool_calls": [_tc("browser_scroll", '{"direction": "down"}')],
        },
        {"role": "tool", "name": "browser_scroll", "content": "{}"},
    ]
    new_assistant = {
        "role": "assistant",
        "content": "step two",
        "tool_calls": [_tc("browser_scroll", '{"direction": "up"}')],
    }
    messages.append(new_assistant)
    fake = _fake_agent(messages)
    assert Agent._collapse_repeated_assistant(fake, new_assistant) is False
    assert new_assistant["tool_calls"]


def test_full_transcript_restores_archived_and_drops_compaction_stub() -> None:
    from metateam.runtime.context import COMPACTION_MARK

    archived = [
        {"role": "user", "content": "第一轮：部署到内网"},
        {"role": "assistant", "content": "已完成第一轮部署"},
    ]
    live = [
        {"role": "system", "content": "sys"},
        {
            "role": "user",
            "content": f"{COMPACTION_MARK}\n\n## Pinned (do not drop)\n### User requests\n- 第一轮：部署到内网",
        },
        {"role": "user", "content": "第二轮：加个按钮"},
        {"role": "assistant", "content": "已加按钮"},
    ]
    fake = SimpleNamespace(messages=live, _archived_messages=archived)
    full = Agent.full_transcript(fake)
    assert full[0] == {"role": "system", "content": "sys"}
    contents = [str(m.get("content") or "") for m in full]
    assert "第一轮：部署到内网" in contents
    assert "已完成第一轮部署" in contents
    assert "第二轮：加个按钮" in contents
    assert not any(c.startswith(COMPACTION_MARK) for c in contents)


def test_full_transcript_empty_messages_returns_archive() -> None:
    fake = SimpleNamespace(messages=[], _archived_messages=[{"role": "user", "content": "x"}])
    assert Agent.full_transcript(fake) == [{"role": "user", "content": "x"}]


def test_write_recoverable_history_is_searchable(tmp_path) -> None:
    from metateam.runtime.context import COMPACTION_MARK

    compact = {"role": "user", "content": f"{COMPACTION_MARK}\n## Goal\nkeep going"}
    fake = SimpleNamespace(
        settings=SimpleNamespace(workspace=tmp_path),
        messages=[
            {"role": "system", "content": "sys"},
            compact,
            {"role": "user", "content": "第二轮：加按钮"},
        ],
        _archived_messages=[{"role": "user", "content": "第一轮：部署到内网"}],
    )
    fake.full_transcript = lambda: Agent.full_transcript(fake)
    Agent._write_recoverable_history(fake)
    text = (tmp_path / ".sidekick" / "context" / "history.md").read_text(encoding="utf-8")
    assert "第一轮：部署到内网" in text
    assert "第二轮：加按钮" in text
    assert ".sidekick/context/history.md" in compact["content"]


def test_collapse_repeated_assistant_catches_growing_text_repeat() -> None:
    prev_text = (
        "正在检查页面内容并总结结果给你，请稍候，这段说明足够长以触发重复检测逻辑，"
        "确保测试覆盖真实场景下的助手输出长度。"
    )
    messages = [
        {"role": "user", "content": "check the page"},
        {"role": "assistant", "content": prev_text},
    ]
    new_assistant = {"role": "assistant", "content": prev_text + prev_text}
    messages.append(new_assistant)
    fake = _fake_agent(messages)
    assert Agent._collapse_repeated_assistant(fake, new_assistant) is True
    assert new_assistant["content"] == ""
