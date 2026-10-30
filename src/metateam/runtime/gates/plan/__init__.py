"""Plan gate package — confirm, prep, generate."""

from .gate import PlanConfirmRequest, PlanGate
from .prep import (
    PLAN_PREP_MAX_ROUNDS,
    PLAN_PREP_HINT,
    PLAN_PREP_RETRY_HINT,
    digest_plan_prep,
    extract_plan_goal,
    parse_needs_plan_reply,
    parse_goal_ready_reply,
    goal_ready_to_plan,
    needs_plan,
)
from .tasks import (
    coalesce_layered_writes,
    generate_plan,
    snapshot_plan_tasks,
    apply_confirmed_plan,
    format_plan_markdown,
)

__all__ = [
    "PlanConfirmRequest",
    "PlanGate",
    "PLAN_PREP_MAX_ROUNDS",
    "PLAN_PREP_HINT",
    "PLAN_PREP_RETRY_HINT",
    "digest_plan_prep",
    "extract_plan_goal",
    "parse_needs_plan_reply",
    "parse_goal_ready_reply",
    "goal_ready_to_plan",
    "needs_plan",
    "coalesce_layered_writes",
    "generate_plan",
    "snapshot_plan_tasks",
    "apply_confirmed_plan",
    "format_plan_markdown",
]
