/** Assistant display: protocol parse, speaker labels, bubble expansion, roster. */

import type { ChatMsg } from "../types/chat";

export type AgentSpeechBlock = { name: string; text: string };

const AGENT_BLOCK_RE =
  /<{2,3}\s*agent\s*:\s*([^>\n]+?)\s*>{2,3}\s*([\s\S]*?)(?=<{2,3}\s*end\s*>{2,3}|<{2,3}\s*agent\s*:|$)/gi;

const AGENT_OPEN_RE = /<{2,3}\s*agent\s*:\s*([^>\n]+?)\s*>{2,3}/gi;

const GENERIC_ROLES = new Set([
  "leaf","orchestrator","task","subagent","assistant","agent","worker","helper","lead",
]);

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function splitAgentSpeechBlocks(text: string): AgentSpeechBlock[] {
  const out: AgentSpeechBlock[] = [];
  if (!text) return out;
  const re = new RegExp(AGENT_BLOCK_RE.source, AGENT_BLOCK_RE.flags);
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    const name = (m[1] || "").trim();
    let body = (m[2] || "").replace(/<{2,3}\s*end\s*>{2,3}/gi, "").trim();
    body = stripProtocolLines(body);
    if (name && body) out.push({ name, text: body });
    else if (name && !body) out.push({ name, text: "" });
  }
  if (out.length === 0) {
    const opens = [...text.matchAll(new RegExp(AGENT_OPEN_RE.source, "gi"))];
    if (opens.length) {
      const last = opens[opens.length - 1];
      const name = (last[1] || "").trim();
      const after = text.slice((last.index || 0) + last[0].length);
      const body = stripProtocolLines(after.replace(/<{2,3}\s*end\s*>{2,3}/gi, "").trim());
      if (name) out.push({ name, text: body || "\u2026" });
    }
  }
  return out.filter((b) => b.name);
}

export function stripAgentSpeechTags(text: string): string {
  if (!text) return "";
  let s = text;
  s = s.replace(/<{2,3}\s*agent\s*:[^>\n]*\s*>{2,3}/gi, "");
  s = s.replace(/<{2,3}\s*end\s*>{2,3}/gi, "");
  return s.replace(/\n{3,}/g, "\n\n").trim();
}

export function stripProtocolLines(raw: string): string {
  return String(raw || "")
    .replace(/^(DECISION|FACT|TASK|DONE_TASK|DONE|FORGET|UNFACT)\s*[:\uFF1A]\s*.+$/gim, "")
    .replace(/\bDECISION\s*[:\uFF1A]\s*(reply|silent)\b/gi, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function formatAssistantDisplay(raw: string): string {
  const text = String(raw || "");
  if (!text.trim()) return text;
  const blocks = splitAgentSpeechBlocks(text);
  if (blocks.length > 0) {
    return blocks.map((b) => stripProtocolLines(b.text)).filter(Boolean).join("\n\n");
  }
  if (
    /DECISION\s*[:\uFF1A]/i.test(text) ||
    /FACT\s*[:\uFF1A]/i.test(text) ||
    /TASK\s*[:\uFF1A]/i.test(text) ||
    /<{2,3}\s*agent\s*:/i.test(text) ||
    /<{2,3}\s*end\s*>{2,3}/i.test(text)
  ) {
    return stripProtocolLines(stripAgentSpeechTags(text));
  }
  return text;
}

/** Infer speaker when the model self-identifies as a known agent. */
export function inferSpeakerFromContent(
  text: string,
  knownNames: string[] | undefined | null,
): string | undefined {
  const names = [...(knownNames || [])]
    .map((n) => (n || "").trim())
    .filter(Boolean)
    .sort((a, b) => b.length - a.length);
  if (!names.length || !(text || "").trim()) return undefined;
  const prefix = "我是|我叫|本人";
  for (const name of names) {
    const re = new RegExp(`(?:${prefix})\\s*${escapeRegExp(name)}`);
    if (re.test(text)) return name;
  }
  return undefined;
}

/** Brand or brand-agent label. */
export function formatSpeakerLabel(brand: string, agentName?: string | null): string {
  const base = (brand || "").trim() || "Assistant";
  const agent = (agentName || "").trim();
  if (!agent) return base;
  if (agent === base) return base;
  if (agent.startsWith(base + "-") || agent.startsWith(base + "\u2010") || agent.startsWith(base + "\uff0d")) return agent;
  return `${base}-${agent}`;
}

function looksTechnicalId(id: string): boolean {
  const s = id.trim();
  if (!s) return true;
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s)) return true;
  if (/^(agent|sub|node|task|worker)[-_]/i.test(s)) return true;
  if (/^[a-z0-9_-]{16,}$/i.test(s) && !/[\u4e00-\u9fff]/.test(s)) return true;
  return false;
}

export function isGenericSpeakerRole(role?: string | null): boolean {
  const r = (role || "").trim().toLowerCase();
  return !r || GENERIC_ROLES.has(r);
}

export function resolveSpeakerName(
  m: Pick<ChatMsg, "agent_id" | "subagent" | "content" | "role"> & {
    speaker_name?: string;
  },
  knownNames?: string[] | null,
): string | undefined {
  const explicit = (m as { speaker_name?: string }).speaker_name?.trim();
  if (explicit) return explicit;
  const role = m.subagent?.role?.trim();
  if (role && !isGenericSpeakerRole(role)) return role;
  const aid = (m.agent_id || "").trim();
  if (aid && !looksTechnicalId(aid) && !isGenericSpeakerRole(aid)) return aid;
  const blocks = splitAgentSpeechBlocks(m.content || "");
  if (blocks.length === 1) return blocks[0].name;
  return inferSpeakerFromContent(m.content || "", knownNames);
}

export function expandAssistantMessages(
  m: ChatMsg,
  knownNames?: string[] | null,
): ChatMsg[] {
  if (m.role !== "assistant") return [m];
  const blocks = splitAgentSpeechBlocks(m.content || "");
  if (blocks.length >= 1) {
    return blocks.map((b, i) => ({
      ...m,
      id: blocks.length === 1 ? m.id : `${m.id}#speak-${i}`,
      agent_id: b.name,
      speaker_name: b.name,
      content: b.text || "",
      streaming: Boolean(m.streaming) && i === blocks.length - 1,
      reasoning: i === 0 ? m.reasoning : undefined,
      reasoningStreaming: i === 0 ? m.reasoningStreaming : undefined,
    }));
  }
  const inferred =
    (m as { speaker_name?: string }).speaker_name ||
    inferSpeakerFromContent(m.content || "", knownNames);
  return [{
    ...m,
    speaker_name: inferred || (m as { speaker_name?: string }).speaker_name,
    content: formatAssistantDisplay(m.content || ""),
  }];
}

export function collectSpeakerRoster(
  messages: ChatMsg[],
  knownNames?: string[] | null,
): string[] {
  const names: string[] = [];
  const seen = new Set<string>();
  const add = (n?: string | null) => {
    const v = (n || "").trim();
    if (!v || isGenericSpeakerRole(v) || looksTechnicalId(v)) return;
    const key = v.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    names.push(v);
  };
  for (const n of knownNames || []) add(n);
  for (const m of messages) {
    add((m as { speaker_name?: string }).speaker_name);
    add(m.agent_id);
    add(m.subagent?.role);
    for (const b of splitAgentSpeechBlocks(m.content || "")) add(b.name);
    add(inferSpeakerFromContent(m.content || "", knownNames));
  }
  return names;
}

