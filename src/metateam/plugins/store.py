"""Installed plugins: packages on disk, per-tenant state, credentials, invocation.

Layout (per tenant)::

    data/tenants/<user>/plugins/<id>/     unpacked package
    data/tenants/<user>/plugins.json      {"plugins": {id: {enabled, version, source, installed_at, credentials}}}

Secret credentials are stored Fernet-encrypted (services/secret_store).
"""

from __future__ import annotations

import io
import json
import os
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


def list_installed(user_id: Optional[str] = None) -> list[InstalledPlugin]:
    state = load_state(user_id)
    root = packages_dir(user_id)
    out: list[InstalledPlugin] = []
    for pid, entry in sorted(state.get("plugins", {}).items()):
        path = root / pid
        try:
            manifest = load_manifest(path)
        except ManifestError:
            continue
        out.append(InstalledPlugin(manifest, path, entry if isinstance(entry, dict) else {}))
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
) -> InstalledPlugin:
    try:
        manifest = load_manifest(source_dir)
    except ManifestError as exc:
        raise PluginError(f"插件清单无效：{exc}") from exc
    if expected_id and manifest.id != expected_id:
        raise PluginError(f"插件包 id 是 {manifest.id}，与市场条目 {expected_id} 不一致")
    root = packages_dir(user_id)
    target = root / manifest.id
    with _lock:
        staging = Path(tempfile.mkdtemp(prefix=f".{manifest.id}-", dir=root))
        try:
            shutil.copytree(
                source_dir,
                staging / "pkg",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".DS_Store"),
            )
            backup = None
            if target.exists():
                backup = root / f".{manifest.id}.old-{int(time.time() * 1000)}"
                os.replace(target, backup)
            os.replace(staging / "pkg", target)
            if backup is not None:
                shutil.rmtree(backup, ignore_errors=True)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        state = load_state(user_id)
        entry = _entry(state, manifest.id)
        is_upgrade = bool(entry.get("installed_at"))
        entry.update(
            {
                "version": manifest.version,
                "source": source,
                "origin": origin,
                "installed_at": entry.get("installed_at") or time.time(),
                "updated_at": time.time(),
            }
        )
        entry.setdefault("enabled", True)
        entry.setdefault("credentials", {})
        # Credentials the new version no longer declares are dropped on upgrade.
        if is_upgrade:
            names = {c.name for c in manifest.credentials}
            entry["credentials"] = {k: v for k, v in entry["credentials"].items() if k in names}
        save_state(state, user_id)
    return get_installed(manifest.id, user_id)


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
    with _lock:
        state = load_state(user_id)
        if plugin_id not in state.get("plugins", {}):
            raise PluginError(f"插件 {plugin_id} 未安装", 404)
        state["plugins"].pop(plugin_id, None)
        save_state(state, user_id)
        shutil.rmtree(packages_dir(user_id) / plugin_id, ignore_errors=True)


def set_enabled(plugin_id: str, enabled: bool, user_id: Optional[str] = None) -> InstalledPlugin:
    with _lock:
        get_installed(plugin_id, user_id)
        state = load_state(user_id)
        _entry(state, plugin_id)["enabled"] = bool(enabled)
        save_state(state, user_id)
    return get_installed(plugin_id, user_id)


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
    if not plugin.enabled:
        return {"ok": False, "error": f"插件「{plugin.manifest.name}」已停用"}
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
