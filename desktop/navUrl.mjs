import { pathToFileURL } from "node:url";
import path from "node:path";

/** URL normalization / match helpers for the desktop guest browser. */
export function normalizeNavUrl(raw) {
  let target = String(raw || "").trim();
  if (!target) return "";
  if (target === "about:blank") return target;

  // Local HTML: keep CJK in the path (do not strip non-ASCII like http chat junk).
  if (/^file:/i.test(target)) {
    try {
      return new URL(target).href;
    } catch {
      return target;
    }
  }
  if (/^[a-zA-Z]:[\\/]/.test(target) && /\.html?([?#]|$)/i.test(target)) {
    try {
      return pathToFileURL(target.split(/[?#]/)[0]).href;
    } catch {
      return "";
    }
  }

  // Strip chat/markdown junk: http://localhost:5173**，已在/ → http://localhost:5173
  const m = target.match(
    /https?:\/\/[A-Za-z0-9][-A-Za-z0-9._~:/?#\[\]@!$&'()+,;=%]*/i,
  );
  if (m) target = m[0];
  const star = target.search(/\*/);
  if (star >= 0) target = target.slice(0, star);
  const nonAscii = target.search(/[^\x00-\x7F]/);
  if (nonAscii >= 0) target = target.slice(0, nonAscii);
  target = target.replace(/[*),.;:!?，。；！？*_~`]+$/g, "").trim();
  if (!target) return "";

  try {
    const u = new URL(target);
    if (u.protocol !== "http:" && u.protocol !== "https:") {
      return target;
    }
    return u.toString();
  } catch {
    return "";
  }
}

/** Vite/Node on Windows may listen on ::1 XOR 127.0.0.1 — try both families. */
export function loopbackUrlCandidates(url) {
  const first = normalizeNavUrl(url);
  if (!first || first === "about:blank") return first ? [first] : [];
  let parsed;
  try {
    parsed = new URL(first);
  } catch {
    return [first];
  }
  if (parsed.protocol === "file:") return [first];
  const host = parsed.hostname;
  const loopback =
    host === "127.0.0.1" ||
    host === "0.0.0.0" ||
    host === "localhost" ||
    host === "::1";
  if (!loopback) return [first];
  const out = [];
  const seen = new Set();
  const pushHost = (hostname) => {
    const u = new URL(parsed);
    u.hostname = hostname;
    const s = u.toString();
    if (!seen.has(s)) {
      seen.add(s);
      out.push(s);
    }
  };
  pushHost(host === "0.0.0.0" ? "127.0.0.1" : host);
  pushHost("127.0.0.1");
  pushHost("localhost");
  pushHost("::1");
  return out;
}

export function isConnectionRefused(err) {
  const code = String(err?.code || err?.errno || "");
  const msg = String(err?.message || err || "");
  return (
    code === "ERR_CONNECTION_REFUSED" ||
    msg.includes("ERR_CONNECTION_REFUSED") ||
    /econnrefused|connection refused/i.test(msg)
  );
}

export function isAbortedNav(err) {
  const code = String(err?.code || err?.errno || err?.errorCode || "");
  const msg = String(err?.message || err || "");
  return (
    code === "ERR_ABORTED" ||
    code === "-3" ||
    msg.includes("ERR_ABORTED") ||
    /\(\s*-3\s*\)/.test(msg)
  );
}

export function canonicalizeUrl(raw) {
  const text = String(raw || "").trim();
  if (!text || text === "about:blank") return text;
  try {
    const u = new URL(text);
    let host = (u.hostname || "").toLowerCase();
    if (host.startsWith("www.")) host = host.slice(4);
    let path = u.pathname || "/";
    if (path.length > 1 && path.endsWith("/")) path = path.slice(0, -1);
    return `${u.protocol}//${host}${path}${u.search}`;
  } catch {
    return text.replace(/\/+$/, "");
  }
}

export function urlsMatch(a, b) {
  const na = canonicalizeUrl(a);
  const nb = canonicalizeUrl(b);
  return Boolean(na && nb && na === nb);
}

/** True when target is site home and current is a deeper same-host page. */
export function isShallowSiteRewind(current, target) {
  const cur = String(current || "").trim();
  const tgt = String(target || "").trim();
  if (!cur || !tgt) return false;
  if (cur === "about:blank" || cur.startsWith("chrome://")) return false;
  let pc;
  let pt;
  try {
    pc = new URL(cur);
    pt = new URL(tgt);
  } catch {
    return false;
  }
  if (pc.protocol !== "http:" && pc.protocol !== "https:") return false;
  if (pt.protocol !== "http:" && pt.protocol !== "https:") return false;
  let ch = (pc.hostname || "").toLowerCase();
  let th = (pt.hostname || "").toLowerCase();
  if (ch.startsWith("www.")) ch = ch.slice(4);
  if (th.startsWith("www.")) th = th.slice(4);
  if (!ch || ch !== th) return false;
  let cpath = pc.pathname || "/";
  let tpath = pt.pathname || "/";
  if (cpath !== "/" && cpath.endsWith("/")) cpath = cpath.slice(0, -1);
  if (tpath !== "/" && tpath.endsWith("/")) tpath = tpath.slice(0, -1);
  const tgtIsHome = (tpath === "/" || tpath === "") && !(pt.search || "").trim();
  const curIsDeeper = cpath !== "/" && cpath !== "" || Boolean((pc.search || "").trim());
  return Boolean(tgtIsHome && curIsDeeper);
}


export function waitForGuestUrl(view, wants, timeoutMs) {
  const want = (wants || []).map(canonicalizeUrl).filter(Boolean);
  return new Promise((resolve) => {
    const started = Date.now();
    let done = false;
    const tick = () => {
      if (done) return;
      let now = "";
      try {
        now = view.webContents.getURL();
      } catch {
        now = "";
      }
      if (want.some((w) => urlsMatch(now, w))) {
        cleanup();
        resolve(now);
        return;
      }
      if (Date.now() - started >= timeoutMs) {
        cleanup();
        resolve(now);
      }
    };
    // Poll only — do not stack did-stop-loading listeners (MaxListenersExceeded).
    const iv = setInterval(tick, 150);
    const to = setTimeout(tick, timeoutMs + 20);
    function cleanup() {
      if (done) return;
      done = true;
      clearInterval(iv);
      clearTimeout(to);
    }
    tick();
  });
}

let navChain = Promise.resolve();

export function friendlyNavError(err, url) {
  const msg = String(err?.message || err || "");
  if (isAbortedNav(err)) {
    return "";
  }
  if (isConnectionRefused(err)) {
    return (
      `无法连接 ${url}（连接被拒绝）。请先在对应项目里启动开发服务` +
      `（如 npm run dev）。本地预览请用终端里的地址；` +
      `开发服务器应绑定 127.0.0.1（Vite: server.host = "127.0.0.1"）。`
    );
  }
  return msg || "navigation failed";
}

