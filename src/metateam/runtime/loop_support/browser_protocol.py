"""Shared Select Mode / browser attachment protocol (Capability A)."""

from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel, Field

PROTOCOL_VERSION = 1
DOM_ELEMENT_KIND = "dom-element"

# Keep computed styles short for token budget (aligned with VS Code attachCSS idea).
STYLE_KEYS = (
    "display",
    "position",
    "color",
    "backgroundColor",
    "fontSize",
    "fontWeight",
    "fontFamily",
    "lineHeight",
    "padding",
    "margin",
    "border",
    "borderRadius",
    "width",
    "height",
    "flexDirection",
    "justifyContent",
    "alignItems",
    "gap",
    "opacity",
    "visibility",
    "overflow",
    "textAlign",
)


class DomRect(BaseModel):
    x: float = 0
    y: float = 0
    width: float = 0
    height: float = 0


class DomComponentHint(BaseModel):
    name: str = ""
    framework: str = ""
    file_hint: str = ""


class DomElementPayload(BaseModel):
    """Structured element identity sent from Select Mode into chat."""

    kind: str = DOM_ELEMENT_KIND
    protocol_version: int = PROTOCOL_VERSION
    url: str = ""
    tag: str = ""
    xpath: str = ""
    css_path: str = ""
    id: str = ""
    classes: list[str] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)
    inner_text: str = ""
    role: str = ""
    rect: DomRect = Field(default_factory=DomRect)
    computed_styles: dict[str, str] = Field(default_factory=dict)
    component: Optional[DomComponentHint] = None
    outer_html: str = ""
    screenshot_base64: str = ""
    selected_at: float = 0


def chip_label(payload: DomElementPayload | dict[str, Any]) -> str:
    data = payload if isinstance(payload, DomElementPayload) else DomElementPayload.model_validate(payload)
    tag = (data.tag or "node").lower()
    parts = [tag]
    if data.id:
        parts.append(f"#{data.id}")
    if data.classes:
        parts.append("." + ".".join(data.classes[:2]))
    return "DOM · " + "".join(parts)[:64]


def format_dom_element_for_agent(payload: DomElementPayload | dict[str, Any]) -> str:
    """Markdown block injected into the user→model message."""
    data = payload if isinstance(payload, DomElementPayload) else DomElementPayload.model_validate(payload)
    slim = data.model_dump()
    # Drop huge screenshot from text block; UI may still keep it separately later.
    slim.pop("screenshot_base64", None)
    if slim.get("outer_html") and len(slim["outer_html"]) > 2500:
        slim["outer_html"] = slim["outer_html"][:2500] + "…"
    if slim.get("inner_text") and len(slim["inner_text"]) > 500:
        slim["inner_text"] = slim["inner_text"][:500] + "…"
    body = json.dumps(slim, ensure_ascii=False, indent=2)
    return (
        "### 选中的页面元素（Select Mode）\n"
        "用户从内置浏览器沙盒点选了以下 DOM 节点。请根据视觉意图修改**源码**（不是运行时 DOM）；"
        "优先用 component / xpath / 文案 / class / id 定位文件。改完依赖页面热更新或刷新验证。\n\n"
        f"```dom-element\n{body}\n```"
    )


def parse_dom_element_json(raw: str) -> Optional[DomElementPayload]:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("kind") and data.get("kind") != DOM_ELEMENT_KIND:
        return None
    try:
        return DomElementPayload.model_validate(data)
    except Exception:
        return None
