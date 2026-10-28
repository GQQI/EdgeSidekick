import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  downloadPluginTemplate,
  fetchMarketplace,
  fetchPlugin,
  fetchPluginGuide,
  fetchPlugins,
  installPlugin,
  savePluginCredentials,
  setPluginActiveVersion,
  setPluginEnabled,
  startPluginOAuth,
  uninstallPlugin,
  uninstallPluginVersion,
  fetchPrefs,
  savePrefs,
  uploadPlugin,
  verifyPlugin,
  type InstalledPlugin,
  type MarketCatalog,
  type MarketItem,
  type PluginCallResult,
  type PluginToolSpec,
  type PluginVersionRow,
} from "../api";
import { usePrefs } from "../prefs";
import type { MsgKey } from "../i18n";
import { MarkdownView } from "./MarkdownView";
import { IconX } from "./icons";

type T = (key: MsgKey, ...args: string[]) => string;

type Props = { onToast?: (msg: string) => void; onOpenMcp?: () => void };
type View = "market" | "installed";
type NetFilter = "all" | "offline" | "online";

const PAGE_SIZE = 9;
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

function isOfflineItem(item: { offline?: boolean; network?: string[]; tags?: string[] }): boolean {
  if (typeof item.offline === "boolean") return item.offline;
  if (Array.isArray(item.network)) return item.network.length === 0;
  return (item.tags || []).map((t) => t.toLowerCase()).includes("offline");
}

function toolCountOf(item: MarketItem | InstalledPlugin): number {
  if ("tool_count" in item && typeof item.tool_count === "number") return item.tool_count;
  if (Array.isArray(item.tools)) return item.tools.length;
  return 0;
}

function PluginIcon({ icon, name, size = 44 }: { icon?: string; name: string; size?: number }) {
  if (icon) return <img className="plugin-icon" src={icon} alt="" width={size} height={size} />;
  return (
    <span className="plugin-icon plugin-icon-fallback" style={{ width: size, height: size }} aria-hidden>
      {name.slice(0, 1).toUpperCase()}
    </span>
  );
}

function OfflineBadge({ offline }: { offline: boolean }) {
  const { t } = usePrefs();
  return offline ? (
    <span className="plugin-badge offline" title={t("pluginBadgeOfflineHint")}>
      {t("pluginBadgeOffline")}
    </span>
  ) : (
    <span className="plugin-badge online" title={t("pluginBadgeOnlineHint")}>
      {t("pluginBadgeOnline")}
    </span>
  );
}

function statusOf(p: InstalledPlugin, t: T): { label: string; tone: "ok" | "warn" | "off" } {
  if (!p.enabled) return { label: t("pluginBadgeDisabled"), tone: "off" };
  if (!p.credential_status.configured) return { label: t("pluginBadgeNeedsConfig"), tone: "warn" };
  return { label: t("pluginBadgeReady"), tone: "ok" };
}

const uploadTree = (t: T) => `my-plugin/
  manifest.json   ← ${t("pluginRequired")}  # version / author / source
  main.py         ← ${t("pluginRequired")}
  icon.svg        ← ${t("pluginOptional")}
  README.md       ← ${t("pluginOptional")}`;

function packageMetaLine(p: InstalledPlugin | MarketItem | null | undefined, t: T): string {
  if (!p) return "";
  const bits: string[] = [`v${p.version}`];
  if (p.author) bits.push(`${t("pluginAuthorLabel")}${p.author}`);
  const pkgSource =
    ("package_source" in p && p.package_source) ||
    ("source" in p && typeof p.source === "string" && p.source && !["upload", "url", "marketplace", "bundled", "remote"].includes(p.source)
      ? p.source
      : "");
  if (pkgSource) bits.push(`${t("pluginSourceLabel")}${pkgSource}`);
  if ("version_count" in p && typeof p.version_count === "number" && p.version_count > 1) {
    bits.push(t("pluginVersionCount", String(p.version_count)));
  }
  return bits.join(" · ");
}

function Pagination({
  page,
  pageCount,
  onChange,
  label,
}: {
  page: number;
  pageCount: number;
  onChange: (p: number) => void;
  label: string;
}) {
  const { t } = usePrefs();
  if (pageCount <= 1) return null;
  return (
    <div className="plugin-pagination" role="navigation" aria-label={label}>
      <button type="button" className="ghost" disabled={page <= 0} onClick={() => onChange(page - 1)}>
        {t("commonPrev")}
      </button>
      <span className="hint">
        {page + 1} / {pageCount}
      </span>
      <button type="button" className="ghost" disabled={page >= pageCount - 1} onClick={() => onChange(page + 1)}>
        {t("commonNext")}
      </button>
    </div>
  );
}

export function PluginSettings({ onToast }: Props) {
  const { t } = usePrefs();
  const toastRef = useRef(onToast);
  toastRef.current = onToast;
  const toast = (msg: string) => toastRef.current?.(msg);

    const [pluginsInChat, setPluginsInChat] = useState(true);
  const [pluginsBindBusy, setPluginsBindBusy] = useState(false);
const [view, setView] = useState<View>("market");
  const [catalog, setCatalog] = useState<MarketCatalog | null>(null);
  const [installed, setInstalled] = useState<InstalledPlugin[] | null>(null);
  const [loadError, setLoadError] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [netFilter, setNetFilter] = useState<NetFilter>("all");
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [urlOpen, setUrlOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const [guideText, setGuideText] = useState("");
  const [guideLoading, setGuideLoading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [marketPage, setMarketPage] = useState(0);
  const [installedPage, setInstalledPage] = useState(0);
  const fileRef = useRef<HTMLInputElement>(null);

  const reload = useCallback(async (refresh = false) => {
    setLoadError("");
    // Load independently: a hung/failed marketplace must not blank「已安装」.
    const results = await Promise.allSettled([fetchMarketplace(refresh), fetchPlugins()]);
    const errors: string[] = [];
    if (results[0].status === "fulfilled") {
      setCatalog(results[0].value);
    } else {
      errors.push(t("pluginLoadMarket", errText(results[0].reason)));
      setCatalog((prev) => prev ?? { items: [], categories: [], sources: [], errors: [] });
    }
    if (results[1].status === "fulfilled") {
      setInstalled(results[1].value);
    } else {
      errors.push(t("pluginLoadInstalled", errText(results[1].reason)));
      setInstalled((prev) => prev ?? []);
    }
    if (errors.length) setLoadError(errors.join("; "));
  }, [t]);

  useEffect(() => {
    void fetchPrefs()
      .then((p) => {
        if (typeof p.plugins_in_chat === "boolean") setPluginsInChat(p.plugins_in_chat);
      })
      .catch(() => undefined);

    void reload();
  }, [reload]);

  const install = async (target: { id?: string; url?: string }, label: string) => {
    setBusy(target.id || "url");
    try {
      const plugin = await installPlugin(target);
      const suffix = plugin.credential_status.configured ? "" : t("pluginInstalledNeedsSetup");
      toast(t("pluginInstalledToast", plugin.name, suffix));
      await reload();
      setSelected(plugin.id);
      return true;
    } catch (e) {
      toast(t("pluginInstallFailed", label, errText(e)));
      return false;
    } finally {
      setBusy(null);
    }
  };

  const onUpload = async (file: File | undefined) => {
    if (!file) return;
    setBusy("upload");
    try {
      const plugin = await uploadPlugin(file);
      toast(t("pluginInstalledToast", plugin.name, `${plugin.version}`));
      setUploadOpen(false);
      setDragOver(false);
      await reload();
      setSelected(plugin.id);
    } catch (e) {
      toast(errText(e));
    } finally {
      setBusy(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const openGuide = async () => {
    setGuideOpen(true);
    if (guideText) return;
    setGuideLoading(true);
    try {
      setGuideText(await fetchPluginGuide());
    } catch (e) {
      setGuideText(t("pluginGuideLoadFail", errText(e)));
    } finally {
      setGuideLoading(false);
    }
  };

  const items = catalog?.items || [];
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter((item) => {
      if (category && item.category !== category) return false;
      const off = isOfflineItem(item);
      if (netFilter === "offline" && !off) return false;
      if (netFilter === "online" && off) return false;
      if (!q) return true;
      return [item.name, item.description, item.id, ...(item.tags || [])].some((s) => String(s || "").toLowerCase().includes(q));
    });
  }, [items, query, category, netFilter]);

  const installedFiltered = useMemo(() => {
    const list = installed || [];
    const q = query.trim().toLowerCase();
    return list.filter((p) => {
      const off = isOfflineItem(p);
      if (netFilter === "offline" && !off) return false;
      if (netFilter === "online" && off) return false;
      if (category && p.category !== category) return false;
      if (!q) return true;
      return [p.name, p.description, p.id, ...(p.tags || [])].some((s) => String(s || "").toLowerCase().includes(q));
    });
  }, [installed, query, category, netFilter]);

  useEffect(() => {
    setMarketPage(0);
    setInstalledPage(0);
  }, [query, category, netFilter, view]);

  const marketPageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const installedPageCount = Math.max(1, Math.ceil(installedFiltered.length / PAGE_SIZE));
  const safeMarketPage = Math.min(marketPage, marketPageCount - 1);
  const safeInstalledPage = Math.min(installedPage, installedPageCount - 1);
  const marketSlice = filtered.slice(safeMarketPage * PAGE_SIZE, safeMarketPage * PAGE_SIZE + PAGE_SIZE);
  const installedSlice = installedFiltered.slice(safeInstalledPage * PAGE_SIZE, safeInstalledPage * PAGE_SIZE + PAGE_SIZE);

  if (selected) {
    return (
      <PluginDetail
        id={selected}
        marketItem={items.find((i) => i.id === selected)}
        installedPlugin={installed?.find((p) => p.id === selected)}
        busy={busy === selected}
        onInstall={(item) => void install({ id: item.id }, item.name)}
        onBack={() => setSelected(null)}
        onChanged={() => void reload()}
        onRemoved={() => {
          setSelected(null);
          void reload();
        }}
        toast={toast}
      />
    );
  }

  const installedCount = installed?.length ?? 0;
  return (
    <div className="plugin-settings settings-pane">
      <header className="plugin-page-head">
        <div className="plugin-page-titles">
          <h3>{t("pluginTitle")}</h3>
          <p className="hint">{t("pluginSubtitle")}</p>
          <label className="mcp-check mcp-chat-bind">
            <input
              type="checkbox"
              checked={pluginsInChat}
              disabled={pluginsBindBusy}
              onChange={(e) => {
                const on = e.target.checked;
                setPluginsInChat(on);
                setPluginsBindBusy(true);
                void savePrefs({ plugins_in_chat: on })
                  .then(() => onToast?.(on ? t("pluginsInChatOn") : t("pluginsInChatOff")))
                  .catch((err) =>
                    onToast?.(err instanceof Error ? err.message : String(err)),
                  )
                  .finally(() => setPluginsBindBusy(false));
              }}
            />
            {t("pluginsInChat")}
          </label>
          <p className="hint">{t("pluginsInChatHint")}</p>

        </div>
        <div className="plugin-bar-actions">
          <button
            type="button"
            className="ghost"
            disabled={busy === "template"}
            onClick={() => {
              setBusy("template");
              void downloadPluginTemplate()
                .then(() => toast(t("pluginTemplateDownloaded")))
                .catch((e) => toast(errText(e)))
                .finally(() => setBusy(null));
            }}
          >
            {t("pluginDownloadTemplate")}
          </button>
          <button type="button" className="ghost" onClick={() => void openGuide()}>
            {t("pluginDevGuide")}
          </button>
          <button type="button" className="ghost" onClick={() => setUrlOpen(true)}>
            {t("pluginInstallUrl")}
          </button>
          <button type="button" className="ghost" onClick={() => setUploadOpen(true)}>
            {t("pluginUpload")}
          </button>
        </div>
      </header>

      <div className="plugin-bar">
        <div className="mcp-transport" role="tablist" aria-label={t("pluginTitle")}>
          <button type="button" role="tab" aria-selected={view === "market"} className={view === "market" ? "active" : ""} onClick={() => setView("market")}>
            {t("pluginMarketTab")}
          </button>
          <button type="button" role="tab" aria-selected={view === "installed"} className={view === "installed" ? "active" : ""} onClick={() => setView("installed")}>
            {t("pluginInstalledCount", installedCount ? ` · ${installedCount}` : "")}
          </button>
        </div>
      </div>

      {urlOpen ? (
        <div className="modal-backdrop" onClick={() => setUrlOpen(false)} role="presentation">
          <div
            className="modal plugin-action-modal"
            role="dialog"
            aria-modal="true"
            aria-label={t("pluginInstallUrl")}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-head">
              <h2>{t("pluginInstallUrl")}</h2>
              <button type="button" className="icon-btn" onClick={() => setUrlOpen(false)} title={t("close")} aria-label={t("close")}>
                <IconX size={14} />
              </button>
            </div>
            <div className="modal-body">
              <UrlInstall
                busy={busy === "url"}
                onSubmit={async (url) => {
                  const ok = await install({ url }, url);
                  if (ok) setUrlOpen(false);
                }}
              />
            </div>
          </div>
        </div>
      ) : null}

      {uploadOpen ? (
        <div className="modal-backdrop" onClick={() => { if (busy !== "upload") setUploadOpen(false); }} role="presentation">
          <div
            className="modal plugin-action-modal plugin-upload-modal"
            role="dialog"
            aria-modal="true"
            aria-label={t("pluginUploadTitle")}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-head">
              <h2>{t("pluginUploadTitle")}</h2>
              <button type="button" className="icon-btn" onClick={() => setUploadOpen(false)} disabled={busy === "upload"} title={t("close")} aria-label={t("close")}>
                <IconX size={14} />
              </button>
            </div>
            <div className="modal-body">
              <p className="hint plugin-upload-hint">{t("pluginUploadRules")}</p>
              <div
                className={`plugin-upload-zone${dragOver ? " drag-over" : ""}${busy === "upload" ? " busy" : ""}`}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragOver(false);
                  const f = e.dataTransfer.files?.[0];
                  if (f) void onUpload(f);
                }}
              >
                <input ref={fileRef} type="file" accept=".zip,application/zip" hidden onChange={(e) => void onUpload(e.target.files?.[0])} />
                <div className="plugin-upload-main">
                  <strong>{t("pluginUploadDrop")}</strong>
                  <p>
                    <button type="button" className="primary" disabled={busy === "upload"} onClick={() => fileRef.current?.click()}>
                      {busy === "upload" ? t("pluginInstalling") : t("pluginChooseFile")}
                    </button>
                  </p>
                </div>
                <pre className="plugin-upload-tree" aria-label={t("pluginUploadStructure")}>
                  {uploadTree(t)}
                </pre>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {guideOpen ? (
        <div className="modal-backdrop" onClick={() => setGuideOpen(false)} role="presentation">
          <div
            className="modal plugin-guide-modal"
            role="dialog"
            aria-modal="true"
            aria-label={t("pluginGuideTitle")}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-head">
              <h2>{t("pluginGuideTitle")}</h2>
              <button type="button" className="icon-btn" onClick={() => setGuideOpen(false)} title={t("close")} aria-label={t("close")}>
                <IconX size={14} />
              </button>
            </div>
            <div className="modal-body plugin-guide-body">
              {guideLoading ? <p className="hint">{t("pluginGuideLoading")}</p> : <MarkdownView content={guideText} />}
            </div>
          </div>
        </div>
      ) : null}

      {catalog?.errors?.length ? (
        <div className="plugin-notice warn" role="status">
          {catalog.errors.map((e) => (
            <div key={e.source}>
              {t("pluginMarketUnavailable", e.source, e.error)}
            </div>
          ))}
        </div>
      ) : null}

      {loadError ? (
        <div className="plugin-notice error" role="alert">
          {t("pluginLoadFail", loadError)}
          <button type="button" className="ghost" onClick={() => void reload()}>
            {t("commonRetry")}
          </button>
        </div>
      ) : !catalog || !installed ? (
        <div className="plugin-grid" aria-busy>
          {[0, 1, 2].map((i) => (
            <div key={i} className="plugin-card skeleton" />
          ))}
        </div>
      ) : (
        <>
          <div className="plugin-filter">
            <input
              type="search"
              placeholder={t("pluginSearchPlaceholder")}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label={t("pluginSearchAria")}
            />
            <div className="plugin-chips" role="group" aria-label={t("pluginFilterNetwork")}>
              {(
                [
                  ["all", t("pluginAll")],
                  ["offline", t("pluginOfflineFilter")],
                  ["online", t("pluginOnlineFilter")],
                ] as const
              ).map(([id, label]) => (
                <button key={id} type="button" className={netFilter === id ? "active" : ""} onClick={() => setNetFilter(id)}>
                  {label}
                </button>
              ))}
            </div>
            <div className="plugin-chips" role="group" aria-label={t("pluginFilterCategory")}>
              {["", ...catalog.categories].map((c) => (
                <button key={c || "all"} type="button" className={category === c ? "active" : ""} onClick={() => setCategory(c)}>
                  {c || t("pluginAllCategories")}
                </button>
              ))}
            </div>
          </div>

          {view === "market" ? (
            filtered.length === 0 ? (
              <p className="hint plugin-empty">{t("pluginEmptyMarket")}</p>
            ) : (
              <>
                <div className="plugin-grid">
                  {marketSlice.map((item) => (
                    <MarketCard
                      key={item.id}
                      item={item}
                      busy={busy === item.id}
                      onOpen={() => setSelected(item.id)}
                      onInstall={() => void install({ id: item.id }, item.name)}
                    />
                  ))}
                </div>
                <Pagination page={safeMarketPage} pageCount={marketPageCount} onChange={setMarketPage} label={t("pluginMarketPager")} />
              </>
            )
          ) : installedFiltered.length === 0 ? (
            <div className="plugin-empty">
              <p className="hint">{installedCount === 0 ? t("pluginEmptyInstalled") : t("pluginNoMatchInstalled")}</p>
              {installedCount === 0 ? (
                <button type="button" className="primary" onClick={() => setView("market")}>
                  {t("pluginBrowseMarket")}
                </button>
              ) : null}
            </div>
          ) : (
            <>
              <div className="plugin-grid">
                {installedSlice.map((p) => {
                  const st = statusOf(p, t);
                  const off = isOfflineItem(p);
                  const labels = p.tools.map((tool) => tool.label).slice(0, 4);
                  return (
                    <button key={p.id} type="button" className="plugin-card" onClick={() => setSelected(p.id)}>
                      <div className="plugin-card-head">
                        <PluginIcon icon={p.icon} name={p.name} />
                        <div className="plugin-card-title">
                          <strong>{p.name}</strong>
                          <span>v{p.version}</span>
                        </div>
                        <span className={`plugin-badge ${st.tone}`}>{st.label}</span>
                      </div>
                      <div className="plugin-card-meta">
                        <OfflineBadge offline={off} />
                        <span className="plugin-meta-muted">{t("pluginToolsCount", String(toolCountOf(p)))}</span>
                      </div>
                      <p className="plugin-card-desc">{p.description}</p>
                      {labels.length ? (
                        <div className="plugin-tool-preview" title={p.tools.map((tool) => tool.label).join(", ")}>
                          {labels.map((lb) => (
                            <span key={lb} className="plugin-tool-chip">
                              {lb}
                            </span>
                          ))}
                          {p.tools.length > labels.length ? <span className="plugin-tool-chip more">+{p.tools.length - labels.length}</span> : null}
                        </div>
                      ) : null}
                      <div className="plugin-card-foot">
                        <span className="plugin-cat">{p.category}</span>
                        <span>
                          {p.source === "upload"
                            ? t("pluginSourceUpload")
                            : p.source === "url"
                              ? t("pluginSourceUrl")
                              : t("pluginSourceMarket")}
                        </span>
                      </div>
                    </button>
                  );
                })}
              </div>
              <Pagination page={safeInstalledPage} pageCount={installedPageCount} onChange={setInstalledPage} label={t("pluginInstalledPager")} />
            </>
          )}
        </>
      )}
    </div>
  );
}

function MarketCard({ item, busy, onOpen, onInstall }: { item: MarketItem; busy: boolean; onOpen: () => void; onInstall: () => void }) {
  const { t } = usePrefs();
  const offline = isOfflineItem(item);
  const count = toolCountOf(item);
  return (
    <div className="plugin-card" role="button" tabIndex={0} onClick={onOpen} onKeyDown={(e) => (e.key === "Enter" ? onOpen() : undefined)}>
      <div className="plugin-card-head">
        <PluginIcon icon={item.icon} name={item.name} />
        <div className="plugin-card-title">
          <strong>{item.name}</strong>
          <span>
            v{item.version}
            {item.author ? ` · ${item.author}` : ""}
          </span>
        </div>
        {item.installed && !item.update_available ? (
          <span className="plugin-badge ok">{t("pluginInstalled")}</span>
        ) : (
          <button
            type="button"
            className="primary plugin-install-btn"
            disabled={busy}
            onClick={(e) => {
              e.stopPropagation();
              onInstall();
            }}
          >
            {busy ? t("pluginInstalling") : item.update_available ? t("pluginUpdate") : t("pluginInstall")}
          </button>
        )}
      </div>
      <div className="plugin-card-meta">
        <OfflineBadge offline={offline} />
        <span className="plugin-meta-muted">{t("pluginToolsCount", String(count))}</span>
      </div>
      <p className="plugin-card-desc">{item.description}</p>
      <div className="plugin-card-foot">
        <span className="plugin-cat">{item.category}</span>
        {item.source === "bundled" ? <span className="plugin-badge built-in">{t("pluginSourceBundled")}</span> : null}
        {(item.tags || []).slice(0, 3).map((tag) => (
          <span key={tag} className="plugin-tag">
            {tag}
          </span>
        ))}
        {item.source === "remote" ? <span title={item.source_url}>{item.source_name}</span> : null}
      </div>
    </div>
  );
}

function UrlInstall({ busy, onSubmit }: { busy: boolean; onSubmit: (url: string) => void }) {
  const { t } = usePrefs();
  const [url, setUrl] = useState("");
  return (
    <form
      className="plugin-modal-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (url.trim()) onSubmit(url.trim());
      }}
    >
      <label className="mcp-field grow">
        <span>{t("pluginPackageUrl")}</span>
        <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/plugins/jira-1.0.0.zip" type="url" required autoFocus />
      </label>
      <div className="plugin-modal-actions">
        <button type="submit" className="primary" disabled={busy || !url.trim()}>
          {busy ? t("pluginInstalling") : t("pluginInstall")}
        </button>
      </div>
    </form>
  );
}


function ToolList({ tools }: { tools: PluginToolSpec[] }) {
  const { t } = usePrefs();
  return (
    <ul className="plugin-tools">
      {tools.map((tool) => (
        <li key={tool.name}>
          <span className={`plugin-badge ${tool.readonly ? "off" : "warn"}`}>
            {tool.readonly ? t("pluginReadonly") : t("pluginWriteOp")}
          </span>
          <div className="plugin-tool-body">
            <strong>{tool.label}</strong>
            <code className="plugin-tool-name">{tool.name}</code>
            <span className="plugin-tool-desc">{tool.description}</span>
          </div>
        </li>
      ))}
    </ul>
  );
}

function PluginDetail({
  id,
  marketItem,
  installedPlugin,
  busy,
  onInstall,
  onBack,
  onChanged,
  onRemoved,
  toast,
}: {
  id: string;
  marketItem?: MarketItem;
  installedPlugin?: InstalledPlugin;
  busy: boolean;
  onInstall: (item: MarketItem) => void;
  onBack: () => void;
  onChanged: () => void;
  onRemoved: () => void;
  toast: (msg: string) => void;
}) {
  const { t } = usePrefs();
  const [plugin, setPlugin] = useState<InstalledPlugin | null>(installedPlugin || null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const isInstalled = Boolean(installedPlugin);

  const refresh = useCallback(async () => {
    try {
      setPlugin(await fetchPlugin(id));
    } catch {
      setPlugin(null);
    }
  }, [id]);

  useEffect(() => {
    if (isInstalled) void refresh();
    else setPlugin(null);
  }, [isInstalled, refresh]);

  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if ((e.data as { type?: string } | null)?.type !== "sidekick-plugin-oauth") return;
      void refresh().then(onChanged);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [refresh, onChanged]);

  const name = plugin?.name || marketItem?.name || id;
  const icon = plugin?.icon || marketItem?.icon;
  const tools = plugin?.tools || marketItem?.tool_specs || [];
  const readme = plugin?.readme || marketItem?.readme || "";
  const st = plugin ? statusOf(plugin, t) : null;
  const offline = isOfflineItem(plugin || marketItem || {});
  const network = plugin?.network || marketItem?.network || [];

  return (
    <div className="plugin-settings settings-pane plugin-detail">
      <button type="button" className="ghost plugin-back" onClick={onBack}>
        {t("pluginBack")}
      </button>
      <div className="plugin-detail-head">
        <PluginIcon icon={icon} name={name} size={56} />
        <div className="plugin-detail-title">
          <h3>
            {name}
            {st ? <span className={`plugin-badge ${st.tone}`}>{st.label}</span> : null}
            <OfflineBadge offline={offline} />
          </h3>
          <p className="hint">
            {packageMetaLine(plugin || marketItem, t)}
            {` · ${t("pluginToolsCount", String(tools.length))}`}
          </p>
          <p>{plugin?.description || marketItem?.description}</p>
        </div>
        <div className="plugin-detail-actions">
          {!plugin && marketItem ? (
            <button type="button" className="primary" disabled={busy} onClick={() => onInstall(marketItem)}>
              {busy ? t("pluginInstalling") : t("pluginInstall")}
            </button>
          ) : null}
          {plugin && marketItem?.update_available ? (
            <button type="button" className="primary" disabled={busy} onClick={() => onInstall(marketItem)}>
              {busy ? t("pluginUpdating") : t("pluginUpdateTo", marketItem.version)}
            </button>
          ) : null}
          {plugin ? (
            <label className="mcp-check plugin-toggle">
              <input
                type="checkbox"
                checked={plugin.enabled}
                onChange={async (e) => {
                  try {
                    setPlugin(await setPluginEnabled(plugin.id, e.target.checked));
                    onChanged();
                  } catch (err) {
                    toast(errText(err));
                  }
                }}
              />
              {t("pluginEnable")}
            </label>
          ) : null}
        </div>
      </div>

      <section className="plugin-section">
        <h4>{t("pluginNetwork")}</h4>
        {offline || network.length === 0 ? (
          <p className="hint plugin-ready-note">{t("pluginNoNetwork")}</p>
        ) : (
          <p className="hint">
            {t("pluginAccess")}
            <code>{network.join(", ")}</code>
          </p>
        )}
      </section>

      {plugin ? (
        <PluginVersionsPanel
          plugin={plugin}
          busy={busy}
          onChanged={(p) => {
            setPlugin(p);
            onChanged();
          }}
          onRemoved={onRemoved}
          toast={toast}
        />
      ) : null}

      {plugin ? <CredentialForm plugin={plugin} onSaved={(p) => { setPlugin(p); onChanged(); }} toast={toast} /> : null}

      {tools.length ? (
        <section className="plugin-section">
          <h4>{t("pluginToolsHeading", String(tools.length))}</h4>
          <ToolList tools={tools} />
        </section>
      ) : null}

      {readme ? (
        <section className="plugin-section plugin-readme">
          <h4>{t("pluginDescriptionHeading")}</h4>
          <MarkdownView content={readme} />
        </section>
      ) : null}

      {plugin ? (
        <section className="plugin-section plugin-danger">
          {confirmRemove ? (
            <>
              <span>{t("pluginUninstallHint")}</span>
              <button
                type="button"
                className="ghost danger"
                onClick={async () => {
                  try {
                    await uninstallPlugin(plugin.id);
                    toast(t("pluginUninstalled", plugin.name));
                    onRemoved();
                  } catch (e) {
                    toast(errText(e));
                  }
                }}
              >
                {t("pluginUninstallConfirm")}
              </button>
              <button type="button" className="ghost" onClick={() => setConfirmRemove(false)}>
                {t("commonCancel")}
              </button>
            </>
          ) : (
            <button type="button" className="ghost danger" onClick={() => setConfirmRemove(true)}>
              {t("pluginUninstall")}
            </button>
          )}
        </section>
      ) : null}
    </div>
  );
}

function PluginVersionsPanel({
  plugin,
  busy,
  onChanged,
  onRemoved,
  toast,
}: {
  plugin: InstalledPlugin;
  busy: boolean;
  onChanged: (p: InstalledPlugin) => void;
  onRemoved: () => void;
  toast: (msg: string) => void;
}) {
  const { t } = usePrefs();
  const versions: PluginVersionRow[] = plugin.versions || [];
  const [switching, setSwitching] = useState("");
  if (versions.length === 0) return null;
  return (
    <section className="plugin-section">
      <h4>{t("pluginVersionsHeading", String(versions.length))}</h4>
      <p className="hint">{t("pluginVersionsHint")}</p>
      <ul className="plugin-version-list">
        {versions.map((row) => (
          <li key={row.version} className={row.active ? "active" : ""}>
            <div className="plugin-version-meta">
              <strong>v{row.version}</strong>
              {row.active ? <span className="plugin-badge ok">{t("pluginVersionActive")}</span> : null}
              <span className="hint">
                {[
                  row.author ? `${t("pluginAuthorLabel")}${row.author}` : "",
                  row.package_source ? `${t("pluginSourceLabel")}${row.package_source}` : "",
                  row.source ? `${t("pluginInstallChannel")}${row.source}` : "",
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </div>
            <div className="plugin-version-actions">
              {!row.active ? (
                <button
                  type="button"
                  className="ghost"
                  disabled={busy || Boolean(switching)}
                  onClick={async () => {
                    setSwitching(row.version);
                    try {
                      const next = await setPluginActiveVersion(plugin.id, row.version);
                      onChanged(next);
                      toast(t("pluginVersionSwitched", row.version));
                    } catch (e) {
                      toast(errText(e));
                    } finally {
                      setSwitching("");
                    }
                  }}
                >
                  {switching === row.version ? t("pluginUpdating") : t("pluginVersionActivate")}
                </button>
              ) : null}
              {versions.length > 1 ? (
                <button
                  type="button"
                  className="ghost danger"
                  disabled={busy || Boolean(switching)}
                  onClick={async () => {
                    if (!window.confirm(t("pluginVersionRemoveConfirm", row.version))) return;
                    setSwitching(row.version);
                    try {
                      const result = await uninstallPluginVersion(plugin.id, row.version);
                      if (result.removed_plugin) {
                        toast(t("pluginUninstalled", plugin.name));
                        onRemoved();
                      } else if (result.plugin) {
                        onChanged(result.plugin);
                        toast(t("pluginVersionRemoved", row.version));
                      }
                    } catch (e) {
                      toast(errText(e));
                    } finally {
                      setSwitching("");
                    }
                  }}
                >
                  {t("pluginVersionRemove")}
                </button>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function CredentialForm({
  plugin,
  onSaved,
  toast,
}: {
  plugin: InstalledPlugin;
  onSaved: (p: InstalledPlugin) => void;
  toast: (msg: string) => void;
}) {
  const { t } = usePrefs();
  const initial = () =>
    Object.fromEntries(
      plugin.credentials.map((c) => [c.name, c.type === "secret" ? "" : plugin.credential_status.fields[c.name]?.value || ""]),
    );
  const [values, setValues] = useState<Record<string, string>>(initial);
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<PluginCallResult | null>(null);
  const [oauthHint, setOauthHint] = useState<string>("");
  const [oauthBusy, setOauthBusy] = useState(false);

  useEffect(() => {
    setValues(initial());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plugin.id, plugin.updated_at]);

  if (!plugin.credentials.length) {
    return (
      <section className="plugin-section">
        <h4>{t("pluginCredentials")}</h4>
        <p className="hint plugin-ready-note">{t("pluginNoCredentials")}</p>
      </section>
    );
  }

  const save = async () => {
    setSaving(true);
    setResult(null);
    try {
      const next = await savePluginCredentials(plugin.id, values);
      onSaved(next);
      setValues(Object.fromEntries(next.credentials.map((c) => [c.name, c.type === "secret" ? "" : next.credential_status.fields[c.name]?.value || ""])));
      if (next.verify) setResult(next.verify);
      else
        toast(
          next.credential_status.configured
            ? t("pluginCredentialsSaved")
            : t("pluginCredentialsPartial"),
        );
    } catch (e) {
      setResult({ ok: false, error: errText(e) });
    } finally {
      setSaving(false);
    }
  };

  const verify = async () => {
    setChecking(true);
    try {
      setResult(await verifyPlugin(plugin.id));
    } catch (e) {
      setResult({ ok: false, error: errText(e) });
    } finally {
      setChecking(false);
    }
  };

  const oauth = plugin.oauth;
  const isGoogleOAuth = Boolean(
    oauth?.scopes?.some((s) => s.includes("googleapis.com") || s.includes("mail.google.com")),
  );

  const startOAuth = async () => {
    setOauthBusy(true);
    setResult(null);
    try {
      // Persist Client ID/Secret before starting; skip verify (token may still be missing).
      const saved = await savePluginCredentials(plugin.id, values, [], false);
      onSaved(saved);
      const idField = saved.credentials.find((c) => c.name.includes("client_id") || c.label.toLowerCase().includes("client id"));
      const secretField = saved.credentials.find((c) => c.name.includes("client_secret") || c.label.toLowerCase().includes("secret"));
      const idOk = idField ? saved.credential_status.fields[idField.name]?.set || Boolean(values[idField.name]?.trim()) : true;
      const secretOk = secretField
        ? saved.credential_status.fields[secretField.name]?.set || Boolean(values[secretField.name]?.trim())
        : true;
      if (oauth && (!idOk || !secretOk)) {
        toast(t("pluginOAuthSaveFirst"));
        return;
      }
      const started = await startPluginOAuth(plugin.id);
      if (started.redirect_uri) {
        setOauthHint(started.redirect_uri);
      }
      window.open(started.url, "sidekick-plugin-oauth", "width=520,height=680");
      toast(t("pluginOAuthOpened"));
      // Desktop uses system browser (no window.opener); poll until refresh token appears.
      const tokenField = oauth?.token_field || "refresh_token";
      const deadline = Date.now() + 180_000;
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 2000));
        try {
          const latest = await fetchPlugin(plugin.id);
          if (latest.credential_status.fields[tokenField]?.set) {
            onSaved(latest);
            setResult(await verifyPlugin(plugin.id));
            toast(t("pluginOAuthDone"));
            break;
          }
        } catch {
          /* keep polling */
        }
      }
    } catch (e) {
      toast(errText(e));
      setResult({ ok: false, error: errText(e) });
    } finally {
      setOauthBusy(false);
    }
  };

  return (
    <section className="plugin-section">
      <h4>{t("pluginCredentials")}</h4>
      <form
        className="plugin-cred-form"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        {plugin.credentials.map((c) => {
          const status = plugin.credential_status.fields[c.name];
          const missing = plugin.credential_status.missing.includes(c.name);
          return (
            <label key={c.name} className="mcp-field">
              <span>
                {c.label}
                {c.required ? <em className="plugin-req"> *</em> : null}
                {c.type === "secret" && status?.set ? (
                  <span className="plugin-saved">{t("pluginSavedBadge")}</span>
                ) : null}
              </span>
              {c.type === "select" ? (
                <select value={values[c.name] || c.default} onChange={(e) => setValues({ ...values, [c.name]: e.target.value })}>
                  {c.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  type={c.type === "secret" ? "password" : "text"}
                  autoComplete="off"
                  value={values[c.name] || ""}
                  placeholder={
                    c.type === "secret" && status?.set
                      ? t("pluginSecretKept")
                      : c.placeholder || c.default
                  }
                  aria-invalid={missing || undefined}
                  onChange={(e) => setValues({ ...values, [c.name]: e.target.value })}
                />
              )}
              {c.help || c.url ? (
                <small className="hint">
                  {c.help}
                  {c.url ? (
                    <>
                      {" "}
                      <a href={c.url} target="_blank" rel="noreferrer">
                        {t("pluginGetIt")}
                      </a>
                    </>
                  ) : null}
                </small>
              ) : null}
            </label>
          );
        })}
        {oauth && isGoogleOAuth ? (
          <div className="plugin-notice" role="note" style={{ marginTop: 8 }}>
            <strong>{t("pluginOAuthGuideTitle")}</strong>
            <p className="hint" style={{ margin: "6px 0 0" }}>{t("pluginOAuthGuideBody")}</p>
            {oauthHint ? (
              <p className="hint" style={{ margin: "6px 0 0", wordBreak: "break-all" }}>
                {t("pluginOAuthRedirect")} <code>{oauthHint}</code>
              </p>
            ) : null}
          </div>
        ) : null}
        <div className="mcp-card-actions">
          <button type="submit" className="primary" disabled={saving || oauthBusy}>
            {saving ? t("commonSaving") : t("pluginSaveVerify")}
          </button>
          {oauth ? (
            <button type="button" className="ghost" disabled={oauthBusy} onClick={() => void startOAuth()}>
              {oauthBusy ? t("pluginOAuthPolling") : oauth.label}
            </button>
          ) : null}
          <button type="button" className="ghost" disabled={checking || oauthBusy || !plugin.credential_status.configured} onClick={() => void verify()}>
            {checking ? t("pluginVerifying") : t("pluginVerify")}
          </button>
        </div>
        {result ? (
          <div className={`plugin-notice ${result.ok ? "ok" : "error"}`} role="status">
            {result.ok
              ? result.result || t("pluginVerified")
              : result.error || t("pluginVerifyFailed")}
          </div>
        ) : null}
      </form>
    </section>
  );
}
