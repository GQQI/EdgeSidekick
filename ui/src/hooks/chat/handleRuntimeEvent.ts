import type { RuntimeEvent } from "../../api";
import type { PlanTask, PlanTaskStatus } from "../../components/TaskPlanPanel";
import type { ActivePlan } from "../../components/TaskPlanPanel";
import {
  FS_MUTATING_TOOLS,
  type ApprovalPrompt,
  type AskOption,
  type AskPrompt,
  type ChatMsg,
  type DetailView,
  type LiveLine,
  type SubNode,
  type SubTool,
  type SubTranscriptItem,
  type ToolCard,
} from "../../types/chat";
import type { PlanConfirmState, ShapeContract } from "../../types/plan";
import { formatToolSummary } from "../../utils/toolSummary";
import { isFileMutatingTool } from "../../utils/diffPreview";
import { declaredDelegateSlotCount, findSubNode, isEphemeralCanvasId, sameSubagentSlot, softParseToolArgs, uid } from "../../utils/chatHelpers";
import { canvasGoalsMatch } from "../../utils/canvasSlots";
import type { MsgKey } from "../../i18n";
import { upsertToolDelta, upsertToolEnd, upsertToolStart, type ToolUpsertCtx } from "./toolUpserts";
import { applyCanvasTree, replaceStageSubagents } from "./canvasSync"
import { setAskEdges, upsertAskEdge } from "../../state/askEdgesStore";

function argsLookEmpty(args: unknown): boolean {
  if (args == null) return true;
  if (typeof args !== "object" || Array.isArray(args)) return false;
  const rec = args as Record<string, unknown>;
  return Object.keys(rec).every((k) => k.startsWith("_"));
}

function sealStreamingTools(transcript: SubTranscriptItem[]): SubTranscriptItem[] {
  return transcript.map((item) => {
    if (item.kind !== "tool") return item;
    const st = item.tool.status;
    if (st !== "streaming" && st !== "running" && st !== "pending") return item;
    return {
      ...item,
      tool: {
        ...item.tool,
        status: argsLookEmpty(item.tool.args) && !item.tool.result ? "error" : "done",
      },
    };
  });
}

export type RuntimeEventHandlerCtx = ToolUpsertCtx & {
  t: (key: MsgKey, ...args: string[]) => string;
  locale: string;
  sessionId: string | null;
  sessionIdRef: React.MutableRefObject<string | null>;
  setPlanConfirm: React.Dispatch<React.SetStateAction<PlanConfirmState | null>>;
  setActivePlan: React.Dispatch<React.SetStateAction<ActivePlan | null>>;
  planPendingRef: React.MutableRefObject<boolean>;
  executingPlanIdRef: React.MutableRefObject<string | null>;
  patchSubagent: (childId: string, fn: (s: SubNode) => SubNode, immediate?: boolean) => void;
  sealSubassistant: (transcript: SubTranscriptItem[]) => SubTranscriptItem[];
  setLive: React.Dispatch<React.SetStateAction<LiveLine[]>>;
  setSessionId: React.Dispatch<React.SetStateAction<string | null>>;
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
  appendStreamChunk: (chunk: string, reset?: boolean, discard?: boolean) => void;
  appendReasoningChunk: (chunk: string, reset?: boolean) => void;
  setApproval: React.Dispatch<React.SetStateAction<ApprovalPrompt | null>>;
  findToolMsg: (opts: {
    callId?: string;
    name?: string;
    statuses?: ToolCard["status"][];
  }) => ChatMsg | undefined;
  updateMsg: (id: string, patch: Partial<ChatMsg>, immediate?: boolean) => void;
  syncToolPanel: (tool: ToolCard, prevCallId?: string, immediate?: boolean) => void;
  setDetail: React.Dispatch<React.SetStateAction<DetailView>>;
  stripDuplicateAskBubble: (question: string) => void;
  askPendingRef: React.MutableRefObject<boolean>;
  setAskChoice: React.Dispatch<React.SetStateAction<string>>;
  setAskOtherText: React.Dispatch<React.SetStateAction<string>>;
  setAskPrompt: React.Dispatch<React.SetStateAction<AskPrompt | null>>;
  setFsRefresh: React.Dispatch<React.SetStateAction<number>>;
  setSubs: React.Dispatch<React.SetStateAction<SubNode[]>>;
  appendMsg: (msg: ChatMsg) => void;
  commit: (next: ChatMsg[], immediate?: boolean) => void;
  bumpStage: () => void;
};

function clipGoal(goal: string) {
  const text = (goal || "").replace(/\s+/g, " ").trim();
  return text.length > 24 ? `${text.slice(0, 23)}…` : text;
}

function pickPendingCanvasHost(messages: ChatMsg[], goal: string): ChatMsg | undefined {
  const pending = messages.filter(
    (m) => m.role === "subagent" && m.subagent && isEphemeralCanvasId(m.subagent.id),
  );
  if (!pending.length) return undefined;
  return (
    pending.find((m) => canvasGoalsMatch(m.subagent?.goal, goal)) || pending[0]
  );
}

function appendTopLevelSubagent(
  ctx: RuntimeEventHandlerCtx,
  node: SubNode,
  replay: boolean,
) {
  const existing = ctx.transcriptRef.current.find(
    (m) =>
      m.role === "subagent" &&
      m.subagent &&
      (m.subagent.id === node.id || sameSubagentSlot(m.subagent, node)),
  );
  if (existing?.subagent) {
    ctx.patchSubagent(existing.subagent.id, (s) => ({
      ...s,
      ...node,
      id: s.id,
      children: s.children,
      transcript:
        s.transcript?.length && !node.transcript.length ? s.transcript : node.transcript,
    }));
    return;
  }
  const isHelper = node.kind === "task" || (!node.kind && node.role === "leaf");
  if (isHelper) {
    const parties = ctx.transcriptRef.current.filter(
      (m) =>
        m.role === "subagent" &&
        m.subagent &&
        (m.subagent.kind === "party" || m.subagent.kind === "talk"),
    );
    if (parties.length >= 2) {
      const host =
        parties.find((m) => {
          const p = m.subagent!;
          const label = (p.role || "").trim();
          return Boolean(label) && (node.goal || "").includes(label);
        }) || parties[parties.length - 1];
      if (host.subagent) {
        ctx.patchSubagent(host.subagent.id, (s) => ({
          ...s,
          children: [...(s.children || []), { ...node, parent_id: s.id }],
        }));
        return;
      }
    }
  }
  const stage = ctx.stageRef.current;
  const declared = declaredDelegateSlotCount(ctx.transcriptRef.current, stage);
  const current = ctx.transcriptRef.current.filter(
    (m) => m.role === "subagent" && (m.stage ?? 0) === stage && m.subagent,
  );
  if (declared > 0 && current.length >= declared) {
    const host = pickPendingCanvasHost(ctx.transcriptRef.current, node.goal);
    if (host?.subagent) {
      ctx.patchSubagent(host.subagent.id, (s) => ({
        ...s,
        ...node,
        children: s.children,
        transcript:
          s.transcript?.length && !node.transcript.length ? s.transcript : node.transcript,
      }));
    }
    return;
  }
  const existingNodes = current
    .map((m) => m.subagent)
    .filter((n): n is SubNode => Boolean(n));
  replaceStageSubagents(ctx, [...existingNodes, node], stage);
  if (!replay && node.kind !== "party" && node.kind !== "talk") {
    ctx.setDetail({ type: "subagent", subagent: node });
  }
}

function parsePlanTasks(raw: unknown): PlanTask[] {
  if (typeof raw === "string") {
    const s = raw.trim();
    if (!s) return [];
    try {
      raw = JSON.parse(s);
    } catch {
      return [];
    }
  }
  if (!Array.isArray(raw)) return [];
  const out: PlanTask[] = [];
  raw.forEach((item, i) => {
    if (typeof item === "string") {
      const title = item.trim();
      if (!title) return;
      out.push({ id: `task_${i}`, title, status: "pending" });
      return;
    }
    const o = item as Record<string, unknown>;
    const title = String(o.title || o.name || "").trim();
    if (!title) return;
    out.push({
      id: String(o.id || `task_${i}`),
      title,
      detail: String(o.detail || o.description || "").trim() || undefined,
      status: String(o.status || "pending") as PlanTaskStatus,
    });
  });
  return out;
}

function planIsExecuting(plan: ActivePlan | null): boolean {
  if (!plan || plan.awaitingConfirm) return false;
  return plan.mode === "agent" && plan.tasks.length > 0;
}

function applyPlanStep(
  prev: ActivePlan | null,
  data: Record<string, unknown>,
  fallbackTitle: string,
): ActivePlan {
  const snapshot = parsePlanTasks(data.tasks);
  const taskId = String(data.task_id || "");
  const status = String(data.status || "running") as PlanTaskStatus;
  const index = Number(data.index);
  const title = String(data.title || "");
  const planId = String(data.plan_id || prev?.planId || "");
  const summary = String(data.summary || prev?.summary || title || fallbackTitle);

  let tasks: PlanTask[] = snapshot.length ? snapshot.map((t) => ({ ...t })) : [...(prev?.tasks || [])];

  const markHit = (list: PlanTask[]): PlanTask[] => {
    let hitIdx = taskId ? list.findIndex((t) => t.id === taskId) : -1;
    if (hitIdx < 0 && Number.isFinite(index)) hitIdx = index;
    if (hitIdx >= 0 && hitIdx < list.length) {
      const next = [...list];
      next[hitIdx] = {
        ...next[hitIdx],
        status,
        title: title || next[hitIdx].title,
      };
      return next;
    }
    const row: PlanTask = {
      id: taskId || `task_${Number.isFinite(index) ? index : list.length}`,
      title: title || `步骤 ${(Number.isFinite(index) ? index : list.length) + 1}`,
      status,
    };
    if (Number.isFinite(index) && index >= list.length) {
      const padded = [...list];
      while (padded.length < index) {
        padded.push({
          id: `task_${padded.length}`,
          title: `步骤 ${padded.length + 1}`,
          status: "pending",
        });
      }
      padded.push(row);
      return padded;
    }
    return [...list, row];
  };

  tasks = markHit(tasks);

  if (status === "running" && Number.isFinite(index) && index > 0) {
    tasks = tasks.map((t, i) =>
      i < index && (t.status === "pending" || t.status === "running")
        ? { ...t, status: "done" as const }
        : t,
    );
  }

  return {
    planId,
    summary,
    mode: "agent",
    awaitingConfirm: false,
    shapeContract: prev?.shapeContract || null,
    tasks,
  };
}

function parseShapeContract(raw: unknown): ShapeContract | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const out: ShapeContract = {};
  for (const key of [
    "reuse",
    "create_only_if",
    "config_placement",
    "control_flow",
    "why_not_smaller",
    "verify_command",
  ] as const) {
    const v = String(o[key] || "").trim();
    if (v) out[key] = v;
  }
  return Object.keys(out).length ? out : null;
}

export function handleRuntimeEvent(ev: RuntimeEvent, ctx: RuntimeEventHandlerCtx): void {
  const type = ev.type;

  if (type === "browser_open") {
    const url = String(ev.data?.url || "").trim();
    const revealOnly = Boolean(ev.data?.reveal_only) || !url;
    const evSid = String(ev.data?.session_id || "");
    const activeSid = String(ctx.sessionIdRef.current || ctx.sessionId || "");
    // Ignore browser pops from a background session the user is not viewing.
    if (evSid && activeSid && evSid !== activeSid) {
      return;
    }
    if (typeof window !== "undefined") {
      // Empty / reveal_only: open the panel host only — never re-loadURL a
      // stale homepage after the guest has already moved deeper.
      window.dispatchEvent(
        new CustomEvent("sidekick-browser-open", {
          detail: { url: revealOnly ? "" : url, revealOnly },
        }),
      );
    }
  }

  if (type === "schedule_trigger") {
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("sidekick-schedule", { detail: ev.data || {} }));
    }
  }

  if (type === "shell_job") {
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("sidekick-shell-job", { detail: ev.data || {} }));
    }
    const jobId = String(ev.data?.job_id || "");
    const status = String(ev.data?.status || "");
    const phase = String(ev.data?.phase || "");
    const message = String(ev.data?.message || "").trim();
    if (
      jobId &&
      message &&
      (phase === "done" || status === "exited" || status === "killed")
    ) {
      ctx.appendMsg({
        id: `job-done-${jobId}`,
        role: "assistant",
        content: message,
        jobNotice: {
          job_id: jobId,
          status,
          exit_code:
            typeof ev.data?.exit_code === "number" ? (ev.data.exit_code as number) : null,
        },
      });
    }
  }

  // The pinned plan panel belongs to the root agent only. A subagent's plan
  // (parent_id set) must not overwrite or clear it; those still flow into the
  // subagent transcript below.
  const isRootPlanEvent = !ev.parent_id;

  if (type === "plan_created" && isRootPlanEvent) {
    const tasks = parsePlanTasks(ev.data.tasks);
    const summary = String(ev.data.summary || "");
    const planId = String(ev.data.plan_id || "");
    const shapeContract = parseShapeContract(ev.data.shape_contract);
    const awaiting =
      Boolean(ev.data.awaiting_confirm) || ev.data.mode === "plan";
    if (awaiting) {
      if (ctx.executingPlanIdRef.current && ctx.executingPlanIdRef.current === planId) {
        return;
      }
      const planSid = String(
        ev.data.session_id || ctx.sessionIdRef.current || ctx.sessionId || "",
      );
      ctx.planPendingRef.current = true;
      ctx.setPlanConfirm({
        planId,
        sessionId: planSid,
        summary,
        tasks,
        shapeContract,
      });
      ctx.setActivePlan((prev) => (planIsExecuting(prev) ? prev : null));
    } else if (tasks.length) {
      ctx.executingPlanIdRef.current = planId || ctx.executingPlanIdRef.current;
      ctx.setActivePlan((prev) => {
        // Only carry statuses over when this is the same plan being re-announced
        // (e.g. after confirm). A different plan id replaces the panel outright,
        // otherwise a stale plan's ticks bleed into the new one.
        const samePlan = Boolean(prev && planId && prev.planId === planId);
        const byId = new Map(
          samePlan ? (prev?.tasks || []).map((t) => [t.id, t.status]) : [],
        );
        return {
          planId,
          summary: summary || (samePlan ? prev?.summary : "") || "",
          mode: "agent",
          awaitingConfirm: false,
          shapeContract: shapeContract || (samePlan ? prev?.shapeContract : null) || null,
          tasks: tasks.map((t, i) => ({
            ...t,
            status:
              byId.get(t.id) ??
              (samePlan ? prev?.tasks[i]?.status : undefined) ??
              t.status ??
              "pending",
          })),
        };
      });
    }
  }
  if (type === "plan_confirm_request" && isRootPlanEvent) {
    const planId = String(ev.data.plan_id || "");
    if (ctx.executingPlanIdRef.current && ctx.executingPlanIdRef.current === planId) {
      return;
    }
    const tasks = parsePlanTasks(ev.data.tasks);
    const summary = String(ev.data.summary || "");
    const shapeContract = parseShapeContract(ev.data.shape_contract);
    const planSid = String(
      ev.data.session_id || ctx.sessionIdRef.current || ctx.sessionId || "",
    );
    ctx.planPendingRef.current = true;
    ctx.setPlanConfirm({
      planId,
      sessionId: planSid,
      summary,
      tasks,
      shapeContract,
    });
    ctx.setActivePlan((prev) => (planIsExecuting(prev) ? prev : null));
  }
  if (type === "plan_confirm_resolved" && isRootPlanEvent) {
    ctx.planPendingRef.current = false;
    ctx.setPlanConfirm((cur) =>
      cur && cur.planId === String(ev.data.plan_id || "") ? null : cur,
    );
    if (ev.data.approved) {
      ctx.executingPlanIdRef.current = String(ev.data.plan_id || "") || ctx.executingPlanIdRef.current;
    } else {
      ctx.executingPlanIdRef.current = null;
      ctx.setActivePlan(null);
    }
  }
  if (type === "plan_step" && isRootPlanEvent) {
    const planId = String(ev.data.plan_id || "");
    if (planId) ctx.executingPlanIdRef.current = planId;
    ctx.setPlanConfirm(null);
    ctx.setActivePlan((prev) => {
      // A step for a different plan than the one pinned means the panel is
      // stale (e.g. leftover from a deleted turn) — start fresh from the
      // event's own task snapshot instead of padding the old list.
      const base = prev && planId && prev.planId && prev.planId !== planId ? null : prev;
      return applyPlanStep(base, ev.data, ctx.t("taskPlanTitle"));
    });
  }
  if (type === "plan_done" && isRootPlanEvent) {
    ctx.planPendingRef.current = false;
    ctx.executingPlanIdRef.current = null;
    ctx.setPlanConfirm(null);
    ctx.setActivePlan(null);
  }

  // Subagent events share the bus — accumulate into subagent transcript.
  // Gate events (approval / ask / nested spawn) must still reach the main UI;
  // otherwise a leaf that needs confirmation stays "running" forever.
  const childGateTypes = new Set([
    "approval_request",
    "approval_resolved",
    "ask_request",
    "ask_resolved",
    "plan_created",
    "plan_confirm_request",
    "plan_confirm_resolved",
    "plan_step",
    "plan_done",
    "subagent_start",
    "subagent_end",
    "canvas_sync",
    "cancelled",
    "error",
    "final",
  ]);
  if (ev.parent_id) {
    const childId = String(ev.agent_id || "");
    if (childId && type === "assistant_delta") {
      const reset = Boolean(ev.data.reset);
      const chunk = String(ev.data.chunk ?? ev.data.text ?? "");
      ctx.patchSubagent(childId, (s) => {
        let tr = [...(s.transcript || [])];
        if (reset) tr = ctx.sealSubassistant(tr);
        // Prefer the last assistant item even if a tool card sits after it
        // (models may interleave content + tool_call deltas).
        let streamIdx = -1;
        for (let i = tr.length - 1; i >= 0; i--) {
          const item = tr[i];
          if (item.kind === "tool") continue;
          if (item.kind === "assistant") {
            streamIdx = i;
            break;
          }
          break;
        }
        if (!reset && streamIdx >= 0) {
          const last = tr[streamIdx];
          if (last.kind === "assistant") {
            tr[streamIdx] = {
              ...last,
              text: last.text + chunk,
              streaming: true,
              turnAt: last.turnAt ?? Date.now(),
            };
          }
        } else if (chunk || reset) {
          tr = ctx.sealSubassistant(tr);
          tr.push({
            id: uid(),
            kind: "assistant",
            text: chunk,
            streaming: true,
            turnAt: Date.now(),
          });
        }
        return {
          ...s,
          transcript: tr,
          activity: chunk.trim() ? "正在生成…" : ctx.t("thinkingActivity"),
        };
      });
    } else if (childId && type === "assistant_reasoning_delta") {
      const chunk = String(ev.data.chunk ?? "");
      ctx.patchSubagent(childId, (s) => {
        let tr = [...(s.transcript || [])];
        const last = tr[tr.length - 1];
        if (last?.kind === "assistant" && last.streaming) {
          tr[tr.length - 1] = {
            ...last,
            reasoning: (last.reasoning || "") + chunk,
            reasoningStreaming: !last.text.trim(),
          };
        } else {
          tr = ctx.sealSubassistant(tr);
          tr.push({
            id: uid(),
            kind: "assistant",
            text: "",
            reasoning: chunk,
            streaming: true,
            reasoningStreaming: true,
            turnAt: Date.now(),
          });
        }
        return { ...s, transcript: tr, activity: ctx.t("thinkingActivity") };
      });
    } else if (childId && type === "assistant_status") {
      const text = String(ev.data.text || "");
      ctx.patchSubagent(childId, (s) => ({
        ...s,
        transcript: ctx.sealSubassistant(s.transcript || []),
        activity: text || s.activity,
      }));
    } else if (childId && type === "tool_call_delta") {
      const callId = String(ev.data.id || `stream_${ev.data.index ?? 0}`);
      const name = String(ev.data.name || "");
      const argsRaw = String(ev.data.arguments || "");
      const args = softParseToolArgs(argsRaw);
      const summary = formatToolSummary(name, args);
      ctx.patchSubagent(childId, (s) => {
        let tr = [...(s.transcript || [])];
        const idx = tr.findIndex((x) => {
          if (x.kind !== "tool") return false;
          const live =
            x.tool.status === "streaming" ||
            x.tool.status === "running" ||
            x.tool.status === "pending";
          if (x.tool.callId === callId && live) return true;
          return live && x.tool.status === "streaming" && x.tool.name === name;
        });
        // Seal assistant only when the first tool delta of this call arrives
        if (idx < 0) tr = ctx.sealSubassistant(tr);
        const tool: SubTool = {
          id: idx >= 0 && tr[idx].kind === "tool" ? tr[idx].tool.id : uid(),
          callId,
          name: name || (idx >= 0 && tr[idx].kind === "tool" ? tr[idx].tool.name : "tool"),
          summary,
          status: "streaming",
          args,
        };
        if (idx >= 0) tr[idx] = { id: tool.id, kind: "tool", tool };
        else tr.push({ id: tool.id, kind: "tool", tool });
        return { ...s, transcript: tr, activity: summary ? `正在 ${summary}` : ctx.t("agentCanvasWorking") };
      });
    } else if (childId && type === "tool_start") {
      const callId = String(ev.data.call_id || uid());
      const name = String(ev.data.name || "tool");
      const summary =
        String(ev.data.summary || "") ||
        formatToolSummary(name, ev.data.args);
      ctx.patchSubagent(childId, (s) => {
        let tr = ctx.sealSubassistant(s.transcript || []);
        const idx = tr.findIndex((x) => {
          if (x.kind !== "tool") return false;
          const live =
            x.tool.status === "streaming" ||
            x.tool.status === "running" ||
            x.tool.status === "pending";
          if (x.tool.callId === callId && live) return true;
          return (
            x.tool.status === "streaming" &&
            (x.tool.name === name || !x.tool.name)
          );
        });
        const tool: SubTool = {
          id: idx >= 0 && tr[idx].kind === "tool" ? tr[idx].tool.id : uid(),
          callId,
          name,
          summary,
          status: "running",
          args: ev.data.args,
        };
        if (idx >= 0) tr[idx] = { id: tool.id, kind: "tool", tool };
        else tr.push({ id: tool.id, kind: "tool", tool });
        return { ...s, transcript: tr, activity: summary ? `正在 ${summary}` : ctx.t("agentCanvasWorking") };
      });
    } else if (childId && type === "tool_end") {
      const callId = String(ev.data.call_id || "");
      const name = String(ev.data.name || "tool");
      const ok = Boolean(ev.data.ok !== false);
      const result = String(ev.data.result ?? ev.data.preview ?? "");
      const summary =
        String(ev.data.summary || "") ||
        formatToolSummary(name, ev.data.args);
      ctx.patchSubagent(childId, (s) => {
        const tr = [...(s.transcript || [])];
        const idx = tr.findIndex((x) => {
          if (x.kind !== "tool") return false;
          if (x.tool.callId === callId) {
            return (
              x.tool.status === "running" ||
              x.tool.status === "streaming" ||
              x.tool.status === "pending"
            );
          }
          return (
            x.tool.name === name &&
            (x.tool.status === "running" || x.tool.status === "streaming")
          );
        });
        const prevArgs =
          idx >= 0 && tr[idx].kind === "tool" ? tr[idx].tool.args : undefined;
        const nextArgs = argsLookEmpty(ev.data.args) ? prevArgs : ev.data.args;
        const tool: SubTool = {
          id: idx >= 0 && tr[idx].kind === "tool" ? tr[idx].tool.id : uid(),
          callId: callId || uid(),
          name,
          summary,
          status: ok ? "done" : "error",
          args: nextArgs,
          result,
        };
        if (idx >= 0) tr[idx] = { id: tool.id, kind: "tool", tool };
        else tr.push({ id: tool.id, kind: "tool", tool });
        for (let i = 0; i < tr.length; i++) {
          const row = tr[i];
          if (row.kind !== "tool" || row.tool.id === tool.id) continue;
          if (row.tool.name !== name) continue;
          if (row.tool.status !== "streaming" && row.tool.status !== "running") continue;
          tr[i] = {
            ...row,
            tool: { ...row.tool, status: ok ? "done" : "error", result: row.tool.result || result },
          };
        }
        return {
          ...s,
          transcript: tr,
          activity: ok ? `${name} 完成` : `${name} 失败`,
        };
      }, true);
      if (FS_MUTATING_TOOLS.has(name)) {
        ctx.setFsRefresh((n) => n + 1);
      }
    }
    if (!childGateTypes.has(type)) {
      if (
        type !== "assistant_delta" &&
        type !== "assistant_reasoning_delta" &&
        type !== "tool_call_delta" &&
        type !== "context_usage" &&
        type !== "llm_start"
      ) {
        const label =
          (ev.data.message as string) ||
          `${type}${ev.data.name ? " " + ev.data.name : ""}`;
        ctx.setLive((prev) =>
          [...prev, { id: uid(), text: `[子] ${label}`, kind: type }].slice(-120),
        );
      }
      return;
    }
  }

  if (type === "session") {
    const sid = String(ev.data.session_id || "");
    if (sid) {
      ctx.sessionIdRef.current = sid;
      ctx.setSessionId(sid);
    }
  }

  if (type === "context_usage" || type === "llm_start") {
    const budget = (ev.data.budget || {}) as Record<string, unknown>;
    ctx.setCtx((c) => {
      const tokens = Number(ev.data.tokens ?? budget.tokens_est ?? c.tokens);
      const limit = Number(ev.data.limit ?? c.limit);
      return {
        tokens: Number.isFinite(tokens) ? tokens : c.tokens,
        limit: Number.isFinite(limit) && limit > 0 ? limit : c.limit,
      };
    });
  }

  if (type === "compress_start" || type === "compress_progress") {
    const blocking = ev.data.blocking !== false && ev.data.background !== true;
    if (blocking) {
      ctx.setCompressState({
        active: true,
        message: String(ev.data.message || "正在快速压缩上下文…"),
        attempt: Number(ev.data.attempt || 0),
        maxAttempts: Number(ev.data.max_attempts || 1),
        before: Number(ev.data.before || ev.data.tokens || 0),
      });
    }
    ctx.setCtx((c) => ({
      tokens: Number(ev.data.tokens ?? c.tokens),
      limit: Number(ev.data.limit ?? c.limit),
    }));
  }

  if (type === "compress") {
    const after = Number(ev.data.after || 0);
    const before = Number(ev.data.before || 0);
    const blocking = ev.data.blocking !== false && ev.data.background !== true;
    if (blocking) {
      ctx.setCompressState({
        active: true,
        message: String(ev.data.message || `上下文已重置 ${before}→${after}`),
        attempt: Number((ev.data.meta as { attempts?: number } | undefined)?.attempts || 0),
        maxAttempts: Number(ev.data.max_attempts || 1),
        before,
        after,
      });
      window.setTimeout(() => ctx.setCompressState(null), 1600);
    } else {
      ctx.setCompressState(null);
    }
    ctx.setCtx((c) => ({
      tokens: after || Number(ev.data.tokens || 0),
      limit: Number(ev.data.limit || c.limit),
    }));
  }

  if (type === "assistant_delta") {
    const reset = Boolean(ev.data.reset);
    const discard = Boolean(ev.data.discard);
    const chunk = String(ev.data.chunk ?? ev.data.text ?? "");
    ctx.appendStreamChunk(chunk, reset, discard);
    return;
  }

  if (type === "assistant_reasoning_delta") {
    const chunk = String(ev.data.chunk ?? ev.data.text ?? "");
    ctx.appendReasoningChunk(chunk, Boolean(ev.data.reset));
    return;
  }

  if (type === "assistant_status" && ev.data.stalled) {
    ctx.sealStreamBubble();
  }

  if (type === "tool_call_delta") {
    upsertToolDelta(ev, ctx);
    return;
  }

  if (type === "approval_request") {
    ctx.setApproval({
      approvalId: String(ev.data.approval_id || ""),
      callId: String(ev.data.call_id || ""),
      name: String(ev.data.name || "tool"),
      args: ev.data.args,
      summary: String(ev.data.summary || ev.data.message || ""),
    });
    const callId = String(ev.data.call_id || "");
    const hit =
      ctx.findToolMsg({ callId }) ||
      ctx.findToolMsg({
        name: String(ev.data.name || ""),
        statuses: ["streaming", "running", "pending"],
      });
    if (hit?.tool) {
      const prevCallId = hit.tool.callId;
      const tool: ToolCard = {
        ...hit.tool,
        callId: callId || hit.tool.callId,
        status: "pending",
        args: ev.data.args ?? hit.tool.args,
        summary: String(ev.data.summary || ""),
      };
      ctx.updateMsg(hit.id, { tool });
      ctx.syncToolPanel(tool, prevCallId);
      // Don't steal the detail panel if the user is inspecting another tool/file.
      if (isFileMutatingTool(tool.name)) {
        ctx.setDetail((d) => {
          if (d == null) return { type: "tool", tool };
          if (d.type !== "tool") return d;
          const same =
            d.tool.id === tool.id ||
            (Boolean(tool.callId) && d.tool.callId === tool.callId);
          return same ? { type: "tool", tool } : d;
        });
      }
    }
  }
  if (type === "approval_resolved") {
    ctx.setApproval((cur) =>
      cur && cur.approvalId === String(ev.data.approval_id || "") ? null : cur,
    );
  }

  if (type === "ask_request") {
    ctx.sealStreamBubble();
    if (!ev.parent_id) ctx.bumpStage();
    const rawOpts = Array.isArray(ev.data.options) ? ev.data.options : [];
    const options: AskOption[] = rawOpts
      .map((o) => {
        if (!o || typeof o !== "object") return null;
        const rec = o as Record<string, unknown>;
        const key = String(rec.key || "").trim();
        const label = String(rec.label || "").trim();
        if (!key || !label) return null;
        return { key, label };
      })
      .filter((o): o is AskOption => Boolean(o));
    const question = String(ev.data.question || "");
    const allowCustom = ev.data.allow_custom !== false;
    const customLabel = String(
      ev.data.custom_label || (ctx.locale === "en" ? "Other (type your answer)" : "其他（请补充）"),
    );
    ctx.stripDuplicateAskBubble(question);
    const askSid = String(
      ev.data.session_id || ctx.sessionIdRef.current || ctx.sessionId || "",
    );
    ctx.askPendingRef.current = true;
    ctx.setAskChoice("");
    ctx.setAskOtherText("");
    ctx.setAskPrompt({
      askId: String(ev.data.ask_id || ""),
      callId: String(ev.data.call_id || ""),
      sessionId: askSid,
      question,
      options,
      allowCustom,
      customLabel,
      summary: String(ev.data.summary || ev.data.message || ""),
    });
  }
  if (type === "ask_resolved") {
    ctx.askPendingRef.current = false;
    ctx.setAskPrompt((cur) =>
      cur && cur.askId === String(ev.data.ask_id || "") ? null : cur,
    );
    ctx.setAskChoice("");
    ctx.setAskOtherText("");
  }

  if (type === "tool_start") {
    upsertToolStart(ev, ctx);
  }
  if (type === "tool_end") {
    upsertToolEnd(ev, ctx);
    const toolName = String(ev.data.name || "");
    if (FS_MUTATING_TOOLS.has(toolName)) {
      ctx.setFsRefresh((n) => n + 1);
    }
    if (
      (toolName === "create_scheduled_task" || toolName === "cancel_scheduled_task") &&
      ev.data?.ok !== false &&
      !ev.data?.error &&
      typeof window !== "undefined"
    ) {
      window.dispatchEvent(new CustomEvent("sidekick-schedule", { detail: ev.data || {} }));
    }
  }

  if (type === "subagent_start") {
    ctx.sealStreamBubble();
    const party = String(ev.data.party || "");
    const label = String(ev.data.label || party || "").trim();
    const goal = String(ev.data.goal || label).trim();
    const replay = Boolean(ev.data.replay);
    const replayItems = Array.isArray(ev.data.transcript)
      ? (ev.data.transcript as Array<Record<string, unknown>>)
      : [];
    const transcript: SubTranscriptItem[] = replayItems.flatMap((item): SubTranscriptItem[] => {
      if (!item || typeof item !== "object") return [];
      if (item.kind === "tool") {
        const name = String(item.name || "tool");
        return [
          {
            id: uid(),
            kind: "tool" as const,
            tool: {
              id: uid(),
              callId: String(item.call_id || uid()),
              name,
              summary: name,
              status: "done" as const,
              result: String(item.result || ""),
            },
          },
        ];
      }
      const text = String(item.text || item.content || "").trim();
      const reasoning = String(item.reasoning || "").trim();
      if (!text && !reasoning) return [];
      return [
        {
          id: uid(),
          kind: "assistant" as const,
          text,
          reasoning: reasoning || undefined,
        },
      ];
    });
    const node: SubNode = {
      id: String(ev.data.child_id),
      goal,
      status: "running",
      role: party || String(ev.data.role || "leaf"),
      kind:
        ev.data.kind === "party" || ev.data.kind === "talk" || ev.data.kind === "task"
          ? ev.data.kind
          : party
            ? "party"
            : "task",
      parent_id: String(ev.parent_id || ev.data.parent_id || ""),
      activity: String(ev.data.activity || ctx.t("thinkingActivity")),
      transcript,
    };
    const spawnerId = String(ev.agent_id || "");
    const already = ctx.transcriptRef.current.find(
      (m) => m.role === "subagent" && m.subagent && findSubNode(m.subagent, node.id),
    );
    const pendingHost = !already
      ? pickPendingCanvasHost(ctx.transcriptRef.current, goal)
      : undefined;
    if (already?.subagent) {
      ctx.patchSubagent(node.id, (s) => ({
        ...s,
        ...node,
        children: s.children,
        transcript:
          s.transcript?.length && !transcript.length
            ? s.transcript
            : transcript.length
              ? transcript
              : s.transcript,
      }));
    } else if (pendingHost?.subagent) {
      ctx.patchSubagent(pendingHost.subagent.id, (s) => ({
        ...s,
        ...node,
        children: s.children,
        transcript:
          s.transcript?.length && !transcript.length
            ? s.transcript
            : transcript.length
              ? transcript
              : s.transcript,
      }));
    } else if (ev.parent_id && spawnerId) {
      const host =
        ctx.transcriptRef.current.find(
          (m) =>
            m.role === "subagent" &&
            m.subagent &&
            findSubNode(m.subagent, spawnerId),
        ) ||
        ctx.transcriptRef.current.find(
          (m) =>
            m.role === "subagent" &&
            m.subagent &&
            findSubNode(m.subagent, String(ev.parent_id)),
        );
      const attachId = host?.subagent
        ? findSubNode(host.subagent, spawnerId)
          ? spawnerId
          : String(ev.parent_id)
        : "";
      if (host?.subagent && attachId) {
        ctx.patchSubagent(attachId, (s) => {
          const kids = [...(s.children || [])];
          const idx = kids.findIndex((k) => k.id === node.id);
          if (idx >= 0) {
            const prev = kids[idx];
            kids[idx] = {
              ...prev,
              ...node,
              children: prev.children,
              transcript:
                prev.transcript?.length && !node.transcript.length
                  ? prev.transcript
                  : node.transcript.length
                    ? node.transcript
                    : prev.transcript,
            };
          } else {
            kids.push(node);
          }
          return {
            ...s,
            children: kids,
            activity: s.status === "running" ? `正在调度 · ${clipGoal(node.goal)}` : s.activity,
          };
        });
      } else {
        appendTopLevelSubagent(ctx, node, replay);
      }
    } else {
      appendTopLevelSubagent(ctx, node, replay);
    }
  }
  if (type === "canvas_sync") {
    const turn = Number(ev.data.turn);
    applyCanvasTree(ctx, ev.data.tree, Number.isFinite(turn) && turn > 0 ? turn : undefined);
    const asks = (ev.data as { asks?: unknown }).asks;
    if (Array.isArray(asks)) {
      setAskEdges(
        asks
          .filter((x): x is Record<string, unknown> => !!x && typeof x === "object")
          .map((x) => ({
            id: String(x.id || ""),
            from_id: String(x.from_id || ""),
            to_id: String(x.to_id || ""),
            kind: String(x.kind || "consult"),
            what: x.what ? String(x.what) : undefined,
            how: x.how ? String(x.how) : undefined,
            who: x.who ? String(x.who) : undefined,
            when: x.when ? String(x.when) : undefined,
            why: x.why ? String(x.why) : undefined,
            need: x.need ? String(x.need) : undefined,
            goal_slice: x.goal_slice ? String(x.goal_slice) : undefined,
            visibility:
              x.visibility && typeof x.visibility === "object"
                ? (x.visibility as { share?: string[]; redact?: string[]; quote_policy?: string; relation?: string })
                : undefined,
            status: x.status ? String(x.status) : undefined,
            summary: x.summary ? String(x.summary) : undefined,
          }))
          .filter((e) => e.id || e.need || e.to_id),
      );
    }
    return;
  }

  if (type === "ask_edge") {
    const edge = (ev.data as { edge?: Record<string, unknown> }).edge;
    const asks = (ev.data as { asks?: unknown }).asks;
    if (Array.isArray(asks)) {
      setAskEdges(
        asks
          .filter((x): x is Record<string, unknown> => !!x && typeof x === "object")
          .map((x) => ({
            id: String(x.id || ""),
            from_id: String(x.from_id || ""),
            to_id: String(x.to_id || ""),
            kind: String(x.kind || "consult"),
            what: x.what ? String(x.what) : undefined,
            how: x.how ? String(x.how) : undefined,
            who: x.who ? String(x.who) : undefined,
            when: x.when ? String(x.when) : undefined,
            why: x.why ? String(x.why) : undefined,
            need: x.need ? String(x.need) : undefined,
            goal_slice: x.goal_slice ? String(x.goal_slice) : undefined,
            visibility:
              x.visibility && typeof x.visibility === "object"
                ? (x.visibility as { share?: string[]; redact?: string[]; quote_policy?: string; relation?: string })
                : undefined,
            status: x.status ? String(x.status) : undefined,
            summary: x.summary ? String(x.summary) : undefined,
          })),
      );
    } else if (edge && typeof edge === "object") {
      upsertAskEdge({
        id: String(edge.id || ""),
        from_id: String(edge.from_id || ""),
        to_id: String(edge.to_id || ""),
        kind: String(edge.kind || "consult"),
        what: edge.what ? String(edge.what) : undefined,
        how: edge.how ? String(edge.how) : undefined,
        who: edge.who ? String(edge.who) : undefined,
        when: edge.when ? String(edge.when) : undefined,
        why: edge.why ? String(edge.why) : undefined,
        need: edge.need ? String(edge.need) : undefined,
        goal_slice: edge.goal_slice ? String(edge.goal_slice) : undefined,
        visibility:
          edge.visibility && typeof edge.visibility === "object"
            ? (edge.visibility as { share?: string[]; redact?: string[]; quote_policy?: string; relation?: string })
            : undefined,
        status: edge.status ? String(edge.status) : undefined,
        summary: edge.summary ? String(edge.summary) : undefined,
      });
    }
    return;
  }

  if (type === "subagent_end") {
    const childId = String(ev.data.child_id);
    const summary = String(ev.data.summary || "");
    const cancelled = Boolean(ev.data.cancelled);
    const ok = !cancelled && !summary.startsWith("ERROR");
    ctx.patchSubagent(childId, (s) => ({
      ...s,
      status: ok ? "done" : "error",
      summary: cancelled ? summary || "（已停止）" : summary,
      activity: undefined,
      transcript: ctx.sealSubassistant(sealStreamingTools(s.transcript || [])),
    }), true);
  }

  if (
    type === "assistant_delta" ||
    type === "assistant_reasoning_delta" ||
    type === "tool_call_delta" ||
    type === "context_usage" ||
    type === "llm_start"
  ) {
    return;
  }

  const label =
    (ev.data.message as string) ||
    `${type}${ev.data.name ? " " + ev.data.name : ""}`;
  ctx.setLive((prev) =>
    [...prev, { id: uid(), text: label, kind: type }].slice(-120),
  );
}
