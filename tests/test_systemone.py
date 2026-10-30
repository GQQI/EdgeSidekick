import json

from metateam.services.systemone import SPEAK_CRITERIA, SPEAK_INSTRUCTIONS, build_judge_state, judge_speak, parse_noul


def test_parse_noul_reads_yes_probability():
    payload = {"answers": {"speak": {"type": "noul", "noul": 0.93}}}
    assert parse_noul(payload) == 0.93
    assert parse_noul({"answers": {}}) is None
    assert parse_noul({"answers": {"speak": {"noul": 2}}}) is None


def test_judge_is_off_until_a_server_is_configured(monkeypatch):
    monkeypatch.delenv("SIDEKICK_SYSTEMONE_URL", raising=False)
    monkeypatch.setattr(
        "metateam.services.systemone._stored_systemone",
        lambda: {"url": "", "model": "", "key": "", "threshold": ""},
    )
    result = judge_speak(name="阿查", duty="", user_text="你好", prior=[], addressed=False)
    assert result["available"] is False
    assert result["speak"] is None


def test_url_comes_from_settings_when_env_unset(monkeypatch):
    from metateam.services.systemone import systemone_base_url

    monkeypatch.delenv("SIDEKICK_SYSTEMONE_URL", raising=False)
    monkeypatch.setattr(
        "metateam.services.systemone._stored_systemone",
        lambda: {"url": "http://127.0.0.1:8009", "model": "kev-latest", "key": "", "threshold": ""},
    )
    assert systemone_base_url() == "http://127.0.0.1:8009"


def test_judge_state_keeps_history_and_does_not_call_a_greeting_unnamed():
    state = build_judge_state(
        name="阿查",
        duty="检索资料",
        user_text="大家好",
        prior=[],
        addressed=False,
        history=[{"role": "user", "name": "用户", "text": "昨天说的功能还在吗"}],
        greeting=True,
    )
    assert "昨天说的功能还在吗" in state
    assert "用户在跟群里的人打招呼。" in state
    assert "本轮还没有人回答这条消息。" in state
    assert "没有点名" not in state
    assert SPEAK_INSTRUCTIONS == "这个成员现在要不要发言？"
    assert SPEAK_CRITERIA["true"].startswith("要发言")
    assert "已经有人说过话" in SPEAK_CRITERIA["false"]
    assert "打招呼" in SPEAK_CRITERIA["true"]


def test_capability_question_is_a_fact_until_someone_has_spoken():
    waiting = build_judge_state(
        name="阿查",
        duty="检索资料",
        user_text="你有哪些功能",
        prior=[],
        addressed=False,
        capability=True,
    )
    assert "用户在问功能、身份或能力。" in waiting
    assert "本轮还没有人回答这条消息。" in waiting
    answered = build_judge_state(
        name="码川",
        duty="写代码",
        user_text="你有哪些功能",
        prior=[{"name": "阿查", "text": "我能查资料。"}],
        addressed=False,
        capability=True,
    )
    assert "本轮已经有人说过话。" in answered
    assert "我能查资料。" in answered


def test_judge_state_keeps_a_window_and_summarizes_older_lines():
    prior = [{"name": f"成员{i}", "text": f"第{i}条"} for i in range(10)]
    state = build_judge_state(
        name="码川",
        duty="写代码",
        user_text="继续",
        prior=prior,
        addressed=False,
    )
    assert "滑动摘要" in state
    assert "滑动窗口" in state
    assert "成员0：第0条" in state
    assert "成员9：第9条" in state
    assert "成员5：第5条" in state


def test_judge_state_puts_file_edits_ahead_of_the_chat_window():
    state = build_judge_state(
        name="墨白",
        duty="写清楚",
        user_text="刚才谁改了文件",
        prior=[],
        addressed=True,
        site="墨白 编辑了 main.py：把「timeout = 5」改成「timeout = 30」",
    )
    assert "工作现场" in state
    assert "main.py" in state
    assert state.index("工作现场") < state.index("滑动窗口")


def test_recent_work_lines_reads_who_edited_which_file(tmp_path):
    from metateam.services.worklog import recent_work_lines

    payload = {
        "meta": {"title": "New chat", "workspace": r"E:\Project\AgentFrame"},
        "messages": [
            {"role": "user", "content": "你是「墨白」（写手）。"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "str_replace",
                            "arguments": json.dumps(
                                {
                                    "path": r"E:\Project\AgentFrame\main.py",
                                    "old_string": "timeout = 5",
                                    "new_string": "timeout = 30",
                                },
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "name": "str_replace", "content": "ok"},
        ],
    }
    (tmp_path / "sess_a.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    lines = recent_work_lines([tmp_path / "sess_a.json"], r"E:\Project\AgentFrame")
    assert any("墨白" in line and "main.py" in line and "timeout = 30" in line for line in lines)

