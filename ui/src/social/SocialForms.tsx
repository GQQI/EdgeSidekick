/** Agent / group create-edit forms and config sheet for SocialShell. */
import { useRef, useState } from "react";
import { streamChat } from "../api";
import type { MsgKey } from "../i18n";
import { AgentAvatar } from "./avatars";
import { dmKey, groupKey } from "./store";
import type { SocialAgent, SocialFolder, SocialGroup } from "./types";

type Translate = (key: MsgKey, ...args: string[]) => string;

export function AgentForm({
  t,
  initial,
  onCancel,
  onSave,
  embedded = false,
}: {
  t: Translate;
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

export function GroupForm({
  t,
  agents,
  initial,
  onCancel,
  onSave,
  embedded = false,
}: {
  t: Translate;
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

export function AnnComposer({
  t,
  onAdd,
}: {
  t: Translate;
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

export function readImageAsDataUrl(file: File): Promise<string> {
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

export function ConfigSheet({
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
  t: Translate;
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
