"""Tiny local bridge so the node verification script can use the app's REAL
model configuration (same tenant model.json the server uses) without going
through the authenticated HTTP API. Never prints secrets.

    .venv\\Scripts\\python.exe scripts\\verify_group_chat\\llm_bridge.py [user_id] [port]
POST /complete {"prompt": "...", "max_tokens": 900}  ->  {"text": "...", "ms": 1234}
"""
from __future__ import annotations

import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from metateam.core.config import get_settings  # noqa: E402
from metateam.runtime.llm import LLM  # noqa: E402
from metateam.services.model_config import apply_to_settings, load_model_config  # noqa: E402
from metateam.services.tenant_context import set_user  # noqa: E402

USER_ID = sys.argv[1] if len(sys.argv) > 1 else "default"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8799

set_user(USER_ID)
SETTINGS = get_settings()
apply_to_settings(SETTINGS, load_model_config())
SYSTEM = (
    "You are one member of a multi-agent group chat inside the Sidekick app. "
    "Follow the instructions in the user message exactly and speak only as yourself. "
    "Verification run: no tools and no workspace files are available here, so never emit tool calls "
    "or XML tags; if a task needs the user's files, ask for them or give concrete guidance instead."
)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def do_GET(self):
        body = json.dumps({"ok": True, "model": SETTINGS.model, "demo": bool(SETTINGS.demo_mode)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        set_user(USER_ID)
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        req = json.loads(raw.decode("utf-8") or "{}")
        started = time.time()
        text, err = "", ""
        for attempt in range(3):
            try:
                # 0 / missing = the app's own setting (Settings.max_tokens, 0 = provider default), like /api/chat.
                mt = int(req.get("max_tokens") or 0)
                llm = LLM(SETTINGS, max_tokens=mt if mt > 0 else None)
                msg = llm.chat(
                    [{"role": "system", "content": SYSTEM}, {"role": "user", "content": str(req.get("prompt") or "")}],
                    tools=None,
                )
                text = re.sub(r"<think>[\s\S]*?</think>", "", str(msg.get("content") or "")).strip()
                err = ""
                break
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {str(exc)[:200]}"
                time.sleep(1.5 * (attempt + 1))
        body = json.dumps({"text": text, "error": err, "ms": int((time.time() - started) * 1000)}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print(f"bridge on 127.0.0.1:{PORT} model={SETTINGS.model} demo={SETTINGS.demo_mode}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
