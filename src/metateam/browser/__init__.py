"""Browser domain package."""
from .actions import element_is_fillable, non_fillable_input_error
from .cdp import discover_cdp_url
from .challenge import challenge_js_source
from .dialogs import (
    dialog_interrupted,
    dialog_result_note,
    merge_dialog_log,
    public_dialog,
    release_unblocked_dialogs,
)
from .sandbox import (
    HOST_KIND,
    SANDBOX,
    SESSION_IDLE_SECONDS,
    SOCIAL_BROWSING,
    BrowserSandbox,
)
from .urls import (
    _looks_like_asset_filename,
    coerce_navigate_target,
    is_chromium_unsafe_port,
    is_shallow_site_rewind,
    lookup_screenshot_page_url,
    loopback_url_candidates,
    resolve_browser_target,
    sanitize_browser_url,
    script_navigation_error,
    url_has_chromium_unsafe_port,
    url_port,
    urls_match,
)

__all__ = [
    "SANDBOX",
    "BrowserSandbox",
    "HOST_KIND",
    "SOCIAL_BROWSING",
    "sanitize_browser_url",
    "coerce_navigate_target",
    "resolve_browser_target",
    "urls_match",
    "element_is_fillable",
    "non_fillable_input_error",
    "discover_cdp_url",
    "challenge_js_source",
]
