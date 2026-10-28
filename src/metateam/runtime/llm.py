"""OpenAI-compatible client (DeepSeek-ready) + demo stub."""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Callable, Iterator, Optional

from openai import OpenAI

from .think_tags import ThinkTagSplitter, split_think_tags
from .text_tool_calls import (
    XmlToolStream,
    merge_text_tool_calls,
    split_before_tool_markup,
)
from ..core.config import Settings
from ..core.logutil import get_logger
from ..core.repetition import (
    MAX_REPEAT_SPAN,
    fold_trailing_repeats,
    normalize_content_text,
    reasoning_hard_looped,
    reasoning_looped,
    repeated_tail_span,
)

_log = get_logger("metateam.llm")

# Thinking models repeat when sampled almost greedily. The usable band is
# roughly 0.5–0.7; this floor only applies to conversational turns that did not
# pin a temperature (judge/summarizer calls pin 0.0–0.1 on purpose).
THINKING_TEMPERATURE_FLOOR = 0.6

# Visible prose used an 80-char floor, so a Chinese sentence (often 15–30
# chars) could repeat until the hard stream cap. Detect short tapes on
# *content* (punctuation and line wraps removed) with a high copy count:
# 8 copies is unmistakable, 4 copies only arms the soft timer.
SHORT_LOOP_MIN_SPAN = 24
SHORT_LOOP_HARD_REPS = 8
SHORT_LOOP_SOFT_REPS = 4


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


def _stream_timeouts(settings: Any) -> tuple[float, float]:
    idle = float(getattr(settings, "llm_idle_timeout", 0) or 0)
    total = float(getattr(settings, "llm_stream_timeout", 0) or 0)
    # Reasoning / thinking models often emit nothing for a long time before the
    # first token. Under concurrent social turns (group + DM) the provider can
    # also queue, so a short idle watchdog falsely surfaces as 思考超时.
    thinking = bool(getattr(settings, "thinking_enabled", False))
    effort = getattr(settings, "reasoning_effort", None)
    if idle > 0 and (thinking or effort):
        idle = max(idle, 180.0)
    return max(0.0, idle), max(0.0, total)


class StreamWatchdog:
    """Close a hung provider stream when idle or over a hard duration."""

    def __init__(
        self,
        close_fn: Callable[[], None],
        *,
        idle_sec: float,
        max_sec: float,
    ) -> None:
        self._close = close_fn
        self.idle_sec = max(0.0, float(idle_sec or 0))
        self.max_sec = max(0.0, float(max_sec or 0))
        now = time.monotonic()
        self._last = now
        self._start = now
        self._stop = threading.Event()
        self.reason = ""
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.idle_sec <= 0 and self.max_sec <= 0:
            return
        self._thread = threading.Thread(
            target=self._run, name="llm-stall-watch", daemon=True
        )
        self._thread.start()

    def bump(self) -> None:
        self._last = time.monotonic()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and threading.current_thread() is not t:
            t.join(timeout=1.0)

    def _fire(self, reason: str) -> None:
        if self.reason or self._stop.is_set():
            return
        self.reason = reason
        try:
            self._close()
        except Exception:
            pass

    def _run(self) -> None:
        while not self._stop.wait(0.4):
            now = time.monotonic()
            if self.idle_sec > 0 and now - self._last >= self.idle_sec:
                self._fire("idle")
                return
            if self.max_sec > 0 and now - self._start >= self.max_sec:
                self._fire("max")
                return


def coerce_tool_arg_piece(value: Any) -> str:
    """Normalize a streamed tool-argument fragment to text.

    Some gateways send ``arguments`` as a dict (already parsed) instead of a
    JSON string. ``str(dict)`` yields Python repr with single quotes, which is
    not JSON and will 400 on the next request.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def merge_stream_fragment(prev: str, piece: str) -> str:
    """Join one streamed tool name/arguments chunk.

    OpenAI-style streams append *deltas*. MiniMax / some vLLM parsers instead
    resend a growing *snapshot* of the whole JSON each chunk. Concatenating
    snapshots produces ``{"path": ...{"path": ...`` which is not JSON — and
    that illegal string is what the next chat.completions call POSTs back,
    causing ``Unterminated string`` / 400.
    """
    if not piece:
        return prev or ""
    if not prev:
        return piece
    if piece == prev:
        return prev
    if piece.startswith(prev):
        return piece
    # A later, shorter snapshot repeats the opening of the accumulator.
    # A one-character "{" delta is also a prefix of every JSON object, and
    # CSS/JS bodies stream that token alone. Dropping it deletes the brace
    # and leaves "*margin:0}" / ".shelldisplay:grid}".
    if piece != "{" and prev.startswith(piece):
        return prev
    prev_obj = _try_json_object(prev)
    piece_obj = _try_json_object(piece)
    if isinstance(prev_obj, dict) and isinstance(piece_obj, dict):
        # Two complete snapshots of the same call — keep the longer one.
        return piece if len(piece) >= len(prev) else prev
    return prev + piece


def _try_json_object(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text, strict=False)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _tool_call_slot_index(
    tc: Any,
    tool_acc: dict[int, dict[str, str]],
    id_to_idx: dict[str, int],
) -> int:
    raw_idx = getattr(tc, "index", None)
    if raw_idx is not None and str(raw_idx) != "":
        try:
            idx = int(raw_idx)
        except (TypeError, ValueError):
            idx = None
        else:
            cid = getattr(tc, "id", None)
            if cid:
                id_to_idx[str(cid)] = idx
            return idx
    cid = str(getattr(tc, "id", None) or "")
    if cid and cid in id_to_idx:
        return id_to_idx[cid]
    # Argument fragments often omit index and id. A new slot would keep the
    # name on the previous call and drop the arguments (executed as {}).
    if tool_acc and not cid:
        return max(tool_acc)
    idx = (max(tool_acc) + 1) if tool_acc else 0
    if cid:
        id_to_idx[cid] = idx
    return idx


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
                                slot["arguments"] or "", arg_piece
                            )
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




def merge_reasoning_fragment(prev: str, piece: str) -> str:
    """Join one streamed reasoning/content chunk.

    Growing *snapshots* (piece strictly extends prev) replace. An identical
    piece is treated as another delta copy and appended so loop detectors can
    see consecutive repeats. A mere prefix of prev is also appended — unlike
    tool-arg snapshot merging — because prose loops often resend the same
    paragraph.
    """
    if not piece:
        return prev or ""
    if not prev:
        return piece
    if len(piece) > len(prev) and piece.startswith(prev):
        return piece
    return prev + piece


REASONING_MIN_SPAN = 16


def _absorb_stream_piece(
    parts: list[str],
    piece: str,
    *,
    emitted: list[str],
    min_span: int = 80,
    max_keep: int = 2,
) -> str:
    """Merge one stream piece into ``parts``; return only the new visible delta.

    Handles providers that resend a growing *snapshot* (like MiniMax tool
    args) by using :func:`merge_reasoning_fragment`, then collapses consecutive
    identical spans so the UI never accumulates a tape-recorder loop.
    """
    if not piece:
        return ""
    prev = "".join(parts)
    merged = merge_reasoning_fragment(prev, piece)
    cleaned, _collapsed = collapse_streaming_repeats(
        merged, min_span=min_span, max_keep=max_keep
    )
    parts[:] = [cleaned] if cleaned else []
    already = emitted[0] if emitted else ""
    if cleaned == already:
        return ""
    if cleaned.startswith(already):
        delta = cleaned[len(already) :]
        emitted[:] = [cleaned]
        return delta
    # Truncated or rewritten (collapse removed a trailing repeat the UI may
    # already have shown). Do not re-append; the agent loop resets the bubble
    # when sidekick_looped fires.
    emitted[:] = [cleaned]
    return ""


def _absorb_reasoning_piece(
    parts: list[str],
    piece: str,
    *,
    emitted: list[str],
) -> str:
    """Reasoning variant: fold short repeated paragraphs, keep one copy.

    Thinking is internal, so a 20-char sentence repeated twice is far more
    likely to be a loop than legitimate prose. Visibility is also stricter:
    the bubble should show the idea once, not the provider's retries.
    """
    return _absorb_stream_piece(
        parts,
        piece,
        emitted=emitted,
        min_span=REASONING_MIN_SPAN,
        max_keep=1,
    )


def fold_reasoning_text(text: str) -> str:
    """Fold a finished reasoning block (non-stream responses, fallbacks)."""
    body = str(text or "").strip()
    if not body:
        return ""
    folded, _ = collapse_streaming_repeats(
        body, min_span=REASONING_MIN_SPAN, max_keep=1
    )
    return folded.strip()


REPEAT_CUT_SEC = 20.0
# Thinking is internal and must not keep streaming after it starts repeating.
# A two-copy repeat is already a stuck model there, so the soft grace is short.
REASONING_REPEAT_CUT_SEC = 6.0


class _RawTail:
    """Raw streamed text used for loop detection.

    The display buffers are *folded* (repeated spans are removed before they
    reach the UI/history). Detecting a loop on the folded buffer is therefore
    impossible: it never contains the second or third copy. This keeps an
    unfold raw tail beside them, capped to a small window, and also understands
    providers that re-send a growing snapshot instead of deltas.
    """

    def __init__(self, window: int = 8000) -> None:
        self.window = int(window)
        self.text = ""
        self.total = 0
        self._head = ""

    def push(self, piece: str) -> str:
        if not piece:
            return self.text
        if self.total and len(piece) > self.total and piece.startswith(self._head):
            # Growing snapshot: the piece already contains everything.
            self.text = piece[-self.window :]
            self.total = len(piece)
            self._head = piece[:512]
            return self.text
        self.total += len(piece)
        if len(self._head) < 512:
            self._head = (self._head + piece)[:512]
        self.text = (self.text + piece)[-self.window :]
        return self.text


def _trailing_unit_repeats(blob: str, unit_len: int) -> int:
    """How many identical copies of blob[-unit_len:] sit at the end."""
    if unit_len <= 0 or len(blob) < unit_len:
        return 0
    unit = blob[-unit_len:]
    if not unit.strip():
        return 0
    reps = 1
    while len(blob) >= unit_len * (reps + 1):
        end = -unit_len * reps
        start = -unit_len * (reps + 1)
        chunk = blob[start:end] if end != 0 else blob[start:]
        if chunk != unit:
            break
        reps += 1
    return reps


def streaming_text_short_looped(
    text: str,
    *,
    min_span: int = SHORT_LOOP_MIN_SPAN,
    min_reps: int = SHORT_LOOP_SOFT_REPS,
) -> bool:
    """Short-sentence tape signal for visible prose.

    Uses content (letters/digits only) so a re-wrapped or re-punctuated copy
    still matches. This is the *soft* probe: the caller must wait before
    cutting, because four identical short lines can be legitimate output.
    """
    return (
        repeated_tail_span(
            normalize_content_text(text), min_span=min_span, min_reps=min_reps
        )
        > 0
    )


def streaming_text_hard_looped(
    text: str, *, min_span: int = 80, min_reps: int = 3
) -> bool:
    """True when the same ~80+ char span repeats consecutively at the end."""
    blob = text or ""
    if len(blob) >= SHORT_LOOP_MIN_SPAN * SHORT_LOOP_HARD_REPS and (
        streaming_text_short_looped(
            blob, min_reps=SHORT_LOOP_HARD_REPS
        )
    ):
        return True
    if len(blob) < min_span * min_reps:
        return False
    max_unit = min(MAX_REPEAT_SPAN, len(blob) // min_reps)
    for unit_len in range(min_span, max_unit + 1):
        if _trailing_unit_repeats(blob, unit_len) >= min_reps:
            return True
    return False


def collapse_streaming_repeats(
    text: str, *, min_span: int = 80, max_keep: int = 2
) -> tuple[str, bool]:
    """Keep at most ``max_keep`` trailing copies of a repeated span.

    Returns ``(cleaned_text, did_collapse)``. Used so the UI / history never
    store endless repeated paragraphs even if the provider keeps sending them.
    Comparison ignores whitespace, so a re-wrapped paragraph is still a repeat.
    """
    cleaned, folded = fold_trailing_repeats(
        text, min_span=min_span, max_keep=max_keep, max_span=MAX_REPEAT_SPAN
    )
    # A Chinese loop may never reach the 80-char floor. Fold short spans only
    # once the tail is already an unmistakable tape (8 copies), so legitimate
    # short repeated lines still survive.
    if (
        min_span > SHORT_LOOP_MIN_SPAN
        # Bound the scan: this runs on every streamed piece, so normalizing a
        # whole long answer each time would be quadratic.
        and streaming_text_short_looped(cleaned[-6000:], min_reps=SHORT_LOOP_HARD_REPS)
    ):
        short, did_short = fold_trailing_repeats(
            cleaned,
            min_span=SHORT_LOOP_MIN_SPAN,
            max_keep=max_keep,
            max_span=MAX_REPEAT_SPAN,
        )
        cleaned = short
        folded = folded or did_short
    return cleaned, folded


class RepeatCut:
    """Cut a generation after similar text keeps repeating.

    Hard consecutive identity (same 80+ char span x3 for visible prose) aborts
    immediately. Softer "looks stuck" patterns still wait a short while so a
    brief echo does not end the task. ``looped``/``hard`` let the reasoning
    path use a shorter-span probe without changing visible-content rules.
    """

    def __init__(
        self,
        *,
        seconds: float = REPEAT_CUT_SEC,
        now=time.monotonic,
        looped: Optional[Callable[[str], bool]] = None,
        hard: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self.seconds = float(seconds)
        self._now = now
        # Reasoning passes a more sensitive probe: internal text loops are
        # shorter than visible prose and should not keep streaming.
        self._looped = looped or streaming_text_looped
        self._hard = hard or streaming_text_hard_looped
        self._since: float | None = None

    def observe(self, text: str) -> bool:
        if self._hard(text):
            return True
        if not self._looped(text):
            self._since = None
            return False
        now = float(self._now())
        if self._since is None:
            self._since = now
            return False
        return (now - self._since) >= self.seconds


def streaming_text_looped(text: str) -> bool:
    """True when the model is stuck repeating the same sentence/paragraph."""
    blob = text or ""
    if streaming_text_hard_looped(blob):
        return True
    if len(blob) < 240:
        return False
    # Scale the window with the candidate span so a ~300-char English
    # paragraph that repeats still trips the detector (a fixed 960/96
    # window only fits ~3 copies and never reached the old >=4 threshold).
    for span in (80, 96, 120, 160, 240, 320):
        if len(blob) < span * 3:
            continue
        tail = blob[-span:]
        if not tail.strip():
            continue
        window = blob[-(span * 4) :] if len(blob) > span * 4 else blob
        if window.count(tail) >= 3:
            return True
    # A sentence glued end to end does not share a fixed tail, so count()
    # on that tail stays at 1. Compare the recent sentences themselves.
    # Include ASCII "." — English reasoning ends with periods.
    recent = blob[-1600:]
    parts = [
        p.strip()
        for p in re.split(r"[。！？.!?\n]+", recent)
        if len(p.strip()) >= 12
    ]
    if len(parts) >= 6 and sum(1 for p in parts[-8:] if p == parts[-1]) >= 3:
        return True
    lines = [ln.strip() for ln in blob.splitlines() if len(ln.strip()) >= 16]
    if len(lines) >= 4 and lines[-1] == lines[-2] == lines[-3]:
        return True
    paras = [p.strip() for p in re.split(r"\n\s*\n", blob) if len(p.strip()) >= 40]
    if len(paras) >= 3 and paras[-1] == paras[-2] == paras[-3]:
        return True
    return False


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


def _as_mapping(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    out: dict[str, Any] = {}
    for name in ("model_dump", "to_dict"):
        fn = getattr(obj, name, None)
        if not callable(fn):
            continue
        try:
            dumped = fn(exclude_none=True) if name == "model_dump" else fn()
        except TypeError:
            try:
                dumped = fn()
            except Exception:
                dumped = None
        except Exception:
            dumped = None
        if isinstance(dumped, dict):
            out.update(dumped)
            break
    extra = getattr(obj, "model_extra", None)
    if extra is None:
        extra = getattr(obj, "__pydantic_extra__", None)
    if isinstance(extra, dict):
        out.update(extra)
    return out


def _choice_reasoning_extras(choice: Any) -> dict[str, Any]:
    """Choice-level extras only — never re-read nested delta/message."""
    data = _as_mapping(choice)
    data.pop("delta", None)
    data.pop("message", None)
    data.pop("logprobs", None)
    return data


def _is_thinking_part(part: Any) -> bool:
    if isinstance(part, dict):
        if part.get("thought") is True:
            return True
        return str(part.get("type") or "").lower() in _THINK_PART_TYPES
    if getattr(part, "thought", None) is True:
        return True
    return str(getattr(part, "type", "") or "").lower() in _THINK_PART_TYPES


def _part_visible_text(part: Any) -> str:
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        text = part.get("text")
        return text if isinstance(text, str) else ""
    text = getattr(part, "text", None)
    return text if isinstance(text, str) else ""


def _coerce_reasoning_text(value: Any, *, _depth: int = 0) -> str:
    """Turn provider-specific reasoning payloads into a plain string."""
    if value is None or _depth > 4:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool, bytes)):
        return ""
    if isinstance(value, list):
        return "".join(_coerce_reasoning_text(item, _depth=_depth + 1) for item in value)
    if isinstance(value, dict):
        ptype = str(value.get("type") or "").lower()
        if ptype in {"tool_use", "tool_call", "function_call", "reasoning.encrypted"}:
            return ""
        for key in _REASONING_TEXT_KEYS:
            if key not in value:
                continue
            text = _coerce_reasoning_text(value.get(key), _depth=_depth + 1)
            if text:
                return text
        return ""
    for key in _REASONING_TEXT_KEYS:
        if not hasattr(value, key):
            continue
        text = _coerce_reasoning_text(getattr(value, key, None), _depth=_depth + 1)
        if text:
            return text
    return _coerce_reasoning_text(_as_mapping(value), _depth=_depth + 1)


def _reasoning_from_content_parts(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if not _is_thinking_part(part):
            continue
        text = _coerce_reasoning_text(part)
        if text:
            parts.append(text)
    return "".join(parts)


def _is_thought_flag(msg: Any) -> bool:
    if msg is None:
        return False
    if isinstance(msg, dict):
        return msg.get("thought") is True
    return getattr(msg, "thought", None) is True


def extract_content_text(msg: Any) -> str:
    """Visible assistant content only (never reasoning/thinking)."""
    if msg is None:
        return ""
    # Gemini-style: the whole content increment is a thought.
    if _is_thought_flag(msg):
        return ""
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    if isinstance(content, str) and content:
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
                continue
            if _is_thinking_part(p):
                continue
            text = _part_visible_text(p)
            if text:
                parts.append(text)
        return "".join(parts)
    return ""


def extract_reasoning_text(msg: Any) -> str:
    """Model thinking / reasoning stream pieces only.

    Providers scatter the same increment across many shapes:
    string fields (reasoning_content), objects ({thinking: ...}),
    content-array parts (Claude type=thinking), or choice-level extras.
    Take the first non-empty source only — never concatenate duplicates.
    """
    if msg is None:
        return ""

    data = _as_mapping(msg)
    for key in _REASONING_FIELD_KEYS:
        raw = getattr(msg, key, None) if not isinstance(msg, dict) else None
        if raw is None:
            raw = data.get(key)
        text = _coerce_reasoning_text(raw)
        if text:
            return text

    details = getattr(msg, "reasoning_details", None) if not isinstance(msg, dict) else None
    if details is None:
        details = data.get("reasoning_details")
    text = _coerce_reasoning_text(details)
    if text:
        return text

    content = getattr(msg, "content", None) if not isinstance(msg, dict) else None
    if content is None:
        content = data.get("content")
    if _is_thought_flag(msg) and isinstance(content, str) and content:
        return content
    return _reasoning_from_content_parts(content)


def extract_message_text(msg: Any) -> str:
    """Prefer visible content; do not fall back to reasoning (kept for compat)."""
    content = extract_content_text(msg)
    if content.strip():
        visible, _ = split_think_tags(content)
        return visible
    return ""


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
)


def _unescape_json_fragment(text: str) -> str:
    return (
        (text or "")
        .replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def _closed_string_fields(raw: str) -> dict[str, str]:
    """Copy only fully closed JSON string fields — never invent a closer."""
    out: dict[str, str] = {}
    for key in _PARTIAL_ARG_KEYS:
        closed = re.search(
            rf'"{key}"\s*:\s*"((?:\\.|[^"\\])*)"',
            raw or "",
        )
        if not closed:
            continue
        body = closed.group(1)
        try:
            out[key] = json.loads(f'"{body}"', strict=False)
        except json.JSONDecodeError:
            out[key] = _unescape_json_fragment(body)
    return out


def decode_tool_args_json(raw: Any) -> tuple[dict[str, Any] | None, str]:
    """Parse tool ``arguments`` without fabricating closers.

    Returns ``(object, reason)``. ``reason`` is one of:
    ``ok``, ``control_chars`` (raw newlines/tabs inside strings — models do
    this for write_file content), ``concatenated`` (two complete objects
    glued by snapshot+delta mix-up), ``truncated``, ``invalid``.
    """
    if isinstance(raw, dict):
        return dict(raw), "ok"
    if raw is None:
        return {}, "ok"
    if isinstance(raw, (list, int, float, bool)):
        return None, "invalid"
    text = str(raw).strip()
    if not text:
        return {}, "ok"
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data, "ok"
        return None, "invalid"
    except json.JSONDecodeError:
        pass
    try:
        data = json.loads(text, strict=False)
        if isinstance(data, dict):
            return data, "control_chars"
        return None, "invalid"
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder(strict=False)
    found: list[dict[str, Any]] = []
    idx = 0
    n = len(text)
    while idx < n:
        while idx < n and text[idx] in " \t\r\n":
            idx += 1
        if idx >= n:
            break
        try:
            data, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            break
        if end <= idx:
            break
        if isinstance(data, dict):
            found.append(data)
        idx = end
    if found and idx >= n:
        return found[-1], "concatenated" if len(found) > 1 else "ok"
    if found:
        return None, "truncated"
    return None, "truncated"


def canonical_tool_arguments(raw: Any) -> str:
    """Wire-format tool arguments: always a JSON object string.

    Gateways (vLLM / MiniMax / OpenAI-compatible) ``json.loads`` this field
    on the *next* request. An unterminated write_file ``content`` string is
    exactly ``Unterminated string starting at: line 1 column 60``.
    """
    if isinstance(raw, str):
        text = raw.strip()
        if text:
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    return text
            except json.JSONDecodeError:
                pass
        else:
            return "{}"
    elif raw is None:
        return "{}"
    obj, reason = decode_tool_args_json(raw)
    if reason in {"ok", "control_chars", "concatenated"} and isinstance(obj, dict):
        return json.dumps(obj, ensure_ascii=False)
    text = raw if isinstance(raw, str) else ""
    placeholder: dict[str, Any] = {"_incomplete": True}
    for key, value in _closed_string_fields(text).items():
        if key in {"path", "command", "goal"}:
            placeholder[key] = value
    _log.warning(
        "tool arguments are not valid JSON (%s, %d chars, prefix=%r); "
        "sending a placeholder instead of the raw fragment",
        reason,
        len(text),
        text[:96],
    )
    return json.dumps(placeholder, ensure_ascii=False)


def canonicalize_tool_call(tc: dict[str, Any]) -> dict[str, Any]:
    fn_in = tc.get("function") if isinstance(tc.get("function"), dict) else {}
    fn = {
        "name": str((fn_in or {}).get("name") or ""),
        "arguments": canonical_tool_arguments((fn_in or {}).get("arguments")),
    }
    out = {k: v for k, v in tc.items() if k != "function"}
    out["type"] = out.get("type") or "function"
    out["function"] = fn
    return out


def _loads_partial_object(raw: str) -> dict[str, Any] | None:
    """Best-effort field extract for *error reporting only*.

    Do not use this to decide a write_file payload is complete — appending
    ``"}`` would hide truncation and still leave illegal JSON in history.
    """
    text = (raw or "").strip()
    if not text:
        return None
    obj, reason = decode_tool_args_json(text)
    if reason in {"ok", "control_chars", "concatenated"} and isinstance(obj, dict):
        return obj
    return None


def _salvage_tool_args(raw: str) -> dict[str, Any]:
    repaired = _loads_partial_object(raw)
    if repaired:
        return repaired
    out: dict[str, Any] = {}
    for key in _PARTIAL_ARG_KEYS:
        closed = re.search(
            rf'"{key}"\s*:\s*"((?:\\.|[^"\\])*)"',
            raw or "",
        )
        if closed:
            out[key] = _unescape_json_fragment(closed.group(1))
            continue
        opened = re.search(rf'"{key}"\s*:\s*"(.*)$', raw or "", re.DOTALL)
        if opened:
            out[key] = _unescape_json_fragment(opened.group(1).rstrip('"'))
    return out if out else {"_raw": raw}


def parse_tool_args(raw: Any) -> dict[str, Any]:
    obj, reason = decode_tool_args_json(raw)
    if reason in {"ok", "control_chars", "concatenated"} and isinstance(obj, dict):
        return obj
    text = raw if isinstance(raw, str) else str(raw or "")
    salvaged = dict(_salvage_tool_args(text) or {})
    salvaged["_incomplete"] = True
    if not any(k != "_incomplete" for k in salvaged):
        salvaged["_raw"] = text
    return salvaged


def align_tool_chains(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop tool results the provider would reject.

    A ``tool`` message is only valid as the reply to the assistant message that
    issued its id. A compacted transcript can append an older result again, or
    repeat a whole assistant/tool span. Either one makes the next request 400
    with "tool must be a response to a preceding message with tool_calls".
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    i = 0
    n = len(messages)
    while i < n:
        m = messages[i]
        calls = m.get("tool_calls") if m.get("role") == "assistant" else None
        if isinstance(calls, list) and calls:
            ids = [
                str(tc.get("id") or "")
                for tc in calls
                if isinstance(tc, dict) and str(tc.get("id") or "")
            ]
            j = i + 1
            found: dict[str, dict[str, Any]] = {}
            while j < n and messages[j].get("role") == "tool":
                tid = str(messages[j].get("tool_call_id") or "")
                if tid and tid in ids and tid not in found and tid not in seen:
                    found[tid] = messages[j]
                j += 1
            if ids and all(tid in seen for tid in ids):
                i = j
                continue
            if not ids or j == i + 1:
                # No results followed. Keep a trailing call so argument repair
                # still runs; an interrupted call is text-only.
                if j < n:
                    kept = dict(m)
                    kept.pop("tool_calls", None)
                    out.append(kept)
                else:
                    out.append(m)
                i = j
                continue
            if all(tid in found for tid in ids):
                out.append(m)
                for tid in ids:
                    out.append(found[tid])
                    seen.add(tid)
            else:
                kept = dict(m)
                kept.pop("tool_calls", None)
                out.append(kept)
            i = j
            continue
        if m.get("role") == "tool":
            i += 1
            continue
        out.append(m)
        i += 1
    return out


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
