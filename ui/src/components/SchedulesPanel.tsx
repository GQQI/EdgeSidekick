import { useCallback, useEffect, useMemo, useState } from "react";
import {
  deleteSchedule,
  fetchSchedules,
  patchSchedule,
  type ScheduleTask,
} from "../api";
import { usePrefs } from "../prefs";
import { IconCalendar, IconRefresh, IconTrash } from "./icons";

type Props = {
  refreshKey?: number;
  onOpenSession?: (id: string) => void;
  /** Hide the workbench chrome when embedded in the 会话版 sidebar. */
  compact?: boolean;
};

const _WEEK_LABELS = ["一", "二", "三", "四", "五", "六", "日"];

function dayKey(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

function fmtDateTime(ts: number | null | undefined): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${dayKey(d)} ${hh}:${mm}`;
}

type BadgeKey =
  | "schedulesStatusEnabled"
  | "schedulesStatusPaused"
  | "schedulesStatusOk"
  | "schedulesStatusError"
  | "schedulesStatusMissed"
  | "schedulesStatusOrphan";

function statusBadgeKey(task: ScheduleTask): BadgeKey {
  if (!task.enabled)
    return task.last_status === "orphan" ? "schedulesStatusOrphan" : "schedulesStatusPaused";
  if (task.last_status === "error") return "schedulesStatusError";
  if (task.last_status === "missed") return "schedulesStatusMissed";
  if (task.last_status === "orphan") return "schedulesStatusOrphan";
  if (task.last_status === "ok") return "schedulesStatusOk";
  return "schedulesStatusEnabled";
}

function inSpan(task: ScheduleTask, key: string): boolean {
  const until = (task.until || "").trim();
  if (until && key > until) return false;
  if (task.created_at) {
    const start = dayKey(new Date(task.created_at * 1000));
    if (key < start) return false;
  }
  return true;
}

/** Dates (within the visible month) a task marks on the mini calendar. */
function taskDays(task: ScheduleTask, year: number, month: number): Set<string> {
  const out = new Set<string>();
  if (task.kind === "once") {
    if (task.at) {
      const d = new Date(task.at * 1000);
      out.add(dayKey(d));
    }
    return out;
  }
  const days = new Date(year, month + 1, 0).getDate();
  for (let i = 0; i < days; i++) {
    const d = new Date(year, month, 1 + i);
    const key = dayKey(d);
    if (!inSpan(task, key)) continue;
    if (task.kind === "daily" || task.kind === "interval") out.add(key);
    else if (task.kind === "weekly" && (task.weekdays || []).includes((d.getDay() + 6) % 7))
      out.add(key);
    else if (task.kind === "monthly") {
      const want = task.month_day || 1;
      const last = new Date(year, month + 1, 0).getDate();
      if (d.getDate() === Math.min(want, last)) out.add(key);
    }
  }
  return out;
}

export function SchedulesPanel({ refreshKey = 0, onOpenSession, compact = false }: Props) {
  const { t } = usePrefs();
  const [tasks, setTasks] = useState<ScheduleTask[]>([]);
  const [viewMonth, setViewMonth] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });
  const [selectedDay, setSelectedDay] = useState("");
  const [expandedId, setExpandedId] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [busyId, setBusyId] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const res = await fetchSchedules();
      setTasks(res.items || []);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  // Slow poll so due-run state (next_run / last_status) stays fresh.
  useEffect(() => {
    const timer = window.setInterval(() => void load(), 60000);
    return () => window.clearInterval(timer);
  }, [load]);

  const todayKey = dayKey(new Date());
  const marks = useMemo(() => {
    const map = new Map<string, { active: boolean; paused: boolean }>();
    const bump = (key: string, enabled: boolean) => {
      const cur = map.get(key) || { active: false, paused: false };
      if (enabled) cur.active = true;
      else cur.paused = true;
      map.set(key, cur);
    };
    for (const task of tasks) {
      for (const key of taskDays(task, viewMonth.getFullYear(), viewMonth.getMonth())) {
        bump(key, task.enabled);
      }
    }
    return map;
  }, [tasks, viewMonth]);

  const grid = useMemo(() => {
    const year = viewMonth.getFullYear();
    const month = viewMonth.getMonth();
    const firstDow = (new Date(year, month, 1).getDay() + 6) % 7; // Monday first
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const cells: (Date | null)[] = [];
    for (let i = 0; i < firstDow; i++) cells.push(null);
    for (let d = 1; d <= daysInMonth; d++) cells.push(new Date(year, month, d));
    while (cells.length % 7) cells.push(null);
    return cells;
  }, [viewMonth]);

  const dayTasks = useMemo(() => {
    if (selectedDay) {
      return tasks.filter((task) => taskDays(task, viewMonth.getFullYear(), viewMonth.getMonth()).has(selectedDay));
    }
    const now = Date.now() / 1000;
    return [...tasks].sort((a, b) => (a.next_run || 0) - (b.next_run || 0) || a.created_at - b.created_at);
  }, [tasks, selectedDay, viewMonth]);

  async function toggleEnabled(task: ScheduleTask) {
    setBusyId(task.id);
    setErr("");
    try {
      await patchSchedule(task.id, { enabled: !task.enabled });
      await load();
      window.dispatchEvent(new CustomEvent("sidekick-schedule"));
    } catch (e) {
      setErr(e instanceof Error ? e.message : t("schedulesPauseFail"));
    } finally {
      setBusyId("");
    }
  }

  async function remove(task: ScheduleTask) {
    if (!window.confirm(t("schedulesDeleteConfirm"))) return;
    setBusyId(task.id);
    setErr("");
    try {
      await deleteSchedule(task.id);
      if (expandedId === task.id) setExpandedId("");
      await load();
      window.dispatchEvent(new CustomEvent("sidekick-schedule"));
    } catch (e) {
      setErr(e instanceof Error ? e.message : t("schedulesDeleteFail"));
    } finally {
      setBusyId("");
    }
  }

  function shiftMonth(delta: number) {
    setViewMonth((cur) => new Date(cur.getFullYear(), cur.getMonth() + delta, 1));
  }

  const enabledCount = tasks.filter((task) => task.enabled).length;
  const monthLabel = `${viewMonth.getFullYear()}年${viewMonth.getMonth() + 1}月`;

  return (
    <div className={"side-panel schedules-panel" + (compact ? " compact" : "")}>
      <div className="side-panel-head">
        <IconCalendar size={16} />
        <span>{t("navSchedules")}</span>
        <div className="side-panel-head-actions">
          {enabledCount > 0 ? (
            <span className="jobs-count">{t("schedulesEnabledCount", String(enabledCount))}</span>
          ) : null}
          <button type="button" className="icon-btn" title={t("refresh")} onClick={() => void load()}>
            <IconRefresh size={14} />
          </button>
        </div>
      </div>
      <div className="side-panel-body schedules-body">
        {err ? <div className="side-error">{err}</div> : null}

        <div className="schedules-cal">
          <div className="schedules-cal-head">
            <button type="button" className="icon-btn" onClick={() => shiftMonth(-1)} aria-label="prev month">
              ‹
            </button>
            <span className="schedules-cal-month">{monthLabel}</span>
            <button type="button" className="icon-btn" onClick={() => shiftMonth(1)} aria-label="next month">
              ›
            </button>
          </div>
          <div className="schedules-cal-grid">
            {_WEEK_LABELS.map((w) => (
              <span key={w} className="schedules-cal-dow">
                {w}
              </span>
            ))}
            {grid.map((d, i) => {
              if (!d) return <span key={`blank-${i}`} className="schedules-cal-day blank" />;
              const key = dayKey(d);
              const mark = marks.get(key);
              const selected = selectedDay === key;
              return (
                <button
                  key={key}
                  type="button"
                  className={[
                    "schedules-cal-day",
                    key === todayKey ? " today" : "",
                    selected ? " selected" : "",
                    mark ? " has-task" : "",
                  ].join(" ")}
                  onClick={() => setSelectedDay(selected ? "" : key)}
                >
                  <span>{d.getDate()}</span>
                  {mark ? (
                    <i className={`schedules-cal-dot${mark.paused && !mark.active ? " paused" : ""}`} />
                  ) : null}
                </button>
              );
            })}
          </div>
        </div>

        {loading && tasks.length === 0 ? <div className="muted">{t("loading")}</div> : null}
        {tasks.length === 0 && !loading ? (
          <div className="jobs-empty">
            <IconCalendar size={22} />
            <p>{t("schedulesEmpty")}</p>
          </div>
        ) : (
          <>
            {selectedDay ? (
              <div className="schedules-day-label">
                <span>{t("schedulesSelectedDay", selectedDay)}</span>
                <button type="button" className="text-btn" onClick={() => setSelectedDay("")}>
                  {t("jobsCollapse")}
                </button>
              </div>
            ) : (
              <div className="schedules-day-label">
                <span>{t("schedulesUpcoming")}</span>
              </div>
            )}
            <ul className="jobs-list">
              {dayTasks.map((task) => {
                const active = expandedId === task.id;
                return (
                  <li key={task.id}>
                    <button
                      type="button"
                      className={`jobs-card${active ? " active" : ""}`}
                      aria-expanded={active}
                      onClick={() => setExpandedId(active ? "" : task.id)}
                    >
                      <span className={`jobs-dot ${task.enabled ? "running" : "exited"}`} />
                      <span className="jobs-card-main">
                        <strong title={task.title}>{task.title}</strong>
                        <em>
                          {task.schedule_text} · {t("schedulesNextRun")} {fmtDateTime(task.next_run)}
                        </em>
                      </span>
                      <span className="jobs-chevron" aria-hidden>
                        {active ? "▾" : "▸"}
                      </span>
                    </button>
                    {active ? (
                      <div className="schedules-detail">
                        <div className="schedules-detail-row">
                          <span className={`schedules-badge ${task.enabled ? "on" : "off"}`}>
                            {t(statusBadgeKey(task))}
                          </span>
                          {task.run_count ? (
                            <span className="muted">
                              {t("schedulesLastRun")} {fmtDateTime(task.last_run)}
                            </span>
                          ) : (
                            <span className="muted">{t("schedulesNever")}</span>
                          )}
                        </div>
                        {task.last_error ? (
                          <div className="schedules-detail-err">{task.last_error}</div>
                        ) : null}
                        <div className="schedules-detail-label">{t("schedulesPrompt")}</div>
                        <pre className="schedules-prompt">{task.prompt}</pre>
                        <div className="schedules-detail-actions">
                          <button
                            type="button"
                            className="text-btn"
                            disabled={busyId === task.id}
                            onClick={() => void toggleEnabled(task)}
                          >
                            {task.enabled ? t("schedulesPause") : t("schedulesEnable")}
                          </button>
                          {onOpenSession && task.session_id ? (
                            <button
                              type="button"
                              className="text-btn"
                              onClick={() => onOpenSession(task.session_id)}
                            >
                              {t("schedulesOpenSession")}
                            </button>
                          ) : null}
                          <button
                            type="button"
                            className="text-btn danger"
                            disabled={busyId === task.id}
                            onClick={() => void remove(task)}
                          >
                            <IconTrash size={12} /> {t("schedulesDelete")}
                          </button>
                        </div>
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}
