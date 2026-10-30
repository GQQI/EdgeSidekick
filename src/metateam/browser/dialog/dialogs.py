"""Browser dialog helpers."""
from __future__ import annotations

from typing import Any

def dialog_interrupted(exc: BaseException) -> bool:
    """Playwright aborts the in-flight click as soon as a JS dialog opens."""
    text = str(exc).lower()
    return (
        "javascript dialog" in text
        or "dialog interrupted" in text
        or "beforeunload" in text
    )

def dialog_result_note(items: list[dict[str, Any]]) -> str:
    """Tell the model whether the user has actually answered."""
    if not items:
        return ""
    if any(it.get("open") for it in items):
        return (
            "浏览器弹窗还开着（alert、confirm、prompt、离开页面或选择文件）。"
            "用户还没有确认。不要调用下一个工具，也不要开始下一步思考。"
        )
    bits: list[str] = []
    for it in items:
        kind = str(it.get("type") or "dialog")
        msg = " ".join(str(it.get("message") or "").split())[:160]
        accepted = it.get("accepted")
        if kind == "file":
            choice = "已选择文件" if accepted else "取消了文件选择"
        elif accepted is True:
            choice = "点了确定"
        elif accepted is False:
            choice = "点了取消"
        else:
            choice = "已关闭"
        bits.append(f"{kind} {choice}" + (f"（{msg}）" if msg else ""))
    return (
        "用户已经处理完浏览器弹窗："
        + "；".join(bits)
        + "。按这个结果继续，不要再当成没点过。"
    )

def merge_dialog_log(items: list[dict[str, Any]], log: list[Any]) -> None:
    """Copy accepted/value from the in-page hook onto matching dialog records."""
    entries = [e for e in log if isinstance(e, dict)]
    used: set[int] = set()
    for item in items:
        if item.get("type") in {"file", "beforeunload"}:
            continue
        for i, entry in enumerate(entries):
            if i in used:
                continue
            if str(entry.get("type") or "") != str(item.get("type") or ""):
                continue
            if str(entry.get("message") or "") != str(item.get("message") or ""):
                continue
            used.add(i)
            if "accepted" in entry:
                item["accepted"] = bool(entry.get("accepted"))
            if entry.get("value") is not None:
                item["value"] = entry.get("value")
            break

def public_dialog(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if not str(k).startswith("_")}

def release_unblocked_dialogs(
    items: list[dict[str, Any]], *, page_ready: bool, url: str
) -> None:
    """A responsive page means alert/confirm/beforeunload already closed.

    A file chooser does not freeze the page, so those stay open until the
    file input fires change or cancel.
    """
    if not page_ready:
        return
    for item in items:
        if not item.get("open") or item.get("type") == "file":
            continue
        if item.get("type") == "beforeunload" and item.get("accepted") is None:
            before = str(item.get("_url") or "")
            item["accepted"] = bool(url and before and url != before)
        item["open"] = False

