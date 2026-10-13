/** Pure helpers extracted from App to keep the shell leaner. */

import { fileRawUrl, type FilePayload, type SessionDetailMessage } from "../api";
import type { ChatMsg, DetailView, MsgAttachment, SubNode, SubTranscriptItem, ToolCard } from "../types/chat";
import { formatToolSummary } from "./toolSummary";
import {
  alignCanvasSlots,
  canvasGoalsMatch,
  canvasPlaceholderId,
  isEphemeralCanvasId,
  limitCanvasRoots,
} from "./canvasSlots";

export {
  canvasPlaceholderId,
  isEphemeralCanvasId,
  limitCanvasRoots,
} from "./canvasSlots";

export type GreetingKey = "greetingMorning" | "greetingAfternoon" | "greetingEvening";

/** Local browser time (not UTC / server). */
export function greetingKey(now = new Date()): GreetingKey {
  const h = now.getHours();
  if (h >= 5 && h < 12) return "greetingMorning";
  if (h >= 12 && h < 18) return "greetingAfternoon";
  return "greetingEvening";
}

export function isSkillInjectMessage(content: string) {
  const text = (content || "").trim();
  return (
    text.includes("【Skill 已注入】") ||
    text.includes("----- SKILL START -----") ||
    text.startsWith("请立即调用函数工具")
  );
}

/** Hide Sidekick-internal user turns from the chat UI / history titles. */
export function isHiddenUserContent(content: string): boolean {
  const c = (content || "").trim();
  if (!c) return true;
  if (c.startsWith("[CONTEXT COMPACTION]")) return true;
  if (/^\[Plan step\s/i.test(c)) return true;
  if (c.startsWith("[sidekick:")) return true;
  if (c.startsWith("Iteration budget exhausted")) return true;
  return false;
}

export function buildSuggestions(
  t: (key: import("../i18n").MsgKey, ...args: string[]) => string,
) {
  return [
    { label: t("suggestSkills"), text: "/skills" },
    { label: t("suggestMemory"), text: "/memory" },
    { label: t("suggestListDir"), text: t("suggestListDirText") },
    { label: t("suggestWrite"), text: t("suggestWriteText") },
  ];
}

export function formatTime(ts: number, locale: "zh" | "en" = "zh") {
  if (!ts) return "";
  const d = new Date(ts > 1e12 ? ts : ts * 1000);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(locale === "en" ? "en-US" : "zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatArgs(args: unknown) {
  try {
    return JSON.stringify(args ?? {}, null, 2);
  } catch {
    return String(args);
  }
}

/** Best-effort parse of streaming write_file / tool JSON arguments. */
export function softParseToolArgs(raw: string): Record<string, unknown> {
  const text = raw || "";
  try {
    const data = JSON.parse(text);
    if (data && typeof data === "object" && !Array.isArray(data)) {
      return data as Record<string, unknown>;
    }
  } catch {
    /* partial */
  }
  const out: Record<string, unknown> = { _partial: true };
  const pathMatch = text.match(/"path"\s*:\s*"((?:\\.|[^"\\])*)"/);
  if (pathMatch) {
    try {
      out.path = JSON.parse(`"${pathMatch[1]}"`);
    } catch {
      out.path = pathMatch[1];
    }
  }
  const contentKey = text.search(/"content"\s*:\s*"/);
  if (contentKey >= 0) {
    const after = text.slice(contentKey);
    const m = after.match(/"content"\s*:\s*"/);
    if (m && m.index != null) {
      let i = m.index + m[0].length;
      let content = "";
      while (i < after.length) {
        const ch = after[i];
        if (ch === "\\" && i + 1 < after.length) {
          const n = after[i + 1];
          const map: Record<string, string> = {
            n: "\n",
            t: "\t",
            r: "\r",
            '"': '"',
            "\\": "\\",
          };
          content += map[n] ?? n;
          i += 2;
          continue;
        }
        if (ch === '"') break;
        content += ch;
        i += 1;
      }
      out.content = content;
    }
  }
  if (Object.keys(out).length === 1 && out._partial) {
    out._raw = text;
  }
  return out;
}

export function writeFilePreview(args: unknown): { path: string; content: string } | null {
  if (!args || typeof args !== "object") return null;
  const obj = args as Record<string, unknown>;
  const path = typeof obj.path === "string" ? obj.path : "";
  const content = typeof obj.content === "string" ? obj.content : "";
  if (!path && !content) return null;
  return { path, content };
}

export function formatBytes(n?: number) {
  if (n == null || Number.isNaN(n)) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function normalizeFileKind(kind?: string): string {
  if (!kind) return "text";
  if (kind === "office") return "document";
  if (kind === "binary") return "unsupported";
  return kind;
}

export function fileToDetail(
  file: FilePayload,
  opts?: { highlightQuery?: string; focusLine?: number },
): Exclude<DetailView, null> {
  const kind = normalizeFileKind(file.kind);
  return {
    type: "file",
    path: file.path,
    content: file.content || "",
    dirty: false,
    kind,
    mime: file.mime,
    size: file.size,
    preview: file.preview || "",
    editable: Boolean(file.editable ?? kind === "text"),
    message: file.message || (kind === "unsupported" ? "暂不支持预览此文件" : ""),
    rawUrl: fileRawUrl(file.path, file.workspace),
    workspace: file.workspace,
    highlightQuery: opts?.highlightQuery,
    focusLine: opts?.focusLine,
    forceEdit: false,
  };
}

export function langFromPath(path: string) {
  const lower = path.toLowerCase();
  if (lower.endsWith(".py")) return "python";
  if (lower.endsWith(".ts") || lower.endsWith(".tsx")) return "typescript";
  if (lower.endsWith(".js") || lower.endsWith(".jsx")) return "javascript";
  if (lower.endsWith(".json")) return "json";
  if (lower.endsWith(".md")) return "markdown";
  if (lower.endsWith(".css")) return "css";
  if (lower.endsWith(".html") || lower.endsWith(".htm")) return "html";
  if (lower.endsWith(".sh")) return "bash";
  return "";
}

let _seq = 0;
export const uid = () => `m_${++_seq}_${Date.now()}`;

const ATTACH_MARKER = "用户上传了以下附件，请根据附件内容进行分析与回答：";

export function parseUserAttachments(content: string): {
  text: string;
  attachments: MsgAttachment[];
} {
  const raw = content || "";
  const idx = raw.indexOf(ATTACH_MARKER);
  const attachPart = idx >= 0 ? raw.slice(idx) : raw;
  const text = idx >= 0 ? raw.slice(0, idx).trim() : raw;
  const attachments: MsgAttachment[] = [];
  const re = /### 附件：([^\n]+)\n路径：`([^`]+)`/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(attachPart)) !== null) {
    attachments.push({ name: m[1].trim(), path: m[2].trim() });
  }
  if (idx < 0 && attachments.length === 0) {
    return { text: raw, attachments: [] };
  }
  return { text, attachments };
}

/** Map persisted session messages → UI chat bubbles (hides internal turns). */
/** Soften skill tool-call prompts when restoring history without display text. */
export function normalizeRestoredUserContent(content: string): string {
  const text = (content || "").trim();
  if (!text.startsWith("请立即调用函数工具")) return text;
  const tool = text.match(/`(skill_[A-Za-z0-9_]+)`/)?.[1];
  const taskMatch = text.match(/task 参数为：(.+?)(?:。|$)/);
  const task = (taskMatch?.[1] || "").trim();
  if (task && !task.includes("可省略 task")) {
    const name = (tool || "skill").replace(/^skill_/, "").replace(/_/g, "-");
    return `/skill ${name} ${task}`;
  }
  if (tool) {
    return `/skill ${tool.replace(/^skill_/, "").replace(/_/g, "-")}`;
  }
  return text;
}

export const DELEGATE_TOOL_NAMES = new Set(["delegate_task", "delegate_dialogue"]);

function parseJsonValue(raw: string): unknown {
  const text = (raw || "").trim();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function toolArgsRecord(args: unknown): Record<string, unknown> {
  if (args && typeof args === "object" && !Array.isArray(args)) {
    return args as Record<string, unknown>;
  }
  return {};
}

export function stripToolCallMarkup(text: string): string {
  if (!text) return text;
  if (
    !/<tool_call\b|<function\s*=|<invoke\s+name=|<\/function>|<parameter\b|<\/parameter/i.test(
      text,
    )
  ) {
    return text;
  }
  let s = text.replace(/<\/?(?:minimax:)?tool_call>/gi, " ");
  s = s.replace(/<function\s*=[\s\S]*?(?:<\/function>|$)/gi, " ");
  s = s.replace(/<invoke\s+name=[^>]+>[\s\S]*?(?:<\/invoke>|$)/gi, " ");
  s = s.replace(/<parameter\s*=[^>]*>[\s\S]*?<\/parameter>/gi, " ");
  s = s.replace(/<\/parameter\s*=[^>]*>[\s\S]*?<\/parameter>/gi, " ");
  s = s.replace(/<\/parameter>\s*[A-Za-z_][\w]*\s*>[\s\S]*?<\/parameter>/gi, " ");
  s = s.replace(/<\/?parameter[^>]*>/gi, " ");
  s = s.replace(/(?:^|\s)function\s*=\s*[A-Za-z0-9_.-]+/g, " ");
  s = s.replace(/<\/(?:function|invoke)>/gi, " ");
  return s.replace(/\s+/g, " ").trim();
}

/** Keep one robot per slot when history restore + live replay both emit cards. */
export function dedupeCanvasNodes(nodes: SubNode[]): SubNode[] {
  const out: SubNode[] = [];
  for (const node of nodes) {
    const hit = out.find((x) => sameSubagentSlot(x, node));
    if (!hit) {
      out.push(node);
      continue;
    }
    const hitLen = (hit.transcript || []).length;
    const nextLen = (node.transcript || []).length;
    if (nextLen > hitLen) {
      const idx = out.indexOf(hit);
      out[idx] = {
        ...hit,
        ...node,
        children: hit.children?.length ? hit.children : node.children,
        transcript: node.transcript?.length ? node.transcript : hit.transcript,
      };
    } else if (node.children?.length && !hit.children?.length) {
      hit.children = node.children;
    }
  }
  return out;
}

export function asSubagentMsg(node: SubNode, stage?: number): ChatMsg {
  return {
    id: uid(),
    role: "subagent",
    content: node.summary || node.goal,
    subagent: node,
    stage,
    agent_id: node.id,
  };
}

const GENERIC_SUBAGENT_ROLES = new Set(["leaf", "orchestrator", "task", "subagent"]);

export function isGenericSubagentRole(role?: string): boolean {
  const r = (role || "").trim().toLowerCase();
  return !r || GENERIC_SUBAGENT_ROLES.has(r);
}

/** Match a live card to its spawn event without collapsing every `leaf` into one. */
export function sameSubagentSlot(
  existing: SubNode,
  incoming: { id?: string; role?: string; goal?: string },
): boolean {
  if (incoming.id && existing.id === incoming.id) return true;
  const existingId = String(existing.id || "");
  const incomingId = String(incoming.id || "");
  if (
    incomingId &&
    existingId &&
    !isEphemeralCanvasId(existingId) &&
    !isEphemeralCanvasId(incomingId)
  ) {
    return false;
  }
  if (canvasGoalsMatch(existing.goal, incoming.goal)) return true;
  const role = (incoming.role || "").trim();
  if (
    role &&
    !isGenericSubagentRole(role) &&
    (existing.role === role ||
      existing.goal === role ||
      existing.goal.startsWith(`${role} —`) ||
      existing.goal.startsWith(`You are ${role}`))
  ) {
    return true;
  }
  return false;
}

/** Bind tool-payload workers onto the live tree without creating extra roots. */
export function unionCanvasNodes(
  tree: SubNode[],
  expanded: SubNode[],
  declared?: number,
): SubNode[] {
  const preferTree = tree.length >= expanded.length;
  const aligned = alignCanvasSlots(
    preferTree ? tree : expanded,
    preferTree ? expanded : tree,
  );
  const cap =
    declared && declared > 0
      ? declared
      : tree.length && expanded.length
        ? Math.min(tree.length, expanded.length)
        : Math.max(tree.length, expanded.length);
  return nestHelperAgents(dedupeCanvasNodes(limitCanvasRoots(aligned, cap)));
}

export function delegateSlotCount(name: string, args: unknown): number {
  const rec = toolArgsRecord(args);
  if (name === "delegate_task") return taskItemsFromDelegateArgs(rec).length;
  if (name === "delegate_dialogue") return speakersFromDelegateArgs(rec).length;
  return 0;
}

export function declaredDelegateSlotCount(messages: ChatMsg[], stage: number): number {
  let total = 0;
  for (const message of messages) {
    if ((message.stage ?? 0) !== stage || message.role !== "tool" || !message.tool) continue;
    if (!DELEGATE_TOOL_NAMES.has(message.tool.name)) continue;
    total += delegateSlotCount(message.tool.name, message.tool.args);
  }
  return total;
}

/** One pass over the thread: stage → canvas roots. Avoids O(stages × n) per render. */
export function groupCanvasRootsByStage(messages: ChatMsg[]): Map<number, SubNode[]> {
  const byStage = new Map<number, SubNode[]>();
  const delegateCounts = new Map<number, number>();
  for (const message of messages) {
    const stage = message.stage ?? 0;
    if (message.role === "subagent" && message.subagent) {
      const list = byStage.get(stage);
      if (list) list.push(message.subagent);
      else byStage.set(stage, [message.subagent]);
    } else if (
      message.role === "tool" &&
      message.tool &&
      DELEGATE_TOOL_NAMES.has(message.tool.name)
    ) {
      delegateCounts.set(
        stage,
        (delegateCounts.get(stage) || 0) +
          delegateSlotCount(message.tool.name, message.tool.args),
      );
    }
  }
  const out = new Map<number, SubNode[]>();
  for (const [stage, nodes] of byStage) {
    const declared = delegateCounts.get(stage) || 0;
    out.set(
      stage,
      nestHelperAgents(dedupeCanvasNodes(limitCanvasRoots(nodes, declared))),
    );
  }
  return out;
}

export function canvasRootsForStage(messages: ChatMsg[], stage: number): SubNode[] {
  return groupCanvasRootsByStage(messages).get(stage) || [];
}

export function isDelegateToolMsg(message: ChatMsg): boolean {
  return Boolean(
    message.role === "tool" && message.tool && DELEGATE_TOOL_NAMES.has(message.tool.name),
  );
}

/** Index after which canvas cards for `stage` belong (delegate tool, not the thread tail). */
export function canvasInsertAfterIndex(messages: ChatMsg[], stage: number): number {
  let lastDelegate = -1;
  let lastTool = -1;
  let lastOfStage = -1;
  for (let i = 0; i < messages.length; i++) {
    const message = messages[i];
    if ((message.stage ?? 0) !== stage) continue;
    if (message.role === "subagent") continue;
    lastOfStage = i;
    if (message.role === "tool" && message.tool) {
      lastTool = i;
      if (DELEGATE_TOOL_NAMES.has(message.tool.name)) lastDelegate = i;
    }
  }
  if (lastDelegate >= 0) return lastDelegate;
  if (lastTool >= 0) return lastTool;
  return lastOfStage;
}

/** Splice canvas messages into the turn that spawned them — never after later user turns. */
export function placeCanvasMessages(
  messages: ChatMsg[],
  canvasMsgs: ChatMsg[],
  stage: number,
): ChatMsg[] {
  const without = messages.filter(
    (message) => !(message.role === "subagent" && (message.stage ?? 0) === stage),
  );
  if (!canvasMsgs.length) return without;
  const after = canvasInsertAfterIndex(without, stage);
  if (after >= 0) {
    return [...without.slice(0, after + 1), ...canvasMsgs, ...without.slice(after + 1)];
  }
  const nextIdx = without.findIndex((message) => (message.stage ?? 0) > stage);
  if (nextIdx >= 0) {
    return [...without.slice(0, nextIdx), ...canvasMsgs, ...without.slice(nextIdx)];
  }
  return [...without, ...canvasMsgs];
}

function partyOwnsTask(party: SubNode, task: SubNode): boolean {
  if (task.parent_id && (task.parent_id === party.id || task.parent_id === party.role)) {
    return true;
  }
  const label = (party.role || "").trim();
  if (!label || isGenericSubagentRole(label)) return false;
  const hay = `${task.goal || ""} ${task.role || ""}`;
  return hay.includes(label);
}

/** Dialogue cast stays the roots; nested delegate_task helpers hang off their owner. */
export function nestHelperAgents(nodes: SubNode[]): SubNode[] {
  if (nodes.length < 2) return nodes;
  const cloned = nodes.map((n) => ({ ...n, children: [...(n.children || [])] }));
  const byId = new Map(cloned.map((n) => [n.id, n]));
  const nested = new Set<string>();
  for (const node of cloned) {
    const pid = (node.parent_id || "").trim();
    if (!pid || pid === node.id) continue;
    const host =
      byId.get(pid) ||
      cloned.find(
        (p) =>
          p.id !== node.id &&
          ((p.role && p.role === pid) || (p.goal && p.goal === pid)),
      );
    if (!host) continue;
    if (!(host.children || []).some((c) => c.id === node.id)) {
      host.children = [...(host.children || []), node];
    }
    nested.add(node.id);
  }
  const remaining = cloned.filter((n) => !nested.has(n.id));
  const parties = remaining.filter((n) => n.kind === "party" || n.kind === "talk");
  const others = remaining.filter((n) => n.kind !== "party" && n.kind !== "talk");
  if (parties.length < 2 || !others.length) return remaining;
  const roots = parties.map((p) => ({ ...p, children: [...(p.children || [])] }));
  for (const task of others) {
    const host = roots.find((p) => partyOwnsTask(p, task)) || roots[roots.length - 1];
    if (!(host.children || []).some((c) => c.id === task.id)) {
      host.children = [...(host.children || []), task];
    }
  }
  return roots;
}

export function taskItemsFromDelegateArgs(args: Record<string, unknown>): { goal: string; role: string }[] {
  const items: { goal: string; role: string }[] = [];
  const tasks = args.tasks;
  if (Array.isArray(tasks) && tasks.length) {
    for (const raw of tasks) {
      if (typeof raw === "string") {
        const goal = raw.trim();
        if (goal) items.push({ goal, role: "leaf" });
        continue;
      }
      if (raw && typeof raw === "object" && !Array.isArray(raw)) {
        const rec = raw as Record<string, unknown>;
        const goal = String(
          rec.goal || rec.task || rec.query || rec.prompt || rec.description || "",
        ).trim();
        if (goal) items.push({ goal, role: String(rec.role || "leaf") });
      }
    }
    if (items.length) return items;
  }
  const goal = String(args.goal || args.task || args.query || args.prompt || "").trim();
  if (goal) items.push({ goal, role: String(args.role || "leaf") });
  return items;
}

function speakersFromDelegateArgs(args: Record<string, unknown>): { name: string; brief: string }[] {
  const out: { name: string; brief: string }[] = [];
  const raw = args.speakers;
  if (!Array.isArray(raw)) return out;
  for (const item of raw) {
    if (typeof item === "string") {
      const name = item.trim();
      if (name) out.push({ name, brief: "" });
      continue;
    }
    if (item && typeof item === "object" && !Array.isArray(item)) {
      const rec = item as Record<string, unknown>;
      const name = String(rec.name || rec.role || rec.side || "").trim();
      if (!name) continue;
      out.push({
        name,
        brief: String(rec.brief || rec.stance || rec.goal || "").trim(),
      });
    }
  }
  return out;
}

function isRunningToolStatus(status?: string, hasResult?: boolean): boolean {
  const raw = (status || "").toLowerCase();
  if (raw === "running" || raw === "pending" || raw === "streaming") return true;
  if (raw === "done" || raw === "error") return false;
  return !hasResult;
}

/** Turn persisted delegate_task / delegate_dialogue tool rows into subagent cards. */
export function subagentMessagesFromDelegateTool(m: {
  name?: string;
  call_id?: string;
  args?: unknown;
  result?: string;
  status?: string;
  stage?: number;
}): ChatMsg[] {
  const name = m.name || "";
  if (!DELEGATE_TOOL_NAMES.has(name)) return [];
  const args = toolArgsRecord(m.args);
  const callId = m.call_id || uid();
  const parsed = parseJsonValue(m.result || "");
  const hasResult = Boolean((m.result || "").trim());
  const running = isRunningToolStatus(m.status, hasResult);

  if (name === "delegate_task") {
    let items = taskItemsFromDelegateArgs(args);
    const rows = Array.isArray(parsed) ? parsed : [];
    if (!items.length) {
      for (const row of rows) {
        if (row && typeof row === "object" && !Array.isArray(row)) {
          const rec = row as Record<string, unknown>;
          const goal = String(rec.goal || "").trim();
          if (goal) items.push({ goal, role: "leaf" });
        }
      }
    }
    return items.map((item, i) => {
      const row =
        rows.find(
          (r) =>
            r &&
            typeof r === "object" &&
            !Array.isArray(r) &&
            Number((r as Record<string, unknown>).index) === i,
        ) || rows[i];
      const rec =
        row && typeof row === "object" && !Array.isArray(row)
          ? (row as Record<string, unknown>)
          : {};
      const summary = String(rec.summary || rec.text || rec.result || "").trim();
      const err = summary.startsWith("ERROR");
      const finished = !running;
      const node: SubNode = {
        id: canvasPlaceholderId("restored", callId, i),
        goal: item.goal,
        role: item.role || "leaf",
        kind: "task",
        parent_id: String(args.parent_id || args.owner || ""),
        status: err ? "error" : finished ? "done" : "running",
        summary: summary || undefined,
        activity: finished || err ? undefined : "运行中…",
        transcript: summary
          ? [{ id: uid(), kind: "assistant", text: stripToolCallMarkup(summary), turnAt: i }]
          : [],
      };
      return asSubagentMsg(node, m.stage);
    });
  }

  let speakers = speakersFromDelegateArgs(args);
  const resultObj =
    parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  if (!speakers.length && Array.isArray(resultObj?.speakers)) {
    for (const sp of resultObj.speakers) {
      const name = String(sp || "").trim();
      if (name) speakers.push({ name, brief: "" });
    }
  }
  const turns = Array.isArray(resultObj?.turns) ? resultObj.turns : [];
  const byName = new Map<string, SubTranscriptItem[]>();
  turns.forEach((raw, idx) => {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return;
    const rec = raw as Record<string, unknown>;
    const speaker = String(rec.name || "").trim();
    const text = String(rec.text || "").trim();
    if (!speaker || !text) return;
    const list = byName.get(speaker) || [];
    list.push({ id: uid(), kind: "assistant", text: stripToolCallMarkup(text), turnAt: idx });
    byName.set(speaker, list);
  });
  return speakers.map((sp, i) => {
    const transcript = byName.get(sp.name) || [];
    const summary = transcript
      .map((item) => (item.kind === "assistant" ? item.text : ""))
      .filter(Boolean)
      .join("\n\n");
    const done = hasResult && !running;
    const node: SubNode = {
      id: canvasPlaceholderId("restored", callId, i),
      goal: sp.brief ? `${sp.name} — ${sp.brief}` : sp.name,
      role: sp.name,
      kind: "party",
      status: done ? "done" : "running",
      summary: summary || undefined,
      activity: done ? undefined : "运行中…",
      transcript,
    };
    return asSubagentMsg(node, m.stage);
  });
}

function latestCanvasTree(agentTree?: unknown[]): unknown[] {
  if (!Array.isArray(agentTree) || !agentTree.length) return [];
  const rows = agentTree.filter((raw) => raw && typeof raw === "object" && !Array.isArray(raw));
  const waves = rows.map((raw) => Number((raw as Record<string, unknown>).turn || 0));
  const latestTurn = Math.max(0, ...waves.filter(Number.isFinite));
  if (latestTurn > 0) {
    return rows.filter(
      (raw) => Number((raw as Record<string, unknown>).turn || 0) === latestTurn,
    );
  }
  return rows;
}

export function canvasTurnFromAgentTree(agentTree?: unknown[]): number {
  const rows = latestCanvasTree(agentTree);
  const turns = rows
    .map((raw) => Number((raw as Record<string, unknown>).turn || 0))
    .filter((n) => Number.isFinite(n) && n > 0);
  return turns.length ? Math.max(...turns) : 0;
}

export function nodesFromAgentTree(agentTree?: unknown[]): SubNode[] {
  return nestHelperAgents(
    latestCanvasTree(agentTree)
      .map((raw) => canvasItemToNode(raw))
      .filter((n): n is SubNode => Boolean(n?.id)),
  );
}

export function mapSessionMessages(
  messages: SessionDetailMessage[],
  agentTree?: unknown[],
): ChatMsg[] {
  const treeRoots = nodesFromAgentTree(agentTree);
  const expandedByStage = new Map<number, SubNode[]>();
  const out: ChatMsg[] = [];
  let stage = 0;
  for (const m of messages || []) {
    if (m.role === "user") {
      if (isHiddenUserContent(m.content || "")) continue;
      stage += 1;
      const parsed = parseUserAttachments(
        normalizeRestoredUserContent(m.content || ""),
      );
      out.push({
        id: uid(),
        role: "user",
        content: parsed.text,
        attachments: parsed.attachments.length ? parsed.attachments : undefined,
        stage,
        agent_id: m.agent_id,
        ts: m.ts ? m.ts * 1000 : undefined,
      });
      continue;
    }
    if (m.role === "assistant") {
      const content = stripToolCallMarkup((m.content || "").trim());
      if (!content && !m.reasoning) continue;
      const jobMeta = m.sidekick?.kind === "shell_job_done" ? m.sidekick : null;
      const jobId = String(jobMeta?.job_id || "").trim();
      out.push({
        id: jobId ? `job-done-${jobId}` : uid(),
        role: "assistant",
        content,
        reasoning: m.reasoning || undefined,
        stage,
        agent_id: m.agent_id,
        ts: m.ts ? m.ts * 1000 : undefined,
        jobNotice: jobId
          ? {
              job_id: jobId,
              status: String(jobMeta?.status || "exited"),
              exit_code: jobMeta?.exit_code ?? null,
            }
          : undefined,
      });
      continue;
    }
    if (m.role === "tool") {
      const name = m.name || "tool";
      const expanded = subagentMessagesFromDelegateTool({
        name,
        call_id: m.call_id,
        args: m.args,
        result: m.result,
        status: m.status,
        stage,
      });
      if (expanded.length) {
        const nodes = expanded
          .map((row) => row.subagent)
          .filter((n): n is SubNode => Boolean(n));
        expandedByStage.set(
          stage,
          unionCanvasNodes(
            expandedByStage.get(stage) || [],
            nodes,
            (expandedByStage.get(stage) || []).length + nodes.length,
          ),
        );
      }
      const callId = m.call_id || uid();
      const statusRaw = (m.status || "done").toLowerCase();
      const status: ToolCard["status"] =
        statusRaw === "error"
          ? "error"
          : statusRaw === "pending"
            ? "pending"
            : statusRaw === "running" || statusRaw === "streaming"
              ? "running"
              : "done";
      const tool: ToolCard = {
        id: uid(),
        callId,
        name,
        args: m.args ?? {},
        result: m.result || "",
        status,
        summary: formatToolSummary(name, m.args ?? {}),
      };
      out.push({
        id: tool.id,
        role: "tool",
        content: "",
        tool,
        stage,
        ts: m.ts ? m.ts * 1000 : undefined,
      });
    }
  }
  const existingStages = [...new Set(out.map((m) => m.stage ?? 0).filter((n) => n > 0))];
  const latestStage = Math.max(1, ...existingStages, stage);
  const treeTurn = canvasTurnFromAgentTree(agentTree);
  const toolStages = [...expandedByStage.keys()];
  const treeStage = existingStages.includes(treeTurn)
    ? treeTurn
    : toolStages.length
      ? Math.max(...toolStages)
      : latestStage;
  if (treeRoots.length) {
    const fromTools = expandedByStage.get(treeStage) || [];
    expandedByStage.set(
      treeStage,
      unionCanvasNodes(treeRoots, fromTools, fromTools.length || treeRoots.length),
    );
  }
  let placed = out;
  for (const [stageKey, nodes] of expandedByStage) {
    const msgs = nodes.map((n) => asSubagentMsg(n, stageKey));
    if (!msgs.length) continue;
    placed = placeCanvasMessages(placed, msgs, stageKey);
  }
  return placed;
}

function canvasStatus(raw: unknown): SubNode["status"] {
  const s = String(raw || "").toLowerCase();
  if (s === "running" || s === "pending" || s === "streaming") return "running";
  if (s === "error") return "error";
  return "done";
}

function canvasItemToNode(raw: unknown): SubNode | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const rec = raw as Record<string, unknown>;
  const id = String(rec.child_id || rec.id || "").trim();
  if (!id) return null;
  const kind =
    rec.kind === "party" || rec.kind === "talk" || rec.kind === "task" ? rec.kind : "task";
  const kids = Array.isArray(rec.children) ? rec.children : [];
  const transcriptRaw = Array.isArray(rec.transcript) ? rec.transcript : [];
  const transcript: SubTranscriptItem[] = transcriptRaw.flatMap((item, i): SubTranscriptItem[] => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const row = item as Record<string, unknown>;
    if (row.kind === "tool") {
      const name = String(row.name || "tool");
      return [
        {
          id: uid(),
          kind: "tool" as const,
          tool: {
            id: uid(),
            callId: String(row.call_id || uid()),
            name,
            summary: name,
            status: "done" as const,
            result: String(row.result || ""),
          },
        },
      ];
    }
    const text = stripToolCallMarkup(String(row.text || row.content || ""));
    const reasoning = String(row.reasoning || "");
    if (!text && !reasoning) return [];
    return [
      {
        id: uid(),
        kind: "assistant" as const,
        text,
        reasoning: reasoning || undefined,
        turnAt: i,
      },
    ];
  });
  const status = canvasStatus(rec.status);
  return {
    id,
    goal: String(rec.goal || rec.label || rec.party || ""),
    role: String(rec.party || rec.role || "leaf"),
    kind,
    parent_id: String(rec.parent_id || ""),
    status,
    summary: String(rec.summary || "").trim() || undefined,
    activity: status === "running" ? String(rec.activity || "") || undefined : undefined,
    transcript,
    children: kids.map((c) => canvasItemToNode(c)).filter((n): n is SubNode => Boolean(n)),
  };
}

export function findSubNode(node: SubNode, id: string): SubNode | null {
  if (node.id === id) return node;
  for (const child of node.children || []) {
    const hit = findSubNode(child, id);
    if (hit) return hit;
  }
  return null;
}

/** Short name for a spawned agent on the canvas / detail header. */
export function subagentDisplayName(node: SubNode, fallback = "Agent"): string {
  const role = (node.role || "").trim();
  if (role && role !== "leaf" && role !== "orchestrator" && !isColorSideLabel(role)) {
    return role;
  }
  const goal = (node.goal || "").trim();
  const dash = goal.split(/[—–-]/)[0]?.trim() || "";
  const fromGoal = dash || goal;
  if (fromGoal && !isColorSideLabel(fromGoal)) return fromGoal;
  return fallback;
}

/** 红方/蓝方 clash with canvas robot hues — never show them as labels. */
export function isColorSideLabel(name: string): boolean {
  const n = (name || "").trim();
  return /^(红方|蓝方|红队|蓝队|红色方|蓝色方|red(?:\s*team)?|blue(?:\s*team)?|team\s*red|team\s*blue)$/i.test(
    n,
  );
}

/** Before the first token, streaming looks like output — label it as thinking. */
export function streamPhaseSuffix(
  streaming: boolean,
  opts: { reasoningStreaming?: boolean; text?: string; reasoning?: string },
  t: (key: "thinking" | "outputting") => string,
): string {
  if (!streaming) return "";
  const hasText = Boolean((opts.text || "").trim());
  if (opts.reasoningStreaming || !hasText) return ` · ${t("thinking")}`;
  return ` · ${t("outputting")}`;
}

export function mapSubNode(
  node: SubNode,
  id: string,
  fn: (s: SubNode) => SubNode,
): SubNode | null {
  if (node.id === id) return fn(node);
  if (!node.children?.length) return null;
  let found = false;
  const children = node.children.map((child) => {
    const next = mapSubNode(child, id, fn);
    if (next) {
      found = true;
      return next;
    }
    return child;
  });
  return found ? { ...node, children } : null;
}
