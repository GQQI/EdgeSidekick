import { useEffect, useRef, useState } from "react";
import { McpSettings } from "./McpSettings";
import { PluginSettings } from "./PluginSettings";
import { SkillSettings } from "./SkillSettings";
import { AccountPane } from "./AccountPane";
import { ModelSettings } from "./ModelSettings";
import { IconMoon, IconSun } from "./icons";
import type { LiveLine, SettingsTab, SubNode } from "../types/chat";
import type { ModelSetup } from "../types/modelSetup";
import type { AccountProfile, SessionItem, WorkspaceItem } from "../api";
import type { Density, Locale, MsgKey, Theme } from "../i18n";
import type { UiMode } from "../uiMode";
import { IconCheck, IconChat, IconFolder, IconTrash } from "./icons";

const APP_VERSION = "0.3.1";

export type SettingsModalProps = {
  t: (key: MsgKey, ...args: string[]) => string;
  locale: Locale;
  theme: Theme;
  density: Density;
  setTheme: (theme: Theme) => void;
  setLocale: (locale: Locale) => void;
  setDensity: (density: Density) => void;
  uiMode: UiMode;
  setUiMode: (mode: UiMode) => void;
  settingsTab: SettingsTab;
  setSettingsTab: (tab: SettingsTab) => void;
  onClose: () => void;
  activeWs: { path: string; name: string } | null;
  workspaces: WorkspaceItem[];
  wsBusy: boolean;
  onBrowseWorkspace: () => void;
  onSwitchWorkspace: (path: string) => void;
  onForgetWorkspace?: (path: string) => void;
  sessions?: SessionItem[];
  model: ModelSetup | null;
  modelSaving: boolean;
  onModelChange: (next: ModelSetup) => void;
  onModelSave: (next?: ModelSetup, opts?: { restartChat?: boolean }) => void;
  subs: SubNode[];
  live: LiveLine[];
  accountUser?: AccountProfile | null;
  onAccountChange?: (user: AccountProfile) => void;
  onLogout?: () => void;
  onToast?: (msg: string) => void;
  onSkillsChanged?: () => void;
  autoApproveTools?: boolean;
  autoApproveBusy?: boolean;
  onToggleAutoApprove?: (enabled: boolean) => void;
  /** Open a sub-agent in the main detail pane (settings should close). */
  onOpenSubagent?: (sub: SubNode) => void;
};

export function SettingsModal({
  t,
  locale,
  theme,
  density,
  setTheme,
  setLocale,
  setDensity,
  uiMode,
  setUiMode,
  settingsTab,
  setSettingsTab,
  onClose,
  activeWs,
  workspaces,
  wsBusy,
  onBrowseWorkspace,
  onSwitchWorkspace,
  onForgetWorkspace,
  sessions = [],
  model,
  modelSaving,
  onModelChange,
  onModelSave,
  subs,
  live,
  accountUser,
  onAccountChange,
  onLogout,
  onToast,
  onSkillsChanged,
  autoApproveTools = false,
  autoApproveBusy = false,
  onToggleAutoApprove,
  onOpenSubagent,
}: SettingsModalProps) {
  const logEndRef = useRef<HTMLLIElement | null>(null);
  const [confirmAllowAll, setConfirmAllowAll] = useState(false);
  const [selectedSubId, setSelectedSubId] = useState<string | null>(null);
  const switchOn = autoApproveTools || confirmAllowAll;
  const flatSubs = flattenSubNodes(subs);
  const selectedSub = flatSubs.find((s) => s.id === selectedSubId) || null;

  useEffect(() => {
    if (settingsTab !== "runtime") {
      setSelectedSubId(null);
      return;
    }
    logEndRef.current?.scrollIntoView({ block: "nearest" });
  }, [settingsTab, live.length]);

  useEffect(() => {
    if (!selectedSubId) return;
    if (!flatSubs.some((s) => s.id === selectedSubId)) setSelectedSubId(null);
  }, [flatSubs, selectedSubId]);

  useEffect(() => {
    if (settingsTab !== "safety" || autoApproveTools) setConfirmAllowAll(false);
  }, [settingsTab, autoApproveTools]);

  const closeAndSave = () => {
    // Model edits are deliberately staged in React state.  This is the only
    // implicit commit point; role pickers no longer write on every click.
    if (model) void onModelSave(model, { restartChat: false });
    onClose();
  };

  return (
    <div
      className="modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) closeAndSave();
      }}
    >
      <div className="modal settings-modal" role="dialog" aria-modal="true" aria-label={t("settings")}>
        <div className="modal-head">
          <h2>{t("settings")}</h2>
          <div className="modal-head-actions">
            <button
              type="button"
              className="theme-toggle"
              title={theme === "dark" ? t("themeLight") : t("themeDark")}
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            >
              {theme === "dark" ? <IconSun /> : <IconMoon />}
            </button>
            <button type="button" className="icon-btn" onClick={closeAndSave}>
              {t("close")}
            </button>
          </div>
        </div>
        <div className="modal-tabs">
          {(
            [
              ["workspace", t("tabWorkspace")],
              ["safety", t("tabSafety")],
              ["model", t("tabModel")],
              ["skills", t("tabSkills")],
              ["plugins", t("tabPlugins")],
              ["mcp", "MCP"],
              ["appearance", t("tabAppearance")],
              ["runtime", t("tabRuntime")],
              ["account", t("tabAccount")],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={settingsTab === id ? "active" : ""}
              onClick={() => setSettingsTab(id)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="modal-body">
          {settingsTab === "workspace" && (
            <div className="settings-pane ws-settings-pane">
              <h3>{t("workspaceTitle")}</h3>
              <div className="ws-current-card">
                <span className="ws-current-icon">
                  <IconFolder size={18} />
                </span>
                <div className="ws-current-info">
                  <strong>{activeWs?.name || t("workspaceNone")}</strong>
                  <span title={activeWs?.path || ""}>{activeWs?.path || t("workspaceNone")}</span>
                </div>
                <em className="ws-default-badge">{t("wsIsDefault")}</em>
              </div>
              <button
                type="button"
                className="primary ws-browse-btn"
                disabled={wsBusy}
                onClick={() => void onBrowseWorkspace()}
              >
                <IconFolder size={14} />
                {wsBusy ? t("browsing") : t("openFolder")}
              </button>

              {workspaces.length > 0 && (
                <>
                  <h3 className="settings-subhead">{t("recentFolders")}</h3>
                  <ul className="ws-open-list">
                    {workspaces.map((w) => {
                      const isDefault = activeWs?.path === w.path;
                      const runningCount = sessions.filter(
                        (s) => s.workspace === w.path && s.busy,
                      ).length;
                      return (
                        <li key={w.path} className="ws-row">
                          <span className="ws-row-icon">
                            <IconFolder size={14} />
                          </span>
                          <div className="ws-row-info">
                            <strong>
                              {w.name}
                              {isDefault && <em className="ws-default-badge">{t("wsIsDefault")}</em>}
                              {runningCount > 0 && (
                                <em className="ws-running-badge">
                                  <IconChat size={10} />
                                  {t("wsRunningCount", String(runningCount))}
                                </em>
                              )}
                            </strong>
                            <span title={w.path}>{w.path}</span>
                          </div>
                          <div className="ws-row-actions">
                            <button
                              type="button"
                              className="icon-btn"
                              title={isDefault ? t("wsIsDefault") : t("wsSetDefault")}
                              disabled={isDefault || wsBusy}
                              onClick={() => void onSwitchWorkspace(w.path)}
                            >
                              <IconCheck size={14} />
                            </button>
                            {onForgetWorkspace && (
                              <button
                                type="button"
                                className="icon-btn danger"
                                title={t("wsRemove")}
                                disabled={wsBusy}
                                onClick={() => onForgetWorkspace(w.path)}
                              >
                                <IconTrash size={14} />
                              </button>
                            )}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                </>
              )}
            </div>
          )}

          {settingsTab === "safety" && (
            <div className="settings-pane settings-pane-fill appearance-pane">
              <header className="settings-pane-intro">
                <h3>{t("safetyTitle")}</h3>
                <p className="hint">{t("safetyHint")}</p>
              </header>
              <div className="settings-card-list">
                <div className="appearance-row">
                  <div className="safety-copy">
                    <span className="appearance-label">{t("safetyAutoApprove")}</span>
                    <p className="hint">{t("safetyAutoApproveHint")}</p>
                  </div>
                  <button
                    type="button"
                    className={`memory-toggle${switchOn ? " on" : ""}`}
                    role="switch"
                    aria-checked={switchOn}
                    disabled={autoApproveBusy || !onToggleAutoApprove}
                    title={switchOn ? t("safetyAutoApproveOn") : t("safetyAutoApproveOff")}
                    onClick={() => {
                      if (autoApproveTools) {
                        setConfirmAllowAll(false);
                        onToggleAutoApprove?.(false);
                        return;
                      }
                      setConfirmAllowAll((open) => !open);
                    }}
                  >
                    <i />
                  </button>
                </div>
              </div>
              {confirmAllowAll && !autoApproveTools && (
                <div className="safety-risk" role="alertdialog" aria-label={t("safetyAutoApproveRiskTitle")}>
                  <strong>{t("safetyAutoApproveRiskTitle")}</strong>
                  <p>{t("safetyAutoApproveRisk")}</p>
                  <div className="safety-risk-actions">
                    <button
                      type="button"
                      className="ghost"
                      disabled={autoApproveBusy}
                      onClick={() => setConfirmAllowAll(false)}
                    >
                      {t("safetyAutoApproveCancel")}
                    </button>
                    <button
                      type="button"
                      className="primary danger"
                      disabled={autoApproveBusy}
                      onClick={() => void onToggleAutoApprove?.(true)}
                    >
                      {t("safetyAutoApproveConfirm")}
                    </button>
                  </div>
                </div>
              )}
              {autoApproveTools && (
                <p className="safety-risk-active" role="status">
                  {t("safetyAutoApproveActive")}
                </p>
              )}
            </div>
          )}

          {settingsTab === "model" &&
            (model ? (
              <ModelSettings
                setup={model}
                locale={locale}
                saving={modelSaving}
                onChange={onModelChange}
                onSave={(next, opts) => void onModelSave(next, opts)}
                t={t}
              />
            ) : (
              // `model` can still be null if the boot-time fetch raced or
              // failed — show a loading hint instead of a blank pane.
              <div className="settings-pane">
                <h3>{t("tabModel")}</h3>
                <p className="hint">{t("loading")}</p>
              </div>
            ))}

          {settingsTab === "skills" && (
            <SkillSettings t={t} onToast={onToast} onChanged={onSkillsChanged} />
          )}

          {settingsTab === "plugins" && (
            <PluginSettings onToast={onToast} onOpenMcp={() => setSettingsTab("mcp")} />
          )}
          {settingsTab === "mcp" && <McpSettings onToast={onToast} />}

          {settingsTab === "account" && (
            <AccountPane
              user={accountUser ?? null}
              onUserChange={onAccountChange}
              onLogout={onLogout}
              onToast={onToast}
            />
          )}

          {settingsTab === "appearance" && (
            <div className="settings-pane settings-pane-fill appearance-pane">
              <header className="settings-pane-intro">
                <h3>{t("appearanceTitle")}</h3>
                <p className="hint">{t("appearanceHint")}</p>
              </header>

              <div className="settings-card-list">
                <div className="appearance-row appearance-mode-card">
                  <span className="appearance-label">{t("uiModeLabel")}</span>
                  <button
                    type="button"
                    className={`mode-option${uiMode === "workbench" ? " active" : ""}`}
                    onClick={() => setUiMode("workbench")}
                  >
                    <strong>{t("uiModeWorkbench")}</strong>
                    <span>{t("uiModeWorkbenchDesc")}</span>
                  </button>
                  <button
                    type="button"
                    className={`mode-option${uiMode === "social" ? " active" : ""}`}
                    onClick={() => setUiMode("social")}
                  >
                    <strong>{t("uiModeSocial")}</strong>
                    <span>{t("uiModeSocialDesc")}</span>
                  </button>
                </div>

                <div className="appearance-row">
                  <span className="appearance-label">{t("language")}</span>
                  <div className="seg-control" role="group" aria-label={t("language")}>
                    <button
                      type="button"
                      className={locale === "zh" ? "active" : ""}
                      onClick={() => setLocale("zh")}
                    >
                      中文
                    </button>
                    <button
                      type="button"
                      className={locale === "en" ? "active" : ""}
                      onClick={() => setLocale("en")}
                    >
                      English
                    </button>
                  </div>
                </div>

                <div className="appearance-row">
                  <span className="appearance-label">{t("theme")}</span>
                  <div className="seg-control" role="group" aria-label={t("theme")}>
                    <button
                      type="button"
                      className={theme === "light" ? "active" : ""}
                      onClick={() => setTheme("light")}
                      title={t("themeLight")}
                    >
                      <IconSun /> {t("themeLight")}
                    </button>
                    <button
                      type="button"
                      className={theme === "dark" ? "active" : ""}
                      onClick={() => setTheme("dark")}
                      title={t("themeDark")}
                    >
                      <IconMoon /> {t("themeDark")}
                    </button>
                  </div>
                </div>

                <div className="appearance-row">
                  <span className="appearance-label">{t("density")}</span>
                  <div className="seg-control" role="group" aria-label={t("density")}>
                    <button
                      type="button"
                      className={density === "comfort" ? "active" : ""}
                      onClick={() => setDensity("comfort")}
                    >
                      {t("densityComfort")}
                    </button>
                    <button
                      type="button"
                      className={density === "compact" ? "active" : ""}
                      onClick={() => setDensity("compact")}
                    >
                      {t("densityCompact")}
                    </button>
                  </div>
                </div>
              </div>

              <div className="settings-about-card">
                <h4>{t("aboutTitle")}</h4>
                <p className="settings-about-ver">{t("aboutVersion", APP_VERSION)}</p>
                <p className="hint">{t("aboutBlurb")}</p>
              </div>
            </div>
          )}

          {settingsTab === "runtime" && (
            <div className="settings-pane settings-pane-fill logs-pane">
              <header className="settings-pane-intro">
                <h3>{t("logsTitle")}</h3>
                <p className="hint">{t("logsHint")}</p>
              </header>

              <div className={`logs-grid${selectedSub ? " has-sub-detail" : ""}`}>
                <section className="logs-panel logs-panel-events">
                  <div className="logs-panel-head">
                    <h4>{t("runtimeEvents")}</h4>
                    <span className="logs-count">{live.length}</span>
                  </div>
                  {live.length === 0 ? (
                    <div className="logs-empty">
                      <strong>{t("runtimeEvents")}</strong>
                      <span>{t("runtimeEventsHint")}</span>
                    </div>
                  ) : (
                    <ul className="live-log">
                      {live.map((l, idx) => (
                        <li key={l.id} ref={idx === live.length - 1 ? logEndRef : undefined}>
                          <code>{l.kind}</code>
                          <span className="live-log-text">{l.text}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                <section className="logs-panel logs-panel-subs">
                  <div className="logs-panel-head">
                    <h4>{t("runtimeSubs")}</h4>
                    <span className="logs-count">{flatSubs.length}</span>
                  </div>
                  {flatSubs.length === 0 ? (
                    <div className="logs-empty">
                      <strong>{t("runtimeSubs")}</strong>
                      <span>{t("runtimeSubsHint")}</span>
                    </div>
                  ) : (
                    <ul className="sub-tree">
                      {flatSubs.map((s) => {
                        const active = selectedSubId === s.id;
                        return (
                          <li key={s.id} className={`${s.status}${active ? " active" : ""}`}>
                            <button
                              type="button"
                              className="sub-row"
                              aria-pressed={active}
                              onClick={() => {
                                if (onOpenSubagent) {
                                  onOpenSubagent(s);
                                  onClose();
                                  return;
                                }
                                setSelectedSubId((id) => (id === s.id ? null : s.id));
                              }}
                            >
                              <div className="sub-goal">{s.goal || s.role || s.id}</div>
                              <div className="sub-meta">
                                {s.status}
                                {s.activity ? ` · ${s.activity}` : ""}
                              </div>
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </section>

                {selectedSub ? (
                  <section className="logs-panel logs-panel-detail">
                    <div className="logs-panel-head">
                      <h4>{t("detailSubagent")}</h4>
                      <button
                        type="button"
                        className="ghost logs-detail-close"
                        onClick={() => setSelectedSubId(null)}
                      >
                        {t("close")}
                      </button>
                    </div>
                    <div className="logs-sub-detail">
                      <p className="sub-goal">{selectedSub.goal}</p>
                      <div className="sub-meta">
                        {selectedSub.status}
                        {selectedSub.activity ? ` · ${selectedSub.activity}` : ""}
                      </div>
                      {selectedSub.summary ? (
                        <pre className="sub-sum">{selectedSub.summary}</pre>
                      ) : null}
                      {(selectedSub.transcript || []).length > 0 ? (
                        <ul className="logs-transcript">
                          {selectedSub.transcript.map((item) => (
                            <li key={item.id}>
                              <code>{item.kind}</code>
                              <span>
                                {item.kind === "assistant"
                                  ? String(item.text || item.reasoning || "").trim()
                                  : item.kind === "tool"
                                    ? `${item.tool?.name || "tool"}${item.tool?.status ? ` · ${item.tool.status}` : ""}`
                                    : ""}
                              </span>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="hint">{t("runtimeSubsHint")}</p>
                      )}
                    </div>
                  </section>
                ) : null}
              </div>
              <p className="hint logs-footnote">{t("logsClearView")}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function flattenSubNodes(nodes: SubNode[]): SubNode[] {
  const out: SubNode[] = [];
  const seen = new Set<string>();
  const walk = (node: SubNode) => {
    if (!node?.id || seen.has(node.id)) return;
    seen.add(node.id);
    out.push(node);
    for (const child of node.children || []) walk(child);
  };
  for (const node of nodes || []) walk(node);
  return out;
}
