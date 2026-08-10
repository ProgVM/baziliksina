# config/__init__.py
from .config import *
from .config import _PARAMS, reload_config_from_db

def __getattr__(name: str):
    from .config import _PARAMS
    if name in _PARAMS:
        return _PARAMS[name].evaluate()
    raise AttributeError(f"module 'config' has no attribute '{name}'")

def __dir__():
    from .config import _PARAMS
    return sorted(list(globals().keys()) + list(_PARAMS.keys()))
