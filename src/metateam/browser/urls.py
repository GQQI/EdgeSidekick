"""Compat shim — implementation lives in metateam.browser.nav.urls."""
from metateam.browser.nav.urls import *  # noqa: F403
from metateam.browser.nav.urls import (  # noqa: F401
    _fix_url_encoding,
    _is_connection_refused,
    _is_history_nav_error,
    _is_unsafe_port_error,
    _looks_like_asset_filename,
)
