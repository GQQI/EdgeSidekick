"""Plugin package manifest (``manifest.json``).

A package is a folder (or a zip of one) with::

    manifest.json   identity, credentials form, tool declarations
    main.py         one function per tool (see sdk.py)
    icon.svg|png    optional
    README.md       optional, shown on the marketplace detail page
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_FILE = "manifest.json"
ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
TOOL_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}([.-][0-9A-Za-z.-]+)?$")
CREDENTIAL_TYPES = ("secret", "text", "select")
TOOL_PREFIX = "plugin_"
MAX_ICON_BYTES = 96_000
MAX_README_CHARS = 20_000


class ManifestError(ValueError):
    pass


@dataclass
class CredentialField:
    name: str
    label: str
    type: str = "secret"
    required: bool = True
    default: str = ""
    placeholder: str = ""
    help: str = ""
    url: str = ""
    options: list[dict[str, str]] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "default": self.default,
            "placeholder": self.placeholder,
            "help": self.help,
            "url": self.url,
            "options": list(self.options),
        }


@dataclass
class ToolSpec:
    name: str
    label: str
    description: str
    parameters: dict[str, Any]
    readonly: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "readonly": self.readonly,
            "parameters": self.parameters,
        }


@dataclass
class OAuthSpec:
    authorize_url: str
    token_url: str
    scopes: list[str]
    client_id_field: str
    client_secret_field: str
    token_field: str
    extra_params: dict[str, str] = field(default_factory=dict)
    label: str = "授权"

    def public(self) -> dict[str, Any]:
        return {"scopes": list(self.scopes), "label": self.label, "token_field": self.token_field}


@dataclass
class Manifest:
    id: str
    name: str
    version: str
    description: str
    author: str = ""
    category: str = "其他"
    tags: list[str] = field(default_factory=list)
    homepage: str = ""
    entry: str = "main.py"
    icon: str = ""
    timeout: float = 30.0
    network: list[str] = field(default_factory=list)
    credentials: list[CredentialField] = field(default_factory=list)
    tools: list[ToolSpec] = field(default_factory=list)
    oauth: OAuthSpec | None = None

    def tool_name(self, tool: str) -> str:
        return qualified_tool_name(self.id, tool)

    def credential(self, name: str) -> CredentialField | None:
        return next((c for c in self.credentials if c.name == name), None)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "category": self.category,
            "tags": list(self.tags),
            "homepage": self.homepage,
            "network": list(self.network),
            "credentials": [c.public() for c in self.credentials],
            "tools": [t.public() for t in self.tools],
            "oauth": self.oauth.public() if self.oauth else None,
        }


def qualified_tool_name(plugin_id: str, tool: str) -> str:
    return f"{TOOL_PREFIX}{plugin_id.replace('-', '_')}_{tool}"


def _str(raw: Any, limit: int = 400) -> str:
    return str(raw if raw is not None else "").strip()[:limit]


def _parameters(raw: Any, where: str) -> dict[str, Any]:
    if raw is None:
        return {"type": "object", "properties": {}}
    if not isinstance(raw, dict) or raw.get("type", "object") != "object":
        raise ManifestError(f"{where}: parameters must be a JSON Schema object")
    props = raw.get("properties") or {}
    if not isinstance(props, dict):
        raise ManifestError(f"{where}: parameters.properties must be an object")
    out = dict(raw)
    out["type"] = "object"
    out["properties"] = props
    req = out.get("required")
    if req is not None and (not isinstance(req, list) or any(r not in props for r in req)):
        raise ManifestError(f"{where}: parameters.required lists unknown properties")
    return out


def parse_manifest(data: Any) -> Manifest:
    if not isinstance(data, dict):
        raise ManifestError("manifest.json must be a JSON object")
    pid = _str(data.get("id"), 64)
    if not ID_RE.match(pid):
        raise ManifestError("id must be 2-32 chars of lowercase letters, digits or '-' and start with a letter")
    name = _str(data.get("name"), 80)
    if not name:
        raise ManifestError("name is required")
    version = _str(data.get("version"), 40) or "0.0.0"
    if not VERSION_RE.match(version):
        raise ManifestError(f"version {version!r} is not a valid version string")
    entry = _str(data.get("entry"), 120) or "main.py"
    if entry.startswith(("/", "\\")) or ".." in Path(entry).parts or not entry.endswith(".py"):
        raise ManifestError("entry must be a relative .py path inside the package")

    creds: list[CredentialField] = []
    for i, raw in enumerate(data.get("credentials") or []):
        if not isinstance(raw, dict):
            raise ManifestError(f"credentials[{i}] must be an object")
        cname = _str(raw.get("name"), 48)
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,47}$", cname):
            raise ManifestError(f"credentials[{i}].name is invalid")
        ctype = _str(raw.get("type"), 16) or "secret"
        if ctype not in CREDENTIAL_TYPES:
            raise ManifestError(f"credentials[{i}].type must be one of {', '.join(CREDENTIAL_TYPES)}")
        options = [
            {"value": _str(o.get("value"), 120), "label": _str(o.get("label") or o.get("value"), 120)}
            for o in (raw.get("options") or [])
            if isinstance(o, dict) and _str(o.get("value"), 120)
        ]
        if ctype == "select" and not options:
            raise ManifestError(f"credentials[{i}] is a select without options")
        if any(c.name == cname for c in creds):
            raise ManifestError(f"duplicate credential {cname!r}")
        creds.append(
            CredentialField(
                name=cname,
                label=_str(raw.get("label"), 80) or cname,
                type=ctype,
                required=bool(raw.get("required", True)),
                default=_str(raw.get("default"), 400),
                placeholder=_str(raw.get("placeholder"), 200),
                help=_str(raw.get("help"), 600),
                url=_str(raw.get("url"), 400),
                options=options,
            )
        )

    tools: list[ToolSpec] = []
    raw_tools = data.get("tools") or []
    if not isinstance(raw_tools, list) or not raw_tools:
        raise ManifestError("a plugin must declare at least one tool")
    for i, raw in enumerate(raw_tools):
        if not isinstance(raw, dict):
            raise ManifestError(f"tools[{i}] must be an object")
        tname = _str(raw.get("name"), 64)
        if not TOOL_RE.match(tname):
            raise ManifestError(f"tools[{i}].name must be lowercase snake_case")
        if len(qualified_tool_name(pid, tname)) > 64:
            raise ManifestError(f"tools[{i}]: plugin id + tool name exceed 64 characters")
        if any(t.name == tname for t in tools):
            raise ManifestError(f"duplicate tool {tname!r}")
        desc = _str(raw.get("description"), 1200)
        if not desc:
            raise ManifestError(f"tools[{i}].description is required — the model picks tools by it")
        tools.append(
            ToolSpec(
                name=tname,
                label=_str(raw.get("label"), 80) or tname,
                description=desc,
                parameters=_parameters(raw.get("parameters"), f"tools[{i}]"),
                readonly=bool(raw.get("readonly", False)),
            )
        )

    oauth = _oauth(data.get("oauth"), creds)

    try:
        timeout = float(data.get("timeout") or 30)
    except (TypeError, ValueError):
        timeout = 30.0
    return Manifest(
        id=pid,
        name=name,
        version=version,
        description=_str(data.get("description"), 600),
        author=_str(data.get("author"), 80),
        category=_str(data.get("category"), 40) or "其他",
        tags=[_str(t, 32) for t in (data.get("tags") or []) if _str(t, 32)][:12],
        homepage=_str(data.get("homepage"), 400),
        entry=entry,
        icon=_str(data.get("icon"), 120),
        timeout=max(3.0, min(timeout, 300.0)),
        network=[_str(h, 120) for h in (data.get("network") or []) if _str(h, 120)][:20],
        credentials=creds,
        tools=tools,
        oauth=oauth,
    )


def _oauth(raw: Any, creds: list[CredentialField]) -> OAuthSpec | None:
    if raw in (None, {}):
        return None
    if not isinstance(raw, dict):
        raise ManifestError("oauth must be an object")
    names = {c.name: c for c in creds}
    spec = OAuthSpec(
        authorize_url=_str(raw.get("authorize_url"), 400),
        token_url=_str(raw.get("token_url"), 400),
        scopes=[_str(x, 200) for x in (raw.get("scopes") or []) if _str(x, 200)],
        client_id_field=_str(raw.get("client_id_field"), 48) or "client_id",
        client_secret_field=_str(raw.get("client_secret_field"), 48) or "client_secret",
        token_field=_str(raw.get("token_field"), 48) or "refresh_token",
        extra_params={str(k)[:48]: _str(v, 200) for k, v in (raw.get("extra_params") or {}).items()},
        label=_str(raw.get("label"), 40) or "授权",
    )
    for url in (spec.authorize_url, spec.token_url):
        if not url.startswith("https://"):
            raise ManifestError("oauth authorize_url and token_url must be https URLs")
    for fname in (spec.client_id_field, spec.client_secret_field, spec.token_field):
        if fname not in names:
            raise ManifestError(f"oauth refers to undeclared credential {fname!r}")
    if names[spec.token_field].type != "secret":
        raise ManifestError("oauth token_field must be a secret credential")
    return spec


def load_manifest(package_dir: Path) -> Manifest:
    path = package_dir / MANIFEST_FILE
    if not path.is_file():
        raise ManifestError(f"{MANIFEST_FILE} not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{MANIFEST_FILE} is not valid JSON: {exc}") from exc
    manifest = parse_manifest(data)
    if not (package_dir / manifest.entry).is_file():
        raise ManifestError(f"entry file {manifest.entry} not found in package")
    return manifest


def icon_data_uri(package_dir: Path, manifest: Manifest) -> str:
    """Inline icon so <img> needs no authenticated request."""
    if not manifest.icon:
        return ""
    path = (package_dir / manifest.icon).resolve()
    if package_dir.resolve() not in path.parents or not path.is_file():
        return ""
    mime = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(
        path.suffix.lower()
    )
    if not mime:
        return ""
    raw = path.read_bytes()
    if len(raw) > MAX_ICON_BYTES:
        return ""
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def readme_text(package_dir: Path) -> str:
    for name in ("README.md", "readme.md", "README_CN.md"):
        path = package_dir / name
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")[:MAX_README_CHARS]
    return ""
