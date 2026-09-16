# Brain Research Lab — Version 6 Cross-Sectional Ranking

## Purpose

Version 6 is a new architecture. It does not inherit the failed independent-
breakout entry rule. Once per completed trading day it compares the same frozen
20-equity universe and asks whether the highest-ranked eligible shares outperform
the universe over the following three sessions.

This is signal research only. Rankings may overlap in time and are not portfolio
PnL, position sizing, or permission to trade.

## Frozen decision and features

- Decision time: the completed hourly candle beginning at 14:15 (ending 15:15).
- All features use that completed candle and earlier data only.
- 30-hour relative return versus NIFTY: 30% weight.
- 120-hour relative return versus NIFTY: 30% weight.
- Distance from the previous 120-hour high: 25% weight.
- Current final-hour volume versus the median final-hour volume of the previous
  20 sessions: 15% weight.
- Each feature is converted to a cross-sectional percentile rank across all
  available members before weights are applied.

## Frozen eligibility

- NIFTY close must exceed the mean of its previous 50 hourly closes.
- Equity close must exceed the mean of its previous 50 hourly closes.
- Equity 120-hour return must exceed the NIFTY 120-hour return.
- Equity must close no more than 2% below its previous 120-hour high.
- Final-hour volume must be at least its previous-20-session median.
- Missing or malformed data blocks that equity.

Select at most the top two eligible equities. If none qualify, emit `NO_CANDIDATE`.

## Frozen outcome

- Hypothetical entry: next available hourly open with 5 bps adverse slippage.
- Exit: close 18 hourly bars after entry (approximately three sessions), with
  5 bps adverse slippage.
- Additional costs: 5 bps per side.
- Primary evidence: mean net return, win rate, top-selection return minus the
  same-day universe mean, percentage of profitable six-month windows, candidate
  frequency, and symbol concentration.
- Use the same five six-month windows frozen for Version 5. No parameter change
  is allowed after viewing Version 6 results.
