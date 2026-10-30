(function () {
  var i18n = {
    zh: {
      nav_home: '首页', nav_features: '功能', nav_docs: '手册', nav_download: '下载',
      nav_cta: '下载安装包',
      badge: '开源 · 本地优先 · 多智能体',
      hero_title_sub: '本地多智能体 AI 工作台',
      hero_lead: '打开本机文件夹，在浏览器或桌面端与主 / 子智能体协作。变更需确认；数据与密钥留在本地。',
      hero_cta1: '下载 Windows 安装包', hero_cta2: '功能与架构',
      arch_title: '核心架构',
      arch_sub: '客户端、后端智能体运行时与本地磁盘分层清楚，便于二开与本机部署。',
      arch_cap: 'Web / 桌面 → REST+SSE API → 主智能体（规划、工具、审批、压缩）→ 工作区 / Skills / Memory / 你的模型 API。',
      home_feat_title: '核心能力',
      home_feat_sub: '对齐产品真实能力：工作台、多智能体协作、模型配置、浏览器沙盒、Skills、桌面端与本地优先。',
      k1: 'WORKSPACE', t1: '工作台与工作区', d1: '打开本地文件夹作为工作区。SSE 流式对话、附件、停止 / 编辑重发、内容搜索，以及与工作区文件联动的详情面板。',
      k2: 'AGENTS', t2: '多智能体协作', d2: '主智能体编排子智能体：单人回答、并行分工、接力流水线或多视角讨论；计划卡展示分工，失败可转交。',
      k3: 'MODELS', t3: '模型与密钥', d3: '主模型 / 子智能体 / 压缩模型可分开配置。自备 OpenAI 兼容、DeepSeek、Ollama 等 API；密钥本机加密存储。',
      k4: 'BROWSER', t4: '浏览器沙盒', d4: '桌面 Electron BrowserView 实况预览；Web 端 Playwright。Select Mode 点选 DOM，Agent 使用 browser_* 工具。',
      k5: 'EXTEND', t5: 'Skills · MCP · 插件', d5: '技能包按需 /skill 调用；设置中连接 MCP；插件市场安装扩展（独立进程，写操作需确认）。',
      k6: 'DESKTOP', t6: '桌面端与本地优先', d6: 'Windows / 麒麟安装包或 start-desktop.bat。历史、Memory、配置均在本机磁盘；默认仅绑定 127.0.0.1。',
      more_features: '查看完整功能与架构图',
      local_title: '本机默认安全',
      local_1: '仅绑定 127.0.0.1',
      local_2: '本地令牌鉴权（X-Sidekick-Token）',
      local_3: '模型 API Key 加密存储',
      local_4: 'Shell / 写文件走审批门',
      who_title: '适合谁',
      who_1: '需要本机多智能体工作台的个人与团队',
      who_2: '希望自备模型、数据不出机的场景',
      who_3: '要二次开发（Python + Vite 分层）的开发者',
      foot: 'EdgeSidekick · 本地多智能体 AI 工作台',
      page_title_home: 'EdgeSidekick · 本地多智能体 AI 工作台',
      page_desc_home: 'EdgeSidekick — 本地多智能体 AI 工作台。开源、本地优先。',
      feat_page_title: '功能与架构',
      feat_page_sub: '从工作区到多智能体、模型、桌面端与扩展能力的完整说明。',
      how_title: '上手流程',
      s1t: '打开工作区', s1d: '选择本地文件夹；历史与 Memory 落在你控制的磁盘。',
      s2t: '配置模型', s2d: '设置主 / 子 / 压缩模型与 API Key，或接入 Ollama。',
      s3t: '对话与确认', s3d: '写文件、Shell、记忆等变更先征求批准再执行。',
      s4t: '按需扩展', s4d: '挂载 Skills、浏览器沙盒、MCP 与插件。',
      shots_title: '界面预览',
      shot1: '浅色工作台', shot2: '深色工作台',
      demo_title: '演示页', demo_open: '打开演示动画',
      slash_title: '常用斜杠命令',
      slash_body: '/help · /new · /clear · /skills · /skill <name> · /memory · /history · /browser · /stop',
      page_title_feat: '功能 · EdgeSidekick',
      page_desc_feat: 'EdgeSidekick 功能与架构：工作台、多智能体、模型、浏览器沙盒、Skills、桌面端。',
      dl_page_title: '下载',
      dl_page_sub: '获取 Windows 安装包，或从源码运行。',
      dl_win_title: 'Windows 安装包',
      dl_win_desc: '双击安装，无需预装 Python / Node。目标机可离线安装。',
      dl_btn: '下载 Setup.exe', dl_zip: '便携版 ZIP', dl_releases: 'GitHub Releases',
      dl_note: '安装包来自 GitHub Releases 最新资源（EdgeSidekick-Setup-*-win-x64.exe）。本地构建产物：desktop/dist/。若 Releases 尚无资源，请先发布 Setup，或按下方从源码运行。',
      d2t: '麒麟 / Linux', d2d: 'deb / AppImage / tar.gz，含 arm64。见 packaging/kylin/README.md。',
      d3t: '源码直跑', d3d: 'Python 3.10+ · Node 18+；或 start-desktop.bat。',
      start_title: '从源码启动',
      start_sub: '选择工作区 → 配置模型与 API Key → 开始对话。',
      page_title_dl: '下载 · EdgeSidekick',
      page_desc_dl: '下载 EdgeSidekick Windows 安装包。'
    },
    en: {
      nav_home: 'Home', nav_features: 'Features', nav_docs: 'Docs', nav_download: 'Download',
      nav_cta: 'Get installer',
      badge: 'OPEN SOURCE · LOCAL-FIRST · MULTI-AGENT',
      hero_title_sub: 'Local multi-agent AI workbench',
      hero_lead: 'Open a local folder and collaborate with main / sub-agents in the browser or desktop app. Changes need approval; data and keys stay on your machine.',
      hero_cta1: 'Download Windows installer', hero_cta2: 'Features & architecture',
      arch_title: 'Architecture',
      arch_sub: 'Clear layers: clients, agent runtime backend, and local disk — built for local use and forks.',
      arch_cap: 'Web / Desktop → REST+SSE API → main agent (plan, tools, approval, compression) → workspace / Skills / Memory / your model API.',
      home_feat_title: 'Core capabilities',
      home_feat_sub: 'Grounded in the real product: workbench, multi-agent collaboration, models, browser sandbox, Skills, desktop, and local-first defaults.',
      k1: 'WORKSPACE', t1: 'Workbench & workspace', d1: 'Open a local folder as the workspace. SSE chat, attachments, stop / edit-resend, content search, and a detail panel tied to workspace files.',
      k2: 'AGENTS', t2: 'Multi-agent collaboration', d2: 'Main agent orchestrates sub-agents: single reply, parallel split, pipeline handoff, or multi-angle discussion — with a plan card and reassignment on failure.',
      k3: 'MODELS', t3: 'Models & keys', d3: 'Separate main / sub / compress models. Bring OpenAI-compatible, DeepSeek, Ollama, and more. API keys encrypted at rest locally.',
      k4: 'BROWSER', t4: 'Browser sandbox', d4: 'Desktop Electron BrowserView live preview; web Playwright. Select Mode picks DOM; agents use browser_* tools.',
      k5: 'EXTEND', t5: 'Skills · MCP · plugins', d5: 'Invoke skill packs with /skill; connect MCP in Settings; install plugins from the marketplace (separate process, writes need confirmation).',
      k6: 'DESKTOP', t6: 'Desktop & local-first', d6: 'Windows / Kylin installers or start-desktop.bat. History, Memory, and config stay on disk. Default bind is 127.0.0.1 only.',
      more_features: 'Full features & architecture',
      local_title: 'Local security defaults',
      local_1: 'Binds 127.0.0.1 only',
      local_2: 'Local token auth (X-Sidekick-Token)',
      local_3: 'Model API keys encrypted at rest',
      local_4: 'Shell / writes go through approval',
      who_title: 'Who it is for',
      who_1: 'Individuals and teams needing a local multi-agent workbench',
      who_2: 'Bring-your-own-model setups where data should stay on-device',
      who_3: 'Developers who want a clear Python + Vite fork surface',
      foot: 'EdgeSidekick · Local multi-agent AI workbench',
      page_title_home: 'EdgeSidekick · Local multi-agent AI workbench',
      page_desc_home: 'EdgeSidekick — local multi-agent AI workbench. Open source, local-first.',
      feat_page_title: 'Features & architecture',
      feat_page_sub: 'From workspace to multi-agent, models, desktop, and extensions.',
      how_title: 'Getting started',
      s1t: 'Open a workspace', s1d: 'Pick a local folder. History and Memory stay on disk you control.',
      s2t: 'Configure models', s2d: 'Set main / sub / compress models and API key, or plug in Ollama.',
      s3t: 'Chat with approval', s3d: 'Writes, shell, and memory changes ask first.',
      s4t: 'Extend on demand', s4d: 'Mount Skills, browser sandbox, MCP, and plugins.',
      shots_title: 'Screenshots',
      shot1: 'Light workbench', shot2: 'Dark workbench',
      demo_title: 'Demo', demo_open: 'Open promo animation',
      slash_title: 'Slash commands',
      slash_body: '/help · /new · /clear · /skills · /skill <name> · /memory · /history · /browser · /stop',
      page_title_feat: 'Features · EdgeSidekick',
      page_desc_feat: 'EdgeSidekick features and architecture: workbench, multi-agent, models, browser sandbox, Skills, desktop.',
      dl_page_title: 'Download',
      dl_page_sub: 'Get the Windows installer, or run from source.',
      dl_win_title: 'Windows installer',
      dl_win_desc: 'Double-click install. No Python / Node preinstall. Offline-capable targets.',
      dl_btn: 'Download Setup.exe', dl_zip: 'Portable ZIP', dl_releases: 'GitHub Releases',
      dl_note: 'Installer comes from the latest GitHub Release asset (EdgeSidekick-Setup-*-win-x64.exe). Local build output: desktop/dist/. If no Release asset exists yet, publish the Setup file first, or run from source below.',
      d2t: 'Kylin / Linux', d2d: 'deb / AppImage / tar.gz, including arm64. See packaging/kylin/README.md.',
      d3t: 'From source', d3d: 'Python 3.10+ · Node 18+, or start-desktop.bat.',
      start_title: 'Run from source',
      start_sub: 'Pick a workspace → set model & API key → start chatting.',
      page_title_dl: 'Download · EdgeSidekick',
      page_desc_dl: 'Download the EdgeSidekick Windows installer.'
    }
  };

  var FALLBACK = {
    setup: 'https://github.com/GQQI/EdgeSidekick/releases/latest/download/EdgeSidekick-Setup-0.3.1-win-x64.exe',
    zip: 'https://github.com/GQQI/EdgeSidekick/releases/latest/download/EdgeSidekick-0.3.1-win-x64.zip',
    releases: 'https://github.com/GQQI/EdgeSidekick/releases'
  };

  var moon = '<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/>';
  var sun = '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>';

  var lang = localStorage.getItem('esk-lang') || 'zh';
  var theme = localStorage.getItem('esk-theme') || 'light';
  var page = document.body.getAttribute('data-page') || 'home';

  function applyTheme(t) {
    theme = t;
    document.documentElement.setAttribute('data-theme', t === 'dark' ? 'dark' : 'light');
    localStorage.setItem('esk-theme', t);
    var icon = document.getElementById('themeIcon');
    if (icon) icon.innerHTML = t === 'dark' ? sun : moon;
  }

  function guideHref(l) {
    var inDocs = page === 'docs';
    var prefix = inDocs ? '' : 'docs/';
    return prefix + (l === 'en' ? 'user-guide.en.html' : 'user-guide.zh.html');
  }

  function applyLang(l) {
    lang = l;
    localStorage.setItem('esk-lang', l);
    document.documentElement.lang = l === 'en' ? 'en' : 'zh-CN';
    var dict = i18n[l] || i18n.zh;
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      var key = el.getAttribute('data-i18n');
      if (dict[key] != null) el.textContent = dict[key];
    });
    document.querySelectorAll('[data-i18n-guide]').forEach(function (a) {
      a.setAttribute('href', guideHref(l));
    });
    var lbl = document.getElementById('langToggleLabel');
    if (lbl) lbl.textContent = l === 'zh' ? 'EN' : '中文';
    var titleKey = { home: 'page_title_home', features: 'page_title_feat', download: 'page_title_dl' }[page];
    var descKey = { home: 'page_desc_home', features: 'page_desc_feat', download: 'page_desc_dl' }[page];
    if (titleKey && dict[titleKey]) document.title = dict[titleKey];
    var meta = document.querySelector('meta[name="description"]');
    if (meta && descKey && dict[descKey]) meta.setAttribute('content', dict[descKey]);
  }

  applyTheme(theme);
  applyLang(lang);

  var langBtn = document.getElementById('langToggle');
  if (langBtn) langBtn.addEventListener('click', function () { applyLang(lang === 'zh' ? 'en' : 'zh'); });
  var themeBtn = document.getElementById('themeToggle');
  if (themeBtn) themeBtn.addEventListener('click', function () { applyTheme(theme === 'dark' ? 'light' : 'dark'); });

  var menuBtn = document.getElementById('menuToggle');
  var mobile = document.getElementById('mobileMenu');
  if (menuBtn && mobile) {
    menuBtn.addEventListener('click', function () { mobile.classList.toggle('open'); });
    mobile.querySelectorAll('a').forEach(function (a) {
      a.addEventListener('click', function () { mobile.classList.remove('open'); });
    });
  }

  function wireDownload() {
    var setupBtn = document.getElementById('dlSetup');
    var zipBtn = document.getElementById('dlZip');
    var relBtn = document.getElementById('dlReleases');
    if (setupBtn) setupBtn.href = FALLBACK.setup;
    if (zipBtn) zipBtn.href = FALLBACK.zip;
    if (relBtn) relBtn.href = FALLBACK.releases;
    if (!setupBtn && !zipBtn) return;
    fetch('https://api.github.com/repos/GQQI/EdgeSidekick/releases/latest')
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (rel) {
        var assets = rel.assets || [];
        var setup = assets.find(function (a) { return /Setup-.*-win-.*\.exe$/i.test(a.name); })
          || assets.find(function (a) { return /Setup.*\.exe$/i.test(a.name); });
        var zip = assets.find(function (a) { return /win-.*\.zip$/i.test(a.name) && !/Setup/i.test(a.name); });
        if (setup && setupBtn) { setupBtn.href = setup.browser_download_url; setupBtn.setAttribute('download', setup.name); }
        if (zip && zipBtn) { zipBtn.href = zip.browser_download_url; zipBtn.setAttribute('download', zip.name); }
      })
      .catch(function () {});
  }
  wireDownload();
})();
