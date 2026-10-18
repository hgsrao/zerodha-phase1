# Brain Research Lab — Version 14 Realistic Transaction Costs

Every research module through V13 modeled trading friction as a flat
"cost_bps_per_side + slippage_bps_per_side" guess (10 bps/side, 20 bps round
trip) - never checked against what Zerodha and Indian regulators actually
charge. V14 replaces the statutory half of that guess with the real,
checkable Zerodha equity-delivery fee schedule and re-runs V11 (momentum,
the strongest result so far) on the extended 2018-2026, 17-fold, 19-symbol
data at the real ₹1,00,000 / 4-position size.

## The real fee schedule (`zerodha_delivery_costs.py`, verified 2026-08-14)

- Brokerage: Rs 0 (Zerodha's standard equity-delivery offer).
- STT: 0.1% of value, BOTH buy and sell.
- NSE exchange transaction charge: 0.00297%, each side.
- SEBI turnover charge: Rs 10/crore (0.0001%), each side.
- GST: 18% on (brokerage + exchange charge + SEBI charge) only.
- Stamp duty: 0.015% of value, BUY side only.
- DP charge: a FLAT Rs 15.93 (Rs 13.50 + 18% GST) per scrip per sell day,
  regardless of quantity or value - the one component a percentage-only
  model cannot represent at all, and the one that matters most at small
  position sizes.

At the actual ~₹25,000-per-position size this project uses (₹1,00,000 /
4 positions), the real statutory round trip is **~28.6 bps** - already
above the old model's entire 20 bps cost+slippage budget, before any
slippage is even added. At ₹10,000 it's ~38 bps; at ₹5,000, ~54 bps. The
flat DP fee is why: it doesn't shrink with trade size, so smaller trades
absorb it disproportionately.

Slippage (`cfg.slippage_bps_per_side`, still 5 bps/side, unchanged) is kept
as a separate assumption on top of the real statutory costs - it isn't a
regulatory fee, so there's nothing to look up; it stays an estimate.

## Result: V11 momentum, extended 17-fold walk-forward, before vs. after

| | Flat 20bps guess (V13) | Real Zerodha delivery costs |
|---|---|---|
| Profitable folds | 12/17 | **11/17** |
| Mean daily return | +0.0929%/day | **+0.0831%/day** |
| Bootstrap 95% CI | [+0.778, +3.106] | **[+0.582, +2.886]** |
| P(bootstrap non-positive) | 0.02% | **0.06%** |

## Conclusion

**The edge survives realistic transaction costs.** It shrinks - one fewer
profitable fold, ~11% lower mean daily return, a bootstrap interval shifted
down but still entirely positive - but it does not disappear. This was a
real risk (the statutory-only round trip at this position size already
exceeded the old total cost+slippage budget), and it did not materialize as
badly as it could have. This is the second consecutive harder test (after
the extended-history walk-forward in V13) that V11 has passed rather than
failed.

This still is not a green light for live capital - the caveats from V13
(daily-return bootstrap autocorrelation, today's-constituents survivorship
bias, small effective independent-fold count) are unchanged and still
apply in full. What's changed is that the one concrete, checkable objection
(the cost model) has now been checked, and the result held up.

## Release boundary

Same as V10-V13: a validation methodology result, not a production
readiness result. Does not authorize enabling live trading, unlocking
Gate 4, connecting a production execution runner, or placing, modifying, or
cancelling broker orders. `LIVE_TRADING_ENABLED` remains false; no broker
calls were made producing this report (all inputs were already-downloaded
CSV files; the cost model is a pure calculation, not a broker fee lookup).
