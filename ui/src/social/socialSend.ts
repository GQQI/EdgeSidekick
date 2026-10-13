// @ts-nocheck — extracted send pipeline; deps typed loosely to preserve behavior.
/** SocialShell send pipeline (group/DM turns). Factory keeps closures explicit. */
import { browserAct, fetchPlugins, streamChat } from "../api";
import { loadLocale } from "../i18n";
import { isWorkbenchLinkedAgent } from "./workbenchBridge";
import {
  appendRlTransition,
  applyOutcome,
  abstractWorkflow,
  classifySituation,
  ensureStrategy,
  strategyHint,
} from "./strategy";
import {
  applyHarnessOutcome,
  diagnoseHarness,
  formatHarnessContract,
  formatHarnessNotice,
  isDiscussionRequest,
  planCollaboration,
  shouldUseHarness,
  type CollaborationPlan,
} from "./harness";
import {
  buildReplyDecisionPrompt,
  defaultMaxAgentMessages,
  formatPipelineNotice,
  type ExplicitPipeline,
  parseReplyDecision,
  runGroupConversation,
  type ChatLine,
  type JudgeInput,
  type LoopEvent,
  type ReplyInput,
} from "./groupLoop";
import {
  buildGroupBrief,
  normalizeTaskKey,
  isLightweightSocialTurn,
  isCollectiveGreeting,
  isGroupGreeting,
  greetingFallback,
  groupTurnStatusLabel,
  hasUnfinishedActionCommitment,
  isExecutionRequested,
  isAuthorCallback,
  resolveAuthorId,
  isContinuationRequest,
  isScheduleRequest,
  isDirectAnswerTurn,
  isCapabilityQuestion,
  mergeExecutionTasks,
  doneTasksForExecution,
  canRunTasksInParallel,
  dutyScore,
  pickSoloSpeaker,
  planReactionOrder,
  maxReactionTurns,
  buildSpeakJudgePrompt,
  parseSpeakVerdict,
  visibleReplyAfterJudge,
  formatPeerDiscuss,
  tasksClaimedInText,
  isSelfAccept,
  extractNominees,
  agentsConfirmedUserTaskDone,
  agentAsksForUserInput,
  isPublicHttpUrl,
  msgFingerprint,
  isNearDuplicateBubble,
  workNoteFromTool,
  findHandoffAgentIds,
  findAskedAgentIds,
  discussHasOwner,
  findPendingDiscussIds,
  resolveExecSlots,
  coordinationAsksInText,
  formatGroupDiscussionMessage,
  sharedFileFromToolEvent,
  pickExecSlots,
} from "./turnLogic";
import {
  applyAgentOutcome,
  emptyBoard,
  commitConsensusFacts,
  autoExtractFacts,
  beginWorkingRound,
  boardProtocolHint,
  bumpWorkingEntry,
  commitRound,
  applyStandingRules,
  formatBoardForPrompt,
  parseAgentDecision,
  parseStandingRules,
  summarizeReply,
  cleanDisplayText,
} from "./sharedBoard";
import {
  agentById,
  agentByName,
  dmKey,
  ensureThread,
  groupKey,
  parseMentions,
  resolveGroupTurn,
  mentionsEveryone,
  appendAgentMemory,
  formatAgentMemoryForPrompt,
  saveSocialStore,
  uid,
  getGroupBoard,
  setGroupBoard,
  getDmActiveSession,
  getDmStreamNonce,
  dmMemoryContext,
} from "./store";
import { buildSocialBackendMessage, extractOwnReply, stripAgentTags } from "./buildPrompt";
import {
  isActiveAgentPhase,
  type AgentLivePhase,
  type AgentActivityKind,
  type AgentActivity,
  type AgentLiveState,
  type SocialAttachment,
} from "./socialLiveState";
import type {
  SocialAgent,
  SocialChatMsg,
  SocialGroup,
  SocialStore,
  SocialThreadKey,
} from "./types";

import type { SocialSendDeps } from "./socialSendTypes";
export type { SocialSendDeps } from "./socialSendTypes";
import { createSocialRunOne } from "./socialSendRunOne";
import { createSocialGroupLoop } from "./socialSendGroupLoop";
import { connectedPluginDigest } from "./pluginDigest";
import { runSocialSendWaves } from "./socialSendWaves";
export function createSocialSend(deps: SocialSendDeps) {
  const {
    store,
    setStore,
    view,
    input,
    setInput,
    attachments,
    setAttachments,
    setBusyThreads,
    setAgentLive,
    quote,
    setQuote,
    setCtx,
    setCtxByAgent,
    setMentionOpen,
    setBrowserOpen,
    setBrowserMini,
    setBrowserFull,
    browserHoldRef,
    browserUserClosedRef,
    setBrowserCard,
    threadScrollerRef,
    abortByThreadRef,
    threadKeyRef,
    workLogsRef,
    busyThreadsRef,
    clearEpochRef,
    patchStore,
    agents,
    groups,
    activeGroup,
    activeAgent,
    chatWorkspacePath,
    threadBusy,
    groupMembers,
    setThreadMessages,
    rewardNotesFor,
    buildTextWithAttachments,
    messages,
    t,
    sessionId,
    onToast,
    onSessionId,
    onOpenBrowserUrl,
    threadKey,
  } = deps;
  return async () => {
    const raw = input.trim();
    if ((!raw && attachments.length === 0) || !threadKey) return;
    if (view.kind !== "group" && view.kind !== "dm") return;
    // Group chat: a new user message interrupts the running round (the old
    // round is aborted below and can no longer write). DMs keep waiting.
    if (threadBusy && view.kind !== "group") return;

    const quoteText = quote;
    const pendingAttachments = attachments;
    const text = buildTextWithAttachments(
      (quoteText ? `【引用】\n${quoteText}\n【/引用】\n\n` : "") + (raw || t("socialAttach")),
    );
    const userMsg: SocialChatMsg = {
      id: uid("m"),
      role: "user",
      text: raw || pendingAttachments.map((a) => a.name).join(", "),
      quote: quoteText || undefined,
      attachments: pendingAttachments.map((a) => ({ name: a.name, path: a.path, kind: a.kind })),
      at: Date.now(),
    };
    setQuote(null);
    // Coordination cards are transient status/audit UI, never conversational
    // history for the next model turn.
    const hist = [
      ...messages
        .filter((m) => !m.readingStatus)
        .map((m) =>
          m.streaming || m.reasoningStreaming || m.reading
            ? { ...m, streaming: false, reasoningStreaming: false, reading: false }
            : m,
        ),
      userMsg,
    ];
    setThreadMessages(threadKey, hist);
    setInput("");
    setAttachments([]);
    setMentionOpen(false);
    const key = threadKey;
    const currentWorkSite = () => (workLogsRef.current[key] || []).slice(-6).join("\n");
    const rememberWork = (who: string, toolName: string, data: { args?: Record<string, unknown> | string; preview?: string; result?: string; content?: string; ok?: boolean; error?: unknown }) => {
      let args = data?.args;
      if (typeof args === "string") {
        try {
          args = JSON.parse(args) as Record<string, unknown>;
        } catch {
          args = undefined;
        }
      }
      const note = workNoteFromTool(who, toolName, {
        args: args && typeof args === "object" ? args : undefined,
        preview: String(data?.preview || ""),
        result: String(data?.result || data?.content || ""),
        ok: data?.ok !== false && !data?.error,
      });
      if (!note) return;
      const prev = workLogsRef.current[key] || [];
      if (prev[prev.length - 1] === note) return;
      const next = [...prev, note].slice(-12);
      workLogsRef.current = { ...workLogsRef.current, [key]: next };
      patchStore((s) => ({ ...s, workLogs: { ...(s.workLogs || {}), [key]: next } }));
    };
    browserUserClosedRef.current = false;
    browserHoldRef.current = false;
    setBusyThreads((prev) => ({ ...prev, [key]: true }));

    const rosterForPrompt =
      view.kind === "group" && activeGroup ? groupMembers(activeGroup) : agents;
    // Chat: every member reads each new message and may answer.
    // A work order still stays with one operator.
    const groupId = view.kind === "group" ? view.groupId : null;
    const turn =
      view.kind === "group"
        ? resolveGroupTurn(raw || text, rosterForPrompt)
        : null;
    const mentioned = turn?.addressed || parseMentions(raw || text, rosterForPrompt);
    const mentionedIds = new Set(mentioned.map((a) => a.id));
    // 「继续执行」没有新的点名时，还是交给刚才说话的那个人，不要换人。
    if (mentionedIds.size === 0 && !turn?.everyone && isContinuationRequest(raw || text)) {
      const lastSpeakerId = [...messages]
        .reverse()
        .find((row) => row.role === "agent" && row.agentId && !row.readingStatus)?.agentId;
      if (lastSpeakerId && rosterForPrompt.some((agent) => agent.id === lastSpeakerId)) {
        mentionedIds.add(lastSpeakerId);
      }
    }
    // 「谁写的谁来改」指向已经做过这件事的人，不要按职责再挑一个调试或审查。
    if (view.kind === "group" && mentionedIds.size === 0 && !turn?.everyone && isAuthorCallback(raw || text)) {
      const authorId = resolveAuthorId(
        rosterForPrompt,
        groupId ? getGroupBoard(store, groupId).tasks : [],
        workLogsRef.current[key] || [],
        messages,
      );
      if (authorId) mentionedIds.add(authorId);
    }
    const executionRequested = view.kind === "group" && isExecutionRequested(raw || text);
    const directAnswerTurn =
      view.kind === "group" && isDirectAnswerTurn(raw || text, mentioned.length) && !turn?.everyone;
    const lightweightSocialTurn =
      view.kind === "group" && isLightweightSocialTurn(raw || text) && !turn?.everyone;
    const collectiveGreeting = view.kind === "group" && isCollectiveGreeting(raw || text);
    const multiVoice =
      view.kind === "group" &&
      (collectiveGreeting ||
        Boolean(turn?.everyone) ||
        isDiscussionRequest(raw || text) ||
        /大家都|各位都|每个人|所有人|分别说|各自|一起说|都回复|都回答/.test(raw || text));
    const scheduleRequest = isScheduleRequest(raw || text);
    const targets =
      view.kind === "dm"
        ? activeAgent
          ? [activeAgent]
          : []
        : turn
          ? turn.targets
          : rosterForPrompt;
    // Group: @ names a lead. Every member still sees the message and may stay silent.
    const optionalReply = view.kind === "group" ? Boolean(turn?.optionalReply) : false;
    const turnExclusive = Boolean(turn?.exclusive);
    const addressedNames = (turn?.addressed || mentioned).map((a) => a.name);

    if (!targets.length) {
      setBusyThreads((prev) => {
        const n = { ...prev };
        delete n[key];
        return n;
      });
      onToast(t("socialEmptyReply"));
      return;
    }

    type Slot = {
      id: string;
      agent: (typeof targets)[number];
      text: string;
      streaming: boolean;
      silent?: boolean;
      decided?: boolean;
      /** Group optional: only true after DECISION:reply — gates bubble paint. */
      replyConfirmed?: boolean;
      reasoning?: string;
      reasoningStreaming?: boolean;
      /** Current lifecycle stage for status UI; independent from whether a bubble is visible. */
      stage: "reading" | "deciding" | "queued" | "executing" | "done" | "error";
      /** Live browser preview attached to this agent's bubble. */
      browserUrl?: string;
      browserBusy?: boolean;
    };
    const slots: Slot[] = targets.map((agent) => ({
      id: uid("m"),
      agent,
      text: "",
      // Group members start idle.  The decision pass wakes one at a time so
      // the status row describes the member currently evaluating the message,
      // rather than incorrectly claiming the whole group is executing.
      streaming: view.kind !== "group",
      silent: view.kind === "group",
      decided: false,
      replyConfirmed: false,
      reasoning: "",
      reasoningStreaming: false,
      stage: view.kind === "group" ? "reading" : "executing",
    }));
    const noteAgentActivity = (
      agent: { id?: string; name: string },
      kind: AgentActivityKind,
      detail: string,
    ) => {
      if (view.kind !== "group" || !agent.id) return;
      const clean = String(detail || "").replace(/\s+/g, " ").trim();
      if (!clean) return;
      setAgentLive((prev) => {
        const current = prev[agent.id!] || {
          phase: "reading" as AgentLivePhase,
          tools: [],
          updatedAt: Date.now(),
        };
        const activity = [...(current.activity || [])];
        const last = activity[activity.length - 1];
        // Streaming tool deltas are updates to one operation, not a flood of
        // indistinguishable task-detail rows.
        if (last && last.kind === kind) {
          last.detail = clean.slice(0, 180);
          last.at = Date.now();
        } else {
          activity.push({ id: uid("activity"), kind, detail: clean.slice(0, 180), at: Date.now() });
        }
        return {
          ...prev,
          [agent.id!]: { ...current, activity: activity.slice(-24), updatedAt: Date.now() },
        };
      });
    };
    // Work orders keep one operator. Conversation asks the whole roster.
    // Greetings and ordinary chat are a shared floor. One speaker is only
    // for a work order, where a second mouth would repeat the same job.
    const reactionFloor = view.kind === "group" && !executionRequested;
    const directAnswerAgentId =
      directAnswerTurn && mentionedIds.size === 0 && !reactionFloor
        ? pickSoloSpeaker(slots.map((slot) => slot.agent), raw || text)?.id
        : undefined;
    const singleVoice = view.kind === "group" && !multiVoice && mentionedIds.size === 0 && !reactionFloor;
    const soloSpeakerId = singleVoice
      ? directAnswerAgentId || pickSoloSpeaker(slots.map((slot) => slot.agent), raw || text)?.id
      : undefined;
    if (view.kind === "group") {
      setAgentLive((prev) => {
        const next = { ...prev };
        for (const slot of slots) {
          next[slot.agent.id] = {
            phase: "reading",
            tools: [],
            activity: [{ id: uid("activity"), kind: "read", detail: "已收到消息，各自判断要不要回复", at: Date.now() }],
            summary: "正在读取本轮消息…",
            updatedAt: Date.now(),
          };
        }
        return next;
      });
    }

    // Per-thread controllers so a group turn and a DM can run together;
    // starting one must not steal / null the other's AbortController.
    try {
      abortByThreadRef.current[key]?.abort();
    } catch {}
    const ac = new AbortController();
    abortByThreadRef.current[key] = ac;
    /** Set when we pause for the human — late stream deltas must not revive「思考中」. */
    let turnPaused = false;
    // Shared by reference with the runner factories; a plain `let` would be
    // captured by value when they are created.
    const turnState: { turnPaused: boolean; freeReply: any; harnessPlan?: any; peerDiscuss?: any[]; status?: string; ended?: boolean } = {
      turnPaused: false,
      freeReply: null,
    };
    const pauseForUserInput = (agentName: string, text: string, force = false): boolean => {
      if (ac.signal.aborted || turnPaused) return true;
      if (!force && !agentAsksForUserInput(text)) return false;
      turnPaused = true;
      turnState.turnPaused = true;
      try {
        ac.abort();
      } catch {}
      // Clear "输出中 / 进行中" so the UI does not look stuck while waiting on the user.
      for (const s of slots) {
        s.streaming = false;
        s.reasoningStreaming = false;
        if (s.stage === "reading" || s.stage === "queued" || s.stage === "deciding") s.stage = "done";
        if (!s.decided) {
          s.decided = true;
          s.silent = !String(s.text || "").trim();
        }
      }
      setAgentLive((prev) => {
        const next: typeof prev = { ...prev };
        for (const id of Object.keys(next)) {
          const cur = next[id];
          if (!cur) continue;
          next[id] = {
            ...cur,
            phase: isActiveAgentPhase(cur.phase) ? "done" : cur.phase,
            summary: isActiveAgentPhase(cur.phase) ? "已暂停，等待你回复" : cur.summary,
            updatedAt: Date.now(),
          };
        }
        return next;
      });
      const notice = {
        id: uid("sys"),
        role: "system" as const,
        text: `${agentName || "智能体"}需要你确认或补充信息。已暂停本轮，请在输入框回复后继续。`,
        at: Date.now(),
      };
      try {
        committedMsgs.push(notice as any);
        paint();
      } catch {}
      return true;
    };
    const confirmationText = (agentId: string, spoken: string) => {
      if (agentAsksForUserInput(spoken)) return spoken;
      for (let i = committedMsgs.length - 1; i >= 0; i -= 1) {
        const row = committedMsgs[i];
        if (row.agentId === agentId && agentAsksForUserInput(row.text || "")) return row.text || "";
      }
      return spoken;
    };

    const sendEpoch = clearEpochRef.current[key] || 0;
    /** A newer send on this thread took over (user sent a new message). */
    const isSuperseded = () => {
      const current = abortByThreadRef.current[key];
      return Boolean(current && current !== ac);
    };
    const isStaleSend = () => (clearEpochRef.current[key] || 0) !== sendEpoch || isSuperseded();
    // Guarded write: refuse inside the setStore updater if clear bumped epoch after paint began.
    const writeThreadMessages = (msgs: SocialChatMsg[]) => {
      if (isStaleSend()) return;
      setStore((prev) => {
        if ((clearEpochRef.current[key] || 0) !== sendEpoch || isSuperseded()) return prev;
        const next = structuredClone(prev);
        const scoreById = new Map(
          (prev.threads[key] || []).filter((row) => row.score).map((row) => [row.id, row.score] as const),
        );
        next.threads[key] = msgs.map((row) => {
          const kept = scoreById.get(row.id);
          if (!kept || row.score) return row;
          return { ...row, score: kept };
        });
        saveSocialStore(next);
        return next;
      });
    };

    // Plan-wave bubbles stay visible while execute wave runs with new slot ids.
    let committedMsgs: Array<{
      id: string;
      role: "agent";
      agentId: string;
      agentName: string;
      text: string;
      streaming: boolean;
      reading: boolean;
      attachments?: Array<{ name: string; path: string; kind?: string }>;
      reasoning?: string;
      reasoningStreaming?: boolean;
      browserUrl?: string;
      browserBusy?: boolean;
      at: number;
    }> = [];
    const harnessNotices: Array<{ id: string; role: "system"; text: string; at: number; collaboration: true }> = [];
    // Group messages always use the free-form reply loop (groupLoop.ts). No
    // router, no default pipeline; a pipeline runs only when the user asks.
    const freeGroupMode: boolean = view.kind === "group";
    let freeStatus = freeGroupMode ? `${targets.length} 位成员已看到消息…` : "";
    /** Set while one member writes its reply inside the free-form loop. */
    let freeReply: null | {
      history: SocialChatMsg[];
      latest: { author: string; isUser: boolean; text: string };
      addressed: boolean;
      step?: { index: number; total: number; task: string; nextName?: string };
      brief?: boolean;
      everyone?: boolean;
      mustAnswer?: boolean;
      tools: boolean;
    } = null;
    let harnessPlan: CollaborationPlan | null = null;
    let harnessRepair = "";
    let harnessRepaired = false;
    let harnessPrior: ReturnType<typeof diagnoseHarness> | null = null;
    const sharedFileKeys = new Set<string>();
    const commitGroupMessage = (slot: Slot, message: string) => {
      const text = cleanDisplayText(message).trim();
      if (!text || isNearDuplicateBubble(slot.agent.id, text, committedMsgs)) return false;
      committedMsgs.push({
        id: slot.id,
        role: "agent",
        agentId: slot.agent.id,
        agentName: slot.agent.name,
        text,
        streaming: false,
        reading: false,
        reasoning: (slot.reasoning || "").trim() || undefined,
        reasoningStreaming: false,
        at: Date.now(),
      });
      return true;
    };
    const commitSharedFileMessage = (slot: Slot, path: string) => {
      if (view.kind !== "group") return false;
      const cleanPath = String(path || "").trim();
      if (!cleanPath) return false;
      const dedupeKey = `${slot.agent.id}::${cleanPath.toLowerCase()}`;
      if (sharedFileKeys.has(dedupeKey)) return false;
      sharedFileKeys.add(dedupeKey);
      const name = cleanPath.replace(/\\/g, "/").split("/").filter(Boolean).pop() || cleanPath;
      committedMsgs.push({
        id: uid("m"),
        role: "agent",
        agentId: slot.agent.id,
        agentName: slot.agent.name,
        text: `已将文件共享到群里：${name}`,
        attachments: [{ name, path: cleanPath, kind: "file" }],
        streaming: false,
        reading: false,
        at: Date.now(),
      });
      noteAgentActivity(slot.agent, "tool", `已共享文件：${cleanPath}`);
      return true;
    };
    const flushSlotsToCommitted = () => {
    const seen = new Set(committedMsgs.map((m) => msgFingerprint(String(m.agentId || ""), String(m.text || ""))));
    const tryPush = (msg: (typeof committedMsgs)[number]) => {
      const fp = msgFingerprint(String(msg.agentId || ""), String(msg.text || ""));
      if (fp.endsWith("::") || seen.has(fp)) return false;
      if (isNearDuplicateBubble(String(msg.agentId || ""), String(msg.text || ""), committedMsgs)) return false;
      seen.add(fp);
      committedMsgs.push(msg);
      return true;
    };

      for (const s of slots) {
        if (s.silent) continue;
        const rawText = cleanDisplayText(String(s.text || ""));
        const reason = (s.reasoning || "").trim();
        if (!rawText && !reason) continue;
        // Skip if this bubble was already committed (prevents identical double paint).
        if (committedMsgs.some((m) => m.id === s.id || (m.agentId === s.agent.id && m.text === rawText))) {
          s.text = "";
          s.reasoning = "";
          s.streaming = false;
          s.silent = true;
          s.decided = true;
          s.browserUrl = undefined;
          s.browserBusy = undefined;
          s.replyConfirmed = false;
          continue;
        }
        tryPush({
          id: s.id,
          role: "agent",
          agentId: s.agent.id,
          agentName: s.agent.name,
          text: rawText,
          streaming: false,
          reading: false,
          reasoning: reason || undefined,
          reasoningStreaming: false,
          browserUrl: s.browserUrl || undefined,
          browserBusy: s.browserBusy,
          at: Date.now(),
        });
        // Clear slot after commit so paint([...committedMsgs, ...replySlots]) cannot draw it twice.
        s.text = "";
        s.reasoning = "";
        s.streaming = false;
        s.silent = true;
        s.decided = true;
        s.browserUrl = undefined;
        s.browserBusy = undefined;
        s.replyConfirmed = false;
      }
    };
    const readingStatusId = `reading-${key}`;
    const paint = () => {
      if (isStaleSend()) return;
      const pending = slots.filter((s) => !s.decided && !s.replyConfirmed);
      const committedIds = new Set(committedMsgs.map((m) => m.id));
      const committedFingerprints = new Set(
        committedMsgs.map((m) => `${m.agentId}::${(m.text || "").trim()}`),
      );
      const replySlots = slots.filter((s) => {
        if (s.silent && !s.browserUrl) return false;
        // The first group pass contains protocol tokens while it streams.
        // Its finished natural-language message is committed separately, so
        // the user sees a stable bubble rather than a half-parsed placeholder.
        if ((s as any)._suppressDecisionBubble) return false;
        if (committedIds.has(s.id)) return false;
        const rawPreview = cleanDisplayText(String(s.text || "")).trim();
        if (rawPreview && committedFingerprints.has(`${s.agent.id}::${rawPreview}`)) return false;
        // Group: never paint until DECISION:reply confirmed — stops thinking flash + vanish.
        if (optionalReply && !s.replyConfirmed && !(s.decided && !s.silent) && !s.browserUrl) return false;
        const rawText = cleanDisplayText(
          s.decided
            ? String(s.text || "")
            : String(extractOwnReply(s.text, s.agent.name) || stripAgentTags(s.text || "") || ""),
        );
        const reason = s.replyConfirmed || s.decided ? (s.reasoning || "").trim() : "";
        // Keep the in-flight bubble mounted while streaming once reply is confirmed,
        // even if a single delta briefly yields empty extractOwnReply.
        if (s.browserUrl) return true;
        // Mount the bubble as soon as this member is speaking, including while
        // a tool is still running, so the reply is not a single dump afterwards.
        if (s.streaming && s.replyConfirmed && !(s as any)._suppressDecisionBubble) return true;
        if (s.streaming && s.replyConfirmed && (rawText || reason || rawPreview)) return true;
        return !!(rawText || reason);
      });
      // The status row is driven by lifecycle state, not a fixed member count.
      // It exposes both the current member and the members waiting to read the
      // same turn, while keeping actual answer bubbles independent.
      const freeSpeakers = freeGroupMode ? slots.filter((s) => s.streaming && s.stage === "executing") : [];
      const freeSpeaker = freeSpeakers[0];
      const freeActivity = freeSpeaker ? String((freeSpeaker as { activityText?: string }).activityText || "").trim() : "";
      const replied = committedMsgs.some((m) => String(m.text || "").trim());
      // "N 位成员已看到消息" is only the wait before the first reply. Once a
      // bubble is on screen and nobody is still writing, the loader must go.
      let statusLabel = "";
      if (freeGroupMode && !turnState.ended) {
        if (freeSpeakers.length > 1) statusLabel = `${freeSpeakers.map((s) => s.agent.name).join("、")} 正在同时处理…`;
        else if (freeSpeaker) statusLabel = `${freeSpeaker.agent.name}${freeActivity ? `：${freeActivity}` : " 正在回复…"}`;
        else if (!replied) statusLabel = turnState.status || freeStatus;
      } else if (!freeGroupMode) statusLabel = groupTurnStatusLabel(
        slots.map((s) => ({
          name: s.agent.name,
          stage: s.stage,
          streaming: s.streaming,
          activityText: (s as { activityText?: string }).activityText,
        })),
      );
      // Keep only a lightweight typing/read indicator in the transcript.
      // Detailed work activity lives in the member task panel; actual group
      // communication is committed below as ordinary agent bubbles.
      const statusMsgs = statusLabel
        ? [{ id: readingStatusId, role: "system" as const, text: statusLabel, readingStatus: true, at: Date.now() }]
        : [];
      if (isStaleSend()) return;
      // Loader always last → sits under the newest bubbles.
      writeThreadMessages([
        ...hist,
        ...harnessNotices,
        ...committedMsgs,
        ...replySlots.map((s) => {
          const rawText = cleanDisplayText(
            s.decided
              ? String(s.text || "")
              : String(extractOwnReply(s.text, s.agent.name) || stripAgentTags(s.text || "") || ""),
          );
          const reason =
            s.replyConfirmed || s.decided ? (s.reasoning || "").trim() : "";
          return {
            id: s.id,
            role: "agent" as const,
            agentId: s.agent.id,
            agentName: s.agent.name,
            text: rawText,
            streaming: s.streaming,
            reading: false,
            reasoning: reason || undefined,
            reasoningStreaming: !turnPaused && !!s.reasoningStreaming && !!(s.replyConfirmed || s.decided),
            browserUrl: s.browserUrl || undefined,
            browserBusy: s.browserBusy,
            at: Date.now(),
          };
        }),
        ...statusMsgs,
      ]);
      requestAnimationFrame(() => {
        const box = threadScrollerRef.current;
        if (!box) return;
        const dist = box.scrollHeight - box.scrollTop - box.clientHeight;
        // Only pin inside the thread scroller — never scrollIntoView (that
        // scrolls ancestor pages and feels like the chat jumps upward).
        if (dist > 120) return;
        box.scrollTop = box.scrollHeight;
      });
    };
    paint(); // one shared reading status line — no per-agent bubbles yet


    let liveBoard = groupId ? getGroupBoard(store, groupId) : null;
    if (groupId && liveBoard && optionalReply) {
      liveBoard = beginWorkingRound(liveBoard, raw || text, targets);
      patchStore((prev) => setGroupBoard(prev, groupId, liveBoard!));
    }

    let peerDiscuss: Array<{ agentId: string; agentName: string; text: string }> = [];
    const yieldToForegroundDm = async () => {
      // Group turns share the same model quota; pause between agents while a
      // foreground DM is busy so the DM first reply is not stuck behind the queue.
      if (!String(key).startsWith("group:")) return;
      while (!ac.signal.aborted) {
        const fg = threadKeyRef.current;
        if (!fg || !String(fg).startsWith("dm:")) break;
        if (!busyThreadsRef.current[fg]) break;
        await new Promise((r) => setTimeout(r, 200));
      }
    };

    const runOne = createSocialRunOne({ ac, activeGroup, addressedNames, appendAgentMemory, applyAgentOutcome, applyStandingRules, browserHoldRef, browserUserClosedRef, buildGroupBrief, buildSocialBackendMessage, bumpWorkingEntry, chatWorkspacePath, cleanDisplayText, collectiveGreeting, commitSharedFileMessage, committedMsgs, coordinationAsksInText, currentWorkSite, directAnswerAgentId, directAnswerTurn, dmMemoryContext, doneTasksForExecution, extractOwnReply, formatAgentMemoryForPrompt, formatBoardForPrompt, formatHarnessContract, formatPeerDiscuss, freeReply, getDmActiveSession, getDmStreamNonce, groupId, groups, harnessPlan, harnessRepair, isNearDuplicateBubble, isStaleSend, isWorkbenchLinkedAgent, key, lightweightSocialTurn, liveBoard, mentionedIds, mergeExecutionTasks, messages, noteAgentActivity, onOpenBrowserUrl, onSessionId, onToast, optionalReply, paint, parseAgentDecision, parseStandingRules, patchStore, pauseForUserInput, peerDiscuss, raw, rememberWork, rewardNotesFor, rosterForPrompt, scheduleRequest, sessionId, setAgentLive, setBrowserCard, setCtx, setCtxByAgent, setGroupBoard, sharedFileFromToolEvent, soloSpeakerId, store, streamChat, stripAgentTags, summarizeReply, t, targets, text, threadKey, threadKeyRef, turn, turnPaused, turnState, uid, view, visibleReplyAfterJudge, yieldToForegroundDm });

    const commitBoardFromSlots = (waveSlots: Slot[]) => {
      if (!groupId || !liveBoard) return;
      let board = liveBoard;
      for (const s of waveSlots) {
        const facts = ((s as any)._facts as string[]) || [];
        const tasks = ((s as any)._tasks as string[]) || [];
        const doneTasks = ((s as any)._doneTasks as string[]) || [];
        const forgetFacts = ((s as any)._forgetFacts as string[]) || [];
        // Tasks update live; FACTS wait for majority consensus below.
        board = applyAgentOutcome(
          board,
          s.agent,
          {
            action: s.silent ? "silent" : "reply",
            text: s.text || "",
            facts: [],
            tasks,
            doneTasks,
            forgetFacts: [],
          },
          { autoExtract: false, writeFacts: false },
        );
        board = bumpWorkingEntry(board, {
          agentId: s.agent.id,
          agentName: s.agent.name,
          action: s.silent ? "silent" : "reply",
          summary: s.silent ? "" : summarizeReply(stripAgentTags(s.text)),
          at: Date.now(),
        });
      }
      const proposals = waveSlots.map((s) => {
        const explicit = ((s as any)._facts as string[]) || [];
        const extracted =
          !s.silent && explicit.length === 0 ? autoExtractFacts(s.text || "") : [];
        return { agent: s.agent, facts: [...explicit, ...extracted] };
      });
      // Majority of this turn's invited agents must agree before durable write.
      board = commitConsensusFacts(board, proposals, Math.max(waveSlots.length, targets.length));

      board = commitRound(board, raw || text);
      liveBoard = board;
      patchStore((prev) => setGroupBoard(prev, groupId, board));
    };

    /**
     * Free-form group chat: every new message (user or member) → every other
     * member decides reply / silent with the full history → one reply at a
     * time → repeat until a message gets no volunteer, a cap, or an abort.
     */
    const runFreeGroupLoop = createSocialGroupLoop({ pluginDigest: () => connectedPluginDigest(fetchPlugins), ac, attachments, buildReplyDecisionPrompt, chatWorkspacePath, cleanDisplayText, commitGroupMessage, commitRound, committedMsgs, confirmationText, currentWorkSite, getGroupBoard, defaultMaxAgentMessages, formatPipelineNotice, freeReply, freeStatus, groupId, harnessNotices, hist, input, isActiveAgentPhase, isStaleSend, liveBoard, loadLocale, messages, noteAgentActivity, paint, parseReplyDecision, patchStore, pauseForUserInput, raw, rosterForPrompt, runGroupConversation, runOne, setAgentLive, setGroupBoard, slots, streamChat, t, text, turn, turnPaused, turnState, uid, userMsg, visibleReplyAfterJudge });

    const currentHarnessDiagnosis = () => {
      if (!harnessPlan) return null;
      return diagnoseHarness({
        plan: harnessPlan,
        openTasks: (liveBoard?.tasks || []).filter((task) => task.status === "open").length,
        agentResults: slots.map((s) => ({
          agentId: s.agent.id,
          role: harnessPlan!.assignments.find((item) => item.agentId === s.agent.id)?.role || "other",
          texts: [
            ...committedMsgs.filter((m) => m.agentId === s.agent.id).map((m) => String(m.text || "")),
            cleanDisplayText(String(s.text || "")),
          ].map((item) => item.trim()).filter(Boolean),
          goalSatisfied: (s as { _goalKnown?: boolean })._goalKnown
            ? (s as { _goalSatisfied?: boolean })._goalSatisfied === true
            : null,
          goalBlocked: Boolean(
            (s as { _goalKnown?: boolean })._goalKnown && (s as { _goalBlocked?: boolean })._goalBlocked,
          ),
          toolErrors: Number((s as { _toolErrors?: number })._toolErrors || 0),
        })),
      });
    };

    try {
      if (freeGroupMode) {
        try {
          await runFreeGroupLoop();
        } finally {
          turnState.ended = true;
        }
      } else {
        await runSocialSendWaves({ abortByThreadRef, abstractWorkflow, ac, activeGroup, agentAsksForUserInput, agents, agentsConfirmedUserTaskDone, appendRlTransition, applyHarnessOutcome, applyOutcome, browserHoldRef, buildSpeakJudgePrompt, canRunTasksInParallel, chatWorkspacePath, classifySituation, cleanDisplayText, collectiveGreeting, commitBoardFromSlots, commitGroupMessage, committedMsgs, confirmationText, currentHarnessDiagnosis, currentWorkSite, directAnswerTurn, discussHasOwner, dutyScore, ensureStrategy, executionRequested, extractNominees, findHandoffAgentIds, findPendingDiscussIds, flushSlotsToCommitted, formatGroupDiscussionMessage, formatHarnessNotice, greetingFallback, groupId, groups, harnessNotices, harnessPlan, harnessPrior, harnessRepair, harnessRepaired, hasUnfinishedActionCommitment, isActiveAgentPhase, isAuthorCallback, isCapabilityQuestion, isDiscussionRequest, isGroupGreeting, isLightweightSocialTurn, isNearDuplicateBubble, isStaleSend, isSuperseded, key, lightweightSocialTurn, liveBoard, loadLocale, maxReactionTurns, mentionedIds, messages, msgFingerprint, multiVoice, noteAgentActivity, optionalReply, paint, parseSpeakVerdict, patchStore, pauseForUserInput, peerDiscuss, planCollaboration, planReactionOrder, reactionFloor, resolveExecSlots, rosterForPrompt, runOne, scheduleRequest, setAgentLive, setBrowserCard, setBrowserFull, setBrowserMini, setBrowserOpen, setBusyThreads, shouldUseHarness, slots, soloSpeakerId, store, streamChat, t, targets, tasksClaimedInText, text, threadKey, threadKeyRef, turn, turnExclusive, turnPaused, turnState, uid, view, visibleReplyAfterJudge });
      }
      // Members who were never called must not stay on "reading" after the turn ends.
      for (const s of slots) {
        if (s.stage !== "reading" && s.stage !== "queued") continue;
        s.stage = "done";
        s.streaming = false;
        if (!s.decided) {
          s.decided = true;
          s.silent = !String(s.text || "").trim();
        }
      }
      // Keep finished reply bubbles; do not leave UI on a transient empty paint.
      try {
        paint();
        if (
          optionalReply &&
          slots.every((s) => s.decided) &&
          slots.every((s) => s.silent) &&
          committedMsgs.length === 0
        ) {
          writeThreadMessages([
            ...hist,
            ...harnessNotices,
            ...committedMsgs,
            {
              id: `noreply-${Date.now()}`,
              role: "system" as const,
              text: t("socialNobodyReplied"),
              at: Date.now(),
            },
          ]);
        }
      } catch {}
    } catch (err) {
      console.error(err);
      if (freeGroupMode && !isStaleSend()) {
        harnessNotices.push({
          id: uid("sys"),
          role: "system",
          text: `这一轮出错了（${String((err as Error)?.message || err).slice(0, 200)}），请重试。`,
          at: Date.now(),
          collaboration: true,
        });
        try {
          paint();
        } catch {}
      }
    } finally {
      if (!isStaleSend()) {
        turnState.ended = true;
        setStore((prev) => {
          const rows = prev.threads[key] || [];
          if (!rows.some((row) => row.readingStatus)) return prev;
          const next = structuredClone(prev);
          next.threads[key] = rows.filter((row) => !row.readingStatus);
          saveSocialStore(next);
          return next;
        });
      }
      setBusyThreads((prev) => {
        const n = { ...prev };
        delete n[key];
        return n;
      });
      if (abortByThreadRef.current[key] === ac) delete abortByThreadRef.current[key];
    }
  };
}
