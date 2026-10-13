import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import {
  addressedAgentIds,
  buildReplyDecisionPrompt,
  buildTurnIntentPrompt,
  fallbackTurnIntent,
  parseTurnIntent,
  resolveMember,
  defaultMaxAgentMessages,
  formatPipelineNotice,
  parseReplyDecision,
  runGroupConversation,
  type ChatAgent,
  type ChatLine,
  type JudgeInput,
  type LoopEvent,
  type ReplyInput,
} from "./src/social/groupLoop.ts";
import { buildSocialBackendMessage } from "./src/social/buildPrompt.ts";

const ABC: ChatAgent[] = [
  { id: "a", name: "阿甲", title: "研究员", duty: "检索资料" },
  { id: "b", name: "阿乙", title: "程序员", duty: "写代码" },
  { id: "c", name: "阿丙", title: "审稿人", duty: "审查漏洞" },
];

const CODE: ChatAgent[] = [
  { id: "coder", name: "码川", title: "程序员", duty: "在当前工作区里实现功能：先读相关文件，再修改，并用命令验证结果。" },
  { id: "debugger", name: "排错", title: "调试", duty: "根据报错和复现步骤定位缺陷，做最小修改，并重新跑一遍确认修好。" },
  { id: "tester", name: "验测", title: "测试", duty: "为改动补上能失败也能通过的检查，运行它们，并指出仍然失败的地方。" },
  { id: "codereview", name: "审码", title: "代码审查", duty: "审查当前改动的正确性、遗漏和风险，只在用户要求时才亲自修改。" },
];

const user = (text: string, id = "u1"): ChatLine => ({ id, role: "user", text });

/** Scripted judge: script[k] = ids that want to reply to the k-th message of this turn. */
function scripted(script: string[][]) {
  const seen: Array<{ trigger: string; agent: string; historyLen: number }> = [];
  let base = -1;
  const judge = async (input: JudgeInput) => {
    if (base < 0) base = input.history.length - 1;
    const k = input.history.length - 1 - base;
    seen.push({ trigger: input.trigger.id, agent: input.agent.id, historyLen: input.history.length });
    return (script[k] || []).includes(input.agent.id);
  };
  return { judge, seen };
}

function replier() {
  const calls: ReplyInput[] = [];
  const reply = async (input: ReplyInput) => {
    calls.push(input);
    return `${input.agent.name}#${calls.length}`;
  };
  return { reply, calls };
}

const TEAM: ChatAgent[] = [
  { id: "luna", name: "Luna", title: "designer", duty: "visual design, layouts and colour systems" },
  { id: "omar", name: "Omar", title: "copywriter", duty: "product copy, tone of voice and naming" },
  { id: "kai", name: "凯文", title: "数据分析", duty: "埋点、指标和 A/B 实验分析" },
];

const intentJson = (o: Record<string, unknown>) => JSON.stringify({ audience: "open", members: [], social: false, action: false, pipeline: null, ...o });

test("no pipeline by default: without the analyzer (or when it fails) there is never a pipeline", async () => {
  for (const text of ["先排错再审码", "排错 → 审码", "修好之后让其他成员看看", "按顺序来"]) {
    const intent = fallbackTurnIntent(user(text), CODE);
    assert.equal(intent.pipeline, null, text);
  }
  const { reply, calls } = replier();
  const res = await runGroupConversation({
    roster: CODE,
    history: [user("先排错再审码：看看这个函数")],
    judge: async () => null,
    reply,
    analyzeTurn: async () => { throw new Error("model down"); },
  });
  assert.ok(calls.every((c) => !c.step));
  assert.equal(res.messages.length, 1);
});

test("turn intent: a model pipeline with two real members is kept; one member or unknown names are not", () => {
  const text = "please have Omar draft the headline first, then Luna lays it out";
  const ok = parseTurnIntent(
    intentJson({ pipeline: { evidence: "Omar draft the headline first, then Luna lays it out", steps: [{ member: "Omar", task: "headline" }, { member: "@Luna", task: "layout" }] } }),
    TEAM,
    text,
  );
  assert.deepEqual(ok!.pipeline!.steps.map((s) => s.agentId), ["omar", "luna"]);
  assert.equal(formatPipelineNotice(ok!.pipeline!, TEAM, "zh"), "协作：按你的要求走流水线 · Omar → Luna");
  // The host keeps a second member the model assigned, even without a verbatim quote.
  const assigned = parseTurnIntent(
    intentJson({ pipeline: { steps: [{ member: "Omar", task: "draft" }, { member: "Luna", task: "layout" }] } }),
    TEAM,
    "make the landing page nicer",
  );
  assert.deepEqual(assigned!.pipeline!.steps.map((s) => s.agentId), ["omar", "luna"]);
  // One member / unknown members → no pipeline.
  const single = parseTurnIntent(intentJson({ pipeline: { evidence: "make", steps: [{ member: "Omar" }, { member: "Nobody" }] } }), TEAM, "make it");
  assert.equal(single!.pipeline, null);
  // Garbage → null (caller falls back to chat syntax).
  assert.equal(parseTurnIntent("I think everyone should answer", TEAM, "hi"), null);
});

test("turn intent: names resolve against whatever roster is configured; think tags and code fences are tolerated", () => {
  const parsed = parseTurnIntent(
    "<think>hmm</think>```json\n" + intentJson({ audience: "members", members: ["凯文（数据分析）", "Ghost"], lead: "kai", social: true, action: true }) + "\n```",
    TEAM,
    "凯文，帮我看下转化率",
  );
  assert.deepEqual(parsed!.memberIds, ["kai"]);
  assert.equal(parsed!.leadId, "kai");
  assert.equal(parsed!.social, true);
  assert.equal(parsed!.action, true);
  assert.equal(parsed!.source, "model");
  // "members" with no resolvable member degrades to open.
  assert.equal(parseTurnIntent(intentJson({ audience: "members", members: ["Ghost"] }), TEAM, "x")!.audience, "open");
  assert.equal(resolveMember("@Luna", TEAM)!.id, "luna");
  assert.equal(resolveMember("copywriter", TEAM)!.id, "omar");
});

test("intent prompt is built from the roster's own descriptions (works for any team)", () => {
  const prompt = buildTurnIntentPrompt({ roster: TEAM, history: [user("hello")], userText: "hello" });
  for (const a of TEAM) assert.match(prompt, new RegExp(a.name));
  assert.match(prompt, /visual design/);
  assert.match(prompt, /"pipeline":null/);
});

test("every new message triggers a decision by every OTHER member; the author never judges its own message", async () => {
  const { judge, seen } = scripted([["a"], ["b"], []]);
  const { reply } = replier();
  const res = await runGroupConversation({ roster: ABC, history: [user("这个方案怎么样")], judge, reply });
  const byTrigger = new Map<string, string[]>();
  for (const row of seen) byTrigger.set(row.trigger, [...(byTrigger.get(row.trigger) || []), row.agent]);
  assert.deepEqual([...byTrigger.get("u1")!].sort(), ["a", "b", "c"]);
  const [m1, m2] = res.messages;
  assert.deepEqual([...byTrigger.get(m1.id)!].sort(), ["b", "c"]); // a wrote m1
  assert.deepEqual([...byTrigger.get(m2.id)!].sort(), ["a", "c"]); // b wrote m2
  assert.equal(res.reason, "quiet");
});

test("the A/B/C example: A,B reply; B still replies after A; C joins after B; A answers C; then quiet", async () => {
  // msg0 user → A,B want; A speaks.  msg1 (A) → B wants, C not; B speaks.
  // msg2 (B) → C wants, A not; C speaks. msg3 (C) → A wants; A speaks. msg4 (A) → nobody.
  const { judge } = scripted([["a", "b"], ["b"], ["c"], ["a"], []]);
  const { reply, calls } = replier();
  const events: LoopEvent[] = [];
  const res = await runGroupConversation({
    roster: ABC,
    history: [user("A、B、C 怎么看这个设计？")],
    judge,
    reply,
    onEvent: (e) => events.push(e),
  });
  assert.deepEqual(res.messages.map((m) => m.agentId), ["a", "b", "c", "a"]);
  assert.equal(res.reason, "quiet");
  // Later speakers saw the earlier replies.
  assert.deepEqual(calls[1].history.map((l) => l.agentId || "user"), ["user", "a"]);
  assert.deepEqual(calls[2].history.map((l) => l.agentId || "user"), ["user", "a", "b"]);
  assert.deepEqual(calls[3].history.map((l) => l.agentId || "user"), ["user", "a", "b", "c"]);
  // Nobody ever replies to its own message.
  for (let i = 1; i < res.messages.length; i += 1) {
    assert.notEqual(res.messages[i].agentId, res.messages[i - 1].agentId);
  }
  const decisions = events.filter((e) => e.type === "decisions");
  assert.equal(decisions.length, 5);
  assert.ok(decisions.every((e) => e.type === "decisions" && e.decisions.every((d) => d.reason !== "pipeline")));
});

test("termination: when a message gets no volunteer the round ends", async () => {
  const { judge } = scripted([["b"], []]);
  const { reply } = replier();
  const res = await runGroupConversation({ roster: ABC, history: [user("问个问题")], judge, reply });
  assert.equal(res.messages.length, 1);
  assert.equal(res.reason, "quiet");
});

test("a user question still gets the closest member when everyone declines or the decision call fails", async () => {
  const { judge } = scripted([[], []]);
  const { reply } = replier();
  const declined = await runGroupConversation({
    roster: CODE,
    history: [user("登录页报错了，空指针异常，怎么修复缺陷")],
    judge,
    reply,
  });
  assert.equal(declined.messages.length, 1);
  assert.equal(declined.messages[0].agentId, "debugger");
  assert.equal(declined.reason, "quiet");

  const events: LoopEvent[] = [];
  const failed = await runGroupConversation({
    roster: CODE,
    history: [user("登录页报错了，空指针异常，怎么修复缺陷")],
    judge: async () => null,
    reply,
    onEvent: (e) => events.push(e),
  });
  assert.equal(failed.messages.length, 1);
  assert.equal(failed.messages[0].agentId, "debugger");
  const first = events.find((e) => e.type === "decisions");
  assert.ok(first && first.type === "decisions" && first.decisions.some((d) => d.reason === "fallback"));
});

test("max-message guard: endless volunteers stop at the cap, per-member cap holds", async () => {
  const judge = async () => true;
  const { reply } = replier();
  const res = await runGroupConversation({
    roster: ABC,
    history: [user("聊聊")],
    judge,
    reply,
    maxAgentMessages: 5,
    maxRepliesPerAgent: 2,
  });
  assert.equal(res.reason, "cap");
  assert.equal(res.messages.length, 5);
  const counts = new Map<string, number>();
  for (const m of res.messages) counts.set(m.agentId!, (counts.get(m.agentId!) || 0) + 1);
  assert.ok([...counts.values()].every((n) => n <= 2));
  for (let i = 1; i < res.messages.length; i += 1) {
    assert.notEqual(res.messages[i].agentId, res.messages[i - 1].agentId);
  }
  assert.equal(defaultMaxAgentMessages(4), 8);
  assert.equal(defaultMaxAgentMessages(1), 3);
  assert.equal(defaultMaxAgentMessages(9), 10);
});

test("per-member cap also ends the round once everybody used its budget", async () => {
  const judge = async () => true;
  const { reply } = replier();
  const res = await runGroupConversation({
    roster: ABC.slice(0, 2),
    history: [user("聊聊")],
    judge,
    reply,
    maxAgentMessages: 50,
    maxRepliesPerAgent: 2,
  });
  assert.equal(res.messages.length, 4);
  assert.equal(res.reason, "quiet");
});

test("a member named by the user is the only one asked; others join only after that reply", async () => {
  const judgedOnUser: string[] = [];
  const judge = async (input: JudgeInput) => {
    if (input.trigger.role === "user") judgedOnUser.push(input.agent.id);
    if (input.addressed) return true;
    return input.agent.id === "a" && input.trigger.agentId === "c";
  };
  const { reply } = replier();
  const events: LoopEvent[] = [];
  const res = await runGroupConversation({
    roster: ABC,
    history: [user("@阿丙 你觉得这个方案有什么漏洞？")],
    judge,
    reply,
    onEvent: (e) => events.push(e),
  });
  assert.deepEqual(judgedOnUser, ["c"]);
  assert.equal(res.messages[0].agentId, "c");
  const first = events.find((e) => e.type === "decisions");
  assert.ok(first && first.type === "decisions" && first.decisions.length === 1);
  assert.deepEqual(res.messages.map((m) => m.agentId), ["c", "a"]);
});

test("a volunteer that waited longer goes before a newer one", async () => {
  // user → a,b want; a speaks. after a → b,c want: b waited longer → b first.
  const { judge } = scripted([["a", "b"], ["b", "c"], ["c"], []]);
  const { reply } = replier();
  const res = await runGroupConversation({ roster: ABC, history: [user("讨论一下")], judge, reply });
  assert.deepEqual(res.messages.map((m) => m.agentId), ["a", "b", "c"]);
});

test("a member who decides to speak but writes nothing is skipped; the next volunteer speaks", async () => {
  const { judge } = scripted([["a", "b"], []]);
  const reply = async (input: ReplyInput) => (input.agent.id === "a" ? "" : "我来补充一点");
  const res = await runGroupConversation({ roster: ABC, history: [user("说说")], judge, reply });
  assert.deepEqual(res.messages.map((m) => m.agentId), ["b"]);
});

test("a failed decision call means silence unless the member was addressed", async () => {
  const judge = async (input: JudgeInput) => (input.agent.id === "b" ? null : false);
  const { reply } = replier();
  const res = await runGroupConversation({
    roster: ABC,
    history: [user("阿乙，你看下这个报错")],
    judge,
    reply,
  });
  // 阿乙 is addressed by the user → answers directly (no judge call needed).
  assert.equal(res.messages[0].agentId, "b");
});

test("stop / new user message: an aborted signal ends the round without more replies", async () => {
  const ac = new AbortController();
  const judge = async () => true;
  let n = 0;
  const reply = async () => {
    n += 1;
    if (n === 2) ac.abort();
    return `r${n}`;
  };
  const res = await runGroupConversation({ roster: ABC, history: [user("go")], judge, reply, signal: ac.signal });
  assert.equal(res.reason, "aborted");
  assert.ok(n <= 2);
});

test("shouldStop (member asks the user) ends the round", async () => {
  const judge = async () => true;
  const { reply } = replier();
  const res = await runGroupConversation({
    roster: ABC,
    history: [user("go")],
    judge,
    reply,
    shouldStop: () => true,
  });
  assert.equal(res.messages.length, 1);
  assert.equal(res.reason, "aborted");
});

test("explicit pipeline (from the analyzer): steps run in the requested order, then the free loop resumes", async () => {
  const text = "先排错再审码";
  const judged: string[] = [];
  const judge = async (input: JudgeInput) => {
    judged.push(`${input.trigger.agentId || "user"}>${input.agent.id}`);
    return false;
  };
  const { reply, calls } = replier();
  const events: LoopEvent[] = [];
  const res = await runGroupConversation({
    roster: CODE,
    history: [user(text)],
    judge,
    reply,
    onEvent: (e) => events.push(e),
    analyzeTurn: async () =>
      intentJson({ pipeline: { evidence: "先排错再审码", steps: [{ member: "排错", task: "定位" }, { member: "审码", task: "审查" }] } }),
  });
  assert.deepEqual(res.messages.map((m) => m.agentId), ["debugger", "codereview"]);
  assert.equal(calls[0].step?.agentId, "debugger");
  assert.equal(calls[1].step?.agentId, "codereview");
  assert.deepEqual(calls[1].history.map((l) => l.agentId || "user"), ["user", "debugger"]);
  assert.ok(events.some((e) => e.type === "intent" && e.intent.pipeline));
  // After the last step, the others decide on 审码's message.
  assert.deepEqual(judged.sort(), ["codereview>coder", "codereview>debugger", "codereview>tester"]);
  assert.equal(res.reason, "quiet");
});

test("addressedAgentIds ignores the author and finds vocatives", () => {
  const line: ChatLine = { id: "x", role: "agent", agentId: "a", agentName: "阿甲", text: "阿乙，你来确认一下；@阿甲 不算" };
  const { ids } = addressedAgentIds(line, ABC);
  assert.deepEqual([...ids], ["b"]);
});

test("decision prompt shows the full history incl. earlier member replies and defaults to silence", () => {
  const history: ChatLine[] = [
    user("这个接口为什么超时？"),
    { id: "m1", role: "agent", agentId: "a", agentName: "阿甲", text: "可能是数据库慢查询。" },
  ];
  const prompt = buildReplyDecisionPrompt({
    agent: ABC[2],
    roster: ABC,
    history,
    trigger: history[1],
    addressed: false,
    addressedNames: [],
    repliesSoFar: 0,
  });
  assert.match(prompt, /这个接口为什么超时/);
  assert.match(prompt, /最新消息【阿甲】/);
  assert.match(prompt, /沉默是默认选项/);
  assert.match(prompt, /reply 或 silent/);
  assert.doesNotMatch(prompt, /流水线·|pipeline ·/);
  assert.equal(parseReplyDecision("reply"), true);
  assert.equal(parseReplyDecision("<think>maybe reply</think>silent"), false);
  assert.equal(parseReplyDecision(""), false);
  assert.equal(parseReplyDecision("Silent."), false);
});

test("reply prompt for free chat says no fixed order / no pipeline and carries the latest message", () => {
  const msg = buildSocialBackendMessage({
    userText: "这个接口为什么超时？",
    targets: [ABC[2] as any],
    history: [{ id: "u1", role: "user", text: "这个接口为什么超时？", at: 1 }] as any,
    roster: ABC as any,
    mode: "group",
    freeChat: { latest: { author: "阿甲", isUser: false, text: "可能是数据库慢查询。" } },
  });
  assert.match(msg, /自由群聊/);
  assert.match(msg, /没有流水线/);
  assert.match(msg, /【最新消息·智能体·阿甲】/);
  assert.match(msg, /本轮用户的原始消息/);
  const step = buildSocialBackendMessage({
    userText: "先排错再审码",
    targets: [CODE[1] as any],
    history: [] as any,
    roster: CODE as any,
    mode: "group",
    freeChat: {
      latest: { author: "用户", isUser: true, text: "先排错再审码" },
      pipelineStep: { index: 1, total: 2, task: "排错", nextName: "审码" },
    },
  });
  assert.match(step, /按已经排好的顺序协作/);
  assert.match(step, /下一步由「审码」/);
});

test("group messages are driven by the collaboration orchestrator", () => {
  const send = readFileSync(new URL("./src/social/socialSend.ts", import.meta.url), "utf8");
  assert.match(send, /const freeGroupMode: boolean = view\.kind === "group";/);
  assert.match(send, /if \(freeGroupMode\) \{\s*try \{\s*await runFreeGroupLoop\(\);/);
  const loop = readFileSync(new URL("./src/social/socialSendGroupLoop.ts", import.meta.url), "utf8");
  assert.match(loop, /runCollaboration\(/);
  assert.doesNotMatch(loop, /runGroupConversation\(/);
  // Each member gets its own brief on the slot, not a shared captured variable.
  assert.match(loop, /s\._freeReply = \{/);
});

test("a direct message is answered without the group silence judge", () => {
  const send = readFileSync(new URL("./src/social/socialSend.ts", import.meta.url), "utf8");
  const call = send.slice(send.indexOf("await runSocialSendWaves("));
  assert.match(call.slice(0, 2200), /\braw\b/);
  const waves = readFileSync(new URL("./src/social/socialSendWaves.ts", import.meta.url), "utf8");
  assert.match(waves, /raw,\n/);
  assert.match(waves, /if \(view\.kind === "dm"\) \{\s*for \(const s of slots\) \{\s*if \(ac\.signal\.aborted \|\| turnState\.turnPaused\) break;\s*\(s as \{ _forceSpeak\?: boolean \}\)\._forceSpeak = true;/);
});

test("a finished or failed group turn clears the status pill and the busy flag", () => {
  const send = readFileSync(new URL("./src/social/socialSend.ts", import.meta.url), "utf8");
  // "N 位成员已看到消息" must not come back after a reply is already on screen.
  assert.match(send, /else if \(!replied\) statusLabel = turnState\.status \|\| freeStatus/);
  assert.match(send, /if \(!rows\.some\(\(row\) => row\.readingStatus\)\) return prev/);
  assert.match(send, /\} finally \{\s*if \(!isStaleSend\(\)\) \{\s*turnState\.ended = true;/);
  const loop = readFileSync(new URL("./src/social/socialSendGroupLoop.ts", import.meta.url), "utf8");
  assert.match(loop, /turnState\.ended = true;\s*turnState\.status = "";/);
});

test("audience=everyone (from the analyzer): each member answers once even if the judge stays silent", async () => {
  const { reply, calls } = replier();
  const res = await runGroupConversation({
    roster: TEAM,
    history: [user("hey team")],
    judge: async () => false,
    reply,
    analyzeTurn: async () => intentJson({ audience: "everyone", social: true }),
  });
  assert.equal(res.messages.length, 3);
  assert.equal(new Set(res.messages.map((m) => m.agentId)).size, 3);
  assert.ok(calls.every((c) => c.brief === true && c.mustAnswer === true && c.action !== true));
  assert.equal(res.reason, "quiet");
});

test("audience=group: nobody is forced; members judge with the group-question fact", async () => {
  const facts: boolean[] = [];
  const judge = async (input: JudgeInput) => {
    facts.push(input.groupQuestion);
    return input.agent.id !== "omar";
  };
  const { reply } = replier();
  const res = await runGroupConversation({
    roster: TEAM,
    history: [user("do you agree that dark mode should be the default?")],
    judge,
    reply,
    maxRepliesPerAgent: 1,
    analyzeTurn: async () => intentJson({ audience: "group" }),
  });
  assert.ok(facts.every(Boolean));
  assert.deepEqual(res.messages.map((m) => m.agentId).sort(), ["kai", "luna"]);
});

test("explicit @mention / vocative is kept even if the analyzer missed it", async () => {
  const asked: string[] = [];
  const judge = async (input: JudgeInput) => {
    if (input.trigger.role === "user") asked.push(input.agent.id);
    return null;
  };
  const { reply } = replier();
  const res = await runGroupConversation({
    roster: TEAM,
    history: [user("@Omar what do you think of this headline?")],
    judge,
    reply,
    analyzeTurn: async () => intentJson({ audience: "open", lead: "Luna" }),
  });
  assert.deepEqual(asked, ["omar"]);
  assert.deepEqual(res.messages.map((m) => m.agentId), ["omar"]);
});

test("two members named by the user both answer that message; the third is not asked", async () => {
  const asked: string[] = [];
  const judge = async (input: JudgeInput) => {
    if (input.trigger.role === "user") asked.push(input.agent.id);
    return input.trigger.role === "user";
  };
  const { reply } = replier();
  const res = await runGroupConversation({ roster: ABC, history: [user("@阿甲 @阿丙 你们怎么看？")], judge, reply });
  assert.deepEqual(asked.sort(), ["a", "c"]);
  assert.deepEqual(res.messages.map((m) => m.agentId), ["a", "c"]);
});

test("a colleague's direct question makes the named member reply", async () => {
  const judge = async (input: JudgeInput) => input.addressed;
  const reply = async (input: ReplyInput) => (input.agent.id === "a" ? "阿乙，你能确认一下这个接口的超时设置吗？" : "确认过了，是 30 秒。");
  const res = await runGroupConversation({ roster: ABC, history: [user("@阿甲 查下超时原因")], judge, reply });
  assert.deepEqual(res.messages.map((m) => m.agentId), ["a", "b"]);
});

test("among volunteers the analyzer's lead answers first; without it the profile overlap decides", async () => {
  const judge = async (input: JudgeInput) => input.trigger.role === "user";
  const { reply } = replier();
  const withLead = await runGroupConversation({
    roster: TEAM,
    history: [user("is this conversion drop real or noise?")],
    judge,
    reply,
    maxRepliesPerAgent: 1,
    analyzeTurn: async () => intentJson({ lead: "凯文" }),
  });
  assert.equal(withLead.messages[0].agentId, "kai");
  const noLead = await runGroupConversation({
    roster: TEAM,
    history: [user("which colour systems fit this layout?")],
    judge,
    reply: replier().reply,
    maxRepliesPerAgent: 1,
  });
  assert.equal(noLead.messages[0].agentId, "luna");
});

test("turn analyzer: one stricter retry when the first output is unparseable; empty does not retry", async () => {
  const prompts: string[] = [];
  const outputs = ["not json", intentJson({ audience: "everyone", social: true })];
  const events: LoopEvent[] = [];
  await runGroupConversation({
    roster: TEAM,
    history: [user("hey team")],
    judge: async () => false,
    reply: replier().reply,
    onEvent: (e) => events.push(e),
    analyzeTurn: async (prompt) => {
      prompts.push(prompt);
      return outputs.shift() ?? null;
    },
  });
  assert.equal(prompts.length, 2);
  assert.match(prompts[1], /直接输出 JSON/);
  const intent = (events.find((e) => e.type === "intent") as Extract<LoopEvent, { type: "intent" }>).intent;
  assert.equal(intent.source, "model");
  assert.equal(intent.audience, "everyone");

  let emptyCalls = 0;
  const emptyEvents: LoopEvent[] = [];
  await runGroupConversation({
    roster: TEAM,
    history: [user("hey team")],
    judge: async () => false,
    reply: replier().reply,
    onEvent: (e) => emptyEvents.push(e),
    analyzeTurn: async () => {
      emptyCalls += 1;
      return "";
    },
  });
  assert.equal(emptyCalls, 1);
  const emptyIntent = (emptyEvents.find((e) => e.type === "intent") as Extract<LoopEvent, { type: "intent" }>).intent;
  assert.equal(emptyIntent.source, "fallback");
  assert.equal(emptyIntent.fallbackReason, "empty");

  const events2: LoopEvent[] = [];
  await runGroupConversation({
    roster: TEAM,
    history: [user("hey team")],
    judge: async () => false,
    reply: replier().reply,
    onEvent: (e) => events2.push(e),
    analyzeTurn: async () => "sorry, no idea",
  });
  const fb = (events2.find((e) => e.type === "intent") as Extract<LoopEvent, { type: "intent" }>).intent;
  assert.equal(fb.source, "fallback");
  assert.equal(fb.fallbackReason, "unparseable");
  assert.equal(fb.pipeline, null);
});

/**
 * Ten kinds of group turn. The member model is a test double: it only looks
 * at who was selected and at the analyzer JSON. The questions themselves are
 * not special cases in the host.
 */
function answerPrompt(roster: ChatAgent[], input: ReplyInput, userText: string, stepTotal = 1, nextName?: string): string {
  const step = input.step;
  const done = input.history.filter((line) => line.role === "agent").length;
  return buildSocialBackendMessage({
    userText,
    targets: [input.agent as ChatAgent],
    history: input.history.slice(0, -1).map((line) => ({
      id: line.id,
      role: line.role,
      text: line.text,
      agentId: line.agentId,
      agentName: line.agentName,
      at: 1,
    })) as never,
    roster: roster as never,
    mode: "group",
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
      action: input.action,
      pipelineStep: step
        ? { index: done + 1, total: stepTotal, task: step.task, nextName }
        : undefined,
    },
  });
}

const TEN = [
  "你好",
  "大家好",
  "@排错 这个报错怎么看",
  "登录页为什么是白屏？",
  "删除当前目录里的内容，然后写入一个登录页面，并打开验证效果",
  "先把登录页写好，然后其他智能体看看这个前端并继续优化",
  "你们觉得这个交互顺不顺？",
  "这个方案还有什么遗漏？",
  "请排错先定位这个问题，然后审码再看一遍风险",
  "删除目录，然后写入登录页",
];

test("the host does not contain the ten sample questions", () => {
  const src = readFileSync(new URL("./src/social/groupLoop.ts", import.meta.url), "utf8");
  for (const question of TEN) assert.equal(src.includes(question), false, question);
});

test("1 greeting: everyone answers once, and the reply is a short greeting", async () => {
  const { reply, calls } = replier();
  const res = await runGroupConversation({
    roster: CODE,
    history: [user(TEN[0])],
    analyzeTurn: async () => intentJson({ audience: "everyone", social: true, lead: "码川" }),
    judge: async () => false,
    reply,
  });
  assert.equal(res.messages.length, CODE.length);
  assert.equal(new Set(res.messages.map((m) => m.agentId)).size, CODE.length);
  assert.equal(res.reason, "quiet");
  assert.ok(calls.every((call) => call.brief === true && call.action !== true && !call.step));
  const prompt = answerPrompt(CODE, calls[0], TEN[0]);
  assert.match(prompt, /寒暄|客套/);
  assert.doesNotMatch(prompt, /整件事都由你完成|你负责第/);
});

test("2 group hello is the same kind of turn: members decide, the host does not pick a solo greeter", async () => {
  const { calls } = replier();
  const res = await runGroupConversation({
    roster: CODE,
    history: [user(TEN[1])],
    analyzeTurn: async () => intentJson({ audience: "everyone", social: true }),
    judge: async (input) => input.repliesSoFar === 0,
    reply: async (input) => {
      calls.push(input);
      return `${input.agent.name}你好`;
    },
  });
  assert.equal(res.messages.length, CODE.length);
  assert.ok(calls.every((call) => call.brief === true && !call.step));
});

test("3 naming one member asks only that member to answer, not to take over the work", async () => {
  const asked: string[] = [];
  const { reply, calls } = replier();
  const res = await runGroupConversation({
    roster: CODE,
    history: [user(TEN[2])],
    analyzeTurn: async () => intentJson({ audience: "open", lead: "码川", action: false }),
    judge: async (input) => {
      if (input.trigger.role === "user") asked.push(input.agent.id);
      return input.addressed;
    },
    reply,
  });
  assert.deepEqual(asked, ["debugger"]);
  assert.deepEqual(res.messages.map((m) => m.agentId), ["debugger"]);
  assert.equal(calls[0].action, false);
  const prompt = answerPrompt(CODE, calls[0], TEN[2]);
  assert.match(prompt, /报错/);
  assert.match(prompt, /点了你的名|直接回应/);
  assert.doesNotMatch(prompt, /整件事都由你完成|你负责第/);
});

test("4 an open question still gets the fitting member when everyone else declines", async () => {
  const events: LoopEvent[] = [];
  const { reply, calls } = replier();
  const res = await runGroupConversation({
    roster: CODE,
    history: [user(TEN[3])],
    analyzeTurn: async () => intentJson({ audience: "open", lead: "排错", action: false }),
    judge: async () => false,
    reply,
    onEvent: (e) => events.push(e),
  });
  assert.deepEqual(res.messages.map((m) => m.agentId), ["debugger"]);
  assert.equal(res.reason, "quiet");
  const first = events.find((e) => e.type === "decisions");
  assert.ok(first && first.type === "decisions" && first.decisions.some((d) => d.agentId === "debugger" && d.reason === "fallback"));
  assert.equal(calls[0].action, false);
  const prompt = answerPrompt(CODE, calls[0], TEN[3]);
  assert.match(prompt, /白屏/);
  assert.match(prompt, /需要你回答|直接/);
  assert.doesNotMatch(prompt, /整件事都由你完成|你负责第/);
});

test("5 implementation steps joined together stay with the one lead", async () => {
  const { reply, calls } = replier();
  const text = TEN[4];
  const res = await runGroupConversation({
    roster: CODE,
    history: [user(text)],
    analyzeTurn: async () => intentJson({ audience: "open", lead: "码川", action: true, pipeline: null }),
    judge: async (input) => input.trigger.role === "user" && input.agent.id === "coder",
    reply,
  });
  assert.deepEqual(res.messages.map((m) => m.agentId), ["coder"]);
  assert.equal(calls[0].step, undefined);
  assert.equal(calls[0].action, true);
  assert.equal(calls[0].trigger.text, text);
  const prompt = answerPrompt(CODE, calls[0], text);
  assert.match(prompt, /整件事都由你完成/);
  assert.match(prompt, /删除/);
  assert.match(prompt, /登录页面/);
  assert.match(prompt, /验证/);
  assert.doesNotMatch(prompt, /你负责第|下一步由/);
});

test("6 writing stays with the author, then the reviewer optimizes", async () => {
  const text = TEN[5];
  const { reply, calls } = replier();
  const res = await runGroupConversation({
    roster: CODE,
    history: [user(text)],
    analyzeTurn: async () =>
      intentJson({
        audience: "open",
        lead: "码川",
        action: true,
        pipeline: {
          steps: [
            { member: "码川", task: "把登录页写好" },
            { member: "审码", task: "看看这个前端并继续优化" },
          ],
        },
      }),
    judge: async () => false,
    reply,
  });
  assert.deepEqual(res.messages.map((m) => m.agentId), ["coder", "codereview"]);
  assert.ok(!res.messages.some((m) => m.agentId === "debugger" || m.agentId === "tester"));
  assert.equal(calls[0].step?.task, "把登录页写好");
  assert.equal(calls[1].step?.task, "看看这个前端并继续优化");
  assert.ok(calls[1].history.some((line) => line.agentId === "coder"));
  const writing = answerPrompt(CODE, calls[0], text, 2, "审码");
  const review = answerPrompt(CODE, calls[1], text, 2);
  assert.match(writing, /把登录页写好/);
  assert.match(writing, /不要再拆给别人/);
  assert.match(writing, /下一步由「审码」/);
  assert.match(review, /继续优化/);
  assert.match(review, /不要重做上游/);
  assert.match(review, /码川/);
});

test("7 asking the group lets whoever wants to speak, and does not force the rest", async () => {
  const res = await runGroupConversation({
    roster: TEAM,
    history: [user(TEN[6])],
    analyzeTurn: async () => intentJson({ audience: "group" }),
    judge: async (input) => input.groupQuestion && input.agent.id !== "omar" && input.repliesSoFar === 0,
    reply: replier().reply,
  });
  assert.deepEqual(res.messages.map((m) => m.agentId).sort(), ["kai", "luna"]);
  assert.equal(res.reason, "quiet");
});

test("8 after two replies a third member can change its mind, then the turn ends", async () => {
  const { judge } = scripted([["a", "b"], ["b"], ["c"], []]);
  const res = await runGroupConversation({
    roster: ABC,
    history: [user(TEN[7])],
    judge,
    reply: replier().reply,
  });
  assert.deepEqual(res.messages.map((m) => m.agentId), ["a", "b", "c"]);
  assert.equal(res.reason, "quiet");
});

test("9 a named order is a pipeline only when the model quotes the user", async () => {
  const text = TEN[8];
  const { reply, calls } = replier();
  const res = await runGroupConversation({
    roster: CODE,
    history: [user(text)],
    analyzeTurn: async () =>
      intentJson({
        action: true,
        pipeline: {
          evidence: "请排错先定位这个问题，然后审码再看一遍风险",
          steps: [
            { member: "排错", task: "定位" },
            { member: "审码", task: "看风险" },
          ],
        },
      }),
    judge: async () => false,
    reply,
  });
  assert.deepEqual(res.messages.map((m) => m.agentId), ["debugger", "codereview"]);
  assert.equal(calls[0].step?.task, "定位");
  assert.equal(calls[1].step?.task, "看风险");
  assert.ok(!res.messages.some((m) => m.agentId === "tester" || m.agentId === "coder"));
});

test("10 a later test step the model assigns is kept and that member joins", async () => {
  const text = TEN[9];
  const invented = parseTurnIntent(
    intentJson({
      action: true,
      lead: "码川",
      pipeline: { steps: [{ member: "码川", task: "删除目录并写入登录页" }, { member: "验测", task: "打开页面验证" }] },
    }),
    CODE,
    text,
  );
  assert.deepEqual(invented?.pipeline?.steps.map((step) => step.agentId), ["coder", "tester"]);
  const { reply, calls } = replier();
  const res = await runGroupConversation({
    roster: CODE,
    history: [user(text)],
    intent: invented || undefined,
    judge: async () => false,
    reply,
  });
  assert.deepEqual(res.messages.map((m) => m.agentId), ["coder", "tester"]);
  assert.equal(calls[0].step?.task, "删除目录并写入登录页");
  assert.equal(calls[1].step?.task, "打开页面验证");
  assert.ok(calls[1].history.some((line) => line.agentId === "coder"));
  const build = answerPrompt(CODE, calls[0], text, 2, "验测");
  const check = answerPrompt(CODE, calls[1], text, 2);
  assert.match(build, /删除目录并写入登录页/);
  assert.match(build, /前后相连的内容都由你做完/);
  assert.match(build, /下一步由「验测」/);
  assert.match(check, /打开页面验证/);
  assert.match(check, /不要重做上游/);
  assert.doesNotMatch(check, /删除目录并写入登录页/);
});
