import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useStickToBottom } from "../hooks/useStickToBottom";
import { browserAct, streamChat, uploadFile, truncateSession } from "../api";
import {
  WORKBENCH_LINKED_AGENT_ID,
  hydrateLinkedDmFromSession,
  isWorkbenchLinkedAgent,
} from "./workbenchBridge";
import {
  rememberSocialView,
} from "./workbenchSocialLink";
import { MarkdownView } from "../components/MarkdownView";
import { formatAssistantDisplay } from "../utils/assistantDisplay";
import { stripToolCallMarkup } from "../utils/chatHelpers";
import { SchedulesPanel } from "../components/SchedulesPanel";
import { ThinkingBlock } from "../components/ThinkingBlock";
import { absorbReasoningStream } from "../utils/streamRepeats";
import { IconChat, IconCheck, IconFiles, IconX, IconPaperclip, IconSearch, IconSend,
  IconCalendar,
  IconGlobe,
  IconPin,
  IconTrash,
  IconUser} from "../components/icons";
import { loadLocale, type MsgKey } from "../i18n";
import { buildSocialBackendMessage, extractOwnReply, stripAgentTags } from "./buildPrompt";
import { AgentAvatar, GroupAvatar } from "./avatars";
import { ConversationList } from "./ConversationList";
import { appendRlTransition, applyOutcome, abstractWorkflow, classifySituation, ensureStrategy, strategyHint } from "./strategy";
import {
  applyHarnessOutcome,
  diagnoseHarness,
  formatHarnessContract,
  formatHarnessNotice,
  isDiscussionRequest,
  planCollaboration,
  shouldUseHarness,
  type CollaborationPlan,
} from "./harness";
import {
  buildReplyDecisionPrompt,
  defaultMaxAgentMessages,
  formatPipelineNotice,
  type ExplicitPipeline,
  parseReplyDecision,
  runGroupConversation,
  type ChatLine,
  type JudgeInput,
  type LoopEvent,
  type ReplyInput,
} from "./groupLoop";
import {
  buildGroupBrief,
  normalizeTaskKey,
  isLightweightSocialTurn,
  isCollectiveGreeting,
  isGroupGreeting,
  greetingFallback,
  groupTurnStatusLabel,
  hasUnfinishedActionCommitment,
  isExecutionRequested,
  isAuthorCallback,
  resolveAuthorId,
  isContinuationRequest,
  isScheduleRequest,
  isDirectAnswerTurn,
  isCapabilityQuestion,
  mergeExecutionTasks,
  doneTasksForExecution,
  canRunTasksInParallel,
  dutyScore,
  pickSoloSpeaker,
  planReactionOrder,
  maxReactionTurns,
  buildSpeakJudgePrompt,
  parseSpeakVerdict,
  visibleReplyAfterJudge,
  formatPeerDiscuss,
  tasksClaimedInText,
  isSelfAccept,
  extractNominees,
  formatMsgTime,
  agentsConfirmedUserTaskDone,
  agentAsksForUserInput,
  isPublicHttpUrl,
  msgFingerprint,
  isNearDuplicateBubble,
  workNoteFromTool,
  findHandoffAgentIds,
  findAskedAgentIds,
  discussHasOwner,
  findPendingDiscussIds,
  resolveExecSlots,
  coordinationAsksInText,
  formatGroupDiscussionMessage,
  sharedFileFromToolEvent,
  pickExecSlots,
  copyBubbleText,
} from "./turnLogic";
import {
  applyAgentOutcome,
  emptyBoard,
  commitConsensusFacts,
  autoExtractFacts,
  beginWorkingRound,
  boardProtocolHint,
  bumpWorkingEntry,
  commitRound,
  applyStandingRules,
  formatBoardForPrompt,
  parseAgentDecision,
  parseStandingRules,
  summarizeReply, cleanDisplayText } from "./sharedBoard";
import {
  agentById,
  agentByName,
  dmKey,
  ensureThread,
  groupKey,
  DEFAULT_SKILLS,
  DEFAULT_TOOLS,
  loadSocialStore,
  nextAgentColor,
  parseMentions,
  resolveGroupTurn,
  mentionsEveryone,
  appendAgentMemory,
  formatAgentMemoryForPrompt,
  saveSocialStore,
  uid,
  getGroupBoard,
  setGroupBoard,
  getDmActiveSession,
  setDmActiveSession,
  startFreshDmSession,
  clearDmThreadOnly,
  getDmSessionMode,
  markDmAware,
  setDmSessionMode,
  getDmStreamNonce,
  bumpDmStreamNonce,
  clearGroupThreadAndMemories,
  isGroupMemoryContext,
  dmMemoryContext} from "./store";
import { formatSpeakerLabel } from "../utils/assistantDisplay";
import type {
  SocialAgent,
  SocialAnnouncement,
  SocialChatMsg,
  SocialFolder,
  SocialGroup,
  SocialStore,
  SocialThreadKey,
} from "./types";

type View =
  | { kind: "home" }
  | { kind: "group"; groupId: string }
  | { kind: "dm"; agentId: string; fromGroupId?: string }
  | { kind: "editAgent"; agentId?: string }
  | { kind: "editGroup"; groupId?: string };

type Props = {
  t: (key: MsgKey, ...args: string[]) => string;
  sessionId: string | null;
  workspacePath: string | null;
  /** Recent workspaces for per-chat binding. */
  workspaces?: { path: string; name: string; id?: string }[];
  /** Soft-adopt a workspace for the explorer without starting a new chat. */
  onAdoptWorkspace?: (path: string) => void;
  onSessionId: (id: string | null) => void;
  onOpenSettings: () => void;
  onToast: (msg: string) => void;
  /** Embed search/files/browser inside group share pane (no left rail). */
  /** Open URL in workbench/social left-sidebar browser (BrowserPanel). */
  onOpenBrowserUrl?: (url: string) => void;
  /** Close workbench/native browser opened via onOpenBrowserUrl. */
  onCloseBrowserUrl?: () => void;
  /** Bumps when App opens the sandbox so social can switch the left share tab to browser. */
  browserOpenNonce?: number;
  renderCollabTool?: (kind: "search" | "files" | "browser") => ReactNode;
  /** Reload workbench transcript from the shared session (after social linked DM). */
  onWorkbenchReload?: () => void;
  /** Clear the shared workbench session transcript (linked DM clear). */
  onWorkbenchClear?: () => void | Promise<void>;
  /** Switch UI to workbench mode. */
  onOpenWorkbench?: () => void;
  /** Hide the native browser while a dialog (settings) is covering the page. */
  browserHeld?: boolean;
  /** When workbench continues a social chat, lock to this view. */
  forcedView?: View;
  /** Hide full-page chrome when mounted inside the workbench chat pane. */
  embedded?: boolean;
};


type SocialAttachment = {
  id: string;
  name: string;
  path: string;
  kind: string;
  text: string;
  size: number;
};

type AgentLivePhase = "idle" | "reading" | "deciding" | "queued" | "executing" | "synthesizing" | "done" | "error";
type AgentActivityKind = "read" | "decide" | "ask" | "claim" | "execute" | "tool" | "done" | "skip";
type AgentActivity = {
  id: string;
  kind: AgentActivityKind;
  detail: string;
  at: number;
};
type AgentLiveState = {
  phase: AgentLivePhase;
  tools: { id: string; name: string; status: "running" | "done" | "error"; detail?: string }[];
  /** Detailed, per-turn activity belongs in the member's task detail, not the chat timeline. */
  activity?: AgentActivity[];
  summary?: string;
  updatedAt: number;
};

function isActiveAgentPhase(phase: AgentLivePhase): boolean {
  return phase === "reading" || phase === "deciding" || phase === "queued" || phase === "executing" || phase === "synthesizing";
}

function agentLivePhaseLabel(phase: AgentLivePhase): string {
  const labels: Record<AgentLivePhase, string> = {
    idle: "待命",
    reading: "读取中",
    deciding: "判断中",
    queued: "已认领，排队中",
    executing: "处理中",
    synthesizing: "汇总中",
    done: "已完成",
    error: "处理失败",
  };
  return labels[phase];
}

function agentActivityLabel(kind: AgentActivityKind): string {
  const labels: Record<AgentActivityKind, string> = {
    read: "已读",
    decide: "判断",
    ask: "提问",
    claim: "认领",
    execute: "执行",
    tool: "工具",
    done: "完成",
    skip: "跳过",
  };
  return labels[kind];
}


export function SocialShell({
  t,
  sessionId,
  workspacePath,
  workspaces = [],
  onAdoptWorkspace,
  onSessionId,
  onOpenSettings,
  onToast,
  renderCollabTool,
  onWorkbenchReload,
  onWorkbenchClear,
  onOpenWorkbench,
  onOpenBrowserUrl,
  browserOpenNonce,
  browserHeld = false,
  forcedView,
  embedded = false,
}: Props) {
  const [store, setStore] = useState<SocialStore>(() => loadSocialStore());
  const [view, setView] = useState<View>(() => forcedView || { kind: "home" });


  useEffect(() => {
    rememberSocialView(view);
  }, [view]);

  useEffect(() => {
    if (forcedView) setView(forcedView);
  }, [forcedView]);
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<SocialAttachment[]>([]);
  const [attachBusy, setAttachBusy] = useState(false);
  const attachInputRef = useRef<HTMLInputElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const [busyThreads, setBusyThreads] = useState<Record<string, boolean>>({});
  const [agentLive, setAgentLive] = useState<Record<string, AgentLiveState>>({});
  const [quote, setQuote] = useState<string | null>(null);
  const [quoteMenu, setQuoteMenu] = useState<{ x: number; y: number; text: string } | null>(null);
  const selectGesture = useRef({ x: 0, y: 0 });
  useEffect(() => {
    const clearPaint = () => {
      const css = window.CSS as unknown as { highlights?: { delete: (name: string) => void } } | undefined;
      try { css?.highlights?.delete("social-select"); } catch { /* ignore */ }
    };
    const onDown = (ev: PointerEvent) => {
      const el = ev.target as HTMLElement | null;
      if (el?.closest?.(".social-quote-menu")) return;
      selectGesture.current = { x: ev.clientX, y: ev.clientY };
      clearPaint();
      setQuoteMenu(null);
      // A press inside a bubble starts a new selection. Anywhere else dismisses it.
      if (!el?.closest?.(".social-bubble")) {
        try { window.getSelection()?.removeAllRanges(); } catch { /* ignore */ }
      }
    };
    document.addEventListener("pointerdown", onDown, true);
    return () => document.removeEventListener("pointerdown", onDown, true);
  }, []);
  const [ctx, setCtx] = useState({ tokens: 0, limit: 48000 });
  const [ctxByAgent, setCtxByAgent] = useState<Record<string, { tokens: number; limit: number; windowLimit?: number }>>({});
  const [homeSearch, setHomeSearch] = useState("");
  /** all | none | folder:<id> */
  const [homeFolderFilter, setHomeFolderFilter] = useState<string>("all");
  const [folderDraft, setFolderDraft] = useState("");
  const [folderCreateOpen, setFolderCreateOpen] = useState(false);
  const [createMenuOpen, setCreateMenuOpen] = useState(false);
  const [createWizard, setCreateWizard] = useState<null | "group" | "agent">(null);
  const [configSheet, setConfigSheet] = useState<
    null | { kind: "agent"; id: string } | { kind: "group"; id: string }
  >(null);
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionFilter, setMentionFilter] = useState("");
  const [shareCollapsed, setShareCollapsed] = useState(() => {
    try { return localStorage.getItem("sidekick.social.shareCollapsed") === "1"; } catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem("sidekick.social.shareCollapsed", shareCollapsed ? "1" : "0"); } catch { /* ignore */ }
  }, [shareCollapsed]);
  const [shareTab, setShareTab] = useState<"ann" | "members" | "search" | "files" | "schedule" | "browser">("schedule");
  const [scheduleRefresh, setScheduleRefresh] = useState(0);

  const browserHoldRef = useRef(false);
  /** User dismissed the browser this turn — don't auto-reopen on later tool_start. */
  const browserUserClosedRef = useRef(false);
  /**
   * Tracks the agent-opened page for the left share-pane browser tab
   * (same BrowserPanel mount as workbench, via renderCollabTool("browser")).
   */
  const [browserCard, setBrowserCard] = useState<{
    url: string;
    threadKey: string;
    agent?: { name: string; color: string; avatar?: string };
  } | null>(null);

  const openSocialBrowserSidebar = useCallback(() => {
    setShareCollapsed(false);
    setShareTab("browser");
  }, []);

  // App-driven sandbox open (settings confirm, etc.) — mirror workbench by
  // revealing the left share pane browser tab so BrowserPanel can mount.
  useEffect(() => {
    if (!browserOpenNonce) return;
    openSocialBrowserSidebar();
  }, [browserOpenNonce, openSocialBrowserSidebar]);

  // Desktop: the live browser is a native BrowserView painted ABOVE the DOM.
  // When the fullscreen sheet closes, the mini preview card re-attaches the
  // SAME view at its own (small) bounds — so only hide when no live card
  // exists. The card-aware hide effect lives below the `messages` decl.
  // Leaving the social shell (e.g. switching to the workbench) must not leak
  // the floating view either — regardless of whether it was shown by the
  // fullscreen sheet or by the mini preview card.
  const [agentPanel, setAgentPanel] = useState<{ agentId: string; groupId?: string } | null>(null);
  const [agentPanelTab, setAgentPanelTab] = useState<"actions" | "tasks">("actions");
  const [listPage, setListPage] = useState(0);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const threadScrollerRef = useRef<HTMLDivElement | null>(null);
  const abortByThreadRef = useRef<Record<string, AbortController>>({});
  /** Tracks focused social thread so background group turns cannot steal the browser UI. */
  const threadKeyRef = useRef<SocialThreadKey | null>(null);
  const workLogsRef = useRef<Record<string, string[]>>(store.workLogs || {});
  const busyThreadsRef = useRef<Record<string, boolean>>({});
  /** Bumped on clearThread so in-flight send paint/finally cannot rewrite cleared bubbles. */
  const clearEpochRef = useRef<Record<string, number>>({});
  /** Remount message list after clear so ghost bubbles cannot linger in the DOM. */
  const [clearTick, setClearTick] = useState(0);

  // Transient loaders are never durable conversation state. A crash, reload,
  // or a closed desktop window must not resurrect an old “处理中” indicator
  // from local storage on the next launch.
  useEffect(() => {
    setBusyThreads({});
    setAgentLive({});
    setStore((prev) => {
      let changed = false;
      const next = structuredClone(prev);
      for (const [key, rows] of Object.entries(next.threads)) {
        const cleaned = rows
          .filter((row) => {
            const transientSystem = row.role === "system" && (row.readingStatus || Boolean((row as any).collaboration));
            if (transientSystem) changed = true;
            return !transientSystem;
          })
          .map((row) => {
            if (!row.streaming && !row.reading && !row.reasoningStreaming) return row;
            changed = true;
            return { ...row, streaming: false, reading: false, reasoningStreaming: false };
          });
        next.threads[key] = cleaned;
      }
      if (changed) saveSocialStore(next);
      return changed ? next : prev;
    });

    const abortAll = () => {
      for (const controller of Object.values(abortByThreadRef.current)) {
        try { controller.abort(); } catch {}
      }
      abortByThreadRef.current = {};
    };
    window.addEventListener("pagehide", abortAll);
    return () => {
      window.removeEventListener("pagehide", abortAll);
      abortAll();
    };
  }, []);


  const patchStore = useCallback(
    (fn: (s: SocialStore) => SocialStore) => {
      setStore((prev) => {
        const next = fn(structuredClone(prev));
        saveSocialStore(next);
        return next;
      });
    },
    [],
  );

  // Scheduled runs stream on the workbench session. Mirror tokens into the linked DM.
  useEffect(() => {
    const bubble = { id: "" };
    const onEv = (e: Event) => {
      const ev = (e as CustomEvent<{ type?: string; ts?: number; data?: Record<string, unknown> }>).detail;
      if (!ev || !sessionId) return;
      const sid = String(ev.data?.session_id || "");
      if (sid !== sessionId) return;
      const key = dmKey(WORKBENCH_LINKED_AGENT_ID, sid);
      const taskId = String(ev.data?.task_id || "task");
      if (ev.type === "schedule_trigger" && String(ev.data?.event || "") === "started") {
        const stamp = Math.floor(Number(ev.ts || Date.now()));
        bubble.id = `sched-live-${taskId}-${stamp}`;
        const title = String(ev.data?.title || "").trim();
        patchStore((s) => {
          const agent = s.agents.find((a) => a.id === WORKBENCH_LINKED_AGENT_ID);
          const rows = s.threads[key] || [];
          s.threads[key] = [
            ...rows,
            {
              id: `sched-note-${taskId}-${stamp}`,
              role: "system",
              text: `定时任务 · ${title}`,
              at: Date.now(),
            },
            {
              id: bubble.id,
              role: "agent",
              agentId: WORKBENCH_LINKED_AGENT_ID,
              agentName: agent?.name || "助手",
              text: "",
              streaming: true,
              at: Date.now(),
            },
          ];
          return s;
        });
        return;
      }
      if (ev.type === "assistant_delta") {
        const chunk = String(ev.data?.chunk ?? "");
        const reset = Boolean(ev.data?.reset);
        const discard = Boolean(ev.data?.discard);
        if (!bubble.id) bubble.id = `sched-live-${sid}`;
        patchStore((s) => {
          const rows = [...(s.threads[key] || [])];
          let idx = rows.findIndex((m) => m.id === bubble.id);
          if (idx < 0) {
            const agent = s.agents.find((a) => a.id === WORKBENCH_LINKED_AGENT_ID);
            rows.push({
              id: bubble.id,
              role: "agent",
              agentId: WORKBENCH_LINKED_AGENT_ID,
              agentName: agent?.name || "助手",
              text: "",
              streaming: true,
              at: Date.now(),
            });
            idx = rows.length - 1;
          }
          const cur = rows[idx];
          if (discard) {
            rows[idx] = { ...cur, text: "", streaming: true };
          } else {
            rows[idx] = { ...cur, text: reset ? chunk : `${cur.text || ""}${chunk}`, streaming: true };
          }
          s.threads[key] = rows;
          return s;
        });
        return;
      }
      if (ev.type === "final") {
        const text = String(ev.data?.text || "");
        const id = bubble.id;
        bubble.id = "";
        if (!id) return;
        patchStore((s) => {
          s.threads[key] = (s.threads[key] || []).map((m) =>
            m.id === id ? { ...m, streaming: false, text: (m.text || "").trim() ? m.text : text } : m,
          );
          return s;
        });
      }
    };
    window.addEventListener("sidekick-schedule-event", onEv);
    return () => window.removeEventListener("sidekick-schedule-event", onEv);
  }, [sessionId, patchStore]);

  useEffect(() => {
    const bump = () => setScheduleRefresh((n) => n + 1);
    window.addEventListener("sidekick-schedule", bump);
    return () => window.removeEventListener("sidekick-schedule", bump);
  }, []);

  const agents = store.agents;
  const groups = store.groups;
  const folders: SocialFolder[] = store.folders || [];
  const sortedGroups = useMemo(
    () =>
      [...groups].sort(
        (a, b) =>
          Number(b.pinned) - Number(a.pinned) ||
          (a.sort ?? 0) - (b.sort ?? 0) ||
          a.name.localeCompare(b.name),
      ),
    [groups],
  );
  const sortedAgents = useMemo(
    () =>
      [...agents].sort(
        (a, b) =>
          Number(b.pinned) - Number(a.pinned) ||
          (a.sort ?? 0) - (b.sort ?? 0) ||
          a.name.localeCompare(b.name),
      ),
    [agents],
  );

  useEffect(() => {
    setListPage(0);
  }, [homeSearch, homeFolderFilter]);

  const conversationList = useMemo(() => {
    const q = homeSearch.trim().toLowerCase();
    const folderMatch = (folderId?: string) => {
      if (homeFolderFilter === "all") return true;
      if (homeFolderFilter === "none") return !folderId;
      if (homeFolderFilter.startsWith("folder:")) return folderId === homeFolderFilter.slice(7);
      return true;
    };
    type Row =
      | { kind: "group"; id: string; pinned?: boolean; sort?: number; name: string; at: number; preview: string; folderId?: string }
      | { kind: "agent"; id: string; pinned?: boolean; sort?: number; name: string; at: number; preview: string; folderId?: string };
    const rows: Row[] = [];
    for (const g of groups) {
      if (!folderMatch(g.folderId)) continue;
      const hay = [g.name, g.remark, g.location, g.topic, folders.find((f) => f.id === g.folderId)?.name]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      if (q && !hay.includes(q)) continue;
      const thr = store.threads[groupKey(g.id)] || [];
      const last = thr[thr.length - 1];
      const preview =
        (last?.text || "").slice(0, 40) ||
        [g.remark, g.location, g.topic || t("socialMembersCount", String(g.memberIds.length))]
          .filter(Boolean)
          .join(" · ");
      rows.push({ kind: "group", id: g.id, pinned: g.pinned, sort: g.sort, name: g.name, at: last?.at || 0, preview, folderId: g.folderId });
    }
    for (const a of agents) {
      if (!folderMatch(a.folderId)) continue;
      const hay = [a.name, a.remark, a.location, a.title, a.duty, a.blurb, folders.find((f) => f.id === a.folderId)?.name]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      if (q && !hay.includes(q)) continue;
      const thr = store.threads[dmKey(a.id)] || [];
      const last = thr[thr.length - 1];
      const preview =
        (last?.text || "").slice(0, 40) ||
        [a.remark, a.location, a.title, a.duty || a.blurb].filter(Boolean).join(" · ");
      rows.push({ kind: "agent", id: a.id, pinned: a.pinned, sort: a.sort, name: a.name, at: last?.at || 0, preview, folderId: a.folderId });
    }
    rows.sort(
      (a, b) =>
        Number(!!b.pinned) - Number(!!a.pinned) ||
        (b.at || 0) - (a.at || 0) ||
        (a.sort ?? 0) - (b.sort ?? 0) ||
        a.name.localeCompare(b.name),
    );
    return rows;
  }, [groups, agents, folders, store.threads, homeSearch, homeFolderFilter, t]);

  const LIST_PAGE_SIZE = 10;
  const listPageCount = Math.max(1, Math.ceil(conversationList.length / LIST_PAGE_SIZE));
  const safeListPage = Math.min(listPage, listPageCount - 1);
  const pagedConversationList = useMemo(
    () => conversationList.slice(safeListPage * LIST_PAGE_SIZE, (safeListPage + 1) * LIST_PAGE_SIZE),
    [conversationList, safeListPage],
  );

  const activeGroup = useMemo(() => {
    if (view.kind === "group") return groups.find((g) => g.id === view.groupId) || null;
    if (view.kind === "dm" && view.fromGroupId)
      return groups.find((g) => g.id === view.fromGroupId) || null;
    return null;
  }, [view, groups]);

  const activeAgent = useMemo(() => {
    if (view.kind === "dm") return agentById(agents, view.agentId) || null;
    return null;
  }, [view, agents]);

  const foldersSorted = useMemo(
    () => [...folders].sort((a, b) => (a.sort ?? 0) - (b.sort ?? 0) || a.name.localeCompare(b.name)),
    [folders],
  );

  /** Per-chat workspace: agent/group binding wins over the global active workspace. */
  const boundWorkspace = useMemo(() => {
    if (view.kind === "dm") return (activeAgent?.workspace || "").trim();
    if (view.kind === "group") return (activeGroup?.workspace || "").trim();
    return "";
  }, [view, activeAgent?.workspace, activeGroup?.workspace]);

  const chatWorkspacePath = boundWorkspace || workspacePath || null;

  const bindWorkspaceToCurrent = useCallback(
    (path: string) => {
      const clean = String(path || "").trim();
      if (!clean) return;
      if (view.kind === "dm") {
        patchStore((s) => {
          const a = s.agents.find((x) => x.id === view.agentId);
          if (a) a.workspace = clean;
          return s;
        });
      } else if (view.kind === "group") {
        patchStore((s) => {
          const g = s.groups.find((x) => x.id === view.groupId);
          if (g) g.workspace = clean;
          return s;
        });
      }
      onAdoptWorkspace?.(clean);
    },
    [view, patchStore, onAdoptWorkspace],
  );

  const focusChatId =
    view.kind === "dm" ? view.agentId : view.kind === "group" ? view.groupId : "";
  // When entering a chat that has its own workspace, soft-adopt it for the file pane.
  useEffect(() => {
    if (view.kind !== "dm" && view.kind !== "group") return;
    if (!boundWorkspace) return;
    if (boundWorkspace === (workspacePath || "")) return;
    onAdoptWorkspace?.(boundWorkspace);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only re-adopt when the focused chat or its binding changes
  }, [view.kind, focusChatId, boundWorkspace]);

  // Hub / explorer workspace switches while in social: bind only this chat.
  useEffect(() => {
    const onBind = (ev: Event) => {
      const path = String((ev as CustomEvent<{ path?: string }>).detail?.path || "").trim();
      if (!path) return;
      if (view.kind === "dm" || view.kind === "group") {
        bindWorkspaceToCurrent(path);
      } else {
        onAdoptWorkspace?.(path);
      }
    };
    window.addEventListener("sidekick-social-bind-workspace", onBind);
    return () => window.removeEventListener("sidekick-social-bind-workspace", onBind);
  }, [view, bindWorkspaceToCurrent, onAdoptWorkspace]);

  const createFolder = () => {
    const name = folderDraft.trim();
    if (!name) {
      onToast(t("socialNeedFolderName"));
      return;
    }
    const id = uid("folder");
    patchStore((s) => {
      if (!s.folders) s.folders = [];
      s.folders.push({ id, name, sort: s.folders.length });
      return s;
    });
    setFolderDraft("");
    setFolderCreateOpen(false);
    setHomeFolderFilter("folder:" + id);
    onToast(t("socialFolderCreated", name));
  };

  const deleteFolder = (folderId: string) => {
    const folder = folders.find((f) => f.id === folderId);
    if (!folder) return;
    if (!window.confirm(t("socialDeleteFolderConfirm", folder.name))) return;
    patchStore((s) => {
      s.folders = (s.folders || []).filter((f) => f.id !== folderId);
      for (const a of s.agents) {
        if (a.folderId === folderId) a.folderId = "";
      }
      for (const g of s.groups) {
        if (g.folderId === folderId) g.folderId = "";
      }
      return s;
    });
    if (homeFolderFilter === "folder:" + folderId) setHomeFolderFilter("all");
    onToast(t("socialFolderDeleted", folder.name));
  };


    const threadKey: SocialThreadKey | null = useMemo(() => {
    if (view.kind === "group") return groupKey(view.groupId);
    if (view.kind === "dm") {
      const sid = getDmActiveSession(store, view.agentId);
      return dmKey(view.agentId, sid);
    }
    return null;
  }, [view, store.dmActiveSession]);

  const threadBusy = threadKey ? !!busyThreads[threadKey] : false;
  threadKeyRef.current = threadKey;
  busyThreadsRef.current = busyThreads;

  // Switching group/DM clears the shared browser so the previous turn cannot keep showing.
  const prevSocialThreadRef = useRef<SocialThreadKey | null>(null);
  useEffect(() => {
    const prev = prevSocialThreadRef.current;
    prevSocialThreadRef.current = threadKey;
    if (prev && threadKey && prev !== threadKey) {
      browserUserClosedRef.current = true;
      setBrowserCard(null);
    }
  }, [threadKey]);

  useEffect(() => {
    if (!threadKey) return;
    setStore((prev) => {
      if (prev.threads[threadKey]?.length) return prev;
      const welcome =
        view.kind === "group"
          ? t("socialGroupWelcome")
          : view.kind === "dm"
            ? t("socialDmWelcome", activeAgent?.name || "")
            : t("socialWelcome");
      const next = structuredClone(prev);
      ensureThread(next, threadKey, welcome);
      saveSocialStore(next);
      return next;
    });
  }, [threadKey, view.kind, activeAgent?.name, t]);

  const messages: SocialChatMsg[] = threadKey ? store.threads[threadKey] || [] : [];
  const lastBubbleSig = messages.length
    ? `${messages[messages.length - 1]?.id}:${String(messages[messages.length - 1]?.text || "").length}`
    : "0";
  useStickToBottom(threadScrollerRef, [messages.length, threadBusy, clearTick], { threshold: 96 });

  // Card-aware hide (desktop): after the fullscreen sheet closes, only hide
  // the native BrowserView when no live preview card exists — the card
  // re-attaches the SAME view at its own (small) bounds, and a blind hide()
  // here would race and override the card's show().

  useEffect(() => {
    const el = composerRef.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = Math.min(Math.max(el.scrollHeight, 44), 168) + "px";
  }, [input]);

  const groupMembers = useCallback(
    (g: SocialGroup) => g.memberIds.map((id) => agentById(agents, id)).filter(Boolean) as SocialAgent[],
    [agents],
  );

  const mentionCandidates = useMemo(() => {
    const pool =
      view.kind === "group" && activeGroup
        ? groupMembers(activeGroup)
        : agents;
    const f = mentionFilter.trim().toLowerCase();
    if (!f) return pool;
    return pool.filter(
      (a) =>
        a.name.toLowerCase().includes(f) ||
        a.title.toLowerCase().includes(f) ||
        a.id.toLowerCase().includes(f),
    );
  }, [view, activeGroup, agents, mentionFilter, groupMembers]);

  const onInputChange = (v: string) => {
    setInput(v);
    if (view.kind !== "group") {
      setMentionOpen(false);
      return;
    }
    const m = /(^|\s)@([\w\u4e00-\u9fff]*)$/.exec(v);
    if (m) {
      setMentionOpen(true);
      setMentionFilter(m[2] || "");
    } else {
      setMentionOpen(false);
    }
  };

  const insertMention = (a: SocialAgent) => {
    setInput((prev) => prev.replace(/(^|\s)@([\w\u4e00-\u9fff]*)$/, `$1@${a.name} `));
    setMentionOpen(false);
  };

  const setThreadMessages = (key: SocialThreadKey, msgs: SocialChatMsg[]) => {
    patchStore((s) => {
      s.threads[key] = msgs;
      return s;
    });
  };

  const rewardNotesFor = (userText: string) => strategyHint(store.strategy, userText);

  const rateReply = (key: SocialThreadKey, msgId: string, score: number) => {
    patchStore((s) => {
      const thread = s.threads[key] || [];
      const msg = thread.find((m) => m.id === msgId);
      if (!msg || msg.role !== "agent") return s;
      msg.score = score;
      const idx = thread.indexOf(msg);
      const priorUser = [...thread.slice(0, idx)].reverse().find((m) => m.role === "user");
      const userText = String(priorUser?.text || "");
      const situation = classifySituation(userText);
      const success = score >= 4;
      const reply = String(msg.text || "");
      const workflow = success ? abstractWorkflow(reply) : "";
      s.strategy = applyOutcome(ensureStrategy(s.strategy), situation, success, score, workflow);
      const policy = s.strategy[situation].policy;
      appendRlTransition({
        id: uid("rl"),
        at: Date.now(),
        threadKey: key,
        agentId: msg.agentId || "",
        agentName: msg.agentName || "",
        situation,
        state: { userText },
        action: { reply: String(msg.text || ""), policy },
        reward: score,
        rewardSource: "user_score",
      });
      return s;
    });
  };

  const clearThread = (key: SocialThreadKey) => {
    // Bump epoch first so any in-flight paint's setStore updater refuses to rewrite.
    clearEpochRef.current[key] = (clearEpochRef.current[key] || 0) + 1;
    setClearTick((n) => n + 1);
    // Drop the browser preview bubble if it belongs to the deleted thread.
    setBrowserCard((c) => (c && c.threadKey === key ? null : c));
    // Stop in-flight social runs so their paint/finally cannot resurrect bubbles.
    try {
      abortByThreadRef.current[key]?.abort();
    } catch {}
    delete abortByThreadRef.current[key];
    setBusyThreads((prev) => {
      if (!prev[key]) return prev;
      const n = { ...prev };
      delete n[key];
      return n;
    });

    if (key.startsWith("dm:")) {
      const parsed = key.slice(3);
      const colon = parsed.indexOf(":");
      const agentId = colon >= 0 ? parsed.slice(0, colon) : parsed;
      const epochAtClear = clearEpochRef.current[key] || 0;
      setStore((prev) => {
        if ((clearEpochRef.current[key] || 0) !== epochAtClear) return prev;
        const next = clearDmThreadOnly(prev, agentId);
        // Ensure the active thread key is empty of chat bubbles (notice-only).
        const sid = getDmActiveSession(next, agentId);
        const dk = dmKey(agentId, sid);
        const notice = (next.threads[dk] || []).filter((m) => m.role === "system").slice(-1);
        next.threads = { ...next.threads, [dk]: notice };
        // Also wipe legacy key forms if present
        if (dk !== key) next.threads[key] = notice;
        // Wipe bare dm:agentId if sessions differ
        const bare = `dm:${agentId}` as SocialThreadKey;
        if (bare !== dk && bare !== key) next.threads[bare] = notice;
        saveSocialStore(next);
        return next;
      });
      setCtx({ tokens: 0, limit: 48000 });
      setCtxByAgent((prev) => {
        const n = { ...prev };
        delete n[agentId];
        return n;
      });
      setAgentLive((prev) => {
        const n = { ...prev };
        delete n[agentId];
        return n;
      });
      if (isWorkbenchLinkedAgent(agentId)) {
        leadHydrateStampRef.current = "";
        void Promise.resolve(onWorkbenchClear?.()).finally(() => onToast(t("socialClearDmToast")));
      } else {
        onToast(t("socialClearDmToast"));
      }
      return;
    }
    if (key.startsWith("group:")) {
      const gid = key.slice("group:".length);
      const epochAtClear = clearEpochRef.current[key] || 0;
      setStore((prev) => {
        // Drop racing writes that still see the old epoch.
        if ((clearEpochRef.current[key] || 0) !== epochAtClear) return prev;
        const next = clearGroupThreadAndMemories(prev, gid);
        const gk = groupKey(gid);
        const notice = (next.threads[gk] || []).filter((m) => m.role === "system").slice(-1);
        // Keep only the clear notice — never leave agent bubbles behind.
        next.threads = { ...next.threads, [gk]: notice, [key]: notice };
        saveSocialStore(next);
        return next;
      });
      setCtx({ tokens: 0, limit: 48000 });
      setCtxByAgent({});
      setAgentLive({});
      // Late stream budget events can land after abort; re-clear on next tick.
      const epochAtClear2 = epochAtClear;
      const gk = key;
      queueMicrotask(() => {
        if ((clearEpochRef.current[gk] || 0) !== epochAtClear2) return;
        setCtx({ tokens: 0, limit: 48000 });
        setCtxByAgent({});
        setAgentLive({});
      });
      onToast(t("socialClearGroupToast"));
      return;
    }
    patchStore((s) => {
      s.threads[key] = [];
      return s;
    });
    setCtx({ tokens: 0, limit: 48000 });
    onToast(t("socialThreadCleared"));
  };

  const updateAgentField = (id: string, patch: Partial<SocialAgent>) => {
    patchStore((s) => {
      const a = s.agents.find((x) => x.id === id);
      if (a) Object.assign(a, patch);
      return s;
    });
    if (patch.workspace && view.kind === "dm" && view.agentId === id) {
      onAdoptWorkspace?.(String(patch.workspace));
    }
  };


  const updateGroupField = (
    id: string,
    patch: Partial<SocialGroup>,
    opts?: { syncName?: boolean },
  ) => {
    let syncedName: string | null = null;
    patchStore((s) => {
      const g = s.groups.find((x) => x.id === id);
      if (!g) return s;
      const oldName = g.name;
      Object.assign(g, patch);
      if (opts?.syncName && patch.name && patch.name !== oldName) {
        syncedName = patch.name;
        const tagRe = /所属群组[\uFF1A:][^\n]*/;
        const line = "所属群组\uFF1A" + patch.name;
        for (const mid of g.memberIds) {
          const a = s.agents.find((x) => x.id === mid);
          if (!a) continue;
          const swap = (v?: string) =>
            (v || "").split(oldName).join(patch.name!).replace(tagRe, line);
          a.persona = swap(a.persona) || a.persona;
          a.blurb = swap(a.blurb) || a.blurb;
          a.duty = swap(a.duty) || a.duty;
          if (!(a.persona || "").includes("所属群组")) {
            a.persona = ((a.persona || "").trim() + "\n" + line).trim();
          }
        }
      }
      return s;
    });
    if (syncedName) onToast(t("socialGroupRenamedSync", syncedName));

    if (patch.workspace && view.kind === "group" && view.groupId === id) {
      onAdoptWorkspace?.(String(patch.workspace));
    }
  };

  const removeAgentFromGroup = (groupId: string, agentId: string) => {
    const g = store.groups.find((x) => x.id === groupId);
    if (!g) return;
    if ((g.memberIds || []).length <= 1) {
      onToast(t("socialNeedMembers"));
      return;
    }
    if (!(g.memberIds || []).includes(agentId)) return;
    patchStore((s) => {
      const gg = s.groups.find((x) => x.id === groupId);
      if (!gg) return s;
      gg.memberIds = (gg.memberIds || []).filter((id) => id !== agentId);
      return s;
    });
    onToast(t("socialRemovedFromGroup"));
  };

  const deleteAgent = (agentId: string) => {
    const a = store.agents.find((x) => x.id === agentId);
    if (!a) return;
    if (store.agents.length <= 1) {
      onToast(t("socialNeedOneAgent"));
      return;
    }
    if (!window.confirm(t("socialDeleteAgentConfirm", a.name))) return;
    patchStore((s) => {
      s.agents = s.agents.filter((x) => x.id !== agentId);
      for (const g of s.groups) {
        g.memberIds = (g.memberIds || []).filter((id) => id !== agentId);
      }
      const nextThreads: typeof s.threads = { ...s.threads };
      for (const key of Object.keys(nextThreads)) {
        if (key === `dm:${agentId}` || key.startsWith(`dm:${agentId}:`)) {
          delete nextThreads[key as keyof typeof nextThreads];
        }
      }
      s.threads = nextThreads;
      if (s.agentMemories) {
        const mem = { ...s.agentMemories };
        delete mem[agentId];
        s.agentMemories = mem;
      }
      if (s.dmActiveSession) {
        const dm = { ...s.dmActiveSession };
        delete dm[agentId];
        s.dmActiveSession = dm;
      }
      if (s.dmSessionMode) {
        const mode = { ...s.dmSessionMode };
        delete mode[agentId];
        s.dmSessionMode = mode;
      }
      return s;
    });
    setAgentPanel(null);
    setConfigSheet(null);
    if (view.kind === "dm" && view.agentId === agentId) {
      setView({ kind: "home" });
    } else if (view.kind === "editAgent" && view.agentId === agentId) {
      setView({ kind: "home" });
    }
    onToast(t("socialAgentDeleted", a.name));
  };

  const deleteGroup = (groupId: string) => {
    const g = store.groups.find((x) => x.id === groupId);
    if (!g) return;
    if (!window.confirm(t("socialDeleteGroupConfirm", g.name))) return;
    patchStore((s) => {
      s.groups = s.groups.filter((x) => x.id !== groupId);
      const nextThreads = { ...s.threads };
      delete nextThreads[groupKey(groupId)];
      s.threads = nextThreads;
      if (s.boards) {
        const boards = { ...s.boards };
        delete boards[groupId];
        s.boards = boards;
      }
      if (s.agentMemories) {
        const mem = { ...s.agentMemories };
        for (const [aid, note] of Object.entries(mem)) {
          const notes = (note.notes || []).filter((n) => !isGroupMemoryContext(n.context, groupId));
          if (notes.length !== (note.notes || []).length) {
            mem[aid] = { notes, updatedAt: Date.now() };
          }
        }
        s.agentMemories = mem;
      }
      return s;
    });
    setAgentPanel((panel) => (panel?.groupId === groupId ? null : panel));
    setConfigSheet(null);
    if (
      (view.kind === "group" && view.groupId === groupId) ||
      (view.kind === "editGroup" && view.groupId === groupId)
    ) {
      setView({ kind: "home" });
    }
    onToast(t("socialGroupDeleted", g.name));
  };

  async function addAttachments(files: FileList | null) {
    if (!files || files.length === 0) return;
    setAttachBusy(true);
    try {
      const next = [...attachments];
      for (const file of Array.from(files)) {
        const uploaded = await uploadFile(file, {
          dest: "_uploads/",
          workspace: chatWorkspacePath || undefined,
        });
        const text =
          typeof uploaded.content === "string"
            ? uploaded.content
            : typeof uploaded.preview === "string"
              ? uploaded.preview
              : "";
        next.push({
          id: uid("att"),
          name: uploaded.name || file.name,
          path: uploaded.path,
          kind: String(uploaded.kind || "file"),
          text: text.slice(0, 12000),
          size: uploaded.size ?? file.size,
        });
      }
      setAttachments(next);
      onToast(t("socialAttach"));
    } catch (e) {
      onToast(e instanceof Error ? e.message : t("socialAttachFail"));
    } finally {
      setAttachBusy(false);
      if (attachInputRef.current) attachInputRef.current.value = "";
    }
  }

  function buildTextWithAttachments(userText: string) {
    if (attachments.length === 0) return userText;
    const blocks = attachments.map((a) => {
      const head = `【附件: ${a.name} | ${a.path}】`;
      if (a.text && a.text.trim()) return `${head}\n${a.text}`;
      return `${head}\n${t("attachBinary")}`;
    });
    return `${userText.trim()}\n\n` + blocks.join("\n\n");
  }


  const send = async () => {
    const raw = input.trim();
    if ((!raw && attachments.length === 0) || !threadKey) return;
    if (view.kind !== "group" && view.kind !== "dm") return;
    // Group chat: a new user message interrupts the running round (the old
    // round is aborted below and can no longer write). DMs keep waiting.
    if (threadBusy && view.kind !== "group") return;

    const quoteText = quote;
    const pendingAttachments = attachments;
    const text = buildTextWithAttachments(
      (quoteText ? `【引用】\n${quoteText}\n【/引用】\n\n` : "") + (raw || t("socialAttach")),
    );
    const userMsg: SocialChatMsg = {
      id: uid("m"),
      role: "user",
      text: raw || pendingAttachments.map((a) => a.name).join(", "),
      quote: quoteText || undefined,
      attachments: pendingAttachments.map((a) => ({ name: a.name, path: a.path, kind: a.kind })),
      at: Date.now(),
    };
    setQuote(null);
    // Coordination cards are transient status/audit UI, never conversational
    // history for the next model turn.
    const hist = [
      ...messages
        .filter((m) => !m.readingStatus)
        .map((m) =>
          m.streaming || m.reasoningStreaming || m.reading
            ? { ...m, streaming: false, reasoningStreaming: false, reading: false }
            : m,
        ),
      userMsg,
    ];
    setThreadMessages(threadKey, hist);
    setInput("");
    setAttachments([]);
    setMentionOpen(false);
    const key = threadKey;
    const currentWorkSite = () => (workLogsRef.current[key] || []).slice(-6).join("\n");
    const rememberWork = (who: string, toolName: string, data: { args?: Record<string, unknown> | string; preview?: string; result?: string; content?: string; ok?: boolean; error?: unknown }) => {
      let args = data?.args;
      if (typeof args === "string") {
        try {
          args = JSON.parse(args) as Record<string, unknown>;
        } catch {
          args = undefined;
        }
      }
      const note = workNoteFromTool(who, toolName, {
        args: args && typeof args === "object" ? args : undefined,
        preview: String(data?.preview || ""),
        result: String(data?.result || data?.content || ""),
        ok: data?.ok !== false && !data?.error,
      });
      if (!note) return;
      const prev = workLogsRef.current[key] || [];
      if (prev[prev.length - 1] === note) return;
      const next = [...prev, note].slice(-12);
      workLogsRef.current = { ...workLogsRef.current, [key]: next };
      patchStore((s) => ({ ...s, workLogs: { ...(s.workLogs || {}), [key]: next } }));
    };
    browserUserClosedRef.current = false;
    browserHoldRef.current = false;
    setBusyThreads((prev) => ({ ...prev, [key]: true }));

    const rosterForPrompt =
      view.kind === "group" && activeGroup ? groupMembers(activeGroup) : agents;
    // Chat: every member reads each new message and may answer.
    // A work order still stays with one operator.
    const groupId = view.kind === "group" ? view.groupId : null;
    const turn =
      view.kind === "group"
        ? resolveGroupTurn(raw || text, rosterForPrompt)
        : null;
    const mentioned = turn?.addressed || parseMentions(raw || text, rosterForPrompt);
    const mentionedIds = new Set(mentioned.map((a) => a.id));
    // 「继续执行」没有新的点名时，还是交给刚才说话的那个人，不要换人。
    if (mentionedIds.size === 0 && !turn?.everyone && isContinuationRequest(raw || text)) {
      const lastSpeakerId = [...messages]
        .reverse()
        .find((row) => row.role === "agent" && row.agentId && !row.readingStatus)?.agentId;
      if (lastSpeakerId && rosterForPrompt.some((agent) => agent.id === lastSpeakerId)) {
        mentionedIds.add(lastSpeakerId);
      }
    }
    // 「谁写的谁来改」指向已经做过这件事的人，不要按职责再挑一个调试或审查。
    if (view.kind === "group" && mentionedIds.size === 0 && !turn?.everyone && isAuthorCallback(raw || text)) {
      const authorId = resolveAuthorId(
        rosterForPrompt,
        groupId ? getGroupBoard(store, groupId).tasks : [],
        workLogsRef.current[key] || [],
        messages,
      );
      if (authorId) mentionedIds.add(authorId);
    }
    const executionRequested = view.kind === "group" && isExecutionRequested(raw || text);
    const directAnswerTurn =
      view.kind === "group" && isDirectAnswerTurn(raw || text, mentioned.length) && !turn?.everyone;
    const lightweightSocialTurn =
      view.kind === "group" && isLightweightSocialTurn(raw || text) && !turn?.everyone;
    const collectiveGreeting = view.kind === "group" && isCollectiveGreeting(raw || text);
    const multiVoice =
      view.kind === "group" &&
      (collectiveGreeting ||
        Boolean(turn?.everyone) ||
        isDiscussionRequest(raw || text) ||
        /大家都|各位都|每个人|所有人|分别说|各自|一起说|都回复|都回答/.test(raw || text));
    const scheduleRequest = isScheduleRequest(raw || text);
    const targets =
      view.kind === "dm"
        ? activeAgent
          ? [activeAgent]
          : []
        : turn
          ? turn.targets
          : rosterForPrompt;
    // Group: @ names a lead. Every member still sees the message and may stay silent.
    const optionalReply = view.kind === "group" ? Boolean(turn?.optionalReply) : false;
    const turnExclusive = Boolean(turn?.exclusive);
    const addressedNames = (turn?.addressed || mentioned).map((a) => a.name);

    if (!targets.length) {
      setBusyThreads((prev) => {
        const n = { ...prev };
        delete n[key];
        return n;
      });
      onToast(t("socialEmptyReply"));
      return;
    }

    type Slot = {
      id: string;
      agent: (typeof targets)[number];
      text: string;
      streaming: boolean;
      silent?: boolean;
      decided?: boolean;
      /** Group optional: only true after DECISION:reply — gates bubble paint. */
      replyConfirmed?: boolean;
      reasoning?: string;
      reasoningStreaming?: boolean;
      /** Current lifecycle stage for status UI; independent from whether a bubble is visible. */
      stage: "reading" | "deciding" | "queued" | "executing" | "done" | "error";
      /** Live browser preview attached to this agent's bubble. */
      browserUrl?: string;
      browserBusy?: boolean;
    };
    const slots: Slot[] = targets.map((agent) => ({
      id: uid("m"),
      agent,
      text: "",
      // Group members start idle.  The decision pass wakes one at a time so
      // the status row describes the member currently evaluating the message,
      // rather than incorrectly claiming the whole group is executing.
      streaming: view.kind !== "group",
      silent: view.kind === "group",
      decided: false,
      replyConfirmed: false,
      reasoning: "",
      reasoningStreaming: false,
      stage: view.kind === "group" ? "reading" : "executing",
    }));
    const noteAgentActivity = (
      agent: { id?: string; name: string },
      kind: AgentActivityKind,
      detail: string,
    ) => {
      if (view.kind !== "group" || !agent.id) return;
      const clean = String(detail || "").replace(/\s+/g, " ").trim();
      if (!clean) return;
      setAgentLive((prev) => {
        const current = prev[agent.id!] || {
          phase: "reading" as AgentLivePhase,
          tools: [],
          updatedAt: Date.now(),
        };
        const activity = [...(current.activity || [])];
        const last = activity[activity.length - 1];
        // Streaming tool deltas are updates to one operation, not a flood of
        // indistinguishable task-detail rows.
        if (last && last.kind === kind) {
          last.detail = clean.slice(0, 180);
          last.at = Date.now();
        } else {
          activity.push({ id: uid("activity"), kind, detail: clean.slice(0, 180), at: Date.now() });
        }
        return {
          ...prev,
          [agent.id!]: { ...current, activity: activity.slice(-24), updatedAt: Date.now() },
        };
      });
    };
    // Work orders keep one operator. Conversation asks the whole roster.
    // Greetings and ordinary chat are a shared floor. One speaker is only
    // for a work order, where a second mouth would repeat the same job.
    const reactionFloor = view.kind === "group" && !executionRequested;
    const directAnswerAgentId =
      directAnswerTurn && mentionedIds.size === 0 && !reactionFloor
        ? pickSoloSpeaker(slots.map((slot) => slot.agent), raw || text)?.id
        : undefined;
    const singleVoice = view.kind === "group" && !multiVoice && mentionedIds.size === 0 && !reactionFloor;
    const soloSpeakerId = singleVoice
      ? directAnswerAgentId || pickSoloSpeaker(slots.map((slot) => slot.agent), raw || text)?.id
      : undefined;
    if (view.kind === "group") {
      setAgentLive((prev) => {
        const next = { ...prev };
        for (const slot of slots) {
          next[slot.agent.id] = {
            phase: "reading",
            tools: [],
            activity: [{ id: uid("activity"), kind: "read", detail: "已收到消息，各自判断要不要回复", at: Date.now() }],
            summary: "正在读取本轮消息…",
            updatedAt: Date.now(),
          };
        }
        return next;
      });
    }

    // Per-thread controllers so a group turn and a DM can run together;
    // starting one must not steal / null the other's AbortController.
    try {
      abortByThreadRef.current[key]?.abort();
    } catch {}
    const ac = new AbortController();
    abortByThreadRef.current[key] = ac;
    /** Set when we pause for the human — late stream deltas must not revive「思考中」. */
    let turnPaused = false;
    const pauseForUserInput = (agentName: string, text: string, force = false): boolean => {
      if (ac.signal.aborted || turnPaused) return true;
      if (!force && !agentAsksForUserInput(text)) return false;
      turnPaused = true;
      try {
        ac.abort();
      } catch {}
      // Clear "输出中 / 进行中" so the UI does not look stuck while waiting on the user.
      for (const s of slots) {
        s.streaming = false;
        s.reasoningStreaming = false;
        if (s.stage === "reading" || s.stage === "queued" || s.stage === "deciding") s.stage = "done";
        if (!s.decided) {
          s.decided = true;
          s.silent = !String(s.text || "").trim();
        }
      }
      setAgentLive((prev) => {
        const next: typeof prev = { ...prev };
        for (const id of Object.keys(next)) {
          const cur = next[id];
          if (!cur) continue;
          next[id] = {
            ...cur,
            phase: isActiveAgentPhase(cur.phase) ? "done" : cur.phase,
            summary: isActiveAgentPhase(cur.phase) ? "已暂停，等待你回复" : cur.summary,
            updatedAt: Date.now(),
          };
        }
        return next;
      });
      const notice = {
        id: uid("sys"),
        role: "system" as const,
        text: `${agentName || "智能体"}需要你确认或补充信息。已暂停本轮，请在输入框回复后继续。`,
        at: Date.now(),
      };
      try {
        committedMsgs.push(notice as any);
        paint();
      } catch {}
      return true;
    };
    const confirmationText = (agentId: string, spoken: string) => {
      if (agentAsksForUserInput(spoken)) return spoken;
      for (let i = committedMsgs.length - 1; i >= 0; i -= 1) {
        const row = committedMsgs[i];
        if (row.agentId === agentId && agentAsksForUserInput(row.text || "")) return row.text || "";
      }
      return spoken;
    };

    const sendEpoch = clearEpochRef.current[key] || 0;
    /** A newer send on this thread took over (user sent a new message). */
    const isSuperseded = () => {
      const current = abortByThreadRef.current[key];
      return Boolean(current && current !== ac);
    };
    const isStaleSend = () => (clearEpochRef.current[key] || 0) !== sendEpoch || isSuperseded();
    // Guarded write: refuse inside the setStore updater if clear bumped epoch after paint began.
    const writeThreadMessages = (msgs: SocialChatMsg[]) => {
      if (isStaleSend()) return;
      setStore((prev) => {
        if ((clearEpochRef.current[key] || 0) !== sendEpoch || isSuperseded()) return prev;
        const next = structuredClone(prev);
        const scoreById = new Map(
          (prev.threads[key] || []).filter((row) => row.score).map((row) => [row.id, row.score] as const),
        );
        next.threads[key] = msgs.map((row) => {
          const kept = scoreById.get(row.id);
          if (!kept || row.score) return row;
          return { ...row, score: kept };
        });
        saveSocialStore(next);
        return next;
      });
    };

    // Plan-wave bubbles stay visible while execute wave runs with new slot ids.
    let committedMsgs: Array<{
      id: string;
      role: "agent";
      agentId: string;
      agentName: string;
      text: string;
      streaming: boolean;
      reading: boolean;
      attachments?: Array<{ name: string; path: string; kind?: string }>;
      reasoning?: string;
      reasoningStreaming?: boolean;
      browserUrl?: string;
      browserBusy?: boolean;
      at: number;
    }> = [];
    const harnessNotices: Array<{ id: string; role: "system"; text: string; at: number; collaboration: true }> = [];
    // Group messages always use the free-form reply loop (groupLoop.ts). No
    // router, no default pipeline; a pipeline runs only when the user asks.
    const freeGroupMode: boolean = view.kind === "group";
    let freeStatus = freeGroupMode ? `${targets.length} 位成员已看到消息，正在各自判断要不要回复…` : "";
    /** Set while one member writes its reply inside the free-form loop. */
    type FreeReplyState = {
      history: SocialChatMsg[];
      latest: { author: string; isUser: boolean; text: string };
      addressed: boolean;
      step?: { index: number; total: number; task: string; nextName?: string };
      brief?: boolean;
      everyone?: boolean;
      mustAnswer?: boolean;
      action?: boolean;
      tools: boolean;
    };
    let freeReply: FreeReplyState | null = null;
    let harnessPlan: CollaborationPlan | null = null;
    let harnessRepair = "";
    let harnessRepaired = false;
    let harnessPrior: ReturnType<typeof diagnoseHarness> | null = null;
    const sharedFileKeys = new Set<string>();
    const commitGroupMessage = (slot: Slot, message: string) => {
      const text = cleanDisplayText(message).trim();
      if (!text || isNearDuplicateBubble(slot.agent.id, text, committedMsgs)) return false;
      committedMsgs.push({
        id: slot.id,
        role: "agent",
        agentId: slot.agent.id,
        agentName: slot.agent.name,
        text,
        streaming: false,
        reading: false,
        reasoning: (slot.reasoning || "").trim() || undefined,
        reasoningStreaming: false,
        at: Date.now(),
      });
      return true;
    };
    const commitSharedFileMessage = (slot: Slot, path: string) => {
      if (view.kind !== "group") return false;
      const cleanPath = String(path || "").trim();
      if (!cleanPath) return false;
      const dedupeKey = `${slot.agent.id}::${cleanPath.toLowerCase()}`;
      if (sharedFileKeys.has(dedupeKey)) return false;
      sharedFileKeys.add(dedupeKey);
      const name = cleanPath.replace(/\\/g, "/").split("/").filter(Boolean).pop() || cleanPath;
      committedMsgs.push({
        id: uid("m"),
        role: "agent",
        agentId: slot.agent.id,
        agentName: slot.agent.name,
        text: `已将文件共享到群里：${name}`,
        attachments: [{ name, path: cleanPath, kind: "file" }],
        streaming: false,
        reading: false,
        at: Date.now(),
      });
      noteAgentActivity(slot.agent, "tool", `已共享文件：${cleanPath}`);
      return true;
    };
    const flushSlotsToCommitted = () => {
    const seen = new Set(committedMsgs.map((m) => msgFingerprint(String(m.agentId || ""), String(m.text || ""))));
    const tryPush = (msg: (typeof committedMsgs)[number]) => {
      const fp = msgFingerprint(String(msg.agentId || ""), String(msg.text || ""));
      if (fp.endsWith("::") || seen.has(fp)) return false;
      if (isNearDuplicateBubble(String(msg.agentId || ""), String(msg.text || ""), committedMsgs)) return false;
      seen.add(fp);
      committedMsgs.push(msg);
      return true;
    };

      for (const s of slots) {
        if (s.silent) continue;
        const rawText = cleanDisplayText(String(s.text || ""));
        const reason = (s.reasoning || "").trim();
        if (!rawText && !reason) continue;
        // Skip if this bubble was already committed (prevents identical double paint).
        if (committedMsgs.some((m) => m.id === s.id || (m.agentId === s.agent.id && m.text === rawText))) {
          s.text = "";
          s.reasoning = "";
          s.streaming = false;
          s.silent = true;
          s.decided = true;
          s.browserUrl = undefined;
          s.browserBusy = undefined;
          s.replyConfirmed = false;
          continue;
        }
        tryPush({
          id: s.id,
          role: "agent",
          agentId: s.agent.id,
          agentName: s.agent.name,
          text: rawText,
          streaming: false,
          reading: false,
          reasoning: reason || undefined,
          reasoningStreaming: false,
          browserUrl: s.browserUrl || undefined,
          browserBusy: s.browserBusy,
          at: Date.now(),
        });
        // Clear slot after commit so paint([...committedMsgs, ...replySlots]) cannot draw it twice.
        s.text = "";
        s.reasoning = "";
        s.streaming = false;
        s.silent = true;
        s.decided = true;
        s.browserUrl = undefined;
        s.browserBusy = undefined;
        s.replyConfirmed = false;
      }
    };
    const readingStatusId = `reading-${key}`;
    const paint = () => {
      if (isStaleSend()) return;
      const pending = slots.filter((s) => !s.decided && !s.replyConfirmed);
      const committedIds = new Set(committedMsgs.map((m) => m.id));
      const committedFingerprints = new Set(
        committedMsgs.map((m) => `${m.agentId}::${(m.text || "").trim()}`),
      );
      const replySlots = slots.filter((s) => {
        if (s.silent && !s.browserUrl) return false;
        // The first group pass contains protocol tokens while it streams.
        // Its finished natural-language message is committed separately, so
        // the user sees a stable bubble rather than a half-parsed placeholder.
        if ((s as any)._suppressDecisionBubble) return false;
        if (committedIds.has(s.id)) return false;
        const rawPreview = cleanDisplayText(String(s.text || "")).trim();
        if (rawPreview && committedFingerprints.has(`${s.agent.id}::${rawPreview}`)) return false;
        // Group: never paint until DECISION:reply confirmed — stops thinking flash + vanish.
        if (optionalReply && !s.replyConfirmed && !(s.decided && !s.silent) && !s.browserUrl) return false;
        const rawText = cleanDisplayText(
          s.decided
            ? String(s.text || "")
            : String(extractOwnReply(s.text, s.agent.name) || stripAgentTags(s.text || "") || ""),
        );
        const reason = s.replyConfirmed || s.decided ? (s.reasoning || "").trim() : "";
        // Keep the in-flight bubble mounted while streaming once reply is confirmed,
        // even if a single delta briefly yields empty extractOwnReply.
        if (s.browserUrl) return true;
        // Mount the bubble as soon as this member is speaking, including while
        // a tool is still running, so the reply is not a single dump afterwards.
        if (s.streaming && s.replyConfirmed && !(s as any)._suppressDecisionBubble) return true;
        if (s.streaming && s.replyConfirmed && (rawText || reason || rawPreview)) return true;
        return !!(rawText || reason);
      });
      // The status row is driven by lifecycle state, not a fixed member count.
      // It exposes both the current member and the members waiting to read the
      // same turn, while keeping actual answer bubbles independent.
      const freeSpeaker = freeGroupMode ? slots.find((s) => s.streaming && s.stage === "executing") : undefined;
      const freeActivity = freeSpeaker ? String((freeSpeaker as { activityText?: string }).activityText || "").trim() : "";
      const statusLabel = freeGroupMode
        ? freeSpeaker
          ? `${freeSpeaker.agent.name}${freeActivity ? `：${freeActivity}` : " 正在回复…"}`
          : freeStatus
        : groupTurnStatusLabel(
        slots.map((s) => ({
          name: s.agent.name,
          stage: s.stage,
          streaming: s.streaming,
          activityText: (s as { activityText?: string }).activityText,
        })),
      );
      // Keep only a lightweight typing/read indicator in the transcript.
      // Detailed work activity lives in the member task panel; actual group
      // communication is committed below as ordinary agent bubbles.
      const statusMsgs = statusLabel
        ? [{ id: readingStatusId, role: "system" as const, text: statusLabel, readingStatus: true, at: Date.now() }]
        : [];
      if (isStaleSend()) return;
      // Loader always last → sits under the newest bubbles.
      writeThreadMessages([
        ...hist,
        ...harnessNotices,
        ...committedMsgs,
        ...replySlots.map((s) => {
          const rawText = cleanDisplayText(
            s.decided
              ? String(s.text || "")
              : String(extractOwnReply(s.text, s.agent.name) || stripAgentTags(s.text || "") || ""),
          );
          const reason =
            s.replyConfirmed || s.decided ? (s.reasoning || "").trim() : "";
          return {
            id: s.id,
            role: "agent" as const,
            agentId: s.agent.id,
            agentName: s.agent.name,
            text: rawText,
            streaming: s.streaming,
            reading: false,
            reasoning: reason || undefined,
            reasoningStreaming: !turnPaused && !!s.reasoningStreaming && !!(s.replyConfirmed || s.decided),
            browserUrl: s.browserUrl || undefined,
            browserBusy: s.browserBusy,
            at: Date.now(),
          };
        }),
        ...statusMsgs,
      ]);
      requestAnimationFrame(() => {
        const box = threadScrollerRef.current;
        if (!box) return;
        const dist = box.scrollHeight - box.scrollTop - box.clientHeight;
        // Only pin inside the thread scroller — never scrollIntoView (that
        // scrolls ancestor pages and feels like the chat jumps upward).
        if (dist > 120) return;
        box.scrollTop = box.scrollHeight;
      });
    };
    paint(); // one shared reading status line — no per-agent bubbles yet


    let liveBoard = groupId ? getGroupBoard(store, groupId) : null;
    if (groupId && liveBoard && optionalReply) {
      liveBoard = beginWorkingRound(liveBoard, raw || text, targets);
      patchStore((prev) => setGroupBoard(prev, groupId, liveBoard!));
    }

    let peerDiscuss: Array<{ agentId: string; agentName: string; text: string }> = [];
    const yieldToForegroundDm = async () => {
      // Group turns share the same model quota; pause between agents while a
      // foreground DM is busy so the DM first reply is not stuck behind the queue.
      if (!String(key).startsWith("group:")) return;
      while (!ac.signal.aborted) {
        const fg = threadKeyRef.current;
        if (!fg || !String(fg).startsWith("dm:")) break;
        if (!busyThreadsRef.current[fg]) break;
        await new Promise((r) => setTimeout(r, 200));
      }
    };

    const runOne = async (slot: Slot, wave: 1 | 2, opts?: { synthesis?: boolean }) => {
      if (ac.signal.aborted) return;
      (slot as { _goalSatisfied?: boolean })._goalSatisfied = false;
      (slot as { _goalBlocked?: boolean })._goalBlocked = false;
      (slot as { _goalKnown?: boolean })._goalKnown = false;
      (slot as { _toolErrors?: number })._toolErrors = 0;
      // Wave 1 is private orchestration, not an answer stream. Keep model
      // output for routing and claims, but never render it midway.
      (slot as any)._suppressDecisionBubble =
        view.kind === "group" && wave === 1 && !(slot as { _clearedToSpeak?: boolean })._clearedToSpeak;
      if (freeReply) {
        noteAgentActivity(
          slot.agent,
          "execute",
          freeReply.step
            ? `流水线第 ${freeReply.step.index}/${freeReply.step.total} 步：${freeReply.step.task}`
            : `决定回复 ${freeReply.latest.author} 的消息，正在写`,
        );
      } else if (view.kind === "group") {
        const assigned = (((slot as any)._tasks as string[]) || []).filter(Boolean);
        noteAgentActivity(
          slot.agent,
          wave === 1 ? "decide" : "execute",
          wave === 1
            ? "正在判断是否需要处理，以及是否需要同事补充信息"
            : assigned.length
              ? `正在执行：${assigned.join("；")}`
              : opts?.synthesis
                ? "正在整合已完成成员的结果"
                : "正在处理已认领事项",
        );
      }
      await yieldToForegroundDm();
      if (ac.signal.aborted) return;
      // Exclusive address => all current targets must reply.
      // Open floor => everyone optional. DM => must reply.
      const slotOptional = optionalReply;
      // Everyone performs the read/judge pass.  For a direct general question,
      // one best-fit member is the answerer and peers remain true bystanders;
      // this prevents six near-identical introductions from appearing at once.
      const peerHandoff = Boolean((slot as { _peerHandoff?: boolean })._peerHandoff);
      const turnRole: "open" | "addressee" | "bystander" =
        view.kind !== "group"
          ? "addressee"
          : peerHandoff
            ? "addressee"
          : collectiveGreeting
            ? "addressee"
            : soloSpeakerId && slot.agent.id === soloSpeakerId
              ? "addressee"
            : directAnswerTurn && directAnswerAgentId
              ? slot.agent.id === directAnswerAgentId
                ? "addressee"
                : "bystander"
              : mentionedIds.size === 0
            ? "open"
            : mentionedIds.has(slot.agent.id)
              ? "addressee"
              : "bystander";
      const boardText =
        view.kind === "group" && liveBoard ? formatBoardForPrompt(liveBoard) : "";
      const privateMemoryText = formatAgentMemoryForPrompt(
        store,
        slot.agent.id,
        12,
        view.kind === "dm"
          ? view.fromGroupId
            ? { includeGroupContext: true, groupId: view.fromGroupId }
            : { includeGroupContext: false, onlyDm: true }
          : view.kind === "group"
            ? { groupId: view.groupId, includeGroupContext: true }
            : undefined,
      );
      const freeTurn = freeReply;
      const backendMsg = freeTurn
        ? buildSocialBackendMessage({
            userText: text,
            targets: [slot.agent],
            history: freeTurn.history,
            historyLimit: 24,
            roster: rosterForPrompt,
            mode: "group",
            group: activeGroup,
            optionalReply: true,
            privateMemory: privateMemoryText,
            rewardNotes: rewardNotesFor(text),
            scheduleRequest,
            workSite: currentWorkSite(),
            freeChat: {
              latest: freeTurn.latest,
              addressed: freeTurn.addressed,
              pipelineStep: freeTurn.step,
              brief: freeTurn.brief,
              everyone: freeTurn.everyone,
              mustAnswer: freeTurn.mustAnswer,
            },
          })
        : buildSocialBackendMessage({
        userText: text,
        targets: [slot.agent],
        // Execution workers need the actual in-turn decisions, not merely the
        // transcript from before this send.  This also makes a handoff
        // deterministic instead of asking the next owner to rediscover work.
        history: wave === 2 ? [...messages, ...committedMsgs] : messages,
        historyLimit: turnRole === "bystander" ? 4 : 12,
        dmFresh: view.kind === "dm" && !view.fromGroupId,
        groupBrief:
          view.kind === "dm" && view.fromGroupId
            ? buildGroupBrief(store, view.fromGroupId)
            : undefined,
        roster: rosterForPrompt,
        mode: view.kind === "dm" ? "dm" : "group",
        group: activeGroup,
        discussAll: false,
        optionalReply: slotOptional,
        // For a direct question, only the addressed member answers.  The
        // others still evaluate the same message as bystanders and can remain
        // silent without being turned into task workers.
        directAnswer:
          directAnswerTurn &&
          (turnRole === "addressee" || slot.agent.id === directAnswerAgentId),
        collectiveGreeting,
        lightweightSocial: lightweightSocialTurn,
        synthesis: Boolean(opts?.synthesis),
        useDecisionProtocol: false,
        clearedToSpeak: Boolean((slot as { _clearedToSpeak?: boolean })._clearedToSpeak),
        sentenceOnly: Boolean((slot as { _sentenceOnly?: boolean })._sentenceOnly),
        scheduleRequest,
        workSite: currentWorkSite(),
        assignedTasks:
          wave === 2
            ? ((((slot as any)._tasks as string[]) || []).map((task) => String(task || "").trim()).filter(Boolean))
            : undefined,
        boardText,
        boardWave: view.kind === "group" ? wave : undefined,
        peerDiscussText:
          view.kind === "group" && peerDiscuss.length
            ? formatPeerDiscuss(peerDiscuss.map((r) => ({ agentName: r.agentName, text: r.text })))
            : undefined,
        privateMemory: formatAgentMemoryForPrompt(
          store,
          slot.agent.id,
          12,
          view.kind === "dm"
            ? view.fromGroupId
              ? { includeGroupContext: true, groupId: view.fromGroupId }
              : { includeGroupContext: false, onlyDm: true }
            : view.kind === "group"
              ? { groupId: view.groupId, includeGroupContext: true }
              : undefined,
        ),
        turnRole,
        addressedNames,
        peerHandoff,
        rewardNotes: rewardNotesFor(text),
        harnessContract: harnessPlan
          ? formatHarnessContract(harnessPlan, slot.agent.id, {
              repair: harnessRepair,
              synthesizing: Boolean(opts?.synthesis),
            })
          : undefined,
        omitPeerRoster: Boolean(harnessPlan),
      });
      let buf = "";
      let lastGoodBuf = "";
      // Each model round is its own bubble. Reset used to erase the previous
      // sentence or append the next one into the same bubble.
      const sealVisibleRound = () => {
        const visible = cleanDisplayText(
          extractOwnReply(buf, slot.agent.name) || stripAgentTags(buf),
        ).trim();
        const reason = (slot.reasoning || "").trim();
        if (!visible && !reason) return;
        if (visible && isNearDuplicateBubble(slot.agent.id, visible, committedMsgs)) {
          slot.id = uid("m");
          slot.text = "";
          slot.reasoning = "";
          slot.reasoningStreaming = false;
          return;
        }
        committedMsgs.push({
          id: slot.id,
          role: "agent",
          agentId: slot.agent.id,
          agentName: slot.agent.name,
          text: visible,
          streaming: false,
          reading: false,
          reasoning: reason || undefined,
          reasoningStreaming: false,
          browserUrl: slot.browserUrl || undefined,
          browserBusy: slot.browserBusy,
          at: Date.now(),
        });
        slot.id = uid("m");
        slot.text = "";
        slot.reasoning = "";
        slot.reasoningStreaming = false;
        slot.replyConfirmed = true;
        slot.streaming = true;
        paint();
      };
      try {
        // Each parallel group agent needs its OWN session. null still collapses
        // on some backends; a unique id keeps assistant_delta isolated.
        const linkedDm = view.kind === "dm" && isWorkbenchLinkedAgent(slot.agent.id);
        const streamSessionId = linkedDm
          ? sessionId
          : view.kind === "dm"
            ? `social-dm-${slot.agent.id}-${getDmActiveSession(store, slot.agent.id)}-n${getDmStreamNonce(store, slot.agent.id)}`
            : `social-${groupId || "g"}-${slot.agent.id}-w${wave}-${slot.id}`;
        setAgentLive((prev) => ({
          ...prev,
          [slot.agent.id]: {
            ...prev[slot.agent.id],
            phase: slot.stage === "deciding" ? "deciding" : "executing",
            tools: prev[slot.agent.id]?.tools || [],
            summary: slot.stage === "deciding" ? "正在读取本轮消息并判断是否需要处理…" : "正在处理已认领事项…",
            updatedAt: Date.now(),
          },
        }));
        await streamChat(
          backendMsg,
          streamSessionId,
          {
            onEvent: (ev) => {
              if (turnPaused || ac.signal.aborted || isStaleSend()) return;
              const anyEv = ev as any;
              if (anyEv?.type === "goal_settled") {
                (slot as { _goalKnown?: boolean })._goalKnown = true;
                (slot as { _goalSatisfied?: boolean })._goalSatisfied = anyEv?.data?.satisfied === true;
                (slot as { _goalBlocked?: boolean })._goalBlocked = anyEv?.data?.blocked === true;
                (slot as { _goalReason?: string })._goalReason = String(anyEv?.data?.reason || "");
              }
              if (anyEv?.type === "assistant_status") {
                const statusText = String(anyEv?.data?.text || anyEv?.data?.message || "").trim();
                if (statusText) {
                  (slot as { activityText?: string }).activityText = statusText;
                  paint();
                }
              }
              if (anyEv?.type === "session") {
                const nid = String(anyEv?.data?.session_id || "");
                // Only bind DM / single-agent streams back to the workbench session.
                // Linked lead DM shares the workbench session; bind it. Other social streams stay isolated.
                if (
                  nid &&
                  !optionalReply &&
                  (linkedDm || (view.kind !== "dm" && !String(streamSessionId || "").startsWith("social-")))
                ) {
                  onSessionId(nid);
                }
              }
              if (anyEv?.type === "tool_start" || anyEv?.type === "tool_end" || anyEv?.type === "tool_call_delta") {
                const toolName = String(
                  anyEv?.data?.name || anyEv?.data?.tool || anyEv?.data?.tool_name || "tool",
                );
                const toolId = String(anyEv?.data?.id || anyEv?.data?.tool_id || toolName);
                const detail = String(
                  anyEv?.data?.detail || anyEv?.data?.summary || anyEv?.data?.args_preview || "",
                ).slice(0, 200);
                const toolBlob = `${toolName}\n${detail}\n${String(anyEv?.data?.result || anyEv?.data?.content || "")}`;
                if (/wait_for_user|needs_human/i.test(toolBlob)) browserHoldRef.current = true;
                if (anyEv?.type === "tool_start" && /^ask_user$/i.test(toolName)) {
                  sealVisibleRound();
                  const question = String(
                    anyEv?.data?.args?.question || anyEv?.data?.question || detail || "请用户确认后再继续",
                  );
                  pauseForUserInput(slot.agent.name, question, true);
                }
                const toolFailed = Boolean(anyEv?.type === "tool_end" && (anyEv?.data?.ok === false || anyEv?.data?.error));
                if (
                  anyEv?.type === "tool_end" &&
                  (toolName === "create_scheduled_task" || toolName === "cancel_scheduled_task") &&
                  !toolFailed
                ) {
                  window.dispatchEvent(new CustomEvent("sidekick-schedule"));
                }
                if (toolFailed) {
                  (slot as { _toolErrors?: number })._toolErrors = ((slot as { _toolErrors?: number })._toolErrors || 0) + 1;
                } else if (anyEv?.type === "tool_end") {
                  rememberWork(slot.agent.name, toolName, anyEv?.data || {});
                }
                setAgentLive((prev) => {
                  const cur: (typeof prev)[string] = prev[slot.agent.id] || {
                    phase: slot.stage === "deciding" ? "deciding" : "executing",
                    tools: [],
                    updatedAt: Date.now(),
                  };
                  const tools = [...cur.tools];
                  const idx = tools.findIndex((x) => x.id === toolId || x.name === toolName);
                  const status =
                    anyEv?.type === "tool_end"
                      ? ((anyEv?.data?.ok === false || anyEv?.data?.error) ? "error" : "done")
                      : "running";
                  const row = {
                    id: toolId,
                    name: toolName,
                    status: status as "running" | "done" | "error",
                    detail: detail || undefined,
                  };
                  if (idx >= 0) tools[idx] = { ...tools[idx], ...row };
                  else tools.push(row);
                  return {
                    ...prev,
                    [slot.agent.id]: {
                      ...cur,
                      phase: slot.stage === "deciding" ? "deciding" : "executing",
                      tools: tools.slice(-12),
                      summary: cur.summary,
                      updatedAt: Date.now(),
                    },
                  };
                });
                (slot as { activityText?: string }).activityText =
                  anyEv?.type === "tool_end"
                    ? `${toolName} 已返回${toolFailed ? "（失败）" : ""}`
                    : `正在使用 ${toolName}`;
                paint();
                if (view.kind === "group") {
                  noteAgentActivity(
                    slot.agent,
                    "tool",
                    anyEv?.type === "tool_end"
                      ? `${toolName} 已返回${toolFailed ? "（失败）" : ""}`
                      : `正在使用 ${toolName}${detail ? `：${detail}` : ""}`,
                  );
                  const sharedFile = sharedFileFromToolEvent(anyEv);
                  if (sharedFile) commitSharedFileMessage(slot, sharedFile);
                  paint();
                }
              }
              // Social browser reveal MUST sit outside the tool_* branch.
              // browser_open is a separate SSE type; nesting it under tool_start/end
              // made the handler dead code, so the modal never opened from backend
              // browser_open and the Electron BrowserView stayed at 1x1 / hidden.
              {
                const revealSocialBrowser = (pageUrlRaw: string, opts?: { navigate?: boolean }) => {
                  const focused = key === threadKeyRef.current;
                  if (!focused || browserUserClosedRef.current) return;
                  const detailStr = String(pageUrlRaw || "");
                  const argsUrl = String(
                    (anyEv as any)?.data?.args?.url ||
                      (anyEv as any)?.data?.url ||
                      "",
                  );
                  const urlHit =
                    detailStr.match(/https?:\/\/[^\s"'<>]+/i)?.[0] ||
                    detailStr.match(/file:\/\/[^\s"'<>]+/i)?.[0] ||
                    argsUrl.match(/https?:\/\/[^\s"'<>]+/i)?.[0] ||
                    argsUrl.match(/file:\/\/[^\s"'<>]+/i)?.[0] ||
                    (detailStr.startsWith("http") || detailStr.startsWith("file:")
                      ? detailStr.trim()
                      : "") ||
                    (argsUrl.startsWith("http") || argsUrl.startsWith("file:")
                      ? argsUrl.trim()
                      : "");
                  // Only a fresh http(s)/file URL may drive loadURL. Re-emitting
                  // browser_open after observe/click with an empty url used to
                  // re-navigate to the stale homepage stored on the slot —
                  // that is the session-mode "jump back home" loop.
                  const freshUrl = (urlHit || "").trim();
                  const navigate = opts?.navigate !== false && Boolean(freshUrl) && freshUrl !== "about:blank";
                  if (freshUrl && freshUrl !== "about:blank") {
                    slot.browserUrl = freshUrl;
                  } else if (!slot.browserUrl) {
                    slot.browserUrl = "about:blank";
                  }
                  slot.browserBusy = true;
                  setBrowserCard({
                    url: slot.browserUrl || "",
                    threadKey: String(key),
                    agent: {
                      name: slot.agent?.name || "",
                      color: slot.agent?.color || "#64748b",
                      avatar: slot.agent?.avatar,
                    },
                  });
                  // Session mode: show BrowserPanel in the left share pane
                  // (same placement pattern as workbench ActivitySidebar).
                  openSocialBrowserSidebar();
                  // A real navigation must load the in-app browser. Leaving this
                  // as a no-op kept the guest on about:blank, so the preview
                  // stayed black even after the dev server was up.
                  // Reveal-only events (click, console) must not loadURL again.
                  if (navigate && freshUrl) onOpenBrowserUrl?.(freshUrl);
                  paint();
                };
                if (anyEv?.type === "browser_open") {
                  const openUrl = String(anyEv?.data?.url || "").trim();
                  const revealOnly = Boolean(anyEv?.data?.reveal_only) || !openUrl;
                  revealSocialBrowser(openUrl, { navigate: !revealOnly });
                }
                if (anyEv?.type === "tool_start") {
                  const tname = String(
                    anyEv?.data?.name ||
                      anyEv?.data?.tool ||
                      anyEv?.data?.tool_name ||
                      "",
                  ).toLowerCase();
                  if (tname.includes("wait_for_user") || tname.includes("needs_human")) {
                    browserHoldRef.current = true;
                  }
                  if (tname.includes("browser")) {
                    const detail = String(
                      anyEv?.data?.args?.url ||
                        anyEv?.data?.detail ||
                        anyEv?.data?.summary ||
                        anyEv?.data?.args_preview ||
                        "",
                    );
                    // tool_start for non-navigate browser_* must not reload.
                    const isNav = tname.includes("navigate") || tname.includes("goto") || tname.includes("open");
                    revealSocialBrowser(detail, { navigate: isNav });
                  }
                }
              }

              if (anyEv?.type === "assistant_delta") {
                const piece = String(
                  anyEv?.data?.chunk ?? anyEv?.data?.text ?? anyEv?.data?.delta ?? "",
                );
                const reset = Boolean(anyEv?.data?.reset);
                const discard = Boolean(anyEv?.data?.discard);
                if (reset) {
                  // Keep lastGoodBuf so an empty final after tool-discard can
                  // still fall back if the model never re-emits visible text.
                  if (buf.trim()) lastGoodBuf = buf;
                  // A new model round used to wipe the live bubble, or glue the
                  // next reply onto it. Keep the sentence that is already visible,
                  // including when a tool asks the user to confirm.
                  if (!discard || buf.trim() || (slot.reasoning || "").trim() || (slot.text || "").trim()) {
                    sealVisibleRound();
                  }
                  buf = "";
                }
                if (!piece) {
                  if (reset && !discard) {
                    if (!slotOptional || slot.replyConfirmed) {
                      slot.text = "";
                      paint();
                    }
                  }
                  return;
                }
                buf += piece;
                if (buf.trim()) lastGoodBuf = buf;
                const decisionEarly = buf.match(
                  /(^|\n)\s*DECISION\s*[:：]\s*(reply|silent|speak|pass|no_reply|noreply|回复|发言|说话|沉默|不回复|跳过)(?=\s|$|[，。！？,.!?\n])/i,
                );
                const decKind = decisionEarly ? String(decisionEarly[2] || "").toLowerCase() : "";
                const silentKinds = ["silent", "pass", "no_reply", "noreply", "沉默", "不回复", "跳过"];
                const replyKinds = ["reply", "speak", "回复", "发言", "说话"];
                // Speak/silent is decided by a hidden judge call before this
                // stream. Do not wait for a DECISION line inside the reply.
                if (slotOptional && (slot as { _awaitDecision?: boolean })._awaitDecision) {
                  if (decKind && silentKinds.includes(decKind)) {
                    slot.silent = true;
                    slot.replyConfirmed = false;
                    slot.text = "";
                    slot.reasoning = "";
                    paint();
                    return;
                  }
                  // Hold visible output until DECISION:reply — buf stays private.
                  if (!replyKinds.includes(decKind)) {
                    return;
                  }
                  slot.replyConfirmed = true;
                  slot.silent = false;
                  const preview = cleanDisplayText(
                    extractOwnReply(buf, slot.agent.name) || stripAgentTags(buf),
                  );
                  slot.text = preview;
                  if (!preview.trim() && !(slot.reasoning || "").trim()) return;
                  paint();
                  return;
                }
                slot.silent = false;
                slot.replyConfirmed = true;
                slot.text = cleanDisplayText(
                  extractOwnReply(buf, slot.agent.name) || stripAgentTags(buf),
                );
                paint();
              }
              if (anyEv?.type === "assistant_reasoning_delta") {
                const piece = String(anyEv?.data?.chunk ?? anyEv?.data?.text ?? "");
                const reset = Boolean(anyEv?.data?.reset);
                if (reset) {
                  slot.reasoning = "";
                  slot.reasoningStreaming = false;
                  if (slot.replyConfirmed || slot.streaming) paint();
                  return;
                }
                if (piece) slot.reasoning = absorbReasoningStream(slot.reasoning || "", piece);
                slot.reasoningStreaming = true;
                if (slotOptional && !slot.replyConfirmed && !slot.streaming) return;
                if (slot.silent) return;
                if ((slot.reasoning || "").trim() || (slot.text || "").trim()) paint();
              }
              if (anyEv?.type === "context_usage" || anyEv?.type === "llm_start") {
                const budget = (anyEv?.data?.budget || {}) as Record<string, unknown>;
                const tokens = Number(anyEv?.data?.tokens ?? budget.tokens_est ?? 0);
                const limit = Number(anyEv?.data?.limit ?? 0);
                const windowLimit = Number(anyEv?.data?.window_limit ?? budget.window_limit ?? 0);
                const next = {
                  tokens: Number.isFinite(tokens) ? tokens : 0,
                  limit: Number.isFinite(limit) && limit > 0 ? limit : 48000,
                  windowLimit: Number.isFinite(windowLimit) && windowLimit > 0 ? windowLimit : undefined,
                };
                                if (!isStaleSend()) {
                  setCtx(next);
                  setCtxByAgent((prev) => ({
                    ...prev,
                    [slot.agent.id]: next,
                  }));
                }

              }
            },
            onFinal: (textOut, meta) => {
              const metaReason = String((meta as { reasoning?: string } | undefined)?.reasoning || "").trim();
              if (metaReason && !(slot.reasoning || "").trim()) slot.reasoning = metaReason;
              // Empty synthetic final after SSE error must not clobber the error bubble.
              if (slot.stage === "error" && slot.decided) {
                slot.streaming = false;
                paint();
                return;
              }
              slot.reasoningStreaming = false;
              slot.stage = "done";
              setAgentLive((prev) => ({
                ...prev,
                [slot.agent.id]: {
                  ...prev[slot.agent.id],
                  phase: "done",
                  tools: prev[slot.agent.id]?.tools || [],
                  summary: "已完成",
                  updatedAt: Date.now(),
                },
              }));
              // Match workbench: prefer incoming final, else streamed buf, else
              // last good paint — never drop a real reply into socialEmptyReply
              // when deltas already delivered text but final.text was empty.
              const painted = cleanDisplayText(String(slot.text || "")).trim();
              const emptyPlaceholder = t("socialEmptyReply");
              const paintedKeep =
                painted && painted !== emptyPlaceholder ? painted : "";
              const rawFinal = (
                (textOut && String(textOut).trim()) ||
                buf ||
                lastGoodBuf ||
                paintedKeep ||
                ""
              ).trim();
              if (groupId) {
                const standing = parseStandingRules(rawFinal);
                if (standing.rules.length || standing.drops.length) {
                  patchStore((prev) => {
                    const g = prev.groups.find((x) => x.id === groupId);
                    if (!g) return prev;
                    g.announcements = applyStandingRules(
                      g.announcements || [],
                      standing.rules,
                      standing.drops,
                      { kind: "agent", id: slot.agent.id, name: slot.agent.name },
                    );
                    return prev;
                  });
                }
              }
              (slot as any)._asks = coordinationAsksInText(rawFinal);
              if (!slotOptional) {
                const full = cleanDisplayText(
                  extractOwnReply(rawFinal, slot.agent.name) || stripAgentTags(rawFinal),
                );
                slot.text = full || paintedKeep || emptyPlaceholder;
                slot.streaming = false;
                slot.silent = false;
                slot.replyConfirmed = true;
                slot.decided = true;
                patchStore((prev) =>
                  appendAgentMemory(
                    prev,
                    slot.agent.id,
                    `用户: ${(raw || text).slice(0, 100)} → 我: ${(slot.text || "").slice(0, 120)}`,
                    groupId
                    ? `group:${groupId}`
                    : dmMemoryContext(slot.agent.id, getDmActiveSession(store, slot.agent.id)),
                  ),
                );
                paint();
                return;
              }
              const assignedTasksBeforeResponse = (((slot as any)._tasks as string[]) || []).filter(Boolean);
              const decision = parseAgentDecision(rawFinal);
              const cleared = Boolean((slot as { _clearedToSpeak?: boolean })._clearedToSpeak);
              if (cleared) {
                const kept =
                  visibleReplyAfterJudge(rawFinal) || visibleReplyAfterJudge(String(lastGoodBuf || ""));
                slot.text = kept;
                slot.reasoningStreaming = false;
                slot.streaming = false;
                slot.silent = !kept;
                slot.replyConfirmed = Boolean(kept);
                slot.decided = true;
              } else if (decision.action === "silent") {
                slot.text = "";
                slot.reasoning = "";
                slot.reasoningStreaming = false;
                slot.streaming = false;
                slot.silent = true;
                slot.replyConfirmed = false;
                slot.decided = true;
              } else {
                const body =
                  cleanDisplayText(
                    extractOwnReply(decision.text || rawFinal, slot.agent.name) ||
                      stripAgentTags(decision.text || rawFinal) ||
                      "",
                  ) || paintedKeep || emptyPlaceholder;
                slot.text = body;
                slot.streaming = false;
                slot.silent = false;
                slot.replyConfirmed = true;
                slot.decided = true;
              }
              const goalSatisfied = (slot as { _goalSatisfied?: boolean })._goalSatisfied === true;
              const mergedTasks =
                wave === 2
                  ? mergeExecutionTasks(assignedTasksBeforeResponse, decision.tasks)
                  : decision.tasks;
              const doneTasks =
                wave === 2
                  ? doneTasksForExecution(assignedTasksBeforeResponse, decision.doneTasks, goalSatisfied)
                  : decision.doneTasks;
              const outcome = { ...decision, tasks: mergedTasks, doneTasks };
              (slot as any)._facts = decision.facts;
              (slot as any)._tasks = mergedTasks;
              (slot as any)._doneTasks = doneTasks;
              (slot as any)._forgetFacts = decision.forgetFacts;
              if (view.kind === "group") {
                const asks = ((slot as any)._asks as Array<{ targetName: string; question: string }>) || [];
                const tasks = (decision.tasks || []).filter(Boolean);
                if (wave === 1 && asks.length) {
                  for (const ask of asks) {
                    noteAgentActivity(slot.agent, "ask", `请 @${ask.targetName} 补充：${ask.question}`);
                  }
                }
                if (wave === 1 && tasks.length) {
                  noteAgentActivity(slot.agent, "claim", `认领：${tasks.join("；")}`);
                } else if (wave === 1 && slot.silent) {
                  noteAgentActivity(slot.agent, "skip", "判断后不参与：无新增处理事项或已有更合适负责人");
                } else if (wave === 2) {
                  noteAgentActivity(
                    slot.agent,
                    goalSatisfied || slot.silent ? "done" : "execute",
                    slot.silent
                      ? "执行后无需补充"
                      : goalSatisfied
                        ? "已完成本次处理并提交结果"
                        : "尚未达到目标，继续处理",
                  );
                }
              }
              // Dynamic board write: each finished agent updates shared memory immediately.
              // Assigned work stays open until goal_settled says the user goal is met.
              if (groupId && liveBoard && optionalReply) {
                liveBoard = applyAgentOutcome(liveBoard, slot.agent, outcome, { autoExtract: true, writeFacts: false });
                liveBoard = bumpWorkingEntry(liveBoard, {
                  agentId: slot.agent.id,
                  agentName: slot.agent.name,
                  action: slot.silent ? "silent" : "reply",
                  summary: slot.silent ? "" : summarizeReply(slot.text),
                  at: Date.now(),
                });
                patchStore((prev) => setGroupBoard(prev, groupId, liveBoard!));
              }
              
              // Private per-agent memory (what I saw / said this turn)
              patchStore((prev) =>
                appendAgentMemory(
                  prev,
                  slot.agent.id,
                  slot.silent
                    ? `群消息（未发言）: ${(raw || text).slice(0, 120)}`
                    : `用户: ${(raw || text).slice(0, 100)} → 我: ${(slot.text || "").slice(0, 120)}`,
                  groupId
                    ? `group:${groupId}`
                    : dmMemoryContext(slot.agent.id, getDmActiveSession(store, slot.agent.id)),
                ),
              );

              paint();
            },
            onError: (err) => {
              const msg = String(err || "error");
              onToast(msg);
              slot.stage = "error";
              setAgentLive((prev) => ({
                ...prev,
                [slot.agent.id]: {
                  ...prev[slot.agent.id],
                  phase: "error",
                  tools: prev[slot.agent.id]?.tools || [],
                  summary: msg.slice(0, 240),
                  updatedAt: Date.now(),
                },
              }));
              slot.text = t("socialSendFail") + ": " + msg;
              slot.streaming = false;
              slot.silent = false;
              slot.replyConfirmed = true;
              slot.decided = true;
              paint();
            },
            onAbort: () => {
              slot.streaming = false;
              slot.stage = "done";
              if (turnPaused) {
                slot.silent = true;
                slot.text = "";
                slot.decided = true;
                slot.replyConfirmed = false;
                paint();
                return;
              }
              if (slotOptional && !slot.replyConfirmed) {
                slot.silent = true;
                slot.text = "";
                slot.reasoning = "";
              } else if (!cleanDisplayText(slot.text || "")) {
                slot.text = "…";
                slot.replyConfirmed = true;
                slot.silent = false;
              }
              slot.decided = true;
              paint();
            },
          },
          ac.signal,
          "agent",
          text,
          chatWorkspacePath,
          // Judge / analyze / classify / polish call streamChat with
          // disableTools=true on their own. Member replies (freeTurn) get the
          // same mutating tool grounds as workbench: write_file / delete_file /
          // run_shell / str_replace, subject to the same backend approval path.
          // Legacy wave-1 discuss + synthesis stay observation-only.
          freeTurn
            ? !freeTurn.tools
            : Boolean(view.kind === "group" && optionalReply && (wave === 1 || opts?.synthesis)),
          // One-shot social streams force a throwaway backend session — hide
          // it from the workbench history. The linked lead DM shares the real
          // workbench session instead, so it stays visible.
          !linkedDm,
          freeTurn
            ? true
            : Boolean(harnessPlan && harnessPlan.phase === "work" && wave === 2 && !opts?.synthesis),
        );
      } catch (e: any) {
        if (ac.signal.aborted) return;
        slot.text = t("socialSendFail") + ": " + String(e?.message || e);
        slot.streaming = false;
        slot.decided = true;
        paint();
      }
    };

    const commitBoardFromSlots = (waveSlots: Slot[]) => {
      if (!groupId || !liveBoard) return;
      let board = liveBoard;
      for (const s of waveSlots) {
        const facts = ((s as any)._facts as string[]) || [];
        const tasks = ((s as any)._tasks as string[]) || [];
        const doneTasks = ((s as any)._doneTasks as string[]) || [];
        const forgetFacts = ((s as any)._forgetFacts as string[]) || [];
        // Tasks update live; FACTS wait for majority consensus below.
        board = applyAgentOutcome(
          board,
          s.agent,
          {
            action: s.silent ? "silent" : "reply",
            text: s.text || "",
            facts: [],
            tasks,
            doneTasks,
            forgetFacts: [],
          },
          { autoExtract: false, writeFacts: false },
        );
        board = bumpWorkingEntry(board, {
          agentId: s.agent.id,
          agentName: s.agent.name,
          action: s.silent ? "silent" : "reply",
          summary: s.silent ? "" : summarizeReply(stripAgentTags(s.text)),
          at: Date.now(),
        });
      }
      const proposals = waveSlots.map((s) => {
        const explicit = ((s as any)._facts as string[]) || [];
        const extracted =
          !s.silent && explicit.length === 0 ? autoExtractFacts(s.text || "") : [];
        return { agent: s.agent, facts: [...explicit, ...extracted] };
      });
      // Majority of this turn's invited agents must agree before durable write.
      board = commitConsensusFacts(board, proposals, Math.max(waveSlots.length, targets.length));

      board = commitRound(board, raw || text);
      liveBoard = board;
      patchStore((prev) => setGroupBoard(prev, groupId, board));
    };

    /**
     * Free-form group chat: every new message (user or member) → every other
     * member decides reply / silent with the full history → one reply at a
     * time → repeat until a message gets no volunteer, a cap, or an abort.
     */
    const runFreeGroupLoop = async () => {
      const locale = loadLocale() === "en" ? "en" : "zh";
      // Pipeline is still model-assigned. Tools for member replies match workbench
      // (write/delete/shell): do not gate on analyzer action/pipeline — a missed
      // reading must not leave the agent unable to edit or run commands.
      let pipeline: ExplicitPipeline | null = null;
      let loopTools = true;
      const priorMsgs = hist.filter(
        (m) => (m.role === "user" || m.role === "agent") && !m.readingStatus && String(m.text || "").trim(),
      );
      const toLine = (m: SocialChatMsg): ChatLine => ({
        id: m.id,
        role: m.role === "user" ? "user" : "agent",
        agentId: m.agentId,
        agentName: m.agentName,
        text: m.id === userMsg.id ? text : String(m.text || ""),
      });
      const toMsg = (line: ChatLine): SocialChatMsg => {
        const known = priorMsgs.find((m) => m.id === line.id);
        return {
          id: line.id,
          role: line.role,
          agentId: line.agentId,
          agentName: line.agentName,
          text: line.text,
          attachments: known && known.id !== userMsg.id ? known.attachments : undefined,
          at: known?.at || Date.now(),
        };
      };
      const slotOf = (agentId: string) => slots.find((s) => s.agent.id === agentId);
      const authorOf = (line: ChatLine) => (line.role === "user" ? "你" : line.agentName || "成员");
      const reasonText: Record<string, string> = {
        addressed: "被你点名，直接回复",
        everyone: "你请每位成员都回应",
        judge: "判断后决定回复",
        judge_failed: "判断失败",
        fallback: "无人主动回复，由最合适的成员回答",
        pipeline: "按你要求的流水线顺序发言",
        cap: "本轮发言次数已到上限",
      };
      const onLoopEvent = (event: LoopEvent) => {
        if (isStaleSend()) return;
        if (event.type === "intent") {
          if (event.intent.source === "fallback" && event.intent.fallbackReason !== "no_analyzer") {
            // Never silent: the model could not read the message, so only @/name
            // syntax is used and no pipeline is assumed.
            console.warn("[group-loop] turn analyzer unavailable:", event.intent.fallbackReason);
            freeStatus = "没能理解消息的指向，按 @ 点名 / 自由判断处理…";
          } else {
            freeStatus = "已理解这条消息，正在请成员判断…";
          }
          // Keep full workbench tool grounds for replies even when the analyzer
          // marks the turn as talk-only. Judge/intent calls remain tool-free.
          loopTools = true;
          if (event.intent.pipeline) {
            pipeline = event.intent.pipeline;
            harnessNotices.push({
              id: uid("sys"),
              role: "system",
              text: formatPipelineNotice(pipeline, rosterForPrompt, locale),
              at: Date.now(),
              collaboration: true,
            });
          }
          paint();
        } else if (event.type === "judging") {
          for (const id of event.agentIds) {
            const s = slotOf(id);
            if (s) s.stage = "deciding";
          }
          freeStatus = event.agentIds.length
            ? `${event.agentIds.length} 位成员正在各自判断要不要回复${authorOf(event.trigger)}的新消息…`
            : freeStatus;
          setAgentLive((prev) => {
            const next = { ...prev };
            for (const id of event.agentIds) {
              next[id] = {
                ...(next[id] || { tools: [] }),
                phase: "deciding",
                tools: next[id]?.tools || [],
                summary: `正在判断要不要回复${authorOf(event.trigger)}的消息…`,
                updatedAt: Date.now(),
              };
            }
            return next;
          });
          paint();
        } else if (event.type === "decisions") {
          for (const d of event.decisions) {
            const s = slotOf(d.agentId);
            if (s && !d.reply) s.stage = "done";
            const agent = rosterForPrompt.find((a) => a.id === d.agentId);
            if (agent) {
              noteAgentActivity(
                agent,
                d.reply ? "decide" : "skip",
                d.reply
                  ? `${reasonText[d.reason] || "决定回复"}（${authorOf(event.trigger)}的消息）`
                  : `看过${authorOf(event.trigger)}的消息后决定不回复${d.reason === "cap" ? "（发言次数已到上限）" : ""}`,
              );
            }
          }
          setAgentLive((prev) => {
            const next = { ...prev };
            for (const d of event.decisions) {
              if (d.reply) continue;
              next[d.agentId] = {
                ...(next[d.agentId] || { tools: [] }),
                phase: "done",
                tools: next[d.agentId]?.tools || [],
                summary: "决定不回复",
                updatedAt: Date.now(),
              };
            }
            return next;
          });
          const yes = event.decisions.filter((d) => d.reply).map((d) => d.agentName);
          freeStatus = yes.length ? `${yes.join("、")} 想回复…` : "";
          paint();
        }
        try {
          console.debug("[group-loop]", event.type, event);
        } catch {}
      };
      const judge = async (input: JudgeInput, signal?: AbortSignal): Promise<boolean | null> => {
        let verdict = "";
        let failed = false;
        try {
          await streamChat(
            buildReplyDecisionPrompt({ ...input, context: currentWorkSite() }),
            `social-judge-${groupId || "g"}-${input.agent.id}-${uid("j")}`,
            {
              onEvent: () => {},
              onFinal: (out) => {
                verdict = String(out || "");
              },
              onError: () => {
                failed = true;
              },
            },
            signal,
            "agent",
            "",
            chatWorkspacePath,
            true,
            true,
            true,
          );
        } catch {
          failed = true;
        }
        if (signal?.aborted || isStaleSend()) return null;
        if (failed && !verdict.trim()) return null;
        return parseReplyDecision(verdict);
      };
      /** One hidden, tool-less model call that reads the user's message (audience / lead / explicit order). */
      const analyzeTurn = async (prompt: string, _input: unknown, signal?: AbortSignal): Promise<string | null> => {
        let out = "";
        freeStatus = "正在理解你的消息…";
        paint();
        const local = new AbortController();
        const timer = window.setTimeout(() => local.abort(), 20000);
        const onParent = () => local.abort();
        signal?.addEventListener("abort", onParent);
        try {
          await streamChat(
            prompt,
            `social-intent-${groupId || "g"}-${uid("i")}`,
            {
              onEvent: () => {},
              onFinal: (final) => {
                out = String(final || "");
              },
              onError: () => {},
            },
            local.signal,
            "agent",
            "",
            chatWorkspacePath,
            true,
            true,
            true,
            true,
          );
        } catch {
          return null;
        } finally {
          window.clearTimeout(timer);
          signal?.removeEventListener("abort", onParent);
        }
        if (signal?.aborted || isStaleSend()) return null;
        return out.trim() || null;
      };
      const reply = async (input: ReplyInput): Promise<string[]> => {
        const s = slotOf(input.agent.id);
        if (!s || ac.signal.aborted || turnPaused) return [];
        const latest = input.trigger;
        const stepIndex = input.step && pipeline ? pipeline.steps.indexOf(input.step) : -1;
        const nextStep = stepIndex >= 0 && pipeline ? pipeline.steps[stepIndex + 1] : undefined;
        s.id = uid("m");
        s.text = "";
        s.reasoning = "";
        s.silent = false;
        s.decided = false;
        s.replyConfirmed = true;
        s.streaming = true;
        s.stage = "executing";
        (s as { activityText?: string }).activityText = "";
        (s as { _clearedToSpeak?: boolean })._clearedToSpeak = true;
        (s as { _sentenceOnly?: boolean })._sentenceOnly = false;
        (s as any)._tasks = input.step ? [input.step.task] : [];
        freeReply = {
          history: input.history.slice(0, -1).map(toMsg),
          latest: {
            author: latest.role === "user" ? "用户" : latest.agentName || "成员",
            isUser: latest.role === "user",
            text: latest.text,
          },
          addressed: input.addressed,
          brief: input.brief,
          everyone: input.everyone,
          mustAnswer: input.mustAnswer,
          action: input.action,
          step: input.step && pipeline
            ? {
                index: stepIndex + 1,
                total: pipeline.steps.length,
                task: input.step.task,
                nextName: nextStep ? rosterForPrompt.find((a) => a.id === nextStep.agentId)?.name : undefined,
              }
            : undefined,
          tools: loopTools,
        };
        setAgentLive((prev) => ({
          ...prev,
          [s.agent.id]: {
            ...prev[s.agent.id],
            phase: "executing",
            tools: prev[s.agent.id]?.tools || [],
            summary: "正在回复…",
            updatedAt: Date.now(),
          },
        }));
        paint();
        const before = committedMsgs.length;
        try {
          await runOne(s, 1);
        } finally {
          freeReply = null;
        }
        if (ac.signal.aborted || isStaleSend()) return [];
        const texts = committedMsgs
          .slice(before)
          .filter((m) => m.agentId === s.agent.id)
          .map((m) => String(m.text || "").trim())
          .filter(Boolean);
        const finalText = visibleReplyAfterJudge(cleanDisplayText(String(s.text || "")));
        if (!s.silent && finalText && finalText !== t("socialEmptyReply") && commitGroupMessage(s, finalText)) {
          texts.push(finalText);
        }
        s.text = "";
        s.reasoning = "";
        s.silent = true;
        s.decided = true;
        s.streaming = false;
        s.reasoningStreaming = false;
        s.replyConfirmed = false;
        s.stage = "done";
        s.browserUrl = undefined;
        s.browserBusy = undefined;
        paint();
        return texts;
      };
      paint();
      const result = await runGroupConversation({
        roster: rosterForPrompt,
        history: priorMsgs.map(toLine),
        judge,
        reply,
        signal: ac.signal,
        maxAgentMessages: defaultMaxAgentMessages(rosterForPrompt.length),
        maxRepliesPerAgent: 2,
        analyzeTurn,
        onEvent: onLoopEvent,
        shouldStop: (line) =>
          line.role === "agent" && pauseForUserInput(line.agentName || "", confirmationText(String(line.agentId || ""), line.text)),
        newId: () => uid("m"),
      });
      if (isStaleSend()) return;
      freeStatus = "";
      for (const s of slots) {
        s.streaming = false;
        s.reasoningStreaming = false;
        if (s.stage !== "error") s.stage = "done";
        if (!s.decided) {
          s.decided = true;
          s.silent = !String(s.text || "").trim();
        }
      }
      setAgentLive((prev) => {
        const next = { ...prev };
        for (const s of slots) {
          const cur = next[s.agent.id];
          if (!cur || !isActiveAgentPhase(cur.phase)) continue;
          next[s.agent.id] = { ...cur, phase: "done", updatedAt: Date.now() };
        }
        return next;
      });
      if (groupId && liveBoard) {
        liveBoard = commitRound(liveBoard, raw || text);
        const board = liveBoard;
        patchStore((prev) => setGroupBoard(prev, groupId, board));
      }
      if (result.reason === "cap" && !turnPaused) {
        harnessNotices.push({
          id: uid("sys"),
          role: "system",
          text: `本轮成员已发言 ${result.messages.length} 条，达到上限后自动停止。`,
          at: Date.now(),
          collaboration: true,
        });
      }
      paint();
    };

    const currentHarnessDiagnosis = () => {
      if (!harnessPlan) return null;
      return diagnoseHarness({
        plan: harnessPlan,
        openTasks: (liveBoard?.tasks || []).filter((task) => task.status === "open").length,
        agentResults: slots.map((s) => ({
          agentId: s.agent.id,
          role: harnessPlan!.assignments.find((item) => item.agentId === s.agent.id)?.role || "other",
          texts: [
            ...committedMsgs.filter((m) => m.agentId === s.agent.id).map((m) => String(m.text || "")),
            cleanDisplayText(String(s.text || "")),
          ].map((item) => item.trim()).filter(Boolean),
          goalSatisfied: (s as { _goalKnown?: boolean })._goalKnown
            ? (s as { _goalSatisfied?: boolean })._goalSatisfied === true
            : null,
          goalBlocked: Boolean(
            (s as { _goalKnown?: boolean })._goalKnown && (s as { _goalBlocked?: boolean })._goalBlocked,
          ),
          toolErrors: Number((s as { _toolErrors?: number })._toolErrors || 0),
        })),
      });
    };

    try {
      if (freeGroupMode) {
        await runFreeGroupLoop();
      } else {
      // Wave 1 DISCUSS (open floor): tools off — agents talk / hand off / claim TASKs.
      // Multi-round sequential discuss: first full pass, then follow-ups for
      // @确认 / nominees until someone accepts or we hit the round cap.
      peerDiscuss = [];
      const discussRoster = rosterForPrompt.map((a) => ({ id: a.id, name: a.name }));
      const judgeWhetherToSpeak = async (s: (typeof slots)[number]) => {
        const prior = peerDiscuss
          .map((row) => ({ name: row.agentName, text: cleanDisplayText(row.text) }))
          .filter((row) => row.text);
        const history = messages
          .filter((m) => !m.readingStatus && (m.role === "user" || m.role === "agent"))
          .map((m) => ({
            role: m.role,
            name: m.role === "user" ? "用户" : m.agentName || "成员",
            text: cleanDisplayText(m.text || "").replace(/\s+/g, " ").trim().slice(0, 180),
          }))
          .filter((m) => m.text)
          .slice(-24);
        const greeting = collectiveGreeting || isLightweightSocialTurn(raw || text);
        const capability = isCapabilityQuestion(raw || text);
        const addressed = mentionedIds.has(s.agent.id) || Boolean(turn?.everyone);
        let verdict = "";
        let failed = false;
        noteAgentActivity(s.agent, "decide", "已看到本轮消息，正在判断要不要发言");
        try {
          await streamChat(
            buildSpeakJudgePrompt({
              name: s.agent.name,
              duty: s.agent.duty || s.agent.blurb,
              userText: raw || text,
              prior,
              history,
              addressed,
              greeting,
              capability,
              context: currentWorkSite(),
            }),
            `social-judge-${groupId || "g"}-${s.agent.id}-${s.id}`,
            {
              onEvent: () => {},
              onFinal: (out) => {
                verdict = String(out || "");
              },
              onError: () => {
                failed = true;
              },
            },
            ac.signal,
            "agent",
            "",
            chatWorkspacePath,
            true,
            true,
            true,
          );
        } catch {
          failed = true;
        }
        if (ac.signal.aborted || isStaleSend()) return false;
        if (failed && !verdict.trim()) return prior.length === 0;
        return parseSpeakVerdict(verdict, prior.length === 0);
      };
      const runDiscussTurn = async (s: (typeof slots)[number], execute = false): Promise<string> => {
        // Fresh bubble id so follow-up turns don't collide with flushed ones.
        s.id = uid("m");
        s.text = "";
        s.reasoning = "";
        s.streaming = false;
        s.stage = "deciding";
        s.silent = false;
        s.decided = false;
        s.replyConfirmed = false;
        s.reasoningStreaming = false;
        paint();
        const forceSpeak = Boolean((s as { _forceSpeak?: boolean })._forceSpeak);
        const speak = forceSpeak || (await judgeWhetherToSpeak(s));
        if (!speak) {
          s.silent = true;
          s.decided = true;
          s.stage = "done";
          s.streaming = false;
          s.text = "";
          s.reasoning = "";
          setAgentLive((prev) => ({
            ...prev,
            [s.agent.id]: {
              ...prev[s.agent.id],
              phase: "done",
              tools: prev[s.agent.id]?.tools || [],
              summary: "决定不回复",
              updatedAt: Date.now(),
            },
          }));
          const seen = peerDiscuss.slice(-6);
          noteAgentActivity(
            s.agent,
            "skip",
            seen.length
              ? `看过最近${seen.length}条发言（${seen.map((row) => row.agentName).join("、")}）后决定不回复`
              : "看过这条消息后决定不回复",
          );
          paint();
          return "";
        }
        (s as { _clearedToSpeak?: boolean })._clearedToSpeak = true;
        s.replyConfirmed = true;
        s.silent = false;
        s.streaming = true;
        s.stage = execute ? "executing" : "deciding";
        paint();
        await runOne(s, execute ? 2 : 1);
        if (turnPaused || ac.signal.aborted) return confirmationText(s.agent.id, cleanDisplayText(String(s.text || "")));
        if ((s as { _clearedToSpeak?: boolean })._clearedToSpeak && !visibleReplyAfterJudge(String(s.text || ""))) {
          (s as { _sentenceOnly?: boolean })._sentenceOnly = true;
          s.silent = false;
          s.text = "";
          s.reasoning = "";
          s.decided = false;
          s.replyConfirmed = false;
          s.streaming = true;
          s.stage = "executing";
          await runOne(s, 1);
          (s as { _sentenceOnly?: boolean })._sentenceOnly = false;
        }
        const raw = cleanDisplayText(String(s.text || "")).trim();
        const asks = ((s as any)._asks as Array<{ targetName: string; question: string }>) || [];
        const claimedTasks = (((s as any)._tasks as string[]) || []).filter(Boolean);
        let publicText = formatGroupDiscussionMessage(raw, asks, claimedTasks);
        if (forceSpeak && !publicText) {
          publicText = greetingFallback(s.agent.name);
          s.text = publicText;
          s.silent = false;
          s.decided = true;
          s.replyConfirmed = true;
        }
        if (!s.silent && publicText) {
          const prior = [
            ...committedMsgs.map((m) => ({ agentId: m.agentId, text: m.text })),
            ...peerDiscuss.map((r) => ({ agentId: r.agentId, text: r.text })),
          ];
          if (isNearDuplicateBubble(s.agent.id, publicText, prior)) {
            // Paraphrased re-claim: treat as silent so discuss does not loop on the same speech.
            s.silent = true;
            s.text = "";
            s.decided = true;
            s.replyConfirmed = false;
            paint();
            return agentAsksForUserInput(publicText) ? publicText : "";
          }
          const coordination = [
            ...asks.map((ask) => `ASK: @${ask.targetName} | ${ask.question}`),
            ...claimedTasks.map((task) => `TASK: ${task}`),
          ];
          // Subsequent members receive targeted questions in their actual
          // coordination context, rather than merely seeing that someone
          // spoke. This creates an actionable ask → answer loop.
          peerDiscuss.push({
            agentId: s.agent.id,
            agentName: s.agent.name,
            text: [raw, ...coordination].filter(Boolean).join("\n"),
          });
          // A group is a shared conversation, not a hidden orchestration
          // console. Publish this member's real question/claim immediately
          // as a durable bubble, then let later members read that same turn.
          // Protocol lines remain private; formatGroupDiscussionMessage turns
          // ASK/TASK into natural group-chat language when needed.
          commitGroupMessage(s, publicText);
          s.text = "";
          s.reasoning = "";
          s.silent = true;
          s.decided = true;
          s.browserUrl = undefined;
          s.browserBusy = undefined;
          s.replyConfirmed = false;
          s.stage = "done";
          paint();
          return publicText;
        } else {
          s.streaming = false;
          s.silent = true;
          s.decided = true;
          s.stage = "done";
          return "";
        }
      };

      // The harness picks who works before anyone speaks. Open-floor discussion
      // stays for chat, questions, and groups that are not on a real task.
      harnessPlan =
        view.kind === "group" &&
        shouldUseHarness({
          text: raw || text,
          execution: executionRequested,
          collab: activeGroup?.collabMode !== false,
          lightweight: lightweightSocialTurn,
          greeting: collectiveGreeting,
          capability: isCapabilityQuestion(raw || text),
          directAnswer: directAnswerTurn,
        })
          ? planCollaboration(raw || text, rosterForPrompt, {
              playbook: store.harness,
              mentionedIds,
              collab: activeGroup?.collabMode !== false,
              topicText: isDiscussionRequest(raw || text)
                ? String([...messages].reverse().find((m) => m.role === "user")?.text || "")
                : "",
            })
          : null;
      const announceHarness = () => {
        if (!harnessPlan) return;
        harnessNotices.push({
          id: uid("sys"),
          role: "system",
          text: formatHarnessNotice(harnessPlan, rosterForPrompt, loadLocale()),
          at: Date.now(),
          collaboration: true,
        });
        paint();
      };
      const park = (s: (typeof slots)[number]) => {
        s.silent = true;
        s.decided = true;
        s.stage = "done";
      };
      let operatorId: string | undefined;
      let floorExecuted = false;
      if (harnessPlan?.phase === "work") {
        const jobs = new Map(
          harnessPlan.assignments
            .filter((item) => item.role === "owner" || item.role === "worker")
            .map((item) => [item.agentId, item.task] as const),
        );
        for (const s of slots) {
          const task = jobs.get(s.agent.id);
          if (!task) {
            park(s);
            (s as any)._tasks = [];
            continue;
          }
          (s as any)._tasks = [task];
          park(s);
        }
        if (harnessPlan.topology === "solo") {
          operatorId = harnessPlan.assignments.find((item) => item.role === "owner")?.agentId;
        }
        announceHarness();
      } else if (harnessPlan?.phase === "talk") {
        const speakerIds = new Set(harnessPlan.assignments.map((item) => item.agentId));
        for (const s of slots) {
          if (!speakerIds.has(s.agent.id)) park(s);
        }
        announceHarness();
        for (const assignment of harnessPlan.assignments) {
          if (ac.signal.aborted || turnPaused) break;
          const speaker = slots.find((s) => s.agent.id === assignment.agentId);
          if (!speaker) continue;
          const spoken = await runDiscussTurn(speaker);
          if (pauseForUserInput(speaker.agent.name, confirmationText(speaker.agent.id, spoken))) break;
        }
      }
      if (harnessPlan?.phase !== "talk" && (!harnessPlan || scheduleRequest || harnessPlan.topology === "solo")) {
      // Chat: after each new bubble, every other member judges again.
      // A work order stays with one operator and does not open that floor.
      const namedTurn = mentionedIds.size > 0 && !turn?.everyone && !multiVoice;
      let discussOrder = namedTurn
        ? slots.filter((s) => mentionedIds.has(s.agent.id))
        : [...slots];
      const capabilityTurn = isCapabilityQuestion(raw || text);
      if (!operatorId && executionRequested && !turn?.everyone && mentionedIds.size <= 1) {
        const byDuty = [...slots]
          .map((slot, index) => ({ slot, index, score: dutyScore(slot.agent, raw || text) }))
          .sort((a, b) => b.score - a.score || a.index - b.index)[0]?.slot.agent.id;
        const lastAuthor = isAuthorCallback(raw || text)
          ? [...(liveBoard?.tasks || [])].reverse().find((task) => task.status === "done" && task.ownerId)?.ownerId
          : "";
        const authorKnown = Boolean(lastAuthor && slots.some((slot) => slot.agent.id === lastAuthor));
        operatorId = mentionedIds.size === 1
          ? [...mentionedIds][0]
          : authorKnown
            ? String(lastAuthor)
            : byDuty;
      }
      const soloOwnerId = operatorId;
      if (reactionFloor && isGroupGreeting(raw || text)) {
        // 你好 / 大家好: every member answers once. Do not ask a judge that can silence the group.
        for (const s of slots) {
          if (ac.signal.aborted || turnPaused) break;
          (s as { _forceSpeak?: boolean })._forceSpeak = true;
          await runDiscussTurn(s, false);
          (s as { _forceSpeak?: boolean })._forceSpeak = false;
        }
        for (const s of slots) {
          s.streaming = false;
          s.stage = "done";
          if (!s.decided) {
            s.decided = true;
            s.silent = !String(s.text || "").trim();
          }
        }
        setAgentLive((prev) => {
          const next = { ...prev };
          for (const s of slots) {
            const cur = next[s.agent.id];
            if (!cur || !isActiveAgentPhase(cur.phase)) continue;
            next[s.agent.id] = {
              ...cur,
              phase: "done",
              summary: "已回复",
              updatedAt: Date.now(),
            };
          }
          return next;
        });
        paint();
      } else if (reactionFloor) {
        let latestAuthorId = "";
        const reactionCap = maxReactionTurns(slots.length);
        let reactions = 0;
        paint();
        while (!ac.signal.aborted && !turnPaused && reactions < reactionCap) {
          const order = planReactionOrder(
            slots.map((slot) => ({ id: slot.agent.id })),
            { latestAuthorId, addressedIds: mentionedIds },
          );
          let spoke = false;
          for (const id of order) {
            if (ac.signal.aborted || turnPaused) break;
            const speaker = slots.find((slot) => slot.agent.id === id);
            if (!speaker) continue;
            const spoken = await runDiscussTurn(speaker, false);
            if (pauseForUserInput(speaker.agent.name, confirmationText(speaker.agent.id, spoken))) break;
            if (!spoken) continue;
            latestAuthorId = speaker.agent.id;
            spoke = true;
            reactions += 1;
            break;
          }
          if (!spoke || turnPaused) break;
        }
      } else {
      if (!multiVoice && !turn?.everyone && operatorId && mentionedIds.size <= 1) {
        discussOrder = slots.filter((slot) => slot.agent.id === operatorId);
      } else if (!multiVoice && !turn?.everyone && !namedTurn && soloSpeakerId) {
        discussOrder = slots.filter((slot) => slot.agent.id === soloSpeakerId);
      } else if (operatorId) {
        discussOrder.sort((a, b) => Number(b.agent.id === operatorId) - Number(a.agent.id === operatorId));
      }
      const called = new Set(discussOrder.map((slot) => slot.agent.id));
      for (const s of slots) {
        if (called.has(s.agent.id)) continue;
        s.stage = "done";
        s.decided = true;
        s.silent = true;
        s.streaming = false;
      }
      paint();
      for (const s of discussOrder) {
        if (ac.signal.aborted) break;
        const asked = new Set(
          peerDiscuss.flatMap((r) => extractNominees(r.text, discussRoster, r.agentId)),
        );
        const focused =
          capabilityTurn ||
          Boolean(soloOwnerId) ||
          (executionRequested && mentionedIds.size > 0 && !turn?.everyone);
        if (focused) (s as { _peerHandoff?: boolean })._peerHandoff = asked.has(s.agent.id);
        const execute = Boolean(
          executionRequested && operatorId && s.agent.id === operatorId && peerDiscuss.length === 0,
        );
        const spoken = await runDiscussTurn(s, execute);
        if (execute && !s.silent) floorExecuted = true;
        if (focused) (s as { _peerHandoff?: boolean })._peerHandoff = false;
        if (pauseForUserInput(s.agent.name, confirmationText(s.agent.id, spoken))) break;
      }

            // A locked operation stays with one person. Do not open a second speaker.
      let lastPendingKey = "";
      let discussStall = 0;
      while (!reactionFloor && !namedTurn && !operatorId && !ac.signal.aborted && !turnPaused) {
        if (discussHasOwner(peerDiscuss, slots)) {
          const pendingEarly = findPendingDiscussIds(peerDiscuss, discussRoster);
          if (!pendingEarly.length) break;
        }
        const pendingIds = findPendingDiscussIds(peerDiscuss, discussRoster);
        if (!pendingIds.length) break;
        const pendingKey = pendingIds.slice().sort().join(",");
        const beforeLen = peerDiscuss.length;
        if (pendingKey === lastPendingKey) break; // same pending set → no progress
        lastPendingKey = pendingKey;
        for (const id of pendingIds) {
          if (ac.signal.aborted) break;
          let s = slots.find((x) => x.agent.id === id);
          if (!s) {
            const agent = rosterForPrompt.find((a) => a.id === id);
            if (!agent) continue;
            s = {
              id: uid("m"),
              agent,
              text: "",
              streaming: false,
              silent: true,
              decided: false,
              replyConfirmed: false,
              reasoning: "",
              reasoningStreaming: false,
              stage: "reading",
            };
            slots.push(s);
          }
          (s as { _peerHandoff?: boolean })._peerHandoff = true;
          const spoken = await runDiscussTurn(s);
          (s as { _peerHandoff?: boolean })._peerHandoff = false;
          if (pauseForUserInput(s.agent.name, confirmationText(s.agent.id, spoken))) break;
        }
        if (peerDiscuss.length === beforeLen) break; // no new discuss bubbles
      }
      }
      }
      if (optionalReply && !turnExclusive && !ac.signal.aborted && !turnPaused) {
        commitBoardFromSlots(slots);
        flushSlotsToCommitted();

        const boardOwners = (liveBoard?.tasks || [])
          .filter((t) => t.status === "open" && t.ownerId)
          .map((t) => String(t.ownerId));
        const discussRosterMini = rosterForPrompt.map((a) => ({ id: a.id, name: a.name }));
        const discussHasTaskLine = peerDiscuss.some(
          (r) => tasksClaimedInText(String(r.text || "")).length > 0,
        );
        // If an agent violated the planning protocol by promising a future
        // action without a TASK line, turn that commitment into the same
        // concrete user request. This is capability-agnostic and means the
        // second wave cannot quietly end after a "let me check" message.
        const deferredCommitters = new Set(
          peerDiscuss
            .filter(
              (r) =>
                hasUnfinishedActionCommitment(String(r.text || "")) &&
                !agentAsksForUserInput(String(r.text || "")),
            )
            .map((r) => r.agentId),
        );
        for (const s of slots) {
          if (!deferredCommitters.has(s.agent.id)) continue;
          const existing = (((s as any)._tasks as string[]) || []).filter(Boolean);
          if (!existing.length) (s as any)._tasks = [text];
        }
        const hasDeferredWork = executionRequested && deferredCommitters.size > 0;
        // Chat, questions, and @ handoffs stay in the discussion. Tools start
        // only for an explicit work order, a claimed TASK, or an open board item.
        const needsExecuteWave =
          !floorExecuted &&
          (harnessPlan?.phase === "talk"
            ? false
            :
          !lightweightSocialTurn &&
          !collectiveGreeting &&
          !directAnswerTurn &&
          (boardOwners.length > 0 ||
            discussHasTaskLine ||
            executionRequested ||
            hasDeferredWork));
        if (!needsExecuteWave) {
          // Every non-silent decision has already been published as a normal
          // group message. Do not replay it here: a chat bubble must never
          // vanish and reappear merely because routing has finished.
          for (const s of slots) {
            s.streaming = false;
            s.reasoningStreaming = false;
            if (!s.decided) {
              s.decided = true;
              s.silent = !String(s.text || "").trim();
            }
          }
          paint();
        } else {
        const harnessWorkers = harnessPlan?.phase === "work"
          ? harnessPlan.assignments
              .filter((item) => item.role === "owner" || item.role === "worker")
              .map((item) => slots.find((s) => s.agent.id === item.agentId))
              .filter((s): s is Slot => Boolean(s))
          : [];
        const operatorSlot = operatorId ? slots.find((s) => s.agent.id === operatorId) : undefined;
        if (operatorSlot) {
          const existing = (((operatorSlot as any)._tasks as string[]) || []).filter(Boolean);
          if (!existing.length) (operatorSlot as any)._tasks = [text];
          operatorSlot.stage = "executing";
          operatorSlot.streaming = true;
          paint();
        }
        const execSlots = operatorSlot
          ? [operatorSlot]
          : harnessWorkers.length
            ? harnessWorkers
            : resolveExecSlots(slots, peerDiscuss, {
              userText: text,
              mentionedIds,
              boardOwnerIds: boardOwners,
              roster: discussRosterMini,
            });
        // The execution queue may become parallel only for independent,
        // non-mutating claims (see canRunTasksInParallel).
        const readyExec = execSlots.slice();
        const claimantIds = new Set(readyExec.map((s) => s.agent.id));
        if (readyExec.length && !ac.signal.aborted) {
          // Non-executors: park as decided/silent so status UI ignores them.
          for (const s of slots) {
            if (claimantIds.has(s.agent.id)) continue;
            s.streaming = false;
            s.silent = true;
            s.decided = true;
            s.replyConfirmed = false;
            s.text = "";
            s.reasoning = "";
            s.reasoningStreaming = false;
          }
          // Executors wait in queue: parked decided so status UI stays quiet.
          for (const s of readyExec) {
            s.id = uid("m");
            s.streaming = false;
            s.silent = true;
            s.replyConfirmed = false;
            s.text = "";
            s.reasoning = "";
            s.reasoningStreaming = false;
            s.decided = true;
            s.stage = "queued";
            (s as any)._facts = [];
            (s as any)._tasks = ((s as any)._tasks as string[]) || [];
            (s as any)._doneTasks = [];
            setAgentLive((prev) => ({
              ...prev,
              [s.agent.id]: {
                ...prev[s.agent.id],
                phase: "queued",
                tools: [],
                summary: "排队中…",
                updatedAt: Date.now(),
              },
            }));
          }
          paint();
          const executeSlot = async (s: Slot) => {
            if (ac.signal.aborted || turnPaused) return;
            s.silent = false;
            s.decided = false;
            s.replyConfirmed = true;
            (s as { _clearedToSpeak?: boolean })._clearedToSpeak = true;
            s.streaming = true;
            s.stage = "executing";
            setAgentLive((prev) => ({
              ...prev,
              [s.agent.id]: {
                ...prev[s.agent.id],
                phase: "executing",
                tools: [],
                summary: "输出中…",
                updatedAt: Date.now(),
              },
            }));
            paint();
            try {
              await runOne(s, 2);
            } finally {
              s.streaming = false;
              s.stage = "done";
              if (!s.decided) {
                s.decided = true;
                s.silent = true;
                s.replyConfirmed = false;
              }
              setAgentLive((prev) => ({
                ...prev,
                [s.agent.id]: {
                  ...prev[s.agent.id],
                  phase: "done",
                  tools: prev[s.agent.id]?.tools || [],
                  summary: prev[s.agent.id]?.summary || (s.silent ? "已跳过" : "已完成"),
                  updatedAt: Date.now(),
                },
              }));
              paint();
            }
          };
          // Independent, read-only assignments run concurrently.  The safety
          // predicate above keeps effects and overlapping work serialized.
          const parallel = harnessPlan?.phase === "work"
            ? harnessPlan.topology === "parallel" && canRunTasksInParallel(readyExec)
            : canRunTasksInParallel(readyExec);
          if (parallel) {
            await Promise.all(readyExec.map((s) => executeSlot(s)));
            if (readyExec.some((s) => pauseForUserInput(s.agent.name, confirmationText(s.agent.id, String(s.text || ""))))) return;
          } else {
            for (const s of readyExec) {
              await executeSlot(s);
              if (harnessPlan?.topology === "sequential") {
                const satisfied = (s as { _goalSatisfied?: boolean })._goalSatisfied === true;
                const blocked = (s as { _goalBlocked?: boolean })._goalBlocked === true;
                const known = (s as { _goalKnown?: boolean })._goalKnown === true;
                // The next member waits until this step is actually finished.
                if (known && !satisfied && !blocked) break;
              }
              const step = cleanDisplayText(String(s.text || "")).trim();
              if (harnessPlan?.topology === "sequential") {
                if (!s.silent && step && !isNearDuplicateBubble(s.agent.id, step, committedMsgs)) {
                  committedMsgs.push({
                    id: s.id,
                    role: "agent",
                    agentId: s.agent.id,
                    agentName: s.agent.name,
                    text: step,
                    streaming: false,
                    reading: false,
                    reasoning: (s.reasoning || "").trim() || undefined,
                    reasoningStreaming: false,
                    browserUrl: s.browserUrl || undefined,
                    browserBusy: s.browserBusy,
                    at: Date.now(),
                  });
                  s.text = "";
                  s.reasoning = "";
                  s.silent = true;
                  s.decided = true;
                  s.browserUrl = undefined;
                  s.browserBusy = undefined;
                  paint();
                }
              }
              if (pauseForUserInput(s.agent.name, confirmationText(s.agent.id, step))) return;
            }
          }
          // Belt: nothing left "输出中" after the queue.
          for (const s of readyExec) {
            s.streaming = false;
            s.stage = "done";
            if (!s.decided) {
              s.decided = true;
              s.silent = true;
            }
          }
          paint();
          commitBoardFromSlots(readyExec);
          // Commit first execute bubbles (deduped) before any continue pass.
          for (const s of readyExec) {
            const raw = cleanDisplayText(String(s.text || "")).trim();
            if (!s.silent && raw) {
              const msg = {
                id: s.id,
                role: "agent" as const,
                agentId: s.agent.id,
                agentName: s.agent.name,
                text: raw,
                streaming: false,
                reading: false,
                reasoning: (s.reasoning || "").trim() || undefined,
                reasoningStreaming: false,
                browserUrl: s.browserUrl || undefined,
                browserBusy: s.browserBusy,
                at: Date.now(),
              };
              const fp = msgFingerprint(String(msg.agentId || ""), String(msg.text || ""));
              if (
                !fp.endsWith("::") &&
                !committedMsgs.some((m) => msgFingerprint(String(m.agentId || ""), String(m.text || "")) === fp) &&
                !isNearDuplicateBubble(String(msg.agentId || ""), String(msg.text || ""), committedMsgs)
              ) {
                committedMsgs.push(msg);
              }
              s.text = "";
              s.reasoning = "";
              s.silent = true;
              s.decided = true;
              s.browserUrl = undefined;
              s.browserBusy = undefined;
            }
          }
          paint();

          // Parallel workers expose their individual evidence first, then one
          // existing member integrates it into a single user-facing result.
          // This is a response role for the current turn, not a permanent
          // leader and not another task assignment.
          const verifierId = harnessPlan?.assignments.find((item) => item.role === "verifier")?.agentId;
          if ((parallel || verifierId) && !ac.signal.aborted) {
            const synthesisSlot = (verifierId ? slots.find((s) => s.agent.id === verifierId) : undefined)
              || (parallel
                ? [...readyExec].sort((a, b) => dutyScore(b.agent, text) - dutyScore(a.agent, text))[0]
                : undefined);
            if (synthesisSlot) {
              synthesisSlot.id = uid("m");
              synthesisSlot.text = "";
              synthesisSlot.reasoning = "";
              synthesisSlot.silent = false;
              synthesisSlot.decided = false;
              synthesisSlot.replyConfirmed = false;
              synthesisSlot.streaming = true;
              synthesisSlot.stage = "executing";
              (synthesisSlot as any)._tasks = [];
              setAgentLive((prev) => ({
                ...prev,
                [synthesisSlot.agent.id]: {
                  ...prev[synthesisSlot.agent.id],
                  phase: "synthesizing",
                  tools: [],
                  summary: "正在汇总并行任务结果…",
                  updatedAt: Date.now(),
                },
              }));
              paint();
              await runOne(synthesisSlot, 2, { synthesis: true });
              synthesisSlot.streaming = false;
              synthesisSlot.stage = "done";
              const synthesis = cleanDisplayText(String(synthesisSlot.text || "")).trim();
              if (!synthesisSlot.silent && synthesis && !isNearDuplicateBubble(synthesisSlot.agent.id, synthesis, committedMsgs)) {
                committedMsgs.push({
                  id: synthesisSlot.id,
                  role: "agent",
                  agentId: synthesisSlot.agent.id,
                  agentName: synthesisSlot.agent.name,
                  text: synthesis,
                  streaming: false,
                  reading: false,
                  reasoning: (synthesisSlot.reasoning || "").trim() || undefined,
                  reasoningStreaming: false,
                  browserUrl: synthesisSlot.browserUrl || undefined,
                  browserBusy: synthesisSlot.browserBusy,
                  at: Date.now(),
                });
              }
              synthesisSlot.text = "";
              synthesisSlot.reasoning = "";
              synthesisSlot.silent = true;
              synthesisSlot.decided = true;
              paint();
            }
          }

          let execStall = 0;
          let goalPasses = 0;
          const committedFp = new Set(
            committedMsgs.map((m) => msgFingerprint(String(m.agentId || ""), String(m.text || ""))),
          );
          const pushUniqueCommitted = (msg: (typeof committedMsgs)[number]) => {
            const fp = msgFingerprint(String(msg.agentId || ""), String(msg.text || ""));
            if (fp.endsWith("::") || committedFp.has(fp)) return false;
            if (isNearDuplicateBubble(String(msg.agentId || ""), String(msg.text || ""), committedMsgs)) return false;
            committedFp.add(fp);
            committedMsgs.push(msg);
            return true;
          };
// Keep executing while shared-board tasks remain open (Stop aborts).
          while (!ac.signal.aborted) {
            {
              const rosterMini = rosterForPrompt.map((a) => ({ id: a.id, name: a.name }));
              const texts = committedMsgs
                .filter((m) => m.role === "agent" && (m.text || "").trim())
                .map((m) => String(m.text || ""));
              if (agentsConfirmedUserTaskDone(liveBoard, texts, rosterMini)) break;
            }
            const openOwners = (liveBoard?.tasks || [])
              .filter((t) => t.status === "open" && t.ownerId)
              .map((t) => String(t.ownerId));
            if (!openOwners.length) break;
            // The backend judge already spent its own continuation budget
            // inside the turn. Two social passes are enough to act on a
            // "continue" verdict; more only re-polishes finished work.
            if (goalPasses >= 2) break;
            const ownerSlots = slots.filter((s) => openOwners.includes(s.agent.id));
            if (
              ownerSlots.length > 0 &&
              ownerSlots.every((s) => (s as { _goalSatisfied?: boolean })._goalSatisfied === true)
            ) {
              break;
            }
            if (
              ownerSlots.length > 0 &&
              ownerSlots.every((s) => Boolean((s as { _goalBlocked?: boolean })._goalBlocked))
            ) {
              break;
            }
            goalPasses += 1;
            const sequentialOrder = harnessPlan?.topology === "sequential"
              ? (harnessPlan.assignments || [])
                  .filter((item) => item.role === "owner" || item.role === "worker")
                  .map((item) => item.agentId)
              : [];
            const sequentialOwner = sequentialOrder.find((id) => openOwners.includes(id));
            const contSlots = slots.filter((s) => {
              if (!openOwners.includes(s.agent.id)) return false;
              if (operatorId && s.agent.id !== operatorId) return false;
              if (sequentialOwner && s.agent.id !== sequentialOwner) return false;
              return true;
            });
            if (!contSlots.length) break;
            for (const s of contSlots) {
              s.id = uid("m");
              s.text = "";
              s.reasoning = "";
              s.streaming = true;
              s.stage = "executing";
              s.silent = false;
              s.decided = false;
              s.replyConfirmed = false;
              s.reasoningStreaming = false;
              const prevTasks = (((s as any)._tasks as string[]) || []).filter(Boolean);
              (s as any)._tasks = prevTasks.length
                ? prevTasks
                : (liveBoard?.tasks || [])
                    .filter((t) => t.status === "open" && t.ownerId === s.agent.id)
                    .map((t) => String((t as any).title || (t as any).text || (t as any).label || "继续执行"))
                    .slice(0, 3);
              setAgentLive((prev) => ({
                ...prev,
                [s.agent.id]: {
                  ...prev[s.agent.id],
                  phase: "executing",
                  tools: [],
                  summary: "继续执行中…",
                  updatedAt: Date.now(),
                },
              }));
            }
            paint();
            for (const s of contSlots) {
              if (ac.signal.aborted || turnPaused) break;
              s.replyConfirmed = true;
              (s as { _clearedToSpeak?: boolean })._clearedToSpeak = true;
              await runOne(s, 2);
              if (pauseForUserInput(s.agent.name, confirmationText(s.agent.id, String(s.text || "")))) return;
            }
            commitBoardFromSlots(contSlots);
            // Flush new completion bubbles; near-duplicates do not count as progress.
            let added = 0;
            for (const s of contSlots) {
              const raw = cleanDisplayText(String(s.text || "")).trim();
              if (!s.silent && raw) {
                const ok = pushUniqueCommitted({
                  id: s.id,
                  role: "agent",
                  agentId: s.agent.id,
                  agentName: s.agent.name,
                  text: raw,
                  streaming: false,
                  reading: false,
                  reasoning: (s.reasoning || "").trim() || undefined,
                  reasoningStreaming: false,
                  browserUrl: s.browserUrl || undefined,
                  browserBusy: s.browserBusy,
                  at: Date.now(),
                });
                if (ok) added += 1;
                s.text = "";
                s.reasoning = "";
                s.silent = true;
                s.decided = true;
                s.browserUrl = undefined;
                s.browserBusy = undefined;
              }
            }
            paint();
            if (added === 0) {
              execStall += 1;
            } else {
              execStall = 0;
            }
            if (execStall >= 4 && contSlots.every((s) => (s as { _goalBlocked?: boolean })._goalBlocked)) {
              break;
            }
          }
          // The same operation is not handed to another member.
          const handledHandoffMsgIds = new Set<string>();
          let handoffStall = 0;
          while (!operatorId && harnessPlan?.phase !== "work" && !ac.signal.aborted) {
            {
              const rosterMini = rosterForPrompt.map((a) => ({ id: a.id, name: a.name }));
              const texts = committedMsgs
                .filter((m) => m.role === "agent" && (m.text || "").trim())
                .map((m) => String(m.text || ""));
              if (agentsConfirmedUserTaskDone(liveBoard, texts, rosterMini)) break;
            }
            const recent = committedMsgs
              .filter((m) => m.role === "agent" && (m.text || "").trim())
              .slice(-12);
            // Only follow handoffs from messages we have not acted on yet.
            const fresh = recent.filter((m) => m.id && !handledHandoffMsgIds.has(String(m.id)));
            if (!fresh.length) break;

            const rosterMini = rosterForPrompt.map((a) => ({ id: a.id, name: a.name }));
            const last = recent[recent.length - 1];
            const fromLast = last
              ? findHandoffAgentIds(String(last.text || ""), rosterMini, String(last.agentId || ""))
              : [];
            // Prefer latest message's @targets; else any fresh handoff targets.
            let runIds = fromLast;
            if (!runIds.length) {
              const ids = new Set<string>();
              for (const m of fresh) {
                for (const id of findHandoffAgentIds(String(m.text || ""), rosterMini, String(m.agentId || ""))) {
                  ids.add(id);
                }
              }
              runIds = [...ids];
            }
            if (!runIds.length) {
              // Mark fresh messages handled even if no target parsed — avoid re-scan forever.
              for (const m of fresh) handledHandoffMsgIds.add(String(m.id));
              break;
            }
            // Don't immediately re-run the author of the latest handoff ask.
            if (last?.agentId) {
              runIds = runIds.filter((id) => id !== String(last.agentId));
            }
            if (!runIds.length) {
              for (const m of fresh) handledHandoffMsgIds.add(String(m.id));
              break;
            }

            const contSlots = slots.filter((s) => runIds.includes(s.agent.id));
            if (!contSlots.length) {
              for (const m of fresh) handledHandoffMsgIds.add(String(m.id));
              break;
            }

            const beforeCount = committedMsgs.length;
            for (const s of contSlots) {
              s.id = uid("m");
              s.text = "";
              s.reasoning = "";
              s.streaming = true;
              s.stage = "executing";
              s.silent = false;
              s.decided = false;
              s.replyConfirmed = false;
              s.reasoningStreaming = false;
              const prevTasks = (((s as any)._tasks as string[]) || []).filter(Boolean);
              if (!prevTasks.length) {
                s.streaming = false;
                s.silent = true;
                s.decided = true;
                s.stage = "done";
                continue;
              }
              (s as any)._tasks = prevTasks;
              setAgentLive((prev) => ({
                ...prev,
                [s.agent.id]: {
                  ...prev[s.agent.id],
                  phase: "executing",
                  tools: [],
                  summary: "接手推进中…",
                  updatedAt: Date.now(),
                },
              }));
            }
            paint();
            for (const s of contSlots) {
              if (ac.signal.aborted || turnPaused) break;
              if (!(((s as any)._tasks as string[]) || []).length) continue;
              s.replyConfirmed = true;
              (s as { _clearedToSpeak?: boolean })._clearedToSpeak = true;
              await runOne(s, 2);
              if (pauseForUserInput(s.agent.name, confirmationText(s.agent.id, String(s.text || "")))) break;
            }
            commitBoardFromSlots(contSlots);
            for (const s of contSlots) {
              const raw = cleanDisplayText(String(s.text || "")).trim();
              if (!s.silent && raw) {
                const msg = {
                  id: s.id,
                  role: "agent" as const,
                  agentId: s.agent.id,
                  agentName: s.agent.name,
                  text: raw,
                  streaming: false,
                  reading: false,
                  reasoning: (s.reasoning || "").trim() || undefined,
                  reasoningStreaming: false,
                  browserUrl: s.browserUrl || undefined,
                  browserBusy: s.browserBusy,
                  at: Date.now(),
                };
                pushUniqueCommitted(msg);
                s.text = "";
                s.reasoning = "";
                s.silent = true;
                s.decided = true;
                s.browserUrl = undefined;
                s.browserBusy = undefined;
              }
            }
            paint();
            for (const m of fresh) handledHandoffMsgIds.add(String(m.id));
            {
              const rosterMini = rosterForPrompt.map((a) => ({ id: a.id, name: a.name }));
              const texts = committedMsgs
                .filter((m) => m.role === "agent" && (m.text || "").trim())
                .map((m) => String(m.text || ""));
              if (agentsConfirmedUserTaskDone(liveBoard, texts, rosterMini)) break;
            }
            if (committedMsgs.length === beforeCount) {
              handoffStall += 1;
              if (handoffStall >= 1) break; // near-dup / no new unique bubbles → stop
            } else {
              handoffStall = 0;
            }
          }
          if (harnessPlan?.phase === "work" && harnessPlan.topology !== "solo" && !ac.signal.aborted && !turnPaused) {
            const before = currentHarnessDiagnosis();
            const repair = before?.innerRepair;
            if (before && repair) {
              harnessPrior = before;
              const owner = slots.find((s) => s.agent.id === repair.agentId);
              if (owner) {
                harnessRepair = repair.note;
                harnessRepaired = true;
                const owned = harnessPlan.assignments.find((item) => item.agentId === owner.agent.id && (item.role === "owner" || item.role === "worker"));
                (owner as any)._tasks = [owned?.task || text];
                owner.id = uid("m");
                owner.text = "";
                owner.reasoning = "";
                owner.silent = false;
                owner.decided = false;
                owner.replyConfirmed = false;
                owner.streaming = true;
                owner.stage = "executing";
                noteAgentActivity(owner.agent, "execute", "上一轮协作没有交付，改为单人完成");
                await runOne(owner, 2);
                const repaired = cleanDisplayText(String(owner.text || "")).trim();
                if (!owner.silent && repaired && !isNearDuplicateBubble(owner.agent.id, repaired, committedMsgs)) {
                  committedMsgs.push({
                    id: owner.id,
                    role: "agent",
                    agentId: owner.agent.id,
                    agentName: owner.agent.name,
                    text: repaired,
                    streaming: false,
                    reading: false,
                    reasoning: (owner.reasoning || "").trim() || undefined,
                    reasoningStreaming: false,
                    browserUrl: owner.browserUrl || undefined,
                    browserBusy: owner.browserBusy,
                    at: Date.now(),
                  });
                }
                owner.text = "";
                owner.reasoning = "";
                owner.silent = true;
                owner.decided = true;
                owner.streaming = false;
                owner.stage = "done";
                paint();
              }
            }
          }

        }
        } // end needsExecuteWave else
      }
      } // end DM / legacy path
    } finally {
      if (isStaleSend()) {
        // A newer send owns the busy flag; only clear it when we were cleared.
        if (!isSuperseded()) {
          setBusyThreads((prev) => {
            const n = { ...prev };
            delete n[key];
            return n;
          });
        }
        if (abortByThreadRef.current[key] === ac) delete abortByThreadRef.current[key];
        return;
      }
      // Only the focused thread may tear down the shared browser — a background
      // group finishing must not blank a DM that is mid-browse.
      if (key === threadKeyRef.current) {
        // 会话版: the agent closes its browser when the turn ends.
        for (const s of slots) {
          s.browserUrl = undefined;
          s.browserBusy = undefined;
        }
        for (const m of committedMsgs) {
          m.browserUrl = undefined;
          m.browserBusy = undefined;
        }
        if (!browserHoldRef.current) {
          setBrowserCard(null);
        }
      }
      if (harnessPlan && !turnPaused && !ac.signal.aborted && !isStaleSend()) {
        const diagnosis = currentHarnessDiagnosis();
        if (diagnosis) {
          const plan = harnessPlan;
          patchStore((s) => {
            s.harness = applyHarnessOutcome(s.harness, plan, diagnosis, {
              repairedToSolo: Boolean(harnessRepaired && diagnosis.success),
              priorFailures: harnessPrior?.failures,
              attemptedSoloRepair: harnessRepaired,
            });
            return s;
          });
        }
      }
      if (executionRequested && key === threadKeyRef.current) {
        const situation = classifySituation(raw || text);
        const reply = [...committedMsgs].reverse().find((m) => m.role === "agent" && String(m.text || "").trim());
        const success = Boolean(reply) && !browserHoldRef.current;
        const workflow = success ? abstractWorkflow(String(reply?.text || "")) : "";
        patchStore((s) => {
          s.strategy = applyOutcome(ensureStrategy(s.strategy), situation, success, undefined, workflow);
          return s;
        });
        appendRlTransition({
          id: uid("rl"),
          at: Date.now(),
          threadKey: key,
          agentId: reply?.agentId || "",
          agentName: reply?.agentName || "",
          situation,
          state: { userText: raw || text },
          action: { reply: String(reply?.text || ""), policy: ensureStrategy(undefined)[situation].policy },
          reward: success ? 1 : 0,
          rewardSource: "task",
        });
      }
      // Members who were never called must not stay on "reading" after the turn ends.
      for (const s of slots) {
        if (s.stage !== "reading" && s.stage !== "queued") continue;
        s.stage = "done";
        s.streaming = false;
        if (!s.decided) {
          s.decided = true;
          s.silent = !String(s.text || "").trim();
        }
      }
      // Keep finished reply bubbles; do not leave UI on a transient empty paint.
      try {
        paint();
        if (
          optionalReply &&
          slots.every((s) => s.decided) &&
          slots.every((s) => s.silent) &&
          committedMsgs.length === 0
        ) {
          writeThreadMessages([
            ...hist,
            ...harnessNotices,
            ...committedMsgs,
            {
              id: `noreply-${Date.now()}`,
              role: "system" as const,
              text: t("socialNobodyReplied"),
              at: Date.now(),
            },
          ]);
        }
      } catch {}
      setBusyThreads((prev) => {
        const n = { ...prev };
        delete n[key];
        return n;
      });
      if (abortByThreadRef.current[key] === ac) delete abortByThreadRef.current[key];
    }
  };


  function paintSelectHighlight(range: Range | null) {
    const css = (window.CSS as unknown as { highlights?: { set: (name: string, value: unknown) => void; delete: (name: string) => void } } | undefined);
    const HighlightCtor = (window as unknown as { Highlight?: new (...ranges: Range[]) => unknown }).Highlight;
    if (!css?.highlights || !HighlightCtor) return;
    if (!range) {
      css.highlights.delete("social-select");
      return;
    }
    css.highlights.set("social-select", new HighlightCtor(range));
  }

  function clearSelectHighlight() {
    paintSelectHighlight(null);
    try {
      window.getSelection()?.removeAllRanges();
    } catch {
      /* ignore */
    }
  }

  function onChatSelect(e: React.MouseEvent) {
    const target = e.target as HTMLElement | null;
    const gesture = selectGesture.current;
    const dragged = Math.hypot(e.clientX - gesture.x, e.clientY - gesture.y) > 4;
    const picked = dragged || e.detail >= 2;
    if (!picked || target?.closest?.("button, input, textarea")) {
      clearSelectHighlight();
      setQuoteMenu(null);
      return;
    }
    const sel = window.getSelection();
    const text = sel?.toString().trim() || "";
    if (!text || text.length < 2 || !sel || sel.rangeCount < 1) {
      clearSelectHighlight();
      setQuoteMenu(null);
      return;
    }
    paintSelectHighlight(sel.getRangeAt(0).cloneRange());
    setQuoteMenu({ x: e.clientX, y: e.clientY, text: text.slice(0, 2000) });
  }

  function applyQuote(text: string) {
    setQuote(text);
    clearSelectHighlight();
    setQuoteMenu(null);
  }

  const stop = () => {
    if (threadKey) {
      try {
        abortByThreadRef.current[threadKey]?.abort();
      } catch {}
      delete abortByThreadRef.current[threadKey];
      setBusyThreads((prev) => {
        const n = { ...prev };
        delete n[threadKey];
        return n;
      });
      // Stop is a terminal UI transition: no member may remain animated after
      // its request was aborted. Keep a short audit state in task details.
      setAgentLive((prev) => {
        const next: typeof prev = {};
        for (const [agentId, live] of Object.entries(prev)) {
          if (!isActiveAgentPhase(live.phase)) {
            next[agentId] = live;
            continue;
          }
          next[agentId] = {
            ...live,
            phase: "done",
            summary: "已停止",
            activity: [
              ...(live.activity || []),
              { id: uid("activity"), kind: "done" as AgentActivityKind, detail: "用户停止了本轮处理", at: Date.now() },
            ].slice(-24),
            updatedAt: Date.now(),
          };
        }
        return next;
      });
    }
    // Stop the turn, but keep the sandbox page so the in-bubble preview
    // (and workbench sidebar) are not left on about:blank / 「已关闭」.
    browserUserClosedRef.current = true;
    if (threadKey) {
      const tk = threadKey;
      patchStore((s) => {
        const rows = s.threads[tk] || [];
        s.threads[tk] = rows.map((msg) =>
          msg.browserUrl ? { ...msg, browserBusy: false } : msg,
        );
        return s;
      });
    }
  };



    // Read-only workbench timeline for the group share pane.
  // Do NOT depend on `agents` — patchStore structuredClones the store and would retrigger forever.
  const agentsRef = useRef(agents);
  agentsRef.current = agents;
  


  const linkedWorkbenchDm =
    view.kind === "dm" && isWorkbenchLinkedAgent(view.agentId);
  const linkedDmAgentId = view.kind === "dm" ? view.agentId : "";

    // Keep lead-agent DM on the same timeline as the workbench session.
  // Guard with a stamp so structuredClone(agents) cannot retrigger an infinite rewrite loop.
  const leadHydrateStampRef = useRef("");
  useEffect(() => {
    if (!linkedWorkbenchDm || !sessionId || !threadKey) {
      if (!linkedWorkbenchDm) leadHydrateStampRef.current = "";
      return;
    }
    const stamp = `${sessionId}::${threadKey}`;
    if (leadHydrateStampRef.current === stamp) return;
    let cancelled = false;
    const list = agentsRef.current;
    const agent =
      agentById(list, linkedDmAgentId) ||
      list.find((a) => a.id === WORKBENCH_LINKED_AGENT_ID);
    if (!agent) return;
    void hydrateLinkedDmFromSession(sessionId, agent)
      .then((msgs) => {
        if (cancelled) return;
        const banner: SocialChatMsg = {
          id: "workbench-sync-banner",
          role: "system",
          text: t("socialWorkbenchSyncBanner"),
          at: 0,
        };
        const nextMsgs = [banner, ...msgs.filter((m) => m.role !== "system")];
        leadHydrateStampRef.current = stamp;
        setThreadMessages(threadKey, nextMsgs);
      })
      .catch((e) => {
        if (!cancelled) onToast(String((e as Error)?.message || e));
      });
    return () => {
      cancelled = true;
    };
  }, [linkedWorkbenchDm, linkedDmAgentId, sessionId, threadKey, t, onToast]);


  const openDm = (agentId: string, fromGroupId?: string) => {
    setShareTab("search");
    // From a group: that group's context only. From the agent list: a private chat
    // with no group roster and no DECISION:silent protocol.
    setStore((prev) => {
      const next = fromGroupId
        ? markDmAware(prev, agentId)
        : setDmSessionMode(prev, agentId, "blank");
      saveSocialStore(next);
      return next;
    });
    setView({ kind: "dm", agentId, fromGroupId });
    setInput("");
  };

  const startFreshDm = () => {
    if (view.kind !== "dm") return;
    if (isWorkbenchLinkedAgent(view.agentId)) {
      onToast(t("socialWorkbenchNoBlankSession"));
      return;
    }
    const agentId = view.agentId;
    const fromGroupId = view.fromGroupId;
    const result = startFreshDmSession(store, agentId);
    setStore(result.store);
    saveSocialStore(result.store);
    setCtx({ tokens: 0, limit: 48000 });
    setCtxByAgent((prev) => {
      const next = { ...prev };
      delete next[agentId];
      return next;
    });
    setView({ kind: "dm", agentId, fromGroupId });
    setInput("");
    onToast(t("socialDmNewSessionToast"));
  };


  const addAnnouncement = (groupId: string, title: string, body: string) => {
    if (!title.trim() || !body.trim()) return;
    patchStore((s) => {
      const g = s.groups.find((x) => x.id === groupId);
      if (!g) return s;
      const ann: SocialAnnouncement = {
        id: uid("ann"),
        title: title.trim(),
        body: body.trim(),
        createdAt: Date.now(),
        authorKind: "user",
        authorName: t("socialYou"),
      };
      g.announcements = [ann, ...(g.announcements || [])];
      return s;
    });
  };

  const dropAnnouncement = (groupId: string, id: string) => {
    patchStore((s) => {
      const g = s.groups.find((x) => x.id === groupId);
      if (!g) return s;
      g.announcements = (g.announcements || []).filter((a) => a.id !== id);
      return s;
    });
  };

  const saveAgent = (draft: {
    id?: string;
    name: string;
    title: string;
    blurb: string;
    duty: string;
    persona: string;
  }) => {
    const name = draft.name.trim();
    if (!name) {
      onToast(t("socialNeedName"));
      return;
    }
    patchStore((s) => {
      if (draft.id) {
        const a = s.agents.find((x) => x.id === draft.id);
        if (a) {
          a.name = name;
          a.title = draft.title.trim() || a.title;
          a.blurb = draft.blurb.trim() || a.blurb;
          a.duty = draft.duty.trim() || a.duty;
          a.persona = draft.persona.trim() || a.persona;
        }
      } else {
        const id = uid("agent");
        s.agents.push({
          id,
          name,
          title: draft.title.trim() || t("socialAgentDefaultTitle"),
          color: nextAgentColor(s.agents),
          blurb: draft.blurb.trim() || draft.duty.trim().slice(0, 24) || name,
          duty: draft.duty.trim() || draft.blurb.trim() || name,
          persona:
            draft.persona.trim() ||
            `\u4f60\u662f\u667a\u80fd\u4f53\u300c${name}\u300d\uff0c\u804c\u8d23\u662f\uff1a${draft.duty.trim() || draft.blurb.trim() || name}\u3002`,
          skills: [...DEFAULT_SKILLS],
          tools: [...DEFAULT_TOOLS],
          remark: "",
          location: "",
        });
        // Only join the group the user is currently viewing.
        // Never fall back to groups[0] — that wrongly lists new agents in old groups.
        if (view.kind === "group") {
          const target = s.groups.find((g) => g.id === view.groupId);
          if (target && !target.memberIds.includes(id)) target.memberIds.push(id);
        }
      }
      return s;
    });
    // Stay in the current group chat after creating an agent there.
    if (view.kind === "group") {
      setView({ kind: "group", groupId: view.groupId });
    } else {
      setView({ kind: "home" });
    }
  };

  const saveGroup = (draft: {
    id?: string;
    name: string;
    topic: string;
    memberIds: string[];
  }) => {
    const name = draft.name.trim();
    if (!name) {
      onToast(t("socialNeedName"));
      return;
    }
    if (!draft.memberIds.length) {
      onToast(t("socialNeedMembers"));
      return;
    }
    let nextId = draft.id;
    patchStore((s) => {
      if (draft.id) {
        const g = s.groups.find((x) => x.id === draft.id);
        if (g) {
          const oldName = g.name;
          g.name = name;
          g.topic = draft.topic.trim() || undefined;
          g.memberIds = [...draft.memberIds];
          if (oldName !== name) {
            const tagRe = /所属群组[\uFF1A:][^\n]*/;
            const line = "所属群组\uFF1A" + name;
            for (const mid of g.memberIds) {
              const a = s.agents.find((x) => x.id === mid);
              if (!a) continue;
              const swap = (v?: string) =>
                (v || "").split(oldName).join(name).replace(tagRe, line);
              a.persona = swap(a.persona) || a.persona;
              a.blurb = swap(a.blurb) || a.blurb;
              a.duty = swap(a.duty) || a.duty;
              if (!(a.persona || "").includes("所属群组")) {
                a.persona = ((a.persona || "").trim() + "\n" + line).trim();
              }
            }
          }
        }
      } else {
        nextId = uid("group");
        s.groups.push({
          id: nextId!,
          name,
          topic: draft.topic.trim() || undefined,
          memberIds: [...draft.memberIds],
          announcements: [
            {
              id: uid("ann"),
              title: t("socialDefaultAnnTitle"),
              body: t("socialDefaultAnnBody"),
              createdAt: Date.now(),
            },
          ],
          shared: [],
          createdAt: Date.now(),
        });
      }
      return s;
    });
    setView({ kind: "group", groupId: nextId! });
  };

  /* ---------- render pieces ---------- */

  const topBar = (
    <header className="social-topbar">
      <div className="social-topbar-left">
        {view.kind !== "home" ? (
          <button
            type="button"
            className="social-icon-btn"
            onClick={() => {
              if (view.kind === "dm" && view.fromGroupId) {
                setView({ kind: "group", groupId: view.fromGroupId });
              } else if (view.kind === "editAgent" || view.kind === "editGroup") {
                setView({ kind: "home" });
              } else {
                setView({ kind: "home" });
              }
              setInput("");
            }}
            aria-label="back"
          >
            ←
          </button>
        ) : (
          <span className="social-brand">{t("socialBrand")}</span>
        )}
        <div className="social-top-title">
          {view.kind === "home" && t("socialHomeTitle")}
          {view.kind === "group" && (activeGroup?.name || t("socialDefaultGroup"))}
          {view.kind === "dm" && (activeAgent ? `${activeAgent.name}` : t("socialDm"))}
          {view.kind === "editAgent" && (view.agentId ? t("socialEditAgent") : t("socialNewAgent"))}
          {view.kind === "editGroup" && (view.groupId ? t("socialEditGroup") : t("socialNewGroup"))}
        </div>
      </div>
      <div className="social-topbar-right">
        {(view.kind === "group" || view.kind === "dm") && (
          <>
            {chatWorkspacePath && (
              <button
                type="button"
                className="social-ws-chip"
                title={(boundWorkspace ? t("socialWorkspaceBound") + ": " : "") + chatWorkspacePath}
                onClick={() => { setShareCollapsed(false); setShareTab("files"); }}
              >
                {(boundWorkspace ? "P " : "") + (chatWorkspacePath.replace(/\\/g, "/").split("/").filter(Boolean).pop() || chatWorkspacePath)}
              </button>
            )}
          </>
        )}
        <button type="button" className="social-icon-btn social-settings-btn" onClick={onOpenSettings} title={t("settings")}>
          ⚙
        </button>
      </div>
    </header>
  );

  if (view.kind === "editAgent") {
    const existing = view.agentId ? agentById(agents, view.agentId) : null;
    return (
      <section className={"social-shell" + (embedded ? " embedded" : "")}>
        {topBar}
        <AgentForm
          t={t}
          initial={existing ?? null}
          onCancel={() => setView({ kind: "home" })}
          onSave={saveAgent}
        />
      </section>
    );
  }

  if (view.kind === "editGroup") {
    const existing = view.groupId ? groups.find((g) => g.id === view.groupId) : null;
    return (
      <section className="social-shell">
        {topBar}
        <GroupForm
          t={t}
          agents={agents}
          initial={existing ?? null}
          onCancel={() => setView({ kind: "home" })}
          onSave={saveGroup}
        />
      </section>
    );
  }

  if (view.kind === "home") {
    return (
      <section className="social-shell">
        {topBar}
        <div className="social-home">
          <div className="social-home-toolbar">
            <input
              className="social-home-search"
              value={homeSearch}
              onChange={(e) => setHomeSearch(e.target.value)}
              placeholder={t("socialSearchPh")}
            />
            <div className="social-create-wrap">
              <button
                type="button"
                className="social-fab-btn"
                aria-label={t("socialCreateMenu")}
                onClick={() => setCreateMenuOpen((v) => !v)}
              >
                +
              </button>
              {createMenuOpen && (
                <div className="social-create-menu">
                  <button
                    type="button"
                    onClick={() => {
                      setCreateMenuOpen(false);
                      setCreateWizard("agent");
                    }}
                  >
                    {t("socialAddAgent")}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setCreateMenuOpen(false);
                      setCreateWizard("group");
                    }}
                  >
                    {t("socialCreateGroup")}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setCreateMenuOpen(false);
                      setFolderCreateOpen(true);
                      setFolderDraft("");
                    }}
                  >
                    {t("socialNewFolder")}
                  </button>
                </div>
              )}
            </div>
          </div>

          <div className="social-folder-chips">
            <button
              type="button"
              className={"social-folder-chip" + (homeFolderFilter === "all" ? " on" : "")}
              onClick={() => setHomeFolderFilter("all")}
            >
              {t("socialFolderAll")}
            </button>
            <button
              type="button"
              className={"social-folder-chip" + (homeFolderFilter === "none" ? " on" : "")}
              onClick={() => setHomeFolderFilter("none")}
            >
              {t("socialFolderNone")}
            </button>
            {foldersSorted.map((f) => {
              const on = homeFolderFilter === "folder:" + f.id;
              return (
                <span key={f.id} className={"social-folder-chip-wrap" + (on ? " on" : "")}>
                  <button
                    type="button"
                    className={"social-folder-chip" + (on ? " on" : "")}
                    onClick={() => setHomeFolderFilter("folder:" + f.id)}
                  >
                    {f.name}
                  </button>
                  {on ? (
                    <button
                      type="button"
                      className="social-folder-chip-delete"
                      title={t("socialDeleteFolder")}
                      aria-label={t("socialDeleteFolder")}
                      onClick={() => deleteFolder(f.id)}
                    >
                      {t("socialDeleteFolder")}
                    </button>
                  ) : null}
                </span>
              );
            })}
          </div>

          {folderCreateOpen ? (
            <div className="social-folder-create">
              <input
                value={folderDraft}
                onChange={(e) => setFolderDraft(e.target.value)}
                placeholder={t("socialFolderNamePh")}
                onKeyDown={(e) => {
                  if (e.key === "Enter") createFolder();
                }}
                autoFocus
              />
              <button type="button" className="social-primary-btn social-tiny" onClick={createFolder}>
                {t("socialSave")}
              </button>
              <button type="button" className="social-ghost-btn social-tiny" onClick={() => setFolderCreateOpen(false)}>
                {t("socialCancel")}
              </button>
            </div>
          ) : null}

          <div className="social-list-meta">
            <span className="social-muted">{t("socialListCount", String(conversationList.length))}</span>
          </div>

          <ConversationList
            t={t}
            rows={conversationList}
            pageRows={pagedConversationList}
            groups={groups}
            agents={agents}
            folders={folders}
            page={safeListPage}
            pageCount={listPageCount}
            onPage={setListPage}
            onOpenGroup={(id) => setView({ kind: "group", groupId: id })}
            onOpenAgent={(id) => openDm(id)}
            onConfigure={(kind, id) => setConfigSheet({ kind, id })}
          />
        </div>

        {createWizard && (
          <div className="social-sheet-mask social-sheet-mask-center" onClick={() => setCreateWizard(null)}>
            <div
              className={"social-sheet social-sheet-landscape" + (createWizard === "agent" ? " social-sheet-agent" : " social-sheet-group")}
              onClick={(e) => e.stopPropagation()}
              role="dialog"
              aria-modal="true"
            >
              <div className="social-sheet-head">
                <button type="button" className="social-ghost-btn" onClick={() => setCreateWizard(null)}>
                  {t("socialCancel")}
                </button>
                <strong>{createWizard === "agent" ? t("socialAddAgent") : t("socialCreateGroup")}</strong>
                <span />
              </div>
              {createWizard === "agent" ? (
                <AgentForm
                  t={t}
                  initial={null}
                  embedded
                  onCancel={() => setCreateWizard(null)}
                  onSave={(d) => {
                    saveAgent(d);
                    setCreateWizard(null);
                  }}
                />
              ) : (
                <GroupForm
                  t={t}
                  agents={agents}
                  initial={null}
                  embedded
                  onCancel={() => setCreateWizard(null)}
                  onSave={(d) => {
                    saveGroup(d);
                    setCreateWizard(null);
                  }}
                />
              )}
            </div>
          </div>
        )}

        {configSheet && (
          <ConfigSheet
            t={t}
            target={configSheet}
            agent={configSheet.kind === "agent" ? agents.find((x) => x.id === configSheet.id) || null : null}
            group={configSheet.kind === "group" ? groups.find((x) => x.id === configSheet.id) || null : null}
            allAgents={agents}
            folders={foldersSorted}
            workspaces={workspaces}
            onClose={() => setConfigSheet(null)}
            onSaveAgent={(id, patch) => {
              updateAgentField(id, patch);
              setConfigSheet(null);
              onToast(t("socialConfigSaved"));
            }}
            onSaveGroup={(id, patch) => {
              const g = groups.find((x) => x.id === id);
              const nameChanged = !!(patch.name && g && patch.name !== g.name);
              updateGroupField(id, patch, { syncName: nameChanged });
              setConfigSheet(null);
              if (!nameChanged) onToast(t("socialConfigSaved"));
            }}
            onClearChat={(key) => {
              clearThread(key);
              setConfigSheet(null);
            }}
            onDeleteAgent={(id) => {
              deleteAgent(id);
            }}
            onDeleteGroup={(id) => {
              deleteGroup(id);
            }}
            onRemoveFromGroup={(groupId, agentId) => {
              removeAgentFromGroup(groupId, agentId);
            }}
          />
        )}
      </section>
    );
  }

  // group or dm chat
  return (
    <section className="social-shell">
      {topBar}
      {configSheet && (
        <ConfigSheet
          t={t}
          target={configSheet}
          agent={configSheet.kind === "agent" ? agents.find((x) => x.id === configSheet.id) || null : null}
          group={configSheet.kind === "group" ? groups.find((x) => x.id === configSheet.id) || null : null}
          allAgents={agents}
            folders={foldersSorted}
            workspaces={workspaces}
          onClose={() => setConfigSheet(null)}
          onSaveAgent={(id, patch) => {
            updateAgentField(id, patch);
            setConfigSheet(null);
            onToast(t("socialConfigSaved"));
          }}
          onSaveGroup={(id, patch) => {
            const g = groups.find((x) => x.id === id);
            const nameChanged = !!(patch.name && g && patch.name !== g.name);
            updateGroupField(id, patch, { syncName: nameChanged });
            setConfigSheet(null);
            if (!nameChanged) onToast(t("socialConfigSaved"));
          }}
          onClearChat={(key) => {
            clearThread(key);
            setConfigSheet(null);
          }}
            onDeleteAgent={(id) => {
              deleteAgent(id);
            }}
            onDeleteGroup={(id) => {
              deleteGroup(id);
            }}
            onRemoveFromGroup={(groupId, agentId) => {
              removeAgentFromGroup(groupId, agentId);
            }}
        />
      )}
      <div className={`social-body ${view.kind === "group" || view.kind === "dm" ? "has-share" : ""}`}>
        {(view.kind === "group" || view.kind === "dm") && (
          <aside className={"social-share-pane" + (shareCollapsed ? " collapsed" : "") + (shareTab === "browser" && !shareCollapsed ? " browser-wide" : "")}>
            {shareCollapsed && (
              <div className="social-share-rail">
                {view.kind === "group" && (
                  <>
                    <button
                      type="button"
                      title={t("socialAnnouncements")}
                      aria-label={t("socialAnnouncements")}
                      className={shareTab === "ann" ? "on" : ""}
                      onClick={() => { setShareCollapsed(false); setShareTab("ann"); }}
                    >
                      <IconPin size={14} />
                    </button>
                    <button
                      type="button"
                      title={t("socialMembers")}
                      aria-label={t("socialMembers")}
                      onClick={() => { setShareCollapsed(false); setShareTab("members"); }}
                    >
                      <IconUser size={14} />
                    </button>
                  </>
                )}
                <button
                  type="button"
                  title={t("socialTabSchedule")}
                  aria-label={t("socialTabSchedule")}
                  className={shareTab === "schedule" ? "on" : ""}
                  onClick={() => { setShareCollapsed(false); setShareTab("schedule"); }}
                >
                  <IconCalendar size={14} />
                </button>
                <button
                  type="button"
                  title={t("socialTabSearch")}
                  aria-label={t("socialTabSearch")}
                  onClick={() => { setShareCollapsed(false); setShareTab("search"); }}
                >
                  <IconSearch size={14} />
                </button>
                <button
                  type="button"
                  title={t("socialTabFiles")}
                  aria-label={t("socialTabFiles")}
                  onClick={() => { setShareCollapsed(false); setShareTab("files"); }}
                >
                  <IconFiles size={14} />
                </button>
                <button
                  type="button"
                  title={t("socialTabBrowser")}
                  aria-label={t("socialTabBrowser")}
                  className={shareTab === "browser" ? "on" : ""}
                  onClick={() => { setShareCollapsed(false); setShareTab("browser"); }}
                >
                  <IconGlobe size={14} />
                </button>
              </div>
            )}
            <div className={"social-share-tabs" + (shareCollapsed ? " icon-rail" : "")}>
              {view.kind === "group" && (
                <>
                  <button
                    type="button"
                    className={shareTab === "ann" ? "on" : ""}
                    title={t("socialAnnouncements")}
                    onClick={() => { setShareCollapsed(false); setShareTab("ann"); }}
                  >
                    <span className="social-tab-ico" aria-hidden><IconPin size={14} /></span>
                    <span className="social-tab-label">{t("socialAnnouncements")}</span>
                  </button>
                  <button
                    type="button"
                    className={shareTab === "members" ? "on" : ""}
                    title={t("socialMembers")}
                    onClick={() => { setShareCollapsed(false); setShareTab("members"); }}
                  >
                    <span className="social-tab-ico" aria-hidden><IconUser size={14} /></span>
                    <span className="social-tab-label">{t("socialMembers")}</span>
                  </button>
                </>
              )}
              <button
                type="button"
                className={shareTab === "schedule" ? "on" : ""}
                title={t("socialTabSchedule")}
                onClick={() => { setShareCollapsed(false); setShareTab("schedule"); }}
              >
                <span className="social-tab-ico" aria-hidden><IconCalendar size={14} /></span>
                <span className="social-tab-label">{t("socialTabSchedule")}</span>
              </button>
              <button
                type="button"
                className={shareTab === "search" ? "on" : ""}
                title={t("socialTabSearch")}
                onClick={() => { setShareCollapsed(false); setShareTab("search"); }}
              >
                <span className="social-tab-ico" aria-hidden><IconSearch size={14} /></span>
                <span className="social-tab-label">{t("socialTabSearch")}</span>
              </button>
              <button
                type="button"
                className={shareTab === "files" ? "on" : ""}
                title={t("socialTabFiles")}
                onClick={() => { setShareCollapsed(false); setShareTab("files"); }}
              >
                <span className="social-tab-ico" aria-hidden><IconFiles size={14} /></span>
                <span className="social-tab-label">{t("socialTabFiles")}</span>
              </button>
              <button
                type="button"
                className={shareTab === "browser" ? "on" : ""}
                title={t("socialTabBrowser")}
                onClick={() => { setShareCollapsed(false); setShareTab("browser"); }}
              >
                <span className="social-tab-ico" aria-hidden><IconGlobe size={14} /></span>
                <span className="social-tab-label">{t("socialTabBrowser")}</span>
              </button>
            </div>
            <div className="social-share-body">
              {shareTab === "schedule" && (
                <SchedulesPanel refreshKey={scheduleRefresh} compact />
              )}
              {view.kind === "group" && activeGroup && shareTab === "ann" && (
                <div className="social-ann-board">
                  {(activeGroup.announcements || []).length === 0 ? (
                    <p className="social-ann-empty">{t("socialAnnEmpty")}</p>
                  ) : (
                    (activeGroup.announcements || []).map((a) => (
                      <article key={a.id} className="social-ann-card">
                        <div className="social-ann-kicker">{t("socialAnnKicker")}</div>
                        <h3 className="social-ann-title">{a.title}</h3>
                        <p className="social-ann-body">{a.body}</p>
                        <div className="social-ann-foot">
                          <span className="social-ann-meta">
                            {(a.authorName ? a.authorName + " · " : "") + new Date(a.createdAt).toLocaleString()}
                          </span>
                          <button
                            type="button"
                            className="social-ann-drop"
                            onClick={() => dropAnnouncement(activeGroup.id, a.id)}
                          >
                            {t("socialAnnDrop")}
                          </button>
                        </div>
                      </article>
                    ))
                  )}
                  <details className="social-ann-add">
                    <summary>{t("socialAnnAdd")}</summary>
                    <AnnComposer
                      t={t}
                      onAdd={(title, body) => addAnnouncement(activeGroup.id, title, body)}
                    />
                  </details>
                </div>
              )}
              {(shareTab === "search" || shareTab === "files" || shareTab === "browser") && (
                <div className={"social-share-tool" + (shareTab === "browser" ? " social-share-browser" : "")}>
                  {renderCollabTool ? (
                    renderCollabTool(shareTab)
                  ) : (
                    <p className="social-muted">{t("socialSharedEmpty")}</p>
                  )}
                </div>
              )}
              {view.kind === "group" && activeGroup && shareTab === "members" && (
                <div className="social-member-list">
                  {groupMembers(activeGroup).map((a) => {
                    const live = agentLive[a.id];
                    return (
                    <div key={a.id} className="social-member-row-wrap">
                      <button
                        type="button"
                        className="social-member-row"
                        onClick={() => setAgentPanel({ agentId: a.id, groupId: activeGroup.id })}
                      >
                        <AgentAvatar agent={a} size={36} />
                        <span className="social-member-copy">
                          <div className="social-card-title"><span className="social-card-name">{a.name}</span></div>
                          <div className="social-card-sub">{a.title}</div>
                          <div className="social-card-duty">{a.duty || a.blurb}</div>
                          {live && live.phase !== "idle" ? (
                            <div className={"social-live-chip " + (isActiveAgentPhase(live.phase) ? "active" : "")}>
                              {agentLivePhaseLabel(live.phase)}
                            </div>
                          ) : null}
                        </span>
                      </button>
                      <button
                        type="button"
                        className="social-ghost-btn social-tiny social-member-remove"
                        title={t("socialRemoveFromGroup")}
                        onClick={(e) => {
                          e.stopPropagation();
                          removeAgentFromGroup(activeGroup.id, a.id);
                        }}
                      >
                        {t("socialRemoveFromGroup")}
                      </button>
                    </div>
                    );
                  })}
                  <button
                    type="button"
                    className="social-ghost-btn"
                    onClick={() => setView({ kind: "editGroup", groupId: activeGroup.id })}
                  >
                    {t("socialManageGroup")}
                  </button>
                  <label className="social-check" style={{ marginTop: 8 }}>
                    <input
                      type="checkbox"
                      checked={activeGroup.collabMode !== false}
                      onChange={(e) => updateGroupField(activeGroup.id, { collabMode: e.target.checked })}
                    />
                    {t("socialToggleCollab")}
                  </label>
                  <button type="button" className="social-clear-btn" onClick={() => clearThread(groupKey(activeGroup.id))}>
                    <IconTrash size={14} />
                    <span>{t("socialClearChat")}</span>
                  </button>
                </div>
              )}
            </div>
          
            <button
              type="button"
              className="social-share-collapse"
              title={shareCollapsed ? t("socialExpandPanel") : t("socialCollapsePanel")}
              onClick={() => setShareCollapsed((v) => !v)}
            >
              {shareCollapsed ? "»" : "«"}
            </button>
          </aside>
        )}

        <div className="social-chat-pane">
          {view.kind === "dm" && activeAgent && (
            <div className={"social-dm-banner" + (linkedWorkbenchDm ? " synced" : "")}>
              <AgentAvatar agent={activeAgent} />
              <div className="social-dm-banner-text">
                <div className="social-card-title">{activeAgent.name}</div>
                <div className="social-card-sub">{activeAgent.title} · {activeAgent.duty}</div>
              </div>
                {linkedWorkbenchDm && (
                  <span className="social-wb-sync" title={t("socialWorkbenchSyncHint")}>
                    {t("socialWorkbenchSyncChip")}
                  </span>
                )}
              <button
                type="button"
                className="social-link-btn social-new-session-btn"
                onClick={startFreshDm}
                title={
                  linkedWorkbenchDm
                    ? t("socialWorkbenchNoBlankSession")
                    : t("socialDmNewSessionHint")
                }
              >
                {t("socialDmNewSession")}
              </button>
              <button
                type="button"
                className="social-icon-btn social-card-more"
                title={t("socialConfig")}
                onClick={() => setConfigSheet({ kind: "agent", id: activeAgent.id })}
              >
                ···
              </button>
            </div>
          )}

          <div key={`${threadKey || "none"}:${clearTick}`} ref={threadScrollerRef} className="social-thread" onMouseUp={onChatSelect}>
            {messages.map((m) => {
              if (m.role === "system") {
                return (
                  <div
                    key={m.id}
                    className={"social-msg system" + (m.readingStatus ? " social-reading-status" : "")}
                  >
                    {m.readingStatus ? (
                      <span className="social-reading-status-inner">
                        <span>{m.text}</span>
                        <span className="social-reading-dots" aria-hidden>
                          <i /><i /><i />
                        </span>
                      </span>
                    ) : (
                      m.text
                    )}
                  </div>
                );
              }
              if (m.role === "user") {
                return (
                  <div key={m.id} className="social-msg user social-bubble-enter">
                    <div className="social-bubble-wrap">
                      <div className="social-bubble user">
                        {m.quote ? (
                          <blockquote className="social-quote-ref">{m.quote}</blockquote>
                        ) : null}
                        <div className="social-user-text">{m.text}</div>
                        {m.attachments?.length ? (
                          <ul className="social-message-attachments" aria-label="已共享文件">
                            {m.attachments.map((attachment) => (
                              <li key={`${attachment.path}:${attachment.name}`} title={attachment.path}>
                                <span aria-hidden>▣</span>
                                <span>{attachment.name}</span>
                              </li>
                            ))}
                          </ul>
                        ) : null}
                      </div>
                      <div className="social-bubble-meta">
                        <span className="social-bubble-time">{formatMsgTime(m.at)}</span>
                        <button
                          type="button"
                          className="social-bubble-copy"
                          title={t("socialCopyMsg")}
                          aria-label={t("socialCopyMsg")}
                          onClick={() => {
                            void copyBubbleText(
                              (m.quote ? `【引用】\n${m.quote}\n【/引用】\n\n` : "") + (m.text || ""),
                            ).then((ok) => onToast(ok ? t("socialCopied") : t("socialCopyFail")));
                          }}
                        >
                          复制
                        </button>
                      </div>
                    </div>
                  </div>
                );
              }
              const ag = m.agentId ? agentById(agents, m.agentId) : agentByName(agents, m.agentName || "");
              return (
                <div key={m.id} className="social-msg agent social-bubble-enter">
                  <button
                    type="button"
                    className="social-avatar-button"
                    onClick={() => ag && setAgentPanel({ agentId: ag.id, groupId: view.kind === "group" ? view.groupId : view.fromGroupId })}
                    title={ag?.name}
                  >
                    <AgentAvatar
                      agent={{
                        name: ag?.name || m.agentName || "?",
                        color: ag?.color || "#64748b",
                        avatar: ag?.avatar,
                      }}
                      size={36}
                    />
                  </button>
                  <div className="social-bubble-wrap">
                    <div className="social-msg-name">{formatSpeakerLabel(t("assistant"), ag?.name || m.agentName)}</div>
                    <div
                      className={
                        "social-bubble agent" +
                        (m.streaming ? " streaming" : "") +
                        (m.reading ? " reading" : "")
                      }
                    >
                      {m.reading ? (
                        <div className="social-reading" aria-live="polite">
                          <span>{t("socialReadingMsg")}</span>
                          <span className="social-reading-dots" aria-hidden>
                            <i /><i /><i />
                          </span>
                        </div>
                      ) : (
                        <>
                          {(m.reasoning || m.reasoningStreaming) && (
                            <ThinkingBlock content={m.reasoning || ""} streaming={!!m.reasoningStreaming} />
                          )}
                          <MarkdownView
                            content={
                              formatAssistantDisplay(
                                stripToolCallMarkup(stripAgentTags(m.text || "")),
                              ) ||
                              (m.streaming && !m.reasoningStreaming ? "…" : "")
                            }
                            streaming={!!m.streaming && !m.reasoningStreaming}
                            openSandboxOnClick
                            onCtrlClickUrl={(url) => {
                              openSocialBrowserSidebar();
                              onOpenBrowserUrl?.(url);
                            }}
                          />
                          {m.attachments?.length ? (
                            <ul className="social-message-attachments" aria-label="群内共享文件">
                              {m.attachments.map((attachment) => (
                                <li key={`${attachment.path}:${attachment.name}`} title={attachment.path}>
                                  <span aria-hidden>▣</span>
                                  <span>{attachment.name}</span>
                                </li>
                              ))}
                            </ul>
                          ) : null}
                        </>
                      )}
                    </div>
                    {!m.reading && stripAgentTags(m.text || "").trim() ? (
                      <div className="social-rate" role="group" aria-label="给这条回复打分">
                        {[1, 2, 3, 4, 5].map((n) => (
                          <button
                            key={n}
                            type="button"
                            className={"social-rate-star" + ((m.score || 0) >= n ? " on" : "")}
                            aria-label={`${n}分`}
                            title={`${n}分`}
                            onClick={() => threadKey && rateReply(threadKey, m.id, n)}
                          >
                            ★
                          </button>
                        ))}
                      </div>
                    ) : null}
                    {!m.reading ? (
                      <div className="social-bubble-meta">
                        <span className="social-bubble-time">{formatMsgTime(m.at)}</span>
                        <button
                          type="button"
                          className="social-bubble-copy"
                          title={t("socialCopyMsg")}
                          aria-label={t("socialCopyMsg")}
                          onClick={() => {
                            const body = stripAgentTags(m.text || "").trim();
                            void copyBubbleText(body).then((ok) =>
                              onToast(ok ? t("socialCopied") : t("socialCopyFail")),
                            );
                          }}
                        >
                          复制
                        </button>
                      </div>
                    ) : null}
                  </div>
                </div>
              );
            })}
              <div ref={bottomRef} />
            </div>

            {quoteMenu && (
            <div
              className="social-quote-menu"
              style={{ left: quoteMenu.x, top: quoteMenu.y }}
              onMouseDown={(e) => e.preventDefault()}
            >
              <button type="button" onClick={() => applyQuote(quoteMenu.text)}>
                {t("socialQuote")}
              </button>
              <button
                type="button"
                onClick={() => {
                  const selected = quoteMenu.text;
                  void copyBubbleText(selected).then((ok) =>
                    onToast(ok ? t("socialCopied") : t("socialCopyFail")),
                  );
                  clearSelectHighlight();
                  setQuoteMenu(null);
                }}
              >
                {t("socialCopyMsg")}
              </button>
            </div>
          )}
          <div className="social-composer">
            <div className="social-composer-meta">
              {view.kind === "group" && activeGroup ? (
                <div className="social-ctx-row" title={t("socialCtxPerAgentHint")}>
                  {(activeGroup.memberIds || [])
                    .map((id) => agentById(agents, id))
                    .filter(Boolean)
                    .map((ag) => {
                      const u = ctxByAgent[ag!.id] || (ctx.tokens > 0 ? ctx : null);
                      if (!u) {
                        return (
                          <span key={ag!.id} className="social-ctx-chip muted">
                            {ag!.name}
                          </span>
                        );
                      }
                      const pct = u.limit > 0 ? Math.min(100, Math.round((u.tokens / u.limit) * 100)) : 0;
                      const cls = pct >= 90 ? "danger" : pct >= 70 ? "warn" : "";
                      const tok =
                        u.tokens >= 1000
                          ? (u.tokens / 1000).toFixed(u.tokens >= 10000 ? 0 : 1) + "k"
                          : String(u.tokens);
                      const lim = u.limit >= 1000 ? Math.round(u.limit / 1000) + "k" : String(u.limit);
                      return (
                        <span
                          key={ag!.id}
                          className={"social-ctx-chip " + cls}
                          title={[
                            t("socialContextUsage", String(pct), String(u.tokens), String(u.limit)),
                            t("socialContextBudgetHint"),
                            u.windowLimit ? `窗口 ${Math.round(u.windowLimit / 1000)}k` : "",
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        >
                          {ag!.name} {pct}% · {tok}/{lim}
                        </span>
                      );
                    })}
                </div>
              ) : (
                (() => {
                  const agentId = view.kind === "dm" ? view.agentId : "";
                  const u: { tokens: number; limit: number; windowLimit?: number } =
                    (agentId && ctxByAgent[agentId]) || ctx;
                  const pct = u.limit > 0 ? Math.min(100, Math.round((u.tokens / u.limit) * 100)) : 0;
                  const cls = pct >= 90 ? "danger" : pct >= 70 ? "warn" : "";
                  const tok =
                    u.tokens >= 1000
                      ? (u.tokens / 1000).toFixed(u.tokens >= 10000 ? 0 : 1) + "k"
                      : String(u.tokens);
                  const lim = u.limit >= 1000 ? Math.round(u.limit / 1000) + "k" : String(u.limit);
                  return (
                    <span
                      className={"social-ctx-chip " + cls}
                      title={[
                            t("socialContextUsage", String(pct), String(u.tokens), String(u.limit)),
                            t("socialContextBudgetHint"),
                            u.windowLimit ? `窗口 ${Math.round(u.windowLimit / 1000)}k` : "",
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                    >
                      {t("socialCtxCompact", String(pct), tok, lim)}
                    </span>
                  );
                })()
              )}
              {view.kind === "dm" && (
                <button type="button" className="social-link-btn social-new-session-btn" onClick={startFreshDm}
                  title={linkedWorkbenchDm ? t("socialWorkbenchNoBlankSession") : t("socialDmNewSessionHint")}>
                  {t("socialDmNewSession")}
                </button>
              )}
              {threadKey && (
                <button
                  type="button"
                  className="social-clear-btn"
                  onClick={() => clearThread(threadKey)}
                  title={view.kind === "dm" ? t("socialClearDmHint") : t("socialClearGroupHint")}
                >
                  <IconTrash size={14} />
                  <span>{view.kind === "dm" ? t("socialClearDm") : t("socialClearChat")}</span>
                </button>
              )}
            </div>

            {mentionOpen && view.kind === "group" && (
              <div className="social-mention-menu">
                {mentionCandidates.map((a) => (
                  <button key={a.id} type="button" onClick={() => insertMention(a)}>
                    <span className="social-avatar-sm" style={{ background: a.color }}>
                      {a.name.slice(0, 1)}
                    </span>
                    {a.name}
                    <span className="social-muted">{a.title}</span>
                  </button>
                ))}
              </div>
            )}
            {quote && (
              <div className="social-quote-preview">
                <div className="social-quote-preview-text">{quote}</div>
                <button type="button" className="social-attach-remove" onClick={() => setQuote(null)} title={t("socialQuoteClear")}>
                  ×
                </button>
              </div>
            )}
            {attachments.length > 0 && (
              <ul className="social-attach-list">
                {attachments.map((a) => (
                  <li key={a.id} className="social-attach-chip">
                    <span className="social-attach-name" title={a.path}>
                      {a.name}
                    </span>
                    <button
                      type="button"
                      className="social-attach-remove"
                      title={t("attachRemove")}
                      onClick={() => setAttachments((prev) => prev.filter((x) => x.id !== a.id))}
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className="social-composer-box">
              <input
                ref={attachInputRef}
                type="file"
                multiple
                hidden
                onChange={(e) => void addAttachments(e.target.files)}
              />
              <textarea
                ref={composerRef}
                className="social-composer-input"
                value={input}
                onChange={(e) => onInputChange(e.target.value)}
                placeholder={
                  view.kind === "dm"
                    ? t("socialDmPlaceholder", activeAgent?.name || "")
                    : t("socialGroupPlaceholder")
                }
                rows={1}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send();
                  }
                }}
                disabled={threadBusy && view.kind !== "group"}
              />
              <div className="social-composer-tools">
                {browserCard && browserCard.threadKey === threadKey && (shareCollapsed || shareTab !== "browser") ? (
                  <button
                    type="button"
                    className="social-tool-btn social-browser-dock"
                    title={t("socialTabBrowser")}
                    aria-label={t("socialTabBrowser")}
                    onClick={() => openSocialBrowserSidebar()}
                  >
                    <IconGlobe size={18} />
                  </button>
                ) : null}
                <button
                  type="button"
                  className="social-tool-btn"
                  title={t("socialAttachHint")}
                  disabled={threadBusy || attachBusy || !chatWorkspacePath}
                  onClick={() => attachInputRef.current?.click()}
                >
                  {attachBusy ? <span className="social-spin">…</span> : <IconPaperclip size={18} />}
                </button>
                {threadBusy && !(view.kind === "group" && (input.trim() || attachments.length > 0)) ? (
                  <button
                    type="button"
                    className="social-stop-fab"
                    onClick={stop}
                    title={t("socialStop")}
                    aria-label={t("socialStop")}
                  >
                    <span className="social-stop-fab-icon" aria-hidden />
                  </button>
                ) : (
                  <button
                    type="button"
                    className="social-send-fab"
                    disabled={!input.trim() && attachments.length === 0}
                    onClick={() => void send()}
                    title={t("socialSend")}
                  >
                    <IconSend size={16} />
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>


      {agentPanel && (() => {
        const panAgent = agentById(store.agents, agentPanel.agentId);
        if (!panAgent) return null;
        const panGroupId = agentPanel.groupId;
        const panBoard = panGroupId ? getGroupBoard(store, panGroupId) : null;
        const panTasks = (panBoard?.tasks || []).filter(
          (t) =>
            t.status === "open" ||
            t.ownerId === panAgent.id ||
            t.ownerName === panAgent.name,
        );
        const openTasks = panTasks.filter((t) => t.status === "open");
        const doneTasks = panTasks.filter((t) => t.status === "done");
        const panReplies = panGroupId
          ? (store.threads[groupKey(panGroupId)] || [])
              .filter((m) => m.role === "agent" && m.agentId === panAgent.id && String(m.text || "").trim())
              .slice(-6)
          : [];
        const panRoundEntries = panBoard
          ? [panBoard.workingRound, panBoard.lastRound, ...(panBoard.recentRounds || [])]
              .filter((round, index, list): round is NonNullable<typeof round> => !!round && list.findIndex((x) => x?.id === round.id) === index)
              .flatMap((round) => round.entries.filter((entry) => entry.agentId === panAgent.id))
              .slice(0, 6)
          : [];
        return (
          <div
            className="social-sheet-mask social-sheet-mask-center"
            onClick={() => setAgentPanel(null)}
          >
            <div
              className="social-sheet social-agent-panel"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="social-sheet-head">
                <div className="social-agent-panel-title">
                  <AgentAvatar agent={panAgent} size={36} />
                  <div>
                    <strong>{panAgent.name}</strong>
                    <div className="social-muted">{panAgent.title}</div>
                  </div>
                </div>
                <button type="button" className="social-icon-btn" onClick={() => setAgentPanel(null)}>
                  <IconX size={16} />
                </button>
              </div>
              <div className="social-agent-panel-tabs">
                <button
                  type="button"
                  className={agentPanelTab === "actions" ? "on" : ""}
                  onClick={() => setAgentPanelTab("actions")}
                >
                  {t("socialAgentActions")}
                </button>
                <button
                  type="button"
                  className={agentPanelTab === "tasks" ? "on" : ""}
                  onClick={() => setAgentPanelTab("tasks")}
                >
                  {t("socialAgentTasks")}
                  {openTasks.length ? ` (${openTasks.length})` : ""}
                </button>
              </div>
              {agentPanelTab === "actions" ? (
                <div className="social-agent-panel-actions">
                  <button
                    type="button"
                    className="social-agent-action"
                    onClick={() => {
                      const gid = agentPanel.groupId;
                      setAgentPanel(null);
                      openDm(panAgent.id, gid);
                    }}
                  >
                    <IconChat size={18} />
                    <span>{t("socialDm")}</span>
                  </button>
                  <button
                    type="button"
                    className="social-agent-action"
                    onClick={() => setAgentPanelTab("tasks")}
                  >
                    <IconCheck size={18} />
                    <span>{t("socialTaskDetails")}</span>
                  </button>
                  {agentPanel.groupId ? (
                    <button
                      type="button"
                      className="social-agent-action"
                      onClick={() => {
                        const gid = agentPanel.groupId!;
                        removeAgentFromGroup(gid, panAgent.id);
                        setAgentPanel(null);
                      }}
                    >
                      <span>{t("socialRemoveFromGroup")}</span>
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="social-agent-action danger"
                    onClick={() => deleteAgent(panAgent.id)}
                  >
                    <span>{t("socialDeleteAgent")}</span>
                  </button>
                </div>
              ) : (
                <div className="social-agent-panel-tasks">
                  {(() => {
                    const live = agentLive[panAgent.id];
                    if (!live || (live.phase === "idle" && live.tools.length === 0 && !(live.activity || []).length)) return null;
                    return (
                      <div className="social-agent-live">
                        <div className="social-agent-task-h">
                          {agentLivePhaseLabel(live.phase)}
                          {isActiveAgentPhase(live.phase) ? " · …" : ""}
                        </div>
                        {live.summary ? (
                          <p className="social-muted social-agent-live-summary">{live.summary}</p>
                        ) : null}
                        {live.tools.length > 0 ? (
                          <ul className="social-agent-task-list">
                            {live.tools.map((tool) => (
                              <li key={tool.id}>
                                <span>
                                  {tool.status === "running" ? "⚡ " : tool.status === "error" ? "✗ " : "✓ "}
                                  {tool.name}
                                </span>
                                {tool.detail ? <em>{tool.detail}</em> : null}
                              </li>
                            ))}
                          </ul>
                        ) : isActiveAgentPhase(live.phase) ? (
                          <p className="social-muted">{isActiveAgentPhase(live.phase) ? "…" : ""}</p>
                        ) : null}
                        {(live.activity || []).length > 0 ? (
                          <>
                            <div className="social-agent-task-h">本轮协作记录</div>
                            <ul className="social-agent-task-list social-agent-activity-list">
                              {(live.activity || []).map((item) => (
                                <li key={item.id} className={"social-agent-activity " + item.kind}>
                                  <span>{agentActivityLabel(item.kind)}</span>
                                  <em>{item.detail}</em>
                                </li>
                              ))}
                            </ul>
                          </>
                        ) : null}
                      </div>
                    );
                  })()}

                  {!panGroupId ? (
                    <p className="social-muted">{t("socialTasksNeedGroup")}</p>
                  ) : openTasks.length === 0 && doneTasks.length === 0 && panRoundEntries.length === 0 && panReplies.length === 0 ? (
                    <p className="social-muted">{t("socialTasksEmpty")}</p>
                  ) : (
                    <>
                      {openTasks.length > 0 && (
                        <>
                          <div className="social-agent-task-h">{t("socialTasksOpen")}</div>
                          <ul className="social-agent-task-list">
                            {openTasks.map((task) => (
                              <li key={task.id}>
                                <span>{task.text}</span>
                                {task.ownerName ? <em>@{task.ownerName}</em> : null}
                              </li>
                            ))}
                          </ul>
                        </>
                      )}
                      {doneTasks.length > 0 && (
                        <>
                          <div className="social-agent-task-h">{t("socialTasksDone")}</div>
                          <ul className="social-agent-task-list done">
                            {doneTasks.slice(0, 8).map((task) => (
                              <li key={task.id}>
                                <span>{task.text}</span>
                              </li>
                            ))}
                          </ul>
                        </>
                      )}
                      {panRoundEntries.length > 0 && (
                        <>
                          <div className="social-agent-task-h">本轮判断与处理</div>
                          <ul className="social-agent-task-list">
                            {panRoundEntries.map((entry, index) => (
                              <li key={`${entry.at}-${index}`}>
                                <span>{entry.action === "reply" ? "已回应" : "已阅后跳过"}</span>
                                {entry.summary ? <em>{entry.summary}</em> : null}
                              </li>
                            ))}
                          </ul>
                        </>
                      )}
                      {panReplies.length > 0 && (
                        <>
                          <div className="social-agent-task-h">最近群内输出</div>
                          <ul className="social-agent-task-list">
                            {panReplies.map((reply) => (
                              <li key={reply.id}>
                                <span>{summarizeReply(reply.text, 160)}</span>
                                <em>{formatMsgTime(reply.at)}</em>
                              </li>
                            ))}
                          </ul>
                        </>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
          </div>
        );
      })()}

    </section>
  );
}

function AgentForm({
  t,
  initial,
  onCancel,
  onSave,
  embedded = false,
}: {
  t: Props["t"];
  initial: SocialAgent | null;
  onCancel: () => void;
  onSave: (d: {
    id?: string;
    name: string;
    title: string;
    blurb: string;
    duty: string;
    persona: string;
  }) => void;
  embedded?: boolean;
}) {
  const [name, setName] = useState(initial?.name || "");
  const [title, setTitle] = useState(initial?.title || "");
  const [blurb, setBlurb] = useState(initial?.blurb || "");
  const [duty, setDuty] = useState(initial?.duty || "");
  const [persona, setPersona] = useState(initial?.persona || "");
    const [polishing, setPolishing] = useState(false);
  const [personaBeforePolish, setPersonaBeforePolish] = useState<string | null>(null);
  const polishPersona = async () => {
    const seed = (persona || "").trim() || (duty || "").trim() || (blurb || "").trim() || (name || "").trim();
    if (!seed || polishing) return;
    const prevPersona = persona;
    setPersonaBeforePolish(prevPersona);
    setPolishing(true);
    // Overwrite the field immediately so streaming is visible.
    setPersona("");
    let out = "";
    const cleanFinal = (raw: string) =>
      String(raw || "")
        .replace(/```[\s\S]*?```/g, (m) => m.replace(/```\w*/g, "").replace(/```/g, ""))
        .replace(/^DECISION\s*[:：].*$/gim, "")
        .replace(/^(silent|reply)\s*$/gim, "")
        .trim();
    try {
      const prompt =
        "你是人设文案助手。根据用户提供的智能体信息，写出一段更丰富、可直接作为系统人设的中文提示词。"
        + "保留原意与身份，补充说话风格、原则、边界与协作方式；不要用 markdown，不要标题，不要输出 DECISION，直接输出人设正文。\n\n"
        + `名称：${name || "未命名"}\n头衔：${title || ""}\n职责：${duty || ""}\n简介：${blurb || ""}\n原稿：\n${seed}`;
      await streamChat(
        prompt,
        "polish-" + Date.now(),
        {
          onEvent: (ev: any) => {
            const typ = String(ev?.type || "");
            if (typ === "assistant_delta") {
              const piece = String(ev?.data?.chunk ?? ev?.data?.text ?? ev?.data?.delta ?? "");
              const reset = Boolean(ev?.data?.reset);
              const discard = Boolean(ev?.data?.discard);
              if (discard) return;
              if (reset) out = "";
              if (piece) out += piece;
              // Live overwrite — don't trim mid-stream (avoids wiping partial text).
              setPersona(out);
            } else if (typ === "error") {
              setPersona(prevPersona);
            }
          },
          onFinal: (finalText: string) => {
            const merged = String(finalText || "").trim() || out;
            const cleaned = cleanFinal(merged);
            setPersona(cleaned || merged || prevPersona);
            out = cleaned || merged;
          },
          onError: () => {
            setPersona(prevPersona);
          },
        },
        undefined,
        "agent",
        undefined,
        undefined,
        true,
        true,
      );
      const finished = cleanFinal(out) || out.trim();
      if (finished) setPersona(finished);
    } catch {
      setPersona(prevPersona);
    } finally {
      setPolishing(false);
    }
  };
  const undoPolish = () => {
    if (personaBeforePolish === null) return;
    setPersona(personaBeforePolish);
    setPersonaBeforePolish(null);
  };
  return (
    <form
      className={"social-form social-create-form" + (embedded ? " embedded" : "")}
      onSubmit={(e) => {
        e.preventDefault();
        onSave({ id: initial?.id, name, title, blurb, duty, persona });
      }}
    >
      <div className="social-create-fields">
        <label className="social-field">
          <span>{t("socialFieldName")}</span>
          <input value={name} onChange={(e) => setName(e.target.value)} required placeholder={t("socialFieldName")} />
        </label>
        <label className="social-field">
          <span>{t("socialFieldTitle")}</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={t("socialFieldTitle")} />
        </label>
        <label className="social-field social-field-span">
          <span>{t("socialFieldDuty")}</span>
          <textarea value={duty} onChange={(e) => setDuty(e.target.value)} rows={3} placeholder={t("socialFieldDuty")} />
        </label>
        <label className="social-field">
          <span>{t("socialFieldBlurb")}</span>
          <input value={blurb} onChange={(e) => setBlurb(e.target.value)} placeholder={t("socialFieldBlurb")} />
        </label>
        <label className="social-field social-field-span">
          <span>{t("socialFieldPersona")}</span>
          <div className={"social-persona-box" + (polishing ? " polishing" : "")}>
            <textarea
              value={persona}
              onChange={(e) => setPersona(e.target.value)}
              rows={5}
              placeholder={polishing ? "正在润色…" : t("socialFieldPersona")}
              disabled={polishing}
            />
            <div className="social-persona-box-actions">
              {personaBeforePolish !== null ? (
                <button
                  type="button"
                  className="social-persona-polish undo"
                  title="撤回润色"
                  aria-label="撤回润色"
                  disabled={polishing}
                  onClick={undoPolish}
                >
                  撤回
                </button>
              ) : null}
              <button
                type="button"
                className="social-persona-polish"
                title={t("socialPersonaPolishHint")}
                aria-label={t("socialPersonaPolish")}
                disabled={polishing}
                onClick={() => void polishPersona()}
              >
                {polishing ? "…" : "润色"}
              </button>
            </div>
          </div>
        </label>
      </div>
      <div className="social-form-actions">
        <button type="button" className="social-ghost-btn" onClick={onCancel}>
          {t("socialCancel")}
        </button>
        <button type="submit" className="social-primary-btn">
          {t("socialSave")}
        </button>
      </div>
    </form>
  );
}

function GroupForm({
  t,
  agents,
  initial,
  onCancel,
  onSave,
  embedded = false,
}: {
  t: Props["t"];
  agents: SocialAgent[];
  initial: SocialGroup | null;
  onCancel: () => void;
  onSave: (d: { id?: string; name: string; topic: string; memberIds: string[] }) => void;
  embedded?: boolean;
}) {
  const [name, setName] = useState(initial?.name || "");
  const [topic, setTopic] = useState(initial?.topic || "");
  const [memberIds, setMemberIds] = useState<string[]>(
    initial?.memberIds || agents.map((a) => a.id),
  );
  const allSelected = agents.length > 0 && memberIds.length === agents.length;
  const toggle = (id: string) => {
    setMemberIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };
  const toggleAll = () => {
    setMemberIds(allSelected ? [] : agents.map((a) => a.id));
  };
  return (
    <form
      className={"social-form social-create-form" + (embedded ? " embedded" : "")}
      onSubmit={(e) => {
        e.preventDefault();
        onSave({ id: initial?.id, name, topic, memberIds });
      }}
    >
      <div className="social-create-fields">
        <label className="social-field">
          <span>{t("socialFieldGroupName")}</span>
          <input value={name} onChange={(e) => setName(e.target.value)} required placeholder={t("socialFieldGroupName")} />
        </label>
        <label className="social-field">
          <span>{t("socialFieldTopic")}</span>
          <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder={t("socialFieldTopic")} />
        </label>
      </div>
      <fieldset className="social-member-pick">
        <div className="social-member-pick-head">
          <legend>{t("socialPickMembers")}</legend>
          <div className="social-member-pick-actions">
            <span className="social-muted">{t("socialSelectedCount", String(memberIds.length))}</span>
            <button type="button" className="social-link-btn" onClick={toggleAll}>
              {allSelected ? t("socialDeselectAll") : t("socialSelectAll")}
            </button>
          </div>
        </div>
        {agents.length === 0 ? (
          <p className="social-muted">{t("socialListEmpty")}</p>
        ) : (
          <div className="social-member-card-grid">
            {agents.map((a) => {
              const on = memberIds.includes(a.id);
              return (
                <button
                  key={a.id}
                  type="button"
                  className={"social-member-card" + (on ? " selected" : "")}
                  onClick={() => toggle(a.id)}
                  aria-pressed={on}
                >
                  <AgentAvatar agent={a} size={40} />
                  <span className="social-member-card-text">
                    <span className="social-member-name">{a.name}</span>
                    <span className="social-member-title">{a.title || a.duty || a.blurb}</span>
                  </span>
                  <span className={"social-member-tick" + (on ? " on" : "")} aria-hidden>
                    {on ? "✓" : ""}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </fieldset>
      <div className="social-form-actions">
        <button type="button" className="social-ghost-btn" onClick={onCancel}>
          {t("socialCancel")}
        </button>
        <button type="submit" className="social-primary-btn">
          {t("socialSave")}
        </button>
      </div>
    </form>
  );
}

function AnnComposer({
  t,
  onAdd,
}: {
  t: Props["t"];
  onAdd: (title: string, body: string) => void;
}) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  return (
    <div className="social-mini-form">
      <input
        placeholder={t("socialAnnTitlePh")}
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
      <textarea
        placeholder={t("socialAnnBodyPh")}
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={2}
      />
      <button
        type="button"
        className="social-ghost-btn"
        onClick={() => {
          onAdd(title, body);
          setTitle("");
          setBody("");
        }}
      >
        {t("socialPublishAnn")}
      </button>
    </div>
  );
}

function readImageAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    if (!file.type.startsWith("image/")) {
      reject(new Error("not-image"));
      return;
    }
    if (file.size > 2.5 * 1024 * 1024) {
      reject(new Error("too-large"));
      return;
    }
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("read-fail"));
    reader.readAsDataURL(file);
  });
}

function ConfigSheet({
  t,
  target,
  agent,
  group,
  allAgents,
  folders,
  workspaces,
  onClose,
  onSaveAgent,
  onSaveGroup,
  onClearChat,
  onDeleteAgent,
  onDeleteGroup,
  onRemoveFromGroup,
}: {
  t: Props["t"];
  target: { kind: "agent"; id: string } | { kind: "group"; id: string };
  agent: SocialAgent | null;
  group: SocialGroup | null;
  allAgents: SocialAgent[];
  folders: SocialFolder[];
  workspaces: { path: string; name: string; id?: string }[];
  onClose: () => void;
  onSaveAgent: (id: string, patch: Partial<SocialAgent>) => void;
  onSaveGroup: (id: string, patch: Partial<SocialGroup>) => void;
  onClearChat: (key: string) => void;
  onDeleteAgent?: (id: string) => void;
  onDeleteGroup?: (id: string) => void;
  onRemoveFromGroup?: (groupId: string, agentId: string) => void;
}) {
  const isAgent = target.kind === "agent";
  const [name, setName] = useState(isAgent ? agent?.name || "" : group?.name || "");
  const [title, setTitle] = useState(agent?.title || "");
  const [remark, setRemark] = useState((isAgent ? agent?.remark : group?.remark) || "");
  const [location, setLocation] = useState((isAgent ? agent?.location : group?.location) || "");
  const [folderId, setFolderId] = useState((isAgent ? agent?.folderId : group?.folderId) || "");
  const [workspace, setWorkspace] = useState((isAgent ? agent?.workspace : group?.workspace) || "");
  const [duty, setDuty] = useState(agent?.duty || "");
  const [blurb, setBlurb] = useState(agent?.blurb || "");
  const [persona, setPersona] = useState(agent?.persona || "");
    const [polishing, setPolishing] = useState(false);
  const [personaBeforePolish, setPersonaBeforePolish] = useState<string | null>(null);
  const polishPersona = async () => {
    const seed = (persona || "").trim() || (duty || "").trim() || (blurb || "").trim() || (name || "").trim();
    if (!seed || polishing) return;
    const prevPersona = persona;
    setPersonaBeforePolish(prevPersona);
    setPolishing(true);
    // Overwrite the field immediately so streaming is visible.
    setPersona("");
    let out = "";
    const cleanFinal = (raw: string) =>
      String(raw || "")
        .replace(/```[\s\S]*?```/g, (m) => m.replace(/```\w*/g, "").replace(/```/g, ""))
        .replace(/^DECISION\s*[:：].*$/gim, "")
        .replace(/^(silent|reply)\s*$/gim, "")
        .trim();
    try {
      const prompt =
        "你是人设文案助手。根据用户提供的智能体信息，写出一段更丰富、可直接作为系统人设的中文提示词。"
        + "保留原意与身份，补充说话风格、原则、边界与协作方式；不要用 markdown，不要标题，不要输出 DECISION，直接输出人设正文。\n\n"
        + `名称：${name || "未命名"}\n头衔：${title || ""}\n职责：${duty || ""}\n简介：${blurb || ""}\n原稿：\n${seed}`;
      await streamChat(
        prompt,
        "polish-" + Date.now(),
        {
          onEvent: (ev: any) => {
            const typ = String(ev?.type || "");
            if (typ === "assistant_delta") {
              const piece = String(ev?.data?.chunk ?? ev?.data?.text ?? ev?.data?.delta ?? "");
              const reset = Boolean(ev?.data?.reset);
              const discard = Boolean(ev?.data?.discard);
              if (discard) return;
              if (reset) out = "";
              if (piece) out += piece;
              // Live overwrite — don't trim mid-stream (avoids wiping partial text).
              setPersona(out);
            } else if (typ === "error") {
              setPersona(prevPersona);
            }
          },
          onFinal: (finalText: string) => {
            const merged = String(finalText || "").trim() || out;
            const cleaned = cleanFinal(merged);
            setPersona(cleaned || merged || prevPersona);
            out = cleaned || merged;
          },
          onError: () => {
            setPersona(prevPersona);
          },
        },
        undefined,
        "agent",
        undefined,
        undefined,
        true,
        true,
      );
      const finished = cleanFinal(out) || out.trim();
      if (finished) setPersona(finished);
    } catch {
      setPersona(prevPersona);
    } finally {
      setPolishing(false);
    }
  };
  const undoPolish = () => {
    if (personaBeforePolish === null) return;
    setPersona(personaBeforePolish);
    setPersonaBeforePolish(null);
  };
  const [topic, setTopic] = useState(group?.topic || "");
  const [collabMode, setCollabMode] = useState(group?.collabMode !== false);
  const [avatar, setAvatar] = useState((isAgent ? agent?.avatar : group?.avatar) || "");
  const [color, setColor] = useState(agent?.color || "#2563eb");
  const fileRef = useRef<HTMLInputElement | null>(null);

  const onPickAvatar = async (file: File | null) => {
    if (!file) return;
    try {
      const url = await readImageAsDataUrl(file);
      setAvatar(url);
    } catch {
      /* ignore */
    }
  };

  const save = () => {
    if (isAgent && agent) {
      onSaveAgent(agent.id, {
        name: name.trim() || agent.name,
        title: title.trim() || agent.title,
        remark: remark.trim(),
        location: location.trim(),
        folderId: folderId.trim(),
        workspace: workspace.trim(),
        duty: duty.trim() || agent.duty,
        blurb: blurb.trim() || agent.blurb,
        persona: persona.trim() || agent.persona,
        avatar,
        color,
      });
    } else if (group) {
      onSaveGroup(group.id, {
        name: name.trim() || group.name,
        topic: topic.trim(),
        remark: remark.trim(),
        location: location.trim(),
        folderId: folderId.trim(),
        workspace: workspace.trim(),
        collabMode,
        avatar,
      });
    }
  };

  return (
    <div className="social-sheet-mask social-sheet-mask-center" onClick={onClose}>
      <div className="social-sheet social-sheet-landscape" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="social-sheet-head">
          <button type="button" className="social-ghost-btn" onClick={onClose}>
            {t("socialCancel")}
          </button>
          <strong>{isAgent ? t("socialAgentConfig") : t("socialGroupConfig")}</strong>
          <button type="button" className="social-primary-btn social-tiny" onClick={save}>
            {t("socialSave")}
          </button>
        </div>

        <div className="social-sheet-body">
        <div className="social-sheet-avatar-block">
          <button type="button" className="social-sheet-avatar-btn" onClick={() => fileRef.current?.click()}>
            {avatar ? (
              <img src={avatar} alt="" />
            ) : isAgent ? (
              <span style={{ background: color }}>{(name || "?").slice(0, 1)}</span>
            ) : (
              <span className="social-avatar-group">{(name || "?").slice(0, 1)}</span>
            )}
            <em>{t("socialChangeAvatar")}</em>
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            className="social-avatar-file-input"
            tabIndex={-1}
            aria-hidden="true"
            onChange={(e) => {
              const input = e.currentTarget;
              // Reset after each selection so choosing the same picture again
              // still emits a change event.  `hidden` inputs are not reliably
              // clickable from Electron on every platform, hence the
              // visually-hidden (but mounted) input class instead.
              void onPickAvatar(input.files?.[0] || null).finally(() => {
                input.value = "";
              });
            }}
          />
          {avatar && (
            <button type="button" className="social-ghost-btn social-tiny" onClick={() => setAvatar("")}>
              {t("socialRemoveAvatar")}
            </button>
          )}
        </div>

        <div className="social-sheet-fields">
        <label className="social-sheet-field">
          <span>{t("socialFieldName")}</span>
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        {isAgent && (
          <label className="social-sheet-field">
            <span>{t("socialFieldTitle")}</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
        )}
        <label className="social-sheet-field">
          <span>{t("socialRemarkPh")}</span>
          <input value={remark} onChange={(e) => setRemark(e.target.value)} placeholder={t("socialRemarkPh")} />
        </label>
        <label className="social-sheet-field">
          <span>{t("socialLocationPh")}</span>
          <input value={location} onChange={(e) => setLocation(e.target.value)} placeholder={t("socialLocationPh")} />
        </label>
        <label className="social-sheet-field">
          <span>{t("socialFieldFolder")}</span>
          <select value={folderId} onChange={(e) => setFolderId(e.target.value)}>
            <option value="">{t("socialFolderNone")}</option>
            {folders.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
        </label>
        <label className="social-sheet-field">
          <span>{t("socialFieldWorkspace")}</span>
          <select value={workspace} onChange={(e) => setWorkspace(e.target.value)}>
            <option value="">{t("socialWorkspaceUnbound")}</option>
            {workspaces.map((w) => (
              <option key={w.path} value={w.path}>
                {w.name || w.path}
              </option>
            ))}
            {workspace && !workspaces.some((w) => w.path === workspace) ? (
              <option value={workspace}>{workspace}</option>
            ) : null}
          </select>
        </label>
        {isAgent ? (
          <>
            <label className="social-sheet-field">
              <span>{t("socialFieldDuty")}</span>
              <textarea value={duty} onChange={(e) => setDuty(e.target.value)} rows={3} />
            </label>
            <label className="social-sheet-field">
              <span>{t("socialFieldBlurb")}</span>
              <input value={blurb} onChange={(e) => setBlurb(e.target.value)} />
            </label>
            <label className="social-sheet-field">
              <span>{t("socialFieldPersona")}</span>
              <div className={"social-persona-box" + (polishing ? " polishing" : "")}>
                <textarea
                  value={persona}
                  onChange={(e) => setPersona(e.target.value)}
                  rows={4}
                  placeholder={polishing ? "正在润色…" : undefined}
                  disabled={polishing}
                />
                <div className="social-persona-box-actions">
                  {personaBeforePolish !== null ? (
                    <button
                      type="button"
                      className="social-persona-polish undo"
                      title="撤回润色"
                      aria-label="撤回润色"
                      disabled={polishing}
                      onClick={undoPolish}
                    >
                      撤回
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="social-persona-polish"
                    title={t("socialPersonaPolishHint")}
                    aria-label={t("socialPersonaPolish")}
                    disabled={polishing}
                    onClick={() => void polishPersona()}
                  >
                    {polishing ? "…" : "润色"}
                  </button>
                </div>
              </div>
            </label>
            {agent && (
              <div className="social-chip-row" style={{ padding: "0 16px 8px" }}>
                {(agent.skills || []).map((sk) => (
                  <span key={sk} className="social-chip">{sk}</span>
                ))}
                {(agent.tools || []).map((tool) => (
                  <span key={tool} className="social-chip tool">{tool}</span>
                ))}
              </div>
            )}
          </>
        ) : (
          <>
            <label className="social-sheet-field">
              <span>{t("socialFieldTopic")}</span>
              <input value={topic} onChange={(e) => setTopic(e.target.value)} />
            </label>
            <label className="social-check" style={{ padding: "0 16px 12px" }}>
              <input type="checkbox" checked={collabMode} onChange={(e) => setCollabMode(e.target.checked)} />
              {t("socialToggleCollab")}
            </label>
            {group && (
              <div className="social-sheet-members">
                <div className="social-muted">{t("socialMembers")}</div>
                {group.memberIds.map((id) => {
                  const a = allAgents.find((x) => x.id === id);
                  if (!a) return null;
                  return (
                    <div key={id} className="social-sheet-member">
                      <AgentAvatar agent={a} size={28} className="social-avatar-sm" />
                      <span className="social-sheet-member-name">{a.name}</span>
                      <span className="social-sheet-member-title">{a.title}</span>
                      <button
                        type="button"
                        className="social-ghost-btn social-tiny"
                        title={t("socialRemoveFromGroup")}
                        onClick={() => onRemoveFromGroup?.(group.id, id)}
                      >
                        {t("socialRemoveFromGroup")}
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}

        </div>
        </div>
        <div className="social-sheet-footer">
          <button
            type="button"
            className="social-clear-btn"
            onClick={() =>
              onClearChat(isAgent ? dmKey(target.id) : groupKey(target.id))
            }
          >
            {t("socialClearChat")}
          </button>
          {isAgent && onDeleteAgent ? (
            <button
              type="button"
              className="social-danger-btn"
              onClick={() => onDeleteAgent(target.id)}
            >
              {t("socialDeleteAgent")}
            </button>
          ) : null}
          {!isAgent && onDeleteGroup ? (
            <button
              type="button"
              className="social-danger-btn"
              onClick={() => onDeleteGroup(target.id)}
            >
              {t("socialDeleteGroup")}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
