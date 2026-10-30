"""Fillable-element contract helpers for browser_input."""
from __future__ import annotations

_FILLABLE_TAGS = frozenset({"input", "textarea", "select"})

_FILLABLE_ROLES = frozenset({"textbox", "searchbox", "combobox", "spinbutton"})

_PROBE_FILLABLE_JS = """(sel) => {
  const el = typeof sel === "string" ? document.querySelector(sel) : sel;
  if (!el || !el.tagName) return { found: false };
  const tag = String(el.tagName || "").toLowerCase();
  const role = String(el.getAttribute("role") || "").toLowerCase();
  const ceAttr = String(el.getAttribute("contenteditable") || "").toLowerCase();
  const ce = !!(el.isContentEditable || ceAttr === "true");
  const fillable =
    ["input", "textarea", "select"].includes(tag) ||
    ce ||
    ["textbox", "searchbox", "combobox", "spinbutton"].includes(role);
  return {
    found: true,
    tag,
    role,
    contenteditable: ce,
    fillable,
    disabled: !!(el.disabled || el.getAttribute("aria-disabled") === "true"),
    readonly: !!(el.readOnly || el.getAttribute("aria-readonly") === "true"),
  };
}"""

def element_is_fillable(
    *,
    tag: str = "",
    role: str = "",
    contenteditable: bool = False,
) -> bool:
    """True if the element matches Playwright's fill() editability contract."""
    t = (tag or "").strip().lower()
    r = (role or "").strip().lower()
    if t in _FILLABLE_TAGS:
        return True
    if contenteditable:
        return True
    if r in _FILLABLE_ROLES:
        return True
    return False

def non_fillable_input_error(
    *,
    tag: str = "",
    role: str = "",
    index: int | None = None,
    selector: str = "",
) -> str:
    """Structured guidance when browser_input targets a non-editable element."""
    t = (tag or "").strip().lower() or "unknown"
    r = (role or "").strip().lower()
    where = ""
    if index is not None:
        where = f" index={int(index)}"
    elif (selector or "").strip():
        where = f" selector={selector.strip()!r}"
    role_bit = f" role={r}" if r else ""
    clickish = t in {"a", "button", "summary", "option"} or r in {
        "link",
        "button",
        "tab",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "checkbox",
        "radio",
        "switch",
        "option",
    }
    if clickish:
        tip = (
            "This element is not a text field — use browser_click instead of "
            "browser_input/browser_type."
        )
    else:
        tip = (
            "browser_input only works on <input>, <textarea>, <select>, "
            "[contenteditable], or ARIA roles textbox/searchbox/combobox/spinbutton. "
            "Pick a fillable field from browser_observe, or use browser_click if "
            "the control is meant to be clicked."
        )
    return f"element is not editable{where}: <{t}>{role_bit}. {tip}"
