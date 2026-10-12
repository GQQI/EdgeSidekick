import { memo, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { AskEdge, DetailView, SubNode, SubTranscriptItem } from "../types/chat";
import { getAskEdges, subscribeAskEdges } from "../state/askEdgesStore";
import type { MsgKey } from "../i18n";
import { subagentDisplayName, stripToolCallMarkup } from "../utils/chatHelpers";

type Props = {
  nodes: SubNode[];
  t: (key: MsgKey, ...args: string[]) => string;
  detail: DetailView;
  onSetDetail: (d: DetailView) => void;
};

type LaidOut = {
  node: SubNode;
  x: number;
  y: number;
  depth: number;
  hue: number;
  parentId?: string;
};

type Face = "idle" | "work" | "happy" | "wink" | "wow";
type WorkMode = "research" | "code" | "browser" | "writing" | "coordination" | "thinking";

// A single office palette makes the hierarchy and task state carry the visual
// meaning instead of competing rainbow agent colours.
const HUES = [222];
const CLICK_FACES: Face[] = ["happy", "wink", "wow"];

function isDialogue(nodes: SubNode[]): boolean {
  if (nodes.some((n) => n.kind === "party" || n.kind === "talk")) return true;
  const names = nodes
    .map((n) => (n.role || "").toLowerCase())
    .filter((r) => r && r !== "leaf" && r !== "orchestrator");
  return names.length >= 2 && new Set(names).size >= 2;
}

function layoutForest(roots: SubNode[], w: number, h: number): LaidOut[] {
  // A layered tree keeps nested delegation readable.  The former radial
  // layout intentionally stopped at depth two and made wide groups overlap.
  // Here every level gets its own row and the stage expands horizontally when
  // needed, so no real agent silently disappears.
  const levels: Array<Array<{ node: SubNode; parentId?: string; hue: number }>> = [];
  const visit = (node: SubNode, depth: number, hue: number, parentId?: string) => {
    (levels[depth] ||= []).push({ node, parentId, hue });
    (node.children || []).forEach((child, index) => visit(child, depth + 1, hue + index + 1, node.id));
  };
  roots.forEach((root, index) => visit(root, 0, index));
  const out: LaidOut[] = [];
  levels.forEach((level, depth) => {
    const gap = w / (level.length + 1);
    level.forEach((item, index) => {
      out.push({
        node: item.node,
        x: gap * (index + 1),
        y: 108 + depth * 124,
        depth,
        hue: HUES[item.hue % HUES.length],
        parentId: item.parentId,
      });
    });
  });
  return out;
}

function roundedRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  const radius = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + w, y, x + w, y + h, radius);
  ctx.arcTo(x + w, y + h, x, y + h, radius);
  ctx.arcTo(x, y + h, x, y, radius);
  ctx.arcTo(x, y, x + w, y, radius);
  ctx.closePath();
}

type PaintTheme = { ink: string; muted: string; surface: string; accent: string };

function outputChars(node: SubNode): number {
  let n = 0;
  for (const item of node.transcript || []) {
    if (item.kind !== "assistant") continue;
    n += (item.text || "").length + (item.reasoning || "").length;
  }
  return n;
}

function drawDesk(ctx: CanvasRenderingContext2D, theme: PaintTheme) {
  ctx.fillStyle = "rgba(15, 23, 42, .13)";
  ctx.beginPath();
  ctx.ellipse(60, 97, 43, 4.5, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = theme.muted;
  ctx.lineWidth = 2.2;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(21, 75);
  ctx.lineTo(18, 96);
  ctx.moveTo(99, 75);
  ctx.lineTo(102, 96);
  ctx.stroke();
  ctx.fillStyle = theme.ink;
  roundedRect(ctx, 12, 70, 96, 7, 2);
  ctx.fill();
  ctx.fillStyle = theme.surface;
  roundedRect(ctx, 15, 71.5, 90, 2.2, 1);
  ctx.fill();
}

function drawMonitor(
  ctx: CanvasRenderingContext2D,
  theme: PaintTheme,
  mode: WorkMode,
  glow: number,
) {
  ctx.fillStyle = theme.ink;
  roundedRect(ctx, 43, 17, 35, 26, 2.5);
  ctx.fill();
  ctx.fillStyle = `rgba(255,255,255,${0.72 + glow * 0.2})`;
  roundedRect(ctx, 46, 20, 29, 19, 1);
  ctx.fill();
  ctx.fillStyle = theme.accent;
  if (mode === "code") {
    ctx.fillRect(49, 24, 8, 1.6);
    ctx.fillRect(49, 28, 17, 1.6);
    ctx.fillRect(49, 32, 12, 1.6);
    ctx.fillRect(49, 36, 15, 1.6);
  } else if (mode === "research" || mode === "browser") {
    ctx.beginPath();
    ctx.arc(59, 29, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = theme.accent;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(63, 33);
    ctx.lineTo(68, 37);
    ctx.stroke();
  } else {
    ctx.fillRect(49, 24, 20, 1.8);
    ctx.fillRect(49, 29, 16, 1.8);
    ctx.fillRect(49, 34, 22, 1.8);
  }
  ctx.fillStyle = theme.ink;
  ctx.fillRect(58, 43, 5, 6);
  ctx.fillRect(51, 48, 19, 2.5);
}

function drawFace(ctx: CanvasRenderingContext2D, theme: PaintTheme, face: Face, bob: number) {
  ctx.fillStyle = theme.accent;
  if (face === "wow") {
    ctx.fillRect(55, 40 + bob, 3, 3);
    ctx.fillRect(63, 40 + bob, 3, 3);
    roundedRect(ctx, 58, 45 + bob, 4, 3.5, 1.5);
    ctx.fill();
  } else if (face === "wink") {
    ctx.fillRect(55, 41 + bob, 4, 1.5);
    ctx.fillRect(64, 40 + bob, 2.5, 3);
    ctx.fill();
    ctx.fillRect(59, 46 + bob, 4, 1.2);
  } else if (face === "happy") {
    ctx.fillRect(55, 40 + bob, 3, 2);
    ctx.fillRect(63, 40 + bob, 3, 2);
    ctx.fillRect(58, 46 + bob, 5, 1.5);
  } else {
    ctx.fillRect(55, 40 + bob, 3, 2.5);
    ctx.fillRect(63, 40 + bob, 3, 2.5);
    ctx.fillRect(58, 46 + bob, 5, 1.2);
  }
}

function drawCharacter(
  ctx: CanvasRenderingContext2D,
  theme: PaintTheme,
  opts: {
    front: boolean;
    face: Face;
    mode: WorkMode;
    bob: number;
    hand: number;
    greet: number;
    pulse: number;
  },
) {
  const { front, face, mode, bob, hand, greet, pulse } = opts;
  ctx.fillStyle = "rgba(15,23,42,.17)";
  roundedRect(ctx, 39, 54 + bob, 42, 30, 7);
  ctx.fill();
  ctx.fillStyle = theme.ink;
  roundedRect(ctx, 44, 47 + bob, 32, 34, 7);
  ctx.fill();
  ctx.fillStyle = theme.surface;
  roundedRect(ctx, 47, 50 + bob, 26, 28, 5);
  ctx.fill();

  if (front) {
    ctx.fillStyle = theme.ink;
    roundedRect(ctx, 49, 34 + bob, 22, 18, 3);
    ctx.fill();
    ctx.fillStyle = theme.surface;
    roundedRect(ctx, 52, 37 + bob, 16, 12, 2);
    ctx.fill();
    drawFace(ctx, theme, face, bob);
    ctx.fillStyle = theme.ink;
    roundedRect(ctx, 49, 52 + bob, 22, 18, 4);
    ctx.fill();
    ctx.fillStyle = theme.accent;
    ctx.fillRect(58, 57 + bob, 4, 4);
  } else {
    ctx.fillStyle = theme.ink;
    roundedRect(ctx, 48, 33 + bob, 24, 19, 4);
    ctx.fill();
    ctx.fillStyle = theme.surface;
    roundedRect(ctx, 46, 40 + bob, 4, 7, 2);
    ctx.fill();
    roundedRect(ctx, 70, 40 + bob, 4, 7, 2);
    ctx.fill();
    ctx.fillStyle = theme.ink;
    roundedRect(ctx, 50, 33 + bob, 20, 8, 4);
    ctx.fill();
    ctx.fillStyle = theme.surface;
    ctx.globalAlpha = 0.35;
    roundedRect(ctx, 54, 42 + bob, 12, 6, 2);
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.fillStyle = theme.ink;
    roundedRect(ctx, 49, 51 + bob, 22, 18, 4);
    ctx.fill();
    ctx.fillStyle = theme.accent;
    ctx.fillRect(58, 56 + bob, 4, 3);
  }

  ctx.strokeStyle = theme.ink;
  ctx.lineWidth = 4.2;
  ctx.lineCap = "round";
  const leftHand = front ? hand * 0.35 : hand;
  const rightHand = front ? -hand * 0.2 - greet : -hand;
  ctx.beginPath();
  ctx.moveTo(50, 58 + bob);
  ctx.lineTo(40, 66 + leftHand);
  ctx.lineTo(48, 71 - (front ? greet * 0.15 : 0));
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(70, 58 + bob);
  ctx.lineTo(mode === "research" ? 78 : 80, 65 + rightHand);
  ctx.lineTo(mode === "research" ? 84 : 72, front ? 68 - greet : 71);
  ctx.stroke();
  ctx.fillStyle = theme.accent;
  if (mode === "research") {
    roundedRect(ctx, 80, 57 + (front ? -greet : 0), 10, 14, 1.5);
    ctx.fill();
    ctx.fillStyle = theme.surface;
    ctx.fillRect(82, 60, 6, 1);
    ctx.fillRect(82, 64, 5, 1);
  }
  if (mode === "coordination") {
    ctx.beginPath();
    ctx.arc(87, 58 + pulse * 2, 4 + pulse, 0, Math.PI * 2);
    ctx.fill();
  }
}

/** Office agent: back while working (hands follow token pace), turns to face you when done. */
function DeskAgentCanvas({
  running,
  depth,
  face,
  mode,
  chars,
}: {
  running: boolean;
  depth: number;
  face: Face;
  mode: WorkMode;
  chars: number;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const scale = depth === 0 ? 1 : depth === 1 ? 0.82 : 0.7;
  const runningRef = useRef(running);
  const faceRef = useRef(face);
  const modeRef = useRef(mode);
  const charsRef = useRef(chars);
  const paceRef = useRef(0);
  const seenRef = useRef({ n: chars, t: performance.now() });
  const turnRef = useRef(running ? 0 : 1);
  runningRef.current = running;
  faceRef.current = face;
  modeRef.current = mode;
  charsRef.current = chars;

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return undefined;
    const ctx = canvas.getContext("2d");
    if (!ctx) return undefined;
    let frame = 0;
    let alive = true;
    let last = performance.now();
    const paint = (now: number) => {
      if (!alive) return;
      const dt = Math.min(48, now - last);
      last = now;
      const working = runningRef.current;
      const target = working ? 0 : 1;
      const step = dt * 0.0034;
      if (turnRef.current < target) turnRef.current = Math.min(target, turnRef.current + step);
      else if (turnRef.current > target) turnRef.current = Math.max(target, turnRef.current - step);
      const turn = turnRef.current;

      const incoming = charsRef.current;
      const seen = seenRef.current;
      if (incoming !== seen.n) {
        const gap = now - seen.t;
        const delta = incoming - seen.n;
        if (gap > 0 && gap < 2000 && delta > 0) {
          const instant = Math.min(1, delta / (gap / 1000) / 90);
          paceRef.current = paceRef.current * 0.52 + instant * 0.48;
        }
        seenRef.current = { n: incoming, t: now };
      } else if (working && now - seen.t > 220) {
        paceRef.current *= Math.pow(0.84, dt / 80);
      }
      if (!working) paceRef.current *= 0.9;
      const pace = Math.max(0, Math.min(1, paceRef.current));

      const cssWidth = canvas.clientWidth || 120;
      const cssHeight = canvas.clientHeight || 104;
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
      if (canvas.width !== Math.round(cssWidth * pixelRatio) || canvas.height !== Math.round(cssHeight * pixelRatio)) {
        canvas.width = Math.round(cssWidth * pixelRatio);
        canvas.height = Math.round(cssHeight * pixelRatio);
      }
      ctx.setTransform(pixelRatio * (cssWidth / 120), 0, 0, pixelRatio * (cssHeight / 104), 0, 0);
      ctx.clearRect(0, 0, 120, 104);
      const style = getComputedStyle(canvas);
      const theme: PaintTheme = {
        ink: style.getPropertyValue("--ink").trim() || "#101a2b",
        muted: style.getPropertyValue("--muted").trim() || "#66768a",
        surface: style.getPropertyValue("--surface").trim() || "#ffffff",
        accent: style.getPropertyValue("--accent").trim() || "#2f6fed",
      };
      const pulse = working ? (Math.sin(now / (240 - pace * 90)) + 1) / 2 : 0;
      const bob = working ? Math.sin(now / (300 - pace * 150)) * (1.1 + pace * 0.55) : 0;
      const hand = working ? Math.sin(now / (88 + (1 - pace) * 170)) * (1.5 + pace * 2.4) : 0;
      const greet = !working && turn > 0.62 ? Math.sin(((turn - 0.62) / 0.38) * Math.PI) * 5 : 0;
      const front = turn >= 0.5;
      const squash = 0.16 + 0.84 * Math.abs(Math.cos(turn * Math.PI));

      drawDesk(ctx, theme);
      drawMonitor(ctx, theme, modeRef.current, working ? 0.35 + pulse * 0.65 : 0.12);

      ctx.save();
      ctx.translate(60, 54);
      ctx.scale(squash, 1);
      ctx.translate(-60, -54);
      drawCharacter(ctx, theme, {
        front,
        face: faceRef.current,
        mode: modeRef.current,
        bob,
        hand,
        greet,
        pulse,
      });
      ctx.restore();

      if (working) {
        ctx.fillStyle = theme.accent;
        ctx.globalAlpha = 0.16 + pulse * 0.2;
        ctx.beginPath();
        ctx.arc(60, 52, 34 + pulse * 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = 1;
      }
      const turning = Math.abs(turn - target) > 0.008;
      if (working || turning) frame = requestAnimationFrame(paint);
    };
    paint(performance.now());
    return () => {
      alive = false;
      cancelAnimationFrame(frame);
    };
  }, [running, face, mode]);

  return <canvas ref={ref} className="agent-desk-canvas" style={{ width: 120 * scale, height: 104 * scale }} aria-hidden />;
}

function workMode(node: SubNode): WorkMode {
  const lastTool = [...(node.transcript || [])].reverse().find((item) => item.kind === "tool");
  const name = lastTool?.kind === "tool" ? lastTool.tool.name.toLowerCase() : "";
  if (/(read_file|search_text|codebase|list_dir)/.test(name)) return "research";
  if (/(write_file|str_replace|verify_run|run_shell)/.test(name)) return "code";
  if (/browser_/.test(name)) return "browser";
  if (/(delegate|ask_user)/.test(name)) return "coordination";
  if (/(memory|skill)/.test(name)) return "writing";
  return "thinking";
}

function modeLabel(mode: WorkMode): string {
  return { research: "检索", code: "开发", browser: "浏览", writing: "整理", coordination: "协作", thinking: "思考" }[mode];
}

function clip(text: string, n: number) {
  const t = text.replace(/\s+/g, " ").trim();
  return t.length > n ? `${t.slice(0, n - 1)}…` : t;
}

function lastAssistant(node: SubNode): Extract<SubTranscriptItem, { kind: "assistant" }> | undefined {
  const items = node.transcript || [];
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (item.kind === "assistant") return item;
  }
  return undefined;
}

function statusBubble(node: SubNode, t: Props["t"]): { text: string; streaming: boolean; loading?: boolean } | null {
  if (node.status === "running") {
    const last = lastAssistant(node);
    const think = stripToolCallMarkup(last?.reasoning || "").trim();
    const text = stripToolCallMarkup(last?.text || "").trim();
    if (think) return { text: clip(think, 32), streaming: true };
    if (text) return { text: clip(text, 32), streaming: Boolean(last?.streaming) };
    const activity = (node.activity || "").trim();
    if (activity && activity !== t("thinkingActivity")) {
      return { text: clip(activity, 32), streaming: true };
    }
    return { text: "", streaming: true, loading: true };
  }
  return null;
}

function AgentCanvasInner({ nodes, t, detail, onSetDetail }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 560, h: 280 });
  const [mood, setMood] = useState<Record<string, Face>>({});
  const [poked, setPoked] = useState<string | null>(null);
  const pokeTimer = useRef<number>(0);
  const roots = useMemo(
    () => nodes.filter((n) => n.id && (n.goal || n.role || (n.children || []).length)),
    [nodes],
  );
  const maxDepth = useMemo(() => {
    let d = 0;
    const walk = (n: SubNode, depth: number) => {
      d = Math.max(d, depth);
      (n.children || []).forEach((c) => walk(c, depth + 1));
    };
    roots.forEach((n) => walk(n, 0));
    return d;
  }, [roots]);
  const maxLevelWidth = useMemo(() => {
    const counts: number[] = [];
    const walk = (n: SubNode, depth: number) => {
      counts[depth] = (counts[depth] || 0) + 1;
      (n.children || []).forEach((c) => walk(c, depth + 1));
    };
    roots.forEach((n) => walk(n, 0));
    return Math.max(1, ...counts);
  }, [roots]);
  const totalAgents = useMemo(() => roots.reduce((sum, n) => {
    const count = (item: SubNode): number => 1 + (item.children || []).reduce((v, c) => v + count(c), 0);
    return sum + count(n);
  }, 0), [roots]);
  const laid = useMemo(() => layoutForest(roots, size.w, size.h), [roots, size.w, size.h]);
  const askEdges = useSyncExternalStore(subscribeAskEdges, getAskEdges, getAskEdges);
  const visibleAsks = useMemo(() => {
    const ids = new Set<string>();
    const walk = (n: SubNode) => {
      ids.add(n.id);
      (n.children || []).forEach(walk);
    };
    roots.forEach(walk);
    // include lead-looking empty from_id edges targeting visible nodes
    return askEdges.filter(
      (e) =>
        (e.to_id && ids.has(e.to_id)) ||
        (e.from_id && ids.has(e.from_id)) ||
        (!e.from_id && e.to_id && ids.has(e.to_id)),
    );
  }, [askEdges, roots]);

  const byId = useMemo(() => new Map(laid.map((item) => [item.node.id, item])), [laid]);
  const dialogue = isDialogue(roots);
  const glowId = `agent-glow-${useId().replace(/:/g, "")}`;
  const offices = useMemo(() => {
    const shared = roots.length > 1
      ? [{ id: "team-floor", label: "协作办公室", left: 8, width: Math.max(260, size.w - 16), top: 50, height: Math.max(156, size.h - 62) }]
      : [];
    const teams = laid.flatMap((parent) => {
    const children = laid.filter((item) => item.parentId === parent.node.id);
    if (!children.length) return [];
    const xs = children.map((item) => item.x);
    return [{
      id: parent.node.id,
      label: `${subagentDisplayName(parent.node)} 的团队`,
      left: Math.max(8, Math.min(...xs) - 52),
      width: Math.max(124, Math.max(...xs) - Math.min(...xs) + 104),
      top: parent.y + 44,
      height: 112,
    }];
    });
    return [...shared, ...teams];
  }, [laid, roots.length, size.h, size.w]);

  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const apply = () => {
      const w = Math.max(360, el.clientWidth, maxLevelWidth * 132 + 44);
      const h = Math.max(250, 170 + maxDepth * 124);
      setSize({ w, h });
    };
    apply();
    const obs = new ResizeObserver(apply);
    obs.observe(el);
    return () => obs.disconnect();
  }, [maxDepth, maxLevelWidth]);

  if (roots.length === 0) return null;

  const title = dialogue ? t("agentCanvasDuel") : t("agentCanvasTeam");

  return (
    <div className={`agent-canvas ${dialogue ? "duel" : "team"}`} ref={wrapRef}>
      <div className="agent-canvas-head">
        <span className="agent-canvas-kicker">{title}</span>
        <span className="agent-canvas-count">{totalAgents}</span>
      </div>
      <div className="agent-canvas-stage" style={{ height: size.h }}>
        <div className="agent-canvas-inner" style={{ width: size.w, height: size.h, position: "relative" }}>
        {offices.map((office) => (
          <div
            key={`office-${office.id}`}
            className="agent-office-pod"
            style={{ left: office.left, top: office.top, width: office.width, height: office.height }}
          >
            <span>{office.label}</span>
          </div>
        ))}
        <svg
          className="agent-canvas-svg"
          viewBox={`0 0 ${size.w} ${size.h}`}
          width={size.w}
          height={size.h}
        >
          <defs>
            <filter id={glowId} x="-40%" y="-40%" width="180%" height="180%">
              <feGaussianBlur stdDeviation="3.5" result="b" />
              <feMerge>
                <feMergeNode in="b" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          
            <marker id="ask-arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
              <path d="M0,0 L6,3 L0,6 Z" className="agent-ask-arrow" />
            </marker>
          </defs>
          {laid.map((item) => {
            if (!item.parentId) return null;
            const parent = byId.get(item.parentId);
            if (!parent) return null;
            return (
              <path
                key={`link-${item.node.id}`}
                className="agent-link"
                d={`M ${parent.x} ${parent.y + 18} Q ${(parent.x + item.x) / 2} ${(parent.y + item.y) / 2 - 8} ${item.x} ${item.y - 18}`}
                fill="none"
              />
            );
          })}
          {visibleAsks.map((edge) => {
            const to = byId.get(edge.to_id);
            if (!to) return null;
            const from =
              (edge.from_id && byId.get(edge.from_id)) ||
              laid.find((x) => !x.parentId) ||
              null;
            if (!from) return null;
            const mx = (from.x + to.x) / 2;
            const my = (from.y + to.y) / 2 - 28;
            const label =
              edge.need?.trim() ||
              edge.goal_slice?.trim() ||
              `${edge.what || "ask"}/${edge.how || edge.kind}`;
            const short = label.length > 22 ? `${label.slice(0, 20)}…` : label;
            const active = detail?.type === "ask" && detail.ask.id === edge.id;
            return (
              <g
                key={`ask-${edge.id || `${edge.to_id}-${edge.need}`}`}
                className={`agent-ask-edge${active ? " active" : ""}${edge.status === "asking" ? " asking" : ""}`}
                onClick={(ev) => {
                  ev.stopPropagation();
                  onSetDetail({ type: "ask", ask: edge });
                }}
                style={{ cursor: "pointer" }}
              >
                <path
                  className="agent-ask-link"
                  d={`M ${from.x} ${from.y + 10} Q ${mx} ${my} ${to.x} ${to.y - 16}`}
                  fill="none"
                  markerEnd="url(#ask-arrow)"
                />
                <rect
                  className="agent-ask-chip-bg"
                  x={mx - Math.min(70, short.length * 3.4) - 6}
                  y={my - 10}
                  width={Math.min(140, short.length * 6.8 + 16)}
                  height={18}
                  rx={9}
                />
                <text className="agent-ask-chip" x={mx} y={my + 3} textAnchor="middle">
                  {short}
                </text>
              </g>
            );
          })}
          {laid.map((item) =>
            item.node.status === "running" ? (
              <circle
                key={`ring-${item.node.id}`}
                className="agent-speak-ring"
                cx={item.x}
                cy={item.y - 6}
                r={depthRing(item.depth)}
                filter={`url(#${glowId})`}
              />
            ) : null,
          )}
        </svg>
        {laid.map((item) => {
          const active = detail?.type === "subagent" && detail.subagent.id === item.node.id;
          const bubble = statusBubble(item.node, t);
          const running = item.node.status === "running";
          const mode = workMode(item.node);
          const face: Face =
            mood[item.node.id] || (running ? "work" : item.node.status === "error" ? "idle" : "happy");
          return (
            <button
              key={item.node.id}
              type="button"
              className={`agent-persona depth-${item.depth} ${item.node.status}${active ? " active" : ""}${poked === item.node.id ? " poked" : ""}${running ? " is-generating" : ""}`}
              style={{ left: item.x, top: item.y, ["--persona-hue" as string]: String(item.hue) }}
              onClick={() => {
                const next = CLICK_FACES[(CLICK_FACES.indexOf(face as Face) + 1) % CLICK_FACES.length] || "happy";
                setMood((prev) => ({ ...prev, [item.node.id]: next }));
                setPoked(item.node.id);
                window.clearTimeout(pokeTimer.current);
                pokeTimer.current = window.setTimeout(() => setPoked(null), 480);
                onSetDetail({ type: "subagent", subagent: item.node });
              }}
              title={item.node.goal}
            >
              {bubble?.loading ? (
                <span className="agent-speech loading" aria-label={t("thinkingActivity")}>
                  <span className="agent-dots">
                    <i />
                    <i />
                    <i />
                  </span>
                </span>
              ) : bubble ? (
                <span className={`agent-speech${bubble.streaming ? " streaming" : ""}`}>
                  {clip(bubble.text, 28)}
                </span>
              ) : (
                <span className="agent-speech quiet">
                  {item.node.status === "error" ? t("toolStatusError") : t("subagentDone")}
                </span>
              )}
              <span className="agent-persona-figure">
                <DeskAgentCanvas
                  running={running}
                  depth={item.depth}
                  face={face}
                  mode={mode}
                  chars={outputChars(item.node)}
                />
              </span>
              <span className="agent-persona-name">{clip(subagentDisplayName(item.node), 10)}</span>
              <span className={`agent-work-badge ${mode}`}>{modeLabel(mode)}</span>
            </button>
          );
        })}
        </div>
      </div>
    </div>
  );
}

export const AgentCanvas = memo(AgentCanvasInner, (prev, next) => {
  if (prev.t !== next.t || prev.detail !== next.detail || prev.onSetDetail !== next.onSetDetail) {
    return false;
  }
  if (prev.nodes.length !== next.nodes.length) return false;
  return prev.nodes.every((node, i) => node === next.nodes[i]);
});

function depthRing(depth: number) {
  if (depth === 0) return 34;
  if (depth === 1) return 28;
  return 22;
}
