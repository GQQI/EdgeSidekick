"""Browser sandbox (Playwright)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from ...services.browser_sandbox import SANDBOX, resolve_browser_target
from ...core.hostinfo import network_available
from ..schemas import BrowserActBody, BrowserNavigateBody, BrowserPickBody, BrowserStartBody


def _reject_public_if_offline(url: str) -> None:
    u = (url or "").strip().lower()
    if not (u.startswith("http://") or u.startswith("https://")):
        return
    # local / loopback always allowed
    if "://127." in u or "://localhost" in u or "://[::1]" in u:
        return
    if not network_available():
        raise HTTPException(
            503,
            "host offline — cannot open public web pages. Use a local workspace HTML file instead.",
        )

router = APIRouter(prefix="/api/browser", tags=["browser"])


@router.get("/status")
def api_browser_status() -> dict[str, Any]:
    return SANDBOX.status()


@router.post("/session")
def api_browser_session(body: BrowserStartBody) -> dict[str, Any]:
    try:
        _reject_public_if_offline(body.url or "")
        return SANDBOX.ensure_session(url=body.url, headless=bool(body.headless))
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/session")
def api_browser_session_close() -> dict[str, str]:
    try:
        SANDBOX.close()
    except Exception:
        # Best-effort: never fail the UI just because the worker is busy.
        pass
    return {"status": "ok"}


@router.post("/navigate")
def api_browser_navigate(body: BrowserNavigateBody) -> dict[str, Any]:
    try:
        _reject_public_if_offline(body.url)
        return SANDBOX.navigate(body.url)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/preview-local")
def api_browser_preview_local(body: BrowserNavigateBody) -> dict[str, str]:
    """Turn a workspace HTML path / file:// into an http://127.0.0.1 preview link."""
    target = resolve_browser_target(body.url)
    if not target:
        raise HTTPException(
            400,
            "not a local workspace HTML file — use a relative path like report.html",
        )
    return {"url": target}


@router.post("/back")
def api_browser_back(lane: str = "social") -> dict[str, Any]:
    lane_arg = lane if lane in ("social", "desk") else "social"
    try:
        return SANDBOX.go_back(lane=lane_arg)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/act")
def api_browser_act(body: BrowserActBody) -> dict[str, Any]:
    lane_arg = body.lane if body.lane in ("social", "desk") else "social"
    op = (body.op or "").strip().lower()
    try:
        if op == "back":
            return SANDBOX.go_back(lane=lane_arg)
        if op == "forward":
            return SANDBOX.go_forward(lane=lane_arg)
        return SANDBOX.human_act(
            lane=lane_arg,
            op=op,
            x=float(body.x or 0),
            y=float(body.y or 0),
            text=body.text or "",
            key=body.key or "",
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/screenshot")
def api_browser_screenshot(full_page: bool = False, lane: str = ""):
    lane_arg = lane if lane in ("social", "desk") else None
    try:
        png = SANDBOX.screenshot_png(full_page=bool(full_page), lane=lane_arg)
    except Exception:
        # Session gone / browser busy — fall back to the last good frame so
        # the chat bubble and fullscreen preview keep showing the page
        # instead of blanking out.
        png = SANDBOX.last_screenshot(lane=lane_arg)
        if png is None:
            raise HTTPException(400, "no browser session and no cached frame")
        return Response(content=png, media_type="image/png", headers={"X-Stale-Frame": "1"})
    return Response(content=png, media_type="image/png")


@router.get("/console")
def api_browser_console(limit: int = 80) -> dict[str, Any]:
    logs = SANDBOX.console_logs(limit=limit)
    return {"count": len(logs), "logs": logs}


@router.post("/select")
def api_browser_select(body: BrowserPickBody) -> dict[str, Any]:
    try:
        payload = SANDBOX.pick_element(
            timeout_ms=int(body.timeout_ms),
            with_screenshot=bool(body.with_screenshot),
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    if payload is None:
        return {"ok": False, "element": None, "message": "cancelled or timed out"}
    return {"ok": True, "element": payload.model_dump()}


@router.post("/select/cancel")
def api_browser_select_cancel() -> dict[str, str]:
    SANDBOX.cancel_pick()
    return {"status": "ok"}
