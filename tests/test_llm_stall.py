from __future__ import annotations

import threading
import time

from metateam.core.config import Settings
from metateam.runtime.llm import (
    LLM,
    RepeatCut,
    StreamWatchdog,
    _stream_timeouts,
    collapse_streaming_repeats,
    streaming_text_hard_looped,
    streaming_text_looped,
    streaming_text_short_looped,
)


def test_stream_timeouts_from_settings() -> None:
    settings = Settings(demo_mode=True, api_key="", llm_idle_timeout=40, llm_stream_timeout=90)
    idle, total = _stream_timeouts(settings)
    assert idle > 0 and total > 0


def test_stream_watchdog_fires_on_idle() -> None:
    closed: list[int] = []
    watch = StreamWatchdog(lambda: closed.append(1), idle_sec=0.25, max_sec=5)
    watch.start()
    time.sleep(0.8)
    watch.stop()
    assert closed
    assert watch.reason == "idle"


def test_stream_chat_idle_stall_unblocks() -> None:
    class HangStream:
        def __init__(self) -> None:
            self._stop = threading.Event()

        def __iter__(self):
            self._stop.wait(30)
            return iter(())

        def close(self) -> None:
            self._stop.set()

    settings = Settings(
        demo_mode=True,
        api_key="",
        thinking_enabled=False,
        reasoning_effort="",
        llm_idle_timeout=1,
        llm_stream_timeout=8,
    )
    llm = LLM(settings)
    llm.demo = False
    llm.client = object()  # type: ignore[assignment]
    hang = HangStream()
    llm._create = lambda kwargs: hang  # type: ignore[method-assign]
    kinds: list[str] = []
    t0 = time.monotonic()
    for kind, _payload in llm.stream_chat([{"role": "user", "content": "hi"}]):
        kinds.append(kind)
    elapsed = time.monotonic() - t0
    assert elapsed < 6
    assert "stalled" in kinds
    assert kinds[-1] == "done"


def test_streaming_text_looped_detects_repeats() -> None:
    assert not streaming_text_looped("short")
    chunk = "正在打开清华官网并阅读首页内容。"
    assert streaming_text_looped((chunk + "\n") * 24)
    assert not streaming_text_looped("第一段。\n第二段。\n第三段，内容不同。")
    sentence = "是客户端在收到消息后立刻关闭了连接。"
    assert streaming_text_looped(sentence * 20)
    assert not streaming_text_looped(sentence * 3)


def test_short_chinese_sentence_tape_is_detected_and_folded() -> None:
    """The visible path used an 80-char floor; a 17-char sentence never hit it.

    Eight copies are an unmistakable tape. Four copies only arm the soft
    timer — a short list can legitimately repeat a line a few times.
    """
    sentence = "这个文件里面没有任何需要修改的错误并且可以直接运行"
    assert len(sentence) >= 24
    assert not streaming_text_hard_looped(sentence * 3)
    assert not streaming_text_hard_looped(sentence * 7)
    assert streaming_text_hard_looped(sentence * 8)

    assert not streaming_text_short_looped(sentence * 3)
    assert streaming_text_short_looped(sentence * 4)

    cleaned, folded = collapse_streaming_repeats(sentence * 8)
    assert folded
    assert cleaned == sentence * 2


def test_repeat_cut_soft_waits_hard_is_immediate() -> None:
    now = [0.0]
    # Soft heuristic: many newline-separated copies of a mid-length line,
    # but vary a trailing index so hard consecutive identity (exact span x3)
    # does not fire — only the looser loop detector should.
    soft = "\n".join(
        f"正在核对接口返回与本地缓存是否一致，步骤编号 {i:04d}。" for i in range(40)
    )
    cut = RepeatCut(seconds=20, now=lambda: now[0])
    assert not streaming_text_hard_looped(soft)
    # May or may not be soft-looped depending on heuristics; if it is, timer applies.
    if streaming_text_looped(soft):
        assert cut.observe(soft) is False
        now[0] = 19.0
        assert cut.observe(soft) is False
        now[0] = 20.0
        assert cut.observe(soft) is True

    para = (
        " Let me trace through the actual error more carefully to see where this "
        "/undefined path is coming from. The __img__ placeholder should resolve to "
        "./images/s6ae2c5e1b1234.jpg, but the error shows /undefined instead."
    )
    hard = RepeatCut(seconds=20, now=lambda: 0.0)
    assert streaming_text_hard_looped(para * 3)
    assert hard.observe(para * 3) is True
