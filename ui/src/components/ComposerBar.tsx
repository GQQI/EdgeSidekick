import { useRef, type RefObject } from "react";
import { AtFileMenu, stripTrailingAtQuery } from "./AtFileMenu";
import { ChangesBar } from "./ChangesBar";
import { DiffReview } from "./DiffReview";
import { FileTypeIcon } from "./FileTypeIcon";
import { IconAt, IconPlus, IconSend, IconX } from "./icons";
import { ModelSwitcher } from "./ModelSwitcher";
import { PlanConfirmDialog } from "./PlanConfirmDialog";
import { SessionBindingMenu, type SessionBinding } from "./SessionBindingMenu";
import { SlashMenu } from "./SlashMenu";
import { TaskPlanPanel } from "./TaskPlanPanel";
import type { ActivePlan } from "./TaskPlanPanel";
import type { SlashMenuItem } from "../slash/commands";
import type {
  ApprovalPrompt,
  QueuedMsg,
} from "../types/chat";
import type { FileDiffPreview } from "../utils/diffPreview";
import { isFileMutatingTool } from "../utils/diffPreview";
import { formatArgs } from "../utils/chatHelpers";
import type { ModelSetup, ModelRole } from "../types/modelSetup";
import type { PlanConfirmState } from "../types/plan";
import type { Locale, MsgKey } from "../i18n";

export type ComposerBarProps = {
  t: (key: MsgKey, ...args: string[]) => string;
  locale: Locale;
  input: string;
  setInput: (v: string | ((prev: string) => string)) => void;
  attachments: {
    id: string;
    name: string;
    path: string;
    kind: string;
    text?: string;
    size?: number;
  }[];
  setAttachments: React.Dispatch<
    React.SetStateAction<
      {
        id: string;
        name: string;
        path: string;
        kind: string;
        text?: string;
        size?: number;
      }[]
    >
  >;
  attachBusy: boolean;
  attachInputRef: RefObject<HTMLInputElement | null>;
  composerRef: RefObject<HTMLTextAreaElement | null>;
  busy: boolean;
  chatMode: "plan" | "agent";
  setChatMode: (mode: "plan" | "agent") => void;
  slashOpen: boolean;
  slashItems: SlashMenuItem[];
  slashIndex: number;
  setSlashIndex: (i: number | ((prev: number) => number)) => void;
  atFileOpen: boolean;
  atFileHits: { path: string; name: string; kind?: string; match?: string }[];
  atFileIndex: number;
  setAtFileIndex: (i: number | ((prev: number) => number)) => void;
  atFileLoading: boolean;
  queued: QueuedMsg[];
  activePlan: ActivePlan | null;
  planConfirm: PlanConfirmState | null;
  planConfirmSubmitting: boolean;
  approval: ApprovalPrompt | null;
  approvalDiff: FileDiffPreview | null;
  approvalDiffLoading: boolean;
  ctxPct: number;
  ctxWarn: boolean;
  ctx: { tokens: number; limit: number };
  model: ModelSetup | null;
  modelSwitchRole: ModelRole;
  setModelSwitchRole: (role: ModelRole) => void;
  modelSaving?: boolean;
  onSend: () => void;
  onStopChat: () => void;
  onApplySlashItem: (item: SlashMenuItem) => void;
  onApplyAtFile: (item: { path: string; name: string; kind?: string }) => void;
  onAddAttachments: (files: FileList | null) => void;
  onClearQueued: () => void;
  onRemoveQueued: (id: string) => void;
  onResolvePlanConfirm: (
    approved: boolean,
    draft?: { summary: string; tasks: ActivePlan["tasks"] },
  ) => void;
  onResolveApproval: (
    approved: boolean,
    remember?: boolean,
    patchArgs?: Record<string, unknown>,
  ) => void;
  onOpenSettings: (tab?: "workspace" | "model") => void;
  onSwitchModelRole: (role: ModelRole, providerId: string, modelId: string) => void;
  gitRefreshKey?: number;
  sessionId?: string | null;
  workspace?: string | null;
  onOpenReview?: () => void;
  sessionBinding: SessionBinding;
  onSessionBindingChange: (next: SessionBinding) => void;
};


export function ComposerBar({
  t,
  locale,
  input,
  setInput,
  attachments,
  setAttachments,
  attachBusy,
  attachInputRef,
  composerRef,
  busy,
  chatMode,
  setChatMode,
  slashOpen,
  slashItems,
  slashIndex,
  setSlashIndex,
  atFileOpen,
  atFileHits,
  atFileIndex,
  setAtFileIndex,
  atFileLoading,
  queued,
  activePlan,
  planConfirm,
  planConfirmSubmitting,
  approval,
  approvalDiff,
  approvalDiffLoading,
  ctxPct,
  ctxWarn,
  ctx,
  model,
  modelSwitchRole,
  setModelSwitchRole,
  modelSaving = false,
  onSend,
  onStopChat,
  onApplySlashItem,
  onApplyAtFile,
  onAddAttachments,
  onClearQueued,
  onRemoveQueued,
  onResolvePlanConfirm,
  onResolveApproval,
  onOpenSettings,
  onSwitchModelRole,
  gitRefreshKey = 0,
  sessionId = null,
  workspace = null,
  onOpenReview,
  sessionBinding,
  onSessionBindingChange,
}: ComposerBarProps) {
  const hunkPatchRef = useRef<Record<string, unknown> | null>(null);
  const approvalPatch = () =>
    approval?.name === "write_file" ? hunkPatchRef.current || undefined : undefined;
  return (
    <>
      {queued.length > 0 && (
        <div className="message-queue">
          <div className="message-queue-head">
            <span>{t("queuedTitle", String(queued.length))}</span>
            <button type="button" className="mini ghost" onClick={onClearQueued}>
              {t("queueClear")}
            </button>
          </div>
          <ul className="message-queue-list">
            {queued.map((q, i) => (
              <li key={q.id}>
                <span className="message-queue-idx">{i + 1}</span>
                <span className="message-queue-text">
                  {q.userDisplay ||
                    (q.attachments?.length
                      ? q.attachments.map((a) => a.name).join(locale === "en" ? ", " : "、")
                      : q.text)}
                  {q.attachments && q.attachments.length > 0
                    ? ` · ${q.attachments.length}${locale === "en" ? " files" : " 个附件"}`
                    : ""}
                </span>
                <button
                  type="button"
                  className="fe-inline-btn cancel"
                  title={t("queueRemove")}
                  onClick={() => onRemoveQueued(q.id)}
                >
                  ✕                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {activePlan && !planConfirm && activePlan.tasks.length > 0 && (
          <TaskPlanPanel
            key={`${activePlan.planId}-${activePlan.tasks.map((x) => x.status).join(".")}`}
            plan={activePlan}
            titleLabel={t("taskPlanTitle")}
            subtitle={t(
              "taskPlanProgress",
              String(
                activePlan.tasks.filter(
                  (x) => x.status === "done" || x.status === "skipped" || x.status === "cancelled",
                ).length,
              ),
              String(activePlan.tasks.length),
            )}
            collapseLabel={t("planCollapse")}
            expandLabel={t("planExpand")}
            defaultCollapsed={false}
          />
        )}
      {planConfirm && (
        <PlanConfirmDialog
          key={planConfirm.planId}
          summary={planConfirm.summary}
          tasks={planConfirm.tasks}
          titleLabel={t("planConfirmNeeded")}
          dialogLabel={t("planDialog")}
          approveLabel={t("planApprove")}
          rejectLabel={t("planReject")}
          addTaskLabel={t("planAddTask")}
          submitting={planConfirmSubmitting}
          onApprove={(draft) => void onResolvePlanConfirm(true, draft)}
          onReject={() => void onResolvePlanConfirm(false)}
        />
      )}
      {approval && (
        <div className="inline-approval" role="dialog" aria-label={t("approvalDialog")}>
          <div className="inline-approval-top">
            <div className="inline-approval-head">
              <div>
                <div className="inline-approval-title">{t("approvalNeeded")}</div>
                <div className="inline-approval-tool">{approval.name}</div>
              </div>
              <button
                type="button"
                className="icon-btn inline-approval-dismiss"
                title={t("approvalDismiss")}
                onClick={() => void onResolveApproval(false)}
              >
                ✕              </button>
            </div>
            <p className="inline-approval-summary">{approval.summary}</p>
          </div>
          {isFileMutatingTool(approval.name) ? (
            <div className="inline-approval-preview">
              <DiffReview
                diff={approvalDiff}
                loading={approvalDiffLoading}
                compact
                selectable={approval.name === "write_file"}
                acceptHunkLabel={t("diffAcceptHunk")}
                title={t("diffPreview")}
                newFileLabel={t("diffNewFile")}
                deletedFileLabel={t("diffDeletedFile")}
                truncatedLabel={t("diffTruncated")}
                emptyLabel={t("diffEmpty")}
                alreadyAppliedLabel={t("diffAlreadyApplied")}
                snippetLabel={t("diffSnippet")}
                onPatchChange={(content) => {
                  hunkPatchRef.current = content != null ? { content } : null;
                }}
              />
            </div>
          ) : (
            <pre className="code-fence inline-approval-args">{formatArgs(approval.args)}</pre>
          )}
          <div className="inline-approval-actions">
            <button
              type="button"
              className="approval-btn reject"
              onClick={() => void onResolveApproval(false)}
            >
              {t("approvalReject")}
            </button>
            <button
              type="button"
              className="approval-btn once"
              onClick={() => void onResolveApproval(true, false, approvalPatch())}
            >
              {t("approvalOnce")}
            </button>
            <button
              type="button"
              className="approval-btn allow"
              onClick={() => void onResolveApproval(true, true, approvalPatch())}
            >
              {t("approvalAllowClass")}
            </button>
          </div>
        </div>
      )}
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          void onSend();
        }}
      >
        <div className="composer-row">
          <div className="composer-input-wrap">
            {slashOpen && (
              <SlashMenu
                items={slashItems}
                activeIndex={Math.min(slashIndex, Math.max(0, slashItems.length - 1))}
                onHover={setSlashIndex}
                onSelect={onApplySlashItem}
              />
            )}
            {atFileOpen && !slashOpen && (
              <AtFileMenu
                items={atFileHits}
                activeIndex={Math.min(atFileIndex, Math.max(0, atFileHits.length - 1))}
                loading={atFileLoading}
                emptyLabel={t("atFileEmpty")}
                menuLabel={t("atFileMenu")}
                onHover={setAtFileIndex}
                onSelect={(item) => void onApplyAtFile(item)}
              />
            )}
            <ChangesBar
              t={t}
              refreshKey={gitRefreshKey}
              sessionId={sessionId}
              workspace={workspace}
              onOpenReview={onOpenReview}
            />
            <div className="composer-input-inner">
              <textarea
                ref={composerRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onCompositionEnd={(e) => {
                  setInput(e.currentTarget.value);
                }}
                placeholder={busy ? t("composerBusy") : t("composerPlaceholder")}
                rows={3}
                onKeyDown={(e) => {
                  if (
                    (e.key === "/" || e.key === "／") &&
                    !e.ctrlKey &&
                    !e.metaKey &&
                    !e.altKey &&
                    !input.trim()
                  ) {
                    return;
                  }
                  if (slashOpen && slashItems.length > 0) {
                    if (e.key === "ArrowDown") {
                      e.preventDefault();
                      setSlashIndex((i) => (i + 1) % slashItems.length);
                      return;
                    }
                    if (e.key === "ArrowUp") {
                      e.preventDefault();
                      setSlashIndex((i) => (i - 1 + slashItems.length) % slashItems.length);
                      return;
                    }
                    if (e.key === "Tab") {
                      e.preventDefault();
                      onApplySlashItem(slashItems[Math.min(slashIndex, slashItems.length - 1)]);
                      return;
                    }
                    if (e.key === "Escape") {
                      e.preventDefault();
                      setInput("");
                      return;
                    }
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      const cur = input.trim();
                      if (/\s/.test(cur)) {
                        void onSend();
                        return;
                      }
                      onApplySlashItem(slashItems[Math.min(slashIndex, slashItems.length - 1)]);
                      return;
                    }
                  }
                  if (atFileOpen && atFileHits.length > 0) {
                    if (e.key === "ArrowDown") {
                      e.preventDefault();
                      setAtFileIndex((i) => (i + 1) % atFileHits.length);
                      return;
                    }
                    if (e.key === "ArrowUp") {
                      e.preventDefault();
                      setAtFileIndex((i) => (i - 1 + atFileHits.length) % atFileHits.length);
                      return;
                    }
                    if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
                      e.preventDefault();
                      void onApplyAtFile(atFileHits[Math.min(atFileIndex, atFileHits.length - 1)]);
                      return;
                    }
                    if (e.key === "Escape") {
                      e.preventDefault();
                      setInput(stripTrailingAtQuery(input));
                      return;
                    }
                  }
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void onSend();
                  }
                }}
              />
              <div className="composer-toolbar">
                <div className="composer-tools">
                  <div className="mode-toggle" role="group" aria-label="chat mode">
                    <button
                      type="button"
                      className={`mode-btn${chatMode === "plan" ? " active" : ""}`}
                      title={t("modePlanHint")}
                      disabled={busy}
                      onClick={() => setChatMode("plan")}
                    >
                      {t("modePlan")}
                    </button>
                    <button
                      type="button"
                      className={`mode-btn${chatMode === "agent" ? " active" : ""}`}
                      title={t("modeAgentHint")}
                      disabled={busy}
                      onClick={() => setChatMode("agent")}
                    >
                      {t("modeAgent")}
                    </button>
                  </div>
                  <SessionBindingMenu
                    t={t}
                    value={sessionBinding}
                    onChange={onSessionBindingChange}
                    disabled={busy}
                  />
                  <button
                    type="button"
                    className="composer-tool"
                    title={t("composerAt")}
                    onClick={() => {
                      setInput((v) => {
                        if (atFileOpen) return v;
                        if (!v || /[\s\n]$/.test(v)) return `${v}@`;
                        return `${v} @`;
                      });
                      requestAnimationFrame(() => composerRef.current?.focus());
                    }}
                  >
                    <IconAt size={16} />
                  </button>
                  <button
                    type="button"
                    className="composer-tool label"
                    title={t("composerAttachHint")}
                    disabled={attachBusy}
                    onClick={() => attachInputRef.current?.click()}
                  >
                    <IconPlus size={16} />
                    <span>{t("composerAttach")}</span>
                  </button>
                  <input
                    ref={attachInputRef}
                    type="file"
                    multiple
                    hidden
                    onChange={(e) => void onAddAttachments(e.target.files)}
                  />
                </div>
                <div className="composer-actions">
                  {busy ? (
                    <>
                      {input.trim() || attachments.length ? (
                        <button type="submit" className="composer-send queue">
                          {t("queue")}
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="composer-send stop"
                          onClick={() => void onStopChat()}
                        >
                          {t("stop")}
                        </button>
                      )}
                    </>
                  ) : (
                    <button
                      type="submit"
                      className="composer-send"
                      disabled={!input.trim() && attachments.length === 0}
                    >
                      <IconSend size={15} />
                      <span>{t("send")}</span>
                    </button>
                  )}
                </div>
              </div>
              {attachments.length > 0 && (
                <ul className="attach-list">
                  {attachments.map((a) => (
                    <li key={a.id} className="attach-chip">
                      <FileTypeIcon name={a.name} />
                      <span className="attach-chip-text">
                        <strong>{a.name}</strong>
                        <em>{a.path}</em>
                      </span>
                      <button
                        type="button"
                        className="attach-remove"
                        title={t("attachRemove")}
                        onClick={() => setAttachments((prev) => prev.filter((x) => x.id !== a.id))}
                      >
                        <IconX size={12} />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
        <div className="status-bar" aria-label="会话状态">
          <div
            className={`ctx-meter ${ctxWarn ? "warn" : ""}`}
            title={`${t("context")} ${ctx.tokens} / ${ctx.limit} tokens`}
          >
            <div className="ctx-meter-bar" style={{ width: `${ctxPct}%` }} />
            <span className="ctx-meter-label">
              {t("context")} {ctx.tokens}/{ctx.limit} · {ctxPct}%
            </span>
          </div>
          <ModelSwitcher
            setup={model}
            locale={locale}
            role={modelSwitchRole}
            saving={modelSaving}
            onRoleChange={setModelSwitchRole}
            onSelect={(role, providerId, modelId) =>
              void onSwitchModelRole(role, providerId, modelId)
            }
            onOpenSettings={() => onOpenSettings("model")}
            t={t}
          />
        </div>
      </form>
    </>
  );
}
