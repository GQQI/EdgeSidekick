from __future__ import annotations

from metateam.runtime.dialogue import (
    normalize_speakers,
    run_sequential_dialogue,
)


def test_normalize_speakers_accepts_name_and_brief() -> None:
    got = normalize_speakers(
        [
            {"name": "进攻方", "brief": "夺控港口"},
            {"name": "防守方", "stance": "守住港口"},
            {"name": "观察员"},
        ]
    )
    assert [s["name"] for s in got] == ["进攻方", "防守方", "观察员"]
    assert got[0]["brief"] == "夺控港口"
    assert got[1]["brief"] == "守住港口"


def test_normalize_speakers_neutralizes_color_sides() -> None:
    got = normalize_speakers([{"name": "红方"}, {"name": "蓝方"}])
    assert [s["name"] for s in got] == ["智能体1", "智能体2"]


def test_normalize_speakers_allows_eight_parties() -> None:
    raw = [{"name": f"方{i}"} for i in range(1, 9)]
    got = normalize_speakers(raw)
    assert len(got) == 8


def test_run_sequential_dialogue_passes_opponent_excerpt_not_full_history() -> None:
    seen: list[str] = []
    kinds: list[str] = []
    keys: list[str] = []

    def fake_child(
        *,
        goal: str,
        context: str,
        role: str,
        kind: str,
        persist_key: str = "",
    ) -> str:
        assert kind == "party"
        assert role == "orchestrator"
        seen.append(context)
        kinds.append(kind)
        keys.append(persist_key)
        if persist_key == "智能体1":
            return "推进到港口外围。"
        return "加强岸防。"

    result = run_sequential_dialogue(
        run_child=fake_child,
        topic="港口争夺",
        speakers=normalize_speakers([{"name": "红方"}, {"name": "蓝方"}]),
        rounds=2,
        mode="推演",
    )
    assert result["speakers"] == ["智能体1", "智能体2"]
    assert len(result["turns"]) == 4
    assert keys == ["智能体1", "智能体2", "智能体1", "智能体2"]
    assert "act first" in seen[0]
    assert "推进到港口外围" in seen[1]
    assert "加强岸防" in seen[2]
    assert "智能体1" in result["transcript"]
    assert "智能体2" in result["transcript"]
    assert "红方" not in result["transcript"]
    assert "蓝方" not in result["transcript"]
    # Later turns must not grow a full transcript dump into the next prompt.
    assert all("Round 1" not in ctx or "Round 2" not in ctx for ctx in seen)
    assert max(len(ctx) for ctx in seen) < 2000


def test_live_subagent_snapshot_includes_party_and_nested() -> None:
    from metateam.runtime.agent import Agent

    parent = Agent.__new__(Agent)
    parent.agent_id = "main"
    parent._children = []
    parent._party_agents = {}

    party = Agent.__new__(Agent)
    party.agent_id = "party_red"
    party.parent_id = "main"
    party.full_agent = True
    party.talk_only = False
    party.goal = "You are 智能体1 in a live multi-agent session."
    party.role = "orchestrator"
    party.messages = [{"role": "assistant", "content": "推进。"}]
    party._children = []
    party._party_agents = {}

    nested = Agent.__new__(Agent)
    nested.agent_id = "leaf_1"
    nested.parent_id = "party_red"
    nested.full_agent = False
    nested.talk_only = False
    nested.goal = "侦察港口"
    nested.role = "leaf"
    nested.messages = []
    nested._children = []
    nested._party_agents = {}
    party._children = [nested]

    parent._children = [party]
    parent._party_agents = {"智能体1": party}

    snap = Agent.live_subagent_snapshot(parent)
    ids = [item["child_id"] for item in snap]
    assert ids == ["party_red", "leaf_1"]
    assert snap[0]["party"] == "智能体1"
    assert snap[0]["label"] == "智能体1"
    assert snap[0]["transcript"][0]["text"] == "推进。"
    assert snap[1]["goal"] == "侦察港口"
    assert snap[0]["parent_id"] == "main"
    assert snap[1]["parent_id"] == "party_red"


def test_ui_messages_marks_unfinished_tool_running() -> None:
    from types import SimpleNamespace

    from metateam.services.store import STORE

    sess = SimpleNamespace(
        agent=SimpleNamespace(
            messages=[
                {"role": "user", "content": "start agents"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "function": {
                                "name": "delegate_dialogue",
                                "arguments": '{"topic":"港口","speakers":[{"name":"进攻方"},{"name":"防守方"}]}',
                            },
                        }
                    ],
                },
            ]
        )
    )
    out = STORE.ui_messages(sess)  # type: ignore[arg-type]
    tools = [m for m in out if m.get("role") == "tool"]
    assert len(tools) == 1
    assert tools[0]["name"] == "delegate_dialogue"
    assert tools[0]["status"] == "running"


def test_ui_messages_keeps_reasoning_without_content() -> None:
    from types import SimpleNamespace

    from metateam.services.store import STORE

    sess = SimpleNamespace(
        agent=SimpleNamespace(
            messages=[
                {"role": "user", "content": "改登录页"},
                {
                    "role": "assistant",
                    "content": "",
                    "reasoning": "先读现有组件再改样式。",
                    "tool_calls": [
                        {
                            "id": "c-read",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path":"src/Login.tsx"}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "c-read",
                    "name": "read_file",
                    "content": "export function Login() {}",
                },
                {
                    "role": "assistant",
                    "content": "已经改好登录页。",
                    "reasoning": "样式对齐设计稿。",
                },
            ]
        )
    )
    out = STORE.ui_messages(sess)  # type: ignore[arg-type]
    assistants = [m for m in out if m.get("role") == "assistant"]
    assert len(assistants) == 2
    assert assistants[0]["content"] == ""
    assert assistants[0]["reasoning"] == "先读现有组件再改样式。"
    assert assistants[1]["reasoning"] == "样式对齐设计稿。"
    tools = [m for m in out if m.get("role") == "tool"]
    assert len(tools) == 1
    assert tools[0]["status"] == "done"
