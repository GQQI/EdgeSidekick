import assert from "node:assert/strict";
import test from "node:test";
import {
  applyHarnessOutcome,
  diagnoseHarness,
  planCollaboration,
  taskSignature,
  type CollaborationPlan,
  type HarnessAgent,
} from "./src/social/harness.ts";

const roster: HarnessAgent[] = [
  { id: "lead", name: "小狼", title: "编排助手", duty: "理解用户目标，协调成员，汇总结论与下一步。" },
  { id: "researcher", name: "阿查", title: "研究员", duty: "检索与整理信息，给出有依据的简短结论。" },
  { id: "engineer", name: "工己", title: "工程师", duty: "把方案落到实现层，标清风险与工期。" },
  { id: "reviewer", name: "审之", title: "审稿人", duty: "审查假设与漏洞，提供改进建议。" },
  { id: "writer", name: "墨白", title: "写手", duty: "把结论写清楚，适合非专业读者。" },
  { id: "coder", name: "码川", title: "程序员", duty: "在当前工作区里实现功能：先读相关文件，再修改，并用命令验证结果。" },
  { id: "debugger", name: "排错", title: "调试", duty: "根据报错和复现步骤定位缺陷，做最小修改，并重新跑一遍确认修好。" },
  { id: "tester", name: "验测", title: "测试", duty: "为改动补上能失败也能通过的检查，运行它们，并指出仍然失败的地方。" },
  { id: "codereview", name: "审码", title: "代码审查", duty: "审查当前改动的正确性、遗漏和风险，只在用户要求时才亲自修改。" },
];

function owner(plan: CollaborationPlan | null) {
  return plan?.assignments.find((item) => item.role === "owner" || item.role === "worker");
}

test("a bugfix goes to the debugger, not the first roster name", () => {
  const plan = planCollaboration("帮我修复登录页的空指针", roster, { collab: true });
  assert.equal(plan?.topology, "solo");
  assert.equal(plan?.phase, "work");
  assert.equal(owner(plan)?.agentId, "debugger");
});

test("an implementation goes to the programmer", () => {
  const plan = planCollaboration("帮我实现导出 CSV", roster, { collab: true });
  assert.equal(plan?.topology, "solo");
  assert.equal(owner(plan)?.agentId, "coder");
});

test("an explicit mention stays with that person", () => {
  const plan = planCollaboration("@码川 帮我改一下按钮", roster, {
    collab: true,
    mentionedIds: new Set(["coder"]),
  });
  assert.equal(plan?.topology, "solo");
  assert.equal(owner(plan)?.agentId, "coder");
});

test("independent lookups run in parallel and then get checked", () => {
  const plan = planCollaboration("分别查一下竞品价格，同时查一下用户评价", roster, { collab: true });
  assert.equal(plan?.topology, "parallel");
  const workers = plan?.assignments.filter((item) => item.role === "worker") || [];
  assert.equal(workers.length >= 2, true);
  assert.equal(new Set(workers.map((item) => item.agentId)).size, workers.length);
  assert.equal(plan?.assignments.some((item) => item.role === "verifier"), true);
});

test("implementation steps stay with the first agent until others are asked", () => {
  const plan = planCollaboration(
    "删除当前的目录中的内容，然后写入一个登录页面，并打开验证效果，然后其他的智能体看看这个前端写的咋样，并继续优化",
    roster,
    { collab: true },
  );
  assert.equal(plan?.topology, "sequential");
  const workers = plan?.assignments.filter((item) => item.role === "worker") || [];
  assert.equal(workers.length, 2);
  assert.equal(workers[0]?.agentId, "coder");
  assert.match(workers[0]?.task || "", /删除/);
  assert.match(workers[0]?.task || "", /登录页面/);
  assert.equal(workers.some((item) => item.agentId === "debugger"), false);
  assert.match(workers[1]?.task || "", /看看|优化/);
});

test("a pipeline is ordered and ends with the tester when the last step is a test", () => {
  const plan = planCollaboration("先改登录校验，然后再跑测试", roster, { collab: true });
  assert.equal(plan?.topology, "sequential");
  const workers = plan?.assignments.filter((item) => item.role === "worker") || [];
  assert.equal(workers.length, 2);
  assert.notEqual(workers[0]?.agentId, "lead");
  assert.equal(workers[1]?.agentId, "tester");
});

test("turning collab off keeps a splittable request on one agent", () => {
  const plan = planCollaboration("分别查一下竞品价格，同时查一下用户评价", roster, { collab: false });
  assert.equal(plan?.topology, "solo");
});

test("discussions about different topics keep separate harnesses", () => {
  const prompt = "请大家一起讨论一下上面的问题，并给出可执行建议。";
  const code = planCollaboration(prompt, roster, { collab: true, topicText: "帮我实现导出 CSV" });
  const write = planCollaboration(prompt, roster, { collab: true, topicText: "帮我写一篇发布说明" });
  assert.equal(code?.phase, "talk");
  assert.notEqual(code?.signature, write?.signature);
  assert.match(code?.assignments[0]?.task || "", /导出 CSV/);
});

test("a discussion does not invite the whole roster", () => {
  const plan = planCollaboration("请大家一起讨论一下上面的问题，并给出可执行建议。", roster, { collab: true });
  assert.equal(plan?.phase, "talk");
  assert.equal(plan?.assignments.length <= 3, true);
  assert.equal(plan?.assignments.length >= 2, true);
});

test("the same wording keeps one signature", () => {
  assert.equal(taskSignature("帮我修复登录页的空指针"), taskSignature("请修复登录页空指针"));
});

test("a failed solo run proposes another topology, and two wins lock it", () => {
  const plan = planCollaboration("帮我实现导出 CSV", roster, { collab: true });
  assert.ok(plan);
  const failed = diagnoseHarness({
    plan: plan!,
    openTasks: 1,
    agentResults: [{
      agentId: "coder",
      role: "owner",
      texts: ["我来处理"],
      goalSatisfied: null,
      goalBlocked: false,
      toolErrors: 0,
    }],
  });
  assert.equal(failed.success, false);
  let book = applyHarnessOutcome(undefined, plan!, failed);
  const memory = book[plan!.signature];
  assert.equal(memory.stable, false);
  assert.ok(memory.pending);
  assert.equal(memory.failedOwnerIds.includes("coder"), true);

  const again = planCollaboration("帮我实现导出 CSV", roster, { collab: true, playbook: book });
  assert.equal(again?.topology, memory.pending);
  assert.equal(again?.adjusted, true);

  const delivered = diagnoseHarness({
    plan: again!,
    openTasks: 0,
    agentResults: [{
      agentId: again!.fallbackOwnerId,
      role: "worker",
      texts: ["已经加上导出 CSV，命令跑通了，结果文件在 exports/users.csv。"],
      goalSatisfied: true,
      goalBlocked: false,
      toolErrors: 0,
    }],
  });
  assert.equal(delivered.success, true);
  book = applyHarnessOutcome(book, again!, delivered);
  assert.equal(book[plan!.signature].stable, false);
  book = applyHarnessOutcome(book, { ...again!, topology: again!.topology }, delivered);
  assert.equal(book[plan!.signature].stable, true);
  assert.equal(book[plan!.signature].best, again!.topology);

  const locked = planCollaboration("请实现导出 CSV", roster, { collab: true, playbook: book });
  assert.equal(locked?.topology, again!.topology);
});

test("a multi-agent miss that a single agent then finishes is remembered as solo", () => {
  const plan = planCollaboration("分别查一下竞品价格，同时查一下用户评价", roster, { collab: true });
  assert.equal(plan?.topology, "parallel");
  const missed = diagnoseHarness({
    plan: plan!,
    openTasks: 1,
    agentResults: plan!.assignments.map((item) => ({
      agentId: item.agentId,
      role: item.role,
      texts: ["稍等，我来处理"],
      goalSatisfied: null,
      goalBlocked: false,
      toolErrors: 0,
    })),
  });
  assert.equal(missed.success, false);
  assert.ok(missed.innerRepair);
  const book = applyHarnessOutcome(undefined, plan!, {
    ...missed,
    success: true,
    failures: [],
  }, { repairedToSolo: true, priorFailures: missed.failures });
  const memory = book[plan!.signature];
  assert.equal(memory.best, "solo");
  assert.equal(memory.pending, null);
  const next = planCollaboration("分别查一下竞品价格，同时查一下用户评价", roster, { collab: true, playbook: book });
  assert.equal(next?.topology, "solo");
});
