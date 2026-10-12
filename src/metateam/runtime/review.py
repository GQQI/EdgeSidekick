"""Post-turn self-improvement: memory notes + optional skill patches (cheap)."""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from ..core.config import Settings
from .llm import LLM
from ..services.memory import append_memory
from ..services.skills import load_skills


_REVIEW_PROMPT = """You are a silent learning reviewer for EdgeSidekick.

Given the recent transcript excerpt, decide if anything durable should be saved.
Return ONLY JSON:
{
  "memory_notes": ["short factual notes worth remembering"],
  "skill": null | {
     "name": "kebab-name",
     "description": "<=80 chars",
     "content": "markdown procedure with When/Steps/Pitfalls"
  },
  "reason": "one sentence"
}

Rules:
- memory_notes: preferences, env facts — NOT task progress / PR numbers.
- skill: only for non-trivial reusable workflows discovered this session.
- Prefer empty arrays / null when nothing durable.
- Match user language for content.
"""


def _excerpt(messages: list[dict[str, Any]], max_chars: int = 12000) -> str:
    parts: list[str] = []
    for m in messages[-24:]:
        role = m.get("role")
        if role == "system":
            continue
        content = str(m.get("content") or "")[:1500]
        if m.get("tool_calls"):
            names = [(tc.get("function") or {}).get("name") for tc in m["tool_calls"]]
            content += f"\n[tools={names}]"
        parts.append(f"{role}: {content}")
    text = "\n\n".join(parts)
    return text[:max_chars]


def run_review(
    settings: Settings,
    messages: list[dict[str, Any]],
    *,
    llm: Optional[LLM] = None,
) -> dict[str, Any]:
    if not settings.auto_skill_review:
        return {"skipped": True}
    client = llm or LLM(settings, model=settings.review_model)
    raw = client.complete_text(_REVIEW_PROMPT, _excerpt(messages))
    data = _parse_json(raw)
    saved: dict[str, Any] = {"memory": [], "skill": None, "reason": data.get("reason")}

    for note in data.get("memory_notes") or []:
        note = str(note).strip()
        if note:
            append_memory(settings.memory_file, note)
            saved["memory"].append(note)

    skill = data.get("skill")
    if isinstance(skill, dict) and skill.get("name") and skill.get("content"):
        from .tools import save_skill_file

        path = save_skill_file(
            settings,
            str(skill["name"]),
            str(skill.get("description") or skill["name"]),
            str(skill["content"]),
        )
        saved["skill"] = {"name": skill["name"], "path": str(path)}
        # refresh is caller's job
    return saved


def _parse_json(raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {"memory_notes": [], "skill": None, "reason": "parse_failed"}
