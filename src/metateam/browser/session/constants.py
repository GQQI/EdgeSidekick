"""Shared browser sandbox constants (no heavy imports — avoid cycles)."""
from __future__ import annotations

from contextvars import ContextVar

HOST_KIND = "cdp_playwright"

SOCIAL_BROWSING: ContextVar[bool] = ContextVar("sidekick_social_browsing", default=False)

SESSION_IDLE_SECONDS = 300.0

_REAP_INTERVAL_SECONDS = 60.0

_HUMAN_CONFIRM_S = 180.0

_USER_WAIT_TIMEOUT = 60.0 + (_HUMAN_CONFIRM_S * 2)

