import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";

/**
 * Stick to bottom while the user is at (or returns to) the bottom.
 * Scrolling up unpins; scrolling back to the bottom re-pins.
 */
export function useStickToBottom(
  scrollerRef: RefObject<HTMLElement | null>,
  deps: unknown[],
  opts?: { threshold?: number; enabled?: boolean },
) {
  const stickRef = useRef(true);
  const programmaticRef = useRef(false);
  const suppressPinRef = useRef(false);
  const pointerDownRef = useRef(false);
  const threshold = opts?.threshold ?? 48;
  const enabled = opts?.enabled !== false;

  const scrollToBottom = () => {
    const el = scrollerRef.current;
    if (!el || pointerDownRef.current) return;
    programmaticRef.current = true;
    el.scrollTop = el.scrollHeight;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        programmaticRef.current = false;
      });
    });
  };

  useLayoutEffect(() => {
    if (!enabled) return;
    if (!stickRef.current) return;
    scrollToBottom();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el || !enabled) return;
    const inner = el.firstElementChild;
    const ro = new ResizeObserver(() => {
      if (stickRef.current) scrollToBottom();
    });
    ro.observe(el);
    if (inner && inner !== el) ro.observe(inner);
    return () => ro.disconnect();
  }, [enabled, scrollerRef]);

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el || !enabled) return;
    let pointerDown = false;
    const unpinIfUp = (deltaY: number) => {
      if (deltaY < 0) {
        stickRef.current = false;
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
      pointerDownRef.current = true;
    };
    const onPointerUp = () => {
      pointerDown = false;
      pointerDownRef.current = false;
    };
    const onScroll = () => {
      if (programmaticRef.current) return;
      const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
      const atBottom = dist <= threshold;
      if (pointerDown && atBottom) {
        suppressPinRef.current = false;
        stickRef.current = true;
        return;
      }
      if (suppressPinRef.current) {
        suppressPinRef.current = false;
        stickRef.current = false;
        return;
      }
      stickRef.current = atBottom;
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
  }, [enabled, scrollerRef, threshold]);

  return { stickRef, scrollToBottom };
}
