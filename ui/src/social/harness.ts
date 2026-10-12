/**
 * 会话版协作 harness。
 *
 * 工作台的单智能体更稳，是因为一次只有一个执行者，并且做到目标才停。
 * 会话版把同一件事分给多人时，常见失败是规格不清、互相抢话、没有人验收
 * （MAST，2025）、错误被放大（Nature Machine Intelligence，2026），
 * 以及在同样的思考预算下通信本身会丢信息。
 *
 * 这里不固定一种编队。每个任务先按依赖形状选拓扑（AdaptOrch），
 * 跑完用轨迹打分；没交付就换成下一种（MANTA），交付两次就沿用（Turbo Harness）。
 * 搜完四种仍不稳定时，锁住历史上成功率最高的那种。
 */

export type Topology = "solo" | "parallel" | "sequential" | "hierarchical";

export type TaskShape = "single" | "parallel" | "pipeline";

export type TaskIntent = "code" | "research" | "write" | "review" | "browser" | "ops" | "general";

export type HarnessPhase = "work" | "talk";

export type HarnessRole = "owner" | "worker" | "verifier";

export type HarnessAgent = {
  id: string;
  name: string;
  title?: string;
  duty?: string;
  blurb?: string;
  persona?: string;
};

export type Assignment = {
  agentId: string;
  role: HarnessRole;
  task: string;
};

export type FailureCode =
  | "spec_no_owner"
  | "misalign_duplicate"
  | "misalign_chatter"
  | "misalign_ignored_ask"
  | "verify_unfinished"
  | "verify_no_evidence"
  | "verify_goal_blocked"
  | "coord_tool_conflict";

export type CollaborationPlan = {
  signature: string;
  phase: HarnessPhase;
  topology: Topology;
  shape: TaskShape;
  intent: TaskIntent;
  lang: "zh" | "en";
  assignments: Assignment[];
  acceptance: string;
  fallbackOwnerId: string;
  requireVerifier: boolean;
  adjusted: boolean;
};

export type HarnessTrial = {
  topology: Topology;
  success: boolean;
  at: number;
  failures: FailureCode[];
};

export type HarnessMemory = {
  signature: string;
  shape: TaskShape;
  trials: HarnessTrial[];
  best: Topology | null;
  stable: boolean;
  pending: Topology | null;
  failedOwnerIds: string[];
};

export type HarnessPlaybook = Record<string, HarnessMemory>;

export type AgentResult = {
  agentId: string;
  role: HarnessRole | "other";
  texts: string[];
  /** null = 这一轮没有收到目标判定。 */
  goalSatisfied: boolean | null;
  goalBlocked: boolean;
  toolErrors: number;
};

export type HarnessDiagnosis = {
  success: boolean;
  failures: FailureCode[];
  summary: string;
  innerRepair: { agentId: string; note: string } | null;
};

const TOPOLOGIES: Topology[] = ["solo", "parallel", "sequential", "hierarchical"];

const STOP_GRAM = new Set([
  "一个", "这个", "那个", "我们", "你们", "他们", "可以", "需要", "进行", "以及", "或者",
  "如果", "因为", "所以", "然后", "已经", "没有", "不是", "什么", "怎么", "如何", "一下",
  "帮我", "请你", "麻烦", "现在", "直接", "不要", "还是", "就是", "这个", "一下",
]);

const ANCHORS = [
  "修复", "实现", "修改", "测试", "审查", "检索", "调研", "撰写", "润色", "部署",
  "安装", "运行", "页面", "代码", "报错", "讨论", "翻译", "总结", "登录", "导出",
  "接口", "函数", "缺陷", "重构", "文档", "论文", "竞品",
];

const INTENT_HINT: Record<TaskIntent, string> = {
  code: "代码 实现 修改 调试 缺陷 测试 编程 文件",
  research: "检索 资料 研究 对比 查证 论文",
  write: "撰写 润色 成文 可读 文档",
  review: "审查 风险 漏洞 质量",
  browser: "网页 页面 浏览器",
  ops: "运行 部署 命令 终端 安装",
  general: "汇总 协调",
};

function prefersZh(text: string): boolean {
  const cn = (text.match(/[\u4e00-\u9fff]/g) || []).length;
  const en = (text.match(/[A-Za-z]/g) || []).length;
  return cn > 0 && cn >= en * 0.4;
}

function blobOf(agent: HarnessAgent): string {
  return `${agent.name || ""} ${agent.title || ""} ${agent.duty || ""} ${agent.blurb || ""} ${agent.persona || ""}`;
}

/** 中文没有空格，按词袋切开会把整句当成一个词，职责匹配就会全部打平。 */
function cjkOverlap(blob: string, text: string): number {
  const hay = blob.toLowerCase();
  const src = text.toLowerCase();
  let score = 0;
  const seen = new Set<string>();
  for (let n = 4; n >= 2; n -= 1) {
    for (let i = 0; i + n <= hay.length; i += 1) {
      const gram = hay.slice(i, i + n);
      if (!/^[\u4e00-\u9fff]+$/.test(gram) || STOP_GRAM.has(gram) || seen.has(gram)) continue;
      if (!src.includes(gram)) continue;
      seen.add(gram);
      score += n >= 3 ? 2 : 1;
      if (score >= 8) return score;
    }
  }
  for (const word of src.split(/[^\p{L}\p{N}]+/u)) {
    if (word.length >= 2 && hay.includes(word)) score += 1;
  }
  return score;
}

function titleBias(agent: HarnessAgent, intent: TaskIntent, text: string): number {
  const blob = blobOf(agent);
  let score = 0;
  if (intent === "code") {
    if (/程序员|工程|调试|测试|代码|实现/.test(blob)) score += 3;
    if (/报错|bug|异常|失败|修|缺陷/i.test(text) && /调试|缺陷|排错|报错/.test(blob)) score += 5;
    if (/跑测试|跑一下|单元测试/.test(text) && /测试|验测/.test(blob)) score += 5;
    if (/验证/.test(text) && !/写入|删除|创建|实现|修改/.test(text) && /测试|验测/.test(blob)) score += 5;
    if (/审查|评审|风险/.test(text) && /审查|风险/.test(blob)) score += 5;
    if (/实现|加一|功能|导出/.test(text) && /程序员/.test(blob)) score += 2;
  } else if (intent === "research" && /研究|检索|查/.test(blob)) score += 4;
  else if (intent === "write" && /写|文案|润色/.test(blob)) score += 4;
  else if (intent === "review" && /审/.test(blob)) score += 4;
  else if (intent === "browser" && /研究|检索|浏览/.test(blob)) score += 2;
  else if (intent === "ops" && /工程|程序|终端|命令/.test(blob)) score += 3;
  else if (intent === "general" && /编排|协调|汇总/.test(blob)) score += 2;
  if (intent !== "general" && /编排|协调成员/.test(blob)) score -= 1;
  return score;
}

function rankAgents(
  roster: HarnessAgent[],
  text: string,
  intent: TaskIntent,
  avoid: string[],
): Array<{ agent: HarnessAgent; score: number }> {
  const scoreOf = (agent: HarnessAgent, useAvoid: boolean) => {
    if (useAvoid && avoid.includes(agent.id)) return -100;
    return cjkOverlap(blobOf(agent), `${text} ${INTENT_HINT[intent]}`) + titleBias(agent, intent, text);
  };
  const sort = (useAvoid: boolean) =>
    roster
      .map((agent) => ({ agent, score: scoreOf(agent, useAvoid) }))
      .sort((a, b) => b.score - a.score || a.agent.name.localeCompare(b.agent.name, "zh"));
  const ranked = sort(true);
  if (avoid.length && ranked.every((row) => row.score < 0)) return sort(false);
  return ranked;
}

export function detectIntent(text: string): TaskIntent {
  if (/浏览器|打开网页|网页|滚动页面/.test(text)) return "browser";
  if (/审查|评审|看一下风险|挑错/.test(text) && !/实现|修复|修改/.test(text)) return "review";
  if (/代码|编程|函数|接口|bug|报错|修复|修改|实现|重构|测试|编译|仓库|缺陷/i.test(text)) return "code";
  if (/撰写|润色|写一|写个|写成|文案|翻译/.test(text)) return "write";
  if (/查一下|检索|调研|对比|搜一下|资料|论文|竞品/.test(text)) return "research";
  if (/部署|安装|发布|启动服务/.test(text)) return "ops";
  return "general";
}

function isMutating(text: string): boolean {
  return /删除|覆盖|写入|修改|创建|部署|安装|运行|提交|发布|修复|改一下|改动/.test(text);
}

export function isDiscussionRequest(text: string): boolean {
  return /讨论|头脑风暴|各自的看法|大家看看|一起想想|一起讨论/.test(text)
    && !/修改|修复|实现|部署|运行|代码|文件|测试|报错/.test(text);
}

/** A later clause that the user gave to someone else, or to test / review. */
function handoffKind(part: string): "test" | "review" | null {
  const text = part.replace(/^[，,\s]+|[，,\s]+$/g, "");
  if (/其他(?:的)?(?:智能体|成员|同事)|别的(?:智能体|成员)|其余(?:的)?(?:人|成员|智能体)/.test(text)) {
    if (/测试/.test(text) && !/审查|看看|优化/.test(text)) return "test";
    return "review";
  }
  if (/^(?:再)?(?:跑|做|补上|补)?测试|跑一遍测试|单元测试/.test(text)) return "test";
  if (/^(?:再)?(?:审查|评审|代码审查)/.test(text)) return "review";
  return null;
}

function splitParts(text: string, pattern: RegExp): string[] {
  return text
    .split(pattern)
    .map((part) => part.replace(/^[，,\s再把请帮我]+|[，,\s]+$/g, "").trim())
    .filter((part) => part.length >= 2);
}

export function detectShape(text: string): { shape: TaskShape; parts: string[] } {
  const parallel = splitParts(text, /并且|同时|分别|一边/);
  const pipeline = splitParts(text, /然后|接着|之后/);
  if (parallel.length >= 2 && !isMutating(text)) return { shape: "parallel", parts: parallel.slice(0, 4) };
  if (pipeline.length >= 2) return { shape: "pipeline", parts: pipeline.slice(0, 4) };
  if (parallel.length >= 2) return { shape: "pipeline", parts: parallel.slice(0, 4) };
  return { shape: "single", parts: [text.trim()] };
}

function skeleton(text: string): string {
  const hits = ANCHORS.filter((anchor) => text.includes(anchor));
  if (hits.length) return [...new Set(hits)].sort().slice(0, 4).join("+");
  const compact = text.replace(/@[\w\u4e00-\u9fff]+/g, "").replace(/[^\u4e00-\u9fffA-Za-z]/g, "");
  const grams: string[] = [];
  for (let i = 0; i + 2 <= compact.length && grams.length < 6; i += 2) {
    const gram = compact.slice(i, i + 2);
    if (!STOP_GRAM.has(gram)) grams.push(gram);
  }
  return [...new Set(grams)].sort().slice(0, 3).join("+") || "task";
}

export function taskSignature(text: string): string {
  const intent = detectIntent(text);
  const { shape } = detectShape(text);
  return `${intent}:${shape}:${skeleton(text)}`;
}

function acceptanceFor(intent: TaskIntent, lang: "zh" | "en"): string {
  const zh: Record<TaskIntent, string> = {
    code: "回复里要有具体改动、命令或可核对的结果，不能只留计划。",
    research: "给出有依据的结论，不能只说接下来去查。",
    write: "直接给出成文。",
    review: "指出具体问题，或明确说明没有发现问题。",
    browser: "说明页面上实际看到的内容。",
    ops: "给出命令或运行结果，不能只承诺去跑。",
    general: "回复本身完成请求，而不是承诺稍后做。",
  };
  const en: Record<TaskIntent, string> = {
    code: "Reply with the concrete change, command, or checkable result, not a plan.",
    research: "Give an evidenced conclusion, not a promise to look it up.",
    write: "Deliver the finished text.",
    review: "Name concrete issues, or say clearly that you found none.",
    browser: "Say what was actually on the page.",
    ops: "Include the command or its result, not a promise to run it.",
    general: "The reply itself finishes the request.",
  };
  return (lang === "zh" ? zh : en)[intent];
}

function poolOf(roster: HarnessAgent[], mentioned?: ReadonlySet<string>): HarnessAgent[] {
  if (!mentioned || mentioned.size === 0) return roster;
  const picked = roster.filter((agent) => mentioned.has(agent.id));
  return picked.length ? picked : roster;
}

function pickVerifier(roster: HarnessAgent[], workerIds: string[], text: string): HarnessAgent | null {
  const rest = roster.filter((agent) => !workerIds.includes(agent.id));
  const ranked = rankAgents(rest, `${text} 审查 汇总 质量`, "review", []);
  const best = ranked[0]?.agent;
  if (!best) return null;
  if (/审|编排|协调|汇总|质量/.test(blobOf(best))) return best;
  return null;
}

function coldTopology(shape: TaskShape, mentioned: number, pool: HarnessAgent[], text: string): Topology {
  if (mentioned >= 2 && shape === "single") {
    const verifier = pickVerifier(pool, [pool[0]?.id || ""], text);
    return verifier ? "hierarchical" : "sequential";
  }
  if (shape === "parallel") return "parallel";
  if (shape === "pipeline") return "sequential";
  return "solo";
}

function lastTrial(entry: HarnessMemory | undefined): HarnessTrial | undefined {
  return entry?.trials[entry.trials.length - 1];
}

function chooseTopology(
  cold: Topology,
  entry: HarnessMemory | undefined,
  ctx: { collab: boolean; mentioned: number },
): { topology: Topology; adjusted: boolean } {
  if (!ctx.collab || ctx.mentioned === 1) return { topology: "solo", adjusted: false };
  if (ctx.mentioned > 0) return { topology: cold, adjusted: false };
  if (entry?.pending) return { topology: entry.pending, adjusted: entry.pending !== cold };
  const last = lastTrial(entry);
  if (entry?.best && (entry.stable || (last?.success && last.topology === entry.best))) {
    return { topology: entry.best, adjusted: entry.best !== cold };
  }
  return { topology: cold, adjusted: false };
}

function soloAssignment(agent: HarnessAgent, task: string): Assignment[] {
  return [{ agentId: agent.id, role: "owner", task }];
}

function assignWork(
  topology: Topology,
  pool: HarnessAgent[],
  fullText: string,
  parts: string[],
  intent: TaskIntent,
  avoid: string[],
): { topology: Topology; assignments: Assignment[] } {
  const ranked = rankAgents(pool, fullText, intent, avoid);
  const owner = ranked[0]?.agent || pool[0];
  if (!owner) return { topology: "solo", assignments: [] };
  if (topology === "solo" || pool.length < 2) {
    return { topology: "solo", assignments: soloAssignment(owner, fullText) };
  }
  if (topology === "parallel") {
    const used = new Set<string>();
    const assignments: Assignment[] = [];
    for (const part of parts) {
      const pick = rankAgents(pool, part, intent, avoid).find((row) => !used.has(row.agent.id))?.agent;
      if (!pick) break;
      used.add(pick.id);
      assignments.push({ agentId: pick.id, role: "worker", task: part });
    }
    if (assignments.length < 2) return { topology: "solo", assignments: soloAssignment(owner, fullText) };
    const verifier = pickVerifier(pool, assignments.map((item) => item.agentId), fullText);
    if (verifier) assignments.push({ agentId: verifier.id, role: "verifier", task: fullText });
    return { topology: "parallel", assignments };
  }
  if (topology === "sequential") {
    // "删除，然后写入，然后打开" is one person's job. A new agent starts only
    // when the user hands the next clause to others, or asks for test/review.
    const steps = parts.length >= 2 ? parts : [fullText];
    const buckets: Array<{ kind: "owner" | "test" | "review"; parts: string[] }> = [];
    for (const part of steps) {
      const kind = handoffKind(part) || "owner";
      const last = buckets[buckets.length - 1];
      if (last && kind === "owner" && last.kind === "owner") last.parts.push(part);
      else buckets.push({ kind, parts: [part] });
    }
    if (buckets.length < 2) return { topology: "solo", assignments: soloAssignment(owner, fullText) };
    const used = new Set<string>();
    const assignments: Assignment[] = [];
    for (const bucket of buckets) {
      const task = bucket.parts.join("，然后");
      const localIntent = detectIntent(task);
      const bucketIntent = bucket.kind === "test"
        ? "code"
        : bucket.kind === "review"
          ? "review"
          : localIntent === "general"
            ? (isMutating(task) ? "code" : intent)
            : localIntent;
      const hint = bucket.kind === "test" ? `${task} 跑测试` : bucket.kind === "review" ? `${task} 审查` : task;
      const pick = rankAgents(pool, hint, bucketIntent, avoid).find((row) => !used.has(row.agent.id))?.agent;
      if (!pick) break;
      used.add(pick.id);
      assignments.push({ agentId: pick.id, role: "worker", task });
    }
    if (assignments.length < 2) return { topology: "solo", assignments: soloAssignment(owner, fullText) };
    return { topology: "sequential", assignments };
  }
  const worker = ranked[0]?.agent || owner;
  const verifier = pickVerifier(pool, [worker.id], fullText);
  if (!verifier || verifier.id === worker.id) {
    return { topology: "solo", assignments: soloAssignment(worker, fullText) };
  }
  return {
    topology: "hierarchical",
    assignments: [
      { agentId: worker.id, role: "worker", task: fullText },
      { agentId: verifier.id, role: "verifier", task: fullText },
    ],
  };
}

function assignTalk(
  topology: Topology,
  pool: HarnessAgent[],
  text: string,
  intent: TaskIntent,
  avoid: string[],
): { topology: Topology; assignments: Assignment[] } {
  const ranked = rankAgents(pool, text, intent, avoid);
  const owner = ranked[0]?.agent || pool[0];
  if (!owner) return { topology: "solo", assignments: [] };
  if (topology === "solo" || pool.length < 2) {
    return { topology: "solo", assignments: soloAssignment(owner, text) };
  }
  if (topology === "hierarchical" || topology === "sequential") {
    const verifier = pickVerifier(pool, [owner.id], text) || ranked.find((row) => row.agent.id !== owner.id)?.agent;
    if (!verifier || verifier.id === owner.id) return { topology: "solo", assignments: soloAssignment(owner, text) };
    return {
      topology: "hierarchical",
      assignments: [
        { agentId: owner.id, role: "worker", task: text },
        { agentId: verifier.id, role: "verifier", task: text },
      ],
    };
  }
  const speakers: HarnessAgent[] = [];
  const seenTitles = new Set<string>();
  for (const row of ranked) {
    if (speakers.length >= 3) break;
    const title = row.agent.title || row.agent.id;
    if (seenTitles.has(title)) continue;
    seenTitles.add(title);
    speakers.push(row.agent);
  }
  if (speakers.length < 2) return { topology: "solo", assignments: soloAssignment(owner, text) };
  return {
    topology: "parallel",
    assignments: speakers.map((agent, index) => ({
      agentId: agent.id,
      role: index === speakers.length - 1 ? "verifier" : "worker",
      task: text,
    })),
  };
}

export function shouldUseHarness(input: {
  text: string;
  execution: boolean;
  collab: boolean;
  lightweight: boolean;
  greeting: boolean;
  capability: boolean;
  directAnswer: boolean;
}): boolean {
  if (input.lightweight || input.greeting || input.capability || input.directAnswer) return false;
  if (input.execution) return true;
  return input.collab && isDiscussionRequest(input.text);
}

function blankMemory(signature: string, shape: TaskShape): HarnessMemory {
  return { signature, shape, trials: [], best: null, stable: false, pending: null, failedOwnerIds: [] };
}

export function planCollaboration(
  userText: string,
  roster: HarnessAgent[],
  ctx: {
    playbook?: HarnessPlaybook | null;
    mentionedIds?: ReadonlySet<string>;
    collab?: boolean;
    /** Earlier user message when this turn only says "discuss that". */
    topicText?: string;
  } = {},
): CollaborationPlan | null {
  const text = String(userText || "").trim();
  if (!text || roster.length === 0) return null;
  const topic = String(ctx.topicText || "").trim();
  const lang = prefersZh(text) ? "zh" : "en";
  const phase: HarnessPhase = isDiscussionRequest(text) ? "talk" : "work";
  const intent = detectIntent(phase === "talk" && topic ? topic : text);
  const { shape, parts } = detectShape(text);
  const signature = `${phase}:${taskSignature(phase === "talk" && topic ? topic : text)}`;
  const brief = phase === "talk" && topic ? `${topic}\n${text}` : text;
  const entry = ctx.playbook?.[signature];
  const mentioned = ctx.mentionedIds?.size || 0;
  const collab = ctx.collab !== false;
  const pool = poolOf(roster, ctx.mentionedIds);
  const cold = phase === "talk"
    ? (pool.length >= 3 ? "parallel" : "solo")
    : coldTopology(shape, mentioned, pool, text);
  const chosen = chooseTopology(cold, entry, { collab, mentioned });
  const avoid = entry?.failedOwnerIds || [];
  const built = phase === "talk"
    ? assignTalk(chosen.topology, pool, brief, intent, avoid)
    : assignWork(chosen.topology, pool, brief, parts, intent, avoid);
  if (!built.assignments.length) return null;
  const fallback = built.assignments.find((item) => item.role === "owner" || item.role === "worker")?.agentId
    || built.assignments[0].agentId;
  const requireVerifier = phase === "work" && built.assignments.some((item) => item.role === "verifier");
  return {
    signature,
    phase,
    topology: built.topology,
    shape,
    intent,
    lang,
    assignments: built.assignments,
    acceptance: acceptanceFor(intent, lang),
    fallbackOwnerId: fallback,
    requireVerifier,
    adjusted: chosen.adjusted,
  };
}

export function formatHarnessContract(
  plan: CollaborationPlan,
  agentId: string,
  opts: { repair?: string; synthesizing?: boolean } = {},
): string {
  const zh = plan.lang === "zh";
  const assignment = plan.assignments.find((item) => item.agentId === agentId);
  const role = opts.synthesizing ? "verifier" : assignment?.role || "worker";
  const task = (opts.repair ? plan.assignments.find((item) => item.role === "owner" || item.role === "worker")?.task : assignment?.task)
    || assignment?.task
    || "";
  if (opts.repair) {
    return zh
      ? `【协作合同·改由你单独完成】\n上一轮没有交付：${opts.repair}\n任务：${task}\n完成标准：${plan.acceptance}\n不要再分派，不要调用 delegate_task、delegate_dialogue 或 ask_agent。`
      : `[Collab contract: you alone]\nThe previous pass did not deliver: ${opts.repair}\nTask: ${task}\nDone when: ${plan.acceptance}\nDo not delegate or ask another agent.`;
  }
  const topicLine = (assignment?.task || "").replace(/\s+/g, " ").trim().slice(0, 180);
  if (plan.phase === "talk" && role === "verifier") {
    return zh
      ? `【协作合同·汇总】\n议题：${topicLine}\n只根据本轮已发言，给用户一个结论。不要 TASK，不要再派活。\n完成标准：${plan.acceptance}`
      : `[Collab contract: summarize]\nTopic: ${topicLine}\nUse only what was already said and give one conclusion. Do not assign more work.\nDone when: ${plan.acceptance}`;
  }
  if (plan.phase === "talk") {
    return zh
      ? `【协作合同·发言】\n议题：${topicLine}\n只补充你职责内的一点新信息，两到三句。不要 TASK，不要 @ 全体，不要复述别人。`
      : `[Collab contract: remark]\nTopic: ${topicLine}\nAdd one new point from your role, in two or three sentences. Do not assign work or repeat others.`;
  }
  if (role === "verifier") {
    return zh
      ? `【协作合同·核验】\n只根据已有结果判断是否达到完成标准。达到就给出最终答案；缺一处就指出那一处。不要重做整项任务。\n完成标准：${plan.acceptance}`
      : `[Collab contract: check]\nJudge the existing results against the bar. If met, give the final answer. If not, name the single gap. Do not redo the task.\nDone when: ${plan.acceptance}`;
  }
  if (plan.topology === "sequential") {
    return zh
      ? `【协作合同·流水线】\n你只做这一步：${task}\n上游结果在最近消息里，不要重做上游，不要再派给别人。\n完成标准：${plan.acceptance}`
      : `[Collab contract: pipeline]\nDo only this step: ${task}\nEarlier results are in the recent messages. Do not redo them or delegate.\nDone when: ${plan.acceptance}`;
  }
  if (plan.topology === "parallel") {
    return zh
      ? `【协作合同·并行】\n你只负责这一支：${task}\n不要做其他人的部分，不要 delegate_task / delegate_dialogue / ask_agent。\n完成标准：${plan.acceptance}`
      : `[Collab contract: parallel]\nYour branch only: ${task}\nDo not take other branches or delegate.\nDone when: ${plan.acceptance}`;
  }
  return zh
    ? `【协作合同·单人执行】\n你是唯一执行人。用工具把事情做完。\n任务：${task}\n完成标准：${plan.acceptance}\n不要转交，不要扮演其他成员，不要调用 delegate_task、delegate_dialogue 或 ask_agent。`
    : `[Collab contract: solo]\nYou are the only executor. Finish with tools.\nTask: ${task}\nDone when: ${plan.acceptance}\nDo not hand off, role-play others, or delegate.`;
}

export function formatHarnessNotice(
  plan: CollaborationPlan,
  roster: HarnessAgent[],
  locale: "zh" | "en",
): string {
  const name = (id: string) => roster.find((agent) => agent.id === id)?.name || id;
  const workers = plan.assignments.filter((item) => item.role === "owner" || item.role === "worker");
  const verifier = plan.assignments.find((item) => item.role === "verifier");
  const zh = locale !== "en";
  const adjusted = plan.adjusted ? (zh ? "（已按同类任务的结果调整）" : " (adjusted from earlier runs)") : "";
  const who = plan.topology === "sequential"
    ? workers.map((item) => name(item.agentId)).join(" → ")
    : workers.map((item) => name(item.agentId)).join(zh ? "、" : ", ");
  if (plan.phase === "talk") {
    return zh
      ? `协作：${who}发言${verifier ? `，${name(verifier.agentId)}汇总` : ""}${adjusted}`
      : `Collab: ${who} speak${verifier ? `, ${name(verifier.agentId)} summarizes` : ""}${adjusted}`;
  }
  const label: Record<Topology, string> = zh
    ? { solo: "单人执行", parallel: "并行", sequential: "流水线", hierarchical: "执行后核验" }
    : { solo: "solo", parallel: "parallel", sequential: "pipeline", hierarchical: "checked" };
  const tail = verifier && plan.topology !== "solo"
    ? (zh ? `，${name(verifier.agentId)}核验` : `, ${name(verifier.agentId)} checks`)
    : "";
  return zh
    ? `协作：${label[plan.topology]} · ${who}${tail}${adjusted}`
    : `Collab: ${label[plan.topology]} · ${who}${tail}${adjusted}`;
}

function substantive(text: string): boolean {
  const compact = text.replace(/\s+/g, "");
  if (compact.length < 8) return false;
  if (/^(好的|收到|我来|稍等|知道了|ok|sure|got it)[.!！。]*$/i.test(compact)) return false;
  return true;
}

function unfinished(text: string): boolean {
  const compact = text.replace(/\s+/g, " ").trim();
  if (!compact) return false;
  return /(?:我(?:来|会|将要|先去|正在)|请稍等|稍等)(?:处理|查|改|修|跑|做|执行|打开)|i(?:'ll| will) (?:check|fix|run|open)/i.test(compact);
}

export function diagnoseHarness(input: {
  plan: CollaborationPlan;
  agentResults: AgentResult[];
  openTasks: number;
}): HarnessDiagnosis {
  const zh = input.plan.lang === "zh";
  const actors = input.agentResults.filter((row) => row.role === "owner" || row.role === "worker" || row.role === "verifier");
  const finals = actors.map((row) => ({
    ...row,
    text: [...row.texts].reverse().find((item) => item.trim()) || "",
  }));
  const workers = finals.filter((row) => row.role === "owner" || row.role === "worker");
  const delivered = workers.filter((row) => substantive(row.text) && !unfinished(row.text) && !row.goalBlocked && row.goalSatisfied !== false);
  const failures: FailureCode[] = [];
  if (!workers.length) failures.push("spec_no_owner");
  if (workers.some((row) => row.goalBlocked)) failures.push("verify_goal_blocked");
  if (workers.some((row) => row.goalSatisfied === false)) failures.push("verify_no_evidence");
  if (workers.some((row) => unfinished(row.text) || !substantive(row.text))) failures.push("verify_unfinished");
  if (workers.some((row) => row.toolErrors > 0 && row.goalSatisfied !== true)) failures.push("coord_tool_conflict");
  if (
    input.plan.phase === "work"
    && input.openTasks > 0
    && delivered.length === 0
    && !workers.some((row) => row.goalSatisfied === true)
  ) {
    failures.push("verify_unfinished");
  }
  const askCount = finals.reduce((sum, row) => sum + (row.text.match(/^ASK\s*[:：]/gim) || []).length, 0);
  if (askCount > 0) failures.push("misalign_ignored_ask");
  const bodies = workers.map((row) => row.text.trim()).filter(Boolean);
  if (new Set(bodies).size < bodies.length) failures.push("misalign_duplicate");
  const outsiders = input.agentResults.filter((row) => row.role === "other" && row.texts.some((item) => substantive(item)));
  if (input.plan.topology === "solo" && outsiders.length) failures.push("misalign_chatter");

  const talkOk = input.plan.phase === "talk" && finals.some((row) => substantive(row.text) && !unfinished(row.text));
  const workOk = input.plan.phase === "work"
    && delivered.length > 0
    && !workers.some((row) => row.goalBlocked || row.goalSatisfied === false || unfinished(row.text));
  const success = (talkOk || workOk)
    && !failures.includes("verify_goal_blocked")
    && !failures.includes("verify_no_evidence");
  const unique = [...new Set(failures)];
  const summary = !success
    ? (zh ? "这一轮没有交付可核对的结果。" : "This pass did not deliver a checkable result.")
    : (zh ? "这一轮已交付。" : "This pass delivered.");
  const innerRepair = !success && input.plan.phase === "work" && input.plan.topology !== "solo" && delivered.length === 0
    ? { agentId: input.plan.fallbackOwnerId, note: summary }
    : null;
  return { success, failures: success ? [] : unique, summary, innerRepair };
}

function historicalBest(entry: HarnessMemory): Topology | null {
  let best: Topology | null = null;
  let bestRate = -1;
  for (const topology of TOPOLOGIES) {
    const rows = entry.trials.filter((trial) => trial.topology === topology);
    if (!rows.length) continue;
    const rate = rows.filter((trial) => trial.success).length / rows.length;
    if (rate > bestRate) {
      bestRate = rate;
      best = topology;
    }
  }
  return best;
}

function markStable(entry: HarnessMemory) {
  if (!entry.best) {
    entry.stable = false;
    return;
  }
  const recent = entry.trials.filter((trial) => trial.topology === entry.best).slice(-2);
  entry.stable = recent.length === 2 && recent.every((trial) => trial.success);
}

function mutate(current: Topology, failures: FailureCode[], tried: Set<Topology>, shape: TaskShape): Topology | null {
  const prefer: Topology[] = [];
  const verifyish = failures.some((code) => code.startsWith("verify_") || code === "misalign_chatter" || code === "misalign_duplicate" || code === "coord_tool_conflict");
  if (verifyish && current !== "solo") prefer.push("solo");
  if (verifyish && current === "solo") prefer.push("hierarchical");
  if (failures.includes("spec_no_owner")) prefer.push("hierarchical");
  if (current === "solo" && shape === "parallel") prefer.push("parallel", "hierarchical");
  if (current === "parallel") prefer.push("sequential", "hierarchical", "solo");
  if (current === "sequential") prefer.push("hierarchical", "solo");
  if (current === "hierarchical") prefer.push("solo", "sequential");
  for (const topology of [...prefer, ...TOPOLOGIES]) {
    if (topology === current || tried.has(topology)) continue;
    if (topology === "parallel" && shape !== "parallel") continue;
    return topology;
  }
  return null;
}

function rememberFailure(entry: HarnessMemory, ownerId: string | undefined) {
  if (!ownerId) return;
  entry.failedOwnerIds = [...new Set([...entry.failedOwnerIds, ownerId])].slice(-4);
}

export function applyHarnessOutcome(
  playbook: HarnessPlaybook | null | undefined,
  plan: CollaborationPlan,
  diagnosis: HarnessDiagnosis,
  opts: { repairedToSolo?: boolean; priorFailures?: FailureCode[]; attemptedSoloRepair?: boolean } = {},
): HarnessPlaybook {
  const next: HarnessPlaybook = { ...(playbook || {}) };
  const entry: HarnessMemory = next[plan.signature]
    ? {
        ...next[plan.signature],
        trials: [...next[plan.signature].trials],
        failedOwnerIds: [...(next[plan.signature].failedOwnerIds || [])],
      }
    : blankMemory(plan.signature, plan.shape);
  const at = Date.now();
  if (opts.repairedToSolo) {
    entry.trials.push({ topology: plan.topology, success: false, at, failures: opts.priorFailures || diagnosis.failures });
    entry.trials.push({ topology: "solo", success: true, at, failures: [] });
    entry.trials = entry.trials.slice(-16);
    entry.best = "solo";
    entry.pending = null;
    entry.failedOwnerIds = [];
    markStable(entry);
    next[plan.signature] = entry;
    return next;
  }
  entry.trials.push({ topology: plan.topology, success: diagnosis.success, at, failures: diagnosis.success ? [] : diagnosis.failures });
  entry.trials = entry.trials.slice(-16);
  if (diagnosis.success) {
    entry.best = plan.topology;
    entry.pending = null;
    entry.failedOwnerIds = [];
    markStable(entry);
  } else {
    if (plan.topology === "solo") rememberFailure(entry, plan.fallbackOwnerId);
    if (opts.attemptedSoloRepair) rememberFailure(entry, plan.fallbackOwnerId);
    entry.stable = false;
    const tried = new Set(entry.trials.map((trial) => trial.topology));
    const pending = mutate(plan.topology, diagnosis.failures, tried, plan.shape);
    if (pending) {
      entry.pending = pending;
      entry.best = historicalBest(entry);
    } else {
      entry.pending = null;
      entry.best = historicalBest(entry) || plan.topology;
      entry.stable = true;
    }
  }
  next[plan.signature] = entry;
  return next;
}
