import assert from "node:assert/strict";
import test from "node:test";
import {
  literalLinkSpans,
  prepMarkdownForUrls,
  remarkLinkifyLiteral,
  splitTextWithUrls,
} from "./src/browser/urlDetect.ts";
import { tightenMarkdownFlow } from "./src/social/turnLogic.ts";

const SHELL = [
  "```bash",
  "cd /tmp",
  "BASE=https://npmmirror.com/mirrors/node/latest-v20.x",
  "VER=v20.19.5",
  'curl -fLO "$BASE/node-$VER-linux-arm64.tar.xz"',
  'curl -fLO "$BASE/SHASUMS256.txt"',
  'grep "node-$VER-linux-arm64.tar.xz" SHASUMS256.txt | sha256sum -c -',
  "```",
].join("\n");

const ASSIGNMENT = '$BASE = "https://npmmirror.com/mirrors/node/latest-v20.x"';

function shown(parts: ReturnType<typeof splitTextWithUrls>): string {
  return parts.map((p) => p.value).join("");
}

function rebuilt(src: string): string {
  const spans = literalLinkSpans(src);
  let out = "";
  let last = 0;
  for (const span of spans) {
    if (span.index < last) continue;
    out += src.slice(last, span.index);
    out += span.raw;
    last = span.index + span.raw.length;
  }
  return out + src.slice(last);
}

type MdNode = { type: string; value?: string; url?: string; children?: MdNode[] };

function flatten(node: MdNode): string {
  if (node.type === "text" || node.type === "inlineCode" || node.type === "code") return node.value || "";
  return (node.children || []).map(flatten).join("");
}

test("display never rewrites a shell assignment into a markdown link", () => {
  assert.equal(prepMarkdownForUrls(ASSIGNMENT), ASSIGNMENT);
  assert.equal(shown(splitTextWithUrls(ASSIGNMENT)), ASSIGNMENT);
  assert.equal(rebuilt(ASSIGNMENT), ASSIGNMENT);
  const tree: MdNode = { type: "root", children: [{ type: "text", value: ASSIGNMENT }] };
  remarkLinkifyLiteral()(tree);
  assert.equal(flatten(tree), ASSIGNMENT);
  assert.equal(tree.children?.[0]?.type, "text");
  assert.match(String(tree.children?.[1]?.url || ""), /^https:\/\/npmmirror\.com\//);
  assert.equal(tree.children?.[1]?.children?.[0]?.value, "https://npmmirror.com/mirrors/node/latest-v20.x");
  assert.equal(tree.children?.[2]?.value, '"');
});

test("a fenced shell script is left character for character", () => {
  const out = prepMarkdownForUrls(tightenMarkdownFlow(SHELL));
  assert.equal(out, SHELL);
  assert.equal(shown(splitTextWithUrls(out)), SHELL);
  assert.equal(rebuilt(out), SHELL);
});

test("quotes, dollars, pipes and parentheses survive link detection", () => {
  const samples = [
    ASSIGNMENT,
    'curl -fLO "$BASE/node-$VER-linux-arm64.tar.xz"',
    'grep "node-$VER-linux-arm64.tar.xz" SHASUMS256.txt | sha256sum -c -',
    "BASE=(https://example.com/a)",
    "see https://example.com/a).",
    "服务在 **http://localhost:5176**，已在",
    "打开 report.html 查看",
  ];
  for (const sample of samples) {
    assert.equal(prepMarkdownForUrls(sample), sample);
    assert.equal(shown(splitTextWithUrls(sample)), sample);
    assert.equal(rebuilt(sample), sample);
    const tree: MdNode = { type: "root", children: [{ type: "paragraph", children: [{ type: "text", value: sample }] }] };
    remarkLinkifyLiteral()(tree);
    assert.equal(flatten(tree), sample);
  }
});

test("an unfinished code fence keeps its URL and shell symbols while streaming", () => {
  const partial = SHELL.replace(/\n```$/, "");
  assert.equal(prepMarkdownForUrls(partial), partial);
  assert.equal(shown(splitTextWithUrls(partial)), partial);
});
