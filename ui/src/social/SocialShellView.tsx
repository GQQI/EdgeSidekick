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
import { SocialShellHomeView } from "./SocialShellHomeView";
import { SocialShellOverlays } from "./SocialShellOverlays";

export type SocialShellViewProps = {
  messages: any;
  threadKey: any;
  folders: any;
  activeAgent: any;
  activeGroup: any;
  addAnnouncement: any;
  addAttachments: any;
  agentLive: any;
  agentPanel: any;
  agentPanelTab: any;
  agents: any;
  applyQuote: any;
  attachBusy: any;
  attachInputRef: any;
  attachments: any;
  bottomRef: any;
  boundWorkspace: any;
  browserCard: any;
  browserDrag: any;
  browserFull: any;
  browserHeld: any;
  browserMini: any;
  browserPos: any;
  chatWorkspacePath: any;
  clearSelectHighlight: any;
  clearThread: any;
  clearTick: any;
  composerRef: any;
  configSheet: any;
  conversationList: any;
  createFolder: any;
  createMenuOpen: any;
  createWizard: any;
  ctx: any;
  ctxByAgent: any;
  deleteAgent: any;
  deleteFolder: any;
  deleteGroup: any;
  dropAnnouncement: any;
  embedded: any;
  folderCreateOpen: any;
  folderDraft: any;
  foldersSorted: any;
  groupMembers: any;
  groups: any;
  homeFolderFilter: any;
  homeSearch: any;
  input: any;
  insertMention: any;
  linkedWorkbenchDm: any;
  listPageCount: any;
  mentionCandidates: any;
  mentionOpen: any;
  onChatSelect: any;
  onInputChange: any;
  onOpenSettings: any;
  onToast: any;
  openDm: any;
  pagedConversationList: any;
  quote: any;
  quoteMenu: any;
  rateReply: any;
  removeAgentFromGroup: any;
  renderCollabTool: any;
  safeListPage: any;
  saveAgent: any;
  saveGroup: any;
  scheduleRefresh: any;
  send: any;
  setAgentPanel: any;
  setAgentPanelTab: any;
  setAttachments: any;
  setBrowserCard: any;
  setBrowserFull: any;
  setBrowserMini: any;
  setBrowserOpen: any;
  setConfigSheet: any;
  setCreateMenuOpen: any;
  setCreateWizard: any;
  setFolderCreateOpen: any;
  setFolderDraft: any;
  setHomeFolderFilter: any;
  setHomeSearch: any;
  setInput: any;
  setListPage: any;
  setQuote: any;
  setQuoteMenu: any;
  setShareCollapsed: any;
  setShareTab: any;
  setSocialPreviewUrl: any;
  setView: any;
  shareCollapsed: any;
  shareTab: any;
  startFreshDm: any;
  stop: any;
  store: any;
  t: any;
  threadBusy: any;
  threadScrollerRef: any;
  updateAgentField: any;
  updateGroupField: any;
  view: any;
  workspaces: any;
};

export function SocialShellView(p: SocialShellViewProps) {
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
    return <SocialShellHomeView p={p} topBar={topBar} />;
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
          <aside className={"social-share-pane" + (shareCollapsed ? " collapsed" : "")}>
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
              {(shareTab === "search" || shareTab === "files") && (
                <div className="social-share-tool">
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
                              stripAgentTags(m.text || "") ||
                              (m.streaming && !m.reasoningStreaming ? "…" : "")
                            }
                            streaming={!!m.streaming && !m.reasoningStreaming}
                            openSandboxOnClick
                            onCtrlClickUrl={(url) => {
                              setSocialPreviewUrl(url);
                              setBrowserOpen(true);
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
                {browserCard && browserCard.threadKey === threadKey && browserMini ? (
                  <button
                    type="button"
                    className="social-tool-btn social-browser-dock"
                    title="展开浏览器"
                    aria-label="展开浏览器"
                    onClick={() => setBrowserMini(false)}
                  >
                    <BrowserGlyph name="globe" />
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


      <SocialShellOverlays p={p} />
    </section>
  );
}
