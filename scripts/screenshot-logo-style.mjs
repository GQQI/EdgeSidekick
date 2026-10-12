/**
 * Screenshot EdgeSidekick light/dark main UI + landing; pixel-sample accent/bg.
 * Run: node scripts/screenshot-logo-style.mjs
 */
import { createServer } from "node:http";
import { readFileSync, writeFileSync, existsSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outDir = path.join(root, "docs/screenshots");
const afRoot = path.resolve(root, "../../AgentFrame");
const requireFromAf = createRequire(path.join(afRoot, "package.json"));
const { chromium } = requireFromAf("playwright");

const mime = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".json": "application/json",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
};

function serveDir(baseDir) {
  return (req, res) => {
    let urlPath = decodeURIComponent((req.url || "/").split("?")[0]);
    if (urlPath === "/") urlPath = "/index.html";
    const fp = path.join(baseDir, urlPath.replace(/^\//, "").replace(/\//g, path.sep));
    if (!fp.startsWith(baseDir) || !existsSync(fp) || statSync(fp).isDirectory()) {
      res.writeHead(404); res.end("not found"); return;
    }
    const ext = path.extname(fp).toLowerCase();
    res.writeHead(200, { "Content-Type": mime[ext] || "application/octet-stream" });
    res.end(readFileSync(fp));
  };
}

async function launch() {
  try { return await chromium.launch(); }
  catch (error) {
    const base = path.join(process.env.LOCALAPPDATA ?? "", "ms-playwright");
    for (const dir of existsSync(base) ? readdirSync(base).filter((d) => /^chromium-\d+$/.test(d)).sort().reverse() : [])
      for (const sub of ["chrome-win64", "chrome-win"]) {
        const exe = path.join(base, dir, sub, "chrome.exe");
        if (existsSync(exe)) return chromium.launch({ executablePath: exe });
      }
    throw error;
  }
}

function startServer(handler) {
  return new Promise((resolve) => {
    const server = createServer(handler);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      resolve({ server, port });
    });
  });
}

function sample(buf, width, x, y) {
  // PNG from playwright is raw? No — screenshot returns PNG buffer. Use page.evaluate instead.
  return null;
}

const uiDist = path.join(root, "ui/dist");
const landingFile = path.join(root, "landing.html");

const uiSrv = await startServer(serveDir(uiDist));
const landSrv = await startServer((req, res) => {
  if ((req.url || "/").split("?")[0] === "/" || (req.url || "").startsWith("/landing")) {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(readFileSync(landingFile));
    return;
  }
  // allow relative docs/screenshots images
  const urlPath = decodeURIComponent((req.url || "/").split("?")[0]);
  const fp = path.join(root, urlPath.replace(/^\//, "").replace(/\//g, path.sep));
  if (fp.startsWith(root) && existsSync(fp) && !statSync(fp).isDirectory()) {
    const ext = path.extname(fp).toLowerCase();
    res.writeHead(200, { "Content-Type": mime[ext] || "application/octet-stream" });
    res.end(readFileSync(fp));
    return;
  }
  res.writeHead(404); res.end("nf");
});

const browser = await launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

async function themeSample(label) {
  return page.evaluate(() => {
    const cs = getComputedStyle(document.documentElement);
    const pick = (sel, prop) => {
      const el = document.querySelector(sel) || document.body;
      const s = getComputedStyle(el);
      return s.getPropertyValue(prop).trim() || s[prop];
    };
    // canvas sample of body bg + accent button if any
    const canvas = document.createElement("canvas");
    canvas.width = 1; canvas.height = 1;
    // report CSS vars
    return {
      bg: cs.getPropertyValue("--bg").trim(),
      panel: cs.getPropertyValue("--panel").trim(),
      ink: cs.getPropertyValue("--ink").trim(),
      muted: cs.getPropertyValue("--muted").trim(),
      accent: cs.getPropertyValue("--accent").trim(),
      border: cs.getPropertyValue("--line").trim() || cs.getPropertyValue("--border").trim(),
      bodyBg: getComputedStyle(document.body).backgroundColor,
    };
  });
}

async function pixelAt(x, y) {
  return page.evaluate(({ x, y }) => {
    // sample via temporary canvas drawing from... we can't easily. Use elementFromPoint + computed.
    const el = document.elementFromPoint(x, y);
    if (!el) return null;
    const s = getComputedStyle(el);
    return { tag: el.tagName, className: el.className?.toString?.().slice(0, 80), bg: s.backgroundColor, color: s.color };
  }, { x, y });
}

// --- Landing ---
await page.goto(`http://127.0.0.1:${landSrv.port}/`, { waitUntil: "networkidle" });
await page.waitForTimeout(600);
await page.screenshot({ path: path.join(outDir, "logo-style-landing.png"), fullPage: false });
const landSample = await themeSample("landing");
const landPixel = await pixelAt(40, 40);

// --- Main UI light ---
await page.goto(`http://127.0.0.1:${uiSrv.port}/`, { waitUntil: "networkidle" });
await page.waitForTimeout(800);
await page.evaluate(() => { document.documentElement.setAttribute("data-theme", "light"); });
await page.waitForTimeout(300);
await page.screenshot({ path: path.join(outDir, "logo-style-light.png"), fullPage: false });
const lightSample = await themeSample("light");
const lightPixel = await pixelAt(30, 30);

// --- Main UI dark ---
await page.evaluate(() => { document.documentElement.setAttribute("data-theme", "dark"); });
await page.waitForTimeout(400);
await page.screenshot({ path: path.join(outDir, "logo-style-dark.png"), fullPage: false });
const darkSample = await themeSample("dark");
const darkPixel = await pixelAt(30, 30);

await browser.close();
uiSrv.server.close();
landSrv.server.close();

const report = { landSample, landPixel, lightSample, lightPixel, darkSample, darkPixel };
writeFileSync(path.join(outDir, "logo-style-sample.json"), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));
console.log("screenshots written to", outDir);