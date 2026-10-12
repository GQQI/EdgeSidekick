import { useCallback, useRef, useState } from "react";
import {
  answerAsk,
  confirmPlan,
  decideApproval,
  fetchHealth,
  saveModel,
  savePrefs,
  writeFileContent,
} from "../api";
import type { PlanTask } from "../components/TaskPlanPanel";
import type { ActivePlan } from "../components/TaskPlanPanel";
import type {
  ApprovalPrompt,
  AskPrompt,
  DetailView,
  PendingConfirm,
  SettingsTab,
} from "../types/chat";
import { ASK_CUSTOM_KEY } from "../types/chat";
import type { PlanConfirmState } from "../types/plan";
import type { ModelSetup, ModelRole } from "../types/modelSetup";
import type { Health } from "../api";
import type { MsgKey } from "../i18n";

export type DialogsDeps = {
  t: (key: MsgKey, ...args: string[]) => string;
  sessionId: string | null;
  sessionIdRef: React.MutableRefObject<string | null>;
  approval: ApprovalPrompt | null;
  askPrompt: AskPrompt | null;
  askSubmitting: boolean;
  planConfirm: PlanConfirmState | null;
  planConfirmSubmitting: boolean;
  model: ModelSetup | null;
  detail: DetailView;
  setSettingsTab: (tab: SettingsTab) => void;
  setSettingsOpen: (v: boolean) => void;
  setModel: (m: ModelSetup | null) => void;
  setModelSaving: (v: boolean) => void;
  setHealth: React.Dispatch<React.SetStateAction<Health | null>>;
  setToast: (msg: string) => void;
  setApproval: React.Dispatch<React.SetStateAction<ApprovalPrompt | null>>;
  setAskPrompt: React.Dispatch<React.SetStateAction<AskPrompt | null>>;
  setAskChoice: (v: string) => void;
  setAskOtherText: (v: string) => void;
  setAskSubmitting: (v: boolean) => void;
  askPendingRef: React.MutableRefObject<boolean>;
  setPlanConfirm: React.Dispatch<React.SetStateAction<PlanConfirmState | null>>;
  setPlanConfirmSubmitting: (v: boolean) => void;
  planPendingRef: React.MutableRefObject<boolean>;
  executingPlanIdRef: React.MutableRefObject<string | null>;
  setActivePlan: React.Dispatch<React.SetStateAction<ActivePlan | null>>;
  setDetail: React.Dispatch<React.SetStateAction<DetailView>>;
  setPendingConfirm: React.Dispatch<React.SetStateAction<PendingConfirm | null>>;
  setFsRefresh: React.Dispatch<React.SetStateAction<number>>;
  onNewChat: () => Promise<void>;
};

export function useDialogs(deps: DialogsDeps) {
  const {
    t,
    sessionId,
    sessionIdRef,
    approval,
    askPrompt,
    askSubmitting,
    planConfirm,
    planConfirmSubmitting,
    model,
    detail,
    setSettingsTab,
    setSettingsOpen,
    setModel,
    setModelSaving,
    setHealth,
    setToast,
    setApproval,
    setAskPrompt,
    setAskChoice,
    setAskOtherText,
    setAskSubmitting,
    askPendingRef,
    setPlanConfirm,
    setPlanConfirmSubmitting,
    planPendingRef,
    executingPlanIdRef,
    setActivePlan,
    setDetail,
    setPendingConfirm,
    setFsRefresh,
    onNewChat,
  } = deps;

  const [autoApproveBusy, setAutoApproveBusy] = useState(false);

  const toggleAutoApprove = useCallback(
    async (enabled: boolean) => {
      setAutoApproveBusy(true);
      try {
        const res = await savePrefs({ auto_approve_tools: enabled });
        const on = Boolean(res.auto_approve_tools);
        setHealth((h) => (h ? { ...h, auto_approve_tools: on } : h));
        if (on) setApproval(null);
        setToast(on ? t("safetyAutoApproveOn") : t("safetyAutoApproveOff"));
        return on;
      } catch (e) {
        setToast(e instanceof Error ? e.message : String(e));
        return null;
      } finally {
        setAutoApproveBusy(false);
      }
    },
    [setHealth, setApproval, setToast, t],
  );

  const openSettings = useCallback(
    (tab: SettingsTab = "workspace") => {
      setSettingsTab(tab);
      setSettingsOpen(true);
    },
    [setSettingsTab, setSettingsOpen],
  );

  const applyModel = useCallback(
    async (next?: ModelSetup, opts?: { restartChat?: boolean }) => {
      const cfg = next ?? model;
      if (!cfg) return;
      setModelSaving(true);
      try {
        const res = await saveModel({ ...cfg, version: 3 });
        setModel(res.config);
        setHealth(await fetchHealth());
        setToast(res.note);
        if (opts?.restartChat) await onNewChat();
      } finally {
        setModelSaving(false);
      }
    },
    [model, setModelSaving, setModel, setHealth, setToast, onNewChat],
  );

  const switchModelRole = useCallback(
    async (role: ModelRole, providerId: string, modelId: string) => {
      if (!model) return;
      const snapshot = model;
      const ref = { provider_id: providerId, model_id: modelId };
      const optimistic = {
        ...model,
        [role]: ref,
        ...(role === "subagent" ? { compress: ref } : {}),
      };
      setModel(optimistic);
      setModelSaving(true);
      try {
        const res = await saveModel({ ...optimistic, version: 3 });
        setModel(res.config);
        setHealth(await fetchHealth());
        setToast(res.note);
      } catch (e) {
        setModel(snapshot);
        setToast(e instanceof Error ? e.message : String(e));
      } finally {
        setModelSaving(false);
      }
    },
    [model, setModel, setModelSaving, setHealth, setToast],
  );

  const saveDetailFile = useCallback(async () => {
    if (!detail || detail.type !== "file" || !detail.editable) return;
    setPendingConfirm({
      key: `save-file-${detail.path}`,
      title: "保存并覆盖文件？",
      detail: detail.path,
      confirmLabel: "保存",
      run: async () => {
        if (!detail || detail.type !== "file") return;
        await writeFileContent(detail.path, detail.content, detail.workspace);
        setDetail({ ...detail, dirty: false });
        setFsRefresh((n) => n + 1);
        setToast(`已保存 ${detail.path}`);
      },
    });
  }, [detail, setPendingConfirm, setDetail, setFsRefresh, setToast]);

  const resolveApproval = useCallback(
    async (approved: boolean, remember = false, patchArgs?: Record<string, unknown>) => {
      if (!approval) return;
      const sid = sessionIdRef.current || sessionId;
      if (!sid) {
        setToast("会话未就绪，请稍后重试。");
        return;
      }
      const id = approval.approvalId;
      const toolName = approval.name;
      const snapshot = approval;
      setApproval(null);
      try {
        await decideApproval(sid, id, approved, remember, approved ? patchArgs : undefined);
        if (!approved) setToast(t("approvalRejected"));
        else if (remember) setToast(t("approvalApprovedClass", toolName));
        else setToast(t("approvalApproved"));
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setApproval(snapshot);
        setToast(msg);
      }
    },
    [approval, sessionId, sessionIdRef, setApproval, setToast, t],
  );

  const resolveAsk = useCallback(
    async (choice: string, otherText = "") => {
      const prompt = askPrompt;
      const sid = prompt?.sessionId || sessionIdRef.current || sessionId;
      if (!prompt || !sid || askSubmitting) {
        if (prompt && !sid) setToast("会话未就绪，请稍后重试。");
        return;
      }
      const id = prompt.askId;
      if (!id) {
        setToast("询问已失效，请重新发送消息。");
        return;
      }
      const opt = prompt.options.find((o) => o.key === choice);
      const label = opt?.label || "";
      const text = choice === ASK_CUSTOM_KEY ? otherText.trim() : "";
      setAskSubmitting(true);
      setAskPrompt(null);
      setAskChoice("");
      setAskOtherText("");
      try {
        await answerAsk(sid, id, choice, text, label);
        setToast(t("askAnswered"));
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setToast(msg);
        askPendingRef.current = true;
        setAskPrompt(prompt);
        if (choice === ASK_CUSTOM_KEY) setAskOtherText(otherText);
        setAskChoice(choice);
      } finally {
        setAskSubmitting(false);
      }
    },
    [
      askPrompt,
      sessionIdRef,
      sessionId,
      askSubmitting,
      setAskSubmitting,
      setAskPrompt,
      setAskChoice,
      setAskOtherText,
      setToast,
      askPendingRef,
      t,
    ],
  );

  const resolvePlanConfirm = useCallback(
    async (approved: boolean, draft?: { summary: string; tasks: PlanTask[] }) => {
      const prompt = planConfirm;
      const sid = prompt?.sessionId || sessionIdRef.current || sessionId;
      if (!prompt || !sid || planConfirmSubmitting) {
        if (prompt && !sid) setToast("会话未就绪，请稍后重试。");
        return;
      }
      if (!prompt.planId) {
        setToast("方案已失效，请重新发送消息。");
        return;
      }
      const nextSummary = (draft?.summary ?? prompt.summary).trim() || prompt.summary;
      const nextTasks = (draft?.tasks ?? prompt.tasks).map((task) => ({
        ...task,
        status: "pending" as const,
      }));
      setPlanConfirmSubmitting(true);
      setPlanConfirm(null);
      if (approved) {
        executingPlanIdRef.current = prompt.planId;
        setActivePlan({
          planId: prompt.planId,
          summary: nextSummary,
          mode: "agent",
          awaitingConfirm: false,
          tasks: nextTasks,
        });
      } else {
        planPendingRef.current = false;
        executingPlanIdRef.current = null;
        setActivePlan(null);
      }
      try {
        await confirmPlan(sid, prompt.planId, {
          approved,
          summary: approved ? nextSummary : undefined,
          tasks: approved
            ? nextTasks.map((task) => ({
                id: task.id,
                title: task.title,
                detail: task.detail || "",
              }))
            : undefined,
        });
        setToast(approved ? t("planApproved") : t("planCancelled"));
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setToast(msg);
        planPendingRef.current = true;
        executingPlanIdRef.current = null;
        setPlanConfirm(prompt);
        if (!approved) setActivePlan(null);
      } finally {
        setPlanConfirmSubmitting(false);
      }
    },
    [
      planConfirm,
      sessionIdRef,
      sessionId,
      planConfirmSubmitting,
      setPlanConfirmSubmitting,
      setPlanConfirm,
      setActivePlan,
      planPendingRef,
      executingPlanIdRef,
      setToast,
      t,
    ],
  );

  return {
    openSettings,
    applyModel,
    switchModelRole,
    saveDetailFile,
    resolveApproval,
    resolveAsk,
    resolvePlanConfirm,
    autoApproveBusy,
    toggleAutoApprove,
  };
}
