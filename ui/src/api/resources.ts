import type { ModelSetup } from "../types/modelSetup";
import { normalizeModelSetup } from "../types/modelSetup";
import {
  json,
  apiFetch,
  authHeaders,
  ensureApiToken,
  getApiBase,
  type AppPrefs,
  type SkillItem,
  type SkillDetail,
  type SkillFileContent,
  type SkillValidateResult,
  type BookToSkillJob,
  type WorkspaceItem,
  type Health,
} from "./client";

export const fetchPrefs = () => json<AppPrefs>("/api/prefs");

export const savePrefs = (patch: Partial<AppPrefs>) =>
  json<AppPrefs & { status: string }>("/api/prefs", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });

/** Public health check; send token when we have one so tenant prefs match. */
export const fetchHealth = async () => {
  const headers = await authHeaders();
  const r = await apiFetch(`/api/health`, { headers });
  if (!r.ok) throw new Error(`/api/health failed: ${r.status}`);
  return r.json() as Promise<Health>;
};

/** Prefer server hostinfo probe; fall back to navigator.onLine. */
export async function fetchNetworkOnline(): Promise<boolean> {
  try {
    const h = await fetchHealth();
    if (typeof h.online === "boolean") return h.online;
  } catch {
    /* ignore */
  }
  return typeof navigator !== "undefined" ? navigator.onLine : true;
}

/** `id` is a stable hash of the resolved folder path — case/slash-insensitive,
 * so tabs/history are queried by id, never by comparing raw path strings. */
export type SessionWorkspaceRef = { path: string; name: string; id?: string };

export const createSession = (workspace?: string) =>
  json<{ id: string; demo: boolean; workspace?: SessionWorkspaceRef | null }>(
    "/api/sessions",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workspace: workspace || null }),
    },
  );

export type SessionItem = {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  messages: number;
  user_turns?: number;
  busy?: boolean;
  demo?: boolean;
  source?: string;
  workspace?: string;
  workspace_id?: string;
  workspace_name?: string;
  is_current_workspace?: boolean;
};

export type SessionsPage = {
  items: SessionItem[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
};

export type SessionDetailMessage = {
  role: string;
  content?: string;
  reasoning?: string;
  name?: string;
  call_id?: string;
  args?: unknown;
  result?: string;
  status?: string;
  agent_id?: string;
  /** Unix seconds when this turn/tool call was recorded (older history may lack it). */
  ts?: number;
  sidekick?: { kind?: string; job_id?: string; status?: string; exit_code?: number | null };
};

export type SessionDetail = {
  id: string;
  title: string;
  workspace?: SessionWorkspaceRef | null;
  messages: SessionDetailMessage[];
  tokens: number;
  limit?: number;
  demo: boolean;
  busy?: boolean;
  agent_tree?: unknown[];
  pending_approvals?: Array<{
    id: string;
    tool: string;
    args?: unknown;
    summary?: string;
  }>;
  pending_asks?: Array<{
    id: string;
    question: string;
    options?: Array<{ key: string; label: string }>;
    allow_custom?: boolean;
    custom_label?: string;
  }>;
  pending_plans?: Array<{
    id: string;
    summary?: string;
    tasks?: Array<{ id?: string; title: string; detail?: string; status?: string }>;
  }>;
  /** Plan currently being executed server-side (null when idle). */
  active_plan?: {
    plan_id: string;
    summary?: string;
    shape_contract?: Record<string, unknown>;
    tasks?: Array<{ id?: string; title: string; detail?: string; status?: string }>;
  } | null;
};

export const HISTORY_PAGE_SIZE = 20;

export const fetchSessions = (
  page = 1,
  pageSize = HISTORY_PAGE_SIZE,
  scope?: { workspaceId?: string | null; workspace?: string | null },
) => {
  let url = `/api/sessions?page=${encodeURIComponent(String(page))}&page_size=${encodeURIComponent(String(pageSize))}`;
  // Query by stable id when we have one — never fall back to comparing raw
  // path strings across two different requests (case/slash drift on Windows).
  if (scope?.workspaceId) {
    url += `&workspace_id=${encodeURIComponent(scope.workspaceId)}`;
  } else if (scope?.workspace) {
    url += `&workspace=${encodeURIComponent(scope.workspace)}`;
  }
  return json<SessionsPage>(url);
};
export const fetchSession = (id: string) => json<SessionDetail>(`/api/sessions/${id}`);
export const stopSession = (id: string) =>
  json<{ status: string }>(`/api/sessions/${id}/stop`, { method: "POST" });
export const truncateSession = (
  id: string,
  keepUserTurns: number,
  opts?: { restoreFiles?: boolean },
) =>
  json<{
    status: string;
    session_id: string;
    keep_user_turns: number;
    messages: number;
    restore_files?: boolean;
    file_undo?: { undone_count?: number; partial?: boolean; errors?: string[] };
  }>(`/api/sessions/${id}/truncate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      keep_user_turns: keepUserTurns,
      restore_files: Boolean(opts?.restoreFiles),
    }),
  });

export const fetchSkills = () => json<SkillItem[]>("/api/skills");
export const fetchSkill = (name: string) =>
  json<SkillDetail>(`/api/skills/${encodeURIComponent(name)}`);

export const fetchSkillFile = (name: string, path: string) =>
  json<SkillFileContent>(
    `/api/skills/${encodeURIComponent(name)}/files/${path.split("/").map(encodeURIComponent).join("/")}`,
  );
export const saveSkillFile = (name: string, path: string, content: string) =>
  json<{ status: string } & SkillFileContent>(
    `/api/skills/${encodeURIComponent(name)}/files/${path.split("/").map(encodeURIComponent).join("/")}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    },
  );
export const createSkillFile = (
  name: string,
  payload: { path: string; type: "file" | "folder"; content?: string },
) =>
  json<{ status: string; path: string; type: string; files?: string[] }>(
    `/api/skills/${encodeURIComponent(name)}/files`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
export const deleteSkillFile = (name: string, path: string) =>
  json<{ status: string; path: string; files?: string[] }>(
    `/api/skills/${encodeURIComponent(name)}/files/${path.split("/").map(encodeURIComponent).join("/")}`,
    { method: "DELETE" },
  );
export const validateSkill = (payload: {
  name?: string;
  description?: string;
  content?: string;
  markdown?: string;
}) =>
  json<SkillValidateResult>("/api/skills/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
export const saveSkill = (payload: {
  name: string;
  description: string;
  content: string;
  overwrite?: boolean;
  previous?: string;
}) =>
  json<{ status: string; skill: SkillItem }>("/api/skills", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ overwrite: true, ...payload }),
  });
export const updateSkill = (
  name: string,
  payload: { name: string; description: string; content: string },
) =>
  json<{ status: string; skill: SkillDetail }>(`/api/skills/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ overwrite: true, ...payload }),
  });
export const deleteSkill = (name: string) =>
  json<{ status: string; name: string }>(`/api/skills/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
export const importSkillDir = (
  path: string,
  overwrite = false,
) =>
  json<{ status: string; imported: SkillItem[] }>("/api/skills/import-dir", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, overwrite }),
  });
export const importSkillFiles = async (files: File[], overwrite = false) => {
  const fd = new FormData();
  for (const file of files) {
    const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
    fd.append("files", file, rel);
  }
  const headers = await authHeaders();
  const q = overwrite ? "?overwrite=true" : "";
  const r = await apiFetch(`/api/skills/import-files${q}`, { method: "POST", body: fd, headers });
  if (!r.ok) {
    let detail = `import failed: ${r.status}`;
    try {
      const body = (await r.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return r.json() as Promise<{ status: string; imported: SkillItem[] }>;
};
export const importSkillFile = async (file: File, overwrite = false) => {
  const fd = new FormData();
  fd.append("file", file);
  const headers = await authHeaders();
  const q = overwrite ? "?overwrite=true" : "";
  const r = await apiFetch(`/api/skills/import${q}`, { method: "POST", body: fd, headers });
  if (!r.ok) {
    let detail = `import failed: ${r.status}`;
    try {
      const body = (await r.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return r.json() as Promise<{ status: string; imported: SkillItem[] }>;
};

export const fetchBookToSkillJobs = () =>
  json<{ jobs: BookToSkillJob[]; active: number }>("/api/book-to-skill");
export const fetchBookToSkillJob = (jobId: string) =>
  json<BookToSkillJob>(`/api/book-to-skill/${encodeURIComponent(jobId)}`);
export const cancelBookToSkillJob = (jobId: string) =>
  json<BookToSkillJob>(`/api/book-to-skill/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
  });
export const createBookToSkillJob = async (
  file: File,
  opts?: { name?: string; overwrite?: boolean },
) => {
  const fd = new FormData();
  fd.append("file", file);
  if (opts?.name) fd.append("name", opts.name);
  fd.append("overwrite", opts?.overwrite ? "true" : "false");
  const headers = await authHeaders();
  const r = await apiFetch(`/api/book-to-skill`, { method: "POST", body: fd, headers });
  if (!r.ok) {
    let detail = `book-to-skill failed: ${r.status}`;
    try {
      const body = (await r.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return r.json() as Promise<{ status: string; job: BookToSkillJob }>;
};

export const fetchMemory = async () =>
  (await json<{ content: string }>("/api/memory")).content || "";
export const saveMemory = (content: string) =>
  json("/api/memory", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
export type MemoryItem = {
  id: string;
  title: string;
  content: string;
  tags: string[];
  enabled: boolean;
  updated_at?: number;
};
export type MemoryCategory = {
  id: string;
  name: string;
  memories: MemoryItem[];
};
export type MemoryLibrary = {
  version: number;
  categories: MemoryCategory[];
};

export const fetchMemoryLibrary = () => json<MemoryLibrary>("/api/memory/library");
export const saveMemoryLibrary = (library: MemoryLibrary) =>
  json<{ status: string; library: MemoryLibrary }>("/api/memory/library", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(library),
  });
export const fetchModel = async () =>
  normalizeModelSetup(await json<unknown>("/api/model"));

export const saveModel = async (patch: Partial<ModelSetup>) => {
  const res = await json<{ status: string; config: unknown; note: string }>(
    "/api/model",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    },
  );
  return { ...res, config: normalizeModelSetup(res.config) };
};

export const selectModel = async (
  role: "main" | "subagent" | "compress",
  providerId: string,
  model: string,
) => {
  const res = await json<{ status: string; config: unknown; note: string }>(
    "/api/model/select",
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role, provider_id: providerId, model }),
    },
  );
  return { ...res, config: normalizeModelSetup(res.config) };
};

export const fetchWorkspaces = () =>
  json<{
    configured: boolean;
    items: WorkspaceItem[];
    active: (SessionWorkspaceRef & { configured?: boolean }) | null;
  }>("/api/workspaces");
export const createWorkspace = (path: string) =>
  json<{
    status: string;
    configured: boolean;
    active: SessionWorkspaceRef;
    items: WorkspaceItem[];
  }>("/api/workspaces", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
export const rememberWorkspace = (path: string) =>
  json<{ status: string; items: WorkspaceItem[]; active: SessionWorkspaceRef | null }>(
    "/api/workspaces/remember",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    },
  );
export const forgetWorkspace = (path: string) =>
  json<{
    status: string;
    configured: boolean;
    active: (SessionWorkspaceRef & { configured?: boolean }) | null;
    items: WorkspaceItem[];
  }>(`/api/workspaces?path=${encodeURIComponent(path)}`, { method: "DELETE" });
export const setWorkspace = (path: string, create = false) =>
  json<{
    status: string;
    configured: boolean;
    active: SessionWorkspaceRef;
    items: WorkspaceItem[];
  }>("/api/workspaces/active", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, create }),
  });

export async function browseWorkspace(): Promise<{ cancelled: boolean; path: string | null }> {
  // Inside the Electron desktop app, use the native picker parented to the
  // main window (IPC) — the backend's detached PowerShell dialog has no
  // window owner there and can end up appearing behind the app.
  if (typeof window !== "undefined" && window.sidekickDesktop?.isDesktop) {
    const pick = window.sidekickDesktop.workspace?.pickFolder;
    if (pick) return pick({ title: "选择工作区文件夹" });
  }
  return json<{ cancelled: boolean; path: string | null }>("/api/workspaces/browse", {
    method: "POST",
  });
}

export const deleteSession = (id: string) =>
  json<{ status: string }>(`/api/sessions/${id}`, { method: "DELETE" });

export const saveSession = (id: string) =>
  json<{ status: string; path?: string }>(`/api/sessions/${id}/save`, {
    method: "POST",
  });
