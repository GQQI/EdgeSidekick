"""Human-challenge / captcha detection helpers."""
from __future__ import annotations

_CHALLENGE_CUES = (
    "验证码",
    "安全验证",
    "人机验证",
    "请完成验证",
    "滑动验证",
    "点击验证",
    "身份验证",
    "unusual traffic",
    "verify you are human",
    "are you a robot",
    "security check",
    "captcha",
    "recaptcha",
    "hcaptcha",
    "cf-challenge",
    "just a moment",
    "attention required",
    "access denied",
    "机器人检测",
)

_CHALLENGE_JS = r"""(() => {
  const cues = __CUES__;
  const url = String(location.href || "");
  const title = String(document.title || "");
  const body = (document.body && (document.body.innerText || document.body.textContent || "")) || "";
  const sample = (url + "\n" + title + "\n" + body.slice(0, 5000)).toLowerCase();
  const hit = [];
  for (const c of cues) {
    if (c && sample.indexOf(String(c).toLowerCase()) >= 0) hit.push(c);
  }
  let iframeHit = false;
  try {
    const iframes = Array.from(document.querySelectorAll("iframe[src]"));
    for (const f of iframes) {
      const src = String(f.getAttribute("src") || "").toLowerCase();
      if (!src) continue;
      if (src.indexOf("captcha") >= 0 || src.indexOf("recaptcha") >= 0 ||
          src.indexOf("hcaptcha") >= 0 || src.indexOf("geetest") >= 0 ||
          src.indexOf("challenge") >= 0) {
        iframeHit = true;
        break;
      }
    }
  } catch (e) {}
  let widget = false;
  try {
    widget = !!(document.querySelector(
      "[class*='captcha' i], [id*='captcha' i], .g-recaptcha, .h-captcha, #captcha, #verify"
    ));
  } catch (e) {}
  const challenged = hit.length > 0 || iframeHit || widget;
  return {
    challenged,
    reasons: hit.slice(0, 6),
    iframe: iframeHit,
    widget,
    url,
    title: title.slice(0, 120),
  };
})()"""

def challenge_js_source() -> str:
    import json as _json
    return _CHALLENGE_JS.replace("__CUES__", _json.dumps(list(_CHALLENGE_CUES), ensure_ascii=False))

