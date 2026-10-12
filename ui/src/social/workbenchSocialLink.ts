/** Tracks whether the workbench is continuing a social conversation. */
export type WorkbenchSocialLink =
  | { kind: "group"; groupId: string }
  | { kind: "dm"; agentId: string; fromGroupId?: string };

type SocialViewSnapshot =
  | WorkbenchSocialLink
  | { kind: "home" }
  | { kind: string; [k: string]: unknown };

let lastChatView: WorkbenchSocialLink | null = null;
let link: WorkbenchSocialLink | null = null;
const listeners = new Set<() => void>();

function emit() {
  for (const fn of listeners) fn();
}

/** Remember the last group/DM the user was in (home/edit ignored). */
export function rememberSocialView(view: SocialViewSnapshot) {
  if (view.kind === "group" && typeof view.groupId === "string") {
    lastChatView = { kind: "group", groupId: view.groupId };
    return;
  }
  if (view.kind === "dm" && typeof view.agentId === "string") {
    lastChatView = {
      kind: "dm",
      agentId: view.agentId,
      fromGroupId: typeof view.fromGroupId === "string" ? view.fromGroupId : undefined,
    };
  }
}

/** When entering workbench, continue the last social chat if any. */
export function captureWorkbenchSocialLinkFromLastView() {
  link = lastChatView;
  emit();
}

export function getWorkbenchSocialLink(): WorkbenchSocialLink | null {
  return link;
}

export function clearWorkbenchSocialLink() {
  // Drop both the active link and the remembered social chat so a workbench
  // "new chat" cannot resurrect group agents.
  lastChatView = null;
  if (link == null) {
    emit();
    return;
  }
  link = null;
  emit();
}

export function subscribeWorkbenchSocialLink(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}
