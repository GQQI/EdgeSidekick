/** Incremental splitter for <think>…</think> / <thinking>…</thinking> in streamed content. */

const OPEN_RE = /<(think|thinking|thought)\b[^>]*>/i;
const CLOSE_RE = /<\/(think|thinking|thought)\s*>/i;
const OPEN_NAMES = ["think", "thinking", "thought"];
const OPEN_PREFIXES = [
  "<",
  "<t",
  "<th",
  "<thi",
  "<thin",
  "<think",
  "<thought",
  "<thinking",
  "<think>",
  "<thought>",
  "<thinking>",
];
const CLOSE_PREFIXES = [
  "<",
  "</",
  "</t",
  "</th",
  "</thi",
  "</thin",
  "</think",
  "</thought",
  "</thinking",
  "</think>",
  "</thought>",
  "</thinking>",
];

function partialSuffixLen(buf: string, prefixes: string[]): number {
  if (!buf) return 0;
  const low = buf.toLowerCase();
  let maxKeep = 0;
  const n = Math.min(low.length, 12);
  for (let i = 1; i <= n; i++) {
    const suffix = low.slice(-i);
    if (prefixes.some((p) => p.startsWith(suffix))) maxKeep = i;
  }
  return maxKeep;
}

function incompleteOpenHold(buf: string): number {
  if (!buf) return 0;
  const low = buf.toLowerCase();
  const idx = low.lastIndexOf("<");
  if (idx < 0) return partialSuffixLen(buf, OPEN_PREFIXES);
  const tail = low.slice(idx);
  if (tail.includes(">")) return partialSuffixLen(buf, OPEN_PREFIXES);
  const rest = tail.slice(1);
  if (OPEN_NAMES.some((name) => name.startsWith(rest) || rest.startsWith(name))) {
    return buf.length - idx;
  }
  return partialSuffixLen(buf, OPEN_PREFIXES);
}

export type ThinkPiece = { kind: "content" | "reasoning"; text: string };

export class ThinkTagSplitter {
  private buf = "";
  private inThink = false;

  feed(text: string): ThinkPiece[] {
    if (!text) return [];
    this.buf += text;
    const out: ThinkPiece[] = [];
    while (true) {
      if (!this.inThink) {
        const m = OPEN_RE.exec(this.buf);
        if (!m) {
          const keep = incompleteOpenHold(this.buf);
          const emit = keep ? this.buf.slice(0, -keep) : this.buf;
          this.buf = keep ? this.buf.slice(-keep) : "";
          if (emit) out.push({ kind: "content", text: emit });
          break;
        }
        const before = this.buf.slice(0, m.index);
        if (before) out.push({ kind: "content", text: before });
        this.buf = this.buf.slice(m.index + m[0].length);
        this.inThink = true;
      } else {
        const m = CLOSE_RE.exec(this.buf);
        if (!m) {
          const keep = partialSuffixLen(this.buf, CLOSE_PREFIXES);
          const emit = keep ? this.buf.slice(0, -keep) : this.buf;
          this.buf = keep ? this.buf.slice(-keep) : "";
          if (emit) out.push({ kind: "reasoning", text: emit });
          break;
        }
        const inside = this.buf.slice(0, m.index);
        if (inside) out.push({ kind: "reasoning", text: inside });
        this.buf = this.buf.slice(m.index + m[0].length);
        this.inThink = false;
      }
    }
    return out;
  }

  flush(): ThinkPiece[] {
    if (!this.buf) return [];
    const kind = this.inThink ? "reasoning" : "content";
    const text = this.buf;
    this.buf = "";
    return text ? [{ kind, text }] : [];
  }

  reset() {
    this.buf = "";
    this.inThink = false;
  }
}

/** Non-streaming split of a complete string. */
export function splitThinkTags(text: string): { content: string; reasoning: string } {
  const s = new ThinkTagSplitter();
  const content: string[] = [];
  const reasoning: string[] = [];
  for (const p of [...s.feed(text), ...s.flush()]) {
    if (p.kind === "reasoning") reasoning.push(p.text);
    else content.push(p.text);
  }
  return { content: content.join(""), reasoning: reasoning.join("") };
}
