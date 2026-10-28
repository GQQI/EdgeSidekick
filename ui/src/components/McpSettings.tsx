import { useEffect, useRef, useState } from "react";
import {
  fetchMcp,
  fetchMcpTools,
  fetchPrefs,
  saveMcp,
  savePrefs,
  testMcpServer,
  type McpServer,
  type McpSetup,
  type McpToolInfo,
} from "../api";
import { usePrefs } from "../prefs";
import type { MsgKey } from "../i18n";
import { McpCatalogPanel } from "./McpCatalogPanel";

type T = (key: MsgKey, ...args: string[]) => string;

type Props = {
  onToast?: (msg: string) => void;
};

type Transport = "stdio" | "http" | "sse";

function slugify(raw: string): string {
  return (raw || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48) || `mcp_${Date.now().toString(36)}`;
}

function emptyServer(transport: Transport = "stdio"): McpServer {
  const id = `mcp_${Date.now().toString(36)}`;
  if (transport === "stdio") {
    return {
      id,
      name: "filesystem",
      transport: "stdio",
      command: "npx",
      args: ["-y", "@modelcontextprotocol/server-filesystem", "."],
      env: {},
      url: "",
      headers: {},
      enabled: true,
    };
  }
  return {
    id,
    name: transport === "sse" ? "remote-sse" : "remote-http",
    transport,
    command: "",
    args: [],
    env: {},
    url: transport === "sse" ? "https://example.com/sse" : "https://example.com/mcp",
    headers: {},
    enabled: true,
  };
}

function kvToText(map?: Record<string, string>): string {
  return Object.entries(map || {})
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
}

function textToKv(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of (text || "").split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t.startsWith("#")) continue;
    const i = t.indexOf("=");
    if (i <= 0) continue;
    out[t.slice(0, i).trim()] = t.slice(i + 1).trim();
  }
  return out;
}

function argsToText(args?: string[]): string {
  return (args || []).join("\n");
}

function textToArgs(text: string): string[] {
  return (text || "")
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function transportBadge(transport: Transport, t: T): string {
  if (transport === "stdio") return t("mcpLocalBadge");
  if (transport === "sse") return "SSE";
  return "HTTP";
}

function serverOneLiner(s: McpServer, t: T): string {
  const transport = (s.transport || "stdio") as Transport;
  if (transport === "stdio") {
    const cmd = (s.command || "").trim();
    const args = (s.args || []).join(" ").trim();
    const line = [cmd, args].filter(Boolean).join(" ");
    return line || t("mcpNoCommand");
  }
  return (s.url || "").trim() || t("mcpNoUrl");
}

export function McpSettings({ onToast }: Props) {
  const { t } = usePrefs();
  const [setup, setSetup] = useState<McpSetup | null>(null);
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{
    id: string;
    ok: boolean;
    message: string;
  } | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [catalogDetailOpen, setCatalogDetailOpen] = useState(false);
  const [mcpInChat, setMcpInChat] = useState(true);
  const [bindingBusy, setBindingBusy] = useState(false);
  const [toolPreview, setToolPreview] = useState<McpToolInfo[] | null>(null);

  const reload = async () => {
    setLoadError("");
    const data = await fetchMcp();
    setSetup(data);
    try {
      const prefs = await fetchPrefs();
      if (typeof prefs.mcp_in_chat === "boolean") setMcpInChat(prefs.mcp_in_chat);
    } catch {
      /* prefs optional */
    }
    try {
      const tools = await fetchMcpTools();
      setToolPreview(tools.tools || []);
    } catch {
      setToolPreview([]);
    }
  };

  // Load once on mount. `onToast` is an inline callback that changes identity
  // on every parent render — keying the effect on it re-fetched MCP on each
  // parent re-render (e.g. the shell-job poll), spinning the API forever.
  const onToastRef = useRef(onToast);
  onToastRef.current = onToast;

  useEffect(() => {
    void reload().catch((e) => {
      const msg = e instanceof Error ? e.message : String(e);
      // Leave loading state: empty setup so the pane is usable / retryable.
      setSetup({ version: 2, servers: [] } as McpSetup);
      setLoadError(msg || t("mcpLoadFailShort"));
      onToastRef.current?.(msg);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!setup) {
    return <p className="hint">{t("mcpLoading")}</p>;
  }

  if (loadError && setup.servers.length === 0) {
    return (
      <div className="mcp-settings settings-pane">
        <div className="plugin-notice error" role="alert">
          {t("mcpLoadFail", loadError)}
          <button
            type="button"
            className="ghost"
            onClick={() =>
              void reload().catch((e) => {
                const msg = e instanceof Error ? e.message : String(e);
                setSetup({ version: 2, servers: [] } as McpSetup);
                setLoadError(msg || t("mcpLoadFailShort"));
              })
            }
          >
            {t("commonRetry")}
          </button>
        </div>
      </div>
    );
  }

  const selectedIdx = selectedId
    ? setup.servers.findIndex((s) => s.id === selectedId)
    : -1;
  const selected =
    selectedIdx >= 0 ? setup.servers[selectedIdx] : null;

  const updateServer = (idx: number, patch: Partial<McpServer>) => {
    setSetup({
      ...setup,
      servers: setup.servers.map((s, i) => (i === idx ? { ...s, ...patch } : s)),
    });
    if (patch.id && selectedId && setup.servers[idx]?.id === selectedId) {
      setSelectedId(patch.id);
    }
  };

  const setTransport = (idx: number, transport: Transport) => {
    const cur = setup.servers[idx];
    if (!cur) return;
    if (transport === "stdio") {
      updateServer(idx, {
        transport,
        command: cur.command || "npx",
        args: cur.args?.length
          ? cur.args
          : ["-y", "@modelcontextprotocol/server-filesystem", "."],
        url: "",
      });
    } else {
      updateServer(idx, {
        transport,
        command: "",
        args: [],
        url: cur.url || (transport === "sse" ? "https://example.com/sse" : "https://example.com/mcp"),
      });
    }
  };

  const save = async () => {
    setBusy(true);
    try {
      const normalized: McpSetup = {
        ...setup,
        servers: setup.servers.map((s) => ({
          ...s,
          id: slugify(s.id || s.name),
          transport: (s.transport || "stdio") as Transport,
        })),
      };
      const next = await saveMcp(normalized);
      setSetup(next);
      if (selectedId) {
        const still = next.servers.some((s) => s.id === selectedId);
        if (!still) {
          // Id may have been slugified on save — try match by previous name/index
          const prev = setup.servers.find((s) => s.id === selectedId);
          const match = prev
            ? next.servers.find((s) => s.name === prev.name) ||
              next.servers.find((s) => s.id === slugify(prev.id || prev.name))
            : null;
          setSelectedId(match?.id ?? null);
        }
      }
      onToast?.(t("mcpSaved"));
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const runTest = async (s: McpServer) => {
    setTesting(s.id);
    setTestResult(null);
    try {
      const res = await testMcpServer(s);
      if (res.ok) {
        const message = t("mcpTestResultOk", String(res.tool_count ?? 0));
        setTestResult({ id: s.id, ok: true, message });
        onToast?.(t("mcpConnected", String(res.tool_count ?? 0)));
      } else {
        const message = res.error || t("mcpConnectFailed");
        setTestResult({ id: s.id, ok: false, message });
        onToast?.(message);
      }
      await reload().catch(() => undefined);
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setTestResult({ id: s.id, ok: false, message });
      onToast?.(message);
    } finally {
      setTesting(null);
    }
  };

  const addServer = (transport: Transport) => {
    const s = emptyServer(transport);
    setSetup({ ...setup, servers: [...setup.servers, s] });
    setSelectedId(s.id);
  };

  const removeSelected = () => {
    if (selectedIdx < 0) return;
    setSetup({
      ...setup,
      servers: setup.servers.filter((_, i) => i !== selectedIdx),
    });
    setSelectedId(null);
  };

  const renderToolbar = (opts?: { detail?: boolean; server?: McpServer }) => (
    <div className="mcp-toolbar">
      {!opts?.detail ? (
        <>
          <button type="button" className="ghost" onClick={() => addServer("stdio")}>
            {t("mcpAddLocal")}
          </button>
          <button type="button" className="ghost" onClick={() => addServer("http")}>
            {t("mcpAddRemote")}
          </button>
        </>
      ) : null}
      <button type="button" className="primary" disabled={busy} onClick={() => void save()}>
        {busy ? t("commonSaving") : t("commonSave")}
      </button>
      {opts?.detail && opts.server ? (
        <>
          <button
            type="button"
            className="ghost"
            disabled={testing === opts.server.id}
            onClick={() => void runTest(opts.server!)}
          >
            {testing === opts.server.id ? t("mcpTesting") : t("mcpTest")}
          </button>
          <button type="button" className="ghost danger" onClick={removeSelected}>
            {t("mcpDelete")}
          </button>
        </>
      ) : null}
    </div>
  );

  // Configured detail: detail-only (no catalog, no list, no intro).
  if (selected) {
    return (
      <div className="mcp-settings settings-pane">
        <div className="mcp-configured-detail">
          <button
            type="button"
            className="ghost plugin-back"
            onClick={() => {
              setSelectedId(null);
              setTestResult(null);
            }}
          >
            {t("mcpBackToList")}
          </button>

          <div className="mcp-card">
            {selected.ok === false || (testResult && testResult.id === selected.id && !testResult.ok) ? (
              <div className="mcp-test-result fail" role="alert">
                {(testResult && testResult.id === selected.id
                  ? testResult.message
                  : selected.error) || t("mcpHealthBad")}
              </div>
            ) : null}
            {testResult && testResult.id === selected.id && testResult.ok ? (
              <div className="mcp-test-result ok" role="status">
                {testResult.message}
              </div>
            ) : null}
            {testing === selected.id ? (
              <p className="mcp-test-hint">{t("mcpTestHint")}</p>
            ) : null}
            <div className="mcp-card-head">
              <label className="mcp-field grow">
                <span>{t("mcpName")}</span>
                <input
                  value={selected.name}
                  placeholder="filesystem"
                  onChange={(e) => {
                    const name = e.target.value;
                    const nextId = slugify(name || selected.id);
                    updateServer(selectedIdx, {
                      name,
                      id: nextId,
                    });
                    setSelectedId(nextId);
                  }}
                />
              </label>
              <label className="mcp-check">
                <input
                  type="checkbox"
                  checked={selected.enabled}
                  onChange={(e) =>
                    updateServer(selectedIdx, { enabled: e.target.checked })
                  }
                />
                {t("mcpEnable")}
              </label>
            </div>

            <div className="mcp-transport" role="group" aria-label={t("mcpTransportLabel")}>
              {(
                [
                  ["stdio", t("mcpStdio")],
                  ["http", t("mcpHttp")],
                  ["sse", t("mcpSse")],
                ] as const
              ).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  className={
                    ((selected.transport || "stdio") as Transport) === id
                      ? "active"
                      : ""
                  }
                  onClick={() => setTransport(selectedIdx, id)}
                >
                  {label}
                </button>
              ))}
            </div>

            {((selected.transport || "stdio") as Transport) === "stdio" ? (
              <>
                <label className="mcp-field">
                  <span>{t("mcpCommandField")}</span>
                  <input
                    value={selected.command || ""}
                    placeholder="npx / node / python / uvx"
                    onChange={(e) =>
                      updateServer(selectedIdx, { command: e.target.value })
                    }
                  />
                </label>
                <label className="mcp-field">
                  <span>{t("mcpArgsField")}</span>
                  <textarea
                    rows={3}
                    value={argsToText(selected.args)}
                    placeholder={"-y\n@modelcontextprotocol/server-filesystem\n."}
                    onChange={(e) =>
                      updateServer(selectedIdx, {
                        args: textToArgs(e.target.value),
                      })
                    }
                  />
                </label>
                <label className="mcp-field">
                  <span>{t("mcpEnvField")}</span>
                  <textarea
                    rows={2}
                    value={kvToText(selected.env)}
                    placeholder="API_KEY=sk-..."
                    onChange={(e) =>
                      updateServer(selectedIdx, {
                        env: textToKv(e.target.value),
                      })
                    }
                  />
                </label>
              </>
            ) : (
              <>
                <label className="mcp-field">
                  <span>URL</span>
                  <input
                    value={selected.url || ""}
                    placeholder={
                      ((selected.transport || "http") as Transport) === "sse"
                        ? "https://mcp.example.com/sse"
                        : "https://mcp.example.com/mcp"
                    }
                    onChange={(e) =>
                      updateServer(selectedIdx, { url: e.target.value })
                    }
                  />
                </label>
                <label className="mcp-field">
                  <span>{t("mcpHeadersField")}</span>
                  <textarea
                    rows={2}
                    value={kvToText(selected.headers)}
                    placeholder="Authorization=Bearer ..."
                    onChange={(e) =>
                      updateServer(selectedIdx, {
                        headers: textToKv(e.target.value),
                      })
                    }
                  />
                </label>
              </>
            )}
          </div>

          {renderToolbar({ detail: true, server: selected })}
        </div>
      </div>
    );
  }

  // Home or catalog detail: keep one McpCatalogPanel mounted so detailId survives.
  // When catalogDetailOpen, panel already returns detail-only UI — hide intro + 已配置.
  return (
    <div className="mcp-settings settings-pane">
      {!catalogDetailOpen ? (
        <header className="settings-pane-intro">
          <h3>MCP</h3>
          <p className="hint">{t("mcpIntro")}</p>
          <label className="mcp-check mcp-chat-bind">
            <input
              type="checkbox"
              checked={mcpInChat}
              disabled={bindingBusy}
              onChange={(e) => {
                const on = e.target.checked;
                setMcpInChat(on);
                setBindingBusy(true);
                void savePrefs({ mcp_in_chat: on })
                  .then(() => onToast?.(on ? t("mcpInChatOn") : t("mcpInChatOff")))
                  .catch((err) =>
                    onToast?.(err instanceof Error ? err.message : String(err)),
                  )
                  .finally(() => setBindingBusy(false));
              }}
            />
            {t("mcpInChat")}
          </label>
          <p className="hint">{t("mcpInChatHint")}</p>
          {toolPreview && toolPreview.length > 0 ? (
            <p className="hint">
              {t("mcpToolsReady", String(toolPreview.length))}
            </p>
          ) : null}
        </header>
      ) : null}

      <McpCatalogPanel
        compact
        onToast={onToast}
        onInstalled={() => void reload().catch(() => undefined)}
        onDetailChange={setCatalogDetailOpen}
      />

      {!catalogDetailOpen ? (
        <section className="mcp-configured" aria-label={t("mcpConfigured")}>
          <h4 className="mcp-configured-title">{t("mcpConfigured")}</h4>
          <div className="mcp-list" role="list">
            {setup.servers.length === 0 && (
              <p className="hint">{t("mcpNoServers")}</p>
            )}
            {setup.servers.map((s) => {
              const transport = (s.transport || "stdio") as Transport;
              return (
                <div
                  key={s.id}
                  className="mcp-list-row"
                  role="listitem"
                  tabIndex={0}
                  onClick={() => setSelectedId(s.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setSelectedId(s.id);
                    }
                  }}
                >
                  <div className="mcp-list-row-main">
                    <strong>{s.name || s.id}</strong>
                    <div className="mcp-list-row-badges">
                      <span className="plugin-badge offline">
                        {transportBadge(transport, t)}
                      </span>
                      {s.enabled ? (
                        <span className="plugin-badge ok">{t("mcpEnabledBadge")}</span>
                      ) : (
                        <span className="plugin-badge off">{t("mcpDisabledBadge")}</span>
                      )}
                      {s.enabled && s.ok === false ? (
                        <span className="plugin-badge off" title={s.error || undefined}>
                          {t("mcpHealthBad")}
                        </span>
                      ) : s.enabled && s.ok !== false ? (
                        <span className="plugin-badge ok">{t("mcpHealthOk")}</span>
                      ) : null}
                      <span className="mcp-list-row-meta" title={serverOneLiner(s, t)}>
                        {serverOneLiner(s, t)}
                      </span>
                    </div>
                  </div>
                  <div className="mcp-list-row-actions">
                    <span className="mcp-list-row-detail" aria-hidden>
                      {t("commonDetails")}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
          {renderToolbar()}
        </section>
      ) : null}
    </div>
  );
}
