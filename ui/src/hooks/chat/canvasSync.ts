import type { Dispatch, MutableRefObject, SetStateAction } from "react";
import type { ChatMsg, SubNode } from "../../types/chat";
import {
  asSubagentMsg,
  canvasTurnFromAgentTree,
  declaredDelegateSlotCount,
  findSubNode,
  isDelegateToolMsg,
  nodesFromAgentTree,
  placeCanvasMessages,
  sameSubagentSlot,
  taskItemsFromDelegateArgs,
  unionCanvasNodes,
} from "../../utils/chatHelpers";
import {
  alignCanvasSlots,
  canvasGoalsMatch,
  canvasPlaceholderId,
  limitCanvasRoots,
} from "../../utils/canvasSlots";

export type CanvasSyncCtx = {
  transcriptRef: MutableRefObject<ChatMsg[]>;
  commit: (next: ChatMsg[], immediate?: boolean) => void;
  setSubs: Dispatch<SetStateAction<SubNode[]>>;
};

function mergeSubNode(prev: SubNode | undefined, next: SubNode): SubNode {
  if (!prev) return { ...next, children: [...(next.children || [])] };
  const prevKids = prev.children || [];
  const nextKids = next.children || [];
  const kids = unionCanvasNodes(nextKids, prevKids).map((child) => {
    const older = prevKids.find((p) => p.id === child.id);
    return mergeSubNode(older, child);
  });
  const prevLen = (prev.transcript || []).length;
  const nextLen = (next.transcript || []).length;
  return {
    ...prev,
    ...next,
    children: kids,
    transcript: nextLen >= prevLen ? next.transcript : prev.transcript,
    summary: next.summary || prev.summary,
    activity: next.status === "running" ? next.activity || prev.activity : undefined,
  };
}

function stageCanvasNodes(live: ChatMsg[], stage: number): SubNode[] {
  return live
    .filter((m) => m.role === "subagent" && (m.stage ?? 0) === stage && m.subagent)
    .map((m) => m.subagent!);
}

function capStageNodes(ctx: CanvasSyncCtx, nodes: SubNode[], stage: number): SubNode[] {
  const declared = declaredDelegateSlotCount(ctx.transcriptRef.current, stage);
  return limitCanvasRoots(nodes, declared || nodes.length);
}

export function replaceStageSubagents(
  ctx: CanvasSyncCtx,
  nodes: SubNode[],
  stage?: number,
  immediate = true,
) {
  if (!nodes.length) return;
  const live = ctx.transcriptRef.current;
  const inferred =
    stage ??
    Math.max(
      0,
      ...live.filter((m) => m.role === "subagent").map((m) => m.stage ?? 0),
      ...live.map((m) => m.stage ?? 0),
    );
  const targetStage = inferred || 1;
  const capped = capStageNodes(ctx, nodes, targetStage);
  const usedPrev = new Set<string>();
  const merged = capped.map((n) => {
    const prevMsg =
      live.find(
        (m) =>
          m.role === "subagent" &&
          m.subagent &&
          !usedPrev.has(m.id) &&
          findSubNode(m.subagent, n.id),
      ) ||
      live.find(
        (m) =>
          m.role === "subagent" &&
          m.subagent &&
          !usedPrev.has(m.id) &&
          (m.stage ?? 0) === targetStage &&
          sameSubagentSlot(m.subagent, n),
      );
    if (prevMsg) usedPrev.add(prevMsg.id);
    return mergeSubNode(prevMsg?.subagent, n);
  });
  const msgs = merged.map((n) => asSubagentMsg(n, targetStage));
  ctx.commit(placeCanvasMessages(live, msgs, targetStage), immediate);
}

function canvasStageFromTranscript(live: ChatMsg[], treeTurn = 0): number {
  const stages = new Set(live.map((m) => m.stage ?? 0));
  if (treeTurn > 0 && stages.has(treeTurn)) return treeTurn;
  const subStages = live.filter((m) => m.role === "subagent").map((m) => m.stage ?? 0);
  if (subStages.length) return Math.max(...subStages);
  const delegateStages = live.filter(isDelegateToolMsg).map((m) => m.stage ?? 0);
  if (delegateStages.length) return Math.max(...delegateStages);
  if (treeTurn > 0) return treeTurn;
  return Math.max(1, ...live.map((m) => m.stage ?? 0));
}

export function applyCanvasTree(ctx: CanvasSyncCtx, tree: unknown, turn?: number) {
  const incoming = nodesFromAgentTree(Array.isArray(tree) ? tree : []);
  if (!incoming.length) return;
  const live = ctx.transcriptRef.current;
  const treeTurn = Number(turn) || canvasTurnFromAgentTree(Array.isArray(tree) ? tree : []);
  const targetStage = canvasStageFromTranscript(live, treeTurn);
  const existing = stageCanvasNodes(live, targetStage);
  const declared = declaredDelegateSlotCount(live, targetStage) || incoming.length;
  replaceStageSubagents(
    ctx,
    limitCanvasRoots(alignCanvasSlots(
      incoming.length >= existing.length ? incoming : existing,
      incoming.length >= existing.length ? existing : incoming,
    ), declared),
    targetStage,
  );
}

export function seedDelegateCanvas(
  ctx: CanvasSyncCtx,
  args: unknown,
  callId: string,
  result?: string,
  running = true,
) {
  const rec =
    args && typeof args === "object" && !Array.isArray(args)
      ? (args as Record<string, unknown>)
      : {};
  const items = taskItemsFromDelegateArgs(rec);
  if (!items.length) return;
  const live = ctx.transcriptRef.current;
  const stage = Math.max(0, ...live.map((m) => m.stage ?? 0));
  const existing = stageCanvasNodes(live, stage);
  // A partial stream of arguments must not wipe slots already painted.
  if (running && existing.length > items.length) return;
  // Same slot count: JSON is still growing character-by-character — skip
  // rebuilding the whole forest until a new task appears or the call ends.
  if (running && existing.length === items.length && existing.length > 0) return;
  const used = new Set<string>();
  const seeded: SubNode[] = items.map((item, i) => {
    const byGoal = existing.find(
      (n) => !used.has(n.id) && canvasGoalsMatch(n.goal, item.goal),
    );
    const byIndex = existing[i] && !used.has(existing[i].id) ? existing[i] : undefined;
    const hit = byGoal || byIndex;
    if (hit) {
      used.add(hit.id);
      return {
        ...hit,
        goal: item.goal || hit.goal,
        role: item.role || hit.role,
        kind: hit.kind || "task",
        status:
          running && hit.status !== "done" && hit.status !== "error" ? "running" : hit.status,
      };
    }
    return {
      id: canvasPlaceholderId("pending", callId, i),
      goal: item.goal,
      role: item.role || "leaf",
      kind: "task",
      status: running ? "running" : "done",
      activity: running ? "运行中…" : undefined,
      transcript: [],
    };
  });
  if (result) {
    try {
      const parsed = JSON.parse(result);
      if (Array.isArray(parsed)) {
        parsed.forEach((row, i) => {
          if (!row || typeof row !== "object" || !seeded[i]) return;
          const summary = String((row as { summary?: string }).summary || "").trim();
          if (!summary) return;
          seeded[i] = {
            ...seeded[i],
            summary,
            status: summary.startsWith("ERROR") ? "error" : "done",
            activity: undefined,
          };
        });
      }
    } catch {
      /* ignore incomplete JSON */
    }
  }
  replaceStageSubagents(ctx, seeded, stage, !running);
}
