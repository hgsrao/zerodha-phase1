"""R1-C composite engine — live Pillar I (C4) evaluator. LOCAL FILES /
IN-MEMORY BUFFERS ONLY in this module - no Kite calls here (the live
observer that feeds this module handles the real connection separately,
with the owner's own credentials). No broker writes, no orders,
anywhere in this module.

**Reuse, not reimplement**: Pillar I's D1 discovery script
(`p01d_pillar1_intraday_v1_d1.py`, in the separate sandbox at
`C:\\Users\\Dishan\\Python - ARR\\Zerodha_live_bot_3.4\\
P01D_intraday_intelligence_R0_20260815\\`) already contains the exact,
already-frozen, already-closed C4 signal logic
(`evaluate_candidate`) and its supporting bar-aggregation helpers
(`p01d_r6_same_symbol_execution_timing.build5`, `add_atr14_1m`). This
module imports those functions directly rather than re-deriving the
relative-strength / momentum / participation / breakout formulas by
hand - the single largest source of subtle divergence bugs in every
prior "port this logic elsewhere" step this project has done.

**Why a live re-evaluation loop, not a bespoke incremental algorithm**:
`evaluate_candidate(cid, params, stock5, nifty5, stock1_by_sym,
block_days)` already accepts an explicit `block_days` set. Restricting
that set to exactly today's date and calling it repeatedly against a
live-growing 1-minute buffer (re-aggregated to 5-minute bars via the
same `build5` each time) reuses 100% of the frozen logic, unmodified -
no new "streaming" reimplementation of the qualification checks exists
anywhere in this module. This costs some recomputation, never
correctness.

**Fail-closed on drift**: the three external files this module depends
on are hash-verified against the recorded closure-time hashes before
import. If any of them has changed since R0's closure, this module
refuses to import rather than silently reuse logic that may no longer
be what R0 actually tested and closed.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

SANDBOX_ROOT = Path(
    r"C:\Users\Dishan\Python - ARR\Zerodha_live_bot_3.4"
    r"\P01D_intraday_intelligence_R0_20260815"
)

# Recorded at R0's closure (P01D_PILLAR_I_INTRADAY_V1_CLOSURE_MANIFEST_20260818.txt
# and independently re-verified here on 2026-08-19 before this module was written).
_EXPECTED_HASHES = {
    "p01d_pillar1_intraday_v1_d1.py":
        "cb41623090322c75e6ec5f1037654eafd3d13f82b23914e1235d4dec27a32501",
    "p01d_r6_same_symbol_execution_timing.py":
        "479cfa908a242d23782fc67d7fbd9342c3c192c7a824e4064c685990060cec33",
    "p01d_r9_100k_bandwidth_real_costs.py":
        "bc4f6427fbea0f8cef3380c67824eb9ebdb10870d1e4e3eb7436fd9085a324b8",
}


class ReuseDriftError(RuntimeError):
    """Raised if a reused external file no longer matches what R0 closed
    against - refuses to import stale-relative-to-expectations logic
    rather than silently trusting it."""


def _verify_hash(filename: str) -> Path:
    """Reads and hash-verifies one reused file BEFORE it is ever
    imported - a drift is a load-time refusal, never a runtime
    surprise. Does not import anything itself, so it has no opinion on
    HOW the file gets imported afterward."""
    path = SANDBOX_ROOT / filename
    if not path.exists():
        raise ReuseDriftError(f"REFUSING TO IMPORT: {path} does not exist.")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = _EXPECTED_HASHES[filename]
    if actual != expected:
        raise ReuseDriftError(
            f"REFUSING TO IMPORT: {path} has changed since R0's closure - "
            f"expected sha256 {expected}, got {actual}. This module will not "
            f"silently reuse logic that may differ from what R0 actually "
            f"tested and closed."
        )
    return path


def _verify_and_import(module_name: str, filename: str):
    """Standalone (single-file) verify-then-load, used for files nothing
    else needs to `import <name>` internally (kept for direct callers /
    tests that want an isolated check without touching sys.path)."""
    path = _verify_hash(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Verified once at import time, not lazily - a drift is a load-time
# refusal, never a runtime surprise mid-evaluation.
#
# p01d_pillar1_intraday_v1_d1.py does a plain `import
# p01d_r6_same_symbol_execution_timing as r6` internally (and likewise
# for p01d_r9) - it needs those two importable by their REAL module
# names via sys.path, not loaded under a private alias the way
# `_verify_and_import` does for a standalone file. Each of the three
# files is still hash-verified individually before any import happens.
for _fn in ("p01d_r6_same_symbol_execution_timing.py",
            "p01d_r9_100k_bandwidth_real_costs.py",
            "p01d_pillar1_intraday_v1_d1.py"):
    _verify_hash(_fn)

if str(SANDBOX_ROOT) not in sys.path:
    sys.path.insert(0, str(SANDBOX_ROOT))

import p01d_r6_same_symbol_execution_timing as r6  # noqa: E402
import p01d_r9_100k_bandwidth_real_costs as r9  # noqa: E402
import p01d_pillar1_intraday_v1_d1 as pillar1  # noqa: E402

C4_PARAMS = pillar1.CANDIDATES["C4"]
assert C4_PARAMS == dict(
    short_rs=6, long_rs=30, breakout=20, participation=1.5,
    combine="AND", stop_mult=1.5, rr=2.0,
), "C4's frozen parameters changed unexpectedly - refusing to proceed silently."


def _add_5m_atr14(stock5: dict[str, list[dict]]) -> None:
    """Verbatim reproduction of the inline 5-minute ATR14 block in
    `p01d_pillar1_intraday_v1_d1.main()` - extracted into a callable
    function (main() itself is never called, since it tries to load
    real historical files from that sandbox's own data/ directory) but
    not altered in any way. Mutates each 5-minute bar dict in place,
    matching the original's own convention exactly."""
    for _sym, bars in stock5.items():
        trbuf: deque = deque(maxlen=14)
        prev_close = None
        for b in bars:
            tr = (b["high"] - b["low"]) if prev_close is None else max(
                b["high"] - b["low"], abs(b["high"] - prev_close), abs(b["low"] - prev_close)
            )
            trbuf.append(tr)
            b["atr14_5m"] = sum(trbuf) / len(trbuf)
            prev_close = b["close"]


@dataclass
class LivePillar1Evaluator:
    """Holds a live-growing, MULTI-DAY, per-symbol 1-minute bar buffer
    (plus NIFTY) and evaluates C4 against it on demand.

    **Load-bearing design correction, found by tracing C4's real logic
    before writing a single test, not discovered via a failing test**:
    C4's participation check (`trailing_slot_median`, window=10) looks
    backward for the 10 MOST RECENT PRIOR TRADING DAYS at the exact same
    intraday time-of-day slot. A buffer reset every session (one day at
    a time) can never satisfy this - `slot_median` would always be
    `None`, `participation_ok` would always be `False`, and C4's
    AND-combine could never fire, silently, for a reason that would have
    had nothing to do with the market. This evaluator therefore
    accumulates a genuine rolling multi-day history; `evaluate(today)`
    restricts which day's EVENTS may qualify (via `block_days`,
    `evaluate_candidate`'s own existing parameter) while still handing
    the full accumulated history to the trailing-window computations
    that need it - exactly how R0's own D1 discovery script used it,
    just fed live instead of from a pre-loaded historical file."""

    universe: tuple[str, ...] = tuple(pillar1.UNIVERSE)
    retention_days: int = 30  # bounds memory; well above C4's own 10-day need
    _buffers: dict[str, list[dict]] = None
    _nifty_buffer: list[dict] = None

    def __post_init__(self):
        self._buffers = {sym: [] for sym in self.universe}
        self._nifty_buffer = []

    def _to_row(self, bar: dict) -> dict:
        """Normalizes one incoming live bar (timestamp/open/high/low/
        close/volume, the same schema used everywhere else in this
        project) into the exact row shape r6.build5/add_atr14_1m
        expect."""
        ts = r6.canon(bar["timestamp"])
        return {
            "timestamp": ts, "dt": r6.ts(ts),
            "open": r6.f(bar["open"]), "high": r6.f(bar["high"]),
            "low": r6.f(bar["low"]), "close": r6.f(bar["close"]),
            "volume": r6.f(bar.get("volume")) or 0.0,
        }

    def ingest_1m_bar(self, symbol: str, bar: dict) -> None:
        """symbol == "NIFTY 50" routes to the index buffer, matching
        `load_nifty_1m`'s own exclusion convention in the reused
        script. Accepts any date - accumulation across sessions is the
        whole point (see class docstring)."""
        row = self._to_row(bar)
        if symbol == "NIFTY 50":
            self._nifty_buffer.append(row)
        elif symbol in self._buffers:
            self._buffers[symbol].append(row)
        # Symbols outside Pillar I's frozen 19-symbol universe are
        # silently ignored here by design - C4 was only ever
        # preregistered and closed against that exact universe.

    def prune_older_than(self, cutoff: date) -> None:
        """Bounds memory growth - never called automatically mid-
        evaluation, only between sessions, so a live observer decides
        when it's safe to trim."""
        self._nifty_buffer = [r for r in self._nifty_buffer if r["dt"].date() >= cutoff]
        for sym in self._buffers:
            self._buffers[sym] = [r for r in self._buffers[sym] if r["dt"].date() >= cutoff]

    def evaluate(self, today: date) -> list[dict]:
        """Returns whatever trade-shaped signal dicts
        `evaluate_candidate` produces for C4, restricted to events on
        `today`, computed against the FULL accumulated multi-day
        history - identical fields to R0's own D1 discovery output
        (candidate, date, symbol, entry_time, entry_price, stop, target,
        exit_price, exit_reason, qty, gross_pnl, fees, net_pnl), since
        this calls that exact function unmodified."""
        if not self._nifty_buffer:
            return []
        nifty_1m = r6.add_atr14_1m(sorted(self._nifty_buffer, key=lambda x: x["dt"]))
        raw = {
            sym: r6.add_atr14_1m(sorted(rows, key=lambda x: x["dt"]))
            for sym, rows in self._buffers.items() if rows
        }
        stock5 = {sym: r6.build5(rows) for sym, rows in raw.items()}
        nifty5 = r6.build5(nifty_1m)
        _add_5m_atr14(stock5)

        block_days = {today.isoformat()}
        return pillar1.evaluate_candidate("C4", C4_PARAMS, stock5, nifty5, raw, block_days)
