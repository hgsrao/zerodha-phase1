"""R8 explicit evidence-consensus governor core.

R8 is intentionally separate from revision7_avr.  It does not alter R7.
"""

from .core import Evidence, Decision, R8Controller

__all__ = ["Evidence", "Decision", "R8Controller"]
