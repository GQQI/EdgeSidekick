"""Static checks for Linux icons, electron-builder config, and fetch hardening."""

from __future__ import annotations

import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"
YML = DESKTOP / "electron-builder.yml"
ICONS = DESKTOP / "build" / "icons"
MAIN = DESKTOP / "main.mjs"
API_TS = ROOT / "ui" / "src" / "api" / "index.ts"
PKG = DESKTOP / "package.json"

LINUX_ICON_SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"not a PNG: {path}"
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def test_electron_builder_linux_icons_and_schema_safe_fields() -> None:
    yml = YML.read_text(encoding="utf-8")
    assert re.search(r"(?m)^linux:\n(?:  .*\n)*  icon: build/icons\n", yml)
    assert "win:" in yml and "icon: build/icon.png" in yml
    assert "Icon: edgesidekick" in yml
    assert "desktopName" not in yml
    assert "syncDesktopName" not in yml
    assert "afterInstall: build/linux-after-install.sh" in yml
    assert "  - build/icon.png" in yml
    pkg = PKG.read_text(encoding="utf-8")
    assert '"homepage"' in pkg
    assert "desktopName" not in pkg


def test_linux_hicolor_png_sizes() -> None:
    assert ICONS.is_dir()
    for size in LINUX_ICON_SIZES:
        path = ICONS / f"{size}x{size}.png"
        assert path.is_file(), path
        w, h = _png_size(path)
        assert (w, h) == (size, size), (path, w, h)
    src = DESKTOP / "build" / "icon.png"
    assert src.is_file()
    w, h = _png_size(src)
    assert w >= 256 and h >= 256
    after = DESKTOP / "build" / "linux-after-install.sh"
    text = after.read_text(encoding="utf-8")
    assert "gtk-update-icon-cache" in text
    assert "\r" not in text


def test_main_waits_for_current_health_and_retries_ui() -> None:
    src = MAIN.read_text(encoding="utf-8")
    assert "function loadUiWithRetry" in src
    assert "healthIsCurrent(late.json)" in src
    assert "opening anyway" not in src
    assert "Backend health timeout" in src
    assert "无法连接本地后端" in src


def test_api_client_has_base_fallback_retry_and_clear_errors() -> None:
    src = API_TS.read_text(encoding="utf-8")
    assert "export function getApiBase" in src
    assert "http://127.0.0.1:8787" in src
    assert "async function apiFetch" in src
    assert "formatNetworkError" in src
    assert "无法连接本地后端" in src
    assert "isTransientNetworkError" in src
    # HTTP status errors must remain distinct from transport failures
    assert "if (!r.ok)" in src
