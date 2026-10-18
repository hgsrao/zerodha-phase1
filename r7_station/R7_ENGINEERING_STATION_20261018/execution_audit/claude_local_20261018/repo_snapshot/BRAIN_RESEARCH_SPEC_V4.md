# Brain Research Lab — Version 4 (Frozen Before Testing)

Version 4 returns to the superior Version 2 baseline and tests one market-context
condition. No Version 3 slope condition is used.

## Frozen change

- Equity data and every Version 2 entry, exit, cost, slippage, and split rule stay
  unchanged.
- Add completed 60-minute NIFTY 50 candles covering the same dates.
- At an equity signal close, use the NIFTY candle having the exact same timestamp.
- The signal is eligible only when that NIFTY close is strictly above the mean of
  the previous 50 NIFTY closes. The current index close is excluded from the mean.
- Missing or unmatched NIFTY data blocks the signal.
- No other condition or parameter search is allowed after seeing Version 4.

Success requires positive combined risk-normalized expectancy and profit factor
above 1.0 after costs, along with reasonable cross-instrument consistency.
