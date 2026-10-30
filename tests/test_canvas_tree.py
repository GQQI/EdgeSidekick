from metateam.runtime.agent import Agent
from metateam.runtime.tools.web import web_search
from metateam.runtime.agent_execute import _alias_tool_name


def test_alias_web_search_names() -> None:
    assert _alias_tool_name("web_search") == "web_search"
    assert _alias_tool_name("google_search") == "web_search"
    assert _alias_tool_name("search_text") == "search_text"
    assert _alias_tool_name("browser_snapshot") == "browser_snapshot"


def test_web_search_rejects_empty() -> None:
    assert web_search("").startswith("ERROR")


def test_canvas_tree_nests_helper_under_party() -> None:
    parent = Agent.__new__(Agent)
    parent.agent_id = "main"
    parent._canvas_index = {}
    parent._children = []
    parent._party_agents = {}

    party = Agent.__new__(Agent)
    party.agent_id = "party_a"
    party.parent_id = "main"
    party.full_agent = True
    party.talk_only = False
    party.goal = "You are 智能体1"
    party.role = "orchestrator"
    party.messages = [{"role": "assistant", "content": "先手"}]
    party._children = []
    party._party_agents = {}

    helper = Agent.__new__(Agent)
    helper.agent_id = "leaf_1"
    helper.parent_id = "party_a"
    helper.full_agent = False
    helper.talk_only = False
    helper.goal = "检索资料"
    helper.role = "leaf"
    helper.messages = []
    helper._children = []
    helper._party_agents = {}
    party._children = [helper]

    parent._ingest_canvas_subtree(party, party="智能体1")
    tree = parent.canvas_tree()
    assert [n["child_id"] for n in tree] == ["party_a"]
    kids = tree[0].get("children") or []
    assert [n["child_id"] for n in kids] == ["leaf_1"]


def test_canvas_tree_preserves_parallel_children_with_the_same_goal() -> None:
    parent = Agent.__new__(Agent)
    parent.agent_id = "main"
    parent._canvas_index = {}
    parent._children = []
    parent._party_agents = {}

    for child_id in ("leaf_1", "leaf_2", "leaf_3"):
        child = Agent.__new__(Agent)
        child.agent_id = child_id
        child.parent_id = "main"
        child.full_agent = False
        child.talk_only = False
        child.goal = "同一个研究任务"
        child.role = "leaf"
        child.messages = []
        child._children = []
        child._party_agents = {}
        parent._ingest_canvas_subtree(child)

    tree = parent.canvas_tree()
    assert [node["child_id"] for node in tree] == ["leaf_1", "leaf_2", "leaf_3"]


def test_canvas_tree_marks_the_user_turn_that_spawned_the_team() -> None:
    parent = Agent.__new__(Agent)
    parent.agent_id = "main"
    parent._canvas_index = {}
    parent._canvas_turn = 4
    parent._children = []
    parent._party_agents = {}

    child = Agent.__new__(Agent)
    child.agent_id = "leaf_1"
    child.parent_id = "main"
    child.full_agent = False
    child.talk_only = False
    child.goal = "检索资料"
    child.role = "leaf"
    child.messages = []
    child._children = []
    child._party_agents = {}

    parent._ingest_canvas_subtree(child)

    assert parent.canvas_tree()[0]["turn"] == 4


def test_canvas_tree_marks_finished_helpers_done() -> None:
    parent = Agent.__new__(Agent)
    parent.agent_id = "main"
    parent._canvas_index = {}
    parent._canvas_turn = 1
    parent._children = []
    parent._party_agents = {}

    child = Agent.__new__(Agent)
    child.agent_id = "leaf_1"
    child.parent_id = "main"
    child.full_agent = False
    child.talk_only = False
    child.goal = "检索资料"
    child.role = "leaf"
    child.messages = [{"role": "assistant", "content": "找到三篇资料"}]
    child._children = []
    child._party_agents = {}

    parent._remember_canvas_child(child)
    assert parent.canvas_tree()[0]["status"] == "running"
    parent._ingest_canvas_subtree(child)
    snap = parent.canvas_tree()[0]
    assert snap["status"] == "done"
    assert snap["activity"] == ""
    assert snap["transcript"][-1]["text"] == "找到三篇资料"


def test_canvas_tree_keeps_all_five_parallel_workers() -> None:
    parent = Agent.__new__(Agent)
    parent.agent_id = "main"
    parent.is_subagent = False
    parent.full_agent = False
    parent._canvas_index = {}
    parent._canvas_turn = 1
    parent._children = []
    parent._party_agents = {}

    for i in range(5):
        child = Agent.__new__(Agent)
        child.agent_id = f"leaf_{i}"
        child.parent_id = "main"
        child.full_agent = False
        child.talk_only = False
        child.goal = f"任务{i + 1}"
        child.role = "leaf"
        child.messages = []
        child._children = []
        child._party_agents = {}
        parent._remember_canvas_child(child)

    tree = parent.canvas_tree()
    assert [node["child_id"] for node in tree] == [f"leaf_{i}" for i in range(5)]


def test_note_canvas_tasks_reserves_five_slots_before_spawn() -> None:
    import threading

    parent = Agent.__new__(Agent)
    parent.agent_id = "main"
    parent.is_subagent = False
    parent.full_agent = False
    parent.parent_id = ""
    parent._canvas_index = {}
    parent._canvas_turn = 2
    parent._children = []
    parent._party_agents = {}
    parent._child_lock = threading.Lock()
    parent._abandoned = False
    parent.bus = None
    parent.on_event = None
    emitted: list[tuple[str, dict]] = []

    def _emit(type_: str, data=None) -> None:
        emitted.append((type_, data or {}))

    parent._emit = _emit  # type: ignore[method-assign]
    parent._note_canvas_tasks(
        [{"child_id": f"queued_{i}", "goal": f"任务{i + 1}", "role": "leaf"} for i in range(5)]
    )
    tree = parent.canvas_tree()
    assert [node["child_id"] for node in tree] == [f"queued_{i}" for i in range(5)]
    assert all(node.get("activity") == "排队中…" for node in tree)
    assert any(kind == "canvas_sync" and len((payload.get("tree") or [])) == 5 for kind, payload in emitted)
