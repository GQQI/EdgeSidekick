/**
 * EdgeSidekick desktop — live embedded browser.
 *
 * Architecture:
 * - Main window hosts the React workbench
 * - A sibling BrowserView paints the live page over the Browser panel host
 * - Select Mode injects into that live webContents (not screenshots)
 */
import {
  app,
  BrowserWindow,
  BrowserView,
  dialog,
  ipcMain,
  shell,
} from "electron";
import { execFileSync, spawn } from "node:child_process";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import http from "node:http";

const require = createRequire(import.meta.url);
const buildSelectBootstrap = require("./select-bootstrap.cjs");

import {
  normalizeNavUrl,
  loopbackUrlCandidates,
  isConnectionRefused,
  isAbortedNav,
  canonicalizeUrl,
  urlsMatch,
  isShallowSiteRewind,
  waitForGuestUrl,
  friendlyNavError,
} from "./navUrl.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..");
const isDev = process.argv.includes("--dev");
const BACKEND_URL = process.env.SIDEKICK_BACKEND_URL || "http://127.0.0.1:8787";

/** Read-only app payload: repo root in dev, extraResources/sidekick when installed. */
function payloadRoot() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "sidekick");
  }
  return REPO_ROOT;
}

function dataDir() {
  return app.getPath("userData");
}

function logDir() {
  const dir = path.join(dataDir(), "logs");
  try {
    fs.mkdirSync(dir, { recursive: true });
  } catch {
    /* ignore */
  }
  return dir;
}

const STYLE_KEYS = [
  "display",
  "position",
  "color",
  "backgroundColor",
  "fontSize",
  "fontWeight",
  "fontFamily",
  "lineHeight",
  "padding",
  "margin",
  "border",
  "borderRadius",
  "width",
  "height",
  "flexDirection",
  "justifyContent",
  "alignItems",
  "gap",
  "opacity",
  "visibility",
  "overflow",
  "textAlign",
];

let mainWindow = null;
let browserView = null;
let backendProc = null;
let browserVisible = false;
let lastBounds = null;
const CDP_PORT = String(process.env.SIDEKICK_CDP_PORT || "8315");

function pythonCandidates() {
  const out = [];
  const push = (p) => {
    const s = String(p || "").trim();
    if (s && !out.includes(s)) out.push(s);
  };
  // Portable order: explicit override → bundled runtime → tip file → project .venv → PATH
  push(process.env.SIDEKICK_PYTHON);
  if (app.isPackaged) {
    push(
      process.platform === "win32"
        ? path.join(payloadRoot(), "python", "python.exe")
        : path.join(payloadRoot(), "python", "bin", "python3"),
    );
    return out;
  }
  const tipFile = path.join(REPO_ROOT, ".sidekick-python");
  try {
    if (fs.existsSync(tipFile)) {
      push(fs.readFileSync(tipFile, "utf8").split(/\r?\n/)[0]);
    }
  } catch {
    /* ignore */
  }
  push(
    process.platform === "win32"
      ? path.join(REPO_ROOT, ".venv", "Scripts", "python.exe")
      : path.join(REPO_ROOT, ".venv", "bin", "python"),
  );
  push(process.platform === "win32" ? "python" : "python3");
  return out;
}

let lastPythonCheckError = "";
let backendStartupFatal = false;

const PYTHON_ENV_BLOCKLIST = new Set([
  "PYTHONHOME",
  "PYTHONPATH",
  "PYTHONSTARTUP",
  "PYTHONEXECUTABLE",
  "PYTHONUSERBASE",
  "PYTHONSAFEPATH",
  "PYTHONPLATLIBDIR",
  "VIRTUAL_ENV",
]);

function windowsPowershellExe() {
  const root = process.env.SystemRoot || process.env.windir || "C:\\Windows";
  const cands = [
    path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
    path.join(root, "Sysnative", "WindowsPowerShell", "v1.0", "powershell.exe"),
    path.join(root, "SysWOW64", "WindowsPowerShell", "v1.0", "powershell.exe"),
  ];
  for (const cand of cands) {
    if (fs.existsSync(cand)) return cand;
  }
  return "powershell.exe";
}

/** Keep System32 / PowerShell / Git on PATH so run_shell can find them. */
function ensureWindowsSearchPath(pathValue) {
  if (process.platform !== "win32") return pathValue || "";
  const root = process.env.SystemRoot || process.env.windir || "C:\\Windows";
  const pf = process.env.ProgramFiles || "C:\\Program Files";
  const pf86 = process.env["ProgramFiles(x86)"] || "C:\\Program Files (x86)";
  const local = process.env.LOCALAPPDATA || "";
  const extras = [
    path.join(root, "System32"),
    path.join(root, "System32", "Wbem"),
    path.join(root, "System32", "WindowsPowerShell", "v1.0"),
    path.join(root, "System32", "OpenSSH"),
    path.join(pf, "Git", "cmd"),
    path.join(pf, "Git", "bin"),
    path.join(pf, "Git", "usr", "bin"),
    path.join(pf86, "Git", "cmd"),
    path.join(pf86, "Git", "bin"),
    local ? path.join(local, "Programs", "Git", "cmd") : "",
    local ? path.join(local, "Programs", "Git", "bin") : "",
  ].filter(Boolean);
  const parts = String(pathValue || "")
    .split(path.delimiter)
    .filter(Boolean);
  const seen = new Set(parts.map((p) => p.toLowerCase()));
  for (const extra of extras) {
    const key = extra.toLowerCase();
    if (seen.has(key) || !fs.existsSync(extra)) continue;
    parts.push(extra);
    seen.add(key);
  }
  return parts.join(path.delimiter);
}

/** Env for bundled Python: never inherit a host PYTHONHOME; keep a full Windows PATH. */
function pythonChildEnv(pythonDir) {
  const env = {};
  for (const [k, v] of Object.entries(process.env)) {
    if (PYTHON_ENV_BLOCKLIST.has(String(k).toUpperCase())) continue;
    env[k] = v;
  }
  // Explicit empty overrides beat any leftover host values on Windows.
  env.PYTHONHOME = "";
  env.PYTHONPATH = "";
  env.PYTHONUTF8 = "1";
  env.PYTHONIOENCODING = "utf-8";
  env.PYTHONUNBUFFERED = "1";
  env.PYTHONDONTWRITEBYTECODE = "1";
  env.PYTHONNOUSERSITE = "1";
  let searchPath = env.PATH || env.Path || "";
  if (pythonDir) {
    searchPath = `${pythonDir}${path.delimiter}${searchPath}`;
  }
  env.PATH = ensureWindowsSearchPath(searchPath);
  if (process.platform === "win32") {
    env.SystemRoot = env.SystemRoot || process.env.SystemRoot || "C:\\Windows";
    env.WINDIR = env.WINDIR || env.SystemRoot;
    env.PATHEXT = env.PATHEXT || ".COM;.EXE;.BAT;.CMD;.VBS;.JS;.MSC";
  }
  return env;
}

function describePythonRuntime(py) {
  const dir = path.dirname(py);
  const lines = [`python: ${py}`, `exists: ${fs.existsSync(py)}`];
  try {
    const names = fs.readdirSync(dir);
    const zip = names.find((n) => /^python\d+\.zip$/i.test(n));
    const pth = names.find((n) => /^python\d+\._pth$/i.test(n));
    const encPy = path.join(dir, "Lib", "encodings", "__init__.py");
    const encPyc = path.join(dir, "Lib", "encodings", "__init__.pyc");
    lines.push(`dir: ${dir}`);
    lines.push(`stdlib zip: ${zip || "(missing)"}`);
    lines.push(`_pth: ${pth || "(missing)"}`);
    lines.push(
      `Lib/encodings: ${fs.existsSync(encPy) || fs.existsSync(encPyc) ? "yes" : "(missing)"}`,
    );
  } catch (e) {
    lines.push(`dir listing failed: ${e?.message || e}`);
  }
  return lines.join("\n");
}

/** Repair embeddable CPython layout if antivirus/install stripped encodings. */
function ensureEmbedStdlib(pyDir) {
  if (process.platform !== "win32" || !pyDir || !fs.existsSync(pyDir)) return;
  let names;
  try {
    names = fs.readdirSync(pyDir);
  } catch {
    return;
  }
  const zipName = names.find((n) => /^python\d+\.zip$/i.test(n));
  if (!zipName) return;
  const pthPath = path.join(pyDir, zipName.replace(/\.zip$/i, "._pth"));
  const pthBody = `Lib\r\n${zipName}\r\nLib\\site-packages\r\nimport site\r\n`;
  try {
    fs.writeFileSync(pthPath, pthBody);
  } catch (e) {
    console.error(`[backend] cannot write ${pthPath}: ${e?.message || e}`);
  }
  const encPy = path.join(pyDir, "Lib", "encodings", "__init__.py");
  const encPyc = path.join(pyDir, "Lib", "encodings", "__init__.pyc");
  if (fs.existsSync(encPy) || fs.existsSync(encPyc)) return;
  const zipPath = path.join(pyDir, zipName);
  const lib = path.join(pyDir, "Lib");
  try {
    fs.mkdirSync(lib, { recursive: true });
  } catch {
    return;
  }
  console.warn(`[backend] Lib/encodings missing — extracting ${zipName} into Lib`);
  try {
    execFileSync("tar", ["-xf", zipPath, "-C", lib], {
      windowsHide: true,
      timeout: 120000,
      stdio: ["ignore", "ignore", "pipe"],
    });
  } catch (e1) {
    try {
      const q = (s) => `'${String(s).replace(/'/g, "''")}'`;
      execFileSync(
        windowsPowershellExe(),
        [
          "-NoProfile",
          "-NonInteractive",
          "-Command",
          `Expand-Archive -LiteralPath ${q(zipPath)} -DestinationPath ${q(lib)} -Force`,
        ],
        {
          windowsHide: true,
          timeout: 120000,
          stdio: ["ignore", "ignore", "pipe"],
        },
      );
    } catch (e2) {
      console.error(
        `[backend] stdlib extract failed: ${e2?.stderr || e2?.message || e1?.message || e2}`,
      );
    }
  }
}

function pythonHasFastapi(py) {
  try {
    const dir = path.dirname(py);
    if (app.isPackaged && dir && dir !== ".") ensureEmbedStdlib(dir);
    const opts = {
      stdio: ["ignore", "pipe", "pipe"],
      timeout: 20000,
      windowsHide: true,
      shell: false,
      env: pythonChildEnv(dir),
    };
    if (dir && dir !== "." && fs.existsSync(dir)) opts.cwd = dir;
    execFileSync(py, ["-c", "import encodings, fastapi, pydantic_core"], opts);
    return true;
  } catch (e) {
    lastPythonCheckError = String(e?.stderr || e?.message || e || "").trim();
    return false;
  }
}

/** Prefer env / tip file / project .venv that actually has Sidekick deps. */
function pythonBin() {
  for (const py of pythonCandidates()) {
    const isBare = py === "python" || py === "python3";
    if (!isBare && !fs.existsSync(py)) continue;
    if (pythonHasFastapi(py)) {
      console.log(`[backend] using python: ${py}`);
      return py;
    }
  }
  // A packaged build must never silently fall through to whatever "python"
  // happens to be on this machine's PATH — that produces a confusing crash
  // pointing at some unrelated system Python install instead of telling the
  // user the bundled runtime itself is broken.
  if (app.isPackaged) return null;
  const fallback = process.platform === "win32" ? "python" : "python3";
  console.error(
    `[backend] WARNING: no python with fastapi found; falling back to ${fallback}`,
  );
  return fallback;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function fetchHealth(url, timeoutMs = 1500) {
  return new Promise((resolve) => {
    const req = http.get(url, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        let json = null;
        try {
          json = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        } catch {
          json = null;
        }
        resolve({
          up: Boolean(res.statusCode && res.statusCode < 500),
          json,
        });
      });
    });
    req.on("error", () => resolve({ up: false, json: null }));
    req.setTimeout(timeoutMs, () => {
      req.destroy();
      resolve({ up: false, json: null });
    });
  });
}

function healthIsCurrent(json) {
  // auth_register is a capability flag: true only before first account exists.
  // After setup it is false — that is healthy, not a stale server.
  return Boolean(json && json.ok === true && typeof json.auth_register === "boolean");
}

function backendPort() {
  try {
    return Number(new URL(BACKEND_URL).port) || 8787;
  } catch {
    return 8787;
  }
}

function pidsListeningOn(port) {
  const pids = new Set();
  try {
    if (process.platform === "win32") {
      const out = execFileSync("netstat", ["-ano"], {
        encoding: "utf8",
        windowsHide: true,
      });
      const re = new RegExp(`[:\\[]${port}(?:]|\\s)`);
      for (const line of out.split(/\r?\n/)) {
        if (!/LISTENING/i.test(line) || !re.test(line)) continue;
        const pid = Number(line.trim().split(/\s+/).pop());
        if (pid > 0) pids.add(pid);
      }
    } else {
      try {
        const out = execFileSync("lsof", ["-nP", `-iTCP:${port}`, "-sTCP:LISTEN", "-t"], {
          encoding: "utf8",
        });
        for (const tok of out.split(/\s+/)) {
          const pid = Number(tok);
          if (pid > 0) pids.add(pid);
        }
      } catch {
        // 麒麟 / 精简 Linux 常无 lsof，退回 ss。
        try {
          const out = execFileSync("ss", ["-lptn", `sport = :${port}`], {
            encoding: "utf8",
          });
          for (const m of out.matchAll(/pid=(\d+)/g)) {
            const pid = Number(m[1]);
            if (pid > 0) pids.add(pid);
          }
        } catch {
          /* none listening */
        }
      }
    }
  } catch {
    /* none listening */
  }
  return [...pids];
}

function killPids(pids) {
  for (const pid of pids) {
    try {
      if (process.platform === "win32") {
        execFileSync("taskkill", ["/PID", String(pid), "/T", "/F"], {
          windowsHide: true,
          stdio: "ignore",
        });
      } else {
        process.kill(pid, "SIGTERM");
      }
    } catch {
      /* already gone */
    }
  }
}

async function replaceStaleBackend() {
  const port = backendPort();
  const pids = pidsListeningOn(port);
  console.warn(
    `[backend] ${BACKEND_URL} is stale (missing auth_register); replacing pids ${pids.join(",") || "?"}`,
  );
  killPids(pids);
  const healthUrl = `${BACKEND_URL}/api/health`;
  const deadline = Date.now() + 8000;
  while (Date.now() < deadline) {
    const h = await fetchHealth(healthUrl, 800);
    if (!h.up) return;
    await sleep(200);
  }
}

function waitForHealth(url, timeoutMs = 120000) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const tick = async () => {
      const h = await fetchHealth(url);
      if (h.up && healthIsCurrent(h.json)) {
        resolve(h);
        return;
      }
      if (Date.now() - start > timeoutMs) {
        reject(new Error(`Backend health timeout: ${url}`));
        return;
      }
      setTimeout(tick, 400);
    };
    void tick();
  });
}

function isRetryableLoadError(err) {
  const msg = String(err?.message || err?.code || err || "");
  return /ERR_CONNECTION|ECONNREFUSED|ERR_FAILED|ERR_ABORTED|ERR_EMPTY_RESPONSE|ERR_CONNECTION_RESET|ERR_CONNECTION_TIMED_OUT/i.test(
    msg,
  );
}

async function loadUiWithRetry(win, uiUrl, attempts = 10) {
  let lastErr = null;
  for (let i = 0; i < attempts; i += 1) {
    try {
      await win.loadURL(uiUrl);
      return;
    } catch (e) {
      lastErr = e;
      if (!isRetryableLoadError(e) && i > 0) break;
      await sleep(400 * Math.min(i + 1, 5));
    }
  }
  throw lastErr || new Error(`Failed to load UI: ${uiUrl}`);
}

function backendNotReadyHtml(detail) {
  const escaped = String(detail || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  return (
    "<!doctype html><meta charset=utf-8><title>EdgeSidekick</title>" +
    "<body style=\"font-family:sans-serif;padding:48px;max-width:640px\">" +
    "<h1>无法连接本地后端</h1>" +
    "<p>桌面端已启动，但 <code>http://127.0.0.1:8787</code> 尚未就绪。这不是业务接口报错。</p>" +
    "<pre style=\"white-space:pre-wrap;background:#f4f4f4;padding:12px\">" +
    escaped +
    "</pre><p>请查看用户数据目录下 logs/backend.log 后重开应用。</p></body>"
  );
}

function startBackend() {
  if (process.env.SIDEKICK_SKIP_BACKEND === "1") return null;
  const root = payloadRoot();
  const py = pythonBin();
  if (!py) {
    // app.isPackaged only (dev always returns a fallback string above).
    const bundled = path.join(
      payloadRoot(),
      "python",
      process.platform === "win32" ? "python.exe" : "bin/python3",
    );
    const detail =
      `${describePythonRuntime(bundled)}\n\n` +
      `failed to import FastAPI/Pydantic:\n${lastPythonCheckError || "(no error captured)"}\n\n` +
      "No module named 'encodings' means the bundled CPython stdlib was not " +
      "found (pythonXX.zip / pythonXX._pth / Lib\\encodings). Typical causes: " +
      "antivirus stripped files during install, or a host PYTHONHOME leaked " +
      "into the child process. Try:\n" +
      "  1. Uninstall, add an antivirus exclusion for the install folder, reinstall.\n" +
      "  2. Rebuild the installer with: scripts\\build-windows.ps1 -Force\n" +
      "  3. Or set SIDEKICK_PYTHON to a working python.exe with pip install -r requirements.txt.";
    console.error(`[backend] fatal: ${detail}`);
    backendStartupFatal = true;
    try {
      dialog.showErrorBox("EdgeSidekick — 后端运行环境损坏 / Backend runtime broken", detail);
    } catch {
      /* headless / no display */
    }
    return null;
  }
  const env = pythonChildEnv(path.dirname(py));
  if (!env.PLAYWRIGHT_DOWNLOAD_HOST) {
    env.PLAYWRIGHT_DOWNLOAD_HOST = "https://npmmirror.com/mirrors/playwright";
  }
  if (!env.PIP_INDEX_URL) {
    env.PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple";
  }
  if (!env.PIP_TRUSTED_HOST) {
    env.PIP_TRUSTED_HOST = "pypi.tuna.tsinghua.edu.cn";
  }
  env.SIDEKICK_CDP_URL = env.SIDEKICK_CDP_URL || `http://127.0.0.1:${CDP_PORT}`;
  env.SIDEKICK_CDP_PORT = env.SIDEKICK_CDP_PORT || CDP_PORT;
  if (app.isPackaged) {
    env.SIDEKICK_REPO_ROOT = root;
    env.SIDEKICK_DATA_DIR = dataDir();
    const pwBrowsers = path.join(root, "ms-playwright");
    if (fs.existsSync(pwBrowsers)) {
      env.PLAYWRIGHT_BROWSERS_PATH = pwBrowsers;
    }
  }
  const child = spawn(py, [path.join(root, "main.py"), "serve"], {
    cwd: root,
    env,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  let logStream = null;
  try {
    logStream = fs.createWriteStream(path.join(logDir(), "backend.log"), {
      flags: "a",
    });
  } catch {
    /* ignore */
  }
  const pipe = (d, stream) => {
    stream.write(`[backend] ${d}`);
    try {
      logStream?.write(d);
    } catch {
      /* ignore */
    }
  };
  child.stdout?.on("data", (d) => pipe(d, process.stdout));
  child.stderr?.on("data", (d) => pipe(d, process.stderr));
  child.on("exit", (code, signal) => {
    console.error(`[backend] exited code=${code} signal=${signal || ""}`);
    try {
      logStream?.end();
    } catch {
      /* ignore */
    }
  });
  return child;
}

function emitNav(url) {
  mainWindow?.webContents.send("browser:navigated", url);
}

function ensureBrowserView() {
  if (browserView) return browserView;
  browserView = new BrowserView({
    webPreferences: {
      preload: path.join(__dirname, "guest-preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      partition: "persist:sidekick-guest",
      // Localhost Vite/Vue apps often need this for HMR websockets.
      webSecurity: true,
    },
  });
  try {
    browserView.setBackgroundColor("#111111");
  } catch {
    /* older electron */
  }
  try {
    browserView.webContents.setBackgroundThrottling(false);
  } catch {
    /* older electron */
  }
  browserView.webContents.setWindowOpenHandler(({ url }) => {
    if (browserView && /^https?:\/\//i.test(url)) {
      void browserView.webContents.loadURL(url);
      return { action: "deny" };
    }
    void shell.openExternal(url);
    return { action: "deny" };
  });
  try {
    browserView.webContents.setUserAgent(
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    );
  } catch {
    /* older electron */
  }
  const STEALTH_JS = `(() => {
    try {
      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
      if (!window.chrome) window.chrome = { runtime: {} };
    } catch (_) {}
  })();`;
  browserView.webContents.on("dom-ready", () => {
    void browserView.webContents.executeJavaScript(STEALTH_JS, true).catch(() => undefined);
  });
  try {
    browserView.webContents.setMaxListeners(32);
  } catch {
    /* ignore */
  }
  browserView.webContents.on("did-navigate", (_e, url) => emitNav(url));
  browserView.webContents.on("did-navigate-in-page", (_e, url) => emitNav(url));
  browserView.webContents.on("did-redirect-navigation", (_e, url) => emitNav(url));
  browserView.webContents.on(
    "did-fail-load",
    (_e, code, desc, url, isMainFrame) => {
      if (!isMainFrame) return;
      mainWindow?.webContents.send("browser:load-fail", {
        code,
        description: desc,
        url,
      });
    },
  );
  // Start the guest renderer so Playwright CDP can see __sidekickGuest
  // even before the user types a URL.
  try {
    void browserView.webContents.loadURL("about:blank");
  } catch {
    /* ignore */
  }
  return browserView;
}

function setBrowserBounds(bounds) {
  if (!browserView || !mainWindow || !bounds) return;
  const b = {
    x: Math.max(0, Math.round(Number(bounds.x) || 0)),
    y: Math.max(0, Math.round(Number(bounds.y) || 0)),
    width: Math.max(40, Math.round(Number(bounds.width) || 40)),
    height: Math.max(40, Math.round(Number(bounds.height) || 40)),
  };
  lastBounds = b;
  browserView.setBounds(b);
}

function attachBrowserView() {
  if (!mainWindow || !browserView) return;
  const views = typeof mainWindow.getBrowserViews === "function"
    ? mainWindow.getBrowserViews()
    : [];
  if (!views.includes(browserView)) {
    if (typeof mainWindow.addBrowserView === "function") {
      mainWindow.addBrowserView(browserView);
    } else {
      mainWindow.setBrowserView(browserView);
    }
  }
  // Keep the live view above the workbench chrome.
  if (typeof mainWindow.setTopBrowserView === "function") {
    try {
      mainWindow.setTopBrowserView(browserView);
    } catch {
      /* ignore */
    }
  }
}

function boundsUsable(b) {
  return Boolean(
    b &&
      Number(b.width) >= 80 &&
      Number(b.height) >= 80,
  );
}

function showBrowser(bounds) {
  ensureBrowserView();
  if (!mainWindow) return;
  attachBrowserView();
  browserVisible = true;
  // Never paint the live guest at the startup 1x1 placeholder — ask the
  // renderer for real host bounds instead (social modal / sidebar mount).
  if (boundsUsable(bounds)) setBrowserBounds(bounds);
  else if (boundsUsable(lastBounds)) setBrowserBounds(lastBounds);
  else {
    mainWindow.webContents.send("browser:requestBounds");
  }
}

function hideBrowser() {
  if (!mainWindow || !browserView) return;
  // Keep the guest attached. Removing it drops the page from CDP, and the
  // session browser then fails with "in-app browser panel not ready" because
  // that mode never opens the workbench sidebar first.
  // Park it off-screen instead. Do not write lastBounds — the next show()
  // must restore the real frame, not this parking spot.
  attachBrowserView();
  browserVisible = false;
  try {
    browserView.setBounds({ x: -20000, y: 0, width: 800, height: 600 });
  } catch {
    /* ignore */
  }
}

function requestBoundsSoon() {
  if (!browserVisible) return;
  mainWindow?.webContents.send("browser:requestBounds");
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1480,
    height: 960,
    minWidth: 1024,
    minHeight: 680,
    title: "EdgeSidekick",
    backgroundColor: "#ffffff",
    show: false,
    icon: path.join(__dirname, "build", "icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.once("ready-to-show", () => mainWindow?.show());
  setTimeout(() => {
    if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.isVisible()) {
      mainWindow.show();
    }
  }, 8000);

  for (const ev of ["resize", "maximize", "unmaximize", "enter-full-screen", "leave-full-screen"]) {
    mainWindow.on(ev, () => requestBoundsSoon());
  }
  mainWindow.on("move", () => requestBoundsSoon());

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (event, url) => {
    try {
      const u = new URL(url);
      if (/\.(html?|pdf)$/i.test(u.pathname)) event.preventDefault();
    } catch {
      /* ignore */
    }
  });

  const uiUrl =
    process.env.SIDEKICK_UI_URL ||
    (isDev ? "http://127.0.0.1:5177" : `${BACKEND_URL}/`);

  try {
    await mainWindow.webContents.session.clearCache();
  } catch {
    /* ignore */
  }
  try {
    await loadUiWithRetry(mainWindow, uiUrl);
  } catch (e) {
    const detail =
      `${e?.message || e}\n\nUI: ${uiUrl}\n日志: ${path.join(logDir(), "backend.log")}`;
    console.error("[ui] load failed", e);
    try {
      await mainWindow.loadURL(
        "data:text/html;charset=utf-8," + encodeURIComponent(backendNotReadyHtml(detail)),
      );
    } catch {
      dialog.showErrorBox("EdgeSidekick", detail);
    }
  }
  // Create the guest BrowserView up front so Playwright can attach over CDP
  // without launching a second Chromium window. Attach once so the renderer
  // process actually starts, then hide until the Browser sidebar is shown.
  ensureBrowserView();
  attachBrowserView();
  try {
    browserView.setBounds({ x: 0, y: 0, width: 1, height: 1 });
  } catch {
    /* ignore */
  }
  hideBrowser();
}

function registerIpc() {
  ipcMain.handle("browser:show", (_e, bounds) => {
    showBrowser(bounds);
    return { ok: true, live: true };
  });
  ipcMain.handle("browser:hide", () => {
    hideBrowser();
    return { ok: true };
  });
  ipcMain.handle("browser:scrollPage", async (_e, kind = "bottom") => {
    if (!browserView) return { ok: false };
    const js =
      kind === "top"
        ? "window.scrollTo({ top: 0, behavior: 'smooth' })"
        : kind === "up"
          ? "window.scrollBy({ top: -Math.max(240, window.innerHeight * 0.85), behavior: 'smooth' })"
          : kind === "down"
            ? "window.scrollBy({ top: Math.max(240, window.innerHeight * 0.85), behavior: 'smooth' })"
            : "window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' })";
    try {
      await browserView.webContents.executeJavaScript(js, true);
      return { ok: true };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  });
  ipcMain.handle("browser:setBounds", (_e, bounds) => {
    if (browserVisible) {
      if (boundsUsable(bounds)) setBrowserBounds(bounds);
      else if (bounds) lastBounds = bounds;
    } else if (bounds) {
      lastBounds = bounds;
    }
    return { ok: true };
  });
  ipcMain.handle("browser:navigate", async (_e, url) => {
    const candidates = loopbackUrlCandidates(url);
    if (!candidates.length) throw new Error("empty url");
    const run = async () => {
      const view = ensureBrowserView();
      // Prefer last known panel bounds; otherwise attach + requestBounds
      // (do not flash the 1x1 CDP placeholder over the social UI).
      showBrowser(boundsUsable(lastBounds) ? lastBounds : null);
      let current = "";
      try {
        current = view.webContents.getURL();
      } catch {
        current = "";
      }
      if (candidates.some((c) => urlsMatch(current, c))) {
        emitNav(current);
        requestBoundsSoon();
        return { url: current, live: true };
      }
      // Queued/stale navigations (App + BrowserPanel both drive loadURL on
      // browser_open) must not rewind a search-results / article page back to
      // the site homepage after the agent has already moved on.
      if (candidates.some((c) => isShallowSiteRewind(current, c))) {
        emitNav(current);
        requestBoundsSoon();
        return {
          url: current,
          live: true,
          blocked_rewind: true,
          note: "refused navigate to site home — guest is already on a deeper page",
        };
      }
      let lastErr = null;
      for (const target of candidates) {
        try {
          await view.webContents.loadURL(target);
          lastErr = null;
          break;
        } catch (err) {
          lastErr = err;
          if (isAbortedNav(err)) {
            const landed = await waitForGuestUrl(view, candidates, 8000);
            if (candidates.some((c) => urlsMatch(landed, c)) || (landed && landed !== "about:blank")) {
              lastErr = null;
              current = landed;
              break;
            }
            continue;
          }
          if (!isConnectionRefused(err)) {
            const nice = friendlyNavError(err, target);
            throw new Error(nice || String(err?.message || err));
          }
        }
      }
      if (lastErr) {
        const landed = await waitForGuestUrl(view, candidates, 2500);
        if (candidates.some((c) => urlsMatch(landed, c)) || (landed && landed !== "about:blank")) {
          current = landed;
        } else {
          const nice = friendlyNavError(lastErr, candidates[0]);
          throw new Error(nice || String(lastErr?.message || lastErr));
        }
      }
      current = current || view.webContents.getURL();
      emitNav(current);
      requestBoundsSoon();
      return { url: current, live: true };
    };
    const pending = navChain.then(run, run);
    navChain = pending.catch(() => {});
    return pending;
  });
  ipcMain.handle("browser:getUrl", () => {
    if (!browserView) return "about:blank";
    return browserView.webContents.getURL();
  });
  ipcMain.handle("browser:reload", async () => {
    if (!browserView) return { ok: false };
    browserView.webContents.reload();
    return { ok: true };
  });
  ipcMain.handle("browser:goBack", async () => {
    if (!browserView?.webContents.navigationHistory?.canGoBack?.()) {
      if (browserView?.webContents.canGoBack?.()) browserView.webContents.goBack();
      return { ok: true };
    }
    if (browserView.webContents.navigationHistory.canGoBack()) {
      browserView.webContents.navigationHistory.goBack();
    }
    return { ok: true };
  });
  ipcMain.handle("browser:goForward", async () => {
    if (!browserView?.webContents.navigationHistory?.canGoForward?.()) {
      if (browserView?.webContents.canGoForward?.()) browserView.webContents.goForward();
      return { ok: true };
    }
    if (browserView.webContents.navigationHistory.canGoForward()) {
      browserView.webContents.navigationHistory.goForward();
    }
    return { ok: true };
  });
  ipcMain.handle("browser:selectCancel", async () => {
    if (!browserView) return { ok: true };
    try {
      await browserView.webContents.executeJavaScript(
        "window.__sidekickSelectCancel && window.__sidekickSelectCancel(); true",
        true,
      );
    } catch {
      /* ignore */
    }
    return { ok: true };
  });
  ipcMain.handle("browser:selectArm", async (_e, timeoutMs) => {
    if (!browserView) throw new Error("live browser not ready — open a URL first");
    showBrowser(boundsUsable(lastBounds) ? lastBounds : null);
    const boot = buildSelectBootstrap(STYLE_KEYS, 1);
    await browserView.webContents.executeJavaScript(boot, true);
    const ms = Math.max(1000, Number(timeoutMs) || 60000);
    const raw = await browserView.webContents.executeJavaScript(
      `window.__sidekickSelectArm(${ms})`,
      true,
    );
    return raw || null;
  });
  ipcMain.handle("workspace:pickFolder", async (_e, optsIn) => {
    const opts = {
      title: String(optsIn?.title || "选择工作区文件夹"),
      properties: ["openDirectory", "createDirectory"],
    };
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.focus();
      const result = await dialog.showOpenDialog(mainWindow, opts);
      if (result.canceled || !result.filePaths?.[0]) {
        return { cancelled: true, path: null };
      }
      return { cancelled: false, path: result.filePaths[0] };
    }
    const result = await dialog.showOpenDialog(opts);
    if (result.canceled || !result.filePaths?.[0]) {
      return { cancelled: true, path: null };
    }
    return { cancelled: false, path: result.filePaths[0] };
  });
}

app.setAppUserModelId("com.edgesidekick.desktop");

// Windows Chromium often fails GPUCache move with Access Denied (0x5),
// especially when productName/userData is non-ASCII or another
// Electron still holds the folder. Must run before app.ready.
if (process.platform === "win32") {
  app.commandLine.appendSwitch("disable-gpu-shader-disk-cache");
  if (app.isPackaged) {
    try {
      app.setPath("userData", path.join(app.getPath("appData"), "EdgeSidekick"));
    } catch {
      /* keep default */
    }
  }
}

// Must be before app.ready. Playwright connects here and drives the in-app
// BrowserView instead of spawning a popup Chromium.
app.commandLine.appendSwitch("remote-debugging-port", CDP_PORT);
app.commandLine.appendSwitch("remote-debugging-address", "127.0.0.1");
// Pages open RTCPeerConnection and Chromium then logs a failed STUN lookup
// (stun.services.mozilla1.com, error -105). This app does not use WebRTC.
app.commandLine.appendSwitch("disable-features", "WebRTC,WebRtcHideLocalIpsWithMdns");
app.commandLine.appendSwitch("webrtc-ip-handling-policy", "disable_non_proxied_udp");

const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
    }
  });
}

if (gotTheLock) app.whenReady().then(async () => {
  registerIpc();

  const healthUrl = `${BACKEND_URL}/api/health`;
  const existing = await fetchHealth(healthUrl);
  let backendReady = existing.up && healthIsCurrent(existing.json);
  if (backendReady) {
    console.log(`[backend] reusing existing server at ${BACKEND_URL}`);
  } else {
    if (existing.up) {
      await replaceStaleBackend();
    }
    backendProc = startBackend();
    if (backendStartupFatal) {
      app.quit();
      return;
    }
    try {
      await waitForHealth(healthUrl, app.isPackaged ? 180000 : 120000);
      backendReady = true;
    } catch (e) {
      const late = await fetchHealth(healthUrl);
      if (late.up && healthIsCurrent(late.json)) {
        console.warn(`[backend] health wait timed out but current health passed; opening`);
        backendReady = true;
      } else {
        console.error(e);
        const logPath = path.join(logDir(), "backend.log");
        const why = late.up
          ? "8787 端口有响应，但不是本应用健康检查（可能被其它程序占用）。"
          : "本机后端没有在时限内就绪。";
        dialog.showErrorBox(
          "EdgeSidekick",
          `${why}\n\n${e?.message || e}\n\n日志：${logPath}\n\n` +
            (app.isPackaged
              ? "请重新安装，或检查 8787 端口是否被占用。"
              : "请先运行 start-desktop.bat 安装 Python 依赖。"),
        );
      }
    }
  }

  if (backendReady) {
    // Health can pass a beat before the static UI mount is fully in service.
    await sleep(200);
  }
  await createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) void createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  hideBrowser();
  // Only kill backend we started ourselves.
  if (backendProc && !backendProc.killed) {
    try {
      if (process.platform === "win32") {
        spawn("taskkill", ["/pid", String(backendProc.pid), "/T", "/F"]);
      } else {
        backendProc.kill("SIGTERM");
      }
    } catch {
      /* ignore */
    }
  }
});
