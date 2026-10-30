"""Compat shim — implementation moved; do not add new logic here."""
import metateam.browser.queue as _m0
_SKIP = {
    "__name__",
    "__package__",
    "__loader__",
    "__spec__",
    "__file__",
    "__cached__",
    "__builtins__",
    "__doc__",
}
for _mod in (_m0,):
    for _k, _v in vars(_mod).items():
        if _k not in _SKIP:
            globals()[_k] = _v
del _SKIP, _mod, _k, _v, _m0
