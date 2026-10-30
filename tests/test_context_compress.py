from __future__ import annotations

from metateam.runtime.context import (
    COMPACTION_MARK,
    _build_compaction_message,
    _tool_result_stub,
    cheap_compact,
    compress_messages,
    current_turn_start,
    ensure_fit,
    extract_durable_pins,
    is_successful_mutation,
    message_tokens,
    messages_tokens,
    obligation_from_compaction,
    park_overflow,
    repair_incomplete_tool_chains,
)


def _tool(name: str, content: str) -> dict:
    return {"role": "tool", "name": name, "content": content}


def test_message_tokens_ignore_reasoning_not_sent_to_provider() -> None:
    short = message_tokens({"role": "assistant", "content": "ok", "tool_calls": []})
    long = message_tokens(
        {
            "role": "assistant",
            "content": "ok",
            "tool_calls": [],
            "reasoning": "再确认一遍接口 " * 400,
        }
    )
    assert long == short


def test_park_overflow_archives_old_turns_and_shrinks_the_live_window() -> None:
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(6):
        msgs.append({"role": "user", "content": f"task {i} " + "要求 " * 30})
        msgs.append(
            {
                "role": "tool",
                "name": "read_file",
                "content": f"{i}|body\n… lines 1-20 of 20\n" + ("X" * 3000),
            }
        )
    before = messages_tokens(msgs)
    live, archived = park_overflow(msgs, context_limit=2500, keep_recent_tokens=1200)
    assert archived
    assert messages_tokens(live) < before
    assert live[0]["role"] == "system"
    assert any("task 0" in str(m.get("content") or "") for m in archived)
    assert any("task 5" in str(m.get("content") or "") for m in live)


def test_cheap_compact_trims_old_tool_dumps() -> None:
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "open tsinghua"},
        _tool("read_file", "1|hello\n… lines 1-136 of 200\n" + ("X" * 8000)),
        _tool("browser_get_page_content", "Y" * 5000),
    ]
    out, did = cheap_compact(msgs, old_cap=400, recent_cap=800, recent_tools=1)
    assert did
    assert out[2]["content"].startswith("[cleared tool result:")
    assert "lines 1-136 of 200" in out[2]["content"]
    assert "read_file" in out[2]["content"]
    assert len(out[3]["content"]) < 900
    assert "open tsinghua" in out[1]["content"]


def test_current_turn_start_skips_internal_and_compaction() -> None:
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "第一轮任务"},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": f"{COMPACTION_MARK}\nsummary"},
        {"role": "user", "content": "hint", "sidekick_internal": True},
        {"role": "user", "content": "修改tsinghua.html"},
        _tool("read_file", "1|<html>… lines 1-400 of 468"),
    ]
    assert current_turn_start(msgs) == 5


def test_cheap_compact_never_clears_active_turn_reads() -> None:
    big_read = "1|<html>\n" + ("X" * 8000) + "\n… lines 1-400 of 468"
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "旧任务"},
        _tool("read_file", "old " * 800),  # previous turn — may be stubbed
        {"role": "user", "content": "修改tsinghua.html"},
        _tool("read_file", big_read),  # active turn — must stay intact
        _tool("read_file", "401|<body>\n" + "Y" * 5000 + "\n… lines 401-468 of 468"),
    ]
    out, did = cheap_compact(
        msgs, old_cap=400, recent_cap=600, recent_tools=0,
        protect_from=current_turn_start(msgs),
    )
    assert did
    # Previous turn's dump was stubbed…
    assert out[2]["content"].startswith("[cleared tool result:")
    # …but the active turn's reads are byte-identical.
    assert out[4]["content"] == big_read
    assert "Y" * 5000 in out[5]["content"]


def test_extract_pins_keeps_user_and_url() -> None:
    msgs = [
        {"role": "user", "content": "打开清华大学官网并截图"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "browser_navigate",
                        "arguments": '{"url": "https://www.tsinghua.edu.cn/"}',
                    }
                }
            ],
        },
        _tool("browser_navigate", '{"url": "https://www.tsinghua.edu.cn/"}'),
    ]
    pins = extract_durable_pins(msgs)
    assert "打开清华大学官网并截图" in pins
    assert "https://www.tsinghua.edu.cn/" in pins


def test_ensure_fit_single_llm_pass() -> None:
    calls = {"n": 0}

    class FakeLLM:
        def complete_text(self, system: str, user: str, temperature: float = 0.1) -> str:
            calls["n"] += 1
            return "## Goal\nkeep going\n## Next\ncontinue"

    msgs = [{"role": "system", "content": "sys"}]
    for i in range(12):
        msgs.append({"role": "user", "content": f"task {i} " + "重要约束不要忘记 " * 40})
        msgs.append(_tool("read_file", ("file contents %d " % i) * 400))
    before = messages_tokens(msgs)
    out, meta = ensure_fit(
        msgs,
        context_limit=max(3000, before // 3),
        keep_recent_tokens=800,
        trigger_ratio=0.7,
        max_attempts=3,
        llm=FakeLLM(),
    )
    assert calls["n"] <= 1
    assert meta.get("compressed")
    joined = "\n".join(str(m.get("content") or "") for m in out)
    assert "重要约束不要忘记" in joined


def test_compress_merges_prior_pins() -> None:
    prior = (
        f"{COMPACTION_MARK}\n\n## Pinned (do not drop)\n### User requests\n"
        "- 第一轮目标：部署到内网\n\n## Working memory\n## Goal\nold"
    )
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": prior},
        {"role": "user", "content": "继续，别忘了内网"},
        _tool("list_dir", ("index.html and more files " * 80)),
        _tool("read_file", "old dump " * 200),
    ]
    out, summary, did, dropped = compress_messages(
        msgs,
        context_limit=400,
        keep_recent_tokens=80,
        trigger_ratio=0.5,
        llm=None,
        prior_summary="",
    )
    assert did
    blob = "\n".join(str(m.get("content") or "") for m in out)
    assert "部署到内网" in blob or "内网" in blob
    assert summary
    assert isinstance(dropped, list)


def test_ensure_fit_archives_dropped_messages_for_display() -> None:
    """Whole turns removed from the working set must survive for the UI/disk transcript."""
    msgs = [{"role": "system", "content": "sys"}]
    markers = []
    for i in range(40):
        marker = f"UNIQUE-MARKER-{i}-别忘记这条早期消息"
        markers.append(marker)
        msgs.append({"role": "user", "content": marker + " " + ("补充内容 " * 60)})
        msgs.append({"role": "assistant", "content": "收到 " + ("处理详情 " * 60)})
    out, meta = ensure_fit(
        msgs,
        context_limit=6000,
        keep_recent_tokens=1500,
        trigger_ratio=0.6,
        max_attempts=1,
        llm=None,
    )
    dropped = meta.get("dropped_messages") or []
    assert dropped, "compaction must report what it removed from the working set"
    # Every dropped message must be a real, whole message (not a lossy stub).
    dropped_blob = "\n".join(str(m.get("content") or "") for m in dropped)
    kept_blob = "\n".join(str(m.get("content") or "") for m in out)
    # At least the earliest turns should show up only in the archive, not in
    # the shrunk working set — proving they were archived rather than deleted.
    assert any(mk in dropped_blob for mk in markers[:3])
    assert len(dropped_blob) > 0
    assert kept_blob


def test_mutation_stub_retains_path_and_outcome() -> None:
    msg = _tool("delete_file", "deleted /ws/tmp/old_report.md")
    stub = _tool_result_stub(msg)
    assert stub.startswith("[cleared tool result: delete_file")
    assert "deleted /ws/tmp/old_report.md" in stub
    assert is_successful_mutation(msg) is True
    assert is_successful_mutation(_tool("delete_file", "ERROR: not found")) is False


def test_park_overflow_keeps_current_turn_mutation_visible() -> None:
    """Early delete_file in a long same-turn chain must not vanish when parking."""
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(5):
        msgs.append({"role": "user", "content": f"old {i} " + ("要求 " * 50)})
        msgs.append(_tool("read_file", ("OLDDUMP" + str(i)) * 400))
    turn_user = {"role": "user", "content": "删除 junk.txt 然后继续大文件处理"}
    msgs.append(turn_user)
    msgs.append(_tool("delete_file", "deleted junk.txt"))
    for i in range(6):
        msgs.append(_tool("read_file", f"big{i}.txt\n" + ("Y" * 3000)))
    turn_start = current_turn_start(msgs)
    assert msgs[turn_start] is turn_user
    before = messages_tokens(msgs)
    live, archived = park_overflow(
        msgs,
        context_limit=max(1800, before // 4),
        keep_recent_tokens=900,
        protect_from=turn_start,
    )
    assert any(
        m.get("role") == "tool"
        and m.get("name") == "delete_file"
        and "junk.txt" in str(m.get("content") or "")
        for m in live
    )
    assert messages_tokens(live) < before


def test_park_overflow_leaves_mutation_stub_when_archiving() -> None:
    msgs = [{"role": "system", "content": "sys"}]
    msgs.append({"role": "user", "content": "先删后读"})
    msgs.append(_tool("delete_file", "deleted early_gone.md"))
    for i in range(10):
        msgs.append({"role": "user", "content": f"later {i} " + ("任务 " * 40)})
        msgs.append(_tool("read_file", ("Z" * 3500)))
    live, archived = park_overflow(msgs, context_limit=2200, keep_recent_tokens=1000)
    assert any(
        m.get("role") == "tool"
        and m.get("name") == "delete_file"
        and "early_gone.md" in str(m.get("content") or "")
        for m in live
    )


def test_compaction_message_is_internal_and_not_truncation_stop() -> None:
    msg = _build_compaction_message(
        "## Pinned (do not drop)\n### User requests\n- 修好登录页\n",
        "## Goal\nkeep going\n## Next\nedit login.html",
    )
    assert msg["role"] == "user"
    assert msg.get("sidekick_internal") is True
    assert (msg.get("sidekick") or {}).get("kind") == "compaction"
    body = msg["content"]
    assert body.startswith(COMPACTION_MARK)
    assert "NOT a truncated assistant reply" in body
    assert "do not apologize about truncation" in body.lower() or "do not apologize about truncation" in body
    assert "修好登录页" in body
    # Must not instruct the model that the prior reply was cut off.
    assert "之前的回复被截断" not in body
    assert "reply was truncated" not in body.lower()


def test_obligation_from_compaction_reads_user_requests() -> None:
    msg = _build_compaction_message(
        "## Pinned (do not drop)\n### User requests\n- 第一轮：部署\n- 继续修浏览器报错\n",
        "## Goal\nfix\n## Next\ncall browser_diagnostics",
    )
    got = obligation_from_compaction(msg["content"])
    assert "继续修浏览器报错" in got


def test_repair_incomplete_tool_chains_stubs_missing_results() -> None:
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "查控制台"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_diag",
                    "type": "function",
                    "function": {"name": "browser_diagnostics", "arguments": "{}"},
                }
            ],
        },
        # result missing — would 400 the next provider call
    ]
    out = repair_incomplete_tool_chains(msgs)
    assert out[-1]["role"] == "tool"
    assert out[-1]["tool_call_id"] == "call_diag"
    assert out[-1]["name"] == "browser_diagnostics"
    assert "compaction" in out[-1]["content"].lower() or "cleared tool result" in out[-1]["content"]


def test_repair_drops_orphan_tool_rows() -> None:
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "orphan", "name": "browser_diagnostics", "content": "{}"},
        {"role": "assistant", "content": "ok"},
    ]
    out = repair_incomplete_tool_chains(msgs)
    assert all(m.get("role") != "tool" or m.get("tool_call_id") != "orphan" for m in out)
    assert out[-1]["content"] == "ok"


def test_ensure_fit_mid_turn_keeps_well_formed_tool_chain() -> None:
    """Compress mid-turn must not leave dangling tool_calls in the live window."""
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(20):
        msgs.append({"role": "user", "content": f"old {i} " + ("任务约束 " * 40)})
        msgs.append({"role": "assistant", "content": "acked " + ("细节 " * 40)})
    msgs.append({"role": "user", "content": "用 browser_diagnostics 查错并继续修"})
    msgs.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "browser_diagnostics", "arguments": "{}"},
                }
            ],
        }
    )
    msgs.append(
        {
            "role": "tool",
            "tool_call_id": "c1",
            "name": "browser_diagnostics",
            "content": '{"console_errors":[' + ('{"text":"' + ("ERR " * 80) + '"},') * 30 + "]}",
        }
    )
    out, meta = ensure_fit(
        msgs,
        context_limit=5000,
        keep_recent_tokens=1200,
        trigger_ratio=0.55,
        max_attempts=1,
        llm=None,
    )
    assert meta.get("compressed")
    fixed = repair_incomplete_tool_chains(out)
    # Every assistant tool_calls must be followed by matching tool results.
    for i, m in enumerate(fixed):
        calls = m.get("tool_calls") if m.get("role") == "assistant" else None
        if not calls:
            continue
        ids = [str(tc.get("id") or "") for tc in calls if isinstance(tc, dict)]
        have = set()
        j = i + 1
        while j < len(fixed) and fixed[j].get("role") == "tool":
            have.add(str(fixed[j].get("tool_call_id") or ""))
            j += 1
        assert set(ids) <= have
    blob = "\n".join(str(m.get("content") or "") for m in fixed)
    assert "之前的回复被截断" not in blob

