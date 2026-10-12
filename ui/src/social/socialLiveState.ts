/** Live agent phase / activity types + labels for SocialShell. */
export type SocialAttachment = {
  id: string;
  name: string;
  path: string;
  kind: string;
  text: string;
  size: number;
};

export type AgentLivePhase = "idle" | "reading" | "deciding" | "queued" | "executing" | "synthesizing" | "done" | "error";
export type AgentActivityKind = "read" | "decide" | "ask" | "claim" | "execute" | "tool" | "done" | "skip";
export type AgentActivity = {
  id: string;
  kind: AgentActivityKind;
  detail: string;
  at: number;
};
export type AgentLiveState = {
  phase: AgentLivePhase;
  tools: { id: string; name: string; status: "running" | "done" | "error"; detail?: string }[];
  /** Detailed, per-turn activity belongs in the member's task detail, not the chat timeline. */
  activity?: AgentActivity[];
  summary?: string;
  updatedAt: number;
};

export function isActiveAgentPhase(phase: AgentLivePhase): boolean {
  return phase === "reading" || phase === "deciding" || phase === "queued" || phase === "executing" || phase === "synthesizing";
}

export function agentLivePhaseLabel(phase: AgentLivePhase): string {
  const labels: Record<AgentLivePhase, string> = {
    idle: "待命",
    reading: "读取中",
    deciding: "判断中",
    queued: "已认领，排队中",
    executing: "处理中",
    synthesizing: "汇总中",
    done: "已完成",
    error: "处理失败",
  };
  return labels[phase];
}

export function agentActivityLabel(kind: AgentActivityKind): string {
  const labels: Record<AgentActivityKind, string> = {
    read: "已读",
    decide: "判断",
    ask: "提问",
    claim: "认领",
    execute: "执行",
    tool: "工具",
    done: "完成",
    skip: "跳过",
  };
  return labels[kind];
}
