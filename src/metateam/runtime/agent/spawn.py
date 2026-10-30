"""Agent mixin: spawn."""
from __future__ import annotations

from typing import Any, Optional

from . import helpers as _h
from .helpers import *  # noqa: F403

class AgentSpawnMixin:

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
        stop_when: str = "",
        deliverable: Optional[list[str]] = None,
    ) -> str:
        child_role = role if role in ("leaf", "orchestrator") else "leaf"
        next_depth = self.depth + 1
        if child_role == "orchestrator" and next_depth >= self.settings.max_spawn_depth:
            child_role = "leaf"
        kind_s = (kind or "task").strip().lower()
        talk_only = kind_s == "talk"
        full_agent = kind_s in ("party", "full")
        contract: dict[str, Any] = {}
        if stop_when.strip():
            contract["stop_when"] = stop_when.strip()
        slots = [str(x).strip() for x in (deliverable or []) if str(x).strip()]
        if slots:
            contract["deliverable"] = slots
        key = (persist_key or "").strip()
        resumed = bool(key and key in self._party_agents)
        preset_id = (child_id or "").strip()

        if resumed:
            child = self._party_agents[key]
            child_id = child.agent_id
            if contract:
                child.task_contract = dict(contract)
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
            # Resolve via package so tests can monkeypatch metateam.runtime.agent.Agent.
            import metateam.runtime.agent as _agent_pkg

            child = _agent_pkg.Agent(
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
                task_contract=contract or None,
            )
            with self._child_lock:
                self._children.append(child)
                if key:
                    self._party_agents[key] = child
            # Children inherit sticky deny-read constraints from the lead.
            child.user_directives = list(getattr(self, "user_directives", None) or [])
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
            from ...core.logutil import get_logger, log_exception

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

    def clear_canvas_forest(self) -> None:
        """Drop spawn cards / ask edges so a resend cannot revive the last team."""
        with self._child_lock:
            self._canvas_index.clear()
            self._ask_edges.clear()

    def canvas_tree(self) -> list[dict[str, Any]]:
        """Durable forest of spawned agents for the active canvas turn only."""
        turn = int(getattr(self, "_canvas_turn", 0) or 0)
        by_id: dict[str, dict[str, Any]] = {}
        for item in self._canvas_index.values():
            if not isinstance(item, dict):
                continue
            cid = str(item.get("child_id") or "").strip()
            if not cid:
                continue
            item_turn = int(item.get("turn") or 0)
            # Only the current user-turn's team belongs on the live board.
            if turn > 0 and item_turn > 0 and item_turn != turn:
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
