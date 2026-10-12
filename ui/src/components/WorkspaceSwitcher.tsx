import { useEffect, useRef, useState } from "react";
import type { WorkspaceItem } from "../api";

type Props = {
  active: { path: string; name: string } | null;
  items: WorkspaceItem[];
  busy?: boolean;
  onSelect: (path: string) => void;
  onBrowse: () => void;
  onOpenSettings: () => void;
};

export function WorkspaceSwitcher({
  active,
  items,
  busy,
  onSelect,
  onBrowse,
  onOpenSettings,
}: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const label = active?.name || "选择工作区";

  return (
    <div className={`ws-switcher ${open ? "open" : ""}`} ref={rootRef}>
      <button
        type="button"
        className="chip muted ws-switcher-trigger"
        title={active?.path || "选择本机文件夹"}
        disabled={busy}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="ws-switcher-label">{label}</span>
        <span className="ws-switcher-caret" aria-hidden>
          ▾
        </span>
      </button>
      {open && (
        <div className="ws-switcher-menu" role="menu">
          <button
            type="button"
            className="ws-switcher-browse"
            disabled={busy}
            onClick={() => {
              setOpen(false);
              onBrowse();
            }}
          >
            文件夹
          </button>
          {items.length > 0 && (
            <>
              <div className="ws-switcher-sep">最近</div>
              {items.map((w) => (
                <button
                  key={w.path}
                  type="button"
                  role="menuitem"
                  className={active?.path === w.path ? "active" : ""}
                  disabled={busy || active?.path === w.path}
                  onClick={() => {
                    setOpen(false);
                    onSelect(w.path);
                  }}
                >
                  <strong>{w.name}</strong>
                  <span>{w.path}</span>
                </button>
              ))}
            </>
          )}
          <button
            type="button"
            className="ws-switcher-settings"
            onClick={() => {
              setOpen(false);
              onOpenSettings();
            }}
          >
            目录设置
          </button>
        </div>
      )}
    </div>
  );
}
