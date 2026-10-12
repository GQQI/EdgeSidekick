// @ts-nocheck
import type { SocialChatMsg } from "./types";
type Slot = any;
type ChatLine = any;
type LoopEvent = any;
type JudgeInput = any;
type ReplyInput = any;
type ExplicitPipeline = any;
/* Group wave / harness orchestration extracted from socialSend.ts */
/* eslint-disable @typescript-eslint/no-explicit-any */
export type SocialSendWavesCtx = Record<string, any>;

export async function runSocialSendWaves(ctx: SocialSendWavesCtx): Promise<void> {
  const {
    abortByThreadRef,
    abstractWorkflow,
    ac,
    activeGroup,
    agentAsksForUserInput,
    agents,
    agentsConfirmedUserTaskDone,
    appendRlTransition,
    applyHarnessOutcome,
    applyOutcome,
    browserHoldRef,
    buildSpeakJudgePrompt,
    canRunTasksInParallel,
    chatWorkspacePath,
    classifySituation,
    cleanDisplayText,
    collectiveGreeting,
    commitBoardFromSlots,
    commitGroupMessage,
    confirmationText,
    currentHarnessDiagnosis,
    currentWorkSite,
    directAnswerTurn,
    discussHasOwner,
    dutyScore,
    ensureStrategy,
    executionRequested,
    extractNominees,
    findHandoffAgentIds,
    findPendingDiscussIds,
    flushSlotsToCommitted,
    formatGroupDiscussionMessage,
    formatHarnessNotice,
    greetingFallback,
    groupId,
    groups,
    hasUnfinishedActionCommitment,
    isActiveAgentPhase,
    isAuthorCallback,
    isCapabilityQuestion,
    isDiscussionRequest,
    isGroupGreeting,
    isLightweightSocialTurn,
    isNearDuplicateBubble,
    isStaleSend,
    isSuperseded,
    key,
    lightweightSocialTurn,
    loadLocale,
    maxReactionTurns,
    mentionedIds,
    messages,
    msgFingerprint,
    multiVoice,
    noteAgentActivity,
    optionalReply,
    paint,
    parseSpeakVerdict,
    patchStore,
    pauseForUserInput,
    planCollaboration,
    planReactionOrder,
    reactionFloor,
    resolveExecSlots,
    rosterForPrompt,
    runOne,
    scheduleRequest,
    setAgentLive,
    setBrowserCard,
    setBrowserFull,
    setBrowserMini,
    setBrowserOpen,
    setBusyThreads,
    shouldUseHarness,
    soloSpeakerId,
    store,
    streamChat,
    t,
    targets,
    tasksClaimedInText,
    text,
    threadKey,
    threadKeyRef,
    turn,
    turnExclusive,
    uid,
    view,
    visibleReplyAfterJudge,
  } = ctx;
  let committedMsgs = ctx.committedMsgs;
  let harnessNotices = ctx.harnessNotices;
  let harnessPlan = ctx.harnessPlan;
  let harnessPrior = ctx.harnessPrior;
  let harnessRepair = ctx.harnessRepair;
  let harnessRepaired = ctx.harnessRepaired;
  let liveBoard = ctx.liveBoard;
  let peerDiscuss = ctx.peerDiscuss;
  let slots = ctx.slots;
  let turnPaused = ctx.turnPaused;

      // Wave 1 DISCUSS (open floor): tools off — agents talk / hand off / claim TASKs.
      // Multi-round sequential discuss: first full pass, then follow-ups for
      // @确认 / nominees until someone accepts or we hit the round cap.
      ctx.peerDiscuss = peerDiscuss = [];
      const discussRoster = rosterForPrompt.map((a) => ({ id: a.id, name: a.name }));
      const judgeWhetherToSpeak = async (s: (typeof slots)[number]) => {
        const prior = peerDiscuss
          .map((row) => ({ name: row.agentName, text: cleanDisplayText(row.text) }))
          .filter((row) => row.text);
        const history = messages
          .filter((m) => !m.readingStatus && (m.role === "user" || m.role === "agent"))
          .map((m) => ({
            role: m.role,
            name: m.role === "user" ? "用户" : m.agentName || "成员",
            text: cleanDisplayText(m.text || "").replace(/\s+/g, " ").trim().slice(0, 180),
          }))
          .filter((m) => m.text)
          .slice(-24);
        const greeting = collectiveGreeting || isLightweightSocialTurn(raw || text);
        const capability = isCapabilityQuestion(raw || text);
        const addressed = mentionedIds.has(s.agent.id) || Boolean(turn?.everyone);
        let verdict = "";
        let failed = false;
        noteAgentActivity(s.agent, "decide", "已看到本轮消息，正在判断要不要发言");
        try {
          await streamChat(
            buildSpeakJudgePrompt({
              name: s.agent.name,
              duty: s.agent.duty || s.agent.blurb,
              userText: raw || text,
              prior,
              history,
              addressed,
              greeting,
              capability,
              context: currentWorkSite(),
            }),
            `social-judge-${groupId || "g"}-${s.agent.id}-${s.id}`,
            {
              onEvent: () => {},
              onFinal: (out) => {
                verdict = String(out || "");
              },
              onError: () => {
                failed = true;
              },
            },
            ac.signal,
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
        if (ac.signal.aborted || isStaleSend()) return false;
        if (failed && !verdict.trim()) return prior.length === 0;
        return parseSpeakVerdict(verdict, prior.length === 0);
      };
      const runDiscussTurn = async (s: (typeof slots)[number], execute = false): Promise<string> => {
        // Fresh bubble id so follow-up turns don't collide with flushed ones.
        s.id = uid("m");
        s.text = "";
        s.reasoning = "";
        s.streaming = false;
        s.stage = "deciding";
        s.silent = false;
        s.decided = false;
        s.replyConfirmed = false;
        s.reasoningStreaming = false;
        paint();
        const forceSpeak = Boolean((s as { _forceSpeak?: boolean })._forceSpeak);
        const speak = forceSpeak || (await judgeWhetherToSpeak(s));
        if (!speak) {
          s.silent = true;
          s.decided = true;
          s.stage = "done";
          s.streaming = false;
          s.text = "";
          s.reasoning = "";
          setAgentLive((prev) => ({
            ...prev,
            [s.agent.id]: {
              ...prev[s.agent.id],
              phase: "done",
              tools: prev[s.agent.id]?.tools || [],
              summary: "决定不回复",
              updatedAt: Date.now(),
            },
          }));
          const seen = peerDiscuss.slice(-6);
          noteAgentActivity(
            s.agent,
            "skip",
            seen.length
              ? `看过最近${seen.length}条发言（${seen.map((row) => row.agentName).join("、")}）后决定不回复`
              : "看过这条消息后决定不回复",
          );
          paint();
          return "";
        }
        (s as { _clearedToSpeak?: boolean })._clearedToSpeak = true;
        s.replyConfirmed = true;
        s.silent = false;
        s.streaming = true;
        s.stage = execute ? "executing" : "deciding";
        paint();
        await runOne(s, execute ? 2 : 1);
        if (turnPaused || ac.signal.aborted) return confirmationText(s.agent.id, cleanDisplayText(String(s.text || "")));
        if ((s as { _clearedToSpeak?: boolean })._clearedToSpeak && !visibleReplyAfterJudge(String(s.text || ""))) {
          (s as { _sentenceOnly?: boolean })._sentenceOnly = true;
          s.silent = false;
          s.text = "";
          s.reasoning = "";
          s.decided = false;
          s.replyConfirmed = false;
          s.streaming = true;
          s.stage = "executing";
          await runOne(s, 1);
          (s as { _sentenceOnly?: boolean })._sentenceOnly = false;
        }
        const raw = cleanDisplayText(String(s.text || "")).trim();
        const asks = ((s as any)._asks as Array<{ targetName: string; question: string }>) || [];
        const claimedTasks = (((s as any)._tasks as string[]) || []).filter(Boolean);
        let publicText = formatGroupDiscussionMessage(raw, asks, claimedTasks);
        if (forceSpeak && !publicText) {
          publicText = greetingFallback(s.agent.name);
          s.text = publicText;
          s.silent = false;
          s.decided = true;
          s.replyConfirmed = true;
        }
        if (!s.silent && publicText) {
          const prior = [
            ...committedMsgs.map((m) => ({ agentId: m.agentId, text: m.text })),
            ...peerDiscuss.map((r) => ({ agentId: r.agentId, text: r.text })),
          ];
          if (isNearDuplicateBubble(s.agent.id, publicText, prior)) {
            // Paraphrased re-claim: treat as silent so discuss does not loop on the same speech.
            s.silent = true;
            s.text = "";
            s.decided = true;
            s.replyConfirmed = false;
            paint();
            return agentAsksForUserInput(publicText) ? publicText : "";
          }
          const coordination = [
            ...asks.map((ask) => `ASK: @${ask.targetName} | ${ask.question}`),
            ...claimedTasks.map((task) => `TASK: ${task}`),
          ];
          // Subsequent members receive targeted questions in their actual
          // coordination context, rather than merely seeing that someone
          // spoke. This creates an actionable ask → answer loop.
          peerDiscuss.push({
            agentId: s.agent.id,
            agentName: s.agent.name,
            text: [raw, ...coordination].filter(Boolean).join("\n"),
          });
          // A group is a shared conversation, not a hidden orchestration
          // console. Publish this member's real question/claim immediately
          // as a durable bubble, then let later members read that same turn.
          // Protocol lines remain private; formatGroupDiscussionMessage turns
          // ASK/TASK into natural group-chat language when needed.
          commitGroupMessage(s, publicText);
          s.text = "";
          s.reasoning = "";
          s.silent = true;
          s.decided = true;
          s.browserUrl = undefined;
          s.browserBusy = undefined;
          s.replyConfirmed = false;
          s.stage = "done";
          paint();
          return publicText;
        } else {
          s.streaming = false;
          s.silent = true;
          s.decided = true;
          s.stage = "done";
          return "";
        }
      };

      // The harness picks who works before anyone speaks. Open-floor discussion
      // stays for chat, questions, and groups that are not on a real task.
      ctx.harnessPlan = harnessPlan =
        view.kind === "group" &&
        shouldUseHarness({
          text: raw || text,
          execution: executionRequested,
          collab: activeGroup?.collabMode !== false,
          lightweight: lightweightSocialTurn,
          greeting: collectiveGreeting,
          capability: isCapabilityQuestion(raw || text),
          directAnswer: directAnswerTurn,
        })
          ? planCollaboration(raw || text, rosterForPrompt, {
              playbook: store.harness,
              mentionedIds,
              collab: activeGroup?.collabMode !== false,
              topicText: isDiscussionRequest(raw || text)
                ? String([...messages].reverse().find((m) => m.role === "user")?.text || "")
                : "",
            })
          : null;
      const announceHarness = () => {
        if (!harnessPlan) return;
        harnessNotices.push({
          id: uid("sys"),
          role: "system",
          text: formatHarnessNotice(harnessPlan, rosterForPrompt, loadLocale()),
          at: Date.now(),
          collaboration: true,
        });
        paint();
      };
      const park = (s: (typeof slots)[number]) => {
        s.silent = true;
        s.decided = true;
        s.stage = "done";
      };
      let operatorId: string | undefined;
      let floorExecuted = false;
      if (harnessPlan?.phase === "work") {
        const jobs = new Map(
          harnessPlan.assignments
            .filter((item) => item.role === "owner" || item.role === "worker")
            .map((item) => [item.agentId, item.task] as const),
        );
        for (const s of slots) {
          const task = jobs.get(s.agent.id);
          if (!task) {
            park(s);
            (s as any)._tasks = [];
            continue;
          }
          (s as any)._tasks = [task];
          park(s);
        }
        if (harnessPlan.topology === "solo") {
          operatorId = harnessPlan.assignments.find((item) => item.role === "owner")?.agentId;
        }
        announceHarness();
      } else if (harnessPlan?.phase === "talk") {
        const speakerIds = new Set(harnessPlan.assignments.map((item) => item.agentId));
        for (const s of slots) {
          if (!speakerIds.has(s.agent.id)) park(s);
        }
        announceHarness();
        for (const assignment of harnessPlan.assignments) {
          if (ac.signal.aborted || turnPaused) break;
          const speaker = slots.find((s) => s.agent.id === assignment.agentId);
          if (!speaker) continue;
          const spoken = await runDiscussTurn(speaker);
          if (pauseForUserInput(speaker.agent.name, confirmationText(speaker.agent.id, spoken))) break;
        }
      }
      if (harnessPlan?.phase !== "talk" && (!harnessPlan || scheduleRequest || harnessPlan.topology === "solo")) {
      // Chat: after each new bubble, every other member judges again.
      // A work order stays with one operator and does not open that floor.
      const namedTurn = mentionedIds.size > 0 && !turn?.everyone && !multiVoice;
      let discussOrder = namedTurn
        ? slots.filter((s) => mentionedIds.has(s.agent.id))
        : [...slots];
      const capabilityTurn = isCapabilityQuestion(raw || text);
      if (!operatorId && executionRequested && !turn?.everyone && mentionedIds.size <= 1) {
        const byDuty = [...slots]
          .map((slot, index) => ({ slot, index, score: dutyScore(slot.agent, raw || text) }))
          .sort((a, b) => b.score - a.score || a.index - b.index)[0]?.slot.agent.id;
        const lastAuthor = isAuthorCallback(raw || text)
          ? [...(liveBoard?.tasks || [])].reverse().find((task) => task.status === "done" && task.ownerId)?.ownerId
          : "";
        const authorKnown = Boolean(lastAuthor && slots.some((slot) => slot.agent.id === lastAuthor));
        operatorId = mentionedIds.size === 1
          ? [...mentionedIds][0]
          : authorKnown
            ? String(lastAuthor)
            : byDuty;
      }
      const soloOwnerId = operatorId;
      if (reactionFloor && isGroupGreeting(raw || text)) {
        // 你好 / 大家好: every member answers once. Do not ask a judge that can silence the group.
        for (const s of slots) {
          if (ac.signal.aborted || turnPaused) break;
          (s as { _forceSpeak?: boolean })._forceSpeak = true;
          await runDiscussTurn(s, false);
          (s as { _forceSpeak?: boolean })._forceSpeak = false;
        }
        for (const s of slots) {
          s.streaming = false;
          s.stage = "done";
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
            next[s.agent.id] = {
              ...cur,
              phase: "done",
              summary: "已回复",
              updatedAt: Date.now(),
            };
          }
          return next;
        });
        paint();
      } else if (reactionFloor) {
        let latestAuthorId = "";
        const reactionCap = maxReactionTurns(slots.length);
        let reactions = 0;
        paint();
        while (!ac.signal.aborted && !turnPaused && reactions < reactionCap) {
          const order = planReactionOrder(
            slots.map((slot) => ({ id: slot.agent.id })),
            { latestAuthorId, addressedIds: mentionedIds },
          );
          let spoke = false;
          for (const id of order) {
            if (ac.signal.aborted || turnPaused) break;
            const speaker = slots.find((slot) => slot.agent.id === id);
            if (!speaker) continue;
            const spoken = await runDiscussTurn(speaker, false);
            if (pauseForUserInput(speaker.agent.name, confirmationText(speaker.agent.id, spoken))) break;
            if (!spoken) continue;
            latestAuthorId = speaker.agent.id;
            spoke = true;
            reactions += 1;
            break;
          }
          if (!spoke || turnPaused) break;
        }
      } else {
      if (!multiVoice && !turn?.everyone && operatorId && mentionedIds.size <= 1) {
        discussOrder = slots.filter((slot) => slot.agent.id === operatorId);
      } else if (!multiVoice && !turn?.everyone && !namedTurn && soloSpeakerId) {
        discussOrder = slots.filter((slot) => slot.agent.id === soloSpeakerId);
      } else if (operatorId) {
        discussOrder.sort((a, b) => Number(b.agent.id === operatorId) - Number(a.agent.id === operatorId));
      }
      const called = new Set(discussOrder.map((slot) => slot.agent.id));
      for (const s of slots) {
        if (called.has(s.agent.id)) continue;
        s.stage = "done";
        s.decided = true;
        s.silent = true;
        s.streaming = false;
      }
      paint();
      for (const s of discussOrder) {
        if (ac.signal.aborted) break;
        const asked = new Set(
          peerDiscuss.flatMap((r) => extractNominees(r.text, discussRoster, r.agentId)),
        );
        const focused =
          capabilityTurn ||
          Boolean(soloOwnerId) ||
          (executionRequested && mentionedIds.size > 0 && !turn?.everyone);
        if (focused) (s as { _peerHandoff?: boolean })._peerHandoff = asked.has(s.agent.id);
        const execute = Boolean(
          executionRequested && operatorId && s.agent.id === operatorId && peerDiscuss.length === 0,
        );
        const spoken = await runDiscussTurn(s, execute);
        if (execute && !s.silent) floorExecuted = true;
        if (focused) (s as { _peerHandoff?: boolean })._peerHandoff = false;
        if (pauseForUserInput(s.agent.name, confirmationText(s.agent.id, spoken))) break;
      }

            // A locked operation stays with one person. Do not open a second speaker.
      let lastPendingKey = "";
      let discussStall = 0;
      while (!reactionFloor && !namedTurn && !operatorId && !ac.signal.aborted && !turnPaused) {
        if (discussHasOwner(peerDiscuss, slots)) {
          const pendingEarly = findPendingDiscussIds(peerDiscuss, discussRoster);
          if (!pendingEarly.length) break;
        }
        const pendingIds = findPendingDiscussIds(peerDiscuss, discussRoster);
        if (!pendingIds.length) break;
        const pendingKey = pendingIds.slice().sort().join(",");
        const beforeLen = peerDiscuss.length;
        if (pendingKey === lastPendingKey) break; // same pending set → no progress
        lastPendingKey = pendingKey;
        for (const id of pendingIds) {
          if (ac.signal.aborted) break;
          let s = slots.find((x) => x.agent.id === id);
          if (!s) {
            const agent = rosterForPrompt.find((a) => a.id === id);
            if (!agent) continue;
            s = {
              id: uid("m"),
              agent,
              text: "",
              streaming: false,
              silent: true,
              decided: false,
              replyConfirmed: false,
              reasoning: "",
              reasoningStreaming: false,
              stage: "reading",
            };
            slots.push(s);
          }
          (s as { _peerHandoff?: boolean })._peerHandoff = true;
          const spoken = await runDiscussTurn(s);
          (s as { _peerHandoff?: boolean })._peerHandoff = false;
          if (pauseForUserInput(s.agent.name, confirmationText(s.agent.id, spoken))) break;
        }
        if (peerDiscuss.length === beforeLen) break; // no new discuss bubbles
      }
      }
      }
      if (optionalReply && !turnExclusive && !ac.signal.aborted && !turnPaused) {
        commitBoardFromSlots(slots);
        flushSlotsToCommitted();

        const boardOwners = (liveBoard?.tasks || [])
          .filter((t) => t.status === "open" && t.ownerId)
          .map((t) => String(t.ownerId));
        const discussRosterMini = rosterForPrompt.map((a) => ({ id: a.id, name: a.name }));
        const discussHasTaskLine = peerDiscuss.some(
          (r) => tasksClaimedInText(String(r.text || "")).length > 0,
        );
        // If an agent violated the planning protocol by promising a future
        // action without a TASK line, turn that commitment into the same
        // concrete user request. This is capability-agnostic and means the
        // second wave cannot quietly end after a "let me check" message.
        const deferredCommitters = new Set(
          peerDiscuss
            .filter(
              (r) =>
                hasUnfinishedActionCommitment(String(r.text || "")) &&
                !agentAsksForUserInput(String(r.text || "")),
            )
            .map((r) => r.agentId),
        );
        for (const s of slots) {
          if (!deferredCommitters.has(s.agent.id)) continue;
          const existing = (((s as any)._tasks as string[]) || []).filter(Boolean);
          if (!existing.length) (s as any)._tasks = [text];
        }
        const hasDeferredWork = executionRequested && deferredCommitters.size > 0;
        // Chat, questions, and @ handoffs stay in the discussion. Tools start
        // only for an explicit work order, a claimed TASK, or an open board item.
        const needsExecuteWave =
          !floorExecuted &&
          (harnessPlan?.phase === "talk"
            ? false
            :
          !lightweightSocialTurn &&
          !collectiveGreeting &&
          !directAnswerTurn &&
          (boardOwners.length > 0 ||
            discussHasTaskLine ||
            executionRequested ||
            hasDeferredWork));
        if (!needsExecuteWave) {
          // Every non-silent decision has already been published as a normal
          // group message. Do not replay it here: a chat bubble must never
          // vanish and reappear merely because routing has finished.
          for (const s of slots) {
            s.streaming = false;
            s.reasoningStreaming = false;
            if (!s.decided) {
              s.decided = true;
              s.silent = !String(s.text || "").trim();
            }
          }
          paint();
        } else {
        const harnessWorkers = harnessPlan?.phase === "work"
          ? harnessPlan.assignments
              .filter((item) => item.role === "owner" || item.role === "worker")
              .map((item) => slots.find((s) => s.agent.id === item.agentId))
              .filter((s): s is Slot => Boolean(s))
          : [];
        const operatorSlot = operatorId ? slots.find((s) => s.agent.id === operatorId) : undefined;
        if (operatorSlot) {
          const existing = (((operatorSlot as any)._tasks as string[]) || []).filter(Boolean);
          if (!existing.length) (operatorSlot as any)._tasks = [text];
          operatorSlot.stage = "executing";
          operatorSlot.streaming = true;
          paint();
        }
        const execSlots = operatorSlot
          ? [operatorSlot]
          : harnessWorkers.length
            ? harnessWorkers
            : resolveExecSlots(slots, peerDiscuss, {
              userText: text,
              mentionedIds,
              boardOwnerIds: boardOwners,
              roster: discussRosterMini,
            });
        // The execution queue may become parallel only for independent,
        // non-mutating claims (see canRunTasksInParallel).
        const readyExec = execSlots.slice();
        const claimantIds = new Set(readyExec.map((s) => s.agent.id));
        if (readyExec.length && !ac.signal.aborted) {
          // Non-executors: park as decided/silent so status UI ignores them.
          for (const s of slots) {
            if (claimantIds.has(s.agent.id)) continue;
            s.streaming = false;
            s.silent = true;
            s.decided = true;
            s.replyConfirmed = false;
            s.text = "";
            s.reasoning = "";
            s.reasoningStreaming = false;
          }
          // Executors wait in queue: parked decided so status UI stays quiet.
          for (const s of readyExec) {
            s.id = uid("m");
            s.streaming = false;
            s.silent = true;
            s.replyConfirmed = false;
            s.text = "";
            s.reasoning = "";
            s.reasoningStreaming = false;
            s.decided = true;
            s.stage = "queued";
            (s as any)._facts = [];
            (s as any)._tasks = ((s as any)._tasks as string[]) || [];
            (s as any)._doneTasks = [];
            setAgentLive((prev) => ({
              ...prev,
              [s.agent.id]: {
                ...prev[s.agent.id],
                phase: "queued",
                tools: [],
                summary: "排队中…",
                updatedAt: Date.now(),
              },
            }));
          }
          paint();
          const executeSlot = async (s: Slot) => {
            if (ac.signal.aborted || turnPaused) return;
            s.silent = false;
            s.decided = false;
            s.replyConfirmed = true;
            (s as { _clearedToSpeak?: boolean })._clearedToSpeak = true;
            s.streaming = true;
            s.stage = "executing";
            setAgentLive((prev) => ({
              ...prev,
              [s.agent.id]: {
                ...prev[s.agent.id],
                phase: "executing",
                tools: [],
                summary: "输出中…",
                updatedAt: Date.now(),
              },
            }));
            paint();
            try {
              await runOne(s, 2);
            } finally {
              s.streaming = false;
              s.stage = "done";
              if (!s.decided) {
                s.decided = true;
                s.silent = true;
                s.replyConfirmed = false;
              }
              setAgentLive((prev) => ({
                ...prev,
                [s.agent.id]: {
                  ...prev[s.agent.id],
                  phase: "done",
                  tools: prev[s.agent.id]?.tools || [],
                  summary: prev[s.agent.id]?.summary || (s.silent ? "已跳过" : "已完成"),
                  updatedAt: Date.now(),
                },
              }));
              paint();
            }
          };
          // Independent, read-only assignments run concurrently.  The safety
          // predicate above keeps effects and overlapping work serialized.
          const parallel = harnessPlan?.phase === "work"
            ? harnessPlan.topology === "parallel" && canRunTasksInParallel(readyExec)
            : canRunTasksInParallel(readyExec);
          if (parallel) {
            await Promise.all(readyExec.map((s) => executeSlot(s)));
            if (readyExec.some((s) => pauseForUserInput(s.agent.name, confirmationText(s.agent.id, String(s.text || ""))))) return;
          } else {
            for (const s of readyExec) {
              await executeSlot(s);
              if (harnessPlan?.topology === "sequential") {
                const satisfied = (s as { _goalSatisfied?: boolean })._goalSatisfied === true;
                const blocked = (s as { _goalBlocked?: boolean })._goalBlocked === true;
                const known = (s as { _goalKnown?: boolean })._goalKnown === true;
                // The next member waits until this step is actually finished.
                if (known && !satisfied && !blocked) break;
              }
              const step = cleanDisplayText(String(s.text || "")).trim();
              if (harnessPlan?.topology === "sequential") {
                if (!s.silent && step && !isNearDuplicateBubble(s.agent.id, step, committedMsgs)) {
                  committedMsgs.push({
                    id: s.id,
                    role: "agent",
                    agentId: s.agent.id,
                    agentName: s.agent.name,
                    text: step,
                    streaming: false,
                    reading: false,
                    reasoning: (s.reasoning || "").trim() || undefined,
                    reasoningStreaming: false,
                    browserUrl: s.browserUrl || undefined,
                    browserBusy: s.browserBusy,
                    at: Date.now(),
                  });
                  s.text = "";
                  s.reasoning = "";
                  s.silent = true;
                  s.decided = true;
                  s.browserUrl = undefined;
                  s.browserBusy = undefined;
                  paint();
                }
              }
              if (pauseForUserInput(s.agent.name, confirmationText(s.agent.id, step))) return;
            }
          }
          // Belt: nothing left "输出中" after the queue.
          for (const s of readyExec) {
            s.streaming = false;
            s.stage = "done";
            if (!s.decided) {
              s.decided = true;
              s.silent = true;
            }
          }
          paint();
          commitBoardFromSlots(readyExec);
          // Commit first execute bubbles (deduped) before any continue pass.
          for (const s of readyExec) {
            const raw = cleanDisplayText(String(s.text || "")).trim();
            if (!s.silent && raw) {
              const msg = {
                id: s.id,
                role: "agent" as const,
                agentId: s.agent.id,
                agentName: s.agent.name,
                text: raw,
                streaming: false,
                reading: false,
                reasoning: (s.reasoning || "").trim() || undefined,
                reasoningStreaming: false,
                browserUrl: s.browserUrl || undefined,
                browserBusy: s.browserBusy,
                at: Date.now(),
              };
              const fp = msgFingerprint(String(msg.agentId || ""), String(msg.text || ""));
              if (
                !fp.endsWith("::") &&
                !committedMsgs.some((m) => msgFingerprint(String(m.agentId || ""), String(m.text || "")) === fp) &&
                !isNearDuplicateBubble(String(msg.agentId || ""), String(msg.text || ""), committedMsgs)
              ) {
                committedMsgs.push(msg);
              }
              s.text = "";
              s.reasoning = "";
              s.silent = true;
              s.decided = true;
              s.browserUrl = undefined;
              s.browserBusy = undefined;
            }
          }
          paint();

          // Parallel workers expose their individual evidence first, then one
          // existing member integrates it into a single user-facing result.
          // This is a response role for the current turn, not a permanent
          // leader and not another task assignment.
          const verifierId = harnessPlan?.assignments.find((item) => item.role === "verifier")?.agentId;
          if ((parallel || verifierId) && !ac.signal.aborted) {
            const synthesisSlot = (verifierId ? slots.find((s) => s.agent.id === verifierId) : undefined)
              || (parallel
                ? [...readyExec].sort((a, b) => dutyScore(b.agent, text) - dutyScore(a.agent, text))[0]
                : undefined);
            if (synthesisSlot) {
              synthesisSlot.id = uid("m");
              synthesisSlot.text = "";
              synthesisSlot.reasoning = "";
              synthesisSlot.silent = false;
              synthesisSlot.decided = false;
              synthesisSlot.replyConfirmed = false;
              synthesisSlot.streaming = true;
              synthesisSlot.stage = "executing";
              (synthesisSlot as any)._tasks = [];
              setAgentLive((prev) => ({
                ...prev,
                [synthesisSlot.agent.id]: {
                  ...prev[synthesisSlot.agent.id],
                  phase: "synthesizing",
                  tools: [],
                  summary: "正在汇总并行任务结果…",
                  updatedAt: Date.now(),
                },
              }));
              paint();
              await runOne(synthesisSlot, 2, { synthesis: true });
              synthesisSlot.streaming = false;
              synthesisSlot.stage = "done";
              const synthesis = cleanDisplayText(String(synthesisSlot.text || "")).trim();
              if (!synthesisSlot.silent && synthesis && !isNearDuplicateBubble(synthesisSlot.agent.id, synthesis, committedMsgs)) {
                committedMsgs.push({
                  id: synthesisSlot.id,
                  role: "agent",
                  agentId: synthesisSlot.agent.id,
                  agentName: synthesisSlot.agent.name,
                  text: synthesis,
                  streaming: false,
                  reading: false,
                  reasoning: (synthesisSlot.reasoning || "").trim() || undefined,
                  reasoningStreaming: false,
                  browserUrl: synthesisSlot.browserUrl || undefined,
                  browserBusy: synthesisSlot.browserBusy,
                  at: Date.now(),
                });
              }
              synthesisSlot.text = "";
              synthesisSlot.reasoning = "";
              synthesisSlot.silent = true;
              synthesisSlot.decided = true;
              paint();
            }
          }

          let execStall = 0;
          let goalPasses = 0;
          const committedFp = new Set(
            committedMsgs.map((m) => msgFingerprint(String(m.agentId || ""), String(m.text || ""))),
          );
          const pushUniqueCommitted = (msg: (typeof committedMsgs)[number]) => {
            const fp = msgFingerprint(String(msg.agentId || ""), String(msg.text || ""));
            if (fp.endsWith("::") || committedFp.has(fp)) return false;
            if (isNearDuplicateBubble(String(msg.agentId || ""), String(msg.text || ""), committedMsgs)) return false;
            committedFp.add(fp);
            committedMsgs.push(msg);
            return true;
          };
// Keep executing while shared-board tasks remain open (Stop aborts).
          while (!ac.signal.aborted) {
            {
              const rosterMini = rosterForPrompt.map((a) => ({ id: a.id, name: a.name }));
              const texts = committedMsgs
                .filter((m) => m.role === "agent" && (m.text || "").trim())
                .map((m) => String(m.text || ""));
              if (agentsConfirmedUserTaskDone(liveBoard, texts, rosterMini)) break;
            }
            const openOwners = (liveBoard?.tasks || [])
              .filter((t) => t.status === "open" && t.ownerId)
              .map((t) => String(t.ownerId));
            if (!openOwners.length) break;
            // The agent already judged the tool results. A settled reply is the
            // end, so this loop does not start another model pass.
            if (goalPasses >= 8) break;
            const ownerSlots = slots.filter((s) => openOwners.includes(s.agent.id));
            if (
              ownerSlots.length > 0 &&
              ownerSlots.every((s) => (s as { _goalSatisfied?: boolean })._goalSatisfied === true)
            ) {
              break;
            }
            if (
              ownerSlots.length > 0 &&
              ownerSlots.every((s) => Boolean((s as { _goalBlocked?: boolean })._goalBlocked))
            ) {
              break;
            }
            goalPasses += 1;
            const sequentialOrder = harnessPlan?.topology === "sequential"
              ? (harnessPlan.assignments || [])
                  .filter((item) => item.role === "owner" || item.role === "worker")
                  .map((item) => item.agentId)
              : [];
            const sequentialOwner = sequentialOrder.find((id) => openOwners.includes(id));
            const contSlots = slots.filter((s) => {
              if (!openOwners.includes(s.agent.id)) return false;
              if (operatorId && s.agent.id !== operatorId) return false;
              if (sequentialOwner && s.agent.id !== sequentialOwner) return false;
              return true;
            });
            if (!contSlots.length) break;
            for (const s of contSlots) {
              s.id = uid("m");
              s.text = "";
              s.reasoning = "";
              s.streaming = true;
              s.stage = "executing";
              s.silent = false;
              s.decided = false;
              s.replyConfirmed = false;
              s.reasoningStreaming = false;
              const prevTasks = (((s as any)._tasks as string[]) || []).filter(Boolean);
              (s as any)._tasks = prevTasks.length
                ? prevTasks
                : (liveBoard?.tasks || [])
                    .filter((t) => t.status === "open" && t.ownerId === s.agent.id)
                    .map((t) => String((t as any).title || (t as any).text || (t as any).label || "继续执行"))
                    .slice(0, 3);
              setAgentLive((prev) => ({
                ...prev,
                [s.agent.id]: {
                  ...prev[s.agent.id],
                  phase: "executing",
                  tools: [],
                  summary: "继续执行中…",
                  updatedAt: Date.now(),
                },
              }));
            }
            paint();
            for (const s of contSlots) {
              if (ac.signal.aborted || turnPaused) break;
              s.replyConfirmed = true;
              (s as { _clearedToSpeak?: boolean })._clearedToSpeak = true;
              await runOne(s, 2);
              if (pauseForUserInput(s.agent.name, confirmationText(s.agent.id, String(s.text || "")))) return;
            }
            commitBoardFromSlots(contSlots);
            // Flush new completion bubbles; near-duplicates do not count as progress.
            let added = 0;
            for (const s of contSlots) {
              const raw = cleanDisplayText(String(s.text || "")).trim();
              if (!s.silent && raw) {
                const ok = pushUniqueCommitted({
                  id: s.id,
                  role: "agent",
                  agentId: s.agent.id,
                  agentName: s.agent.name,
                  text: raw,
                  streaming: false,
                  reading: false,
                  reasoning: (s.reasoning || "").trim() || undefined,
                  reasoningStreaming: false,
                  browserUrl: s.browserUrl || undefined,
                  browserBusy: s.browserBusy,
                  at: Date.now(),
                });
                if (ok) added += 1;
                s.text = "";
                s.reasoning = "";
                s.silent = true;
                s.decided = true;
                s.browserUrl = undefined;
                s.browserBusy = undefined;
              }
            }
            paint();
            if (added === 0) {
              execStall += 1;
            } else {
              execStall = 0;
            }
            if (execStall >= 4 && contSlots.every((s) => (s as { _goalBlocked?: boolean })._goalBlocked)) {
              break;
            }
          }
          // The same operation is not handed to another member.
          const handledHandoffMsgIds = new Set<string>();
          let handoffStall = 0;
          while (!operatorId && harnessPlan?.phase !== "work" && !ac.signal.aborted) {
            {
              const rosterMini = rosterForPrompt.map((a) => ({ id: a.id, name: a.name }));
              const texts = committedMsgs
                .filter((m) => m.role === "agent" && (m.text || "").trim())
                .map((m) => String(m.text || ""));
              if (agentsConfirmedUserTaskDone(liveBoard, texts, rosterMini)) break;
            }
            const recent = committedMsgs
              .filter((m) => m.role === "agent" && (m.text || "").trim())
              .slice(-12);
            // Only follow handoffs from messages we have not acted on yet.
            const fresh = recent.filter((m) => m.id && !handledHandoffMsgIds.has(String(m.id)));
            if (!fresh.length) break;

            const rosterMini = rosterForPrompt.map((a) => ({ id: a.id, name: a.name }));
            const last = recent[recent.length - 1];
            const fromLast = last
              ? findHandoffAgentIds(String(last.text || ""), rosterMini, String(last.agentId || ""))
              : [];
            // Prefer latest message's @targets; else any fresh handoff targets.
            let runIds = fromLast;
            if (!runIds.length) {
              const ids = new Set<string>();
              for (const m of fresh) {
                for (const id of findHandoffAgentIds(String(m.text || ""), rosterMini, String(m.agentId || ""))) {
                  ids.add(id);
                }
              }
              runIds = [...ids];
            }
            if (!runIds.length) {
              // Mark fresh messages handled even if no target parsed — avoid re-scan forever.
              for (const m of fresh) handledHandoffMsgIds.add(String(m.id));
              break;
            }
            // Don't immediately re-run the author of the latest handoff ask.
            if (last?.agentId) {
              runIds = runIds.filter((id) => id !== String(last.agentId));
            }
            if (!runIds.length) {
              for (const m of fresh) handledHandoffMsgIds.add(String(m.id));
              break;
            }

            const contSlots = slots.filter((s) => runIds.includes(s.agent.id));
            if (!contSlots.length) {
              for (const m of fresh) handledHandoffMsgIds.add(String(m.id));
              break;
            }

            const beforeCount = committedMsgs.length;
            for (const s of contSlots) {
              s.id = uid("m");
              s.text = "";
              s.reasoning = "";
              s.streaming = true;
              s.stage = "executing";
              s.silent = false;
              s.decided = false;
              s.replyConfirmed = false;
              s.reasoningStreaming = false;
              const prevTasks = (((s as any)._tasks as string[]) || []).filter(Boolean);
              if (!prevTasks.length) {
                s.streaming = false;
                s.silent = true;
                s.decided = true;
                s.stage = "done";
                continue;
              }
              (s as any)._tasks = prevTasks;
              setAgentLive((prev) => ({
                ...prev,
                [s.agent.id]: {
                  ...prev[s.agent.id],
                  phase: "executing",
                  tools: [],
                  summary: "接手推进中…",
                  updatedAt: Date.now(),
                },
              }));
            }
            paint();
            for (const s of contSlots) {
              if (ac.signal.aborted || turnPaused) break;
              if (!(((s as any)._tasks as string[]) || []).length) continue;
              s.replyConfirmed = true;
              (s as { _clearedToSpeak?: boolean })._clearedToSpeak = true;
              await runOne(s, 2);
              if (pauseForUserInput(s.agent.name, confirmationText(s.agent.id, String(s.text || "")))) break;
            }
            commitBoardFromSlots(contSlots);
            for (const s of contSlots) {
              const raw = cleanDisplayText(String(s.text || "")).trim();
              if (!s.silent && raw) {
                const msg = {
                  id: s.id,
                  role: "agent" as const,
                  agentId: s.agent.id,
                  agentName: s.agent.name,
                  text: raw,
                  streaming: false,
                  reading: false,
                  reasoning: (s.reasoning || "").trim() || undefined,
                  reasoningStreaming: false,
                  browserUrl: s.browserUrl || undefined,
                  browserBusy: s.browserBusy,
                  at: Date.now(),
                };
                pushUniqueCommitted(msg);
                s.text = "";
                s.reasoning = "";
                s.silent = true;
                s.decided = true;
                s.browserUrl = undefined;
                s.browserBusy = undefined;
              }
            }
            paint();
            for (const m of fresh) handledHandoffMsgIds.add(String(m.id));
            {
              const rosterMini = rosterForPrompt.map((a) => ({ id: a.id, name: a.name }));
              const texts = committedMsgs
                .filter((m) => m.role === "agent" && (m.text || "").trim())
                .map((m) => String(m.text || ""));
              if (agentsConfirmedUserTaskDone(liveBoard, texts, rosterMini)) break;
            }
            if (committedMsgs.length === beforeCount) {
              handoffStall += 1;
              if (handoffStall >= 1) break; // near-dup / no new unique bubbles → stop
            } else {
              handoffStall = 0;
            }
          }
          if (harnessPlan?.phase === "work" && harnessPlan.topology !== "solo" && !ac.signal.aborted && !turnPaused) {
            const before = currentHarnessDiagnosis();
            const repair = before?.innerRepair;
            if (before && repair) {
              ctx.harnessPrior = harnessPrior = before;
              const owner = slots.find((s) => s.agent.id === repair.agentId);
              if (owner) {
                ctx.harnessRepair = harnessRepair = repair.note;
                ctx.harnessRepaired = harnessRepaired = true;
                const owned = harnessPlan.assignments.find((item) => item.agentId === owner.agent.id && (item.role === "owner" || item.role === "worker"));
                (owner as any)._tasks = [owned?.task || text];
                owner.id = uid("m");
                owner.text = "";
                owner.reasoning = "";
                owner.silent = false;
                owner.decided = false;
                owner.replyConfirmed = false;
                owner.streaming = true;
                owner.stage = "executing";
                noteAgentActivity(owner.agent, "execute", "上一轮协作没有交付，改为单人完成");
                await runOne(owner, 2);
                const repaired = cleanDisplayText(String(owner.text || "")).trim();
                if (!owner.silent && repaired && !isNearDuplicateBubble(owner.agent.id, repaired, committedMsgs)) {
                  committedMsgs.push({
                    id: owner.id,
                    role: "agent",
                    agentId: owner.agent.id,
                    agentName: owner.agent.name,
                    text: repaired,
                    streaming: false,
                    reading: false,
                    reasoning: (owner.reasoning || "").trim() || undefined,
                    reasoningStreaming: false,
                    browserUrl: owner.browserUrl || undefined,
                    browserBusy: owner.browserBusy,
                    at: Date.now(),
                  });
                }
                owner.text = "";
                owner.reasoning = "";
                owner.silent = true;
                owner.decided = true;
                owner.streaming = false;
                owner.stage = "done";
                paint();
              }
            }
          }

        }
        } // end needsExecuteWave else
      }
}
