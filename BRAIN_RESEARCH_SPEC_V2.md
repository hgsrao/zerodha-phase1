# Brain Research Lab — Version 2 (Frozen Before Testing)

Version 1 showed a weak gross signal that was consumed by transaction costs.
Version 2 tests one change only: reduce turnover by operating on completed
60-minute candles instead of 15-minute candles.

## Frozen rules

- Source: the validated Version 1 fifteen-minute NSE datasets.
- Construction: four consecutive fifteen-minute candles starting at 09:15,
  10:15, 11:15, 12:15, 13:15, and 14:15 local exchange time.
- Any incomplete group is discarded; candles are never combined across days.
- OHLCV aggregation: first open, maximum high, minimum low, last close, summed
  volume.
- All Version 1 strategy parameters, next-bar execution, conservative intrabar
  ordering, costs, slippage, and chronological 60/20/20 split remain unchanged.
- No parameter search is permitted after viewing the Version 2 test results.

Success requires positive expectancy and profit factor above 1.0 after costs in
the untouched test segment, with broadly consistent behaviour across instruments.
