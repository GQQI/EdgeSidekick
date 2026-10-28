import { boardProtocolHint, summarizeReply } from "./sharedBoard";
import type { SocialAgent, SocialChatMsg, SocialGroup } from "./types";

function speakerLabel(m: SocialChatMsg): string {
  if (m.role === "user") return "【人类用户】";
  if (m.role === "agent") return `【智能体·${m.agentName || m.agentId || "未知"}】`;
  return "【系统】";
}

function messageTextForHistory(m: SocialChatMsg): string {
  const attachments = (m.attachments || [])
    .map((item) => `【群内共享文件: ${item.name} | ${item.path}】`)
    .join("\n");
  return [m.text, attachments].filter(Boolean).join("\n");
}

function historyBlock(history: SocialChatMsg[], limit = 8): string {
  const all = history.filter((m) => m.role !== "system" && !(m as { readingStatus?: boolean }).readingStatus);
  if (!all.length) return "（暂无历史）";
  const recent = all.slice(-limit);
  const older = all.slice(0, -limit).slice(-12);
  const summary = older.length
    ? `更早的滑动摘要: ${summarizeReply(
        older.map((m) => `${speakerLabel(m)} ${summarizeReply(messageTextForHistory(m), 60)}`).join("；"),
        280,
      )}\n`
    : "";
  return summary + recent.map((m) => `${speakerLabel(m)}: ${messageTextForHistory(m)}`).join("\n");
}

function sharedBlock(group?: SocialGroup | null): string {
  if (!group) return "";
  const anns = (group.announcements || [])
    .slice(0, 12)
    .map((a) => `- ${a.title}: ${a.body}`)
    .join("\n");
  const shares = (group.shared || [])
    .slice(0, 5)
    .map((s) => `- [共享/${s.kind}] ${s.title}${s.detail ? ` — ${s.detail}` : ""}`)
    .join("\n");
  const parts = [
    group.topic ? `群主题: ${group.topic}` : "",
    anns ? `一直生效的公告（取消前都要遵守）:\n${anns}` : "",
    shares ? `群共享:\n${shares}` : "",
  ].filter(Boolean);
  return parts.length ? parts.join("\n") + "\n\n" : "";
}

function rosterBlock(roster: SocialAgent[], selfId?: string): string {
  return roster
    .map((a) => {
      const me = selfId && a.id === selfId ? " ←你本人" : "";
      return `- ${a.name}（${a.title}） 职责:${a.duty || a.blurb}${me}`;
    })
    .join("\n");
}


function userPrefersChinese(text: string): boolean {
  const cn = (text.match(/[\u4e00-\u9fff]/g) || []).length;
  const en = (text.match(/[A-Za-z]/g) || []).length;
  return cn > 0 && cn >= en * 0.4;
}

function languageRule(userText: string): string {
  if (userPrefersChinese(userText)) {
    return (
      "本轮回复语言是简体中文。系统说明和工具结果可能是英文，这不改变回复语言。" +
      "思考过程和给用户看的话都用简体中文。代码、路径、工具名保持原文。\n"
    );
  }
  const en = (userText.match(/[A-Za-z]/g) || []).length;
  if (en >= 2) {
    return "This turn is English. Think and reply in English. Do not answer in Chinese. Keep code and paths as written.\n";
  }
  return "Think and reply in the user's language. Keep code and paths as written.\n";
}

function lockLanguage(body: string, userText: string): string {
  return body.replace(/\s+$/, "") + "\n" + languageRule(userText);
}

function standingRuleHint(): string {
  return (
    "\n公告只放以后一直要遵守的规矩，不放一次性的任务或进度。" +
    "用户要求以后都这样做，或你判断出一条以后都要遵守的规矩时，另起一行写 RULE: 短标题 | 具体规矩。" +
    "用户要求取消某条规矩时，另起一行写 UNRULE: 标题或内容里的词。这两行不会出现在对话里。\n"
  );
}

const DM_SCENE_RULE = "这是和用户的私聊。直接回答，不要输出 DECISION。\n";

function identityBlock(who: SocialAgent, userText = "", scene: "dm" | "group" = "group", rewardNotes = ""): string {
  return (
    languageRule(userText) +
    (scene === "dm" ? DM_SCENE_RULE : "只以自己的身份说话。已经出现过的内容和工具结果不要再说一遍。\n") +
    `你是「${who.name}」（${who.title}）。\n` +
    `职责: ${who.duty || who.blurb}\n` +
    (who.persona ? `人设: ${who.persona}\n` : "") +
    (rewardNotes ? `以往同类做法: ${rewardNotes}\n` : "")
  );
}

export function buildSocialBackendMessage(opts: {
  userText: string;
  targets: SocialAgent[];
  history: SocialChatMsg[];
  roster: SocialAgent[];
  mode: "group" | "dm";
  group?: SocialGroup | null;
  discussAll?: boolean;
  /** Stay silent with [NO_REPLY] when irrelevant */
  optionalReply?: boolean;
  /** The user asked a normal question; answer it without creating a task. */
  directAnswer?: boolean;
  /** The user greeted the group / everyone; each member should acknowledge once. */
  collectiveGreeting?: boolean;
  /** A short courtesy line, such as 你好. The member may answer in one sentence. */
  lightweightSocial?: boolean;
  /** Produce a final integration of independent completed work; never re-run it. */
  synthesis?: boolean;
  /** Concrete work already confirmed for this member in the execution wave. */
  assignedTasks?: string[];
  /** Preformatted group shared-board snapshot */
  boardText?: string;
  /** Mid-turn discuss lines so later speakers see nominations. */
  peerDiscussText?: string;
  /** 1 = first wave, 2 = follow-up after peers committed */
  boardWave?: 1 | 2;
  /** A colleague @'d this member during the current group turn. */
  peerHandoff?: boolean;
  /** Recent user scores for this agent. */
  rewardNotes?: string;
  /** Per-agent private memory block (not shared). */
  privateMemory?: string;
  /**
   * open = no strong address (floor open, silent-by-default);
   * addressee = the user or a colleague asked you to lead (you still may decline);
   * bystander = you saw the same message; speak only to correct a wrong @.
   */
  turnRole?: "open" | "addressee" | "bystander";
  /** Override history window (bystanders default short). */
  historyLimit?: number;
  /** One-shot group brief for DM (not live-bound). */
  groupBrief?: string;
  /** Mark DM as a fresh session. */
  dmFresh?: boolean;
  /** Names of strongly addressed agents this turn (for bystander prompts). */
  addressedNames?: string[];
  /** Task harness contract for this member. */
  harnessContract?: string;
  /** Hide the peer roster so this member does not role-play the rest of the group. */
  omitPeerRoster?: boolean;
  /** Group members who may stay silent need the DECISION line. A sole speaker does not. */
  useDecisionProtocol?: boolean;
  /** A hidden judge already decided this member should speak. */
  clearedToSpeak?: boolean;
  /** Judge already chose speak, and the first draft was only a silence token. */
  sentenceOnly?: boolean;
  /** User asked to create a reminder / scheduled run. */
  scheduleRequest?: boolean;
  /** Who edited which file in this workspace. Short lines, not file bodies. */
  workSite?: string;
  /**
   * Free-form group chat (the default group mode). The member already decided
   * to reply to `latest`; `history` holds everything before it.
   */
  freeChat?: {
    latest: { author: string; isUser: boolean; text: string };
    /** The latest message named this member. */
    addressed?: boolean;
    /** Only for a pipeline the user explicitly asked for. */
    pipelineStep?: { index: number; total: number; task: string; nextName?: string };
    /** The user greeted the group: answer with one short line. */
    brief?: boolean;
    /** The user asked every member to speak from its own angle. */
    everyone?: boolean;
    /** This member must answer (named, owed, or the fallback answerer). */
    mustAnswer?: boolean;
    /** The user asked for the work itself, not a discussion of it. */
    action?: boolean;
  };
}): string {
  const hist = historyBlock(opts.history, opts.historyLimit ?? (opts.turnRole === "bystander" ? 4 : 12));
  const shared = sharedBlock(opts.group);
  const targets = opts.targets;
  if (opts.sentenceOnly) {
    const speaker = targets[0] || opts.roster[0];
    return lockLanguage(
      [
        `你是「${speaker?.name || "成员"}」。`,
        `用户刚说：${opts.userText}`,
        "你已经要发言。只写给用户的那一句。",
        "不要写 silent、DECISION、TASK、ASK。",
      ].join("\n"),
      opts.userText,
    );
  }

  if (opts.mode === "dm") {
    const who = targets[0] || opts.roster[0];
    const freshNote = opts.dmFresh
      ? "\n【会话·空白】这是空白私聊会话：你不知道任何群聊内容，也不要使用群记忆/群摘要。只根据本会话里出现的消息回答。\n"
      : "";
    const brief = !opts.dmFresh && opts.groupBrief
      ? `
【群摘要·只读背景】（来自群资料/共享板，不是本私聊历史）
${opts.groupBrief}
`
      : "";
    return lockLanguage(
      "[社交私聊模式]\n" +
      identityBlock(who, opts.userText, "dm", opts.rewardNotes || "") +
      freshNote +
      (opts.privateMemory ? opts.privateMemory + "\n" : "") +
      brief +
      `\n最近对话:\n${hist}\n\n` +
      (!opts.dmFresh && opts.workSite?.trim()
        ? `工作现场（谁改过文件、看过哪个目录，可直接引用）：\n${opts.workSite.trim()}\n\n`
        : "") +
      `【当前消息·人类用户】（不是任何智能体）:\n${opts.userText}\n\n` +
      `请只以「${who.name}」身份回复，自然中文，不要冒充他人。`,
      opts.userText,
    );
  }

  // Multi-agent one-shot (legacy discuss-all). Prefer parallel single-agent calls instead.
  if (opts.discussAll || targets.length >= 2) {
    const names = (targets.length ? targets : opts.roster.filter((a) => a.id !== "lead"))
      .map((a) => a.name)
      .join("、");
    return lockLanguage(
      "[社交群聊模式]\n" +
      `群名: ${opts.group?.name || "协作群"}\n` +
      shared +
      `成员名册:\n${rosterBlock(opts.roster)}\n\n` +
      `最近消息:\n${hist}\n\n` +
      `【当前消息·人类用户】（不是任何智能体）:\n${opts.userText}\n\n` +
      `请让以下成员依次发言讨论（每人一小段）: ${names}。\n` +
      "必须严格用下面格式输出（便于界面拆成多条气泡）:\n" +
      "<<<agent:成员名>>>\n发言内容\n<<<end>>>\n" +
      "总共不超过 6 段。" +
      standingRuleHint(),
      opts.userText,
    );
  }

  const who = targets[0] || opts.roster[0];
  const others = opts.roster.filter((a) => a.id !== who.id);
  if (opts.freeChat) return freeChatMessage(who, others, opts, opts.freeChat, shared);
  return lockLanguage(
    "[社交群聊模式 · 单成员视角]\n" +
    identityBlock(who, opts.userText, "group", opts.rewardNotes || "") +
    (opts.privateMemory ? opts.privateMemory + "\n" : "") +
    `群名: ${opts.group?.name || "协作群"}\n` +
    shared +
    (opts.omitPeerRoster
      ? (userPrefersChinese(opts.userText)
          ? "本组有其他成员，但这次不要转交，也不要扮演他们。\n\n"
          : "Other members exist. Do not hand off and do not speak as them.\n\n")
      : others.length
        ? `其他成员（仅供了解群上下文，绝不可扮演）:\n${rosterBlock(others)}\n\n`
        : "") +
    `最近消息:\n${hist}\n\n` +
    (opts.workSite?.trim() ? `工作现场（谁改过文件、看过哪个目录，可直接引用）：\n${opts.workSite.trim()}\n\n` : "") +
    `用户:\n${opts.userText}\n` +
    `只输出「${who.name}」自己的话。` +
    (opts.boardText ? `\n\n群里已有记录:\n${opts.boardText}` : "") +
    (opts.peerDiscussText ? `\n\n本轮已发言:\n${opts.peerDiscussText}` : "") +
    (opts.boardWave === 2 && (opts.assignedTasks || []).length
      ? `\n\n请完成:\n${opts.assignedTasks!.join("\n")}\n`
      : "") +
    (opts.harnessContract ? `\n${opts.harnessContract}\n` : "") +
    (opts.collectiveGreeting
      ? "\n用户在跟大家打招呼。用自己的身份回一句短问候。\n"
      : opts.lightweightSocial
        ? "\n用户说了一句很短的客套话。用自己的身份接一句，简短自然。\n"
        : turnRoleInstruction(who, opts)) +
    (opts.clearedToSpeak
      ? "\n你已经决定发言。只写前面还没有说过的内容。不要输出 DECISION、TASK、ASK、FACT。\n"
      : opts.useDecisionProtocol
        ? boardProtocolHint(opts.boardWave || 1)
        : "\n直接把要说的话写出来。不要输出 DECISION、TASK、ASK、FACT 这些协议行。\n") +
    standingRuleHint() +
    (opts.scheduleRequest
      ? "\n这是定时任务。调用 create_scheduled_task 创建。会话版可以直接创建，不要说需要换到持久化会话或另一个会话。工具返回「已创建」才算成功，告诉用户下次运行时间、结束日期，以及可在侧边栏「日程」查看。返回「创建失败」就说明失败，不要根据群记忆改口说已经设置成功，也不要把成功改口成失败。不要说没有这个功能。\n「定时一个月 / 持续一个月」不是只在今天跑一次：用 daily（或用户指定的频率），until 填今天起约 30 天后的 YYYY-MM-DD。\n「每个月」用 monthly 和 month_day。「一个月后」才用 once，且 at 必须是一个月后的日期。\n"
      : "") +
    (opts.directAnswer ? "\n直接回答，不要派任务。\n" : "") +
    (opts.synthesis ? "\n只合并已有结果，不要再执行。\n" : ""),
    opts.userText,
  );
}

type FreeChat = NonNullable<Parameters<typeof buildSocialBackendMessage>[0]["freeChat"]>;

/** Prompt for one member's reply in the free-form group loop. */
function freeChatMessage(
  who: SocialAgent,
  others: SocialAgent[],
  opts: Parameters<typeof buildSocialBackendMessage>[0],
  free: FreeChat,
  shared: string,
): string {
  const hist = historyBlock(opts.history, opts.historyLimit ?? 24);
  const author = free.latest.isUser ? "人类用户" : `智能体·${free.latest.author}`;
  const step = free.pipelineStep;
  const rules = step
    ? [
        `按已经排好的顺序协作（共 ${step.total} 步）。你负责第 ${step.index} 步的全部：${step.task}`,
        "这一步里前后相连的内容都由你做完，不要再拆给别人。",
        "上游成员的结果在上面的对话里。不要重做上游，也不要替下一步的成员做。",
        step.nextName ? `做完你这一步就停，下一步由「${step.nextName}」接手。` : "你是最后一步。做完这一步，并给出可以核对的结果。",
      ]
    : free.action
      ? [
          "用户要求把这件事做完，不是只讨论。",
          "最新消息里的整件事都由你完成，包括前后连在一起的每一步。不要只做开头就停，也不要把后面的步骤交给别人。",
          "做到有可以核对的结果。",
        ]
      : [
        "这是自由群聊：没有固定发言顺序，没有流水线，也没有人给你派活。每条新消息出现后，每个成员都会自己决定要不要说话。",
        `你已经决定回复最新这条消息（来自${author}）。`,
        free.brief
          ? "这条消息是寒暄或客套（问候、致谢、告别之类）。像真人群聊一样用一两句简短自然的话回应，不要长篇自我介绍，不要展开技术内容，不要和前面成员说得一样。"
          : free.everyone
            ? "用户请每个人从自己的角度说。只讲你职责角度的要点，前面成员已经说过的不要再说。"
            : free.addressed
              ? "最新消息点了你的名，请直接回应。"
              : "",
        "只说前面没人说过的内容：回答问你的问题、纠正错误，或补充新的信息。不要附和，不要复述或总结别人说过的话。",
        "回复的长度和深度要和消息相称：简单的问题简短回答，复杂的问题再展开。",
        "只以你自己的身份说话，不要替其他成员发言，不要给大家安排发言顺序或流水线。",
        free.mustAnswer
          ? "这条消息需要你回答，请直接给出回复（简短的客套话也可以），不要输出 silent。"
          : "如果写的时候发现其实没有新的内容可说，只输出 silent。",
      ];
  return lockLanguage(
    "[社交群聊模式 · 自由群聊]\n" +
    identityBlock(who, opts.userText, "group", opts.rewardNotes || "") +
    (opts.privateMemory ? opts.privateMemory + "\n" : "") +
    `群名: ${opts.group?.name || "协作群"}\n` +
    shared +
    (others.length ? `其他成员（只供了解，绝不可扮演）:\n${rosterBlock(others)}\n\n` : "") +
    `之前的对话（从旧到新，包含本轮其他成员的回复）:\n${hist}\n\n` +
    (opts.workSite?.trim() ? `工作现场（谁改过文件、看过哪个目录，可直接引用）：\n${opts.workSite.trim()}\n\n` : "") +
    (free.latest.isUser || free.latest.text === opts.userText
      ? ""
      : `本轮用户的原始消息:\n${opts.userText}\n\n`) +
    `【最新消息·${author}】:\n${free.latest.text}\n\n` +
    rules.filter(Boolean).join("\n") +
    `\n只输出「${who.name}」自己的话。不要输出 DECISION、TASK、ASK、FACT 这些协议行。\n` +
    standingRuleHint() +
    (opts.scheduleRequest
      ? "\n这是定时任务。调用 create_scheduled_task 创建，工具返回「已创建」才算成功；返回失败就如实说明。\n"
      : ""),
    opts.userText,
  );
}

function turnRoleInstruction(
  who: SocialAgent,
  opts: {
    optionalReply?: boolean;
    turnRole?: "open" | "addressee" | "bystander";
    addressedNames?: string[];
    userText: string;
    peerHandoff?: boolean;
  },
): string {
  const role = opts.turnRole || (opts.optionalReply ? "open" : "addressee");
  if (opts.peerHandoff) return "\n同事刚 @ 了你。要答就 reply，一句接住或说明接不了。";
  if (role === "addressee") return `\n用户希望「${who.name}」处理。这件事由你做完。`;
  if (role === "bystander") {
    const whoList = (opts.addressedNames || []).filter(Boolean).join("、") || "别人";
    return `\n用户点的是${whoList}。默认 silent。`;
  }
  return "\n没有点名。说你自己的一句，不要重复同事已经说过的话。";
}

export function splitAgentBlocks(text: string): { name: string; text: string }[] {
  const out: { name: string; text: string }[] = [];
  const re = /<{2,3}\s*agent\s*:\s*([^>]+?)\s*>{2,3}\s*([\s\S]*?)<{2,3}\s*end\s*>{2,3}/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    const name = m[1].trim();
    const body = m[2].trim();
    if (name && body) out.push({ name, text: body });
  }
  return out;
}

/** Keep only this agent's slice if the model leaked multi-agent tags. */
export function extractOwnReply(text: string, agentName: string): string {
  if (!text) return "";
  const blocks = splitAgentBlocks(text);
  if (blocks.length) {
    const exact = blocks.find((b) => b.name === agentName);
    if (exact) return exact.text;
    const fuzzy = blocks.find(
      (b) => b.name.includes(agentName) || agentName.includes(b.name),
    );
    if (fuzzy) return fuzzy.text;
    // Tags present but not for this agent — keep stripped body so a finished
    // reply never collapses to empty (which made bubbles vanish onFinal).
    return stripAgentTags(text);
  }
  return stripAgentTags(text);
}

export function stripAgentTags(text: string): string {
  if (!text) return "";
  let s = text;
  s = s.replace(/<{2,3}\s*agent\s*:[^>]*\s*>{2,3}/gi, "");
  s = s.replace(/<{2,3}\s*end\s*>{2,3}/gi, "");
  s = s.replace(/<<<[^\n>]{0,40}agent[^\n>]{0,40}>>>/gi, "");
  s = s.replace(/<<<[^\n>]{0,20}end[^\n>]{0,20}>>>/gi, "");
  return s.replace(/\n{3,}/g, "\n\n").trim();
}
