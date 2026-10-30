"""CDP discovery and guest-page helpers."""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any
from urllib.parse import urlparse

async def _find_guest_page(browser: Any) -> Any:
    """Locate the in-app BrowserView page; never the workbench renderer."""
    deadline = time.monotonic() + 8.0
    fallback = None
    while time.monotonic() < deadline:
        for ctx in list(getattr(browser, "contexts", None) or []):
            for page in list(getattr(ctx, "pages", None) or []):
                url = ""
                try:
                    url = page.url or ""
                except Exception:
                    continue
                if _is_workbench_url(url):
                    continue
                try:
                    if await page.evaluate("() => Boolean(window.__sidekickGuest)"):
                        return page
                except Exception:
                    pass
                # about:blank / empty guest view is fine; the workbench is not.
                if fallback is None:
                    fallback = page
        await asyncio.sleep(0.15)
    if fallback is not None:
        try:
            url = fallback.url or ""
        except Exception:
            url = ""
        if _is_workbench_url(url):
            return None
    return fallback

def _is_workbench_url(url: str) -> bool:
    u = (url or "").lower()
    prefixes = []
    ui = (os.getenv("SIDEKICK_UI_URL") or "").strip().rstrip("/").lower()
    if ui:
        prefixes.append(ui)
    port = os.getenv("META_PORT", "8787")
    prefixes.extend(
        [
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
            "http://127.0.0.1:5177",
            "http://localhost:5177",
            "http://127.0.0.1:8787",
            "http://localhost:8787",
        ]
    )
    return any(u.startswith(p) for p in prefixes)

def discover_cdp_url() -> str:
    """Loopback CDP endpoint of the desktop BrowserView, if running."""
    global _cdp_cached
    if _cdp_cached:
        return _cdp_cached
    env = (os.getenv("SIDEKICK_CDP_URL") or "").strip()
    if env:
        _cdp_cached = env
        return env
    port = (os.getenv("SIDEKICK_CDP_PORT") or "8315").strip() or "8315"
    url = f"http://127.0.0.1:{port}"
    try:
        req = urllib.request.Request(f"{url}/json/version")
        with urllib.request.urlopen(req, timeout=0.35) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        browser = str(data.get("Browser") or "")
        if "Electron" in browser or "Chrome" in browser:
            _cdp_cached = url
            return url
    except Exception:
        pass
    return ""

