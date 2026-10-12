import type { SidePanel } from "../layoutPersist";
import { FileExplorer } from "./FileExplorer";
import { FileSearchPanel } from "./FileSearchPanel";
import { HistoryPanel } from "./HistoryPanel";
import { BrowserPanel, type BrowserOpenRequest } from "./BrowserPanel";
import { GitPanel } from "./GitPanel";
import { UndoTimeline } from "./UndoTimeline";
import { IconBook, IconCalendar, IconChevronRight, IconClock, IconFiles, IconGit, IconGlobe, IconSearch, IconSettings, IconTerminal, IconUndo, IconX } from "./icons";
import { ShellJobsPanel } from "./ShellJobsPanel";
import { SchedulesPanel } from "./SchedulesPanel";
import { IconRobotCube } from "./IconRobotCube";
import type { SessionItem, WorkspaceItem } from "../api";
import { fileToDetail } from "../utils/chatHelpers";
import type { MsgKey } from "../i18n";
import type { DomElementPayload } from "../browser/protocol";

export type ActivitySidebarProps = {
  /** Hide left icon rail; show only panel body (for social collab pane). */
  embedded?: boolean;
  /** When embedded, force this panel. */
  forcedPanel?: SidePanel;
  t: (key: MsgKey, ...args: string[]) => string;
  sidePanel: SidePanel;
  setSidePanel: (panel: SidePanel) => void;
  explorerCollapsed: boolean;
  setExplorerCollapsed: (v: boolean) => void;
  explorerWidth: number;
  fsRefresh: number;
  activeWs: { path: string; name: string } | null;
  workspaces?: WorkspaceItem[];
  /** Workspaces backing currently-open chat tabs (drives the hub row alongside `workspaces`). */
  openTabWorkspaces?: { path: string; name: string; running: boolean }[];
  sessions: SessionItem[];
  sessionId: string | null;
  sessionsPage: number;
  sessionsTotalPages: number;
  sessionsTotal: number;
  navBusy?: boolean;
  onOpenHistoryPanel: () => void;
  onRefreshSessions: (page?: number) => void;
  onOpenSession: (id: string) => void;
  onNewChat: () => void;
  /** Switch the active chat/workspace to `path` (opens an existing chat or starts a new one). */
  onSwitchWorkspace?: (path: string) => void;
  /** Pick a folder on disk and switch the project to it. */
  onBrowseWorkspace?: () => void;
  /** Remove a folder from the recent/hub workspace list. */
  onForgetWorkspace?: (path: string) => void;
  onDeleteSession: (id: string) => Promise<void>;
  onOpenSettings: () => void;
  onOpenFile: (file: Parameters<typeof fileToDetail>[0], opts?: Parameters<typeof fileToDetail>[1]) => void;
  onFileDeleted: (path: string) => void;
  /** File currently shown in the detail pane, if any. */
  activeFilePath?: string | null;
  onResizeStart: () => void;
  onPickDomElement: (el: DomElementPayload) => void;
  browserOpenRequest?: BrowserOpenRequest | null;
  browserSuspended?: boolean;
  onWorkspaceMutated?: () => void;
  mainView?: "chat" | "memory";
  onOpenMemory?: () => void;
  onOpenChat?: () => void;
  jobsRunning?: number;
  jobsFocusId?: string;
  jobsRefreshKey?: number;
  schedulesCount?: number;
  schedulesRefreshKey?: number;
};

export function ActivitySidebar({
  embedded = false,
  forcedPanel,
  t,
  sidePanel,
  setSidePanel,
  explorerCollapsed,
  setExplorerCollapsed,
  explorerWidth,
  fsRefresh,
  activeWs,
  workspaces = [],
  sessions,
  sessionId,
  sessionsPage,
  sessionsTotalPages,
  sessionsTotal,
  navBusy = false,
  onOpenHistoryPanel,
  onRefreshSessions,
  onOpenSession,
  onNewChat,
  onSwitchWorkspace,
  onBrowseWorkspace,
  onDeleteSession,
  onOpenSettings,
  onOpenFile,
  onFileDeleted,
  activeFilePath = null,
  onResizeStart,
  onPickDomElement,
  browserOpenRequest,
  browserSuspended = false,
  onWorkspaceMutated,
  mainView = "chat",
  onOpenMemory,
  onOpenChat,
  jobsRunning = 0,
  jobsFocusId = "",
  jobsRefreshKey = 0,
  schedulesCount = 0,
  schedulesRefreshKey = 0,
}: ActivitySidebarProps) {
  const sidePanelEff = forcedPanel ?? sidePanel;
  const explorerCollapsedEff = embedded ? false : explorerCollapsed;
  const showChat = () => onOpenChat?.();

  const projectSwitch = (
    <ProjectSwitch
      activePath={activeWs?.path || ""}
      workspaces={workspaces}
      onPick={(path) => onSwitchWorkspace?.(path)}
      onBrowse={onBrowseWorkspace}
    />
  );

  return (
    <>
      {!embedded && (
      <nav className={`activity-rail${explorerCollapsedEff ? " icons-only" : ""}`} aria-label="EdgeSidekick">
        <button
          type="button"
          className="activity-brand"
          title="EdgeSidekick"
          onClick={() => {
            setSidePanel("files");
            setExplorerCollapsed(false);
          }}
        >
          <IconRobotCube size={26} />
        </button>
        <div className="activity-top">
          <button
            type="button"
            className={`activity-btn${sidePanelEff === "search" && !explorerCollapsedEff ? " active" : ""}`}
            title={t("navSearch")}
            onClick={() => {
              setSidePanel("search");
              setExplorerCollapsed(false);
            }}
          >
            <IconSearch size={18} />
            <span>{t("navSearch")}</span>
          </button>
          <button
            type="button"
            className={`activity-btn${sidePanelEff === "files" && !explorerCollapsedEff ? " active" : ""}`}
            title={t("navFiles")}
            onClick={() => {
              if (sidePanelEff === "files" && !explorerCollapsedEff) {
                setExplorerCollapsed(true);
              } else {
                setSidePanel("files");
                setExplorerCollapsed(false);
              }
            }}
          >
            <IconFiles size={18} />
            <span>{t("navFiles")}</span>
          </button>
          <button
            type="button"
            className={`activity-btn${sidePanelEff === "browser" && !explorerCollapsedEff ? " active" : ""}`}
            title={t("navBrowser")}
            onClick={() => {
              if (sidePanelEff === "browser" && !explorerCollapsedEff) {
                setExplorerCollapsed(true);
              } else {
                setSidePanel("browser");
                setExplorerCollapsed(false);
              }
            }}
            data-panel="browser"
          >
            <IconGlobe size={18} />
            <span>{t("navBrowser")}</span>
          </button>
          <button
            type="button"
            className={`activity-btn${sidePanelEff === "git" && !explorerCollapsedEff ? " active" : ""}`}
            title={t("navGit")}
            onClick={() => {
              if (sidePanelEff === "git" && !explorerCollapsedEff) {
                setExplorerCollapsed(true);
              } else {
                setSidePanel("git");
                setExplorerCollapsed(false);
              }
            }}
          >
            <IconGit size={18} />
            <span>{t("navGit")}</span>
          </button>
          <button
            type="button"
            className={`activity-btn${sidePanelEff === "jobs" && !explorerCollapsedEff ? " active" : ""}${jobsRunning > 0 ? " has-badge" : ""}`}
            title={t("navJobs")}
            onClick={() => {
              showChat();
              if (sidePanelEff === "jobs" && !explorerCollapsedEff) {
                setExplorerCollapsed(true);
              } else {
                setSidePanel("jobs");
                setExplorerCollapsed(false);
              }
            }}
            data-panel="jobs"
          >
            <span className="activity-ico">
              <IconTerminal size={18} />
              {jobsRunning > 0 ? <i className="activity-badge">{jobsRunning > 9 ? "9+" : jobsRunning}</i> : null}
            </span>
            <span>{t("navJobs")}</span>
          </button>
          <button
            type="button"
            className={`activity-btn${sidePanelEff === "schedules" && !explorerCollapsedEff ? " active" : ""}${schedulesCount > 0 ? " has-badge" : ""}`}
            title={t("navSchedules")}
            onClick={() => {
              showChat();
              if (sidePanelEff === "schedules" && !explorerCollapsedEff) {
                setExplorerCollapsed(true);
              } else {
                setSidePanel("schedules");
                setExplorerCollapsed(false);
              }
            }}
            data-panel="schedules"
          >
            <span className="activity-ico">
              <IconCalendar size={18} />
              {schedulesCount > 0 ? <i className="activity-badge">{schedulesCount > 9 ? "9+" : schedulesCount}</i> : null}
            </span>
            <span>{t("navSchedules")}</span>
          </button>
          <button
            type="button"
            className={`activity-btn${sidePanelEff === "undo" && !explorerCollapsedEff ? " active" : ""}`}
            title={t("navUndo")}
            onClick={() => {
              showChat();
              if (sidePanelEff === "undo" && !explorerCollapsedEff) {
                setExplorerCollapsed(true);
              } else {
                setSidePanel("undo");
                setExplorerCollapsed(false);
              }
            }}
          >
            <IconUndo size={18} />
            <span>{t("navUndo")}</span>
          </button>
          <button
            type="button"
            className={`activity-btn${mainView === "memory" ? " active" : ""}`}
            title={t("navMemory")}
            onClick={() => {
              if (mainView === "memory") showChat();
              else onOpenMemory?.();
            }}
          >
            <IconBook size={18} />
            <span>{t("navMemory")}</span>
          </button>
          <button
            type="button"
            className={`activity-btn${sidePanelEff === "history" && !explorerCollapsedEff && mainView !== "memory" ? " active" : ""}`}
            title={t("history")}
            onClick={() => {
              showChat();
              if (sidePanelEff === "history" && !explorerCollapsedEff) {
                setExplorerCollapsed(true);
              } else {
                onOpenHistoryPanel();
              }
            }}
          >
            <IconClock size={18} />
            <span>{t("history")}</span>
          </button>
        </div>
        <div className="activity-bottom">
          <button
            type="button"
            className="activity-btn activity-collapse"
            title={explorerCollapsedEff ? t("sidebarExpand") : t("sidebarToIcons")}
            aria-label={explorerCollapsedEff ? t("sidebarExpand") : t("sidebarToIcons")}
            onClick={() => setExplorerCollapsed(!explorerCollapsedEff)}
          >
            <IconChevronRight size={18} />
          </button>
          <button
            type="button"
            className="activity-btn"
            title={t("navSettings")}
            onClick={onOpenSettings}
          >
            <IconSettings size={18} />
            <span>{t("navSettings")}</span>
          </button>
        </div>
      </nav>
      )}

      {!explorerCollapsedEff && (
        <>
          {sidePanelEff === "search" ? (
            <div className="side-panel-wrap ws-hub-wrap" style={{ width: embedded ? "100%" : explorerWidth }}>
              <FileSearchPanel
                refreshKey={fsRefresh}
                activeFilePath={activeFilePath}
                workspace={activeWs?.path || null}
                onOpenFile={(file, opts) => onOpenFile(file, opts)}
              />
            </div>
          ) : sidePanelEff === "history" ? (
            <div className="side-panel-wrap ws-hub-wrap" style={{ width: embedded ? "100%" : explorerWidth }}>
              {projectSwitch}
              <HistoryPanel
                sessions={sessions}
                activeSessionId={sessionId}
                page={sessionsPage}
                totalPages={sessionsTotalPages}
                total={sessionsTotal}
                onRefresh={() => void onRefreshSessions(sessionsPage)}
                onPageChange={(p) => void onRefreshSessions(p)}
                onOpen={(id) => void onOpenSession(id)}
                onNew={() => void onNewChat()}
                onDelete={(id) => onDeleteSession(id)}
                busy={navBusy}
              />
            </div>
          ) : sidePanelEff === "browser" ? (
            <div className="side-panel-wrap ws-hub-wrap" style={{ width: embedded ? "100%" : explorerWidth }}>
              <BrowserPanel
                key={`br-${activeWs?.path || ""}-${sessionId || ""}`}
                onPickElement={onPickDomElement}
                openRequest={browserOpenRequest}
                suspended={browserSuspended}
              />
            </div>
          ) : sidePanelEff === "git" ? (
            <div className="side-panel-wrap ws-hub-wrap" style={{ width: embedded ? "100%" : explorerWidth }}>
              <GitPanel
                refreshKey={fsRefresh}
                onChanged={onWorkspaceMutated}
                workspace={activeWs?.path || null}
              />
            </div>
          ) : sidePanelEff === "jobs" ? (
            <div className="side-panel-wrap ws-hub-wrap" style={{ width: embedded ? "100%" : explorerWidth }}>
              <ShellJobsPanel refreshKey={jobsRefreshKey} focusJobId={jobsFocusId} />
            </div>
          ) : sidePanelEff === "schedules" ? (
            <div className="side-panel-wrap ws-hub-wrap" style={{ width: embedded ? "100%" : explorerWidth }}>
              <SchedulesPanel refreshKey={schedulesRefreshKey} onOpenSession={(id) => void onOpenSession(id)} />
            </div>
          ) : sidePanelEff === "undo" ? (
            <div className="side-panel-wrap ws-hub-wrap" style={{ width: embedded ? "100%" : explorerWidth }}>
              <UndoTimeline
                refreshKey={fsRefresh}
                sessionId={sessionId}
                onRestored={onWorkspaceMutated}
              />
            </div>
          ) : (
            <div className="side-panel-wrap ws-hub-wrap" style={{ width: embedded ? "100%" : explorerWidth }}>
              {projectSwitch}
              <FileExplorer
              hideChrome={embedded}
                rootName={activeWs?.name || "workspace"}
                workspaceAbsPath={activeWs?.path || null}
                collapsed={false}
                width={embedded ? 0 : explorerWidth}
                onToggle={() => setExplorerCollapsed(true)}
                refreshKey={fsRefresh}
                onOpenFile={(file) => onOpenFile(file)}
                onDeleted={onFileDeleted}
                activeFilePath={activeFilePath}
              />
            </div>
          )}
        </>
      )}
      {!embedded && (
        <div
          className="sidebar-resizer"
          onMouseDown={(e) => {
            e.preventDefault();
            onResizeStart();
          }}
          title={explorerCollapsedEff ? t("sidebarExpand") : "拖拽调整宽度，拖到最左只留图标"}
        />
      )}
    </>
  );
}

function folderKey(path: string) {
  return path.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

/** Project picker used only in the files and history panels. */
function ProjectSwitch({
  workspaces,
  activePath,
  onPick,
  onBrowse,
}: {
  workspaces: { path: string; name: string }[];
  activePath: string;
  onPick: (path: string) => void;
  onBrowse?: () => void;
}) {
  const byPath = new Map<string, { path: string; name: string }>();
  const bump = (path: string, name: string) => {
    const key = folderKey(path);
    if (!key || byPath.has(key)) return;
    byPath.set(key, { path, name: name || path.split(/[/\\]/).pop() || path });
  };
  if (activePath) bump(activePath, activePath.split(/[/\\]/).pop() || activePath);
  for (const w of workspaces) bump(w.path, w.name);
  const options = [...byPath.values()];
  const selected =
    options.find((w) => folderKey(w.path) === folderKey(activePath))?.path || activePath;
  if (!selected && options.length === 0) return null;
  return (
    <div className="project-switch">
      <label>
        <span>项目</span>
        <select
          value={selected}
          title={selected}
          onChange={(e) => {
            const path = e.target.value;
            if (path && folderKey(path) !== folderKey(activePath)) onPick(path);
          }}
        >
          {options.map((w) => (
            <option key={w.path} value={w.path}>
              {w.name}
            </option>
          ))}
        </select>
      </label>
      {onBrowse ? (
        <button type="button" onClick={onBrowse}>
          文件夹
        </button>
      ) : null}
    </div>
  );
}

