/**
 * 10-question verification of the free-form group chat loop with the REAL
 * model (through llm_bridge.py) and the SAME prompt builders / loop the UI
 * uses (groupLoop.ts, buildPrompt.ts).
 *
 *   cd ui
 *   node --import ./ts-ext-hook.mjs ../scripts/verify_group_chat/run.ts [--bridge http://127.0.0.1:8799] [--only 3] [--stub]
 */
import { writeFileSync } from "node:fs";
import {
  buildReplyDecisionPrompt,
  defaultMaxAgentMessages,
  formatPipelineNotice,
  type ExplicitPipeline,
  type TurnIntent,
  parseReplyDecision,
  runGroupConversation,
  type ChatAgent,
  type ChatLine,
  type LoopEvent,
} from "../../ui/src/social/groupLoop.ts";
import { buildSocialBackendMessage } from "../../ui/src/social/buildPrompt.ts";
import { isNearDuplicateBubble, visibleReplyAfterJudge, isExecutionRequested } from "../../ui/src/social/turnLogic.ts";
import { cleanDisplayText } from "../../ui/src/social/sharedBoard.ts";
import { programmingAgents, defaultAgents } from "../../ui/src/social/store.ts";
import { formatHarnessNotice, planCollaboration } from "../../ui/src/social/harness.ts";

const args = process.argv.slice(2);
const arg = (name: string) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : undefined;
};
const BRIDGE = arg("--bridge") || "http://127.0.0.1:8799";
const ONLY = arg("--only") ? Number(arg("--only")) : 0;
const STUB = args.includes("--stub");
const OUT = arg("--out") || "../scripts/verify_group_chat/results.json";
/** --set main (the 10 questions) | extra (unseen overfitting probes) | all */
const SET = arg("--set") || "main";

const asChat = (list: ReturnType<typeof programmingAgents>): ChatAgent[] =>
  list.map((a) => ({ id: a.id, name: a.name, title: a.title, duty: a.duty, blurb: a.blurb, persona: a.persona }));
const ROSTERS: { [k: string]: ChatAgent[] } = { code: asChat(programmingAgents()), default: asChat(defaultAgents()) };
const GROUPS: { [k: string]: { name: string; topic: string } } = {
  code: { name: "编程组", topic: "读代码、改代码、跑测试" },
  default: { name: "协作群", topic: "日常协作" },
};


let llmCalls = 0;
/** Transport / provider errors. Any error makes the run invalid (it could mask behaviour). */
let llmErrors = 0;
/** At most JOBS model calls in flight: more only gets throttled by the provider. */
const JOBS = Number(arg("--jobs") || 6);
let inFlight = 0;
const queue: Array<() => void> = [];
async function slot<T>(fn: () => Promise<T>): Promise<T> {
  if (inFlight >= JOBS) await new Promise<void>((resolve) => queue.push(resolve));
  inFlight += 1;
  try {
    return await fn();
  } finally {
    inFlight -= 1;
    queue.shift()?.();
  }
}
async function complete(prompt: string, maxTokens: number): Promise<string | null> {
  llmCalls += 1;
  if (STUB) return null;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      const data = await slot(async () => {
        const r = await fetch(`${BRIDGE}/complete`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt, max_tokens: maxTokens }),
        });
        return (await r.json()) as { text: string; error?: string };
      });
      if (!data.error || data.text) return data.text;
      console.error(`  LLM error (attempt ${attempt}):`, data.error);
    } catch (err) {
      console.error(`  bridge error (attempt ${attempt}):`, String(err));
    }
    await new Promise((resolve) => setTimeout(resolve, 2000 * attempt));
  }
  llmErrors += 1;
  return null;
}

type Scenario = {
  n: number;
  kind: string;
  text: string;
  /** Earlier conversation (follow-up); "from:N" reuses scenario N's transcript. */
  priorFrom?: number;
  expectPipeline: boolean;
  expectFirst?: string;
  expectPipelineOrder?: string[];
  /** Which configured team answers (default: the programming team). */
  roster?: "code" | "default";
  /** Members that must be among the speakers. */
  mustSpeak?: string[];
  /** Natural-group-chat expectations. */
  minSpeakers?: number;
  maxSpeakers?: number;
  maxChars?: number;
};

const SCENARIOS: Scenario[] = [
  { n: 1, kind: "greeting", text: "大家好", expectPipeline: false, minSpeakers: 3, maxChars: 120 },
  {
    n: 2,
    kind: "coding bug",
    text: "我写了 def avg(xs): return sum(xs)/len(xs)，传空列表就报 ZeroDivisionError，怎么改比较好？",
    expectPipeline: false,
  },
  {
    n: 3,
    kind: "code review",
    expectFirst: "codereview",
    text: "帮我审一下这段代码有没有问题：\n```python\nimport json\ndef read_config(path):\n    f = open(path)\n    data = json.load(f)\n    return data['db']['host']\n```",
    expectPipeline: false,
  },
  {
    n: 4,
    kind: "addressed to one agent",
    text: "验测，你会怎么给 avg(xs) 这个函数写单元测试？要覆盖哪些边界情况？",
    expectPipeline: false,
    expectFirst: "tester",
  },
  {
    n: 5,
    kind: "build on each other",
    text: "我们要给一个 Flask 小项目加用户登录功能，大家从各自的角度说说要注意什么？",
    expectPipeline: false,
    minSpeakers: 3,
  },
  {
    n: 6,
    kind: "debate",
    text: "有人说单元测试覆盖率必须到 100% 才算合格，你们同意吗？有不同意见可以直接反驳。",
    expectPipeline: false,
    minSpeakers: 2,
  },
  {
    n: 7,
    kind: "explicit pipeline",
    text: "先排错再审码：下面这个函数有时返回 None，帮我找原因，然后审查修复方案。\n```python\ndef find_user(users, name):\n    for u in users:\n        if u['name'] == name:\n            return u\n```",
    expectPipeline: true,
    expectPipelineOrder: ["debugger", "codereview"],
  },
  {
    n: 8,
    kind: "follow-up mid-conversation",
    text: "那如果列表里混进了 None 值呢？刚才的写法还行吗？",
    priorFrom: 2,
    expectPipeline: false,
  },
  {
    n: 9,
    kind: "old pipeline trigger (然后/其他成员)",
    text: "帮我修复登录页的空指针报错，修好之后让其他成员看看",
    expectPipeline: false,
  },
  { n: 10, kind: "thanks / closing", text: "好的，明白了，谢谢大家", priorFrom: 2, expectPipeline: false, maxSpeakers: 2, maxChars: 150 },
];

/** Unseen probes (written after the 10 passed) to guard against overfitting. */
const EXTRA: Scenario[] = [
  {
    n: 11,
    kind: "extra · third-person mention, not an address",
    text: "刚才码川说的那个方案我觉得有点复杂，有没有更简单的写法？",
    priorFrom: 2,
    expectPipeline: false,
  },
  {
    n: 12,
    kind: "extra · order requested without 先/再 wording",
    text: "这次按这个顺序来：码川写一个 slugify(text) 函数（转小写、空格变连字符、去掉标点），写完交给验测补单元测试。",
    expectPipeline: true,
    expectPipelineOrder: ["coder", "tester"],
  },
  {
    n: 13,
    kind: "extra · two members @-addressed",
    text: "@验测 @审码 你们俩看看正则 ^\\d{3}-\\d{4}$ 能不能匹配 123-45678？",
    expectPipeline: false,
    mustSpeak: ["tester", "codereview"],
    maxSpeakers: 3,
  },
  {
    n: 14,
    kind: "extra · differently worded greeting, other team",
    roster: "default",
    text: "大伙儿晚上好，今天辛苦啦",
    expectPipeline: false,
    minSpeakers: 1,
    maxChars: 120,
  },
  {
    n: 15,
    kind: "extra · English, order by role (other team)",
    roster: "default",
    text: "Could the researcher look up the boiling point of water at 3000 m altitude first, and then have the writer turn it into a one-line tweet?",
    expectPipeline: true,
  },
];

type Record = {
  n: number;
  kind: string;
  message: string;
  intent: TurnIntent | null;
  pipelineShown: boolean;
  notice: string | null;
  /** What the OLD automatic planner would have shown for this message. */
  oldNotice: string | null;
  rounds: Array<{ trigger: string; author: string; decisions: string[] }>;
  order: string[];
  sawEarlier: boolean;
  endReason: string;
  agentMessages: number;
  checks: Array<{ name: string; ok: boolean; note?: string }>;
  pass: boolean;
  transcript: ChatLine[];
  logs?: string[];
  ms: number;
};

const results: Record[] = [];

async function runScenario(sc: Scenario, prior: ChatLine[] = []): Promise<Record> {
  const logs: string[] = [];
  const log = (line: string) => logs.push(line);
  const started = Date.now();
  const userLine: ChatLine = { id: `u${sc.n}`, role: "user", text: sc.text };
  const history: ChatLine[] = [...prior, userLine];
  // Each scenario has its own roster binding (scenarios run concurrently).
  const ROSTER = ROSTERS[sc.roster || "code"];
  const nameOf = (id?: string) => ROSTER.find((a) => a.id === id)?.name || id || "用户";
  let pipeline: ExplicitPipeline | null = null;
  let intent: TurnIntent | null = null;
  const oldPlan = planCollaboration(sc.text, ROSTER as any, { collab: true });
  const oldNotice = isExecutionRequested(sc.text) && oldPlan ? formatHarnessNotice(oldPlan, ROSTER as any, "zh") : null;
  const events: LoopEvent[] = [];
  const replyHistories: Array<{ agentId: string; ids: string[] }> = [];
  const produced: ChatLine[] = [];
  const res = await runGroupConversation({
    roster: ROSTER,
    history,
    analyzeTurn: async (prompt) => (STUB ? null : await complete(prompt, 0)),
    maxAgentMessages: defaultMaxAgentMessages(ROSTER.length),
    maxRepliesPerAgent: 3,
    onEvent: (e) => {
      events.push(e);
      if (e.type === "intent") {
        intent = e.intent;
        pipeline = e.intent.pipeline;
        log(
          `  intent(${e.intent.source}${e.intent.fallbackReason ? ":" + e.intent.fallbackReason : ""}): audience=${e.intent.audience} members=[${e.intent.memberIds.map(nameOf).join(",")}] lead=${e.intent.leadId ? nameOf(e.intent.leadId) : "-"} social=${e.intent.social} action=${e.intent.action} pipeline=${pipeline ? pipeline.steps.map((x) => nameOf(x.agentId)).join("→") : "none"}`,
        );
      }
      if (e.type === "decisions") {
        log(
          `  [${nameOf(e.trigger.agentId)}] → ` +
            e.decisions.map((d) => `${d.agentName}:${d.reply ? "回复" : "沉默"}(${d.reason})`).join(" "),
        );
      }
      if (e.type === "message") log(`  >> ${e.line.agentName}: ${e.line.text.replace(/\s+/g, " ").slice(0, 110)}`);
      if (e.type === "end") log(`  end: ${e.reason} (${e.agentMessages} msgs)`);
    },
    judge: async (input) => {
      if (STUB) {
        // Stub: reply when addressed or when the trigger is the user and duty overlaps.
        if (input.addressed) return true;
        return input.trigger.role === "user" && input.agent.id === "coder";
      }
      const out = await complete(buildReplyDecisionPrompt(input), 0);
      if (out === null) return null;
      return parseReplyDecision(out);
    },
    reply: async (input) => {
      replyHistories.push({ agentId: input.agent.id, ids: input.history.map((l) => l.id) });
      const stepIndex = input.step && pipeline ? pipeline.steps.indexOf(input.step) : -1;
      const next = stepIndex >= 0 && pipeline ? pipeline.steps[stepIndex + 1] : undefined;
      const prompt = buildSocialBackendMessage({
        userText: sc.text,
        targets: [input.agent as any],
        history: input.history.slice(0, -1).map((l) => ({ ...l, at: 0 })) as any,
        historyLimit: 24,
        roster: ROSTER as any,
        mode: "group",
        group: { id: `group_${sc.roster || "code"}`, ...GROUPS[sc.roster || "code"], memberIds: [], announcements: [], shared: [], createdAt: 0 } as any,
        optionalReply: true,
        freeChat: {
          latest: {
            author: input.trigger.role === "user" ? "用户" : input.trigger.agentName || "成员",
            isUser: input.trigger.role === "user",
            text: input.trigger.text,
          },
          addressed: input.addressed,
          brief: input.brief,
          everyone: input.everyone,
          mustAnswer: input.mustAnswer,
          pipelineStep: input.step && pipeline
            ? { index: stepIndex + 1, total: pipeline.steps.length, task: input.step.task, nextName: next ? nameOf(next.agentId) : undefined }
            : undefined,
        },
      });
      const out = STUB ? `${input.agent.name}的回复（stub）` : await complete(prompt, 0);
      const visible = visibleReplyAfterJudge(cleanDisplayText(String(out || "")));
      if (!visible) return null;
      if (isNearDuplicateBubble(input.agent.id, visible, produced)) return null;
      produced.push({ id: "tmp", role: "agent", agentId: input.agent.id, text: visible });
      return visible;
    },
  });

  const rounds = events
    .filter((e): e is Extract<LoopEvent, { type: "decisions" }> => e.type === "decisions")
    .map((e) => ({
      trigger: e.trigger.id,
      author: nameOf(e.trigger.agentId),
      decisions: e.decisions.map((d) => `${d.agentName}:${d.reply ? "回复" : "沉默"}${d.reason === "judge" ? "" : `(${d.reason})`}`),
    }));
  const order = res.messages.map((m) => m.agentName || "");
  const notice = pipeline ? formatPipelineNotice(pipeline, ROSTER, "zh") : null;
  const checks: Record["checks"] = [];
  checks.push({ name: "pipeline label only when explicitly requested", ok: Boolean(pipeline) === sc.expectPipeline, note: notice || "none" });
  // Every free-loop trigger got a decision from every other member.
  const freeRounds = events.filter(
    (e): e is Extract<LoopEvent, { type: "decisions" }> => e.type === "decisions" && !e.decisions.some((d) => d.reason === "pipeline"),
  );
  const everyoneDecided = freeRounds.every((e) => {
    const ids = new Set(e.decisions.map((d) => d.agentId));
    return ROSTER.filter((a) => a.id !== e.trigger.agentId).every((a) => ids.has(a.id)) && !ids.has(String(e.trigger.agentId));
  });
  checks.push({ name: "every new message → every other member decided", ok: everyoneDecided && freeRounds.length >= 1 });
  const noSelf = res.messages.every((m, i) => i === 0 || m.agentId !== res.messages[i - 1].agentId);
  checks.push({ name: "no member replied to its own message", ok: noSelf });
  const sawEarlier = replyHistories.every((row, i) => {
    const earlier = res.messages.slice(0, i).map((m) => m.id);
    return earlier.every((id) => row.ids.includes(id)) && row.ids.includes(userLine.id);
  });
  checks.push({ name: "later replies saw all earlier replies", ok: sawEarlier });
  checks.push({ name: "loop terminated (quiet/cap)", ok: res.reason === "quiet" || res.reason === "cap", note: res.reason });
  checks.push({ name: "user got at least one reply", ok: res.messages.length >= 1 });
  if (sc.expectFirst) {
    checks.push({ name: `expected first responder (${nameOf(sc.expectFirst)}) answers first`, ok: res.messages[0]?.agentId === sc.expectFirst });
  }
  if (sc.expectPipelineOrder) {
    const got = res.messages.slice(0, sc.expectPipelineOrder.length).map((m) => m.agentId);
    checks.push({
      name: `pipeline order ${sc.expectPipelineOrder.map(nameOf).join(" → ")}`,
      ok: JSON.stringify(got) === JSON.stringify(sc.expectPipelineOrder),
    });
  }
  const speakers = new Set(res.messages.map((m) => m.agentId));
  if (sc.mustSpeak) checks.push({ name: `${sc.mustSpeak.map(nameOf).join("、")} all spoke`, ok: sc.mustSpeak.every((id) => speakers.has(id)) });
  if (sc.minSpeakers) checks.push({ name: `at least ${sc.minSpeakers} members spoke`, ok: speakers.size >= sc.minSpeakers, note: String(speakers.size) });
  if (sc.maxSpeakers) checks.push({ name: `at most ${sc.maxSpeakers} members spoke`, ok: speakers.size <= sc.maxSpeakers, note: String(speakers.size) });
  if (sc.maxChars) {
    const longest = Math.max(0, ...res.messages.map((m) => m.text.length));
    checks.push({ name: `replies stay short (≤${sc.maxChars} chars)`, ok: longest <= sc.maxChars, note: String(longest) });
  }
  const leakedTool = res.messages.some((m) => /<\/?(?:search_files|read_file|run_shell|tool_call)[^>]*>/i.test(m.text));
  checks.push({ name: "no raw tool-call markup in replies", ok: !leakedTool });
  if (sc.priorFrom) {
    checks.push({ name: "follow-up saw earlier conversation", ok: (replyHistories[0]?.ids.length || 0) > prior.length });
  }
  const pass = checks.every((c) => c.ok);
  return {
    n: sc.n,
    kind: sc.kind,
    message: sc.text,
    intent,
    pipelineShown: Boolean(pipeline),
    notice,
    oldNotice,
    rounds,
    order,
    sawEarlier,
    endReason: res.reason,
    agentMessages: res.messages.length,
    checks,
    pass,
    transcript: [...history, ...res.messages],
    logs,
    ms: Date.now() - started,
  };
}

(async () => {
  if (!STUB) {
    const h = await fetch(`${BRIDGE}/`).then((r) => r.json()).catch(() => null);
    console.log("bridge:", h);
    if (!h || h.demo) {
      console.error("No working LLM bridge (or demo mode).");
      process.exit(2);
    }
  }
  const plan = SET === "extra" ? EXTRA : SET === "all" ? [...SCENARIOS, ...EXTRA] : SCENARIOS;
  const selected = plan.filter((sc) => !ONLY || sc.n === ONLY);
  // Independent scenarios run concurrently; a follow-up waits only for its base.
  const running = new Map<number, Promise<Record>>();
  const contextOnly = new Set<number>();
  const start = (sc: Scenario): Promise<Record> => {
    const existing = running.get(sc.n);
    if (existing) return existing;
    const job = (async () => {
      let prior: ChatLine[] = [];
      if (sc.priorFrom) {
        const base = [...SCENARIOS, ...EXTRA].find((x) => x.n === sc.priorFrom)!;
        if (!selected.includes(base)) contextOnly.add(base.n);
        prior = (await start(base)).transcript;
      }
      return runScenario(sc, prior);
    })();
    running.set(sc.n, job);
    return job;
  };
  const t0 = Date.now();
  // Print each scenario the moment it finishes (stdout is redirected to a file
  // that can be read live), then a summary table in question order.
  const report = (sc: Scenario, rec: Record) => {
    const ctx = contextOnly.has(sc.n);
    if (ctx) (rec as any).contextOnly = true;
    results.push(rec);
    const out = [`\n=== Q${sc.n} [${sc.kind}]${ctx ? " (context only, not scored)" : ""} ${sc.text.split("\n")[0]}`];
    out.push(...(rec.logs || []));
    out.push(`  old planner would show: ${rec.oldNotice || "(nothing)"}`);
    for (const c of rec.checks) out.push(`  ${c.ok ? "PASS" : "FAIL"} ${c.name}${c.note ? ` [${c.note}]` : ""}`);
    out.push(`  => ${rec.pass ? "PASS" : "FAIL"} in ${(rec.ms / 1000).toFixed(1)}s (t+${((Date.now() - t0) / 1000).toFixed(0)}s)`);
    console.log(out.join("\n"));
    writeFileSync(OUT, JSON.stringify({ llmCalls, results }, null, 2), "utf8");
  };
  await Promise.all(
    selected.map(async (sc) => {
      const rec = await start(sc);
      if (sc.priorFrom && contextOnly.has(sc.priorFrom) && !results.some((r) => r.n === sc.priorFrom)) {
        report([...SCENARIOS, ...EXTRA].find((x) => x.n === sc.priorFrom)!, await running.get(sc.priorFrom)!);
      }
      report(sc, rec);
    }),
  );
  results.sort((x, y) => x.n - y.n);
  console.log("\n--- summary (question order) ---");
  for (const r of results) {
    if ((r as any).contextOnly) continue;
    console.log(`  Q${r.n} ${r.pass ? "PASS" : "FAIL"}  pipeline=${r.notice || "none"}  order=${r.order.join(" → ") || "(none)"}`);
  }
  writeFileSync(OUT, JSON.stringify({ llmCalls, wallSeconds: (Date.now() - t0) / 1000, results }, null, 2), "utf8");
  console.log(`\nwall time: ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  const scored = results.filter((r) => !(r as any).contextOnly);
  const passed = scored.filter((r) => r.pass).length;
  console.log(`\nSUMMARY: ${passed}/${scored.length} passed, ${llmCalls} LLM calls, ${llmErrors} LLM errors${llmErrors ? " (RUN INVALID: errors can mask behaviour)" : ""}`);
  process.exit(passed === scored.length && !llmErrors ? 0 : 1);
})();
