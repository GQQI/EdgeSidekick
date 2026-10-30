"""Multi-version plugin install + package provenance (author / source / version)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from metateam.plugins import store
from metateam.plugins.manifest import parse_manifest
from metateam.plugins.store import (
    get_installed,
    install_from_zip,
    list_version_records,
    set_active_version,
    uninstall_version,
)


def _zip(files: dict[str, str], prefix: str = "pkg/") -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(prefix + name, content)
    return buf.getvalue()


def _pkg(*, version: str, author: str = "Ada", source: str = "https://example.com/p") -> bytes:
    main = (
        "def greet(params, ctx):\n"
        f"    return 'hi-' + str(params.get('name') or 'world') + '-{version}'\n"
    )
    manifest = {
        "id": "multi-ver",
        "name": "Multi Ver",
        "version": version,
        "author": author,
        "source": source,
        "description": "versioned offline plugin",
        "network": [],
        "credentials": [],
        "tools": [
            {
                "name": "greet",
                "description": "Say hi",
                "readonly": True,
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                },
            }
        ],
    }
    return _zip({"manifest.json": json.dumps(manifest), "main.py": main})


@pytest.fixture()
def tenant_plugins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(store, "tenant_dir", lambda user_id=None: tmp_path)
    monkeypatch.setattr(store, "packages_dir", lambda user_id=None: tmp_path / "plugins")
    (tmp_path / "plugins").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_manifest_parses_author_source_aliases() -> None:
    m = parse_manifest(
        {
            "id": "alias-demo",
            "name": "Alias",
            "version": "2.0.0",
            "maker": "李四",
            "repository": "git@host/repo",
            "description": "d",
            "tools": [{"name": "t", "description": "tool"}],
        }
    )
    assert m.author == "李四"
    assert m.source == "git@host/repo"
    assert m.version == "2.0.0"
    pub = m.public()
    assert pub["author"] == "李四"
    assert pub["source"] == "git@host/repo"


def test_upload_keeps_multiple_versions_and_activates_latest(tenant_plugins: Path) -> None:
    p1 = install_from_zip(_pkg(version="1.0.0", author="A1", source="src-a"), source="upload", origin="a.zip")
    assert p1.manifest.version == "1.0.0"
    assert p1.manifest.author == "A1"
    assert p1.manifest.source == "src-a"
    assert (tenant_plugins / "plugins" / "multi-ver" / "versions" / "1.0.0" / "manifest.json").is_file()

    p2 = install_from_zip(_pkg(version="1.1.0", author="A2", source="src-b"), source="upload", origin="b.zip")
    assert p2.manifest.version == "1.1.0"
    assert p2.active_version == "1.1.0"
    rows = list_version_records("multi-ver")
    assert {r["version"] for r in rows} == {"1.0.0", "1.1.0"}
    assert sum(1 for r in rows if r["active"]) == 1
    assert next(r for r in rows if r["active"])["version"] == "1.1.0"
    # Old version still on disk
    assert (tenant_plugins / "plugins" / "multi-ver" / "versions" / "1.0.0" / "manifest.json").is_file()


def test_switch_and_remove_version(tenant_plugins: Path) -> None:
    install_from_zip(_pkg(version="1.0.0"), source="upload", origin="a.zip")
    install_from_zip(_pkg(version="2.0.0"), source="upload", origin="b.zip")
    switched = set_active_version("multi-ver", "1.0.0")
    assert switched.manifest.version == "1.0.0"
    assert get_installed("multi-ver").active_version == "1.0.0"

    result = uninstall_version("multi-ver", "2.0.0")
    assert result["removed_plugin"] is False
    rows = list_version_records("multi-ver")
    assert [r["version"] for r in rows] == ["1.0.0"]

    result2 = uninstall_version("multi-ver", "1.0.0")
    assert result2["removed_plugin"] is True
    with pytest.raises(store.PluginError):
        get_installed("multi-ver")


def test_legacy_flat_layout_migrates(tenant_plugins: Path) -> None:
    """Old plugins/<id>/manifest.json layout still loads and migrates."""
    flat = tenant_plugins / "plugins" / "legacy"
    flat.mkdir(parents=True)
    (flat / "manifest.json").write_text(
        json.dumps(
            {
                "id": "legacy",
                "name": "Legacy",
                "version": "0.9.0",
                "author": "Old",
                "source": "archive",
                "description": "flat",
                "tools": [{"name": "ping", "description": "ping"}],
            }
        ),
        encoding="utf-8",
    )
    (flat / "main.py").write_text("def ping(params, ctx):\n    return 'pong'\n", encoding="utf-8")
    state = {"plugins": {"legacy": {"enabled": True, "source": "upload", "installed_at": 1}}}
    store.save_state(state)
    plugin = get_installed("legacy")
    assert plugin.manifest.version == "0.9.0"
    assert plugin.manifest.author == "Old"
    assert (tenant_plugins / "plugins" / "legacy" / "versions" / "0.9.0" / "manifest.json").is_file()
