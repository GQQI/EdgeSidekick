// Render EdgeSidekick single-agent favicon.svg into UI + desktop PNG icons.
// Uses AgentFrame's Playwright Chromium. Run from Sidekick root:
//   node scripts/make-edgesidekick-icons.mjs
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync, copyFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const svgPath = path.join(root, "ui/public/favicon.svg");
const svg = readFileSync(svgPath, "utf8");

// Resolve playwright from AgentFrame (sibling reference project)
const afRoot = path.resolve(root, "../../AgentFrame");
const requireFromAf = createRequire(path.join(afRoot, "package.json"));
const { chromium } = requireFromAf("playwright");

async function launch() {
  try {
    return await chromium.launch();
  } catch (error) {
    const base = path.join(process.env.LOCALAPPDATA ?? "", "ms-playwright");
    const dirs = existsSync(base)
      ? readdirSync(base).filter((d) => /^chromium-\d+$/.test(d)).sort().reverse()
      : [];
    for (const dir of dirs) {
      for (const sub of ["chrome-win64", "chrome-win"]) {
        const exe = path.join(base, dir, sub, "chrome.exe");
        if (existsSync(exe)) return chromium.launch({ executablePath: exe });
      }
    }
    throw error;
  }
}

const browser = await launch();
const page = await browser.newPage({ deviceScaleFactor: 1 });

async function render(size, markup = svg) {
  await page.setViewportSize({ width: size, height: size });
  await page.setContent(
    `<style>html,body{margin:0;background:transparent}svg{display:block;width:${size}px;height:${size}px}</style>${markup}`,
  );
  return page.screenshot({
    omitBackground: true,
    clip: { x: 0, y: 0, width: size, height: size },
  });
}

const pub = path.join(root, "ui/public");
const desk = path.join(root, "desktop/build");
const iconsDir = path.join(desk, "icons");
mkdirSync(iconsDir, { recursive: true });

const icon512 = await render(512);
const icon256 = await render(256);
const icon128 = await render(128);
const icon64 = await render(64);
const icon48 = await render(48);
const icon32 = await render(32);
const icon24 = await render(24);
const icon22 = await render(22);
const icon16 = await render(16);

writeFileSync(path.join(pub, "favicon.png"), icon32);
writeFileSync(path.join(pub, "brand-icon.png"), icon256);
writeFileSync(path.join(desk, "icon.png"), icon512);
writeFileSync(path.join(iconsDir, "icon.png"), icon512);
writeFileSync(path.join(iconsDir, "512x512.png"), icon512);
writeFileSync(path.join(iconsDir, "256x256.png"), icon256);
writeFileSync(path.join(iconsDir, "128x128.png"), icon128);
writeFileSync(path.join(iconsDir, "64x64.png"), icon64);
writeFileSync(path.join(iconsDir, "48x48.png"), icon48);
writeFileSync(path.join(iconsDir, "32x32.png"), icon32);
writeFileSync(path.join(iconsDir, "24x24.png"), icon24);
writeFileSync(path.join(iconsDir, "22x22.png"), icon22);
writeFileSync(path.join(iconsDir, "16x16.png"), icon16);

// Optional tray (if folder expects it)
writeFileSync(path.join(desk, "tray.png"), icon32);
writeFileSync(path.join(desk, "tray-16.png"), icon16);

await browser.close();
console.log("EdgeSidekick icons written from", svgPath);