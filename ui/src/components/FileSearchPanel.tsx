import { useEffect, useRef, useState } from "react";
import { readFileContent, searchFiles, type SearchHit } from "../api";
import { usePrefs } from "../prefs";
import { FileTypeIcon } from "./FileTypeIcon";
import { IconSearch } from "./icons";
import { sameFsPath } from "../utils/fsPath";

type OpenOpts = {
  highlightQuery?: string;
  focusLine?: number;
};

type Props = {
  onOpenFile: (file: import("../api").FilePayload, opts?: OpenOpts) => void;
  refreshKey?: number;
  activeFilePath?: string | null;
  workspace?: string | null;
};

export function FileSearchPanel({
  onOpenFile,
  refreshKey = 0,
  activeFilePath = null,
  workspace = null,
}: Props) {
  const { t } = usePrefs();
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const timer = useRef<number | null>(null);
  // Bumped when the query/workspace changes; a slow response for an older
  // query (or the previous workspace) must never overwrite newer results.
  const epochRef = useRef(0);

  useEffect(() => {
    if (timer.current) window.clearTimeout(timer.current);
    const q = query.trim();
    const epoch = ++epochRef.current;
    if (!q) {
      setHits([]);
      setLoading(false);
      setError("");
      setExpanded({});
      return;
    }
    setLoading(true);
    timer.current = window.setTimeout(() => {
      void searchFiles(q, ".", workspace || undefined)
        .then((res) => {
          if (epochRef.current !== epoch) return;
          setHits(res.hits || []);
          setError("");
          setExpanded({});
        })
        .catch((e) => {
          if (epochRef.current !== epoch) return;
          setHits([]);
          setError(e instanceof Error ? e.message : String(e));
        })
        .finally(() => {
          if (epochRef.current === epoch) setLoading(false);
        });
    }, 280);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
      epochRef.current += 1;
    };
  }, [query, refreshKey, workspace]);

  async function openFile(path: string, focusLine = 0) {
    try {
      const file = await readFileContent(path, workspace || undefined);
      const q = query.trim();
      onOpenFile(workspace ? { ...file, workspace } : file, {
        highlightQuery: q || undefined,
        focusLine: focusLine > 0 ? focusLine : undefined,
      });
    } catch (e) {
      console.error(e);
    }
  }

  function toggleExpand(hit: SearchHit) {
    const key = `${hit.path}:${hit.match}`;
    setExpanded((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  async function onHitClick(hit: SearchHit) {
    if (hit.kind === "dir") {
      setQuery(hit.path);
      return;
    }
    if (hit.match === "content" && (hit.matchCount ?? hit.lines?.length ?? 0) > 0) {
      const key = `${hit.path}:${hit.match}`;
      if (!expanded[key]) toggleExpand(hit);
      const first = hit.lines?.[0] || hit.line || 0;
      await openFile(hit.path, first);
      return;
    }
    await openFile(hit.path);
  }

  return (
    <aside className="side-panel search-panel">
      <div className="side-panel-head">
        <IconSearch size={15} />
        <span>{t("navSearch")}</span>
      </div>
      <div className="search-box">
        <IconSearch size={14} />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("searchPlaceholder")}
          autoFocus
        />
      </div>
      <div className="search-results">
        {loading && <div className="fe-hint">{t("feLoading")}</div>}
        {error && <div className="fe-hint err">{error}</div>}
        {!loading && !error && hits.length === 0 && (
          <div className="fe-hint">{query ? t("searchEmpty") : t("searchHint")}</div>
        )}
        {hits.map((h) => {
          const key = `${h.path}:${h.match}`;
          const isContent = h.match === "content";
          const lineCount = h.matchCount ?? h.lines?.length ?? 0;
          const snippets =
            h.snippets?.length
              ? h.snippets
              : (h.lines || []).map((line) => ({
                  line,
                  text: line === h.line ? h.snippet : "",
                }));
          const isOpen = Boolean(expanded[key]);

          return (
            <div key={key} className={`search-hit-group${isOpen ? " open" : ""}`}>
              <button
                type="button"
                className={`search-hit${sameFsPath(h.path, activeFilePath) ? " selected" : ""}`}
                onClick={() => void onHitClick(h)}
                title={isContent ? t("searchExpandLines") : h.snippet}
              >
                <FileTypeIcon name={h.name} kind={h.kind === "dir" ? "dir" : "file"} />
                <span className="search-hit-text">
                  <strong>
                    {h.name}
                    <em className={`search-match-tag ${h.match}`}>
                      {isContent ? t("searchMatchContent") : t("searchMatchName")}
                    </em>
                    {isContent && lineCount > 0 && (
                      <em className="search-line-count">
                        {t("searchMatchLines").replace("{n}", String(lineCount))}
                      </em>
                    )}
                  </strong>
                  <span>{h.path}</span>
                </span>
                {isContent && lineCount > 0 && (
                  <span className={`search-chevron${isOpen ? " open" : ""}`} aria-hidden>
                    ▾
                  </span>
                )}
              </button>
              {isContent && isOpen && snippets.length > 0 && (
                <div className="search-line-list">
                  <div className="search-line-list-head">
                    <button
                      type="button"
                      className="search-open-file"
                      onClick={() => void openFile(h.path, snippets[0]?.line || h.line || 0)}
                    >
                      {t("searchOpenFile")}
                    </button>
                  </div>
                  {snippets.map((s) => (
                    <button
                      key={`${h.path}:${s.line}`}
                      type="button"
                      className="search-line-hit"
                      onClick={() => void openFile(h.path, s.line)}
                      title={s.text}
                    >
                      <span className="search-line-no">L{s.line}</span>
                      <span className="search-line-text">{s.text || "…"}</span>
                    </button>
                  ))}
                  {(h.matchCount ?? 0) > snippets.length && (
                    <div className="search-line-more">
                      {t("searchMoreLines").replace(
                        "{n}",
                        String((h.matchCount ?? 0) - snippets.length),
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </aside>
  );
}
