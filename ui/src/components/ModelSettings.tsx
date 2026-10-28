import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type {
  ModelSetup,
  ModelProvider,
  ModelEntry,
  VendorTemplate,
  ModelRef,
} from "../types/modelSetup";
import {
  newModelEntry,
  refKey,
  autoRef,
  isAutoRef,
  DEFAULT_VENDOR_TEMPLATES,
} from "../types/modelSetup";
import type { MsgKey } from "../i18n";
import {
  IconCheck,
  IconChevronDown,
  IconEye,
  IconEyeOff,
  IconPencil,
  IconPlus,
  IconSearch,
  IconSettings,
  IconTrash,
  IconX,
} from "./icons";

type Props = {
  setup: ModelSetup;
  locale: "zh" | "en";
  onChange: (next: ModelSetup) => void;
  onSave: (next?: ModelSetup, opts?: { restartChat?: boolean }) => void;
  saving?: boolean;
  t: (key: MsgKey, ...args: string[]) => string;
  extra?: ReactNode;
};

type MarketKind = "custom" | "openai" | "anthropic" | "ollama";
type NetFilter = "all" | "offline" | "online";

type MarketItem = {
  id: MarketKind;
  nameZh: string;
  nameEn: string;
  descZh: string;
  descEn: string;
  tags: string[];
  tagKind?: "official" | "local" | "compat";
  net: "offline" | "online";
};

const MARKET: MarketItem[] = [
  {
    id: "custom",
    nameZh: "OpenAI-API-Compatible",
    nameEn: "OpenAI-API-Compatible",
    descZh: "支持自定义 Base URL，兼容任意 OpenAI 协议网关",
    descEn: "Custom Base URL — any OpenAI-compatible gateway",
    tags: ["LLM", "OpenAI", "Local"],
    tagKind: "compat",
    net: "online",
  },
  {
    id: "openai",
    nameZh: "OpenAI",
    nameEn: "OpenAI",
    descZh: "接入 OpenAI API 模型",
    descEn: "Connect to OpenAI API models",
    tags: ["LLM"],
    tagKind: "official",
    net: "online",
  },
  {
    id: "anthropic",
    nameZh: "Anthropic",
    nameEn: "Anthropic",
    descZh: "接入 Claude 系列模型",
    descEn: "Connect to Claude models",
    tags: ["LLM"],
    net: "online",
  },
  {
    id: "ollama",
    nameZh: "Ollama",
    nameEn: "Ollama",
    descZh: "连接本地运行的开源模型",
    descEn: "Connect local open-source models",
    tags: ["LLM"],
    tagKind: "local",
    net: "offline",
  },
];

function uniqueName(base: string, existing: string[]): string {
  if (!existing.includes(base)) return base;
  let n = 2;
  while (existing.includes(`${base} ${n}`)) n += 1;
  return `${base} ${n}`;
}

function newProvider(
  kind: MarketKind,
  templates: Record<string, VendorTemplate>,
  existingNames: string[],
): ModelProvider {
  const tpl = templates[kind] || templates.custom;
  const market = MARKET.find((m) => m.id === kind);
  const baseName = market ? market.nameZh : tpl?.name || kind;
  const baseUrl = tpl?.base_url || "";
  const seedNames = (tpl?.models || []).filter(Boolean);
  const models =
    seedNames.length > 0
      ? seedNames.map((n) => newModelEntry(n, { base_url: baseUrl }))
      : [newModelEntry("", { base_url: baseUrl })];
  return {
    id: `prov_${Math.random().toString(36).slice(2, 10)}`,
    name: uniqueName(baseName, existingNames),
    vendor: tpl?.vendor || kind,
    market_id: kind,
    models,
  };
}

function setRole(setup: ModelSetup, role: "main" | "subagent", ref: ModelRef): ModelSetup {
  const next: ModelSetup = { ...setup, [role]: ref };
  if (role === "subagent") next.compress = { ...ref };
  return next;
}

function providerKind(p: ModelProvider): string {
  return (p.market_id || p.vendor || "custom").toLowerCase();
}

function iconKind(id: string): string {
  const k = id.toLowerCase();
  if (k.includes("anthropic") || k.includes("claude")) return "anthropic";
  if (k.includes("ollama")) return "ollama";
  if (k.includes("minimax") || k === "mm") return "minimax";
  if (k.includes("deepseek")) return "deepseek";
  if (k === "openai" || k === "oa") return "openai";
  if (k.includes("compatible") || k === "custom" || k === "api") return "compatible";
  return k || "compatible";
}

/** Distinct vendor glyphs — never a shared fallback letter for known vendors. */
/** Real brand logos from /vendor-icons (Simple Icons / brand paths). */
const VENDOR_ICON_SRC: Record<string, string> = {
  openai: "/vendor-icons/openai.svg",
  compatible: "/vendor-icons/openai-compatible.svg",
  custom: "/vendor-icons/openai-compatible.svg",
  anthropic: "/vendor-icons/anthropic.svg",
  ollama: "/vendor-icons/ollama.svg",
  minimax: "/vendor-icons/minimax.svg",
  deepseek: "/vendor-icons/deepseek.svg",
};

function ProviderIcon({ kind, size = 36 }: { kind: string; size?: number }) {
  const k = iconKind(kind);
  const src = VENDOR_ICON_SRC[k] || VENDOR_ICON_SRC.compatible;
  return (
    <span
      className={`rf-logo rf-logo-img v-${k}`}
      style={{ width: size, height: size }}
      title={k}
    >
      <img src={src} alt="" width={Math.round(size * 0.62)} height={Math.round(size * 0.62)} draggable={false} />
    </span>
  );
}

type RfOption = { value: string; label: string };
type RfGroup = { label: string; options: RfOption[] };

function RfSelect({
  value,
  disabled,
  placeholder,
  options,
  groups,
  className,
  onChange,
}: {
  value: string;
  disabled?: boolean;
  placeholder?: string;
  options?: RfOption[];
  groups?: RfGroup[];
  className?: string;
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [menuPos, setMenuPos] = useState<{ top: number; left: number; width: number; maxH: number } | null>(
    null,
  );
  const rootRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const visibleGroups = (groups || []).filter((g) => g.options.length > 0);
  const all = visibleGroups.length ? visibleGroups.flatMap((g) => g.options) : options ?? [];
  const current = all.find((o) => o.value === value);

  useEffect(() => {
    if (!open) return;
    const place = () => {
      const el = rootRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      const spaceBelow = window.innerHeight - r.bottom - 12;
      const spaceAbove = r.top - 12;
      const dropUp = spaceBelow < 180 && spaceAbove > spaceBelow;
      const maxH = Math.max(120, Math.min(240, dropUp ? spaceAbove : spaceBelow));
      setMenuPos({
        top: dropUp ? Math.max(8, r.top - maxH - 6) : r.bottom + 6,
        left: r.left,
        width: Math.max(r.width, 180),
        maxH,
      });
    };
    place();
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node;
      if (rootRef.current?.contains(t) || menuRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open]);

  const menu =
    open && !disabled && menuPos
      ? createPortal(
          <div
            ref={menuRef}
            className="rf-select-menu"
            role="listbox"
            style={{
              top: menuPos.top,
              left: menuPos.left,
              width: menuPos.width,
              maxHeight: menuPos.maxH,
            }}
            onMouseDown={(e) => e.stopPropagation()}
          >
            {visibleGroups.length
              ? visibleGroups.map((g, gi) => (
                  <div key={g.label || `g${gi}`} className="rf-select-group">
                    {g.label ? <div className="rf-select-group-label">{g.label}</div> : null}
                    {g.options.map((o) => (
                      <button
                        key={o.value}
                        type="button"
                        className={o.value === value ? "active" : ""}
                        onClick={() => {
                          onChange(o.value);
                          setOpen(false);
                        }}
                      >
                        {o.label}
                      </button>
                    ))}
                  </div>
                ))
              : all.map((o) => (
                  <button
                    key={o.value}
                    type="button"
                    className={o.value === value ? "active" : ""}
                    onClick={() => {
                      onChange(o.value);
                      setOpen(false);
                    }}
                  >
                    {o.label}
                  </button>
                ))}
          </div>,
          document.body,
        )
      : null;

  return (
    <div
      className={`rf-select${open ? " open" : ""}${disabled ? " disabled" : ""}${className ? ` ${className}` : ""}`}
      ref={rootRef}
    >
      <button
        type="button"
        className="rf-select-btn"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
      >
        <span className={current ? "" : "placeholder"}>{current?.label || placeholder || "—"}</span>
        <IconChevronDown size={14} />
      </button>
      {menu}
    </div>
  );
}

type EditTarget = { providerId: string; modelId: string };

export function ModelSettings({ setup, locale, onChange, onSave, saving, t, extra }: Props) {
  const templates = { ...DEFAULT_VENDOR_TEMPLATES, ...(setup.vendor_templates || {}) };
  const [edit, setEdit] = useState<EditTarget | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [showKey, setShowKey] = useState(false);
  const [marketQuery, setMarketQuery] = useState("");
  const [netFilter, setNetFilter] = useState<NetFilter>("all");
  const [renamingProv, setRenamingProv] = useState("");
  const [renameValue, setRenameValue] = useState("");
  const [confirmProvId, setConfirmProvId] = useState("");

  const editingModel = useMemo(() => {
    if (!edit) return null;
    const prov = setup.providers.find((p) => p.id === edit.providerId);
    const model = prov?.models.find((m) => m.id === edit.modelId) || null;
    return prov && model ? { prov, model } : null;
  }, [edit, setup.providers]);

  const marketList = useMemo(() => {
    const q = marketQuery.trim().toLowerCase();
    return MARKET.filter((m) => {
      if (netFilter === "offline" && m.net !== "offline") return false;
      if (netFilter === "online" && m.net !== "online") return false;
      if (!q) return true;
      return (
        m.nameZh.toLowerCase().includes(q) ||
        m.nameEn.toLowerCase().includes(q) ||
        m.descZh.includes(q) ||
        m.descEn.toLowerCase().includes(q)
      );
    });
  }, [marketQuery, netFilter]);

  const installedIds = useMemo(() => {
    const ids = new Set<string>();
    for (const p of setup.providers) ids.add((p.market_id || "custom").toLowerCase());
    return ids;
  }, [setup.providers]);


  function persist(next: ModelSetup) {
    onChange(next);
  }

  function patchModel(
    providerId: string,
    modelId: string,
    patch: Partial<ModelEntry>,
  ) {
    const next = {
      ...setup,
      providers: setup.providers.map((p) =>
        p.id !== providerId
          ? p
          : {
              ...p,
              models: p.models.map((m) => (m.id === modelId ? { ...m, ...patch } : m)),
            },
      ),
    };
    onChange(next);
  }


  function renameProvider(id: string, name: string) {
    const next = name.trim();
    if (!next) {
      setRenamingProv("");
      return;
    }
    persist({
      ...setup,
      providers: setup.providers.map((p) => (p.id === id ? { ...p, name: next } : p)),
    });
    setRenamingProv("");
  }

  function installFromMarket(kind: MarketKind) {
    if (installedIds.has(kind)) return;
    const p = newProvider(
      kind,
      templates,
      setup.providers.map((x) => x.name),
    );
    persist({ ...setup, providers: [...setup.providers, p] });
    setExpanded((d) => ({ ...d, [p.id]: true }));
  }

  function removeProvider(id: string) {
    const next = setup.providers.filter((p) => p.id !== id);
    const fallback = next[0]?.models[0];
    const remap = (ref: ModelRef): ModelRef => {
      if (ref.provider_id !== id) return ref;
      return fallback
        ? { provider_id: next[0].id, model_id: fallback.id }
        : { provider_id: "", model_id: "" };
    };
    persist({
      ...setup,
      providers: next,
      main: remap(setup.main),
      subagent: remap(setup.subagent),
      compress: remap(setup.compress),
    });
    if (edit?.providerId === id) setEdit(null);
    setConfirmProvId("");
  }

  function addBlankModel(provider: ModelProvider) {
    const tpl = templates[provider.market_id || ""] || templates.custom;
    const seed = provider.models[0];
    const entry = newModelEntry("", {
      base_url: seed?.base_url || tpl?.base_url || "",
      api_key: seed?.api_key || "",
    });
    persist({
      ...setup,
      providers: setup.providers.map((p) =>
        p.id === provider.id ? { ...p, models: [...p.models, entry] } : p,
      ),
    });
    setExpanded((d) => ({ ...d, [provider.id]: true }));
    openEdit(provider.id, entry.id );
  }


  function removeModel(providerId: string, modelId: string) {
    const prov = setup.providers.find((p) => p.id === providerId);
    if (!prov) return;
    const models = prov.models.filter((m) => m.id !== modelId);
    let next: ModelSetup = {
      ...setup,
      providers: setup.providers.map((p) => (p.id === providerId ? { ...p, models } : p)),
    };
    const fallback = models[0] || next.providers.find((p) => p.models[0])?.models[0];
    const fallbackProv =
      models[0]
        ? providerId
        : next.providers.find((p) => p.models.some((m) => m.id === fallback?.id))?.id || "";
    for (const role of ["main", "subagent", "compress"] as const) {
      if (next[role].model_id === modelId) {
        next = {
          ...next,
          [role]: fallback
            ? { provider_id: fallbackProv, model_id: fallback.id }
            : { provider_id: "", model_id: "" },
        };
      }
    }
    persist(next);
    if (edit?.modelId === modelId) setEdit(null);
  }

  function pickRole(role: "main" | "subagent", key: string) {
    const i = key.indexOf("::");
    if (i <= 0) return;
    persist(
      setRole(setup, role, {
        provider_id: key.slice(0, i),
        model_id: key.slice(i + 2),
      }),
    );
  }

  function setAsDefault(providerId: string, modelId: string) {
    pickRole("main", refKey({ provider_id: providerId, model_id: modelId }));
  }

  function applyNow() {
    onSave(setup, { restartChat: false });
  }

  function openEdit(providerId: string, modelId: string) {
    setEdit({ providerId, modelId });
    setShowKey(false);
  }

  function closeEdit() {
    setEdit(null);
    setShowKey(false);
  }

  const hasModels = setup.providers.some((p) => p.models.length > 0);

  function tagLabel(item: MarketItem): string | null {
    if (item.tagKind === "official") return t("modelTagOfficial");
    if (item.tagKind === "local") return t("modelTagLocal");
    if (item.tagKind === "compat") return t("modelTagCompat");
    return null;
  }


  return (
    <div className="rf">
      {setup.demo_mode ? <p className="rf-banner">{t("modelDemoHint")}</p> : null}

      <div className="rf-toolbar">
        <div className="rf-toolbar-copy">
          <h3 className="rf-page-title">{t("modelTitle")}</h3>
          <p className="hint rf-toolbar-hint">{t("modelPageHint")}</p>
        </div>
        <div className="rf-toolbar-actions">
          <button type="button" className="primary rf-save" disabled={saving} onClick={applyNow}>
            {saving ? t("modelSavingBtn") : t("saveModel")}
          </button>
        </div>
      </div>

      {extra}


      <section className="rf-block rf-block-defaults rf-top-defaults">
        <h3 className="rf-block-title">{t("modelStepAgents")}</h3>
        <div className="rf-defaults">
          {(["main", "subagent"] as const).map((role) => {
            const ref = role === "main" ? setup.main : setup.subagent;
            const auto = isAutoRef(ref);
            return (
              <label key={role} className={auto ? "rf-default-auto" : undefined}>
                <span>
                  {role === "main" ? t("mainModel") : t("subModel")}
                  {auto ? <em className="rf-auto-badge">{t("modelAutoBadge")}</em> : null}
                </span>
                <RfSelect
                  value={refKey(ref)}
                  disabled={!hasModels}
                  placeholder={t("modelPickProviderFirst")}
                  groups={[
                    {
                      label: "",
                      options: [
                        { value: refKey(autoRef()), label: `✦ ${t("modelAutoOption")}` },
                      ],
                    },
                    ...setup.providers
                      .filter((prov) => prov.models.length > 0)
                      .map((prov) => ({
                        label: prov.name,
                        options: prov.models.map((m) => ({
                          value: refKey({ provider_id: prov.id, model_id: m.id }),
                          label: m.name || t("modelUnnamed"),
                        })),
                      })),
                  ]}
                  onChange={(v) => pickRole(role, v)}
                />
              </label>
            );
          })}
        </div>
      </section>

      <div className="rf-layout">
        <div className="rf-main">
          {/* Installed providers with nested model lists */}
          <section className="rf-block rf-block-installed">
            <div className="rf-block-head">
              <h3 className="rf-block-title">
                {t("modelInstalledTitle")}
                <em className="rf-count-badge">{setup.providers.length}</em>
              </h3>
            </div>

            {setup.providers.length === 0 ? (
              <p className="rf-empty">{t("modelMarketEmpty")}</p>
            ) : (
              <ul className="rf-added">
                {setup.providers.map((p) => {
                  const kind = providerKind(p);
                  const show = expanded[p.id] ?? true;
                  return (
                    <li key={p.id} id={`rf-prov-${p.id}`} className="rf-added-card">
                      {confirmProvId === p.id ? (
                        <div className="rf-inline-confirm">
                          <span>{t("providerDeleteConfirm", p.name)}</span>
                          <div>
                            <button
                              type="button"
                              className="ghost danger"
                              onClick={() => removeProvider(p.id)}
                            >
                              {t("providerRemove")}
                            </button>
                            <button type="button" className="ghost" onClick={() => setConfirmProvId("")}>
                              {t("cancel")}
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="rf-added-top">
                          <ProviderIcon kind={kind} />
                          {renamingProv === p.id ? (
                            <form
                              className="rf-rename"
                              onSubmit={(e) => {
                                e.preventDefault();
                                renameProvider(p.id, renameValue);
                              }}
                            >
                              <input
                                autoFocus
                                value={renameValue}
                                onChange={(e) => setRenameValue(e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === "Escape") setRenamingProv("");
                                }}
                              />
                              <button type="submit" className="rf-icon-btn" title={t("modelDone")}>
                                <IconCheck size={14} />
                              </button>
                            </form>
                          ) : (
                            <div className="rf-added-info">
                              <div className="rf-added-name-row">
                                <strong>{p.name}</strong>
                                <span className="rf-status ok">{t("modelInstalledBadge")}</span>
                              </div>
                              <span className="rf-market-tags">
                                {(MARKET.find((m) => m.id === kind)?.tags || ["LLM"]).map((tag) => (
                                  <i key={tag}>{tag}</i>
                                ))}
                              </span>
                            </div>
                          )}
                          <div className="rf-added-actions">
                            <button
                              type="button"
                              className="ghost rf-manage-btn"
                              onClick={() => setExpanded((d) => ({ ...d, [p.id]: !show }))}
                            >
                              <IconSettings size={14} />
                              {t("modelManageModels")}
                              <span className="rf-chevron" aria-hidden>
                                {show ? "▴" : "▾"}
                              </span>
                            </button>
                            <button
                              type="button"
                              className="rf-icon-btn"
                              title={t("providerName")}
                              onClick={() => {
                                setRenamingProv(p.id);
                                setRenameValue(p.name);
                              }}
                            >
                              <IconPencil size={14} />
                            </button>
                            <button
                              type="button"
                              className="rf-icon-btn danger"
                              title={t("providerRemove")}
                              onClick={() => setConfirmProvId(p.id)}
                            >
                              <IconTrash size={14} />
                            </button>
                          </div>
                        </div>
                      )}

                      {show ? (
                        <div className="rf-prov-models">
                            <div className="rf-prov-models-head">
                              <span>{t("modelConfiguredTitle")}</span>
                              <button
                                type="button"
                                className="ghost rf-add-model-btn"
                                onClick={() => addBlankModel(p)}
                              >
                                <IconPlus size={14} /> {t("modelAddModel")}
                              </button>
                            </div>
                            {p.models.length === 0 ? (
                              <p className="hint rf-prov-models-empty">{t("modelAddModelsHint")}</p>
                            ) : (
                              <ul className="rf-cfg-rows">
                                {p.models.map((m) => {
                                  const mainOn =
                                    setup.main.provider_id === p.id && setup.main.model_id === m.id;
                                  const ready = Boolean(m.api_key_set || m.api_key || m.name);
                                  const iconFor =
                                    kind === "custom" && m.name.toLowerCase().includes("gpt")
                                      ? "openai"
                                      : kind;
                                  return (
                                    <li
                                      key={m.id}
                                      className={`rf-cfg-row${mainOn ? " is-default" : ""}`}
                                    >
                                      <button
                                        type="button"
                                        className="rf-cfg-open"
                                        onClick={() =>
                                          openEdit(p.id, m.id )
                                        }
                                        title={t("modelConfigure")}
                                      >
                                        <ProviderIcon kind={iconFor} size={28} />
                                        <code className="rf-cfg-name">
                                          {m.name || t("modelUnnamed")}
                                        </code>
                                        <span
                                          className={`rf-switch${ready ? " on" : ""}`}
                                          aria-hidden
                                        >
                                          <span className="rf-switch-track" />
                                          <em>{t("modelEnabled")}</em>
                                        </span>
                                      </button>
                                      <button
                                        type="button"
                                        className={`ghost rf-default-btn${mainOn ? " on" : ""}`}
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          setAsDefault(p.id, m.id);
                                        }}
                                      >
                                        {t("modelSetDefault")}
                                      </button>
                                      <button
                                        type="button"
                                        className="rf-icon-btn danger"
                                        title={t("modelDelete")}
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          removeModel(p.id, m.id);
                                        }}
                                      >
                                        <IconTrash size={14} />
                                      </button>
                                    </li>
                                  );
                                })}
                              </ul>
                            )}
                          </div>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </div>

        <aside className="rf-side">
          <div className="rf-market">
            <h3 className="rf-block-title">{t("modelProviders")}</h3>
            <p className="hint rf-side-hint">{t("modelMarketSideHint")}</p>
            <label className="rf-search">
              <IconSearch size={14} />
              <input
                value={marketQuery}
                onChange={(e) => setMarketQuery(e.target.value)}
                placeholder={t("modelSearchMarket")}
              />
            </label>
            <div className="rf-filter-chips" role="group" aria-label={t("modelFilterAll")}>
              {(
                [
                  ["all", "modelFilterAll"],
                  ["offline", "modelFilterOffline"],
                  ["online", "modelFilterOnline"],
                ] as const
              ).map(([id, key]) => (
                <button
                  key={id}
                  type="button"
                  className={netFilter === id ? "active" : ""}
                  onClick={() => setNetFilter(id)}
                >
                  {t(key)}
                </button>
              ))}
            </div>
            <ul className="rf-market-list">
              {marketList.map((item) => {
                const installed = installedIds.has(item.id);
                const tag = tagLabel(item);
                return (
                  <li key={item.id}>
                    <div className={`rf-market-card${installed ? " added" : ""}`}>
                      <ProviderIcon kind={item.id === "custom" ? "compatible" : item.id} />
                      <span className="rf-market-text">
                        <strong>
                          {locale === "en" ? item.nameEn : item.nameZh}
                          {tag ? (
                            <i className={`rf-tag-pill rf-tag-${item.tagKind || "plain"}`}>{tag}</i>
                          ) : null}
                        </strong>
                        <em>{locale === "en" ? item.descEn : item.descZh}</em>
                      </span>
                      <button
                        type="button"
                        className={`rf-install-btn${installed ? " installed" : ""}`}
                        disabled={installed}
                        onClick={() => installFromMarket(item.id)}
                      >
                        {installed ? t("modelInstalledBadge") : t("modelInstall")}
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>

        </aside>
      </div>

      {editingModel
        ? createPortal(
            <div className="rf-drawer-backdrop" onClick={closeEdit}>
              <div
                className="rf-drawer"
                role="dialog"
                aria-label={t("modelConfigure")}
                onClick={(e) => e.stopPropagation()}
              >
                <div className="rf-drawer-head">
                  <h3>{t("modelConfigure")}</h3>
                  <button type="button" className="rf-icon-btn" onClick={closeEdit} title={t("close")}>
                    <IconX size={14} />
                  </button>
                </div>
                <p className="rf-drawer-sub">{editingModel.prov.name}</p>
                <div className="rf-drawer-body">
                  <label>
                    <span>{t("modelNameLocked")}</span>
                    <input
                      value={editingModel.model.name}
                      onChange={(e) =>
                        patchModel(editingModel.prov.id, editingModel.model.id, {
                          name: e.target.value,
                        })
                      }
                      placeholder={t("modelNamePlaceholder")}
                      autoFocus
                    />
                  </label>
                  <label>
                    <span>{t("modelBaseUrl")}</span>
                    <input
                      value={editingModel.model.base_url}
                      onChange={(e) =>
                        patchModel(editingModel.prov.id, editingModel.model.id, {
                          base_url: e.target.value,
                        })
                      }
                      placeholder="https://api.openai.com/v1"
                    />
                  </label>
                  <label>
                    <span>API Key</span>
                    <div className="rf-key-row">
                      <input
                        type={showKey ? "text" : "password"}
                        autoComplete="off"
                        spellCheck={false}
                        placeholder={
                          editingModel.model.api_key_set
                            ? editingModel.model.api_key_masked || "sk-••••••••"
                            : "sk-..."
                        }
                        value={editingModel.model.api_key || ""}
                        onChange={(e) =>
                          patchModel(editingModel.prov.id, editingModel.model.id, {
                            api_key: e.target.value,
                            api_key_set: Boolean(e.target.value.trim()),
                          })
                        }
                      />
                      <button
                        type="button"
                        className="rf-key-eye"
                        title={showKey ? t("modelKeyHide") : t("modelKeyShow")}
                        onClick={() => setShowKey((v) => !v)}
                      >
                        {showKey ? <IconEyeOff size={16} /> : <IconEye size={16} />}
                      </button>
                    </div>
                  </label>
                </div>
                <div className="rf-drawer-foot">
                  <button
                    type="button"
                    className="ghost danger"
                    onClick={() => {
                      removeModel(editingModel.prov.id, editingModel.model.id);
                      closeEdit();
                    }}
                  >
                    <IconTrash size={14} /> {t("modelDelete")}
                  </button>
                  <button type="button" className="primary" onClick={closeEdit}>
                    {t("modelDone")}
                  </button>
                </div>
              </div>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
