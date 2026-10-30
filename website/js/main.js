(function () {
  var i18n = {
    zh: {
      nav_home: '首页',
      nav_features: '功能',
      nav_docs: '手册',
      nav_download: '下载',
      nav_cta: '下载安装包',
      badge: '开源 · 本地优先 · 多智能体',
      hero_title_sub: '本地多智能体 AI 工作台',
      hero_lead: '在本机运行多智能体协作。数据留在本地，模型自备。',
      hero_cta1: '下载 Windows 安装包',
      hero_cta2: '查看功能',
      home_feat_title: '核心能力',
      home_feat_sub: '对话、浏览器沙箱、Skills、桌面端与 MCP——够用即可，不堆营销词。',
      f1t: '多智能体对话', f1d: '主智能体调度子智能体，SSE 流式输出，变更需确认。',
      f2t: '浏览器沙箱', f2d: '桌面 BrowserView / Playwright；Select Mode 点选 DOM。',
      f3t: 'Skills · MCP', f3d: '按需加载技能包与外部工具，不把巨型 prompt 塞进每一轮。',
      more_features: '全部功能',
      foot: 'EdgeSidekick · 本地多智能体 AI 工作台',
      page_title_home: 'EdgeSidekick · 本地多智能体 AI 工作台',
      page_desc_home: 'EdgeSidekick — 本地多智能体 AI 工作台。开源、本地优先。',
      feat_page_title: '功能',
      feat_page_sub: '为真实工作流准备的能力清单。',
      feat_title: '功能一览',
      f4t: '桌面应用', f4d: 'Windows 安装包、麒麟/Linux deb/AppImage，或 start-desktop.bat。',
      f5t: '记忆 · 任务', f5d: 'MEMORY.md 可追加/改写（需确认）；任务面板跟进进度。',
      f6t: '浅色 / 深色', f6d: '浅蓝白主视觉，一键深色；中英双语。',
      how_title: '四步上手',
      s1t: '打开工作区', s1d: '选择本地文件夹，历史与 Memory 落在你控制的磁盘上。',
      s2t: '配置模型', s2d: '主模型 / 子智能体 / 压缩模型可分开设置，自备 API Key 或 Ollama。',
      s3t: '对话与确认', s3d: '写文件、Shell、记忆等变更先征求批准再执行。',
      s4t: '扩展能力', s4d: 'Skills、浏览器沙箱、MCP 按需挂载。',
      shots_title: '界面预览',
      shot1: '浅色', shot2: '深色',
      demo_title: '演示',
      demo_open: '打开演示页',
      page_title_feat: '功能 · EdgeSidekick',
      page_desc_feat: 'EdgeSidekick 功能：多智能体、浏览器沙箱、Skills、桌面端。',
      dl_page_title: '下载',
      dl_page_sub: '获取 Windows 安装包，或从源码运行。',
      dl_win_title: 'Windows 安装包',
      dl_win_desc: '双击安装，无需预装 Python / Node。目标机可离线安装。',
      dl_btn: '下载 Setup.exe',
      dl_zip: '便携版 ZIP',
      dl_releases: 'GitHub Releases',
      dl_note: '安装包来自 GitHub Releases 最新资源（EdgeSidekick-Setup-*-win-x64.exe）。本地构建产物路径：desktop/dist/。若 Releases 尚无资源，请先发布该 Setup 文件，或按下方从源码运行。',
      d2t: '麒麟 / Linux', d2d: 'deb / AppImage / tar.gz，含 arm64。见 packaging/kylin/README.md。',
      d3t: '源码直跑', d3d: 'Python 3.10+ · Node 18+；或 start-desktop.bat。',
      start_title: '从源码启动',
      start_sub: '选择工作区 → 配置模型与 API Key → 开始对话。',
      page_title_dl: '下载 · EdgeSidekick',
      page_desc_dl: '下载 EdgeSidekick Windows 安装包。'
    },
    en: {
      nav_home: 'Home',
      nav_features: 'Features',
      nav_docs: 'Docs',
      nav_download: 'Download',
      nav_cta: 'Get installer',
      badge: 'OPEN SOURCE · LOCAL-FIRST · MULTI-AGENT',
      hero_title_sub: 'Local multi-agent AI workbench',
      hero_lead: 'Run multi-agent collaboration on your machine. Data stays local; bring your own model.',
      hero_cta1: 'Download Windows installer',
      hero_cta2: 'Features',
      home_feat_title: 'Core capabilities',
      home_feat_sub: 'Chat, browser sandbox, Skills, desktop, and MCP — short and practical.',
      f1t: 'Multi-agent chat', f1d: 'Main agent orchestrates sub-agents. SSE streaming; changes need approval.',
      f2t: 'Browser sandbox', f2d: 'Desktop BrowserView / Playwright. Select Mode picks DOM nodes.',
      f3t: 'Skills · MCP', f3d: 'Load skill packs and external tools on demand.',
      more_features: 'All features',
      foot: 'EdgeSidekick · Local multi-agent AI workbench',
      page_title_home: 'EdgeSidekick · Local multi-agent AI workbench',
      page_desc_home: 'EdgeSidekick — local multi-agent AI workbench. Open source, local-first.',
      feat_page_title: 'Features',
      feat_page_sub: 'A short list of what the product actually does.',
      feat_title: 'Feature list',
      f4t: 'Desktop app', f4d: 'Windows installer, Kylin/Linux packages, or start-desktop.bat.',
      f5t: 'Memory · Tasks', f5d: 'Append or rewrite MEMORY.md (with approval). Task panel tracks progress.',
      f6t: 'Light / Dark', f6d: 'Clean light UI with one-click dark mode. Chinese / English.',
      how_title: 'Four steps',
      s1t: 'Open a workspace', s1d: 'Pick a local folder. History and Memory stay on disk you control.',
      s2t: 'Configure models', s2d: 'Separate main / sub / compress models. API key or Ollama.',
      s3t: 'Chat with approval', s3d: 'Writes, shell, and memory changes ask first.',
      s4t: 'Extend', s4d: 'Skills, browser sandbox, and MCP mount on demand.',
      shots_title: 'Screenshots',
      shot1: 'Light', shot2: 'Dark',
      demo_title: 'Demo',
      demo_open: 'Open demo page',
      page_title_feat: 'Features · EdgeSidekick',
      page_desc_feat: 'EdgeSidekick features: multi-agent, browser sandbox, Skills, desktop.',
      dl_page_title: 'Download',
      dl_page_sub: 'Get the Windows installer, or run from source.',
      dl_win_title: 'Windows installer',
      dl_win_desc: 'Double-click install. No Python / Node preinstall. Offline-capable target machines.',
      dl_btn: 'Download Setup.exe',
      dl_zip: 'Portable ZIP',
      dl_releases: 'GitHub Releases',
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
    version: '0.3.1',
    setup: 'https://github.com/GQQI/EdgeSidekick/releases/latest/download/EdgeSidekick-Setup-0.3.1-win-x64.exe',
    zip: 'https://github.com/GQQI/EdgeSidekick/releases/latest/download/EdgeSidekick-0.3.1-win-x64.zip',
    releases: 'https://github.com/GQQI/EdgeSidekick/releases'
  };

  var lang = localStorage.getItem('esk-lang') || 'zh';
  var theme = localStorage.getItem('esk-theme') || 'light';
  var page = document.body.getAttribute('data-page') || 'home';

  function applyTheme(t) {
    theme = t;
    document.documentElement.setAttribute('data-theme', t === 'dark' ? 'dark' : 'light');
    localStorage.setItem('esk-theme', t);
    var btn = document.getElementById('themeToggle');
    if (btn) btn.textContent = t === 'dark' ? '☀' : '☾';
  }

  function guideHref(l) {
    var prefix = page === 'docs' ? '' : 'docs/';
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
    var btn = document.getElementById('langToggle');
    if (btn) btn.textContent = l === 'zh' ? 'EN' : '中文';
    var titleKey = {
      home: 'page_title_home',
      features: 'page_title_feat',
      download: 'page_title_dl'
    }[page];
    var descKey = {
      home: 'page_desc_home',
      features: 'page_desc_feat',
      download: 'page_desc_dl'
    }[page];
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

  var nav = document.getElementById('nav');
  if (nav) {
    addEventListener('scroll', function () {
      nav.classList.toggle('scrolled', scrollY > 8);
    }, { passive: true });
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
        if (setup && setupBtn) {
          setupBtn.href = setup.browser_download_url;
          setupBtn.setAttribute('download', setup.name);
        }
        if (zip && zipBtn) {
          zipBtn.href = zip.browser_download_url;
          zipBtn.setAttribute('download', zip.name);
        }
      })
      .catch(function () { /* keep fallback URLs */ });
  }
  wireDownload();
})();
