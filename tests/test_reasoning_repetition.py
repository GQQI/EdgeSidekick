"""Short reasoning paragraphs must fold like long visible prose."""

from __future__ import annotations

from metateam.core.repetition import (
    fold_trailing_repeats,
    reasoning_hard_looped,
    repeated_tail_span,
    normalize_repeat_text,
)
from metateam.runtime.llm import (
    RepeatCut,
    _absorb_reasoning_piece,
    collapse_streaming_repeats,
    reasoning_hard_looped as llm_reasoning_hard_looped,
    reasoning_looped,
)


# A typical Chinese reasoning sentence: far below the old 80-char floor.
PARA = "我需要先检查页面结构和数据来源，然后再决定怎么修改这个区块。"


def test_reasoning_paragraph_is_shorter_than_content_floor() -> None:
    assert len(PARA) < 80


def test_short_reasoning_paragraph_is_folded_to_one_copy() -> None:
    parts: list[str] = []
    emitted = [""]
    deltas = [
        _absorb_reasoning_piece(parts, PARA, emitted=emitted) for _ in range(4)
    ]
    assert "".join(parts) == PARA
    assert deltas[0] == PARA
    assert deltas[1:] == ["", "", ""]


def test_rewrapped_repeat_counts_as_a_repeat() -> None:
    english = "I need to inspect the page structure and the data source before editing."
    rewrapped = english.replace(" ", "\n")
    assert normalize_repeat_text(rewrapped) == normalize_repeat_text(english)
    parts: list[str] = []
    emitted = [""]
    _absorb_reasoning_piece(parts, english, emitted=emitted)
    _absorb_reasoning_piece(parts, rewrapped, emitted=emitted)
    assert parts == [english]


def test_reasoning_hard_loop_triggers_on_three_short_copies() -> None:
    assert not reasoning_hard_looped(PARA * 2)
    assert reasoning_hard_looped(PARA * 3)
    assert repeated_tail_span(PARA * 3, min_span=24, min_reps=3) > 0


def test_long_paragraph_tape_is_detected() -> None:
    """A stuck model can repeat a whole block, not only a sentence."""
    unit = (" ".join(f"token{i}" for i in range(160)))[:700]
    assert not reasoning_hard_looped(unit)
    assert reasoning_hard_looped(unit * 3)


def test_reasoning_cut_is_immediate_but_content_threshold_stays() -> None:
    cut = RepeatCut(looped=reasoning_looped, hard=reasoning_hard_looped)
    assert cut.observe(PARA * 3) is True
    # Visible content keeps the conservative 80-char floor.
    cleaned, folded = collapse_streaming_repeats(PARA * 5)
    assert folded is False
    assert cleaned == PARA * 5


def test_fold_keeps_the_requested_number_of_copies() -> None:
    folded, did = fold_trailing_repeats(
        PARA * 4, min_span=16, max_keep=1
    )
    assert did is True
    assert folded == PARA


def test_llm_public_reasoning_probe_is_the_same_rule() -> None:
    assert llm_reasoning_hard_looped is reasoning_hard_looped
