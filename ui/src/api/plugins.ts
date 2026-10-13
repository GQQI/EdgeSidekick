import { apiFetch, authHeaders, json } from "./client";

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

export type InstalledPlugin = {
  id: string;
  name: string;
  version: string;
  description: string;
  author: string;
  category: string;
  tags: string[];
  homepage: string;
  network: string[];
  icon: string;
  credentials: PluginCredentialField[];
  tools: PluginToolSpec[];
  oauth: { scopes: string[]; label: string; token_field: string } | null;
  enabled: boolean;
  source: string;
  origin: string;
  installed_at?: number;
  updated_at?: number;
  credential_status: PluginCredentialStatus;
  agent_tools: string[];
  readme?: string;
  verify?: PluginCallResult;
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
  tools: string[];
  tool_specs?: PluginToolSpec[];
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

const jsonBody = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const fetchPlugins = () => json<InstalledPlugin[]>("/api/plugins");
export const fetchPlugin = (id: string) => json<InstalledPlugin>(`/api/plugins/${encodeURIComponent(id)}`);
export const fetchMarketplace = (refresh = false) =>
  json<MarketCatalog>(`/api/plugins/marketplace${refresh ? "?refresh=true" : ""}`);
export const saveMarketplaceSources = (sources: string[]) =>
  json<MarketCatalog>("/api/plugins/marketplace/sources", jsonBody("PUT", { sources }));
export const installPlugin = (target: { id?: string; url?: string }) =>
  json<InstalledPlugin>("/api/plugins/install", jsonBody("POST", target));
export const setPluginEnabled = (id: string, enabled: boolean) =>
  json<InstalledPlugin>(`/api/plugins/${encodeURIComponent(id)}`, jsonBody("PATCH", { enabled }));
export const uninstallPlugin = (id: string) =>
  json<{ status: string }>(`/api/plugins/${encodeURIComponent(id)}`, { method: "DELETE" });
export const savePluginCredentials = (id: string, values: Record<string, string>, clear: string[] = [], verify = true) =>
  json<InstalledPlugin>(`/api/plugins/${encodeURIComponent(id)}/credentials`, jsonBody("PUT", { values, clear, verify }));
export const verifyPlugin = (id: string) =>
  json<PluginCallResult>(`/api/plugins/${encodeURIComponent(id)}/verify`, { method: "POST" });
export const startPluginOAuth = (id: string) =>
  json<{ url: string; state: string }>(`/api/plugins/${encodeURIComponent(id)}/oauth/start`, { method: "POST" });

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
