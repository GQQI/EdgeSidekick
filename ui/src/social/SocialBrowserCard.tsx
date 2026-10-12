import { useEffect, useRef, useState } from "react";
import { browserFetchScreenshot } from "../api";
import { getDesktop } from "../desktopBridge";

type Props = {
  url: string;
  busy: boolean;
  paused?: boolean;
  onExpand: () => void;
  onClose: () => void;
  /** Detail view: show the whole page instead of a cropped strip. */
  fill?: boolean;
  /** Float window already has a toolbar; hide this card's own header. */
  bare?: boolean;
  /** When set, clicks on the picture go to the page instead of expanding. */
  onPageClick?: (x: number, y: number) => void;
  /** Changes when the float is dragged or resized, so the native page follows the frame. */
  follow?: string;
};

const SHOT_INTERVAL_MS = 2500;

export function BrowserGlyph({ name }: { name: "back" | "pointer" | "check" | "full" | "restore" | "min" | "close" | "globe" }) {
  const common = { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.75, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true as const };
  if (name === "back") return <svg {...common}><path d="M15 6 9 12l6 6" /></svg>;
  if (name === "pointer") return <svg {...common}><path d="M5 3l6.5 16 2.2-6.3L20 10.5 5 3z" /></svg>;
  if (name === "check") return <svg {...common}><path d="M5 12.5 9.5 17 19 7" /></svg>;
  if (name === "full") return <svg {...common}><path d="M8 4H4v4M16 4h4v4M4 16v4h4M20 16v4h-4" /></svg>;
  if (name === "restore") return <svg {...common}><path d="M8 8h8v8H8zM4 10V4h6" /></svg>;
  if (name === "min") return <svg {...common}><path d="M5 12h14" /></svg>;
  if (name === "globe") return <svg {...common}><circle cx="12" cy="12" r="8" /><path d="M4 12h16M12 4c2.5 2.8 2.5 13.2 0 16M12 4c-2.5 2.8-2.5 13.2 0 16" /></svg>;
  return <svg {...common}><path d="M7 7l10 10M17 7 7 17" /></svg>;
}

function hostLabel(url: string) {
  const raw = String(url || "").trim();
  if (!raw) return "";
  try {
    const u = new URL(raw);
    return (u.host || u.hostname || "") + (u.pathname === "/" ? "" : u.pathname || "");
  } catch {
    return raw;
  }
}

/**
 * Mini preview of the in-app browser.
 *
 * Desktop: the same BrowserView that loadURL uses is attached to this card,
 * so the page the agent opened is the picture. Web falls back to screenshots.
 */
export function SocialBrowserCard({ url, busy, paused = false, onExpand, onClose, fill = false, bare = false, onPageClick, follow = "" }: Props) {
  const liveDesktop = Boolean(getDesktop());
  const [shot, setShot] = useState("");
  const [ended, setEnded] = useState(false);
  const shotRef = useRef("");
  const inFlightRef = useRef(false);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const syncBoundsRef = useRef<(() => void) | null>(null);

  // Keep the in-app page sized to this card. A hidden or 1x1 guest is the
  // black screen: the page was loaded, but nothing put it on screen.
  useEffect(() => {
    if (!liveDesktop || paused) return;
    const desktop = getDesktop();
    if (!desktop) return;
    const hasPage = Boolean(url && url !== "about:blank");
    if (!hasPage) return;
    let last = "";
    const sync = () => {
      const el = bodyRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      if (r.width < 8 || r.height < 8) return;
      const next = `${Math.round(r.left)},${Math.round(r.top)},${Math.round(r.width)},${Math.round(r.height)}`;
      if (next === last) return;
      last = next;
      void desktop.browser
        .show({
          x: Math.round(r.left),
          y: Math.round(r.top),
          width: Math.max(40, Math.round(r.width)),
          height: Math.max(40, Math.round(r.height)),
        })
        .catch(() => undefined);
    };
    syncBoundsRef.current = sync;
    sync();
    const ro = new ResizeObserver(sync);
    if (bodyRef.current) ro.observe(bodyRef.current);
    window.addEventListener("resize", sync);
    document.addEventListener("scroll", sync, { capture: true, passive: true });
    const offBounds = desktop.browser.onRequestBounds(sync);
    const t1 = window.setTimeout(sync, 50);
    const t2 = window.setTimeout(sync, 300);
    return () => {
      syncBoundsRef.current = null;
      window.clearTimeout(t1);
      window.clearTimeout(t2);
      ro.disconnect();
      window.removeEventListener("resize", sync);
      document.removeEventListener("scroll", sync, { capture: true });
      offBounds();
      void desktop.browser.hide().catch(() => undefined);
    };
  }, [liveDesktop, paused, url]);

  useEffect(() => {
    syncBoundsRef.current?.();
  }, [follow]);

  useEffect(() => {
    if (liveDesktop) return;
    let cancelled = false;
    let timer: number | null = null;

    const clearTimer = () => {
      if (timer != null) {
        window.clearTimeout(timer);
        timer = null;
      }
    };

    const schedule = () => {
      clearTimer();
      if (cancelled || paused || !busy) return;
      timer = window.setTimeout(() => void tick(), SHOT_INTERVAL_MS);
    };

    const tick = async () => {
      if (cancelled || paused || inFlightRef.current) {
        schedule();
        return;
      }
      inFlightRef.current = true;
      try {
        const next = await browserFetchScreenshot(false, "social");
        if (cancelled) return;
        const prev = shotRef.current;
        shotRef.current = next;
        setShot(next);
        setEnded(false);
        if (prev && prev !== next) URL.revokeObjectURL(prev);
      } catch {
        if (!cancelled) setEnded(true);
      } finally {
        inFlightRef.current = false;
        schedule();
      }
    };

    if (paused) {
      clearTimer();
      return () => {
        cancelled = true;
        clearTimer();
      };
    }

    void tick();
    return () => {
      cancelled = true;
      clearTimer();
    };
  }, [busy, paused, liveDesktop]);

  useEffect(
    () => () => {
      if (shotRef.current) URL.revokeObjectURL(shotRef.current);
    },
    [],
  );

  const label = hostLabel(url);
  const showEnded = ended && !busy && !paused && !liveDesktop;
  const blankUrl = !url || url === "about:blank";
  const desktopHint = blankUrl
    ? "正在打开内置浏览器…"
    : "内置浏览器 — 点击展开";
  const titleText = label
    || (showEnded ? "浏览器已结束" : paused ? "全屏浏览中" : liveDesktop ? "内置浏览器" : "浏览器预览");
  const emptyText = liveDesktop ? desktopHint : showEnded ? "浏览器已关闭" : "正在等待浏览器画面…";

  return (
    <div className={"social-browser-card" + (showEnded ? " ended" : "") + (fill ? " fill" : "")}>
      {bare ? null : (
      <div className="social-browser-card-head">
        <span className={"social-browser-card-dot" + (showEnded || paused ? "" : " live")} aria-hidden />
        <span className="social-browser-card-title" title={url || undefined}>{titleText}</span>
        <button type="button" className="social-browser-card-btn" title="展开全屏" aria-label="展开全屏" onClick={onExpand}>
          <BrowserGlyph name="full" />
        </button>
        <button type="button" className="social-browser-card-btn" title="关闭预览" aria-label="关闭预览" onClick={onClose}>
          <BrowserGlyph name="close" />
        </button>
      </div>
      )}
      <div
        className="social-browser-card-body"
        ref={bodyRef}
        onClick={onPageClick ? undefined : onExpand}
        role="button"
        aria-label="展开浏览器预览"
      >
        {shot ? (
          <img
            src={shot}
            alt="浏览器预览"
            className="social-browser-card-shot"
            draggable={false}
            onClick={onPageClick ? (e) => {
              e.stopPropagation();
              const img = e.currentTarget;
              const rect = img.getBoundingClientRect();
              const nw = img.naturalWidth || rect.width;
              const nh = img.naturalHeight || rect.height;
              const scale = Math.min(rect.width / nw, rect.height / nh) || 1;
              const dw = nw * scale;
              const dh = nh * scale;
              const ox = (rect.width - dw) / 2;
              const oy = (rect.height - dh) / 2;
              const x = (e.clientX - rect.left - ox) / scale;
              const y = (e.clientY - rect.top - oy) / scale;
              if (x < 0 || y < 0 || x > nw || y > nh) return;
              onPageClick(Math.round(x), Math.round(y));
            } : undefined}
          />
        ) : (
          <p className="social-browser-card-empty">{emptyText}</p>
        )}
        {showEnded && shot ? <span className="social-browser-card-flag">已结束</span> : null}
      </div>
    </div>
  );
}

