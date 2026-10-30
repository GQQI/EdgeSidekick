from __future__ import annotations

from types import SimpleNamespace

from metateam.api.sse import event_payload, gate_replay_events
from metateam.core.events import PROTOCOL_VERSION, Event, EventBus, KNOWN_EVENT_TYPES


def test_event_payload_versioned() -> None:
    payload = event_payload("session", {"session_id": "s1"}, agent_id="a1")
    assert payload["type"] == "session"
    assert payload["protocol"] == PROTOCOL_VERSION
    assert payload["type"] in KNOWN_EVENT_TYPES


def test_event_to_dict_versioned() -> None:
    d = Event(type="final", data={"ok": True}).to_dict()
    assert d["protocol"] == PROTOCOL_VERSION
    assert d["type"] == "final"


def test_bus_logs_and_continues() -> None:
    bus = EventBus()
    seen: list[str] = []

    def boom(_ev: Event) -> None:
        raise RuntimeError("subscriber exploded")

    def ok(ev: Event) -> None:
        seen.append(ev.type)

    bus.subscribe(boom)
    bus.subscribe(ok)
    bus.emit(Event(type="tool_end", data={}))
    assert seen == ["tool_end"]


def test_gate_replay_includes_session(monkeypatch) -> None:
    class FakeAgent:
        agent_id = "agent_x"
        settings = SimpleNamespace(demo_mode=False)

    sess = SimpleNamespace(id="sess1", agent=FakeAgent())

    class FakeStore:
        def runtime_snapshot(self, _sess):
            return {
                "busy": True,
                "pending_approvals": [
                    {
                        "id": "ap1",
                        "tool": "write_file",
                        "args": {"path": "a.py"},
                        "summary": "write a.py",
                    }
                ],
                "pending_asks": [],
                "pending_plans": [],
            }

    monkeypatch.setattr("metateam.api.sse.STORE", FakeStore())
    events = gate_replay_events(sess)  # type: ignore[arg-type]
    types = [e["type"] for e in events]
    assert types == ["session", "approval_request"]
    assert events[0]["protocol"] == PROTOCOL_VERSION
    assert events[1]["data"]["approval_id"] == "ap1"


def test_gate_replay_includes_live_subagents(monkeypatch) -> None:
    class FakeAgent:
        agent_id = "agent_x"
        settings = SimpleNamespace(demo_mode=False)

    sess = SimpleNamespace(id="sess1", agent=FakeAgent())

    class FakeStore:
        def runtime_snapshot(self, _sess):
            return {
                "busy": True,
                "pending_approvals": [],
                "pending_asks": [],
                "pending_plans": [],
                "live_subagents": [
                    {
                        "child_id": "child_1",
                        "goal": "search docs",
                        "role": "leaf",
                        "party": "",
                        "label": "search docs",
                    }
                ],
            }

    monkeypatch.setattr("metateam.api.sse.STORE", FakeStore())
    events = gate_replay_events(sess)  # type: ignore[arg-type]
    types = [e["type"] for e in events]
    assert types == ["session", "subagent_start"]
    assert events[1]["data"]["child_id"] == "child_1"
    assert events[1]["data"]["replay"] is True


def test_gate_replay_nests_subagent_under_parent(monkeypatch) -> None:
    class FakeAgent:
        agent_id = "main"
        settings = SimpleNamespace(demo_mode=False)

    sess = SimpleNamespace(id="sess1", agent=FakeAgent())

    class FakeStore:
        def runtime_snapshot(self, _sess):
            return {
                "busy": True,
                "pending_approvals": [],
                "pending_asks": [],
                "pending_plans": [],
                "live_subagents": [
                    {
                        "child_id": "child_1",
                        "parent_id": "main",
                        "goal": "parent job",
                        "role": "leaf",
                    },
                    {
                        "child_id": "grand_1",
                        "parent_id": "child_1",
                        "goal": "nested job",
                        "role": "leaf",
                    },
                ],
            }

    monkeypatch.setattr("metateam.api.sse.STORE", FakeStore())
    events = gate_replay_events(sess)  # type: ignore[arg-type]
    nested = events[2]
    assert nested["type"] == "subagent_start"
    assert nested["agent_id"] == "child_1"
    assert nested["parent_id"] == "main"
    assert nested["data"]["child_id"] == "grand_1"
