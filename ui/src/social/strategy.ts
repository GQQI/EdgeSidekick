import type { SituationKind, StrategyRow, StrategyTable } from "./types";

export type Situation = SituationKind;

export type { StrategyRow, StrategyTable };

/** One transition for a later RL run. Append-only; never used as prompt text. */
export type RlTransition = {
  id: string;
  at: number;
  threadKey: string;
  agentId: string;
  agentName: string;
  situation: Situation;
  state: { userText: string };
  action: { reply: string; policy: string };
  reward: number;
  rewardSource: "user_score" | "task";
};

const RL_KEY = "sidekick.social.rl.v1";

const POLICIES: Record<Situation, string> = {
  chat: "短句回应，不调用工具，不复述别人刚说过的话。",
  capability: "用两三句说明自己能做什么，不调用工具，不展开流程清单。",
  code: "先读相关文件，再做最小修改，并用命令确认。",
  browser: "只操作当前视口；页面没变就滚动一屏；读完就结束。",
  other: "先判断是在闲聊还是在派活。闲聊短答；派活再调用工具。",
};

export function emptyStrategy(): StrategyTable {
  const situations = Object.keys(POLICIES) as Situation[];
  return Object.fromEntries(
    situations.map((situation) => [
      situation,
      {
        situation,
        policy: POLICIES[situation],
        attempts: 0,
        successes: 0,
        ratingSum: 0,
        ratingCount: 0,
        workflows: [],
      },
    ]),
  ) as unknown as StrategyTable;
}

export function classifySituation(userText: string): Situation {
  const text = String(userText || "");
  if (/浏览器|打开网页|页面|滚动|点击/.test(text)) return "browser";
  if (/代码|修改|修复|实现|运行|测试|文件|bug|报错/.test(text)) return "code";
  if (/你是谁|你能做什么|你会什么|你的职责|有(?:什么|啥)能力|介绍一下你|擅长什么|能干啥|会啥/.test(text)) return "capability";
  if (/你好|您好|谢谢|在吗|早上好|晚安/.test(text)) return "chat";
  return "other";
}

export function ensureStrategy(current: StrategyTable | undefined): StrategyTable {
  const base = emptyStrategy();
  if (!current) return base;
  for (const key of Object.keys(base) as Situation[]) {
    const row = current[key];
    if (!row) continue;
    base[key] = {
      ...base[key],
      attempts: row.attempts || 0,
      successes: row.successes || 0,
      ratingSum: row.ratingSum || 0,
      ratingCount: row.ratingCount || 0,
      workflows: Array.isArray(row.workflows) ? row.workflows.slice(0, 3) : [],
    };
  }
  return base;
}

export function abstractWorkflow(reply: string): string {
  return String(reply || "")
    .replace(/[A-Za-z]:\\[^\s"'，。；;]+/g, "某路径")
    .replace(/(?:\/|\\)[\w.\-\\/]{3,}/g, "某路径")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 96);
}

export function applyOutcome(
  table: StrategyTable,
  situation: Situation,
  success: boolean,
  score?: number,
  workflow?: string,
): StrategyTable {
  const row = { ...table[situation], workflows: [...table[situation].workflows] };
  row.attempts += 1;
  if (success) row.successes += 1;
  if (typeof score === "number") {
    row.ratingSum += score;
    row.ratingCount += 1;
  }
  const step = String(workflow || "").trim();
  if (success && step && (situation === "code" || situation === "browser") && !row.workflows.includes(step)) {
    row.workflows = [...row.workflows, step].slice(-3);
  }
  return { ...table, [situation]: row };
}

export function strategyHint(table: StrategyTable | undefined, userText: string): string {
  const situation = classifySituation(userText);
  const row = (table || emptyStrategy())[situation];
  const rate = row.attempts > 0 ? Math.round((row.successes / row.attempts) * 100) : 0;
  const stars = row.ratingCount > 0 ? (row.ratingSum / row.ratingCount).toFixed(1) : "";
  if (row.attempts <= 0 && row.workflows.length === 0) return "";
  const extra = row.workflows[0] || "";
  const stat = row.attempts > 0 ? `成功率 ${rate}%${stars ? `，平均 ${stars} 星` : ""}` : "";
  return [stat, extra].filter(Boolean).join("。");
}

export function appendRlTransition(row: RlTransition) {
  try {
    const prev = JSON.parse(localStorage.getItem(RL_KEY) || "[]") as RlTransition[];
    prev.push(row);
    localStorage.setItem(RL_KEY, JSON.stringify(prev));
  } catch {
    /* keep the chat usable if the log cannot grow */
  }
}

export function rlTransitionCount(): number {
  try {
    const prev = JSON.parse(localStorage.getItem(RL_KEY) || "[]") as RlTransition[];
    return prev.length;
  } catch {
    return 0;
  }
}
