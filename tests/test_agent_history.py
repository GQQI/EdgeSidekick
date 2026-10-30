from __future__ import annotations

from metateam.runtime.agent_history import (
    AgentHistoryMixin,
    is_cancel_control_text,
    strip_cancel_control,
)


class _Hist(AgentHistoryMixin):
    def __init__(self, messages):
        self.messages = messages


def test_internal_plan_step() -> None:
    h = _Hist([])
    assert h._is_internal_message({"role": "user", "content": "[Plan step 1] go"})
    assert h._is_internal_message({"role": "user", "sidekick_internal": True, "content": "x"})
    assert not h._is_internal_message({"role": "user", "content": "real question"})


def test_seal_cancelled_keeps_user_visible_without_notice() -> None:
    h = _Hist(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "do it"},
            {"role": "assistant", "content": "working", "tool_calls": [{"id": "c1"}]},
        ]
    )
    text = h._seal_cancelled_turn("partial")
    assert text == "partial"
    assert "用户已停止" not in text
    assert h.messages[1]["role"] == "user"
    assert h.messages[-2]["role"] == "assistant"
    assert h.messages[-2]["content"] == "partial"
    assert h.messages[-1]["role"] == "system"
    assert h.messages[-1].get("sidekick_internal") is True
    assert "用户已停止" in h.messages[-1]["content"]
    assert not any(m.get("tool_calls") for m in h.messages)


def test_strip_cancel_control_removes_injected_notice() -> None:
    body = (
        "真实官网的北大人物正常显示了。\n\n"
        "（用户已停止本轮生成。请等待下一条用户指令；"
        "不要继续或恢复刚才未完成的任务。）"
    )
    cleaned = strip_cancel_control(body)
    assert "北大人物" in cleaned
    assert "用户已停止" not in cleaned
    assert is_cancel_control_text(
        "（用户已停止本轮生成。请等待下一条用户指令；不要继续或恢复刚才未完成的任务。）"
    )


def test_scrub_history_removes_poisoned_assistant() -> None:
    h = _Hist(
        [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": (
                    "继续修复\n\n（用户已停止本轮生成。请等待下一条用户指令；"
                    "不要继续或恢复刚才未完成的任务。）"
                ),
            },
        ]
    )
    h._scrub_cancel_notices_in_history()
    assert h.messages[-1]["content"] == "继续修复"


def test_repair_dangling_tool_calls() -> None:
    h = _Hist(
        [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "about to act", "tool_calls": [{"id": "c1"}]},
        ]
    )
    h._repair_dangling_tool_calls()
    assert h.messages[0]["role"] == "user"
    assert h.messages[1]["role"] == "assistant"
    assert h.messages[1]["content"] == "about to act"
    assert h.messages[-1].get("sidekick_internal") is True
    assert "已中断" in h.messages[-1]["content"]
