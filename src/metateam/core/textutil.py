"""Small text helpers shared across runtime (clipping, cleanup)."""

from __future__ import annotations


def split_dangling_colon(text: str) -> tuple[str, str]:
    """Split a trailing run of colons (plus whitespace) from visible text.

    Models usually write "我先检查一下：" right before a tool call. Streaming
    withholds that tail until the next fragment proves it is mid-sentence;
    when the stream ends without one, the tail is dropped instead of shown.
    Returns ``(visible, held_tail)``.
    """
    raw = text or ""
    body = raw.rstrip()
    cut = len(body)
    while cut:
        # Treat whitespace between trailing colons as part of the same tail.
        j = cut
        while j and body[j - 1].isspace():
            j -= 1
        if j and body[j - 1] in ":：":
            cut = j - 1
            continue
        break
    if cut == len(body):
        return raw, ""
    return body[:cut], body[cut:] + raw[len(body) :]


def strip_dangling_colon(text: str) -> str:
    """Drop a trailing lead-in colon left when tool markup is spliced out.

    The colon is the last visible character of a preamble and makes a finished
    message look truncated. Only the trailing run is removed — colons inside
    the text ("注意：事项") are meaningful.
    """
    return split_dangling_colon(text)[0]


def safe_clip(text: str, limit: int, *, ellipsis: str = "…") -> str:
    """Clip text without leaving dangling markdown emphasis like ``**插``.

    Truncates on a character boundary and, when an odd number of ``**`` remains,
    backs up to the last opener so UI summaries don't end mid-bold.
    """
    raw = text or ""
    if limit <= 0:
        return ""
    if len(raw) <= limit:
        return raw
    cut = raw[:limit].rstrip()
    # Prefer breaking at a paragraph / sentence boundary near the end.
    for sep in ("\n\n", "\n", "。", "！", "？", ". ", "; ", "，", ", "):
        idx = cut.rfind(sep)
        if idx >= max(0, limit // 2):
            cut = cut[: idx + len(sep)].rstrip()
            break
    # Close or drop unfinished **bold**
    if cut.count("**") % 2 == 1:
        last = cut.rfind("**")
        if last >= 0:
            cut = cut[:last].rstrip()
    # Drop a trailing lone *
    if cut.endswith("*") and not cut.endswith("**"):
        cut = cut[:-1].rstrip()
    return cut + ellipsis
