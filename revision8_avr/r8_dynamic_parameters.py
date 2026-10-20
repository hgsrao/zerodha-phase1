"""R8 alias of ``revision5.dynamic_parameters``: one source for these formulas, no copy.

``revision8_avr.r8_dynamic_parameters`` resolves to the ``revision5.dynamic_parameters`` module object itself, so R8 and R5/R6 share
the same code and the same module state. A fix made in ``revision5.dynamic_parameters`` reaches R8 directly.
"""
import sys as _sys
import importlib as _importlib

_sys.modules[__name__] = _importlib.import_module("revision5.dynamic_parameters")
