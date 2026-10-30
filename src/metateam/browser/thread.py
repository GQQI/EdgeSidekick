"""Compat shim — implementation lives in metateam.browser.session.thread."""
from metateam.browser.session.thread import *  # noqa: F403
from metateam.browser.session.thread import (  # noqa: F401
    _Job,
    _PlaywrightThread,
    _WORKER,
)
