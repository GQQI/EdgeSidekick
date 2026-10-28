import { useEffect, useMemo, useState } from "react";
import type { ActivePlan, PlanTaskStatus } from "../types/plan";

export type { ActivePlan, PlanTask, PlanTaskStatus } from "../types/plan";

type Props = {
  plan: ActivePlan;
  subtitle: string;
  titleLabel: string;
  collapseLabel?: string;
  expandLabel?: string;
  /** Default collapsed to keep the chat viewport free. */
  defaultCollapsed?: boolean;
};

function statusClass(status: PlanTaskStatus): string {
  if (status === "done") return "done";
  if (status === "running") return "running";
  if (status === "error") return "error";
  if (status === "cancelled") return "cancelled";
  if (status === "skipped") return "skipped";
  return "pending";
}

function isFinished(status: PlanTaskStatus): boolean {
  return status === "done" || status === "skipped" || status === "cancelled";
}

function markFor(status: PlanTaskStatus): string {
  if (status === "done" || status === "skipped") return "✓";
  if (status === "running") return "…";
  if (status === "error") return "!";
  if (status === "cancelled") return "–";
  return String("");
}

export function TaskPlanPanel({
  plan,
  subtitle,
  titleLabel,
  collapseLabel = "收起",
  expandLabel = "展开",
  defaultCollapsed = true,
}: Props) {
  const statusSig = useMemo(
    () => plan.tasks.map((t) => `${t.id}:${t.status}`).join("|"),
    [plan.tasks],
  );
  const done = plan.tasks.filter((x) => isFinished(x.status)).length;
  const running = plan.tasks.find((x) => x.status === "running");
  const hasProgress = done > 0 || Boolean(running);
  const [collapsed, setCollapsed] = useState(defaultCollapsed && !hasProgress);

  // Keep the list open whenever a step is running or any step finished so
  // strikethrough updates are visible after each plan_step event.
  useEffect(() => {
    if (hasProgress) setCollapsed(false);
  }, [hasProgress, statusSig]);

  return (
    <div
      className={`plan-doc task-plan plan-doc-compact${collapsed ? " collapsed" : ""}`}
      role="region"
      aria-label={titleLabel}
      data-plan-status={statusSig}
    >
      <button
        type="button"
        className="plan-doc-head task-plan-head"
        aria-expanded={!collapsed}
        onClick={() => setCollapsed((v) => !v)}
      >
        <span className="plan-doc-head-main">
          <span className="plan-doc-badge">{titleLabel}</span>
          <strong className="plan-doc-heading">{plan.summary || titleLabel}</strong>
          <span className="plan-doc-overview">
            {subtitle}
            {running ? ` · ${running.title}` : ""}
          </span>
        </span>
        <span className="plan-doc-toggle" aria-hidden>
          {collapsed ? "▸" : "▾"}
        </span>
        <span className="sr-only">{collapsed ? expandLabel : collapseLabel}</span>
      </button>
      {!collapsed && (
        <ol className="plan-doc-list plan-doc-list-compact" key={statusSig}>
          {plan.tasks.map((task, i) => (
            <li
              key={`${task.id || i}-${task.status}`}
              className={`plan-doc-item status-${task.status}`}
              title={task.detail || ""}
            >
              <span className={`plan-index ${statusClass(task.status)}`} aria-hidden>
                {markFor(task.status) || i + 1}
              </span>
              <span className="plan-doc-text">
                <span className="plan-doc-task-title">{task.title}</span>
              </span>
            </li>
          ))}
        </ol>
      )}
      {collapsed && (
        <div className="plan-doc-mini-progress" aria-hidden>
          <span>
            {done}/{plan.tasks.length}
          </span>
        </div>
      )}
    </div>
  );
}
