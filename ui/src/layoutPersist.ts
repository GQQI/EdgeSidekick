/** Persist left activity sidebar selection across refresh. */

export type SidePanel =
  | "files"
  | "search"
  | "history"
  | "browser"
  | "git"
  | "undo"
  | "jobs"
  | "schedules";

const SIDE_PANEL_KEY = "sidekick.sidePanel";
const EXPLORER_COLLAPSED_KEY = "sidekick.explorerCollapsed";
const EXPLORER_WIDTH_KEY = "sidekick.explorerWidth";

export function loadSidePanel(): SidePanel {
  try {
    const v = localStorage.getItem(SIDE_PANEL_KEY);
    if (
      v === "search" ||
      v === "history" ||
      v === "files" ||
      v === "browser" ||
      v === "git" ||
      v === "undo" ||
      v === "jobs" ||
      v === "schedules"
    )
      return v;
  } catch {
    /* ignore */
  }
  return "files";
}

export function saveSidePanel(panel: SidePanel): void {
  try {
    localStorage.setItem(SIDE_PANEL_KEY, panel);
  } catch {
    /* ignore quota / private mode */
  }
}

export function loadExplorerCollapsed(): boolean {
  try {
    return localStorage.getItem(EXPLORER_COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

export function saveExplorerCollapsed(collapsed: boolean): void {
  try {
    localStorage.setItem(EXPLORER_COLLAPSED_KEY, collapsed ? "1" : "0");
  } catch {
    /* ignore */
  }
}

export function loadExplorerWidth(fallback = 280): number {
  try {
    const n = Number(localStorage.getItem(EXPLORER_WIDTH_KEY));
    if (Number.isFinite(n) && n >= 200 && n <= 1400) return Math.round(n);
  } catch {
    /* ignore */
  }
  return fallback;
}

export function saveExplorerWidth(width: number): void {
  try {
    localStorage.setItem(EXPLORER_WIDTH_KEY, String(Math.round(width)));
  } catch {
    /* ignore */
  }
}
