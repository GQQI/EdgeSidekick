import type { ModelSetup } from "../types/modelSetup";
import { normalizeModelSetup } from "../types/modelSetup";

export type Health = {
  ok: boolean;
  /** Public internet reachability (hostinfo probes). */
  online?: boolean;
  demo: boolean;
  model: string;
  subagent_model?: string;
  base_url?: string;
  provider?: string;
  main_provider_id?: string;
  subagent_provider_id?: string;
  workspace: string;
  workspace_configured?: boolean;
  thinking_enabled?: boolean;
  reasoning_effort?: string;
  context_limit?: number;
  compress_trigger_ratio?: number;
  auto_approve_tools?: boolean;
  mcp_in_chat?: boolean;
  plugins_in_chat?: boolean;
};

export type AppPrefs = {
  auto_approve_tools: boolean;
  mcp_in_chat?: boolean;
  plugins_in_chat?: boolean;
  systemone_url?: string;
  systemone_model?: string;
  systemone_key?: string;
};

export type SkillItem = {
  name: string;
  tool: string;
  description: string;
  path: string;
  mode: string;
  folder?: string;
  writable?: boolean;
};

export type SkillDetail = SkillItem & {
  body: string;
  root?: string;
  files?: string[];
};

export type SkillFileContent = {
  path: string;
  content?: string;
  encoding?: string;
  binary?: boolean;
  size?: number;
};

export type SkillValidateResult = {
  ok: boolean;
  errors: string[];
  warnings: string[];
  name: string;
  description: string;
  body: string;
};


export type BookToSkillJob = {
  job_id: string;
  status: string;
  progress: number;
  message?: string;
  source_filename?: string;
  skill_name?: string | null;
  skill_path?: string | null;
  error?: string | null;
  overwrite?: boolean;
  created_at?: number;
  updated_at?: number;
  cancel_requested?: boolean;
};


/** @deprecated use ModelSetup */
export type ModelConfig = ModelSetup;

export type WorkspaceItem = {
  id: string;
  name: string;
  path: string;
  is_default?: boolean;
};

export type RuntimeEvent = {
  type: string;
  data: Record<string, unknown>;
  ts?: number;
  agent_id?: string;
  parent_id?: string;
};

const DEFAULT_BACKEND = "http://127.0.0.1:8787";

/** Same-origin when the page is http(s) (Vite proxy or FastAPI). file:/chrome-error fall back. */
export function getApiBase(): string {
  if (typeof window === "undefined") return "";
  try {
    const u = new URL(window.location.href);
    if (u.protocol === "http:" || u.protocol === "https:") return "";
  } catch {
    /* ignore */
  }
  return DEFAULT_BACKEND;
}

export function isTransientNetworkError(e: unknown): boolean {
  if (e instanceof DOMException && e.name === "AbortError") return false;
  const msg = e instanceof Error ? e.message : String(e ?? "");
  return /failed to fetch|network ?error|load failed|econnreset|socket hang up|err_connection|econnrefused|networkerror/i.test(
    msg,
  );
}

export function formatNetworkError(e: unknown, path?: string): Error {
  if (!isTransientNetworkError(e)) {
    return e instanceof Error ? e : new Error(String(e ?? "request failed"));
  }
  const origin =
    getApiBase() ||
    (typeof window !== "undefined" ? window.location.origin : DEFAULT_BACKEND);
  const raw = e instanceof Error ? e.message : String(e ?? "Failed to fetch");
  const where = path ? `${origin}${path}` : origin;
  return new Error(
    `无法连接本地后端 ${where}（${raw}）。服务可能仍在启动或已退出，请稍后重试。`,
  );
}

function waitMs(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, ms);
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    if (signal?.aborted) {
      onAbort();
      return;
    }
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

async function apiFetch(path: string, init?: RequestInit, retries = 2): Promise<Response> {
  const url = `${getApiBase()}${path}`;
  let lastErr: unknown = null;
  const max = Math.max(0, retries);
  for (let attempt = 0; attempt <= max; attempt += 1) {
    try {
      return await fetch(url, init);
    } catch (e) {
      lastErr = e;
      if (
        (e instanceof DOMException && e.name === "AbortError") ||
        init?.signal?.aborted ||
        !isTransientNetworkError(e) ||
        attempt === max
      ) {
        break;
      }
      try {
        await waitMs(300 * (attempt + 1), init?.signal || undefined);
      } catch {
        break;
      }
    }
  }
  throw formatNetworkError(lastErr, path);
}

const TOKEN_KEY = "sidekick_local_token";
let _token: string | null =
  typeof localStorage !== "undefined" ? localStorage.getItem(TOKEN_KEY) : null;
let _bootstrapPromise: Promise<string | null> | null = null;

export type AccountProfile = {
  id: string;
  username: string;
  email?: string;
  display_name?: string;
  title?: string;
  bio?: string;
  avatar?: string;
  created_at?: number;
};

export type AuthStatus = {
  needs_setup: boolean;
  multi_user: boolean;
  user_count: number;
  auth_required?: boolean;
  authenticated?: boolean;
  user?: AccountProfile | null;
  token?: string | null;
  token_header?: string;
};

export async function updateAccountProfile(patch: {
  display_name?: string;
  email?: string;
  title?: string;
  bio?: string;
  avatar?: string;
}): Promise<AccountProfile> {
  const headers = await authHeaders({ "Content-Type": "application/json" });
  const r = await apiFetch(`/api/auth/profile`, {
    method: "PATCH",
    headers,
    body: JSON.stringify(patch),
  });
  if (!r.ok) {
    let detail = `profile update failed: ${r.status}`;
    try {
      const body = (await r.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    noteUnauthorized(r.status, detail);
    throw new Error(detail);
  }
  const body = (await r.json()) as { user: AccountProfile };
  return body.user;
}

export function getStoredToken(): string | null {
  return _token;
}

export function setApiToken(token: string | null) {
  _token = token;
  if (typeof localStorage === "undefined") return;
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

const authLostHandlers = new Set<() => void>();

/** Fired when a saved login is rejected. The app should show the login screen. */
export function onAuthLost(handler: () => void): () => void {
  authLostHandlers.add(handler);
  return () => {
    authLostHandlers.delete(handler);
  };
}

function noteUnauthorized(status: number, detail: string) {
  if (status !== 401 || !_token) return;
  if (!/missing or invalid local token|not authenticated/i.test(detail)) return;
  setApiToken(null);
  for (const fn of authLostHandlers) {
    try {
      fn();
    } catch {
      /* ignore */
    }
  }
}

export async function fetchAuthStatus(): Promise<AuthStatus> {
  const headers = new Headers();
  if (_token) headers.set("X-Sidekick-Token", _token);
  const r = await apiFetch(`/api/auth/status`, { headers });
  if (!r.ok) throw new Error(`/api/auth/status failed: ${r.status}`);
  return r.json() as Promise<AuthStatus>;
}

export async function authSetup(payload: {
  username: string;
  email: string;
  password: string;
}) {
  const r = await apiFetch(`/api/auth/setup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    let detail = `setup failed: ${r.status}`;
    try {
      const body = (await r.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  const body = (await r.json()) as {
    token?: string;
    user?: { id: string; username: string; email?: string };
  };
  if (body.token) setApiToken(body.token);
  return body;
}

export async function authLogin(payload: { email: string; password: string }) {
  const r = await apiFetch(`/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    let detail = `login failed: ${r.status}`;
    try {
      const body = (await r.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  const body = (await r.json()) as {
    token?: string;
    user?: { id: string; username: string; email?: string };
  };
  if (body.token) setApiToken(body.token);
  return body;
}

export async function authRegister(payload: {
  username: string;
  email: string;
  password: string;
}) {
  const r = await apiFetch(`/api/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    let detail = `register failed: ${r.status}`;
    try {
      const body = (await r.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  const body = (await r.json()) as {
    token?: string;
    user?: { id: string; username: string; email?: string };
  };
  if (body.token) setApiToken(body.token);
  return body;
}

export async function authLogout() {
  try {
    const headers = new Headers();
    if (_token) headers.set("X-Sidekick-Token", _token);
    await apiFetch(`/api/auth/logout`, { method: "POST", headers });
  } catch {
    /* ignore */
  }
  setApiToken(null);
}

export async function ensureApiToken(): Promise<string | null> {
  if (_token) return _token;
  if (_bootstrapPromise) return _bootstrapPromise;
  _bootstrapPromise = (async () => {
    try {
      const r = await apiFetch(`/api/bootstrap`);
      if (!r.ok) return null;
      const body = (await r.json()) as AuthStatus;
      if (body.auth_required) {
        // Multi-user: must login — do not invent a token
        return null;
      }
      if (body.token) {
        setApiToken(body.token);
        return body.token;
      }
    } catch {
      /* ignore */
    }
    return null;
  })();
  try {
    return await _bootstrapPromise;
  } finally {
    _bootstrapPromise = null;
  }
}

async function authHeaders(extra?: HeadersInit): Promise<Headers> {
  const headers = new Headers(extra || {});
  const token = await ensureApiToken();
  if (token) headers.set("X-Sidekick-Token", token);
  return headers;
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const headers = await authHeaders(init?.headers);
  const r = await apiFetch(url, { ...init, headers });
  if (!r.ok) {
    let detail = `${url} failed: ${r.status}`;
    try {
      const body = (await r.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* ignore */
    }
    noteUnauthorized(r.status, detail);
    throw new Error(detail);
  }
  return r.json() as Promise<T>;
}

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

export type FsEntry = {
  name: string;
  path: string;
  /** Absolute path on the host machine (when provided by the API). */
  abs_path?: string;
  type: "file" | "dir";
  size?: number | null;
  kind?: string;
};

export type FsList = {
  path: string;
  name: string;
  type: string;
  entries: FsEntry[];
};

export type FileKind =
  | "text"
  | "image"
  | "pdf"
  | "audio"
  | "video"
  | "document"
  | "unsupported";

export type FilePayload = {
  path: string;
  name?: string;
  kind: FileKind | string;
  mime?: string;
  size: number;
  content?: string;
  preview?: string;
  truncated?: boolean;
  editable?: boolean;
  supported?: boolean;
  message?: string;
  raw_url?: string;
  /** Which open workspace this came from — carried into the detail/edit panel. */
  workspace?: string;
};

export const listFiles = (path = ".", workspace?: string) =>
  json<FsList>(
    `/api/files?path=${encodeURIComponent(path)}${
      workspace ? `&workspace=${encodeURIComponent(workspace)}` : ""
    }`,
  );
export type SearchHit = {
  path: string;
  name: string;
  kind: "file" | "dir" | string;
  match: "name" | "content" | string;
  line: number;
  snippet: string;
  /** All matching line numbers (content hits). */
  lines?: number[];
  /** Total matching lines found (may equal lines.length when capped). */
  matchCount?: number;
  /** Per-line snippets for expanded view. */
  snippets?: { line: number; text: string }[];
};
export const searchFiles = (q: string, path = ".", workspace?: string) =>
  json<{ query: string; hits: SearchHit[] }>(
    `/api/files/search?q=${encodeURIComponent(q)}&path=${encodeURIComponent(path)}${
      workspace ? `&workspace=${encodeURIComponent(workspace)}` : ""
    }`,
  );

export type BrowserStatus = {
  host: string;
  available: boolean;
  message: string;
  session: { url: string; started_at: number; ready: boolean; host: string } | null;
};

export const browserStatus = () => json<BrowserStatus>("/api/browser/status");
export const browserStart = (url = "", headless = false) =>
  json<{ host: string; url: string; started_at: number; ready: boolean }>(
    "/api/browser/session",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, headless }),
    },
  );
export const browserClose = () =>
  json<{ status: string }>("/api/browser/session", { method: "DELETE" });
export const browserAct = (body: {
  lane?: string;
  op: string;
  x?: number;
  y?: number;
  text?: string;
  key?: string;
}) =>
  json<{ ok?: boolean; url?: string; error?: string }>("/api/browser/act", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lane: "social", ...body }),
  });
export const browserNavigate = (url: string) =>
  json<{ host: string; url: string; started_at: number; ready: boolean }>(
    "/api/browser/navigate",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    },
  );
export const browserPreviewLocal = (url: string) =>
  json<{ url: string }>("/api/browser/preview-local", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
export const browserSelect = (timeout_ms = 60000, with_screenshot = true) =>
  json<{ ok: boolean; element: Record<string, unknown> | null; message?: string }>(
    "/api/browser/select",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ timeout_ms, with_screenshot }),
    },
  );
export const browserSelectCancel = () =>
  json<{ status: string }>("/api/browser/select/cancel", { method: "POST" });
export const browserScreenshotUrl = (bust = Date.now()) => {
  const q = new URLSearchParams({ t: String(bust) });
  return `/api/browser/screenshot?${q.toString()}`;
};

/** Fetch an authenticated API resource as a blob object URL (caller must revoke). */
export async function fetchAuthedBlob(url: string): Promise<string> {
  const headers = await authHeaders();
  let path = url;
  try {
    const u = new URL(url, "http://local.invalid");
    u.searchParams.delete("token");
    path = `${u.pathname}${u.search}`;
  } catch {
    /* keep original */
  }
  const r = await apiFetch(path, { headers });
  if (!r.ok) {
    throw new Error(`fetch failed: ${r.status}`);
  }
  const blob = await r.blob();
  return URL.createObjectURL(blob);
}

export async function downloadAuthedFile(url: string, filename: string): Promise<void> {
  const objectUrl = await fetchAuthedBlob(url);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename.split(/[\\/]/).pop() || "download";
  a.rel = "noreferrer";
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 30_000);
}

/** Fetch screenshot as a blob URL (revoker must call URL.revokeObjectURL). */
export async function browserFetchScreenshot(fullPage = false, lane = ""): Promise<string> {
  const headers = await authHeaders();
  const params = new URLSearchParams();
  if (fullPage) params.set("full_page", "true");
  if (lane) params.set("lane", lane);
  const qs = params.toString();
  const q = qs ? `?${qs}` : "";
  const r = await apiFetch(`/api/browser/screenshot${q}`, { headers });
  if (!r.ok) {
    let detail = `screenshot failed: ${r.status}`;
    try {
      const body = (await r.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      try {
        detail = (await r.text()) || detail;
      } catch {
        /* ignore */
      }
    }
    throw new Error(detail);
  }
  const blob = await r.blob();
  return URL.createObjectURL(blob);
}
export const uploadFile = async (
  file: File,
  opts?: { dest?: string; workspace?: string },
) => {
  const fd = new FormData();
  fd.append("file", file);
  if (opts?.dest) fd.append("dest", opts.dest);
  if (opts?.workspace) fd.append("workspace", opts.workspace);
  const headers = await authHeaders();
  const qs = opts?.workspace
    ? `?workspace=${encodeURIComponent(opts.workspace)}`
    : "";
  const r = await apiFetch(`/api/files/upload${qs}`, { method: "POST", body: fd, headers });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(text || `upload failed: ${r.status}`);
  }
  return r.json() as Promise<FilePayload & { uploaded?: boolean }>;
};
export const readFileContent = (path: string, workspace?: string) =>
  json<FilePayload>(
    `/api/files/content?path=${encodeURIComponent(path)}${
      workspace ? `&workspace=${encodeURIComponent(workspace)}` : ""
    }`,
  );
export const getApiToken = () => _token;

/** Strip query tokens — media must use fetchAuthedBlob + Authorization header. */
export function withAuthToken(url: string | undefined | null): string | undefined {
  if (!url) return undefined;
  try {
    const u = new URL(url, "http://local.invalid");
    u.searchParams.delete("token");
    if (url.startsWith("/") || url.startsWith("/api/")) {
      return `${u.pathname}${u.search}`;
    }
    return url;
  } catch {
    return url;
  }
}

export const fileRawUrl = (path: string, workspace?: string) => {
  const q = new URLSearchParams({ path });
  if (workspace) q.set("workspace", workspace);
  return `/api/files/raw?${q.toString()}`;
};
export const writeFileContent = (path: string, content: string, workspace?: string) =>
  json<{ path: string; size: number }>("/api/files/content", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, content, workspace: workspace || null }),
  });
export const createFsEntry = (path: string, kind: "file" | "dir" = "file", workspace?: string) =>
  json<{ path: string; type: string }>("/api/files", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, kind, workspace: workspace || null }),
  });
export const deleteFsEntry = (path: string, recursive = false, workspace?: string) =>
  json<{ path: string; type: string; deleted: boolean }>(
    `/api/files?path=${encodeURIComponent(path)}&recursive=${recursive ? "true" : "false"}${
      workspace ? `&workspace=${encodeURIComponent(workspace)}` : ""
    }`,
    { method: "DELETE" },
  );
export const renameFsEntry = (path: string, newName: string, workspace?: string) =>
  json<{ path: string; from: string; name: string; type: string }>("/api/files/rename", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, new_name: newName, workspace: workspace || null }),
  });
export const moveFsEntry = (path: string, destDir: string, workspace?: string) =>
  json<{ path: string; from: string; to_dir: string; type: string }>("/api/files/move", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, dest_dir: destDir, workspace: workspace || null }),
  });

export const revealFsEntry = (path: string, workspace?: string) =>
  json<{ status: string; path: string; abs_path: string; type: string }>(
    "/api/files/reveal",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, workspace: workspace || null }),
    },
  );

export const decideApproval = (
  sessionId: string,
  approvalId: string,
  approved: boolean,
  remember = false,
  patchArgs?: Record<string, unknown>,
) =>
  json<{ status: string; approval_id: string; approved: boolean; remember?: boolean }>(
    `/api/sessions/${sessionId}/approvals/${approvalId}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        approved,
        remember,
        ...(patchArgs ? { patch_args: patchArgs } : {}),
      }),
    },
  );

export const answerAsk = (
  sessionId: string,
  askId: string,
  choice: string,
  text = "",
  optionLabel = "",
) =>
  json<{ status: string; ask_id: string; choice: string }>(
    `/api/sessions/${sessionId}/asks/${askId}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        choice,
        text,
        option_label: optionLabel,
      }),
    },
  );

export const confirmPlan = (
  sessionId: string,
  planId: string,
  body: {
    approved: boolean;
    summary?: string;
    tasks?: Array<{ id?: string; title: string; detail?: string; status?: string }>;
  },
) =>
  json<{ status: string; plan_id: string; approved: boolean }>(
    `/api/sessions/${sessionId}/plans/${planId}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );

export type ChatHandlers = {
  onEvent: (ev: RuntimeEvent) => void;
  onFinal: (text: string, meta: Record<string, unknown>) => void;
  onError: (msg: string) => void;
  onAbort?: () => void;
};

function parseSseBlock(block: string): RuntimeEvent | null {
  // Normalize CRLF so event:/data: parsing is reliable on Windows servers
  const lines = block.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  let eventName = "";
  const dataLines: string[] = [];
  for (const line of lines) {
    if (!line || line.startsWith(":")) continue; // comments / keep-alives
    if (line.startsWith("event:")) eventName = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length) return null;
  const raw = dataLines.join("\n");
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    // Support both full envelope {type,data} and bare payloads
    if (typeof parsed.type === "string") {
      return parsed as unknown as RuntimeEvent;
    }
    return {
      type: eventName || "message",
      data: parsed as Record<string, unknown>,
    };
  } catch {
    return {
      type: eventName || "message",
      data: { text: raw, message: raw },
    };
  }
}

const transientFetchError = isTransientNetworkError;

export async function streamChat(
  message: string,
  sessionId: string | null,
  handlers: ChatHandlers,
  signal?: AbortSignal,
  mode: "plan" | "agent" = "agent",
  display?: string,
  workspace?: string | null,
  disableTools?: boolean,
  /** Social one-shot call: never surface the forced session in history. */
  hidden?: boolean,
  /** Group harness already assigned this member; do not spawn another team. */
  blockDelegate?: boolean,
  /** One JSON classification: no tools, one model reply. */
  classify?: boolean,
  /** Per-conversation MCP server ids to bind (empty = none). */
  mcpServerIds?: string[],
  /** Per-conversation local plugin ids to bind (empty = none). */
  pluginIds?: string[],
): Promise<string | null> {
  let r: Response | null = null;
  let lastFetchError: unknown = null;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const headers = await authHeaders({
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      });
      const body: Record<string, unknown> = {
        message,
        session_id: sessionId,
        mode,
      };
      if (disableTools) body.disable_tools = true;
      if (display && display !== message) body.display = display;
      if (hidden) body.hidden = true;
      if (blockDelegate) body.block_delegate = true;
      if (classify) body.classify = true;
      if (mcpServerIds !== undefined) body.mcp_server_ids = mcpServerIds;
      if (pluginIds !== undefined) body.plugin_ids = pluginIds;
      // Every turn carries the workspace currently bound to this chat. Social
      // members use short-lived, synthetic session ids, so limiting this to new
      // sessions would silently fall back to the server's default folder.
      if (workspace) body.workspace = workspace;
      r = await apiFetch(`/api/chat`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal,
      }, 0);
      lastFetchError = null;
      break;
    } catch (e) {
      if (signal?.aborted || (e instanceof DOMException && e.name === "AbortError")) {
        handlers.onAbort?.();
        return sessionId;
      }
      lastFetchError = e;
      if (!transientFetchError(e) || attempt === 2) break;
      try {
        await waitMs(400 * (attempt + 1), signal);
      } catch {
        handlers.onAbort?.();
        return sessionId;
      }
    }
  }
  if (!r) {
    throw formatNetworkError(lastFetchError || new Error("Failed to fetch"), "/api/chat");
  }
  if (!r.ok || !r.body) {
    let detail = `chat failed: ${r.status}`;
    try {
      const body = (await r.json()) as { detail?: unknown };
      if (typeof body.detail === "string" && body.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    noteUnauthorized(r.status, detail);
    handlers.onError(detail);
    return sessionId;
  }

  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let sid = sessionId;
  let sawFinal = false;
  let aborted = false;
  let sawTerminal = false;
  let sawError = false;

  const handle = (ev: RuntimeEvent) => {
    const type = ev.type || "message";
    if (type === "session") {
      sid = String(ev.data?.session_id || sid);
      handlers.onEvent({ ...ev, type });
      return;
    }
    if (type === "final") {
      sawFinal = true;
      sawTerminal = true;
      // Deliver final to UI before cancelling the reader — social/workbench
      // must commit text (and browser_open that arrived earlier) first.
      handlers.onFinal(String(ev.data?.text ?? ""), ev.data || {});
      sid = String(ev.data?.session_id || sid);
      return;
    }
    if (type === "error") {
      sawError = true;
      sawTerminal = true;
      handlers.onError(String(ev.data?.message || "error"));
      return;
    }
    handlers.onEvent({ ...ev, type });
  };

  const flushParts = () => {
    const parts = buf.split(/\r?\n\r?\n/);
    buf = parts.pop() || "";
    for (const block of parts) {
      const ev = parseSseBlock(block);
      if (ev) handle(ev);
    }
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // SSE events are separated by blank lines; servers often use CRLF (\r\n\r\n)
      flushParts();
      // Turn is logically done on final/error — do not wait for the server to
      // close the SSE (persist / browser teardown can lag and leave Stop stuck).
      // Critical: only cancel AFTER handle() ran for every complete event in
      // this chunk (including final), so social can commit text/browser first.
      if (sawTerminal) {
        // Rare framing: more complete events may still sit behind a trailing
        // separator in buf — flush once more before tearing down.
        if (buf.includes("\n\n") || buf.includes("\r\n\r\n")) flushParts();
        try {
          await reader.cancel();
        } catch {
          /* ignore */
        }
        break;
      }
    }
    if (!sawTerminal && buf.trim()) {
      const ev = parseSseBlock(buf);
      if (ev) handle(ev);
    }
  } catch (e) {
    if (signal?.aborted || (e instanceof DOMException && e.name === "AbortError")) {
      aborted = true;
      handlers.onAbort?.();
    } else if (transientFetchError(e) && sid && !sawTerminal) {
      // The turn can still be running after the browser drops the socket
      // (common while the in-app page is opening). Rejoin that session
      // instead of showing a hard send failure.
      try {
        await attachSessionEvents(sid, handlers, signal);
        return sid;
      } catch (reattachError) {
        if (signal?.aborted || (reattachError instanceof DOMException && reattachError.name === "AbortError")) {
          handlers.onAbort?.();
          return sid;
        }
        throw e;
      }
    } else {
      throw e;
    }
  }
  // Never synthesize an empty final after an SSE error — that overwrote social
  // error text with socialEmptyReply.
  if (!sawFinal && !aborted && !sawError) {
    handlers.onFinal("", { session_id: sid, partial: true });
  }
  return sid;
}

export async function attachSessionEvents(
  sessionId: string,
  handlers: ChatHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const headers = await authHeaders({ Accept: "text/event-stream" });
  let r: Response;
  try {
    r = await apiFetch(`/api/sessions/${encodeURIComponent(sessionId)}/events`, {
      method: "GET",
      headers,
      signal,
    });
  } catch (e) {
    if (signal?.aborted || (e instanceof DOMException && e.name === "AbortError")) {
      handlers.onAbort?.();
      return;
    }
    throw e;
  }
  if (!r.ok || !r.body) {
    handlers.onError(`attach failed: ${r.status}`);
    return;
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let sawFinal = false;
  let aborted = false;
  const handle = (ev: RuntimeEvent) => {
    const type = ev.type || "message";
    if (type === "final") {
      sawFinal = true;
      handlers.onFinal(String(ev.data?.text ?? ""), ev.data || {});
      return;
    }
    if (type === "error") {
      handlers.onError(String(ev.data?.message || "error"));
      return;
    }
    handlers.onEvent({ ...ev, type });
  };
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split(/\r?\n\r?\n/);
      buf = parts.pop() || "";
      for (const block of parts) {
        const ev = parseSseBlock(block);
        if (ev) handle(ev);
      }
    }
    if (buf.trim()) {
      const ev = parseSseBlock(buf);
      if (ev) handle(ev);
    }
  } catch (e) {
    if (signal?.aborted || (e instanceof DOMException && e.name === "AbortError")) {
      aborted = true;
      handlers.onAbort?.();
    } else {
      throw e;
    }
  }
  if (!sawFinal && !aborted) {
    handlers.onFinal("", { session_id: sessionId, partial: true });
  }
}

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
  /** Any process from the job still runs, even if the shell wrapper exited. */
  group_alive?: boolean;
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

export const fetchUndo = (
  sessionId?: string | null,
  opts?: { workspace?: string | null },
) => {
  const qs = new URLSearchParams();
  if (sessionId) qs.set("session_id", sessionId);
  if (opts?.workspace) qs.set("workspace", opts.workspace);
  const q = qs.toString();
  return json<{ count: number; items: UndoItem[] }>(
    q ? `/api/files/undo?${q}` : "/api/files/undo",
  );
};
export const postUndo = (
  id?: string,
  sessionId?: string | null,
  opts?: { path?: string; userTurn?: number; workspace?: string | null },
) =>
  json<{ status: string; undone_count?: number; remaining?: number; errors?: string[] }>(
    "/api/files/undo",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...(id ? { id } : {}),
        ...(sessionId ? { session_id: sessionId } : {}),
        ...(opts?.workspace ? { workspace: opts.workspace } : {}),
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
  source?: string;
  /** Populated by GET /api/mcp from runtime health. */
  ok?: boolean;
  error?: string | null;
  health?: string;
};

export type McpSetup = {
  version: number;
  servers: McpServer[];
  mcpServers?: Record<string, Record<string, unknown>>;
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


export type McpToolInfo = {
  server_id: string;
  server_name: string;
  name: string;
  qualified: string;
  description: string;
  input_schema?: Record<string, unknown>;
};

export const fetchMcpTools = () =>
  json<{ tools: McpToolInfo[]; count: number }>("/api/mcp/tools");


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

/* ---- Plugins (插件应用): marketplace, installed plugins, credentials, OAuth ---- */

export type PluginCredentialField = {
  name: string;
  label: string;
  type: "secret" | "text" | "select";
  required: boolean;
  default: string;
  placeholder: string;
  help: string;
  url: string;
  options: Array<{ value: string; label: string }>;
};

export type PluginToolSpec = {
  name: string;
  label: string;
  description: string;
  readonly: boolean;
};

export type PluginCredentialStatus = {
  configured: boolean;
  missing: string[];
  fields: Record<string, { set: boolean; value?: string }>;
};

export type PluginVersionRow = {
  version: string;
  active: boolean;
  author?: string;
  package_source?: string;
  source?: string;
  origin?: string;
  installed_at?: number;
  updated_at?: number;
};

export type InstalledPlugin = {
  id: string;
  name: string;
  version: string;
  description: string;
  author: string;
  /** Package-declared provenance (来源). */
  package_source?: string;
  category: string;
  tags: string[];
  homepage: string;
  network: string[];
  icon: string;
  credentials: PluginCredentialField[];
  tools: PluginToolSpec[];
  oauth: { scopes: string[]; label: string; token_field: string } | null;
  enabled: boolean;
  /** Install channel: upload / url / marketplace. */
  source: string;
  install_source?: string;
  origin: string;
  active_version?: string;
  versions?: PluginVersionRow[];
  version_count?: number;
  installed_at?: number;
  updated_at?: number;
  credential_status: PluginCredentialStatus;
  agent_tools: string[];
  readme?: string;
  verify?: PluginCallResult;
  offline?: boolean;
  tool_count?: number;
};

export type MarketItem = {
  id: string;
  name: string;
  version: string;
  description: string;
  author: string;
  category: string;
  tags: string[];
  homepage: string;
  icon: string;
  network?: string[];
  tools: string[];
  tool_specs?: PluginToolSpec[];
  tool_count?: number;
  offline?: boolean;
  readme: string;
  source: "bundled" | "remote";
  source_name: string;
  source_url?: string;
  installed: boolean;
  installed_version: string;
  update_available: boolean;
};

export type MarketCatalog = {
  items: MarketItem[];
  categories: string[];
  sources: string[];
  errors: Array<{ source: string; error: string }>;
};

export type PluginCallResult = { ok: boolean; result?: string; error?: string };

const pluginJsonBody = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const fetchPlugins = () => json<InstalledPlugin[]>("/api/plugins");
export const fetchPlugin = (id: string) => json<InstalledPlugin>(`/api/plugins/${encodeURIComponent(id)}`);
export const fetchMarketplace = (refresh = false) =>
  json<MarketCatalog>(`/api/plugins/marketplace${refresh ? "?refresh=true" : ""}`);
export const saveMarketplaceSources = (sources: string[]) =>
  json<MarketCatalog>("/api/plugins/marketplace/sources", pluginJsonBody("PUT", { sources }));
export const installPlugin = (target: { id?: string; url?: string }) =>
  json<InstalledPlugin>("/api/plugins/install", pluginJsonBody("POST", target));
export const setPluginEnabled = (id: string, enabled: boolean) =>
  json<InstalledPlugin>(`/api/plugins/${encodeURIComponent(id)}`, pluginJsonBody("PATCH", { enabled }));
export const uninstallPlugin = (id: string) =>
  json<{ status: string }>(`/api/plugins/${encodeURIComponent(id)}`, { method: "DELETE" });
export const setPluginActiveVersion = (id: string, version: string) =>
  json<InstalledPlugin>(
    `/api/plugins/${encodeURIComponent(id)}/version`,
    pluginJsonBody("PUT", { version }),
  );
export const uninstallPluginVersion = (id: string, version: string) =>
  json<{
    status: string;
    removed_plugin?: boolean;
    version?: string;
    active_version?: string;
    plugin?: InstalledPlugin;
  }>(`/api/plugins/${encodeURIComponent(id)}/versions/${encodeURIComponent(version)}`, {
    method: "DELETE",
  });
export const savePluginCredentials = (id: string, values: Record<string, string>, clear: string[] = [], verify = true) =>
  json<InstalledPlugin>(`/api/plugins/${encodeURIComponent(id)}/credentials`, pluginJsonBody("PUT", { values, clear, verify }));
export const verifyPlugin = (id: string) =>
  json<PluginCallResult>(`/api/plugins/${encodeURIComponent(id)}/verify`, { method: "POST" });
export const startPluginOAuth = (id: string) =>
  json<{ url: string; state: string; redirect_uri?: string }>(`/api/plugins/${encodeURIComponent(id)}/oauth/start`, { method: "POST" });


export async function fetchPluginGuide(): Promise<string> {
  const headers = await authHeaders();
  const res = await apiFetch("/api/plugins/guide", { headers });
  if (!res.ok) throw new Error(`加载开发说明失败：${res.status}`);
  return res.text();
}

export async function downloadPluginTemplate(): Promise<void> {
  await downloadAuthedFile("/api/plugins/template.zip", "hello-plugin.zip");
}

export async function uploadPlugin(file: File): Promise<InstalledPlugin> {
  const fd = new FormData();
  fd.append("file", file);
  const headers = await authHeaders();
  const r = await apiFetch("/api/plugins/upload", { method: "POST", body: fd, headers });
  if (!r.ok) {
    let detail = `上传失败：${r.status}`;
    try {
      const body = (await r.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return r.json() as Promise<InstalledPlugin>;
}
