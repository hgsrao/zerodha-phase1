# Brain Research Lab — Version 3 (Frozen Before Testing)

Version 2 reduced turnover and moved the combined result close to break-even,
but performance was inconsistent across instruments. Version 3 tests one change:
only enter when the established slow trend is itself rising.

## Frozen change

- Continue using the completed 60-minute candles created for Version 2.
- Keep all Version 2 windows, breakout, volume, ATR, entry, exit, cost, slippage,
  and chronological split rules unchanged.
- Add `slow_slope_lookback = 10`.
- At a signal close, calculate the mean of the previous 50 closes and compare it
  with the previous-50-close mean ending 10 bars earlier.
- Entry is eligible only when the recent slow mean is strictly higher.
- Both means exclude the signal candle, so this filter cannot see future data.

No other filter or parameter search is allowed after the Version 3 test result.
Success requires positive combined risk-normalized expectancy and profit factor
above 1.0 after costs, with reasonable consistency across instruments.
