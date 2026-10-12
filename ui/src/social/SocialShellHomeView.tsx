// @ts-nocheck — extracted social home view.
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

export function SocialShellHomeView({ p, topBar }: { p: SocialShellViewProps; topBar: React.ReactNode }) {
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
