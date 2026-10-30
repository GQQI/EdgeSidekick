"""FS undo package."""

from .context import (
    MAX_UNDO,
    set_turn_context,
    set_mutation_meta,
    clear_mutation_meta,
    clear_turn_context,
)
from .stack import (
    push,
    push_before_write,
    push_before_create,
    push_before_delete,
    push_before_move,
    push_before_rename,
    push_checkpoint,
)
from .review import review_snapshot, file_review_pair
from .ops import (
    status,
    turn_user_text,
    undo_file,
    undo_to_turn,
    undo_latest_turn,
    undo_to_id,
    undo_one,
)

__all__ = [
    "MAX_UNDO",
    "set_turn_context", "set_mutation_meta", "clear_mutation_meta", "clear_turn_context",
    "push", "push_before_write", "push_before_create", "push_before_delete",
    "push_before_move", "push_before_rename", "push_checkpoint",
    "review_snapshot", "file_review_pair",
    "status", "turn_user_text", "undo_file", "undo_to_turn", "undo_latest_turn",
    "undo_to_id", "undo_one",
]
