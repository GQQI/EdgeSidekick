"""ask_user, delegate_task, delegate_dialogue, ask_agent — with AskPacket visibility."""

from __future__ import annotations

import contextvars
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from ....core.events import new_id
from ...ask import normalize_option_labels
from ...coord.policy import (
    enrich_child_context,
    enrich_consult_context,
    enrich_dialogue_extra,
    format_delegate_payload,
    parse_child_summary,
    prepare_user_ask,
)
from ...tool_registry import Tool, ToolRegistry
from ..context import ToolContext


def _record_ask_edge(ctx: ToolContext, edge: dict[str, Any]) -> None:
    fn = getattr(ctx, "note_ask_edge", None)
    if callable(fn):
        try:
            fn(edge)
        except Exception:
            pass


_DROP_TOP_LEVEL = (
    "goal",
    "task",
    "query",
    "prompt",
    "description",
    "context",
    "role",
    "what",
    "how",
    "why",
    "stop",
    "need",
    "evidence",
    "constraints",
    "prior_answers",
)


def _clean_task_row(row: dict[str, Any]) -> dict[str, Any]:
    goal = str(row.get("goal") or row.get("task") or row.get("query") or "").strip()
    if not goal:
        return {}
    return {"goal": goal, "context": str(row.get("context") or "").strip()}


def normalize_delegate_tasks(tasks: Any) -> list[Any] | None:
    """Turn provider quirks into a real list. Does not invent or split workers.

    Only unwraps JSON-encoded strings / single-dict wrappers some models emit.
    """
    if tasks is None:
        return None
    if isinstance(tasks, str):
        text = tasks.strip()
        if not text:
            return None
        if text[0] in "[{":
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                return list(parsed)
            if isinstance(parsed, dict):
                return [parsed]
        return [{"goal": text}]
    if isinstance(tasks, dict):
        return [tasks]
    if isinstance(tasks, (list, tuple)):
        out: list[Any] = []
        for item in tasks:
            if isinstance(item, str) and item.strip()[:1] in "[{":
                try:
                    nested = json.loads(item.strip())
                except Exception:
                    nested = None
                if isinstance(nested, list):
                    out.extend(nested)
                    continue
                if isinstance(nested, dict):
                    out.append(nested)
                    continue
            out.append(item)
        return out
    return None


def coerce_delegate_task_args(args: dict[str, Any] | None) -> dict[str, Any]:
    """Pass through the model's ``tasks[]``; only fix wire shape.

    - ``tasks`` as JSON string / dict → list
    - legacy top-level ``goal`` (truncated stream) → one-item ``tasks``
    - drop legacy top-level fields; keep each item as ``{goal, context}``
    Does not invent extra workers from path heuristics.
    """
    out = dict(args or {})
    tasks = normalize_delegate_tasks(out.get("tasks"))
    if not tasks:
        goal_s = str(
            out.get("goal")
            or out.get("task")
            or out.get("query")
            or out.get("prompt")
            or out.get("description")
            or ""
        ).strip()
        if goal_s:
            tasks = [
                {
                    "goal": goal_s,
                    "context": str(out.get("context") or "").strip(),
                }
            ]
    if not tasks:
        return out

    cleaned: list[dict[str, Any]] = []
    for item in tasks:
        if isinstance(item, str) and item.strip():
            row = _clean_task_row({"goal": item.strip()})
        elif isinstance(item, dict):
            row = _clean_task_row(item)
        else:
            continue
        if row:
            cleaned.append(row)
    if cleaned:
        out["tasks"] = cleaned
        out.pop("_incomplete", None)
        for key in _DROP_TOP_LEVEL:
            out.pop(key, None)
    return out


def register_ask_and_delegate(reg: ToolRegistry, ctx: ToolContext) -> None:
    settings = ctx.settings
    ask_user_fn = ctx.ask_user_fn
    run_child = ctx.run_child
    allow_delegate = ctx.run_child is not None

    if ask_user_fn is not None:

        def ask_user(
            question: str,
            options: Optional[list[str]] = None,
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
                                "REQUIRED: 2–12 mutually exclusive choices as a JSON "
                                "array of short button labels, e.g. "
                                "[\"方案 A\",\"方案 B\"]. Do not put choices only in "
                                "why/question — they must be in this options field."
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
            tasks: Optional[list[Any]] = None,
            share: Optional[list[str]] = None,
            redact: Optional[list[str]] = None,
        ) -> str:
            """Spawn one child per ``tasks[]`` item. Args are coerced upstream."""
            coerced = coerce_delegate_task_args({"tasks": tasks})
            task_list = normalize_delegate_tasks(coerced.get("tasks"))
            if not task_list:
                return (
                    'ERROR: emit ONLY {"tasks":[{"goal":"…","context":"…"},…]} — '
                    "one object per worker, distinct goals, no top-level goal."
                )
            items: list[dict[str, Any]] = []
            for raw in task_list:
                if isinstance(raw, str) and raw.strip():
                    items.append({"goal": raw.strip(), "context": ""})
                    continue
                if not isinstance(raw, dict):
                    continue
                g = str(raw.get("goal") or "").strip()
                if not g:
                    continue
                items.append(
                    {
                        "goal": g,
                        "context": str(raw.get("context") or "").strip(),
                        "share": share,
                        "redact": redact,
                    }
                )
            if not items:
                return "ERROR: tasks[] had no usable goal fields"

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
                    role="leaf",
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
                        "what": "execute",
                        "how": "consult",
                        "who": "child",
                        "when": "now",
                        "why": "",
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
                        role="leaf",
                        child_id=str(item.get("child_id") or ""),
                        stop_when=str(packet.act.stop or ""),
                        deliverable=list(packet.reply_contract.slots or []),
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
            from ...dialogue import normalize_speakers, run_sequential_dialogue

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
                    stop_when=str(packet.act.stop or ""),
                    deliverable=list(packet.reply_contract.slots or []),
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
                "Spawn isolated subagent(s) in ONE call. "
                "Arguments MUST be "
                '{"tasks":[{"goal":"…","context":"…"},…]} only. '
                "Independent scopes ⇒ multiple DISTINCT tasks[] items "
                "(never one catch-all task). "
                "Example: "
                '{"tasks":['
                '{"goal":"审计前端","context":"读 static/"},'
                '{"goal":"审计后端","context":"读 api/"},'
                '{"goal":"审计部署","context":"读 Dockerfile"}'
                "]}. "
                "goal≤80, context≤240. Parent synthesizes. "
                "NOT for live multi-party talk — use delegate_dialogue.",
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "tasks": {
                            "type": "array",
                            "minItems": 1,
                            "description": (
                                "One object per worker. Multiple scopes ⇒ "
                                "multiple items with distinct goals."
                            ),
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "goal": {
                                        "type": "string",
                                        "description": "Short distinct objective (≤80 chars).",
                                    },
                                    "context": {
                                        "type": "string",
                                        "description": "Brief boundary/format (≤240 chars).",
                                    },
                                },
                                "required": ["goal"],
                            },
                        },
                    },
                    "required": ["tasks"],
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
