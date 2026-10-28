import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { fetchMcp, fetchPlugins, type InstalledPlugin, type McpServer } from "../api";
import type { MsgKey } from "../i18n";

export type SessionBinding = {
  mcpServerIds: string[];
  pluginIds: string[];
};

type Props = {
  t: (key: MsgKey, ...args: string[]) => string;
  value: SessionBinding;
  onChange: (next: SessionBinding) => void;
  disabled?: boolean;
};

type PopPos = { bottom: number; left: number; width: number; maxHeight: number };
type BindTab = "mcp" | "plugins";

function BindItemIcon({ icon, name, size = 18 }: { icon?: string; name: string; size?: number }) {
  if (icon) {
    return <img className="session-bind-item-icon" src={icon} alt="" width={size} height={size} />;
  }
  return (
    <span
      className="session-bind-item-icon session-bind-item-icon-fallback"
      style={{ width: size, height: size }}
      aria-hidden
    >
      {(name || "?").slice(0, 1).toUpperCase()}
    </span>
  );
}

export function SessionBindingMenu({ t, value, onChange, disabled }: Props) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<BindTab>("mcp");
  const [servers, setServers] = useState<McpServer[]>([]);
  const [plugins, setPlugins] = useState<InstalledPlugin[]>([]);
  const [loadErr, setLoadErr] = useState("");
  const [popPos, setPopPos] = useState<PopPos | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoadErr("");
    void Promise.all([fetchMcp(), fetchPlugins()])
      .then(([mcp, plugs]) => {
        if (cancelled) return;
        // MCP still requires enable; local plugins: install in Settings is enough.
        setServers((mcp.servers || []).filter((s) => s.enabled));
        setPlugins(plugs || []);
      })
      .catch((e) => {
        if (!cancelled) setLoadErr(String((e as Error)?.message || e));
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (!open) {
      setPopPos(null);
      return;
    }
    const place = () => {
      const el = btnRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      const width = Math.min(340, Math.max(260, window.innerWidth * 0.8));
      const maxHeight = Math.min(400, Math.max(200, window.innerHeight * 0.52));
      const left = Math.min(Math.max(8, r.left), Math.max(8, window.innerWidth - width - 8));
      // Anchor above the composer button (fixed + bottom) so nothing clips it.
      const bottom = Math.max(8, window.innerHeight - r.top + 8);
      setPopPos({ bottom, left, width, maxHeight });
    };
    place();
    const onDoc = (ev: MouseEvent) => {
      const t = ev.target as Node;
      if (rootRef.current?.contains(t) || popRef.current?.contains(t)) return;
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

  const count = value.mcpServerIds.length + value.pluginIds.length;
  const label = useMemo(() => {
    if (count === 0) return t("bindEmpty");
    return t("bindCount", String(count));
  }, [count, t]);

  const toggleMcp = (id: string) => {
    const set = new Set(value.mcpServerIds);
    if (set.has(id)) set.delete(id);
    else set.add(id);
    onChange({ ...value, mcpServerIds: Array.from(set) });
  };

  const togglePlugin = (id: string) => {
    const set = new Set(value.pluginIds);
    if (set.has(id)) set.delete(id);
    else set.add(id);
    onChange({ ...value, pluginIds: Array.from(set) });
  };

  const mcpSelected = value.mcpServerIds.length;
  const pluginSelected = value.pluginIds.length;

  const pop =
    open && popPos
      ? createPortal(
          <div
            ref={popRef}
            className="session-bind-pop session-bind-pop-portal"
            role="dialog"
            aria-label={t("bindTitle")}
            style={{
              bottom: popPos.bottom,
              left: popPos.left,
              width: popPos.width,
              maxHeight: popPos.maxHeight,
            }}
            onMouseDown={(e) => e.stopPropagation()}
          >
            <div className="session-bind-head">
              <strong>{t("bindTitle")}</strong>
              <p>{t("bindHint")}</p>
            </div>

            <div className="session-bind-tabs" role="tablist" aria-label={t("bindTitle")}>
              <button
                type="button"
                role="tab"
                aria-selected={tab === "mcp"}
                className={`session-bind-tab${tab === "mcp" ? " active" : ""}`}
                onClick={() => setTab("mcp")}
              >
                <span>{t("bindTabMcp")}</span>
                {mcpSelected > 0 ? <em className="session-bind-tab-count">{mcpSelected}</em> : null}
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={tab === "plugins"}
                className={`session-bind-tab${tab === "plugins" ? " active" : ""}`}
                onClick={() => setTab("plugins")}
              >
                <span>{t("bindTabPlugins")}</span>
                {pluginSelected > 0 ? <em className="session-bind-tab-count">{pluginSelected}</em> : null}
              </button>
            </div>

            {loadErr ? <div className="session-bind-err">{loadErr}</div> : null}

            <div className="session-bind-body">
              {tab === "mcp" ? (
                <section className="session-bind-section" role="tabpanel">
                  {servers.length === 0 ? (
                    <p className="session-bind-empty-state">{t("bindNoMcp")}</p>
                  ) : (
                    <ul>
                      {servers.map((s) => {
                        const checked = value.mcpServerIds.includes(s.id);
                        const status = s.transport || s.id;
                        return (
                          <li key={s.id}>
                            <label className={checked ? "selected" : undefined}>
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => toggleMcp(s.id)}
                              />
                              <BindItemIcon name={s.name || s.id} />
                              <span className="session-bind-item-text">
                                <span className="session-bind-item-name">{s.name || s.id}</span>
                                <em className="session-bind-item-status">{status}</em>
                              </span>
                            </label>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </section>
              ) : (
                <section className="session-bind-section" role="tabpanel">
                  {plugins.length === 0 ? (
                    <p className="session-bind-empty-state">{t("bindNoPlugins")}</p>
                  ) : (
                    <ul>
                      {plugins.map((p) => {
                        const checked = value.pluginIds.includes(p.id);
                        const status = p.version ? `v${p.version}` : p.id;
                        return (
                          <li key={p.id}>
                            <label className={checked ? "selected" : undefined}>
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => togglePlugin(p.id)}
                              />
                              <BindItemIcon icon={p.icon || undefined} name={p.name || p.id} />
                              <span className="session-bind-item-text">
                                <span className="session-bind-item-name">{p.name || p.id}</span>
                                <em className="session-bind-item-status">{status}</em>
                              </span>
                            </label>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </section>
              )}
            </div>

            <div className="session-bind-actions">
              <button
                type="button"
                className="session-bind-clear"
                onClick={() => onChange({ mcpServerIds: [], pluginIds: [] })}
              >
                {t("bindClear")}
              </button>
              <button type="button" className="session-bind-done" onClick={() => setOpen(false)}>
                {t("bindDone")}
              </button>
            </div>
          </div>,
          document.body,
        )
      : null;

  return (
    <div className="session-bind" ref={rootRef}>
      <button
        ref={btnRef}
        type="button"
        className={`composer-tool label session-bind-btn${count ? " active" : ""}`}
        title={t("bindTitle")}
        disabled={disabled}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="session-bind-icon" aria-hidden>
          ⛓
        </span>
        <span>{label}</span>
      </button>
      {pop}
    </div>
  );
}
