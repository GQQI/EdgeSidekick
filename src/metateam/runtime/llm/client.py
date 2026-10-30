"""LLM client and request construction."""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Callable, Iterator, Optional

from openai import OpenAI

from ..think_tags import ThinkTagSplitter, split_think_tags
from ..text_tool_calls import (
    XmlToolStream,
    merge_text_tool_calls,
    split_before_tool_markup,
)
from ...core.config import Settings
from ...core.logutil import get_logger
from ...core.repetition import (
    MAX_REPEAT_SPAN,
    fold_trailing_repeats,
    normalize_content_text,
    reasoning_hard_looped,
    reasoning_looped,
    repeated_tail_span,
)

from .parse import *  # noqa: F403
from .stream import *  # noqa: F403

_log = get_logger("metateam.llm")
THINKING_TEMPERATURE_FLOOR = 0.6
SHORT_LOOP_MIN_SPAN = 24
SHORT_LOOP_HARD_REPS = 8
SHORT_LOOP_SOFT_REPS = 4
REASONING_MIN_SPAN = 16
REPEAT_CUT_SEC = 20.0
REASONING_REPEAT_CUT_SEC = 6.0
_THINK_PART_TYPES = frozenset(
    {
        "thinking",
        "reasoning",
        "reasoning_content",
        "reasoning_text",
        "reasoning.text",
        "thought",
    }
)
_REASONING_FIELD_KEYS = (
    "reasoning_content",
    "reasoning",
    "thinking",
    "thought",
)
_REASONING_TEXT_KEYS = (
    "thinking",
    "reasoning_content",
    "reasoning",
    "thought",
    "text",
    "content",
    "summary",
)
_PARTIAL_ARG_KEYS = (
    "goal",
    "task",
    "context",
    "role",
    "topic",
    "query",
    "prompt",
    "description",
    "path",
    "command",
    "content",
    "old_string",
    "new_string",
    "offset",
    "limit",
    "pattern",
    "why",
    "how",
    "stop",
    "need",
)
def _is_tool_choice_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        "enable-auto-tool-choice" in msg
        or "tool-call-parser" in msg
        or '"auto" tool choice' in msg
        or "auto tool choice" in msg
        or ("tool_choice" in msg and ("invalid" in msg or "unsupported" in msg or "not support" in msg))
        or ("tool choice" in msg and ("invalid" in msg or "unsupported" in msg or "not support" in msg))
        or ("required" in msg and "tool" in msg and "choice" in msg)
    )

def _is_parallel_tool_calls_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    if "parallel_tool_calls" in msg or "parallel tool call" in msg:
        return True
    return "unexpected keyword" in msg and "parallel" in msg

def _retry_kwargs_for_exc(kwargs: dict[str, Any], exc: BaseException) -> dict[str, Any] | None:
    msg = str(exc).lower()
    retry = dict(kwargs)
    changed = False
    if _is_tool_choice_error(exc):
        # Prefer dropping only tool_choice so tools stay available for a soft
        # continuation. Stripping tools entirely made intent-only turns "finish"
        # with no way to call plugins/MCP.
        if "tool_choice" in retry:
            retry.pop("tool_choice", None)
            retry.pop("parallel_tool_calls", None)
            changed = True
        elif "tools" in retry:
            retry.pop("tools", None)
            retry.pop("parallel_tool_calls", None)
            changed = True
    if _is_parallel_tool_calls_error(exc):
        if "parallel_tool_calls" in retry:
            retry.pop("parallel_tool_calls", None)
            changed = True
    if "thinking" in msg or "reasoning" in msg or "extra_body" in msg:
        if "extra_body" in retry or "reasoning_effort" in retry:
            retry.pop("extra_body", None)
            retry.pop("reasoning_effort", None)
            changed = True
    if "stream_options" in msg or "include_usage" in msg:
        if "stream_options" in retry:
            retry.pop("stream_options", None)
            changed = True
    return retry if changed else None

def _strip_for_api(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in align_tool_chains(messages):
        item = {
            k: v
            for k, v in m.items()
            if k in ("role", "content", "tool_calls", "tool_call_id", "name")
        }
        calls = item.get("tool_calls")
        if isinstance(calls, list) and calls:
            item["tool_calls"] = [
                canonicalize_tool_call(tc)
                for tc in calls
                if isinstance(tc, dict)
            ]
        # Providers reject tool_calls: [] — omit the field when nothing was called.
        if not item.get("tool_calls"):
            item.pop("tool_calls", None)
        if item.get("role") == "assistant" and not item.get("content") and not item.get("tool_calls"):
            item["content"] = ""
        out.append(item)
    return out

def _demo_chat(
    messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]]
) -> dict[str, Any]:
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = str(m.get("content") or "")
            break

    if messages and messages[-1].get("role") == "tool":
        content = str(messages[-1].get("content") or "")
        if "Function skill:" in content or content.lstrip().startswith("# Function skill"):
            return {
                "role": "assistant",
                "content": (
                    "【Demo】当前是 Demo 模式，不会真正按 Skill 执行写文件/改代码。\n\n"
                    "Skill 工具只返回了流程说明；请到「设置 → 模型」配置可用的 API Key，"
                    "确认顶栏不是 Demo，然后新建会话再执行 `/skill`。"
                ),
            }
        return {
            "role": "assistant",
            "content": (
                "【Demo】工具结果已收到（Demo 模式不会继续真实执行）。\n\n"
                f"{content[:280]}"
            ),
        }

    tool_names = set()
    if tools:
        for t in tools:
            fn = t.get("function") or {}
            tool_names.add(fn.get("name"))

    low = last_user.lower()

    # Prefer skill_* function tools
    skill_tools = [n for n in tool_names if n and str(n).startswith("skill_")]
    skill_asked = (
        "skill" in low
        or "技能" in last_user
        or "/skill" in low
        or "【skill" in low
        or any(str(n) in last_user for n in skill_tools)
    )
    if skill_asked and skill_tools:
        # Match the requested skill_* name from the user text when possible.
        name = next((n for n in skill_tools if n and str(n) in last_user), None)
        if not name:
            # /skill html-deck-editorial → skill_html_deck_editorial
            m = re.search(r"/skill\s+([A-Za-z0-9._-]+)", last_user, re.I)
            if m:
                want = "skill_" + re.sub(r"[^a-z0-9]+", "_", m.group(1).lower()).strip("_")
                name = next((n for n in skill_tools if n == want or want in str(n)), None)
        if not name:
            name = (
                "skill_hello_workspace"
                if "skill_hello_workspace" in skill_tools
                else skill_tools[0]
            )
        # Keep task short — never pass the whole SKILL injection blob as task.
        task = last_user.strip()
        if "用户本次附加指令" in task:
            m = re.search(r"用户本次附加指令：\s*\n(.+?)(?:\n\n请按该 Skill|\Z)", task, re.S)
            task = (m.group(1).strip() if m else "") or "按该 Skill 的标准流程执行"
        elif len(task) > 240:
            task = task[:240]
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "demo_sk_1",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps({"task": task}, ensure_ascii=False),
                    },
                }
            ],
        }

    if "并行" in last_user or "delegate" in low:
        if "delegate_task" in tool_names:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "demo_del_1",
                        "type": "function",
                        "function": {
                            "name": "delegate_task",
                            "arguments": json.dumps(
                                {
                                    "tasks": [
                                        {"goal": "List workspace", "context": "list_dir ."},
                                        {
                                            "goal": "Use hello skill",
                                            "context": "call skill_hello_workspace",
                                        },
                                    ]
                                },
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            }

    if any(k in last_user for k in ("列出", "list", "目录", "workspace")) and "list_dir" in tool_names:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "demo_ls_1",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": json.dumps({"path": "."})},
                }
            ],
        }

    if ("写" in last_user or "write" in low or "hello" in low) and "write_file" in tool_names:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "demo_w_1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps(
                            {
                                "path": "notes/demo.md",
                                "content": "# Demo\n\nHello from EdgeSidekick.\n",
                            },
                            ensure_ascii=False,
                        ),
                    },
                }
            ],
        }

    return {
        "role": "assistant",
        "content": (
            "【Demo 模式】配置模型 API Key 后可使用真实推理。\n"
            "可试：列出 workspace / 调用 hello skill / 并行委派。"
        ),
    }

class LLM:
    def __init__(
        self,
        settings: Settings,
        model: Optional[str] = None,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ):
        self.settings = settings
        self.model = model or settings.model
        self.max_tokens = int(
            max_tokens if max_tokens is not None else getattr(settings, "max_tokens", 0) or 0
        )
        key = (api_key if api_key is not None else settings.api_key) or ""
        url = (base_url if base_url is not None else settings.base_url) or settings.base_url
        self.demo = settings.demo_mode and not key.strip()
        if key.strip():
            self.demo = False
        self.client: OpenAI | None = None
        self._active_stream: Any = None
        self._disable_tools = False
        self._disable_parallel_tool_calls = False
        if not self.demo:
            if not key.strip():
                raise RuntimeError("API key empty — configure in UI or META_DEMO_MODE=1")
            self.client = OpenAI(api_key=key, base_url=url.rstrip("/"))

    def close_active_stream(self) -> None:
        """Best-effort close of the in-flight OpenAI stream (interrupts provider read)."""
        stream = self._active_stream
        self._active_stream = None
        if stream is None:
            return
        for closer in (
            getattr(stream, "close", None),
            getattr(getattr(stream, "response", None), "close", None),
            getattr(getattr(stream, "http_response", None), "close", None),
        ):
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass

    def _call_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]],
        temperature: float,
        stream: bool = False,
        tool_choice: Optional[Any] = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": _strip_for_api(messages),
            "temperature": temperature,
            "stream": stream,
        }
        if tools and not self._disable_tools:
            kwargs["tools"] = tools
            # Do not send tool_choice="auto" by default: vLLM 400s unless the
            # server was started with --enable-auto-tool-choice --tool-call-parser.
            # Callers may still force "required" after a text-only tool-intent turn;
            # _create retries by stripping tool_choice on provider errors.
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
            if not self._disable_parallel_tool_calls:
                kwargs["parallel_tool_calls"] = True

        mt = int(getattr(self, "max_tokens", 0) or getattr(self.settings, "max_tokens", 0) or 0)
        if mt > 0:
            kwargs["max_tokens"] = mt

        # DeepSeek / reasoning models
        effort = getattr(self.settings, "reasoning_effort", None)
        if effort:
            kwargs["reasoning_effort"] = effort

        extra: dict[str, Any] = {}
        if getattr(self.settings, "thinking_enabled", False):
            extra["thinking"] = {"type": "enabled"}
        if extra:
            kwargs["extra_body"] = extra
        return kwargs

    def _effective_temperature(self, temperature: Optional[float]) -> float:
        """Temperature for a conversational turn.

        Thinking models loop when sampled almost greedily; the usable band is
        roughly 0.5–0.7. Apply a floor only when the caller did not pin a
        value — judge/summarizer calls pass 0.0–0.1 explicitly and must stay
        deterministic.
        """
        if temperature is not None:
            return float(temperature)
        temp = float(self.settings.temperature)
        if getattr(self.settings, "thinking_enabled", False):
            return max(temp, THINKING_TEMPERATURE_FLOOR)
        return temp

    def _create(self, kwargs: dict[str, Any]) -> Any:
        assert self.client is not None
        pending = dict(kwargs)
        _, stream_cap = _stream_timeouts(self.settings)
        if stream_cap > 0 and "timeout" not in pending:
            pending["timeout"] = stream_cap
        last: BaseException | None = None
        for _ in range(4):
            try:
                return self.client.chat.completions.create(**pending)
            except TypeError as exc:
                last = exc
                pending.pop("reasoning_effort", None)
                pending.pop("stream_options", None)
                pending.pop("timeout", None)
                if "parallel_tool_calls" in pending and _is_parallel_tool_calls_error(exc):
                    pending.pop("parallel_tool_calls", None)
                    self._disable_parallel_tool_calls = True
                continue
            except Exception as exc:
                last = exc
                retry = _retry_kwargs_for_exc(pending, exc)
                if retry is None:
                    raise
                if _is_tool_choice_error(exc):
                    # Only permanently disable tools when tool_choice was already
                    # stripped and the provider still rejects the tools payload.
                    if "tool_choice" not in pending:
                        self._disable_tools = True
                if _is_parallel_tool_calls_error(exc):
                    self._disable_parallel_tool_calls = True
                pending = retry
        assert last is not None
        raise last

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        tool_choice: Optional[Any] = None,
        fold_repeats: bool = False,
    ) -> dict[str, Any]:
        if self.demo:
            return _demo_chat(messages, tools)

        assert self.client is not None
        temp = self._effective_temperature(temperature)
        kwargs = self._call_kwargs(
            messages, tools, temp, stream=False, tool_choice=tool_choice
        )
        # create() doesn't take stream=False as needed if we pop it
        kwargs.pop("stream", None)
        resp = self._create(kwargs)

        msg = resp.choices[0].message
        content = extract_content_text(msg)
        reasoning = extract_reasoning_text(msg)
        content, tagged = split_think_tags(content)
        # Native reasoning already present → do not append tagged duplicates
        if tagged and not reasoning:
            reasoning = tagged

        looped = False
        if fold_repeats:
            # Non-streaming fallback: the provider can return a whole tape in
            # one message. Fold it before it reaches history, and flag it so
            # the agent asks for the next tool instead of trusting the draft.
            content, content_folded = collapse_streaming_repeats(content)
            reasoning, reasoning_folded = collapse_streaming_repeats(
                reasoning, min_span=REASONING_MIN_SPAN, max_keep=1
            )
            looped = content_folded or reasoning_folded

        out: dict[str, Any] = {"role": "assistant", "content": content}
        if looped:
            out["sidekick_looped"] = True
        if reasoning:
            out["reasoning"] = reasoning
        if msg.tool_calls:
            out["tool_calls"] = [
                canonicalize_tool_call(
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": coerce_tool_arg_piece(tc.function.arguments)
                            or "{}",
                        },
                    }
                )
                for tc in msg.tool_calls
            ]
        return merge_text_tool_calls(out)

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        cancel_check: Optional[Any] = None,
        tool_choice: Optional[Any] = None,
    ) -> Iterator[tuple[str, Any]]:
        """Yield ("delta", text) then a final ("done", assistant_message)."""
        if self.demo:
            msg = _demo_chat(messages, tools)
            text = msg.get("content") or ""
            for i in range(0, len(text), 12):
                if callable(cancel_check) and cancel_check():
                    break
                yield ("delta", text[i : i + 12])
            yield ("done", msg)
            return

        assert self.client is not None
        temp = self._effective_temperature(temperature)
        kwargs = self._call_kwargs(
            messages, tools, temp, stream=True, tool_choice=tool_choice
        )
        stream = self._create(kwargs)

        self._active_stream = stream
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_acc: dict[int, dict[str, str]] = {}
        id_to_idx: dict[str, int] = {}
        cancelled = False
        looped_text = False
        # Visible prose is cut on the long-span rules immediately, and on
        # short-sentence tapes only after the grace period (four identical
        # short lines can be real output; eight are not).
        repeat_cut = RepeatCut(
            looped=lambda t: streaming_text_looped(t) or streaming_text_short_looped(t)
        )
        # Reasoning is internal text: shorter repeats are folded and cut
        # earlier than visible prose, which can legitimately contain lists.
        reasoning_cut = RepeatCut(
            seconds=REASONING_REPEAT_CUT_SEC,
            looped=reasoning_looped,
            hard=reasoning_hard_looped,
        )
        # Folded buffers feed the UI/history; the raw tails feed the cutters.
        # Checking the folded text made exact loops invisible (one copy left),
        # so a stuck provider kept generating until max_tokens / the 180s cap.
        reasoning_raw = _RawTail()
        content_raw = _RawTail()
        reasoning_since_check = 0
        content_since_check = 0
        reasoning_emitted: list[str] = [""]
        content_emitted: list[str] = [""]
        xml_hold = ""
        xml_tools = XmlToolStream()
        # Stateful splitter for <think>…</think> embedded in content stream
        tag_split = ThinkTagSplitter()
        idle_sec, max_sec = _stream_timeouts(self.settings)
        watch = StreamWatchdog(
            self.close_active_stream, idle_sec=idle_sec, max_sec=max_sec
        )
        watch.start()

        try:
            for chunk in stream:
                watch.bump()
                if callable(cancel_check) and cancel_check():
                    cancelled = True
                    break
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = getattr(choice, "delta", None)

                # 1) Native reasoning_content / reasoning / thinking blocks.
                # Some gateways put the increment on the choice, not the delta.
                reasoning_piece = extract_reasoning_text(delta)
                if not reasoning_piece:
                    reasoning_piece = extract_reasoning_text(
                        _choice_reasoning_extras(choice)
                    )
                if reasoning_piece:
                    delta_out = _absorb_reasoning_piece(
                        reasoning_parts,
                        reasoning_piece,
                        emitted=reasoning_emitted,
                    )
                    if delta_out:
                        yield ("reasoning_delta", delta_out)
                    reasoning_raw.push(reasoning_piece)
                    reasoning_since_check += len(reasoning_piece)
                    # Check on growth, not on every token: the detector scans a
                    # bounded tail and a loop only needs to be caught within a
                    # few dozen characters of its third copy.
                    if reasoning_since_check >= 24:
                        reasoning_since_check = 0
                        if reasoning_cut.observe(reasoning_raw.text):
                            looped_text = True
                            try:
                                self.close_active_stream()
                            except Exception:
                                pass

                if not delta:
                    if looped_text:
                        break
                    continue

                # 2) Visible content, with <think> tags peeled into reasoning.
                # If this delta already carried native reasoning, only emit
                # visible content — do not also treat tagged content as
                # reasoning (avoids NowNow / let let duplication).
                content_piece = extract_content_text(delta)
                if content_piece:
                    for kind, piece in tag_split.feed(content_piece):
                        if not piece:
                            continue
                        if kind == "reasoning":
                            if reasoning_piece:
                                continue
                            delta_out = _absorb_reasoning_piece(
                                reasoning_parts,
                                piece,
                                emitted=reasoning_emitted,
                            )
                            if delta_out:
                                yield ("reasoning_delta", delta_out)
                            reasoning_raw.push(piece)
                            reasoning_since_check += len(piece)
                            if reasoning_since_check >= 24:
                                reasoning_since_check = 0
                                if reasoning_cut.observe(reasoning_raw.text):
                                    looped_text = True
                                    try:
                                        self.close_active_stream()
                                    except Exception:
                                        pass
                                    break
                        else:
                            visible, xml_hold = split_before_tool_markup(xml_hold + piece)
                            if visible:
                                delta_out = _absorb_stream_piece(
                                    content_parts,
                                    visible,
                                    emitted=content_emitted,
                                )
                                if delta_out:
                                    yield ("delta", delta_out)
                                content_raw.push(visible)
                                content_since_check += len(visible)
                                if content_since_check >= 24:
                                    content_since_check = 0
                                    if repeat_cut.observe(content_raw.text):
                                        looped_text = True
                                        try:
                                            self.close_active_stream()
                                        except Exception:
                                            pass
                                        break
                            if not tool_acc:
                                for payload in xml_tools.feed(xml_hold):
                                    yield ("tool_delta", payload)
                    if looped_text:
                        break

                if looped_text:
                    break

                for tc in getattr(delta, "tool_calls", None) or []:
                    idx = _tool_call_slot_index(tc, tool_acc, id_to_idx)
                    slot = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    changed = False
                    if getattr(tc, "id", None):
                        slot["id"] = tc.id
                        id_to_idx[str(tc.id)] = idx
                        changed = True
                    fn = getattr(tc, "function", None)
                    arg_piece = ""
                    if fn is not None:
                        if getattr(fn, "name", None):
                            slot["name"] = merge_stream_fragment(
                                slot["name"] or "", str(fn.name)
                            )
                            changed = True
                        arg_piece = coerce_tool_arg_piece(getattr(fn, "arguments", None))
                        if arg_piece:
                            slot["arguments"] = merge_stream_fragment(
                                slot["arguments"] or "", arg_piece, json_payload=True
                            )
                            # Hold the stream open until tool JSON balances —
                            # DeepSeek often writes goal/context before tasks[].
                            acc_args = slot.get("arguments") or ""
                            balanced, bal_reason = decode_tool_args_json(acc_args)
                            args_ok = (
                                bal_reason in {"ok", "control_chars", "concatenated"}
                                and isinstance(balanced, dict)
                            )
                            if args_ok:
                                watch.set_tool_args_open(False)
                            else:
                                watch.grant_tool_arg_grace()
                            changed = True
                    if changed:
                        yield (
                            "tool_delta",
                            {
                                "index": idx,
                                "id": slot.get("id") or "",
                                "name": slot.get("name") or "",
                                "arguments_delta": arg_piece,
                                "arguments": slot.get("arguments") or "",
                            },
                        )
        except Exception:
            # Closing our own stream after a long repeat raises inside the
            # provider iterator. That is not a failed turn.
            if looped_text:
                pass
            elif not watch.reason and not cancelled and not (
                callable(cancel_check) and cancel_check()
            ):
                watch.stop()
                raise
        finally:
            watch.stop()
            if self._active_stream is stream:
                self._active_stream = None
            for closer in (
                getattr(stream, "close", None),
                getattr(getattr(stream, "response", None), "close", None),
                getattr(getattr(stream, "http_response", None), "close", None),
            ):
                if callable(closer):
                    try:
                        closer()
                    except Exception:
                        pass

        for kind, piece in tag_split.flush():
            if not piece:
                continue
            if kind == "reasoning":
                delta_out = _absorb_reasoning_piece(
                    reasoning_parts, piece, emitted=reasoning_emitted
                )
                if delta_out:
                    yield ("reasoning_delta", delta_out)
            else:
                visible, xml_hold = split_before_tool_markup(xml_hold + piece)
                if visible:
                    content_parts.append(visible)
                    yield ("delta", visible)

        if not tool_acc:
            for payload in xml_tools.feed(xml_hold):
                yield ("tool_delta", payload)

        content = "".join(content_parts) + xml_hold
        reasoning = "".join(reasoning_parts)
        reasoning, _ = collapse_streaming_repeats(
            reasoning, min_span=REASONING_MIN_SPAN, max_keep=1
        )
        content, _ = collapse_streaming_repeats(content)
        if looped_text:
            # Keep a short prefix. The repeated tail must not re-enter history
            # or the next turn copies it.
            if len(reasoning) > 480:
                reasoning = reasoning[:480].rstrip() + "…"
            if streaming_text_looped(content) or streaming_text_hard_looped(content):
                content = content[:480].rstrip()
        out: dict[str, Any] = {"role": "assistant", "content": content}
        if looped_text:
            out["sidekick_looped"] = True
        if reasoning:
            out["reasoning"] = reasoning
        if tool_acc and not cancelled and not looped_text:
            out["tool_calls"] = [
                canonicalize_tool_call(
                    {
                        "id": tool_acc[i].get("id") or f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": tool_acc[i].get("name") or "",
                            "arguments": tool_acc[i].get("arguments") or "{}",
                        },
                    }
                )
                for i in sorted(tool_acc)
                if tool_acc[i].get("name")
            ]
        if watch.reason and not cancelled:
            yield (
                "stalled",
                {
                    "reason": watch.reason,
                    "idle_sec": idle_sec,
                    "max_sec": max_sec,
                },
            )
        yield ("done", merge_text_tool_calls(out, id_prefix=xml_tools.prefix))

    def stream_text(
        self,
        messages: list[dict[str, Any]],
        temperature: Optional[float] = None,
        cancel_check: Optional[Any] = None,
    ) -> Iterator[str]:
        for kind, payload in self.stream_chat(
            messages, tools=None, temperature=temperature, cancel_check=cancel_check
        ):
            if kind == "delta":
                yield str(payload)

    def complete_text(self, system: str, user: str, temperature: float = 0.1) -> str:
        msg = self.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            tools=None,
            temperature=temperature,
        )
        return (msg.get("content") or "").strip()

