"""V11 -> P02 Rebalance Bridge — shared Kite credential loading.

One function, used by both v34_bridge_trigger_main.py and
v34_bridge_runner_main.py: read KITE_API_KEY/KITE_ACCESS_TOKEN from the
environment and construct an authenticated kiteconnect.KiteConnect
instance. Factored out here once a second real entrypoint needed the
identical logic - this session's own recurring rule (the read-only
client, then the retry-count schema field) has been: don't abstract
ahead of a second real need, but don't duplicate once there is one.

Matches run_production.py's established credential-handling convention
for this project exactly: never prompted for, never logged, never
hardcoded - FAIL_CLOSED (a plain RuntimeError) if either variable is
missing, not a silent None passed through to the SDK.
"""

from __future__ import annotations

import os
from typing import Any


def build_kite_client_from_env() -> Any:
    """Constructs a real kiteconnect.KiteConnect from KITE_API_KEY/
    KITE_ACCESS_TOKEN. Imported lazily (not at module top level) so this
    module stays importable - and its refusal path fast to test - even
    where the kiteconnect package isn't installed."""
    from kiteconnect import KiteConnect

    api_key = os.getenv("KITE_API_KEY")
    access_token = os.getenv("KITE_ACCESS_TOKEN")
    if not api_key:
        raise RuntimeError("FAIL_CLOSED: KITE_API_KEY environment variable is missing.")
    if not access_token:
        raise RuntimeError("FAIL_CLOSED: KITE_ACCESS_TOKEN environment variable is missing.")

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite
