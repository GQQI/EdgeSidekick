"""A round of thinking that only restates the previous round is not re-shown."""

from __future__ import annotations

from metateam.runtime.reasoning_gate import ReasoningRoundGate, dedupe_reasoning_round

PREVIOUS = "Let me continue downloading the remaining images and then generate the pku.html file."


def test_pure_restatement_is_suppressed() -> None:
    gate = ReasoningRoundGate(PREVIOUS)
    assert gate.observe("Let me continue downloading the ") == ""
    assert gate.observe("remaining images and then generate the pku.html file.") == ""
    assert gate.suppressed is True
    assert gate.flush() == ""


def test_a_shorter_copy_is_suppressed() -> None:
    gate = ReasoningRoundGate(PREVIOUS)
    assert gate.observe("Let me continue downloading the remaining images.") == ""
    assert gate.suppressed is True


def test_new_thinking_is_released_without_the_shared_words() -> None:
    gate = ReasoningRoundGate(PREVIOUS)
    # "Let me c…" is already shown above; only the new words are painted.
    assert gate.observe("Let me check the CSS instead.") == "check the CSS instead."
    assert gate.suppressed is False


def test_divergence_after_a_shared_opening_releases_the_new_tail() -> None:
    gate = ReasoningRoundGate(PREVIOUS)
    assert gate.observe("Let me continue downloading the ") == ""
    assert gate.observe("CSS files instead.") == "CSS files instead."


def test_a_repeated_prefix_is_not_painted_again() -> None:
    gate = ReasoningRoundGate(PREVIOUS)
    assert gate.observe(PREVIOUS + " Then verify the page.") == (
        "Then verify the page."
    )


def test_no_previous_round_streams_immediately() -> None:
    gate = ReasoningRoundGate("")
    assert gate.observe("Thinking about the next step.") == "Thinking about the next step."
    assert gate.suppressed is False


def test_whitespace_rewrap_does_not_defeat_detection() -> None:
    previous = "Let me check the page.\n\nThen fix the image path."
    gate = ReasoningRoundGate(previous)
    assert gate.observe("Let me check the page. Then fix\n the image path.") == ""
    assert gate.suppressed is True


def test_punctuation_and_width_differences_do_not_defeat_detection() -> None:
    """No punctuation list is involved: layout is dropped, not enumerated."""
    gate = ReasoningRoundGate("Let me check the page, then fix the image path.")
    assert gate.observe("Let me check the page。Then fix the image path！") == ""
    assert gate.suppressed is True


def test_whole_block_helper_matches_the_stream_gate() -> None:
    assert dedupe_reasoning_round(PREVIOUS, PREVIOUS) == ""
    assert dedupe_reasoning_round(PREVIOUS, "Something new.") == "Something new."
    assert dedupe_reasoning_round("", "Something new.") == "Something new."
