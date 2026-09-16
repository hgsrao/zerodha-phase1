# Brain Research Lab — Version 1

## Purpose

This is an offline research hypothesis, not a production trading strategy. It does
not connect to Zerodha, submit orders, or change `LIVE_TRADING_ENABLED`.

The first question is deliberately narrow: **after a liquid instrument breaks a
recent high while its broader trend and volume agree, does a long-only trade have
positive expectancy after realistic costs?**

## Exact rules

All decisions use completed OHLCV candles only.

1. Trend regime: the signal candle closes above the mean close of the previous
   `slow_window` candles.
2. Price trigger: the signal candle closes above the highest high of the previous
   `breakout_window` candles. The current candle is excluded from that window.
3. Participation: signal-candle volume is greater than
   `volume_multiplier × median(previous volume_window volumes)`.
4. Volatility: ATR is calculated using information available at the signal close
   and must be positive.
5. Entry: buy at the **next candle's open**, with adverse slippage. This prevents
   close-to-close look-ahead.
6. Initial stop: `entry − atr_stop_multiple × signal ATR`.
7. Profit target: `entry + reward_risk × initial risk`.
8. Time exit: close the trade after `max_holding_bars` if neither price exit has
   occurred.
9. If stop and target are both touched inside one candle, the stop is assumed to
   occur first. This is deliberately conservative because OHLCV cannot reveal the
   intrabar path.
10. Brokerage/charges and slippage are applied on both sides.

Only one long position may be open at a time. Version 1 uses a fixed quantity so
that signal quality is measured separately from position sizing.

## Evidence standard

Parameters are frozen before examining validation and test results. Results must
be reported separately for chronological train, validation, and untouched test
segments, and compared with a simple buy-and-hold reference. Required measures
include trade count, win rate, expectancy, profit factor, maximum drawdown, and
maximum consecutive losses.

A promising backtest is not permission to trade live. The later stages are:

1. Multiple instruments and market regimes.
2. Walk-forward/out-of-sample stability and parameter-neighbourhood checks.
3. Offline replay through the bot's entry and exit interfaces.
4. Live-data shadow observation with broker writes impossible.
5. Only after explicit review: one tightly capped production trade.

## Data still required

The existing project logs contain useful quote snapshots, but not a sufficiently
continuous sequence of completed OHLCV candles. They cannot support an honest
historical conclusion. Version 1 therefore accepts a CSV with these columns:

`timestamp,open,high,low,close,volume`

Rows must be strictly chronological and free of duplicates. Five-minute or
fifteen-minute candles are reasonable starting points, but different intervals
must never be mixed in one file.
