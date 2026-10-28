"""Plugin marketplace, installed plugins, credentials and OAuth."""

from __future__ import annotations

import html
from typing import Any, Optional

import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from ...plugins import marketplace, oauth
from ...plugins.manifest import icon_data_uri, readme_text
from ...plugins.store import (
    InstalledPlugin,
    PluginError,
    credential_status,
    current_user,
    download_package,
    get_installed,
    install_from_zip,
    invoke_tool,
    list_installed,
    list_version_records,
    set_active_version,
    set_credentials,
    set_enabled,
    uninstall,
    uninstall_version,
    verify_credentials,
)

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "plugins" / "templates" / "hello-plugin"
DOCS_DIR = Path(__file__).resolve().parents[4] / "docs"

router = APIRouter(prefix="/api/plugins", tags=["plugins"])

OAUTH_CALLBACK_PATH = "/api/plugins/oauth/callback"


class InstallBody(BaseModel):
    id: str = ""
    url: str = ""


class EnabledBody(BaseModel):
    enabled: bool


class CredentialsBody(BaseModel):
    values: dict[str, Optional[str]] = Field(default_factory=dict)
    clear: list[str] = Field(default_factory=list)
    verify: bool = True


class SourcesBody(BaseModel):
    sources: list[str] = Field(default_factory=list)


class InvokeBody(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


class ActiveVersionBody(BaseModel):
    version: str = ""


def _fail(exc: PluginError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


def _public(plugin: InstalledPlugin, *, detail: bool = False) -> dict[str, Any]:
    offline = marketplace.is_offline_manifest(plugin.manifest)
    versions = list_version_records(plugin.id)
    out = {
        **plugin.manifest.public(),
        "enabled": plugin.enabled,
        # Install channel (upload / url / marketplace) — not package provenance.
        "install_source": plugin.state.get("source", ""),
        "source": plugin.state.get("source", ""),
        "origin": plugin.state.get("origin", ""),
        # Package-declared provenance (来源) mirrored for clients that expect it
        # beside install_source; also present as manifest.public()["source"].
        "package_source": plugin.manifest.source,
        "author": plugin.manifest.author,
        "active_version": plugin.active_version,
        "versions": versions,
        "version_count": len(versions),
        "installed_at": plugin.state.get("installed_at"),
        "updated_at": plugin.state.get("updated_at"),
        "icon": icon_data_uri(plugin.path, plugin.manifest),
        "credential_status": credential_status(plugin),
        "agent_tools": [plugin.manifest.tool_name(t.name) for t in plugin.manifest.tools],
        "offline": offline,
        "tool_count": len(plugin.manifest.tools),
    }
    if detail:
        out["readme"] = readme_text(plugin.path)
    return out


@router.get("")
def api_plugins() -> list[dict[str, Any]]:
    return [_public(p) for p in list_installed()]


@router.get("/marketplace")
def api_marketplace(refresh: bool = False) -> dict[str, Any]:
    return marketplace.catalog(refresh=refresh)


@router.put("/marketplace/sources")
def api_marketplace_sources(body: SourcesBody) -> dict[str, Any]:
    try:
        marketplace.set_sources(body.sources)
    except PluginError as exc:
        raise _fail(exc) from exc
    return marketplace.catalog(refresh=True)


@router.post("/install")
def api_install(body: InstallBody) -> dict[str, Any]:
    try:
        if body.id.strip():
            plugin = marketplace.install_from_marketplace(body.id.strip())
        elif body.url.strip():
            raw = download_package(body.url.strip())
            plugin = install_from_zip(raw, source="url", origin=body.url.strip())
        else:
            raise PluginError("需要提供市场里的插件 id 或插件包下载地址")
    except PluginError as exc:
        raise _fail(exc) from exc
    return _public(plugin, detail=True)


@router.post("/upload")
async def api_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    raw = await file.read()
    try:
        plugin = install_from_zip(raw, source="upload", origin=file.filename or "")
    except PluginError as exc:
        raise _fail(exc) from exc
    return _public(plugin, detail=True)



def _zip_template() -> bytes:
    root = TEMPLATE_DIR
    if not root.is_dir() or not (root / "manifest.json").is_file():
        raise PluginError("开发模板不可用", 404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.name.startswith(".") or "__pycache__" in path.parts:
                continue
            arc = Path("hello-plugin") / path.relative_to(root)
            zf.write(path, arc.as_posix())
    return buf.getvalue()


@router.get("/template.zip")
def api_plugin_template_zip():
    """Download the hello-plugin sample package as a zip attachment."""
    try:
        raw = _zip_template()
    except PluginError as exc:
        raise _fail(exc) from exc
    return StreamingResponse(
        io.BytesIO(raw),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="hello-plugin.zip"'},
    )


@router.get("/guide")
def api_plugin_guide():
    """Short developer guide (markdown) for custom plugin uploaders."""
    for name in ("plugin-dev-guide.md", "plugin-upload.md", "plugins.md"):
        path = DOCS_DIR / name
        if path.is_file():
            return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")
    raise HTTPException(404, "开发说明不可用")


@router.get("/oauth/callback", response_class=HTMLResponse)
def api_oauth_callback(
    state: str = "",
    code: str = "",
    error: str = "",
    error_description: str = "",
) -> HTMLResponse:
    ok = False
    try:
        if error:
            raise PluginError(oauth.explain_provider_error(error, error_description))
        result = oauth.complete(state, code)
        ok = True
        message = f"「{result['name']}」授权成功，可以关闭这个窗口回到 EdgeSidekick。点「验证」确认即可。"
    except PluginError as exc:
        message = str(exc)
    color = "#16a34a" if ok else "#dc2626"
    body = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>插件授权</title>
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:system-ui,-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;display:grid;place-items:center;min-height:100vh;margin:0;background:#f8fafc;color:#0f172a">
<div style="max-width:420px;padding:28px 32px;border-radius:16px;background:#fff;box-shadow:0 10px 30px rgba(15,23,42,.08);text-align:center">
<div style="font-size:40px;color:{color}">{'✓' if ok else '!'}</div>
<p style="font-size:15px;line-height:1.6">{html.escape(message)}</p></div>
<script>try{{window.opener&&window.opener.postMessage({{type:"sidekick-plugin-oauth",ok:{str(ok).lower()}}},"*")}}catch(e){{}}{'setTimeout(function(){window.close()},1500)' if ok else ''}</script>
</body></html>"""
    return HTMLResponse(body, status_code=200 if ok else 400)


@router.get("/{plugin_id}")
def api_plugin(plugin_id: str) -> dict[str, Any]:
    try:
        return _public(get_installed(plugin_id), detail=True)
    except PluginError as exc:
        raise _fail(exc) from exc


@router.patch("/{plugin_id}")
def api_plugin_enable(plugin_id: str, body: EnabledBody) -> dict[str, Any]:
    try:
        return _public(set_enabled(plugin_id, body.enabled))
    except PluginError as exc:
        raise _fail(exc) from exc


@router.delete("/{plugin_id}")
def api_plugin_delete(plugin_id: str) -> dict[str, Any]:
    try:
        uninstall(plugin_id)
    except PluginError as exc:
        raise _fail(exc) from exc
    return {"status": "ok"}


@router.get("/{plugin_id}/versions")
def api_plugin_versions(plugin_id: str) -> dict[str, Any]:
    try:
        plugin = get_installed(plugin_id)
    except PluginError as exc:
        raise _fail(exc) from exc
    return {
        "id": plugin.id,
        "active_version": plugin.active_version,
        "versions": list_version_records(plugin_id),
    }


@router.put("/{plugin_id}/version")
def api_plugin_set_version(plugin_id: str, body: ActiveVersionBody) -> dict[str, Any]:
    try:
        plugin = set_active_version(plugin_id, body.version.strip())
    except PluginError as exc:
        raise _fail(exc) from exc
    return _public(plugin, detail=True)


@router.delete("/{plugin_id}/versions/{version}")
def api_plugin_delete_version(plugin_id: str, version: str) -> dict[str, Any]:
    try:
        result = uninstall_version(plugin_id, version)
    except PluginError as exc:
        raise _fail(exc) from exc
    if result.get("removed_plugin"):
        return result
    try:
        plugin = get_installed(plugin_id)
        return {**result, "plugin": _public(plugin, detail=True)}
    except PluginError:
        return result


@router.put("/{plugin_id}/credentials")
def api_plugin_credentials(plugin_id: str, body: CredentialsBody) -> dict[str, Any]:
    try:
        plugin = set_credentials(plugin_id, dict(body.values), clear=list(body.clear))
    except PluginError as exc:
        raise _fail(exc) from exc
    out = _public(plugin)
    if body.verify and out["credential_status"]["configured"]:
        out["verify"] = verify_credentials(plugin_id)
    return out


@router.post("/{plugin_id}/verify")
def api_plugin_verify(plugin_id: str) -> dict[str, Any]:
    try:
        return verify_credentials(plugin_id)
    except PluginError as exc:
        raise _fail(exc) from exc


@router.post("/{plugin_id}/oauth/start")
def api_plugin_oauth_start(plugin_id: str, request: Request) -> dict[str, str]:
    redirect_uri = str(request.base_url).rstrip("/") + OAUTH_CALLBACK_PATH
    try:
        return oauth.start(plugin_id, redirect_uri, current_user())
    except PluginError as exc:
        raise _fail(exc) from exc


@router.post("/{plugin_id}/tools/{tool}/invoke")
def api_plugin_invoke(plugin_id: str, tool: str, body: InvokeBody) -> dict[str, Any]:
    """Run one tool from the settings page, outside any chat."""
    try:
        return invoke_tool(plugin_id, tool, body.params)
    except PluginError as exc:
        raise _fail(exc) from exc
