import { useEffect, useRef, useState } from "react";
import {
  fetchMcp,
  saveMcp,
  testMcpServer,
  type McpServer,
  type McpSetup,
} from "../api";

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

export function McpSettings({ onToast }: Props) {
  const [setup, setSetup] = useState<McpSetup | null>(null);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);

  const reload = async () => {
    const data = await fetchMcp();
    setSetup(data);
  };

  // Load once on mount. `onToast` is an inline callback that changes identity
  // on every parent render — keying the effect on it re-fetched MCP on each
  // parent re-render (e.g. the shell-job poll), spinning the API forever.
  const onToastRef = useRef(onToast);
  onToastRef.current = onToast;

  useEffect(() => {
    void reload().catch((e) =>
      onToastRef.current?.(e instanceof Error ? e.message : String(e)),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!setup) {
    return <p className="hint">加载 MCP 配置…</p>;
  }

  const updateServer = (idx: number, patch: Partial<McpServer>) => {
    setSetup({
      ...setup,
      servers: setup.servers.map((s, i) => (i === idx ? { ...s, ...patch } : s)),
    });
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
      onToast?.("MCP 已保存（新会话生效）");
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const runTest = async (s: McpServer) => {
    setTesting(s.id);
    try {
      const res = await testMcpServer(s);
      if (res.ok) onToast?.(`连接成功 · ${res.tool_count ?? 0} 个工具`);
      else onToast?.(res.error || "连接失败");
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : String(e));
    } finally {
      setTesting(null);
    }
  };

  return (
    <div className="mcp-settings settings-pane">
      <header className="settings-pane-intro">
        <h3>MCP</h3>
      </header>

      <div className="mcp-list">
        {setup.servers.length === 0 && (
          <p className="hint">还没有 MCP Server。先添加本地进程或远程 URL。</p>
        )}
        {setup.servers.map((s, idx) => {
          const transport = (s.transport || "stdio") as Transport;
          const isLocal = transport === "stdio";
          return (
            <div key={s.id} className="mcp-card">
              <div className="mcp-card-head">
                <label className="mcp-field grow">
                  <span>名称</span>
                  <input
                    value={s.name}
                    placeholder="filesystem"
                    onChange={(e) => {
                      const name = e.target.value;
                      updateServer(idx, {
                        name,
                        id: slugify(name || s.id),
                      });
                    }}
                  />
                </label>
                <label className="mcp-check">
                  <input
                    type="checkbox"
                    checked={s.enabled}
                    onChange={(e) => updateServer(idx, { enabled: e.target.checked })}
                  />
                  启用
                </label>
              </div>

              <div className="mcp-transport" role="group" aria-label="连接方式">
                {(
                  [
                    ["stdio", "本地进程"],
                    ["http", "远程 HTTP"],
                    ["sse", "远程 SSE"],
                  ] as const
                ).map(([id, label]) => (
                  <button
                    key={id}
                    type="button"
                    className={transport === id ? "active" : ""}
                    onClick={() => setTransport(idx, id)}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {isLocal ? (
                <>
                  <label className="mcp-field">
                    <span>启动命令</span>
                    <input
                      value={s.command || ""}
                      placeholder="npx / node / python / uvx"
                      onChange={(e) => updateServer(idx, { command: e.target.value })}
                    />
                  </label>
                  <label className="mcp-field">
                    <span>参数（每行一条）</span>
                    <textarea
                      rows={3}
                      value={argsToText(s.args)}
                      placeholder={"-y\n@modelcontextprotocol/server-filesystem\n."}
                      onChange={(e) => updateServer(idx, { args: textToArgs(e.target.value) })}
                    />
                  </label>
                  <label className="mcp-field">
                    <span>环境变量（KEY=value，每行一条）</span>
                    <textarea
                      rows={2}
                      value={kvToText(s.env)}
                      placeholder="API_KEY=sk-..."
                      onChange={(e) => updateServer(idx, { env: textToKv(e.target.value) })}
                    />
                  </label>
                </>
              ) : (
                <>
                  <label className="mcp-field">
                    <span>URL</span>
                    <input
                      value={s.url || ""}
                      placeholder={
                        transport === "sse"
                          ? "https://mcp.example.com/sse"
                          : "https://mcp.example.com/mcp"
                      }
                      onChange={(e) => updateServer(idx, { url: e.target.value })}
                    />
                  </label>
                  <label className="mcp-field">
                    <span>Headers（KEY=value，每行一条）</span>
                    <textarea
                      rows={2}
                      value={kvToText(s.headers)}
                      placeholder="Authorization=Bearer ..."
                      onChange={(e) =>
                        updateServer(idx, { headers: textToKv(e.target.value) })
                      }
                    />
                  </label>
                </>
              )}

              <div className="mcp-card-actions">
                <button
                  type="button"
                  className="ghost"
                  disabled={testing === s.id}
                  onClick={() => void runTest(s)}
                >
                  {testing === s.id ? "测试中…" : "测试"}
                </button>
                <button
                  type="button"
                  className="ghost danger"
                  onClick={() =>
                    setSetup({
                      ...setup,
                      servers: setup.servers.filter((_, i) => i !== idx),
                    })
                  }
                >
                  删除
                </button>
              </div>
            </div>
          );
        })}
      </div>

      <div className="mcp-toolbar">
        <button
          type="button"
          className="ghost"
          onClick={() => setSetup({ ...setup, servers: [...setup.servers, emptyServer("stdio")] })}
        >
          添加本地
        </button>
        <button
          type="button"
          className="ghost"
          onClick={() => setSetup({ ...setup, servers: [...setup.servers, emptyServer("http")] })}
        >
          添加远程
        </button>
        <button type="button" className="primary" disabled={busy} onClick={() => void save()}>
          {busy ? "保存中…" : "保存"}
        </button>
      </div>
    </div>
  );
}
