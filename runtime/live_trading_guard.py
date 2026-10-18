"""Fail-closed LIVE_TRADING_ENABLED check for offline launchers.

Execution may proceed only when the environment variable is present and is exactly the
string ``False``. A missing value, ``True``, any other spelling (``false``, ``FALSE``,
``0``, ``no``), surrounding whitespace, or an empty value all refuse. The guard only reads
the environment; it never sets or repairs the value.
"""
import os
import sys

VARIABLE = "LIVE_TRADING_ENABLED"
REQUIRED = "False"


def live_trading_violation(environ=None):
    """Return None when execution is permitted, else a short refusal reason."""
    env = os.environ if environ is None else environ
    if VARIABLE not in env:
        return f"{VARIABLE} is not set; it must be exactly {REQUIRED!r}"
    value = env[VARIABLE]
    if value != REQUIRED:
        return f"{VARIABLE}={value!r} refused; it must be exactly {REQUIRED!r}"
    return None


def require_live_trading_disabled(environ=None):
    """Stop the process (SystemExit, non-zero) unless LIVE_TRADING_ENABLED is exactly 'False'."""
    reason = live_trading_violation(environ)
    if reason is not None:
        print(f"REFUSED: {reason}. No engine was constructed and no data was read.", file=sys.stderr)
        raise SystemExit(78)
