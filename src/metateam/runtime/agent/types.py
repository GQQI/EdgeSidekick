"""AgentResult and shared agent constants."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SUBAGENT_ITERATION_BUDGET = 12

_STALL_NUDGE = (
    "Previous generation stalled. Continue now: call a tool or answer briefly. "
    "Do not restart a long silent thinking pass."
)

_FILE_RETRY_NUDGE = (
    "Previous file tool returned ERROR/WARNING — not success. "
    "Retry with complete args, look up a missing path, or change encoding=. "
    "Do not invent a file body or repeat the same missing path."
)

_BROWSER_NAV_RECOVER_NUDGE = (
    "browser_navigate did not load the page (connection refused / chrome-error / "
    "load failure). Do NOT stop the task and do NOT emit a final answer that says "
    "the page did not open or that retries have stopped. Do NOT call browser_navigate "
    "again with the identical URL/args. Recover toward the user goal: if the target "
    "is localhost/127.0.0.1, start or restart the static/dev server for that workspace "
    "file first (bind 127.0.0.1), confirm the listening port, then navigate; for a "
    "static .html prefer the workspace-relative path; try localhost vs 127.0.0.1 when "
    "one refused; diagnose with run_shell / read_file as needed. Keep working."
)

_POST_COMPRESS_CONTINUE_NUDGE = (
    "Context was compacted into working memory. "
    "Your previous assistant output was NOT truncated — do not apologize about "
    "truncation or stop. Continue the same user goal now: call a tool or give "
    "the final answer from existing evidence."
)

_EMPTY_ASSISTANT_RECOVER_NUDGE = (
    "Previous model round returned no message (often after a large tool result "
    "or context compaction). Continue the same user goal: call a tool or answer "
    "briefly. Do not apologize about truncation."
)


@dataclass
class AgentResult:
    text: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    compressed: bool = False
    agent_id: str = ""
    review: dict[str, Any] = field(default_factory=dict)
    cancelled: bool = False
