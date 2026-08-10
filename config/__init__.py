# config/__init__.py
from .config import *
from .config import _PARAMS

def __getattr__(name: str):
    from .config import __getattr__ as config_getattr
    return config_getattr(name)

def __dir__():
    from .config import _PARAMS
    return sorted(list(globals().keys()) + list(_PARAMS.keys()) + ["SESSION_PATH"])
