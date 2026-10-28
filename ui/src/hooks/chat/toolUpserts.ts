import type { RuntimeEvent } from "../../api";
import type { ChatMsg, DetailView, SubNode, ToolCard } from "../../types/chat";
import { formatToolSummary } from "../../utils/toolSummary";
import { softParseToolArgs, uid, writeFilePreview } from "../../utils/chatHelpers";
import { seedDelegateCanvas } from "./canvasSync";

export type ToolUpsertCtx = {
  sealStreamBubble: () => void;
  /** Seal only if the bubble already has text/reasoning; otherwise keep it
   * open so reasoning that streams AFTER the first tool-arg delta still lands
   * in the bubble above the tool chip instead of being dropped. */
  sealStreamBubbleIfContent: () => void;
  findToolMsg: (opts: {
    callId?: string;
    name?: string;
    statuses?: ToolCard["status"][];
  }) => ChatMsg | undefined;
  updateMsg: (id: string, patch: Partial<ChatMsg>, immediate?: boolean) => void;
  syncToolPanel: (tool: ToolCard, prevCallId?: string, immediate?: boolean) => void;
  queueDetail: (updater: (d: DetailView) => DetailView, immediate?: boolean) => void;
  appendMsg: (msg: ChatMsg) => void;
  removeMsg: (id: string) => void;
  commit: (next: ChatMsg[], immediate?: boolean) => void;
  setSubs: React.Dispatch<React.SetStateAction<SubNode[]>>;
  setDetail: React.Dispatch<React.SetStateAction<DetailView>>;
  transcriptRef: React.MutableRefObject<ChatMsg[]>;
  stageRef: React.MutableRefObject<number>;
};

const LIVE_TOOL: ToolCard["status"][] = ["streaming", "running", "pending"];

function taskIdentity(row: unknown): string {
  if (typeof row === "string") return row.trim();
  if (row && typeof row === "object" && !Array.isArray(row)) {
    const rec = row as Record<string, unknown>;
    return String(
      rec.goal || rec.task || rec.query || rec.prompt || rec.description || "",
    ).trim();
  }
  return "";
}

function isTaskListPrefix(prefix: unknown[], full: unknown[]): boolean {
  if (prefix.length > full.length) return false;
  return prefix.every((row, i) => {
    const left = taskIdentity(row);
    const right = taskIdentity(full[i]);
    return !left || !right || left === right;
  });
}

function mergeDelegateTaskLists(prior: unknown[], next: unknown[]): unknown[] {
  if (!next.length) return prior;
  if (!prior.length) return next;
  if (next.length >= prior.length && isTaskListPrefix(prior, next)) return next;
  if (prior.length >= next.length && isTaskListPrefix(next, prior)) return prior;
  const seen = new Set<string>();
  const out: unknown[] = [];
  for (const row of [...prior, ...next]) {
    const key = taskIdentity(row) || JSON.stringify(row);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(row);
  }
  return out;
}

function mergeDelegateArgs(current: unknown, incoming: unknown): unknown {
  const prior = current && typeof current === "object" && !Array.isArray(current)
    ? current as Record<string, unknown>
    : {};
  const next = incoming && typeof incoming === "object" && !Array.isArray(incoming)
    ? incoming as Record<string, unknown>
    : {};
  const priorTasks = Array.isArray(prior.tasks) ? prior.tasks : [];
  const nextTasks = Array.isArray(next.tasks) ? next.tasks : [];
  const tasks = mergeDelegateTaskLists(priorTasks, nextTasks);
  return { ...prior, ...next, ...(tasks.length ? { tasks } : {}) };
}

/**
 * Refresh write_file (or any streaming tool) detail only if the user already
 * has that tool open. Never auto-open after they dismissed the panel — stream
 * chunks must not bring the right detail back.
 */
function revealWriteFileDetail(
  ctx: ToolUpsertCtx,
  tool: ToolCard,
  immediate = true,
) {
  ctx.queueDetail((d) => {
    if (d == null) return d;
    if (d.type !== "tool") return d;
    const same =
      d.tool.id === tool.id ||
      (Boolean(tool.callId) && d.tool.callId === tool.callId);
    return same ? { type: "tool", tool } : d;
  }, immediate);
}

export function upsertToolStart(ev: RuntimeEvent, ctx: ToolUpsertCtx) {
  // Seal thinking/text that already arrived, but never wipe a reasoning bubble
  // that was mid-stream (sealStreamBubbleIfContent keeps empty openers for late
  // reasoning; sealStreamBubble would delete them and thinking vanishes).
  ctx.sealStreamBubbleIfContent();
  const callId = String(ev.data.call_id || uid());
  const name = String(ev.data.name || "tool");
  const pending = Boolean(ev.data.needs_approval);
  const existing =
    ctx.findToolMsg({ callId, statuses: LIVE_TOOL }) ||
    ctx.findToolMsg({
      name,
      statuses: ["streaming", "pending"],
    });
  if (existing?.tool) {
    const prevCallId = existing.tool.callId;
    const tool: ToolCard = {
      ...existing.tool,
      callId,
      status: pending ? "pending" : "running",
      args: ev.data.args ?? existing.tool.args,
      name: name || existing.tool.name,
      summary:
        String(ev.data.summary || "") ||
        formatToolSummary(name, ev.data.args ?? existing.tool.args),
    };
    ctx.updateMsg(existing.id, { tool });
    ctx.syncToolPanel(tool, prevCallId);
    if (name === "write_file") {
      const preview = writeFilePreview(tool.args);
      if (preview) revealWriteFileDetail(ctx, tool);
    }
    if (name === "delegate_task") seedDelegateCanvas(ctx, tool.args, tool.callId, undefined, true);
    return tool;
  }
  const tool: ToolCard = {
    id: uid(),
    callId,
    name,
    args: ev.data.args,
    status: pending ? "pending" : "running",
    summary:
      String(ev.data.summary || "") || formatToolSummary(name, ev.data.args),
  };
  ctx.appendMsg({ id: tool.id, role: "tool", content: "", tool });
  ctx.syncToolPanel(tool);
  if (name === "write_file") revealWriteFileDetail(ctx, tool);
  if (name === "delegate_task") seedDelegateCanvas(ctx, tool.args, tool.callId, undefined, true);
  return tool;
}

export function upsertToolDelta(ev: RuntimeEvent, ctx: ToolUpsertCtx) {
  const index = Number(ev.data.index ?? 0);
  const streamKey = `stream_${index}`;
  const realId = String(ev.data.id || "");
  const name = String(ev.data.name || "");
  const argsRaw = String(ev.data.arguments || "");
  const args = softParseToolArgs(argsRaw);
  const callId = realId || streamKey;

  const existing =
    ctx.findToolMsg({ callId, statuses: LIVE_TOOL }) ||
    ctx.findToolMsg({ callId: streamKey, statuses: LIVE_TOOL }) ||
    (realId ? ctx.findToolMsg({ callId: realId, statuses: LIVE_TOOL }) : undefined) ||
    ctx.transcriptRef.current.find(
      (m) =>
        m.role === "tool" &&
        m.tool?.status === "streaming" &&
        Number(
          (m.tool.args as { _streamIndex?: number } | undefined)?._streamIndex,
        ) === index,
    );
  const delegateExisting = name === "delegate_task"
    ? ctx.findToolMsg({ name, statuses: LIVE_TOOL })
    : undefined;
  const active = existing || delegateExisting;

  // Seal the assistant bubble only once when tool streaming *starts*, and
  // only if it already holds text/reasoning. Sealing an EMPTY bubble removed
  // it, and reasoning that streamed after the first arg delta then re-opened
  // a new bubble BELOW the tool chip (or was lost) -- "thinking not shown".
  if (!active?.tool) {
    ctx.sealStreamBubbleIfContent();
  }

  const summary = formatToolSummary(name || active?.tool?.name || "", args);
  if (active?.tool) {
    const prevCallId = active.tool.callId;
    const tool: ToolCard = {
      ...active.tool,
      callId: name === "delegate_task" ? active.tool.callId : callId,
      name: name || active.tool.name,
      args: name === "delegate_task"
        ? mergeDelegateArgs(active.tool.args, args)
        : { ...args, _streamIndex: index },
      argsRaw,
      status: "streaming",
      summary,
    };
    ctx.updateMsg(active.id, { tool }, false);
    ctx.syncToolPanel(tool, prevCallId, false);
    revealWriteFileDetail(ctx, tool, false);
    if ((name || active.tool.name) === "delegate_task") {
      seedDelegateCanvas(ctx, tool.args, tool.callId, undefined, true);
    }
    return;
  }

  const tool: ToolCard = {
    id: uid(),
    callId,
    name: name || "tool",
    args: { ...args, _streamIndex: index },
    argsRaw,
    status: "streaming",
    summary,
  };
  ctx.appendMsg({ id: tool.id, role: "tool", content: "", tool });
  ctx.syncToolPanel(tool);
  revealWriteFileDetail(ctx, tool);
  if ((name || "tool") === "delegate_task") {
    seedDelegateCanvas(ctx, tool.args, tool.callId, undefined, true);
  }
}

export function upsertToolEnd(ev: RuntimeEvent, ctx: ToolUpsertCtx) {
  const callId = String(ev.data.call_id || "");
  const name = String(ev.data.name || "tool");
  const result = String(ev.data.result ?? ev.data.preview ?? "");
  const ok = ev.data.ok !== false && !result.startsWith("ERROR");
  const hit =
    ctx.findToolMsg({ callId }) ||
    ctx.findToolMsg({
      name,
      statuses: ["running", "streaming", "pending"],
    });
  if (hit?.tool) {
    const prevCallId = hit.tool.callId;
    const tool: ToolCard = {
      ...hit.tool,
      callId: callId || hit.tool.callId,
      name,
      args: ev.data.args ?? hit.tool.args,
      result,
      status: ok ? "done" : "error",
    };
    ctx.updateMsg(hit.id, { tool });
    ctx.syncToolPanel(tool, prevCallId);
    if (name === "delegate_task") {
      seedDelegateCanvas(ctx, tool.args, tool.callId, result, false);
    }
    return;
  }
  const tool: ToolCard = {
    id: uid(),
    callId: callId || uid(),
    name,
    args: ev.data.args,
    result,
    status: ok ? "done" : "error",
  };
  ctx.appendMsg({ id: tool.id, role: "tool", content: "", tool });
  ctx.syncToolPanel(tool);
  if (name === "delegate_task") {
    seedDelegateCanvas(ctx, tool.args, tool.callId, result, false);
  }
}
