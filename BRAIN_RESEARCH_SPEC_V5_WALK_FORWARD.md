# Brain Research Lab — Version 5 Walk-Forward (Frozen Before Data)

Version 5 does not change the Version 4 signal. It tests whether that signal
persists across time and a larger, predeclared liquid-equity universe.

## Frozen universe

Original five:

`TATASTEEL, INFY, ZYDUSLIFE, LAURUSLABS, POLYCAB`

Additional fifteen selected before downloading or testing their results:

`RELIANCE, HDFCBANK, ICICIBANK, SBIN, LT, AXISBANK, KOTAKBANK, BHARTIARTL,
ITC, BAJFINANCE, MARUTI, TCS, HINDUNILVR, SUNPHARMA, NTPC`

No symbol may be removed because of poor performance.

## Frozen evaluation

- Download the same 15-minute date range and construct completed 60-minute bars.
- Keep all Version 4 signal, NIFTY regime, execution, cost, and slippage rules.
- Use consecutive six-month evaluation windows after sufficient indicator warmup.
- Parameters remain fixed; there is no optimization within any window.
- Report every symbol-window result, combined risk-normalized expectancy, profit
  factor, percentage of profitable windows, and concentration by symbol.
- A missing equity or NIFTY timestamp blocks that signal.

This is a robustness test, not a basis for enabling live trading.
