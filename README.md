<p align="center">
  English | <a href="./README_CN.md">简体中文</a>
</p>

<p align="center">
  <img src="./website/assets/brand-icon.png" alt="EdgeSidekick" width="96" />
</p>

<p align="center">
  <b>EdgeSidekick</b> — local-first multi-agent AI workbench for your own machine
</p>

<p align="center">
  <a href="./website/index.html">Website (local)</a> ·
  <a href="https://YOUR_USER.github.io/EdgeSidekick/">GitHub Pages (placeholder)</a> ·
  <a href="./website/docs/user-guide.en.html">User guide</a> ·
  <a href="./LICENSE">MIT License</a>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-blue" />
  <img alt="Node" src="https://img.shields.io/badge/Node.js-18%2B-green" />
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey" />
  <img alt="License" src="https://img.shields.io/badge/license-MIT-yellow" />
</p>

> **Website address**
> - **Ship with the repo (open now):** [website/index.html](./website/index.html) — or serve the folder: python -m http.server 8080 inside website/, then visit http://127.0.0.1:8080/
> - **Planned public URL (not published yet):** https://YOUR_USER.github.io/EdgeSidekick/ — replace YOUR_USER after you enable GitHub Pages. Until then, use the local path above.

**EdgeSidekick** is an open-source **local multi-agent AI workbench**. Open a folder on your disk, chat in the browser or desktop shell, and let a main agent plus sub-agents search and edit files — with human approval, drop-in Skills, Memory that sticks, MCP tools, and a browser sandbox.

Built to be **forked** (clear Python + Vite layers) and **cheap to run** (context compression, on-demand Skills, separate main / sub / compress models).

**Windows · macOS · Linux** · default API **http://127.0.0.1:8787**

## Screenshots

![Workbench — light](./docs/screenshots/gallery-workbench-light.png)

![Workbench — dark](./docs/screenshots/gallery-workbench-dark.png)

More product pages and the bilingual marketing site live under [website/](./website/).

## Quick start

> Requirements:
>
> - Python **3.10+**
> - Node.js **18+** (UI / desktop build)
> - Optional: Playwright Chromium for the browser sandbox / agent browser tools

### Run the API + web UI

**Windows (PowerShell)**

`powershell
cd path\to\Sidekick
python -m pip install -r requirements.txt
python main.py
`

Or double-click **start.bat**.

**macOS / Linux**

`ash
cd /path/to/Sidekick
python3 -m pip install -r requirements.txt
python3 main.py
`

Open **http://127.0.0.1:8787** → pick a workspace folder → Settings → Model → API key.

### Desktop app

`powershell
.\start-desktop.bat
`

Creates a project-local .venv, installs Python + Playwright Chromium if needed, builds the UI, and launches Electron with a live embedded browser preview.

See [desktop/README.md](./desktop/README.md) and [docs/browser-sandbox.md](./docs/browser-sandbox.md).

### Offline installers

- **Windows:** .\scripts\build-windows.bat → desktop/dist/EdgeSidekick-Setup-<version>-win-x64.exe — [packaging/windows/README.md](./packaging/windows/README.md)
- **Kylin / Linux:** ash scripts/build-kylin.sh → .deb / AppImage / tar.gz — [packaging/kylin/README.md](./packaging/kylin/README.md)

Installed app data defaults to %APPDATA%\EdgeSidekick\ (Windows) or ~/.config/EdgeSidekick/ (Linux).

> Never commit real API keys, .env, chat sessions, or model.json (see .gitignore).

### Local security (defaults)

- Binds **127.0.0.1** only; non-loopback bind requires META_ALLOW_REMOTE=1 (unsafe).
- Local token auth (X-Sidekick-Token); UI bootstraps via /api/bootstrap.
- Model API keys encrypted at rest under the local data dir.
- Shell tools go through the agent **approval gate** (disable with META_ALLOW_SHELL=0).

## Key features

**1. Workbench**  
Streaming chat (SSE), attachments, stop / edit-resend, content search, and a detail panel tied to your workspace files.

**2. Social mode**  
Session-oriented chat UI alongside the classic workbench — same local runtime, different layout for conversation-first use.

**3. Browser sandbox**  
Desktop: live Electron BrowserView. Web: Playwright-backed preview. Select Mode sends DOM picks to the agent; agents can use rowser_* tools.

**4. Skills**  
Drop skill packs under src/skills/ (or tenant skill dirs) and invoke with /skill <name>. Book → Skill can distill PDF/EPUB offline — see [docs/book-to-skill.md](./docs/book-to-skill.md).

**5. Desktop shell**  
One-click Electron app with embedded preview, packaging for Windows and Kylin/Linux, no cloud account required to run locally.

**6. MCP**  
Discover and connect Model Context Protocol servers from Settings → MCP (META_MCP_ENABLED=1).

**7. Light / dark + bilingual UI**  
Chinese / English interface and light / dark themes (local preference keys).

**8. Local-first**  
History, Memory (MEMORY.md), workspaces, and configs stay on disk you control. Bring your own model API (OpenAI-compatible, DeepSeek, Ollama, etc.).

### Slash commands

/help · /new · /clear · /skills · /skill <name> · /memory · /history · /browser · /stop

## Using EdgeSidekick

- **From source** — follow [Quick start](#quick-start).
- **Desktop** — start-desktop.bat or packaged installers under desktop/dist/.
- **Website & handbook** — [website/index.html](./website/index.html), [website/docs/user-guide.en.html](./website/docs/user-guide.en.html) / [user-guide.zh.html](./website/docs/user-guide.zh.html).
- **Docs** — [docs/browser-sandbox.md](./docs/browser-sandbox.md), [docs/book-to-skill.md](./docs/book-to-skill.md).

## Architecture (short)

`
Sidekick/
├── src/metateam/      # API, agents, tools, approval, compression
├── src/skills/       # drop-in Skills
├── src/memory/       # MEMORY.md
├── src/data/         # runtime data (local; not committed)
├── src/sessions/     # chat sessions (local; not committed)
├── ui/               # Vite + React control UI
├── desktop/          # Electron shell
├── packaging/        # Windows / Kylin offline packaging notes
├── website/          # static marketing site + handbooks
├── docs/
├── main.py
├── start.bat
└── start-desktop.bat
`

## Contributing

Contributions of all kinds are welcome:

- **Code / docs** — read [CONTRIBUTING.md](./CONTRIBUTING.md).
- **Ideas** — GitHub Discussions (placeholder until the public repo is live).
- **Translations** — keep README.md (EN) and README_CN.md (中文) in sync for short user-facing copy.

## Community

Placeholders until channels are published:

- GitHub Discussions — Q&A and ideas
- GitHub Issues — reproducible bugs
- Discord / chat — real-time help (link TBD)
- X / social — release notes (handle TBD)

## Security disclosure

Please report vulnerabilities privately per [SECURITY.md](./SECURITY.md). Do not post exploits in public issues.

## License & third-party notices

This repository is licensed under the [MIT License](./LICENSE).

### Project license

| Component | License |
|-----------|---------|
| EdgeSidekick (this repo) | **MIT** |

### Notable direct dependencies (scan of 
equirements.txt + ui/package.json + desktop/package.json)

| Package | Typical license |
|---------|-----------------|
| fastapi, pydantic, mcp, pytest, react, react-dom, react-markdown, remark-gfm, rehype-highlight, mermaid, vite, electron, electron-builder | MIT |
| openai, playwright, python-multipart, cryptography (Apache portion), typescript | Apache-2.0 |
| httpx, uvicorn, python-dotenv, sse-starlette, pypdf, highlight.js | BSD-style |
| cryptography | Apache-2.0 / BSD |

Exact texts ship inside each package (and under .venv / 
ode_modules when installed). This table is a convenience notice, not a substitute for upstream LICENSE files.

---

**Product name:** EdgeSidekick · **Default bind:** 127.0.0.1:8787
