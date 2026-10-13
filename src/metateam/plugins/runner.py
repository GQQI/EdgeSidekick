"""Execute one plugin call. Runs as a child process: ``python runner.py``.

Request (stdin, JSON)::

    {"dir": "...", "entry": "main.py", "id": "github", "action": "invoke" | "validate",
     "tool": "search_issues", "params": {...}, "credentials": {...}, "timeout": 30}

Response (last stdout line, JSON)::

    {"ok": true, "result": "..."}  |  {"ok": false, "error": "..."}

Plugin ``print`` output goes to stderr so it cannot corrupt the response.
Credentials travel on stdin only — never argv or the environment.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent


def _sdk() -> ModuleType:
    mod = sys.modules.get("sidekick_plugin")
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location("sidekick_plugin", _HERE / "sdk.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sidekick_plugin"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_entry(plugin_dir: Path, entry: str, plugin_id: str) -> ModuleType:
    _sdk()
    path = (plugin_dir / entry).resolve()
    if plugin_dir.resolve() not in path.parents:
        raise RuntimeError("entry must live inside the plugin package")
    if str(plugin_dir) not in sys.path:
        sys.path.insert(0, str(plugin_dir))
    name = f"sidekick_plugin_pkg_{plugin_id.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load {entry}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def execute(request: dict[str, Any], *, http: Any = None) -> dict[str, Any]:
    """Run one request in this process. ``http`` lets tests inject a mock client."""
    sdk = _sdk()
    plugin_dir = Path(str(request.get("dir") or ""))
    plugin_id = str(request.get("id") or plugin_dir.name)
    ctx = sdk.PluginContext(
        request.get("credentials") or {},
        http=http,
        timeout=float(request.get("timeout") or 30),
        plugin_id=plugin_id,
    )
    try:
        mod = _load_entry(plugin_dir, str(request.get("entry") or "main.py"), plugin_id)
        action = str(request.get("action") or "invoke")
        if action == "validate":
            fn = getattr(mod, "validate_credentials", None)
            if fn is None:
                return {"ok": True, "result": "该插件未提供凭据校验，已保存。"}
            out = fn(ctx)
            return {"ok": True, "result": sdk.to_text(out if out is not None else "凭据可用。")}
        tool = str(request.get("tool") or "")
        fn = getattr(mod, tool, None)
        if not tool or tool.startswith("_") or not callable(fn):
            return {"ok": False, "error": f"插件没有实现工具 {tool!r}"}
        params = request.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        return {"ok": True, "result": sdk.to_text(fn(params, ctx))}
    except sdk.ToolError as exc:
        return {"ok": False, "error": str(exc) or "插件调用失败"}
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc(limit=4)
        print(tb, file=sys.stderr)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:600]}
    finally:
        if http is None:
            ctx.close()


def main(argv: Optional[list[str]] = None) -> int:
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        request = json.loads(sys.stdin.read() or "{}")
        response = execute(request)
    except Exception as exc:  # noqa: BLE001
        response = {"ok": False, "error": f"runner: {exc}"}
    real_stdout.write("\n" + json.dumps(response, ensure_ascii=False) + "\n")
    real_stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
