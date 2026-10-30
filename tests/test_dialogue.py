from __future__ import annotations

from metateam.runtime.dialogue import (
    clip_turn_text,
    last_opponent_excerpt,
    neutralize_party_name,
    party_turn_prompt,
)


def test_neutralize_party_name_replaces_color_sides() -> None:
    assert neutralize_party_name("红方", 0) == "智能体1"
    assert neutralize_party_name("蓝方", 1) == "智能体2"
    assert neutralize_party_name("Red Team", 0) == "智能体1"
    assert neutralize_party_name("北约", 0) == "北约"


def test_clip_turn_text() -> None:
    assert clip_turn_text("短") == "短"
    long = "字" * 1300
    clipped = clip_turn_text(long, max_chars=1200)
    assert len(clipped) == 1200
    assert clipped.endswith("…")


def test_last_opponent_excerpt_skips_self() -> None:
    turns = [
        {"name": "智能体1", "text": "先手发言"},
        {"name": "智能体2", "text": "回应很长" * 400},
    ]
    excerpt = last_opponent_excerpt(turns, "智能体1")
    assert excerpt.startswith("智能体2:")
    assert len(excerpt) < 1300


def test_party_turn_prompt_stays_short() -> None:
    p = party_turn_prompt(round_no=3, rounds=4, opponent="智能体2:\n你好")
    assert "你好" in p
    assert "full history" in p or "prior turns" in p
    assert len(p) < 400


def test_subagent_gets_own_smaller_context() -> None:
    from metateam.core.config import Settings
    from metateam.runtime.agent import Agent

    parent = Settings()
    parent.demo_mode = True
    parent.api_key = ""
    parent.context_limit = 48000
    parent.keep_recent_tokens = 12000
    child = Agent(parent, is_subagent=True, goal="探路", talk_only=True)
    assert child.settings.context_limit <= 24000
    assert child.settings.context_limit < parent.context_limit
    assert int(getattr(child.settings, "max_tokens", 0) or 0) == parent.subagent_max_tokens
    assert parent.context_limit == 48000


def test_leaf_subagent_cannot_delegate_more_agents() -> None:
    from metateam.core.config import Settings
    from metateam.runtime.agent import Agent

    child = Agent(Settings(), is_subagent=True, goal="检索论文")
    assert child.registry.get("delegate_task") is None
    assert child.registry.get("delegate_dialogue") is None
