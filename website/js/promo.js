(function () {
  var reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  var i18n = {
  "zh": {
    "hud_left": "EDGESIDEKICK · PROMO · ~32s",
    "back": "← 返回官网",
    "logo_sub": "本地多智能体 AI 工作台",
    "name_title": "开源 · 本地优先 · 多智能体",
    "name_sub": "Open Source · Local-first · Multi-agent",
    "c1t": "多智能体对话",
    "c1d": "主/子智能体协同 · SSE 流式",
    "c2t": "浏览器沙箱",
    "c2d": "Select Mode · browser_* 工具",
    "c3t": "Skills",
    "c3d": "按需加载 · /skill 调用",
    "c4t": "桌面应用",
    "c4d": "Windows · 麒麟 · Linux",
    "c5t": "记忆 · 任务 · MCP",
    "c5d": "MEMORY.md · 工具扩展",
    "c6t": "浅色 / 深色",
    "c6d": "AgentFrame 蓝白风格",
    "shots_cap": "WORKBENCH · SOCIAL · AGENTFRAME",
    "cta_title": "开始使用 EdgeSidekick",
    "cta_btn": "打开官网 · 立即开始",
    "btn_pause": "暂停",
    "btn_play": "播放",
    "btn_restart": "重播"
  },
  "en": {
    "hud_left": "EDGESIDEKICK · PROMO · ~32s",
    "back": "← Back to site",
    "logo_sub": "Local multi-agent AI workbench",
    "name_title": "Open Source · Local-first · Multi-agent",
    "name_sub": "Open Source · Local-first · Multi-agent",
    "c1t": "Multi-agent chat",
    "c1d": "Main + sub agents · SSE stream",
    "c2t": "Browser sandbox",
    "c2d": "Select Mode · browser_* tools",
    "c3t": "Skills",
    "c3d": "On-demand · /skill",
    "c4t": "Desktop app",
    "c4d": "Windows · Kylin · Linux",
    "c5t": "Memory · Tasks · MCP",
    "c5d": "MEMORY.md · tool extensions",
    "c6t": "Light / Dark",
    "c6d": "AgentFrame blue-white",
    "shots_cap": "WORKBENCH · SOCIAL · AGENTFRAME",
    "cta_title": "Get started with EdgeSidekick",
    "cta_btn": "Open site · Get started",
    "btn_pause": "Pause",
    "btn_play": "Play",
    "btn_restart": "Replay"
  }
};

  var lang = localStorage.getItem('esk-lang') || 'zh';
  var scenes = [].slice.call(document.querySelectorAll('.scene'));
  var bar = document.querySelector('.progress > i');
  var label = document.getElementById('sceneLabel');
  var TOTAL_MS = 32000;
  var timeline = [
    { id: 'logo', at: 0, dur: 4500 },
    { id: 'name', at: 4500, dur: 4000 },
    { id: 'features', at: 8500, dur: 9000 },
    { id: 'shots', at: 17500, dur: 8000 },
    { id: 'cta', at: 25500, dur: 6500 }
  ];
  var t0 = null;
  var raf = null;
  var playing = true;
  var current = -1;
  var btnPlay = document.getElementById('btnPlay');
  var btnRestart = document.getElementById('btnRestart');
  var langBtn = document.getElementById('langToggle');

  function applyLang(l) {
    lang = l === 'en' ? 'en' : 'zh';
    localStorage.setItem('esk-lang', lang);
    document.documentElement.lang = lang === 'en' ? 'en' : 'zh-CN';
    var dict = i18n[lang] || i18n.zh;
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      var key = el.getAttribute('data-i18n');
      if (dict[key] != null) el.textContent = dict[key];
    });
    if (langBtn) langBtn.textContent = lang === 'zh' ? 'EN' : '中文';
    var active = document.querySelector('.scene.active');
    if (active && label) {
      label.textContent = active.getAttribute(lang === 'en' ? 'data-label-en' : 'data-label-zh') || '';
    }
    if (btnPlay) btnPlay.textContent = playing ? dict.btn_pause : dict.btn_play;
  }

  function showScene(idx) {
    if (idx === current) return;
    scenes.forEach(function (s, i) {
      s.classList.toggle('active', i === idx);
      s.classList.toggle('exit', i === current && i !== idx);
    });
    current = idx;
    var scene = scenes[idx];
    if (!scene) return;
    if (label) label.textContent = scene.getAttribute(lang === 'en' ? 'data-label-en' : 'data-label-zh') || '';
    if (scene.id === 'features') {
      var cards = scene.querySelectorAll('.card');
      cards.forEach(function (c) { c.classList.remove('show'); });
      cards.forEach(function (c, i) {
        setTimeout(function () { c.classList.add('show'); }, reduced ? 0 : 120 + i * 160);
      });
    }
    if (scene.id === 'shots') {
      var shotsEls = scene.querySelectorAll('.shot-main, .shot-side');
      shotsEls.forEach(function (el) { el.classList.remove('show'); });
      shotsEls.forEach(function (el, i) {
        setTimeout(function () { el.classList.add('show'); }, reduced ? 0 : 200 + i * 280);
      });
    }
  }

  function tick(now) {
    if (!playing) return;
    if (t0 == null) t0 = now;
    var elapsed = (now - t0) % TOTAL_MS;
    if (bar) bar.style.width = ((elapsed / TOTAL_MS) * 100).toFixed(2) + '%';
    var idx = 0;
    for (var i = 0; i < timeline.length; i++) {
      if (elapsed >= timeline[i].at) idx = i;
    }
    showScene(idx);
    raf = requestAnimationFrame(tick);
  }

  function play() {
    playing = true;
    if (!raf) raf = requestAnimationFrame(tick);
    var dict = i18n[lang] || i18n.zh;
    if (btnPlay) btnPlay.textContent = dict.btn_pause;
  }
  function pause() {
    playing = false;
    if (raf) { cancelAnimationFrame(raf); raf = null; }
    var dict = i18n[lang] || i18n.zh;
    if (btnPlay) btnPlay.textContent = dict.btn_play;
  }
  function restart() {
    t0 = null;
    current = -1;
    play();
  }

  applyLang(lang);

  if (reduced) {
    scenes.forEach(function (s) { s.classList.add('active'); });
    document.querySelectorAll('.card, .shot-main, .shot-side').forEach(function (el) { el.classList.add('show'); });
    if (bar) bar.style.width = '100%';
    if (label) label.textContent = 'ALL SCENES (reduced motion)';
  } else {
    play();
  }

  if (btnPlay) btnPlay.addEventListener('click', function () { if (playing) pause(); else play(); });
  if (btnRestart) btnRestart.addEventListener('click', function () { restart(); });
  if (langBtn) langBtn.addEventListener('click', function () { applyLang(lang === 'zh' ? 'en' : 'zh'); });

  // When embedded in the homepage iframe, keep navigation on the parent page.
  if (window.parent && window.parent !== window) {
    document.querySelectorAll('a[href="/EdgeSidekick/"]').forEach(function (a) {
      a.setAttribute('target', '_parent');
    });
  }
})();
