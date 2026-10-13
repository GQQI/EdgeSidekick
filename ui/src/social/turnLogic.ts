import { getGroupBoard } from "./store";
import { cleanDisplayText, similar, summarizeReply } from "./sharedBoard";
import type { SocialStore } from "./types";

export function buildGroupBrief(store: SocialStore, groupId: string | undefined, limit = 8): string {
  if (!groupId) return "";
  const board = getGroupBoard(store, groupId);
  const facts = (board?.facts || []).slice(-limit).map((f) => `- ${f.text}`).join("\n");
  const group = store.groups.find((g) => g.id === groupId);
  const topic = group?.topic ? `群主题: ${group.topic}` : "";
  const name = group?.name ? `群名: ${group.name}` : "";
  const parts = [name, topic, facts ? `共享要点:\n${facts}` : ""].filter(Boolean);
  return parts.join("\n");
}



/** Normalize task text for duplicate-claim collapse. */
export function normalizeTaskKey(raw: string): string {
  return String(raw || "")
    .toLowerCase()
    .replace(/[\s\p{P}\p{S}]+/gu, "")
    .slice(0, 48);
}

/**
 * A greeting/acknowledgement does not need a full group deliberation.  Keep
 * this deliberately narrow: as soon as the user includes a concrete request,
 * it falls back to the normal collaborative routing path.
 */
export function isLightweightSocialTurn(raw: string): boolean {
  const text = String(raw || "")
    .replace(/@[\w\u4e00-\u9fff]+/g, "")
    .replace(/[\s，,。！？!~～…、:：；;（）()【】\[\]"'“”‘’]/g, "")
    .toLowerCase();
  if (!text || text.length > 24) return false;
  return /^(你好|您好|嗨|哈喽|hello|hi|hey|早上好|上午好|中午好|下午好|晚上好|晚安|在吗|谢谢|感谢|辛苦了|再见|拜拜|bye)+$/i.test(
    text,
  );
}

/** A short hello to the group. Every member answers; a hidden judge must not veto it. */
export function isGroupGreeting(raw: string): boolean {
  if (isLightweightSocialTurn(raw) || isCollectiveGreeting(raw)) return true;
  const text = String(raw || "")
    .replace(/@[\w\u4e00-\u9fff]+/g, "")
    .replace(/[\s，,。！？!~～…、:：；;（）()【】\[\]"'“”‘’]/g, "");
  if (!text || text.length > 12) return false;
  return /^(?:你好|您好|嗨|哈喽|hello|hi|hey|大家好|各位好|早上好|下午好|晚上好)/i.test(text);
}

/** Used only when a greeting turn's model reply is empty or a silence token. */
export function greetingFallback(name: string): string {
  const who = String(name || "").trim() || "我";
  return `你好，我是${who}。`;
}

/**
 * Chat status for one group turn. The count is the whole roster.
 * The member currently judging or replying is not omitted from that count.
 */
export function groupTurnStatusLabel(
  slots: Array<{ name: string; stage: string; streaming?: boolean; activityText?: string }>,
): string {
  const rosterSize = slots.length;
  if (!rosterSize) return "";
  const current =
    slots.find((slot) => slot.streaming) ||
    slots.find((slot) => slot.stage === "deciding" || slot.stage === "executing");
  const waiting = slots.filter((slot) => slot.stage === "reading" || slot.stage === "queued").length;
  if (current) {
    const action =
      current.activityText ||
      (current.stage === "deciding" ? "正在判断要不要回复" : "正在回复");
    const rest = Math.max(rosterSize - 1, 0);
    return `全组 ${rosterSize} 人：${current.name}${action}，其余 ${rest} 人排队…`;
  }
  if (waiting) return `全组 ${rosterSize} 人正在读取这条消息…`;
  return "";
}

/** A group-facing greeting is intentionally social: every member may answer. */
export function isCollectiveGreeting(raw: string): boolean {
  // `@全体` can introduce either a greeting or a real work request.  Only the
  // wording itself decides whether every member should reply socially.
  const text = String(raw || "")
    .replace(/@[\w\u4e00-\u9fff]+/g, "")
    .replace(/\s+/g, "");
  return /^(?:大家|各位|各位同事|伙伴们|诸位|所有人).{0,4}(?:好|您好|早上好|下午好|晚上好)[！!。．…]*$/.test(text);
}

/**
 * Detect a model response that promises a future action rather than delivering
 * a result. This is a protocol-violation guard, not a request-specific route:
 * it works for any capability (files, web, tools, or an external system).
 */
export function hasUnfinishedActionCommitment(raw: string): boolean {
  const text = cleanDisplayText(String(raw || "")).replace(/\s+/g, " ").trim();
  if (!text) return false;
  return /(?:我(?:来|会|将要|先去|正在)|请稍等|稍等)(?:处理|查|改|修|跑|做|执行|打开)|i(?:'ll| will) (?:check|fix|run|open)/i.test(text);
}

/**
 * A real schedule order (create / remind), not a question about whether the
 * feature exists. "设置" alone is not an execution verb elsewhere, so this
 * is matched on its own and then treated as execution.
 */
export function isScheduleRequest(raw: string): boolean {
  const text = String(raw || "").trim();
  if (!text || isCapabilityQuestion(text)) return false;
  if (!/(?:定时任务|定时提醒|提醒我|设(?:置|一个|个)?定时|日程|每天.{0,12}(?:点|提醒)|每周|每隔\s*\d+|每\d+\s*分钟|schedule)/i.test(text)) {
    return false;
  }
  if (/[？?]|(?:是什么|有没有|是否支持|能不能用|可以吗|怎么设置)/.test(text) && !/(?:提醒我|帮我|创建|每天|每周|每隔)/.test(text)) {
    return false;
  }
  return true;
}

/** A short follow-up that should stay with the agent who just spoke. */
export function isContinuationRequest(raw: string): boolean {
  const bare = String(raw || "")
    .replace(/@[\w\u4e00-\u9fff]+/g, "")
    .trim();
  if (!bare || bare.length > 40) return false;
  return /^(?:请|那|好|嗯)?(?:你)?(?:继续|接着|然后继续|再继续|往下)(?:执行|做|处理|跑|查|说|写|吧|一下)?/.test(bare)
    || /继续执行|接着执行|按这个继续|按刚才的继续/.test(bare);
}

/**
 * "谁写的谁来改" style: the person who already did the work should do the
 * follow-up. Not a question, and not one fixed sentence.
 */
/**
 * Who already did the work this follow-up points at. Edit notes win, then a
 * task about writing or changing files, then the latest reply that actually
 * wrote something.
 */
export function resolveAuthorId(
  roster: Array<{ id: string; name: string }>,
  tasks: Array<{ status?: string; ownerId?: string; text?: string }>,
  notes: string[],
  history: Array<{ role?: string; agentId?: string; text?: string }>,
): string | undefined {
  const ids = new Set(roster.map((agent) => agent.id));
  const byName = [...roster].filter((agent) => agent.name).sort((a, b) => b.name.length - a.name.length);
  for (const note of [...notes].reverse()) {
    const agent = byName.find((item) => note.startsWith(`${item.name} `) && /编辑了|写入了/.test(note));
    if (agent) return agent.id;
  }
  for (const task of [...tasks].reverse()) {
    if (task.ownerId && ids.has(task.ownerId) && /写|实现|创建|修改|修复|文件/.test(task.text || "")) {
      return task.ownerId;
    }
  }
  for (const msg of [...history].reverse()) {
    if (msg.role !== "agent" || !msg.agentId || !ids.has(msg.agentId)) continue;
    if (/写入|创建的文件|已完成|修改了|编辑了/.test(String(msg.text || ""))) return msg.agentId;
  }
  const done = [...tasks].reverse().find((task) => task.status === "done" && task.ownerId && ids.has(task.ownerId));
  return done?.ownerId;
}

export function isAuthorCallback(raw: string): boolean {
  const text = String(raw || "")
    .replace(/@[\w\u4e00-\u9fff]+/g, "")
    .trim();
  if (!text || /[？?]/.test(text) || isCapabilityQuestion(text)) return false;
  return /谁[\u4e00-\u9fff]{1,16}谁/.test(text);
}

/** Only explicit operational language may start the task-execution path. */
export function isExecutionRequested(raw: string): boolean {
  const text = String(raw || "").trim();
  if (!text || isLightweightSocialTurn(text)) return false;
  if (isCapabilityQuestion(text)) return false;
  if (isScheduleRequest(text)) return true;
  if (isAuthorCallback(text)) return true;
  if (/[？?]|(?:有没有|好不好|行不行|能不能)/.test(text)) return false;
  return /(?:帮我|请|麻烦|执行|开始|创建|生成|修改|修复|实现|运行|搜索|查找|打开|删除|安装|部署|写(?:一|个|份|篇|到)|整理(?:成|到)|翻译(?:成|为)|分析(?:文件|代码|数据|报告)|(?:来|去)改|改(?:一下|成|为|掉|好)|重写|调整一下)/.test(
    text,
  );
}

/** A question about who someone is or what they can do. Not a work order. */
export function isCapabilityQuestion(raw: string): boolean {
  return /(?:你是干啥|你是谁|你能做什么|你会什么|你的职责|介绍(?:一下)?你|有(?:什么|啥|哪些)(?:功能|能力)|有哪些功能|有什么功能|什么功能|哪些功能|能做什么|会做什么|能干啥|会啥|擅长什么|你能干什么|你会干什么)/.test(
    String(raw || ""),
  );
}

/** Questions are answered naturally; they are not silently converted to work orders. */
export function isDirectAnswerTurn(raw: string, mentionedCount: number): boolean {
  const text = String(raw || "").trim();
  if (!text || isExecutionRequested(text)) return false;
  const profileQuestion = isCapabilityQuestion(text);
  const ordinaryQuestion = /[？?]|(?:是什么|为什么|怎么|怎样|如何|能否|是否|多少)/.test(text);
  return profileQuestion || (mentionedCount > 0 && ordinaryQuestion) || (mentionedCount === 0 && ordinaryQuestion);
}

/** Keep work already assigned when an execution reply forgets to repeat TASK: lines. */
export function mergeExecutionTasks(assigned: string[], claimed: string[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const raw of [...assigned, ...claimed]) {
    const task = String(raw || "").trim();
    if (!task) continue;
    const key = normalizeTaskKey(task) || task;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(task);
  }
  return out;
}

/**
 * Assigned work closes only when the runtime reports the user goal is met.
 * A progress reply, or a DONE_TASK line emitted before that, does not close it.
 */
export function doneTasksForExecution(
  assigned: string[],
  explicitDone: string[],
  goalSatisfied: boolean,
): string[] {
  if (!goalSatisfied) return [];
  const explicit = explicitDone.map((task) => String(task || "").trim()).filter(Boolean);
  const carried = assigned.map((task) => String(task || "").trim()).filter(Boolean);
  return [...new Set([...explicit, ...carried])];
}

/**
 * Parallelism is opt-in from the actual task graph: different, non-mutating
 * assignments may run together.  Anything that can change shared/external
 * state stays ordered, so concurrency never becomes a race-condition switch.
 */
export function canRunTasksInParallel<T extends { agent: { id: string } }>(slots: T[]): boolean {
  if (slots.length < 2) return false;
  const assignments = slots.map((slot) => (((slot as any)._tasks as string[]) || []).filter(Boolean));
  if (assignments.some((tasks) => tasks.length !== 1)) return false;
  const taskKeys = assignments.map((tasks) => normalizeTaskKey(tasks[0]));
  if (taskKeys.some((key) => !key) || new Set(taskKeys).size !== taskKeys.length) return false;
  const exclusive = /(删除|覆盖|写入|修改|创建|发送|部署|安装|运行|执行|命令|终端|支付|提交|发布)/;
  return assignments.every((tasks) => !exclusive.test(tasks[0]));
}

/**
 * One operator for a work order. Conversational turns do not use this:
 * every member reads each new message and decides alone.
 * Tie goes to roster order.
 */
export function pickSoloSpeaker<
  T extends { id: string; name?: string; title?: string; duty?: string; blurb?: string; persona?: string },
>(roster: T[], text: string): T | undefined {
  if (!roster.length) return undefined;
  let best = roster[0];
  let bestScore = dutyScore(best, text);
  for (let i = 1; i < roster.length; i += 1) {
    const score = dutyScore(roster[i], text);
    if (score > bestScore) {
      best = roster[i];
      bestScore = score;
    }
  }
  return best;
}

/** Rough duty match: higher = better owner for this user ask / task. */
export function dutyScore(
  agent: { name?: string; title?: string; duty?: string; blurb?: string; persona?: string },
  hay: string,
): number {
  const blob = `${agent.name || ""} ${agent.title || ""} ${agent.duty || ""} ${agent.blurb || ""} ${agent.persona || ""}`.toLowerCase();
  const words = String(hay || "")
    .toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .filter((w) => w.length >= 2);
  let score = 0;
  for (const w of words) {
    if (blob.includes(w)) score += 1;
  }
  if (/有没有|好不好|行不行/.test(hay) && /审/.test(blob)) score += 3;
  return score;
}

/** Cap on replies one user message may pull in, so a chatty floor still ends. */
export function maxReactionTurns(rosterSize: number): number {
  const count = Math.max(0, rosterSize);
  return Math.min(12, Math.max(count * 3, count));
}

/**
 * After a new message, every other member judges. Addressed members go first.
 * The author of the newest message is left out until someone else speaks.
 */
export function planReactionOrder(
  roster: Array<{ id: string }>,
  opts?: { latestAuthorId?: string; addressedIds?: Iterable<string> },
): string[] {
  const latest = String(opts?.latestAuthorId || "");
  const addressed = new Set(opts?.addressedIds || []);
  return roster
    .map((agent, index) => ({ id: agent.id, index, hot: addressed.has(agent.id) }))
    .sort((a, b) => Number(b.hot) - Number(a.hot) || a.index - b.index)
    .map((row) => row.id)
    .filter((id) => id && id !== latest);
}

/**
 * After discuss wave: one owner per task intent; auto-pick one if nobody claimed.
 */

export function formatPeerDiscuss(
  rows: Array<{ agentName: string; text: string }>,
): string {
  if (!rows.length) return "";
  const windowed = buildSlidingWindow(rows.map((row) => ({ name: row.agentName, text: row.text })));
  const summary = windowed.summary ? `更早的滑动摘要：\n${windowed.summary}\n` : "";
  const recent = windowed.recent.map((row, index) => `${index + 1}. ${row.name}：${row.text}`).join("\n");
  return summary + recent;
}

/** Recent messages stay verbatim. Older ones fold into a short sliding summary. */
export function buildSlidingWindow(
  rows: Array<{ name: string; text: string }>,
  recentCount = 6,
): { summary: string; recent: Array<{ name: string; text: string }> } {
  const clip = (text: string, max: number) => (text.length <= max ? text : `${text.slice(0, max - 1)}…`);
  const clean = rows
    .map((row) => ({
      name: String(row.name || "成员").trim() || "成员",
      text: cleanDisplayText(row.text || "").replace(/\s+/g, " ").trim(),
    }))
    .filter((row) => row.text);
  if (clean.length <= recentCount) {
    return { summary: "", recent: clean.map((row) => ({ name: row.name, text: clip(row.text, 180) })) };
  }
  const older = clean.slice(0, -recentCount);
  const recent = clean.slice(-recentCount).map((row) => ({ name: row.name, text: clip(row.text, 180) }));
  const chunks: string[] = [];
  for (let i = 0; i < older.length; i += 4) {
    const piece = older.slice(i, i + 4).map((row) => `${row.name}：${clip(row.text, 70)}`).join("；");
    chunks.push(clip(piece, 200));
  }
  return { summary: chunks.slice(-3).join("\n"), recent };
}

/** TASK: lines in a discuss bubble (protocol claim — not NL verb lists). */
export function tasksClaimedInText(text: string): string[] {
  const out: string[] = [];
  const re = /^TASK\s*[:：]\s*(.+)$/gim;
  let m: RegExpExecArray | null;
  const raw = String(text || "");
  while ((m = re.exec(raw))) {
    const v = String(m[1] || "").trim();
    if (v) out.push(v);
  }
  return out;
}

/** True when this bubble structurally claims work via TASK:. */
export function isSelfAccept(text: string): boolean {
  return tasksClaimedInText(text).length > 0;
}

/**
 * Structural nominations only: @显示名
 * (No「交给/让/请…」verb lists — prompts tell members to @ the owner.)
 */
export function extractNominees(
  text: string,
  roster: Array<{ id: string; name: string }>,
  selfId: string,
): string[] {
  const t = String(text || "");
  const hits: string[] = [];
  for (const a of roster) {
    if (a.id === selfId || !a.name) continue;
    if (t.includes("@" + a.name)) hits.push(a.id);
  }
  return hits;
}

/** Prefer acceptor / most-nominated; else TASK-claim collapse. */

/** Who was asked to confirm / reply in this discuss line? */

/** @handoff / 推进下一步 — keep the group turn alive for the named agent. */


export function formatMsgTime(at?: number): string {
  if (!at) return "";
  try {
    return new Date(at).toLocaleString();
  } catch {
    return "";
  }
}

export async function copyBubbleText(text: string): Promise<boolean> {
  const v = (text || "").trim();
  if (!v) return false;
  try {
    await navigator.clipboard.writeText(v);
    return true;
  } catch {
    try {
      const ta = document.createElement("textarea");
      ta.value = v;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return ok;
    } catch {
      return false;
    }
  }
}

/** Normalize bubble text for duplicate detection. */

/** Agents themselves confirmed the user's turn is done: no open board tasks and no pending @handoff. */
export function agentsConfirmedUserTaskDone(
  board: { tasks?: Array<{ status?: string }> } | null | undefined,
  recentAgentTexts: string[],
  roster: Array<{ id: string; name: string }>,
): boolean {
  const open = (board?.tasks || []).filter((t) => String(t.status || "") === "open");
  if (open.length > 0) return false;
  const last = [...recentAgentTexts].reverse().find((t) => String(t || "").trim());
  if (!last) return true;
  // An @ alone is a conversation, not unfinished work.
  if (
    findHandoffAgentIds(String(last), roster, "").length > 0 &&
    /(实现|修改|修复|运行|查一下|处理|交给你做|请你改|请你跑)/.test(last)
  ) {
    return false;
  }
  // Prefer explicit DONE_TASK / completion cues when board had no tasks.
  const doneCue = /(DONE_TASK|DONE\s*[:：]|已完成|完成了|任务完成|搞定了|交付完毕|可以收工)/i.test(last);
  const stillWorking = /(继续|下一步|推进中|我再|正在处理|还没|未完成)/.test(last);
  if (stillWorking && !doneCue) return false;
  return true;
}


/**
 * True only when this member is blocked on the human: it needs a confirmation,
 * a choice, or a missing fact before it can continue. An offer or a greeting
 * question is conversation, and must not pause the turn.
 */
export function agentAsksForUserInput(text: string): boolean {
  const t = String(text || "").trim();
  if (!t) return false;
  // 「请 @人类用户 确认…」— @human sits between 请 and 确认, so allow a short gap.
  if (/@人类用户|@用户\b/.test(t) && /(确认|提供|告诉|选择|回复|决定|是否|范围)/.test(t)) {
    return true;
  }
  if (
    /(请(你|您|用户)?.{0,24}(提供|告诉|确认|补充|输入|选择|填写)|需要(你|您|用户)(们)?(来)?(提供|确认|补充|决定|选择)|等(你|您|用户)(确认|回复|决定|选择)|确认后再|确认后我|确认之后|请用户)/.test(
      t,
    )
  ) {
    return true;
  }
  // A question mark is not itself a hold. Pause only when the question asks
  // for a decision or a fact this member cannot continue without.
  if (/[?？]/.test(t) && /(确认|请选择|选择哪|哪[个里]|是否|路径|账号|密码)/.test(t)) {
    return true;
  }
  if (/please (provide|confirm|choose)|need you to (confirm|provide|choose|decide)|could you (please )?(provide|confirm)/i.test(t)) {
    return true;
  }
  return false;
}


export function isPublicHttpUrl(url: string): boolean {
  const u = String(url || "").trim().toLowerCase();
  if (!u.startsWith("http://") && !u.startsWith("https://")) return false;
  try {
    const host = new URL(u).hostname;
    if (host === "localhost" || host === "127.0.0.1" || host === "[::1]" || host.endsWith(".localhost")) return false;
  } catch {
    return true;
  }
  return true;
}

export function msgFingerprint(agentId: string, text: string): string {
  return `${agentId}::${String(text || "").replace(/\s+/g, " ").trim().slice(0, 240)}`;
}

/**
 * Hidden verdict before any visible reply. The model reads the user message
 * and what earlier members already said, then answers speak or silent.
 * That word is not a chat bubble.
 */
/** Join wrapped sentences. Keep list items, headings, and code fences. */
export function tightenMarkdownFlow(raw: string): string {
  const lines = String(raw || "").replace(/\r\n/g, "\n").split("\n");
  const out: string[] = [];
  let fence = false;
  const item = (s: string) => /^(?:[-*+]|\d+[.)])\s+\S/.test(s);
  const structural = (s: string) => /^(?:#{1,6}\s|>\s|\|)/.test(s) || item(s);
  const gap = (prev: string, next: string) => {
    if (/[\u4e00-\u9fff。！？，、；：]$/.test(prev) || /^[\u4e00-\u9fff]/.test(next)) return "";
    if (/[.!?,;:]$/.test(prev)) return " ";
    return " ";
  };
  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith("```")) {
      fence = !fence;
      out.push(line);
      continue;
    }
    if (fence) {
      out.push(line);
      continue;
    }
    if (!trimmed) {
      if (out.length && out[out.length - 1] !== "") out.push("");
      continue;
    }
    const prev = out.length ? out[out.length - 1] : "";
    const prevTrim = prev.trim();
    if (
      prevTrim &&
      prev !== "" &&
      !structural(trimmed) &&
      !prevTrim.startsWith("```") &&
      (!structural(prevTrim) || item(prevTrim))
    ) {
      out[out.length - 1] = prev.replace(/\s+$/, "") + gap(prevTrim, trimmed) + trimmed;
      continue;
    }
    out.push(trimmed);
  }
  const compact: string[] = [];
  for (let i = 0; i < out.length; i += 1) {
    const next = (out[i + 1] || "").trim();
    if (out[i] === "" && item(compact[compact.length - 1] || "") && item(next)) continue;
    compact.push(out[i]);
  }
  return compact.join("\n").replace(/\n{3,}/g, "\n\n");
}

export function workNoteFromTool(
  who: string,
  tool: string,
  data: { args?: Record<string, unknown>; preview?: string; result?: string; ok?: boolean },
): string {
  if (data?.ok === false) return "";
  const args = data?.args || {};
  const path = String(args.path || args.file_path || args.target_file || "");
  const base = path.replace(/\\/g, "/").split("/").pop() || path;
  const clip = (value: unknown, limit = 28) => String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);
  if (tool === "str_replace") {
    const oldText = clip(args.old_string || args.old);
    const newText = clip(args.new_string || args.new);
    const change = oldText && newText ? `把「${oldText}」改成「${newText}」` : "修改了内容";
    return `${who} 编辑了 ${base || "文件"}：${change}`.slice(0, 96);
  }
  if (tool === "write_file" || tool === "apply_patch") {
    const snippet = clip(args.content || args.patch || data.preview || data.result, 36);
    return `${who} 写入了 ${base || "文件"}：${snippet}`.slice(0, 96);
  }
  if (tool === "delete_file") return `${who} 删除了 ${base || "文件"}`.slice(0, 96);
  if (tool === "list_dir") {
    const preview = clip(data.preview || data.result, 48);
    return `${who} 查看了目录 ${path || base || "."}：${preview}`.slice(0, 96);
  }
  return "";
}

export function buildSpeakJudgePrompt(input: {
  name: string;
  duty?: string;
  userText: string;
  prior: Array<{ name: string; text: string }>;
  history?: Array<{ name: string; text: string }>;
  addressed: boolean;
  greeting?: boolean;
  capability?: boolean;
  context?: string;
}): string {
  const windowed = buildSlidingWindow([
    ...(input.history || []).map((row) => ({ name: row.name, text: row.text })),
    ...input.prior,
  ]);
  const prior = windowed.recent.length
    ? windowed.recent.map((row, index) => `${index + 1}. ${row.name}：${row.text}`).join("\n")
    : "（窗口里还没有消息）";
  const history = windowed.summary || "（没有落在窗口外、需要摘要的更早消息）";
  const facts = [
    input.greeting ? "用户在跟群里的人打招呼。" : "",
    input.capability ? "用户在问功能、身份或能力。" : "",
    input.addressed ? "用户点名了这个成员。" : "用户没有点名这个成员，没有点名也可以发言。",
    input.prior.length ? "本轮已经有人说过话。同事的新消息也要重新判断。" : "本轮还没有人回答这条消息。",
    input.context?.trim() ? "工作现场里有文件改动或目录查看，窗口里未必已经说过。" : "",
  ].filter(Boolean);
  const site = String(input.context || "").trim();
  return [
    `你是群成员「${input.name}」。`,
    input.duty ? `职责：${input.duty}` : "",
    "你看到的是滑动窗口，加上工作现场：谁编辑过哪个文件、改了什么。不只是上一句聊天。",
    "现在只判断你要不要开口，不要写发言内容，不要解释。",
    "只输出一个词：speak 或 silent。",
    "speak 表示根据这些事实，你现在应该发言。",
    "silent 表示根据这些事实，你现在不应该发言。",
    "每出现一条新消息，无论来自用户还是同事，都要重新判断。想回问候、补充、纠正、追问，或同事的话让你改了主意，就输出 speak。",
    "只会重复已经说过的话，或确实没有要接的，就输出 silent。不要因为没被点名、或已经有人开口，就自动沉默。",
    "如果窗口和工作现场里这件事已经说完，并且你没有新的一句，输出 silent。还有没被说出来的文件改动，就输出 speak。",
    `事实：\n${facts.map((fact) => `- ${fact}`).join("\n")}`,
    site ? `工作现场：\n${site}` : "",
    `滑动摘要：\n${history}`,
    `用户消息：\n${input.userText}`,
    `滑动窗口：\n${prior}`,
  ].filter(Boolean).join("\n");
}

const SILENCE_ONLY =
  /^(?:silent|pass|no_reply|noreply|沉默|不发言|不回复|不说|跳过|无需回复)$/i;

/** After the judge already said speak, a later silence token is not a reply. */
export function visibleReplyAfterJudge(raw: string): string {
  const text = cleanDisplayText(raw).trim();
  const flat = text.replace(/\s+/g, " ").replace(/[。.!！]+$/, "").trim();
  if (!flat || SILENCE_ONLY.test(flat)) return "";
  return text;
}

export function parseSpeakVerdict(raw: string, preferSpeak: boolean): boolean {
  const text = String(raw || "").trim().toLowerCase();
  if (!text) return preferSpeak;
  const head = text.slice(0, 32);
  const silentFirst = /^(?:silent|pass|no_reply|noreply|沉默|不发言|不回复|不说|跳过)\b/.test(head);
  const speakFirst = /^(?:speak|reply|yes|发言|要说|要回复)\b/.test(head);
  if (silentFirst && !speakFirst) return false;
  if (speakFirst && !silentFirst) return true;
  const silentAt = head.search(/silent|pass|沉默|不发言|不回复|不说|跳过/);
  const speakAt = head.search(/speak|reply|发言|要说/);
  if (silentAt >= 0 && (speakAt < 0 || silentAt < speakAt)) return false;
  if (speakAt >= 0 && (silentAt < 0 || speakAt < silentAt)) return true;
  return preferSpeak;
}

/** Same agent + near-identical wording (paraphrased re-claims). */
export function isNearDuplicateBubble(
  agentId: string,
  text: string,
  existing: Array<{ agentId?: string; text?: string }>,
): boolean {
  const raw = String(text || "").replace(/\s+/g, " ").trim();
  if (!raw) return true;
  for (const m of existing) {
    if (String(m.agentId || "") !== agentId) continue;
    const prev = String(m.text || "").replace(/\s+/g, " ").trim();
    if (!prev) continue;
    if (msgFingerprint(agentId, raw) === msgFingerprint(agentId, prev)) return true;
    if (similar(raw, prev)) {
      // A later result that quotes an earlier line is still a new message.
      const longer = Math.max(raw.length, prev.length);
      const shorter = Math.min(raw.length, prev.length);
      if (longer >= shorter + 16) return false;
      return true;
    }
  }
  return false;
}

export function findHandoffAgentIds(
  text: string,
  roster: Array<{ id: string; name: string }>,
  selfId: string,
): string[] {
  const t = String(text || "");
  const hits: string[] = [];
  for (const a of roster) {
    if (!a.name || a.id === selfId) continue;
    if (t.includes("@" + a.name)) hits.push(a.id);
  }
  return hits;
}

export function findAskedAgentIds(
  text: string,
  roster: Array<{ id: string; name: string }>,
  selfId: string,
): string[] {
  return findHandoffAgentIds(text, roster, selfId);
}

/** Discuss can stop when someone accepted or a clear TASK owner exists. */
export function discussHasOwner(
  peerDiscussRows: Array<{ agentId: string; agentName: string; text: string }>,
  slots: Array<{ agent: { id: string }; }>,
): boolean {
  for (const row of peerDiscussRows) {
    if (isSelfAccept(row.text)) return true;
  }
  for (const s of slots) {
    const tasks = (((s as any)._tasks as string[]) || []).filter(Boolean);
    if (tasks.length > 0) return true;
  }
  return false;
}

/** Pending agents who were asked but have not spoken since the ask. */
export function findPendingDiscussIds(
  peerDiscussRows: Array<{ agentId: string; agentName: string; text: string }>,
  roster: Array<{ id: string; name: string }>,
): string[] {
  if (!peerDiscussRows.length) return [];
  const last = peerDiscussRows[peerDiscussRows.length - 1];
  const asked = findAskedAgentIds(last.text, roster, last.agentId);
  // Also: nominees who never accepted and never replied after being named.
  const nominateCount = new Map<string, number>();
  const spoken = new Set(peerDiscussRows.map((r) => r.agentId));
  const accepted = new Set(
    peerDiscussRows.filter((r) => isSelfAccept(r.text)).map((r) => r.agentId),
  );
  for (const row of peerDiscussRows) {
    for (const nid of extractNominees(row.text, roster, row.agentId)) {
      nominateCount.set(nid, (nominateCount.get(nid) || 0) + 1);
    }
  }
  const pending = new Set<string>(asked);
  for (const [id, n] of nominateCount) {
    if (n <= 0) continue;
    if (accepted.has(id)) continue;
    // Nominee should get a chance to say「我来」if they have not accepted yet.
    // If they already spoke before the nomination, they still need a follow-up turn.
    const lastNomRound = [...peerDiscussRows]
      .reverse()
      .find((r) => extractNominees(r.text, roster, r.agentId).includes(id));
    if (!lastNomRound) continue;
    const idx = peerDiscussRows.indexOf(lastNomRound);
    const repliedAfter = peerDiscussRows.slice(idx + 1).some((r) => r.agentId === id);
    if (!repliedAfter) pending.add(id);
  }
  // Asked agents: need a reply after the ask line.
  for (const id of asked) {
    const idx = peerDiscussRows.length - 1;
    const repliedAfter = peerDiscussRows.slice(idx + 1).some((r) => r.agentId === id);
    if (repliedAfter) pending.delete(id);
  }
  return [...pending];
}


export function resolveExecSlots<
  T extends {
    agent: { id: string; name?: string; title?: string; duty?: string; blurb?: string; persona?: string };
  },
>(
  slots: T[],
  _peerDiscussRows: Array<{ agentId: string; agentName: string; text: string }>,
  opts: {
    userText: string;
    mentionedIds: Set<string>;
    boardOwnerIds: string[];
    roster: Array<{ id: string; name: string }>;
  },
): T[] {
  // The discuss wave has already collected explicit TASK claims and board
  // owners.  Running the whole roster here replays the first speaker after it
  // has already spoken, and turns a single answer into an N-agent queue.
  const nominatedIds = new Set<string>();
  for (const row of _peerDiscussRows) {
    for (const id of extractNominees(row.text, opts.roster, row.agentId)) nominatedIds.add(id);
  }
  return pickExecSlots(slots, { ...opts, nominatedIds });
}

/** A targeted peer question in the group coordination protocol. */
export function coordinationAsksInText(text: string): Array<{ targetName: string; question: string }> {
  const asks: Array<{ targetName: string; question: string }> = [];
  const re = /^ASK\s*[:：]\s*@?([^|｜\n]+?)\s*[|｜]\s*(.+)$/gim;
  let match: RegExpExecArray | null;
  const raw = String(text || "");
  while ((match = re.exec(raw))) {
    const targetName = String(match[1] || "").trim();
    const question = String(match[2] || "").trim();
    if (targetName && question) asks.push({ targetName, question });
  }
  return asks.slice(0, 3);
}

/**
 * Convert private routing protocol into one ordinary, durable group message.
 * The protocol itself stays out of chat, but a real ask or work claim must be
 * visible to people and to later group members just like a normal group-chat
 * exchange.
 */
export function formatGroupDiscussionMessage(
  body: string,
  asks: Array<{ targetName: string; question: string }>,
  tasks: string[],
): string {
  const visible = String(body || "").trim();
  const additions: string[] = [];
  for (const ask of asks) {
    const plainAsk = `@${ask.targetName}，想请你补充：${ask.question}`;
    if (!visible.includes(ask.targetName) || !visible.includes(ask.question)) additions.push(plainAsk);
  }
  for (const task of tasks.map((item) => String(item || "").trim()).filter(Boolean)) {
    if (!visible.includes(task)) additions.push(`我认领：${task}`);
  }
  return [visible, ...additions].filter(Boolean).join("\n\n").trim();
}

/** Return a workspace-relative file only for successful, explicit file-writing tools. */
export function sharedFileFromToolEvent(event: any): string | null {
  if (event?.type !== "tool_end" || event?.data?.ok === false || event?.data?.error) return null;
  const name = String(event?.data?.name || event?.data?.tool || "").toLowerCase();
  if (!new Set(["write_file", "str_replace"]).has(name)) return null;
  const args = event?.data?.args;
  if (!args || typeof args !== "object") return null;
  const path = String(args.path || args.file_path || args.file || "").trim();
  return path || null;
}


export function pickExecSlots<
  T extends {
    agent: { id: string; name?: string; title?: string; duty?: string; blurb?: string; persona?: string };
  },
>(
  slots: T[],
  opts: {
    userText: string;
    mentionedIds: Set<string>;
    nominatedIds?: Set<string>;
    boardOwnerIds: string[];
  },
): T[] {
  type Claim = { slot: T; task: string; key: string };
  const claims: Claim[] = [];
  for (const s of slots) {
    const tasks = (((s as any)._tasks as string[]) || [])
      .map((x) => String(x || "").trim())
      .filter(Boolean);
    for (const task of tasks) {
      claims.push({ slot: s, task, key: normalizeTaskKey(task) || `id:${s.agent.id}` });
    }
  }
  for (const oid of opts.boardOwnerIds) {
    const s = slots.find((x) => x.agent.id === oid);
    if (!s) continue;
    if (claims.some((c) => c.slot.agent.id === oid)) continue;
    claims.push({
      slot: s,
      task: "看板待办",
      key: `board:${oid}`,
    });
  }

  if (claims.length > 0) {
    const byKey = new Map<string, Claim[]>();
    for (const c of claims) {
      const list = byKey.get(c.key) || [];
      list.push(c);
      byKey.set(c.key, list);
    }
    const winners = new Map<string, T>();
    const hay = opts.userText;
    for (const group of byKey.values()) {
      let best = group[0];
      let bestScore = -1;
      for (const c of group) {
        let score = dutyScore(c.slot.agent, `${hay} ${c.task}`);
        if (opts.nominatedIds?.has(c.slot.agent.id)) score += 8;
        if (opts.mentionedIds.has(c.slot.agent.id)) score += 5;
        if (score > bestScore) {
          bestScore = score;
          best = c;
        }
      }
      winners.set(best.slot.agent.id, best.slot);
      const winTasks = group
        .filter((c) => c.slot.agent.id === best.slot.agent.id)
        .map((c) => c.task);
      (best.slot as any)._tasks = winTasks.length ? winTasks : [best.task];
    }
    for (const s of slots) {
      if (winners.has(s.agent.id)) continue;
      (s as any)._tasks = [];
    }
    return Array.from(winners.values());
  }

  // For an explicit user work order, select one stable, best-fit owner if the
  // planner omitted TASK:.  Keep the user's own wording as the assignment;
  // never manufacture a generic "execute the request" pseudo-task.
  const named = opts.nominatedIds?.size
    ? slots.filter((s) => opts.nominatedIds!.has(s.agent.id))
    : opts.mentionedIds.size
      ? slots.filter((s) => opts.mentionedIds.has(s.agent.id))
      : slots;
  const candidates = named.length ? named : slots;
  let best = candidates[0];
  let bestScore = dutyScore(best.agent, opts.userText);
  for (const candidate of candidates.slice(1)) {
    const score = dutyScore(candidate.agent, opts.userText);
    if (score > bestScore) {
      best = candidate;
      bestScore = score;
    }
  }
  (best as any)._tasks = [summarizeReply(opts.userText, 160)];
  return [best];
}


