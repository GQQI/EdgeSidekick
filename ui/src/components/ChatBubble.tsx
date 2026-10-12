import { memo } from "react";
import { FileTypeIcon, fileCardMeta } from "./FileTypeIcon";
import { IconCheck, IconCube, IconUser } from "./icons";
import { IconRobotCube } from "./IconRobotCube";
import { MarkdownView } from "./MarkdownView";
import { ThinkingBlock } from "./ThinkingBlock";
import { readFileContent } from "../api";
import type { ChatMsg, DetailView, ToolCard } from "../types/chat";
import {
  fileToDetail,
  formatTime,
  isSkillInjectMessage,
  writeFilePreview,
  streamPhaseSuffix,
  stripToolCallMarkup,
} from "../utils/chatHelpers";
import {
  formatAssistantDisplay,
  formatSpeakerLabel,
  resolveSpeakerName,
} from "../utils/assistantDisplay";
import { formatToolSummary } from "../utils/toolSummary";
import type { MsgKey } from "../i18n";

type ToolShared = {
  t: (key: MsgKey, ...args: string[]) => string;
  onSetDetail: (d: DetailView) => void;
};

type ToolChipProps = ToolShared & {
  message: ChatMsg;
  active: boolean;
};

function toolChipEqual(prev: ToolChipProps, next: ToolChipProps) {
  return prev.message === next.message && prev.active === next.active && prev.t === next.t;
}

export const ToolChip = memo(function ToolChip({
  message: m,
  active,
  onSetDetail,
}: ToolChipProps) {
  const tool = m.tool as ToolCard;
  const writePrev = tool.name === "write_file" ? writeFilePreview(tool.args) : null;
  const readPath =
    tool.name === "read_file" &&
    tool.args &&
    typeof tool.args === "object" &&
    typeof (tool.args as { path?: unknown }).path === "string"
      ? String((tool.args as { path: string }).path)
      : "";
  const filePath = writePrev?.path || readPath;
  if (
    filePath &&
    (tool.status === "done" || tool.status === "running" || tool.status === "streaming")
  ) {
    const meta = fileCardMeta(filePath);
    return (
      <button
        type="button"
        className={`file-ref-card${active ? " active" : ""}${tool.status !== "done" ? " pending" : ""}`}
        onClick={() => onSetDetail({ type: "tool", tool })}
        title={tool.summary || filePath}
      >
        <FileTypeIcon name={meta.name} />
        <span className="file-ref-body">
          <strong>{meta.name}</strong>
          <span>
            {tool.name === "read_file"
              ? tool.summary || meta.dir || filePath
              : meta.dir || filePath}
          </span>
        </span>
        {tool.status === "done" ? (
          <span className="file-ref-status ok">
            <IconCheck size={14} />
          </span>
        ) : (
          <span className="file-ref-status">…</span>
        )}
      </button>
    );
  }
  const label =
    tool.summary || formatToolSummary(tool.name || "", tool.args) || tool.name || "tool";
  return (
    <button
      type="button"
      className={`tool-chip ${tool.status}${active ? " active" : ""}`}
      onClick={() => onSetDetail({ type: "tool", tool })}
      title={label}
    >
      <span className="tool-chip-mark">
        {tool.status === "pending"
          ? "?"
          : tool.status === "streaming" || tool.status === "running"
            ? "…"
            : tool.status === "error"
              ? "!"
              : "✓"}
      </span>
      <span className="tool-chip-body">
        <span className="tool-chip-name">{tool.name || "tool"}</span>
        <span className="tool-chip-summary">{label}</span>
      </span>
      {tool.status === "pending" && <span className="tool-chip-hint">等待确认</span>}
      {tool.status === "streaming" && <span className="tool-chip-hint">生成中</span>}
    </button>
  );
}, toolChipEqual);

type BubbleProps = ToolShared & {
  busy: boolean;
  message: ChatMsg;
  editing: boolean;
  editDraft: string;
  copied: boolean;
  onCopyBubble: (id: string, text: string) => void;
  onStartEditUser: (id: string, content: string) => void;
  onEditDraftChange: (text: string) => void;
  onCancelEdit: () => void;
  onRequestSubmitEdit: (msgId: string) => void;
  onToast: (msg: string) => void;
  onCtrlClickUrl?: (url: string, clientX: number, clientY: number) => void;
};

function bubbleEqual(prev: BubbleProps, next: BubbleProps) {
  return (
    prev.message === next.message &&
    prev.editing === next.editing &&
    prev.copied === next.copied &&
    prev.busy === next.busy &&
    prev.t === next.t &&
    prev.onCtrlClickUrl === next.onCtrlClickUrl &&
    (!prev.editing || prev.editDraft === next.editDraft)
  );
}

export const ChatBubble = memo(function ChatBubble({
  message: m,
  t,
  busy,
  editing,
  editDraft,
  copied,
  onSetDetail,
  onCopyBubble,
  onStartEditUser,
  onEditDraftChange,
  onCancelEdit,
  onRequestSubmitEdit,
  onToast,
  onCtrlClickUrl,
}: BubbleProps) {
  const skillInject = m.role === "user" && isSkillInjectMessage(m.content);
  return (
    <article
      className={`bubble ${m.role}${m.streaming ? " streaming" : ""}${
        editing ? " editing" : ""
      }${skillInject ? " skill-inject" : ""}${m.jobNotice ? " job-notice" : ""}`}
    >
      <div className="bubble-head">
        <div className="role">
          <span className={`role-avatar ${skillInject ? "system" : m.role}`}>
            {skillInject ? (
              <IconCube size={18} />
            ) : m.role === "user" ? (
              <IconUser size={18} />
            ) : m.role === "system" ? (
              <IconCube size={18} />
            ) : (
              <IconRobotCube size={22} />
            )}
          </span>
          <span className="role-label">
            {skillInject
              ? t("skillInjected")
              : m.jobNotice
                ? t("navJobs")
                : m.role === "user"
                  ? t("you")
                  : m.role === "system"
                    ? t("command")
                    : m.role === "assistant" || m.role === "subagent"
                      ? formatSpeakerLabel(
                          t("assistant"),
                          m.speaker_name || resolveSpeakerName(m),
                        )
                      : t("assistant")}
            {m.streaming
              ? streamPhaseSuffix(
                  true,
                  {
                    reasoningStreaming: m.reasoningStreaming,
                    text: m.content,
                    reasoning: m.reasoning,
                  },
                  t,
                )
              : ""}
          </span>
          {m.ts && !m.streaming ? (
            <span className="bubble-time" title={new Date(m.ts).toLocaleString()}>
              {formatTime(m.ts)}
            </span>
          ) : null}
        </div>
        {!m.streaming &&
          (m.role === "user" || m.role === "assistant" || m.role === "system") && (
            <div className="bubble-actions">
              <button
                type="button"
                className="bubble-action"
                title={t("copy")}
                onClick={() => void onCopyBubble(m.id, m.content)}
              >
                {copied ? t("copied") : t("copy")}
              </button>
              {m.role === "user" && !skillInject && !editing && (
                <button
                  type="button"
                  className="bubble-action"
                  title={t("edit")}
                  disabled={busy}
                  onClick={() => onStartEditUser(m.id, m.content)}
                >
                  {t("edit")}
                </button>
              )}
            </div>
          )}
      </div>
      {m.role === "assistant" && (m.reasoning || m.reasoningStreaming) && (
        <ThinkingBlock content={m.reasoning || ""} streaming={Boolean(m.reasoningStreaming)} />
      )}
      {editing && m.role === "user" ? (
        <div className="bubble-edit">
          <textarea
            value={editDraft}
            autoFocus
            rows={Math.min(12, Math.max(3, editDraft.split("\n").length + 1))}
            onChange={(e) => onEditDraftChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                onCancelEdit();
              }
              if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                e.preventDefault();
                onRequestSubmitEdit(m.id);
              }
            }}
          />
          <div className="bubble-edit-actions">
            <button type="button" className="bubble-edit-btn cancel" onClick={onCancelEdit}>
              {t("cancel")}
            </button>
            <button
              type="button"
              className="bubble-edit-btn primary"
              disabled={!editDraft.trim() || busy}
              onClick={() => onRequestSubmitEdit(m.id)}
            >
              {t("resend")}
            </button>
          </div>
          <p className="bubble-edit-hint">{t("editHint")}</p>
        </div>
      ) : m.role === "assistant" || m.role === "system" || skillInject ? (
        m.content ? (
          <MarkdownView
            content={m.role === "assistant" ? formatAssistantDisplay(stripToolCallMarkup(m.content)) : m.content}
            streaming={m.streaming && !m.reasoningStreaming}
            onCtrlClickUrl={onCtrlClickUrl}
          />
        ) : m.streaming && !m.reasoningStreaming ? (
          <div className="plain muted">…</div>
        ) : null
      ) : (
        <div className="user-bubble-body">
          {m.attachments && m.attachments.length > 0 && (
            <ul className="bubble-attach-list">
              {m.attachments.map((a) => (
                <li key={`${a.path}:${a.name}`}>
                  <button
                    type="button"
                    className="bubble-attach-chip"
                    title={a.path}
                    onClick={() => {
                      void readFileContent(a.path)
                        .then((file) => onSetDetail(fileToDetail(file)))
                        .catch((err) =>
                          onToast(err instanceof Error ? err.message : String(err)),
                        );
                    }}
                  >
                    <FileTypeIcon name={a.name} />
                    <span className="attach-chip-text">
                      <strong>{a.name}</strong>
                      <em>{a.path}</em>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {m.content ? <div className="plain">{m.content}</div> : null}
        </div>
      )}
    </article>
  );
}, bubbleEqual);
