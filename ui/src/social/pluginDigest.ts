/* Short description of connected plugins for the collaboration planner. */

type PluginLike = {
  name: string;
  enabled: boolean;
  credential_status: { configured: boolean };
  tools: Array<{ label: string; readonly: boolean }>;
};

const CACHE_MS = 30_000;
let cache: { at: number; text: string } | null = null;

export function formatPluginDigest(plugins: PluginLike[]): string {
  return plugins
    .filter((p) => p.enabled && p.credential_status.configured && p.tools.length)
    .map((p) => {
      const reads = p.tools.filter((t) => t.readonly).map((t) => t.label);
      const writes = p.tools.filter((t) => !t.readonly).map((t) => t.label);
      const parts = [reads.length ? reads.join("、") : "", writes.length ? `${writes.join("、")}（需用户确认）` : ""].filter(Boolean);
      return `- ${p.name}：${parts.join("；")}`;
    })
    .join("\n");
}

/** Never blocks a turn for long: a slow or failing fetch just means no digest. */
export async function connectedPluginDigest(fetchPlugins: () => Promise<PluginLike[]>, timeoutMs = 1500): Promise<string> {
  if (cache && Date.now() - cache.at < CACHE_MS) return cache.text;
  try {
    const plugins = await Promise.race([
      fetchPlugins(),
      new Promise<never>((_, reject) => setTimeout(() => reject(new Error("timeout")), timeoutMs)),
    ]);
    const text = formatPluginDigest(plugins);
    cache = { at: Date.now(), text };
    return text;
  } catch {
    return cache?.text || "";
  }
}

export function resetPluginDigestCache(): void {
  cache = null;
}
