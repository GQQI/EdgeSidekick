import { useEffect, useRef, useState } from "react";
import {
  answerAsk,
  attachSessionEvents,
  confirmPlan,
  decideApproval,
  fetchMemory,
  fetchSession,
  fetchSkills,
  stopSession,
  streamChat,
  type RuntimeEvent,
  type SessionDetail,
  type SessionItem,
  type SkillItem,
} from "../api";
import type { ActivePlan } from "../components/TaskPlanPanel";
import {
  ASK_CUSTOM_KEY,
  type ApprovalPrompt,
  type AskPrompt,
  type ChatMsg,
  type DetailView,
  type LiveLine,
  type MsgAttachment,
  type QueuedMsg,
  type SettingsTab,
  type SubNode,
  type SubTranscriptItem,
  type ToolCard,
} from "../types/chat";
import type { PlanConfirmState, PlanTask } from "../types/plan";
import { ThinkTagSplitter, splitThinkTags } from "../utils/thinkTags";
import {
  absorbReasoningChunk,
  absorbReasoningStream,
} from "../utils/streamRepeats";
import { setAskEdges } from "../state/askEdgesStore";
import {
  mapSessionMessages,
  uid,
  findSubNode,
  mapSubNode,
} from "../utils/chatHelpers";
import type { MsgKey } from "../i18n";
import { handleRuntimeEvent } from "./chat/handleRuntimeEvent";
import { replaceStageSubagents } from "./chat/canvasSync";
import {
  upsertToolDelta,
  upsertToolEnd,
  upsertToolStart,
  type ToolUpsertCtx,
} from "./chat/toolUpserts";

export type ChatStreamDeps = {
  t: (key: MsgKey, ...args: string[]) => string;
  locale: string;
  sessionId: string | null;
  sessionIdRef: React.MutableRefObject<string | null>;
  activeWs: { path: string; name: string; id?: string } | null;
  chatMode: "plan" | "agent";
  setMessages: React.Dispatch<React.SetStateAction<ChatMsg[]>>;
  setInput: React.Dispatch<React.SetStateAction<string>>;
  setBusy: React.Dispatch<React.SetStateAction<boolean>>;
  setLive: React.Dispatch<React.SetStateAction<LiveLine[]>>;
  setSubs: React.Dispatch<React.SetStateAction<SubNode[]>>;
  setDetail: React.Dispatch<React.SetStateAction<DetailView>>;
  setCtx: React.Dispatch<React.SetStateAction<{ tokens: number; limit: number }>>;
  setCompressState: React.Dispatch<
    React.SetStateAction<{
      active: boolean;
      message: string;
      attempt: number;
      maxAttempts: number;
      before: number;
      after?: number;
    } | null>
  >;
  setActivePlan: React.Dispatch<React.SetStateAction<ActivePlan | null>>;
  setPlanConfirm: React.Dispatch<React.SetStateAction<PlanConfirmState | null>>;
  setApproval: React.Dispatch<React.SetStateAction<ApprovalPrompt | null>>;
  setAskPrompt: React.Dispatch<React.SetStateAction<AskPrompt | null>>;
  setAskChoice: React.Dispatch<React.SetStateAction<string>>;
  setAskOtherText: React.Dispatch<React.SetStateAction<string>>;
  setFsRefresh: React.Dispatch<React.SetStateAction<number>>;
  setToast: React.Dispatch<React.SetStateAction<string>>;
  setSessionId: React.Dispatch<React.SetStateAction<string | null>>;
  setStats: React.Dispatch<React.SetStateAction<{ tokens: number; iters: number }>>;
  setSkills: React.Dispatch<React.SetStateAction<SkillItem[]>>;
  setMemory: React.Dispatch<React.SetStateAction<string>>;
  setSettingsTab: (tab: SettingsTab) => void;
  setSettingsOpen: (v: boolean) => void;
  setQueued: React.Dispatch<React.SetStateAction<QueuedMsg[]>>;
  approval: ApprovalPrompt | null;
  askPrompt: AskPrompt | null;
  planConfirm: PlanConfirmState | null;
  stickBottomRef: React.MutableRefObject<boolean>;
  askPendingRef: React.MutableRefObject<boolean>;
  planPendingRef: React.MutableRefObject<boolean>;
  executingPlanIdRef: React.MutableRefObject<string | null>;
  refreshSessionsRef: React.MutableRefObject<(page?: number) => Promise<void>>;
};

export function useChatStream(deps: ChatStreamDeps) {
  const {
    t,
    locale,
    sessionId,
    sessionIdRef,
    activeWs,
    chatMode,
    setMessages,
    setInput,
    setBusy,
    setLive,
    setSubs,
    setDetail,
    setCtx,
    setCompressState,
    setActivePlan,
    setPlanConfirm,
    setApproval,
    setAskPrompt,
    setAskChoice,
    setAskOtherText,
    setFsRefresh,
    setToast,
    setSessionId,
    setStats,
    setSkills,
    setMemory,
    setSettingsTab,
    setSettingsOpen,
    setQueued,
    approval,
    askPrompt,
    planConfirm,
    stickBottomRef,
    askPendingRef,
    planPendingRef,
    executingPlanIdRef,
    refreshSessionsRef,
  } = deps;

  const openSettings = (tab: SettingsTab = "workspace") => {
    setSettingsTab(tab);
    setSettingsOpen(true);
  };

  const transcriptRef = useRef<ChatMsg[]>([]);
  /** pending:… ↔ real child_id so live deltas still find the canvas card. */
  const subagentAliasRef = useRef<Map<string, string>>(new Map());
  const messagesFlushTimerRef = useRef<number | null>(null);
  const detailFlushTimerRef = useRef<number | null>(null);
  const pendingDetailRef = useRef<((d: DetailView) => DetailView) | null>(null);
  const stageRef = useRef(0);
  const streamIdRef = useRef<string | null>(null);
  const streamTextRef = useRef("");
  const streamReasoningRef = useRef("");
  const nativeReasoningRef = useRef(false);
  const thinkSplitRef = useRef(new ThinkTagSplitter());
  const abortRef = useRef<AbortController | null>(null);
  const listenerGenRef = useRef(0);
  const stoppingRef = useRef(false);
  const turnDoneRef = useRef(false);
  const queuedRef = useRef<QueuedMsg[]>([]);
  const bindingRef = useRef<{ mcpServerIds: string[]; pluginIds: string[] }>({
    mcpServerIds: [],
    pluginIds: [],
  });
  const setSessionBinding = (next: { mcpServerIds: string[]; pluginIds: string[] }) => {
    bindingRef.current = {
      mcpServerIds: [...(next.mcpServerIds || [])],
      pluginIds: [...(next.pluginIds || [])],
    };
  };
  const busyRef = useRef(false);
  const runningSinceRef = useRef<Record<string, number>>({});
  const [runningSessionIds, setRunningSessionIds] = useState<string[]>([]);

  function markSessionRunning(id: string | null | undefined) {
    const sid = String(id || "").trim();
    if (!sid) return;
    runningSinceRef.current[sid] = Date.now();
    setRunningSessionIds((prev) => (prev.includes(sid) ? prev : [...prev, sid]));
  }

  function markSessionIdle(id: string | null | undefined) {
    const sid = String(id || "").trim();
    if (!sid) return;
    delete runningSinceRef.current[sid];
    setRunningSessionIds((prev) => (prev.includes(sid) ? prev.filter((x) => x !== sid) : prev));
  }

  function reconcileRunningSessions(items: SessionItem[]) {
    const now = Date.now();
    setRunningSessionIds((prev) => {
      const next = prev.filter((id) => {
        const started = runningSinceRef.current[id] || 0;
        // Explicit idle clears the timestamp; do not revive via the start grace.
        if (!started) return false;
        const hit = items.find((s) => s.id === id);
        if (hit?.busy) return true;
        return now - started < 8000;
      });
      if (next.length === prev.length && next.every((id, i) => id === prev[i])) return prev;
      return next;
    });
  }

  function publishSubs() {
    const next = transcriptRef.current
      .filter((m) => m.role === "subagent" && m.subagent)
      .map((m) => m.subagent!);
    setSubs((prev) => {
      if (prev.length === next.length && prev.every((n, i) => n === next[i])) return prev;
      return next;
    });
  }

  function flushDetailNow() {
    if (detailFlushTimerRef.current != null) {
      window.clearTimeout(detailFlushTimerRef.current);
      detailFlushTimerRef.current = null;
    }
    const fn = pendingDetailRef.current;
    pendingDetailRef.current = null;
    if (fn) setDetail(fn);
  }

  function queueDetail(updater: (d: DetailView) => DetailView, immediate = true) {
    const prev = pendingDetailRef.current;
    pendingDetailRef.current = prev ? (d) => updater(prev(d)) : updater;
    if (immediate) {
      flushDetailNow();
      return;
    }
    if (detailFlushTimerRef.current != null) return;
    detailFlushTimerRef.current = window.setTimeout(() => {
      detailFlushTimerRef.current = null;
      flushDetailNow();
    }, 50);
  }


  /** User closed the detail panel: drop coalesced stream updates so the next
   * chunk cannot reopen it, then clear selection. */
  function dismissDetail() {
    if (detailFlushTimerRef.current != null) {
      window.clearTimeout(detailFlushTimerRef.current);
      detailFlushTimerRef.current = null;
    }
    pendingDetailRef.current = null;
    setDetail(null);
  }
  function flushMessagesNow() {
    if (messagesFlushTimerRef.current != null) {
      window.clearTimeout(messagesFlushTimerRef.current);
      messagesFlushTimerRef.current = null;
    }
    setMessages(transcriptRef.current);
    publishSubs();
  }

  function scheduleMessagesFlush() {
    if (messagesFlushTimerRef.current != null) return;
    // Streaming tokens arrive far faster than paint. Coalesce so a long thread
    // is not fully reconciled on every SSE chunk.
    messagesFlushTimerRef.current = window.setTimeout(() => {
      messagesFlushTimerRef.current = null;
      setMessages(transcriptRef.current);
      publishSubs();
    }, 50);
  }

  function commit(next: ChatMsg[], immediate = true) {
    transcriptRef.current = next;
    if (immediate) flushMessagesNow();
    else scheduleMessagesFlush();
  }

  useEffect(() => {
    subagentAliasRef.current.clear();
  }, [sessionId]);

  useEffect(
    () => () => {
      if (messagesFlushTimerRef.current != null) {
        window.clearTimeout(messagesFlushTimerRef.current);
      }
      if (detailFlushTimerRef.current != null) {
        window.clearTimeout(detailFlushTimerRef.current);
      }
    },
    [],
  );

  function loadTranscript(next: ChatMsg[]) {
    const maxStage = next.reduce((acc, m) => Math.max(acc, m.stage ?? 0), 0);
    stageRef.current = next.length ? maxStage : 0;
    commit(next);
  }

function appendMsg(msg: ChatMsg) {
  if (msg.id && transcriptRef.current.some((m) => m.id === msg.id)) return;
  commit([
    ...transcriptRef.current,
    { ...msg, stage: msg.stage ?? stageRef.current, ts: msg.ts ?? Date.now() },
  ]);
}

function removeMsg(id: string) {
  commit(transcriptRef.current.filter((m) => m.id !== id));
}

function bumpStage() {
  stageRef.current += 1;
}

function updateMsg(id: string, patch: Partial<ChatMsg>, immediate = true) {
  const list = transcriptRef.current;
  const idx = list.findIndex((m) => m.id === id);
  if (idx < 0) return;
  const next = list.slice();
  next[idx] = { ...list[idx], ...patch };
  commit(next, immediate);
}

/** Keep the open tool detail panel in sync when status/callId advances. */
function syncToolPanel(tool: ToolCard, prevCallId?: string, immediate = true) {
  queueDetail((d) => {
    if (d?.type !== "tool") return d;
    const same =
      d.tool.id === tool.id ||
      d.tool.callId === tool.callId ||
      (prevCallId != null &&
        prevCallId !== "" &&
        d.tool.callId === prevCallId) ||
      (Boolean(d.tool.name) &&
        d.tool.name === tool.name &&
        (d.tool.status === "streaming" ||
          d.tool.status === "running" ||
          d.tool.status === "pending") &&
        (tool.status === "running" ||
          tool.status === "pending" ||
          tool.status === "done" ||
          tool.status === "error"));
    return same ? { type: "tool", tool } : d;
  }, immediate);
}

function findToolMsg(opts: {
  callId?: string;
  name?: string;
  statuses?: ToolCard["status"][];
}): ChatMsg | undefined {
  const callId = opts.callId || "";
  const name = opts.name || "";
  const statuses = opts.statuses;
  const list = transcriptRef.current;
  for (let i = list.length - 1; i >= 0; i--) {
    const m = list[i];
    if (m.role !== "tool" || !m.tool) continue;
    if (callId && m.tool.callId === callId) {
      if (!statuses || statuses.includes(m.tool.status)) return m;
      continue;
    }
    if (
      name &&
      m.tool.name === name &&
      (!statuses || statuses.includes(m.tool.status))
    ) {
      return m;
    }
  }
  return undefined;
}

function setBusyState(v: boolean) {
  busyRef.current = v;
  setBusy(v);
}

function setQueuedState(next: QueuedMsg[]) {
  queuedRef.current = next;
  setQueued(next);
}

function enqueueMessage(
  text: string,
  opts?: { userDisplay?: string; attachments?: MsgAttachment[] },
) {
  const item: QueuedMsg = {
    id: uid(),
    text,
    userDisplay: opts?.userDisplay,
    attachments: opts?.attachments,
  };
  setQueuedState([...queuedRef.current, item]);
  setInput("");
  setToast("已加入队列，当前任务结束后发送。");
}

function removeQueued(id: string) {
  setQueuedState(queuedRef.current.filter((q) => q.id !== id));
}

function clearQueued() {
  setQueuedState([]);
}

function rememberSubagentAlias(a: string, b: string) {
  if (!a || !b || a === b) return;
  subagentAliasRef.current.set(a, b);
  subagentAliasRef.current.set(b, a);
}

function lookupSubagentIds(id: string): string[] {
  const out = [id];
  const alt = subagentAliasRef.current.get(id);
  if (alt && alt !== id) out.push(alt);
  return out;
}

function updateSubagentMsg(childId: string, patch: Partial<SubNode>) {
  patchSubagent(childId, (s) => ({ ...s, ...patch }));
}

function patchSubagent(
  childId: string,
  fn: (s: SubNode) => SubNode,
  immediate = false,
) {
  if (!childId) return;
  const candidates = lookupSubagentIds(childId);
  let hitMsg: ChatMsg | undefined;
  let targetId = childId;
  for (const id of candidates) {
    const msg = transcriptRef.current.find(
      (m) => m.role === "subagent" && m.subagent && findSubNode(m.subagent, id),
    );
    if (msg?.subagent) {
      hitMsg = msg;
      targetId = id;
      break;
    }
  }
  if (!hitMsg?.subagent) {
    // Do not invent a nameless robot for a tool/step event that arrived
    // before subagent_start — those stubs looked like extra agents.
    return;
  }
  const prevId = targetId;
  const mappedRoot =
    hitMsg.subagent.id === targetId
      ? fn(hitMsg.subagent)
      : mapSubNode(hitMsg.subagent, targetId, fn);
  if (!mappedRoot) return;
  const patched =
    findSubNode(mappedRoot, targetId) ||
    findSubNode(mappedRoot, childId) ||
    (mappedRoot.id !== hitMsg.subagent.id ? mappedRoot : null) ||
    mappedRoot;
  if (patched.id && patched.id !== prevId) {
    rememberSubagentAlias(prevId, patched.id);
  }
  if (patched.id && patched.id !== childId) {
    rememberSubagentAlias(childId, patched.id);
  }
  updateMsg(
    hitMsg.id,
    {
      subagent: mappedRoot,
      content: mappedRoot.summary || mappedRoot.goal,
    },
    immediate,
  );
  queueDetail((d) => {
    if (d?.type !== "subagent") return d;
    const openId = d.subagent.id;
    const openSet = new Set(lookupSubagentIds(openId));
    openSet.add(openId);
    const touchesOpen =
      openSet.has(childId) ||
      openSet.has(targetId) ||
      openSet.has(patched.id) ||
      openSet.has(mappedRoot.id) ||
      Boolean(findSubNode(mappedRoot, openId));
    if (!touchesOpen) return d;
    const live =
      findSubNode(mappedRoot, patched.id) ||
      findSubNode(mappedRoot, openId) ||
      patched;
    return { type: "subagent", subagent: live };
  }, immediate);
}

function sealSubassistant(transcript: SubTranscriptItem[]): SubTranscriptItem[] {
  return transcript.map((item) =>
    item.kind === "assistant" && (item.streaming || item.reasoningStreaming)
      ? { ...item, streaming: false, reasoningStreaming: false }
      : item,
  );
}

function sealStoppedSubtree(node: SubNode): SubNode {
  const stopped = node.status === "running";
  return {
    ...node,
    status: stopped ? "error" : node.status,
    summary: stopped ? node.summary || "（已停止）" : node.summary,
    activity: undefined,
    transcript: sealSubassistant((node.transcript || []).map((item) =>
      item.kind === "tool" && ["streaming", "running", "pending"].includes(item.tool.status)
        ? { ...item, tool: { ...item.tool, status: "error", result: item.tool.result || "ERROR: cancelled" } }
        : item,
    )),
    children: (node.children || []).map(sealStoppedSubtree),
  };
}

function sealStoppedTurn() {
  const next = transcriptRef.current.map((message) => {
    if (message.role === "subagent" && message.subagent) {
      const subagent = sealStoppedSubtree(message.subagent);
      return { ...message, subagent, content: subagent.summary || message.content };
    }
    if (message.role === "tool" && message.tool && ["streaming", "running", "pending"].includes(message.tool.status)) {
      return { ...message, tool: { ...message.tool, status: "error" as const, result: message.tool.result || "ERROR: cancelled" } };
    }
    return message;
  });
  commit(next);
}

function looksLikeOptionList(text: string): boolean {
  const lines = text
    .split("\n")
    .filter((line) => /^\s*(\d+|[A-Za-z])[\.\)、：]\s*.+/.test(line));
  return lines.length >= 2;
}

function discardStreamBubble() {
  const id = streamIdRef.current;
  // Flush tag leftovers into the refs first so we can decide seal vs drop.
  for (const p of thinkSplitRef.current.flush()) {
    if (p.kind === "reasoning") streamReasoningRef.current += p.text;
    else streamTextRef.current += p.text;
  }
  thinkSplitRef.current.reset();
  // Keep any thinking (or text) that already streamed — ask/plan discard must
  // not erase the "思考" bubble the user just watched. Only drop a truly empty
  // opener that was created solely to be replaced.
  if (streamReasoningRef.current.trim() || streamTextRef.current.trim()) {
    sealStreamBubble();
    return;
  }
  if (id) {
    commit(transcriptRef.current.filter((m) => m.id !== id));
  }
  streamIdRef.current = null;
  streamTextRef.current = "";
  streamReasoningRef.current = "";
  nativeReasoningRef.current = false;
}

function stripDuplicateAskBubble(question: string) {
  discardStreamBubble();
  const q = question.trim();
  const qHead = q.slice(0, Math.min(80, q.length));
  for (let i = transcriptRef.current.length - 1; i >= 0; i--) {
    const m = transcriptRef.current[i];
    if (m.role !== "assistant") continue;
    if (m.tool) break;
    const text = (m.content || "").trim();
    if (!text) break;
    const sameQuestion = Boolean(
      qHead &&
        (text.includes(qHead) ||
          (text.length >= 24 && q.includes(text.slice(0, Math.min(80, text.length))))),
    );
    if (looksLikeOptionList(text) || sameQuestion) {
      commit(transcriptRef.current.filter((x) => x.id !== m.id));
    }
    break;
  }
}

function sealStreamBubble() {
  const id = streamIdRef.current;
  if (!id) return;
  // Flush any buffered partial <think> tag leftovers
  for (const p of thinkSplitRef.current.flush()) {
    if (p.kind === "reasoning") streamReasoningRef.current += p.text;
    else streamTextRef.current += p.text;
  }
  thinkSplitRef.current.reset();
  const text = streamTextRef.current;
  const reasoning = streamReasoningRef.current;
  if (text.trim() || reasoning.trim()) {
    updateMsg(id, {
      content: text,
      reasoning: reasoning || undefined,
      streaming: false,
      reasoningStreaming: false,
    });
  } else {
    commit(transcriptRef.current.filter((m) => m.id !== id));
  }
  streamIdRef.current = null;
  streamTextRef.current = "";
  streamReasoningRef.current = "";
  nativeReasoningRef.current = false;
}

function sealStreamBubbleIfContent() {
  if (!streamIdRef.current) return;
  if (streamTextRef.current.trim() || streamReasoningRef.current.trim()) {
    sealStreamBubble();
  }
  // Empty bubble: keep it open. Reasoning that streams after the first
  // tool-arg delta still lands here, above the tool chip.
}

function ensureStreamBubble(reset: boolean) {
  if (reset) sealStreamBubble();
  if (streamIdRef.current) return;
  // Continue the same LLM turn only while that bubble is still actively
  // streaming (content interleaved with tool_call_delta for THIS turn).
  // A sealed (non-streaming) bubble means a previous turn already finished —
  // reusing it just because a tool chip sits after it glued every later
  // turn's reasoning into the very first reply. Always start fresh instead.
  if (!reset) {
    for (let i = transcriptRef.current.length - 1; i >= 0; i--) {
      const m = transcriptRef.current[i];
      if (m.role === "tool") continue;
      if (m.role === "assistant") {
        if (!m.streaming) break;
        streamIdRef.current = m.id;
        streamTextRef.current = m.content || "";
        streamReasoningRef.current = m.reasoning || "";
        nativeReasoningRef.current = Boolean(m.reasoning);
        updateMsg(m.id, {
          streaming: true,
          reasoningStreaming: Boolean(m.reasoning) && !(m.content || "").trim(),
        });
        return;
      }
      break;
    }
  }
  const id = uid();
  streamIdRef.current = id;
  streamTextRef.current = "";
  streamReasoningRef.current = "";
  nativeReasoningRef.current = false;
  thinkSplitRef.current.reset();
  appendMsg({
    id,
    role: "assistant",
    content: "",
    reasoning: "",
    streaming: true,
    reasoningStreaming: false,
  });
}

function syncStreamBubble() {
  const id = streamIdRef.current!;
  updateMsg(
    id,
    {
      content: streamTextRef.current,
      reasoning: streamReasoningRef.current || undefined,
      streaming: true,
      reasoningStreaming:
        Boolean(streamReasoningRef.current) && !streamTextRef.current.trim(),
    },
    false,
  );
}

function appendStreamChunk(chunk: string, reset = false, discard = false) {
  if (reset) {
    if (discard) discardStreamBubble();
    else sealStreamBubble();
  }
  if (!chunk) {
    // Empty reset only seals the previous bubble. Opening a blank streaming
    // bubble here would wipe plan-prep thinking or leave an empty card.
    return;
  }
  ensureStreamBubble(false);
  // Peel <think>…</think> out of content (models that embed thinking in content).
  // Skip tagged pieces once native reasoning_content has started this turn.
  for (const p of thinkSplitRef.current.feed(chunk)) {
    if (p.kind === "reasoning") {
      if (nativeReasoningRef.current) continue;
      streamReasoningRef.current = absorbReasoningStream(
        streamReasoningRef.current,
        p.text,
      );
    } else {
      streamTextRef.current = absorbReasoningChunk(streamTextRef.current, p.text);
    }
  }
  syncStreamBubble();
}

function appendReasoningChunk(chunk: string, reset = false) {
  if (reset) {
    sealStreamBubble();
    if (!chunk) return;
  }
  ensureStreamBubble(false);
  // Native reasoning_content / reasoning field
  nativeReasoningRef.current = true;
  // Merge cumulative snapshots + collapse tape-recorder tails so the
  // thinking bubble never paints the same paragraph endlessly.
  streamReasoningRef.current = absorbReasoningStream(
    streamReasoningRef.current,
    chunk,
  );
  syncStreamBubble();
}

function stripCancelControlNotice(text: string): string {
  if (!text) return text;
  if (
    !text.includes("用户已停止本轮生成") &&
    !text.includes("上一轮生成已中断") &&
    !text.includes("请等待下一条用户指令")
  ) {
    return text;
  }
  const lines = text.split("\n");
  const kept: string[] = [];
  for (const line of lines) {
    const stripped = line.trim();
    if (!stripped) {
      kept.push(line);
      continue;
    }
    if (
      (stripped.startsWith("（") && stripped.endsWith("）") &&
        (stripped.includes("用户已停止本轮生成") || stripped.includes("上一轮生成已中断"))) ||
      stripped.includes("用户已停止本轮生成") ||
      stripped.includes("上一轮生成已中断")
    ) {
      const markers = [
        "（用户已停止本轮生成",
        "用户已停止本轮生成",
        "（上一轮生成已中断",
        "上一轮生成已中断",
      ];
      let cut = stripped;
      for (const marker of markers) {
        const idx = cut.indexOf(marker);
        if (idx >= 0) {
          cut = cut.slice(0, idx).replace(/[\s（(]+$/u, "");
          break;
        }
      }
      if (cut) kept.push(cut);
      continue;
    }
    kept.push(line);
  }
  while (kept.length && !kept[kept.length - 1].trim()) kept.pop();
  return kept.join("\n").trim();
}

function finalizeAssistant(text: string, opts?: { stopped?: boolean; reasoning?: string }) {
  if (turnDoneRef.current) return;
  turnDoneRef.current = true;
  for (const p of thinkSplitRef.current.flush()) {
    if (p.kind === "reasoning") streamReasoningRef.current += p.text;
    else streamTextRef.current += p.text;
  }
  thinkSplitRef.current.reset();
  const id = streamIdRef.current;
  const streamed = streamTextRef.current.trim();
  const incoming = (text || "").trim();
  const placeholder = "（已停止）";
  let body: string;
  if (opts?.stopped) {
    // Prefer already-streamed UI text; never replace it with the stop placeholder
    const existing =
      id
        ? (transcriptRef.current.find((m) => m.id === id)?.content || "").trim()
        : "";
    body =
      streamed ||
      existing ||
      (incoming && incoming !== placeholder ? incoming : "") ||
      placeholder;
  } else {
    body = incoming || streamed;
  }
  let reasoning = streamReasoningRef.current.trim();
  if (!reasoning && opts?.reasoning) {
    reasoning = String(opts.reasoning).trim();
  }
  if (/<\/?think/i.test(body)) {
    const peeled = splitThinkTags(body);
    body = peeled.content.trim();
    // Prefer native reasoning; only keep tagged peel when none was streamed.
    if (peeled.reasoning && !nativeReasoningRef.current) {
      reasoning = `${reasoning}${peeled.reasoning}`.trim();
    }
  }
  if (!body && !opts?.stopped) {
    body = t("emptyRoundOutput");
  }
  // Never show the internal stop-control parenthetical as if the model said it.
  body = stripCancelControlNotice(body);
  if (id && transcriptRef.current.some((m) => m.id === id)) {
    updateMsg(id, {
      content: body,
      reasoning: reasoning || undefined,
      streaming: false,
      reasoningStreaming: false,
    });
  } else if (body) {
    appendMsg({
      id: uid(),
      role: "assistant",
      content: body,
      reasoning: reasoning || undefined,
    });
  }
  streamIdRef.current = null;
  streamTextRef.current = "";
  streamReasoningRef.current = "";
  nativeReasoningRef.current = false;
}

function isLiveListener(gen: number) {
  return listenerGenRef.current === gen;
}

/** `final` carries the post-turn window usage; settle the meter on it. */
function syncCtxFromFinal(meta: Record<string, unknown>) {
  const metaSid = String(meta.session_id || "");
  const activeSid = String(sessionIdRef.current || sessionId || "");
  if (metaSid && activeSid && metaSid !== activeSid) return;
  const tokens = Number(meta.context_tokens);
  const limit = Number(meta.context_limit);
  if (!Number.isFinite(tokens) || tokens < 0) return;
  setCtx((c) => ({
    tokens,
    limit: Number.isFinite(limit) && limit > 0 ? limit : c.limit,
  }));
}

function detachListener() {
  listenerGenRef.current += 1;
  const activeAbort = abortRef.current;
  abortRef.current = null;
  activeAbort?.abort();
  sealStoppedTurn();
  abortRef.current = null;
  stoppingRef.current = false;
  // The next session's snapshot (resumeFromSnapshot) restores its own plan.
  executingPlanIdRef.current = null;
  setActivePlan(null);
  setBusyState(false);
}

function beginUiListener() {
  listenerGenRef.current += 1;
  abortRef.current?.abort();
  const ac = new AbortController();
  abortRef.current = ac;
  return { gen: listenerGenRef.current, ac };
}

async function stopChat() {
  const pending = approval;
  const pendingAsk = askPrompt;
  const pendingPlan = planConfirm;
  // Clear UI immediately so the panel cannot stick
  setApproval(null);
  setAskPrompt(null);
  askPendingRef.current = false;
  setAskChoice("");
  setAskOtherText("");
  setPlanConfirm(null);
  planPendingRef.current = false;

  if (!busyRef.current && !abortRef.current) {
    // Chat already idle: only dismiss leftover prompts
    if (pending && sessionId) {
      try {
        await decideApproval(sessionId, pending.approvalId, false, false);
      } catch {
        /* ignore */
      }
      setToast("已取消待确认操作");
    }
    if (pendingAsk && (sessionIdRef.current || sessionId)) {
      try {
        await answerAsk(
          sessionIdRef.current || sessionId!,
          pendingAsk.askId,
          ASK_CUSTOM_KEY,
          "",
        );
      } catch {
        /* ignore */
      }
    }
    if (pendingPlan && (sessionIdRef.current || sessionId || pendingPlan.sessionId)) {
      try {
        await confirmPlan(
          pendingPlan.sessionId || sessionIdRef.current || sessionId!,
          pendingPlan.planId,
          { approved: false },
        );
      } catch {
        /* ignore */
      }
    }
    return;
  }

  stoppingRef.current = true;
  setToast("正在停止…");
  const sid = sessionIdRef.current || sessionId;
  // Detach the UI first. A provider can take time to unwind a stream after the
  // server accepts cancellation, but one press must immediately end the local
  // loading state and make history safe to open.
  abortRef.current?.abort();
  if (sid) markSessionIdle(sid);
  // plan_done may never arrive once we detach; drop the pinned plan now.
  executingPlanIdRef.current = null;
  setActivePlan(null);
  setBusyState(false);
  void refreshSessionsRef.current();
  // Server cancel also rejects pending approvals — do not double-call decide here
  if (sid) {
    void stopSession(sid).catch(() => {});
  }
}

function alignCanvasFromSession(sid: string) {
  if (!sid) return;
  void fetchSession(sid)
    .then((d) => {
      if ((sessionIdRef.current || "") !== sid) return;
      const mapped = mapSessionMessages(d.messages, d.agent_tree);
      const byStage = new Map<number, SubNode[]>();
      for (const m of mapped) {
        if (m.role !== "subagent" || !m.subagent) continue;
        const st = m.stage ?? 0;
        const list = byStage.get(st) || [];
        list.push(m.subagent);
        byStage.set(st, list);
      }
      if (!byStage.size) return;
      for (const [st, nodes] of byStage) {
        replaceStageSubagents(toolUpsertCtx, nodes, st);
      }
    })
    .catch(() => {});
}

const toolUpsertCtx: ToolUpsertCtx = {
  sealStreamBubble,
  sealStreamBubbleIfContent,
  findToolMsg,
  updateMsg,
  syncToolPanel,
  queueDetail,
  appendMsg,
  removeMsg,
  commit,
  setSubs,
  setDetail,
  transcriptRef,
  stageRef,
};

async function drainQueueSoon() {
  window.setTimeout(() => {
    if (busyRef.current || stoppingRef.current) return;
    const [next, ...rest] = queuedRef.current;
    if (!next) return;
    setQueuedState(rest);
    void sendChat(next.text, {
      showUser: true,
      userDisplay: next.userDisplay,
      attachments: next.attachments,
    });
  }, 40);
}

async function sendChat(
  msg: string,
  opts?: {
    showUser?: boolean;
    userDisplay?: string;
    attachments?: MsgAttachment[];
    mode?: "plan" | "agent";
    mcpServerIds?: string[];
    pluginIds?: string[];
  },
) {
  if (!msg || busyRef.current) return;
  if (!activeWs?.path) {
    setToast(t("pickWorkspaceToast"));
    openSettings("workspace");
    return;
  }
  const showUser = opts?.showUser !== false;
  setInput("");
  setBusyState(true);
  markSessionRunning(sessionId);
  stoppingRef.current = false;
  turnDoneRef.current = false;
  stickBottomRef.current = true;
  setLive([]);
  setSubs([]);
  setCompressState(null);
  setActivePlan(null);
  executingPlanIdRef.current = null;
  streamIdRef.current = null;
  streamTextRef.current = "";
  streamReasoningRef.current = "";
  nativeReasoningRef.current = false;
  thinkSplitRef.current.reset();
  setAskEdges([]);
  bumpStage();
  if (showUser) {
    const displayText =
      opts?.userDisplay !== undefined ? opts.userDisplay : msg;
    appendMsg({
      id: uid(),
      role: "user",
      content: displayText,
      attachments: opts?.attachments,
    });
  }

  const { gen, ac } = beginUiListener();

  const runMode = opts?.mode ?? chatMode;
  const boundMcp = opts?.mcpServerIds ?? bindingRef.current?.mcpServerIds;
  const boundPlugins = opts?.pluginIds ?? bindingRef.current?.pluginIds;
  const eventCtx = {
    ...toolUpsertCtx,
    t,
    locale,
    sessionId,
    sessionIdRef,
    setPlanConfirm,
    setActivePlan,
    planPendingRef,
    executingPlanIdRef,
    patchSubagent,
    sealSubassistant,
    setLive,
    setSessionId,
    setCtx,
    setCompressState,
    appendStreamChunk,
    appendReasoningChunk,
    setApproval,
    findToolMsg,
    updateMsg,
    syncToolPanel,
    setDetail,
    stripDuplicateAskBubble,
    askPendingRef,
    setAskChoice,
    setAskOtherText,
    setAskPrompt,
    setFsRefresh,
    setSubs,
    appendMsg,
    commit,
    bumpStage,
  };

  try {
    const displayForApi =
      opts?.userDisplay !== undefined && opts.userDisplay !== msg
        ? opts.userDisplay
        : undefined;
    const sid = await streamChat(
      msg,
      sessionId,
      {
        onEvent: (ev) => {
          if (ev.type === "session") {
            const nid = String(ev.data.session_id || "");
            markSessionRunning(nid);
            void refreshSessionsRef.current();
          }
          if (!isLiveListener(gen)) return;
          handleRuntimeEvent(ev, eventCtx);
        },
        onFinal: (textOut, meta) => {
          markSessionIdle(String(meta.session_id || sessionId || ""));
          void refreshSessionsRef.current();
          if (!isLiveListener(gen)) return;
          const stopped = Boolean(meta.cancelled);
          finalizeAssistant(textOut, {
            stopped,
            reasoning: String(meta.reasoning || ""),
          });
          if (stopped && String(textOut || "").trim()) {
            setToast(t("stoppedKeep"));
          }
          setStats({
            tokens: Number(meta.tokens || 0),
            iters: Number(meta.iterations || 0),
          });
          syncCtxFromFinal(meta);
          if (meta.session_id) setSessionId(String(meta.session_id));
          alignCanvasFromSession(String(meta.session_id || sessionId || ""));
          void fetchSkills().then(setSkills);
          void fetchMemory().then(setMemory);
          setFsRefresh((n) => n + 1);
          // The turn is logically done the moment "final" arrives — don't
          // leave the "正在工作…" spinner up while the HTTP stream (which
          // may take a while to actually close server-side) keeps draining.
          stoppingRef.current = false;
          setBusyState(false);
          void drainQueueSoon();
        },
        onError: (err) => {
          markSessionIdle(sessionIdRef.current || sessionId);
          if (!isLiveListener(gen)) return;
          sealStreamBubble();
          appendMsg({ id: uid(), role: "assistant", content: `错误：${err}` });
          turnDoneRef.current = true;
          stoppingRef.current = false;
          setBusyState(false);
          void drainQueueSoon();
        },
        onAbort: () => {
          if (!isLiveListener(gen)) return;
          markSessionIdle(sessionIdRef.current || sessionId);
          const had = streamTextRef.current.trim();
          finalizeAssistant(had, { stopped: true });
          setToast(had ? t("stoppedKeep") : t("stopped"));
          stoppingRef.current = false;
          setBusyState(false);
        },
      },
      ac.signal,
      runMode,
      displayForApi,
      activeWs?.path || null,
      undefined,
      undefined,
      undefined,
      undefined,
      boundMcp,
      boundPlugins,
    );
    if (isLiveListener(gen) && sid) setSessionId(sid);
    // streamChat resolves after the turn ends (final / abort / error).
    // Re-marking running here would leave the history spinner stuck.
    if (isLiveListener(gen)) {
      markSessionIdle(sid || sessionIdRef.current || sessionId);
    }
  } catch (e) {
    if (!(e instanceof DOMException && e.name === "AbortError")) {
      markSessionIdle(sessionIdRef.current || sessionId);
      appendMsg({
        id: uid(),
        role: "assistant",
        content: `请求失败：${e instanceof Error ? e.message : String(e)}`,
      });
    }
  } finally {
    if (!isLiveListener(gen)) return;
    markSessionIdle(sessionIdRef.current || sessionId);
    abortRef.current = null;
    stoppingRef.current = false;
    setBusyState(false);
    setApproval(null);
    if (!askPendingRef.current) {
      setAskPrompt(null);
      setAskChoice("");
      setAskOtherText("");
    }
    if (!planPendingRef.current) {
      setPlanConfirm(null);
    }
    void drainQueueSoon();
  }
}

async function attachLive(sid: string) {
  if (!sid) return;
  const { gen, ac } = beginUiListener();
  stoppingRef.current = false;
  turnDoneRef.current = false;
  setBusyState(true);
  markSessionRunning(sid);
  try {
    await attachSessionEvents(
      sid,
      {
        onEvent: (ev) => {
          if (!isLiveListener(gen)) return;
          handleRuntimeEvent(ev, {
            ...toolUpsertCtx,
            t,
            locale,
            sessionId: sid,
            sessionIdRef,
            setPlanConfirm,
            setActivePlan,
            planPendingRef,
            executingPlanIdRef,
            patchSubagent,
            sealSubassistant,
            setLive,
            setSessionId,
            setCtx,
            setCompressState,
            appendStreamChunk,
            appendReasoningChunk,
            setApproval,
            findToolMsg,
            updateMsg,
            syncToolPanel,
            setDetail,
            stripDuplicateAskBubble,
            askPendingRef,
            setAskChoice,
            setAskOtherText,
            setAskPrompt,
            setFsRefresh,
            setSubs,
            appendMsg,
            commit,
            bumpStage,
          });
        },
        onFinal: (textOut, meta) => {
          if (meta.replay) {
            markSessionIdle(sid);
            if (!isLiveListener(gen)) return;
            void fetchSession(sid)
              .then((d) => loadTranscript(mapSessionMessages(d.messages, d.agent_tree)))
              .catch(() => {});
            return;
          }
          markSessionIdle(sid);
          void refreshSessionsRef.current();
          if (!isLiveListener(gen)) return;
          const stopped = Boolean(meta.cancelled);
          finalizeAssistant(textOut, {
            stopped,
            reasoning: String(meta.reasoning || ""),
          });
          alignCanvasFromSession(sid);
          setStats({
            tokens: Number(meta.tokens || 0),
            iters: Number(meta.iterations || 0),
          });
          syncCtxFromFinal(meta);
          void fetchSkills().then(setSkills);
          void fetchMemory().then(setMemory);
          setFsRefresh((n) => n + 1);
          stoppingRef.current = false;
          setBusyState(false);
          void drainQueueSoon();
        },
        onError: (err) => {
          markSessionIdle(sid);
          if (!isLiveListener(gen)) return;
          sealStreamBubble();
          appendMsg({ id: uid(), role: "assistant", content: `错误：${err}` });
          stoppingRef.current = false;
          setBusyState(false);
          void drainQueueSoon();
        },
        onAbort: () => {
          /* detached to another chat; server turn keeps running */
        },
      },
      ac.signal,
    );
  } catch (e) {
    if (!(e instanceof DOMException && e.name === "AbortError")) {
      markSessionIdle(sid);
      setToast(e instanceof Error ? e.message : String(e));
    }
  } finally {
    if (!isLiveListener(gen)) return;
    markSessionIdle(sid);
    if (abortRef.current === ac) abortRef.current = null;
    stoppingRef.current = false;
    setBusyState(false);
  }
}

function resumeFromSnapshot(detail: SessionDetail) {
  const ap = (detail.pending_approvals || [])[0];
  if (ap?.id) {
    setApproval({
      approvalId: String(ap.id),
      callId: String(ap.id),
      name: String(ap.tool || ""),
      args: ap.args,
      summary: String(ap.summary || ap.tool || ""),
    });
  } else {
    setApproval(null);
  }
  const ask = (detail.pending_asks || [])[0];
  if (ask?.id) {
    askPendingRef.current = true;
    setAskPrompt({
      askId: String(ask.id),
      callId: String(ask.id),
      sessionId: detail.id,
      question: String(ask.question || ""),
      options: Array.isArray(ask.options) ? ask.options : [],
      allowCustom: ask.allow_custom !== false,
      customLabel: String(ask.custom_label || ""),
      summary: String(ask.question || ""),
    });
  } else {
    askPendingRef.current = false;
    setAskPrompt(null);
  }
  const plan = (detail.pending_plans || [])[0];
  if (plan?.id) {
    planPendingRef.current = true;
    setPlanConfirm({
      planId: String(plan.id),
      sessionId: detail.id,
      summary: String(plan.summary || ""),
      tasks: (plan.tasks || []).map((task, i) => ({
        id: String(task.id || `task_${i}`),
        title: String(task.title || `步骤 ${i + 1}`),
        detail: String(task.detail || ""),
        status: (task.status as "pending") || "pending",
      })),
    });
  } else {
    planPendingRef.current = false;
    setPlanConfirm(null);
  }
  // The pinned plan panel is re-derived from the server on every (re)attach so
  // a plan from another session, or one whose transcript was deleted, cannot
  // linger above the composer.
  const live = detail.active_plan;
  if (detail.busy && live?.plan_id) {
    executingPlanIdRef.current = String(live.plan_id);
    setActivePlan({
      planId: String(live.plan_id),
      summary: String(live.summary || ""),
      mode: "agent",
      awaitingConfirm: false,
      shapeContract: null,
      tasks: (live.tasks || []).map((task, i) => ({
        id: String(task.id || `task_${i}`),
        title: String(task.title || `步骤 ${i + 1}`),
        detail: String(task.detail || ""),
        status: (task.status as PlanTask["status"]) || "pending",
      })),
    });
  } else {
    executingPlanIdRef.current = null;
    setActivePlan(null);
  }
  if (detail.busy) void attachLive(detail.id);
}

  const scheduleCtxRef = useRef<Parameters<typeof handleRuntimeEvent>[1] | null>(null);
  scheduleCtxRef.current = {
    ...toolUpsertCtx,
    t,
    locale,
    sessionId,
    sessionIdRef,
    setPlanConfirm,
    setActivePlan,
    planPendingRef,
    executingPlanIdRef,
    patchSubagent,
    sealSubassistant,
    setLive,
    setSessionId,
    setCtx,
    setCompressState,
    appendStreamChunk,
    appendReasoningChunk,
    setApproval,
    findToolMsg,
    updateMsg,
    syncToolPanel,
    setDetail,
    stripDuplicateAskBubble,
    askPendingRef,
    setAskChoice,
    setAskOtherText,
    setAskPrompt,
    setFsRefresh,
    setSubs,
    appendMsg,
    commit,
    bumpStage,
  };

  useEffect(() => {
    const onScheduleEvent = (e: Event) => {
      const ev = (e as CustomEvent<RuntimeEvent>).detail;
      const ctx = scheduleCtxRef.current;
      if (!ev || !ctx) return;
      const sid = String(ev.data?.session_id || "");
      const current = ctx.sessionIdRef.current || ctx.sessionId || "";
      if (!sid || sid !== current) return;
      if (ev.type === "schedule_trigger") {
        if (String(ev.data?.event || "") !== "started") return;
        streamIdRef.current = null;
        streamTextRef.current = "";
        streamReasoningRef.current = "";
        nativeReasoningRef.current = false;
        thinkSplitRef.current.reset();
        bumpStage();
        stickBottomRef.current = true;
        setBusyState(true);
        markSessionRunning(sid);
        const taskId = String(ev.data?.task_id || "task");
        appendMsg({
          id: `sched-user-${taskId}-${Math.floor(Number(ev.ts || Date.now()))}`,
          role: "user",
          content: `定时任务 · ${String(ev.data?.title || "").trim()}`,
        });
        return;
      }
      if (ev.type === "final") {
        const text = String(ev.data?.text || "");
        if (!streamTextRef.current.trim() && text.trim()) {
          if (streamIdRef.current) streamTextRef.current = text;
          else appendMsg({ id: uid(), role: "assistant", content: text });
        }
        sealStreamBubble();
        markSessionIdle(sid);
        setBusyState(false);
        void refreshSessionsRef.current();
        void drainQueueSoon();
        return;
      }
      if (ev.type === "error") {
        sealStreamBubble();
        appendMsg({
          id: uid(),
          role: "assistant",
          content: String(ev.data?.message || "定时任务执行失败"),
        });
        markSessionIdle(sid);
        setBusyState(false);
        return;
      }
      handleRuntimeEvent(ev, ctx);
    };
    window.addEventListener("sidekick-schedule-event", onScheduleEvent);
    return () => window.removeEventListener("sidekick-schedule-event", onScheduleEvent);
  }, []);

  return {
    transcriptRef,
    streamIdRef,
    streamTextRef,
    streamReasoningRef,
    nativeReasoningRef,
    thinkSplitRef,
    abortRef,
    stoppingRef,
    turnDoneRef,
    queuedRef,
    busyRef,
    commit: loadTranscript,
    appendMsg,
    updateMsg,
    syncToolPanel,
    dismissDetail,
    findToolMsg,
    setBusyState,
    setQueuedState,
    enqueueMessage,
    removeQueued,
    clearQueued,
    updateSubagentMsg,
    patchSubagent,
    sealSubassistant,
    discardStreamBubble,
    stripDuplicateAskBubble,
    sealStreamBubble,
    ensureStreamBubble,
    syncStreamBubble,
    appendStreamChunk,
    appendReasoningChunk,
    finalizeAssistant,
    stopChat,
    detachListener,
    upsertToolStart: (ev: RuntimeEvent) => upsertToolStart(ev, toolUpsertCtx),
    upsertToolDelta: (ev: RuntimeEvent) => upsertToolDelta(ev, toolUpsertCtx),
    upsertToolEnd: (ev: RuntimeEvent) => upsertToolEnd(ev, toolUpsertCtx),
    drainQueueSoon,
    sendChat,
    setSessionBinding,
    bindingRef,

    resumeFromSnapshot,
    runningSessionIds,
    reconcileRunningSessions,
  };
}
