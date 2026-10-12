// @ts-nocheck
import {
  answerAsk,
  attachSessionEvents,
  confirmPlan,
  decideApproval,
  fetchMemory,
  fetchSession,
  fetchSkills,
  stopSession,
  streamChat,
  type RuntimeEvent,
  type SessionDetail,
  type SessionItem,
  type SkillItem,
} from "../api";
import type { ActivePlan } from "../components/TaskPlanPanel";
import {
  ASK_CUSTOM_KEY,
  type ApprovalPrompt,
  type AskPrompt,
  type ChatMsg,
  type DetailView,
  type LiveLine,
  type MsgAttachment,
  type QueuedMsg,
  type SettingsTab,
  type SubNode,
  type SubTranscriptItem,
  type ToolCard,
} from "../types/chat";
import type { PlanConfirmState, PlanTask } from "../types/plan";
import type { MsgKey } from "../i18n";
import {
  upsertToolDelta,
  upsertToolEnd,
  upsertToolStart,
  type ToolUpsertCtx,
} from "./chat/toolUpserts";

export type ChatStreamDeps = {
  t: (key: MsgKey, ...args: string[]) => string;
  locale: string;
  sessionId: string | null;
  sessionIdRef: React.MutableRefObject<string | null>;
  activeWs: { path: string; name: string; id?: string } | null;
  chatMode: "plan" | "agent";
  setMessages: React.Dispatch<React.SetStateAction<ChatMsg[]>>;
  setInput: React.Dispatch<React.SetStateAction<string>>;
  setBusy: React.Dispatch<React.SetStateAction<boolean>>;
  setLive: React.Dispatch<React.SetStateAction<LiveLine[]>>;
  setSubs: React.Dispatch<React.SetStateAction<SubNode[]>>;
  setDetail: React.Dispatch<React.SetStateAction<DetailView>>;
  setCtx: React.Dispatch<React.SetStateAction<{ tokens: number; limit: number }>>;
  setCompressState: React.Dispatch<
    React.SetStateAction<{
      active: boolean;
      message: string;
      attempt: number;
      maxAttempts: number;
      before: number;
      after?: number;
    } | null>
  >;
  setActivePlan: React.Dispatch<React.SetStateAction<ActivePlan | null>>;
  setPlanConfirm: React.Dispatch<React.SetStateAction<PlanConfirmState | null>>;
  setApproval: React.Dispatch<React.SetStateAction<ApprovalPrompt | null>>;
  setAskPrompt: React.Dispatch<React.SetStateAction<AskPrompt | null>>;
  setAskChoice: React.Dispatch<React.SetStateAction<string>>;
  setAskOtherText: React.Dispatch<React.SetStateAction<string>>;
  setFsRefresh: React.Dispatch<React.SetStateAction<number>>;
  setToast: React.Dispatch<React.SetStateAction<string>>;
  setSessionId: React.Dispatch<React.SetStateAction<string | null>>;
  setStats: React.Dispatch<React.SetStateAction<{ tokens: number; iters: number }>>;
  setSkills: React.Dispatch<React.SetStateAction<SkillItem[]>>;
  setMemory: React.Dispatch<React.SetStateAction<string>>;
  setSettingsTab: (tab: SettingsTab) => void;
  setSettingsOpen: (v: boolean) => void;
  setQueued: React.Dispatch<React.SetStateAction<QueuedMsg[]>>;
  approval: ApprovalPrompt | null;
  askPrompt: AskPrompt | null;
  planConfirm: PlanConfirmState | null;
  stickBottomRef: React.MutableRefObject<boolean>;
  askPendingRef: React.MutableRefObject<boolean>;
  planPendingRef: React.MutableRefObject<boolean>;
  executingPlanIdRef: React.MutableRefObject<string | null>;
  refreshSessionsRef: React.MutableRefObject<(page?: number) => Promise<void>>;
};

