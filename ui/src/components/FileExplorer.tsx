import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type DragEvent as ReactDragEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from "react";
import {
  createFsEntry,
  deleteFsEntry,
  listFiles,
  moveFsEntry,
  readFileContent,
  renameFsEntry,
  revealFsEntry,
  uploadFile,
  type FsEntry,
} from "../api";
import { usePrefs } from "../prefs";
import { FileTypeIcon } from "./FileTypeIcon";
import {
  IconCheck,
  IconChevronDown,
  IconExternal,
  IconFile,
  IconFiles,
  IconFolder,
  IconPaperclip,
  IconPlus,
  IconRefresh,
  IconTrash,
  IconX,
} from "./icons";
import { ancestorFsDirs, relFsPath, sameFsPath } from "../utils/fsPath";

type Props = {
  rootName: string;
  /** Absolute path of the active workspace (for tooltips / fallback). */
  workspaceAbsPath?: string | null;
  collapsed: boolean;
  width: number;
  onToggle: () => void;
  onOpenFile: (file: import("../api").FilePayload) => void;
  refreshKey?: number;
  /** Workspace-relative (or absolute) path of the file shown in the detail panel. */
  activeFilePath?: string | null;
  /** Called after a path is deleted (file or folder). */
  onDeleted?: (path: string) => void;
  /** Hide explorer title chrome (social embed). */
  hideChrome?: boolean;
};

type DirState = {
  loading?: boolean;
  entries?: FsEntry[];
  error?: string;
};

type Creating = {
  parent: string;
  kind: "file" | "dir";
  depth: number;
};

type Renaming = {
  path: string;
  parent: string;
  name: string;
  kind: "file" | "dir";
  depth: number;
};

type CtxMenu = {
  x: number;
  y: number;
  entry: FsEntry;
  depth: number;
};

export function FileExplorer({
  rootName,
  workspaceAbsPath = null,
  collapsed,
  width,
  onToggle,
  onOpenFile,
  refreshKey = 0,
  activeFilePath = null,
  onDeleted,
  hideChrome = false,
}: Props) {
  const { t } = usePrefs();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({ ".": true });
  const [dirs, setDirs] = useState<Record<string, DirState>>({});
  const [creating, setCreating] = useState<Creating | null>(null);
  const [renaming, setRenaming] = useState<Renaming | null>(null);
  const [newName, setNewName] = useState("");
  const [createError, setCreateError] = useState("");
  const [renameError, setRenameError] = useState("");
  const [createBusy, setCreateBusy] = useState(false);
  const [renameBusy, setRenameBusy] = useState(false);
  const [ctxMenu, setCtxMenu] = useState<CtxMenu | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [pendingDeletePath, setPendingDeletePath] = useState<string | null>(null);
  const [selectedDir, setSelectedDir] = useState(".");
  const [uploadBusy, setUploadBusy] = useState(false);
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const [pickedFile, setPickedFile] = useState<string | null>(null);
  const [draggingPath, setDraggingPath] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const [actionError, setActionError] = useState("");
  const openClickTimer = useRef<number | null>(null);
  const treeRef = useRef<HTMLDivElement | null>(null);
  const dirsRef = useRef(dirs);
  dirsRef.current = dirs;
  const shownFile = activeFilePath || pickedFile;

  function absPathFor(entry: Pick<FsEntry, "path" | "abs_path">): string {
    if (entry.abs_path) return entry.abs_path;
    const root = (workspaceAbsPath || "").replace(/[/\\]+$/, "");
    if (!root) return entry.path;
    if (!entry.path || entry.path === ".") return root;
    const rel = entry.path.replace(/\\/g, "/").replace(/^\.\//, "");
    const sep = root.includes("\\") ? "\\" : "/";
    return `${root}${sep}${rel.replace(/\//g, sep)}`;
  }

  async function revealInOs(path: string) {
    setCtxMenu(null);
    try {
      await revealFsEntry(path, workspaceAbsPath || undefined);
    } catch (e) {
      setActionError(
        t("feRevealFail", e instanceof Error ? e.message : String(e)),
      );
    }
  }

  // Bumped whenever the workspace changes; responses from loads of a previous
  // workspace must never land in the new tree (they used to overwrite the
  // freshly cleared "." dir and show another project's files).
  const wsEpochRef = useRef(0);

  const loadDir = useCallback(async (path: string) => {
    const epoch = wsEpochRef.current;
    setDirs((prev) => ({ ...prev, [path]: { ...prev[path], loading: true, error: undefined } }));
    try {
      const data = await listFiles(path, workspaceAbsPath || undefined);
      if (wsEpochRef.current !== epoch) return;
      setDirs((prev) => ({
        ...prev,
        [path]: { loading: false, entries: data.entries },
      }));
    } catch (e) {
      if (wsEpochRef.current !== epoch) return;
      setDirs((prev) => ({
        ...prev,
        [path]: {
          loading: false,
          error: e instanceof Error ? e.message : String(e),
          entries: [],
        },
      }));
    }
  }, [workspaceAbsPath]);

  useEffect(() => {
    wsEpochRef.current += 1;
    setDirs({});
    void loadDir(".");
    setExpanded({ ".": true });
    setSelectedDir(".");
    setPickedFile(null);
  }, [loadDir, rootName, workspaceAbsPath]);

  useEffect(() => {
    if (refreshKey <= 0) return;
    setDirs((prev) => {
      const paths = Object.keys(prev);
      if (paths.length === 0) {
        void loadDir(".");
      } else {
        for (const p of paths) void loadDir(p);
      }
      return prev;
    });
  }, [refreshKey, loadDir]);

  useEffect(() => {
    return () => {
      if (openClickTimer.current) window.clearTimeout(openClickTimer.current);
    };
  }, []);

  useEffect(() => {
    if (!ctxMenu) return;
    const close = () => setCtxMenu(null);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("click", close);
    window.addEventListener("scroll", close, true);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("keydown", onKey);
    };
  }, [ctxMenu]);

  useEffect(() => {
    if (!activeFilePath) return;
    const parents = ancestorFsDirs(activeFilePath, workspaceAbsPath);
    setExpanded((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const dir of parents) {
        if (!next[dir]) {
          next[dir] = true;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
    for (const dir of parents) {
      const state = dirsRef.current[dir];
      if (!state?.entries && !state?.loading) void loadDir(dir);
    }
  }, [activeFilePath, workspaceAbsPath, loadDir]);

  useEffect(() => {
    const rel = relFsPath(shownFile, workspaceAbsPath);
    if (!rel || rel === ".") return;
    const root = treeRef.current;
    if (!root) return;
    const node = root.querySelector(`[data-fe-path="${CSS.escape(rel)}"]`);
    if (node instanceof HTMLElement) {
      node.scrollIntoView({ block: "nearest" });
    }
  }, [shownFile, workspaceAbsPath, dirs]);

  function parentOf(path: string) {
    const norm = path.replace(/\\/g, "/");
    if (!norm.includes("/")) return ".";
    return norm.split("/").slice(0, -1).join("/") || ".";
  }

  function depthOf(path: string) {
    if (!path || path === ".") return 0;
    return path.replace(/\\/g, "/").split("/").filter(Boolean).length;
  }

  function isDescendantOrSelf(src: string, destDir: string) {
    if (src === destDir) return true;
    return destDir.startsWith(`${src}/`);
  }

  function startCreateInSelected(kind: "file" | "dir") {
    const parent = selectedDir || ".";
    startCreate(parent, kind, depthOf(parent));
  }

  function openContextMenu(e: ReactMouseEvent, entry: FsEntry, depth: number) {
    e.preventDefault();
    e.stopPropagation();
    setCreating(null);
    setRenaming(null);
    setCtxMenu({ x: e.clientX, y: e.clientY, entry, depth });
  }

  async function refreshAfterPathChange(srcPath: string, destDir?: string) {
    const fromParent = parentOf(srcPath);
    await loadDir(fromParent);
    if (fromParent !== ".") await loadDir(".");
    if (destDir) {
      await loadDir(destDir);
      if (destDir !== "." && destDir !== fromParent) await loadDir(".");
    }
  }

  async function deleteEntry(entry: FsEntry) {
    const isDir = entry.type === "dir";
    setDeleteBusy(true);
    setCtxMenu(null);
    setPendingDeletePath(null);
    setActionError("");
    try {
      await deleteFsEntry(entry.path, isDir, workspaceAbsPath || undefined);
      if (selectedDir === entry.path || selectedDir.startsWith(`${entry.path}/`)) {
        setSelectedDir(parentOf(entry.path));
      }
      await refreshAfterPathChange(entry.path);
      if (isDir) {
        setDirs((prev) => {
          const next = { ...prev };
          for (const key of Object.keys(next)) {
            if (key === entry.path || key.startsWith(`${entry.path}/`)) {
              delete next[key];
            }
          }
          return next;
        });
        setExpanded((prev) => {
          const next = { ...prev };
          delete next[entry.path];
          return next;
        });
      }
      onDeleted?.(entry.path);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeleteBusy(false);
    }
  }

  async function moveEntryTo(srcPath: string, destDir: string) {
    if (!srcPath || !destDir) return;
    if (parentOf(srcPath) === destDir) return;
    if (isDescendantOrSelf(srcPath, destDir)) {
      setActionError(t("feMoveIntoSelf"));
      return;
    }
    setActionError("");
    try {
      await moveFsEntry(srcPath, destDir, workspaceAbsPath || undefined);
      if (selectedDir === srcPath || selectedDir.startsWith(`${srcPath}/`)) {
        setSelectedDir(destDir);
      }
      setExpanded((prev) => ({ ...prev, [destDir]: true }));
      await refreshAfterPathChange(srcPath, destDir);
      // Drop stale cache for moved dir tree
      setDirs((prev) => {
        const next = { ...prev };
        for (const key of Object.keys(next)) {
          if (key === srcPath || key.startsWith(`${srcPath}/`)) {
            delete next[key];
          }
        }
        return next;
      });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    }
  }

  function onDragStart(e: ReactDragEvent, path: string) {
    e.dataTransfer.setData("text/plain", path);
    e.dataTransfer.effectAllowed = "move";
    setDraggingPath(path);
    setActionError("");
  }

  function onDragEnd() {
    setDraggingPath(null);
    setDropTarget(null);
  }

  function onDragOverDir(e: ReactDragEvent, destDir: string) {
    const hasOsFiles = e.dataTransfer.types.includes("Files");
    if (!draggingPath && !hasOsFiles) return;
    if (draggingPath) {
      if (isDescendantOrSelf(draggingPath, destDir)) return;
      if (parentOf(draggingPath) === destDir) return;
    }
    e.preventDefault();
    e.dataTransfer.dropEffect = hasOsFiles && !draggingPath ? "copy" : "move";
    if (dropTarget !== destDir) setDropTarget(destDir);
  }


  async function uploadFilesToDir(fileList: FileList | File[] | null, destDir: string) {
    if (!fileList || (fileList as FileList).length === 0) return;
    const files = Array.from(fileList as FileList | File[]);
    if (files.length === 0) return;
    const ws = (workspaceAbsPath || "").trim();
    if (!ws) {
      setActionError("请先选择工作区，再上传文件");
      return;
    }
    // Upload into the currently selected folder (workspace-relative), including root.
    const dest = !destDir || destDir === "."
      ? "."
      : destDir.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
    setUploadBusy(true);
    setActionError("");
    try {
      for (const file of files) {
        await uploadFile(file, {
          dest: `${dest}/`,
          workspace: ws,
        });
      }
      const parent = dest === "." ? "." : dest;
      await loadDir(parent);
      if (parent !== ".") await loadDir(".");
      setExpanded((prev) => ({ ...prev, [parent]: true, ".": true }));
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploadBusy(false);
      if (uploadInputRef.current) uploadInputRef.current.value = "";
    }
  }

  function onDropOnDir(e: ReactDragEvent, destDir: string) {
    e.preventDefault();
    e.stopPropagation();
    setDropTarget(null);
    const osFiles = e.dataTransfer.files;
    if (osFiles && osFiles.length > 0) {
      setDraggingPath(null);
      void uploadFilesToDir(osFiles, destDir);
      return;
    }
    const src = e.dataTransfer.getData("text/plain") || draggingPath;
    setDraggingPath(null);
    if (src) void moveEntryTo(src, destDir);
  }

  function startCreate(parent: string, kind: "file" | "dir", depth: number) {
    setRenaming(null);
    setRenameError("");
    setCreating({ parent, kind, depth });
    setNewName("");
    setCreateError("");
    setExpanded((prev) => ({ ...prev, [parent]: true }));
    if (parent !== "." && !dirs[parent]?.entries) void loadDir(parent);
  }

  function cancelCreate() {
    setCreating(null);
    setNewName("");
    setCreateError("");
    setCreateBusy(false);
  }

  function startRename(entry: FsEntry, depth: number) {
    if (openClickTimer.current) {
      window.clearTimeout(openClickTimer.current);
      openClickTimer.current = null;
    }
    setCreating(null);
    setCreateError("");
    const parent = parentOf(entry.path);
    setRenaming({
      path: entry.path,
      parent,
      name: entry.name,
      kind: entry.type === "dir" ? "dir" : "file",
      depth,
    });
    setNewName(entry.name);
    setRenameError("");
  }

  function cancelRename() {
    setRenaming(null);
    setNewName("");
    setRenameError("");
    setRenameBusy(false);
  }

  async function submitCreate() {
    if (!creating) return;
    const name = newName.trim().replace(/\\/g, "/").split("/").filter(Boolean).pop();
    if (!name) {
      setCreateError(t("feNameRequired"));
      return;
    }
    setCreateBusy(true);
    setCreateError("");
    const parentPath = creating.parent;
    const kind = creating.kind;
    try {
      const parent = parentPath === "." ? "" : parentPath;
      const path = parent ? `${parent}/${name}` : name;
      await createFsEntry(path, kind, workspaceAbsPath || undefined);
      cancelCreate();
      await loadDir(parentPath);
      if (parentPath !== ".") await loadDir(".");
      if (kind === "dir") {
        setSelectedDir(path);
        setExpanded((prev) => ({ ...prev, [parentPath]: true, [path]: true }));
        void loadDir(path);
      }
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : String(e));
      setCreateBusy(false);
    }
  }

  async function submitRename() {
    if (!renaming) return;
    const name = newName.trim().replace(/\\/g, "/").split("/").filter(Boolean).pop();
    if (!name) {
      setRenameError(t("feNameRequired"));
      return;
    }
    if (name === renaming.name) {
      cancelRename();
      return;
    }
    setRenameBusy(true);
    setRenameError("");
    try {
      await renameFsEntry(renaming.path, name, workspaceAbsPath || undefined);
      const parent = renaming.parent;
      cancelRename();
      await loadDir(parent);
      if (parent !== ".") await loadDir(".");
    } catch (e) {
      setRenameError(e instanceof Error ? e.message : String(e));
      setRenameBusy(false);
    }
  }

  function toggleDir(path: string) {
    setExpanded((prev) => {
      const next = !prev[path];
      if (next && !dirs[path]?.entries) void loadDir(path);
      return { ...prev, [path]: next };
    });
  }

  function scheduleOpenFile(path: string) {
    if (openClickTimer.current) window.clearTimeout(openClickTimer.current);
    openClickTimer.current = window.setTimeout(() => {
      void (async () => {
        try {
          const data = await readFileContent(path, workspaceAbsPath || undefined);
          onOpenFile(workspaceAbsPath ? { ...data, workspace: workspaceAbsPath } : data);
        } catch (e) {
          console.error(t("feReadFail", e instanceof Error ? e.message : String(e)));
        }
      })();
    }, 220);
  }

  function renderCreateRow(depth: number) {
    if (!creating) return null;
    return (
      <div className="fe-create-block" style={{ paddingLeft: 8 + depth * 14 }}>
        <div className="fe-row fe-create-row">
          <span className="fe-twist spacer" />
          <FileTypeIcon
            name={newName || (creating.kind === "dir" ? "folder" : "file.txt")}
            kind={creating.kind === "dir" ? "dir" : "file"}
          />
          <input
            className="fe-create-input"
            autoFocus
            value={newName}
            disabled={createBusy}
            placeholder={creating.kind === "dir" ? t("feDirName") : t("feFileName")}
            onChange={(e) => {
              setNewName(e.target.value);
              if (createError) setCreateError("");
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void submitCreate();
              }
              if (e.key === "Escape") {
                e.preventDefault();
                cancelCreate();
              }
            }}
            onClick={(e) => e.stopPropagation()}
          />
          <button
            type="button"
            className="fe-inline-btn ok"
            title={t("feConfirmCreate")}
            disabled={createBusy || !newName.trim()}
            onClick={(e) => {
              e.stopPropagation();
              void submitCreate();
            }}
          >
            <IconCheck size={14} />
          </button>
          <button
            type="button"
            className="fe-inline-btn cancel"
            title={t("cancel")}
            disabled={createBusy}
            onClick={(e) => {
              e.stopPropagation();
              cancelCreate();
            }}
          >
            <IconX size={14} />
          </button>
        </div>
        {createError && <div className="fe-create-error">{createError}</div>}
      </div>
    );
  }

  function renderRenameRow(item: Renaming) {
    return (
      <div className="fe-create-block" style={{ paddingLeft: 8 + item.depth * 14 }}>
        <div className="fe-row fe-create-row">
          <span className="fe-twist spacer" />
          <FileTypeIcon name={newName || item.name} kind={item.kind === "dir" ? "dir" : "file"} />
          <input
            className="fe-create-input"
            autoFocus
            value={newName}
            disabled={renameBusy}
            onChange={(e) => {
              setNewName(e.target.value);
              if (renameError) setRenameError("");
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void submitRename();
              }
              if (e.key === "Escape") {
                e.preventDefault();
                cancelRename();
              }
            }}
            onClick={(e) => e.stopPropagation()}
            onFocus={(e) => e.target.select()}
          />
          <button
            type="button"
            className="fe-inline-btn ok"
            title={t("feConfirmRename")}
            disabled={renameBusy || !newName.trim()}
            onClick={(e) => {
              e.stopPropagation();
              void submitRename();
            }}
          >
            <IconCheck size={14} />
          </button>
          <button
            type="button"
            className="fe-inline-btn cancel"
            title={t("cancel")}
            disabled={renameBusy}
            onClick={(e) => {
              e.stopPropagation();
              cancelRename();
            }}
          >
            <IconX size={14} />
          </button>
        </div>
        {renameError && <div className="fe-create-error">{renameError}</div>}
      </div>
    );
  }

  function renderRowActions(entry: FsEntry, depth: number) {
    const confirming = pendingDeletePath === entry.path;
    return (
      <div className={`fe-row-actions${confirming ? " confirming" : ""}`}>
        {confirming ? (
          <>
            <button
              type="button"
              className="fe-mini-ok"
              title={t("feConfirmDelete")}
              disabled={deleteBusy}
              onClick={(ev) => {
                ev.stopPropagation();
                void deleteEntry(entry);
              }}
            >
              <IconCheck size={13} />
            </button>
            <button
              type="button"
              className="fe-mini-cancel"
              title={t("cancel")}
              disabled={deleteBusy}
              onClick={(ev) => {
                ev.stopPropagation();
                setPendingDeletePath(null);
              }}
            >
              <IconX size={13} />
            </button>
          </>
        ) : (
          <>
            {entry.type === "dir" && (
              <button
                type="button"
                className="fe-mini-add"
                title={t("feNewFileHere")}
                onClick={(ev) => {
                  ev.stopPropagation();
                  setPendingDeletePath(null);
                  setSelectedDir(entry.path);
                  startCreate(entry.path, "file", depth + 1);
                }}
              >
                <IconPlus size={12} />
              </button>
            )}
            <button
              type="button"
              className="fe-mini-del"
              title={t("feDelete")}
              disabled={deleteBusy}
              onClick={(ev) => {
                ev.stopPropagation();
                setPendingDeletePath(entry.path);
              }}
            >
              <IconTrash size={12} />
            </button>
          </>
        )}
      </div>
    );
  }

  function renderEntries(path: string, depth: number): ReactNode {
    const state = dirs[path];
    if (state?.loading) {
      return <div className="fe-hint" style={{ paddingLeft: 12 + depth * 14 }}>{t("feLoading")}</div>;
    }
    if (state?.error) {
      return (
        <div className="fe-hint err" style={{ paddingLeft: 12 + depth * 14 }}>
          {state.error}
        </div>
      );
    }
    const entries = state?.entries || [];
    return (
      <>
        {creating?.parent === path && renderCreateRow(depth)}
        {entries.map((e) => {
          if (renaming?.path === e.path) {
            return <div key={e.path}>{renderRenameRow(renaming)}</div>;
          }
          const isDragging = draggingPath === e.path;
          if (e.type === "dir") {
            const open = Boolean(expanded[e.path]);
            const selected = !shownFile && selectedDir === e.path;
            const isDrop = dropTarget === e.path;
            return (
              <div key={e.path}>
                <div
                  className={`fe-row-wrap${isDrop ? " drop-target" : ""}${isDragging ? " dragging" : ""}`}
                  style={{ paddingLeft: 8 + depth * 14 }}
                  onDragOver={(ev) => onDragOverDir(ev, e.path)}
                  onDragLeave={() => {
                    if (dropTarget === e.path) setDropTarget(null);
                  }}
                  onDrop={(ev) => onDropOnDir(ev, e.path)}
                >
                  <button
                    type="button"
                    className={`fe-row${selected ? " selected" : ""}`}
                    draggable
                    title={absPathFor(e)}
                    onDragStart={(ev) => onDragStart(ev, e.path)}
                    onDragEnd={onDragEnd}
                    onClick={() => {
                      setSelectedDir(e.path);
                      toggleDir(e.path);
                    }}
                    onContextMenu={(ev) => openContextMenu(ev, e, depth)}
                  >
                    <span className="fe-twist">{open ? "▾" : "▸"}</span>
                    <FileTypeIcon name={e.name} kind={open ? "dir-open" : "dir"} />
                    <span
                      className="fe-name"
                      title={t("feDblRename")}
                      onDoubleClick={(ev) => {
                        ev.preventDefault();
                        ev.stopPropagation();
                        startRename(e, depth);
                      }}
                    >
                      {e.name}
                    </span>
                  </button>
                  {renderRowActions(e, depth)}
                </div>
                {open && renderEntries(e.path, depth + 1)}
              </div>
            );
          }
          const fileSelected = sameFsPath(e.path, shownFile, workspaceAbsPath);
          return (
            <div
              key={e.path}
              className={`fe-row-wrap${isDragging ? " dragging" : ""}${fileSelected ? " selected" : ""}`}
              style={{ paddingLeft: 8 + depth * 14 }}
            >
              <button
                type="button"
                className={`fe-row${fileSelected ? " selected" : ""}`}
                data-fe-path={relFsPath(e.path, workspaceAbsPath)}
                draggable
                title={absPathFor(e)}
                onDragStart={(ev) => onDragStart(ev, e.path)}
                onDragEnd={onDragEnd}
                onClick={() => {
                  setPickedFile(e.path);
                  setSelectedDir(parentOf(e.path));
                  scheduleOpenFile(e.path);
                }}
                onContextMenu={(ev) => openContextMenu(ev, e, depth)}
              >
                <span className="fe-twist spacer" />
                <FileTypeIcon name={e.name} kind="file" />
                <span
                  className="fe-name"
                  title={t("feDblRename")}
                  onDoubleClick={(ev) => {
                    ev.preventDefault();
                    ev.stopPropagation();
                    startRename(e, depth);
                  }}
                >
                  {e.name}
                </span>
              </button>
              {renderRowActions(e, depth)}
            </div>
          );
        })}
      </>
    );
  }

  if (collapsed) {
    return (
      <aside className="explorer collapsed">
        <button type="button" className="fe-rail" onClick={onToggle} title={t("feExpand")}>
          <IconFiles size={18} />
          <span>{t("feFiles")}</span>
        </button>
      </aside>
    );
  }

  return (
    <aside className="explorer" style={width && width > 0 ? { width } : { width: "100%", minWidth: 0, flex: 1 }}>
      {!hideChrome && (
      <div className="fe-head">
        <button type="button" className="fe-title" onClick={onToggle} title={t("feCollapse")}>
          <IconFile size={15} />
          <span>{t("feExplorer")}</span>
          <IconChevronDown size={14} />
        </button>
      </div>
      )}
      <div className="fe-actions">
        <button
          type="button"
          title={
            selectedDir && selectedDir !== "."
              ? t("feSelectedDir").replace("{path}", selectedDir)
              : t("feNewFile")
          }
          onClick={() => startCreateInSelected("file")}
        >
          <IconPlus size={13} />
          <span>{t("feNewFile")}</span>
        </button>
        <button
          type="button"
          title={
            selectedDir && selectedDir !== "."
              ? t("feSelectedDir").replace("{path}", selectedDir)
              : t("feNewDir")
          }
          onClick={() => startCreateInSelected("dir")}
        >
          <IconFolder size={13} />
          <span>{t("feNewDir")}</span>
        </button>

        <button
          type="button"
          title={t("feUpload")}
          disabled={uploadBusy}
          onClick={() => uploadInputRef.current?.click()}
        >
          <IconPaperclip size={13} />
          <span>{uploadBusy ? t("feUploading") : t("feUpload")}</span>
        </button>
        <input
          ref={uploadInputRef}
          type="file"
          multiple
          hidden
          onChange={(e) => void uploadFilesToDir(e.target.files, selectedDir || ".")}
        />
        <button
          type="button"
          title={t("feRefresh")}
          onClick={() => {
            const paths = Object.keys(dirs);
            if (paths.length === 0) {
              void loadDir(".");
              return;
            }
            for (const p of paths) void loadDir(p);
          }}
        >
          <IconRefresh size={13} />
          <span>{t("feRefresh")}</span>
        </button>
      </div>
      <button
        type="button"
        className={`fe-root-label${!shownFile && selectedDir === "." ? " selected" : ""}${
          dropTarget === "." ? " drop-target" : ""
        }`}
        title={workspaceAbsPath || rootName}
        onClick={() => setSelectedDir(".")}
        onContextMenu={(ev) => {
          ev.preventDefault();
          setCtxMenu({
            x: ev.clientX,
            y: ev.clientY,
            entry: {
              name: rootName,
              path: ".",
              abs_path: workspaceAbsPath || undefined,
              type: "dir",
            },
            depth: -1,
          });
        }}
        onDragOver={(ev) => onDragOverDir(ev, ".")}
        onDragLeave={() => {
          if (dropTarget === ".") setDropTarget(null);
        }}
        onDrop={(ev) => onDropOnDir(ev, ".")}
      >
        <IconFolder size={13} />
        <span>{rootName}</span>
      </button>
      {actionError && <div className="fe-hint err fe-action-error">{actionError}</div>}
      <div className="fe-tree" ref={treeRef}>{renderEntries(".", 0)}</div>
      {ctxMenu && (
        <div
          className="fe-ctx-menu"
          style={{ left: ctxMenu.x, top: ctxMenu.y }}
          role="menu"
          onClick={(e) => e.stopPropagation()}
          onContextMenu={(e) => e.preventDefault()}
        >
          {ctxMenu.entry.type !== "dir" && (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                const path = ctxMenu.entry.path;
                setCtxMenu(null);
                setPickedFile(path);
                scheduleOpenFile(path);
              }}
            >
              <IconFile size={14} />
              <span>{t("feOpen")}</span>
            </button>
          )}
          <button
            type="button"
            role="menuitem"
            onClick={() => void revealInOs(ctxMenu.entry.path)}
          >
            <IconExternal size={14} />
            <span>{t("feRevealInOs")}</span>
          </button>
          {ctxMenu.depth >= 0 && (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                const { entry, depth } = ctxMenu;
                setCtxMenu(null);
                startRename(entry, depth);
              }}
            >
              <IconFiles size={14} />
              <span>{t("feRename")}</span>
            </button>
          )}
          {ctxMenu.entry.type === "dir" && (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                const { entry, depth } = ctxMenu;
                setCtxMenu(null);
                setSelectedDir(entry.path);
                startCreate(entry.path, "file", Math.max(0, depth) + 1);
              }}
            >
              <IconPlus size={14} />
              <span>{t("feNewFile")}</span>
            </button>
          )}
        </div>
      )}
    </aside>
  );
}
