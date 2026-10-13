(function () {
  var reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  var i18n = {
    zh: {
      nav_features: '功能特性',
      nav_how: '如何工作',
      nav_shots: '界面预览',
      nav_demo: '演示',
      nav_download: '下载',
      nav_guide: '使用手册',
      nav_cta: '立即开始',
      badge: '开源 · 本地优先 · 多智能体',
      hero_title_sub: '本地多智能体 AI 工作台',
      hero_cta1: '快速开始',
      hero_cta2: '查看功能',
      hero_cta3: '产品演示',
      typed: [
        '打开文件夹，对话即协作。',
        '主智能体 + 子智能体协同，人类确认。',
        '数据不出境，Key 留在本地。'
      ],
      mq: 'MULTI-AGENT · BROWSER SANDBOX · SKILLS · DESKTOP · MEMORY · MCP · LIGHT / DARK',
      stat1: '本地运行',
      stat2: '中英界面',
      stat3: '浅/深主题',
      stat4: '跨平台',
      feat_eyebrow: 'Features',
      feat_title: '为真实工作流设计',
      feat_sub: '对话、浏览器沙箱、Skills、桌面端、记忆与任务、浅色/深色——一块蓝白 AgentFrame 风格工作台全部齐备。',
      f1t: '多智能体对话', f1d: '主智能体调度子智能体，SSE 流式输出，附件、停止、编辑重发与文件回滚一步到位。',
      f2t: '浏览器沙箱', f2d: '桌面 Electron BrowserView / Web Playwright；Select Mode 点选 DOM，Agent 用 browser_* 工具操作页面。',
      f3t: 'Skills 技能包', f3d: '拖入 src/skills/，用 /skill 按需调用——工具按需加载，不把巨型 prompt 塞进每一轮。',
      f4t: '桌面应用', f4d: 'Windows 安装包、麒麟/Linux deb/AppImage，或 start-desktop.bat 一键拉起 Electron 工作台。',
      f5t: '记忆 · 任务 · MCP', f5d: 'MEMORY.md 可追加/改写（需确认）；任务面板跟进进度；MCP 扩展把外部工具接到同一运行时。',
      f6t: '浅色 / 深色', f6d: 'AgentFrame 蓝白浅色主视觉，一键切换深色；中英双语，适合日夜长时间工作。',
      how_eyebrow: 'How it works',
      how_title: '四步上手',
      how_sub: '从打开文件夹到多智能体协作，不到一分钟。',
      s1t: '打开工作区', s1d: '选择本地文件夹，历史与 Memory 都落在你控制的磁盘上。',
      s2t: '配置模型', s2d: '主模型 / 子智能体 / 压缩模型可分开设置，自备 API Key 或接 Ollama。',
      s3t: '对话与确认', s3d: '写文件、Shell、记忆等变更动作先征求人类批准，再执行。',
      s4t: '扩展能力', s4d: 'Skills、浏览器沙箱、MCP 工具按需挂载，fork 友好分层架构。',
      shots_eyebrow: 'Interface',
      shots_title: '浅色清爽 · 深色沉浸',
      shots_sub: '同一工作台，两种氛围。浅色清爽适合日间专注；深色沉浸适合夜间长时工作。',
      shot1: '浅色清爽', shot2: '深色沉浸',
      demo_eyebrow: 'Demo',
      demo_title: '产品演示',
      demo_sub: '一分钟看清核心能力：工作台、会话版、多智能体对话、浏览器沙箱、Skills 与本地桌面。',
      demo_bar: '介绍片 · ~46s',
      demo_open: '全屏动画版',
      
      dl_eyebrow: 'Download',
      dl_title: '三种方式，立刻可用',
      d1t: 'Windows 安装包', d1d: '构建机一次打好，目标机双击安装，无需预装运行时。',
      d2t: '麒麟 / Linux', d2d: '国产系统与通用 Linux 包，含 arm64 支持，dpkg 一行安装。',
      d3t: '源码直跑', d3d: 'Python 3.10+ · Node 18+；或双击 start-desktop.bat 一键启动。',
      start_title: '60 秒快速上手',
      start_sub: '选择工作区文件夹 → 配置模型与 API Key → 开始对话。',
      cta_title: '把 EdgeSidekick 拉进你的工作流',
      cta_sub: '开源 · 本地优先 · 数据不出境 —— 现在就把 EdgeSidekick 拉进你的工作流。',
      cta_btn: '立即开始',
      foot: 'EdgeSidekick · 本地多智能体 AI 工作台',
      page_title: 'EdgeSidekick · 本地多智能体 AI 工作台',
      page_desc: 'EdgeSidekick — 本地多智能体 AI 工作台。开源、本地优先、AgentFrame 蓝白风格。'
    },
    en: {
      nav_features: 'Features',
      nav_how: 'How it works',
      nav_shots: 'Screenshots',
      nav_demo: 'Demo',
      nav_download: 'Download',
      nav_guide: 'User guide',
      nav_cta: 'Get started',
      badge: 'OPEN SOURCE · LOCAL-FIRST · MULTI-AGENT',
      hero_title_sub: 'Local multi-agent AI workbench',
      hero_cta1: 'Quick start',
      hero_cta2: 'Features',
      hero_cta3: 'Product demo',
      typed: [
        'Open a folder — chat becomes collaboration.',
        'Main + sub-agents with human approval.',
        'Your data stays local. Your keys stay yours.'
      ],
      mq: 'MULTI-AGENT · BROWSER SANDBOX · SKILLS · DESKTOP · MEMORY · MCP · LIGHT / DARK',
      stat1: 'Local-first',
      stat2: 'ZH / EN UI',
      stat3: 'Light / Dark',
      stat4: 'Cross-platform',
      feat_eyebrow: 'Features',
      feat_title: 'Built for real workflows',
      feat_sub: 'Multi-agent chat, browser sandbox, Skills, desktop app, memory/tasks/MCP, light/dark — one AgentFrame blue-white workbench.',
      f1t: 'Multi-agent chat', f1d: 'Main agent orchestrates sub-agents. SSE streaming, attachments, stop, edit & resend with optional file restore.',
      f2t: 'Browser sandbox', f2d: 'Desktop Electron BrowserView / Web Playwright. Select Mode picks DOM; agent drives pages via browser_* tools.',
      f3t: 'Skills packs', f3d: 'Drop packs under src/skills/, invoke with /skill — tools load on demand instead of bloating every prompt.',
      f4t: 'Desktop app', f4d: 'Windows installer, Kylin/Linux deb/AppImage, or start-desktop.bat to launch the Electron workbench.',
      f5t: 'Memory · Tasks · MCP', f5d: 'Append or rewrite MEMORY.md (with approval). Task panel tracks progress. MCP hooks external tools into the same runtime.',
      f6t: 'Light / Dark', f6d: 'AgentFrame blue-white light primary with one-click dark mode. Chinese / English for all-day work.',
      how_eyebrow: 'How it works',
      how_title: 'Four steps',
      how_sub: 'From open folder to multi-agent collaboration in under a minute.',
      s1t: 'Open a workspace', s1d: 'Pick a local folder. History and Memory stay on disk you control.',
      s2t: 'Configure models', s2d: 'Separate main / sub / compress models. Bring your API key or plug in Ollama.',
      s3t: 'Chat with approval', s3d: 'Writes, shell, and memory changes ask for human confirmation first.',
      s4t: 'Extend', s4d: 'Skills, browser sandbox, and MCP tools mount on demand — fork-friendly layers.',
      shots_eyebrow: 'Interface',
      shots_title: 'Light clean · Dark immersive',
      shots_sub: 'One workbench, two moods. Light clean for daytime focus; dark immersive for long night sessions.',
      shot1: 'Light clean', shot2: 'Dark immersive',
      demo_eyebrow: 'Demo',
      demo_title: 'Product demo',
      demo_sub: 'See the core capabilities in about a minute: workbench, social sessions, multi-agent chat, browser sandbox, Skills, and local desktop.',
      demo_bar: 'Intro · ~46s',
      demo_open: 'Fullscreen animation',
      
      dl_eyebrow: 'Download',
      dl_title: 'Three ways to run',
      d1t: 'Windows installer', d1d: 'Build once, install on target machines — no runtime preinstall.',
      d2t: 'Kylin / Linux', d2d: 'Native packages with arm64 support. One dpkg command.',
      d3t: 'From source', d3d: 'Python 3.10+ · Node 18+, or start-desktop.bat for Electron.',
      start_title: 'Ready in 60 seconds',
      start_sub: 'Pick a workspace → set model & API key → start chatting.',
      cta_title: 'Pull EdgeSidekick into your workflow',
      cta_sub: 'Open source · local-first · your data stays yours. Pull EdgeSidekick into your workflow now.',
      cta_btn: 'Get started',
      foot: 'EdgeSidekick · Local multi-agent AI workbench',
      page_title: 'EdgeSidekick · Local multi-agent AI workbench',
      page_desc: 'EdgeSidekick — local multi-agent AI workbench. Open source, local-first, AgentFrame blue-white style.'
    }
  };

  var lang = localStorage.getItem('esk-lang') || 'zh';
  var theme = localStorage.getItem('esk-theme') || 'light';

  function applyTheme(t) {
    theme = t;
    document.documentElement.setAttribute('data-theme', t === 'dark' ? 'dark' : 'light');
    localStorage.setItem('esk-theme', t);
    var btn = document.getElementById('themeToggle');
    if (btn) btn.textContent = t === 'dark' ? '☀' : '☾';
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
    document.querySelectorAll('a[href*="user-guide"]').forEach(function (a) {
      if (a.getAttribute('href').indexOf('user-guide.en.html') >= 0 && l === 'zh') return;
      if (a.getAttribute('data-i18n') === 'nav_guide' || a.getAttribute('href').indexOf('docs/user-guide') === 0) {
        a.setAttribute('href', l === 'en' ? 'docs/user-guide.en.html' : 'docs/user-guide.zh.html');
      }
    });
    var btn = document.getElementById('langToggle');
    if (btn) btn.textContent = l === 'zh' ? 'EN' : '中文';
    if (dict.page_title) document.title = dict.page_title;
    var meta = document.querySelector('meta[name="description"]');
    if (meta && dict.page_desc) meta.setAttribute('content', dict.page_desc);
    startTyping(dict.typed || i18n.zh.typed);
    var mq = document.getElementById('mq');
    if (mq) {
      var item = '<span><b>EdgeSidekick</b> · ' + (dict.mq || i18n.zh.mq) + '</span>';
      mq.innerHTML = item + item;
    }
  }

  var typedTimer = null;
  function startTyping(lines) {
    var typed = document.getElementById('typed');
    if (!typed) return;
    if (typedTimer) clearTimeout(typedTimer);
    if (reduced) { typed.textContent = lines[0]; return; }
    var li = 0, ci = 0, del = false;
    (function tick() {
      var line = lines[li];
      ci += del ? -1 : 1;
      typed.textContent = line.slice(0, Math.max(0, ci));
      var wait = del ? 34 : 92;
      if (!del && ci === line.length) { wait = 1800; del = true; }
      else if (del && ci === 0) { del = false; li = (li + 1) % lines.length; wait = 420; }
      typedTimer = setTimeout(tick, wait);
    })();
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

  var progress = document.getElementById('progress');
  var nav = document.getElementById('nav');
  function onScroll() {
    var max = document.documentElement.scrollHeight - innerHeight;
    if (progress) progress.style.transform = 'scaleX(' + (max > 0 ? scrollY / max : 0) + ')';
    if (nav) nav.classList.toggle('scrolled', scrollY > 10);
  }
  addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  var reveals = [].slice.call(document.querySelectorAll('.reveal'));
  if (reduced || !('IntersectionObserver' in window)) {
    reveals.forEach(function (el) { el.classList.add('in'); });
  } else {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); }
      });
    }, { threshold: 0.15, rootMargin: '0px 0px -40px 0px' });
    reveals.forEach(function (el) { io.observe(el); });
  }

  function countUp(el) {
    var target = +el.dataset.count, suf = el.dataset.suffix || '', t0 = null;
    if (reduced) { el.textContent = target.toLocaleString() + suf; return; }
    function step(t) {
      if (!t0) t0 = t;
      var p = Math.min((t - t0) / 1400, 1), e = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(target * e).toLocaleString() + suf;
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }
  var nums = [].slice.call(document.querySelectorAll('[data-count]'));
  if ('IntersectionObserver' in window && !reduced) {
    var nio = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { countUp(e.target); nio.unobserve(e.target); } });
    }, { threshold: 0.6 });
    nums.forEach(function (el) { nio.observe(el); });
  } else nums.forEach(countUp);
})();
