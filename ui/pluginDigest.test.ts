import assert from "node:assert/strict";
import test from "node:test";
import { connectedPluginDigest, formatPluginDigest, resetPluginDigestCache } from "./src/social/pluginDigest.ts";

const plugin = (name: string, over: Record<string, unknown> = {}) => ({
  name,
  enabled: true,
  credential_status: { configured: true },
  tools: [
    { label: "搜索 Issue", readonly: true },
    { label: "创建 Issue", readonly: false },
  ],
  ...over,
});

test("only enabled, configured plugins are described, with write tools flagged", () => {
  const text = formatPluginDigest([
    plugin("GitHub"),
    plugin("Gmail", { enabled: false }),
    plugin("GitLab", { credential_status: { configured: false } }),
  ]);
  assert.equal(text, "- GitHub：搜索 Issue；创建 Issue（需用户确认）");
});

test("a slow plugin endpoint never stalls the turn", async () => {
  resetPluginDigestCache();
  const started = Date.now();
  const text = await connectedPluginDigest(() => new Promise(() => {}), 50);
  assert.equal(text, "");
  assert.ok(Date.now() - started < 1000);
});

test("results are cached between turns", async () => {
  resetPluginDigestCache();
  let calls = 0;
  const fetcher = async () => {
    calls++;
    return [plugin("GitHub")];
  };
  await connectedPluginDigest(fetcher);
  await connectedPluginDigest(fetcher);
  assert.equal(calls, 1);
});
