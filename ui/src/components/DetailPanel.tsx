import { useEffect, useState } from "react";
import { FileHighlightView, FileTextEditor, countTextLines } from "./FileHighlightView";
import { DiffReview } from "./DiffReview";
import { ReviewPanel } from "./ChangesBar";
import { LinkifiedText } from "./LinkifiedText";
import { MarkdownView } from "./MarkdownView";
import { ThinkingBlock } from "./ThinkingBlock";
import { IconX } from "./icons";
import { isFileMutatingTool, type FileDiffPreview } from "../utils/diffPreview";
import {
  formatArgs,
  formatBytes,
  writeFilePreview,
  subagentDisplayName,
  streamPhaseSuffix,
  stripToolCallMarkup,
  findSubNodeInMessages,
  isEphemeralCanvasId,
} from "../utils/chatHelpers";
import { canvasGoalsMatch } from "../utils/canvasSlots";
import { sanitizeBrowserUrl } from "../browser/urlDetect";
import { downloadAuthedFile, fetchAuthedBlob } from "../api";
import type { ChatMsg, DetailView, SubNode } from "../types/chat";
import type { Locale, MsgKey } from "../i18n";

type UrlPick = { url: string; x: number; y: number };

type Props = {
  t: (key: MsgKey, ...args: string[]) => string;
  locale: Locale;
  detail: NonNullable<DetailView>;
  detailWidth: number;
  detailDiff: FileDiffPreview | null;
  detailDiffLoading: boolean;
  fsRefresh: number;
  sessionId: string | null;
  workspace?: string | null;
  /** Live message list so subagent detail tracks transcript updates. */
  messages?: ChatMsg[];
  onResizeStart: () => void;
  onClose: () => void;
  onChange: (next: NonNullable<DetailView>) => void;
  onSaveFile: () => void;
  onPickUrl: (pick: UrlPick) => void;
};

function liveSubagent(
  snapshot: SubNode,
  messages: ChatMsg[] | undefined,
): SubNode {
  if (!messages?.length) return snapshot;
  const exact = findSubNodeInMessages(messages, snapshot.id);
  if (exact) return exact;
  // Click may still hold pending:… while the forest already adopted real child_id.
  if (!isEphemeralCanvasId(snapshot.id)) return snapshot;
  for (const m of messages) {
    if (m.role !== "subagent" || !m.subagent) continue;
    const stack: SubNode[] = [m.subagent];
    while (stack.length) {
      const n = stack.pop()!;
      if (!isEphemeralCanvasId(n.id) && canvasGoalsMatch(snapshot.goal, n.goal)) {
        return n;
      }
      if (n.children?.length) stack.push(...n.children);
    }
  }
  return snapshot;
}

function toolStatusLabel(
  t: Props["t"],
  status: string,
): string {
  if (status === "streaming") return t("toolStatusStreaming");
  if (status === "running" || status === "pending") return t("toolStatusRunning");
  if (status === "error") return t("toolStatusError");
  if (status === "done") return t("toolStatusDone");
  return status;
}

export function DetailPanel({
  t,
  locale,
  detail,
  detailWidth,
  detailDiff,
  detailDiffLoading,
  fsRefresh,
  sessionId,
  workspace = null,
  messages,
  onResizeStart,
  onClose,
  onChange,
  onSaveFile,
  onPickUrl,
}: Props) {
  const pickUrl = (url: string, x: number, y: number) => {
    onPickUrl({ url: sanitizeBrowserUrl(url) || url, x, y });
  };

  const subagentLive =
    detail.type === "subagent" ? liveSubagent(detail.subagent, messages) : null;

  let title = "";
  if (detail.type === "tool") title = `${t("detailTool")} · ${detail.tool.name}`;
  else if (detail.type === "ask") title = "ask";
  else if (detail.type === "subagent") {
    title = `${t("detailSubagent")} · ${subagentDisplayName(subagentLive || detail.subagent)}`;
  } else if (detail.type === "changes") title = t("gitReviewList");
  else title = detail.path;

  return (
    <>
      <div
        className="sidebar-resizer detail-resizer"
        onMouseDown={onResizeStart}
        title="拖拽调整预览宽度"
      />
      <aside className="detail-panel" style={{ width: detailWidth }}>
        <div className="detail-head">
          <h3>{title}</h3>
          <div className="detail-actions">
            {detail.type === "file" &&
              detail.kind === "text" &&
              detail.editable &&
              !detail.forceEdit &&
              !detail.dirty && (
                <button
                  type="button"
                  className="mini"
                  onClick={() => onChange({ ...detail, forceEdit: true })}
                >
                  {locale === "en" ? "Edit" : "编辑"}
                </button>
              )}
            {detail.type === "file" && detail.editable && (
              <button type="button" className="mini" disabled={!detail.dirty} onClick={onSaveFile}>
                {locale === "en" ? "Save" : "保存"}
              </button>
            )}
            {detail.type === "file" && detail.rawUrl && detail.kind !== "text" && (
              <button
                type="button"
                className="mini linkish"
                onClick={() => {
                  void downloadAuthedFile(detail.rawUrl || "", detail.path).catch(() => undefined);
                }}
              >
                {locale === "en" ? "Open / Download" : "打开/下载"}
              </button>
            )}
            <button
              type="button"
              className="icon-btn detail-close"
              title={t("detailClose")}
              aria-label={t("detailClose")}
              onClick={onClose}
            >
              <IconX size={16} />
            </button>
          </div>
        </div>
        {detail.type === "tool" ? (
          <ToolDetail
            t={t}
            locale={locale}
            detail={detail}
            detailDiff={detailDiff}
            detailDiffLoading={detailDiffLoading}
            onPickUrl={pickUrl}
          />
        ) : detail.type === "ask" ? (
        <AskEdgeDetail t={t} detail={detail} />
      ) : detail.type === "subagent" && subagentLive ? (
          <SubagentDetail
            t={t}
            detail={{ type: "subagent", subagent: subagentLive }}
            onPickUrl={pickUrl}
            onChange={onChange}
          />
        ) : detail.type === "changes" ? (
          <ReviewPanel
            t={t}
            refreshKey={fsRefresh}
            sessionId={sessionId}
            workspace={workspace}
            selectedPath={detail.selectedPath}
            onSelectPath={(path) => onChange({ type: "changes", selectedPath: path })}
          />
        ) : detail.type === "file" ? (
          <FileDetail detail={detail} onChange={onChange} />
        ) : null}
      </aside>
    </>
  );
}

function ToolDetail({
  t,
  locale,
  detail,
  detailDiff,
  detailDiffLoading,
  onPickUrl,
}: {
  t: Props["t"];
  locale: Locale;
  detail: Extract<NonNullable<DetailView>, { type: "tool" }>;
  detailDiff: FileDiffPreview | null;
  detailDiffLoading: boolean;
  onPickUrl: (url: string, x: number, y: number) => void;
}) {
  const mutating = Boolean(detail.tool.name && isFileMutatingTool(detail.tool.name));
  const preview = mutating && detail.tool.status === "streaming" ? writeFilePreview(detail.tool.args) : null;

  return (
    <div className="detail-body">
      <div className="detail-meta">
        {t("detailStatus")}
        {toolStatusLabel(t, detail.tool.status)}
      </div>
      {mutating ? (
        detail.tool.status === "streaming" ? (
          <>
            <div className="detail-meta">
              {t("toolStatusStreaming")}
              {preview?.path ? ` · ${preview.path}` : ""}
            </div>
            <h4>{preview ? (locale === "en" ? "Content" : "内容") : t("detailArgs")}</h4>
            <pre className="code-fence detail-stream">
              {preview ? preview.content : detail.tool.argsRaw || formatArgs(detail.tool.args)}
            </pre>
          </>
        ) : (
          <>
            <h4>{t("diffPreview")}</h4>
            <DiffReview
              diff={detailDiff}
              loading={detailDiffLoading}
              title={t("diffPreview")}
              newFileLabel={t("diffNewFile")}
              deletedFileLabel={t("diffDeletedFile")}
              truncatedLabel={t("diffTruncated")}
              emptyLabel={t("diffEmpty")}
              alreadyAppliedLabel={t("diffAlreadyApplied")}
              snippetLabel={t("diffSnippet")}
            />
          </>
        )
      ) : (
        <>
          <h4>{t("detailArgs")}</h4>
          <pre className="code-fence">
            {detail.tool.argsRaw && detail.tool.status === "streaming"
              ? detail.tool.argsRaw
              : formatArgs(detail.tool.args)}
          </pre>
        </>
      )}
      {detail.tool.status !== "streaming" || !mutating ? (
        <>
          <h4>{t("detailOutput")}</h4>
          <LinkifiedText
            className="code-fence"
            text={
              detail.tool.result ||
              (detail.tool.status === "streaming"
                ? t("toolArgsStreaming")
                : detail.tool.status === "running" || detail.tool.status === "pending"
                  ? t("toolRunning")
                  : t("toolNoOutput"))
            }
            onCtrlClickUrl={onPickUrl}
          />
        </>
      ) : null}
    </div>
  );
}


function AskEdgeDetail({
  t,
  detail,
}: {
  t: Props["t"];
  detail: Extract<NonNullable<DetailView>, { type: "ask" }>;
}) {
  const ask = detail.ask;
  const vis = ask.visibility || {};
  return (
    <div className="detail-body ask-edge-detail">
      <div className="detail-meta">
        {(ask.kind || "consult").toUpperCase()}
        {ask.status ? ` · ${ask.status}` : ""}
      </div>
      <h3 className="ask-edge-title">{ask.need || ask.goal_slice || "Ask"}</h3>
      <div className="ask-edge-grid">
        <div><span className="ask-k">what</span>{ask.what || "—"}</div>
        <div><span className="ask-k">how</span>{ask.how || "—"}</div>
        <div><span className="ask-k">who</span>{ask.who || "—"}</div>
        <div><span className="ask-k">when</span>{ask.when || "—"}</div>
      </div>
      {ask.why ? <p className="ask-edge-why"><span className="ask-k">why</span>{ask.why}</p> : null}
      <div className="ask-edge-vis">
        <div className="ask-k">visibility.share</div>
        <div className="ask-vis-tags">
          {(vis.share || []).length
            ? (vis.share || []).map((s) => (
                <span key={s} className="ask-tag share">{s}</span>
              ))
            : "—"}
        </div>
        <div className="ask-k">visibility.redact</div>
        <div className="ask-vis-tags">
          {(vis.redact || []).length
            ? (vis.redact || []).map((s) => (
                <span key={s} className="ask-tag redact">{s}</span>
              ))
            : "—"}
        </div>
        {vis.quote_policy ? (
          <div className="ask-edge-quote">quote: {vis.quote_policy}</div>
        ) : null}
      </div>
      {ask.summary ? (
        <div className="ask-edge-summary">
          <div className="ask-k">reply</div>
          <pre>{ask.summary}</pre>
        </div>
      ) : null}
      <div className="ask-edge-ids">
        <span>from: {ask.from_id || "lead"}</span>
        <span>to: {ask.to_id || "—"}</span>
      </div>
    </div>
  );
}

function SubagentDetail({
  t,
  detail,
  onPickUrl,
  onChange,
}: {
  t: Props["t"];
  detail: Extract<NonNullable<DetailView>, { type: "subagent" }>;
  onPickUrl: (url: string, x: number, y: number) => void;
  onChange: (next: NonNullable<DetailView>) => void;
}) {
  return (
    <div className="detail-body subagent-detail">
      <div className="detail-meta">
        {detail.subagent.status === "running"
          ? t("toolStatusRunning")
          : detail.subagent.status === "error"
            ? t("toolStatusFailStop")
            : t("subagentDone")}
        {detail.subagent.activity ? ` · ${detail.subagent.activity}` : ""}
      </div>
      <p className="subagent-detail-goal">{detail.subagent.goal}</p>
      {(detail.subagent.children || []).length > 0 ? (
        <div className="subagent-children detail-subagent-children">
          {detail.subagent.children!.map((child) => (
            <button
              key={child.id}
              type="button"
              className={`subagent-card nested ${child.status}`}
              onClick={() => onChange({ type: "subagent", subagent: child })}
            >
              <div className="subagent-card-head">
                <span className="subagent-card-badge">
                  {"Ask"}
                  {child.role ? ` · ${child.role}` : ""}
                </span>
                <span className="subagent-card-status">
                  {child.status === "running"
                    ? t("subtaskRunning")
                    : child.status === "error"
                      ? t("toolStatusError")
                      : t("toolStatusDone")}
                </span>
              </div>
              <div className="subagent-card-goal">{child.goal}</div>
            </button>
          ))}
        </div>
      ) : null}
      <div className="subagent-detail-thread">
        {(detail.subagent.transcript || []).length === 0 && (
          <p className="hint">
            {detail.subagent.status === "running"
              ? t("thinkingActivity")
              : t("subagentWaiting")}
          </p>
        )}
        {(detail.subagent.transcript || []).map((item) => {
          if (item.kind === "assistant") {
            return (
              <article
                key={item.id}
                className={`bubble assistant${item.streaming ? " streaming" : ""}`}
              >
                <div className="role">
                  {subagentDisplayName(detail.subagent, t("subagentLabel"))}
                  {streamPhaseSuffix(Boolean(item.streaming), {
                    reasoningStreaming: item.reasoningStreaming,
                    text: item.text,
                    reasoning: item.reasoning,
                  }, t)}
                </div>
                {(item.reasoning || item.reasoningStreaming) && (
                  <ThinkingBlock
                    content={item.reasoning || ""}
                    streaming={Boolean(item.reasoningStreaming)}
                  />
                )}
                {item.text ? (
                  <MarkdownView
                    content={stripToolCallMarkup(item.text)}
                    streaming={item.streaming && !item.reasoningStreaming}
                    onCtrlClickUrl={onPickUrl}
                  />
                ) : null}
              </article>
            );
          }
          const tool = item.tool;
          return (
            <div key={item.id} className={`tool-chip ${tool.status}`} title={tool.summary}>
              <span className="tool-chip-mark">
                {tool.status === "pending"
                  ? "?"
                  : tool.status === "streaming" || tool.status === "running"
                    ? "…"
                    : tool.status === "error"
                      ? "!"
                      : "…"}
              </span>
              <span className="tool-chip-body">
                <span className="tool-chip-name">{tool.name}</span>
                <span className="tool-chip-summary">{tool.summary}</span>
              </span>
              {tool.result && (
                <LinkifiedText
                  className="subagent-tool-result"
                  text={tool.result.slice(0, 2000)}
                  onCtrlClickUrl={onPickUrl}
                />
              )}
            </div>
          );
        })}
      </div>
      {detail.subagent.summary && detail.subagent.status !== "running" && (
        <>
          <h4>{t("subagentFinalSummary")}</h4>
          <pre className="code-fence">{detail.subagent.summary}</pre>
        </>
      )}
    </div>
  );
}

function useAuthedObjectUrl(apiPath: string | undefined, enabled: boolean) {
  const [objectUrl, setObjectUrl] = useState("");
  useEffect(() => {
    if (!enabled || !apiPath) {
      setObjectUrl("");
      return;
    }
    let cancelled = false;
    let created = "";
    void fetchAuthedBlob(apiPath)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        created = url;
        setObjectUrl(url);
      })
      .catch(() => {
        if (!cancelled) setObjectUrl("");
      });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [apiPath, enabled]);
  return objectUrl;
}

function FileDetail({
  detail,
  onChange,
}: {
  detail: Extract<NonNullable<DetailView>, { type: "file" }>;
  onChange: (next: NonNullable<DetailView>) => void;
}) {
  const media = detail.kind === "image" || detail.kind === "pdf" || detail.kind === "audio" || detail.kind === "video";
  const objectUrl = useAuthedObjectUrl(detail.rawUrl, media);
  return (
    <div className="detail-body file-preview">
      <div className="detail-meta">
        {detail.kind}
        {detail.mime ? ` · ${detail.mime}` : ""}
        {detail.size != null ? ` · ${formatBytes(detail.size)}` : ""}
        {detail.kind === "text" ? ` · ${countTextLines(detail.content || "")} lines` : ""}
      </div>
      {detail.kind === "image" && objectUrl && (
        <div className="media-frame">
          <img src={objectUrl} alt={detail.path} />
        </div>
      )}
      {detail.kind === "pdf" && objectUrl && (
        <iframe className="pdf-frame" title={detail.path} src={objectUrl} />
      )}
      {detail.kind === "audio" && objectUrl && (
        <audio className="media-player" controls src={objectUrl} />
      )}
      {detail.kind === "video" && objectUrl && (
        <video className="media-player" controls src={objectUrl} />
      )}
      {detail.kind === "document" && (
        <div className="office-preview">
          <p className="hint">
            {detail.message || "文本预览（非完整排版）"}。需要原文件请点「打开/下载」。
          </p>
          <pre className="code-fence office-text">{detail.preview || "（无文字内容）"}</pre>
        </div>
      )}
      {detail.kind === "unsupported" && (
        <div className="unsupported-preview">
          <p className="hint unsupported-msg">{detail.message || "暂不支持预览此文件"}</p>
          {detail.rawUrl && <p className="hint">可使用「打开/下载」在系统中查看原文件。</p>}
        </div>
      )}
      {detail.kind === "text" && !detail.forceEdit && !detail.dirty ? (
        <FileHighlightView
          content={detail.content}
          query={detail.highlightQuery || ""}
          focusLine={detail.focusLine}
        />
      ) : detail.kind === "text" ? (
        <FileTextEditor
          value={detail.content}
          readOnly={!detail.editable}
          onChange={(content) => onChange({ ...detail, content, dirty: true })}
        />
      ) : null}
    </div>
  );
}
