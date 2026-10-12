/** Live AskPacket edges for AgentCanvas (who asked whom). */
import type { AskEdge } from "../types/chat";

type Listener = () => void;

let edges: AskEdge[] = [];
const listeners = new Set<Listener>();

export function getAskEdges(): AskEdge[] {
  return edges;
}

export function setAskEdges(next: AskEdge[]) {
  edges = Array.isArray(next) ? next : [];
  listeners.forEach((l) => l());
}

export function upsertAskEdge(edge: AskEdge) {
  if (!edge || !edge.id) {
    setAskEdges([...edges, edge].filter(Boolean));
    return;
  }
  const idx = edges.findIndex((e) => e.id === edge.id);
  if (idx >= 0) {
    const copy = edges.slice();
    copy[idx] = { ...copy[idx], ...edge };
    setAskEdges(copy);
  } else {
    setAskEdges([...edges, edge]);
  }
}

export function subscribeAskEdges(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
