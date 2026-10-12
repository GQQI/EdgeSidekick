import assert from "node:assert/strict";
import test from "node:test";
import { applyStandingRules, cleanDisplayText, parseStandingRules } from "./src/social/sharedBoard.ts";
import { parseMentions } from "./src/social/store.ts";
import { buildSocialBackendMessage } from "./src/social/buildPrompt.ts";
import {
  buildSpeakJudgePrompt,
  isCapabilityQuestion,
  isDirectAnswerTurn,
  isExecutionRequested,
  isAuthorCallback,
  resolveAuthorId,
  isContinuationRequest,
  isScheduleRequest,
  parseSpeakVerdict,
  visibleReplyAfterJudge,
  pickSoloSpeaker,
  planReactionOrder,
  maxReactionTurns,
  isGroupGreeting,
  greetingFallback,
  groupTurnStatusLabel,
  agentAsksForUserInput,
  isNearDuplicateBubble,
  tightenMarkdownFlow,
  workNoteFromTool,
} from "./src/social/turnLogic.ts";

const roster = [
  { id: "a", name: "阿查", title: "研究员", duty: "检索资料" },
  { id: "b", name: "码川", title: "程序员", duty: "写代码" },
];

test("continue stays a follow-up instead of a new open-floor request", () => {
  assert.equal(isContinuationRequest("继续执行"), true);
  assert.equal(isContinuationRequest("请继续"), true);
  assert.equal(isContinuationRequest("帮我把这个目录整理一下"), false);
});

test("a plain question is not an execution fan-out", () => {
  assert.equal(isExecutionRequested("今天星期几"), false);
  assert.equal(isScheduleRequest("定时任务是什么？"), false);
  assert.equal(isExecutionRequested("你看一下刚才写的代码，有没有写的不好的地方"), false);
});

test("asking the author to change the result is execution, not a chat reply", () => {
  assert.equal(isAuthorCallback("谁写的谁来改"), true);
  assert.equal(isExecutionRequested("谁写的谁来改"), true);
  assert.equal(isExecutionRequested("把标题改成红色"), true);
  const author = resolveAuthorId(
    roster,
    [{ status: "done", ownerId: "b", text: "在当前目录中写一个主页" }],
    [],
    [{ role: "agent", agentId: "a", text: "我来看看有没有问题" }],
  );
  assert.equal(author, "b");
});

test("a name followed by 在吗 is that person, not whoever is free", () => {
  const hits = parseMentions("审码在吗", [
    { id: "c", name: "码川", title: "", duty: "", blurb: "", color: "", skills: [], tools: [] },
    { id: "r", name: "审码", title: "", duty: "", blurb: "", color: "", skills: [], tools: [] },
  ]);
  assert.deepEqual(hits.map((agent) => agent.name), ["审码"]);
});

test("setting a reminder is a schedule order for one executor", () => {
  assert.equal(isScheduleRequest("设置一个定时任务，每天下午3点提醒我开会"), true);
  assert.equal(isExecutionRequested("设置一个定时任务，每天下午3点提醒我开会"), true);
});

test("你好 and 大家好 ask every member, and the status counts the whole group", () => {
  const room = ["甲", "乙", "丙", "丁"];
  assert.equal(isGroupGreeting("你好"), true);
  assert.equal(isGroupGreeting("大家好"), true);
  assert.equal(isGroupGreeting("帮我写代码"), false);
  assert.equal(greetingFallback("甲"), "你好，我是甲。");
  const label = groupTurnStatusLabel([
    { name: room[0], stage: "deciding" },
    { name: room[1], stage: "reading" },
    { name: room[2], stage: "reading" },
    { name: room[3], stage: "reading" },
  ]);
  assert.match(label, /全组 4 人/);
  assert.match(label, /甲正在判断要不要回复/);
  assert.doesNotMatch(label, /3 位智能体正在读取/);
  const greeted: string[] = [];
  for (const name of room) {
    if (!isGroupGreeting("你好")) continue;
    greeted.push(greetingFallback(name));
  }
  assert.equal(greeted.length, 4);
});

test("a new message is re-judged by every other member until a full pass stays quiet", () => {
  const room = [{ id: "A" }, { id: "B" }, { id: "C" }];
  const wants = (id: string, spoken: string[]) => {
    const table: Record<string, string[]> = {
      "": ["A", "B"],
      A: ["B"],
      "A,B": ["C"],
      "A,B,C": ["A"],
      "A,B,C,A": [],
    };
    return (table[spoken.join(",")] || []).includes(id);
  };
  const spoken: string[] = [];
  let latest = "";
  let guard = 0;
  const cap = maxReactionTurns(room.length);
  while (guard < cap) {
    guard += 1;
    const order = planReactionOrder(room, { latestAuthorId: latest });
    let spoke = false;
    for (const id of order) {
      if (!wants(id, spoken)) continue;
      spoken.push(id);
      latest = id;
      spoke = true;
      break;
    }
    if (!spoke) break;
  }
  assert.deepEqual(spoken, ["A", "B", "C", "A"]);
});

test("addressed members judge first, and the latest speaker skips their own message", () => {
  assert.deepEqual(
    planReactionOrder([{ id: "A" }, { id: "B" }, { id: "C" }], { addressedIds: ["C"] }),
    ["C", "A", "B"],
  );
  assert.deepEqual(
    planReactionOrder([{ id: "A" }, { id: "B" }, { id: "C" }], { latestAuthorId: "A" }),
    ["B", "C"],
  );
});

test("solo speaker is the best duty match, otherwise the first member", () => {
  assert.equal(pickSoloSpeaker(roster, "写代码")?.id, "b");
  assert.equal(pickSoloSpeaker(roster, "今天星期几")?.id, "a");
});

test("asking which features exist is a capability question, not a work order", () => {
  assert.equal(isCapabilityQuestion("你有哪些功能"), true);
  assert.equal(isDirectAnswerTurn("你有哪些功能", 0), true);
  assert.equal(isExecutionRequested("你有哪些功能"), false);
});

test("a group greeting asks for a short hello, not a finished task", () => {
  const prompt = buildSocialBackendMessage({
    userText: "大家好",
    targets: [roster[0]],
    history: [],
    roster,
    mode: "group",
    collectiveGreeting: true,
    clearedToSpeak: true,
    useDecisionProtocol: false,
  });
  assert.match(prompt, /用自己的身份回一句短问候/);
  assert.doesNotMatch(prompt, /这件事由你做完/);
});

test("the speak judge reads earlier dialogue and this turn's replies", () => {
  const prompt = buildSpeakJudgePrompt({
    name: "码川",
    duty: "写代码",
    userText: "你有哪些功能",
    prior: [],
    history: [
      { name: "用户", text: "大家好" },
      { name: "阿查", text: "你好。" },
    ],
    addressed: false,
    capability: true,
  });
  assert.match(prompt, /大家好/);
  assert.match(prompt, /阿查：你好/);
  assert.match(prompt, /用户在问功能、身份或能力/);
  assert.match(prompt, /本轮还没有人回答这条消息/);
  assert.match(prompt, /只输出一个词：speak 或 silent/);
});

test("a speak verdict keeps the sentence and drops a later silence token", () => {
  assert.equal(visibleReplyAfterJudge("DECISION: silent\n你好，我是阿查。"), "你好，我是阿查。");
  assert.equal(visibleReplyAfterJudge("silent"), "");
  assert.equal(visibleReplyAfterJudge("大家好，我在。"), "大家好，我在。");
});

test("the speak judge reads earlier replies and only returns speak or silent", () => {
  const prompt = buildSpeakJudgePrompt({
    name: "码川",
    duty: "写代码",
    userText: "今天星期几",
    prior: [{ name: "阿查", text: "今天是星期四。" }],
    addressed: false,
  });
  assert.match(prompt, /阿查：今天是星期四/);
  assert.match(prompt, /只输出一个词：speak 或 silent/);
  assert.match(prompt, /都要重新判断/);
  assert.equal(parseSpeakVerdict("silent", true), false);
  assert.equal(parseSpeakVerdict("speak", false), true);
  assert.equal(parseSpeakVerdict("", false), false);
  assert.equal(parseSpeakVerdict("", true), true);
});

test("a request for user confirmation is recognized before anyone else runs", () => {
  assert.equal(agentAsksForUserInput("需要用户确认后再执行。"), true);
  assert.equal(agentAsksForUserInput("请用户确认范围。"), true);
  assert.equal(agentAsksForUserInput("用哪个路径？"), true);
  assert.equal(agentAsksForUserInput("目录里有三个文件。"), false);
  assert.equal(agentAsksForUserInput("你好！有什么需要实现的代码需求吗？"), false);
  assert.equal(agentAsksForUserInput("需要的话我可以接着改。"), false);
});

test("a longer result is not treated as a duplicate of the sentence it quotes", () => {
  const earlier = "我先看一下当前目录。";
  const result = "我先看一下当前目录。里面有 main.py、README 和测试文件，已经列出来了。";
  assert.equal(
    isNearDuplicateBubble("a", result, [{ agentId: "a", text: earlier }]),
    false,
  );
  assert.equal(
    isNearDuplicateBubble("a", earlier, [{ agentId: "a", text: earlier }]),
    true,
  );
});

test("later speakers see a sliding window of messages, with older ones summarized", () => {
  const prior = Array.from({ length: 10 }, (_, index) => ({
    name: `成员${index}`,
    text: `第${index}条消息`,
  }));
  const prompt = buildSpeakJudgePrompt({
    name: "码川",
    duty: "写代码",
    userText: "继续",
    prior,
    addressed: false,
  });
  assert.match(prompt, /滑动摘要/);
  assert.match(prompt, /滑动窗口/);
  assert.match(prompt, /成员0：第0条消息/);
  assert.match(prompt, /成员9：第9条消息/);
  assert.match(prompt, /成员5：第5条消息/);
});

test("standing rules are pinned and cancelled without showing the protocol line", () => {
  const raw = "好的，以后都这样做。\nRULE: 改项目前读文件 | 每次修改项目之前，先读取 docs/spec.md\n";
  assert.equal(cleanDisplayText(raw), "好的，以后都这样做。");
  const parsed = parseStandingRules(raw);
  assert.equal(parsed.rules[0]?.title, "改项目前读文件");
  assert.match(parsed.rules[0]?.body || "", /docs\/spec.md/);
  let board = applyStandingRules([], parsed.rules, [], { kind: "agent", name: "码川" });
  assert.equal(board.length, 1);
  assert.equal(board[0]?.authorName, "码川");
  const drop = parseStandingRules("UNRULE: 改项目前读文件");
  board = applyStandingRules(board, drop.rules, drop.drops);
  assert.equal(board.length, 0);
  assert.equal(parseStandingRules("取消公告：改项目前读文件").drops.length, 1);
});

test("DECISION tokens are removed even when they sit inside a sentence", () => {
  assert.equal(cleanDisplayText("DECISION: reply\n好的，我来处理。"), "好的，我来处理。");
  assert.equal(cleanDisplayText("好的 DECISION: reply 我来处理这件事。"), "好的 我来处理这件事。");
});

test("a sole speaker is told not to emit the decision protocol", () => {
  const prompt = buildSocialBackendMessage({
    userText: "今天星期几",
    targets: [roster[0]],
    history: [],
    roster,
    mode: "group",
    useDecisionProtocol: false,
  });
  assert.match(prompt, /不要输出 DECISION/);
  assert.doesNotMatch(prompt, /第一行只能是 DECISION/);
});

test("a schedule turn tells the agent to call the tool", () => {
  const prompt = buildSocialBackendMessage({
    userText: "每天下午3点提醒我",
    targets: [roster[0]],
    history: [],
    roster,
    mode: "group",
    scheduleRequest: true,
    useDecisionProtocol: false,
  });
  assert.match(prompt, /create_scheduled_task/);
  assert.match(prompt, /不要说没有这个功能/);
  assert.match(prompt, /侧边栏「日程」/);
  assert.match(prompt, /创建失败/);
  assert.match(prompt, /不要说需要换到持久化会话/);
});

test("thinking points keep the list and join the following sentence", () => {
  const out = tightenMarkdownFlow("1. 先看目录。\n\n2. 再改文件。\n这句话还是第二点。");
  assert.match(out, /1\. 先看目录。\n2\. 再改文件。这句话还是第二点。/);
});

test("a file edit becomes a short work note", () => {
  const note = workNoteFromTool("墨白", "str_replace", {
    ok: true,
    args: { path: "E:/Project/AgentFrame/main.py", old_string: "timeout = 5", new_string: "timeout = 30" },
  });
  assert.match(note, /墨白/);
  assert.match(note, /main\.py/);
  assert.match(note, /timeout = 30/);
});

test("the speak prompt includes the work site, not only the chat window", () => {
  const prompt = buildSpeakJudgePrompt({
    name: "墨白",
    userText: "刚才谁改了文件",
    prior: [],
    addressed: true,
    context: "墨白 编辑了 main.py：把「timeout = 5」改成「timeout = 30」",
  });
  assert.match(prompt, /工作现场/);
  assert.match(prompt, /main\.py/);
});
