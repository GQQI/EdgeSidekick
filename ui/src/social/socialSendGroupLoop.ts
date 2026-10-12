// @ts-nocheck
import type { SocialChatMsg } from "./types";
type Slot = any;
type ChatLine = any;
type LoopEvent = any;
type JudgeInput = any;
type ReplyInput = any;
type ExplicitPipeline = any;
/* Extracted from socialSend.ts — runFreeGroupLoop */
/* eslint-disable @typescript-eslint/no-explicit-any */
export type createSocialGroupLoopCtx = Record<string, any>;

export function createSocialGroupLoop(ctx: createSocialGroupLoopCtx) {
  const {
    ac,
    attachments,
    buildReplyDecisionPrompt,
    chatWorkspacePath,
    cleanDisplayText,
    commitGroupMessage,
    commitRound,
    confirmationText,
    currentWorkSite,
    defaultMaxAgentMessages,
    formatPipelineNotice,
    groupId,
    hist,
    input,
    isActiveAgentPhase,
    isStaleSend,
    loadLocale,
    messages,
    noteAgentActivity,
    paint,
    parseReplyDecision,
    patchStore,
    pauseForUserInput,
    raw,
    rosterForPrompt,
    runGroupConversation,
    runOne,
    setAgentLive,
    setGroupBoard,
    streamChat,
    t,
    text,
    turn,
    uid,
    userMsg,
    visibleReplyAfterJudge,
  } = ctx;
  let committedMsgs = ctx.committedMsgs;
  let freeReply = ctx.freeReply;
  let freeStatus = ctx.freeStatus;
  let harnessNotices = ctx.harnessNotices;
  let liveBoard = ctx.liveBoard;
  let slots = ctx.slots;
  let turnPaused = ctx.turnPaused;

  return async () => {


      const locale = loadLocale() === "en" ? "en" : "zh";
      // Set only when the turn analyzer finds the user explicitly asked for an order.
      let pipeline: ExplicitPipeline | null = null;
      let loopTools = false;
      const priorMsgs = hist.filter(
        (m) => (m.role === "user" || m.role === "agent") && !m.readingStatus && String(m.text || "").trim(),
      );
      const toLine = (m: SocialChatMsg): ChatLine => ({
        id: m.id,
        role: m.role === "user" ? "user" : "agent",
        agentId: m.agentId,
        agentName: m.agentName,
        text: m.id === userMsg.id ? text : String(m.text || ""),
      });
      const toMsg = (line: ChatLine): SocialChatMsg => {
        const known = priorMsgs.find((m) => m.id === line.id);
        return {
          id: line.id,
          role: line.role,
          agentId: line.agentId,
          agentName: line.agentName,
          text: line.text,
          attachments: known && known.id !== userMsg.id ? known.attachments : undefined,
          at: known?.at || Date.now(),
        };
      };
      const slotOf = (agentId: string) => slots.find((s) => s.agent.id === agentId);
      const authorOf = (line: ChatLine) => (line.role === "user" ? "你" : line.agentName || "成员");
      const reasonText: Record<string, string> = {
        addressed: "被你点名，直接回复",
        everyone: "你请每位成员都回应",
        judge: "判断后决定回复",
        judge_failed: "判断失败",
        fallback: "无人主动回复，由最合适的成员回答",
        pipeline: "按你要求的流水线顺序发言",
        cap: "本轮发言次数已到上限",
      };
      const onLoopEvent = (event: LoopEvent) => {
        if (isStaleSend()) return;
        if (event.type === "intent") {
          if (event.intent.source === "fallback" && event.intent.fallbackReason !== "no_analyzer") {
            // Never silent: the model could not read the message, so only @/name
            // syntax is used and no pipeline is assumed.
            console.warn("[group-loop] turn analyzer unavailable:", event.intent.fallbackReason);
            ctx.freeStatus = freeStatus = "没能理解消息的指向，按 @ 点名 / 自由判断处理…";
          } else {
            ctx.freeStatus = freeStatus = "已理解这条消息，正在请成员判断…";
          }
          // Tools follow the model's reading of the message. A missed reading
          // stays a conversation: the host does not turn keywords into a work order.
          loopTools = Boolean(
            event.intent.source === "model" && (event.intent.pipeline || event.intent.action),
          );
          if (event.intent.pipeline) {
            pipeline = event.intent.pipeline;
            harnessNotices.push({
              id: uid("sys"),
              role: "system",
              text: formatPipelineNotice(pipeline, rosterForPrompt, locale),
              at: Date.now(),
              collaboration: true,
            });
          }
          paint();
        } else if (event.type === "judging") {
          for (const id of event.agentIds) {
            const s = slotOf(id);
            if (s) s.stage = "deciding";
          }
          ctx.freeStatus = freeStatus = event.agentIds.length
            ? `${event.agentIds.length} 位成员正在各自判断要不要回复${authorOf(event.trigger)}的新消息…`
            : freeStatus;
          setAgentLive((prev) => {
            const next = { ...prev };
            for (const id of event.agentIds) {
              next[id] = {
                ...(next[id] || { tools: [] }),
                phase: "deciding",
                tools: next[id]?.tools || [],
                summary: `正在判断要不要回复${authorOf(event.trigger)}的消息…`,
                updatedAt: Date.now(),
              };
            }
            return next;
          });
          paint();
        } else if (event.type === "decisions") {
          for (const d of event.decisions) {
            const s = slotOf(d.agentId);
            if (s && !d.reply) s.stage = "done";
            const agent = rosterForPrompt.find((a) => a.id === d.agentId);
            if (agent) {
              noteAgentActivity(
                agent,
                d.reply ? "decide" : "skip",
                d.reply
                  ? `${reasonText[d.reason] || "决定回复"}（${authorOf(event.trigger)}的消息）`
                  : `看过${authorOf(event.trigger)}的消息后决定不回复${d.reason === "cap" ? "（发言次数已到上限）" : ""}`,
              );
            }
          }
          setAgentLive((prev) => {
            const next = { ...prev };
            for (const d of event.decisions) {
              if (d.reply) continue;
              next[d.agentId] = {
                ...(next[d.agentId] || { tools: [] }),
                phase: "done",
                tools: next[d.agentId]?.tools || [],
                summary: "决定不回复",
                updatedAt: Date.now(),
              };
            }
            return next;
          });
          const yes = event.decisions.filter((d) => d.reply).map((d) => d.agentName);
          ctx.freeStatus = freeStatus = yes.length ? `${yes.join("、")} 想回复…` : "";
          paint();
        }
        try {
          console.debug("[group-loop]", event.type, event);
        } catch {}
      };
      const judge = async (input: JudgeInput, signal?: AbortSignal): Promise<boolean | null> => {
        let verdict = "";
        let failed = false;
        try {
          await streamChat(
            buildReplyDecisionPrompt({ ...input, context: currentWorkSite() }),
            `social-judge-${groupId || "g"}-${input.agent.id}-${uid("j")}`,
            {
              onEvent: () => {},
              onFinal: (out) => {
                verdict = String(out || "");
              },
              onError: () => {
                failed = true;
              },
            },
            signal,
            "agent",
            "",
            chatWorkspacePath,
            true,
            true,
            true,
          );
        } catch {
          failed = true;
        }
        if (signal?.aborted || isStaleSend()) return null;
        if (failed && !verdict.trim()) return null;
        return parseReplyDecision(verdict);
      };
      /** One hidden, tool-less model call that reads the user's message (audience / lead / explicit order). */
      const analyzeTurn = async (prompt: string, _input: unknown, signal?: AbortSignal): Promise<string | null> => {
        let out = "";
        ctx.freeStatus = freeStatus = "正在理解你的消息…";
        paint();
        const local = new AbortController();
        const timer = window.setTimeout(() => local.abort(), 20000);
        const onParent = () => local.abort();
        signal?.addEventListener("abort", onParent);
        try {
          await streamChat(
            prompt,
            `social-intent-${groupId || "g"}-${uid("i")}`,
            {
              onEvent: () => {},
              onFinal: (final) => {
                out = String(final || "");
              },
              onError: () => {},
            },
            local.signal,
            "agent",
            "",
            chatWorkspacePath,
            true,
            true,
            true,
            true,
          );
        } catch {
          return null;
        } finally {
          window.clearTimeout(timer);
          signal?.removeEventListener("abort", onParent);
        }
        if (signal?.aborted || isStaleSend()) return null;
        return out.trim() || null;
      };
      const reply = async (input: ReplyInput): Promise<string[]> => {
        const s = slotOf(input.agent.id);
        if (!s || ac.signal.aborted || turnPaused) return [];
        const latest = input.trigger;
        const stepIndex = input.step && pipeline ? pipeline.steps.indexOf(input.step) : -1;
        const nextStep = stepIndex >= 0 && pipeline ? pipeline.steps[stepIndex + 1] : undefined;
        s.id = uid("m");
        s.text = "";
        s.reasoning = "";
        s.silent = false;
        s.decided = false;
        s.replyConfirmed = true;
        s.streaming = true;
        s.stage = "executing";
        (s as { activityText?: string }).activityText = "";
        (s as { _clearedToSpeak?: boolean })._clearedToSpeak = true;
        (s as { _sentenceOnly?: boolean })._sentenceOnly = false;
        (s as any)._tasks = input.step ? [input.step.task] : [];
        ctx.freeReply = freeReply = {
          history: input.history.slice(0, -1).map(toMsg),
          latest: {
            author: latest.role === "user" ? "用户" : latest.agentName || "成员",
            isUser: latest.role === "user",
            text: latest.text,
          },
          addressed: input.addressed,
          brief: input.brief,
          everyone: input.everyone,
          mustAnswer: input.mustAnswer,
          action: input.action,
          step: input.step && pipeline
            ? {
                index: stepIndex + 1,
                total: pipeline.steps.length,
                task: input.step.task,
                nextName: nextStep ? rosterForPrompt.find((a) => a.id === nextStep.agentId)?.name : undefined,
              }
            : undefined,
          tools: loopTools,
        };
        setAgentLive((prev) => ({
          ...prev,
          [s.agent.id]: {
            ...prev[s.agent.id],
            phase: "executing",
            tools: prev[s.agent.id]?.tools || [],
            summary: "正在回复…",
            updatedAt: Date.now(),
          },
        }));
        paint();
        const before = committedMsgs.length;
        try {
          await runOne(s, 1);
        } finally {
          ctx.freeReply = freeReply = null;
        }
        if (ac.signal.aborted || isStaleSend()) return [];
        const texts = committedMsgs
          .slice(before)
          .filter((m) => m.agentId === s.agent.id)
          .map((m) => String(m.text || "").trim())
          .filter(Boolean);
        const finalText = visibleReplyAfterJudge(cleanDisplayText(String(s.text || "")));
        if (!s.silent && finalText && finalText !== t("socialEmptyReply") && commitGroupMessage(s, finalText)) {
          texts.push(finalText);
        }
        s.text = "";
        s.reasoning = "";
        s.silent = true;
        s.decided = true;
        s.streaming = false;
        s.reasoningStreaming = false;
        s.replyConfirmed = false;
        s.stage = "done";
        s.browserUrl = undefined;
        s.browserBusy = undefined;
        paint();
        return texts;
      };
      paint();
      const result = await runGroupConversation({
        roster: rosterForPrompt,
        history: priorMsgs.map(toLine),
        judge,
        reply,
        signal: ac.signal,
        maxAgentMessages: defaultMaxAgentMessages(rosterForPrompt.length),
        maxRepliesPerAgent: 2,
        analyzeTurn,
        onEvent: onLoopEvent,
        shouldStop: (line) =>
          line.role === "agent" && pauseForUserInput(line.agentName || "", confirmationText(String(line.agentId || ""), line.text)),
        newId: () => uid("m"),
      });
      if (isStaleSend()) return;
      ctx.freeStatus = freeStatus = "";
      for (const s of slots) {
        s.streaming = false;
        s.reasoningStreaming = false;
        if (s.stage !== "error") s.stage = "done";
        if (!s.decided) {
          s.decided = true;
          s.silent = !String(s.text || "").trim();
        }
      }
      setAgentLive((prev) => {
        const next = { ...prev };
        for (const s of slots) {
          const cur = next[s.agent.id];
          if (!cur || !isActiveAgentPhase(cur.phase)) continue;
          next[s.agent.id] = { ...cur, phase: "done", updatedAt: Date.now() };
        }
        return next;
      });
      if (groupId && liveBoard) {
        ctx.liveBoard = liveBoard = commitRound(liveBoard, raw || text);
        const board = liveBoard;
        patchStore((prev) => setGroupBoard(prev, groupId, board));
      }
      if (result.reason === "cap" && !turnPaused) {
        harnessNotices.push({
          id: uid("sys"),
          role: "system",
          text: `本轮成员已发言 ${result.messages.length} 条，达到上限后自动停止。`,
          at: Date.now(),
          collaboration: true,
        });
      }
      paint();
        };
}
