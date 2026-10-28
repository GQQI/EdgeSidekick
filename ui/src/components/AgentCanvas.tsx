import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { DetailView, SubNode, SubTranscriptItem } from "../types/chat";
import type { MsgKey } from "../i18n";
import { subagentDisplayName, stripToolCallMarkup } from "../utils/chatHelpers";
import { canvasGoalsMatch } from "../utils/canvasSlots";
import { paintAgentMascot, type MascotFace } from "./agentMascot";

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

type Face = MascotFace;
type WorkMode = "research" | "code" | "browser" | "writing" | "coordination" | "thinking";

// A single office palette makes the hierarchy and task state carry the visual
// meaning instead of competing rainbow agent colours.
const HUES = [222];
const CLICK_FACES: Face[] = ["happy", "wink", "wow"];
const MASCOT_W = 220;
const MASCOT_H = 180;

function isDialogue(nodes: SubNode[]): boolean {
  if (nodes.some((n) => n.kind === "party" || n.kind === "talk")) return true;
  const names = nodes
    .map((n) => (n.role || "").toLowerCase())
    .filter((r) => r && r !== "leaf" && r !== "orchestrator");
  return names.length >= 2 && new Set(names).size >= 2;
}

function layoutForest(roots: SubNode[], w: number, _h: number): LaidOut[] {
  // Team canvas is a flat peer row — no hierarchy / no link lines between agents.
  const peers = roots.length === 1 && (roots[0].children || []).length >= 2
    ? (roots[0].children || [])
    : roots;
  const gap = w / (peers.length + 1);
  return peers.map((node, index) => ({
    node: { ...node, children: [] },
    x: gap * (index + 1),
    y: 160,
    depth: 0,
    hue: HUES[index % HUES.length],
  }));
}

function outputChars(node: SubNode): number {
  let n = 0;
  for (const item of node.transcript || []) {
    if (item.kind !== "assistant") continue;
    n += (item.text || "").length + (item.reasoning || "").length;
  }
  return n;
}

/** Office agent: typing (back) while working, faces you and waves when done. */
function DeskAgentCanvas({
  running,
  depth,
  face,
  chars,
}: {
  running: boolean;
  depth: number;
  face: Face;
  mode: WorkMode;
  chars: number;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const scale = depth === 0 ? 1 : depth === 1 ? 0.88 : 0.76;
  const runningRef = useRef(running);
  const faceRef = useRef(face);
  const charsRef = useRef(chars);
  const paceRef = useRef(0);
  const seenRef = useRef({ n: chars, t: performance.now() });
  const turnRef = useRef(running ? 0 : 1);
  const phaseRef = useRef(0);
  const phaseAtRef = useRef(performance.now());
  const scrollRef = useRef(0);
  runningRef.current = running;
  faceRef.current = face;
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
          // Only real token deltas move the screen.
          scrollRef.current += delta / 24;
        }
        seenRef.current = { n: incoming, t: now };
      } else if (working && now - seen.t > 180) {
        paceRef.current *= Math.pow(0.78, dt / 80);
      }
      if (!working) paceRef.current *= 0.85;
      const pace = Math.max(0, Math.min(1, paceRef.current));
      // Hands + key highlights only while tokens are actually arriving.
      const typing = working && pace > 0.08 && now - seenRef.current.t < 420;
      if (typing) {
        const phaseGap = Math.max(36, 110 - pace * 70);
        if (now - phaseAtRef.current > phaseGap) {
          phaseRef.current = (phaseRef.current + 1) % 3;
          phaseAtRef.current = now;
        }
        scrollRef.current += (dt / 1000) * (0.6 + pace * 4.5);
      }

      const cssWidth = canvas.clientWidth || MASCOT_W;
      const cssHeight = canvas.clientHeight || MASCOT_H;
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
      if (
        canvas.width !== Math.round(cssWidth * pixelRatio) ||
        canvas.height !== Math.round(cssHeight * pixelRatio)
      ) {
        canvas.width = Math.round(cssWidth * pixelRatio);
        canvas.height = Math.round(cssHeight * pixelRatio);
      }
      ctx.setTransform(
        pixelRatio * (cssWidth / MASCOT_W),
        0,
        0,
        pixelRatio * (cssHeight / MASCOT_H),
        0,
        0,
      );
      ctx.clearRect(0, 0, MASCOT_W, MASCOT_H);
      const greet =
        !working && turn > 0.62 ? Math.sin(((turn - 0.62) / 0.38) * Math.PI) * 18 : 0;
      paintAgentMascot(ctx, {
        working,
        face: faceRef.current,
        turn,
        phase: phaseRef.current,
        greet,
        scroll: scrollRef.current,
        typing,
      });
      const turning = Math.abs(turn - target) > 0.008;
      if (working || turning) frame = requestAnimationFrame(paint);
    };
    paint(performance.now());
    return () => {
      alive = false;
      cancelAnimationFrame(frame);
    };
  }, [running, face]);

  return (
    <canvas
      ref={ref}
      className="agent-desk-canvas"
      style={{ width: MASCOT_W * scale, height: MASCOT_H * scale }}
      aria-hidden
    />
  );
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
  const laid = useMemo(() => layoutForest(roots, size.w, size.h), [roots, size.w, size.h]);
  const dialogue = isDialogue(roots);

  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const apply = () => {
      const peerCount = Math.max(laid.length || roots.length, maxLevelWidth);
      const w = Math.max(420, el.clientWidth, peerCount * 230 + 64);
      setSize({ w, h: 320 });
    };
    apply();
    const obs = new ResizeObserver(apply);
    obs.observe(el);
    return () => obs.disconnect();
  }, [maxDepth, maxLevelWidth, roots.length, laid.length]);

  if (roots.length === 0) return null;

  const title = dialogue ? t("agentCanvasDuel") : t("agentCanvasTeam");

  return (
    <div className={`agent-canvas ${dialogue ? "duel" : "team"}`} ref={wrapRef}>
      <div className="agent-canvas-head">
        <span className="agent-canvas-kicker">{title}</span>
        <span className="agent-canvas-count">{laid.length}</span>
      </div>
      <div className="agent-canvas-stage" style={{ height: size.h }}>
        <div className="agent-canvas-inner" style={{ width: size.w, height: size.h, position: "relative" }}>
        <svg
          className="agent-canvas-svg"
          viewBox={`0 0 ${size.w} ${size.h}`}
          width={size.w}
          height={size.h}
          aria-hidden
        />
        {laid.map((item) => {
          const active =
            detail?.type === "subagent" &&
            (detail.subagent.id === item.node.id ||
              canvasGoalsMatch(detail.subagent.goal, item.node.goal));
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
                  {clip(bubble.text, 22)}
                </span>
              ) : null}
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
