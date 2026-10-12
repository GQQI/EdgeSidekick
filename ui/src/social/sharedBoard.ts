/** Group shared board — durable, dynamically updated memory (data layer). */

import type {
  BoardFact,
  BoardRound,
  BoardTask,
  BoardTurnEntry,
  GroupSharedBoard,
  SocialAgent,
  SocialAnnouncement,
} from "./types";

function nid(prefix: string): string {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
}

function norm(s: string): string {
  return (s || "").replace(/\s+/g, " ").trim();
}

export function similar(a: string, b: string): boolean {
  const x = norm(a).toLowerCase();
  const y = norm(b).toLowerCase();
  if (!x || !y) return false;
  if (x === y) return true;
  if (x.length >= 6 && y.includes(x)) return true;
  if (y.length >= 6 && x.includes(y)) return true;
  const tx = new Set(x.split(/[，。；;、\s,/]+/).filter((t) => t.length >= 2));
  const ty = new Set(y.split(/[，。；;、\s,/]+/).filter((t) => t.length >= 2));
  if (!tx.size || !ty.size) return false;
  let hit = 0;
  for (const t of tx) if (ty.has(t)) hit += 1;
  return hit / Math.min(tx.size, ty.size) >= 0.7;
}

export function emptyBoard(): GroupSharedBoard {
  return {
    revision: 0,
    updatedAt: Date.now(),
    facts: [],
    tasks: [],
    lastRound: null,
    workingRound: null,
    recentRounds: [],
    digest: "",
  };
}

export function normalizeBoard(raw: Partial<GroupSharedBoard> | null | undefined): GroupSharedBoard {
  const b = raw || {};
  return {
    revision: Number(b.revision) || 0,
    updatedAt: Number(b.updatedAt) || Date.now(),
    facts: Array.isArray(b.facts) ? b.facts : [],
    tasks: Array.isArray(b.tasks) ? b.tasks : [],
    lastRound: b.lastRound ?? null,
    workingRound: b.workingRound ?? null,
    recentRounds: Array.isArray(b.recentRounds) ? b.recentRounds.slice(0, 8) : [],
    digest: typeof b.digest === "string" ? b.digest : "",
  };
}

export function summarizeReply(text: string, max = 120): string {
  const s = norm(text);
  if (s.length <= max) return s;
  return s.slice(0, max - 1) + "…";
}

function rerebuildDigest(board: GroupSharedBoard): string {
  const parts: string[] = [];
  const facts = board.facts.slice(-5);
  if (facts.length) parts.push("事实: " + facts.map((f) => f.text).join("；"));
  const open = board.tasks.filter((t) => t.status === "open").slice(-4);
  if (open.length) parts.push("待办: " + open.map((t) => t.text).join("；"));
  const round = board.workingRound || board.lastRound;
  if (round?.entries?.length) {
    const spoke = round.entries.filter((e) => e.action === "reply");
    if (spoke.length) {
      parts.push("本轮已发言: " + spoke.map((e) => `${e.agentName}「${e.summary}」`).join("；"));
    }
    const silent = round.entries.filter((e) => e.action === "silent");
    if (silent.length) parts.push("本轮未发言: " + silent.map((e) => e.agentName).join("、"));
  }
  return summarizeReply(parts.join(" | "), 360);
}

export function formatBoardForPrompt(board: GroupSharedBoard | null | undefined, limitFacts = 8): string {
  if (!board) return "";
  const lines: string[] = [`[群共享记忆 rev.${board.revision} · 动态更新]`];
  if (board.digest) lines.push(`摘要: ${board.digest}`);
  if (board.facts.length) {
    lines.push("耐久事实:");
    for (const f of board.facts.slice(-limitFacts)) {
      const who = f.byAgentName ? `（${f.byAgentName}）` : "";
      lines.push(`- ${f.text}${who}`);
    }
  }
  const openTasks = board.tasks.filter((t) => t.status === "open").slice(-6);
  if (openTasks.length) {
    lines.push("开放任务:");
    for (const t of openTasks) {
      const owner = t.ownerName ? `@${t.ownerName}` : "未指派";
      lines.push(`- [${owner}] ${t.text}`);
    }
  }
  const done = board.tasks.filter((t) => t.status === "done").slice(-3);
  if (done.length) {
    lines.push("近期完成:");
    for (const t of done) lines.push(`- ✓ ${t.text}`);
  }
  const live = board.workingRound;
  if (live?.entries?.length) {
    lines.push(`本轮进行中（用户: ${live.userPreview || "…"}）:`);
    for (const e of live.entries) {
      if (e.action === "silent") lines.push(`- ${e.agentName}: （未发言）`);
      else lines.push(`- ${e.agentName}: ${e.summary}`);
    }
  } else {
    const round = board.lastRound || board.recentRounds[0];
    if (round?.entries?.length) {
      lines.push(`上一轮（用户: ${round.userPreview || "…"}）:`);
      for (const e of round.entries) {
        if (e.action === "silent") lines.push(`- ${e.agentName}: （未发言）`);
        else lines.push(`- ${e.agentName}: ${e.summary}`);
      }
    }
  }
  if (lines.length <= 1) return "";
  return lines.join("\n") + "\n\n";
}

export type AgentDecision = {
  action: "reply" | "silent";
  text: string;
  facts: string[];
  tasks: string[];
  doneTasks: string[];
  forgetFacts: string[];
};

/** Strip protocol lines so bubbles never show DECISION/FACT/TASK scaffolding. */
export function cleanDisplayText(raw: string): string {
  return (
    String(raw || "")
      // Hide leaked tool-call markup (must not show in chat bubbles).
      .replace(/\[\s*TOOL[_\s-]?CALL\s*\][\s\S]*?\[\s*\/\s*TOOL[_\s-]?CALL\s*\]/gi, "")
      .replace(/```\s*tool[_\s-]?call[\s\S]*?```/gi, "")
      .replace(/\{\s*tool\s*=>[\s\S]*?\}\s*/g, "")
      .replace(
        /(?:\*\*|__|`)*\s*DECISION\s*[:：]\s*(?:reply|silent|speak|pass|no_reply|noreply|回复|发言|说话|沉默|不回复|跳过)\s*(?:\*\*|__|`)*/gi,
        " ",
      )
      .replace(/^(?:FACT|TASK|ASK|DONE_TASK|DONE|FORGET|UNFACT)\s*[:：]\s*.+$/gim, "")
      .replace(/^(?:RULE|UNRULE|公告|取消公告)\s*[:：]\s*.+$/gim, "")
      .replace(/（已停止重复输出）/g, "")
      .replace(/[ \t]{2,}/g, " ")
      .replace(/ *\n */g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim()
  );
}

export function parseAgentDecision(raw: string): AgentDecision {
  const full = (raw || "").trim();
  const facts: string[] = [];
  const tasks: string[] = [];
  const doneTasks: string[] = [];
  const forgetFacts: string[] = [];
  let body = full;

  body = body
    .replace(
      /^(FACT|TASK|DONE_TASK|DONE|FORGET|UNFACT)\s*[:：]\s*(.+)$/gim,
      (_m: string, kind: string, val: string) => {
        const v = String(val).trim();
        if (!v) return "";
        const k = String(kind).toUpperCase();
        if (k === "FACT") facts.push(v);
        else if (k === "TASK") tasks.push(v);
        else if (k === "DONE_TASK" || k === "DONE") doneTasks.push(v);
        else forgetFacts.push(v);
        return "";
      },
    )
    .trim();

  // English or Chinese decision tokens, on any line.
  const decAnywhere = body.match(
    /(^|\n)\s*DECISION\s*[:：]\s*(reply|silent|speak|pass|no_reply|noreply|回复|发言|说话|沉默|不回复|跳过)(?=\s|$|[，。！？,.!?\n])[^\n]*/i,
  );
  if (decAnywhere) {
    const kindRaw = String(decAnywhere[2] || "").toLowerCase();
    const silent = ["silent", "pass", "no_reply", "noreply", "沉默", "不回复", "跳过"].includes(kindRaw);
    const isReply = ["reply", "speak", "回复", "发言", "说话"].includes(kindRaw);
    body = body.replace(decAnywhere[0], "\n").trim();
    body = cleanDisplayText(body);
    if (silent) {
      return { action: "silent", text: "", facts, tasks, doneTasks, forgetFacts };
    }
    if (isReply) {
      // Empty body after DECISION:reply is still a reply (placeholder), never silent.
      return { action: "reply", text: body, facts, tasks, doneTasks, forgetFacts };
    }
  }

  body = cleanDisplayText(body);
  if (!body || /^\[?NO_REPLY\]?$/i.test(body) || /^(无需回复|不回复|跳过|沉默)$/.test(body)) {
    return { action: "silent", text: "", facts, tasks, doneTasks, forgetFacts };
  }
  // No explicit DECISION: models often just answer — treat as reply so the UI is not blank.
  return { action: "reply", text: body, facts, tasks, doneTasks, forgetFacts };
}

export type StandingRule = { title: string; body: string };

function splitStandingRule(value: string): StandingRule {
  const parts = value.split(/\s*[|｜]\s*/);
  const head = (parts[0] || "").trim();
  if (parts.length >= 2 && head.length > 0 && head.length <= 40) {
    const body = parts.slice(1).join("｜").trim();
    return { title: head, body: body || head };
  }
  const body = value.trim();
  return { title: body.length > 22 ? body.slice(0, 22) : body, body };
}

/** Standing rules the agent pins to the group notice board, or asks to remove. */
export function parseStandingRules(raw: string): { rules: StandingRule[]; drops: string[] } {
  const rules: StandingRule[] = [];
  const drops: string[] = [];
  const re = /^(?:RULE|UNRULE|公告|取消公告)\s*[:：]\s*(.+)$/gim;
  let m: RegExpExecArray | null;
  while ((m = re.exec(String(raw || "")))) {
    const value = String(m[1] || "").trim();
    if (!value) continue;
    if (/^(?:UNRULE|取消公告)\s*[:：]/i.test(String(m[0] || "").trim())) drops.push(value);
    else rules.push(splitStandingRule(value));
  }
  return { rules, drops };
}

export function applyStandingRules(
  current: SocialAnnouncement[],
  rules: StandingRule[],
  drops: string[],
  author?: { kind?: "user" | "agent"; id?: string; name?: string },
): SocialAnnouncement[] {
  let next = [...(current || [])];
  for (const drop of drops) {
    const key = norm(drop);
    if (!key) continue;
    next = next.filter(
      (a) => !similar(a.title, key) && !similar(a.body, key) && !similar(`${a.title} ${a.body}`, key),
    );
  }
  const now = Date.now();
  for (const rule of rules) {
    const title = norm(rule.title);
    const body = norm(rule.body);
    if (!title || !body) continue;
    const idx = next.findIndex((a) => similar(a.title, title) || similar(a.body, body));
    if (idx >= 0) {
      const old = next[idx];
      next[idx] = {
        ...old,
        title,
        body,
        authorKind: author?.kind || old.authorKind,
        authorId: author?.id || old.authorId,
        authorName: author?.name || old.authorName,
      };
    } else {
      next.unshift({
        id: nid("ann"),
        title,
        body,
        createdAt: now,
        authorKind: author?.kind,
        authorId: author?.id,
        authorName: author?.name,
      });
    }
  }
  return next.slice(0, 24);
}


export function autoExtractFacts(reply: string, max = 2): string[] {
  const text = norm(reply);
  if (!text || text.length < 8) return [];
  const out: string[] = [];
  const re = /(?:结论|决定|确认|约定|记住|目标)[:：]\s*(.+?)(?:[。；\n]|$)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) && out.length < max) {
    const v = norm(m[1] || "");
    if (v.length >= 6 && v.length <= 80) out.push(v);
  }
  for (const line of text.split(/[\n•·]/)) {
    if (out.length >= max) break;
    const v = norm(line.replace(/^\s*[-—]\s*/, ""));
    if (v.length >= 8 && v.length <= 60 && /(?:是|为|将|要|已|确定)/.test(v)) out.push(v);
  }
  return out.slice(0, max);
}

export function upsertFacts(board: GroupSharedBoard, agent: SocialAgent, texts: string[]): GroupSharedBoard {
  const next: GroupSharedBoard = { ...board, facts: [...board.facts] };
  const now = Date.now();
  let changed = false;
  for (const raw of texts) {
    const t = norm(raw);
    if (!t) continue;
    const idx = next.facts.findIndex((f) => similar(f.text, t));
    if (idx >= 0) {
      const old = next.facts[idx];
      next.facts[idx] = {
        ...old,
        text: t.length >= old.text.length ? t : old.text,
        byAgentId: agent.id,
        byAgentName: agent.name,
        updatedAt: now,
        hits: (old.hits || 1) + 1,
      };
      changed = true;
    } else {
      next.facts.push({
        id: nid("fact"),
        text: t,
        byAgentId: agent.id,
        byAgentName: agent.name,
        at: now,
        updatedAt: now,
        hits: 1,
      });
      changed = true;
    }
  }
  if (!changed) return board;
  next.facts = next.facts.slice(-30);
  next.updatedAt = now;
  return next;
}

export function upsertTasks(board: GroupSharedBoard, agent: SocialAgent, texts: string[]): GroupSharedBoard {
  const next: GroupSharedBoard = { ...board, tasks: [...board.tasks] };
  const now = Date.now();
  let changed = false;
  for (const raw of texts) {
    const t = norm(raw);
    if (!t) continue;
    const idx = next.tasks.findIndex((x) => x.status === "open" && similar(x.text, t));
    if (idx >= 0) {
      next.tasks[idx] = {
        ...next.tasks[idx],
        text: t.length >= next.tasks[idx].text.length ? t : next.tasks[idx].text,
        ownerId: agent.id,
        ownerName: agent.name,
        at: now,
      };
      changed = true;
    } else {
      next.tasks.push({
        id: nid("task"),
        text: t,
        ownerId: agent.id,
        ownerName: agent.name,
        status: "open",
        at: now,
      });
      changed = true;
    }
  }
  if (!changed) return board;
  next.tasks = next.tasks.slice(-30);
  next.updatedAt = now;
  return next;
}

export function completeTasks(board: GroupSharedBoard, texts: string[]): GroupSharedBoard {
  if (!texts.length) return board;
  const nextTasks = board.tasks.map((t) => ({ ...t }));
  const now = Date.now();
  let changed = false;
  for (const raw of texts) {
    const q = norm(raw);
    if (!q) continue;
    for (const task of nextTasks) {
      if (task.status !== "open") continue;
      if (similar(task.text, q) || task.text.includes(q) || q.includes(task.text)) {
        task.status = "done";
        task.at = now;
        changed = true;
      }
    }
  }
  if (!changed) return board;
  return { ...board, tasks: nextTasks, updatedAt: now };
}

export function forgetFacts(board: GroupSharedBoard, texts: string[]): GroupSharedBoard {
  if (!texts.length) return board;
  const nextFacts = board.facts.filter(
    (f) => !texts.some((q) => similar(f.text, q) || f.text.includes(norm(q))),
  );
  if (nextFacts.length === board.facts.length) return board;
  return { ...board, facts: nextFacts, updatedAt: Date.now() };
}

export function applyAgentOutcome(
  board: GroupSharedBoard,
  agent: SocialAgent,
  decision: AgentDecision,
  opts?: { autoExtract?: boolean; writeFacts?: boolean },
): GroupSharedBoard {
  let next = board;
  let facts = [...decision.facts];
  if (opts?.autoExtract !== false && decision.action === "reply" && facts.length === 0) {
    facts = autoExtractFacts(decision.text);
  }
  // Facts are consensus-gated at round end unless writeFacts:true (legacy/manual).
  if (opts?.writeFacts) {
    next = upsertFacts(next, agent, facts);
  }
  next = upsertTasks(next, agent, decision.tasks);
  next = completeTasks(next, decision.doneTasks);
  // Forgetting durable facts also requires consensus — ignore per-agent FORGET here.
  if (opts?.writeFacts) {
    next = forgetFacts(next, decision.forgetFacts);
  }
  if (next !== board) {
    next = { ...next, updatedAt: Date.now() };
  }
  return next;
}

/** Write FACT proposals only when more than half of voters agree (similar text). */
export function commitConsensusFacts(
  board: GroupSharedBoard,
  proposals: { agent: SocialAgent; facts: string[] }[],
  voterCount: number,
): GroupSharedBoard {
  const voters = Math.max(1, voterCount);
  const need = Math.floor(voters / 2) + 1; // strict majority
  type Cluster = { text: string; agentIds: Set<string>; agent: SocialAgent };
  const clusters: Cluster[] = [];
  for (const p of proposals) {
    for (const raw of p.facts) {
      const t = norm(raw);
      if (!t || t.length < 4) continue;
      let c = clusters.find((x) => similar(x.text, t));
      if (!c) {
        c = { text: t, agentIds: new Set(), agent: p.agent };
        clusters.push(c);
      }
      c.agentIds.add(p.agent.id);
      if (t.length > c.text.length) c.text = t;
      c.agent = p.agent;
    }
  }
  let next = board;
  for (const c of clusters) {
    if (c.agentIds.size >= need) {
      next = upsertFacts(next, c.agent, [c.text]);
    }
  }
  return next;
}

export function beginWorkingRound(
  board: GroupSharedBoard,
  userPreview: string,
  roster: SocialAgent[],
): GroupSharedBoard {
  const at = Date.now();
  const workingRound: BoardRound = {
    id: nid("round"),
    userPreview: summarizeReply(userPreview, 80),
    entries: roster.map((a) => ({
      agentId: a.id,
      agentName: a.name,
      action: "silent",
      summary: "",
      at,
    })),
    at,
  };
  const next = { ...board, workingRound, updatedAt: at };
  next.digest = rerebuildDigest(next);
  return next;
}

export function bumpWorkingEntry(board: GroupSharedBoard, entry: BoardTurnEntry): GroupSharedBoard {
  const base = board.workingRound;
  const at = Date.now();
  if (!base) {
    const workingRound: BoardRound = {
      id: nid("round"),
      userPreview: "",
      entries: [entry],
      at,
    };
    const next = { ...board, workingRound, updatedAt: at };
    next.digest = rerebuildDigest(next);
    return next;
  }
  const entries = [...base.entries];
  const idx = entries.findIndex((e) => e.agentId === entry.agentId);
  if (idx >= 0) entries[idx] = { ...entry, at };
  else entries.push({ ...entry, at });
  const workingRound = { ...base, entries, at };
  const next = { ...board, workingRound, updatedAt: at };
  next.digest = rerebuildDigest(next);
  return next;
}

export function consolidateBoard(board: GroupSharedBoard): GroupSharedBoard {
  const now = Date.now();
  const DAY = 86400000;
  let tasks = board.tasks.filter((t) => t.status === "open" || now - t.at < DAY * 3);
  const ranked = [...board.facts].sort((a, b) => {
    const sa = (a.hits || 1) * 1e12 + (a.updatedAt || a.at);
    const sb = (b.hits || 1) * 1e12 + (b.updatedAt || b.at);
    return sb - sa;
  });
  const merged: BoardFact[] = [];
  for (const f of ranked) {
    const hit = merged.find((m) => similar(m.text, f.text));
    if (hit) {
      hit.hits = (hit.hits || 1) + (f.hits || 1);
      hit.updatedAt = Math.max(hit.updatedAt || hit.at, f.updatedAt || f.at);
      if ((f.text || "").length > hit.text.length) hit.text = f.text;
    } else {
      merged.push({ ...f });
    }
  }
  const facts = merged.slice(0, 24);
  tasks = tasks.slice(-24);
  const next = { ...board, facts, tasks, updatedAt: now };
  next.digest = rerebuildDigest(next);
  return next;
}

export function commitRound(
  board: GroupSharedBoard,
  userPreview: string,
  entries?: BoardTurnEntry[],
): GroupSharedBoard {
  const at = Date.now();
  const round: BoardRound = {
    id: board.workingRound?.id || nid("round"),
    userPreview: summarizeReply(userPreview || board.workingRound?.userPreview || "", 80),
    entries: entries || board.workingRound?.entries || [],
    at,
  };
  const next: GroupSharedBoard = {
    ...board,
    revision: board.revision + 1,
    updatedAt: at,
    lastRound: round,
    workingRound: null,
    recentRounds: [round, ...board.recentRounds].slice(0, 8),
  };
  next.digest = rerebuildDigest(next);
  return consolidateBoard(next);
}

export function removeFact(board: GroupSharedBoard, factId: string): GroupSharedBoard {
  const facts = board.facts.filter((f) => f.id !== factId);
  if (facts.length === board.facts.length) return board;
  const next = { ...board, facts, revision: board.revision + 1, updatedAt: Date.now() };
  next.digest = rerebuildDigest(next);
  return next;
}

export function setTaskStatus(
  board: GroupSharedBoard,
  taskId: string,
  status: "open" | "done",
): GroupSharedBoard {
  const tasks = board.tasks.map((t) => (t.id === taskId ? { ...t, status, at: Date.now() } : t));
  const next = { ...board, tasks, revision: board.revision + 1, updatedAt: Date.now() };
  next.digest = rerebuildDigest(next);
  return next;
}

/** Backward-compatible alias. */
export function applyPatches(
  board: GroupSharedBoard,
  agent: SocialAgent,
  facts: string[],
  tasks: string[],
): GroupSharedBoard {
  return applyAgentOutcome(board, agent, {
    action: "reply",
    text: "",
    facts,
    tasks,
    doneTasks: [],
    forgetFacts: [],
  });
}

export function boardProtocolHint(wave: 1 | 2 = 1): string {
  const step =
    wave === 2
      ? "用工具持续做，直到交给你的事达到用户目标。做完再用结果回复。计划、进度说明和做了一半都不是结束。\n"
      : "没有新内容就 silent。\n";
  return (
    "\n\n第一行只能是 DECISION: reply 或 DECISION: silent。\n" +
    step +
    "reply 的下一行是给群里看的话。silent 不要正文。\n" +
    "可选：TASK: 你认领的事；ASK: @名字 | 问题；DONE_TASK: 完成的事。\n"
  );
}

