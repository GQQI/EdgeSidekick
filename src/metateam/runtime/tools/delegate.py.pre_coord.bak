"""ask_user and delegate_task."""

from __future__ import annotations

import contextvars
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from ...core.events import new_id
from ..ask import normalize_option_labels
from ..tool_registry import Tool, ToolRegistry
from .context import ToolContext


def register_ask_and_delegate(reg: ToolRegistry, ctx: ToolContext) -> None:
    settings = ctx.settings
    ask_user_fn = ctx.ask_user_fn
    run_child = ctx.run_child
    allow_delegate = ctx.run_child is not None

    if ask_user_fn is not None:

        def ask_user(
            question: str,
            options: list[str],
            allow_custom: bool = True,
            custom_label: str = "其他（请补充）",
        ) -> str:
            return ask_user_fn(
                question=question,
                options=normalize_option_labels(options),
                allow_custom=bool(allow_custom),
                custom_label=custom_label or "其他（请补充）",
            )

        reg.register(
            Tool(
                "ask_user",
                "Ask the user to clarify ONLY when a real decision or missing info blocks progress. "
                "Do NOT use ask_user to present 要点, summaries, plans, status lists, or findings "
                "— those belong in markdown assistant text. Do NOT use it to list past user tasks "
                "or answer meta questions from chat history. "
                "The UI shows clickable buttons for mutually exclusive choices only. "
                "Provide question + options (array of 2–12 short labels). Set allow_custom=true "
                "so the user can type a custom answer. Wait for the result before continuing.",
                {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "Clear question explaining what you need.",
                        },
                        "options": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 2,
                            "maxItems": 12,
                            "description": "2–12 choice labels shown as buttons.",
                        },
                        "allow_custom": {
                            "type": "boolean",
                            "description": "Show a free-text 'other' field (default true).",
                            "default": True,
                        },
                        "custom_label": {
                            "type": "string",
                            "description": "Label for the custom/other choice.",
                            "default": "其他（请补充）",
                        },
                    },
                    "required": ["question", "options"],
                },
                ask_user,
                parallel_safe=False,
            )
        )

    if allow_delegate and run_child is not None:

        def delegate_task(
            goal: str = "",
            context: str = "",
            role: str = "leaf",
            tasks: Optional[list[Any]] = None,
            task: str = "",
            query: str = "",
            prompt: str = "",
            description: str = "",
        ) -> str:
            items: list[dict[str, Any]]
            goal_s = str(goal or task or query or prompt or description or "").strip()
            if isinstance(tasks, str) and tasks.strip():
                tasks = [{"goal": tasks.strip()}]
            if tasks:
                items = []
                for raw in tasks:
                    if isinstance(raw, str) and raw.strip():
                        items.append({"goal": raw.strip(), "context": context, "role": role})
                        continue
                    if not isinstance(raw, dict):
                        continue
                    g = str(
                        raw.get("goal")
                        or raw.get("task")
                        or raw.get("query")
                        or raw.get("prompt")
                        or raw.get("description")
                        or ""
                    ).strip()
                    if not g:
                        continue
                    items.append(
                        {
                            "goal": g,
                            "context": str(raw.get("context") or context or "").strip(),
                            "role": str(raw.get("role") or role or "leaf"),
                        }
                    )
            elif goal_s:
                items = [{"goal": goal_s, "context": context, "role": role}]
            else:
                return "ERROR: provide goal or tasks[]"
            if not items:
                return "ERROR: provide goal or tasks[]"

            # No hard reject on task count — extra workers queue on the
            # thread pool (max_concurrent_children is concurrency, not a cap).
            workers = max(1, int(settings.max_concurrent_children or 3))
            planned: list[dict[str, Any]] = []
            for it in items:
                row = dict(it)
                row["child_id"] = str(row.get("child_id") or "").strip() or new_id("agent")
                planned.append(row)
            note = ctx.note_canvas_tasks
            if note is not None:
                try:
                    note(planned)
                except Exception:
                    pass

            results: list[str | None] = [None] * len(planned)

            def _one(idx: int, item: dict[str, Any]) -> tuple[int, str]:
                g = str(item.get("goal") or "").strip()
                child_ctx = str(item.get("context") or "").strip()
                r = str(item.get("role") or role or "leaf")
                if not g:
                    return idx, "ERROR: empty goal"
                try:
                    summary = run_child(
                        goal=g,
                        context=child_ctx,
                        role=r,
                        child_id=str(item.get("child_id") or ""),
                    )
                except Exception as exc:  # noqa: BLE001
                    summary = f"ERROR: child failed: {exc}"
                return idx, summary

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = [
                    pool.submit(contextvars.copy_context().run, _one, i, it)
                    for i, it in enumerate(planned)
                ]
                for fut in as_completed(futs):
                    i, summary = fut.result()
                    results[i] = summary

            payload = [
                {
                    "index": i,
                    "goal": planned[i].get("goal"),
                    "summary": results[i],
                }
                for i in range(len(planned))
            ]
            return json.dumps(payload, ensure_ascii=False, indent=2)

        def delegate_dialogue(
            topic: str = "",
            speakers: Optional[list[Any]] = None,
            rounds: int = 3,
            extra: str = "",
            mode: str = "",
        ) -> str:
            from ..dialogue import normalize_speakers, run_sequential_dialogue

            try:
                parsed = normalize_speakers(speakers)
                n_rounds = max(1, min(int(rounds or 3), 8))
                mode_s = (mode or "").strip()
                topic_s = (topic or "").strip()
                try:
                    result = run_sequential_dialogue(
                        run_child=run_child,
                        topic=topic_s,
                        speakers=parsed,
                        rounds=n_rounds,
                        extra=extra,
                        mode=mode_s,
                    )
                finally:
                    end = ctx.end_party_session
                    if end is not None:
                        end()
            except ValueError as exc:
                return f"ERROR: {exc}"
            except Exception as exc:  # noqa: BLE001
                return f"ERROR: dialogue failed: {exc}"
            return json.dumps(result, ensure_ascii=False, indent=2)

        reg.register(
            Tool(
                "delegate_task",
                "DEFAULT: spawn isolated subagent(s) for work (search, research, "
                "edit, gather sources). Parallel: tasks=[{goal,context,role?}]. "
                "Parent gets summaries and synthesizes. Children cannot hear each "
                "other. Use this when the user wants several agents to work "
                "separately then merge — NOT delegate_dialogue. "
                "Children may re-delegate if spawn depth remains.",
                {
                    "type": "object",
                    "properties": {
                        "goal": {"type": "string"},
                        "context": {"type": "string"},
                        "role": {
                            "type": "string",
                            "enum": ["leaf", "orchestrator"],
                            "default": "leaf",
                        },
                        "tasks": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "goal": {"type": "string"},
                                    "context": {"type": "string"},
                                    "role": {"type": "string"},
                                },
                                "required": ["goal"],
                            },
                        },
                    },
                    "required": [],
                },
                delegate_task,
                # Safe to run multiple delegate_task calls concurrently: each
                # spawns an isolated child agent, and shared parent bookkeeping
                # (children list / canvas index) is protected by Agent._child_lock.
                # This matters because some models emit N separate delegate_task
                # calls (one per worker) instead of a single tasks=[...] call —
                # without this, those N calls would run strictly one-at-a-time.
                parallel_safe=True,
            )
        )
        reg.register(
            Tool(
                "delegate_dialogue",
                "ONLY for live in-character turns where parties hear each other "
                "(debate, negotiation, military sim, tabletop) — 2 to 8 named "
                "parties. Each party is a FULL agent with the same tools as you, "
                "kept across rounds, and may spawn further agents. "
                "NOT for parallel research, search, or 'start N agents then "
                "summarize' — that is delegate_task. Do NOT write a code simulator. "
                "Do NOT enter Plan mode. If the user did not specify the exact "
                "scenario, party list, or format, ask_user and/or search first. "
                "speakers: [{name, brief}]. mode is a free label. rounds: 1–8.",
                {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Scenario in the user's own words. Do not invent a motion they did not state.",
                        },
                        "mode": {
                            "type": "string",
                            "description": "Free-form session type, e.g. simulation, negotiation, debate, tabletop.",
                        },
                        "speakers": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 8,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {
                                        "type": "string",
                                        "description": "Party name (any label: 红方, NATO, 调解人, …).",
                                    },
                                    "brief": {
                                        "type": "string",
                                        "description": "That party's role, objective, or constraints.",
                                    },
                                    "stance": {
                                        "type": "string",
                                        "description": "Alias of brief.",
                                    },
                                },
                                "required": ["name"],
                            },
                        },
                        "rounds": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 8,
                            "default": 3,
                            "description": "How many full cycles through all parties.",
                        },
                        "extra": {
                            "type": "string",
                            "description": "Optional extra rules (fog of war, time, scoring).",
                        },
                    },
                    "required": ["topic", "speakers"],
                },
                delegate_dialogue,
                parallel_safe=False,
            )
        )
