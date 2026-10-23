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
};

export type AppPrefs = {
  auto_approve_tools: boolean;
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

export function waitMs(ms: number, signal?: AbortSignal): Promise<void> {
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

export async function apiFetch(path: string, init?: RequestInit, retries = 2): Promise<Response> {
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
  const r = await apiFetch(`/api/auth/profile`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
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
    throw new Error(detail);
  }
  const body = (await r.json()) as { user: AccountProfile };
  return body.user;
}

export function getStoredToken(): string | null {
  return _token;
}

export const getApiToken = () => _token;

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

export function noteUnauthorized(status: number, detail: string) {
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
    user?: AccountProfile;
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
    user?: AccountProfile;
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
    user?: AccountProfile;
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

export async function authHeaders(extra?: HeadersInit): Promise<Headers> {
  const headers = new Headers(extra || {});
  const token = await ensureApiToken();
  if (token) headers.set("X-Sidekick-Token", token);
  return headers;
}

export async function json<T>(url: string, init?: RequestInit): Promise<T> {
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

