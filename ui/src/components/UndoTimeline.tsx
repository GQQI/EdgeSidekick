import { useCallback, useEffect, useState } from "react";
import { fetchUndo, postUndo, truncateSession, type UndoItem } from "../api";
import { usePrefs } from "../prefs";
import { IconRefresh, IconUndo } from "./icons";

type Props = {
  refreshKey?: number;
  sessionId?: string | null;
  workspace?: string | null;
  onRestored?: (info?: { truncated?: boolean }) => void;
  onToast?: (msg: string) => void;
};

export function UndoTimeline({
  refreshKey = 0,
  sessionId = null,
  workspace = null,
  onRestored,
  onToast,
}: Props) {
  const { t, locale } = usePrefs();
  const [items, setItems] = useState<UndoItem[]>([]);
  const [count, setCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [busyId, setBusyId] = useState("");

  const load = useCallback(async () => {
    if (!sessionId && !workspace) {
      setItems([]);
      setCount(0);
      setErr("");
      return;
    }
    setLoading(true);
    setErr("");
    try {
      const res = await fetchUndo(sessionId, { workspace });
      setItems(res.items || []);
      setCount(res.count || 0);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [sessionId, workspace]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  async function restore(item?: UndoItem) {
    setBusyId(item?.id || "*");
    setErr("");
    try {
      const sid = sessionId || item?.session_id || null;
      const turn = item?.user_turn;
      // Checkpoint cards: rewind chat + files together via truncate.
      // File-only / latest: reverse FS stack for this workspace.
      if (sid && item && item.op === "checkpoint" && typeof turn === "number") {
        const res = await truncateSession(sid, turn, { restoreFiles: true });
        const n = res.file_undo?.undone_count ?? 0;
        await load();
        onRestored?.({ truncated: true });
        if (res.file_undo?.partial) {
          onToast?.(t("undoRestorePartial", String(n)));
        } else if (n > 0) {
          onToast?.(t("undoRestoreOk", String(n)));
        } else {
          onToast?.(t("undoRestoreChatOnly"));
        }
        return;
      }
      const res = await postUndo(item?.id, sid, { workspace });
      const n = res.undone_count ?? 0;
      await load();
      onRestored?.({ truncated: false });
      if (n > 0) {
        onToast?.(t("undoRestoreFilesOk", String(n)));
      } else {
        onToast?.(t("undoRestoreNone"));
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId("");
    }
  }

  return (
    <div className="side-panel undo-panel">
      <div className="side-panel-head">
        <IconUndo size={16} />
        <span>{t("navUndo")}</span>
        <div className="side-panel-head-actions">
          {count > 0 ? <span className="undo-count">{count}</span> : null}
          <button type="button" className="icon-btn" title={t("refresh")} onClick={() => void load()}>
            <IconRefresh size={14} />
          </button>
        </div>
      </div>
      <div className="side-panel-body undo-body">
        {loading && items.length === 0 ? <div className="muted">{t("loading")}</div> : null}
        {err ? <div className="side-error">{err}</div> : null}
        {items.length === 0 && !loading ? (
          <div className="undo-empty">
            <IconUndo size={22} />
            <p>{t("undoEmpty")}</p>
          </div>
        ) : null}
        <ol className="undo-list">
          {items.map((item, i) => {
            const ts = Number(item.ts || 0);
            const when = ts
              ? new Date(ts > 1e12 ? ts : ts * 1000).toLocaleString(
                  locale === "en" ? "en-US" : "zh-CN",
                  { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" },
                )
              : "";
            const title = item.user_text || item.label || item.op || item.id;
            const files = item.files || [];
            return (
              <li key={item.id || i} className="undo-card">
                <div className="undo-card-top">
                  {when ? <time className="undo-card-when">{when}</time> : null}
                  <button
                    type="button"
                    className="undo-card-restore"
                    disabled={Boolean(busyId)}
                    onClick={() => void restore(item)}
                  >
                    {t("undoRestoreHere")}
                  </button>
                </div>
                <p className="undo-card-title" title={title}>
                  {title}
                </p>
                {files.length > 0 ? (
                  <ul className="undo-card-files">
                    {files.slice(0, 6).map((path) => (
                      <li key={path} className="undo-file-chip" title={path}>
                        {path.split("/").pop() || path}
                      </li>
                    ))}
                    {files.length > 6 ? (
                      <li className="undo-file-chip more">+{files.length - 6}</li>
                    ) : null}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ol>
        {items.length > 0 ? (
          <button
            type="button"
            className="undo-latest"
            disabled={Boolean(busyId)}
            onClick={() => void restore(items[0])}
          >
            {t("undoLatest")}
          </button>
        ) : null}
      </div>
    </div>
  );
}
