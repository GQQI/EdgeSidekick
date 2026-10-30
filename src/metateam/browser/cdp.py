"""Compat shim — implementation lives in metateam.browser.session.cdp."""
from metateam.browser.session.cdp import *  # noqa: F403
from metateam.browser.session.cdp import (  # noqa: F401
    _find_guest_page,
    _is_workbench_url,
)
