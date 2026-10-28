/** Collapse consecutive identical spans in streamed reasoning/text.
 *
 * Providers / models sometimes loop the same ~80+ char paragraph. Appending
 * every delta would paint a "tape recorder" in the thinking bubble. Keep at
 * most two trailing copies so the UI stays readable even if the backend is
 * slow to abort the completion.
 */
/** Whitespace-insensitive block key: a re-wrapped paragraph is still a repeat. */
function normalizeRepeatText(text: string): string {
  return (text || "").replace(/\s+/g, "");
}

function lastSignificantChar(text: string): string {
  const body = (text || "").replace(/\s+$/, "");
  return body.slice(-1);
}

export function collapseStreamingRepeats(
  text: string,
  opts?: { minSpan?: number; maxKeep?: number },
): string {
  const minSpan = opts?.minSpan ?? 80;
  const maxKeep = opts?.maxKeep ?? 2;
  let blob = text || "";

  const trailingReps = (unitLen: number): number => {
    if (unitLen <= 0 || blob.length < unitLen) return 0;
    const unit = blob.slice(-unitLen);
    const normUnit = normalizeRepeatText(unit);
    if (!normUnit) return 0;
    const lastChar = lastSignificantChar(unit);
    let reps = 1;
    while (blob.length >= unitLen * (reps + 1)) {
      const start = blob.length - unitLen * (reps + 1);
      const end = blob.length - unitLen * reps;
      const prev = blob.slice(start, end);
      if (lastSignificantChar(prev) !== lastChar) break;
      if (normalizeRepeatText(prev) !== normUnit) break;
      reps += 1;
    }
    return reps;
  };

  for (let round = 0; round < 8; round++) {
    const maxUnit = Math.min(480, Math.floor(blob.length / (maxKeep + 1)));
    if (maxUnit < minSpan) break;
    let found = false;
    for (let unitLen = maxUnit; unitLen >= minSpan; unitLen--) {
      const reps = trailingReps(unitLen);
      if (reps > maxKeep) {
        blob = blob.slice(0, blob.length - unitLen * (reps - maxKeep));
        found = true;
        break;
      }
    }
    if (!found) break;
  }
  return blob;
}

/** Merge reasoning/text chunks: deltas append; growing snapshots replace.
 *
 * Unlike tool-arg snapshot merging, a piece that is only a prefix of prev is
 * still appended — for prose that is usually another copy of the same
 * paragraph, which collapseStreamingRepeats then trims.
 */
export function mergeReasoningFragment(prev: string, piece: string): string {
  if (!piece) return prev || "";
  if (!prev) return piece;
  // Growing snapshot only. Identical piece = another copy — append it.
  if (piece.length > prev.length && piece.startsWith(prev)) return piece;
  return prev + piece;
}

export function absorbReasoningChunk(prev: string, piece: string): string {
  const merged = mergeReasoningFragment(prev, piece);
  return collapseStreamingRepeats(merged);
}

/** Thinking bubble variant: folds short paragraphs and keeps one copy.
 *
 * The model's private chain of thought repeats in much smaller units than
 * visible prose (a 20-30 char Chinese sentence), and the bubble only needs
 * the idea once. Content keeps the conservative 80-char floor.
 */
export function absorbReasoningStream(prev: string, piece: string): string {
  const merged = mergeReasoningFragment(prev, piece);
  return collapseStreamingRepeats(merged, { minSpan: 16, maxKeep: 1 });
}
