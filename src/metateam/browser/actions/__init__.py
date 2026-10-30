"""Browser actions package — fillable helpers + composed mixin."""

from .fillable import (
    _FILLABLE_ROLES,
    _FILLABLE_TAGS,
    _PROBE_FILLABLE_JS,
    element_is_fillable,
    non_fillable_input_error,
)
from .helpers import BrowserActionHelpersMixin
from .pick import BrowserPickMixin
from .click import BrowserClickMixin
from .input_ops import BrowserInputMixin
from .motion import BrowserMotionMixin


class BrowserActionsMixin(
    BrowserPickMixin,
    BrowserClickMixin,
    BrowserInputMixin,
    BrowserMotionMixin,
    BrowserActionHelpersMixin,
):
    """Composed browser action surface used by BrowserSandbox."""


__all__ = [
    "BrowserActionsMixin",
    "element_is_fillable",
    "non_fillable_input_error",
    "_FILLABLE_TAGS",
    "_FILLABLE_ROLES",
    "_PROBE_FILLABLE_JS",
]
