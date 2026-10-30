"""Multi-step tool cycles are loops only when their results stop changing."""

from __future__ import annotations

from metateam.runtime.tool_repeats import call_rounds, stalled_tool_cycle


def _call(call_id: str, name: str, args: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


def _round(index: int, steps: list[tuple[str, str, str]], results: list[str]) -> list[dict]:
    calls = [
        _call(f"c{index}_{i}", name, args) for i, (name, args, _label) in enumerate(steps)
    ]
    rows = [
        {
            "role": "tool",
            "tool_call_id": call["id"],
            "name": call["function"]["name"],
            "content": result,
        }
        for call, result in zip(calls, results)
    ]
    return [{"role": "assistant", "content": "", "tool_calls": calls}, *rows]


def _messages(cycles: list[list[str]]) -> list[dict]:
    steps = [
        ("read_file", '{"path":"a.txt"}', "read"),
        ("str_replace", '{"path":"a.txt","old_string":"x","new_string":"y"}', "edit"),
    ]
    out: list[dict] = [{"role": "user", "content": "fix a.txt"}]
    for i, results in enumerate(cycles):
        out.extend(_round(i, steps, results))
    return out


def test_same_calls_with_same_results_are_a_stalled_cycle() -> None:
    rows = [
        ["1|same body", "updated a.txt"],
        ["1|same body", "updated a.txt"],
        ["1|same body", "updated a.txt"],
    ]
    # The two calls belong to one assistant round, so the round period is one.
    period = stalled_tool_cycle(_messages(rows), start=1)
    assert period == 1


def test_a_two_round_cycle_is_reported_as_period_two() -> None:
    read = [("read_file", '{"path":"a.txt"}', "read")]
    edit = [
        ("str_replace", '{"path":"a.txt","old_string":"x","new_string":"y"}', "edit")
    ]
    messages = [{"role": "user", "content": "fix a.txt"}]
    for i in range(3):
        messages.extend(_round(i * 2, read, ["same body"]))
        messages.extend(_round(i * 2 + 1, edit, ["updated a.txt"]))
    assert stalled_tool_cycle(messages, start=1) == 2


def test_changing_results_are_not_a_stalled_cycle() -> None:
    rows = [
        ["1|v1", "updated a.txt"],
        ["1|v2", "updated a.txt"],
        ["1|v3", "updated a.txt"],
    ]
    assert stalled_tool_cycle(_messages(rows), start=1) == 0


def test_a_recent_plateau_is_detected_after_earlier_progress() -> None:
    rows = [
        ["1|v1", "updated a.txt"],
        ["1|same", "updated a.txt"],
        ["1|same", "updated a.txt"],
        ["1|same", "updated a.txt"],
    ]
    assert stalled_tool_cycle(_messages(rows), start=1) == 1


def test_different_calls_are_work_not_a_cycle() -> None:
    messages = [{"role": "user", "content": "read many files"}]
    for i in range(3):
        messages.extend(
            _round(
                i,
                [
                    ("read_file", f'{{"path":"file{i}.txt"}}', "read"),
                    ("str_replace", f'{{"path":"file{i}.txt","old":"x","new":"y"}}', "edit"),
                ],
                ["same body", "updated"],
            )
        )
    assert stalled_tool_cycle(messages, start=1) == 0


def test_browser_cycles_are_left_to_the_live_page_guard() -> None:
    """A screenshot path repeats while the page underneath changes."""
    messages = [{"role": "user", "content": "inspect the page"}]
    for i in range(3):
        messages.extend(
            _round(
                i,
                [
                    ("browser_navigate", '{"url":"http://localhost/a"}', "nav"),
                    ("browser_screenshot", "{}", "shot"),
                ],
                ["http://localhost/a", ".sidekick/browser/shot.png"],
            )
        )
    assert stalled_tool_cycle(messages, start=1) == 0


def test_call_rounds_keeps_multi_call_rounds_together() -> None:
    messages = _messages(
        [
            ["1|v1", "updated"],
            ["1|v2", "updated"],
        ]
    )
    rounds = call_rounds(messages, start=1)
    assert len(rounds) == 2
    assert len(rounds[0].signatures) == 2
    assert rounds[0].names == ("read_file", "str_replace")
    assert rounds[0].results == ("1|v1", "updated")
