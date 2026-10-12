/**
 * Group chat loop for 会话版, following Grok Bot's group host:
 *  - The host does not classify the task with phrase lists. A model reads the
 *    message once (who it is for, whether it is work, whether a named order
 *    was requested). Members then decide for themselves.
 *  - `@name` selects only the named members for that message. `@everyone` or
 *    no mention selects every other member. The author never judges itself.
 *  - Each selected member says reply or silent. One speaker at a time; the
 *    next pass sees that reply. A pass where nobody speaks ends the turn.
 *  - One owner for a stretch of implementation the model kept on one lead.
 *    A second member still joins when the model assigns them a later step,
 *    or when a new message names them.
 *  - The user's own question is never dropped on the floor: if nobody
 *    volunteers, the lead or the closest profile answers once.
 *  - Caps match the group room: 10 replies a turn, 2 from any one member.
 */
import { parseSpeakVerdict, buildSlidingWindow } from "./turnLogic";
import { parseMentions, mentionsEveryone } from "./store";
import type { SocialAgent } from "./types";

export type ChatAgent = {
  id: string;
  name: string;
  title?: string;
  duty?: string;
  blurb?: string;
  persona?: string;
};

export type ChatLine = {
  id: string;
  role: "user" | "agent";
  agentId?: string;
  agentName?: string;
  text: string;
};

export type PipelineStep = { agentId: string; task: string };

export type ExplicitPipeline = {
  steps: PipelineStep[];
  /** How the request was recognised; used for the notice and for tests. */
  how: "requested";
};

export type DecisionReason =
  | "addressed" // named by the user (or by a colleague's latest message): answers
  | "everyone" // the turn analyzer found the user wants every member to respond: each answers once
  | "judge" // the member's own reply/silent decision
  | "judge_failed" // the decision call failed; addressed => reply, else silent
  | "fallback" // nobody volunteered for a user message; best fit answers
  | "pipeline" // explicit, user-requested pipeline step
  | "cap"; // member already used its per-turn reply budget

export type ReplyDecision = {
  agentId: string;
  agentName: string;
  reply: boolean;
  reason: DecisionReason;
};

export type EndReason = "quiet" | "cap" | "aborted";

export type LoopEvent =
  | { type: "judging"; trigger: ChatLine; agentIds: string[] }
  | { type: "decisions"; trigger: ChatLine; decisions: ReplyDecision[] }
  | { type: "speaking"; agentId: string; trigger: ChatLine; step?: PipelineStep }
  | { type: "message"; line: ChatLine }
  | { type: "declined"; agentId: string; trigger: ChatLine }
  | { type: "intent"; intent: TurnIntent }
  | { type: "end"; reason: EndReason; agentMessages: number };

export type JudgeInput = {
  agent: ChatAgent;
  roster: ChatAgent[];
  /** Full conversation so far, oldest first. The trigger is the last line. */
  history: ChatLine[];
  trigger: ChatLine;
  /** The trigger addresses this member by name (or @everyone). */
  addressed: boolean;
  /** Names the trigger addressed (may be other members). */
  addressedNames: string[];
  /** How many times this member already spoke in this user turn. */
  repliesSoFar: number;
  /** The user's message that started this turn. */
  turnUserText: string;
  /** Agent messages so far in this turn. */
  repliesThisTurn: number;
  /** The user asked the group (你们 / 大家) rather than one member. */
  groupQuestion: boolean;
  /** Small talk: a short greeting is enough. The member still decides. */
  social?: boolean;
};

export type ReplyInput = {
  agent: ChatAgent;
  roster: ChatAgent[];
  history: ChatLine[];
  trigger: ChatLine;
  addressed: boolean;
  step?: PipelineStep;
  /** The user greeted the group: one short line. */
  brief?: boolean;
  /** The user asked every member to speak from its own angle. */
  everyone?: boolean;
  /** Addressed / owed / fallback / pipeline: the member must answer (no silent option). */
  mustAnswer?: boolean;
  /** The user asked for the work to actually be done, not only discussed. */
  action?: boolean;
};

export type GroupLoopOptions = {
  roster: ChatAgent[];
  /** Conversation including the new user message as the LAST line. */
  history: ChatLine[];
  /** true = reply, false = silent, null = the decision call failed. */
  judge: (input: JudgeInput, signal?: AbortSignal) => Promise<boolean | null>;
  /**
   * Produce this member's reply. Return the visible text (or several bubbles).
   * Empty / null means the member changed its mind and stays silent.
   */
  reply: (input: ReplyInput, signal?: AbortSignal) => Promise<string | string[] | null>;
  signal?: AbortSignal;
  /** Max agent messages per user turn (pipeline steps are added on top). */
  maxAgentMessages?: number;
  /** Max messages a single member may send per user turn. */
  maxRepliesPerAgent?: number;
  /** Force a pipeline (tests); normally it comes from the turn analyzer. */
  pipeline?: ExplicitPipeline | null;
  /**
   * Understand the user's message (audience / lead / small talk / explicit
   * order) with the model. Gets the prompt from buildTurnIntentPrompt and
   * returns the raw model output. Missing or failing → fallbackTurnIntent.
   */
  analyzeTurn?: (prompt: string, input: TurnIntentInput, signal?: AbortSignal) => Promise<string | null>;
  /** Pre-computed intent (tests / callers that already analysed the turn). */
  intent?: TurnIntent;
  onEvent?: (event: LoopEvent) => void;
  /** Return true to stop after a message (e.g. the member asked the user). */
  shouldStop?: (line: ChatLine) => boolean;
  newId?: () => string;
};

export type GroupLoopResult = {
  /** Agent messages produced in this turn, in order. */
  messages: ChatLine[];
  reason: EndReason;
  events: LoopEvent[];
};

export function defaultMaxAgentMessages(rosterSize: number): number {
  return Math.max(3, Math.min(10, rosterSize * 2));
}

/** Grok Bot: one member posts at most twice on a single user message. */
export const GROUP_REPLIES_PER_MEMBER = 2;

let lineSeq = 0;
function fallbackId(): string {
  lineSeq += 1;
  return `gl_${Date.now().toString(36)}_${lineSeq}`;
}

/** Members a message addresses by name / @, excluding its author. */
export function addressedAgentIds(line: ChatLine, roster: ChatAgent[]): { ids: Set<string>; everyone: boolean } {
  const everyone = mentionsEveryone(line.text || "");
  const hits = parseMentions(line.text || "", roster as unknown as SocialAgent[])
    .map((agent) => agent.id)
    .filter((id) => id !== line.agentId);
  return { ids: new Set(hits), everyone };
}

/**
 * Lexical overlap between a message and a member's OWN configured profile
 * (name / title / duty / blurb). No role-specific keywords: it only measures
 * how much of the message's vocabulary appears in that member's description.
 * Used as a last tie-break, and as the fallback answerer when the turn
 * analyzer (an LLM call) is unavailable.
 */
export function fitScore(agent: ChatAgent, text: string): number {
  const blob = `${agent.name} ${agent.title || ""} ${agent.duty || ""} ${agent.blurb || ""}`.toLowerCase();
  const hay = String(text || "").toLowerCase();
  let score = 0;
  // Space-separated words (Latin scripts).
  for (const word of hay.split(/[^\p{L}\p{N}]+/u)) {
    if (word.length >= 3 && /^[a-z0-9]+$/.test(word) && blob.includes(word)) score += 1;
  }
  // CJK has no spaces: shared character bigrams.
  const seen = new Set<string>();
  for (let i = 0; i + 2 <= blob.length; i += 1) {
    const gram = blob.slice(i, i + 2);
    if (!seen.has(gram) && /^[\u3040-\u30ff\u3400-\u9fff]{2}$/.test(gram) && hay.includes(gram)) {
      seen.add(gram);
      score += 1;
    }
  }
  if (agent.name && hay.includes(agent.name.toLowerCase())) score += 3;
  if (agent.title && hay.includes(agent.title.toLowerCase())) score += 3;
  return score;
}

/** Best-fit member for a user message nobody volunteered for. */
export function bestFitAgent(roster: ChatAgent[], text: string): ChatAgent | undefined {
  if (!roster.length) return undefined;
  let best = roster[0];
  let bestScore = fitScore(best, text);
  for (const agent of roster.slice(1)) {
    const score = fitScore(agent, text);
    if (score > bestScore) {
      best = agent;
      bestScore = score;
    }
  }
  return best;
}

/* ------------------------------------------------------------------------ */
/* Turn intent: who the user is talking to, and whether they asked for a     */
/* fixed order. Understood by the model from the roster's own descriptions,  */
/* never by phrase lists.                                                    */
/* ------------------------------------------------------------------------ */

export type TurnAudience = "everyone" | "group" | "members" | "open";

export type TurnIntent = {
  /** everyone: each member should respond; group: the group's views are asked (anyone with a view);
   *  members: specific members were addressed; open: no addressee, best-suited member answers. */
  audience: TurnAudience;
  /** Members the user spoke to directly. */
  memberIds: string[];
  /** Member whose role best fits answering first (model's judgement from the role descriptions). */
  leadId?: string;
  /** Small talk (greeting / thanks / pleasantry): replies stay short. */
  social: boolean;
  /** The user wants members to actually do work (edit files, run code, inspect the project), not just discuss. */
  action: boolean;
  /** Only when the user explicitly asked specific members to work in a given order. */
  pipeline: ExplicitPipeline | null;
  source: "model" | "fallback";
  /** Why the model reading was not used (surfaced in logs / UI, never silent). */
  fallbackReason?: "no_analyzer" | "empty" | "unparseable" | "error" | "aborted";
};

export type TurnIntentInput = { roster: ChatAgent[]; history: ChatLine[]; userText: string };

/** Find a roster member from a name the model wrote (name, title or id; tolerant of @ and quotes). */
export function resolveMember(name: unknown, roster: ChatAgent[]): ChatAgent | undefined {
  const raw = String(name ?? "").replace(/^[@\s「『"'“]+|[\s」』"'”]+$/g, "").trim();
  if (!raw) return undefined;
  const low = raw.toLowerCase();
  return (
    roster.find((a) => a.name === raw || a.id === raw) ||
    roster.find((a) => a.name.toLowerCase() === low || a.id.toLowerCase() === low || (a.title || "").toLowerCase() === low) ||
    // "审码（代码审查）" style answers: the configured name inside the string.
    [...roster].sort((a, b) => b.name.length - a.name.length).find((a) => a.name && raw.includes(a.name))
  );
}

/** Deterministic intent from chat syntax only (@member, @all, vocative names); never a pipeline. */
export function fallbackTurnIntent(userLine: ChatLine, roster: ChatAgent[]): TurnIntent {
  const { ids, everyone } = addressedAgentIds(userLine, roster);
  return {
    audience: everyone ? "everyone" : ids.size ? "members" : "open",
    memberIds: [...ids],
    social: false,
    action: false,
    pipeline: null,
    source: "fallback",
  };
}

export function buildTurnIntentPrompt(input: TurnIntentInput): string {
  const members = input.roster
    .map((a) => `- ${a.name}${a.title ? `（${a.title}）` : ""}：${String(a.duty || a.blurb || "").replace(/\s+/g, " ").slice(0, 160)}`)
    .join("\n");
  const recent = input.history
    .slice(-9, -1)
    .map((line) => `【${speaker(line)}】${String(line.text || "").replace(/\s+/g, " ").trim().slice(0, 240)}`)
    .join("\n");
  return [
    "你是群聊的消息分析器。不要回答用户的问题，只分析用户最新这条消息是对谁说的、期待什么样的回应。",
    `群成员（名字（头衔）：职责）：\n${members}`,
    recent ? `最近的对话：\n${recent}` : "",
    `用户最新消息：\n「${String(input.userText || "").trim().slice(0, 1500)}」`,
    "只输出一个 JSON 对象，不要任何其他文字：",
    '{"audience":"everyone|group|members|open","members":["成员名"],"lead":"成员名","social":false,"action":false,"pipeline":null}',
    "字段含义：",
    "- audience：everyone = 用户希望每位成员都各自回应一下；group = 用户在征求整个群的看法，有想法的成员回应即可，不要求人人发言；members = 用户在直接对某几位成员说话（称呼了他们的名字或 @ 了他们）；open = 没有特定对象的问题或任务，交给最合适的成员处理。",
    "- members：用户直接称呼、点名要求回应的成员名字（必须是上面列表里的名字）。只是在话里提到某人、而不是对他说话，不算。",
    "- lead：根据各成员的职责描述，最适合第一个回应这条消息的成员名字。",
    "- social：这条消息是不是寒暄、问候、致谢、告别之类的客套话（不需要实质性内容）。",
    "- action：用户是否要求成员实际动手（修改文件、运行代码或命令、查看项目里的文件等），而不只是讨论或解答问题。",
    "- pipeline：默认 null。同一个人能做完的实现步骤（例如先删再建）放在 lead 上，pipeline 保持 null。后面如果还有另一位成员负责的不同工作（测试、审查、优化），把这位成员写进 steps，不要省掉。格式为 {\"steps\":[{\"member\":\"成员名\",\"task\":\"这一步要做的事\"}]}，至少两位不同的成员。",
  ]
    .filter(Boolean)
    .join("\n");
}

function extractJsonObject(raw: string): Record<string, unknown> | null {
  const text = String(raw || "")
    .replace(/<think>[\s\S]*?<\/think>/gi, "")
    .replace(/^[\s\S]*<\/think>/i, "")
    .replace(/```(?:json)?/gi, "");
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start < 0 || end <= start) return null;
  try {
    const value = JSON.parse(text.slice(start, end + 1));
    return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/**
 * Parse the analyzer's JSON. Names must resolve to configured members.
 * A pipeline is the model's own assignment: two or more distinct members,
 * each with a step. The host does not throw that assignment away.
 */
export function parseTurnIntent(raw: string, roster: ChatAgent[], userText: string): TurnIntent | null {
  const obj = extractJsonObject(raw);
  if (!obj) return null;
  const audienceRaw = String(obj.audience || "").toLowerCase();
  const audience: TurnAudience = (["everyone", "group", "members", "open"] as const).find((a) => a === audienceRaw) || "open";
  const memberIds = [
    ...new Set(
      (Array.isArray(obj.members) ? obj.members : [])
        .map((name) => resolveMember(name, roster)?.id)
        .filter((id): id is string => Boolean(id)),
    ),
  ];
  const leadId = resolveMember(obj.lead, roster)?.id;
  let pipeline: ExplicitPipeline | null = null;
  const p = obj.pipeline as { evidence?: unknown; steps?: unknown } | null | undefined;
  if (p && typeof p === "object" && Array.isArray(p.steps)) {
    const steps: PipelineStep[] = [];
    for (const row of p.steps as Array<{ member?: unknown; task?: unknown }>) {
      const agent = resolveMember(row?.member, roster);
      if (!agent || steps.some((step) => step.agentId === agent.id)) continue;
      steps.push({ agentId: agent.id, task: String(row?.task || "").trim() || userText });
    }
    if (steps.length >= 2) pipeline = { steps, how: "requested" };
  }
  return {
    audience: memberIds.length || audience !== "members" ? audience : "open",
    memberIds: audience === "members" ? memberIds : [],
    leadId,
    social: obj.social === true || String(obj.social).toLowerCase() === "true",
    action: obj.action === true || String(obj.action).toLowerCase() === "true",
    pipeline,
    source: "model",
  };
}


function asTexts(out: string | string[] | null | undefined): string[] {
  const list = Array.isArray(out) ? out : [out];
  return list.map((item) => String(item || "").trim()).filter(Boolean);
}

export async function runGroupConversation(opts: GroupLoopOptions): Promise<GroupLoopResult> {
  const roster = opts.roster.filter((agent) => agent && agent.id);
  const history = [...opts.history];
  const events: LoopEvent[] = [];
  const produced: ChatLine[] = [];
  const emit = (event: LoopEvent) => {
    events.push(event);
    try {
      opts.onEvent?.(event);
    } catch {
      /* UI callbacks never break the loop */
    }
  };
  const newId = opts.newId || fallbackId;
  const aborted = () => Boolean(opts.signal?.aborted);
  const perAgentCap = Math.max(1, opts.maxRepliesPerAgent ?? GROUP_REPLIES_PER_MEMBER);
  const spoken = new Map<string, number>();
  /** Members that wanted to reply earlier but were not picked yet (FIFO). */
  const waiting = new Map<string, number>();
  let tick = 0;
  const finish = (reason: EndReason): GroupLoopResult => {
    emit({ type: "end", reason, agentMessages: produced.length });
    return { messages: produced, reason, events };
  };
  const nameOf = (id: string) => roster.find((agent) => agent.id === id)?.name || id;

  /** Run one reply; returns the new line, or null when the member declined. */
  const speak = async (
    agent: ChatAgent,
    trigger: ChatLine,
    addressed: boolean,
    step?: PipelineStep,
    style: { brief?: boolean; everyone?: boolean; mustAnswer?: boolean; action?: boolean } = {},
  ) => {
    emit({ type: "speaking", agentId: agent.id, trigger, step });
    const texts = asTexts(
      await opts.reply({ agent, roster, history: [...history], trigger, addressed, step, ...style }, opts.signal),
    );
    if (!texts.length) {
      emit({ type: "declined", agentId: agent.id, trigger });
      return null;
    }
    const line: ChatLine = {
      id: newId(),
      role: "agent",
      agentId: agent.id,
      agentName: agent.name,
      text: texts.join("\n\n"),
    };
    history.push(line);
    produced.push(line);
    spoken.set(agent.id, (spoken.get(agent.id) || 0) + 1);
    waiting.delete(agent.id);
    emit({ type: "message", line });
    return line;
  };

  const userLine = history[history.length - 1];
  if (!userLine || !roster.length) return finish("quiet");
  const turnUserText = userLine.text || "";

  // 0) Understand the user's message once (model), fall back to chat syntax.
  let intent: TurnIntent = opts.intent || { ...fallbackTurnIntent(userLine, roster), fallbackReason: "no_analyzer" };
  if (!opts.intent && userLine.role === "user" && opts.analyzeTurn) {
    const input: TurnIntentInput = { roster, history: [...history], userText: turnUserText };
    const basePrompt = buildTurnIntentPrompt(input);
    let reason: TurnIntent["fallbackReason"] = "empty";
    // Retry only when the model wrote something that is not JSON. An empty or
    // timed-out call is not retried — that second wait is what froze the turn.
    for (let attempt = 0; attempt < 2 && !aborted(); attempt += 1) {
      const prompt = attempt === 0
        ? basePrompt
        : `${basePrompt}\n上一次的输出无法解析成 JSON。这次直接输出 JSON 对象本身，不要解释，不要写思考过程。`;
      try {
        const raw = await opts.analyzeTurn(prompt, input, opts.signal);
        const parsed = raw && raw.trim() ? parseTurnIntent(raw, roster, turnUserText) : null;
        if (parsed) {
          intent = parsed;
          reason = undefined;
          break;
        }
        reason = raw && raw.trim() ? "unparseable" : "empty";
        if (reason === "empty") break;
      } catch {
        reason = "error";
        break;
      }
    }
    if (aborted()) return finish("aborted");
    if (reason) intent = { ...intent, fallbackReason: reason };
  }
  // Explicit @mentions / vocatives are hard evidence of address; keep them even if the model missed one.
  const syntaxIds = userLine.role === "user" ? [...addressedAgentIds(userLine, roster).ids] : [];
  if (syntaxIds.length && intent.audience !== "everyone") {
    intent = { ...intent, audience: "members", memberIds: [...new Set([...intent.memberIds, ...syntaxIds])] };
  }
  if (opts.pipeline !== undefined) intent = { ...intent, pipeline: opts.pipeline };
  emit({ type: "intent", intent });

  const turnEveryone = intent.audience === "everyone";
  const greeting = intent.social;
  const groupQuestion = intent.audience === "everyone" || intent.audience === "group";
  const leadId = intent.leadId;
  const pipelineSteps = (intent.pipeline?.steps || []).filter((step) => roster.some((a) => a.id === step.agentId));
  const cap = Math.max(1, opts.maxAgentMessages ?? defaultMaxAgentMessages(roster.length)) + pipelineSteps.length;

  // 1) Steps the model assigned to two or more members, in that order.
  //    The host does not invent these from conjunctions, and does not drop
  //    a later member the model named.
  for (const step of pipelineSteps) {
    if (aborted()) return finish("aborted");
    const agent = roster.find((a) => a.id === step.agentId)!;
    const trigger = history[history.length - 1];
    emit({
      type: "decisions",
      trigger,
      decisions: [{ agentId: agent.id, agentName: agent.name, reply: true, reason: "pipeline" }],
    });
    const line = await speak(agent, trigger, true, step, { mustAnswer: true, action: Boolean(intent.action) });
    if (aborted()) return finish("aborted");
    if (line && opts.shouldStop?.(line)) return finish("aborted");
  }

  // 2) Each new message: the host picks who even gets a turn, then each of
  //    them decides. One reply, then everyone selected for the new message
  //    decides again. Nobody speaking ends the turn.
  while (produced.length < cap) {
    if (aborted()) return finish("aborted");
    const trigger = history[history.length - 1];
    const { ids: addressedIds, everyone } = addressedAgentIds(trigger, roster);
    const addressedNames = [...addressedIds].map(nameOf);
    // @name selects only those members. @everyone or no mention selects the rest.
    const exclusive = addressedIds.size > 0 && !everyone;
    const candidates = roster.filter(
      (agent) => agent.id !== trigger.agentId && (!exclusive || addressedIds.has(agent.id)),
    );
    const decisions: ReplyDecision[] = [];
    const toJudge: ChatAgent[] = [];
    for (const agent of candidates) {
      if ((spoken.get(agent.id) || 0) >= perAgentCap) {
        decisions.push({ agentId: agent.id, agentName: agent.name, reply: false, reason: "cap" });
      } else if (turnEveryone && (spoken.get(agent.id) || 0) === 0) {
        // The analyzer already decided the user wants each member once.
        // A later silent vote must not wipe that.
        decisions.push({ agentId: agent.id, agentName: agent.name, reply: true, reason: "everyone" });
      } else {
        toJudge.push(agent);
      }
    }
    emit({ type: "judging", trigger, agentIds: toJudge.map((agent) => agent.id) });
    const snapshot = [...history];
    const verdicts = await Promise.all(
      toJudge.map(async (agent) => {
        const addressed = addressedIds.has(agent.id) || everyone;
        let verdict: boolean | null = null;
        try {
          verdict = await opts.judge(
            {
              agent,
              roster,
              history: snapshot,
              trigger,
              addressed,
              addressedNames,
              repliesSoFar: spoken.get(agent.id) || 0,
              turnUserText,
              repliesThisTurn: produced.length,
              groupQuestion,
              social: greeting,
            },
            opts.signal,
          );
        } catch {
          verdict = null;
        }
        return { agent, addressed, verdict };
      }),
    );
    if (aborted()) return finish("aborted");
    for (const row of verdicts) {
      if (row.verdict === null) {
        // The decision call failed. A member the message actually named still
        // answers; everyone else stays quiet rather than guessing.
        decisions.push({
          agentId: row.agent.id,
          agentName: row.agent.name,
          reply: row.addressed && exclusive,
          reason: "judge_failed",
        });
      } else {
        decisions.push({ agentId: row.agent.id, agentName: row.agent.name, reply: row.verdict, reason: "judge" });
      }
    }
    // A user message is never left with zero replies. If every selected member
    // stays silent or the decision call fails, the lead (or the closest
    // profile) still answers. Later messages can end the turn by silence.
    if (trigger.role === "user" && produced.length === 0 && !decisions.some((d) => d.reply)) {
      const eligible = candidates.filter((agent) => (spoken.get(agent.id) || 0) < perAgentCap);
      const pick = eligible.find((agent) => agent.id === leadId) || bestFitAgent(eligible, trigger.text);
      if (pick) {
        const row = decisions.find((d) => d.agentId === pick.id);
        if (row) {
          row.reply = true;
          row.reason = "fallback";
        }
      }
    }
    decisions.sort(
      (a, b) => roster.findIndex((x) => x.id === a.agentId) - roster.findIndex((x) => x.id === b.agentId),
    );
    emit({ type: "decisions", trigger, decisions });

    const wanting = decisions.filter((d) => d.reply).map((d) => d.agentId);
    for (const id of [...waiting.keys()]) if (!wanting.includes(id)) waiting.delete(id);
    tick += 1;
    for (const id of wanting) if (!waiting.has(id)) waiting.set(id, tick);
    if (!wanting.length) return finish("quiet");

    // Who speaks first: named by this message > the analyzer's lead (only while
    // nobody has answered the user yet) > waited longest > profile fit > roster order.
    const fitText = `${trigger.text}\n${turnUserText}`;
    const fit = new Map(wanting.map((id) => [id, fitScore(roster.find((a) => a.id === id)!, fitText)]));
    const firstAnswer = produced.length === 0;
    const named = (id: string) => addressedIds.has(id);
    const order = [...wanting].sort((a, b) => {
      const byName = Number(named(b)) - Number(named(a));
      if (byName) return byName;
      if (firstAnswer && leadId && (a === leadId || b === leadId)) return a === leadId ? -1 : 1;
      const waited = (waiting.get(a) ?? tick) - (waiting.get(b) ?? tick);
      if (waited) return waited;
      const better = (fit.get(b) || 0) - (fit.get(a) || 0);
      if (better) return better;
      return roster.findIndex((x) => x.id === a) - roster.findIndex((x) => x.id === b);
    });
    // Several people named in one message all answer that message. An open
    // message lets the first person who actually writes something speak, then
    // everyone decides again on that reply.
    let line: ChatLine | null = null;
    for (const id of order) {
      if (aborted()) return finish("aborted");
      if (produced.length >= cap) break;
      const agent = roster.find((a) => a.id === id)!;
      const why = decisions.find((d) => d.agentId === id)?.reason;
      const spokenLine = await speak(agent, trigger, addressedIds.has(agent.id) || everyone, undefined, {
        brief: greeting,
        everyone: turnEveryone && !greeting,
        mustAnswer: why === "fallback" || why === "everyone",
        action: Boolean(intent.action),
      });
      if (!spokenLine) {
        waiting.delete(id);
        continue;
      }
      line = spokenLine;
      if (!exclusive) break;
      if (opts.shouldStop?.(spokenLine)) return finish("aborted");
    }
    if (aborted()) return finish("aborted");
    if (!line) return finish("quiet");
    if (!exclusive && opts.shouldStop?.(line)) return finish("aborted");
  }
  return finish("cap");
}

/* ------------------------------------------------------------------------ */
/* Explicit pipeline detection                                               */
/* ------------------------------------------------------------------------ */

/**
 * A pipeline (fixed order of members) exists only when the user explicitly
 * asks for one; the turn analyzer decides that from the message's meaning and
 * parseTurnIntent validates it. No phrase list is involved.
 */
export function formatPipelineNotice(pipeline: ExplicitPipeline, roster: ChatAgent[], locale: "zh" | "en"): string {
  const names = pipeline.steps.map((step) => roster.find((agent) => agent.id === step.agentId)?.name || step.agentId);
  return locale === "en"
    ? `Collab: pipeline you asked for · ${names.join(" → ")}`
    : `协作：按你的要求走流水线 · ${names.join(" → ")}`;
}

/* ------------------------------------------------------------------------ */
/* Prompts                                                                   */
/* ------------------------------------------------------------------------ */

function speaker(line: ChatLine): string {
  return line.role === "user" ? "用户" : line.agentName || line.agentId || "成员";
}

/** The reply-or-silent decision for one member on one new message. */
export function buildReplyDecisionPrompt(input: JudgeInput & { context?: string }): string {
  const { agent, trigger } = input;
  const others = input.roster
    .filter((row) => row.id !== agent.id)
    .map((row) => `${row.name}${row.title ? `（${row.title}）` : ""}`)
    .join("、");
  const earlier = input.history.slice(0, -1).map((line) => ({
    name: speaker(line),
    text: String(line.text || "").replace(/\s+/g, " ").trim().slice(0, 400),
  }));
  const windowed = buildSlidingWindow(earlier, 16);
  const transcript = windowed.recent.length
    ? windowed.recent.map((row) => `【${row.name}】${row.text}`).join("\n")
    : "（之前没有消息）";
  const author = speaker(trigger);
  const facts = [
    trigger.role === "user" ? "最新消息来自用户。" : `最新消息来自成员「${author}」。`,
    input.addressed
      ? "最新消息点了你的名（或 @ 了全体），在问你或请你做事。"
      : input.addressedNames.length
        ? `最新消息点名的是：${input.addressedNames.join("、")}，不是你。`
        : "最新消息没有点名任何人。",
    input.repliesSoFar > 0 ? `你在这一轮已经发过 ${input.repliesSoFar} 条消息。` : "你在这一轮还没有发言。",
    trigger.role === "user" && !input.repliesThisTurn
      ? "还没有任何成员回答这条用户消息。只要它和你的职责相关、你能给出有用的回答，就回复，不要等别人先说。"
      : "",
    input.groupQuestion
      ? "用户是在问整个群的看法。如果你有自己的观点，尤其是和前面成员不同的看法或他们漏掉的角度，就回复。"
      : "",
    input.social ? "这是寒暄。如果你还没打过招呼，用一句短问候即可，不要展开成任务。" : "",
  ].filter(Boolean);
  return [
    `你是群聊成员「${agent.name}」${agent.title ? `（${agent.title}）` : ""}。`,
    agent.duty || agent.blurb ? `你的职责：${agent.duty || agent.blurb}` : "",
    others ? `群里的其他成员：${others}。` : "",
    "这是一个 Grok Bot 式的群聊：主持人不替你决定内容。没有被单独点名时，每个其他成员都看完整对话，自己决定回复还是沉默。被 @ 或直接称呼时，这一轮只有被点到的人有机会发言。",
    "同一阶段只有一个负责人。用户用“然后、接着、之后”串起来的实现步骤属于同一个人，不要把后半段接过来。只有最新消息点了你的名，或者用户本来就点名要求你做下一步，你才接手。",
    "只在以下情况回复（reply）：",
    "- 最新消息直接问你、点你的名、或把这件事交给你；",
    "- 还没有人回答用户，而这件事和你的职责相符；",
    "- 前面的消息里有错误或遗漏，你能明确纠正；",
    "- 用户请每位成员都回应，而你还没回应。",
    "以下情况保持沉默（silent）：",
    "- 你只会表示赞同、感谢、附和，或把别人说过的话换个说法再说一遍；",
    "- 别人已经在做这件事，你只是想把同一步再做一遍；",
    "- 最新消息点名的是别人；",
    "- 最新消息是在问用户、等用户回复，或者话题已经结束。",
    "沉默是默认选项。用户的问题还没人回答、又和你的职责相符时，要回复，不能全员沉默。已经有人回答之后，没有新内容就保持沉默，这一轮随之结束。",
    `事实：\n${facts.map((fact) => `- ${fact}`).join("\n")}`,
    input.context?.trim() ? `工作现场：\n${input.context.trim()}` : "",
    windowed.summary ? `更早的对话摘要：\n${windowed.summary}` : "",
    `之前的对话（从旧到新）：\n${transcript}`,
    trigger.role === "agent" && input.turnUserText
      ? `本轮用户的原始消息：\n${String(input.turnUserText).trim().slice(0, 1200)}`
      : "",
    `最新消息【${author}】：\n${String(trigger.text || "").trim().slice(0, 2000)}`,
    "只输出一个词：reply 或 silent。",
  ]
    .filter(Boolean)
    .join("\n");
}

/** reply → true, silent → false. Unknown output counts as silent. */
export function parseReplyDecision(raw: string): boolean {
  const text = String(raw || "")
    .replace(/<think>[\s\S]*?<\/think>/gi, "")
    .replace(/^[\s\S]*<\/think>/i, "")
    .trim();
  return parseSpeakVerdict(text, false);
}
