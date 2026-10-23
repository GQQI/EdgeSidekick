import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  cancelBookToSkillJob,
  createBookToSkillJob,
  deleteSkill,
  fetchBookToSkillJob,
  fetchSkill,
  fetchSkillFile,
  fetchSkills,
  importSkillDir,
  importSkillFile,
  importSkillFiles,
  createSkillFile,
  deleteSkillFile,
  saveSkill,
  saveSkillFile,
  updateSkill,
  validateSkill,
  type BookToSkillJob,
  type SkillDetail,
  type SkillItem,
  type SkillValidateResult,
} from "../api";
import { getDesktop } from "../desktopBridge";
import type { MsgKey } from "../i18n";

type Props = {
  t: (key: MsgKey, ...args: string[]) => string;
  onToast?: (msg: string) => void;
  onChanged?: () => void;
};

type Draft = {
  previous: string;
  name: string;
  description: string;
  content: string;
};

const EMPTY: Draft = {
  previous: "",
  name: "",
  description: "",
  content: `# New skill

## When to use
Describe when this skill should run.

## Steps
1. Confirm inputs
2. Execute in order
3. Report results
`,
};

const isSkillMd = (p: string) => p.replace(/\\/g, "/").split("/").pop()?.toLowerCase() === "skill.md";

export function SkillSettings({ t, onToast, onChanged }: Props) {
  const [items, setItems] = useState<SkillItem[] | null>(null);
  const [loadError, setLoadError] = useState("");
  const [query, setQuery] = useState("");
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [creating, setCreating] = useState(false);
  const [selectedFile, setSelectedFile] = useState<string>("SKILL.md");
  const [fileContent, setFileContent] = useState("");
  const [fileBinary, setFileBinary] = useState(false);
  const [fileSize, setFileSize] = useState<number | null>(null);
  const [fileDirty, setFileDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [check, setCheck] = useState<SkillValidateResult | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const dirRef = useRef<HTMLInputElement | null>(null);
  const bookRef = useRef<HTMLInputElement | null>(null);
  const [bookName, setBookName] = useState("");
  const [bookOverwrite, setBookOverwrite] = useState(false);
  const [bookJob, setBookJob] = useState<BookToSkillJob | null>(null);
  const [createKind, setCreateKind] = useState<"file" | "folder" | null>(null);
  const [createPath, setCreatePath] = useState("");
  const createInputRef = useRef<HTMLInputElement | null>(null);

  const writable = creating || detail?.writable !== false;
  const files = detail?.files?.length ? detail.files : creating ? ["SKILL.md"] : [];

  const bookHint = t("bookToSkillHint").trim();
  const skillsHint = t("skillsHint").trim();
  const distilling = Boolean(bookJob && !["installed", "failed"].includes(bookJob.status));

  const reload = async (notify = true) => {
    setLoadError("");
    const list = await fetchSkills();
    setItems(list);
    if (notify) onChanged?.();
    return list;
  };

  const onToastRef = useRef(onToast);
  onToastRef.current = onToast;

  useEffect(() => {
    void reload(false).catch((e) => {
      const msg = e instanceof Error ? e.message : String(e);
      setItems([]);
      setLoadError(msg || t("skillsLoadFailShort"));
      onToastRef.current?.(msg);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const filtered = useMemo(() => {
    if (!items) return [];
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (sk) =>
        sk.name.toLowerCase().includes(q) ||
        (sk.description || "").toLowerCase().includes(q) ||
        (sk.tool || "").toLowerCase().includes(q),
    );
  }, [items, query]);

  const loadSkill = async (name: string) => {
    setBusy(true);
    setCreating(false);
    setCreateKind(null);
    try {
      const d = await fetchSkill(name);
      setSelectedName(d.name);
      setDetail(d);
      setCheck(null);
      setDraft({
        previous: d.name,
        name: d.name,
        description: d.description,
        content: d.body || "",
      });
      const files = d.files?.length ? d.files : ["SKILL.md"];
      const first = files.find(isSkillMd) || files[0] || "SKILL.md";
      setSelectedFile(first);
      setFileDirty(false);
      if (isSkillMd(first)) {
        setFileBinary(false);
        setFileContent(d.body || "");
        setFileSize(null);
      } else {
        await openPackageFile(d.name, first, d);
      }
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const openPackageFile = async (skillName: string, path: string, d?: SkillDetail | null) => {
    setBusy(true);
    try {
      if (isSkillMd(path)) {
        const body = d?.body ?? detail?.body ?? draft?.content ?? "";
        setSelectedFile(path);
        setFileBinary(false);
        setFileContent(body);
        setFileSize(null);
        setFileDirty(false);
        if (draft && d) {
          setDraft({
            previous: d.name,
            name: d.name,
            description: d.description,
            content: d.body || "",
          });
        }
        return;
      }
      const file = await fetchSkillFile(skillName, path);
      setSelectedFile(file.path || path);
      setFileBinary(Boolean(file.binary));
      setFileContent(file.binary ? "" : file.content || "");
      setFileSize(typeof file.size === "number" ? file.size : null);
      setFileDirty(false);
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const startCreate = () => {
    setCreating(true);
    setSelectedName(null);
    setDetail(null);
    setCreateKind(null);
    setSelectedFile("SKILL.md");
    setFileBinary(false);
    setFileContent("");
    setFileDirty(false);
    setCheck(null);
    setDraft({ ...EMPTY });
  };

  const runValidate = async (next = draft) => {
    if (!next) return;
    setBusy(true);
    try {
      const res = await validateSkill({
        name: next.name,
        description: next.description,
        content: next.content,
      });
      setCheck(res);
      if (res.ok) onToast?.(t("skillsValidateOk"));
      else onToast?.(res.errors[0] || t("skillsValidateFail"));
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const runSaveSkillMd = async () => {
    if (!draft) return;
    setBusy(true);
    try {
      const res = await validateSkill({
        name: draft.name,
        description: draft.description,
        content: draft.content,
      });
      setCheck(res);
      if (!res.ok) {
        onToast?.(res.errors[0] || t("skillsValidateFail"));
        return;
      }
      let savedName = draft.name;
      if (draft.previous) {
        const out = await updateSkill(draft.previous, {
          name: draft.name,
          description: draft.description,
          content: draft.content,
        });
        savedName = out.skill.name;
      } else {
        const out = await saveSkill({
          name: draft.name,
          description: draft.description,
          content: draft.content,
          overwrite: false,
        });
        savedName = out.skill.name;
      }
      onToast?.(t("skillsSaved"));
      setCreating(false);
      setCheck(null);
      await reload();
      await loadSkill(savedName);
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const runSavePackageFile = async () => {
    if (!selectedName || !selectedFile || fileBinary) return;
    if (isSkillMd(selectedFile)) {
      await runSaveSkillMd();
      return;
    }
    setBusy(true);
    try {
      await saveSkillFile(selectedName, selectedFile, fileContent);
      setFileDirty(false);
      onToast?.(t("skillsFileSaved"));
      const d = await fetchSkill(selectedName);
      setDetail(d);
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const runDelete = async (name: string) => {
    if (!window.confirm(t("skillsDeleteConfirm", name))) return;
    setBusy(true);
    try {
      await deleteSkill(name);
      if (selectedName === name || draft?.previous === name || draft?.name === name) {
        setSelectedName(null);
        setDetail(null);
        setDraft(null);
        setCreating(false);
        setCheck(null);
      }
      onToast?.(t("skillsDeleted"));
      await reload();
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const pollBookJob = async (jobId: string) => {
    for (let i = 0; i < 600; i++) {
      await new Promise((r) => setTimeout(r, 1500));
      const snap = await fetchBookToSkillJob(jobId);
      setBookJob(snap);
      if (snap.status === "installed") {
        onToast?.(t("bookToSkillDone", snap.skill_name || ""));
        const list = await reload();
        if (snap.skill_name) {
          const hit = list.find((x) => x.name === snap.skill_name);
          if (hit) void loadSkill(hit.name);
        }
        return;
      }
      if (snap.status === "failed") {
        onToast?.(snap.error || t("bookToSkillFail"));
        return;
      }
    }
  };

  const runBookUpload = async (file: File) => {
    setBusy(true);
    try {
      const res = await createBookToSkillJob(file, {
        name: bookName.trim() || undefined,
        overwrite: bookOverwrite,
      });
      setBookJob(res.job);
      onToast?.(t("bookToSkillQueued"));
      void pollBookJob(res.job.job_id);
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      if (bookRef.current) bookRef.current.value = "";
    }
  };

  const finishImport = async (
    run: (overwrite: boolean) => Promise<{ imported: SkillItem[] }>,
  ) => {
    setBusy(true);
    try {
      const res = await run(false);
      onToast?.(t("skillsImported", String(res.imported.length)));
      const list = await reload();
      if (res.imported[0]?.name) {
        const hit = list.find((x) => x.name === res.imported[0].name);
        if (hit) void loadSkill(hit.name);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (/already exists/i.test(msg) && window.confirm(t("skillsOverwriteConfirm"))) {
        try {
          const res = await run(true);
          onToast?.(t("skillsImported", String(res.imported.length)));
          await reload();
          return;
        } catch (e2) {
          onToast?.(e2 instanceof Error ? e2.message : String(e2));
          return;
        }
      }
      onToast?.(msg);
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
      if (dirRef.current) dirRef.current.value = "";
    }
  };

  const runImport = async (file: File) => {
    await finishImport((overwrite) => importSkillFile(file, overwrite));
  };

  const runImportFolder = async () => {
    const desktop = getDesktop();
    if (desktop?.workspace?.pickFolder) {
      const picked = await desktop.workspace.pickFolder({ title: t("skillsImportDir") });
      if (picked.cancelled || !picked.path) return;
      await finishImport((overwrite) => importSkillDir(picked.path as string, overwrite));
      return;
    }
    dirRef.current?.click();
  };

  const runImportFiles = async (list: FileList) => {
    const files = Array.from(list);
    if (!files.length) return;
    await finishImport((overwrite) => importSkillFiles(files, overwrite));
  };


  const openCreateForm = (kind: "file" | "folder") => {
    if (!selectedName) return;
    if (!writable) {
      onToast?.(t("skillsReadonly"));
      return;
    }
    setCreateKind(kind);
    setCreatePath(kind === "folder" ? "scripts" : "scripts/new.py");
    requestAnimationFrame(() => createInputRef.current?.select());
  };

  const cancelCreateForm = () => {
    setCreateKind(null);
    setCreatePath("");
  };

  const submitCreateForm = async () => {
    if (!selectedName || createKind == null) return;
    if (!writable) {
      onToast?.(t("skillsReadonly"));
      return;
    }
    const kind = createKind;
    const rel = createPath.trim().replace(/\\/g, "/").replace(/^\/+/, "");
    if (!rel) {
      onToast?.(t("skillsCreateFail"));
      return;
    }
    setBusy(true);
    try {
      const res = await createSkillFile(selectedName, {
        path: rel,
        type: kind,
        content: kind === "file" ? "" : undefined,
      });
      onToast?.(t("skillsCreated", res.path || rel));
      cancelCreateForm();
      const d = await fetchSkill(selectedName);
      setDetail(d);
      if (kind === "file") {
        setSelectedFile(res.path || rel);
        setFileBinary(false);
        setFileContent("");
        setFileSize(0);
        setFileDirty(false);
      }
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : t("skillsCreateFail"));
    } finally {
      setBusy(false);
    }
  };

  const runDeletePackageFile = async () => {
    if (!selectedName || !selectedFile || isSkillMd(selectedFile) || !writable) return;
    if (!window.confirm(t("skillsDeleteConfirm", selectedFile))) return;
    setBusy(true);
    try {
      await deleteSkillFile(selectedName, selectedFile);
      onToast?.(t("skillsFileDeleted", selectedFile));
      const d = await fetchSkill(selectedName);
      setDetail(d);
      const next = d.files?.find(isSkillMd) || d.files?.[0] || "SKILL.md";
      await openPackageFile(d.name, next, d);
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!items) {
    return <p className="hint">{t("skillsLoading")}</p>;
  }

  if (loadError && items.length === 0) {
    return (
      <div className="settings-pane">
        <div className="plugin-notice error" role="alert">
          {t("skillsLoadFail", loadError)}
          <button
            type="button"
            className="ghost"
            onClick={() =>
              void reload(false).catch((e) => {
                const msg = e instanceof Error ? e.message : String(e);
                setItems([]);
                setLoadError(msg || t("skillsLoadFailShort"));
              })
            }
          >
            {t("commonRetry")}
          </button>
        </div>
      </div>
    );
  }

  type EditorParts = { body: ReactNode; actions: ReactNode | null };

  const renderSkillMdEditor = (): EditorParts | null =>
    draft
      ? {
          body: (
            <>
              <div className="skill-meta-grid">
                <label className="mcp-field">
                  <span>{t("skillsName")}</span>
                  <input
                    value={draft.name}
                    disabled={busy || !writable}
                    placeholder="pdf-report"
                    onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                  />
                  <small className="skill-field-hint">{t("skillsNameHint")}</small>
                </label>
                <label className="mcp-field">
                  <span>{t("skillsDescription")}</span>
                  <textarea
                    className="skill-desc-input"
                    rows={3}
                    value={draft.description}
                    disabled={busy || !writable}
                    placeholder={t("skillsDescriptionHint")}
                    onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                  />
                </label>
              </div>
              <label className="mcp-field skill-body-field">
                <span>{t("skillsBody")}</span>
                <textarea
                  className="skill-body"
                  rows={14}
                  value={draft.content}
                  disabled={busy || !writable}
                  onChange={(e) => setDraft({ ...draft, content: e.target.value })}
                />
              </label>
              {check && (
                <div className={`skill-check ${check.ok ? "ok" : "bad"}`}>
                  {!check.ok &&
                    check.errors.map((err) => (
                      <p key={err} className="skill-check-err">
                        {err}
                      </p>
                    ))}
                  {check.warnings.map((warn) => (
                    <p key={warn} className="skill-check-warn">
                      {warn}
                    </p>
                  ))}
                  {check.ok && !check.warnings.length && <p>{t("skillsValidateOk")}</p>}
                </div>
              )}
            </>
          ),
          actions: (
            <>
              <button
                type="button"
                className="primary"
                disabled={busy || !writable}
                onClick={() => void runSaveSkillMd()}
              >
                {t("skillsSave")}
              </button>
              <button type="button" className="ghost" disabled={busy} onClick={() => void runValidate()}>
                {t("skillsValidate")}
              </button>
              {creating && (
                <button
                  type="button"
                  className="ghost"
                  disabled={busy}
                  onClick={() => {
                    setCreating(false);
                    setDraft(null);
                    setCheck(null);
                  }}
                >
                  {t("cancel")}
                </button>
              )}
            </>
          ),
        }
      : null;

  const renderFileEditor = (): EditorParts => {
    if (fileBinary) {
      return {
        body: (
          <div className="skill-binary-notice">
            <p>{t("skillsBinaryFile")}</p>
            {fileSize != null && <p className="hint">{fileSize} bytes</p>}
          </div>
        ),
        actions: null,
      };
    }
    return {
      body: (
        <label className="mcp-field skill-body-field">
          <span>{selectedFile}</span>
          <textarea
            className="skill-body"
            rows={16}
            value={fileContent}
            disabled={busy || !writable}
            onChange={(e) => {
              setFileContent(e.target.value);
              setFileDirty(true);
            }}
          />
        </label>
      ),
      actions: (
        <button
          type="button"
          className="primary"
          disabled={busy || !writable || !fileDirty}
          onClick={() => void runSavePackageFile()}
        >
          {t("skillsSaveFile")}
        </button>
      ),
    };
  };

  const renderEditor = (parts: EditorParts | null) =>
    parts ? (
      <>
        <div className="skill-editor-scroll">{parts.body}</div>
        {parts.actions ? <div className="skill-editor-footer">{parts.actions}</div> : null}
      </>
    ) : null;

  return (
    <div className="skill-settings settings-pane">
      <header className="settings-pane-intro">
        <h3>{t("skillsTitle")}</h3>
        {skillsHint ? <p className="hint">{skillsHint}</p> : null}
      </header>

      <div className="skill-toolbar">
        <button type="button" className="primary" disabled={busy} onClick={startCreate}>
          {t("skillsNew")}
        </button>
        <button type="button" className="ghost" disabled={busy} onClick={() => void runImportFolder()}>
          {t("skillsImportDir")}
        </button>
        <button type="button" className="ghost" disabled={busy} onClick={() => fileRef.current?.click()}>
          {t("skillsImportFile")}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".md,.zip,text/markdown,application/zip"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void runImport(file);
          }}
        />
        <input
          ref={dirRef}
          type="file"
          hidden
          multiple
          // @ts-expect-error Chromium folder picker
          webkitdirectory=""
          directory=""
          onChange={(e) => {
            if (e.target.files?.length) void runImportFiles(e.target.files);
          }}
        />
      </div>

      <div className="skill-book-bar">
        <strong>{t("bookToSkillTitle")}</strong>
        {bookHint ? <span className="hint">{bookHint}</span> : null}
        <input
          className="skill-book-name"
          value={bookName}
          disabled={busy || distilling}
          placeholder={t("bookToSkillName")}
          onChange={(e) => setBookName(e.target.value)}
        />
        <label className="mcp-check">
          <input
            type="checkbox"
            checked={bookOverwrite}
            disabled={busy || distilling}
            onChange={(e) => setBookOverwrite(e.target.checked)}
          />
          <span>{t("bookToSkillOverwrite")}</span>
        </label>
        <button
          type="button"
          className="ghost"
          disabled={busy || distilling}
          onClick={() => bookRef.current?.click()}
        >
          {distilling ? t("bookToSkillBusy") : t("bookToSkillUpload")}
        </button>
        {bookJob && !["installed", "failed"].includes(bookJob.status) && (
          <button
            type="button"
            className="ghost"
            onClick={() => void cancelBookToSkillJob(bookJob.job_id).then(setBookJob)}
          >
            {t("cancel")}
          </button>
        )}
        <input
          ref={bookRef}
          type="file"
          accept=".pdf,.epub,.docx,.xlsx,.xlsm,.pptx,.odt,.ods,.odp,.rtf,.txt,.md,.markdown,.csv,.tsv,.html,.htm,.json,application/pdf,application/epub+zip"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void runBookUpload(file);
          }}
        />
        {bookJob && (
          <span className="hint">
            {bookJob.status} · {bookJob.progress}%
            {bookJob.message ? ` · ${bookJob.message}` : ""}
            {bookJob.error ? ` · ${bookJob.error}` : ""}
          </span>
        )}
      </div>

      <div className="skill-pool">
        <aside className="skill-pool-list">
          <div className="skill-pool-list-head">
            <input
              className="skill-pool-search"
              value={query}
              placeholder={t("skillsSearch")}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <div className="skill-pool-items">
            {filtered.length === 0 && <p className="hint">{t("skillsEmpty")}</p>}
            {filtered.map((sk) => (
              <button
                key={sk.name}
                type="button"
                className={`skill-pool-item${selectedName === sk.name && !creating ? " active" : ""}`}
                disabled={busy}
                onClick={() => void loadSkill(sk.name)}
              >
                <span className="skill-pool-item-name">{sk.name}</span>
                <p className="skill-pool-item-desc">{sk.description || t("skillsNoDesc")}</p>
              </button>
            ))}
          </div>
        </aside>

        <section className="skill-pool-detail">
          {creating ? (
            <>
              <div className="skill-pool-detail-head">
                <strong>{t("skillsNew")}</strong>
              </div>
              <div className="skill-pool-detail-body" style={{ gridTemplateColumns: "1fr" }}>
                <div className="skill-file-editor">{renderEditor(renderSkillMdEditor())}</div>
              </div>
            </>
          ) : detail && draft ? (
            <>
              <div className="skill-pool-detail-head">
                <div className="skill-pool-detail-meta">
                  <strong>{detail.name}</strong>
                  <p
                    className="skill-pool-detail-desc"
                    title={draft.description || detail.description || t("skillsNoDesc")}
                  >
                    {draft.description || detail.description || t("skillsNoDesc")}
                  </p>
                </div>
                <div className="mcp-card-actions">
                  {!writable && <span className="hint">{t("skillsReadonly")}</span>}
                  <button
                    type="button"
                    className="mini ghost danger"
                    disabled={busy}
                    onClick={() => void runDelete(detail.name)}
                  >
                    {t("skillsDelete")}
                  </button>
                </div>
              </div>
              <div className="skill-pool-detail-body">
                <div className="skill-file-tree-wrap">
                  <div className="skill-file-tree-actions">
                    {createKind == null ? (
                      <>
                        <button
                          type="button"
                          className="mini ghost"
                          disabled={busy}
                          onClick={() => openCreateForm("file")}
                        >
                          {t("skillsNewFile")}
                        </button>
                        <button
                          type="button"
                          className="mini ghost"
                          disabled={busy}
                          onClick={() => openCreateForm("folder")}
                        >
                          {t("skillsNewFolder")}
                        </button>
                        {!isSkillMd(selectedFile) && (
                          <button
                            type="button"
                            className="mini ghost danger"
                            disabled={busy || !writable}
                            onClick={() => void runDeletePackageFile()}
                          >
                            {t("skillsDelete")}
                          </button>
                        )}
                      </>
                    ) : (
                      <form
                        className="skill-create-form"
                        onSubmit={(e) => {
                          e.preventDefault();
                          void submitCreateForm();
                        }}
                      >
                        <input
                          ref={createInputRef}
                          value={createPath}
                          disabled={busy}
                          placeholder={t(
                            createKind === "folder" ? "skillsFolderPrompt" : "skillsPathPrompt",
                          )}
                          aria-label={t(
                            createKind === "folder" ? "skillsFolderPrompt" : "skillsPathPrompt",
                          )}
                          onChange={(e) => setCreatePath(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Escape") {
                              e.preventDefault();
                              cancelCreateForm();
                            }
                          }}
                        />
                        <button type="submit" className="mini primary" disabled={busy}>
                          {t("confirm")}
                        </button>
                        <button
                          type="button"
                          className="mini ghost"
                          disabled={busy}
                          onClick={cancelCreateForm}
                        >
                          {t("cancel")}
                        </button>
                      </form>
                    )}
                  </div>
                  <nav className="skill-file-tree" aria-label={t("skillsFileTree")}>
                    {files.map((f) => (
                      <button
                        key={f}
                        type="button"
                        className={selectedFile === f ? "active" : ""}
                        disabled={busy}
                        title={f}
                        onClick={() => void openPackageFile(detail.name, f)}
                      >
                        {f}
                      </button>
                    ))}
                  </nav>
                </div>
                <div className="skill-file-editor">
                  {renderEditor(
                    isSkillMd(selectedFile) ? renderSkillMdEditor() : renderFileEditor(),
                  )}
                </div>
              </div>
            </>
          ) : (
            <div className="skill-empty-detail">
              <p>{t("skillsSelectHint")}</p>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
