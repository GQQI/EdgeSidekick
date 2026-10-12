import {
  json,
  apiFetch,
  authHeaders,
  ensureApiToken,
  getApiBase,
  getStoredToken,
  getApiToken,
} from "./client";


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

