# Brain Research Lab — Version 9 Portfolio Construction

Version 9 does not change the Version 8 ranking or 0.25% patient entry. It tests
whether portfolio construction can control the delivery of that near-break-even
signal. Results are exploratory because the same history informed earlier work.

## Frozen portfolio variants

1. `TOP1`: maximum one open position and one candidate per day.
2. `TOP2_SECTOR`: maximum two open positions, maximum two candidates per day,
   and no duplicate sector.
3. `TOP4_SECTOR`: maximum four open positions, maximum four candidates per day,
   and no duplicate sector.

No variant will be selected for production from this history.

## Frozen risk and execution

- Starting research capital: ₹100,000.
- Initial risk allowance: 0.25% of current marked equity per position.
- Initial stop distance: 1.5 × prior 14-hour ATR.
- Combined initial open risk: maximum 1% of current equity.
- Position notional: maximum 20% of current equity.
- Total deployed notional: maximum 80% of current equity.
- Version 8 limit: 0.25% below signal close, valid six hourly bars.
- An unfilled candidate is not replaced.
- Stop has conservative priority within a candle.
- Otherwise exit 18 hourly bars after fill.
- Retain 5 bps adverse slippage and 5 bps costs on each side.
- No leverage, shorts, averaging down, or broker connectivity.

## Frozen sectors

Sector labels are declared in the implementation before results. Open positions
and pending candidates may not duplicate a sector in the sector-controlled
variants.

## Required evidence

Report net return, maximum drawdown, completed trades, win rate, profit factor,
turnover, average capital utilization, six-month returns, and NIFTY buy-and-hold
over the same period. Portfolio construction cannot manufacture an edge; stable
positive results across windows are required before future shadow observation.
