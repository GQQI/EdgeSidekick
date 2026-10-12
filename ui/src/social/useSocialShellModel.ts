// @ts-nocheck
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
import { SchedulesPanel } from "../components/SchedulesPanel";
import { ThinkingBlock } from "../components/ThinkingBlock";
import { IconChat, IconCheck, IconFiles, IconX, IconPaperclip, IconSearch, IconSend,
  IconCalendar,
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
import { BrowserGlyph, SocialBrowserCard } from "./SocialBrowserCard";
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

import {
  type SocialAttachment,
  type AgentLivePhase,
  type AgentActivityKind,
  type AgentActivity,
  type AgentLiveState,
  isActiveAgentPhase,
  agentLivePhaseLabel,
  agentActivityLabel,
} from "./socialLiveState";
import { AgentForm, GroupForm, AnnComposer, ConfigSheet, readImageAsDataUrl } from "./SocialForms";
import { createSocialSend } from "./socialSend";


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
  /** Open URL in workbench browser sandbox (and social browser modal). */
  onOpenBrowserUrl?: (url: string) => void;
  /** Close workbench/native browser opened via onOpenBrowserUrl. */
  onCloseBrowserUrl?: () => void;
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


/* eslint-disable @typescript-eslint/no-explicit-any */
export function useSocialShellModel(props: Record<string, any>) {
  const {
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
  browserHeld = false,
  forcedView,
  embedded = false,
  } = props;

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
  const [shareTab, setShareTab] = useState<"ann" | "members" | "search" | "files" | "schedule">("schedule");
  const [scheduleRefresh, setScheduleRefresh] = useState(0);

  const [browserOpen, setBrowserOpen] = useState(false);
  const [browserMini, setBrowserMini] = useState(false);
  const [browserFull, setBrowserFull] = useState(false);
  const browserHoldRef = useRef(false);
  const [browserPos, setBrowserPos] = useState({ x: 72, y: 72, w: 640, h: 440 });
  const browserDrag = useRef<{
    mode: "move" | "size";
    sx: number;
    sy: number;
    ox: number;
    oy: number;
    ow: number;
    oh: number;
  } | null>(null);
  useEffect(() => {
    const move = (e: PointerEvent) => {
      const d = browserDrag.current;
      if (!d) return;
      if (d.mode === "move") {
        setBrowserPos((p) => ({
          ...p,
          x: Math.max(8, d.ox + e.clientX - d.sx),
          y: Math.max(8, d.oy + e.clientY - d.sy),
        }));
      } else {
        setBrowserPos((p) => ({
          ...p,
          w: Math.max(320, d.ow + e.clientX - d.sx),
          h: Math.max(240, d.oh + e.clientY - d.sy),
        }));
      }
    };
    const up = () => {
      browserDrag.current = null;
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, []);
  const [socialPreviewUrl, setSocialPreviewUrl] = useState("");
  /** User dismissed the browser this turn — don't auto-reopen on later tool_start. */
  const browserUserClosedRef = useRef(false);
  /**
   * Browser preview bubble rendered in the message flow: mirrors the agent's
   * (headless) browser as a live screenshot while the turn runs. Click expands
   * the fullscreen browser sheet. Cleared when the thread switches.
   */
  const [browserCard, setBrowserCard] = useState<{
    url: string;
    threadKey: string;
    agent?: { name: string; color: string; avatar?: string };
  } | null>(null);
  // The browser preview renders as an in-flow message bubble — keep it in
  // view when it appears (same pin-to-bottom rule as new messages).
  useEffect(() => {
    if (!browserCard) return;
    requestAnimationFrame(() => {
      const box = threadScrollerRef.current;
      if (!box) return;
      const dist = box.scrollHeight - box.scrollTop - box.clientHeight;
      if (dist > 120) return;
      box.scrollTop = box.scrollHeight;
    });
  }, [browserCard]);
  const closeBrowserSheet = () => {
    // Only dismiss the fullscreen sheet. Keep the sandbox session so the
    // in-bubble preview and workbench sidebar can keep showing the page.
    browserUserClosedRef.current = true;
    setBrowserMini(false);
    setBrowserOpen(false);
  };
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
      setBrowserOpen(false);
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


  const send = createSocialSend({
    store,
    setStore,
    view,
    input,
    setInput,
    attachments,
    setAttachments,
    setBusyThreads,
    setAgentLive,
    quote,
    setQuote,
    setCtx,
    setCtxByAgent,
    setMentionOpen,
    setBrowserOpen,
    setBrowserMini,
    setBrowserFull,
    browserHoldRef,
    browserUserClosedRef,
    setBrowserCard,
    threadScrollerRef,
    abortByThreadRef,
    threadKeyRef,
    workLogsRef,
    busyThreadsRef,
    clearEpochRef,
    patchStore,
    agents,
    groups,
    activeGroup,
    activeAgent,
    chatWorkspacePath,
    threadBusy,
    groupMembers,
    setThreadMessages,
    rewardNotesFor,
    buildTextWithAttachments,
    messages,
    t,
    sessionId,
    onToast,
    onSessionId,
    onOpenBrowserUrl,
    threadKey,
  });


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
    setBrowserOpen(false);
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


  return {
    messages,
    threadKey,
    folders,
    activeAgent,
    activeGroup,
    addAnnouncement,
    addAttachments,
    agentLive,
    agentPanel,
    agentPanelTab,
    agents,
    applyQuote,
    attachBusy,
    attachInputRef,
    attachments,
    bottomRef,
    boundWorkspace,
    browserCard,
    browserDrag,
    browserFull,
    browserHeld,
    browserMini,
    browserPos,
    chatWorkspacePath,
    clearSelectHighlight,
    clearThread,
    clearTick,
    composerRef,
    configSheet,
    conversationList,
    createFolder,
    createMenuOpen,
    createWizard,
    ctx,
    ctxByAgent,
    deleteAgent,
    deleteFolder,
    deleteGroup,
    dropAnnouncement,
    embedded,
    folderCreateOpen,
    folderDraft,
    foldersSorted,
    groupMembers,
    groups,
    homeFolderFilter,
    homeSearch,
    input,
    insertMention,
    linkedWorkbenchDm,
    listPageCount,
    mentionCandidates,
    mentionOpen,
    onChatSelect,
    onInputChange,
    onOpenSettings,
    onToast,
    openDm,
    pagedConversationList,
    quote,
    quoteMenu,
    rateReply,
    removeAgentFromGroup,
    renderCollabTool,
    safeListPage,
    saveAgent,
    saveGroup,
    scheduleRefresh,
    send,
    setAgentPanel,
    setAgentPanelTab,
    setAttachments,
    setBrowserCard,
    setBrowserFull,
    setBrowserMini,
    setBrowserOpen,
    setConfigSheet,
    setCreateMenuOpen,
    setCreateWizard,
    setFolderCreateOpen,
    setFolderDraft,
    setHomeFolderFilter,
    setHomeSearch,
    setInput,
    setListPage,
    setQuote,
    setQuoteMenu,
    setShareCollapsed,
    setShareTab,
    setSocialPreviewUrl,
    setView,
    shareCollapsed,
    shareTab,
    startFreshDm,
    stop,
    store,
    t,
    threadBusy,
    threadScrollerRef,
    updateAgentField,
    updateGroupField,
    view,
    workspaces,
  };
}
