export type ModelRef = {
  provider_id: string;
  model_id: string;
};

export type ModelEntry = {
  id: string;
  name: string;
  base_url: string;
  api_key?: string;
  api_key_masked?: string;
  api_key_set?: boolean;
  max_tokens?: number;
};

export type ModelProvider = {
  id: string;
  name: string;
  vendor: string;
  market_id?: string;
  models: ModelEntry[];
};

export type VendorTemplate = {
  name: string;
  vendor: string;
  base_url: string;
  models: string[];
};

export type ModelSetup = {
  version: number;
  providers: ModelProvider[];
  main: ModelRef;
  subagent: ModelRef;
  compress: ModelRef;
  reasoning_effort: string;
  thinking_enabled: boolean;
  temperature: number;
  demo_mode?: boolean;
  vendor_templates?: Record<string, VendorTemplate>;
};

export type ModelRole = "main" | "subagent";

/** Sentinel provider/model id for "Auto" — server picks the best keyed model per task. */
export const AUTO_ID = "auto";

export function isAutoRef(ref: ModelRef | null | undefined): boolean {
  if (!ref) return false;
  return ref.provider_id === AUTO_ID || ref.model_id === AUTO_ID;
}

export function autoRef(): ModelRef {
  return { provider_id: AUTO_ID, model_id: AUTO_ID };
}

export const DEFAULT_VENDOR_TEMPLATES: Record<string, VendorTemplate> = {
  custom: {
    name: "OpenAI-API-Compatible",
    vendor: "openai",
    base_url: "https://api.openai.com/v1",
    models: [],
  },
  openai: {
    name: "OpenAI",
    vendor: "openai",
    base_url: "https://api.openai.com/v1",
    models: ["gpt-4.1", "gpt-4o"],
  },
  anthropic: {
    name: "Anthropic",
    vendor: "anthropic",
    base_url: "https://api.anthropic.com/v1",
    models: ["claude-sonnet-4-5"],
  },
  ollama: {
    name: "Ollama",
    vendor: "ollama",
    base_url: "http://127.0.0.1:11434/v1",
    models: [],
  },
};

function emptyRef(): ModelRef {
  return { provider_id: "", model_id: "" };
}

function providersOf(setup: ModelSetup | null | undefined): ModelProvider[] {
  return Array.isArray(setup?.providers) ? setup!.providers : [];
}

/** Adapt legacy flat `/api/model` payload (provider/model/api_key) to multi-provider setup. */
function fromLegacyFlat(raw: Record<string, unknown>): ModelSetup {
  const rawProvider = String(raw.provider || "").trim().toLowerCase();
  const providerName =
    !rawProvider || ["deepseek", "openai", "minimax", "ollama"].includes(rawProvider)
      ? "OpenAI-API-Compatible"
      : String(raw.provider);
  const providerId = `prov_${providerName}`;
  const baseUrl = String(raw.base_url || "");
  const hasKey = Boolean(raw.api_key_set || raw.api_key);
  const masked = String(raw.api_key_masked || "");
  const names = [
    String(raw.model || "").trim(),
    String(raw.subagent_model || "").trim(),
    String(raw.compress_model || "").trim(),
    String(raw.review_model || "").trim(),
  ].filter(Boolean);
  const unique = [...new Set(names)];
  if (unique.length === 0) unique.push("model");

  const models: ModelEntry[] = unique.map((name, i) => ({
    id: `mdl_${i}_${name.replace(/[^\w.-]+/g, "_").slice(0, 40)}`,
    name,
    base_url: baseUrl,
    api_key: "",
    api_key_masked: masked,
    api_key_set: hasKey,
  }));

  const byName = (name: string | undefined) =>
    models.find((m) => m.name === name) || models[0];

  const mainM = byName(String(raw.model || ""));
  const subM = byName(String(raw.subagent_model || raw.model || ""));
  const cmpM = byName(String(raw.compress_model || raw.subagent_model || raw.model || ""));

  return {
    version: 3,
    providers: [
      {
        id: providerId,
        name: providerName,
        vendor: "openai",
        market_id: "custom",
        models,
      },
    ],
    main: { provider_id: providerId, model_id: mainM.id },
    subagent: { provider_id: providerId, model_id: subM.id },
    compress: { provider_id: providerId, model_id: cmpM.id },
    reasoning_effort: String(raw.reasoning_effort || "medium"),
    thinking_enabled: Boolean(raw.thinking_enabled ?? true),
    temperature: Number(raw.temperature ?? 0.2),
    demo_mode: Boolean(raw.demo_mode),
    vendor_templates: { ...DEFAULT_VENDOR_TEMPLATES },
  };
}

/** Ensure UI always receives a ModelSetup with iterable `providers`. */
export function normalizeModelSetup(raw: unknown): ModelSetup {
  const data =
    raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};

  if (!Array.isArray(data.providers)) {
    if (data.model || data.provider || data.base_url || data.api_key_set != null) {
      return fromLegacyFlat(data);
    }
    return {
      version: 3,
      providers: [],
      main: emptyRef(),
      subagent: emptyRef(),
      compress: emptyRef(),
      reasoning_effort: String(data.reasoning_effort || "medium"),
      thinking_enabled: Boolean(data.thinking_enabled ?? true),
      temperature: Number(data.temperature ?? 0.2),
      demo_mode: Boolean(data.demo_mode ?? true),
      vendor_templates: {
        ...DEFAULT_VENDOR_TEMPLATES,
        ...((data.vendor_templates as Record<string, VendorTemplate>) || {}),
      },
    };
  }

  const providers = (data.providers as ModelProvider[]).map((p) => ({
    ...p,
    models: Array.isArray(p.models) ? p.models : [],
  }));

  const asRef = (v: unknown): ModelRef => {
    if (v && typeof v === "object") {
      const r = v as Record<string, unknown>;
      return {
        provider_id: String(r.provider_id || ""),
        model_id: String(r.model_id || ""),
      };
    }
    return emptyRef();
  };

  return {
    version: Number(data.version || 3),
    providers,
    main: asRef(data.main),
    subagent: asRef(data.subagent),
    compress: asRef(data.compress),
    reasoning_effort: String(data.reasoning_effort || "medium"),
    thinking_enabled: Boolean(data.thinking_enabled ?? true),
    temperature: Number(data.temperature ?? 0.2),
    demo_mode: Boolean(data.demo_mode),
    vendor_templates: {
      ...DEFAULT_VENDOR_TEMPLATES,
      ...((data.vendor_templates as Record<string, VendorTemplate>) || {}),
    },
  };
}

export function refKey(ref: ModelRef): string {
  return `${ref.provider_id}::${ref.model_id}`;
}

export function parseRefKey(key: string): ModelRef | null {
  const i = key.indexOf("::");
  if (i <= 0) return null;
  return { provider_id: key.slice(0, i), model_id: key.slice(i + 2) };
}

export function findModel(
  setup: ModelSetup | null,
  ref: ModelRef | undefined,
): ModelEntry | null {
  if (!setup || !ref?.model_id) return null;
  for (const p of providersOf(setup)) {
    if (p.id !== ref.provider_id) continue;
    const hit = (p.models || []).find((m) => m.id === ref.model_id);
    if (hit) return hit;
  }
  return null;
}

export function modelLabel(
  setup: ModelSetup | null,
  ref: ModelRef | undefined,
): string {
  if (!setup || !ref) return "";
  const prov = providersOf(setup).find((p) => p.id === ref.provider_id);
  const entry = findModel(setup, ref);
  if (!prov || !entry) return entry?.name || "";
  return `${prov.name} / ${entry.name}`;
}

export function allModelOptions(
  setup: ModelSetup | null | undefined,
  opts?: { requireKey?: boolean },
): Array<{
  key: string;
  provider_id: string;
  provider_name: string;
  vendor: string;
  model_id: string;
  model: string;
  has_key: boolean;
  disabled: boolean;
}> {
  const requireKey = opts?.requireKey ?? false;
  const out: Array<{
    key: string;
    provider_id: string;
    provider_name: string;
    vendor: string;
    model_id: string;
    model: string;
    has_key: boolean;
    disabled: boolean;
  }> = [];
  for (const p of providersOf(setup ?? null)) {
    for (const m of p.models || []) {
      const hasKey = Boolean(m.api_key_set || m.api_key);
      out.push({
        key: refKey({ provider_id: p.id, model_id: m.id }),
        provider_id: p.id,
        provider_name: p.name,
        vendor: p.vendor,
        model_id: m.id,
        model: m.name,
        has_key: hasKey,
        disabled: requireKey && !hasKey,
      });
    }
  }
  return out;
}

export function newModelEntry(
  name: string,
  opts?: { base_url?: string; api_key?: string },
): ModelEntry {
  return {
    id: `mdl_${Math.random().toString(36).slice(2, 10)}`,
    name,
    base_url: opts?.base_url || "",
    api_key: opts?.api_key || "",
  };
}
