from __future__ import annotations

from types import SimpleNamespace

from metateam.core.config import Settings
from metateam.runtime.llm import (
    LLM,
    RepeatCut,
    _absorb_stream_piece,
    _strip_for_api,
    collapse_streaming_repeats,
    merge_reasoning_fragment,
    streaming_text_hard_looped,
    streaming_text_looped,
)


PARA = (
    " Let me trace through the actual error more carefully to see where this "
    "/undefined path is coming from. The __img__ placeholder should resolve to "
    "./images/s6ae2c5e1b1234.jpg, but the error shows /undefined instead, which "
    "suggests the replacement isn't happening correctly or there's a caching "
    "issue with the JavaScript file."
)


def test_hard_loop_detects_english_paragraph_x3() -> None:
    assert not streaming_text_hard_looped(PARA)
    assert not streaming_text_hard_looped(PARA * 2)
    assert streaming_text_hard_looped(PARA * 3)
    assert streaming_text_looped(PARA * 3)


def test_collapse_keeps_two_copies() -> None:
    cleaned, did = collapse_streaming_repeats(PARA * 5)
    assert did
    assert cleaned == PARA * 2
    assert not streaming_text_hard_looped(cleaned)


def test_absorb_stream_piece_no_double_full_snapshot() -> None:
    parts: list[str] = []
    emitted = [""]
    d1 = _absorb_stream_piece(parts, "Hello", emitted=emitted)
    d2 = _absorb_stream_piece(parts, "Hello world", emitted=emitted)
    d3 = _absorb_stream_piece(parts, "Hello world!", emitted=emitted)
    assert d1 == "Hello"
    assert d2 == " world"
    assert d3 == "!"
    assert "".join(parts) == "Hello world!"


def test_absorb_stream_piece_truncates_repeated_paragraphs() -> None:
    parts: list[str] = []
    emitted = [""]
    deltas = []
    for _ in range(3):
        deltas.append(_absorb_stream_piece(parts, PARA, emitted=emitted))
    joined = "".join(parts)
    assert joined == PARA * 2
    assert deltas[0] == PARA
    assert deltas[1] == PARA
    assert deltas[2] == ""


def test_repeat_cut_hard_loop_is_immediate() -> None:
    cut = RepeatCut(seconds=20, now=lambda: 0.0)
    assert cut.observe(PARA * 3) is True


def test_strip_for_api_drops_reasoning() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "answer",
            "reasoning": "long private chain of thought " * 20,
            "reasoning_content": "should also vanish",
        },
        {"role": "user", "content": "next"},
    ]
    stripped = _strip_for_api(messages)
    assistant = next(m for m in stripped if m["role"] == "assistant")
    assert "reasoning" not in assistant
    assert "reasoning_content" not in assistant
    assert assistant["content"] == "answer"


def test_merge_reasoning_fragment_grows_snapshot_and_appends_delta() -> None:
    assert merge_reasoning_fragment("ab", "abcd") == "abcd"
    assert merge_reasoning_fragment("abcd", "abcd") == "abcdabcd"
    assert merge_reasoning_fragment("abcdabcd", "abcd") == "abcdabcdabcd"


def test_thinking_turn_raises_a_near_greedy_temperature() -> None:
    """0.2 is a known loop trigger for thinking models.

    Judge/summarizer calls pin 0.0–0.1 explicitly and must stay deterministic,
    so the floor applies only when the caller left the temperature unset.
    """
    llm = LLM(
        Settings(
            demo_mode=True,
            api_key="",
            thinking_enabled=True,
            temperature=0.2,
        )
    )
    assert llm._effective_temperature(None) == 0.6
    assert llm._effective_temperature(0.1) == 0.1

    plain = LLM(
        Settings(
            demo_mode=True,
            api_key="",
            thinking_enabled=False,
            temperature=0.2,
        )
    )
    assert plain._effective_temperature(None) == 0.2


def test_chinese_reasoning_loop_is_cut_and_shown_once() -> None:
    """The reported symptom: a short Chinese sentence loops while thinking."""
    sentence = "这个文件里面没有任何需要修改的错误并且可以直接运行"

    class FakeStream:
        def __init__(self) -> None:
            self.closed = False

        def __iter__(self):
            for _ in range(8):
                if self.closed:
                    return
                yield SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(reasoning_content=sentence)
                        )
                    ]
                )

        def close(self) -> None:
            self.closed = True

    llm = LLM(
        Settings(
            demo_mode=True,
            api_key="",
            thinking_enabled=True,
            reasoning_effort="",
            llm_idle_timeout=0,
            llm_stream_timeout=0,
        )
    )
    llm.demo = False
    llm.client = object()  # type: ignore[assignment]
    stream = FakeStream()
    llm._create = lambda kwargs: stream  # type: ignore[method-assign]

    events = list(llm.stream_chat([{"role": "user", "content": "hi"}]))
    done = next(payload for kind, payload in events if kind == "done")
    assert done.get("sidekick_looped") is True
    assert stream.closed
    assert str(done.get("reasoning") or "").count(sentence) == 1


def test_chinese_visible_loop_is_cut_and_folded() -> None:
    """Visible prose used to ignore repeats below the old 80-char floor."""
    sentence = "这个文件里面没有任何需要修改的错误并且可以直接运行"

    class FakeStream:
        def __init__(self) -> None:
            self.closed = False

        def __iter__(self):
            for _ in range(8):
                if self.closed:
                    return
                yield SimpleNamespace(
                    choices=[
                        SimpleNamespace(delta=SimpleNamespace(content=sentence))
                    ]
                )

        def close(self) -> None:
            self.closed = True

    llm = LLM(
        Settings(
            demo_mode=True,
            api_key="",
            thinking_enabled=False,
            llm_idle_timeout=0,
            llm_stream_timeout=0,
        )
    )
    llm.demo = False
    llm.client = object()  # type: ignore[assignment]
    stream = FakeStream()
    llm._create = lambda kwargs: stream  # type: ignore[method-assign]

    events = list(llm.stream_chat([{"role": "user", "content": "hi"}]))
    done = next(payload for kind, payload in events if kind == "done")
    assert done.get("sidekick_looped") is True
    assert stream.closed
    assert str(done.get("content") or "").count(sentence) == 2


def test_non_stream_fallback_folds_a_tape_in_one_message() -> None:
    """A provider without streaming can return the whole loop as one message."""
    sentence = "这个文件里面没有任何需要修改的错误并且可以直接运行"
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=sentence * 8,
                    reasoning_content="",
                    reasoning="",
                    tool_calls=None,
                )
            )
        ]
    )
    llm = LLM(
        Settings(
            demo_mode=True,
            api_key="",
            thinking_enabled=False,
            llm_idle_timeout=0,
            llm_stream_timeout=0,
        )
    )
    llm.demo = False
    llm.client = object()  # type: ignore[assignment]
    llm._create = lambda kwargs: response  # type: ignore[method-assign]

    out = llm.chat([{"role": "user", "content": "hi"}], fold_repeats=True)
    assert out.get("sidekick_looped") is True
    assert out["content"] == sentence * 2


def test_stream_cut_sees_raw_copies_although_the_display_is_folded() -> None:
    """Regression: the cutter used the folded buffer, so it never saw a loop.

    ``_absorb_reasoning_piece`` keeps one copy. Hitting the cutter with that
    buffer made every exact repetition invisible, and the provider kept
    streaming until max_tokens (or the 180s cap) instead of being stopped.
    """

    class FakeStream:
        def __init__(self, pieces: list[str]) -> None:
            self.pieces = list(pieces)
            self.closed = False

        def __iter__(self):
            for piece in self.pieces:
                if self.closed:
                    return
                yield SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(reasoning_content=piece)
                        )
                    ]
                )

        def close(self) -> None:
            self.closed = True

    settings = Settings(
        demo_mode=True,
        api_key="",
        thinking_enabled=True,
        reasoning_effort="",
        llm_idle_timeout=0,
        llm_stream_timeout=0,
    )
    llm = LLM(settings)
    llm.demo = False
    llm.client = object()  # type: ignore[assignment]
    stream = FakeStream([PARA] * 6)
    llm._create = lambda kwargs: stream  # type: ignore[method-assign]

    events = list(llm.stream_chat([{"role": "user", "content": "hi"}]))
    done = next(payload for kind, payload in events if kind == "done")
    assert done.get("sidekick_looped") is True
    assert stream.closed
    # The displayed history keeps one readable copy, not the tape.
    reasoning = str(done.get("reasoning") or "")
    assert reasoning.count("Let me trace through the actual error") == 1
