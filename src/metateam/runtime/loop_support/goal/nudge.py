"""Continue nudges and plan-step continuation helpers."""

from __future__ import annotations

from typing import Any, Optional

from ....core.textutil import safe_clip

GOAL_CONTINUE_NUDGE = (
    # Internal developer/system instruction only — never framed as a user request.
    "Goal unfinished. Next concrete action now: native tool call, or final "
    "answer from existing evidence. Do not repeat a successful step; do not "
    "only plan/promise. If done or blocked on the user, stop."
)


def goal_continue_nudge(reason: str = "") -> str:
    """System delta that sends the loop back into ReAct Think."""
    body = GOAL_CONTINUE_NUDGE
    why = safe_clip((reason or "").strip(), 160)
    if why:
        return f"{body}\nJudge reason: {why}"
    return body


def status_for_goal_verdict(
    state: Optional[str],
    *,
    reason: str = "",
    stalled_no_progress: bool = False,
) -> str:
    """Short user-visible status line for the ReAct gate (zh)."""
    why = safe_clip((reason or "").strip(), 72)
    if stalled_no_progress:
        return "继续执行没有带来新结果，已停止。" + (f"（{why}）" if why else "")
    if state == "continue":
        return f"未完成，继续执行…{(' ' + why) if why else ''}".rstrip()
    if state == "done":
        return f"目标已完成{(' · ' + why) if why else ''}"
    if state == "blocked":
        return f"等待你的决定{(' · ' + why) if why else ''}"
    return "目标判定不可用，本轮结束。"


def should_continue_for_goal(state: Optional[str]) -> bool:
    """Whether a tool-less reply should be rejected so the turn keeps working.

    Only an explicit ``continue`` verdict reopens the turn. ``done`` and
    ``blocked`` stop. An unreadable verdict also stops: spending another
    model round on a guess is what made finished turns keep "improving".

    There is deliberately no fixed continuation count here. A fixed count is
    both too small (a long task stops while it is still progressing) and too
    large (a stuck model repeats the same answer three times). The loop ends a
    continuation only when it produced no new tool evidence; that is the
    observable difference between progress and tape-recorder repetition.
    """
    return state == "continue"


def should_run_next_plan_step(state: Optional[str]) -> bool:
    """Whether the remaining plan steps still have work to do.

    A plan is drafted before execution, so an early step can already satisfy
    the user's actual goal; later steps are then unrequested polish. Stop only
    on a positive ``done``/``blocked`` verdict. An unreadable verdict keeps the
    approved plan running — skipping approved work is worse than one extra
    step, and the per-step turn gate still guards each step's own stop.
    """
    return state not in ("done", "blocked")

