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
import { SocialShellView } from "./SocialShellView";


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


import { useSocialShellModel } from "./useSocialShellModel";
export function SocialShell(props: Record<string, any>) {
  const m = useSocialShellModel(props);
  return (
    <SocialShellView
      messages={m.messages}
      threadKey={m.threadKey}
      folders={m.folders}
      activeAgent={m.activeAgent}
      activeGroup={m.activeGroup}
      addAnnouncement={m.addAnnouncement}
      addAttachments={m.addAttachments}
      agentLive={m.agentLive}
      agentPanel={m.agentPanel}
      agentPanelTab={m.agentPanelTab}
      agents={m.agents}
      applyQuote={m.applyQuote}
      attachBusy={m.attachBusy}
      attachInputRef={m.attachInputRef}
      attachments={m.attachments}
      bottomRef={m.bottomRef}
      boundWorkspace={m.boundWorkspace}
      browserCard={m.browserCard}
      browserDrag={m.browserDrag}
      browserFull={m.browserFull}
      browserHeld={m.browserHeld}
      browserMini={m.browserMini}
      browserPos={m.browserPos}
      chatWorkspacePath={m.chatWorkspacePath}
      clearSelectHighlight={m.clearSelectHighlight}
      clearThread={m.clearThread}
      clearTick={m.clearTick}
      composerRef={m.composerRef}
      configSheet={m.configSheet}
      conversationList={m.conversationList}
      createFolder={m.createFolder}
      createMenuOpen={m.createMenuOpen}
      createWizard={m.createWizard}
      ctx={m.ctx}
      ctxByAgent={m.ctxByAgent}
      deleteAgent={m.deleteAgent}
      deleteFolder={m.deleteFolder}
      deleteGroup={m.deleteGroup}
      dropAnnouncement={m.dropAnnouncement}
      embedded={m.embedded}
      folderCreateOpen={m.folderCreateOpen}
      folderDraft={m.folderDraft}
      foldersSorted={m.foldersSorted}
      groupMembers={m.groupMembers}
      groups={m.groups}
      homeFolderFilter={m.homeFolderFilter}
      homeSearch={m.homeSearch}
      input={m.input}
      insertMention={m.insertMention}
      linkedWorkbenchDm={m.linkedWorkbenchDm}
      listPageCount={m.listPageCount}
      mentionCandidates={m.mentionCandidates}
      mentionOpen={m.mentionOpen}
      onChatSelect={m.onChatSelect}
      onInputChange={m.onInputChange}
      onOpenSettings={m.onOpenSettings}
      onToast={m.onToast}
      openDm={m.openDm}
      pagedConversationList={m.pagedConversationList}
      quote={m.quote}
      quoteMenu={m.quoteMenu}
      rateReply={m.rateReply}
      removeAgentFromGroup={m.removeAgentFromGroup}
      renderCollabTool={m.renderCollabTool}
      safeListPage={m.safeListPage}
      saveAgent={m.saveAgent}
      saveGroup={m.saveGroup}
      scheduleRefresh={m.scheduleRefresh}
      send={m.send}
      setAgentPanel={m.setAgentPanel}
      setAgentPanelTab={m.setAgentPanelTab}
      setAttachments={m.setAttachments}
      setBrowserCard={m.setBrowserCard}
      setBrowserFull={m.setBrowserFull}
      setBrowserMini={m.setBrowserMini}
      setBrowserOpen={m.setBrowserOpen}
      setConfigSheet={m.setConfigSheet}
      setCreateMenuOpen={m.setCreateMenuOpen}
      setCreateWizard={m.setCreateWizard}
      setFolderCreateOpen={m.setFolderCreateOpen}
      setFolderDraft={m.setFolderDraft}
      setHomeFolderFilter={m.setHomeFolderFilter}
      setHomeSearch={m.setHomeSearch}
      setInput={m.setInput}
      setListPage={m.setListPage}
      setQuote={m.setQuote}
      setQuoteMenu={m.setQuoteMenu}
      setShareCollapsed={m.setShareCollapsed}
      setShareTab={m.setShareTab}
      setSocialPreviewUrl={m.setSocialPreviewUrl}
      setView={m.setView}
      shareCollapsed={m.shareCollapsed}
      shareTab={m.shareTab}
      startFreshDm={m.startFreshDm}
      stop={m.stop}
      store={m.store}
      t={m.t}
      threadBusy={m.threadBusy}
      threadScrollerRef={m.threadScrollerRef}
      updateAgentField={m.updateAgentField}
      updateGroupField={m.updateGroupField}
      view={m.view}
      workspaces={m.workspaces}
    />
  );
}
