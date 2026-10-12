import type { RuntimeEvent } from "./client";
import { apiFetch, authHeaders, ensureApiToken, formatNetworkError, isTransientNetworkError, waitMs, noteUnauthorized } from "./client";

export type ChatHandlers = {
  onEvent: (ev: RuntimeEvent) => void;
  onFinal: (text: string, meta: Record<string, unknown>) => void;
  onError: (msg: string) => void;
  onAbort?: () => void;
};

export function parseSseBlock(block: string): RuntimeEvent | null {
  // Normalize CRLF so event:/data: parsing is reliable on Windows servers
  const lines = block.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  let eventName = "";
  const dataLines: string[] = [];
  for (const line of lines) {
    if (!line || line.startsWith(":")) continue; // comments / keep-alives
    if (line.startsWith("event:")) eventName = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length) return null;
  const raw = dataLines.join("\n");
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    // Support both full envelope {type,data} and bare payloads
    if (typeof parsed.type === "string") {
      return parsed as unknown as RuntimeEvent;
    }
    return {
      type: eventName || "message",
      data: parsed as Record<string, unknown>,
    };
  } catch {
    return {
      type: eventName || "message",
      data: { text: raw, message: raw },
    };
  }
}

const transientFetchError = isTransientNetworkError;

export async function streamChat(
  message: string,
  sessionId: string | null,
  handlers: ChatHandlers,
  signal?: AbortSignal,
  mode: "plan" | "agent" = "agent",
  display?: string,
  workspace?: string | null,
  disableTools?: boolean,
  /** Social one-shot call: never surface the forced session in history. */
  hidden?: boolean,
  /** Group harness already assigned this member; do not spawn another team. */
  blockDelegate?: boolean,
  /** One JSON classification: no tools, one model reply. */
  classify?: boolean,
): Promise<string | null> {
  let r: Response | null = null;
  let lastFetchError: unknown = null;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const headers = await authHeaders({
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      });
      const body: Record<string, unknown> = {
        message,
        session_id: sessionId,
        mode,
      };
      if (disableTools) body.disable_tools = true;
      if (display && display !== message) body.display = display;
      if (hidden) body.hidden = true;
      if (blockDelegate) body.block_delegate = true;
      if (classify) body.classify = true;
      // Every turn carries the workspace currently bound to this chat. Social
      // members use short-lived, synthetic session ids, so limiting this to new
      // sessions would silently fall back to the server's default folder.
      if (workspace) body.workspace = workspace;
      r = await apiFetch(`/api/chat`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal,
      }, 0);
      lastFetchError = null;
      break;
    } catch (e) {
      if (signal?.aborted || (e instanceof DOMException && e.name === "AbortError")) {
        handlers.onAbort?.();
        return sessionId;
      }
      lastFetchError = e;
      if (!transientFetchError(e) || attempt === 2) break;
      try {
        await waitMs(400 * (attempt + 1), signal);
      } catch {
        handlers.onAbort?.();
        return sessionId;
      }
    }
  }
  if (!r) {
    throw formatNetworkError(lastFetchError || new Error("Failed to fetch"), "/api/chat");
  }
  if (!r.ok || !r.body) {
    let detail = `chat failed: ${r.status}`;
    try {
      const body = (await r.json()) as { detail?: unknown };
      if (typeof body.detail === "string" && body.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    noteUnauthorized(r.status, detail);
    handlers.onError(detail);
    return sessionId;
  }

  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let sid = sessionId;
  let sawFinal = false;
  let aborted = false;
  let sawTerminal = false;
  let sawError = false;

  const handle = (ev: RuntimeEvent) => {
    const type = ev.type || "message";
    if (type === "session") {
      sid = String(ev.data?.session_id || sid);
      handlers.onEvent({ ...ev, type });
      return;
    }
    if (type === "final") {
      sawFinal = true;
      sawTerminal = true;
      // Deliver final to UI before cancelling the reader — social/workbench
      // must commit text (and browser_open that arrived earlier) first.
      handlers.onFinal(String(ev.data?.text ?? ""), ev.data || {});
      sid = String(ev.data?.session_id || sid);
      return;
    }
    if (type === "error") {
      sawError = true;
      sawTerminal = true;
      handlers.onError(String(ev.data?.message || "error"));
      return;
    }
    handlers.onEvent({ ...ev, type });
  };

  const flushParts = () => {
    const parts = buf.split(/\r?\n\r?\n/);
    buf = parts.pop() || "";
    for (const block of parts) {
      const ev = parseSseBlock(block);
      if (ev) handle(ev);
    }
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // SSE events are separated by blank lines; servers often use CRLF (\r\n\r\n)
      flushParts();
      // Turn is logically done on final/error — do not wait for the server to
      // close the SSE (persist / browser teardown can lag and leave Stop stuck).
      // Critical: only cancel AFTER handle() ran for every complete event in
      // this chunk (including final), so social can commit text/browser first.
      if (sawTerminal) {
        // Rare framing: more complete events may still sit behind a trailing
        // separator in buf — flush once more before tearing down.
        if (buf.includes("\n\n") || buf.includes("\r\n\r\n")) flushParts();
        try {
          await reader.cancel();
        } catch {
          /* ignore */
        }
        break;
      }
    }
    if (!sawTerminal && buf.trim()) {
      const ev = parseSseBlock(buf);
      if (ev) handle(ev);
    }
  } catch (e) {
    if (signal?.aborted || (e instanceof DOMException && e.name === "AbortError")) {
      aborted = true;
      handlers.onAbort?.();
    } else if (transientFetchError(e) && sid && !sawTerminal) {
      // The turn can still be running after the browser drops the socket
      // (common while the in-app page is opening). Rejoin that session
      // instead of showing a hard send failure.
      try {
        await attachSessionEvents(sid, handlers, signal);
        return sid;
      } catch (reattachError) {
        if (signal?.aborted || (reattachError instanceof DOMException && reattachError.name === "AbortError")) {
          handlers.onAbort?.();
          return sid;
        }
        throw e;
      }
    } else {
      throw e;
    }
  }
  // Never synthesize an empty final after an SSE error — that overwrote social
  // error text with socialEmptyReply.
  if (!sawFinal && !aborted && !sawError) {
    handlers.onFinal("", { session_id: sid, partial: true });
  }
  return sid;
}

export async function attachSessionEvents(
  sessionId: string,
  handlers: ChatHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const headers = await authHeaders({ Accept: "text/event-stream" });
  let r: Response;
  try {
    r = await apiFetch(`/api/sessions/${encodeURIComponent(sessionId)}/events`, {
      method: "GET",
      headers,
      signal,
    });
  } catch (e) {
    if (signal?.aborted || (e instanceof DOMException && e.name === "AbortError")) {
      handlers.onAbort?.();
      return;
    }
    throw e;
  }
  if (!r.ok || !r.body) {
    handlers.onError(`attach failed: ${r.status}`);
    return;
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let sawFinal = false;
  let aborted = false;
  const handle = (ev: RuntimeEvent) => {
    const type = ev.type || "message";
    if (type === "final") {
      sawFinal = true;
      handlers.onFinal(String(ev.data?.text ?? ""), ev.data || {});
      return;
    }
    if (type === "error") {
      handlers.onError(String(ev.data?.message || "error"));
      return;
    }
    handlers.onEvent({ ...ev, type });
  };
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split(/\r?\n\r?\n/);
      buf = parts.pop() || "";
      for (const block of parts) {
        const ev = parseSseBlock(block);
        if (ev) handle(ev);
      }
    }
    if (buf.trim()) {
      const ev = parseSseBlock(buf);
      if (ev) handle(ev);
    }
  } catch (e) {
    if (signal?.aborted || (e instanceof DOMException && e.name === "AbortError")) {
      aborted = true;
      handlers.onAbort?.();
    } else {
      throw e;
    }
  }
  if (!sawFinal && !aborted) {
    handlers.onFinal("", { session_id: sessionId, partial: true });
  }
}

