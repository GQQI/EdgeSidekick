import type {
  AgentPrivateMemory,
  GroupSharedBoard,
  SocialAgent,
  SocialChatMsg,
  SocialFolder,
  SocialGroup,
  SocialStore,
  SocialThreadKey,
} from "./types";
import { emptyBoard, normalizeBoard } from "./sharedBoard";

export const DEFAULT_SKILLS = ["研究", "写作", "总结"];
export const DEFAULT_TOOLS = ["搜索", "文件", "浏览器"];

function normalizeAgent(a: Partial<SocialAgent> & Pick<SocialAgent, "id" | "name">): SocialAgent {
  return {
    id: a.id,
    name: a.name,
    title: a.title || "成员",
    color: a.color || "#3b82f6",
    blurb: a.blurb || a.duty || a.name,
    duty: a.duty || a.blurb || a.name,
    persona: a.persona || `你是智能体「${a.name}」，职责是：${a.duty || a.blurb || a.name}。`,
    remark: a.remark ?? "",
    location: a.location ?? "",
    folderId: a.folderId || "",
    workspace: a.workspace || "",
    sort: a.sort ?? 0,
    pinned: Boolean(a.pinned),
    avatar: a.avatar || "",
    skills: a.skills?.length ? a.skills : [...DEFAULT_SKILLS],
    tools: a.tools?.length ? a.tools : [...DEFAULT_TOOLS],
  };
}

function normalizeFolder(f: Partial<SocialFolder> & Pick<SocialFolder, "id" | "name">): SocialFolder {
  return {
    id: f.id,
    name: String(f.name || "").trim() || "未命名分组",
    sort: f.sort ?? 0,
    color: f.color || "",
  };
}

function normalizeGroup(g: SocialGroup): SocialGroup {
  return {
    ...g,
    remark: g.remark ?? "",
    location: g.location ?? "",
    folderId: g.folderId || "",
    workspace: g.workspace || "",
    sort: g.sort ?? 0,
    pinned: Boolean(g.pinned),
    avatar: g.avatar || "",
    collabMode: g.collabMode !== false,
  };
}


const KEY = "sidekick.social.store.v1";

const COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#a78bfa", "#f472b6", "#06b6d4", "#f97316", "#84cc16"];

export function uid(prefix = "id"): string {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
}

export function defaultAgents(): SocialAgent[] {
  return [
    {
      id: "lead",
      name: "\u5c0f\u72fc",
      title: "\u7f16\u6392\u52a9\u624b",
      color: COLORS[0],
      blurb: "\u542c\u9700\u6c42\u3001\u5206\u6d3b\u3001\u6c47\u603b",
      duty: "\u7406\u89e3\u7528\u6237\u76ee\u6807\uff0c\u534f\u8c03\u6210\u5458\uff0c\u6c47\u603b\u7ed3\u8bba\u4e0e\u4e0b\u4e00\u6b65\u3002",
      persona:
        "\u4f60\u662f\u7fa4\u804a\u91cc\u7684\u7f16\u6392\u52a9\u624b\u300c\u5c0f\u72fc\u300d\uff0c\u8d1f\u8d23\u7406\u89e3\u7528\u6237\u3001\u534f\u8c03\u5176\u4ed6\u6210\u5458\u3001\u7ed9\u51fa\u6e05\u6670\u603b\u7ed3\u3002",
    },
    {
      id: "researcher",
      name: "\u963f\u67e5",
      title: "\u7814\u7a76\u5458",
      color: COLORS[1],
      blurb: "\u67e5\u8d44\u6599\u3001\u5bf9\u6bd4\u3001\u5f15\u7528",
      duty: "\u68c0\u7d22\u4e0e\u6574\u7406\u4fe1\u606f\uff0c\u7ed9\u51fa\u6709\u4f9d\u636e\u7684\u7b80\u77ed\u7ed3\u8bba\u3002",
      persona:
        "\u4f60\u662f\u7fa4\u804a\u6210\u5458\u300c\u963f\u67e5\u300d\uff08\u7814\u7a76\u5458\uff09\uff0c\u64c5\u957f\u68c0\u7d22\u4e0e\u5bf9\u6bd4\uff0c\u53d1\u8a00\u7b80\u77ed\u6709\u4f9d\u636e\u3002",
    },
    {
      id: "engineer",
      name: "\u5de5\u5df1",
      title: "\u5de5\u7a0b\u5e08",
      color: COLORS[2],
      blurb: "\u6539\u4ee3\u7801\u3001\u843d\u5730\u5b9e\u73b0",
      duty: "\u628a\u65b9\u6848\u843d\u5230\u5b9e\u73b0\u5c42\uff0c\u6807\u6e05\u98ce\u9669\u4e0e\u5de5\u671f\u3002",
      persona:
        "\u4f60\u662f\u7fa4\u804a\u6210\u5458\u300c\u5de5\u5df1\u300d\uff08\u5de5\u7a0b\u5e08\uff09\uff0c\u5173\u6ce8\u53ef\u843d\u5730\u5b9e\u73b0\u4e0e\u98ce\u9669\uff0c\u53d1\u8a00\u52a1\u5b9e\u3002",
    },
    {
      id: "reviewer",
      name: "\u5ba1\u4e4b",
      title: "\u5ba1\u7a3f\u4eba",
      color: COLORS[3],
      blurb: "\u6311\u523a\u3001\u628a\u5173\u3001\u8d28\u91cf",
      duty: "\u5ba1\u67e5\u5047\u8bbe\u4e0e\u6f0f\u6d1e\uff0c\u63d0\u4f9b\u6539\u8fdb\u5efa\u8bae\u3002",
      persona:
        "\u4f60\u662f\u7fa4\u804a\u6210\u5458\u300c\u5ba1\u4e4b\u300d\uff08\u5ba1\u7a3f\u4eba\uff09\uff0c\u8d1f\u8d23\u8d28\u7591\u5047\u8bbe\u3001\u6307\u51fa\u6f0f\u6d1e\uff0c\u8bed\u6c14\u76f4\u63a5\u4f46\u4e0d\u4eba\u8eab\u653b\u51fb\u3002",
    },
    {
      id: "writer",
      name: "\u58a8\u767d",
      title: "\u5199\u624b",
      color: COLORS[4],
      blurb: "\u6da6\u8272\u3001\u8bf4\u660e\u3001\u6210\u6587",
      duty: "\u628a\u7ed3\u8bba\u5199\u6e05\u695a\u597d\u8bfb\uff0c\u9002\u5408\u975e\u4e13\u4e1a\u8bfb\u8005\u3002",
      persona:
        "\u4f60\u662f\u7fa4\u804a\u6210\u5458\u300c\u58a8\u767d\u300d\uff08\u5199\u624b\uff09\uff0c\u628a\u7ed3\u8bba\u5199\u6e05\u695a\u3001\u597d\u8bfb\uff0c\u907f\u514d\u672f\u8bed\u5806\u780c\u3002",
    },
  ].map(normalizeAgent);
}

const CODE_FOLDER_ID = "folder_code";

/** General programming agents. Stable ids so an existing store can adopt them once. */
export function programmingAgents(): SocialAgent[] {
  return [
    {
      id: "coder",
      name: "码川",
      title: "程序员",
      color: COLORS[5],
      blurb: "读代码、改代码、跑命令",
      duty: "在当前工作区里实现功能：先读相关文件，再修改，并用命令验证结果。",
      persona:
        "你是程序员「码川」。用户要求编程时，直接使用工作区工具完成，不要只给方案。"
        + "先用搜索和读文件确认现状，再用写入或替换改代码，最后用终端运行能证明结果的命令。"
        + "路径使用工作区相对路径。一次只改完成任务所需的文件，改完说明改了什么、怎么验证的。",
      folderId: CODE_FOLDER_ID,
      skills: ["编程", "阅读代码", "实现"],
      tools: ["文件", "终端", "搜索"],
    },
    {
      id: "debugger",
      name: "排错",
      title: "调试",
      color: COLORS[6],
      blurb: "复现问题、定位原因、修好",
      duty: "根据报错和复现步骤定位缺陷，做最小修改，并重新跑一遍确认修好。",
      persona:
        "你是调试工程师「排错」。先复现或阅读报错，再沿调用链读代码，不要凭猜测大改。"
        + "找到原因后用最小改动修好，并用原来的失败命令或测试再跑一次。说清原因、改动和验证结果。",
      folderId: CODE_FOLDER_ID,
      skills: ["调试", "阅读报错", "最小修复"],
      tools: ["文件", "终端", "搜索"],
    },
    {
      id: "tester",
      name: "验测",
      title: "测试",
      color: COLORS[7],
      blurb: "补测试、跑测试、报失败点",
      duty: "为改动补上能失败也能通过的检查，运行它们，并指出仍然失败的地方。",
      persona:
        "你是测试工程师「验测」。先看现有测试怎么跑，再为这次改动补一个能暴露问题的检查。"
        + "用终端实际运行，不要声称通过却没跑。失败时给出命令、断言和相关文件，不扩大无关范围。",
      folderId: CODE_FOLDER_ID,
      skills: ["测试", "回归", "复现"],
      tools: ["文件", "终端", "搜索"],
    },
    {
      id: "codereview",
      name: "审码",
      title: "代码审查",
      color: "#64748b",
      blurb: "看差异、指出风险、不扩大改动",
      duty: "审查当前改动的正确性、遗漏和风险，只在用户要求时才亲自修改。",
      persona:
        "你是代码审查「审码」。先读实际差异和相关代码，按正确性、遗漏、风险给出具体意见，并指出文件位置。"
        + "用户没让你改代码时不要改。若要求修改，只处理你指出的问题，并说明如何验证。",
      folderId: CODE_FOLDER_ID,
      skills: ["审查", "风险", "阅读差异"],
      tools: ["文件", "搜索", "终端"],
    },
  ].map(normalizeAgent);
}

function programmingFolder(): SocialFolder {
  return { id: CODE_FOLDER_ID, name: "编程", sort: 0 };
}

function programmingGroup(agents: SocialAgent[]): SocialGroup {
  const now = Date.now();
  return {
    id: "group_code",
    name: "编程组",
    topic: "读代码、改代码、跑测试",
    memberIds: agents.map((a) => a.id),
    announcements: [
      {
        id: "ann_code_welcome",
        title: "怎么用",
        body: "直接说要改什么。码川负责实现，排错负责修缺陷，验测负责跑检查，审码负责看风险。",
        createdAt: now,
      },
    ],
    shared: [],
    createdAt: now,
    folderId: CODE_FOLDER_ID,
    collabMode: true,
  };
}

/** Insert the programming pack when this store does not already have it. */
export function ensureProgrammingPack(store: SocialStore): SocialStore {
  const pack = programmingAgents();
  const known = new Set(store.agents.map((a) => a.id));
  const agents = [...store.agents];
  for (const agent of pack) {
    if (!known.has(agent.id)) agents.push(agent);
  }
  const folders = [...(store.folders || [])];
  if (!folders.some((f) => f.id === CODE_FOLDER_ID)) folders.unshift(programmingFolder());
  const groups = [...(store.groups || [])];
  if (!groups.some((g) => g.id === "group_code")) {
    groups.push(programmingGroup(pack));
  }
  return { ...store, agents, folders, groups };
}

export function defaultGroups(agents: SocialAgent[]): SocialGroup[] {
  const now = Date.now();
  return [
    {
      id: "group_main",
      name: "\u534f\u4f5c\u7fa4",
      topic: "\u65e5\u5e38\u534f\u4f5c\u4e0e\u6d3e\u6d3b",
      memberIds: agents.map((a) => a.id),
      announcements: [
        {
          id: uid("ann"),
          title: "\u7fa4\u516c\u544a",
          body: "\u6b22\u8fce\u6765\u5230\u534f\u4f5c\u7fa4\u3002\u70b9\u51fb\u6210\u5458\u53ef\u8fdb\u5165\u79c1\u804a\uff1b\u5728\u7fa4\u91cc\u53ef @ \u6210\u5458\u6d3e\u6d3b\u3002",
          createdAt: now,
        },
      ],
      shared: [
        {
          id: uid("share"),
          kind: "note",
          title: "\u5171\u4eab\u7b14\u8bb0",
          detail: "\u53ef\u5728\u8fd9\u91cc\u653e\u7fa4\u516c\u5171\u8981\u70b9\uff08MVP \u5360\u4f4d\uff0c\u540e\u7eed\u53ef\u63a5\u6587\u4ef6\uff09\u3002",
          createdAt: now,
        },
      ],
      createdAt: now,
    },
  ];
}

function seed(): SocialStore {
  const agents = defaultAgents();
  return ensureProgrammingPack({
    agents,
    groups: defaultGroups(agents),
    folders: [],
    threads: {},
    boards: {},
    agentMemories: {},
    dmActiveSession: {},
    dmSessionMode: {},
    dmStreamNonce: {},
  });
}

export function loadSocialStore(): SocialStore {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) {
      const fresh = seed();
      saveSocialStore(fresh);
      return fresh;
    }
    const parsed = JSON.parse(raw) as SocialStore;
    if (!parsed?.agents?.length) return seed();
    if (!parsed.groups) parsed.groups = defaultGroups(parsed.agents);
    if (!parsed.threads) parsed.threads = {};
    const boardsIn = (parsed as SocialStore).boards || {};
    const boards: Record<string, GroupSharedBoard> = {};
    for (const [gid, raw] of Object.entries(boardsIn)) {
      boards[gid] = normalizeBoard(raw);
    }
    const agentMemories = (parsed as SocialStore).agentMemories || {};
    const folders = ((parsed as SocialStore).folders || []).map((f) =>
      normalizeFolder(f as SocialFolder),
    );
    const loaded: SocialStore = {
      ...parsed,
      agents: parsed.agents.map((a) => normalizeAgent(a)),
      groups: parsed.groups.map((g) => normalizeGroup(g)),
      folders,
      boards,
      agentMemories,
    };
    const next = ensureProgrammingPack(loaded);
    const added =
      next.agents.length !== loaded.agents.length
      || next.groups.length !== loaded.groups.length
      || next.folders.length !== loaded.folders.length;
    if (added) saveSocialStore(next);
    return next;
  } catch {
    return seed();
  }
}

export function saveSocialStore(store: SocialStore) {
  try {
    localStorage.setItem(KEY, JSON.stringify(store));
  } catch {
    /* ignore */
  }
}

export function groupKey(groupId: string): SocialThreadKey {
  return `group:${groupId}`;
}

export function dmKey(agentId: string, sessionId: string = "main"): SocialThreadKey {
  const sid = (sessionId || "main").trim() || "main";
  return sid === "main" ? `dm:${agentId}` : `dm:${agentId}:${sid}`;
}

export function parseDmThreadKey(key: string): { agentId: string; sessionId: string } | null {
  if (!key.startsWith("dm:")) return null;
  const rest = key.slice(3);
  const i = rest.indexOf(":");
  if (i < 0) return { agentId: rest, sessionId: "main" };
  return { agentId: rest.slice(0, i), sessionId: rest.slice(i + 1) || "main" };
}

export function getDmActiveSession(store: SocialStore, agentId: string): string {
  return (store.dmActiveSession && store.dmActiveSession[agentId]) || "main";
}

export function setDmActiveSession(store: SocialStore, agentId: string, sessionId: string): SocialStore {
  return {
    ...store,
    dmActiveSession: { ...(store.dmActiveSession || {}), [agentId]: sessionId || "main" },
  };
}

/** Start a fresh DM session for an agent without touching group threads or other DM sessions. */

export function getDmSessionMode(store: SocialStore, agentId: string): "aware" | "blank" {
  return (store.dmSessionMode && store.dmSessionMode[agentId]) || "aware";
}

export function setDmSessionMode(
  store: SocialStore,
  agentId: string,
  mode: "aware" | "blank",
): SocialStore {
  return {
    ...store,
    dmSessionMode: { ...(store.dmSessionMode || {}), [agentId]: mode },
  };
}

export function dmStreamNonceKey(agentId: string, sessionId: string = "main"): string {
  return `${agentId}:${sessionId || "main"}`;
}

export function getDmStreamNonce(store: SocialStore, agentId: string, sessionId?: string): number {
  const sid = sessionId || getDmActiveSession(store, agentId);
  const key = dmStreamNonceKey(agentId, sid);
  return (store.dmStreamNonce && store.dmStreamNonce[key]) || 0;
}

export function bumpDmStreamNonce(store: SocialStore, agentId: string, sessionId?: string): SocialStore {
  const sid = sessionId || getDmActiveSession(store, agentId);
  const key = dmStreamNonceKey(agentId, sid);
  const cur = (store.dmStreamNonce && store.dmStreamNonce[key]) || 0;
  return {
    ...store,
    dmStreamNonce: { ...(store.dmStreamNonce || {}), [key]: cur + 1 },
  };
}

/** Clear only the DM thread messages. Keeps group boards and all agent memories (including group-tagged). Rotates stream nonce. */

/** Mark active DM as group-aware (default when opening from roster / group). Does not create a new thread. */
export function markDmAware(store: SocialStore, agentId: string): SocialStore {
  return setDmSessionMode(store, agentId, "aware");
}

/** Memory context tag for a DM session. Legacy notes may use plain "dm". */
export function dmMemoryContext(agentId: string, sessionId: string = "main"): string {
  const sid = (sessionId || "main").trim() || "main";
  return sid === "main" ? `dm:${agentId}` : `dm:${agentId}:${sid}`;
}

export function isDmMemoryContext(context: string | undefined, agentId: string, sessionId?: string): boolean {
  if (!context) return false;
  // Legacy unscoped DM notes count as current-DM memory when clearing any DM.
  if (context === "dm") return true;
  if (sessionId) {
    // Only this DM session (not other parallel DM sessions).
    return context === dmMemoryContext(agentId, sessionId);
  }
  // No session scope: any DM lane for this agent.
  return context === `dm:${agentId}` || context.startsWith(`dm:${agentId}:`);
}

export function isGroupMemoryContext(context: string | undefined, groupId: string): boolean {
  return !!context && (context === `group:${groupId}` || context.startsWith(`group:${groupId}:`));
}


/** Clear a group thread + shared board, and wipe every member's memories tagged to this group. Does not touch DM memories. */
export function clearGroupThreadAndMemories(store: SocialStore, groupId: string): SocialStore {
  const key = groupKey(groupId);
  const group = store.groups.find((g) => g.id === groupId);
  const notice: SocialChatMsg = {
    id: uid("m"),
    role: "system",
    text: group
      ? `已清空群「${group.name}」的聊天，并清除所有成员在本群的记忆。`
      : "已清空本群聊天，并清除所有成员在本群的记忆。",
    at: Date.now(),
  };
  const memberIds = group?.memberIds || [];
  const agentMemories = { ...(store.agentMemories || {}) };
  for (const aid of memberIds) {
    const mem = agentMemories[aid];
    if (!mem) continue;
    agentMemories[aid] = {
      notes: (mem.notes || []).filter((n) => !isGroupMemoryContext(n.context, groupId)),
      updatedAt: Date.now(),
    };
  }
  for (const [aid, mem] of Object.entries(agentMemories)) {
    if (memberIds.includes(aid)) continue;
    const filtered = (mem.notes || []).filter((n) => !isGroupMemoryContext(n.context, groupId));
    if (filtered.length !== (mem.notes || []).length) {
      agentMemories[aid] = { notes: filtered, updatedAt: Date.now() };
    }
  }
  return {
    ...store,
    threads: { ...store.threads, [key]: [notice] },
    boards: { ...(store.boards || {}), [groupId]: emptyBoard() },
    agentMemories,
  };
}

export function clearDmThreadOnly(store: SocialStore, agentId: string): SocialStore {
  const sid = getDmActiveSession(store, agentId);
  const key = dmKey(agentId, sid);
  const agent = store.agents.find((a) => a.id === agentId);
  const notice: SocialChatMsg = {
    id: uid("m"),
    role: "system",
    text: agent
      ? `已清空与「${agent.name}」的当前私聊，并忘记本场私聊记忆（群聊记忆仍保留）。`
      : "已清空当前私聊，并忘记本场私聊记忆（群聊记忆仍保留）。",
    at: Date.now(),
  };
  const mem = store.agentMemories?.[agentId];
  const notes = (mem?.notes || []).filter((n) => !isDmMemoryContext(n.context, agentId, sid));
  let next: SocialStore = {
    ...store,
    threads: { ...store.threads, [key]: [notice] },
    agentMemories: {
      ...(store.agentMemories || {}),
      [agentId]: { notes, updatedAt: Date.now() },
    },
  };
  next = bumpDmStreamNonce(next, agentId, sid);
  return next;
}


export function startFreshDmSession(store: SocialStore, agentId: string): { store: SocialStore; key: SocialThreadKey; sessionId: string } {
  const sessionId = `s${Date.now().toString(36)}`;
  const key = dmKey(agentId, sessionId);
  const agent = store.agents.find((a) => a.id === agentId);
  const welcome: SocialChatMsg = {
    id: uid("m"),
    role: "system",
    text: agent
      ? `已与「${agent.name}」开启空白私聊会话：不带入群聊记录与群记忆，与其他会话相互独立。`
      : "已开启空白私聊会话：不带入群聊记录与群记忆。",
    at: Date.now(),
  };
  let next: SocialStore = {
    ...store,
    threads: { ...store.threads, [key]: [welcome] },
    dmActiveSession: { ...(store.dmActiveSession || {}), [agentId]: sessionId },
    dmSessionMode: { ...(store.dmSessionMode || {}), [agentId]: "blank" },
    dmStreamNonce: { ...(store.dmStreamNonce || {}), [dmStreamNonceKey(agentId, sessionId)]: 0 },
  };
  return { store: next, key, sessionId };
}


export function nextAgentColor(agents: SocialAgent[]): string {
  return COLORS[agents.length % COLORS.length];
}

export function agentById(agents: SocialAgent[], id: string): SocialAgent | undefined {
  return agents.find((a) => a.id === id);
}

export function agentByName(agents: SocialAgent[], name: string): SocialAgent | undefined {
  const n = name.trim().toLowerCase();
  return agents.find(
    (a) => a.name.toLowerCase() === n || a.id.toLowerCase() === n || a.title === name.trim(),
  );
}

export function parseMentions(text: string, agents: SocialAgent[]): SocialAgent[] {
  const found: SocialAgent[] = [];
  const add = (hit: SocialAgent | undefined) => {
    if (hit && !found.some((a) => a.id === hit.id)) found.push(hit);
  };
  const raw = text || "";
  // Longer names first so short names don't steal a prefix match.
  const ordered = [...agents].filter((a) => a.name).sort((a, b) => b.name.length - a.name.length);

  // @Name
  const atRe = /@([\w\u4e00-\u9fff]+)/g;
  let m: RegExpExecArray | null;
  while ((m = atRe.exec(raw))) {
    add(agentByName(agents, m[1]));
  }

  // 让/请/叫/麻烦 Name …回复/回答/说/评价/负责…
  const askRe =
    /(?:让|请|叫|麻烦)\s*([\w\u4e00-\u9fff]{1,12})\s*(?:来|去)?\s*(?:回复|回答|发言|说一下|说说|接一下|处理|评价|评估|负责|看看|分析)/g;
  while ((m = askRe.exec(raw))) {
    add(agentByName(agents, m[1]));
  }

  // Vocative / direct address (no @ required):
  //   墨白你现在… / 墨白，评价… / 墨白：… / 墨白、你来…
  //   问墨白… / 交给墨白… / 回复墨白
  for (const a of ordered) {
    const n = a.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const vocative = new RegExp(
      "(?:^|[\\s，,。！？、：:;；])" + n + "(?:你|[，,：:、]\\s*|\\s+)(?!的)",
    );
    const imperative = new RegExp(
      n + "[，,\\s]*你(?:来|去)?(?:回复|回答|发言|说|评价|评估|负责|看看|分析)|" +
        "(?:回复|回答|交给|问|请|让)" + n,
    );
    const presence = new RegExp(
      "(?:^|[\\s，,。！？、：:;；])" + n + "(?:在吗|在么|呢|出来|来一下)",
    );
    if (vocative.test(raw) || imperative.test(raw) || presence.test(raw)) add(a);
  }
  return found;
}

/** Resolve who should speak this group turn. */
export type GroupTurnPlan = {
  /** Agents that receive a model call. In a group this is the whole roster. */
  targets: SocialAgent[];
  /** True = each target may choose silent; false = must reply. */
  optionalReply: boolean;
  /** Legacy presentation flag. Audience restriction is expressed by `targets`. */
  exclusive: boolean;
  /** Explicit @全体 / everyone. */
  everyone: boolean;
  /** Strongly addressed agents (may equal targets when exclusive). */
  addressed: SocialAgent[];
};

export function resolveGroupTurn(text: string, roster: SocialAgent[]): GroupTurnPlan {
  const everyone = mentionsEveryone(text);
  const addressed = parseMentions(text, roster);
  // @ names who the user hopes will lead. Every member still sees the message,
  // so a wrong @ can be noticed and a later @ from a colleague can be answered.
  const targets = roster;
  return {
    targets,
    optionalReply: true,
    exclusive: false,
    everyone,
    addressed,
  };
}

export function mentionsEveryone(text: string): boolean {
  return /@(?:全体成员|全体|所有人|全部|everyone|all)\b/i.test(text) || /@(?:全体成员|全体|所有人|全部)/.test(text);
}

export function ensureThread(
  store: SocialStore,
  key: SocialThreadKey,
  welcome?: string,
): SocialChatMsg[] {
  if (store.threads[key]?.length) return store.threads[key];
  const msgs: SocialChatMsg[] = welcome
    ? [{ id: uid("m"), role: "system", text: welcome, at: Date.now() }]
    : [];
  store.threads[key] = msgs;
  return msgs;
}


export function getGroupBoard(store: SocialStore, groupId: string): GroupSharedBoard {
  return normalizeBoard(store.boards?.[groupId]);
}

export function setGroupBoard(
  store: SocialStore,
  groupId: string,
  board: GroupSharedBoard,
): SocialStore {
  return {
    ...store,
    boards: { ...(store.boards || {}), [groupId]: board },
  };
}


export function emptyAgentMemory(): AgentPrivateMemory {
  return { notes: [], updatedAt: Date.now() };
}

export function getAgentMemory(store: SocialStore, agentId: string): AgentPrivateMemory {
  return store.agentMemories?.[agentId] || emptyAgentMemory();
}

export function appendAgentMemory(
  store: SocialStore,
  agentId: string,
  text: string,
  context?: string,
  limit = 40,
): SocialStore {
  const noteText = (text || "").trim();
  if (!noteText) return store;
  const prev = getAgentMemory(store, agentId);
  const note = {
    id: uid("mem"),
    text: noteText.slice(0, 240),
    at: Date.now(),
    context,
  };
  const notes = [...prev.notes, note].slice(-limit);
  return {
    ...store,
    agentMemories: {
      ...(store.agentMemories || {}),
      [agentId]: { notes, updatedAt: Date.now() },
    },
  };
}

export function formatAgentMemoryForPrompt(
  store: SocialStore,
  agentId: string,
  limit = 12,
  opts?: { includeGroupContext?: boolean; onlyDm?: boolean; groupId?: string },
): string {
  const mem = getAgentMemory(store, agentId);
  let notes = mem.notes.slice(-limit * 2);
  if (opts?.onlyDm) {
    notes = notes.filter((n) => !n.context || n.context === "dm" || String(n.context).startsWith("dm"));
  } else if (opts?.includeGroupContext === false) {
    notes = notes.filter((n) => !String(n.context || "").startsWith("group:"));
  } else if (opts?.groupId) {
    const gctx = `group:${opts.groupId}`;
    // Prefer notes from this group + dm, keep a few others for continuity.
    const preferred = notes.filter(
      (n) => !n.context || n.context === "dm" || n.context === gctx || String(n.context).startsWith("dm"),
    );
    notes = preferred.length ? preferred : notes;
  }
  notes = notes.slice(-limit);
  if (!notes.length) return "";
  return (
    "【你的私人记忆】（仅你可见，不是群共享）\n" +
    notes.map((n) => `- ${n.text}`).join("\n") +
    "\n"
  );
}

