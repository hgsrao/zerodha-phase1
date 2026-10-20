"""R8 alias of ``revision5.machine_dynamics``: one source for these formulas, no copy.

``revision8_avr.r8_machine_dynamics`` resolves to the ``revision5.machine_dynamics`` module object itself, so R8 and R5/R6 share
the same code and the same module state. A fix made in ``revision5.machine_dynamics`` reaches R8 directly.
"""
import sys as _sys
import importlib as _importlib

_sys.modules[__name__] = _importlib.import_module("revision5.machine_dynamics")
