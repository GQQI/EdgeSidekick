/** Shared chat / tool UI types for the main app. */

export type ToolCard = {
  id: string;
  callId: string;
  name: string;
  args: unknown;
  argsRaw?: string;
  result?: string;
  status: "streaming" | "running" | "done" | "error" | "pending";
  summary?: string;
};

export type ApprovalPrompt = {
  approvalId: string;
  callId: string;
  name: string;
  args: unknown;
  summary: string;
};

export type AskOption = { key: string; label: string };

export type AskPrompt = {
  askId: string;
  callId: string;
  sessionId: string;
  question: string;
  options: AskOption[];
  allowCustom: boolean;
  customLabel: string;
  summary: string;
};

export type SubTool = {
  id: string;
  callId: string;
  name: string;
  summary: string;
  status: "streaming" | "running" | "done" | "error" | "pending";
  args?: unknown;
  result?: string;
};

export type SubTranscriptItem =
  | {
      id: string;
      kind: "assistant";
      text: string;
      streaming?: boolean;
      reasoning?: string;
      reasoningStreaming?: boolean;
      /** Order key so a shared stage can replay who spoke after whom. */
      turnAt?: number;
    }
  | { id: string; kind: "tool"; tool: SubTool };

export type SubNode = {
  id: string;
  goal: string;
  status: "running" | "done" | "error";
  summary?: string;
  role?: string;
  activity?: string;
  /** party / talk = dialogue cast; task = parallel workers. */
  kind?: "party" | "talk" | "task";
  /** Parent agent id so nested helpers never become a new peer. */
  parent_id?: string;
  transcript: SubTranscriptItem[];
  children?: SubNode[];
};

export type MsgAttachment = {
  name: string;
  path: string;
  kind?: string;
};

export type ChatMsg = {
  id: string;
  role: "user" | "assistant" | "tool" | "system" | "subagent";
  content: string;
  streaming?: boolean;
  reasoning?: string;
  reasoningStreaming?: boolean;
  tool?: ToolCard;
  subagent?: SubNode;
  /** Wave id so ask_user / next spawn is a new canvas, not the previous one. */
  stage?: number;
  /** Which agent produced this row — never mix two agents into one bubble. */
  agent_id?: string;
  /** Display name for assistant bubble label (e.g. 墨白). */
  speaker_name?: string;
  /** Uploaded files shown as chips in the bubble (not the full model payload). */
  attachments?: MsgAttachment[];
  /** When this message was recorded, in epoch milliseconds. */
  ts?: number;
  /** Background shell job completion card. */
  jobNotice?: { job_id: string; status: string; exit_code?: number | null };
};

export type LiveLine = { id: string; text: string; kind: string };
export type SettingsTab =
  | "workspace"
  | "safety"
  | "model"
  | "mcp"
  | "skills"
  | "runtime"
  | "appearance"
  | "account";

export type QueuedMsg = {
  id: string;
  text: string;
  userDisplay?: string;
  attachments?: MsgAttachment[];
};

export type PendingConfirm = {
  key: string;
  title: string;
  detail?: string;
  confirmLabel?: string;
  run: () => void | Promise<void>;
};


/** Directed multi-agent AskPacket edge for canvas visualization. */
export type AskEdge = {
  id: string;
  from_id: string;
  to_id: string;
  kind: "consult" | "delegate" | "dialogue" | string;
  what?: string;
  how?: string;
  who?: string;
  when?: string;
  why?: string;
  need?: string;
  goal_slice?: string;
  visibility?: {
    share?: string[];
    redact?: string[];
    quote_policy?: string;
    relation?: string;
  };
  status?: "asking" | "done" | "error" | string;
  summary?: string;
};

export type DetailView =
  | { type: "tool"; tool: ToolCard }
  | { type: "subagent"; subagent: SubNode }
  | { type: "ask"; ask: AskEdge }
  | { type: "changes"; selectedPath: string | null }
  | {
      type: "file";
      path: string;
      content: string;
      dirty?: boolean;
      kind: string;
      mime?: string;
      size?: number;
      preview?: string;
      editable?: boolean;
      message?: string;
      rawUrl?: string;
      /** Which open workspace this file belongs to (undefined = default workspace). */
      workspace?: string;
      highlightQuery?: string;
      focusLine?: number;
      forceEdit?: boolean;
    }
  | null;

export const ASK_CUSTOM_KEY = "custom";

export const FS_MUTATING_TOOLS = new Set([
  "write_file",
  "str_replace",
  "delete_file",
  "run_shell",
  "skill_save",
]);

export const ATTACH_MARKER = "用户上传了以下附件，请根据附件内容进行分析与回答：";
