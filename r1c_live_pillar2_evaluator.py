"""R1-C composite engine — live Pillar II (C3) evaluator. LOCAL FILES /
IN-MEMORY BUFFERS ONLY in this module - no Kite calls here (the live
observer that feeds this module handles the real connection separately,
with the owner's own credentials). No broker writes, no orders,
anywhere in this module.

**Reuse, not reimplement** - same discipline as
`r1c_live_pillar1_evaluator.py`: this module imports Pillar II's D1
discovery script's real, already-frozen, already-closed C3 signal logic
(`evaluate_candidate` in `p01d_pillar2_intraday_v1_d1.py`, hash-verified
before import) rather than re-deriving the dislocation Z-score,
idiosyncratic filter, climactic-volume, or VWAP-distance/reclaim
formulas by hand.

**Multi-day history, not per-session reset - same reason as Pillar I's
evaluator**: C3's `vol_ok` check uses the identical
`trailing_slot_median(window=10)` pattern as Pillar I's participation
check - it needs 10 PRIOR trading days at the same intraday time slot.
A buffer reset every session could never satisfy it. (C3's Z-score
check, unlike the volume check, only needs `z_window + 1 = 21` PRIOR
5-minute bars, satisfiable within a single well-progressed session -
but the volume check's multi-day requirement is the binding constraint
either way, so this evaluator carries the same rolling multi-day buffer
design as Pillar I's.)

**Fail-closed on drift**: `p01d_pillar2_intraday_v1_d1.py` itself
depends on `p01d_pillar1_intraday_v1_d1.py` (imported there only for
its NIFTY loader, unused by this module) and on the same r6/r9 helpers
as Pillar I - all four files are hash-verified against their recorded
closure-time hashes before any import happens.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from r1c_live_pillar1_evaluator import (  # noqa: F401 - re-exported for callers
    ReuseDriftError, SANDBOX_ROOT, _verify_hash, r6, r9, pillar1,
)

# Recorded at R0's closure
# (P01D_PILLAR_II_INTRADAY_V1_CLOSURE_MANIFEST_20260818.txt) and
# independently re-verified here on 2026-08-19 before this module was
# written.
_PILLAR2_EXPECTED_HASH = "9a79d4f0933afbf4ccbb1881e93a8eff4290a0ad537915a4a74264cce75264b9"

_path = SANDBOX_ROOT / "p01d_pillar2_intraday_v1_d1.py"
if not _path.exists():
    raise ReuseDriftError(f"REFUSING TO IMPORT: {_path} does not exist.")
_actual = hashlib.sha256(_path.read_bytes()).hexdigest()
if _actual != _PILLAR2_EXPECTED_HASH:
    raise ReuseDriftError(
        f"REFUSING TO IMPORT: {_path} has changed since R0's closure - "
        f"expected sha256 {_PILLAR2_EXPECTED_HASH}, got {_actual}. This module "
        f"will not silently reuse logic that may differ from what R0 actually "
        f"tested and closed."
    )

# r1c_live_pillar1_evaluator already put SANDBOX_ROOT on sys.path and
# hash-verified p01d_r6/p01d_r9/p01d_pillar1_intraday_v1_d1 (which this
# script's own `import p01d_pillar1_intraday_v1_d1 as p1` line depends
# on) before this module is ever reached.
import p01d_pillar2_intraday_v1_d1 as pillar2  # noqa: E402

C3_PARAMS = pillar2.CANDIDATES["C3"]
assert C3_PARAMS == dict(
    z_window=20, z_thresh=-2.0, idio_thresh=-0.003, vol_mult=2.0,
    vwap_dist=0.003, combine="AND", stop_mult=1.0, target="VWAP_RECLAIM", rr=None,
), "C3's frozen parameters changed unexpectedly - refusing to proceed silently."


@dataclass
class LivePillar2Evaluator:
    """Holds a live-growing, MULTI-DAY, per-symbol 1-minute bar buffer
    (plus NIFTY) and evaluates C3 against it on demand - same rolling-
    history design as `LivePillar1Evaluator`, and for the same reason
    (see module docstring): the volume check needs 10 prior same-slot
    trading days, which a per-session buffer could never provide."""

    universe: tuple[str, ...] = tuple(pillar2.UNIVERSE)
    retention_days: int = 30
    _buffers: dict[str, list[dict]] = None
    _nifty_buffer: list[dict] = None

    def __post_init__(self):
        self._buffers = {sym: [] for sym in self.universe}
        self._nifty_buffer = []

    def _to_row(self, bar: dict) -> dict:
        ts = r6.canon(bar["timestamp"])
        return {
            "timestamp": ts, "dt": r6.ts(ts),
            "open": r6.f(bar["open"]), "high": r6.f(bar["high"]),
            "low": r6.f(bar["low"]), "close": r6.f(bar["close"]),
            "volume": r6.f(bar.get("volume")) or 0.0,
        }

    def ingest_1m_bar(self, symbol: str, bar: dict) -> None:
        """symbol == "NIFTY 50" routes to the index buffer. Accepts any
        date - accumulation across sessions is the whole point."""
        row = self._to_row(bar)
        if symbol == "NIFTY 50":
            self._nifty_buffer.append(row)
        elif symbol in self._buffers:
            self._buffers[symbol].append(row)
        # Symbols outside Pillar II's frozen 19-symbol universe are
        # silently ignored here by design.

    def prune_older_than(self, cutoff: date) -> None:
        self._nifty_buffer = [r for r in self._nifty_buffer if r["dt"].date() >= cutoff]
        for sym in self._buffers:
            self._buffers[sym] = [r for r in self._buffers[sym] if r["dt"].date() >= cutoff]

    def evaluate(self, today: date) -> list[dict]:
        """Returns whatever trade-shaped signal dicts
        `evaluate_candidate` produces for C3, restricted to events on
        `today`, computed against the full accumulated multi-day
        history - identical fields to R0's own D1 discovery output
        (candidate, date, symbol, entry_time, entry_price, stop,
        exit_price, exit_reason, qty, gross_pnl, fees, net_pnl,
        turnover, z), since this calls that exact function unmodified.
        No `target` field (Pillar I has one, Pillar II does not) and no
        `atr14_5m` post-processing step (Pillar II's evaluate_candidate
        never reads it - only Pillar I's does)."""
        if not self._nifty_buffer:
            return []
        nifty_1m = r6.add_atr14_1m(sorted(self._nifty_buffer, key=lambda x: x["dt"]))
        raw = {
            sym: r6.add_atr14_1m(sorted(rows, key=lambda x: x["dt"]))
            for sym, rows in self._buffers.items() if rows
        }
        stock5 = {sym: r6.build5(rows) for sym, rows in raw.items()}
        nifty5 = r6.build5(nifty_1m)

        block_days = {today.isoformat()}
        return pillar2.evaluate_candidate("C3", C3_PARAMS, stock5, nifty5, raw, block_days)
