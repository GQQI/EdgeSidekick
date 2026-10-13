/**
 * 会话版群聊的协作编排器（Orchestrator）。
 *
 * 旧做法是「每条消息都让每个成员各自判断要不要说话」：N 个并发的判断调用、
 * 默认沉默、彼此看不到分工，结果常常没人回答，或者几个人重复同一件事。
 *
 * 这里改为业界验证过的「编排者 + 成员」结构：
 *  1. 规划（一次调用，用主模型）：选协作模式、负责人、每一步的任务与交付标准。
 *     - chat     寒暄：1-3 人各回一句
 *     - answer   一个人就能做好的事：只交给最合适的人（等同单智能体，不拖后腿）
 *     - parallel 可拆分的独立子任务 / 多视角评估：并行，最后汇总
 *     - pipeline 有依赖的工作：接力，下一步拿到上一步的交付
 *     - discuss  征求意见 / 方案比较：依次发言，每人只补充新角度，最后汇总
 *  2. 执行：成员拿到明确的任务说明、同伴分工、上游交付；成员之间可以 @ 交接。
 *  3. 复核：重要产出交给另一位成员审查，有问题就由原负责人修订一次。
 *  4. 汇总：多人贡献时由负责人整合成一份最终答复。
 *  5. 兜底：空回复会重试，调用失败会转交给其他成员，绝不静默结束。
 */
import { bestFitAgent, resolveMember, type ChatAgent, type ChatLine } from "./groupLoop";
import { mentionsEveryone } from "./store";

export type CollabMode = "chat" | "answer" | "parallel" | "pipeline" | "discuss";

export type StepRole =
  | "greeter"
  | "lead"
  | "worker"
  | "discussant"
  | "handoff"
  | "reviewer"
  | "reviser"
  | "synthesizer";

export type CollabStep = {
  id: string;
  agentId: string;
  task: string;
  expected?: string;
  dependsOn: string[];
  tools: boolean;
};

export type CollabPlan = {
  mode: CollabMode;
  leadId: string;
  goal: string;
  steps: CollabStep[];
  review: { agentId: string; focus: string } | null;
  synthesize: boolean;
  reason: string;
  source: "model" | "fallback";
  fallbackReason?: "no_planner" | "empty" | "unparseable" | "error";
};

export type Contribution = { agentId: string; name: string; task: string; text: string };

export type MemberTask = {
  agent: ChatAgent;
  role: StepRole;
  plan: CollabPlan;
  step?: CollabStep;
  /** Conversation so far (oldest first), including this turn's replies. */
  history: ChatLine[];
  /** The message this member is responding to. */
  trigger: ChatLine;
  task: string;
  expected?: string;
  /** Who else works on this turn, so the member does not duplicate them. */
  peers: Array<{ name: string; task: string }>;
  /** Deliverables this step builds on. */
  upstream: Contribution[];
  /** Material to review or to integrate. */
  contributions: Contribution[];
  /** Reviewer findings a reviser must address. */
  issues?: string;
  tools: boolean;
  /** false = the member may answer PASS when it has nothing new. */
  mustAnswer: boolean;
  brief: boolean;
  /** 0 on the first try, 1 when the previous try returned nothing. */
  attempt: number;
};

export type MemberResult = { text: string; error?: string };

export type CollabEndReason = "done" | "paused" | "cap" | "aborted" | "failed";

export type CollabEvent =
  | { type: "planning" }
  | { type: "plan"; plan: CollabPlan }
  | { type: "step_start"; agentId: string; role: StepRole; stepId?: string; task: string }
  | { type: "step_done"; agentId: string; role: StepRole; stepId?: string; line: ChatLine | null }
  | { type: "step_failed"; agentId: string; role: StepRole; stepId?: string; error: string; reassignedTo?: string }
  | { type: "review"; agentId: string; approved: boolean; issues: string }
  | { type: "handoff"; fromId: string; toId: string }
  | { type: "end"; reason: CollabEndReason; messages: number };

export type CollabOptions = {
  roster: ChatAgent[];
  /** Conversation including the new user message as the LAST line. */
  history: ChatLine[];
  /** One JSON-only call to the configured main model. null = unavailable. */
  plan?: (prompt: string, signal?: AbortSignal) => Promise<string | null>;
  runMember: (task: MemberTask, signal?: AbortSignal) => Promise<MemberResult>;
  signal?: AbortSignal;
  /** Parallel member runs at once. */
  concurrency?: number;
  /** Hard cap on agent messages per user turn. */
  maxMessages?: number;
  /** Shared-board digest (facts / open tasks) for the planner. */
  boardDigest?: string;
  /** See buildPlannerPrompt. */
  capabilities?: string;
  onEvent?: (event: CollabEvent) => void;
  /** Return true to pause the turn after a message (e.g. a member asked the user). */
  shouldStop?: (line: ChatLine) => boolean;
  newId?: () => string;
  /** Pre-computed plan (tests). */
  presetPlan?: CollabPlan;
};

export type CollabResult = {
  plan: CollabPlan;
  messages: ChatLine[];
  reason: CollabEndReason;
  events: CollabEvent[];
};

const MODES: CollabMode[] = ["chat", "answer", "parallel", "pipeline", "discuss"];
const MAX_STEPS = 5;
const MAX_HANDOFFS = 3;

let seq = 0;
function defaultId(): string {
  seq += 1;
  return `cl_${Date.now().toString(36)}_${seq}`;
}

function speaker(line: ChatLine): string {
  return line.role === "user" ? "用户" : line.agentName || line.agentId || "成员";
}

function oneLine(text: string, max: number): string {
  const flat = String(text || "").replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/** Members written as `@名字` in a message (explicit address only, never bare names). */
export function atMentions(text: string, roster: ChatAgent[]): ChatAgent[] {
  const out: ChatAgent[] = [];
  const raw = String(text || "");
  const sorted = [...roster].filter((a) => a.name).sort((a, b) => b.name.length - a.name.length);
  for (const m of raw.matchAll(/@([^\s@，,。！？!?：:；;、（）()【】]+)/g)) {
    const token = m[1];
    const hit =
      resolveMember(token, roster) || sorted.find((a) => token.startsWith(a.name));
    if (hit && !out.some((a) => a.id === hit.id)) out.push(hit);
  }
  return out;
}

const PASS_RE = /^(?:pass|silent|no_reply|跳过|沉默|无补充|没有补充|无需补充|不需要补充)[。.!！]?$/i;

export function isPass(text: string): boolean {
  return PASS_RE.test(String(text || "").replace(/\s+/g, " ").trim());
}

const APPROVE_RE = /^\s*(?:\*\*)?\s*(?:APPROVE[D]?|LGTM|通过|复核通过|没有问题|无问题)/i;
const CHANGES_RE = /^\s*(?:\*\*)?\s*(?:CHANGES|REQUEST[_ ]CHANGES|需要修改|不通过|有问题)/i;

/** Reviewer verdict. The first line decides; anything without a clear approval is treated as findings. */
export function parseReview(text: string): { approved: boolean; issues: string } {
  const raw = String(text || "").trim();
  if (!raw) return { approved: true, issues: "" };
  const body = raw
    .replace(/^\s*(?:\*\*)?\s*(?:APPROVE[D]?|LGTM|CHANGES|REQUEST[_ ]CHANGES|复核通过|通过|需要修改|不通过)\s*(?:\*\*)?\s*[:：]?\s*/i, "")
    .trim();
  if (CHANGES_RE.test(raw)) return { approved: false, issues: body || raw };
  if (APPROVE_RE.test(raw)) return { approved: true, issues: body };
  return { approved: false, issues: raw };
}

const GREETING_RE =
  /^(?:@\S+\s*)*(?:你们好|大家好|你好|您好|嗨|哈喽|hi|hello|hey|早上好|上午好|中午好|下午好|晚上好|晚安|早安|谢谢|多谢|感谢|辛苦了|在吗|再见|拜拜)[\s!！。.,，~～]*$/i;

export function looksLikeGreeting(text: string): boolean {
  return GREETING_RE.test(String(text || "").trim());
}

/* ------------------------------------------------------------------------ */
/* Planning                                                                  */
/* ------------------------------------------------------------------------ */

export function buildPlannerPrompt(input: {
  roster: ChatAgent[];
  history: ChatLine[];
  userText: string;
  mentioned: ChatAgent[];
  everyone: boolean;
  boardDigest?: string;
  /** Connected external apps (plugins) any member can use in a tools=true step. */
  capabilities?: string;
}): string {
  const members = input.roster
    .map(
      (a) =>
        `- ${a.name}${a.title ? `（${a.title}）` : ""}：${oneLine(a.duty || a.blurb || "", 180) || "未写职责"}`,
    )
    .join("\n");
  const recent = input.history
    .slice(-11, -1)
    .map((line) => `【${speaker(line)}】${oneLine(line.text, 260)}`)
    .join("\n");
  const constraint = input.everyone
    ? "用户 @ 了全体成员：每位成员都要参与（chat 或 discuss 时安排全部成员）。"
    : input.mentioned.length
      ? `用户点名了：${input.mentioned.map((a) => a.name).join("、")}。只安排这些成员，除非用户在消息里明确要求其他人参与。`
      : "";
  return [
    "你是群聊的协作编排者，不是群成员，不直接回答用户。你要决定这条用户消息由谁、以什么方式处理，让团队的结果比任何一个成员单干更好，同时不浪费成员的精力。",
    `群成员（名字（头衔）：职责）：\n${members}`,
    input.capabilities?.trim()
      ? `已连接的外部应用（任何成员在 tools 为 true 的步骤里都能调用；需要这些数据或操作的步骤必须填 tools: true）：\n${input.capabilities.trim()}`
      : "",
    input.boardDigest?.trim() ? `群共享记录：\n${input.boardDigest.trim()}` : "",
    recent ? `最近的对话（从旧到新）：\n${recent}` : "",
    `用户最新消息：\n「${String(input.userText || "").trim().slice(0, 2000)}」`,
    constraint,
    [
      "协作模式（mode）只能选一个：",
      "- chat：寒暄、问候、致谢、闲聊。安排 1-3 位成员各回一句；用户问候「大家」时可以安排全部成员。",
      "- answer：一位成员就能做好的问题或任务（大多数情况都是这种）。只安排最合适的一位，不要为了热闹拉更多人。",
      "- parallel：能拆成互不依赖的子任务，或需要不同专业视角分别给出结果。安排 2-4 位成员同时做，最后由 lead 汇总。",
      "- pipeline：有先后依赖的工作（例如先设计、再实现、再测试）。每一步交给职责最匹配的成员，后一步能看到前一步的交付。",
      "- discuss：征求意见、比较方案、头脑风暴、做决策。安排 2-4 位视角不同的成员依次发言，最后由 lead 汇总共识和分歧。",
    ].join("\n"),
    [
      "要求：",
      "- 每一步的 task 写清楚目标，expected 写清楚交付物（输出格式或验收标准）。各步骤之间不能重叠，同一件事不要交给两个人。",
      "- tools：这一步需要动手（读写文件、运行命令、浏览网页、查邮件/仓库等插件工具）就填 true；只讨论或凭知识回答填 false。",
      "- review：会改文件或代码、对外发送内容、给出重要结论时，如果有职责适合审查或测试的成员（且不是做这件事的人），安排他复核；寒暄和简单问答填 null。",
      "- synthesize：parallel 和 discuss 有两位以上成员时填 true；pipeline 的最后一步本身就是交付时填 false。",
      "- 成员名必须是上面列表里的名字。",
    ].join("\n"),
    "只输出一个 JSON 对象，不要任何其他文字：",
    '{"mode":"answer","lead":"成员名","goal":"一句话复述用户目标","steps":[{"id":"s1","member":"成员名","task":"这一步做什么","expected":"交付什么","depends_on":[],"tools":false}],"review":null,"synthesize":false,"reason":"一句话说明为什么这样安排"}',
  ]
    .filter(Boolean)
    .join("\n\n");
}

function extractJson(raw: string): Record<string, unknown> | null {
  const text = String(raw || "")
    .replace(/<think>[\s\S]*?<\/think>/gi, "")
    .replace(/^[\s\S]*<\/think>/i, "")
    .replace(/```(?:json)?/gi, "");
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start < 0 || end <= start) return null;
  try {
    const value = JSON.parse(text.slice(start, end + 1));
    return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

function asBool(value: unknown, fallback: boolean): boolean {
  if (typeof value === "boolean") return value;
  const s = String(value ?? "").trim().toLowerCase();
  if (s === "true" || s === "yes" || s === "1") return true;
  if (s === "false" || s === "no" || s === "0") return false;
  return fallback;
}

type PlanContext = {
  roster: ChatAgent[];
  userText: string;
  mentioned: ChatAgent[];
  everyone: boolean;
};

/** Validate and repair a plan so the engine can always run it. */
export function normalizePlan(draft: Partial<CollabPlan> & { steps?: CollabStep[] }, ctx: PlanContext): CollabPlan {
  const { roster, userText } = ctx;
  const known = new Set(roster.map((a) => a.id));
  let mode: CollabMode = MODES.includes(draft.mode as CollabMode) ? (draft.mode as CollabMode) : "answer";
  let steps = (draft.steps || []).filter((s) => known.has(s.agentId));

  const allowed = ctx.everyone || !ctx.mentioned.length ? null : new Set(ctx.mentioned.map((a) => a.id));
  if (allowed) steps = steps.filter((s) => allowed.has(s.agentId));

  if (!steps.length) {
    const people = allowed
      ? ctx.mentioned
      : ctx.everyone
        ? roster
        : [roster.find((a) => a.id === draft.leadId) || bestFitAgent(roster, userText)].filter(Boolean) as ChatAgent[];
    steps = people.map((agent, i) => ({
      id: `s${i + 1}`,
      agentId: agent.id,
      task: mode === "chat" ? "简短回应用户" : userText,
      dependsOn: [],
      tools: mode !== "chat" && mode !== "discuss",
    }));
    if (people.length > 1 && (mode === "answer" || mode === "pipeline")) mode = "discuss";
  }

  // Someone the user @-named must get a turn.
  for (const agent of allowed ? ctx.mentioned : []) {
    if (!steps.some((s) => s.agentId === agent.id)) {
      steps.push({ id: `s${steps.length + 1}`, agentId: agent.id, task: userText, dependsOn: [], tools: true });
    }
  }
  if (ctx.everyone && (mode === "chat" || mode === "discuss")) {
    for (const agent of roster) {
      if (!steps.some((s) => s.agentId === agent.id)) {
        steps.push({
          id: `s${steps.length + 1}`,
          agentId: agent.id,
          task: mode === "chat" ? "简短回应用户" : "从你的职责角度发表看法",
          dependsOn: [],
          tools: false,
        });
      }
    }
  }

  const cap = ctx.everyone ? Math.max(MAX_STEPS, roster.length) : MAX_STEPS;
  steps = steps.slice(0, cap);
  if (mode === "answer" && steps.length > 1 && !allowed) steps = steps.slice(0, 1);
  if (mode === "answer" && steps.length > 1) mode = "discuss";
  if ((mode === "parallel" || mode === "discuss" || mode === "pipeline") && steps.length === 1) mode = "answer";

  const ids = new Set<string>();
  steps = steps.map((s, i) => {
    let id = String(s.id || `s${i + 1}`);
    if (ids.has(id)) id = `s${i + 1}_${i}`;
    ids.add(id);
    return { ...s, id, task: String(s.task || "").trim() || userText };
  });
  steps = steps.map((s, i) => ({
    ...s,
    dependsOn: mode === "pipeline"
      ? (s.dependsOn || []).filter((d) => ids.has(d) && d !== s.id && steps.findIndex((x) => x.id === d) < i)
      : [],
  }));
  if (mode === "chat") steps = steps.map((s) => ({ ...s, tools: false }));

  const leadId =
    (draft.leadId && steps.some((s) => s.agentId === draft.leadId) ? draft.leadId : undefined) ||
    (draft.leadId && known.has(draft.leadId) && !allowed ? draft.leadId : undefined) ||
    steps[0].agentId;

  const workers = new Set(steps.map((s) => s.agentId));
  let review = draft.review && known.has(draft.review.agentId) ? draft.review : null;
  if (review && (mode === "chat" || mode === "discuss")) review = null;
  if (review && workers.size === 1 && workers.has(review.agentId)) review = null;
  if (review && allowed && !allowed.has(review.agentId)) review = null;

  const synthesize =
    (mode === "parallel" || mode === "discuss") && steps.length >= 2
      ? draft.synthesize !== false
      : mode === "pipeline"
        ? draft.synthesize === true
        : false;

  return {
    mode,
    leadId,
    goal: String(draft.goal || "").trim() || oneLine(userText, 80),
    steps,
    review,
    synthesize,
    reason: String(draft.reason || "").trim(),
    source: draft.source || "fallback",
    fallbackReason: draft.fallbackReason,
  };
}

export function parsePlan(raw: string, ctx: PlanContext): CollabPlan | null {
  const obj = extractJson(raw);
  if (!obj) return null;
  const steps: CollabStep[] = [];
  const rows = Array.isArray(obj.steps) ? (obj.steps as Array<Record<string, unknown>>) : [];
  rows.forEach((row, i) => {
    const agent = resolveMember(row?.member ?? row?.agent ?? row?.name, ctx.roster);
    if (!agent) return;
    const deps = Array.isArray(row?.depends_on) ? row.depends_on : Array.isArray(row?.dependsOn) ? row.dependsOn : [];
    steps.push({
      id: String(row?.id || `s${i + 1}`),
      agentId: agent.id,
      task: String(row?.task || "").trim(),
      expected: String(row?.expected || "").trim() || undefined,
      dependsOn: (deps as unknown[]).map((d) => (typeof d === "number" ? `s${d}` : String(d))),
      tools: asBool(row?.tools, true),
    });
  });
  const reviewRaw = obj.review as Record<string, unknown> | null | undefined;
  const reviewer = reviewRaw && typeof reviewRaw === "object" ? resolveMember(reviewRaw.member, ctx.roster) : undefined;
  return normalizePlan(
    {
      mode: String(obj.mode || "").toLowerCase() as CollabMode,
      leadId: resolveMember(obj.lead, ctx.roster)?.id,
      goal: String(obj.goal || ""),
      steps,
      review: reviewer ? { agentId: reviewer.id, focus: String(reviewRaw?.focus || "").trim() } : null,
      synthesize: obj.synthesize === undefined ? undefined : asBool(obj.synthesize, false),
      reason: String(obj.reason || ""),
      source: "model",
    },
    ctx,
  );
}

/** Deterministic plan when the planner call is unavailable: never silent, never a guessed pipeline. */
export function fallbackPlan(ctx: PlanContext, why: CollabPlan["fallbackReason"]): CollabPlan {
  const greeting = looksLikeGreeting(ctx.userText);
  const mode: CollabMode = greeting ? "chat" : ctx.mentioned.length > 1 || ctx.everyone ? "discuss" : "answer";
  return normalizePlan({ mode, steps: [], source: "fallback", fallbackReason: why }, ctx);
}

export async function planTurn(
  opts: Pick<CollabOptions, "roster" | "history" | "plan" | "signal" | "boardDigest" | "capabilities">,
): Promise<CollabPlan> {
  const userLine = opts.history[opts.history.length - 1];
  const userText = userLine?.text || "";
  const ctx: PlanContext = {
    roster: opts.roster,
    userText,
    mentioned: atMentions(userText, opts.roster),
    everyone: mentionsEveryone(userText),
  };
  if (!opts.plan) return fallbackPlan(ctx, "no_planner");
  const base = buildPlannerPrompt({
    ...ctx,
    history: opts.history,
    boardDigest: opts.boardDigest,
    capabilities: opts.capabilities,
  });
  let why: CollabPlan["fallbackReason"] = "empty";
  for (let attempt = 0; attempt < 2; attempt += 1) {
    if (opts.signal?.aborted) break;
    let raw: string | null = null;
    try {
      raw = await opts.plan(
        attempt === 0 ? base : `${base}\n\n上一次的输出不是合法的 JSON。这次只输出 JSON 对象本身。`,
        opts.signal,
      );
    } catch {
      why = "error";
      break;
    }
    if (!raw || !raw.trim()) {
      why = "empty";
      break;
    }
    const plan = parsePlan(raw, ctx);
    if (plan) return plan;
    why = "unparseable";
  }
  return fallbackPlan(ctx, why);
}

/* ------------------------------------------------------------------------ */
/* Execution                                                                 */
/* ------------------------------------------------------------------------ */

export function modeLabel(mode: CollabMode): string {
  return { chat: "闲聊", answer: "单人负责", parallel: "并行分工", pipeline: "接力流水线", discuss: "多视角讨论" }[mode];
}

/** One-line plan summary for the transcript notice. */
export function formatPlanNotice(plan: CollabPlan, roster: ChatAgent[]): string {
  const name = (id: string) => roster.find((a) => a.id === id)?.name || id;
  const sep = plan.mode === "pipeline" ? " → " : "、";
  const steps = plan.steps
    .map((s) => (plan.mode === "chat" || plan.mode === "discuss" ? name(s.agentId) : `${name(s.agentId)}：${oneLine(s.task, 40)}`))
    .join(sep);
  const tail = [
    plan.review ? `${name(plan.review.agentId)} 复核` : "",
    plan.synthesize ? `${name(plan.leadId)} 汇总` : "",
  ].filter(Boolean);
  return `协作 · ${modeLabel(plan.mode)}｜${steps}${tail.length ? `｜${tail.join("，")}` : ""}`;
}

async function pool<T>(items: T[], limit: number, run: (item: T) => Promise<void>, canStart: (item: T) => boolean) {
  const queue = [...items];
  const active = new Set<Promise<void>>();
  while (queue.length || active.size) {
    const idx = queue.findIndex((item) => canStart(item));
    if (idx >= 0 && active.size < limit) {
      const [item] = queue.splice(idx, 1);
      const p: Promise<void> = run(item).finally(() => active.delete(p));
      active.add(p);
      continue;
    }
    if (!active.size) break;
    await Promise.race(active);
  }
}

export async function runCollaboration(opts: CollabOptions): Promise<CollabResult> {
  const roster = opts.roster.filter((a) => a && a.id);
  const history = [...opts.history];
  const produced: ChatLine[] = [];
  const events: CollabEvent[] = [];
  const newId = opts.newId || defaultId;
  const maxMessages = Math.max(1, opts.maxMessages ?? 12);
  const concurrency = Math.max(1, opts.concurrency ?? 2);
  const aborted = () => Boolean(opts.signal?.aborted);
  const byId = (id: string) => roster.find((a) => a.id === id);
  const emit = (event: CollabEvent) => {
    events.push(event);
    try {
      opts.onEvent?.(event);
    } catch {
      /* UI callbacks never break the turn */
    }
  };
  let paused = false;
  let failedHard = false;
  const userLine = history[history.length - 1];

  const emptyPlan = (): CollabPlan => ({
    mode: "answer", leadId: roster[0]?.id || "", goal: "", steps: [], review: null,
    synthesize: false, reason: "", source: "fallback",
  });
  if (!userLine || !roster.length) {
    emit({ type: "end", reason: "done", messages: 0 });
    return { plan: emptyPlan(), messages: [], reason: "done", events };
  }

  emit({ type: "planning" });
  const plan = opts.presetPlan || (await planTurn(opts));
  if (aborted()) {
    emit({ type: "end", reason: "aborted", messages: 0 });
    return { plan, messages: [], reason: "aborted", events };
  }
  emit({ type: "plan", plan });

  const finish = (reason: CollabEndReason): CollabResult => {
    emit({ type: "end", reason, messages: produced.length });
    return { plan, messages: produced, reason, events };
  };
  const budgetLeft = () => produced.length < maxMessages;
  const stopNow = () => aborted() || paused || !budgetLeft();

  const contributions = new Map<string, Contribution>();
  const failedAgents = new Set<string>();
  const handoffsBy = new Map<string, number>();
  let handoffs = 0;
  const peersFor = (step: CollabStep) =>
    plan.steps
      .filter((s) => s.id !== step.id)
      .map((s) => ({ name: byId(s.agentId)?.name || s.agentId, task: s.task }));

  const commit = (agent: ChatAgent, text: string): ChatLine => {
    const line: ChatLine = { id: newId(), role: "agent", agentId: agent.id, agentName: agent.name, text };
    history.push(line);
    produced.push(line);
    if (opts.shouldStop?.(line)) paused = true;
    return line;
  };

  type RunSpec = Omit<MemberTask, "agent" | "history" | "attempt" | "plan">;

  /** Run one member with retry on empty output and one reassignment on failure. */
  const execute = async (agent: ChatAgent, spec: RunSpec, allowReassign: boolean): Promise<ChatLine | null> => {
    let lastError = "";
    for (let attempt = 0; attempt < 2; attempt += 1) {
      if (stopNow()) return null;
      emit({ type: "step_start", agentId: agent.id, role: spec.role, stepId: spec.step?.id, task: spec.task });
      let result: MemberResult;
      try {
        result = await opts.runMember({ ...spec, agent, plan, history: [...history], attempt }, opts.signal);
      } catch (err) {
        result = { text: "", error: String((err as Error)?.message || err || "error") };
      }
      if (aborted()) return null;
      const text = String(result.text || "").trim();
      if (text && !isPass(text)) {
        const line = commit(agent, text);
        emit({ type: "step_done", agentId: agent.id, role: spec.role, stepId: spec.step?.id, line });
        return line;
      }
      if (result.error) {
        lastError = result.error;
        continue;
      }
      if (!spec.mustAnswer) {
        emit({ type: "step_done", agentId: agent.id, role: spec.role, stepId: spec.step?.id, line: null });
        return null;
      }
      lastError = "没有给出回复";
    }
    failedAgents.add(agent.id);
    const backup = allowReassign
      ? bestFitAgent(
          roster.filter((a) => a.id !== agent.id && !failedAgents.has(a.id)),
          `${spec.task}\n${userLine.text}`,
        )
      : undefined;
    emit({
      type: "step_failed",
      agentId: agent.id,
      role: spec.role,
      stepId: spec.step?.id,
      error: lastError,
      reassignedTo: backup?.id,
    });
    if (backup && !stopNow()) return execute(backup, spec, false);
    return null;
  };

  const runStep = async (step: CollabStep, role: StepRole, extra: Partial<RunSpec> = {}) => {
    const agent = byId(step.agentId);
    if (!agent) return null;
    const upstream = step.dependsOn.map((id) => contributions.get(id)).filter(Boolean) as Contribution[];
    const line = await execute(
      agent,
      {
        role,
        step,
        trigger: userLine,
        task: step.task,
        expected: step.expected,
        peers: peersFor(step),
        upstream,
        contributions: [],
        tools: step.tools,
        mustAnswer: true,
        brief: plan.mode === "chat",
        ...extra,
      },
      role !== "greeter" && role !== "discussant",
    );
    if (line) {
      contributions.set(step.id, {
        agentId: line.agentId || agent.id,
        name: line.agentName || agent.name,
        task: step.task,
        text: line.text,
      });
    }
    return line;
  };

  /** A member wrote `@同事` with a request: let that colleague answer it once. */
  const followHandoffs = async (line: ChatLine | null, pending: Set<string>) => {
    if (!line || stopNow()) return;
    for (const target of atMentions(line.text, roster)) {
      if (stopNow() || handoffs >= MAX_HANDOFFS) return;
      if (target.id === line.agentId || pending.has(target.id) || (handoffsBy.get(target.id) || 0) >= 1) continue;
      handoffs += 1;
      handoffsBy.set(target.id, (handoffsBy.get(target.id) || 0) + 1);
      emit({ type: "handoff", fromId: String(line.agentId || ""), toId: target.id });
      const reply = await execute(
        target,
        {
          role: "handoff",
          trigger: line,
          task: `回应「${line.agentName || "同事"}」在上一条消息里对你提出的请求或问题`,
          peers: [],
          upstream: [],
          contributions: [],
          tools: plan.steps.some((s) => s.tools),
          mustAnswer: true,
          brief: false,
        },
        false,
      );
      await followHandoffs(reply, pending);
    }
  };

  const remainingAgents = (from: number) => new Set(plan.steps.slice(from).map((s) => s.agentId));

  // 1) Main work.
  if (plan.mode === "parallel") {
    const busy = new Set<string>();
    await pool(
      plan.steps,
      concurrency,
      async (step) => {
        busy.add(step.agentId);
        try {
          await runStep(step, "worker");
        } finally {
          busy.delete(step.agentId);
        }
      },
      (step) => !busy.has(step.agentId) && !stopNow(),
    );
    for (const c of [...contributions.values()]) {
      await followHandoffs(produced.find((l) => l.text === c.text) || null, new Set([plan.leadId]));
    }
  } else if (plan.mode === "pipeline") {
    for (let i = 0; i < plan.steps.length; i += 1) {
      if (stopNow()) break;
      const step = plan.steps[i];
      const deps = step.dependsOn.length ? step.dependsOn : i > 0 ? [plan.steps[i - 1].id] : [];
      const missing = deps.filter((d) => !contributions.has(d));
      if (missing.length) {
        failedHard = true;
        break;
      }
      const line = await runStep({ ...step, dependsOn: deps }, "worker");
      await followHandoffs(line, remainingAgents(i + 1));
    }
  } else {
    const role: StepRole = plan.mode === "chat" ? "greeter" : plan.mode === "discuss" ? "discussant" : "lead";
    for (let i = 0; i < plan.steps.length; i += 1) {
      if (stopNow()) break;
      const step = plan.steps[i];
      // Discussion: the first voice must answer; later voices may PASS when they have nothing new.
      // A member the user @-named always answers.
      const named = atMentions(userLine.text, roster).some((a) => a.id === step.agentId);
      const mustAnswer = role !== "discussant" || i === 0 || named;
      const line = await runStep(step, role, { mustAnswer });
      if (role !== "greeter") await followHandoffs(line, remainingAgents(i + 1));
    }
  }

  if (aborted()) return finish("aborted");
  if (paused) return finish("paused");

  // 2) Review → at most one revision.
  if (plan.review && contributions.size && budgetLeft()) {
    const reviewer = byId(plan.review.agentId);
    const deliverables = [...contributions.values()];
    if (reviewer && !failedAgents.has(reviewer.id)) {
      const line = await execute(
        reviewer,
        {
          role: "reviewer",
          trigger: history[history.length - 1],
          task: plan.review.focus || "复核上面的交付是否正确、完整，有没有遗漏或风险",
          peers: [],
          upstream: [],
          contributions: deliverables,
          tools: plan.steps.some((s) => s.tools),
          mustAnswer: true,
          brief: false,
        },
        false,
      );
      if (line) {
        const verdict = parseReview(line.text);
        emit({ type: "review", agentId: reviewer.id, approved: verdict.approved, issues: verdict.issues });
        if (!verdict.approved && verdict.issues && !stopNow()) {
          const last = deliverables[deliverables.length - 1];
          const owner = byId(plan.mode === "pipeline" ? last.agentId : plan.leadId) || byId(last.agentId);
          if (owner && owner.id !== reviewer.id) {
            const revised = await execute(
              owner,
              {
                role: "reviser",
                trigger: line,
                task: "根据复核意见修订你们的交付",
                peers: [],
                upstream: [],
                contributions: deliverables,
                issues: verdict.issues,
                tools: plan.steps.some((s) => s.tools),
                mustAnswer: true,
                brief: false,
              },
              false,
            );
            if (revised) {
              contributions.set("revision", { agentId: owner.id, name: owner.name, task: "修订", text: revised.text });
            }
          }
        }
      }
    }
  }

  if (aborted()) return finish("aborted");
  if (paused) return finish("paused");

  // 3) Synthesis.
  const material = [...contributions.values()];
  const distinctVoices = new Set(material.map((c) => c.agentId)).size;
  if (plan.synthesize && distinctVoices >= 2 && budgetLeft()) {
    const lead = byId(plan.leadId) || byId(material[0].agentId);
    if (lead) {
      await execute(
        failedAgents.has(lead.id) ? byId(material[0].agentId) || lead : lead,
        {
          role: "synthesizer",
          trigger: history[history.length - 1],
          task: plan.goal || userLine.text,
          peers: [],
          upstream: [],
          contributions: material,
          tools: false,
          mustAnswer: true,
          brief: false,
        },
        false,
      );
    }
  }

  if (aborted()) return finish("aborted");
  if (paused) return finish("paused");
  if (!produced.length) return finish("failed");
  if (failedHard) return finish("failed");
  return finish(budgetLeft() ? "done" : "cap");
}
