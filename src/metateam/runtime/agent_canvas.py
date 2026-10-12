"Canvas tree, child agents, and ask-user gate helpers."

from __future__ import annotations

import copy
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Any, Callable, Optional

from .ask import (
    AskGate,
    MIN_ASK_OPTIONS,
    build_ask_options,
    normalize_option_labels,
    should_keep_as_markdown,
    try_parse_inline_ask,
)
from ..core.config import Settings, get_settings
from .context import (
    COMPACTION_MARK,
    cheap_compact,
    context_budget_tokens,
    current_turn_start,
    debug_dump_budget,
    ensure_fit,
    messages_tokens,
    park_overflow,
    schemas_tokens,
    squeeze_fresh_tool_results,
)
from ..core.events import EventBus, emit, new_id
from ..core.guardrails import Guardrails, repeating_tool_sequence, tool_call_signature
from .llm import LLM, parse_tool_args, streaming_text_looped
from .plan import (
    PLAN_PREP_HINT,
    PLAN_PREP_MAX_ROUNDS,
    PLAN_PREP_RETRY_HINT,
    PlanGate,
    apply_confirmed_plan,
    digest_plan_prep,
    extract_plan_goal,
    format_plan_markdown,
    generate_plan,
    goal_ready_to_plan,
    is_multi_agent_request,
    needs_plan,
    snapshot_plan_tasks,
)
from .prompts import (
    apply_response_language,
    build_system_prompt,
    language_directive,
    language_pin_messages,
)
from .coherence import inject_contract_into_goal, shape_contract_from_plan
from .review import run_review
from ..services.skills import Skill, invalidate_skills_cache, load_skills
from .tool_registry import drop_delegate_schemas, filter_readonly_schemas, skill_tool_name
from .tools import ToolRegistry, build_registry
from .tools.support import _skill_as_tool
from .agent_execute import drop_unready_tool_calls, file_tool_results_blocked



def _subagent_snapshot_item(
    child: "Agent",
    party: str = "",
    *,
    turn: int = 0,
) -> dict[str, Any]:
    kind = "party" if child.full_agent else ("talk" if child.talk_only else "task")
    label = party or (child.goal or "").strip().split("\n")[0][:80]
    transcript: list[dict[str, Any]] = []
    for m in child.messages:
        role = m.get("role")
        if role == "assistant":
            text = str(m.get("content") or "").strip()
            reasoning = str(m.get("reasoning") or "").strip()
            try:
                from .text_tool_calls import extract_text_tool_calls

                text, xml_calls = extract_text_tool_calls(text)
            except Exception:
                xml_calls = []
            if text or reasoning:
                item: dict[str, Any] = {"kind": "assistant", "text": text}
                if reasoning:
                    item["reasoning"] = reasoning
                transcript.append(item)
            if not m.get("tool_calls"):
                for tc in xml_calls:
                    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                    name = str((fn or {}).get("name") or "").strip()
                    if name:
                        transcript.append({"kind": "tool", "name": name, "result": ""})
        elif role == "tool":
            transcript.append(
                {
                    "kind": "tool",
                    "name": str(m.get("name") or "tool"),
                    "result": str(m.get("content") or "")[:2000],
                }
            )
    return {
        "child_id": child.agent_id,
        "goal": child.goal or label,
        "role": child.role,
        "kind": kind,
        "party": party,
        "label": label,
        "parent_id": child.parent_id,
        "turn": turn,
        "replay": True,
        "transcript": transcript[-30:],
        "status": "running",
        "activity": "运行中…",
        "message": f"resume {child.role}: {label}",
    }


class AgentCanvasMixin:
    def _wait_child_run(self, child: "Agent", user_text: str) -> AgentResult:
        timeout = float(getattr(self.settings, "subagent_timeout", 0) or 0)
        if timeout <= 0:
            return child.run(user_text, do_review=False)
        pool = ThreadPoolExecutor(max_workers=1)
        fut = pool.submit(lambda: child.run(user_text, do_review=False))
        try:
            return fut.result(timeout=timeout)
        except FuturesTimeout:
            child.stop_generation()
            try:
                result = fut.result(timeout=12)
                text = (result.text or "").strip() or (
                    f"ERROR: subagent timed out after {int(timeout)}s "
                    "(stuck thinking). Parent should continue with other results."
                )
                return AgentResult(
                    text=text,
                    messages=result.messages,
                    iterations=result.iterations,
                    cancelled=False,
                    agent_id=child.agent_id,
                )
            except FuturesTimeout:
                child._abandoned = True
                return AgentResult(
                    text=(
                        f"ERROR: subagent timed out after {int(timeout)}s "
                        "(stuck thinking). Parent should continue with other results."
                    ),
                    cancelled=False,
                    agent_id=child.agent_id,
                )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def _run_child(
        self,
        *,
        goal: str,
        context: str = "",
        role: str = "leaf",
        kind: str = "task",
        persist_key: str = "",
        child_id: str = "",
    ) -> str:
        child_role = role if role in ("leaf", "orchestrator") else "leaf"
        next_depth = self.depth + 1
        if child_role == "orchestrator" and next_depth >= self.settings.max_spawn_depth:
            child_role = "leaf"
        kind_s = (kind or "task").strip().lower()
        talk_only = kind_s == "talk"
        full_agent = kind_s in ("party", "full")
        key = (persist_key or "").strip()
        resumed = bool(key and key in self._party_agents)
        preset_id = (child_id or "").strip()

        if resumed:
            child = self._party_agents[key]
            child_id = child.agent_id
        else:
            child_id = preset_id or new_id("agent")

        event_kind = "party" if full_agent else ("talk" if talk_only else "task")
        label = key or (goal or context or "").strip().split("\n")[0][:80]
        self._emit(
            "subagent_start",
            {
                "child_id": child_id,
                "goal": goal,
                "role": child_role,
                "depth": next_depth,
                "kind": event_kind,
                "party": key,
                "label": label,
                "resumed": resumed,
                "message": f"{'resume' if resumed else 'spawn'} {child_role}: {label}",
            },
        )
        if not resumed:
            # Minimal grounding for task workers: they skip the workspace
            # grounding refresh, so hand them the root path + top-level list
            # or their first read/search guesses at imaginary layouts.
            if not full_agent and not talk_only:
                from .agent_grounding import minimal_grounding

                block = minimal_grounding(self.settings.workspace)
                context = (block + "\n\n" + (context or "").strip()).strip()
            from .agent import Agent
            child = Agent(
                self.settings,
                is_subagent=True,
                role=child_role,
                goal=goal,
                context="" if (full_agent or key) else context,
                depth=next_depth,
                parent_id=self.agent_id,
                agent_id=child_id,
                bus=self.bus,
                on_event=self.on_event,
                approval=self.approval,
                ask=self.ask,
                plan_gate=self.plan_gate,
                talk_only=talk_only,
                full_agent=full_agent,
            )
            with self._child_lock:
                self._children.append(child)
                if key:
                    self._party_agents[key] = child
        with self._child_lock:
            self._remember_canvas_child(child, party=key)
        self._emit_canvas_sync()
        if self.cancelled():
            child.request_cancel()
        child._inherited_language = str(getattr(self, "_response_language_src", "") or "")
        if resumed or full_agent:
            user_text = (context or "").strip() or (goal or "").strip() or "Your turn."
        elif talk_only:
            user_text = (
                "Speak your turn now. Do not write code or files. "
                "Output only in-character dialogue."
            )
        else:
            user_text = "Begin now. Use tools, then summarize."
        result_text = "(empty summary)"
        iterations = 0
        cancelled = False
        try:
            result = self._wait_child_run(child, user_text)
            result_text = result.text or "(empty summary)"
            iterations = result.iterations
            cancelled = bool(result.cancelled)
        except Exception as exc:  # noqa: BLE001
            from ..core.logutil import get_logger, log_exception

            log_exception(get_logger("metateam.agent"), f"child {child_id} failed", exc)
            result_text = f"ERROR: child failed: {exc}"
            cancelled = True
        finally:
            with self._child_lock:
                self._ingest_canvas_subtree(
                    child,
                    party=key,
                    status="error" if cancelled or str(result_text).startswith("ERROR") else "done",
                )
                if not key:
                    self._children = [c for c in self._children if c is not child]
            self._emit_canvas_sync()
            self._emit(
                "subagent_end",
                {
                    "child_id": child_id,
                    "goal": goal,
                    "summary": (result_text or "")[:12000],
                    "iterations": iterations,
                    "cancelled": cancelled,
                    "message": f"done: {(goal or context)[:60]}",
                },
            )
        return result_text

    def _end_party_session(self) -> None:
        for name, child in list(self._party_agents.items()):
            self._ingest_canvas_subtree(child, party=name)
        for child in list(self._party_agents.values()):
            self._children = [c for c in self._children if c is not child]
        self._party_agents.clear()
        self._emit_canvas_sync()

    def _note_canvas_tasks(self, items: list[Any] | None) -> None:
        """Reserve a canvas slot for every queued worker, including those waiting on the pool."""
        turn = int(getattr(self, "_canvas_turn", 0) or 0)
        rows = items if isinstance(items, list) else []
        with self._child_lock:
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                cid = str(raw.get("child_id") or "").strip()
                goal = str(raw.get("goal") or "").strip()
                if not cid or not goal:
                    continue
                prev = self._canvas_index.get(cid)
                if isinstance(prev, dict) and str(prev.get("status") or "") in ("done", "error"):
                    continue
                label = goal.split("\n")[0][:80]
                self._canvas_index[cid] = {
                    "child_id": cid,
                    "parent_id": self.agent_id,
                    "goal": goal,
                    "role": str(raw.get("role") or "leaf"),
                    "kind": "task",
                    "party": "",
                    "label": label,
                    "turn": turn,
                    "replay": True,
                    "transcript": list(prev.get("transcript") or []) if isinstance(prev, dict) else [],
                    "status": "running",
                    "activity": "排队中…",
                    "message": f"queue leaf: {label}",
                    "children": [],
                }
        self._emit_canvas_sync()

    def _remember_canvas_child(self, child: Agent, party: str = "", *, status: str = "running") -> None:
        item = _subagent_snapshot_item(
            child,
            party,
            turn=int(getattr(self, "_canvas_turn", 0) or 0),
        )
        item["status"] = "error" if status == "error" else ("running" if status == "running" else "done")
        item["activity"] = "运行中…" if item["status"] == "running" else ""
        self._canvas_index[child.agent_id] = item

    def _ingest_canvas_subtree(self, child: Agent, party: str = "", *, status: str = "done") -> None:
        finished = "error" if str(status).startswith("error") else "done"
        self._remember_canvas_child(child, party, status=finished)
        kids = getattr(child, "_children", None)
        if isinstance(kids, list):
            for grand in list(kids):
                self._ingest_canvas_subtree(grand, status=finished)
        parties = getattr(child, "_party_agents", None)
        if isinstance(parties, dict):
            for name, grand in list(parties.items()):
                self._ingest_canvas_subtree(grand, party=str(name), status=finished)


    def _note_ask_edge(self, edge: dict[str, Any] | None) -> None:
        """Record an AskPacket edge for canvas (who asked whom + visibility)."""
        if not isinstance(edge, dict):
            return
        row = {
            "id": str(edge.get("id") or "").strip() or f"ask_{len(self._ask_edges)+1}",
            "from_id": str(edge.get("from_id") or self.agent_id or "").strip(),
            "to_id": str(edge.get("to_id") or "").strip(),
            "kind": str(edge.get("kind") or "consult").strip() or "consult",
            "what": str(edge.get("what") or "").strip(),
            "how": str(edge.get("how") or "").strip(),
            "who": str(edge.get("who") or "").strip(),
            "when": str(edge.get("when") or "now").strip() or "now",
            "why": str(edge.get("why") or "").strip(),
            "need": str(edge.get("need") or edge.get("question") or "").strip(),
            "goal_slice": str(edge.get("goal_slice") or "").strip(),
            "visibility": edge.get("visibility") if isinstance(edge.get("visibility"), dict) else {},
            "status": str(edge.get("status") or "sent").strip() or "sent",
            "summary": str(edge.get("summary") or "").strip()[:500],
        }
        if not row["to_id"] and not row["need"]:
            return
        with self._child_lock:
            replaced = False
            if not row["id"]:
                # match latest same to_id+need
                for i in range(len(self._ask_edges) - 1, -1, -1):
                    prev = self._ask_edges[i]
                    if str(prev.get("to_id")) == row["to_id"] and str(prev.get("need")) == row["need"]:
                        row["id"] = str(prev.get("id") or row["id"])
                        self._ask_edges[i] = {**prev, **{k: v for k, v in row.items() if v}}
                        replaced = True
                        break
            if not replaced:
                for i, prev in enumerate(self._ask_edges):
                    if row["id"] and str(prev.get("id")) == row["id"]:
                        self._ask_edges[i] = {**prev, **row}
                        replaced = True
                        break
            if not replaced:
                if not row["id"]:
                    row["id"] = f"ask_{len(self._ask_edges)+1}"
                self._ask_edges.append(row)
            if len(self._ask_edges) > 80:
                self._ask_edges = self._ask_edges[-80:]
        try:
            self._emit(
                "ask_edge",
                {"edge": row, "asks": list(self._ask_edges)},
            )
        except Exception:
            pass
        self._emit_canvas_sync()

    def _emit_canvas_sync(self) -> None:
        """Push the full spawn forest so the live canvas matches persisted history."""
        emit = getattr(self, "_emit", None)
        if not callable(emit):
            return
        # Nested leaves update the parent index via ingest; only the lead
        # (or a full party) owns the forest the UI should paint.
        if bool(getattr(self, "is_subagent", False)) and not bool(
            getattr(self, "full_agent", False)
        ):
            return
        try:
            emit(
                "canvas_sync",
                {
                    "tree": self.canvas_tree(),
                    "turn": int(getattr(self, "_canvas_turn", 0) or 0),
                    "asks": list(getattr(self, "_ask_edges", []) or []),
                },
            )
        except Exception:
            return

    def canvas_tree(self) -> list[dict[str, Any]]:
        """Durable forest of spawned agents (including finished nested helpers)."""
        by_id: dict[str, dict[str, Any]] = {}
        for item in self._canvas_index.values():
            if not isinstance(item, dict):
                continue
            cid = str(item.get("child_id") or "").strip()
            if not cid:
                continue
            by_id[cid] = {**item, "children": []}
        for item in self.live_subagent_snapshot():
            cid = str(item.get("child_id") or "").strip()
            if cid:
                prev = by_id.get(cid) or {}
                kids = prev.get("children") or []
                by_id[cid] = {**prev, **item, "children": kids}
        roots: list[dict[str, Any]] = []
        for item in by_id.values():
            pid = str(item.get("parent_id") or "").strip()
            parent = by_id.get(pid)
            if parent is not None:
                parent.setdefault("children", []).append(item)
            else:
                roots.append(item)
        return roots

    def active_plan_snapshot(self) -> Optional[dict[str, Any]]:
        """The plan being executed right now, or None. Cleared on plan_done/cancel."""
        plan = self._active_plan
        if not plan:
            return None
        return {
            "plan_id": str(plan.get("plan_id") or ""),
            "summary": str(plan.get("summary") or ""),
            "shape_contract": plan.get("shape_contract") or {},
            "tasks": snapshot_plan_tasks(list(plan.get("tasks") or [])),
        }

    def live_subagent_snapshot(self) -> list[dict[str, Any]]:
        """Running children (including nested) so a reattached UI can restore cards."""
        out: list[dict[str, Any]] = []
        seen: set[str] = set()

        def party_name(agent: Agent, child: Agent) -> str:
            for name, party in agent._party_agents.items():
                if party is child or party.agent_id == child.agent_id:
                    return str(name)
            return ""

        def walk(agent: Agent) -> None:
            ordered: list[tuple[str, Agent]] = []
            local: set[str] = set()
            for child in list(agent._children):
                ordered.append((party_name(agent, child), child))
                local.add(child.agent_id)
            for name, child in list(agent._party_agents.items()):
                if child.agent_id in local:
                    continue
                ordered.append((str(name), child))
            for party, child in ordered:
                if child.agent_id in seen:
                    continue
                seen.add(child.agent_id)
                out.append(
                    _subagent_snapshot_item(
                        child,
                        party,
                        turn=int(getattr(self, "_canvas_turn", 0) or 0),
                    )
                )
                walk(child)

        walk(self)
        return out

    def _ask_user(
        self,
        *,
        question: str,
        options: list[str] | None = None,
        allow_custom: bool = True,
        custom_label: str = "其他（请补充）",
        # Legacy flat args (older prompts / cached tool calls)
        option_a: str = "",
        option_b: str = "",
        option_c: str = "",
        option_d: str = "",
    ) -> str:
        q = (question or "").strip()
        if not q:
            return "ERROR: empty question"

        labels = normalize_option_labels(options)
        if len(labels) < MIN_ASK_OPTIONS:
            legacy = [
                str(option_a or "").strip(),
                str(option_b or "").strip(),
                str(option_c or "").strip(),
            ]
            labels = [x for x in legacy if x]
            if str(option_d or "").strip():
                labels.append(str(option_d).strip())

        built = build_ask_options(labels)
        if len(built) < MIN_ASK_OPTIONS:
            return (
                f"ERROR: ask_user needs at least {MIN_ASK_OPTIONS} options; "
                f"got {len(built)}"
            )
        if should_keep_as_markdown(q, labels):
            return (
                "ERROR: these options look like a summary/要点, not mutually exclusive "
                "choices. If the user must pick one next action, call ask_user again "
                "with a direct question and 2–12 option labels that each name one "
                "choice (put file lists and reasons in why/constraints, not in every "
                "label). If you are only reporting findings, write them as markdown "
                "and do not call ask_user."
            )

        allow_other = bool(allow_custom)
        other_label = str(custom_label or "其他（请补充）").strip() or "其他（请补充）"
        with self.ask.serialize_ui():
            if self.cancelled():
                return "ERROR: cancelled — proceed with a reasonable default or stop."
            ask_id = new_id("ask")
            call_id = new_id("call")
            self._emit("assistant_delta", {"chunk": "", "reset": True, "discard": True})
            self._emit(
                "ask_request",
                {
                    "ask_id": ask_id,
                    "call_id": call_id,
                    "session_id": self.session_id or "",
                    "question": q,
                    "options": built,
                    "allow_custom": allow_other,
                    "custom_label": other_label,
                    "summary": f"询问用户: {q[:120]}",
                    "message": f"等待用户选择：{q[:80]}",
                },
            )
            answer = self.ask.request(
                ask_id,
                q,
                built,
                allow_custom=allow_other,
                custom_label=other_label,
            )
            self._emit(
                "ask_resolved",
                {
                    "ask_id": ask_id,
                    "call_id": call_id,
                    "answer": answer,
                    "message": "用户已回答" if not str(answer).startswith("ERROR:") else "询问已取消或超时",
                },
            )
        return answer

