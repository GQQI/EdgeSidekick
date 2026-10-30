"""LLM package — stable exports (plus private helpers for back-compat)."""
from . import client as _client
from . import parse as _parse
from . import stream as _stream
from .client import LLM

_SKIP = {
    "__name__",
    "__package__",
    "__loader__",
    "__spec__",
    "__file__",
    "__cached__",
    "__builtins__",
    "__doc__",
}
for _mod in (_parse, _stream, _client):
    for _k, _v in vars(_mod).items():
        if _k not in _SKIP:
            globals()[_k] = _v

__all__ = [
    "LLM",
    "parse_tool_args",
    "fold_reasoning_text",
    "streaming_text_looped",
    "extract_content_text",
    "extract_reasoning_text",
    "extract_message_text",
    "align_tool_chains",
    "StreamWatchdog",
    "RepeatCut",
]

del _SKIP, _mod, _k, _v, _client, _parse, _stream
