"""R8 alias of ``revision6_avr.r6_avr_core_fixed``: one source for these formulas, no copy.

``revision8_avr.r8_avr_core`` resolves to the ``revision6_avr.r6_avr_core_fixed`` module object itself, so R8 and R5/R6 share
the same code and the same module state. A fix made in ``revision6_avr.r6_avr_core_fixed`` reaches R8 directly.
"""
import sys as _sys
import importlib as _importlib

_sys.modules[__name__] = _importlib.import_module("revision6_avr.r6_avr_core_fixed")
