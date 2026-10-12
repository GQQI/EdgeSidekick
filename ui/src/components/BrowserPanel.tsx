import { useCallback, useEffect, useRef, useState } from "react";
import {
  browserClose,
  browserFetchScreenshot,
  browserNavigate,
  browserPreviewLocal,
  browserSelect,
  browserSelectCancel,
  browserStart,
  browserStatus,
} from "../api";
import { parseDomElement, type DomElementPayload } from "../browser/protocol";
import { isLocalPreviewTarget, sanitizeBrowserUrl } from "../browser/urlDetect";
import { getDesktop, type DesktopBounds } from "../desktopBridge";
import { usePrefs } from "../prefs";
import { IconChevronRight, IconRefresh, IconX } from "./icons";

export type BrowserOpenRequest = {
  url: string;
  nonce: number;
  /** App already called desktop.browser.navigate — panel must not queue another loadURL. */
  alreadyDriven?: boolean;
};

type Props = {
  onPickElement: (el: DomElementPayload) => void;
  openRequest?: BrowserOpenRequest | null;
  /**
   * True while a modal dialog (Settings, prompts, ...) is open. The live
   * preview is a native OS-level view painted above the DOM, so a React
   * modal can never cover it — we must hide it ourselves and restore it
   * once the dialog closes.
   */
  suspended?: boolean;
};

function readBounds(el: HTMLElement | null): DesktopBounds | null {
  if (!el) return null;
  const r = el.getBoundingClientRect();
  if (r.width < 8 || r.height < 8) return null;
  return {
    x: Math.round(r.left),
    y: Math.round(r.top),
    width: Math.max(40, Math.round(r.width)),
    height: Math.max(40, Math.round(r.height)),
  };
}

export function BrowserPanel({ onPickElement, openRequest, suspended = false }: Props) {
  const { t } = usePrefs();
  const desktop = getDesktop();
  const live = Boolean(desktop);

  const [url, setUrl] = useState("");
  const [sessionUrl, setSessionUrl] = useState("");
  const [available, setAvailable] = useState(true);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState(false);
  const [shotBust, setShotBust] = useState(0);
  const [shotSrc, setShotSrc] = useState("");
  const pollRef = useRef<number | null>(null);
  const shotSrcRef = useRef("");
  const sessionUrlRef = useRef("");
  const hostRef = useRef<HTMLDivElement | null>(null);
  const openSeqRef = useRef(0);
  const suspendedRef = useRef(suspended);
  suspendedRef.current = suspended;
  sessionUrlRef.current = sessionUrl;

  const syncBounds = useCallback(async () => {
    if (!desktop) return;
    // Settings and other dialogs sit in the page. The native view paints
    // above them, so a delayed show() while suspended is the black curtain.
    if (suspendedRef.current) {
      await desktop.browser.hide();
      return;
    }
    const bounds = readBounds(hostRef.current);
    if (!bounds) return;
    // Always show() with real bounds. setBounds alone is a no-op while the
    // BrowserView is hidden, which left social modal mounts stuck at the
    // startup 1x1 guest until a later sessionUrl flip.
    await desktop.browser.show(bounds);
  }, [desktop]);

  const showLive = useCallback(async () => {
    if (!desktop) return;
    if (suspendedRef.current) {
      await desktop.browser.hide();
      return;
    }
    const bounds = readBounds(hostRef.current);
    if (!bounds) return;
    await desktop.browser.show(bounds);
  }, [desktop]);

  const refreshStatus = useCallback(async () => {
    if (live && desktop) {
      setAvailable(true);
      try {
        const cur = await desktop.browser.getUrl();
        if (cur && cur !== "about:blank") {
          setSessionUrl(cur);
          setUrl(cur);
        }
      } catch {
        /* ignore */
      }
      return;
    }
    try {
      const st = await browserStatus();
      setAvailable(st.available);
      if (st.message) setMessage(st.message);
      if (st.session?.url) {
        setSessionUrl(st.session.url);
        if (st.session.url !== "about:blank") setUrl(st.session.url);
      } else setSessionUrl("");
    } catch (e) {
      setAvailable(false);
      setMessage(e instanceof Error ? e.message : String(e));
    }
  }, [desktop, live]);

  const refreshShot = useCallback(async () => {
    if (live || !sessionUrlRef.current) return;
    try {
      const next = await browserFetchScreenshot(false);
      const prev = shotSrcRef.current;
      shotSrcRef.current = next;
      setShotSrc(next);
      if (prev) URL.revokeObjectURL(prev);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    }
  }, [live]);

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  // Live BrowserView lifecycle + bounds sync.
  useEffect(() => {
    if (!live || !desktop) return;
    if (suspended) {
      void desktop.browser.hide();
      return;
    }
    void showLive();
    const ro = new ResizeObserver(() => {
      void syncBounds();
    });
    if (hostRef.current) ro.observe(hostRef.current);
    const onWin = () => void syncBounds();
    window.addEventListener("resize", onWin);
    const offBounds = desktop.browser.onRequestBounds(() => {
      void syncBounds();
    });
    const offNav = desktop.browser.onNavigated((next) => {
      if (!next || next === "about:blank") return;
      if (next && next !== "about:blank") setSessionUrl(next);
      setUrl(next);
      setMessage("");
      // No auto-scroll here: it fired on every did-navigate / redirect /
      // in-page event, so pages like baidu.com visibly jumped to the bottom
      // right after loading — reads as random self-scrolling to the user.
    });
    const offFail = desktop.browser.onLoadFailure((info) => {
      setMessage(`${t("browserLoadFail")}: ${info.description || info.code} (${info.url})`);
    });
    // Layout settles after paint (social modal mounts late; retry show).
    const timers = [0, 50, 120, 300, 800].map((ms) =>
      window.setTimeout(() => void syncBounds(), ms),
    );
    const raf = window.requestAnimationFrame(() => void syncBounds());
    return () => {
      window.cancelAnimationFrame(raf);
      for (const id of timers) window.clearTimeout(id);
      ro.disconnect();
      window.removeEventListener("resize", onWin);
      offBounds();
      offNav();
      offFail();
      // The native view paints above the page. Leaving it up covers the next
      // sidebar. Hide only; do not navigate, so coming back keeps the page.
      void desktop.browser.hide();
    };
  }, [live, desktop, showLive, syncBounds, t, suspended]);

  // Non-live (no Electron BrowserView): poll screenshots on a fixed cadence,
  // skipping ticks while a fetch is still in flight so jobs never pile up.
  const shotInFlightRef = useRef(false);
  useEffect(() => {
    if (live) return;
    if (!sessionUrl) {
      if (pollRef.current) window.clearTimeout(pollRef.current);
      pollRef.current = null;
      if (shotSrcRef.current) {
        URL.revokeObjectURL(shotSrcRef.current);
        shotSrcRef.current = "";
        setShotSrc("");
      }
      return;
    }
    let cancelled = false;
    const INTERVAL = 2000;
    const tick = async () => {
      if (cancelled || shotInFlightRef.current) {
        if (!cancelled) pollRef.current = window.setTimeout(() => void tick(), INTERVAL);
        return;
      }
      shotInFlightRef.current = true;
      try {
        await refreshShot();
      } finally {
        shotInFlightRef.current = false;
        if (!cancelled) pollRef.current = window.setTimeout(() => void tick(), INTERVAL);
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (pollRef.current) window.clearTimeout(pollRef.current);
      pollRef.current = null;
    };
  }, [sessionUrl, refreshShot, live]);

  async function openTarget(targetRaw: string) {
    const trimmed = targetRaw.trim() || "about:blank";
    const http =
      trimmed === "about:blank" ? trimmed : sanitizeBrowserUrl(trimmed);
    const local = trimmed !== "about:blank" && !http && isLocalPreviewTarget(trimmed);
    if (trimmed !== "about:blank" && !http && !local) {
      setMessage(`无效地址（已剥离 markdown/中文残留）: ${trimmed}`);
      return;
    }
    let target = http || trimmed;
    const seq = ++openSeqRef.current;
    setUrl(target);
    setBusy(true);
    setMessage(live ? t("browserOpeningLive") : "");
    try {
      if (local) {
        const info = await browserPreviewLocal(trimmed);
        target = info.url;
      }
      if (desktop) {
        await showLive();
        await syncBounds();
        try {
          const cur = await desktop.browser.getUrl();
          if (cur && cur !== "about:blank" && sameHttpUrl(cur, target)) {
            if (seq !== openSeqRef.current) return;
            setSessionUrl(cur);
            setUrl(cur);
            setMessage("");
            await syncBounds();
            return;
          }
          // Stale openRequest (homepage) after search must not loadURL home again.
          if (cur && cur !== "about:blank" && isShallowSiteRewind(cur, target)) {
            if (seq !== openSeqRef.current) return;
            setSessionUrl(cur);
            setUrl(cur);
            setMessage("");
            await syncBounds();
            return;
          }
        } catch {
          /* navigate below */
        }
        const info = await desktop.browser.navigate(target);
        if (seq !== openSeqRef.current) return;
        setSessionUrl(info.url);
        setUrl(info.url);
        setMessage("");
        await syncBounds();
      } else {
        const info = sessionUrlRef.current
          ? await browserNavigate(target)
          : await browserStart(target, false);
        if (seq !== openSeqRef.current) return;
        setSessionUrl(info.url);
        setUrl(info.url);
        setShotBust(Date.now());
        setMessage("");
      }
    } catch (e) {
      if (seq !== openSeqRef.current) return;
      const msg = e instanceof Error ? e.message : String(e);
      if (desktop && /\(\s*-3\s*\)|ERR_ABORTED/i.test(msg)) {
        try {
          const cur = await desktop.browser.getUrl();
          if (cur && cur !== "about:blank") {
            setSessionUrl(cur);
            setUrl(cur);
            setMessage("");
            await syncBounds();
            return;
          }
        } catch {
          /* fall through */
        }
      }
      setMessage(msg);
    } finally {
      if (seq === openSeqRef.current) setBusy(false);
    }
  }

  async function startOrGo() {
    await openTarget(url);
  }

  // A modal dialog opened above us — the native BrowserView still paints on
  // top of it, so hide it while the dialog is up and restore afterwards.
  useEffect(() => {
    if (!live || !desktop) return;
    if (suspended) {
      void desktop.browser.hide();
      return;
    }
    if (sessionUrlRef.current) {
      void showLive().then(() => void syncBounds());
    }
  }, [suspended, live, desktop, showLive, syncBounds]);

  useEffect(() => {
    // Reveal-only bumps may omit url (keep current guest page). Still need nonce.
    if (!openRequest?.nonce) return;
    if (!openRequest?.url && !openRequest.alreadyDriven) return;
    // App.tsx may already have called desktop.browser.navigate for this
    // request (social sheet / panel not mounted yet). A second navigate is
    // queued on Electron's navChain and often fires AFTER the agent has
    // submitted a search — rewinding results back to the homepage.
    if (openRequest.alreadyDriven) {
      void (async () => {
        try {
          // Modal host may still be 0x0 on first paint — retry show.
          for (const ms of [0, 50, 150, 400]) {
            if (ms) await new Promise((r) => window.setTimeout(r, ms));
            await showLive();
            await syncBounds();
          }
          if (desktop) {
            const cur = await desktop.browser.getUrl();
            if (cur && cur !== "about:blank") {
              setSessionUrl(cur);
              setUrl(cur);
            } else if (openRequest.url && openRequest.url !== "about:blank") {
              setSessionUrl(openRequest.url);
              setUrl(openRequest.url);
            }
          } else {
            setSessionUrl(openRequest.url);
            setUrl(openRequest.url);
          }
          setMessage("");
          await syncBounds();
        } catch {
          /* ignore — live URL events will catch up */
        }
      })();
      return;
    }
    void openTarget(openRequest.url);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openRequest?.nonce]);

  async function closeSession() {
    setBusy(true);
    try {
      if (desktop) {
        await desktop.browser.navigate("about:blank");
        await desktop.browser.hide();
        setSessionUrl("");
        setMessage("");
      } else {
        await browserClose();
        setSessionUrl("");
        setShotBust(0);
      }
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function armSelect() {
    if (!sessionUrl) await startOrGo();
    setPicking(true);
    setMessage(t("browserSelectHint"));
    try {
      if (desktop) {
        await syncBounds();
        const raw = await desktop.browser.selectArm(90000);
        const el = raw ? parseDomElement(raw) : null;
        if (el) onPickElement(el);
        setMessage(el ? t("browserSelectOk") : t("browserSelectCancel"));
      } else {
        const res = await browserSelect(90000, true);
        if (res.ok && res.element) {
          const el = parseDomElement(res.element);
          if (el) onPickElement(el);
          setMessage(t("browserSelectOk"));
        } else setMessage(res.message || t("browserSelectCancel"));
      }
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setPicking(false);
      if (!live) setShotBust(Date.now());
    }
  }

  async function cancelSelect() {
    try {
      if (desktop) await desktop.browser.selectCancel();
      else await browserSelectCancel();
    } catch {
      /* ignore */
    }
    setPicking(false);
    setMessage(t("browserSelectCancel"));
  }

  return (
    <div className="side-panel browser-panel">
      <div className="side-panel-head">
        <strong>{t("navBrowser")}</strong>
        <span className={`browser-mode-badge${live ? " live" : ""}`}>
          {live ? t("browserModeLive") : t("browserModeShot")}
        </span>
        <div className="side-panel-head-actions">
          {live ? (
            <>
              <button
                type="button"
                className="icon-btn browser-nav-back"
                title={t("browserBack")}
                disabled={busy}
                onClick={() => void desktop?.browser.goBack()}
              >
                <IconChevronRight size={16} />
              </button>
              <button
                type="button"
                className="icon-btn"
                title={t("browserForward")}
                disabled={busy}
                onClick={() => void desktop?.browser.goForward()}
              >
                <IconChevronRight size={16} />
              </button>
            </>
          ) : null}
          <button
            type="button"
            className="icon-btn"
            title={t("browserRefreshShot")}
            disabled={!sessionUrl || busy}
            onClick={() => {
              if (desktop) void desktop.browser.reload();
              else setShotBust(Date.now());
            }}
          >
            <IconRefresh size={16} />
          </button>
          {sessionUrl ? (
            <button
              type="button"
              className="icon-btn"
              title={t("browserClose")}
              disabled={busy}
              onClick={() => void closeSession()}
            >
              <IconX size={16} />
            </button>
          ) : null}
        </div>
      </div>

      {!live && (
        <p className="browser-host-hint muted">{t("browserHostHint")}</p>
      )}

      <form
        className="browser-url-row"
        onSubmit={(e) => {
          e.preventDefault();
          void startOrGo();
        }}
      >
        <input
          className="browser-url-input"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="http://127.0.0.1:3000"
          spellCheck={false}
        />
        <button type="submit" className="chip action" disabled={busy || picking}>
          {sessionUrl ? t("browserGo") : t("browserStart")}
        </button>
      </form>

      <div className="browser-actions">
        <button
          type="button"
          className={`chip action${picking ? " warn" : ""}`}
          disabled={busy || (!available && !sessionUrl && !live)}
          onClick={() => void (picking ? cancelSelect() : armSelect())}
        >
          {picking ? t("browserSelecting") : t("browserSelect")}
        </button>
      </div>

      {!available && !live && (
        <p className="browser-warn">{message || t("browserUnavailable")}</p>
      )}
      {message ? <p className="hint browser-msg">{message}</p> : null}

      <div
        className={`browser-preview${live ? " browser-preview-live" : ""}${
          live && sessionUrl ? " has-session" : ""
        }`}
        ref={hostRef}
      >
        {live ? (
          <p className="hint browser-live-placeholder">
            {sessionUrl ? t("browserLiveActive") : t("browserPreviewEmpty")}
          </p>
        ) : shotSrc ? (
          <img src={shotSrc} alt={t("browserPreviewAlt")} className="browser-shot" />
        ) : (
          <p className="hint">{t("browserPreviewEmpty")}</p>
        )}
      </div>

      {sessionUrl && sessionUrl !== "about:blank" ? (
        <p className="hint browser-session-url" title={sessionUrl}>
          {chipLabelSession(sessionUrl)}
        </p>
      ) : null}
    </div>
  );
}

function sameHttpUrl(a: string, b: string): boolean {
  const norm = (raw: string) => {
    try {
      const u = new URL(raw);
      let host = (u.hostname || "").toLowerCase();
      if (host.startsWith("www.")) host = host.slice(4);
      let path = u.pathname || "/";
      if (path.length > 1 && path.endsWith("/")) path = path.slice(0, -1);
      return `${u.protocol}//${host}${path}${u.search}`;
    } catch {
      return String(raw || "").replace(/\/+$/, "");
    }
  };
  const na = norm(a);
  const nb = norm(b);
  return Boolean(na && nb && na === nb);
}

/** Skip navigations that would rewind results/article pages to the site home. */
function isShallowSiteRewind(current: string, target: string): boolean {
  const cur = (current || "").trim();
  const tgt = (target || "").trim();
  if (!cur || !tgt) return false;
  if (cur === "about:blank" || cur.startsWith("chrome://")) return false;
  try {
    const pc = new URL(cur);
    const pt = new URL(tgt);
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
    const curIsDeeper =
      (cpath !== "/" && cpath !== "") || Boolean((pc.search || "").trim());
    return Boolean(tgtIsHome && curIsDeeper);
  } catch {
    return false;
  }
}


function chipLabelSession(u: string) {
  const raw = String(u || "").trim();
  if (!raw || raw === "about:blank") return "";
  try {
    const parsed = new URL(raw);
    if (parsed.protocol === "about:") return "";
    const host = parsed.host || parsed.hostname || "";
    const path = parsed.pathname === "/" ? "" : parsed.pathname;
    return (host + path) || raw;
  } catch {
    return raw;
  }
}

export type { DomElementPayload };
