import assert from "node:assert/strict";
import test from "node:test";
import {
  atMentions,
  buildPlannerPrompt,
  fallbackPlan,
  formatPlanNotice,
  isPass,
  looksLikeGreeting,
  parsePlan,
  parseReview,
  runCollaboration,
  type CollabEvent,
  type MemberTask,
} from "./src/social/collab.ts";
import type { ChatAgent, ChatLine } from "./src/social/groupLoop.ts";

const TEAM: ChatAgent[] = [
  { id: "arch", name: "架构师", title: "架构", duty: "拆解需求，设计方案与接口" },
  { id: "dev", name: "码川", title: "程序员", duty: "在工作区里实现功能并运行验证" },
  { id: "qa", name: "验测", title: "测试", duty: "编写并运行测试，审查改动的正确性与风险" },
];

const user = (text: string): ChatLine => ({ id: "u1", role: "user", text });
const ctx = (text: string) => ({
  roster: TEAM,
  userText: text,
  mentioned: atMentions(text, TEAM),
  everyone: /@(全体|所有人|everyone|all)/.test(text),
});

function scriptedMembers(script: (task: MemberTask, n: number) => string | { text: string; error?: string }) {
  const calls: MemberTask[] = [];
  const runMember = async (task: MemberTask) => {
    calls.push(task);
    const out = script(task, calls.length);
    return typeof out === "string" ? { text: out } : out;
  };
  return { calls, runMember };
}

const planJson = (obj: Record<string, unknown>) => async () => JSON.stringify(obj);

test("atMentions only honours explicit @ names", () => {
  assert.deepEqual(atMentions("@码川 帮忙看下，架构师的方案不错", TEAM).map((a) => a.id), ["dev"]);
  assert.deepEqual(atMentions("@码川，@验测 一起来", TEAM).map((a) => a.id), ["dev", "qa"]);
  assert.deepEqual(atMentions("没有点名", TEAM), []);
});

test("small helpers: greeting, PASS, review verdict", () => {
  assert.ok(looksLikeGreeting("大家好！"));
  assert.ok(!looksLikeGreeting("大家好，帮我修个 bug"));
  assert.ok(isPass("PASS"));
  assert.ok(isPass("无补充。"));
  assert.ok(!isPass("我补充一点：要加缓存"));
  assert.deepEqual(parseReview("APPROVE\n看起来没问题"), { approved: true, issues: "看起来没问题" });
  assert.equal(parseReview("CHANGES: 缺少空值判断").approved, false);
  assert.equal(parseReview("CHANGES: 缺少空值判断").issues, "缺少空值判断");
  assert.equal(parseReview("第 3 行会抛异常").approved, false);
});

test("planner prompt lists roster, constraint and the JSON contract", () => {
  const prompt = buildPlannerPrompt({ ...ctx("@码川 改一下登录页"), history: [user("@码川 改一下登录页")] });
  assert.match(prompt, /架构师（架构）/);
  assert.match(prompt, /用户点名了：码川/);
  assert.match(prompt, /"mode":"answer"/);
});

test("parsePlan resolves members, keeps pipeline order and drops forward deps", () => {
  const plan = parsePlan(
    "```json\n" +
      JSON.stringify({
        mode: "pipeline",
        lead: "架构师",
        steps: [
          { id: "s1", member: "架构师", task: "设计接口", expected: "接口清单", depends_on: ["s2"] },
          { id: "s2", member: "码川", task: "实现", depends_on: ["s1"], tools: true },
        ],
        review: { member: "验测", focus: "边界条件" },
        synthesize: false,
      }) +
      "\n```",
    ctx("做一个登录接口"),
  )!;
  assert.equal(plan.mode, "pipeline");
  assert.deepEqual(plan.steps.map((s) => s.agentId), ["arch", "dev"]);
  assert.deepEqual(plan.steps[0].dependsOn, []);
  assert.deepEqual(plan.steps[1].dependsOn, ["s1"]);
  assert.equal(plan.review?.agentId, "qa");
  assert.equal(plan.source, "model");
});

test("parsePlan: @-named members win over the planner's choice", () => {
  const plan = parsePlan(
    JSON.stringify({ mode: "answer", lead: "架构师", steps: [{ member: "架构师", task: "x" }] }),
    ctx("@验测 这个函数有没有 bug"),
  )!;
  assert.deepEqual(plan.steps.map((s) => s.agentId), ["qa"]);
  assert.equal(plan.leadId, "qa");
});

test("parsePlan: a reviewer who did the work is dropped; chat never uses tools", () => {
  const solo = parsePlan(
    JSON.stringify({ mode: "answer", lead: "码川", steps: [{ member: "码川", task: "x" }], review: { member: "码川" } }),
    ctx("改个 bug"),
  )!;
  assert.equal(solo.review, null);
  const chat = parsePlan(
    JSON.stringify({ mode: "chat", steps: [{ member: "码川", task: "问好", tools: true }] }),
    ctx("你好"),
  )!;
  assert.equal(chat.steps[0].tools, false);
});

test("fallback plan: greeting → chat; plain question → one best-fit answerer", () => {
  assert.equal(fallbackPlan(ctx("大家好"), "error").mode, "chat");
  const answer = fallbackPlan(ctx("帮我写测试并运行"), "error");
  assert.equal(answer.mode, "answer");
  assert.equal(answer.steps.length, 1);
  assert.equal(answer.steps[0].agentId, "qa");
});

test("answer mode: exactly one member answers — a multi-agent group is never worse than one agent", async () => {
  const { calls, runMember } = scriptedMembers(() => "这是答案");
  const res = await runCollaboration({
    roster: TEAM,
    history: [user("Python 里怎么反转列表？")],
    plan: planJson({ mode: "answer", lead: "码川", steps: [{ member: "码川", task: "回答", tools: false }] }),
    runMember,
  });
  assert.equal(res.messages.length, 1);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].role, "lead");
  assert.equal(calls[0].mustAnswer, true);
  assert.equal(res.reason, "done");
});

test("planner failure never leaves the user unanswered", async () => {
  const { calls, runMember } = scriptedMembers(() => "我来答");
  const res = await runCollaboration({
    roster: TEAM,
    history: [user("实现一个排序函数")],
    plan: async () => {
      throw new Error("network");
    },
    runMember,
  });
  assert.equal(res.plan.source, "fallback");
  assert.equal(res.plan.fallbackReason, "error");
  assert.equal(res.messages.length, 1);
  assert.equal(calls[0].tools, true);
});

test("unparseable planner output is retried once, then falls back", async () => {
  let n = 0;
  const { runMember } = scriptedMembers(() => "ok");
  const res = await runCollaboration({
    roster: TEAM,
    history: [user("你好")],
    plan: async () => {
      n += 1;
      return "我觉得应该让大家都说话";
    },
    runMember,
  });
  assert.equal(n, 2);
  assert.equal(res.plan.mode, "chat");
  assert.ok(res.messages.length >= 1);
});

test("empty reply is retried with attempt=1, then reassigned to a colleague", async () => {
  const { calls, runMember } = scriptedMembers((task) => (task.agent.id === "dev" ? "" : `${task.agent.name} 接手完成`));
  const events: CollabEvent[] = [];
  const res = await runCollaboration({
    roster: TEAM,
    history: [user("实现并测试")],
    plan: planJson({ mode: "answer", lead: "码川", steps: [{ member: "码川", task: "实现" }] }),
    runMember,
    onEvent: (e) => events.push(e),
  });
  assert.deepEqual(calls.map((c) => [c.agent.id, c.attempt]), [["dev", 0], ["dev", 1], [calls[2].agent.id, 0]]);
  assert.notEqual(calls[2].agent.id, "dev");
  assert.equal(res.messages.length, 1);
  const failed = events.find((e) => e.type === "step_failed");
  assert.ok(failed && failed.type === "step_failed" && failed.reassignedTo === calls[2].agent.id);
});

test("member errors surface as step_failed; total failure ends with reason=failed", async () => {
  const { runMember } = scriptedMembers(() => ({ text: "", error: "429 rate limit" }));
  const events: CollabEvent[] = [];
  const res = await runCollaboration({
    roster: TEAM,
    history: [user("做点什么")],
    plan: planJson({ mode: "answer", lead: "码川", steps: [{ member: "码川", task: "做" }] }),
    runMember,
    onEvent: (e) => events.push(e),
  });
  assert.equal(res.reason, "failed");
  assert.ok(events.some((e) => e.type === "step_failed" && e.error.includes("429")));
});

test("parallel: workers know each other's tasks, run concurrently, and the lead synthesizes", async () => {
  let running = 0;
  let peak = 0;
  const calls: MemberTask[] = [];
  const runMember = async (task: MemberTask) => {
    calls.push(task);
    running += 1;
    peak = Math.max(peak, running);
    await new Promise((r) => setTimeout(r, 10));
    running -= 1;
    return { text: `${task.agent.name}:${task.role}` };
  };
  const res = await runCollaboration({
    roster: TEAM,
    history: [user("从架构、实现、测试三个角度评估这个方案")],
    plan: planJson({
      mode: "parallel",
      lead: "架构师",
      steps: [
        { member: "架构师", task: "评估架构" },
        { member: "码川", task: "评估实现成本" },
        { member: "验测", task: "评估可测性" },
      ],
    }),
    runMember,
    concurrency: 2,
  });
  assert.equal(peak, 2);
  const workers = calls.filter((c) => c.role === "worker");
  assert.equal(workers.length, 3);
  assert.deepEqual(workers[0].peers.map((p) => p.task).sort(), ["评估可测性", "评估实现成本"]);
  const synth = calls.find((c) => c.role === "synthesizer")!;
  assert.equal(synth.agent.id, "arch");
  assert.equal(synth.contributions.length, 3);
  assert.equal(res.messages.length, 4);
});

test("pipeline: each step receives its upstream deliverable", async () => {
  const { calls, runMember } = scriptedMembers((task) => `${task.agent.name} 交付`);
  await runCollaboration({
    roster: TEAM,
    history: [user("设计并实现登录接口")],
    plan: planJson({
      mode: "pipeline",
      lead: "架构师",
      steps: [
        { id: "s1", member: "架构师", task: "设计接口" },
        { id: "s2", member: "码川", task: "实现", depends_on: ["s1"] },
      ],
    }),
    runMember,
  });
  assert.equal(calls[0].upstream.length, 0);
  assert.equal(calls[1].upstream.length, 1);
  assert.equal(calls[1].upstream[0].text, "架构师 交付");
});

test("review requests changes → the owner revises once with the reviewer's issues", async () => {
  const { calls, runMember } = scriptedMembers((task) =>
    task.role === "reviewer" ? "CHANGES: 没处理空密码" : task.role === "reviser" ? "已修复空密码" : "实现完成",
  );
  const events: CollabEvent[] = [];
  const res = await runCollaboration({
    roster: TEAM,
    history: [user("实现登录")],
    plan: planJson({
      mode: "answer",
      lead: "码川",
      steps: [{ member: "码川", task: "实现登录", tools: true }],
      review: { member: "验测", focus: "边界" },
    }),
    runMember,
    onEvent: (e) => events.push(e),
  });
  assert.deepEqual(calls.map((c) => c.role), ["lead", "reviewer", "reviser"]);
  assert.equal(calls[1].contributions[0].text, "实现完成");
  assert.equal(calls[2].agent.id, "dev");
  assert.equal(calls[2].issues, "没处理空密码");
  assert.ok(events.some((e) => e.type === "review" && !e.approved));
  assert.equal(res.messages.length, 3);
});

test("approved review does not trigger a revision", async () => {
  const { calls, runMember } = scriptedMembers((task) => (task.role === "reviewer" ? "APPROVE" : "完成"));
  await runCollaboration({
    roster: TEAM,
    history: [user("实现登录")],
    plan: planJson({ mode: "answer", lead: "码川", steps: [{ member: "码川", task: "x" }], review: { member: "验测" } }),
    runMember,
  });
  assert.deepEqual(calls.map((c) => c.role), ["lead", "reviewer"]);
});

test("discuss: later voices may PASS; a lone surviving voice is not synthesized", async () => {
  const { calls, runMember } = scriptedMembers((task) => (task.agent.id === "arch" ? "我建议用方案A" : "PASS"));
  const res = await runCollaboration({
    roster: TEAM,
    history: [user("方案A和B选哪个？")],
    plan: planJson({
      mode: "discuss",
      lead: "架构师",
      steps: [{ member: "架构师", task: "x" }, { member: "码川", task: "y" }, { member: "验测", task: "z" }],
    }),
    runMember,
  });
  assert.deepEqual(calls.map((c) => [c.agent.id, c.mustAnswer]), [["arch", true], ["dev", false], ["qa", false]]);
  assert.equal(res.messages.length, 1);
  assert.ok(!calls.some((c) => c.role === "synthesizer"));
});

test("members talk to each other: an @colleague request gets one handoff reply", async () => {
  const { calls, runMember } = scriptedMembers((task) =>
    task.role === "lead" ? "实现好了，@验测 帮我跑一下测试" : "测试全部通过",
  );
  const events: CollabEvent[] = [];
  const res = await runCollaboration({
    roster: TEAM,
    history: [user("实现排序")],
    plan: planJson({ mode: "answer", lead: "码川", steps: [{ member: "码川", task: "实现" }] }),
    runMember,
    onEvent: (e) => events.push(e),
  });
  assert.deepEqual(calls.map((c) => [c.agent.id, c.role]), [["dev", "lead"], ["qa", "handoff"]]);
  assert.equal(calls[1].trigger.agentId, "dev");
  assert.ok(events.some((e) => e.type === "handoff" && e.toId === "qa"));
  assert.equal(res.messages.length, 2);
});

test("handoffs are bounded: ping-pong stops after one reply per member", async () => {
  const { calls, runMember } = scriptedMembers((task) =>
    task.agent.id === "dev" ? "@验测 你看看" : "@码川 你再看看",
  );
  await runCollaboration({
    roster: TEAM,
    history: [user("看看")],
    plan: planJson({ mode: "answer", lead: "码川", steps: [{ member: "码川", task: "x" }] }),
    runMember,
  });
  assert.ok(calls.length <= 4);
});

test("a member asking the user pauses the turn", async () => {
  const { calls, runMember } = scriptedMembers(() => "请确认要删除哪个文件？");
  const res = await runCollaboration({
    roster: TEAM,
    history: [user("清理一下")],
    plan: planJson({
      mode: "pipeline",
      steps: [{ id: "s1", member: "码川", task: "a" }, { id: "s2", member: "验测", task: "b", depends_on: ["s1"] }],
    }),
    runMember,
    shouldStop: (line) => /请确认/.test(line.text),
  });
  assert.equal(res.reason, "paused");
  assert.equal(calls.length, 1);
});

test("abort stops immediately", async () => {
  const ac = new AbortController();
  const { calls, runMember } = scriptedMembers(() => {
    ac.abort();
    return "x";
  });
  const res = await runCollaboration({
    roster: TEAM,
    history: [user("讨论一下")],
    plan: planJson({ mode: "discuss", steps: [{ member: "码川", task: "a" }, { member: "验测", task: "b" }] }),
    runMember,
    signal: ac.signal,
  });
  assert.equal(res.reason, "aborted");
  assert.equal(calls.length, 1);
});

test("@全体 greeting: every member greets once, briefly, without tools", async () => {
  const { calls, runMember } = scriptedMembers((task) => `${task.agent.name}：你好`);
  const res = await runCollaboration({ roster: TEAM, history: [user("@全体 大家好")], runMember });
  assert.equal(res.plan.mode, "chat");
  assert.equal(calls.length, 3);
  assert.ok(calls.every((c) => c.brief && !c.tools && c.role === "greeter"));
});

test("plan notice reads like a short collaboration card", () => {
  const plan = parsePlan(
    JSON.stringify({
      mode: "pipeline",
      lead: "架构师",
      steps: [{ id: "s1", member: "架构师", task: "设计" }, { id: "s2", member: "码川", task: "实现", depends_on: ["s1"] }],
      review: { member: "验测" },
    }),
    ctx("做登录"),
  )!;
  assert.equal(formatPlanNotice(plan, TEAM), "协作 · 接力流水线｜架构师：设计 → 码川：实现｜验测 复核");
});

test("the planner is told which connected apps exist so those steps get tools", () => {
  const prompt = buildPlannerPrompt({
    ...ctx("看看 GitHub 上 a/b 仓库最新的 bug"),
    history: [user("看看 GitHub 上 a/b 仓库最新的 bug")],
    capabilities: "- GitHub：搜索 Issue / PR；创建 Issue（需用户确认）",
  });
  assert.match(prompt, /已连接的外部应用[\s\S]*tools: true[\s\S]*- GitHub：搜索 Issue/);
  const without = buildPlannerPrompt({ ...ctx("你好"), history: [user("你好")] });
  assert.doesNotMatch(without, /已连接的外部应用/);
});
