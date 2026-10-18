# External model comparison — frozen Version 1

This is a broker-free research comparison. `LIVE_TRADING_ENABLED` remains false,
there are no broker calls, and the production runner is never imported or started.

## Models

- Our `V9 TOP4 SECTOR` portfolio.
- A long-only 12-1 cross-sectional momentum control based on the published
  Jegadeesh–Titman winner-ranking idea.
- A long-only 12-month absolute-trend control based on the published
  Moskowitz–Ooi–Pedersen time-series momentum idea.
- NIFTY buy-and-hold.

The external controls are transparent adaptations, not exact reproductions of
the papers' long-short institutional portfolios. All models use the same frozen
20-equity universe, common evaluation start, ₹600,000 paper capital, and 10 bps
per-side combined cost/slippage assumption.

The result is evidence for research prioritisation only. The short period,
current-constituent universe, and survivorship bias prohibit a go-live decision.

## Published foundations

- Jegadeesh and Titman (1993), *Returns to Buying Winners and Selling Losers*:
  https://doi.org/10.1111/j.1540-6261.1993.tb04702.x
- Moskowitz, Ooi, and Pedersen (2012), *Time Series Momentum*:
  https://doi.org/10.1016/j.jfineco.2011.11.003
