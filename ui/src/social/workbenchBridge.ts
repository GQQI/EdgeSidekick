/** Bridge workbench session transcript <-> social DM with the lead agent. */
import { fetchSession, type SessionDetailMessage } from "../api";
import type { SocialAgent, SocialChatMsg } from "./types";
import { uid } from "./store";

/** Default agent that shares the workbench timeline (lead / 小狼). */
export const WORKBENCH_LINKED_AGENT_ID = "lead";

export function isWorkbenchLinkedAgent(agentId: string | undefined | null): boolean {
  return agentId === WORKBENCH_LINKED_AGENT_ID;
}

function isHiddenUserContent(content: string): boolean {
  const t = (content || "").trim();
  return !t || t.startsWith("[[sidekick:") || t.startsWith("[[internal:");
}

/** Map a backend session transcript into social DM bubbles for the linked agent. */
export function sessionMessagesToSocialDm(
  messages: SessionDetailMessage[] | undefined,
  agent: SocialAgent,
): SocialChatMsg[] {
  const out: SocialChatMsg[] = [];
  for (const m of messages || []) {
    const role = String(m.role || "");
    const content = String(m.content || "").trim();
    const at = m.ts ? Math.round(Number(m.ts) * (Number(m.ts) > 1e12 ? 1 : 1000)) : Date.now();
    if (role === "user") {
      if (isHiddenUserContent(content)) continue;
      out.push({
        id: uid("m"),
        role: "user",
        text: content,
        at,
      });
      continue;
    }
    if (role === "assistant") {
      if (!content && !m.reasoning) continue;
      out.push({
        id: uid("m"),
        role: "agent",
        agentId: agent.id,
        agentName: agent.name,
        text: content || "",
        reasoning: m.reasoning || undefined,
        at,
      });
      continue;
    }
    // Skip tool / system noise in the social timeline for now.
  }
  return out;
}

/** Load workbench session messages into social-DM shaped bubbles. */
export async function hydrateLinkedDmFromSession(
  sessionId: string | null | undefined,
  agent: SocialAgent,
): Promise<SocialChatMsg[]> {
  if (!sessionId) return [];
  const detail = await fetchSession(sessionId);
  return sessionMessagesToSocialDm(detail.messages, agent);
}
