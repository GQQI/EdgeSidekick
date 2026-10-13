import type { HarnessPlaybook } from "./harness";

export type SocialFolder = {
  id: string;
  name: string;
  sort?: number;
  color?: string;
};

export type SocialAgent = {
  id: string;
  name: string;
  title: string;
  color: string;
  /** Short blurb shown in lists. */
  blurb: string;
  /** Duty / responsibility written by the user; fed into prompts. */
  duty: string;
  persona: string;
  /** Optional avatar as data URL. */
  avatar?: string;
  /** User-facing nickname / note on the list page. */
  remark?: string;
  /** Free-form placement / role tag, e.g. "前线" / "审稿". */
  location?: string;
  /** Optional list folder (分组) id. */
  folderId?: string;
  /** Workspace path bound to this agent only (not global). */
  workspace?: string;
  /** Display order; lower = higher. */
  sort?: number;
  pinned?: boolean;
  skills: string[];
  tools: string[];
};

export type SocialAnnouncement = {
  id: string;
  title: string;
  body: string;
  createdAt: number;
  authorKind?: "user" | "agent";
  authorId?: string;
  authorName?: string;
};

export type SocialSharedItem = {
  id: string;
  kind: "file" | "link" | "note";
  title: string;
  detail?: string;
  createdAt: number;
  /** Who wrote this shared note (user or agent co-author). */
  authorKind?: "user" | "agent";
  authorId?: string;
  authorName?: string;
};

export type SocialGroup = {
  id: string;
  name: string;
  topic?: string;
  memberIds: string[];
  announcements: SocialAnnouncement[];
  shared: SocialSharedItem[];
  createdAt: number;
  /** Optional group avatar as data URL. */
  avatar?: string;
  remark?: string;
  location?: string;
  /** Optional list folder (分组) id. */
  folderId?: string;
  /** Workspace path bound to this group chat only (not global). */
  workspace?: string;
  sort?: number;
  pinned?: boolean;
  /** When true, group messages default to multi-agent collaboration. */
  collabMode?: boolean;
};

export type SocialChatMsg = {
  id: string;
  role: "user" | "agent" | "system";
  agentId?: string;
  agentName?: string;
  text: string;
  quote?: string;
  streaming?: boolean;
  /** Agent is observing / deciding; show reading loader. */
  reading?: boolean;
  /** Single shared 'N agents reading' status row. */
  readingStatus?: boolean;
  /** Files shared with this conversation. All group members receive their paths and extracted text. */
  attachments?: Array<{ name: string; path: string; kind?: string }>;
  /** Live browser preview if the agent opened a page during this turn. */
  browserUrl?: string;
  browserBusy?: boolean;
  /** Model thinking / reasoning (optional). */
  reasoning?: string;
  reasoningStreaming?: boolean;
  /** User reward for this reply, 1–5. Later turns read these scores. */
  score?: number;
  /** Transient coordination status. Dropped on reload. */
  collaboration?: boolean;
  /** The orchestrator's plan for this turn, shown as a card above the replies. */
  planCard?: SocialPlanCard;
  at: number;
};

export type SocialPlanCard = {
  mode: string;
  label: string;
  goal: string;
  reason?: string;
  steps: Array<{ id: string; name: string; task: string; expected?: string }>;
  review?: string;
  synthesizer?: string;
  source?: "model" | "fallback";
};

/** Scores the user gave to replies. High scores are kept as style, low scores as things to avoid. */
export type HermesReward = {
  id: string;
  agentId: string;
  agentName: string;
  score: number;
  excerpt: string;
  at: number;
};

export type SituationKind = "chat" | "capability" | "code" | "browser" | "other";

export type StrategyRow = {
  situation: SituationKind;
  policy: string;
  attempts: number;
  successes: number;
  ratingSum: number;
  ratingCount: number;
  workflows: string[];
};

export type StrategyTable = Record<SituationKind, StrategyRow>;

export type SocialThreadKey = string; // `group:<id>` | `dm:<agentId>` | `dm:<agentId>:<sessionId>`


export type BoardFact = {
  id: string;
  text: string;
  byAgentId?: string;
  byAgentName?: string;
  at: number;
  /** Last time this fact was confirmed/updated. */
  updatedAt?: number;
  /** How many times agents reinforced this fact. */
  hits?: number;
};

export type BoardTask = {
  id: string;
  text: string;
  ownerId?: string;
  ownerName?: string;
  status: "open" | "done";
  at: number;
};

export type BoardTurnEntry = {
  agentId: string;
  agentName: string;
  action: "reply" | "silent";
  /** Short summary of what they said or did (empty when silent). */
  summary: string;
  at: number;
};

export type BoardRound = {
  id: string;
  userPreview: string;
  entries: BoardTurnEntry[];
  at: number;
};

/** Durable, continuously updated group-shared memory (not just prompt stuffing). */
export type GroupSharedBoard = {
  revision: number;
  updatedAt: number;
  facts: BoardFact[];
  tasks: BoardTask[];
  /** Latest user-turn outcomes (who spoke / stayed silent). */
  lastRound: BoardRound | null;
  /** In-progress round while agents are still answering (live). */
  workingRound: BoardRound | null;
  /** Rolling history of recent rounds for longer context. */
  recentRounds: BoardRound[];
  /** Compact rolling digest for prompt (auto-maintained). */
  digest?: string;
};


/** Per-agent private memory (separate from group shared board). */
export type AgentMemoryNote = {
  id: string;
  text: string;
  at: number;
  /** e.g. group:<id> | dm | system */
  context?: string;
};

export type AgentPrivateMemory = {
  notes: AgentMemoryNote[];
  updatedAt: number;
};

export type SocialStore = {
  agents: SocialAgent[];
  groups: SocialGroup[];
  /** User-defined list folders for filtering agents/groups. */
  folders: SocialFolder[];
  threads: Record<SocialThreadKey, SocialChatMsg[]>;
  /** Per-group shared boards, keyed by group id. */
  boards: Record<string, GroupSharedBoard>;
  /** Per-agent private memory, keyed by agent id. */
  agentMemories: Record<string, AgentPrivateMemory>;
  /** Active DM session id per agent (`main` or a generated id). Does not affect group threads. */
  dmActiveSession?: Record<string, string>;
  /**
   * Per-agent DM knowledge mode for the active session:
   * - aware: may use group memories / group brief (default when opening from roster/group)
   * - blank: blank slate — no group memories, no group brief
   */
  dmSessionMode?: Record<string, "aware" | "blank">;
  /** Bumped when clearing a DM thread so backend stream sessions rotate without wiping memories. */
  dmStreamNonce?: Record<string, number>;
  /** Reward notes from message scores. Capped so the prompt stays short. */
  hermesRewards?: HermesReward[];
  /** Situation policies updated from scores and task outcomes. */
  strategy?: StrategyTable;
  /** Per-task collaboration topologies that survived real runs. */
  harness?: HarnessPlaybook;
  /** Short notes of who edited which file, keyed by thread. */
  workLogs?: Record<string, string[]>;
};
