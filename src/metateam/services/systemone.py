"""Optional System One judge (self-hosted Kev, or hosted Jev).

TypeSafe Jev has no published weights. A compatible server speaks
POST /v1/systemone and returns a yes/no probability on a noul question.
URL comes from SIDEKICK_SYSTEMONE_URL, else the address saved in Settings.
Unset means this judge is off and the selected chat model decides.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse


def _stored_systemone() -> dict[str, str]:
    try:
        from .app_prefs import load_app_prefs

        prefs = load_app_prefs()
    except Exception:
        prefs = {}
    return {
        "url": str(prefs.get("systemone_url") or "").strip(),
        "model": str(prefs.get("systemone_model") or "").strip(),
        "key": str(prefs.get("systemone_key") or "").strip(),
        "threshold": str(prefs.get("systemone_threshold") or "").strip(),
    }


def systemone_base_url() -> str:
    env = os.getenv("SIDEKICK_SYSTEMONE_URL", "").strip().rstrip("/")
    if env:
        return env
    return _stored_systemone()["url"].rstrip("/")


def _systemone_model() -> str:
    env = os.getenv("SIDEKICK_SYSTEMONE_MODEL", "").strip()
    if env:
        return env
    return _stored_systemone()["model"] or "kev-latest"


def _systemone_key() -> str:
    env = os.getenv("SIDEKICK_SYSTEMONE_KEY", "").strip()
    if env:
        return env
    return _stored_systemone()["key"]


def _systemone_threshold() -> float:
    raw = os.getenv("SIDEKICK_SYSTEMONE_SPEAK_THRESHOLD", "").strip()
    if not raw:
        raw = _stored_systemone()["threshold"] or "0.5"
    try:
        return float(raw)
    except ValueError:
        return 0.5


def systemone_configured() -> bool:
    return bool(systemone_base_url())


# Kev scores a noul question by comparing the true option with the false option.
# A bare instruction has no options to score, so a greeting stays under the line.
SPEAK_INSTRUCTIONS = "这个成员现在要不要发言？"
SPEAK_CRITERIA = {
    "true": "要发言。用户在跟群里的人打招呼，或在问功能、身份、能力，或点名了这个成员，或在问谁改过文件、改了什么、目录有什么变化，而且本轮还没有人把这件事说完。",
    "false": "不发言。滑动窗口和工作现场里这件事已经说完。本轮已经有人说过话，而且没有还没被说出来的文件改动。就算这个成员刚才也想说，看过这些之后就不用再说。",
}


def _window_lines(lines: list[str], recent: int = 6) -> tuple[str, list[str]]:
    """Keep the latest lines. Fold older lines into a short sliding summary."""
    if len(lines) <= recent:
        return "", lines
    older = lines[:-recent]
    chunks: list[str] = []
    for start in range(0, len(older), 4):
        chunks.append(_clip("；".join(older[start : start + 4]), 120))
    return "\n".join(chunks[-2:]), lines[-recent:]


def _clip(text: str, limit: int = 180) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "…"


def build_judge_state(
    *,
    name: str,
    duty: str,
    user_text: str,
    prior: list[dict[str, str]],
    addressed: bool,
    history: list[dict[str, str]] | None = None,
    greeting: bool = False,
    capability: bool = False,
    site: str = "",
) -> str:
    """State sent to Kev. Older lines fold into a summary; the latest stay in a window."""
    timeline: list[str] = []
    for row in history or []:
        if not isinstance(row, dict):
            continue
        text = _clip(row.get("text") or "")
        if not text:
            continue
        who = "用户" if str(row.get("role") or "") == "user" else (str(row.get("name") or "").strip() or "成员")
        timeline.append(f"{who}：{text}")
    for row in prior:
        if not isinstance(row, dict):
            continue
        text = _clip(row.get("text") or "")
        if not text:
            continue
        timeline.append(f"{str(row.get('name') or '成员').strip() or '成员'}：{text}")
    summary, recent = _window_lines(timeline, 6)
    spoken = [f"{i + 1}. {line}" for i, line in enumerate(recent)] or ["（窗口里还没有消息）"]
    facts = []
    if greeting:
        facts.append("用户在跟群里的人打招呼。")
    if capability:
        facts.append("用户在问功能、身份或能力。")
    if addressed:
        facts.append("用户点名了这个成员。")
    elif not greeting:
        facts.append("用户没有点名这个成员。")
    facts.append("本轮已经有人说过话。" if prior else "本轮还没有人回答这条消息。")
    work = "\n".join(line.strip() for line in str(site or "").splitlines() if line.strip())
    if work:
        facts.append("工作现场里有文件改动或目录查看，窗口里未必已经说过。")
    blocks = [
        f"成员：{name}",
        f"职责：{duty or name}",
        f"用户消息：\n{user_text}",
    ]
    if work:
        blocks.append("工作现场：\n" + work[:360])
    blocks.extend(
        [
            "滑动摘要：\n" + (summary or "（没有落在窗口外的更早消息）"),
            "滑动窗口：\n" + "\n".join(spoken),
            "事实：\n" + "\n".join(f"- {fact}" for fact in facts),
        ]
    )
    return "\n".join(blocks)


def parse_noul(payload: dict[str, Any], key: str = "speak") -> float | None:
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        return None
    row = answers.get(key)
    if not isinstance(row, dict) or "noul" not in row:
        return None
    try:
        value = float(row["noul"])
    except (TypeError, ValueError):
        return None
    if value < 0 or value > 1:
        return None
    return value


def _allowed_base(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "systemone url must be http(s) with a host"
    host = parsed.hostname.lower().rstrip(".")
    if host in {"metadata.google.internal", "metadata", "metadata.internal"}:
        return "systemone url is blocked"
    return None


def _session_files() -> list[Path]:
    """Saved chats from this process, and the desktop app's session folder."""
    from pathlib import Path

    found: list[Path] = []
    try:
        from .store import STORE

        found.extend(STORE._session_paths_for_user())
    except Exception:
        pass
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        base = Path(appdata) / "EdgeSidekick" / "sessions"
        if base.is_dir():
            found.extend(base.glob("*/*.json"))
    unique: list[Path] = []
    seen: set[str] = set()
    for path in found:
        key = str(path)
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _work_site(context: str, workspace: str) -> str:
    """Client notes from this chat, plus edits saved in earlier sessions."""
    from .worklog import recent_work_lines

    lines = [row.strip()[:100] for row in str(context or "").splitlines() if row.strip()]
    try:
        for row in recent_work_lines(_session_files(), workspace):
            if row not in lines:
                lines.append(row)
    except Exception:
        pass
    return "\n".join(lines[:6])[:360]


def judge_speak(
    *,
    name: str,
    duty: str,
    user_text: str,
    prior: list[dict[str, str]],
    addressed: bool,
    history: list[dict[str, str]] | None = None,
    greeting: bool = False,
    capability: bool = False,
    context: str = "",
    workspace: str = "",
    timeout_sec: float = 8.0,
) -> dict[str, Any]:
    """Return {available, speak, probability}. available is false when unset."""
    base = systemone_base_url()
    if not base:
        return {"available": False, "speak": None, "probability": None}
    blocked = _allowed_base(base)
    if blocked:
        return {"available": True, "speak": None, "probability": None, "error": blocked}
    state = build_judge_state(
        name=name,
        duty=duty,
        user_text=user_text,
        prior=prior,
        addressed=addressed,
        history=history,
        greeting=greeting,
        capability=capability,
        site=_work_site(context, workspace),
    )
    body = {
        "state": state,
        "model": _systemone_model(),
        "questions": {
            "speak": {
                "type": "noul",
                "instructions": SPEAK_INSTRUCTIONS,
                "criteria": SPEAK_CRITERIA,
            }
        },
    }
    threshold = _systemone_threshold()
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    key = _systemone_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(
        f"{base}/v1/systemone",
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return {"available": True, "speak": None, "probability": None, "error": str(exc)}
    if not isinstance(payload, dict):
        return {"available": True, "speak": None, "probability": None, "error": "bad systemone payload"}
    probability = parse_noul(payload)
    if probability is None:
        return {"available": True, "speak": None, "probability": None, "error": "missing noul"}
    return {"available": True, "speak": probability >= threshold, "probability": probability}
