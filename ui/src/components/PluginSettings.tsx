import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  downloadPluginTemplate,
  fetchMarketplace,
  fetchPlugin,
  fetchPluginGuide,
  fetchPlugins,
  installPlugin,
  saveMarketplaceSources,
  savePluginCredentials,
  setPluginEnabled,
  startPluginOAuth,
  uninstallPlugin,
  uploadPlugin,
  verifyPlugin,
  type InstalledPlugin,
  type MarketCatalog,
  type MarketItem,
  type PluginCallResult,
  type PluginToolSpec,
} from "../api";
import { MarkdownView } from "./MarkdownView";

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
  return offline ? (
    <span className="plugin-badge offline" title="无需联网、无需凭据">
      离线可用
    </span>
  ) : (
    <span className="plugin-badge online" title="需要访问外网或配置凭据">
      需联网
    </span>
  );
}

function statusOf(p: InstalledPlugin): { label: string; tone: "ok" | "warn" | "off" } {
  if (!p.enabled) return { label: "已停用", tone: "off" };
  if (!p.credential_status.configured) return { label: "待配置", tone: "warn" };
  return { label: "可用", tone: "ok" };
}

const UPLOAD_TREE = `my-plugin/
  manifest.json   ← 必需
  main.py         ← 必需
  icon.svg        ← 可选
  README.md       ← 可选`;

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
  if (pageCount <= 1) return null;
  return (
    <div className="plugin-pagination" role="navigation" aria-label={label}>
      <button type="button" className="ghost" disabled={page <= 0} onClick={() => onChange(page - 1)}>
        上一页
      </button>
      <span className="hint">
        {page + 1} / {pageCount}
      </span>
      <button type="button" className="ghost" disabled={page >= pageCount - 1} onClick={() => onChange(page + 1)}>
        下一页
      </button>
    </div>
  );
}

export function PluginSettings({ onToast }: Props) {
  const toastRef = useRef(onToast);
  toastRef.current = onToast;
  const toast = (msg: string) => toastRef.current?.(msg);

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
  const [sourcesOpen, setSourcesOpen] = useState(false);
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
      errors.push(`市场：${errText(results[0].reason)}`);
      setCatalog((prev) => prev ?? { items: [], categories: [], sources: [], errors: [] });
    }
    if (results[1].status === "fulfilled") {
      setInstalled(results[1].value);
    } else {
      errors.push(`已安装：${errText(results[1].reason)}`);
      setInstalled((prev) => prev ?? []);
    }
    if (errors.length) setLoadError(errors.join("；"));
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const install = async (target: { id?: string; url?: string }, label: string) => {
    setBusy(target.id || "url");
    try {
      const plugin = await installPlugin(target);
      toast(`已安装「${plugin.name}」${plugin.credential_status.configured ? "" : "，填写凭据后即可使用"}`);
      await reload();
      setSelected(plugin.id);
      return true;
    } catch (e) {
      toast(`安装「${label}」失败：${errText(e)}`);
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
      toast(`已安装「${plugin.name}」${plugin.version}`);
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
      setGuideText(`加载失败：${errText(e)}`);
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
      <header className="settings-pane-intro">
        <h3>插件</h3>
        <p className="hint">第三方应用</p>
      </header>

      <div className="plugin-bar">
        <div className="mcp-transport" role="tablist" aria-label="插件视图">
          <button type="button" role="tab" aria-selected={view === "market"} className={view === "market" ? "active" : ""} onClick={() => setView("market")}>
            插件市场
          </button>
          <button type="button" role="tab" aria-selected={view === "installed"} className={view === "installed" ? "active" : ""} onClick={() => setView("installed")}>
            已安装{installedCount ? ` · ${installedCount}` : ""}
          </button>
        </div>
        <div className="plugin-bar-actions">
          <button
            type="button"
            className="ghost"
            disabled={busy === "template"}
            onClick={() => {
              setBusy("template");
              void downloadPluginTemplate()
                .then(() => toast("已下载 hello-plugin.zip 开发模板"))
                .catch((e) => toast(errText(e)))
                .finally(() => setBusy(null));
            }}
          >
            下载开发模板
          </button>
          <button type="button" className="ghost" onClick={() => void openGuide()}>
            查看开发说明
          </button>
          <button type="button" className="ghost" onClick={() => setUrlOpen((v) => !v)} aria-expanded={urlOpen}>
            从 URL 安装
          </button>
          <button type="button" className="ghost" onClick={() => setSourcesOpen((v) => !v)} aria-expanded={sourcesOpen}>
            市场源
          </button>
        </div>
      </div>

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
          <strong>上传插件包</strong>
          <p>
            将 zip 拖到此处，或
            <button type="button" className="plugin-link-btn" disabled={busy === "upload"} onClick={() => fileRef.current?.click()}>
              {busy === "upload" ? "安装中…" : "选择文件"}
            </button>
            。根目录须含 <code>manifest.json</code>，体积 ≤10MB。离线插件请保持 <code>network</code> / <code>credentials</code> 为空。
          </p>
        </div>
        <pre className="plugin-upload-tree" aria-label="插件包结构示例">
          {UPLOAD_TREE}
        </pre>
      </div>

      {urlOpen ? (
        <UrlInstall
          busy={busy === "url"}
          onSubmit={async (url) => {
            const ok = await install({ url }, url);
            if (ok) setUrlOpen(false);
          }}
        />
      ) : null}
      {sourcesOpen && catalog ? (
        <SourcesEditor
          sources={catalog.sources}
          onSave={async (sources) => {
            try {
              setCatalog(await saveMarketplaceSources(sources));
              toast("市场源已更新");
              setSourcesOpen(false);
            } catch (e) {
              toast(errText(e));
            }
          }}
        />
      ) : null}

      {guideOpen ? (
        <div className="modal-backdrop" onClick={() => setGuideOpen(false)} role="presentation">
          <div
            className="modal plugin-guide-modal"
            role="dialog"
            aria-modal="true"
            aria-label="插件开发说明"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-head">
              <h2>插件开发说明</h2>
              <button type="button" onClick={() => setGuideOpen(false)}>
                关闭
              </button>
            </div>
            <div className="modal-body plugin-guide-body">
              {guideLoading ? <p className="hint">加载中…</p> : <MarkdownView content={guideText} />}
            </div>
          </div>
        </div>
      ) : null}

      {catalog?.errors?.length ? (
        <div className="plugin-notice warn" role="status">
          {catalog.errors.map((e) => (
            <div key={e.source}>
              市场源 <code>{e.source}</code> 暂时不可用：{e.error}
            </div>
          ))}
        </div>
      ) : null}

      {loadError ? (
        <div className="plugin-notice error" role="alert">
          插件列表加载失败：{loadError}
          <button type="button" className="ghost" onClick={() => void reload()}>
            重试
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
              placeholder="搜索插件"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="搜索插件"
            />
            <div className="plugin-chips" role="group" aria-label="联网筛选">
              {(
                [
                  ["all", "全部"],
                  ["offline", "离线"],
                  ["online", "需联网"],
                ] as const
              ).map(([id, label]) => (
                <button key={id} type="button" className={netFilter === id ? "active" : ""} onClick={() => setNetFilter(id)}>
                  {label}
                </button>
              ))}
            </div>
            <div className="plugin-chips" role="group" aria-label="分类">
              {["", ...catalog.categories].map((c) => (
                <button key={c || "all"} type="button" className={category === c ? "active" : ""} onClick={() => setCategory(c)}>
                  {c || "全部分类"}
                </button>
              ))}
            </div>
          </div>

          {view === "market" ? (
            filtered.length === 0 ? (
              <p className="hint plugin-empty">没有匹配的插件。可上传自定义包，或在「市场源」添加索引。</p>
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
                <Pagination page={safeMarketPage} pageCount={marketPageCount} onChange={setMarketPage} label="插件市场分页" />
              </>
            )
          ) : installedFiltered.length === 0 ? (
            <div className="plugin-empty">
              <p className="hint">{installedCount === 0 ? "还没有安装插件。" : "没有匹配的已安装插件。"}</p>
              {installedCount === 0 ? (
                <button type="button" className="primary" onClick={() => setView("market")}>
                  去插件市场看看
                </button>
              ) : null}
            </div>
          ) : (
            <>
              <div className="plugin-grid">
                {installedSlice.map((p) => {
                  const st = statusOf(p);
                  const off = isOfflineItem(p);
                  const labels = p.tools.map((t) => t.label).slice(0, 4);
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
                        <span className="plugin-meta-muted">{toolCountOf(p)} 个工具</span>
                      </div>
                      <p className="plugin-card-desc">{p.description}</p>
                      {labels.length ? (
                        <div className="plugin-tool-preview" title={p.tools.map((t) => t.label).join("、")}>
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
                        <span>{p.source === "upload" ? "本地上传" : p.source === "url" ? "URL 安装" : "市场"}</span>
                      </div>
                    </button>
                  );
                })}
              </div>
              <Pagination page={safeInstalledPage} pageCount={installedPageCount} onChange={setInstalledPage} label="已安装插件分页" />
            </>
          )}
        </>
      )}
    </div>
  );
}

function MarketCard({ item, busy, onOpen, onInstall }: { item: MarketItem; busy: boolean; onOpen: () => void; onInstall: () => void }) {
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
          <span className="plugin-badge ok">已安装</span>
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
            {busy ? "安装中…" : item.update_available ? "更新" : "安装"}
          </button>
        )}
      </div>
      <div className="plugin-card-meta">
        <OfflineBadge offline={offline} />
        <span className="plugin-meta-muted">{count} 个工具</span>
      </div>
      <p className="plugin-card-desc">{item.description}</p>
      <div className="plugin-card-foot">
        <span className="plugin-cat">{item.category}</span>
        {item.source === "bundled" ? <span className="plugin-badge built-in">内置集成</span> : null}
        {(item.tags || []).slice(0, 3).map((t) => (
          <span key={t} className="plugin-tag">
            {t}
          </span>
        ))}
        {item.source === "remote" ? <span title={item.source_url}>{item.source_name}</span> : null}
      </div>
    </div>
  );
}

function UrlInstall({ busy, onSubmit }: { busy: boolean; onSubmit: (url: string) => void }) {
  const [url, setUrl] = useState("");
  return (
    <form
      className="plugin-inline-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (url.trim()) onSubmit(url.trim());
      }}
    >
      <label className="mcp-field grow">
        <span>插件包地址（.zip）</span>
        <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/plugins/jira-1.0.0.zip" type="url" required />
      </label>
      <button type="submit" className="primary" disabled={busy || !url.trim()}>
        {busy ? "安装中…" : "安装"}
      </button>
    </form>
  );
}

function SourcesEditor({ sources, onSave }: { sources: string[]; onSave: (sources: string[]) => void }) {
  const [text, setText] = useState(sources.join("\n"));
  return (
    <div className="plugin-inline-form column">
      <label className="mcp-field">
        <span>市场索引地址（每行一个）</span>
        <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="https://plugins.example.com/index.json" />
      </label>
      <p className="hint">
        索引是一个 JSON：<code>{'{"plugins":[{"id","name","version","description","download_url"}]}'}</code>。内置市场始终可用。
      </p>
      <div className="mcp-card-actions">
        <button
          type="button"
          className="primary"
          onClick={() =>
            onSave(
              text
                .split(/\r?\n/)
                .map((s) => s.trim())
                .filter(Boolean),
            )
          }
        >
          保存市场源
        </button>
      </div>
    </div>
  );
}

function ToolList({ tools }: { tools: PluginToolSpec[] }) {
  return (
    <ul className="plugin-tools">
      {tools.map((t) => (
        <li key={t.name}>
          <span className={`plugin-badge ${t.readonly ? "off" : "warn"}`}>{t.readonly ? "读取" : "写操作"}</span>
          <div className="plugin-tool-body">
            <strong>{t.label}</strong>
            <code className="plugin-tool-name">{t.name}</code>
            <span className="plugin-tool-desc">{t.description}</span>
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
  const st = plugin ? statusOf(plugin) : null;
  const offline = isOfflineItem(plugin || marketItem || {});
  const network = plugin?.network || marketItem?.network || [];

  return (
    <div className="plugin-settings settings-pane plugin-detail">
      <button type="button" className="ghost plugin-back" onClick={onBack}>
        ← 返回
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
            v{plugin?.version || marketItem?.version}
            {(plugin?.author || marketItem?.author) ? ` · ${plugin?.author || marketItem?.author}` : ""}
            {` · ${tools.length} 个工具`}
          </p>
          <p>{plugin?.description || marketItem?.description}</p>
        </div>
        <div className="plugin-detail-actions">
          {!plugin && marketItem ? (
            <button type="button" className="primary" disabled={busy} onClick={() => onInstall(marketItem)}>
              {busy ? "安装中…" : "安装"}
            </button>
          ) : null}
          {plugin && marketItem?.update_available ? (
            <button type="button" className="primary" disabled={busy} onClick={() => onInstall(marketItem)}>
              {busy ? "更新中…" : `更新到 v${marketItem.version}`}
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
              启用
            </label>
          ) : null}
        </div>
      </div>

      <section className="plugin-section">
        <h4>联网</h4>
        {offline || network.length === 0 ? (
          <p className="hint plugin-ready-note">无需联网</p>
        ) : (
          <p className="hint">
            访问：<code>{network.join("、")}</code>
          </p>
        )}
      </section>

      {plugin ? <CredentialForm plugin={plugin} onSaved={(p) => { setPlugin(p); onChanged(); }} toast={toast} /> : null}

      {tools.length ? (
        <section className="plugin-section">
          <h4>工具（{tools.length}）</h4>
          <ToolList tools={tools} />
        </section>
      ) : null}

      {readme ? (
        <section className="plugin-section plugin-readme">
          <h4>说明</h4>
          <MarkdownView content={readme} />
        </section>
      ) : null}

      {plugin ? (
        <section className="plugin-section plugin-danger">
          {confirmRemove ? (
            <>
              <span>卸载会删除插件文件和已保存的凭据。</span>
              <button
                type="button"
                className="ghost danger"
                onClick={async () => {
                  try {
                    await uninstallPlugin(plugin.id);
                    toast(`已卸载「${plugin.name}」`);
                    onRemoved();
                  } catch (e) {
                    toast(errText(e));
                  }
                }}
              >
                确认卸载
              </button>
              <button type="button" className="ghost" onClick={() => setConfirmRemove(false)}>
                取消
              </button>
            </>
          ) : (
            <button type="button" className="ghost danger" onClick={() => setConfirmRemove(true)}>
              卸载插件
            </button>
          )}
        </section>
      ) : null}
    </div>
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
  const initial = () =>
    Object.fromEntries(
      plugin.credentials.map((c) => [c.name, c.type === "secret" ? "" : plugin.credential_status.fields[c.name]?.value || ""]),
    );
  const [values, setValues] = useState<Record<string, string>>(initial);
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<PluginCallResult | null>(null);

  useEffect(() => {
    setValues(initial());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plugin.id, plugin.updated_at]);

  if (!plugin.credentials.length) {
    return (
      <section className="plugin-section">
        <h4>凭据</h4>
        <p className="hint plugin-ready-note">无需凭据，安装即可用</p>
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
      else toast(next.credential_status.configured ? "凭据已保存" : "已保存，还有必填项没有填写");
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
  const startOAuth = async () => {
    try {
      const { url } = await startPluginOAuth(plugin.id);
      window.open(url, "sidekick-plugin-oauth", "width=520,height=680");
      toast("已打开授权页面，完成后会自动保存");
    } catch (e) {
      toast(errText(e));
    }
  };

  return (
    <section className="plugin-section">
      <h4>凭据</h4>
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
                {c.type === "secret" && status?.set ? <span className="plugin-saved">已保存</span> : null}
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
                  placeholder={c.type === "secret" && status?.set ? "已保存，留空则不修改" : c.placeholder || c.default}
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
                        去获取 ↗
                      </a>
                    </>
                  ) : null}
                </small>
              ) : null}
            </label>
          );
        })}
        <div className="mcp-card-actions">
          <button type="submit" className="primary" disabled={saving}>
            {saving ? "保存中…" : "保存并验证"}
          </button>
          {oauth ? (
            <button type="button" className="ghost" onClick={() => void startOAuth()}>
              {oauth.label}
            </button>
          ) : null}
          <button type="button" className="ghost" disabled={checking || !plugin.credential_status.configured} onClick={() => void verify()}>
            {checking ? "验证中…" : "验证"}
          </button>
        </div>
        {result ? (
          <div className={`plugin-notice ${result.ok ? "ok" : "error"}`} role="status">
            {result.ok ? result.result || "验证通过" : result.error || "验证失败"}
          </div>
        ) : null}
      </form>
    </section>
  );
}
