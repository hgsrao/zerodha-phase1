# Brain Research Lab — Version 12 Momentum + Risk Management

Version 12 combines V11's better-walk-forwarding selection (12-1 cross-
sectional momentum) with V9's execution discipline (ATR-based stops,
risk-fraction sizing, sector caps), on daily bars resampled from the same
hourly data. The hypothesis: momentum's edge survives the honest walk-
forward better than V9's own ranking, but as implemented it has zero
downside protection between monthly rebalances - wrapping it in V9's risk
machinery should only help.

**The hypothesis was wrong. The hybrid is worse than either parent.**

## Full-period number is misleading on its own - read this first

`V12_TOP4_SECTOR`'s full-period (2023-2026, no walk-forward) headline is
+4.8% net return. That number is an artifact: **closed trades net a loss
of −₹2,291** over the full period (28 stops, 7 rebalance exits, profit
factor 0.66); the entire positive headline comes from **+₹7,048 of
unrealized gain sitting in whichever positions happened to still be open at
the arbitrary Aug-2026 data cutoff.** A backtest ending one month earlier or
later could easily flip that sign. This is exactly the kind of artifact an
anchored walk-forward is supposed to catch, since each fold's test window
ends at a different date and can't all be flattered by the same cutoff.

## Anchored walk-forward result (2026-08-14 run, ₹100,000)

| Fold | Selected | Test net P&L (₹) |
|---|---|---|
| 2024-02 – 2024-08 | TOP4_SECTOR (fallback) | 0 |
| 2024-08 – 2025-02 | TOP4_SECTOR (fallback) | **−3,812** |
| 2025-02 – 2025-08 | TOP2_SECTOR | +343 |
| 2025-08 – 2026-02 | TOP2_SECTOR | +184 |
| 2026-02 – 2026-08 | TOP2_SECTOR | **−743** |

- Profitable folds: **2 of 5**.
- Aggregate out-of-sample: 29 trades, net P&L **−₹4,028** (−4.0% on
  ₹100,000), win rate **17.2%**, profit factor **0.30**.
- Bootstrap (5,000 resamples): 95% CI **[−₹6,304, −₹1,429]** - entirely
  negative. Probability of a non-positive total: **99.8%**.

This is worse than both V10 (V9 alone, −5.8%, 88% P(loss)) on a percentage
basis is comparable, but V12's profit factor (0.30) and P(loss) (99.8%) are
markedly worse than either V9 alone or momentum alone (V11: +positive
aggregate, 91% P(profit)).

## Why the hybrid failed

The most likely explanation: **the stop was borrowed from the wrong regime.**
V9's 1.5×ATR(14) stop was calibrated for a strategy that enters and exits
within about 3 trading days on hourly bars - it expects to give a position
very little room before deciding it was wrong. V12 holds through a full
month between rebalances. A stop sized for a 3-day swing trade is far too
tight for a position meant to ride out a month of ordinary volatility: the
17.2% win rate and 28-stops-vs-7-successful-rebalances ratio from the
full-period run both point at the same thing - most positions are getting
stopped out on normal monthly noise long before the momentum thesis had
room to play out.

This is a real, useful finding, not a dead end: it says the momentum
edge from V11 is worth protecting, but the protection needs to be sized for
a monthly holding period (e.g. a stop based on monthly/weekly volatility, a
much wider multiple, or a portfolio-level drawdown circuit breaker instead
of a per-position intraday-style stop) - not V9's parameters copied over
unchanged.

## Conclusion

Do not use V12 as implemented. Of the three anchored walk-forward results so
far, V11 (plain momentum, no added risk overlay) remains the strongest
result by a clear margin. If risk-managing momentum is still worth pursuing,
the next version should size its stop to the strategy's own holding period
instead of reusing V9's numbers, and should walk-forward that choice too
rather than assume it.

## Release boundary

Same as V10/V11: a validation methodology result, not a production readiness
result. Does not authorize enabling live trading, unlocking Gate 4,
connecting a production execution runner, or placing, modifying, or
cancelling broker orders. `LIVE_TRADING_ENABLED` remains false; no broker
calls were made producing this report.
