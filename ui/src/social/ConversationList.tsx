import type { MsgKey } from "../i18n";
import { AgentAvatar, GroupAvatar } from "./avatars";
import type { SocialAgent, SocialGroup } from "./types";

export type ConversationRow = {
  kind: "group" | "agent";
  id: string;
  preview: string;
};

type Folder = { id: string; name: string };

type Props = {
  t: (key: MsgKey, ...args: string[]) => string;
  rows: ConversationRow[];
  pageRows: ConversationRow[];
  groups: SocialGroup[];
  agents: SocialAgent[];
  folders: Folder[];
  page: number;
  pageCount: number;
  onPage: (page: number) => void;
  onOpenGroup: (id: string) => void;
  onOpenAgent: (id: string) => void;
  onConfigure: (kind: "group" | "agent", id: string) => void;
};

export function ConversationList({
  t,
  rows,
  pageRows,
  groups,
  agents,
  folders,
  page,
  pageCount,
  onPage,
  onOpenGroup,
  onOpenAgent,
  onConfigure,
}: Props) {
  return (
    <div className="social-card-list social-chat-list">
      {rows.length === 0 && <div className="social-empty-list">{t("socialListEmpty")}</div>}
      {pageRows.map((row) => {
        if (row.kind === "group") {
          const g = groups.find((x) => x.id === row.id);
          if (!g) return null;
          const members = g.memberIds
            .map((id) => agents.find((x) => x.id === id))
            .filter(Boolean) as SocialAgent[];
          return (
            <div key={"g-" + g.id} className="social-card social-card-wechat">
              <button type="button" className="social-card-main" onClick={() => onOpenGroup(g.id)}>
                <GroupAvatar group={g} members={members} />
                <span className="social-card-text">
                  <div className="social-card-title">
                    <span className="social-card-name">{g.name}</span>
                    {g.collabMode !== false && (
                      <span className="social-collab-badge">{t("socialCollabBadge")}</span>
                    )}
                    {g.folderId ? (
                      <span className="social-folder-badge">
                        {folders.find((f) => f.id === g.folderId)?.name || ""}
                      </span>
                    ) : null}
                  </div>
                  <div className="social-card-sub">
                    {[
                      g.workspace
                        ? g.workspace.replace(/\\/g, "/").split("/").filter(Boolean).pop() || ""
                        : "",
                      row.preview,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </div>
                </span>
              </button>
              <button
                type="button"
                className="social-icon-btn social-card-more"
                title={t("socialConfig")}
                onClick={(e) => {
                  e.stopPropagation();
                  onConfigure("group", g.id);
                }}
              >
                ···
              </button>
            </div>
          );
        }
        const a = agents.find((x) => x.id === row.id);
        if (!a) return null;
        return (
          <div key={"a-" + a.id} className="social-card social-card-wechat">
            <button type="button" className="social-card-main" onClick={() => onOpenAgent(a.id)}>
              <AgentAvatar agent={a} size={44} />
              <span className="social-card-text">
                <div className="social-card-title">
                  <span className="social-card-name">{a.name}</span>
                  {a.folderId ? (
                    <span className="social-folder-badge">
                      {folders.find((f) => f.id === a.folderId)?.name || ""}
                    </span>
                  ) : null}
                </div>
                <div className="social-card-sub">
                  {[
                    a.workspace
                      ? a.workspace.replace(/\\/g, "/").split("/").filter(Boolean).pop() || ""
                      : "",
                    row.preview,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </div>
              </span>
            </button>
            <button
              type="button"
              className="social-icon-btn social-card-more"
              title={t("socialConfig")}
              onClick={(e) => {
                e.stopPropagation();
                onConfigure("agent", a.id);
              }}
            >
              ···
            </button>
          </div>
        );
      })}
      {rows.length > 0 && (
        <div className="social-list-pager">
          <button
            type="button"
            className="social-ghost-btn"
            disabled={page <= 0}
            onClick={() => onPage(Math.max(0, page - 1))}
          >
            {t("socialPrevPage")}
          </button>
          <span className="social-muted">
            {t("socialPageStatus", String(page + 1), String(pageCount))}
          </span>
          <button
            type="button"
            className="social-ghost-btn"
            disabled={page >= pageCount - 1}
            onClick={() => onPage(Math.min(pageCount - 1, page + 1))}
          >
            {t("socialNextPage")}
          </button>
        </div>
      )}
    </div>
  );
}
