"""UI message projection for sessions."""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ...runtime.agent import Agent
from ...core.config import Settings, get_settings, reload_settings
from ...core.events import EventBus, new_id
from ...core.logutil import get_logger, log_exception
from ..session import load_session, load_session_bundle, save_session, sessions_dir, workspace_matches

_log = get_logger("metateam.store")

from .model import ChatSession
from .titles import _is_internal_message, _message_display_text, _extract_skill_title_source

class SessionUiMixin:
    def ui_messages(self, sess: ChatSession) -> list[dict[str, Any]]:
        """User-facing transcript including assistant text and tool cards.

        Internal plan-step prompts / system noise are omitted. Tool calls are
        paired with their results so history restore matches the live thread.
        """
        import json as _json

        msgs = _full_transcript(sess.agent)
        owner = str(getattr(sess.agent, "agent_id", "") or "main")
        results_by_id: dict[str, dict[str, Any]] = {}
        for m in msgs:
            if m.get("role") != "tool":
                continue
            cid = str(m.get("tool_call_id") or "").strip()
            if cid:
                results_by_id[cid] = m

        out: list[dict[str, Any]] = []
        emitted_tool_ids: set[str] = set()

        for m in msgs:
            role = m.get("role")
            if role == "user":
                if _is_internal_message(m):
                    continue
                content = _message_display_text(m).strip()
                if content and not content.startswith("Iteration budget exhausted"):
                    item: dict[str, Any] = {"role": "user", "content": content, "agent_id": owner}
                    ts = m.get("ts")
                    if ts:
                        item["ts"] = ts
                    out.append(item)
                continue

            if role == "assistant":
                if _is_internal_message(m):
                    continue
                content = str(m.get("content") or "").strip()
                reasoning = str(m.get("reasoning") or "").strip()
                try:
                    from ...runtime.text_tool_calls import extract_text_tool_calls

                    content, _ = extract_text_tool_calls(content)
                except Exception:
                    pass
                # Tool rounds often have empty content + reasoning + tool_calls.
                # Dropping those rows hid thinking when history was reloaded.
                if content or reasoning:
                    item = {
                        "role": "assistant",
                        "content": content,
                        "agent_id": owner,
                    }
                    if reasoning:
                        item["reasoning"] = reasoning
                    ts = m.get("ts")
                    if ts:
                        item["ts"] = ts
                    meta = m.get("sidekick")
                    if isinstance(meta, dict):
                        item["sidekick"] = meta
                    out.append(item)

                for tc in m.get("tool_calls") or []:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                    call_id = str(tc.get("id") or "").strip()
                    name = str((fn or {}).get("name") or "tool")
                    args_raw = (fn or {}).get("arguments") or "{}"
                    args: Any
                    if isinstance(args_raw, dict):
                        args = args_raw
                    else:
                        try:
                            parsed = _json.loads(str(args_raw))
                            args = parsed if isinstance(parsed, dict) else {"_raw": str(args_raw)}
                        except Exception:
                            args = {"_raw": str(args_raw)}
                    result_msg = results_by_id.get(call_id) if call_id else None
                    result = str((result_msg or {}).get("content") or "")
                    if result.startswith("ERROR"):
                        status = "error"
                    elif result_msg is not None:
                        status = "done"
                    else:
                        status = "running"
                    tool_item = {
                        "role": "tool",
                        "name": name,
                        "call_id": call_id or new_id("call"),
                        "args": args,
                        "result": result,
                        "status": status,
                        "agent_id": owner,
                    }
                    ts = (result_msg or {}).get("ts") or m.get("ts")
                    if ts:
                        tool_item["ts"] = ts
                    out.append(tool_item)
                    if call_id:
                        emitted_tool_ids.add(call_id)
                continue

            # Orphan tool rows (no matching assistant tool_calls) — still show them.
            if role == "tool":
                cid = str(m.get("tool_call_id") or "").strip()
                if cid and cid in emitted_tool_ids:
                    continue
                result = str(m.get("content") or "")
                out.append(
                    {
                        "role": "tool",
                        "name": str(m.get("name") or "tool"),
                        "call_id": cid or new_id("call"),
                        "args": {},
                        "result": result,
                        "status": "error" if result.startswith("ERROR") else "done",
                        "agent_id": owner,
                        **({"ts": m.get("ts")} if m.get("ts") else {}),
                    }
                )
                if cid:
                    emitted_tool_ids.add(cid)

        return out

