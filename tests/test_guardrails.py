from __future__ import annotations

import json

from metateam.core.guardrails import Guardrails, looks_failed, repeating_tool_sequence


def test_looks_failed() -> None:
    assert looks_failed("ERROR: nope")
    assert looks_failed('{"error": "x"}')
    assert looks_failed("Traceback (most recent call last):")
    assert not looks_failed("wrote src/a.py (12 chars)")
    # A successful console read often includes log level "error".
    console = '{"count": 1, "logs": [{"type": "error", "text": "failed to fetch"}]}'
    assert not looks_failed(console)
    page = '{"url": "http://localhost:5173/", "observation": "button says \\"error\\""}'
    assert not looks_failed(page)


def test_repeat_fail_blocks() -> None:
    g = Guardrails(same_call_fail_limit=2, same_call_ok_limit=1, max_explore_streak=8)
    args = {"path": "a.py"}
    assert g.before("read_file", args) is None
    g.after("read_file", args, "ERROR: missing")
    assert g.before("read_file", args) is None
    g.after("read_file", args, "ERROR: missing")
    blocked = g.before("read_file", args)
    assert blocked is not None
    assert "blocked repeated failing" in blocked


def test_identical_ok_explore_not_repeated() -> None:
    g = Guardrails(same_call_ok_limit=1, max_explore_streak=8)
    args = {"path": "./src"}
    assert g.before("list_dir", args) is None
    g.after("list_dir", args, "file\tapp.py")
    again = g.before("list_dir", {"path": "src"})
    assert again is not None
    assert "already returned a result" in again


def test_read_file_identical_reread_allowed() -> None:
    g = Guardrails(same_call_ok_limit=1, max_explore_streak=8)
    args = {"path": "tsinghua.html", "offset": 193, "limit": 0}
    assert g.before("read_file", args) is None
    g.after("read_file", args, "193|<body>\n… lines 193-468 of 468")
    # Same slice may be fetched again (Claude: re-read if you still need it).
    assert g.before("read_file", args) is None


def test_read_file_counts_as_explore_streak() -> None:
    g = Guardrails(max_explore_streak=2, same_call_ok_limit=4)
    for i, path in enumerate(("a.py", "b.py")):
        assert g.before("read_file", {"path": path}) is None
        g.after("read_file", {"path": path}, f"{i}|ok")
    blocked = g.before("list_dir", {"path": "."})
    assert blocked is not None
    assert "explore streak" in blocked


def test_explore_only_skips_streak() -> None:
    g = Guardrails(max_explore_streak=2, same_call_ok_limit=8)
    g.set_explore_only(True)
    for path in ("a.py", "b.py", "c.py"):
        assert g.before("read_file", {"path": path}) is None
        g.after("read_file", {"path": path}, "ok")
    assert g.before("list_dir", {"path": "."}) is None


def test_parallel_inflight_same_args_not_blocked() -> None:
    g = Guardrails(same_call_ok_limit=1, max_explore_streak=8)
    args = {"query": "needle", "path": "."}
    assert g.before("search_text", args) is None
    # Second call still in-flight (no after yet) — parallel batch.
    assert g.before("search_text", args) is None
    g.after("search_text", args, "src/a.py:1:needle")
    g.after("search_text", args, "src/a.py:1:needle")
    # After success, a later identical call is still blocked.
    again = g.before("search_text", args)
    assert again is not None
    assert "already returned a result" in again


def test_console_error_logs_are_not_a_failing_call() -> None:
    g = Guardrails(same_call_fail_limit=2, same_call_ok_limit=4, max_explore_streak=8)
    args = {"limit": 40}
    payload = '{"count": 2, "logs": [{"type": "error", "text": "x"}]}'
    assert g.before("browser_console", args) is None
    g.after("browser_console", args, payload)
    assert g.before("browser_console", args) is None
    g.after("browser_console", args, payload)
    blocked = g.before("browser_console", args)
    assert blocked is None or "failing" not in blocked


def test_browser_live_tools_can_be_called_again() -> None:
    g = Guardrails(same_call_ok_limit=1, max_explore_streak=8)
    cases = (
        ("browser_navigate", {"url": "http://localhost:5173/"}, '{"url": "http://localhost:5173/"}'),
        ("browser_snapshot", {"limit": 40}, '{"url": "http://localhost:5173/", "count": 3}'),
        ("browser_console", {"limit": 40}, '{"count": 1, "logs": [{"type": "error", "text": "x"}]}'),
        ("browser_diagnostics", {"limit": 20}, '{"url": "http://localhost:5173/", "console_error_total": 0}'),
    )
    for name, args, payload in cases:
        assert g.before(name, args) is None
        g.after(name, args, payload)
        assert g.before(name, args) is None
        g.after(name, args, payload)


def test_repeating_tool_sequence_detects_a_fixed_cycle() -> None:
    cycle = [
        "browser_navigate",
        "browser_console",
        "shell_job_log",
        "str_replace",
        "shell_job_list",
        "shell_job_stop",
    ]
    assert repeating_tool_sequence(cycle * 2) is False
    assert repeating_tool_sequence(cycle * 3) is True
    assert repeating_tool_sequence(["browser_navigate"] * 4) is True
    assert repeating_tool_sequence(["run_shell"] * 6) is False
    assert repeating_tool_sequence(["write_file", "read_file", "run_shell"]) is False


def test_identical_ok_browser_click_can_run_again() -> None:
    g = Guardrails(same_call_ok_limit=1, max_explore_streak=8)
    args = {"index": 4}
    assert g.before("browser_click", args) is None
    g.after("browser_click", args, '{"url": "http://localhost:5175/login", "action": "click"}')
    assert g.before("browser_click", args) is None


def test_identical_ok_browser_navigate_can_refresh() -> None:
    g = Guardrails(same_call_ok_limit=1, max_explore_streak=8)
    args = {"url": "https://www.tsinghua.edu.cn/"}
    assert g.before("browser_navigate", args) is None
    g.after("browser_navigate", args, '{"url": "https://www.tsinghua.edu.cn/"}')
    # The page and the compacted history both go stale. A later call is allowed.
    assert g.before("browser_navigate", args) is None


def test_parallel_browser_navigate_blocked() -> None:
    g = Guardrails(same_call_ok_limit=1, max_explore_streak=8)
    args = {"url": "https://example.com"}
    assert g.before("browser_navigate", args) is None
    blocked = g.before("browser_navigate", args)
    assert blocked is not None
    assert "already running" in blocked


def test_dedup_read_full_coverage_serves_stub() -> None:
    g = Guardrails(same_call_ok_limit=1, max_explore_streak=8)
    args1 = {"path": "src/app.css", "offset": 110, "limit": 90}
    assert g.before("read_file", args1) is None
    g.after("read_file", args1, "110|a\n…\n… lines 110-199 of 400")
    # Fully-contained re-read: serve a short stub instead of duplicate bytes.
    args2 = {"path": "src/app.css", "offset": 140, "limit": 60}
    assert g.before("read_file", args2) is None
    served, adjusted = g.dedup_read("read_file", args2)
    assert served is not None
    assert served.startswith("[already in context]")
    assert "110-199" in served
    assert adjusted is None


def test_dedup_read_head_overlap_narrows_to_new_lines() -> None:
    g = Guardrails(same_call_ok_limit=1, max_explore_streak=8)
    args1 = {"path": "tsinghua.html", "offset": 1, "limit": 136}
    assert g.before("read_file", args1) is None
    g.after("read_file", args1, "1|<html>\n… lines 1-136 of 468")
    # Request 40-200: head 40-136 is already in context → serve only 137-200.
    args2 = {"path": "tsinghua.html", "offset": 40, "limit": 161}
    served, adjusted = g.dedup_read("read_file", args2)
    assert served is None
    assert adjusted is not None
    assert adjusted["offset"] == 137
    assert adjusted["limit"] == 64


def test_dedup_read_new_range_untouched() -> None:
    g = Guardrails(same_call_ok_limit=1, max_explore_streak=8)
    args1 = {"path": "src/app.css", "offset": 1, "limit": 100}
    assert g.before("read_file", args1) is None
    g.after("read_file", args1, "1|a\n… lines 1-100 of 400")
    # Genuinely new lines: run as-is.
    args2 = {"path": "src/app.css", "offset": 101, "limit": 100}
    assert g.before("read_file", args2) is None
    served, adjusted = g.dedup_read("read_file", args2)
    assert served is None
    assert adjusted is None


def test_dedup_read_entire_file_says_stop() -> None:
    g = Guardrails(same_call_ok_limit=1, max_explore_streak=8)
    args1 = {"path": "tsinghua.html", "offset": 1, "limit": 0}
    assert g.before("read_file", args1) is None
    g.after("read_file", args1, "1|<html>\n… lines 1-468 of 468")
    served, adjusted = g.dedup_read("read_file", {"path": "tsinghua.html", "offset": 330})
    assert adjusted is None
    assert served is not None
    assert "already in this conversation" in served


def test_dedup_read_repeat_escalates_to_error() -> None:
    g = Guardrails(same_call_ok_limit=1, max_explore_streak=8)
    args1 = {"path": "tsinghua.html", "offset": 1, "limit": 0}
    assert g.before("read_file", args1) is None
    g.after("read_file", args1, "1|<html>\n… lines 1-468 of 468")
    repeat = {"path": "tsinghua.html", "offset": 330}
    served1, _ = g.dedup_read("read_file", repeat)
    assert served1 is not None and served1.startswith("[already in context]")
    served2, _ = g.dedup_read("read_file", repeat)
    assert served2 is not None and served2.startswith("[already in context]")
    assert not served2.startswith("ERROR")


def test_dedup_read_no_limit_uses_known_total() -> None:
    g = Guardrails(same_call_ok_limit=1, max_explore_streak=8)
    args1 = {"path": "notes.md", "offset": 1, "limit": 0}
    assert g.before("read_file", args1) is None
    g.after("read_file", args1, "1|a\n… lines 1-400 of 400")
    # No limit given, but we know total=400, so this is a full subset.
    args2 = {"path": "notes.md", "offset": 200, "limit": 0}
    served, adjusted = g.dedup_read("read_file", args2)
    assert served is not None
    assert served.startswith("[already in context]")
    assert adjusted is None


def test_reconcile_drops_coverage_when_payload_was_cleared() -> None:
    g = Guardrails(same_call_ok_limit=1, max_explore_streak=8)
    args = {"path": "src/app.css", "offset": 200, "limit": 200}
    assert g.before("read_file", args) is None
    g.after("read_file", args, "200|a\n… lines 200-399 of 800")
    g.reconcile_read_coverage(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "c1",
                        "function": {"name": "read_file", "arguments": json.dumps(args)},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "c1",
                "name": "read_file",
                "content": (
                    "[cleared tool result: read_file lines 200-399 of 800. "
                    "The payload was dropped to free context.]"
                ),
            },
        ]
    )
    served, adjusted = g.dedup_read(
        "read_file", {"path": "src/app.css", "offset": 200, "limit": 50}
    )
    assert served is None
    assert adjusted is None


def test_reconcile_keeps_only_lines_still_visible() -> None:
    g = Guardrails(same_call_ok_limit=1, max_explore_streak=8)
    args = {"path": "src/app.css", "offset": 1, "limit": 400}
    body = "\n".join(f"{i}|x" for i in range(1, 401)) + "\n… lines 1-400 of 900"
    assert g.before("read_file", args) is None
    g.after("read_file", args, body)
    visible = "\n".join(f"{i}|x" for i in range(1, 21))
    g.reconcile_read_coverage(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "c1",
                        "function": {"name": "read_file", "arguments": json.dumps(args)},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "c1",
                "name": "read_file",
                "content": visible,
            },
        ]
    )
    hit, _ = g.dedup_read("read_file", {"path": "src/app.css", "offset": 5, "limit": 5})
    assert hit is not None and hit.startswith("[already in context]")
    missed, adjusted = g.dedup_read(
        "read_file", {"path": "src/app.css", "offset": 100, "limit": 20}
    )
    assert missed is None
    assert adjusted is None


def test_read_file_range_cleared_after_mutation() -> None:
    g = Guardrails(same_call_ok_limit=1, max_explore_streak=8)
    args1 = {"path": "src/app.css", "offset": 1, "limit": 200}
    assert g.before("read_file", args1) is None
    g.after("read_file", args1, "1|a\n… lines 1-200 of 200")
    assert g.before("write_file", {"path": "src/app.css", "content": "x"}) is None
    g.after("write_file", {"path": "src/app.css", "content": "x"}, "wrote src/app.css")
    # File changed — the old coverage must not dedupe the fresh read.
    args2 = {"path": "src/app.css", "offset": 50, "limit": 50}
    assert g.before("read_file", args2) is None
    served, adjusted = g.dedup_read("read_file", args2)
    assert served is None
    assert adjusted is None


def test_mutating_tool_resets_explore_streak() -> None:
    g = Guardrails(max_explore_streak=2, same_call_ok_limit=4)
    assert g.before("read_file", {"path": "a.py"}) is None
    g.after("read_file", {"path": "a.py"}, "ok")
    assert g.before("write_file", {"path": "a.py", "content": "x"}) is None
    g.after("write_file", {"path": "a.py", "content": "x"}, "wrote a.py")
    assert g.before("read_file", {"path": "b.py"}) is None
    g.after("read_file", {"path": "b.py"}, "ok")


def test_same_successful_delete_file_is_blocked() -> None:
    g = Guardrails(same_call_ok_limit=1)
    g.begin_turn()
    assert g.before("delete_file", {"path": "junk.txt"}) is None
    g.after("delete_file", {"path": "junk.txt"}, "deleted junk.txt")
    blocked = g.before("delete_file", {"path": "junk.txt"})
    assert blocked is not None
    assert "already returned" in blocked
    assert "junk.txt" in blocked or "delete_file" in blocked
    # A different path is still allowed.
    assert g.before("delete_file", {"path": "other.txt"}) is None
