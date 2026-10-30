"""Browser challenge / CAPTCHA helpers.

Re-export the public JS source so ``from metateam.browser.challenge import
challenge_js_source`` keeps working. Ops mixin lives in ``.ops``.
"""
from .core import *  # noqa: F403
