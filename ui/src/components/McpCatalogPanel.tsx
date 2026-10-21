import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchMcpCatalog,
  installMcpCatalog,
  uninstallMcpCatalog,
  type McpCatalogEntry,
} from "../api";

type Props = {
  onToast?: (msg: string) => void;
  onInstalled?: () => void;
  compact?: boolean;
  /** Notifies parent when catalog detail is open so list+detail never stack. */
  onDetailChange?: (open: boolean) => void;
};

const PAGE_SIZE = 9;
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

const PATH_ENTRY_IDS = new Set(["filesystem", "git", "sqlite"]);

function needsConfig(entry: McpCatalogEntry): boolean {
  return PATH_ENTRY_IDS.has(entry.id) || (entry.env_placeholders || []).length > 0;
}

function pathFieldLabel(id: string): string {
  if (id === "filesystem") return "允许访问的目录";
  if (id === "git") return "Git 仓库路径";
  if (id === "sqlite") return "数据库文件路径";
  return "路径";
}

function defaultPath(id: string): string {
  if (id === "sqlite") return "./data.db";
  // Git must not default to "." — Sidekick project root is often not a git repo.
  if (id === "git") return "";
  if (id === "filesystem") return "";
  return "";
}

function pathPlaceholder(id: string): string {
  if (id === "git") return "例如 D:\\Repos\\my-repo";
  if (id === "sqlite") return "./data.db 或绝对路径";
  if (id === "filesystem") return "例如 D:\\workspace";
  return "本机路径";
}

function isBadPath(id: string, path: string): string | null {
  const trimmed = path.trim();
  if (!trimmed) {
    if (id === "git") return "请填写真实的 Git 仓库路径（不能为空或 .）";
    if (id === "filesystem") return "请填写允许访问的目录路径";
    if (id === "sqlite") return "请填写数据库文件路径";
    return "请填写路径";
  }
  if (id === "git" && (trimmed === "." || trimmed === "..")) {
    return "Git 仓库路径不能是 . 或 ..，请填写本机已有仓库的绝对路径";
  }
  if ((id === "filesystem" || id === "sqlite") && (trimmed === "." || trimmed === "..")) {
    return "请填写更具体的路径（不要只用 .）";
  }
  return null;
}

function getPathFromArgs(entry: McpCatalogEntry): string {
  const args = entry.args || [];
  if (entry.id === "git") {
    const idx = args.indexOf("--repository");
    if (idx >= 0 && idx + 1 < args.length) {
      const v = (args[idx + 1] || "").trim();
      return v === "." || v === ".." ? "" : v;
    }
    const last = args[args.length - 1];
    if (last && last !== "mcp-server-git" && last !== "." && last !== "..") return last;
    return defaultPath(entry.id);
  }
  if (entry.id === "sqlite") {
    const idx = args.indexOf("--db-path");
    if (idx >= 0 && idx + 1 < args.length) return args[idx + 1] || defaultPath(entry.id);
    const last = args[args.length - 1];
    if (last && last !== "mcp-server-sqlite" && last !== "-y" && !last.startsWith("@")) {
      return last;
    }
    return defaultPath(entry.id);
  }
  if (entry.id === "filesystem") {
    return args.length ? args[args.length - 1] : defaultPath(entry.id);
  }
  return defaultPath(entry.id);
}

function rebuildArgs(entry: McpCatalogEntry, path: string): string[] | undefined {
  if (!PATH_ENTRY_IDS.has(entry.id)) return undefined;
  const trimmed = path.trim() || defaultPath(entry.id);
  const args = [...(entry.args || [])];
  if (entry.id === "git") {
    const idx = args.indexOf("--repository");
    if (idx >= 0 && idx + 1 < args.length) {
      args[idx + 1] = trimmed;
      return args;
    }
    return ["mcp-server-git", "--repository", trimmed];
  }
  if (entry.id === "sqlite") {
    const idx = args.indexOf("--db-path");
    if (idx >= 0 && idx + 1 < args.length) {
      args[idx + 1] = trimmed;
      return args;
    }
    // Prefer Python uvx package; fall back to last-arg style for legacy npm entries.
    if (args.includes("mcp-server-sqlite") || entry.package === "mcp-server-sqlite") {
      return ["mcp-server-sqlite", "--db-path", trimmed];
    }
    if (args.length === 0) return ["-y", "@executeautomation/database-server", trimmed];
    args[args.length - 1] = trimmed;
    return args;
  }
  if (args.length === 0) return [trimmed];
  args[args.length - 1] = trimmed;
  return args;
}

function envKeyLabel(key: string): string {
  if (key === "BRAVE_API_KEY") return "Brave Search API Key";
  if (key.endsWith("_API_KEY") || key.endsWith("_KEY") || key.endsWith("_TOKEN")) return "API 密钥";
  return key;
}

export function McpCatalogPanel({ onToast, onInstalled, compact, onDetailChange }: Props) {
  const toastRef = useRef(onToast);
  toastRef.current = onToast;
  const toast = (msg: string) => toastRef.current?.(msg);

  const onDetailChangeRef = useRef(onDetailChange);
  onDetailChangeRef.current = onDetailChange;

  const [items, setItems] = useState<McpCatalogEntry[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  /** Entry currently being configured on the detail page (never shown on list). */
  const [configuring, setConfiguring] = useState<McpCatalogEntry | null>(null);
  const [envValues, setEnvValues] = useState<Record<string, string>>({});
  const [pathValue, setPathValue] = useState("");
  const [detailId, setDetailId] = useState<string | null>(null);
  const [page, setPage] = useState(0);

  useEffect(() => {
    onDetailChangeRef.current?.(Boolean(detailId));
  }, [detailId]);

  useEffect(() => {
    return () => {
      onDetailChangeRef.current?.(false);
    };
  }, []);

  const reload = useCallback(async () => {
    setError("");
    try {
      const cat = await fetchMcpCatalog();
      setItems(cat.items);
    } catch (e) {
      setItems([]);
      setError(errText(e));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const list = items || [];
  const pageCount = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const pageItems = useMemo(
    () => list.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE),
    [list, safePage],
  );

  useEffect(() => {
    if (page !== safePage) setPage(safePage);
  }, [page, safePage]);

  const detail = detailId ? list.find((i) => i.id === detailId) || null : null;

  const beginConfigure = (entry: McpCatalogEntry) => {
    setConfiguring(entry);
    setEnvValues(Object.fromEntries((entry.env_placeholders || []).map((k) => [k, ""])));
    setPathValue(PATH_ENTRY_IDS.has(entry.id) ? getPathFromArgs(entry) : "");
  };

  const cancelConfigure = () => {
    setConfiguring(null);
    setEnvValues({});
    setPathValue("");
  };

  const doInstall = async (entry: McpCatalogEntry, env?: Record<string, string>, args?: string[]) => {
    setBusy(entry.id);
    try {
      const body: { id: string; args?: string[]; env?: Record<string, string> } = { id: entry.id };
      if (args) body.args = args;
      if (env && Object.keys(env).length) body.env = env;
      const res = await installMcpCatalog(body);
      if (res.catalog) setItems(res.catalog.items);
      else await reload();
      if (res.status === "already_installed") toast(`「${entry.name}」已在 MCP 配置中`);
      else toast(`已添加「${entry.name}」到 MCP`);
      onInstalled?.();
      cancelConfigure();
    } catch (e) {
      toast(`添加失败：${errText(e)}`);
    } finally {
      setBusy(null);
    }
  };

  /** List 「添加」: open detail + configure when needed; otherwise install. */
  const onListAddClick = (entry: McpCatalogEntry) => {
    if (entry.installed) return;
    if (needsConfig(entry)) {
      setDetailId(entry.id);
      beginConfigure(entry);
      return;
    }
    void doInstall(entry);
  };

  /** Detail 「添加」: enter configure mode or one-click install. */
  const onDetailAddClick = (entry: McpCatalogEntry) => {
    if (entry.installed) return;
    if (needsConfig(entry)) {
      beginConfigure(entry);
      return;
    }
    void doInstall(entry);
  };

  const onConfirmAdd = (entry: McpCatalogEntry) => {
    if (PATH_ENTRY_IDS.has(entry.id)) {
      const bad = isBadPath(entry.id, pathValue);
      if (bad) {
        toast(bad);
        return;
      }
    }
    const args = rebuildArgs(entry, pathValue);
    const env =
      (entry.env_placeholders || []).length > 0 ? envValues : undefined;
    void doInstall(entry, env, args);
  };

  const onUninstall = async (entry: McpCatalogEntry) => {
    setBusy(entry.id);
    try {
      const res = await uninstallMcpCatalog(entry.id);
      if (res.catalog) setItems(res.catalog.items);
      else await reload();
      toast(`已从 MCP 移除「${entry.name}」`);
      onInstalled?.();
    } catch (e) {
      toast(errText(e));
    } finally {
      setBusy(null);
    }
  };

  const goBack = () => {
    setDetailId(null);
    cancelConfigure();
  };

  if (detail) {
    const homepageLabel =
      detail.homepage && /github\.com/i.test(detail.homepage) ? "在 GitHub 打开" : "主页";
    const argsPreview = (detail.args || []).filter(Boolean);
    const isPathBased = PATH_ENTRY_IDS.has(detail.id);
    const pathPreview = isPathBased ? getPathFromArgs(detail) : "";
    const subtitle = [detail.author, detail.license, detail.category].filter(Boolean).join(" · ");
    const isConfiguring = configuring?.id === detail.id;
    const showInstallHintSection = Boolean(detail.install_hint) && !isConfiguring;

    return (
      <div className={`mcp-catalog${compact ? " compact" : ""}`}>
        <button type="button" className="ghost plugin-back" onClick={goBack}>
          ← 返回目录
        </button>
        <div className="mcp-catalog-detail">
          <div className="plugin-detail-head">
            <span className="plugin-icon plugin-icon-fallback mcp-detail-icon" style={{ width: 56, height: 56 }} aria-hidden>
              {detail.name.slice(0, 1).toUpperCase()}
            </span>
            <div className="plugin-detail-title">
              <h3>
                {detail.name}
                <span className="plugin-badge oss">OSS</span>
                {detail.offline_capable ? (
                  <span className="plugin-badge offline">可离线</span>
                ) : (
                  <span className="plugin-badge online">需联网</span>
                )}
                {detail.requires_api_key ? <span className="plugin-badge warn">需密钥</span> : null}
                {detail.installed ? <span className="plugin-badge ok">已添加</span> : null}
              </h3>
              {subtitle ? <p className="hint">{subtitle}</p> : null}
              {detail.description ? <p>{detail.description}</p> : null}
            </div>
            <div className="plugin-detail-actions">
              {detail.installed ? (
                <button
                  type="button"
                  className="ghost danger"
                  disabled={busy === detail.id}
                  onClick={() => void onUninstall(detail)}
                >
                  {busy === detail.id ? "移除中…" : "移除"}
                </button>
              ) : isConfiguring ? (
                <>
                  <button type="button" className="ghost" onClick={cancelConfigure}>
                    取消
                  </button>
                  <button
                    type="button"
                    className="primary"
                    disabled={busy === detail.id}
                    onClick={() => onConfirmAdd(detail)}
                  >
                    {busy === detail.id ? "添加中…" : "确认添加"}
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  className="primary"
                  disabled={busy === detail.id}
                  onClick={() => onDetailAddClick(detail)}
                >
                  {busy === detail.id ? "添加中…" : "添加"}
                </button>
              )}
            </div>
          </div>

          <section className="plugin-section mcp-detail-section">
            <h4>连接信息</h4>
            <dl className="mcp-detail-dl">
              {detail.package ? (
                <div className="mcp-detail-row">
                  <dt>包名</dt>
                  <dd><code className="mcp-pkg">{detail.package}</code></dd>
                </div>
              ) : null}
              {detail.transport ? (
                <div className="mcp-detail-row">
                  <dt>传输</dt>
                  <dd>{detail.transport}</dd>
                </div>
              ) : null}
              {detail.command ? (
                <div className="mcp-detail-row">
                  <dt>启动命令</dt>
                  <dd><code className="mcp-pkg">{detail.command}</code></dd>
                </div>
              ) : null}
              {isPathBased ? (
                <div className="mcp-detail-row">
                  <dt>路径参数</dt>
                  <dd><code className="mcp-pkg">{pathPreview}</code></dd>
                </div>
              ) : null}
              {argsPreview.length ? (
                <div className="mcp-detail-row">
                  <dt>启动参数</dt>
                  <dd><code className="mcp-pkg mcp-pkg-muted">{argsPreview.join(" ")}</code></dd>
                </div>
              ) : null}
              {detail.homepage ? (
                <div className="mcp-detail-row">
                  <dt>主页</dt>
                  <dd>
                    <a href={detail.homepage} target="_blank" rel="noreferrer">
                      {homepageLabel}
                    </a>
                  </dd>
                </div>
              ) : null}
            </dl>
          </section>

          {(detail.tags || []).length ? (
            <section className="plugin-section mcp-detail-section">
              <h4>标签</h4>
              <div className="mcp-detail-tags">
                {detail.tags.map((tag) => (
                  <span key={tag} className="plugin-tag">{tag}</span>
                ))}
              </div>
            </section>
          ) : null}

          {showInstallHintSection ? (
            <section className="plugin-section mcp-detail-section">
              <h4>安装说明</h4>
              <p className="mcp-detail-hint">{detail.install_hint}</p>
            </section>
          ) : null}

          {isConfiguring ? (
            <section className="plugin-section mcp-detail-section mcp-detail-config" aria-label="添加前配置">
              <h4>添加前配置</h4>
              {isPathBased ? (
                <label className="mcp-field">
                  <span>{pathFieldLabel(detail.id)}</span>
                  <input
                    type="text"
                    autoComplete="off"
                    value={pathValue}
                    placeholder={pathPlaceholder(detail.id)}
                    onChange={(e) => setPathValue(e.target.value)}
                  />
                  <small className="hint">
                    {detail.id === "git"
                      ? "必须是本机已有 Git 仓库的绝对路径（不能填 .）"
                      : "本机绝对路径或相对路径"}
                  </small>
                </label>
              ) : null}
              {(detail.env_placeholders || []).map((key) => (
                <label key={key} className="mcp-field">
                  <span>{envKeyLabel(key)}</span>
                  <input
                    type="password"
                    autoComplete="off"
                    value={envValues[key] || ""}
                    placeholder="填入密钥"
                    onChange={(e) => setEnvValues((v) => ({ ...v, [key]: e.target.value }))}
                  />
                </label>
              ))}
              <div className="mcp-detail-config-actions">
                <button type="button" className="ghost" onClick={cancelConfigure}>
                  取消
                </button>
                <button
                  type="button"
                  className="primary"
                  disabled={busy === detail.id}
                  onClick={() => onConfirmAdd(detail)}
                >
                  {busy === detail.id ? "添加中…" : "确认添加"}
                </button>
              </div>
            </section>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <div className={`mcp-catalog${compact ? " compact" : ""}`}>
      {!compact ? <h4 className="mcp-catalog-title">开源目录</h4> : null}

      {error ? (
        <div className="plugin-notice error" role="alert">
          {error}
          <button type="button" className="ghost" onClick={() => void reload()}>
            重试
          </button>
        </div>
      ) : null}

      {!items ? (
        <p className="hint">加载中…</p>
      ) : list.length === 0 ? (
        <p className="hint">目录为空。</p>
      ) : (
        <>
          <div className="mcp-catalog-list" role="list">
            {pageItems.map((item) => (
              <div
                key={item.id}
                className="mcp-catalog-row"
                role="listitem"
                tabIndex={0}
                onClick={() => {
                  setDetailId(item.id);
                  cancelConfigure();
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setDetailId(item.id);
                    cancelConfigure();
                  }
                }}
              >
                <div className="mcp-catalog-row-main">
                  <strong>{item.name}</strong>
                  <div className="mcp-catalog-row-badges">
                    {item.category ? <span className="plugin-meta-muted">{item.category}</span> : null}
                    {item.author ? <span className="plugin-meta-muted">{item.author}</span> : null}
                    {item.license ? <span className="plugin-badge off">{item.license}</span> : null}
                    {item.offline_capable ? (
                      <span className="plugin-badge offline">可离线</span>
                    ) : (
                      <span className="plugin-badge online">需联网</span>
                    )}
                    {item.requires_api_key ? <span className="plugin-badge warn">需密钥</span> : null}
                    {item.installed ? <span className="plugin-badge ok">已添加</span> : null}
                  </div>
                </div>
                <div className="mcp-catalog-row-actions" onClick={(e) => e.stopPropagation()}>
                  <span className="mcp-catalog-row-detail" aria-hidden>详情</span>
                  {item.installed ? (
                    <button
                      type="button"
                      className="ghost danger"
                      disabled={busy === item.id}
                      onClick={() => void onUninstall(item)}
                    >
                      移除
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="primary plugin-install-btn"
                      disabled={busy === item.id}
                      onClick={() => onListAddClick(item)}
                    >
                      {busy === item.id ? "添加中…" : "添加"}
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
          {list.length > PAGE_SIZE ? (
            <div className="mcp-pagination" role="navigation" aria-label="MCP 目录分页">
              <button type="button" className="ghost" disabled={safePage <= 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>
                上一页
              </button>
              <span className="hint">
                {safePage + 1} / {pageCount}
              </span>
              <button
                type="button"
                className="ghost"
                disabled={safePage >= pageCount - 1}
                onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
              >
                下一页
              </button>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
