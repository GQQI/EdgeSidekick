"""Plugin marketplace: bundled packages plus optional remote index sources.

A remote source is any http(s) URL serving::

    {"name": "My team plugins",
     "plugins": [{"id": "jira", "name": "Jira", "version": "1.2.0", "description": "...",
                  "author": "...", "category": "...", "tags": [...], "icon_url": "...",
                  "homepage": "...", "download_url": "https://.../jira-1.2.0.zip"}]}

Sources come from ``SIDEKICK_PLUGIN_MARKETPLACE_URLS`` (comma-separated) and
from the list the user adds in Settings.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .manifest import ID_RE, ManifestError, icon_data_uri, load_manifest, readme_text
from .store import (
    PluginError,
    download_package,
    install_from_dir,
    install_from_zip,
    list_installed,
    load_state,
    save_state,
)

BUNDLED_DIR = Path(__file__).resolve().parent / "marketplace"
REMOTE_TTL = 600.0
_remote_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def _version_key(v: str) -> tuple:
    parts: list[Any] = []
    for chunk in str(v or "0").replace("-", ".").split("."):
        parts.append((0, int(chunk)) if chunk.isdigit() else (1, chunk))
    return tuple(parts)


def is_newer(candidate: str, installed: str) -> bool:
    return _version_key(candidate) > _version_key(installed)


def bundled_packages() -> list[tuple[Any, Path]]:
    out = []
    if not BUNDLED_DIR.is_dir():
        return out
    for child in sorted(BUNDLED_DIR.iterdir()):
        if not child.is_dir():
            continue
        try:
            out.append((load_manifest(child), child))
        except ManifestError:
            continue
    return out


def sources(user_id: Optional[str] = None) -> list[str]:
    env = [u.strip() for u in os.getenv("SIDEKICK_PLUGIN_MARKETPLACE_URLS", "").split(",") if u.strip()]
    saved = [str(u).strip() for u in load_state(user_id).get("sources", []) if str(u).strip()]
    seen: list[str] = []
    for url in env + saved:
        if url not in seen:
            seen.append(url)
    return seen


def set_sources(urls: list[str], user_id: Optional[str] = None) -> list[str]:
    from ..core.netguard import blocked_http_reason

    clean: list[str] = []
    for url in urls:
        url = str(url or "").strip()
        if not url:
            continue
        reason = blocked_http_reason(url)
        if reason:
            raise PluginError(f"市场地址不可用：{url}（{reason}）")
        if url not in clean:
            clean.append(url)
    state = load_state(user_id)
    state["sources"] = clean[:10]
    save_state(state, user_id)
    return sources(user_id)


def fetch_remote_index(url: str, *, http: Any = None, refresh: bool = False) -> dict[str, Any]:
    now = time.time()
    with _cache_lock:
        hit = _remote_cache.get(url)
        if hit and not refresh and now - hit[0] < REMOTE_TTL:
            return hit[1]
    from ..core.netguard import blocked_http_reason

    reason = blocked_http_reason(url)
    if reason:
        raise PluginError(f"市场地址不可用：{reason}")
    import httpx

    client = http or httpx.Client(timeout=20, follow_redirects=True)
    try:
        resp = client.get(url)
        if resp.status_code >= 400:
            raise PluginError(f"市场索引请求失败：HTTP {resp.status_code}")
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PluginError(f"市场索引读取失败：{exc}") from exc
    finally:
        if http is None:
            client.close()
    if not isinstance(data, dict) or not isinstance(data.get("plugins"), list):
        raise PluginError("市场索引格式不对：缺少 plugins 列表")
    with _cache_lock:
        _remote_cache[url] = (now, data)
    return data


def _remote_items(user_id: Optional[str], *, http: Any = None, refresh: bool = False) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for url in sources(user_id):
        try:
            index = fetch_remote_index(url, http=http, refresh=refresh)
        except PluginError as exc:
            errors.append({"source": url, "error": str(exc)})
            continue
        for raw in index.get("plugins", []):
            if not isinstance(raw, dict):
                continue
            pid = str(raw.get("id") or "").strip()
            if not ID_RE.match(pid) or not str(raw.get("download_url") or "").strip():
                continue
            items.append(
                {
                    "id": pid,
                    "name": str(raw.get("name") or pid)[:80],
                    "version": str(raw.get("version") or "0.0.0")[:40],
                    "description": str(raw.get("description") or "")[:600],
                    "author": str(raw.get("author") or "")[:80],
                    "category": str(raw.get("category") or "其他")[:40],
                    "tags": [str(t)[:32] for t in (raw.get("tags") or [])][:12],
                    "homepage": str(raw.get("homepage") or "")[:400],
                    "icon": str(raw.get("icon_url") or "")[:400],
                    "download_url": str(raw.get("download_url")),
                    "tools": [str(t.get("name") if isinstance(t, dict) else t)[:64] for t in (raw.get("tools") or [])][:40],
                    "credentials": [],
                    "readme": "",
                    "source": "remote",
                    "source_url": url,
                    "source_name": str(index.get("name") or url)[:80],
                }
            )
    return items, errors


def catalog(user_id: Optional[str] = None, *, http: Any = None, refresh: bool = False) -> dict[str, Any]:
    installed = {p.id: p for p in list_installed(user_id)}
    items: list[dict[str, Any]] = []
    for manifest, path in bundled_packages():
        items.append(
            {
                **manifest.public(),
                "icon": icon_data_uri(path, manifest),
                "tools": [t.label for t in manifest.tools],
                "tool_specs": [t.public() for t in manifest.tools],
                "readme": readme_text(path),
                "source": "bundled",
                "source_name": "内置市场",
            }
        )
    remote, errors = _remote_items(user_id, http=http, refresh=refresh)
    known = {i["id"] for i in items}
    for item in remote:
        if item["id"] in known:
            continue
        known.add(item["id"])
        items.append(item)
    for item in items:
        plugin = installed.get(item["id"])
        item["installed"] = plugin is not None
        item["installed_version"] = plugin.manifest.version if plugin else ""
        item["update_available"] = bool(plugin and is_newer(item["version"], plugin.manifest.version))
    categories = sorted({i["category"] for i in items})
    return {"items": items, "categories": categories, "sources": sources(user_id), "errors": errors}


def install_from_marketplace(plugin_id: str, user_id: Optional[str] = None, *, http: Any = None):
    for manifest, path in bundled_packages():
        if manifest.id == plugin_id:
            return install_from_dir(path, source="marketplace", origin="bundled", user_id=user_id)
    remote, _errors = _remote_items(user_id, http=http)
    for item in remote:
        if item["id"] == plugin_id:
            raw = download_package(item["download_url"], http=http)
            return install_from_zip(
                raw, source="marketplace", origin=item["download_url"], user_id=user_id, expected_id=plugin_id
            )
    raise PluginError(f"市场里没有插件 {plugin_id}", 404)
