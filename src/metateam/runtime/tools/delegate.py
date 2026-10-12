"""ask_user, delegate_task, delegate_dialogue, ask_agent — with AskPacket visibility."""

from __future__ import annotations

import contextvars
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from ...core.events import new_id
from ..ask import normalize_option_labels
from ..coord.policy import (
    enrich_child_context,
    enrich_consult_context,
    enrich_dialogue_extra,
    format_delegate_payload,
    parse_child_summary,
    prepare_user_ask,
)
from ..tool_registry import Tool, ToolRegistry
from .context import ToolContext




def _record_ask_edge(ctx: ToolContext, edge: dict[str, Any]) -> None:
    fn = getattr(ctx, "note_ask_edge", None)
    if callable(fn):
        try:
            fn(edge)
        except Exception:
            pass

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
            what: str = "",
            how: str = "",
            why: str = "",
            stop: str = "",
            goal_slice: str = "",
            constraints: str = "",
            evidence: str = "",
            prior_answers: str = "",
            share: Optional[list[str]] = None,
            redact: Optional[list[str]] = None,
        ) -> str:
            q, packet = prepare_user_ask(
                question=question,
                what=what,
                how=how or "options",
                why=why,
                stop=stop,
                goal_slice=goal_slice,
                constraints=constraints,
                evidence=evidence,
                prior_answers=prior_answers,
                share=share,
                redact=redact,
            )
            # Packet is applied to the visible question; options UI unchanged.
            _ = packet
            return ask_user_fn(
                question=q,
                options=normalize_option_labels(options),
                allow_custom=bool(allow_custom),
                custom_label=custom_label or "其他（请补充）",
            )

        reg.register(
            Tool(
                "ask_user",
                "Ask the user ONLY when a real mutually exclusive decision or missing "
                "fact blocks progress (including irreversible deletes). "
                "question is one direct question. options is 2–12 labels, each naming "
                "one choice — not a paragraph and not a 要点. Put reasons and path "
                "lists in why/constraints. Carry what=decision how=options when the "
                "user must pick. Do NOT use ask_user to display summaries, plans, or "
                "findings; those belong in markdown. allow_custom lets the user type "
                "a custom answer.",
                {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": (
                                "One direct question naming the decision. "
                                "Not a summary heading and not a placeholder."
                            ),
                        },
                        "options": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 2,
                            "maxItems": 12,
                            "description": (
                                "2–12 mutually exclusive choices. Each item is one "
                                "button label, not a paragraph or a finding."
                            ),
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
                        "what": {
                            "type": "string",
                            "description": "AskAct.what: decision|fact|preference|constraint|review|clarify",
                        },
                        "how": {
                            "type": "string",
                            "description": "AskAct.how: options|short_answer|structured|evidence",
                        },
                        "why": {
                            "type": "string",
                            "description": "Why this ask is needed now.",
                        },
                        "stop": {
                            "type": "string",
                            "description": "What counts as a sufficient answer.",
                        },
                        "goal_slice": {
                            "type": "string",
                            "description": "Short goal slice the user may see.",
                        },
                        "constraints": {
                            "type": "string",
                            "description": "Hard constraints safe to show the user.",
                        },
                        "evidence": {
                            "type": "string",
                            "description": "Optional evidence refs (usually redacted for users).",
                        },
                        "prior_answers": {
                            "type": "string",
                            "description": "Prior related answers to avoid re-asking.",
                        },
                        "share": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "VisibilitySpec.share override.",
                        },
                        "redact": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "VisibilitySpec.redact override.",
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
            what: str = "",
            how: str = "",
            why: str = "",
            stop: str = "",
            need: str = "",
            evidence: str = "",
            constraints: str = "",
            prior_answers: str = "",
            share: Optional[list[str]] = None,
            redact: Optional[list[str]] = None,
        ) -> str:
            items: list[dict[str, Any]]
            goal_s = str(goal or task or query or prompt or description or "").strip()
            if isinstance(tasks, str) and tasks.strip():
                tasks = [{"goal": tasks.strip()}]
            if tasks:
                items = []
                for raw in tasks:
                    if isinstance(raw, str) and raw.strip():
                        items.append(
                            {
                                "goal": raw.strip(),
                                "context": context,
                                "role": role,
                            }
                        )
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
                            "what": str(raw.get("what") or what or ""),
                            "how": str(raw.get("how") or how or ""),
                            "why": str(raw.get("why") or why or ""),
                            "stop": str(raw.get("stop") or stop or ""),
                            "need": str(raw.get("need") or need or ""),
                            "evidence": str(raw.get("evidence") or evidence or ""),
                            "constraints": str(raw.get("constraints") or constraints or ""),
                            "prior_answers": str(
                                raw.get("prior_answers") or prior_answers or ""
                            ),
                            "share": raw.get("share", share),
                            "redact": raw.get("redact", redact),
                        }
                    )
            elif goal_s:
                items = [
                    {
                        "goal": goal_s,
                        "context": context,
                        "role": role,
                        "what": what,
                        "how": how,
                        "why": why,
                        "stop": stop,
                        "need": need,
                        "evidence": evidence,
                        "constraints": constraints,
                        "prior_answers": prior_answers,
                        "share": share,
                        "redact": redact,
                    }
                ]
            else:
                return "ERROR: provide goal or tasks[]"
            if not items:
                return "ERROR: provide goal or tasks[]"

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
                if not g:
                    return idx, "ERROR: empty goal"
                child_ctx, packet = enrich_child_context(
                    goal=g,
                    context=str(item.get("context") or "").strip(),
                    role=str(item.get("role") or role or "leaf"),
                    what=str(item.get("what") or ""),
                    how=str(item.get("how") or ""),
                    why=str(item.get("why") or ""),
                    stop=str(item.get("stop") or ""),
                    need=str(item.get("need") or ""),
                    evidence=str(item.get("evidence") or ""),
                    constraints=str(item.get("constraints") or ""),
                    prior_answers=str(item.get("prior_answers") or ""),
                    share=item.get("share") if isinstance(item.get("share"), list) else share,
                    redact=item.get("redact") if isinstance(item.get("redact"), list) else redact,
                )
                _record_ask_edge(
                    ctx,
                    {
                        "id": str(item.get("child_id") or new_id("ask")),
                        "from_id": "",
                        "to_id": str(item.get("child_id") or ""),
                        "kind": "delegate",
                        "what": str(item.get("what") or "execute"),
                        "how": str(item.get("how") or "consult"),
                        "who": "child",
                        "when": "now",
                        "why": str(item.get("why") or ""),
                        "need": g,
                        "goal_slice": g,
                        "visibility": packet.to_dict().get("visibility") if hasattr(packet, "to_dict") else {},
                        "status": "asking",
                    },
                )

                try:
                    summary = run_child(
                        goal=g,
                        context=child_ctx,
                        role=str(item.get("role") or role or "leaf"),
                        child_id=str(item.get("child_id") or ""),
                    )
                except Exception as exc:  # noqa: BLE001
                    summary = f"ERROR: child failed: {exc}"
                parsed = parse_child_summary(summary, goal=g)
                wrapped = {
                    "index": idx,
                    "goal": g,
                    "child_id": str(item.get("child_id") or ""),
                    "ask_packet": packet.to_dict(),
                    "result": parsed.to_dict(),
                    "summary": parsed.findings or summary,
                }
                return idx, json.dumps(wrapped, ensure_ascii=False)

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = [
                    pool.submit(contextvars.copy_context().run, _one, i, it)
                    for i, it in enumerate(planned)
                ]
                for fut in as_completed(futs):
                    i, summary = fut.result()
                    results[i] = summary

            payload: list[dict[str, Any]] = []
            for i in range(len(planned)):
                raw = results[i] or ""
                try:
                    payload.append(json.loads(raw))
                except Exception:
                    parsed = parse_child_summary(raw, goal=str(planned[i].get("goal") or ""))
                    payload.append(
                        {
                            "index": i,
                            "goal": planned[i].get("goal"),
                            "child_id": planned[i].get("child_id"),
                            "result": parsed.to_dict(),
                            "summary": raw,
                        }
                    )
            return format_delegate_payload(payload)

        def delegate_dialogue(
            topic: str = "",
            speakers: Optional[list[Any]] = None,
            rounds: int = 3,
            extra: str = "",
            mode: str = "",
            why: str = "",
            share: Optional[list[str]] = None,
            redact: Optional[list[str]] = None,
        ) -> str:
            from ..dialogue import normalize_speakers, run_sequential_dialogue

            extra_s, packet = enrich_dialogue_extra(
                topic=topic,
                extra=extra,
                mode=mode,
                why=why,
                share=share,
                redact=redact,
            )
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
                        extra=extra_s,
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
            if isinstance(result, dict):
                result = dict(result)
                result["ask_packet"] = packet.to_dict()
            return json.dumps(result, ensure_ascii=False, indent=2)

        def ask_agent(
            question: str,
            target: str = "",
            goal_slice: str = "",
            evidence: str = "",
            constraints: str = "",
            prior_answers: str = "",
            why: str = "",
            stop: str = "",
            role: str = "leaf",
            share: Optional[list[str]] = None,
            redact: Optional[list[str]] = None,
        ) -> str:
            """Directed peer consult: A asks B with an explicit visibility packet."""
            q = (question or "").strip()
            if not q:
                return "ERROR: question is required"
            child_ctx, packet = enrich_consult_context(
                question=q,
                goal_slice=goal_slice,
                evidence=evidence,
                constraints=constraints,
                prior_answers=prior_answers,
                target=target,
                why=why,
                stop=stop,
                share=share,
                redact=redact,
            )
            _record_ask_edge(
                ctx,
                {
                    "id": new_id("ask"),
                    "from_id": "",
                    "to_id": target or "",
                    "kind": "consult",
                    "what": "fact",
                    "how": "consult",
                    "who": "peer",
                    "when": "now",
                    "why": why or "peer consult",
                    "need": q,
                    "goal_slice": goal_slice,
                    "visibility": packet.to_dict().get("visibility") if hasattr(packet, "to_dict") else {},
                    "status": "asking",
                },
            )

            goal = f"Consult answer: {q}" if not target else f"Consult ({target}): {q}"
            try:
                summary = run_child(
                    goal=goal,
                    context=child_ctx,
                    role=(role or "leaf"),
                    child_id=new_id("agent"),
                )
            except Exception as exc:  # noqa: BLE001
                return f"ERROR: ask_agent failed: {exc}"
            parsed = parse_child_summary(summary, goal=goal)
            _record_ask_edge(
                ctx,
                {
                    "id": "",  # update latest consult by need match in note handler? send full
                    "from_id": "",
                    "to_id": target or "",
                    "kind": "consult",
                    "what": "fact",
                    "how": "consult",
                    "who": "peer",
                    "when": "now",
                    "why": why or "peer consult",
                    "need": q,
                    "goal_slice": goal_slice,
                    "visibility": packet.to_dict().get("visibility") if hasattr(packet, "to_dict") else {},
                    "status": "done",
                    "summary": (parsed.findings or summary or "")[:500],
                },
            )

            return json.dumps(
                {
                    "target": target or "",
                    "question": q,
                    "ask_packet": packet.to_dict(),
                    "result": parsed.to_dict(),
                    "summary": parsed.findings or summary,
                },
                ensure_ascii=False,
                indent=2,
            )

        reg.register(
            Tool(
                "delegate_task",
                "DEFAULT: spawn isolated subagent(s) for work (search, research, "
                "edit, gather sources). Parallel: tasks=[{goal,context,role?}]. "
                "Each task is wrapped in an AskPacket (what/how/who/when + VisibilitySpec); "
                "siblings stay isolated by default. Parent receives structured results "
                "(findings / recommendation / open_questions). "
                "Workers see NO parent history: goal must state the objective and "
                "what done means; context must carry the output format to merge, "
                "tool guidance (use/skip), and boundaries (out of scope). Set stop "
                "per task so workers end on sufficient evidence. "
                "NOT for live multi-party talk — use delegate_dialogue. "
                "For one directed question to another agent, prefer ask_agent.",
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
                                    "what": {"type": "string"},
                                    "how": {"type": "string"},
                                    "why": {"type": "string"},
                                    "stop": {"type": "string"},
                                    "need": {"type": "string"},
                                    "evidence": {"type": "string"},
                                    "constraints": {"type": "string"},
                                    "share": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "redact": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                },
                                "required": ["goal"],
                            },
                        },
                        "what": {"type": "string"},
                        "how": {"type": "string"},
                        "why": {"type": "string"},
                        "stop": {"type": "string"},
                        "need": {"type": "string"},
                        "evidence": {"type": "string"},
                        "constraints": {"type": "string"},
                        "prior_answers": {"type": "string"},
                        "share": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "redact": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [],
                },
                delegate_task,
                parallel_safe=True,
            )
        )
        reg.register(
            Tool(
                "delegate_dialogue",
                "ONLY for live in-character turns where parties hear each other "
                "(debate, negotiation, military sim, tabletop) — 2 to 8 named "
                "parties. Visibility defaults to dialogue_turn (stance + capped "
                "excerpts; no raw tool dumps). "
                "NOT for parallel research — that is delegate_task. "
                "For a single directed consult, use ask_agent. "
                "speakers: [{name, brief}]. mode is a free label. rounds: 1–8.",
                {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Scenario in the user's own words.",
                        },
                        "mode": {
                            "type": "string",
                            "description": "Free-form session type, e.g. simulation, negotiation, debate.",
                        },
                        "speakers": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 8,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "brief": {"type": "string"},
                                    "stance": {"type": "string"},
                                },
                                "required": ["name"],
                            },
                        },
                        "rounds": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 8,
                            "default": 3,
                        },
                        "extra": {
                            "type": "string",
                            "description": "Optional extra rules (fog of war, time, scoring).",
                        },
                        "why": {"type": "string"},
                        "share": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "redact": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["topic", "speakers"],
                },
                delegate_dialogue,
                parallel_safe=False,
            )
        )
        reg.register(
            Tool(
                "ask_agent",
                "Ask another agent a directed question with an AskPacket "
                "(what/how/who/when + VisibilitySpec). Use for peer consult — "
                "not a full debate (delegate_dialogue) and not bulk parallel work "
                "(delegate_task). Siblings remain isolated unless you explicitly "
                "share fields via share[]. Returns findings / open_questions.",
                {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "What you need from the other agent.",
                        },
                        "target": {
                            "type": "string",
                            "description": "Optional role/name label for who is asked.",
                        },
                        "goal_slice": {"type": "string"},
                        "evidence": {"type": "string"},
                        "constraints": {"type": "string"},
                        "prior_answers": {"type": "string"},
                        "why": {"type": "string"},
                        "stop": {"type": "string"},
                        "role": {
                            "type": "string",
                            "enum": ["leaf", "orchestrator"],
                            "default": "leaf",
                        },
                        "share": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "redact": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["question"],
                },
                ask_agent,
                parallel_safe=True,
            )
        )
