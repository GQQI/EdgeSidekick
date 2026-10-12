// @ts-nocheck — extracted social overlays (agent panel / browser float).
// @ts-nocheck
/* Render tree for SocialShell — logic stays in SocialShell.tsx. */
import { browserAct, streamChat, uploadFile, truncateSession } from "../api";
import { MarkdownView } from "../components/MarkdownView";
import { SchedulesPanel } from "../components/SchedulesPanel";
import { ThinkingBlock } from "../components/ThinkingBlock";
import { IconChat, IconCheck, IconFiles, IconX, IconPaperclip, IconSearch, IconSend,
  IconCalendar,
  IconPin,
  IconTrash,
  IconUser} from "../components/icons";
import { buildSocialBackendMessage, extractOwnReply, stripAgentTags } from "./buildPrompt";
import { AgentAvatar, GroupAvatar } from "./avatars";
import { ConversationList } from "./ConversationList";
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


import type { SocialShellViewProps } from "./SocialShellView";

export function SocialShellOverlays({ p }: { p: SocialShellViewProps }) {
  const {
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
  } = p;


  return (
    <>
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

      {browserCard && browserCard.threadKey === threadKey && !browserMini && (
        <div
          className={"social-browser-float" + (browserFull ? " full" : "")}
          style={browserFull ? undefined : { left: browserPos.x, top: browserPos.y, width: browserPos.w, height: browserPos.h }}
          role="dialog"
          aria-label={t("socialTabBrowser")}
        >
          <div
            className="social-browser-float-head"
            onPointerDown={(e) => {
              if ((e.target as HTMLElement).closest("button")) return;
              browserDrag.current = {
                mode: "move",
                sx: e.clientX,
                sy: e.clientY,
                ox: browserPos.x,
                oy: browserPos.y,
                ow: browserPos.w,
                oh: browserPos.h,
              };
            }}
          >
            <strong>{t("socialTabBrowser")}</strong>
            <span className="social-browser-float-tools">
              <button type="button" className="social-icon-btn" title="上一页" aria-label="上一页" onClick={() => { void browserAct({ op: "back" }); }}><BrowserGlyph name="back" /></button>
              <button type="button" className="social-icon-btn" title={browserFull ? "退出全屏" : "全屏"} aria-label={browserFull ? "退出全屏" : "全屏"} onClick={() => setBrowserFull((v) => !v)}><BrowserGlyph name={browserFull ? "restore" : "full"} /></button>
              <button type="button" className="social-icon-btn" title="最小化" aria-label="最小化" onClick={() => { setBrowserFull(false); setBrowserMini(true); }}><BrowserGlyph name="min" /></button>
              <button type="button" className="social-icon-btn" title="关闭" aria-label="关闭" onClick={() => setBrowserCard(null)}><BrowserGlyph name="close" /></button>
            </span>
          </div>
          <div className="social-browser-float-body">
            <SocialBrowserCard
              url={browserCard.url}
              busy={threadBusy}
              paused={browserHeld}
              fill
              bare
              onExpand={() => setBrowserFull(true)}
              onClose={() => setBrowserCard(null)}
              follow={`${browserPos.x},${browserPos.y},${browserPos.w},${browserPos.h},${browserFull ? 1 : 0}`}
            />
          </div>
          <div
            className="social-browser-float-resize"
            onPointerDown={(e) => {
              e.stopPropagation();
              browserDrag.current = {
                mode: "size",
                sx: e.clientX,
                sy: e.clientY,
                ox: browserPos.x,
                oy: browserPos.y,
                ow: browserPos.w,
                oh: browserPos.h,
              };
            }}
          />
        </div>
      )}

    </>
  );
}
