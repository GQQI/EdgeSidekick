(function () {
  var i18n = {
    zh: {
      badge: '开源 · 本地优先',
      hero_title_sub: '本地 AI 工作台',
      hero_lead: '面向项目任务的本地 AI 工作台，整合模型、浏览器与可扩展工具。',
      hero_cta1: '下载 Windows 安装包',
      arch_eyebrow: 'HOW IT WORKS',
      arch_title: '任务执行流程',
      features_eyebrow: 'FEATURES',
      home_feat_title: '功能概览',
      f_workspace_t: '项目工作区', f_workspace_d: '项目文件、对话与任务集中管理。',
      f_agent_t: '智能体协作', f_agent_d: '拆分子任务，并行执行并汇总结果。',
      f_models_t: '模型配置', f_models_d: '支持 OpenAI 兼容接口、DeepSeek、Ollama 等。',
      f_browser_t: '浏览器操作', f_browser_d: '内置网页浏览与页面元素交互能力。',
      f_extend_t: '工具扩展', f_extend_d: '通过 Skills、MCP 和插件接入额外工具。',
      f_desktop_t: '桌面应用', f_desktop_d: '提供 Windows、麒麟与 Linux 版本。',
      dl_page_title: '下载',
      dl_win_title: 'Windows 安装包',
      dl_win_desc: '适用于 Windows x64 的桌面安装包。',
      dl_btn: '下载安装包', dl_zip: '便携版 ZIP', dl_releases: 'GitHub Releases',
      d2t: '麒麟 / Linux', d2d: '提供 deb、AppImage 与 tar.gz 安装包。',
      d3t: '源码运行', d3d: '需要 Python 3.10+ 与 Node 18+。',
      start_title: '命令行启动',
      demo_eyebrow: 'PREVIEW',
      demo_title: '界面演示',
      foot: 'EdgeSidekick',
      nav_docs: '手册',
      page_title_home: 'EdgeSidekick · 本地 AI 工作台',
      page_desc_home: 'EdgeSidekick — 开源、本地优先的 AI 工作台。'
    },
    en: {
      badge: 'OPEN SOURCE · LOCAL-FIRST',
      hero_title_sub: 'Local AI workbench',
      hero_lead: 'A local AI workbench for project tasks, models, browser actions, and extensible tools.',
      hero_cta1: 'Download Windows installer',
      arch_eyebrow: 'HOW IT WORKS',
      arch_title: 'Task workflow',
      features_eyebrow: 'FEATURES',
      home_feat_title: 'Features',
      f_workspace_t: 'Project workspace', f_workspace_d: 'Manage project files, chats, and tasks together.',
      f_agent_t: 'Agent collaboration', f_agent_d: 'Delegate subtasks, run them in parallel, and combine results.',
      f_models_t: 'Model setup', f_models_d: 'Supports OpenAI-compatible APIs, DeepSeek, Ollama, and more.',
      f_browser_t: 'Browser actions', f_browser_d: 'Browse websites and interact with page elements.',
      f_extend_t: 'Tool extensions', f_extend_d: 'Add tools through Skills, MCP, and plugins.',
      f_desktop_t: 'Desktop app', f_desktop_d: 'Available for Windows, Kylin, and Linux.',
      dl_page_title: 'Download',
      dl_win_title: 'Windows installer',
      dl_win_desc: 'Desktop installer for Windows x64.',
      dl_btn: 'Download installer', dl_zip: 'Portable ZIP', dl_releases: 'GitHub Releases',
      d2t: 'Kylin / Linux', d2d: 'Available as deb, AppImage, and tar.gz.',
      d3t: 'Run from source', d3d: 'Requires Python 3.10+ and Node 18+.',
      start_title: 'Command line',
      demo_eyebrow: 'PREVIEW',
      demo_title: 'Interface preview',
      foot: 'EdgeSidekick',
      nav_docs: 'Docs',
      page_title_home: 'EdgeSidekick · Local AI workbench',
      page_desc_home: 'EdgeSidekick — open-source, local-first AI workbench.'
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
  var architectureRequestId = 0;

  function applyTheme(t) {
    theme = t;
    document.documentElement.setAttribute('data-theme', t === 'dark' ? 'dark' : 'light');
    localStorage.setItem('esk-theme', t);
    var icon = document.getElementById('themeIcon');
    if (icon) icon.innerHTML = t === 'dark' ? sun : moon;
  }

  function guideHref(l) {
    return '/EdgeSidekick/docs/' + (l === 'en' ? 'user-guide.en.html' : 'user-guide.zh.html');
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
    if (dict.page_title_home) document.title = dict.page_title_home;
    var meta = document.querySelector('meta[name="description"]');
    if (meta && dict.page_desc_home) meta.setAttribute('content', dict.page_desc_home);
    embedArchitecture(l === 'en' ? 'en' : 'zh');
  }

  applyTheme(theme);
  applyLang(lang);

  var langBtn = document.getElementById('langToggle');
  if (langBtn) langBtn.addEventListener('click', function () { applyLang(lang === 'zh' ? 'en' : 'zh'); });
  var themeBtn = document.getElementById('themeToggle');
  if (themeBtn) themeBtn.addEventListener('click', function () { applyTheme(theme === 'dark' ? 'light' : 'dark'); });

  /* Scroll reveal */
  function initReveal() {
    var nodes = document.querySelectorAll('.reveal');
    if (!nodes.length) return;
    if (!('IntersectionObserver' in window)) {
      nodes.forEach(function (n) { n.classList.add('in'); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          e.target.classList.add('in');
        } else {
          var rect = e.boundingClientRect;
          var aboveViewport = rect.bottom < window.innerHeight * 0.06;
          var belowViewport = rect.top > window.innerHeight * 0.92;
          if (aboveViewport || belowViewport) e.target.classList.remove('in');
        }
      });
    }, { rootMargin: '-6% 0px -8% 0px', threshold: 0.08 });
    nodes.forEach(function (n) { io.observe(n); });
  }
  initReveal();

  /* Scroll progress line under the sticky navigation. */
  function initScrollProgress() {
    var scheduled = false;
    function update() {
      if (scheduled) return;
      scheduled = true;
      requestAnimationFrame(function () {
        scheduled = false;
        var range = document.documentElement.scrollHeight - window.innerHeight;
        var progress = range > 0 ? window.scrollY / range : 0;
        document.documentElement.style.setProperty('--scroll-progress', String(Math.max(0, Math.min(1, progress))));
      });
    }
    window.addEventListener('scroll', update, { passive: true });
    window.addEventListener('resize', update);
    update();
  }
  initScrollProgress();

  /* Inline SVG: fetch and embed so there is never an <a href=svg> download */
  function embedArchitecture(language) {
    var host = document.getElementById('archInline');
    if (!host) return;
    var english = language === 'en';
    var asset = english ? '/EdgeSidekick/assets/architecture.en.svg' : '/EdgeSidekick/assets/architecture.svg';
    var label = english ? 'EdgeSidekick task workflow' : 'EdgeSidekick 任务执行流程';
    var requestId = ++architectureRequestId;
    host.setAttribute('aria-label', label);
    fetch(asset)
      .then(function (r) { return r.ok ? r.text() : Promise.reject(); })
      .then(function (svgText) {
        if (requestId !== architectureRequestId) return;
        host.innerHTML = svgText;
        var svg = host.querySelector('svg');
        if (svg) {
          svg.removeAttribute('width');
          svg.removeAttribute('height');
          svg.setAttribute('class', 'arch-inline');
          svg.style.width = '100%';
          svg.style.height = 'auto';
          svg.setAttribute('aria-hidden', 'false');
          svg.setAttribute('aria-label', label);
        }
      })
      .catch(function () {
        if (requestId !== architectureRequestId) return;
        /* fallback: object without download link behavior */
        host.innerHTML = '<object type="image/svg+xml" data="' + asset + '" class="arch-inline" aria-label="' + label + '"></object>';
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
