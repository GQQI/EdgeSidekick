// @ts-nocheck — extracted send pipeline; deps typed loosely to preserve behavior.
/** SocialShell send pipeline (group/DM turns). Factory keeps closures explicit. */
import { browserAct, streamChat } from "../api";
import { loadLocale } from "../i18n";
import { isWorkbenchLinkedAgent } from "./workbenchBridge";
import {
  appendRlTransition,
  applyOutcome,
  abstractWorkflow,
  classifySituation,
  ensureStrategy,
  strategyHint,
} from "./strategy";
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
  summarizeReply,
  cleanDisplayText,
} from "./sharedBoard";
import {
  agentById,
  agentByName,
  dmKey,
  ensureThread,
  groupKey,
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
  getDmStreamNonce,
  dmMemoryContext,
} from "./store";
import { buildSocialBackendMessage, extractOwnReply, stripAgentTags } from "./buildPrompt";
import {
  isActiveAgentPhase,
  type AgentLivePhase,
  type AgentActivityKind,
  type AgentActivity,
  type AgentLiveState,
  type SocialAttachment,
} from "./socialLiveState";
import type {
  SocialAgent,
  SocialChatMsg,
  SocialGroup,
  SocialStore,
  SocialThreadKey,
} from "./types";

export type SocialSendDeps = {
  store: any;
  setStore: any;
  view: any;
  input: any;
  setInput: any;
  attachments: any;
  setAttachments: any;
  setBusyThreads: any;
  setAgentLive: any;
  quote: any;
  setQuote: any;
  setCtx: any;
  setCtxByAgent: any;
  setMentionOpen: any;
  setBrowserOpen: any;
  setBrowserMini: any;
  setBrowserFull: any;
  browserHoldRef: any;
  browserUserClosedRef: any;
  setBrowserCard: any;
  threadScrollerRef: any;
  abortByThreadRef: any;
  threadKeyRef: any;
  workLogsRef: any;
  busyThreadsRef: any;
  clearEpochRef: any;
  patchStore: any;
  agents: any;
  groups: any;
  activeGroup: any;
  activeAgent: any;
  chatWorkspacePath: any;
  threadBusy: any;
  groupMembers: any;
  setThreadMessages: any;
  rewardNotesFor: any;
  buildTextWithAttachments: any;
  messages: any;
  t: any;
  sessionId: any;
  onToast: any;
  onSessionId: any;
  onOpenBrowserUrl: any;
  threadKey: any;
};

