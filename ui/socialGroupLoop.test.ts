import assert from "node:assert/strict";
import test from "node:test";
import { createSocialGroupLoop } from "./src/social/socialSendGroupLoop.ts";

(globalThis as any).window ??= globalThis;

const ROSTER = [
  { id: "arch", name: "架构师", title: "架构", duty: "拆解需求，设计方案与接口" },
  { id: "dev", name: "码川", title: "程序员", duty: "在工作区里实现功能并运行验证" },
  { id: "qa", name: "验测", title: "测试", duty: "编写并运行测试，审查改动的正确性与风险" },
];

let seq = 0;
const uid = (p: string) => `${p}-${++seq}`;

function harness(opts: {
  text: string;
  planReply: string | Error;
  reply: (agentId: string, freeReply: any) => string | { error: string };
}) {
  const userMsg = { id: "u1", role: "user", text: opts.text, at: 1 };
  const committedMsgs: any[] = [];
  const harnessNotices: any[] = [];
  const turnState: any = { turnPaused: false, freeReply: null };
  const briefs: Array<{ agentId: string; freeReply: any }> = [];
  const slots = ROSTER.map((agent) => ({ id: uid("m"), agent, text: "", stage: "reading", streaming: false }));
  let plannerCalls = 0;
  const ac = new AbortController();
  const ctx = {
    ac,
    chatWorkspacePath: "",
    cleanDisplayText: (s: string) => s,
    commitGroupMessage: (slot: any, message: string) => {
      committedMsgs.push({ id: uid("c"), role: "agent", agentId: slot.agent.id, agentName: slot.agent.name, text: message });
      return true;
    },
    commitRound: (board: any) => board,
    committedMsgs,
    confirmationText: (_id: string, t: string) => t,
    getGroupBoard: () => null,
    groupId: "g1",
    harnessNotices,
    hist: [userMsg],
    isActiveAgentPhase: () => false,
    isStaleSend: () => false,
    liveBoard: null,
    noteAgentActivity: () => {},
    paint: () => {},
    patchStore: () => {},
    pauseForUserInput: () => false,
    raw: opts.text,
    rosterForPrompt: ROSTER,
    runOne: async (slot: any) => {
      briefs.push({ agentId: slot.agent.id, freeReply: slot._freeReply });
      const out = opts.reply(slot.agent.id, slot._freeReply);
      if (typeof out === "string") {
        slot.text = out;
        slot._error = "";
      } else {
        slot.text = "";
        slot._error = out.error;
      }
    },
    setAgentLive: () => {},
    setGroupBoard: (s: any) => s,
    slots,
    streamChat: async (_prompt: string, _sid: string, handlers: any) => {
      plannerCalls++;
      if (opts.planReply instanceof Error) {
        handlers.onError(opts.planReply.message);
        return;
      }
      handlers.onFinal(opts.planReply);
    },
    t: (k: string) => k,
    text: opts.text,
    uid,
    userMsg,
    visibleReplyAfterJudge: (s: string) => s.trim(),
    turnState,
  };
  return { run: createSocialGroupLoop(ctx), committedMsgs, harnessNotices, briefs, turnState, planner: () => plannerCalls };
}

test("a planned pipeline runs members in order and hands upstream output downstream", async () => {
  const h = harness({
    text: "帮我设计并实现一个登录接口",
    planReply: JSON.stringify({
      mode: "pipeline",
      lead: "arch",
      goal: "登录接口",
      steps: [
        { id: "s1", member: "架构师", task: "设计接口", expected: "接口定义" },
        { id: "s2", member: "码川", task: "实现接口", depends_on: ["s1"], tools: true },
      ],
      review: { member: "验测", focus: "正确性" },
      synthesize: false,
      reason: "先设计再实现",
    }),
    reply: (id, fr) => {
      if (id === "arch") return "POST /login {user, pass}";
      if (id === "dev") {
        assert.match(JSON.stringify(fr.collab.upstream), /POST \/login/);
        return "已实现 /login";
      }
      return "APPROVE\n没有问题";
    },
  });
  await h.run();
  assert.equal(h.planner(), 1);
  assert.deepEqual(h.briefs.map((b) => b.agentId), ["arch", "dev", "qa"]);
  assert.equal(h.briefs[0].freeReply.collab.role, "worker");
  assert.equal(h.briefs[0].freeReply.collab.downstream[0].name, "码川");
  assert.equal(h.briefs[1].freeReply.tools, true);
  assert.equal(h.briefs[2].freeReply.collab.role, "reviewer");
  const card = h.harnessNotices.find((n) => n.planCard);
  assert.ok(card, "plan card notice is shown");
  assert.equal(card.planCard.mode, "pipeline");
  assert.deepEqual(card.planCard.steps.map((s: any) => s.name), ["架构师", "码川"]);
  assert.equal(card.planCard.review, "验测");
  assert.deepEqual(h.committedMsgs.map((m) => m.text), ["POST /login {user, pass}", "已实现 /login", "复核通过：没有问题"]);
  assert.equal(h.turnState.status, "");
});

test("each parallel member carries its own brief on its slot", async () => {
  const h = harness({
    text: "大家分别评估一下这个方案",
    planReply: JSON.stringify({
      mode: "parallel",
      lead: "arch",
      goal: "评估方案",
      steps: [
        { id: "s1", member: "架构师", task: "评估架构风险" },
        { id: "s2", member: "验测", task: "评估测试成本" },
      ],
      review: null,
      synthesize: true,
      reason: "两个独立视角",
    }),
    reply: (id, fr) => (fr.collab.role === "synthesizer" ? "综合结论：可行" : `${id}: ${fr.collab.task}`),
  });
  await h.run();
  const tasks = h.briefs.filter((b) => b.freeReply.collab.role === "worker").map((b) => [b.agentId, b.freeReply.collab.task]);
  assert.deepEqual(tasks.sort(), [["arch", "评估架构风险"], ["qa", "评估测试成本"]]);
  assert.equal(h.briefs.at(-1)!.freeReply.collab.role, "synthesizer");
  assert.equal(h.committedMsgs.at(-1).text, "综合结论：可行");
});

test("planner failure still gets one member to answer", async () => {
  const h = harness({
    text: "测试用例应该怎么写？",
    planReply: new Error("upstream 500"),
    reply: () => "先列出边界条件。",
  });
  await h.run();
  assert.equal(h.briefs.length, 1);
  assert.equal(h.briefs[0].freeReply.mustAnswer, true);
  assert.deepEqual(h.committedMsgs.map((m) => m.text), ["先列出边界条件。"]);
});

test("when every member errors the user sees why instead of silence", async () => {
  const h = harness({
    text: "你好",
    planReply: JSON.stringify({ mode: "answer", lead: "arch", goal: "问候", steps: [{ id: "s1", member: "架构师", task: "回应" }], review: null, synthesize: false, reason: "" }),
    reply: () => ({ error: "401 invalid api key" }),
  });
  await h.run();
  assert.equal(h.committedMsgs.length, 0);
  const notice = h.harnessNotices.at(-1);
  assert.match(notice.text, /没有成员成功回复/);
  assert.match(notice.text, /401 invalid api key/);
});
