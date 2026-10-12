import { useEffect, useState } from "react";
import type { SessionItem } from "../api";
import { usePrefs } from "../prefs";
import {
  IconChat,
  IconCheck,
  IconChevronDown,
  IconClock,
  IconFolder,
  IconPlus,
  IconRefresh,
  IconTrash,
  IconX,
} from "./icons";

type HistoryGroupId = "today" | "yesterday" | "earlier";

function historyGroupId(ts: number, now = Date.now()): HistoryGroupId {
  const t = ts > 1e12 ? ts : ts * 1000;
  const d = new Date(t);
  const startToday = new Date();
  startToday.setHours(0, 0, 0, 0);
  const startYesterday = new Date(startToday);
  startYesterday.setDate(startYesterday.getDate() - 1);
  if (d >= startToday) return "today";
  if (d >= startYesterday) return "yesterday";
  return "earlier";
}

function formatHistoryClock(ts: number, locale: "zh" | "en" = "zh") {
  if (!ts) return "";
  const t = ts > 1e12 ? ts : ts * 1000;
  const d = new Date(t);
  return d.toLocaleTimeString(locale === "zh" ? "zh-CN" : "en-US", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

const UNTITLED_TITLES = new Set(["新会话", "New chat", "Untitled", ""]);

function displaySessionTitle(title: string | undefined, untitled: string, fallback: string) {
  const raw = (title || "").trim();
  if (UNTITLED_TITLES.has(raw)) return untitled;
  if (raw.startsWith("[CONTEXT COMPACTION]")) return untitled;
  return raw || fallback;
}

type Props = {
  sessions: SessionItem[];
  activeSessionId: string | null;
  page: number;
  totalPages: number;
  total: number;
  onRefresh: () => void;
  onPageChange: (page: number) => void;
  onOpen: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => Promise<void>;
  busy?: boolean;
};

export function HistoryPanel({
  sessions,
  activeSessionId,
  page,
  totalPages,
  total,
  onRefresh,
  onPageChange,
  onOpen,
  onNew,
  onDelete,
  busy = false,
}: Props) {
  const { t, locale } = usePrefs();
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [earlierOpen, setEarlierOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    setPendingDeleteId(null);
  }, [page, sessions]);

  return (
    <aside className="side-panel history-panel">
      <div className="side-panel-head">
        <IconClock size={15} />
        <span>{t("historyTitle")}</span>
        <div className="side-panel-head-actions">
          <button type="button" className="icon-btn" title={t("historyNew")} onClick={onNew}>
            <IconPlus size={16} />
          </button>
          <button
            type="button"
            className="icon-btn"
            title={t("feRefresh")}
            disabled={refreshing}
            onClick={() => {
              setRefreshing(true);
              void Promise.resolve(onRefresh()).finally(() => setRefreshing(false));
            }}
          >
            <IconRefresh size={15} />
          </button>
        </div>
      </div>
      <div className="history-scroll side-history-scroll">
        {busy ? <p className="hint">正在切换…</p> : null}
        {sessions.length === 0 && !busy && <p className="hint">{t("historyEmpty")}</p>}
        {(["today", "yesterday", "earlier"] as const).map((gid) => {
          const items = sessions.filter((s) => historyGroupId(s.updated_at) === gid);
          if (items.length === 0) return null;
          const label =
            gid === "today"
              ? t("historyToday")
              : gid === "yesterday"
                ? t("historyYesterday")
                : t("historyEarlier");
          const collapsed = gid === "earlier" && !earlierOpen;
          return (
            <div key={gid} className="history-group">
              <button
                type="button"
                className="history-group-head"
                onClick={() => {
                  if (gid === "earlier") setEarlierOpen((v) => !v);
                }}
              >
                <span>{label}</span>
                {gid === "earlier" && (
                  <IconChevronDown size={14} className={earlierOpen ? "rot-180" : ""} />
                )}
              </button>
              {!collapsed && (
                <ul className="history-list">
                  {items.map((s) => {
                    const running = Boolean(s.busy);
                    return (
                    <li
                      key={s.id}
                      className={`history-item${pendingDeleteId === s.id ? " confirming" : ""}${running ? " is-running" : ""}`}
                    >
                      <button
                        type="button"
                        className={`history-open${s.id === activeSessionId ? " active" : ""}${running ? " running" : ""}`}
                        aria-busy={running || undefined}
                        disabled={busy}
                        onClick={() => {
                          if (busy) return;
                          setPendingDeleteId(null);
                          onOpen(s.id);
                        }}
                      >
                        <span className={`history-open-icon${running ? " loading" : ""}`}>
                          {running ? <span className="history-spinner" aria-hidden /> : <IconChat size={14} />}
                        </span>
                        <span className="history-open-text">
                          <strong title={displaySessionTitle(s.title, t("sessionUntitled"), s.id)}>
                            {displaySessionTitle(s.title, t("sessionUntitled"), s.id)}
                          </strong>
                          {s.workspace_name && (
                            <em
                              className={`history-ws-badge${s.is_current_workspace ? " current" : ""}`}
                              title={s.workspace}
                            >
                              <IconFolder size={11} />
                              {s.workspace_name}
                            </em>
                          )}
                        </span>
                        {pendingDeleteId !== s.id && (
                          <span className={`history-open-time${running ? " running" : ""}`}>
                            {running ? t("historyRunning") : formatHistoryClock(s.updated_at, locale)}
                          </span>
                        )}
                      </button>
                      {pendingDeleteId === s.id ? (
                        <div
                          className="history-confirm"
                          role="group"
                          aria-label={t("historyDeleteConfirm")}
                        >
                          <span className="history-confirm-label">{t("historyDeleteConfirm")}</span>
                          <button
                            type="button"
                            className="fe-inline-btn cancel"
                            title={t("cancel")}
                            onClick={(e) => {
                              e.stopPropagation();
                              setPendingDeleteId(null);
                            }}
                          >
                            <IconX size={14} />
                          </button>
                          <button
                            type="button"
                            className="fe-inline-btn danger"
                            title={t("historyDelete")}
                            onClick={(e) => {
                              e.stopPropagation();
                              void onDelete(s.id)
                                .then(() => setPendingDeleteId(null))
                                .catch(() => undefined);
                            }}
                          >
                            <IconCheck size={14} />
                          </button>
                        </div>
                      ) : (
                        <button
                          type="button"
                          className="history-delete icon-only"
                          title={t("historyDelete")}
                          onClick={(e) => {
                            e.stopPropagation();
                            setPendingDeleteId(s.id);
                          }}
                        >
                          <IconTrash size={14} />
                        </button>
                      )}
                    </li>
                    );
                  })}
                </ul>
              )}
            </div>
          );
        })}
      </div>
      {total > 0 && (
        <div className="history-pager">
          <button
            type="button"
            className="history-page-btn"
            disabled={page <= 1}
            onClick={() => onPageChange(page - 1)}
          >
            {t("historyPrev")}
          </button>
          <span className="history-page-info">
            {t("historyPage")
              .replace("{page}", String(page))
              .replace("{total}", String(Math.max(1, totalPages)))}
          </span>
          <button
            type="button"
            className="history-page-btn"
            disabled={page >= totalPages}
            onClick={() => onPageChange(page + 1)}
          >
            {t("historyNext")}
          </button>
        </div>
      )}
    </aside>
  );
}
