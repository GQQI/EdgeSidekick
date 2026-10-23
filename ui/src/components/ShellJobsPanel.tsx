import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  deleteShellJob,
  fetchShellJob,
  fetchShellJobs,
  stopShellJob,
  type ShellJobSnapshot,
} from "../api";
import { usePrefs } from "../prefs";
import { IconRefresh, IconTerminal, IconTrash, IconX } from "./icons";

type Props = {
  refreshKey?: number;
  focusJobId?: string;
};

function fmtElapsed(sec: number): string {
  const n = Math.max(0, Math.round(sec || 0));
  if (n < 60) return `${n}s`;
  const m = Math.floor(n / 60);
  const s = n % 60;
  if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function statusKey(status: string): "jobsStatusRunning" | "jobsStatusExited" | "jobsStatusKilled" {
  if (status === "running") return "jobsStatusRunning";
  if (status === "killed") return "jobsStatusKilled";
  return "jobsStatusExited";
}

/** A job is live while its shell or any child process still runs. */
function isLive(job: ShellJobSnapshot): boolean {
  return job.status === "running" || Boolean(job.group_alive);
}

function displayStatus(job: ShellJobSnapshot): string {
  return isLive(job) ? "running" : job.status;
}

export function ShellJobsPanel({ refreshKey = 0, focusJobId = "" }: Props) {
  const { t } = usePrefs();
  const [jobs, setJobs] = useState<ShellJobSnapshot[]>([]);
  /** Empty = list only; set when the user expands a job (or focusJobId arrives). */
  const [expandedId, setExpandedId] = useState("");
  const [detail, setDetail] = useState<ShellJobSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [busyId, setBusyId] = useState("");
  const logRef = useRef<HTMLPreElement | null>(null);
  const stickBottomRef = useRef(true);

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const res = await fetchShellJobs(true);
      setJobs(res.jobs || []);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  useEffect(() => {
    if (focusJobId) setExpandedId(focusJobId);
  }, [focusJobId]);

  const expanded = useMemo(
    () => (expandedId ? jobs.find((j) => j.job_id === expandedId) || null : null),
    [jobs, expandedId],
  );

  // Drop the expansion if the job was deleted from the list.
  useEffect(() => {
    if (expandedId && jobs.length && !jobs.some((j) => j.job_id === expandedId)) {
      setExpandedId("");
      setDetail(null);
    }
  }, [jobs, expandedId]);

  const loadDetail = useCallback(async (jobId: string) => {
    if (!jobId) {
      setDetail(null);
      return;
    }
    try {
      const snap = await fetchShellJob(jobId, 200);
      setDetail(snap);
    } catch {
      /* keep last log */
    }
  }, []);

  useEffect(() => {
    if (!expanded?.job_id) {
      setDetail(null);
      return;
    }
    void loadDetail(expanded.job_id);
  }, [expanded?.job_id, loadDetail, refreshKey]);

  const anyRunning = jobs.some(isLive);
  useEffect(() => {
    if (!anyRunning && !expanded?.job_id) return;
    const ms = anyRunning ? 2000 : 8000;
    const timer = window.setInterval(() => {
      void load();
      if (expanded?.job_id) void loadDetail(expanded.job_id);
    }, ms);
    return () => window.clearInterval(timer);
  }, [anyRunning, expanded?.job_id, load, loadDetail]);

  useEffect(() => {
    const el = logRef.current;
    if (!el || !stickBottomRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [detail?.log, detail?.log_lines]);

  function toggleExpand(jobId: string) {
    setExpandedId((cur) => (cur === jobId ? "" : jobId));
  }

  async function stop(jobId: string) {
    setBusyId(jobId);
    setErr("");
    try {
      await stopShellJob(jobId);
      await load();
      if (expandedId === jobId) await loadDetail(jobId);
    } catch (e) {
      setErr(e instanceof Error ? e.message : t("jobsStopFail"));
    } finally {
      setBusyId("");
    }
  }

  async function remove(jobId: string) {
    setBusyId(jobId);
    setErr("");
    try {
      await deleteShellJob(jobId);
      if (expandedId === jobId) {
        setExpandedId("");
        setDetail(null);
      }
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : t("jobsDeleteFail"));
    } finally {
      setBusyId("");
    }
  }

  async function clearFinished() {
    const done = jobs.filter((j) => !isLive(j));
    if (!done.length) return;
    setBusyId("*");
    setErr("");
    try {
      await Promise.all(done.map((j) => deleteShellJob(j.job_id).catch(() => undefined)));
      if (done.some((j) => j.job_id === expandedId)) {
        setExpandedId("");
        setDetail(null);
      }
      await load();
    } finally {
      setBusyId("");
    }
  }

  return (
    <div className="side-panel jobs-panel">
      <div className="side-panel-head">
        <IconTerminal size={16} />
        <span>{t("navJobs")}</span>
        <div className="side-panel-head-actions">
          {anyRunning ? (
            <span className="jobs-count">{t("jobsRunningCount", String(jobs.filter(isLive).length))}</span>
          ) : null}
          {jobs.some((j) => !isLive(j)) && (
            <button
              type="button"
              className="icon-btn"
              title={t("jobsClearFinished")}
              disabled={busyId === "*"}
              onClick={() => void clearFinished()}
            >
              <IconTrash size={14} />
            </button>
          )}
          <button type="button" className="icon-btn" title={t("refresh")} onClick={() => void load()}>
            <IconRefresh size={14} />
          </button>
        </div>
      </div>
      <div className="side-panel-body jobs-body">
        {loading && jobs.length === 0 ? <div className="muted">{t("loading")}</div> : null}
        {err ? <div className="side-error">{err}</div> : null}
        {jobs.length === 0 && !loading ? (
          <div className="jobs-empty">
            <IconTerminal size={22} />
            <p>{t("jobsEmpty")}</p>
          </div>
        ) : (
          <ul className="jobs-list">
            {jobs.map((job) => {
              const active = expanded?.job_id === job.job_id;
              return (
                <li key={job.job_id}>
                  <button
                    type="button"
                    className={`jobs-card${active ? " active" : ""}`}
                    aria-expanded={active}
                    onClick={() => toggleExpand(job.job_id)}
                  >
                    <span className={`jobs-dot ${isLive(job) ? "running" : job.status}`} />
                    <span className="jobs-card-main">
                      <strong title={job.command}>{job.command}</strong>
                      <em>
                        {t(statusKey(displayStatus(job)))} · {t("jobsElapsed", fmtElapsed(job.elapsed_sec))}
                        {active ? "" : ` · ${t("jobsTapDetail")}`}
                      </em>
                    </span>
                    <span className="jobs-chevron" aria-hidden>
                      {active ? "▾" : "▸"}
                    </span>
                    {isLive(job) ? (
                      <span
                        className="jobs-stop"
                        role="button"
                        tabIndex={0}
                        onClick={(e) => {
                          e.stopPropagation();
                          void stop(job.job_id);
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            e.stopPropagation();
                            void stop(job.job_id);
                          }
                        }}
                      >
                        {busyId === job.job_id ? "…" : t("jobsStop")}
                      </span>
                    ) : (
                      <span
                        className="jobs-remove"
                        role="button"
                        tabIndex={0}
                        title={t("jobsRemove")}
                        onClick={(e) => {
                          e.stopPropagation();
                          void remove(job.job_id);
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            e.stopPropagation();
                            void remove(job.job_id);
                          }
                        }}
                      >
                        <IconTrash size={12} />
                      </span>
                    )}
                  </button>
                  {active && detail && detail.job_id === job.job_id ? (
                    <div className="jobs-log-wrap">
                      <div className="jobs-log-head">
                        <code>{detail.job_id}</code>
                        <button
                          type="button"
                          className="text-btn"
                          onClick={() => setExpandedId("")}
                        >
                          {t("jobsCollapse")}
                        </button>
                        {isLive(detail) ? (
                          <button type="button" className="text-btn" onClick={() => void stop(detail.job_id)} disabled={busyId === detail.job_id}>
                            <IconX size={12} /> {t("jobsStop")}
                          </button>
                        ) : (
                          <button type="button" className="text-btn danger" onClick={() => void remove(detail.job_id)} disabled={busyId === detail.job_id}>
                            <IconTrash size={12} /> {t("jobsRemove")}
                          </button>
                        )}
                      </div>
                      <pre
                        ref={logRef}
                        className="jobs-log"
                        onScroll={(e) => {
                          const el = e.currentTarget;
                          stickBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
                        }}
                      >
                        {(detail.log || "").trim() || t("jobsNoLog")}
                      </pre>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
