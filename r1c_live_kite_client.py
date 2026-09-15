"""R1-C live observer — minimal, read-only Kite historical-minute-bar
client. A NEW module, deliberately separate from
`v34_bridge_kite_read_only_client.py` (the V11 bridge's own frozen,
already-sealed read-only client) rather than an edit to it - that file
belongs to a different, already-closed phase of a different project
thread, and R1-C has its own, narrower need (1-minute OHLCV candles,
which that client does not fetch at all - it only exposes LTP/positions/
orders/trades).

**No write-capable Kite method is referenced anywhere in this file.**
Grep it yourself: no `place_order`, no `modify_order`, no `cancel_order`,
no `exit_order` - only `instruments()` and `historical_data()`, both
read-only market-data endpoints incapable of submitting anything.
`LIVE_TRADING_ENABLED` is not read, checked, or referenced anywhere in
this file, because there is no execution path here for it to gate.

Same exception discipline as the V11 bridge's own read-only client, for
the same reason: Kite exceptions (`NetworkException`, `TokenException`,
etc.) propagate completely unwrapped - no `try/except` around a
`self.kite.*()` call anywhere in this module - and a response that
connects fine but cannot be trusted (wrong type, missing field) raises
`KiteResponseMalformedError`, a distinct failure class from a
connectivity error. No retries anywhere in this module - retry/backoff
is the live observer's own decision, one layer up, same as the V11
bridge's own layering.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from kite_request_governor import DEFAULT as GOV_DEFAULT
from kite_request_governor import HISTORICAL as GOV_HISTORICAL
from kite_request_governor import KiteRequestGovernor

IST = timezone(timedelta(hours=5, minutes=30))


class KiteResponseMalformedError(RuntimeError):
    """The Kite API call itself succeeded; the response shape cannot be
    trusted. Never raised for a network/API error."""


class R1CLiveKiteClient:
    """Wraps a real `kiteconnect.KiteConnect` instance (or any duck-typed
    object exposing `.instruments(exchange)` and `.historical_data(...)`
    with the same signatures - never isinstance-checked, matching this
    project's own established test-double convention)."""

    def __init__(self, kite: Any, *, governor: Optional[KiteRequestGovernor] = None):
        self.kite = kite
        self._instrument_token_cache: Dict[str, int] = {}
        # EA1-R1, 2026-08-19: optional, defaults to None. This account's
        # credentials are shared with the V11 bridge terminals and P02's
        # live scan (owner-confirmed) - see kite_request_governor.py's
        # own module docstring. Gating here does not change this module's
        # "exceptions propagate completely unwrapped" contract at all.
        self.governor = governor

    def _gate(self, endpoint_class: str) -> None:
        if self.governor is not None:
            self.governor.acquire(endpoint_class)

    def _load_instrument_tokens(self) -> Dict[str, int]:
        self._gate(GOV_DEFAULT)
        instruments = self.kite.instruments("NSE")
        if not isinstance(instruments, list):
            raise KiteResponseMalformedError(f"kite.instruments('NSE'): expected a list, got {instruments!r}.")
        cache: Dict[str, int] = {}
        for row in instruments:
            if not isinstance(row, dict) or "tradingsymbol" not in row or "instrument_token" not in row:
                raise KiteResponseMalformedError(f"kite.instruments('NSE'): malformed instrument row {row!r}.")
            cache[row["tradingsymbol"]] = row["instrument_token"]
        return cache

    def instrument_token(self, symbol: str) -> int:
        """`symbol` here is "NIFTY 50" for the index, or a bare NSE equity
        tradingsymbol - matching this project's own established
        convention (see r1c_live_pillar1_evaluator's own NIFTY-routing
        comment)."""
        if not self._instrument_token_cache:
            self._instrument_token_cache = self._load_instrument_tokens()
        if symbol not in self._instrument_token_cache:
            raise KiteResponseMalformedError(
                f"instrument_token({symbol!r}): symbol not found in the NSE instruments dump."
            )
        return self._instrument_token_cache[symbol]

    def get_minute_bars(self, symbol: str, from_dt: datetime, to_dt: datetime) -> List[Dict[str, Any]]:
        """Real 1-minute OHLCV candles via `kite.historical_data(...,
        interval="minute")`. Returns rows already normalized to this
        project's own live-bar schema (`timestamp`/`open`/`high`/`low`/
        `close`/`volume`), the same shape every live evaluator's
        `ingest_1m_bar` already expects - the caller never has to know
        Kite's own field names."""
        token = self.instrument_token(symbol)
        self._gate(GOV_HISTORICAL)
        rows = self.kite.historical_data(token, from_dt, to_dt, "minute")
        if not isinstance(rows, list):
            raise KiteResponseMalformedError(
                f"kite.historical_data({symbol!r}): expected a list, got {rows!r}."
            )
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                raise KiteResponseMalformedError(f"kite.historical_data({symbol!r}): malformed row {row!r}.")
            for field_name in ("date", "open", "high", "low", "close"):
                if field_name not in row:
                    raise KiteResponseMalformedError(
                        f"kite.historical_data({symbol!r}): row missing {field_name!r}: {row!r}."
                    )
            ts = row["date"]
            # Real Kite responses carry a timezone-AWARE datetime (IST).
            # Normalized to a NAIVE IST wall-clock string here, at the
            # single boundary where broker data enters this project -
            # every live evaluator and the observer's own bookkeeping
            # then only ever compares naive datetimes to each other,
            # never aware to naive. This is the fix for a real bug found
            # live: `datetime.now()` (naive) compared directly against a
            # timestamp parsed from an aware Kite string raised
            # `TypeError: can't compare offset-naive and offset-aware
            # datetimes` on the very first continuous-mode run.
            if hasattr(ts, "isoformat"):
                if getattr(ts, "tzinfo", None) is not None:
                    ts = ts.astimezone(IST).replace(tzinfo=None)
                ts_str = ts.isoformat()
            else:
                ts_str = str(ts)
            out.append({
                "timestamp": ts_str,
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "volume": float(row.get("volume") or 0.0),
            })
        return out
