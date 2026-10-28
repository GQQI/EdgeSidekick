"""Installed plugins: packages on disk, per-tenant state, credentials, invocation.

Layout (per tenant)::

    data/tenants/<user>/plugins/<id>/versions/<version>/   one unpacked package per version
    data/tenants/<user>/plugins.json
        {"plugins": {id: {
            enabled, active_version, version, source, origin,
            installed_at, updated_at, credentials,
            versions: {ver: {source, origin, author, package_source, installed_at, updated_at}}
        }}}

Legacy flat layout ``plugins/<id>/manifest.json`` is migrated into
``versions/<version>/`` on first access.

Secret credentials are stored Fernet-encrypted (services/secret_store).
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from ..services.secret_store import decrypt_secret, encrypt_secret
from ..services.tenant_context import get_user_id, tenant_dir
from .manifest import MANIFEST_FILE, Manifest, ManifestError, load_manifest

MAX_PACKAGE_BYTES = 10_000_000
MAX_UNPACKED_BYTES = 40_000_000
RUNNER = Path(__file__).resolve().parent / "runner.py"
_ENV_PASSTHROUGH = (
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "PYTHONPATH",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "all_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
)

_lock = threading.RLock()


class PluginError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class InstalledPlugin:
    manifest: Manifest
    path: Path
    state: dict[str, Any]

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def enabled(self) -> bool:
        return bool(self.state.get("enabled", True))

    @property
    def active_version(self) -> str:
        return str(
            self.state.get("active_version")
            or self.state.get("version")
            or self.manifest.version
            or ""
        )


# ── paths & state ─────────────────────────────────────────────────────────


def packages_dir(user_id: Optional[str] = None) -> Path:
    p = tenant_dir(user_id) / "plugins"
    p.mkdir(parents=True, exist_ok=True)
    return p


def state_path(user_id: Optional[str] = None) -> Path:
    return tenant_dir(user_id) / "plugins.json"


def load_state(user_id: Optional[str] = None) -> dict[str, Any]:
    path = state_path(user_id)
    if not path.is_file():
        return {"plugins": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"plugins": {}}
    if not isinstance(data, dict) or not isinstance(data.get("plugins"), dict):
        return {"plugins": {}}
    return data


def save_state(state: dict[str, Any], user_id: Optional[str] = None) -> None:
    path = state_path(user_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _entry(state: dict[str, Any], plugin_id: str) -> dict[str, Any]:
    return state.setdefault("plugins", {}).setdefault(plugin_id, {})


def version_slug(version: str) -> str:
    """Filesystem-safe directory name for a semver-like string."""
    raw = str(version or "").strip() or "0.0.0"
    cleaned = re.sub(r"[^\w.\-+]", "_", raw)
    return cleaned[:64] or "0.0.0"


def plugin_home(plugin_id: str, user_id: Optional[str] = None) -> Path:
    return packages_dir(user_id) / plugin_id


def version_dir(plugin_id: str, version: str, user_id: Optional[str] = None) -> Path:
    return plugin_home(plugin_id, user_id) / "versions" / version_slug(version)


def _legacy_flat_package(home: Path) -> bool:
    return (home / MANIFEST_FILE).is_file() and not (home / "versions").is_dir()


def _migrate_legacy_flat(plugin_id: str, user_id: Optional[str] = None) -> None:
    """Move ``plugins/<id>/*`` into ``plugins/<id>/versions/<ver>/`` once."""
    home = plugin_home(plugin_id, user_id)
    if not _legacy_flat_package(home):
        return
    try:
        manifest = load_manifest(home)
    except ManifestError:
        return
    ver = manifest.version or "0.0.0"
    dest = version_dir(plugin_id, ver, user_id)
    if dest.exists():
        return
    staging = Path(tempfile.mkdtemp(prefix=f".migrate-{plugin_id}-", dir=packages_dir(user_id)))
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Move everything except versions/ into the version folder.
        dest.mkdir(parents=True, exist_ok=True)
        for child in list(home.iterdir()):
            if child.name in {"versions"} or child.name.startswith("."):
                continue
            os.replace(str(child), str(dest / child.name))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    with _lock:
        state = load_state(user_id)
        entry = _entry(state, plugin_id)
        entry["active_version"] = ver
        entry["version"] = ver
        versions = entry.setdefault("versions", {})
        if not isinstance(versions, dict):
            versions = {}
            entry["versions"] = versions
        versions.setdefault(
            ver,
            {
                "source": entry.get("source") or "migrated",
                "origin": entry.get("origin") or "",
                "author": manifest.author,
                "package_source": manifest.source,
                "installed_at": entry.get("installed_at") or time.time(),
                "updated_at": entry.get("updated_at") or time.time(),
            },
        )
        save_state(state, user_id)


def _resolve_active_path(
    plugin_id: str,
    entry: dict[str, Any],
    user_id: Optional[str] = None,
) -> tuple[Path, str] | None:
    """Return (package_dir, active_version) for the installed plugin."""
    home = plugin_home(plugin_id, user_id)
    if not home.exists():
        return None
    if _legacy_flat_package(home):
        _migrate_legacy_flat(plugin_id, user_id)
    versions_root = home / "versions"
    active = str(entry.get("active_version") or entry.get("version") or "").strip()
    if active:
        path = version_dir(plugin_id, active, user_id)
        if (path / MANIFEST_FILE).is_file():
            return path, active
    if versions_root.is_dir():
        candidates = sorted(
            (p for p in versions_root.iterdir() if p.is_dir() and (p / MANIFEST_FILE).is_file()),
            key=lambda p: p.name,
        )
        if candidates:
            path = candidates[-1]
            return path, path.name
    if (home / MANIFEST_FILE).is_file():
        try:
            ver = load_manifest(home).version
        except ManifestError:
            ver = "0.0.0"
        return home, ver
    return None


def list_version_records(
    plugin_id: str,
    user_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Public version rows for API/UI (active flagged)."""
    state = load_state(user_id)
    entry = state.get("plugins", {}).get(plugin_id)
    if not isinstance(entry, dict):
        return []
    resolved = _resolve_active_path(plugin_id, entry, user_id)
    if not resolved:
        return []
    _path, active = resolved
    versions_meta = entry.get("versions") if isinstance(entry.get("versions"), dict) else {}
    home = plugin_home(plugin_id, user_id)
    versions_root = home / "versions"
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    dirs: list[Path] = []
    if versions_root.is_dir():
        dirs = [p for p in versions_root.iterdir() if p.is_dir() and (p / MANIFEST_FILE).is_file()]
    elif (home / MANIFEST_FILE).is_file():
        dirs = [home]
    for path in sorted(dirs, key=lambda p: p.name):
        try:
            manifest = load_manifest(path)
        except ManifestError:
            continue
        ver = manifest.version
        key = version_slug(ver)
        if key in seen:
            continue
        seen.add(key)
        meta = versions_meta.get(ver) or versions_meta.get(key) or {}
        if not isinstance(meta, dict):
            meta = {}
        rows.append(
            {
                "version": ver,
                "active": version_slug(ver) == version_slug(active) or ver == active,
                "author": manifest.author or str(meta.get("author") or ""),
                "package_source": manifest.source or str(meta.get("package_source") or ""),
                "source": str(meta.get("source") or entry.get("source") or ""),
                "origin": str(meta.get("origin") or entry.get("origin") or ""),
                "installed_at": meta.get("installed_at") or entry.get("installed_at"),
                "updated_at": meta.get("updated_at") or entry.get("updated_at"),
            }
        )
    return rows


def list_installed(user_id: Optional[str] = None) -> list[InstalledPlugin]:
    state = load_state(user_id)
    out: list[InstalledPlugin] = []
    for pid, entry in sorted(state.get("plugins", {}).items()):
        if not isinstance(entry, dict):
            continue
        resolved = _resolve_active_path(pid, entry, user_id)
        if not resolved:
            continue
        path, active = resolved
        try:
            manifest = load_manifest(path)
        except ManifestError:
            continue
        # Keep entry mirrors fresh for callers that read version/source.
        merged = dict(entry)
        merged["active_version"] = active
        merged["version"] = manifest.version
        out.append(InstalledPlugin(manifest, path, merged))
    return out


def get_installed(plugin_id: str, user_id: Optional[str] = None) -> InstalledPlugin:
    for plugin in list_installed(user_id):
        if plugin.id == plugin_id:
            return plugin
    raise PluginError(f"插件 {plugin_id} 未安装", 404)


# ── install / uninstall ──────────────────────────────────────────────────


def _find_package_root(folder: Path) -> Path:
    if (folder / MANIFEST_FILE).is_file():
        return folder
    children = [c for c in folder.iterdir() if c.is_dir() and not c.name.startswith(("__MACOSX", "."))]
    if len(children) == 1 and (children[0] / MANIFEST_FILE).is_file():
        return children[0]
    raise PluginError("压缩包里找不到 manifest.json（应位于根目录或唯一的顶层文件夹内）")


def _safe_extract(raw: bytes, dest: Path) -> None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise PluginError("不是有效的 zip 插件包") from exc
    total = 0
    root = dest.resolve()
    with zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if not name or name.endswith("/"):
                continue
            target = (dest / name).resolve()
            if root not in target.parents:
                raise PluginError(f"插件包包含非法路径：{info.filename}")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise PluginError(f"插件包不能包含符号链接：{info.filename}")
            total += info.file_size
            if total > MAX_UNPACKED_BYTES:
                raise PluginError("插件包解压后过大（上限 40MB）")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def install_from_dir(
    source_dir: Path,
    *,
    source: str,
    user_id: Optional[str] = None,
    origin: str = "",
    expected_id: str = "",
    activate: bool = True,
) -> InstalledPlugin:
    """Install or replace one version of a plugin; other versions stay on disk.

    ``activate`` (default True) switches the active version to the package just
    installed. Pass False to stage a version without switching the running one.
    """
    try:
        manifest = load_manifest(source_dir)
    except ManifestError as exc:
        raise PluginError(f"插件清单无效：{exc}") from exc
    if expected_id and manifest.id != expected_id:
        raise PluginError(f"插件包 id 是 {manifest.id}，与市场条目 {expected_id} 不一致")
    root = packages_dir(user_id)
    home = plugin_home(manifest.id, user_id)
    # Migrate legacy flat package before writing a new version beside it.
    if home.exists() and _legacy_flat_package(home):
        _migrate_legacy_flat(manifest.id, user_id)
    target = version_dir(manifest.id, manifest.version, user_id)
    with _lock:
        staging = Path(tempfile.mkdtemp(prefix=f".{manifest.id}-", dir=root))
        try:
            shutil.copytree(
                source_dir,
                staging / "pkg",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".DS_Store"),
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            backup = None
            if target.exists():
                backup = root / f".{manifest.id}-{version_slug(manifest.version)}.old-{int(time.time() * 1000)}"
                os.replace(target, backup)
            os.replace(staging / "pkg", target)
            if backup is not None:
                shutil.rmtree(backup, ignore_errors=True)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        state = load_state(user_id)
        entry = _entry(state, manifest.id)
        is_upgrade = bool(entry.get("installed_at"))
        now = time.time()
        versions = entry.setdefault("versions", {})
        if not isinstance(versions, dict):
            versions = {}
            entry["versions"] = versions
        prev_meta = versions.get(manifest.version) if isinstance(versions.get(manifest.version), dict) else {}
        versions[manifest.version] = {
            "source": source,
            "origin": origin,
            "author": manifest.author,
            "package_source": manifest.source,
            "installed_at": prev_meta.get("installed_at") or now,
            "updated_at": now,
        }
        entry.update(
            {
                "source": source,
                "origin": origin,
                "installed_at": entry.get("installed_at") or now,
                "updated_at": now,
            }
        )
        if activate or not entry.get("active_version"):
            entry["active_version"] = manifest.version
            entry["version"] = manifest.version
        else:
            entry.setdefault("version", entry.get("active_version") or manifest.version)
        entry.setdefault("enabled", True)
        entry.setdefault("credentials", {})
        # When activating a new version, drop credentials the new manifest
        # no longer declares (same as previous single-version upgrade).
        if activate and is_upgrade:
            names = {c.name for c in manifest.credentials}
            entry["credentials"] = {
                k: v for k, v in (entry.get("credentials") or {}).items() if k in names
            }
        save_state(state, user_id)
        plugin = get_installed(manifest.id, user_id)
    try:
        from .mcp_bridge import sync_plugin_mcp

        sync_plugin_mcp(plugin.id, plugin.manifest, enabled=plugin.enabled, user_id=user_id)
    except Exception:
        pass
    return plugin


def set_active_version(
    plugin_id: str,
    version: str,
    *,
    user_id: Optional[str] = None,
) -> InstalledPlugin:
    """Switch which installed version is active for tool registration."""
    ver = str(version or "").strip()
    if not ver:
        raise PluginError("需要指定版本号")
    path = version_dir(plugin_id, ver, user_id)
    if not (path / MANIFEST_FILE).is_file():
        # Also accept exact folder name match under versions/
        home = plugin_home(plugin_id, user_id) / "versions"
        alt = home / version_slug(ver)
        if (alt / MANIFEST_FILE).is_file():
            path = alt
        else:
            raise PluginError(f"插件 {plugin_id} 没有已安装版本 {ver}", 404)
    try:
        manifest = load_manifest(path)
    except ManifestError as exc:
        raise PluginError(f"插件清单无效：{exc}") from exc
    with _lock:
        state = load_state(user_id)
        if plugin_id not in state.get("plugins", {}):
            raise PluginError(f"插件 {plugin_id} 未安装", 404)
        entry = _entry(state, plugin_id)
        entry["active_version"] = manifest.version
        entry["version"] = manifest.version
        entry["updated_at"] = time.time()
        # Align credentials with the newly active manifest.
        names = {c.name for c in manifest.credentials}
        entry["credentials"] = {
            k: v for k, v in (entry.get("credentials") or {}).items() if k in names
        }
        save_state(state, user_id)
    plugin = get_installed(plugin_id, user_id)
    try:
        from .mcp_bridge import sync_plugin_mcp

        sync_plugin_mcp(plugin.id, plugin.manifest, enabled=plugin.enabled, user_id=user_id)
    except Exception:
        pass
    return plugin


def uninstall_version(
    plugin_id: str,
    version: str,
    *,
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    """Remove one version. If it was the last, uninstall the whole plugin."""
    ver = str(version or "").strip()
    if not ver:
        raise PluginError("需要指定版本号")
    rows = list_version_records(plugin_id, user_id)
    if not rows:
        raise PluginError(f"插件 {plugin_id} 未安装", 404)
    match = next(
        (r for r in rows if r["version"] == ver or version_slug(r["version"]) == version_slug(ver)),
        None,
    )
    if match is None:
        raise PluginError(f"插件 {plugin_id} 没有已安装版本 {ver}", 404)
    if len(rows) <= 1:
        uninstall(plugin_id, user_id=user_id)
        return {"status": "ok", "removed_plugin": True, "version": match["version"]}
    path = version_dir(plugin_id, match["version"], user_id)
    was_active = bool(match.get("active"))
    with _lock:
        shutil.rmtree(path, ignore_errors=True)
        state = load_state(user_id)
        entry = _entry(state, plugin_id)
        versions = entry.get("versions") if isinstance(entry.get("versions"), dict) else {}
        versions.pop(match["version"], None)
        versions.pop(version_slug(match["version"]), None)
        entry["versions"] = versions
        if was_active or entry.get("active_version") == match["version"]:
            home = plugin_home(plugin_id, user_id) / "versions"
            leftovers = sorted(
                (
                    p
                    for p in (home.iterdir() if home.is_dir() else [])
                    if p.is_dir() and (p / MANIFEST_FILE).is_file()
                ),
                key=lambda p: p.name,
            )
            if leftovers:
                try:
                    nxt = load_manifest(leftovers[-1]).version
                except ManifestError:
                    nxt = leftovers[-1].name
                entry["active_version"] = nxt
                entry["version"] = nxt
        entry["updated_at"] = time.time()
        save_state(state, user_id)
    plugin = get_installed(plugin_id, user_id)
    try:
        from .mcp_bridge import sync_plugin_mcp

        sync_plugin_mcp(plugin.id, plugin.manifest, enabled=plugin.enabled, user_id=user_id)
    except Exception:
        pass
    return {
        "status": "ok",
        "removed_plugin": False,
        "version": match["version"],
        "active_version": plugin.active_version,
    }

def install_from_zip(
    raw: bytes,
    *,
    source: str,
    user_id: Optional[str] = None,
    origin: str = "",
    expected_id: str = "",
) -> InstalledPlugin:
    if not raw:
        raise PluginError("插件包为空")
    if len(raw) > MAX_PACKAGE_BYTES:
        raise PluginError("插件包过大（上限 10MB）")
    with tempfile.TemporaryDirectory(prefix="sidekick-plugin-") as tmp:
        dest = Path(tmp)
        _safe_extract(raw, dest)
        return install_from_dir(
            _find_package_root(dest), source=source, user_id=user_id, origin=origin, expected_id=expected_id
        )


def download_package(url: str, *, http: Any = None) -> bytes:
    from ..core.netguard import blocked_http_reason

    reason = blocked_http_reason(url)
    if reason:
        raise PluginError(f"不能从这个地址下载插件：{reason}")
    import httpx

    client = http or httpx.Client(timeout=60, follow_redirects=True)
    try:
        with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                raise PluginError(f"下载插件失败：HTTP {resp.status_code}")
            buf = bytearray()
            for chunk in resp.iter_bytes():
                buf.extend(chunk)
                if len(buf) > MAX_PACKAGE_BYTES:
                    raise PluginError("插件包过大（上限 10MB）")
            return bytes(buf)
    except httpx.HTTPError as exc:
        raise PluginError(f"下载插件失败：{exc}") from exc
    finally:
        if http is None:
            client.close()


def uninstall(plugin_id: str, user_id: Optional[str] = None) -> None:
    try:
        from .mcp_bridge import remove_plugin_mcp

        remove_plugin_mcp(plugin_id, user_id=user_id)
    except Exception:
        pass
    with _lock:
        state = load_state(user_id)
        if plugin_id not in state.get("plugins", {}):
            raise PluginError(f"插件 {plugin_id} 未安装", 404)
        state["plugins"].pop(plugin_id, None)
        save_state(state, user_id)
        shutil.rmtree(plugin_home(plugin_id, user_id), ignore_errors=True)


def set_enabled(plugin_id: str, enabled: bool, user_id: Optional[str] = None) -> InstalledPlugin:
    with _lock:
        get_installed(plugin_id, user_id)
        state = load_state(user_id)
        _entry(state, plugin_id)["enabled"] = bool(enabled)
        save_state(state, user_id)
    plugin = get_installed(plugin_id, user_id)
    try:
        from .mcp_bridge import sync_plugin_mcp

        sync_plugin_mcp(plugin.id, plugin.manifest, enabled=bool(enabled), user_id=user_id)
    except Exception:
        pass
    return plugin


# ── credentials ──────────────────────────────────────────────────────────


def set_credentials(
    plugin_id: str,
    values: dict[str, Any],
    *,
    clear: Optional[list[str]] = None,
    user_id: Optional[str] = None,
) -> InstalledPlugin:
    """Merge credential values. An empty secret keeps the stored one; use ``clear`` to remove."""
    plugin = get_installed(plugin_id, user_id)
    with _lock:
        state = load_state(user_id)
        stored = _entry(state, plugin_id).setdefault("credentials", {})
        for name in clear or []:
            stored.pop(name, None)
        for name, raw in (values or {}).items():
            field = plugin.manifest.credential(name)
            if field is None:
                raise PluginError(f"未知凭据字段：{name}")
            value = str(raw if raw is not None else "").strip()
            if field.type == "secret":
                if value:
                    stored[name] = encrypt_secret(value)
                continue
            if field.type == "select" and value and value not in {o["value"] for o in field.options}:
                raise PluginError(f"「{field.label}」的取值无效")
            if value:
                stored[name] = value
            else:
                stored.pop(name, None)
        save_state(state, user_id)
    return get_installed(plugin_id, user_id)


def credentials_for(plugin: InstalledPlugin) -> dict[str, str]:
    stored = plugin.state.get("credentials") or {}
    out: dict[str, str] = {}
    for field in plugin.manifest.credentials:
        raw = str(stored.get(field.name) or "")
        value = decrypt_secret(raw) if field.type == "secret" else raw
        out[field.name] = value or field.default
    return out


def credential_status(plugin: InstalledPlugin) -> dict[str, Any]:
    stored = plugin.state.get("credentials") or {}
    fields: dict[str, Any] = {}
    missing: list[str] = []
    for field in plugin.manifest.credentials:
        raw = str(stored.get(field.name) or "")
        has = bool(raw) or bool(field.default)
        info: dict[str, Any] = {"set": bool(raw)}
        if field.type != "secret":
            info["value"] = raw
        fields[field.name] = info
        if field.required and not has:
            missing.append(field.name)
    return {"configured": not missing, "missing": missing, "fields": fields}


# ── invocation ───────────────────────────────────────────────────────────


def _child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _ENV_PASSTHROUGH}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_subprocess(request: dict[str, Any], timeout: float) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            [sys.executable, str(RUNNER)],
            input=json.dumps(request, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout + 5,
            cwd=str(request.get("dir") or "."),
            env=_child_env(),
            **kwargs,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"插件调用超时（{int(timeout)} 秒）"}
    except OSError as exc:
        return {"ok": False, "error": f"无法启动插件进程：{exc}"}
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            break
        if isinstance(data, dict) and "ok" in data:
            return data
        break
    tail = (proc.stderr or "").strip().splitlines()[-3:]
    return {"ok": False, "error": "插件进程异常退出" + (f"：{' | '.join(tail)}" if tail else "")}


# Replaced in tests to run plugin code in-process with a mock HTTP transport.
executor: Callable[[dict[str, Any], float], dict[str, Any]] = run_subprocess


def _request(plugin: InstalledPlugin, **extra: Any) -> dict[str, Any]:
    return {
        "dir": str(plugin.path),
        "entry": plugin.manifest.entry,
        "id": plugin.id,
        "credentials": credentials_for(plugin),
        "timeout": plugin.manifest.timeout,
        **extra,
    }


def invoke_tool(plugin_id: str, tool: str, params: dict[str, Any], *, user_id: Optional[str] = None) -> dict[str, Any]:
    plugin = get_installed(plugin_id, user_id)
    if not any(t.name == tool for t in plugin.manifest.tools):
        return {"ok": False, "error": f"插件「{plugin.manifest.name}」没有工具 {tool}"}
    status = credential_status(plugin)
    if not status["configured"]:
        labels = [plugin.manifest.credential(n).label for n in status["missing"]]  # type: ignore[union-attr]
        return {"ok": False, "error": f"插件「{plugin.manifest.name}」缺少凭据：{'、'.join(labels)}。请在「设置 → 插件」里配置。"}
    return executor(_request(plugin, action="invoke", tool=tool, params=params or {}), plugin.manifest.timeout)


def verify_credentials(plugin_id: str, *, user_id: Optional[str] = None) -> dict[str, Any]:
    plugin = get_installed(plugin_id, user_id)
    status = credential_status(plugin)
    if not status["configured"]:
        return {"ok": False, "error": "还有必填凭据没有填写"}
    return executor(_request(plugin, action="validate"), plugin.manifest.timeout)


def current_user() -> str:
    return get_user_id()
