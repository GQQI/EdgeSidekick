from __future__ import annotations

from metateam.api.app import app


def _collect_paths(routes) -> set[str]:
    """Flatten FastAPI/Starlette routes, including lazily-wrapped sub-routers."""
    paths: set[str] = set()
    for r in routes:
        path = getattr(r, "path", "")
        if path:
            paths.add(path)
        sub_router = getattr(r, "original_router", None)
        if sub_router is not None:
            paths |= _collect_paths(getattr(sub_router, "routes", []))
    return paths


def test_app_registers_core_routes() -> None:
    paths = _collect_paths(app.routes)
    for needed in (
        "/api/sessions",
        "/api/chat",
        "/api/git",
        "/api/git/pull",
        "/api/git/push",
        "/api/git/fetch",
        "/api/git/checkout",
        "/api/git/remote",
        "/api/git/file-diff",
        "/api/git/review",
        "/api/memory/library",
        "/api/files/undo",
        "/api/sessions/{session_id}/events",
        "/api/skills",
        "/api/skills/validate",
        "/api/skills/import",
        "/api/skills/import-dir",
        "/api/skills/import-files",
        "/api/schedules",
        "/api/schedules/{task_id}",
    ):
        assert needed in paths, f"missing route {needed}"
