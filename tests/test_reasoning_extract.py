from __future__ import annotations

from types import SimpleNamespace

from metateam.runtime.llm import extract_content_text, extract_reasoning_text
from metateam.runtime.think_tags import ThinkTagSplitter, split_think_tags


def test_extract_reasoning_string_fields() -> None:
    assert extract_reasoning_text(SimpleNamespace(reasoning_content="先看文件")) == "先看文件"
    assert extract_reasoning_text({"reasoning": "分步想"}) == "分步想"
    assert extract_reasoning_text(SimpleNamespace(thinking="内部推理")) == "内部推理"


def test_extract_reasoning_object_and_details() -> None:
    assert (
        extract_reasoning_text({"reasoning": {"content": "对象里的思考"}})
        == "对象里的思考"
    )
    assert (
        extract_reasoning_text(
            {"reasoning_details": [{"type": "reasoning.text", "text": "网关细节"}]}
        )
        == "网关细节"
    )


def test_extract_claude_thinking_blocks() -> None:
    msg = {
        "content": [
            {"type": "thinking", "thinking": "先确认需求。"},
            {"type": "text", "text": "可以开始改。"},
        ]
    }
    assert extract_reasoning_text(msg) == "先确认需求。"
    assert extract_content_text(msg) == "可以开始改。"


def test_extract_gemini_thought_flag() -> None:
    thinking = {"thought": True, "content": "这是思考增量"}
    assert extract_reasoning_text(thinking) == "这是思考增量"
    assert extract_content_text(thinking) == ""

    answer = {"content": "这是正文"}
    assert extract_reasoning_text(answer) == ""
    assert extract_content_text(answer) == "这是正文"


def test_extract_skips_encrypted_and_duplicates_prefer_first() -> None:
    msg = SimpleNamespace(
        reasoning_content="原生思考",
        reasoning="重复的另一份",
        content=[{"type": "thinking", "thinking": "块里的思考"}],
    )
    assert extract_reasoning_text(msg) == "原生思考"
    assert extract_reasoning_text({"reasoning_details": [{"type": "reasoning.encrypted"}]}) == ""


def test_think_tags_with_attributes() -> None:
    content, reasoning = split_think_tags('<think seed="1">内部</think>对外')
    assert reasoning == "内部"
    assert content == "对外"


def test_think_tags_stream_partial_open() -> None:
    splitter = ThinkTagSplitter()
    assert splitter.feed("<thi") == []
    assert splitter.feed('nk seed="x">abc') == [("reasoning", "abc")]
    assert splitter.feed("</think>ok") == [("content", "ok")]
    assert splitter.flush() == []


def test_stream_chat_emits_thinking_blocks_as_reasoning() -> None:
    class FakeStream:
        def __iter__(self):
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta={
                            "content": [
                                {"type": "thinking", "thinking": "先读代码。"},
                                {"type": "text", "text": "开始改。"},
                            ]
                        }
                    )
                ]
            )

        def close(self) -> None:
            return None

    from metateam.core.config import Settings
    from metateam.runtime.llm import LLM

    llm = LLM(Settings(demo_mode=True, api_key="", thinking_enabled=False, reasoning_effort=""))
    llm.demo = False
    llm.client = object()  # type: ignore[assignment]
    llm._create = lambda kwargs: FakeStream()  # type: ignore[method-assign]
    events = list(llm.stream_chat([{"role": "user", "content": "hi"}]))
    kinds = [kind for kind, _ in events]
    assert "reasoning_delta" in kinds
    assert "delta" in kinds
    reasoning = "".join(str(p) for k, p in events if k == "reasoning_delta")
    visible = "".join(str(p) for k, p in events if k == "delta")
    assert reasoning == "先读代码。"
    assert visible == "开始改。"
