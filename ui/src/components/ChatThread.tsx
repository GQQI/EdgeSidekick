import { memo, useEffect, useRef, useState, type RefObject } from "react";
import { AgentCanvas } from "./AgentCanvas";
import {
  expandAssistantMessages,
} from "../utils/assistantDisplay";
import { AskDialog } from "./AskDialog";
import { ChatBubble, ToolChip } from "./ChatBubble";
import { IconRobotCube } from "./IconRobotCube";
import type { AskPrompt, ChatMsg, DetailView, SubNode } from "../types/chat";
import { ASK_CUSTOM_KEY } from "../types/chat";
import {
  buildSuggestions,
  greetingKey,
  groupCanvasRootsByStage,
} from "../utils/chatHelpers";
import type { MsgKey } from "../i18n";

export type ChatThreadProps = {
  t: (key: MsgKey, ...args: string[]) => string;
  messages: ChatMsg[];
  busy: boolean;
  stopping: boolean;
  queuedCount: number;
  compressState: {
    active: boolean;
    message: string;
    attempt: number;
    maxAttempts: number;
    before: number;
    after?: number;
  } | null;
  detail: DetailView;
  editingId: string | null;
  editDraft: string;
  copiedId: string | null;
  threadRef: RefObject<HTMLDivElement | null>;
  bottomRef: RefObject<HTMLDivElement | null>;
  onSetDetail: (d: DetailView) => void;
  onSend: (text?: string) => void;
  onStopChat: () => void;
  onCopyBubble: (id: string, text: string) => void;
  onStartEditUser: (id: string, content: string) => void;
  onEditDraftChange: (text: string) => void;
  onCancelEdit: () => void;
  onRequestSubmitEdit: (msgId: string) => void;
  onToast: (msg: string) => void;
  onCtrlClickUrl?: (url: string, clientX: number, clientY: number) => void;
  askPrompt?: AskPrompt | null;
  askChoice?: string;
  askOtherText?: string;
  askSubmitting?: boolean;
  onResolveAsk?: (choice: string, otherText?: string) => void;
  onAskChoice?: (key: string) => void;
  onAskOtherText?: (text: string) => void;
  runningJobs?: { job_id: string; command: string; elapsed_sec?: number }[];
  onOpenJobs?: (jobId?: string) => void;
  /** Optional names for speaker-label inference only (not shown as a roster). */
  knownSpeakers?: string[];
};

function useCanvasByStage(messages: ChatMsg[]) {
  const cacheRef = useRef<{ nodes: SubNode[]; map: Map<number, SubNode[]> } | null>(null);
  const nodes: SubNode[] = [];
  for (const m of messages) {
    if (m.role === "subagent" && m.subagent) nodes.push(m.subagent);
  }
  const prev = cacheRef.current;
  if (prev && prev.nodes.length === nodes.length && prev.nodes.every((n, i) => n === nodes[i])) {
    return prev.map;
  }
  const map = groupCanvasRootsByStage(messages);
  cacheRef.current = { nodes, map };
  return map;
}

export const ChatThread = memo(function ChatThread({
  t,
  messages,
  busy,
  stopping,
  queuedCount,
  compressState,
  detail,
  editingId,
  editDraft,
  copiedId,
  threadRef,
  bottomRef,
  onSetDetail,
  onSend,
  onStopChat,
  onCopyBubble,
  onStartEditUser,
  onEditDraftChange,
  onCancelEdit,
  onRequestSubmitEdit,
  onToast,
  onCtrlClickUrl,
  askPrompt,
  askChoice = "",
  askOtherText = "",
  askSubmitting = false,
  onResolveAsk,
  onAskChoice,
  onAskOtherText,
  runningJobs = [],
  onOpenJobs,
  knownSpeakers = [],
}: ChatThreadProps) {
  const displayMessages = messages.flatMap((m) => expandAssistantMessages(m, knownSpeakers));
  const canvasByStage = useCanvasByStage(messages);
  const canvasShown = new Set<number>();
  let hasStreaming = false;
  const runningKey = runningJobs.map((job) => job.job_id).join("\n");
  const announcedJobs = useRef(new Set<string>());
  const [jobsBannerOn, setJobsBannerOn] = useState(false);
  useEffect(() => {
    const ids = runningKey.split("\n").filter(Boolean);
    const fresh = ids.filter((id) => !announcedJobs.current.has(id));
    if (!fresh.length) return;
    for (const id of fresh) announcedJobs.current.add(id);
    setJobsBannerOn(true);
    const timer = window.setTimeout(() => setJobsBannerOn(false), 3000);
    return () => window.clearTimeout(timer);
  }, [runningKey]);

  return (
    <>
      {jobsBannerOn && runningJobs.length > 0 && (
        <button
          type="button"
          className="jobs-banner"
          onClick={() => onOpenJobs?.(runningJobs[0]?.job_id)}
        >
          <span className="jobs-banner-dot" />
          <strong>{t("jobsBanner")}</strong>
          <span>
            {runningJobs[0]?.command || ""}
            {runningJobs.length > 1 ? ` · +${runningJobs.length - 1}` : ""}
          </span>
          <em>{t("jobsOpen")}</em>
        </button>
      )}
      {compressState?.active && (
        <div className="compress-banner">
          <div className="compress-banner-text">
            <strong>
              {compressState.after != null ? t("compressReset") : t("compressResetting")}
            </strong>
            <span>{compressState.message}</span>
          </div>
          <div className="compress-track">
            <div
              className="compress-fill"
              style={{
                width:
                  compressState.after != null
                    ? "100%"
                    : `${Math.min(
                        95,
                        Math.round(
                          (Math.max(1, compressState.attempt) /
                            Math.max(1, compressState.maxAttempts)) *
                            100,
                        ),
                      )}%`,
              }}
            />
          </div>
          <div className="compress-meta">
            {compressState.after != null
              ? `${compressState.before} → ${compressState.after}`
              : `第 ${compressState.attempt || 1}/${compressState.maxAttempts} 轮`}
          </div>
        </div>
      )}
            <div className="thread" ref={threadRef}>
        <div className="thread-inner">
{messages.length === 0 && !busy && (
            <div className="empty">
              <div className="empty-hero">
                <span className="empty-hero-icon">
                  <IconRobotCube size={40} />
                </span>
                <h3>{t(greetingKey())}</h3>
                <p>{t("tagline")}</p>
              </div>
              <div className="suggestions">
                {buildSuggestions(t).map((s) => (
                  <button key={s.text} type="button" onClick={() => void onSend(s.text)}>
                    {s.label}
                  </button>
                ))}
              </div>
            </div>
          )}
          {displayMessages.map((m) => {
            if (m.streaming) hasStreaming = true;
            if (m.role === "tool" && m.tool) {
              const active = detail?.type === "tool" && detail.tool.callId === m.tool.callId;
              return (
                <ToolChip
                  key={m.id}
                  message={m}
                  active={active}
                  t={t}
                  onSetDetail={onSetDetail}
                />
              );
            }
            if (m.role === "subagent" && m.subagent) {
              const stage = m.stage ?? 0;
              if (canvasShown.has(stage)) return null;
              canvasShown.add(stage);
              const group = canvasByStage.get(stage) || [];
              return (
                <AgentCanvas
                  key={`canvas-${stage}-${m.id}`}
                  nodes={group}
                  t={t}
                  detail={detail}
                  onSetDetail={onSetDetail}
                />
              );
            }
            return (
              <ChatBubble
                key={m.id}
                message={m}
                t={t}
                busy={busy}
                editing={editingId === m.id}
                editDraft={editingId === m.id ? editDraft : ""}
                copied={copiedId === m.id}
                onSetDetail={onSetDetail}
                onCopyBubble={onCopyBubble}
                onStartEditUser={onStartEditUser}
                onEditDraftChange={onEditDraftChange}
                onCancelEdit={onCancelEdit}
                onRequestSubmitEdit={onRequestSubmitEdit}
                onToast={onToast}
                onCtrlClickUrl={onCtrlClickUrl}
              />
            );
          })}
          {askPrompt && onResolveAsk ? (
            <div className="thread-ask">
              <AskDialog
                question={askPrompt.question}
                options={askPrompt.options}
                allowCustom={askPrompt.allowCustom}
                customLabel={askPrompt.customLabel}
                choice={askChoice}
                otherText={askOtherText}
                submitting={askSubmitting}
                titleLabel={t("askNeeded")}
                dialogLabel={t("askDialog")}
                submitLabel={t("askSubmit")}
                otherPlaceholder={t("askOtherPlaceholder")}
                onPick={(key) => {
                  onAskChoice?.(key);
                  void onResolveAsk(key);
                }}
                onOtherChange={(text) => {
                  onAskChoice?.(ASK_CUSTOM_KEY);
                  onAskOtherText?.(text);
                }}
                onOtherFocus={() => onAskChoice?.(ASK_CUSTOM_KEY)}
                onSubmitCustom={() => void onResolveAsk(ASK_CUSTOM_KEY, askOtherText)}
              />
            </div>
          ) : null}
          {busy && !hasStreaming && (
            <div className="typing-row">
              <span className="typing">
                {stopping
                  ? t("stoppingNow")
                  : queuedCount
                    ? t("busyWorkingQueued", String(queuedCount))
                    : t("busyWorking")}
              </span>
              <button type="button" className="stop-btn" onClick={() => void onStopChat()}>
                {t("stop")}
              </button>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </div>
    </>
  );
});
