import { json, apiFetch, authHeaders, type RuntimeEvent } from "./client";
import { parseSseBlock } from "./stream";

export type GitFileEntry = {
  path: string;
  xy: string;
  staged: boolean;
  unstaged: boolean;
  untracked: boolean;
  kind?: string;
  added?: number;
  deleted?: number;
};

export type GitBranch = {
  name: string;
  current?: boolean;
  remote?: boolean;
};

export type GitSnapshot = {
  is_repo: boolean;
  branch?: string;
  head?: string;
  files?: GitFileEntry[];
  status?: string;
  error?: string;
  message?: string;
  branches?: GitBranch[];
  ahead?: number;
  behind?: number;
  unpublished?: number;
  upstream?: string;
  remote?: string;
  remote_url?: string;
  totals?: { files: number; added: number; deleted: number };
};

export const fetchGit = (workspace?: string) =>
  json<GitSnapshot>(
    workspace ? `/api/git?workspace=${encodeURIComponent(workspace)}` : "/api/git",
  );

export type ShellJobSnapshot = {
  job_id: string;
  pid: number;
  command: string;
  cwd: string;
  session_id?: string;
  background?: boolean;
  status: string;
  exit_code: number | null;
  started_at: number;
  ended_at: number | null;
  elapsed_sec: number;
  log_lines: number;
  log: string;
};

export const fetchShellJobs = (includeDone = true) =>
  json<{ jobs: ShellJobSnapshot[]; running: number }>(
    `/api/shell-jobs?include_done=${includeDone ? "true" : "false"}`,
  );

export const fetchShellJob = (jobId: string, tail = 120) =>
  json<ShellJobSnapshot>(
    `/api/shell-jobs/${encodeURIComponent(jobId)}?tail=${encodeURIComponent(String(tail))}`,
  );

export const stopShellJob = (jobId: string) =>
  json<ShellJobSnapshot>(`/api/shell-jobs/${encodeURIComponent(jobId)}/stop`, {
    method: "POST",
  });
export const deleteShellJob = (jobId: string) =>
  json<{ status: string; job_id: string }>(`/api/shell-jobs/${encodeURIComponent(jobId)}`, {
    method: "DELETE",
  });

export type ScheduleTask = {
  id: string;
  user_id?: string;
  session_id: string;
  title: string;
  prompt: string;
  kind: "once" | "daily" | "weekly" | "interval" | "monthly";
  enabled: boolean;
  at?: number;
  time?: string;
  weekdays?: number[];
  interval_minutes?: number;
  until?: string;
  month_day?: number;
  schedule_text: string;
  created_at: number;
  next_run: number;
  last_run: number | null;
  last_status: string;
  last_error?: string;
  defer_count?: number;
  run_count?: number;
};

export const fetchSchedules = () =>
  json<{ items: ScheduleTask[]; enabled: number }>("/api/schedules");

export const fetchSchedule = (taskId: string) =>
  json<ScheduleTask & { upcoming: number[] }>(
    `/api/schedules/${encodeURIComponent(taskId)}`,
  );

export const createSchedule = (body: {
  title: string;
  prompt: string;
  kind: ScheduleTask["kind"];
  at?: string;
  time?: string;
  weekdays?: number[];
  interval_minutes?: number;
  session_id: string;
  enabled?: boolean;
}) =>
  json<ScheduleTask>("/api/schedules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export const patchSchedule = (
  taskId: string,
  patch: Partial<{
    enabled: boolean;
    title: string;
    prompt: string;
    at: string;
    time: string;
    weekdays: number[];
    interval_minutes: number;
  }>,
) =>
  json<ScheduleTask>(`/api/schedules/${encodeURIComponent(taskId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });

export const deleteSchedule = (taskId: string) =>
  json<{ status: string; task_id: string }>(
    `/api/schedules/${encodeURIComponent(taskId)}`,
    { method: "DELETE" },
  );

/** Long-lived stream of scheduled-task turns (tokens, tools, final). */
export async function subscribeScheduleLive(
  onEvent: (ev: RuntimeEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const headers = await authHeaders({ Accept: "text/event-stream" });
  const r = await apiFetch("/api/schedules/live", { method: "GET", headers, signal });
  if (!r.ok || !r.body) throw new Error(`schedule live failed: ${r.status}`);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split(/\r?\n\r?\n/);
    buf = parts.pop() || "";
    for (const block of parts) {
      const ev = parseSseBlock(block);
      if (!ev || ev.type === "ping") continue;
      onEvent(ev);
    }
  }
}

export const fetchGitReview = (sessionId?: string | null, workspace?: string) => {
  const q = new URLSearchParams();
  if (sessionId) q.set("session_id", sessionId);
  if (workspace) q.set("workspace", workspace);
  const qs = q.toString();
  return json<GitSnapshot>(`/api/git/review${qs ? `?${qs}` : ""}`);
};
export type GitFileDiff = {
  path: string;
  old: string;
  new: string;
  kind: string;
  is_new?: boolean;
  is_deleted?: boolean;
  binary?: boolean;
};
export const fetchGitFileDiff = (
  path: string,
  sessionId?: string | null,
  workspace?: string,
) => {
  const q = new URLSearchParams({ path });
  if (sessionId) q.set("session_id", sessionId);
  if (workspace) q.set("workspace", workspace);
  return json<GitFileDiff>(`/api/git/file-diff?${q.toString()}`);
};
export const gitStage = (paths: string[], workspace?: string) =>
  json<GitSnapshot>("/api/git/stage", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths, workspace: workspace || null }),
  });
export const gitUnstage = (paths: string[], workspace?: string) =>
  json<GitSnapshot>("/api/git/unstage", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths, workspace: workspace || null }),
  });
export const gitCommit = (message: string, workspace?: string) =>
  json<GitSnapshot>("/api/git/commit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, workspace: workspace || null }),
  });
export const gitFetch = (workspace?: string) =>
  json<GitSnapshot>(
    `/api/git/fetch${workspace ? `?workspace=${encodeURIComponent(workspace)}` : ""}`,
    { method: "POST" },
  );
export const gitPull = (workspace?: string) =>
  json<GitSnapshot>(
    `/api/git/pull${workspace ? `?workspace=${encodeURIComponent(workspace)}` : ""}`,
    { method: "POST" },
  );
export const gitPush = (workspace?: string) =>
  json<GitSnapshot>(
    `/api/git/push${workspace ? `?workspace=${encodeURIComponent(workspace)}` : ""}`,
    { method: "POST" },
  );
export const gitCheckout = (branch: string, create = false, workspace?: string) =>
  json<GitSnapshot>("/api/git/checkout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ branch, create, workspace: workspace || null }),
  });
export const gitSetRemote = (url: string, name = "origin", workspace?: string) =>
  json<GitSnapshot>("/api/git/remote", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, name, workspace: workspace || null }),
  });

export type UndoFileEntry = {
  path: string;
  op?: string;
  id?: string;
  actor?: string;
  actor_label?: string;
  why?: string;
  tool?: string;
  call_id?: string;
  label?: string;
};

export type UndoItem = {
  id: string;
  label?: string;
  op?: string;
  ts?: number;
  session_id?: string;
  user_turn?: number;
  user_text?: string;
  files?: string[];
  file_entries?: UndoFileEntry[];
  actors?: string[];
};

export const fetchUndo = (sessionId?: string | null) =>
  json<{ count: number; items: UndoItem[] }>(
    sessionId
      ? `/api/files/undo?session_id=${encodeURIComponent(sessionId)}`
      : "/api/files/undo",
  );
export const postUndo = (
  id?: string,
  sessionId?: string | null,
  opts?: { path?: string; userTurn?: number },
) =>
  json<{ status: string; undone_count?: number; remaining?: number; errors?: string[] }>(
    "/api/files/undo",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...(id ? { id } : {}),
        ...(sessionId ? { session_id: sessionId } : {}),
        ...(opts?.path ? { path: opts.path } : {}),
        ...(typeof opts?.userTurn === "number" ? { user_turn: opts.userTurn } : {}),
      }),
    },
  );

export const replaySession = (
  id: string,
  userTurn: number,
  opts?: { restoreFiles?: boolean },
) =>
  json<{
    status: string;
    session_id: string;
    user_turn: number;
    keep_user_turns: number;
    user_text: string;
    messages: number;
    restore_files?: boolean;
    file_undo?: { undone_count?: number; partial?: boolean; errors?: string[] };
  }>(`/api/sessions/${id}/replay`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_turn: userTurn,
      restore_files: opts?.restoreFiles !== false,
    }),
  });

export type McpServer = {
  id: string;
  name: string;
  transport?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  env_keys?: string[];
  url?: string;
  headers?: Record<string, string>;
  header_keys?: string[];
  enabled: boolean;
};

export type McpSetup = {
  version: number;
  servers: McpServer[];
};

export const fetchMcp = () => json<McpSetup>("/api/mcp");

export const saveMcp = (setup: McpSetup) =>
  json<McpSetup & { status?: string }>("/api/mcp", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(setup),
  });

export const testMcpServer = (server: McpServer) =>
  json<{ ok: boolean; error?: string; tool_count?: number; tools?: unknown[] }>(
    "/api/mcp/test",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(server),
    },
  );

export type McpCatalogEntry = {
  id: string;
  name: string;
  description: string;
  author: string;
  license: string;
  homepage: string;
  category: string;
  tags: string[];
  offline_capable: boolean;
  requires_api_key: boolean;
  requires_network: boolean;
  transport: string;
  command: string;
  args: string[];
  env_placeholders: string[];
  install_hint: string;
  package: string;
  installed: boolean;
};

export type McpCatalog = {
  items: McpCatalogEntry[];
  categories: string[];
};

export const fetchMcpCatalog = () => json<McpCatalog>("/api/mcp/catalog");

export const installMcpCatalog = (body: { id: string; args?: string[]; env?: Record<string, string> }) =>
  json<McpSetup & { status?: string; id?: string; catalog?: McpCatalog }>("/api/mcp/catalog/install", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export const uninstallMcpCatalog = (id: string) =>
  json<McpSetup & { status?: string; id?: string; catalog?: McpCatalog }>("/api/mcp/catalog/uninstall", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
