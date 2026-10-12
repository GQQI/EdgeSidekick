/** Debug helper: print the raw turn-analyzer output for a message. */
import { buildTurnIntentPrompt, parseTurnIntent } from "../../ui/src/social/groupLoop.ts";
import { programmingAgents } from "../../ui/src/social/store.ts";
const roster = programmingAgents().map((a) => ({ id: a.id, name: a.name, title: a.title, duty: a.duty, blurb: a.blurb }));
const text = process.argv[2] || "大家好";
const maxTokens = Number(process.argv[3] || 600);
const prompt = buildTurnIntentPrompt({ roster, history: [{ id: "u", role: "user", text }], userText: text });
const t0 = Date.now();
const r = await fetch("http://127.0.0.1:8799/complete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt, max_tokens: maxTokens }) });
const d = (await r.json()) as { text: string; error?: string };
console.log(JSON.stringify({ ms: Date.now() - t0, error: d.error, raw: d.text, parsed: parseTurnIntent(d.text || "", roster, text) }, null, 1));
