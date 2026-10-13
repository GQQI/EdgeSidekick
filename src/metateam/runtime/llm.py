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
from .llm_extract import (
    _choice_reasoning_extras,
    extract_content_text,
    extract_message_text,
    extract_reasoning_text,
)
from .llm_args import align_tool_chains, parse_tool_args

_log = get_logger("metateam.llm")


def _is_tool_choice_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        "enable-auto-tool-choice" in msg
        or "tool-call-parser" in msg
        or '"auto" tool choice' in msg
        or "auto tool choice" in msg
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
        if "tools" in retry or "tool_choice" in retry:
            retry.pop("tools", None)
            retry.pop("tool_choice", None)
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
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": _strip_for_api(messages),
            "temperature": temperature,
            "stream": stream,
        }
        if tools and not self._disable_tools:
            kwargs["tools"] = tools
            # Do not send tool_choice="auto": vLLM 400s unless the server was
            # started with --enable-auto-tool-choice --tool-call-parser.
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
    ) -> dict[str, Any]:
        if self.demo:
            return _demo_chat(messages, tools)

        assert self.client is not None
        temp = self.settings.temperature if temperature is None else temperature
        kwargs = self._call_kwargs(messages, tools, temp, stream=False)
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

        out: dict[str, Any] = {"role": "assistant", "content": content}
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
        temp = self.settings.temperature if temperature is None else temperature
        kwargs = self._call_kwargs(messages, tools, temp, stream=True)
        stream = self._create(kwargs)

        self._active_stream = stream
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_acc: dict[int, dict[str, str]] = {}
        id_to_idx: dict[str, int] = {}
        cancelled = False
        looped_text = False
        repeat_cut = RepeatCut()
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
                    reasoning_parts.append(reasoning_piece)
                    yield ("reasoning_delta", reasoning_piece)
                    if repeat_cut.observe("".join(reasoning_parts)):
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
                            reasoning_parts.append(piece)
                            yield ("reasoning_delta", piece)
                            if repeat_cut.observe("".join(reasoning_parts)):
                                looped_text = True
                                try:
                                    self.close_active_stream()
                                except Exception:
                                    pass
                                break
                        else:
                            visible, xml_hold = split_before_tool_markup(xml_hold + piece)
                            if visible:
                                content_parts.append(visible)
                                yield ("delta", visible)
                                if repeat_cut.observe("".join(content_parts)):
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
                reasoning_parts.append(piece)
                yield ("reasoning_delta", piece)
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
        if looped_text:
            # Keep a short prefix. The repeated tail must not re-enter history
            # or the next turn copies it.
            if len(reasoning) > 480:
                reasoning = reasoning[:480].rstrip() + "…"
            if streaming_text_looped(content):
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


REPEAT_CUT_SEC = 20.0


class RepeatCut:
    """Cut a generation only after similar text has been repeating for a while.

    A few repeated sentences are not a reason to end the task. The clock
    starts when the text first looks stuck, and resets if new text arrives.
    """

    def __init__(self, *, seconds: float = REPEAT_CUT_SEC, now=time.monotonic) -> None:
        self.seconds = float(seconds)
        self._now = now
        self._since: float | None = None

    def observe(self, text: str) -> bool:
        if not streaming_text_looped(text):
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
    if len(blob) < 360:
        return False
    tail = blob[-96:]
    window = blob[-960:] if len(blob) > 960 else blob
    if tail.strip() and window.count(tail) >= 4:
        return True
    # A sentence glued end to end does not share a 96-char tail, so count()
    # on that tail stays at 1. Compare the recent sentences themselves.
    recent = blob[-1200:]
    parts = [p.strip() for p in re.split(r"[。！？!?\n]+", recent) if len(p.strip()) >= 12]
    if len(parts) >= 6 and sum(1 for p in parts[-8:] if p == parts[-1]) >= 4:
        return True
    lines = [ln.strip() for ln in blob.splitlines() if len(ln.strip()) >= 16]
    if len(lines) >= 5 and lines[-1] == lines[-2] == lines[-3] == lines[-4]:
        return True
    paras = [p.strip() for p in re.split(r"\n\s*\n", blob) if len(p.strip()) >= 40]
    if len(paras) >= 4 and paras[-1] == paras[-2] == paras[-3]:
        return True
    return False





# Re-exports so existing "from metateam.runtime.llm import ..." keep working.
from .llm_extract import (  # noqa: E402
    _THINK_PART_TYPES,
    _REASONING_TEXT_KEYS,
    extract_content_text,
    extract_message_text,
    extract_reasoning_text,
)
from .llm_args import (  # noqa: E402
    align_tool_chains,
    canonical_tool_arguments,
    canonicalize_tool_call,
    decode_tool_args_json,
    parse_tool_args,
    _strip_for_api,
    _demo_chat,
)
