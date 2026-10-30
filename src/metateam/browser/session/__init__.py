"""Browser session / CDP / sandbox facade.

Import concrete modules (e.g. ``session.sandbox``, ``session.constants``)
rather than star-importing this package — the facade pulls in actions/observe
and must not load during lightweight constant imports.
"""
