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

type MarketKind = "custom";

const MARKET: Array<{
  id: MarketKind;
  nameZh: string;
  nameEn: string;
  descZh: string;
  descEn: string;
  tags: string[];
}> = [
  {
    id: "custom",
    nameZh: "OpenAI-API-Compatible",
    nameEn: "OpenAI-API-Compatible",
    descZh: "任意兼容接口 / 网关，模型名称可自由填写",
    descEn: "Any compatible gateway — type any model name",
    tags: ["LLM"],
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
  return {
    id: `prov_${Math.random().toString(36).slice(2, 10)}`,
    name: uniqueName(baseName, existingNames),
    vendor: tpl?.vendor || "openai",
    market_id: kind,
    models: [newModelEntry("", { base_url: baseUrl })],
  };
}

function setRole(setup: ModelSetup, role: "main" | "subagent", ref: ModelRef): ModelSetup {
  const next: ModelSetup = { ...setup, [role]: ref };
  if (role === "subagent") next.compress = { ...ref };
  return next;
}

function badge(kind: string): string {
  if (kind === "deepseek") return "DS";
  if (kind === "openai") return "OA";
  if (kind === "minimax") return "MM";
  if (kind === "ollama") return "OL";
  return "API";
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
  const [mineQuery, setMineQuery] = useState("");
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
    return MARKET.filter(
      (m) =>
        !q ||
        m.nameZh.toLowerCase().includes(q) ||
        m.nameEn.toLowerCase().includes(q) ||
        m.descZh.includes(q) ||
        m.descEn.toLowerCase().includes(q),
    );
  }, [marketQuery]);

  const filteredProviders = useMemo(() => {
    const q = mineQuery.trim().toLowerCase();
    if (!q) return setup.providers;
    return setup.providers
      .map((p) => {
        const nameHit = p.name.toLowerCase().includes(q);
        const models = nameHit ? p.models : p.models.filter((m) => m.name.toLowerCase().includes(q));
        return nameHit || models.length ? { ...p, models } : null;
      })
      .filter((p): p is ModelProvider => Boolean(p));
  }, [setup.providers, mineQuery]);

  function persist(next: ModelSetup) {
    onChange(next);
  }

  function patchModel(
    providerId: string,
    modelId: string,
    patch: Partial<ModelEntry>,
    save = false,
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
    if (save) persist(next);
    else onChange(next);
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

  function addFromMarket(kind: MarketKind) {
    const existing =
      setup.providers.find((p) => (p.market_id || "custom") === kind) || setup.providers[0];
    if (existing) {
      addBlankModel(existing);
      return;
    }
    const p = newProvider(
      kind,
      templates,
      setup.providers.map((x) => x.name),
    );
    persist({ ...setup, providers: [...setup.providers, p] });
    setExpanded((d) => ({ ...d, [p.id]: true }));
    if (p.models[0]) {
      setEdit({ providerId: p.id, modelId: p.models[0].id });
      setShowKey(false);
    }
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
    setEdit({ providerId: provider.id, modelId: entry.id });
    setShowKey(false);
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

  function openEdit(providerId: string, modelId: string) {
    setEdit({ providerId, modelId });
    setShowKey(false);
    setExpanded((d) => ({ ...d, [providerId]: true }));
  }

  function applyNow() {
    onSave(setup, { restartChat: false });
  }

  function closeEdit() {
    setEdit(null);
    setShowKey(false);
  }

  function handleDone() {
    closeEdit();
  }

  const hasModels = setup.providers.some((p) => p.models.length > 0);

  return (
    <div className="rf">
      {setup.demo_mode ? <p className="rf-banner">{t("modelDemoHint")}</p> : null}

      <div className="rf-toolbar">
        <p className="hint rf-toolbar-hint">{t("modelHint")}</p>
        <div className="rf-toolbar-actions">
          <button
            type="button"
            className="primary rf-save"
            disabled={saving}
            onClick={applyNow}
          >
            {saving ? t("modelSavingBtn") : t("saveModel")}
          </button>
        </div>
      </div>

      {extra}

      <div className="rf-layout">
        <div className="rf-main">
          <section className="rf-block rf-block-defaults">
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
                            { value: refKey(autoRef()), label: `✨ ${t("modelAutoOption")}` },
                          ],
                        },
                        ...setup.providers
                          .filter((p) => p.models.length > 0)
                          .map((p) => ({
                            label: p.name,
                            options: p.models.map((m) => ({
                              value: refKey({ provider_id: p.id, model_id: m.id }),
                              label: m.name || t("modelUnnamed"),
                            })),
                          })),
                      ]}
                      onChange={(v) => pickRole(role, v)}
                    />
                    {auto ? <p className="hint rf-auto-hint">{t("modelAutoDesc")}</p> : null}
                  </label>
                );
              })}
            </div>
          </section>

          <section className="rf-block rf-block-mine">
            <div className="rf-block-head">
              <h3 className="rf-block-title">{t("modelAddedTitle")}</h3>
            </div>
            {setup.providers.length > 0 && (
              <label className="rf-search">
                <IconSearch size={14} />
                <input
                  value={mineQuery}
                  onChange={(e) => setMineQuery(e.target.value)}
                  placeholder={t("modelSearchMine")}
                />
              </label>
            )}

            {setup.providers.length === 0 ? (
              <p className="rf-empty">{t("modelMarketEmpty")}</p>
            ) : filteredProviders.length === 0 ? (
              <p className="rf-empty">{t("modelSearchMine")}</p>
            ) : (
              <ul className="rf-added">
                {filteredProviders.map((p) => {
                  const full = setup.providers.find((x) => x.id === p.id) || p;
                  const kind = p.market_id || p.vendor || "custom";
                  const show = mineQuery ? true : (expanded[p.id] ?? true);
                  const readyCount = full.models.filter((m) => m.api_key_set || m.api_key).length;
                  return (
                    <li key={p.id} id={`rf-prov-${p.id}`} className="rf-added-card">
                      {confirmProvId === p.id ? (
                        <div className="rf-inline-confirm">
                          <span>{t("providerDeleteConfirm", p.name)}</span>
                          <div>
                            <button type="button" className="ghost danger" onClick={() => removeProvider(p.id)}>
                              {t("providerRemove")}
                            </button>
                            <button type="button" className="ghost" onClick={() => setConfirmProvId("")}>
                              {t("cancel")}
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="rf-added-top">
                          <span className={`rf-logo v-${kind}`}>{badge(kind)}</span>
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
                              <strong>{p.name}</strong>
                              <span>{t("modelCount", String(full.models.length), String(readyCount))}</span>
                            </div>
                          )}
                          <div className="rf-added-actions">
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
                              className="rf-icon-btn"
                              title={t("modelAddModel")}
                              onClick={() => addBlankModel(full)}
                            >
                              <IconPlus size={14} />
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

                      <button
                        type="button"
                        className="rf-models-toggle"
                        onClick={() => setExpanded((d) => ({ ...d, [p.id]: !show }))}
                      >
                        {show ? t("modelHideList") : t("modelShowList", String(p.models.length))}
                        <span aria-hidden>{show ? "▴" : "▾"}</span>
                      </button>

                      {show ? (
                        <div className="rf-model-panel">
                          {p.models.length === 0 ? (
                            <p className="hint">{t("modelAddModelsHint")}</p>
                          ) : (
                            <ul className="rf-model-rows">
                              {p.models.map((m) => {
                                const mainOn =
                                  setup.main.provider_id === p.id && setup.main.model_id === m.id;
                                const subOn =
                                  setup.subagent.provider_id === p.id &&
                                  setup.subagent.model_id === m.id;
                                const ready = Boolean(m.api_key_set || m.api_key);
                                const active = edit?.providerId === p.id && edit?.modelId === m.id;
                                return (
                                  <li key={m.id} className={active ? "active" : ""}>
                                    <button
                                      type="button"
                                      className="rf-model-open"
                                      onClick={() => openEdit(p.id, m.id)}
                                      title={t("modelConfigure")}
                                    >
                                      <code>{m.name || t("modelUnnamed")}</code>
                                      <span className={`rf-dot${ready ? " ok" : ""}`} />
                                    </button>
                                    <div className="rf-model-row-actions">
                                      <button
                                        type="button"
                                        className={`rf-pill${mainOn ? " on" : ""}`}
                                        onClick={() =>
                                          pickRole(
                                            "main",
                                            refKey({ provider_id: p.id, model_id: m.id }),
                                          )
                                        }
                                      >
                                        {t("mainModel").slice(0, 1)}
                                      </button>
                                      <button
                                        type="button"
                                        className={`rf-pill${subOn ? " on" : ""}`}
                                        onClick={() =>
                                          pickRole(
                                            "subagent",
                                            refKey({ provider_id: p.id, model_id: m.id }),
                                          )
                                        }
                                      >
                                        {t("subModel").slice(0, 1)}
                                      </button>
                                      <button
                                        type="button"
                                        className="rf-icon-btn"
                                        title={t("modelConfigure")}
                                        onClick={() => openEdit(p.id, m.id)}
                                      >
                                        <IconSettings size={14} />
                                      </button>
                                    </div>
                                  </li>
                                );
                              })}
                            </ul>
                          )}
                          <button
                            type="button"
                            className="rf-add-model-link"
                            onClick={() => addBlankModel(full)}
                          >
                            <IconPlus size={12} /> {t("modelAddModel")}
                          </button>
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
          {editingModel ? (
            <div className="rf-editor">
              <div className="rf-side-head">
                <div>
                  <h3>{t("modelConfigure")}</h3>
                  <p>{editingModel.prov.name}</p>
                </div>
                <button type="button" className="rf-icon-btn" onClick={closeEdit} title={t("close")}>
                  <IconX size={14} />
                </button>
              </div>
              <div className="rf-editor-body">
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
                  />
                </label>
                <label>
                  <span>Base URL</span>
                  <input
                    value={editingModel.model.base_url}
                    onChange={(e) =>
                      patchModel(editingModel.prov.id, editingModel.model.id, {
                        base_url: e.target.value,
                      })
                    }
                    placeholder="https://api.example.com/v1"
                  />
                </label>
                <label>
                  <span>API Key</span>
                  <div className="rf-key-row">
                    <input
                      type={showKey ? "text" : "password"}
                      autoComplete="off"
                      spellCheck={false}
                      placeholder="sk-..."
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
              <div className="rf-editor-foot">
                <button
                  type="button"
                  className="ghost danger"
                  onClick={() => removeModel(editingModel.prov.id, editingModel.model.id)}
                >
                  <IconTrash size={14} /> {t("modelDelete")}
                </button>
                <button type="button" className="primary" disabled={saving} onClick={handleDone}>
                  {saving ? t("modelSavingBtn") : t("modelDone")}
                </button>
              </div>
            </div>
          ) : (
            <div className="rf-market">
              <h3 className="rf-block-title">{t("modelAvailableTitle")}</h3>
              <p className="hint rf-side-hint">{t("modelMarketHint")}</p>
              {MARKET.length > 1 && (
                <label className="rf-search">
                  <IconSearch size={14} />
                  <input
                    value={marketQuery}
                    onChange={(e) => setMarketQuery(e.target.value)}
                    placeholder={t("modelSearchMarket")}
                  />
                </label>
              )}
              <ul className="rf-market-list">
                {marketList.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      className="rf-market-card"
                      onClick={() => addFromMarket(item.id)}
                    >
                      <span className={`rf-logo v-${item.id}`}>{badge(item.id)}</span>
                      <span className="rf-market-text">
                        <strong>{locale === "en" ? item.nameEn : item.nameZh}</strong>
                        <em>{locale === "en" ? item.descEn : item.descZh}</em>
                        <span className="rf-market-tags">
                          {item.tags.map((tag) => (
                            <i key={tag}>{tag}</i>
                          ))}
                        </span>
                      </span>
                      <span className="rf-market-add">+</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
