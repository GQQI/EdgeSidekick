// @ts-nocheck
import type { SocialChatMsg } from "./types";
type Slot = any;
type ChatLine = any;
type LoopEvent = any;
type JudgeInput = any;
type ReplyInput = any;
type ExplicitPipeline = any;
/* Extracted from socialSend.ts — runOne */
/* eslint-disable @typescript-eslint/no-explicit-any */
export type createSocialRunOneCtx = Record<string, any>;

export function createSocialRunOne(ctx: createSocialRunOneCtx) {
  const {
    ac,
    activeGroup,
    addressedNames,
    appendAgentMemory,
    applyAgentOutcome,
    applyStandingRules,
    browserHoldRef,
    browserUserClosedRef,
    buildGroupBrief,
    buildSocialBackendMessage,
    bumpWorkingEntry,
    chatWorkspacePath,
    cleanDisplayText,
    collectiveGreeting,
    commitSharedFileMessage,
    coordinationAsksInText,
    currentWorkSite,
    directAnswerAgentId,
    directAnswerTurn,
    dmMemoryContext,
    doneTasksForExecution,
    extractOwnReply,
    formatAgentMemoryForPrompt,
    formatBoardForPrompt,
    formatHarnessContract,
    formatPeerDiscuss,
    getDmActiveSession,
    getDmStreamNonce,
    groupId,
    groups,
    harnessRepair,
    isNearDuplicateBubble,
    isStaleSend,
    isWorkbenchLinkedAgent,
    lightweightSocialTurn,
    mentionedIds,
    mergeExecutionTasks,
    messages,
    noteAgentActivity,
    onOpenBrowserUrl,
    onSessionId,
    onToast,
    optionalReply,
    paint,
    parseAgentDecision,
    parseStandingRules,
    patchStore,
    pauseForUserInput,
    raw,
    rememberWork,
    rewardNotesFor,
    rosterForPrompt,
    scheduleRequest,
    sessionId,
    setAgentLive,
    setBrowserCard,
    setCtx,
    setCtxByAgent,
    setGroupBoard,
    sharedFileFromToolEvent,
    soloSpeakerId,
    store,
    streamChat,
    stripAgentTags,
    summarizeReply,
    t,
    targets,
    text,
    threadKey,
    threadKeyRef,
    turn,
    uid,
    view,
    visibleReplyAfterJudge,
    yieldToForegroundDm,
  } = ctx;
  let committedMsgs = ctx.committedMsgs;
  let freeReply = ctx.freeReply;
  let harnessPlan = ctx.harnessPlan;
  let key = ctx.key;
  let liveBoard = ctx.liveBoard;
  let peerDiscuss = ctx.peerDiscuss;
  let turnPaused = ctx.turnPaused;

  return async (slot: Slot, wave: 1 | 2, opts?: { synthesis?: boolean }) => {


      if (ac.signal.aborted) return;
      (slot as { _goalSatisfied?: boolean })._goalSatisfied = false;
      (slot as { _goalBlocked?: boolean })._goalBlocked = false;
      (slot as { _goalKnown?: boolean })._goalKnown = false;
      (slot as { _toolErrors?: number })._toolErrors = 0;
      // Wave 1 is private orchestration, not an answer stream. Keep model
      // output for routing and claims, but never render it midway.
      (slot as any)._suppressDecisionBubble =
        view.kind === "group" && wave === 1 && !(slot as { _clearedToSpeak?: boolean })._clearedToSpeak;
      if (freeReply) {
        noteAgentActivity(
          slot.agent,
          "execute",
          freeReply.step
            ? `流水线第 ${freeReply.step.index}/${freeReply.step.total} 步：${freeReply.step.task}`
            : `决定回复 ${freeReply.latest.author} 的消息，正在写`,
        );
      } else if (view.kind === "group") {
        const assigned = (((slot as any)._tasks as string[]) || []).filter(Boolean);
        noteAgentActivity(
          slot.agent,
          wave === 1 ? "decide" : "execute",
          wave === 1
            ? "正在判断是否需要处理，以及是否需要同事补充信息"
            : assigned.length
              ? `正在执行：${assigned.join("；")}`
              : opts?.synthesis
                ? "正在整合已完成成员的结果"
                : "正在处理已认领事项",
        );
      }
      await yieldToForegroundDm();
      if (ac.signal.aborted) return;
      // Exclusive address => all current targets must reply.
      // Open floor => everyone optional. DM => must reply.
      const slotOptional = optionalReply;
      // Everyone performs the read/judge pass.  For a direct general question,
      // one best-fit member is the answerer and peers remain true bystanders;
      // this prevents six near-identical introductions from appearing at once.
      const peerHandoff = Boolean((slot as { _peerHandoff?: boolean })._peerHandoff);
      const turnRole: "open" | "addressee" | "bystander" =
        view.kind !== "group"
          ? "addressee"
          : peerHandoff
            ? "addressee"
          : collectiveGreeting
            ? "addressee"
            : soloSpeakerId && slot.agent.id === soloSpeakerId
              ? "addressee"
            : directAnswerTurn && directAnswerAgentId
              ? slot.agent.id === directAnswerAgentId
                ? "addressee"
                : "bystander"
              : mentionedIds.size === 0
            ? "open"
            : mentionedIds.has(slot.agent.id)
              ? "addressee"
              : "bystander";
      const boardText =
        view.kind === "group" && liveBoard ? formatBoardForPrompt(liveBoard) : "";
      const privateMemoryText = formatAgentMemoryForPrompt(
        store,
        slot.agent.id,
        12,
        view.kind === "dm"
          ? view.fromGroupId
            ? { includeGroupContext: true, groupId: view.fromGroupId }
            : { includeGroupContext: false, onlyDm: true }
          : view.kind === "group"
            ? { groupId: view.groupId, includeGroupContext: true }
            : undefined,
      );
      const freeTurn = freeReply;
      const backendMsg = freeTurn
        ? buildSocialBackendMessage({
            userText: text,
            targets: [slot.agent],
            history: freeTurn.history,
            historyLimit: 24,
            roster: rosterForPrompt,
            mode: "group",
            group: activeGroup,
            optionalReply: true,
            privateMemory: privateMemoryText,
            rewardNotes: rewardNotesFor(text),
            scheduleRequest,
            workSite: currentWorkSite(),
            freeChat: {
              latest: freeTurn.latest,
              addressed: freeTurn.addressed,
              pipelineStep: freeTurn.step,
              brief: freeTurn.brief,
              everyone: freeTurn.everyone,
              mustAnswer: freeTurn.mustAnswer,
            },
          })
        : buildSocialBackendMessage({
        userText: text,
        targets: [slot.agent],
        // Execution workers need the actual in-turn decisions, not merely the
        // transcript from before this send.  This also makes a handoff
        // deterministic instead of asking the next owner to rediscover work.
        history: wave === 2 ? [...messages, ...committedMsgs] : messages,
        historyLimit: turnRole === "bystander" ? 4 : 12,
        dmFresh: view.kind === "dm" && !view.fromGroupId,
        groupBrief:
          view.kind === "dm" && view.fromGroupId
            ? buildGroupBrief(store, view.fromGroupId)
            : undefined,
        roster: rosterForPrompt,
        mode: view.kind === "dm" ? "dm" : "group",
        group: activeGroup,
        discussAll: false,
        optionalReply: slotOptional,
        // For a direct question, only the addressed member answers.  The
        // others still evaluate the same message as bystanders and can remain
        // silent without being turned into task workers.
        directAnswer:
          directAnswerTurn &&
          (turnRole === "addressee" || slot.agent.id === directAnswerAgentId),
        collectiveGreeting,
        lightweightSocial: lightweightSocialTurn,
        synthesis: Boolean(opts?.synthesis),
        useDecisionProtocol: false,
        clearedToSpeak: Boolean((slot as { _clearedToSpeak?: boolean })._clearedToSpeak),
        sentenceOnly: Boolean((slot as { _sentenceOnly?: boolean })._sentenceOnly),
        scheduleRequest,
        workSite: currentWorkSite(),
        assignedTasks:
          wave === 2
            ? ((((slot as any)._tasks as string[]) || []).map((task) => String(task || "").trim()).filter(Boolean))
            : undefined,
        boardText,
        boardWave: view.kind === "group" ? wave : undefined,
        peerDiscussText:
          view.kind === "group" && peerDiscuss.length
            ? formatPeerDiscuss(peerDiscuss.map((r) => ({ agentName: r.agentName, text: r.text })))
            : undefined,
        privateMemory: formatAgentMemoryForPrompt(
          store,
          slot.agent.id,
          12,
          view.kind === "dm"
            ? view.fromGroupId
              ? { includeGroupContext: true, groupId: view.fromGroupId }
              : { includeGroupContext: false, onlyDm: true }
            : view.kind === "group"
              ? { groupId: view.groupId, includeGroupContext: true }
              : undefined,
        ),
        turnRole,
        addressedNames,
        peerHandoff,
        rewardNotes: rewardNotesFor(text),
        harnessContract: harnessPlan
          ? formatHarnessContract(harnessPlan, slot.agent.id, {
              repair: harnessRepair,
              synthesizing: Boolean(opts?.synthesis),
            })
          : undefined,
        omitPeerRoster: Boolean(harnessPlan),
      });
      let buf = "";
      let lastGoodBuf = "";
      // Each model round is its own bubble. Reset used to erase the previous
      // sentence or append the next one into the same bubble.
      const sealVisibleRound = () => {
        const visible = cleanDisplayText(
          extractOwnReply(buf, slot.agent.name) || stripAgentTags(buf),
        ).trim();
        const reason = (slot.reasoning || "").trim();
        if (!visible && !reason) return;
        if (visible && isNearDuplicateBubble(slot.agent.id, visible, committedMsgs)) {
          slot.id = uid("m");
          slot.text = "";
          slot.reasoning = "";
          slot.reasoningStreaming = false;
          return;
        }
        committedMsgs.push({
          id: slot.id,
          role: "agent",
          agentId: slot.agent.id,
          agentName: slot.agent.name,
          text: visible,
          streaming: false,
          reading: false,
          reasoning: reason || undefined,
          reasoningStreaming: false,
          browserUrl: slot.browserUrl || undefined,
          browserBusy: slot.browserBusy,
          at: Date.now(),
        });
        slot.id = uid("m");
        slot.text = "";
        slot.reasoning = "";
        slot.reasoningStreaming = false;
        slot.replyConfirmed = true;
        slot.streaming = true;
        paint();
      };
      try {
        // Each parallel group agent needs its OWN session. null still collapses
        // on some backends; a unique id keeps assistant_delta isolated.
        const linkedDm = view.kind === "dm" && isWorkbenchLinkedAgent(slot.agent.id);
        const streamSessionId = linkedDm
          ? sessionId
          : view.kind === "dm"
            ? `social-dm-${slot.agent.id}-${getDmActiveSession(store, slot.agent.id)}-n${getDmStreamNonce(store, slot.agent.id)}`
            : `social-${groupId || "g"}-${slot.agent.id}-w${wave}-${slot.id}`;
        setAgentLive((prev) => ({
          ...prev,
          [slot.agent.id]: {
            ...prev[slot.agent.id],
            phase: slot.stage === "deciding" ? "deciding" : "executing",
            tools: prev[slot.agent.id]?.tools || [],
            summary: slot.stage === "deciding" ? "正在读取本轮消息并判断是否需要处理…" : "正在处理已认领事项…",
            updatedAt: Date.now(),
          },
        }));
        await streamChat(
          backendMsg,
          streamSessionId,
          {
            onEvent: (ev) => {
              if (turnPaused || ac.signal.aborted || isStaleSend()) return;
              const anyEv = ev as any;
              if (anyEv?.type === "goal_settled") {
                (slot as { _goalKnown?: boolean })._goalKnown = true;
                (slot as { _goalSatisfied?: boolean })._goalSatisfied = anyEv?.data?.satisfied === true;
                (slot as { _goalBlocked?: boolean })._goalBlocked = anyEv?.data?.blocked === true;
              }
              if (anyEv?.type === "assistant_status") {
                const statusText = String(anyEv?.data?.text || anyEv?.data?.message || "").trim();
                if (statusText) {
                  (slot as { activityText?: string }).activityText = statusText;
                  paint();
                }
              }
              if (anyEv?.type === "session") {
                const nid = String(anyEv?.data?.session_id || "");
                // Only bind DM / single-agent streams back to the workbench session.
                // Linked lead DM shares the workbench session; bind it. Other social streams stay isolated.
                if (
                  nid &&
                  !optionalReply &&
                  (linkedDm || (view.kind !== "dm" && !String(streamSessionId || "").startsWith("social-")))
                ) {
                  onSessionId(nid);
                }
              }
              if (anyEv?.type === "tool_start" || anyEv?.type === "tool_end" || anyEv?.type === "tool_call_delta") {
                const toolName = String(
                  anyEv?.data?.name || anyEv?.data?.tool || anyEv?.data?.tool_name || "tool",
                );
                const toolId = String(anyEv?.data?.id || anyEv?.data?.tool_id || toolName);
                const detail = String(
                  anyEv?.data?.detail || anyEv?.data?.summary || anyEv?.data?.args_preview || "",
                ).slice(0, 200);
                const toolBlob = `${toolName}\n${detail}\n${String(anyEv?.data?.result || anyEv?.data?.content || "")}`;
                if (/wait_for_user|needs_human/i.test(toolBlob)) browserHoldRef.current = true;
                if (anyEv?.type === "tool_start" && /^ask_user$/i.test(toolName)) {
                  sealVisibleRound();
                  const question = String(
                    anyEv?.data?.args?.question || anyEv?.data?.question || detail || "请用户确认后再继续",
                  );
                  pauseForUserInput(slot.agent.name, question, true);
                }
                const toolFailed = Boolean(anyEv?.type === "tool_end" && (anyEv?.data?.ok === false || anyEv?.data?.error));
                if (
                  anyEv?.type === "tool_end" &&
                  (toolName === "create_scheduled_task" || toolName === "cancel_scheduled_task") &&
                  !toolFailed
                ) {
                  window.dispatchEvent(new CustomEvent("sidekick-schedule"));
                }
                if (toolFailed) {
                  (slot as { _toolErrors?: number })._toolErrors = ((slot as { _toolErrors?: number })._toolErrors || 0) + 1;
                } else if (anyEv?.type === "tool_end") {
                  rememberWork(slot.agent.name, toolName, anyEv?.data || {});
                }
                setAgentLive((prev) => {
                  const cur: (typeof prev)[string] = prev[slot.agent.id] || {
                    phase: slot.stage === "deciding" ? "deciding" : "executing",
                    tools: [],
                    updatedAt: Date.now(),
                  };
                  const tools = [...cur.tools];
                  const idx = tools.findIndex((x) => x.id === toolId || x.name === toolName);
                  const status =
                    anyEv?.type === "tool_end"
                      ? ((anyEv?.data?.ok === false || anyEv?.data?.error) ? "error" : "done")
                      : "running";
                  const row = {
                    id: toolId,
                    name: toolName,
                    status: status as "running" | "done" | "error",
                    detail: detail || undefined,
                  };
                  if (idx >= 0) tools[idx] = { ...tools[idx], ...row };
                  else tools.push(row);
                  return {
                    ...prev,
                    [slot.agent.id]: {
                      ...cur,
                      phase: slot.stage === "deciding" ? "deciding" : "executing",
                      tools: tools.slice(-12),
                      summary: cur.summary,
                      updatedAt: Date.now(),
                    },
                  };
                });
                (slot as { activityText?: string }).activityText =
                  anyEv?.type === "tool_end"
                    ? `${toolName} 已返回${toolFailed ? "（失败）" : ""}`
                    : `正在使用 ${toolName}`;
                paint();
                if (view.kind === "group") {
                  noteAgentActivity(
                    slot.agent,
                    "tool",
                    anyEv?.type === "tool_end"
                      ? `${toolName} 已返回${toolFailed ? "（失败）" : ""}`
                      : `正在使用 ${toolName}${detail ? `：${detail}` : ""}`,
                  );
                  const sharedFile = sharedFileFromToolEvent(anyEv);
                  if (sharedFile) commitSharedFileMessage(slot, sharedFile);
                  paint();
                }
              }
              // Social browser reveal MUST sit outside the tool_* branch.
              // browser_open is a separate SSE type; nesting it under tool_start/end
              // made the handler dead code, so the modal never opened from backend
              // browser_open and the Electron BrowserView stayed at 1x1 / hidden.
              {
                const revealSocialBrowser = (pageUrlRaw: string, opts?: { navigate?: boolean }) => {
                  const focused = ctx.key = key === threadKeyRef.current;
                  if (!focused || browserUserClosedRef.current) return;
                  const detailStr = String(pageUrlRaw || "");
                  const argsUrl = String(
                    (anyEv as any)?.data?.args?.url ||
                      (anyEv as any)?.data?.url ||
                      "",
                  );
                  const urlHit =
                    detailStr.match(/https?:\/\/[^\s"'<>]+/i)?.[0] ||
                    detailStr.match(/file:\/\/[^\s"'<>]+/i)?.[0] ||
                    argsUrl.match(/https?:\/\/[^\s"'<>]+/i)?.[0] ||
                    argsUrl.match(/file:\/\/[^\s"'<>]+/i)?.[0] ||
                    (detailStr.startsWith("http") || detailStr.startsWith("file:")
                      ? detailStr.trim()
                      : "") ||
                    (argsUrl.startsWith("http") || argsUrl.startsWith("file:")
                      ? argsUrl.trim()
                      : "");
                  // Only a fresh http(s)/file URL may drive loadURL. Re-emitting
                  // browser_open after observe/click with an empty url used to
                  // re-navigate to the stale homepage stored on the slot —
                  // that is the session-mode "jump back home" loop.
                  const freshUrl = (urlHit || "").trim();
                  const navigate = opts?.navigate !== false && Boolean(freshUrl) && freshUrl !== "about:blank";
                  if (freshUrl && freshUrl !== "about:blank") {
                    slot.browserUrl = freshUrl;
                  } else if (!slot.browserUrl) {
                    slot.browserUrl = "about:blank";
                  }
                  slot.browserBusy = true;
                  setBrowserCard({
                    url: slot.browserUrl || "",
                    threadKey: String(key),
                    agent: {
                      name: slot.agent?.name || "",
                      color: slot.agent?.color || "#64748b",
                      avatar: slot.agent?.avatar,
                    },
                  });
                  // A real navigation must load the in-app browser. Leaving this
                  // as a no-op kept the guest on about:blank, so the preview
                  // stayed black even after the dev server was up.
                  // Reveal-only events (click, console) must not loadURL again.
                  if (navigate && freshUrl) onOpenBrowserUrl?.(freshUrl);
                  paint();
                };
                if (anyEv?.type === "browser_open") {
                  const openUrl = String(anyEv?.data?.url || "").trim();
                  const revealOnly = Boolean(anyEv?.data?.reveal_only) || !openUrl;
                  revealSocialBrowser(openUrl, { navigate: !revealOnly });
                }
                if (anyEv?.type === "tool_start") {
                  const tname = String(
                    anyEv?.data?.name ||
                      anyEv?.data?.tool ||
                      anyEv?.data?.tool_name ||
                      "",
                  ).toLowerCase();
                  if (tname.includes("wait_for_user") || tname.includes("needs_human")) {
                    browserHoldRef.current = true;
                  }
                  if (tname.includes("browser")) {
                    const detail = String(
                      anyEv?.data?.args?.url ||
                        anyEv?.data?.detail ||
                        anyEv?.data?.summary ||
                        anyEv?.data?.args_preview ||
                        "",
                    );
                    // tool_start for non-navigate browser_* must not reload.
                    const isNav = tname.includes("navigate") || tname.includes("goto") || tname.includes("open");
                    revealSocialBrowser(detail, { navigate: isNav });
                  }
                }
              }

              if (anyEv?.type === "assistant_delta") {
                const piece = String(
                  anyEv?.data?.chunk ?? anyEv?.data?.text ?? anyEv?.data?.delta ?? "",
                );
                const reset = Boolean(anyEv?.data?.reset);
                const discard = Boolean(anyEv?.data?.discard);
                if (reset) {
                  // Keep lastGoodBuf so an empty final after tool-discard can
                  // still fall back if the model never re-emits visible text.
                  if (buf.trim()) lastGoodBuf = buf;
                  // A new model round used to wipe the live bubble, or glue the
                  // next reply onto it. Keep the sentence that is already visible,
                  // including when a tool asks the user to confirm.
                  if (!discard || buf.trim() || (slot.reasoning || "").trim() || (slot.text || "").trim()) {
                    sealVisibleRound();
                  }
                  buf = "";
                }
                if (!piece) {
                  if (reset && !discard) {
                    if (!slotOptional || slot.replyConfirmed) {
                      slot.text = "";
                      paint();
                    }
                  }
                  return;
                }
                buf += piece;
                if (buf.trim()) lastGoodBuf = buf;
                const decisionEarly = buf.match(
                  /(^|\n)\s*DECISION\s*[:：]\s*(reply|silent|speak|pass|no_reply|noreply|回复|发言|说话|沉默|不回复|跳过)(?=\s|$|[，。！？,.!?\n])/i,
                );
                const decKind = decisionEarly ? String(decisionEarly[2] || "").toLowerCase() : "";
                const silentKinds = ["silent", "pass", "no_reply", "noreply", "沉默", "不回复", "跳过"];
                const replyKinds = ["reply", "speak", "回复", "发言", "说话"];
                // Speak/silent is decided by a hidden judge call before this
                // stream. Do not wait for a DECISION line inside the reply.
                if (slotOptional && (slot as { _awaitDecision?: boolean })._awaitDecision) {
                  if (decKind && silentKinds.includes(decKind)) {
                    slot.silent = true;
                    slot.replyConfirmed = false;
                    slot.text = "";
                    slot.reasoning = "";
                    paint();
                    return;
                  }
                  // Hold visible output until DECISION:reply — buf stays private.
                  if (!replyKinds.includes(decKind)) {
                    return;
                  }
                  slot.replyConfirmed = true;
                  slot.silent = false;
                  const preview = cleanDisplayText(
                    extractOwnReply(buf, slot.agent.name) || stripAgentTags(buf),
                  );
                  slot.text = preview;
                  if (!preview.trim() && !(slot.reasoning || "").trim()) return;
                  paint();
                  return;
                }
                slot.silent = false;
                slot.replyConfirmed = true;
                slot.text = cleanDisplayText(
                  extractOwnReply(buf, slot.agent.name) || stripAgentTags(buf),
                );
                paint();
              }
              if (anyEv?.type === "assistant_reasoning_delta") {
                const piece = String(anyEv?.data?.chunk ?? anyEv?.data?.text ?? "");
                const reset = Boolean(anyEv?.data?.reset);
                if (reset) {
                  slot.reasoning = "";
                  slot.reasoningStreaming = false;
                  if (slot.replyConfirmed || slot.streaming) paint();
                  return;
                }
                if (piece) slot.reasoning = (slot.reasoning || "") + piece;
                slot.reasoningStreaming = true;
                if (slotOptional && !slot.replyConfirmed && !slot.streaming) return;
                if (slot.silent) return;
                if ((slot.reasoning || "").trim() || (slot.text || "").trim()) paint();
              }
              if (anyEv?.type === "context_usage" || anyEv?.type === "llm_start") {
                const budget = (anyEv?.data?.budget || {}) as Record<string, unknown>;
                const tokens = Number(anyEv?.data?.tokens ?? budget.tokens_est ?? 0);
                const limit = Number(anyEv?.data?.limit ?? 0);
                const windowLimit = Number(anyEv?.data?.window_limit ?? budget.window_limit ?? 0);
                const next = {
                  tokens: Number.isFinite(tokens) ? tokens : 0,
                  limit: Number.isFinite(limit) && limit > 0 ? limit : 48000,
                  windowLimit: Number.isFinite(windowLimit) && windowLimit > 0 ? windowLimit : undefined,
                };
                                if (!isStaleSend()) {
                  setCtx(next);
                  setCtxByAgent((prev) => ({
                    ...prev,
                    [slot.agent.id]: next,
                  }));
                }

              }
            },
            onFinal: (textOut, meta) => {
              const metaReason = String((meta as { reasoning?: string } | undefined)?.reasoning || "").trim();
              if (metaReason && !(slot.reasoning || "").trim()) slot.reasoning = metaReason;
              // Empty synthetic final after SSE error must not clobber the error bubble.
              if (slot.stage === "error" && slot.decided) {
                slot.streaming = false;
                paint();
                return;
              }
              slot.reasoningStreaming = false;
              slot.stage = "done";
              setAgentLive((prev) => ({
                ...prev,
                [slot.agent.id]: {
                  ...prev[slot.agent.id],
                  phase: "done",
                  tools: prev[slot.agent.id]?.tools || [],
                  summary: "已完成",
                  updatedAt: Date.now(),
                },
              }));
              // Match workbench: prefer incoming final, else streamed buf, else
              // last good paint — never drop a real reply into socialEmptyReply
              // when deltas already delivered text but final.text was empty.
              const painted = cleanDisplayText(String(slot.text || "")).trim();
              const emptyPlaceholder = t("socialEmptyReply");
              const paintedKeep =
                painted && painted !== emptyPlaceholder ? painted : "";
              const rawFinal = (
                (textOut && String(textOut).trim()) ||
                buf ||
                lastGoodBuf ||
                paintedKeep ||
                ""
              ).trim();
              if (groupId) {
                const standing = parseStandingRules(rawFinal);
                if (standing.rules.length || standing.drops.length) {
                  patchStore((prev) => {
                    const g = prev.groups.find((x) => x.id === groupId);
                    if (!g) return prev;
                    g.announcements = applyStandingRules(
                      g.announcements || [],
                      standing.rules,
                      standing.drops,
                      { kind: "agent", id: slot.agent.id, name: slot.agent.name },
                    );
                    return prev;
                  });
                }
              }
              (slot as any)._asks = coordinationAsksInText(rawFinal);
              if (!slotOptional) {
                const full = cleanDisplayText(
                  extractOwnReply(rawFinal, slot.agent.name) || stripAgentTags(rawFinal),
                );
                slot.text = full || paintedKeep || emptyPlaceholder;
                slot.streaming = false;
                slot.silent = false;
                slot.replyConfirmed = true;
                slot.decided = true;
                patchStore((prev) =>
                  appendAgentMemory(
                    prev,
                    slot.agent.id,
                    `用户: ${(raw || text).slice(0, 100)} → 我: ${(slot.text || "").slice(0, 120)}`,
                    groupId
                    ? `group:${groupId}`
                    : dmMemoryContext(slot.agent.id, getDmActiveSession(store, slot.agent.id)),
                  ),
                );
                paint();
                return;
              }
              const assignedTasksBeforeResponse = (((slot as any)._tasks as string[]) || []).filter(Boolean);
              const decision = parseAgentDecision(rawFinal);
              const cleared = Boolean((slot as { _clearedToSpeak?: boolean })._clearedToSpeak);
              if (cleared) {
                const kept =
                  visibleReplyAfterJudge(rawFinal) || visibleReplyAfterJudge(String(lastGoodBuf || ""));
                slot.text = kept;
                slot.reasoningStreaming = false;
                slot.streaming = false;
                slot.silent = !kept;
                slot.replyConfirmed = Boolean(kept);
                slot.decided = true;
              } else if (decision.action === "silent") {
                slot.text = "";
                slot.reasoning = "";
                slot.reasoningStreaming = false;
                slot.streaming = false;
                slot.silent = true;
                slot.replyConfirmed = false;
                slot.decided = true;
              } else {
                const body =
                  cleanDisplayText(
                    extractOwnReply(decision.text || rawFinal, slot.agent.name) ||
                      stripAgentTags(decision.text || rawFinal) ||
                      "",
                  ) || paintedKeep || emptyPlaceholder;
                slot.text = body;
                slot.streaming = false;
                slot.silent = false;
                slot.replyConfirmed = true;
                slot.decided = true;
              }
              const goalSatisfied = (slot as { _goalSatisfied?: boolean })._goalSatisfied === true;
              const mergedTasks =
                wave === 2
                  ? mergeExecutionTasks(assignedTasksBeforeResponse, decision.tasks)
                  : decision.tasks;
              const doneTasks =
                wave === 2
                  ? doneTasksForExecution(assignedTasksBeforeResponse, decision.doneTasks, goalSatisfied)
                  : decision.doneTasks;
              const outcome = { ...decision, tasks: mergedTasks, doneTasks };
              (slot as any)._facts = decision.facts;
              (slot as any)._tasks = mergedTasks;
              (slot as any)._doneTasks = doneTasks;
              (slot as any)._forgetFacts = decision.forgetFacts;
              if (view.kind === "group") {
                const asks = ((slot as any)._asks as Array<{ targetName: string; question: string }>) || [];
                const tasks = (decision.tasks || []).filter(Boolean);
                if (wave === 1 && asks.length) {
                  for (const ask of asks) {
                    noteAgentActivity(slot.agent, "ask", `请 @${ask.targetName} 补充：${ask.question}`);
                  }
                }
                if (wave === 1 && tasks.length) {
                  noteAgentActivity(slot.agent, "claim", `认领：${tasks.join("；")}`);
                } else if (wave === 1 && slot.silent) {
                  noteAgentActivity(slot.agent, "skip", "判断后不参与：无新增处理事项或已有更合适负责人");
                } else if (wave === 2) {
                  noteAgentActivity(
                    slot.agent,
                    goalSatisfied || slot.silent ? "done" : "execute",
                    slot.silent
                      ? "执行后无需补充"
                      : goalSatisfied
                        ? "已完成本次处理并提交结果"
                        : "尚未达到目标，继续处理",
                  );
                }
              }
              // Dynamic board write: each finished agent updates shared memory immediately.
              // Assigned work stays open until goal_settled says the user goal is met.
              if (groupId && liveBoard && optionalReply) {
                ctx.liveBoard = liveBoard = applyAgentOutcome(liveBoard, slot.agent, outcome, { autoExtract: true, writeFacts: false });
                ctx.liveBoard = liveBoard = bumpWorkingEntry(liveBoard, {
                  agentId: slot.agent.id,
                  agentName: slot.agent.name,
                  action: slot.silent ? "silent" : "reply",
                  summary: slot.silent ? "" : summarizeReply(slot.text),
                  at: Date.now(),
                });
                patchStore((prev) => setGroupBoard(prev, groupId, liveBoard!));
              }
              
              // Private per-agent memory (what I saw / said this turn)
              patchStore((prev) =>
                appendAgentMemory(
                  prev,
                  slot.agent.id,
                  slot.silent
                    ? `群消息（未发言）: ${(raw || text).slice(0, 120)}`
                    : `用户: ${(raw || text).slice(0, 100)} → 我: ${(slot.text || "").slice(0, 120)}`,
                  groupId
                    ? `group:${groupId}`
                    : dmMemoryContext(slot.agent.id, getDmActiveSession(store, slot.agent.id)),
                ),
              );

              paint();
            },
            onError: (err) => {
              const msg = String(err || "error");
              onToast(msg);
              slot.stage = "error";
              setAgentLive((prev) => ({
                ...prev,
                [slot.agent.id]: {
                  ...prev[slot.agent.id],
                  phase: "error",
                  tools: prev[slot.agent.id]?.tools || [],
                  summary: msg.slice(0, 240),
                  updatedAt: Date.now(),
                },
              }));
              slot.text = t("socialSendFail") + ": " + msg;
              slot.streaming = false;
              slot.silent = false;
              slot.replyConfirmed = true;
              slot.decided = true;
              paint();
            },
            onAbort: () => {
              slot.streaming = false;
              slot.stage = "done";
              if (turnPaused) {
                slot.silent = true;
                slot.text = "";
                slot.decided = true;
                slot.replyConfirmed = false;
                paint();
                return;
              }
              if (slotOptional && !slot.replyConfirmed) {
                slot.silent = true;
                slot.text = "";
                slot.reasoning = "";
              } else if (!cleanDisplayText(slot.text || "")) {
                slot.text = "…";
                slot.replyConfirmed = true;
                slot.silent = false;
              }
              slot.decided = true;
              paint();
            },
          },
          ac.signal,
          "agent",
          text,
          chatWorkspacePath,
          // The read/judge pass and the final synthesis are deliberately
          // tool-free.  Only an explicitly claimed execution pass may use
          // Sidekick tools.
          freeTurn
            ? !freeTurn.tools
            : Boolean(view.kind === "group" && optionalReply && (wave === 1 || opts?.synthesis)),
          // One-shot social streams force a throwaway backend session — hide
          // it from the workbench history. The linked lead DM shares the real
          // workbench session instead, so it stays visible.
          !linkedDm,
          freeTurn
            ? true
            : Boolean(harnessPlan && harnessPlan.phase === "work" && wave === 2 && !opts?.synthesis),
        );
      } catch (e: any) {
        if (ac.signal.aborted) return;
        slot.text = t("socialSendFail") + ": " + String(e?.message || e);
        slot.streaming = false;
        slot.decided = true;
        paint();
      }
        };
}
