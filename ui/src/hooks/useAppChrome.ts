import { useEffect, useLayoutEffect, useRef } from "react";
import {
  saveExplorerCollapsed,
  saveExplorerWidth,
  saveSidePanel,
  type SidePanel,
} from "../layoutPersist";
import type { ApprovalPrompt, AskPrompt, DetailView } from "../types/chat";
import type { PlanConfirmState } from "../types/plan";
import type { ChatMsg } from "../types/chat";

type CompressState = {
  active: boolean;
  message: string;
  attempt: number;
  maxAttempts: number;
  before: number;
  after?: number;
} | null;

type Deps = {
  sidePanel: SidePanel;
  explorerCollapsed: boolean;
  explorerWidth: number;
  setExplorerCollapsed: (v: boolean | ((p: boolean) => boolean)) => void;
  setExplorerWidth: (v: number | ((p: number) => number)) => void;
  setDetailWidth: (v: number | ((p: number) => number)) => void;
  resizingRef: React.MutableRefObject<boolean>;
  resizingDetailRef: React.MutableRefObject<boolean>;
  stickBottomRef: React.MutableRefObject<boolean>;
  threadRef: React.RefObject<HTMLDivElement | null>;
  bottomRef: React.RefObject<HTMLDivElement | null>;
  composerRef: React.RefObject<HTMLTextAreaElement | null>;
  messages: ChatMsg[];
  busy: boolean;
  compressState: CompressState;
  bootReady: boolean;
  contextLimit?: number;
  /** When a session is open, its own limit (from /api/sessions and runtime events) wins. */
  hasSession: boolean;
  setCtx: React.Dispatch<React.SetStateAction<{ tokens: number; limit: number }>>;
  toast: string;
  setToast: (msg: string) => void;
  approval: ApprovalPrompt | null;
  askPrompt: AskPrompt | null;
  planConfirm: PlanConfirmState | null;
  settingsOpen: boolean;
  setSettingsOpen: (v: boolean) => void;
  detail: DetailView;
  setDetail: React.Dispatch<React.SetStateAction<DetailView>>;
  input: string;
  setInput: React.Dispatch<React.SetStateAction<string>>;
  sessionsPage: number;
  onNewChat: () => void;
  onOpenHistory: () => void;
  onOpenSettings: () => void;
};

export function useAppChrome(d: Deps) {
  useEffect(() => {
    saveSidePanel(d.sidePanel);
  }, [d.sidePanel]);

  useEffect(() => {
    if (d.sidePanel === "browser" && !d.explorerCollapsed) {
      d.setExplorerWidth((w) => (w < 520 ? 640 : w));
    }
  }, [d.sidePanel, d.explorerCollapsed]);

  useEffect(() => {
    saveExplorerCollapsed(d.explorerCollapsed);
  }, [d.explorerCollapsed]);

  useEffect(() => {
    saveExplorerWidth(d.explorerWidth);
  }, [d.explorerWidth]);

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 768px)");
    const apply = () => {
      if (mq.matches) d.setExplorerCollapsed(true);
    };
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, []);

  useEffect(() => {
    // /api/health reports the *global* settings' window. A live session may run
    // a different model (auto-routing, per-session switch), so only use the
    // global value as a placeholder until a session supplies its own limit.
    if (d.contextLimit && !d.hasSession) {
      d.setCtx((c) => ({ ...c, limit: d.contextLimit || c.limit }));
    }
  }, [d.contextLimit, d.hasSession]);

  const programmaticScrollRef = useRef(false);
  const suppressPinRef = useRef(false);

  const scrollThreadToBottom = () => {
    const el = d.threadRef.current;
    if (!el) {
      d.bottomRef.current?.scrollIntoView({ behavior: "auto", block: "end" });
      return;
    }
    programmaticScrollRef.current = true;
    el.scrollTop = el.scrollHeight;
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        programmaticScrollRef.current = false;
      });
    });
  };

  const followIfPinned = () => {
    if (!d.stickBottomRef.current) return;
    scrollThreadToBottom();
  };

  const lastMsgFingerprint = (() => {
    const last = d.messages[d.messages.length - 1];
    if (!last) return "";
    return `${last.id}:${String((last as any).text || (last as any).content || "").length}:${d.busy ? 1 : 0}`;
  })();

  useLayoutEffect(() => {
    followIfPinned();
  }, [d.messages, d.busy, d.compressState?.active, lastMsgFingerprint]);

  useEffect(() => {
    const el = d.threadRef.current;
    if (!el) return;
    const inner = el.querySelector(".thread-inner") || el.firstElementChild;
    const ro = new ResizeObserver(() => followIfPinned());
    ro.observe(el);
    if (inner && inner !== el) ro.observe(inner);
    return () => ro.disconnect();
  }, [d.bootReady, d.messages.length]);

  useEffect(() => {
    const el = d.threadRef.current;
    if (!el) return;
    let pointerDown = false;
    const unpinIfUp = (deltaY: number) => {
      if (deltaY < 0) {
        d.stickBottomRef.current = false;
        suppressPinRef.current = true;
      }
    };
    const onWheel = (e: WheelEvent) => unpinIfUp(e.deltaY);
    let touchY: number | null = null;
    const onTouchStart = (e: TouchEvent) => {
      touchY = e.touches[0]?.clientY ?? null;
    };
    const onTouchMove = (e: TouchEvent) => {
      const y = e.touches[0]?.clientY;
      if (touchY != null && y != null) unpinIfUp(touchY - y);
      touchY = y ?? touchY;
    };
    const onPointerDown = () => {
      pointerDown = true;
    };
    const onPointerUp = () => {
      pointerDown = false;
    };
    const onScroll = () => {
      if (programmaticScrollRef.current) return;
      const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
      const atBottom = dist <= 72;
      if (pointerDown && atBottom) {
        suppressPinRef.current = false;
        d.stickBottomRef.current = true;
        return;
      }
      if (suppressPinRef.current) {
        suppressPinRef.current = false;
        d.stickBottomRef.current = false;
        return;
      }
      d.stickBottomRef.current = atBottom;
    };
    el.addEventListener("wheel", onWheel, { passive: true });
    el.addEventListener("touchstart", onTouchStart, { passive: true });
    el.addEventListener("touchmove", onTouchMove, { passive: true });
    el.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("pointerup", onPointerUp);
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("touchstart", onTouchStart);
      el.removeEventListener("touchmove", onTouchMove);
      el.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("pointerup", onPointerUp);
      el.removeEventListener("scroll", onScroll);
    };
  }, [d.bootReady, d.hasSession, d.messages.length]);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (d.resizingRef.current) {
        const maxW = Math.min(1200, Math.max(520, Math.floor(window.innerWidth * 0.72)));
        const next = e.clientX - 64;
        if (next < 120) {
          d.setExplorerCollapsed(true);
        } else {
          d.setExplorerWidth(Math.min(maxW, Math.max(200, next)));
          d.setExplorerCollapsed(false);
        }
      }
      if (d.resizingDetailRef.current) {
        const fromRight = window.innerWidth - e.clientX - 12;
        const next = Math.min(720, Math.max(280, fromRight));
        d.setDetailWidth(next);
      }
    };
    const onUp = () => {
      d.resizingRef.current = false;
      d.resizingDetailRef.current = false;
      document.body.classList.remove("resizing-sidebar");
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  const chromeRef = useRef(d);
  chromeRef.current = d;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const d = chromeRef.current;
      const mod = e.metaKey || e.ctrlKey;
      const target = e.target as HTMLElement | null;
      const typing =
        target &&
        (target.tagName === "TEXTAREA" ||
          target.tagName === "INPUT" ||
          target.isContentEditable);

      if (e.key === "Escape") {
        if (d.approval || d.askPrompt || d.planConfirm) return;
        if (d.settingsOpen) {
          d.setSettingsOpen(false);
          return;
        }
        if (d.sidePanel === "history" && !d.explorerCollapsed) {
          d.setExplorerCollapsed(true);
          return;
        }
        if (d.detail) {
          if (d.detail.type === "changes" && d.detail.selectedPath) {
            d.setDetail({ type: "changes", selectedPath: null });
            return;
          }
          d.setDetail(null);
          return;
        }
        if (d.input.startsWith("/")) {
          d.setInput("");
          return;
        }
      }

      if (mod && e.key.toLowerCase() === "n") {
        e.preventDefault();
        d.onNewChat();
        return;
      }
      if (mod && e.key.toLowerCase() === "k") {
        e.preventDefault();
        d.onOpenHistory();
        return;
      }
      if (mod && e.key === ",") {
        e.preventDefault();
        d.onOpenSettings();
        return;
      }
      if (mod && e.key === "/") {
        e.preventDefault();
        d.composerRef.current?.focus();
        d.setInput((v) => (v.startsWith("/") ? v : "/"));
        return;
      }
      if (!typing && e.key === "/" && !mod && !e.altKey) {
        e.preventDefault();
        d.composerRef.current?.focus();
        d.setInput("/");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!d.toast) return;
    const t = window.setTimeout(() => d.setToast(""), 5000);
    return () => window.clearTimeout(t);
  }, [d.toast]);
}
