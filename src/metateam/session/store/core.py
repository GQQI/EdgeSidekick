"""SessionStore core — composed mixins."""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ...runtime.agent import Agent
from ...core.config import Settings, get_settings, reload_settings
from ...core.events import EventBus, new_id
from ...core.logutil import get_logger, log_exception
from ..session import load_session, load_session_bundle, save_session, sessions_dir, workspace_matches

_log = get_logger("metateam.store")

from .model import ChatSession
from .gates import SessionGatesMixin
from .lifecycle import SessionLifecycleMixin
from .persist import SessionPersistMixin
from .ui import SessionUiMixin


class SessionStore(
    SessionLifecycleMixin,
    SessionGatesMixin,
    SessionPersistMixin,
    SessionUiMixin,
):
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._lock = threading.Lock()
        self._sessions: dict[str, ChatSession] = {}


STORE = SessionStore()
