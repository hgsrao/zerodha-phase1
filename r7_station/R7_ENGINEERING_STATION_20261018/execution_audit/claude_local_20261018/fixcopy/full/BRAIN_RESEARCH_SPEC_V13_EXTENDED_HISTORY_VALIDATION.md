# Brain Research Lab — Version 13 Extended-History Validation

Version 13 re-runs the exact same three anchored walk-forward methodologies
(V10 for V9, V11 for momentum, V12 for momentum+risk) against real Zerodha
history extended from ~3 years back to ~8.5 years - 17 anchored folds
instead of 5. No trading logic changed; only the amount of out-of-sample
evidence available to grade it did.

## Data

- Downloaded via `download_historical_ohlcv.py` (60-minute interval,
  2016-01-01 to 2026-08-13) by the user, through their own Zerodha login -
  this session never touched a credential or request token.
- 3 bad OHLC ticks found and corrected by the existing
  `prepare_research_ohlcv.py` tool (all on the same bar, 2024-06-25 09:15,
  across BHARTIARTL/HDFCBANK/LAURUSLABS - consistent with an NSE-side
  open-auction data anomaly that day, not a download error). Logged in
  `historical_data_60minute_extended_ready/correction_audit.csv`.
- **POLYCAB excluded** from the universe: its April 2019 listing would
  otherwise cap the whole universe's usable history to ~7.3 years. The
  remaining 19 symbols (see `extended_history_windows.EXCLUDED_SYMBOLS`)
  all have data back to at least 2016-12-19 (LAURUSLABS).
- Folds: `extended_history_windows.EXTENDED_WINDOWS`, 2018-02-14 to
  2026-08-14, 17 six-month anchored folds. The last 5 line up exactly with
  the original `walk_forward_v5.WINDOWS` boundaries.

## Result: 5 folds vs. 17 folds

| | V10 (V9 alone) | V11 (momentum) | V12 (momentum + V9 risk) |
|---|---|---|---|
| Folds (5 → 17) | 2/5 → **7/17** | 3/5 → **12/17** | 2/5 → **3/17** |
| Aggregate return | −5.8% → **+9.8%** | positive → **positive, tighter** | −4.0% → **−22.0%** |
| Profit factor | 0.85 → **1.06** | n/a | 0.30 → **0.41** |
| P(bootstrap loss) | 88% → **21.8%** | 9% → **0.02%** | 99.8% → **100%** |

Three genuinely different conclusions emerge, and the 5-fold sample was
too small to trust any of them on their own:

- **V9 alone (V10)** flips from "looks like a clear loss" to "wide,
  inconclusive interval leaning positive" ([−₹14,095, +₹34,706] 95% CI,
  still straddling zero). The 5-fold read was noise-driven, not wrong in
  direction so much as overconfident in a bad direction.
- **Momentum alone (V11)** goes from "cautiously encouraging" to
  **genuinely strong**: 12 of 17 folds profitable, aggregate daily-return
  bootstrap CI **entirely positive** ([+0.78, +3.11], probability of a
  non-positive result **0.02%**). This is the first result in this whole
  research program with a bootstrap interval that doesn't touch zero.
- **Momentum + V9's risk management (V12)** goes from "clearly worse than
  either parent" to **decisively worse** - the tight intraday-calibrated
  stop keeps destroying the momentum edge, now with a 100% bootstrap
  probability of loss instead of 99.8%. More data made this conclusion more
  certain, not less; the mismatched-stop diagnosis from BRAIN_RESEARCH_
  SPEC_V12 holds up.

## Honest caveats that still apply

- V11's bootstrap resamples daily equity-curve returns, not independent
  monthly decisions - the true independent-decision count is closer to
  17 folds × ~5-6 rebalances than to 2,088 daily observations. Treat the
  12-of-17-folds pattern as the primary evidence, the tight CI as
  corroborating, not load-bearing on its own.
- The 19-symbol universe is still today's constituents, evaluated back to
  2018 - not a point-in-time investable universe. Survivorship bias is not
  corrected for.
- All three models share the same 5-6 sector-heavy large/mid-cap NSE
  universe; a result this size (17 folds, ~2 years apiece at most before
  overlap) cannot rule out that the whole universe simply had a favorable
  multi-year run that a long-only momentum tilt was well-positioned to
  capture, independent of the ranking's own skill.
- No transaction-cost realism beyond the existing flat 5+5 bps model has
  been added; real Zerodha brokerage/STT at ₹1,00,000 position sizes has
  still not been checked against this result.

## Conclusion

Momentum alone (V11) is now the clearly strongest candidate in this
project's research history, and unlike every earlier result here, this one
did not weaken under a harder test - it got stronger. It still is not a
green light for live capital: the caveats above are real, and this
project's own standing bar ("stable positive results across windows before
future shadow observation") is arguably now the closest it's been to being
met, but "closest" is not "met." The reasonable next steps, in order, are
transaction-cost realism at real position size, and then - if that still
holds - shadow observation against live data before any capital decision.

## Release boundary

Same as V10/V11/V12: a validation methodology result, not a production
readiness result. Does not authorize enabling live trading, unlocking
Gate 4, connecting a production execution runner, or placing, modifying, or
cancelling broker orders. `LIVE_TRADING_ENABLED` remains false; no broker
calls were made producing this report (all inputs were already-downloaded
CSV files).
