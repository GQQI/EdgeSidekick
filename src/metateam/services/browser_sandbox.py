"""Compat shim — implementation lives in metateam.browser."""
from metateam.browser.sandbox import *  # noqa: F403
from metateam.browser.urls import *  # noqa: F403
from metateam.browser.actions import element_is_fillable, non_fillable_input_error  # noqa: F401
from metateam.browser.cdp import discover_cdp_url, _find_guest_page, _is_workbench_url  # noqa: F401
from metateam.browser.challenge import challenge_js_source  # noqa: F401
from metateam.browser.dialogs import *  # noqa: F403
from metateam.browser.thread import _WORKER, _PlaywrightThread, _Job  # noqa: F401
