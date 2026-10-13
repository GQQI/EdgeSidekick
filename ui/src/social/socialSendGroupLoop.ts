// @ts-nocheck
/* Group turn driver: plans with the orchestrator (collab.ts), then runs each member through runOne. */
/* eslint-disable @typescript-eslint/no-explicit-any */
import type { SocialChatMsg } from "./types";
import {
  formatPlanNotice,
  isPass,
  modeLabel,
  parseReview,
  runCollaboration,
  type CollabEvent,
  type CollabPlan,
  type MemberTask,
} from "./collab";
import { formatBoardForPrompt } from "./sharedBoard";

export type createSocialGroupLoopCtx = Record<string, any>;

const ROLE_VERB: Record<string, string> = {
  greeter: "回复",
  lead: "处理",
  worker: "处理分工",
  discussant: "发表看法",
  handoff: "回应同事",
  reviewer: "复核",
  reviser: "修订",
  synthesizer: "汇总",
};

/** Planner budget. Reasoning models can take a while; past this the turn falls back to a single answerer. */
const PLAN_TIMEOUT_MS = 60000;

export function planCardFor(plan: CollabPlan, roster: Array<{ id: string; name: string }>) {
  const name = (id: string) => roster.find((a) => a.id === id)?.name || id;
  return {
    mode: plan.mode,
    label: modeLabel(plan.mode),
    goal: plan.goal,
    reason: plan.reason,
    steps: plan.steps.map((s) => ({ id: s.id, name: name(s.agentId), task: s.task, expected: s.expected })),
    review: plan.review ? name(plan.review.agentId) : undefined,
    synthesizer: plan.synthesize ? name(plan.leadId) : undefined,
    source: plan.source,
  };
}

/** Plans worth showing: anything beyond one person simply answering. */
export function planIsWorthShowing(plan: CollabPlan): boolean {
  return plan.mode !== "chat" && (plan.mode !== "answer" || Boolean(plan.review));
}

export function createSocialGroupLoop(ctx: createSocialGroupLoopCtx) {
  const {
    ac,
    chatWorkspacePath,
    cleanDisplayText,
    commitGroupMessage,
    commitRound,
    confirmationText,
    getGroupBoard,
    groupId,
    harnessNotices,
    hist,
    isActiveAgentPhase,
    isStaleSend,
    noteAgentActivity,
    pluginDigest,
    paint,
    patchStore,
    pauseForUserInput,
    raw,
    rosterForPrompt,
    runOne,
    setAgentLive,
    setGroupBoard,
    slots,
    streamChat,
    t,
    text,
    uid,
    userMsg,
    visibleReplyAfterJudge,
    turnState,
  } = ctx;
  const committedMsgs = ctx.committedMsgs;
  let liveBoard = ctx.liveBoard;

  const setStatus = (status: string) => {
    turnState.status = status;
    paint();
  };
  const nameOf = (id: string) => rosterForPrompt.find((a) => a.id === id)?.name || "成员";
  const pushSystem = (body: string, extra: Record<string, unknown> = {}) => {
    harnessNotices.push({ id: uid("sys"), role: "system", text: body, at: Date.now(), ...extra });
  };
  const setLive = (agentId: string, phase: string, summary: string) =>
    setAgentLive((prev) => ({
      ...prev,
      [agentId]: { ...(prev[agentId] || {}), phase, tools: prev[agentId]?.tools || [], summary, updatedAt: Date.now() },
    }));

  return async () => {
    const priorMsgs: SocialChatMsg[] = hist.filter(
      (m) => (m.role === "user" || m.role === "agent") && !m.readingStatus && String(m.text || "").trim(),
    );
    const toLine = (m: SocialChatMsg) => ({
      id: m.id,
      role: m.role === "user" ? "user" : "agent",
      agentId: m.agentId,
      agentName: m.agentName,
      text: m.id === userMsg.id ? text : String(m.text || ""),
    });
    const toMsg = (line: any): SocialChatMsg => {
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
    let lastError = "";

    /** Planner: one classify call to the configured main model (same path as every other member call). */
    const plan = async (prompt: string, signal?: AbortSignal): Promise<string | null> => {
      let out = "";
      let failed = "";
      const local = new AbortController();
      const timer = window.setTimeout(() => local.abort(), PLAN_TIMEOUT_MS);
      const onParent = () => local.abort();
      signal?.addEventListener("abort", onParent);
      try {
        await streamChat(
          prompt,
          `social-plan-${groupId || "g"}-${uid("p")}`,
          {
            onEvent: () => {},
            onFinal: (final) => {
              out = String(final || "");
            },
            onError: (err) => {
              failed = String(err || "error");
            },
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
      } catch (err) {
        failed = String((err as Error)?.message || err);
      } finally {
        window.clearTimeout(timer);
        signal?.removeEventListener("abort", onParent);
      }
      if (signal?.aborted || isStaleSend()) return null;
      if (failed && !out.trim()) throw new Error(failed);
      return out.trim() || null;
    };

    const runMember = async (task: MemberTask): Promise<{ text: string; error?: string }> => {
      const s = slotOf(task.agent.id);
      if (!s || ac.signal.aborted || turnState.turnPaused) return { text: "" };
      const trigger = task.trigger;
      const upcoming =
        task.plan.mode === "pipeline" && task.step
          ? task.plan.steps
              .slice(task.plan.steps.findIndex((x) => x.id === task.step.id) + 1)
              .map((x) => ({ name: nameOf(x.agentId), task: x.task }))
          : [];
      s.id = uid("m");
      s.text = "";
      s.reasoning = "";
      s.silent = false;
      s.decided = false;
      s.replyConfirmed = true;
      s.streaming = true;
      s.stage = "executing";
      s.activityText = "";
      s._clearedToSpeak = true;
      s._sentenceOnly = false;
      s._tasks = task.step ? [task.step.task] : [];
      s._freeReply = {
        history: task.history.slice(0, -1).map(toMsg),
        latest: {
          author: trigger.role === "user" ? "用户" : trigger.agentName || "成员",
          isUser: trigger.role === "user",
          text: trigger.text,
        },
        addressed: true,
        brief: task.brief,
        mustAnswer: task.mustAnswer,
        tools: task.tools,
        collab: {
          role: task.role,
          mode: task.plan.mode,
          goal: task.plan.goal,
          task: task.task,
          expected: task.expected,
          peers: task.peers,
          upstream: task.upstream,
          contributions: task.contributions,
          issues: task.issues,
          tools: task.tools,
          mustAnswer: task.mustAnswer,
          downstream: upcoming,
          attempt: task.attempt,
        },
      };
      setLive(s.agent.id, "executing", `正在${ROLE_VERB[task.role] || "回复"}…`);
      paint();
      const before = committedMsgs.length;
      try {
        await runOne(s, 1);
      } finally {
        s._freeReply = null;
      }
      if (ac.signal.aborted || isStaleSend()) return { text: "" };
      const error = String(s._error || "");
      // Rounds already sealed into their own bubbles during the stream.
      const sealed = committedMsgs
        .slice(before)
        .filter((m) => m.agentId === s.agent.id)
        .map((m) => String(m.text || "").trim())
        .filter(Boolean);
      const finalText = error ? "" : visibleReplyAfterJudge(cleanDisplayText(String(s.text || "")));
      let spoken = [...sealed];
      if (finalText && finalText !== t("socialEmptyReply") && !isPass(finalText)) {
        let shown = finalText;
        if (task.role === "reviewer") {
          const verdict = parseReview(finalText);
          shown = verdict.approved
            ? `复核通过${verdict.issues ? `：${verdict.issues}` : "。"}`
            : `复核意见：\n${verdict.issues}`;
        }
        if (commitGroupMessage(s, shown)) spoken.push(finalText);
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
      setLive(s.agent.id, error ? "error" : "done", error ? error.slice(0, 200) : "已完成");
      paint();
      if (error && !spoken.length) {
        lastError = error;
        return { text: "", error };
      }
      return { text: spoken.join("\n\n") };
    };

    const onEvent = (event: CollabEvent) => {
      if (isStaleSend()) return;
      switch (event.type) {
        case "planning":
          setStatus("正在安排分工…");
          break;
        case "plan": {
          const p = event.plan;
          if (p.source === "fallback" && p.fallbackReason !== "no_planner") {
            console.warn("[collab] planner unavailable:", p.fallbackReason);
          }
          if (planIsWorthShowing(p)) {
            pushSystem(formatPlanNotice(p, rosterForPrompt), { planCard: planCardFor(p, rosterForPrompt) });
          }
          for (const step of p.steps) {
            const agent = rosterForPrompt.find((a) => a.id === step.agentId);
            if (agent) noteAgentActivity(agent, "claim", `分配到：${step.task}`);
          }
          for (const s of slots) {
            if (!p.steps.some((x) => x.agentId === s.agent.id) && p.review?.agentId !== s.agent.id) {
              s.stage = "done";
              setLive(s.agent.id, "done", "本轮不需要参与");
            }
          }
          setStatus(`${modeLabel(p.mode)} · 已安排 ${new Set(p.steps.map((x) => x.agentId)).size} 位成员`);
          break;
        }
        case "step_start":
          setStatus(`${nameOf(event.agentId)} 正在${ROLE_VERB[event.role] || "回复"}…`);
          break;
        case "step_failed": {
          const agent = rosterForPrompt.find((a) => a.id === event.agentId);
          if (agent) noteAgentActivity(agent, "skip", `没能完成：${event.error}`);
          pushSystem(
            event.reassignedTo
              ? `${nameOf(event.agentId)} 没能完成（${event.error}），已转交给 ${nameOf(event.reassignedTo)}。`
              : `${nameOf(event.agentId)} 没能完成（${event.error}）。`,
          );
          paint();
          break;
        }
        case "review": {
          const agent = rosterForPrompt.find((a) => a.id === event.agentId);
          if (agent) noteAgentActivity(agent, "done", event.approved ? "复核通过" : "复核提出修改意见");
          break;
        }
        case "handoff": {
          const agent = rosterForPrompt.find((a) => a.id === event.fromId);
          if (agent) noteAgentActivity(agent, "ask", `请 @${nameOf(event.toId)} 协助`);
          break;
        }
        default:
          break;
      }
    };

    let capabilities = "";
    try {
      capabilities = pluginDigest ? String((await pluginDigest()) || "") : "";
    } catch {
      capabilities = "";
    }
    if (isStaleSend() || ac.signal.aborted) return;

    const result = await runCollaboration({
      roster: rosterForPrompt,
      history: priorMsgs.map(toLine),
      plan,
      runMember,
      signal: ac.signal,
      concurrency: 2,
      maxMessages: Math.max(6, Math.min(14, rosterForPrompt.length * 3)),
      boardDigest: liveBoard ? formatBoardForPrompt(liveBoard) : "",
      capabilities,
      onEvent,
      shouldStop: (line) =>
        line.role === "agent" &&
        pauseForUserInput(line.agentName || "", confirmationText(String(line.agentId || ""), line.text)),
      newId: () => uid("m"),
    });
    if (isStaleSend()) return;
    turnState.ended = true;
    turnState.status = "";
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
      // Members write tasks to the stored board while they run; our copy is from turn start.
      patchStore((prev) => {
        const latest = (getGroupBoard && getGroupBoard(prev, groupId)) || liveBoard;
        ctx.liveBoard = liveBoard = commitRound(latest, raw || text);
        return setGroupBoard(prev, groupId, liveBoard);
      });
    }
    if (result.reason === "failed" && !result.messages.length) {
      pushSystem(
        `这一轮没有成员成功回复${lastError ? `（${lastError}）` : ""}。请检查「设置 → 模型」后重试。`,
      );
    } else if (result.reason === "cap" && !turnState.turnPaused) {
      pushSystem(`本轮成员已发言 ${result.messages.length} 条，达到上限后自动停止。`, { collaboration: true });
    }
    paint();
  };
}
