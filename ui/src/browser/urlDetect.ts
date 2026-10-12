/** Detect / sanitize http(s) URLs in chat text and browser navigation. */

/**
 * ASCII URL body. Brackets and parentheses are not consumed: they are markdown
 * link syntax, and a greedy match was rewriting shell snippets such as
 * `BASE=https://…` into `BASE=[https://…](https://…` with the closing `)` dropped.
 * Balanced parentheses that are part of the URL itself are handled by findHttpUrls.
 */
export const URL_RE =
  /https?:\/\/[A-Za-z0-9][-A-Za-z0-9._~:/?#@!$&'+,;=%]*/gi;

const URL_TRAIL = /[,.;:!?，。；！？*_~`]/;

/** Http(s) spans in `src`. Trailing punctuation and markdown delimiters stay outside the match. */
export function findHttpUrls(src: string): Array<{ index: number; raw: string }> {
  const text = src || "";
  const out: Array<{ index: number; raw: string }> = [];
  const re = /https?:\/\//gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    const start = m.index;
    let i = start + m[0].length;
    if (!/[A-Za-z0-9]/.test(text[i] || "")) continue;
    let depth = 0;
    while (i < text.length) {
      const c = text[i];
      if (c === "(") {
        depth += 1;
        i += 1;
        continue;
      }
      if (c === ")") {
        if (depth === 0) break;
        depth -= 1;
        i += 1;
        continue;
      }
      if (c === "[" || c === "]") break;
      if (!/[-A-Za-z0-9._~:/?#@!$&'+,;=%]/.test(c)) break;
      i += 1;
    }
    while (depth > 0 && text[i - 1] === "(") {
      i -= 1;
      depth -= 1;
    }
    while (i > start + m[0].length && URL_TRAIL.test(text[i - 1])) i -= 1;
    const raw = text.slice(start, i);
    if (raw.length <= m[0].length) continue;
    out.push({ index: start, raw });
    re.lastIndex = i;
  }
  return out;
}

export function normalizeDetectedUrl(raw: string): string {
  let u = (raw || "").trim();
  try {
    u = decodeURIComponent(u);
  } catch {
    /* keep */
  }
  u = u.replace(/[*),.;:!?，。；！？*_~`]+$/g, "");
  return u;
}

/** Display form for localhost:port (no forced trailing slash). */
export function displayHttpUrl(href: string): string {
  try {
    const u = new URL(href);
    if (u.protocol !== "http:" && u.protocol !== "https:") return href;
    if (u.pathname === "/" && !u.search && !u.hash) {
      return `${u.protocol}//${u.host}`;
    }
    return u.toString();
  } catch {
    return href;
  }
}

const PREVIEW_FILE_RE = /\.(html?|pdf)([?#]|$)/i;

/**
 * Workspace-relative, file://, or same-origin path to HTML/PDF — not a remote link.
 */
export function isLocalPreviewTarget(raw: string): boolean {
  const t = String(raw || "").trim();
  if (!t || t === "about:blank") return false;
  if (/^file:/i.test(t)) return PREVIEW_FILE_RE.test(t);
  if (/^https?:/i.test(t)) {
    try {
      const u = new URL(t);
      const origin = typeof window !== "undefined" ? window.location.origin : "";
      return Boolean(origin) && u.origin === origin && PREVIEW_FILE_RE.test(u.pathname);
    } catch {
      return false;
    }
  }
  if (/^[a-z][a-z0-9+.-]*:/i.test(t)) return false;
  return PREVIEW_FILE_RE.test(t);
}

/** @deprecated use isLocalPreviewTarget */
export function isLocalHtmlTarget(raw: string): boolean {
  return isLocalPreviewTarget(raw);
}

/** Workspace-relative path (or file://) to pass to the preview server. */
export function localPreviewPath(raw: string): string {
  const t = String(raw || "").trim();
  if (!t) return "";
  if (/^file:/i.test(t)) return t;
  if (/^https?:/i.test(t)) {
    try {
      const u = new URL(t);
      return decodeURIComponent(u.pathname.replace(/^\/+/, ""));
    } catch {
      return t;
    }
  }
  return t.replace(/^\.\//, "").replace(/^\/+/, "");
}

/**
 * Extract a navigable http(s) URL from chat/markdown junk.
 * "http://localhost:5173**，已在/" → "http://localhost:5173"
 */
export function sanitizeBrowserUrl(raw: string): string {
  let text = String(raw || "").trim();
  if (!text) return "";
  if (text === "about:blank") return text;
  try {
    text = decodeURIComponent(text);
  } catch {
    /* keep */
  }

  const matched = findHttpUrls(text)[0]?.raw;
  let candidate = normalizeDetectedUrl(matched || (/^https?:\/\//i.test(text) ? text : ""));
  if (!candidate) return "";

  const star = candidate.search(/\*/);
  if (star >= 0) candidate = candidate.slice(0, star);
  const nonAscii = candidate.search(/[^\x00-\x7F]/);
  if (nonAscii >= 0) candidate = candidate.slice(0, nonAscii);
  candidate = normalizeDetectedUrl(candidate);
  if (!candidate) return "";

  try {
    const u = new URL(candidate);
    if (u.protocol !== "http:" && u.protocol !== "https:") return "";
    return displayHttpUrl(u.toString());
  } catch {
    return "";
  }
}

export function isHttpUrl(value: string): boolean {
  return Boolean(sanitizeBrowserUrl(value));
}

/**
 * Do not rewrite chat text into markdown links.
 * Inserting `[url](url)` deletes or invents brackets and parentheses, so a
 * shell assignment like `BASE="https://example.com"` was shown as
 * `BASE="[https://example.com](https://example.com` with the closing `)` gone.
 * Links are added later as nodes whose visible text is the original slice.
 */
export function prepMarkdownForUrls(src: string): string {
  return src || "";
}

const FILE_RE = /(^|[\s])((?:\.\/)?[\w./\\\-\u4e00-\u9fff]+\.(?:html?|pdf))(?=$|[\s,，。;；])/gi;

type MdNode = {
  type: string;
  value?: string;
  url?: string;
  children?: MdNode[];
};

/** Spans that may become links. Visible text is always `raw` itself. */
export function literalLinkSpans(src: string): Array<{ index: number; raw: string; href: string }> {
  const text = src || "";
  const spans: Array<{ index: number; raw: string; href: string }> = [];
  for (const hit of findHttpUrls(text)) {
    const href = sanitizeBrowserUrl(hit.raw);
    if (href) spans.push({ index: hit.index, raw: hit.raw, href });
  }
  const fileRe = new RegExp(FILE_RE.source, "gi");
  let m: RegExpExecArray | null;
  while ((m = fileRe.exec(text))) {
    const file = m[2];
    const index = m.index + m[1].length;
    if (spans.some((s) => index < s.index + s.raw.length && index + file.length > s.index)) continue;
    spans.push({ index, raw: file, href: file });
  }
  spans.sort((a, b) => a.index - b.index);
  return spans;
}

function splitTextNode(value: string): MdNode[] {
  const spans = literalLinkSpans(value);
  if (!spans.length) return [{ type: "text", value }];
  const out: MdNode[] = [];
  let last = 0;
  for (const span of spans) {
    if (span.index < last) continue;
    if (span.index > last) out.push({ type: "text", value: value.slice(last, span.index) });
    out.push({
      type: "link",
      url: span.href,
      children: [{ type: "text", value: span.raw }],
    });
    last = span.index + span.raw.length;
  }
  if (last < value.length) out.push({ type: "text", value: value.slice(last) });
  return out;
}

const SKIP_LINKIFY = new Set(["code", "inlineCode", "link", "linkReference", "definition"]);

function linkifyNode(node: MdNode) {
  if (!node.children || SKIP_LINKIFY.has(node.type)) return;
  const next: MdNode[] = [];
  for (const child of node.children) {
    if (child.type === "text" && typeof child.value === "string") {
      next.push(...splitTextNode(child.value));
    } else {
      linkifyNode(child);
      next.push(child);
    }
  }
  node.children = next;
}

/** Remark plugin: wrap URLs and local html/pdf in links without changing characters. */
export function remarkLinkifyLiteral() {
  return (tree: MdNode) => {
    linkifyNode(tree);
  };
}

/**
 * If anchor label contains an http(s) URL (possibly with markdown/CJK junk),
 * return clean href + how to render before/url/after.
 */
export function splitDirtyUrlLabel(label: string): {
  href: string;
  text: string;
  before: string;
  after: string;
} | null {
  const src = label || "";
  if (!src.trim()) return null;
  const hit = findHttpUrls(src)[0];
  if (!hit) return null;
  const href = sanitizeBrowserUrl(hit.raw);
  if (!href) return null;
  return {
    href,
    text: hit.raw,
    before: src.slice(0, hit.index),
    after: src.slice(hit.index + hit.raw.length),
  };
}

/** Ctrl/Cmd+click or right-click http(s); any click on local HTML/PDF → sandbox prompt. */
export function sandboxUrlGesture(
  url: string | undefined,
  e: {
    ctrlKey: boolean;
    metaKey: boolean;
    clientX: number;
    clientY: number;
    preventDefault: () => void;
    stopPropagation: () => void;
  },
  onOpen: ((url: string, clientX: number, clientY: number) => void) | undefined,
  opts: { mode: "click" | "contextmenu" },
): boolean {
  const raw = url || "";
  const local = isLocalPreviewTarget(raw) ? localPreviewPath(raw) : "";
  const clean = local || sanitizeBrowserUrl(raw);
  if (!clean || !onOpen) return false;
  if (opts.mode === "click" && !local && !(e.ctrlKey || e.metaKey)) return false;
  e.preventDefault();
  e.stopPropagation();
  onOpen(clean, e.clientX, e.clientY);
  return true;
}

export type TextSegment = { type: "text"; value: string } | { type: "url"; value: string; href: string };

export function splitTextWithUrls(text: string): TextSegment[] {
  const src = text || "";
  if (!src) return [];
  const out: TextSegment[] = [];
  let last = 0;
  for (const hit of findHttpUrls(src)) {
    const start = hit.index;
    if (start > last) out.push({ type: "text", value: src.slice(last, start) });
    const href = sanitizeBrowserUrl(hit.raw);
    if (href) {
      // Keep the original characters on screen. The href is only for navigation.
      out.push({ type: "url", value: hit.raw, href: displayHttpUrl(href) });
    } else {
      out.push({ type: "text", value: hit.raw });
    }
    last = start + hit.raw.length;
  }
  if (last < src.length) out.push({ type: "text", value: src.slice(last) });
  return out.length ? out : [{ type: "text", value: src }];
}
