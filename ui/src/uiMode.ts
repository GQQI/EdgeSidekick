/** Workbench vs social (consumer) UI shell. Same backend chat engine. */
export type UiMode = "workbench" | "social";

const KEY = "sidekick.uiMode";

export function loadUiMode(): UiMode {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "social" || v === "workbench") return v;
  } catch {
    /* ignore */
  }
  return "workbench";
}

export function saveUiMode(mode: UiMode) {
  try {
    localStorage.setItem(KEY, mode);
  } catch {
    /* ignore */
  }
}
